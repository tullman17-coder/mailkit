import threading
from urllib.error import HTTPError

from mailkit.config import AccountConfig
from mailkit.db import Store
from mailkit.errors import NetworkError
from mailkit.watchers.gmail_push import GmailPushWatcher


class FakeGmail:
    id = "gmail"

    def __init__(self, store, *, fail_code=None, uids=None):
        self.store = store
        self.secrets = {"access_token": "tok"}
        self._fail_code = fail_code
        self.uids = uids
        self.list_calls: list[str] = []
        self.history_calls = 0

    def history_id(self):
        self.history_calls += 1
        return "999"

    def list_history(self, start):
        self.list_calls.append(start)
        if self._fail_code and start == "stale":
            code = self._fail_code
            self._fail_code = None
            raise NetworkError(
                f"HTTP {code} for https://gmail.googleapis.com/gmail/v1/users/me/history: historyId too old"
            )
        return {"history": [], "historyId": start}

    def recent_uids(self, mailbox, since_uid):
        if self.uids is None:
            raise RuntimeError("imap unavailable")
        return list(self.uids)


def _account():
    return AccountConfig(id="g1", address="a@gmail.com", watch="gmail_push")


def _run_watch(account, provider, events, stop_after_waits=1):
    stop = threading.Event()
    waits = {"n": 0}

    def wait(timeout=None):
        waits["n"] += 1
        if waits["n"] >= stop_after_waits:
            stop.set()
        return False

    stop.wait = wait  # type: ignore[method-assign]
    GmailPushWatcher().watch(account, provider, events.append, stop)
    return waits["n"]


def test_history_cursor_expired_detects_404_and_410():
    from mailkit.watchers.gmail_push import history_cursor_expired

    assert history_cursor_expired(NetworkError("HTTP 404 for https://example/history: gone"))
    assert history_cursor_expired(NetworkError("HTTP 410 for https://example/history: gone"))
    assert not history_cursor_expired(NetworkError("HTTP 500 for https://example/history: boom"))
    assert not history_cursor_expired(RuntimeError("timeout"))
    wrapped = NetworkError("request failed")
    wrapped.__cause__ = HTTPError("https://gmail.googleapis.com/gmail/v1/users/me/history", 404, "Not Found", None, None)
    assert history_cursor_expired(wrapped)


def test_watch_resets_expired_history_cursor_on_404(tmp_path):
    store = Store(tmp_path)
    store.set_sync_cursor("g1", "INBOX", "gmail_history", "stale")
    provider = FakeGmail(store, fail_code=404)
    events: list = []
    _run_watch(_account(), provider, events)

    assert provider.history_calls >= 1
    assert "stale" in provider.list_calls
    assert "999" in provider.list_calls
    assert store.get_sync_cursor("g1", "INBOX", "gmail_history") == "999"


def test_watch_resets_expired_history_cursor_on_410(tmp_path):
    store = Store(tmp_path)
    store.set_sync_cursor("g1", "INBOX", "gmail_history", "stale")
    provider = FakeGmail(store, fail_code=410)
    events: list = []
    _run_watch(_account(), provider, events)

    assert provider.history_calls >= 1
    assert "999" in provider.list_calls
    assert store.get_sync_cursor("g1", "INBOX", "gmail_history") == "999"


def test_watch_does_not_reset_cursor_on_other_errors(tmp_path):
    store = Store(tmp_path)
    store.set_sync_cursor("g1", "INBOX", "gmail_history", "stale")
    provider = FakeGmail(store, fail_code=500)
    events: list = []
    _run_watch(_account(), provider, events)

    assert provider.history_calls == 0
    assert provider.list_calls == ["stale"]
    assert store.get_sync_cursor("g1", "INBOX", "gmail_history") == "stale"


def test_reset_idle_backfill_emits_created_without_payload(tmp_path):
    store = Store(tmp_path)
    store.set_sync_cursor("g1", "INBOX", "gmail_history", "stale")
    provider = FakeGmail(store, fail_code=404, uids=[10, 11])
    events: list = []
    _run_watch(_account(), provider, events)

    created = [e for e in events if e.type == "message.created"]
    assert [e.data.get("gmail_id") for e in created] == [None, None]
    assert all(e.data.get("history_id") == "999" for e in created)
    assert all(e.message is None for e in created)
    assert store.get_sync_cursor("g1", "INBOX", "gmail_history") == "999"
