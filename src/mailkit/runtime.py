"""Shared runtime: config, vault, store, plugins, providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mailkit.config import AppConfig, load_config
from mailkit.db import Store
from mailkit.errors import ConfigError, NotFoundError
from mailkit.plugins.registry import PluginRegistry, load_plugins
from mailkit.vault import Vault


@dataclass
class Runtime:
    root: Path
    config: AppConfig
    vault: Vault
    store: Store
    plugins: PluginRegistry

    def reload(self) -> None:
        self.config = load_config(self.root)

    def account(self, account_id: str | None, *, unified: bool = False):
        return self.config.require_account(account_id, unified=unified)

    def provider_for(self, account_id: str):
        acc = self.config.accounts.get(account_id)
        if not acc:
            raise NotFoundError(f"Unknown account: {account_id}")
        plugin = self.plugins.provider_for(acc)
        if not plugin:
            raise ConfigError(f"No provider plugin for account {account_id}")
        secrets = self.vault.get_account(account_id)
        provider = plugin.create(acc, secrets, store=self.store)
        if hasattr(provider, "vault"):
            provider.vault = self.vault
        return provider, acc, secrets

    def close(self) -> None:
        self.store.close()


def open_runtime(root: Path | None = None) -> Runtime:
    from mailkit.paths import data_dir

    home = data_dir(root)
    cfg = load_config(home)
    vault = Vault(home)
    store = Store(home)
    plugins = load_plugins(home)
    return Runtime(root=home, config=cfg, vault=vault, store=store, plugins=plugins)
