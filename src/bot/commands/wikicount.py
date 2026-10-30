"""
WikiCount Command - Look up Wikipedia page views.
"""

import logging
import discord
from discord import app_commands

from src.services.death_service import PAGEVIEW_MONTHS
from src.utils.url_utils import wiki_title_candidates
from src.utils.async_utils import safe_run
from src.utils.discord_error_utils import safe_defer

logger = logging.getLogger(__name__)


class WikiCountCommands:
    """Handles the /wikicount Discord command."""

    def __init__(self, bot: discord.ext.commands.Bot, *, death_service_instance, state_service_instance):
        self.bot = bot
        self._death_service = death_service_instance
        self._state_service = state_service_instance

    def setup_commands(self) -> None:
        """Set up all wikicount-related commands."""
        
        @self.bot.tree.command(name="wikicount", description="Look up Wikipedia average monthly pageviews for an article")
        @app_commands.describe(target="Wikipedia URL or exact article title (e.g. 'Daveigh Chase' or 'Las Vegas')")
        async def wikicount(interaction: discord.Interaction, target: str):
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return
            await safe_run(interaction, self._handle_wikicount, interaction, target)

    async def _handle_wikicount(self, interaction: discord.Interaction, target: str) -> None:
        candidates = wiki_title_candidates(target)
        if not candidates:
            await interaction.followup.send("Could not determine a valid Wikipedia article title from the input.")
            return

        try:
            # Reusing death service settings if any (default to PAGEVIEW_MONTHS)
            settings = self._state_service.get_death_settings() or {}
            months = settings.get("pageview_months", PAGEVIEW_MONTHS)

            # Fall back to title case if views are 0 or None (e.g. "daveigh chase" -> "Daveigh_Chase")
            article_title, avg_views = candidates[0], None
            for title in candidates:
                avg_views = await self._death_service.get_avg_monthly_views(title)
                if avg_views:
                    article_title = title
                    break

            if not avg_views:
                await interaction.followup.send(f"Could not fetch pageviews for `{article_title}`. Make sure the title is exact (including capitalization).")
                return

            await interaction.followup.send(f"**{article_title.replace('_', ' ')}**: {avg_views:,} avg monthly views over past {months} months")
            
        except Exception:
            logger.exception(f"Error in /wikicount for {target}")
            await interaction.followup.send("An error occurred while fetching pageviews.")
