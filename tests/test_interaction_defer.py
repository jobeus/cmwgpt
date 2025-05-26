"""
Test Discord interaction deferring functionality.
Tests that slash commands properly defer interactions before queueing.
"""

from src.services.queue_service import QueueService
from src.bot.commands.image import ImageCommands
from src.bot.commands.system import SystemCommands
from src.bot.commands.chat import ChatCommands
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import discord.ext.commands first to ensure proper module loading


class TestInteractionDefer(unittest.TestCase):
    """Test Discord interaction deferring functionality."""

    def setUp(self):
        """Set up test environment."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Create mock bot
        self.mock_bot = MagicMock()

        # Create command instances
        self.chat_commands = ChatCommands(self.mock_bot)
        self.system_commands = SystemCommands(self.mock_bot)
        self.image_commands = ImageCommands(self.mock_bot)

        # Create a test queue service
        self.queue_service = QueueService(max_queue_size=3)

    def tearDown(self):
        """Clean up test environment."""
        # Ensure queue service is stopped
        if self.queue_service.is_running():
            self.loop.run_until_complete(self.queue_service.stop())
        self.loop.close()

    def test_chat_command_defers_interaction(self):
        """Test that /chat command properly defers the interaction."""

        async def run_test():
            # Create mock interaction
            mock_interaction = MagicMock()
            mock_interaction.response = AsyncMock()
            mock_interaction.followup = AsyncMock()
            mock_interaction.user = MagicMock()
            mock_interaction.user.__str__ = MagicMock(return_value="TestUser")
            mock_interaction.channel = MagicMock()
            mock_interaction.channel.__str__ = MagicMock(return_value="TestChannel")

            # Mock the queue service to return False (queue full)
            with patch("src.bot.commands.chat.queue_service") as mock_queue_service:
                mock_queue_service.queue_command = AsyncMock(return_value=False)

                # Create the chat command
                chat_command = self.chat_commands._create_chat_command()

                # Execute the command
                await chat_command.callback(mock_interaction, "test message")

                # Verify interaction was deferred first
                mock_interaction.response.defer.assert_called_once_with(ephemeral=False, thinking=True)

                # Verify queue_command was called
                mock_queue_service.queue_command.assert_called_once()

                # Verify followup was used (not response.send_message)
                mock_interaction.followup.send.assert_called_once()
                mock_interaction.response.send_message.assert_not_called()

        self.loop.run_until_complete(run_test())

    def test_model_command_defers_interaction(self):
        """Test that /model command properly defers the interaction."""

        async def run_test():
            # Create mock interaction
            mock_interaction = MagicMock()
            mock_interaction.response = AsyncMock()
            mock_interaction.followup = AsyncMock()
            mock_interaction.user = MagicMock()
            mock_interaction.user.__str__ = MagicMock(return_value="TestUser")
            mock_interaction.channel = MagicMock()
            mock_interaction.channel.__str__ = MagicMock(return_value="TestChannel")

            # Mock the queue service to return False (queue full)
            with patch("src.bot.commands.system.queue_service") as mock_queue_service:
                mock_queue_service.queue_command = AsyncMock(return_value=False)

                # Create the model command
                model_command = self.system_commands._create_model_command()

                # Execute the command
                await model_command.callback(mock_interaction, "gpt-4o-mini")

                # Verify interaction was deferred first
                mock_interaction.response.defer.assert_called_once_with(ephemeral=False, thinking=True)

                # Verify queue_command was called
                mock_queue_service.queue_command.assert_called_once()

                # Verify followup was used (not response.send_message)
                mock_interaction.followup.send.assert_called_once()
                mock_interaction.response.send_message.assert_not_called()

        self.loop.run_until_complete(run_test())

    def test_draw_command_defers_interaction(self):
        """Test that /draw command properly defers the interaction."""

        async def run_test():
            # Create mock interaction
            mock_interaction = MagicMock()
            mock_interaction.response = AsyncMock()
            mock_interaction.followup = AsyncMock()
            mock_interaction.user = MagicMock()
            mock_interaction.user.__str__ = MagicMock(return_value="TestUser")
            mock_interaction.channel = MagicMock()
            mock_interaction.channel.__str__ = MagicMock(return_value="TestChannel")

            # Mock the queue service to return False (queue full)
            with patch("src.bot.commands.image.queue_service") as mock_queue_service:
                mock_queue_service.queue_command = AsyncMock(return_value=False)

                # Create the draw command
                draw_command = self.image_commands._create_draw_command()

                # Execute the command
                await draw_command.callback(mock_interaction, "test prompt")

                # Verify interaction was deferred first
                mock_interaction.response.defer.assert_called_once_with(ephemeral=False, thinking=True)

                # Verify queue_command was called
                mock_queue_service.queue_command.assert_called_once()

                # Verify followup was used (not response.send_message)
                mock_interaction.followup.send.assert_called_once()
                mock_interaction.response.send_message.assert_not_called()

        self.loop.run_until_complete(run_test())

    def test_successful_queue_no_error_message(self):
        """Test that successful queueing doesn't send error messages."""

        async def run_test():
            # Create mock interaction
            mock_interaction = MagicMock()
            mock_interaction.response = AsyncMock()
            mock_interaction.followup = AsyncMock()
            mock_interaction.user = MagicMock()
            mock_interaction.user.__str__ = MagicMock(return_value="TestUser")
            mock_interaction.channel = MagicMock()
            mock_interaction.channel.__str__ = MagicMock(return_value="TestChannel")

            # Mock the queue service to return True (successful queue)
            with patch("src.bot.commands.chat.queue_service") as mock_queue_service:
                mock_queue_service.queue_command = AsyncMock(return_value=True)

                # Create the chat command
                chat_command = self.chat_commands._create_chat_command()

                # Execute the command
                await chat_command.callback(mock_interaction, "test message")

                # Verify interaction was deferred
                mock_interaction.response.defer.assert_called_once_with(ephemeral=False, thinking=True)

                # Verify queue_command was called
                mock_queue_service.queue_command.assert_called_once()

                # Verify no error message was sent
                mock_interaction.followup.send.assert_not_called()
                mock_interaction.response.send_message.assert_not_called()

        self.loop.run_until_complete(run_test())


if __name__ == "__main__":
    unittest.main()
