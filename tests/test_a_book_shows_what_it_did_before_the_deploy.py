"""A book's Completed Trades must not start empty just because it restarted.

CE_SL15_NoMonTue read "0 trades" and ₹0.00 on the Live desk while its real CE
trade of 2026-09-03 had already happened. Phil sent the screenshot four times.

The gap used to be filled from the broker account, which records no strategy --
so a PE book "borrowed" Phil's scalps and the Gap Carry's legs on any day it had
no trade of its own (2026-09-16). It is filled from the book's OWN saved runs
now, matched by the book's name, and the broker only corrects the charges.

Three endpoints serve three pages, and the borrowing belongs in one; the last
class pins that down, because the fix reads as correct in all three.
"""

import os
import unittest
from pathlib import Path

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "app.py").read_text(encoding="utf-8")

CE_03 = {
    "entry_time": "2026-09-03 09:20:05",
    "exit_time": "2026-09-03 10:45:00",
    "trading_symbol": "NIFTY 23750CE 2026-09-08",
    "quantity": 130,
    "entry_premium": 294.73,
    "exit_premium": 282.38,
    "pnl": -1723.8,
    "exit_reason": "EXIT_SIGNAL",
    "entry_order_id": "34226090300001",
    "book_name": "CE_SL15_NoMonTue",
    "book_side": "CE",
}
PE_10 = {
    "entry_time": "2026-09-10 09:25:09",
    "exit_time": "2026-09-10 15:26:36",
    "trading_symbol": "NIFTY 23700PE 2026-09-15",
    "quantity": 130,
    "entry_premium": 265.0,
    "exit_premium": 279.45,
    "pnl": 1763.21,
    "exit_reason": "BROKER_MANUAL_EXIT",
    "entry_order_id": "34226091013711",
    "book_name": "PE_NoTarget",
    "book_side": "PE",
}
BOOK_TRADES = [CE_03, PE_10]

ACCOUNT = [
    {
        "date": "2026-09-03",
        "side": "CE",
        "symbol": "NIFTY 08 SEP 23750 CALL",
        "quantity": 130,
        "entry_time": "2026-09-03T09:20",
        "gross_pnl": -1605.5,
        "charges": 134.75,
        "pnl": -1740.25,
        "costs_known": True,
    },
    # a scalp on a day the CE book did not trade -- must never be borrowed
    {
        "date": "2026-09-11",
        "side": "CE",
        "symbol": "NIFTY 15 SEP 23250 CALL",
        "quantity": 260,
        "entry_time": "2026-09-11T14:00",
        "gross_pnl": 1001.0,
        "charges": 177.08,
        "pnl": 823.92,
        "costs_known": True,
    },
]


def _book(side, name):
    return {"run_name": name, "legs": [{"option_type": side}]}


CE_BOOK = _book("CE", "CE_SL15_NoMonTue")
PE_BOOK = _book("PE", "PE_NoTarget")


def _missing(strategy=CE_BOOK, own=None, trades=None):
    return app._saved_trades_this_book_is_missing(strategy, own or [], BOOK_TRADES if trades is None else trades)


class TheCeBookFindsItsCeTrade(unittest.TestCase):
    def test_the_row_comes_back(self):
        rows = _missing()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trading_symbol"], "NIFTY 23750CE 2026-09-08")

    def test_it_is_shaped_like_an_engine_trade(self):
        row = _missing()[0]
        for field in (
            "trading_symbol",
            "entry_time",
            "exit_time",
            "entry_premium",
            "exit_premium",
            "quantity",
            "pnl",
            "gross_pnl",
            "charges",
            "exit_reason",
        ):
            self.assertIn(field, row, field)

    def test_it_keeps_its_own_exit_reason(self):
        self.assertEqual(_missing()[0]["exit_reason"], "EXIT_SIGNAL")

    def test_it_is_marked_as_earlier(self):
        self.assertTrue(_missing()[0]["from_account"])

    def test_it_offers_no_journal_chart(self):
        self.assertIsNone(_missing()[0].get("id"))


class NothingThatIsNotTheBooks(unittest.TestCase):
    def test_a_scalp_is_never_borrowed(self):
        """The CE scalp of 11-Sep sat in the account on a day the CE book had
        no trade. The old day-and-side rule borrowed it."""
        status = {"strategy": CE_BOOK, "closed_trades": [], "total_pnl": 0.0}
        merged = app._with_account_history(status, ACCOUNT, BOOK_TRADES)
        self.assertEqual([t["trading_symbol"] for t in merged["closed_trades"]], ["NIFTY 23750CE 2026-09-08"])

    def test_another_books_trade_is_never_borrowed(self):
        self.assertEqual([r["trading_symbol"] for r in _missing(PE_BOOK)], ["NIFTY 23700PE 2026-09-15"])

    def test_a_book_with_another_name_borrows_nothing(self):
        self.assertEqual(_missing(_book("CE", "Some_Other_CE")), [])

    def test_a_two_sided_book_borrows_nothing(self):
        strategy = {"run_name": "Straddle", "legs": [{"option_type": "CE"}, {"option_type": "PE"}]}
        self.assertEqual(_missing(strategy), [])

    def test_a_book_with_no_legs_borrows_nothing(self):
        self.assertEqual(_missing({}), [])


class TheBooksOwnRecordWins(unittest.TestCase):
    def test_a_trade_the_engine_still_holds_is_not_duplicated(self):
        own = [
            {
                "entry_time": "2026-09-03 09:20:05",
                "trading_symbol": "NIFTY 23750CE 2026-09-08",
                "entry_order_id": "34226090300001",
                "pnl": -1723.8,
            }
        ]
        self.assertEqual(_missing(own=own), [])

    def test_the_same_minute_and_strike_is_the_same_trade(self):
        own = [{"entry_time": "2026-09-03 09:20:59", "trading_symbol": "NIFTY 23750CE 2026-09-08", "pnl": -1723.8}]
        self.assertEqual(_missing(own=own), [])

    def test_another_trade_that_day_is_still_borrowed(self):
        own = [{"entry_time": "2026-09-03 13:00:00", "trading_symbol": "NIFTY 23900CE 2026-09-08", "pnl": 5.0}]
        self.assertEqual(len(_missing(own=own)), 1)

    def test_they_come_back_oldest_first(self):
        later = dict(
            CE_03, entry_time="2026-09-05 09:20:00", trading_symbol="NIFTY 23800CE 2026-09-08", entry_order_id="later"
        )
        rows = _missing(trades=[later, CE_03])
        self.assertEqual([r["entry_time"][:10] for r in rows], ["2026-09-03", "2026-09-05"])

    def test_nothing_saved_is_not_an_error(self):
        self.assertEqual(_missing(trades=[]), [])
        self.assertEqual(app._saved_trades_this_book_is_missing(CE_BOOK, [], None), [])


class TheHeadlineMovesWithTheRows(unittest.TestCase):
    def test_the_total_takes_the_borrowed_pnl_settled_by_the_broker(self):
        status = {"strategy": CE_BOOK, "closed_trades": [], "total_pnl": 0.0}
        merged = app._with_account_history(status, ACCOUNT, BOOK_TRADES)
        self.assertEqual(merged["total_pnl"], -1740.25)
        self.assertEqual(len(merged["closed_trades"]), 1)

    def test_a_book_with_nothing_to_borrow_is_returned_untouched(self):
        status = {"strategy": CE_BOOK, "closed_trades": [], "total_pnl": 0.0}
        self.assertIs(app._with_account_history(status, [], []), status)

    def test_an_existing_total_is_added_to_not_replaced(self):
        own = [{"entry_time": "2026-09-12 09:20:00", "trading_symbol": "NIFTY 23900CE", "pnl": 500.0}]
        status = {"strategy": CE_BOOK, "closed_trades": own, "total_pnl": 500.0}
        merged = app._with_account_history(status, ACCOUNT, BOOK_TRADES)
        self.assertEqual(merged["total_pnl"], round(500.0 - 1740.25, 2))


class ExactlyOnePageBorrows(unittest.TestCase):
    """`/api/engines/all` -> the Live page borrows; the CE + PE desk lists the
    saved trades itself as `history`; the builder preview does neither."""

    def _body(self, name):
        after = SRC.split(f"async def {name}(")[1]
        cuts = [after.find(marker) for marker in ("\n@app.", "\ndef ", "\nasync def ")]
        end = min(c for c in cuts if c != -1)
        return after[:end]

    def _calls(self, name):
        body = self._body(name)
        return "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))

    def test_the_live_page_borrows_from_the_saved_runs(self):
        calls = self._calls("engines_all")
        self.assertIn("_with_account_history(st, account_rows, book_trades)", calls)
        self.assertEqual(calls.count("await _live_book_trades("), 1)
        self.assertEqual(calls.count("await _account_option_history("), 1)

    def test_the_ce_pe_desk_does_not_borrow(self):
        calls = self._calls("live_runs")
        self.assertNotIn("_saved_trades_this_book_is_missing(", calls)
        self.assertNotIn("_with_account_history(", calls)

    def test_the_builder_preview_does_not(self):
        calls = self._calls("live_status")
        self.assertNotIn("_saved_trades_this_book_is_missing(", calls)
        self.assertNotIn("_with_account_history(", calls)

    def test_only_a_live_book_borrows(self):
        calls = self._calls("engines_all")
        paper_loop = calls.index("paper_engines")
        borrow = calls.index("_with_account_history(")
        self.assertLess(paper_loop, borrow)
        self.assertNotIn("_with_account_history(", calls[paper_loop:borrow])

    def test_the_old_account_borrowing_is_gone(self):
        self.assertNotIn("def _account_rows_this_book_is_missing(", SRC)


if __name__ == "__main__":
    unittest.main()
