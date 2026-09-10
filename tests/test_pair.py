"""Pairing payload, LAN bind, and native client contract."""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
import importlib
import json

from mailkit.api.http import App
from mailkit.api.routes import dispatch
from mailkit.config import load_config
from mailkit.demo import seed_demo
from mailkit.events import EventBus
from mailkit.db import Store
from mailkit.pair import apply_pair_mode, build_pair_payload, lan_ipv4, resolve_bind_host, set_lan_bind
from mailkit.cli.main import build_parser, main
from mailkit.cli.schemas import dump_schema
from mailkit.plugins.registry import load_plugins
from mailkit.runtime import Runtime
from mailkit.vault import Vault

cli_mod = importlib.import_module("mailkit.cli.main")


def test_pair_schema_is_stable():
    schema = dump_schema("pair")
    assert schema["$id"] == "mailkit.pair.v1"
    assert {"schema", "url", "token", "deeplink"} <= set(schema["required"])


def test_build_pair_payload_loopback(tmp_path: Path):
    payload = build_pair_payload(tmp_path, token="secret-token")
    assert payload["schema"] == "mailkit.pair.v1"
    assert payload["url"] == "http://127.0.0.1:8765"
    assert payload["loopback_url"] == "http://127.0.0.1:8765"
    assert payload["token"] == "secret-token"
    assert payload["allow_remote"] is False
    parsed = urlparse(payload["deeplink"])
    assert parsed.scheme == "mailkit"
    assert parsed.hostname == "connect"
    query = parse_qs(parsed.query)
    assert query["url"] == ["http://127.0.0.1:8765"]
    assert query["token"] == ["secret-token"]


def test_lan_bind_unlocks_non_loopback_url(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("mailkit.pair.lan_ipv4", lambda: ["192.168.9.40"])
    set_lan_bind(tmp_path, enabled=True)
    cfg = load_config(tmp_path)
    assert cfg.daemon.host == "0.0.0.0"
    assert cfg.daemon.allow_remote is True
    payload = build_pair_payload(tmp_path, token="tok")
    assert payload["allow_remote"] is True
    assert payload["url"] == "http://192.168.9.40:8765"
    assert "http://127.0.0.1:8765" in payload["urls"]


def test_env_allow_remote_without_persisting(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MAILKIT_HOST", "0.0.0.0")
    monkeypatch.setenv("MAILKIT_ALLOW_REMOTE", "1")
    cfg = load_config(tmp_path)
    host, allow = resolve_bind_host(cfg)
    assert host == "0.0.0.0"
    assert allow is True
    monkeypatch.setenv("MAILKIT_ALLOW_REMOTE", "0")
    monkeypatch.setenv("MAILKIT_HOST", "0.0.0.0")
    host, allow = resolve_bind_host(cfg)
    assert allow is False
    assert host == "127.0.0.1"


def test_pair_off_restores_loopback(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("mailkit.pair.is_running", lambda root: False)
    monkeypatch.setattr("mailkit.pair.spawn_background", lambda root: 11)
    monkeypatch.setattr("mailkit.pair.stop_daemon", lambda root: None)
    set_lan_bind(tmp_path, enabled=True)
    payload = apply_pair_mode(tmp_path, off=True, start=False)
    cfg = load_config(tmp_path)
    assert cfg.daemon.host == "127.0.0.1"
    assert cfg.daemon.allow_remote is False
    assert payload["url"] == "http://127.0.0.1:8765"


def test_cli_pair_json(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr("mailkit.pair.is_running", lambda root: True)
    monkeypatch.setattr("mailkit.pair.spawn_background", lambda root: (_ for _ in ()).throw(AssertionError("should not spawn")))
    rc = main(["--home", str(tmp_path), "-o", "json", "pair", "--no-start"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["schema"] == "mailkit.pair.v1"
    assert payload["data"]["deeplink"].startswith("mailkit://connect?")


def test_help_lists_pair_and_desktop():
    help_text = build_parser().format_help()
    assert "pair" in help_text
    assert "desktop" in help_text
    assert "AI agents" in help_text or "agents" in help_text


def test_http_pair_route(tmp_path: Path):
    seed_demo(tmp_path)
    store = Store(tmp_path)
    app = App(
        runtime=Runtime(
            root=tmp_path,
            config=load_config(tmp_path),
            vault=Vault(tmp_path),
            store=store,
            plugins=load_plugins(tmp_path),
        ),
        bus=EventBus(store),
        supervisor=SimpleNamespace(status=lambda: []),
        webhooks=None,
        token="pair-token",
        started_at="now",
    )
    result = dispatch(app, "GET", "/v1/pair", {}, {}, None)
    assert result["ok"] is True
    data = result["data"]
    assert data["token"] == "pair-token"
    assert data["schema"] == "mailkit.pair.v1"
    assert data["deeplink"].startswith("mailkit://connect?")


def test_demo_text_points_at_native_clients(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli_mod, "is_running", lambda root: True)
    monkeypatch.setattr(cli_mod, "spawn_background", lambda root: 99)
    monkeypatch.setattr(cli_mod, "read_pid", lambda root: 99)
    rc = main(["--home", str(tmp_path), "demo"])
    assert rc == 0
    text = capsys.readouterr().out
    assert "mailkit desktop" in text
    assert "mailkit -o json" in text
    assert "open http://" not in text


def test_cli_heads_include_pair_and_demo():
    from mailkit.service import _CLI_HEADS, frozen_dispatch_argv

    assert "pair" in _CLI_HEADS
    assert "demo" in _CLI_HEADS
    assert frozen_dispatch_argv(["pair", "--lan"]) == ["pair", "--lan"]
    assert frozen_dispatch_argv(["demo"], spawn_gen=1) == ["service", "run", "--background-child"]
    assert frozen_dispatch_argv(["pair"], spawn_gen=1) == ["service", "run", "--background-child"]


def test_lan_ipv4_filters_loopback():
    for ip in lan_ipv4():
        assert not ip.startswith("127.")
