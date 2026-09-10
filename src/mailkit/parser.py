"""RFC 5322 / MIME parsing into the normalized Message model."""

from __future__ import annotations

import re
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message as EmailMsg
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from mailkit.ids import message_id as make_message_id, thread_id_from_headers
from mailkit.models import Address, AttachmentMeta, Message


def decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def parse_addresses(value: str | None) -> list[Address]:
    if not value:
        return []
    out: list[Address] = []
    for name, addr in getaddresses([value]):
        if not addr:
            continue
        out.append(Address(address=addr, name=decode_header_value(name)))
    return out


def _walk_bodies(msg: EmailMsg) -> tuple[str, str, list[AttachmentMeta]]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[AttachmentMeta] = []
    for part in msg.walk():
        ctype = part.get_content_type()
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename() or ""
        if disp == "attachment" or filename:
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                AttachmentMeta(
                    filename=decode_header_value(filename),
                    content_type=ctype,
                    size=len(payload),
                )
            )
            continue
        if part.is_multipart():
            continue
        if ctype == "text/plain":
            text_parts.append(_decode_part(part))
        elif ctype == "text/html":
            html_parts.append(_decode_part(part))
    return "\n".join(text_parts).strip(), "\n".join(html_parts).strip(), attachments


def _decode_part(part: EmailMsg) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        data = part.get_payload()
        return data if isinstance(data, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


_TAG_RE = re.compile(r"<[^>]+>")


def snippet_of(text: str, html: str, limit: int = 240) -> str:
    source = text or _TAG_RE.sub(" ", html)
    compact = re.sub(r"\s+", " ", source).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def parse_rfc822(
    raw: bytes,
    *,
    account_id: str,
    provider_id: str,
    mailbox: str,
    uid: int | None = None,
    uidvalidity: int | None = None,
    flags: list[str] | None = None,
    labels: list[str] | None = None,
    native_id: str = "",
    size: int = 0,
    internal_date: str = "",
) -> Message:
    parsed: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)
    flags = [f.lstrip("\\") for f in (flags or [])]
    flag_set = {f.lower() for f in flags}
    text, html, attachments = _walk_bodies(parsed)
    mid = decode_header_value(parsed.get("Message-ID")).strip("<>")
    refs_raw = decode_header_value(parsed.get("References"))
    references = [r.strip("<>") for r in refs_raw.split() if r.strip()]
    in_reply_to = decode_header_value(parsed.get("In-Reply-To")).strip("<>")
    date_hdr = parsed.get("Date")
    date_iso = ""
    if date_hdr:
        try:
            date_iso = parsedate_to_datetime(date_hdr).isoformat()
        except (TypeError, ValueError, OverflowError):
            date_iso = str(date_hdr)
    native = native_id or (str(uid) if uid is not None else mid)
    msg_id = make_message_id(account_id, mailbox, uidvalidity or 0, native)
    from_addrs = parse_addresses(parsed.get("From"))
    to_addrs = parse_addresses(parsed.get("To"))
    cc_addrs = parse_addresses(parsed.get("Cc"))
    return Message(
        id=msg_id,
        account_id=account_id,
        provider_id=provider_id,
        mailbox=mailbox,
        uid=uid,
        uidvalidity=uidvalidity,
        native_id=str(native),
        message_id=mid,
        thread_id=thread_id_from_headers(mid, refs_raw, in_reply_to),
        in_reply_to=in_reply_to,
        references=references,
        date=date_iso,
        subject=decode_header_value(parsed.get("Subject")),
        from_=from_addrs,
        to=to_addrs,
        cc=cc_addrs,
        bcc=parse_addresses(parsed.get("Bcc")),
        reply_to=parse_addresses(parsed.get("Reply-To")),
        flags=flags,
        labels=labels or [],
        unread="seen" not in flag_set,
        flagged="flagged" in flag_set,
        draft="draft" in flag_set,
        answered="answered" in flag_set,
        has_attachments=bool(attachments),
        attachments=attachments,
        attachment_types=sorted({a.content_type for a in attachments}),
        snippet=snippet_of(text, html),
        body_text=text or None,
        body_html=html or None,
        size=size or len(raw),
        internal_date=internal_date,
    )


_FLAGS_PAREN_RE = re.compile(r"\bFLAGS\s*\(([^)]*)\)", re.IGNORECASE)
_FLAGS_KEYWORD_RE = re.compile(r"\bFLAGS\b", re.IGNORECASE)
_FETCH_ATOM_RE = re.compile(
    r"\b(?:UID|RFC822(?:\.\w+)?|INTERNALDATE|ENVELOPE|BODY(?:STRUCTURE|\.PEEK)?|"
    r"MODSEQ|X-GM-\w+|BINARY(?:\.\w+)?)\b",
    re.IGNORECASE,
)


def _flag_tokens(text: str) -> list[str]:
    return [t.lstrip("\\") for t in text.replace("(", " ").replace(")", " ").split() if t]


def _parse_flags_text(text: str) -> list[str]:
    """Keep only tokens from the IMAP FLAGS (...) group, not the rest of FETCH meta."""
    grouped = _FLAGS_PAREN_RE.search(text)
    if grouped:
        return _flag_tokens(grouped.group(1))
    keyword = _FLAGS_KEYWORD_RE.search(text)
    if keyword:
        rest = text[keyword.end() :]
        stop = _FETCH_ATOM_RE.search(rest)
        return _flag_tokens(rest if stop is None else rest[: stop.start()])
    return _flag_tokens(text)


def parse_flags(raw_flags: str | bytes | list | None) -> list[str]:
    if raw_flags is None:
        return []
    if isinstance(raw_flags, list):
        chunks: list[str] = []
        for item in raw_flags:
            if isinstance(item, bytes):
                item = item.decode("utf-8", errors="replace")
            chunks.append(str(item))
        return _parse_flags_text(" ".join(chunks))
    if isinstance(raw_flags, bytes):
        raw_flags = raw_flags.decode("utf-8", errors="replace")
    return _parse_flags_text(str(raw_flags))
