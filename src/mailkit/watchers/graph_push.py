"""Microsoft Graph delta + optional change-notification webhook renewal."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mailkit.backoff import Backoff
from mailkit.config import AccountConfig
from mailkit.ids import idempotency_key, message_id as make_message_id, new_id
from mailkit.logutil import get_logger
from mailkit.models import Address, Event, Message, utcnow
from mailkit.providers.graph import GraphProvider
from mailkit.watchers.flag_diff import (
    emit_typed_events,
    find_indexed,
    flag_event_types,
    persist_index_flags,
)

log = get_logger("mailkit.graph_push")

# Public API path Microsoft Graph POSTs to. Handler._dispatch must not require Bearer.
GRAPH_HOOK_PATH = "/v1/provider-hooks/graph"


class GraphPushWatcher:
    plugin_type = "watcher"
    id = "graph_push"

    def supports(self, account: AccountConfig, provider: Any) -> bool:
        return isinstance(provider, GraphProvider) and bool(provider.secrets.get("access_token"))

    def watch(self, account: AccountConfig, provider: GraphProvider, emit: Callable[[Event], None], stop: threading.Event) -> None:
        mailbox = account.folders.inbox or "inbox"
        store = provider.store
        delta_link = store.get_sync_cursor(account.id, mailbox, "graph_delta") if store else None
        backoff = Backoff(initial=5, maximum=180)
        graph_sub = None
        if account.oauth.graph_notify_url:
            try:
                exp = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat()
                # graph_notify_url should be a public URL that reaches GRAPH_HOOK_PATH
                # without Authorization; Graph's validation handshake cannot send our token.
                graph_sub = provider.subscribe(account.oauth.graph_notify_url, expiration=exp)
                if store and graph_sub and graph_sub.get("id"):
                    store.set_sync_cursor(account.id, mailbox, "graph_sub", graph_sub["id"])
            except Exception as exc:
                log.warning("graph subscribe failed (delta still runs): %s", exc)
        last_renew = 0.0
        while not stop.is_set():
            try:
                payload = provider.delta("inbox", delta_link)
                values = payload.get("value") or []
                for item in values:
                    _emit_delta_item(account, provider, mailbox, item, emit)
                delta_link = payload.get("@odata.deltaLink") or payload.get("@odata.nextLink") or delta_link
                if store and delta_link:
                    store.set_sync_cursor(account.id, mailbox, "graph_delta", delta_link)
                now = datetime.now(timezone.utc).timestamp()
                if graph_sub and account.oauth.graph_notify_url and now - last_renew > 12 * 3600:
                    exp = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat()
                    try:
                        provider.renew_subscription(graph_sub.get("id"), exp)
                        last_renew = now
                    except Exception as exc:
                        log.warning("graph subscription renew failed: %s", exc)
                backoff.reset()
                stop.wait(30)
            except Exception as exc:
                log.warning("graph delta error: %s", exc)
                stop.wait(backoff.fail())


def _delta_event_type(item: dict, *, known: bool) -> str:
    """Map Graph delta / notification change types. FLAG-F7 owns flag/read specialization."""
    if item.get("@removed"):
        return "message.deleted"
    change = str(item.get("changeType") or "").strip().lower()
    if change in {"created", "updated", "deleted"}:
        return f"message.{change}"
    return "message.updated" if known else "message.created"


def _imap_uid_for(row: dict | None) -> str | None:
    if not row:
        return None
    uid = row.get("uid")
    if uid is None or uid == "":
        return None
    return str(uid)


def _message_from_graph(account, mailbox: str, item: dict) -> Message:
    native = item.get("id") or ""
    from_addr = ((item.get("from") or {}).get("emailAddress") or {})
    flagged = (item.get("flag") or {}).get("flagStatus") == "flagged"
    unread = item.get("isRead") is False
    flags: list[str] = []
    if flagged:
        flags.append("Flagged")
    if not unread:
        flags.append("Seen")
    mid = str(item.get("internetMessageId") or "").strip("<>")
    return Message(
        id=make_message_id(account.id, mailbox, "graph", native),
        account_id=account.id,
        provider_id="graph",
        mailbox=mailbox,
        native_id=native,
        message_id=mid,
        thread_id=item.get("conversationId") or "",
        subject=item.get("subject") or "",
        from_=[Address(address=from_addr.get("address") or "", name=from_addr.get("name") or "")],
        flags=flags,
        unread=unread,
        flagged=flagged,
        draft=bool(item.get("isDraft")),
        has_attachments=bool(item.get("hasAttachments")),
        snippet=item.get("bodyPreview") or "",
    )


def _fetch_and_upsert(account, provider, mailbox: str, item: dict, existing: dict | None) -> Message | None:
    store = getattr(provider, "store", None)
    uid = _imap_uid_for(existing)
    getter = getattr(provider, "get_message", None)
    if uid and callable(getter):
        try:
            msg = getter(mailbox, uid, peek=True)
            if msg is not None:
                return msg
        except Exception as exc:
            log.info("graph delta imap fetch uid=%s failed: %s", uid, exc)
    msg = _message_from_graph(account, mailbox, item)
    if existing:
        msg.id = existing.get("id") or msg.id
        if existing.get("uid") is not None:
            msg.uid = existing.get("uid")
        if existing.get("uidvalidity") is not None:
            msg.uidvalidity = existing.get("uidvalidity")
        msg.tags = existing.get("tags") or []
    if store:
        store.upsert_message(msg)
    return msg


def _emit_delta_item(account, provider, mailbox: str, item: dict, emit) -> None:
    native = item.get("id") or ""
    store = getattr(provider, "store", None)
    existing = find_indexed(store, account.id, native, mailbox)
    etype = _delta_event_type(item, known=bool(existing))
    if etype == "message.deleted":
        emit(
            Event(
                id=new_id("evt"),
                ts=utcnow(),
                account_id=account.id,
                provider_id="graph",
                mailbox=mailbox,
                type="message.deleted",
                thread_id=item.get("conversationId") or "",
                data={"graph": item},
                message=None,
                idempotency_key=idempotency_key(account.id, mailbox, "message.deleted", native),
            )
        )
        return
    fetched = _fetch_and_upsert(account, provider, mailbox, item, existing)
    message = fetched.summary() if fetched is not None and hasattr(fetched, "summary") else None
    flagged = bool(getattr(fetched, "flagged", False)) if fetched is not None else (item.get("flag") or {}).get("flagStatus") == "flagged"
    unread = bool(getattr(fetched, "unread", True)) if fetched is not None else item.get("isRead") is False
    explicit = str(item.get("changeType") or "").strip().lower() in {"created", "updated", "deleted"}
    if existing and not explicit:
        types = flag_event_types(
            prev_flagged=bool(existing.get("flagged")),
            prev_unread=bool(existing.get("unread", True)),
            flagged=bool(flagged),
            unread=bool(unread),
        )
        if types:
            persist_index_flags(store, existing, flagged=bool(flagged), unread=bool(unread))
            emit_typed_events(
                account,
                "graph",
                mailbox,
                types,
                emit,
                native_id=native,
                thread_id=item.get("conversationId") or "",
                message=message,
                data={"graph": {"id": native, "subject": item.get("subject")}},
            )
            return
    emit(
        Event(
            id=new_id("evt"),
            ts=utcnow(),
            account_id=account.id,
            provider_id="graph",
            mailbox=mailbox,
            type=etype,
            thread_id=item.get("conversationId") or (message or {}).get("thread_id") or "",
            data={"graph": item if etype == "message.deleted" else {"id": native, "subject": item.get("subject")}},
            message=message,
            idempotency_key=idempotency_key(account.id, mailbox, etype, native),
        )
    )
