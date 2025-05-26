"""
Message Service - Handles message formatting and sending
"""

import logging

import discord

from .paste_service import paste_service


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
        except Exception as e:
            logger.error(f"Error uploading to paste service: {e}")
            error_reply = (
                f"The content of my response was over {self.DISCORD_MESSAGE_LIMIT} characters "
                "(discord limit), and there was a problem uploading it to paste service. "
                "Sorry, try again later."
            )
            await channel.send(error_reply)

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
        total_length = len(base_content + f"\n{reply_text}")

        if total_length <= self.DISCORD_MESSAGE_LIMIT:
            final_content = f"{base_content}\n{reply_text}"
            await interaction.followup.send(content=final_content)
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
                f"{base_content}\n\n" f"My detailed response was too long, so I've uploaded it here: {pasted_url}"
            )
            await interaction.followup.send(content=final_content, suppress_embeds=True)
        except Exception as e:
            logger.error(f"Error uploading to paste service for interaction: {e}")
            error_content = (
                f"{base_content}\n\n"
                f"The content of my response was over {self.DISCORD_MESSAGE_LIMIT} characters, "
                "and there was a problem uploading it. Sorry, try again later."
            )
            await interaction.followup.send(content=error_content)

    def format_attachment_message(self, attachment: discord.Attachment, message: str) -> str:
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
