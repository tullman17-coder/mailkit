"""Stable v1 JSON schemas. Additive changes only."""

from __future__ import annotations

import json
from pathlib import Path

SCHEMAS: dict[str, dict] = {
    "error": {
        "$id": "mailkit.error.v1",
        "type": "object",
        "required": ["schema", "code", "message"],
        "properties": {
            "schema": {"const": "mailkit.error.v1"},
            "code": {"type": "string"},
            "message": {"type": "string"},
            "details": {"type": "object"},
        },
    },
    "response": {
        "$id": "mailkit.response.v1",
        "type": "object",
        "required": ["ok", "schema", "data", "error"],
        "properties": {
            "ok": {"type": "boolean"},
            "schema": {"const": "mailkit.response.v1"},
            "data": {},
            "error": {"oneOf": [{"type": "null"}, {"$ref": "mailkit.error.v1"}]},
        },
    },
    "address": {
        "$id": "mailkit.address.v1",
        "type": "object",
        "required": ["address"],
        "properties": {"address": {"type": "string"}, "name": {"type": "string"}},
    },
    "message": {
        "$id": "mailkit.message.v1",
        "type": "object",
        "required": ["schema", "id", "account_id", "provider_id", "mailbox"],
        "properties": {
            "schema": {"const": "mailkit.message.v1"},
            "id": {"type": "string"},
            "account_id": {"type": "string"},
            "provider_id": {"type": "string"},
            "mailbox": {"type": "string"},
            "uid": {"type": ["integer", "null"]},
            "uidvalidity": {"type": ["integer", "null"]},
            "native_id": {"type": "string"},
            "message_id": {"type": "string"},
            "thread_id": {"type": "string"},
            "in_reply_to": {"type": "string"},
            "references": {"type": "array", "items": {"type": "string"}},
            "date": {"type": "string"},
            "subject": {"type": "string"},
            "from": {"type": "array", "items": {"$ref": "mailkit.address.v1"}},
            "to": {"type": "array", "items": {"$ref": "mailkit.address.v1"}},
            "cc": {"type": "array", "items": {"$ref": "mailkit.address.v1"}},
            "flags": {"type": "array", "items": {"type": "string"}},
            "labels": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
            "unread": {"type": "boolean"},
            "flagged": {"type": "boolean"},
            "draft": {"type": "boolean"},
            "has_attachments": {"type": "boolean"},
            "attachment_types": {"type": "array", "items": {"type": "string"}},
            "snippet": {"type": "string"},
            "body_text": {"type": ["string", "null"]},
            "body_html": {"type": ["string", "null"]},
            "size": {"type": "integer"},
        },
        "additionalProperties": True,
    },
    "event": {
        "$id": "mailkit.event.v1",
        "type": "object",
        "required": ["schema", "id", "ts", "account_id", "provider_id", "type"],
        "properties": {
            "schema": {"const": "mailkit.event.v1"},
            "id": {"type": "string", "description": "Stable event id"},
            "ts": {"type": "string"},
            "account_id": {"type": "string"},
            "provider_id": {"type": "string"},
            "mailbox": {"type": "string"},
            "type": {"type": "string"},
            "thread_id": {"type": "string"},
            "message": {"oneOf": [{"type": "null"}, {"$ref": "mailkit.message.v1"}]},
            "data": {"type": "object"},
            "idempotency_key": {"type": "string"},
        },
        "additionalProperties": True,
    },
    "account": {
        "$id": "mailkit.account.v1",
        "type": "object",
        "required": ["id", "address"],
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "address": {"type": "string"},
            "provider": {"type": "string"},
            "auth": {"type": "string"},
            "enabled": {"type": "boolean"},
        },
        "additionalProperties": True,
    },
    "mailbox": {
        "$id": "mailkit.mailbox.v1",
        "type": "object",
        "required": ["name", "role"],
        "properties": {
            "name": {"type": "string"},
            "role": {"enum": ["inbox", "saved", "sent", "drafts", "archives", "trash", "junk", "custom"]},
            "account_id": {"type": "string"},
        },
        "additionalProperties": True,
    },
    "subscription": {
        "$id": "mailkit.subscription.v1",
        "type": "object",
        "required": ["id", "filter"],
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "filter": {"type": "object"},
            "cursor": {"type": ["string", "null"]},
            "durable": {"type": "boolean"},
            "ack_required": {"type": "boolean"},
        },
    },
    "webhook": {
        "$id": "mailkit.webhook.v1",
        "type": "object",
        "required": ["id", "url"],
        "properties": {
            "id": {"type": "string"},
            "url": {"type": "string"},
            "filter": {"type": "object"},
            "enabled": {"type": "boolean"},
        },
    },
    "doctor": {
        "$id": "mailkit.doctor.v1",
        "type": "object",
        "required": ["schema", "ts", "ok", "findings"],
        "properties": {
            "schema": {"const": "mailkit.doctor.v1"},
            "ts": {"type": "string"},
            "ok": {"type": "boolean"},
            "passed": {"type": "integer"},
            "repaired": {"type": "integer"},
            "failed": {"type": "integer"},
            "skipped": {"type": "integer"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["check", "status", "severity", "message"],
                    "properties": {
                        "check": {"type": "string"},
                        "title": {"type": "string"},
                        "severity": {"enum": ["ok", "info", "warn", "error", "critical"]},
                        "status": {"enum": ["pass", "fail", "repaired", "repair_failed", "skip"]},
                        "message": {"type": "string"},
                        "repair": {"type": ["string", "null"]},
                        "auto": {"type": "boolean"},
                        "details": {"type": "object"},
                    },
                },
            },
        },
    },
    "pair": {
        "$id": "mailkit.pair.v1",
        "type": "object",
        "required": ["schema", "url", "token", "deeplink"],
        "properties": {
            "schema": {"const": "mailkit.pair.v1"},
            "url": {"type": "string"},
            "loopback_url": {"type": "string"},
            "urls": {"type": "array", "items": {"type": "string"}},
            "token": {"type": "string"},
            "deeplink": {"type": "string"},
            "allow_remote": {"type": "boolean"},
            "host": {"type": "string"},
            "port": {"type": "integer"},
            "shell": {"type": "string"},
            "cli": {"type": "string"},
        },
    },
    "rule": {
        "$id": "mailkit.rule.v1",
        "type": "object",
        "required": ["id", "match", "actions"],
        "properties": {
            "id": {"type": "string"},
            "priority": {"type": "integer"},
            "enabled": {"type": "boolean"},
            "stop": {"type": "boolean"},
            "match": {"type": "object"},
            "actions": {"type": "object"},
        },
    },
}


def dump_schema(name: str) -> dict:
    if name not in SCHEMAS:
        return {"schemas": sorted(SCHEMAS)}
    return SCHEMAS[name]


def write_schema_files(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, schema in SCHEMAS.items():
        (directory / f"{name}.json").write_text(json.dumps(schema, indent=2) + "\n")
