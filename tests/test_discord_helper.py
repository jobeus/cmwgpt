"""Tests for Discord attachment/url helper utilities."""

import base64
import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from PIL import Image

from src.utils import discord_helper
from src.utils.cache_utils import LRUCache, PersistentCache


def ok_response(content=b"image-bytes", status=200, url="https://example.com/image.png"):
    return httpx.Response(status, content=content, request=httpx.Request("GET", url))


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

    def test_caches_are_redis_caches(self):
        for cache in (
            discord_helper._url_base64_cache,
            discord_helper._attachment_base64_cache,
            discord_helper._audio_transcript_cache,
        ):
            self.assertIsInstance(cache, PersistentCache)
        self.assertEqual(discord_helper._url_base64_cache.ttl_seconds, discord_helper.IMAGE_CACHE_TTL_SECONDS)

    async def test_image_compression_runs_in_a_thread(self):
        attachment = MagicMock()
        attachment.id = 77
        attachment.filename = "img.png"
        attachment.content_type = "image/png"
        attachment.read = AsyncMock(return_value=b"image-bytes")

        async def fake_to_thread(fn, *args):
            self.assertIs(fn, discord_helper.compress_image)
            return b"jpeg"

        with patch("src.utils.discord_helper._attachment_base64_cache", LRUCache(5)), patch(
            "src.utils.discord_helper.asyncio.to_thread", side_effect=fake_to_thread
        ) as mock_to_thread:
            result = await discord_helper.attachment_to_base64_data_url(attachment)

        mock_to_thread.assert_called_once_with(discord_helper.compress_image, b"image-bytes")
        self.assertEqual(result, "data:image/jpeg;base64," + base64.b64encode(b"jpeg").decode())


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

    def test_is_voice_message(self):
        self.assertTrue(discord_helper.is_voice_message(SimpleNamespace(is_voice_message=lambda: True)))
        self.assertTrue(discord_helper.is_voice_message(SimpleNamespace(is_voice_message=True)))
        self.assertFalse(discord_helper.is_voice_message(SimpleNamespace(is_voice_message=lambda: False)))
        self.assertFalse(discord_helper.is_voice_message(SimpleNamespace()))
        # A bare MagicMock attribute returns a truthy MagicMock, which must not count.
        self.assertFalse(discord_helper.is_voice_message(MagicMock()))
        self.assertFalse(discord_helper.is_voice_message(SimpleNamespace(is_voice_message="yes")))

    async def test_attachment_without_content_type_or_voice_flag_is_treated_as_image(self):
        attachment = MagicMock()
        attachment.id = 1004
        attachment.filename = "mystery"
        attachment.content_type = None
        attachment.read = AsyncMock(return_value=b"bytes")

        with patch("src.utils.discord_helper._attachment_base64_cache", LRUCache(5)), patch(
            "src.utils.discord_helper.compress_image", return_value=b"jpeg"
        ):
            result = await discord_helper.attachment_to_base64_data_url(attachment)

        self.assertTrue(result.startswith("data:image/jpeg;base64,"))

    async def test_url_to_base64_data_url_returns_data_urls_unchanged(self):
        data_url = "data:image/png;base64,AAAA"
        with patch("src.utils.discord_helper.fetch_public", new=AsyncMock()) as mock_fetch:
            self.assertEqual(await discord_helper.url_to_base64_data_url(data_url), data_url)
        mock_fetch.assert_not_called()

    async def test_url_to_base64_data_url_uses_fetch_public_and_caches_success(self):
        mock_fetch = AsyncMock(return_value=ok_response())

        with patch("src.utils.discord_helper._url_base64_cache", LRUCache(5)), patch(
            "src.utils.discord_helper.fetch_public", new=mock_fetch
        ), patch("src.utils.discord_helper.compress_image", return_value=b"jpeg") as mock_compress:
            result = await discord_helper.url_to_base64_data_url("https://example.com/image.png")
            cached = await discord_helper.url_to_base64_data_url("https://example.com/image.png")

        self.assertEqual(result, cached)
        self.assertEqual(base64.b64decode(result.split(",", 1)[1]), b"jpeg")
        mock_compress.assert_called_once_with(b"image-bytes")
        mock_fetch.assert_awaited_once()
        args, kwargs = mock_fetch.await_args
        self.assertEqual(args, ("https://example.com/image.png",))
        self.assertEqual(kwargs["max_bytes"], discord_helper.MAX_IMAGE_DOWNLOAD_BYTES)
        self.assertIn("User-Agent", kwargs["headers"])

    async def test_url_to_base64_data_url_does_not_cache_failures(self):
        cases = [
            AsyncMock(side_effect=httpx.TimeoutException("slow")),
            AsyncMock(return_value=ok_response(b"", status=500)),
            AsyncMock(side_effect=ValueError("Refusing to fetch non-public URL")),
        ]
        for mock_fetch in cases:
            cache = LRUCache(5)
            with self.subTest(mock_fetch=mock_fetch), patch(
                "src.utils.discord_helper._url_base64_cache", cache
            ), patch("src.utils.discord_helper.fetch_public", new=mock_fetch):
                for _ in range(2):
                    with self.assertRaises(Exception):
                        await discord_helper.url_to_base64_data_url("https://example.com/fail.png")
                self.assertEqual(mock_fetch.await_count, 2)
                self.assertEqual(len(cache), 0)

    async def test_url_to_base64_data_url_does_not_cache_compression_failure(self):
        cache = LRUCache(5)
        with patch("src.utils.discord_helper._url_base64_cache", cache), patch(
            "src.utils.discord_helper.fetch_public", new=AsyncMock(return_value=ok_response())
        ), patch("src.utils.discord_helper.compress_image", side_effect=RuntimeError("compress failed")):
            with self.assertRaises(RuntimeError):
                await discord_helper.url_to_base64_data_url("https://example.com/generic.png")

        self.assertEqual(len(cache), 0)

    async def test_url_to_base64_data_url_evicts_least_recently_used(self):
        cache = LRUCache(2)
        cache["https://cached/0"] = "cached-0"
        cache["https://cached/1"] = "cached-1"

        with patch("src.utils.discord_helper._url_base64_cache", cache), patch(
            "src.utils.discord_helper.fetch_public", new=AsyncMock(return_value=ok_response())
        ), patch("src.utils.discord_helper.compress_image", return_value=b"jpeg"):
            await discord_helper.url_to_base64_data_url("https://example.com/image.png")

        self.assertNotIn("https://cached/0", cache)
        self.assertIn("https://cached/1", cache)
        self.assertIn("https://example.com/image.png", cache)

    async def test_url_to_base64_data_url_blocks_non_public_urls(self):
        # Real fetch_public + real SSRF guard: a literal private IP never hits the network.
        with patch("src.utils.discord_helper._url_base64_cache", LRUCache(5)) as guard_cache, patch(
            "src.utils.http_client.httpx.AsyncClient"
        ) as mock_client:
            with self.assertRaises(ValueError):
                await discord_helper.url_to_base64_data_url("http://192.168.1.5/internal.png")

        self.assertNotIn("http://192.168.1.5/internal.png", guard_cache)
        mock_client.return_value.__aenter__.return_value.stream.assert_not_called()

    async def test_get_mention_legend_formats_members(self):
        channel = SimpleNamespace(members=[SimpleNamespace(display_name="Alice", id=1), SimpleNamespace(display_name="Bob", id=2)])
        bot_user = SimpleNamespace(id=999)

        result = await discord_helper.get_mention_legend(channel, bot_user)

        self.assertIn("You are <@999>!", result)
        self.assertIn("@Alice = <@1>", result)
        self.assertIn("@Bob = <@2>", result)

    async def test_transcribe_audio_attachment_delegates_and_caches(self):
        attachment = MagicMock()
        attachment.id = 5001
        attachment.content_type = "audio/mp3"
        attachment.filename = "test.mp3"
        attachment.read = AsyncMock(return_value=b"some-mp3-bytes")
        cache = LRUCache(5)

        with patch(
            "src.utils.discord_helper.transcribe_audio", new=AsyncMock(return_value="This is a test transcript")
        ) as mock_transcribe, patch("src.utils.discord_helper._audio_transcript_cache", cache):
            result = await discord_helper.transcribe_audio_attachment(attachment)
            again = await discord_helper.transcribe_audio_attachment(attachment)

        self.assertEqual(result, "This is a test transcript")
        self.assertEqual(again, result)
        self.assertEqual(cache.get(5001), "This is a test transcript")
        mock_transcribe.assert_awaited_once_with(b"some-mp3-bytes", "test.mp3", "audio/mp3")

    async def test_large_audio_attachment_goes_through_the_chunker(self):
        attachment = MagicMock()
        attachment.id = 5002
        attachment.content_type = "audio/mpeg"
        attachment.filename = "podcast.mp3"
        attachment.read = AsyncMock(return_value=b"x" * 64)
        seen = {}

        async def fake_file_transcribe(path, mime):
            with open(path, "rb") as f:
                seen["bytes"] = f.read()
            seen["name"], seen["mime"] = os.path.basename(path), mime
            return "long transcript"

        with patch("src.utils.discord_helper.GROQ_MAX_UPLOAD_BYTES", 10), patch(
            "src.utils.discord_helper.transcribe_audio", new=AsyncMock()
        ) as mock_single, patch(
            "src.utils.discord_helper.transcribe_audio_file", side_effect=fake_file_transcribe
        ), patch("src.utils.discord_helper._audio_transcript_cache", LRUCache(5)):
            result = await discord_helper.transcribe_audio_attachment(attachment)

        self.assertEqual(result, "long transcript")
        self.assertEqual(seen, {"bytes": b"x" * 64, "name": "podcast.mp3", "mime": "audio/mpeg"})
        mock_single.assert_not_called()

    async def test_transcribe_audio_attachment_pcm_to_wav(self):
        import wave
        attachment = MagicMock()
        attachment.id = 5002
        attachment.content_type = "audio/s16le"
        attachment.filename = "voice.pcm"
        raw_pcm = b"\x00\x01" * 48000
        attachment.read = AsyncMock(return_value=raw_pcm)

        with patch(
            "src.utils.discord_helper.transcribe_audio", new=AsyncMock(return_value="Wav transcript")
        ) as mock_transcribe, patch("src.utils.discord_helper._audio_transcript_cache", LRUCache(5)):
            result = await discord_helper.transcribe_audio_attachment(attachment)

        self.assertEqual(result, "Wav transcript")
        sent_bytes, filename, mime = mock_transcribe.await_args.args
        self.assertEqual(filename, "voice.wav")
        self.assertEqual(mime, "audio/wav")
        with wave.open(io.BytesIO(sent_bytes), 'rb') as wav_file:
            self.assertEqual(wav_file.getframerate(), 48000)
            self.assertEqual(wav_file.getnchannels(), 2)
            self.assertEqual(wav_file.readframes(48000), raw_pcm)

    async def test_transcribe_audio_attachment_fixes_unknown_extension_from_mime(self):
        attachment = MagicMock()
        attachment.id = 5004
        attachment.content_type = "audio/x-m4a"
        attachment.filename = "memo.caf"
        attachment.read = AsyncMock(return_value=b"bytes")

        with patch(
            "src.utils.discord_helper.transcribe_audio", new=AsyncMock(return_value="ok")
        ) as mock_transcribe, patch("src.utils.discord_helper._audio_transcript_cache", LRUCache(5)):
            await discord_helper.transcribe_audio_attachment(attachment)

        self.assertEqual(mock_transcribe.await_args.args[1], "memo.m4a")

    async def test_transcribe_voice_message_defaults_to_ogg(self):
        attachment = MagicMock()
        attachment.id = 5005
        attachment.content_type = None
        attachment.is_voice_message = MagicMock(return_value=True)
        attachment.filename = "voice-message"
        attachment.read = AsyncMock(return_value=b"opus")

        with patch(
            "src.utils.discord_helper.transcribe_audio", new=AsyncMock(return_value="hi")
        ) as mock_transcribe, patch("src.utils.discord_helper._audio_transcript_cache", LRUCache(5)):
            await discord_helper.transcribe_audio_attachment(attachment)

        self.assertEqual(mock_transcribe.await_args.args[1:], ("voice-message.ogg", "audio/ogg"))

    async def test_transcribe_audio_attachment_empty_result_not_cached(self):
        attachment = MagicMock()
        attachment.id = 5003
        attachment.content_type = "audio/ogg"
        attachment.filename = "a.ogg"
        attachment.read = AsyncMock(return_value=b"bytes")
        cache = LRUCache(5)

        with patch("src.utils.discord_helper.transcribe_audio", new=AsyncMock(return_value=None)), patch(
            "src.utils.discord_helper._audio_transcript_cache", cache
        ):
            self.assertIsNone(await discord_helper.transcribe_audio_attachment(attachment))

        self.assertEqual(len(cache), 0)

    async def test_transcribe_audio_attachment_error_returns_none(self):
        attachment = MagicMock()
        attachment.id = 5006
        attachment.content_type = "audio/ogg"
        attachment.filename = "a.ogg"
        attachment.read = AsyncMock(return_value=b"bytes")

        with patch(
            "src.utils.discord_helper.transcribe_audio", new=AsyncMock(side_effect=httpx.HTTPError("groq down"))
        ), patch("src.utils.discord_helper._audio_transcript_cache", LRUCache(5)):
            self.assertIsNone(await discord_helper.transcribe_audio_attachment(attachment))

    def test_prod_module_does_not_import_unittest_mock(self):
        import inspect
        self.assertNotIn("unittest", inspect.getsource(discord_helper))


if __name__ == "__main__":
    unittest.main()
