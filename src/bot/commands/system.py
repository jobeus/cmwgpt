"""
System Commands - Handles system/admin Discord commands
"""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.app_commands import Choice

from src.config import SYSTEM_PROMPT, DEFAULT_MODEL
from src.bot_state import conversations, models, channel_system_prompts
from src.utils.discord_helper import get_mention_legend


logger = logging.getLogger(__name__)


class SystemCommands:
    """Handles system/admin Discord commands."""

    def __init__(self, bot: discord.ext.commands.Bot):
        self.bot = bot

    def setup_commands(self) -> None:
        """Set up all system-related commands."""
        self.bot.tree.add_command(self._create_model_command())
        self.bot.tree.add_command(self._create_systemprompt_group())

    def _create_model_command(self) -> app_commands.Command:
        """Create the /model command."""

        @app_commands.command(name="model", description="View or set OpenAI model")
        @app_commands.describe(model="Model name to use")
        @app_commands.choices(
            model=[
                Choice(name="gpt-4.1-mini", value="gpt-4.1-mini"),
                Choice(name="gpt-4.1-nano", value="gpt-4.1-nano"),
                Choice(name="gpt-4o-mini", value="gpt-4o-mini"),
            ]
        )
        async def model_command(interaction: discord.Interaction, model: Optional[str] = None):
            await self._handle_model_command(interaction, model)

        return model_command

    def _create_systemprompt_group(self) -> app_commands.Group:
        """Create the /systemprompt command group."""
        systemprompt_group = app_commands.Group(
            name="systemprompt", description="Manage channel-specific system prompt"
        )

        @systemprompt_group.command(name="set", description="View or set the system prompt for this channel")
        @app_commands.describe(prompt_text="The new system prompt. Omit to view current prompt.")
        async def systemprompt_set(interaction: discord.Interaction, prompt_text: Optional[str] = None):
            await self._handle_systemprompt_set(interaction, prompt_text)

        @systemprompt_group.command(name="reset", description="Reset the system prompt for this channel to the default")
        async def systemprompt_reset(interaction: discord.Interaction):
            await self._handle_systemprompt_reset(interaction)

        return systemprompt_group

    async def _handle_model_command(self, interaction: discord.Interaction, model: Optional[str] = None) -> None:
        """
        Handle the /model command.

        Args:
            interaction: The Discord interaction
            model: Optional model name to set
        """
        channel_id = interaction.channel.id
        await interaction.response.defer(ephemeral=False, thinking=True)

        if model:
            models[channel_id] = model
            logger.info(f"[/model] Channel {channel_id}: model set to {model}")
            await interaction.followup.send(f"Model set to `{model}`.", ephemeral=True)
        else:
            current_model = models.get(channel_id, DEFAULT_MODEL)
            await interaction.followup.send(f"Model is `{current_model}`.", ephemeral=True)

    async def _handle_systemprompt_set(
        self, interaction: discord.Interaction, prompt_text: Optional[str] = None
    ) -> None:
        """
        Handle the /systemprompt set command.

        Args:
            interaction: The Discord interaction
            prompt_text: Optional new system prompt text
        """
        channel_id = interaction.channel.id
        await interaction.response.defer(ephemeral=True, thinking=True)
        legend_section = await get_mention_legend(interaction.channel)

        if prompt_text:
            # Set new system prompt
            channel_system_prompts[channel_id] = prompt_text

            # Update existing conversation if it exists
            if channel_id in conversations and conversations[channel_id]:
                if conversations[channel_id][0]["role"] == "system":
                    conversations[channel_id][0]["content"] = prompt_text
                else:
                    conversations[channel_id].insert(0, {"role": "system", "content": prompt_text})
            else:
                conversations.setdefault(channel_id, []).insert(0, {"role": "system", "content": prompt_text})

            logger.info(f"[/systemprompt set] Channel {channel_id}: system prompt updated.")
            await interaction.followup.send(
                "System prompt updated for this channel. The new prompt will be used for future messages and context.",
                ephemeral=True,
            )
        else:
            # Show current system prompt
            current_prompt = channel_system_prompts.get(channel_id, SYSTEM_PROMPT + "\n" + legend_section)
            logger.info(f"[/systemprompt set] Channel {channel_id}: displayed current system prompt.")
            await interaction.followup.send(
                f"Current system prompt for this channel:\n```\n{current_prompt}\n```", ephemeral=True
            )

    async def _handle_systemprompt_reset(self, interaction: discord.Interaction) -> None:
        """
        Handle the /systemprompt reset command.

        Args:
            interaction: The Discord interaction
        """
        channel_id = interaction.channel.id
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Remove custom prompt
        if channel_id in channel_system_prompts:
            del channel_system_prompts[channel_id]
            logger.info(f"[/systemprompt reset] Channel {channel_id}: custom prompt removed, reverting to default.")

        # Reset conversation system prompt
        if (
            channel_id in conversations
            and conversations[channel_id]
            and conversations[channel_id][0]["role"] == "system"
        ):
            conversations[channel_id][0]["content"] = SYSTEM_PROMPT
        else:
            # Ensure conversation list exists and prepend system prompt
            conversations.setdefault(channel_id, []).insert(0, {"role": "system", "content": SYSTEM_PROMPT})

            # Clean up any duplicate system prompts
            if len(conversations[channel_id]) > 1:
                new_convo = [conversations[channel_id][0]]
                for msg in conversations[channel_id][1:]:
                    if msg["role"] != "system":
                        new_convo.append(msg)
                conversations[channel_id] = new_convo

        logger.info(f"[/systemprompt reset] Channel {channel_id}: system prompt reset to default.")
        await interaction.followup.send("System prompt for this channel has been reset to the default.", ephemeral=True)
