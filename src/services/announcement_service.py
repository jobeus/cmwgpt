"""
Announcement service for the Discord bot.

This service handles sending announcements to channels where the bot has been used,
particularly for update notifications after restarts.
"""

import asyncio
import logging
import subprocess
from typing import Optional

import discord
from discord.ext import commands

from src.config import QUIET_UPDATES
from src.services.state_service import state_service

logger = logging.getLogger(__name__)


class AnnouncementService:
    """Service for sending announcements to active channels."""

    def __init__(self):
        """Initialize the announcement service."""
        self._bot: Optional[commands.Bot] = None

    def set_bot(self, bot: commands.Bot) -> None:
        """
        Set the Discord bot instance.

        Args:
            bot: The Discord bot instance
        """
        self._bot = bot

    def _get_current_git_sha(self) -> Optional[str]:
        """
        Get the current git commit SHA.

        Returns:
            Git commit SHA or None if unable to determine
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()[:7]  # Short SHA
            else:
                logger.error(f"Failed to get git SHA: {result.stderr}")
                return None
        except Exception as e:
            logger.error(f"Error getting git SHA: {e}")
            return None

    def _get_previous_git_sha(self) -> Optional[str]:
        """
        Get the previous git commit SHA (one commit before current).

        Returns:
            Previous git commit SHA or None if unable to determine
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD~1"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()[:7]  # Short SHA
            else:
                logger.debug(f"Could not get previous SHA: {result.stderr}")
                return None
        except Exception as e:
            logger.debug(f"Error getting previous git SHA: {e}")
            return None

    def _get_commit_summary(self, sha: str) -> Optional[str]:
        """
        Get a brief summary of commits since the given SHA.

        Args:
            sha: Git commit SHA to compare from

        Returns:
            Brief commit summary or None if unable to determine
        """
        try:
            result = subprocess.run(
                ["git", "log", f"{sha}..HEAD", "--oneline", "--max-count=3"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                if len(lines) == 1:
                    return f"• {lines[0]}"
                elif len(lines) <= 3:
                    return "\n".join(f"• {line}" for line in lines)
                else:
                    return "\n".join(f"• {line}" for line in lines[:2]) + f"\n• ... and {len(lines) - 2} more commits"
            return None
        except Exception as e:
            logger.debug(f"Error getting commit summary: {e}")
            return None

    async def announce_update(self, was_manual: bool = False) -> None:
        """
        Announce bot update to all active channels.

        Args:
            was_manual: Whether this was a manual restart
        """
        if not self._bot:
            logger.error("Bot instance not set, cannot send announcements")
            return

        # Check if quiet updates are enabled
        if QUIET_UPDATES:
            logger.info("QUIET_UPDATES is enabled, skipping update announcement")
            return

        # Get current git SHA
        current_sha = self._get_current_git_sha()
        if not current_sha:
            logger.warning("Could not determine git SHA, skipping update announcement")
            return

        # Get active channels
        active_channels = state_service.get_active_channels()
        if not active_channels:
            logger.info("No active channels to announce to")
            return

        # Get previous SHA and commit summary for context
        previous_sha = self._get_previous_git_sha()
        commit_summary = None
        if previous_sha:
            commit_summary = self._get_commit_summary(previous_sha)

        # Prepare announcement message
        restart_type = "manual restart" if was_manual else "auto-update"
        base_message = f"🤖 **Bot Updated** ({restart_type})\n📝 Now running commit `{current_sha}`"

        if commit_summary:
            message = f"{base_message}\n\n**Recent changes:**\n{commit_summary}"
        else:
            message = base_message

        # Add footer
        message += "\n\n*Ready to assist! Use `/chat` or mention me to continue.*"

        logger.info(f"Announcing update to {len(active_channels)} channels: {current_sha}")

        # Send announcements to all active channels
        successful_announcements = 0
        failed_announcements = 0

        for channel_id in active_channels:
            try:
                channel = self._bot.get_channel(channel_id)
                if channel and isinstance(channel, discord.TextChannel):
                    await channel.send(message)
                    successful_announcements += 1
                    logger.debug(f"Sent update announcement to #{channel.name} ({channel_id})")

                    # Small delay to avoid rate limiting
                    await asyncio.sleep(0.5)
                else:
                    logger.warning(f"Could not find or access channel {channel_id}")
                    failed_announcements += 1
            except discord.Forbidden:
                logger.warning(f"No permission to send message to channel {channel_id}")
                failed_announcements += 1
            except discord.HTTPException as e:
                logger.error(f"HTTP error sending announcement to channel {channel_id}: {e}")
                failed_announcements += 1
            except Exception as e:
                logger.error(f"Unexpected error sending announcement to channel {channel_id}: {e}")
                failed_announcements += 1

        logger.info(f"Update announcements sent: {successful_announcements} successful, {failed_announcements} failed")


# Global announcement service instance
announcement_service = AnnouncementService()
