"""Standard IMAP + SMTP provider used by custom domains and as a fallback."""

from __future__ import annotations

import imaplib
import smtplib
import socket
import ssl
import threading
import time
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


def _shutdown_sock(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass
    try:
        sock.close()
    except Exception:
        pass


def _create_connection(host: str, port: int, timeout: float | None, stop: threading.Event | None) -> socket.socket:
    overall = 30.0 if timeout is None else float(timeout)
    if stop is None:
        return socket.create_connection((host, port), timeout=overall)
    deadline = time.time() + overall
    last_exc: Exception | None = None
    while True:
        if stop.is_set():
            raise NetworkError("IMAP connect aborted")
        remaining = deadline - time.time()
        if remaining <= 0:
            raise TimeoutError("timed out") from last_exc
        try:
            return socket.create_connection((host, port), timeout=min(1.0, remaining))
        except TimeoutError as exc:
            last_exc = exc


class _AbortableIMAP4(imaplib.IMAP4):
    def __init__(self, host: str, port: int, timeout: float | None, provider: "ImapSmtpProvider"):
        self._mailkit_provider = provider
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout):
        sock = _create_connection(self.host, self.port, timeout, self._mailkit_provider._stop)
        self._mailkit_provider._register_socket(sock)
        return sock


class _AbortableIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, port: int, ssl_context, timeout: float | None, provider: "ImapSmtpProvider"):
        self._mailkit_provider = provider
        super().__init__(host, port, ssl_context=ssl_context, timeout=timeout)

    def _create_socket(self, timeout):
        sock = _create_connection(self.host, self.port, timeout, self._mailkit_provider._stop)
        self._mailkit_provider._register_socket(sock)
        try:
            wrapped = self.ssl_context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            _shutdown_sock(sock)
            raise
        self._mailkit_provider._register_socket(wrapped)
        return wrapped


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
        self._imap_caps: set[str] = set()
        self._stop: threading.Event | None = None
        self._idle: imaplib.IMAP4 | None = None
        self._pending: list[socket.socket] = []
        self._abort_thread: threading.Thread | None = None

    def imap_capability_tokens(self) -> set[str]:
        return set(self._imap_caps)

    def set_stop(self, stop: threading.Event | None) -> None:
        self._stop = stop
        if stop is None:
            return
        existing = self._abort_thread
        if existing is not None and existing.is_alive():
            return

        def abort():
            stop.wait()
            self.close()

        self._abort_thread = threading.Thread(target=abort, name=f"mailkit-imap-abort-{self.account.id}", daemon=True)
        self._abort_thread.start()

    def _register_socket(self, sock: socket.socket) -> None:
        self._pending.append(sock)
        if self._stop is not None and self._stop.is_set():
            _shutdown_sock(sock)
            raise NetworkError(f"IMAP connect aborted for {self.account.id}")

    def capabilities(self) -> set[str]:
        caps = {"list", "read", "search", "move", "flags", "send"}
        if "IDLE" in self._imap_caps:
            caps.add("idle")
        return caps

    def connect(self) -> imaplib.IMAP4:
        with self._lock:
            if self._imap is not None:
                try:
                    self._imap.noop()
                    return self._imap
                except Exception:
                    self._imap = None
                    self._imap_caps = set()
            if self._stop is not None and self._stop.is_set():
                raise NetworkError(f"IMAP connect aborted for {self.account.id}")
            host = self.account.imap.host
            port = self.account.imap.port
            if not host:
                raise NetworkError(f"Account {self.account.id} has no IMAP host")
            timeout = self.account.imap.timeout
        try:
            if self.account.imap.tls:
                client: imaplib.IMAP4 = _AbortableIMAP4SSL(host, port, _ssl_context(), timeout, self)
            else:
                client = _AbortableIMAP4(host, port, timeout, self)
                if self.account.imap.starttls:
                    client.starttls(ssl_context=_ssl_context())
        except NetworkError:
            raise
        except Exception as exc:
            if self._stop is not None and self._stop.is_set():
                raise NetworkError(f"IMAP connect aborted for {self.account.id}") from exc
            raise NetworkError(f"IMAP connect failed for {self.account.id}: {exc}") from exc
        if self._stop is not None and self._stop.is_set():
            try:
                _shutdown_sock(client.socket())
            except Exception:
                pass
            raise NetworkError(f"IMAP connect aborted for {self.account.id}")
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
        self._imap_caps = _capability_tokens(client)
        if self.vault is not None:
            try:
                self.vault.put_account(self.account.id, self.secrets)
            except Exception as exc:
                log.warning("could not persist secrets for %s: %s", self.account.id, exc)
        with self._lock:
            self._imap = client
        log.info("connected imap account=%s host=%s", self.account.id, host)
        return client

    def close(self) -> None:
        pending = list(self._pending)
        self._pending.clear()
        for sock in pending:
            _shutdown_sock(sock)
        with self._lock:
            for attr in ("_imap", "_idle"):
                client = getattr(self, attr)
                if client is None:
                    continue
                try:
                    client.logout()
                except Exception:
                    try:
                        sock = client.socket()
                    except Exception:
                        sock = None
                    _shutdown_sock(sock)
                    try:
                        client.shutdown()
                    except Exception:
                        pass
                setattr(self, attr, None)
            self._selected = None
            self._imap_caps = set()

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
                    if not msg.body_text:
                        msg.body_text = existing.get("body_text")
                    if not msg.body_html:
                        msg.body_html = existing.get("body_html")
                self.store.upsert_message(msg)
        return messages

    def get_message(self, mailbox: str, native_id: str, *, peek: bool = True) -> Message:
        uid = str(native_id)
        folder = mailbox
        if self.store:
            cached = self.store.get_message(str(native_id))
            if cached:
                uid = str(cached.get("native_id") or cached.get("uid") or native_id)
                folder = cached.get("mailbox") or mailbox
        uidvalidity = self._select(folder, readonly=peek)
        client = self._client()
        spec = "(FLAGS RFC822.SIZE INTERNALDATE BODY.PEEK[])" if peek else "(FLAGS RFC822.SIZE INTERNALDATE RFC822)"
        self.rate.consume()
        typ, data = client.uid("FETCH", uid, spec)
        if typ != "OK" or not data or data[0] is None:
            raise NotFoundError(f"Message {uid} not found in {folder}")
        parsed = self._parse_fetch(folder, uidvalidity, data, peek=peek)
        if not parsed:
            raise NotFoundError(f"Message {uid} not found in {folder}")
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
        uid = _require_uid(native_id)
        self._select(mailbox, readonly=False)
        client = self._client()
        if add:
            flags = " ".join(_flag_token(f) for f in add)
            typ, _ = client.uid("STORE", uid, "+FLAGS.SILENT", f"({flags})")
            if typ != "OK":
                raise NetworkError(f"IMAP STORE failed for UID {uid}")
        if remove:
            flags = " ".join(_flag_token(f) for f in remove)
            typ, _ = client.uid("STORE", uid, "-FLAGS.SILENT", f"({flags})")
            if typ != "OK":
                raise NetworkError(f"IMAP STORE failed for UID {uid}")

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
        if self._stop is not None and self._stop.is_set():
            raise NetworkError(f"IMAP connect aborted for {self.account.id}")
        host = self.account.imap.host
        port = self.account.imap.port
        timeout = self.account.imap.timeout
        try:
            if self.account.imap.tls:
                client: imaplib.IMAP4 = _AbortableIMAP4SSL(host, port, _ssl_context(), timeout, self)
            else:
                client = _AbortableIMAP4(host, port, timeout, self)
                if self.account.imap.starttls:
                    client.starttls(ssl_context=_ssl_context())
        except NetworkError:
            raise
        except Exception as exc:
            if self._stop is not None and self._stop.is_set():
                raise NetworkError(f"IMAP connect aborted for {self.account.id}") from exc
            raise NetworkError(f"IMAP connect failed for {self.account.id}: {exc}") from exc
        if self._stop is not None and self._stop.is_set():
            try:
                _shutdown_sock(client.socket())
            except Exception:
                pass
            raise NetworkError(f"IMAP connect aborted for {self.account.id}")
        self._auth.prepare_imap(client, self.account, self.secrets)
        self._imap_caps = _capability_tokens(client)
        try:
            client.sock.settimeout(None)
        except Exception:
            pass
        self._idle = client
        return client


class ImapSmtpPlugin:
    plugin_type = "provider"
    id = "imap"
    label = "IMAP/SMTP"

    def supports(self, account: AccountConfig) -> bool:
        return account.provider in {"auto", "imap", ""} and bool(account.imap.host or account.address)

    def create(self, account: AccountConfig, secrets: dict[str, Any], *, store=None) -> ImapSmtpProvider:
        return ImapSmtpProvider(account, secrets, store=store)


def _capability_tokens(client) -> set[str]:
    """Parse IMAP CAPABILITY tokens from greeting plus a post-auth probe."""
    tokens: set[str] = set()

    def _add(value) -> None:
        if value is None:
            return
        if isinstance(value, (bytes, bytearray)):
            text = value.decode("utf-8", "replace")
        else:
            text = str(value)
        tokens.update(part.upper() for part in text.split() if part)

    raw = getattr(client, "capabilities", None)
    if raw:
        if isinstance(raw, (str, bytes, bytearray)):
            _add(raw)
        else:
            try:
                for item in raw:
                    _add(item)
            except TypeError:
                _add(raw)
    try:
        typ, data = client.capability()
    except Exception:
        return tokens
    if typ == "OK" and data:
        for item in data:
            _add(item)
    return tokens


def _require_uid(native_id) -> str:
    if native_id is None:
        raise NotFoundError("Message has no IMAP UID")
    uid = str(native_id).strip()
    if not uid or uid.lower() in {"none", "null"}:
        raise NotFoundError("Message has no IMAP UID")
    return uid


def _flag_token(flag: str) -> str:
    flag = flag.lstrip("\\")
    known = {"Seen", "Flagged", "Deleted", "Draft", "Answered", "Recent"}
    token = flag if flag[:1].isupper() else flag.capitalize()
    if token.capitalize() in known or token in known:
        return "\\" + token.capitalize()
    return "\\" + token if not flag.startswith("$") else flag


def _imap_criteria(query: dict[str, Any]) -> list[str]:
    crit: list[str] = []
    unread = query.get("unread")
    if unread is not None:
        crit.append("UNSEEN" if unread else "SEEN")
    flagged = query.get("flagged")
    if flagged is not None:
        crit.append("FLAGGED" if flagged else "UNFLAGGED")
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
