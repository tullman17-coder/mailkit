import threading
from pathlib import Path
from types import SimpleNamespace

from mailkit.api.routes import _mutate_message
from mailkit.config import AccountConfig, AppConfig
from mailkit.db import Store
from mailkit.events import EventBus
from mailkit.ids import idempotency_key, new_id
from mailkit.models import Event, EventFilter, Message


def test_idempotent_publish_and_cursor(tmp_path: Path):
    store = Store(tmp_path)
    bus = EventBus(store)
    key = idempotency_key("work", "INBOX", "message.created", "17")
    first = bus.publish(
        Event(
            id=new_id("evt"),
            account_id="work",
            provider_id="imap",
            mailbox="INBOX",
            type="message.created",
            idempotency_key=key,
            message={"schema": "mailkit.message.v1", "id": "msg_1", "subject": "Hi", "account_id": "work", "provider_id": "imap", "mailbox": "INBOX"},
        )
    )
    second = bus.publish(
        Event(
            id=new_id("evt"),
            account_id="work",
            provider_id="imap",
            mailbox="INBOX",
            type="message.created",
            idempotency_key=key,
        )
    )
    assert first is not None
    assert second is None
    replayed = bus.replay(None, None, limit=10)
    assert len(replayed) == 1
    assert replayed[0]["id"] == first.id
    later = bus.publish(
        Event(id=new_id("evt"), account_id="work", provider_id="imap", mailbox="INBOX", type="message.flagged")
    )
    after = bus.replay(None, first.id, limit=10)
    assert [e["id"] for e in after] == [later.id]


def test_live_filter_queue(tmp_path: Path):
    store = Store(tmp_path)
    bus = EventBus(store)
    q = bus.subscribe_live(EventFilter(subject=["Invoice"]))
    bus.publish(
        Event(
            id=new_id("evt"),
            account_id="work",
            provider_id="imap",
            mailbox="INBOX",
            type="message.created",
            message={"subject": "Hello", "from": [], "account_id": "work", "provider_id": "imap", "mailbox": "INBOX", "id": "a", "schema": "mailkit.message.v1"},
        )
    )
    bus.publish(
        Event(
            id=new_id("evt"),
            account_id="work",
            provider_id="imap",
            mailbox="INBOX",
            type="message.created",
            message={"subject": "Invoice INV-1042", "from": [], "account_id": "work", "provider_id": "imap", "mailbox": "INBOX", "id": "b", "schema": "mailkit.message.v1"},
        )
    )
    item = q.get_nowait()
    assert "Invoice" in item["message"]["subject"]
    assert q.empty()


def test_durable_subscription_ack(tmp_path: Path):
    store = Store(tmp_path)
    bus = EventBus(store)
    ev = bus.publish(Event(id=new_id("evt"), account_id="work", provider_id="imap", type="message.created"))
    store.save_subscription("sub1", "invoices", EventFilter(account=["work"]), durable=True, ack_required=True, cursor=None)
    store.ack("sub1", ev.id, "pending")
    store.ack("sub1", ev.id, "acked")
    row = store.conn.execute("SELECT status FROM acks WHERE subscription_id='sub1'").fetchone()
    assert row["status"] == "acked"


def test_stream_delivers_event_published_during_replay(tmp_path: Path):
    """Events published between replay and live subscribe must still be delivered."""
    store = Store(tmp_path)
    bus = EventBus(store)
    historical = bus.publish(
        Event(id=new_id("evt"), account_id="work", provider_id="imap", mailbox="INBOX", type="message.created")
    )
    assert historical is not None

    original_replay = bus.replay
    gap_ids: list[str] = []

    def replay_with_gap(filt, cursor, limit=100):
        events = original_replay(filt, cursor, limit=limit)
        gap = bus.publish(
            Event(id=new_id("evt"), account_id="work", provider_id="imap", mailbox="INBOX", type="message.flagged")
        )
        assert gap is not None
        gap_ids.append(gap.id)
        return events

    bus.replay = replay_with_gap  # type: ignore[method-assign]

    received: list[dict] = []
    stop = threading.Event()

    def consume():
        for ev in bus.stream(None, None, stop):
            received.append(ev)
            if gap_ids and any(e.get("id") == gap_ids[0] for e in received):
                stop.set()
                break

    thread = threading.Thread(target=consume)
    thread.start()
    thread.join(timeout=3)
    stop.set()
    thread.join(timeout=2)

    ids = [e["id"] for e in received]
    assert historical.id in ids
    assert gap_ids, "replay window did not publish a gap event"
    assert gap_ids[0] in ids, f"event published during replay was dropped; got {ids}"


class _FlagProvider:
    def set_flags(self, mailbox, native_id, add=None, remove=None):
        return None


class _FlagRuntime:
    def __init__(self, store: Store):
        self.store = store
        self.config = AppConfig()
        self._acc = AccountConfig(id="work", address="a@b.com", provider="imap")
        self.config.accounts["work"] = self._acc
        self._provider = _FlagProvider()

    def provider_for(self, account_id):
        return self._provider, self._acc, {}


def test_flag_and_read_mutations_publish_events(tmp_path: Path):
    store = Store(tmp_path)
    bus = EventBus(store)
    store.upsert_message(
        Message(
            id="msg_1",
            account_id="work",
            provider_id="imap",
            mailbox="INBOX",
            uid=17,
            native_id="17",
            subject="Hello",
            unread=True,
            flagged=False,
            flags=[],
        )
    )
    created_key = idempotency_key("work", "INBOX", "message.created", "17")
    bus.publish(
        Event(
            id=new_id("evt"),
            account_id="work",
            provider_id="imap",
            mailbox="INBOX",
            type="message.created",
            idempotency_key=created_key,
            message={"id": "msg_1", "subject": "Hello"},
        )
    )
    app = SimpleNamespace(runtime=_FlagRuntime(store), bus=bus)

    flagged = _mutate_message(app, "msg_1", "flag", {}, {})
    assert flagged["ok"] is True
    read = _mutate_message(app, "msg_1", "read", {}, {})
    assert read["ok"] is True

    events = [e for e in bus.replay(None, None, limit=20) if e["type"] != "message.created"]
    types = [e["type"] for e in events]
    assert "message.flagged" in types
    assert "message.read" in types
    flagged_ev = next(e for e in events if e["type"] == "message.flagged")
    read_ev = next(e for e in events if e["type"] == "message.read")
    assert flagged_ev["message"]["id"] == "msg_1"
    assert flagged_ev["message"]["flagged"] is True
    assert "Flagged" in (flagged_ev["message"].get("flags") or [])
    assert read_ev["message"]["id"] == "msg_1"
    assert read_ev["message"]["unread"] is False
    assert flagged_ev["idempotency_key"] != created_key
    assert read_ev["idempotency_key"] != created_key
    assert flagged_ev["idempotency_key"] != read_ev["idempotency_key"]
