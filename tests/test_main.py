"""Tests for main module signal-handler wiring and shutdown behavior."""

import asyncio
import runpy
import signal
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import main


class FakeStoppedLoop:
    def is_running(self):
        return False

    def run_until_complete(self, coro):
        return asyncio.run(coro)


class FakeRunningLoop:
    def is_running(self):
        return True


class BrokenLoop:
    def is_running(self):
        raise RuntimeError("loop broken")


def make_bot_client():
    services = SimpleNamespace(
        restart_handler=MagicMock(),
        auto_update_service=MagicMock(),
        death_service=MagicMock(),
        queue_service=MagicMock(),
        openai_service=MagicMock(),
    )
    services.restart_handler.perform_graceful_shutdown = AsyncMock()
    services.auto_update_service.stop = MagicMock()
    services.death_service.stop = MagicMock()
    services.queue_service.stop = AsyncMock()
    services.openai_service.close = AsyncMock()
    bot = MagicMock()
    bot.is_closed.return_value = False
    bot.close = AsyncMock()
    return SimpleNamespace(services=services, bot=bot)


class TestMainSignalHandlers(unittest.TestCase):
    def test_setup_signal_handlers_registers_sigint_and_sigterm(self):
        bot_client = make_bot_client()
        handlers = {}

        def fake_signal(sig, handler):
            handlers[sig] = handler

        with patch("main.signal.signal", side_effect=fake_signal):
            main.setup_signal_handlers(bot_client)

        self.assertIn(signal.SIGINT, handlers)
        self.assertIn(signal.SIGTERM, handlers)

    def test_signal_handler_runs_full_shutdown_on_non_running_loop(self):
        bot_client = make_bot_client()
        handlers = {}

        with patch("main.signal.signal", side_effect=lambda sig, fn: handlers.setdefault(sig, fn)):
            main.setup_signal_handlers(bot_client)

        with patch("main.asyncio.get_event_loop", return_value=FakeStoppedLoop()):
            handlers[signal.SIGTERM](signal.SIGTERM, None)

        bot_client.services.restart_handler.perform_graceful_shutdown.assert_awaited_once_with()
        bot_client.services.auto_update_service.stop.assert_called_once_with()
        bot_client.services.death_service.stop.assert_called_once_with()
        bot_client.services.queue_service.stop.assert_awaited_once_with()
        bot_client.services.openai_service.close.assert_awaited_once_with()
        bot_client.bot.close.assert_awaited_once_with()

    def test_signal_handler_uses_create_task_when_loop_is_running(self):
        bot_client = make_bot_client()
        handlers = {}

        with patch("main.signal.signal", side_effect=lambda sig, fn: handlers.setdefault(sig, fn)):
            main.setup_signal_handlers(bot_client)

        with patch("main.asyncio.get_event_loop", return_value=FakeRunningLoop()), patch(
            "main.asyncio.create_task"
        ) as mock_create_task:
            handlers[signal.SIGINT](signal.SIGINT, None)

        mock_create_task.assert_called_once()
        scheduled_coro = mock_create_task.call_args.args[0]
        asyncio.run(scheduled_coro)
        bot_client.services.restart_handler.perform_graceful_shutdown.assert_awaited_once_with()

    def test_shutdown_continues_when_a_service_stop_raises(self):
        bot_client = make_bot_client()
        bot_client.services.auto_update_service.stop.side_effect = RuntimeError("boom")
        handlers = {}

        with patch("main.signal.signal", side_effect=lambda sig, fn: handlers.setdefault(sig, fn)):
            main.setup_signal_handlers(bot_client)

        with patch("main.asyncio.get_event_loop", return_value=FakeStoppedLoop()):
            handlers[signal.SIGTERM](signal.SIGTERM, None)

        bot_client.services.death_service.stop.assert_called_once_with()
        bot_client.services.queue_service.stop.assert_awaited_once_with()
        bot_client.services.openai_service.close.assert_awaited_once_with()
        bot_client.bot.close.assert_awaited_once_with()

    def test_shutdown_logs_and_continues_when_remaining_services_raise(self):
        bot_client = make_bot_client()
        bot_client.services.death_service.stop.side_effect = RuntimeError("death boom")
        bot_client.services.queue_service.stop.side_effect = RuntimeError("queue boom")
        bot_client.services.openai_service.close.side_effect = RuntimeError("openai boom")
        bot_client.bot.close.side_effect = RuntimeError("discord boom")
        handlers = {}

        with patch("main.signal.signal", side_effect=lambda sig, fn: handlers.setdefault(sig, fn)):
            main.setup_signal_handlers(bot_client)

        with patch("main.asyncio.get_event_loop", return_value=FakeStoppedLoop()), patch("builtins.print") as mock_print:
            handlers[signal.SIGTERM](signal.SIGTERM, None)

        bot_client.services.death_service.stop.assert_called_once_with()
        bot_client.services.queue_service.stop.assert_awaited_once_with()
        bot_client.services.openai_service.close.assert_awaited_once_with()
        bot_client.bot.close.assert_awaited_once_with()
        self.assertTrue(any("Error closing Discord bot" in call.args[0] for call in mock_print.call_args_list))

    def test_shutdown_handles_cancelled_error_cleanly(self):
        bot_client = make_bot_client()
        bot_client.services.restart_handler.perform_graceful_shutdown = AsyncMock(side_effect=asyncio.CancelledError())
        handlers = {}

        with patch("main.signal.signal", side_effect=lambda sig, fn: handlers.setdefault(sig, fn)):
            main.setup_signal_handlers(bot_client)

        with patch("main.asyncio.get_event_loop", return_value=FakeStoppedLoop()):
            handlers[signal.SIGINT](signal.SIGINT, None)

        bot_client.services.auto_update_service.stop.assert_not_called()
        bot_client.bot.close.assert_not_awaited()

    def test_shutdown_reports_top_level_shutdown_errors(self):
        bot_client = make_bot_client()
        bot_client.services.restart_handler.perform_graceful_shutdown = AsyncMock(side_effect=RuntimeError("boom"))
        handlers = {}

        with patch("main.signal.signal", side_effect=lambda sig, fn: handlers.setdefault(sig, fn)):
            main.setup_signal_handlers(bot_client)

        with patch("main.asyncio.get_event_loop", return_value=FakeStoppedLoop()), patch("builtins.print") as mock_print:
            handlers[signal.SIGTERM](signal.SIGTERM, None)

        self.assertTrue(any("Error during shutdown" in call.args[0] for call in mock_print.call_args_list))

    def test_signal_handler_creates_new_loop_when_get_event_loop_fails(self):
        bot_client = make_bot_client()
        handlers = {}
        new_loop = FakeStoppedLoop()

        with patch("main.signal.signal", side_effect=lambda sig, fn: handlers.setdefault(sig, fn)):
            main.setup_signal_handlers(bot_client)

        with patch("main.asyncio.get_event_loop", side_effect=RuntimeError("no loop")), patch(
            "main.asyncio.new_event_loop", return_value=new_loop
        ) as mock_new_loop, patch("main.asyncio.set_event_loop") as mock_set_loop:
            handlers[signal.SIGTERM](signal.SIGTERM, None)

        mock_new_loop.assert_called_once_with()
        mock_set_loop.assert_called_once_with(new_loop)
        bot_client.services.restart_handler.perform_graceful_shutdown.assert_awaited_once_with()

    def test_signal_handler_reports_critical_execution_errors(self):
        bot_client = make_bot_client()
        handlers = {}

        with patch("main.signal.signal", side_effect=lambda sig, fn: handlers.setdefault(sig, fn)):
            main.setup_signal_handlers(bot_client)

        with patch("main.asyncio.get_event_loop", return_value=BrokenLoop()), patch("builtins.print") as mock_print:
            handlers[signal.SIGTERM](signal.SIGTERM, None)

        self.assertTrue(any("Critical error during shutdown" in call.args[0] for call in mock_print.call_args_list))

    def test_main_module_script_entrypoint_creates_client_registers_signals_and_runs(self):
        bot_client = MagicMock()

        with patch("src.startup.create_bot_client", return_value=bot_client), patch("signal.signal") as mock_signal:
            runpy.run_module("main", run_name="__main__")

        bot_client.run.assert_called_once_with()
        self.assertEqual(mock_signal.call_count, 2)
