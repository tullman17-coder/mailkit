"""FLAG-F1: flag/unflag/read/unread must persist into the SQLite index."""

from __future__ import annotations

import json
from types import SimpleNamespace

from mailkit.api.routes import _messages, _mutate_message
from mailkit.config import AccountConfig, AppConfig
from mailkit.db import Store
from mailkit.models import Message


class FakeProvider:
    def __init__(self):
        self.calls: list[tuple] = []

    def set_flags(self, mailbox, native_id, add=None, remove=None):
        self.calls.append((mailbox, str(native_id), list(add or []), list(remove or [])))


class FakeRuntime:
    def __init__(self, store: Store, provider: FakeProvider):
        self.store = store
        self.config = AppConfig()
        self._acc = AccountConfig(id="work", address="a@b.com")
        self.config.accounts["work"] = self._acc
        self._provider = provider

    def provider_for(self, account_id):
        return self._provider, self._acc, {}


def _seed(tmp_path, *, flagged=False, unread=True, flags=None):
    store = Store(tmp_path)
    msg = Message(
        id="msg_1",
        account_id="work",
        provider_id="imap",
        mailbox="INBOX",
        uid=17,
        native_id="17",
        subject="Hello",
        unread=unread,
        flagged=flagged,
        flags=list(flags or []),
        body_text="Hello body",
    )
    store.upsert_message(msg)
    provider = FakeProvider()
    app = SimpleNamespace(runtime=FakeRuntime(store, provider))
    return store, provider, app


def _row(store: Store, msg_id: str = "msg_1"):
    return store.conn.execute(
        "SELECT flagged, unread, flags_json, payload_json FROM messages WHERE id=?",
        (msg_id,),
    ).fetchone()


def test_flag_updates_sqlite_index(tmp_path):
    store, provider, app = _seed(tmp_path)
    result = _mutate_message(app, "msg_1", "flag", {}, {})
    assert result["ok"] is True
    assert provider.calls == [("INBOX", "17", ["Flagged"], [])]

    row = _row(store)
    assert row["flagged"] == 1
    assert row["unread"] == 1
    flags = json.loads(row["flags_json"])
    assert "Flagged" in flags
    payload = json.loads(row["payload_json"])
    assert payload["flagged"] is True
    assert "Flagged" in payload["flags"]

    cached = store.get_message("msg_1")
    assert cached["flagged"] is True
    assert "Flagged" in cached["flags"]


def test_unflag_updates_sqlite_index(tmp_path):
    store, provider, app = _seed(tmp_path, flagged=True, flags=["Flagged"])
    _mutate_message(app, "msg_1", "unflag", {}, {})
    assert provider.calls == [("INBOX", "17", [], ["Flagged"])]

    row = _row(store)
    assert row["flagged"] == 0
    flags = json.loads(row["flags_json"])
    assert "Flagged" not in flags
    payload = json.loads(row["payload_json"])
    assert payload["flagged"] is False
    assert "Flagged" not in payload["flags"]


def test_read_unread_updates_sqlite_index(tmp_path):
    store, provider, app = _seed(tmp_path, unread=True, flags=[])
    _mutate_message(app, "msg_1", "read", {}, {})
    assert provider.calls[-1] == ("INBOX", "17", ["Seen"], [])

    row = _row(store)
    assert row["unread"] == 0
    flags = json.loads(row["flags_json"])
    assert "Seen" in flags
    payload = json.loads(row["payload_json"])
    assert payload["unread"] is False
    assert "Seen" in payload["flags"]

    _mutate_message(app, "msg_1", "unread", {}, {})
    assert provider.calls[-1] == ("INBOX", "17", [], ["Seen"])

    row = _row(store)
    assert row["unread"] == 1
    flags = json.loads(row["flags_json"])
    assert "Seen" not in flags
    payload = json.loads(row["payload_json"])
    assert payload["unread"] is True
    assert "Seen" not in payload["flags"]


def test_get_message_returns_updated_flags_after_mutation(tmp_path):
    store, provider, app = _seed(tmp_path)
    _mutate_message(app, "msg_1", "flag", {}, {})
    got = _messages(app, "GET", ["msg_1"], {}, {})
    assert got["ok"] is True
    assert got["data"]["flagged"] is True
    assert "Flagged" in got["data"]["flags"]


def test_flag_mutation_emits_events(tmp_path):
    store, _, app = _seed(tmp_path)
    from mailkit.events import EventBus

    app.bus = EventBus(store)
    _mutate_message(app, "msg_1", "flag", {}, {})
    _mutate_message(app, "msg_1", "read", {}, {})
    types = [e["type"] for e in store.events_after(None, limit=10)]
    assert "message.flagged" in types
    assert "message.read" in types
