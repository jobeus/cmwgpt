"""
Unit tests for utils/discord_helper.py module.
Tests Discord utility functions.
"""

import unittest
from unittest.mock import MagicMock
import asyncio
import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.discord_helper import get_mention_legend  # noqa: E402


class TestDiscordHelper(unittest.TestCase):
    """Test discord_helper.py functionality."""

    def setUp(self):
        """Set up test environment."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        """Clean up test environment."""
        self.loop.close()

    def test_get_mention_legend_basic(self):
        """Test basic mention legend generation."""

        async def run_test():
            # Mock channel
            mock_channel = MagicMock()

            # Mock bot user
            mock_bot_user = MagicMock()
            mock_bot_user.id = 99999

            # Mock members
            mock_member1 = MagicMock()
            mock_member1.display_name = "Alice"
            mock_member1.id = 12345

            mock_member2 = MagicMock()
            mock_member2.display_name = "Bob"
            mock_member2.id = 67890

            # Mock channel.members (list, not async iterator)
            mock_channel.members = [mock_member1, mock_member2]

            # Test the function
            result = await get_mention_legend(mock_channel, mock_bot_user)

            # Verify result
            expected_lines = [
                "Here are all the users in this channel:",
                "You are <@99999>!",
                "@Alice = <@12345>",
                "@Bob = <@67890>",
                "Whenever you see a mention like <@discord_user_id>, map it back to the corresponding handle. "
                "If you want to @mention someone yourself use <@discord_user_id> instead of @nickname for discord "
                "to recoginize your intent.",
            ]
            expected = "\n".join(expected_lines)

            self.assertEqual(result, expected)

        self.loop.run_until_complete(run_test())

    def test_get_mention_legend_empty_guild(self):
        """Test mention legend with no members."""

        async def run_test():
            # Mock channel
            mock_channel = MagicMock()

            # Mock bot user
            mock_bot_user = MagicMock()
            mock_bot_user.id = 99999

            # Mock empty members list
            mock_channel.members = []

            # Test the function
            result = await get_mention_legend(mock_channel, mock_bot_user)

            # Verify result
            expected_lines = [
                "Here are all the users in this channel:",
                "You are <@99999>!",
                "Whenever you see a mention like <@discord_user_id>, map it back to the corresponding handle. "
                "If you want to @mention someone yourself use <@discord_user_id> instead of @nickname for discord "
                "to recoginize your intent.",
            ]
            expected = "\n".join(expected_lines)

            self.assertEqual(result, expected)

        self.loop.run_until_complete(run_test())

    def test_get_mention_legend_special_characters(self):
        """Test mention legend with special characters in names."""

        async def run_test():
            # Mock channel
            mock_channel = MagicMock()

            # Mock bot user
            mock_bot_user = MagicMock()
            mock_bot_user.id = 99999

            # Mock members with special characters
            mock_member1 = MagicMock()
            mock_member1.display_name = "User_123"
            mock_member1.id = 11111

            mock_member2 = MagicMock()
            mock_member2.display_name = "Test-User"
            mock_member2.id = 22222

            mock_member3 = MagicMock()
            mock_member3.display_name = "émoji🚀user"
            mock_member3.id = 33333

            # Mock channel.members
            mock_channel.members = [mock_member1, mock_member2, mock_member3]

            # Test the function
            result = await get_mention_legend(mock_channel, mock_bot_user)

            # Verify special characters are handled
            self.assertIn("You are <@99999>!", result)
            self.assertIn("@User_123 = <@11111>", result)
            self.assertIn("@Test-User = <@22222>", result)
            self.assertIn("@émoji🚀user = <@33333>", result)

        self.loop.run_until_complete(run_test())

    def test_get_mention_legend_large_guild(self):
        """Test mention legend with many members."""

        async def run_test():
            # Mock channel
            mock_channel = MagicMock()

            # Mock bot user
            mock_bot_user = MagicMock()
            mock_bot_user.id = 99999

            # Create many mock members
            members = []
            for i in range(100):
                mock_member = MagicMock()
                mock_member.display_name = f"User{i}"
                mock_member.id = 10000 + i
                members.append(mock_member)

            # Mock channel.members
            mock_channel.members = members

            # Test the function
            result = await get_mention_legend(mock_channel, mock_bot_user)

            # Verify bot identity is included
            self.assertIn("You are <@99999>!", result)

            # Verify all members are included
            for i in range(100):
                self.assertIn(f"@User{i} = <@{10000 + i}>", result)

            # Verify structure
            self.assertIn("Here are all the users in this channel:", result)
            self.assertIn("Whenever you see a mention like <@discord_user_id>", result)

        self.loop.run_until_complete(run_test())

    def test_get_mention_legend_duplicate_names(self):
        """Test mention legend with duplicate display names."""

        async def run_test():
            # Mock channel
            mock_channel = MagicMock()

            # Mock bot user
            mock_bot_user = MagicMock()
            mock_bot_user.id = 99999

            # Mock members with same display name but different IDs
            mock_member1 = MagicMock()
            mock_member1.display_name = "SameName"
            mock_member1.id = 11111

            mock_member2 = MagicMock()
            mock_member2.display_name = "SameName"
            mock_member2.id = 22222

            # Mock channel.members
            mock_channel.members = [mock_member1, mock_member2]

            # Test the function
            result = await get_mention_legend(mock_channel, mock_bot_user)

            # Verify bot identity is included
            self.assertIn("You are <@99999>!", result)

            # Verify both members are listed with their unique IDs
            self.assertIn("@SameName = <@11111>", result)
            self.assertIn("@SameName = <@22222>", result)

        self.loop.run_until_complete(run_test())

    def test_get_mention_legend_format_consistency(self):
        """Test that the mention legend format is consistent."""

        async def run_test():
            # Mock channel
            mock_channel = MagicMock()

            # Mock bot user
            mock_bot_user = MagicMock()
            mock_bot_user.id = 99999

            # Mock single member
            mock_member = MagicMock()
            mock_member.display_name = "TestUser"
            mock_member.id = 12345

            # Mock channel.members
            mock_channel.members = [mock_member]

            # Test the function
            result = await get_mention_legend(mock_channel, mock_bot_user)

            # Verify exact format
            lines = result.split("\n")
            self.assertEqual(
                lines[0], "Here are all the users in this channel:")
            self.assertEqual(lines[1], "You are <@99999>!")
            self.assertEqual(lines[2], "@TestUser = <@12345>")
            self.assertTrue(lines[3].startswith("Whenever you see a mention"))
            self.assertTrue(lines[3].endswith("to recoginize your intent."))

        self.loop.run_until_complete(run_test())


if __name__ == "__main__":
    unittest.main()
