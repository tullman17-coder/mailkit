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
from mailkit.watchers.flag_diff import (
    emit_typed_events,
    find_indexed,
    gmail_label_event_types,
    persist_index_flags,
)

log = get_logger("mailkit.gmail_push")


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
                log.warning("gmail history loop: %s", exc)
                stop.wait(backoff.fail())

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
            self._emit_label_change(account, provider, mailbox, hid, labels, added=True, emit=emit)
        for labels in item.get("labelsRemoved") or []:
            self._emit_label_change(account, provider, mailbox, hid, labels, added=False, emit=emit)

    def _emit_label_change(self, account, provider, mailbox, hid, labels, *, added: bool, emit) -> None:
        gmail_id = (labels.get("message") or {}).get("id")
        label_ids = labels.get("labelIds") or []
        types = gmail_label_event_types(label_ids, added=added)
        store = getattr(provider, "store", None)
        row = find_indexed(store, account.id, gmail_id, mailbox)
        summary = None
        extra = {"gmail_id": gmail_id, "history_id": hid}
        if types:
            flagged = None
            unread = None
            if "message.flagged" in types:
                flagged = True
            if "message.unflagged" in types:
                flagged = False
            if "message.unread" in types:
                unread = True
            if "message.read" in types:
                unread = False
            if row:
                persist_index_flags(store, row, flagged=flagged, unread=unread)
                summary = store.get_message(row.get("id") or "") if store else None
            extra["labels_added" if added else "labels_removed"] = labels
            emit_typed_events(
                account,
                "gmail",
                mailbox,
                types,
                emit,
                native_id=gmail_id or hid,
                thread_id=(labels.get("message") or {}).get("threadId") or "",
                message=summary,
                data=extra,
            )
        other = [x for x in label_ids if str(x).upper() not in {"STARRED", "UNREAD"}]
        if types and not other:
            return
        key = "labelsAdded" if added else "labelsRemoved"
        emit(
            Event(
                id=new_id("evt"),
                ts=utcnow(),
                account_id=account.id,
                provider_id="gmail",
                mailbox=mailbox,
                type="message.updated",
                data={"labels_added" if added else "labels_removed": labels, "gmail_id": gmail_id},
                idempotency_key=idempotency_key(account.id, mailbox, key, json.dumps(labels, sort_keys=True)),
            )
        )
