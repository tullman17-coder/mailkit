from types import SimpleNamespace

from mailkit.api.routes import _list_messages
from mailkit.config import AccountConfig, AppConfig
from mailkit.db import Store
from mailkit.folders import parse_list_line
from mailkit.models import Mailbox, Message


class RecordingProvider:
    def __init__(self, boxes):
        self.boxes = boxes
        self.calls = []

    def list_mailboxes(self):
        return self.boxes

    def list_messages(self, mailbox, **query):
        self.calls.append({"mailbox": mailbox, **query})
        return [
            Message(
                id="msg-flagged",
                account_id="work",
                mailbox=mailbox,
                subject="starred in inbox",
                flagged=True,
            )
        ]


def _app(tmp_path, boxes):
    provider = RecordingProvider(boxes)
    store = Store(tmp_path)
    config = AppConfig()
    acc = AccountConfig(id="work", address="a@b.com")
    config.accounts["work"] = acc
    runtime = SimpleNamespace(
        config=config,
        store=store,
        provider_for=lambda account_id: (provider, acc, {}),
    )
    return SimpleNamespace(runtime=runtime), provider


def _inbox_and_leftover_starred():
    leftover = parse_list_line(r'(\HasNoChildren) "/" "Starred"')
    assert leftover is not None
    return [
        Mailbox(name="INBOX", role="inbox"),
        leftover,
    ]


def test_saved_section_flagged_search_despite_leftover_starred_folder(tmp_path):
    app, provider = _app(tmp_path, _inbox_and_leftover_starred())
    result = _list_messages(app, {"account": "work", "mailbox": "saved"})
    assert provider.calls, "Saved section should list messages live"
    call = provider.calls[0]
    assert call["mailbox"] == "INBOX"
    assert call.get("flagged") is True
    assert result["ok"] is True
    assert result["data"][0]["subject"] == "starred in inbox"


def test_saved_section_uses_virtual_flagged_mailbox(tmp_path):
    gmail = parse_list_line(r'(\HasNoChildren \Flagged) "/" "[Gmail]/Starred"')
    assert gmail is not None
    app, provider = _app(tmp_path, [Mailbox(name="INBOX", role="inbox"), gmail])
    _list_messages(app, {"account": "work", "mailbox": "saved"})
    call = provider.calls[0]
    assert call["mailbox"] == "[Gmail]/Starred"
    assert call.get("flagged") is not True


def test_saved_section_prefers_virtual_flagged_over_heuristic_starred(tmp_path):
    leftover = parse_list_line(r'(\HasNoChildren) "/" "Starred"')
    gmail = parse_list_line(r'(\HasNoChildren \Flagged) "/" "[Gmail]/Starred"')
    assert leftover is not None and gmail is not None
    app, provider = _app(
        tmp_path,
        [Mailbox(name="INBOX", role="inbox"), leftover, gmail],
    )
    _list_messages(app, {"account": "work", "section": "saved"})
    call = provider.calls[0]
    assert call["mailbox"] == "[Gmail]/Starred"
    assert call.get("flagged") is not True


def test_inbox_section_does_not_force_flagged_filter(tmp_path):
    app, provider = _app(tmp_path, _inbox_and_leftover_starred())
    _list_messages(app, {"account": "work", "mailbox": "inbox"})
    call = provider.calls[0]
    assert call["mailbox"] == "INBOX"
    assert call.get("flagged") is None
