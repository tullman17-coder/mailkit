"""Standard IMAP + SMTP provider used by custom domains and as a fallback."""

from __future__ import annotations

import imaplib
import smtplib
import ssl
import threading
from email.utils import make_msgid
from typing import Any

from mailkit.auth.password import PasswordAuth
from mailkit.auth.xoauth2 import XOAuth2Auth
from mailkit.backoff import TokenBucket, retry
from mailkit.config import AccountConfig
from mailkit.errors import AuthError, NetworkError, NotFoundError
from mailkit.folders import Mailbox, build_folder_map, parse_list_line
from mailkit.imaputf7 import decode as utf7dec, encode as utf7enc
from mailkit.logutil import get_logger
from mailkit.models import Message
from mailkit.parser import parse_flags, parse_rfc822
from mailkit.providers.base import BaseProvider

log = get_logger("mailkit.imap")


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    return ctx


class ImapSmtpProvider(BaseProvider):
    id = "imap"

    def __init__(self, account: AccountConfig, secrets: dict[str, Any], *, store=None):
        self.account = account
        self.secrets = secrets
        self.store = store
        self.vault = None
        self._imap: imaplib.IMAP4 | None = None
        self._lock = threading.RLock()
        self._uidvalidity: dict[str, int] = {}
        self._selected: str | None = None
        self.rate = TokenBucket(rate=4.0, burst=10.0)
        self._auth = XOAuth2Auth() if account.auth in {"oauth2", "xoauth2"} else PasswordAuth()
        self.folder_map = None

    def capabilities(self) -> set[str]:
        caps = {"list", "read", "search", "move", "flags", "send", "idle"}
        return caps

    def connect(self) -> imaplib.IMAP4:
        with self._lock:
            if self._imap is not None:
                try:
                    self._imap.noop()
                    return self._imap
                except Exception:
                    self._imap = None
            host = self.account.imap.host
            port = self.account.imap.port
            if not host:
                raise NetworkError(f"Account {self.account.id} has no IMAP host")
            timeout = self.account.imap.timeout
            try:
                if self.account.imap.tls:
                    client: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, ssl_context=_ssl_context(), timeout=timeout)
                else:
                    client = imaplib.IMAP4(host, port, timeout=timeout)
                    if self.account.imap.starttls:
                        client.starttls(ssl_context=_ssl_context())
            except Exception as exc:
                raise NetworkError(f"IMAP connect failed for {self.account.id}: {exc}") from exc
            try:
                self._auth.prepare_imap(client, self.account, self.secrets)
            except AuthError:
                raise
            except Exception as exc:
                raise AuthError(f"IMAP auth failed for {self.account.id}: {exc}") from exc
            try:
                client.enable("UTF8=ACCEPT")
            except Exception:
                pass
            if self.vault is not None:
                try:
                    self.vault.put_account(self.account.id, self.secrets)
                except Exception as exc:
                    log.warning("could not persist secrets for %s: %s", self.account.id, exc)
            self._imap = client
            log.info("connected imap account=%s host=%s", self.account.id, host)
            return client

    def close(self) -> None:
        with self._lock:
            if self._imap is not None:
                try:
                    self._imap.logout()
                except Exception:
                    try:
                        self._imap.shutdown()
                    except Exception:
                        pass
                self._imap = None
                self._selected = None

    def _client(self) -> imaplib.IMAP4:
        return self.connect()

    def _quote(self, mailbox: str) -> str:
        enc = utf7enc(mailbox)
        if enc.upper() == "INBOX":
            return "INBOX"
        return f'"{enc}"'

    def _select(self, mailbox: str, *, readonly: bool = True) -> int:
        client = self._client()
        name = mailbox or "INBOX"
        typ, data = client.examine(self._quote(name)) if readonly else client.select(self._quote(name))
        if typ != "OK":
            raise NotFoundError(f"Mailbox not found: {name}", details={"mailbox": name})
        uidvalidity = 0
        try:
            status, sdata = client.status(self._quote(name), "(UIDVALIDITY)")
            if status == "OK" and sdata and sdata[0]:
                raw = sdata[0].decode() if isinstance(sdata[0], bytes) else str(sdata[0])
                if "UIDVALIDITY" in raw:
                    uidvalidity = int(raw.split("UIDVALIDITY")[1].strip(" ()"))
        except Exception:
            pass
        self._uidvalidity[name] = uidvalidity
        self._selected = name
        return uidvalidity

    def list_mailboxes(self) -> list[Mailbox]:
        self.rate.consume()
        client = self._client()
        boxes: list[Mailbox] = []
        for cmd in ("list", "lsub"):
            try:
                typ, data = getattr(client, cmd)("", "*")
            except Exception:
                continue
            if typ != "OK" or not data:
                continue
            for line in data:
                if not line:
                    continue
                parsed = parse_list_line(line)
                if parsed:
                    parsed.name = utf7dec(parsed.name)
                    parsed.account_id = self.account.id
                    boxes.append(parsed)
        if not boxes:
            boxes = [Mailbox(name="INBOX", role="inbox", account_id=self.account.id)]
        # de-dupe by name
        uniq: dict[str, Mailbox] = {}
        for box in boxes:
            uniq[box.name] = box
        listed = list(uniq.values())
        self.folder_map = build_folder_map(listed, self.account.folders.overrides())
        return self.folder_map.sections()

    def _search_uids(self, mailbox: str, criteria: list[str]) -> list[int]:
        self._select(mailbox, readonly=True)
        client = self._client()
        charset = None
        try:
            typ, data = client.uid("SEARCH", charset, *criteria)
        except Exception:
            typ, data = client.uid("SEARCH", None, *criteria)
        if typ != "OK":
            return []
        raw = data[0] or b""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        return [int(x) for x in str(raw).split() if x.isdigit()]

    def _fetch_summaries(self, mailbox: str, uids: list[int], *, peek: bool = True) -> list[Message]:
        if not uids:
            return []
        uidvalidity = self._uidvalidity.get(mailbox) or self._select(mailbox, readonly=peek)
        client = self._client()
        spec = "(FLAGS RFC822.SIZE INTERNALDATE BODY.PEEK[HEADER] BODYSTRUCTURE)" if peek else "(FLAGS RFC822.SIZE INTERNALDATE RFC822)"
        messages: list[Message] = []
        # Fetch in chunks to stay polite
        for i in range(0, len(uids), 50):
            chunk = uids[i : i + 50]
            seq = ",".join(str(u) for u in chunk)
            self.rate.consume()
            typ, data = client.uid("FETCH", seq, spec)
            if typ != "OK" or not data:
                continue
            messages.extend(self._parse_fetch(mailbox, uidvalidity, data, peek=peek))
        return messages

    def _parse_fetch(self, mailbox: str, uidvalidity: int, data, *, peek: bool) -> list[Message]:
        out: list[Message] = []
        i = 0
        while i < len(data):
            item = data[i]
            raw_bytes = b""
            flags: list[str] = []
            uid = None
            size = 0
            internal = ""
            if isinstance(item, tuple):
                meta = item[0]
                raw_bytes = item[1] if isinstance(item[1], (bytes, bytearray)) else b""
                meta_s = meta.decode("utf-8", "replace") if isinstance(meta, bytes) else str(meta)
                flags = parse_flags(meta_s)
                uid = _extract_uid(meta_s)
                size = _extract_int(meta_s, "RFC822.SIZE") or len(raw_bytes)
                internal = _extract_quoted(meta_s, "INTERNALDATE")
            elif isinstance(item, bytes):
                meta_s = item.decode("utf-8", "replace")
                uid = _extract_uid(meta_s)
                flags = parse_flags(meta_s)
            if raw_bytes:
                try:
                    msg = parse_rfc822(
                        raw_bytes,
                        account_id=self.account.id,
                        provider_id=self.id,
                        mailbox=mailbox,
                        uid=uid,
                        uidvalidity=uidvalidity,
                        flags=flags,
                        native_id=str(uid or ""),
                        size=size,
                        internal_date=internal,
                    )
                    out.append(msg)
                except Exception as exc:
                    log.warning("parse failed uid=%s: %s", uid, exc)
            i += 1
        return out

    def list_messages(self, mailbox: str, **query: Any) -> list[Message]:
        criteria = _imap_criteria(query)
        limit = int(query.get("limit") or 50)
        uids = self._search_uids(mailbox, criteria)
        uids = list(reversed(uids))[:limit]
        messages = self._fetch_summaries(mailbox, uids, peek=True)
        if self.store:
            for msg in messages:
                existing = self.store.get_message(msg.id)
                if existing:
                    msg.tags = existing.get("tags") or []
                self.store.upsert_message(msg)
        return messages

    def get_message(self, mailbox: str, native_id: str, *, peek: bool = True) -> Message:
        uidvalidity = self._select(mailbox, readonly=peek)
        client = self._client()
        spec = "(FLAGS RFC822.SIZE INTERNALDATE BODY.PEEK[])" if peek else "(FLAGS RFC822.SIZE INTERNALDATE RFC822)"
        self.rate.consume()
        typ, data = client.uid("FETCH", str(native_id), spec)
        if typ != "OK" or not data or data[0] is None:
            raise NotFoundError(f"Message {native_id} not found in {mailbox}")
        parsed = self._parse_fetch(mailbox, uidvalidity, data, peek=peek)
        if not parsed:
            raise NotFoundError(f"Message {native_id} not found in {mailbox}")
        msg = parsed[0]
        if self.store:
            existing = self.store.get_message(msg.id)
            if existing:
                msg.tags = existing.get("tags") or []
            self.store.upsert_message(msg)
        return msg

    def search(self, mailbox: str, **query: Any) -> list[Message]:
        return self.list_messages(mailbox, **query)

    def move(self, mailbox: str, native_id: str, dest: str) -> None:
        self._select(mailbox, readonly=False)
        client = self._client()
        self.rate.consume()
        try:
            typ, _ = client.uid("MOVE", str(native_id), self._quote(dest))
            if typ == "OK":
                return
        except Exception:
            pass
        typ, _ = client.uid("COPY", str(native_id), self._quote(dest))
        if typ != "OK":
            raise NetworkError(f"IMAP COPY to {dest} failed")
        client.uid("STORE", str(native_id), "+FLAGS", r"(\Deleted)")
        try:
            client.expunge()
        except Exception:
            pass

    def set_flags(self, mailbox: str, native_id: str, add=None, remove=None) -> None:
        self._select(mailbox, readonly=False)
        client = self._client()
        if add:
            flags = " ".join(_flag_token(f) for f in add)
            client.uid("STORE", str(native_id), "+FLAGS.SILENT", f"({flags})")
        if remove:
            flags = " ".join(_flag_token(f) for f in remove)
            client.uid("STORE", str(native_id), "-FLAGS.SILENT", f"({flags})")

    def uidvalidity(self, mailbox: str) -> int:
        return self._uidvalidity.get(mailbox) or self._select(mailbox, readonly=True)

    def recent_uids(self, mailbox: str, since_uid: int | None) -> list[int]:
        if since_uid:
            return self._search_uids(mailbox, ["UID", f"{int(since_uid) + 1}:*"])
        return self._search_uids(mailbox, ["ALL"])

    def send(self, from_addr: str, to: list[str], raw: bytes) -> str:
        host = self.account.smtp.host
        if not host:
            raise NetworkError(f"Account {self.account.id} has no SMTP host")
        port = self.account.smtp.port
        timeout = self.account.smtp.timeout

        @retry(times=3, retry_on=(OSError, smtplib.SMTPException, NetworkError))
        def _send() -> None:
            if self.account.smtp.tls:
                smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout, context=_ssl_context())
            else:
                smtp = smtplib.SMTP(host, port, timeout=timeout)
            try:
                smtp.ehlo()
                if self.account.smtp.starttls and not self.account.smtp.tls:
                    smtp.starttls(context=_ssl_context())
                    smtp.ehlo()
                self._auth.prepare_smtp(smtp, self.account, self.secrets)
                smtp.sendmail(from_addr, to, raw)
            finally:
                try:
                    smtp.quit()
                except Exception:
                    smtp.close()

        self.rate.consume()
        _send()
        return make_msgid()

    def open_idle_client(self) -> imaplib.IMAP4:
        """Dedicated IMAP connection for IDLE so command traffic is not blocked."""
        host = self.account.imap.host
        port = self.account.imap.port
        timeout = None  # IDLE must not use a short socket timeout
        if self.account.imap.tls:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port, ssl_context=_ssl_context(), timeout=timeout)
        else:
            client = imaplib.IMAP4(host, port, timeout=timeout)
            if self.account.imap.starttls:
                client.starttls(ssl_context=_ssl_context())
        self._auth.prepare_imap(client, self.account, self.secrets)
        return client


class ImapSmtpPlugin:
    plugin_type = "provider"
    id = "imap"
    label = "IMAP/SMTP"

    def supports(self, account: AccountConfig) -> bool:
        return account.provider in {"auto", "imap", ""} and bool(account.imap.host or account.address)

    def create(self, account: AccountConfig, secrets: dict[str, Any], *, store=None) -> ImapSmtpProvider:
        return ImapSmtpProvider(account, secrets, store=store)


def _flag_token(flag: str) -> str:
    flag = flag.lstrip("\\")
    known = {"Seen", "Flagged", "Deleted", "Draft", "Answered", "Recent"}
    token = flag if flag[:1].isupper() else flag.capitalize()
    if token.capitalize() in known or token in known:
        return "\\" + token.capitalize()
    return "\\" + token if not flag.startswith("$") else flag


def _imap_criteria(query: dict[str, Any]) -> list[str]:
    crit: list[str] = []
    if query.get("unread"):
        crit.append("UNSEEN")
    if query.get("flagged"):
        crit.append("FLAGGED")
    if query.get("since"):
        crit.extend(["SINCE", _imap_date(query["since"])])
    if query.get("before"):
        crit.extend(["BEFORE", _imap_date(query["before"])])
    if query.get("from_"):
        crit.extend(["FROM", str(query["from_"])])
    if query.get("to"):
        crit.extend(["TO", str(query["to"])])
    if query.get("subject"):
        crit.extend(["SUBJECT", str(query["subject"])])
    if query.get("text"):
        crit.extend(["TEXT", str(query["text"])])
    if query.get("uid"):
        crit.extend(["UID", str(query["uid"])])
    return crit or ["ALL"]


def _imap_date(value: str) -> str:
    # IMAP dates look like 01-Jan-2024
    value = value[:10]
    try:
        from datetime import date

        y, m, d = value.split("-")
        dt = date(int(y), int(m), int(d))
        return dt.strftime("%d-%b-%Y")
    except Exception:
        return value


def _extract_uid(meta: str) -> int | None:
    import re

    m = re.search(r"UID (\d+)", meta)
    return int(m.group(1)) if m else None


def _extract_int(meta: str, key: str) -> int | None:
    import re

    m = re.search(rf"{key} (\d+)", meta)
    return int(m.group(1)) if m else None


def _extract_quoted(meta: str, key: str) -> str:
    import re

    m = re.search(rf'{key} "([^"]*)"', meta)
    return m.group(1) if m else ""
