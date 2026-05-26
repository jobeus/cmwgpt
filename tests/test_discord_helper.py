"""Tests for Discord attachment/url helper utilities."""

import base64
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from PIL import Image

from src.utils import discord_helper


class FakeAsyncClientContext:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestDiscordHelper(unittest.IsolatedAsyncioTestCase):
    def test_compress_image_returns_original_on_failure(self):
        with patch("src.utils.discord_helper.Image.open", side_effect=RuntimeError("bad image")):
            result = discord_helper.compress_image(b"raw-bytes")

        self.assertEqual(result, b"raw-bytes")

    def test_compress_image_resizes_and_outputs_bytes(self):
        image = Image.new("RGBA", (2000, 1200), (255, 0, 0, 255))
        buf = io.BytesIO()
        image.save(buf, format="PNG")

        result = discord_helper.compress_image(buf.getvalue(), max_size=100)

        self.assertIsInstance(result, bytes)
        self.assertNotEqual(result, buf.getvalue())

    async def test_attachment_to_base64_data_url_caches_success_but_not_failures(self):
        attachment = MagicMock()
        attachment.id = 1
        attachment.filename = "image.png"
        attachment.read = AsyncMock(return_value=b"image-bytes")

        with patch("src.utils.discord_helper._attachment_base64_cache", {}), patch(
            "src.utils.discord_helper.compress_image", return_value=b"jpeg"
        ):
            result = await discord_helper.attachment_to_base64_data_url(attachment)
            cached = await discord_helper.attachment_to_base64_data_url(attachment)

        self.assertEqual(result, cached)
        self.assertTrue(result.startswith("data:image/jpeg;base64,"))
        attachment.read.assert_awaited_once()

        broken = MagicMock()
        broken.id = 2
        broken.filename = "broken.png"
        broken.read = AsyncMock(side_effect=RuntimeError("nope"))
        with patch("src.utils.discord_helper._attachment_base64_cache", {}) as failure_cache:
            with self.assertRaises(RuntimeError):
                await discord_helper.attachment_to_base64_data_url(broken)
            with self.assertRaises(RuntimeError):
                await discord_helper.attachment_to_base64_data_url(broken)

        self.assertNotIn(2, failure_cache)
        self.assertEqual(broken.read.await_count, 2)

    async def test_attachment_to_base64_data_url_discards_legacy_failure_sentinel(self):
        attachment = MagicMock()
        attachment.id = 2
        attachment.filename = "recovered.png"
        attachment.read = AsyncMock(return_value=b"image-bytes")
        legacy_cache = {2: None}

        with patch("src.utils.discord_helper._attachment_base64_cache", legacy_cache), patch(
            "src.utils.discord_helper.compress_image", return_value=b"jpeg"
        ):
            result = await discord_helper.attachment_to_base64_data_url(attachment)

        self.assertTrue(result.startswith("data:image/jpeg;base64,"))
        self.assertEqual(legacy_cache[2], result)
        attachment.read.assert_awaited_once()

    async def test_attachment_to_base64_data_url_evicts_oldest_cache_entries(self):
        attachment = MagicMock()
        attachment.id = 999
        attachment.filename = "fresh.png"
        attachment.read = AsyncMock(return_value=b"image-bytes")
        full_cache = {i: f"cached-{i}" for i in range(discord_helper.MAX_CACHE_SIZE)}

        with patch("src.utils.discord_helper._attachment_base64_cache", full_cache), patch(
            "src.utils.discord_helper.compress_image", return_value=b"jpeg"
        ):
            await discord_helper.attachment_to_base64_data_url(attachment)

        self.assertNotIn(0, full_cache)
        self.assertIn(999, full_cache)

    async def test_attachment_to_base64_data_url_with_audio_attachment(self):
        attachment = MagicMock()
        attachment.id = 1001
        attachment.filename = "voice.ogg"
        attachment.content_type = "audio/ogg; codecs=opus"
        attachment.read = AsyncMock(return_value=b"raw-audio-bytes")

        with patch("src.utils.discord_helper._attachment_base64_cache", {}), patch(
            "src.utils.discord_helper.compress_image"
        ) as mock_compress:
            result = await discord_helper.attachment_to_base64_data_url(attachment)

        self.assertTrue(result.startswith("data:audio/ogg;base64,"))
        self.assertEqual(base64.b64decode(result.split(",", 1)[1]), b"raw-audio-bytes")
        mock_compress.assert_not_called()

    async def test_attachment_to_base64_data_url_with_voice_message(self):
        attachment = MagicMock()
        attachment.id = 1002
        attachment.filename = "voice"
        attachment.content_type = None
        attachment.is_voice_message = MagicMock(return_value=True)
        attachment.read = AsyncMock(return_value=b"voice-bytes")

        with patch("src.utils.discord_helper._attachment_base64_cache", {}), patch(
            "src.utils.discord_helper.compress_image"
        ) as mock_compress:
            result = await discord_helper.attachment_to_base64_data_url(attachment)

        self.assertTrue(result.startswith("data:audio/ogg;base64,"))
        self.assertEqual(base64.b64decode(result.split(",", 1)[1]), b"voice-bytes")
        mock_compress.assert_not_called()

    async def test_attachment_to_base64_data_url_with_s16le_attachment(self):
        import wave
        import io
        attachment = MagicMock()
        attachment.id = 1003
        attachment.filename = "voice.pcm"
        attachment.content_type = "audio/s16le; rate=48000"
        raw_pcm = b"\x00\x01" * 96000  # 1 second of stereo 16-bit PCM at 48000Hz
        attachment.read = AsyncMock(return_value=raw_pcm)

        with patch("src.utils.discord_helper._attachment_base64_cache", {}), patch(
            "src.utils.discord_helper.compress_image"
        ) as mock_compress:
            result = await discord_helper.attachment_to_base64_data_url(attachment)

        self.assertTrue(result.startswith("data:audio/wav;base64,"))
        wav_bytes = base64.b64decode(result.split(",", 1)[1])
        mock_compress.assert_not_called()

        # Verify wav header integrity
        wav_io = io.BytesIO(wav_bytes)
        with wave.open(wav_io, 'rb') as wav_file:
            self.assertEqual(wav_file.getnchannels(), 2)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getframerate(), 48000)
            self.assertEqual(wav_file.readframes(96000), raw_pcm)

    async def test_url_to_base64_data_url_caches_success_but_not_timeout_failures(self):
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=SimpleNamespace(
            content=b"image-bytes",
            raise_for_status=lambda: None,
        ))

        with patch("src.utils.discord_helper._url_base64_cache", {}), patch(
            "httpx.AsyncClient", side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client)
        ), patch("src.utils.discord_helper.compress_image", return_value=b"jpeg"):
            result = await discord_helper.url_to_base64_data_url("https://example.com/image.png")
            cached = await discord_helper.url_to_base64_data_url("https://example.com/image.png")

        self.assertEqual(result, cached)
        self.assertEqual(fake_client.get.await_count, 1)
        self.assertEqual(base64.b64decode(result.split(",", 1)[1]), b"jpeg")

        timeout_client = MagicMock()
        timeout_client.get = AsyncMock(side_effect=httpx.TimeoutException("slow"))
        with patch("src.utils.discord_helper._url_base64_cache", {}) as failure_cache, patch(
            "httpx.AsyncClient", side_effect=lambda **kwargs: FakeAsyncClientContext(timeout_client)
        ):
            with self.assertRaises(httpx.TimeoutException):
                await discord_helper.url_to_base64_data_url("https://example.com/slow.png")
            with self.assertRaises(httpx.TimeoutException):
                await discord_helper.url_to_base64_data_url("https://example.com/slow.png")

        self.assertNotIn("https://example.com/slow.png", failure_cache)
        self.assertEqual(timeout_client.get.await_count, 2)

    async def test_url_to_base64_data_url_evicts_and_does_not_cache_http_or_generic_failures(self):
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=SimpleNamespace(
            content=b"image-bytes",
            raise_for_status=lambda: None,
        ))
        full_cache = {f"https://cached/{i}": f"cached-{i}" for i in range(discord_helper.MAX_CACHE_SIZE)}

        with patch("src.utils.discord_helper._url_base64_cache", full_cache), patch(
            "httpx.AsyncClient", side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client)
        ), patch("src.utils.discord_helper.compress_image", return_value=b"jpeg"):
            await discord_helper.url_to_base64_data_url("https://example.com/image.png")

        self.assertNotIn("https://cached/0", full_cache)
        self.assertIn("https://example.com/image.png", full_cache)

        request = httpx.Request("GET", "https://example.com/fail.png")
        response = httpx.Response(500, request=request)
        http_error_client = MagicMock()
        http_error_client.get = AsyncMock(return_value=SimpleNamespace(
            content=b"",
            raise_for_status=lambda: (_ for _ in ()).throw(httpx.HTTPStatusError("boom", request=request, response=response)),
        ))
        http_failure_cache = {f"https://cached/{i}": f"cached-{i}" for i in range(discord_helper.MAX_CACHE_SIZE)}

        with patch("src.utils.discord_helper._url_base64_cache", http_failure_cache), patch(
            "httpx.AsyncClient", side_effect=lambda **kwargs: FakeAsyncClientContext(http_error_client)
        ):
            with self.assertRaises(httpx.HTTPError):
                await discord_helper.url_to_base64_data_url("https://example.com/fail.png")
            with self.assertRaises(httpx.HTTPError):
                await discord_helper.url_to_base64_data_url("https://example.com/fail.png")

        self.assertIn("https://cached/0", http_failure_cache)
        self.assertNotIn("https://example.com/fail.png", http_failure_cache)
        self.assertEqual(http_error_client.get.await_count, 2)

        generic_client = MagicMock()
        generic_client.get = AsyncMock(return_value=SimpleNamespace(
            content=b"image-bytes",
            raise_for_status=lambda: None,
        ))
        generic_failure_cache = {}

        with patch("src.utils.discord_helper._url_base64_cache", generic_failure_cache), patch(
            "httpx.AsyncClient", side_effect=lambda **kwargs: FakeAsyncClientContext(generic_client)
        ), patch("src.utils.discord_helper.compress_image", side_effect=RuntimeError("compress failed")):
            with self.assertRaises(RuntimeError):
                await discord_helper.url_to_base64_data_url("https://example.com/generic.png")

        self.assertNotIn("https://example.com/generic.png", generic_failure_cache)

    async def test_url_to_base64_data_url_discards_legacy_failure_sentinel(self):
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=SimpleNamespace(
            content=b"image-bytes",
            raise_for_status=lambda: None,
        ))
        legacy_cache = {"https://example.com/image.png": None}

        with patch("src.utils.discord_helper._url_base64_cache", legacy_cache), patch(
            "httpx.AsyncClient", side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client)
        ), patch("src.utils.discord_helper.compress_image", return_value=b"jpeg"):
            result = await discord_helper.url_to_base64_data_url("https://example.com/image.png")

        self.assertTrue(result.startswith("data:image/jpeg;base64,"))
        self.assertEqual(legacy_cache["https://example.com/image.png"], result)
        fake_client.get.assert_awaited_once()

    async def test_get_mention_legend_formats_members(self):
        channel = SimpleNamespace(members=[SimpleNamespace(display_name="Alice", id=1), SimpleNamespace(display_name="Bob", id=2)])
        bot_user = SimpleNamespace(id=999)

        result = await discord_helper.get_mention_legend(channel, bot_user)

        self.assertIn("You are <@999>!", result)
        self.assertIn("@Alice = <@1>", result)
        self.assertIn("@Bob = <@2>", result)

    async def test_transcribe_audio_attachment_success(self):
        attachment = MagicMock()
        attachment.id = 5001
        attachment.content_type = "audio/mp3"
        attachment.filename = "test.mp3"
        attachment.read = AsyncMock(return_value=b"some-mp3-bytes")

        fake_resp = MagicMock()
        fake_resp.text = "This is a test transcript"
        fake_resp.raise_for_status = lambda: None
        fake_client = MagicMock()
        fake_client.post = AsyncMock(return_value=fake_resp)

        with patch("src.utils.discord_helper.GROQ_API_KEY", "groq-key"), patch(
            "httpx.AsyncClient", side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client)
        ), patch("src.utils.discord_helper._audio_transcript_cache", {}) as cache:
            result = await discord_helper.transcribe_audio_attachment(attachment)

        self.assertEqual(result, "This is a test transcript")
        self.assertEqual(cache[5001], "This is a test transcript")
        fake_client.post.assert_called_once()
        kwargs = fake_client.post.call_args.kwargs
        self.assertEqual(kwargs["data"]["model"], "whisper-large-v3-turbo")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer groq-key")
        self.assertEqual(kwargs["files"]["file"][0], "test.mp3")
        self.assertEqual(kwargs["files"]["file"][1], b"some-mp3-bytes")
        self.assertEqual(kwargs["files"]["file"][2], "audio/mp3")

    async def test_transcribe_audio_attachment_pcm_to_wav(self):
        import wave
        import io
        attachment = MagicMock()
        attachment.id = 5002
        attachment.content_type = "audio/s16le"
        attachment.filename = "voice.pcm"
        raw_pcm = b"\x00\x01" * 48000
        attachment.read = AsyncMock(return_value=raw_pcm)

        fake_resp = MagicMock()
        fake_resp.text = "Wav transcript"
        fake_resp.raise_for_status = lambda: None
        fake_client = MagicMock()
        fake_client.post = AsyncMock(return_value=fake_resp)

        with patch("src.utils.discord_helper.GROQ_API_KEY", "groq-key"), patch(
            "httpx.AsyncClient", side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client)
        ), patch("src.utils.discord_helper._audio_transcript_cache", {}):
            result = await discord_helper.transcribe_audio_attachment(attachment)

        self.assertEqual(result, "Wav transcript")
        fake_client.post.assert_called_once()
        kwargs = fake_client.post.call_args.kwargs
        self.assertEqual(kwargs["files"]["file"][0], "voice.wav")
        self.assertEqual(kwargs["files"]["file"][2], "audio/wav")
        
        # Verify the bytes sent are indeed a valid WAV file wrapping the PCM bytes
        sent_bytes = kwargs["files"]["file"][1]
        wav_io = io.BytesIO(sent_bytes)
        with wave.open(wav_io, 'rb') as wav_file:
            self.assertEqual(wav_file.getframerate(), 48000)
            self.assertEqual(wav_file.getnchannels(), 2)
            self.assertEqual(wav_file.readframes(48000), raw_pcm)

    async def test_transcribe_audio_attachment_missing_api_key(self):
        attachment = MagicMock()
        attachment.id = 5003

        with patch("src.utils.discord_helper.GROQ_API_KEY", ""):
            result = await discord_helper.transcribe_audio_attachment(attachment)

        self.assertIsNone(result)
