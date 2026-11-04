"""
Polymarket Service - Read-only lookups of Polymarket prediction markets for the ``polymarket`` chat tool.

Uses Polymarket's public, keyless APIs: Gamma (``gamma-api.polymarket.com``) for search and event
details, and the CLOB (``clob.polymarket.com``) for price history. Everything comes back as plain text
for the model.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from src.utils.market_format import amount, get_json, num, pct

logger = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
EVENT_URL = "https://polymarket.com/event/"
TIMEOUT = 15.0

STATUSES = ("active", "closed", "all")
# Events one search returns, and markets shown per event in a search vs. a single-event lookup.
MAX_SEARCH_EVENTS = 5
MAX_SEARCH_MARKETS = 8
MAX_EVENT_MARKETS = 30
# Open markets in a single-event lookup that get a 30-day price trend.
MAX_TRENDS = 3
MAX_RULES_CHARS = 1500

_EVENT_URL_RE = re.compile(r"polymarket\.com/(?:[a-z]{2}/)?(?:event|market)/(?P<slug>[\w-]+)(?:/(?P<market>[\w-]+))?", re.I)
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")


async def _get(client: httpx.AsyncClient, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    return await get_json(client, url, params, "Polymarket")


def _json_list(value: Any) -> List[Any]:
    """Gamma sends some list fields (outcomes, prices, token IDs) as JSON-encoded strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _money(value: Any) -> str:
    return amount(value)


def _day(value: Any) -> Optional[str]:
    return str(value)[:10] if value else None


def _prices(market: Dict[str, Any]) -> List[Optional[float]]:
    return [num(p) for p in _json_list(market.get("outcomePrices"))]


def _yes_price(market: Dict[str, Any]) -> float:
    prices = _prices(market)
    return prices[0] if prices and prices[0] is not None else -1.0


def _market_line(market: Dict[str, Any], *, multi: bool) -> str:
    outcomes = [str(o) for o in _json_list(market.get("outcomes"))]
    prices = _prices(market)
    label = (market.get("groupItemTitle") or market.get("question") or "?") if multi else None
    closed = bool(market.get("closed"))

    if closed and prices:
        winner = next((o for o, p in zip(outcomes, prices, strict=False) if p is not None and p >= 0.99), None)
        odds = f"closed, resolved {winner}" if winner else "closed, " + " · ".join(
            f"{o} {pct(p)}" for o, p in zip(outcomes, prices, strict=False) if p is not None)
    elif outcomes[:2] == ["Yes", "No"] and prices and prices[0] is not None:
        odds = f"Yes {pct(prices[0])}"
    else:
        odds = " · ".join(f"{o} {pct(p)}" for o, p in zip(outcomes, prices, strict=False) if p is not None) or "no price"

    parts = [odds]
    change = num(market.get("oneDayPriceChange"))
    if not closed and change:
        parts.append(f"24h {change * 100:+.1f}pts")
    bid, ask = num(market.get("bestBid")), num(market.get("bestAsk"))
    if not closed and bid is not None and ask is not None and ask - bid > 0.05:
        parts.append(f"bid {pct(bid)}/ask {pct(ask)}")
    volume = num(market.get("volumeNum")) or num(market.get("volume"))
    if volume:
        parts.append(f"vol {_money(volume)}")
    if not multi and market.get("question"):
        return f"{market['question']}: " + " · ".join(parts)
    return f"{label}: " + " · ".join(parts)


def _event_header(event: Dict[str, Any]) -> str:
    title = event.get("title") or event.get("slug") or "?"
    bits = [f"volume {_money(event.get('volume'))}"]
    if not event.get("closed") and num(event.get("volume24hr")):
        bits.append(f"24h {_money(event.get('volume24hr'))}")
    if num(event.get("liquidity")):
        bits.append(f"liquidity {_money(event.get('liquidity'))}")
    if num(event.get("openInterest")):
        bits.append(f"open interest {_money(event.get('openInterest'))}")
    end = _day(event.get("endDate"))
    if event.get("closed"):
        bits.append(f"closed (ended {end})" if end else "closed")
    elif end:
        bits.append(f"ends {end}")
    return f"{title}\n   {EVENT_URL}{event.get('slug', '')}\n   " + " · ".join(bits)


def _sorted_markets(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Open markets first, most likely first, then closed ones. Open ones nobody has traded
    (placeholder outcomes like "Person A" sitting at 50%) are left out."""
    markets = [
        m for m in event.get("markets") or []
        if isinstance(m, dict) and (m.get("closed") or num(m.get("volumeNum")) or num(m.get("volume")))
    ]
    return sorted(markets, key=lambda m: (bool(m.get("closed")), -_yes_price(m)))


def _event_block(event: Dict[str, Any], max_markets: int) -> List[str]:
    markets = _sorted_markets(event)
    multi = len(markets) > 1
    lines = [_event_header(event)]
    for market in markets[:max_markets]:
        lines.append(f"   - {_market_line(market, multi=multi)}")
    if len(markets) > max_markets:
        lines.append(f"   (+{len(markets) - max_markets} more outcomes, less likely or closed)")
    return lines


async def _trend(client: httpx.AsyncClient, market: Dict[str, Any]) -> Optional[str]:
    tokens = _json_list(market.get("clobTokenIds"))
    if not tokens:
        return None
    data = await _get(client, f"{CLOB_URL}/prices-history",
                      {"market": str(tokens[0]), "interval": "1m", "fidelity": 360})
    history: List[Tuple[float, float]] = []
    for point in (data or {}).get("history") or []:
        t, p = (num(point.get("t")), num(point.get("p"))) if isinstance(point, dict) else (None, None)
        if t is not None and p is not None:
            history.append((t, p))
    if len(history) < 2:
        return None
    week_ago = history[-1][0] - 7 * 86400
    start = datetime.fromtimestamp(history[0][0], tz=timezone.utc).strftime("%Y-%m-%d")
    steps = [f"{pct(history[0][1])} on {start}"]
    if history[0][0] < week_ago - 86400:
        steps.append(f"{pct(next(p for t, p in history if t >= week_ago))} a week ago")
    steps.append(f"{pct(history[-1][1])} now")
    values = [p for _, p in history]
    outcome = (_json_list(market.get("outcomes")) or ["Yes"])[0]
    label = market.get("groupItemTitle") or market.get("question") or "?"
    return f"{label} ({outcome}): " + " → ".join(steps) + f" (range {pct(min(values))}–{pct(max(values))})"


async def _event_by_slug(client: httpx.AsyncClient, slug: str) -> Optional[Dict[str, Any]]:
    event = await _get(client, f"{GAMMA_URL}/events/slug/{slug}")
    if isinstance(event, dict) and event.get("markets") is not None:
        return event
    # A market's own slug: find the event it belongs to.
    market = await _get(client, f"{GAMMA_URL}/markets/slug/{slug}")
    events = market.get("events") if isinstance(market, dict) else None
    if events and isinstance(events[0], dict) and events[0].get("slug") and events[0]["slug"] != slug:
        return await _event_by_slug(client, events[0]["slug"])
    return None


async def _describe_event(client: httpx.AsyncClient, event: Dict[str, Any]) -> str:
    lines = _event_block(event, MAX_EVENT_MARKETS)
    lines[0] = f"Polymarket event: {lines[0]}"
    open_markets = [m for m in _sorted_markets(event) if not m.get("closed")][:MAX_TRENDS]
    trends = await asyncio.gather(*(_trend(client, m) for m in open_markets), return_exceptions=True)
    trend_lines = [t for t in trends if isinstance(t, str)]
    if trend_lines:
        lines.append("Price trend over the last 30 days:")
        lines.extend(f"   - {t}" for t in trend_lines)
    rules = str(event.get("description") or "").strip()
    if rules:
        if len(rules) > MAX_RULES_CHARS:
            rules = rules[:MAX_RULES_CHARS] + "…"
        lines.append(f"Resolution rules: {rules}")
    return "\n".join(lines)


async def _search(client: httpx.AsyncClient, query: str, status: str) -> str:
    params: Dict[str, Any] = {
        "q": query, "limit_per_type": MAX_SEARCH_EVENTS, "search_tags": "false", "search_profiles": "false",
        "sort": "volume",
    }
    if status != "all":
        params["events_status"] = status
    data = await _get(client, f"{GAMMA_URL}/public-search", params) or {}
    events: Sequence[Dict[str, Any]] = [e for e in data.get("events") or [] if isinstance(e, dict)]
    if not events:
        return ""
    total = (data.get("pagination") or {}).get("totalResults")
    head = f'Polymarket events matching "{query}" ({status}'
    head += f", {total} matches, top {len(events)} by volume):" if total else "):"
    lines = [head]
    for i, event in enumerate(events[:MAX_SEARCH_EVENTS], 1):
        block = _event_block(event, MAX_SEARCH_MARKETS)
        lines.append(f"{i}. {block[0]}")
        lines.extend(block[1:])
    return "\n".join(lines)


async def polymarket_lookup(query: str, status: str = "active") -> str:
    """Search Polymarket, or describe one event when ``query`` is a polymarket.com link or an event slug.

    Returns "" when nothing matches. Raises ``MarketDataError`` when Polymarket can't be reached.
    """
    query = query.strip()
    status = status if status in STATUSES else "active"
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": "cmwgpt"}) as client:
        match = _EVENT_URL_RE.search(query)
        slug = match.group("slug").lower() if match else (query if _SLUG_RE.fullmatch(query) else None)
        if slug:
            event = await _event_by_slug(client, slug)
            if event:
                return await _describe_event(client, event)
            if match:
                return ""
        return await _search(client, query, status)
