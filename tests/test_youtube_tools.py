"""Tests for summarize_youtube (Gemini watches the video) and transcribe_youtube."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.chat_tools import MAX_TRANSCRIPT_CHARS, build_mention_tools
from src.services.cost_service import cost_scope
from src.services.gemini_service import GeminiService, GeminiServiceError
from tests.config_helpers import AsyncDictCache

URL = "https://youtu.be/dQw4w9WgXcQ?t=42"


def _tools(video_summarizer=None):
    return {t.definition["name"]: t for t in build_mention_tools(
        guild_id=None, channel_id=20, user_id=30, user_notes_provider=None, channel_summary_provider=None,
        how_i_work_loader=lambda: "", state_service=MagicMock(), gemini_service=MagicMock(),
        runpod_service=MagicMock(), default_draw_model=None, video_summarizer=video_summarizer,
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


class TestGeminiWatchesVideos(unittest.IsolatedAsyncioTestCase):
    async def test_sends_the_video_at_low_resolution_and_records_the_cost(self):
        service = GeminiService(api_key="key")
        usage = SimpleNamespace(prompt_token_count=100_000, cached_content_token_count=0,
                                candidates_token_count=500, thoughts_token_count=200)
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(return_value=SimpleNamespace(text=" Summary. ", usage_metadata=usage))
        service._client = client

        with cost_scope() as ledger:
            text, cost = await service.summarize_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "the ending")

        self.assertEqual(text, "Summary.")
        self.assertAlmostEqual(cost, (100_000 * 1.50 + 700 * 9.00) / 1_000_000)
        self.assertAlmostEqual(ledger.by_provider["gemini-direct"], cost)
        kwargs = client.aio.models.generate_content.await_args.kwargs
        parts = kwargs["contents"].parts
        self.assertEqual(parts[0].file_data.file_uri, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("the ending", parts[1].text)
        self.assertEqual(kwargs["config"].media_resolution.name, "MEDIA_RESOLUTION_LOW")

    async def test_api_errors_become_service_errors(self):
        service = GeminiService(api_key="key")
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(side_effect=RuntimeError("Video unavailable"))
        service._client = client
        with self.assertRaises(GeminiServiceError):
            await service.summarize_youtube("https://www.youtube.com/watch?v=x", "")


if __name__ == "__main__":
    unittest.main()
