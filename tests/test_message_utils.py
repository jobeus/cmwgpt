"""Tests for stateless message formatting helpers."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from src.utils import message_utils


class TestMessageUtils(unittest.TestCase):
    def test_format_attachment_message_handles_single_and_list(self):
        attachment = SimpleNamespace(url="https://cdn.example/one.png")
        second = SimpleNamespace(url="https://cdn.example/two.png")

        self.assertEqual(
            message_utils.format_attachment_message(attachment, "hello"),
            "> https://cdn.example/one.png\n> hello",
        )
        self.assertEqual(
            message_utils.format_attachment_message([attachment, None, second], "hello"),
            "> https://cdn.example/one.png\n> https://cdn.example/two.png\n> hello",
        )

    def test_format_prompt_message_prefixes_prompt(self):
        self.assertEqual(message_utils.format_prompt_message("hello"), "> hello")

    def test_format_discord_timestamp_uses_tz_env(self):
        created_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        with patch.dict("os.environ", {"TZ": "America/Denver"}, clear=False):
            formatted = message_utils.format_discord_timestamp(created_at)

        self.assertEqual(formatted, "2024-01-01 05:00:00")

    def test_clean_openai_response_handles_quotes_prefixes_mentions_and_escapes(self):
        raw = '"[2024-01-01 12:00:00] [123456] <@999>: assistant: Hello\\nworld"'

        cleaned = message_utils.clean_openai_response(raw, bot_id=999)

        self.assertEqual(cleaned, "Hello\nworld")

    def test_clean_openai_response_fixes_broken_mentions_and_keeps_nonempty_original(self):
        raw = '<@1234: hi there'

        cleaned = message_utils.clean_openai_response(raw)

        self.assertEqual(cleaned, 'hi there')

    def test_clean_openai_response_returns_original_when_stripping_would_empty_it(self):
        raw = 'assistant: '

        cleaned = message_utils.clean_openai_response(raw)

        self.assertEqual(cleaned, 'assistant:')
