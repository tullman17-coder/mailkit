"""Local demo provider, seeded mailbox, and desktop UI served from the API."""

from pathlib import Path
from types import SimpleNamespace

from mailkit.api.http import App, _desktop_file_response, _is_desktop_path, serve_forever
from mailkit.api.routes import dispatch
from mailkit.config import AccountConfig, load_config
from mailkit.demo import DEMO_ACCOUNT_ID, seed_demo
from mailkit.db import Store
from mailkit.doctor import DoctorContext, check_endpoints
from mailkit.events import EventBus
from mailkit.plugins.registry import load_plugins
from mailkit.providers.local import LocalPlugin, LocalProvider
from mailkit.runtime import Runtime
from mailkit.vault import Vault
from mailkit.watchers.local import LocalWatcher


def test_local_plugin_is_selected_for_local_accounts():
    registry = load_plugins()
    acc = AccountConfig(id="demo", address="you@mailkit.local", provider="local")
    plugin = registry.provider_for(acc)
    assert isinstance(plugin, LocalPlugin)
    watcher = registry.watcher_for("local")
    assert isinstance(watcher, LocalWatcher)
    assert watcher.supports(acc, plugin.create(acc, {}, store=None))


def test_seed_demo_lists_and_flags(tmp_path: Path):
    acc = seed_demo(tmp_path)
    assert acc.id == DEMO_ACCOUNT_ID
    cfg = load_config(tmp_path)
    assert cfg.accounts[DEMO_ACCOUNT_ID].provider == "local"
    vault = Vault(tmp_path)
    assert vault.get_account(DEMO_ACCOUNT_ID).get("password") == "demo"

    store = Store(tmp_path)
    provider = LocalProvider(acc, {}, store=store)
    boxes = {b.role: b.name for b in provider.list_mailboxes()}
    assert boxes["inbox"] == "INBOX"
    assert boxes["archives"] == "Archive"
    inbox = provider.list_messages("INBOX")
    assert len(inbox) == 3
    starred = provider.list_messages("INBOX", flagged=True)
    assert any(m.flagged for m in starred)
    welcome = next(m for m in inbox if m.native_id == "1")
    fetched = provider.get_message("INBOX", "1")
    assert fetched.body_text and "local demo" in fetched.body_text.lower()
    provider.set_flags("INBOX", "1", add=["Flagged"])
    again = store.get_message(welcome.id)
    assert again["flagged"] is True


def test_local_move_and_send(tmp_path: Path):
    acc = seed_demo(tmp_path)
    store = Store(tmp_path)
    provider = LocalProvider(acc, {}, store=store)
    provider.move("INBOX", "3", "Archive")
    archived = store.find_by_native(acc.id, "3")
    assert archived["mailbox"] == "Archive"
    mid = provider.send(acc.address, ["alex@zermo.org"], b"Subject: Hello\r\n\r\nHi")
    assert mid
    sent = provider.list_messages("Sent")
    assert any(m.subject == "Hello" for m in sent)


def test_http_lists_seeded_demo_and_serves_ui(tmp_path: Path):
    acc = seed_demo(tmp_path)
    store = Store(tmp_path)
    cfg = load_config(tmp_path)
    runtime = Runtime(root=tmp_path, config=cfg, vault=Vault(tmp_path), store=store, plugins=load_plugins(tmp_path))
    app = App(
        runtime=runtime,
        bus=EventBus(store),
        supervisor=SimpleNamespace(status=lambda: []),
        webhooks=None,
        token="tok",
        started_at="now",
    )
    listed = dispatch(app, "GET", "/v1/accounts", {}, {}, None)
    assert listed["ok"] is True
    assert listed["data"][0]["id"] == acc.id
    messages = dispatch(app, "GET", "/v1/messages", {"account": [acc.id], "mailbox": ["inbox"]}, {}, None)
    assert messages["ok"] is True
    assert len(messages["data"]) >= 3
    saved = dispatch(app, "GET", "/v1/messages", {"account": [acc.id], "mailbox": ["saved"]}, {}, None)
    assert any(row.get("flagged") for row in saved["data"])
    msg = messages["data"][0]
    body = dispatch(
        app,
        "GET",
        f"/v1/messages/{msg['id']}",
        {"account": [acc.id], "mailbox": ["INBOX"], "body": ["1"]},
        {},
        None,
    )
    assert body["data"].get("body_text")
    flagged = dispatch(app, "POST", f"/v1/messages/{msg['id']}/flag", {}, {}, None)
    assert flagged["ok"] is True
    moved = dispatch(app, "POST", f"/v1/messages/{msg['id']}/move", {}, {"mailbox": "archives"}, None)
    assert moved["ok"] is True
    assert moved["data"]["mailbox"] == "Archive"

    assert _is_desktop_path("/")
    assert _is_desktop_path("/js/app.js")
    html = _desktop_file_response("/")
    assert html.status == 200
    assert b"Mailkit" in html.body
    js = _desktop_file_response("/js/app.js")
    assert js.status == 200
    assert b"unflag" in js.body
    assert _desktop_file_response("/js/../config.toml").status == 404


def test_doctor_skips_imap_host_for_local_accounts(tmp_path: Path):
    seed_demo(tmp_path)
    finding = check_endpoints(DoctorContext(root=tmp_path))
    assert finding.status == "pass"


def test_http_handler_serves_ui_without_token(tmp_path: Path):
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
        token="secret",
        started_at="now",
    )
    server = serve_forever(app, "127.0.0.1", 0)
    try:
        import http.client

        host, port = server.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert b"Mailkit" in body
        conn.request("GET", "/js/app.js")
        js = conn.getresponse()
        assert js.status == 200
        assert b"unflag" in js.read()
        conn.close()
    finally:
        server.shutdown()
