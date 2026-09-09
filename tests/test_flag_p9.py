from pathlib import Path
from types import SimpleNamespace

from mailkit.api.http import App
from mailkit.api.routes import dispatch
from mailkit.config import AccountConfig, AppConfig
from mailkit.db import Store
from mailkit.models import Address, Mailbox, Message


class FakeProvider:
    def __init__(self):
        self.moved = []
        self.sent = []
        self.flags = []

    def list_mailboxes(self):
        return [
            Mailbox(name="INBOX", role="inbox"),
            Mailbox(name="[Gmail]/All Mail", role="archives"),
            Mailbox(name="[Gmail]/Sent Mail", role="sent"),
        ]

    def move(self, mailbox, native_id, dest):
        self.moved.append({"mailbox": mailbox, "native_id": native_id, "dest": dest})

    def send(self, from_addr, to, raw):
        self.sent.append(raw)
        return "<new@mailkit>"

    def set_flags(self, mailbox, native_id, add=None, remove=None):
        self.flags.append({"mailbox": mailbox, "native_id": native_id, "add": add or [], "remove": remove or []})


def _app(tmp_path, provider):
    store = Store(tmp_path)
    acc = AccountConfig(id="work", address="me@example.com")
    runtime = SimpleNamespace(
        store=store,
        config=AppConfig(accounts={"work": acc}),
        provider_for=lambda _id: (provider, acc, {}),
        root=tmp_path,
    )
    app = App(runtime=runtime, bus=None, supervisor=None, webhooks=None, token="t", started_at="now")
    return app, store


def _cached(store):
    msg = Message(
        id="msg_cached",
        account_id="work",
        provider_id="gmail",
        mailbox="INBOX",
        native_id="17",
        uid=17,
        subject="Hello",
        from_=[Address("sender@example.com", "Sender")],
        message_id="orig@example.com",
    )
    store.upsert_message(msg)
    return msg


def test_move_archives_role_resolves_gmail_all_mail(tmp_path):
    provider = FakeProvider()
    app, store = _app(tmp_path, provider)
    _cached(store)
    result = dispatch(app, "POST", "/v1/messages/msg_cached/move", {}, {"mailbox": "archives"}, None)
    assert result["ok"] is True
    assert result["data"]["mailbox"] == "[Gmail]/All Mail"
    assert provider.moved == [{"mailbox": "INBOX", "native_id": "17", "dest": "[Gmail]/All Mail"}]


def test_reply_uses_cached_id_and_sets_in_reply_to(tmp_path):
    provider = FakeProvider()
    app, store = _app(tmp_path, provider)
    _cached(store)
    result = dispatch(
        app,
        "POST",
        "/v1/reply",
        {},
        {"account": "work", "id": "msg_cached", "body": "thanks"},
        None,
    )
    assert result["ok"] is True
    assert result["data"]["in_reply_to"] == "orig@example.com"
    raw = provider.sent[0]
    assert b"In-Reply-To:" in raw
    assert b"orig@example.com" in raw
    assert provider.flags[0]["add"] == ["Answered"]


def test_desktop_reply_posts_v1_reply_and_archive_uses_role():
    src = (Path(__file__).resolve().parents[1] / "desktop" / "js" / "app.js").read_text()
    assert 'api("POST", "/v1/reply"' in src
    assert "state.replyId" in src
    assert '{ mailbox: "archives" }' in src
    assert '{ mailbox: "Archive" }' not in src
    assert 'api("POST", `/v1/messages/${state.current.id}/flag`' in src
