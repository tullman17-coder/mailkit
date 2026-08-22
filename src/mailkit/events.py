"""Durable event log, live fan-out, subscriptions, acknowledgements, cursors."""

from __future__ import annotations

import json
import queue
import threading
from typing import Callable

from mailkit.db import Store
from mailkit.ids import new_id
from mailkit.logutil import get_logger
from mailkit.models import Event, EventFilter
from mailkit.rules import event_filter_match

log = get_logger("mailkit.events")


class EventBus:
    def __init__(self, store: Store, *, retention: int = 100_000):
        self.store = store
        self.retention = retention
        self._subs: list[tuple[EventFilter, queue.Queue, str | None]] = []
        self._lock = threading.Lock()
        self._listeners: list[Callable[[dict], None]] = []

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        self._listeners.append(fn)

    def publish(self, event: Event) -> Event | None:
        if not event.id:
            event.id = new_id("evt")
        stored = self.store.append_event(event)
        if stored is None:
            log.debug("duplicate event dropped key=%s", event.idempotency_key)
            return None
        payload = event.to_dict()
        self.store.trim_events(self.retention)
        with self._lock:
            live = list(self._subs)
            listeners = list(self._listeners)
        for filt, q, _name in live:
            if event_filter_match(filt, payload):
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        q.put_nowait(payload)
                    except queue.Full:
                        pass
        for fn in listeners:
            try:
                fn(payload)
            except Exception as exc:
                log.warning("listener failed: %s", exc)
        return event

    def subscribe_live(self, filt: EventFilter | None = None, *, maxsize: int = 1000, name: str | None = None) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subs.append((filt or EventFilter(), q, name))
        return q

    def unsubscribe_live(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs = [s for s in self._subs if s[1] is not q]

    def replay(self, filt: EventFilter | None, cursor: str | None, limit: int = 100) -> list[dict]:
        events = self.store.events_after(cursor, limit=limit)
        if not filt:
            return events
        return [e for e in events if event_filter_match(filt, e)]

    def stream(self, filt: EventFilter | None, cursor: str | None, stop: threading.Event):
        """Yield historical events then live ones. Durable cursor is the last yielded id."""
        for ev in self.replay(filt, cursor, limit=1000):
            yield ev
            cursor = ev.get("id")
        q = self.subscribe_live(filt)
        try:
            while not stop.is_set():
                try:
                    ev = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                yield ev
        finally:
            self.unsubscribe_live(q)
