"""
Unit tests for the StateService class.
Tests per-channel settings, their Redis persistence, restart state and the legacy file imports.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

import fakeredis
import redis

from src.db.state_store import StateStore
from src.services.state_service import StateService


class TestStateService(unittest.TestCase):
    """Test thread-safe state management."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_settings_file = os.path.join(self.temp_dir, "settings.json")
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.state_service = self._service()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _service(self, **kwargs):
        """A StateService on this test's Redis, with legacy files looked for in the test dir."""
        kwargs.setdefault("legacy_settings_file", self.temp_settings_file)
        kwargs.setdefault("legacy_restart_files", os.path.join(self.temp_dir, "cmwgpt_state_backup_*.json"))
        return StateService(StateStore(lambda: self.redis), **kwargs)

    def _state_file(self, name):
        return os.path.join(self.temp_dir, name)

    def test_model_management(self):
        self.state_service.set_model(12345, "gpt-5-mini")
        self.assertEqual(self.state_service.get_model(12345), "gpt-5-mini")
        self.assertIsNone(self.state_service.get_model(99999))

    def test_system_prompt_management(self):
        channel_id = 12345
        prompt = "You are a coding assistant."

        self.state_service.set_system_prompt(channel_id, prompt)
        self.assertEqual(self.state_service.get_system_prompt(channel_id), prompt)
        self.assertIsNone(self.state_service.get_system_prompt(99999))

        self.state_service.clear_system_prompt(channel_id)
        self.assertIsNone(self.state_service.get_system_prompt(channel_id))

    def test_image_model_death_active_and_git_sha_management(self):
        self.state_service.set_image_model(1, "image-model")
        self.state_service.set_death_settings({"interval": 30})
        self.state_service.mark_channel_active(1)
        self.state_service.set_last_git_sha("abc123")

        self.assertEqual(self.state_service.get_image_model(1), "image-model")
        self.assertEqual(self.state_service.get_death_settings(), {"interval": 30})
        self.assertEqual(self.state_service.get_active_channels(), {1})
        self.assertEqual(self.state_service.get_last_git_sha(), "abc123")

        self.state_service.clear_death_settings()
        self.assertIsNone(self.state_service.get_death_settings())

    def test_getters_return_copies(self):
        settings = {"interval": 30}
        self.state_service.set_death_settings(settings)
        settings["interval"] = 99
        self.assertEqual(self.state_service.get_death_settings(), {"interval": 30})

        self.state_service.mark_channel_active(1)
        channels = self.state_service.get_active_channels()
        channels.add(2)
        self.assertEqual(self.state_service.get_active_channels(), {1})

    def test_thread_safety(self):
        num_threads = 10
        operations_per_thread = 50
        errors = []

        def worker_thread(thread_id):
            try:
                channel_id = 12345 + thread_id
                for i in range(operations_per_thread):
                    model = f"model-{thread_id}-{i}"
                    prompt = f"Prompt for thread {thread_id}, iteration {i}"
                    self.state_service.set_model(channel_id, model)
                    self.state_service.set_system_prompt(channel_id, prompt)
                    self.state_service.mark_channel_active(channel_id)
                    if self.state_service.get_model(channel_id) != model:
                        errors.append(f"Model mismatch in thread {thread_id}")
                    if self.state_service.get_system_prompt(channel_id) != prompt:
                        errors.append(f"Prompt mismatch in thread {thread_id}")
            except Exception as e:
                errors.append(f"Thread {thread_id} failed: {e}")

        threads = [threading.Thread(target=worker_thread, args=(i,)) for i in range(num_threads)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(self.state_service.get_active_channels()), num_threads)

    # -- settings in Redis ----------------------------------------------------

    def test_settings_persist_to_redis_and_reload(self):
        self.state_service.set_model(1, "gpt-test")
        self.state_service.set_image_model(2, "gemini-3-pro-image")
        self.state_service.set_system_prompt(1, "be helpful")
        self.state_service.set_death_settings({"interval": 30})

        self.assertEqual(
            {k: json.loads(v) for k, v in self.redis.hgetall("cmwgpt:settings:channels").items()},
            {"1": {"model": "gpt-test", "system_prompt": "be helpful"}, "2": {"image_model": "gemini-3-pro-image"}},
        )
        self.assertEqual(json.loads(self.redis.get("cmwgpt:settings:death")), {"interval": 30})

        reloaded = self._service()
        self.assertEqual(reloaded.get_model(1), "gpt-test")
        self.assertEqual(reloaded.get_image_model(2), "gemini-3-pro-image")
        self.assertEqual(reloaded.get_system_prompt(1), "be helpful")
        self.assertEqual(reloaded.get_death_settings(), {"interval": 30})

    def test_cleared_settings_are_removed_from_redis(self):
        self.state_service.set_system_prompt(1, "be helpful")
        self.state_service.clear_system_prompt(1)
        self.assertFalse(self.redis.hexists("cmwgpt:settings:channels", "1"))
        self.assertIsNone(self._service().get_system_prompt(1))

        self.state_service.set_death_settings({"interval": 30})
        self.state_service.clear_death_settings()
        self.assertIsNone(self._service().get_death_settings())

    def test_unreadable_redis_values_are_ignored(self):
        self.redis.hset("cmwgpt:settings:channels", "1", "not json")
        self.redis.hset("cmwgpt:settings:channels", "x", json.dumps({"model": "m"}))
        self.redis.set("cmwgpt:settings:death", json.dumps([1, 2]))
        service = self._service()
        self.assertIsNone(service.get_model(1))
        self.assertIsNone(service.get_death_settings())

    def test_without_redis_settings_live_in_memory(self):
        service = StateService()
        service.set_model(1, "gpt-test")
        self.assertEqual(service.get_model(1), "gpt-test")
        self.assertFalse(service.save_restart_state())
        self.assertFalse(service.load_restart_state())

    def test_redis_outage_keeps_settings_in_memory(self):
        def down():
            raise redis.ConnectionError("refused")

        with self.assertLogs("src.db.state_store", "WARNING"):
            service = StateService(StateStore(down))
        service.set_model(1, "gpt-test")
        self.assertEqual(service.get_model(1), "gpt-test")

    def test_legacy_settings_file_is_imported_once(self):
        with open(self.temp_settings_file, "w", encoding="utf-8") as f:
            json.dump({
                "models": {"1": "gpt-test"},
                "image_models": {"2": "gemini-3-pro-image"},
                "system_prompts": {"1": "be helpful", "3": None},
                "death_settings": {"interval": 30},
            }, f)

        service = self._service()
        self.assertEqual(service.get_model(1), "gpt-test")
        self.assertEqual(service.get_image_model(2), "gemini-3-pro-image")
        self.assertEqual(service.get_system_prompt(1), "be helpful")
        self.assertEqual(service.get_death_settings(), {"interval": 30})
        self.assertFalse(os.path.exists(self.temp_settings_file))
        self.assertTrue(os.path.exists(self.temp_settings_file + ".migrated"))
        self.assertEqual(self._service().get_model(1), "gpt-test")

    def test_legacy_settings_file_is_left_alone_when_redis_has_settings_or_is_down(self):
        self.state_service.set_model(1, "from-redis")
        with open(self.temp_settings_file, "w", encoding="utf-8") as f:
            json.dump({"models": {"1": "from-file"}}, f)
        self.assertEqual(self._service().get_model(1), "from-redis")
        self.assertTrue(os.path.exists(self.temp_settings_file))

        def down():
            raise redis.ConnectionError("refused")

        with self.assertLogs("src.db.state_store", "WARNING"):
            StateService(StateStore(down), legacy_settings_file=self.temp_settings_file)
        self.assertTrue(os.path.exists(self.temp_settings_file))

    # -- restart state --------------------------------------------------------

    def test_save_restart_state_writes_only_restart_fields_with_a_ttl(self):
        self.state_service.set_model(1, "gpt-test")
        self.state_service.mark_channel_active(1)
        self.state_service.set_last_git_sha("abc123")

        # Not patching time.time: fakeredis expires keys by it.
        with patch("src.services.state_service.os.getpid", return_value=4321):
            self.assertTrue(self.state_service.save_restart_state({"manual_restart": True}))

        state = json.loads(self.redis.get("cmwgpt:restart_state"))
        self.assertIsInstance(state.pop("timestamp"), int)
        self.assertEqual(
            state,
            {
                "active_channels": [1],
                "last_git_sha": "abc123",
                "pid": 4321,
                "restart_info": {"manual_restart": True},
            },
        )
        self.assertGreater(self.redis.ttl("cmwgpt:restart_state"), 0)

        self.state_service.save_restart_state()
        self.assertNotIn("restart_info", json.loads(self.redis.get("cmwgpt:restart_state")))

    def test_restart_info_round_trip_takes_the_state(self):
        self.assertEqual(self.state_service.restart_info, {})
        self.state_service.mark_channel_active(5)
        self.state_service.set_last_git_sha("abc123")
        self.state_service.save_restart_state({"manual_restart": True})

        restored = self._service()
        self.assertTrue(restored.load_restart_state())
        self.assertEqual(restored.get_active_channels(), {5})
        self.assertEqual(restored.get_last_git_sha(), "abc123")
        self.assertEqual(restored.restart_info, {"manual_restart": True})
        # The property hands out a copy.
        restored.restart_info["manual_restart"] = False
        self.assertTrue(restored.restart_info["manual_restart"])
        # Taken: the start after this one isn't a restart.
        self.assertFalse(self._service().load_restart_state())

    def test_unreadable_restart_state_is_ignored(self):
        self.redis.set("cmwgpt:restart_state", json.dumps({"active_channels": ["x"]}))
        self.assertFalse(self.state_service.load_restart_state())

    def test_legacy_restart_files_skip_invalid_then_load_valid_and_clean_up(self):
        # Sorted newest-first, so the invalid file (later name) is tried first.
        valid = self._state_file("cmwgpt_state_backup_1_1.json")
        invalid = self._state_file("cmwgpt_state_backup_2_2.json")
        with open(invalid, "w", encoding="utf-8") as f:
            f.write("{not json")
        with open(valid, "w", encoding="utf-8") as f:
            json.dump({"active_channels": ["5"], "last_git_sha": "abc123"}, f)

        self.assertTrue(self.state_service.load_restart_state())
        self.assertEqual(self.state_service.get_active_channels(), {5})
        self.assertEqual(self.state_service.get_last_git_sha(), "abc123")
        self.assertEqual(self.state_service.restart_info, {})
        self.assertFalse(os.path.exists(invalid))
        self.assertFalse(os.path.exists(valid))

    def test_legacy_restart_files_handle_missing_files_and_remove_errors(self):
        self.assertFalse(self.state_service.load_restart_state())

        path = self._state_file("cmwgpt_state_backup_3_3.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"active_channels": [7]}, f)
        with patch("src.services.state_service.os.remove", side_effect=OSError("denied")):
            self.assertTrue(self.state_service.load_restart_state())
        self.assertEqual(self.state_service.get_active_channels(), {7})


if __name__ == "__main__":
    unittest.main()
