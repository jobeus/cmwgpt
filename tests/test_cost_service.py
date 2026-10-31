"""Tests for cost recording, scopes and the daily Redis tallies."""

import asyncio
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import fakeredis

from src.services import cost_service
from src.services.cost_service import (
    CLAUDE_DIRECT, GROQ, RUNPOD, CostStore, cost_day, cost_scope, record_cost, set_cost_store,
)


class TestRecordCost(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        self.store = CostStore(redis_getter=lambda: self.redis)
        set_cost_store(self.store)
        self.addCleanup(set_cost_store, None)

    async def test_scope_collects_costs_by_provider_including_from_child_tasks(self):
        with cost_scope(user_id=1, channel_id=2) as ledger:
            await record_cost(CLAUDE_DIRECT, 0.02)
            # A draw and a transcript running side by side, as tool calls do.
            await asyncio.gather(record_cost(RUNPOD, 0.04), record_cost(GROQ, 0.001))
            await record_cost(CLAUDE_DIRECT, 0.01)

        self.assertAlmostEqual(ledger.total, 0.071)
        self.assertAlmostEqual(ledger.by_provider[CLAUDE_DIRECT], 0.03)
        self.assertAlmostEqual(ledger.by_provider[RUNPOD], 0.04)

    async def test_saves_daily_totals_by_provider_user_and_channel(self):
        with cost_scope(user_id=1, channel_id=2):
            await record_cost(CLAUDE_DIRECT, 0.25)
        await record_cost(CLAUDE_DIRECT, 0.5)  # background work: provider only

        totals = await self.store.totals(cost_day(), cost_day())
        self.assertAlmostEqual(totals.providers[CLAUDE_DIRECT], 0.75)
        self.assertEqual(totals.users, {1: 0.25})
        self.assertEqual(totals.channels, {2: 0.25})
        self.assertGreater(await self.redis.ttl(f"cmwgpt:costs:{cost_day().isoformat()}"), 0)

    async def test_zero_missing_and_junk_costs_are_ignored(self):
        with cost_scope() as ledger:
            await record_cost(RUNPOD, 0)
            await record_cost(RUNPOD, None)
            await record_cost(RUNPOD, "lots")
        self.assertEqual(ledger.by_provider, {})
        self.assertEqual(await self.redis.keys("*"), [])

    async def test_string_costs_from_an_api_are_counted(self):
        with cost_scope() as ledger:
            await record_cost(RUNPOD, "0.04")
        self.assertAlmostEqual(ledger.total, 0.04)

    async def test_a_broken_store_never_raises(self):
        broken = MagicMock()
        broken.add.side_effect = RuntimeError("redis down")
        set_cost_store(broken)
        with cost_scope() as ledger, self.assertLogs("src.services.cost_service", level="WARNING"):
            await record_cost(RUNPOD, 0.04)
        self.assertAlmostEqual(ledger.total, 0.04)

    async def test_totals_add_up_a_range_of_days(self):
        await self.store.add(date(2026, 10, 1), RUNPOD, 1.0, 5, None)
        await self.store.add(date(2026, 10, 2), RUNPOD, 2.0, 5, None)
        await self.store.add(date(2026, 10, 3), GROQ, 4.0, 6, None)

        totals = await self.store.totals(date(2026, 10, 1), date(2026, 10, 2))
        self.assertEqual(totals.providers, {RUNPOD: 3.0})
        self.assertEqual(totals.top_users(5), [(5, 3.0)])


class TestCostDay(unittest.TestCase):
    def test_days_run_on_denver_time(self):
        # 03:00 UTC is still the previous evening in Denver.
        self.assertEqual(cost_day(datetime(2026, 10, 16, 3, 0, tzinfo=timezone.utc)), date(2026, 10, 15))

    def test_scope_is_reset_after_the_block(self):
        with cost_scope():
            self.assertIsNotNone(cost_service.current_ledger())
        self.assertIsNone(cost_service.current_ledger())


class TestStartupWiring(unittest.TestCase):
    def test_no_store_under_tests(self):
        from src.startup import create_services
        from tests.config_helpers import cfg

        with patch("src.services.cost_service._store", None):
            services = create_services(cfg(is_testing=True))
            self.assertIsNone(services.cost_store)
            self.assertIsNone(cost_service._store)


if __name__ == "__main__":
    unittest.main()
