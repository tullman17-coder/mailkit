"""FLAG-P12: Graph delta must emit created/updated/deleted with a stable message id."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from mailkit.config import AccountConfig
from mailkit.db import Store
from mailkit.ids import message_id as make_message_id
from mailkit.models import Message
from mailkit.providers.graph import GraphProvider
from mailkit.watchers.graph_push import GraphPushWatcher

LONG_GRAPH_ID = (
    "AAMkAGVmMDEzMTM4LTZmYWUtNDdkNC1hMDZiLTU1OGY5OTZhYmY4OABGAAAAAAAiQ8W967B7TKBjgx9rVEUR"
)


def _account() -> AccountConfig:
    return AccountConfig(id="work", address="ops@example.com")


def _run_delta(payload: dict, *, store=None, provider=None) -> list:
    acc = _account()
    if provider is None:
        provider = GraphProvider(acc, {"access_token": "tok"}, store=store)
        provider.delta = lambda folder="inbox", delta_link=None: payload
        provider.subscribe = lambda *a, **k: None
    events = []
    stop = threading.Event()

    def emit(ev):
        events.append(ev)
        stop.set()

    GraphPushWatcher().watch(acc, provider, emit, stop)
    return events


def test_delta_removed_emits_deleted():
    events = _run_delta({"value": [{"id": LONG_GRAPH_ID, "@removed": {"reason": "deleted"}}]})
    assert [e.type for e in events] == ["message.deleted"]
    assert events[0].message is None


def test_delta_change_type_created():
    events = _run_delta(
        {"value": [{"id": LONG_GRAPH_ID, "changeType": "created", "subject": "Hi", "isRead": False}]}
    )
    assert [e.type for e in events] == ["message.created"]


def test_delta_change_type_updated_not_created():
    events = _run_delta(
        {
            "value": [
                {
                    "id": LONG_GRAPH_ID,
                    "changeType": "updated",
                    "isRead": True,
                    "flag": {"flagStatus": "flagged"},
                    "subject": "Hi",
                }
            ]
        }
    )
    assert [e.type for e in events] == ["message.updated"]


def test_delta_change_type_deleted_without_removed():
    events = _run_delta({"value": [{"id": LONG_GRAPH_ID, "changeType": "deleted"}]})
    assert [e.type for e in events] == ["message.deleted"]


def test_delta_read_change_without_change_type_is_updated_when_indexed(tmp_path):
    store = Store(tmp_path)
    stable = make_message_id("work", "inbox", "graph", LONG_GRAPH_ID)
    store.upsert_message(
        Message(
            id=stable,
            account_id="work",
            provider_id="graph",
            mailbox="inbox",
            native_id=LONG_GRAPH_ID,
            subject="Hi",
            unread=True,
            flagged=False,
        )
    )
    events = _run_delta(
        {"value": [{"id": LONG_GRAPH_ID, "isRead": True, "subject": "Hi", "flag": {"flagStatus": "notFlagged"}}]},
        store=store,
    )
    types = [e.type for e in events]
    assert "message.created" not in types
    assert "message.read" in types or "message.updated" in types


def test_delta_new_item_without_change_type_is_created():
    events = _run_delta({"value": [{"id": LONG_GRAPH_ID, "subject": "Welcome", "isRead": False}]})
    assert [e.type for e in events] == ["message.created"]


def test_delta_message_id_is_stable_hash_not_truncated_prefix(tmp_path):
    store = Store(tmp_path)
    events = _run_delta(
        {"value": [{"id": LONG_GRAPH_ID, "subject": "Welcome", "isRead": False, "bodyPreview": "hello"}]},
        store=store,
    )
    expected = make_message_id("work", "inbox", "graph", LONG_GRAPH_ID)
    assert events[0].message is not None
    assert events[0].message["id"] == expected
    assert events[0].message["id"].startswith("msg_")
    assert events[0].message["id"] != f"msg_graph_{LONG_GRAPH_ID[:20]}"
    assert len(LONG_GRAPH_ID) > 20
    cached = store.get_message(expected)
    assert cached is not None
    assert cached["native_id"] == LONG_GRAPH_ID
    assert cached["subject"] == "Welcome"


def test_delta_fetch_upserts_imap_stable_id(tmp_path):
    store = Store(tmp_path)
    imap_id = make_message_id("work", "inbox", 99, 17)
    fetched = Message(
        id=imap_id,
        account_id="work",
        provider_id="graph",
        mailbox="inbox",
        uid=17,
        uidvalidity=99,
        native_id="17",
        subject="Fetched",
        unread=False,
        flagged=True,
    )
    store.upsert_message(
        Message(
            id=imap_id,
            account_id="work",
            provider_id="graph",
            mailbox="inbox",
            uid=17,
            uidvalidity=99,
            native_id=LONG_GRAPH_ID,
            subject="Old",
            unread=True,
            flagged=False,
        )
    )

    class FetchingGraph(GraphProvider):
        def __init__(self):
            super().__init__(_account(), {"access_token": "tok"}, store=store)
            self.fetched: list[tuple[str, str]] = []

        def delta(self, folder="inbox", delta_link=None):
            return {
                "value": [
                    {
                        "id": LONG_GRAPH_ID,
                        "changeType": "updated",
                        "isRead": True,
                        "flag": {"flagStatus": "flagged"},
                        "subject": "Fetched",
                    }
                ]
            }

        def get_message(self, mailbox, native_id, *, peek=True):
            self.fetched.append((mailbox, str(native_id)))
            store.upsert_message(fetched)
            return fetched

        def subscribe(self, *a, **k):
            return None

    provider = FetchingGraph()
    events = _run_delta({}, store=store, provider=provider)
    assert provider.fetched == [("inbox", "17")]
    assert [e.type for e in events] == ["message.updated"]
    assert events[0].message["id"] == imap_id
    assert store.get_message(imap_id)["subject"] == "Fetched"


def test_emit_delta_item_maps_mixed_change_types():
    from mailkit.watchers.graph_push import _emit_delta_item

    events = []
    acc = _account()
    provider = SimpleNamespace(id="graph", store=None, get_message=None)
    _emit_delta_item(acc, provider, "inbox", {"id": "n1", "changeType": "created", "subject": "A"}, events.append)
    _emit_delta_item(acc, provider, "inbox", {"id": "n2", "changeType": "updated", "isRead": True}, events.append)
    _emit_delta_item(acc, provider, "inbox", {"id": "n3", "@removed": {"reason": "deleted"}}, events.append)
    assert [e.type for e in events] == ["message.created", "message.updated", "message.deleted"]
