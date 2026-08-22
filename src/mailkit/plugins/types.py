"""Plugin contracts. New providers, auth methods, and hooks implement these."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from mailkit.config import AccountConfig
from mailkit.models import Event, Message


@dataclass
class HookAction:
    tag: list[str] = field(default_factory=list)
    untag: list[str] = field(default_factory=list)
    move: str | None = None
    flag: bool | None = None
    mark_read: bool | None = None
    stop: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookContext:
    account_id: str
    provider_id: str
    mailbox: str
    message: Message
    event: Event | None = None
    store: Any = None


class Provider(Protocol):
    id: str

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def list_mailboxes(self) -> list: ...
    def list_messages(self, mailbox: str, **query: Any) -> list[Message]: ...
    def get_message(self, mailbox: str, native_id: str, *, peek: bool = True) -> Message: ...
    def search(self, mailbox: str, **query: Any) -> list[Message]: ...
    def move(self, mailbox: str, native_id: str, dest: str) -> None: ...
    def set_flags(self, mailbox: str, native_id: str, add: list[str] | None = None, remove: list[str] | None = None) -> None: ...
    def send(self, from_addr: str, to: list[str], raw: bytes) -> str: ...
    def capabilities(self) -> set[str]: ...


class ProviderPlugin(Protocol):
    plugin_type: str
    id: str
    label: str

    def supports(self, account: AccountConfig) -> bool: ...
    def create(self, account: AccountConfig, secrets: dict[str, Any], *, store=None) -> Provider: ...


class AuthPlugin(Protocol):
    plugin_type: str
    id: str

    def prepare_imap(self, client: Any, account: AccountConfig, secrets: dict[str, Any]) -> None: ...
    def prepare_smtp(self, client: Any, account: AccountConfig, secrets: dict[str, Any]) -> None: ...
    def refresh(self, account: AccountConfig, secrets: dict[str, Any]) -> dict[str, Any] | None: ...


class HookPlugin(Protocol):
    plugin_type: str
    id: str
    priority: int

    def process(self, ctx: HookContext) -> HookAction | None: ...


class WatchPlugin(Protocol):
    plugin_type: str
    id: str

    def supports(self, account: AccountConfig, provider: Provider) -> bool: ...
    def watch(self, account: AccountConfig, provider: Provider, emit: Callable[[Event], None], stop) -> None: ...
