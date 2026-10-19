"""Tests for the shared multimodal chat-context builder."""

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.utils import chat_context
from src.utils.chat_context import build_chat_context

BOT_ID = 999
WHEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def make_msg(msg_id, content="", *, author_id=1, attachments=None, embeds=None, reference=None):
    return SimpleNamespace(
        id=msg_id,
        content=content,
        author=SimpleNamespace(id=author_id),
        created_at=WHEN,
        attachments=attachments or [],
        embeds=embeds or [],
        reference=reference,
    )


def make_embed(*, title=None, description=None, url=None, image=None, thumbnail=None):
    return SimpleNamespace(title=title, description=description, url=url, image=image, thumbnail=thumbnail)


def media(url=None, proxy_url=None):
    return SimpleNamespace(url=url, proxy_url=proxy_url)


def text_of(entry):
    return entry["content"][0]["text"]


class TestBuildChatContext(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.attachment_converter = AsyncMock(return_value="data:image/jpeg;base64,ATT")
        self.url_converter = AsyncMock(return_value="data:image/jpeg;base64,EMBED")
        self.transcriber = AsyncMock(return_value="spoken words")

    async def build(self, messages, **kwargs):
        return await build_chat_context(
            messages, BOT_ID,
            attachment_converter=self.attachment_converter,
            url_converter=self.url_converter,
            transcriber=self.transcriber,
            **kwargs,
        )

    async def test_basic_text_message_and_roles(self):
        result = await self.build([
            make_msg(1, "hello", author_id=5),
            make_msg(2, "hi back", author_id=BOT_ID),
        ])

        self.assertEqual([e["role"] for e in result], ["user", "assistant"])
        self.assertIn("[1] <@5>:", text_of(result[0]))
        self.assertTrue(text_of(result[0]).endswith("hello"))
        self.assertIn("hi back", text_of(result[1]))

    async def test_empty_message_placeholder(self):
        result = await self.build([make_msg(1)])
        self.assertIn("[Empty Message]", text_of(result[0]))

    async def test_reply_resolved_from_history(self):
        original = make_msg(10, "original text", author_id=7)
        reply = make_msg(11, "reply text", reference=SimpleNamespace(message_id=10, resolved=None, cached_message=None))

        result = await self.build([original, reply])

        self.assertIn('[Replying to message: "[', text_of(result[1]))
        self.assertIn("[10] <@7>: original text", text_of(result[1]))

    async def test_order_is_preserved_when_conversion_finishes_out_of_order(self):
        delays = {1: 0.03, 2: 0.0, 3: 0.015}

        async def fetcher(text, *, cache_only):
            for msg_id, delay in delays.items():
                if f"[{msg_id}]" in text:
                    await asyncio.sleep(delay)
            return "", []

        msgs = [make_msg(i, f"m{i}") for i in (1, 2, 3)]
        result = await self.build(msgs, url_content_fetcher=fetcher)

        self.assertEqual([text_of(e)[-2:] for e in result], ["m1", "m2", "m3"])

    async def test_messages_are_converted_concurrently_with_limit(self):
        running = 0
        peak = 0

        async def fetcher(text, *, cache_only):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
            return "", []

        msgs = [make_msg(i, f"m{i}") for i in range(20)]
        await self.build(msgs, url_content_fetcher=fetcher)

        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, chat_context.MAX_CONCURRENT_MESSAGES)

    async def test_only_recent_messages_fetch_fresh_url_content(self):
        calls = {}

        async def fetcher(text, *, cache_only):
            calls[text.split("]")[1].strip(" [")] = cache_only
            return "", []

        total = chat_context.FRESH_FETCH_RECENT_MESSAGES + 5
        msgs = [make_msg(i, f"see https://example.com/{i}") for i in range(total)]
        await self.build(msgs, url_content_fetcher=fetcher)

        self.assertEqual(chat_context.FRESH_FETCH_RECENT_MESSAGES, 10)
        self.assertEqual(len(calls), total)
        for i in range(total):
            self.assertEqual(calls[str(i)], i < 5, f"message {i}")

    async def test_short_history_fetches_everything_fresh(self):
        fetcher = AsyncMock(return_value=("", []))
        await self.build([make_msg(i, "x") for i in range(3)], url_content_fetcher=fetcher)
        self.assertTrue(all(c.kwargs["cache_only"] is False for c in fetcher.await_args_list))

    async def test_url_content_is_prepended_and_images_attached(self):
        part = {"type": "image_url", "image_url": {"url": "data:image/png;base64,TW"}}
        fetcher = AsyncMock(return_value=("------\nURL content:\nbody\n\n", [part]))

        result = await self.build([make_msg(1, "https://example.com")], url_content_fetcher=fetcher)

        self.assertTrue(text_of(result[0]).startswith("------\nURL content:\nbody"))
        self.assertIn(part, result[0]["content"])

    async def test_bot_messages_are_not_url_enriched(self):
        fetcher = AsyncMock(return_value=("extra", []))
        await self.build([make_msg(1, "https://example.com", author_id=BOT_ID)], url_content_fetcher=fetcher)
        fetcher.assert_not_awaited()

    async def test_no_fetcher_skips_enrichment(self):
        result = await self.build([make_msg(1, "https://example.com")])
        self.assertEqual(len(result[0]["content"]), 1)

    async def test_image_audio_and_other_attachments(self):
        attachments = [
            SimpleNamespace(content_type="image/png", filename="a.png"),
            SimpleNamespace(content_type="audio/ogg", filename="v.ogg", duration=3.5),
            SimpleNamespace(content_type=None, filename="voice", is_voice_message=lambda: True),
            SimpleNamespace(content_type="application/pdf", filename="doc.pdf"),
        ]
        self.transcriber.side_effect = ["spoken words", None]

        result = await self.build([make_msg(1, "look", attachments=attachments)])

        text = text_of(result[0])
        self.assertIn("(3.5s): v.ogg\nTranscript: spoken words", text)
        self.assertIn("voice\n(Transcription failed or unavailable)", text)
        self.assertIn("[Attached file: doc.pdf]", text)
        self.assertIn({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ATT"}}, result[0]["content"])
        self.assertEqual(self.transcriber.await_count, 2)

    async def test_attachment_without_voice_flag_is_a_plain_file(self):
        # SimpleNamespace without is_voice_message, and a MagicMock-like truthy attr, are not voice.
        attachments = [SimpleNamespace(content_type=None, filename="blob", is_voice_message=lambda: None)]
        result = await self.build([make_msg(1, "x", attachments=attachments)])
        self.assertIn("[Attached file: blob]", text_of(result[0]))
        self.transcriber.assert_not_awaited()

    async def test_bot_attachments_are_not_converted(self):
        attachments = [SimpleNamespace(content_type="image/png", filename="a.png")]
        await self.build([make_msg(1, "x", author_id=BOT_ID, attachments=attachments)])
        self.attachment_converter.assert_not_awaited()

    async def test_attachment_conversion_failure_is_logged_not_raised(self):
        self.attachment_converter.side_effect = RuntimeError("boom")
        attachments = [SimpleNamespace(content_type="image/png", filename="a.png")]

        result = await self.build([make_msg(1, "x", attachments=attachments)])

        self.assertEqual(len(result[0]["content"]), 1)

    async def test_embed_text_is_included(self):
        embed = make_embed(title="T", description="D", url="https://e.com")
        result = await self.build([make_msg(1, "x", embeds=[embed])])
        self.assertIn("[Embeds:\n- Title: T | Description: D | URL: https://e.com\n]", text_of(result[0]))

    async def test_embed_preview_prefers_proxy_url(self):
        embeds = [
            make_embed(title="a", image=media(url="https://origin/img.png", proxy_url="https://media.discordapp.net/img.png")),
            make_embed(title="b", image=media(url="https://origin/only.png", proxy_url=None)),
            make_embed(title="c", image=None,
                       thumbnail=media(url="https://origin/th.png", proxy_url="https://media.discordapp.net/th.png")),
            make_embed(title="d", image=media(url=None, proxy_url=None),
                       thumbnail=media(url="https://origin/th2.png")),
        ]

        await self.build([make_msg(1, "x", embeds=embeds)])

        self.assertEqual([c.args[0] for c in self.url_converter.await_args_list], [
            "https://media.discordapp.net/img.png",
            "https://origin/only.png",
            "https://media.discordapp.net/th.png",
            "https://origin/th2.png",
        ])

    async def test_embed_preview_failure_is_logged(self):
        self.url_converter.side_effect = RuntimeError("fetch failed")
        embed = make_embed(title="a", image=media(url="https://origin/img.png"))

        with patch("src.utils.chat_context.logger.warning") as mock_warning:
            result = await self.build([make_msg(1, "x", embeds=[embed])])

        mock_warning.assert_called_once()
        self.assertEqual(len(result[0]["content"]), 1)

    async def test_assistant_images_are_split_into_follow_up_user_entry(self):
        embed = make_embed(image=media(proxy_url="https://media.discordapp.net/gen.png"))

        result = await self.build([make_msg(7, "here you go", author_id=BOT_ID, embeds=[embed])])

        self.assertEqual([e["role"] for e in result], ["assistant", "user"])
        self.assertEqual(len(result[0]["content"]), 1)
        self.assertIn("[7] <@999>:", result[1]["content"][0]["text"])
        self.assertEqual(result[1]["content"][1]["image_url"]["url"], "data:image/jpeg;base64,EMBED")


if __name__ == "__main__":
    unittest.main()


class TestCostPrefixStripping(unittest.IsolatedAsyncioTestCase):
    async def test_cost_prefix_is_stripped_from_bot_messages(self):
        from src.utils.chat_context import build_chat_context

        bot_msg = MagicMock()
        bot_msg.id = 2
        bot_msg.author.id = 999
        bot_msg.content = "[$0.012 @ z-image] here you go"
        bot_msg.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bot_msg.reference = None
        bot_msg.embeds = []
        bot_msg.attachments = []

        result = await build_chat_context(
            [bot_msg], 999,
            attachment_converter=AsyncMock(), url_converter=AsyncMock(), transcriber=AsyncMock(),
        )

        text = result[0]["content"][0]["text"]
        self.assertTrue(text.endswith("here you go"))
        self.assertNotIn("$0.012", text)
