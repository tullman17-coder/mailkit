from pathlib import Path

from mailkit.parser import parse_flags, parse_rfc822

FIXTURE = Path(__file__).parent / "fixtures" / "sample.eml"


def test_parse_sample_eml():
    raw = FIXTURE.read_bytes()
    msg = parse_rfc822(
        raw,
        account_id="work",
        provider_id="imap",
        mailbox="INBOX",
        uid=17,
        uidvalidity=99,
        flags=["Seen", "Flagged"],
    )
    assert msg.account_id == "work"
    assert msg.mailbox == "INBOX"
    assert msg.uid == 17
    assert "Invoice INV-1042" in msg.subject
    assert msg.from_[0].address == "billing@vendor.example"
    assert msg.to[0].address == "ops@example.com"
    assert msg.flagged is True
    assert msg.unread is False
    assert msg.has_attachments is True
    assert "application/pdf" in msg.attachment_types
    assert msg.attachments[0].filename == "invoice-1042.pdf"
    assert "invoice" in (msg.body_text or "")
    assert msg.thread_id.startswith("thr_")
    assert msg.id.startswith("msg_")


def test_parse_flags_from_imap_meta():
    flags = parse_flags(b"1 (UID 17 FLAGS (\\Seen \\Flagged) RFC822.SIZE 120)")
    assert flags == ["Seen", "Flagged"]
    extras = {"1", "UID", "17", "FLAGS", "RFC822.SIZE", "RFC822", "SIZE", "120"}
    assert extras.isdisjoint(flags)


def test_parse_flags_from_tokenized_fetch():
    flags = parse_flags(["1", "UID", "17", "FLAGS", "Seen", "Flagged", "RFC822.SIZE", "120"])
    assert [f.lower() for f in flags] == ["seen", "flagged"]
    extras = {"1", "UID", "17", "FLAGS", "RFC822.SIZE", "120"}
    assert extras.isdisjoint(set(flags))


def test_parse_flags_empty_group():
    assert parse_flags(b"1 (UID 17 FLAGS () RFC822.SIZE 120)") == []


def test_parse_flags_plain_list():
    assert parse_flags(["\\Seen", "\\Draft"]) == ["Seen", "Draft"]
