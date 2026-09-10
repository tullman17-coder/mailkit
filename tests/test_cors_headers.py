"""Desktop loads as file:// and fetches the loopback API with Authorization.

CORS requires Access-Control-Allow-Origin on the actual GET/POST (and SSE),
not only on the OPTIONS preflight that already advertised it.
"""

from __future__ import annotations

import http.client
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from mailkit.api.http import App, serve_forever
from mailkit.db import Store
from mailkit.events import EventBus


TOKEN = "desktop-secret"


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
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _base(server) -> tuple[str, int]:
    host, port = server.server_address[:2]
    return host, port


def _url(server, path: str) -> str:
    host, port = _base(server)
    return f"http://{host}:{port}{path}"


def _open(url: str, *, method: str = "GET", data: bytes | None = None, headers: dict | None = None):
    req = Request(url, data=data, headers=headers or {}, method=method)
    return urlopen(req, timeout=3)


def _assert_acao(headers) -> None:
    assert headers.get("Access-Control-Allow-Origin") == "*"
    assert "Authorization" in (headers.get("Access-Control-Allow-Headers") or "")


def test_options_preflight_advertises_cors(api):
    with _open(_url(api, "/v1/status"), method="OPTIONS") as resp:
        assert resp.status == 204
        _assert_acao(resp.headers)


def test_get_includes_acao(api):
    with _open(_url(api, "/v1/health")) as resp:
        assert resp.status == 200
        _assert_acao(resp.headers)

    with _open(
        _url(api, "/v1/status"),
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as resp:
        assert resp.status == 200
        _assert_acao(resp.headers)


def test_post_includes_acao(api):
    with _open(
        _url(api, "/v1/discover"),
        method="POST",
        data=b'{"address":"user@gmail.com"}',
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    ) as resp:
        assert resp.status == 200
        _assert_acao(resp.headers)


def test_unauthorized_and_sse_include_acao(api):
    with pytest.raises(HTTPError) as caught:
        _open(_url(api, "/v1/status"))
    assert caught.value.code == 401
    _assert_acao(caught.value.headers)

    host, port = _base(api)
    conn = http.client.HTTPConnection(host, port, timeout=3)
    try:
        conn.request(
            "GET",
            "/v1/events/stream",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "text/event-stream"
        assert resp.getheader("Access-Control-Allow-Origin") == "*"
        assert "Authorization" in (resp.getheader("Access-Control-Allow-Headers") or "")
    finally:
        conn.close()
