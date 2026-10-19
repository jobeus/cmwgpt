"""
Unit tests for the StateService class.
Tests per-channel settings, settings-file persistence and restart state.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from src.services.state_service import StateService


class TestStateService(unittest.TestCase):
    """Test thread-safe state management."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_settings_file = os.path.join(self.temp_dir, "settings.json")
        self._env_patch = patch.dict(os.environ, {"CMWGPT_SETTINGS_FILE": self.temp_settings_file})
        self._env_patch.start()
        self.state_service = StateService()

    def tearDown(self):
        self._env_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

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

    def test_draw_edit_death_active_and_git_sha_management(self):
        self.state_service.set_draw_model(1, "draw-model")
        self.state_service.set_edit_model(1, "edit-model")
        self.state_service.set_death_settings({"interval": 30})
        self.state_service.mark_channel_active(1)
        self.state_service.set_last_git_sha("abc123")

        self.assertEqual(self.state_service.get_draw_model(1), "draw-model")
        self.assertEqual(self.state_service.get_edit_model(1), "edit-model")
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

    # -- settings file --------------------------------------------------------

    def test_settings_persist_to_file_and_reload(self):
        self.state_service.set_model(1, "gpt-test")
        self.state_service.set_draw_model(1, "seedream")
        self.state_service.set_edit_model(2, "qwen")
        self.state_service.set_system_prompt(1, "be helpful")
        self.state_service.set_death_settings({"interval": 30})

        with open(self.temp_settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["models"], {"1": "gpt-test"})
        self.assertEqual(data["draw_models"], {"1": "seedream"})
        self.assertEqual(data["edit_models"], {"2": "qwen"})
        self.assertEqual(data["system_prompts"], {"1": "be helpful"})
        self.assertEqual(data["death_settings"], {"interval": 30})

        reloaded = StateService()
        self.assertEqual(reloaded.get_model(1), "gpt-test")
        self.assertEqual(reloaded.get_draw_model(1), "seedream")
        self.assertEqual(reloaded.get_edit_model(2), "qwen")
        self.assertEqual(reloaded.get_system_prompt(1), "be helpful")
        self.assertEqual(reloaded.get_death_settings(), {"interval": 30})

    def test_cleared_system_prompt_is_omitted_from_settings_file(self):
        self.state_service.set_system_prompt(1, "be helpful")
        self.state_service.clear_system_prompt(1)

        with open(self.temp_settings_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["system_prompts"], {})
        self.assertIsNone(StateService().get_system_prompt(1))

    def test_corrupt_settings_file_starts_empty(self):
        with open(self.temp_settings_file, "w", encoding="utf-8") as f:
            f.write("not json")
        service = StateService()
        self.assertIsNone(service.get_model(1))
        self.assertIsNone(service.get_death_settings())

    # -- restart state --------------------------------------------------------

    def test_save_state_to_temp_file_writes_only_restart_fields(self):
        self.state_service.set_model(1, "gpt-test")
        self.state_service.set_death_settings({"interval": 30})
        self.state_service.mark_channel_active(1)
        self.state_service.set_last_git_sha("abc123")

        with patch("src.services.state_service.time.time", return_value=1234567890), patch(
            "src.services.state_service.os.getpid", return_value=4321
        ):
            temp_file = self.state_service.save_state_to_temp_file({"manual_restart": True})

        try:
            self.assertEqual(temp_file, "/tmp/cmwgpt_state_backup_1234567890_4321.json")
            self.assertEqual(os.stat(temp_file).st_mode & 0o777, 0o600)
            with open(temp_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.assertEqual(
                state,
                {
                    "active_channels": [1],
                    "last_git_sha": "abc123",
                    "timestamp": 1234567890,
                    "pid": 4321,
                    "restart_info": {"manual_restart": True},
                },
            )
        finally:
            if temp_file and os.path.exists(temp_file):
                os.remove(temp_file)

    def test_save_state_to_temp_file_omits_restart_info_when_not_given_and_handles_failure(self):
        with patch("src.services.state_service.time.time", return_value=1234567891), patch(
            "src.services.state_service.os.getpid", return_value=4322
        ):
            temp_file = self.state_service.save_state_to_temp_file()
        try:
            with open(temp_file, "r", encoding="utf-8") as f:
                self.assertNotIn("restart_info", json.load(f))
        finally:
            os.remove(temp_file)

        with patch("src.services.state_service.os.open", side_effect=OSError("denied")):
            self.assertIsNone(self.state_service.save_state_to_temp_file())

    def test_restart_info_round_trip(self):
        self.assertEqual(self.state_service.restart_info, {})
        self.state_service.mark_channel_active(5)
        self.state_service.set_last_git_sha("abc123")

        pattern = os.path.join(self.temp_dir, "cmwgpt_state_backup_*.json")
        saved = self.state_service.save_state_to_temp_file({"manual_restart": True})
        self.assertIsNotNone(saved)
        # Move the file into the test dir so other /tmp state files don't interfere.
        shutil.move(saved, self._state_file(os.path.basename(saved)))

        restored = StateService()
        with patch("src.services.state_service.STATE_FILE_PATTERN", pattern):
            self.assertTrue(restored.load_state_from_temp_files())

        self.assertEqual(restored.get_active_channels(), {5})
        self.assertEqual(restored.get_last_git_sha(), "abc123")
        self.assertEqual(restored.restart_info, {"manual_restart": True})
        # The property hands out a copy.
        restored.restart_info["manual_restart"] = False
        self.assertTrue(restored.restart_info["manual_restart"])

    def test_load_state_from_temp_files_skips_invalid_then_loads_valid_and_cleans_up(self):
        # Sorted newest-first, so the invalid file (later name) is tried first.
        valid = self._state_file("cmwgpt_state_backup_1_1.json")
        invalid = self._state_file("cmwgpt_state_backup_2_2.json")
        with open(invalid, "w", encoding="utf-8") as f:
            f.write("{not json")
        with open(valid, "w", encoding="utf-8") as f:
            json.dump({"active_channels": ["5"], "last_git_sha": "abc123"}, f)

        with patch("src.services.state_service.glob.glob", return_value=[valid, invalid]):
            loaded = self.state_service.load_state_from_temp_files()

        self.assertTrue(loaded)
        self.assertEqual(self.state_service.get_active_channels(), {5})
        self.assertEqual(self.state_service.get_last_git_sha(), "abc123")
        self.assertEqual(self.state_service.restart_info, {})
        self.assertFalse(os.path.exists(invalid))
        self.assertFalse(os.path.exists(valid))

    def test_load_state_from_temp_files_handles_missing_files_and_remove_errors(self):
        with patch("src.services.state_service.glob.glob", return_value=[]):
            self.assertFalse(self.state_service.load_state_from_temp_files())

        path = self._state_file("cmwgpt_state_backup_3_3.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"active_channels": [7]}, f)
        with patch("src.services.state_service.glob.glob", return_value=[path]), patch(
            "src.services.state_service.os.remove", side_effect=OSError("denied")
        ):
            self.assertTrue(self.state_service.load_state_from_temp_files())
        self.assertEqual(self.state_service.get_active_channels(), {7})


if __name__ == "__main__":
    unittest.main()
