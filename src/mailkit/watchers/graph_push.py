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

log = get_logger("mailkit.graph_push")


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


def _find_indexed(store, account_id: str, native_id: str, mailbox: str) -> dict | None:
    if not store or not native_id:
        return None
    finder = getattr(store, "find_by_native", None)
    if callable(finder):
        found = finder(account_id, native_id, mailbox=mailbox)
        if found:
            return found
    try:
        rows = store.list_messages(account_id=account_id, mailbox=mailbox, limit=500)
    except Exception:
        return None
    for row in rows:
        if str(row.get("native_id") or "") == native_id or str(row.get("uid") or "") == native_id:
            return row
    return None


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
    existing = _find_indexed(store, account.id, native, mailbox)
    etype = _delta_event_type(item, known=bool(existing))
    message = None
    if etype != "message.deleted":
        fetched = _fetch_and_upsert(account, provider, mailbox, item, existing)
        if fetched is not None:
            message = fetched.summary() if hasattr(fetched, "summary") else None
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


def _graph_summary(account, mailbox, item: dict) -> dict:
    native = item.get("id") or ""
    return {
        "schema": "mailkit.message.v1",
        "id": make_message_id(account.id, mailbox, "graph", native),
        "account_id": account.id,
        "provider_id": "graph",
        "mailbox": mailbox,
        "native_id": native,
        "subject": item.get("subject") or "",
        "from": [
            {
                "address": ((item.get("from") or {}).get("emailAddress") or {}).get("address", ""),
                "name": ((item.get("from") or {}).get("emailAddress") or {}).get("name", ""),
            }
        ],
        "thread_id": item.get("conversationId") or "",
        "unread": bool(item.get("isRead") is False),
        "flagged": ((item.get("flag") or {}).get("flagStatus") == "flagged"),
        "has_attachments": bool(item.get("hasAttachments")),
        "snippet": item.get("bodyPreview") or "",
    }
