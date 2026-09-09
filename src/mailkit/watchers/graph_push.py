"""Microsoft Graph delta + optional change-notification webhook renewal."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mailkit.backoff import Backoff
from mailkit.config import AccountConfig
from mailkit.ids import idempotency_key, new_id
from mailkit.logutil import get_logger
from mailkit.models import Event, utcnow
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


def _emit_delta_item(account, provider, mailbox, item: dict, emit) -> None:
    native = item.get("id") or ""
    store = getattr(provider, "store", None)
    if item.get("@removed"):
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
                idempotency_key=idempotency_key(account.id, mailbox, "message.deleted", native),
            )
        )
        return
    flagged = (item.get("flag") or {}).get("flagStatus") == "flagged"
    unread = item.get("isRead") is False
    summary = _graph_summary(account, mailbox, item)
    prev = find_indexed(store, account.id, native, mailbox)
    if prev:
        types = flag_event_types(
            prev_flagged=bool(prev.get("flagged")),
            prev_unread=bool(prev.get("unread", True)),
            flagged=flagged,
            unread=unread,
        )
        if types:
            persist_index_flags(store, prev, flagged=flagged, unread=unread)
            emit_typed_events(
                account,
                "graph",
                mailbox,
                types,
                emit,
                native_id=native,
                thread_id=item.get("conversationId") or "",
                message=summary,
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
            type="message.created",
            thread_id=item.get("conversationId") or "",
            data={"graph": {"id": native, "subject": item.get("subject")}},
            message=summary,
            idempotency_key=idempotency_key(account.id, mailbox, "message.created", native),
        )
    )


def _graph_summary(account, mailbox, item: dict) -> dict:
    return {
        "schema": "mailkit.message.v1",
        "id": f"msg_graph_{item.get('id','')[:20]}",
        "account_id": account.id,
        "provider_id": "graph",
        "mailbox": mailbox,
        "native_id": item.get("id"),
        "subject": item.get("subject") or "",
        "from": [{"address": ((item.get("from") or {}).get("emailAddress") or {}).get("address", ""), "name": ((item.get("from") or {}).get("emailAddress") or {}).get("name", "")}],
        "thread_id": item.get("conversationId") or "",
        "unread": bool(item.get("isRead") is False),
        "flagged": ((item.get("flag") or {}).get("flagStatus") == "flagged"),
        "has_attachments": bool(item.get("hasAttachments")),
        "snippet": item.get("bodyPreview") or "",
    }
