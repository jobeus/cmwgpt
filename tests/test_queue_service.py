"""
Unit tests for the QueueService class.
Tests per-channel FIFO message queue functionality.
"""

from src.services.queue_service import QueueService
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestQueueService(unittest.TestCase):
    """Test per-channel FIFO message queue functionality."""

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

    def _make_mock_message(self, content="Test", author="User1",
                           channel_name="Channel1", channel_id=100):
        """Helper to create a mock Discord message with channel.id."""
        mock_message = MagicMock()
        mock_message.content = content
        mock_message.author = MagicMock()
        mock_message.author.__str__ = MagicMock(return_value=author)
        mock_message.channel = MagicMock()
        mock_message.channel.__str__ = MagicMock(return_value=channel_name)
        mock_message.channel.id = channel_id
        return mock_message

    def _make_mock_interaction(self, user="User1",
                               channel_name="Channel1", channel_id=100):
        """Helper to create a mock Discord interaction with channel.id."""
        mock_interaction = MagicMock()
        mock_interaction.user = MagicMock()
        mock_interaction.user.name = user
        mock_interaction.user.__str__ = MagicMock(return_value=user)
        mock_interaction.channel = MagicMock()
        mock_interaction.channel.__str__ = MagicMock(
            return_value=channel_name)
        mock_interaction.channel.id = channel_id
        return mock_interaction

    def test_initialization(self):
        """Test queue service initialization."""
        self.assertFalse(self.queue_service.is_running())
        self.assertEqual(self.queue_service.get_queue_size(), 0)

        stats = self.queue_service.get_stats()
        self.assertFalse(stats["is_running"])
        self.assertEqual(stats["total_queue_size"], 0)
        self.assertEqual(stats["active_channels"], 0)
        self.assertEqual(stats["max_queue_size_per_channel"], 3)
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
            mock_message = self._make_mock_message()
            mock_bot_user = MagicMock()
            mock_handler = AsyncMock()

            result = await self.queue_service.queue_mention(
                mock_message, mock_bot_user, "test-model", mock_handler)
            self.assertFalse(result)

        self.loop.run_until_complete(run_test())

    def test_fifo_message_processing_same_channel(self):
        """Test that messages are processed in FIFO order within the same channel."""

        async def run_test():
            await self.queue_service.start()
            await asyncio.sleep(0.01)

            processed_messages = []

            async def mock_handler(message, bot_user, model):
                processed_messages.append(message.content)
                await asyncio.sleep(0.01)

            mock_bot_user = MagicMock()

            # Queue 3 messages in the same channel
            for i in range(3):
                mock_message = self._make_mock_message(
                    content=f"Message {i}",
                    author=f"User{i}",
                    channel_id=100  # Same channel
                )
                result = await self.queue_service.queue_mention(
                    mock_message, mock_bot_user, f"model-{i}", mock_handler)
                self.assertTrue(result)

            await asyncio.sleep(0.2)
            await self.queue_service.stop()

            # Verify FIFO order within the channel
            self.assertEqual(len(processed_messages), 3)
            self.assertEqual(
                processed_messages, [
                    "Message 0", "Message 1", "Message 2"])

        self.loop.run_until_complete(run_test())

    def test_cross_channel_concurrent_processing(self):
        """Test that different channels process concurrently."""

        async def run_test():
            await self.queue_service.start()
            await asyncio.sleep(0.01)

            processing_log = []

            async def slow_handler(message, bot_user, model):
                channel_id = message.channel.id
                processing_log.append(f"start:{channel_id}")
                await asyncio.sleep(0.1)
                processing_log.append(f"end:{channel_id}")

            mock_bot_user = MagicMock()

            # Queue one message in channel A and one in channel B
            msg_a = self._make_mock_message(
                content="ChannelA msg", channel_id=100)
            msg_b = self._make_mock_message(
                content="ChannelB msg", channel_id=200)

            await self.queue_service.queue_mention(
                msg_a, mock_bot_user, "model", slow_handler)
            await self.queue_service.queue_mention(
                msg_b, mock_bot_user, "model", slow_handler)

            # Wait for processing
            await asyncio.sleep(0.3)
            await self.queue_service.stop()

            # Both channels should have started before either finished
            # (concurrent processing)
            self.assertEqual(len(processing_log), 4)
            start_indices = [
                i for i,
                v in enumerate(processing_log) if v.startswith("start:")]
            end_indices = [
                i for i,
                v in enumerate(processing_log) if v.startswith("end:")]

            # Both starts should come before any end
            self.assertTrue(max(start_indices) < min(end_indices),
                            f"Expected concurrent processing but got: {processing_log}")

        self.loop.run_until_complete(run_test())

    def test_queue_overflow_per_channel(self):
        """Test queue overflow handling per channel."""

        async def run_test():
            await self.queue_service.start()
            await asyncio.sleep(0.01)

            async def slow_handler(message, bot_user, model):
                await asyncio.sleep(0.5)

            mock_bot_user = MagicMock()

            # Queue messages rapidly in the same channel to fill up
            results = []
            for i in range(6):  # Try to queue more than max (3)
                mock_message = self._make_mock_message(
                    content=f"Message {i}",
                    author=f"User{i}",
                    channel_id=100  # Same channel
                )
                result = await self.queue_service.queue_mention(
                    mock_message, mock_bot_user, f"model-{i}", slow_handler)
                results.append(result)

            # Should have some failures due to channel queue overflow
            successful_queues = sum(results)
            total_attempts = len(results)
            self.assertLess(successful_queues, total_attempts)

            stats = self.queue_service.get_stats()
            self.assertGreaterEqual(stats["queue_overflows"], 1)

            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_error_handling_in_processing(self):
        """Test error handling during message processing."""

        async def run_test():
            await self.queue_service.start()
            await asyncio.sleep(0.01)

            async def failing_handler(message, bot_user, model):
                raise ValueError("Test error")

            mock_message = self._make_mock_message()
            mock_bot_user = MagicMock()

            result = await self.queue_service.queue_mention(
                mock_message, mock_bot_user, "test-model", failing_handler)
            self.assertTrue(result)

            await asyncio.sleep(0.1)

            stats = self.queue_service.get_stats()
            self.assertGreater(stats["messages_failed"], 0)

            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_concurrent_queueing(self):
        """Test concurrent message queueing in the same channel."""

        async def run_test():
            await self.queue_service.start()
            await asyncio.sleep(0.01)

            processed_messages = []

            async def tracking_handler(message, bot_user, model):
                processed_messages.append(message.content)
                await asyncio.sleep(0.01)

            mock_bot_user = MagicMock()

            async def queue_message(i):
                mock_message = self._make_mock_message(
                    content=f"Concurrent message {i}",
                    author=f"User{i}",
                    channel_id=100  # Same channel
                )
                return await self.queue_service.queue_mention(
                    mock_message, mock_bot_user, f"model-{i}",
                    tracking_handler
                )

            # Queue 3 messages concurrently (same as max queue size)
            tasks = [queue_message(i) for i in range(3)]
            results = await asyncio.gather(*tasks)

            self.assertTrue(all(results))

            await asyncio.sleep(0.2)
            self.assertEqual(len(processed_messages), 3)

            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_stats_tracking(self):
        """Test statistics tracking."""

        async def run_test():
            await self.queue_service.start()
            await asyncio.sleep(0.01)

            async def success_handler(message, bot_user, model):
                pass

            async def fail_handler(message, bot_user, model):
                raise RuntimeError("Test failure")

            mock_bot_user = MagicMock()

            # Queue successful message
            mock_message1 = self._make_mock_message(
                content="Success", channel_id=100)
            await self.queue_service.queue_mention(
                mock_message1, mock_bot_user, "model1", success_handler)

            # Queue failing message (same channel, processed sequentially)
            mock_message2 = self._make_mock_message(
                content="Fail", channel_id=100)
            await self.queue_service.queue_mention(
                mock_message2, mock_bot_user, "model2", fail_handler)

            await asyncio.sleep(0.1)

            stats = self.queue_service.get_stats()
            self.assertEqual(stats["messages_processed"], 1)
            self.assertEqual(stats["messages_failed"], 1)

            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_queue_command(self):
        """Test queuing commands."""

        async def run_test():
            await self.queue_service.start()
            await asyncio.sleep(0.01)

            processed_commands = []

            async def mock_command_handler(interaction, *args, **kwargs):
                processed_commands.append(
                    (interaction.user.name, args, kwargs))
                await asyncio.sleep(0.01)

            mock_interaction = self._make_mock_interaction(
                user="TestUser", channel_id=100)

            result = await self.queue_service.queue_command(
                mock_interaction, mock_command_handler,
                "arg1", "arg2", kwarg1="value1"
            )

            self.assertTrue(result)

            await asyncio.sleep(0.1)

            self.assertEqual(len(processed_commands), 1)
            user, args, kwargs = processed_commands[0]
            self.assertEqual(user, "TestUser")
            self.assertEqual(args, ("arg1", "arg2"))
            self.assertEqual(kwargs, {"kwarg1": "value1"})

            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_mixed_message_types_same_channel(self):
        """Test processing both mentions and commands in FIFO order within a channel."""

        async def run_test():
            await self.queue_service.start()
            await asyncio.sleep(0.01)

            processed_items = []

            async def mock_mention_handler(message, bot_user, model):
                processed_items.append(f"mention:{message.content}")
                await asyncio.sleep(0.01)

            async def mock_command_handler(interaction, command_name):
                processed_items.append(f"command:{command_name}")
                await asyncio.sleep(0.01)

            channel_id = 100  # Same channel for all

            mock_message = self._make_mock_message(
                content="Hello bot", channel_id=channel_id)
            mock_interaction = self._make_mock_interaction(
                channel_id=channel_id)
            mock_bot_user = MagicMock()

            # Queue mention, then command, then mention (all same channel)
            result1 = await self.queue_service.queue_mention(
                mock_message, mock_bot_user, "test-model",
                mock_mention_handler
            )
            result2 = await self.queue_service.queue_command(
                mock_interaction, mock_command_handler, "test_command")

            mock_message2 = self._make_mock_message(
                content="Second message", channel_id=channel_id)
            result3 = await self.queue_service.queue_mention(
                mock_message2, mock_bot_user, "test-model",
                mock_mention_handler
            )

            self.assertTrue(all([result1, result2, result3]))

            await asyncio.sleep(0.2)

            # Verify FIFO order within the channel
            self.assertEqual(len(processed_items), 3)
            self.assertEqual(processed_items[0], "mention:Hello bot")
            self.assertEqual(processed_items[1], "command:test_command")
            self.assertEqual(processed_items[2], "mention:Second message")

            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_channel_queue_size(self):
        """Test per-channel queue size reporting."""

        async def run_test():
            await self.queue_service.start()
            await asyncio.sleep(0.01)

            async def slow_handler(message, bot_user, model):
                await asyncio.sleep(1.0)

            mock_bot_user = MagicMock()

            # Queue messages in two different channels
            msg1 = self._make_mock_message(content="A1", channel_id=100)
            msg2 = self._make_mock_message(content="A2", channel_id=100)
            msg3 = self._make_mock_message(content="B1", channel_id=200)

            await self.queue_service.queue_mention(
                msg1, mock_bot_user, "m", slow_handler)
            await asyncio.sleep(0.01)  # Let it start processing msg1
            await self.queue_service.queue_mention(
                msg2, mock_bot_user, "m", slow_handler)
            await self.queue_service.queue_mention(
                msg3, mock_bot_user, "m", slow_handler)

            await asyncio.sleep(0.05)

            # Channel 100 should have 1 queued (msg2 waiting, msg1 processing)
            # Channel 200 should have 0 queued (msg3 processing)
            ch100_size = self.queue_service.get_channel_queue_size(100)
            ch200_size = self.queue_service.get_channel_queue_size(200)

            self.assertGreaterEqual(ch100_size, 0)
            self.assertGreaterEqual(ch200_size, 0)

            # Total should be sum
            total = self.queue_service.get_queue_size()
            self.assertEqual(total, ch100_size + ch200_size)

            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_queue_command_not_running_and_overflow(self):
        async def run_test():
            interaction = self._make_mock_interaction()
            handler = AsyncMock()
            self.assertFalse(await self.queue_service.queue_command(interaction, handler))

            await self.queue_service.start()
            queue = asyncio.Queue(maxsize=3)
            for _ in range(3):
                queue.put_nowait(MagicMock())
            with patch.object(self.queue_service, "_ensure_channel_worker", return_value=queue):
                self.assertFalse(await self.queue_service.queue_command(interaction, handler))
            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_queue_restart_success_not_running_and_overflow(self):
        async def run_test():
            handler = AsyncMock()
            self.assertFalse(await self.queue_service.queue_restart(handler))

            await self.queue_service.start()
            self.assertTrue(await self.queue_service.queue_restart(handler))

            queue = asyncio.Queue(maxsize=3)
            for _ in range(3):
                queue.put_nowait(MagicMock())
            with patch.object(self.queue_service, "_ensure_channel_worker", return_value=queue):
                self.assertFalse(await self.queue_service.queue_restart(handler))
            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_stop_cancels_workers_after_timeout(self):
        async def run_test():
            await self.queue_service.start()
            worker = MagicMock()
            worker.cancel = MagicMock()
            self.queue_service._channel_workers = {100: worker}
            self.queue_service._channel_queues = {100: asyncio.Queue(maxsize=3)}

            def fake_gather(*args, **kwargs):
                fut = asyncio.get_event_loop().create_future()
                fut.set_result([])
                return fut

            with patch("src.services.queue_service.asyncio.wait_for", side_effect=asyncio.TimeoutError()), patch(
                "src.services.queue_service.asyncio.gather", side_effect=fake_gather
            ):
                await self.queue_service.stop()

            worker.cancel.assert_called_once_with()

        self.loop.run_until_complete(run_test())

    def test_process_channel_messages_handles_restart_runtime_error_and_system_exit(self):
        async def run_test():
            from src.services.queue_service import MessageType, QueuedMessage

            await self.queue_service.start()
            queue = asyncio.Queue()
            self.queue_service._channel_queues[100] = queue

            restart_handler = AsyncMock()
            await queue.put(QueuedMessage(
                message_type=MessageType.RESTART,
                handler=restart_handler,
                timestamp=0.0,
                channel_id=100,
            ))
            await queue.put(QueuedMessage(
                message_type=MessageType.SHUTDOWN,
                handler=None,
                timestamp=0.0,
                channel_id=100,
            ))
            await self.queue_service._process_channel_messages(100)
            restart_handler.assert_awaited_once()

            self.queue_service._is_running = True
            queue = asyncio.Queue()
            self.queue_service._channel_queues[200] = queue
            await queue.put(MagicMock())
            await queue.put(QueuedMessage(
                message_type=MessageType.SHUTDOWN,
                handler=None,
                timestamp=0.0,
                channel_id=200,
            ))
            with patch.object(self.queue_service, "_handle_queued_message", new=AsyncMock(side_effect=RuntimeError("boom"))):
                await self.queue_service._process_channel_messages(200)
            self.assertGreater(self.queue_service.get_stats()["messages_failed"], 0)

            self.queue_service._is_running = True
            queue = asyncio.Queue()
            self.queue_service._channel_queues[300] = queue
            await queue.put(MagicMock())
            await queue.put(QueuedMessage(
                message_type=MessageType.SHUTDOWN,
                handler=None,
                timestamp=0.0,
                channel_id=300,
            ))

            def fake_exit(code):
                self.queue_service._is_running = False

            with patch.object(self.queue_service, "_handle_queued_message", new=AsyncMock(side_effect=SystemExit(5))), patch(
                "src.services.queue_service.os._exit", side_effect=fake_exit
            ) as mock_exit:
                await self.queue_service._process_channel_messages(300)
            mock_exit.assert_called_once_with(5)

        self.loop.run_until_complete(run_test())

    def test_unexpected_exception_does_not_kill_worker(self):
        """An exception type outside the old catch-list must not kill the channel worker."""

        async def run_test():
            await self.queue_service.start()
            await asyncio.sleep(0.01)

            processed_messages = []

            async def unexpected_failure_handler(message, bot_user, model):
                raise KeyError("totally unexpected")

            async def tracking_handler(message, bot_user, model):
                processed_messages.append(message.content)

            mock_bot_user = MagicMock()

            failing_msg = self._make_mock_message(
                content="Boom", channel_id=100)
            self.assertTrue(await self.queue_service.queue_mention(
                failing_msg, mock_bot_user, "model", unexpected_failure_handler))

            follow_up_msg = self._make_mock_message(
                content="Still alive", channel_id=100)
            self.assertTrue(await self.queue_service.queue_mention(
                follow_up_msg, mock_bot_user, "model", tracking_handler))

            await asyncio.sleep(0.1)

            # The worker survived the unexpected exception and processed the
            # follow-up message.
            worker = self.queue_service._channel_workers[100]
            self.assertFalse(worker.done())
            self.assertEqual(processed_messages, ["Still alive"])
            self.assertGreaterEqual(
                self.queue_service.get_stats()["messages_failed"], 1)

            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_dead_worker_is_respawned(self):
        """A dead worker task must be respawned on the next queued message."""

        async def run_test():
            await self.queue_service.start()
            await asyncio.sleep(0.01)

            processed_messages = []

            async def tracking_handler(message, bot_user, model):
                processed_messages.append(message.content)

            mock_bot_user = MagicMock()

            first_msg = self._make_mock_message(content="First", channel_id=100)
            self.assertTrue(await self.queue_service.queue_mention(
                first_msg, mock_bot_user, "model", tracking_handler))
            await asyncio.sleep(0.05)

            # Kill the worker task to simulate an unrecoverable crash
            old_worker = self.queue_service._channel_workers[100]
            old_worker.cancel()
            await asyncio.gather(old_worker, return_exceptions=True)
            self.assertTrue(old_worker.done())

            # Queueing another message should respawn a fresh worker
            second_msg = self._make_mock_message(
                content="Second", channel_id=100)
            self.assertTrue(await self.queue_service.queue_mention(
                second_msg, mock_bot_user, "model", tracking_handler))
            await asyncio.sleep(0.05)

            new_worker = self.queue_service._channel_workers[100]
            self.assertIsNot(new_worker, old_worker)
            self.assertFalse(new_worker.done())
            self.assertEqual(processed_messages, ["First", "Second"])

            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())

    def test_handle_queued_message_restart_and_unknown_failure_branch(self):
        async def run_test():
            from src.services.queue_service import MessageType, QueuedMessage

            await self.queue_service.start()
            restart_handler = AsyncMock()
            restart_msg = QueuedMessage(
                message_type=MessageType.RESTART,
                handler=restart_handler,
                timestamp=0.0,
                channel_id=0,
            )
            await self.queue_service._handle_queued_message(restart_msg)
            restart_handler.assert_awaited_once()

            command_msg = QueuedMessage(
                message_type=MessageType.COMMAND,
                handler=AsyncMock(side_effect=TypeError("bad")),
                timestamp=0.0,
                channel_id=0,
                interaction=self._make_mock_interaction(),
            )
            await self.queue_service._handle_queued_message(command_msg)
            self.assertGreaterEqual(self.queue_service.get_stats()["messages_failed"], 1)
            await self.queue_service.stop()

        self.loop.run_until_complete(run_test())


if __name__ == "__main__":
    unittest.main()
