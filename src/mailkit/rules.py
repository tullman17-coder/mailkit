"""Priority-ordered classification and routing. Safe defaults: never delete."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any

from mailkit.models import Message
from mailkit.plugins.types import HookAction, HookContext, HookPlugin

UNSAFE_ACTIONS = {"delete", "purge", "drop"}


@dataclass
class Rule:
    id: str
    name: str = ""
    priority: int = 0
    enabled: bool = True
    stop: bool = False
    match: dict[str, Any] = field(default_factory=dict)
    actions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema": "mailkit.rule.v1",
            "id": self.id,
            "name": self.name,
            "priority": self.priority,
            "enabled": self.enabled,
            "stop": self.stop,
            "match": self.match,
            "actions": self.actions,
        }


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _contains(haystack: str, needles: list[str]) -> bool:
    h = haystack.lower()
    return any(n.lower() in h for n in needles if n)


def _addr_list(addrs) -> str:
    parts = []
    for a in addrs or []:
        if isinstance(a, dict):
            parts.append(f"{a.get('name','')} {a.get('address','')}")
        else:
            parts.append(f"{getattr(a, 'name', '')} {getattr(a, 'address', '')}")
    return " ".join(parts).lower()


def rule_matches(rule: Rule, message: Message, *, account_id: str, mailbox: str) -> bool:
    if not rule.enabled:
        return False
    m = rule.match or {}
    mode = (m.get("mode") or "all").lower()
    checks: list[bool] = []

    def add(cond: bool | None) -> None:
        if cond is not None:
            checks.append(bool(cond))

    if m.get("account"):
        add(account_id in _as_list(m["account"]))
    if m.get("mailbox"):
        add(mailbox in _as_list(m["mailbox"]) or any(fnmatch.fnmatch(mailbox, p) for p in _as_list(m["mailbox"])))
    if m.get("subject_contains"):
        add(_contains(message.subject or "", _as_list(m["subject_contains"])))
    if m.get("subject"):
        add(any(fnmatch.fnmatch(message.subject or "", p) for p in _as_list(m["subject"])))
    if m.get("from"):
        hay = _addr_list(message.from_)
        add(any(tok.lower() in hay for tok in _as_list(m["from"])))
    if m.get("from_domain"):
        hay = _addr_list(message.from_)
        add(any(f"@{d.lower()}" in hay or hay.endswith(d.lower()) for d in _as_list(m["from_domain"])))
    if m.get("to"):
        hay = _addr_list(message.to) + _addr_list(message.cc)
        add(any(tok.lower() in hay for tok in _as_list(m["to"])))
    if m.get("recipient"):
        hay = _addr_list(message.to) + _addr_list(message.cc)
        add(any(tok.lower() in hay for tok in _as_list(m["recipient"])))
    if m.get("label"):
        labels = {x.lower() for x in (message.labels + message.tags)}
        add(any(v.lower() in labels for v in _as_list(m["label"])))
    if m.get("tag"):
        tags = {x.lower() for x in message.tags}
        add(any(v.lower() in tags for v in _as_list(m["tag"])))
    if m.get("category"):
        cats = {x.lower() for x in message.labels}
        add(any(v.lower() in cats for v in _as_list(m["category"])))
    if m.get("thread"):
        add(message.thread_id in _as_list(m["thread"]))
    if m.get("attachment_type"):
        types = {t.lower() for t in message.attachment_types}
        add(any(v.lower() in types or any(v.lower() in t for t in types) for v in _as_list(m["attachment_type"])))
    if m.get("has_attachments") is not None:
        add(bool(message.has_attachments) is bool(m["has_attachments"]))
    if m.get("query"):
        blob = " ".join([message.subject, message.snippet, _addr_list(message.from_), _addr_list(message.to)])
        add(m["query"].lower() in blob.lower())
    if m.get("regex"):
        try:
            add(bool(re.search(m["regex"], message.subject or "", re.I)))
        except re.error:
            add(False)
    if not checks:
        return False
    return all(checks) if mode == "all" else any(checks)


def apply_actions(actions: dict[str, Any]) -> HookAction:
    cleaned = {k: v for k, v in actions.items() if k not in UNSAFE_ACTIONS}
    return HookAction(
        tag=_as_list(cleaned.get("tag")),
        untag=_as_list(cleaned.get("untag")),
        move=cleaned.get("move"),
        flag=cleaned.get("flag"),
        mark_read=cleaned.get("mark_read"),
        stop=bool(cleaned.get("stop")),
        extra={k: v for k, v in cleaned.items() if k not in {"tag", "untag", "move", "flag", "mark_read", "stop"}},
    )


def merge_actions(base: HookAction, extra: HookAction) -> HookAction:
    base.tag = list(dict.fromkeys(base.tag + extra.tag))
    base.untag = list(dict.fromkeys(base.untag + extra.untag))
    if extra.move:
        base.move = extra.move
    if extra.flag is not None:
        base.flag = extra.flag
    if extra.mark_read is not None:
        base.mark_read = extra.mark_read
    base.extra.update(extra.extra)
    if extra.stop:
        base.stop = True
    return base


class RulesEngine:
    def __init__(self, rules: list[Rule] | None = None):
        self.rules = list(rules or [])

    def evaluate(self, message: Message, *, account_id: str, mailbox: str) -> HookAction:
        result = HookAction()
        ordered = sorted(self.rules, key=lambda r: r.priority, reverse=True)
        for rule in ordered:
            if not rule_matches(rule, message, account_id=account_id, mailbox=mailbox):
                continue
            result = merge_actions(result, apply_actions(rule.actions))
            result.extra.setdefault("matched_rules", []).append(rule.id)
            if rule.stop or result.stop:
                result.stop = True
                break
        return result


class RulesHook:
    plugin_type = "hook"
    id = "rules"
    priority = 1000

    def __init__(self, engine: RulesEngine):
        self.engine = engine

    def process(self, ctx: HookContext) -> HookAction | None:
        return self.engine.evaluate(ctx.message, account_id=ctx.account_id, mailbox=ctx.mailbox)


def event_filter_match(filt, event) -> bool:
    """Used by subscriptions/webhooks. Empty filter matches everything."""
    from mailkit.models import EventFilter

    if filt is None:
        return True
    if isinstance(filt, dict):
        filt = EventFilter.from_dict(filt)
    msg = event.get("message") if isinstance(event, dict) else getattr(event, "message", None)
    msg = msg or {}
    ev_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", "")
    account = event.get("account_id") if isinstance(event, dict) else getattr(event, "account_id", "")
    mailbox = event.get("mailbox") if isinstance(event, dict) else getattr(event, "mailbox", "")
    thread = event.get("thread_id") if isinstance(event, dict) else getattr(event, "thread_id", "") or msg.get("thread_id") or ""

    def want(needles: list[str], value: str, *, substr: bool = False) -> bool:
        if not needles:
            return True
        value = (value or "").lower()
        for n in needles:
            n = n.lower()
            if substr:
                if n in value:
                    return True
            elif fnmatch.fnmatch(value, n) or value == n:
                return True
        return False

    if not want(filt.account, account):
        return False
    if not want(filt.mailbox, mailbox):
        return False
    if not want(filt.event_type, ev_type):
        return False
    if not want(filt.thread, thread):
        return False
    subject = msg.get("subject") or ""
    if not want(filt.subject, subject, substr=True):
        return False
    senders = " ".join(
        a.get("address", "") if isinstance(a, dict) else str(a) for a in (msg.get("from") or [])
    )
    if not want(filt.sender, senders, substr=True):
        return False
    recips = " ".join(
        a.get("address", "") if isinstance(a, dict) else str(a)
        for a in (msg.get("to") or []) + (msg.get("cc") or [])
    )
    if not want(filt.recipient, recips, substr=True):
        return False
    labels = [x.lower() for x in (msg.get("labels") or []) + (msg.get("tags") or [])]
    if filt.label and not any(v.lower() in labels for v in filt.label):
        return False
    if filt.tag and not any(v.lower() in labels for v in filt.tag):
        return False
    if filt.category and not any(v.lower() in labels for v in filt.category):
        return False
    types = [t.lower() for t in msg.get("attachment_types") or []]
    if filt.attachment_type and not any(v.lower() in types or any(v.lower() in t for t in types) for v in filt.attachment_type):
        return False
    if filt.query:
        blob = " ".join([subject, msg.get("snippet") or "", senders, recips]).lower()
        if filt.query.lower() not in blob:
            return False
    if filt.rule:
        matched = ((event.get("data") if isinstance(event, dict) else getattr(event, "data", {})) or {}).get("matched_rules") or []
        if not any(r in matched for r in filt.rule):
            return False
    return True
