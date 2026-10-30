"""Tests for the per-mention chat tools."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.chat_tools import MAX_DRAWS_PER_REPLY, build_mention_tools


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


if __name__ == "__main__":
    unittest.main()
