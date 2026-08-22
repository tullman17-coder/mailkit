"""Shared provider utilities."""

from __future__ import annotations

from typing import Any

from mailkit.config import AccountConfig
from mailkit.models import Message


class BaseProvider:
    id = "base"

    def capabilities(self) -> set[str]:
        return {"list", "read", "search", "move", "flags", "send"}

    def list_mailboxes(self):
        raise NotImplementedError

    def list_messages(self, mailbox: str, **query: Any) -> list[Message]:
        raise NotImplementedError

    def get_message(self, mailbox: str, native_id: str, *, peek: bool = True) -> Message:
        raise NotImplementedError

    def search(self, mailbox: str, **query: Any) -> list[Message]:
        return self.list_messages(mailbox, **query)

    def move(self, mailbox: str, native_id: str, dest: str) -> None:
        raise NotImplementedError

    def set_flags(self, mailbox: str, native_id: str, add=None, remove=None) -> None:
        raise NotImplementedError

    def send(self, from_addr: str, to: list[str], raw: bytes) -> str:
        raise NotImplementedError

    def close(self) -> None:
        return None


def resolved_provider_id(account: AccountConfig, fallback: str) -> str:
    if account.provider and account.provider not in {"auto", ""}:
        return account.provider
    return fallback
