"""Optional OS service units. The engine itself has no OS-specific core."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mailkit.paths import log_dir


LABEL = "dev.mailkit.daemon"


def unit_text(target: str, root: Path) -> str:
    exe = sys.executable
    home = root
    logs = log_dir(root)
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        prog_args = f"""    <string>{exe}</string>
    <string>service</string>
    <string>run</string>"""
    else:
        prog_args = f"""    <string>{exe}</string>
    <string>-m</string>
    <string>mailkit</string>
    <string>service</string>
    <string>run</string>"""
    if target == "launchd":
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{prog_args}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>MAILKIT_HOME</key><string>{home}</string>
    <key>MAILKIT_ROLE</key><string>daemon</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{logs / "launchd.out.log"}</string>
  <key>StandardErrorPath</key><string>{logs / "launchd.err.log"}</string>
</dict>
</plist>
"""
    exec_start = f"{exe} service run" if frozen else f"{exe} -m mailkit service run"
    return f"""[Unit]
Description=Mailkit local email engine
After=network.target

[Service]
Type=simple
Environment=MAILKIT_HOME={home}
Environment=MAILKIT_ROLE=daemon
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
StandardOutput=append:{logs / "daemon.out.log"}
StandardError=append:{logs / "daemon.err.log"}

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
    text = unit_text(target, root)
    changed = not path.exists() or path.read_text(encoding="utf-8") != text
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    loaded = _activate(target, path, runner=runner, reload=changed) if activate else False
    return {
        "target": target,
        "path": str(path),
        "label": LABEL,
        "changed": changed,
        "loaded": loaded,
        "activate": activate,
    }


def _activate(target: str, path: Path, *, runner, reload: bool = False) -> bool:
    try:
        if target == "launchd":
            domain = f"gui/{os.getuid()}"
            spec = f"{domain}/{LABEL}"
            printed = runner(["launchctl", "print", spec], capture_output=True, text=True)
            if reload and printed.returncode == 0:
                removed = runner(["launchctl", "bootout", spec], capture_output=True, text=True)
                if removed.returncode != 0:
                    return False
            if printed.returncode != 0 or reload:
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
