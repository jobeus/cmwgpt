"""Unit tests for explicit config loading."""

import unittest
from unittest.mock import mock_open, patch

import src.config as config_module
from src.config import (
    DEFAULT_SYSTEM_PROMPT,
    load_config,
    load_system_prompt,
    resolve_env_file_path,
)


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

    @patch("src.config.load_resolved_env_file")
    def test_load_config_can_control_dotenv_loading(self, mock_load_resolved_env_file):
        load_config(env={}, load_env_file=True)
        mock_load_resolved_env_file.assert_called_once_with({})

    @patch("src.config.find_dotenv")
    def test_resolve_env_file_path_prefers_explicit_env_file(self, mock_find_dotenv):
        result = resolve_env_file_path({"CMWGPT_ENV_FILE": ".env.production"})

        self.assertEqual(result, ".env.production")
        mock_find_dotenv.assert_not_called()

    @patch("src.config.find_dotenv")
    def test_resolve_env_file_path_prefers_dotenv_production_over_dotenv(self, mock_find_dotenv):
        mock_find_dotenv.side_effect = ["/tmp/.env.production", "/tmp/.env"]

        result = resolve_env_file_path({})

        self.assertEqual(result, "/tmp/.env.production")
        mock_find_dotenv.assert_called_once_with(".env.production", usecwd=True)

    @patch("src.config.find_dotenv")
    def test_resolve_env_file_path_falls_back_to_dotenv(self, mock_find_dotenv):
        mock_find_dotenv.side_effect = ["", "/tmp/.env"]

        result = resolve_env_file_path({})

        self.assertEqual(result, "/tmp/.env")

    def test_get_config_loads_once_and_caches(self):
        config_module.reset_config_cache()
        try:
            with patch.object(
                config_module, "load_config", wraps=config_module.load_config
            ) as mock_load:
                first = config_module.get_config()
                second = config_module.get_config()

            self.assertIs(first, second)
            mock_load.assert_called_once()
        finally:
            config_module.reset_config_cache()

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
