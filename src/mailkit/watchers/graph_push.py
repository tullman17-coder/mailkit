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
                    etype = "message.deleted" if item.get("@removed") else "message.created"
                    native = item.get("id") or ""
                    emit(
                        Event(
                            id=new_id("evt"),
                            ts=utcnow(),
                            account_id=account.id,
                            provider_id="graph",
                            mailbox=mailbox,
                            type=etype,
                            thread_id=item.get("conversationId") or "",
                            data={"graph": item if etype == "message.deleted" else {"id": native, "subject": item.get("subject")}},
                            message=None if etype == "message.deleted" else _graph_summary(account, mailbox, item),
                            idempotency_key=idempotency_key(account.id, mailbox, etype, native),
                        )
                    )
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
