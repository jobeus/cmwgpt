"""Tests for the combined Polymarket + Kalshi lookup and the ``prediction_markets`` chat tool."""

import unittest
from unittest.mock import AsyncMock, patch

from src.services import prediction_markets as pmk
from src.services.chat_tools import build_mention_tools
from src.utils.market_format import MarketDataError


def _patch_sites(polymarket, kalshi):
    def as_mock(value):
        return AsyncMock(side_effect=value) if isinstance(value, Exception) else AsyncMock(return_value=value)
    return (patch.object(pmk, "polymarket_lookup", as_mock(polymarket)),
            patch.object(pmk, "kalshi_lookup", as_mock(kalshi)))


class TestPredictionMarketLookup(unittest.IsolatedAsyncioTestCase):
    async def _lookup(self, query, polymarket="", kalshi="", status="active"):
        poly_patch, kalshi_patch = _patch_sites(polymarket, kalshi)
        with poly_patch as poly, kalshi_patch as kal:
            text = await pmk.prediction_market_lookup(query, status)
        return text, poly, kal

    async def test_keywords_ask_both_sites_and_join_the_results(self):
        text, poly, kal = await self._lookup("fed rate", "POLY RESULTS", "KALSHI RESULTS", "closed")
        poly.assert_awaited_once_with("fed rate", "closed")
        kal.assert_awaited_once_with("fed rate", "closed")
        self.assertEqual(text, "POLY RESULTS\n\nKALSHI RESULTS")

    async def test_one_empty_site_is_noted(self):
        text, _, _ = await self._lookup("fed rate", "POLY RESULTS", "")
        self.assertEqual(text, 'POLY RESULTS\n\nKalshi: no active events match "fed rate".')

    async def test_nothing_anywhere_is_empty(self):
        text, _, _ = await self._lookup("zzqq")
        self.assertEqual(text, "")

    async def test_one_failing_site_does_not_hide_the_other(self):
        text, _, _ = await self._lookup("fed", MarketDataError("Polymarket answered HTTP 503."), "KALSHI")
        self.assertEqual(text, "Polymarket: lookup failed (Polymarket answered HTTP 503.)\n\nKALSHI")
        text, _, _ = await self._lookup("fed", "POLY", RuntimeError("bug"))
        self.assertEqual(text, "POLY\n\nKalshi: lookup failed")

    async def test_every_site_failing_raises(self):
        with self.assertRaises(MarketDataError):
            await self._lookup("fed", MarketDataError("a"), MarketDataError("b"))

    async def test_links_only_go_to_their_own_site(self):
        text, poly, kal = await self._lookup("https://polymarket.com/event/x-y", "POLY", "KALSHI")
        self.assertEqual(text, "POLY")
        kal.assert_not_awaited()
        text, poly, kal = await self._lookup("see kalshi.com/markets/kxsb", "POLY", "KALSHI")
        self.assertEqual(text, "KALSHI")
        poly.assert_not_awaited()


class TestPredictionMarketsTool(unittest.IsolatedAsyncioTestCase):
    def _tool(self):
        tools = build_mention_tools(
            guild_id=None, channel_id=1, user_id=2, user_notes_provider=None, how_i_work_loader=lambda: "",
            state_service=None, gemini_service=None, default_image_model=None,
        )
        return {t.definition["name"]: t for t in tools}["prediction_markets"]

    @patch("src.services.chat_tools.prediction_market_lookup", return_value="odds")
    async def test_passes_query_and_status(self, lookup):
        result = await self._tool().run({"query": " bitcoin ", "status": "closed"})
        lookup.assert_awaited_once_with("bitcoin", "closed")
        self.assertEqual(result.content[0]["text"], "odds")

    @patch("src.services.chat_tools.prediction_market_lookup", return_value="")
    async def test_no_match_is_an_error(self, _lookup):
        result = await self._tool().run({"query": "zzqq", "status": "active"})
        self.assertTrue(result.is_error)
        self.assertIn("Polymarket or Kalshi", result.content[0]["text"])

    @patch("src.services.chat_tools.prediction_market_lookup", side_effect=MarketDataError("both down"))
    async def test_every_site_failing_is_an_error(self, _lookup):
        result = await self._tool().run({"query": "bitcoin", "status": "active"})
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0]["text"], "both down")

    async def test_empty_query_is_an_error(self):
        self.assertTrue((await self._tool().run({"query": " ", "status": "active"})).is_error)


if __name__ == "__main__":
    unittest.main()
