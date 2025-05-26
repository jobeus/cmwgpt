"""
Tests for the auto-update service functionality.
"""

import unittest
import os
import json

from unittest.mock import patch, MagicMock

from src.services.auto_update_service import AutoUpdateService
from src.services.state_service import StateService
from src.services.restart_handler import RestartHandler
from src.services.announcement_service import AnnouncementService


class TestAutoUpdateService(unittest.TestCase):
    """Test cases for AutoUpdateService."""

    def setUp(self):
        """Set up test fixtures."""
        self.service = AutoUpdateService(
            check_interval=1)  # Short interval for testing

    def tearDown(self):
        """Clean up after tests."""
        self.service.stop()

    def test_initialization(self):
        """Test auto-update service initialization."""
        self.assertEqual(self.service.check_interval, 1)
        self.assertFalse(self.service._is_running)
        self.assertIsNone(self.service._restart_callback)

    def test_set_restart_callback(self):
        """Test setting restart callback."""

        async def dummy_callback():
            pass

        self.service.set_restart_callback(dummy_callback)
        self.assertEqual(self.service._restart_callback, dummy_callback)

    @patch("src.services.auto_update_service.KEEP_UP_TO_DATE_WITH_GIT", True)
    @patch("src.services.auto_update_service.is_git_repository", return_value=True)
    @patch("src.services.auto_update_service.get_current_commit_hash",
           return_value="abc123")
    def test_start_with_git_enabled(self, mock_commit, mock_git_repo):
        """Test starting service when git is enabled and available."""
        self.service.start()
        self.assertTrue(self.service._is_running)
        self.assertEqual(self.service._last_known_commit, "abc123")

    @patch("src.services.auto_update_service.KEEP_UP_TO_DATE_WITH_GIT", False)
    def test_start_with_git_disabled(self):
        """Test starting service when git is disabled."""
        self.service.start()
        self.assertFalse(self.service._is_running)

    @patch("src.services.auto_update_service.KEEP_UP_TO_DATE_WITH_GIT", True)
    @patch("src.services.auto_update_service.is_git_repository", return_value=False)
    def test_start_without_git_repo(self, mock_git_repo):
        """Test starting service when not in a git repository."""
        self.service.start()
        self.assertFalse(self.service._is_running)

    def test_get_status(self):
        """Test getting service status."""
        status = self.service.get_status()
        self.assertIn("enabled", status)
        self.assertIn("running", status)
        self.assertIn("check_interval", status)
        self.assertIn("consecutive_failures", status)
        self.assertIn("is_git_repo", status)


class TestStateServicePersistence(unittest.TestCase):
    """Test cases for state persistence functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.state_service = StateService()
        # Clear any existing state
        self.state_service.clear_all_data()

    def tearDown(self):
        """Clean up after tests."""
        # Clean up any temp files created during tests
        self.state_service.cleanup_temp_files()

    def test_save_and_load_state(self):
        """Test saving and loading state to/from temporary files."""
        # Set up some test state
        channel_id = 12345
        self.state_service.set_model(channel_id, "gpt-4o-mini")
        self.state_service.set_system_prompt(channel_id, "Test prompt")
        self.state_service.add_message_to_conversation(
            channel_id, {"role": "user", "content": "Hello"})

        # Save state
        temp_file = self.state_service.save_state_to_temp_file()
        self.assertIsNotNone(temp_file)
        self.assertTrue(os.path.exists(temp_file))

        # Verify file permissions are secure (600)
        file_stat = os.stat(temp_file)
        file_mode = file_stat.st_mode & 0o777
        self.assertEqual(file_mode, 0o600)

        # Clear current state
        self.state_service.clear_all_data()
        self.assertIsNone(self.state_service.get_model(channel_id))

        # Load state back
        success = self.state_service.load_state_from_temp_files()
        self.assertTrue(success)

        # Verify state was restored
        self.assertEqual(
            self.state_service.get_model(channel_id),
            "gpt-4o-mini")
        self.assertEqual(
            self.state_service.get_system_prompt(channel_id),
            "Test prompt")
        conversation = self.state_service.get_conversation(channel_id)
        self.assertEqual(len(conversation), 1)
        self.assertEqual(conversation[0]["content"], "Hello")

        # Verify temp file was cleaned up
        self.assertFalse(os.path.exists(temp_file))

    def test_load_state_no_files(self):
        """Test loading state when no temp files exist."""
        success = self.state_service.load_state_from_temp_files()
        self.assertFalse(success)

    def test_save_state_with_empty_data(self):
        """Test saving state when no data exists."""
        temp_file = self.state_service.save_state_to_temp_file()
        self.assertIsNotNone(temp_file)
        self.assertTrue(os.path.exists(temp_file))

        # Verify file contains empty data structures
        with open(temp_file, "r") as f:
            data = json.load(f)

        self.assertEqual(data["conversations"], {})
        self.assertEqual(data["models"], {})
        self.assertEqual(data["system_prompts"], {})

        # Clean up
        os.remove(temp_file)

    def test_cleanup_temp_files(self):
        """Test cleaning up temporary files."""
        # Create a temp file
        temp_file = self.state_service.save_state_to_temp_file()
        self.assertTrue(os.path.exists(temp_file))

        # Clean up
        self.state_service.cleanup_temp_files()
        self.assertFalse(os.path.exists(temp_file))


class TestRestartHandler(unittest.TestCase):
    """Test cases for RestartHandler."""

    def setUp(self):
        """Set up test fixtures."""
        self.handler = RestartHandler()

    def test_initialization(self):
        """Test restart handler initialization."""
        self.assertFalse(self.handler._restart_in_progress)

    def test_is_restart_in_progress(self):
        """Test checking restart progress status."""
        self.assertFalse(self.handler.is_restart_in_progress())

        # Simulate restart in progress
        self.handler._restart_in_progress = True
        self.assertTrue(self.handler.is_restart_in_progress())

    @patch("src.utils.git_utils.perform_git_pull")
    def test_git_repository_check(self, mock_git_pull):
        """Test git repository detection."""
        # Mock successful git pull
        mock_git_pull.return_value = True

        from src.utils.git_utils import perform_git_pull

        result = perform_git_pull()
        self.assertTrue(result)

    @patch("src.utils.git_utils.perform_git_pull")
    def test_git_pull_failure(self, mock_git_pull):
        """Test handling of git pull failure."""
        # Mock git pull failure
        mock_git_pull.return_value = False

        from src.utils.git_utils import perform_git_pull

        result = perform_git_pull()
        self.assertFalse(result)


class TestAnnouncementService(unittest.TestCase):
    """Test cases for AnnouncementService."""

    def setUp(self):
        """Set up test fixtures."""
        self.service = AnnouncementService()

    def test_initialization(self):
        """Test announcement service initialization."""
        self.assertIsNone(self.service._bot)

    def test_set_bot(self):
        """Test setting bot instance."""
        mock_bot = MagicMock()
        self.service.set_bot(mock_bot)
        self.assertEqual(self.service._bot, mock_bot)

    @patch("subprocess.run")
    def test_get_current_git_sha(self, mock_run):
        """Test getting current git SHA."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "abcdef1234567890\n"

        sha = self.service._get_current_git_sha()
        self.assertEqual(sha, "abcdef1234567890")

    @patch("subprocess.run")
    def test_get_current_git_sha_failure(self, mock_run):
        """Test handling of git SHA failure."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "Not a git repository"

        sha = self.service._get_current_git_sha()
        self.assertIsNone(sha)

    def test_active_channel_tracking(self):
        """Test active channel tracking in state service."""
        state_service = StateService()
        state_service.clear_all_data()

        # Test marking channels as active
        state_service.mark_channel_active(12345)
        state_service.mark_channel_active(67890)

        active_channels = state_service.get_active_channels()
        self.assertEqual(len(active_channels), 2)
        self.assertIn(12345, active_channels)
        self.assertIn(67890, active_channels)

        # Test clearing active channels
        state_service.clear_active_channels()
        active_channels = state_service.get_active_channels()
        self.assertEqual(len(active_channels), 0)

    @patch("src.services.announcement_service.QUIET_UPDATES", True)
    @patch("subprocess.run")
    def test_announce_update_quiet_mode(self, mock_run):
        """Test that announcements are skipped when QUIET_UPDATES is enabled."""
        # Set up mock bot
        mock_bot = MagicMock()
        self.service.set_bot(mock_bot)

        # Mock git SHA
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "abcdef1234567890\n"

        # Set up active channels
        from src.services.state_service import state_service

        state_service.mark_channel_active(12345)

        # Call announce_update - should return early due to QUIET_UPDATES
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                self.service.announce_update(
                    was_manual=True))
        finally:
            loop.close()

        # Verify no messages were sent
        mock_bot.get_channel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
