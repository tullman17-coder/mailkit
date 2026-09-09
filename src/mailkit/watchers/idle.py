"""IMAP IDLE watcher. Primary real-time mechanism for standard IMAP."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from mailkit.backoff import Backoff
from mailkit.config import AccountConfig
from mailkit.ids import idempotency_key, new_id
from mailkit.logutil import get_logger
from mailkit.models import Event, utcnow
from mailkit.providers.imap_smtp import ImapSmtpProvider
from mailkit.watchers.poll import emit_message_created, load_uid_cursor, save_uid_cursor

log = get_logger("mailkit.idle")


class IdleWatcher:
    plugin_type = "watcher"
    id = "idle"

    def supports(self, account: AccountConfig, provider: Any) -> bool:
        tokens_fn = getattr(provider, "imap_capability_tokens", None)
        if callable(tokens_fn):
            return "IDLE" in {str(t).upper() for t in tokens_fn()}
        return "idle" in getattr(provider, "capabilities", lambda: set())()

    def watch(self, account: AccountConfig, provider: ImapSmtpProvider, emit: Callable[[Event], None], stop: threading.Event) -> None:
        mailbox = account.folders.inbox or "INBOX"
        backoff = Backoff(initial=1, maximum=120)
        last_uid = load_uid_cursor(provider, account.id, mailbox)
        while not stop.is_set():
            client = None
            try:
                client = provider.open_idle_client()
                typ, _ = client.select(f'"{mailbox}"' if mailbox.upper() != "INBOX" else "INBOX")
                if typ != "OK":
                    raise RuntimeError(f"cannot select {mailbox}")
                last_uid = max(last_uid, load_uid_cursor(provider, account.id, mailbox))
                last_uid = self._catch_up(account, provider, mailbox, last_uid, emit)
                emit(
                    Event(
                        id=new_id("evt"),
                        ts=utcnow(),
                        account_id=account.id,
                        provider_id=provider.id,
                        mailbox=mailbox,
                        type="account.connected",
                        idempotency_key=idempotency_key(account.id, mailbox, "account.connected", "idle"),
                    )
                )
                backoff.reset()
                last_uid = self._idle_loop(account, provider, client, mailbox, last_uid, emit, stop)
            except Exception as exc:
                log.warning("IDLE error account=%s: %s", account.id, exc)
                stored = load_uid_cursor(provider, account.id, mailbox)
                if stored > last_uid:
                    last_uid = stored
                emit(
                    Event(
                        id=new_id("evt"),
                        ts=utcnow(),
                        account_id=account.id,
                        provider_id=getattr(provider, "id", "imap"),
                        mailbox=mailbox,
                        type="account.disconnected",
                        data={"error": str(exc)},
                    )
                )
                stop.wait(backoff.fail())
            finally:
                if client is not None:
                    try:
                        client.logout()
                    except Exception:
                        try:
                            client.shutdown()
                        except Exception:
                            pass

    def _catch_up(self, account, provider, mailbox, last_uid, emit) -> int:
        """Emit UIDs in (cursor, max] after reconnect; seed the high-water mark on first run."""
        try:
            uids = provider.recent_uids(mailbox, last_uid if last_uid else None)
        except Exception:
            uids = []
        if not last_uid:
            last_uid = max(uids) if uids else 0
            save_uid_cursor(provider, account.id, mailbox, last_uid)
            return last_uid
        for uid in uids:
            if uid <= last_uid:
                continue
            last_uid = max(last_uid, uid)
            emit_message_created(account, provider, mailbox, uid, emit)
        save_uid_cursor(provider, account.id, mailbox, last_uid)
        return last_uid

    def _idle_loop(self, account, provider, client, mailbox, last_uid, emit, stop) -> int:
        # Slice IDLE into 60s windows so shutdown is prompt; IMAP servers cap IDLE near 29 minutes.
        while not stop.is_set():
            notified = _wait_idle(client, 60.0, stop)
            if stop.is_set():
                return last_uid
            try:
                new_uids = provider.recent_uids(mailbox, last_uid if last_uid else None)
            except Exception as exc:
                log.info("uid refresh failed: %s", exc)
                raise
            for uid in new_uids:
                if uid <= last_uid:
                    continue
                last_uid = max(last_uid, uid)
                emit_message_created(account, provider, mailbox, uid, emit)
            save_uid_cursor(provider, account.id, mailbox, last_uid)
            if not notified:
                try:
                    client.noop()
                except Exception:
                    raise
        return last_uid


def _wait_idle(client, duration: float, stop: threading.Event) -> bool:
    """Block in IDLE until a server untagged response or timeout."""
    if hasattr(client, "idle"):
        deadline = time.time() + duration
        try:
            with client.idle(duration=duration) as idler:
                for _typ, data in idler:
                    if stop.is_set():
                        return False
                    joined = b" ".join(x if isinstance(x, bytes) else str(x).encode() for x in (data or []))
                    if any(token in joined.upper() for token in (b"EXISTS", b"EXPUNGE", b"FETCH", b"RECENT")):
                        return True
                    if time.time() >= deadline:
                        return False
        except Exception:
            raise
        return False
    return _idle_legacy(client, duration, stop)


def _idle_legacy(client, duration: float, stop: threading.Event) -> bool:
    sock = client.socket()
    tag = client._new_tag()
    client.send(f"{tag} IDLE\r\n".encode("ascii"))
    sock.settimeout(1.0)
    deadline = time.time() + duration
    continuation = False
    notified = False
    buf = b""
    while time.time() < deadline and not stop.is_set():
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            continue
        except OSError:
            raise
        if not chunk:
            raise ConnectionError("IMAP socket closed during IDLE")
        buf += chunk
        while b"\r\n" in buf:
            line, buf = buf.split(b"\r\n", 1)
            upper = line.upper()
            if upper.startswith(b"+"):
                continuation = True
            if any(tok in upper for tok in (b"EXISTS", b"EXPUNGE", b"FETCH", b"RECENT")):
                notified = True
                deadline = time.time()
    if continuation:
        try:
            client.send(b"DONE\r\n")
            sock.settimeout(10)
            while True:
                line = sock.recv(4096)
                if not line or tag.encode() in line:
                    break
        except Exception:
            pass
    return notified
