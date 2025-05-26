"""
Discord Bot Client - Main bot setup and event handling
"""

import logging

import discord
from discord.ext import commands

from src.config import DISCORD_BOT_TOKEN, DEFAULT_MODEL, REPLY_TO_MENTIONS
from src.bot.handlers.mention import mention_handler
from src.bot.commands.chat import ChatCommands
from src.bot.commands.image import ImageCommands
from src.bot.commands.system import SystemCommands
from src.services.queue_service import queue_service
from src.services.state_service import state_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger("discord_bot")


class DiscordBotClient:
    """Main Discord bot client with event handling and command setup."""

    def __init__(self):
        # Configure Discord bot with intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        self.bot = commands.Bot(command_prefix="/", intents=intents)

        # Set up event handlers
        self._setup_events()

        # Set up commands
        self._setup_commands()

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

            logger.info(
                f"Logged in as {
                    self.bot.user} (ID: {
                    self.bot.user.id})")
            logger.info("Message queue service started")

        @self.bot.event
        async def on_disconnect():
            logger.warning(
                "Disconnected from Discord, attempting to reconnect")

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
        if message.author.bot or not isinstance(
                message.channel, discord.TextChannel):
            return

        # Handle bot mentions
        if self.bot.user and self.bot.user in message.mentions and REPLY_TO_MENTIONS:
            model = state_service.get_model(
                message.channel.id) or DEFAULT_MODEL

            # Queue the mention for FIFO processing
            queued = await mention_handler.queue_mention(message, self.bot.user, model)

            if not queued:
                logger.warning(
                    f"Failed to queue mention from {
                        message.author} in #{
                        message.channel} - queue may be full")
                # Optionally, you could fall back to immediate processing:
                # await mention_handler.handle_mention(message, self.bot.user,
                # model)

        # Ensure other commands are still processed
        await self.bot.process_commands(message)

    def run(self) -> None:
        """Start the Discord bot."""
        logger.info("Starting bot...")
        try:
            self.bot.run(DISCORD_BOT_TOKEN)
        except Exception as e:
            logger.error(f"Bot failed to start: {e}")
            raise
        finally:
            # Ensure queue service is properly shut down
            import asyncio

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

            logger.info("Bot shutdown.")


def create_bot() -> DiscordBotClient:
    """Create and return a new Discord bot client."""
    return DiscordBotClient()
