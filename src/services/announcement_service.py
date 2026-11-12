"""
Announcement service for the Discord bot.

This service handles sending announcements to channels where the bot has been used,
particularly for update notifications after restarts.
"""

import asyncio
import logging
import subprocess
from typing import Callable, Optional

import discord
from discord.ext import commands

from src.config import get_config
from src.utils.git_utils import get_current_commit_hash

logger = logging.getLogger(__name__)


class AnnouncementService:
    """Service for sending announcements to active channels."""

    def __init__(
        self,
        *,
        state_service,
        paste_service=None,
        quiet_updates: Optional[bool] = None,
        current_git_sha_loader: Optional[Callable[[], Optional[str]]] = None,
        changelog_loader: Optional[Callable[[str, str], Optional[str]]] = None,
    ):
        """Initialize the announcement service."""
        self._bot: Optional[commands.Bot] = None
        self._state_service = state_service
        self._paste_service = paste_service
        self._quiet_updates = get_config().quiet_updates if quiet_updates is None else quiet_updates
        self._current_git_sha_loader = current_git_sha_loader or get_current_commit_hash
        self._changelog_loader = changelog_loader or self._get_complete_changelog

    def set_bot(self, bot: commands.Bot) -> None:
        """
        Set the Discord bot instance.

        Args:
            bot: The Discord bot instance
        """
        self._bot = bot

    def _get_complete_changelog(
            self,
            from_sha: str,
            to_sha: str) -> Optional[str]:
        """
        Get complete changelog between two git SHAs containing #announce.

        Args:
            from_sha: Starting git commit SHA
            to_sha: Ending git commit SHA

        Returns:
            Complete changelog or None if unable to determine or no matching commits
        """
        try:
            result = subprocess.run(["git",
                                     "log",
                                     f"{from_sha}..{to_sha}",
                                     "--grep=#announce",
                                     "-i",
                                     "--oneline"],
                                    capture_output=True,
                                    text=True,
                                    timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split("\n")
                return "\n".join(f"• {line}" for line in lines)
            return None
        except Exception as e:
            logger.debug("Error getting changelog: %s", e)
            return None

    @staticmethod
    def _build_truncated_message(base_message: str, changelog: str) -> str:
        lines = changelog.split("\n")
        truncated_changelog = "\n".join(lines[:5])
        if len(lines) > 5:
            truncated_changelog += f"\n• ... and {len(lines) - 5} more commits"
        return f"{base_message} Recent changes:\n{truncated_changelog}"

    async def announce_update(self, was_manual: bool = False) -> None:
        """
        Announce bot update to all active channels.
        Only announces if the git SHA has changed since the last shutdown and a commit in
        between is marked ``#announce``.

        Args:
            was_manual: Whether this was a manual restart
        """
        if not self._bot:
            logger.error("Bot instance not set, cannot send announcements")
            return

        if self._quiet_updates:
            logger.info(
                "QUIET_UPDATES is enabled, skipping update announcement")
            return

        # git is synchronous: run it in a thread so the Discord heartbeat keeps going.
        current_sha = await asyncio.to_thread(self._current_git_sha_loader)
        if not current_sha:
            logger.warning(
                "Could not determine git SHA, skipping update announcement")
            return

        previous_sha = self._state_service.get_last_git_sha()

        if previous_sha and previous_sha == current_sha:
            logger.info(
                "Git SHA unchanged (%s), skipping duplicate update announcement", current_sha[:7])
            return

        active_channels = self._state_service.get_active_channels()
        if not active_channels:
            logger.info("No active channels to announce to")
            return

        changelog = None
        if previous_sha:
            changelog = await asyncio.to_thread(
                self._changelog_loader, previous_sha, current_sha)

        if not changelog:
            logger.info(
                "No commits with #announce found, skipping announcement")
            self._state_service.set_last_git_sha(current_sha)
            return

        restart_type = "manual restart" if was_manual else "auto-update"
        current_sha_short = current_sha[:7]
        base_message = f"🤖 **Bot Updated** ({restart_type})\n📝 Now running commit `{current_sha_short}`"

        full_message = f"{base_message} Changes:\n{changelog}"

        if len(full_message) <= 2000:
            message = full_message
        else:
            if self._paste_service:
                try:
                    paste_url = await self._paste_service.upload_markdown(changelog)
                    message = f"{base_message} Changes:\n[View complete changelog]({paste_url})"
                except Exception as e:
                    logger.error(
                        "Failed to upload changelog to paste service: %s", e, exc_info=True)
                    message = self._build_truncated_message(base_message, changelog)
            else:
                message = self._build_truncated_message(base_message, changelog)

        logger.info(
            "Announcing update to %s channels: %s", len(active_channels), current_sha_short)

        successful_announcements = 0
        failed_announcements = 0

        for channel_id in active_channels:
            try:
                channel = self._bot.get_channel(channel_id)
                if channel and hasattr(channel, "send"):
                    await channel.send(message)
                    successful_announcements += 1
                    logger.debug(
                        "Sent update announcement to #%s (%s)", channel.name, channel_id
                    )

                    await asyncio.sleep(0.5)
                else:
                    logger.warning(
                        "Could not find or access channel %s", channel_id)
                    failed_announcements += 1
            except discord.Forbidden:
                logger.warning(
                    "No permission to send message to channel %s", channel_id)
                failed_announcements += 1
            except discord.HTTPException as e:
                logger.error(
                    "HTTP error sending announcement to channel %s: %s", channel_id, e)
                failed_announcements += 1
            except Exception as e:
                logger.error(
                    "Unexpected error sending announcement to channel %s: %s", channel_id, e, exc_info=True)
                failed_announcements += 1

        logger.info(
            "Update announcements sent: %s successful, %s failed", successful_announcements, failed_announcements)

        # Update the git SHA in state now that we've announced the update
        self._state_service.set_last_git_sha(current_sha)
        logger.debug("Updated last git SHA to: %s", current_sha[:7])
