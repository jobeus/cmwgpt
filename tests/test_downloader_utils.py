"""Tests for aggregated downloader utility behavior."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.utils import downloader_utils


class FakeRequest:
    def __init__(self):
        self.method = "POST"
        self.url = "https://api.groq.com/openai/v1/audio/transcriptions"
        self.headers = {"X-Test": "1"}
        self.content = b"abc123"
        self.was_read = False

    def read(self):
        self.was_read = True


class FakeGroqResponse:
    def __init__(self):
        self.request = FakeRequest()
        self.status_code = 200
        self.headers = {"Content-Type": "text/plain"}


class TestDownloaderUtils(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_all_url_content_returns_empty_for_no_text(self):
        self.assertEqual(await downloader_utils.fetch_all_url_content(""), "")

    async def test_fetch_all_url_content_aggregates_all_supported_sources(self):
        groq_response = FakeGroqResponse()

        async def fake_to_thread(func, url):
            if func is downloader_utils.get_tiktok_transcript:
                return {
                    "transcript_text": "tiktok transcript",
                    "groq_response": groq_response,
                    "audio_artifact": {"name": "tik.mp3", "media_type": "audio/mpeg"},
                    "download_strategy": "direct",
                }
            if func is downloader_utils.get_facebook_transcript:
                return {
                    "transcript_text": "facebook transcript",
                    "groq_response": groq_response,
                    "audio_artifact": {"name": "fb.mp3", "media_type": "audio/mpeg"},
                    "download_strategy": "direct",
                }
            raise AssertionError("unexpected to_thread call")

        with patch("src.utils.downloader_utils.extract_video_ids", return_value=["abc"]), patch(
            "src.utils.downloader_utils.get_transcript", new=AsyncMock(return_value="youtube transcript")
        ), patch("src.utils.downloader_utils.extract_tiktok_urls", return_value=["https://vt.tiktok.com/1"]), patch(
            "src.utils.downloader_utils.extract_twitter_urls", return_value=["https://x.com/a/status/1"]
        ), patch(
            "src.utils.downloader_utils.get_tweet_context", new=AsyncMock(return_value="tweet context")
        ), patch("src.utils.downloader_utils.extract_facebook_urls", return_value=["https://fb.watch/1"]), patch(
            "src.utils.downloader_utils.extract_target_urls", return_value=["https://example.com/article"]
        ), patch(
            "src.utils.downloader_utils.get_article_text", new=AsyncMock(return_value="article text")
        ), patch("src.utils.downloader_utils.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)), patch(
            "src.utils.downloader_utils.log_api_request", new=AsyncMock()
        ) as mock_log_api, patch(
            "src.utils.downloader_utils.log_pipeline_step", new=AsyncMock()
        ) as mock_log_step:
            result = await downloader_utils.fetch_all_url_content("look at these links")

        self.assertIn("Included youtube link transcript", result)
        self.assertIn("youtube transcript", result)
        self.assertIn("Included TikTok video transcript", result)
        self.assertIn("tweet context", result)
        self.assertIn("Included Facebook video transcript", result)
        self.assertIn("Included article content follows", result)
        self.assertTrue(result.endswith("\n\n"))
        self.assertEqual(mock_log_api.await_count, 2)
        self.assertEqual(mock_log_step.await_count, 6)
        self.assertTrue(groq_response.request.was_read)

    async def test_fetch_all_url_content_continues_when_some_sources_fail(self):
        async def fake_to_thread(func, url):
            raise RuntimeError(f"fail {url}")

        with patch("src.utils.downloader_utils.extract_video_ids", return_value=["abc"]), patch(
            "src.utils.downloader_utils.get_transcript", new=AsyncMock(side_effect=RuntimeError("yt fail"))
        ), patch("src.utils.downloader_utils.extract_tiktok_urls", return_value=["https://vt.tiktok.com/1"]), patch(
            "src.utils.downloader_utils.extract_twitter_urls", return_value=["https://x.com/a/status/1"]
        ), patch(
            "src.utils.downloader_utils.get_tweet_context", new=AsyncMock(return_value="tweet ok")
        ), patch("src.utils.downloader_utils.extract_facebook_urls", return_value=["https://fb.watch/1"]), patch(
            "src.utils.downloader_utils.extract_target_urls", return_value=["https://example.com/article"]
        ), patch(
            "src.utils.downloader_utils.get_article_text", new=AsyncMock(return_value=None)
        ), patch("src.utils.downloader_utils.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)):
            result = await downloader_utils.fetch_all_url_content("mixed")

        self.assertIn("tweet ok", result)
        self.assertNotIn("youtube transcript", result)
