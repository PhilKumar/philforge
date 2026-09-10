"""One trade, one row — with Dhan's money once Dhan has counted it.

The CE + PE desk prints every book's own closed list AND the whole broker record
underneath it, and nothing reconciled the two. So 2026-09-10's single PE trade
appeared twice:

    PE              2026-09-10  NIFTY 23700PE      265.00 → 279.45  BROKER MANUAL EXIT  ₹1,763
    PE  old closed  2026-09-10  NIFTY-Sep2026-...  265.00 → 279.45  —                   ₹1,879*

Five rows for four trades. Phil: "I have said I need one entry but why you are
making 2? Put the entry from Dhan or the engine one and once the charges updates
on Dhan, update the PL column on the next day."

They are the same trade and each half knows something the other does not. The
engine's row knows why it closed and can draw its own chart. The account's row
knows what it really cost — but only after Dhan books the day's charges
overnight, which is why one read ₹1,763 and the other ₹1,879 for the same 130
lots.

So the book's row survives, the account's copy is dropped, and when the charges
settle the account's net takes over the P&L column.
"""

import unittest

import app


def _engine_row(date, pnl, symbol="NIFTY 23700PE 2026-09-15"):
    return {
        "symbol": symbol,
        "entry_time": f"{date} 09:25:09",
        "exit_time": f"{date} 15:26:36",
        "pnl": pnl,
        "exit_reason": "BROKER_MANUAL_EXIT",
        "id": 7,
    }


def _account_row(date, side, pnl, costs_known=True, symbol="NIFTY-Sep2026-23700-PE"):
    return {
        "date": date,
        "side": side,
        "symbol": symbol,
        "pnl": pnl,
        "gross_pnl": 1878.5,
        "charges": 80.06 if costs_known else 0.0,
        "costs_known": costs_known,
    }


def _run(side, recent, booked=0.0):
    return {"side": side, "name": f"{side}_book", "booked_pnl": booked, "recent": list(recent)}


class TheDuplicateIsGone(unittest.TestCase):
    def test_a_trade_both_halves_know_prints_once(self):
        runs = [_run("PE", [_engine_row("2026-09-10", 1763.21)], 1763.21)]
        history = [_account_row("2026-09-10", "PE", 1878.5, costs_known=False)]
        left = app._one_row_per_trade(runs, history)
        self.assertEqual(left, [], "the account's copy of a booked trade must not survive")
        self.assertEqual(len(runs[0]["recent"]), 1, "the book's own row is the one that stays")

    def test_the_row_that_stays_is_the_one_that_can_explain_itself(self):
        runs = [_run("PE", [_engine_row("2026-09-10", 1763.21)], 1763.21)]
        app._one_row_per_trade(runs, [_account_row("2026-09-10", "PE", 1878.5, costs_known=False)])
        kept = runs[0]["recent"][0]
        self.assertEqual(kept["exit_reason"], "BROKER_MANUAL_EXIT")
        self.assertEqual(kept["id"], 7, "and can still draw its own chart")

    def test_the_whole_screenshot_comes_out_at_four_rows(self):
        runs = [
            _run("CE", []),
            _run("PE", [_engine_row("2026-09-10", 1763.21)], 1763.21),
        ]
        history = [
            _account_row("2026-09-10", "PE", 1878.5, costs_known=False),
            _account_row("2026-09-08", "PE", -2859.18, symbol="NIFTY 08 SEP 23950 PUT"),
            _account_row("2026-09-07", "PE", 979.12, symbol="NIFTY 08 SEP 24100 PUT"),
            _account_row("2026-09-03", "CE", -1740.25, symbol="NIFTY 08 SEP 23750 CALL"),
        ]
        left = app._one_row_per_trade(runs, history)
        printed = sum(len(r["recent"]) for r in runs) + len(left)
        self.assertEqual(printed, 4, "four trades, four rows")


class DhansMoneyWinsOnceDhanHasIt(unittest.TestCase):
    def test_the_pnl_column_updates_when_the_charges_settle(self):
        runs = [_run("PE", [_engine_row("2026-09-10", 1763.21)], 1763.21)]
        app._one_row_per_trade(runs, [_account_row("2026-09-10", "PE", 1798.44, costs_known=True)])
        row = runs[0]["recent"][0]
        self.assertEqual(row["pnl"], 1798.44)
        self.assertEqual(row["charges"], 80.06)
        self.assertTrue(row["settled_by_broker"])

    def test_an_unsettled_day_keeps_the_engines_own_figure(self):
        """Until Dhan books the charges its P&L is GROSS — ₹1,879 against the
        engine's ₹1,763. Taking it early would overstate the day."""
        runs = [_run("PE", [_engine_row("2026-09-10", 1763.21)], 1763.21)]
        app._one_row_per_trade(runs, [_account_row("2026-09-10", "PE", 1878.5, costs_known=False)])
        row = runs[0]["recent"][0]
        self.assertEqual(row["pnl"], 1763.21)
        self.assertNotIn("settled_by_broker", row)

    def test_the_books_booked_total_moves_with_the_correction(self):
        runs = [_run("PE", [_engine_row("2026-09-10", 1763.21)], 5000.0)]
        app._one_row_per_trade(runs, [_account_row("2026-09-10", "PE", 1798.44, costs_known=True)])
        self.assertEqual(runs[0]["booked_pnl"], round(5000.0 + (1798.44 - 1763.21), 2))

    def test_an_untouched_book_keeps_its_total_exactly(self):
        """`recent` is the tail of a longer list, so the total must never be
        added up again from the rows that happen to be visible."""
        runs = [_run("PE", [_engine_row("2026-09-10", 1763.21)], 99999.0)]
        app._one_row_per_trade(runs, [_account_row("2026-09-10", "PE", 1878.5, costs_known=False)])
        self.assertEqual(runs[0]["booked_pnl"], 99999.0)


class TheAccountStillCarriesWhatNoBookKnows(unittest.TestCase):
    def test_a_day_no_book_has_survives(self):
        runs = [_run("CE", [])]
        history = [_account_row("2026-09-03", "CE", -1740.25, symbol="NIFTY 08 SEP 23750 CALL")]
        self.assertEqual(len(app._one_row_per_trade(runs, history)), 1)

    def test_the_other_side_is_not_matched_away(self):
        """A PE row and a CE row on the same day are two different trades."""
        runs = [_run("PE", [_engine_row("2026-09-10", 1763.21)], 1763.21)]
        history = [
            _account_row("2026-09-10", "PE", 1878.5, costs_known=False),
            _account_row("2026-09-10", "CE", -500.0, symbol="A CALL"),
        ]
        left = app._one_row_per_trade(runs, history)
        self.assertEqual([r["side"] for r in left], ["CE"])

    def test_two_trades_on_one_day_are_left_alone(self):
        """A book takes one trade a day. Two means something is not what it
        seems, and showing a duplicate beats hiding the wrong one."""
        runs = [_run("PE", [_engine_row("2026-09-10", 1763.21)], 1763.21)]
        history = [
            _account_row("2026-09-10", "PE", 1878.5, costs_known=False),
            _account_row("2026-09-10", "PE", 42.0, symbol="ANOTHER PUT"),
        ]
        self.assertEqual(len(app._one_row_per_trade(runs, history)), 2)

    def test_newest_first(self):
        runs = [_run("PE", [])]
        history = [
            _account_row("2026-09-07", "PE", 979.12),
            _account_row("2026-09-10", "PE", 1878.5),
            _account_row("2026-09-08", "PE", -2859.18),
        ]
        left = app._one_row_per_trade(runs, history)
        self.assertEqual([r["date"] for r in left], ["2026-09-10", "2026-09-08", "2026-09-07"])

    def test_a_book_with_no_side_matches_nothing(self):
        runs = [{"side": "", "name": "?", "booked_pnl": 0.0, "recent": [_engine_row("2026-09-10", 1.0)]}]
        history = [_account_row("2026-09-10", "PE", 1878.5)]
        self.assertEqual(len(app._one_row_per_trade(runs, history)), 1)

    def test_an_empty_desk_is_not_an_error(self):
        self.assertEqual(app._one_row_per_trade([], []), [])
        self.assertEqual(app._one_row_per_trade([], None), [])


class ItIsWiredIntoTheDesk(unittest.TestCase):
    def test_live_runs_reconciles_before_it_answers(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
        body = src.split("async def live_runs(")[1].split("\n@app.")[0]
        self.assertIn("_one_row_per_trade(runs, await _account_option_history(user_id))", body)


if __name__ == "__main__":
    unittest.main()
