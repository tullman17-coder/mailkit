import importlib

from mailkit.cli.client import ApiClient
from mailkit.cli.main import build_parser, main
from mailkit.cli.schemas import dump_schema, write_schema_files

cli_main = importlib.import_module("mailkit.cli.main")
oauth_flow = importlib.import_module("mailkit.oauth_flow")


def test_help_lists_required_commands():
    parser = build_parser()
    help_text = parser.format_help()
    for token in (
        "accounts",
        "messages",
        "send",
        "reply",
        "watch",
        "events",
        "webhooks",
        "service",
        "rules",
        "subscriptions",
        "doctor",
        "desktop",
    ):
        assert token in help_text


def test_schema_event_is_stable():
    schema = dump_schema("event")
    assert schema["$id"] == "mailkit.event.v1"
    required = set(schema["required"])
    assert {"id", "ts", "account_id", "provider_id", "type", "schema"} <= required


def test_json_schema_command(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILKIT_HOME", str(tmp_path))
    assert main(["-o", "json", "schema", "event"]) == 0
    assert main(["schema", "event", "-o", "json"]) == 0


def test_write_schema_files(tmp_path):
    write_schema_files(tmp_path)
    assert (tmp_path / "event.json").exists()


_OAUTH_TOKENS = {
    "access_token": "at-test",
    "refresh_token": "rt-test",
    "token_expiry": 1,
}

_OAUTH_ADD_ARGV = [
    "accounts",
    "add",
    "--address",
    "you@gmail.com",
    "--auth",
    "oauth2",
    "--client-id",
    "cid",
    "--no-discover",
    "--imap-host",
    "imap.gmail.com",
    "--smtp-host",
    "smtp.gmail.com",
]


def test_oauth2_add_posts_accounts_when_daemon_running(tmp_path, monkeypatch):
    """OAuth add must tell a running daemon to start the watcher, like password add."""
    calls = []

    monkeypatch.setattr(cli_main, "is_running", lambda root: True)
    monkeypatch.setattr(oauth_flow, "run_local_oauth", lambda acc, secrets, **kwargs: dict(_OAUTH_TOKENS))

    def capture_request(self, method, path, *, query=None, body=None):
        calls.append({"method": method, "path": path, "body": body})
        return {"data": {"id": (body or {}).get("id") or "you"}}

    monkeypatch.setattr(ApiClient, "request", capture_request)

    rc = main(["--home", str(tmp_path), *_OAUTH_ADD_ARGV])
    assert rc == 0
    posts = [c for c in calls if c["method"] == "POST" and c["path"] == "/v1/accounts"]
    assert len(posts) == 1
    assert posts[0]["body"]["auth"] == "oauth2"
    assert posts[0]["body"]["access_token"] == "at-test"
    assert posts[0]["body"]["refresh_token"] == "rt-test"


def test_oauth2_add_skips_daemon_post_when_stopped(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setattr(cli_main, "is_running", lambda root: False)
    monkeypatch.setattr(oauth_flow, "run_local_oauth", lambda acc, secrets, **kwargs: dict(_OAUTH_TOKENS))
    monkeypatch.setattr(
        ApiClient,
        "request",
        lambda self, method, path, *, query=None, body=None: calls.append((method, path)) or {},
    )

    rc = main(["--home", str(tmp_path), *_OAUTH_ADD_ARGV])
    assert rc == 0
    assert calls == []
