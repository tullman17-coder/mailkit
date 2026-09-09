from mailkit.folders import (
    build_folder_map,
    is_virtual_flagged_mailbox,
    parse_list_line,
    resolve_mailbox_name,
    resolve_saved_view,
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
    assert role_from_name("Starred") == "saved"
    assert role_from_name("Flagged") == "saved"
    assert role_from_name("[Gmail]/Starred") == "saved"
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


def test_leftover_starred_name_is_not_virtual_flagged():
    leftover = parse_list_line(r'(\HasNoChildren) "/" "Starred"')
    gmail = parse_list_line(r'(\HasNoChildren \Flagged) "/" "[Gmail]/Starred"')
    inbox = parse_list_line(r'(\HasNoChildren) "/" INBOX')
    assert leftover is not None and gmail is not None and inbox is not None
    assert leftover.role == "saved"
    assert gmail.role == "saved"
    assert is_virtual_flagged_mailbox(leftover) is False
    assert is_virtual_flagged_mailbox(gmail) is True
    assert is_virtual_flagged_mailbox(inbox) is False


def test_heuristic_gmail_starred_name_is_not_virtual_flagged():
    named = Mailbox(name="[Gmail]/Starred", role="saved", flags=["\\HasNoChildren"])
    assert named.role == "saved"
    assert is_virtual_flagged_mailbox(named) is False


def test_saved_view_uses_flagged_search_when_only_heuristic_starred_exists():
    boxes = [
        Mailbox(name="INBOX", role="inbox"),
        Mailbox(name="Starred", role="saved", flags=["\\HasNoChildren"]),
    ]
    folder, apply_flagged = resolve_saved_view(boxes)
    assert folder == "INBOX"
    assert apply_flagged is True


def test_saved_view_uses_virtual_flagged_special_use_mailbox():
    boxes = [
        Mailbox(name="INBOX", role="inbox"),
        parse_list_line(r'(\HasNoChildren \Flagged) "/" "[Gmail]/Starred"'),
    ]
    folder, apply_flagged = resolve_saved_view(boxes)
    assert folder == "[Gmail]/Starred"
    assert apply_flagged is False


def test_saved_view_prefers_virtual_flagged_over_leftover_starred():
    leftover = parse_list_line(r'(\HasNoChildren) "/" "Starred"')
    gmail = parse_list_line(r'(\HasNoChildren \Flagged) "/" "[Gmail]/Starred"')
    boxes = [
        Mailbox(name="INBOX", role="inbox"),
        leftover,
        gmail,
    ]
    folder, apply_flagged = resolve_saved_view(boxes)
    assert folder == "[Gmail]/Starred"
    assert apply_flagged is False


def test_saved_view_without_saved_mailbox_searches_inbox_flagged():
    folder, apply_flagged = resolve_saved_view([Mailbox(name="INBOX", role="inbox")])
    assert folder == "INBOX"
    assert apply_flagged is True


def test_folder_map_prefers_special_use_saved_over_heuristic_name():
    leftover = parse_list_line(r'(\HasNoChildren) "/" "Starred"')
    gmail = parse_list_line(r'(\HasNoChildren \Flagged) "/" "[Gmail]/Starred"')
    listed = [
        Mailbox(name="INBOX", role="inbox"),
        leftover,
        gmail,
    ]
    fmap = build_folder_map(listed)
    assert fmap.by_role["saved"].name == "[Gmail]/Starred"
    assert leftover.role == "saved"
    sections = fmap.sections()
    assert sections[0].role == "inbox"
    assert sections[1].name == "[Gmail]/Starred"
    assert sections[1].role == "saved"


def test_leftover_starred_still_occupies_saved_section_when_alone():
def test_leftover_starred_still_occupies_saved_section_when_alone():
    leftover = parse_list_line(r'(\HasNoChildren) "/" "Starred"')
    fmap = build_folder_map([Mailbox(name="INBOX", role="inbox"), leftover])
    assert fmap.by_role["saved"].name == "Starred"
    folder, apply_flagged = resolve_saved_view(fmap.sections())
    assert folder == "INBOX"
    assert apply_flagged is True


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
