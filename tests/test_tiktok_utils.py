"""Tests for TikTok transcript helpers."""

import unittest
from unittest.mock import MagicMock, mock_open, patch

from src.utils import tiktok_utils
from tests.config_helpers import cfg


class FakeYoutubeDL:
    def __init__(self, opts, *, should_fail=False, filename="/tmp/tiktok_1.webm"):
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
        self._transport = MagicMock()
        self._transport.pending_logs = []

    def __enter__(self):
        return self._client

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        return self._client.post(*args, **kwargs)


class TestTikTokUtils(unittest.TestCase):
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

    def test_get_tiktok_transcript_returns_cached_values_and_retries_legacy_failure_sentinels(self):
        legacy_cache = {"u1": "cached", "u2": None}
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.text = "transcribed text"
        fake_client = MagicMock()
        fake_client.post.return_value = fake_response

        with patch("src.utils.tiktok_utils._tiktok_cache", legacy_cache), patch(
            "src.config._cached_config", cfg(groq_api_key="groq-key")
        ), patch("src.utils.media_transcribe.yt_dlp.YoutubeDL", side_effect=lambda opts: FakeYoutubeDL(opts)), patch(
            "src.utils.media_transcribe.os.path.exists", return_value=True
        ), patch("builtins.open", mock_open(read_data=b"audio-bytes")), patch(
            "src.utils.media_transcribe.create_sync_client", return_value=FakeHttpxClientContext(fake_client)
        ), patch(
            "src.utils.media_transcribe.tempfile.mkdtemp", return_value="/tmp/fake_tiktok_dir"
        ), patch("src.utils.media_transcribe.shutil.rmtree"):
            self.assertEqual(tiktok_utils.get_tiktok_transcript("u1")["transcript_text"], "cached")
            self.assertEqual(tiktok_utils.get_tiktok_transcript("u2")["transcript_text"], "transcribed text")

        self.assertEqual(legacy_cache["u2"], "transcribed text")

    def test_get_tiktok_transcript_requires_api_key(self):
        with patch("src.config._cached_config", cfg(groq_api_key="")):
            self.assertIsNone(tiktok_utils.get_tiktok_transcript("https://vt.tiktok.com/abc123/"))

    def test_get_tiktok_transcript_downloads_and_transcribes(self):
        fake_cache = {}
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.text = "transcribed text"
        fake_client = MagicMock()
        fake_client.post.return_value = fake_response

        with patch("src.utils.tiktok_utils._tiktok_cache", fake_cache), patch(
            "src.config._cached_config", cfg(groq_api_key="groq-key")
        ), patch("src.utils.media_transcribe.yt_dlp.YoutubeDL", side_effect=lambda opts: FakeYoutubeDL(opts)), patch(
            "src.utils.media_transcribe.os.path.exists", return_value=True
        ), patch("builtins.open", mock_open(read_data=b"audio-bytes")), patch(
            "src.utils.media_transcribe.create_sync_client", return_value=FakeHttpxClientContext(fake_client)
        ), patch(
            "src.utils.media_transcribe.tempfile.mkdtemp", return_value="/tmp/fake_tiktok_dir"
        ), patch("src.utils.media_transcribe.shutil.rmtree") as mock_rmtree:
            result = tiktok_utils.get_tiktok_transcript("https://vt.tiktok.com/abc123/")

        self.assertEqual(result["transcript_text"], "transcribed text")
        self.assertIs(result["groq_response"], fake_response)
        self.assertEqual(result["audio_artifact"]["media_type"], "audio/mpeg")
        self.assertEqual(fake_cache["https://vt.tiktok.com/abc123/"], "transcribed text")
        mock_rmtree.assert_called_once_with("/tmp/fake_tiktok_dir")

    def test_get_tiktok_transcript_returns_none_when_download_fails_without_proxy(self):
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key", transcript_proxy="")), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL", side_effect=lambda opts: FakeYoutubeDL(opts, should_fail=True)):
            self.assertIsNone(tiktok_utils.get_tiktok_transcript("https://vt.tiktok.com/abc123/"))

    def test_get_tiktok_transcript_retries_with_proxy_and_handles_empty_transcript(self):
        created = []

        def fake_ydl(opts):
            inst = FakeYoutubeDL(opts, should_fail=(len(created) == 0), filename="/tmp/tiktok_2.webm")
            created.append(inst)
            return inst

        empty_response = MagicMock()
        empty_response.raise_for_status.return_value = None
        empty_response.text = "   "
        fake_client = MagicMock()
        fake_client.post.return_value = empty_response

        with patch("src.utils.tiktok_utils._tiktok_cache", {}), patch(
            "src.config._cached_config", cfg(groq_api_key="groq-key", transcript_proxy="http://proxy")
        ), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL", side_effect=fake_ydl
        ), patch("src.utils.media_transcribe.os.path.exists", return_value=True), patch(
            "builtins.open", mock_open(read_data=b"audio-bytes")
        ), patch(
            "src.utils.media_transcribe.create_sync_client", return_value=FakeHttpxClientContext(fake_client)
        ):
            result = tiktok_utils.get_tiktok_transcript("https://vt.tiktok.com/xyz987/")

        self.assertIsNone(result)
        self.assertEqual(created[1].opts["proxy"], "http://proxy")
