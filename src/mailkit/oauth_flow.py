"""Loopback OAuth2 authorization-code flow for Gmail and Microsoft."""

from __future__ import annotations

import http.server
import threading
import urllib.parse
import webbrowser
from secrets import token_urlsafe
from typing import Callable

from mailkit.auth.xoauth2 import oauth_tokens
from mailkit.config import AccountConfig
from mailkit.errors import AuthError


def authorization_url(account: AccountConfig, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": account.oauth.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(account.oauth.scopes),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return account.oauth.auth_url + "?" + urllib.parse.urlencode(params)


def run_local_oauth(account: AccountConfig, secrets: dict, *, port: int = 8766) -> dict:
    if not account.oauth.client_id or not account.oauth.auth_url or not account.oauth.token_url:
        raise AuthError("OAuth client_id, auth_url, and token_url are required")
    state = token_urlsafe(16)
    redirect_uri = f"http://127.0.0.1:{port}/oauth/callback"
    result: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/oauth/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"state mismatch")
                done.set()
                return
            result["code"] = qs.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Mailkit authorized. You can close this tab.</body></html>")
            done.set()

        def log_message(self, fmt, *args):
            return

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = authorization_url(account, redirect_uri, state)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    if not done.wait(timeout=300):
        server.shutdown()
        raise AuthError("OAuth timed out waiting for the browser callback")
    server.shutdown()
    if not result.get("code"):
        raise AuthError("OAuth callback did not include an authorization code")
    tokens = oauth_tokens(
        account.oauth.token_url,
        client_id=account.oauth.client_id,
        client_secret=secrets.get("client_secret") or "",
        code=result["code"],
        redirect_uri=redirect_uri,
    )
    import time

    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token") or secrets.get("refresh_token"),
        "token_expiry": int(time.time()) + int(tokens.get("expires_in") or 3600),
        "client_id": account.oauth.client_id,
        "token_url": account.oauth.token_url,
    }
