"""FLAG-F8: rule/plugin flag and read actions must persist after IMAP STORE."""

from __future__ import annotations

import json

from mailkit.config import AccountConfig, AppConfig
from mailkit.db import Store
from mailkit.events import EventBus
from mailkit.ids import new_id
from mailkit.models import Event, Message
from mailkit.rules import apply_imap_flags, flag_event_types
from mailkit.supervisor import AccountWorker


class RecordingProvider:
    id = "imap"

    def __init__(self, *, fail_on=None):
        self.calls: list[tuple] = []
        self.fail_on = fail_on or set()

    def set_flags(self, mailbox, native_id, add=None, remove=None):
        add = list(add or [])
        remove = list(remove or [])
        self.calls.append((mailbox, str(native_id), add, remove))
        key = tuple(add or remove)
        if key[0] in self.fail_on:
            raise RuntimeError(f"STORE failed for {key}")


class FakePlugins:
    def watcher_for(self, name):
        return None

    def hook_list(self):
        return []


class FakeRuntime:
    def __init__(self, root):
        self.root = root
        self.store = Store(root)
        self.plugins = FakePlugins()
        self.config = AppConfig()
        acc = AccountConfig(id="work", address="a@b.com")
        self.config.accounts["work"] = acc
        self._acc = acc


def _seed_message(store: Store, **kwargs) -> Message:
    defaults = dict(
        id="msg_1",
        account_id="work",
        provider_id="imap",
        mailbox="INBOX",
        uid=17,
        native_id="17",
        subject="Invoice INV-1042 attached",
        unread=True,
        flagged=False,
        flags=[],
        tags=[],
    )
    defaults.update(kwargs)
    msg = Message(**defaults)
    store.upsert_message(msg)
    return msg


def _created_event(**kwargs) -> Event:
    payload = {
        "schema": "mailkit.message.v1",
        "id": "msg_1",
        "account_id": "work",
        "provider_id": "imap",
        "mailbox": "INBOX",
        "uid": 17,
        "native_id": "17",
        "subject": "Invoice INV-1042 attached",
        "from": [{"address": "billing@vendor.example", "name": "Billing"}],
        "to": [{"address": "ops@example.com", "name": ""}],
        "flags": [],
        "tags": [],
        "unread": True,
        "flagged": False,
        "snippet": "Please review the attached invoice",
        "attachment_types": ["application/pdf"],
        "has_attachments": True,
        "thread_id": "thr_inv",
    }
    payload.update(kwargs.pop("message", {}))
    return Event(
        id=new_id("evt"),
        account_id="work",
        provider_id="imap",
        mailbox="INBOX",
        type="message.created",
        thread_id="thr_inv",
        message=payload,
        **kwargs,
    )


def _row(store: Store, msg_id: str = "msg_1"):
    return store.conn.execute(
        "SELECT flagged, unread, flags_json, payload_json FROM messages WHERE id=?",
        (msg_id,),
    ).fetchone()


def test_apply_imap_flags_sets_flagged_and_seen():
    payload = {"flags": [], "flagged": False, "unread": True}
    flagged = apply_imap_flags(payload, add=["Flagged"])
    assert flagged["flagged"] is True
    assert flagged["unread"] is True
    assert "Flagged" in flagged["flags"]

    read = apply_imap_flags(flagged, add=["Seen"])
    assert read["unread"] is False
    assert "Seen" in read["flags"]
    assert read["flagged"] is True

    cleared = apply_imap_flags(read, remove=["Flagged", "Seen"])
    assert cleared["flagged"] is False
    assert cleared["unread"] is True
    assert "Flagged" not in cleared["flags"]
    assert "Seen" not in cleared["flags"]


def test_flag_event_types_map_store_tokens():
    assert flag_event_types(add=["Flagged"]) == ["message.flagged"]
    assert flag_event_types(remove=["Flagged"]) == ["message.unflagged"]
    assert flag_event_types(add=["Seen"]) == ["message.read"]
    assert flag_event_types(remove=["Seen"]) == ["message.unread"]
    assert flag_event_types(add=["Flagged", "Seen"]) == ["message.flagged", "message.read"]


def test_rule_flag_and_read_persist_to_store_and_created_event(tmp_path):
    runtime = FakeRuntime(tmp_path)
    _seed_message(runtime.store)
    runtime.store.save_rule(
        "invoice",
        "flag invoices",
        10,
        True,
        False,
        {"subject_contains": ["Invoice"]},
        {"flag": True, "mark_read": True, "tag": ["invoice"]},
    )
    bus = EventBus(runtime.store)
    worker = AccountWorker(runtime, runtime._acc, bus)
    provider = RecordingProvider()
    event = _created_event()

    extras = worker._apply_hooks(provider, event)

    assert ("INBOX", "17", ["Flagged"], []) in provider.calls
    assert ("INBOX", "17", ["Seen"], []) in provider.calls

    row = _row(runtime.store)
    assert row["flagged"] == 1
    assert row["unread"] == 0
    flags = json.loads(row["flags_json"])
    assert "Flagged" in flags
    assert "Seen" in flags
    payload = json.loads(row["payload_json"])
    assert payload["flagged"] is True
    assert payload["unread"] is False

    cached = runtime.store.get_message("msg_1")
    assert cached["flagged"] is True
    assert cached["unread"] is False
    assert "invoice" in cached["tags"]

    assert event.message["flagged"] is True
    assert event.message["unread"] is False
    assert "Flagged" in event.message["flags"]
    assert "Seen" in event.message["flags"]
    assert "invoice" in event.message["tags"]

    extra_types = [e.type for e in extras]
    assert "message.flagged" in extra_types
    assert "message.read" in extra_types
    for extra in extras:
        assert extra.message["flagged"] is True
        assert extra.message["unread"] is False


def test_emit_publishes_flag_events_after_created(tmp_path):
    runtime = FakeRuntime(tmp_path)
    _seed_message(runtime.store)
    runtime.store.save_rule(
        "star",
        "flag all",
        1,
        True,
        False,
        {"subject_contains": ["Invoice"]},
        {"flag": True},
    )
    bus = EventBus(runtime.store)
    worker = AccountWorker(runtime, runtime._acc, bus)
    provider = RecordingProvider()
    event = _created_event()

    extras = worker._apply_hooks(provider, event)
    published = bus.publish(event)
    assert published is not None
    for extra in extras:
        assert bus.publish(extra) is not None

    types = [e["type"] for e in runtime.store.events_after(None, limit=20)]
    assert types[0] == "message.created"
    assert "message.flagged" in types
    created = runtime.store.events_after(None, limit=1)[0]
    assert created["message"]["flagged"] is True


def test_failed_store_leaves_sqlite_and_event_unchanged(tmp_path):
    runtime = FakeRuntime(tmp_path)
    _seed_message(runtime.store)
    runtime.store.save_rule(
        "star",
        "flag all",
        1,
        True,
        False,
        {"subject_contains": ["Invoice"]},
        {"flag": True, "mark_read": True},
    )
    bus = EventBus(runtime.store)
    worker = AccountWorker(runtime, runtime._acc, bus)
    provider = RecordingProvider(fail_on={"Flagged", "Seen"})
    event = _created_event()

    extras = worker._apply_hooks(provider, event)

    row = _row(runtime.store)
    assert row["flagged"] == 0
    assert row["unread"] == 1
    assert event.message["flagged"] is False
    assert event.message["unread"] is True
    assert extras == []


def test_unflag_and_unread_actions(tmp_path):
    runtime = FakeRuntime(tmp_path)
    _seed_message(runtime.store, flagged=True, unread=False, flags=["Flagged", "Seen"])
    runtime.store.save_rule(
        "clear",
        "clear flags",
        1,
        True,
        False,
        {"subject_contains": ["Invoice"]},
        {"flag": False, "mark_read": False},
    )
    worker = AccountWorker(runtime, runtime._acc, EventBus(runtime.store))
    provider = RecordingProvider()
    event = _created_event(message={"flagged": True, "unread": False, "flags": ["Flagged", "Seen"]})

    extras = worker._apply_hooks(provider, event)

    assert ("INBOX", "17", [], ["Flagged"]) in provider.calls
    assert ("INBOX", "17", [], ["Seen"]) in provider.calls
    row = _row(runtime.store)
    assert row["flagged"] == 0
    assert row["unread"] == 1
    assert event.message["flagged"] is False
    assert event.message["unread"] is True
    extra_types = [e.type for e in extras]
    assert "message.unflagged" in extra_types
    assert "message.unread" in extra_types
