"""
Message Service - Handles message formatting and sending
"""

import asyncio
import logging

import discord
from discord import HTTPException, Forbidden, NotFound

from .paste_service import paste_service


logger = logging.getLogger(__name__)


class MessageService:
    """Service for handling message operations."""

    DISCORD_MESSAGE_LIMIT = 2000

    async def send_channel_reply(
            self,
            channel: discord.TextChannel,
            reply_text: str) -> None:
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
                    await channel.send(reply_text)
                    return

                # Message is too long, try to upload to paste service
                try:
                    logger.info(
                        "Reply for channel message exceeded %d characters, attempting to upload to paste service",
                        self.DISCORD_MESSAGE_LIMIT,
                    )
                    pasted_url = paste_service.upload_markdown(reply_text)
                    final_reply = f"My response was too long to post here, so I've uploaded it to: {pasted_url}"
                    await channel.send(final_reply)
                    return
                except Exception as e:
                    logger.error(f"Error uploading to paste service: {e}")
                    error_reply = (
                        f"The content of my response was over {self.DISCORD_MESSAGE_LIMIT} characters "
                        "(discord limit), and there was a problem uploading it to paste service. "
                        "Sorry, try again later."
                    )
                    await channel.send(error_reply)
                    return

            except HTTPException as e:
                if e.status == 429:  # Rate limited
                    logger.warning(
                        f"Rate limited on attempt {
                            attempt + 1}: {e}")
                    if attempt < max_retries - 1:
                        # Extract retry-after from headers if available
                        retry_after = getattr(
                            e.response, "headers", {}).get("Retry-After")
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
                logger.error(
                    f"Discord NotFound error (channel/message not found): {e}")
                raise

            except Exception as e:
                logger.error(
                    f"Unexpected error sending message on attempt {
                        attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                logger.error("Max retries exceeded for unexpected error")
                raise

    async def send_interaction_followup(
            self,
            interaction: discord.Interaction,
            base_content: str,
            reply_text: str) -> None:
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
                    await interaction.followup.send(content=final_content)
                    return

                # Message would be too long, upload reply to paste service
                try:
                    logger.info(
                        "Reply for interaction followup exceeded %d characters with base_content, "
                        "attempting to upload to paste service", self.DISCORD_MESSAGE_LIMIT, )
                    pasted_url = paste_service.upload_markdown(reply_text)
                    final_content = (
                        f"{base_content}\n\n"
                        f"My detailed response was too long, so I've uploaded it here: {pasted_url}"
                    )
                    await interaction.followup.send(content=final_content, suppress_embeds=True)
                    return
                except Exception as e:
                    logger.error(
                        f"Error uploading to paste service for interaction: {e}")
                    error_content = (
                        f"{base_content}\n\n"
                        f"The content of my response was over {self.DISCORD_MESSAGE_LIMIT} characters, "
                        "and there was a problem uploading it. Sorry, try again later."
                    )
                    await interaction.followup.send(content=error_content)
                    return

            except HTTPException as e:
                if e.status == 429:  # Rate limited
                    logger.warning(
                        f"Rate limited on interaction followup attempt {
                            attempt + 1}: {e}")
                    if attempt < max_retries - 1:
                        # Extract retry-after from headers if available
                        retry_after = getattr(
                            e.response, "headers", {}).get("Retry-After")
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
                    logger.error(
                        f"Discord HTTP error on interaction followup: {e}")
                    raise

            except Forbidden as e:
                logger.error(
                    f"Discord Forbidden error on interaction followup (no permission): {e}")
                raise

            except NotFound as e:
                logger.error(
                    f"Discord NotFound error on interaction followup (interaction not found): {e}")
                raise

            except Exception as e:
                logger.error(
                    f"Unexpected error sending interaction followup on attempt {
                        attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                    continue
                logger.error("Max retries exceeded for unexpected error")
                raise

    def format_attachment_message(
            self,
            attachment: discord.Attachment,
            message: str) -> str:
        """
        Format a message with an attachment URL.

        Args:
            attachment: The Discord attachment
            message: The message text

        Returns:
            Formatted message string
        """
        return f"{attachment.url}\n> {message}"

    def format_prompt_message(self, message: str) -> str:
        """
        Format a prompt message.

        Args:
            message: The message text

        Returns:
            Formatted message string
        """
        return f"> {message}"


# Global service instance
message_service = MessageService()
