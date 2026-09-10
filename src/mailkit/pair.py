"""Pairing payload for native phone apps and LAN agents.

The engine stays on this machine. Phones and agents connect to it; they do not
host mail. ``allow_remote`` must be on before a non-loopback bind is used.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from urllib.parse import urlencode

from mailkit.config import load_config, save_config
from mailkit.service import is_running, load_or_create_token, spawn_background, stop_daemon


SCHEMA = "mailkit.pair.v1"
DEEPLINK_SCHEME = "mailkit"


def lan_ipv4() -> list[str]:
    """Best-effort LAN IPv4 addresses, default-route first."""
    found: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.connect(("8.8.8.8", 80))
        route = probe.getsockname()[0]
        probe.close()
        if _publicish(route):
            found.append(route)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _publicish(ip) and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found


def _publicish(ip: str) -> bool:
    if not ip or ip.startswith("127.") or ip.startswith("0."):
        return False
    if ip.startswith("169.254."):
        return False
    return True


def resolve_bind_host(cfg, *, env: dict[str, str] | None = None) -> tuple[str, bool]:
    env = env if env is not None else os.environ
    host = (env.get("MAILKIT_HOST") or cfg.daemon.host or "127.0.0.1").strip()
    allow = bool(cfg.daemon.allow_remote)
    flag = (env.get("MAILKIT_ALLOW_REMOTE") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        allow = True
    if flag in {"0", "false", "no", "off"}:
        allow = False
    if allow is False and host not in {"127.0.0.1", "localhost", "::1"}:
        host = "127.0.0.1"
    return host, allow


def set_lan_bind(root: Path, *, enabled: bool) -> None:
    cfg = load_config(root)
    if enabled:
        cfg.daemon.host = "0.0.0.0"
        cfg.daemon.allow_remote = True
    else:
        cfg.daemon.host = "127.0.0.1"
        cfg.daemon.allow_remote = False
    save_config(cfg, root)


def restart_engine(root: Path) -> int | None:
    if is_running(root):
        stop_daemon(root)
    return spawn_background(root)


def pair_urls(cfg, *, env: dict[str, str] | None = None) -> dict[str, str | list[str]]:
    host, allow = resolve_bind_host(cfg, env=env)
    port = int(cfg.daemon.port)
    loopback = f"http://127.0.0.1:{port}"
    urls = [loopback]
    primary = loopback
    if allow:
        for ip in lan_ipv4():
            url = f"http://{ip}:{port}"
            if url not in urls:
                urls.append(url)
        if host not in {"0.0.0.0", "::", "127.0.0.1", "localhost", "::1"}:
            bound = f"http://{host}:{port}"
            if bound not in urls:
                urls.append(bound)
        lan = [u for u in urls if "127.0.0.1" not in u]
        if lan:
            primary = lan[0]
    return {"url": primary, "loopback_url": loopback, "urls": urls, "host": host, "allow_remote": allow}


def build_pair_payload(root: Path | None = None, *, token: str | None = None) -> dict:
    cfg = load_config(root)
    tok = token if token is not None else load_or_create_token(root)
    urls = pair_urls(cfg)
    primary = str(urls["url"])
    deeplink = f"{DEEPLINK_SCHEME}://connect?{urlencode({'url': primary, 'token': tok})}"
    return {
        "schema": SCHEMA,
        "url": primary,
        "loopback_url": urls["loopback_url"],
        "urls": urls["urls"],
        "token": tok,
        "deeplink": deeplink,
        "allow_remote": bool(urls["allow_remote"]),
        "host": urls["host"],
        "port": int(cfg.daemon.port),
        "shell": "mobile",
        "cli": "mailkit -o json messages list --mailbox inbox",
    }


def apply_pair_mode(root: Path, *, lan: bool = False, off: bool = False, start: bool = True) -> dict:
    if lan and off:
        raise ValueError("use --lan or --off, not both")
    restarted = False
    if lan or off:
        set_lan_bind(root, enabled=lan)
        if start:
            restart_engine(root)
            restarted = True
    elif start and not is_running(root):
        spawn_background(root)
    payload = build_pair_payload(root)
    payload["restarted"] = restarted
    payload["running"] = is_running(root)
    return payload
