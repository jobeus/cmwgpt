"""
Async command utilities for Discord bot commands.
"""

import logging
import traceback

import discord

logger = logging.getLogger(__name__)


async def safe_run(interaction: discord.Interaction,
                   handler: callable, *args, **kwargs) -> None:
    """
    Safely run an async command handler as a fire-and-forget task.

    Catches all exceptions and sends an error message back through
    the interaction's followup, so unhandled errors don't crash silently.

    Args:
        interaction: The Discord interaction to report errors to
        handler: The async handler function to call
        *args: Positional arguments for the handler
        **kwargs: Keyword arguments for the handler
    """
    try:
        await handler(*args, **kwargs)
    except Exception:
        error_dump = traceback.format_exc()
        logger.error(
            f"\U0001f6a8 Unhandled error in async command!\n"
            f"====== ERROR DUMP ======\n{error_dump}\n========================"
        )
        try:
            await interaction.followup.send(
                content="Sorry, I encountered an unexpected error. Please try again later.",
                ephemeral=True
            )
        except Exception as discord_error:
            logger.error(
                f"Failed to send error message to Discord: {discord_error}")
