from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

"""
- Cache key 저장 방식
{작업내용}:{kg_version}:{개입하는term}
{작업내용}:{kg_version}:{개입하는term1|term2|...}
"""

@dataclass
class CacheItem:
    value_json: str
    expires_at: float


class InMemoryTTLCache:
    """Thread-safe LRU + TTL cache.

    - Designed for single-process FastAPI (1 worker) usage.
    - For production, use Redis/Memcached or a distributed cache.
    """

    def __init__(self, max_items: int = 2048):
        self.max_items = max_items
        self._lock = threading.Lock()
        self._store: "OrderedDict[str, CacheItem]" = OrderedDict()

    def get(self, key: str) -> dict | None:
        now = time.time()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            if item.expires_at < now:
                # expired
                self._store.pop(key, None)
                return None
            # LRU bump
            self._store.move_to_end(key)
            return json.loads(item.value_json)

    def set(self, key: str, value: dict, ttl_seconds: int = 60) -> None:
        expires_at = time.time() + ttl_seconds
        value_json = json.dumps(value, ensure_ascii=False)
        with self._lock:
            self._store[key] = CacheItem(value_json=value_json, expires_at=expires_at)
            self._store.move_to_end(key)
            # evict
            while len(self._store) > self.max_items:
                self._store.popitem(last=False)

    def invalidate_prefix(self, prefix: str) -> int:
        """Invalidate all keys starting with prefix. Returns removed count."""
        with self._lock:
            keys = [k for k in self._store.keys() if k.startswith(prefix)]
            for k in keys:
                self._store.pop(k, None)
            return len(keys)
