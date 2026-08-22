import time

from mailkit.config import AccountConfig, AppConfig
from mailkit.db import Store
from mailkit.events import EventBus
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
