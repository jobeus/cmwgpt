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
from src.utils.pasters import upload_to_pasters

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
            Full git commit SHA or None if unable to determine
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()  # Full SHA
            else:
                logger.error(f"Failed to get git SHA: {result.stderr}")
                return None
        except Exception as e:
            logger.error(f"Error getting git SHA: {e}")
            return None

    def _get_complete_changelog(self, from_sha: str, to_sha: str) -> Optional[str]:
        """
        Get complete changelog between two git SHAs.

        Args:
            from_sha: Starting git commit SHA
            to_sha: Ending git commit SHA

        Returns:
            Complete changelog or None if unable to determine
        """
        try:
            result = subprocess.run(
                ["git", "log", f"{from_sha}..{to_sha}", "--oneline"],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                return "\n".join(f"• {line}" for line in lines)
            return None
        except Exception as e:
            logger.debug(f"Error getting changelog: {e}")
            return None

    async def announce_update(self, was_manual: bool = False) -> None:
        """
        Announce bot update to all active channels.
        Only announces if the git SHA has actually changed since last shutdown.

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

        # Get previous SHA from state
        previous_sha = state_service.get_last_git_sha()

        # If we have a previous SHA and it's the same as current, skip announcement
        if previous_sha and previous_sha == current_sha:
            logger.info(f"Git SHA unchanged ({current_sha[:7]}), skipping duplicate update announcement")
            return

        # Get active channels
        active_channels = state_service.get_active_channels()
        if not active_channels:
            logger.info("No active channels to announce to")
            return

        # Get complete changelog if we have a previous SHA
        changelog = None
        if previous_sha:
            changelog = self._get_complete_changelog(previous_sha, current_sha)

        # Prepare announcement message
        restart_type = "manual restart" if was_manual else "auto-update"
        current_sha_short = current_sha[:7]
        base_message = f"🤖 **Bot Updated** ({restart_type})\n📝 Now running commit `{current_sha_short}`"

        if changelog:
            # Check if message would be too long for Discord
            full_message = f"{base_message}\n\n**Changes:**\n{changelog}\n\n*Ready to assist! Use `/chat` or mention me to continue.*"

            if len(full_message) <= 2000:
                message = full_message
            else:
                # Message too long, upload to paste service
                try:
                    paste_url = upload_to_pasters(changelog)
                    message = f"{base_message}\n\n**Changes:** [View complete changelog]({paste_url})\n\n*Ready to assist! Use `/chat` or mention me to continue.*"
                except Exception as e:
                    logger.error(f"Failed to upload changelog to paste service: {e}")
                    # Fallback to truncated message
                    lines = changelog.split('\n')
                    truncated_changelog = '\n'.join(lines[:5])
                    if len(lines) > 5:
                        truncated_changelog += f"\n• ... and {len(lines) - 5} more commits"
                    message = f"{base_message}\n\n**Recent changes:**\n{truncated_changelog}\n\n*Ready to assist! Use `/chat` or mention me to continue.*"
        else:
            message = f"{base_message}\n\n*Ready to assist! Use `/chat` or mention me to continue.*"

        logger.info(f"Announcing update to {len(active_channels)} channels: {current_sha_short}")

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
