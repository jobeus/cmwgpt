"""Redis storage for per-user memory and per-channel summaries.

One key per user holds their current profile and nothing else (no history):

- ``cmwgpt:memory:<guild_id>:<user_id>`` - the profile text, at most ``MAX_PROFILE_BYTES``
- ``cmwgpt:channel_summary:<guild_id>:<channel_id>`` - what's been going on lately in a
  channel, at most ``MAX_PROFILE_BYTES``
- ``cmwgpt:memory_job:<guild_id>`` - hash with the nightly job's ``processed_through``,
  ``last_run_at`` and ``last_status``
- ``cmwgpt:self:<guild_id>`` - hash of the bot's own state in that server: one field per
  aspect (``mood``, ``interests``, ...) plus ``<aspect>_updated`` with when it last changed,
  ``refreshed_at`` with when the scheduled refresh last rewrote it, and ``follow_ups``, a
  JSON list of things to ask people about later, and ``idle_ask``, the last follow-up the
  bot asked about unprompted as ``<channel_id>:<message_id>``
- ``cmwgpt:day_summary:<channel_id>`` - hash with today's running chat summary for a
  channel (``text``), the ``day_start`` it covers from and the ``until_id`` it stops
  before, so a restart doesn't re-summarize the whole day. Expires after
  ``DAY_SUMMARY_TTL_SECONDS``.

Only the day summaries expire. All datetimes passed in and returned are naive UTC.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import redis.asyncio as aioredis

from src.db.redis_client import KEY_PREFIX, get_async_redis

logger = logging.getLogger(__name__)

# One user's profile may not grow past this; the merge prompts keep them far smaller.
MAX_PROFILE_BYTES = 100 * 1024

# A day summary is only used until the next 02:00, so it can go a little after that.
DAY_SUMMARY_TTL_SECONDS = 30 * 3600


def _profile_key(guild_id: int, user_id: int) -> str:
    return f"{KEY_PREFIX}memory:{guild_id}:{user_id}"


def _channel_summary_key(guild_id: int, channel_id: int) -> str:
    return f"{KEY_PREFIX}channel_summary:{guild_id}:{channel_id}"


def _job_key(guild_id: int) -> str:
    return f"{KEY_PREFIX}memory_job:{guild_id}"


def _self_key(guild_id: int) -> str:
    return f"{KEY_PREFIX}self:{guild_id}"


def _day_summary_key(channel_id: int) -> str:
    return f"{KEY_PREFIX}day_summary:{channel_id}"


def cap_profile(profile: str) -> str:
    """Trim a profile to ``MAX_PROFILE_BYTES`` of UTF-8 without splitting a character."""
    encoded = profile.encode("utf-8")
    if len(encoded) <= MAX_PROFILE_BYTES:
        return profile
    return encoded[:MAX_PROFILE_BYTES].decode("utf-8", errors="ignore")


class MemoryStore:
    """Reads and writes user memory in Redis."""

    def __init__(self, redis_getter: Callable[[], aioredis.Redis] = get_async_redis):
        self._redis_getter = redis_getter

    async def get(self, guild_id: int, user_id: int) -> str:
        """Return one user's profile, or an empty string if none."""
        return await self._redis_getter().get(_profile_key(guild_id, user_id)) or ""

    async def get_many(self, guild_id: int, user_ids: Iterable[int]) -> Dict[int, str]:
        """Return ``{user_id: profile}`` for the users that have one."""
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        values = await self._redis_getter().mget([_profile_key(guild_id, uid) for uid in ids])
        return {uid: value for uid, value in zip(ids, values, strict=True) if value}

    async def list_user_ids(self, guild_id: int) -> List[int]:
        """The IDs of every user in a server who has a profile, sorted."""
        prefix = f"{KEY_PREFIX}memory:{guild_id}:"
        client = self._redis_getter()
        ids = []
        async for key in client.scan_iter(match=f"{prefix}*", count=500):
            key = key.decode() if isinstance(key, bytes) else key
            if key[len(prefix):].isdigit():
                ids.append(int(key[len(prefix):]))
        return sorted(ids)

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

    async def get_channel_summary(self, guild_id: int, channel_id: int) -> str:
        """Return a channel's summary, or an empty string if none."""
        return await self._redis_getter().get(_channel_summary_key(guild_id, channel_id)) or ""

    async def save_channel_summary(self, guild_id: int, channel_id: int, summary: str) -> None:
        """Replace a channel's summary (an empty one removes it)."""
        key = _channel_summary_key(guild_id, channel_id)
        if not summary:
            await self._redis_getter().delete(key)
            return
        await self._redis_getter().set(key, cap_profile(summary))

    async def clear_channel_summaries(self, guild_id: int) -> None:
        """Wipe every channel summary in a server (used by /fillmemory)."""
        client = self._redis_getter()
        match = f"{KEY_PREFIX}channel_summary:{guild_id}:*"
        keys = [key async for key in client.scan_iter(match=match, count=500)]
        if keys:
            await client.delete(*keys)

    async def get_day_summary(self, channel_id: int) -> Optional[Tuple[datetime, Optional[int], str]]:
        """A channel's saved day summary as ``(day_start, until_id, text)``, or ``None``.

        ``day_start`` is the aware datetime the summary was saved with.
        """
        fields = await self._redis_getter().hgetall(_day_summary_key(channel_id))
        if not fields or not fields.get("day_start"):
            return None
        until_id = fields.get("until_id")
        return datetime.fromisoformat(fields["day_start"]), int(until_id) if until_id else None, fields.get("text", "")

    async def save_day_summary(
        self, channel_id: int, day_start: datetime, until_id: Optional[int], text: str
    ) -> None:
        """Replace a channel's day summary."""
        key = _day_summary_key(channel_id)
        client = self._redis_getter()
        await client.hset(key, mapping={
            "day_start": day_start.isoformat(),
            "until_id": str(until_id or ""),
            "text": cap_profile(text),
        })
        await client.expire(key, DAY_SUMMARY_TTL_SECONDS)

    async def get_self_state(self, guild_id: int) -> Dict[str, str]:
        """The bot's own state in a server (aspects and their ``_updated`` stamps), or ``{}``."""
        return await self._redis_getter().hgetall(_self_key(guild_id)) or {}

    async def save_self_state(self, guild_id: int, fields: Dict[str, str]) -> None:
        """Set some of the bot's own state; each aspect's ``_updated`` stamp becomes now.

        An empty value removes that aspect.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
        client = self._redis_getter()
        kept = {name: cap_profile(value) for name, value in fields.items() if value}
        removed = [name for name, value in fields.items() if not value]
        if kept:
            mapping = dict(kept)
            mapping.update({f"{name}_updated": now for name in kept})
            await client.hset(_self_key(guild_id), mapping=mapping)
        if removed:
            await client.hdel(_self_key(guild_id), *removed, *(f"{name}_updated" for name in removed))

    async def save_follow_ups(self, guild_id: int, items: List[Dict[str, Any]]) -> None:
        """Replace the bot's follow-up list (an empty one removes it)."""
        if items:
            await self._redis_getter().hset(_self_key(guild_id), "follow_ups", json.dumps(items))
        else:
            await self._redis_getter().hdel(_self_key(guild_id), "follow_ups")

    async def set_self_field(self, guild_id: int, name: str, value: str) -> None:
        """Set one bookkeeping field (like ``idle_ask``) in the bot's state."""
        await self._redis_getter().hset(_self_key(guild_id), name, value)

    async def mark_self_refreshed(self, guild_id: int, when: datetime) -> None:
        await self._redis_getter().hset(_self_key(guild_id), "refreshed_at", when.isoformat())

    async def clear_self_state(self, guild_id: int) -> None:
        await self._redis_getter().delete(_self_key(guild_id))

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
