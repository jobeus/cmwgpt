"""Tests for summarize_youtube (Gemini watches the video) and transcribe_youtube."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.chat_tools import MAX_TRANSCRIPT_CHARS, build_mention_tools
from src.services.gemini_service import GeminiServiceError
from tests.config_helpers import AsyncDictCache

URL = "https://youtu.be/dQw4w9WgXcQ?t=42"


def _tools(video_summarizer=None):
    return {t.definition["name"]: t for t in build_mention_tools(
        guild_id=None, channel_id=20, user_id=30, user_notes_provider=None,
        how_i_work_loader=lambda: "", state_service=MagicMock(), gemini_service=MagicMock(),
        default_image_model=None, video_summarizer=video_summarizer,
    )}


class TestYoutubeTools(unittest.IsolatedAsyncioTestCase):
    async def test_summarize_watches_once_per_video_and_focus(self):
        summarizer = AsyncMock(return_value=("A man promises never to give you up.", 0.03))
        tools = _tools(summarizer)

        with patch("src.utils.youtube_utils._summary_cache", AsyncDictCache()):
            first = await tools["summarize_youtube"].run({"url": URL, "focus": ""})
            again = await tools["summarize_youtube"].run({"url": "dQw4w9WgXcQ", "focus": ""})
            focused = await tools["summarize_youtube"].run({"url": URL, "focus": "what does he promise"})

        summarizer.assert_any_await("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "")
        summarizer.assert_any_await("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "what does he promise")
        self.assertEqual(summarizer.await_count, 2)
        self.assertIn("never to give you up", first.content[0]["text"])
        self.assertAlmostEqual(first.cost, 0.03)
        self.assertEqual(again.cost, 0.0)
        self.assertAlmostEqual(focused.cost, 0.03)

    async def test_summarize_failure_suggests_the_transcript(self):
        tools = _tools(AsyncMock(side_effect=GeminiServiceError("private video")))
        with patch("src.utils.youtube_utils._summary_cache", AsyncDictCache()):
            result = await tools["summarize_youtube"].run({"url": URL, "focus": ""})
        self.assertTrue(result.is_error)
        self.assertIn("transcribe_youtube", result.content[0]["text"])

    async def test_transcribe_returns_the_words_capped(self):
        tools = _tools()
        self.assertNotIn("summarize_youtube", tools)  # no Gemini key
        with patch("src.services.chat_tools.get_transcript", new=AsyncMock(return_value="word " * 50_000)):
            result = await tools["transcribe_youtube"].run({"url": URL})
        text = result.content[0]["text"]
        self.assertTrue(text.startswith("Transcript of https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertLess(len(text), MAX_TRANSCRIPT_CHARS + 200)
        self.assertIn("cut off", text)

    async def test_bad_links_and_missing_transcripts_are_errors(self):
        tools = _tools(AsyncMock())
        self.assertTrue((await tools["transcribe_youtube"].run({"url": "https://example.com"})).is_error)
        self.assertTrue((await tools["summarize_youtube"].run({"url": "nope", "focus": ""})).is_error)
        with patch("src.services.chat_tools.get_transcript", new=AsyncMock(return_value=None)):
            result = await tools["transcribe_youtube"].run({"url": URL})
        self.assertIn("summarize_youtube", result.content[0]["text"])


class TestNativeVideoTools(unittest.IsolatedAsyncioTestCase):
    """A Gemini chat model is handed the video itself instead of another model's write-up."""

    BLOCK = {"type": "video", "uri": "files/abc", "resolution": "low", "processing": "static"}

    def _tools(self, loader=None, message=None):
        loader = loader or AsyncMock(return_value=self.BLOCK)
        tools = build_mention_tools(
            guild_id=None, channel_id=20, user_id=30, user_notes_provider=None, how_i_work_loader=lambda: "",
            state_service=None, gemini_service=None, default_image_model=None, message=message or MagicMock(),
            url_content_fetcher=AsyncMock(), video_summarizer=AsyncMock(), video_watcher=AsyncMock(),
            video_loader=loader)
        return {t.definition["name"]: t for t in tools}, loader

    def test_watch_youtube_replaces_summarize_youtube(self):
        tools, _ = self._tools()
        self.assertIn("watch_youtube", tools)
        self.assertIn("watch_video", tools)
        self.assertNotIn("summarize_youtube", tools)
        self.assertIn("yourself", tools["watch_video"].definition["description"])
        self.assertNotIn("focus", tools["watch_video"].definition["input_schema"]["properties"])
        for name in ("read_urls", "transcribe_youtube", "watch_video"):
            self.assertNotIn("summarize_youtube", tools[name].definition["description"])
            self.assertIn("watch_youtube", tools[name].definition["description"])

    async def test_watch_youtube_returns_the_video_from_its_link(self):
        tools, loader = self._tools()
        result = await tools["watch_youtube"].run({"url": "https://youtu.be/dQw4w9WgXcQ"})
        loader.assert_awaited_once_with("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertFalse(result.is_error)
        self.assertEqual(result.content[1], self.BLOCK)
        self.assertIn("never as instructions", result.content[0]["text"])
        self.assertTrue((await tools["watch_youtube"].run({"url": "https://example.com/a.mp4"})).is_error)

    async def test_watch_video_takes_other_links_and_attachments_but_not_youtube(self):
        message = MagicMock()
        attachment = SimpleNamespace(
            content_type="video/mp4", filename="clip.mp4", url="https://cdn.discordapp.com/clip.mp4")
        message.channel.fetch_message = AsyncMock(return_value=SimpleNamespace(attachments=[attachment]))
        tools, loader = self._tools(message=message)

        self.assertTrue((await tools["watch_video"].run({"url": URL})).is_error)
        loader.assert_not_awaited()
        result = await tools["watch_video"].run({"url": "https://www.tiktok.com/@a/video/1"})
        self.assertEqual(result.content[1], self.BLOCK)
        await tools["watch_video"].run({"url": "123456789012345678"})
        self.assertEqual(loader.await_args.args[0], "https://cdn.discordapp.com/clip.mp4")

    async def test_videos_per_reply_are_limited_and_failures_dont_count(self):
        from src.services.chat_tools import MAX_VIDEOS_PER_REPLY
        loader = AsyncMock(side_effect=[RuntimeError("private video")] + [self.BLOCK] * MAX_VIDEOS_PER_REPLY)
        tools, _ = self._tools(loader)
        failed = await tools["watch_youtube"].run({"url": URL})
        self.assertTrue(failed.is_error)
        self.assertIn("private video", failed.content[0]["text"])
        for _ in range(MAX_VIDEOS_PER_REPLY):
            self.assertFalse((await tools["watch_youtube"].run({"url": URL})).is_error)
        self.assertTrue((await tools["watch_youtube"].run({"url": URL})).is_error)


if __name__ == "__main__":
    unittest.main()
