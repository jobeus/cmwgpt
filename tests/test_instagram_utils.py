"""Tests for the Instagram/Threads fetcher: the Threads API, and its preview-card fallback."""

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


POST_ID = "4039412338625577264"
API_POST = {
    "code": "DgO4B7JG4Ew",
    "user": {"username": "minh_nbk"},
    "taken_at": 1795755496,
    "like_count": 563,
    "media_type": 1,
    "caption": {"text": "GTA 6 weather stuck on sunny forever?\n1. shut your PS5 down"},
    "image_versions2": {"candidates": [{"url": "https://cdn.example.net/big.jpg"},
                                       {"url": "https://cdn.example.net/small.jpg"}]},
    "carousel_media": None,
    "video_versions": None,
    "text_post_app_info": {"direct_reply_count": 67, "repost_count": 35, "quote_count": 2,
                           "reshare_count": 192, "link_preview_attachment": None},
}


def api_response(post):
    return {"data": {"data": {"edges": [{"node": {"thread_items": [{"post": post}]}}]}}}


class TestParse(unittest.TestCase):
    def test_threads_urls(self):
        self.assertTrue(instagram_utils.is_threads_url(SHARE))
        self.assertTrue(instagram_utils.is_threads_url("https://threads.net/@a/post/b"))
        self.assertFalse(instagram_utils.is_threads_url("https://www.instagram.com/p/abc"))

    def test_post_id_is_the_shortcode_in_base_64(self):
        self.assertEqual(instagram_utils.threads_post_id(POST + "?xmt=abc"), POST_ID)
        self.assertIsNone(instagram_utils.threads_post_id(SHARE))

    def test_renders_a_threads_post(self):
        text, images, video = instagram_utils.render_threads_post(API_POST)
        self.assertEqual(text, (
            "Post on Threads by @minh_nbk on 2026-11-27\n"
            "563 likes, 67 replies, 35 reposts, 2 quotes, 192 shares\n"
            "Text: GTA 6 weather stuck on sunny forever?\n1. shut your PS5 down"
        ))
        self.assertEqual(images, ["https://cdn.example.net/big.jpg"])
        self.assertIsNone(video)

    def test_renders_a_carousel_with_a_video(self):
        carousel = dict(API_POST, carousel_media=[
            {"image_versions2": {"candidates": [{"url": f"https://cdn.example.net/{i}.jpg"}]},
             "video_versions": [{"url": "https://cdn.example.net/v.mp4"}] if i == 2 else None}
            for i in range(6)
        ])
        text, images, video = instagram_utils.render_threads_post(carousel)
        self.assertEqual(len(images), 4)
        self.assertEqual(video, "https://cdn.example.net/v.mp4")
        self.assertIn("Carousel of 6 items, the first 4 images attached", text)

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
        if request.url.host in (instagram_utils.RAPIDAPI_HOST, instagram_utils.THREADS_RAPIDAPI_HOST):
            return httpx.Response(200, json={"error": True, "message": "Media not found or unable to download"})
        if request.url.path == "/share/BAEITDTd3q":
            return httpx.Response(302, headers={"location": POST + "?xmt=abc"})
        return httpx.Response(200, text=PAGE)

    @patch("src.utils.instagram_utils.url_to_base64_data_url", new_callable=AsyncMock,
           return_value="data:image/jpeg;base64,QUFB")
    async def test_threads_share_link_comes_from_the_threads_api(self, to_data_url):
        def handler(request):
            if request.url.host == instagram_utils.THREADS_RAPIDAPI_HOST:
                return httpx.Response(200, json=api_response(API_POST))
            return self.handler(request)

        self.use_http(handler)
        cache = AsyncDictCache()
        with patch("src.utils.instagram_utils._instagram_cache", cache), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            context, images = await instagram_utils.get_instagram_context(SHARE)

        self.assertTrue(context.startswith("Post on Threads by @minh_nbk on 2026-11-27\n"))
        self.assertEqual(images, ["data:image/jpeg;base64,QUFB"])
        self.assertEqual(cache[SHARE], (context, images))
        to_data_url.assert_awaited_once_with("https://cdn.example.net/big.jpg")
        api = [r for r in self.requests if r.url.host == instagram_utils.THREADS_RAPIDAPI_HOST]
        self.assertEqual(api[0].url.path, "/api/post/detail")
        self.assertEqual(api[0].url.params["post_id"], POST_ID)
        self.assertEqual(api[0].headers["x-rapidapi-key"], "rapid-key")
        self.assertFalse([r for r in self.requests if r.url.host == instagram_utils.RAPIDAPI_HOST])

    async def test_old_cache_entries_hold_one_image(self):
        cache = AsyncDictCache({POST: ("text", "data:x"), SHARE: ("text", None)})
        with patch("src.utils.instagram_utils._instagram_cache", cache):
            self.assertEqual(await instagram_utils.get_instagram_context(POST), ("text", ["data:x"]))
            self.assertEqual(await instagram_utils.get_instagram_context(SHARE), ("text", []))

    @patch("src.utils.instagram_utils.url_to_base64_data_url", new_callable=AsyncMock,
           return_value="data:image/jpeg;base64,QUFB")
    async def test_threads_falls_back_to_the_crawler_card(self, to_data_url):
        self.use_http(self.handler)
        cache = AsyncDictCache()
        with patch("src.utils.instagram_utils._instagram_cache", cache), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            context, images = await instagram_utils.get_instagram_context(SHARE)

        self.assertTrue(context.startswith("Post on Threads: Minh Nbk (@minh_nbk) on Threads\nLink: " + POST))
        self.assertEqual(images, ["data:image/jpeg;base64,QUFB"])
        self.assertEqual(cache[SHARE], (context, images))
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
        self.assertFalse([r for r in self.requests if r.url.host.endswith("rapidapi.com")])

    async def test_instagram_has_no_card_fallback(self):
        self.use_http(self.handler)
        with patch("src.utils.instagram_utils._instagram_cache", AsyncDictCache()), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            self.assertIsNone(await instagram_utils.get_instagram_context("https://www.instagram.com/p/abc"))
        self.assertEqual({r.url.host for r in self.requests}, {instagram_utils.RAPIDAPI_HOST})

    async def test_no_card_image_is_a_failure(self):
        self.use_http(lambda request: httpx.Response(200, text="<html></html>")
                      if not request.url.host.endswith("rapidapi.com")
                      else httpx.Response(200, json={"error": True}))
        with patch("src.utils.instagram_utils._instagram_cache", AsyncDictCache()), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            self.assertIsNone(await instagram_utils.get_instagram_context(POST))


if __name__ == "__main__":
    unittest.main()
