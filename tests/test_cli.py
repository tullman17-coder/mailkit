import io
import json
import urllib.error

import pytest

from mailkit.cli.client import ApiClient
from mailkit.cli.main import build_parser, main
from mailkit.cli.schemas import dump_schema, write_schema_files
from mailkit.config import AppConfig
from mailkit.errors import AuthError, DaemonError, ExitCode, NetworkError, NotFoundError


def _http_error(status: int, payload) -> urllib.error.HTTPError:
    if isinstance(payload, (dict, list)):
        raw = json.dumps(payload).encode()
    elif isinstance(payload, bytes):
        raw = payload
    else:
        raw = str(payload).encode()
    return urllib.error.HTTPError("http://127.0.0.1:8765/v1/x", status, "Error", hdrs=None, fp=io.BytesIO(raw))


def _api_error(code: str, message: str) -> dict:
    return {
        "ok": False,
        "schema": "mailkit.response.v1",
        "data": None,
        "error": {"schema": "mailkit.error.v1", "code": code, "message": message, "details": {}},
    }


def _client(tmp_path) -> ApiClient:
    return ApiClient(AppConfig(), tmp_path, token="test-token")


def _boom(exc):
    def _raise(*_args, **_kwargs):
        raise exc

    return _raise


def test_cli_client_maps_not_found_code_from_live_daemon(tmp_path, monkeypatch):
    # Daemon currently serializes NotFoundError as HTTP 400 with error.code not_found.
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _boom(_http_error(400, _api_error("not_found", "Unknown account: work"))),
    )
    with pytest.raises(NotFoundError) as caught:
        _client(tmp_path).request("GET", "/v1/messages/missing")
    assert caught.value.exit_code == ExitCode.NOT_FOUND


def test_cli_client_maps_http_404_to_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _boom(_http_error(404, "missing")))
    with pytest.raises(NotFoundError) as caught:
        _client(tmp_path).request("GET", "/v1/messages/missing")
    assert caught.value.exit_code == ExitCode.NOT_FOUND


def test_cli_client_maps_http_401_to_auth(tmp_path, monkeypatch):
    body = {"ok": False, "error": {"code": "auth", "message": "invalid token"}}
    monkeypatch.setattr("urllib.request.urlopen", _boom(_http_error(401, body)))
    with pytest.raises(AuthError) as caught:
        _client(tmp_path).request("GET", "/v1/status")
    assert caught.value.exit_code == ExitCode.AUTH


def test_cli_client_maps_http_500_to_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _boom(_http_error(500, _api_error("error", "internal"))))
    with pytest.raises(DaemonError) as caught:
        _client(tmp_path).request("GET", "/v1/status")
    assert caught.value.exit_code == ExitCode.DAEMON


def test_cli_client_maps_network_code(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _boom(_http_error(400, _api_error("network", "IMAP timed out"))),
    )
    with pytest.raises(NetworkError) as caught:
        _client(tmp_path).request("GET", "/v1/mailboxes")
    assert caught.value.exit_code == ExitCode.NETWORK


def test_cli_http_401_exit_code(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILKIT_HOME", str(tmp_path))
    monkeypatch.setattr("mailkit.cli.client.is_running", lambda _root: True)
    body = {"ok": False, "error": {"code": "auth", "message": "invalid token"}}
    monkeypatch.setattr("urllib.request.urlopen", _boom(_http_error(401, body)))
    assert main(["-o", "json", "messages", "get", "msg_1"]) == ExitCode.AUTH


def test_cli_http_not_found_exit_code(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILKIT_HOME", str(tmp_path))
    monkeypatch.setattr("mailkit.cli.client.is_running", lambda _root: True)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _boom(_http_error(400, _api_error("not_found", "Message not found"))),
    )
    assert main(["-o", "json", "messages", "get", "msg_1"]) == ExitCode.NOT_FOUND


def test_cli_daemon_not_running_still_exits_6(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILKIT_HOME", str(tmp_path))
    monkeypatch.setattr("mailkit.cli.client.is_running", lambda _root: False)
    assert main(["messages", "get", "msg_1"]) == ExitCode.DAEMON


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
