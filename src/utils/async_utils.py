"""
Async command utilities for Discord bot commands.
"""

import logging
from typing import Any, Awaitable, Callable

import discord

from src.services.cost_service import cost_scope
from src.utils.discord_error_utils import concise_error

logger = logging.getLogger(__name__)


async def safe_run(interaction: discord.Interaction,
                   handler: Callable[..., Awaitable[Any]], *args, **kwargs) -> None:
    """
    Run an async command handler, reporting any error to the user.

    Catches all exceptions and sends an error message back through
    the interaction's followup, so unhandled errors don't crash silently.

    Args:
        interaction: The Discord interaction to report errors to
        handler: The async handler function to call
        *args: Positional arguments for the handler
        **kwargs: Keyword arguments for the handler
    """
    try:
        # Charges what the command spends to whoever ran it, where they ran it.
        with cost_scope(
            user_id=getattr(interaction.user, "id", None),
            channel_id=getattr(interaction.channel, "id", None),
        ):
            await handler(*args, **kwargs)
    except discord.NotFound as e:
        # The interaction's response message was deleted (or the token
        # expired) mid-command. Nothing to respond to — one small log line,
        # no traceback dump.
        logger.warning(
            "Interaction vanished mid-command (%s) — the invocation message was likely deleted. Skipping the reply; the bot is still running.", concise_error(e)
        )
    except Exception:
        command = getattr(getattr(interaction, "command", None), "qualified_name", None) or "?"
        logger.exception("Unhandled error in /%s for %s", command, getattr(interaction, "user", None))
        try:
            await interaction.followup.send(
                content="Sorry, I encountered an unexpected error. Please try again later.",
                ephemeral=True
            )
        except Exception as discord_error:
            logger.error(
                "Failed to send error message to Discord: %s", concise_error(discord_error))
