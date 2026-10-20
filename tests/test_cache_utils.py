"""Tests for persistent cache helpers."""

import json
import unittest
from unittest.mock import MagicMock, patch

import fakeredis
import redis

from src.utils import cache_utils
from src.utils.cache_utils import LRUCache, PersistentCache


class TestPersistentCache(unittest.TestCase):
    def setUp(self):
        self.redis = fakeredis.FakeRedis(decode_responses=True)

    def make(self, name, **kwargs):
        return PersistentCache(name, client_getter=lambda: self.redis, **kwargs)

    def test_values_round_trip_as_json_under_a_prefixed_key(self):
        cache = self.make("demo")
        cache["https://x.com/a/status/1"] = ("text", ["data:image/jpeg;base64,AAA"])

        self.assertEqual(cache.get("https://x.com/a/status/1"), ["text", ["data:image/jpeg;base64,AAA"]])
        raw = self.redis.get("cmwgpt:cache:demo:https://x.com/a/status/1")
        self.assertEqual(json.loads(raw), ["text", ["data:image/jpeg;base64,AAA"]])

    def test_entries_expire_and_reads_refresh_the_ttl(self):
        cache = self.make("ttl", ttl_seconds=100)
        cache["k"] = "v"
        self.redis.expire("cmwgpt:cache:ttl:k", 5)

        self.assertEqual(cache.get("k"), "v")
        self.assertGreater(self.redis.ttl("cmwgpt:cache:ttl:k"), 50)

    def test_get_missing_returns_default_and_getitem_raises(self):
        cache = self.make("missing")
        self.assertIsNone(cache.get("nope"))
        self.assertEqual(cache.get("nope", "dflt"), "dflt")
        with self.assertRaises(KeyError):
            cache["nope"]

    def test_contains_and_delete(self):
        cache = self.make("delete")
        cache["key"] = "value"
        self.assertIn("key", cache)
        cache.delete("key")
        self.assertNotIn("key", cache)

    def test_non_string_keys_work(self):
        cache = self.make("ids")
        cache[1234] = "transcript"
        self.assertEqual(cache.get(1234), "transcript")

    def test_oversized_values_are_not_stored(self):
        with patch("src.utils.cache_utils.MAX_VALUE_BYTES", 10):
            cache = self.make("big")
            cache["k"] = "x" * 50
        self.assertIsNone(cache.get("k"))

    def test_without_redis_it_works_from_memory(self):
        def unavailable():
            raise RuntimeError("Real Redis client requested during IS_TESTING=True")

        cache = PersistentCache("offline", client_getter=unavailable)
        cache["k"] = "v"
        self.assertEqual(cache.get("k"), "v")
        self.assertIn("k", cache)
        cache.delete("k")
        self.assertNotIn("k", cache)

    def test_redis_errors_fall_back_to_memory_then_retry_later(self):
        broken = MagicMock()
        broken.set.side_effect = redis.ConnectionError("down")
        broken.getex.side_effect = redis.ConnectionError("down")
        cache = PersistentCache("flaky", client_getter=lambda: broken)

        with self.assertLogs("src.utils.cache_utils", level="WARNING"):
            cache["k"] = "v"
        self.assertEqual(cache.get("k"), "v")  # served from memory without touching Redis
        broken.getex.assert_not_called()

        with patch("src.utils.cache_utils.time.monotonic", return_value=cache._down_until + 1):
            broken.getex.side_effect = None
            broken.getex.return_value = json.dumps("from redis")
            self.assertEqual(cache.get("k"), "from redis")


class TestLRUCache(unittest.TestCase):
    def test_evicts_least_recently_used(self):
        cache = LRUCache(2)
        cache["a"] = 1
        cache["b"] = 2
        self.assertEqual(cache.get("a"), 1)
        cache["c"] = 3

        self.assertIn("a", cache)
        self.assertNotIn("b", cache)
        self.assertIn("c", cache)
        self.assertEqual(len(cache), 2)

    def test_overwrite_refreshes_recency(self):
        cache = LRUCache(2)
        cache["a"] = 1
        cache["b"] = 2
        cache["a"] = 10
        cache["c"] = 3

        self.assertEqual(cache.get("a"), 10)
        self.assertNotIn("b", cache)

    def test_get_missing_and_clear(self):
        cache = LRUCache(3)
        self.assertIsNone(cache.get("missing"))
        cache["a"] = 1
        cache.clear()
        self.assertEqual(len(cache), 0)
        self.assertNotIn("a", cache)


class TestSkipRecentFailures(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fake = fakeredis.FakeRedis(decode_responses=True)
        self.store = PersistentCache(
            "fetch_failures", ttl_seconds=cache_utils.FETCH_FAILURE_TTL_SECONDS, client_getter=lambda: fake
        )
        store_patcher = patch("src.utils.cache_utils._fetch_failures", self.store)
        store_patcher.start()
        self.addCleanup(store_patcher.stop)

    async def test_failure_is_remembered_then_expires(self):
        calls = []

        @cache_utils.skip_recent_failures("demo")
        async def fetch(url):
            calls.append(url)
            return None

        self.assertIsNone(await fetch("https://dead.example"))
        self.assertIsNone(await fetch("https://dead.example"))
        self.assertEqual(len(calls), 1)

        with patch("src.utils.cache_utils.time.time",
                   return_value=self.store.get("demo:https://dead.example") + 3601):
            await fetch("https://dead.example")
        self.assertEqual(len(calls), 2)

    def test_success_clears_failure_for_sync_fetcher(self):
        results = iter([None, "text"])

        @cache_utils.skip_recent_failures("demo")
        def fetch(url):
            return next(results)

        self.assertIsNone(fetch("u"))
        self.store["demo:u"] = 0  # pretend the failure is old
        self.assertEqual(fetch("u"), "text")
        self.assertNotIn("demo:u", self.store)

    async def test_cache_only_async_call_passes_through_and_never_records(self):
        calls = []

        @cache_utils.skip_recent_failures("demo")
        async def fetch(url, *, cache_only=False):
            calls.append(cache_only)
            return None

        self.assertIsNone(await fetch("u", cache_only=True))
        self.assertNotIn("demo:u", self.store)

        # Even a recorded failure doesn't short-circuit a cache_only lookup.
        self.store["demo:u"] = cache_utils.time.time()
        await fetch("u", cache_only=True)
        self.assertEqual(calls, [True, True])

    def test_cache_only_sync_call_passes_through_and_never_records(self):
        @cache_utils.skip_recent_failures("demo")
        def fetch(url, *, cache_only=False):
            return None

        self.assertIsNone(fetch("u", cache_only=True))
        self.assertNotIn("demo:u", self.store)


if __name__ == "__main__":
    unittest.main()
