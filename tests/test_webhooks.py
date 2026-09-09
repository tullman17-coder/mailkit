import threading
import time
import urllib.error
from pathlib import Path

from mailkit.db import Store
from mailkit.models import EventFilter
from mailkit.vault import Vault
from mailkit.webhooks import WebhookDispatcher


class _OkResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FixedBackoff:
    """Deterministic backoff so tests can assert blocking vs. isolation."""

    def __init__(self, initial=1, maximum=60, **_kwargs):
        self._delay = 5.0 if initial >= 1 else 0.05

    def fail(self):
        return self._delay


def _dispatcher(tmp_path: Path, **kwargs) -> WebhookDispatcher:
    store = Store(tmp_path)
    vault = Vault(tmp_path)
    return WebhookDispatcher(store, vault, **kwargs), store, vault


def _event(eid: str = "evt_1") -> dict:
    return {
        "schema": "mailkit.event.v1",
        "id": eid,
        "type": "message.created",
        "account_id": "work",
        "provider_id": "imap",
        "mailbox": "INBOX",
        "message": {"subject": "Invoice", "id": "msg_1"},
    }


def test_failed_hook_does_not_block_other_hooks(tmp_path: Path, monkeypatch):
    """A down endpoint must not stall the dispatcher for other webhooks."""
    dispatcher, store, _vault = _dispatcher(tmp_path)
    filt = EventFilter()
    store.save_webhook("slow", "slow", "http://127.0.0.1:9/slow", filt)
    store.save_webhook("fast", "fast", "http://127.0.0.1:9/fast", filt)
    assert [h["id"] for h in store.list_webhooks()][:2] == ["slow", "fast"]

    delivered: list[str] = []
    lock = threading.Lock()

    def fake_urlopen(req, timeout=15):
        url = req.full_url
        if url.endswith("/slow"):
            raise urllib.error.URLError("down")
        with lock:
            delivered.append(url)
        return _OkResp()

    monkeypatch.setattr("mailkit.webhooks.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("mailkit.webhooks.Backoff", _FixedBackoff)

    dispatcher.enqueue(_event())
    started = time.monotonic()
    dispatcher.start()
    deadline = started + 1.0
    while time.monotonic() < deadline:
        with lock:
            if any(u.endswith("/fast") for u in delivered):
                break
        time.sleep(0.01)
    dispatcher.stop()

    elapsed = time.monotonic() - started
    with lock:
        assert any(u.endswith("/fast") for u in delivered), f"fast hook not delivered; got {delivered}"
    assert elapsed < 1.0, f"fast hook blocked for {elapsed:.2f}s behind a failing hook"


def test_webhook_retries_survive_dispatcher_restart(tmp_path: Path, monkeypatch):
    """Pending deliveries must be persisted and replayed after a daemon restart."""
    calls: list[int] = []
    lock = threading.Lock()
    succeed = threading.Event()

    def fake_urlopen(req, timeout=15):
        with lock:
            calls.append(1)
        if not succeed.is_set():
            raise urllib.error.HTTPError(req.full_url, 500, "fail", hdrs=None, fp=None)
        return _OkResp()

    monkeypatch.setattr("mailkit.webhooks.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(
        "mailkit.webhooks.Backoff",
        lambda *a, **k: type("B", (), {"fail": staticmethod(lambda: 0.05)})(),
    )

    first, store, vault = _dispatcher(tmp_path)
    store.save_webhook("hook", "hook", "http://127.0.0.1:9/hook", EventFilter())
    first.start()
    first.enqueue(_event("evt_restart"))
    deadline = time.time() + 2
    while time.time() < deadline:
        with lock:
            if calls:
                break
        time.sleep(0.01)
    with lock:
        assert calls, "first dispatcher never attempted delivery"
    first.stop()
    attempts_before = len(calls)

    succeed.set()
    restarted = WebhookDispatcher(store, vault)
    restarted.start()
    deadline = time.time() + 2
    while time.time() < deadline:
        with lock:
            if len(calls) > attempts_before:
                break
        time.sleep(0.01)
    restarted.stop()

    with lock:
        assert len(calls) > attempts_before, "restart did not replay the persisted pending delivery"
    rows = store.pending_webhook_deliveries()
    delivered = [r for r in store.list_webhook_deliveries() if r["status"] == "delivered"]
    assert not rows
    assert delivered and delivered[0]["event_id"] == "evt_restart"


def test_webhook_gives_up_after_max_attempts_and_persists_failure(tmp_path: Path, monkeypatch):
    def fake_urlopen(req, timeout=15):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("mailkit.webhooks.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(
        "mailkit.webhooks.Backoff",
        lambda *a, **k: type("B", (), {"fail": staticmethod(lambda: 0.01)})(),
    )

    dispatcher, store, _vault = _dispatcher(tmp_path, max_attempts=3)
    store.save_webhook("hook", "hook", "http://127.0.0.1:9/hook", EventFilter())
    dispatcher.start()
    dispatcher.enqueue(_event("evt_giveup"))
    deadline = time.time() + 2
    while time.time() < deadline:
        rows = store.list_webhook_deliveries()
        if rows and rows[0]["status"] == "failed" and rows[0]["attempts"] >= 3:
            break
        time.sleep(0.02)
    dispatcher.stop()
    rows = store.list_webhook_deliveries()
    assert rows
    assert rows[0]["status"] == "failed"
    assert rows[0]["attempts"] >= 3
    assert rows[0]["event_id"] == "evt_giveup"
