"""Desktop shell. The window is a client of the local API — no mail logic lives here."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from mailkit.config import load_config
from mailkit.logutil import get_logger
from mailkit.paths import data_dir
from mailkit.service import is_running, load_or_create_token, spawn_background

log = get_logger("mailkit.desktop")


def desktop_dir() -> Path:
    here = Path(__file__).resolve()
    frozen = getattr(sys, "_MEIPASS", None)
    candidates = []
    if frozen:
        candidates.append(Path(frozen) / "desktop")
    candidates.extend(
        [
            here.parent / "ui",
            here.parents[2] / "desktop" if len(here.parents) > 2 else here / "desktop",
            Path.cwd() / "desktop",
        ]
    )
    for path in candidates:
        if (path / "index.html").exists():
            return path
    raise SystemExit("Desktop UI not found. Expected desktop/index.html in the repo or app bundle.")


def ensure_engine(root: Path | None = None) -> None:
    home = data_dir(root)
    if is_running(home):
        return
    log.info("starting engine for desktop")
    spawn_background(home)
    deadline = time.time() + 8
    while time.time() < deadline:
        if is_running(home):
            return
        time.sleep(0.15)


def run_desktop(root: Path | None = None) -> int:
    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "Desktop extra missing. Install with: pip install 'mailkit[desktop]'"
        ) from exc

    home = data_dir(root)
    ensure_engine(home)
    cfg = load_config(home)
    token = load_or_create_token(home)
    index = desktop_dir() / "index.html"
    if not index.exists():
        raise SystemExit(f"Desktop UI not found at {index}")

    url = index.resolve().as_uri()
    window = webview.create_window(
        "Mailkit",
        url,
        width=1280,
        height=820,
        min_size=(720, 520),
        background_color="#2A1216",
    )

    def inject():
        time.sleep(0.4)
        origin = f"http://{cfg.daemon.host}:{cfg.daemon.port}"
        js = f"window.mailkitDesktop && window.mailkitDesktop.setToken({token!r}, {origin!r})"
        try:
            window.evaluate_js(js)
        except Exception as exc:
            log.warning("could not inject API token: %s", exc)

    threading.Thread(target=inject, daemon=True).start()
    webview.start()
    return 0
