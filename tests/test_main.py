"""Tests for main module signal-handler wiring and shutdown behavior."""

import asyncio
import runpy
import signal
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import main


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
    bot.setup_hook = AsyncMock()
    return SimpleNamespace(services=services, bot=bot)


def run_scheduled_shutdown(loop):
    """Run the single shutdown coroutine scheduled on a fake loop."""
    loop.create_task.assert_called_once()
    coro = loop.create_task.call_args.args[0]
    asyncio.run(coro)


class TestMainSignalHandlers(unittest.TestCase):
    def test_register_signal_handlers_registers_sigint_and_sigterm_on_loop(self):
        bot_client = make_bot_client()
        register, handle_signal, _ = main.setup_signal_handlers(bot_client)

        loop = MagicMock()
        register(loop)

        registered_signals = [call.args[0] for call in loop.add_signal_handler.call_args_list]
        self.assertIn(signal.SIGINT, registered_signals)
        self.assertIn(signal.SIGTERM, registered_signals)
        for call in loop.add_signal_handler.call_args_list:
            self.assertIs(call.args[1], handle_signal)

    def test_setup_hook_wrapper_registers_handlers_and_chains_original(self):
        bot_client = make_bot_client()
        original_setup_hook = bot_client.bot.setup_hook
        main.setup_signal_handlers(bot_client)

        self.assertIsNot(bot_client.bot.setup_hook, original_setup_hook)

        loop = MagicMock()
        with patch("main.asyncio.get_running_loop", return_value=loop):
            asyncio.run(bot_client.bot.setup_hook())

        self.assertEqual(loop.add_signal_handler.call_count, 2)
        original_setup_hook.assert_awaited_once_with()

    def test_signal_handler_schedules_full_shutdown(self):
        bot_client = make_bot_client()
        _, handle_signal, _ = main.setup_signal_handlers(bot_client)

        loop = MagicMock()
        handle_signal(signal.SIGTERM, loop)
        run_scheduled_shutdown(loop)

        bot_client.services.restart_handler.perform_graceful_shutdown.assert_awaited_once_with()
        bot_client.services.auto_update_service.stop.assert_called_once_with()
        bot_client.services.death_service.stop.assert_called_once_with()
        bot_client.services.queue_service.stop.assert_awaited_once_with()
        bot_client.services.openai_service.close.assert_awaited_once_with()
        bot_client.bot.close.assert_awaited_once_with()

    def test_second_signal_does_not_spawn_duplicate_shutdown_task(self):
        bot_client = make_bot_client()
        _, handle_signal, _ = main.setup_signal_handlers(bot_client)

        loop = MagicMock()
        with patch("builtins.print") as mock_print:
            handle_signal(signal.SIGINT, loop)
            handle_signal(signal.SIGINT, loop)
            handle_signal(signal.SIGTERM, loop)

        self.assertEqual(loop.create_task.call_count, 1)
        self.assertTrue(
            any("already in progress" in call.args[0] for call in mock_print.call_args_list)
        )
        # Clean up the single scheduled coroutine
        loop.create_task.call_args.args[0].close()

    def test_shutdown_continues_when_a_service_stop_raises(self):
        bot_client = make_bot_client()
        bot_client.services.auto_update_service.stop.side_effect = RuntimeError("boom")
        _, handle_signal, _ = main.setup_signal_handlers(bot_client)

        loop = MagicMock()
        handle_signal(signal.SIGTERM, loop)
        run_scheduled_shutdown(loop)

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
        _, handle_signal, _ = main.setup_signal_handlers(bot_client)

        loop = MagicMock()
        with patch("builtins.print") as mock_print:
            handle_signal(signal.SIGTERM, loop)
            run_scheduled_shutdown(loop)

        bot_client.services.death_service.stop.assert_called_once_with()
        bot_client.services.queue_service.stop.assert_awaited_once_with()
        bot_client.services.openai_service.close.assert_awaited_once_with()
        bot_client.bot.close.assert_awaited_once_with()
        self.assertTrue(any("Error closing Discord bot" in call.args[0] for call in mock_print.call_args_list))

    def test_shutdown_handles_cancelled_error_cleanly(self):
        bot_client = make_bot_client()
        bot_client.services.restart_handler.perform_graceful_shutdown = AsyncMock(side_effect=asyncio.CancelledError())
        _, handle_signal, _ = main.setup_signal_handlers(bot_client)

        loop = MagicMock()
        handle_signal(signal.SIGINT, loop)
        run_scheduled_shutdown(loop)

        bot_client.services.auto_update_service.stop.assert_not_called()
        bot_client.bot.close.assert_not_awaited()

    def test_shutdown_reports_top_level_shutdown_errors(self):
        bot_client = make_bot_client()
        bot_client.services.restart_handler.perform_graceful_shutdown = AsyncMock(side_effect=RuntimeError("boom"))
        _, handle_signal, _ = main.setup_signal_handlers(bot_client)

        loop = MagicMock()
        with patch("builtins.print") as mock_print:
            handle_signal(signal.SIGTERM, loop)
            run_scheduled_shutdown(loop)

        self.assertTrue(any("Error during shutdown" in call.args[0] for call in mock_print.call_args_list))

    def test_main_module_script_entrypoint_creates_client_registers_signals_and_runs(self):
        bot_client = MagicMock()

        with patch("src.startup.create_bot_client", return_value=bot_client):
            runpy.run_module("main", run_name="__main__")

        bot_client.run.assert_called_once_with()
        # setup_signal_handlers must have replaced the bot's setup_hook with
        # the signal-registering wrapper.
        self.assertEqual(bot_client.bot.setup_hook.__name__, "setup_hook_with_signals")
