"""
Paper Trading Service - A stock and option trading game on one Alpaca paper account.

The Alpaca account holds $1M of play money and knows nothing about Discord users, so the game keeps its own
ledger. Up to ``MAX_PLAYERS`` people get ``ALLOWANCE`` each; someone becomes a player the first time they ask
for a trade. Every order is sent to Alpaca's paper account and recorded here against the player who asked,
and their cash and holdings change only by what Alpaca reports as filled, at the price it filled at.

The game is long-only: buying, and selling what you hold. A buy sets cash aside until it fills or ends (a
market buy sets aside the current price plus a buffer), and a sell sets the shares aside, so a player can't
spend the same money or sell the same shares twice. All orders are day orders; one placed while the market
is closed waits for the next open.

Options are never exercised. Shortly before the close on its expiration day an option still held is sold at
market, and one that got past that is written off at $0.

Open orders are checked against Alpaca whenever a game tool runs and every ``POLL_SECONDS``.

Redis keys:

- ``cmwgpt:paper:players`` - hash, user ID -> JSON ``{cash, joined}``
- ``cmwgpt:paper:positions:<user_id>`` - hash, symbol -> JSON ``{qty, cost}`` (``cost`` is what the whole
  position was bought for)
- ``cmwgpt:paper:orders:open`` - hash, order number -> JSON order still working at Alpaca
- ``cmwgpt:paper:orders:done:<user_id>`` - list of that player's last ``MAX_DONE_ORDERS`` finished orders
- ``cmwgpt:paper:orders:next`` - counter for order numbers
"""

import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import redis.asyncio as aioredis

from src.db.redis_client import KEY_PREFIX, get_async_redis
from src.services.alpaca_service import (
    MARKET_ZONE, OPTION_MULTIPLIER, AlpacaClient, AlpacaError, OptionContract, clean_symbol, is_stock_symbol,
    parse_option,
)
from src.telemetry import routine_span
from src.utils.discord_error_utils import concise_error

logger = logging.getLogger(__name__)

ALLOWANCE = Decimal("100000")
MAX_PLAYERS = 10
POLL_SECONDS = 60
# A market buy sets aside the current price plus this much, since it fills at whatever the price is then.
STOCK_BUFFER = Decimal("1.05")
OPTION_BUFFER = Decimal("1.15")
# After placing an order, look this many times, a second apart, for its fill.
FILL_CHECKS = 3
# Options are sold this long before the close on their expiration day, and can't be bought after that.
EXPIRY_SELL_BEFORE_CLOSE = timedelta(minutes=30)
MAX_DONE_ORDERS = 20
SHOWN_DONE_ORDERS = 8
FINAL_STATUSES = {"filled", "canceled", "expired", "rejected"}

_CENT = Decimal("0.01")
_SHARE = Decimal("0.000000001")
_PLAYERS_KEY = f"{KEY_PREFIX}paper:players"
_OPEN_KEY = f"{KEY_PREFIX}paper:orders:open"
_NEXT_KEY = f"{KEY_PREFIX}paper:orders:next"

Order = Dict[str, Any]


class PaperTradeError(ValueError):
    """A trade the game refuses; the message tells the model why."""


def _positions_key(user_id: int) -> str:
    return f"{KEY_PREFIX}paper:positions:{user_id}"


def _done_key(user_id: int) -> str:
    return f"{KEY_PREFIX}paper:orders:done:{user_id}"


def _d(value: Any) -> Decimal:
    try:
        d = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return d if d.is_finite() else Decimal(0)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def money(value: Any) -> str:
    return f"${_d(value):,.2f}"


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _percent(now: Decimal, before: Decimal) -> str:
    return f"{(now - before) / before * 100:+.2f}%" if before else "n/a"


def _multiplier(symbol: str) -> int:
    return OPTION_MULTIPLIER if parse_option(symbol) else 1


def _units(symbol: str, quantity: Decimal) -> str:
    unit = "contract" if parse_option(symbol) else "share"
    return f"{_qty(quantity)} {unit}{'' if quantity == 1 else 's'}"


def _name(symbol: str) -> str:
    contract = parse_option(symbol)
    return f"{symbol} ({contract.describe()})" if contract else symbol


def _describe_order(order: Order) -> str:
    """An order in a line: what was asked, then what became of it."""
    size = f"{money(order['dollars'])} of" if _d(order.get("dollars")) else _units(order["symbol"], _d(order["qty"]))
    kind = f"limit {money(order['limit'])}" if _d(order.get("limit")) else "market"
    text = f"#{order['no']} {order['side']} {size} {order['symbol']} ({kind})"
    if order.get("auto"):
        text += " [sold automatically before expiration]"
    filled, status = _d(order.get("filled_qty")), order.get("status") or "new"
    if filled:
        price = _d(order["filled_cash"]) / filled / _multiplier(order["symbol"])
        text += f": {'filled' if status == 'filled' else status + ', filled so far'} " \
                f"{_units(order['symbol'], filled)} at {money(price)} = {money(order['filled_cash'])}"
    else:
        text += f": {status}, nothing filled"
    return text


class PaperStore:
    """The game's ledger in Redis."""

    def __init__(self, redis_getter: Callable[[], aioredis.Redis] = get_async_redis):
        self._redis_getter = redis_getter

    @property
    def _redis(self) -> aioredis.Redis:
        return self._redis_getter()

    async def players(self) -> Dict[int, Dict[str, Any]]:
        return {int(uid): json.loads(raw) for uid, raw in (await self._redis.hgetall(_PLAYERS_KEY)).items()}

    async def save_player(self, user_id: int, player: Dict[str, Any]) -> None:
        await self._redis.hset(_PLAYERS_KEY, str(user_id), json.dumps(player))

    async def positions(self, user_id: int) -> Dict[str, Dict[str, Any]]:
        raw = await self._redis.hgetall(_positions_key(user_id))
        return {symbol: json.loads(value) for symbol, value in sorted(raw.items())}

    async def save_position(self, user_id: int, symbol: str, position: Optional[Dict[str, Any]]) -> None:
        if position is None:
            await self._redis.hdel(_positions_key(user_id), symbol)
        else:
            await self._redis.hset(_positions_key(user_id), symbol, json.dumps(position))

    async def next_order_no(self) -> int:
        return int(await self._redis.incr(_NEXT_KEY))

    async def open_orders(self) -> List[Order]:
        orders = [json.loads(raw) for raw in (await self._redis.hgetall(_OPEN_KEY)).values()]
        return sorted(orders, key=lambda o: o["no"])

    async def save_open(self, order: Order) -> None:
        await self._redis.hset(_OPEN_KEY, str(order["no"]), json.dumps(order))

    async def finish(self, order: Order) -> None:
        """Move an order from the open ones to its player's finished ones."""
        await self._redis.hdel(_OPEN_KEY, str(order["no"]))
        await self.add_done(order["user_id"], order)

    async def add_done(self, user_id: int, record: Dict[str, Any]) -> None:
        await self._redis.lpush(_done_key(user_id), json.dumps(record))
        await self._redis.ltrim(_done_key(user_id), 0, MAX_DONE_ORDERS - 1)

    async def done_orders(self, user_id: int) -> List[Dict[str, Any]]:
        return [json.loads(raw) for raw in await self._redis.lrange(_done_key(user_id), 0, SHOWN_DONE_ORDERS - 1)]

    async def wipe(self) -> None:
        """Forget every player, holding and order. Order numbers carry on."""
        keys = [_PLAYERS_KEY, _OPEN_KEY]
        for user_id in await self.players():
            keys += [_positions_key(user_id), _done_key(user_id)]
        await self._redis.delete(*keys)


class PaperTradingService:
    """Places players' trades on the paper account and keeps who owns what."""

    def __init__(
        self,
        client: AlpacaClient,
        *,
        store: Optional[PaperStore] = None,
        is_testing: bool = False,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ):
        self.client = client
        self._store = store or PaperStore()
        self._is_testing = is_testing
        self._now = now
        self._sleep = sleep
        # One trade or sync at a time, so two can't spend the same cash.
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._is_testing or (self._task and not self._task.done()):
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("Paper-trading orders checked every %ss", POLL_SECONDS)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                with routine_span("paper trading poll"):
                    await self.tick()
            except Exception as e:
                logger.warning("Couldn't check paper-trading orders: %s", concise_error(e))
            await asyncio.sleep(max(1.0, POLL_SECONDS - (time.monotonic() - started)))

    async def tick(self) -> None:
        """Bring open orders up to date, then deal with options at their expiration."""
        async with self._lock:
            await self._sync()
            await self._handle_expirations()

    # -- fills ----------------------------------------------------------------------

    async def _sync(self) -> None:
        for order in await self._store.open_orders():
            try:
                remote = await self.client.get_order(order["alpaca_id"])
            except AlpacaError as e:
                logger.warning("[paper] Couldn't check order #%s: %s", order["no"], e)
                continue
            # Alpaca no longer knowing an order means it will never fill.
            await self._apply(order, remote or {"status": "canceled"})

    async def _apply(self, order: Order, remote: Dict[str, Any]) -> Order:
        """Record what Alpaca says about ``order``: move cash and shares for anything newly filled."""
        user_id, symbol = order["user_id"], order["symbol"]
        filled = _d(remote.get("filled_qty"))
        filled_cash = _cents(filled * _d(remote.get("filled_avg_price")) * _multiplier(symbol))
        new_qty, new_cash = filled - _d(order.get("filled_qty")), filled_cash - _d(order.get("filled_cash"))
        if new_qty > 0:
            player = (await self._store.players())[user_id]
            position = (await self._store.positions(user_id)).get(symbol) or {"qty": "0", "cost": "0"}
            held, cost = _d(position["qty"]), _d(position["cost"])
            if order["side"] == "buy":
                held, cost = held + new_qty, cost + new_cash
                player["cash"] = str(_d(player["cash"]) - new_cash)
            else:
                sold = min(new_qty, held)
                cost -= _cents(cost * sold / held) if held else cost
                held -= sold
                player["cash"] = str(_d(player["cash"]) + new_cash)
            await self._store.save_player(user_id, player)
            gone = held < _SHARE
            await self._store.save_position(user_id, symbol, None if gone else {"qty": str(held), "cost": str(cost)})
            order["filled_qty"], order["filled_cash"] = str(filled), str(filled_cash)
            logger.info("[paper] #%s %s: %s %s %s filled for %s", order["no"], user_id, order["side"],
                        _qty(new_qty), symbol, money(new_cash))
        order["status"] = str(remote.get("status") or order.get("status") or "new")
        if order["status"] in FINAL_STATUSES:
            await self._store.finish(order)
        else:
            await self._store.save_open(order)
        return order

    # -- trading --------------------------------------------------------------------

    @staticmethod
    def _cash_set_aside(orders: List[Order], user_id: int) -> Decimal:
        """Cash this player's open buys still hold."""
        return sum((max(Decimal(0), _d(o["reserved"]) - _d(o.get("filled_cash")))
                    for o in orders if o["user_id"] == user_id and o["side"] == "buy"), Decimal(0))

    @staticmethod
    def _shares_set_aside(orders: List[Order], user_id: int, symbol: str) -> Decimal:
        """Shares of ``symbol`` this player's open sells still hold."""
        return sum((_d(o["qty"]) - _d(o.get("filled_qty")) for o in orders
                    if o["user_id"] == user_id and o["side"] == "sell" and o["symbol"] == symbol), Decimal(0))

    async def _closing_soon(self) -> bool:
        clock = await self.client.clock()
        if not clock.get("is_open"):
            return False
        try:
            closes = datetime.fromisoformat(str(clock.get("next_close")))
        except ValueError:
            return False
        return closes - self._now() <= EXPIRY_SELL_BEFORE_CLOSE

    def _today(self) -> date:
        return self._now().astimezone(MARKET_ZONE).date()

    async def trade(
        self, user_id: int, side: str, symbol: str, *, quantity: float = 0, dollars: float = 0, limit_price: float = 0,
    ) -> str:
        """Place a buy or sell for ``user_id`` and say what happened.

        Raises ``PaperTradeError`` for a trade the game refuses and ``AlpacaError`` for one Alpaca does.
        """
        async with self._lock:
            return await self._trade(user_id, side, clean_symbol(symbol), _d(quantity), _d(dollars), _d(limit_price))

    async def _trade(
        self, user_id: int, side: str, symbol: str, quantity: Decimal, dollars: Decimal, limit: Decimal,
    ) -> str:
        contract = parse_option(symbol)
        if contract is None and not is_stock_symbol(symbol):
            raise PaperTradeError(f"'{symbol}' isn't a stock ticker or an option symbol like AAPL261120C00230000.")
        if side not in ("buy", "sell") or min(quantity, dollars, limit) < 0:
            raise PaperTradeError("Give a side of buy or sell, and amounts that aren't negative.")
        quantity, dollars = quantity.quantize(_SHARE), _cents(dollars)
        limit = limit.quantize(_CENT if limit >= 1 else Decimal("0.0001"))
        if contract and quantity != quantity.to_integral_value():
            raise PaperTradeError("Options trade in whole contracts.")
        if dollars and (contract or limit or side == "sell"):
            raise PaperTradeError("A dollar amount only works for buying a stock at market. Give a quantity instead.")

        await self._sync()
        players = await self._store.players()
        open_orders = await self._store.open_orders()
        player = players.get(user_id)
        reserved = Decimal(0)
        joined = False

        if side == "buy":
            if bool(quantity) == bool(dollars):
                raise PaperTradeError("Give either a quantity or a dollar amount to buy.")
            if player is None and len(players) >= MAX_PLAYERS:
                raise PaperTradeError(f"The game is full: all {MAX_PLAYERS} seats are taken.")
            if contract:
                await self._check_buyable(contract)
            price = (await self.client.prices([symbol])).get(symbol)
            if price is None and (contract is None or not limit):
                raise PaperTradeError(
                    f"There's no quote for {symbol}. " + ("Check the symbol against an option chain, or give a "
                                                          "limit price." if contract else "Check the ticker."))
            if dollars:
                reserved = dollars
            elif limit:
                reserved = _cents(limit * quantity * _multiplier(symbol))
            else:
                assert price is not None
                reserved = _cents(price * quantity * _multiplier(symbol) * (OPTION_BUFFER if contract else STOCK_BUFFER))
            cash = _d(player["cash"]) if player else ALLOWANCE
            free = cash - self._cash_set_aside(open_orders, user_id)
            if reserved > free:
                raise PaperTradeError(
                    f"That needs {money(reserved)} set aside" + ("" if dollars or limit else " (the current price "
                    "plus a buffer, since a market order fills at whatever the price is)") +
                    f", and only {money(free)} of the player's cash is free.")
            if player is None:
                player = {"cash": str(ALLOWANCE), "joined": self._now().timestamp()}
                await self._store.save_player(user_id, player)
                joined = True
                logger.info("[paper] %s joined the game", user_id)
        else:
            position = (await self._store.positions(user_id)).get(symbol) if player else None
            free_shares = _d(position["qty"]) - self._shares_set_aside(open_orders, user_id, symbol) if position else Decimal(0)
            if free_shares <= 0:
                raise PaperTradeError(
                    f"The player has no {symbol} free to sell" + (" (it's all in open sell orders)." if position else
                                                                 ". The game is long-only: no short selling."))
            quantity = quantity or free_shares
            if quantity > free_shares:
                raise PaperTradeError(f"The player only has {_units(symbol, free_shares)} of {symbol} free to sell.")

        number = await self._store.next_order_no()
        request: Dict[str, Any] = {
            "symbol": symbol, "side": side, "type": "limit" if limit else "market", "time_in_force": "day",
            "client_order_id": f"cmwgpt-{user_id}-{number}",
        }
        if dollars:
            request["notional"] = str(dollars)
        else:
            request["qty"] = _qty(quantity)
        if limit:
            request["limit_price"] = str(limit)
        placed = await self.client.submit_order(request)
        order: Order = {
            "no": number, "alpaca_id": placed["id"], "user_id": user_id, "symbol": symbol, "side": side,
            "qty": str(quantity), "dollars": str(dollars), "limit": str(limit), "reserved": str(reserved),
            "filled_qty": "0", "filled_cash": "0", "status": "new", "placed": self._now().timestamp(),
        }
        order = await self._apply(order, placed)
        order = await self._await_fill(order)

        lines = [("The player just joined the game with " + money(ALLOWANCE) + ". ") if joined else ""]
        lines[0] += _describe_order(order)
        if order["status"] not in FINAL_STATUSES:
            clock = await self.client.clock()
            lines.append(
                "It's still open" + ("" if clock.get("is_open") else ": the market is closed, so it waits for the "
                                     f"next open ({clock.get('next_open') or 'time unknown'})")
                + f". It's a day order. Cancel it with order number {order['no']}; its status shows in the portfolio.")
        cash = _d((await self._store.players())[user_id]["cash"])
        lines.append(f"Cash now {money(cash)}.")
        return "\n".join(lines)

    async def _check_buyable(self, contract: OptionContract) -> None:
        today = self._today()
        if contract.expiration < today:
            raise PaperTradeError(f"{contract.symbol} already expired.")
        if contract.expiration == today and await self._closing_soon():
            raise PaperTradeError(f"{contract.symbol} expires at today's close; it's too late to buy it.")

    async def _await_fill(self, order: Order) -> Order:
        for _ in range(FILL_CHECKS):
            if order["status"] in FINAL_STATUSES:
                break
            await self._sleep(1.0)
            try:
                remote = await self.client.get_order(order["alpaca_id"])
            except AlpacaError:
                break
            if remote:
                order = await self._apply(order, remote)
        return order

    async def cancel(self, user_id: int, order_no: int) -> str:
        """Cancel one of ``user_id``'s open orders."""
        async with self._lock:
            await self._sync()
            order = next((o for o in await self._store.open_orders()
                          if o["no"] == order_no and o["user_id"] == user_id), None)
            if order is None:
                raise PaperTradeError(f"The player has no open order #{order_no}; it may have filled or ended already.")
            await self.client.cancel_order(order["alpaca_id"])
            order = await self._await_fill(order)
            if order["status"] not in FINAL_STATUSES:
                return f"Asked Alpaca to cancel it; not confirmed yet. {_describe_order(order)}"
            return f"Done. {_describe_order(order)}"

    # -- option expiration ------------------------------------------------------------

    async def _handle_expirations(self) -> None:
        today = self._today()
        closing_soon: Optional[bool] = None
        for user_id in await self._store.players():
            for symbol, position in (await self._store.positions(user_id)).items():
                contract = parse_option(symbol)
                if contract is None or contract.expiration > today:
                    continue
                mine = [o for o in await self._store.open_orders()
                        if o["user_id"] == user_id and o["symbol"] == symbol]
                if contract.expiration < today:
                    if not mine:
                        await self._store.save_position(user_id, symbol, None)
                        await self._store.add_done(user_id, {"note": f"{_name(symbol)}: "
                                                   f"{_units(symbol, _d(position['qty']))} expired unsold, written off at $0"})
                        logger.warning("[paper] %s's %s expired unsold; written off", user_id, symbol)
                    continue
                if closing_soon is None:
                    closing_soon = await self._closing_soon()
                if closing_soon and not any(o.get("auto") for o in mine):
                    await self._sell_before_expiration(user_id, symbol, _d(position["qty"]), mine)

    async def _sell_before_expiration(self, user_id: int, symbol: str, quantity: Decimal, mine: List[Order]) -> None:
        try:
            # Their own orders on it hold the contracts, so those go first. If Alpaca hasn't let go of them
            # yet the sell is refused, and the next check tries again.
            for order in mine:
                await self.client.cancel_order(order["alpaca_id"])
            number = await self._store.next_order_no()
            placed = await self.client.submit_order({
                "symbol": symbol, "side": "sell", "type": "market", "time_in_force": "day", "qty": _qty(quantity),
                "client_order_id": f"cmwgpt-{user_id}-{number}",
            })
        except AlpacaError as e:
            logger.warning("[paper] Couldn't sell %s's expiring %s, will retry: %s", user_id, symbol, e)
            return
        await self._apply({
            "no": number, "alpaca_id": placed["id"], "user_id": user_id, "symbol": symbol, "side": "sell",
            "qty": str(quantity), "dollars": "0", "limit": "0", "reserved": "0", "filled_qty": "0",
            "filled_cash": "0", "status": "new", "placed": self._now().timestamp(), "auto": True,
        }, placed)
        logger.info("[paper] Selling %s's expiring %s", user_id, symbol)

    # -- reading the game -----------------------------------------------------------

    async def _player_report(
        self, user_id: int, player: Dict[str, Any], open_orders: List[Order], marks: Dict[str, Decimal],
        positions: Dict[str, Dict[str, Any]], *, detail: bool,
    ) -> Tuple[Decimal, str]:
        """A player's total worth and their report: one line for the leaderboard, or everything."""
        cash = _d(player["cash"])
        worth = cash
        holdings = []
        for symbol, position in positions.items():
            held, cost = _d(position["qty"]), _d(position["cost"])
            mark = marks.get(symbol)
            # Without a quote a holding counts for what was paid.
            value = _cents(mark * held * _multiplier(symbol)) if mark else cost
            worth += value
            average = cost / held / _multiplier(symbol)
            holdings.append((value, f"{_name(symbol)}: {_units(symbol, held)}, avg cost {money(average)}, "
                             + (f"now {money(mark)}, worth {money(value)} ({_percent(value, cost)})" if mark
                                else f"no quote, counted at cost {money(cost)}")))
        head = f"<@{user_id}>: worth {money(worth)} ({_percent(worth, ALLOWANCE)} on {money(ALLOWANCE)})"
        if not detail:
            top = ", ".join(line.split(":")[0] for _, line in sorted(holdings, key=lambda h: -h[0])[:5])
            return worth, f"{head} · cash {money(cash)} · " + (f"holds {top}" if top else "no holdings")

        mine = [o for o in open_orders if o["user_id"] == user_id]
        set_aside = self._cash_set_aside(open_orders, user_id)
        lines = [head, f"Cash: {money(cash)}" + (f" ({money(cash - set_aside)} free, the rest set aside for open buys)"
                                                 if set_aside else "")]
        lines += ["Holdings:"] + [f"- {line}" for _, line in holdings] if holdings else ["Holdings: none"]
        if mine:
            lines += ["Open orders:"] + [f"- {_describe_order(o)}" for o in mine]
        done = await self._store.done_orders(user_id)
        if done:
            lines += ["Recent finished orders, newest first:"] + [
                f"- {d['note'] if 'note' in d else _describe_order(d)}" for d in done]
        return worth, "\n".join(lines)

    async def portfolio(self, user_id: int) -> str:
        """One player's cash, holdings and orders, or "" if they haven't joined."""
        async with self._lock:
            await self._sync()
            player = (await self._store.players()).get(user_id)
            if player is None:
                return ""
            positions = await self._store.positions(user_id)
            marks = await self.client.prices(list(positions))
            _, text = await self._player_report(
                user_id, player, await self._store.open_orders(), marks, positions, detail=True)
            return text

    async def leaderboard(self) -> str:
        """Every player ranked by what they're worth, the seats left, and whether the ledger matches Alpaca."""
        async with self._lock:
            await self._sync()
            players = await self._store.players()
            holdings = {uid: await self._store.positions(uid) for uid in players}
            open_orders = await self._store.open_orders()
            marks = await self.client.prices(sorted({s for positions in holdings.values() for s in positions}))
            reports = [await self._player_report(uid, player, open_orders, marks, holdings[uid], detail=False)
                       for uid, player in players.items()]
            seats = MAX_PLAYERS - len(players)
            lines = [f"{rank}. {text}" for rank, (_, text) in enumerate(sorted(reports, key=lambda r: -r[0]), 1)]
            lines.append(f"{len(players)} of {MAX_PLAYERS} seats taken; {money(seats * ALLOWANCE)} unassigned. "
                         "Someone joins by asking for their first trade.")
            lines.append(await self._drift(holdings))
            return "\n".join(lines)

    async def _drift(self, holdings: Dict[int, Dict[str, Dict[str, Any]]]) -> str:
        """Whether the players' holdings add up to what the Alpaca account holds."""
        ours: Dict[str, Decimal] = {}
        for positions in holdings.values():
            for symbol, position in positions.items():
                ours[symbol] = ours.get(symbol, Decimal(0)) + _d(position["qty"])
        try:
            theirs = {str(p.get("symbol")): _d(p.get("qty")) for p in await self.client.positions()}
        except AlpacaError as e:
            return f"Couldn't compare the ledger with the Alpaca account: {e}"
        off = [f"{s} (ledger {_qty(ours.get(s, Decimal(0)))}, Alpaca {_qty(theirs.get(s, Decimal(0)))})"
               for s in sorted(set(ours) | set(theirs))
               if abs(ours.get(s, Decimal(0)) - theirs.get(s, Decimal(0))) > Decimal("0.000001")]
        if off:
            return ("The ledger doesn't match the Alpaca account for: " + ", ".join(off)
                    + ". A stock split, or a trade made outside the bot, does that; it needs fixing by hand.")
        return "The ledger matches the Alpaca account's holdings."

    async def reset(self) -> str:
        """End the game: cancel every order, sell everything at Alpaca and forget every player."""
        async with self._lock:
            await self.client.close_everything()
            await self._store.wipe()
            logger.warning("[paper] The game was reset")
            return ("The game is reset: every player and holding is forgotten and all 10 seats are open. Alpaca was "
                    "told to cancel every order and sell every position (when the market is closed the sells wait "
                    "for the open). The Alpaca account's own balance isn't put back to $1M; that's done from "
                    "Alpaca's dashboard.")
