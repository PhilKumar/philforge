"""A trading day is filed under the day it traded, not the day we looked.

`_persist_daily_trades` wrote the broker's day summary under `_ist_date_str()`
— the clock. The clock rolls at midnight; the trade book still holds the session
that just ended. So a refresh at 00:05 on 2026-09-11 filed 2026-09-10's PE trade
under the 11th, and the CE + PE desk showed it twice: once as the book's own row
on the 10th, once as the account's on the 11th. Nothing could match them,
because they were not on the same day.

The same fault put 2026-09-08's trade on 2026-09-09 three days earlier. That one
was cleaned up by the provisional-day sweep in `_backfill_trade_history` — which
tidied after this bug instead of stopping it, and could only reach a day once it
was no longer today.

The fills carry their own exchange timestamps. `_summarize_real_trade_fills`
worked the date out from them all along and then threw it away.
"""

import unittest
from pathlib import Path

import app

SRC = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")


def _fill(date, side="BUY", price=265.0, qty=65):
    return {
        "securityId": "47306",
        "tradingSymbol": "NIFTY-Sep2026-23700-PE",
        "transactionType": side,
        "tradedQuantity": qty,
        "tradedPrice": price,
        "exchangeTime": f"{date} 09:25:16" if side == "BUY" else f"{date} 15:26:36",
        "productType": "INTRADAY",
    }


class TheDateTravelsWithTheSummary(unittest.TestCase):
    def test_the_summary_says_which_day_it_is(self):
        entry = app._summarize_real_trade_fills([_fill("2026-09-10"), _fill("2026-09-10", "SELL", 279.45)])
        self.assertEqual(entry["trade_date"], "2026-09-10")

    def test_it_is_the_fills_day_not_the_clocks(self):
        """The whole point: this must not vary with when it is run."""
        entry = app._summarize_real_trade_fills([_fill("2026-09-08"), _fill("2026-09-08", "SELL", 229.45)])
        self.assertEqual(entry["trade_date"], "2026-09-08")
        self.assertNotEqual(entry["trade_date"], app._ist_date_str())

    def test_the_latest_day_wins_when_the_book_spans_two(self):
        fills = [_fill("2026-09-09"), _fill("2026-09-09", "SELL"), _fill("2026-09-10")]
        self.assertEqual(app._summarize_real_trade_fills(fills)["trade_date"], "2026-09-10")

    def test_no_fills_is_still_nothing(self):
        self.assertIsNone(app._summarize_real_trade_fills([]))


class ThePersistUsesIt(unittest.TestCase):
    def _body(self):
        after = SRC.split("async def _persist_daily_trades(")[1]
        cuts = [after.find(m) for m in ("\n@app.", "\ndef ", "\nasync def ")]
        return after[: min(c for c in cuts if c != -1)]

    def test_the_row_is_keyed_on_the_fills_date(self):
        self.assertIn('entry.get("trade_date")', self._body())

    def test_the_clock_is_only_the_fallback(self):
        body = self._body()
        keyed = body.index('today_str = str(entry.get("trade_date")')
        self.assertIn("or _ist_date_str()", body[keyed : keyed + 120])

    def test_a_row_filed_by_the_clock_is_cleared_up(self):
        """The mis-filed rows already exist; the fix has to reach them."""
        body = self._body()
        self.assertIn("delete_trade_history_entry_sync", body)
        self.assertIn('== "live_day_fifo"', body, "a settled record is never dropped")

    def test_it_only_drops_when_the_two_dates_differ(self):
        body = self._body()
        guard = body.index("if clock_str != today_str:")
        drop = body.index("delete_trade_history_entry_sync")
        self.assertLess(guard, drop)


if __name__ == "__main__":
    unittest.main()
