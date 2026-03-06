"""Tests for YouTube transcript helpers."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.utils import youtube_utils


class FakeYoutubeApi:
    def fetch(self, video_id):
        return [SimpleNamespace(text="hello"), SimpleNamespace(text="world")]


class TestYoutubeUtils(unittest.IsolatedAsyncioTestCase):
    def test_extract_video_ids_handles_multiple_formats_and_deduplicates(self):
        text = (
            "https://www.youtube.com/watch?v=abcdefghijk "
            "https://youtu.be/abcdefghijk "
            "https://youtube.com/shorts/lmnopqrstuv"
        )

        result = youtube_utils.extract_video_ids(text)

        self.assertEqual(result, ["abcdefghijk", "lmnopqrstuv"])

    async def test_get_transcript_returns_cached_values(self):
        with patch("src.utils.youtube_utils._transcript_cache", {"abc": "cached", "bad": None}):
            self.assertEqual(await youtube_utils.get_transcript("abc"), "cached")
            self.assertIsNone(await youtube_utils.get_transcript("bad"))

    async def test_get_transcript_fetches_and_logs(self):
        fake_cache = {}

        async def fake_to_thread(fn):
            return fn()

        with patch("src.utils.youtube_utils._transcript_cache", fake_cache), patch(
            "src.utils.youtube_utils.TRANSCRIPT_PROXY", ""
        ), patch("src.utils.youtube_utils.YouTubeTranscriptApi", return_value=FakeYoutubeApi()), patch(
            "src.utils.youtube_utils.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)
        ), patch("src.utils.youtube_utils.log_api_request", new=AsyncMock()) as mock_log:
            result = await youtube_utils.get_transcript("abcdefghijk")

        self.assertEqual(result, "hello world")
        self.assertEqual(fake_cache["abcdefghijk"], "hello world")
        self.assertIn("video_id = \"abcdefghijk\"", mock_log.await_args.kwargs["request_body"])

    async def test_get_transcript_returns_none_on_failure(self):
        with patch("src.utils.youtube_utils._transcript_cache", {}), patch(
            "src.utils.youtube_utils.asyncio.to_thread", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            result = await youtube_utils.get_transcript("abcdefghijk")

        self.assertIsNone(result)
