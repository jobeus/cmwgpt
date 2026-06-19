"""
Discord Bot Client - Main bot setup and event handling
"""

import asyncio
import logging
import subprocess
from typing import Any, Optional

import discord
from discord.ext import commands

from src.config import AppConfig, get_system_prompt, load_config
from src.bot.commands.image import ImageCommands
from src.bot.commands.system import SystemCommands
from src.bot.commands.interject import InterjectCommands
from src.bot.commands.death import DeathCommands
from src.bot.commands.wikicount import WikiCountCommands

from src.utils.logger import setup_logger

# Configure logging
root_logger = setup_logger()
logger = logging.getLogger("discord_bot")


class DiscordBotClient:
    """Main Discord bot client with event handling and command setup."""

    def __init__(self, *, config: Optional[AppConfig] = None, services: Any, mention_handler=None):
        self.config = config or load_config()
        self.services = services
        self.mention_handler = mention_handler or services.mention_handler

        # Configure Discord bot with intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        self.bot = commands.Bot(command_prefix="/", intents=intents)
        self.services.openai_service.set_bot_id_loader(
            lambda: self.bot.user.id if self.bot.user else None)

        # Load any saved state from previous restart
        self._load_saved_state()

        # Set current git SHA for comparison on next restart
        self._set_current_git_sha()

        # Set up auto-update service
        self._setup_auto_update()

        # Set up announcement service
        self.services.announcement_service.set_bot(self.bot)

        # Set up interject service
        self.services.interject_service.set_bot(self.bot)

        # Set up death service
        self.services.death_service.set_bot(self.bot)

        # Set up event handlers
        self._setup_events()

        # Set up commands
        self._setup_commands()

    def _load_saved_state(self) -> None:
        """Load any saved state from previous restart."""
        try:
            if self.services.state_service.load_state_from_temp_files():
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
                # This preserves the previous SHA for comparison by the
                # announcement service
                if not self.services.state_service.get_last_git_sha():
                    self.services.state_service.set_last_git_sha(current_sha)
                    logger.info(f"Set initial git SHA: {current_sha[:7]}")
                else:
                    # Don't update - let the announcement service handle the
                    # comparison and update
                    logger.debug(
                        f"Current git SHA: {current_sha[:7]}, preserving previous SHA for comparison")
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
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
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
            self.services.auto_update_service.set_restart_callback(
                self.services.restart_handler.perform_restart)
            logger.info("Auto-update service configured")
        except Exception as e:
            logger.error(f"Error setting up auto-update service: {e}")

    async def _send_update_announcement_if_needed(self) -> None:
        """Send update announcement if this was a restart."""
        try:
            # Check if we have active channels and this looks like a restart
            active_channels = self.services.state_service.get_active_channels()
            if not active_channels:
                logger.info(
                    "No active channels found, skipping update announcement")
                return

            # Small delay to ensure bot is fully ready
            await asyncio.sleep(2)

            # Check for restart info to determine if it was manual
            was_manual = self._check_for_restart_info()

            # Send announcement
            await self.services.announcement_service.announce_update(was_manual=was_manual)
            logger.info(
                f"Sent update announcements to active channels (manual: {was_manual})")

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

            # Look for state files that might contain restart info
            pattern = "/tmp/cmwgpt_state_backup_*.json"
            state_files = glob.glob(pattern)

            was_manual = False
            for state_file in state_files:
                try:
                    with open(state_file, "r") as f:
                        state_data = json.load(f)

                    # Check if restart info is embedded in the state file
                    restart_info = state_data.get("restart_info", {})
                    if restart_info:
                        was_manual = restart_info.get("manual_restart", False)
                        logger.info(
                            f"Found restart info in state: manual={was_manual}")
                        break

                except Exception as e:
                    logger.warning(
                        f"Error reading state file {state_file}: {e}")

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
            # Sync commands - guild-specific if configured (instant),
            # otherwise global (can take up to 1 hour)
            if self.config.discord_guild_id:
                guild = discord.Object(id=int(self.config.discord_guild_id))
                self.bot.tree.copy_global_to(guild=guild)
                await self.bot.tree.sync(guild=guild)
                logger.info(f"Commands synced instantly to guild {self.config.discord_guild_id}")
            else:
                await self.bot.tree.sync()
                logger.info("Commands synced globally (may take up to 1 hour to propagate)")

            # Start the message queue service
            await self.services.queue_service.start()

            # Start the auto-update service
            self.services.auto_update_service.start()

            # Start the interject service
            self.services.interject_service.start()

            # Start the death service
            self.services.death_service.start()

            print(f"🤖 Logged in as {self.bot.user}")

            # Log auto-update status
            status = self.services.auto_update_service.get_status()
            if status["enabled"]:
                print(
                    f"🔄 Auto-update enabled (checking every {status['check_interval']}s)")
            else:
                print("🔄 Auto-update disabled")

            # Send update announcement if this was a restart
            await self._send_update_announcement_if_needed()

            print("🚀 Bot ready!")

        @self.bot.event
        async def on_disconnect():
            logger.warning(
                "Disconnected from Discord, attempting to reconnect")

        @self.bot.event
        async def on_message(message: discord.Message):
            await self._handle_message(message)

    def _setup_commands(self) -> None:
        """Set up Discord slash commands."""
        image_commands = ImageCommands(
            self.bot,
            state_service=self.services.state_service,
            message_service=self.services.message_service,
            runpod_service=self.services.runpod_service,
            default_draw_model=self.config.default_draw_model,
            default_edit_model=self.config.default_edit_model,
            enable_runpod_models=bool(self.config.runpod_io_api_key),
        )
        image_commands.setup_commands()

        system_commands = SystemCommands(
            self.bot,
            queue_service_instance=self.services.queue_service,
            state_service_instance=self.services.state_service,
            auto_update_service=self.services.auto_update_service,
            system_prompt_loader=lambda: get_system_prompt(self.config.system_prompt_path),
            default_model=self.config.default_model,
        )
        system_commands.setup_commands()

        interject_commands = InterjectCommands(
            self.bot,
            state_service_instance=self.services.state_service,
            interject_service=self.services.interject_service,
        )
        interject_commands.setup_commands()

        death_commands = DeathCommands(
            self.bot,
            state_service_instance=self.services.state_service,
            death_channel_id=self.config.death_channel_id,
        )
        death_commands.setup_commands()

        wikicount_commands = WikiCountCommands(
            self.bot,
            death_service_instance=self.services.death_service,
            state_service_instance=self.services.state_service,
        )
        wikicount_commands.setup_commands()

    async def _handle_message(self, message: discord.Message) -> None:
        """
        Handle incoming Discord messages.

        Args:
            message: The Discord message to handle
        """
        # Ignore bots and DMs
        if message.author.bot or not isinstance(
                message.channel, discord.TextChannel):
            return

        # Check for X/Twitter links to provide xcancel.com alternatives
        import re
        pattern = r'\b(?:https?://)?(?:www\.)?(?:x|twitter)\.com/([^\s>)]+)'
        content = getattr(message, 'content', '')
        matches = re.finditer(pattern, content, re.IGNORECASE)
        xcancel_links = []
        for match in matches:
            path = match.group(1)
            # Drop query string / tracking args (e.g. ?get_args=xxx)
            path = path.split('?')[0]
            # Remove trailing punctuation that might have been captured
            path = re.sub(r'[.,!?\'"]+$', '', path)
            xcancel_links.append(f"( <https://xcancel.com/{path}> )")
            
        if xcancel_links:
            await message.channel.send("\n".join(xcancel_links))

        # Handle bot mentions
        if self.bot.user and self.bot.user in message.mentions and self.config.reply_to_mentions:
            model = self.services.state_service.get_model(
                message.channel.id) or self.config.default_model

            # Queue the mention for FIFO processing
            queued = await self.mention_handler.queue_mention(message, self.bot.user, model)

            if not queued:
                logger.warning(
                    f"Failed to queue mention from {message.author} in #{message.channel} - queue may be full"
                )
                # Optionally, you could fall back to immediate processing:
                # await mention_handler.handle_mention(message, self.bot.user,
                # model)

        # Check for interjection opportunity (event-driven, no polling)
        await self.services.interject_service.on_new_message(message)

        # Ensure other commands are still processed
        await self.bot.process_commands(message)

    def run(self) -> None:
        """Start the Discord bot."""
        print("🔌 Starting Discord bot...")
        try:
            self.bot.run(self.config.discord_bot_token)
        except Exception as e:
            logger.error(f"Bot failed to start: {e}")
            raise
        finally:
            # Only perform cleanup if not already handled by signal handler
            # The signal handler sets skip_cleanup flag to prevent duplicate
            # cleanup
            if not self.services.restart_handler.should_skip_cleanup():
                logger.info("Performing bot shutdown cleanup...")

                # Ensure services are properly shut down
                import asyncio

                try:
                    # Stop auto-update service
                    self.services.auto_update_service.stop()
                    logger.info("Auto-update service stopped")
                except Exception as e:
                    logger.error(
                        f"Error shutting down auto-update service: {e}")

                try:
                    self.services.interject_service.stop()
                    self.services.death_service.stop()
                    logger.info("Background services stopped")
                except Exception as e:
                    logger.error(
                        f"Error shutting down background services: {e}")

                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # If we're in an async context, schedule the shutdown
                        asyncio.create_task(self.services.queue_service.stop())
                    else:
                        # If we're not in an async context, run it
                        loop.run_until_complete(self.services.queue_service.stop())
                except Exception as e:
                    logger.error(f"Error shutting down queue service: {e}")

                # Never clean up temporary files during shutdown - they should only be
                # cleaned up after successful loading on startup
                logger.debug(
                    "Skipping temp file cleanup during shutdown - files will be cleaned up on next startup after loading"
                )
            else:
                logger.info(
                    "Skipping bot cleanup - already handled by signal handler")

            logger.info("Bot shutdown.")


def create_bot() -> DiscordBotClient:
    """Create and return a new Discord bot client."""
    from src.startup import create_bot_client

    return create_bot_client()
