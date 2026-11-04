"""
Kalshi Service - Read-only lookups of Kalshi prediction markets for the ``kalshi`` chat tool.

Keyword search uses the keyless search endpoint kalshi.com's own site uses (``/v1/search/series``,
undocumented); event details, markets and candlesticks use the public, keyless Trade API v2. Prices are
dollars per $1 contract, so a price is the market's probability. Volumes are in contracts.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.utils.market_format import amount, get_json, num, pct

logger = logging.getLogger(__name__)

API_URL = "https://api.elections.kalshi.com"
TRADE_URL = f"{API_URL}/trade-api/v2"
SITE_URL = "https://kalshi.com/markets/"
TIMEOUT = 15.0

# The tool's statuses (shared with polymarket) mapped to Kalshi's search filter.
SEARCH_STATUS = {"active": "open", "closed": "settled", "all": None}
MAX_SEARCH_EVENTS = 5
MAX_SEARCH_MARKETS = 8
MAX_EVENT_MARKETS = 30
MAX_TRENDS = 3
MAX_RULES_CHARS = 1500

_URL_RE = re.compile(r"kalshi\.com/(?:markets|events)/(?P<path>[\w./-]+)", re.I)
_TICKER_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9.]+)+")


async def _get(client: httpx.AsyncClient, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    return await get_json(client, url, params, "Kalshi")


def _contracts(value: Any) -> str:
    return amount(value, prefix="") + " contracts"


def _site_link(series_ticker: Any) -> str:
    return f"{SITE_URL}{str(series_ticker or '').lower()}"


def _price_line(label: str, *, result: str, last: Optional[float], bid: Optional[float], ask: Optional[float],
                previous: Optional[float], volume: Optional[float]) -> str:
    if result:
        parts = [f"settled {result.capitalize()}"]
    else:
        price = last if last else ((bid + ask) / 2 if bid is not None and ask is not None else None)
        parts = [f"Yes {pct(price)}" if price is not None else "no trades yet"]
        if price is not None and previous is not None and price != previous:
            parts.append(f"24h {(price - previous) * 100:+.1f}pts")
        if bid is not None and ask is not None and ask - bid > 0.05:
            parts.append(f"bid {pct(bid)}/ask {pct(ask)}")
    if volume:
        parts.append(f"vol {_contracts(volume)}")
    return f"{label}: " + " · ".join(parts)


def _sort_key(result: str, price: Optional[float]) -> Tuple[bool, float]:
    """Open markets first, likeliest first; settled ones after, winners first."""
    if result:
        return (True, 0.0 if result == "yes" else 1.0)
    return (False, -(price or 0.0))


# --- search (/v1/search/series): prices in cents ---

def _cents(value: Any) -> Optional[float]:
    n = num(value)
    return n / 100 if n is not None else None


def _search_markets(event: Dict[str, Any]) -> List[str]:
    rows = []
    for m in event.get("markets") or []:
        if not isinstance(m, dict):
            continue
        result = str(m.get("result") or "").lower()
        last, bid, ask = _cents(m.get("last_price")), _cents(m.get("yes_bid")), _cents(m.get("yes_ask"))
        # Per-market volume here doesn't match the Trade API's, so search rows leave it out.
        volume = num(m.get("volume"))
        if not result and not volume and not last:
            continue  # nobody has traded it
        delta = _cents(m.get("price_delta"))
        previous = last - delta if last is not None and delta is not None else None
        label = str(m.get("yes_subtitle") or m.get("ticker") or "?")
        line = _price_line(label, result=result, last=last, bid=bid, ask=ask, previous=previous, volume=None)
        rows.append((_sort_key(result, last), line))
    return [line for _, line in sorted(rows, key=lambda r: r[0])]


async def _search(client: httpx.AsyncClient, query: str, status: str, series: Optional[str] = None) -> str:
    """Keyword search, or with ``series`` the events of that series ticker only."""
    params: Dict[str, Any] = {"query": query, "order_by": "querymatch", "page_size": MAX_SEARCH_EVENTS}
    if SEARCH_STATUS.get(status):
        params["status"] = SEARCH_STATUS[status]
    data = await _get(client, f"{API_URL}/v1/search/series", params) or {}
    events = [e for e in data.get("current_page") or [] if isinstance(e, dict)
              and (not series or str(e.get("series_ticker") or "").upper() == series.upper())][:MAX_SEARCH_EVENTS]
    if not events:
        return ""
    total = None if series else data.get("total_results_count")
    head = f"Kalshi series {series.upper()} (all" if series else f'Kalshi events matching "{query}" ({status}'
    lines = [head + (f", {total} matches, top {len(events)} by relevance):" if total else "):")]
    for i, event in enumerate(events, 1):
        title = event.get("event_title") or event.get("event_ticker") or "?"
        if event.get("event_subtitle"):
            title += f" ({event['event_subtitle']})"
        lines.append(f"{i}. {title}")
        lines.append(f"   {_site_link(event.get('series_ticker'))} · event {event.get('event_ticker')}")
        bits = [f"volume {_contracts(event.get('total_volume'))}"]
        if event.get("active_market_count") == 0:
            bits.append("no open markets")
        lines.append("   " + " · ".join(bits))
        markets = _search_markets(event)
        lines.extend(f"   - {line}" for line in markets[:MAX_SEARCH_MARKETS])
        if len(markets) > MAX_SEARCH_MARKETS:
            lines.append(f"   (+{len(markets) - MAX_SEARCH_MARKETS} more outcomes, less likely or settled)")
    return "\n".join(lines)


# --- one event (/trade-api/v2): prices in dollars ---

def _market_label(market: Dict[str, Any]) -> str:
    return str(market.get("yes_sub_title") or market.get("subtitle") or market.get("title") or market.get("ticker"))


def _sorted_markets(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    markets = [
        m for m in event.get("markets") or []
        if isinstance(m, dict) and (m.get("result") or num(m.get("volume_fp")) or num(m.get("last_price_dollars")))
    ]
    return sorted(markets, key=lambda m: _sort_key(str(m.get("result") or "").lower(), num(m.get("last_price_dollars"))))


async def _trend(client: httpx.AsyncClient, series_ticker: str, market: Dict[str, Any]) -> Optional[str]:
    now = int(time.time())
    data = await _get(client, f"{TRADE_URL}/series/{series_ticker}/markets/{market['ticker']}/candlesticks",
                      {"start_ts": now - 30 * 86400, "end_ts": now, "period_interval": 1440})
    history: List[Tuple[float, float]] = []
    for candle in (data or {}).get("candlesticks") or []:
        if not isinstance(candle, dict):
            continue
        t, p = num(candle.get("end_period_ts")), num((candle.get("price") or {}).get("close_dollars"))
        if t is not None and p is not None:
            history.append((t, p))
    if len(history) < 2:
        return None
    week_ago = history[-1][0] - 7 * 86400
    start = datetime.fromtimestamp(history[0][0], tz=timezone.utc).strftime("%Y-%m-%d")
    steps = [f"{pct(history[0][1])} on {start}"]
    if history[0][0] < week_ago - 86400:
        steps.append(f"{pct(next(p for t, p in history if t >= week_ago))} a week ago")
    steps.append(f"{pct(history[-1][1])} at the latest daily close")
    values = [p for _, p in history]
    return f"{_market_label(market)}: " + " → ".join(steps) + f" (range {pct(min(values))}–{pct(max(values))})"


async def _describe_event(client: httpx.AsyncClient, event: Dict[str, Any]) -> str:
    markets = _sorted_markets(event)
    all_markets = [m for m in event.get("markets") or [] if isinstance(m, dict)]
    title = event.get("title") or event.get("event_ticker") or "?"
    if event.get("sub_title"):
        title += f" ({event['sub_title']})"
    bits = [f"volume {_contracts(sum(num(m.get('volume_fp')) or 0 for m in all_markets))}"]
    day = sum(num(m.get("volume_24h_fp")) or 0 for m in all_markets)
    if day:
        bits.append(f"24h {_contracts(day)}")
    interest = sum(num(m.get("open_interest_fp")) or 0 for m in all_markets)
    if interest:
        bits.append(f"open interest {_contracts(interest)}")
    expires = min((str(m.get("expected_expiration_time") or "")[:10] for m in all_markets
                   if m.get("expected_expiration_time")), default="")
    if all_markets and all(m.get("result") for m in all_markets):
        bits.append("settled")
    elif expires:
        bits.append(f"expected to resolve {expires}")
    lines = [f"Kalshi event: {title}", f"   {_site_link(event.get('series_ticker'))} · event {event.get('event_ticker')}",
             "   " + " · ".join(bits)]
    for m in markets[:MAX_EVENT_MARKETS]:
        line = _price_line(
            _market_label(m), result=str(m.get("result") or "").lower(), last=num(m.get("last_price_dollars")),
            bid=num(m.get("yes_bid_dollars")), ask=num(m.get("yes_ask_dollars")),
            previous=num(m.get("previous_price_dollars")), volume=num(m.get("volume_fp")))
        lines.append(f"   - {line}")
    if len(markets) > MAX_EVENT_MARKETS:
        lines.append(f"   (+{len(markets) - MAX_EVENT_MARKETS} more outcomes, less likely or settled)")

    open_markets = [m for m in markets if not m.get("result") and m.get("ticker")][:MAX_TRENDS]
    series = str(event.get("series_ticker") or "")
    if series and open_markets:
        trends = await asyncio.gather(*(_trend(client, series, m) for m in open_markets), return_exceptions=True)
        trend_lines = [t for t in trends if isinstance(t, str)]
        if trend_lines:
            lines.append("Price trend over the last 30 days (daily closes):")
            lines.extend(f"   - {t}" for t in trend_lines)
    rules = str((markets or all_markets or [{}])[0].get("rules_primary") or "").strip()
    if rules:
        lines.append(f"Resolution rules (first outcome): {rules[:MAX_RULES_CHARS]}")
    return "\n".join(lines)


async def _event(client: httpx.AsyncClient, ticker: str) -> Optional[Dict[str, Any]]:
    """The event with this ticker, or the event of the market with this ticker."""
    ticker = ticker.upper()
    data = await _get(client, f"{TRADE_URL}/events/{ticker}", {"with_nested_markets": "true"})
    if isinstance(data, dict) and isinstance(data.get("event"), dict):
        event = data["event"]
        if "markets" not in event and isinstance(data.get("markets"), list):
            event["markets"] = data["markets"]
        return event
    market = await _get(client, f"{TRADE_URL}/markets/{ticker}")
    event_ticker = (market or {}).get("market", {}).get("event_ticker") if isinstance(market, dict) else None
    if event_ticker and event_ticker.upper() != ticker:
        return await _event(client, event_ticker)
    return None


async def kalshi_lookup(query: str, status: str = "active") -> str:
    """Search Kalshi, or describe one event when ``query`` is a kalshi.com link or an event/market ticker.

    Returns "" when nothing matches. Raises ``MarketDataError`` when Kalshi can't be reached.
    """
    query = query.strip()
    status = status if status in SEARCH_STATUS else "active"
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": "cmwgpt"}) as client:
        match = _URL_RE.search(query)
        if match:
            segments = [s for s in match.group("path").split("?")[0].split("/") if s]
            # kalshi.com/markets/<series>/<slug>/<event>: the last segment with a hyphen is the event ticker.
            tickers = [s for s in reversed(segments) if _TICKER_RE.fullmatch(s)]
            for ticker in tickers[:1]:
                event = await _event(client, ticker)
                if event:
                    return await _describe_event(client, event)
            # A series page: search by its ticker, which finds the series' events.
            return await _search(client, segments[0], "all", series=segments[0]) if segments else ""
        if _TICKER_RE.fullmatch(query):
            event = await _event(client, query)
            if event:
                return await _describe_event(client, event)
        return await _search(client, query, status)
