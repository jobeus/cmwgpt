"""
Per-channel FIFO message queue service for the Discord bot.

This service provides a queue-based message processing system to ensure:
- Messages are processed one at a time *per channel* (FIFO order)
- Different channels process concurrently (not blocked by each other)
- No race conditions between concurrent requests in the same channel
- Proper error handling and logging
- Graceful shutdown capabilities
"""

import asyncio
import logging
from typing import Dict, Any, Callable, Awaitable, Optional
from dataclasses import dataclass, field
from enum import Enum
import discord

from src.utils.discord_error_utils import concise_error, is_discord_server_error

logger = logging.getLogger(__name__)

# How long stop() waits for workers to finish the message they're on (a mention
# reply can take a minute with search) before cancelling them.
STOP_TIMEOUT_SECONDS = 90.0


class MessageType(Enum):
    """Types of messages that can be queued."""

    MENTION = "mention"
    COMMAND = "command"
    SHUTDOWN = "shutdown"
    RESTART = "restart"


async def _shutdown_handler() -> None:
    """Placeholder for shutdown messages; the worker stops before calling it."""


@dataclass
class QueuedMessage:
    """Represents a message in the processing queue."""

    message_type: MessageType
    handler: Callable[..., Awaitable[None]]
    timestamp: float
    channel_id: int = 0
    # Additional data for different message types
    discord_message: Optional[discord.Message] = None
    bot_user: Optional[discord.User] = None
    model: Optional[str] = None
    interaction: Optional[discord.Interaction] = None
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)


class QueueService:
    """Per-channel FIFO message queue service for processing bot messages."""

    def __init__(self, max_queue_size: int = 100):
        """
        Initialize the queue service.

        Args:
            max_queue_size: Maximum number of messages to queue per channel
        """
        self._max_queue_size = max_queue_size
        self._channel_queues: Dict[int, asyncio.Queue[QueuedMessage]] = {}
        self._channel_workers: Dict[int, asyncio.Task] = {}
        self._is_running = False
        self._stats = {
            "messages_processed": 0,
            "messages_failed": 0,
            "queue_overflows": 0}

        logger.info(
            f"QueueService initialized with max queue size per channel: {max_queue_size}")

    async def start(self) -> None:
        """Start the queue service (enables accepting messages)."""
        if self._is_running:
            logger.warning("QueueService is already running")
            return

        self._is_running = True
        logger.info("QueueService started")

    async def stop(self) -> None:
        """Stop all channel workers gracefully."""
        if not self._is_running:
            logger.warning("QueueService is not running")
            return

        logger.info("Stopping QueueService...")
        self._is_running = False

        # Send shutdown signal to all active channel queues
        for channel_id, queue in self._channel_queues.items():
            try:
                shutdown_msg = QueuedMessage(
                    message_type=MessageType.SHUTDOWN,
                    handler=_shutdown_handler,
                    timestamp=asyncio.get_event_loop().time(),
                    channel_id=channel_id,
                )
                queue.put_nowait(shutdown_msg)
            except asyncio.QueueFull:
                logger.warning(
                    f"Queue full for channel {channel_id}, forcing shutdown")

        # Wait for all channel workers to complete
        if self._channel_workers:
            workers = list(self._channel_workers.values())
            try:
                await asyncio.wait_for(
                    asyncio.gather(*workers, return_exceptions=True),
                    timeout=STOP_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Channel workers did not complete within timeout, cancelling")
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

        self._channel_queues.clear()
        self._channel_workers.clear()
        logger.info("QueueService stopped")

    def _ensure_channel_worker(self, channel_id: int) -> asyncio.Queue:
        """
        Ensure a queue and worker exist for the given channel.

        Args:
            channel_id: The Discord channel ID

        Returns:
            The asyncio.Queue for the channel
        """
        if channel_id not in self._channel_queues:
            self._channel_queues[channel_id] = asyncio.Queue(
                maxsize=self._max_queue_size)

        worker = self._channel_workers.get(channel_id)
        if worker is None or worker.done():
            if worker is not None and worker.done():
                logger.warning(
                    f"Worker for channel {channel_id} died, respawning")
            self._channel_workers[channel_id] = asyncio.create_task(
                self._process_channel_messages(channel_id))
            logger.debug(
                f"Created queue and worker for channel {channel_id}")

        return self._channel_queues[channel_id]

    async def queue_mention(
        self,
        message: discord.Message,
        bot_user: discord.User,
        model: str,
        handler: Callable[[discord.Message, discord.User, str], Awaitable[None]],
    ) -> bool:
        """
        Queue a mention message for per-channel processing.

        Args:
            message: Discord message containing the mention
            bot_user: Bot user object
            model: Model to use for response
            handler: Async handler function to process the mention

        Returns:
            True if message was queued successfully, False if queue is full
        """
        if not self._is_running:
            logger.error("Cannot queue message: QueueService is not running")
            return False

        channel_id = message.channel.id
        queue = self._ensure_channel_worker(channel_id)

        queued_msg = QueuedMessage(
            message_type=MessageType.MENTION,
            handler=handler,
            timestamp=asyncio.get_event_loop().time(),
            channel_id=channel_id,
            discord_message=message,
            bot_user=bot_user,
            model=model,
        )

        try:
            queue.put_nowait(queued_msg)
            logger.debug(
                f"Queued mention from {message.author} in #{message.channel} (channel queue {channel_id})"
            )
            return True
        except asyncio.QueueFull:
            self._stats["queue_overflows"] += 1
            logger.warning(
                f"Channel {channel_id} queue full, dropping message from {message.author}"
            )
            return False

    async def queue_command(self,
                            interaction: discord.Interaction,
                            handler: Callable[...,
                                              Awaitable[None]],
                            *args,
                            **kwargs) -> bool:
        """
        Queue a command for per-channel processing.

        Args:
            interaction: Discord interaction object
            handler: Async handler function to process the command
            *args: Positional arguments for the handler
            **kwargs: Keyword arguments for the handler

        Returns:
            True if command was queued successfully, False if queue is full
        """
        if not self._is_running:
            logger.error("Cannot queue command: QueueService is not running")
            return False

        channel_id = interaction.channel.id
        queue = self._ensure_channel_worker(channel_id)

        queued_msg = QueuedMessage(
            message_type=MessageType.COMMAND,
            handler=handler,
            timestamp=asyncio.get_event_loop().time(),
            channel_id=channel_id,
            interaction=interaction,
            args=args,
            kwargs=kwargs,
        )

        try:
            queue.put_nowait(queued_msg)
            logger.debug(
                f"Queued command from {interaction.user} in #{interaction.channel} (channel queue {channel_id})"
            )
            return True
        except asyncio.QueueFull:
            self._stats["queue_overflows"] += 1
            logger.warning(
                f"Channel {channel_id} queue full, dropping command from {interaction.user}"
            )
            return False

    async def queue_restart(
        self,
        handler: Callable[[],
                          Awaitable[None]]) -> bool:
        """
        Queue a restart on the system (channel 0) worker.

        The handler only prepares the restart and schedules shutdown; the
        other channels' workers finish their current message during shutdown.

        Args:
            handler: Async handler function to process the restart

        Returns:
            True if restart was queued successfully, False if queue is full
        """
        if not self._is_running:
            logger.error("Cannot queue restart: QueueService is not running")
            return False

        # Use channel_id 0 for system-level operations
        queue = self._ensure_channel_worker(0)

        queued_msg = QueuedMessage(
            message_type=MessageType.RESTART,
            handler=handler,
            timestamp=asyncio.get_event_loop().time(),
            channel_id=0,
        )

        try:
            queue.put_nowait(queued_msg)
            logger.info("Queued restart signal")
            return True
        except asyncio.QueueFull:
            self._stats["queue_overflows"] += 1
            logger.warning("Queue full, dropping restart signal")
            return False

    async def _process_channel_messages(self, channel_id: int) -> None:
        """
        Message processing loop for a single channel.

        Args:
            channel_id: The channel ID this worker processes
        """
        logger.debug(f"Channel {channel_id} processing loop started")
        queue = self._channel_queues[channel_id]

        while self._is_running:
            try:
                # Get next message from this channel's queue
                queued_msg = await queue.get()

                # Check for shutdown signal
                if queued_msg.message_type == MessageType.SHUTDOWN:
                    logger.debug(
                        f"Channel {channel_id} received shutdown signal")
                    queue.task_done()
                    break

                # Check for restart signal
                if queued_msg.message_type == MessageType.RESTART:
                    logger.info("Received restart signal")
                    await queued_msg.handler()
                    queue.task_done()
                    continue

                # Process the message
                await self._handle_queued_message(queued_msg)

                # Mark task as done
                queue.task_done()

            except asyncio.CancelledError:
                logger.debug(
                    f"Channel {channel_id} processing loop cancelled")
                break
            except Exception as e:
                # Catch *everything* else (discord.HTTPException, KeyError, ...)
                # so an unexpected error can never kill the channel worker.
                if is_discord_server_error(e):
                    logger.error(
                        f"Discord API server error in channel {channel_id} processing loop: {concise_error(e)}. "
                        "This is a Discord-side outage; the worker is still running and will keep processing new messages."
                    )
                else:
                    logger.error(
                        f"Error in channel {channel_id} processing loop: {e}",
                        exc_info=True)
                self._stats["messages_failed"] += 1

        logger.debug(f"Channel {channel_id} processing loop ended")

    async def _handle_queued_message(self, queued_msg: QueuedMessage) -> None:
        """
        Handle a single queued message.

        Args:
            queued_msg: The queued message to process
        """
        start_time = asyncio.get_event_loop().time()

        try:
            # Log processing start
            if queued_msg.message_type == MessageType.MENTION:
                logger.debug(
                    f"Processing {queued_msg.message_type.value} message from {getattr(queued_msg.discord_message, "author", None)}"
                )
            elif queued_msg.message_type == MessageType.COMMAND:
                logger.debug(
                    f"Processing {queued_msg.message_type.value} from {getattr(queued_msg.interaction, "user", None)}"
                )
            elif queued_msg.message_type == MessageType.RESTART:
                logger.debug(
                    f"Processing {queued_msg.message_type.value} signal"
                )

            # Call the appropriate handler directly - they should be properly
            # async
            if queued_msg.message_type == MessageType.MENTION:
                await queued_msg.handler(queued_msg.discord_message, queued_msg.bot_user, queued_msg.model)
            elif queued_msg.message_type == MessageType.COMMAND:
                await queued_msg.handler(queued_msg.interaction, *queued_msg.args, **queued_msg.kwargs)
            elif queued_msg.message_type == MessageType.RESTART:
                await queued_msg.handler()

            processing_time = asyncio.get_event_loop().time() - start_time
            self._stats["messages_processed"] += 1

            logger.debug(f"Processed message in {processing_time:.2f}s")

        except Exception as e:
            # Catch *everything* (discord.HTTPException, KeyError, ...) so a
            # misbehaving handler can never propagate up and kill the worker.
            self._stats["messages_failed"] += 1
            if queued_msg.message_type == MessageType.MENTION:
                user_info = f"{getattr(queued_msg.discord_message, "author", None)}"
            elif queued_msg.message_type == MessageType.COMMAND:
                user_info = f"{getattr(queued_msg.interaction, "user", None)}"
            elif queued_msg.message_type == MessageType.RESTART:
                user_info = "system"
            else:
                user_info = "unknown"

            if is_discord_server_error(e):
                logger.error(
                    f"Discord API server error while processing {queued_msg.message_type.value} from {user_info}: "
                    f"{concise_error(e)}. This is a Discord-side outage; the bot is still running and will "
                    "handle new messages normally."
                )
            else:
                logger.error(
                    f"Error processing {queued_msg.message_type.value} from {user_info}: {e}",
                    exc_info=True,
                )

    def get_queue_size(self) -> int:
        """Get total queue size across all channels."""
        return sum(q.qsize() for q in self._channel_queues.values())

    def get_channel_queue_size(self, channel_id: int) -> int:
        """Get queue size for a specific channel."""
        if channel_id in self._channel_queues:
            return self._channel_queues[channel_id].qsize()
        return 0

    def get_stats(self) -> Dict[str, Any]:
        """
        Get queue processing statistics.

        Returns:
            Dictionary with processing statistics
        """
        return {
            "is_running": self._is_running,
            "total_queue_size": self.get_queue_size(),
            "active_channels": len(self._channel_queues),
            "max_queue_size_per_channel": self._max_queue_size,
            **self._stats,
        }

    def is_running(self) -> bool:
        """Check if the queue service is running."""
        return self._is_running

