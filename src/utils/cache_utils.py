"""
Cache Utilities - Persistent cross-restart caching
"""

import functools
import inspect
import os
import json
import logging
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Optional, TypeVar, cast

logger = logging.getLogger(__name__)

CACHE_DIR = ".cache"

F = TypeVar("F", bound=Callable[..., Any])


class PersistentCache:
    """A dictionary-like cache that automatically saves to disk."""

    def __init__(self, cache_name: str, max_size: int = 200):
        self.cache_name = cache_name
        self.max_size = max_size
        self.filepath = os.path.join(CACHE_DIR, f"{cache_name}.json")
        self._cache: Dict[str, Any] = {}
        # Mutations + saves happen from both the event loop and to_thread
        # workers, so serialize them behind a lock.
        self._lock = threading.Lock()

        # Ensure directory exists
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR, exist_ok=True)

        self._load()

    def _load(self):
        """Load the cache from disk if it exists."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    self._cache = json.load(f)
                logger.debug(f"Loaded {len(self._cache)} items into {self.cache_name} cache from disk.")
            except Exception as e:
                logger.error(f"Failed to load cache {self.cache_name}: {e}")
                self._cache = {}

    def _save(self):
        """Save the cache to disk. Callers must hold ``self._lock``."""
        try:
            # We write to a uniquely-named temp file in the same directory and
            # atomically rename it into place, so concurrent saves from
            # different threads can never corrupt or clobber each other.
            fd, temp_filepath = tempfile.mkstemp(
                dir=os.path.dirname(self.filepath) or ".",
                prefix=f"{self.cache_name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(self._cache, f)
                os.replace(temp_filepath, self.filepath)
            except Exception:
                try:
                    os.remove(temp_filepath)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.error(f"Failed to save cache {self.cache_name} to disk: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._cache

    def __getitem__(self, key: str) -> Any:
        return self._cache[key]

    def __setitem__(self, key: str, value: Any):
        with self._lock:
            # Evict oldest if full (simple dictionary ordering is insertion order in modern Python)
            if len(self._cache) >= self.max_size and key not in self._cache:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]

            self._cache[key] = value
            self._save()

    def __len__(self) -> int:
        return len(self._cache)

    def delete(self, key: str):
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._save()


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
        _fetch_failures = PersistentCache("fetch_failures", max_size=1000)
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
    """
    def decorator(fn: F) -> F:
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(key: str, *args: Any, **kwargs: Any) -> Any:
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
            failure_key = f"{kind}:{key}"
            if _recently_failed(failure_key):
                logger.debug(f"Skipping {failure_key}: failed within the last hour")
                return None
            result = fn(key, *args, **kwargs)
            _record_result(failure_key, result)
            return result
        return cast(F, sync_wrapper)
    return decorator
