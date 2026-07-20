"""
Helpers for surviving Discord API failures gracefully.

When Discord's API has server trouble it returns Cloudflare-fronted HTML
error pages, which discord.py embeds verbatim in the exception message.
These helpers keep such failures from aborting work that could still
succeed, and keep the raw HTML/JavaScript out of the logs.
"""

import logging
from contextlib import asynccontextmanager

import discord

logger = logging.getLogger(__name__)

MAX_ERROR_MESSAGE_LENGTH = 200


def concise_error(error: BaseException, max_length: int = MAX_ERROR_MESSAGE_LENGTH) -> str:
    """Format an exception for logging without dumping HTML error pages."""
    text = " ".join(str(error).split())
    html_start = text.lower().find("<html")
    if html_start == -1:
        html_start = text.lower().find("<!doctype")
    if html_start != -1:
        text = f"{text[:html_start].strip()} [HTML error page omitted]".strip()
    if len(text) > max_length:
        text = text[:max_length] + "…"
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


def is_discord_server_error(error: BaseException) -> bool:
    """True if the error is a 5xx from Discord's API (their outage, not our bug)."""
    return isinstance(error, discord.HTTPException) and (error.status or 0) >= 500


async def safe_defer(interaction, *, ephemeral: bool = False, thinking: bool = True) -> bool:
    """Acknowledge a slash-command interaction, tolerating an expired token.

    Discord gives bots 3 seconds to acknowledge an interaction. If the event
    loop was stalled past that (or Discord lagged delivering it), defer()
    raises NotFound (10062 Unknown interaction) and there is no way to respond
    to that invocation at all — retrying is pointless, the token is dead.

    Returns True when the interaction is usable, False when it should be
    abandoned (the failure has already been logged concisely).
    """
    try:
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except discord.InteractionResponded:
        # Someone already acknowledged it; the interaction is still usable.
        return True
    except discord.NotFound:
        command_name = getattr(getattr(interaction, "command", None), "name", "?")
        logger.warning(
            f"Interaction for '/{command_name}' expired before it could be acknowledged "
            "(bot took >3s to respond — event loop stall or Discord lag). Skipping this "
            "invocation; the bot is still running and new commands will work."
        )
        return False
    except discord.HTTPException as e:
        command_name = getattr(getattr(interaction, "command", None), "name", "?")
        logger.warning(
            f"Failed to defer interaction for '/{command_name}': {concise_error(e)}. "
            "Skipping this invocation; the bot is still running."
        )
        return False


async def safe_followup_send(interaction, content, *, ephemeral: bool = False, **kwargs) -> bool:
    """Send an interaction followup, surviving a deleted invocation message.

    If the user deleted the "X used /command" message (or the token expired),
    followup.send raises NotFound. For public replies we fall back to a plain
    channel message so the result still gets delivered; ephemeral replies have
    no such fallback (posting them publicly would leak them), so they are just
    dropped with a small log line. Returns True if something was delivered.
    """
    try:
        await interaction.followup.send(content=content, ephemeral=ephemeral, **kwargs)
        return True
    except discord.NotFound as e:
        logger.warning(
            f"Interaction followup target is gone ({concise_error(e)}) — the invocation "
            "message was likely deleted or the token expired."
        )
        channel = getattr(interaction, "channel", None)
        if ephemeral or channel is None:
            return False
        try:
            await channel.send(content, **kwargs)
            logger.info("Delivered the reply as a plain channel message instead.")
            return True
        except Exception as send_error:
            logger.warning(f"Channel fallback also failed: {concise_error(send_error)}")
            return False


@asynccontextmanager
async def best_effort_typing(channel):
    """
    Show the typing indicator, but never let its failure abort the work.

    The typing endpoint is purely cosmetic; if Discord 500s on it (discord.py
    already retried several times internally by then), log one concise
    warning and run the wrapped block anyway.
    """
    typing_ctx = channel.typing()
    try:
        await typing_ctx.__aenter__()
    except Exception as e:
        logger.warning(
            f"Couldn't start typing indicator ({concise_error(e)}); continuing without it."
        )
        yield
        return

    try:
        yield
    finally:
        try:
            await typing_ctx.__aexit__(None, None, None)
        except Exception as e:
            logger.debug(f"Failed to stop typing indicator: {concise_error(e)}")
