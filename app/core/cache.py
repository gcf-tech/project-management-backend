"""Pluggable async cache for the calendar service (Redis or in-process).

Why an abstraction:
- In dev / tests / single-worker we want zero-config in-memory caching.
- In prod with multiple workers (or Vercel-style stateless functions) we
  need a shared store, so Redis becomes mandatory the moment REDIS_URL is set.

The contract is intentionally tiny — `get/set/delete/delete_prefix` plus a
freshness flag on read so the service layer can implement stale-while-
revalidate without leaking storage details.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from cachetools import TTLCache

from app.core.config import REDIS_URL

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CacheHit:
    """Result of a cache lookup that returned data."""
    value: Any
    age_s: float           # how long ago this entry was written
    ttl_s: int             # ttl the entry was originally written with
    is_stale: bool         # True ⇒ caller should trigger a background refresh


class Cache(ABC):
    """Async cache interface. Implementations must be coroutine-safe."""

    @abstractmethod
    async def get(self, key: str, *, stale_threshold: float = 0.7) -> Optional[CacheHit]: ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl_s: int) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def delete_prefix(self, prefix: str) -> int:
        """Delete every entry whose key starts with `prefix`. Returns count."""


# ── In-memory implementation ────────────────────────────────────────────────

class InMemoryTTLCache(Cache):
    """Per-process TTL cache backed by `cachetools`.

    Suitable for single-worker dev / unit tests. In a multi-worker production
    deployment each worker would maintain its own copy → use `RedisCache`
    instead by setting REDIS_URL.
    """

    def __init__(self, *, max_entries: int = 1024, default_ttl: int = 1800) -> None:
        # cachetools.TTLCache evicts only on access; we store our own
        # `(value, written_at, ttl)` tuple so we can compute precise age.
        self._store: TTLCache = TTLCache(maxsize=max_entries, ttl=default_ttl)
        self._lock           = asyncio.Lock()

    async def get(self, key: str, *, stale_threshold: float = 0.7) -> Optional[CacheHit]:
        async with self._lock:
            entry = self._store.get(key)
        if entry is None:
            return None
        value, written_at, ttl_s = entry
        age = time.monotonic() - written_at
        if age > ttl_s:
            return None
        return CacheHit(
            value=value,
            age_s=age,
            ttl_s=ttl_s,
            is_stale=age > stale_threshold * ttl_s,
        )

    async def set(self, key: str, value: Any, ttl_s: int) -> None:
        async with self._lock:
            self._store[key] = (value, time.monotonic(), ttl_s)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def delete_prefix(self, prefix: str) -> int:
        async with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                self._store.pop(k, None)
            return len(keys)


# ── Redis implementation ────────────────────────────────────────────────────

class RedisCache(Cache):
    """Redis-backed implementation. Each value is wrapped with metadata.

    The wrapper format is `{"v": <value>, "w": <written_unix_ts>, "t": <ttl>}`,
    serialized as JSON. We keep the Redis TTL aligned with `ttl_s` so old
    entries get evicted automatically; the wrapper just lets us compute
    age/staleness without an extra round-trip.
    """

    def __init__(self, redis_url: str) -> None:
        # Lazy-import so projects without redis installed still load this module.
        from redis import asyncio as aioredis  # noqa: WPS433
        self._redis = aioredis.from_url(redis_url, decode_responses=True)

    async def get(self, key: str, *, stale_threshold: float = 0.7) -> Optional[CacheHit]:
        try:
            raw = await self._redis.get(key)
        except Exception:  # noqa: BLE001  (network / serialization)
            logger.warning("[cache] redis.get failed for %s", key, exc_info=True)
            return None
        if raw is None:
            return None
        try:
            wrapped = json.loads(raw)
            value   = wrapped["v"]
            written = float(wrapped["w"])
            ttl_s   = int(wrapped["t"])
        except (KeyError, ValueError, TypeError):
            logger.warning("[cache] corrupt redis payload for %s", key)
            return None

        age = time.time() - written
        if age > ttl_s:
            return None
        return CacheHit(
            value=value,
            age_s=age,
            ttl_s=ttl_s,
            is_stale=age > stale_threshold * ttl_s,
        )

    async def set(self, key: str, value: Any, ttl_s: int) -> None:
        wrapped = json.dumps({"v": value, "w": time.time(), "t": ttl_s})
        try:
            await self._redis.set(key, wrapped, ex=ttl_s)
        except Exception:  # noqa: BLE001
            logger.warning("[cache] redis.set failed for %s", key, exc_info=True)

    async def delete(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except Exception:  # noqa: BLE001
            logger.warning("[cache] redis.delete failed for %s", key, exc_info=True)

    async def delete_prefix(self, prefix: str) -> int:
        # SCAN is preferred over KEYS in production (non-blocking on large
        # keyspaces). Pattern uses Redis glob: "<prefix>*".
        deleted = 0
        try:
            async for key in self._redis.scan_iter(match=f"{prefix}*", count=100):
                await self._redis.delete(key)
                deleted += 1
        except Exception:  # noqa: BLE001
            logger.warning("[cache] redis.scan failed for %s*", prefix, exc_info=True)
        return deleted


# ── Selection / lifecycle ───────────────────────────────────────────────────

_singleton: Optional[Cache] = None


def get_cache() -> Cache:
    """Return the process-wide cache, lazily creating it on first call.

    Selection rule:
      - REDIS_URL set ⇒ `RedisCache(REDIS_URL)`.
      - otherwise     ⇒ `InMemoryTTLCache()`.

    The cache is stateless from the caller's POV; tests can override it via
    `set_cache_for_tests`.
    """
    global _singleton
    if _singleton is not None:
        return _singleton
    if REDIS_URL:
        try:
            _singleton = RedisCache(REDIS_URL)
            logger.info("[cache] using RedisCache")
            return _singleton
        except Exception:  # noqa: BLE001
            # Don't crash startup if redis package is missing — degrade.
            logger.warning(
                "[cache] redis init failed, falling back to in-memory",
                exc_info=True,
            )
    _singleton = InMemoryTTLCache()
    logger.info("[cache] using InMemoryTTLCache")
    return _singleton


def set_cache_for_tests(cache: Optional[Cache]) -> None:
    """Test helper: swap the singleton (or reset it with `None`)."""
    global _singleton
    _singleton = cache
