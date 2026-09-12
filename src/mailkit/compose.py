"""Build RFC 5322 messages for send and reply."""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Iterable


def build_message(
    *,
    from_addr: str,
    to: Iterable[str],
    subject: str,
    body: str,
    html: str | None = None,
    cc: Iterable[str] | None = None,
    bcc: Iterable[str] | None = None,
    in_reply_to: str | None = None,
    references: Iterable[str] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    if in_reply_to:
        msg["In-Reply-To"] = _bracket(in_reply_to)
    if references:
        msg["References"] = " ".join(_bracket(r) for r in references)
    if extra_headers:
        for k, v in extra_headers.items():
            msg[k] = v
    # Blind recipients belong only in the SMTP envelope, never in transmitted MIME.
    del msg["Bcc"]
    if html:
        msg.set_content(body or "")
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(body or "")
    return msg


def reply_subject(subject: str) -> str:
    s = subject or ""
    if s.lower().startswith("re:"):
        return s
    return f"Re: {s}"


def _bracket(value: str) -> str:
    value = value.strip()
    if value.startswith("<"):
        return value
    return f"<{value}>"
