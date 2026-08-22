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
    assert "Empire Today claim #4421" in msg.subject
    assert msg.from_[0].address == "claims@empiretoday.com"
    assert msg.to[0].address == "ops@example.com"
    assert msg.flagged is True
    assert msg.unread is False
    assert msg.has_attachments is True
    assert "application/pdf" in msg.attachment_types
    assert msg.attachments[0].filename == "claim-4421.pdf"
    assert "claim packet" in (msg.body_text or "")
    assert msg.thread_id.startswith("thr_")
    assert msg.id.startswith("msg_")


def test_parse_flags_from_imap_meta():
    flags = parse_flags(b"1 (UID 17 FLAGS (\\Seen \\Flagged) RFC822.SIZE 120)")
    assert "Seen" in flags or "seen" in [f.lower() for f in flags]
    assert any(f.lower() == "flagged" for f in flags)
