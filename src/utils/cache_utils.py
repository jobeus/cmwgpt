"""
Cache Utilities - content caches kept in Redis so they survive restarts
"""

import functools
import inspect
import json
import logging
import os
import time
from collections import OrderedDict
from typing import Any, Callable, Generic, Hashable, Optional, TypeVar, cast

import redis

from src.db.redis_client import KEY_PREFIX, get_sync_redis

logger = logging.getLogger(__name__)

# Where the old JSON-file caches lived; each is imported into Redis once, then renamed.
CACHE_DIR = ".cache"

# Entries expire this long after they were last read or written, so a link that's
# still being talked about stays cached while dead ones age out on their own.
DEFAULT_TTL_SECONDS = 30 * 24 * 3600

# Values bigger than this aren't worth holding in Redis (a long voice note as base64).
MAX_VALUE_BYTES = 8 * 1024 * 1024

# Without Redis (tests, or while it's down) each cache is an in-memory LRU this big.
FALLBACK_MAX_SIZE = 200

# After a Redis error, use the in-memory fallback for this long before trying again.
REDIS_RETRY_SECONDS = 30.0

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
    """A dictionary-like cache stored in Redis as JSON, one key per entry.

    Keys are ``cmwgpt:cache:<name>:<key>`` and expire ``ttl_seconds`` after their last
    use. If Redis can't be reached the cache quietly works from memory instead, so a
    Redis outage costs refetches, never a failed reply.
    """

    def __init__(
        self,
        cache_name: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        client_getter: Callable[[], redis.Redis] = get_sync_redis,
    ):
        self.cache_name = cache_name
        self.ttl_seconds = ttl_seconds
        self._prefix = f"{KEY_PREFIX}cache:{cache_name}:"
        self._client_getter = client_getter
        self._client: Optional[redis.Redis] = None
        self._down_until = 0.0
        self._fallback: LRUCache[Any] = LRUCache(FALLBACK_MAX_SIZE)

    def _key(self, key: Hashable) -> str:
        return f"{self._prefix}{key}"

    def _redis(self) -> Optional[redis.Redis]:
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
            self._import_legacy_file()
        return self._client

    def _redis_failed(self, e: Exception) -> None:
        logger.warning(f"Redis error in cache {self.cache_name}, using memory for now: {e}")
        self._down_until = time.monotonic() + REDIS_RETRY_SECONDS

    def _import_legacy_file(self) -> None:
        """Copy an old ``.cache/<name>.json`` into Redis once, then rename it out of the way."""
        path = os.path.join(CACHE_DIR, f"{self.cache_name}.json")
        if not os.path.exists(path) or self._client is None:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
            pipe = self._client.pipeline(transaction=False)
            for key, value in entries.items():
                pipe.set(self._key(key), json.dumps(value), ex=self.ttl_seconds, nx=True)
            pipe.execute()
            os.replace(path, path + ".migrated")
            logger.info(f"Imported {len(entries)} entries from {path} into Redis")
        except Exception as e:
            logger.warning(f"Couldn't import {path} into Redis: {e}")

    def get(self, key: Hashable, default: Any = None) -> Any:
        client = self._redis()
        if client is not None:
            try:
                raw = client.getex(self._key(key), ex=self.ttl_seconds)
            except redis.RedisError as e:
                self._redis_failed(e)
            else:
                return default if raw is None else json.loads(raw)
        value = self._fallback.get(key)
        return default if value is None else value

    def __contains__(self, key: Hashable) -> bool:
        client = self._redis()
        if client is not None:
            try:
                return bool(client.exists(self._key(key)))
            except redis.RedisError as e:
                self._redis_failed(e)
        return key in self._fallback

    def __getitem__(self, key: Hashable) -> Any:
        missing = object()
        value = self.get(key, missing)
        if value is missing:
            raise KeyError(key)
        return value

    def __setitem__(self, key: Hashable, value: Any) -> None:
        raw = json.dumps(value)
        if len(raw) > MAX_VALUE_BYTES:
            logger.debug(f"Not caching {self.cache_name}:{key}: {len(raw)} bytes is too big")
            return
        client = self._redis()
        if client is not None:
            try:
                client.set(self._key(key), raw, ex=self.ttl_seconds)
                return
            except redis.RedisError as e:
                self._redis_failed(e)
        self._fallback[key] = value

    def delete(self, key: Hashable) -> None:
        self._fallback.pop(key)
        client = self._redis()
        if client is not None:
            try:
                client.delete(self._key(key))
            except redis.RedisError as e:
                self._redis_failed(e)


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


def _recently_failed(failure_key: str) -> bool:
    store = _failure_store()
    if store is None:
        return False
    failed_at = store.get(failure_key)
    if failed_at is None:
        return False
    if time.time() - failed_at < FETCH_FAILURE_TTL_SECONDS:
        return True
    store.delete(failure_key)
    return False


def _record_result(failure_key: str, result: Any) -> None:
    store = _failure_store()
    if store is None:
        return
    if result is None:
        store[failure_key] = time.time()
    elif failure_key in store:
        store.delete(failure_key)


def skip_recent_failures(kind: str) -> Callable[[F], F]:
    """Decorate a fetcher ``fn(key, ...) -> result | None`` so a ``None`` (failure)
    is remembered for ``FETCH_FAILURE_TTL_SECONDS`` and returned straight away for
    that key instead of fetching again. Works on sync and async functions.

    A call with ``cache_only=True`` is a lookup, not a fetch, so its ``None``
    (a cache miss) is passed through without being recorded.
    """
    def decorator(fn: F) -> F:
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(key: str, *args: Any, **kwargs: Any) -> Any:
                if kwargs.get("cache_only"):
                    return await fn(key, *args, **kwargs)
                failure_key = f"{kind}:{key}"
                if _recently_failed(failure_key):
                    logger.debug(f"Skipping {failure_key}: failed within the last hour")
                    return None
                result = await fn(key, *args, **kwargs)
                _record_result(failure_key, result)
                return result
            return cast(F, async_wrapper)

        @functools.wraps(fn)
        def sync_wrapper(key: str, *args: Any, **kwargs: Any) -> Any:
            if kwargs.get("cache_only"):
                return fn(key, *args, **kwargs)
            failure_key = f"{kind}:{key}"
            if _recently_failed(failure_key):
                logger.debug(f"Skipping {failure_key}: failed within the last hour")
                return None
            result = fn(key, *args, **kwargs)
            _record_result(failure_key, result)
            return result
        return cast(F, sync_wrapper)
    return decorator
