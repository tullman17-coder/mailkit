"""Gmail users.history + optional Pub/Sub pull. Falls back to IDLE/poll if unset."""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

from mailkit.backoff import Backoff
from mailkit.config import AccountConfig
from mailkit.httputil import request_json
from mailkit.ids import idempotency_key, new_id
from mailkit.logutil import get_logger
from mailkit.models import Event, utcnow
from mailkit.providers.gmail import GmailProvider

log = get_logger("mailkit.gmail_push")


def history_cursor_expired(exc: BaseException) -> bool:
    """True when Gmail history.list rejected startHistoryId as too old (HTTP 404/410)."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        code = getattr(cur, "code", None)
        if isinstance(code, int) and code in (404, 410):
            return True
        text = str(cur)
        if "HTTP 404" in text or "HTTP 410" in text:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


class GmailPushWatcher:
    plugin_type = "watcher"
    id = "gmail_push"

    def supports(self, account: AccountConfig, provider: Any) -> bool:
        return isinstance(provider, GmailProvider) and bool(
            account.oauth.pubsub_subscription or account.watch in {"gmail_push", "auto"}
        ) and bool(provider.secrets.get("access_token"))

    def watch(self, account: AccountConfig, provider: GmailProvider, emit: Callable[[Event], None], stop: threading.Event) -> None:
        mailbox = account.folders.inbox or "INBOX"
        store = provider.store
        start = None
        if store:
            start = store.get_sync_cursor(account.id, mailbox, "gmail_history")
        if not start:
            start = provider.history_id()
            if start and store:
                store.set_sync_cursor(account.id, mailbox, "gmail_history", start)
        if account.oauth.pubsub_topic:
            try:
                provider.watch(account.oauth.pubsub_topic)
            except Exception as exc:
                log.warning("gmail users.watch failed: %s", exc)
        backoff = Backoff(initial=2, maximum=120)
        while not stop.is_set():
            try:
                if account.oauth.pubsub_subscription:
                    self._pull_pubsub(account, provider, emit, stop)
                # Always drain history so we do not depend on push delivery.
                if start:
                    payload = provider.list_history(start)
                    hist = payload.get("history") or []
                    new_hid = str(payload.get("historyId") or start)
                    for item in hist:
                        self._emit_history_item(account, provider, mailbox, item, emit)
                    start = new_hid
                    if store:
                        store.set_sync_cursor(account.id, mailbox, "gmail_history", start)
                backoff.reset()
                stop.wait(15 if not account.oauth.pubsub_subscription else 5)
            except Exception as exc:
                if history_cursor_expired(exc):
                    log.warning("gmail historyId expired (%s); resetting cursor", exc)
                    start = self._reset_history_cursor(account, provider, mailbox, store, emit)
                    if start:
                        backoff.reset()
                        continue
                log.warning("gmail history loop: %s", exc)
                stop.wait(backoff.fail())

    def _reset_history_cursor(self, account, provider, mailbox, store, emit) -> str | None:
        try:
            fresh = provider.history_id()
        except Exception as exc:
            log.warning("gmail historyId reset failed: %s", exc)
            return None
        if not fresh:
            return None
        if store:
            store.set_sync_cursor(account.id, mailbox, "gmail_history", fresh)
        self._idle_backfill(account, provider, mailbox, fresh, emit)
        return fresh

    def _idle_backfill(self, account, provider, mailbox, history_id: str, emit) -> None:
        recent_uids = getattr(provider, "recent_uids", None)
        if not callable(recent_uids):
            return
        try:
            uids = recent_uids(mailbox, None) or []
        except Exception as exc:
            log.info("gmail history reset backfill skipped: %s", exc)
            return
        for uid in sorted(uids)[-50:]:
            emit(
                Event(
                    id=new_id("evt"),
                    ts=utcnow(),
                    account_id=account.id,
                    provider_id="gmail",
                    mailbox=mailbox,
                    type="message.created",
                    data={"gmail_id": None, "history_id": history_id},
                    idempotency_key=idempotency_key(account.id, mailbox, "message.created", str(uid)),
                )
            )

    def _pull_pubsub(self, account, provider, emit, stop) -> None:
        # Optional Google Pub/Sub pull. Requires a pull subscription the user owns.
        token = provider.secrets.get("access_token")
        sub = account.oauth.pubsub_subscription
        if not token or not sub:
            return
        url = f"https://pubsub.googleapis.com/v1/{sub}:pull"
        try:
            payload = request_json(url, method="POST", token=token, body={"maxMessages": 20})
        except Exception as exc:
            log.info("pubsub pull skipped: %s", exc)
            return
        msgs = (payload or {}).get("receivedMessages") or []
        ack_ids = []
        for item in msgs:
            ack_ids.append(item.get("ackId"))
            data = item.get("message", {}).get("data")
            if data:
                emit(
                    Event(
                        id=new_id("evt"),
                        ts=utcnow(),
                        account_id=account.id,
                        provider_id="gmail",
                        mailbox="INBOX",
                        type="message.updated",
                        data={"pubsub": True},
                        idempotency_key=idempotency_key(account.id, "INBOX", "gmail.pubsub", item.get("ackId") or ""),
                    )
                )
        if ack_ids:
            try:
                request_json(
                    f"https://pubsub.googleapis.com/v1/{sub}:acknowledge",
                    method="POST",
                    token=token,
                    body={"ackIds": ack_ids},
                )
            except Exception:
                pass

    def _emit_history_item(self, account, provider, mailbox, item, emit) -> None:
        hid = str(item.get("id") or "")
        for added in item.get("messagesAdded") or []:
            gmail_id = (added.get("message") or {}).get("id")
            emit(
                Event(
                    id=new_id("evt"),
                    ts=utcnow(),
                    account_id=account.id,
                    provider_id="gmail",
                    mailbox=mailbox,
                    type="message.created",
                    data={"gmail_id": gmail_id, "history_id": hid},
                    idempotency_key=idempotency_key(account.id, mailbox, "message.created", gmail_id or hid),
                )
            )
        for removed in item.get("messagesDeleted") or []:
            gmail_id = (removed.get("message") or {}).get("id")
            emit(
                Event(
                    id=new_id("evt"),
                    ts=utcnow(),
                    account_id=account.id,
                    provider_id="gmail",
                    mailbox=mailbox,
                    type="message.deleted",
                    data={"gmail_id": gmail_id},
                    idempotency_key=idempotency_key(account.id, mailbox, "message.deleted", gmail_id or hid),
                )
            )
        for labels in item.get("labelsAdded") or []:
            emit(
                Event(
                    id=new_id("evt"),
                    ts=utcnow(),
                    account_id=account.id,
                    provider_id="gmail",
                    mailbox=mailbox,
                    type="message.updated",
                    data={"labels_added": labels},
                    idempotency_key=idempotency_key(account.id, mailbox, "labelsAdded", json.dumps(labels, sort_keys=True)),
                )
            )
