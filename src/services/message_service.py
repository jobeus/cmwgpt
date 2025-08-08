"""
Message Service - Handles message formatting and sending
"""

import asyncio
import logging
from typing import List, Optional

import discord
from discord import HTTPException, Forbidden, NotFound

from src.utils.pasters import paste_service
from src.utils.message_utils import format_attachment_message, format_prompt_message

logger = logging.getLogger(__name__)


class MessageService:
    """Service for handling message operations."""

    DISCORD_MESSAGE_LIMIT = 2000

    async def send_channel_reply(self, channel: discord.TextChannel, reply_text: str) -> None:
        """
        Sends a reply to a channel, handling potential paste upload for long messages.

        Args:
            channel: The Discord channel to send to
            reply_text: The reply text content
        """
        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                if len(reply_text) <= self.DISCORD_MESSAGE_LIMIT:
                    await channel.send(reply_text, suppress_embeds=True)
                    return

                # Message is too long, try to upload to paste service
                try:
                    logger.info(
                        "Reply for channel message exceeded %d characters, attempting to upload to paste service",
                        self.DISCORD_MESSAGE_LIMIT,
                    )
                    pasted_url = paste_service.upload_markdown(reply_text)
                    final_reply = f"My response was too long to post here, so I've uploaded it to: {pasted_url}"
                    await channel.send(final_reply, suppress_embeds=True)
                    return
                except Exception as e:
                    logger.error(f"Error uploading to paste service: {e}")
                    error_reply = (
                        f"The content of my response was over {self.DISCORD_MESSAGE_LIMIT} characters "
                        "(discord limit), and there was a problem uploading it to paste service. "
                        "Sorry, try again later."
                    )
                    await channel.send(error_reply, suppress_embeds=True)
                    return

            except HTTPException as e:
                if e.status == 429:  # Rate limited
                    logger.warning(
                        f"""Rate limited on attempt {
                        attempt + 1}: {e}"""
                    )
                    if attempt < max_retries - 1:
                        # Extract retry-after from headers if available
                        retry_after = getattr(e.response, "headers", {}).get("Retry-After")
                        if retry_after:
                            delay = float(retry_after)
                        else:
                            # Exponential backoff
                            delay = base_delay * (2**attempt)
                        logger.info(f"Retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                        continue
                    logger.error("Max retries exceeded for rate limit")
                    raise
                else:
                    logger.error(f"Discord HTTP error: {e}")
                    raise

            except Forbidden as e:
                logger.error(f"Discord Forbidden error (no permission): {e}")
                raise

            except NotFound as e:
                logger.error(f"Discord NotFound error (channel/message not found): {e}")
                raise

            except Exception as e:
                logger.error(
                    f"""Unexpected error sending message on attempt {
                    attempt + 1}: {e}"""
                )
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                logger.error("Max retries exceeded for unexpected error")
                raise

    async def send_interaction_followup(
        self, interaction: discord.Interaction, base_content: str, reply_text: str
    ) -> None:
        """
        Sends a followup to an interaction, handling potential paste upload for long replies.

        Args:
            interaction: The Discord interaction to follow up on
            base_content: The base content (e.g., prompt)
            reply_text: The reply text content
        """
        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                total_length = len(base_content + f"\n{reply_text}")

                if total_length <= self.DISCORD_MESSAGE_LIMIT:
                    final_content = f"{base_content}\n{reply_text}"
                    await interaction.followup.send(content=final_content, suppress_embeds=True)
                    return

                # Message would be too long, upload reply to paste service
                try:
                    logger.info(
                        "Reply for interaction followup exceeded %d characters with base_content, "
                        "attempting to upload to paste service",
                        self.DISCORD_MESSAGE_LIMIT,
                    )
                    pasted_url = paste_service.upload_markdown(reply_text)
                    final_content = (
                        f"{base_content}\n\n"
                        f"My detailed response was too long, so I've uploaded it here: {pasted_url}"
                    )
                    await interaction.followup.send(content=final_content, suppress_embeds=True)
                    return
                except Exception as e:
                    logger.error(f"Error uploading to paste service for interaction: {e}")
                    error_content = (
                        f"{base_content}\n\n"
                        f"The content of my response was over {self.DISCORD_MESSAGE_LIMIT} characters, "
                        "and there was a problem uploading it. Sorry, try again later."
                    )
                    await interaction.followup.send(content=error_content, suppress_embeds=True)
                    return

            except HTTPException as e:
                if e.status == 429:  # Rate limited
                    logger.warning(
                        f"""Rate limited on interaction followup attempt {
                            attempt + 1}: {e}"""
                    )
                    if attempt < max_retries - 1:
                        # Extract retry-after from headers if available
                        retry_after = getattr(e.response, "headers", {}).get("Retry-After")
                        if retry_after:
                            delay = float(retry_after)
                        else:
                            # Exponential backoff
                            delay = base_delay * (2**attempt)
                        logger.info(f"Retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                        continue
                    logger.error("Max retries exceeded for rate limit")
                    raise
                else:
                    logger.error(f"Discord HTTP error on interaction followup: {e}")
                    raise

            except Forbidden as e:
                logger.error(f"Discord Forbidden error on interaction followup (no permission): {e}")
                raise

            except NotFound as e:
                logger.error(f"Discord NotFound error on interaction followup (interaction not found): {e}")
                raise

            except Exception as e:
                logger.error(
                    f"""Unexpected error sending interaction followup on attempt {
                        attempt + 1}: {e}"""
                )
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                logger.error("Max retries exceeded for unexpected error")
                raise

    async def send_interaction_followup_with_files(
        self, interaction: discord.Interaction, base_content: str, reply_text: str, files: List[discord.File] = None
    ) -> None:
        """
        Sends a followup to an interaction with optional file attachments.

        Args:
            interaction: The Discord interaction to follow up on
            base_content: The base content (e.g., prompt)
            reply_text: The reply text content
            files: Optional list of Discord File objects to attach
        """
        max_retries = 3
        base_delay = 1.0

        if files is None:
            files = []

        for attempt in range(max_retries):
            try:
                total_length = len(base_content + f"\n{reply_text}")

                if total_length <= self.DISCORD_MESSAGE_LIMIT:
                    final_content = f"{base_content}\n{reply_text}"
                    await interaction.followup.send(content=final_content, files=files, suppress_embeds=True)
                    return

                # Message would be too long, upload reply to paste service
                try:
                    logger.info(
                        "Reply for interaction followup with files exceeded %d characters with base_content, "
                        "attempting to upload to paste service",
                        self.DISCORD_MESSAGE_LIMIT,
                    )
                    pasted_url = paste_service.upload_markdown(reply_text)
                    final_content = (
                        f"{base_content}\n\n"
                        f"My detailed response was too long, so I've uploaded it here: {pasted_url}"
                    )
                    await interaction.followup.send(content=final_content, files=files, suppress_embeds=True)
                    return
                except Exception as e:
                    logger.error(f"Error uploading to paste service: {e}")
                    error_content = (
                        f"{base_content}\n\n"
                        f"The content of my response was over {self.DISCORD_MESSAGE_LIMIT} characters "
                        "(discord limit), and there was a problem uploading it to paste service. "
                        "Sorry, try again later."
                    )
                    await interaction.followup.send(content=error_content, files=files, suppress_embeds=True)
                    return

            except HTTPException as e:
                if e.status == 413:  # Payload too large
                    logger.error("File attachment too large for Discord")
                    error_content = f"{base_content}\n\nSorry, the generated files are too large to upload to Discord."
                    await interaction.followup.send(content=error_content, suppress_embeds=True)
                    return
                elif e.status == 429:  # Rate limited
                    if attempt < max_retries - 1:
                        delay = base_delay * (2**attempt)
                        logger.warning(f"Rate limited, retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                        continue
                    logger.error("Max retries exceeded for rate limit")
                    raise
                else:
                    logger.error(f"HTTP error during interaction followup with files: {e}")
                    raise

            except (Forbidden, NotFound) as e:
                logger.error(f"Permission or channel error during interaction followup with files: {e}")
                raise

            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(f"Unexpected error during interaction followup with files, retrying in {delay} seconds: {e}")
                    await asyncio.sleep(delay)
                    continue
                logger.error("Max retries exceeded for unexpected error")
                raise

    async def send_channel_reply_with_files(self, channel: discord.TextChannel, reply_text: str, files: List[discord.File] = None) -> None:
        """
        Sends a reply to a channel with optional file attachments.

        Args:
            channel: The Discord channel to send to
            reply_text: The reply text content
            files: Optional list of Discord File objects to attach
        """
        max_retries = 3
        base_delay = 1.0

        if files is None:
            files = []

        for attempt in range(max_retries):
            try:
                if len(reply_text) <= self.DISCORD_MESSAGE_LIMIT:
                    await channel.send(content=reply_text, files=files, suppress_embeds=True)
                    return

                # Message is too long, try to upload to paste service
                try:
                    logger.info(
                        "Reply for channel message with files exceeded %d characters, attempting to upload to paste service",
                        self.DISCORD_MESSAGE_LIMIT,
                    )
                    pasted_url = paste_service.upload_markdown(reply_text)
                    final_reply = f"My response was too long to post here, so I've uploaded it to: {pasted_url}"
                    await channel.send(content=final_reply, files=files, suppress_embeds=True)
                    return
                except Exception as e:
                    logger.error(f"Error uploading to paste service: {e}")
                    error_reply = (
                        f"The content of my response was over {self.DISCORD_MESSAGE_LIMIT} characters "
                        "(discord limit), and there was a problem uploading it to paste service. "
                        "Sorry, try again later."
                    )
                    await channel.send(content=error_reply, files=files, suppress_embeds=True)
                    return

            except HTTPException as e:
                if e.status == 413:  # Payload too large
                    logger.error("File attachment too large for Discord")
                    error_content = "Sorry, the generated files are too large to upload to Discord."
                    await channel.send(content=error_content, suppress_embeds=True)
                    return
                elif e.status == 429:  # Rate limited
                    if attempt < max_retries - 1:
                        delay = base_delay * (2**attempt)
                        logger.warning(f"Rate limited, retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                        continue
                    logger.error("Max retries exceeded for rate limit")
                    raise
                else:
                    logger.error(f"HTTP error during channel reply with files: {e}")
                    raise

            except (Forbidden, NotFound) as e:
                logger.error(f"Permission or channel error during channel reply with files: {e}")
                raise

            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(f"Unexpected error during channel reply with files, retrying in {delay} seconds: {e}")
                    await asyncio.sleep(delay)
                    continue
                logger.error("Max retries exceeded for unexpected error")
                raise

    # Delegate to utility functions for message formatting
    def format_attachment_message(self, attachment: discord.Attachment, message: str) -> str:
        """Format a message with an attachment URL."""
        return format_attachment_message(attachment, message)

    def format_prompt_message(self, message: str) -> str:
        """Format a prompt message."""
        return format_prompt_message(message)


# Global service instance
message_service = MessageService()
