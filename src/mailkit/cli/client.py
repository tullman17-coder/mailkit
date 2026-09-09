"""CLI HTTP client for the local daemon."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from mailkit.config import AppConfig
from mailkit.errors import DaemonError, from_api_error
from mailkit.service import is_running, load_or_create_token


def _http_error(exc: urllib.error.HTTPError):
    payload = exc.read().decode("utf-8", "replace")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        parsed = {"error": {"message": payload, "code": "error"}}
    err = (parsed or {}).get("error") or {"message": payload}
    if not isinstance(err, dict):
        err = {"message": str(err)}
    message = err.get("message") or payload or f"HTTP {exc.code}"
    return from_api_error(message, code=err.get("code"), details=err, status=exc.code)


class ApiClient:
    def __init__(self, cfg: AppConfig, root, *, token: str | None = None):
        self.base = f"http://{cfg.daemon.host}:{cfg.daemon.port}"
        self.token = token or load_or_create_token(root)
        self.root = root

    def require_daemon(self) -> None:
        if not is_running(self.root):
            raise DaemonError("mailkit service is not running. Start it with: mailkit service start")

    def request(self, method: str, path: str, *, query: dict | None = None, body: dict | None = None) -> Any:
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None}, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise _http_error(exc) from exc
        except urllib.error.URLError as exc:
            raise DaemonError(f"Cannot reach mailkit service at {self.base}: {exc}") from exc

    def stream_sse(self, path: str, query: dict | None = None) -> Iterator[dict]:
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None}, doseq=True)
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "text/event-stream")
        try:
            resp = urllib.request.urlopen(req, timeout=None)
        except urllib.error.HTTPError as exc:
            raise _http_error(exc) from exc
        except urllib.error.URLError as exc:
            raise DaemonError(f"Cannot stream from {self.base}: {exc}") from exc
        buf = ""
        data_lines: list[str] = []
        with resp:
            while True:
                chunk = resp.read(1)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                if "\n" not in buf:
                    continue
                line, buf = buf.split("\n", 1)
                line = line.rstrip("\r")
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif line == "" and data_lines:
                    raw = "\n".join(data_lines)
                    data_lines = []
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        continue
