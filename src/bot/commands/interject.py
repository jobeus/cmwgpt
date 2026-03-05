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
from src.services.interject_service import (
    INTERJECT_CHANCE_PERCENT, COOLDOWN_MINUTES, MIN_MESSAGES,
    MIN_UNIQUE_AUTHORS, ACTIVITY_WINDOW_MINUTES, CONTEXT_LINES,
    MAX_INTERJECTIONS_PER_DAY, EXCLUDE_EMBEDS
)
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
            min_messages="Minimum qualifying messages in the activity window to trigger an interjection",
            min_authors="Minimum number of distinct non-bot authors in the qualifying streak",
            window_mins="Only messages within this many minutes from *now* count",
            context_lines="Number of recent messages to include as AI context when generating a reply",
            daily_max="Maximum interjections per calendar day (UTC) for this channel",
            exclude_embeds="True to break streak if messages contain embeds/attachments"
        )
        async def interject_set(
                interaction: discord.Interaction,
                chance: Optional[app_commands.Range[int, 0, 100]] = None,
                cooldown: Optional[app_commands.Range[int, 0, 1000]] = None,
                min_messages: Optional[app_commands.Range[int, 1, 100]] = None,
                min_authors: Optional[app_commands.Range[int, 1, 50]] = None,
                window_mins: Optional[app_commands.Range[int, 1, 1440]] = None,
                context_lines: Optional[app_commands.Range[int, 1, 100]] = None,
                daily_max: Optional[app_commands.Range[int, 1, 1000]] = None,
                exclude_embeds: Optional[bool] = None):
            await interaction.response.defer(ephemeral=True, thinking=True)
            
            # Since this is a lightweight state mutation and doesn't call OpenAI or long processes,
            # we can run it safely without putting it through the heavy queue
            asyncio.create_task(
                safe_run(interaction, self._handle_interject_set, interaction, 
                         chance, cooldown, min_messages, min_authors, window_mins, 
                         context_lines, daily_max, exclude_embeds)
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

        @interject_group.command(
            name="count",
            description="View the number of interjections used and remaining for this channel today"
        )
        async def interject_count(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            asyncio.create_task(
                safe_run(interaction, self._handle_interject_count, interaction)
            )

        return interject_group

    async def _handle_interject_set(
            self,
            interaction: discord.Interaction,
            chance: Optional[int],
            cooldown: Optional[int],
            min_messages: Optional[int],
            min_authors: Optional[int],
            window_mins: Optional[int],
            context_lines: Optional[int],
            daily_max: Optional[int],
            exclude_embeds: Optional[bool]) -> None:
        """Handle the /interject set command."""
        channel_id = interaction.channel.id
        state_service.mark_channel_active(channel_id)

        if all(x is None for x in [chance, cooldown, min_messages, min_authors, window_mins, context_lines, daily_max, exclude_embeds]):
            await interaction.followup.send("You must provide at least one setting to change.", ephemeral=True)
            return

        current_settings = state_service.get_interject_settings(channel_id) or {}
        
        if chance is not None:
            current_settings["chance"] = chance
        if cooldown is not None:
            current_settings["cooldown"] = cooldown
        if min_messages is not None:
            current_settings["min_messages"] = min_messages
        if min_authors is not None:
            current_settings["min_authors"] = min_authors
        if window_mins is not None:
            current_settings["window_mins"] = window_mins
        if context_lines is not None:
            current_settings["context_lines"] = context_lines
        if daily_max is not None:
            current_settings["daily_max"] = daily_max
        if exclude_embeds is not None:
            current_settings["exclude_embeds"] = exclude_embeds

        state_service.set_interject_settings(channel_id, current_settings)

        logger.info(f"[/interject set] Channel {channel_id}: settings updated to {current_settings}.")
        await interaction.followup.send(
            f"Interjection settings updated for this channel:\n"
            f"- Chance: `{current_settings.get('chance', INTERJECT_CHANCE_PERCENT)}%`\n"
            f"- Cooldown: `{current_settings.get('cooldown', COOLDOWN_MINUTES)} min`\n"
            f"- Min messages: `{current_settings.get('min_messages', MIN_MESSAGES)}`\n"
            f"- Min authors: `{current_settings.get('min_authors', MIN_UNIQUE_AUTHORS)}`\n"
            f"- Window mins: `{current_settings.get('window_mins', ACTIVITY_WINDOW_MINUTES)}`\n"
            f"- Context lines: `{current_settings.get('context_lines', CONTEXT_LINES)}`\n"
            f"- Daily max: `{current_settings.get('daily_max', MAX_INTERJECTIONS_PER_DAY)}`\n"
            f"- Exclude embeds: `{current_settings.get('exclude_embeds', EXCLUDE_EMBEDS)}`",
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
        min_authors = settings.get("min_authors", MIN_UNIQUE_AUTHORS)
        window_mins = settings.get("window_mins", ACTIVITY_WINDOW_MINUTES)
        context_lines = settings.get("context_lines", CONTEXT_LINES)
        daily_max = settings.get("daily_max", MAX_INTERJECTIONS_PER_DAY)
        exclude_embeds = settings.get("exclude_embeds", EXCLUDE_EMBEDS)

        msg = (
            f"**Current Interjection Settings for #{interaction.channel.name}**\n"
            f"- Chance: `{chance}%` {'(custom)' if 'chance' in settings else '(default)'}\n"
            f"- Cooldown: `{cooldown} min` {'(custom)' if 'cooldown' in settings else '(default)'}\n"
            f"- Min messages: `{min_messages}` {'(custom)' if 'min_messages' in settings else '(default)'}\n"
            f"- Min authors: `{min_authors}` {'(custom)' if 'min_authors' in settings else '(default)'}\n"
            f"- Window mins: `{window_mins}` {'(custom)' if 'window_mins' in settings else '(default)'}\n"
            f"- Context lines: `{context_lines}` {'(custom)' if 'context_lines' in settings else '(default)'}\n"
            f"- Daily max: `{daily_max}` {'(custom)' if 'daily_max' in settings else '(default)'}\n"
            f"- Exclude embeds: `{exclude_embeds}` {'(custom)' if 'exclude_embeds' in settings else '(default)'}"
        )

        logger.info(f"[/interject view] Channel {channel_id}: displayed settings.")
        await interaction.followup.send(msg, ephemeral=True)

    async def _handle_interject_count(self, interaction: discord.Interaction) -> None:
        """Handle the /interject count command."""
        channel_id = interaction.channel.id
        from src.services.interject_service import interject_service
        current_count, daily_max = interject_service.get_daily_status(channel_id)
        
        remaining = max(0, daily_max - current_count)
        msg = (
            f"**Interjection Count for #{interaction.channel.name}**\n"
            f"- Used today: `{current_count}`\n"
            f"- Daily Max: `{daily_max}`\n"
            f"- Remaining: `{remaining}`"
        )
        logger.info(f"[/interject count] Channel {channel_id}: displayed count ({current_count}/{daily_max}).")
        await interaction.followup.send(msg, ephemeral=True)
