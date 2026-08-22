"""Optional OS service units. The engine itself has no OS-specific core."""

from __future__ import annotations

import sys
from pathlib import Path


def unit_text(target: str, root: Path) -> str:
    exe = sys.executable
    home = root
    if target == "launchd":
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>dev.mailkit.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>{exe}</string>
    <string>-m</string>
    <string>mailkit</string>
    <string>service</string>
    <string>run</string>
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
    return f"""[Unit]
Description=Mailkit local email engine
After=network.target

[Service]
Type=simple
Environment=MAILKIT_HOME={home}
ExecStart={exe} -m mailkit service run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
