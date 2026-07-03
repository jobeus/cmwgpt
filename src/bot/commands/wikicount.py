"""
WikiCount Command - Look up Wikipedia page views.
"""

import asyncio
import logging
from urllib.parse import urlparse, unquote

import discord
from discord import app_commands

from src.services.death_service import PAGEVIEW_MONTHS
from src.utils.async_utils import safe_run

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
            await interaction.response.defer(ephemeral=False, thinking=True)
            asyncio.create_task(
                safe_run(interaction, self._handle_wikicount, interaction, target)
            )

    async def _handle_wikicount(self, interaction: discord.Interaction, target: str) -> None:
        # Determine the article title
        article_title = ""
        
        if "wikipedia.org/wiki/" in target:
            parsed = urlparse(target)
            path_parts = parsed.path.split("/wiki/")
            if len(path_parts) > 1:
                article_title = unquote(path_parts[1])
        else:
            # If it's just a name like "Daveigh Chase" or "Las Vegas"
            # Wikipedia URLs typically replace spaces with underscores.
            article_title = target.strip().replace(" ", "_")
            
            # Uppercase the first letter as Wikipedia is case-sensitive for first letters
            if article_title:
                article_title = article_title[0].upper() + article_title[1:]

        if not article_title:
            await interaction.followup.send("Could not determine a valid Wikipedia article title from the input.")
            return

        try:
            # Reusing death service settings if any (default to PAGEVIEW_MONTHS)
            settings = self._state_service.get_death_settings() or {}
            months = settings.get("pageview_months", PAGEVIEW_MONTHS)
            
            # Obtain the shared session via the death service's public accessor
            session = await self._death_service.get_session()
            
            avg_views = await self._death_service.get_avg_monthly_views(article_title, session)
            
            # Fallback to title case if views are 0 or None (e.g. "daveigh chase" -> "Daveigh_Chase")
            if not avg_views and "wikipedia.org/wiki/" not in target:
                title_cased = target.strip().title().replace(" ", "_")
                if title_cased != article_title:
                    fallback_views = await self._death_service.get_avg_monthly_views(title_cased, session)
                    if fallback_views:
                        article_title = title_cased
                        avg_views = fallback_views

            if not avg_views:
                await interaction.followup.send(f"Could not fetch pageviews for `{article_title}`. Make sure the title is exact (including capitalization).")
                return

            await interaction.followup.send(f"**{article_title.replace('_', ' ')}**: {avg_views:,} avg monthly views over past {months} months")
            
        except Exception as e:
            logger.exception(f"Error in /wikicount for {target}")
            await interaction.followup.send("An error occurred while fetching pageviews.")
