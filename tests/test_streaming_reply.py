"""Tests for StreamingReply, the live-updating mention reply."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.streaming_reply import CURSOR, StreamingReply, _preview, is_status_only
from src.utils.status_lines import STATUS_LINES, WILDCARDS, flavor


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
    message_service.send_channel_reply = AsyncMock()
    return StreamingReply(channel, message_service), channel, sent, message_service


async def settle():
    """Let scheduled preview updates run."""
    for _ in range(5):
        await asyncio.sleep(0)


class TestStreamingReply(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Plain statuses, so the previews are predictable; the joke lines have their own tests.
        patcher = patch("src.services.streaming_reply.flavor", side_effect=lambda status: status)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_first_push_posts_preview_with_cursor(self):
        streamer, channel, _, _ = make_streamer()
        streamer.push("Hello")
        await settle()

        channel.send.assert_awaited_once_with(content="Hello" + CURSOR, suppress_embeds=True)
        self.assertIsNotNone(streamer._message)

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

    async def test_status_set_while_an_edit_is_in_flight_is_shown_after_it(self):
        streamer, channel, sent, _ = make_streamer()
        release = asyncio.Event()

        async def slow_send(**_kwargs):
            await release.wait()
            return sent

        channel.send.side_effect = slow_send
        with patch("src.services.streaming_reply.EDIT_INTERVAL_SECONDS", 0.01):
            streamer.push("Hello")
            await settle()
            # Arrives mid-send, and nothing else comes in afterwards.
            streamer.status("drawing...")
            release.set()
            await asyncio.sleep(0.05)

        sent.edit.assert_awaited_once_with(content="Hello" + CURSOR + "\n-# drawing...", suppress=True)

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

        message_service.send_channel_reply.assert_awaited_once_with(channel, "whole reply", None)
        channel.send.assert_not_awaited()

    async def test_finish_right_after_push_posts_only_once(self):
        # The preview task was scheduled but hadn't run when the reply finished.
        streamer, channel, _, message_service = make_streamer()
        streamer.push("whole reply")

        await streamer.finish("whole reply", ["file-a"])
        await settle()

        channel.send.assert_not_awaited()
        message_service.send_channel_reply.assert_awaited_once_with(channel, "whole reply", ["file-a"])

    async def test_failed_preview_stops_updates_and_reply_still_sends(self):
        streamer, channel, _, message_service = make_streamer(send_side_effect=RuntimeError("discord down"))
        streamer.push("Hello")
        await settle()
        streamer.push("Hello again")
        await settle()

        await streamer.finish("Hello again!")

        channel.send.assert_awaited_once()
        message_service.send_channel_reply.assert_awaited_once_with(channel, "Hello again!", None)

    async def test_failed_final_edit_replaces_preview_with_fresh_message(self):
        streamer, channel, sent, message_service = make_streamer()
        streamer.push("Hello")
        await settle()
        sent.edit = AsyncMock(side_effect=RuntimeError("edit failed"))

        await streamer.finish("Hello, world")

        sent.delete.assert_awaited_once()
        message_service.send_channel_reply.assert_awaited_once_with(channel, "Hello, world", None)

    async def test_discard_deletes_preview(self):
        streamer, _, sent, _ = make_streamer()
        streamer.push("partial")
        await settle()

        await streamer.discard()

        sent.delete.assert_awaited_once()
        self.assertIsNone(streamer._message)

    async def test_whitespace_push_posts_nothing(self):
        streamer, channel, _, _ = make_streamer()
        streamer.push("  \n")
        await settle()
        channel.send.assert_not_awaited()


    async def test_status_shows_alone_then_under_text_until_more_text_arrives(self):
        streamer, channel, sent, _ = make_streamer()
        with patch("src.services.streaming_reply.EDIT_INTERVAL_SECONDS", 0.01):
            streamer.status("reading links...")
            await settle()
            channel.send.assert_awaited_once_with(content="-# reading links...", suppress_embeds=True)

            streamer.push("Here's")
            await asyncio.sleep(0.03)
            sent.edit.assert_awaited_with(content="Here's" + CURSOR, suppress=True)

            streamer.status("drawing...")
            await asyncio.sleep(0.03)
            sent.edit.assert_awaited_with(content="Here's" + CURSOR + "\n-# drawing...", suppress=True)

            streamer.push("Here's the cat")
            await asyncio.sleep(0.03)
            sent.edit.assert_awaited_with(content="Here's the cat" + CURSOR, suppress=True)

    async def test_empty_push_clears_a_discarded_draft(self):
        streamer, _, sent, _ = make_streamer()
        with patch("src.services.streaming_reply.EDIT_INTERVAL_SECONDS", 0.01):
            streamer.push("draft")
            await settle()
            streamer.push("")
            streamer.status("thinking harder...")
            await asyncio.sleep(0.03)
        sent.edit.assert_awaited_with(content="-# thinking harder...", suppress=True)


class TestStatusLines(unittest.IsolatedAsyncioTestCase):
    def _statuses(self):
        from src.bot.handlers import mention
        from src.services import anthropic_service
        from tests.test_chat_tools import _full_tools
        tools, _ = _full_tools(
            irc_log=MagicMock(), quote_file=MagicMock(), url_content_fetcher=AsyncMock(),
            self_state_updater=AsyncMock(), reminder_service=MagicMock(), timezone_store=MagicMock(),
            video_summarizer=AsyncMock(), video_watcher=AsyncMock(),
        )
        named = [v for module in (mention, anthropic_service) for k, v in vars(module).items()
                 if k.endswith("_STATUS") and isinstance(v, str)]
        return set(named) | {tool.status for tool in tools.values()}

    def test_every_status_has_joke_lines(self):
        statuses = self._statuses()
        self.assertGreater(len(statuses), 30)
        self.assertEqual(statuses - set(STATUS_LINES), set())
        self.assertEqual(set(STATUS_LINES) - statuses, set(), "lines for a status nothing shows")

    def test_every_line_still_reads_as_a_status(self):
        for line in [line for lines in STATUS_LINES.values() for line in lines] + list(WILDCARDS):
            self.assertTrue(is_status_only(_preview("", line, 2000)), line)
            self.assertLessEqual(len(line), 90, line)

    def test_flavor_picks_a_line_and_sometimes_a_wildcard(self):
        with patch("src.utils.status_lines.random.random", return_value=0.5):
            self.assertIn(flavor("thinking..."), STATUS_LINES["thinking..."])
        with patch("src.utils.status_lines.random.random", return_value=0.0):
            self.assertIn(flavor("thinking..."), WILDCARDS)
        self.assertEqual(flavor("something new..."), "something new...")

    async def test_a_repeated_status_keeps_its_line_and_a_new_step_picks_again(self):
        streamer, _, _, _ = make_streamer()
        with patch("src.services.streaming_reply.flavor", side_effect=["one...", "two...", "three..."]) as pick:
            streamer.status("thinking...")
            streamer.status("thinking...")
            self.assertEqual(streamer._status, "one...")
            streamer.status("drawing...")
            self.assertEqual(streamer._status, "two...")
            streamer.push("text")
            streamer.status("drawing...")
            self.assertEqual(streamer._status, "three...")
            self.assertEqual(pick.call_count, 3)
            await settle()


class TestPreview(unittest.TestCase):
    def test_long_text_is_cut_to_fit(self):
        preview = _preview("x" * 5000, "", 2000)
        self.assertEqual(len(preview), 2000)
        self.assertTrue(preview.endswith("…" + CURSOR))

    def test_long_text_leaves_room_for_the_status(self):
        preview = _preview("x" * 5000, "drawing...", 2000)
        self.assertEqual(len(preview), 2000)
        self.assertTrue(preview.endswith("…" + CURSOR + "\n-# drawing..."))


class TestIsStatusOnly(unittest.TestCase):
    def test_a_preview_with_only_its_status_is_recognized(self):
        self.assertTrue(is_status_only(_preview("", "reading the chat...", 2000)))

    def test_anything_with_reply_text_is_not(self):
        self.assertFalse(is_status_only(_preview("Here's the answer", "thinking...", 2000)))
        self.assertFalse(is_status_only("Here's the answer"))
        self.assertFalse(is_status_only("-# a small note"))
        self.assertFalse(is_status_only(""))


if __name__ == "__main__":
    unittest.main()
