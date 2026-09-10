"""Microsoft Graph / Outlook provider. IMAP fallback plus Graph delta/subscriptions."""

from __future__ import annotations

from typing import Any

from mailkit.config import AccountConfig
from mailkit.discovery import WELL_KNOWN
from mailkit.httputil import request_json, urljoin
from mailkit.logutil import get_logger
from mailkit.providers.imap_smtp import ImapSmtpProvider

log = get_logger("mailkit.graph")
GRAPH = "https://graph.microsoft.com/v1.0"


class GraphProvider(ImapSmtpProvider):
    id = "graph"

    def capabilities(self) -> set[str]:
        caps = super().capabilities()
        if self.secrets.get("access_token") and "graph.microsoft.com" in " ".join(self.account.oauth.scopes):
            caps.add("graph_delta")
            caps.add("graph_push")
        return caps

    def _token(self) -> str | None:
        return self.secrets.get("access_token")

    def delta(self, folder: str = "inbox", delta_link: str | None = None) -> dict:
        token = self._token()
        if not token:
            return {"value": []}
        url = delta_link or urljoin(GRAPH, f"me/mailFolders/{folder}/messages/delta")
        return request_json(url, token=token) or {"value": []}

    def subscribe(self, notify_url: str, folder: str = "inbox", expiration: str | None = None) -> dict | None:
        token = self._token()
        if not token or not notify_url:
            return None
        body = {
            "changeType": "created,updated,deleted",
            "notificationUrl": notify_url,
            "resource": f"me/mailFolders/{folder}/messages",
            "expirationDateTime": expiration,
            "clientState": self.account.id,
        }
        return request_json(urljoin(GRAPH, "subscriptions"), method="POST", token=token, body=body)

    def renew_subscription(self, sub_id: str, expiration: str) -> dict | None:
        token = self._token()
        if not token:
            return None
        return request_json(
            urljoin(GRAPH, f"subscriptions/{sub_id}"),
            method="PATCH",
            token=token,
            body={"expirationDateTime": expiration},
        )


class GraphPlugin:
    plugin_type = "provider"
    id = "graph"
    label = "Microsoft 365 / Outlook"

    def supports(self, account: AccountConfig) -> bool:
        if account.provider in {"graph", "outlook", "microsoft"}:
            return True
        host = account.imap.host.lower()
        domain = (account.address.split("@")[-1] if "@" in account.address else "").lower()
        return host.startswith("outlook.") or domain in {
            "outlook.com",
            "hotmail.com",
            "live.com",
            "msn.com",
            "office365.com",
        }

    def create(self, account: AccountConfig, secrets: dict[str, Any], *, store=None) -> GraphProvider:
        domain = account.address.rsplit("@", 1)[-1].lower()
        spec = WELL_KNOWN["outlook.com" if domain in {"outlook.com", "hotmail.com", "live.com", "msn.com"} else "office365.com"]
        if not account.imap.host:
            account.imap.host = spec.imap_host
            account.imap.port = spec.imap_port
            account.imap.tls = True
        if not account.smtp.host:
            account.smtp.host = spec.smtp_host
            account.smtp.port = spec.smtp_port
            account.smtp.starttls = True
        if not account.oauth.auth_url:
            tenant = account.oauth.tenant or "common"
            account.oauth.auth_url = spec.oauth_auth_url.replace("/common/", f"/{tenant}/")
            account.oauth.token_url = spec.oauth_token_url.replace("/common/", f"/{tenant}/")
            account.oauth.scopes = list(spec.oauth_scopes)
        return GraphProvider(account, secrets, store=store)
