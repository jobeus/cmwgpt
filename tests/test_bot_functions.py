"""
Unit tests for bot service functions.
Tests helper functions and logic that can be tested without Discord integration.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import json
import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Add src directory for new architecture
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


class TestBotFunctions(unittest.TestCase):
    """Test bot service functions."""

    def setUp(self):
        """Set up test environment."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        """Clean up test environment."""
        self.loop.close()

    @patch("src.services.openai_service.openai_service.get_chat_completion")
    @patch("src.utils.discord_helper.get_mention_legend")
    def test_prepare_mention_context(self, mock_get_legend, mock_get_chat):
        """Test mention handler _prepare_mention_context function."""

        async def run_test():
            # Import the function we want to test
            from src.bot.handlers.mention import mention_handler

            # Mock message and bot user
            mock_message = MagicMock()
            mock_message.author = MagicMock()
            mock_message.author.id = 12345
            mock_message.content = "Hey @bot, can you help me?"
            mock_message.channel = MagicMock()

            mock_bot_user = MagicMock()
            mock_bot_user.id = 99999

            # Mock channel history
            mock_msg1 = MagicMock()
            mock_msg1.author.id = 11111
            mock_msg1.content = "First message"

            mock_msg2 = MagicMock()
            mock_msg2.author.id = 22222
            mock_msg2.content = "Second message"

            mock_msg3 = mock_message  # The mention message

            # Mock async iterator for channel history
            async def mock_history(limit):
                # Return in reverse order (newest first)
                for msg in [mock_msg3, mock_msg2, mock_msg1]:
                    yield msg

            mock_message.channel.history = mock_history

            # Mock get_mention_legend
            mock_get_legend.return_value = "Legend: @user1 = <@11111>"

            # Mock state service
            with patch("src.services.state_service.state_service") as mock_state_service:
                mock_state_service.get_system_prompt.return_value = None
                with patch("src.config.get_system_prompt", return_value="Default system prompt"):
                    with patch("src.config.INCLUDE_NUM_CHATLINES", 3):
                        # Call the function
                        result, system_prompt = await mention_handler._prepare_mention_context(
                            mock_message, mock_bot_user
                        )

            # Verify result structure
            self.assertIsInstance(result, list)
            self.assertIsInstance(system_prompt, str)
            # Only user message with history (no system message in result)
            self.assertEqual(len(result), 1)

            # Verify system prompt
            # The system prompt should contain the mocked legend and bot ID
            self.assertIn("Legend: @user1 = <@11111>", system_prompt)
            # Bot ID should be in system prompt
            self.assertIn("<@99999>", system_prompt)
            # Should contain some system prompt content (either default or from
            # file)
            self.assertTrue(len(system_prompt) > 0)

            # Verify user message with history
            self.assertEqual(result[0]["role"], "user")

            # Parse the JSON content to verify history structure
            content = result[0]["content"]
            self.assertIn("Conversation lines are below", content)

            # Extract JSON from content
            json_start = content.find("[")
            json_content = content[json_start:]
            history_data = json.loads(json_content)

            # Verify history is in correct order (oldest first)
            self.assertEqual(len(history_data), 3)
            self.assertEqual(history_data[0]["user"], "<@11111>")
            self.assertEqual(history_data[0]["says"], "First message")
            self.assertEqual(history_data[1]["user"], "<@22222>")
            self.assertEqual(history_data[1]["says"], "Second message")
            self.assertEqual(history_data[2]["user"], "<@12345>")
            self.assertEqual(history_data[2]["says"], "Hey @bot, can you help me?")

        self.loop.run_until_complete(run_test())

    @patch("src.services.paste_service.paste_service.upload_markdown")
    def test_send_channel_reply_short_message(self, mock_upload):
        """Test message service send_channel_reply with short message."""

        async def run_test():
            from src.services.message_service import message_service

            # Mock channel
            mock_channel = AsyncMock()

            # Test short message
            short_message = "This is a short reply."

            await message_service.send_channel_reply(mock_channel, short_message)

            # Verify direct send was called
            mock_channel.send.assert_called_once_with(short_message, suppress_embeds=True)
            mock_upload.assert_not_called()

        self.loop.run_until_complete(run_test())

    @patch("src.services.paste_service.paste_service.upload_markdown")
    def test_send_channel_reply_long_message(self, mock_upload):
        """Test message service send_channel_reply with long message that needs pasting."""

        async def run_test():
            from src.services.message_service import message_service

            # Mock channel
            mock_channel = AsyncMock()

            # Mock pasters upload
            mock_upload.return_value = "https://paste.rs/abc123.md"

            # Test long message (over 2000 characters)
            long_message = "A" * 2500

            await message_service.send_channel_reply(mock_channel, long_message)

            # Verify upload was called
            mock_upload.assert_called_once_with(long_message)

            # Verify channel send was called with paste URL
            expected_message = (
                "My response was too long to post here, so I've uploaded it to: https://paste.rs/abc123.md"
            )
            mock_channel.send.assert_called_once_with(expected_message, suppress_embeds=True)

        self.loop.run_until_complete(run_test())

    @patch("src.services.paste_service.paste_service.upload_markdown")
    def test_send_channel_reply_upload_error(self, mock_upload):
        """Test message service send_channel_reply when paste upload fails."""

        async def run_test():
            from src.services.message_service import message_service

            # Mock channel
            mock_channel = AsyncMock()

            # Mock pasters upload failure
            mock_upload.side_effect = Exception("Upload failed")

            # Test long message
            long_message = "B" * 2500

            await message_service.send_channel_reply(mock_channel, long_message)

            # Verify upload was attempted
            mock_upload.assert_called_once_with(long_message)

            # Verify error message was sent
            expected_message = (
                "The content of my response was over 2000 characters "
                "(discord limit), and there was a problem uploading it to paste service. "
                "Sorry, try again later."
            )
            mock_channel.send.assert_called_once_with(expected_message, suppress_embeds=True)

        self.loop.run_until_complete(run_test())

    @patch("src.services.paste_service.paste_service.upload_markdown")
    def test_send_interaction_followup_short_message(self, mock_upload):
        """Test message service send_interaction_followup with short message."""

        async def run_test():
            from src.services.message_service import message_service

            # Mock interaction
            mock_interaction = MagicMock()
            mock_interaction.followup = AsyncMock()

            # Test parameters
            base_content = "> Test prompt"
            reply_text = "Short reply"

            await message_service.send_interaction_followup(mock_interaction, base_content, reply_text)

            # Verify direct followup was called
            expected_content = f"{base_content}\n{reply_text}"
            mock_interaction.followup.send.assert_called_once_with(content=expected_content, suppress_embeds=True)
            mock_upload.assert_not_called()

        self.loop.run_until_complete(run_test())

    @patch("src.services.paste_service.paste_service.upload_markdown")
    def test_send_interaction_followup_long_message(self, mock_upload):
        """Test message service send_interaction_followup with long message that needs pasting."""

        async def run_test():
            from src.services.message_service import message_service

            # Mock interaction
            mock_interaction = MagicMock()
            mock_interaction.followup = AsyncMock()

            # Mock pasters upload
            mock_upload.return_value = "https://paste.rs/xyz789.md"

            # Test parameters
            base_content = "> Test prompt"
            reply_text = "C" * 2500  # Long reply that would exceed 2000 chars with base_content

            await message_service.send_interaction_followup(mock_interaction, base_content, reply_text)

            # Verify upload was called
            mock_upload.assert_called_once_with(reply_text)

            # Verify followup was called with paste URL
            expected_content = f"{base_content}\n\nMy detailed response was too long, so I've uploaded it here: https://paste.rs/xyz789.md"
            mock_interaction.followup.send.assert_called_once_with(content=expected_content, suppress_embeds=True)

        self.loop.run_until_complete(run_test())

    def test_message_length_calculation(self):
        """Test message length calculations for Discord limits."""
        # Test exact limit
        base_content = "> Test prompt"
        reply_text = "A" * (2000 - len(base_content) - 1)  # Exactly at limit
        total_length = len(base_content + f"\n{reply_text}")
        self.assertEqual(total_length, 2000)

        # Test over limit
        reply_text_over = "A" * (2000 - len(base_content))  # Over limit
        total_length_over = len(base_content + f"\n{reply_text_over}")
        self.assertGreater(total_length_over, 2000)

    def test_json_content_handling(self):
        """Test JSON content handling in conversations."""
        # Test that JSON serialization works correctly
        content_payload = [
            {"type": "input_text", "text": "Hello"},
            {"type": "input_image", "image_url": "http://example.com/image.jpg"},
        ]

        json_content = json.dumps(content_payload)
        parsed_content = json.loads(json_content)

        self.assertEqual(parsed_content, content_payload)
        self.assertEqual(parsed_content[0]["type"], "input_text")
        self.assertEqual(parsed_content[1]["type"], "input_image")

    def test_username_formatting(self):
        """Test username formatting for chat messages."""
        # Mock user
        mock_user = MagicMock()
        mock_user.display_name = "TestUser"

        # Test message formatting
        original_message = "Hello, how are you?"
        formatted_message = f"""{
            mock_user.display_name} says: {original_message}"""

        expected = "TestUser says: Hello, how are you?"
        self.assertEqual(formatted_message, expected)

    def test_attachment_url_formatting(self):
        """Test attachment URL formatting in messages."""
        from src.services.message_service import message_service

        # Mock attachment
        mock_attachment = MagicMock()
        mock_attachment.url = "https://cdn.discord.com/attachments/123/456/image.png"

        # Test formatting
        message = "Analyze this image"
        formatted = message_service.format_attachment_message(mock_attachment, message)

        expected = "https://cdn.discord.com/attachments/123/456/image.png\n> Analyze this image"
        self.assertEqual(formatted, expected)


if __name__ == "__main__":
    unittest.main()
