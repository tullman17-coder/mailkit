"""FLAG-P10: --provider auto must pick gmail/graph/yahoo, not generic IMAP."""

from mailkit.cli.main import main
from mailkit.config import AccountConfig, ImapSettings, infer_provider_id, load_config
from mailkit.plugins.registry import load_plugins


def test_auto_selects_gmail_by_address():
    registry = load_plugins()
    acc = AccountConfig(id="g", address="you@gmail.com", provider="auto")
    assert registry.provider_for(acc).id == "gmail"


def test_auto_selects_gmail_by_googlemail_domain():
    registry = load_plugins()
    acc = AccountConfig(id="g", address="you@googlemail.com", provider="auto")
    assert registry.provider_for(acc).id == "gmail"


def test_auto_selects_gmail_by_imap_host():
    registry = load_plugins()
    acc = AccountConfig(
        id="g",
        address="it@corp.test",
        provider="auto",
        imap=ImapSettings(host="imap.gmail.com"),
    )
    assert registry.provider_for(acc).id == "gmail"


def test_auto_selects_graph_by_outlook_address():
    registry = load_plugins()
    acc = AccountConfig(id="o", address="you@outlook.com", provider="auto")
    assert registry.provider_for(acc).id == "graph"


def test_auto_selects_graph_by_hotmail_address():
    registry = load_plugins()
    acc = AccountConfig(id="o", address="you@hotmail.com", provider="auto")
    assert registry.provider_for(acc).id == "graph"


def test_auto_selects_graph_by_imap_host():
    registry = load_plugins()
    acc = AccountConfig(
        id="o",
        address="it@contoso.test",
        provider="auto",
        imap=ImapSettings(host="outlook.office365.com"),
    )
    assert registry.provider_for(acc).id == "graph"


def test_auto_selects_yahoo_by_address():
    registry = load_plugins()
    acc = AccountConfig(id="y", address="me@yahoo.com", provider="auto")
    assert registry.provider_for(acc).id == "yahoo"


def test_auto_selects_yahoo_by_ymail():
    registry = load_plugins()
    acc = AccountConfig(id="y", address="me@ymail.com", provider="auto")
    assert registry.provider_for(acc).id == "yahoo"


def test_auto_selects_imap_for_custom_host():
    registry = load_plugins()
    acc = AccountConfig(
        id="c",
        address="me@example.com",
        provider="auto",
        imap=ImapSettings(host="imap.example.com"),
    )
    assert registry.provider_for(acc).id == "imap"


def test_explicit_imap_wins_on_gmail_address():
    registry = load_plugins()
    acc = AccountConfig(id="g", address="you@gmail.com", provider="imap")
    assert registry.provider_for(acc).id == "imap"


def test_explicit_gmail_wins():
    registry = load_plugins()
    acc = AccountConfig(id="g", address="you@gmail.com", provider="gmail")
    assert registry.provider_for(acc).id == "gmail"


def test_infer_provider_id_well_known_and_hosts():
    assert infer_provider_id("a@gmail.com") == "gmail"
    assert infer_provider_id("a@googlemail.com") == "gmail"
    assert infer_provider_id("a@outlook.com") == "graph"
    assert infer_provider_id("a@hotmail.com") == "graph"
    assert infer_provider_id("a@yahoo.com") == "yahoo"
    assert infer_provider_id("a@ymail.com") == "yahoo"
    assert infer_provider_id("it@corp.test", imap_host="imap.gmail.com") == "gmail"
    assert infer_provider_id("it@corp.test", imap_host="outlook.office365.com") == "graph"
    assert infer_provider_id("me@example.com", imap_host="imap.example.com") == "imap"
    assert infer_provider_id("a@gmail.com", explicit="imap") == "imap"
    assert infer_provider_id("me@example.com", discovered_id="gmail") == "gmail"


def test_account_resolved_provider():
    acc = AccountConfig(id="g", address="you@gmail.com", provider="auto")
    assert acc.resolved_provider() == "gmail"
    acc.provider = "imap"
    assert acc.resolved_provider() == "imap"


def _add_account(tmp_path, address, extra=None):
    argv = [
        "--home",
        str(tmp_path),
        "-o",
        "json",
        "accounts",
        "add",
        "--address",
        address,
        "--password",
        "secret",
        "--id",
        "acct",
    ]
    if extra:
        argv.extend(extra)
    assert main(argv) == 0
    return load_config(tmp_path).accounts["acct"]


def test_cli_add_auto_persists_gmail(tmp_path, monkeypatch):
    monkeypatch.setattr("mailkit.cli.main.is_running", lambda root: False)
    acc = _add_account(tmp_path, "you@gmail.com")
    assert acc.provider == "gmail"
    assert acc.imap.host == "imap.gmail.com"


def test_cli_add_auto_persists_graph(tmp_path, monkeypatch):
    monkeypatch.setattr("mailkit.cli.main.is_running", lambda root: False)
    acc = _add_account(tmp_path, "you@outlook.com")
    assert acc.provider == "graph"


def test_cli_add_auto_persists_yahoo(tmp_path, monkeypatch):
    monkeypatch.setattr("mailkit.cli.main.is_running", lambda root: False)
    acc = _add_account(tmp_path, "me@yahoo.com")
    assert acc.provider == "yahoo"


def test_cli_add_auto_persists_imap_for_custom(tmp_path, monkeypatch):
    monkeypatch.setattr("mailkit.cli.main.is_running", lambda root: False)
    acc = _add_account(
        tmp_path,
        "me@example.com",
        extra=["--no-discover", "--imap-host", "imap.example.com", "--smtp-host", "smtp.example.com"],
    )
    assert acc.provider == "imap"


def test_cli_explicit_provider_not_overridden(tmp_path, monkeypatch):
    monkeypatch.setattr("mailkit.cli.main.is_running", lambda root: False)
    acc = _add_account(tmp_path, "you@gmail.com", extra=["--provider", "imap"])
    assert acc.provider == "imap"


def test_account_from_body_auto_persists_specialized():
    from mailkit.api.routes import _account_from_body

    gmail = _account_from_body({"address": "you@gmail.com", "provider": "auto"})
    assert gmail.provider == "gmail"
    graph = _account_from_body({"address": "you@hotmail.com", "provider": "auto"})
    assert graph.provider == "graph"
    yahoo = _account_from_body({"address": "me@rocketmail.com", "provider": "auto"})
    assert yahoo.provider == "yahoo"
