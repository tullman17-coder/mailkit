import threading
from pathlib import Path

from mailkit.db import Store
from mailkit.events import EventBus
from mailkit.ids import idempotency_key, new_id
from mailkit.models import Event, EventFilter


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
