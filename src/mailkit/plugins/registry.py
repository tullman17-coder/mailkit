"""Load built-in, entry-point, and ~/.mailkit/plugins modules without core changes."""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

from mailkit.logutil import get_logger
from mailkit.paths import plugin_dir

log = get_logger("mailkit.plugins")

ENTRY_POINTS = (
    "mailkit.providers",
    "mailkit.auth",
    "mailkit.hooks",
    "mailkit.watchers",
)


class PluginRegistry:
    def __init__(self) -> None:
        self.providers: dict[str, Any] = {}
        self.auth: dict[str, Any] = {}
        self.hooks: dict[str, Any] = {}
        self.watchers: dict[str, Any] = {}

    def register(self, plugin: Any) -> None:
        kind = getattr(plugin, "plugin_type", None)
        pid = getattr(plugin, "id", None)
        if not kind or not pid:
            raise ValueError(f"Plugin {plugin!r} needs plugin_type and id")
        bucket = {
            "provider": self.providers,
            "auth": self.auth,
            "hook": self.hooks,
            "watcher": self.watchers,
        }.get(kind)
        if bucket is None:
            raise ValueError(f"Unknown plugin_type {kind}")
        bucket[pid] = plugin
        log.debug("registered %s plugin %s", kind, pid)

    def provider_for(self, account) -> Any:
        hinted = self.providers.get(account.provider)
        if hinted and hinted.supports(account):
            return hinted
        for plugin in self.providers.values():
            if plugin.id == "auto":
                continue
            if plugin.supports(account):
                return plugin
        return self.providers.get("imap")

    def auth_for(self, name: str) -> Any:
        if name == "app_password":
            name = "password"
        plugin = self.auth.get(name)
        if not plugin:
            raise KeyError(f"Unknown auth strategy: {name}")
        return plugin

    def hook_list(self) -> list[Any]:
        return sorted(self.hooks.values(), key=lambda h: getattr(h, "priority", 0), reverse=True)

    def watcher_for(self, name: str) -> Any | None:
        return self.watchers.get(name)


def load_plugins(root: Path | None = None, extra: Iterable[ModuleType] | None = None) -> PluginRegistry:
    registry = PluginRegistry()
    from mailkit.auth.password import PasswordAuth
    from mailkit.auth.xoauth2 import XOAuth2Auth
    from mailkit.providers.gmail import GmailPlugin
    from mailkit.providers.graph import GraphPlugin
    from mailkit.providers.imap_smtp import ImapSmtpPlugin
    from mailkit.providers.yahoo import YahooPlugin
    from mailkit.watchers.gmail_push import GmailPushWatcher
    from mailkit.watchers.graph_push import GraphPushWatcher
    from mailkit.watchers.idle import IdleWatcher
    from mailkit.watchers.poll import PollWatcher

    for plugin in (
        ImapSmtpPlugin(),
        GmailPlugin(),
        GraphPlugin(),
        YahooPlugin(),
        PasswordAuth(),
        XOAuth2Auth(),
        IdleWatcher(),
        PollWatcher(),
        GmailPushWatcher(),
        GraphPushWatcher(),
    ):
        registry.register(plugin)

    _load_entry_points(registry)
    _load_local_dir(registry, plugin_dir(root))
    if extra:
        for mod in extra:
            _invoke_register(registry, mod)
    return registry


def _load_entry_points(registry: PluginRegistry) -> None:
    for group in ENTRY_POINTS:
        try:
            eps = importlib.metadata.entry_points()
            selected = eps.select(group=group) if hasattr(eps, "select") else eps.get(group, [])
        except Exception:
            continue
        for ep in selected:
            try:
                obj = ep.load()
                _accept(registry, obj)
            except Exception as exc:
                log.warning("entry point %s failed: %s", ep.name, exc)


def _load_local_dir(registry: PluginRegistry, directory: Path) -> None:
    if not directory.exists():
        return
    sys.path.insert(0, str(directory))
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        name = f"mailkit_user_plugin_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _invoke_register(registry, module)
        except Exception as exc:
            log.warning("plugin %s failed to load: %s", path.name, exc)


def _invoke_register(registry: PluginRegistry, module: ModuleType) -> None:
    fn = getattr(module, "register", None)
    if callable(fn):
        fn(registry)
        return
    for attr in ("PLUGIN", "plugin"):
        obj = getattr(module, attr, None)
        if obj is not None:
            _accept(registry, obj)


def _accept(registry: PluginRegistry, obj: Any) -> None:
    if callable(obj) and not hasattr(obj, "plugin_type"):
        try:
            obj = obj()
        except TypeError:
            pass
    if hasattr(obj, "plugin_type"):
        registry.register(obj)
        return
    if isinstance(obj, (list, tuple)):
        for item in obj:
            _accept(registry, item)


# Late import for spec_from_file_location
import importlib.util  # noqa: E402
