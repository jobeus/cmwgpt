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
from src.bot.commands.cost import build_cost_commands
from src.bot.commands.death import DeathCommands
from src.bot.commands.memory import MemoryCommands
from src.bot.commands.wikicount import WikiCountCommands
from src.services.model_catalog import is_model_unavailable
from src.telemetry import instrument_command_tree

logger = logging.getLogger(__name__)


class DiscordBotClient:
    """Main Discord bot client with event handling and command setup."""

    def __init__(self, *, config: Optional[AppConfig] = None, services: Any, mention_handler=None):
        self.config = config or load_config()
        self.services = services
        self.mention_handler = mention_handler or services.mention_handler

        # on_ready fires again on every gateway reconnect; this flag ensures
        # once-only work (command sync, service starts, announcements) only
        # runs on the first ready event.
        self._ready_once_completed = False

        # Configure Discord bot with intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        self.bot = commands.Bot(command_prefix="/", intents=intents)
        instrument_command_tree(self.bot.tree)
        self._shutdown_started = False
        for service in (self.services.gemini_service, self.services.anthropic_service):
            service.set_bot_id_loader(lambda: self.bot.user.id if self.bot.user else None)
        self.services.restart_handler.set_shutdown_callback(self.shutdown)

        # Load any saved state from previous restart
        self._load_saved_state()

        # Set current git SHA for comparison on next restart
        self._set_current_git_sha()

        # Set up auto-update service
        self._setup_auto_update()

        # Set up announcement service
        self.services.announcement_service.set_bot(self.bot)

        # Set up death service
        self.services.death_service.set_bot(self.bot)

        # Set up the nightly user-memory job
        if getattr(self.services, "memory_service", None) is not None:
            self.services.memory_service.set_bot(self.bot)

        # Reminders post themselves when due, then hand any task to the mention handler
        if getattr(self.services, "reminder_service", None) is not None:
            self.services.reminder_service.set_bot(self.bot)
            self.services.reminder_service.set_task_runner(self._run_reminder_task)

        # Set up event handlers
        self._setup_events()

        # Set up commands
        self._setup_commands()

    def _load_saved_state(self) -> None:
        """Load any saved state from previous restart."""
        try:
            if self.services.state_service.load_restart_state():
                logger.info("Restored the state saved before the last restart")
            else:
                logger.info("No saved state from a restart; starting fresh")
        except Exception as e:
            logger.error("Couldn't restore the saved state, starting fresh: %s", e, exc_info=True)
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
                    logger.info("Set initial git SHA: %s", current_sha[:7])
                else:
                    # Don't update - let the announcement service handle the
                    # comparison and update
                    logger.debug(
                        "Current git SHA: %s, preserving previous SHA for comparison", current_sha[:7])
            else:
                logger.warning("Could not determine current git SHA")
        except Exception as e:
            logger.error("Error setting current git SHA: %s", e, exc_info=True)

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
                logger.error("Failed to get git SHA: %s", result.stderr)
                return None
        except Exception as e:
            logger.error("Error getting git SHA: %s", e, exc_info=True)
            return None

    def _setup_auto_update(self) -> None:
        """Set up the auto-update service."""
        try:
            # Set the restart callback
            self.services.auto_update_service.set_restart_callback(
                self.services.restart_handler.perform_restart)
            logger.info("Auto-update service configured")
        except Exception as e:
            logger.error("Error setting up auto-update service: %s", e, exc_info=True)

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
                "Sent update announcements to active channels (manual: %s)", was_manual)

        except Exception as e:
            logger.error("Error sending update announcement: %s", e, exc_info=True)

    def _check_for_restart_info(self) -> bool:
        """True if the previous process was restarted manually (/restart)."""
        return bool(self.services.state_service.restart_info.get("manual_restart", False))

    def _setup_events(self) -> None:
        """Set up Discord event handlers."""

        @self.bot.event
        async def on_connect():
            logger.info("Connected to Discord")

        @self.bot.event
        async def on_ready():
            # on_ready re-fires on every gateway reconnect — skip the
            # once-only setup (command sync is a rate-limited API call, and
            # services must not be started twice).
            if self._ready_once_completed:
                logger.info(
                    "on_ready fired again (gateway reconnect), skipping one-time setup")
                return
            self._ready_once_completed = True

            # This runs once, so each step is on its own: a step that fails (Discord 5xx on the
            # sync, the webhook port in use) mustn't leave the ones after it never started.
            async def step(name: str, run: Any) -> None:
                try:
                    result = run()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception("Startup step failed: %s", name)

            async def sync_commands() -> None:
                # Guild-specific if configured (instant), otherwise global (can take up to 1 hour)
                if self.config.discord_guild_id:
                    guild = discord.Object(id=int(self.config.discord_guild_id))
                    self.bot.tree.copy_global_to(guild=guild)
                    await self.bot.tree.sync(guild=guild)
                    logger.info("Commands synced instantly to guild %s", self.config.discord_guild_id)
                else:
                    await self.bot.tree.sync()
                    logger.info("Commands synced globally (may take up to 1 hour to propagate)")

            # The queue first: without it no mention is answered.
            await step("message queue", self.services.queue_service.start)
            await step("command sync", sync_commands)
            await step("deploy webhook", self.services.auto_update_service.start)
            if getattr(self.services, "package_update_service", None) is not None:
                await step("package update checks", self.services.package_update_service.start)
            await step("death service", self.services.death_service.start)

            if getattr(self.services, "memory_service", None) is not None:
                await step("memory service", self.services.memory_service.start)

            if getattr(self.services, "reminder_service", None) is not None:
                await step("reminder service", self.services.reminder_service.start)

            # Read the IRC log's dates and top speakers now, not on the first mention.
            if hasattr(self.mention_handler, "warm_up"):
                await step("IRC log warm-up", self.mention_handler.warm_up)

            logger.info("Logged in to Discord as %s in %s guild(s)", self.bot.user, len(self.bot.guilds))

            # Send update announcement if this was a restart
            await self._send_update_announcement_if_needed()

            logger.info("Bot ready")

        @self.bot.event
        async def on_disconnect():
            logger.warning(
                "Disconnected from Discord, attempting to reconnect")

        @self.bot.event
        async def on_resumed():
            logger.info("Resumed the Discord gateway session")

        @self.bot.event
        async def on_message(message: discord.Message):
            await self._handle_message(message)

    def _setup_commands(self) -> None:
        """Set up Discord slash commands."""
        image_commands = ImageCommands(
            self.bot,
            state_service=self.services.state_service,
            message_service=self.services.message_service,
            gemini_service=self.services.gemini_service,
            default_image_model=self.config.default_image_model,
        )
        image_commands.setup_commands()

        system_commands = SystemCommands(
            self.bot,
            queue_service_instance=self.services.queue_service,
            state_service_instance=self.services.state_service,
            auto_update_service=self.services.auto_update_service,
            system_prompt_loader=lambda: get_system_prompt(self.config.system_prompt_path),
            default_model=self.config.default_model,
            on_model_change=self._clear_model_caches,
        )
        system_commands.setup_commands()

        death_commands = DeathCommands(
            self.bot,
            state_service_instance=self.services.state_service,
            death_channel_id=self.config.death_channel_id,
            death_service_instance=self.services.death_service,
        )
        death_commands.setup_commands()

        wikicount_commands = WikiCountCommands(
            self.bot,
            death_service_instance=self.services.death_service,
            state_service_instance=self.services.state_service,
        )
        wikicount_commands.setup_commands()

        cost_commands = build_cost_commands(self.bot, getattr(self.services, "cost_store", None))
        if cost_commands is not None:
            cost_commands.setup_commands()

        if getattr(self.services, "memory_service", None) is not None:
            memory_commands = MemoryCommands(
                self.bot,
                memory_service=self.services.memory_service,
                memory_store=self.services.memory_store,
            )
            memory_commands.setup_commands()

    def _clear_model_caches(self, channel_id: int) -> None:
        """Drop per-channel provider state that a model switch makes stale."""
        self.services.gemini_service.clear_channel_cache(channel_id)
        if getattr(self.services, "anthropic_service", None) is not None:
            self.services.anthropic_service.clear_channel_cache(channel_id)

    def _channel_model(self, channel: Any) -> str:
        """The model a channel answers with: its ``/model``, or the default if that's unset or unavailable."""
        model = self.services.state_service.get_model(channel.id) or self.config.default_model
        if is_model_unavailable(model, self.config):
            logger.info("Model %s for #%s is unavailable; using %s", model, channel, self.config.default_model)
            model = self.config.default_model
        return model

    async def _run_reminder_task(self, reminder: Any, request: discord.Message) -> None:
        """Answer a due reminder's task in its channel, queued like a mention."""
        if self.bot.user is None:
            return
        queued = await self.mention_handler.queue_reminder_task(
            reminder, request, self.bot.user, self._channel_model(request.channel))
        if not queued:
            logger.warning("[reminder] Couldn't queue the task of #%s: the channel's queue is full", reminder.id)

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

        # Handle bot mentions
        if self.bot.user and self.bot.user in message.mentions and self.config.reply_to_mentions:
            model = self._channel_model(message.channel)

            # Queue the mention for FIFO processing
            queued = await self.mention_handler.queue_mention(message, self.bot.user, model)

            if not queued:
                logger.warning(
                    "Failed to queue mention from %s in #%s - queue may be full", message.author, message.channel
                )
                try:
                    await self.services.message_service.send_channel_reply(
                        message.channel,
                        "I'm too backed up in this channel to take that right now. Mention me again in a bit."
                    )
                except Exception as e:
                    logger.warning("Couldn't tell #%s the queue is full: %s", message.channel, e)

        # Ensure other commands are still processed
        await self.bot.process_commands(message)

    async def shutdown(self) -> None:
        """Shut down services, then the Discord connection (which ends ``run``). Idempotent."""
        if self._shutdown_started:
            return
        self._shutdown_started = True
        from src.startup import shutdown_services

        await shutdown_services(self.services)
        if not self.bot.is_closed():
            await self.bot.close()
        logger.info("Bot shutdown complete.")

    def run(self) -> None:
        """Start the Discord bot; returns once the bot has been closed."""
        logger.info("Starting the Discord bot")
        self.bot.run(self.config.discord_bot_token)


def create_bot() -> DiscordBotClient:
    """Create and return a new Discord bot client."""
    from src.startup import create_bot_client

    return create_bot_client()
