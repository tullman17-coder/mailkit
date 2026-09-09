import socket
import threading
import time

from mailkit.config import AccountConfig, AppConfig, ImapSettings
from mailkit.db import Store
from mailkit.events import EventBus
from mailkit.errors import NetworkError
from mailkit.supervisor import AccountWorker, Supervisor


class FakeProvider:
    id = "imap"

    def capabilities(self):
        return {"idle"}

    def close(self):
        return None


class BoomThenBlock:
    id = "idle"

    def __init__(self):
        self.calls = 0

    def supports(self, account, provider):
        return True

    def watch(self, account, provider, emit, stop):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient")
        stop.wait(5)


class FakePlugins:
    def __init__(self, watcher):
        self._watcher = watcher

    def watcher_for(self, name):
        if name in {"idle", "poll"}:
            return self._watcher
        return None


class FakeRuntime:
    def __init__(self, root, watcher):
        self.root = root
        self.store = Store(root)
        self.plugins = FakePlugins(watcher)
        self.config = AppConfig()
        acc = AccountConfig(id="work", address="a@b.com")
        self.config.accounts["work"] = acc
        self._acc = acc

    def provider_for(self, account_id):
        return FakeProvider(), self._acc, {}


def test_worker_reconnects_after_crash(tmp_path):
    watcher = BoomThenBlock()
    runtime = FakeRuntime(tmp_path, watcher)
    bus = EventBus(runtime.store)
    worker = AccountWorker(runtime, runtime._acc, bus)
    worker.start()
    deadline = time.time() + 3
    while time.time() < deadline and watcher.calls < 2:
        time.sleep(0.05)
    assert watcher.calls >= 2
    assert worker.alive() or worker.status in {"running", "reconnecting"}
    worker.join(timeout=2)
    assert worker.status == "stopped"


def test_restart_account_skips_live_worker(tmp_path):
    watcher = BoomThenBlock()
    runtime = FakeRuntime(tmp_path, watcher)
    bus = EventBus(runtime.store)
    supervisor = Supervisor(runtime, bus)
    supervisor.start_account("work")
    time.sleep(0.2)
    # After the first crash it reconnects and blocks; restart_account should no-op if alive.
    deadline = time.time() + 3
    while time.time() < deadline and not supervisor.workers["work"].alive():
        time.sleep(0.05)
    assert supervisor.restart_account("work") is False
    supervisor.stop_all()


class SlowConnectRuntime(FakeRuntime):
    """provider_for() blocks like a 30s IMAP handshake that ignores stop."""

    def __init__(self, root, watcher, delay=0.6):
        super().__init__(root, watcher)
        self.delay = delay
        self.entered = threading.Event()

    def provider_for(self, account_id):
        self.entered.set()
        time.sleep(self.delay)
        return FakeProvider(), self._acc, {}


def _mailkit_threads(account_id="work"):
    return [t for t in threading.enumerate() if t.name == f"mailkit-{account_id}"]


def test_slow_connect_does_not_spawn_second_watcher(tmp_path):
    """Boot-time doctor must not duplicate a watcher still in IMAP connect."""
    from mailkit.doctor import run_doctor

    watcher = BoomThenBlock()
    runtime = SlowConnectRuntime(tmp_path, watcher, delay=0.8)
    bus = EventBus(runtime.store)
    supervisor = Supervisor(runtime, bus)

    class App:
        def __init__(self):
            self.runtime = runtime
            self.supervisor = supervisor

    supervisor.start_account("work")
    assert runtime.entered.wait(1)
    worker = supervisor.workers["work"]
    original = worker.thread
    assert original is not None and original.is_alive()
    assert worker.alive()

    try:
        report = run_doctor(tmp_path, repair=True, app=App(), runtime=runtime)
        finding = next(f for f in report.findings if f.check == "workers")
        assert finding.status == "pass", finding.message
        assert supervisor.workers["work"].thread is original
        assert original.is_alive()
        assert _mailkit_threads("work") == [original]
    finally:
        supervisor.stop_all()
    assert not original.is_alive()


def test_start_account_joins_until_old_connect_exits(tmp_path):
    watcher = BoomThenBlock()
    runtime = SlowConnectRuntime(tmp_path, watcher, delay=0.5)
    bus = EventBus(runtime.store)
    supervisor = Supervisor(runtime, bus)
    supervisor.start_account("work")
    try:
        assert runtime.entered.wait(1)
        first = supervisor.workers["work"].thread
        supervisor.start_account("work")
        assert first is not None
        assert not first.is_alive()
        second = supervisor.workers["work"].thread
        assert second is not first
        assert second.is_alive()
        assert len(_mailkit_threads("work")) == 1
    finally:
        supervisor.stop_all()


def test_imap_connect_aborts_on_stop():
    from mailkit.providers.imap_smtp import ImapSmtpProvider

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    accepted = []

    def hold():
        try:
            conn, _ = srv.accept()
            accepted.append(conn)
            time.sleep(30)
            conn.close()
        except Exception:
            pass

    threading.Thread(target=hold, daemon=True).start()
    acc = AccountConfig(
        id="work",
        address="a@b.com",
        imap=ImapSettings(host="127.0.0.1", port=port, tls=False, timeout=30.0),
    )
    provider = ImapSmtpProvider(acc, {"password": "x"})
    stop = threading.Event()
    provider.set_stop(stop)
    errors = []

    def run():
        try:
            provider.connect()
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.time() + 2
    while time.time() < deadline and not accepted:
        time.sleep(0.02)
    stop.set()
    thread.join(timeout=2.5)
    srv.close()
    for conn in accepted:
        try:
            conn.close()
        except Exception:
            pass
    assert not thread.is_alive()
    assert errors
    assert isinstance(errors[0], NetworkError)
