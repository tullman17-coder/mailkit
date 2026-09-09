"""Incremental UID/history polling. Fallback only when IDLE/push is unavailable."""

from __future__ import annotations

import threading
from typing import Any, Callable

from mailkit.backoff import Backoff
from mailkit.config import AccountConfig
from mailkit.ids import idempotency_key, new_id
from mailkit.logutil import get_logger
from mailkit.models import Event, utcnow

log = get_logger("mailkit.poll")

UID_CURSOR_KIND = "uid"


def load_uid_cursor(provider: Any, account_id: str, mailbox: str) -> int:
    """Restore the shared IMAP UID high-water mark used by poll and IDLE."""
    store = getattr(provider, "store", None)
    if not store:
        return 0
    raw = store.get_sync_cursor(account_id, mailbox, UID_CURSOR_KIND)
    if not raw:
        return 0
    return int(raw)


def save_uid_cursor(provider: Any, account_id: str, mailbox: str, last_uid: int) -> None:
    store = getattr(provider, "store", None)
    if store:
        store.set_sync_cursor(account_id, mailbox, UID_CURSOR_KIND, str(last_uid))


def emit_message_created(account, provider, mailbox, uid, emit) -> None:
    try:
        msg = provider.get_message(mailbox, str(uid), peek=True)
    except Exception as exc:
        log.warning("fetch uid %s failed: %s", uid, exc)
        return
    emit(
        Event(
            id=new_id("evt"),
            ts=utcnow(),
            account_id=account.id,
            provider_id=provider.id,
            mailbox=mailbox,
            type="message.created",
            thread_id=msg.thread_id,
            message=msg.summary(),
            idempotency_key=idempotency_key(account.id, mailbox, "message.created", str(uid)),
        )
    )


class PollWatcher:
    plugin_type = "watcher"
    id = "poll"

    def supports(self, account: AccountConfig, provider: Any) -> bool:
        return True

    def watch(self, account: AccountConfig, provider: Any, emit: Callable[[Event], None], stop: threading.Event) -> None:
        mailbox = account.folders.inbox or "INBOX"
        interval = max(15, int(account.poll_interval or 45))
        backoff = Backoff(initial=interval, maximum=300)
        last_uid = load_uid_cursor(provider, account.id, mailbox)
        while not stop.is_set():
            try:
                provider.connect()
                uids = provider.recent_uids(mailbox, last_uid if last_uid else None)
                if not last_uid and uids:
                    # First run: record high-water mark, backfill only recent 50.
                    last_uid = max(uids)
                    recent = sorted(uids)[-50:]
                    emit(
                        Event(
                            id=new_id("evt"),
                            ts=utcnow(),
                            account_id=account.id,
                            provider_id=provider.id,
                            mailbox=mailbox,
                            type="service.backfill.started",
                            data={"count": len(recent)},
                        )
                    )
                    for uid in recent:
                        emit_message_created(account, provider, mailbox, uid, emit)
                    emit(
                        Event(
                            id=new_id("evt"),
                            ts=utcnow(),
                            account_id=account.id,
                            provider_id=provider.id,
                            mailbox=mailbox,
                            type="service.backfill.completed",
                            data={"uid": last_uid},
                        )
                    )
                else:
                    for uid in uids:
                        if uid <= last_uid:
                            continue
                        last_uid = uid
                        emit_message_created(account, provider, mailbox, uid, emit)
                save_uid_cursor(provider, account.id, mailbox, last_uid)
                backoff.reset()
                stop.wait(interval)
            except Exception as exc:
                log.warning("poll error account=%s: %s", account.id, exc)
                stop.wait(backoff.fail())
