"""Tests for article URL extraction and fetching helpers."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.utils import url_utils


class FakeAsyncClientContext:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeResponse:
    def __init__(self, *, text, url="https://example.com/page", headers=None, status_code=200):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "text/html"}
        self.request = SimpleNamespace(headers={"User-Agent": "UA"})
        self._url = url

    def raise_for_status(self):
        return None


class TestUrlUtils(unittest.IsolatedAsyncioTestCase):
    def test_extract_target_urls_filters_excluded_domains_and_deduplicates(self):
        text = (
            "See https://example.com/post and www.example.com/post and "
            "https://twitter.com/a/status/1 and https://example.com/post"
        )

        urls = url_utils.extract_target_urls(text)

        self.assertEqual(urls, ["https://example.com/post", "www.example.com/post"])

    def test_extract_target_urls_skips_discord_cdn_and_direct_media_urls(self):
        text = (
            "https://cdn.discordapp.com/ephemeral-attachments/1/2/image.png?ex=abc "
            "https://media.discordapp.net/attachments/1/2/photo.jpg "
            "https://example.com/image.webp?width=1024&height=768 "
            "https://example.com/article "
            "https://example.com/post.html"
        )

        urls = url_utils.extract_target_urls(text)

        self.assertEqual(urls, ["https://example.com/article", "https://example.com/post.html"])

    def test_inject_article_cache_writes_directly(self):
        fake_cache = {}

        with patch("src.utils.url_utils._article_cache", fake_cache):
            url_utils.inject_article_cache("https://example.com", "hello")

        self.assertEqual(fake_cache["https://example.com"], "hello")

    async def test_get_article_text_returns_cached_values_and_retries_legacy_failure_sentinels(self):
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=FakeResponse(text="<html>content</html>"))
        legacy_cache = {"https://ok": "cached", "https://bad": None}

        with patch("src.utils.url_utils._article_cache", legacy_cache), patch(
            "src.utils.url_utils.httpx.AsyncClient",
            side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client),
        ), patch("src.utils.url_utils.trafilatura.extract", return_value="parsed text"), patch(
            "src.utils.url_utils.log_pipeline_step", new=AsyncMock()
        ):
            self.assertEqual(await url_utils.get_article_text("https://ok"), "cached")
            self.assertEqual(await url_utils.get_article_text("https://bad"), "parsed text")

        self.assertEqual(legacy_cache["https://bad"], "parsed text")

    async def test_get_article_text_uses_trafilatura_and_logs(self):
        fake_cache = {}
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=FakeResponse(text="<html>content</html>"))

        with patch("src.utils.url_utils._article_cache", fake_cache), patch(
            "src.utils.url_utils.httpx.AsyncClient",
            side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client),
        ), patch("src.utils.url_utils.trafilatura.extract", return_value="parsed text"), patch(
            "src.utils.url_utils.log_pipeline_step", new=AsyncMock()
        ) as mock_log:
            result = await url_utils.get_article_text("https://example.com/page")

        self.assertEqual(result, "parsed text")
        self.assertEqual(fake_cache["https://example.com/page"], "parsed text")
        self.assertEqual(mock_log.await_count, 2)

    async def test_get_article_text_falls_back_to_newspaper(self):
        fake_cache = {}
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=FakeResponse(text="<html>fallback</html>"))
        article = MagicMock()
        article.text = "article text"

        with patch("src.utils.url_utils._article_cache", fake_cache), patch(
            "src.utils.url_utils.httpx.AsyncClient",
            side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client),
        ), patch("src.utils.url_utils.trafilatura.extract", return_value=""), patch(
            "src.utils.url_utils.Article", return_value=article
        ), patch("src.utils.url_utils.log_pipeline_step", new=AsyncMock()):
            result = await url_utils.get_article_text("https://example.com/fallback")

        self.assertEqual(result, "article text")
        article.set_html.assert_called_once_with("<html>fallback</html>")
        article.parse.assert_called_once_with()

    async def test_get_article_text_returns_none_on_fetch_failure(self):
        fake_client = MagicMock()
        fake_client.get = AsyncMock(side_effect=RuntimeError("network down"))

        with patch("src.utils.url_utils._article_cache", {}), patch(
            "src.utils.url_utils.httpx.AsyncClient",
            side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client),
        ):
            result = await url_utils.get_article_text("https://example.com/fail")

        self.assertIsNone(result)

    async def test_get_article_text_fetches_old_reddit_but_caches_original_url(self):
        original_url = "https://www.reddit.com/r/test/comments/1/reddit.com_in_title/"
        fake_cache = {}
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=FakeResponse(text="<html>reddit</html>"))

        with patch("src.utils.url_utils._article_cache", fake_cache), patch(
            "src.utils.url_utils.httpx.AsyncClient",
            side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client),
        ), patch("src.utils.url_utils.trafilatura.extract", return_value="reddit text"), patch(
            "src.utils.url_utils.log_pipeline_step", new=AsyncMock()
        ), patch("src.utils.url_utils.assert_public_url", new=AsyncMock()):
            result = await url_utils.get_article_text(original_url)

        self.assertEqual(result, "reddit text")
        # Only the host is swapped to old.reddit.com; the path stays intact.
        fetched_url = fake_client.get.await_args[0][0]
        self.assertEqual(fetched_url, "https://old.reddit.com/r/test/comments/1/reddit.com_in_title/")
        # The cache is keyed on the original URL so future lookups hit.
        self.assertEqual(fake_cache[original_url], "reddit text")

    async def test_get_article_text_blocks_non_public_urls(self):
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=FakeResponse(text="<html>internal</html>"))

        with patch("src.utils.url_utils._article_cache", {}), patch(
            "src.utils.url_utils.httpx.AsyncClient",
            side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client),
        ):
            result = await url_utils.get_article_text("http://127.0.0.1:8080/admin")

        self.assertIsNone(result)
        fake_client.get.assert_not_awaited()
