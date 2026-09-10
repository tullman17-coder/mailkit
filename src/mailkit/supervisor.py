"""Account supervisor: keep every mailbox connection alive and emit events."""

from __future__ import annotations

import threading
import time
from typing import Any

from mailkit.backoff import Backoff
from mailkit.config import AccountConfig
from mailkit.events import EventBus
from mailkit.ids import idempotency_key, new_id
from mailkit.logutil import get_logger
from mailkit.models import Event, utcnow
from mailkit.plugins.types import HookAction, HookContext
from mailkit.rules import (
    RulesEngine,
    RulesHook,
    apply_imap_flags,
    flag_event_types,
    persist_imap_flags,
)
from mailkit.runtime import Runtime
from mailkit.watchers.gmail_push import load_gmail_created_message

log = get_logger("mailkit.supervisor")


def choose_watcher(account: AccountConfig, provider: Any, plugins) -> Any:
    requested = account.watch or "auto"
    caps = provider.capabilities() if hasattr(provider, "capabilities") else set()
    if requested != "auto":
        watcher = plugins.watcher_for(requested)
        if watcher and watcher.supports(account, provider):
            return watcher
    # Preference order: local demo, native push, IDLE, poll.
    for name in ("local", "gmail_push", "graph_push", "idle", "poll"):
        if requested == "auto" or requested == name:
            watcher = plugins.watcher_for(name)
            if not watcher:
                continue
            if name == "gmail_push" and "gmail_push" not in caps and not account.oauth.pubsub_subscription:
                # History API still useful when we have a Gmail access token.
                if not (name == "gmail_push" and getattr(provider, "id", "") == "gmail" and provider.secrets.get("access_token")):
                    continue
            if name == "graph_push" and getattr(provider, "id", "") != "graph":
                continue
            if name == "idle" and "idle" not in caps:
                continue
            if watcher.supports(account, provider):
                return watcher
    return plugins.watcher_for("poll")


class AccountWorker:
    def __init__(self, runtime: Runtime, account: AccountConfig, bus: EventBus):
        self.runtime = runtime
        self.account = account
        self.bus = bus
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.status = "stopped"
        self.last_error = ""
        self.watcher_id = ""
        self.restarts = 0
        self.last_start = 0.0
        self._provider = None

    def start(self) -> None:
        self.stop.clear()
        self.status = "connecting"
        self.last_start = time.time()
        self.thread = threading.Thread(target=self._run, name=f"mailkit-{self.account.id}", daemon=True)
        self.thread.start()

    def join(self, timeout: float | None = None) -> None:
        self.stop.set()
        provider = self._provider
        if provider is not None:
            try:
                provider.close()
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=timeout)

    def alive(self) -> bool:
        # A worker is live as soon as its thread exists, including the IMAP
        # connect window when status is still "connecting"/"stopped".
        return bool(self.thread and self.thread.is_alive())

    def _run(self) -> None:
        backoff = Backoff(initial=1.0, maximum=120.0)
        while not self.stop.is_set():
            provider = None
            self.status = "connecting"
            self.last_start = time.time()
            try:
                provider, acc, _secrets = self.runtime.provider_for(self.account.id)
                self._provider = provider
                setter = getattr(provider, "set_stop", None)
                if callable(setter):
                    setter(self.stop)
                if self.stop.is_set():
                    break
                if hasattr(provider, "connect"):
                    provider.connect()
                watcher = choose_watcher(acc, provider, self.runtime.plugins)
                if watcher is None:
                    raise RuntimeError("no watcher plugin available")
                self.watcher_id = getattr(watcher, "id", "poll")
                self.status = "running"
                self.last_error = ""
                log.info("watch account=%s provider=%s watcher=%s", acc.id, provider.id, self.watcher_id)

                def emit(event: Event, prov=provider) -> None:
                    extras: list[Event] = []
                    self._prepare_created_event(prov, event)
                    if event.type == "message.created" and event.message:
                        extras = self._apply_hooks(prov, event) or []
                    published = self.bus.publish(event)
                    for extra in extras:
                        self.bus.publish(extra)
                    if published and event.type == "message.created":
                        log.info("event %s account=%s mailbox=%s", event.type, event.account_id, event.mailbox)

                backoff.reset()
                watcher.watch(acc, provider, emit, self.stop)
                if self.stop.is_set():
                    break
                self.status = "reconnecting"
                self.last_error = "watcher exited"
                self.restarts += 1
            except Exception as exc:
                self.status = "error"
                self.last_error = str(exc)
                self.restarts += 1
                log.warning("worker %s crashed: %s", self.account.id, exc)
                self.bus.publish(
                    Event(
                        id=new_id("evt"),
                        ts=utcnow(),
                        account_id=self.account.id,
                        provider_id=self.account.provider,
                        type="account.disconnected",
                        data={"error": str(exc), "restarts": self.restarts},
                    )
                )
            finally:
                if provider is not None:
                    try:
                        provider.close()
                    except Exception:
                        pass
                if self._provider is provider:
                    self._provider = None
            if not self.stop.is_set():
                self.status = "reconnecting"
                self.stop.wait(backoff.fail())
        self.status = "stopped"

    def _prepare_created_event(self, provider, event: Event) -> None:
        if event.type != "message.created" or event.message:
            return
        gmail_id = (event.data or {}).get("gmail_id")
        if not gmail_id:
            return
        msg = load_gmail_created_message(provider, event.mailbox, gmail_id)
        if msg is None:
            return
        event.message = msg.summary()
        if not event.thread_id:
            event.thread_id = msg.thread_id

    def _apply_hooks(self, provider, event: Event) -> list[Event]:
        payload = event.message or {}
        # Reconstruct a Message-like object from summary for matching.
        msg = _message_from_summary(payload)
        engine = RulesEngine(_load_rules(self.runtime))
        hooks = [RulesHook(engine), *self.runtime.plugins.hook_list()]
        ctx = HookContext(
            account_id=event.account_id,
            provider_id=event.provider_id,
            mailbox=event.mailbox,
            message=msg,
            event=event,
            store=self.runtime.store,
        )
        action = HookAction()
        from mailkit.rules import merge_actions

        for hook in hooks:
            try:
                result = hook.process(ctx)
            except Exception as exc:
                log.warning("hook %s failed: %s", getattr(hook, "id", hook), exc)
                continue
            if result:
                action = merge_actions(action, result)
                if action.stop:
                    break
        native = payload.get("native_id") or payload.get("uid")
        mailbox = event.mailbox
        extras: list[Event] = []
        if action.extra.get("matched_rules"):
            event.data.setdefault("matched_rules", action.extra.get("matched_rules", []))
        if action.tag and payload.get("id"):
            current = list((event.message or payload).get("tags") or payload.get("tags") or [])
            updated = self.runtime.store.set_tags(payload["id"], current + action.tag)
            if updated:
                event.message = {**(event.message or payload), "tags": updated.get("tags") or []}
        if action.flag is True and native:
            try:
                provider.set_flags(mailbox, str(native), add=["Flagged"])
                extras.extend(self._sync_flags_after_store(event, add=["Flagged"]))
            except Exception as exc:
                log.warning("flag failed: %s", exc)
        if action.flag is False and native:
            try:
                provider.set_flags(mailbox, str(native), remove=["Flagged"])
                extras.extend(self._sync_flags_after_store(event, remove=["Flagged"]))
            except Exception as exc:
                log.warning("unflag failed: %s", exc)
        if action.mark_read is True and native:
            try:
                provider.set_flags(mailbox, str(native), add=["Seen"])
                extras.extend(self._sync_flags_after_store(event, add=["Seen"]))
            except Exception as exc:
                log.warning("mark-read failed: %s", exc)
        if action.mark_read is False and native:
            try:
                provider.set_flags(mailbox, str(native), remove=["Seen"])
                extras.extend(self._sync_flags_after_store(event, remove=["Seen"]))
            except Exception as exc:
                log.warning("mark-unread failed: %s", exc)
        if action.move and native:
            try:
                provider.move(mailbox, str(native), action.move)
                event.type = "message.moved"
                event.data["moved_to"] = action.move
            except Exception as exc:
                log.warning("rule move failed: %s", exc)
        # Follow-up events carry the post-STORE snapshot (flag + read may both apply).
        for extra in extras:
            extra.message = event.message
        return extras

    def _sync_flags_after_store(self, event: Event, *, add: list[str] | None = None, remove: list[str] | None = None) -> list[Event]:
        payload = event.message or {}
        updated = apply_imap_flags(payload, add=add, remove=remove)
        event.message = updated
        msg_id = updated.get("id")
        if msg_id:
            persisted = persist_imap_flags(self.runtime.store, msg_id, add=add, remove=remove)
            if persisted:
                event.message = {
                    **updated,
                    "flags": persisted.get("flags") or updated["flags"],
                    "flagged": persisted.get("flagged"),
                    "unread": persisted.get("unread"),
                    "tags": persisted.get("tags") if persisted.get("tags") is not None else updated.get("tags"),
                }
        native = str((event.message or {}).get("native_id") or (event.message or {}).get("uid") or "")
        extras: list[Event] = []
        for etype in flag_event_types(add=add, remove=remove):
            extras.append(
                Event(
                    id=new_id("evt"),
                    ts=utcnow(),
                    account_id=event.account_id,
                    provider_id=event.provider_id,
                    mailbox=event.mailbox,
                    type=etype,
                    thread_id=event.thread_id,
                    message=event.message,
                    data={"matched_rules": event.data.get("matched_rules", [])},
                    idempotency_key=idempotency_key(event.account_id, event.mailbox, etype, native),
                )
            )
        return extras


class Supervisor:
    def __init__(self, runtime: Runtime, bus: EventBus):
        self.runtime = runtime
        self.bus = bus
        self.workers: dict[str, AccountWorker] = {}

    def start_all(self) -> None:
        for acc in self.runtime.config.accounts.values():
            if acc.enabled:
                self.start_account(acc.id)

    def start_account(self, account_id: str) -> None:
        self.stop_account(account_id)
        acc = self.runtime.config.accounts[account_id]
        worker = AccountWorker(self.runtime, acc, self.bus)
        self.workers[account_id] = worker
        worker.start()

    def stop_account(self, account_id: str) -> None:
        worker = self.workers.pop(account_id, None)
        if worker:
            # IMAP connect can take the full account timeout (default 30s) and
            # used to outlive a 5s join, so start_account spawned a duplicate.
            worker.join()

    def request_stop(self) -> None:
        """Abort in-flight connect/IDLE so join() does not wait on a 30s handshake."""
        for worker in self.workers.values():
            worker.stop.set()
            provider = getattr(worker, "_provider", None)
            if provider is not None:
                try:
                    provider.close()
                except Exception:
                    pass

    def stop_all(self) -> None:
        self.request_stop()
        for account_id in list(self.workers):
            self.stop_account(account_id)

    def status(self) -> dict:
        return {
            account_id: {
                "status": w.status,
                "watcher": w.watcher_id,
                "error": w.last_error,
                "address": w.account.address,
                "provider": w.account.provider,
                "alive": w.alive(),
                "restarts": w.restarts,
            }
            for account_id, w in self.workers.items()
        }

    def restart_account(self, account_id: str) -> bool:
        acc = self.runtime.config.accounts.get(account_id)
        if not acc or not acc.enabled:
            return False
        worker = self.workers.get(account_id)
        if worker and worker.alive():
            return False
        self.start_account(account_id)
        return True


def _load_rules(runtime: Runtime):
    from mailkit.rules import Rule

    return [
        Rule(
            id=r["id"],
            name=r.get("name") or "",
            priority=int(r.get("priority") or 0),
            enabled=bool(r.get("enabled")),
            stop=bool(r.get("stop")),
            match=r.get("match") or {},
            actions=r.get("actions") or {},
        )
        for r in runtime.store.list_rules()
    ]


def _message_from_summary(payload: dict):
    from mailkit.models import Address, Message

    def addrs(key):
        out = []
        for item in payload.get(key) or []:
            if isinstance(item, dict):
                out.append(Address(address=item.get("address") or "", name=item.get("name") or ""))
        return out

    return Message(
        id=payload.get("id") or "",
        account_id=payload.get("account_id") or "",
        provider_id=payload.get("provider_id") or "",
        mailbox=payload.get("mailbox") or "",
        uid=payload.get("uid"),
        native_id=str(payload.get("native_id") or ""),
        message_id=payload.get("message_id") or "",
        thread_id=payload.get("thread_id") or "",
        subject=payload.get("subject") or "",
        from_=addrs("from"),
        to=addrs("to"),
        cc=addrs("cc"),
        flags=payload.get("flags") or [],
        labels=payload.get("labels") or [],
        tags=payload.get("tags") or [],
        unread=bool(payload.get("unread", True)),
        flagged=bool(payload.get("flagged")),
        has_attachments=bool(payload.get("has_attachments")),
        attachment_types=payload.get("attachment_types") or [],
        snippet=payload.get("snippet") or "",
    )
