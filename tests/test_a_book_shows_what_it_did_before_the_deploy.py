"""A book's Completed Trades must not start empty just because it restarted.

CE_SL15_NoMonTue read "0 trades" and ₹0.00 on the Live desk while its real CE
trade of 2026-09-03 — NIFTY 08 SEP 23750 CALL, 294.73 → 282.38, −₹1,740.25 —
sat in the account table lower down the SAME page. Phil sent the screenshot
four times.

Twice I fixed it in `/api/live/status`. The desk does not call that endpoint.
It polls `/api/live/runs`, which builds every number a book shows — the closed
count, the booked total, the Completed Trades table — from ONE list. So the
borrowing has to happen where that list is assembled, and the last test here
pins that down, because the fix reads as correct either way and only works in
one of the two places.

The rows carry no attribution. The broker records the fill, not the strategy
that placed it, and Gap Carry has traded real NIFTY options since 02-Sep-2026,
so a CE row from the account is PROBABLY this book and never certainly it.
"""

import unittest
from pathlib import Path

import app

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "app.py").read_text(encoding="utf-8")

ACCOUNT = [
    {
        "date": "2026-09-10",
        "side": "PE",
        "symbol": "NIFTY-Sep2026-23700-PE",
        "entry_time": "2026-09-10 09:25",
        "exit_time": "2026-09-10 15:26",
        "entry_premium": 265.0,
        "exit_premium": 279.45,
        "quantity": 130,
        "gross_pnl": 1878.5,
        "charges": 0.0,
        "pnl": 1878.5,
    },
    {
        "date": "2026-09-03",
        "side": "CE",
        "symbol": "NIFTY 08 SEP 23750 CALL",
        "entry_time": "2026-09-03T09:20",
        "exit_time": "2026-09-03T10:45",
        "entry_premium": 294.73,
        "exit_premium": 282.38,
        "quantity": 130,
        "gross_pnl": -1605.5,
        "charges": 134.75,
        "pnl": -1740.25,
    },
]


def _book(side):
    return {"legs": [{"option_type": side}]}


def _missing(side, own=None, account=None):
    return app._account_rows_this_book_is_missing(_book(side), own or [], ACCOUNT if account is None else account)


class TheCeBookFindsItsCeTrade(unittest.TestCase):
    def test_the_row_comes_back(self):
        rows = _missing("CE")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trading_symbol"], "NIFTY 08 SEP 23750 CALL")
        self.assertEqual(rows[0]["pnl"], -1740.25)

    def test_it_is_shaped_like_an_engine_trade(self):
        """The desk's count, booked total and table are all derived from one
        list, so a borrowed row has to look like what that list holds."""
        row = _missing("CE")[0]
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

    def test_it_says_where_it_came_from(self):
        row = _missing("CE")[0]
        self.assertTrue(row["from_account"])
        self.assertEqual(row["exit_reason"], "from the account")

    def test_it_offers_no_journal_chart(self):
        """The chart button renders on `id`. An account row has no campaign to
        draw, so it must not advertise one."""
        self.assertIsNone(_missing("CE")[0].get("id"))


class TheOtherSideIsNotBorrowed(unittest.TestCase):
    def test_a_ce_book_does_not_take_pe_trades(self):
        symbols = [r["trading_symbol"] for r in _missing("CE")]
        self.assertNotIn("NIFTY-Sep2026-23700-PE", symbols)

    def test_a_pe_book_takes_only_its_own(self):
        rows = _missing("PE")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trading_symbol"], "NIFTY-Sep2026-23700-PE")

    def test_a_two_sided_book_borrows_nothing(self):
        """Nothing in the account says which leg of a straddle a fill was."""
        strategy = {"legs": [{"option_type": "CE"}, {"option_type": "PE"}]}
        self.assertEqual(app._account_rows_this_book_is_missing(strategy, [], ACCOUNT), [])

    def test_a_book_with_no_legs_borrows_nothing(self):
        self.assertEqual(app._account_rows_this_book_is_missing({}, [], ACCOUNT), [])


class TheBooksOwnRecordWins(unittest.TestCase):
    def test_a_day_the_engine_knows_is_not_duplicated(self):
        own = [{"entry_time": "2026-09-03 09:20:00", "trading_symbol": "NIFTY 23750CE", "pnl": -1605.5}]
        self.assertEqual(_missing("CE", own), [])

    def test_an_unrelated_day_is_still_borrowed(self):
        own = [{"entry_time": "2026-09-12 09:20:00", "pnl": 10.0}]
        self.assertEqual(len(_missing("CE", own)), 1)

    def test_they_come_back_oldest_first(self):
        account = ACCOUNT + [{**ACCOUNT[1], "date": "2026-09-05", "entry_time": "2026-09-05T09:20", "symbol": "LATER"}]
        rows = _missing("CE", account=account)
        self.assertEqual([r["trading_symbol"] for r in rows], ["NIFTY 08 SEP 23750 CALL", "LATER"])

    def test_an_empty_account_is_not_an_error(self):
        self.assertEqual(app._account_rows_this_book_is_missing(_book("CE"), [], []), [])
        self.assertEqual(app._account_rows_this_book_is_missing(_book("CE"), [], None), [])


class ExactlyOnePageBorrows(unittest.TestCase):
    """Three endpoints serve three pages, and the borrowing belongs in one.

    `/api/engines/all` -> the Live page. Its panel builds Completed Trades from
      `closed_trades` and has no account table of its own, so it borrows.
    `/api/live/runs` -> the CE + PE desk. It already receives the whole account
      record as `history` and prints it in an ALL TIME table, so borrowing there
      too showed every trade twice: once "from the account", once "old closed".
    `/api/live/status` -> the builder preview. One engine, different page.

    Two earlier attempts put it in `/api/live/status`, a third in
    `/api/live/runs`. Each read as correct. Each was wrong.
    """

    def _body(self, name):
        after = SRC.split(f"async def {name}(")[1]
        # Stop at the next top-level definition OR decorator, whichever comes
        # first: `live_status` is followed by a plain `def`, not by `@app.`,
        # and slicing only to the decorator swallowed the helper defined
        # between them — a test that passed while the page stayed broken.
        cuts = [after.find(marker) for marker in ("\n@app.", "\ndef ", "\nasync def ")]
        end = min(c for c in cuts if c != -1)
        return after[:end]

    def _calls(self, name):
        """The body with comments stripped: these tests are about what the code
        DOES. The comment in `live_runs` names the helper precisely to say it is
        not called there, and must not be read as calling it."""
        body = self._body(name)
        return "\n".join(line for line in body.splitlines() if not line.strip().startswith("#"))

    def test_the_live_page_borrows(self):
        self.assertIn("_with_account_history(", self._calls("engines_all"))

    def test_the_ce_pe_desk_does_not(self):
        """It prints the account record itself; borrowing would duplicate it."""
        calls = self._calls("live_runs")
        self.assertNotIn("_account_rows_this_book_is_missing(", calls)
        self.assertNotIn("_with_account_history(", calls)

    def test_the_builder_preview_does_not(self):
        calls = self._calls("live_status")
        self.assertNotIn("_account_rows_this_book_is_missing(", calls)
        self.assertNotIn("_with_account_history(", calls)

    def test_the_desk_still_receives_the_record_whole(self):
        """Removing the borrowing must not remove the ALL TIME table's source."""
        self.assertIn('"history": await _account_option_history(user_id)', self._body("live_runs"))

    def test_only_a_live_book_borrows(self):
        """A paper book's trades never reached the broker at all."""
        calls = self._calls("engines_all")
        paper_loop = calls.index("paper_engines")
        borrow = calls.index("_with_account_history(")
        self.assertLess(paper_loop, borrow, "the paper loop must be done before any borrowing")
        self.assertNotIn("_with_account_history(", calls[paper_loop:borrow])

    def test_the_account_is_read_once_per_request(self):
        self.assertEqual(self._calls("engines_all").count("await _account_option_history("), 1)


class TheHeadlineMovesWithTheRows(unittest.TestCase):
    def test_the_total_takes_the_borrowed_pnl(self):
        status = {"strategy": _book("CE"), "closed_trades": [], "total_pnl": 0.0}
        merged = app._with_account_history(status, ACCOUNT)
        self.assertEqual(merged["total_pnl"], -1740.25)
        self.assertEqual(len(merged["closed_trades"]), 1)

    def test_a_book_with_nothing_to_borrow_is_returned_untouched(self):
        status = {"strategy": _book("CE"), "closed_trades": [], "total_pnl": 0.0}
        self.assertIs(app._with_account_history(status, []), status)

    def test_an_existing_total_is_added_to_not_replaced(self):
        own = [{"entry_time": "2026-09-12 09:20:00", "pnl": 500.0}]
        status = {"strategy": _book("CE"), "closed_trades": own, "total_pnl": 500.0}
        merged = app._with_account_history(status, ACCOUNT)
        self.assertEqual(merged["total_pnl"], round(500.0 - 1740.25, 2))


if __name__ == "__main__":
    unittest.main()
