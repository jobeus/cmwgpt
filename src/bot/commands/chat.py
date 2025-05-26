"""
Chat Commands - Handles chat-related Discord commands
"""

import json
import logging
from typing import Optional

import discord
from discord import app_commands

from src.config import get_system_prompt, DEFAULT_MODEL, INCLUDE_USERNAMES
from src.utils.discord_helper import get_mention_legend
from src.services.openai_service import openai_service, OpenAIServiceError
from src.services.message_service import message_service
from src.services.queue_service import queue_service
from src.services.state_service import state_service

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
                logger.warning(f"""Failed to queue chat command from {
                    interaction.user} in #{
                    interaction.channel} - queue may be full""")
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
                logger.warning(f"""Failed to queue reset command from {
                    interaction.user} in #{
                    interaction.channel} - queue may be full""")
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
        if INCLUDE_USERNAMES:
            message = f"<@{interaction.user.id}> says: {message}"

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
        if attachment:
            logger.info(
                f"[/chat] Channel {channel_id}: including image URL {attachment.url}")
            content_payload = [
                {"type": "text", "text": message},
                {"type": "image_url", "image_url": {"url": attachment.url}},
            ]
        else:
            content_payload = message

        # Log user input and add to conversation
        logger.info(f"[/chat] Channel {channel_id} User: {message}")
        state_service.add_message_to_conversation(
            channel_id, {"role": "user", "content": json.dumps(content_payload)})

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
                    model=current_model, messages=current_conversation, system_prompt=system_prompt
                )

                # Log and store assistant reply
                logger.info(f"[/chat] Channel {channel_id} Assistant: {reply}")
                state_service.add_message_to_conversation(
                    channel_id, {"role": "assistant", "content": json.dumps(reply)}
                )

                # Prepare base message content
                if attachment:
                    base_content = message_service.format_attachment_message(
                        attachment, message)
                else:
                    base_content = message_service.format_prompt_message(
                        message)

                await message_service.send_interaction_followup(interaction, base_content, reply)

            except OpenAIServiceError as e:
                logger.error(f"OpenAI API error in chat command: {e}")
                # Prepare base message content for error response
                if attachment:
                    base_content = message_service.format_attachment_message(
                        attachment, message)
                else:
                    base_content = message_service.format_prompt_message(
                        message)

                error_message = f"{base_content}\n\nSorry, I encountered an error while processing your request: {str(e)}"

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
                logger.error(f"Unexpected error in chat command: {e}")
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
        conversation = []  # Empty conversation - system prompt will be added dynamically
        state_service.set_conversation(channel_id, conversation)
        state_service.set_model(channel_id, DEFAULT_MODEL)

        logger.info(f"[/reset] Channel {channel_id}: conversation reset")
        # Interaction already deferred in slash command handler
        await interaction.followup.send("Conversation reset.", ephemeral=True)
