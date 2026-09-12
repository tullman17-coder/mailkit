"""Provider discovery from well-known maps, SRV/MX records, and common hosts."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field, replace
from email.utils import parseaddr
from typing import Callable

from mailkit.errors import ConfigError

ResolveFn = Callable[[str], list[tuple[int, int, int, str]]]
MxFn = Callable[[str], list[tuple[int, str]]]


@dataclass
class Discovered:
    provider_id: str
    auth_hint: str
    imap_host: str
    imap_port: int = 993
    imap_tls: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_starttls: bool = True
    smtp_tls: bool = False
    oauth_auth_url: str = ""
    oauth_token_url: str = ""
    oauth_scopes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    source: str = "well-known"

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "auth_hint": self.auth_hint,
            "imap": {"host": self.imap_host, "port": self.imap_port, "tls": self.imap_tls},
            "smtp": {
                "host": self.smtp_host,
                "port": self.smtp_port,
                "starttls": self.smtp_starttls,
                "tls": self.smtp_tls,
            },
            "oauth": {
                "auth_url": self.oauth_auth_url,
                "token_url": self.oauth_token_url,
                "scopes": self.oauth_scopes,
            },
            "notes": self.notes,
            "source": self.source,
        }


WELL_KNOWN: dict[str, Discovered] = {}


def _reg(*domains: str, **kwargs) -> None:
    spec = Discovered(**kwargs)
    for domain in domains:
        WELL_KNOWN[domain.lower()] = spec


_reg(
    "gmail.com",
    "googlemail.com",
    provider_id="gmail",
    auth_hint="oauth2",
    imap_host="imap.gmail.com",
    smtp_host="smtp.gmail.com",
    oauth_auth_url="https://accounts.google.com/o/oauth2/v2/auth",
    oauth_token_url="https://oauth2.googleapis.com/token",
    oauth_scopes=["https://mail.google.com/"],
    notes=["Use registered Google OAuth with the mail.google.com scope. App passwords depend on account policy."],
)
_reg(
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    provider_id="graph",
    auth_hint="oauth2",
    imap_host="outlook.office365.com",
    smtp_host="smtp-mail.outlook.com",
    oauth_auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    oauth_token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    oauth_scopes=[
        "https://outlook.office.com/IMAP.AccessAsUser.All",
        "https://outlook.office.com/SMTP.Send",
        "offline_access",
    ],
    notes=["Enable IMAP in Outlook.com settings. These OAuth scopes authorize IMAP/SMTP, not Microsoft Graph."],
)
WELL_KNOWN["office365.com"] = replace(
    WELL_KNOWN["outlook.com"],
    smtp_host="smtp.office365.com",
    notes=["Your organization must permit IMAP and SMTP AUTH. These OAuth scopes authorize IMAP/SMTP, not Microsoft Graph."],
)
_reg(
    "yahoo.com",
    "ymail.com",
    "rocketmail.com",
    provider_id="yahoo",
    auth_hint="app_password",
    imap_host="imap.mail.yahoo.com",
    smtp_host="smtp.mail.yahoo.com",
    notes=["Generate a Yahoo app password for manual IMAP/SMTP sign-in."],
)
_reg(
    "icloud.com",
    "me.com",
    "mac.com",
    provider_id="imap",
    auth_hint="app_password",
    imap_host="imap.mail.me.com",
    smtp_host="smtp.mail.me.com",
    notes=["Generate an Apple app-specific password. Use your full email address for SMTP."],
)
_reg(
    "aol.com",
    "aim.com",
    "netscape.com",
    provider_id="imap",
    auth_hint="app_password",
    imap_host="imap.aol.com",
    smtp_host="smtp.aol.com",
    smtp_port=465,
    smtp_starttls=False,
    smtp_tls=True,
    notes=["Generate an AOL app password for manual IMAP/SMTP sign-in."],
)
_reg(
    "fastmail.com",
    "fastmail.fm",
    provider_id="imap",
    auth_hint="app_password",
    imap_host="imap.fastmail.com",
    smtp_host="smtp.fastmail.com",
    notes=["An app password is required. Fastmail Basic plans do not include IMAP/SMTP."],
)
_reg(
    "zoho.com",
    "zohomail.com",
    provider_id="imap",
    auth_hint="password",
    imap_host="imap.zoho.com",
    smtp_host="smtp.zoho.com",
    notes=["Enable IMAP; use an app password with 2FA. Check account settings for plan/datacenter-specific servers; paid organizations use imappro/smtppro."],
)
_reg(
    "gmx.com",
    provider_id="imap",
    auth_hint="app_password",
    imap_host="imap.gmx.com",
    smtp_host="mail.gmx.com",
    notes=["Enable IMAP in GMX settings and generate an app password. Regional GMX servers can differ."],
)
_reg(
    "gmx.net",
    "gmx.de",
    provider_id="imap",
    auth_hint="app_password",
    imap_host="imap.gmx.net",
    smtp_host="mail.gmx.net",
    notes=["Enable IMAP in GMX settings and generate an app password."],
)
_reg(
    "mail.com",
    provider_id="imap",
    auth_hint="password",
    imap_host="imap.mail.com",
    smtp_host="smtp.mail.com",
    notes=["IMAP requires a mail.com Premium account; use an app password with 2FA."],
)
_reg(
    "ionos.com",
    provider_id="imap",
    auth_hint="password",
    imap_host="imap.ionos.com",
    smtp_host="smtp.ionos.com",
    smtp_port=465,
    smtp_starttls=False,
    smtp_tls=True,
    notes=["Use your mailbox password. IONOS country-specific servers and hosted Exchange settings can differ."],
)


def domain_of(address: str) -> str:
    _, email = parseaddr(address)
    if "@" not in email:
        raise ConfigError(f"Invalid email address: {address}")
    return email.split("@", 1)[1].lower()


def _srv_lookup(name: str, resolver: ResolveFn | None = None) -> list[tuple[int, int, int, str]]:
    if resolver:
        return resolver(name)
    try:
        import dns.resolver  # type: ignore

        answers = dns.resolver.resolve(name, "SRV")
        return [(r.priority, r.weight, r.port, str(r.target).rstrip(".")) for r in answers]
    except Exception:
        return _dns_srv_stdlib(name)


def _dns_srv_stdlib(name: str) -> list[tuple[int, int, int, str]]:
    try:
        records = socket.getaddrinfo(name, None)
        # getaddrinfo cannot fetch SRV; leave empty without dnspython.
        del records
    except Exception:
        pass
    return []


def _mx_lookup(domain: str, resolver: MxFn | None = None) -> list[tuple[int, str]]:
    if resolver:
        return resolver(domain)
    try:
        import dns.resolver  # type: ignore

        answers = dns.resolver.resolve(domain, "MX")
        return sorted((r.preference, str(r.exchange).rstrip(".")) for r in answers)
    except Exception:
        return []


def _guess_hosts(domain: str) -> tuple[str, str]:
    return f"imap.{domain}", f"smtp.{domain}"


def discover(
    address: str,
    *,
    srv_resolver: ResolveFn | None = None,
    mx_resolver: MxFn | None = None,
    probe: bool = False,
) -> Discovered:
    domain = domain_of(address)
    if domain in WELL_KNOWN:
        found = WELL_KNOWN[domain]
        return Discovered(**{**found.__dict__})
    # Microsoft 365 custom domains often use outlook MX.
    mx = _mx_lookup(domain, mx_resolver)
    mx_hosts = " ".join(h for _, h in mx).lower()
    if "google.com" in mx_hosts or "googlemail.com" in mx_hosts:
        spec = WELL_KNOWN["gmail.com"]
        out = Discovered(**{**spec.__dict__})
        out.source = "mx"
        out.notes = list(out.notes) + [f"MX for {domain} points at Google."]
        return out
    if "outlook.com" in mx_hosts or "protection.outlook.com" in mx_hosts:
        spec = WELL_KNOWN["office365.com"]
        out = Discovered(**{**spec.__dict__})
        out.source = "mx"
        out.notes = list(out.notes) + [f"MX for {domain} points at Microsoft 365."]
        return out
    if "yahoodns.net" in mx_hosts or "yahoo.com" in mx_hosts:
        spec = WELL_KNOWN["yahoo.com"]
        out = Discovered(**{**spec.__dict__})
        out.source = "mx"
        return out

    srv_imap = _srv_lookup(f"_imaps._tcp.{domain}", srv_resolver)
    srv_sub = _srv_lookup(f"_submission._tcp.{domain}", srv_resolver)
    imap_host, smtp_host = _guess_hosts(domain)
    imap_port, smtp_port = 993, 587
    source = "guess"
    notes = [f"No well-known provider for {domain}; guessed imap.{domain} / smtp.{domain}."]
    if srv_imap:
        srv_imap.sort()
        _, _, imap_port, imap_host = srv_imap[0]
        source = "srv"
        notes = [f"Used SRV _imaps._tcp.{domain}."]
    if srv_sub:
        srv_sub.sort()
        _, _, smtp_port, smtp_host = srv_sub[0]
        source = "srv"
    if probe:
        notes.append("Manual host/port/TLS settings can override discovery at any time.")
    return Discovered(
        provider_id="imap",
        auth_hint="password",
        imap_host=imap_host,
        imap_port=imap_port,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        source=source,
        notes=notes,
    )
