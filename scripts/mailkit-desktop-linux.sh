#!/usr/bin/env bash
# Launch the Mailkit desktop window on Linux (Qt/QtWebEngine backend).
#
# The UI is loaded from a file:// URL and talks to the local engine over HTTP.
# QtWebEngine (Chromium) blocks file://-origin fetches at the browser layer, so
# we relax that here; on macOS the native WebKit shell needs none of this.
#
# Usage: scripts/mailkit-desktop-linux.sh   (starts the engine if not running)
set -euo pipefail

cd "$(dirname "$0")/.."

export DISPLAY="${DISPLAY:-:1}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export QTWEBENGINE_DISABLE_SANDBOX="${QTWEBENGINE_DISABLE_SANDBOX:-1}"
export QTWEBENGINE_CHROMIUM_FLAGS="${QTWEBENGINE_CHROMIUM_FLAGS:---disable-web-security --allow-file-access-from-files}"

exec .venv/bin/mailkit desktop "$@"
