from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque


class LoginRateLimiter:
    """Process-local 5/15 limiter; multi-worker deployments need a shared store."""

    def __init__(self, limit: int = 5, window_seconds: int = 900, max_keys: int = 10_000) -> None:
        self.limit, self.window_seconds, self.max_keys = limit, window_seconds, max_keys
        self._failures: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self, values: deque[float], now: float) -> None:
        while values and values[0] <= now - self.window_seconds:
            values.popleft()

    def blocked(self, username: str, ip: str) -> bool:
        key, timestamp = (username.casefold(), ip), time.monotonic()
        with self._lock:
            values = self._failures.get(key)
            if values is None:
                return False
            self._prune(values, timestamp)
            if not values:
                del self._failures[key]
                return False
            return len(values) >= self.limit

    def fail(self, username: str, ip: str) -> None:
        key, timestamp = (username.casefold(), ip), time.monotonic()
        with self._lock:
            values = self._failures.get(key)
            if values is None:
                for old_key, old_values in list(self._failures.items()):
                    self._prune(old_values, timestamp)
                    if not old_values:
                        del self._failures[old_key]
                if len(self._failures) >= self.max_keys:
                    self._failures.popitem(last=False)
                values = deque()
                self._failures[key] = values
            self._prune(values, timestamp)
            values.append(timestamp)
            self._failures.move_to_end(key)

    def clear(self, username: str, ip: str) -> None:
        with self._lock:
            self._failures.pop((username.casefold(), ip), None)


class AccountLocks:
    def __init__(self) -> None:
        self._active: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def acquire(self, user_id: str, kind: str) -> bool:
        with self._lock:
            key = (user_id, kind)
            if key in self._active:
                return False
            self._active.add(key)
            return True

    def release(self, user_id: str, kind: str) -> None:
        with self._lock:
            self._active.discard((user_id, kind))
