"""Local versioned HTTP, SSE, WebSocket, and Unix event socket API."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from mailkit.events import EventBus
from mailkit.logutil import get_logger
from mailkit.models import EventFilter, fail, ok
from mailkit.runtime import Runtime
from mailkit.supervisor import Supervisor
from mailkit.webhooks import WebhookDispatcher
from mailkit.watchers.graph_push import GRAPH_HOOK_PATH

log = get_logger("mailkit.api")

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
UI_ROOT_PATHS = {"/", "/index.html", "/favicon.ico"}
UI_PREFIXES = ("/css/", "/js/", "/brand/")

# Desktop loads index.html as a file:// URI and fetches this API with
# Authorization. Browsers require these headers on the actual GET/POST/SSE
# response, not only on the OPTIONS preflight.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,DELETE,PATCH,OPTIONS",
}


@dataclass
class App:
    runtime: Runtime
    bus: EventBus
    supervisor: Supervisor
    webhooks: WebhookDispatcher
    token: str
    started_at: str


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
        return token == self.app.token

    def do_GET(self):  # noqa: N802
        self._dispatch()

    def do_POST(self):  # noqa: N802
        self._dispatch()

    def do_DELETE(self):  # noqa: N802
        self._dispatch()

    def do_PATCH(self):  # noqa: N802
        self._dispatch()

    def _send_cors_headers(self) -> None:
        for name, value in CORS_HEADERS.items():
            self.send_header(name, value)

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        if self.command == "GET" and _is_desktop_path(parsed.path):
            resp = _desktop_file_response(parsed.path)
            self._write(resp)
            return
        if parsed.path in {"/v1/events/ws", "/v1/ws"}:
            if not self._check_auth() and parse_qs(parsed.query).get("token", [""])[0] != self.app.token:
                self._unauthorized()
                return
            self._websocket(parsed)
            return
        # Graph cannot attach the daemon bearer for subscription validation or
        # change notifications, so the hook must stay reachable unauthenticated.
        if not self._check_auth() and parsed.path not in {"/v1/health", GRAPH_HOOK_PATH}:
            self._unauthorized()
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            self._write(json_response(fail({"code": "usage", "message": "invalid JSON"}), 400))
            return
        from mailkit.api.routes import dispatch

        try:
            result = dispatch(self.app, self.command, parsed.path, parse_qs(parsed.query), payload, self)
        except Exception as exc:
            from mailkit.errors import MailkitError

            if isinstance(exc, MailkitError):
                self._write(json_response(fail(exc.to_dict()), exc.exit_code if exc.exit_code >= 400 else 400))
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
        self._send_cors_headers()
        self.end_headers()
        if resp.body:
            self.wfile.write(resp.body)

    def _sse(self, resp: Response) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._send_cors_headers()
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


def _is_desktop_path(path: str) -> bool:
    if path in UI_ROOT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in UI_PREFIXES)


def _desktop_file_response(url_path: str) -> Response:
    from mailkit.desktop import desktop_dir

    relative = "index.html" if url_path in {"/", "/index.html"} else unquote(url_path).lstrip("/")
    if url_path == "/favicon.ico":
        relative = "brand/mark.svg"
    root = desktop_dir().resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return Response(status=404, body=b"not found", headers={"Content-Type": "text/plain"})
    if not target.is_file():
        return Response(status=404, body=b"not found", headers={"Content-Type": "text/plain"})
    data = target.read_bytes()
    mime, _ = mimetypes.guess_type(str(target))
    if target.suffix == ".js":
        mime = "application/javascript"
    return Response(
        status=200,
        body=data,
        headers={
            "Content-Type": mime or "application/octet-stream",
            "Content-Length": str(len(data)),
            "Cache-Control": "no-cache",
        },
    )


def serve_forever(app: App, host: str, port: int, *, stop: threading.Event | None = None) -> ThreadingHTTPServer:
    Handler.app = app
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="mailkit-http", daemon=True)
    thread.start()
    log.info("API listening on http://%s:%s/v1 (desktop UI at /)", host, port)
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
