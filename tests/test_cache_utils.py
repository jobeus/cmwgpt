"""Tests for persistent cache helpers."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from src.utils import cache_utils
from src.utils.cache_utils import LRUCache, PersistentCache


class TestPersistentCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        patcher = patch("src.utils.cache_utils.CACHE_DIR", self.tmpdir)
        patcher.start()
        self.caches = []

        def cleanup():
            # Flush (and cancel) pending timers before the directory goes away.
            for cache in self.caches:
                cache.flush()
            patcher.stop()
            self._tmp.cleanup()

        self.addCleanup(cleanup)

    def make(self, name, **kwargs):
        cache = PersistentCache(name, **kwargs)
        self.caches.append(cache)
        return cache

    def test_loads_existing_cache_file(self):
        (Path(self.tmpdir) / "demo.json").write_text(json.dumps({"a": 1}), encoding="utf-8")

        cache = self.make("demo")

        self.assertEqual(cache.get("a"), 1)

    def test_invalid_cache_file_resets_to_empty_and_stays_usable(self):
        (Path(self.tmpdir) / "bad.json").write_text("{not-json", encoding="utf-8")

        cache = self.make("bad")
        self.assertEqual(len(cache), 0)

        cache["k"] = "v"
        self.assertEqual(cache.get("k"), "v")

    def test_setitem_evicts_least_recently_used_entry(self):
        cache = self.make("evict", max_size=2)
        cache["one"] = "1"
        cache["two"] = "2"
        cache["three"] = "3"

        self.assertNotIn("one", cache)
        self.assertEqual(cache["two"], "2")
        self.assertEqual(cache["three"], "3")

    def test_get_marks_entry_recently_used(self):
        cache = self.make("lru_get", max_size=2)
        cache["one"] = "1"
        cache["two"] = "2"
        self.assertEqual(cache.get("one"), "1")
        cache["three"] = "3"

        self.assertIn("one", cache)
        self.assertNotIn("two", cache)

    def test_getitem_marks_entry_recently_used(self):
        cache = self.make("lru_getitem", max_size=2)
        cache["one"] = "1"
        cache["two"] = "2"
        self.assertEqual(cache["one"], "1")
        cache["three"] = "3"

        self.assertIn("one", cache)
        self.assertNotIn("two", cache)

    def test_get_missing_returns_default(self):
        cache = self.make("missing")
        self.assertIsNone(cache.get("nope"))
        self.assertEqual(cache.get("nope", "dflt"), "dflt")

    def test_saves_are_batched_until_timer_or_flush(self):
        with patch("src.utils.cache_utils.SAVE_DELAY_SECONDS", 3600):
            cache = self.make("batched")
            cache["a"] = 1
            cache["b"] = 2

            path = Path(self.tmpdir) / "batched.json"
            self.assertFalse(path.exists())
            timer = cache._save_timer
            self.assertIsNotNone(timer)

            cache["c"] = 3
            self.assertIs(cache._save_timer, timer)  # one timer for the burst

            cache.flush()

        self.assertIsNone(cache._save_timer)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1, "b": 2, "c": 3})

    def test_timer_writes_after_delay(self):
        with patch("src.utils.cache_utils.SAVE_DELAY_SECONDS", 0.01):
            cache = self.make("timed")
            cache["a"] = 1
            timer = cache._save_timer
        timer.join(timeout=2)

        path = Path(self.tmpdir) / "timed.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1})
        self.assertIsNone(cache._save_timer)

    def test_flush_without_pending_changes_does_not_write(self):
        cache = self.make("idle")
        cache.flush()
        self.assertFalse((Path(self.tmpdir) / "idle.json").exists())

    def test_flush_all_caches_writes_every_live_cache(self):
        with patch("src.utils.cache_utils.SAVE_DELAY_SECONDS", 3600):
            first = self.make("all_one")
            second = self.make("all_two")
            first["x"] = 1
            second["y"] = 2

            cache_utils.flush_all_caches()

        self.assertEqual(json.loads((Path(self.tmpdir) / "all_one.json").read_text()), {"x": 1})
        self.assertEqual(json.loads((Path(self.tmpdir) / "all_two.json").read_text()), {"y": 2})

    def test_concurrent_writes_keep_cache_file_valid(self):
        cache = self.make("threads", max_size=500)

        def writer(prefix):
            for i in range(25):
                cache[f"{prefix}-{i}"] = i

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        cache.flush()

        # The persisted file must be valid JSON containing every write.
        reloaded = self.make("threads", max_size=500)
        leftover_tmp = [p for p in Path(self.tmpdir).iterdir() if p.suffix == ".tmp"]

        self.assertEqual(len(cache), 100)
        self.assertEqual(len(reloaded), 100)
        self.assertEqual(leftover_tmp, [])

    def test_delete_removes_key_and_persists(self):
        cache = self.make("delete")
        cache["key"] = "value"
        cache.delete("key")
        cache.flush()
        reloaded = self.make("delete")

        self.assertNotIn("key", reloaded)

    def test_reload_preserves_lru_order(self):
        cache = self.make("order", max_size=2)
        cache["one"] = 1
        cache["two"] = 2
        cache.get("one")  # "two" is now least recently used
        cache.flush()

        reloaded = self.make("order", max_size=2)
        reloaded["three"] = 3

        self.assertIn("one", reloaded)
        self.assertNotIn("two", reloaded)


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
        self._tmp = tempfile.TemporaryDirectory()
        patcher = patch("src.utils.cache_utils.CACHE_DIR", self._tmp.name)
        patcher.start()
        self.store = PersistentCache("fetch_failures")
        store_patcher = patch("src.utils.cache_utils._fetch_failures", self.store)
        store_patcher.start()

        def cleanup():
            store_patcher.stop()
            self.store.flush()
            patcher.stop()
            self._tmp.cleanup()

        self.addCleanup(cleanup)

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
