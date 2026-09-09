"""FLAG-F7: watchers must emit flag/read events and update the index."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import mailkit.watchers.idle as idle_mod
from mailkit.config import AccountConfig
from mailkit.db import Store
from mailkit.models import Message
from mailkit.watchers.gmail_push import GmailPushWatcher
from mailkit.watchers.graph_push import GraphPushWatcher, _emit_delta_item
from mailkit.watchers.idle import IdleWatcher
from mailkit.watchers.poll import _poll_cycle


def _account():
    return AccountConfig(id="work", address="ops@example.com")


def _msg(uid: int, *, flagged: bool = False, unread: bool = True, **kwargs) -> Message:
    flags = list(kwargs.pop("flags", []))
    if flagged and not any(f.lower() == "flagged" for f in flags):
        flags.append("Flagged")
    if not unread and not any(f.lower() == "seen" for f in flags):
        flags.append("Seen")
    return Message(
        id=kwargs.get("id") or f"msg_{uid}",
        account_id="work",
        provider_id=kwargs.get("provider_id") or "imap",
        mailbox="INBOX",
        uid=uid,
        native_id=str(kwargs.get("native_id") or uid),
        thread_id=kwargs.get("thread_id") or "thr_1",
        subject=kwargs.get("subject") or "Hello",
        flagged=flagged,
        unread=unread,
        flags=flags,
    )


class FakeImapProvider:
    id = "imap"

    def __init__(self, by_uid: dict[int, Message], store: Store | None = None):
        self.by_uid = by_uid
        self.store = store
        self.high_water = max(by_uid) if by_uid else 0

    def recent_uids(self, mailbox, since_uid):
        uids = sorted(self.by_uid)
        if since_uid:
            return [u for u in uids if u > int(since_uid)]
        return list(uids)

    def get_message(self, mailbox, native_id, peek=True):
        msg = self.by_uid[int(native_id)]
        if self.store:
            self.store.upsert_message(msg)
        return msg


def _event_types(events):
    return [e.type for e in events]


def test_idle_fetch_emits_flagged_and_read(monkeypatch, tmp_path):
    store = Store(tmp_path)
    original = _msg(10, flagged=False, unread=True)
    store.upsert_message(original)
    current = _msg(10, flagged=True, unread=False)
    provider = FakeImapProvider({10: original}, store)
    events = []
    stop = threading.Event()
    wakes = {"n": 0}

    def fake_wait(client, duration, stop_event):
        wakes["n"] += 1
        if wakes["n"] == 1:
            provider.by_uid[10] = current
            return True
        stop_event.set()
        return False

    monkeypatch.setattr(idle_mod, "_wait_idle", fake_wait)
    IdleWatcher()._idle_loop(_account(), provider, SimpleNamespace(), "INBOX", 10, events.append, stop)

    assert "message.flagged" in _event_types(events)
    assert "message.read" in _event_types(events)
    assert "message.created" not in _event_types(events)
    cached = store.get_message("msg_10")
    assert cached["flagged"] is True
    assert cached["unread"] is False


def test_idle_new_uid_still_emits_created(monkeypatch):
    provider = FakeImapProvider({11: _msg(11)})
    events = []
    stop = threading.Event()
    wakes = {"n": 0}

    def fake_wait(client, duration, stop_event):
        wakes["n"] += 1
        if wakes["n"] == 1:
            return True
        stop_event.set()
        return False

    monkeypatch.setattr(idle_mod, "_wait_idle", fake_wait)
    IdleWatcher()._idle_loop(_account(), provider, SimpleNamespace(), "INBOX", 10, events.append, stop)
    assert "message.created" in _event_types(events)
    assert events[0].message["uid"] == 11


def test_poll_emits_unflagged_for_existing_uid(tmp_path):
    store = Store(tmp_path)
    store.upsert_message(_msg(7, flagged=True, unread=False))
    provider = FakeImapProvider({7: _msg(7, flagged=False, unread=False)}, store)
    events = []
    last_uid, flag_state = _poll_cycle(_account(), provider, "INBOX", 7, {}, events.append)
    assert last_uid == 7
    assert "message.unflagged" in _event_types(events)
    assert "message.created" not in _event_types(events)
    cached = store.get_message("msg_7")
    assert cached["flagged"] is False


def test_poll_new_uid_emits_created():
    provider = FakeImapProvider({4: _msg(4)})
    events = []
    last_uid, flag_state = _poll_cycle(_account(), provider, "INBOX", 3, {}, events.append)
    assert last_uid == 4
    assert _event_types(events) == ["message.created"]
    assert flag_state[4] == (False, True)


def test_gmail_labels_added_starred_emits_flagged(tmp_path):
    store = Store(tmp_path)
    store.upsert_message(_msg(1, native_id="abc", id="msg_g", flagged=False))
    provider = SimpleNamespace(id="gmail", store=store)
    events = []
    GmailPushWatcher()._emit_history_item(
        _account(),
        provider,
        "INBOX",
        {"id": "99", "labelsAdded": [{"message": {"id": "abc"}, "labelIds": ["STARRED"]}]},
        events.append,
    )
    assert "message.flagged" in _event_types(events)
    assert store.get_message("msg_g")["flagged"] is True


def test_gmail_labels_removed_starred_and_unread(tmp_path):
    store = Store(tmp_path)
    store.upsert_message(_msg(1, native_id="abc", id="msg_g", flagged=True, unread=True, flags=["Flagged"]))
    provider = SimpleNamespace(id="gmail", store=store)
    events = []
    GmailPushWatcher()._emit_history_item(
        _account(),
        provider,
        "INBOX",
        {
            "id": "100",
            "labelsRemoved": [
                {"message": {"id": "abc"}, "labelIds": ["STARRED", "UNREAD"]},
            ],
        },
        events.append,
    )
    types = _event_types(events)
    assert "message.unflagged" in types
    assert "message.read" in types
    cached = store.get_message("msg_g")
    assert cached["flagged"] is False
    assert cached["unread"] is False


def test_gmail_labels_added_unread_emits_unread():
    events = []
    GmailPushWatcher()._emit_history_item(
        _account(),
        SimpleNamespace(id="gmail", store=None),
        "INBOX",
        {"id": "101", "labelsAdded": [{"message": {"id": "xyz"}, "labelIds": ["UNREAD"]}]},
        events.append,
    )
    assert _event_types(events) == ["message.unread"]


def test_graph_delta_emits_flag_events_instead_of_created(tmp_path):
    store = Store(tmp_path)
    store.upsert_message(
        Message(
            id="msg_graph_mid",
            account_id="work",
            provider_id="graph",
            mailbox="inbox",
            native_id="mid",
            flagged=False,
            unread=True,
        )
    )
    events = []
    item = {
        "id": "mid",
        "conversationId": "thr",
        "subject": "Hello",
        "isRead": True,
        "flag": {"flagStatus": "flagged"},
        "bodyPreview": "",
    }
    _emit_delta_item(_account(), SimpleNamespace(id="graph", store=store), "inbox", item, events.append)
    types = _event_types(events)
    assert "message.flagged" in types
    assert "message.read" in types
    assert "message.created" not in types
    cached = store.get_message("msg_graph_mid")
    assert cached["flagged"] is True
    assert cached["unread"] is False


def test_graph_delta_new_message_still_created():
    events = []
    item = {"id": "new1", "subject": "Hi", "isRead": False, "flag": {"flagStatus": "notFlagged"}}
    _emit_delta_item(_account(), SimpleNamespace(id="graph", store=None), "inbox", item, events.append)
    assert _event_types(events) == ["message.created"]


def test_graph_delta_removed_still_deleted():
    events = []
    item = {"id": "gone", "@removed": {"reason": "deleted"}}
    _emit_delta_item(_account(), SimpleNamespace(id="graph", store=None), "inbox", item, events.append)
    assert _event_types(events) == ["message.deleted"]
