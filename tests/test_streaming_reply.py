"""Tests for StreamingReply, the live-updating mention reply."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.streaming_reply import CURSOR, StreamingReply, _preview


def make_streamer(*, send_side_effect=None):
    sent = MagicMock()
    sent.edit = AsyncMock()
    sent.delete = AsyncMock()
    channel = MagicMock()
    channel.send = AsyncMock(return_value=sent, side_effect=send_side_effect)
    message_service = MagicMock()
    message_service.DISCORD_MESSAGE_LIMIT = 2000
    message_service.fit_to_discord = AsyncMock(side_effect=lambda text: text)
    message_service.send_channel_reply = AsyncMock()
    message_service.send_channel_reply_with_files = AsyncMock()
    return StreamingReply(channel, message_service), channel, sent, message_service


async def settle():
    """Let scheduled preview updates run."""
    for _ in range(5):
        await asyncio.sleep(0)


class TestStreamingReply(unittest.IsolatedAsyncioTestCase):
    async def test_first_push_posts_preview_with_cursor(self):
        streamer, channel, _, _ = make_streamer()
        streamer.push("Hello")
        await settle()

        channel.send.assert_awaited_once_with(content="Hello" + CURSOR, suppress_embeds=True)
        self.assertTrue(streamer.started)

    async def test_pushes_within_interval_coalesce_into_one_edit(self):
        streamer, channel, sent, _ = make_streamer()
        with patch("src.services.streaming_reply.EDIT_INTERVAL_SECONDS", 0.05):
            streamer.push("Hel")
            await settle()
            streamer.push("Hello")
            streamer.push("Hello, wor")
            streamer.push("Hello, world")
            await asyncio.sleep(0.1)

        channel.send.assert_awaited_once()
        sent.edit.assert_awaited_once_with(content="Hello, world" + CURSOR, suppress=True)

    async def test_finish_edits_preview_into_final_reply(self):
        streamer, channel, sent, message_service = make_streamer()
        streamer.push("Hello")
        await settle()
        streamer.push("Hello, world")

        await streamer.finish("[$0.010] Hello, world!")

        message_service.fit_to_discord.assert_awaited_once_with("[$0.010] Hello, world!")
        sent.edit.assert_awaited_once_with(content="[$0.010] Hello, world!", suppress=True)
        channel.send.assert_awaited_once()
        message_service.send_channel_reply.assert_not_awaited()

    async def test_finish_attaches_files_to_preview(self):
        streamer, _, sent, _ = make_streamer()
        streamer.push("Here")
        await settle()

        await streamer.finish("Here you go", ["file-a"])

        sent.edit.assert_awaited_once_with(content="Here you go", attachments=["file-a"], suppress=True)

    async def test_finish_without_preview_sends_normally(self):
        streamer, channel, _, message_service = make_streamer()

        await streamer.finish("whole reply")

        message_service.send_channel_reply.assert_awaited_once_with(channel, "whole reply")
        channel.send.assert_not_awaited()

    async def test_finish_right_after_push_posts_only_once(self):
        # The preview task was scheduled but hadn't run when the reply finished.
        streamer, channel, _, message_service = make_streamer()
        streamer.push("whole reply")

        await streamer.finish("whole reply", ["file-a"])
        await settle()

        channel.send.assert_not_awaited()
        message_service.send_channel_reply_with_files.assert_awaited_once_with(channel, "whole reply", ["file-a"])

    async def test_failed_preview_stops_updates_and_reply_still_sends(self):
        streamer, channel, _, message_service = make_streamer(send_side_effect=RuntimeError("discord down"))
        streamer.push("Hello")
        await settle()
        streamer.push("Hello again")
        await settle()

        await streamer.finish("Hello again!")

        channel.send.assert_awaited_once()
        message_service.send_channel_reply.assert_awaited_once_with(channel, "Hello again!")

    async def test_failed_final_edit_replaces_preview_with_fresh_message(self):
        streamer, channel, sent, message_service = make_streamer()
        streamer.push("Hello")
        await settle()
        sent.edit = AsyncMock(side_effect=RuntimeError("edit failed"))

        await streamer.finish("Hello, world")

        sent.delete.assert_awaited_once()
        message_service.send_channel_reply.assert_awaited_once_with(channel, "Hello, world")

    async def test_discard_deletes_preview(self):
        streamer, _, sent, _ = make_streamer()
        streamer.push("partial")
        await settle()

        await streamer.discard()

        sent.delete.assert_awaited_once()
        self.assertFalse(streamer.started)

    async def test_whitespace_push_posts_nothing(self):
        streamer, channel, _, _ = make_streamer()
        streamer.push("  \n")
        await settle()
        channel.send.assert_not_awaited()


class TestPreview(unittest.TestCase):
    def test_long_text_is_cut_to_fit(self):
        preview = _preview("x" * 5000, 2000)
        self.assertEqual(len(preview), 2000)
        self.assertTrue(preview.endswith("…" + CURSOR))


if __name__ == "__main__":
    unittest.main()
