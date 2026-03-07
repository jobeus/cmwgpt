"""Tests for injected auto-update, restart, and announcement services."""

import unittest
from types import SimpleNamespace
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

    def test_start_uses_event_loop_fallback_and_ignores_duplicate_start(self):
        service = AutoUpdateService(
            check_interval=1,
            enabled=True,
            queue_service=self.queue_service,
            is_git_repository_fn=lambda: True,
            get_current_commit_hash_fn=lambda: None,
        )

        with patch("src.services.auto_update_service.asyncio.get_running_loop", side_effect=RuntimeError()), patch(
            "src.services.auto_update_service.asyncio.get_event_loop", return_value="loop"
        ) as mock_get_loop, patch("src.services.auto_update_service.threading.Thread.start"):
            service.start()

        self.assertEqual(service._loop, "loop")
        mock_get_loop.assert_called_once_with()

        with patch("src.services.auto_update_service.threading.Thread.start") as mock_start:
            service.start()
        mock_start.assert_not_called()

    async def test_trigger_restart_queues_injected_callback(self):
        callback = AsyncMock()
        self.service.set_restart_callback(callback)

        result = await self.service.trigger_restart(manual=True)

        self.assertTrue(result)
        self.queue_service.queue_restart.assert_awaited_once()
        queued_restart = self.queue_service.queue_restart.await_args.args[0]
        await queued_restart()
        callback.assert_awaited_once_with(manual=True)

    async def test_trigger_restart_without_callback_returns_false(self):
        service = AutoUpdateService(enabled=True, queue_service=self.queue_service)

        result = await service.trigger_restart(manual=False)

        self.assertFalse(result)
        self.queue_service.queue_restart.assert_not_awaited()

    def test_disabled_service_does_not_start(self):
        service = AutoUpdateService(enabled=False, queue_service=self.queue_service)
        service.start()
        self.assertFalse(service._is_running)

    def test_service_does_not_start_outside_git_repository(self):
        service = AutoUpdateService(
            enabled=True,
            queue_service=self.queue_service,
            is_git_repository_fn=lambda: False,
        )

        service.start()

        self.assertFalse(service._is_running)

    def test_monitor_git_updates_schedules_restart_when_new_commits_found(self):
        self.service._loop = object()

        with patch("src.services.auto_update_service.asyncio.run_coroutine_threadsafe") as mock_schedule:
            def close_coro(coro, loop):
                coro.close()
                return MagicMock()

            mock_schedule.side_effect = close_coro
            self.service._monitor_git_updates()

        mock_schedule.assert_called_once()
        scheduled_loop = mock_schedule.call_args.args[1]
        self.assertIs(scheduled_loop, self.service._loop)

    def test_monitor_git_updates_stops_after_repeated_fetch_failures(self):
        service = AutoUpdateService(
            check_interval=1,
            enabled=True,
            queue_service=self.queue_service,
            is_git_repository_fn=lambda: True,
            get_current_commit_hash_fn=lambda: "abc123",
            fetch_updates_fn=lambda: False,
            check_for_new_commits_fn=lambda: False,
        )

        with patch.object(service._stop_monitoring, "wait", return_value=False):
            service._monitor_git_updates()

        self.assertEqual(service._consecutive_failures, service._max_consecutive_failures)

    def test_monitor_git_updates_resets_failures_and_handles_exceptions(self):
        service = AutoUpdateService(
            check_interval=1,
            enabled=True,
            queue_service=self.queue_service,
            is_git_repository_fn=lambda: True,
            fetch_updates_fn=lambda: True,
            check_for_new_commits_fn=lambda: False,
        )
        service._consecutive_failures = 2

        with patch.object(service._stop_monitoring, "wait", side_effect=[True]):
            service._monitor_git_updates()

        self.assertEqual(service._consecutive_failures, 0)

        exploding = AutoUpdateService(
            check_interval=1,
            enabled=True,
            queue_service=self.queue_service,
            is_git_repository_fn=lambda: True,
            fetch_updates_fn=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            check_for_new_commits_fn=lambda: False,
        )

        with patch.object(exploding._stop_monitoring, "wait", return_value=False):
            exploding._monitor_git_updates()

        self.assertEqual(exploding._consecutive_failures, exploding._max_consecutive_failures)

    def test_stop_handles_stuck_thread(self):
        service = AutoUpdateService(enabled=True, queue_service=self.queue_service)
        service._is_running = True
        stuck_thread = MagicMock()
        stuck_thread.is_alive.side_effect = [True, True]
        service._monitoring_thread = stuck_thread

        service.stop()

        stuck_thread.join.assert_called_once_with(timeout=10.0)
        self.assertFalse(service._is_running)

    def test_get_status_reports_runtime_fields(self):
        self.service._is_running = True
        self.service._consecutive_failures = 2
        self.service._last_known_commit = "abc123"

        status = self.service.get_status()

        self.assertTrue(status["enabled"])
        self.assertTrue(status["running"])
        self.assertEqual(status["check_interval"], 1)
        self.assertEqual(status["consecutive_failures"], 2)
        self.assertEqual(status["last_known_commit"], "abc123")
        self.assertTrue(status["is_git_repo"])


class TestRestartHandler(unittest.IsolatedAsyncioTestCase):
    async def test_restart_status_accessors_default_false(self):
        handler = RestartHandler(state_service=MagicMock(), git_pull=MagicMock())

        self.assertFalse(handler.is_restart_in_progress())
        self.assertFalse(handler.should_skip_cleanup())

    async def test_perform_graceful_shutdown_uses_injected_state_service(self):
        state_service = MagicMock()
        state_service.save_state_to_temp_file.return_value = "/tmp/state.json"
        handler = RestartHandler(state_service=state_service, git_pull=lambda: True)

        await handler.perform_graceful_shutdown()

        state_service.save_state_to_temp_file.assert_called_once_with(
            {"manual_restart": False, "graceful_shutdown": True}
        )

    async def test_perform_graceful_shutdown_while_restart_in_progress_and_on_failure(self):
        state_service = MagicMock()
        state_service.save_state_to_temp_file.return_value = None
        handler = RestartHandler(state_service=state_service, git_pull=lambda: True)
        handler._restart_in_progress = True

        with patch("src.services.restart_handler.asyncio.sleep", new=AsyncMock()):
            await handler.perform_graceful_shutdown()

        self.assertTrue(handler.should_skip_cleanup())

        state_service = MagicMock()
        state_service.save_state_to_temp_file.side_effect = RuntimeError("disk full")
        handler = RestartHandler(state_service=state_service, git_pull=lambda: True)

        with patch("src.services.restart_handler.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(RuntimeError):
                await handler.perform_graceful_shutdown()

    async def test_perform_restart_exits_with_restart_code(self):
        state_service = MagicMock()
        state_service.save_state_to_temp_file.return_value = "/tmp/state.json"
        git_pull = MagicMock(return_value=True)
        handler = RestartHandler(state_service=state_service, git_pull=git_pull)

        with patch("src.services.restart_handler.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(SystemExit) as ctx:
                await handler.perform_restart(manual=True)

        self.assertEqual(ctx.exception.code, 42)
        state_service.save_state_to_temp_file.assert_called_once_with({"manual_restart": True})
        git_pull.assert_called_once_with()
        self.assertTrue(handler.is_restart_in_progress())
        self.assertTrue(handler.should_skip_cleanup())

    async def test_duplicate_restart_request_is_ignored(self):
        state_service = MagicMock()
        handler = RestartHandler(state_service=state_service, git_pull=MagicMock())
        handler._restart_in_progress = True

        await handler.perform_restart(manual=False)

        state_service.save_state_to_temp_file.assert_not_called()

    async def test_perform_restart_continues_when_save_and_git_pull_fail(self):
        state_service = MagicMock()
        state_service.save_state_to_temp_file.return_value = None
        git_pull = MagicMock(return_value=False)
        handler = RestartHandler(state_service=state_service, git_pull=git_pull)

        with patch("src.services.restart_handler.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(SystemExit) as ctx:
                await handler.perform_restart(manual=False)

        self.assertEqual(ctx.exception.code, 42)
        state_service.save_state_to_temp_file.assert_called_once_with({"manual_restart": False})
        git_pull.assert_called_once_with()


class TestAnnouncementService(unittest.IsolatedAsyncioTestCase):
    def test_get_current_git_sha_success_and_exception(self):
        state_service = MagicMock()
        service = AnnouncementService(state_service=state_service)

        with patch(
            "src.services.announcement_service.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="abc123\n", stderr=""),
        ):
            self.assertEqual(service._get_current_git_sha(), "abc123")

        with patch("src.services.announcement_service.subprocess.run", side_effect=RuntimeError("boom")):
            self.assertIsNone(service._get_current_git_sha())

    def test_get_current_git_sha_returns_none_on_subprocess_failure(self):
        state_service = MagicMock()
        service = AnnouncementService(state_service=state_service)

        with patch(
            "src.services.announcement_service.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="fatal"),
        ):
            result = service._get_current_git_sha()

        self.assertIsNone(result)

    def test_get_complete_changelog_formats_matching_commits(self):
        state_service = MagicMock()
        service = AnnouncementService(state_service=state_service)

        with patch(
            "src.services.announcement_service.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="abc123 first\ndef456 second\n", stderr=""),
        ):
            result = service._get_complete_changelog("old", "new")

        self.assertEqual(result, "• abc123 first\n• def456 second")

    def test_get_complete_changelog_returns_none_for_empty_or_errors(self):
        state_service = MagicMock()
        service = AnnouncementService(state_service=state_service)

        with patch(
            "src.services.announcement_service.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ):
            self.assertIsNone(service._get_complete_changelog("old", "new"))

        with patch("src.services.announcement_service.subprocess.run", side_effect=RuntimeError("boom")):
            self.assertIsNone(service._get_complete_changelog("old", "new"))

    async def test_long_changelog_uses_injected_paste_service(self):
        state_service = MagicMock()
        state_service.get_last_git_sha.return_value = "oldsha"
        state_service.get_active_channels.return_value = [123]

        bot = MagicMock()
        channel = MagicMock()
        channel.send = AsyncMock()
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
        channel = MagicMock()
        channel.send = AsyncMock()
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

    async def test_quiet_updates_skip_announcements(self):
        state_service = MagicMock()
        service = AnnouncementService(
            state_service=state_service,
            quiet_updates=True,
            current_git_sha_loader=lambda: "newsha",
            changelog_loader=lambda *_: "• commit",
        )
        service.set_bot(MagicMock())

        await service.announce_update()

        state_service.get_last_git_sha.assert_not_called()

    async def test_missing_current_sha_and_no_active_channels_skip_announcements(self):
        state_service = MagicMock()
        service = AnnouncementService(
            state_service=state_service,
            quiet_updates=False,
            current_git_sha_loader=lambda: None,
            changelog_loader=lambda *_: "• commit",
        )
        service.set_bot(MagicMock())

        await service.announce_update()

        state_service.get_last_git_sha.assert_not_called()

        state_service = MagicMock()
        state_service.get_last_git_sha.return_value = "oldsha"
        state_service.get_active_channels.return_value = []
        service = AnnouncementService(
            state_service=state_service,
            quiet_updates=False,
            current_git_sha_loader=lambda: "newsha",
            changelog_loader=lambda *_: "• commit",
        )
        service.set_bot(MagicMock())

        await service.announce_update()

        state_service.set_last_git_sha.assert_not_called()

    async def test_missing_bot_skips_announcements(self):
        state_service = MagicMock()
        service = AnnouncementService(
            state_service=state_service,
            quiet_updates=False,
            current_git_sha_loader=lambda: "newsha",
            changelog_loader=lambda *_: "• commit",
        )

        await service.announce_update()

        state_service.get_last_git_sha.assert_not_called()

    async def test_unchanged_sha_skips_duplicate_announcement(self):
        state_service = MagicMock()
        state_service.get_last_git_sha.return_value = "same-sha"
        service = AnnouncementService(
            state_service=state_service,
            quiet_updates=False,
            current_git_sha_loader=lambda: "same-sha",
            changelog_loader=lambda *_: "• commit",
        )
        service.set_bot(MagicMock())

        await service.announce_update()

        state_service.get_active_channels.assert_not_called()
        state_service.set_last_git_sha.assert_not_called()

    async def test_no_matching_changelog_updates_sha_without_sending(self):
        state_service = MagicMock()
        state_service.get_last_git_sha.return_value = "oldsha"
        state_service.get_active_channels.return_value = [123]
        bot = MagicMock()
        empty_channel = MagicMock()
        empty_channel.send = AsyncMock()
        bot.get_channel.return_value = empty_channel
        service = AnnouncementService(
            state_service=state_service,
            quiet_updates=False,
            current_git_sha_loader=lambda: "newsha",
            changelog_loader=lambda *_: None,
        )
        service.set_bot(bot)

        await service.announce_update()

        bot.get_channel.assert_not_called()
        state_service.set_last_git_sha.assert_called_once_with("newsha")

    async def test_paste_upload_failure_falls_back_to_truncated_message(self):
        state_service = MagicMock()
        state_service.get_last_git_sha.return_value = "oldsha"
        state_service.get_active_channels.return_value = [123]
        bot = MagicMock()
        channel = MagicMock()
        channel.send = AsyncMock()
        bot.get_channel.return_value = channel
        paste_service = MagicMock()
        paste_service.upload_markdown = AsyncMock(side_effect=Exception("boom"))
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
        sent_message = channel.send.await_args.args[0]
        self.assertIn("Recent changes", sent_message)
        self.assertNotIn("View complete changelog", sent_message)

    async def test_announcement_handles_missing_channels_and_send_failures(self):
        class FakeForbidden(Exception):
            pass

        class FakeHTTPException(Exception):
            pass

        state_service = MagicMock()
        state_service.get_last_git_sha.return_value = "oldsha"
        state_service.get_active_channels.return_value = [1, 2, 3]

        forbidden_channel = MagicMock()
        forbidden_channel.send = AsyncMock(side_effect=FakeForbidden())
        http_channel = MagicMock()
        http_channel.send = AsyncMock(side_effect=FakeHTTPException("boom"))

        bot = MagicMock()
        bot.get_channel.side_effect = [None, forbidden_channel, http_channel]

        service = AnnouncementService(
            state_service=state_service,
            quiet_updates=False,
            current_git_sha_loader=lambda: "newsha",
            changelog_loader=lambda *_: "• commit",
        )
        service.set_bot(bot)

        with patch("src.services.announcement_service.discord.Forbidden", FakeForbidden), patch(
            "src.services.announcement_service.discord.HTTPException", FakeHTTPException
        ):
            await service.announce_update()

        self.assertEqual(bot.get_channel.call_count, 3)
        state_service.set_last_git_sha.assert_called_once_with("newsha")

    async def test_announcement_handles_unexpected_channel_errors_and_success_delay(self):
        state_service = MagicMock()
        state_service.get_last_git_sha.return_value = "oldsha"
        state_service.get_active_channels.return_value = [1, 2]

        bad_channel = MagicMock()
        bad_channel.send = AsyncMock(side_effect=RuntimeError("boom"))
        good_channel = MagicMock()
        good_channel.name = "general"
        good_channel.send = AsyncMock()

        bot = MagicMock()
        bot.get_channel.side_effect = [bad_channel, good_channel]

        service = AnnouncementService(
            state_service=state_service,
            quiet_updates=False,
            current_git_sha_loader=lambda: "newsha",
            changelog_loader=lambda *_: "• commit",
        )
        service.set_bot(bot)

        with patch("src.services.announcement_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await service.announce_update(was_manual=True)

        good_channel.send.assert_awaited_once()
        self.assertIn("manual restart", good_channel.send.await_args.args[0])
        mock_sleep.assert_awaited_once_with(0.5)


if __name__ == "__main__":
    unittest.main()
