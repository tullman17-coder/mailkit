"""IMAP/SMTP username+password and app-password auth."""

from __future__ import annotations

from typing import Any

from mailkit.config import AccountConfig
from mailkit.errors import AuthError


class PasswordAuth:
    plugin_type = "auth"
    id = "password"

    def prepare_imap(self, client: Any, account: AccountConfig, secrets: dict[str, Any]) -> None:
        password = secrets.get("password")
        if not password:
            raise AuthError(f"No password stored for account {account.id}")
        user = secrets.get("username") or account.address
        typ, _ = client.login(user, password)
        if typ != "OK":
            raise AuthError(f"IMAP login failed for {account.address}")

    def prepare_smtp(self, client: Any, account: AccountConfig, secrets: dict[str, Any]) -> None:
        password = secrets.get("smtp_password") or secrets.get("password")
        if not password:
            raise AuthError(f"No password stored for account {account.id}")
        user = secrets.get("smtp_username") or secrets.get("username") or account.address
        client.login(user, password)

    def refresh(self, account: AccountConfig, secrets: dict[str, Any]) -> dict[str, Any] | None:
        return None
