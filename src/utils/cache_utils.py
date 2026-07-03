"""
Cache Utilities - Persistent cross-restart caching
"""

import os
import json
import logging
import tempfile
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)

CACHE_DIR = ".cache"


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
