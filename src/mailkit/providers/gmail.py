"""Gmail provider: IMAP/SMTP XOAUTH2 plus optional Gmail API history/push."""

from __future__ import annotations

import base64
from typing import Any

from mailkit.config import AccountConfig
from mailkit.discovery import WELL_KNOWN
from mailkit.httputil import request_json, urljoin
from mailkit.logutil import get_logger
from mailkit.models import Message
from mailkit.parser import parse_rfc822
from mailkit.providers.imap_smtp import ImapSmtpProvider

log = get_logger("mailkit.gmail")
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


def _b64url_decode(data: str) -> bytes:
    pad = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + pad)


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

    def uid_for_gmail_id(self, mailbox: str, gmail_id: str) -> str | None:
        """Map a Gmail API hex id to an IMAP UID via X-GM-MSGID. Gmail ids are not UIDs."""
        if not gmail_id:
            return None
        try:
            msgid = int(str(gmail_id), 16)
        except ValueError:
            return None
        try:
            uids = self._search_uids(mailbox, ["X-GM-MSGID", str(msgid)])
        except Exception as exc:
            log.info("X-GM-MSGID search failed for %s: %s", gmail_id, exc)
            return None
        if not uids:
            return None
        return str(uids[0])

    def get_gmail_api_message(self, mailbox: str, gmail_id: str) -> Message | None:
        """Fetch RFC822 via users.messages.get and prefer the IMAP UID as native_id."""
        token = self.secrets.get("access_token")
        if not token or not gmail_id:
            return None
        payload = request_json(
            urljoin(GMAIL_API, f"users/me/messages/{gmail_id}", format="raw"),
            token=token,
        )
        if not isinstance(payload, dict) or not payload.get("raw"):
            return None
        raw = _b64url_decode(str(payload["raw"]))
        uidvalidity = 0
        try:
            uidvalidity = self._select(mailbox, readonly=True) or 0
        except Exception:
            uidvalidity = 0
        parsed = parse_rfc822(
            raw,
            account_id=self.account.id,
            provider_id=self.id,
            mailbox=mailbox,
            uidvalidity=uidvalidity,
        )
        if parsed.message_id:
            try:
                uids = self._search_uids(mailbox, ["HEADER", "Message-ID", parsed.message_id])
                if uids:
                    return self.get_message(mailbox, str(uids[0]), peek=True)
            except Exception as exc:
                log.info("Message-ID search after Gmail API get failed: %s", exc)
        uid = self.uid_for_gmail_id(mailbox, gmail_id)
        if uid:
            try:
                return self.get_message(mailbox, uid, peek=True)
            except Exception as exc:
                log.info("IMAP fetch after Gmail API get failed: %s", exc)
        if self.store:
            self.store.upsert_message(parsed)
        return parsed


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
