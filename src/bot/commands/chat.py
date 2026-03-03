"""
Chat Commands - Handles chat-related Discord commands
"""

import logging
from typing import Optional

import discord
from discord import app_commands

from src.config import get_system_prompt, DEFAULT_MODEL, INCLUDE_USERNAMES
from src.utils.discord_helper import get_mention_legend, attachment_to_base64_data_url
from src.services.openai_service import openai_service, OpenAIServiceError
from src.services.message_service import message_service
from src.services.queue_service import queue_service
from src.services.state_service import state_service
import asyncio
from src.utils.youtube_utils import extract_video_ids, get_transcript

logger = logging.getLogger(__name__)


class ChatCommands:
    """Handles chat-related Discord commands."""

    def __init__(self, bot: discord.ext.commands.Bot):
        self.bot = bot

    def setup_commands(self) -> None:
        """Set up all chat-related commands."""
        self.bot.tree.add_command(self._create_chat_command())
        self.bot.tree.add_command(self._create_reset_command())

    def _create_chat_command(self) -> app_commands.Command:
        """Create the /chat command."""

        @app_commands.command(name="chat",
                              description="Send a message to the chatbot")
        @app_commands.describe(message="Your message",
                               attachment="Optional image to attach to the prompt")
        async def chat(interaction: discord.Interaction, message: str,
                       attachment: Optional[discord.Attachment] = None):
            # Immediately defer the interaction to avoid Discord's 3-second
            # timeout
            await interaction.response.defer(ephemeral=False, thinking=True)

            # Queue the command for FIFO processing
            queued = await queue_service.queue_command(interaction, self._handle_chat_command, message, attachment)

            if not queued:
                logger.warning(
                    f"""Failed to queue chat command from {
                        interaction.user} in #{
                        interaction.channel} - queue may be full"""
                )
                await interaction.followup.send(
                    "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
                )

        return chat

    def _create_reset_command(self) -> app_commands.Command:
        """Create the /reset command."""

        @app_commands.command(name="reset",
                              description="Reset the conversation history")
        async def reset(interaction: discord.Interaction):
            # Immediately defer the interaction to avoid Discord's 3-second
            # timeout
            await interaction.response.defer(ephemeral=False, thinking=True)

            # Queue the command for FIFO processing
            queued = await queue_service.queue_command(interaction, self._handle_reset_command)

            if not queued:
                logger.warning(
                    f"""Failed to queue reset command from {
                        interaction.user} in #{
                        interaction.channel} - queue may be full"""
                )
                await interaction.followup.send(
                    "Sorry, the bot is currently busy. Please try again in a moment.", ephemeral=True
                )

        return reset

    async def _handle_chat_command(self,
                                   interaction: discord.Interaction,
                                   message: str,
                                   attachment: Optional[discord.Attachment] = None) -> None:
        """
        Handle the /chat command.

        Args:
            interaction: The Discord interaction
            message: The user's message
            attachment: Optional image attachment
        """
        channel_id = interaction.channel.id

        # Mark channel as active
        state_service.mark_channel_active(channel_id)

        legend_section = await get_mention_legend(interaction.channel, self.bot.user)

        # Add username if configured
        prefix_message = f"<@{
            interaction.user.id}>: {message}" if INCLUDE_USERNAMES else message

        # Look for YouTube links and append transcripts
        video_ids = extract_video_ids(message)
        if video_ids:
            transcripts = []
            for vid_id in video_ids:
                try:
                    transcript_text = await asyncio.to_thread(get_transcript, vid_id)
                    if transcript_text:
                        transcripts.append(
                            f"Target Video ID {vid_id} Transcript:\n{transcript_text}")
                except Exception as e:
                    logger.warning(
                        f"[/chat] Failed to fetch transcript for {vid_id}: {e}")

            if transcripts:
                prefix_message += "\n\n------\nIncluded youtube link transcript follows:\n\n" + \
                    "\n\n".join(transcripts)

        # Initialize conversation if missing (no system prompt in conversation
        # array)
        conversation = state_service.get_conversation(channel_id)
        if conversation is None:
            conversation = []  # Empty conversation - system prompt will be added dynamically
            state_service.set_conversation(channel_id, conversation)
            state_service.set_model(channel_id, DEFAULT_MODEL)
            logger.info(
                f"[/chat] Channel {channel_id}: initialized conversation and model")

        # Construct content payload for OpenAI
        file_payloads = []
        if attachment:
            try:
                base64_data_url = await attachment_to_base64_data_url(attachment)

                if attachment.content_type and attachment.content_type.startswith(
                        'image/'):
                    # Image attachment
                    file_payloads = [
                        {"type": "image_url", "image_url": {"url": base64_data_url}},
                    ]
                    logger.info(
                        f"[/chat] Channel {channel_id}: payload with base64 image ({len(base64_data_url)} chars)"
                    )
                else:
                    # Non-image attachment (e.g., PDF)
                    file_payloads = [
                        {"type": "file", "file": {"url": base64_data_url}},
                    ]
                    logger.info(
                        f"[/chat] Channel {channel_id}: payload with base64 file ({len(base64_data_url)} chars)"
                    )
            except Exception as e:
                logger.error(
                    f"[/chat] Channel {channel_id}: Failed to convert attachment to base64: {e}")

                # Fallbacks using URLs
                if attachment.content_type and attachment.content_type.startswith(
                        'image/'):
                    file_payloads = [{"type": "image_url",
                                      "image_url": {"url": attachment.url}}, ]
                    logger.warning(
                        f"[/chat] Channel {channel_id}: Using image attachment URL as fallback (may expire)")
                else:
                    # Non-image attachment fallback
                    attachment_info = f"\n\n[Attached File: {
                        attachment.filename}, type: {
                        attachment.content_type}]"
                    prefix_message += attachment_info
                    logger.warning(
                        f"[/chat] Channel {channel_id}: Using textual file reference as fallback")

        # Construct the final content payload
        if file_payloads:
            content_payload = [
                {"type": "text", "text": prefix_message}] + file_payloads
        else:
            content_payload = prefix_message
            logger.info(
                f"[/chat] Channel {channel_id}: text payload only")

        # Log user input and add to conversation
        logger.info(f"[/chat] Channel {channel_id} User: {message}")
        state_service.add_message_to_conversation(
            channel_id, {"role": "user", "content": content_payload})

        # Get response from OpenAI (interaction already deferred in slash
        # command handler)
        async with interaction.channel.typing():
            try:
                current_conversation = state_service.get_conversation(
                    channel_id)
                current_model = state_service.get_model(
                    channel_id) or DEFAULT_MODEL

                # Get system prompt (channel-specific or default with legend)
                channel_system_prompt = state_service.get_system_prompt(
                    channel_id)
                if channel_system_prompt:
                    system_prompt = channel_system_prompt + "\n" + legend_section
                else:
                    system_prompt = get_system_prompt() + "\n" + legend_section

                reply = await openai_service.get_chat_completion(
                    model=current_model, messages=current_conversation, system_prompt=system_prompt, channel_id=channel_id, state_service=state_service
                )

                # Handle different response formats
                reply_text = reply
                files_to_upload = []

                if isinstance(reply, dict) and "text" in reply:
                    # Response includes files (from image generation or other
                    # tools)
                    reply_text = reply["text"]
                    files_to_upload = reply.get("files", [])

                # Log and store assistant reply
                logger.info(
                    f"[/chat] Channel {channel_id} Assistant: {reply_text}")
                state_service.add_message_to_conversation(
                    channel_id, {"role": "assistant", "content": reply_text}
                )

                # Prepare base message content
                if attachment:
                    base_content = message_service.format_attachment_message(
                        attachment, message)
                else:
                    base_content = message_service.format_prompt_message(
                        message)

                # Send response with files if any
                if files_to_upload:
                    await message_service.send_interaction_followup_with_files(interaction, base_content, reply_text, files_to_upload)
                else:
                    await message_service.send_interaction_followup(interaction, base_content, reply_text)

            except OpenAIServiceError as e:
                logger.error(f"❌ OpenAI API error in /chat command:\\n{e}")
                # Prepare base message content for error response
                if attachment:
                    base_content = message_service.format_attachment_message(
                        attachment, message)
                else:
                    base_content = message_service.format_prompt_message(
                        message)

                error_message = (
                    f"{base_content}\n\nSorry, I encountered an error while processing your request: {
                        str(e)}")

                try:
                    await interaction.followup.send(content=error_message)
                except Exception as discord_error:
                    logger.error(
                        f"Failed to send error message to Discord: {discord_error}")
                    # Try to send a simpler error message
                    try:
                        await interaction.followup.send(
                            content="Sorry, I encountered an error and couldn't send my response. Please try again later."
                        )
                    except Exception:
                        logger.error("Failed to send fallback error message")

            except Exception as e:
                import traceback
                error_dump = traceback.format_exc()
                logger.error(f"🚨 Unexpected error in /chat command! Pretty-formatted dump:\\n====== ERROR DUMP ======\\n{error_dump}\\n========================")
                # Prepare base message content for error response
                if attachment:
                    base_content = message_service.format_attachment_message(
                        attachment, message)
                else:
                    base_content = message_service.format_prompt_message(
                        message)

                error_message = f"{base_content}\n\nSorry, I encountered an unexpected error. Please try again later."

                try:
                    await interaction.followup.send(content=error_message)
                except Exception as discord_error:
                    logger.error(
                        f"Failed to send error message to Discord: {discord_error}")
                    # Try to send a simpler error message
                    try:
                        await interaction.followup.send(
                            content="Sorry, I encountered an error and couldn't send my response. Please try again later."
                        )
                    except Exception:
                        logger.error("Failed to send fallback error message")

    async def _handle_reset_command(
            self, interaction: discord.Interaction) -> None:
        """
        Handle the /reset command.

        Args:
            interaction: The Discord interaction
        """
        channel_id = interaction.channel.id

        # Reset conversation and model (no system prompt in conversation array)
        state_service.clear_conversation(channel_id)
        state_service.clear_response_id(channel_id)
        state_service.set_model(channel_id, DEFAULT_MODEL)

        logger.info(f"[/reset] Channel {channel_id}: conversation reset")
        # Interaction already deferred in slash command handler
        await interaction.followup.send("Conversation reset.", ephemeral=True)
