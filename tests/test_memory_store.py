"""Tests for the Redis-backed user memory store and its one-time MariaDB import."""

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis

from src.db.memory_store import MAX_PROFILE_BYTES, MIGRATED_KEY, MemoryStore, cap_profile
from tests.config_helpers import cfg


def fake_pool(profiles, job_states):
    """A MariaDB pool whose cursor returns ``profiles`` then ``job_states``."""
    cur = MagicMock()
    cur.execute = AsyncMock()
    cur.fetchall = AsyncMock(side_effect=[profiles, job_states])
    cur_ctx = MagicMock()
    cur_ctx.__aenter__ = AsyncMock(return_value=cur)
    cur_ctx.__aexit__ = AsyncMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur_ctx
    conn_ctx = MagicMock()
    conn_ctx.__aenter__ = AsyncMock(return_value=conn)
    conn_ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = conn_ctx
    return pool


class TestMemoryStore(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        self.pool_getter = AsyncMock()
        self.store = MemoryStore(redis_getter=lambda: self.redis, mariadb_pool_getter=self.pool_getter)

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

    async def test_migrates_mariadb_once_without_overwriting_redis(self):
        await self.store.save_profile(42, 2, "written by a nightly run on Redis")
        self.pool_getter.return_value = fake_pool(
            [(42, 1, "Stable: has two dogs"), (42, 2, "stale mariadb copy"), (7, 3, "x" * (MAX_PROFILE_BYTES + 5))],
            [(42, datetime(2026, 10, 19, 8, 0), datetime(2026, 10, 19, 8, 5), "nightly: ok")],
        )

        with patch("src.config._cached_config", cfg(db_password="secret")):
            await self.store.migrate_from_mariadb()
            await self.store.migrate_from_mariadb()

        self.pool_getter.assert_awaited_once()
        self.assertEqual(await self.store.get(42, 1), "Stable: has two dogs")
        self.assertEqual(await self.store.get(42, 2), "written by a nightly run on Redis")
        self.assertEqual(len(await self.store.get(7, 3)), MAX_PROFILE_BYTES)
        self.assertEqual(await self.store.get_processed_through(42), datetime(2026, 10, 19, 8, 0))
        self.assertTrue(await self.redis.exists(MIGRATED_KEY))

    async def test_migration_retries_next_start_when_mariadb_is_down(self):
        self.pool_getter.side_effect = RuntimeError("MariaDB is unavailable")

        with patch("src.config._cached_config", cfg(db_password="secret")):
            with self.assertLogs("src.db.memory_store", level="WARNING"):
                await self.store.migrate_from_mariadb()

        self.assertFalse(await self.redis.exists(MIGRATED_KEY))

    async def test_migration_is_skipped_without_mariadb_configured(self):
        with patch("src.config._cached_config", cfg(db_password="")):
            await self.store.migrate_from_mariadb()

        self.pool_getter.assert_not_awaited()
        self.assertTrue(await self.redis.exists(MIGRATED_KEY))

    async def test_migration_never_raises_when_redis_is_down(self):
        broken = MagicMock()
        broken.exists = AsyncMock(side_effect=ConnectionError("refused"))
        store = MemoryStore(redis_getter=lambda: broken, mariadb_pool_getter=self.pool_getter)

        with self.assertLogs("src.db.memory_store", level="ERROR"):
            await store.migrate_from_mariadb()
        self.pool_getter.assert_not_awaited()


class TestRedisClient(unittest.TestCase):
    def test_refuses_real_clients_under_tests(self):
        from src.db import redis_client

        with patch("src.config._cached_config", cfg(is_testing=True)), self.assertRaises(RuntimeError):
            redis_client.get_async_redis()
        with patch("src.config._cached_config", cfg(is_testing=True)), self.assertRaises(RuntimeError):
            redis_client.get_sync_redis()


if __name__ == "__main__":
    unittest.main()
