"""
Discord Bot Client - Main bot setup and event handling
"""

import asyncio
import logging
import subprocess
from typing import Optional

import discord
from discord.ext import commands

from src.config import DISCORD_BOT_TOKEN, DEFAULT_MODEL, REPLY_TO_MENTIONS
from src.bot.handlers.mention import mention_handler
from src.bot.commands.chat import ChatCommands
from src.bot.commands.image import ImageCommands
from src.bot.commands.system import SystemCommands
from src.services.queue_service import queue_service
from src.services.state_service import state_service
from src.services.auto_update_service import auto_update_service
from src.services.restart_handler import restart_handler
from src.services.announcement_service import announcement_service

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger("discord_bot")


class DiscordBotClient:
    """Main Discord bot client with event handling and command setup."""

    def __init__(self):
        # Configure Discord bot with intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        self.bot = commands.Bot(command_prefix="/", intents=intents)

        # Load any saved state from previous restart
        self._load_saved_state()

        # Set current git SHA for comparison on next restart
        self._set_current_git_sha()

        # Set up auto-update service
        self._setup_auto_update()

        # Set up announcement service
        announcement_service.set_bot(self.bot)

        # Set up event handlers
        self._setup_events()

        # Set up commands
        self._setup_commands()

    def _load_saved_state(self) -> None:
        """Load any saved state from previous restart."""
        try:
            if state_service.load_state_from_temp_files():
                print("✅ Restored previous state")
            else:
                print("🆕 Starting with fresh state")
        except Exception as e:
            logger.error(f"Error loading saved state: {e}")
            print("⚠️  Failed to restore state, starting fresh")
            # Continue with fresh state if loading fails

    def _set_current_git_sha(self) -> None:
        """Set the current git SHA in state for future comparison."""
        try:
            current_sha = self._get_current_git_sha()
            if current_sha:
                # Only update if we don't already have a SHA (from loaded state)
                # This preserves the previous SHA for comparison by the announcement service
                if not state_service.get_last_git_sha():
                    state_service.set_last_git_sha(current_sha)
                    logger.info(f"Set initial git SHA: {current_sha[:7]}")
                else:
                    # Don't update - let the announcement service handle the comparison and update
                    logger.debug(f"Current git SHA: {current_sha[:7]}, preserving previous SHA for comparison")
            else:
                logger.warning("Could not determine current git SHA")
        except Exception as e:
            logger.error(f"Error setting current git SHA: {e}")

    def _get_current_git_sha(self) -> Optional[str]:
        """
        Get the current git commit SHA.

        Returns:
            Git commit SHA or None if unable to determine
        """
        try:
            result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                logger.error(f"Failed to get git SHA: {result.stderr}")
                return None
        except Exception as e:
            logger.error(f"Error getting git SHA: {e}")
            return None

    def _setup_auto_update(self) -> None:
        """Set up the auto-update service."""
        try:
            # Set the restart callback
            auto_update_service.set_restart_callback(restart_handler.perform_restart)
            logger.info("Auto-update service configured")
        except Exception as e:
            logger.error(f"Error setting up auto-update service: {e}")

    async def _send_update_announcement_if_needed(self) -> None:
        """Send update announcement if this was a restart."""
        try:
            # Check if we have active channels and this looks like a restart
            active_channels = state_service.get_active_channels()
            if not active_channels:
                logger.info("No active channels found, skipping update announcement")
                return

            # Small delay to ensure bot is fully ready
            await asyncio.sleep(2)

            # Check for restart info to determine if it was manual
            was_manual = self._check_for_restart_info()

            # Send announcement
            await announcement_service.announce_update(was_manual=was_manual)
            logger.info(f"Sent update announcements to active channels (manual: {was_manual})")

        except Exception as e:
            logger.error(f"Error sending update announcement: {e}")

    def _check_for_restart_info(self) -> bool:
        """
        Check for restart info in the loaded state to determine if this was a manual restart.

        Returns:
            True if this was a manual restart, False otherwise
        """
        try:
            import glob
            import json
            import os

            # Look for state files that might contain restart info
            pattern = "/tmp/cmwgpt_state_backup_*.json"
            state_files = glob.glob(pattern)

            # Filter out any old restart info files (shouldn't exist with new
            # approach)
            state_files = [f for f in state_files if not f.endswith("_restart_info.json")]

            was_manual = False
            for state_file in state_files:
                try:
                    with open(state_file, "r") as f:
                        state_data = json.load(f)

                    # Check if restart info is embedded in the state file
                    restart_info = state_data.get("restart_info", {})
                    if restart_info:
                        was_manual = restart_info.get("manual_restart", False)
                        logger.info(f"Found restart info in state: manual={was_manual}")
                        break

                except Exception as e:
                    logger.warning(f"Error reading state file {state_file}: {e}")

            # Also clean up any old-style restart info files if they exist
            old_restart_info_pattern = "/tmp/cmwgpt_state_backup_*_restart_info.json"
            old_restart_info_files = glob.glob(old_restart_info_pattern)
            for info_file in old_restart_info_files:
                try:
                    os.remove(info_file)
                    logger.debug(f"Cleaned up old restart info file: {info_file}")
                except Exception as e:
                    logger.warning(f"Error cleaning up old restart info file {info_file}: {e}")

            return was_manual

        except Exception as e:
            logger.warning(f"Error checking for restart info: {e}")
            return False

    def _setup_events(self) -> None:
        """Set up Discord event handlers."""

        @self.bot.event
        async def on_connect():
            logger.info("Connected to Discord")

        @self.bot.event
        async def on_ready():
            await self.bot.tree.sync()

            # Start the message queue service
            await queue_service.start()

            # Start the auto-update service
            auto_update_service.start()

            print(f"🤖 Logged in as {self.bot.user}")

            # Log auto-update status
            status = auto_update_service.get_status()
            if status["enabled"]:
                print(f"🔄 Auto-update enabled (checking every {status['check_interval']}s)")
            else:
                print("🔄 Auto-update disabled")

            # Send update announcement if this was a restart
            await self._send_update_announcement_if_needed()

            print("🚀 Bot ready!")

        @self.bot.event
        async def on_disconnect():
            logger.warning("Disconnected from Discord, attempting to reconnect")

        @self.bot.event
        async def on_message(message: discord.Message):
            await self._handle_message(message)

    def _setup_commands(self) -> None:
        """Set up Discord slash commands."""
        chat_commands = ChatCommands(self.bot)
        chat_commands.setup_commands()

        image_commands = ImageCommands(self.bot)
        image_commands.setup_commands()

        system_commands = SystemCommands(self.bot)
        system_commands.setup_commands()

    async def _handle_message(self, message: discord.Message) -> None:
        """
        Handle incoming Discord messages.

        Args:
            message: The Discord message to handle
        """
        # Ignore bots and DMs
        if message.author.bot or not isinstance(message.channel, discord.TextChannel):
            return

        # Handle bot mentions
        if self.bot.user and self.bot.user in message.mentions and REPLY_TO_MENTIONS:
            model = state_service.get_model(message.channel.id) or DEFAULT_MODEL

            # Queue the mention for FIFO processing
            queued = await mention_handler.queue_mention(message, self.bot.user, model)

            if not queued:
                logger.warning(
                    f"""Failed to queue mention from {
                    message.author} in #{
                    message.channel} - queue may be full"""
                )
                # Optionally, you could fall back to immediate processing:
                # await mention_handler.handle_mention(message, self.bot.user,
                # model)

        # Ensure other commands are still processed
        await self.bot.process_commands(message)

    def run(self) -> None:
        """Start the Discord bot."""
        print("🔌 Starting Discord bot...")
        try:
            self.bot.run(DISCORD_BOT_TOKEN)
        except Exception as e:
            logger.error(f"Bot failed to start: {e}")
            raise
        finally:
            # Ensure services are properly shut down
            import asyncio

            try:
                # Stop auto-update service
                auto_update_service.stop()
                logger.info("Auto-update service stopped")
            except Exception as e:
                logger.error(f"Error shutting down auto-update service: {e}")

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If we're in an async context, schedule the shutdown
                    asyncio.create_task(queue_service.stop())
                else:
                    # If we're not in an async context, run it
                    loop.run_until_complete(queue_service.stop())
            except Exception as e:
                logger.error(f"Error shutting down queue service: {e}")

            # Never clean up temporary files during shutdown - they should only be
            # cleaned up after successful loading on startup
            logger.debug(
                "Skipping temp file cleanup during shutdown - files will be cleaned up on next startup after loading"
            )

            logger.info("Bot shutdown.")


def create_bot() -> DiscordBotClient:
    """Create and return a new Discord bot client."""
    return DiscordBotClient()
