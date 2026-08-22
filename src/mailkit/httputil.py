"""Minimal HTTP JSON helpers for Gmail/Graph APIs. No third-party HTTP client."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mailkit.errors import AuthError, NetworkError, RateLimitError


def request_json(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any] | list | None:
    data = None
    req_headers = {"Accept": "application/json", "User-Agent": "mailkit/0.1"}
    if token:
        req_headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return None
            if resp.headers.get_content_type() == "application/json" or raw[:1] in (b"{", b"["):
                return json.loads(raw.decode("utf-8"))
            return {"raw": raw.decode("utf-8", "replace")}
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", "replace")
        if exc.code in (401, 403):
            raise AuthError(f"HTTP {exc.code} for {url}", details={"body": payload[:500]}) from exc
        if exc.code == 429:
            raise RateLimitError(f"Rate limited calling {url}") from exc
        raise NetworkError(f"HTTP {exc.code} for {url}: {payload[:300]}") from exc
    except urllib.error.URLError as exc:
        raise NetworkError(f"Request failed {url}: {exc}") from exc


def urljoin(base: str, path: str, **params: Any) -> str:
    url = base.rstrip("/") + "/" + path.lstrip("/")
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return url
