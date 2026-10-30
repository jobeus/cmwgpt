"""Tests for the Reddit post fetcher."""

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.utils import reddit_utils
from src.utils.downloader_utils import SOURCES
from src.utils.url_utils import extract_target_urls
from tests.config_helpers import cfg

_RealAsyncClient = httpx.AsyncClient

POST = "https://www.reddit.com/r/fut/comments/1jv41on/baby_coming_in_team_2/"


def post(**overrides):
    """A trimmed getPostDetails ``data`` object for an image post."""
    data = {
        "title": "Baby coming in team 2",
        "author": "No-Jackfruit6571",
        "subreddit_name_prefixed": "r/fut",
        "created_utc": 1744200072,
        "link_flair_text": "Leaks",
        "spoiler": True,
        "score": 4,
        "upvote_ratio": 1,
        "num_comments": 13,
        "selftext": "",
        "is_self": False,
        "is_video": False,
        "url": "https://i.redd.it/oezh7n6buste1.jpeg",
        "url_overridden_by_dest": "https://i.redd.it/oezh7n6buste1.jpeg",
        "preview": {"images": [{"source": {
            "url": "https://preview.redd.it/oezh7n6buste1.jpeg?auto=webp&amp;s=1584", "width": 1170}}]},
    }
    data.update(overrides)
    return data


class TestExtract(unittest.TestCase):
    def test_normalizes_post_links_and_skips_other_pages(self):
        text = (
            "(https://old.reddit.com/r/fut/comments/1JV41ON/baby_coming_in_team_2/?utm_source=share). "
            f"{POST} https://redd.it/abc123 https://www.reddit.com/r/pics/ https://reddit.com/r/x/s/AbCd"
        )
        self.assertEqual(reddit_utils.extract_reddit_urls(text), [POST, "https://www.reddit.com/comments/abc123/"])

    def test_post_links_skip_the_article_fetcher_but_other_reddit_pages_dont(self):
        urls = extract_target_urls(f"{POST} https://www.reddit.com/r/pics/ https://v.redd.it/abc")
        self.assertEqual(urls, ["https://www.reddit.com/r/pics/", "https://v.redd.it/abc"])
        self.assertIn("reddit", [s.name for s in SOURCES])


class TestRender(unittest.TestCase):
    def test_image_post(self):
        self.assertEqual(reddit_utils.render_post(post()), (
            "Title: Baby coming in team 2\n"
            "Posted in r/fut by u/No-Jackfruit6571 on 2025-04-09\n"
            "(flair: Leaks, spoiler)\n"
            "Score 4 (100% upvoted), 13 comments\n"
            "Links to: https://i.redd.it/oezh7n6buste1.jpeg"
        ))
        self.assertEqual(reddit_utils._image_urls(post()),
                         ["https://preview.redd.it/oezh7n6buste1.jpeg?auto=webp&s=1584"])

    def test_text_post_unescapes_and_caps_its_text(self):
        text_post = post(is_self=True, url="https://www.reddit.com/r/fut/comments/x/", url_overridden_by_dest=None,
                         selftext="a &amp; b " + "x" * reddit_utils.MAX_SELFTEXT_CHARS, preview=None)
        rendered = reddit_utils.render_post(text_post)
        self.assertIn("Text:\na & b x", rendered)
        self.assertTrue(rendered.endswith("…"))
        self.assertNotIn("Links to", rendered)

    def test_gallery_images_come_in_order(self):
        gallery = post(gallery_data={"items": [{"media_id": "b"}, {"media_id": "a"}]},
                       media_metadata={"a": {"s": {"u": "https://i/a.jpg"}}, "b": {"s": {"u": "https://i/b.jpg"}}})
        self.assertEqual(reddit_utils._image_urls(gallery), ["https://i/b.jpg", "https://i/a.jpg"])
        self.assertNotIn("Links to", reddit_utils.render_post(gallery))


class TestFetch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("src.utils.cache_utils._failure_store", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.requests = []

    def use_api(self, handler):
        def wrapped(request):
            self.requests.append(request)
            return handler(request)

        def factory(*args, **kwargs):
            return _RealAsyncClient(*args, transport=httpx.MockTransport(wrapped), **kwargs)

        patcher = patch("src.utils.reddit_utils.httpx.AsyncClient", factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("src.utils.reddit_utils.url_to_base64_data_url", new_callable=AsyncMock,
           return_value="data:image/jpeg;base64,QUFB")
    async def test_fetches_renders_and_caches_the_post(self, to_data_url):
        self.use_api(lambda request: httpx.Response(200, json={"success": True, "data": post()}))
        cache = {}

        with patch("src.utils.reddit_utils._reddit_cache", cache), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            text, images = await reddit_utils.get_reddit_post(POST)

        self.assertTrue(text.startswith("Title: Baby coming in team 2\n"))
        self.assertEqual(images, ["data:image/jpeg;base64,QUFB"])
        self.assertEqual(cache[POST], (text, images))
        to_data_url.assert_awaited_once_with("https://preview.redd.it/oezh7n6buste1.jpeg?auto=webp&s=1584")
        request = self.requests[0]
        self.assertEqual(request.url.path, "/getPostDetails")
        self.assertEqual(request.url.params["post_url"], POST)
        self.assertEqual(request.headers["x-rapidapi-key"], "rapid-key")

    async def test_failures_and_cache_only_misses_return_none(self):
        self.use_api(lambda request: httpx.Response(200, json={"success": False}))
        with patch("src.utils.reddit_utils._reddit_cache", {}), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            self.assertIsNone(await reddit_utils.get_reddit_post(POST))
            self.assertIsNone(await reddit_utils.get_reddit_post(POST, cache_only=True))
        with patch("src.utils.reddit_utils._reddit_cache", {}), patch(
            "src.config._cached_config", cfg(rapidapi_key="")
        ):
            self.assertIsNone(await reddit_utils.get_reddit_post(POST + "x/"))
