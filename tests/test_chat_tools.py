"""Tests for the per-mention chat tools."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from src.services.chat_tools import (
    MAX_DRAWS_PER_REPLY, MAX_REACTIONS_PER_REPLY, MAX_VIEW_IMAGES, UNKNOWN_EMOJI_CODE, build_mention_tools,
)
from src.services.discord_history import INDEX_NOT_READY_CODE
from src.utils.url_utils import wiki_title_candidates


def _tools(**overrides):
    state_service = MagicMock()
    state_service.get_draw_model.return_value = None
    runpod_service = MagicMock()
    runpod_service.has_model.return_value = True
    runpod_service.generate_image = AsyncMock(return_value=(b"png-bytes", 0.02))
    deps = dict(
        guild_id=10,
        channel_id=20,
        user_id=30,
        user_notes_provider=AsyncMock(return_value="<user_notes>notes</user_notes>"),
        channel_summary_provider=AsyncMock(return_value="lately"),
        how_i_work_loader=lambda: "architecture",
        state_service=state_service,
        gemini_service=MagicMock(),
        runpod_service=runpod_service,
        default_draw_model="seedream",
    )
    deps.update(overrides)
    return {t.definition["name"]: t for t in build_mention_tools(**deps)}, deps


def _perms(ok=True):
    return SimpleNamespace(view_channel=ok, read_message_history=ok)


def _channel(channel_id, name, readable=True):
    channel = MagicMock()
    channel.id = channel_id
    channel.name = name
    channel.permissions_for.side_effect = lambda who: _perms(readable or who == "bot")
    return channel


def _message(admin=False):
    """A mention in channel 20 of guild 10, by user 30; channel 21 is readable, 222222222 isn't (to the asker)."""
    here, other, secret = _channel(20, "general"), _channel(21, "games"), _channel(222222222, "mods", readable=False)
    guild = MagicMock()
    guild.id = 10
    guild.me = "bot"
    guild.text_channels = [here, other, secret]
    guild.get_channel.side_effect = lambda cid: {c.id: c for c in guild.text_channels}.get(cid)
    guild.get_member.return_value = SimpleNamespace(display_name="Bob")
    guild._state.http.request = AsyncMock(return_value={"total_results": 0, "messages": []})
    author = SimpleNamespace(id=30, guild_permissions=SimpleNamespace(administrator=admin))
    return SimpleNamespace(id=999999999, guild=guild, channel=here, author=author)


def _full_tools(message=None, **overrides):
    deps = dict(
        message=message or _message(),
        default_edit_model="qwen",
        memory_corrector=AsyncMock(return_value=("Stable: drives a Ranger", 0.01)),
        wiki_views=AsyncMock(return_value=None),
    )
    deps.update(overrides)
    return _tools(**deps)


class TestChatTools(unittest.IsolatedAsyncioTestCase):
    async def test_follow_up_adds_and_marks_done(self):
        updater = AsyncMock(return_value="#1 <@123456789> (from 2026-10-30): the trip")
        tools, _ = _tools(follow_up_updater=updater)

        result = await tools["follow_up"].run({"user_id": "<@123456789>", "about": "the trip", "done": [3]})
        updater.assert_awaited_once_with(10, add=(123456789, "the trip"), done=[3])
        self.assertIn("the trip", result.content[0]["text"])

        for bad in ({"user_id": "123456789"}, {}, {"about": "no one"}):
            self.assertTrue((await tools["follow_up"].run(bad)).is_error)
        self.assertEqual(updater.await_count, 1)

    async def test_update_self_saves_and_reports_errors(self):
        updater = AsyncMock(return_value="Mood (since 2026-10-30): chipper")
        tools, _ = _tools(self_state_updater=updater)
        self.assertEqual(list(tools)[3], "update_self")

        result = await tools["update_self"].run({"aspect": "mood", "text": "chipper"})
        updater.assert_awaited_once_with(10, "mood", "chipper")
        self.assertFalse(result.is_error)
        self.assertIn("chipper", result.content[0]["text"])

        updater.side_effect = ValueError("too long")
        result = await tools["update_self"].run({"aspect": "mood", "text": "x" * 500})
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0]["text"], "too long")

    def test_offers_all_tools_in_a_fixed_order(self):
        tools, _ = _tools()
        self.assertEqual(list(tools), ["draw", "recall_users", "recall_channel", "transcribe_youtube", "how_i_work"])

    def test_leaves_out_tools_without_their_dependencies(self):
        tools, _ = _tools(guild_id=None, default_draw_model=None)
        self.assertEqual(list(tools), ["transcribe_youtube", "how_i_work"])

    @patch("src.services.chat_tools.compress_image", return_value=b"jpeg")
    async def test_draw_uses_the_channel_model_and_returns_the_image(self, _compress):
        tools, deps = _tools()
        deps["state_service"].get_draw_model.return_value = "flux"

        result = await tools["draw"].run({"prompt": "a cat"})

        deps["runpod_service"].generate_image.assert_awaited_once_with(
            prompt="a cat", model="flux", discord_user_id=30, discord_channel_id=20)
        self.assertAlmostEqual(result.cost, 0.02)
        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.content[1]["type"], "image")
        self.assertEqual(result.content[1]["source"]["media_type"], "image/jpeg")
        self.assertIn("flux", result.content[0]["text"])

    @patch("src.services.chat_tools.compress_image", return_value=b"jpeg")
    async def test_draw_is_capped_per_reply(self, _compress):
        tools, deps = _tools()
        for _ in range(MAX_DRAWS_PER_REPLY):
            await tools["draw"].run({"prompt": "x"})
        result = await tools["draw"].run({"prompt": "x"})
        self.assertTrue(result.is_error)
        self.assertEqual(deps["runpod_service"].generate_image.await_count, MAX_DRAWS_PER_REPLY)

    async def test_recall_users_accepts_mentions_and_bare_ids(self):
        tools, deps = _tools()
        result = await tools["recall_users"].run({"user_ids": ["<@123456789>", "987654321", "nope"]})
        deps["user_notes_provider"].assert_awaited_once_with(10, [123456789, 987654321])
        self.assertEqual(result.content[0]["text"], "<user_notes>notes</user_notes>")

    async def test_recall_users_says_when_there_are_no_notes(self):
        tools, deps = _tools()
        deps["user_notes_provider"].return_value = ""
        result = await tools["recall_users"].run({"user_ids": ["123456789"]})
        self.assertIn("No memory notes", result.content[0]["text"])
        self.assertTrue((await tools["recall_users"].run({"user_ids": []})).is_error)

    async def test_recall_channel_and_how_i_work(self):
        tools, deps = _tools()
        self.assertEqual((await tools["recall_channel"].run({})).content[0]["text"], "lately")
        deps["channel_summary_provider"].assert_awaited_once_with(10, 20)
        self.assertEqual((await tools["how_i_work"].run({})).content[0]["text"], "architecture")


class TestNewTools(unittest.IsolatedAsyncioTestCase):
    def test_offers_every_tool_when_it_has_a_message(self):
        tools, _ = _full_tools()
        self.assertEqual(list(tools), [
            "draw", "edit_image", "recall_users", "recall_channel", "remember",
            "search_messages", "read_messages", "view_images", "react", "transcribe_youtube", "wiki_pageviews",
            "how_i_work",
        ])

    def test_every_tool_is_named_in_the_prompt_guidance(self):
        tools, _ = _full_tools(
            irc_log=MagicMock(), quote_file=MagicMock(), url_content_fetcher=AsyncMock(),
            day_summary_loader=AsyncMock(), reminder_service=MagicMock(), timezone_store=MagicMock(),
            video_summarizer=AsyncMock(), video_watcher=AsyncMock(),
        )
        guidance = "".join(tool.guidance for tool in tools.values())
        for name in tools:
            self.assertIn(f"`{name}(", guidance)

    @patch("src.services.chat_tools.compress_image", return_value=b"jpeg")
    async def test_edit_image_sends_the_message_images_to_the_edit_model(self, _compress):
        message = _message()
        image = MagicMock(content_type="image/png")
        image.read = AsyncMock(return_value=b"raw")
        message.channel.fetch_message = AsyncMock(return_value=SimpleNamespace(
            attachments=[image, MagicMock(content_type="text/plain")], embeds=[]))
        tools, deps = _full_tools(message)
        deps["state_service"].get_edit_model.return_value = None
        deps["runpod_service"].has_edit_model.return_value = True
        deps["runpod_service"].edit_image = AsyncMock(return_value=(b"edited", 0.03))

        result = await tools["edit_image"].run({"prompt": "make it red", "message_ids": ["123456789"]})

        message.channel.fetch_message.assert_awaited_once_with(123456789)
        kwargs = deps["runpod_service"].edit_image.await_args.kwargs
        self.assertEqual((kwargs["model"], kwargs["images"]), ("qwen", ["cmF3"]))
        self.assertEqual(len(result.files), 1)
        self.assertAlmostEqual(result.cost, 0.03)

    async def test_edit_image_needs_images(self):
        message = _message()
        message.channel.fetch_message = AsyncMock(return_value=SimpleNamespace(attachments=[], embeds=[]))
        tools, _ = _full_tools(message)
        result = await tools["edit_image"].run({"prompt": "x", "message_ids": ["123456789"]})
        self.assertTrue(result.is_error)

    async def test_remember_changes_the_askers_own_memory(self):
        tools, deps = _full_tools(user_id=123456789)
        result = await tools["remember"].run({"user_id": "<@123456789>", "note": "I drive a Ranger now"})
        deps["memory_corrector"].assert_awaited_once_with(10, 123456789, "Bob", "I drive a Ranger now")
        self.assertIn("drives a Ranger", result.content[0]["text"])
        self.assertAlmostEqual(result.cost, 0.01)

    async def test_only_admins_change_someone_elses_memory(self):
        tools, deps = _full_tools(user_id=123456789)
        result = await tools["remember"].run({"user_id": "987654321", "note": "x"})
        self.assertTrue(result.is_error)
        deps["memory_corrector"].assert_not_awaited()

        tools, deps = _full_tools(_message(admin=True), user_id=123456789)
        await tools["remember"].run({"user_id": "987654321", "note": "x"})
        deps["memory_corrector"].assert_awaited_once()

    async def test_search_covers_only_channels_the_asker_can_read(self):
        message = _message()
        http = message.guild._state.http
        http.request.return_value = {"total_results": 1, "messages": [[{
            "id": "555", "channel_id": "21", "content": "the tacoma is for sale",
            "timestamp": "2026-10-01T12:00:00+00:00",
            "author": {"id": "123456789", "username": "bob", "global_name": "Bob"},
            "attachments": [], "embeds": [],
        }]]}
        tools, _ = _full_tools(message)

        text = (await tools["search_messages"].run({"query": "tacoma", "all_channels": True})).content[0]["text"]

        params = http.request.await_args.kwargs["params"]
        self.assertEqual([v for k, v in params if k == "channel_id"], ["20", "21"])
        self.assertIn(("content", "tacoma"), params)
        self.assertIn("[555] #games <@123456789> (Bob): the tacoma is for sale", text)

        await tools["search_messages"].run({"query": "tacoma"})
        params = http.request.await_args.kwargs["params"]
        self.assertEqual([v for k, v in params if k == "channel_id"], ["20"])

    async def test_search_reports_an_unindexed_server_and_bad_dates(self):
        message = _message()
        message.guild._state.http.request.return_value = {"code": INDEX_NOT_READY_CODE, "retry_after": 5}
        tools, _ = _full_tools(message)
        self.assertIn("still indexing", (await tools["search_messages"].run({"query": "x"})).content[0]["text"])
        self.assertTrue((await tools["search_messages"].run({"after": "last week"})).is_error)

    async def test_read_messages_refuses_channels_the_asker_cant_read(self):
        message = _message()
        tools, _ = _full_tools(message)
        result = await tools["read_messages"].run({"message_id": "123456789", "channel_id": "222222222"})
        self.assertTrue(result.is_error)

    async def test_read_messages_marks_the_target(self):
        message = _message()
        target = SimpleNamespace(id=123456789, channel=message.channel, author=SimpleNamespace(id=1, display_name="A"),
                                 content="hi", attachments=[], embeds=[],
                                 created_at=discord.utils.snowflake_time(123456789))

        async def history(around=None, limit=None):
            yield target

        message.channel.history = history
        tools, _ = _full_tools(message)
        text = (await tools["read_messages"].run({"message_id": "123456789"})).content[0]["text"]
        self.assertIn("→ [", text)
        self.assertIn("(A): hi", text)

    async def test_wiki_pageviews_falls_back_to_title_case(self):
        views = AsyncMock(side_effect=[None, 52000])
        tools, _ = _full_tools(wiki_views=views)
        result = await tools["wiki_pageviews"].run({"title": "daveigh chase"})
        self.assertEqual([c.args[0] for c in views.await_args_list], ["Daveigh_chase", "Daveigh_Chase"])
        self.assertIn("52,000", result.content[0]["text"])

    async def test_old_quotes_only_when_a_quote_file_is_set(self):
        tools, _ = _full_tools()
        self.assertNotIn("old_quotes", tools)
        quote_file = MagicMock()
        quote_file.get.return_value = "quotes"
        tools, _ = _full_tools(quote_file=quote_file)
        self.assertEqual(list(tools)[-3:], ["old_quotes", "wiki_pageviews", "how_i_work"])
        result = await tools["old_quotes"].run({"query": "rogan", "random_count": 3})
        quote_file.get.assert_called_once_with(query="rogan", random_count=3, skip=0)
        self.assertEqual(result.content[0]["text"], "quotes")

    async def test_read_urls_fetches_the_links_and_returns_images(self):
        fetcher = AsyncMock(return_value=("------\nYouTube transcript\n\nhello", [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
            {"type": "image_url", "image_url": {"url": "https://cdn.example/p.png"}},
            {"type": "image_url", "image_url": {"url": "data:application/pdf;base64,QUJD"}},
        ]))
        tools, _ = _tools(url_content_fetcher=fetcher)
        urls = [f"https://example.com/{i}" for i in range(7)]

        result = await tools["read_urls"].run({"urls": urls + [urls[0]]})

        fetcher.assert_awaited_once_with("\n".join(urls[:5]), cache_only=False)
        self.assertIn("hello", result.content[0]["text"])
        self.assertEqual([b["source"]["type"] for b in result.content[1:]], ["base64", "url"])

    async def test_read_urls_reports_unreadable_links(self):
        tools, _ = _tools(url_content_fetcher=AsyncMock(return_value=("", [])))
        self.assertTrue((await tools["read_urls"].run({"urls": ["https://nope.example"]})).is_error)
        self.assertTrue((await tools["read_urls"].run({"urls": []})).is_error)

    async def test_recall_today_returns_the_summary_and_its_cost(self):
        loader = AsyncMock(return_value=("they argued about trucks", 0.04))
        tools, _ = _tools(day_summary_loader=loader)
        result = await tools["recall_today"].run({})
        self.assertIn("<day_summary>\nthey argued about trucks\n</day_summary>", result.content[0]["text"])
        self.assertAlmostEqual(result.cost, 0.04)
        loader.return_value = ("", 0.0)
        self.assertIn("Nothing was said", (await tools["recall_today"].run({})).content[0]["text"])

    async def test_count_irc_log_passes_the_grouping_through(self):
        irc_log = MagicMock()
        irc_log.count.return_value = "42 occurrences"
        tools, _ = _tools(irc_log=irc_log)
        result = await tools["count_irc_log"].run({"query": "fuck", "nick": "@Cyc66", "group_by": ["year", "bogus"]})
        irc_log.count.assert_called_once_with("fuck", nick="Cyc66", group_by=["year"], top=15)
        self.assertEqual(result.content[0]["text"], "42 occurrences")

    def test_wiki_title_candidates(self):
        self.assertEqual(wiki_title_candidates("https://en.wikipedia.org/wiki/Las_Vegas"), ["Las_Vegas"])
        self.assertEqual(wiki_title_candidates("las vegas"), ["Las_vegas", "Las_Vegas"])
        self.assertEqual(wiki_title_candidates("  "), [])


if __name__ == "__main__":
    unittest.main()


def _posted(message_id, *, attachments=(), embeds=()):
    return SimpleNamespace(id=message_id, author=SimpleNamespace(id=7), attachments=list(attachments),
                           embeds=list(embeds))


class TestViewImages(unittest.IsolatedAsyncioTestCase):
    @patch("src.services.chat_tools.url_to_base64_data_url", new_callable=AsyncMock,
           return_value="data:image/png;base64,QkJC")
    @patch("src.services.chat_tools.attachment_to_base64_data_url", new_callable=AsyncMock,
           return_value="data:image/jpeg;base64,QUFB")
    async def test_returns_attachment_and_embed_images(self, _attachment, _url):
        message = _message()
        photo = SimpleNamespace(content_type="image/png")
        notes = SimpleNamespace(content_type="text/plain")
        embed = SimpleNamespace(image=SimpleNamespace(url="https://x/y.gif"), thumbnail=None)
        message.channel.fetch_message = AsyncMock(return_value=_posted(55555, attachments=[photo, notes],
                                                                       embeds=[embed]))
        tools, _ = _full_tools(message)

        result = await tools["view_images"].run({"message_ids": ["55555"]})

        self.assertFalse(result.is_error)
        self.assertEqual(result.content[0], {"type": "text", "text": "[55555] <@7>: 2 images"})
        self.assertEqual([b["source"]["media_type"] for b in result.content[1:]], ["image/jpeg", "image/png"])

    async def test_caps_the_images_and_refuses_unreadable_channels(self):
        message = _message()
        photos = [SimpleNamespace(content_type="image/png")] * (MAX_VIEW_IMAGES + 3)
        message.channel.fetch_message = AsyncMock(return_value=_posted(55555, attachments=photos))
        tools, _ = _full_tools(message)
        with patch("src.services.chat_tools.attachment_to_base64_data_url", new_callable=AsyncMock,
                   return_value="data:image/jpeg;base64,QUFB"):
            result = await tools["view_images"].run({"message_ids": ["55555"]})
        self.assertEqual(sum(b["type"] == "image" for b in result.content), MAX_VIEW_IMAGES)

        denied = await tools["view_images"].run({"message_ids": ["55555"], "channel_id": "222222222"})
        self.assertTrue(denied.is_error)

    async def test_a_message_without_images_is_an_error(self):
        message = _message()
        message.channel.fetch_message = AsyncMock(return_value=_posted(55555))
        tools, _ = _full_tools(message)
        self.assertTrue((await tools["view_images"].run({"message_ids": ["55555"]})).is_error)


class TestReact(unittest.IsolatedAsyncioTestCase):
    async def test_reacts_to_the_mention_with_a_custom_emoji_by_name(self):
        message = _message()
        kekw = SimpleNamespace(name="KEKW")
        message.guild.emojis = [kekw]
        message.add_reaction = AsyncMock()
        tools, _ = _full_tools(message)

        result = await tools["react"].run({"message_id": "999999999", "emoji": ":kekw:"})

        self.assertFalse(result.is_error)
        message.add_reaction.assert_awaited_once_with(kekw)

    async def test_reacts_to_other_messages_with_unicode_and_is_capped(self):
        message = _message()
        message.guild.emojis = []
        partial = MagicMock()
        partial.add_reaction = AsyncMock()
        message.channel.get_partial_message.return_value = partial
        tools, _ = _full_tools(message)

        for _ in range(MAX_REACTIONS_PER_REPLY):
            self.assertFalse((await tools["react"].run({"message_id": "55555", "emoji": "😂"})).is_error)
        self.assertTrue((await tools["react"].run({"message_id": "55555", "emoji": "😂"})).is_error)
        message.channel.get_partial_message.assert_called_with(55555)
        partial.add_reaction.assert_awaited_with("😂")

    async def test_unknown_emoji_is_an_error(self):
        message = _message()
        message.guild.emojis = []
        partial = MagicMock()
        response = SimpleNamespace(status=400, reason="Bad Request")
        partial.add_reaction = AsyncMock(side_effect=discord.HTTPException(
            response, {"code": UNKNOWN_EMOJI_CODE, "message": "Unknown Emoji"}))
        message.channel.get_partial_message.return_value = partial
        tools, _ = _full_tools(message)

        result = await tools["react"].run({"message_id": "55555", "emoji": "notanemoji"})

        self.assertTrue(result.is_error)
        self.assertIn("doesn't know that emoji", result.content[0]["text"])


class TestStrictSchemaLimits(unittest.TestCase):
    """The API rejects a request whose strict tool schemas, all together, go over these limits."""

    def _schemas(self):
        from src.services import chat_tools
        from src.services.anthropic_service import THINK_HARDER_TOOL
        tools, _ = _full_tools(
            irc_log=MagicMock(),
            quote_file=MagicMock(),
            url_content_fetcher=AsyncMock(),
            day_summary_loader=AsyncMock(),
            self_state_updater=AsyncMock(),
            follow_up_updater=AsyncMock(),
            reminder_service=MagicMock(),
            timezone_store=MagicMock(),
            video_summarizer=AsyncMock(),
            video_watcher=AsyncMock(),
        )
        every_tool = {v["name"] for k, v in vars(chat_tools).items() if k.endswith("_TOOL") and isinstance(v, dict)}
        self.assertEqual(set(tools), every_tool)  # so a new tool can't sneak past this test
        return [t.definition for t in tools.values()] + [THINK_HARDER_TOOL]

    def test_strict_schemas_stay_under_the_api_limits(self):
        strict = [d for d in self._schemas() if d.get("strict")]
        props = [
            (name, spec, name in d["input_schema"].get("required", []))
            for d in strict for name, spec in d["input_schema"]["properties"].items()
        ]
        optional = [name for name, _, required in props if not required]
        unions = [name for name, spec, _ in props if "anyOf" in spec or isinstance(spec.get("type"), list)]

        self.assertLessEqual(len(strict), 20, "strict tools per request")
        self.assertLessEqual(len(optional), 24, f"optional parameters across strict tools: {optional}")
        self.assertLessEqual(len(unions), 16, f"union-type parameters across strict tools: {unions}")
