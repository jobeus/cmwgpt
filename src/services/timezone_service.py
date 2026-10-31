"""
Timezone Service - Each person's time zone, so "8pm" means their 8pm.

The direct models save one with the ``set_timezone`` tool when someone says where they
are or what time it is for them, or their memory makes it clear. Every mention then
carries a ``<time_zones>`` block for the people in the conversation, with their local
time right now, and ``remind`` sets reminders in the asker's zone.

Redis: ``cmwgpt:timezones:<guild_id>`` is a hash of user ID -> IANA zone name
(``America/Denver``). It doesn't expire.
"""

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import redis.asyncio as aioredis

from src.db.redis_client import KEY_PREFIX, get_async_redis

logger = logging.getLogger(__name__)

# Common names people (and models) use, mapped to a zone.
ZONE_ALIASES = {
    "eastern": "America/New_York", "et": "America/New_York", "est": "America/New_York", "edt": "America/New_York",
    "central": "America/Chicago", "ct": "America/Chicago", "cst": "America/Chicago", "cdt": "America/Chicago",
    "mountain": "America/Denver", "mt": "America/Denver", "mst": "America/Denver", "mdt": "America/Denver",
    "arizona": "America/Phoenix",
    "pacific": "America/Los_Angeles", "pt": "America/Los_Angeles", "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "alaska": "America/Anchorage", "hawaii": "Pacific/Honolulu",
    "utc": "UTC", "gmt": "UTC",
}


def _key(guild_id: int) -> str:
    return f"{KEY_PREFIX}timezones:{guild_id}"


def resolve_zone(name: str) -> ZoneInfo:
    """An IANA zone (``America/Denver``) or a common alias (``mountain``, ``PST``). Raises ``ValueError``."""
    text = (name or "").strip()
    try:
        return ZoneInfo(ZONE_ALIASES.get(text.lower(), text))
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            f"'{name}' isn't a time zone I know. Use an IANA name like 'America/Denver' or 'Europe/London'."
        ) from None


def describe_zone(zone: ZoneInfo, now: Optional[datetime] = None) -> str:
    """``America/Denver (MDT, now Fri 2:03 PM)``."""
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    clock = local.strftime("%a %I:%M %p").replace(" 0", " ")
    return f"{zone.key} ({local:%Z}, now {clock})"


class TimezoneStore:
    """People's time zones in Redis, per server."""

    def __init__(self, redis_getter: Callable[[], aioredis.Redis] = get_async_redis):
        self._redis_getter = redis_getter

    async def get_many(self, guild_id: int, user_ids: Sequence[int]) -> Dict[int, ZoneInfo]:
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        names = await self._redis_getter().hmget(_key(guild_id), [str(uid) for uid in ids])
        zones: Dict[int, ZoneInfo] = {}
        for uid, name in zip(ids, names, strict=True):
            if name:
                try:
                    zones[uid] = ZoneInfo(name)
                except (ZoneInfoNotFoundError, ValueError):
                    logger.warning(f"Ignoring unknown saved time zone {name!r} for {uid}")
        return zones

    async def get(self, guild_id: int, user_id: int) -> Optional[ZoneInfo]:
        return (await self.get_many(guild_id, [user_id])).get(user_id)

    async def set(self, guild_id: int, user_id: int, zone: ZoneInfo) -> None:
        await self._redis_getter().hset(_key(guild_id), str(user_id), zone.key)

    async def time_zones_block(self, guild_id: int, user_ids: Sequence[int]) -> str:
        """The ``<time_zones>`` block for the people in a conversation, or ``""`` if none are known."""
        zones = await self.get_many(guild_id, user_ids)
        if not zones:
            return ""
        lines = "\n".join(f"<@{uid}>: {describe_zone(zone)}" for uid, zone in zones.items())
        return (
            "<time_zones>\n"
            "Saved time zones of people in this conversation. Use them for anyone's local time, and say "
            "the zone when you give a time.\n"
            f"{lines}\n"
            "</time_zones>"
        )
