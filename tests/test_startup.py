"""Integration tests for real service wiring."""

import unittest

from src.config import load_config
from src.services.message_service import message_service as global_message_service
from src.services.queue_service import queue_service as global_queue_service
from src.services.state_service import state_service as global_state_service
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
        self.assertIs(services.interject_service._message_service, services.message_service)
        self.assertIs(services.death_service._state_service, services.state_service)
        self.assertIs(services.mention_handler._queue_service, services.queue_service)
        self.assertEqual(services.death_service._death_channel_id, "999")
        self.assertEqual(services.mention_handler._include_num_chatlines, 7)


if __name__ == "__main__":
    unittest.main()