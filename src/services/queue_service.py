"""
FIFO message queue service for the Discord bot.

This service provides a queue-based message processing system to ensure:
- Messages are processed one at a time (FIFO order)
- No race conditions between concurrent requests
- Proper error handling and logging
- Graceful shutdown capabilities
"""

import asyncio
import logging
from typing import Dict, Any, Callable, Awaitable, Optional
from dataclasses import dataclass
from enum import Enum
import discord

logger = logging.getLogger(__name__)


class MessageType(Enum):
    """Types of messages that can be queued."""

    MENTION = "mention"
    COMMAND = "command"
    SHUTDOWN = "shutdown"


@dataclass
class QueuedMessage:
    """Represents a message in the processing queue."""

    message_type: MessageType
    handler: Callable[..., Awaitable[None]]
    timestamp: float
    # Additional data for different message types
    discord_message: discord.Message = None
    bot_user: discord.User = None
    model: str = None
    interaction: discord.Interaction = None
    args: tuple = None
    kwargs: dict = None


class QueueService:
    """FIFO message queue service for processing bot messages."""

    def __init__(self, max_queue_size: int = 100):
        """
        Initialize the queue service.

        Args:
            max_queue_size: Maximum number of messages to queue
        """
        self._queue: asyncio.Queue[QueuedMessage] = asyncio.Queue(
            maxsize=max_queue_size)
        self._processing_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._stats = {
            "messages_processed": 0,
            "messages_failed": 0,
            "queue_overflows": 0}

        logger.info(
            f"QueueService initialized with max queue size: {max_queue_size}")

    async def start(self) -> None:
        """Start the message processing loop."""
        if self._is_running:
            logger.warning("QueueService is already running")
            return

        self._is_running = True

        # Create the processing task but don't await it - let it run
        # concurrently
        self._processing_task = asyncio.create_task(self._process_messages())
        logger.info("QueueService started")

    async def stop(self) -> None:
        """Stop the message processing loop gracefully."""
        if not self._is_running:
            logger.warning("QueueService is not running")
            return

        logger.info("Stopping QueueService...")

        # Add shutdown message to queue
        try:
            shutdown_msg = QueuedMessage(
                message_type=MessageType.SHUTDOWN,
                discord_message=None,
                bot_user=None,
                model="",
                handler=None,
                timestamp=asyncio.get_event_loop().time(),
            )
            await self._queue.put(shutdown_msg)
        except asyncio.QueueFull:
            logger.warning("Queue full, forcing shutdown")

        # Wait for processing task to complete
        if self._processing_task:
            try:
                await asyncio.wait_for(self._processing_task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "Processing task did not complete within timeout, cancelling")
                self._processing_task.cancel()
                try:
                    await self._processing_task
                except asyncio.CancelledError:
                    pass

        self._is_running = False
        logger.info("QueueService stopped")

    async def queue_mention(
        self,
        message: discord.Message,
        bot_user: discord.User,
        model: str,
        handler: Callable[[discord.Message, discord.User, str], Awaitable[None]],
    ) -> bool:
        """
        Queue a mention message for processing.

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

        queued_msg = QueuedMessage(
            message_type=MessageType.MENTION,
            handler=handler,
            timestamp=asyncio.get_event_loop().time(),
            discord_message=message,
            bot_user=bot_user,
            model=model,
        )

        try:
            # Use put_nowait to immediately fail if queue is full
            self._queue.put_nowait(queued_msg)
            logger.debug(
                f"Queued mention from {
                    message.author} in #{
                    message.channel}"
            )
            return True
        except asyncio.QueueFull:
            self._stats["queue_overflows"] += 1
            logger.warning(
                f"Queue full, dropping message from {
                    message.author} in #{
                    message.channel}"
            )
            return False

    async def queue_command(self,
                            interaction: discord.Interaction,
                            handler: Callable[...,
                                              Awaitable[None]],
                            *args,
                            **kwargs) -> bool:
        """
        Queue a command for processing.

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

        queued_msg = QueuedMessage(
            message_type=MessageType.COMMAND,
            handler=handler,
            timestamp=asyncio.get_event_loop().time(),
            interaction=interaction,
            args=args,
            kwargs=kwargs,
        )

        try:
            # Use put_nowait to immediately fail if queue is full
            self._queue.put_nowait(queued_msg)
            logger.debug(
                f"Queued command from {
                    interaction.user} in #{
                    interaction.channel}"
            )
            return True
        except asyncio.QueueFull:
            self._stats["queue_overflows"] += 1
            logger.warning(
                f"Queue full, dropping command from {
                    interaction.user} in #{
                    interaction.channel}"
            )
            return False

    async def _process_messages(self) -> None:
        """Main message processing loop."""
        logger.info("Message processing loop started")

        while self._is_running:
            try:
                # Get next message from queue
                queued_msg = await self._queue.get()

                # Check for shutdown signal
                if queued_msg.message_type == MessageType.SHUTDOWN:
                    logger.info("Received shutdown signal")
                    break

                # Process the message
                await self._handle_queued_message(queued_msg)

                # Mark task as done
                self._queue.task_done()

            except asyncio.CancelledError:
                logger.info("Message processing loop cancelled")
                break
            except Exception as e:
                logger.error(
                    f"Error in message processing loop: {e}",
                    exc_info=True)
                self._stats["messages_failed"] += 1

        logger.info("Message processing loop ended")

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
                    f"Processing {
                        queued_msg.message_type.value} message from {
                        queued_msg.discord_message.author}"
                )
            elif queued_msg.message_type == MessageType.COMMAND:
                logger.debug(
                    f"Processing {
                        queued_msg.message_type.value} from {
                        queued_msg.interaction.user}"
                )

            # Call the appropriate handler directly - they should be properly
            # async
            if queued_msg.message_type == MessageType.MENTION:
                await queued_msg.handler(queued_msg.discord_message, queued_msg.bot_user, queued_msg.model)
            elif queued_msg.message_type == MessageType.COMMAND:
                await queued_msg.handler(queued_msg.interaction, *queued_msg.args, **queued_msg.kwargs)

            processing_time = asyncio.get_event_loop().time() - start_time
            self._stats["messages_processed"] += 1

            logger.debug(f"Processed message in {processing_time:.2f}s")

        except Exception as e:
            self._stats["messages_failed"] += 1
            if queued_msg.message_type == MessageType.MENTION:
                user_info = f"{queued_msg.discord_message.author}"
            elif queued_msg.message_type == MessageType.COMMAND:
                user_info = f"{queued_msg.interaction.user}"
            else:
                user_info = "unknown"

            logger.error(
                f"Error processing {
                    queued_msg.message_type.value} from {user_info}: {e}",
                exc_info=True,
            )

    def get_queue_size(self) -> int:
        """Get current queue size."""
        return self._queue.qsize()

    def get_stats(self) -> Dict[str, Any]:
        """
        Get queue processing statistics.

        Returns:
            Dictionary with processing statistics
        """
        return {
            "is_running": self._is_running,
            "queue_size": self._queue.qsize(),
            "max_queue_size": self._queue.maxsize,
            **self._stats,
        }

    def is_running(self) -> bool:
        """Check if the queue service is running."""
        return self._is_running


# Global queue service instance
queue_service = QueueService()
