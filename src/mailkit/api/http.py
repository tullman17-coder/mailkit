"""Local versioned HTTP, SSE, WebSocket, and Unix event socket API."""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from mailkit.events import EventBus
from mailkit.logutil import get_logger
from mailkit.models import EventFilter, fail, ok
from mailkit.runtime import Runtime
from mailkit.oauth_flow import NativeOAuthSessions
from mailkit.supervisor import Supervisor
from mailkit.webhooks import WebhookDispatcher

log = get_logger("mailkit.api")

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


@dataclass
class App:
    runtime: Runtime
    bus: EventBus
    supervisor: Supervisor
    webhooks: WebhookDispatcher
    token: str
    started_at: str
    oauth_sessions: NativeOAuthSessions = field(default_factory=NativeOAuthSessions)
    account_lock: Any = field(default_factory=threading.RLock)


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] | None = None
    stream: Callable | None = None


def json_response(payload: Any, status: int = 200) -> Response:
    body = json.dumps(payload, default=str).encode("utf-8")
    return Response(
        status=status,
        body=body,
        headers={"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(body))},
    )


class Handler(BaseHTTPRequestHandler):
    app: App

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _unauthorized(self) -> None:
        self._write(json_response(fail({"code": "auth", "message": "invalid token"}), 401))

    def _check_auth(self) -> bool:
        header = self.headers.get("Authorization") or ""
        token = ""
        if header.startswith("Bearer "):
            token = header[7:].strip()
        if not token:
            token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        # Loopback is still token-gated so other local users cannot call the API.
        return bool(token and self.app.token) and hmac.compare_digest(token.encode(), self.app.token.encode())

    def do_GET(self):  # noqa: N802
        self._dispatch()

    def do_POST(self):  # noqa: N802
        self._dispatch()

    def do_DELETE(self):  # noqa: N802
        self._dispatch()

    def do_PATCH(self):  # noqa: N802
        self._dispatch()

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,PATCH,OPTIONS")
        self.end_headers()

    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/v1/events/ws", "/v1/ws"}:
            if not self._check_auth():
                self._unauthorized()
                return
            self._websocket(parsed)
            return
        if not self._check_auth() and parsed.path not in {"/v1/health"}:
            self._unauthorized()
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > 1048576:
                raise ValueError
        except ValueError:
            self._write(json_response(fail({"code": "usage", "message": "invalid or oversized request body"}), 400))
            return
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self._write(json_response(fail({"code": "usage", "message": "invalid JSON"}), 400))
            return
        from mailkit.api.routes import dispatch

        try:
            result = dispatch(self.app, self.command, parsed.path, parse_qs(parsed.query), payload, self)
        except Exception as exc:
            from mailkit.errors import MailkitError

            if isinstance(exc, MailkitError):
                status = {"not_found": 404, "conflict": 409, "auth": 401, "network": 502}.get(exc.code, 400)
                self._write(json_response(fail(exc.to_dict()), status))
                return
            log.exception("api error")
            self._write(json_response(fail({"code": "error", "message": str(exc)}), 500))
            return
        if isinstance(result, Response):
            if result.stream:
                self._sse(result)
                return
            self._write(result)
            return
        self._write(json_response(result))

    def _write(self, resp: Response) -> None:
        self.send_response(resp.status)
        headers = resp.headers or {"Content-Type": "application/json"}
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if resp.body:
            self.wfile.write(resp.body)

    def _sse(self, resp: Response) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            resp.stream(self.wfile)
        except BrokenPipeError:
            return

    def _websocket(self, parsed) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "Missing Sec-WebSocket-Key")
            return
        accept = hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
        import base64

        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", base64.b64encode(accept).decode("ascii"))
        self.end_headers()
        qs = parse_qs(parsed.query)
        filt = EventFilter.from_dict({k: v[0] if len(v) == 1 else v for k, v in qs.items() if k != "token"})
        cursor = qs.get("cursor", [None])[0]
        stop = threading.Event()
        q = self.app.bus.subscribe_live(filt)
        sock = self.connection
        try:
            for ev in self.app.bus.replay(filt, cursor, limit=200):
                _ws_send(sock, json.dumps(ev))
            while not stop.is_set():
                sock.settimeout(1.0)
                try:
                    frame = _ws_recv(sock)
                except TimeoutError:
                    frame = None
                except Exception:
                    break
                if frame == "":
                    break
                if frame:
                    try:
                        msg = json.loads(frame)
                    except json.JSONDecodeError:
                        msg = {}
                    op = msg.get("op")
                    if op == "ack" and msg.get("subscription_id") and msg.get("event_id"):
                        self.app.runtime.store.ack(msg["subscription_id"], msg["event_id"])
                    if op == "subscribe":
                        filt = EventFilter.from_dict(msg.get("filter") or {})
                    if op == "ping":
                        _ws_send(sock, json.dumps({"op": "pong"}))
                try:
                    ev = q.get_nowait()
                    _ws_send(sock, json.dumps(ev))
                except Exception:
                    pass
        finally:
            self.app.bus.unsubscribe_live(q)


def _ws_send(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray([0x81])
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header.extend(n.to_bytes(2, "big"))
    else:
        header.append(127)
        header.extend(n.to_bytes(8, "big"))
    sock.sendall(header + payload)


def _ws_recv(sock: socket.socket) -> str | None:
    hdr = sock.recv(2)
    if not hdr:
        return ""
    opcode = hdr[0] & 0x0F
    masked = hdr[1] & 0x80
    length = hdr[1] & 0x7F
    if length == 126:
        length = int.from_bytes(sock.recv(2), "big")
    elif length == 127:
        length = int.from_bytes(sock.recv(8), "big")
    mask = sock.recv(4) if masked else b""
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            return ""
        data += chunk
    if masked:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    if opcode == 0x8:
        return ""
    if opcode == 0x9:
        # ping
        sock.sendall(b"\x8A" + bytes([len(data)]) + data)
        return None
    return data.decode("utf-8", "replace")


def serve_forever(app: App, host: str, port: int, *, stop: threading.Event | None = None) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        from mailkit.errors import ConfigError

        raise ConfigError("The API must bind to loopback; use an HTTPS reverse proxy for remote devices")
    Handler.app = app
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="mailkit-http", daemon=True)
    thread.start()
    log.info("API listening on http://%s:%s/v1", host, port)
    if stop:
        def _watch():
            stop.wait()
            server.shutdown()

        threading.Thread(target=_watch, daemon=True).start()
    return server


def serve_event_socket(app: App, path, stop: threading.Event) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.listen(16)
    sock.settimeout(0.5)

    def client(conn):
        q = app.bus.subscribe_live(EventFilter())
        try:
            while not stop.is_set():
                try:
                    ev = q.get(timeout=0.5)
                except Exception:
                    continue
                try:
                    conn.sendall((json.dumps(ev) + "\n").encode("utf-8"))
                except OSError:
                    break
        finally:
            app.bus.unsubscribe_live(q)
            conn.close()

    def loop():
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=client, args=(conn,), daemon=True).start()
        sock.close()

    threading.Thread(target=loop, name="mailkit-eventsock", daemon=True).start()
