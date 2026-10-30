"""Tests for the Reddit comment-thread fetcher."""

import unittest
from unittest.mock import patch

import httpx

from src.utils import reddit_utils
from src.utils.downloader_utils import SOURCES
from tests.config_helpers import cfg

_RealAsyncClient = httpx.AsyncClient

POST = "https://www.reddit.com/r/AskReddit/comments/ablzuq/people_who/"


def comment(body, depth=0, author="someone", score=3, created=1634330161):
    return {"body": body, "depth": depth, "author": author, "score": score, "created_utc": created,
            "subreddit_name_prefixed": "r/AskReddit"}


class TestExtract(unittest.TestCase):
    def test_normalizes_post_links_and_skips_other_pages(self):
        text = (
            f"(https://old.reddit.com/r/AskReddit/comments/ABLZUQ/people_who/?utm_source=share). "
            f"{POST} https://redd.it/abc123 https://www.reddit.com/r/pics/ https://reddit.com/r/x/s/AbCd"
        )
        self.assertEqual(reddit_utils.extract_reddit_urls(text), [POST, "https://www.reddit.com/comments/abc123/"])

    def test_reddit_comes_before_the_article_source(self):
        names = [s.name for s in SOURCES]
        self.assertLess(names.index("reddit"), names.index("article"))


class TestRender(unittest.TestCase):
    def test_indents_replies_unescapes_and_drops_deleted(self):
        thread = reddit_utils.render_thread([
            comment("top &amp; first"),
            comment("[deleted]", depth=1),
            comment("a reply\nsecond line", depth=1, author="b", score=1),
        ])
        self.assertEqual(thread, (
            "- u/someone (3 points, 2021-10-15): top & first\n"
            "  - u/b (1 points, 2021-10-15): a reply\n    second line"
        ))

    def test_caps_long_comments_and_threads(self):
        long_body = "x" * (reddit_utils.MAX_COMMENT_CHARS + 50)
        self.assertTrue(reddit_utils.render_thread([comment(long_body)]).endswith("…"))
        many = [comment("y" * 900) for _ in range(100)]
        thread = reddit_utils.render_thread(many)
        self.assertLessEqual(len(thread), reddit_utils.MAX_THREAD_CHARS + 100)
        self.assertTrue(thread.endswith("(more comments not shown)"))


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

    async def test_fetches_renders_and_caches_the_thread(self):
        payload = {"success": True, "data": {"cursor": "c", "comments": [comment("Chicken nuggets")]}}
        self.use_api(lambda request: httpx.Response(200, json=payload))
        cache = {}

        with patch("src.utils.reddit_utils._reddit_cache", cache), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            text = await reddit_utils.get_reddit_thread(POST)

        self.assertEqual(text, "r/AskReddit comments, top first:\n- u/someone (3 points, 2021-10-15): Chicken nuggets")
        self.assertEqual(cache[POST], text)
        request = self.requests[0]
        self.assertEqual(request.url.path, "/getPostCommentsWithSortV2")
        self.assertEqual(request.url.params["post_url"], POST)
        self.assertEqual(request.headers["x-rapidapi-key"], "rapid-key")

    async def test_failures_and_cache_only_misses_return_none(self):
        self.use_api(lambda request: httpx.Response(200, json={"success": False}))
        with patch("src.utils.reddit_utils._reddit_cache", {}), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            self.assertIsNone(await reddit_utils.get_reddit_thread(POST))
            self.assertIsNone(await reddit_utils.get_reddit_thread(POST, cache_only=True))
        with patch("src.utils.reddit_utils._reddit_cache", {}), patch(
            "src.config._cached_config", cfg(rapidapi_key="")
        ):
            self.assertIsNone(await reddit_utils.get_reddit_thread(POST + "x/"))
