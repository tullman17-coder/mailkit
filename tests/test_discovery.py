import pytest

from mailkit.discovery import discover, domain_of


def test_well_known_gmail():
    spec = discover("person@gmail.com")
    assert spec.provider_id == "gmail"
    assert spec.imap_host == "imap.gmail.com"
    assert spec.smtp_host == "smtp.gmail.com"
    assert spec.auth_hint == "oauth2"


def test_well_known_yahoo():
    spec = discover("me@ymail.com")
    assert spec.provider_id == "yahoo"
    assert spec.imap_host == "imap.mail.yahoo.com"


def test_mx_google_workspace():
    spec = discover(
        "it@custom-corp.test",
        mx_resolver=lambda _d: [(10, "aspmx.l.google.com")],
        srv_resolver=lambda _n: [],
    )
    assert spec.provider_id == "gmail"
    assert spec.source == "mx"


def test_mx_microsoft():
    spec = discover(
        "it@contoso.test",
        mx_resolver=lambda _d: [(0, "contoso-com.mail.protection.outlook.com")],
        srv_resolver=lambda _n: [],
    )
    assert spec.provider_id == "graph"
    assert spec.smtp_host == "smtp.office365.com"
    assert spec.source == "mx"


@pytest.mark.parametrize("domain,imap,smtp,port", [
    ("gmail.com", "imap.gmail.com", "smtp.gmail.com", 587),
    ("outlook.com", "outlook.office365.com", "smtp-mail.outlook.com", 587),
    ("icloud.com", "imap.mail.me.com", "smtp.mail.me.com", 587),
    ("yahoo.com", "imap.mail.yahoo.com", "smtp.mail.yahoo.com", 587),
    ("aol.com", "imap.aol.com", "smtp.aol.com", 465),
    ("fastmail.com", "imap.fastmail.com", "smtp.fastmail.com", 587),
    ("zoho.com", "imap.zoho.com", "smtp.zoho.com", 587),
    ("gmx.com", "imap.gmx.com", "mail.gmx.com", 587),
    ("mail.com", "imap.mail.com", "smtp.mail.com", 587),
    ("ionos.com", "imap.ionos.com", "smtp.ionos.com", 465),
])
def test_ten_provider_presets_are_encrypted(domain, imap, smtp, port):
    def no_dns(_name):
        pytest.fail("Known provider should not need DNS discovery")

    spec = discover(f"user@{domain.upper()}", mx_resolver=no_dns, srv_resolver=no_dns)
    assert spec.imap_host == imap
    assert spec.imap_port == 993 and spec.imap_tls
    assert spec.smtp_host == smtp and spec.smtp_port == port
    assert spec.smtp_tls == (port == 465)
    assert spec.smtp_starttls == (port == 587)
    assert spec.source == "well-known"


def test_microsoft_consumer_and_business_use_imap_oauth():
    for domain in ("outlook.com", "hotmail.com", "live.com", "msn.com", "office365.com"):
        spec = discover(f"user@{domain}")
        assert spec.smtp_host == ("smtp.office365.com" if domain == "office365.com" else "smtp-mail.outlook.com")
        assert spec.auth_hint == "oauth2"
        assert spec.oauth_scopes == [
            "https://outlook.office.com/IMAP.AccessAsUser.All",
            "https://outlook.office.com/SMTP.Send",
            "offline_access",
        ]


def test_srv_custom_domain():
    spec = discover(
        "me@example.test",
        mx_resolver=lambda _d: [],
        srv_resolver=lambda name: [(0, 0, 993, "mail.example.test")] if name.startswith("_imaps") else [(0, 0, 587, "mail.example.test")],
    )
    assert spec.imap_host == "mail.example.test"
    assert spec.imap_port == 993
    assert spec.source == "srv"


def test_domain_of():
    assert domain_of("Name <a.b@Host.COM>") == "host.com"
