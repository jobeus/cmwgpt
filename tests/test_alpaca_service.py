"""Tests for the Alpaca client and the ``market_data`` tool's lookups."""

import json
import unittest
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List

import httpx

from src.services import alpaca_service as alpaca
from src.services.alpaca_service import AlpacaClient, AlpacaError

TODAY = date(2026, 11, 12)

AAPL = {
    "latestTrade": {"p": 200.0, "s": 100, "t": "2026-11-12T20:59:59.123456789Z"},
    "latestQuote": {"bp": 199.9, "ap": 200.1, "t": "2026-11-12T20:59:59Z"},
    "dailyBar": {"o": 198.0, "h": 201.5, "l": 197.5, "c": 200.0, "v": 1_500_000},
    "prevDailyBar": {"c": 196.0},
}


def _option(bid: float, ask: float) -> Dict[str, Any]:
    return {"latestQuote": {"bp": bid, "ap": ask}, "latestTrade": {"p": bid}, "impliedVolatility": 0.31,
            "greeks": {"delta": 0.52}}


class _Api:
    """A stand-in for Alpaca: answers by path and records every request."""

    def __init__(self, routes: Dict[str, Any]):
        self.routes = routes
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get(request.url.path)
        if answer is None:
            return httpx.Response(404, json={"message": "not found"})
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)


def _client(routes: Dict[str, Any], clock: List[float]) -> tuple[AlpacaClient, _Api]:
    api = _Api(routes)
    return AlpacaClient("key", "secret", transport=httpx.MockTransport(api), monotonic=lambda: clock[0]), api


class TestSymbols(unittest.TestCase):
    def test_option_symbols_are_read(self):
        contract = alpaca.parse_option("AAPL261120C00230000")
        assert contract is not None
        self.assertEqual((contract.underlying, contract.expiration, contract.kind, contract.strike),
                         ("AAPL", date(2026, 11, 20), "call", Decimal("230")))
        self.assertEqual(contract.describe(), "AAPL $230 call exp 2026-11-20")
        self.assertEqual(alpaca.parse_option("SPY261120P00612500").strike, Decimal("612.5"))  # type: ignore[union-attr]

    def test_stocks_and_junk_are_not_options(self):
        self.assertIsNone(alpaca.parse_option("AAPL"))
        self.assertIsNone(alpaca.parse_option("AAPL269999C00230000"))
        self.assertTrue(alpaca.is_stock_symbol("BRK.B"))
        self.assertFalse(alpaca.is_stock_symbol("AAPL261120C00230000"))
        self.assertEqual(alpaca.clean_symbol(" $aapl "), "AAPL")


class TestClient(unittest.IsolatedAsyncioTestCase):
    async def test_quotes_are_cached_for_a_minute(self):
        clock = [0.0]
        client, api = _client({"/v2/stocks/snapshots": {"AAPL": AAPL}}, clock)
        await client.stock_snapshots(["AAPL"])
        clock[0] = 59.0
        await client.stock_snapshots(["AAPL"])
        self.assertEqual(len(api.requests), 1)
        clock[0] = 61.0
        await client.stock_snapshots(["AAPL"])
        self.assertEqual(len(api.requests), 2)
        self.assertEqual(api.requests[0].headers["APCA-API-KEY-ID"], "key")

    async def test_orders_only_go_to_the_paper_account(self):
        client, api = _client({"/v2/orders": {"id": "abc", "status": "accepted"}}, [0.0])
        await client.submit_order({"symbol": "AAPL", "qty": "1", "side": "buy"})
        self.assertEqual(api.requests[0].url.host, "paper-api.alpaca.markets")
        self.assertEqual(json.loads(api.requests[0].content)["symbol"], "AAPL")

    async def test_a_refusal_carries_alpacas_reason(self):
        client, _ = _client(
            {"/v2/orders": httpx.Response(403, json={"message": "insufficient buying power"})}, [0.0])
        with self.assertRaises(AlpacaError) as caught:
            await client.submit_order({"symbol": "AAPL"})
        self.assertIn("insufficient buying power", str(caught.exception))

    async def test_prices_cover_stocks_and_options(self):
        client, _ = _client({
            "/v2/stocks/snapshots": {"AAPL": AAPL},
            "/v1beta1/options/snapshots": {"snapshots": {"AAPL261120C00200000": _option(3.0, 3.2)}},
        }, [0.0])
        prices = await client.prices(["AAPL", "AAPL261120C00200000", "NOPE"])
        self.assertEqual(prices, {"AAPL": Decimal("200.0"), "AAPL261120C00200000": Decimal("3.1")})


class TestMarketDataText(unittest.IsolatedAsyncioTestCase):
    async def test_quote_lines(self):
        client, _ = _client({
            "/v2/stocks/snapshots": {"AAPL": AAPL},
            "/v1beta1/options/snapshots": {"snapshots": {"AAPL261120C00200000": _option(3.0, 3.2)}},
        }, [0.0])
        text = await alpaca.quote_text(client, ["aapl", "AAPL261120C00200000", "ZZZZ"])
        self.assertIn("AAPL: $200.00 (+2.04% vs prev close $196.00)", text)
        self.assertIn("bid $199.90 ask $200.10", text)
        self.assertIn("as of 2026-11-12 15:59 ET", text)
        self.assertIn("AAPL261120C00200000 (AAPL $200 call exp 2026-11-20): bid $3.00 ask $3.20", text)
        self.assertIn("IV 31% · delta 0.52", text)
        self.assertIn("ZZZZ: no quote", text)

    async def test_history(self):
        bars = [{"t": "2026-10-12T04:00:00Z", "c": 180.0, "h": 182.0, "l": 179.0},
                {"t": "2026-11-11T05:00:00Z", "c": 198.0, "h": 203.0, "l": 195.0}]
        client, api = _client({"/v2/stocks/bars": {"bars": {"AAPL": bars}}}, [0.0])
        text = await alpaca.history_text(client, "AAPL", "1mo", TODAY)
        self.assertIn("AAPL 1mo (1Day bars, split-adjusted): $180.00 → $198.00 (+10.00%), high $203.00, low $179.00",
                      text)
        self.assertIn("2026-10-12 180.00, 2026-11-11 198.00", text)
        self.assertEqual(api.requests[0].url.params["start"], "2026-10-12")
        self.assertEqual(await alpaca.history_text(_client({}, [0.0])[0], "ZZZZ", "1mo", TODAY), "")

    async def test_chain_shows_the_nearest_expiration(self):
        client, api = _client({
            "/v2/stocks/snapshots": {"AAPL": AAPL},
            "/v1beta1/options/snapshots/AAPL": {"snapshots": {
                "AAPL261120C00200000": _option(3.0, 3.2),
                "AAPL261120P00195000": _option(1.0, 1.1),
                "AAPL261127C00200000": _option(4.0, 4.2),
            }},
        }, [0.0])
        text = await alpaca.chain_text(client, "AAPL", "both", "", 0, 0, TODAY)
        lines = text.splitlines()
        self.assertEqual(lines[0], "AAPL at $200.00; options expiring 2026-11-20, strikes $184–$216:")
        self.assertTrue(lines[1].startswith("AAPL261120P00195000"))
        self.assertTrue(lines[2].startswith("AAPL261120C00200000"))
        self.assertIn("Other expirations in the next few weeks: 2026-11-27", text)
        params = api.requests[-1].url.params
        self.assertEqual((params["expiration_date_gte"], params["expiration_date_lte"]), ("2026-11-12", "2026-12-17"))
        self.assertNotIn("type", params)

    async def test_chain_of_an_unknown_stock(self):
        client, _ = _client({"/v2/stocks/snapshots": {}}, [0.0])
        with self.assertRaises(AlpacaError):
            await alpaca.chain_text(client, "ZZZZ", "call", "", 0, 0, TODAY)
