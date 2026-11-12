"""
Unit tests for bot service functions.
Tests helper functions and logic that can be tested without Discord integration.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock
import asyncio
import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Add src directory for new architecture
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(
            os.path.dirname(
                os.path.abspath(__file__))),
        "src"))


class TestBotFunctions(unittest.TestCase):
    """Test bot service functions."""

    def setUp(self):
        """Set up test environment."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        from src.services.message_service import MessageService
        self._message_service = MessageService()

    def tearDown(self):
        """Clean up test environment."""
        self.loop.close()

    def _set_mock_paste_service(self, upload_mock):
        paste_service = MagicMock()
        paste_service.upload_markdown = upload_mock
        self._message_service._paste_service = paste_service

    def test_prepare_mention_context(self):
        """Test mention handler _prepare_mention_context function."""

        async def run_test():
            from src.bot.handlers.mention import MentionHandler

            # Mock message and bot user
            mock_message = MagicMock()
            mock_message.author = MagicMock()
            mock_message.author.id = 12345
            mock_message.id = 33333
            mock_message.content = "Hey @bot, can you help me?"
            mock_message.channel = MagicMock()
            mock_message.reference = None
            mock_message.attachments = []
            mock_message.embeds = []

            mock_bot_user = MagicMock()
            mock_bot_user.id = 99999

            # Mock channel history
            mock_msg1 = MagicMock()
            mock_msg1.author.id = 11111
            mock_msg1.id = 111111
            mock_msg1.content = "First message"
            mock_msg1.reference = None
            mock_msg1.attachments = []
            mock_msg1.embeds = []

            mock_msg2 = MagicMock()
            mock_msg2.author.id = 22222
            mock_msg2.id = 222222
            mock_msg2.content = "Second message"
            mock_msg2.reference = None
            mock_msg2.attachments = []
            mock_msg2.embeds = []

            mock_msg3 = mock_message  # The mention message

            # Mock async iterator for channel history
            async def mock_history(limit):
                # Return in reverse order (newest first)
                for msg in [mock_msg3, mock_msg2, mock_msg1]:
                    yield msg

            mock_message.channel.history = mock_history

            mock_state_service = MagicMock()
            mock_state_service.get_system_prompt.return_value = None

            async def mock_mention_legend(channel, bot_user):
                return "Legend: @user1 = <@11111>"

            mention_handler = MentionHandler(
                state_service=mock_state_service,
                message_service=MagicMock(),
                queue_service=MagicMock(),
                system_prompt_loader=lambda: "Default system prompt",
                include_num_chatlines=3,
                mention_legend_provider=mock_mention_legend,
            )

            result, system_prompt = await mention_handler._prepare_mention_context(
                mock_message, mock_bot_user
            )

            # Verify result structure
            self.assertIsInstance(result, list)
            self.assertIsInstance(system_prompt, str)

            # Now we should have 3 messages in the result array (for the 3 mock
            # messages)
            self.assertEqual(len(result), 3)

            # Verify system prompt
            # The system prompt should contain the mocked legend and bot ID
            self.assertIn("Legend: @user1 = <@11111>", system_prompt)
            # Bot ID should be in system prompt
            self.assertIn("<@99999>", system_prompt)
            # Should contain some system prompt content (either default or from
            # file)
            self.assertTrue(len(system_prompt) > 0)

            # The system prompt should contain the preamble about discord IDs
            self.assertIn(
                "(timestamp, message ID, sender's Discord ID)",
                system_prompt)

            # Verify history is in correct order (oldest first)
            # Msg 1
            self.assertEqual(result[0]["role"], "user")

            # Msg 2
            self.assertEqual(result[1]["role"], "user")

            # Msg 3
            self.assertEqual(result[2]["role"], "user")

        self.loop.run_until_complete(run_test())

    def test_send_channel_reply_short_message(self):
        """Test message service send_channel_reply with short message."""

        async def run_test():
            message_service = self._message_service
            mock_upload = AsyncMock()
            self._set_mock_paste_service(mock_upload)

            # Mock channel
            mock_channel = AsyncMock()

            # Test short message
            short_message = "This is a short reply."

            await message_service.send_channel_reply(mock_channel, short_message)

            # Verify direct send was called
            mock_channel.send.assert_called_once_with(
                content=short_message, files=[], suppress_embeds=True)
            mock_upload.assert_not_called()

        self.loop.run_until_complete(run_test())

    def test_send_channel_reply_long_message(self):
        """Test message service send_channel_reply with long message that needs pasting."""

        async def run_test():
            message_service = self._message_service
            mock_upload = AsyncMock(return_value="https://paste.jobe.wtf/abc123.md")
            self._set_mock_paste_service(mock_upload)

            # Mock channel
            mock_channel = AsyncMock()

            # Test long message (over 2000 characters)
            long_message = "A" * 2500

            await message_service.send_channel_reply(mock_channel, long_message)

            # Verify upload was called
            mock_upload.assert_called_once_with(long_message)

            # Verify channel send was called with paste URL
            expected_message = (
                "My response was too long to post here, so I've uploaded it to: https://paste.jobe.wtf/abc123.md"
            )
            mock_channel.send.assert_called_once_with(
                content=expected_message, files=[], suppress_embeds=True)

        self.loop.run_until_complete(run_test())

    def test_send_channel_reply_upload_error(self):
        """Test message service send_channel_reply when paste upload fails."""

        async def run_test():
            message_service = self._message_service
            mock_upload = AsyncMock(side_effect=Exception("Upload failed"))
            self._set_mock_paste_service(mock_upload)

            # Mock channel
            mock_channel = AsyncMock()

            # Test long message
            long_message = "B" * 2500

            await message_service.send_channel_reply(mock_channel, long_message)

            # Verify upload was attempted
            mock_upload.assert_called_once_with(long_message)

            # Verify error message was sent
            expected_message = (
                "The content of my response was over 2000 characters "
                "(discord limit), and there was a problem uploading it to paste service. "
                "Sorry, try again later.")
            mock_channel.send.assert_called_once_with(
                content=expected_message, files=[], suppress_embeds=True)

        self.loop.run_until_complete(run_test())



if __name__ == "__main__":
    unittest.main()
