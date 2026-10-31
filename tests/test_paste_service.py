"""Tests for PasteService using injected client factories and cache hooks."""

import unittest
from unittest.mock import AsyncMock

import httpx

from src.services.paste_service import PasteService


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class FakeClientContext:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestPasteService(unittest.IsolatedAsyncioTestCase):
    async def test_upload_text_returns_markdown_url_and_injects_cache(self):
        injected = []

        class FakeClient:
            async def post(self, url, content):
                self.last_url = url
                self.last_content = content
                return FakeResponse(201, "https://paste.jobe.wtf/abc123")

        fake_client = FakeClient()
        service = PasteService(
            cache_injector=AsyncMock(side_effect=lambda url, text: injected.append((url, text))),
            client_factory=lambda timeout: FakeClientContext(fake_client),
        )

        result = await service.upload_text("hello world")

        self.assertEqual(result, "https://paste.jobe.wtf/abc123.md")
        self.assertEqual(fake_client.last_url, "https://paste.jobe.wtf")
        self.assertEqual(fake_client.last_content, b"hello world")
        self.assertEqual(injected, [("https://paste.jobe.wtf/abc123.md", "hello world")])

    async def test_upload_text_raises_on_non_201_status(self):
        class FakeClient:
            async def post(self, url, content):
                return FakeResponse(500, "nope")

        service = PasteService(client_factory=lambda timeout: FakeClientContext(FakeClient()))

        with self.assertRaises(Exception) as ctx:
            await service.upload_text("bad")

        self.assertIn("Paste service error: 500 - nope", str(ctx.exception))

    async def test_upload_text_wraps_request_errors(self):
        request = httpx.Request("POST", "https://paste.jobe.wtf")

        class FakeClient:
            async def post(self, url, content):
                raise httpx.RequestError("network down", request=request)

        service = PasteService(client_factory=lambda timeout: FakeClientContext(FakeClient()))

        with self.assertRaises(Exception) as ctx:
            await service.upload_text("boom")

        self.assertIn("Failed to upload to paste service", str(ctx.exception))

    async def test_upload_markdown_delegates_to_upload_text(self):
        class FakeClient:
            async def post(self, url, content):
                return FakeResponse(201, "https://paste.jobe.wtf/xyz789")

        service = PasteService(client_factory=lambda timeout: FakeClientContext(FakeClient()))

        result = await service.upload_markdown("# title")

        self.assertEqual(result, "https://paste.jobe.wtf/xyz789.md")

    async def test_default_client_factory_is_httpx_async_client(self):
        service = PasteService()
        self.assertIs(service._client_factory, httpx.AsyncClient)

        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["body"] = request.content
            return httpx.Response(201, text="https://paste.jobe.wtf/real\n")

        transport = httpx.MockTransport(handler)
        service = PasteService(
            client_factory=lambda timeout: httpx.AsyncClient(timeout=timeout, transport=transport)
        )

        self.assertEqual(await service.upload_text("hi"), "https://paste.jobe.wtf/real.md")
        self.assertEqual(seen, {"url": "https://paste.jobe.wtf", "body": b"hi"})
