"""Local or remote webhook delivery with per-hook retries, HMAC signatures, and durable backoff."""

from __future__ import annotations

import hashlib
import hmac
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from mailkit.backoff import Backoff
from mailkit.db import Store
from mailkit.logutil import get_logger
from mailkit.models import EventFilter, utcnow
from mailkit.rules import event_filter_match
from mailkit.vault import Vault

log = get_logger("mailkit.webhooks")

MAX_ATTEMPTS = 5


def _retry_at(delay: float) -> str:
    when = datetime.now(timezone.utc) + timedelta(seconds=max(0.0, delay))
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _delay_until(next_retry: str | None) -> float:
    if not next_retry:
        return 0.0
    try:
        when = datetime.fromisoformat(next_retry.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


class WebhookDispatcher:
    def __init__(
        self,
        store: Store,
        vault: Vault,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        backoff_factory: Callable[[], Backoff] | None = None,
    ):
        self.store = store
        self.vault = vault
        self.max_attempts = max_attempts
        self._backoff_factory = backoff_factory or (lambda: Backoff(initial=1, maximum=60))
        self._stop = threading.Event()
        self._stop.set()
        self._running = False
        self._lock = threading.Lock()
        self._db_lock = threading.Lock()
        self._queues: dict[str, queue.Queue] = {}
        self._threads: dict[str, threading.Thread] = {}

    def start(self) -> None:
        self._stop.clear()
        self._running = True
        self._replay_pending()
        with self._lock:
            hook_ids = list(self._queues)
        for hook_id in hook_ids:
            self._ensure_worker(hook_id)

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout=2)

    def enqueue(self, event: dict) -> None:
        matched: list[dict] = []
        with self._db_lock:
            for hook in self.store.list_webhooks():
                if not hook.get("enabled"):
                    continue
                filt = EventFilter.from_dict(json.loads(hook["filter_json"] or "{}"))
                if not event_filter_match(filt, event):
                    continue
                self.store.upsert_webhook_delivery(
                    hook["id"],
                    event,
                    attempts=0,
                    status="pending",
                    next_retry=utcnow(),
                )
                matched.append(hook)
        if self._running:
            now = time.monotonic()
            for hook in matched:
                self._submit(hook, event, attempt=0, due=now)

    def _replay_pending(self) -> None:
        with self._db_lock:
            hooks = {h["id"]: h for h in self.store.list_webhooks()}
            pending = self.store.pending_webhook_deliveries()
        for row in pending:
            hook = hooks.get(row["webhook_id"])
            if not hook or not hook.get("enabled"):
                continue
            try:
                event = json.loads(row["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            due = time.monotonic() + _delay_until(row.get("next_retry"))
            self._submit(hook, event, attempt=int(row["attempts"] or 0), due=due)

    def _submit(self, hook: dict, event: dict, *, attempt: int, due: float) -> None:
        hook_id = hook["id"]
        q = self._ensure_worker(hook_id)
        q.put((due, hook, event, attempt))

    def _ensure_worker(self, hook_id: str) -> queue.Queue:
        with self._lock:
            q = self._queues.get(hook_id)
            if q is None:
                q = queue.Queue()
                self._queues[hook_id] = q
            if self._running:
                thread = self._threads.get(hook_id)
                if thread is None or not thread.is_alive():
                    thread = threading.Thread(
                        target=self._run_hook,
                        args=(hook_id,),
                        name=f"mailkit-webhook-{hook_id}",
                        daemon=True,
                    )
                    self._threads[hook_id] = thread
                    thread.start()
            return q

    def _run_hook(self, hook_id: str) -> None:
        q = self._queues[hook_id]
        while not self._stop.is_set():
            try:
                due, hook, event, attempt = q.get(timeout=0.25)
            except queue.Empty:
                continue
            wait = due - time.monotonic()
            if wait > 0:
                if self._stop.wait(min(wait, 0.25)):
                    break
                if time.monotonic() < due:
                    q.put((due, hook, event, attempt))
                    continue
            if self._deliver(hook, event):
                with self._db_lock:
                    self.store.mark_webhook_delivered(hook["id"], event.get("id") or "")
                continue
            attempt += 1
            if attempt < self.max_attempts:
                delay = self._delay_for(attempt)
                with self._db_lock:
                    self.store.upsert_webhook_delivery(
                        hook["id"],
                        event,
                        attempts=attempt,
                        status="pending",
                        next_retry=_retry_at(delay),
                        error="delivery failed",
                    )
                q.put((time.monotonic() + delay, hook, event, attempt))
            else:
                with self._db_lock:
                    self.store.upsert_webhook_delivery(
                        hook["id"],
                        event,
                        attempts=attempt,
                        status="failed",
                        error="gave up",
                    )
                log.warning("webhook %s gave up on event %s", hook.get("id"), event.get("id"))

    def _delay_for(self, attempt: int) -> float:
        delay = self._backoff_factory()
        wait = 0.0
        for _ in range(max(attempt, 1)):
            wait = delay.fail()
        return wait

    def _deliver(self, hook: dict, event: dict) -> bool:
        secret = self.vault.get_webhook_secret(hook["id"])
        body = json.dumps(event, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "mailkit/0.1",
            "X-Mailkit-Event": event.get("type") or "",
            "X-Mailkit-Delivery": event.get("id") or "",
        }
        if secret:
            sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-Mailkit-Signature"] = f"sha256={sig}"
        req = urllib.request.Request(hook["url"], data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return 200 <= resp.status < 300
        except urllib.error.HTTPError as exc:
            log.warning("webhook %s HTTP %s", hook.get("id"), exc.code)
            return False
        except Exception as exc:
            log.warning("webhook %s error: %s", hook.get("id"), exc)
            return False
