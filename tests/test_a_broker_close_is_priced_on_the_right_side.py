"""When Dhan closes a position for us, book the price Dhan actually got.

2026-09-10. Dhan squared off 130 NIFTY 23700 PE — bought at 265.00, sold at
279.45, a real +₹1,798 after charges. The engine reconciled it and wrote:

    🔚 Leg 1 closed (BROKER_MANUAL_EXIT): Entry ₹265.00 → Exit ₹265.00
       | Qty 130 | P&L: ₹-112.94

It read `buyAvg` as the exit price. For a bought option `buyAvg` is the ENTRY,
so every broker-closed long booked at breakeven-minus-charges.

Two things follow from that, and the second is the expensive one. The ledger
lies — and `banked_pnl` feeds the compounding ladder, so a winner closed by the
broker would have sized the book DOWN instead of up.

A long is closed by a SELL. A short is closed by a BUY. Read the side.
"""

import unittest

from engine.live import LiveEngine


def _engine():
    return LiveEngine.__new__(LiveEngine)


BROKER_ROW = {
    "tradingSymbol": "NIFTY-Sep2026-23700-PE",
    "netQty": 0,
    "buyAvg": 265.0,
    "sellAvg": 279.45,
    "productType": "INTRADAY",
    "positionType": "CLOSED",
    "realizedProfit": 1878.5,
}


class ALongIsClosedByItsSale(unittest.TestCase):
    def test_the_sell_average_is_the_exit(self):
        pos = {"transaction_type": "BUY", "entry_premium": 265.0}
        self.assertEqual(_engine()._broker_exit_premium(pos, BROKER_ROW), 279.45)

    def test_it_is_not_the_entry_price_back_again(self):
        """The exact 2026-09-10 failure: a +₹1,798 trade booked as -₹112.94."""
        pos = {"transaction_type": "BUY", "entry_premium": 265.0}
        self.assertNotEqual(_engine()._broker_exit_premium(pos, BROKER_ROW), 265.0)

    def test_a_stale_after_hours_mark_does_not_win_over_the_fill(self):
        """`lastTradedPrice` is a mark, not a fill, and after hours not current."""
        row = {**BROKER_ROW, "lastTradedPrice": 262.45}
        pos = {"transaction_type": "BUY", "entry_premium": 265.0}
        self.assertEqual(_engine()._broker_exit_premium(pos, row), 279.45)


class AShortIsClosedByItsPurchase(unittest.TestCase):
    def test_the_buy_average_is_the_exit(self):
        row = {"netQty": 0, "buyAvg": 120.5, "sellAvg": 140.0}
        pos = {"transaction_type": "SELL", "entry_premium": 140.0}
        self.assertEqual(_engine()._broker_exit_premium(pos, row), 120.5)

    def test_a_missing_side_falls_back_rather_than_reading_the_wrong_one(self):
        row = {"netQty": 0, "sellAvg": 140.0, "lastTradedPrice": 118.0}
        pos = {"transaction_type": "SELL", "entry_premium": 140.0}
        self.assertEqual(_engine()._broker_exit_premium(pos, row), 118.0)


class TheFallbacksStillWork(unittest.TestCase):
    def test_no_broker_row_falls_back_to_the_engines_own_mark(self):
        pos = {"transaction_type": "BUY", "current_premium": 275.0, "entry_premium": 265.0}
        self.assertEqual(_engine()._broker_exit_premium(pos, None), 275.0)

    def test_with_nothing_at_all_it_falls_back_to_the_entry(self):
        pos = {"transaction_type": "BUY", "entry_premium": 265.0}
        self.assertEqual(_engine()._broker_exit_premium(pos, {}), 265.0)

    def test_it_never_returns_a_negative_or_zero_price(self):
        row = {"netQty": 0, "sellAvg": 0, "lastTradedPrice": 0}
        pos = {"transaction_type": "BUY", "entry_premium": 0, "current_premium": 0}
        self.assertEqual(_engine()._broker_exit_premium(pos, row), 0.0)

    def test_an_unstated_side_is_treated_as_a_long(self):
        """Every book here buys options; a missing side must not silently read
        the entry price back as the exit."""
        self.assertEqual(_engine()._broker_exit_premium({"entry_premium": 265.0}, BROKER_ROW), 279.45)


class TheLadderSeesTheRightMoney(unittest.TestCase):
    def test_the_gain_that_reaches_banked_pnl_is_the_real_one(self):
        pos = {"transaction_type": "BUY", "entry_premium": 265.0}
        exit_premium = _engine()._broker_exit_premium(pos, BROKER_ROW)
        gross = (exit_premium - 265.0) * 130
        self.assertAlmostEqual(gross, 1878.5, places=2)


if __name__ == "__main__":
    unittest.main()
