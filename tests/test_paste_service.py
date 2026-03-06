"""Tests for PasteService using injected client factories and cache hooks."""

import unittest

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
                return FakeResponse(201, "https://paste.rs/abc123")

        fake_client = FakeClient()
        service = PasteService(
            cache_injector=lambda url, text: injected.append((url, text)),
            client_factory=lambda timeout: FakeClientContext(fake_client),
        )

        result = await service.upload_text("hello world")

        self.assertEqual(result, "https://paste.rs/abc123.md")
        self.assertEqual(fake_client.last_url, "https://paste.rs")
        self.assertEqual(fake_client.last_content, b"hello world")
        self.assertEqual(injected, [("https://paste.rs/abc123.md", "hello world")])

    async def test_upload_text_raises_on_non_201_status(self):
        class FakeClient:
            async def post(self, url, content):
                return FakeResponse(500, "nope")

        service = PasteService(client_factory=lambda timeout: FakeClientContext(FakeClient()))

        with self.assertRaises(Exception) as ctx:
            await service.upload_text("bad")

        self.assertIn("paste.rs error: 500 - nope", str(ctx.exception))

    async def test_upload_text_wraps_request_errors(self):
        request = httpx.Request("POST", "https://paste.rs")

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
                return FakeResponse(201, "https://paste.rs/xyz789")

        service = PasteService(client_factory=lambda timeout: FakeClientContext(FakeClient()))

        result = await service.upload_markdown("# title")

        self.assertEqual(result, "https://paste.rs/xyz789.md")
