"""Scalp exits a set distance from the premium, and a Zerodha entry that fills.

Phil, 14-Oct-2026:
  * "Entry refused before the broker saw it: The option is trading at Rs 480.85.
    A BUY target of Rs 300.00 is not above it" -- "I want the advanced order set
    correctly automatically according to the premium I select ... the SL has to
    be set as 100pts less and 100 points more for Target".
  * "Why it took 8 sec to fill a scalp entry?" -- Kite turns a MARKET option
    order into a limit capped near the last price; SENSEX 73000PE sat at
    Rs 510.60 for eight seconds while the premium ran. The entry is now a limit
    a little through the best offer.
"""

import asyncio
import unittest

from scalp import ScalpEngine, ScalpTrade, scalp_entry_limit


class Dhan:
    def __init__(self, ltp=480.85):
        self.ltp = ltp

    def get_option_ltp(self, *a, **k):
        return self.ltp

    def get_positions_cached(self, ttl):
        return []


class Kite:
    def __init__(self, ltp=505.55, bid=505.0, ask=506.0):
        self.book = {"ltp": ltp, "bid": bid, "ask": ask}
        self.orders = []

    def get_option_quote(self, *a, **k):
        return dict(self.book)

    def place_option_order(self, **kw):
        self.orders.append(kw)
        return {"orderId": f"Z{len(self.orders)}", "orderStatus": "TRANSIT"}

    def verify_order_fill(self, order_id, max_wait_sec=15, poll_interval=1.0):
        return {"order_id": order_id, "status": "FILLED", "avg_price": 507.0}

    def get_order_status(self, order_id):
        return {"orderId": order_id, "orderStatus": "PENDING"}

    def get_positions(self):
        return []


class TheEntryLimit(unittest.TestCase):
    def test_a_buy_pays_up_to_two_percent_over_the_offer(self):
        self.assertEqual(scalp_entry_limit("BUY", {"ask": 506.0, "bid": 505.0, "ltp": 505.55}, 0), 516.1)

    def test_a_sell_accepts_two_percent_under_the_bid(self):
        self.assertEqual(scalp_entry_limit("SELL", {"ask": 506.0, "bid": 505.0}, 0), 494.9)

    def test_with_no_book_it_goes_three_percent_past_the_last_price(self):
        self.assertEqual(scalp_entry_limit("BUY", {"ltp": 500.0}, 0), 515.0)
        self.assertEqual(scalp_entry_limit("BUY", {}, 500.0), 515.0)

    def test_with_no_price_at_all_it_is_left_to_market(self):
        self.assertEqual(scalp_entry_limit("BUY", {}, 0), 0.0)

    def test_the_price_is_on_the_tick(self):
        price = scalp_entry_limit("BUY", {"ask": 123.37}, 0)
        self.assertAlmostEqual(price * 20, round(price * 20), places=6)


class PointsFromThePremium(unittest.TestCase):
    def trade(self, side="BUY", entry=480.0, **kw):
        return ScalpTrade(
            trade_id=1,
            underlying="SENSEX",
            strike=73000,
            option_type="PE",
            expiry="2026-10-15",
            transaction_type=side,
            lots=4,
            lot_size=20,
            entry_premium=entry,
            sl_points=100,
            target_points=100,
            **kw,
        )

    def test_a_buy_stops_100_under_and_targets_100_over(self):
        t = self.trade()
        self.assertEqual((t.sl_premium, t.target_premium), (380.0, 580.0))

    def test_a_sell_is_the_other_way_round(self):
        t = self.trade("SELL")
        self.assertEqual((t.sl_premium, t.target_premium), (580.0, 380.0))

    def test_the_real_fill_moves_both(self):
        t = self.trade()
        t.rebase_points(507.0)
        self.assertEqual((t.sl_premium, t.target_premium), (407.0, 607.0))

    def test_a_stop_never_goes_below_a_paisa(self):
        t = self.trade(entry=60.0)
        self.assertEqual(t.sl_premium, 0.05)

    def test_a_pending_trade_waits_for_its_entry(self):
        t = self.trade(entry=0.0)
        self.assertEqual((t.sl_premium, t.target_premium), (0.0, 0.0))
        t.rebase_points(300.0)
        self.assertEqual((t.sl_premium, t.target_premium), (200.0, 400.0))


class _Engine(unittest.IsolatedAsyncioTestCase):
    def make(self, kite=None, dhan=None):
        engine = ScalpEngine(dhan or Dhan(), zerodha_client=kite or Kite())
        self.logged = []
        engine._log = lambda level, msg: self.logged.append((level, msg))
        engine._schedule_broker_sync = lambda trade_id: None
        engine.start = lambda: None
        return engine

    def entry(self, **over):
        args = dict(
            underlying="SENSEX",
            strike=73000,
            option_type="PE",
            expiry="2026-10-15",
            transaction_type="BUY",
            lots=4,
            lot_size=20,
            target_premium=300.0,
            sl_premium=10.0,
            mode="live",
            broker="zerodha",
            product_type="MARGIN",
        )
        args.update(over)
        return args


class TheEntryWithPoints(_Engine):
    async def test_the_form_s_old_300_target_no_longer_refuses_the_order(self):
        kite = Kite(ltp=480.85, bid=480.5, ask=481.0)
        engine = self.make(kite)
        result = await engine.enter_trade(**self.entry(target_points=100, sl_points=100))
        self.assertEqual(result["status"], "ok", result)
        trade = engine.open_trades[result["trade_id"]]
        self.assertEqual((trade.sl_premium, trade.target_premium), (380.85, 580.85))

    async def test_without_points_the_check_still_refuses_a_target_under_the_price(self):
        engine = self.make(Kite(ltp=480.85, bid=480.5, ask=481.0))
        result = await engine.enter_trade(**self.entry())
        self.assertEqual(result["status"], "error")
        self.assertIn("not above it", result["message"])

    async def test_the_zerodha_entry_is_a_limit_through_the_offer(self):
        kite = Kite(ltp=505.55, bid=505.0, ask=506.0)
        engine = self.make(kite)
        await engine.enter_trade(**self.entry(target_points=100, sl_points=100))
        order = kite.orders[0]
        self.assertEqual((order["order_type"], order["price"], order["quantity"]), ("LIMIT", 516.1, 80))

    async def test_zerodha_s_own_price_is_used_not_dhan_s(self):
        kite = Kite(ltp=505.55, bid=505.0, ask=506.0)
        engine = self.make(kite, Dhan(ltp=999.0))
        result = await engine.enter_trade(**self.entry(target_points=100, sl_points=100))
        self.assertEqual(engine.open_trades[result["trade_id"]].entry_premium, 505.55)

    async def test_the_verified_fill_moves_the_exits(self):
        engine = self.make(Kite())
        result = await engine.enter_trade(**self.entry(target_points=100, sl_points=100))
        engine._place_broker_sl_tp = lambda trade, **kw: asyncio.sleep(0)
        await engine._verify_fill_and_sync_broker_orders(result["trade_id"])
        trade = engine.open_trades[result["trade_id"]]
        self.assertEqual((trade.entry_premium, trade.sl_premium, trade.target_premium), (507.0, 407.0, 607.0))

    async def test_typing_a_target_by_hand_takes_it_off_the_points(self):
        engine = self.make(Kite())
        result = await engine.enter_trade(**self.entry(target_points=100, sl_points=100))
        trade = engine.open_trades[result["trade_id"]]
        trade.target_from_points = trade.target_from_points  # set
        await engine.update_trade_targets(result["trade_id"], target_premium=650.0)
        trade.rebase_points(520.0)
        self.assertEqual(trade.target_premium, 650.0)
        self.assertEqual(trade.sl_premium, 420.0)


if __name__ == "__main__":
    unittest.main()
