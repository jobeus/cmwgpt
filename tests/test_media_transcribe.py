"""Tests for shared media download + Groq transcription helpers."""

import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.utils import media_transcribe
from tests.config_helpers import cfg

_RealAsyncClient = httpx.AsyncClient


class FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL; "downloads" by writing an mp3 into outtmpl's dir."""

    instances: list = []

    def __init__(self, opts, *, error=None, write_file=True, info=None):
        self.opts = opts
        self.error = error
        self.write_file = write_file
        self.info = {"id": "1", "format_id": "a"} if info is None else info
        self.urls = []
        FakeYoutubeDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def _path(self):
        return self.opts["outtmpl"].replace("%(id)s", "1").replace("%(format_id)s", "a").replace("%(ext)s", "webm")

    def extract_info(self, url, download=True):
        self.urls.append(url)
        if self.error:
            raise self.error
        if self.info and self.write_file:
            mp3 = os.path.splitext(self._path())[0] + ".mp3"
            with open(mp3, "wb") as f:
                f.write(b"audio-bytes")
        return self.info

    def prepare_filename(self, info):
        return self._path()


def ydl_sequence(*factories):
    """Return a YoutubeDL replacement that builds each call from the next factory."""
    remaining = list(factories)

    def make(opts):
        return remaining.pop(0)(opts)
    return make


class TestTranscribeAudio(unittest.IsolatedAsyncioTestCase):
    def use_handler(self, handler):
        def factory(*args, **kwargs):
            return _RealAsyncClient(*args, transport=httpx.MockTransport(handler), **kwargs)
        patcher = patch("src.utils.media_transcribe.httpx.AsyncClient", factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_returns_none_without_api_key(self):
        handler = AsyncMock()
        self.use_handler(handler)
        with patch("src.config._cached_config", cfg(groq_api_key="")):
            self.assertIsNone(await media_transcribe.transcribe_audio(b"a", "a.mp3", "audio/mpeg"))
        handler.assert_not_called()

    async def test_posts_audio_to_groq_and_returns_stripped_text(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, text="  hello world \n")

        self.use_handler(handler)
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key")):
            result = await media_transcribe.transcribe_audio(b"audio-bytes", "voice.ogg", "audio/ogg")

        self.assertEqual(result, "hello world")
        request = seen[0]
        self.assertEqual(str(request.url), media_transcribe.GROQ_TRANSCRIBE_URL)
        self.assertEqual(request.headers["authorization"], "Bearer groq-key")
        body = request.content
        self.assertIn(b"audio-bytes", body)
        self.assertIn(b"voice.ogg", body)
        self.assertIn(media_transcribe.GROQ_MODEL.encode(), body)

    async def test_empty_transcript_returns_none(self):
        self.use_handler(lambda request: httpx.Response(200, text="   "))
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key")):
            self.assertIsNone(await media_transcribe.transcribe_audio(b"a", "a.mp3", "audio/mpeg"))

    async def test_http_error_raises(self):
        self.use_handler(lambda request: httpx.Response(500, text="boom"))
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key")):
            with self.assertRaises(httpx.HTTPStatusError):
                await media_transcribe.transcribe_audio(b"a", "a.mp3", "audio/mpeg")


class TestTranscribeVideoUrl(unittest.IsolatedAsyncioTestCase):
    async def test_returns_none_without_api_key(self):
        with patch("src.config._cached_config", cfg(groq_api_key="")), patch(
            "src.utils.media_transcribe.download_public", new_callable=AsyncMock
        ) as mock_download:
            self.assertIsNone(await media_transcribe.transcribe_video_url("https://video.twimg.com/v.mp4"))
        mock_download.assert_not_called()

    async def test_downloads_converts_and_transcribes(self):
        dirs = []

        async def fake_download(url, path, **kwargs):
            dirs.append(os.path.dirname(path))
            self.assertEqual(kwargs["max_bytes"], media_transcribe.MAX_VIDEO_DOWNLOAD_BYTES)
            with open(path, "wb") as f:
                f.write(b"mp4")

        def fake_ffmpeg(cmd, **kwargs):
            self.assertEqual(cmd[0], "ffmpeg")
            self.assertIn("-vn", cmd)
            self.assertEqual(kwargs["timeout"], media_transcribe.FFMPEG_TIMEOUT_SECONDS)
            with open(cmd[-1], "wb") as f:
                f.write(b"mp3-bytes")

        with patch("src.config._cached_config", cfg(groq_api_key="groq-key")), patch(
            "src.utils.media_transcribe.download_public", side_effect=fake_download
        ), patch("src.utils.media_transcribe.subprocess.run", side_effect=fake_ffmpeg), patch(
            "src.utils.media_transcribe.transcribe_audio", new_callable=AsyncMock, return_value="spoken words"
        ) as mock_transcribe:
            result = await media_transcribe.transcribe_video_url("https://video.twimg.com/v.mp4")

        self.assertEqual(result, "spoken words")
        mock_transcribe.assert_awaited_once_with(b"mp3-bytes", "audio.mp3", "audio/mpeg")
        self.assertFalse(os.path.exists(dirs[0]))  # temp dir cleaned up

    async def test_download_failure_propagates_and_cleans_up(self):
        dirs = []

        async def failing_download(url, path, **kwargs):
            dirs.append(os.path.dirname(path))
            raise ValueError("Refusing to fetch non-public URL")

        with patch("src.config._cached_config", cfg(groq_api_key="groq-key")), patch(
            "src.utils.media_transcribe.download_public", side_effect=failing_download
        ):
            with self.assertRaises(ValueError):
                await media_transcribe.transcribe_video_url("http://10.0.0.1/v.mp4")

        self.assertFalse(os.path.exists(dirs[0]))


class TestRejectLongMedia(unittest.TestCase):
    def test_rejects_media_over_limit(self):
        reason = media_transcribe._reject_long_media(
            {"duration": media_transcribe.MAX_MEDIA_DURATION_SECONDS + 1})
        self.assertIsNotNone(reason)

    def test_allows_short_or_unknown_duration(self):
        self.assertIsNone(media_transcribe._reject_long_media({"duration": 30}))
        self.assertIsNone(media_transcribe._reject_long_media({}))
        self.assertIsNone(media_transcribe._reject_long_media({"duration": None}, incomplete=True))

    def test_ydl_opts_cap_size_and_duration(self):
        opts = media_transcribe._build_ydl_opts("tiktok", "/tmp/x")
        self.assertEqual(opts["max_filesize"], media_transcribe.MAX_VIDEO_DOWNLOAD_BYTES)
        self.assertIs(opts["match_filter"], media_transcribe._reject_long_media)
        self.assertTrue(opts["outtmpl"].startswith(os.path.join("/tmp/x", "tiktok_")))


class TestDownloadAndTranscribeVideo(unittest.IsolatedAsyncioTestCase):
    URL = "https://vt.tiktok.com/abc123/"

    def setUp(self):
        FakeYoutubeDL.instances = []

    async def run_dl(self, cache, *, cache_only=False):
        return await media_transcribe.download_and_transcribe_video(
            self.URL, cache, label="TikTok", file_prefix="tiktok", cache_only=cache_only)

    async def test_returns_cached_transcript_without_downloading(self):
        with patch("src.utils.media_transcribe.yt_dlp.YoutubeDL") as mock_ydl:
            self.assertEqual(await self.run_dl({self.URL: "cached"}), "cached")
        mock_ydl.assert_not_called()

    async def test_cache_only_miss_returns_none_without_downloading(self):
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key")), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL"
        ) as mock_ydl:
            self.assertIsNone(await self.run_dl({}, cache_only=True))
        mock_ydl.assert_not_called()

    async def test_requires_api_key(self):
        with patch("src.config._cached_config", cfg(groq_api_key="")), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL"
        ) as mock_ydl:
            self.assertIsNone(await self.run_dl({}))
        mock_ydl.assert_not_called()

    async def test_downloads_transcribes_and_caches(self):
        cache = {}
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key")), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL", side_effect=lambda opts: FakeYoutubeDL(opts)
        ), patch(
            "src.utils.media_transcribe.transcribe_audio", new_callable=AsyncMock, return_value="transcribed"
        ) as mock_transcribe:
            result = await self.run_dl(cache)

        self.assertEqual(result, "transcribed")
        self.assertEqual(cache, {self.URL: "transcribed"})
        args = mock_transcribe.await_args.args
        self.assertEqual(args[0], b"audio-bytes")
        self.assertEqual(args[1], "tiktok_1_a.mp3")
        self.assertEqual(args[2], "audio/mpeg")
        ydl = FakeYoutubeDL.instances[0]
        self.assertEqual(ydl.opts["max_filesize"], media_transcribe.MAX_VIDEO_DOWNLOAD_BYTES)
        self.assertIs(ydl.opts["match_filter"], media_transcribe._reject_long_media)
        self.assertNotIn("proxy", ydl.opts)
        # Private temp dir removed afterwards.
        self.assertFalse(os.path.exists(os.path.dirname(ydl.opts["outtmpl"])))

    async def test_empty_transcript_is_not_cached(self):
        cache = {}
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key")), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL", side_effect=lambda opts: FakeYoutubeDL(opts)
        ), patch("src.utils.media_transcribe.transcribe_audio", new_callable=AsyncMock, return_value=None):
            self.assertIsNone(await self.run_dl(cache))
        self.assertEqual(cache, {})

    async def test_download_failure_without_proxy_returns_none(self):
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key", transcript_proxy="")), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL",
            side_effect=lambda opts: FakeYoutubeDL(opts, error=RuntimeError("blocked")),
        ):
            self.assertIsNone(await self.run_dl({}))
        self.assertEqual(len(FakeYoutubeDL.instances), 1)

    async def test_retries_with_proxy_after_direct_failure(self):
        cache = {}
        factory = ydl_sequence(
            lambda opts: FakeYoutubeDL(opts, error=RuntimeError("blocked")),
            lambda opts: FakeYoutubeDL(opts),
        )
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key", transcript_proxy="http://proxy")), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL", side_effect=factory
        ), patch("src.utils.media_transcribe.transcribe_audio", new_callable=AsyncMock, return_value="via proxy"):
            result = await self.run_dl(cache)

        self.assertEqual(result, "via proxy")
        self.assertEqual(FakeYoutubeDL.instances[1].opts["proxy"], "http://proxy")
        self.assertEqual(cache[self.URL], "via proxy")

    async def test_proxy_failure_returns_none(self):
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key", transcript_proxy="http://proxy")), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL",
            side_effect=lambda opts: FakeYoutubeDL(opts, error=RuntimeError("blocked")),
        ):
            self.assertIsNone(await self.run_dl({}))
        self.assertEqual(len(FakeYoutubeDL.instances), 2)

    async def test_audio_codec_error_retries_with_h264_format(self):
        factory = ydl_sequence(
            lambda opts: FakeYoutubeDL(opts, error=RuntimeError("ERROR: unable to obtain file audio codec")),
            lambda opts: FakeYoutubeDL(opts),
        )
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key", transcript_proxy="")), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL", side_effect=factory
        ), patch("src.utils.media_transcribe.transcribe_audio", new_callable=AsyncMock, return_value="ok"):
            self.assertEqual(await self.run_dl({}), "ok")
        self.assertEqual(FakeYoutubeDL.instances[1].opts["format"], "best[vcodec=h264]")

    async def test_filtered_media_with_no_file_returns_none(self):
        # match_filter rejecting a long video means yt-dlp writes nothing.
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key")), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL",
            side_effect=lambda opts: FakeYoutubeDL(opts, write_file=False),
        ), patch("src.utils.media_transcribe.transcribe_audio", new_callable=AsyncMock) as mock_transcribe:
            self.assertIsNone(await self.run_dl({}))
        mock_transcribe.assert_not_called()

    async def test_transcription_error_returns_none(self):
        with patch("src.config._cached_config", cfg(groq_api_key="groq-key")), patch(
            "src.utils.media_transcribe.yt_dlp.YoutubeDL", side_effect=lambda opts: FakeYoutubeDL(opts)
        ), patch(
            "src.utils.media_transcribe.transcribe_audio", new_callable=AsyncMock,
            side_effect=httpx.HTTPError("groq down"),
        ):
            self.assertIsNone(await self.run_dl({}))


if __name__ == "__main__":
    unittest.main()
