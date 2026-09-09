"""Offline local provider backed by the SQLite index. Used by `mailkit demo`."""

from __future__ import annotations

import json
from email.utils import make_msgid
from typing import Any

from mailkit.config import AccountConfig
from mailkit.errors import NotFoundError
from mailkit.ids import new_id
from mailkit.models import Address, Mailbox, Message, apply_system_flags, utcnow
from mailkit.providers.base import BaseProvider


LOCAL_BOXES = (
    Mailbox(name="INBOX", role="inbox", special_use=["\\Inbox"]),
    Mailbox(name="Sent", role="sent", special_use=["\\Sent"]),
    Mailbox(name="Drafts", role="drafts", special_use=["\\Drafts"]),
    Mailbox(name="Archive", role="archives", special_use=["\\Archive"]),
    Mailbox(name="Trash", role="trash", special_use=["\\Trash"]),
)


def message_from_payload(payload: dict) -> Message:
    def addrs(key: str) -> list[Address]:
        out: list[Address] = []
        for item in payload.get(key) or []:
            if isinstance(item, dict):
                out.append(Address(address=item.get("address") or "", name=item.get("name") or ""))
            elif isinstance(item, Address):
                out.append(item)
        return out

    return Message(
        id=payload.get("id") or "",
        account_id=payload.get("account_id") or "",
        provider_id=payload.get("provider_id") or "local",
        mailbox=payload.get("mailbox") or "INBOX",
        uid=payload.get("uid"),
        uidvalidity=payload.get("uidvalidity"),
        native_id=str(payload.get("native_id") or payload.get("uid") or ""),
        message_id=payload.get("message_id") or "",
        thread_id=payload.get("thread_id") or "",
        in_reply_to=payload.get("in_reply_to") or "",
        references=list(payload.get("references") or []),
        date=payload.get("date") or "",
        subject=payload.get("subject") or "",
        from_=addrs("from"),
        to=addrs("to"),
        cc=addrs("cc"),
        flags=list(payload.get("flags") or []),
        labels=list(payload.get("labels") or []),
        tags=list(payload.get("tags") or []),
        unread=bool(payload.get("unread", True)),
        flagged=bool(payload.get("flagged")),
        draft=bool(payload.get("draft")),
        answered=bool(payload.get("answered")),
        has_attachments=bool(payload.get("has_attachments")),
        snippet=payload.get("snippet") or "",
        body_text=payload.get("body_text"),
        body_html=payload.get("body_html"),
        size=int(payload.get("size") or 0),
    )


class LocalProvider(BaseProvider):
    id = "local"

    def __init__(self, account: AccountConfig, secrets: dict[str, Any], *, store=None):
        self.account = account
        self.secrets = secrets or {}
        self.store = store

    def capabilities(self) -> set[str]:
        return {"list", "read", "search", "move", "flags", "send"}

    def connect(self) -> None:
        return None

    def list_mailboxes(self):
        return [Mailbox(**{**box.__dict__, "account_id": self.account.id}) for box in LOCAL_BOXES]

    def list_messages(self, mailbox: str, **query: Any) -> list[Message]:
        if not self.store:
            return []
        rows = self.store.list_messages(
            account_id=self.account.id,
            mailbox=mailbox,
            unread=query.get("unread"),
            flagged=query.get("flagged"),
            since=query.get("since"),
            before=query.get("before"),
            query=query.get("text") or query.get("subject") or None,
            limit=int(query.get("limit") or 50),
        )
        return [message_from_payload(row) for row in rows]

    def get_message(self, mailbox: str, native_id: str, *, peek: bool = True) -> Message:
        if not self.store:
            raise NotFoundError(f"No local store for {native_id}")
        row = self.store.find_by_native(self.account.id, str(native_id), mailbox=mailbox)
        if not row:
            row = self.store.find_by_native(self.account.id, str(native_id))
        if not row:
            row = self.store.get_message(str(native_id))
        if not row:
            raise NotFoundError(f"Local message {native_id} not found")
        return message_from_payload(row)

    def move(self, mailbox: str, native_id: str, dest: str) -> None:
        if not self.store:
            return
        row = self.store.find_by_native(self.account.id, str(native_id), mailbox=mailbox)
        if not row:
            row = self.store.find_by_native(self.account.id, str(native_id))
        if row and row.get("id"):
            payload = dict(row)
            payload["mailbox"] = dest
            self.store.conn.execute(
                "UPDATE messages SET mailbox=?, payload_json=?, updated_at=? WHERE id=?",
                (dest, json.dumps(payload), utcnow(), row["id"]),
            )
            self.store.conn.commit()

    def set_flags(self, mailbox: str, native_id: str, add=None, remove=None) -> None:
        if not self.store:
            return
        row = self.store.find_by_native(self.account.id, str(native_id), mailbox=mailbox)
        if not row:
            row = self.store.find_by_native(self.account.id, str(native_id))
        if not row or not row.get("id"):
            raise NotFoundError(f"Local message {native_id} not found")
        apply_system_flags(row, add=add, remove=remove)
        self.store.patch_message_flags(row["id"], add=add, remove=remove)

    def send(self, from_addr: str, to: list[str], raw: bytes) -> str:
        mid = make_msgid()
        if not self.store:
            return mid
        text = ""
        try:
            text = raw.decode("utf-8", "replace")
        except Exception:
            text = ""
        subject = ""
        for line in text.splitlines():
            if line.lower().startswith("subject:"):
                subject = line.split(":", 1)[1].strip()
                break
        snippet = text[-400:]
        msg = Message(
            id=new_id("msg"),
            account_id=self.account.id,
            provider_id="local",
            mailbox="Sent",
            native_id=mid.strip("<>"),
            message_id=mid,
            date=utcnow(),
            subject=subject or "(no subject)",
            from_=[Address(address=from_addr)],
            to=[Address(address=addr) for addr in to],
            unread=False,
            flagged=False,
            flags=["Seen"],
            snippet=snippet[:160],
            body_text=text,
        )
        self.store.upsert_message(msg)
        return mid

    def recent_uids(self, mailbox: str, since_uid: int | None) -> list[int]:
        if not self.store:
            return []
        rows = self.store.list_messages(account_id=self.account.id, mailbox=mailbox, limit=500)
        uids = [int(r["uid"]) for r in rows if r.get("uid") is not None]
        if since_uid:
            return [u for u in uids if u > int(since_uid)]
        return uids

    def close(self) -> None:
        return None


class LocalPlugin:
    plugin_type = "provider"
    id = "local"
    label = "Local demo"

    def supports(self, account: AccountConfig) -> bool:
        return account.provider == "local"

    def create(self, account: AccountConfig, secrets: dict[str, Any], *, store=None) -> LocalProvider:
        return LocalProvider(account, secrets, store=store)
