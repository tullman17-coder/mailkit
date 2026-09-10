"""No-op watcher for the offline local provider."""

from __future__ import annotations

import threading
from typing import Any, Callable

from mailkit.config import AccountConfig
from mailkit.ids import idempotency_key, new_id
from mailkit.models import Event, utcnow


class LocalWatcher:
    plugin_type = "watcher"
    id = "local"

    def supports(self, account: AccountConfig, provider: Any) -> bool:
        return getattr(provider, "id", "") == "local" or getattr(account, "provider", "") == "local"

    def watch(self, account: AccountConfig, provider: Any, emit: Callable[[Event], None], stop: threading.Event) -> None:
        mailbox = account.folders.inbox or "INBOX"
        emit(
            Event(
                id=new_id("evt"),
                ts=utcnow(),
                account_id=account.id,
                provider_id="local",
                mailbox=mailbox,
                type="account.connected",
                idempotency_key=idempotency_key(account.id, mailbox, "account.connected", "local"),
            )
        )
        stop.wait()
