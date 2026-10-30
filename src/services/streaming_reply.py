"""
Streaming Reply - Shows a mention reply in Discord while the model is still writing it.

The model's service calls ``push`` with the reply so far, and ``status`` says what the
bot is waiting on (reading links, thinking, a tool call), shown as a small line under
the text until more text arrives. The first update posts a message and later ones edit
it, at most once per ``EDIT_INTERVAL_SECONDS`` to stay clear of Discord's edit rate
limit. ``finish`` swaps the preview for the final reply; ``discard`` deletes it when the
request fails.
"""

import asyncio
import contextlib
import logging
import time
from typing import Any, List, Optional

import discord

from src.utils.discord_error_utils import concise_error

logger = logging.getLogger(__name__)

EDIT_INTERVAL_SECONDS = 1.5
# Shown at the end of the preview while the reply is still coming in.
CURSOR = " ▌"


class StreamingReply:
    """One mention's reply, posted early and edited as more text arrives."""

    def __init__(self, channel: discord.abc.Messageable, message_service: Any):
        self._channel = channel
        self._message_service = message_service
        self._message: Optional[discord.Message] = None
        self._latest = ""
        self._status = ""
        self._shown = ""
        self._next_flush_at = 0.0
        self._task: Optional[asyncio.Task] = None
        self._sending = False
        self._closed = False
        self._failed = False

    @property
    def started(self) -> bool:
        """Whether a preview message has been posted."""
        return self._message is not None

    def push(self, text: str) -> None:
        """Record the reply so far and schedule a preview update.

        New text clears the status. Empty text clears what's shown (a discarded draft).
        """
        if self._closed or self._failed:
            return
        self._latest = text if text.strip() else ""
        if self._latest:
            self._status = ""
        self._schedule()

    def status(self, text: str) -> None:
        """Show what the bot is doing, like ``reading links...``, under the reply so far."""
        if self._closed or self._failed:
            return
        self._status = text.strip()
        self._schedule()

    def _schedule(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._flush())

    async def _flush(self) -> None:
        delay = self._next_flush_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        if self._closed:
            return
        preview = _preview(self._latest, self._status, self._message_service.DISCORD_MESSAGE_LIMIT)
        if not preview or preview == self._shown:
            return
        self._sending = True
        try:
            if self._message is None:
                self._message = await self._channel.send(content=preview, suppress_embeds=True)
            else:
                await self._message.edit(content=preview, suppress=True)
            self._shown = preview
        except Exception as e:
            # Stop previewing; finish() still delivers the full reply.
            logger.warning(f"Couldn't update the streamed reply preview: {concise_error(e)}")
            self._failed = True
        finally:
            self._sending = False
            self._next_flush_at = time.monotonic() + EDIT_INTERVAL_SECONDS

    async def _stop(self) -> None:
        """Stop previewing, letting a send already in flight land so it isn't posted twice."""
        self._closed = True
        task = self._task
        if task is None or task.done():
            return
        if not self._sending:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def finish(self, text: str, files: Optional[List[discord.File]] = None) -> None:
        """Replace the preview with the final reply, or post it if no preview went out."""
        await self._stop()
        if self._message is not None:
            content = await self._message_service.fit_to_discord(text)
            try:
                if files:
                    await self._message.edit(content=content, attachments=files, suppress=True)
                else:
                    await self._message.edit(content=content, suppress=True)
                return
            except Exception as e:
                logger.warning(f"Couldn't finish the streamed reply, posting it fresh: {concise_error(e)}")
                await self.discard()
        if files:
            await self._message_service.send_channel_reply_with_files(self._channel, text, files)
        else:
            await self._message_service.send_channel_reply(self._channel, text)

    async def discard(self) -> None:
        """Stop previewing and delete the preview, if one was posted."""
        await self._stop()
        message, self._message = self._message, None
        if message is not None:
            try:
                await message.delete()
            except Exception as e:
                logger.warning(f"Couldn't delete the streamed reply preview: {concise_error(e)}")


def _preview(text: str, status: str, limit: int) -> str:
    """The reply so far with a cursor and the status under it, cut to fit in one Discord message."""
    text = text.strip()
    status_line = f"-# {status}" if status else ""
    if not text:
        return status_line
    tail = CURSOR + (f"\n{status_line}" if status_line else "")
    if len(text) + len(tail) > limit:
        text = text[: limit - len(tail) - 1] + "…"
    return text + tail
