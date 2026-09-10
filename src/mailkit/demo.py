"""Seed a local mailbox so the desktop UI can be demonstrated without IMAP."""

from __future__ import annotations

from pathlib import Path

from mailkit.config import AccountConfig, AppConfig, load_config, save_config
from mailkit.db import Store
from mailkit.ids import message_id as make_message_id
from mailkit.models import Address, Message, utcnow
from mailkit.vault import Vault


DEMO_ACCOUNT_ID = "demo"


def seed_demo(root: Path) -> AccountConfig:
    """Create the demo account and a handful of messages if they are missing."""
    cfg = load_config(root)
    acc = cfg.accounts.get(DEMO_ACCOUNT_ID)
    if acc is None:
        acc = AccountConfig(
            id=DEMO_ACCOUNT_ID,
            name="Demo",
            address="you@mailkit.local",
            provider="local",
            auth="password",
            enabled=True,
            watch="local",
        )
        cfg.accounts[DEMO_ACCOUNT_ID] = acc
        cfg.defaults.account = DEMO_ACCOUNT_ID
        save_config(cfg, root)
    vault = Vault(root)
    if not vault.get_account(DEMO_ACCOUNT_ID).get("password"):
        vault.put_account(DEMO_ACCOUNT_ID, {"password": "demo"})
    store = Store(root)
    try:
        existing = store.list_messages(account_id=DEMO_ACCOUNT_ID, limit=1)
        if existing:
            return acc
        _seed_messages(store, acc)
    finally:
        store.close()
    return acc


def _seed_messages(store: Store, acc: AccountConfig) -> None:
    now = utcnow()
    samples = [
        Message(
            id=make_message_id(acc.id, "INBOX", "local", "1"),
            account_id=acc.id,
            provider_id="local",
            mailbox="INBOX",
            uid=1,
            native_id="1",
            message_id="welcome@mailkit.local",
            date=now,
            subject="Welcome to Mailkit",
            from_=[Address("mailkit@local", "Mailkit")],
            to=[Address(acc.address, "You")],
            unread=True,
            flagged=False,
            flags=[],
            snippet="Your local engine is running. Star, archive, and reply from this window.",
            body_text=(
                "This is a local demo mailbox — no IMAP login required.\n\n"
                "Try starring this message, opening Saved, then Unstar.\n"
                "Archive moves it to the archives role. Reply uses /v1/reply."
            ),
        ),
        Message(
            id=make_message_id(acc.id, "INBOX", "local", "2"),
            account_id=acc.id,
            provider_id="local",
            mailbox="INBOX",
            uid=2,
            native_id="2",
            message_id="invoice@acme.test",
            date=now,
            subject="Invoice INV-1042",
            from_=[Address("billing@acme.test", "Acme Billing")],
            to=[Address(acc.address, "You")],
            unread=True,
            flagged=True,
            flags=["Flagged"],
            snippet="Please pay invoice INV-1042 by Friday.",
            body_text="Invoice INV-1042 is attached in spirit. Amount due: $42.00.\n\nThanks,\nAcme Billing",
        ),
        Message(
            id=make_message_id(acc.id, "INBOX", "local", "3"),
            account_id=acc.id,
            provider_id="local",
            mailbox="INBOX",
            uid=3,
            native_id="3",
            message_id="review@zermo.org",
            date=now,
            subject="Design review notes",
            from_=[Address("alex@zermo.org", "Alex")],
            to=[Address(acc.address, "You")],
            unread=False,
            flagged=False,
            flags=["Seen"],
            snippet="The Hallmark layout is holding. A few notes on the reader pane.",
            body_text="Looks good. The reader pane should show the full body, and Star should toggle.\n\n— Alex",
        ),
        Message(
            id=make_message_id(acc.id, "Sent", "local", "4"),
            account_id=acc.id,
            provider_id="local",
            mailbox="Sent",
            uid=4,
            native_id="4",
            message_id="sent@mailkit.local",
            date=now,
            subject="Re: kicking the tires",
            from_=[Address(acc.address, "You")],
            to=[Address("alex@zermo.org", "Alex")],
            unread=False,
            flagged=False,
            flags=["Seen", "Answered"],
            answered=True,
            snippet="Sent from the local demo mailbox.",
            body_text="Sent from Mailkit. This copy lives in Sent.",
        ),
    ]
    for msg in samples:
        store.upsert_message(msg)
