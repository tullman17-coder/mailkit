"""Yahoo / Ymail IMAP+SMTP. Real-time via IMAP IDLE; app passwords typical."""

from __future__ import annotations

from typing import Any

from mailkit.config import AccountConfig
from mailkit.discovery import WELL_KNOWN
from mailkit.providers.imap_smtp import ImapSmtpProvider


class YahooProvider(ImapSmtpProvider):
    id = "yahoo"


class YahooPlugin:
    plugin_type = "provider"
    id = "yahoo"
    label = "Yahoo Mail"

    def supports(self, account: AccountConfig) -> bool:
        if account.provider == "yahoo":
            return True
        domain = (account.address.split("@")[-1] if "@" in account.address else "").lower()
        return domain in {"yahoo.com", "ymail.com", "rocketmail.com"} or "yahoo" in account.imap.host

    def create(self, account: AccountConfig, secrets: dict[str, Any], *, store=None) -> YahooProvider:
        spec = WELL_KNOWN["yahoo.com"]
        if not account.imap.host:
            account.imap.host = spec.imap_host
            account.imap.port = spec.imap_port
            account.imap.tls = True
        if not account.smtp.host:
            account.smtp.host = spec.smtp_host
            account.smtp.port = spec.smtp_port
            account.smtp.starttls = True
        return YahooProvider(account, secrets, store=store)
