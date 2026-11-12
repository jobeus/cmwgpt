"""
Alpaca Service - The Alpaca Markets client behind the ``market_data`` chat tool and the paper-trading game.

Market data (stock and option quotes, price history, option chains) comes from Alpaca's Market Data API on
the free plan: stock quotes from IEX, option quotes from the indicative feed (15 minutes delayed), and
history up to 15 minutes ago. Every market-data read is cached in memory for a short time, so the same
quote asked for several times a minute is fetched once.

Orders go to the **paper** Trading API only. ``PAPER_URL`` is fixed here and nothing reads it from the
environment, so no key can place a real-money order through this client.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import httpx

from src.utils.market_format import amount, num

logger = logging.getLogger(__name__)

PAPER_URL = "https://paper-api.alpaca.markets/v2"
DATA_URL = "https://data.alpaca.markets"
TIMEOUT = 15.0
MARKET_ZONE = ZoneInfo("America/New_York")

# How long market-data reads are reused.
QUOTE_TTL = 60.0
HISTORY_TTL = 600.0
CLOCK_TTL = 30.0
MAX_CACHE_ENTRIES = 500

MAX_QUOTE_SYMBOLS = 20
# Alpaca takes at most this many option symbols per snapshot request.
OPTION_SNAPSHOT_BATCH = 100
MAX_CHAIN_ROWS = 40
# With no strikes given, a chain shows strikes within this fraction of the stock's price.
CHAIN_STRIKE_WINDOW = 0.08
# With no expiration given, a chain looks this far ahead for the nearest one.
CHAIN_LOOKAHEAD_DAYS = 35
OPTION_MULTIPLIER = 100

# The tool's history periods: bar size, how many days back to ask for.
PERIODS: Dict[str, Tuple[str, int]] = {
    "1d": ("15Min", 5),
    "5d": ("1Hour", 8),
    "1mo": ("1Day", 31),
    "3mo": ("1Day", 92),
    "1y": ("1Week", 366),
    "5y": ("1Month", 5 * 366),
}

_STOCK_RE = re.compile(r"[A-Z]{1,6}(?:[.-][A-Z]{1,2})?")
_OPTION_RE = re.compile(r"(?P<root>[A-Z]{1,6})\d?(?P<day>\d{6})(?P<type>[CP])(?P<strike>\d{8})")


class AlpacaError(Exception):
    """Alpaca couldn't be reached, or refused the request; the message is fit to show the model."""


@dataclass(frozen=True)
class OptionContract:
    """An option, read from its OCC symbol (``AAPL261120C00230000``)."""

    symbol: str
    underlying: str
    expiration: date
    kind: str  # "call" or "put"
    strike: Decimal

    def describe(self) -> str:
        return f"{self.underlying} ${self.strike.normalize():f} {self.kind} exp {self.expiration.isoformat()}"


def clean_symbol(value: Any) -> str:
    return str(value or "").strip().upper().lstrip("$").replace(" ", "")


def parse_option(symbol: str) -> Optional[OptionContract]:
    """The contract ``symbol`` names, or ``None`` if it isn't an OCC option symbol."""
    match = _OPTION_RE.fullmatch(symbol)
    if not match:
        return None
    try:
        expiration = datetime.strptime(match["day"], "%y%m%d").date()
    except ValueError:
        return None
    return OptionContract(
        symbol=symbol,
        underlying=match["root"],
        expiration=expiration,
        kind="call" if match["type"] == "C" else "put",
        strike=Decimal(match["strike"]) / 1000,
    )


def is_stock_symbol(symbol: str) -> bool:
    return bool(_STOCK_RE.fullmatch(symbol))


def _decimal(value: Any) -> Optional[Decimal]:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and d > 0 else None


def stock_mark(snapshot: Dict[str, Any]) -> Optional[Decimal]:
    """A stock's current price: its last trade, else the day's close."""
    return (_decimal((snapshot.get("latestTrade") or {}).get("p"))
            or _decimal((snapshot.get("dailyBar") or {}).get("c"))
            or _decimal((snapshot.get("prevDailyBar") or {}).get("c")))


def option_mark(snapshot: Dict[str, Any]) -> Optional[Decimal]:
    """An option's current price: the middle of its quote, else its last trade, else its bid."""
    quote = snapshot.get("latestQuote") or {}
    bid, ask = _decimal(quote.get("bp")), _decimal(quote.get("ap"))
    if bid and ask:
        return (bid + ask) / 2
    return _decimal((snapshot.get("latestTrade") or {}).get("p")) or bid


class AlpacaClient:
    """Alpaca's paper Trading API and Market Data API, with market-data reads cached."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
        self._transport = transport
        self._monotonic = monotonic
        self._http: Optional[httpx.AsyncClient] = None
        self._cache: Dict[str, Tuple[float, Any]] = {}

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=TIMEOUT, headers=self._headers, transport=self._transport)
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _request(
        self, method: str, url: str, *, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """The JSON answer; ``None`` for a 404 or an empty body. Raises ``AlpacaError`` otherwise."""
        try:
            response = await self._client().request(method, url, params=params, json=body)
        except httpx.HTTPError as e:
            raise AlpacaError(f"Couldn't reach Alpaca: {e.__class__.__name__}") from e
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            try:
                reason = str(response.json().get("message") or "")
            except (ValueError, AttributeError):
                reason = ""
            raise AlpacaError(f"Alpaca refused it (HTTP {response.status_code}): {reason or 'no reason given'}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as e:
            raise AlpacaError("Alpaca sent something that isn't JSON.") from e

    async def _cached_get(self, url: str, params: Dict[str, Any], ttl: float) -> Any:
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        now = self._monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        data = await self._request("GET", url, params=params)
        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        self._cache[key] = (now + ttl, data)
        return data

    # -- market data ----------------------------------------------------------------

    async def stock_snapshots(self, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        if not symbols:
            return {}
        data = await self._cached_get(
            f"{DATA_URL}/v2/stocks/snapshots", {"symbols": ",".join(sorted(set(symbols)))}, QUOTE_TTL)
        return {k: v for k, v in (data or {}).items() if isinstance(v, dict)}

    async def option_snapshots(self, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        wanted = sorted(set(symbols))
        found: Dict[str, Dict[str, Any]] = {}
        for start in range(0, len(wanted), OPTION_SNAPSHOT_BATCH):
            batch = wanted[start:start + OPTION_SNAPSHOT_BATCH]
            data = await self._cached_get(
                f"{DATA_URL}/v1beta1/options/snapshots", {"symbols": ",".join(batch)}, QUOTE_TTL)
            found.update({k: v for k, v in ((data or {}).get("snapshots") or {}).items() if isinstance(v, dict)})
        return found

    async def option_chain(self, underlying: str, filters: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        data = await self._cached_get(
            f"{DATA_URL}/v1beta1/options/snapshots/{underlying}", {**filters, "limit": 1000}, QUOTE_TTL)
        return {k: v for k, v in ((data or {}).get("snapshots") or {}).items() if isinstance(v, dict)}

    async def bars(self, symbol: str, timeframe: str, start: date) -> List[Dict[str, Any]]:
        data = await self._cached_get(
            f"{DATA_URL}/v2/stocks/bars",
            {"symbols": symbol, "timeframe": timeframe, "start": start.isoformat(), "adjustment": "split",
             "limit": 1000},
            HISTORY_TTL)
        return [b for b in ((data or {}).get("bars") or {}).get(symbol) or [] if isinstance(b, dict)]

    async def prices(self, symbols: Sequence[str]) -> Dict[str, Decimal]:
        """Current prices of stocks and options (per share), for the symbols Alpaca has one for."""
        options = [s for s in symbols if parse_option(s)]
        stocks = [s for s in symbols if s not in options]
        stock_data, option_data = await asyncio.gather(self.stock_snapshots(stocks), self.option_snapshots(options))
        marks = {s: stock_mark(snap) for s, snap in stock_data.items()}
        marks.update({s: option_mark(snap) for s, snap in option_data.items()})
        return {s: price for s, price in marks.items() if price}

    # -- paper trading --------------------------------------------------------------

    async def clock(self) -> Dict[str, Any]:
        """Whether the market is open, and its next open and close."""
        return await self._cached_get(f"{PAPER_URL}/clock", {}, CLOCK_TTL) or {}

    async def submit_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._request("POST", f"{PAPER_URL}/orders", body=order)
        if not isinstance(result, dict) or not result.get("id"):
            raise AlpacaError("Alpaca didn't confirm the order.")
        return result

    async def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        return await self._request("GET", f"{PAPER_URL}/orders/{order_id}")

    async def cancel_order(self, order_id: str) -> None:
        await self._request("DELETE", f"{PAPER_URL}/orders/{order_id}")

    async def positions(self) -> List[Dict[str, Any]]:
        return [p for p in await self._request("GET", f"{PAPER_URL}/positions") or [] if isinstance(p, dict)]

    async def close_everything(self) -> None:
        """Cancel every open order and sell every position in the paper account."""
        await self._request("DELETE", f"{PAPER_URL}/orders")
        await self._request("DELETE", f"{PAPER_URL}/positions", params={"cancel_orders": "true"})


# -- the market_data tool's text ----------------------------------------------------

def _market_time(stamp: Any) -> str:
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return "?"
    return when.astimezone(MARKET_ZONE).strftime("%Y-%m-%d %H:%M ET")


def _price(value: Any) -> str:
    n = num(value)
    return f"${n:,.2f}" if n else "n/a"


def _change(now: Optional[float], before: Optional[float]) -> str:
    if not now or not before:
        return ""
    return f" ({(now - before) / before * 100:+.2f}% vs prev close {_price(before)})"


def _stock_line(symbol: str, snap: Dict[str, Any]) -> str:
    trade, quote = snap.get("latestTrade") or {}, snap.get("latestQuote") or {}
    day, previous = snap.get("dailyBar") or {}, snap.get("prevDailyBar") or {}
    last = num(trade.get("p")) or num(day.get("c"))
    parts = [f"{symbol}: {_price(last)}{_change(last, num(previous.get('c')))}"]
    if num(quote.get("bp")) and num(quote.get("ap")):
        parts.append(f"bid {_price(quote.get('bp'))} ask {_price(quote.get('ap'))}")
    if num(day.get("l")) and num(day.get("h")):
        parts.append(f"day {_price(day.get('l'))}–{_price(day.get('h'))}")
    if num(day.get("v")):
        parts.append(f"IEX vol {amount(day.get('v'), prefix='')}")
    parts.append(f"as of {_market_time(trade.get('t') or quote.get('t'))}")
    return " · ".join(parts)


def _option_line(contract: OptionContract, snap: Dict[str, Any]) -> str:
    quote, trade, greeks = snap.get("latestQuote") or {}, snap.get("latestTrade") or {}, snap.get("greeks") or {}
    parts = [f"{contract.symbol} ({contract.describe()}): bid {_price(quote.get('bp'))} ask {_price(quote.get('ap'))}",
             f"last {_price(trade.get('p'))}"]
    volatility, delta = num(snap.get("impliedVolatility")), num(greeks.get("delta"))
    if volatility:
        parts.append(f"IV {volatility * 100:.0f}%")
    if delta is not None:
        parts.append(f"delta {delta:.2f}")
    return " · ".join(parts)


async def quote_text(client: AlpacaClient, symbols: Sequence[str]) -> str:
    """One line per stock or option symbol; unknown symbols say so."""
    wanted = list(dict.fromkeys(clean_symbol(s) for s in symbols if clean_symbol(s)))[:MAX_QUOTE_SYMBOLS]
    contracts = {s: c for s in wanted if (c := parse_option(s))}
    stocks = [s for s in wanted if s not in contracts and is_stock_symbol(s)]
    stock_data, option_data = await asyncio.gather(
        client.stock_snapshots(stocks), client.option_snapshots(list(contracts)))
    lines = []
    for symbol in wanted:
        if symbol in stock_data:
            lines.append(_stock_line(symbol, stock_data[symbol]))
        elif symbol in option_data:
            lines.append(_option_line(contracts[symbol], option_data[symbol]))
        else:
            lines.append(f"{symbol}: no quote. Check the ticker (US stocks and ETFs only), or for an option "
                         "get its exact symbol from an option chain.")
    if contracts:
        lines.append("Option prices are per share (a contract is 100 shares) and 15 minutes delayed.")
    return "\n".join(lines)


async def history_text(client: AlpacaClient, symbol: str, period: str, today: date) -> str:
    """A stock's closes over ``period``, with its change, high and low. "" when there's no data."""
    symbol = clean_symbol(symbol)
    timeframe, days = PERIODS[period]
    bars = await client.bars(symbol, timeframe, today - timedelta(days=days))
    if period == "1d" and bars:
        last_day = str(bars[-1].get("t"))[:10]
        bars = [b for b in bars if str(b.get("t"))[:10] == last_day]
    closes = [(str(b.get("t")), num(b.get("c"))) for b in bars]
    points = [(t, c) for t, c in closes if c]
    if not points:
        return ""
    first, last = points[0][1], points[-1][1]
    high = max(num(b.get("h")) or 0.0 for b in bars)
    low = min(num(b.get("l")) or last for b in bars)
    intraday = timeframe.endswith(("Min", "Hour"))

    def label(stamp: str) -> str:
        return _market_time(stamp)[5:16] if intraday else stamp[:10]

    return (
        f"{symbol} {period} ({timeframe} bars, split-adjusted): {_price(first)} → {_price(last)} "
        f"({(last - first) / first * 100:+.2f}%), high {_price(high)}, low {_price(low)}\n"
        "Closes: " + ", ".join(f"{label(t)} {c:,.2f}" for t, c in points)
    )


async def chain_text(
    client: AlpacaClient, underlying: str, option_type: str, expiration: str,
    strike_min: float, strike_max: float, today: date,
) -> str:
    """The option contracts of ``underlying`` for one expiration, nearest the money first cut, then by strike."""
    underlying = clean_symbol(underlying)
    spot = stock_mark((await client.stock_snapshots([underlying])).get(underlying) or {})
    if spot is None:
        raise AlpacaError(f"No quote for {underlying}; check the ticker.")
    price = float(spot)
    filters: Dict[str, Any] = {
        "strike_price_gte": round(strike_min or price * (1 - CHAIN_STRIKE_WINDOW), 2),
        "strike_price_lte": round(strike_max or price * (1 + CHAIN_STRIKE_WINDOW), 2),
    }
    if option_type in ("call", "put"):
        filters["type"] = option_type
    if expiration:
        filters["expiration_date"] = expiration
    else:
        filters["expiration_date_gte"] = today.isoformat()
        filters["expiration_date_lte"] = (today + timedelta(days=CHAIN_LOOKAHEAD_DAYS)).isoformat()

    found = [(c, snap) for symbol, snap in (await client.option_chain(underlying, filters)).items()
             if (c := parse_option(symbol))]
    if not found:
        return ""
    expirations = sorted({c.expiration for c, _ in found})
    chosen = expirations[0]
    rows = sorted((r for r in found if r[0].expiration == chosen), key=lambda r: abs(r[0].strike - spot))
    rows = sorted(rows[:MAX_CHAIN_ROWS], key=lambda r: (r[0].strike, r[0].kind))
    lines = [f"{underlying} at {_price(price)}; options expiring {chosen.isoformat()}, strikes "
             f"${filters['strike_price_gte']:g}–${filters['strike_price_lte']:g}:"]
    lines += [_option_line(c, snap) for c, snap in rows]
    if len(expirations) > 1:
        lines.append("Other expirations in the next few weeks: " + ", ".join(d.isoformat() for d in expirations[1:])
                     + ". Later ones need their date.")
    lines.append("Prices are per share (a contract is 100 shares) and 15 minutes delayed.")
    return "\n".join(lines)
