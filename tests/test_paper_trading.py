"""Tests for the paper-trading game: its ledger, its rules and its tools."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import fakeredis

from src.services.alpaca_service import AlpacaError
from src.services.chat_tools import build_mention_tools
from src.services.paper_trading_service import PaperStore, PaperTradeError, PaperTradingService

# Thu 2026-11-12 11:00 ET
NOW = datetime(2026, 11, 12, 16, 0, tzinfo=timezone.utc)
CALL = "AAPL261120C00200000"
ALICE, BOB = 111111, 222222


class FakeAlpaca:
    """A paper account that fills market orders at the current price as soon as they're placed."""

    def __init__(self) -> None:
        self.marks: Dict[str, Decimal] = {"AAPL": Decimal("200"), CALL: Decimal("3")}
        self.is_open = True
        self.next_close = "2026-11-12T16:00:00-05:00"
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.refuse: Optional[str] = None
        self.closed_everything = False

    async def prices(self, symbols: List[str]) -> Dict[str, Decimal]:
        return {s: self.marks[s] for s in symbols if s in self.marks}

    async def clock(self) -> Dict[str, Any]:
        return {"is_open": self.is_open, "next_open": "2026-11-13T09:30:00-05:00", "next_close": self.next_close}

    async def submit_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        if self.refuse:
            raise AlpacaError(self.refuse)
        placed = {**order, "id": f"alpaca-{len(self.orders) + 1}", "status": "accepted", "filled_qty": "0",
                  "filled_avg_price": None}
        self.orders[placed["id"]] = placed
        if self.is_open and order["type"] == "market":
            self.fill(placed["id"])
        return dict(placed)

    def fill(self, order_id: str, price: Optional[Decimal] = None) -> None:
        order = self.orders[order_id]
        price = price or self.marks[order["symbol"]]
        quantity = Decimal(order["qty"]) if "qty" in order else Decimal(order["notional"]) / price
        order.update(status="filled", filled_qty=str(quantity), filled_avg_price=str(price))

    async def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        return dict(self.orders[order_id]) if order_id in self.orders else None

    async def cancel_order(self, order_id: str) -> None:
        if self.orders[order_id]["status"] == "filled":
            raise AlpacaError("order is already filled")
        self.orders[order_id]["status"] = "canceled"

    async def positions(self) -> List[Dict[str, Any]]:
        held: Dict[str, Decimal] = {}
        for order in self.orders.values():
            sign = 1 if order["side"] == "buy" else -1
            held[order["symbol"]] = held.get(order["symbol"], Decimal(0)) + sign * Decimal(order["filled_qty"])
        return [{"symbol": s, "qty": str(q)} for s, q in held.items() if q]

    async def close_everything(self) -> None:
        self.closed_everything = True


class GameCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        self.alpaca = FakeAlpaca()
        self.now = [NOW]

        async def no_sleep(_seconds: float) -> None:
            return None

        self.game = PaperTradingService(
            self.alpaca,  # type: ignore[arg-type]
            store=PaperStore(redis_getter=lambda: self.redis), now=lambda: self.now[0], sleep=no_sleep)
        self.store = PaperStore(redis_getter=lambda: self.redis)

    async def cash(self, user_id: int) -> Decimal:
        return Decimal((await self.store.players())[user_id]["cash"])


class TestTrading(GameCase):
    async def test_first_trade_joins_with_the_allowance(self):
        self.assertEqual(await self.game.portfolio(ALICE), "")
        text = await self.game.trade(ALICE, "buy", "aapl", quantity=10)
        self.assertIn("just joined the game with $100,000.00", text)
        self.assertIn("#1 buy 10 shares AAPL (market): filled 10 shares at $200.00 = $2,000.00", text)
        self.assertEqual(await self.cash(ALICE), Decimal("98000.00"))
        self.assertEqual((await self.store.positions(ALICE))["AAPL"], {"qty": "10", "cost": "2000.00"})

    async def test_buying_by_dollars(self):
        await self.game.trade(ALICE, "buy", "AAPL", dollars=5000)
        self.assertEqual(self.alpaca.orders["alpaca-1"]["notional"], "5000.00")
        self.assertEqual(await self.cash(ALICE), Decimal("95000.00"))
        self.assertEqual(Decimal((await self.store.positions(ALICE))["AAPL"]["qty"]), Decimal("25"))

    async def test_players_cannot_spend_more_than_their_own_cash(self):
        with self.assertRaises(PaperTradeError) as caught:
            await self.game.trade(ALICE, "buy", "AAPL", quantity=500)  # $100k + the buffer
        self.assertIn("only $100,000.00", str(caught.exception))
        self.assertEqual(await self.store.players(), {})  # a refused first trade doesn't take a seat
        self.assertEqual(self.alpaca.orders, {})

    async def test_open_buys_hold_their_cash(self):
        await self.game.trade(ALICE, "buy", "AAPL", quantity=300, limit_price=190)  # $57,000 set aside
        with self.assertRaises(PaperTradeError):
            await self.game.trade(ALICE, "buy", "AAPL", quantity=300, limit_price=190)
        self.assertEqual(await self.cash(ALICE), Decimal("100000"))
        self.assertIn("$43,000.00 free", await self.game.portfolio(ALICE))

    async def test_a_limit_order_fills_later_at_alpacas_price(self):
        text = await self.game.trade(ALICE, "buy", "AAPL", quantity=10, limit_price=190)
        self.assertIn("It's still open. It's a day order.", text)
        self.alpaca.fill("alpaca-1", Decimal("189.50"))
        report = await self.game.portfolio(ALICE)
        self.assertIn("#1 buy 10 shares AAPL (limit $190.00): filled 10 shares at $189.50 = $1,895.00", report)
        self.assertEqual(await self.cash(ALICE), Decimal("98105.00"))
        self.assertEqual(await self.store.open_orders(), [])

    async def test_a_partial_fill_is_only_counted_once(self):
        await self.game.trade(ALICE, "buy", "AAPL", quantity=10, limit_price=190)
        self.alpaca.orders["alpaca-1"].update(status="partially_filled", filled_qty="4", filled_avg_price="190")
        await self.game.tick()
        await self.game.tick()
        self.assertEqual(await self.cash(ALICE), Decimal("99240.00"))
        self.alpaca.fill("alpaca-1", Decimal("190"))
        await self.game.tick()
        self.assertEqual(await self.cash(ALICE), Decimal("98100.00"))
        self.assertEqual(Decimal((await self.store.positions(ALICE))["AAPL"]["qty"]), Decimal("10"))

    async def test_orders_wait_when_the_market_is_closed(self):
        self.alpaca.is_open = False
        text = await self.game.trade(ALICE, "buy", "AAPL", quantity=10)
        self.assertIn("the market is closed, so it waits for the next open (2026-11-13T09:30:00-05:00)", text)
        self.assertEqual(await self.cash(ALICE), Decimal("100000"))

    async def test_selling_only_what_you_hold(self):
        await self.game.trade(ALICE, "buy", "AAPL", quantity=10)
        with self.assertRaises(PaperTradeError):
            await self.game.trade(ALICE, "sell", "AAPL", quantity=11)
        with self.assertRaises(PaperTradeError) as caught:
            await self.game.trade(BOB, "sell", "AAPL", quantity=1)  # Alice's shares aren't Bob's
        self.assertIn("long-only", str(caught.exception))
        self.alpaca.marks["AAPL"] = Decimal("210")
        text = await self.game.trade(ALICE, "sell", "AAPL")  # no quantity: all of it
        self.assertIn("sell 10 shares AAPL (market): filled 10 shares at $210.00 = $2,100.00", text)
        self.assertEqual(await self.cash(ALICE), Decimal("100100.00"))
        self.assertEqual(await self.store.positions(ALICE), {})

    async def test_shares_in_an_open_sell_cannot_be_sold_again(self):
        await self.game.trade(ALICE, "buy", "AAPL", quantity=10)
        await self.game.trade(ALICE, "sell", "AAPL", quantity=10, limit_price=250)
        with self.assertRaises(PaperTradeError):
            await self.game.trade(ALICE, "sell", "AAPL", quantity=1)

    async def test_cancelling(self):
        await self.game.trade(ALICE, "buy", "AAPL", quantity=10, limit_price=190)
        with self.assertRaises(PaperTradeError):
            await self.game.cancel(BOB, 1)  # not Bob's order
        self.assertIn("#1 buy 10 shares AAPL (limit $190.00): canceled, nothing filled", await self.game.cancel(ALICE, 1))
        self.assertEqual(await self.store.open_orders(), [])

    async def test_alpaca_refusing_changes_nothing(self):
        await self.game.trade(ALICE, "buy", "AAPL", quantity=1)
        self.alpaca.refuse = "asset is not tradable"
        with self.assertRaises(AlpacaError):
            await self.game.trade(ALICE, "buy", "AAPL", quantity=1)
        self.assertEqual(await self.cash(ALICE), Decimal("99800.00"))
        self.assertEqual(await self.store.open_orders(), [])

    async def test_the_eleventh_player_is_turned_away(self):
        for user_id in range(1, 11):
            await self.game.trade(user_id, "buy", "AAPL", quantity=1)
        with self.assertRaises(PaperTradeError) as caught:
            await self.game.trade(ALICE, "buy", "AAPL", quantity=1)
        self.assertIn("full", str(caught.exception))

    async def test_bad_requests(self):
        for kwargs in ({"quantity": 1, "dollars": 100}, {}, {"dollars": 100, "limit_price": 5}):
            with self.assertRaises(PaperTradeError):
                await self.game.trade(ALICE, "buy", "AAPL", **kwargs)
        with self.assertRaises(PaperTradeError):
            await self.game.trade(ALICE, "buy", "NOPE", quantity=1)  # no quote
        with self.assertRaises(PaperTradeError):
            await self.game.trade(ALICE, "buy", "not a ticker!", quantity=1)


class TestOptions(GameCase):
    async def test_a_contract_is_a_hundred_shares(self):
        text = await self.game.trade(ALICE, "buy", CALL, quantity=2)
        self.assertIn("buy 2 contracts", text)
        self.assertIn("filled 2 contracts at $3.00 = $600.00", text)
        self.assertEqual(await self.cash(ALICE), Decimal("99400.00"))
        with self.assertRaises(PaperTradeError):
            await self.game.trade(ALICE, "buy", CALL, quantity=1.5)
        with self.assertRaises(PaperTradeError):
            await self.game.trade(ALICE, "buy", CALL, dollars=500)
        self.assertIn(f"{CALL} (AAPL $200 call exp 2026-11-20): 2 contracts, avg cost $3.00, now $3.00, worth $600.00",
                      await self.game.portfolio(ALICE))

    async def test_sold_before_the_close_on_expiration_day(self):
        await self.game.trade(ALICE, "buy", CALL, quantity=2)
        await self.game.trade(ALICE, "sell", CALL, quantity=2, limit_price=9)
        self.now[0] = datetime(2026, 11, 20, 20, 0, tzinfo=timezone.utc)  # 3:00pm ET on expiration day
        self.alpaca.next_close = "2026-11-20T16:00:00-05:00"
        await self.game.tick()
        self.assertEqual(len(self.alpaca.orders), 2)  # an hour before the close: left alone

        self.now[0] = datetime(2026, 11, 20, 20, 35, tzinfo=timezone.utc)
        self.alpaca.marks[CALL] = Decimal("1")
        await self.game.tick()
        await self.game.tick()
        self.assertEqual(self.alpaca.orders["alpaca-2"]["status"], "canceled")  # their own limit sell made way
        self.assertEqual(len(self.alpaca.orders), 3)  # and it was sold once
        self.assertEqual(await self.store.positions(ALICE), {})
        self.assertEqual(await self.cash(ALICE), Decimal("99600.00"))
        self.assertIn("[sold automatically before expiration]", await self.game.portfolio(ALICE))
        with self.assertRaises(PaperTradeError):
            await self.game.trade(ALICE, "buy", CALL, quantity=1)  # too late to buy

    async def test_one_that_expired_unsold_is_written_off(self):
        await self.game.trade(ALICE, "buy", CALL, quantity=2)
        self.now[0] = datetime(2026, 11, 23, 16, 0, tzinfo=timezone.utc)
        await self.game.tick()
        self.assertEqual(await self.store.positions(ALICE), {})
        self.assertEqual(await self.cash(ALICE), Decimal("99400.00"))
        self.assertIn("2 contracts expired unsold, written off at $0", await self.game.portfolio(ALICE))


class TestReading(GameCase):
    async def test_leaderboard(self):
        await self.game.trade(ALICE, "buy", "AAPL", quantity=100)
        await self.game.trade(BOB, "buy", CALL, quantity=10)
        self.alpaca.marks.update({"AAPL": Decimal("220"), CALL: Decimal("2")})
        lines = (await self.game.leaderboard()).splitlines()
        self.assertEqual(lines[0], f"1. <@{ALICE}>: worth $102,000.00 (+2.00% on $100,000.00) · cash $80,000.00 · "
                                   "holds AAPL")
        self.assertTrue(lines[1].startswith(f"2. <@{BOB}>: worth $99,000.00 (-1.00% on $100,000.00)"))
        self.assertEqual(lines[2], "2 of 10 seats taken; $800,000.00 unassigned. Someone joins by asking for their "
                                   "first trade.")
        self.assertEqual(lines[3], "The ledger matches the Alpaca account's holdings.")

    async def test_leaderboard_flags_a_ledger_that_is_off(self):
        await self.game.trade(ALICE, "buy", "AAPL", quantity=100)
        await self.store.save_position(ALICE, "AAPL", {"qty": "50", "cost": "10000"})
        self.assertIn("doesn't match the Alpaca account for: AAPL (ledger 50, Alpaca 100)",
                      await self.game.leaderboard())

    async def test_reset(self):
        await self.game.trade(ALICE, "buy", "AAPL", quantity=100)
        await self.game.reset()
        self.assertTrue(self.alpaca.closed_everything)
        self.assertEqual(await self.store.players(), {})
        self.assertEqual(await self.store.positions(ALICE), {})


class TestTools(GameCase):
    def tools(self, user_id: int, *, admin: bool = False) -> Dict[str, Any]:
        author = SimpleNamespace(id=user_id, guild_permissions=SimpleNamespace(administrator=admin))
        message = SimpleNamespace(id=1, guild=None, author=author, channel=SimpleNamespace(id=5))
        built = build_mention_tools(
            guild_id=None, channel_id=5, user_id=user_id, user_notes_provider=None, how_i_work_loader=lambda: "",
            state_service=None, gemini_service=None, default_image_model=None, message=message,
            alpaca=MagicMock(), paper_trading=self.game)
        return {t.definition["name"]: t.run for t in built}

    def trade_args(self, **changes: Any) -> Dict[str, Any]:
        return {"action": "buy", "symbol": "AAPL", "quantity": 0, "dollars": 0, "limit_price": 0,
                "order_number": 0, **changes}

    async def test_trades_are_for_whoever_asked(self):
        tools = self.tools(ALICE)
        result = await tools["paper_trade"](self.trade_args(quantity=10))
        self.assertFalse(result.is_error)
        self.assertEqual(list(await self.store.players()), [ALICE])
        mine = await tools["paper_portfolio"]({"view": "player", "user_id": ""})
        self.assertIn(f"<@{ALICE}>: worth $100,000.00", mine.content[0]["text"])
        theirs = await self.tools(BOB)["paper_portfolio"]({"view": "player", "user_id": str(ALICE)})
        self.assertIn("AAPL: 10 shares", theirs.content[0]["text"])
        nobody = await self.tools(BOB)["paper_portfolio"]({"view": "player", "user_id": ""})
        self.assertIn(f"<@{BOB}> isn't in the game yet", nobody.content[0]["text"])

    async def test_a_refused_trade_is_an_error(self):
        result = await self.tools(ALICE)["paper_trade"](self.trade_args(action="sell", quantity=1))
        self.assertTrue(result.is_error)
        self.assertTrue(result.content[0]["text"].startswith("Nothing was traded."))

    async def test_only_admins_reset(self):
        await self.tools(ALICE)["paper_trade"](self.trade_args(quantity=10))
        refused = await self.tools(ALICE)["paper_trade"](self.trade_args(action="reset_game", symbol=""))
        self.assertTrue(refused.is_error)
        self.assertEqual(list(await self.store.players()), [ALICE])
        await self.tools(BOB, admin=True)["paper_trade"](self.trade_args(action="reset_game", symbol=""))
        self.assertEqual(await self.store.players(), {})

    def test_tools_are_off_without_alpaca(self):
        built = build_mention_tools(
            guild_id=None, channel_id=5, user_id=ALICE, user_notes_provider=None, how_i_work_loader=lambda: "",
            state_service=None, gemini_service=None, default_image_model=None)
        names = [t.definition["name"] for t in built]
        self.assertNotIn("market_data", names)
        self.assertNotIn("paper_trade", names)
