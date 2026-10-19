"""A blank live exit is set from the price, never from a flat Rs 300 / Rs 100.

Phil, 2026-10-19: "Why this settings got jumbled and not usable?". Live mode
filled a blank target and stop with flat premiums -- Rs 300 and Rs 100 --
which put a Rs 232 option's stop 132 points away and sat on the wrong side of
a Rs 50 one. A blank is now set 25% from the price the order is judged at:
the quote for a market entry, the trigger price for a stop-limit one.
"""

import asyncio
import unittest

import scalp


class _Dhan:
    def __init__(self, ltp):
        self.ltp, self.placed = ltp, []

    def get_option_ltp(self, *a):
        return self.ltp

    def place_super_order(self, **kw):
        self.placed.append(kw)
        return {"orderId": f"SO{len(self.placed)}", "orderStatus": "PENDING"}


def _engine(ltp):
    engine = scalp.ScalpEngine(_Dhan(ltp))
    engine.start = lambda: None
    return engine


ENTRY = dict(
    underlying="NIFTY",
    strike=22700,
    expiry="2026-10-27",
    option_type="PE",
    transaction_type="BUY",
    lots=1,
    lot_size=65,
    mode="live",
)


class BlankExitsComeFromThePrice(unittest.TestCase):
    def test_a_market_entry_with_only_a_target_gets_a_stop_25_percent_below(self):
        engine = _engine(232.2)
        out = asyncio.run(engine.enter_trade(**ENTRY, target_points=10))
        self.assertEqual(out["status"], "ok", out)
        sent = engine.dhan.placed[0]
        self.assertEqual((sent["target_price"], sent["stop_loss_price"]), (242.2, 174.15))

    def test_a_stop_limit_entry_is_judged_at_its_trigger(self):
        engine = _engine(220.0)
        out = asyncio.run(engine.enter_trade(**ENTRY, target_points=10, entry_limit_price=230, entry_limit_max=235))
        trade = engine.open_trades[out["trade_id"]]
        self.assertEqual(trade.status, "pending")
        self.assertEqual(engine.dhan.placed, [])
        trade.current_premium = 232.2
        asyncio.run(engine._activate_pending_trade(trade))
        sent = engine.dhan.placed[0]
        # Not the flat Rs 300 / Rs 100 the blanks used to become.
        self.assertEqual((sent["target_price"], sent["stop_loss_price"]), (242.2, 174.15))

    def test_typed_premiums_are_kept(self):
        engine = _engine(232.2)
        asyncio.run(engine.enter_trade(**ENTRY, target_premium=260, sl_premium=210))
        sent = engine.dhan.placed[0]
        self.assertEqual((sent["target_price"], sent["stop_loss_price"]), (260.0, 210.0))


if __name__ == "__main__":
    unittest.main()
