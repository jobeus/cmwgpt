"""Shared helpers for the prediction-market lookups (Polymarket, Kalshi): JSON fetching and number formatting."""

from typing import Any, Dict, Optional

import httpx


class MarketDataError(Exception):
    """A prediction market's API couldn't be reached or answered with an error."""


async def get_json(client: httpx.AsyncClient, url: str, params: Optional[Dict[str, Any]], source: str) -> Any:
    """GET ``url`` as JSON. A 404 is ``None``; anything else that fails raises ``MarketDataError``."""
    try:
        response = await client.get(url, params=params)
    except httpx.HTTPError as e:
        raise MarketDataError(f"Couldn't reach {source}: {e.__class__.__name__}") from e
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise MarketDataError(f"{source} answered HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError as e:
        raise MarketDataError(f"{source} sent something that isn't JSON.") from e


def num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def amount(value: Any, prefix: str = "$") -> str:
    """A dollar amount or count, shortened: $1.2M, $45k, $9,170."""
    n = num(value) or 0.0
    if n >= 1_000_000_000:
        return f"{prefix}{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{prefix}{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{prefix}{n / 1000:.0f}k"
    return f"{prefix}{n:,.0f}"


def pct(p: float) -> str:
    """A 0–1 price as a percentage."""
    return f"{p * 100:.1f}%"
