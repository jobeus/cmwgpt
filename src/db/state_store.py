"""Small JSON state kept in Redis: channel settings, restart state, death names, and the
per-channel conversation state that keeps the prompt cache warm across a restart.

Services read it once when they're built and write it as it changes. It uses the
synchronous Redis client: the values are tiny, Redis runs on the same host, and the
reads happen before the event loop starts. Under tests there's no Redis and the state
lives only in memory. After a Redis error the store skips Redis for a while, so an
outage can't stall every mention on socket timeouts.

Every key starts with ``KEY_PREFIX``. Values are JSON; hashes hold one JSON value per field.
"""

import json
import logging
import os
import time
from typing import Any, Callable, Dict, Optional

import redis

from src.db.redis_client import KEY_PREFIX, get_sync_redis

logger = logging.getLogger(__name__)

# After a Redis error, keep state only in memory for this long before trying again.
REDIS_RETRY_SECONDS = 30.0

# The prompt cache the mention replies write lasts an hour, so their per-channel
# conversation state is only worth restoring for that long.
CONTEXT_TTL_SECONDS = 3600

_FAILED = object()


def _decode(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        logger.warning("Ignoring a Redis state value that isn't JSON: %.80s", raw)
        return None


class StateStore:
    """JSON values and hashes of JSON values in Redis, or nowhere when ``redis_getter`` is None."""

    def __init__(self, redis_getter: Optional[Callable[[], redis.Redis]] = get_sync_redis):
        self._redis_getter = redis_getter
        self._down_until = 0.0

    def _call(self, what: str, action: Callable[[redis.Redis], Any]) -> Any:
        """Run ``action`` on the client, or return ``_FAILED`` without Redis or after an error."""
        if self._redis_getter is None or time.monotonic() < self._down_until:
            return _FAILED
        try:
            return action(self._redis_getter())
        except redis.RedisError as e:
            self._down_until = time.monotonic() + REDIS_RETRY_SECONDS
            logger.warning(
                "Redis %s failed; keeping state in memory only for %ds: %s", what, REDIS_RETRY_SECONDS, e)
            return _FAILED

    def get(self, name: str) -> Any:
        """The value saved under ``name``, or None."""
        raw = self._call(f"read of {name}", lambda r: r.get(KEY_PREFIX + name))
        return None if raw is _FAILED else _decode(raw)

    def set(self, name: str, value: Any, ttl_seconds: Optional[int] = None) -> bool:
        """Save ``value`` under ``name``. True if it reached Redis."""
        data = json.dumps(value)
        return self._call(f"write of {name}", lambda r: r.set(KEY_PREFIX + name, data, ex=ttl_seconds)) is not _FAILED

    def pop(self, name: str) -> Any:
        """The value saved under ``name``, deleting it."""
        raw = self._call(f"read of {name}", lambda r: r.getdel(KEY_PREFIX + name))
        return None if raw is _FAILED else _decode(raw)

    def get_all(self, name: str) -> Dict[str, Any]:
        """Every field of the hash ``name``, decoded."""
        raw = self._call(f"read of {name}", lambda r: r.hgetall(KEY_PREFIX + name))
        if raw is _FAILED or not raw:
            return {}
        return {field: value for field, value in ((f, _decode(v)) for f, v in raw.items()) if value is not None}

    def set_field(self, name: str, field: Any, value: Any, ttl_seconds: Optional[int] = None) -> None:
        """Save ``value`` as ``field`` of the hash ``name``; ``ttl_seconds`` (re)sets the whole hash's expiry."""
        data = json.dumps(value)

        def write(r: redis.Redis) -> None:
            pipe = r.pipeline(transaction=False)
            pipe.hset(KEY_PREFIX + name, str(field), data)
            if ttl_seconds:
                pipe.expire(KEY_PREFIX + name, ttl_seconds)
            pipe.execute()

        self._call(f"write of {name}", write)

    def delete_field(self, name: str, field: Any) -> None:
        self._call(f"delete from {name}", lambda r: r.hdel(KEY_PREFIX + name, str(field)))

    def import_legacy_file(self, path: str, name: str) -> Any:
        """Read a JSON state file from before Redis, once, if ``name`` isn't in Redis yet.

        Returns the file's contents (None if there's nothing to import) and renames the file
        to ``<path>.migrated``. Does nothing without a working Redis, so the file stays put
        until its contents can be saved.
        """
        if not path or not os.path.exists(path):
            return None
        exists = self._call(f"check of {name}", lambda r: r.exists(KEY_PREFIX + name))
        if exists is _FAILED or exists:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            os.replace(path, f"{path}.migrated")
        except (OSError, ValueError) as e:
            logger.error("Couldn't import %s into Redis: %s", path, e)
            return None
        logger.info("Imported %s into Redis key %s%s; renamed it to %s.migrated", path, KEY_PREFIX, name, path)
        return data
