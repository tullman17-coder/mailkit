from mailkit.folders import (
    build_folder_map,
    parse_list_line,
    resolve_mailbox_name,
    role_from_attrs,
    role_from_name,
    section_role,
)
from mailkit.models import Mailbox


def test_special_use_sent():
    box = parse_list_line(r'(\HasNoChildren \Sent) "/" "Sent Items"')
    assert box is not None
    assert box.role == "sent"
    assert box.name == "Sent Items"


def test_gmail_xlist_names():
    starred = parse_list_line(r'(\HasNoChildren \Flagged) "/" "[Gmail]/Starred"')
    all_mail = parse_list_line(r'(\HasNoChildren \All) "/" "[Gmail]/All Mail"')
    drafts = parse_list_line(r'(\HasNoChildren \Drafts) "/" "[Gmail]/Drafts"')
    assert starred.role == "saved"
    assert all_mail.role == "archives"
    assert drafts.role == "drafts"


def test_name_heuristics_without_special_use():
    assert role_from_name("INBOX") == "inbox"
    assert role_from_name("Sent Mail") == "sent"
    assert role_from_name("Deleted Items") == "trash"
    assert role_from_name("Junk Email") == "junk"
    assert role_from_name("Projects/Client") == "custom"


def test_inbox_attr_and_name():
    assert role_from_attrs("INBOX", []) == "inbox"
    box = parse_list_line(r'(\HasNoChildren) "/" INBOX')
    assert box.role == "inbox"


def test_overrides_win_and_section_order():
    listed = [
        Mailbox(name="INBOX", role="inbox"),
        Mailbox(name="Sent", role="sent"),
        Mailbox(name="Archive", role="archives"),
        Mailbox(name="Starred", role="saved"),
        Mailbox(name="Drafts", role="drafts"),
        Mailbox(name="Receipts", role="custom"),
    ]
    fmap = build_folder_map(listed, {"archives": "Receipts"})
    assert fmap.by_role["archives"].name == "Receipts"
    sections = [m.role for m in fmap.sections()[:5]]
    assert sections == ["inbox", "saved", "sent", "drafts", "archives"]


def test_yahoo_bulk_is_junk():
    assert role_from_name("Bulk Mail") == "junk"


def test_resolve_archives_uses_gmail_all_mail():
    listed = [
        Mailbox(name="INBOX", role="inbox"),
        Mailbox(name="[Gmail]/All Mail", role="archives"),
        Mailbox(name="[Gmail]/Sent Mail", role="sent"),
    ]
    assert section_role("archives") == "archives"
    assert section_role("Archive") == "archives"
    assert resolve_mailbox_name(listed, "archives") == "[Gmail]/All Mail"
    assert resolve_mailbox_name(listed, "Archive") == "[Gmail]/All Mail"
    assert resolve_mailbox_name(listed, "Receipts") == "Receipts"
    assert resolve_mailbox_name(listed, "inbox") == "INBOX"
