"""
Prediction Markets - The ``prediction_markets`` chat tool's lookup: Polymarket and Kalshi at once.

Both sites are queried concurrently and their results joined, each in the same shape (event title, link,
event stats, then outcomes as "label: Yes N% · 24h ±N pts · ..."). A polymarket.com link only goes to
Polymarket and a kalshi.com link only to Kalshi. One site failing or finding nothing doesn't hide the other.
"""

import asyncio
import logging
import re
from typing import Awaitable, Callable, List, Tuple

from src.services.kalshi_service import kalshi_lookup
from src.services.polymarket_service import polymarket_lookup
from src.utils.market_format import MarketDataError

logger = logging.getLogger(__name__)

_POLYMARKET_LINK = re.compile(r"polymarket\.com/", re.I)
_KALSHI_LINK = re.compile(r"kalshi\.com/", re.I)


def _sites(query: str) -> List[Tuple[str, Callable[[str, str], Awaitable[str]]]]:
    if _POLYMARKET_LINK.search(query):
        return [("Polymarket", polymarket_lookup)]
    if _KALSHI_LINK.search(query):
        return [("Kalshi", kalshi_lookup)]
    return [("Polymarket", polymarket_lookup), ("Kalshi", kalshi_lookup)]


async def prediction_market_lookup(query: str, status: str = "active") -> str:
    """Every site's results for ``query``, joined. Returns "" when no site found anything.

    Raises ``MarketDataError`` only when every site asked failed.
    """
    sites = _sites(query)
    results = await asyncio.gather(*(lookup(query, status) for _, lookup in sites), return_exceptions=True)

    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, Exception):
            raise result  # cancellation

    sections, errors = [], []
    for (name, _), result in zip(sites, results, strict=True):
        if isinstance(result, MarketDataError):
            errors.append(f"{name}: {result}")
            sections.append(f"{name}: lookup failed ({result})")
        elif isinstance(result, Exception):
            logger.error(f"{name} lookup failed", exc_info=result)
            errors.append(f"{name}: lookup failed")
            sections.append(f"{name}: lookup failed")
        elif isinstance(result, str) and result:
            sections.append(result)
        else:
            sections.append(f'{name}: no {status} events match "{query}".')

    if len(errors) == len(sites):
        raise MarketDataError("; ".join(errors))
    if not any(isinstance(r, str) and r for r in results):
        return ""
    return "\n\n".join(sections)
