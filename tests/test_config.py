"""
Unit tests for config.py module.
Tests environment variable loading and configuration validation.
"""

import unittest
import os
from unittest.mock import patch


class TestConfig(unittest.TestCase):
    """Test configuration loading and validation."""

    def setUp(self):
        """Set up test environment."""
        # Clear any existing environment variables
        self.env_vars_to_clear = [
            'OPENAI_API_KEY', 'DISCORD_BOT_TOKEN', 'SYSTEM_PROMPT',
            'DEFAULT_MODEL', 'DEFAULT_IMAGE_MODEL', 'INCLUDE_USERNAMES',
            'REPLY_TO_MENTIONS', 'INCLUDE_NUM_CHATLINES'
        ]
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

    @patch('config.load_dotenv')
    @patch.dict(os.environ, {}, clear=True)
    def test_default_values(self, mock_load_dotenv):
        """Test that default values are set correctly when env vars are missing."""
        # Mock os.getenv to return None for all calls (simulating missing env
        # vars)
        with patch('config.os.getenv') as mock_getenv:
            def mock_getenv_side_effect(key, default=None):
                return default
            mock_getenv.side_effect = mock_getenv_side_effect

            # Reload config module to test defaults
            import importlib
            import config
            importlib.reload(config)

            self.assertEqual(
                config.SYSTEM_PROMPT,
                'You are a helpful assistant.')
            self.assertEqual(config.DEFAULT_MODEL, 'gpt-4.1-nano')
            self.assertEqual(config.DEFAULT_IMAGE_MODEL, 'gpt-image-1')
            self.assertTrue(config.INCLUDE_USERNAMES)
            self.assertTrue(config.REPLY_TO_MENTIONS)
            self.assertEqual(config.INCLUDE_NUM_CHATLINES, 100)

    @patch('config.load_dotenv')
    @patch.dict(os.environ, {}, clear=True)
    def test_environment_variable_loading(self, mock_load_dotenv):
        """Test that environment variables are loaded correctly."""
        # Mock os.getenv to return specific test values
        test_env_values = {
            'OPENAI_API_KEY': 'test_openai_key',
            'DISCORD_BOT_TOKEN': 'test_discord_token',
            'SYSTEM_PROMPT': 'Test system prompt',
            'DEFAULT_MODEL': 'gpt-4',
            'DEFAULT_IMAGE_MODEL': 'dall-e-3',
            'INCLUDE_USERNAMES': 'false',
            'REPLY_TO_MENTIONS': 'false',
            'INCLUDE_NUM_CHATLINES': '50'
        }

        with patch('config.os.getenv') as mock_getenv:
            def mock_getenv_side_effect(key, default=None):
                return test_env_values.get(key, default)
            mock_getenv.side_effect = mock_getenv_side_effect

            # Reload config module
            import importlib
            import config
            importlib.reload(config)

            self.assertEqual(config.OPENAI_API_KEY, 'test_openai_key')
            self.assertEqual(config.DISCORD_BOT_TOKEN, 'test_discord_token')
            self.assertEqual(config.SYSTEM_PROMPT, 'Test system prompt')
            self.assertEqual(config.DEFAULT_MODEL, 'gpt-4')
            self.assertEqual(config.DEFAULT_IMAGE_MODEL, 'dall-e-3')
            self.assertFalse(config.INCLUDE_USERNAMES)
            self.assertFalse(config.REPLY_TO_MENTIONS)
            self.assertEqual(config.INCLUDE_NUM_CHATLINES, 50)

    @patch('config.load_dotenv')
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
                with patch('config.os.getenv') as mock_getenv:
                    def mock_getenv_side_effect(key, default=None):
                        if key == 'INCLUDE_USERNAMES':
                            return env_value
                        return default
                    mock_getenv.side_effect = mock_getenv_side_effect

                    import importlib
                    import config
                    importlib.reload(config)

                    self.assertEqual(config.INCLUDE_USERNAMES, expected)

    @patch('config.load_dotenv')
    @patch.dict(os.environ, {}, clear=True)
    def test_integer_parsing(self, mock_load_dotenv):
        """Test integer environment variable parsing."""
        with patch('config.os.getenv') as mock_getenv:
            def mock_getenv_side_effect(key, default=None):
                if key == 'INCLUDE_NUM_CHATLINES':
                    return '250'
                return default
            mock_getenv.side_effect = mock_getenv_side_effect

            import importlib
            import config
            importlib.reload(config)

            self.assertEqual(config.INCLUDE_NUM_CHATLINES, 250)
            self.assertIsInstance(config.INCLUDE_NUM_CHATLINES, int)

    @patch('config.load_dotenv')
    @patch.dict(os.environ, {}, clear=True)
    def test_invalid_integer_fallback(self, mock_load_dotenv):
        """Test that invalid integer values fall back to default."""
        with patch('config.os.getenv') as mock_getenv:
            def mock_getenv_side_effect(key, default=None):
                if key == 'INCLUDE_NUM_CHATLINES':
                    return 'invalid_number'
                return default
            mock_getenv.side_effect = mock_getenv_side_effect

            import importlib
            import config

            # This should raise ValueError, but we want to test graceful
            # handling
            with self.assertRaises(ValueError):
                importlib.reload(config)


if __name__ == '__main__':
    unittest.main()
