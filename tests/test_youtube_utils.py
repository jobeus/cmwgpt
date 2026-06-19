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

    async def test_get_transcript_returns_cached_values_and_retries_legacy_failure_sentinels(self):
        legacy_cache = {"abc": "cached", "bad": None}

        async def fake_to_thread(fn):
            return fn()

        with patch("src.utils.youtube_utils._transcript_cache", legacy_cache), patch(
            "src.config._cached_config", cfg(transcript_proxy="")
        ), patch("src.utils.youtube_utils.YouTubeTranscriptApi", return_value=FakeYoutubeApi()), patch(
            "src.utils.youtube_utils.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)
        ), patch("src.utils.youtube_utils.log_pipeline_step", new=AsyncMock()):
            self.assertEqual(await youtube_utils.get_transcript("abc"), "cached")
            self.assertEqual(await youtube_utils.get_transcript("bad"), "hello world")

        self.assertEqual(legacy_cache["bad"], "hello world")

    async def test_get_transcript_fetches_and_logs(self):
        fake_cache = {}

        async def fake_to_thread(fn):
            return fn()

        with patch("src.utils.youtube_utils._transcript_cache", fake_cache), patch(
            "src.config._cached_config", cfg(transcript_proxy="")
        ), patch("src.utils.youtube_utils.YouTubeTranscriptApi", return_value=FakeYoutubeApi()), patch(
            "src.utils.youtube_utils.asyncio.to_thread", new=AsyncMock(side_effect=fake_to_thread)
        ), patch("src.utils.youtube_utils.log_pipeline_step", new=AsyncMock()) as mock_log:
            result = await youtube_utils.get_transcript("abcdefghijk")

        self.assertEqual(result, "hello world")
        self.assertEqual(fake_cache["abcdefghijk"], "hello world")
        self.assertIn("video_id = \"abcdefghijk\"", mock_log.await_args.kwargs["request_replay"]["python"])

    async def test_get_transcript_returns_none_on_failure(self):
        with patch("src.utils.youtube_utils._transcript_cache", {}), patch(
            "src.utils.youtube_utils.asyncio.to_thread", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            result = await youtube_utils.get_transcript("abcdefghijk")

        self.assertIsNone(result)
