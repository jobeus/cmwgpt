"""
Day Summary Service - Keeps a running summary of each channel's chat since 02:00.

A mention only sends the last ``INCLUDE_NUM_CHATLINES`` messages, and user memories
only cover chat up to the last nightly run (02:00 America/Denver). Everything said
between the two would be invisible to the bot, so this service summarizes it:

* The first mention of the day summarizes 02:00 up to the oldest message in the
  mention's history window.
* While the history window holds still (mentions within the prompt cache lifetime),
  the summary is reused as-is.
* When the window moves forward, only the new stretch is read and folded into the
  existing summary.
* At 02:00 the summary starts over, since the nightly memory job has caught up.

Summaries are kept in memory and, when a store is given, saved to Redis so a restart
(every deploy is one) picks up where it left off instead of re-reading the day. Like
the memory job, only message text, reply
targets, embed titles/descriptions and attachment names are read: no URL fetching,
transcription or images.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional, Tuple

import discord

from src.services.completion_dispatch import dispatch_completion
from src.services.memory_service import (
    DAY_SUMMARY_SYSTEM_PROMPT,
    JOB_TIME,
    JOB_TIMEZONE,
    build_transcript_chunks,
    clean_profile,
    day_summary_request,
)

logger = logging.getLogger(__name__)


@dataclass
class _ChannelSummary:
    day_start: datetime
    # ID of the message the summary stops just before (the oldest message in the last
    # history window); the next stretch starts with it.
    until_id: Optional[int]
    text: str


def day_start_for(now: datetime) -> datetime:
    """The most recent 02:00 America/Denver at or before ``now`` (aware datetime)."""
    local_now = now.astimezone(JOB_TIMEZONE)
    start = datetime.combine(local_now.date(), JOB_TIME, tzinfo=JOB_TIMEZONE)
    if start > local_now:
        start = datetime.combine(local_now.date() - timedelta(days=1), JOB_TIME, tzinfo=JOB_TIMEZONE)
    return start


class DaySummaryService:
    """Summarizes each channel's chat between 02:00 and the start of a mention's history."""

    def __init__(
        self,
        *,
        openai_service: Any,
        gemini_service: Any = None,
        anthropic_service: Any = None,
        store: Any = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._openai_service = openai_service
        self._gemini_service = gemini_service
        self._anthropic_service = anthropic_service
        # A MemoryStore; without one, summaries only live until a restart.
        self._store = store
        self._now = now
        # channel_id -> summary for the current day
        self._summaries: Dict[int, _ChannelSummary] = {}

    async def get_summary(
        self, channel: Any, oldest_message: discord.Message, bot_id: int, model: str
    ) -> Tuple[str, float]:
        """The summary of ``channel`` from 02:00 up to (not including) ``oldest_message``.

        Returns ``(summary, cost)``; the summary is ``""`` when there's nothing before the
        history window today. Never raises: a failed update keeps the previous summary.
        """
        day_start = day_start_for(self._now())
        if oldest_message.created_at <= day_start:
            return "", 0.0

        entry = self._summaries.get(channel.id)
        if entry is None:
            entry = await self._load(channel.id)
        if entry is None or entry.day_start != day_start:
            entry = _ChannelSummary(day_start=day_start, until_id=None, text="")
            # Yesterday's summaries are stale for every channel, so drop them all.
            self._summaries = {
                cid: s for cid, s in self._summaries.items() if s.day_start == day_start
            }
            self._summaries[channel.id] = entry

        if entry.until_id is not None and oldest_message.id <= entry.until_id:
            return entry.text, 0.0

        after = discord.Object(id=entry.until_id - 1) if entry.until_id else day_start
        text, cost = entry.text, 0.0
        try:
            messages = [
                msg async for msg in channel.history(
                    limit=None, after=after, before=oldest_message, oldest_first=True
                )
            ]
            chunks, speakers = build_transcript_chunks([(getattr(channel, "name", "chat"), messages)], bot_id)
            for chunk in chunks:
                text, chunk_cost = await self._fold(text, chunk, speakers, model)
                cost += chunk_cost
        except Exception as e:
            logger.warning(f"Couldn't update the day summary for channel {channel.id}: {e}")
            return entry.text, cost
        entry.text, entry.until_id = text, oldest_message.id
        await self._save(channel.id, entry)
        if chunks:
            logger.info(
                f"Day summary for #{getattr(channel, 'name', channel.id)}: folded in {len(messages)} "
                f"messages (${cost:.4f})"
            )
        return entry.text, cost

    async def _load(self, channel_id: int) -> Optional[_ChannelSummary]:
        """The channel's saved summary, or ``None`` (no store, nothing saved, or Redis is down)."""
        if self._store is None:
            return None
        try:
            saved = await self._store.get_day_summary(channel_id)
        except Exception as e:
            logger.warning(f"Couldn't load the day summary for channel {channel_id}: {e}")
            return None
        if saved is None:
            return None
        day_start, until_id, text = saved
        return _ChannelSummary(day_start=day_start, until_id=until_id, text=text)

    async def _save(self, channel_id: int, entry: _ChannelSummary) -> None:
        if self._store is None:
            return
        try:
            await self._store.save_day_summary(channel_id, entry.day_start, entry.until_id, entry.text)
        except Exception as e:
            logger.warning(f"Couldn't save the day summary for channel {channel_id}: {e}")

    async def _fold(self, summary: str, transcript: str, speakers: Dict[int, str], model: str) -> Tuple[str, float]:
        reply, cost = await dispatch_completion(
            model=model,
            messages=[{"role": "user", "content": [{"type": "text", "text": day_summary_request(
                summary, transcript, speakers
            )}]}],
            system_prompt=DAY_SUMMARY_SYSTEM_PROMPT,
            openai_service=self._openai_service,
            gemini_service=self._gemini_service,
            anthropic_service=self._anthropic_service,
            identity={},
            search=False,
        )
        if isinstance(reply, dict):
            reply = reply.get("text", "")
        return clean_profile(str(reply or "")), cost or 0.0
