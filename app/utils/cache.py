"""Small in-memory TTL cache used to avoid paying for repeated analyses."""

import time
from collections import OrderedDict
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Least-recently-used cache whose entries expire after ``ttl_seconds``.

    A ``ttl_seconds`` of ``0`` disables caching. The cache is process-local;
    use Redis or similar when running multiple instances.
    """

    def __init__(self, ttl_seconds: float, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    def get(self, key: str) -> T | None:
        """Return the cached value for ``key`` or ``None`` if absent or expired."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: T) -> None:
        """Store ``value`` under ``key``, evicting the oldest entry when full."""
        if not self.enabled:
            return
        self._entries[key] = (time.monotonic() + self._ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()
