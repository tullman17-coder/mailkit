"""Optional OS service units. The engine itself has no OS-specific core."""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

from mailkit.paths import log_dir


LABEL = "dev.mailkit.daemon"


def unit_text(target: str, root: Path) -> str:
    exe = str(sys.executable)
    home = str(root)
    logs = log_dir(root)
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        prog_args = [exe, "service", "run"]
    else:
        prog_args = [exe, "-m", "mailkit", "service", "run"]
    if target == "launchd":
        return plistlib.dumps(
            {
                "Label": LABEL,
                "ProgramArguments": prog_args,
                "EnvironmentVariables": {"MAILKIT_HOME": home, "MAILKIT_ROLE": "daemon"},
                "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},
                "StandardOutPath": str(logs / "launchd.out.log"),
                "StandardErrorPath": str(logs / "launchd.err.log"),
            },
            fmt=plistlib.FMT_XML,
            sort_keys=False,
        ).decode("utf-8")
    exec_start = " ".join(shlex.quote(arg) for arg in prog_args)
    return f"""[Unit]
Description=Mailkit local email engine
After=network.target

[Service]
Type=simple
Environment=MAILKIT_HOME={shlex.quote(home)}
Environment=MAILKIT_ROLE=daemon
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
StandardOutput=append:{shlex.quote(str(logs / "daemon.out.log"))}
StandardError=append:{shlex.quote(str(logs / "daemon.err.log"))}

[Install]
WantedBy=default.target
"""


def unit_path(target: str, home: Path | None = None) -> Path:
    base = home or Path.home()
    if target == "launchd":
        return base / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    return base / ".config" / "systemd" / "user" / "mailkit.service"


def apply_os_service(
    root: Path,
    target: str | None = None,
    *,
    activate: bool = True,
    runner=subprocess.run,
    home: Path | None = None,
) -> dict:
    """Write the user unit and load it so the engine is independent of the UI."""
    if target is None:
        target = "launchd" if sys.platform == "darwin" else "systemd"
    path = unit_path(target, home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text(target, root), encoding="utf-8")
    loaded = _activate(target, path, runner=runner) if activate else False
    return {
        "target": target,
        "path": str(path),
        "label": LABEL,
        "loaded": loaded,
        "activate": activate,
    }


def _activate(target: str, path: Path, *, runner) -> bool:
    try:
        if target == "launchd":
            domain = f"gui/{os.getuid()}"
            spec = f"{domain}/{LABEL}"
            printed = runner(["launchctl", "print", spec], capture_output=True, text=True)
            if printed.returncode != 0:
                boot = runner(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
                err = (boot.stderr or "") + (boot.stdout or "")
                if boot.returncode != 0 and "already" not in err.lower():
                    return False
            return True
        runner(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
        enabled = runner(["systemctl", "--user", "enable", "--now", "mailkit.service"], capture_output=True, text=True)
        return enabled.returncode == 0
    except OSError:
        return False
