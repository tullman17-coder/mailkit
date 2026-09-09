import threading

from mailkit.config import AccountConfig
from mailkit.db import Store
from mailkit.models import Message
from mailkit.watchers.idle import IdleWatcher
from mailkit.watchers.poll import PollWatcher, UID_CURSOR_KIND, load_uid_cursor, save_uid_cursor


class FakeIdleClient:
    def select(self, mailbox):
        return ("OK", [b"1"])

    def logout(self):
        return ("BYE", [])

    def noop(self):
        return ("OK", [])

    def shutdown(self):
        pass


class FakeImapProvider:
    id = "imap"

    def __init__(self, uids, store=None):
        self.uids = list(uids)
        self.store = store

    def capabilities(self):
        return {"idle"}

    def connect(self):
        return None

    def open_idle_client(self):
        return FakeIdleClient()

    def recent_uids(self, mailbox, since_uid):
        if since_uid:
            return [uid for uid in self.uids if uid > since_uid]
        return list(self.uids)

    def get_message(self, mailbox, native_id, peek=True):
        uid = int(native_id)
        return Message(
            id=f"msg_{uid}",
            account_id="work",
            provider_id="imap",
            mailbox=mailbox,
            uid=uid,
            native_id=str(uid),
            subject=f"mail {uid}",
            thread_id=f"thr_{uid}",
        )


def _created_uids(events):
    return [int(ev.message["uid"]) for ev in events if ev.type == "message.created" and ev.message]


def _run_idle_until_connected(monkeypatch, account, provider, on_idle=None):
    events = []
    stop = threading.Event()
    calls = {"n": 0}

    def fake_wait(client, duration, stop_ev):
        calls["n"] += 1
        if on_idle is not None:
            on_idle(calls["n"], stop_ev)
        else:
            stop_ev.set()
        return False

    monkeypatch.setattr("mailkit.watchers.idle._wait_idle", fake_wait)
    IdleWatcher().watch(account, provider, events.append, stop)
    return events, calls


def test_shared_uid_cursor_roundtrip(tmp_path):
    store = Store(tmp_path)
    provider = FakeImapProvider([1], store)
    assert UID_CURSOR_KIND == "uid"
    assert load_uid_cursor(provider, "work", "INBOX") == 0
    save_uid_cursor(provider, "work", "INBOX", 42)
    assert load_uid_cursor(provider, "work", "INBOX") == 42
    assert store.get_sync_cursor("work", "INBOX", "uid") == "42"


def test_idle_first_connect_records_high_water_without_emitting_history(tmp_path, monkeypatch):
    store = Store(tmp_path)
    account = AccountConfig(id="work", address="a@b.com")
    provider = FakeImapProvider([1, 2, 3, 4], store)
    events, _ = _run_idle_until_connected(monkeypatch, account, provider)
    assert _created_uids(events) == []
    assert any(ev.type == "account.connected" for ev in events)
    assert store.get_sync_cursor("work", "INBOX", "uid") == "4"


def test_idle_reconnect_emits_uids_after_stored_cursor(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.set_sync_cursor("work", "INBOX", "uid", "10")
    account = AccountConfig(id="work", address="a@b.com")
    provider = FakeImapProvider([8, 9, 10, 11, 12], store)
    events, _ = _run_idle_until_connected(monkeypatch, account, provider)
    assert _created_uids(events) == [11, 12]
    assert store.get_sync_cursor("work", "INBOX", "uid") == "12"


def test_idle_downtime_backfill_across_sessions(tmp_path, monkeypatch):
    store = Store(tmp_path)
    account = AccountConfig(id="work", address="a@b.com")
    provider = FakeImapProvider([1, 2, 3], store)
    _run_idle_until_connected(monkeypatch, account, provider)
    assert store.get_sync_cursor("work", "INBOX", "uid") == "3"

    provider.uids.extend([4, 5])
    events, _ = _run_idle_until_connected(monkeypatch, account, provider)
    assert _created_uids(events) == [4, 5]
    assert store.get_sync_cursor("work", "INBOX", "uid") == "5"


def test_poll_emits_uids_after_stored_cursor(tmp_path):
    store = Store(tmp_path)
    store.set_sync_cursor("work", "INBOX", "uid", "10")
    account = AccountConfig(id="work", address="a@b.com")
    provider = FakeImapProvider([8, 9, 10, 11, 12], store)
    events = []
    stop = threading.Event()

    def emit(event):
        events.append(event)
        if event.type == "message.created" and event.message and int(event.message["uid"]) == 12:
            stop.set()

    thread = threading.Thread(target=PollWatcher().watch, args=(account, provider, emit, stop), daemon=True)
    thread.start()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert _created_uids(events) == [11, 12]
    assert store.get_sync_cursor("work", "INBOX", UID_CURSOR_KIND) == "12"


def test_idle_loop_persists_new_uids(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.set_sync_cursor("work", "INBOX", "uid", "20")
    account = AccountConfig(id="work", address="a@b.com")
    provider = FakeImapProvider([20], store)

    def on_idle(n, stop_ev):
        if n == 1:
            provider.uids.extend([21, 22])
            return
        stop_ev.set()

    events, calls = _run_idle_until_connected(monkeypatch, account, provider, on_idle=on_idle)
    assert calls["n"] >= 2
    assert _created_uids(events) == [21, 22]
    assert store.get_sync_cursor("work", "INBOX", "uid") == "22"
