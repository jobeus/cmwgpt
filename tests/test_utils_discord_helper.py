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

from utils.discord_helper import get_mention_legend  # noqa: E402


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
            # Mock channel and guild
            mock_channel = MagicMock()
            mock_guild = MagicMock()
            mock_channel.guild = mock_guild

            # Mock members
            mock_member1 = MagicMock()
            mock_member1.display_name = "Alice"
            mock_member1.id = 12345

            mock_member2 = MagicMock()
            mock_member2.display_name = "Bob"
            mock_member2.id = 67890

            # Mock async iterator for fetch_members
            async def mock_fetch_members(limit=None):
                for member in [mock_member1, mock_member2]:
                    yield member

            mock_guild.fetch_members = mock_fetch_members

            # Test the function
            result = await get_mention_legend(mock_channel)

            # Verify result
            expected_lines = [
                "Here are all the users in this channel:",
                "@Alice = <@12345>",
                "@Bob = <@67890>",
                "Whenever you see a mention like <@USER_ID>, map it back to the corresponding handle. "
                "If you want to @mention someone yourself use <@USER_ID> instead of @nickname for discord "
                "to recoginize your intent.",
            ]
            expected = "\n".join(expected_lines)

            self.assertEqual(result, expected)

        self.loop.run_until_complete(run_test())

    def test_get_mention_legend_empty_guild(self):
        """Test mention legend with no members."""

        async def run_test():
            # Mock channel and guild
            mock_channel = MagicMock()
            mock_guild = MagicMock()
            mock_channel.guild = mock_guild

            # Mock empty async iterator
            async def mock_fetch_members(limit=None):
                return
                yield  # This line never executes, making it an empty async generator

            mock_guild.fetch_members = mock_fetch_members

            # Test the function
            result = await get_mention_legend(mock_channel)

            # Verify result
            expected_lines = [
                "Here are all the users in this channel:",
                "",
                "Whenever you see a mention like <@USER_ID>, map it back to the corresponding handle. "
                "If you want to @mention someone yourself use <@USER_ID> instead of @nickname for discord "
                "to recoginize your intent.",
            ]
            expected = "\n".join(expected_lines)

            self.assertEqual(result, expected)

        self.loop.run_until_complete(run_test())

    def test_get_mention_legend_special_characters(self):
        """Test mention legend with special characters in names."""

        async def run_test():
            # Mock channel and guild
            mock_channel = MagicMock()
            mock_guild = MagicMock()
            mock_channel.guild = mock_guild

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

            # Mock async iterator
            async def mock_fetch_members(limit=None):
                for member in [mock_member1, mock_member2, mock_member3]:
                    yield member

            mock_guild.fetch_members = mock_fetch_members

            # Test the function
            result = await get_mention_legend(mock_channel)

            # Verify special characters are handled
            self.assertIn("@User_123 = <@11111>", result)
            self.assertIn("@Test-User = <@22222>", result)
            self.assertIn("@émoji🚀user = <@33333>", result)

        self.loop.run_until_complete(run_test())

    def test_get_mention_legend_large_guild(self):
        """Test mention legend with many members."""

        async def run_test():
            # Mock channel and guild
            mock_channel = MagicMock()
            mock_guild = MagicMock()
            mock_channel.guild = mock_guild

            # Create many mock members
            members = []
            for i in range(100):
                mock_member = MagicMock()
                mock_member.display_name = f"User{i}"
                mock_member.id = 10000 + i
                members.append(mock_member)

            # Mock async iterator
            async def mock_fetch_members(limit=None):
                for member in members:
                    yield member

            mock_guild.fetch_members = mock_fetch_members

            # Test the function
            result = await get_mention_legend(mock_channel)

            # Verify all members are included
            for i in range(100):
                self.assertIn(f"@User{i} = <@{10000 + i}>", result)

            # Verify structure
            self.assertIn("Here are all the users in this channel:", result)
            self.assertIn("Whenever you see a mention like <@USER_ID>", result)

        self.loop.run_until_complete(run_test())

    def test_get_mention_legend_duplicate_names(self):
        """Test mention legend with duplicate display names."""

        async def run_test():
            # Mock channel and guild
            mock_channel = MagicMock()
            mock_guild = MagicMock()
            mock_channel.guild = mock_guild

            # Mock members with same display name but different IDs
            mock_member1 = MagicMock()
            mock_member1.display_name = "SameName"
            mock_member1.id = 11111

            mock_member2 = MagicMock()
            mock_member2.display_name = "SameName"
            mock_member2.id = 22222

            # Mock async iterator
            async def mock_fetch_members(limit=None):
                for member in [mock_member1, mock_member2]:
                    yield member

            mock_guild.fetch_members = mock_fetch_members

            # Test the function
            result = await get_mention_legend(mock_channel)

            # Verify both members are listed with their unique IDs
            self.assertIn("@SameName = <@11111>", result)
            self.assertIn("@SameName = <@22222>", result)

        self.loop.run_until_complete(run_test())

    def test_get_mention_legend_format_consistency(self):
        """Test that the mention legend format is consistent."""

        async def run_test():
            # Mock channel and guild
            mock_channel = MagicMock()
            mock_guild = MagicMock()
            mock_channel.guild = mock_guild

            # Mock single member
            mock_member = MagicMock()
            mock_member.display_name = "TestUser"
            mock_member.id = 12345

            # Mock async iterator
            async def mock_fetch_members(limit=None):
                yield mock_member

            mock_guild.fetch_members = mock_fetch_members

            # Test the function
            result = await get_mention_legend(mock_channel)

            # Verify exact format
            lines = result.split("\n")
            self.assertEqual(
                lines[0], "Here are all the users in this channel:")
            self.assertEqual(lines[1], "@TestUser = <@12345>")
            self.assertTrue(lines[2].startswith("Whenever you see a mention"))
            self.assertTrue(lines[2].endswith("to recoginize your intent."))

        self.loop.run_until_complete(run_test())


if __name__ == "__main__":
    unittest.main()
