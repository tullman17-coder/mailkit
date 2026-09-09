"""Local TOML configuration. Secrets never live here."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from mailkit.errors import ConfigError, NotFoundError
from mailkit.paths import config_path, data_dir
from mailkit.tomlutil import dumps

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


DEFAULT_PORT = 8765


@dataclass
class ImapSettings:
    host: str = ""
    port: int = 993
    tls: bool = True
    starttls: bool = False
    timeout: float = 30.0


@dataclass
class SmtpSettings:
    host: str = ""
    port: int = 587
    tls: bool = False
    starttls: bool = True
    timeout: float = 30.0


@dataclass
class OAuthSettings:
    client_id: str = ""
    tenant: str = "common"
    token_url: str = ""
    auth_url: str = ""
    scopes: list[str] = field(default_factory=list)
    pubsub_topic: str = ""
    pubsub_subscription: str = ""
    graph_notify_url: str = ""


@dataclass
class FolderSettings:
    inbox: str = ""
    saved: str = ""
    sent: str = ""
    drafts: str = ""
    archives: str = ""
    trash: str = ""
    junk: str = ""

    def overrides(self) -> dict[str, str]:
        return {k: v for k, v in self.__dict__.items() if v}


@dataclass
class AccountConfig:
    id: str
    name: str = ""
    address: str = ""
    provider: str = "auto"
    auth: str = "password"
    enabled: bool = True
    watch: str = "auto"
    poll_interval: int = 45
    imap: ImapSettings = field(default_factory=ImapSettings)
    smtp: SmtpSettings = field(default_factory=SmtpSettings)
    oauth: OAuthSettings = field(default_factory=OAuthSettings)
    folders: FolderSettings = field(default_factory=FolderSettings)

    def display_name(self) -> str:
        return self.name or self.address or self.id

    def resolved_provider(self) -> str:
        """Concrete plugin id: honor an explicit choice, else address/host."""
        host = self.imap.host if self.imap else ""
        return infer_provider_id(self.address, host, explicit=self.provider)


@dataclass
class DaemonSettings:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    log_level: str = "info"
    json_logs: bool = False
    event_retention: int = 100_000
    allow_remote: bool = False
    doctor_interval: int = 60
    doctor_repair: bool = True


@dataclass
class DefaultsSettings:
    account: str = ""
    format: str = "text"


@dataclass
class AppConfig:
    daemon: DaemonSettings = field(default_factory=DaemonSettings)
    defaults: DefaultsSettings = field(default_factory=DefaultsSettings)
    accounts: dict[str, AccountConfig] = field(default_factory=dict)

    def default_account_id(self) -> str | None:
        if self.defaults.account and self.defaults.account in self.accounts:
            return self.defaults.account
        enabled = [a.id for a in self.accounts.values() if a.enabled]
        if len(enabled) == 1:
            return enabled[0]
        return None

    def require_account(self, account_id: str | None, *, unified: bool = False) -> AccountConfig | None:
        if unified:
            return None
        aid = account_id or self.default_account_id()
        if not aid:
            raise ConfigError("Select an account with --account, or set defaults.account.")
        if aid not in self.accounts:
            raise NotFoundError(f"Unknown account: {aid}", details={"account": aid})
        return self.accounts[aid]


def infer_provider_id(
    address: str = "",
    imap_host: str = "",
    explicit: str = "auto",
    discovered_id: str | None = None,
) -> str:
    """Resolve auto/empty provider from discovery, well-known domains, or IMAP host."""
    if explicit and explicit not in {"auto", ""}:
        return explicit
    if discovered_id and discovered_id not in {"auto", ""}:
        return discovered_id
    domain = ""
    if address and "@" in address:
        domain = address.rsplit("@", 1)[-1].lower().strip().rstrip(">")
    from mailkit.discovery import WELL_KNOWN

    if domain in WELL_KNOWN:
        return WELL_KNOWN[domain].provider_id
    host = (imap_host or "").lower()
    if host == "imap.gmail.com" or host.endswith(".gmail.com"):
        return "gmail"
    if host.startswith("outlook.") or "office365" in host:
        return "graph"
    if "yahoo" in host:
        return "yahoo"
    return "imap"


def _as_dataclass(cls, data: dict[str, Any]):
    if not data:
        return cls()
    allowed = {f.name: f for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in allowed:
            continue
        typ = allowed[key].type
        origin = getattr(typ, "__origin__", None)
        if origin is list:
            kwargs[key] = list(value or [])
        else:
            kwargs[key] = value
    return cls(**kwargs)


def load_config(root: Path | None = None) -> AppConfig:
    path = config_path(root)
    if not path.exists():
        cfg = AppConfig()
        save_config(cfg, root)
        return cfg
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    daemon = _as_dataclass(DaemonSettings, raw.get("daemon") or {})
    defaults = _as_dataclass(DefaultsSettings, raw.get("defaults") or {})
    accounts: dict[str, AccountConfig] = {}
    for acc_id, payload in (raw.get("accounts") or {}).items():
        payload = dict(payload or {})
        imap = _as_dataclass(ImapSettings, payload.pop("imap", None) or {})
        smtp = _as_dataclass(SmtpSettings, payload.pop("smtp", None) or {})
        oauth = _as_dataclass(OAuthSettings, payload.pop("oauth", None) or {})
        folders = _as_dataclass(FolderSettings, payload.pop("folders", None) or {})
        payload["id"] = payload.get("id") or acc_id
        base = _as_dataclass(AccountConfig, {k: v for k, v in payload.items() if k in AccountConfig.__dataclass_fields__})
        base.imap = imap
        base.smtp = smtp
        base.oauth = oauth
        base.folders = folders
        accounts[base.id] = base
    return AppConfig(daemon=daemon, defaults=defaults, accounts=accounts)


def save_config(cfg: AppConfig, root: Path | None = None) -> Path:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {
        "daemon": {k: v for k, v in cfg.daemon.__dict__.items()},
        "defaults": {k: v for k, v in cfg.defaults.__dict__.items()},
        "accounts": {},
    }
    for acc_id, acc in cfg.accounts.items():
        raw["accounts"][acc_id] = {
            "id": acc.id,
            "name": acc.name,
            "address": acc.address,
            "provider": acc.provider,
            "auth": acc.auth,
            "enabled": acc.enabled,
            "watch": acc.watch,
            "poll_interval": acc.poll_interval,
            "imap": dict(acc.imap.__dict__),
            "smtp": dict(acc.smtp.__dict__),
            "oauth": dict(acc.oauth.__dict__),
            "folders": dict(acc.folders.__dict__),
        }
    path.write_text(dumps(raw), encoding="utf-8")
    path.chmod(0o600)
    return path


def ensure_home(root: Path | None = None) -> Path:
    return data_dir(root)
