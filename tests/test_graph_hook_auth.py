"""Graph webhook handshake must be reachable without the daemon bearer token."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from mailkit.api.http import App, serve_forever
from mailkit.db import Store
from mailkit.events import EventBus


TOKEN = "daemon-secret"


@pytest.fixture
def api(tmp_path):
    store = Store(tmp_path)
    bus = EventBus(store)
    app = App(
        runtime=SimpleNamespace(store=store),
        bus=bus,
        supervisor=Mock(status=Mock(return_value={})),
        webhooks=Mock(),
        token=TOKEN,
        started_at="2026-01-01T00:00:00Z",
    )
    server = serve_forever(app, "127.0.0.1", 0)
    try:
        yield server, app
    finally:
        server.shutdown()
        server.server_close()


def _base(server) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}"


def _open(url: str, *, method: str = "GET", data: bytes | None = None, headers: dict | None = None):
    req = Request(url, data=data, headers=headers or {}, method=method)
    return urlopen(req, timeout=3)


def test_health_remains_public(api):
    server, _app = api
    with _open(f"{_base(server)}/v1/health") as resp:
        assert resp.status == 200
        payload = json.loads(resp.read())
        assert payload["ok"] is True


def test_status_still_requires_bearer(api):
    server, _app = api
    with pytest.raises(HTTPError) as caught:
        _open(f"{_base(server)}/v1/status")
    assert caught.value.code == 401
    body = json.loads(caught.value.read())
    assert body["error"]["code"] == "auth"

    with _open(
        f"{_base(server)}/v1/status",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as resp:
        assert resp.status == 200


def test_graph_validation_handshake_without_bearer(api):
    server, _app = api
    token = "GraphValidation Token+/=?"
    url = f"{_base(server)}/v1/provider-hooks/graph?validationToken={quote(token, safe='')}"
    with _open(url, method="POST", data=b"") as resp:
        assert resp.status == 200
        assert resp.headers.get_content_type() == "text/plain"
        assert resp.read().decode() == token
    with _open(url, method="GET") as resp:
        assert resp.status == 200
        assert resp.read().decode() == token


def test_graph_notifications_without_bearer(api):
    server, app = api
    note = {
        "subscriptionId": "sub-1",
        "clientState": "work",
        "changeType": "created",
        "resource": "me/messages/abc",
    }
    body = json.dumps({"value": [note]}).encode()
    with _open(
        f"{_base(server)}/v1/provider-hooks/graph",
        method="POST",
        data=body,
        headers={"Content-Type": "application/json"},
    ) as resp:
        assert resp.status == 200
        payload = json.loads(resp.read())
        assert payload["ok"] is True
        assert payload["data"]["accepted"] is True
    events = app.bus.replay(None, None, limit=10)
    assert len(events) == 1
    assert events[0]["provider_id"] == "graph"
    assert events[0]["data"]["graph_notification"]["subscriptionId"] == "sub-1"


def test_other_posts_still_require_bearer(api):
    server, _app = api
    with pytest.raises(HTTPError) as caught:
        _open(
            f"{_base(server)}/v1/send",
            method="POST",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
    assert caught.value.code == 401
