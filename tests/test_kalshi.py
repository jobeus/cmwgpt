"""Tests for the Kalshi lookups behind the ``kalshi`` chat tool."""

import unittest
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

from src.services import kalshi_service as ks
from src.services.chat_tools import build_mention_tools
from src.utils.market_format import MarketDataError


def _search_market(name: str, cents: int, *, result: str = "", volume: int = 1000, delta: int = 0) -> Dict[str, Any]:
    return {"yes_subtitle": name, "last_price": cents, "yes_bid": cents - 1, "yes_ask": cents + 1,
            "price_delta": delta, "volume": volume, "result": result}


SEARCH_EVENT = {
    "event_title": "2028 U.S. Presidential Election winner?",
    "event_subtitle": "In 2028",
    "event_ticker": "KXPRESPERSON-28",
    "series_ticker": "KXPRESPERSON",
    "total_volume": 80_786_644,
    "active_market_count": 3,
    "markets": [
        _search_market("Gavin Newsom", 13, delta=-1),
        _search_market("J.D. Vance", 18, delta=-2),
        _search_market("Nobody", 0, volume=0),
        _search_market("Kamala Harris", 0, result="no"),
    ],
}


def _v2_market(name: str, price: str, *, result: str = "", **extra: Any) -> Dict[str, Any]:
    market = {
        "ticker": f"KXPRESPERSON-28-{name[:4].upper()}", "yes_sub_title": name, "last_price_dollars": price,
        "yes_bid_dollars": price, "yes_ask_dollars": price, "previous_price_dollars": "0.1000",
        "volume_fp": "1000.00", "volume_24h_fp": "50.00", "open_interest_fp": "500.00", "result": result,
        "expected_expiration_time": "2029-01-21T15:00:00Z", "rules_primary": f"If {name} wins, Yes.",
    }
    market.update(extra)
    return market


V2_EVENT = {
    "event": {
        "event_ticker": "KXPRESPERSON-28", "series_ticker": "KXPRESPERSON",
        "title": "2028 U.S. Presidential Election winner?", "sub_title": "In 2028",
        "markets": [_v2_market("Gavin Newsom", "0.1300"), _v2_market("Vance", "0.1800")],
    }
}


class FakeApi:
    def __init__(self, routes: Dict[str, Any]):
        self.routes = routes
        self.calls: List[Tuple[str, Optional[Dict[str, Any]]]] = []

    async def __call__(self, _client, url, params=None):
        self.calls.append((url, params))
        for fragment, answer in self.routes.items():
            if fragment in url:
                return answer
        return None


class TestKalshiLookup(unittest.IsolatedAsyncioTestCase):
    async def _lookup(self, routes, query, status="active"):
        api = FakeApi(routes)
        with patch.object(ks, "_get", api):
            return await ks.kalshi_lookup(query, status), api

    async def test_search_lists_matching_events_with_likeliest_first(self):
        routes = {"/v1/search/series": {"current_page": [SEARCH_EVENT], "total_results_count": 240}}
        text, api = await self._lookup(routes, "2028 election")
        params = api.calls[0][1]
        self.assertEqual((params["query"], params["order_by"], params["status"]), ("2028 election", "querymatch", "open"))
        self.assertIn("240 matches", text)
        self.assertIn("https://kalshi.com/markets/kxpresperson · event KXPRESPERSON-28", text)
        self.assertIn("volume 80.8M contracts", text)
        self.assertLess(text.index("J.D. Vance: Yes 18.0% · 24h -2.0pts"), text.index("Gavin Newsom: Yes 13.0%"))
        self.assertLess(text.index("Gavin Newsom"), text.index("Kamala Harris: settled No"))
        self.assertNotIn("Nobody", text)

    async def test_closed_searches_settled_and_all_has_no_filter(self):
        _, api = await self._lookup({"/v1/search/series": {"current_page": [SEARCH_EVENT]}}, "x", "closed")
        self.assertEqual(api.calls[0][1]["status"], "settled")
        _, api = await self._lookup({"/v1/search/series": {"current_page": [SEARCH_EVENT]}}, "x", "all")
        self.assertNotIn("status", api.calls[0][1])

    async def test_no_match_is_empty(self):
        text, _ = await self._lookup({"/v1/search/series": {"current_page": []}}, "zzqq")
        self.assertEqual(text, "")

    async def test_link_describes_the_event_with_trends_and_rules(self):
        now = 1_800_000_000
        candles = {"candlesticks": [
            {"end_period_ts": now - 20 * 86400, "price": {"close_dollars": "0.1000"}},
            {"end_period_ts": now - 6 * 86400, "price": {"close_dollars": "0.1500"}},
            {"end_period_ts": now, "price": {"close_dollars": "0.1800"}},
        ]}
        routes = {"/candlesticks": candles, "/events/KXPRESPERSON-28": V2_EVENT}
        text, api = await self._lookup(routes, "https://kalshi.com/markets/kxpresperson/pres-person/kxpresperson-28")
        self.assertIn("volume 2,000 contracts · 24h 100 contracts · open interest 1,000 contracts", text)
        self.assertIn("expected to resolve 2029-01-21", text)
        self.assertLess(text.index("Vance: Yes 18.0% · 24h +8.0pts"), text.index("Gavin Newsom: Yes 13.0%"))
        self.assertIn("Vance: 10.0% on", text)
        self.assertIn("15.0% a week ago → 18.0% at the latest daily close", text)
        self.assertIn("Resolution rules (first outcome): If Vance wins, Yes.", text)
        candle_urls = [url for url, _ in api.calls if url.endswith("/candlesticks")]
        self.assertTrue(all("/series/KXPRESPERSON/markets/" in url for url in candle_urls))

    async def test_market_ticker_finds_its_event(self):
        routes = {
            "/events/KXPRESPERSON-28": V2_EVENT,
            "/markets/KXPRESPERSON-28-JVAN": {"market": {"event_ticker": "KXPRESPERSON-28"}},
        }
        api = FakeApi(routes)

        async def get(client, url, params=None):
            if url.endswith("/events/KXPRESPERSON-28-JVAN"):  # not an event ticker
                return None
            return await api(client, url, params)

        with patch.object(ks, "_get", get):
            text = await ks.kalshi_lookup("kxpresperson-28-jvan")
        self.assertTrue(text.startswith("2028 U.S. Presidential Election winner? (In 2028)"))

    async def test_series_link_lists_only_that_series(self):
        other = dict(SEARCH_EVENT, series_ticker="KXSBUXCC", event_title="Starbucks spend")
        sb = dict(SEARCH_EVENT, series_ticker="KXSB", event_title="2027 NFL Champion")
        routes = {"/v1/search/series": {"current_page": [sb, other], "total_results_count": 78}}
        text, _ = await self._lookup(routes, "https://kalshi.com/markets/kxsb")
        self.assertIn("Kalshi series KXSB", text)
        self.assertIn("2027 NFL Champion", text)
        self.assertNotIn("Starbucks", text)


class TestKalshiTool(unittest.IsolatedAsyncioTestCase):
    def _tool(self):
        tools = build_mention_tools(
            guild_id=None, channel_id=1, user_id=2, user_notes_provider=None, how_i_work_loader=lambda: "",
            state_service=None, gemini_service=None, default_image_model=None,
        )
        return {t.definition["name"]: t for t in tools}["kalshi"]

    @patch("src.services.chat_tools.kalshi_lookup", return_value="odds")
    async def test_passes_query_and_status(self, lookup):
        result = await self._tool().run({"query": " fed ", "status": "closed"})
        lookup.assert_awaited_once_with("fed", "closed")
        self.assertEqual(result.content[0]["text"], "odds")

    @patch("src.services.chat_tools.kalshi_lookup", return_value="")
    async def test_no_match_is_an_error(self, _lookup):
        result = await self._tool().run({"query": "zzqq", "status": "active"})
        self.assertTrue(result.is_error)
        self.assertIn("Kalshi", result.content[0]["text"])

    @patch("src.services.chat_tools.kalshi_lookup", side_effect=MarketDataError("down"))
    async def test_api_failure_is_an_error(self, _lookup):
        result = await self._tool().run({"query": "fed", "status": "active"})
        self.assertTrue(result.is_error)


if __name__ == "__main__":
    unittest.main()
