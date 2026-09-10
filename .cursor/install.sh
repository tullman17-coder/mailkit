#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for Mailkit.
# Creates an isolated virtualenv and installs the package with dev extras.
set -euo pipefail

cd "$(dirname "$0")/.."

# The default image ships Python 3.12 but not the venv/ensurepip module, and
# its interpreter is PEP 668 "externally managed", so a venv is required.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3-venv
fi

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"

# Desktop UI support (optional client). Mailkit's window is a pywebview shell.
# On Linux we use the Qt/QtWebEngine backend (self-contained wheels) plus the
# X libraries QtWebEngine's xcb platform plugin dynamically loads. macOS uses
# the system WebKit and needs none of this.
.venv/bin/python -m pip install -e ".[desktop]"
.venv/bin/python -m pip install qtpy PyQt5 PyQtWebEngine
sudo apt-get install -y \
  libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
  libxcb-xinerama0 libxcb-xkb1 libxkbcommon-x11-0 libxcb-cursor0

echo "mailkit install complete: $(.venv/bin/mailkit --version)"
