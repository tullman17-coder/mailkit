"""Text and JSON output plus process exit handling."""

from __future__ import annotations

import json
import sys
from typing import Any

from mailkit.errors import ExitCode, MailkitError
from mailkit.models import fail, ok


class Printer:
    def __init__(self, fmt: str = "text"):
        self.fmt = fmt if fmt in {"text", "json", "ndjson"} else "text"

    def data(self, value: Any, *, text: str | None = None) -> None:
        if self.fmt == "json":
            sys.stdout.write(json.dumps(ok(value), default=str, indent=2) + "\n")
        elif self.fmt == "ndjson":
            if isinstance(value, list):
                for item in value:
                    sys.stdout.write(json.dumps(item, default=str) + "\n")
            else:
                sys.stdout.write(json.dumps(value, default=str) + "\n")
        else:
            sys.stdout.write((text if text is not None else _stringify(value)) + ("" if (text or "").endswith("\n") else "\n"))

    def event(self, payload: dict) -> None:
        if self.fmt == "text":
            msg = payload.get("message") or {}
            subject = msg.get("subject") or payload.get("type")
            sys.stdout.write(
                f"{payload.get('ts','')}  {payload.get('account_id','')}  "
                f"{payload.get('mailbox','')}  {payload.get('type')}  {subject}\n"
            )
        else:
            sys.stdout.write(json.dumps(payload, default=str) + "\n")
        sys.stdout.flush()

    def error(self, exc: BaseException) -> int:
        if isinstance(exc, MailkitError):
            payload = fail(exc.to_dict())
            code = exc.exit_code
        else:
            payload = fail({"code": "error", "message": str(exc)})
            code = ExitCode.ERROR
        if self.fmt == "text":
            sys.stderr.write(f"mailkit: {payload['error']['message']}\n")
        else:
            sys.stderr.write(json.dumps(payload, default=str) + "\n")
        return code


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not value:
            return "(none)"
        if all(isinstance(v, dict) for v in value):
            return "\n".join(_row(v) for v in value)
        return "\n".join(str(v) for v in value)
    if isinstance(value, dict):
        return _row(value)
    return str(value)


def _row(item: dict) -> str:
    if "address" in item and "id" in item:
        return f"{item.get('id'):<16} {item.get('address',''):<32} {item.get('provider',''):<8} {item.get('name','')}"
    if item.get("schema") == "mailkit.message.v1" or "subject" in item:
        unread = "●" if item.get("unread") else " "
        flag = "*" if item.get("flagged") else " "
        acc = item.get("account_id") or ""
        return f"{unread}{flag} {acc:<12} {item.get('mailbox',''):<16} {item.get('id',''):<24} {item.get('subject','')}"
    if "type" in item and "account_id" in item:
        return f"{item.get('ts','')} {item.get('account_id')} {item.get('type')} {item.get('id')}"
    return json.dumps(item, default=str)
