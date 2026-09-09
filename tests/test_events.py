from pathlib import Path
from types import SimpleNamespace

from mailkit.api.routes import dispatch
from mailkit.db import Store
from mailkit.events import EventBus
from mailkit.ids import idempotency_key, new_id
from mailkit.models import Event, EventFilter


def _publish(bus: EventBus, **kwargs) -> Event:
    defaults = dict(id=new_id("evt"), account_id="work", provider_id="imap", type="message.created")
    defaults.update(kwargs)
    return bus.publish(Event(**defaults))


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


def test_durable_ack_advances_subscription_cursor(tmp_path: Path):
    store = Store(tmp_path)
    bus = EventBus(store)
    first = _publish(bus)
    later = _publish(bus, type="message.flagged")
    store.save_subscription("sub1", "invoices", EventFilter(account=["work"]), durable=True, ack_required=True, cursor=None)
    store.ack("sub1", first.id, "pending")
    assert store.get_subscription("sub1")["cursor"] is None
    store.ack("sub1", first.id, "acked")
    assert store.get_subscription("sub1")["cursor"] == first.id
    store.ack("sub1", later.id, "acked")
    assert store.get_subscription("sub1")["cursor"] == later.id
    replayed = bus.replay(None, store.get_subscription("sub1")["cursor"], limit=10)
    assert replayed == []


def test_durable_ack_does_not_rewind_cursor(tmp_path: Path):
    store = Store(tmp_path)
    bus = EventBus(store)
    older = _publish(bus)
    newer = _publish(bus, type="message.flagged")
    store.save_subscription("sub1", "invoices", EventFilter(account=["work"]), durable=True, ack_required=True, cursor=None)
    store.ack("sub1", newer.id, "acked")
    assert store.get_subscription("sub1")["cursor"] == newer.id
    store.ack("sub1", older.id, "acked")
    assert store.get_subscription("sub1")["cursor"] == newer.id


def test_http_events_ack_advances_subscription_cursor(tmp_path: Path):
    store = Store(tmp_path)
    bus = EventBus(store)
    ev = _publish(bus)
    store.save_subscription("sub1", "invoices", EventFilter(account=["work"]), durable=True, ack_required=True, cursor=None)
    app = SimpleNamespace(runtime=SimpleNamespace(store=store), bus=bus)
    result = dispatch(
        app,
        "POST",
        "/v1/events/ack",
        {},
        {"subscription_id": "sub1", "event_id": ev.id},
        handler=None,
    )
    assert result["ok"] is True
    assert result["data"]["acked"] == ev.id
    assert store.get_subscription("sub1")["cursor"] == ev.id
