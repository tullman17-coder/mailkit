"""Stable identifiers for accounts, messages, events, and subscriptions."""

from __future__ import annotations

import hashlib
import secrets
import time


def new_id(prefix: str) -> str:
    ts = int(time.time() * 1000)
    return f"{prefix}_{ts:x}_{secrets.token_hex(5)}"


def message_id(account_id: str, mailbox: str, uidvalidity: int | str, uid: int | str) -> str:
    raw = f"{account_id}\0{mailbox}\0{uidvalidity}\0{uid}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"msg_{digest}"


def thread_id_from_headers(message_id_header: str | None, references: str | None, in_reply_to: str | None) -> str:
    seed = (references or in_reply_to or message_id_header or "").strip()
    if not seed:
        return new_id("thr")
    first = seed.split()[0].strip("<>")
    digest = hashlib.sha256(first.encode("utf-8")).hexdigest()[:16]
    return f"thr_{digest}"


def idempotency_key(
    account_id: str,
    mailbox: str,
    event_type: str,
    native_id: str,
    extra: str = "",
) -> str:
    raw = f"{account_id}|{mailbox}|{event_type}|{native_id}|{extra}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
