"""
Death Commands - Configure the death service (restricted to DEATH_CHANNEL_ID).
"""

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands

from src.services.death_service import POLL_INTERVAL_SECONDS, MIN_AVG_MONTHLY_VIEWS, PAGEVIEW_MONTHS
from src.utils.async_utils import safe_run

logger = logging.getLogger(__name__)


class DeathCommands:
    """Handles /death Discord commands."""

    def __init__(self, bot: discord.ext.commands.Bot, *, state_service_instance, death_channel_id: str, death_service_instance=None):
        self.bot = bot
        self._state_service = state_service_instance
        self._death_channel_id = death_channel_id
        self._death_service = death_service_instance

    def setup_commands(self) -> None:
        """Set up all death-related commands."""
        self.bot.tree.add_command(self._create_death_group())
        self._create_forcedeath_command()
        self._create_limerick_command()

    def _create_forcedeath_command(self) -> None:
        """Create the /forcedeath command (server admins only)."""

        @self.bot.tree.command(
            name="forcedeath",
            description="Force a death announcement for a Wikipedia URL (admin only, for testing)")
        @app_commands.describe(url="Wikipedia URL or article title (e.g. 'Kevin Keegan')")
        async def forcedeath(interaction: discord.Interaction, url: str):
            if not getattr(interaction.user, "guild_permissions", None) or not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need administrator permissions to use this command.", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            asyncio.create_task(
                safe_run(interaction, self._handle_forcedeath, interaction, url)
            )

    def _create_limerick_command(self) -> None:
        """Create the /limerick command (open to everyone)."""

        @self.bot.tree.command(
            name="limerick",
            description="Write a funny limerick about a person")
        @app_commands.describe(person="Person's name or Wikipedia URL (e.g. 'Kevin Keegan')")
        async def limerick(interaction: discord.Interaction, person: str):
            await interaction.response.defer(thinking=True)
            asyncio.create_task(
                safe_run(interaction, self._handle_limerick, interaction, person)
            )

    async def _handle_limerick(self, interaction: discord.Interaction, person: str) -> None:
        """Handle the /limerick command."""
        if self._death_service is None:
            await interaction.followup.send("The limerick service is not available.")
            return

        ok, message = await self._death_service.write_limerick(person)
        logger.info(f"[/limerick] {interaction.user} requested '{person}' -> ok={ok}")
        await interaction.followup.send(message if ok else f"❌ {message}")

    async def _handle_forcedeath(self, interaction: discord.Interaction, url: str) -> None:
        """Handle the /forcedeath command."""
        if self._death_service is None:
            await interaction.followup.send("Death service is not available.", ephemeral=True)
            return

        ok, message = await self._death_service.force_announce(url)
        logger.info(f"[/forcedeath] {interaction.user} forced '{url}' -> ok={ok}")
        await interaction.followup.send(
            message if ok else f"❌ {message}", ephemeral=True
        )

    def _create_death_group(self) -> app_commands.Group:
        """Create the /death command group."""
        death_group = app_commands.Group(
            name="death",
            description="Manage global death announcement settings")

        @death_group.command(name="set",
                             description="Set death service configuration")
        @app_commands.describe(
            poll_interval="Polling interval in seconds (default 15)",
            min_views="Minimum average monthly views to announce (default 180000)",
            pageview_months="How many months to average pageview data (default 12)")
        async def death_set(
                interaction: discord.Interaction,
                poll_interval: Optional[app_commands.Range[int, 5, 3600]] = None,
                min_views: Optional[app_commands.Range[int, 1, 100000000]] = None,
                pageview_months: Optional[app_commands.Range[int, 1, 60]] = None):
            await interaction.response.defer(ephemeral=True, thinking=True)
            asyncio.create_task(
                safe_run(interaction, self._handle_death_set, interaction, poll_interval, min_views, pageview_months)
            )

        @death_group.command(
            name="reset",
            description="Reset the death service settings to defaults")
        async def death_reset(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            asyncio.create_task(
                safe_run(interaction, self._handle_death_reset, interaction)
            )

        @death_group.command(
            name="view",
            description="View the current death service settings")
        async def death_view(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            asyncio.create_task(
                safe_run(interaction, self._handle_death_view, interaction)
            )

        return death_group

    def _check_auth(self, interaction: discord.Interaction) -> bool:
        """Ensure the command is only run in the DEATH_CHANNEL_ID."""
        if not self._death_channel_id:
            return False

        if str(interaction.channel_id) != str(self._death_channel_id):
            return False

        return True

    async def _handle_death_set(
            self,
            interaction: discord.Interaction,
            poll_interval: Optional[int],
            min_views: Optional[int],
            pageview_months: Optional[int]) -> None:
        """Handle the /death set command."""
        if not self._check_auth(interaction):
            await interaction.followup.send("This command can only be used in the designated death announcements channel.", ephemeral=True)
            return

        self._state_service.mark_channel_active(interaction.channel.id)

        if poll_interval is None and min_views is None and pageview_months is None:
            await interaction.followup.send("You must provide at least one setting to change.", ephemeral=True)
            return

        current_settings = self._state_service.get_death_settings() or {}

        if poll_interval is not None:
            current_settings["interval"] = poll_interval
        if min_views is not None:
            current_settings["min_views"] = min_views
        if pageview_months is not None:
            current_settings["pageview_months"] = pageview_months

        self._state_service.set_death_settings(current_settings)

        logger.info(f"[/death set] Settings updated to {current_settings}.")
        await interaction.followup.send(
            f"Death settings updated globally:\n"
            f"- Poll interval: `{current_settings.get('interval', POLL_INTERVAL_SECONDS)}s`\n"
            f"- Min views: `{current_settings.get('min_views', MIN_AVG_MONTHLY_VIEWS):,}`\n"
            f"- Pageview months: `{current_settings.get('pageview_months', PAGEVIEW_MONTHS)}`",
            ephemeral=True
        )

    async def _handle_death_reset(self, interaction: discord.Interaction) -> None:
        """Handle the /death reset command."""
        if not self._check_auth(interaction):
            await interaction.followup.send("This command can only be used in the designated death announcements channel.", ephemeral=True)
            return

        self._state_service.mark_channel_active(interaction.channel.id)

        self._state_service.clear_death_settings()

        logger.info("[/death reset] Settings reset to defaults.")
        await interaction.followup.send("Death settings have been reset to defaults.", ephemeral=True)

    async def _handle_death_view(self, interaction: discord.Interaction) -> None:
        """Handle the /death view command."""
        if not self._check_auth(interaction):
            await interaction.followup.send("This command can only be used in the designated death announcements channel.", ephemeral=True)
            return

        settings = self._state_service.get_death_settings() or {}

        interval = settings.get("interval", POLL_INTERVAL_SECONDS)
        min_views = settings.get("min_views", MIN_AVG_MONTHLY_VIEWS)
        pageview_months = settings.get("pageview_months", PAGEVIEW_MONTHS)

        msg = (
            f"**Current Death Settings**\n"
            f"- Poll interval: `{interval}s` {'(custom)' if 'interval' in settings else '(default)'}\n"
            f"- Min views: `{min_views:,}` {'(custom)' if 'min_views' in settings else '(default)'}\n"
            f"- Pageview months: `{pageview_months}` {'(custom)' if 'pageview_months' in settings else '(default)'}"
        )

        logger.info("[/death view] Displayed settings.")
        await interaction.followup.send(msg, ephemeral=True)
