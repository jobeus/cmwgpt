"""
Cost Commands - /cost: what the bot has spent today, this week and this month, by provider.
"""

import logging
from datetime import timedelta
from typing import Any, Callable, List, Optional, Tuple

import discord

from src.services.cost_service import CostStore, CostTotals, cost_day
from src.utils.async_utils import safe_run
from src.utils.discord_error_utils import safe_defer

logger = logging.getLogger(__name__)

# The report names people by <@id>; it shouldn't ping them.
NO_PINGS = discord.AllowedMentions.none()
# How many of this month's biggest spenders the report lists.
TOP_USERS = 5


def format_money(amount: float) -> str:
    return f"${amount:.3f}"


def format_providers(totals: CostTotals) -> str:
    """``claude-direct $1.200 · runpod $0.040``, biggest first, leaving out providers that cost nothing."""
    spent = [(name, amount) for name, amount in totals.providers.items() if round(amount, 3) > 0]
    spent.sort(key=lambda item: item[1], reverse=True)
    return " · ".join(f"{name} {format_money(amount)}" for name, amount in spent)


def format_cost_report(
    periods: List[Tuple[str, CostTotals]], top_users: List[Tuple[int, float]], top_label: str,
) -> str:
    """The /cost reply: each period's total and provider breakdown, then the top spenders."""
    lines = ["**What I've spent**"]
    for label, totals in periods:
        lines.append(f"**{label}:** {format_money(totals.total)}")
        breakdown = format_providers(totals)
        if breakdown:
            lines.append(f"-# {breakdown}")
    spenders = [(user_id, amount) for user_id, amount in top_users if round(amount, 3) > 0]
    if spenders:
        lines.append(f"**Top spenders {top_label}:** " + ", ".join(
            f"<@{user_id}> {format_money(amount)}" for user_id, amount in spenders))
    return "\n".join(lines)


class CostCommands:
    """Handles the /cost Discord command."""

    def __init__(
        self,
        bot: discord.ext.commands.Bot,
        *,
        cost_store: CostStore,
        today: Callable[[], Any] = cost_day,
    ):
        self.bot = bot
        self._cost_store = cost_store
        self._today = today

    def setup_commands(self) -> None:
        @self.bot.tree.command(name="cost", description="Show what the bot has spent, by provider")
        async def cost(interaction: discord.Interaction):
            if not await safe_defer(interaction, ephemeral=False, thinking=True):
                return
            await safe_run(interaction, self._handle_cost, interaction)

    async def _handle_cost(self, interaction: discord.Interaction) -> None:
        report = await self.build_report()
        await interaction.followup.send(report, allowed_mentions=NO_PINGS)

    async def build_report(self) -> str:
        today = self._today()
        month_start = today.replace(day=1)
        try:
            day = await self._cost_store.totals(today, today)
            week = await self._cost_store.totals(today - timedelta(days=6), today)
            month = await self._cost_store.totals(month_start, today)
        except Exception as e:
            logger.warning(f"Couldn't read the cost totals: {e}")
            return "I couldn't read my spending totals right now."
        month_label = f"{today:%B}"
        return format_cost_report(
            [("Today", day), ("Last 7 days", week), (month_label, month)],
            month.top_users(TOP_USERS),
            f"in {month_label}",
        )


def build_cost_commands(bot: discord.ext.commands.Bot, cost_store: Optional[CostStore]) -> Optional[CostCommands]:
    """The /cost commands, or ``None`` when costs aren't being saved (no store)."""
    if cost_store is None:
        return None
    return CostCommands(bot, cost_store=cost_store)
