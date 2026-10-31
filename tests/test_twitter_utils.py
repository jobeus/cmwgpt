"""Tests for Twitter/X URL helpers."""

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.utils import twitter_utils
from tests.config_helpers import AsyncDictCache, cfg

_RealAsyncClient = httpx.AsyncClient


def build_tweet_result(author, text):
    return {
        "legacy": {"full_text": text},
        "core": {"user_results": {"result": {"legacy": {"name": author}}}},
    }


def tweet_payload(main, *replies, media=None):
    main = dict(main)
    if media is not None:
        main["legacy"] = {**main["legacy"], "extended_entities": {"media": media}}
    entries = [{"content": {"itemContent": {"tweet_results": {"result": main}}}}]
    for reply in replies:
        entries.append({"content": {"itemContent": {"tweet_results": {"result": reply}}}})
    return {"data": {"threaded_conversation_with_injections_v2": {"instructions": [None, {"entries": entries}]}}}


class TestTwitterUtils(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Keep the shared fetch-failure store out of these tests.
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

        patcher = patch("src.utils.twitter_utils.httpx.AsyncClient", factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_extract_twitter_urls_normalizes_and_deduplicates(self):
        text = "https://twitter.com/a/status/1 https://xcancel.com/a/status/1 https://x.com/a/status/1"

        urls = twitter_utils.extract_twitter_urls(text)

        self.assertEqual(urls, ["https://x.com/a/status/1"])

    def test_extract_twitter_urls_ignores_profiles_and_non_status_pages(self):
        text = (
            "https://x.com/elonmusk https://x.com/home https://twitter.com/some_user "
            "and a real tweet https://twitter.com/foo/status/42?s=20"
        )

        urls = twitter_utils.extract_twitter_urls(text)

        self.assertEqual(urls, ["https://x.com/foo/status/42?s=20"])

    def test_extract_media_returns_lowest_bitrate_mp4(self):
        data = {
            "data": {
                "threaded_conversation_with_injections_v2": {
                    "instructions": [
                        None,
                        {
                            "entries": [
                                {
                                    "content": {
                                        "itemContent": {
                                            "tweet_results": {
                                                "result": {
                                                    "legacy": {
                                                        "extended_entities": {
                                                            "media": [
                                                                {
                                                                    "video_info": {
                                                                        "variants": [
                                                                            {"content_type": "video/mp4", "bitrate": 999, "url": "high"},
                                                                            {"content_type": "video/mp4", "bitrate": 111, "url": "low"},
                                                                        ]
                                                                    }
                                                                }
                                                            ]
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            ]
                        },
                    ]
                }
            }
        }

        media = twitter_utils.extract_media(data)
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]["url"], "low")
        self.assertEqual(twitter_utils.extract_media({}), [])

    async def test_get_tweet_context_returns_cached_tuple_without_fetching(self):
        cache = AsyncDictCache({"https://x.com/a/status/1": ["cached context", ["data:image/png;base64,AAA"]]})
        self.use_api(lambda request: httpx.Response(500))

        with patch("src.utils.twitter_utils._twitter_cache", cache), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            result = await twitter_utils.get_tweet_context("https://x.com/a/status/1")

        # JSON round-trips tuples as lists; the getter still returns a tuple.
        self.assertEqual(result, ("cached context", ["data:image/png;base64,AAA"]))
        self.assertEqual(self.requests, [])

    async def test_get_tweet_context_cache_only_miss_returns_none(self):
        self.use_api(lambda request: httpx.Response(500))
        with patch("src.utils.twitter_utils._twitter_cache", AsyncDictCache()), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            self.assertIsNone(await twitter_utils.get_tweet_context("https://x.com/a/status/1", cache_only=True))
        self.assertEqual(self.requests, [])

    async def test_get_tweet_context_requires_api_key(self):
        with patch("src.utils.twitter_utils._twitter_cache", AsyncDictCache()), patch(
            "src.config._cached_config", cfg(rapidapi_key="")
        ):
            self.assertIsNone(await twitter_utils.get_tweet_context("https://x.com/a/status/1"))

    async def test_get_tweet_context_rejects_urls_without_status_id(self):
        self.use_api(lambda request: httpx.Response(200, json={}))
        with patch("src.utils.twitter_utils._twitter_cache", AsyncDictCache()), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            result = await twitter_utils.get_tweet_context("https://x.com/elonmusk")

        self.assertIsNone(result)
        self.assertEqual(self.requests, [])

    async def test_get_tweet_context_fetches_context_and_replies(self):
        fake_cache = AsyncDictCache()
        payload = tweet_payload(build_tweet_result("Alice", "Main post"), build_tweet_result("Bob", "Reply one"))
        self.use_api(lambda request: httpx.Response(200, json=payload))

        with patch("src.utils.twitter_utils._twitter_cache", fake_cache), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ), patch("src.utils.twitter_utils.transcribe_video_url", new_callable=AsyncMock) as mock_video:
            result = await twitter_utils.get_tweet_context("https://x.com/a/status/123/photo/1?foo=bar")

        context, images = result
        self.assertIn("Tweet by Alice:\nMain post", context)
        self.assertIn("Top replies:\n  ↳ Bob: Reply one", context)
        self.assertEqual(images, [])
        self.assertEqual(fake_cache["https://x.com/a/status/123/photo/1?foo=bar"], (context, []))
        mock_video.assert_not_called()

        request = self.requests[0]
        self.assertEqual(request.url.params["id"], "123")
        self.assertEqual(request.headers["x-rapidapi-key"], "rapid-key")
        self.assertEqual(request.url.host, twitter_utils.RAPIDAPI_HOST)

    async def test_get_tweet_context_prefers_note_tweet_text(self):
        main = build_tweet_result("Alice", "truncated…")
        main["note_tweet"] = {"note_tweet_results": {"result": {"text": "The full long post"}}}
        self.use_api(lambda request: httpx.Response(200, json=tweet_payload(main)))

        with patch("src.utils.twitter_utils._twitter_cache", AsyncDictCache()), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            context, _ = await twitter_utils.get_tweet_context("https://x.com/a/status/9")

        self.assertIn("The full long post", context)
        self.assertNotIn("Top replies", context)

    async def test_get_tweet_context_returns_none_on_parse_error(self):
        fake_cache = AsyncDictCache()
        self.use_api(lambda request: httpx.Response(200, json={"bad": "shape"}))

        with patch("src.utils.twitter_utils._twitter_cache", fake_cache), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            result = await twitter_utils.get_tweet_context("https://x.com/a/status/123")

        self.assertIsNone(result)
        self.assertEqual(fake_cache, {})

    async def test_get_tweet_context_returns_none_on_http_error(self):
        self.use_api(lambda request: httpx.Response(429, text="rate limited"))

        with patch("src.utils.twitter_utils._twitter_cache", AsyncDictCache()), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ):
            self.assertIsNone(await twitter_utils.get_tweet_context("https://x.com/a/status/123"))

    async def test_get_tweet_context_includes_video_transcript(self):
        media = [{"video_info": {"variants": [
            {"content_type": "video/mp4", "bitrate": 900, "url": "https://video.twimg.com/high.mp4"},
            {"content_type": "video/mp4", "bitrate": 100, "url": "https://video.twimg.com/low.mp4"},
        ]}}]
        payload = tweet_payload(build_tweet_result("Alice", "Main post"), media=media)
        self.use_api(lambda request: httpx.Response(200, json=payload))

        with patch("src.utils.twitter_utils._twitter_cache", AsyncDictCache()), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ), patch(
            "src.utils.twitter_utils.transcribe_video_url", new_callable=AsyncMock, return_value="video words"
        ) as mock_video:
            context, _ = await twitter_utils.get_tweet_context("https://x.com/a/status/123")

        self.assertIn("transcript of video: video words", context)
        mock_video.assert_awaited_once_with("https://video.twimg.com/low.mp4")

    async def test_get_tweet_context_survives_video_failure(self):
        media = [{"video_info": {"variants": [
            {"content_type": "video/mp4", "bitrate": 100, "url": "https://video.twimg.com/v.mp4"}]}}]
        payload = tweet_payload(build_tweet_result("Alice", "Main post"), media=media)
        self.use_api(lambda request: httpx.Response(200, json=payload))

        with patch("src.utils.twitter_utils._twitter_cache", AsyncDictCache()), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ), patch(
            "src.utils.twitter_utils.transcribe_video_url", new_callable=AsyncMock,
            side_effect=RuntimeError("ffmpeg died"),
        ):
            context, _ = await twitter_utils.get_tweet_context("https://x.com/a/status/123")

        self.assertIn("Main post", context)
        self.assertNotIn("transcript of video", context)

    async def test_get_tweet_context_caches_photos_as_base64_data_urls(self):
        fake_cache = AsyncDictCache()
        media = [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/1.jpg"},
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/2.jpg"},
        ]
        payload = tweet_payload(build_tweet_result("Alice", "Look"), media=media)
        self.use_api(lambda request: httpx.Response(200, json=payload))

        async def fake_data_url(url):
            if url.endswith("2.jpg"):
                raise RuntimeError("fetch failed")
            return "data:image/jpeg;base64,ONE"

        with patch("src.utils.twitter_utils._twitter_cache", fake_cache), patch(
            "src.config._cached_config", cfg(rapidapi_key="rapid-key")
        ), patch("src.utils.twitter_utils.url_to_base64_data_url", side_effect=fake_data_url):
            context, images = await twitter_utils.get_tweet_context("https://x.com/a/status/5")

        # A failed photo is skipped; the rest are kept as base64 in the cache.
        self.assertEqual(images, ["data:image/jpeg;base64,ONE"])
        self.assertEqual(fake_cache["https://x.com/a/status/5"], (context, ["data:image/jpeg;base64,ONE"]))


if __name__ == "__main__":
    unittest.main()
