"""Tests for Twitter/X URL helpers."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from src.utils import twitter_utils


class FakeAsyncClientContext:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeStreamContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        yield b"video-bytes"


def build_tweet_result(author, text):
    return {
        "legacy": {"full_text": text},
        "core": {"user_results": {"result": {"legacy": {"name": author}}}},
    }


class TestTwitterUtils(unittest.IsolatedAsyncioTestCase):
    def test_extract_twitter_urls_normalizes_and_deduplicates(self):
        text = "https://twitter.com/a/status/1 https://xcancel.com/a/status/1 https://x.com/a/status/1"

        urls = twitter_utils.extract_twitter_urls(text)

        self.assertEqual(urls, ["https://x.com/a/status/1"])

    def test_extract_video_url_returns_lowest_bitrate_mp4(self):
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

        self.assertEqual(twitter_utils.extract_video_url(data), "low")
        self.assertIsNone(twitter_utils.extract_video_url({}))

    async def test_get_tweet_context_returns_cached_values(self):
        with patch("src.utils.twitter_utils._twitter_cache", {"https://x.com/ok": "cached", "https://x.com/bad": None}):
            self.assertEqual(await twitter_utils.get_tweet_context("https://x.com/ok"), "cached")
            self.assertIsNone(await twitter_utils.get_tweet_context("https://x.com/bad"))

    async def test_get_tweet_context_requires_api_key(self):
        with patch("src.utils.twitter_utils.RAPIDAPI_KEY", ""):
            self.assertIsNone(await twitter_utils.get_tweet_context("https://x.com/a/status/1"))

    async def test_get_tweet_context_fetches_context_and_replies(self):
        fake_cache = {}
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "data": {
                "threaded_conversation_with_injections_v2": {
                    "instructions": [
                        None,
                        {"entries": [
                            {"content": {"itemContent": {"tweet_results": {"result": build_tweet_result("Alice", "Main post")}}}},
                            {"content": {"itemContent": {"tweet_results": {"result": build_tweet_result("Bob", "Reply one")}}}},
                        ]}
                    ]
                }
            }
        }
        fake_response.request = SimpleNamespace(method="GET", url="https://api.example/tweet", headers={"X": "1"}, content=b"")
        fake_response.status_code = 200
        fake_response.headers = {"Content-Type": "application/json"}
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)

        with patch("src.utils.twitter_utils._twitter_cache", fake_cache), patch(
            "src.utils.twitter_utils.RAPIDAPI_KEY", "rapid-key"
        ), patch("src.utils.twitter_utils.GROQ_API_KEY", ""), patch(
            "src.utils.twitter_utils.create_async_client",
            side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client),
        ), patch("src.utils.twitter_utils.extract_video_url", return_value=None):
            result = await twitter_utils.get_tweet_context("https://x.com/a/status/123?foo=bar")

        self.assertIn("Tweet by Alice:\nMain post", result)
        self.assertIn("↳ Bob: Reply one", result)
        self.assertEqual(fake_cache["https://x.com/a/status/123?foo=bar"], result)

    async def test_get_tweet_context_returns_none_on_parse_error(self):
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {"bad": "shape"}
        fake_response.request = SimpleNamespace(method="GET", url="https://api.example/tweet", headers={}, content=b"")
        fake_response.status_code = 200
        fake_response.headers = {}
        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)

        with patch("src.utils.twitter_utils._twitter_cache", {}), patch(
            "src.utils.twitter_utils.RAPIDAPI_KEY", "rapid-key"
        ), patch("src.utils.twitter_utils.create_async_client", side_effect=lambda **kwargs: FakeAsyncClientContext(fake_client)):
            result = await twitter_utils.get_tweet_context("https://x.com/a/status/123")

        self.assertIsNone(result)

    async def test_get_tweet_context_includes_video_transcript_when_available(self):
        fake_cache = {}
        first_response = MagicMock()
        first_response.raise_for_status.return_value = None
        first_response.json.return_value = {
            "data": {
                "threaded_conversation_with_injections_v2": {
                    "instructions": [
                        None,
                        {"entries": [
                            {"content": {"itemContent": {"tweet_results": {"result": build_tweet_result("Alice", "Main post")}}}},
                            {"content": {"itemContent": {"tweet_results": {"result": build_tweet_result("Bob", "Reply one")}}}},
                        ]}
                    ]
                }
            }
        }
        first_response.request = SimpleNamespace(method="GET", url="https://api.example/tweet", headers={}, content=b"")
        first_response.status_code = 200
        first_response.headers = {}

        fetch_client = MagicMock()
        fetch_client.get = AsyncMock(return_value=first_response)

        stream_client = MagicMock()
        stream_client.stream.return_value = FakeStreamContext()

        groq_request = SimpleNamespace(
            method="POST",
            url="https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": "Bearer groq"},
            content=b"audio",
            aread=AsyncMock(),
        )
        groq_response = SimpleNamespace(
            raise_for_status=lambda: None,
            text="video words",
            request=groq_request,
            status_code=200,
            headers={"Content-Type": "text/plain"},
        )
        post_client = MagicMock()
        post_client.post = AsyncMock(return_value=groq_response)

        contexts = [
            FakeAsyncClientContext(fetch_client),
            FakeAsyncClientContext(stream_client),
            FakeAsyncClientContext(post_client),
        ]

        with patch("src.utils.twitter_utils._twitter_cache", fake_cache), patch(
            "src.utils.twitter_utils.RAPIDAPI_KEY", "rapid-key"
        ), patch("src.utils.twitter_utils.GROQ_API_KEY", "groq-key"), patch(
            "src.utils.twitter_utils.create_async_client", side_effect=contexts
        ), patch("src.utils.twitter_utils.extract_video_url", return_value="https://cdn.example/video.mp4"), patch(
            "src.utils.twitter_utils.asyncio.to_thread", new=AsyncMock(return_value=None)
        ), patch("src.utils.twitter_utils.os.path.exists", return_value=True), patch(
            "src.utils.twitter_utils.os.remove"
        ) as mock_remove, patch("builtins.open", mock_open(read_data=b"audio-bytes")), patch(
            "src.utils.twitter_utils.uuid.uuid4", return_value=SimpleNamespace(hex="abc123")
        ):
            result = await twitter_utils.get_tweet_context("https://x.com/a/status/123")

        self.assertIn("transcript of video: video words", result)
        self.assertEqual(mock_remove.call_count, 2)
