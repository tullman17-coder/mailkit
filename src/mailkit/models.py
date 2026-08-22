"""Normalized mail, account, mailbox, and event models. JSON-stable."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


SCHEMA_EVENT = "mailkit.event.v1"
SCHEMA_MESSAGE = "mailkit.message.v1"
SCHEMA_ACCOUNT = "mailkit.account.v1"
SCHEMA_MAILBOX = "mailkit.mailbox.v1"
SCHEMA_RESPONSE = "mailkit.response.v1"
SCHEMA_SUBSCRIPTION = "mailkit.subscription.v1"
SCHEMA_WEBHOOK = "mailkit.webhook.v1"
SCHEMA_RULE = "mailkit.rule.v1"
SCHEMA_STATUS = "mailkit.status.v1"

EVENT_TYPES = (
    "message.created",
    "message.updated",
    "message.deleted",
    "message.moved",
    "message.flagged",
    "message.unflagged",
    "message.read",
    "message.unread",
    "message.tagged",
    "mailbox.created",
    "mailbox.deleted",
    "account.connected",
    "account.disconnected",
    "account.auth_error",
    "service.backfill.started",
    "service.backfill.completed",
    "service.heartbeat",
    "service.doctor",
)

MAILBOX_ROLES = ("inbox", "saved", "sent", "drafts", "archives", "trash", "junk", "custom")


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


@dataclass
class Address:
    address: str
    name: str = ""

    def __str__(self) -> str:
        return f"{self.name} <{self.address}>" if self.name else self.address


@dataclass
class AttachmentMeta:
    filename: str = ""
    content_type: str = "application/octet-stream"
    size: int = 0


@dataclass
class Message:
    schema: str = SCHEMA_MESSAGE
    id: str = ""
    account_id: str = ""
    provider_id: str = ""
    mailbox: str = ""
    uid: int | None = None
    uidvalidity: int | None = None
    native_id: str = ""
    message_id: str = ""
    thread_id: str = ""
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    date: str = ""
    subject: str = ""
    from_: list[Address] = field(default_factory=list)
    to: list[Address] = field(default_factory=list)
    cc: list[Address] = field(default_factory=list)
    bcc: list[Address] = field(default_factory=list)
    reply_to: list[Address] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    unread: bool = True
    flagged: bool = False
    draft: bool = False
    answered: bool = False
    has_attachments: bool = False
    attachments: list[AttachmentMeta] = field(default_factory=list)
    attachment_types: list[str] = field(default_factory=list)
    snippet: str = ""
    body_text: str | None = None
    body_html: str | None = None
    size: int = 0
    internal_date: str = ""

    def to_dict(self, *, include_body: bool = True) -> dict:
        data = to_jsonable(self)
        data["from"] = data.pop("from_")
        if not include_body:
            data.pop("body_text", None)
            data.pop("body_html", None)
        return data

    def summary(self) -> dict:
        return self.to_dict(include_body=False)


@dataclass
class Mailbox:
    schema: str = SCHEMA_MAILBOX
    name: str = ""
    role: str = "custom"
    delimiter: str = "/"
    flags: list[str] = field(default_factory=list)
    special_use: list[str] = field(default_factory=list)
    selectable: bool = True
    exists: int | None = None
    unseen: int | None = None
    account_id: str = ""


@dataclass
class Event:
    schema: str = SCHEMA_EVENT
    id: str = ""
    ts: str = field(default_factory=utcnow)
    account_id: str = ""
    provider_id: str = ""
    mailbox: str = ""
    type: str = "message.created"
    thread_id: str = ""
    message: dict | None = None
    data: dict = field(default_factory=dict)
    idempotency_key: str = ""

    def to_dict(self) -> dict:
        return to_jsonable(self)


@dataclass
class EventFilter:
    account: list[str] = field(default_factory=list)
    mailbox: list[str] = field(default_factory=list)
    sender: list[str] = field(default_factory=list)
    recipient: list[str] = field(default_factory=list)
    subject: list[str] = field(default_factory=list)
    label: list[str] = field(default_factory=list)
    category: list[str] = field(default_factory=list)
    tag: list[str] = field(default_factory=list)
    thread: list[str] = field(default_factory=list)
    attachment_type: list[str] = field(default_factory=list)
    event_type: list[str] = field(default_factory=list)
    rule: list[str] = field(default_factory=list)
    query: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in to_jsonable(self).items() if v}

    @classmethod
    def from_dict(cls, data: dict | None) -> "EventFilter":
        if not data:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        payload = {}
        for key, value in data.items():
            if key not in known:
                continue
            if key == "query":
                payload[key] = value or ""
            elif isinstance(value, str):
                payload[key] = [value]
            elif isinstance(value, Iterable):
                payload[key] = list(value)
        return cls(**payload)


def ok(data: Any, **extra: Any) -> dict:
    body = {"ok": True, "schema": SCHEMA_RESPONSE, "data": data, "error": None}
    body.update(extra)
    return body


def fail(error: dict) -> dict:
    return {"ok": False, "schema": SCHEMA_RESPONSE, "data": None, "error": error}
