"""Tests for the /cost report."""

import unittest
from datetime import date

import fakeredis

from src.bot.commands.cost import CostCommands, build_cost_commands, format_cost_report, format_providers
from src.services.cost_service import CLAUDE_DIRECT, GROQ, OPENROUTER, RUNPOD, CostStore, CostTotals


class TestFormatting(unittest.TestCase):
    def test_providers_are_biggest_first_and_free_ones_are_left_out(self):
        totals = CostTotals(providers={RUNPOD: 0.04, CLAUDE_DIRECT: 1.2, OPENROUTER: 0.0, GROQ: 0.0001})
        self.assertEqual(format_providers(totals), "claude-direct $1.200 · runpod $0.040")

    def test_report_skips_breakdowns_and_spenders_with_nothing_spent(self):
        report = format_cost_report(
            [("Today", CostTotals()), ("October", CostTotals(providers={RUNPOD: 0.5}))],
            [(1, 0.5), (2, 0.0)],
            "in October",
        )
        self.assertEqual(report, (
            "**What I've spent**\n"
            "**Today:** $0.000\n"
            "**October:** $0.500\n"
            "-# runpod $0.500\n"
            "**Top spenders in October:** <@1> $0.500"
        ))


class TestBuildReport(unittest.IsolatedAsyncioTestCase):
    async def test_reports_today_the_week_and_the_month(self):
        redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        store = CostStore(redis_getter=lambda: redis)
        await store.add(date(2026, 10, 30), CLAUDE_DIRECT, 0.30, 1, 10)
        await store.add(date(2026, 10, 30), RUNPOD, 0.12, 2, 10)
        await store.add(date(2026, 10, 26), OPENROUTER, 1.00, 1, 10)
        await store.add(date(2026, 10, 2), GROQ, 0.01, None, None)
        commands = CostCommands(bot=None, cost_store=store, today=lambda: date(2026, 10, 30))

        report = await commands.build_report()

        self.assertIn("**Today:** $0.420\n-# claude-direct $0.300 · runpod $0.120", report)
        self.assertIn("**Last 7 days:** $1.420\n-# openrouter $1.000 · claude-direct $0.300 · runpod $0.120", report)
        self.assertIn("**October:** $1.430", report)
        self.assertIn("groq $0.010", report)
        self.assertIn("**Top spenders in October:** <@1> $1.300, <@2> $0.120", report)

    async def test_redis_errors_get_a_short_apology(self):
        def unavailable():
            raise RuntimeError("down")

        commands = CostCommands(bot=None, cost_store=CostStore(redis_getter=unavailable),
                                today=lambda: date(2026, 10, 30))
        with self.assertLogs("src.bot.commands.cost", level="WARNING"):
            self.assertEqual(await commands.build_report(), "I couldn't read my spending totals right now.")

    def test_no_command_without_a_store(self):
        self.assertIsNone(build_cost_commands(bot=None, cost_store=None))


if __name__ == "__main__":
    unittest.main()
