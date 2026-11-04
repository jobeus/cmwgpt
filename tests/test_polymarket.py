"""Tests for the Polymarket lookups behind the ``polymarket`` chat tool."""

import json
import unittest
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

from src.services import polymarket_service as pm
from src.services.chat_tools import build_mention_tools
from src.utils.market_format import MarketDataError


def _market(label: str, yes: float, *, volume: float = 1000.0, closed: bool = False, **extra: Any) -> Dict[str, Any]:
    market = {
        "question": f"Will {label} win?",
        "groupItemTitle": label,
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps([str(yes), str(round(1 - yes, 4))]),
        "volumeNum": volume,
        "closed": closed,
        "oneDayPriceChange": 0.02,
        "bestBid": yes - 0.01,
        "bestAsk": yes + 0.01,
        "clobTokenIds": json.dumps([f"tok-{label}", f"tok-{label}-no"]),
    }
    market.update(extra)
    return market


ELECTION = {
    "title": "Presidential Election Winner 2028",
    "slug": "presidential-election-winner-2028",
    "volume": 728_266_974,
    "volume24hr": 1_400_000,
    "liquidity": 51_600_000,
    "endDate": "2028-11-08T00:00:00Z",
    "closed": False,
    "description": "Resolves to the winner of the 2028 election.",
    "markets": [
        _market("Gavin Newsom", 0.117),
        _market("JD Vance", 0.175),
        _market("Person A", 0.5, volume=0),
        _market("Kamala Harris", 0.0, closed=True, outcomePrices=json.dumps(["0", "1"])),
    ],
}


class FakeApi:
    """Stands in for ``_get``, answering by URL and recording each call."""

    def __init__(self, routes: Dict[str, Any]):
        self.routes = routes
        self.calls: List[Tuple[str, Optional[Dict[str, Any]]]] = []

    async def __call__(self, _client, url, params=None):
        self.calls.append((url, params))
        for suffix, answer in self.routes.items():
            if url.endswith(suffix):
                return answer(params) if callable(answer) else answer
        return None


class TestPolymarketLookup(unittest.IsolatedAsyncioTestCase):
    async def _lookup(self, routes, query, status="active"):
        api = FakeApi(routes)
        with patch.object(pm, "_get", api):
            return await pm.polymarket_lookup(query, status), api

    async def test_search_lists_events_by_volume_with_likeliest_outcomes_first(self):
        routes = {"/public-search": {"events": [ELECTION], "pagination": {"totalResults": 43}}}
        text, api = await self._lookup(routes, "2028 election")
        params = api.calls[0][1]
        self.assertEqual(params["q"], "2028 election")
        self.assertEqual(params["events_status"], "active")
        self.assertEqual(params["sort"], "volume")
        self.assertIn("43 matches", text)
        self.assertIn("$728.3M", text)
        self.assertIn("https://polymarket.com/event/presidential-election-winner-2028", text)
        self.assertLess(text.index("JD Vance: Yes 17.5%"), text.index("Gavin Newsom: Yes 11.7%"))
        self.assertLess(text.index("Gavin Newsom"), text.index("Kamala Harris: closed, resolved No"))
        self.assertNotIn("Person A", text)  # untraded placeholder
        self.assertIn("24h +2.0pts", text)

    async def test_status_all_searches_without_a_status_filter(self):
        _, api = await self._lookup({"/public-search": {"events": [ELECTION]}}, "election", "all")
        self.assertNotIn("events_status", api.calls[0][1])

    async def test_no_match_is_empty(self):
        text, _ = await self._lookup({"/public-search": {"events": []}}, "zzqq")
        self.assertEqual(text, "")

    async def test_link_describes_the_event_with_trends_and_rules(self):
        now = 1_800_000_000
        history = {"history": [{"t": now - 20 * 86400, "p": 0.10}, {"t": now - 6 * 86400, "p": 0.15},
                               {"t": now, "p": 0.175}]}
        routes = {"/events/slug/presidential-election-winner-2028": ELECTION, "/prices-history": history}
        text, api = await self._lookup(
            routes, "what are the odds https://polymarket.com/event/presidential-election-winner-2028?tid=1")
        self.assertIn("Price over the last 30 days:", text)
        self.assertIn("JD Vance (Yes): 10.0% on", text)
        self.assertIn("15.0% a week ago → 17.5% now (range 10.0%–17.5%)", text)
        self.assertIn("Resolution rules: Resolves to the winner", text)
        trend_tokens = [p["market"] for url, p in api.calls if url.endswith("/prices-history")]
        self.assertEqual(trend_tokens, ["tok-JD Vance", "tok-Gavin Newsom"])  # open, traded markets only

    async def test_market_slug_finds_its_event(self):
        routes = {
            "/events/slug/presidential-election-winner-2028": ELECTION,
            "/markets/slug/will-jd-vance-win-2028": {"events": [{"slug": "presidential-election-winner-2028"}]},
        }
        text, _ = await self._lookup(routes, "will-jd-vance-win-2028")
        self.assertTrue(text.startswith("Presidential Election Winner 2028"))

    async def test_unknown_slug_falls_back_to_search(self):
        routes = {"/public-search": {"events": [ELECTION]}}
        text, api = await self._lookup(routes, "fed-rates")
        self.assertEqual(api.calls[-1][1]["q"], "fed-rates")
        self.assertIn("Polymarket events matching", text)

    async def test_non_binary_and_resolved_markets(self):
        game = {"title": "Lakers vs Celtics", "slug": "nba-lal-bos", "volume": 5000, "closed": False, "markets": [
            _market("x", 0.55, outcomes=json.dumps(["Lakers", "Celtics"]), question="Lakers vs Celtics"),
        ]}
        text, _ = await self._lookup({"/public-search": {"events": [game]}}, "lakers")
        self.assertIn("Lakers vs Celtics: Lakers 55.0% · Celtics 45.0%", text)


class TestPolymarketTool(unittest.IsolatedAsyncioTestCase):
    def _tool(self):
        tools = build_mention_tools(
            guild_id=None, channel_id=1, user_id=2, user_notes_provider=None, how_i_work_loader=lambda: "",
            state_service=None, gemini_service=None, default_image_model=None,
        )
        return {t.definition["name"]: t for t in tools}["polymarket"]

    @patch("src.services.chat_tools.polymarket_lookup", return_value="odds")
    async def test_passes_query_and_status(self, lookup):
        result = await self._tool().run({"query": " bitcoin ", "status": "closed"})
        lookup.assert_awaited_once_with("bitcoin", "closed")
        self.assertEqual(result.content[0]["text"], "odds")

    @patch("src.services.chat_tools.polymarket_lookup", return_value="")
    async def test_no_match_is_an_error(self, _lookup):
        result = await self._tool().run({"query": "zzqq", "status": "active"})
        self.assertTrue(result.is_error)

    @patch("src.services.chat_tools.polymarket_lookup", side_effect=MarketDataError("down"))
    async def test_api_failure_is_an_error(self, _lookup):
        result = await self._tool().run({"query": "bitcoin", "status": "active"})
        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0]["text"], "down")


if __name__ == "__main__":
    unittest.main()
