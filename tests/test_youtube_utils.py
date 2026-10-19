"""Tests for YouTube transcript helpers."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from tests.config_helpers import cfg

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

    def setUp(self):
        # Keep the shared fetch-failure store out of these tests.
        patcher = patch("src.utils.cache_utils._failure_store", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_get_transcript_returns_cached_value_without_fetching(self):
        with patch("src.utils.youtube_utils._transcript_cache", {"abc": "cached"}), patch(
            "src.utils.youtube_utils.YouTubeTranscriptApi"
        ) as mock_api:
            self.assertEqual(await youtube_utils.get_transcript("abc"), "cached")
        mock_api.assert_not_called()

    async def test_get_transcript_cache_only_miss_returns_none_without_fetching(self):
        with patch("src.utils.youtube_utils._transcript_cache", {}), patch(
            "src.utils.youtube_utils.YouTubeTranscriptApi"
        ) as mock_api:
            self.assertIsNone(await youtube_utils.get_transcript("abc", cache_only=True))
        mock_api.assert_not_called()

    async def test_get_transcript_fetches_and_caches(self):
        fake_cache = {}

        with patch("src.utils.youtube_utils._transcript_cache", fake_cache), patch(
            "src.config._cached_config", cfg(transcript_proxy="")
        ), patch("src.utils.youtube_utils.YouTubeTranscriptApi", return_value=FakeYoutubeApi()) as mock_api:
            result = await youtube_utils.get_transcript("abcdefghijk")

        self.assertEqual(result, "hello world")
        self.assertEqual(fake_cache["abcdefghijk"], "hello world")
        mock_api.assert_called_once_with()

    async def test_get_transcript_uses_proxy_when_configured(self):
        with patch("src.utils.youtube_utils._transcript_cache", {}), patch(
            "src.config._cached_config", cfg(transcript_proxy="http://proxy:1")
        ), patch("src.utils.youtube_utils.YouTubeTranscriptApi", return_value=FakeYoutubeApi()) as mock_api:
            self.assertEqual(await youtube_utils.get_transcript("abcdefghijk"), "hello world")

        proxy_config = mock_api.call_args.kwargs["proxy_config"]
        self.assertEqual(proxy_config.https_url, "http://proxy:1")

    async def test_get_transcript_returns_none_on_failure(self):
        fake_cache = {}
        with patch("src.utils.youtube_utils._transcript_cache", fake_cache), patch(
            "src.utils.youtube_utils.asyncio.to_thread", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            result = await youtube_utils.get_transcript("abcdefghijk")

        self.assertIsNone(result)
        self.assertEqual(fake_cache, {})


if __name__ == "__main__":
    unittest.main()
