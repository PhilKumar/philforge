"""High Entry's live order path: the replay decides, the book remembers.

The strategy REPLAYS its whole campaign from bars on every poll. That is right
for paper and it is the one thing that cannot be pointed at a broker unchanged
-- a second replay of the same trade must not buy it twice, and a trade that
disappears from a later replay must never be quietly sold.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from unittest.mock import patch

from engine.candle_recovery_live import RecoveryOrderBook, trade_key
from engine.options_live_executor import (
    ExecutionRefused,
    OptionsLiveExecutor,
    OptionsPaperExecutor,
    OrderRejected,
    build_executor,
)

WHEN = datetime(2026, 8, 30, 10, 15)


@dataclass
class FakeTrade:
    """What the replay hands the book."""

    trade_no: int
    armed_at: datetime
    entry_time: Optional[datetime] = WHEN
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    strike: Optional[int] = 24_400
    expiry: date = date(2026, 9, 3)
    quantity: int = 75
    entry_premium: Optional[float] = 200.0


class _Executor(OptionsPaperExecutor):
    """A paper executor that counts what it was asked to do."""

    def __init__(self):
        super().__init__(tag="TEST")
        self.buys: list = []
        self.sells: list = []
        self.released: list = []

    def buy(self, **kw):
        self.buys.append(kw)
        return super().buy(**kw)

    def sell(self, **kw):
        self.sells.append(kw)
        return super().sell(**kw)

    def cancel_bracket(self, *, order_id):
        self.released.append(str(order_id))
        return super().cancel_bracket(order_id=order_id)


def book():
    ex = _Executor()
    return RecoveryOrderBook(ex), ex


def sync(bk, trades, when=WHEN):
    return bk.sync("C1", trades, symbol="NIFTY", side="CE", when=when)


class OrderBookTests(unittest.TestCase):
    def test_an_open_trade_is_bought_once_however_often_it_replays(self):
        """The whole point. Every poll replays the same trade; the second,
        third and hundredth replay must not buy it again."""
        bk, ex = book()
        trade = FakeTrade(trade_no=1, armed_at=WHEN)
        for _ in range(5):
            sync(bk, [trade])
        self.assertEqual(len(ex.buys), 1)

    def test_the_key_survives_a_renumbered_replay(self):
        """A revised candle can renumber the list. The bar that armed the
        trade is a fact about the market, so identity hangs off that."""
        armed = datetime(2026, 8, 30, 9, 45)
        self.assertEqual(
            trade_key("C1", FakeTrade(trade_no=1, armed_at=armed)),
            trade_key("C1", FakeTrade(trade_no=1, armed_at=armed)),
        )
        self.assertNotEqual(
            trade_key("C1", FakeTrade(trade_no=1, armed_at=armed)),
            trade_key("C1", FakeTrade(trade_no=2, armed_at=armed)),
        )

    def test_a_trade_that_closed_before_the_book_ever_saw_it_is_not_bought(self):
        """History the app was down for. Buying it now would open a position
        the rules have already exited."""
        bk, ex = book()
        sync(bk, [FakeTrade(trade_no=1, armed_at=WHEN, exit_time=WHEN, exit_reason="stop")])
        self.assertEqual(ex.buys, [])

    def test_the_buy_carries_a_stop_inside_it(self):
        bk, ex = book()
        sync(bk, [FakeTrade(trade_no=1, armed_at=WHEN)])
        self.assertEqual(ex.buys[0]["stop_price"], 60.0, "70% under the 200 paid")
        record = bk.orders[trade_key("C1", FakeTrade(trade_no=1, armed_at=WHEN))]
        self.assertTrue(record["bracket_order_id"])

    def test_closing_the_trade_releases_the_bracket_before_selling(self):
        """A stop still working at Dhan would sell a position already gone."""
        bk, ex = book()
        trade = FakeTrade(trade_no=1, armed_at=WHEN)
        sync(bk, [trade])
        bracket = bk.orders[trade_key("C1", trade)]["bracket_order_id"]
        trade.exit_time = datetime(2026, 8, 30, 11, 0)
        trade.exit_reason = "target"
        sync(bk, [trade])
        self.assertEqual(ex.released, [bracket])
        self.assertEqual(len(ex.sells), 1)
        self.assertEqual(bk.orders[trade_key("C1", trade)]["state"], "CLOSED")

    def test_a_bracket_leg_that_already_traded_books_instead_of_selling_again(self):
        class _AlreadyTraded(_Executor):
            def cancel_bracket(self, *, order_id):
                return {"order_id": str(order_id), "traded": True, "avg_price": 58.0}

        ex = _AlreadyTraded()
        bk = RecoveryOrderBook(ex)
        trade = FakeTrade(trade_no=1, armed_at=WHEN)
        sync(bk, [trade])
        trade.exit_time = datetime(2026, 8, 30, 11, 0)
        sync(bk, [trade])
        self.assertEqual(ex.sells, [], "the stop already sold it")
        record = bk.orders[trade_key("C1", trade)]
        self.assertEqual(record["state"], "CLOSED")
        self.assertEqual(record["exit_price"], 58.0)

    def test_a_trade_that_vanishes_freezes_the_book_and_sells_nothing(self):
        """The vendor revised a candle under us. An order nobody can explain
        is not one a program should be closing."""
        bk, ex = book()
        trade = FakeTrade(trade_no=1, armed_at=WHEN)
        sync(bk, [trade])
        outcome = sync(bk, [])  # the replay no longer reports it
        self.assertTrue(outcome["frozen"])
        self.assertTrue(bk.frozen)
        self.assertEqual(ex.sells, [], "nothing is sold on a disagreement")

    def test_a_frozen_book_opens_nothing_new(self):
        bk, ex = book()
        first = FakeTrade(trade_no=1, armed_at=WHEN)
        sync(bk, [first])
        sync(bk, [])
        sync(bk, [FakeTrade(trade_no=2, armed_at=datetime(2026, 8, 30, 12, 0))])
        self.assertEqual(len(ex.buys), 1, "still only the first")

    def test_only_a_human_thaws_it(self):
        bk, ex = book()
        trade = FakeTrade(trade_no=1, armed_at=WHEN)
        sync(bk, [trade])
        sync(bk, [])
        self.assertTrue(bk.frozen)
        self.assertEqual(bk.clear_freeze(WHEN), 1)
        self.assertFalse(bk.frozen)

    def test_an_unknown_exit_freezes_rather_than_guesses(self):
        class _Unknown(_Executor):
            def sell(self, **kw):
                self.sells.append(kw)
                return {"order_id": "X1", "status": "UNKNOWN", "avg_price": None}

        ex = _Unknown()
        bk = RecoveryOrderBook(ex)
        trade = FakeTrade(trade_no=1, armed_at=WHEN)
        sync(bk, [trade])
        trade.exit_time = datetime(2026, 8, 30, 11, 0)
        sync(bk, [trade])
        self.assertTrue(bk.frozen)
        self.assertEqual(bk.orders[trade_key("C1", trade)]["state"], "EXIT_UNKNOWN")

    def test_a_refused_entry_records_no_order(self):
        """Recording an order the broker never took is how a book starts
        lying. A refusal can be retried; a phantom cannot be undone."""

        class _Refusing(_Executor):
            def buy(self, **kw):
                raise ExecutionRefused("not armed")

        bk = RecoveryOrderBook(_Refusing())
        sync(bk, [FakeTrade(trade_no=1, armed_at=WHEN)])
        self.assertEqual(bk.orders, {})
        self.assertFalse(bk.frozen)

    def test_the_book_survives_a_restart(self):
        bk, _ = book()
        trade = FakeTrade(trade_no=1, armed_at=WHEN)
        sync(bk, [trade])
        back = RecoveryOrderBook(_Executor())
        back.load(bk.to_dict())
        self.assertEqual(back.orders, bk.orders)
        # And the restored book does not re-buy what it already holds.
        sync(back, [trade])
        self.assertEqual(back.executor.buys, [])


class SharedExecutorTests(unittest.TestCase):
    """The executor four strategies will share. Closed until proven."""

    def test_live_is_refused_while_the_gate_is_shut(self):
        live = OptionsLiveExecutor(broker=object(), symbol="NIFTY", armed=True)
        with self.assertRaisesRegex(ExecutionRefused, "built but disabled"):
            live.buy(when=WHEN, strike=24_400, expiry=date(2026, 9, 3), option_type="CE", quantity=75, premium=200.0)

    def test_armed_is_still_required_once_the_gate_opens(self):
        live = OptionsLiveExecutor(broker=object(), symbol="NIFTY")
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            with self.assertRaisesRegex(ExecutionRefused, "not armed"):
                live.buy(
                    when=WHEN, strike=24_400, expiry=date(2026, 9, 3), option_type="CE", quantity=75, premium=200.0
                )

    def test_a_bracketed_buy_is_one_super_order_with_a_placeholder_target(self):
        sent = {}

        class _Broker:
            def place_super_order(self, **order):
                sent.update(order)
                return {"orderId": "SO-1"}

            def get_super_orders(self):
                # A super order is NOT in the ordinary order book. Asking the
                # wrong one reads UNKNOWN for an entry that filled.
                return [
                    {
                        "orderId": "SO-1",
                        "orderStatus": "TRADED",
                        "filledQty": 75,
                        "averageTradedPrice": 198.5,
                    }
                ]

            def verify_order_fill(self, order_id, max_wait_sec=20):
                raise AssertionError("a bracketed entry must be read from the super order book")

        live = OptionsLiveExecutor(broker=_Broker(), symbol="NIFTY", armed=True, tag="PF_HIGH_ENTRY")
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            receipt = live.buy(
                when=WHEN,
                strike=24_400,
                expiry=date(2026, 9, 3),
                option_type="CE",
                quantity=75,
                premium=200.0,
                stop_price=60.0,
            )
        self.assertEqual(receipt["bracket_order_id"], "SO-1")
        self.assertEqual(receipt["traded_premium"], 198.5)
        # NOT a decision, and NOT zero: Dhan refuses a super order with no
        # target leg, which is why live Scalp forces both exit prices in.
        # Ten times the entry is far outside anything a rule would ask for,
        # and `amend_bracket_target` replaces it once one is measurable.
        self.assertEqual(sent["target_price"], 2000.0)
        self.assertEqual(sent["stop_loss_price"], 60.0)
        self.assertEqual(sent["product_type"], "MARGIN")
        self.assertEqual(sent["tag"], "PF_HIGH_ENTRY_SO")

    def test_a_rejection_and_an_unknown_are_different_answers(self):
        class _Rejects:
            def place_option_order(self, **order):
                return {"orderId": "R-1"}

            def verify_order_fill(self, order_id, max_wait_sec=20):
                return {"status": "REJECTED", "filled_qty": 0, "message": "margin"}

        class _Silent:
            def place_option_order(self, **order):
                return {"orderId": "U-1"}

            def verify_order_fill(self, order_id, max_wait_sec=20):
                return {"status": "TIMEOUT", "filled_qty": 0}

        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            live = OptionsLiveExecutor(broker=_Rejects(), symbol="NIFTY", armed=True)
            with self.assertRaises(OrderRejected):
                live.buy(
                    when=WHEN, strike=24_400, expiry=date(2026, 9, 3), option_type="CE", quantity=75, premium=200.0
                )
            silent = OptionsLiveExecutor(broker=_Silent(), symbol="NIFTY", armed=True)
            with self.assertRaises(RuntimeError) as caught:
                silent.buy(
                    when=WHEN, strike=24_400, expiry=date(2026, 9, 3), option_type="CE", quantity=75, premium=200.0
                )
            self.assertNotIsInstance(caught.exception, OrderRejected)

    def test_build_executor_gives_paper_unless_live_is_asked_for(self):
        self.assertIsInstance(build_executor(object(), "NIFTY", mode="paper"), OptionsPaperExecutor)
        self.assertIsInstance(build_executor(object(), "NIFTY", mode="live"), OptionsLiveExecutor)


if __name__ == "__main__":
    unittest.main()


class GapCarryLiveTests(unittest.TestCase):
    """Gap Carry holds ONE leg across one night. The seam is its arm and its
    close, and the close must refuse to book a position it could not sell."""

    def engine(self, executor):
        from datetime import time as dt_time

        from engine.gap_carry import GapCarryConfig
        from engine.gap_carry_paper import GapCarryPaper

        return GapCarryPaper(
            config=GapCarryConfig(),
            option_premium_lookup=lambda when, strike, side, expiry: 200.0,
            expiry_lookup=lambda session: date(2026, 9, 3),
            lot_size_lookup=lambda expiry: 75,
            executor=executor,
        ), dt_time

    def armed(self, executor):
        """Arm one position without driving the whole signal machinery."""
        from engine.gap_carry import SignalReading

        eng, _ = self.engine(executor)
        signal = SignalReading(
            timestamp=datetime(2026, 8, 31, 15, 10),
            close=24_500.0,
            ema=24_480.0,
            rsi=61.0,
            side="CE",
            reason="test",
        )
        eng.last_index_close = 24_500.0
        eng._arm(date(2026, 8, 31), signal)
        return eng

    def test_a_live_arm_sends_one_bracketed_buy(self):
        ex = _Executor()
        eng = self.armed(ex)
        self.assertEqual(len(ex.buys), 1)
        self.assertEqual(ex.buys[0]["stop_price"], 60.0)
        self.assertIsNotNone(eng.position)
        self.assertTrue(eng.position.order_id)
        self.assertTrue(eng.position.bracket_order_id)

    def test_a_paper_arm_sends_nothing_and_carries_no_order_id(self):
        eng = self.armed(None)
        self.assertIsNotNone(eng.position)
        self.assertIsNone(eng.position.order_id)

    def test_a_refused_entry_leaves_no_position(self):
        class _Refusing(_Executor):
            def buy(self, **kw):
                raise ExecutionRefused("not armed")

        eng = self.armed(_Refusing())
        self.assertIsNone(eng.position, "nothing is held that was never bought")

    def test_an_unknown_entry_freezes_and_holds_no_position(self):
        class _Unknown(_Executor):
            def buy(self, **kw):
                raise RuntimeError("timeout")

        eng = self.armed(_Unknown())
        self.assertIsNone(eng.position)
        self.assertTrue(eng.frozen_reason)

    def test_the_exit_books_the_price_dhan_traded(self):
        class _Priced(_Executor):
            def sell(self, **kw):
                self.sells.append(kw)
                return {"order_id": "X1", "status": "FILLED", "avg_price": 244.0}

        ex = _Priced()
        eng = self.armed(ex)
        pos = eng.position
        eng._close(pos, datetime(2026, 9, 1, 9, 20), 250.0, "clock", priced=True)
        self.assertEqual(pos.exit_premium, 244.0, "the broker's price, not the quote")
        self.assertEqual(eng.position, None)

    def test_an_unknown_exit_does_not_retire_the_position(self):
        """A book that marks a position closed while its order may still be
        working is a book that lies about real money."""

        class _Unknown(_Executor):
            def sell(self, **kw):
                self.sells.append(kw)
                return {"order_id": "X1", "status": "UNKNOWN", "avg_price": None}

        eng = self.armed(_Unknown())
        pos = eng.position
        eng._close(pos, datetime(2026, 9, 1, 9, 20), 250.0, "clock", priced=True)
        self.assertIs(eng.position, pos, "still held")
        self.assertEqual(eng.history, [])
        self.assertTrue(eng.frozen_reason)

    def test_the_bracket_is_released_before_the_leg_is_sold(self):
        ex = _Executor()
        eng = self.armed(ex)
        pos = eng.position
        bracket = pos.bracket_order_id
        eng._close(pos, datetime(2026, 9, 1, 9, 20), 250.0, "clock", priced=True)
        self.assertEqual(ex.released, [bracket])
        self.assertEqual(len(ex.sells), 1)


class SupertrendLiveTests(unittest.TestCase):
    """Same shape as Gap Carry: one leg, an arm and a close."""

    def armed(self, executor):
        from engine.supertrend_entry import SupertrendConfig
        from engine.supertrend_paper import SupertrendPaper

        eng = SupertrendPaper(
            config=SupertrendConfig(),
            option_premium_lookup=lambda when, strike, side, expiry: 200.0,
            expiry_lookup=lambda session: date(2026, 9, 3),
            lot_size_lookup=lambda expiry: 75,
            executor=executor,
        )
        eng.last_index_close = 24_500.0
        eng._arm(WHEN, None)
        return eng

    def test_a_live_arm_sends_one_bracketed_buy(self):
        ex = _Executor()
        eng = self.armed(ex)
        self.assertEqual(len(ex.buys), 1)
        self.assertEqual(ex.buys[0]["stop_price"], 60.0)
        self.assertTrue(eng.position.bracket_order_id)

    def test_paper_still_arms_with_no_orders_at_all(self):
        eng = self.armed(None)
        self.assertIsNotNone(eng.position)
        self.assertIsNone(eng.position.order_id)

    def test_an_unknown_entry_holds_nothing_and_freezes(self):
        class _Unknown(_Executor):
            def buy(self, **kw):
                raise RuntimeError("timeout")

        eng = self.armed(_Unknown())
        self.assertIsNone(eng.position)
        self.assertTrue(eng.frozen_reason)

    def test_an_unknown_exit_keeps_the_position_held(self):
        class _Unknown(_Executor):
            def sell(self, **kw):
                self.sells.append(kw)
                return {"order_id": "X1", "status": "UNKNOWN"}

        eng = self.armed(_Unknown())
        pos = eng.position
        eng._close(pos, WHEN, 250.0, "flip", priced=True, spot=24_600.0, status="CLOSED")
        self.assertIs(eng.position, pos)
        self.assertEqual(eng.history, [])
        self.assertTrue(eng.frozen_reason)

    def test_the_exit_books_what_dhan_traded(self):
        class _Priced(_Executor):
            def sell(self, **kw):
                self.sells.append(kw)
                return {"order_id": "X1", "status": "FILLED", "avg_price": 271.5}

        ex = _Priced()
        eng = self.armed(ex)
        pos = eng.position
        bracket = pos.bracket_order_id
        eng._close(pos, WHEN, 250.0, "flip", priced=True, spot=24_600.0, status="CLOSED")
        self.assertEqual(ex.released, [bracket])
        self.assertEqual(pos.exit_premium, 271.5)
        self.assertIsNone(eng.position)


class CandleEntryLiveTests(unittest.TestCase):
    """A ladder, so several legs. The brackets must ALL come off before any
    leg is sold: one still working at Dhan is a short nobody asked for."""

    def ladder(self, executor, fills=2):
        from engine.candle_ladder import LadderCandle, LadderFill, TwoRedLadder

        mother = LadderCandle(
            timestamp=datetime(2026, 8, 30, 9, 15),
            open=24_500.0,
            high=24_550.0,
            low=24_450.0,
            close=24_500.0,
            timeframe="5m",
        )
        lad = TwoRedLadder(
            mother,
            stages=("5m",),
            strike_for=lambda when, price: (24_400, "CE"),
            premium_lookup=lambda when, strike, option_type: 200.0,
            lot_size=75,
            expiry=date(2026, 9, 3),
            executor=executor,
        )
        for i in range(fills):
            lad.fills.append(
                LadderFill(
                    rung=i + 1,
                    timeframe="5m",
                    timestamp=WHEN,
                    index_price=24_400.0,
                    option_premium=200.0,
                    lots=1,
                    quantity=75,
                    strike=24_400 + i * 50,
                    option_type="CE",
                    marked_low=24_400.0,
                    priced_at=WHEN,
                    order_id=f"O{i}",
                    bracket_order_id=f"B{i}",
                )
            )
            # What `_fill` records when it sends a real order: the fill itself
            # is frozen history, and what is still WORKING lives here.
            lad._legs_open.add(f"O{i}")
            lad._brackets_open.add(f"B{i}")
        return lad

    def test_every_bracket_comes_off_before_any_leg_is_sold(self):
        ex = _Executor()
        lad = self.ladder(ex)
        self.assertTrue(lad._sell_basket_for_real(WHEN))
        self.assertEqual(ex.released, ["B0", "B1"])
        self.assertEqual(len(ex.sells), 2)

    def test_a_leg_its_own_bracket_already_sold_is_not_sold_again(self):
        class _OneTraded(_Executor):
            def cancel_bracket(self, *, order_id):
                self.released.append(str(order_id))
                if order_id == "B0":
                    return {"order_id": "B0", "traded": True, "avg_price": 55.0}
                return {"order_id": str(order_id), "cancelled": True}

        ex = _OneTraded()
        lad = self.ladder(ex)
        self.assertTrue(lad._sell_basket_for_real(WHEN))
        self.assertEqual(len(ex.sells), 1, "only the leg still held")

    def test_an_unknown_leg_exit_stops_the_whole_basket(self):
        class _Unknown(_Executor):
            def sell(self, **kw):
                self.sells.append(kw)
                return {"order_id": "X", "status": "UNKNOWN"}

        ex = _Unknown()
        lad = self.ladder(ex)
        self.assertFalse(lad._sell_basket_for_real(WHEN))
        self.assertTrue(lad.frozen_reason)

    def test_a_paper_ladder_has_no_orders_to_release(self):
        lad = self.ladder(None, fills=0)
        self.assertIsNone(lad.executor)


class _ReconcileBroker:
    """A Dhan that answers about orders and positions."""

    def __init__(self, *, bracket_status="PENDING", held=None, positions_raise=False):
        self.bracket_status = bracket_status
        self.held = held
        self.positions_raise = positions_raise

    def get_order_status(self, order_id):
        return {"orderStatus": self.bracket_status, "averagePrice": 61.0}

    def get_positions(self):
        if self.positions_raise:
            raise RuntimeError("token expired")
        return [{"securityId": "SEC1", "netQty": self.held if self.held is not None else 75}]


class ReconcileTests(unittest.TestCase):
    """What the four ask Dhan after a restart, and what they do with it."""

    def legs(self, quantity=75):
        return [
            {
                "order_id": "O1",
                "bracket_order_id": "B1",
                "strike": 24_400,
                "expiry": "2026-09-03",
                "option_type": "CE",
                "quantity": quantity,
            }
        ]

    def reconcile(self, broker, legs=None):
        from engine.options_live_executor import reconcile_live_orders

        with patch("broker.dhan.ScripMaster") as scrip:
            scrip.lookup.return_value = "SEC1"
            return reconcile_live_orders(broker, symbol="NIFTY", legs=legs or self.legs())

    def test_a_matching_book_reports_nothing_to_do(self):
        out = self.reconcile(_ReconcileBroker(held=75))
        self.assertEqual(out["settled"], {})
        self.assertEqual(out["short_by"], {})

    def test_a_bracket_that_traded_while_down_books_its_leg(self):
        out = self.reconcile(_ReconcileBroker(bracket_status="TRADED"))
        self.assertEqual(out["settled"], {"O1": 61.0})

    def test_a_super_orders_own_id_traded_is_the_entry_not_an_exit(self):
        """Dhan gives a Super Order one id; its TRADED status is the BUY.
        Read as an exit it froze a live Gap Carry that still held its leg
        (2026-09-15)."""
        legs = self.legs()
        legs[0]["bracket_order_id"] = legs[0]["order_id"]
        out = self.reconcile(_ReconcileBroker(bracket_status="TRADED", held=75), legs)
        self.assertEqual(out["settled"], {})
        self.assertEqual(out["short_by"], {})

    def test_a_super_order_whose_position_is_gone_is_still_caught(self):
        legs = self.legs()
        legs[0]["bracket_order_id"] = legs[0]["order_id"]
        out = self.reconcile(_ReconcileBroker(bracket_status="TRADED", held=0), legs)
        self.assertEqual(out["settled"], {})
        self.assertTrue(out["short_by"], "the position book must still see a sale")

    def test_the_broker_holding_less_is_reported_as_short(self):
        """The dangerous direction: something closed that the engine still
        thinks it owns."""
        out = self.reconcile(_ReconcileBroker(held=0))
        self.assertTrue(out["short_by"])

    def test_the_broker_holding_more_is_only_noted(self):
        """The account is shared across strategies. A leg belonging to
        another one is not this engine's to close."""
        out = self.reconcile(_ReconcileBroker(held=150))
        self.assertEqual(out["short_by"], {})
        self.assertTrue(any("more than" in note for note in out["notes"]))

    def test_a_position_book_that_cannot_be_read_is_not_agreement(self):
        out = self.reconcile(_ReconcileBroker(positions_raise=True))
        self.assertIn("__unchecked__", out["short_by"])

    def test_paper_orders_are_never_taken_to_the_broker(self):
        legs = self.legs()
        legs[0]["order_id"] = "paper-1"
        legs[0]["bracket_order_id"] = "paper-1"
        out = self.reconcile(_ReconcileBroker(held=0), legs)
        self.assertEqual(out["short_by"], {}, "nothing real to compare")


class ReconcilePersistenceTests(unittest.TestCase):
    """Order ids have to survive the restart, or reconciliation has nothing
    to ask about."""

    def test_the_high_entry_book_round_trips(self):
        bk, _ = book()
        sync(bk, [FakeTrade(trade_no=1, armed_at=WHEN)])
        back = RecoveryOrderBook(_Executor())
        back.load(bk.to_dict())
        self.assertEqual(back.open_orders(), bk.open_orders())
        self.assertTrue(back.open_orders()[0]["order_id"])

    def test_gap_carry_persists_its_order_ids(self):
        from engine.gap_carry_paper import _position_from_dict, _position_to_dict

        eng = GapCarryLiveTests().armed(_Executor())
        row = _position_to_dict(eng.position)
        self.assertTrue(row["order_id"])
        self.assertEqual(_position_from_dict(row).bracket_order_id, eng.position.bracket_order_id)

    def test_supertrend_persists_its_order_ids(self):
        from engine.supertrend_paper import _position_from_dict, _position_to_dict

        eng = SupertrendLiveTests().armed(_Executor())
        row = _position_to_dict(eng.position)
        self.assertTrue(row["order_id"])
        self.assertEqual(_position_from_dict(row).bracket_order_id, eng.position.bracket_order_id)


class PlacementResponseTests(unittest.TestCase):
    """A 200 from Dhan is not acceptance.

    Scalp has read the response body since it started trading live -- it
    checks orderStatus and the presence of an order id before believing an
    order exists. Nothing built here did, and the difference matters: a clean
    REJECTED read as an unknown outcome freezes a strategy that could simply
    have tried again on the next trigger.
    """

    def executor(self, response):
        class _Broker:
            def place_option_order(self, **kw):
                return response

            def place_super_order(self, **kw):
                return response

            def verify_order_fill(self, order_id, max_wait_sec=20):
                raise AssertionError("a refused order must never reach the fill check")

        return OptionsLiveExecutor(broker=_Broker(), symbol="NIFTY", armed=True)

    def buy(self, live, **kw):
        return live.buy(
            when=WHEN, strike=24_400, expiry=date(2026, 9, 3), option_type="CE", quantity=65, premium=200.0, **kw
        )

    def test_a_rejected_body_is_a_rejection_not_an_unknown(self):
        live = self.executor({"orderStatus": "REJECTED", "remarks": "insufficient margin"})
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            with self.assertRaises(OrderRejected) as caught:
                self.buy(live)
        self.assertIn("insufficient margin", str(caught.exception))

    def test_a_body_with_no_order_id_is_a_rejection_too(self):
        live = self.executor({"status": "failed", "message": "market closed"})
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            with self.assertRaises(OrderRejected):
                self.buy(live)

    def test_the_same_holds_for_a_bracketed_entry(self):
        live = self.executor({"orderStatus": "REJECTED", "remarks": "no such contract"})
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            with self.assertRaises(OrderRejected):
                self.buy(live, stop_price=60.0)

    def test_a_refused_exit_reports_rejected_and_never_freezes(self):
        """An exit that was refused leaves nothing working, so the next bar
        can take it again. Only an UNKNOWN exit should freeze anything."""
        live = self.executor({"orderStatus": "REJECTED", "remarks": "closed"})
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            receipt = live.sell(when=WHEN, strike=24_400, expiry=date(2026, 9, 3), option_type="CE", quantity=65)
        self.assertEqual(receipt["status"], "REJECTED")

    def test_a_clean_acknowledgement_still_goes_on_to_the_fill_check(self):
        class _Broker:
            def place_option_order(self, **kw):
                return {"orderId": "OK-1", "orderStatus": "TRANSIT"}

            def verify_order_fill(self, order_id, max_wait_sec=20):
                return {"status": "FILLED", "filled_qty": 65, "avg_price": 201.0}

        live = OptionsLiveExecutor(broker=_Broker(), symbol="NIFTY", armed=True)
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            receipt = self.buy(live)
        self.assertEqual(receipt["traded_premium"], 201.0)


class SuperOrderBookTests(unittest.TestCase):
    """A bracketed entry lives in the SUPER ORDER book, not the ordinary one.

    Getting this wrong is not a small mistake: `verify_order_fill` would
    answer UNKNOWN for an entry that filled perfectly, an unknown entry
    freezes the strategy, and every live campaign would stop on its first
    trade holding a real position it did not believe in.
    """

    def broker(self, rows, *, plain=None):
        class _Broker:
            def place_super_order(self, **kw):
                return {"orderId": "SO-9"}

            def place_option_order(self, **kw):
                return {"orderId": "PLAIN-9"}

            def get_super_orders(self):
                return rows

            def verify_order_fill(self, order_id, max_wait_sec=20):
                if plain is None:
                    raise AssertionError("the ordinary book must not be asked about a super order")
                return plain

        return _Broker()

    def buy(self, broker, **kw):
        live = OptionsLiveExecutor(broker=broker, symbol="NIFTY", armed=True)
        live.verify_wait_sec = 1  # the same question, asked in a second
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            return live.buy(
                when=WHEN, strike=24_400, expiry=date(2026, 9, 3), option_type="CE", quantity=65, premium=200.0, **kw
            )

    def test_a_filled_super_order_is_read_from_its_own_book(self):
        receipt = self.buy(
            self.broker([{"orderId": "SO-9", "orderStatus": "TRADED", "filledQty": 65, "averageTradedPrice": 205.5}]),
            stop_price=60.0,
        )
        self.assertEqual(receipt["traded_premium"], 205.5)
        self.assertEqual(receipt["traded_quantity"], 65)
        self.assertEqual(receipt["bracket_order_id"], "SO-9")

    def test_a_rejected_super_order_is_a_rejection_not_a_freeze(self):
        broker = self.broker([{"orderId": "SO-9", "orderStatus": "REJECTED", "remarks": "margin"}])
        with self.assertRaises(OrderRejected):
            self.buy(broker, stop_price=60.0)

    def test_an_entry_that_never_appears_times_out_rather_than_inventing_a_fill(self):
        broker = self.broker([])
        with self.assertRaises(RuntimeError) as caught:
            self.buy(broker, stop_price=60.0)
        self.assertNotIsInstance(caught.exception, OrderRejected)

    def test_an_unbracketed_buy_still_uses_the_ordinary_book(self):
        broker = self.broker([], plain={"status": "FILLED", "filled_qty": 65, "avg_price": 199.0})
        receipt = self.buy(broker)
        self.assertEqual(receipt["traded_premium"], 199.0)
        self.assertNotIn("bracket_order_id", receipt)


class RealDhanBehaviourTests(unittest.TestCase):
    """Pinned to what a REAL Dhan super order did on 2026-09-01.

    Order 34326090116372: NIFTY 24350CE 08-Sep, 65 qty, MARGIN. Both of these
    were wrong in code until that order was placed, and neither was findable
    with a fake broker, because the fakes were built from the same assumptions.
    """

    ENTRY = "34326090116372"

    def broker(self, *, leg_status="CANCELLED", leg_error="", super_avg=0.0, plain_avg=37.05):
        entry = self

        class _Broker:
            cancels: list = []

            def place_super_order(self, **kw):
                return {"orderId": entry.ENTRY, "orderStatus": "TRANSIT"}

            def get_super_orders(self):
                # EXACTLY what Dhan returned: TRADED, a real filledQty, and
                # averageTradedPrice 0.0.
                return [
                    {
                        "orderId": entry.ENTRY,
                        "orderStatus": "TRADED",
                        "filledQty": 65,
                        "averageTradedPrice": super_avg,
                        "omsErrorDescription": "TRADE CONFIRMED",
                        "legDetails": [
                            {"legName": "STOP_LOSS_LEG", "orderStatus": "PENDING", "price": 11.0},
                            {"legName": "TARGET_LEG", "orderStatus": "PENDING", "price": 54.95},
                        ],
                    }
                ]

            def verify_order_fill(self, order_id, max_wait_sec=20):
                # The ordinary book DOES resolve a super order id, with the
                # price the super order book withheld.
                return {"status": "FILLED", "filled_qty": 65, "avg_price": plain_avg}

            def cancel_super_order(self, order_id, leg_name="ENTRY_LEG"):
                type(self).cancels.append(leg_name)
                if leg_name == "ENTRY_LEG":
                    raise AssertionError("cancelling ENTRY_LEG after a fill leaves the exit legs working")
                if leg_error:
                    return {"orderId": order_id, "orderStatus": "TRADED", "errorMessage": leg_error}
                return {"orderId": order_id, "orderStatus": leg_status}

        _Broker.cancels = []
        return _Broker()

    def live(self, broker):
        ex = OptionsLiveExecutor(broker=broker, symbol="NIFTY", armed=True)
        ex.verify_wait_sec = 2
        return ex

    def test_the_fill_price_comes_from_the_book_that_actually_has_it(self):
        """The super order book says TRADED but reports averageTradedPrice 0.
        Booking that would put the QUOTE in the ledger instead of the fill."""
        live = self.live(self.broker(super_avg=0.0, plain_avg=37.05))
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            receipt = live.buy(
                when=WHEN,
                strike=24_350,
                expiry=date(2026, 9, 8),
                option_type="CE",
                quantity=65,
                premium=36.65,
                stop_price=11.0,
            )
        self.assertEqual(receipt["traded_premium"], 37.05, "the real fill, not the quote")
        self.assertEqual(receipt["traded_quantity"], 65)

    def test_the_super_book_is_believed_when_it_does_carry_a_price(self):
        live = self.live(self.broker(super_avg=37.5, plain_avg=99.0))
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            receipt = live.buy(
                when=WHEN,
                strike=24_350,
                expiry=date(2026, 9, 8),
                option_type="CE",
                quantity=65,
                premium=36.65,
                stop_price=11.0,
            )
        self.assertEqual(receipt["traded_premium"], 37.5)

    def test_releasing_a_bracket_cancels_the_exit_legs_not_the_entry(self):
        """Cancelling ENTRY_LEG after the fill returns DH-906 and leaves the
        target and stop WORKING over a position the engine thinks is closed."""
        broker = self.broker()
        live = self.live(broker)
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            outcome = live.cancel_bracket(order_id=self.ENTRY)
        self.assertEqual(type(broker).cancels, ["TARGET_LEG", "STOP_LOSS_LEG"])
        self.assertTrue(outcome["cancelled"])
        self.assertNotIn("traded", outcome)

    def test_a_leg_that_really_traded_is_still_reported_as_a_sale(self):
        broker = self.broker(leg_error="Order Has Traded Please Refresh Order Book")
        live = self.live(broker)
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            outcome = live.cancel_bracket(order_id=self.ENTRY)
        self.assertTrue(outcome["traded"])
        self.assertEqual(outcome["avg_price"], 54.95, "priced from the leg that sold it")

    def test_a_leg_that_will_not_cancel_is_refused_loudly(self):
        """Half a bracket released is worse than none: the other leg is still
        live over a position about to be sold."""

        class _Stuck:
            def cancel_super_order(self, order_id, leg_name="ENTRY_LEG"):
                raise RuntimeError("broker down")

            def get_super_orders(self):
                return []

        live = self.live(_Stuck())
        with patch("engine.options_live_executor.OPTIONS_LIVE_EXECUTION_ENABLED", True):
            with self.assertRaises(ExecutionRefused):
                live.cancel_bracket(order_id=self.ENTRY)
