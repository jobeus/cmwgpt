"""Tests for TikTok transcript helpers."""

import unittest
from unittest.mock import AsyncMock, patch

from src.utils import tiktok_utils
from tests.config_helpers import AsyncDictCache


class TestTikTokUrlExtraction(unittest.TestCase):
    def test_extract_tiktok_urls_deduplicates(self):
        text = "https://vt.tiktok.com/abc123/ https://www.tiktok.com/@user/video/123 https://vt.tiktok.com/abc123/"

        urls = tiktok_utils.extract_tiktok_urls(text)

        self.assertEqual(urls, ["https://vt.tiktok.com/abc123/", "https://www.tiktok.com/@user/video/123"])

    def test_extract_tiktok_urls_matches_vm_short_links(self):
        text = "check this https://vm.tiktok.com/ZMabc123/ out"

        urls = tiktok_utils.extract_tiktok_urls(text)

        self.assertEqual(urls, ["https://vm.tiktok.com/ZMabc123/"])

    def test_extract_tiktok_urls_matches_t_share_links(self):
        text = "https://www.tiktok.com/t/ZTabc123/ and https://tiktok.com/t/ZTdef456"

        urls = tiktok_utils.extract_tiktok_urls(text)

        self.assertEqual(urls, ["https://www.tiktok.com/t/ZTabc123/", "https://tiktok.com/t/ZTdef456"])

    def test_extract_tiktok_urls_matches_video_links_without_www(self):
        text = "https://tiktok.com/@some.user_1/video/7300000000000000000"

        urls = tiktok_utils.extract_tiktok_urls(text)

        self.assertEqual(urls, ["https://tiktok.com/@some.user_1/video/7300000000000000000"])


class TestTikTokTranscript(unittest.IsolatedAsyncioTestCase):
    URL = "https://vt.tiktok.com/abc123/"

    def setUp(self):
        # Keep the shared fetch-failure store out of these tests.
        patcher = patch("src.utils.cache_utils._failure_store", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_delegates_to_shared_downloader_with_platform_cache(self):
        with patch(
            "src.utils.tiktok_utils.download_and_transcribe_video",
            new_callable=AsyncMock, return_value="transcribed text",
        ) as mock_dl:
            result = await tiktok_utils.get_tiktok_transcript(self.URL)

        self.assertEqual(result, "transcribed text")
        mock_dl.assert_awaited_once_with(
            self.URL, tiktok_utils._tiktok_cache,
            label="TikTok", file_prefix="tiktok", cache_only=False,
        )

    async def test_cache_only_is_forwarded(self):
        with patch(
            "src.utils.tiktok_utils.download_and_transcribe_video",
            new_callable=AsyncMock, return_value=None,
        ) as mock_dl:
            result = await tiktok_utils.get_tiktok_transcript(self.URL, cache_only=True)

        self.assertIsNone(result)
        self.assertTrue(mock_dl.await_args.kwargs["cache_only"])

    async def test_returns_cached_transcript_without_download(self):
        with patch("src.utils.tiktok_utils._tiktok_cache", AsyncDictCache({self.URL: "cached"})), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL"
        ) as mock_ydl:
            self.assertEqual(await tiktok_utils.get_tiktok_transcript(self.URL), "cached")
        mock_ydl.assert_not_called()

    async def test_failure_is_remembered_by_skip_recent_failures(self):
        recorded = []
        with patch("src.utils.cache_utils._failure_store", return_value=None), patch(
            "src.utils.cache_utils._record_result", side_effect=lambda k, r: recorded.append((k, r))
        ), patch(
            "src.utils.tiktok_utils.download_and_transcribe_video",
            new_callable=AsyncMock, return_value=None,
        ):
            await tiktok_utils.get_tiktok_transcript(self.URL)
            await tiktok_utils.get_tiktok_transcript(self.URL, cache_only=True)

        # Only the real fetch is recorded; the cache_only lookup is not.
        self.assertEqual(recorded, [("tiktok:" + self.URL, None)])


if __name__ == "__main__":
    unittest.main()
