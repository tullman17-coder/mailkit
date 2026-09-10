"""Foreground and background daemon process helpers."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from mailkit.errors import DaemonError
from mailkit.paths import pid_path, token_path


def read_pid(root: Path | None = None) -> int | None:
    path = pid_path(root)
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except ValueError:
        return None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_command(pid: int) -> str:
    if os.name == "nt":
        return ""
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except Exception:
        return ""


def is_mailkit_process(pid: int) -> bool | None:
    """True if pid is mailkit, False if it is some other process, None if unknown."""
    if not pid_alive(pid):
        return False
    cmd = pid_command(pid).lower()
    if not cmd:
        return None
    return "mailkit" in cmd


def is_running(root: Path | None = None) -> bool:
    pid = read_pid(root)
    if not pid:
        return False
    ident = is_mailkit_process(pid)
    if ident is False:
        return False
    return True


def pid_is_stale(root: Path | None = None) -> bool:
    pid = read_pid(root)
    if not pid:
        return False
    ident = is_mailkit_process(pid)
    return ident is False


def write_pid(root: Path | None = None) -> None:
    pid_path(root).write_text(str(os.getpid()))


def clear_pid(root: Path | None = None) -> None:
    path = pid_path(root)
    if path.exists():
        path.unlink()


def load_or_create_token(root: Path | None = None) -> str:
    path = token_path(root)
    if path.exists():
        return path.read_text().strip()
    token = os.urandom(24).hex()
    path.write_text(token)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def stop_daemon(root: Path | None = None, timeout: float = 10.0) -> None:
    pid = read_pid(root)
    if not pid or not is_running(root):
        clear_pid(root)
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_running(root):
            clear_pid(root)
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    clear_pid(root)


# Frozen Mailkit.app ignores `-m` and used to re-open the UI for every spawn.
_DAEMON_ARGV = ["service", "run", "--background-child"]
_SPAWN_GEN_ENV = "MAILKIT_SPAWN_GEN"
_MAX_SPAWN_GEN = 1
_CLI_HEADS = frozenset(
    {
        "service",
        "accounts",
        "schema",
        "desktop",
        "demo",
        "pair",
        "doctor",
        "plugins",
        "messages",
        "mail",
        "send",
        "reply",
        "watch",
        "events",
        "subscriptions",
        "webhooks",
        "rules",
        "mailboxes",
        "-h",
        "--help",
        "--version",
    }
)


def spawn_generation(env: dict[str, str] | None = None) -> int:
    raw = (env or os.environ).get(_SPAWN_GEN_ENV, "0")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _strip_module_prefix(argv: list[str]) -> list[str]:
    if argv and argv[0] == "-m" and len(argv) >= 2 and argv[1] == "mailkit":
        return argv[2:]
    return argv


def _is_service_run(argv: list[str]) -> bool:
    return len(argv) >= 2 and argv[0] == "service" and argv[1] == "run"


def frozen_dispatch_argv(
    argv: list[str],
    role: str | None = None,
    spawn_gen: int = 0,
) -> list[str] | None:
    """CLI argv if this frozen process should not open a window. None = open UI.

    A nested spawn (MAILKIT_ROLE=daemon or MAILKIT_SPAWN_GEN>=1) must never
    open the desktop. That is what froze Macs on v0.1.0. Nested children
    always get daemon argv unless they are already ``service run``; ``desktop``
    is a CLI head and must not be treated as an allowed child command.
    """
    nested = role == "daemon" or spawn_gen > 0
    module_style = bool(argv) and argv[0] == "-m" and len(argv) >= 2 and argv[1] == "mailkit"
    rest = _strip_module_prefix(argv)

    if nested:
        if _is_service_run(rest):
            return list(rest)
        return list(_DAEMON_ARGV)

    if not argv:
        return None
    if module_style:
        return rest or list(_DAEMON_ARGV)
    if argv[0] in _CLI_HEADS:
        return argv
    return None


def daemon_spawn_cmd(executable: str, *, frozen: bool) -> list[str]:
    if frozen:
        return [executable, *_DAEMON_ARGV]
    return [executable, "-m", "mailkit", *_DAEMON_ARGV]


def spawn_background(root: Path | None = None) -> int:
    if is_running(root):
        pid = read_pid(root)
        raise DaemonError(f"mailkit service already running (pid {pid})")
    gen = spawn_generation()
    if gen >= _MAX_SPAWN_GEN:
        raise DaemonError("refusing nested engine spawn")
    frozen = bool(getattr(sys, "frozen", False))
    cmd = daemon_spawn_cmd(sys.executable, frozen=frozen)
    env = os.environ.copy()
    src = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    env["MAILKIT_ROLE"] = "daemon"
    env[_SPAWN_GEN_ENV] = str(gen + 1)
    if root:
        env["MAILKIT_HOME"] = str(root)
    popen_kw: dict = {
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name != "nt":
        popen_kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **popen_kw)
    pid = proc.pid
    # The child rewrites the pid file; wait briefly for it.
    for _ in range(20):
        time.sleep(0.1)
        if is_running(root):
            return read_pid(root) or pid
    return pid
