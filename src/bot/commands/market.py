"""
Market Game Commands - /resetmarketgame, which wipes the paper-trading game.
"""

import logging

import discord
from discord import app_commands

from src.services.alpaca_service import AlpacaError
from src.utils.async_utils import safe_run
from src.utils.discord_error_utils import safe_defer

logger = logging.getLogger(__name__)


class MarketCommands:
    """Handles the paper-trading game's slash commands."""

    def __init__(self, bot: discord.ext.commands.Bot, *, paper_trading_service):
        self.bot = bot
        self._paper_trading = paper_trading_service

    def setup_commands(self) -> None:
        @self.bot.tree.command(
            name="resetmarketgame",
            description="Wipe the paper-trading game: every player, holding and order (admin only)")
        @app_commands.describe(confirm="True to really wipe the game for everyone")
        @app_commands.default_permissions(administrator=True)
        async def resetmarketgame(interaction: discord.Interaction, confirm: bool):
            if not getattr(interaction.user, "guild_permissions", None) or not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ You need administrator permissions to use this command.", ephemeral=True
                )
                return
            if not confirm:
                await interaction.response.send_message("Nothing was reset. Set `confirm` to True to wipe the game.", ephemeral=True)
                return
            if not await safe_defer(interaction, thinking=True):
                return
            await safe_run(interaction, self._handle_reset, interaction)

    async def _handle_reset(self, interaction: discord.Interaction) -> None:
        logger.warning("[paper] %s asked to reset the game", interaction.user.id)
        try:
            await interaction.followup.send(await self._paper_trading.reset())
        except AlpacaError as e:
            await interaction.followup.send(f"❌ The game wasn't reset. {e}")
