"""Tests for the per-channel day summary and how mentions use it."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from src.bot.handlers.mention import MentionHandler
from src.services.day_summary_service import DaySummaryService, day_start_for

UTC = timezone.utc
BOT_ID = 999
# 02:00 MDT on 2026-10-15
DAY_START = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


def make_msg(index, author_id=1, *, hours=1.0, content=None):
    return SimpleNamespace(
        id=1000 + index,
        author=SimpleNamespace(id=author_id, bot=author_id == BOT_ID, display_name=f"user{author_id}"),
        content=content or f"message {index}",
        embeds=[],
        attachments=[],
        reference=None,
        created_at=DAY_START + timedelta(hours=hours, minutes=index),
    )


class FakeChannel:
    def __init__(self, messages, channel_id=5):
        self.id = channel_id
        self.name = "general"
        self.messages = messages
        self.calls = []

    def history(self, *, limit, after, before, oldest_first):
        self.calls.append((after, before))

        def is_after(msg):
            if isinstance(after, datetime):
                return msg.created_at > after
            return msg.id > after.id

        async def gen():
            for msg in self.messages:
                if is_after(msg) and msg.id < before.id:
                    yield msg

        return gen()


def fake_dispatch(replies):
    """A dispatch_completion stand-in that returns ``replies`` in order and records prompts."""
    prompts = []

    async def dispatch(**kwargs):
        prompts.append(kwargs["messages"][0]["content"][0]["text"])
        return replies[len(prompts) - 1], 0.01

    return dispatch, prompts


class TestDayStart(unittest.TestCase):
    def test_day_start_is_latest_2am_denver(self):
        self.assertEqual(day_start_for(datetime(2026, 10, 15, 20, 0, tzinfo=UTC)), DAY_START)
        # 01:00 MDT belongs to the previous day
        self.assertEqual(
            day_start_for(datetime(2026, 10, 16, 7, 0, tzinfo=UTC)), DAY_START
        )
        # After DST ends, 02:00 MST is 09:00 UTC
        self.assertEqual(
            day_start_for(datetime(2026, 11, 5, 12, 0, tzinfo=UTC)).astimezone(UTC),
            datetime(2026, 11, 5, 9, 0, tzinfo=UTC),
        )


class TestDaySummaryService(unittest.IsolatedAsyncioTestCase):
    def make_service(self, now=DAY_START + timedelta(hours=12)):
        return DaySummaryService(openai_service=MagicMock(), now=lambda: now)

    async def test_summarizes_from_2am_up_to_the_history_window(self):
        messages = [make_msg(i) for i in range(5)]
        channel = FakeChannel(messages)
        service = self.make_service()
        dispatch, prompts = fake_dispatch(["talked about tacos"])

        with patch("src.services.day_summary_service.dispatch_completion", side_effect=dispatch):
            summary, cost = await service.get_summary(channel, messages[3], BOT_ID, "model-x")

        self.assertEqual((summary, cost), ("talked about tacos", 0.01))
        self.assertEqual(channel.calls[0][0], DAY_START)
        self.assertIn("message 2", prompts[0])
        self.assertNotIn("message 3", prompts[0])

    async def test_reuses_summary_while_window_holds_then_folds_only_the_gap(self):
        messages = [make_msg(i) for i in range(10)]
        channel = FakeChannel(messages)
        service = self.make_service()
        dispatch, prompts = fake_dispatch(["first", "first plus more"])

        with patch("src.services.day_summary_service.dispatch_completion", side_effect=dispatch):
            await service.get_summary(channel, messages[3], BOT_ID, "model-x")
            again = await service.get_summary(channel, messages[3], BOT_ID, "model-x")
            moved = await service.get_summary(channel, messages[7], BOT_ID, "model-x")

        self.assertEqual(again, ("first", 0.0))
        self.assertEqual(moved, ("first plus more", 0.01))
        self.assertEqual(len(prompts), 2)
        self.assertIn("Summary so far:\nfirst", prompts[1])
        # The gap starts with the old window's oldest message, which wasn't summarized yet.
        self.assertIn("message 3", prompts[1])
        self.assertIn("message 6", prompts[1])
        self.assertNotIn("message 2", prompts[1])
        self.assertNotIn("message 7", prompts[1])

    async def test_starts_over_at_2am(self):
        now = [DAY_START + timedelta(hours=12)]
        service = DaySummaryService(openai_service=MagicMock(), now=lambda: now[0])
        yesterday = FakeChannel([make_msg(i) for i in range(5)])
        dispatch, prompts = fake_dispatch(["yesterday", "today"])

        with patch("src.services.day_summary_service.dispatch_completion", side_effect=dispatch):
            await service.get_summary(yesterday, yesterday.messages[3], BOT_ID, "model-x")
            now[0] += timedelta(days=1)
            today = [make_msg(i, hours=25) for i in range(10, 15)]
            yesterday.messages += today
            summary, _ = await service.get_summary(yesterday, today[3], BOT_ID, "model-x")

        self.assertEqual(summary, "today")
        self.assertIn("Summary so far:\n(none)", prompts[1])
        self.assertNotIn("message 2\n", prompts[1])

    async def test_nothing_before_window_today_skips_model(self):
        service = self.make_service()
        oldest = make_msg(0, hours=-1)
        with patch("src.services.day_summary_service.dispatch_completion") as dispatch:
            self.assertEqual(await service.get_summary(FakeChannel([oldest]), oldest, BOT_ID, "m"), ("", 0.0))
        dispatch.assert_not_called()

    async def test_failure_keeps_previous_summary_and_retries_gap(self):
        messages = [make_msg(i) for i in range(10)]
        channel = FakeChannel(messages)
        service = self.make_service()
        calls = []

        async def dispatch(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise RuntimeError("api down")
            return f"summary {len(calls)}", 0.01

        with patch("src.services.day_summary_service.dispatch_completion", side_effect=dispatch):
            await service.get_summary(channel, messages[3], BOT_ID, "m")
            failed = await service.get_summary(channel, messages[7], BOT_ID, "m")
            retried = await service.get_summary(channel, messages[7], BOT_ID, "m")

        self.assertEqual(failed, ("summary 1", 0.0))
        self.assertEqual(retried, ("summary 3", 0.01))

    async def test_none_reply_means_no_summary(self):
        messages = [make_msg(i) for i in range(5)]
        dispatch, _ = fake_dispatch(["(none)"])
        with patch("src.services.day_summary_service.dispatch_completion", side_effect=dispatch):
            summary, _ = await self.make_service().get_summary(FakeChannel(messages), messages[3], BOT_ID, "m")
        self.assertEqual(summary, "")


class TestMentionDaySummary(unittest.IsolatedAsyncioTestCase):
    async def test_summaries_go_in_system_prompt_lately_first_and_cost_is_returned(self):
        provider = AsyncMock(return_value=("talked about tacos", 0.02))
        channel_provider = AsyncMock(return_value="## Lately in this channel\n<channel_summary>\ntrucks\n</channel_summary>")
        state_service = MagicMock()
        state_service.get_system_prompt.return_value = None
        handler = MentionHandler(
            state_service=state_service,
            openai_service=MagicMock(),
            message_service=MagicMock(),
            queue_service=MagicMock(),
            system_prompt_loader=lambda: "base",
            include_num_chatlines=10,
            mention_legend_provider=AsyncMock(return_value="legend"),
            url_content_fetcher=AsyncMock(return_value=("", [])),
            channel_summary_provider=channel_provider,
            day_summary_provider=provider,
        )
        history = [make_msg(i) for i in range(3)]
        mention = history[-1]

        async def channel_history(limit):
            for msg in reversed(history):
                yield msg

        mention.channel = SimpleNamespace(id=5, name="general", history=channel_history)
        mention.guild = SimpleNamespace(id=42)

        _, system_prompt, cost = await handler._prepare_mention_context(
            mention, SimpleNamespace(id=BOT_ID), model="model-x"
        )

        provider.assert_awaited_once_with(mention.channel, history[0], BOT_ID, "model-x")
        channel_provider.assert_awaited_once_with(42, 5)
        self.assertIn("<day_summary>\ntalked about tacos\n</day_summary>", system_prompt)
        self.assertLess(system_prompt.index("<channel_summary>"), system_prompt.index("<day_summary>"))
        self.assertEqual(cost, 0.02)


if __name__ == "__main__":
    unittest.main()
