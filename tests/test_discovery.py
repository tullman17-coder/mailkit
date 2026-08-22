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
