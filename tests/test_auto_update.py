"""Tests for injected auto-update, restart, and announcement services."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.announcement_service import AnnouncementService
from src.services.auto_update_service import AutoUpdateService
from src.services.restart_handler import RestartHandler


class TestAutoUpdateService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.queue_service = MagicMock()
        self.queue_service.queue_restart = AsyncMock(return_value=True)
        self.service = AutoUpdateService(
            check_interval=1,
            enabled=True,
            queue_service=self.queue_service,
            is_git_repository_fn=lambda: True,
            get_current_commit_hash_fn=lambda: "abc123",
            fetch_updates_fn=lambda: True,
            check_for_new_commits_fn=lambda: True,
        )

    def tearDown(self):
        self.service.stop()

    def test_start_uses_injected_git_dependencies(self):
        with patch("src.services.auto_update_service.threading.Thread.start") as mock_start:
            self.service.start()

        self.assertTrue(self.service._is_running)
        self.assertEqual(self.service._last_known_commit, "abc123")
        mock_start.assert_called_once()

    async def test_trigger_restart_queues_injected_callback(self):
        callback = AsyncMock()
        self.service.set_restart_callback(callback)

        result = await self.service.trigger_restart(manual=True)

        self.assertTrue(result)
        self.queue_service.queue_restart.assert_awaited_once()
        queued_restart = self.queue_service.queue_restart.await_args.args[0]
        await queued_restart()
        callback.assert_awaited_once_with(manual=True)

    def test_disabled_service_does_not_start(self):
        service = AutoUpdateService(enabled=False, queue_service=self.queue_service)
        service.start()
        self.assertFalse(service._is_running)


class TestRestartHandler(unittest.IsolatedAsyncioTestCase):
    async def test_perform_graceful_shutdown_uses_injected_state_service(self):
        state_service = MagicMock()
        state_service.save_state_to_temp_file.return_value = "/tmp/state.json"
        handler = RestartHandler(state_service=state_service, git_pull=lambda: True)

        await handler.perform_graceful_shutdown()

        state_service.save_state_to_temp_file.assert_called_once_with(
            {"manual_restart": False, "graceful_shutdown": True}
        )


class TestAnnouncementService(unittest.IsolatedAsyncioTestCase):
    async def test_long_changelog_uses_injected_paste_service(self):
        state_service = MagicMock()
        state_service.get_last_git_sha.return_value = "oldsha"
        state_service.get_active_channels.return_value = [123]

        bot = MagicMock()
        channel = AsyncMock()
        bot.get_channel.return_value = channel

        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(return_value="https://paste.rs/changelog.md")

        service = AnnouncementService(
            state_service=state_service,
            paste_service=paste_service,
            quiet_updates=False,
            current_git_sha_loader=lambda: "newsha",
            changelog_loader=lambda *_: "• commit\n" * 600,
        )
        service.set_bot(bot)

        await service.announce_update()

        paste_service.upload_markdown.assert_awaited_once()
        channel.send.assert_awaited_once()
        sent_message = channel.send.await_args.args[0]
        self.assertIn("View complete changelog", sent_message)
        state_service.set_last_git_sha.assert_called_with("newsha")

    async def test_long_changelog_falls_back_when_paste_service_disabled(self):
        state_service = MagicMock()
        state_service.get_last_git_sha.return_value = "oldsha"
        state_service.get_active_channels.return_value = [123]

        bot = MagicMock()
        channel = AsyncMock()
        bot.get_channel.return_value = channel

        service = AnnouncementService(
            state_service=state_service,
            paste_service=None,
            quiet_updates=False,
            current_git_sha_loader=lambda: "newsha",
            changelog_loader=lambda *_: "• commit\n" * 600,
        )
        service.set_bot(bot)

        await service.announce_update()

        channel.send.assert_awaited_once()
        sent_message = channel.send.await_args.args[0]
        self.assertIn("Recent changes", sent_message)
        self.assertNotIn("View complete changelog", sent_message)


if __name__ == "__main__":
    unittest.main()