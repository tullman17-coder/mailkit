"""FLAG-F10: reply must persist IMAP \\Answered locally and not swallow STORE errors."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from mailkit.api.routes import _reply
from mailkit.config import AccountConfig, AppConfig
from mailkit.db import Store
from mailkit.errors import NetworkError
from mailkit.models import Address, Message


class FakeProvider:
    def __init__(self, *, fail_store: bool = False):
        self.sent: list[tuple] = []
        self.flag_calls: list[tuple] = []
        self.fail_store = fail_store

    def send(self, from_addr, to, raw):
        self.sent.append((from_addr, list(to), raw))
        return "<reply@example.com>"

    def set_flags(self, mailbox, native_id, add=None, remove=None):
        self.flag_calls.append((mailbox, str(native_id), list(add or []), list(remove or [])))
        if self.fail_store:
            raise RuntimeError("IMAP STORE failed")


class FakeRuntime:
    def __init__(self, store: Store, provider: FakeProvider):
        self.store = store
        self.config = AppConfig()
        self._acc = AccountConfig(id="work", address="ops@example.com")
        self.config.accounts["work"] = self._acc
        self._provider = provider

    def provider_for(self, account_id):
        return self._provider, self._acc, {}


def _seed(tmp_path, *, provider=None):
    store = Store(tmp_path)
    msg = Message(
        id="msg_1",
        account_id="work",
        provider_id="imap",
        mailbox="INBOX",
        uid=17,
        native_id="17",
        message_id="<orig@example.com>",
        subject="Hello",
        from_=[Address(address="alice@example.com", name="Alice")],
        unread=False,
        flagged=False,
        answered=False,
        flags=["Seen"],
    )
    store.upsert_message(msg)
    provider = provider or FakeProvider()
    app = SimpleNamespace(runtime=FakeRuntime(store, provider))
    return store, provider, app


def _row(store: Store, msg_id: str = "msg_1"):
    return store.conn.execute(
        "SELECT answered, flagged, unread, flags_json, payload_json FROM messages WHERE id=?",
        (msg_id,),
    ).fetchone()


def test_messages_schema_has_answered_column(tmp_path):
    store = Store(tmp_path)
    cols = {row[1] for row in store.conn.execute("PRAGMA table_info(messages)")}
    assert "answered" in cols


def test_existing_db_migrates_answered_column(tmp_path):
    from mailkit.paths import db_path

    conn = sqlite3.connect(db_path(tmp_path))
    conn.executescript(
        """
        CREATE TABLE messages (
          id TEXT PRIMARY KEY,
          account_id TEXT NOT NULL,
          provider_id TEXT NOT NULL,
          mailbox TEXT NOT NULL,
          uid INTEGER,
          uidvalidity INTEGER,
          native_id TEXT,
          message_id TEXT,
          thread_id TEXT,
          date TEXT,
          subject TEXT,
          from_json TEXT,
          to_json TEXT,
          cc_json TEXT,
          flags_json TEXT,
          labels_json TEXT,
          tags_json TEXT,
          unread INTEGER,
          flagged INTEGER,
          draft INTEGER,
          has_attachments INTEGER,
          attachment_types_json TEXT,
          snippet TEXT,
          size INTEGER,
          payload_json TEXT,
          updated_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    store = Store(tmp_path)
    cols = {row[1] for row in store.conn.execute("PRAGMA table_info(messages)")}
    assert "answered" in cols


def test_upsert_persists_answered_column(tmp_path):
    store = Store(tmp_path)
    msg = Message(
        id="msg_1",
        account_id="work",
        provider_id="imap",
        mailbox="INBOX",
        native_id="17",
        answered=True,
        flags=["Answered"],
    )
    store.upsert_message(msg)
    row = _row(store)
    assert row["answered"] == 1
    assert "Answered" in json.loads(row["flags_json"])
    assert json.loads(row["payload_json"])["answered"] is True


def test_reply_persists_answered_on_cached_message(tmp_path):
    store, provider, app = _seed(tmp_path)
    result = _reply(app, {"id": "msg_1", "body": "Thanks"})

    assert result["ok"] is True
    assert provider.flag_calls == [("INBOX", "17", ["Answered"], [])]
    assert provider.sent

    row = _row(store)
    assert row["answered"] == 1
    flags = json.loads(row["flags_json"])
    assert "Answered" in flags
    payload = json.loads(row["payload_json"])
    assert payload["answered"] is True
    assert "Answered" in payload["flags"]

    cached = store.get_message("msg_1")
    assert cached["answered"] is True
    assert "Answered" in cached["flags"]


def test_reply_store_error_is_logged_and_raised(tmp_path, caplog):
    provider = FakeProvider(fail_store=True)
    store, _, app = _seed(tmp_path, provider=provider)

    with pytest.raises(NetworkError, match="STORE"):
        _reply(app, {"id": "msg_1", "body": "Thanks"})

    assert provider.sent, "SMTP send already succeeded"
    assert any("STORE" in rec.getMessage() or "Answered" in rec.getMessage() for rec in caplog.records)

    row = _row(store)
    assert row["answered"] in (0, None)
    cached = store.get_message("msg_1")
    assert not cached.get("answered")
    assert "Answered" not in (cached.get("flags") or [])
