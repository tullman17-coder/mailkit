"""Incremental UID/history polling. Fallback only when IDLE/push is unavailable."""

from __future__ import annotations

import threading
from typing import Any, Callable

from mailkit.backoff import Backoff
from mailkit.config import AccountConfig
from mailkit.ids import idempotency_key, new_id
from mailkit.logutil import get_logger
from mailkit.models import Event, utcnow
from mailkit.watchers.flag_diff import refresh_known_flags, seed_flag_state, snapshot_of

log = get_logger("mailkit.poll")


class PollWatcher:
    plugin_type = "watcher"
    id = "poll"

    def supports(self, account: AccountConfig, provider: Any) -> bool:
        return True

    def watch(self, account: AccountConfig, provider: Any, emit: Callable[[Event], None], stop: threading.Event) -> None:
        mailbox = account.folders.inbox or "INBOX"
        interval = max(15, int(account.poll_interval or 45))
        backoff = Backoff(initial=interval, maximum=300)
        last_uid = 0
        flag_state: dict[int, tuple[bool, bool]] = {}
        if getattr(provider, "store", None):
            raw = provider.store.get_sync_cursor(account.id, mailbox, "uid")
            if raw:
                last_uid = int(raw)
            seed_flag_state(provider.store, account.id, mailbox, flag_state)
        while not stop.is_set():
            try:
                provider.connect()
                last_uid, flag_state = _poll_cycle(account, provider, mailbox, last_uid, flag_state, emit)
                if getattr(provider, "store", None):
                    provider.store.set_sync_cursor(account.id, mailbox, "uid", str(last_uid))
                backoff.reset()
                stop.wait(interval)
            except Exception as exc:
                log.warning("poll error account=%s: %s", account.id, exc)
                stop.wait(backoff.fail())


def _poll_cycle(account, provider, mailbox, last_uid, flag_state, emit) -> tuple[int, dict[int, tuple[bool, bool]]]:
    flag_state = dict(flag_state)
    store = getattr(provider, "store", None)
    seed_flag_state(store, account.id, mailbox, flag_state)
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
            msg = _emit_created(account, provider, mailbox, uid, emit)
            if msg is not None:
                flag_state[int(uid)] = snapshot_of(msg)
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
            msg = _emit_created(account, provider, mailbox, uid, emit)
            if msg is not None:
                flag_state[int(uid)] = snapshot_of(msg)
    refresh_known_flags(account, provider, mailbox, flag_state, emit)
    return last_uid, flag_state


def _emit_created(account, provider, mailbox, uid, emit):
    try:
        msg = provider.get_message(mailbox, str(uid), peek=True)
    except Exception as exc:
        log.warning("poll fetch %s failed: %s", uid, exc)
        return None
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
    return msg
