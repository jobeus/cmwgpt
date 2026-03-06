"""Tests for Facebook transcript helpers."""

import unittest
from unittest.mock import MagicMock, mock_open, patch

from src.utils import facebook_utils


class FakeYoutubeDL:
    def __init__(self, opts, *, should_fail=False, filename="/tmp/facebook_1.webm"):
        self.opts = opts
        self.should_fail = should_fail
        self.filename = filename

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=True):
        if self.should_fail:
            raise RuntimeError("download failed")
        return {"id": "1"}

    def prepare_filename(self, info):
        return self.filename


class FakeHttpxClientContext:
    def __init__(self, client):
        self._client = client

    def __enter__(self):
        return self._client

    def __exit__(self, exc_type, exc, tb):
        return False


class TestFacebookUtils(unittest.TestCase):
    def test_extract_facebook_urls_deduplicates(self):
        text = "https://facebook.com/a/videos/1 https://fb.watch/abc https://facebook.com/a/videos/1"

        urls = facebook_utils.extract_facebook_urls(text)

        self.assertEqual(urls, ["https://facebook.com/a/videos/1", "https://fb.watch/abc"])

    def test_get_facebook_transcript_returns_cached_values(self):
        with patch("src.utils.facebook_utils._facebook_cache", {"u1": "cached", "u2": None}):
            self.assertEqual(facebook_utils.get_facebook_transcript("u1"), ("cached", None))
            self.assertIsNone(facebook_utils.get_facebook_transcript("u2"))

    def test_get_facebook_transcript_requires_api_key(self):
        with patch("src.utils.facebook_utils.GROQ_API_KEY", ""):
            self.assertIsNone(facebook_utils.get_facebook_transcript("https://fb.watch/abc"))

    def test_get_facebook_transcript_downloads_and_transcribes(self):
        fake_cache = {}
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.text = "transcribed text"
        fake_client = MagicMock()
        fake_client.post.return_value = fake_response

        with patch("src.utils.facebook_utils._facebook_cache", fake_cache), patch(
            "src.utils.facebook_utils.GROQ_API_KEY", "groq-key"
        ), patch("src.utils.facebook_utils.yt_dlp.YoutubeDL", side_effect=lambda opts: FakeYoutubeDL(opts)), patch(
            "src.utils.facebook_utils.os.path.exists", return_value=True
        ), patch("builtins.open", mock_open(read_data=b"audio-bytes")), patch(
            "src.utils.facebook_utils.httpx.Client", side_effect=lambda **kwargs: FakeHttpxClientContext(fake_client)
        ), patch("src.utils.facebook_utils.os.remove") as mock_remove:
            result = facebook_utils.get_facebook_transcript("https://facebook.com/a/videos/1")

        self.assertEqual(result, ("transcribed text", fake_response))
        self.assertEqual(fake_cache["https://facebook.com/a/videos/1"], "transcribed text")
        mock_remove.assert_called_once()

    def test_get_facebook_transcript_returns_none_when_download_fails_without_proxy(self):
        with patch("src.utils.facebook_utils.GROQ_API_KEY", "groq-key"), patch(
            "src.utils.facebook_utils.TRANSCRIPT_PROXY", ""
        ), patch("src.utils.facebook_utils.yt_dlp.YoutubeDL", side_effect=lambda opts: FakeYoutubeDL(opts, should_fail=True)):
            self.assertIsNone(facebook_utils.get_facebook_transcript("https://facebook.com/a/videos/1"))

    def test_get_facebook_transcript_retries_with_proxy_and_handles_empty_transcript(self):
        created = []

        def fake_ydl(opts):
            inst = FakeYoutubeDL(opts, should_fail=(len(created) == 0), filename="/tmp/facebook_2.webm")
            created.append(inst)
            return inst

        empty_response = MagicMock()
        empty_response.raise_for_status.return_value = None
        empty_response.text = "   "
        fake_client = MagicMock()
        fake_client.post.return_value = empty_response

        with patch("src.utils.facebook_utils._facebook_cache", {}), patch(
            "src.utils.facebook_utils.GROQ_API_KEY", "groq-key"
        ), patch("src.utils.facebook_utils.TRANSCRIPT_PROXY", "http://proxy"), patch(
            "src.utils.facebook_utils.yt_dlp.YoutubeDL", side_effect=fake_ydl
        ), patch("src.utils.facebook_utils.os.path.exists", return_value=True), patch(
            "builtins.open", mock_open(read_data=b"audio-bytes")
        ), patch(
            "src.utils.facebook_utils.httpx.Client", side_effect=lambda **kwargs: FakeHttpxClientContext(fake_client)
        ):
            result = facebook_utils.get_facebook_transcript("https://facebook.com/a/videos/2")

        self.assertIsNone(result)
        self.assertEqual(created[1].opts["proxy"], "http://proxy")
