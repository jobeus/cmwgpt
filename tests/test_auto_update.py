"""Tests for injected auto-update, restart, and announcement services."""

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.announcement_service import AnnouncementService
from src.services.auto_update_service import AutoUpdateService
from src.services.restart_handler import RESTART_EXIT_CODE, RestartHandler
from src.utils.deploy_utils import request_redeploy


class TestAutoUpdateService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.queue_service = MagicMock()
        self.queue_service.queue_restart = AsyncMock(return_value=True)
        self.service = AutoUpdateService(queue_service=self.queue_service)

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
        result = await self.service.trigger_restart(manual=False)

        self.assertFalse(result)
        self.queue_service.queue_restart.assert_not_awaited()

    def test_the_bot_no_longer_listens_for_deploys(self):
        self.assertFalse(hasattr(self.service, "start"))
        self.assertFalse(hasattr(self.service, "build_app"))


class TestRequestRedeploy(unittest.TestCase):
    def test_nobody_to_ask_without_an_app_name(self):
        with patch.dict("os.environ", {}, clear=True), patch("src.utils.deploy_utils.subprocess.run") as run:
            self.assertFalse(request_redeploy())
        run.assert_not_called()

    def test_asks_deployd_for_a_forced_release_of_this_app(self):
        done = SimpleNamespace(returncode=0, stdout="deploying\n", stderr="")
        with patch.dict("os.environ", {"APP_NAME": "cmwgpt"}, clear=True), patch(
            "src.utils.deploy_utils.subprocess.run", return_value=done
        ) as run:
            self.assertTrue(request_redeploy())
        self.assertEqual(run.call_args.args[0], ["deploy", "request", "cmwgpt", "--force"])

    def test_a_refusal_a_missing_command_and_a_timeout_are_all_no(self):
        env = {"APP_NAME": "cmwgpt"}
        refused = SimpleNamespace(returncode=1, stdout="", stderr="bad signature")
        with patch.dict("os.environ", env, clear=True):
            with patch("src.utils.deploy_utils.subprocess.run", return_value=refused):
                self.assertFalse(request_redeploy())
            with patch("src.utils.deploy_utils.subprocess.run", side_effect=FileNotFoundError("deploy")):
                self.assertFalse(request_redeploy())
            with patch(
                "src.utils.deploy_utils.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="deploy", timeout=30),
            ):
                self.assertFalse(request_redeploy())

    def test_redeploy_command_replaces_the_command(self):
        done = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.dict("os.environ", {"REDEPLOY_COMMAND": "echo 'a b'"}, clear=True), patch(
            "src.utils.deploy_utils.subprocess.run", return_value=done
        ) as run:
            self.assertTrue(request_redeploy())
        self.assertEqual(run.call_args.args[0], ["echo", "a b"])


class TestRestartHandler(unittest.IsolatedAsyncioTestCase):
    async def test_restart_status_defaults(self):
        handler = RestartHandler(state_service=MagicMock(), redeploy=MagicMock())

        self.assertFalse(handler._restart_in_progress)
        self.assertIsNone(handler.exit_code)
        self.assertFalse(hasattr(handler, "should_skip_cleanup"))

    async def test_perform_graceful_shutdown_uses_injected_state_service(self):
        state_service = MagicMock()
        state_service.save_restart_state.return_value = "/tmp/state.json"
        handler = RestartHandler(state_service=state_service, redeploy=lambda: True)
        handler.set_shutdown_callback(AsyncMock())

        await handler.perform_graceful_shutdown()

        state_service.save_restart_state.assert_called_once_with(
            {"manual_restart": False, "graceful_shutdown": True}
        )
        # A plain shutdown only saves state: no exit code, no shutdown callback.
        self.assertIsNone(handler.exit_code)
        handler._shutdown.assert_not_called()

    async def test_perform_graceful_shutdown_on_save_failure(self):
        state_service = MagicMock()
        state_service.save_restart_state.return_value = None
        handler = RestartHandler(state_service=state_service, redeploy=lambda: True)

        with self.assertLogs("src.services.restart_handler", "WARNING") as logs:
            await handler.perform_graceful_shutdown()
        self.assertIn("Couldn't save state before shutting down", logs.output[-1])

        state_service = MagicMock()
        state_service.save_restart_state.side_effect = RuntimeError("disk full")
        handler = RestartHandler(state_service=state_service, redeploy=lambda: True)

        with self.assertRaises(RuntimeError):
            await handler.perform_graceful_shutdown()

    async def test_perform_restart_asks_for_a_release_and_keeps_running(self):
        state_service = MagicMock()
        state_service.save_restart_state.return_value = "/tmp/state.json"
        redeploy = MagicMock(return_value=True)
        handler = RestartHandler(state_service=state_service, redeploy=redeploy)
        shutdown = AsyncMock()
        handler.set_shutdown_callback(shutdown)

        await handler.perform_restart(manual=True)

        state_service.save_restart_state.assert_called_once_with({"manual_restart": True})
        redeploy.assert_called_once_with()
        self.assertTrue(handler._restart_in_progress)
        # The deploy agent stops the bot when the release is built: nothing shuts down here.
        self.assertIsNone(handler.exit_code)
        self.assertIsNone(handler._shutdown_task)
        shutdown.assert_not_awaited()

        # ...and that later shutdown still counts as the manual restart it is.
        await handler.perform_graceful_shutdown()
        state_service.save_restart_state.assert_called_with(
            {"manual_restart": True, "graceful_shutdown": True}
        )
        handler._give_up_task.cancel()

    async def test_restarts_are_allowed_again_when_the_release_never_arrives(self):
        handler = RestartHandler(state_service=MagicMock(), redeploy=MagicMock(return_value=True))

        with patch("src.services.restart_handler.REDEPLOY_WAIT_SECONDS", 0):
            await handler.perform_restart(manual=True)
            with self.assertLogs("src.services.restart_handler", level="WARNING"):
                await handler._give_up_task

        self.assertFalse(handler._restart_in_progress)
        self.assertFalse(handler._manual_restart)

    async def test_without_a_deploy_agent_restart_sets_exit_code_and_schedules_shutdown(self):
        state_service = MagicMock()
        redeploy = MagicMock(return_value=False)
        handler = RestartHandler(state_service=state_service, redeploy=redeploy)
        shutdown = AsyncMock()
        handler.set_shutdown_callback(shutdown)

        await handler.perform_restart(manual=True)

        self.assertEqual(handler.exit_code, RESTART_EXIT_CODE)
        self.assertEqual(RESTART_EXIT_CODE, 42)
        # Shutdown runs as its own task, not inline in perform_restart.
        shutdown.assert_not_awaited()
        await handler._shutdown_task
        shutdown.assert_awaited_once_with()

    async def test_perform_restart_asks_off_the_event_loop(self):
        handler = RestartHandler(state_service=MagicMock(), redeploy=MagicMock(return_value=True))
        handler.set_shutdown_callback(AsyncMock())

        with patch(
            "src.services.restart_handler.asyncio.to_thread", new=AsyncMock(return_value=False)
        ) as mock_to_thread:
            await handler.perform_restart()

        mock_to_thread.assert_awaited_once_with(handler._redeploy)
        await handler._shutdown_task

    async def test_perform_restart_without_shutdown_callback_still_sets_exit_code(self):
        handler = RestartHandler(state_service=MagicMock(), redeploy=MagicMock(return_value=False))

        with self.assertLogs("src.services.restart_handler", level="ERROR"):
            await handler.perform_restart()

        self.assertEqual(handler.exit_code, RESTART_EXIT_CODE)
        self.assertIsNone(handler._shutdown_task)

    async def test_duplicate_restart_request_is_ignored(self):
        state_service = MagicMock()
        handler = RestartHandler(state_service=state_service, redeploy=MagicMock())
        handler._restart_in_progress = True

        await handler.perform_restart(manual=False)

        state_service.save_restart_state.assert_not_called()
        self.assertIsNone(handler.exit_code)

    async def test_perform_restart_continues_when_save_and_the_request_fail(self):
        state_service = MagicMock()
        state_service.save_restart_state.return_value = None
        redeploy = MagicMock(side_effect=RuntimeError("deploy exploded"))
        handler = RestartHandler(state_service=state_service, redeploy=redeploy)
        shutdown = AsyncMock()
        handler.set_shutdown_callback(shutdown)

        await handler.perform_restart(manual=False)
        await handler._shutdown_task

        self.assertEqual(handler.exit_code, RESTART_EXIT_CODE)
        state_service.save_restart_state.assert_called_once_with({"manual_restart": False})
        redeploy.assert_called_once_with()
        shutdown.assert_awaited_once_with()


class TestAnnouncementService(unittest.IsolatedAsyncioTestCase):
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
        paste_service.upload_markdown = AsyncMock(return_value="https://paste.jobe.wtf/changelog.md")

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
