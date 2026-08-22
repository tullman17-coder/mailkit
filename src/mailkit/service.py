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


def spawn_background(root: Path | None = None) -> int:
    if is_running(root):
        pid = read_pid(root)
        raise DaemonError(f"mailkit service already running (pid {pid})")
    cmd = [sys.executable, "-m", "mailkit", "service", "run", "--background-child"]
    env = os.environ.copy()
    src = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    if root:
        env["MAILKIT_HOME"] = str(root)
    pid = os.spawnve(os.P_NOWAIT, sys.executable, cmd, env)
    # The child rewrites the pid file; wait briefly for it.
    for _ in range(20):
        time.sleep(0.1)
        if is_running(root):
            return read_pid(root) or pid
    return pid
