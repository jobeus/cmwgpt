"""Tests for persistent cache helpers."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from src.utils.cache_utils import PersistentCache


class TestPersistentCache(unittest.TestCase):
    def test_loads_existing_cache_file(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("src.utils.cache_utils.CACHE_DIR", tmpdir):
            cache_path = Path(tmpdir) / "demo.json"
            cache_path.write_text(json.dumps({"a": 1}), encoding="utf-8")

            cache = PersistentCache("demo")

        self.assertEqual(cache.get("a"), 1)

    def test_invalid_cache_file_resets_to_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("src.utils.cache_utils.CACHE_DIR", tmpdir):
            cache_path = Path(tmpdir) / "bad.json"
            cache_path.write_text("{not-json", encoding="utf-8")

            cache = PersistentCache("bad")

        self.assertEqual(len(cache), 0)

    def test_setitem_evicts_oldest_entry_when_full(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("src.utils.cache_utils.CACHE_DIR", tmpdir):
            cache = PersistentCache("evict", max_size=2)
            cache["one"] = "1"
            cache["two"] = "2"
            cache["three"] = "3"

        self.assertNotIn("one", cache)
        self.assertEqual(cache["two"], "2")
        self.assertEqual(cache["three"], "3")

    def test_concurrent_writes_keep_cache_file_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("src.utils.cache_utils.CACHE_DIR", tmpdir):
            cache = PersistentCache("threads", max_size=500)

            def writer(prefix):
                for i in range(25):
                    cache[f"{prefix}-{i}"] = i

            threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            # The persisted file must be valid JSON containing every write.
            reloaded = PersistentCache("threads", max_size=500)
            leftover_tmp = [p for p in Path(tmpdir).iterdir() if p.suffix == ".tmp"]

        self.assertEqual(len(cache), 100)
        self.assertEqual(len(reloaded), 100)
        self.assertEqual(leftover_tmp, [])

    def test_delete_removes_key_and_persists(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("src.utils.cache_utils.CACHE_DIR", tmpdir):
            cache = PersistentCache("delete")
            cache["key"] = "value"
            cache.delete("key")
            reloaded = PersistentCache("delete")

        self.assertNotIn("key", reloaded)
