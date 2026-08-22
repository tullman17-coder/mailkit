"""Encrypted local credential store. Credentials never leave this machine."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mailkit.errors import AuthError, ConfigError
from mailkit.paths import master_key_path, vault_path


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_or_create_master_key(root: Path | None = None) -> bytes:
    env = os.environ.get("MAILKIT_MASTER_KEY")
    if env:
        raw = env.strip()
        try:
            key = bytes.fromhex(raw) if all(c in "0123456789abcdefABCDEF" for c in raw) and len(raw) in (64, 32) else raw.encode()
        except ValueError:
            key = raw.encode()
        if len(key) < 32:
            key = (key + b"\0" * 32)[:32]
        return key[:32]
    path = master_key_path(root)
    if path.exists():
        data = path.read_bytes()
        if len(data) < 32:
            raise ConfigError("master.key is too short")
        return data[:32]
    key = os.urandom(32)
    path.write_bytes(key)
    _chmod_private(path)
    return key


class Vault:
    def __init__(self, root: Path | None = None):
        self.root = root
        self.path = vault_path(root)
        self._key = load_or_create_master_key(root)
        self._data: dict[str, Any] = {"accounts": {}, "webhooks": {}, "oauth_clients": {}}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.save()
            return
        blob = self.path.read_bytes()
        if len(blob) < 13:
            raise AuthError("Credential vault is corrupt")
        nonce, ct = blob[:12], blob[12:]
        try:
            plain = AESGCM(self._key).decrypt(nonce, ct, None)
        except Exception as exc:
            raise AuthError("Unable to decrypt credential vault") from exc
        self._data = json.loads(plain.decode("utf-8"))
        self._data.setdefault("accounts", {})
        self._data.setdefault("webhooks", {})
        self._data.setdefault("oauth_clients", {})

    def save(self) -> None:
        nonce = os.urandom(12)
        ct = AESGCM(self._key).encrypt(nonce, json.dumps(self._data).encode("utf-8"), None)
        self.path.write_bytes(nonce + ct)
        _chmod_private(self.path)

    def get_account(self, account_id: str) -> dict[str, Any]:
        return dict(self._data["accounts"].get(account_id) or {})

    def put_account(self, account_id: str, secrets: dict[str, Any]) -> None:
        current = self.get_account(account_id)
        current.update({k: v for k, v in secrets.items() if v is not None})
        self._data["accounts"][account_id] = current
        self.save()

    def delete_account(self, account_id: str) -> None:
        self._data["accounts"].pop(account_id, None)
        self.save()

    def put_oauth_client(self, provider: str, payload: dict[str, Any]) -> None:
        self._data["oauth_clients"][provider] = payload
        self.save()

    def get_oauth_client(self, provider: str) -> dict[str, Any]:
        return dict(self._data["oauth_clients"].get(provider) or {})

    def put_webhook_secret(self, webhook_id: str, secret: str) -> None:
        self._data["webhooks"][webhook_id] = {"secret": secret}
        self.save()

    def get_webhook_secret(self, webhook_id: str) -> str:
        return (self._data["webhooks"].get(webhook_id) or {}).get("secret") or ""
