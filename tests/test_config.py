"""Unit tests for explicit config loading."""

import importlib
import unittest
from unittest.mock import mock_open, patch

import src.config as config_module
from src.config import DEFAULT_SYSTEM_PROMPT, load_config, load_system_prompt


class TestConfig(unittest.TestCase):
    def test_load_config_uses_explicit_mapping(self):
        config = load_config(
            env={
                "DEFAULT_MODEL": "gpt-test",
                "REPLY_TO_MENTIONS": "false",
                "INCLUDE_NUM_CHATLINES": "42",
            },
            load_env_file=False,
        )

        self.assertEqual(config.default_model, "gpt-test")
        self.assertFalse(config.reply_to_mentions)
        self.assertEqual(config.include_num_chatlines, 42)

    @patch("src.config.load_dotenv")
    def test_load_config_can_control_dotenv_loading(self, mock_load_dotenv):
        load_config(env={}, load_env_file=True)
        mock_load_dotenv.assert_called_once()

    @patch("dotenv.load_dotenv")
    def test_legacy_config_constants_still_load_dotenv_on_import(self, mock_load_dotenv):
        importlib.reload(config_module)
        mock_load_dotenv.assert_called()
        importlib.reload(config_module)

    def test_load_system_prompt_replaces_timestamp(self):
        with patch(
            "builtins.open",
            mock_open(read_data="Today is [[CURRENT_DATE_AND_TIME]]"),
        ):
            result = load_system_prompt()

        self.assertNotIn("[[CURRENT_DATE_AND_TIME]]", result)
        self.assertRegex(result, r"\d{4}-\d{2}-\d{2}")

    def test_load_system_prompt_falls_back_when_file_missing(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = load_system_prompt()

        self.assertEqual(result, DEFAULT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()