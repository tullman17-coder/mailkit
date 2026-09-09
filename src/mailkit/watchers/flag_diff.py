"""Diff previous flags/read state and emit message.flagged/unflagged/read/unread."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from mailkit.ids import idempotency_key, new_id
from mailkit.logutil import get_logger
from mailkit.models import Address, Event, Message, utcnow

log = get_logger("mailkit.watchers.flags")

GMAIL_STARRED = "STARRED"
GMAIL_UNREAD = "UNREAD"


def flag_event_types(
    *,
    prev_flagged: bool | None,
    prev_unread: bool | None,
    flagged: bool,
    unread: bool,
) -> list[str]:
    types: list[str] = []
    if prev_flagged is not None and bool(flagged) != bool(prev_flagged):
        types.append("message.flagged" if flagged else "message.unflagged")
    if prev_unread is not None and bool(unread) != bool(prev_unread):
        types.append("message.unread" if unread else "message.read")
    return types


def gmail_label_event_types(label_ids: Iterable[Any] | None, *, added: bool) -> list[str]:
    labels = {str(x).upper() for x in (label_ids or [])}
    types: list[str] = []
    if added:
        if GMAIL_STARRED in labels:
            types.append("message.flagged")
        if GMAIL_UNREAD in labels:
            types.append("message.unread")
    else:
        if GMAIL_STARRED in labels:
            types.append("message.unflagged")
        if GMAIL_UNREAD in labels:
            types.append("message.read")
    return types


def snapshot_of(msg: Message | dict) -> tuple[bool, bool]:
    if isinstance(msg, dict):
        return bool(msg.get("flagged")), bool(msg.get("unread", True))
    return bool(getattr(msg, "flagged", False)), bool(getattr(msg, "unread", True))


def emit_typed_events(
    account,
    provider_id: str,
    mailbox: str,
    event_types: Iterable[str],
    emit: Callable[[Event], None],
    *,
    native_id: str,
    thread_id: str = "",
    message: dict | None = None,
    data: dict | None = None,
) -> None:
    for etype in event_types:
        emit(
            Event(
                id=new_id("evt"),
                ts=utcnow(),
                account_id=account.id,
                provider_id=provider_id,
                mailbox=mailbox,
                type=etype,
                thread_id=thread_id or "",
                message=message,
                data=data or {},
                idempotency_key=idempotency_key(account.id, mailbox, etype, str(native_id or "")),
            )
        )


def find_indexed(store, account_id: str, native_id: str | None, mailbox: str | None = None) -> dict | None:
    if not store or not native_id:
        return None
    native = str(native_id)
    finder = getattr(store, "find_by_native", None)
    if callable(finder):
        found = finder(account_id, native, mailbox=mailbox)
        if found:
            return found
    try:
        rows = store.list_messages(account_id=account_id, mailbox=mailbox, limit=500)
    except Exception:
        return None
    for row in rows:
        if str(row.get("native_id") or "") == native or str(row.get("uid") or "") == native:
            return row
    return None


def seed_flag_state(store, account_id: str, mailbox: str, flag_state: dict[int, tuple[bool, bool]]) -> None:
    if not store:
        return
    try:
        rows = store.list_messages(account_id=account_id, mailbox=mailbox, limit=500)
    except Exception:
        return
    for row in rows:
        uid = row.get("uid")
        if uid is None:
            continue
        uid_i = int(uid)
        if uid_i not in flag_state:
            flag_state[uid_i] = snapshot_of(row)


def refresh_known_flags(
    account,
    provider,
    mailbox: str,
    flag_state: dict[int, tuple[bool, bool]],
    emit: Callable[[Event], None],
) -> None:
    pid = getattr(provider, "id", "imap")
    for uid in list(flag_state):
        try:
            msg = provider.get_message(mailbox, str(uid), peek=True)
        except Exception as exc:
            log.debug("flag refresh uid %s failed: %s", uid, exc)
            continue
        prev_flagged, prev_unread = flag_state[uid]
        types = flag_event_types(
            prev_flagged=prev_flagged,
            prev_unread=prev_unread,
            flagged=msg.flagged,
            unread=msg.unread,
        )
        if types:
            emit_typed_events(
                account,
                pid,
                mailbox,
                types,
                emit,
                native_id=str(getattr(msg, "native_id", None) or uid),
                thread_id=getattr(msg, "thread_id", "") or "",
                message=msg.summary() if hasattr(msg, "summary") else None,
            )
        flag_state[uid] = snapshot_of(msg)


def persist_index_flags(store, row: dict, *, flagged: bool | None = None, unread: bool | None = None) -> dict | None:
    if not store or not row:
        return None
    if flagged is None:
        flagged = bool(row.get("flagged"))
    if unread is None:
        unread = bool(row.get("unread", True))
    flags = [f for f in (row.get("flags") or []) if str(f).lower() not in {"flagged", "seen"}]
    if flagged:
        flags.append("Flagged")
    if not unread:
        flags.append("Seen")
    updated = {**row, "flagged": bool(flagged), "unread": bool(unread), "flags": flags}
    store.upsert_message(_message_from_row(updated))
    return updated


def _message_from_row(row: dict) -> Message:
    def addrs(key: str) -> list[Address]:
        out: list[Address] = []
        for item in row.get(key) or []:
            if isinstance(item, dict):
                out.append(Address(address=item.get("address") or "", name=item.get("name") or ""))
        return out

    return Message(
        id=row.get("id") or "",
        account_id=row.get("account_id") or "",
        provider_id=row.get("provider_id") or "",
        mailbox=row.get("mailbox") or "",
        uid=row.get("uid"),
        native_id=str(row.get("native_id") or ""),
        message_id=row.get("message_id") or "",
        thread_id=row.get("thread_id") or "",
        subject=row.get("subject") or "",
        from_=addrs("from"),
        to=addrs("to"),
        cc=addrs("cc"),
        flags=list(row.get("flags") or []),
        labels=list(row.get("labels") or []),
        tags=list(row.get("tags") or []),
        unread=bool(row.get("unread", True)),
        flagged=bool(row.get("flagged")),
        has_attachments=bool(row.get("has_attachments")),
        snippet=row.get("snippet") or "",
    )
