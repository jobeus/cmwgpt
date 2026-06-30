"""Integration tests for real service wiring."""

import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.config import load_config
from src.services.message_service import message_service as global_message_service
from src.services.queue_service import queue_service as global_queue_service
from src.services.state_service import state_service as global_state_service
import src.startup as startup
from src.startup import create_services


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

        self.assertIsNot(services.state_service, global_state_service)
        self.assertIsNot(services.queue_service, global_queue_service)
        self.assertIsNot(services.message_service, global_message_service)
        self.assertIs(services.message_service._paste_service, services.paste_service)
        self.assertIs(services.auto_update_service._queue_service, services.queue_service)
        self.assertIs(services.announcement_service._state_service, services.state_service)
        self.assertIs(services.death_service._state_service, services.state_service)
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


if __name__ == "__main__":
    unittest.main()
