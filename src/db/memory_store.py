"""MariaDB storage for per-user memory (``user_memory`` and friends in ``init_db.sql``).

All datetimes passed in and returned are naive UTC.
"""

import logging
from datetime import datetime
from typing import Awaitable, Callable, Dict, Iterable, Optional

import aiomysql

from src.db.connection import get_db_pool

logger = logging.getLogger(__name__)


class MemoryStore:
    """Reads and writes user memory rows. Every write also records a history row."""

    def __init__(self, pool_getter: Callable[[], Awaitable[aiomysql.Pool]] = get_db_pool):
        self._pool_getter = pool_getter

    async def get(self, guild_id: int, user_id: int) -> str:
        """Return one user's profile, or an empty string if none."""
        rows = await self.get_many(guild_id, [user_id])
        return rows.get(user_id, "")

    async def get_many(self, guild_id: int, user_ids: Iterable[int]) -> Dict[int, str]:
        """Return ``{user_id: profile}`` for the users that have a row."""
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        placeholders = ", ".join(["%s"] * len(ids))
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT user_id, profile FROM user_memory "
                    f"WHERE guild_id = %s AND user_id IN ({placeholders})",
                    (guild_id, *ids),
                )
                rows = await cur.fetchall()
        return {int(user_id): profile or "" for user_id, profile in rows}

    async def save_profile(
        self,
        guild_id: int,
        user_id: int,
        profile: str,
        *,
        source: str,
        observations: Optional[str] = None,
        window_start: Optional[datetime] = None,
        window_end: Optional[datetime] = None,
    ) -> None:
        """Replace a user's profile."""
        await self._write(
            "INSERT INTO user_memory (guild_id, user_id, profile) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE profile = VALUES(profile)",
            (guild_id, user_id, profile),
            guild_id, user_id, source, observations, window_start, window_end,
        )

    async def clear_user(self, guild_id: int, user_id: int) -> None:
        """Wipe a user's profile."""
        await self._write(
            "UPDATE user_memory SET profile = '' WHERE guild_id = %s AND user_id = %s",
            (guild_id, user_id),
            guild_id, user_id, "clear",
        )

    async def clear_learned_profiles(self, guild_id: int) -> None:
        """Wipe every profile in a server (used by /fillmemory)."""
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE user_memory SET profile = '' WHERE guild_id = %s", (guild_id,))

    async def get_processed_through(self, guild_id: int) -> Optional[datetime]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT processed_through FROM user_memory_job_state WHERE guild_id = %s", (guild_id,)
                )
                row = await cur.fetchone()
        return row[0] if row else None

    async def set_job_state(self, guild_id: int, processed_through: datetime, status: str) -> None:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO user_memory_job_state (guild_id, processed_through, last_run_at, last_status) "
                    "VALUES (%s, %s, UTC_TIMESTAMP(), %s) "
                    "ON DUPLICATE KEY UPDATE processed_through = VALUES(processed_through), "
                    "last_run_at = VALUES(last_run_at), last_status = VALUES(last_status)",
                    (guild_id, processed_through, status[:255]),
                )

    async def _write(
        self,
        query: str,
        args: tuple,
        guild_id: int,
        user_id: int,
        source: str,
        observations: Optional[str] = None,
        window_start: Optional[datetime] = None,
        window_end: Optional[datetime] = None,
    ) -> None:
        """Run ``query`` then snapshot the user's row into ``user_memory_history``."""
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, args)
                await cur.execute(
                    "INSERT INTO user_memory_history "
                    "(guild_id, user_id, source, profile, observations, window_start, window_end) "
                    "SELECT guild_id, user_id, %s, profile, %s, %s, %s "
                    "FROM user_memory WHERE guild_id = %s AND user_id = %s",
                    (source, observations, window_start, window_end, guild_id, user_id),
                )
