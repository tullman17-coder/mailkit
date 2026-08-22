"""Optional OS service units. The engine itself has no OS-specific core."""

from __future__ import annotations

import sys
from pathlib import Path


def unit_text(target: str, root: Path) -> str:
    exe = sys.executable
    home = root
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
  <key>Label</key><string>dev.mailkit.daemon</string>
  <key>ProgramArguments</key>
  <array>
{prog_args}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>MAILKIT_HOME</key><string>{home}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
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
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
