"""
Interject Commands - Configure the interject service per-channel.
"""

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands

from src.services.queue_service import queue_service
from src.services.state_service import state_service
from src.services.interject_service import INTERJECT_CHANCE_PERCENT, COOLDOWN_MINUTES, MIN_MESSAGES
from src.utils.async_utils import safe_run

logger = logging.getLogger(__name__)


class InterjectCommands:
    """Handles /interject Discord commands."""

    def __init__(self, bot: discord.ext.commands.Bot):
        self.bot = bot

    def setup_commands(self) -> None:
        """Set up all interject-related commands."""
        self.bot.tree.add_command(self._create_interject_group())

    def _create_interject_group(self) -> app_commands.Group:
        """Create the /interject command group."""
        interject_group = app_commands.Group(
            name="interject",
            description="Manage interjection settings for this channel")

        @interject_group.command(name="set",
                                description="Set interjection configuration for this channel")
        @app_commands.describe(
            chance="Percentage chance (0-100) to interject when conditions are met",
            cooldown="Per-channel cooldown in minutes after an interjection or failed roll",
            min_messages="Minimum qualifying messages in the activity window to trigger an interjection")
        async def interject_set(
                interaction: discord.Interaction,
                chance: Optional[app_commands.Range[int, 0, 100]] = None,
                cooldown: Optional[app_commands.Range[int, 0, 1000]] = None,
                min_messages: Optional[app_commands.Range[int, 1, 100]] = None):
            await interaction.response.defer(ephemeral=True, thinking=True)
            
            # Since this is a lightweight state mutation and doesn't call OpenAI or long processes,
            # we can run it safely without putting it through the heavy queue
            asyncio.create_task(
                safe_run(interaction, self._handle_interject_set, interaction, chance, cooldown, min_messages)
            )

        @interject_group.command(
            name="reset",
            description="Reset the interjection settings for this channel to defaults")
        async def interject_reset(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            asyncio.create_task(
                safe_run(interaction, self._handle_interject_reset, interaction)
            )

        @interject_group.command(
            name="view",
            description="View the current interjection settings for this channel")
        async def interject_view(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            asyncio.create_task(
                safe_run(interaction, self._handle_interject_view, interaction)
            )

        return interject_group

    async def _handle_interject_set(
            self,
            interaction: discord.Interaction,
            chance: Optional[int],
            cooldown: Optional[int],
            min_messages: Optional[int]) -> None:
        """Handle the /interject set command."""
        channel_id = interaction.channel.id
        state_service.mark_channel_active(channel_id)

        if chance is None and cooldown is None and min_messages is None:
            await interaction.followup.send("You must provide at least one setting to change.", ephemeral=True)
            return

        current_settings = state_service.get_interject_settings(channel_id) or {}
        
        if chance is not None:
            current_settings["chance"] = chance
        if cooldown is not None:
            current_settings["cooldown"] = cooldown
        if min_messages is not None:
            current_settings["min_messages"] = min_messages

        state_service.set_interject_settings(channel_id, current_settings)

        logger.info(f"[/interject set] Channel {channel_id}: settings updated to {current_settings}.")
        await interaction.followup.send(
            f"Interjection settings updated for this channel:\n"
            f"- Chance: `{current_settings.get('chance', INTERJECT_CHANCE_PERCENT)}%`\n"
            f"- Cooldown: `{current_settings.get('cooldown', COOLDOWN_MINUTES)} min`\n"
            f"- Min messages: `{current_settings.get('min_messages', MIN_MESSAGES)}`",
            ephemeral=True
        )

    async def _handle_interject_reset(self, interaction: discord.Interaction) -> None:
        """Handle the /interject reset command."""
        channel_id = interaction.channel.id
        state_service.mark_channel_active(channel_id)

        state_service.clear_interject_settings(channel_id)
        
        logger.info(f"[/interject reset] Channel {channel_id}: settings reset to defaults.")
        await interaction.followup.send("Interjection settings for this channel have been reset to defaults.", ephemeral=True)

    async def _handle_interject_view(self, interaction: discord.Interaction) -> None:
        """Handle the /interject view command."""
        channel_id = interaction.channel.id
        settings = state_service.get_interject_settings(channel_id) or {}
        
        chance = settings.get("chance", INTERJECT_CHANCE_PERCENT)
        cooldown = settings.get("cooldown", COOLDOWN_MINUTES)
        min_messages = settings.get("min_messages", MIN_MESSAGES)

        msg = (
            f"**Current Interjection Settings for #{interaction.channel.name}**\n"
            f"- Chance: `{chance}%` {'(custom)' if 'chance' in settings else '(default)'}\n"
            f"- Cooldown: `{cooldown} min` {'(custom)' if 'cooldown' in settings else '(default)'}\n"
            f"- Min messages: `{min_messages}` {'(custom)' if 'min_messages' in settings else '(default)'}"
        )

        logger.info(f"[/interject view] Channel {channel_id}: displayed settings.")
        await interaction.followup.send(msg, ephemeral=True)
