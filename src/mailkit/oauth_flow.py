"""Loopback OAuth2 authorization-code flow for Gmail and Microsoft."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import time
import threading
import urllib.parse
import webbrowser
from secrets import token_urlsafe

from mailkit.auth.xoauth2 import oauth_tokens
from mailkit.config import AccountConfig
from mailkit.errors import AuthError, UsageError


def authorization_url(account: AccountConfig, redirect_uri: str, state: str, verifier: str = "") -> str:
    params = {
        "client_id": account.oauth.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(account.oauth.scopes),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    if verifier:
        params["code_challenge"] = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params["code_challenge_method"] = "S256"
    params["login_hint"] = account.address
    return account.oauth.auth_url + "?" + urllib.parse.urlencode(params)


class NativeOAuthSessions:
    """Short-lived PKCE sessions; codes and verifiers never reach config or logs."""

    def __init__(self):
        self.sessions: dict[str, tuple] = {}
        self.lock = threading.Lock()

    def begin(self, account: AccountConfig, redirect_uri: str) -> dict:
        if not isinstance(redirect_uri, str) or len(redirect_uri) > 2048:
            raise UsageError("A registered native OAuth redirect URI is required")
        parsed = urllib.parse.urlparse(redirect_uri)
        if (not parsed.scheme or parsed.scheme in {"http", "https", "file", "data", "javascript", "ftp"}
                or parsed.query or parsed.fragment or parsed.username or not parsed.path):
            raise UsageError("Use the registered native app callback URI without query or fragment")
        state, verifier = token_urlsafe(32), token_urlsafe(64)
        now = time.monotonic()
        with self.lock:
            self.sessions = {key: value for key, value in self.sessions.items() if value[0] > now}
            if len(self.sessions) >= 32:
                raise UsageError("Too many pending sign-ins; retry after five minutes")
            self.sessions[state] = (now + 300, account, redirect_uri, verifier)
        return {"authorization_url": authorization_url(account, redirect_uri, state, verifier),
                "state": state, "expires_in": 300}

    def complete(self, state: str, callback_url: str) -> tuple[AccountConfig, dict]:
        if not isinstance(state, str) or not isinstance(callback_url, str) or len(callback_url) > 16384:
            raise UsageError("state and callback_url are required")
        with self.lock:
            session = self.sessions.pop(state, None)
        if not session or session[0] <= time.monotonic():
            raise AuthError("Sign-in expired or was already completed; start again")
        _, account, redirect_uri, verifier = session
        parsed = urllib.parse.urlparse(callback_url)
        if parsed._replace(query="").geturl() != redirect_uri:
            raise AuthError("OAuth callback URI mismatch")
        values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if len(values.get("state", [])) != 1 or not hmac.compare_digest(values["state"][0].encode(), state.encode()):
            raise AuthError("OAuth state mismatch")
        if values.get("error"):
            raise AuthError("Authorization was declined; start sign-in again")
        if len(values.get("code", [])) != 1 or not values["code"][0]:
            raise AuthError("OAuth callback did not include one authorization code")
        tokens = oauth_tokens(account.oauth.token_url, client_id=account.oauth.client_id,
                              code=values["code"][0], redirect_uri=redirect_uri,
                              extra={"code_verifier": verifier})
        return account, {"access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token"),
                         "token_expiry": int(time.time()) + int(tokens.get("expires_in") or 3600)}


def run_local_oauth(account: AccountConfig, secrets: dict, *, port: int = 8766) -> dict:
    if not account.oauth.client_id or not account.oauth.auth_url or not account.oauth.token_url:
        raise AuthError("OAuth client_id, auth_url, and token_url are required")
    state, verifier = token_urlsafe(32), token_urlsafe(64)
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
    url = authorization_url(account, redirect_uri, state, verifier)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    if not done.wait(timeout=300):
        server.shutdown()
        raise AuthError("OAuth timed out waiting for the browser callback")
    server.shutdown()
    server.server_close()
    if not result.get("code"):
        raise AuthError("OAuth callback did not include an authorization code")
    tokens = oauth_tokens(
        account.oauth.token_url,
        client_id=account.oauth.client_id,
        client_secret=secrets.get("client_secret") or "",
        code=result["code"],
        redirect_uri=redirect_uri,
        extra={"code_verifier": verifier},
    )
    import time

    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token") or secrets.get("refresh_token"),
        "token_expiry": int(time.time()) + int(tokens.get("expires_in") or 3600),
        "client_id": account.oauth.client_id,
        "token_url": account.oauth.token_url,
    }
