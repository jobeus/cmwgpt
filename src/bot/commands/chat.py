"""
Chat Commands - Handles chat-related Discord commands
"""

import json
import logging
from typing import Optional

import discord
from discord import app_commands

from src.config import SYSTEM_PROMPT, DEFAULT_MODEL, INCLUDE_USERNAMES
from src.bot_state import conversations, models, channel_system_prompts
from src.utils.discord_helper import get_mention_legend
from src.services.openai_service import openai_service
from src.services.message_service import message_service


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

        @app_commands.command(name="chat", description="Send a message to the chatbot")
        @app_commands.describe(message="Your message", attachment="Optional image to attach to the prompt")
        async def chat(interaction: discord.Interaction, message: str, attachment: Optional[discord.Attachment] = None):
            await self._handle_chat_command(interaction, message, attachment)

        return chat

    def _create_reset_command(self) -> app_commands.Command:
        """Create the /reset command."""

        @app_commands.command(name="reset", description="Reset the conversation history")
        async def reset(interaction: discord.Interaction):
            await self._handle_reset_command(interaction)

        return reset

    async def _handle_chat_command(
        self, interaction: discord.Interaction, message: str, attachment: Optional[discord.Attachment] = None
    ) -> None:
        """
        Handle the /chat command.

        Args:
            interaction: The Discord interaction
            message: The user's message
            attachment: Optional image attachment
        """
        channel_id = interaction.channel.id
        legend_section = await get_mention_legend(interaction.channel)

        # Add username if configured
        if INCLUDE_USERNAMES:
            message = f"{interaction.user.display_name} says: {message}"

        # Initialize conversation if missing
        if channel_id not in conversations:
            conversations[channel_id] = [
                {
                    "role": "system",
                    "content": channel_system_prompts.get(channel_id, SYSTEM_PROMPT + "\n" + legend_section),
                }
            ]
            models[channel_id] = DEFAULT_MODEL
            logger.info(f"[/chat] Channel {channel_id}: initialized conversation and model")

        # Construct content payload for OpenAI
        if attachment:
            logger.info(f"[/chat] Channel {channel_id}: including image URL {attachment.url}")
            content_payload = [
                {"type": "text", "text": message},
                {"type": "image_url", "image_url": {"url": attachment.url}},
            ]
        else:
            content_payload = message

        # Log user input and add to conversation
        logger.info(f"[/chat] Channel {channel_id} User: {message}")
        conversations[channel_id].append({"role": "user", "content": json.dumps(content_payload)})

        # Acknowledge the interaction
        await interaction.response.defer(ephemeral=False, thinking=True)

        # Get response from OpenAI
        async with interaction.channel.typing():
            reply = openai_service.get_chat_completion(
                model=models.get(channel_id, DEFAULT_MODEL), messages=conversations[channel_id]
            )

            # Log and store assistant reply
            logger.info(f"[/chat] Channel {channel_id} Assistant: {reply}")
            conversations[channel_id].append({"role": "assistant", "content": json.dumps(reply)})

            # Prepare base message content
            if attachment:
                base_content = message_service.format_attachment_message(attachment, message)
            else:
                base_content = message_service.format_prompt_message(message)

            await message_service.send_interaction_followup(interaction, base_content, reply)

    async def _handle_reset_command(self, interaction: discord.Interaction) -> None:
        """
        Handle the /reset command.

        Args:
            interaction: The Discord interaction
        """
        channel_id = interaction.channel.id
        legend_section = await get_mention_legend(interaction.channel)

        # Reset conversation and model
        conversations[channel_id] = [
            {"role": "system", "content": channel_system_prompts.get(channel_id, SYSTEM_PROMPT) + "\n" + legend_section}
        ]
        models[channel_id] = DEFAULT_MODEL

        logger.info(f"[/reset] Channel {channel_id}: conversation reset")
        await interaction.response.defer(ephemeral=False, thinking=True)
        await interaction.followup.send("Conversation reset.", ephemeral=True)
