"""Exponential backoff, retry, and per-account rate limiting."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass


@dataclass
class Backoff:
    initial: float = 1.0
    maximum: float = 300.0
    multiplier: float = 2.0
    jitter: float = 0.2

    def __post_init__(self) -> None:
        self._delay = self.initial
        self.attempts = 0

    def reset(self) -> None:
        self._delay = self.initial
        self.attempts = 0

    def peek(self) -> float:
        return self._delay

    def fail(self) -> float:
        self.attempts += 1
        delay = self._delay
        spread = delay * self.jitter
        sleep_for = max(0.0, delay + random.uniform(-spread, spread))
        self._delay = min(self.maximum, self._delay * self.multiplier)
        return sleep_for

    def sleep(self) -> float:
        delay = self.fail()
        time.sleep(delay)
        return delay


class TokenBucket:
    """Thread-safe token bucket used to cap IMAP/SMTP/API calls per account."""

    def __init__(self, rate: float = 4.0, burst: float = 8.0):
        self.rate = rate
        self.burst = burst
        self._tokens = burst
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: float = 1.0, wait: bool = True) -> bool:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated
                self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                need = (tokens - self._tokens) / self.rate if self.rate else 1.0
            if not wait:
                return False
            time.sleep(max(need, 0.05))


def retry(times: int = 3, *, backoff: Backoff | None = None, retry_on: tuple[type[BaseException], ...] = (Exception,)):
    """Synchronous retry decorator for network operations."""

    def wrap(fn):
        def inner(*args, **kwargs):
            policy = backoff or Backoff()
            last: BaseException | None = None
            for attempt in range(times):
                try:
                    result = fn(*args, **kwargs)
                    policy.reset()
                    return result
                except retry_on as exc:
                    last = exc
                    if attempt >= times - 1:
                        break
                    policy.sleep()
            assert last is not None
            raise last

        return inner

    return wrap
