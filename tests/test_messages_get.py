"""GET /v1/messages/{id} must live-fetch bodies when the list cache is headers-only."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from mailkit.api.routes import dispatch
from mailkit.config import AccountConfig, AppConfig
from mailkit.db import Store
from mailkit.models import Message
from mailkit.providers.imap_smtp import ImapSmtpProvider

FIXTURE = Path(__file__).parent / "fixtures" / "sample.eml"


def _app(store, provider, account_id="work"):
    acc = AccountConfig(id=account_id, address="ops@example.com", provider="imap")
    config = AppConfig()
    config.accounts[account_id] = acc
    runtime = SimpleNamespace(store=store, config=config, provider_for=lambda _aid: (provider, acc, {}))
    return SimpleNamespace(runtime=runtime)


def _header_msg(**extra) -> Message:
    data = dict(
        id="msg_abc",
        account_id="work",
        provider_id="imap",
        mailbox="INBOX",
        uid=17,
        native_id="17",
        subject="Invoice INV-1042 attached",
        snippet="Please review",
        body_text=None,
        body_html=None,
    )
    data.update(extra)
    return Message(**data)


def _full_msg(**extra) -> Message:
    fields = dict(
        snippet="Please review the attached invoice.",
        body_text="Please review the attached invoice.",
    )
    fields.update(extra)
    return _header_msg(**fields)


def test_get_returns_cache_when_body_is_present(tmp_path):
    store = Store(tmp_path)
    store.upsert_message(_full_msg())
    provider = MagicMock()
    result = dispatch(
        _app(store, provider),
        "GET",
        "/v1/messages/msg_abc",
        {"account": ["work"], "mailbox": ["INBOX"]},
        {},
        None,
    )
    provider.get_message.assert_not_called()
    assert result["ok"] is True
    assert result["data"]["body_text"] == "Please review the attached invoice."


def test_get_live_fetches_when_cache_is_headers_only(tmp_path):
    store = Store(tmp_path)
    store.upsert_message(_header_msg())
    provider = MagicMock()
    provider.get_message.return_value = _full_msg()
    result = dispatch(
        _app(store, provider),
        "GET",
        "/v1/messages/msg_abc",
        {"account": ["work"], "mailbox": ["INBOX"]},
        {},
        None,
    )
    provider.get_message.assert_called_once_with("INBOX", "17", peek=True)
    assert result["ok"] is True
    assert result["data"]["body_text"] == "Please review the attached invoice."
    cached = store.get_message("msg_abc")
    assert cached["body_text"] == "Please review the attached invoice."


def test_get_body_query_refetches_even_when_cached(tmp_path):
    store = Store(tmp_path)
    store.upsert_message(_full_msg(body_text="stale body"))
    provider = MagicMock()
    provider.get_message.return_value = _full_msg()
    result = dispatch(
        _app(store, provider),
        "GET",
        "/v1/messages/msg_abc",
        {"account": ["work"], "mailbox": ["INBOX"], "body": ["1"]},
        {},
        None,
    )
    provider.get_message.assert_called_once_with("INBOX", "17", peek=True)
    assert result["data"]["body_text"] == "Please review the attached invoice."
    assert store.get_message("msg_abc")["body_text"] == "Please review the attached invoice."


def test_get_live_fetches_when_missing_from_store(tmp_path):
    store = Store(tmp_path)
    provider = MagicMock()
    provider.get_message.return_value = _full_msg()
    result = dispatch(
        _app(store, provider),
        "GET",
        "/v1/messages/17",
        {"account": ["work"], "mailbox": ["INBOX"]},
        {},
        None,
    )
    provider.get_message.assert_called_once_with("INBOX", "17", peek=True)
    assert result["data"]["body_text"] == "Please review the attached invoice."
    assert store.get_message("msg_abc")["body_text"] == "Please review the attached invoice."


def test_imap_get_message_fetches_full_body_peek():
    acc = AccountConfig(id="work", address="ops@example.com")
    store = MagicMock()
    store.get_message.return_value = None
    provider = ImapSmtpProvider(acc, {}, store=store)
    raw = FIXTURE.read_bytes()
    client = MagicMock()
    client.uid.return_value = (
        "OK",
        [(b'1 (UID 17 FLAGS (\\Seen) RFC822.SIZE 120 INTERNALDATE "21-Aug-2026 10:00:00 -0500" BODY[])', raw)],
    )
    provider._select = lambda mailbox, *, readonly=True: 99
    provider._client = lambda: client
    msg = provider.get_message("INBOX", "17", peek=True)
    args = client.uid.call_args[0]
    assert args[0] == "FETCH"
    assert args[1] == "17"
    assert "BODY.PEEK[]" in args[2]
    assert "BODY.PEEK[HEADER]" not in args[2]
    assert "invoice" in (msg.body_text or "").lower()
    store.upsert_message.assert_called_once()


def test_list_messages_does_not_clobber_cached_body(tmp_path):
    store = Store(tmp_path)
    store.upsert_message(_full_msg())
    acc = AccountConfig(id="work", address="ops@example.com")
    provider = ImapSmtpProvider(acc, {}, store=store)
    provider._search_uids = lambda mailbox, criteria: [17]
    provider._fetch_summaries = lambda mailbox, uids, *, peek=True: [_header_msg()]
    listed = provider.list_messages("INBOX")
    assert listed[0].body_text == "Please review the attached invoice."
    cached = store.get_message("msg_abc")
    assert cached["body_text"] == "Please review the attached invoice."
