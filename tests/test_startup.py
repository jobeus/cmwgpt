"""Integration tests for real service wiring."""

import asyncio
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import load_config
import src.startup as startup
from src.startup import create_services, shutdown_services


def make_shutdown_services(**overrides):
    services = SimpleNamespace(
        auto_update_service=MagicMock(),
        memory_service=MagicMock(),
        death_service=MagicMock(),
        queue_service=MagicMock(),
        openai_service=MagicMock(),
        gemini_service=MagicMock(),
        anthropic_service=MagicMock(),
        runpod_service=MagicMock(),
    )
    services.death_service.close = AsyncMock()
    services.queue_service.stop = AsyncMock()
    services.openai_service.close = AsyncMock()
    services.gemini_service.close = AsyncMock()
    services.anthropic_service.close = AsyncMock()
    services.runpod_service.close = AsyncMock()
    for name, value in overrides.items():
        setattr(services, name, value)
    return services


class TestStartupIntegration(unittest.TestCase):
    def test_create_services_wires_real_services_together(self):
        config = load_config(
            env={
                "DEFAULT_MODEL": "integration-model",
                "KEEP_UP_TO_DATE_WITH_GIT": "false",
                "QUIET_UPDATES": "true",
                "DEATH_CHANNEL_ID": "999",
                "INCLUDE_NUM_CHATLINES": "7",
            },
            load_env_file=False,
        )

        services = create_services(config)

        self.assertIs(services.message_service._paste_service, services.paste_service)
        self.assertIs(services.auto_update_service._queue_service, services.queue_service)
        self.assertIs(services.announcement_service._state_service, services.state_service)
        self.assertIs(services.death_service._state_service, services.state_service)
        self.assertIs(services.death_service._openai_service, services.openai_service)
        self.assertIs(services.death_service._gemini_service, services.gemini_service)
        self.assertIs(services.death_service._anthropic_service, services.anthropic_service)
        self.assertIs(services.mention_handler._queue_service, services.queue_service)
        self.assertEqual(services.death_service._death_channel_id, "999")
        self.assertEqual(services.mention_handler._include_num_chatlines, 7)

    def test_create_bot_client_uses_load_config_services_and_discord_bot_client(self):
        fake_config = SimpleNamespace(name="config")
        fake_services = SimpleNamespace(mention_handler="mention-handler")
        fake_module = types.ModuleType("src.bot.client")

        class FakeDiscordBotClient:
            def __init__(self, *, config, services, mention_handler):
                self.config = config
                self.services = services
                self.mention_handler = mention_handler

        fake_module.DiscordBotClient = FakeDiscordBotClient

        with patch.object(startup, "load_config", return_value=fake_config), patch.object(
            startup, "create_services", return_value=fake_services
        ), patch.dict(sys.modules, {"src.bot.client": fake_module}):
            client = startup.create_bot_client()

        self.assertIs(client.config, fake_config)
        self.assertIs(client.services, fake_services)
        self.assertEqual(client.mention_handler, "mention-handler")


class TestShutdownServices(unittest.TestCase):
    def _run(self, services, flush=None, close_pool=None):
        flush = MagicMock() if flush is None else flush
        close_pool = AsyncMock() if close_pool is None else close_pool
        with patch.object(startup, "flush_all_caches", flush), patch.object(
            startup, "close_db_pool", close_pool
        ):
            asyncio.run(shutdown_services(services))
        return flush, close_pool

    def test_stops_and_closes_every_service_in_order(self):
        services = make_shutdown_services()
        order = MagicMock()
        order.attach_mock(services.auto_update_service.stop, "auto_update_stop")
        order.attach_mock(services.memory_service.stop, "memory_stop")
        order.attach_mock(services.death_service.close, "death_close")
        order.attach_mock(services.queue_service.stop, "queue_stop")
        order.attach_mock(services.openai_service.close, "openai_close")
        order.attach_mock(services.gemini_service.close, "gemini_close")
        order.attach_mock(services.anthropic_service.close, "anthropic_close")
        order.attach_mock(services.runpod_service.close, "runpod_close")
        flush = MagicMock()
        close_pool = AsyncMock()
        order.attach_mock(flush, "flush")
        order.attach_mock(close_pool, "close_pool")

        self._run(services, flush, close_pool)

        self.assertEqual(
            [c[0] for c in order.mock_calls],
            [
                "auto_update_stop", "memory_stop", "death_close", "queue_stop",
                "openai_close", "gemini_close", "anthropic_close", "runpod_close",
                "flush", "close_pool",
            ],
        )
        services.death_service.close.assert_awaited_once_with()
        services.queue_service.stop.assert_awaited_once_with()
        close_pool.assert_awaited_once_with()

    def test_each_step_failure_is_logged_and_later_steps_still_run(self):
        services = make_shutdown_services()
        services.auto_update_service.stop.side_effect = RuntimeError("a")
        services.memory_service.stop.side_effect = RuntimeError("b")
        services.death_service.close.side_effect = RuntimeError("c")
        services.queue_service.stop.side_effect = RuntimeError("d")
        services.openai_service.close.side_effect = RuntimeError("e")
        services.gemini_service.close.side_effect = RuntimeError("f")
        services.anthropic_service.close.side_effect = RuntimeError("g")
        services.runpod_service.close.side_effect = RuntimeError("h")
        flush = MagicMock(side_effect=RuntimeError("i"))
        close_pool = AsyncMock()

        with self.assertLogs("src.startup", level="ERROR") as logs:
            self._run(services, flush, close_pool)

        self.assertEqual(len(logs.records), 9)
        self.assertTrue(any("queue drain failed" in r.getMessage() for r in logs.records))
        services.runpod_service.close.assert_awaited_once_with()
        flush.assert_called_once_with()
        close_pool.assert_awaited_once_with()

    def test_optional_services_may_be_absent(self):
        services = make_shutdown_services(memory_service=None, anthropic_service=None)
        flush, close_pool = self._run(services)
        services.queue_service.stop.assert_awaited_once_with()
        flush.assert_called_once_with()
        close_pool.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
