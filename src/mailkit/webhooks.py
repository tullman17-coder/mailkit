"""Local or remote webhook delivery with retries, HMAC signatures, and backoff."""

from __future__ import annotations

import hashlib
import hmac
import json
import queue
import threading
import urllib.error
import urllib.request

from mailkit.backoff import Backoff
from mailkit.db import Store
from mailkit.logutil import get_logger
from mailkit.models import EventFilter
from mailkit.rules import event_filter_match
from mailkit.vault import Vault

log = get_logger("mailkit.webhooks")


class WebhookDispatcher:
    def __init__(self, store: Store, vault: Vault):
        self.store = store
        self.vault = vault
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mailkit-webhooks", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def enqueue(self, event: dict) -> None:
        for hook in self.store.list_webhooks():
            if not hook.get("enabled"):
                continue
            filt = EventFilter.from_dict(json.loads(hook["filter_json"] or "{}"))
            if event_filter_match(filt, event):
                self._q.put((hook, event, 0))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                hook, event, attempt = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if not self._deliver(hook, event):
                if attempt < 5:
                    delay = Backoff(initial=1, maximum=60)
                    for _ in range(attempt + 1):
                        wait = delay.fail()
                    self._stop.wait(wait)
                    self._q.put((hook, event, attempt + 1))
                else:
                    log.warning("webhook %s gave up on event %s", hook.get("id"), event.get("id"))

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
