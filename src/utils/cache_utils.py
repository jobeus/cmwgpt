"""
Cache Utilities - content caches kept in Redis so they survive restarts
"""

import asyncio
import functools
import json
import logging
import time
from collections import OrderedDict
from typing import Any, Callable, Generic, Hashable, Optional, TypeVar, cast

import redis
import redis.asyncio as aioredis

from src.db.redis_client import KEY_PREFIX, get_async_redis

logger = logging.getLogger(__name__)

# Entries expire this long after they were last read or written, so a link that's
# still being talked about stays cached while dead ones age out on their own.
DEFAULT_TTL_SECONDS = 30 * 24 * 3600

# Values bigger than this aren't worth holding in Redis (a long voice note as base64).
MAX_VALUE_BYTES = 8 * 1024 * 1024

# Without Redis (tests, or while it's down) each cache is an in-memory LRU this big.
FALLBACK_MAX_SIZE = 200

# After a Redis error, use the in-memory fallback for this long before trying again.
REDIS_RETRY_SECONDS = 30.0

# Values at least this big are JSON-encoded and decoded in a worker thread.
OFF_LOOP_JSON_BYTES = 256 * 1024

F = TypeVar("F", bound=Callable[..., Any])
V = TypeVar("V")


class LRUCache(Generic[V]):
    """A small in-memory LRU mapping."""

    def __init__(self, max_size: int):
        self.max_size = max_size
        self._data: "OrderedDict[Hashable, V]" = OrderedDict()

    def get(self, key: Hashable) -> Optional[V]:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def __setitem__(self, key: Hashable, value: V) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.max_size:
            self._data.popitem(last=False)

    def __contains__(self, key: Hashable) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def pop(self, key: Hashable) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


class PersistentCache:
    """An async cache stored in Redis as JSON, one key per entry.

    Keys are ``cmwgpt:cache:<name>:<key>`` and expire ``ttl_seconds`` after their last
    use. If Redis can't be reached the cache quietly works from memory instead, so a
    Redis outage costs refetches, never a failed reply. Big values (base64 images) are
    encoded and decoded off the event loop.
    """

    def __init__(
        self,
        cache_name: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        client_getter: Callable[[], aioredis.Redis] = get_async_redis,
    ):
        self.cache_name = cache_name
        self.ttl_seconds = ttl_seconds
        self._prefix = f"{KEY_PREFIX}cache:{cache_name}:"
        self._client_getter = client_getter
        self._client: Optional[aioredis.Redis] = None
        self._down_until = 0.0
        self._fallback: LRUCache[Any] = LRUCache(FALLBACK_MAX_SIZE)

    def _key(self, key: Hashable) -> str:
        return f"{self._prefix}{key}"

    def _redis(self) -> Optional[aioredis.Redis]:
        """The client, or ``None`` while Redis is unavailable."""
        if time.monotonic() < self._down_until:
            return None
        if self._client is None:
            try:
                self._client = self._client_getter()
            except Exception as e:
                # Under tests there's no Redis at all: stay in memory for good.
                logger.debug(f"Cache {self.cache_name} running in memory: {e}")
                self._down_until = float("inf")
                return None
        return self._client

    def _redis_failed(self, e: Exception) -> None:
        logger.warning(f"Redis error in cache {self.cache_name}, using memory for now: {e}")
        self._down_until = time.monotonic() + REDIS_RETRY_SECONDS

    async def get(self, key: Hashable, default: Any = None) -> Any:
        client = self._redis()
        if client is not None:
            try:
                raw = await client.getex(self._key(key), ex=self.ttl_seconds)
            except redis.RedisError as e:
                self._redis_failed(e)
            else:
                if raw is None:
                    return default
                if len(raw) > OFF_LOOP_JSON_BYTES:
                    return await asyncio.to_thread(json.loads, raw)
                return json.loads(raw)
        value = self._fallback.get(key)
        return default if value is None else value

    async def set(self, key: Hashable, value: Any) -> None:
        raw = await _dumps(value)
        if len(raw) > MAX_VALUE_BYTES:
            logger.debug(f"Not caching {self.cache_name}:{key}: {len(raw)} bytes is too big")
            return
        client = self._redis()
        if client is not None:
            try:
                await client.set(self._key(key), raw, ex=self.ttl_seconds)
                return
            except redis.RedisError as e:
                self._redis_failed(e)
        self._fallback[key] = value

    async def delete(self, key: Hashable) -> None:
        self._fallback.pop(key)
        client = self._redis()
        if client is not None:
            try:
                await client.delete(self._key(key))
            except redis.RedisError as e:
                self._redis_failed(e)


async def _dumps(value: Any) -> str:
    """JSON-encode ``value``, off the event loop when it holds big strings."""
    if _rough_size(value) > OFF_LOOP_JSON_BYTES:
        return await asyncio.to_thread(json.dumps, value)
    return json.dumps(value)


def _rough_size(value: Any) -> int:
    """Total length of the strings in ``value``, which is most of its JSON size."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (list, tuple)):
        return sum(_rough_size(v) for v in value)
    if isinstance(value, dict):
        return sum(_rough_size(v) for v in value.values())
    return 0


# ---------------------------------------------------------------------------
# Fetch-failure caching
# ---------------------------------------------------------------------------

# How long a failed URL fetch is remembered before it's tried again. Long enough
# that a dead link isn't re-fetched on every mention, short enough that a
# transient failure (timeout, rate limit) recovers on its own.
FETCH_FAILURE_TTL_SECONDS = 3600

_fetch_failures: Optional[PersistentCache] = None


def _failure_store() -> Optional[PersistentCache]:
    """The shared failure cache, created on first use (disabled under tests unless patched)."""
    global _fetch_failures
    if _fetch_failures is None:
        from src.config import get_config
        if get_config().is_testing:
            return None
        _fetch_failures = PersistentCache("fetch_failures", ttl_seconds=FETCH_FAILURE_TTL_SECONDS)
    return _fetch_failures


async def _recently_failed(failure_key: str) -> bool:
    store = _failure_store()
    if store is None:
        return False
    failed_at = await store.get(failure_key)
    if failed_at is None:
        return False
    if time.time() - failed_at < FETCH_FAILURE_TTL_SECONDS:
        return True
    await store.delete(failure_key)
    return False


async def _record_result(failure_key: str, result: Any) -> None:
    store = _failure_store()
    if store is None:
        return
    if result is None:
        await store.set(failure_key, time.time())
    else:
        # Deleting a key that isn't there is harmless, and saves a lookup first.
        await store.delete(failure_key)


def skip_recent_failures(kind: str) -> Callable[[F], F]:
    """Decorate an async fetcher ``fn(key, ...) -> result | None`` so a ``None`` (failure)
    is remembered for ``FETCH_FAILURE_TTL_SECONDS`` and returned straight away for
    that key instead of fetching again.

    A call with ``cache_only=True`` is a lookup, not a fetch, so its ``None``
    (a cache miss) is passed through without being recorded.
    """
    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(key: str, *args: Any, **kwargs: Any) -> Any:
            if kwargs.get("cache_only"):
                return await fn(key, *args, **kwargs)
            failure_key = f"{kind}:{key}"
            if await _recently_failed(failure_key):
                logger.debug(f"Skipping {failure_key}: failed within the last hour")
                return None
            result = await fn(key, *args, **kwargs)
            await _record_result(failure_key, result)
            return result
        return cast(F, wrapper)
    return decorator
