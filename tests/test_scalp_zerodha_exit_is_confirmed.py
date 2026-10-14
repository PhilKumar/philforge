"""A Zerodha scalp's exit settles the entry first and books only a real fill.

Phil, 14-Oct-2026: "Exiting scalping created confusions today.... in zerodha".
SENSEX 73000PE, 80 qty. Exit was pressed eight seconds after the entry:

    13:06:39  SELL LIMIT 488.90   REJECTED  "Insufficient funds. Margin required: 692108"
    13:06:40  BUY  LIMIT 510.60   COMPLETE  (the entry -- Kite turns MARKET into a protected LIMIT)
    13:07:14  SELL LIMIT 521.60   COMPLETE  (sold by hand in Kite)

The buy had not filled, so the sell was a fresh SHORT and Zerodha refused it
for margin. The site booked the trade closed anyway, at the LTP (514.65, +728),
and the buy then filled with nothing here watching it.
"""

import unittest

from scalp import ScalpEngine, ScalpTrade


class Dhan:
    def __init__(self, ltp=514.65):
        self.ltp = ltp
        self.orders = []

    def get_option_ltp(self, *a, **k):
        return self.ltp

    def get_positions_cached(self, ttl):
        return []

    def place_option_order(self, **kw):
        self.orders.append(kw)
        return {"orderId": "D1", "orderStatus": "TRANSIT"}


class Kite:
    """Zerodha, answering for the entry E1, its stop S1 and the exits X1, X2..."""

    def __init__(
        self,
        entry="TRADED",
        entry_filled=80,
        entry_avg=510.6,
        after_cancel=None,
        exit_result="FILLED",
        exit_avg=521.6,
        unknown=False,
    ):
        self.entry = {"orderStatus": entry, "filledQty": entry_filled, "averageTradedPrice": entry_avg}
        self.after_cancel = after_cancel
        self.exit_result = exit_result
        self.exit_avg = exit_avg
        self.unknown = unknown
        self.orders, self.cancelled, self.exit_states = [], [], {}

    def place_option_order(self, **kw):
        self.orders.append(kw)
        oid = f"X{len(self.orders)}"
        return {"orderId": oid, "orderStatus": "TRANSIT"}

    def verify_order_fill(self, order_id, max_wait_sec=15, poll_interval=1.0):
        state = self.exit_states.get(order_id, self.exit_result)
        if state == "FILLED":
            return {"order_id": order_id, "status": "FILLED", "avg_price": self.exit_avg}
        if state == "REJECTED":
            return {
                "order_id": order_id,
                "status": "REJECTED",
                "message": "Insufficient funds. Margin required: 692108.05",
            }
        return {"order_id": order_id, "status": "TIMEOUT", "message": "still open"}

    def get_order_status(self, order_id):
        if self.unknown:
            return {"orderStatus": "UNKNOWN", "message": "gateway timeout"}
        if order_id == "E1":
            return {"orderId": "E1", **self.entry}
        if order_id.startswith("X"):
            state = self.exit_states.get(order_id, self.exit_result)
            return {
                "orderId": order_id,
                "orderStatus": "TRADED" if state == "FILLED" else "PENDING",
                "averageTradedPrice": self.exit_avg,
            }
        return {"orderId": order_id, "orderStatus": "PENDING"}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        if order_id == "E1" and self.after_cancel:
            self.entry = dict(self.after_cancel)
        return {"orderId": order_id, "orderStatus": ""}

    def get_positions(self):
        return []


def _trade(**over):
    trade = ScalpTrade(
        trade_id=1,
        underlying="SENSEX",
        strike=73000,
        option_type="PE",
        expiry="2026-10-15",
        transaction_type="BUY",
        lots=4,
        lot_size=20,
        entry_premium=505.55,
        target_premium=600.0,
        sl_premium=10.0,
        order_id="E1",
        mode="live",
        broker="zerodha",
        product_type="MARGIN",
    )
    trade.broker_order_model = "zerodha"
    for key, value in over.items():
        setattr(trade, key, value)
    return trade


class _Engine(unittest.IsolatedAsyncioTestCase):
    def make(self, kite, trade=None):
        engine = ScalpEngine(Dhan(), zerodha_client=kite)
        self.logged = []
        engine._log = lambda level, msg: self.logged.append((level, msg))
        engine.open_trades[1] = trade or _trade()
        return engine


class TheEntryIsSettledFirst(_Engine):
    async def test_an_entry_still_working_is_cancelled_and_nothing_is_sold(self):
        kite = Kite(entry="PENDING", entry_filled=0, after_cancel={"orderStatus": "CANCELLED", "filledQty": 0})
        engine = self.make(kite)
        result = await engine.exit_trade(1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(kite.cancelled, ["E1"])
        self.assertEqual(kite.orders, [])  # no sell: nothing was held
        self.assertEqual(engine.closed_trades[-1]["exit_reason"], "cancelled")
        self.assertEqual(engine.closed_trades[-1]["pnl"], 0.0)

    async def test_14_october_the_buy_fills_as_it_is_cancelled_so_the_fill_is_sold(self):
        # The cancel lost the race: Kite reports the buy COMPLETE at 510.60.
        kite = Kite(
            entry="PENDING",
            entry_filled=0,
            after_cancel={"orderStatus": "TRADED", "filledQty": 80, "averageTradedPrice": 510.6},
        )
        engine = self.make(kite)
        result = await engine.exit_trade(1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(kite.orders), 1)
        self.assertEqual((kite.orders[0]["transaction_type"], kite.orders[0]["quantity"]), ("SELL", 80))
        closed = engine.closed_trades[-1]
        self.assertEqual((closed["entry_premium"], closed["exit_premium"]), (510.6, 521.6))
        self.assertEqual(closed["pnl"], round((521.6 - 510.6) * 80, 2))

    async def test_a_part_filled_entry_sells_only_what_filled(self):
        kite = Kite(
            entry="PENDING",
            entry_filled=40,
            after_cancel={"orderStatus": "CANCELLED", "filledQty": 40, "averageTradedPrice": 509.0},
        )
        engine = self.make(kite)
        await engine.exit_trade(1)
        self.assertEqual(kite.orders[0]["quantity"], 40)
        self.assertEqual(engine.closed_trades[-1]["pnl"], round((521.6 - 509.0) * 40, 2))

    async def test_when_zerodha_cannot_say_nothing_is_sent_and_the_trade_stays(self):
        kite = Kite(unknown=True)
        engine = self.make(kite)
        result = await engine.exit_trade(1)
        self.assertEqual(result["status"], "error")
        self.assertIn("nothing was sent", result["message"])
        self.assertEqual((kite.orders, kite.cancelled), ([], []))
        self.assertIn(1, engine.open_trades)


class OnlyARealFillClosesTheTrade(_Engine):
    async def test_the_exit_is_booked_at_its_fill_not_the_ltp(self):
        engine = self.make(Kite())
        await engine.exit_trade(1)
        closed = engine.closed_trades[-1]
        self.assertEqual((closed["entry_premium"], closed["exit_premium"]), (510.6, 521.6))

    async def test_a_refused_exit_leaves_the_trade_open_and_says_why(self):
        kite = Kite(exit_result="REJECTED")
        engine = self.make(kite)
        result = await engine.exit_trade(1)
        self.assertEqual(result["status"], "error")
        self.assertIn("Insufficient funds", result["message"])
        self.assertIn("still open", result["message"])
        self.assertIn(1, engine.open_trades)
        self.assertEqual(engine.closed_trades, [])

    async def test_after_a_refusal_the_monitor_does_not_fire_again_at_once(self):
        kite = Kite(exit_result="REJECTED")
        engine = self.make(kite)
        await engine.exit_trade(1)
        await engine._close_trade(engine.open_trades[1], "target_hit")
        self.assertEqual(len(kite.orders), 1)  # the automatic retry waits
        await engine.exit_trade(1)  # a person pressing Exit is not made to wait
        self.assertEqual(len(kite.orders), 2)

    async def test_an_exit_still_working_is_not_sent_twice(self):
        kite = Kite(exit_result="TIMEOUT")
        engine = self.make(kite)
        first = await engine.exit_trade(1)
        self.assertEqual(first["status"], "error")
        self.assertIn("has not filled yet", first["message"])
        second = await engine.exit_trade(1)
        self.assertIn("still working", second["message"])
        self.assertEqual(len(kite.orders), 1)
        # It fills: the next press books it, at its fill, without another order.
        kite.exit_states["X1"] = "FILLED"
        third = await engine.exit_trade(1)
        self.assertEqual(third["status"], "ok")
        self.assertEqual(len(kite.orders), 1)
        self.assertEqual(engine.closed_trades[-1]["exit_premium"], 521.6)

    async def test_a_refused_exit_puts_the_stop_back(self):
        kite = Kite(exit_result="REJECTED")
        engine = self.make(kite, _trade(broker_sl_order_id="S1", sl_premium=450.0))
        await engine.exit_trade(1)
        self.assertIn("S1", kite.cancelled)
        stops = [o for o in kite.orders if o.get("order_type") == "SL"]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["trigger_price"], 450.0)


class DhanIsUntouched(_Engine):
    async def test_a_dhan_scalp_exits_as_before(self):
        engine = ScalpEngine(Dhan(), zerodha_client=Kite())
        engine._log = lambda *a: None
        trade = _trade(broker="dhan", order_id="D0")
        trade.broker_order_model = ""
        engine.open_trades[1] = trade
        result = await engine.exit_trade(1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(engine.dhan.orders[0]["transaction_type"], "SELL")


if __name__ == "__main__":
    unittest.main()
