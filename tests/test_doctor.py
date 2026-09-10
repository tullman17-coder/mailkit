import os
import threading
import time
from pathlib import Path

from mailkit.config import AccountConfig, AppConfig, ImapSettings, save_config
from mailkit.doctor import Finding, Report, run_doctor, run_loop
from mailkit.vault import Vault


def test_repair_empty_home(tmp_path: Path):
    report = run_doctor(tmp_path, repair=True)
    by_check = {f.check: f for f in report.findings}
    assert by_check["config"].status == "repaired"
    assert by_check["vault"].status == "repaired"
    assert by_check["sqlite"].status == "repaired"
    assert by_check["token"].status == "repaired"
    assert (tmp_path / "config.toml").exists()
    assert (tmp_path / "vault.enc").exists()
    assert (tmp_path / "mailkit.db").exists()
    assert (tmp_path / "daemon.token").read_text().strip()
    assert report.ok is True
    again = run_doctor(tmp_path, repair=False)
    assert again.ok is True
    assert all(f.status in {"pass", "skip"} for f in again.findings)


def test_secret_permissions_repaired(tmp_path: Path):
    run_doctor(tmp_path, repair=True)
    vault = tmp_path / "vault.enc"
    vault.chmod(0o644)
    report = run_doctor(tmp_path, repair=True)
    finding = next(f for f in report.findings if f.check == "permissions")
    assert finding.status == "repaired"
    assert os.stat(vault).st_mode & 0o077 == 0


def test_stale_pid_removed(tmp_path: Path):
    (tmp_path / "mailkit.pid").write_text("99999999\n")
    report = run_doctor(tmp_path, repair=True)
    finding = next(f for f in report.findings if f.check == "pidfile")
    assert finding.status == "repaired"
    assert not (tmp_path / "mailkit.pid").exists()


def test_stale_socket_removed(tmp_path: Path):
    sock = tmp_path / "events.sock"
    sock.write_text("stale")
    report = run_doctor(tmp_path, repair=True)
    finding = next(f for f in report.findings if f.check == "sockets")
    assert finding.status == "repaired"
    assert not sock.exists()


def test_corrupt_vault_is_not_overwritten(tmp_path: Path):
    blob = b"not-a-vault"
    (tmp_path / "vault.enc").write_bytes(blob)
    (tmp_path / "master.key").write_bytes(os.urandom(32))
    report = run_doctor(tmp_path, repair=True)
    finding = next(f for f in report.findings if f.check == "vault")
    assert finding.status == "fail"
    assert finding.auto is False
    assert (tmp_path / "vault.enc").read_bytes() == blob
    assert report.ok is False


def test_gmail_hosts_filled(tmp_path: Path):
    cfg = AppConfig()
    cfg.accounts["work"] = AccountConfig(id="work", address="me@gmail.com", provider="auto")
    save_config(cfg, tmp_path)
    Vault(tmp_path).put_account("work", {"password": "x"})
    report = run_doctor(tmp_path, repair=True)
    finding = next(f for f in report.findings if f.check == "endpoints")
    assert finding.status == "repaired"
    loaded = __import__("mailkit.config", fromlist=["load_config"]).load_config(tmp_path)
    assert loaded.accounts["work"].imap.host == "imap.gmail.com"
    assert loaded.accounts["work"].smtp.host == "smtp.gmail.com"


def test_default_account_fixed(tmp_path: Path):
    cfg = AppConfig()
    cfg.defaults.account = "ghost"
    cfg.accounts["work"] = AccountConfig(
        id="work",
        address="me@example.com",
        imap=ImapSettings(host="imap.example.com"),
    )
    save_config(cfg, tmp_path)
    Vault(tmp_path).put_account("work", {"password": "x"})
    report = run_doctor(tmp_path, repair=True)
    finding = next(f for f in report.findings if f.check == "config")
    assert finding.status == "repaired"
    loaded = __import__("mailkit.config", fromlist=["load_config"]).load_config(tmp_path)
    assert loaded.defaults.account == "work"


def test_custom_domain_hosts_not_guessed(tmp_path: Path):
    cfg = AppConfig()
    cfg.accounts["work"] = AccountConfig(id="work", address="me@not-a-real-tld.invalid", provider="imap")
    save_config(cfg, tmp_path)
    Vault(tmp_path).put_account("work", {"password": "x"})
    report = run_doctor(tmp_path, repair=True)
    finding = next(f for f in report.findings if f.check == "endpoints")
    assert finding.status in {"fail", "repair_failed"}
    loaded = __import__("mailkit.config", fromlist=["load_config"]).load_config(tmp_path)
    assert loaded.accounts["work"].imap.host == ""


def test_worker_restart_via_doctor(tmp_path: Path):
    cfg = AppConfig()
    cfg.accounts["work"] = AccountConfig(
        id="work",
        address="me@example.com",
        imap=ImapSettings(host="imap.example.com"),
    )
    save_config(cfg, tmp_path)
    Vault(tmp_path).put_account("work", {"password": "x"})

    class Dead:
        def alive(self):
            return False

    class Live:
        def alive(self):
            return True

    class Super:
        def __init__(self):
            self.workers = {"work": Dead()}
            self.restarted = []

        def restart_account(self, account_id):
            self.restarted.append(account_id)
            self.workers[account_id] = Live()
            return True

    class Runtime:
        def __init__(self):
            self.root = tmp_path
            self.config = cfg

    class App:
        runtime = Runtime()
        supervisor = Super()

    app = App()
    report = run_doctor(tmp_path, repair=True, app=app)
    finding = next(f for f in report.findings if f.check == "workers")
    assert finding.status == "repaired"
    assert app.supervisor.restarted == ["work"]


def test_doctor_skips_connecting_watcher_thread(tmp_path: Path):
    cfg = AppConfig()
    cfg.accounts["work"] = AccountConfig(
        id="work",
        address="me@example.com",
        imap=ImapSettings(host="imap.example.com"),
    )
    save_config(cfg, tmp_path)
    Vault(tmp_path).put_account("work", {"password": "x"})
    stop = threading.Event()

    def block():
        stop.wait()

    thread = threading.Thread(target=block, name="mailkit-work", daemon=True)
    thread.start()

    class Connecting:
        def alive(self):
            return False

        def __init__(self, thread):
            self.thread = thread
            self.status = "stopped"
            self.last_start = time.time()

    class Super:
        def __init__(self):
            self.workers = {"work": Connecting(thread)}
            self.restarted = []

        def restart_account(self, account_id):
            self.restarted.append(account_id)
            return True

    class Runtime:
        def __init__(self):
            self.root = tmp_path
            self.config = cfg

    class App:
        runtime = Runtime()
        supervisor = Super()

    app = App()
    try:
        report = run_doctor(tmp_path, repair=True, app=app)
        finding = next(f for f in report.findings if f.check == "workers")
        assert finding.status == "pass"
        assert app.supervisor.restarted == []
    finally:
        stop.set()
        thread.join(timeout=1)


def test_report_schema_and_cli(tmp_path: Path, monkeypatch):
    from mailkit.cli.main import main
    from mailkit.cli.schemas import dump_schema

    schema = dump_schema("doctor")
    assert schema["$id"] == "mailkit.doctor.v1"
    monkeypatch.setenv("MAILKIT_HOME", str(tmp_path))
    assert main(["doctor", "--repair", "-o", "json"]) == 0


def test_loop_stops(tmp_path: Path):
    stop = threading.Event()
    errors = []

    def run():
        try:
            run_loop(tmp_path, interval=0.15, repair=True, stop=stop)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.4)
    stop.set()
    thread.join(timeout=3)
    assert errors == []
    assert not thread.is_alive()


def test_report_ok_ignores_warnings():
    report = Report()
    report.add(Finding("daemon", "Background service", "warn", "fail", "not running"))
    report.add(Finding("home", "Data directory", "ok", "pass", "ok"))
    assert report.ok is True
    assert report.failed == 0
    report.add(Finding("vault", "Credential vault", "critical", "fail", "locked"))
    assert report.ok is False
    assert report.failed == 1
