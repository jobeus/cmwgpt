"""
Message Service - Handles message formatting and sending
"""

import asyncio
import logging
from typing import Awaitable, Callable, List, Optional, Protocol, Union

import discord
from discord import HTTPException, Forbidden, NotFound

from src.utils.discord_error_utils import concise_error
from src.utils.message_utils import format_attachment_message, format_prompt_message

logger = logging.getLogger(__name__)

SEND_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0


class PasteUploader(Protocol):
    async def upload_markdown(self, markdown_text: str) -> str:
        """Upload markdown content and return a URL."""


class MessageService:
    """Service for handling message operations."""

    DISCORD_MESSAGE_LIMIT = 2000

    def __init__(self, paste_service_instance: Optional[PasteUploader] = None):
        self._paste_service = paste_service_instance

    def set_paste_service(self, paste_service_instance: Optional[PasteUploader]) -> None:
        self._paste_service = paste_service_instance

    async def _upload_to_paste(self, reply_text: str) -> str:
        if not self._paste_service:
            raise RuntimeError("Paste service is disabled")
        return await self._paste_service.upload_markdown(reply_text)

    async def fit_to_discord(self, text: str) -> str:
        """``text`` as-is if it fits in one message, else a link to it on the paste service."""
        if len(text) <= self.DISCORD_MESSAGE_LIMIT:
            return text
        logger.info("Reply exceeded %d characters, uploading to paste service", self.DISCORD_MESSAGE_LIMIT)
        try:
            pasted_url = await self._upload_to_paste(text)
            return f"My response was too long to post here, so I've uploaded it to: {pasted_url}"
        except Exception as e:
            logger.error(f"Error uploading to paste service: {e}")
            return (
                f"The content of my response was over {self.DISCORD_MESSAGE_LIMIT} characters "
                "(discord limit), and there was a problem uploading it to paste service. "
                "Sorry, try again later."
            )

    async def _send_with_retry(
        self,
        send: Callable[..., Awaitable[object]],
        content: str,
        files: Optional[List[discord.File]] = None,
        **kwargs,
    ) -> None:
        """Send, retrying Discord 5xx and unexpected errors with backoff.

        Rate limits (429) are already retried inside discord.py. discord.py
        closes the files after an attempt, so each retry rewinds them first.
        """
        for attempt in range(1, SEND_ATTEMPTS + 1):
            if files:
                for f in files:
                    f.reset()
            try:
                await send(content=content, files=files or [], suppress_embeds=True, **kwargs)
                return
            except (Forbidden, NotFound) as e:
                logger.error(f"Discord permission/not-found error while sending: {e}")
                raise
            except HTTPException as e:
                if e.status == 413 and files:
                    logger.error("File attachment too large for Discord")
                    await send(
                        content="Sorry, the generated files are too large to upload to Discord.",
                        suppress_embeds=True, **kwargs)
                    return
                if e.status < 500 or attempt == SEND_ATTEMPTS:
                    logger.error(f"Discord HTTP error while sending: {concise_error(e)}")
                    raise
                logger.warning(f"Discord server error on attempt {attempt}: {concise_error(e)}")
            except Exception as e:
                if attempt == SEND_ATTEMPTS:
                    logger.error(f"Unexpected error sending message, giving up: {e}")
                    raise
                logger.warning(f"Unexpected error sending message on attempt {attempt}: {e}")
            await asyncio.sleep(RETRY_BASE_DELAY_SECONDS * 2 ** (attempt - 1))

    async def send_channel_reply(self, channel: discord.TextChannel, reply_text: str) -> None:
        """Send a reply to a channel, via the paste service if it's too long."""
        await self._send_with_retry(channel.send, await self.fit_to_discord(reply_text))

    async def send_channel_reply_with_files(
            self,
            channel: discord.TextChannel,
            reply_text: str,
            files: Optional[List[discord.File]] = None) -> None:
        """Send a reply with optional file attachments, via the paste service if it's too long."""
        await self._send_with_retry(channel.send, await self.fit_to_discord(reply_text), files=files)

    async def send_interaction_followup(
            self,
            interaction: discord.Interaction,
            text: str,
            *,
            ephemeral: bool = False) -> None:
        """Send an interaction followup, via the paste service if it's too long."""
        await self._send_with_retry(
            interaction.followup.send, await self.fit_to_discord(text), ephemeral=ephemeral)

    # Delegate to utility functions for message formatting
    def format_attachment_message(
            self,
            attachment: Union[discord.Attachment, List[discord.Attachment]],
            message: str) -> str:
        """Format a message with one or more attachment URLs."""
        return format_attachment_message(attachment, message)

    def format_prompt_message(self, message: str) -> str:
        """Format a prompt message."""
        return format_prompt_message(message)
