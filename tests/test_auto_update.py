"""Tests for injected auto-update, restart, and announcement services."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.announcement_service import AnnouncementService
from aiohttp.test_utils import TestClient, TestServer

from src.services.auto_update_service import AutoUpdateService, sign_deploy_request
from src.services.restart_handler import RESTART_EXIT_CODE, RestartHandler


class TestAutoUpdateService(unittest.IsolatedAsyncioTestCase):
    SECRET = "s3cret"
    NOW = 1_700_000_000

    def setUp(self):
        self.queue_service = MagicMock()
        self.queue_service.queue_restart = AsyncMock(return_value=True)
        self.service = AutoUpdateService(
            queue_service=self.queue_service,
            secret=self.SECRET,
            get_current_commit_hash_fn=lambda: "old111",
            clock=lambda: self.NOW,
        )
        self.service.set_restart_callback(AsyncMock())

    async def post(self, payload, *, secret=None, timestamp=None, signature=None):
        body = json.dumps(payload).encode()
        ts = str(self.NOW if timestamp is None else timestamp)
        headers = {
            "X-Deploy-Timestamp": ts,
            "X-Deploy-Signature": signature or sign_deploy_request(secret or self.SECRET, ts, body),
        }
        async with TestClient(TestServer(self.service.build_app())) as client:
            resp = await client.post("/deploy", data=body, headers=headers)
            return resp.status, await resp.json()

    async def test_valid_webhook_for_new_commit_queues_restart(self):
        status, data = await self.post({"sha": "new222"})

        self.assertEqual(status, 202)
        self.assertEqual(data["status"], "restarting")
        self.queue_service.queue_restart.assert_awaited_once()

    async def test_webhook_for_running_commit_is_a_noop(self):
        status, data = await self.post({"sha": "old111"})

        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "up-to-date")
        self.queue_service.queue_restart.assert_not_awaited()

    async def test_bad_signature_is_rejected(self):
        status, _ = await self.post({"sha": "new222"}, secret="wrong")

        self.assertEqual(status, 401)
        self.queue_service.queue_restart.assert_not_awaited()

    async def test_stale_or_garbage_timestamp_is_rejected(self):
        status, _ = await self.post({"sha": "new222"}, timestamp=self.NOW - 3600)
        self.assertEqual(status, 401)

        status, _ = await self.post({"sha": "new222"}, timestamp="soon", signature="sha256=00")
        self.assertEqual(status, 401)
        self.queue_service.queue_restart.assert_not_awaited()

    async def test_invalid_body_is_rejected(self):
        status, _ = await self.post(["not", "an", "object"])

        self.assertEqual(status, 400)
        self.queue_service.queue_restart.assert_not_awaited()

    async def test_restart_queue_failure_returns_503(self):
        self.queue_service.queue_restart.return_value = False

        status, _ = await self.post({"sha": "new222"})

        self.assertEqual(status, 503)

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
        service = AutoUpdateService(queue_service=self.queue_service)

        result = await service.trigger_restart(manual=False)

        self.assertFalse(result)
        self.queue_service.queue_restart.assert_not_awaited()

    async def test_service_without_secret_does_not_listen(self):
        service = AutoUpdateService(queue_service=self.queue_service)

        await service.start()

        self.assertFalse(service.get_status()["running"])
        self.assertFalse(service.get_status()["enabled"])

    async def test_start_and_stop_listener(self):
        service = AutoUpdateService(queue_service=self.queue_service, secret="x", host="127.0.0.1", port=0)

        await service.start()
        self.assertTrue(service.get_status()["running"])
        await service.start()  # duplicate start is ignored
        await service.stop()
        self.assertFalse(service.get_status()["running"])
        await service.stop()  # stopping twice is harmless

    async def test_start_survives_bind_failure(self):
        service = AutoUpdateService(queue_service=self.queue_service, secret="x", host="127.0.0.1", port=0)

        with patch("src.services.auto_update_service.web.TCPSite.start", side_effect=OSError("in use")):
            await service.start()

        self.assertFalse(service.get_status()["running"])


class TestRestartHandler(unittest.IsolatedAsyncioTestCase):
    async def test_restart_status_defaults(self):
        handler = RestartHandler(state_service=MagicMock(), git_pull=MagicMock())

        self.assertFalse(handler.is_restart_in_progress())
        self.assertIsNone(handler.exit_code)
        self.assertFalse(hasattr(handler, "should_skip_cleanup"))

    async def test_perform_graceful_shutdown_uses_injected_state_service(self):
        state_service = MagicMock()
        state_service.save_state_to_temp_file.return_value = "/tmp/state.json"
        handler = RestartHandler(state_service=state_service, git_pull=lambda: True)
        handler.set_shutdown_callback(AsyncMock())

        await handler.perform_graceful_shutdown()

        state_service.save_state_to_temp_file.assert_called_once_with(
            {"manual_restart": False, "graceful_shutdown": True}
        )
        # A plain shutdown only saves state: no exit code, no shutdown callback.
        self.assertIsNone(handler.exit_code)
        handler._shutdown.assert_not_called()

    async def test_perform_graceful_shutdown_on_save_failure(self):
        state_service = MagicMock()
        state_service.save_state_to_temp_file.return_value = None
        handler = RestartHandler(state_service=state_service, git_pull=lambda: True)

        with patch("builtins.print") as mock_print:
            await handler.perform_graceful_shutdown()
        mock_print.assert_called_with("⚠️  Failed to save state")

        state_service = MagicMock()
        state_service.save_state_to_temp_file.side_effect = RuntimeError("disk full")
        handler = RestartHandler(state_service=state_service, git_pull=lambda: True)

        with self.assertRaises(RuntimeError):
            await handler.perform_graceful_shutdown()

    async def test_perform_restart_sets_exit_code_and_schedules_shutdown(self):
        state_service = MagicMock()
        state_service.save_state_to_temp_file.return_value = "/tmp/state.json"
        git_pull = MagicMock(return_value=True)
        handler = RestartHandler(state_service=state_service, git_pull=git_pull)
        shutdown = AsyncMock()
        handler.set_shutdown_callback(shutdown)

        await handler.perform_restart(manual=True)

        self.assertEqual(handler.exit_code, RESTART_EXIT_CODE)
        self.assertEqual(RESTART_EXIT_CODE, 42)
        state_service.save_state_to_temp_file.assert_called_once_with({"manual_restart": True})
        git_pull.assert_called_once_with()
        self.assertTrue(handler.is_restart_in_progress())
        # Shutdown runs as its own task, not inline in perform_restart.
        shutdown.assert_not_awaited()
        await handler._shutdown_task
        shutdown.assert_awaited_once_with()

    async def test_perform_restart_runs_git_pull_off_the_event_loop(self):
        handler = RestartHandler(state_service=MagicMock(), git_pull=MagicMock(return_value=True))
        handler.set_shutdown_callback(AsyncMock())

        with patch(
            "src.services.restart_handler.asyncio.to_thread", new=AsyncMock(return_value=True)
        ) as mock_to_thread:
            await handler.perform_restart()

        mock_to_thread.assert_awaited_once_with(handler._git_pull)
        await handler._shutdown_task

    async def test_perform_restart_without_shutdown_callback_still_sets_exit_code(self):
        handler = RestartHandler(state_service=MagicMock(), git_pull=MagicMock(return_value=True))

        with self.assertLogs("src.services.restart_handler", level="ERROR"):
            await handler.perform_restart()

        self.assertEqual(handler.exit_code, RESTART_EXIT_CODE)
        self.assertIsNone(handler._shutdown_task)

    async def test_duplicate_restart_request_is_ignored(self):
        state_service = MagicMock()
        handler = RestartHandler(state_service=state_service, git_pull=MagicMock())
        handler._restart_in_progress = True

        await handler.perform_restart(manual=False)

        state_service.save_state_to_temp_file.assert_not_called()
        self.assertIsNone(handler.exit_code)

    async def test_perform_restart_continues_when_save_and_git_pull_fail(self):
        state_service = MagicMock()
        state_service.save_state_to_temp_file.return_value = None
        git_pull = MagicMock(side_effect=RuntimeError("git exploded"))
        handler = RestartHandler(state_service=state_service, git_pull=git_pull)
        shutdown = AsyncMock()
        handler.set_shutdown_callback(shutdown)

        await handler.perform_restart(manual=False)
        await handler._shutdown_task

        self.assertEqual(handler.exit_code, RESTART_EXIT_CODE)
        state_service.save_state_to_temp_file.assert_called_once_with({"manual_restart": False})
        git_pull.assert_called_once_with()
        shutdown.assert_awaited_once_with()


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
