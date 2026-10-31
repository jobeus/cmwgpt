"""
Reminder Service - Reminders the bot sets when asked, posted in their channel when due.

The direct models set them with the ``remind`` tool, for the asker or anyone else in the
server. Each is posted in the channel it was set in, pinging the people it's for, as a
reply to the message that asked for it. They're checked every ``POLL_SECONDS``, so one
fires within a minute of its time, and there's no limit on how far ahead one can be.

Redis keys:

- ``cmwgpt:reminders:due`` - sorted set of reminder IDs, scored by when they're due
  (Unix seconds)
- ``cmwgpt:reminder:<id>`` - hash with the reminder: ``guild_id``, ``channel_id``,
  ``message_id`` (the message that asked), ``user_ids`` (comma-separated), ``set_by``,
  ``text``, ``due_at`` and ``tz`` (the zone its time was given in, for showing it)
- ``cmwgpt:reminders:next_id`` - counter for new IDs

A reminder is removed once it's posted, or if its channel is gone. If posting fails
for another reason it's tried again on the next check.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
import redis.asyncio as aioredis

from src.db.redis_client import KEY_PREFIX, get_async_redis
from src.utils.discord_error_utils import concise_error
from src.utils.message_utils import prompt_timezone

logger = logging.getLogger(__name__)

POLL_SECONDS = 15
# A reminder posted more than this late says when it was meant for.
LATE_AFTER = timedelta(minutes=2)
MAX_REMINDER_CHARS = 1000
# Absolute times may be a little in the past (the model's clock is the mention's
# timestamp); anything older than this is refused.
PAST_GRACE = timedelta(minutes=1)
WHEN_FORMAT = "%Y-%m-%d %H:%M"
SHOWN_FORMAT = "%a %Y-%m-%d %I:%M %p %Z"

_DUE_KEY = f"{KEY_PREFIX}reminders:due"
_NEXT_ID_KEY = f"{KEY_PREFIX}reminders:next_id"

_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
# A unit ends at anything but a letter, so "2h30m" splits into 2h and 30m.
_UNIT = r"(minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|wks?|w)(?![a-z])"
_RELATIVE_PART = re.compile(r"(\d+)\s*" + _UNIT, re.IGNORECASE)
_RELATIVE_WHOLE = re.compile(r"^\s*(?:in\s+)?(?:\d+\s*" + _UNIT + r"[\s,]*(?:and\s+)?)+$", re.IGNORECASE)


def _reminder_key(reminder_id: int) -> str:
    return f"{KEY_PREFIX}reminder:{reminder_id}"


def parse_when(when: str, now: datetime, zone: Optional[Any] = None) -> datetime:
    """When a reminder is due: ``in 90 minutes``/``2h30m``/``3 days``, or ``YYYY-MM-DD HH:MM``.

    Absolute times are in ``zone``, or the prompt's timezone (the one the chat timestamps
    use) without one. Returns an aware UTC datetime. Raises ``ValueError`` for anything
    else or a time that's already passed.
    """
    text = (when or "").strip()
    if _RELATIVE_WHOLE.match(text):
        seconds = sum(int(n) * _UNIT_SECONDS[unit[0].lower()] for n, unit in _RELATIVE_PART.findall(text))
        if seconds <= 0:
            raise ValueError("That's not in the future.")
        return now + timedelta(seconds=seconds)
    try:
        local = datetime.strptime(text, WHEN_FORMAT)
    except ValueError:
        raise ValueError(
            "Give `when` as a time from now like 'in 90 minutes' or '2h30m', or as 'YYYY-MM-DD HH:MM' "
            "in the reminder's time zone."
        ) from None
    due = local.replace(tzinfo=zone or prompt_timezone()).astimezone(timezone.utc)
    if due < now - PAST_GRACE:
        raise ValueError(f"{text} has already passed.")
    return max(due, now)


def format_when(due: datetime, zone: Optional[Any] = None) -> str:
    """``Sat 2026-10-31 08:00 PM MDT``: ``due`` in ``zone``, or the chat's timezone."""
    return due.astimezone(zone or prompt_timezone()).strftime(SHOWN_FORMAT).replace(" 0", " ", 1)


@dataclass
class Reminder:
    id: int
    guild_id: int
    channel_id: int
    message_id: Optional[int]
    user_ids: List[int]
    set_by: int
    text: str
    due_at: datetime
    tz: str = ""

    @property
    def zone(self) -> Optional[ZoneInfo]:
        try:
            return ZoneInfo(self.tz) if self.tz else None
        except (ZoneInfoNotFoundError, ValueError):
            return None

    def describe(self) -> str:
        """One line for the model: ``#3 Sat 2026-10-31 8:00 PM MDT for <@1> (set by <@2>): trash``."""
        people = ", ".join(f"<@{uid}>" for uid in self.user_ids)
        return f"#{self.id} {format_when(self.due_at, self.zone)} for {people} (set by <@{self.set_by}>): {self.text}"


def _from_hash(reminder_id: int, fields: Dict[str, str]) -> Optional[Reminder]:
    try:
        return Reminder(
            id=reminder_id,
            guild_id=int(fields["guild_id"]),
            channel_id=int(fields["channel_id"]),
            message_id=int(fields["message_id"]) if fields.get("message_id") else None,
            user_ids=[int(uid) for uid in fields.get("user_ids", "").split(",") if uid],
            set_by=int(fields["set_by"]),
            text=fields.get("text", ""),
            due_at=datetime.fromtimestamp(float(fields["due_at"]), timezone.utc),
            tz=fields.get("tz", ""),
        )
    except (KeyError, ValueError):
        logger.warning(f"Skipping malformed reminder {reminder_id}: {fields}")
        return None


class ReminderStore:
    """Reminders in Redis."""

    def __init__(self, redis_getter: Callable[[], aioredis.Redis] = get_async_redis):
        self._redis_getter = redis_getter

    async def add(
        self, *, guild_id: int, channel_id: int, message_id: Optional[int], user_ids: Sequence[int],
        set_by: int, text: str, due_at: datetime, tz: str = "",
    ) -> Reminder:
        client = self._redis_getter()
        reminder_id = int(await client.incr(_NEXT_ID_KEY))
        reminder = Reminder(reminder_id, guild_id, channel_id, message_id, list(user_ids), set_by, text, due_at, tz)
        await client.hset(_reminder_key(reminder_id), mapping={
            "guild_id": str(guild_id),
            "channel_id": str(channel_id),
            "message_id": str(message_id or ""),
            "user_ids": ",".join(str(uid) for uid in user_ids),
            "set_by": str(set_by),
            "text": text,
            "due_at": str(due_at.timestamp()),
            "tz": tz,
        })
        await client.zadd(_DUE_KEY, {str(reminder_id): due_at.timestamp()})
        return reminder

    async def _load(self, ids: Sequence[str]) -> List[Reminder]:
        if not ids:
            return []
        pipe = self._redis_getter().pipeline(transaction=False)
        for reminder_id in ids:
            pipe.hgetall(_reminder_key(int(reminder_id)))
        reminders = []
        for reminder_id, fields in zip(ids, await pipe.execute(), strict=True):
            if fields:
                reminder = _from_hash(int(reminder_id), fields)
                if reminder:
                    reminders.append(reminder)
            else:
                # The hash is gone (removed mid-way); drop the stray ID.
                await self._redis_getter().zrem(_DUE_KEY, reminder_id)
        return reminders

    async def due(self, now: datetime) -> List[Reminder]:
        """Reminders due at or before ``now``, soonest first."""
        ids = await self._redis_getter().zrangebyscore(_DUE_KEY, "-inf", now.timestamp())
        return await self._load(ids)

    async def pending(self, channel_id: Optional[int] = None) -> List[Reminder]:
        """Every reminder not yet posted (in one channel, if given), soonest first."""
        reminders = await self._load(await self._redis_getter().zrange(_DUE_KEY, 0, -1))
        return [r for r in reminders if channel_id is None or r.channel_id == channel_id]

    async def get(self, reminder_id: int) -> Optional[Reminder]:
        fields = await self._redis_getter().hgetall(_reminder_key(reminder_id))
        return _from_hash(reminder_id, fields) if fields else None

    async def remove(self, reminder_id: int) -> None:
        client = self._redis_getter()
        await client.zrem(_DUE_KEY, str(reminder_id))
        await client.delete(_reminder_key(reminder_id))


def reminder_message(reminder: Reminder, now: datetime) -> str:
    """What the bot posts when a reminder is due."""
    people = " ".join(f"<@{uid}>" for uid in reminder.user_ids)
    lines = [f"⏰ {people} {reminder.text}".strip()]
    notes = []
    if reminder.set_by not in reminder.user_ids:
        notes.append(f"set by <@{reminder.set_by}>")
    if now - reminder.due_at > LATE_AFTER:
        notes.append(f"meant for {format_when(reminder.due_at, reminder.zone)}, sorry I'm late")
    if notes:
        lines.append(f"-# {', '.join(notes)}")
    return "\n".join(lines)


class ReminderService:
    """Sets, lists and cancels reminders, and posts them when they're due."""

    def __init__(
        self,
        *,
        store: Optional[ReminderStore] = None,
        is_testing: bool = False,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._store = store or ReminderStore()
        self._is_testing = is_testing
        self._now = now
        self._bot: Optional[discord.Client] = None
        self._task: Optional[asyncio.Task] = None

    def set_bot(self, bot: discord.Client) -> None:
        self._bot = bot

    def start(self) -> None:
        if self._is_testing or (self._task and not self._task.done()):
            return
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Reminders checked every {POLL_SECONDS}s")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self.deliver_due()
            except Exception as e:
                logger.warning(f"Couldn't check reminders: {concise_error(e)}")
            await asyncio.sleep(max(1.0, POLL_SECONDS - (time.monotonic() - started)))

    # -- for the tools ----------------------------------------------------------

    async def add(
        self, *, guild_id: int, channel_id: int, message_id: Optional[int], user_ids: Sequence[int],
        set_by: int, text: str, when: str, zone: Optional[ZoneInfo] = None,
    ) -> Reminder:
        """Set a reminder. ``zone`` is what an absolute ``when`` is in (default: the chat's).

        Raises ``ValueError`` with a reason the model can act on.
        """
        text = " ".join(text.split())
        if not text:
            raise ValueError("Say what the reminder is about.")
        if len(text) > MAX_REMINDER_CHARS:
            raise ValueError(f"Keep the reminder under {MAX_REMINDER_CHARS} characters.")
        due_at = parse_when(when, self._now(), zone)
        reminder = await self._store.add(
            guild_id=guild_id, channel_id=channel_id, message_id=message_id,
            user_ids=list(dict.fromkeys(user_ids or [set_by])), set_by=set_by, text=text, due_at=due_at,
            tz=zone.key if zone else "",
        )
        logger.info(f"[reminder] Set {reminder.describe()}")
        return reminder

    async def pending(self, channel_id: int) -> List[Reminder]:
        return await self._store.pending(channel_id)

    async def cancel(self, reminder_id: int, channel_id: int) -> Reminder:
        """Cancel a reminder set in ``channel_id``. Raises ``ValueError`` if there's no such reminder."""
        reminder = await self._store.get(reminder_id)
        if reminder is None or reminder.channel_id != channel_id:
            raise ValueError(f"There's no pending reminder #{reminder_id} in this channel.")
        await self._store.remove(reminder_id)
        logger.info(f"[reminder] Cancelled {reminder.describe()}")
        return reminder

    # -- delivery -----------------------------------------------------------------

    async def deliver_due(self) -> int:
        """Post every reminder that's due. Returns how many were posted."""
        if self._bot is None:
            return 0
        now = self._now()
        posted = 0
        for reminder in await self._store.due(now):
            if await self._deliver(reminder, now):
                posted += 1
        return posted

    async def _deliver(self, reminder: Reminder, now: datetime) -> bool:
        assert self._bot is not None
        channel: Any = self._bot.get_channel(reminder.channel_id)
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(reminder.channel_id)
            except (discord.NotFound, discord.Forbidden):
                logger.warning(f"[reminder] Dropping #{reminder.id}: channel {reminder.channel_id} is gone")
                await self._store.remove(reminder.id)
                return False
        content = reminder_message(reminder, now)
        # Ping only the people it's for, not whoever set it.
        mentions = discord.AllowedMentions(
            everyone=False, roles=False, users=[discord.Object(id=uid) for uid in reminder.user_ids])
        reference = None
        if reminder.message_id:
            reference = discord.MessageReference(
                message_id=reminder.message_id, channel_id=reminder.channel_id, fail_if_not_exists=False)
        try:
            await channel.send(content, allowed_mentions=mentions, reference=reference, mention_author=False)
        except discord.Forbidden:
            logger.warning(f"[reminder] Dropping #{reminder.id}: can't post in channel {reminder.channel_id}")
            await self._store.remove(reminder.id)
            return False
        except discord.HTTPException as e:
            logger.warning(f"[reminder] Couldn't post #{reminder.id}, will retry: {concise_error(e)}")
            return False
        await self._store.remove(reminder.id)
        logger.info(f"[reminder] Posted {reminder.describe()}")
        return True
