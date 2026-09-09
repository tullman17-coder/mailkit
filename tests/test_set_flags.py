"""FLAG-F9: set_flags must require a real UID and fail when IMAP STORE returns NO."""

from __future__ import annotations

import pytest

from mailkit.api.routes import _mutate_message
from mailkit.config import AccountConfig, AppConfig, ImapSettings
from mailkit.errors import NetworkError, NotFoundError
from mailkit.providers.imap_smtp import ImapSmtpProvider


class DummyIMAP:
    def __init__(self, store_result=("OK", [b""])):
        self.store_result = store_result
        self.uid_calls = []

    def noop(self):
        return ("OK", [b""])

    def select(self, mailbox):
        return ("OK", [b"1"])

    def examine(self, mailbox):
        return ("OK", [b"1"])

    def status(self, mailbox, items):
        return ("OK", [b"INBOX (UIDVALIDITY 1)"])

    def uid(self, command, *args):
        self.uid_calls.append((command,) + args)
        if command == "STORE":
            return self.store_result
        return ("OK", [b""])


def _provider(dummy: DummyIMAP) -> ImapSmtpProvider:
    account = AccountConfig(id="work", address="a@b.com", imap=ImapSettings(host="imap.example.com"))
    provider = ImapSmtpProvider(account, {})
    provider._imap = dummy
    return provider


def test_set_flags_raises_when_store_returns_no():
    dummy = DummyIMAP(store_result=("NO", [b"STORE failed"]))
    provider = _provider(dummy)
    with pytest.raises(NetworkError, match="STORE"):
        provider.set_flags("INBOX", "17", add=["Flagged"])
    assert dummy.uid_calls
    assert dummy.uid_calls[0][0] == "STORE"
    assert dummy.uid_calls[0][1] == "17"


def test_set_flags_rejects_missing_native_id():
    dummy = DummyIMAP()
    provider = _provider(dummy)
    for native in (None, "", "None"):
        dummy.uid_calls.clear()
        with pytest.raises(NotFoundError):
            provider.set_flags("INBOX", native, add=["Flagged"])
        assert not any(call[0] == "STORE" for call in dummy.uid_calls)


def test_set_flags_succeeds_when_store_returns_ok():
    dummy = DummyIMAP(store_result=("OK", [b""]))
    provider = _provider(dummy)
    provider.set_flags("INBOX", "17", add=["Flagged"], remove=["Seen"])
    stores = [call for call in dummy.uid_calls if call[0] == "STORE"]
    assert len(stores) == 2
    assert stores[0][1] == "17"
    assert stores[1][1] == "17"


class RecordingProvider:
    def __init__(self, *, error=None):
        self.calls = []
        self.error = error

    def set_flags(self, mailbox, native_id, add=None, remove=None):
        self.calls.append((mailbox, native_id, list(add or []), list(remove or [])))
        if self.error:
            raise self.error


class FakeStore:
    def __init__(self, message):
        self.message = message

    def get_message(self, msg_id):
        return self.message


class FakeRuntime:
    def __init__(self, provider, stored):
        self.store = FakeStore(stored)
        self.config = AppConfig()
        acc = AccountConfig(id="work", address="a@b.com")
        self.config.accounts["work"] = acc
        self._provider = provider
        self._acc = acc

    def provider_for(self, account_id):
        return self._provider, self._acc, {}


class FakeApp:
    def __init__(self, runtime):
        self.runtime = runtime


def test_api_flag_requires_real_native_id():
    provider = RecordingProvider()
    stored = {"id": "msg_1", "account_id": "work", "mailbox": "INBOX"}
    app = FakeApp(FakeRuntime(provider, stored))
    with pytest.raises(NotFoundError):
        _mutate_message(app, "msg_1", "flag", {}, {})
    assert provider.calls == []


def test_api_flag_does_not_store_uid_none():
    dummy = DummyIMAP(store_result=("OK", [b""]))
    provider = _provider(dummy)
    stored = {"id": "msg_1", "account_id": "work", "mailbox": "INBOX", "native_id": None, "uid": None}
    app = FakeApp(FakeRuntime(provider, stored))
    with pytest.raises(NotFoundError):
        _mutate_message(app, "msg_1", "flag", {}, {})
    assert not any(call[0] == "STORE" for call in dummy.uid_calls)
    assert not any(call[1] == "None" for call in dummy.uid_calls if call[0] == "STORE")


def test_api_flag_surfaces_store_failure():
    dummy = DummyIMAP(store_result=("NO", [b"STORE failed"]))
    provider = _provider(dummy)
    stored = {
        "id": "msg_1",
        "account_id": "work",
        "mailbox": "INBOX",
        "native_id": "17",
        "uid": 17,
    }
    app = FakeApp(FakeRuntime(provider, stored))
    with pytest.raises(NetworkError, match="STORE"):
        _mutate_message(app, "msg_1", "flag", {}, {})


def test_api_flag_ok_when_store_succeeds():
    dummy = DummyIMAP(store_result=("OK", [b""]))
    provider = _provider(dummy)
    stored = {
        "id": "msg_1",
        "account_id": "work",
        "mailbox": "INBOX",
        "native_id": "17",
        "uid": 17,
    }
    app = FakeApp(FakeRuntime(provider, stored))
    result = _mutate_message(app, "msg_1", "flag", {}, {})
    assert result["ok"] is True
    stores = [call for call in dummy.uid_calls if call[0] == "STORE"]
    assert stores
    assert stores[0][1] == "17"
