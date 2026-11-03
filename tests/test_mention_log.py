"""Tests for the per-mention JSON log and the usage stats it collects."""

import json
import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services import mention_log
from src.services.anthropic_service import _note_usage
from src.services.cost_service import cost_scope, note_stat
from src.services.mention_log import log_mention, mention_record, setup_mention_log


def _message(content="<@999> who won the game?", reply_author_id=None):
    resolved = SimpleNamespace(author=SimpleNamespace(id=reply_author_id)) if reply_author_id else None
    return SimpleNamespace(
        id=5,
        created_at=datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc),
        guild=SimpleNamespace(id=10),
        channel=SimpleNamespace(id=20, name="general"),
        author=SimpleNamespace(id=30, name="jobe"),
        content=content,
        attachments=[],
        reference=SimpleNamespace(message_id=4, resolved=resolved) if reply_author_id else None,
    )


class TestNoteStat(unittest.TestCase):
    def test_numbers_add_up_and_strings_collect(self):
        with cost_scope(user_id=1) as ledger:
            note_stat("web_searches", 2)
            note_stat("web_searches", 1)
            note_stat("tools", "draw")
            note_stat("tools", "recall_users")
        self.assertEqual(ledger.stats, {"web_searches": 3, "tools": ["draw", "recall_users"]})

    def test_outside_a_scope_it_does_nothing(self):
        note_stat("tools", "draw")  # no error

    def test_a_response_adds_its_tokens_and_searches(self):
        usage = SimpleNamespace(
            input_tokens=10, output_tokens=200, cache_read_input_tokens=12000, cache_creation_input_tokens=300,
            server_tool_use=SimpleNamespace(web_search_requests=2),
        )
        with cost_scope() as ledger:
            _note_usage(SimpleNamespace(model="claude-sonnet-5-5", usage=usage))
            _note_usage(SimpleNamespace(model="claude-sonnet-5-5", usage=usage))
        self.assertEqual(ledger.stats["requests"], 2)
        self.assertEqual(ledger.stats["output_tokens"], 400)
        self.assertEqual(ledger.stats["cache_read_input_tokens"], 24000)
        self.assertEqual(ledger.stats["web_searches"], 4)
        self.assertEqual(ledger.stats["served_by"], ["claude-sonnet-5-5", "claude-sonnet-5-5"])


class TestMentionRecord(unittest.TestCase):
    def test_keeps_the_whole_text_and_the_usage(self):
        text = "<@999> " + "long question " * 200
        with cost_scope(user_id=30, channel_id=20) as ledger:
            ledger.add("claude-direct", 0.0123)
            note_stat("tools", "search_messages")
            ledger.stats["reply_chars"] = 120
        record = mention_record(_message(text), 999, "sonnet-5.5-direct", ledger, seconds=4.567)

        self.assertEqual(record["text"], text)
        self.assertEqual(record["ts"], "2026-11-03T18:00:00+00:00")
        self.assertEqual((record["guild_id"], record["channel"], record["user"]), (10, "general", "jobe"))
        self.assertEqual(record["model"], "sonnet-5.5-direct")
        self.assertEqual(record["cost"], 0.0123)
        self.assertEqual(record["tools"], ["search_messages"])
        self.assertEqual(record["reply_chars"], 120)
        self.assertEqual(record["seconds"], 4.57)
        self.assertIsNone(record["error"])
        self.assertFalse(record["reply_to_bot"])

    def test_marks_replies_to_the_bot_and_reminder_tasks(self):
        with cost_scope() as ledger:
            pass
        record = mention_record(_message(reply_author_id=999), 999, "opus-5.5-direct", ledger,
                                seconds=1.0, task="look up the score")
        self.assertTrue(record["reply_to_bot"])
        self.assertEqual(record["reply_to"], 4)
        self.assertEqual(record["reminder_task"], "look up the score")

    def test_writes_one_json_line_per_mention(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "logs" / "mentions.jsonl"
            setup_mention_log(path)
            try:
                log_mention({"text": "héllo", "cost": 0.01})
                log_mention({"text": "again", "cost": 0.02})
            finally:
                for handler in mention_log._mention_logger.handlers[:]:
                    handler.close()
                    mention_log._mention_logger.removeHandler(handler)
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["text"] for line in lines], ["héllo", "again"])

    def test_stays_out_of_the_main_log(self):
        self.assertFalse(logging.getLogger("cmwgpt.mentions").propagate)


class TestHandlerLogsEachMention(unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_reply_is_logged_with_its_error(self):
        from tests.test_mention_handler import AsyncTyping, TestMentionHandler

        handler, *_ = TestMentionHandler.make_handler(TestMentionHandler())
        handler._prepare_mention_context = AsyncMock(side_effect=RuntimeError("boom"))
        handler._send_error = AsyncMock()
        channel = MagicMock()
        channel.id = 20
        channel.typing.return_value = AsyncTyping()
        message = _message()
        message.channel = channel
        with patch("src.bot.handlers.mention.log_mention") as log:
            await handler.handle_mention(message, SimpleNamespace(id=999), "sonnet-5.5-direct")

        [record] = [c.args[0] for c in log.call_args_list]
        self.assertEqual(record["error"], "RuntimeError: boom")
        self.assertEqual(record["text"], "<@999> who won the game?")
        self.assertEqual(record["model"], "sonnet-5.5-direct")


if __name__ == "__main__":
    unittest.main()
