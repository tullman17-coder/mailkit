"""Gmail provider: IMAP/SMTP XOAUTH2 plus optional Gmail API history/push."""

from __future__ import annotations

from typing import Any

from mailkit.config import AccountConfig
from mailkit.discovery import WELL_KNOWN
from mailkit.httputil import request_json, urljoin
from mailkit.logutil import get_logger
from mailkit.providers.imap_smtp import ImapSmtpProvider

log = get_logger("mailkit.gmail")
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


class GmailProvider(ImapSmtpProvider):
    id = "gmail"

    def capabilities(self) -> set[str]:
        caps = super().capabilities()
        caps.add("labels")
        if self.secrets.get("access_token"):
            caps.add("history")
            if self.account.oauth.pubsub_subscription:
                caps.add("gmail_push")
        return caps

    def history_id(self) -> str | None:
        token = self.secrets.get("access_token")
        if not token:
            return None
        profile = request_json(urljoin(GMAIL_API, "users/me/profile"), token=token)
        if isinstance(profile, dict):
            return str(profile.get("historyId") or "") or None
        return None

    def list_history(self, start: str) -> dict:
        token = self.secrets.get("access_token")
        if not token:
            return {"history": []}
        return request_json(
            urljoin(GMAIL_API, "users/me/history", startHistoryId=start, userId="me"),
            token=token,
        ) or {"history": []}

    def watch(self, topic: str) -> dict | None:
        token = self.secrets.get("access_token")
        if not token or not topic:
            return None
        return request_json(
            urljoin(GMAIL_API, "users/me/watch"),
            method="POST",
            token=token,
            body={"topicName": topic, "labelIds": ["INBOX"]},
        )


class GmailPlugin:
    plugin_type = "provider"
    id = "gmail"
    label = "Gmail"

    def supports(self, account: AccountConfig) -> bool:
        if account.provider == "gmail":
            return True
        domain = (account.address.split("@")[-1] if "@" in account.address else "").lower()
        return domain in {"gmail.com", "googlemail.com"} or account.imap.host == "imap.gmail.com"

    def create(self, account: AccountConfig, secrets: dict[str, Any], *, store=None) -> GmailProvider:
        spec = WELL_KNOWN["gmail.com"]
        if not account.imap.host:
            account.imap.host = spec.imap_host
            account.imap.port = spec.imap_port
            account.imap.tls = True
        if not account.smtp.host:
            account.smtp.host = spec.smtp_host
            account.smtp.port = spec.smtp_port
            account.smtp.starttls = True
        if not account.oauth.auth_url:
            account.oauth.auth_url = spec.oauth_auth_url
            account.oauth.token_url = spec.oauth_token_url
            account.oauth.scopes = list(spec.oauth_scopes)
        return GmailProvider(account, secrets, store=store)
