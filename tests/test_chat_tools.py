"""Tests for the per-mention chat tools."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from src.services.chat_tools import MAX_DRAWS_PER_REPLY, build_mention_tools
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
    return SimpleNamespace(guild=guild, channel=here, author=author)


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
    def test_offers_all_tools_in_a_fixed_order(self):
        tools, _ = _tools()
        self.assertEqual(list(tools), ["draw", "recall_users", "recall_channel", "how_i_work"])

    def test_leaves_out_tools_without_their_dependencies(self):
        tools, _ = _tools(guild_id=None, default_draw_model=None)
        self.assertEqual(list(tools), ["how_i_work"])

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
            "search_messages", "read_messages", "wiki_pageviews", "how_i_work",
        ])

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

    def test_wiki_title_candidates(self):
        self.assertEqual(wiki_title_candidates("https://en.wikipedia.org/wiki/Las_Vegas"), ["Las_Vegas"])
        self.assertEqual(wiki_title_candidates("las vegas"), ["Las_vegas", "Las_Vegas"])
        self.assertEqual(wiki_title_candidates("  "), [])


if __name__ == "__main__":
    unittest.main()
