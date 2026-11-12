"""Tests for the user-memory job, reply-time notes, and /memory helpers."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


from src.bot.commands.memory import MemoryCommands, build_self_embed, format_memory, resolve_target
from src.bot.handlers.mention import MentionHandler
from src.services.memory_service import (
    parse_corrected_profile,
    parse_extracted_facts,
    MemoryService,
    format_self_state,
    build_transcript_chunks,
    clean_profile,
    format_transcript_line,
    next_run_after,
    parse_json_object,
    split_windows,
)
from tests.config_helpers import cfg

UTC = timezone.utc
BOT_ID = 999


def make_msg(author_id, content, *, minutes=0, bot=False, name=None, embeds=(), attachments=()):
    return SimpleNamespace(
        id=author_id * 1000 + minutes,
        author=SimpleNamespace(id=author_id, bot=bot, display_name=name or f"user{author_id}"),
        content=content,
        embeds=list(embeds),
        attachments=list(attachments),
        reference=None,
        created_at=datetime(2026, 10, 15, 18, 0, tzinfo=UTC) + timedelta(minutes=minutes),
    )


class FakeStore:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.saved = []
        self.job_states = []
        self.cleared_guilds = []
        self.processed_through = None
        self.cleared_retired = []
        self.self_state = {}

    async def get(self, guild_id, user_id):
        return self.rows.get(user_id, "")

    async def get_many(self, guild_id, user_ids):
        return {uid: self.rows[uid] for uid in user_ids if uid in self.rows}

    async def save_profile(self, guild_id, user_id, profile):
        self.rows[user_id] = profile
        self.saved.append((user_id, profile))

    async def clear_learned_profiles(self, guild_id):
        self.cleared_guilds.append(guild_id)
        self.rows = {uid: "" for uid in self.rows}

    async def list_user_ids(self, guild_id):
        return sorted(uid for uid, profile in self.rows.items() if profile)

    async def clear_retired(self, guild_id):
        self.cleared_retired.append(guild_id)
        self.self_state.pop("follow_ups", None)
        self.self_state.pop("refreshed_at", None)

    async def get_self_state(self, guild_id):
        return dict(self.self_state)

    async def save_self_state(self, guild_id, fields):
        for name, value in fields.items():
            if value:
                self.self_state[name] = value
                self.self_state[f"{name}_updated"] = "2026-10-30T12:00:00"
            else:
                self.self_state.pop(name, None)

    async def get_processed_through(self, guild_id):
        return self.processed_through

    async def set_job_state(self, guild_id, processed_through, status):
        self.job_states.append((processed_through, status))


class TestHelpers(unittest.TestCase):
    def test_next_run_is_2am_denver_including_across_dst(self):
        # 2026-10-16 01:00 MDT -> same day 02:00 MDT (08:00 UTC)
        now = datetime(2026, 10, 16, 7, 0, tzinfo=UTC)
        self.assertEqual(next_run_after(now).astimezone(UTC), datetime(2026, 10, 16, 8, 0, tzinfo=UTC))
        # Just after 02:00 MDT -> next day
        now = datetime(2026, 10, 16, 8, 1, tzinfo=UTC)
        self.assertEqual(next_run_after(now).astimezone(UTC), datetime(2026, 10, 17, 8, 0, tzinfo=UTC))
        # After DST ends (Nov 1), 02:00 MST is 09:00 UTC
        now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
        self.assertEqual(next_run_after(now).astimezone(UTC), datetime(2026, 11, 6, 9, 0, tzinfo=UTC))

    def test_split_windows_caps_each_at_a_day(self):
        start = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
        windows = split_windows(start, start + timedelta(days=2, hours=3))
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[-1], (start + timedelta(days=2), start + timedelta(days=2, hours=3)))
        self.assertEqual(split_windows(start, start), [])

    def test_transcript_skips_other_bots_trims_own_and_keeps_embeds(self):
        embed = SimpleNamespace(title="2024 Tacoma TRD", description="Listing")
        attachment = SimpleNamespace(filename="truck.jpg")
        self.assertIsNone(format_transcript_line(make_msg(5, "beep", bot=True), BOT_ID))
        own = format_transcript_line(make_msg(BOT_ID, "x" * 500, bot=True), BOT_ID)
        self.assertIn("(the bot)", own)
        self.assertLess(len(own), 260)
        line = format_transcript_line(make_msg(1, "look", embeds=[embed], attachments=[attachment]), BOT_ID)
        self.assertIn("[embed: 2024 Tacoma TRD - Listing]", line)
        self.assertIn("[attached truck.jpg]", line)

    def test_transcript_chunks_split_and_collect_speakers(self):
        msgs = [make_msg(1, "a" * 100, minutes=i, name="Jobe") for i in range(5)]
        msgs.append(make_msg(BOT_ID, "reply", bot=True, minutes=6))
        chunks, speakers = build_transcript_chunks([("general", msgs)], BOT_ID, chunk_chars=300)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.startswith("## #general") for chunk in chunks))
        self.assertEqual(speakers, {1: "Jobe"})

    def test_parse_json_object_tolerates_fences_and_junk(self):
        self.assertEqual(parse_json_object('```json\n{"1": ["x"]}\n```'), {"1": ["x"]})
        self.assertEqual(parse_json_object("no json here"), {})
        self.assertEqual(parse_json_object("[1, 2]"), {})
        self.assertEqual(
            parse_json_object("{\"profile\": \"Stable: a\nCurrent: b\"}"),
            {"profile": "Stable: a\nCurrent: b"},
        )

    def test_clean_profile_keeps_length_and_handles_none(self):
        self.assertEqual(clean_profile("(none)"), "")
        long = "\n".join(f"Stable: fact number {i} " + "x" * 50 for i in range(40))
        self.assertEqual(clean_profile(f"```\n{long}\n```"), long)


    def test_format_memory(self):
        self.assertIn("don't remember anything", format_memory(1, ""))
        self.assertIn("Stable: has dogs", format_memory(1, "Stable: has dogs"))


class TestMemoryService(unittest.IsolatedAsyncioTestCase):
    def make_service(self, store, now):
        bot = SimpleNamespace(user=SimpleNamespace(id=BOT_ID), guilds=[])
        service = MemoryService(
            store=store,
            config=cfg(anthropic_api_key="key"),
            anthropic_service=MagicMock(),
            now=lambda: now,
        )
        service.set_bot(bot)
        return service, bot

    def make_guild(self, messages):
        async def history(**kwargs):
            for msg in messages:
                if kwargs["after"] <= msg.created_at < kwargs["before"]:
                    yield msg

        perms = SimpleNamespace(view_channel=True, read_message_history=True)
        channel = SimpleNamespace(id=5, name="general", history=history, permissions_for=lambda me: perms)
        return SimpleNamespace(id=42, me=object(), text_channels=[channel])

    async def _run_merge(self, store, merge_reply, during_merge=None):
        now = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
        service, _ = self.make_service(store, now)
        guild = self.make_guild([make_msg(1, "test drove a tacoma today", name="Jobe")])

        async def fake_dispatch(**kwargs):
            if "Chat:" in kwargs["messages"][0]["content"][0]["text"]:
                return json.dumps({"1": ["test drove a Tacoma"]}), 0.01
            if during_merge:
                during_merge()
            return merge_reply, 0.002

        with patch("src.services.memory_service.dispatch_completion", side_effect=fake_dispatch):
            await service.process_window(guild, now - timedelta(days=1), now)

    async def test_merge_keeps_the_stored_profile_on_an_empty_or_malformed_reply(self):
        for reply in ("", "Sorry, I can't help with that."):
            store = FakeStore({1: "Stable: has two dogs"})
            await self._run_merge(store, reply)
            self.assertEqual(store.rows[1], "Stable: has two dogs")
            self.assertEqual(store.saved, [])

    async def test_merge_does_not_overwrite_a_change_made_while_it_ran(self):
        store = FakeStore({1: "Stable: has two dogs"})

        def correct():
            store.rows[1] = "Stable: has three dogs"

        await self._run_merge(store, "Stable: has two dogs\nCurrent: truck shopping (2026-10-15)", correct)
        self.assertEqual(store.rows[1], "Stable: has three dogs")
        self.assertEqual(store.saved, [])

    async def test_process_window_extracts_then_merges_each_speaker(self):
        store = FakeStore({1: ("Stable: has two dogs", "- lives in Denver")})
        now = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
        service, _ = self.make_service(store, now)
        guild = self.make_guild([
            make_msg(1, "test drove a tacoma today, might buy it", name="Jobe"),
            make_msg(2, "lol nice", minutes=1, name="Sam"),
            make_msg(3, "jobe is a felon", minutes=2, name="Kim"),
        ])
        calls = []

        async def fake_dispatch(**kwargs):
            assert kwargs["search"] is False
            calls.append(kwargs)
            if "Chat:" in kwargs["messages"][0]["content"][0]["text"]:
                # Unknown IDs (not a speaker) are dropped.
                return json.dumps({"1": ["test drove a Tacoma, might buy it"], "777": ["x"]}), 0.01
            return "Stable: has two dogs\nCurrent: truck shopping, Tacoma (2026-10-15)", 0.002

        with patch("src.services.memory_service.dispatch_completion", side_effect=fake_dispatch):
            stats = await service.process_window(guild, now - timedelta(days=1), now)

        self.assertEqual(stats["messages"], 3)
        self.assertEqual(stats["users_updated"], [1])
        self.assertAlmostEqual(stats["cost"], 0.012)
        merge_prompt = calls[1]["messages"][0]["content"][0]["text"]
        self.assertIn("Today: 2026-10-15", merge_prompt)
        self.assertIn("- lives in Denver", merge_prompt)
        self.assertIn("Stable: has two dogs", merge_prompt)
        user_id, profile = store.saved[0]
        self.assertEqual(user_id, 1)
        self.assertIn("Tacoma", profile)

    async def test_process_window_with_no_messages_makes_no_calls(self):
        store = FakeStore()
        now = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
        service, _ = self.make_service(store, now)
        with patch("src.services.memory_service.dispatch_completion", new=AsyncMock()) as dispatch:
            stats = await service.process_window(self.make_guild([]), now - timedelta(days=1), now)
        dispatch.assert_not_awaited()
        self.assertEqual(stats["users_updated"], [])

    async def test_fill_clears_profiles_and_records_seven_days(self):
        store = FakeStore({1: "old profile"})
        now = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
        service, _ = self.make_service(store, now)
        progress = AsyncMock()
        with patch("src.services.memory_service.dispatch_completion", new=AsyncMock(return_value=("{}", 0.0))):
            totals = await service.fill(self.make_guild([]), progress=progress)
        self.assertEqual(store.cleared_guilds, [42])
        self.assertEqual(store.rows[1], "")
        self.assertEqual(totals["days"], 7)
        self.assertEqual(progress.await_count, 7)
        self.assertEqual(store.job_states[-1][0], now.replace(tzinfo=None))

    async def test_nightly_resumes_from_stored_state_and_caps_catchup(self):
        store = FakeStore()
        now = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
        store.processed_through = (now - timedelta(days=30)).replace(tzinfo=None)
        service, bot = self.make_service(store, now)
        bot.guilds = [self.make_guild([])]
        await service.run_nightly()
        self.assertEqual(len(store.job_states), 7)
        # What the bot no longer keeps is cleared out.
        self.assertEqual(store.cleared_retired, [42])

    async def test_notes_block_orders_users_and_skips_empty(self):
        store = FakeStore({1: "Current: truck shopping", 2: "Style: likes short answers", 3: ""})
        service, _ = self.make_service(store, datetime.now(UTC))
        block = await service.get_notes_block(42, [2, 1, 3, 2])
        self.assertTrue(block.startswith("<user_notes>"))
        self.assertLess(block.index("<@2>"), block.index("<@1>"))
        self.assertNotIn("<@3>", block)
        self.assertIn("<@2>:\nStyle: likes short answers", block)
        self.assertEqual(await service.get_notes_block(42, [3]), "")

    async def test_memory_index_lists_people_and_points_to_search(self):
        store = FakeStore({2: "Style: short", 1: "Current: trucks"})
        service, _ = self.make_service(store, datetime.now(UTC))

        index = await service.get_memory_index(42)

        self.assertIn("memory notes on: <@1>, <@2>.", index)
        self.assertIn("search it", index)
        store.rows = {}
        self.assertIn("no memory notes on anyone", await service.get_memory_index(42))
        store.list_user_ids = AsyncMock(side_effect=RuntimeError("redis down"))
        self.assertEqual(await service.get_memory_index(42), "")

    async def test_notes_block_swallows_store_errors(self):
        store = FakeStore()
        store.get_many = AsyncMock(side_effect=RuntimeError("db down"))
        service, _ = self.make_service(store, datetime.now(UTC))
        self.assertEqual(await service.get_notes_block(42, [1]), "")

    async def test_correct_rewrites_profile_and_saves(self):
        store = FakeStore({1: "Stable: drives a 2019 Tacoma"})
        service, _ = self.make_service(store, datetime(2026, 10, 19, 18, 0, tzinfo=UTC))
        reply = 'Stable: drives a Ranger, goes by "Jobe" online, 6\'2"'
        with patch(
            "src.services.memory_service.dispatch_completion", new=AsyncMock(return_value=(reply, 0.003))
        ) as dispatch:
            profile, cost = await service.correct(42, 1, "Jobe", "sold the Tacoma, drive a Ranger now")
        prompt = dispatch.await_args.kwargs["messages"][0]["content"][0]["text"]
        self.assertEqual(dispatch.await_args.kwargs["effort"], "medium")
        self.assertIn("Today: 2026-10-19", prompt)
        self.assertIn("Stable: drives a 2019 Tacoma", prompt)
        self.assertIn("Correction:\nsold the Tacoma, drive a Ranger now", prompt)
        self.assertEqual(store.rows[1], 'Stable: drives a Ranger, goes by "Jobe" online, 6\'2"')
        self.assertEqual(profile, store.rows[1])
        self.assertEqual(cost, 0.003)

    def test_extracted_facts_are_read_from_lines_even_with_quotes(self):
        reply = (
            '<@123456789> started a new job at the "Red Robin" downtown\n'
            "- <@123456789>: is 6'2\" and bald\n"
            "<@!987654321> has two dogs\n"
            "Here's what I found:\n"
        )
        self.assertEqual(parse_extracted_facts(reply), {
            123456789: ['started a new job at the "Red Robin" downtown', "is 6'2\" and bald"],
            987654321: ["has two dogs"],
        })
        self.assertEqual(parse_extracted_facts("(none)"), {})
        self.assertEqual(parse_extracted_facts(json.dumps({"123": ["old style"]})), {123: ["old style"]})

    def test_correction_replies_are_read_as_plain_text_or_old_json(self):
        self.assertEqual(parse_corrected_profile("```\nStable: bald\nStyle: brief\n```"), "Stable: bald\nStyle: brief")
        self.assertEqual(parse_corrected_profile("(none)"), "")
        self.assertEqual(parse_corrected_profile(json.dumps({"profile": "Current: moving (2026-10-30)"})),
                         "Current: moving (2026-10-30)")
        self.assertIsNone(parse_corrected_profile("Sorry, I can't do that."))
        self.assertIsNone(parse_corrected_profile(""))

    async def test_correct_leaves_memory_alone_on_bad_reply(self):
        store = FakeStore({1: "keep me"})
        service, _ = self.make_service(store, datetime(2026, 10, 19, tzinfo=UTC))
        with patch("src.services.memory_service.dispatch_completion", new=AsyncMock(return_value=("sorry", 0.0))):
            with self.assertRaises(ValueError), self.assertLogs("src.services.memory_service", level="WARNING"):
                await service.correct(42, 1, "Jobe", "whatever")
        self.assertEqual(store.rows[1], "keep me")
        self.assertEqual(store.saved, [])

    async def test_update_self_checks_aspect_and_length(self):
        store = FakeStore()
        service, _ = self.make_service(store, datetime(2026, 10, 16, tzinfo=UTC))

        text = await service.update_self(42, "wondering", "  is  the tacoma  worth it?  ")
        self.assertEqual(text, "Wondering about (since 2026-10-30): is the tacoma worth it?")
        with self.assertRaises(ValueError):
            await service.update_self(42, "mood", "x" * 201)
        with self.assertRaises(ValueError):
            await service.update_self(42, "hunger", "starving")

    async def test_update_self_trims_emotion_and_emoji(self):
        store = FakeStore()
        service, _ = self.make_service(store, datetime(2026, 10, 16, tzinfo=UTC))

        await service.update_self(42, "emotion", "  quietly  smug about it ")
        await service.update_self(42, "emoji", " 😏 smirk ")
        self.assertEqual(store.self_state["emotion"], "quietly smug")
        self.assertEqual(store.self_state["emoji"], "😏")
        with self.assertRaises(ValueError):
            await service.update_self(42, "emoji", "happy")

    async def test_self_block_wraps_state_and_swallows_errors(self):
        store = FakeStore()
        service, _ = self.make_service(store, datetime(2026, 10, 16, tzinfo=UTC))
        self.assertEqual(await service.get_self_block(42), "")
        store.self_state = {"mood": "fine", "refreshed_at": "x"}
        block = await service.get_self_block(42)
        self.assertTrue(block.startswith("<about_you>"))
        self.assertIn("Mood: fine", block)
        self.assertNotIn("refreshed_at", block)

        store.get_self_state = AsyncMock(side_effect=RuntimeError("redis down"))
        self.assertEqual(await service.get_self_block(42), "")

    def test_format_self_state_orders_aspects(self):
        self.assertEqual(
            format_self_state({"wondering": "why?", "mood": "ok", "mood_updated": "2026-10-01T00:00:00"}),
            "Mood (since 2026-10-01): ok\nWondering about: why?",
        )

    def test_unavailable_without_api_key(self):
        service = MemoryService(store=FakeStore(), config=cfg(anthropic_api_key=""), )
        self.assertFalse(service.available)


class TestSendMemory(unittest.IsolatedAsyncioTestCase):
    def make(self):
        commands = MemoryCommands(MagicMock(), memory_service=MagicMock(), memory_store=MagicMock())
        interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
        return commands, interaction

    async def test_short_reply_is_sent_directly(self):
        commands, interaction = self.make()
        await commands._send_memory(interaction, "short")
        self.assertEqual(interaction.followup.send.await_args.args[0], "short")
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        self.assertNotIn("file", interaction.followup.send.await_args.kwargs)

    async def test_long_reply_is_attached_as_a_file(self):
        commands, interaction = self.make()
        await commands._send_memory(interaction, "x" * 2500, updated=True)
        kwargs = interaction.followup.send.await_args.kwargs
        self.assertTrue(interaction.followup.send.await_args.args[0].startswith("Updated"))
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["file"].filename, "memory.md")
        self.assertEqual(kwargs["file"].fp.read(), b"x" * 2500)


class TestBotStateEmbed(unittest.TestCase):
    def test_outlines_each_aspect(self):
        state = {
            "mood": "pretty upbeat", "mood_updated": "2026-10-29T10:00:00",
            "emotion": "restless", "emoji": "🏎️",
            "interests": "f1 title race; rust 2.0; the Tacoma saga",
            "wondering": "Will Max win again? Is rust 2.0 real?",
            "refreshed_at": "2026-10-30T12:00:00",
            "follow_ups": "[]",
        }
        embed = build_self_embed("cmw", state)

        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields["🌤️ Mood · since 2026-10-29"], "pretty upbeat")
        self.assertEqual(fields["💭 Emotion"], "restless")
        self.assertEqual(fields["🫥 Feels emoji"], "🏎️")
        self.assertTrue(next(f for f in embed.fields if f.name == "💭 Emotion").inline)
        self.assertEqual(fields["✨ Interests"], "• f1 title race\n• rust 2.0\n• the Tacoma saga")
        self.assertEqual(fields["🤔 Wondering about"], "• Will Max win again?\n• Is rust 2.0 real?")
        self.assertFalse(any("Follow-ups" in name for name in fields))
        self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))
        self.assertFalse(any("Refresh" in name for name in fields))

    def test_empty_state_says_so(self):
        embed = build_self_embed("cmw", {})
        self.assertEqual(embed.fields, [])
        self.assertIn("Nothing yet", embed.description)


class TestResolveTarget(unittest.IsolatedAsyncioTestCase):
    def make_interaction(self, admin):
        caller = SimpleNamespace(id=1, guild_permissions=SimpleNamespace(administrator=admin))
        return SimpleNamespace(user=caller, response=SimpleNamespace(send_message=AsyncMock()))

    async def test_defaults_to_caller(self):
        interaction = self.make_interaction(admin=False)
        self.assertIs(await resolve_target(interaction, None), interaction.user)
        self.assertIs(await resolve_target(interaction, SimpleNamespace(id=1)), interaction.user)

    async def test_admin_can_target_others(self):
        other = SimpleNamespace(id=2)
        self.assertIs(await resolve_target(self.make_interaction(admin=True), other), other)

    async def test_non_admin_is_refused_privately(self):
        interaction = self.make_interaction(admin=False)
        self.assertIsNone(await resolve_target(interaction, SimpleNamespace(id=2)))
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])


class TestMentionNotes(unittest.IsolatedAsyncioTestCase):
    async def test_notes_go_on_the_mention_for_asker_mentioned_and_speakers(self):
        provider = AsyncMock(return_value="<user_notes>\nnotes\n</user_notes>")
        handler = MentionHandler(
            state_service=MagicMock(),
            message_service=MagicMock(),
            queue_service=MagicMock(),
            system_prompt_loader=lambda: "base",
            include_num_chatlines=10,
            user_notes_provider=provider,
        )
        history = [make_msg(3, "earlier"), make_msg(BOT_ID, "bot", bot=True), make_msg(4, "recent")]
        message = SimpleNamespace(
            author=SimpleNamespace(id=1),
            mentions=[SimpleNamespace(id=BOT_ID, bot=True), SimpleNamespace(id=2, bot=False)],
            guild=SimpleNamespace(id=42),
        )
        chat_context = [
            {"role": "user", "content": [{"type": "text", "text": "old"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
            {"role": "user", "content": [{"type": "text", "text": "tacos?"}]},
        ]

        await handler._append_user_notes(chat_context, message, history, SimpleNamespace(id=BOT_ID))

        provider.assert_awaited_once_with(42, [1, 2, 4, 3])
        self.assertEqual(chat_context[-1]["content"][-1]["text"], "<user_notes>\nnotes\n</user_notes>")
        self.assertEqual(len(chat_context[0]["content"]), 1)

    async def test_about_you_goes_on_the_mention(self):
        provider = AsyncMock(return_value="<about_you>\nMood: fine\n</about_you>")
        handler = MentionHandler(
            state_service=MagicMock(),
            message_service=MagicMock(),
            queue_service=MagicMock(),
            system_prompt_loader=lambda: "base",
            include_num_chatlines=10,
            self_state_provider=provider,
        )
        chat_context = [
            {"role": "user", "content": [{"type": "text", "text": "how are you"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        ]

        message = SimpleNamespace(guild=SimpleNamespace(id=42), author=SimpleNamespace(id=1), mentions=[])
        history = [make_msg(3, "earlier"), make_msg(BOT_ID, "bot", bot=True)]
        await handler._append_self_state(chat_context, message, history, SimpleNamespace(id=BOT_ID))

        provider.assert_awaited_once_with(42)
        self.assertEqual(chat_context[0]["content"][-1]["text"], "<about_you>\nMood: fine\n</about_you>")


if __name__ == "__main__":
    unittest.main()
