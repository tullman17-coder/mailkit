import io
from pathlib import Path
from types import SimpleNamespace

from mailkit.api.routes import _events
from mailkit.db import Store
from mailkit.events import EventBus
from mailkit.ids import idempotency_key, new_id
from mailkit.models import Event, EventFilter


class _CaptureBus:
    def __init__(self):
        self.cursor = object()

    def stream(self, filt, cursor, stop):
        self.cursor = cursor
        return iter(())


def test_sse_honors_last_event_id_when_query_cursor_absent():
    bus = _CaptureBus()
    handler = SimpleNamespace(headers={"Last-Event-ID": "evt_last"})
    resp = _events(SimpleNamespace(bus=bus), "GET", ["stream"], {}, {}, handler)
    resp.stream(io.BytesIO())
    assert bus.cursor == "evt_last"


def test_sse_query_cursor_wins_over_last_event_id():
    bus = _CaptureBus()
    handler = SimpleNamespace(headers={"Last-Event-ID": "evt_header"})
    qs = {"cursor": ["evt_query"]}
    resp = _events(SimpleNamespace(bus=bus), "GET", ["stream"], qs, {}, handler)
    resp.stream(io.BytesIO())
    assert bus.cursor == "evt_query"


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
