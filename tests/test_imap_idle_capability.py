"""FLAG-P8: advertise IMAP IDLE only when CAPABILITY includes it."""

import time

from mailkit.config import AccountConfig, AppConfig, ImapSettings
from mailkit.db import Store
from mailkit.events import EventBus
from mailkit.plugins.registry import load_plugins
from mailkit.providers.imap_smtp import ImapSmtpProvider
from mailkit.supervisor import AccountWorker, choose_watcher
from mailkit.watchers.idle import IdleWatcher


def _account(**kwargs) -> AccountConfig:
    return AccountConfig(
        id="work",
        address="a@example.com",
        watch="auto",
        imap=ImapSettings(host="imap.example.com"),
        **kwargs,
    )


def _provider(account=None) -> ImapSmtpProvider:
    return ImapSmtpProvider(account or _account(), {"password": "secret"})


class FakeIMAP:
    def __init__(self, *args, capability="IMAP4REV1 AUTH=PLAIN", **kwargs):
        self._cap_line = capability
        self.capabilities = tuple(capability.upper().split())
        self.capability_calls = 0

    def capability(self):
        self.capability_calls += 1
        return "OK", [self._cap_line.encode()]

    def login(self, user, password):
        return "OK", [None]

    def enable(self, *args, **kwargs):
        raise RuntimeError("ENABLE not supported")

    def noop(self):
        return "OK", [None]

    def logout(self):
        return "OK", [None]

    def starttls(self, *args, **kwargs):
        return "OK", [None]


def _install_imap(monkeypatch, capability: str) -> list:
    created: list[FakeIMAP] = []

    class BoundIMAP(FakeIMAP):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, capability=capability, **kwargs)
            created.append(self)

    monkeypatch.setattr("mailkit.providers.imap_smtp.imaplib.IMAP4_SSL", BoundIMAP)
    return created


def test_unconnected_provider_does_not_advertise_idle():
    provider = _provider()
    assert "idle" not in provider.capabilities()
    assert IdleWatcher().supports(provider.account, provider) is False


def test_connect_omits_idle_when_capability_lacks_it(monkeypatch):
    created = _install_imap(monkeypatch, "IMAP4rev1 AUTH=PLAIN")
    provider = _provider()
    provider.connect()
    assert created and created[0].capability_calls >= 1
    assert "idle" not in provider.capabilities()
    assert IdleWatcher().supports(provider.account, provider) is False


def test_connect_includes_idle_when_capability_has_it(monkeypatch):
    created = _install_imap(monkeypatch, "IMAP4rev1 IDLE AUTH=PLAIN")
    provider = _provider()
    provider.connect()
    assert created and created[0].capability_calls >= 1
    assert "idle" in provider.capabilities()
    assert IdleWatcher().supports(provider.account, provider) is True


def test_idle_token_is_matched_case_insensitively(monkeypatch):
    _install_imap(monkeypatch, "IMAP4rev1 Idle NAMESPACE")
    provider = _provider()
    provider.connect()
    assert "idle" in provider.capabilities()


def test_choose_watcher_falls_back_to_poll_without_idle():
    provider = _provider()
    watcher = choose_watcher(provider.account, provider, load_plugins())
    assert watcher.id == "poll"


def test_choose_watcher_prefers_idle_after_idle_capability(monkeypatch):
    _install_imap(monkeypatch, "IMAP4rev1 IDLE")
    provider = _provider()
    provider.connect()
    watcher = choose_watcher(provider.account, provider, load_plugins())
    assert watcher.id == "idle"


def test_choose_watcher_polls_after_connect_without_idle(monkeypatch):
    _install_imap(monkeypatch, "IMAP4rev1 AUTH=PLAIN")
    provider = _provider()
    provider.connect()
    watcher = choose_watcher(provider.account, provider, load_plugins())
    assert watcher.id == "poll"


def test_requested_idle_falls_back_to_poll_when_missing(monkeypatch):
    _install_imap(monkeypatch, "IMAP4rev1")
    provider = _provider()
    provider.account.watch = "idle"
    provider.connect()
    watcher = choose_watcher(provider.account, provider, load_plugins())
    assert watcher.id == "poll"


class _ProbeProvider:
    id = "imap"

    def __init__(self):
        self.connected = False
        self.order: list[str] = []

    def connect(self):
        self.order.append("connect")
        self.connected = True

    def capabilities(self):
        self.order.append("capabilities")
        return {"idle"} if self.connected else {"list", "read"}

    def close(self):
        return None


class _BlockWatcher:
    def __init__(self, watcher_id, *, always=False):
        self.id = watcher_id
        self.always = always

    def supports(self, account, provider):
        if self.always:
            return True
        return "idle" in getattr(provider, "capabilities", lambda: set())()

    def watch(self, account, provider, emit, stop):
        stop.wait(5)


class _RecordingPlugins:
    def __init__(self):
        self.idle = _BlockWatcher("idle")
        self.poll = _BlockWatcher("poll", always=True)

    def watcher_for(self, name):
        if name == "idle":
            return self.idle
        if name == "poll":
            return self.poll
        return None


class _ProbeRuntime:
    def __init__(self, root, provider):
        self.root = root
        self.store = Store(root)
        self.plugins = _RecordingPlugins()
        self.config = AppConfig()
        acc = AccountConfig(id="work", address="a@b.com", watch="auto")
        self.config.accounts["work"] = acc
        self._acc = acc
        self._provider = provider

    def provider_for(self, account_id):
        return self._provider, self._acc, {}


def test_worker_connects_before_choosing_watcher(tmp_path):
    provider = _ProbeProvider()
    runtime = _ProbeRuntime(tmp_path, provider)
    bus = EventBus(runtime.store)
    worker = AccountWorker(runtime, runtime._acc, bus)
    worker.start()
    deadline = time.time() + 3
    while time.time() < deadline and "connect" not in provider.order:
        time.sleep(0.05)
    worker.join(timeout=2)
    assert provider.order
    assert provider.order[0] == "connect"
    assert "capabilities" in provider.order
    assert provider.order.index("connect") < provider.order.index("capabilities")
    assert worker.watcher_id == "idle"
