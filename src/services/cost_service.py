"""
Cost Service - What the bot spends, per provider, person and channel.

Every paid API call reports its cost here with :func:`record_cost`, at the one place
the cost is known (the service that made the call), so nothing is counted twice and
nothing that runs inside something else (a draw inside a tool call, a voice note
transcribed while reading the chat) is missed.

Two things hear about each cost:

* the open :func:`cost_scope`, if any: the running total for one mention or command,
  shown as the reply's cost prefix. Tasks started inside a scope (``asyncio.gather``)
  share it.
* Redis, one hash per day (America/Denver) for ``/cost``:
  ``cmwgpt:costs:<YYYY-MM-DD>`` with fields ``provider:<name>``, ``user:<id>`` and
  ``channel:<id>``. Costs outside a scope (the nightly memory job, scheduled posts)
  only count toward their provider.

Redis being down never fails a reply; that day's totals just come up short.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, Iterator, List, Optional, Tuple
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis

from src.db.redis_client import KEY_PREFIX, get_async_redis

logger = logging.getLogger(__name__)

# Provider names, as /cost shows them.
OPENROUTER = "openrouter"
CLAUDE_DIRECT = "claude-direct"
GEMINI_DIRECT = "gemini-direct"
RUNPOD = "runpod"
GROQ = "groq"

COST_TIMEZONE = ZoneInfo("America/Denver")
# Daily hashes are kept a little over a year.
COST_TTL_SECONDS = 400 * 24 * 3600


@dataclass
class CostLedger:
    """What one mention or command has spent so far."""
    user_id: Optional[int] = None
    channel_id: Optional[int] = None
    by_provider: Dict[str, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return sum(self.by_provider.values())

    def add(self, provider: str, amount: float) -> None:
        self.by_provider[provider] = self.by_provider.get(provider, 0.0) + amount


_current: ContextVar[Optional[CostLedger]] = ContextVar("cost_ledger", default=None)


@contextmanager
def cost_scope(*, user_id: Optional[int] = None, channel_id: Optional[int] = None) -> Iterator[CostLedger]:
    """Collect the costs recorded inside the block, charged to ``user_id`` and ``channel_id``."""
    ledger = CostLedger(user_id=user_id, channel_id=channel_id)
    token = _current.set(ledger)
    try:
        yield ledger
    finally:
        _current.reset(token)


def current_ledger() -> Optional[CostLedger]:
    return _current.get()


def _cost_key(day: date) -> str:
    return f"{KEY_PREFIX}costs:{day.isoformat()}"


def cost_day(now: Optional[datetime] = None) -> date:
    """The day (America/Denver) a cost made at ``now`` counts toward."""
    return (now or datetime.now(timezone.utc)).astimezone(COST_TIMEZONE).date()


class CostStore:
    """The daily cost tallies in Redis."""

    def __init__(self, redis_getter: Callable[[], aioredis.Redis] = get_async_redis):
        self._redis_getter = redis_getter

    async def add(self, day: date, provider: str, amount: float,
                  user_id: Optional[int], channel_id: Optional[int]) -> None:
        key = _cost_key(day)
        pipe = self._redis_getter().pipeline(transaction=False)
        pipe.hincrbyfloat(key, f"provider:{provider}", amount)
        if user_id is not None:
            pipe.hincrbyfloat(key, f"user:{user_id}", amount)
        if channel_id is not None:
            pipe.hincrbyfloat(key, f"channel:{channel_id}", amount)
        pipe.expire(key, COST_TTL_SECONDS)
        await pipe.execute()

    async def totals(self, start: date, end: date) -> "CostTotals":
        """Totals for the days ``start`` through ``end``, inclusive."""
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        pipe = self._redis_getter().pipeline(transaction=False)
        for day in days:
            pipe.hgetall(_cost_key(day))
        totals = CostTotals()
        for fields in await pipe.execute():
            for name, value in (fields or {}).items():
                kind, _, ident = name.partition(":")
                amount = float(value)
                if kind == "provider":
                    totals.providers[ident] = totals.providers.get(ident, 0.0) + amount
                elif kind in ("user", "channel"):
                    ids = totals.users if kind == "user" else totals.channels
                    ids[int(ident)] = ids.get(int(ident), 0.0) + amount
        return totals


@dataclass
class CostTotals:
    providers: Dict[str, float] = field(default_factory=dict)
    users: Dict[int, float] = field(default_factory=dict)
    channels: Dict[int, float] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return sum(self.providers.values())

    def top_users(self, count: int) -> List[Tuple[int, float]]:
        return sorted(self.users.items(), key=lambda item: item[1], reverse=True)[:count]


_store: Optional[CostStore] = None


def set_cost_store(store: Optional[CostStore]) -> None:
    """Where :func:`record_cost` saves tallies (``None`` keeps them out of Redis)."""
    global _store
    _store = store


async def record_cost(provider: str, amount: Optional[float]) -> None:
    """Report money spent with ``provider``. Never raises."""
    try:
        amount = float(amount or 0.0)
    except (TypeError, ValueError):
        logger.warning(f"Ignoring a non-numeric {provider} cost: {amount!r}")
        return
    if amount <= 0:
        return
    ledger = _current.get()
    if ledger is not None:
        ledger.add(provider, amount)
    if _store is None:
        return
    try:
        await _store.add(
            cost_day(), provider, amount,
            ledger.user_id if ledger else None, ledger.channel_id if ledger else None,
        )
    except Exception as e:
        logger.warning(f"Couldn't save a {provider} cost of ${amount:.4f}: {e}")
