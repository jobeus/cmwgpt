"""Tests for the Redis-backed user memory store."""

import unittest
from datetime import datetime
from unittest.mock import patch

import fakeredis

from src.db.memory_store import MAX_PROFILE_BYTES, MemoryStore, cap_profile
from tests.config_helpers import cfg


class TestMemoryStore(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        self.store = MemoryStore(redis_getter=lambda: self.redis)

    async def test_one_key_per_user_round_trips(self):
        await self.store.save_profile(42, 1, "Stable: has two dogs")
        await self.store.save_profile(42, 2, "Style: short answers")

        self.assertEqual(await self.store.get(42, 1), "Stable: has two dogs")
        self.assertEqual(await self.store.get(43, 1), "")
        self.assertEqual(
            await self.store.get_many(42, [1, 2, 3, 1]), {1: "Stable: has two dogs", 2: "Style: short answers"}
        )
        self.assertEqual(await self.redis.get("cmwgpt:memory:42:1"), "Stable: has two dogs")
        self.assertEqual(await self.redis.ttl("cmwgpt:memory:42:1"), -1)  # never expires

    async def test_saving_again_replaces_the_profile(self):
        await self.store.save_profile(42, 1, "old")
        await self.store.save_profile(42, 1, "new")
        self.assertEqual(await self.store.get(42, 1), "new")

    async def test_empty_profile_and_clear_remove_the_key(self):
        await self.store.save_profile(42, 1, "something")
        await self.store.save_profile(42, 1, "")
        self.assertFalse(await self.redis.exists("cmwgpt:memory:42:1"))

        await self.store.save_profile(42, 1, "again")
        await self.store.clear_user(42, 1)
        self.assertEqual(await self.store.get(42, 1), "")

    async def test_profiles_are_capped_at_100kb(self):
        with self.assertLogs("src.db.memory_store", level="WARNING"):
            await self.store.save_profile(42, 1, "é" * MAX_PROFILE_BYTES)
        stored = await self.store.get(42, 1)
        self.assertLessEqual(len(stored.encode("utf-8")), MAX_PROFILE_BYTES)
        self.assertTrue(stored.startswith("éé"))

    def test_cap_profile_never_splits_a_character(self):
        capped = cap_profile("a" + "€" * MAX_PROFILE_BYTES)
        capped.encode("utf-8")  # valid UTF-8
        self.assertLessEqual(len(capped.encode("utf-8")), MAX_PROFILE_BYTES)
        self.assertEqual(cap_profile("short"), "short")

    async def test_clear_learned_profiles_only_touches_that_guild(self):
        await self.store.save_profile(42, 1, "a")
        await self.store.save_profile(42, 2, "b")
        await self.store.save_profile(7, 1, "other server")

        await self.store.clear_learned_profiles(42)

        self.assertEqual(await self.store.get_many(42, [1, 2]), {})
        self.assertEqual(await self.store.get(7, 1), "other server")

    async def test_job_state_round_trips(self):
        self.assertIsNone(await self.store.get_processed_through(42))
        when = datetime(2026, 10, 20, 8, 0)
        await self.store.set_job_state(42, when, "nightly: " + "x" * 400)

        self.assertEqual(await self.store.get_processed_through(42), when)
        self.assertEqual(len(await self.redis.hget("cmwgpt:memory_job:42", "last_status")), 255)

    async def test_channel_summaries_round_trip_and_clear_per_guild(self):
        await self.store.save_channel_summary(42, 5, "tacos (2026-10-15)")
        await self.store.save_channel_summary(43, 5, "other server")
        await self.store.save_profile(42, 1, "profile")
        self.assertEqual(await self.store.get_channel_summary(42, 5), "tacos (2026-10-15)")
        self.assertEqual(await self.store.get_channel_summary(42, 6), "")

        await self.store.clear_learned_profiles(42)
        self.assertEqual(await self.store.get_channel_summary(42, 5), "tacos (2026-10-15)")

        await self.store.clear_channel_summaries(42)
        self.assertEqual(await self.store.get_channel_summary(42, 5), "")
        self.assertEqual(await self.store.get_channel_summary(43, 5), "other server")
        self.assertEqual(await self.store.get(42, 1), "")

        await self.store.save_channel_summary(43, 5, "")
        self.assertFalse(await self.redis.exists("cmwgpt:channel_summary:43:5"))


class TestRedisClient(unittest.TestCase):
    def test_refuses_real_clients_under_tests(self):
        from src.db import redis_client

        with patch("src.config._cached_config", cfg(is_testing=True)), self.assertRaises(RuntimeError):
            redis_client.get_async_redis()
        with patch("src.config._cached_config", cfg(is_testing=True)), self.assertRaises(RuntimeError):
            redis_client.get_sync_redis()



if __name__ == "__main__":
    unittest.main()
