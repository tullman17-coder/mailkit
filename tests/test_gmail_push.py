"""Gmail history: created payloads, expired historyId reset, and flag labels."""

import threading
from urllib.error import HTTPError

from mailkit.config import AccountConfig, AppConfig
from mailkit.db import Store
from mailkit.errors import NetworkError
from mailkit.events import EventBus
from mailkit.models import Event, Message
from mailkit.providers.gmail import GmailProvider
from mailkit.supervisor import AccountWorker
from mailkit.watchers.gmail_push import GmailPushWatcher


GMAIL_HEX_ID = "18c5e2a0b1f"
IMAP_UID = "42"


def _account():
    return AccountConfig(id="work", address="user@gmail.com", provider="gmail")


def _created_message(**overrides):
    data = dict(
        id="msg_imap_42",
        account_id="work",
        provider_id="gmail",
        mailbox="INBOX",
        uid=int(IMAP_UID),
        native_id=IMAP_UID,
        subject="Invoice 1042",
        thread_id="thr_1",
        snippet="please pay",
    )
    data.update(overrides)
    return Message(**data)


class FakeGmailProvider:
    id = "gmail"

    def __init__(self, message=None, uid=IMAP_UID):
        self.message = message or _created_message()
        self.uid = uid
        self.searched = []
        self.fetched = []
        self.api_gets = []
        self.flagged = []

    def uid_for_gmail_id(self, mailbox, gmail_id):
        self.searched.append((mailbox, gmail_id))
        if gmail_id == GMAIL_HEX_ID:
            return self.uid
        return None

    def get_message(self, mailbox, native_id, *, peek=True):
        self.fetched.append((mailbox, str(native_id), peek))
        if str(native_id) != str(self.uid):
            raise LookupError(f"gmail hex id is not an IMAP UID: {native_id}")
        return self.message

    def get_gmail_api_message(self, mailbox, gmail_id):
        self.api_gets.append((mailbox, gmail_id))
        return self.message

    def set_flags(self, mailbox, native_id, add=None, remove=None):
        self.flagged.append((mailbox, str(native_id), add or [], remove or []))

    def close(self):
        return None


def test_history_created_attaches_message_summary():
    events = []
    watcher = GmailPushWatcher()
    provider = FakeGmailProvider()
    watcher._emit_history_item(
        _account(),
        provider,
        "INBOX",
        {"id": "99", "messagesAdded": [{"message": {"id": GMAIL_HEX_ID}}]},
        events.append,
    )
    assert len(events) == 1
    event = events[0]
    assert event.type == "message.created"
    assert event.message is not None
    assert event.message["id"] == "msg_imap_42"
    assert event.message["native_id"] == IMAP_UID
    assert event.message["subject"] == "Invoice 1042"
    assert event.thread_id == "thr_1"
    assert event.data["gmail_id"] == GMAIL_HEX_ID
    assert event.data["history_id"] == "99"
    assert provider.searched == [("INBOX", GMAIL_HEX_ID)]
    assert provider.fetched == [("INBOX", IMAP_UID, True)]
    assert GMAIL_HEX_ID not in {event.message["id"], event.message["native_id"]}


def test_history_created_does_not_treat_gmail_id_as_imap_uid():
    events = []
    provider = FakeGmailProvider()
    GmailPushWatcher()._emit_history_item(
        _account(),
        provider,
        "INBOX",
        {"id": "1", "messagesAdded": [{"message": {"id": GMAIL_HEX_ID}}]},
        events.append,
    )
    assert provider.fetched
    assert provider.fetched[0][1] == IMAP_UID
    assert provider.fetched[0][1] != GMAIL_HEX_ID


def test_history_created_falls_back_to_gmail_api_get():
    events = []

    class ApiOnly(FakeGmailProvider):
        def uid_for_gmail_id(self, mailbox, gmail_id):
            self.searched.append((mailbox, gmail_id))
            return None

        def get_message(self, mailbox, native_id, *, peek=True):
            raise AssertionError("IMAP get_message must not run without a UID")

    provider = ApiOnly()
    GmailPushWatcher()._emit_history_item(
        _account(),
        provider,
        "INBOX",
        {"id": "7", "messagesAdded": [{"message": {"id": GMAIL_HEX_ID}}]},
        events.append,
    )
    assert provider.api_gets == [("INBOX", GMAIL_HEX_ID)]
    assert events[0].message["native_id"] == IMAP_UID
    assert events[0].message["id"] == "msg_imap_42"


def test_history_created_skips_when_message_cannot_be_loaded():
    events = []

    class Missing(FakeGmailProvider):
        def uid_for_gmail_id(self, mailbox, gmail_id):
            return None

        def get_gmail_api_message(self, mailbox, gmail_id):
            return None

        def get_message(self, mailbox, native_id, *, peek=True):
            raise AssertionError("should not fetch")

    GmailPushWatcher()._emit_history_item(
        _account(),
        Missing(),
        "INBOX",
        {"id": "3", "messagesAdded": [{"message": {"id": GMAIL_HEX_ID}}]},
        events.append,
    )
    assert events == []


def test_uid_for_gmail_id_searches_x_gm_msgid():
    provider = GmailProvider(_account(), {"access_token": "tok"})
    seen = []

    def fake_search(mailbox, criteria):
        seen.append((mailbox, list(criteria)))
        return [42]

    provider._search_uids = fake_search  # type: ignore[method-assign]
    uid = provider.uid_for_gmail_id("INBOX", GMAIL_HEX_ID)
    assert uid == "42"
    assert seen == [("INBOX", ["X-GM-MSGID", str(int(GMAIL_HEX_ID, 16))])]


class _FakePlugins:
    def __init__(self, watcher=None):
        self._watcher = watcher

    def watcher_for(self, name):
        return self._watcher

    def hook_list(self):
        return []


class _FakeRuntime:
    def __init__(self, root, provider, watcher=None):
        self.store = Store(root)
        self.plugins = _FakePlugins(watcher)
        self.config = AppConfig()
        acc = _account()
        self.config.accounts[acc.id] = acc
        self._acc = acc
        self._provider = provider

    def provider_for(self, account_id):
        return self._provider, self._acc, {}


def test_supervisor_hydrates_gmail_created_and_applies_hooks(tmp_path):
    provider = FakeGmailProvider()
    runtime = _FakeRuntime(tmp_path, provider)
    runtime.store.save_rule(
        "r1",
        "flag invoices",
        0,
        True,
        False,
        {"subject_contains": ["Invoice"]},
        {"flag": True},
    )
    worker = AccountWorker(runtime, runtime._acc, EventBus(runtime.store))
    event = Event(
        id="evt_1",
        account_id="work",
        provider_id="gmail",
        mailbox="INBOX",
        type="message.created",
        data={"gmail_id": GMAIL_HEX_ID, "history_id": "99"},
    )
    worker._prepare_created_event(provider, event)
    assert event.message is not None
    assert event.message["id"] == "msg_imap_42"
    assert event.message["native_id"] == IMAP_UID
    worker._apply_hooks(provider, event)
    assert provider.flagged == [("INBOX", IMAP_UID, ["Flagged"], [])]
    assert GMAIL_HEX_ID not in {call[1] for call in provider.flagged}


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


def _watch_account():
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
    _run_watch(_watch_account(), provider, events)

    assert provider.history_calls >= 1
    assert "stale" in provider.list_calls
    assert "999" in provider.list_calls
    assert store.get_sync_cursor("g1", "INBOX", "gmail_history") == "999"


def test_watch_resets_expired_history_cursor_on_410(tmp_path):
    store = Store(tmp_path)
    store.set_sync_cursor("g1", "INBOX", "gmail_history", "stale")
    provider = FakeGmail(store, fail_code=410)
    events: list = []
    _run_watch(_watch_account(), provider, events)

    assert provider.history_calls >= 1
    assert "999" in provider.list_calls
    assert store.get_sync_cursor("g1", "INBOX", "gmail_history") == "999"


def test_watch_does_not_reset_cursor_on_other_errors(tmp_path):
    store = Store(tmp_path)
    store.set_sync_cursor("g1", "INBOX", "gmail_history", "stale")
    provider = FakeGmail(store, fail_code=500)
    events: list = []
    _run_watch(_watch_account(), provider, events)

    assert provider.history_calls == 0
    assert provider.list_calls == ["stale"]
    assert store.get_sync_cursor("g1", "INBOX", "gmail_history") == "stale"


def test_reset_idle_backfill_emits_created_without_payload(tmp_path):
    store = Store(tmp_path)
    store.set_sync_cursor("g1", "INBOX", "gmail_history", "stale")
    provider = FakeGmail(store, fail_code=404, uids=[10, 11])
    events: list = []
    _run_watch(_watch_account(), provider, events)

    created = [e for e in events if e.type == "message.created"]
    assert [e.data.get("gmail_id") for e in created] == [None, None]
    assert all(e.data.get("history_id") == "999" for e in created)
    assert all(e.message is None for e in created)
    assert store.get_sync_cursor("g1", "INBOX", "gmail_history") == "999"
