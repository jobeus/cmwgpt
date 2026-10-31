"""Tests for reminders, people's time zones, and the tools that set them."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
import fakeredis

from src.services.chat_tools import build_mention_tools
from src.services.reminder_service import (
    ReminderService, ReminderStore, format_when, parse_when, reminder_message,
)
from src.services.timezone_service import TimezoneStore, describe_zone, resolve_zone

UTC = timezone.utc
DENVER = ZoneInfo("America/Denver")
# Fri 2026-10-30 14:00 MDT
NOW = datetime(2026, 10, 30, 20, 0, tzinfo=UTC)


class TestParseWhen(unittest.TestCase):
    def test_relative_times(self):
        self.assertEqual(parse_when("in 20 minutes", NOW), NOW + timedelta(minutes=20))
        self.assertEqual(parse_when("2h30m", NOW), NOW + timedelta(hours=2, minutes=30))
        self.assertEqual(parse_when("1 day and 3 hours", NOW), NOW + timedelta(days=1, hours=3))
        self.assertEqual(parse_when("2 weeks", NOW), NOW + timedelta(weeks=2))
        self.assertEqual(parse_when("1m", NOW), NOW + timedelta(minutes=1))

    def test_clock_times_are_in_the_given_zone(self):
        # 8pm Mountain is 02:00 UTC the next day.
        self.assertEqual(parse_when("2026-10-30 20:00", NOW, DENVER), datetime(2026, 10, 31, 2, 0, tzinfo=UTC))
        self.assertEqual(
            parse_when("2026-10-30 20:00", NOW, ZoneInfo("America/New_York")),
            datetime(2026, 10, 31, 0, 0, tzinfo=UTC))

    def test_no_limit_on_how_far_ahead(self):
        self.assertEqual(parse_when("2031-01-01 09:00", NOW, UTC), datetime(2031, 1, 1, 9, 0, tzinfo=UTC))

    def test_past_and_garbage_are_refused(self):
        with self.assertRaises(ValueError):
            parse_when("2026-10-30 13:00", NOW, DENVER)  # an hour ago
        with self.assertRaises(ValueError):
            parse_when("next tuesday-ish", NOW)
        with self.assertRaises(ValueError):
            parse_when("0 minutes", NOW)

    def test_a_time_just_past_counts_as_now(self):
        self.assertEqual(parse_when("2026-10-30 14:00", NOW + timedelta(seconds=30), DENVER),
                         NOW + timedelta(seconds=30))

    def test_shown_with_its_zone(self):
        due = datetime(2026, 10, 31, 2, 0, tzinfo=UTC)
        self.assertEqual(format_when(due, DENVER), "Fri 2026-10-30 8:00 PM MDT")


class TestTimezones(unittest.IsolatedAsyncioTestCase):
    def test_aliases_and_iana_names(self):
        self.assertEqual(resolve_zone("Mountain").key, "America/Denver")
        self.assertEqual(resolve_zone("PST").key, "America/Los_Angeles")
        self.assertEqual(resolve_zone("Europe/London").key, "Europe/London")
        with self.assertRaises(ValueError):
            resolve_zone("Narnia/Cair_Paravel")

    def test_describe_shows_local_time(self):
        self.assertEqual(describe_zone(DENVER, NOW), "America/Denver (MDT, now Fri 2:00 PM)")

    async def test_block_lists_only_people_with_a_saved_zone(self):
        redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        store = TimezoneStore(redis_getter=lambda: redis)
        await store.set(10, 1, DENVER)
        block = await store.time_zones_block(10, [1, 2])
        self.assertIn("<@1>: America/Denver", block)
        self.assertNotIn("<@2>", block)
        self.assertEqual(await store.time_zones_block(10, [2]), "")
        self.assertEqual(await store.get(10, 1), DENVER)


def _bot_with_channel():
    channel = MagicMock()
    channel.send = AsyncMock()
    bot = MagicMock()
    bot.get_channel.return_value = channel
    return bot, channel


class TestReminderService(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        self.now = [NOW]
        self.service = ReminderService(
            store=ReminderStore(redis_getter=lambda: self.redis), now=lambda: self.now[0])

    async def add(self, **overrides):
        args = dict(guild_id=10, channel_id=20, message_id=99, user_ids=[1], set_by=1,
                    text="check the Tacoma listing", when="in 30 minutes")
        args.update(overrides)
        return await self.service.add(**args)

    async def test_posts_when_due_pinging_only_the_people_it_is_for(self):
        bot, channel = _bot_with_channel()
        self.service.set_bot(bot)
        await self.add(user_ids=[2, 3], set_by=1)

        self.assertEqual(await self.service.deliver_due(), 0)
        self.now[0] = NOW + timedelta(minutes=30)
        self.assertEqual(await self.service.deliver_due(), 1)

        content = channel.send.await_args.args[0]
        kwargs = channel.send.await_args.kwargs
        self.assertTrue(content.startswith("⏰ <@2> <@3> check the Tacoma listing"))
        self.assertIn("set by <@1>", content)
        self.assertEqual([u.id for u in kwargs["allowed_mentions"].users], [2, 3])
        self.assertEqual(kwargs["reference"].message_id, 99)
        # Posted once, then gone.
        self.assertEqual(await self.service.deliver_due(), 0)
        self.assertEqual(await self.service.pending(20), [])

    async def test_empty_user_ids_means_the_asker(self):
        reminder = await self.add(user_ids=[], set_by=7)
        self.assertEqual(reminder.user_ids, [7])

    async def test_a_late_reminder_says_when_it_was_meant_for(self):
        reminder = await self.add(when="2026-10-30 20:00", zone=DENVER)
        late = reminder_message(reminder, reminder.due_at + timedelta(minutes=10))
        self.assertIn("meant for Fri 2026-10-30 8:00 PM MDT", late)
        self.assertNotIn("set by", late)

    async def test_failed_posts_are_retried_and_missing_channels_dropped(self):
        bot, channel = _bot_with_channel()
        channel.send.side_effect = discord.HTTPException(SimpleNamespace(status=500, reason="oops"), "oops")
        self.service.set_bot(bot)
        await self.add(when="1m")
        self.now[0] = NOW + timedelta(minutes=2)
        self.assertEqual(await self.service.deliver_due(), 0)
        self.assertEqual(len(await self.service.pending(20)), 1)

        bot.get_channel.return_value = None
        bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(SimpleNamespace(status=404, reason="gone"), "gone"))
        self.assertEqual(await self.service.deliver_due(), 0)
        self.assertEqual(await self.service.pending(20), [])

    async def test_cancel_only_in_its_own_channel(self):
        reminder = await self.add()
        with self.assertRaises(ValueError):
            await self.service.cancel(reminder.id, 21)
        await self.service.cancel(reminder.id, 20)
        self.assertEqual(await self.service.pending(20), [])

    async def test_keeps_its_zone(self):
        reminder = await self.add(when="2026-10-30 20:00", zone=DENVER)
        [saved] = await self.service.pending(20)
        self.assertEqual(saved.tz, "America/Denver")
        self.assertIn("8:00 PM MDT", saved.describe())
        self.assertEqual(saved.due_at, reminder.due_at)

    async def test_text_is_required_and_capped(self):
        with self.assertRaises(ValueError):
            await self.add(text="  ")
        with self.assertRaises(ValueError):
            await self.add(text="x" * 2000)


class TestReminderTools(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        self.reminders = ReminderService(store=ReminderStore(redis_getter=lambda: self.redis), now=lambda: NOW)
        self.zones = TimezoneStore(redis_getter=lambda: self.redis)
        guild = MagicMock()
        guild.id = 10
        message = SimpleNamespace(id=99, guild=guild, channel=MagicMock(id=20), author=SimpleNamespace(id=111111111))
        self.tools = {t.definition["name"]: t for t in build_mention_tools(
            guild_id=10, channel_id=20, user_id=111111111, user_notes_provider=None, channel_summary_provider=None,
            how_i_work_loader=lambda: "", state_service=MagicMock(), gemini_service=MagicMock(),
            runpod_service=MagicMock(), default_draw_model=None, message=message,
            reminder_service=self.reminders, timezone_store=self.zones,
        )}

    async def test_clock_times_default_to_the_askers_saved_zone(self):
        await self.tools["set_timezone"].run({"user_id": "111111111", "timezone": "mountain"})
        result = await self.tools["remind"].run(
            {"when": "2026-10-30 20:00", "about": "call mom", "user_ids": [], "timezone": ""})

        self.assertFalse(result.is_error)
        self.assertIn("8:00 PM MDT", result.content[0]["text"])
        [reminder] = await self.reminders.pending(20)
        self.assertEqual(reminder.due_at, datetime(2026, 10, 31, 2, 0, tzinfo=UTC))

    async def test_an_explicit_zone_wins_and_others_can_be_pinged(self):
        await self.tools["remind"].run({
            "when": "2026-10-30 20:00", "about": "game night", "user_ids": ["<@222222222>"],
            "timezone": "America/New_York"})
        [reminder] = await self.reminders.pending(20)
        self.assertEqual(reminder.user_ids, [222222222])
        self.assertEqual(reminder.due_at, datetime(2026, 10, 31, 0, 0, tzinfo=UTC))

    async def test_list_and_cancel(self):
        await self.tools["remind"].run({"when": "in 5 minutes", "about": "stretch", "user_ids": [], "timezone": ""})
        listed = (await self.tools["list_reminders"].run({})).content[0]["text"]
        self.assertIn("stretch", listed)
        [reminder] = await self.reminders.pending(20)
        cancelled = await self.tools["cancel_reminder"].run({"reminder_id": reminder.id})
        self.assertFalse(cancelled.is_error)
        self.assertIn("No reminders", (await self.tools["list_reminders"].run({})).content[0]["text"])
        self.assertTrue((await self.tools["cancel_reminder"].run({"reminder_id": reminder.id})).is_error)

    async def test_bad_input_is_an_error_result(self):
        self.assertTrue((await self.tools["remind"].run(
            {"when": "soonish", "about": "x", "user_ids": [], "timezone": ""})).is_error)
        self.assertTrue((await self.tools["remind"].run(
            {"when": "in 5m", "about": "x", "user_ids": [], "timezone": "Mars/Olympus"})).is_error)
        self.assertTrue((await self.tools["set_timezone"].run({"user_id": "111111111", "timezone": "nope"})).is_error)


class TestStartupWiring(unittest.TestCase):
    def test_reminders_are_offered_to_mentions_and_not_started_under_tests(self):
        from src.startup import create_services
        from tests.config_helpers import cfg

        services = create_services(cfg(is_testing=True))
        with patch("src.services.reminder_service.asyncio.create_task") as create_task:
            services.reminder_service.start()
        create_task.assert_not_called()
        self.assertIs(services.mention_handler._reminder_service, services.reminder_service)


if __name__ == "__main__":
    unittest.main()
