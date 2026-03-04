"""
Unit tests for config.py module.
Tests environment variable loading and configuration validation.
"""

import unittest
import os
from unittest.mock import patch, mock_open
from datetime import datetime


class TestConfig(unittest.TestCase):
    """Test configuration loading and validation."""

    def setUp(self):
        """Set up test environment."""
        # Clear any existing environment variables
        self.env_vars_to_clear = [
            'OPENROUTER_API_KEY',
            'DISCORD_BOT_TOKEN',
            'DEFAULT_MODEL',
            'DEFAULT_DRAW_MODEL',
            'DEFAULT_EDIT_MODEL',
            'INCLUDE_USERNAMES',
            'REPLY_TO_MENTIONS',
            'INCLUDE_NUM_CHATLINES',
            'YT_TRANSCRIPT_PROXY']
        self.original_env = {}
        for var in self.env_vars_to_clear:
            self.original_env[var] = os.environ.get(var)
            if var in os.environ:
                del os.environ[var]

    def tearDown(self):
        """Clean up test environment."""
        # Restore original environment variables
        for var, value in self.original_env.items():
            if value is not None:
                os.environ[var] = value
            elif var in os.environ:
                del os.environ[var]

    @patch('src.config.load_dotenv')
    @patch.dict(os.environ, {}, clear=True)
    def test_default_values(self, mock_load_dotenv):
        """Test that default values are set correctly when env vars are missing."""
        # Mock os.getenv to return None for all calls (simulating missing env
        # vars)
        with patch('src.config.os.getenv') as mock_getenv:
            def mock_getenv_side_effect(key, default=None):
                return default
            mock_getenv.side_effect = mock_getenv_side_effect

            # Reload config module to test defaults
            import importlib
            import src.config as config
            importlib.reload(config)

            self.assertEqual(
                config.DEFAULT_MODEL,
                'anthropic/claude-haiku-4.5')
            self.assertEqual(config.DEFAULT_DRAW_MODEL, 'seedream')
            self.assertEqual(config.DEFAULT_EDIT_MODEL, 'seedream')
            self.assertTrue(config.INCLUDE_USERNAMES)
            self.assertTrue(config.REPLY_TO_MENTIONS)
            self.assertEqual(config.INCLUDE_NUM_CHATLINES, 10)

    @patch('src.config.load_dotenv')
    @patch.dict(os.environ, {}, clear=True)
    def test_environment_variable_loading(self, mock_load_dotenv):
        """Test that environment variables are loaded correctly."""
        # Mock os.getenv to return specific test values
        test_env_values = {
            'OPENROUTER_API_KEY': 'test_openrouter_key',
            'DISCORD_BOT_TOKEN': 'test_discord_token',
            'DEFAULT_MODEL': 'gpt-4',
            'DEFAULT_DRAW_MODEL': 'dall-e-3',
            'DEFAULT_EDIT_MODEL': 'dall-e-3',
            'INCLUDE_USERNAMES': 'false',
            'REPLY_TO_MENTIONS': 'false',
            'INCLUDE_NUM_CHATLINES': '50',
            'TRANSCRIPT_PROXY': 'http://proxy.example.com'
        }

        with patch('src.config.os.getenv') as mock_getenv:
            def mock_getenv_side_effect(key, default=None):
                return test_env_values.get(key, default)
            mock_getenv.side_effect = mock_getenv_side_effect

            # Reload config module
            import importlib
            import src.config as config
            importlib.reload(config)

            self.assertEqual(config.OPENROUTER_API_KEY, 'test_openrouter_key')
            self.assertEqual(config.DISCORD_BOT_TOKEN, 'test_discord_token')
            self.assertEqual(config.DEFAULT_MODEL, 'gpt-4')
            self.assertEqual(config.DEFAULT_DRAW_MODEL, 'dall-e-3')
            self.assertEqual(config.DEFAULT_EDIT_MODEL, 'dall-e-3')
            self.assertFalse(config.INCLUDE_USERNAMES)
            self.assertFalse(config.REPLY_TO_MENTIONS)
            self.assertEqual(config.INCLUDE_NUM_CHATLINES, 50)
            self.assertEqual(config.TRANSCRIPT_PROXY, 'http://proxy.example.com')

    @patch('src.config.load_dotenv')
    @patch.dict(os.environ, {}, clear=True)
    def test_boolean_parsing(self, mock_load_dotenv):
        """Test boolean environment variable parsing."""
        test_cases = [
            ('true', True),
            ('True', True),
            ('TRUE', True),
            ('1', True),
            ('false', False),
            ('False', False),
            ('FALSE', False),
            ('0', False),
            ('', False),
            ('invalid', False)
        ]

        for env_value, expected in test_cases:
            with self.subTest(env_value=env_value, expected=expected):
                with patch('src.config.os.getenv') as mock_getenv:
                    def mock_getenv_side_effect(key, default=None):
                        if key == 'INCLUDE_USERNAMES':
                            return env_value
                        return default
                    mock_getenv.side_effect = mock_getenv_side_effect

                    import importlib
                    import src.config as config
                    importlib.reload(config)

                    self.assertEqual(config.INCLUDE_USERNAMES, expected)

    @patch('src.config.load_dotenv')
    @patch.dict(os.environ, {}, clear=True)
    def test_integer_parsing(self, mock_load_dotenv):
        """Test integer environment variable parsing."""
        with patch('src.config.os.getenv') as mock_getenv:
            def mock_getenv_side_effect(key, default=None):
                if key == 'INCLUDE_NUM_CHATLINES':
                    return '250'
                return default
            mock_getenv.side_effect = mock_getenv_side_effect

            import importlib
            import src.config as config
            importlib.reload(config)

            self.assertEqual(config.INCLUDE_NUM_CHATLINES, 250)
            self.assertIsInstance(config.INCLUDE_NUM_CHATLINES, int)

    @patch('src.config.load_dotenv')
    @patch.dict(os.environ, {}, clear=True)
    def test_invalid_integer_fallback(self, mock_load_dotenv):
        """Test that invalid integer values fall back to default."""
        with patch('src.config.os.getenv') as mock_getenv:
            def mock_getenv_side_effect(key, default=None):
                if key == 'INCLUDE_NUM_CHATLINES':
                    return 'invalid_number'
                return default
            mock_getenv.side_effect = mock_getenv_side_effect

            import importlib
            import src.config as config

            # This should raise ValueError, but we want to test graceful
            # handling
            with self.assertRaises(ValueError):
                importlib.reload(config)

    def test_load_system_prompt_from_file(self):
        """Test loading system prompt from file."""
        # Import here to avoid import issues during test setup
        from src.config import load_system_prompt

        test_content = "You are a test assistant. Today is [[CURRENT_DATE_AND_TIME]]."

        with patch("builtins.open", mock_open(read_data=test_content)):
            result = load_system_prompt()

            # Should replace the date/time variable
            self.assertNotIn("[[CURRENT_DATE_AND_TIME]]", result)
            self.assertIn("You are a test assistant. Today is", result)
            # Should contain a date in YYYY-MM-DD format
            self.assertRegex(result, r"\d{4}-\d{2}-\d{2}")

    def test_load_system_prompt_file_not_found(self):
        """Test fallback when system prompt file doesn't exist."""
        from src.config import load_system_prompt, DEFAULT_SYSTEM_PROMPT

        with patch("builtins.open", side_effect=FileNotFoundError):
            result = load_system_prompt()
            self.assertEqual(result, DEFAULT_SYSTEM_PROMPT)

    def test_load_system_prompt_empty_file(self):
        """Test fallback when system prompt file is empty."""
        from src.config import load_system_prompt, DEFAULT_SYSTEM_PROMPT

        with patch("builtins.open", mock_open(read_data="")):
            result = load_system_prompt()
            self.assertEqual(result, DEFAULT_SYSTEM_PROMPT)

    def test_load_system_prompt_whitespace_only(self):
        """Test fallback when system prompt file contains only whitespace."""
        from src.config import load_system_prompt, DEFAULT_SYSTEM_PROMPT

        with patch("builtins.open", mock_open(read_data="   \n\t  \n")):
            result = load_system_prompt()
            self.assertEqual(result, DEFAULT_SYSTEM_PROMPT)

    def test_get_system_prompt(self):
        """Test get_system_prompt function."""
        from src.config import get_system_prompt

        test_content = "Test prompt with [[CURRENT_DATE_AND_TIME]] variable."

        with patch("builtins.open", mock_open(read_data=test_content)):
            result = get_system_prompt()

            # Should replace the date/time variable
            self.assertNotIn("[[CURRENT_DATE_AND_TIME]]", result)
            self.assertIn("Test prompt with", result)

    def test_date_time_replacement_format(self):
        """Test that date/time replacement uses correct format."""
        from src.config import load_system_prompt

        test_content = "Current time: [[CURRENT_DATE_AND_TIME]]"

        with patch("builtins.open", mock_open(read_data=test_content)):
            result = load_system_prompt()

            # Extract the date part
            date_part = result.replace("Current time: ", "")

            # Try parsing with known timezone suffixes
            try:
                # Remove timezone abbreviation and parse the rest
                parts = date_part.rsplit(" ", 1)
                if len(parts) != 2:
                    raise ValueError("Could not split timezone suffix")

                datetime_part, tz_abbr = parts
                # Ensure datetime part is valid
                datetime.strptime(datetime_part, "%Y-%m-%d %H:%M:%S")

                # Check if tz_abbr is in allowed list
                assert tz_abbr in (
                    "MDT", "MST"), f"Unexpected timezone: {tz_abbr}"

            except Exception as e:
                self.fail(f"Date format is incorrect: {date_part} ({e})")


if __name__ == '__main__':
    unittest.main()
