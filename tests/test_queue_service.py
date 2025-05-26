"""
Unit tests for the QueueService class.
Tests FIFO message queue functionality.
"""

from src.services.queue_service import QueueService
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock
import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestQueueService(unittest.TestCase):
    """Test FIFO message queue functionality."""

    def setUp(self):
        """Set up test environment."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.queue_service = QueueService(max_queue_size=3)

    def tearDown(self):
        """Clean up test environment."""
        # Ensure queue service is stopped
        if self.queue_service.is_running():
            self.loop.run_until_complete(self.queue_service.stop())
        self.loop.close()

    def test_initialization(self):
        """Test queue service initialization."""
        self.assertFalse(self.queue_service.is_running())
        self.assertEqual(self.queue_service.get_queue_size(), 0)

        stats = self.queue_service.get_stats()
        self.assertFalse(stats["is_running"])
        self.assertEqual(stats["queue_size"], 0)
        self.assertEqual(stats["max_queue_size"], 3)
        self.assertEqual(stats["messages_processed"], 0)
        self.assertEqual(stats["messages_failed"], 0)

    def test_start_stop_service(self):
        """Test starting and stopping the queue service."""

        async def run_test():
            # Test starting
            await self.queue_service.start()
            self.assertTrue(self.queue_service.is_running())

            # Test starting when already running
            await self.queue_service.start()  # Should not cause issues
            self.assertTrue(self.queue_service.is_running())

            # Test stopping
            await self.queue_service.stop()
            self.assertFalse(self.queue_service.is_running())

            # Test stopping when not running
            await self.queue_service.stop()  # Should not cause issues
            self.assertFalse(self.queue_service.is_running())

        self.loop.run_until_complete(run_test())

    def test_queue_mention_when_not_running(self):
        """Test queuing mention when service is not running."""

        async def run_test():
            # Create mock objects
            mock_message = MagicMock()
            mock_bot_user = MagicMock()
            mock_handler = AsyncMock()

            # Try to queue when not running
            result = await self.queue_service.queue_mention(mock_message, mock_bot_user, "test-model", mock_handler)

            self.assertFalse(result)

        self.loop.run_until_complete(run_test())

    def test_fifo_message_processing(self):
        """Test that messages are processed in FIFO order."""

        async def run_test():
            # Start the service
            await self.queue_service.start()

            # Wait for service to be fully ready
            await asyncio.sleep(0.01)

            # Track processing order
            processed_messages = []

            async def mock_handler(message, bot_user, model):
                processed_messages.append(message.content)
                # Add small delay to ensure sequential processing
                await asyncio.sleep(0.01)

            # Create mock messages
            messages = []
            for i in range(3):
                mock_message = MagicMock()
                mock_message.content = f"Message {i}"
                mock_message.author = MagicMock()
                mock_message.author.__str__ = MagicMock(
                    return_value=f"User{i}")
                mock_message.channel = MagicMock()
                mock_message.channel.__str__ = MagicMock(
                    return_value=f"Channel{i}")
                messages.append(mock_message)

            mock_bot_user = MagicMock()

            # Queue all messages
            for i, message in enumerate(messages):
                result = await self.queue_service.queue_mention(message, mock_bot_user, f"model-{i}", mock_handler)
                self.assertTrue(result)

            # Wait for processing to complete
            await asyncio.sleep(0.1)

            # Stop the service
            await self.queue_service.stop()

            # Verify FIFO order
            self.assertEqual(len(processed_messages), 3)
            self.assertEqual(
                processed_messages, [
                    "Message 0", "Message 1", "Message 2"])

        self.loop.run_until_complete(run_test())

    def test_queue_overflow(self):
        """Test queue overflow handling."""

        async def run_test():
            # Start the service
            await self.queue_service.start()

            # Wait a moment for the service to fully start
            await asyncio.sleep(0.01)

            # Create a slow handler to fill up the queue
            async def slow_handler(message, bot_user, model):
                await asyncio.sleep(0.5)  # Slow but not too slow for tests

            mock_bot_user = MagicMock()

            # Queue messages rapidly to fill up the queue
            results = []
            for i in range(6):  # Try to queue more than max (3)
                mock_message = MagicMock()
                mock_message.content = f"Message {i}"
                mock_message.author = MagicMock()
                mock_message.author.__str__ = MagicMock(
                    return_value=f"User{i}")
                mock_message.channel = MagicMock()
                mock_message.channel.__str__ = MagicMock(
                    return_value=f"Channel{i}")

                result = await self.queue_service.queue_mention(mock_message, mock_bot_user, f"model-{i}", slow_handler)
                results.append(result)

            # Should have some failures due to queue overflow
            successful_queues = sum(results)
            total_attempts = len(results)

            # We should have at least one failure
            self.assertLess(successful_queues, total_attempts)

            # Check that we have queue overflow stats
            stats = self.queue_service.get_stats()
            self.assertGreaterEqual(stats["queue_overflows"], 1)

            # Stop the service
            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_error_handling_in_processing(self):
        """Test error handling during message processing."""

        async def run_test():
            # Start the service
            await self.queue_service.start()

            # Wait for service to be fully ready
            await asyncio.sleep(0.01)

            # Create a handler that raises an exception
            async def failing_handler(message, bot_user, model):
                raise ValueError("Test error")

            mock_message = MagicMock()
            mock_message.content = "Test message"
            mock_message.author = MagicMock()
            mock_message.author.__str__ = MagicMock(return_value="TestUser")
            mock_message.channel = MagicMock()
            mock_message.channel.__str__ = MagicMock(
                return_value="TestChannel")
            mock_bot_user = MagicMock()

            # Queue a message that will fail
            result = await self.queue_service.queue_mention(mock_message, mock_bot_user, "test-model", failing_handler)
            self.assertTrue(result)

            # Wait for processing
            await asyncio.sleep(0.1)

            # Check that error was recorded in stats
            stats = self.queue_service.get_stats()
            self.assertGreater(stats["messages_failed"], 0)

            # Stop the service
            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_concurrent_queueing(self):
        """Test concurrent message queueing."""

        async def run_test():
            # Start the service
            await self.queue_service.start()

            # Wait for service to be fully ready
            await asyncio.sleep(0.01)

            processed_messages = []

            async def tracking_handler(message, bot_user, model):
                processed_messages.append(message.content)
                await asyncio.sleep(0.01)

            mock_bot_user = MagicMock()

            # Create multiple tasks that queue messages concurrently
            async def queue_message(i):
                mock_message = MagicMock()
                mock_message.content = f"Concurrent message {i}"
                mock_message.author = MagicMock()
                mock_message.author.__str__ = MagicMock(
                    return_value=f"User{i}")
                mock_message.channel = MagicMock()
                mock_message.channel.__str__ = MagicMock(
                    return_value=f"Channel{i}")

                return await self.queue_service.queue_mention(
                    mock_message, mock_bot_user, f"model-{i}", tracking_handler
                )

            # Queue 3 messages concurrently (same as max queue size)
            tasks = [queue_message(i) for i in range(3)]
            results = await asyncio.gather(*tasks)

            # All should succeed since we're not exceeding queue size
            self.assertTrue(all(results))

            # Wait for processing
            await asyncio.sleep(0.2)

            # All messages should be processed
            self.assertEqual(len(processed_messages), 3)

            # Stop the service
            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_stats_tracking(self):
        """Test statistics tracking."""

        async def run_test():
            # Start the service
            await self.queue_service.start()

            # Wait for service to be fully ready
            await asyncio.sleep(0.01)

            # Create handlers - one successful, one failing
            async def success_handler(message, bot_user, model):
                pass

            async def fail_handler(message, bot_user, model):
                raise RuntimeError("Test failure")

            mock_bot_user = MagicMock()

            # Queue successful message
            mock_message1 = MagicMock()
            mock_message1.author = MagicMock()
            mock_message1.author.__str__ = MagicMock(
                return_value="SuccessUser")
            mock_message1.channel = MagicMock()
            mock_message1.channel.__str__ = MagicMock(
                return_value="SuccessChannel")
            await self.queue_service.queue_mention(mock_message1, mock_bot_user, "model1", success_handler)

            # Queue failing message
            mock_message2 = MagicMock()
            mock_message2.author = MagicMock()
            mock_message2.author.__str__ = MagicMock(return_value="FailUser")
            mock_message2.channel = MagicMock()
            mock_message2.channel.__str__ = MagicMock(
                return_value="FailChannel")
            await self.queue_service.queue_mention(mock_message2, mock_bot_user, "model2", fail_handler)

            # Wait for processing
            await asyncio.sleep(0.1)

            # Check stats
            stats = self.queue_service.get_stats()
            self.assertEqual(stats["messages_processed"], 1)
            self.assertEqual(stats["messages_failed"], 1)

            # Stop the service
            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_queue_command(self):
        """Test queuing commands."""

        async def run_test():
            # Start the service
            await self.queue_service.start()

            # Wait for service to be fully ready
            await asyncio.sleep(0.01)

            processed_commands = []

            async def mock_command_handler(interaction, *args, **kwargs):
                processed_commands.append(
                    (interaction.user.name, args, kwargs))
                await asyncio.sleep(0.01)

            # Create mock interaction
            mock_interaction = MagicMock()
            mock_interaction.user = MagicMock()
            mock_interaction.user.name = "TestUser"
            mock_interaction.user.__str__ = MagicMock(return_value="TestUser")
            mock_interaction.channel = MagicMock()
            mock_interaction.channel.__str__ = MagicMock(
                return_value="TestChannel")

            # Queue a command
            result = await self.queue_service.queue_command(
                mock_interaction, mock_command_handler, "arg1", "arg2", kwarg1="value1"
            )

            self.assertTrue(result)

            # Wait for processing
            await asyncio.sleep(0.1)

            # Verify command was processed
            self.assertEqual(len(processed_commands), 1)
            user, args, kwargs = processed_commands[0]
            self.assertEqual(user, "TestUser")
            self.assertEqual(args, ("arg1", "arg2"))
            self.assertEqual(kwargs, {"kwarg1": "value1"})

            # Stop the service
            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_mixed_message_types(self):
        """Test processing both mentions and commands in FIFO order."""

        async def run_test():
            # Start the service
            await self.queue_service.start()

            # Wait for service to be fully ready
            await asyncio.sleep(0.01)

            processed_items = []

            async def mock_mention_handler(message, bot_user, model):
                processed_items.append(f"mention:{message.content}")
                await asyncio.sleep(0.01)

            async def mock_command_handler(interaction, command_name):
                processed_items.append(f"command:{command_name}")
                await asyncio.sleep(0.01)

            # Create mock objects
            mock_message = MagicMock()
            mock_message.content = "Hello bot"
            mock_message.author = MagicMock()
            mock_message.author.__str__ = MagicMock(return_value="User1")
            mock_message.channel = MagicMock()
            mock_message.channel.__str__ = MagicMock(return_value="Channel1")

            mock_interaction = MagicMock()
            mock_interaction.user = MagicMock()
            mock_interaction.user.__str__ = MagicMock(return_value="User2")
            mock_interaction.channel = MagicMock()
            mock_interaction.channel.__str__ = MagicMock(
                return_value="Channel2")

            mock_bot_user = MagicMock()

            # Queue mention, then command, then mention
            result1 = await self.queue_service.queue_mention(
                mock_message, mock_bot_user, "test-model", mock_mention_handler
            )
            result2 = await self.queue_service.queue_command(mock_interaction, mock_command_handler, "test_command")

            mock_message2 = MagicMock()
            mock_message2.content = "Second message"
            mock_message2.author = MagicMock()
            mock_message2.author.__str__ = MagicMock(return_value="User3")
            mock_message2.channel = MagicMock()
            mock_message2.channel.__str__ = MagicMock(return_value="Channel3")

            result3 = await self.queue_service.queue_mention(
                mock_message2, mock_bot_user, "test-model", mock_mention_handler
            )

            self.assertTrue(all([result1, result2, result3]))

            # Wait for processing
            await asyncio.sleep(0.2)

            # Verify FIFO order
            self.assertEqual(len(processed_items), 3)
            self.assertEqual(processed_items[0], "mention:Hello bot")
            self.assertEqual(processed_items[1], "command:test_command")
            self.assertEqual(processed_items[2], "mention:Second message")

            # Stop the service
            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())


if __name__ == "__main__":
    unittest.main()
