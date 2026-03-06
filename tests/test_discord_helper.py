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

    async def test_attachment_to_base64_data_url_caches_success_and_failure(self):
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
        with patch("src.utils.discord_helper._attachment_base64_cache", {}):
            with self.assertRaises(RuntimeError):
                await discord_helper.attachment_to_base64_data_url(broken)
            with self.assertRaises(Exception):
                await discord_helper.attachment_to_base64_data_url(broken)

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

        broken = MagicMock()
        broken.id = 1000
        broken.filename = "broken.png"
        broken.read = AsyncMock(side_effect=RuntimeError("nope"))
        full_failure_cache = {i: f"cached-{i}" for i in range(discord_helper.MAX_CACHE_SIZE)}

        with patch("src.utils.discord_helper._attachment_base64_cache", full_failure_cache):
            with self.assertRaises(RuntimeError):
                await discord_helper.attachment_to_base64_data_url(broken)

        self.assertNotIn(0, full_failure_cache)
        self.assertIsNone(full_failure_cache[1000])

    async def test_url_to_base64_data_url_caches_success_and_timeout_failure(self):
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
        with patch("src.utils.discord_helper._url_base64_cache", {}), patch(
            "httpx.AsyncClient", side_effect=lambda **kwargs: FakeAsyncClientContext(timeout_client)
        ):
            with self.assertRaises(httpx.TimeoutException):
                await discord_helper.url_to_base64_data_url("https://example.com/slow.png")
            with self.assertRaises(Exception):
                await discord_helper.url_to_base64_data_url("https://example.com/slow.png")

    async def test_url_to_base64_data_url_evicts_and_caches_http_and_generic_failures(self):
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
            with self.assertRaises(Exception):
                await discord_helper.url_to_base64_data_url("https://example.com/fail.png")

        self.assertNotIn("https://cached/0", http_failure_cache)
        self.assertIsNone(http_failure_cache["https://example.com/fail.png"])

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

        self.assertIsNone(generic_failure_cache["https://example.com/generic.png"])

    async def test_get_mention_legend_formats_members(self):
        channel = SimpleNamespace(members=[SimpleNamespace(display_name="Alice", id=1), SimpleNamespace(display_name="Bob", id=2)])
        bot_user = SimpleNamespace(id=999)

        result = await discord_helper.get_mention_legend(channel, bot_user)

        self.assertIn("You are <@999>!", result)
        self.assertIn("@Alice = <@1>", result)
        self.assertIn("@Bob = <@2>", result)
