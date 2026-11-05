"""Tests for StateStore: JSON values in Redis, the outage backoff and the legacy file import."""

import os
import tempfile
import unittest
from unittest.mock import patch

import fakeredis
import redis

from src.db.state_store import StateStore


class TestStateStore(unittest.TestCase):
    def setUp(self):
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.store = StateStore(lambda: self.redis)

    def test_values_and_hash_fields_round_trip_as_json(self):
        self.assertTrue(self.store.set("a", {"x": [1, 2]}, ttl_seconds=60))
        self.assertEqual(self.store.get("a"), {"x": [1, 2]})
        self.assertLessEqual(self.redis.ttl("cmwgpt:a"), 60)
        self.assertEqual(self.store.pop("a"), {"x": [1, 2]})
        self.assertIsNone(self.store.get("a"))

        self.store.set_field("h", 5, {"y": 1}, ttl_seconds=60)
        self.store.set_field("h", 6, "z")
        self.assertEqual(self.store.get_all("h"), {"5": {"y": 1}, "6": "z"})
        self.store.delete_field("h", 5)
        self.assertEqual(self.store.get_all("h"), {"6": "z"})

    def test_values_that_are_not_json_are_ignored(self):
        self.redis.set("cmwgpt:a", "{oops")
        self.redis.hset("cmwgpt:h", "1", "{oops")
        with self.assertLogs("src.db.state_store", "WARNING"):
            self.assertIsNone(self.store.get("a"))
            self.assertEqual(self.store.get_all("h"), {})

    def test_without_redis_nothing_is_saved(self):
        store = StateStore(None)
        self.assertFalse(store.set("a", 1))
        self.assertIsNone(store.get("a"))
        self.assertEqual(store.get_all("h"), {})

    def test_redis_errors_back_off_instead_of_retrying_every_call(self):
        calls = []

        def down():
            calls.append(1)
            raise redis.ConnectionError("refused")

        store = StateStore(down)
        with self.assertLogs("src.db.state_store", "WARNING") as logs:
            self.assertFalse(store.set("a", 1))
            self.assertIsNone(store.get("a"))
        self.assertEqual((len(calls), len(logs.output)), (1, 1))

        with patch("src.db.state_store.time.monotonic", return_value=10**9):
            with self.assertLogs("src.db.state_store", "WARNING"):
                store.get("a")
        self.assertEqual(len(calls), 2)

    def test_legacy_file_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            self.assertIsNone(self.store.import_legacy_file(path, "k"))

            with open(path, "w", encoding="utf-8") as f:
                f.write("{bad json")
            with self.assertLogs("src.db.state_store", "ERROR"):
                self.assertIsNone(self.store.import_legacy_file(path, "k"))
            self.assertTrue(os.path.exists(path))

            with open(path, "w", encoding="utf-8") as f:
                f.write('{"a": 1}')
            self.redis.set("cmwgpt:k", "1")
            self.assertIsNone(self.store.import_legacy_file(path, "k"))  # Redis already has it
            self.redis.delete("cmwgpt:k")
            self.assertEqual(self.store.import_legacy_file(path, "k"), {"a": 1})
            self.assertFalse(os.path.exists(path))
            self.assertTrue(os.path.exists(path + ".migrated"))


if __name__ == "__main__":
    unittest.main()
