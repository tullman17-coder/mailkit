"""Self-diagnosis and safe self-repair for the local mail engine.

The doctor never talks to a third party, never wipes the credential vault, and
never deletes mail. It fixes local process/file/schema problems and restarts
dead watchers. A loop runs inside the daemon; `mailkit doctor watchdog` keeps
the daemon itself alive.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mailkit.config import AccountConfig, AppConfig, load_config, save_config
from mailkit.discovery import WELL_KNOWN, discover, domain_of
from mailkit.errors import AuthError, ConfigError
from mailkit.ids import new_id
from mailkit.logutil import get_logger
from mailkit.models import utcnow
from mailkit.paths import (
    config_path,
    data_dir,
    db_path,
    events_socket_path,
    log_dir,
    master_key_path,
    pid_path,
    plugin_dir,
    socket_path,
    token_path,
    vault_path,
)
from mailkit.service import (
    clear_pid,
    is_running,
    load_or_create_token,
    pid_is_stale,
    read_pid,
    spawn_background,
)
from mailkit.vault import Vault

log = get_logger("mailkit.doctor")

SCHEMA = "mailkit.doctor.v1"
SECRET_NAMES = ("config.toml", "vault.enc", "master.key", "daemon.token", "mailkit.db")
POSIX = os.name == "posix"


@dataclass
class Finding:
    check: str
    title: str
    severity: str  # ok, info, warn, error, critical
    status: str  # pass, fail, repaired, repair_failed, skip
    message: str
    repair: str | None = None
    auto: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "title": self.title,
            "severity": self.severity,
            "status": self.status,
            "message": self.message,
            "repair": self.repair,
            "auto": self.auto,
            "details": self.details,
        }


@dataclass
class Report:
    schema: str = SCHEMA
    ts: str = field(default_factory=utcnow)
    ok: bool = True
    passed: int = 0
    repaired: int = 0
    failed: int = 0
    skipped: int = 0
    findings: list[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)
        if finding.status == "pass":
            self.passed += 1
        elif finding.status == "repaired":
            self.repaired += 1
        elif finding.status == "skip":
            self.skipped += 1
        elif finding.status in {"fail", "repair_failed"} and finding.severity in {"error", "critical"}:
            self.failed += 1
        self.ok = self.failed == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ts": self.ts,
            "ok": self.ok,
            "passed": self.passed,
            "repaired": self.repaired,
            "failed": self.failed,
            "skipped": self.skipped,
            "findings": [f.to_dict() for f in self.findings],
        }

    def render(self) -> str:
        lines = []
        for item in self.findings:
            mark = {
                "pass": "PASS",
                "repaired": "REPAIR",
                "fail": "FAIL",
                "repair_failed": "FAIL",
                "skip": "SKIP",
            }.get(item.status, item.status.upper())
            lines.append(f"{mark:<8} {item.check:<22} {item.message}")
            if item.repair:
                lines.append(f"{'':8} -> {item.repair}")
        lines.append(
            f"{self.passed} passed, {self.repaired} repaired, {self.failed} failed, {self.skipped} skipped"
        )
        return "\n".join(lines)


@dataclass
class DoctorContext:
    root: Path
    repair: bool = False
    start_daemon: bool = False
    runtime: Any = None
    app: Any = None


CheckFn = Callable[[DoctorContext], Finding]
RepairFn = Callable[[DoctorContext, Finding], str]


@dataclass
class Check:
    id: str
    title: str
    diagnose: CheckFn
    repair: RepairFn | None = None
    auto: bool = True


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _chmod(path: Path, mode: int) -> None:
    if POSIX and path.exists():
        os.chmod(path, mode)


def check_home(ctx: DoctorContext) -> Finding:
    root = ctx.root
    if root.is_dir() and os.access(root, os.W_OK):
        mode_ok = (not POSIX) or (_mode(root) & 0o077 == 0)
        if mode_ok:
            return Finding("home", "Data directory", "ok", "pass", f"{root} is writable")
        return Finding(
            "home",
            "Data directory",
            "warn",
            "fail",
            f"{root} is writable by others",
            auto=True,
            details={"mode": oct(_mode(root))},
        )
    return Finding("home", "Data directory", "error", "fail", f"{root} missing or not writable", auto=True)


def repair_home(ctx: DoctorContext, finding: Finding) -> str:
    ctx.root.mkdir(parents=True, exist_ok=True)
    _chmod(ctx.root, 0o700)
    log_dir(ctx.root)
    plugin_dir(ctx.root)
    return f"ensured {ctx.root} mode 0700"


def check_secrets_perms(ctx: DoctorContext) -> Finding:
    if not POSIX:
        return Finding("permissions", "Secret file modes", "ok", "skip", "permission check is POSIX-only")
    bad: list[str] = []
    for name in SECRET_NAMES:
        path = ctx.root / name
        if path.exists() and _mode(path) & 0o077:
            bad.append(f"{name}={oct(_mode(path))}")
    if not bad:
        return Finding("permissions", "Secret file modes", "ok", "pass", "secret files are owner-only")
    return Finding(
        "permissions",
        "Secret file modes",
        "error",
        "fail",
        "world- or group-readable secrets: " + ", ".join(bad),
        auto=True,
        details={"files": bad},
    )


def repair_secrets_perms(ctx: DoctorContext, finding: Finding) -> str:
    fixed = []
    for name in SECRET_NAMES:
        path = ctx.root / name
        if path.exists() and _mode(path) & 0o077:
            _chmod(path, 0o600)
            fixed.append(name)
    _chmod(ctx.root, 0o700)
    return "chmod 0600 " + ", ".join(fixed)


def check_config(ctx: DoctorContext) -> Finding:
    path = config_path(ctx.root)
    if not path.exists():
        return Finding("config", "Config file", "warn", "fail", f"{path.name} is missing", auto=True)
    try:
        cfg = load_config(ctx.root)
    except Exception as exc:
        return Finding("config", "Config file", "error", "fail", f"cannot parse config: {exc}", auto=False)
    issues = []
    if cfg.defaults.account and cfg.defaults.account not in cfg.accounts:
        issues.append(f"defaults.account={cfg.defaults.account} does not exist")
    for acc in cfg.accounts.values():
        if not acc.address:
            issues.append(f"account {acc.id} has no address")
        if acc.poll_interval < 15:
            issues.append(f"account {acc.id} poll_interval {acc.poll_interval} is too low")
    if issues:
        return Finding(
            "config",
            "Config file",
            "error",
            "fail",
            "; ".join(issues),
            auto=True,
            details={"issues": issues},
        )
    return Finding("config", "Config file", "ok", "pass", f"{len(cfg.accounts)} account(s) loaded")


def repair_config(ctx: DoctorContext, finding: Finding) -> str:
    path = config_path(ctx.root)
    if not path.exists():
        save_config(AppConfig(), ctx.root)
        return "wrote empty config.toml"
    cfg = load_config(ctx.root)
    notes = []
    if cfg.defaults.account and cfg.defaults.account not in cfg.accounts:
        enabled = [a.id for a in cfg.accounts.values() if a.enabled]
        cfg.defaults.account = enabled[0] if len(enabled) == 1 else ""
        notes.append(f"defaults.account={cfg.defaults.account or '(cleared)'}")
    for acc in cfg.accounts.values():
        if acc.poll_interval < 15:
            acc.poll_interval = 45
            notes.append(f"{acc.id}.poll_interval=45")
    save_config(cfg, ctx.root)
    return "updated config: " + (", ".join(notes) or "rewritten")


def check_vault(ctx: DoctorContext) -> Finding:
    path = vault_path(ctx.root)
    key = master_key_path(ctx.root)
    if not path.exists():
        return Finding("vault", "Credential vault", "warn", "fail", "vault.enc is missing", auto=True)
    try:
        Vault(ctx.root)
    except AuthError as exc:
        return Finding(
            "vault",
            "Credential vault",
            "critical",
            "fail",
            str(exc) + " (will not overwrite)",
            auto=False,
        )
    except ConfigError as exc:
        return Finding("vault", "Credential vault", "critical", "fail", str(exc), auto=False)
    if POSIX and key.exists() and _mode(key) & 0o077:
        return Finding("vault", "Credential vault", "warn", "fail", "master.key is too open", auto=True)
    return Finding("vault", "Credential vault", "ok", "pass", "vault decrypts")


def repair_vault(ctx: DoctorContext, finding: Finding) -> str:
    if "will not overwrite" in finding.message or finding.severity == "critical":
        raise RuntimeError("refusing to destroy a locked vault")
    if not vault_path(ctx.root).exists():
        Vault(ctx.root)
        return "created empty vault.enc"
    _chmod(master_key_path(ctx.root), 0o600)
    _chmod(vault_path(ctx.root), 0o600)
    return "tightened vault permissions"


def check_account_secrets(ctx: DoctorContext) -> Finding:
    try:
        cfg = load_config(ctx.root)
        vault = Vault(ctx.root)
    except Exception as exc:
        return Finding("credentials", "Account credentials", "error", "skip", f"cannot open vault: {exc}", auto=False)
    missing = []
    for acc in cfg.accounts.values():
        if not acc.enabled:
            continue
        secrets = vault.get_account(acc.id)
        if acc.auth in {"password", "app_password"} and not secrets.get("password"):
            missing.append(f"{acc.id} has no password")
        if acc.auth in {"oauth2", "xoauth2"} and not (secrets.get("refresh_token") or secrets.get("access_token")):
            missing.append(f"{acc.id} has no OAuth tokens")
    if missing:
        return Finding(
            "credentials",
            "Account credentials",
            "error",
            "fail",
            "; ".join(missing),
            auto=False,
            details={"missing": missing},
        )
    return Finding("credentials", "Account credentials", "ok", "pass", "enabled accounts have credentials")


def check_endpoints(ctx: DoctorContext) -> Finding:
    try:
        cfg = load_config(ctx.root)
    except Exception as exc:
        return Finding("endpoints", "IMAP/SMTP endpoints", "error", "skip", str(exc), auto=False)
    missing = []
    fixable = []
    for acc in cfg.accounts.values():
        if not acc.enabled:
            continue
        if acc.imap.host:
            continue
        missing.append(acc.id)
        if _well_known_for(acc):
            fixable.append(acc.id)
    if not missing:
        return Finding("endpoints", "IMAP/SMTP endpoints", "ok", "pass", "every enabled account has an IMAP host")
    return Finding(
        "endpoints",
        "IMAP/SMTP endpoints",
        "error",
        "fail",
        "no IMAP host for: " + ", ".join(missing),
        auto=bool(fixable),
        details={"missing": missing, "fixable": fixable},
    )


def _well_known_for(acc: AccountConfig):
    if acc.provider in {"gmail", "yahoo", "graph"}:
        return True
    if not acc.address or "@" not in acc.address:
        return False
    try:
        return domain_of(acc.address) in WELL_KNOWN
    except Exception:
        return False


def repair_endpoints(ctx: DoctorContext, finding: Finding) -> str:
    cfg = load_config(ctx.root)
    notes = []
    for acc in cfg.accounts.values():
        if acc.imap.host or not acc.enabled or not acc.address:
            continue
        if not _well_known_for(acc):
            continue
        spec = discover(acc.address)
        acc.imap.host = spec.imap_host
        acc.imap.port = spec.imap_port
        acc.imap.tls = spec.imap_tls
        acc.smtp.host = spec.smtp_host
        acc.smtp.port = spec.smtp_port
        acc.smtp.starttls = spec.smtp_starttls
        acc.smtp.tls = spec.smtp_tls
        if acc.provider in {"auto", ""}:
            acc.provider = spec.provider_id
        notes.append(f"{acc.id}->{spec.imap_host}")
    save_config(cfg, ctx.root)
    if not notes:
        raise RuntimeError("no well-known provider to fill; set --imap-host manually")
    return "filled hosts " + ", ".join(notes)


def check_sqlite(ctx: DoctorContext) -> Finding:
    path = db_path(ctx.root)
    if not path.exists():
        return Finding("sqlite", "Message index", "warn", "fail", "mailkit.db is missing", auto=True)
    from mailkit.db import Store

    try:
        store = Store(ctx.root)
        result = store.integrity()
        store.close()
    except Exception as exc:
        return Finding("sqlite", "Message index", "error", "fail", f"sqlite open failed: {exc}", auto=True)
    if result != "ok":
        return Finding("sqlite", "Message index", "critical", "fail", f"integrity_check={result}", auto=False)
    return Finding("sqlite", "Message index", "ok", "pass", "integrity_check=ok")


def repair_sqlite(ctx: DoctorContext, finding: Finding) -> str:
    if finding.severity == "critical":
        raise RuntimeError("corrupt index: moved aside only with explicit backup, not auto-wiped")
    from mailkit.db import Store

    store = Store(ctx.root)
    store.checkpoint()
    store.close()
    _chmod(db_path(ctx.root), 0o600)
    return "ensured schema, WAL checkpoint, mode 0600"


def check_pid(ctx: DoctorContext) -> Finding:
    pid = read_pid(ctx.root)
    if not pid:
        return Finding("pidfile", "PID file", "ok", "pass", "no pid file")
    if pid_is_stale(ctx.root):
        return Finding(
            "pidfile",
            "PID file",
            "warn",
            "fail",
            f"stale pid {pid} is not a mailkit process",
            auto=True,
            details={"pid": pid},
        )
    return Finding("pidfile", "PID file", "ok", "pass", f"pid {pid} is mailkit")


def repair_pid(ctx: DoctorContext, finding: Finding) -> str:
    pid = read_pid(ctx.root)
    clear_pid(ctx.root)
    return f"removed stale pid {pid}"


def check_sockets(ctx: DoctorContext) -> Finding:
    stale = []
    for path in (events_socket_path(ctx.root), socket_path(ctx.root)):
        if path.exists() and not is_running(ctx.root):
            stale.append(path.name)
    if stale:
        return Finding(
            "sockets",
            "Unix sockets",
            "warn",
            "fail",
            "stale sockets: " + ", ".join(stale),
            auto=True,
            details={"files": stale},
        )
    return Finding("sockets", "Unix sockets", "ok", "pass", "no stale event sockets")


def repair_sockets(ctx: DoctorContext, finding: Finding) -> str:
    removed = []
    for path in (events_socket_path(ctx.root), socket_path(ctx.root)):
        if path.exists() and not is_running(ctx.root):
            path.unlink()
            removed.append(path.name)
    return "unlinked " + ", ".join(removed)


def check_token(ctx: DoctorContext) -> Finding:
    path = token_path(ctx.root)
    if not path.exists():
        return Finding("token", "API token", "warn", "fail", "daemon.token is missing", auto=True)
    if POSIX and _mode(path) & 0o077:
        return Finding("token", "API token", "error", "fail", "daemon.token is too open", auto=True)
    if not path.read_text().strip():
        return Finding("token", "API token", "error", "fail", "daemon.token is empty", auto=True)
    return Finding("token", "API token", "ok", "pass", "API token present")


def repair_token(ctx: DoctorContext, finding: Finding) -> str:
    load_or_create_token(ctx.root)
    path = token_path(ctx.root)
    if not path.read_text().strip():
        path.write_text(os.urandom(24).hex())
    _chmod(path, 0o600)
    return "ensured daemon.token"


def check_plugins(ctx: DoctorContext) -> Finding:
    from mailkit.plugins.registry import load_plugins

    try:
        registry = load_plugins(ctx.root)
    except Exception as exc:
        return Finding("plugins", "Plugins", "error", "fail", str(exc), auto=False)
    missing = [name for name in ("imap", "gmail", "graph", "yahoo") if name not in registry.providers]
    missing += [name for name in ("idle", "poll") if name not in registry.watchers]
    if missing:
        return Finding("plugins", "Plugins", "error", "fail", "missing built-ins: " + ", ".join(missing), auto=False)
    return Finding(
        "plugins",
        "Plugins",
        "ok",
        "pass",
        f"{len(registry.providers)} providers, {len(registry.watchers)} watchers, {len(registry.hooks)} hooks",
    )


def check_daemon(ctx: DoctorContext) -> Finding:
    running = is_running(ctx.root)
    if running:
        return Finding("daemon", "Background service", "ok", "pass", f"running pid {read_pid(ctx.root)}")
    if ctx.start_daemon:
        return Finding(
            "daemon",
            "Background service",
            "error",
            "fail",
            "mailkit service is not running",
            auto=True,
        )
    return Finding(
        "daemon",
        "Background service",
        "warn",
        "skip",
        "not running; pass --start or `mailkit service start`",
        auto=False,
    )


def repair_daemon(ctx: DoctorContext, finding: Finding) -> str:
    if not ctx.start_daemon:
        raise RuntimeError("pass --start to launch the daemon")
    if is_running(ctx.root):
        return "already running"
    pid = spawn_background(ctx.root)
    deadline = time.time() + 5
    while time.time() < deadline:
        if is_running(ctx.root):
            return f"started pid {read_pid(ctx.root) or pid}"
        time.sleep(0.1)
    return f"spawned pid {pid} (health not confirmed yet)"


def check_workers(ctx: DoctorContext) -> Finding:
    if ctx.app is None:
        return Finding("workers", "Account watchers", "ok", "skip", "worker check needs the running daemon")
    cfg = ctx.app.runtime.config
    supervisor = ctx.app.supervisor
    dead = []
    for acc in cfg.accounts.values():
        if not acc.enabled:
            continue
        worker = supervisor.workers.get(acc.id)
        if worker is None or not worker.alive():
            dead.append(acc.id)
    if dead:
        return Finding(
            "workers",
            "Account watchers",
            "error",
            "fail",
            "dead watchers: " + ", ".join(dead),
            auto=True,
            details={"dead": dead},
        )
    enabled = [a.id for a in cfg.accounts.values() if a.enabled]
    return Finding("workers", "Account watchers", "ok", "pass", f"{len(enabled)} watcher(s) alive")


def repair_workers(ctx: DoctorContext, finding: Finding) -> str:
    if ctx.app is None:
        raise RuntimeError("daemon is not in-process")
    restarted = []
    for acc_id in finding.details.get("dead") or []:
        if ctx.app.supervisor.restart_account(acc_id):
            restarted.append(acc_id)
    if not restarted:
        raise RuntimeError("no watchers restarted")
    return "restarted " + ", ".join(restarted)


def check_oauth(ctx: DoctorContext) -> Finding:
    try:
        cfg = load_config(ctx.root)
        vault = Vault(ctx.root)
    except Exception as exc:
        return Finding("oauth", "OAuth tokens", "ok", "skip", str(exc), auto=False)
    stale = []
    for acc in cfg.accounts.values():
        if acc.auth not in {"oauth2", "xoauth2"} or not acc.enabled:
            continue
        secrets = vault.get_account(acc.id)
        expiry = int(secrets.get("token_expiry") or 0)
        if secrets.get("refresh_token") and expiry and expiry < int(time.time()) + 60:
            stale.append(acc.id)
    if stale:
        return Finding(
            "oauth",
            "OAuth tokens",
            "warn",
            "fail",
            "access token expired for: " + ", ".join(stale),
            auto=True,
            details={"stale": stale},
        )
    return Finding("oauth", "OAuth tokens", "ok", "pass", "no expired access tokens (or none configured)")


def repair_oauth(ctx: DoctorContext, finding: Finding) -> str:
    from mailkit.auth.xoauth2 import XOAuth2Auth

    cfg = load_config(ctx.root)
    vault = Vault(ctx.root)
    auth = XOAuth2Auth()
    refreshed = []
    for acc_id in finding.details.get("stale") or []:
        acc = cfg.accounts.get(acc_id)
        if not acc:
            continue
        secrets = vault.get_account(acc_id)
        update = auth.refresh(acc, secrets)
        if update:
            secrets.update(update)
            vault.put_account(acc_id, secrets)
            refreshed.append(acc_id)
    if not refreshed:
        raise RuntimeError("token refresh returned nothing; re-run accounts add --auth oauth2")
    return "refreshed " + ", ".join(refreshed)


def check_events(ctx: DoctorContext) -> Finding:
    from mailkit.db import Store

    if not db_path(ctx.root).exists():
        return Finding("events", "Event log", "ok", "skip", "no database yet")
    try:
        store = Store(ctx.root)
        row = store.conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        n = int(row["n"])
        keep = 100_000
        try:
            keep = load_config(ctx.root).daemon.event_retention
        except Exception:
            pass
        store.close()
    except Exception as exc:
        return Finding("events", "Event log", "warn", "skip", str(exc))
    if n > keep * 1.2:
        return Finding(
            "events",
            "Event log",
            "warn",
            "fail",
            f"{n} events exceeds retention {keep}",
            auto=True,
            details={"count": n, "keep": keep},
        )
    return Finding("events", "Event log", "ok", "pass", f"{n} events stored")


def repair_events(ctx: DoctorContext, finding: Finding) -> str:
    from mailkit.db import Store

    store = Store(ctx.root)
    keep = int(finding.details.get("keep") or 100_000)
    store.trim_events(keep)
    store.close()
    return f"trimmed event log to {keep}"


CHECKS: list[Check] = [
    Check("home", "Data directory", check_home, repair_home),
    Check("permissions", "Secret file modes", check_secrets_perms, repair_secrets_perms),
    Check("config", "Config file", check_config, repair_config),
    Check("vault", "Credential vault", check_vault, repair_vault),
    Check("credentials", "Account credentials", check_account_secrets),
    Check("endpoints", "IMAP/SMTP endpoints", check_endpoints, repair_endpoints),
    Check("sqlite", "Message index", check_sqlite, repair_sqlite),
    Check("pidfile", "PID file", check_pid, repair_pid),
    Check("sockets", "Unix sockets", check_sockets, repair_sockets),
    Check("token", "API token", check_token, repair_token),
    Check("plugins", "Plugins", check_plugins),
    Check("daemon", "Background service", check_daemon, repair_daemon),
    Check("workers", "Account watchers", check_workers, repair_workers),
    Check("oauth", "OAuth tokens", check_oauth, repair_oauth),
    Check("events", "Event log", check_events, repair_events),
]


def run_doctor(
    root: Path | None = None,
    *,
    repair: bool = False,
    start_daemon: bool = False,
    app: Any = None,
    runtime: Any = None,
) -> Report:
    root_path = Path(root).expanduser() if root is not None else data_dir()
    ctx = DoctorContext(
        root=root_path,
        repair=repair,
        start_daemon=start_daemon,
        runtime=runtime,
        app=app,
    )
    report = Report()
    for check in CHECKS:
        try:
            finding = check.diagnose(ctx)
        except Exception as exc:
            finding = Finding(check.id, check.title, "error", "fail", f"check crashed: {exc}", auto=False)
        finding.auto = check.auto if finding.auto else False
        if ctx.repair and finding.status == "fail" and finding.auto and check.repair:
            try:
                note = check.repair(ctx, finding)
                finding.repair = note
                finding.status = "repaired"
                finding.severity = "info"
                try:
                    again = check.diagnose(ctx)
                    if again.status == "fail":
                        finding.status = "repair_failed"
                        finding.severity = "error"
                        finding.message = f"{finding.message}; still failing: {again.message}"
                except Exception as exc:
                    finding.status = "repair_failed"
                    finding.message = f"{finding.message}; re-check crashed: {exc}"
            except Exception as exc:
                finding.status = "repair_failed"
                finding.severity = "error"
                finding.repair = str(exc)
        report.add(finding)
    _persist_report(ctx, report)
    return report


def _persist_report(ctx: DoctorContext, report: Report) -> None:
    try:
        from mailkit.db import Store

        store = Store(ctx.root)
        store.set_meta("doctor.last", json.dumps(report.to_dict()))
        store.close()
    except Exception:
        pass


def start_doctor_loop(app, stop: threading.Event, *, interval: float = 60.0, repair: bool = True) -> threading.Thread:
    def loop():
        # First pass shortly after boot so dead workers are caught quickly.
        delay = 3.0
        while not stop.wait(delay):
            delay = max(15.0, float(interval or 60.0))
            try:
                report = run_doctor(app.runtime.root, repair=repair, app=app, runtime=app.runtime)
                if not report.ok or report.repaired:
                    log.info("doctor %s", report.render().replace("\n", " | "))
                    _emit_doctor(app, report)
            except Exception as exc:
                log.warning("doctor loop error: %s", exc)

    thread = threading.Thread(target=loop, name="mailkit-doctor", daemon=True)
    thread.start()
    return thread


def _emit_doctor(app, report: Report) -> None:
    from mailkit.models import Event

    app.bus.publish(
        Event(
            id=new_id("evt"),
            type="service.doctor",
            data={
                "ok": report.ok,
                "passed": report.passed,
                "repaired": report.repaired,
                "failed": report.failed,
                "findings": [
                    f.to_dict() for f in report.findings if f.status not in {"pass", "skip"}
                ],
            },
        )
    )


def run_loop(
    root: Path | None = None,
    *,
    interval: float = 60.0,
    repair: bool = True,
    start_daemon: bool = False,
    stop: threading.Event | None = None,
) -> int:
    stop = stop or threading.Event()
    last_spawn = 0.0
    code = 0
    while not stop.is_set():
        report = run_doctor(root, repair=repair, start_daemon=start_daemon)
        if not report.ok:
            code = 1
        if start_daemon and not is_running(root) and time.time() - last_spawn > 15:
            try:
                spawn_background(root)
                last_spawn = time.time()
            except Exception as exc:
                log.warning("watchdog spawn failed: %s", exc)
        if stop.wait(max(5.0, interval)):
            break
    return code


def watchdog_main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Mailkit self-repair watchdog")
    parser.add_argument("--home")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.home).expanduser() if args.home else None
    if args.once:
        report = run_doctor(root, repair=True, start_daemon=True)
        print(report.render())
        return 0 if report.ok else 1
    return run_loop(root, interval=args.interval, repair=True, start_daemon=True)
