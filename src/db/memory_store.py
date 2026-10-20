"""Redis storage for per-user memory.

One key per user holds their current profile and nothing else (no history):

- ``cmwgpt:memory:<guild_id>:<user_id>`` - the profile text, at most ``MAX_PROFILE_BYTES``
- ``cmwgpt:memory_job:<guild_id>`` - hash with the nightly job's ``processed_through``,
  ``last_run_at`` and ``last_status``
- ``cmwgpt:memory_migrated`` - set once the old MariaDB tables were copied in

None of these keys expire. All datetimes passed in and returned are naive UTC.
"""

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, Iterable, Optional

import aiomysql
import redis.asyncio as aioredis

from src.config import get_config
from src.db.connection import get_db_pool
from src.db.redis_client import KEY_PREFIX, get_async_redis

logger = logging.getLogger(__name__)

# One user's profile may not grow past this; the merge prompts keep them far smaller.
MAX_PROFILE_BYTES = 100 * 1024

MIGRATED_KEY = f"{KEY_PREFIX}memory_migrated"


def _profile_key(guild_id: int, user_id: int) -> str:
    return f"{KEY_PREFIX}memory:{guild_id}:{user_id}"


def _job_key(guild_id: int) -> str:
    return f"{KEY_PREFIX}memory_job:{guild_id}"


def cap_profile(profile: str) -> str:
    """Trim a profile to ``MAX_PROFILE_BYTES`` of UTF-8 without splitting a character."""
    encoded = profile.encode("utf-8")
    if len(encoded) <= MAX_PROFILE_BYTES:
        return profile
    return encoded[:MAX_PROFILE_BYTES].decode("utf-8", errors="ignore")


class MemoryStore:
    """Reads and writes user memory in Redis."""

    def __init__(
        self,
        redis_getter: Callable[[], aioredis.Redis] = get_async_redis,
        mariadb_pool_getter: Callable[[], Awaitable[aiomysql.Pool]] = get_db_pool,
    ):
        self._redis_getter = redis_getter
        self._mariadb_pool_getter = mariadb_pool_getter

    async def get(self, guild_id: int, user_id: int) -> str:
        """Return one user's profile, or an empty string if none."""
        return await self._redis_getter().get(_profile_key(guild_id, user_id)) or ""

    async def get_many(self, guild_id: int, user_ids: Iterable[int]) -> Dict[int, str]:
        """Return ``{user_id: profile}`` for the users that have one."""
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        values = await self._redis_getter().mget([_profile_key(guild_id, uid) for uid in ids])
        return {uid: value for uid, value in zip(ids, values) if value}

    async def save_profile(self, guild_id: int, user_id: int, profile: str) -> None:
        """Replace a user's profile (an empty one removes it)."""
        if not profile:
            await self.clear_user(guild_id, user_id)
            return
        capped = cap_profile(profile)
        if capped != profile:
            logger.warning(
                f"Memory for user {user_id} in guild {guild_id} was over {MAX_PROFILE_BYTES} bytes; trimmed"
            )
        await self._redis_getter().set(_profile_key(guild_id, user_id), capped)

    async def clear_user(self, guild_id: int, user_id: int) -> None:
        """Wipe a user's profile."""
        await self._redis_getter().delete(_profile_key(guild_id, user_id))

    async def clear_learned_profiles(self, guild_id: int) -> None:
        """Wipe every profile in a server (used by /fillmemory)."""
        client = self._redis_getter()
        keys = [key async for key in client.scan_iter(match=f"{KEY_PREFIX}memory:{guild_id}:*", count=500)]
        if keys:
            await client.delete(*keys)

    async def get_processed_through(self, guild_id: int) -> Optional[datetime]:
        value = await self._redis_getter().hget(_job_key(guild_id), "processed_through")
        return datetime.fromisoformat(value) if value else None

    async def set_job_state(self, guild_id: int, processed_through: datetime, status: str) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        await self._redis_getter().hset(
            _job_key(guild_id),
            mapping={
                "processed_through": processed_through.isoformat(),
                "last_run_at": now.isoformat(),
                "last_status": status[:255],
            },
        )

    async def migrate_from_mariadb(self) -> None:
        """Copy profiles and job state out of the old MariaDB tables, once.

        Runs at startup until it succeeds. Anything already in Redis wins, so a
        nightly run that happened before the copy isn't overwritten. Never raises.
        """
        try:
            client = self._redis_getter()
            if await client.exists(MIGRATED_KEY):
                return
        except Exception as e:
            logger.error(f"Redis is unavailable, so memory won't work until it is: {e}")
            return
        if not get_config().db_password:
            # No MariaDB was ever set up here, so there's nothing to bring over.
            await client.set(MIGRATED_KEY, "no mariadb configured")
            return

        try:
            pool = await self._mariadb_pool_getter()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT guild_id, user_id, profile FROM user_memory WHERE profile <> ''")
                    profiles = await cur.fetchall()
                    await cur.execute(
                        "SELECT guild_id, processed_through, last_run_at, last_status FROM user_memory_job_state"
                    )
                    job_states = await cur.fetchall()
        except Exception as e:
            logger.warning(f"Couldn't read memory from MariaDB to move it into Redis; will retry next start: {e}")
            return

        copied = 0
        for guild_id, user_id, profile in profiles:
            if await client.set(_profile_key(guild_id, user_id), cap_profile(profile), nx=True):
                copied += 1
        for guild_id, processed_through, last_run_at, last_status in job_states:
            key = _job_key(guild_id)
            if not await client.exists(key):
                await client.hset(key, mapping={
                    "processed_through": processed_through.isoformat(),
                    "last_run_at": last_run_at.isoformat() if last_run_at else "",
                    "last_status": last_status or "",
                })
        await client.set(MIGRATED_KEY, datetime.now(timezone.utc).isoformat())
        logger.info(
            f"Moved {copied} of {len(profiles)} user memories and {len(job_states)} job states from MariaDB to Redis"
        )
