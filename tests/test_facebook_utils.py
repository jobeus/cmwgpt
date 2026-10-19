"""Tests for Facebook transcript helpers."""

import unittest
from unittest.mock import AsyncMock, patch

from src.utils import facebook_utils


class TestFacebookUrlExtraction(unittest.TestCase):
    def test_extract_facebook_urls_deduplicates(self):
        text = "https://facebook.com/a/videos/1 https://fb.watch/abc https://facebook.com/a/videos/1"

        urls = facebook_utils.extract_facebook_urls(text)

        self.assertEqual(urls, ["https://facebook.com/a/videos/1", "https://fb.watch/abc"])


class TestFacebookTranscript(unittest.IsolatedAsyncioTestCase):
    URL = "https://facebook.com/a/videos/1"

    def setUp(self):
        # Keep the shared fetch-failure store out of these tests.
        patcher = patch("src.utils.cache_utils._failure_store", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_delegates_to_shared_downloader_with_platform_cache(self):
        with patch(
            "src.utils.facebook_utils.download_and_transcribe_video",
            new_callable=AsyncMock, return_value="transcribed text",
        ) as mock_dl:
            result = await facebook_utils.get_facebook_transcript(self.URL)

        self.assertEqual(result, "transcribed text")
        mock_dl.assert_awaited_once_with(
            self.URL, facebook_utils._facebook_cache,
            label="Facebook", file_prefix="facebook", cache_only=False,
        )

    async def test_cache_only_is_forwarded(self):
        with patch(
            "src.utils.facebook_utils.download_and_transcribe_video",
            new_callable=AsyncMock, return_value=None,
        ) as mock_dl:
            result = await facebook_utils.get_facebook_transcript(self.URL, cache_only=True)

        self.assertIsNone(result)
        self.assertTrue(mock_dl.await_args.kwargs["cache_only"])

    async def test_returns_cached_transcript_without_download(self):
        with patch("src.utils.facebook_utils._facebook_cache", {self.URL: "cached"}), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL"
        ) as mock_ydl:
            self.assertEqual(await facebook_utils.get_facebook_transcript(self.URL), "cached")
        mock_ydl.assert_not_called()

    async def test_failure_is_remembered_by_skip_recent_failures(self):
        recorded = []
        with patch("src.utils.cache_utils._failure_store", return_value=None), patch(
            "src.utils.cache_utils._record_result", side_effect=lambda k, r: recorded.append((k, r))
        ), patch(
            "src.utils.facebook_utils.download_and_transcribe_video",
            new_callable=AsyncMock, return_value=None,
        ):
            await facebook_utils.get_facebook_transcript(self.URL)
            await facebook_utils.get_facebook_transcript(self.URL, cache_only=True)

        # Only the real fetch is recorded; the cache_only lookup is not.
        self.assertEqual(recorded, [("facebook:" + self.URL, None)])


if __name__ == "__main__":
    unittest.main()
