"""Tests for the SSRF-safe HTTP fetch helpers."""

import asyncio
import gzip
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

from src.utils import http_client
from src.utils.http_client import ResponseTooLarge, download_public, fetch_public

_RealAsyncClient = httpx.AsyncClient


def _client_factory(handler, captured=None):
    """Return an AsyncClient replacement that routes requests to ``handler``."""
    def factory(*args, **kwargs):
        if captured is not None:
            captured.append(dict(kwargs))
        kwargs.pop("proxy", None)
        return _RealAsyncClient(*args, transport=httpx.MockTransport(handler), **kwargs)
    return factory


class HttpClientTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.checked = []

        async def fake_assert(url):
            self.checked.append(url)
            host = httpx.URL(url).host
            if host.startswith("10.") or host == "169.254.169.254":
                raise ValueError(f"Refusing to fetch non-public URL {url}")

        patcher = patch("src.utils.http_client.assert_public_url", side_effect=fake_assert)
        patcher.start()
        self.addCleanup(patcher.stop)

    def use_handler(self, handler, captured=None):
        patcher = patch("src.utils.http_client.httpx.AsyncClient", _client_factory(handler, captured))
        patcher.start()
        self.addCleanup(patcher.stop)


class TestFetchPublic(HttpClientTestCase):
    async def test_normal_fetch_returns_fully_read_response(self):
        captured = []

        def handler(request):
            self.assertEqual(request.headers["x-test"], "1")
            self.assertEqual(request.url.params["q"], "abc")
            return httpx.Response(200, text="hello", headers={"content-type": "text/plain"})

        self.use_handler(handler, captured)

        response = await fetch_public(
            "https://example.com/page", headers={"X-Test": "1"}, params={"q": "abc"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "hello")
        self.assertEqual(response.headers["content-type"], "text/plain")
        self.assertEqual(self.checked, ["https://example.com/page"])
        self.assertFalse(captured[0]["follow_redirects"])

    async def test_follows_redirects_checking_every_hop(self):
        def handler(request):
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "/middle"})
            if request.url.path == "/middle":
                return httpx.Response(301, headers={"location": "https://other.example/final"})
            return httpx.Response(200, text="done")

        self.use_handler(handler)

        response = await fetch_public("https://example.com/start")

        self.assertEqual(response.text, "done")
        self.assertEqual(self.checked, [
            "https://example.com/start",
            "https://example.com/middle",
            "https://other.example/final",
        ])

    async def test_redirect_to_private_address_is_refused(self):
        requested = []

        def handler(request):
            requested.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

        self.use_handler(handler)

        with self.assertRaises(ValueError):
            await fetch_public("https://example.com/redirect")

        self.assertEqual(requested, ["https://example.com/redirect"])

    async def test_initial_private_url_is_refused_without_request(self):
        requested = []
        self.use_handler(lambda request: requested.append(request) or httpx.Response(200))

        with self.assertRaises(ValueError):
            await fetch_public("http://10.0.0.1/")

        self.assertEqual(requested, [])

    async def test_too_many_redirects_raises(self):
        self.use_handler(lambda request: httpx.Response(302, headers={"location": "/loop"}))

        with self.assertRaises(httpx.TooManyRedirects):
            await fetch_public("https://example.com/loop")

        self.assertEqual(len(self.checked), http_client.MAX_REDIRECTS + 1)

    async def test_body_over_limit_raises(self):
        # Streamed body with no Content-Length so the byte counter is what trips.
        async def body():
            for _ in range(10):
                yield b"x" * 100

        self.use_handler(lambda request: httpx.Response(200, content=body()))

        with self.assertRaises(ResponseTooLarge):
            await fetch_public("https://example.com/big", max_bytes=500)

    async def test_declared_content_length_over_limit_raises(self):
        self.use_handler(lambda request: httpx.Response(
            200, content=b"small", headers={"content-length": "999999"}))

        with self.assertRaises(ResponseTooLarge):
            await fetch_public("https://example.com/declared", max_bytes=1000)

    async def test_body_at_limit_is_allowed(self):
        self.use_handler(lambda request: httpx.Response(200, content=b"x" * 500))

        response = await fetch_public("https://example.com/exact", max_bytes=500)

        self.assertEqual(len(response.content), 500)

    async def test_gzip_encoded_response_comes_back_decoded(self):
        payload = "hello gzip " * 50
        self.use_handler(lambda request: httpx.Response(
            200,
            content=gzip.compress(payload.encode()),
            headers={"content-encoding": "gzip", "content-type": "text/plain"},
        ))

        response = await fetch_public("https://example.com/gz")

        self.assertEqual(response.text, payload)
        self.assertEqual(response.content, payload.encode())
        self.assertNotIn("content-encoding", response.headers)
        self.assertEqual(response.headers["content-type"], "text/plain")

    async def test_error_status_is_returned_for_caller_to_check(self):
        self.use_handler(lambda request: httpx.Response(404, text="missing"))

        response = await fetch_public("https://example.com/missing")

        self.assertEqual(response.status_code, 404)
        with self.assertRaises(httpx.HTTPStatusError):
            response.raise_for_status()

    async def test_whole_fetch_deadline(self):
        async def slow_fetch(*args, **kwargs):
            await asyncio.sleep(10)

        with patch("src.utils.http_client._fetch", side_effect=slow_fetch):
            with self.assertRaises(asyncio.TimeoutError):
                await fetch_public("https://example.com/slow", timeout=0.01)

    async def test_passes_proxy_to_client(self):
        captured = []
        self.use_handler(lambda request: httpx.Response(200), captured)

        await fetch_public("https://example.com/", proxy="http://proxy:8080")

        self.assertEqual(captured[0]["proxy"], "http://proxy:8080")


class TestDownloadPublic(HttpClientTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "video.mp4")

    async def test_streams_body_to_file(self):
        def handler(request):
            if request.url.path == "/v":
                return httpx.Response(302, headers={"location": "/real.mp4"})
            return httpx.Response(200, content=b"video-bytes")

        self.use_handler(handler)

        await download_public("https://cdn.example/v", self.path)

        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), b"video-bytes")
        self.assertEqual(len(self.checked), 2)

    async def test_too_large_download_removes_partial_file(self):
        async def body():
            for _ in range(10):
                yield b"x" * 100

        self.use_handler(lambda request: httpx.Response(200, content=body()))

        with self.assertRaises(ResponseTooLarge):
            await download_public("https://cdn.example/big.mp4", self.path, max_bytes=250)

        self.assertFalse(os.path.exists(self.path))

    async def test_http_error_removes_file(self):
        self.use_handler(lambda request: httpx.Response(500, content=b"oops"))

        with self.assertRaises(httpx.HTTPStatusError):
            await download_public("https://cdn.example/err.mp4", self.path)

        self.assertFalse(os.path.exists(self.path))

    async def test_redirect_to_private_address_is_refused(self):
        self.use_handler(lambda request: httpx.Response(
            302, headers={"location": "http://10.0.0.5/secret"}))

        with self.assertRaises(ValueError):
            await download_public("https://cdn.example/v.mp4", self.path)

        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
