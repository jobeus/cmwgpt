"""Tests for the Instagram/Threads fetcher's Threads preview-card fallback."""

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.utils import instagram_utils
from tests.config_helpers import AsyncDictCache, cfg

_RealAsyncClient = httpx.AsyncClient

SHARE = "https://www.threads.com/share/BAEITDTd3q"
POST = "https://www.threads.com/@minh_nbk/post/DgO4B7JG4Ew"
CARD = "https://scontent.example.net/v/card.jpg?a=1&b=2"
PAGE = (
    '<html><head><meta property="og:type" content="article" />'
    '<meta property="og:title" content="Minh Nbk (&#064;minh_nbk) on Threads" />'
    '<meta property="og:url" content="https://www.threads.com/&#064;minh_nbk/post/DgO4B7JG4Ew" />'
    '<meta property="og:image" content="https://scontent.example.net/v/card.jpg?a=1&amp;b=2" />'
    '<meta property="og:image" content="https://scontent.example.net/other.jpg" />'
    '</head></html>'
)


class TestParse(unittest.TestCase):
    def test_threads_urls(self):
        self.assertTrue(instagram_utils.is_threads_url(SHARE))
        self.assertTrue(instagram_utils.is_threads_url("https://threads.net/@a/post/b"))
        self.assertFalse(instagram_utils.is_threads_url("https://www.instagram.com/p/abc"))

    def test_og_tags_decode_entities_and_keep_the_first(self):
        tags = instagram_utils.parse_og_tags(PAGE)
        self.assertEqual(tags["og:title"], "Minh Nbk (@minh_nbk) on Threads")
        self.assertEqual(tags["og:image"], CARD)


class TestFetch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("src.utils.cache_utils._failure_store", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.requests = []

    def use_http(self, handler):
        def wrapped(request):
            self.requests.append(request)
            return handler(request)

        def factory(*args, **kwargs):
            return _RealAsyncClient(*args, transport=httpx.MockTransport(wrapped), **kwargs)

        patcher = patch("src.utils.instagram_utils.httpx.AsyncClient", factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def handler(request):
        if request.url.host == instagram_utils.RAPIDAPI_HOST:
            return httpx.Response(200, json={"error": True, "message": "Media not found or unable to download"})
        if request.url.path == "/share/BAEITDTd3q":
            return httpx.Response(302, headers={"location": POST + "?xmt=abc"})
        return httpx.Response(200, text=PAGE)

    @patch("src.utils.instagram_utils.url_to_base64_data_url", new_callable=AsyncMock,
           return_value="data:image/jpeg;base64,QUFB")
    async def test_threads_falls_back_to_the_crawler_card(self, to_data_url):
        self.use_http(self.handler)
        cache = AsyncDictCache()
        with patch("src.utils.instagram_utils._instagram_cache", cache), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            context, image = await instagram_utils.get_instagram_context(SHARE)

        self.assertTrue(context.startswith("Post on Threads: Minh Nbk (@minh_nbk) on Threads\nLink: " + POST))
        self.assertEqual(image, "data:image/jpeg;base64,QUFB")
        self.assertEqual(cache[SHARE], (context, image))
        to_data_url.assert_awaited_once_with(CARD)
        page_requests = [r for r in self.requests if r.url.host == "www.threads.com"]
        self.assertEqual(page_requests[0].headers["user-agent"], instagram_utils.CRAWLER_USER_AGENT)

    @patch("src.utils.instagram_utils.url_to_base64_data_url", new_callable=AsyncMock,
           return_value="data:image/jpeg;base64,QUFB")
    async def test_threads_card_works_without_a_rapidapi_key(self, _):
        self.use_http(self.handler)
        with patch("src.utils.instagram_utils._instagram_cache", AsyncDictCache()), patch(
            "src.config._cached_config", cfg(rapidapi_key="")
        ):
            self.assertIsNotNone(await instagram_utils.get_instagram_context(POST))
        self.assertFalse([r for r in self.requests if r.url.host == instagram_utils.RAPIDAPI_HOST])

    async def test_instagram_has_no_card_fallback(self):
        self.use_http(self.handler)
        with patch("src.utils.instagram_utils._instagram_cache", AsyncDictCache()), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            self.assertIsNone(await instagram_utils.get_instagram_context("https://www.instagram.com/p/abc"))
        self.assertEqual({r.url.host for r in self.requests}, {instagram_utils.RAPIDAPI_HOST})

    async def test_no_card_image_is_a_failure(self):
        self.use_http(lambda request: httpx.Response(200, text="<html></html>")
                      if request.url.host != instagram_utils.RAPIDAPI_HOST
                      else httpx.Response(200, json={"error": True}))
        with patch("src.utils.instagram_utils._instagram_cache", AsyncDictCache()), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            self.assertIsNone(await instagram_utils.get_instagram_context(POST))


if __name__ == "__main__":
    unittest.main()
