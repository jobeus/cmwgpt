"""
Test Discord interaction deferring functionality.
Tests that slash commands properly defer interactions before queueing.
"""

from src.bot.commands.image import ImageCommands
from src.bot.commands.system import SystemCommands
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
        self.mock_queue_service = MagicMock()
        self.mock_queue_service.queue_command = AsyncMock()
        self.mock_state_service = MagicMock()
        self.system_commands = SystemCommands(
            self.mock_bot,
            queue_service_instance=self.mock_queue_service,
            state_service_instance=self.mock_state_service,
        )
        self.image_commands = ImageCommands(
            self.mock_bot,
            state_service=MagicMock(),
            message_service=MagicMock(),
            runpod_service=MagicMock(),
            default_draw_model="seedream",
            default_edit_model="seedream",
            enable_runpod_models=False,
        )

    def tearDown(self):
        """Clean up test environment."""
        self.loop.close()

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
            mock_interaction.channel.__str__ = MagicMock(
                return_value="TestChannel")

            self.mock_queue_service.queue_command.return_value = False

            model_command = self.system_commands._create_model_command()

            # Execute the command
            await model_command.callback(mock_interaction, "gpt-5-mini")

            # Verify interaction was deferred first
            mock_interaction.response.defer.assert_called_once_with(
                ephemeral=False, thinking=True)

            # Verify queue_command was called
            self.mock_queue_service.queue_command.assert_called_once()

            # Verify followup was used (not response.send_message)
            mock_interaction.followup.send.assert_called_once()
            mock_interaction.response.send_message.assert_not_called()

        self.loop.run_until_complete(run_test())

    def test_draw_command_defers_interaction(self):
        """Test that /draw command properly defers the interaction (async, no queue)."""

        async def run_test():
            # Create mock interaction
            mock_interaction = MagicMock()
            mock_interaction.response = AsyncMock()
            mock_interaction.followup = AsyncMock()
            mock_interaction.user = MagicMock()
            mock_interaction.user.__str__ = MagicMock(return_value="TestUser")
            mock_interaction.channel = MagicMock()
            mock_interaction.channel.__str__ = MagicMock(
                return_value="TestChannel")

            with patch("src.bot.commands.image.asyncio.create_task") as mock_create_task:
                draw_command = self.image_commands._create_draw_command()

                # Execute the command — /draw is fire-and-forget (no queue)
                await draw_command.callback(mock_interaction, "test prompt")

                # Verify interaction was deferred first
                mock_interaction.response.defer.assert_called_once_with(
                    ephemeral=False, thinking=True)

                # Verify async execution was scheduled
                mock_create_task.assert_called_once()
                scheduled_coro = mock_create_task.call_args.args[0]
                scheduled_coro.close()

                mock_interaction.response.send_message.assert_not_called()

        self.loop.run_until_complete(run_test())


if __name__ == "__main__":
    unittest.main()
