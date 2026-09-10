"""A book's Completed Trades must not start empty just because it restarted.

CE_SL15_NoMonTue read "0 trades" and ₹0.00 on the Live page while its real CE
trade of 2026-09-03 — NIFTY 08 SEP 23750 CALL, 294.73 → 282.38, −₹1,740.25 —
sat in the account table below it. Phil asked three times.

An engine's own list only covers what it has closed since it last started, so
the account record fills the gap. That record has NO strategy attribution: the
broker logs the fill, not the book that placed it, and Gap Carry has traded
real NIFTY options since 02-Sep-2026. So these rows are matched on option side
only, tagged `from_account`, and never allowed to override a day the engine
actually knows about.
"""

import asyncio
import unittest
from unittest.mock import patch

import app

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
        "pnl": -1740.25,
    },
]


def _status(side, closed=None, total_pnl=0.0):
    return {
        "run_id": "book",
        "strategy": {"legs": [{"option_type": side}]},
        "closed_trades": list(closed or []),
        "total_pnl": total_pnl,
    }


def _merge(status, account=None):
    rows = ACCOUNT if account is None else account

    async def _history(_user_id, limit=60):
        return rows

    with patch.object(app, "_account_option_history", _history):
        return asyncio.run(app._with_account_history(status, 1))


class TheCeBookFindsItsCeTrade(unittest.TestCase):
    def test_the_row_appears(self):
        merged = _merge(_status("CE"))
        self.assertEqual(len(merged["closed_trades"]), 1)
        self.assertEqual(merged["closed_trades"][0]["symbol"], "NIFTY 08 SEP 23750 CALL")

    def test_the_headline_moves_with_it(self):
        """A tile reading ₹0.00 over a table of real losses is worse than both."""
        merged = _merge(_status("CE"))
        self.assertEqual(merged["total_pnl"], -1740.25)

    def test_it_is_tagged_as_coming_from_the_account(self):
        """It is probably this book and cannot be shown as certainly this book."""
        row = _merge(_status("CE"))["closed_trades"][0]
        self.assertTrue(row["from_account"])
        self.assertEqual(row["exit_reason"], "from the account")

    def test_the_renderer_has_every_field_it_reads(self):
        row = _merge(_status("CE"))["closed_trades"][0]
        for field in (
            "symbol",
            "entry_time",
            "exit_time",
            "transaction_type",
            "entry_premium",
            "exit_premium",
            "quantity",
            "pnl",
            "exit_reason",
        ):
            self.assertIn(field, row, field)


class TheOtherSideIsNotBorrowed(unittest.TestCase):
    def test_a_ce_book_does_not_take_pe_trades(self):
        symbols = [t["symbol"] for t in _merge(_status("CE"))["closed_trades"]]
        self.assertNotIn("NIFTY-Sep2026-23700-PE", symbols)

    def test_a_pe_book_takes_only_its_own(self):
        merged = _merge(_status("PE"))
        self.assertEqual(len(merged["closed_trades"]), 1)
        self.assertEqual(merged["closed_trades"][0]["side"], "PE")

    def test_a_two_sided_book_borrows_nothing(self):
        """Nothing in the account says which side of a straddle a fill was."""
        status = {"strategy": {"legs": [{"option_type": "CE"}, {"option_type": "PE"}]}, "closed_trades": []}
        self.assertEqual(_merge(status)["closed_trades"], [])


class TheBooksOwnRecordWins(unittest.TestCase):
    def test_a_day_the_engine_knows_is_not_duplicated(self):
        own = [{"entry_time": "2026-09-03 09:20:00", "symbol": "NIFTY 23750CE", "pnl": -1605.5}]
        merged = _merge(_status("CE", own))
        self.assertEqual(len(merged["closed_trades"]), 1)
        self.assertEqual(merged["closed_trades"][0]["symbol"], "NIFTY 23750CE")

    def test_the_headline_is_untouched_when_nothing_is_borrowed(self):
        own = [{"entry_time": "2026-09-03 09:20:00", "pnl": -1605.5}]
        merged = _merge(_status("CE", own, total_pnl=-1605.5))
        self.assertEqual(merged["total_pnl"], -1605.5)

    def test_borrowed_rows_come_before_the_engines_own(self):
        """Oldest first, matching the engine's list, which the panel reverses."""
        own = [{"entry_time": "2026-09-12 09:20:00", "symbol": "NEWER", "pnl": 10.0}]
        merged = _merge(_status("CE", own))
        self.assertEqual([t.get("symbol") for t in merged["closed_trades"]], ["NIFTY 08 SEP 23750 CALL", "NEWER"])


class ItCannotBreakTheStatus(unittest.TestCase):
    def test_a_broker_record_that_will_not_load_changes_nothing(self):
        async def _boom(_user_id, limit=60):
            raise RuntimeError("db gone")

        with patch.object(app, "_account_option_history", _boom):
            merged = asyncio.run(app._with_account_history(_status("CE"), 1))
        self.assertEqual(merged["closed_trades"], [])

    def test_a_status_with_no_strategy_is_returned_as_is(self):
        merged = _merge({"closed_trades": [], "total_pnl": 0})
        self.assertEqual(merged["closed_trades"], [])


if __name__ == "__main__":
    unittest.main()
