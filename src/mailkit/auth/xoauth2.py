"""OAuth2 token refresh and SASL XOAUTH2 for IMAP/SMTP."""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
from typing import Any

from mailkit.config import AccountConfig
from mailkit.errors import AuthError, NetworkError
from mailkit.logutil import get_logger

log = get_logger("mailkit.auth")


def build_xoauth2(user: str, access_token: str) -> str:
    if any(ord(c) < 32 for c in user + access_token):
        raise AuthError("Invalid control character in OAuth credentials")
    raw = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def oauth_tokens(
    token_url: str,
    *,
    client_id: str,
    client_secret: str = "",
    refresh_token: str | None = None,
    code: str | None = None,
    redirect_uri: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    if urllib.parse.urlparse(token_url).scheme != "https":
        raise AuthError("OAuth token endpoints must use HTTPS")
    data: dict[str, str] = {"client_id": client_id}
    if client_secret:
        data["client_secret"] = client_secret
    if refresh_token:
        data["grant_type"] = "refresh_token"
        data["refresh_token"] = refresh_token
    elif code:
        data["grant_type"] = "authorization_code"
        data["code"] = code
        if redirect_uri:
            data["redirect_uri"] = redirect_uri
    else:
        raise AuthError("OAuth2 refresh requires a refresh_token or authorization code")
    if extra:
        data.update(extra)
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(token_url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        # Never forward a code, refresh token, or client secret on a redirect.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise NetworkError(f"OAuth token request failed: {exc}") from exc
    if "access_token" not in payload:
        raise AuthError("OAuth token response missing access_token")
    payload["obtained_at"] = int(time.time())
    return payload


def access_token_valid(secrets: dict[str, Any], skew: int = 60) -> bool:
    token = secrets.get("access_token")
    expiry = int(secrets.get("token_expiry") or 0)
    return bool(token) and expiry > int(time.time()) + skew


class XOAuth2Auth:
    plugin_type = "auth"
    id = "oauth2"

    def _token(self, account: AccountConfig, secrets: dict[str, Any]) -> str:
        refreshed = self.refresh(account, secrets)
        if refreshed:
            secrets.update(refreshed)
        token = secrets.get("access_token")
        if not token:
            raise AuthError(f"No OAuth access token for {account.id}")
        return token

    def prepare_imap(self, client: Any, account: AccountConfig, secrets: dict[str, Any]) -> None:
        user = secrets.get("username") or account.address
        token = self._token(account, secrets)
        sasl = build_xoauth2(user, token)
        # imaplib performs the SASL base64 encoding itself.
        response = iter((base64.b64decode(sasl),))
        typ, dat = client.authenticate("XOAUTH2", lambda _: next(response, b""))
        if typ != "OK":
            raise AuthError(f"IMAP XOAUTH2 failed for {account.address}", details={"data": str(dat)})

    def prepare_smtp(self, client: Any, account: AccountConfig, secrets: dict[str, Any]) -> None:
        user = secrets.get("username") or account.address
        token = self._token(account, secrets)
        sasl = build_xoauth2(user, token)
        code, _ = client.docmd("AUTH", "XOAUTH2 " + sasl)
        if code == 334:
            # XOAUTH2 errors carry a challenge; finish it with an empty response.
            code, _ = client.docmd("")
        if code != 235:
            raise AuthError(f"SMTP XOAUTH2 failed for {account.address}")

    def refresh(self, account: AccountConfig, secrets: dict[str, Any]) -> dict[str, Any] | None:
        if access_token_valid(secrets):
            return None
        refresh = secrets.get("refresh_token")
        token_url = account.oauth.token_url or secrets.get("token_url")
        client_id = account.oauth.client_id or secrets.get("client_id")
        client_secret = secrets.get("client_secret") or ""
        if not (refresh and token_url and client_id):
            if secrets.get("access_token"):
                return None
            raise AuthError(f"Cannot refresh OAuth token for {account.id}")
        payload = oauth_tokens(
            token_url,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh,
        )
        expiry = int(time.time()) + int(payload.get("expires_in") or 3600)
        update = {
            "access_token": payload["access_token"],
            "token_expiry": expiry,
            "refresh_token": payload.get("refresh_token") or refresh,
            "token_url": token_url,
            "client_id": client_id,
        }
        log.info("refreshed oauth token for %s", account.id)
        return update
