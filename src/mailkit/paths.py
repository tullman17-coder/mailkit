"""Local data directory. Never sent off-box."""

from __future__ import annotations

import os
from pathlib import Path


APP_DIR_NAME = "mailkit"


def data_dir(override: str | os.PathLike[str] | None = None) -> Path:
    if override:
        path = Path(override).expanduser()
    elif os.environ.get("MAILKIT_HOME"):
        path = Path(os.environ["MAILKIT_HOME"]).expanduser()
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            path = Path(xdg) / APP_DIR_NAME
        else:
            path = Path.home() / ".mailkit"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "config.toml"


def vault_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "vault.enc"


def master_key_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "master.key"


def db_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "mailkit.db"


def pid_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "mailkit.pid"


def token_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "daemon.token"


def log_dir(root: Path | None = None) -> Path:
    path = (root or data_dir()) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def plugin_dir(root: Path | None = None) -> Path:
    path = (root or data_dir()) / "plugins"
    path.mkdir(parents=True, exist_ok=True)
    return path


def socket_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "mailkit.sock"


def events_socket_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "events.sock"
