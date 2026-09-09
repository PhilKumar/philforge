"""Trades closed before a re-deploy must still appear, honestly labelled.

Phil, 2026-09-09: "I need that closed trades to be populated on the completed
trades with some note like {old closed}".

Re-deploying a book creates a fresh engine whose closed_trades list is empty,
so the desk showed nothing the book had done before. trade_history is the
durable record -- rebuilt FIFO from the broker's own fills -- and survives.

It carries NO strategy attribution, and Gap Carry has traded real NIFTY options
since 02-Sep-2026, so these rows cannot be claimed for the CE or PE book. They
are shown, tagged, dimmed, and captioned as the account's rather than the
book's.
"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
JS = open(os.path.join(ROOT, "static", "philforge-app.js"), encoding="utf-8").read()
HTML = open(os.path.join(ROOT, "strategy.html"), encoding="utf-8").read()
CSS = open(os.path.join(ROOT, "static", "philforge-app.css"), encoding="utf-8").read()


def helper() -> str:
    return APP.split("async def _account_option_history(")[1].split('\n@app.get("/api/live/runs")')[0]


def helper_code() -> str:
    """The helper with its docstring removed -- an assertion that matches the
    prose explaining a bug is not testing the code that avoids it."""
    body = helper()
    parts = body.split('"""')
    return parts[0] + "".join(parts[2:]) if len(parts) > 2 else body


class TheSourceOutlivesADeploy(unittest.TestCase):
    def test_it_reads_the_durable_record_not_the_engine(self):
        self.assertIn("list_trade_history", helper())
        self.assertNotIn("closed_trades", helper_code(), "engine state is emptied by a deploy")

    def test_the_runs_payload_carries_it(self):
        body = APP.split('@app.get("/api/live/runs")')[1].split("@app.get(")[0]
        self.assertIn('"history": await _account_option_history(user_id)', body)

    def test_only_real_money_and_only_nifty_options(self):
        block = helper_code()
        self.assertIn('"mode", ""', block)
        self.assertIn('!= "real"', block, "a paper day must not enter a real ledger")
        self.assertIn('"NIFTY" not in upper', block)

    def test_newest_first(self):
        self.assertIn('rows.sort(key=lambda r: r["date"], reverse=True)', helper_code())


class TheDeskSaysWhereTheyCameFrom(unittest.TestCase):
    def test_each_old_row_is_tagged(self):
        self.assertIn("cepe-old-tag", JS)
        self.assertIn("old closed", JS)

    def test_the_tag_explains_the_attribution_gap(self):
        """A row that may belong to another book must say so on hover."""
        tag = JS.split("cepe-old-tag")[1][:400]
        self.assertIn("broker account", tag)
        self.assertIn("another book", tag)

    def test_the_table_is_captioned(self):
        caption = HTML.split('id="oc-cepe-closed-count"')[0][-400:]
        self.assertIn("old closed", caption)
        self.assertIn("records no strategy", caption)

    def test_only_the_cepe_table_is_captioned(self):
        """Supertrend has a table with the same heading; it must be untouched."""
        self.assertEqual(HTML.count("records no strategy"), 1)

    def test_old_rows_are_dimmed_but_readable_on_hover(self):
        self.assertIn(".cepe-row-old", CSS)
        self.assertIn(".cepe-row-old:hover", CSS)

    def test_the_side_filter_still_applies_to_them(self):
        body = JS.split("function renderCePe(data)")[1].split("\nasync function refreshCePeStatus")[0]
        block = body.split("(data && data.history)")[1][:300]
        self.assertIn("_cepeFilter", block, "CE/PE filter must cover the old rows too")

    def test_they_are_sorted_in_with_the_live_rows(self):
        body = JS.split("function renderCePe(data)")[1].split("\nasync function refreshCePeStatus")[0]
        self.assertLess(
            body.index("(data && data.history)"),
            body.index("rows.sort("),
            "old rows must be pushed before the sort, not appended after it",
        )


if __name__ == "__main__":
    unittest.main()


class OneColumnMeansOneThing(unittest.TestCase):
    """Phil, 2026-09-09: "does this include the charges? Both closed and the
    live ones".

    It did not. The engine books pnl = gross - charges on every live exit, but
    trade_history stores GROSS per leg -- its day-level net_pnl is pnl minus
    total_costs. Reading the leg's pnl straight put net and gross in the same
    column and overstated the older half of the ledger.
    """

    def test_the_account_rows_are_netted(self):
        block = helper_code()
        self.assertIn('"pnl": round(gross - costs, 2)', block)
        self.assertIn('"gross_pnl": gross', block)
        self.assertIn('"charges": costs', block)

    def test_the_live_rows_send_their_breakdown_too(self):
        body = APP.split('@app.get("/api/live/runs")')[1].split("@app.get(")[0]
        self.assertIn('"gross_pnl": t.get("gross_pnl")', body)
        self.assertIn('"charges": t.get("charges")', body)

    def test_the_engine_pnl_really_is_net(self):
        """If this ever stops being true, netting the account rows would be
        wrong in the other direction."""
        live = open(os.path.join(ROOT, "engine", "live.py"), encoding="utf-8").read()
        self.assertIn("pnl = round(gross - charges, 2)", live)

    def test_the_column_says_what_it_holds(self):
        self.assertIn("Net of charges", HTML)

    def test_every_figure_carries_its_deduction(self):
        self.assertIn("function _cepeNetNote(", JS)
        note = JS.split("function _cepeNetNote(")[1][:500]
        self.assertIn("Gross", note)
        self.assertIn("charges", note)

    def test_a_day_with_no_charges_booked_is_flagged_not_silently_gross(self):
        block = helper_code()
        self.assertIn('"costs_known": costs > 0', block)
        note = JS.split("function _cepeNetNote(")[1][:500]
        self.assertIn("costs_known === false", note)
        self.assertIn("cepe-nocost", JS)


class TheStampIsCompleteOrHonest(unittest.TestCase):
    """Phil, 2026-09-09: "Why no proper time here? I need the exact time and date".

    Two faults. The formatter sliced [5:16], dropping the YEAR -- so an April
    trade read "04-07" and could not be told from a September one. And the day
    summary kept only the fill's date: _trade_date_str truncates the broker's
    stamp to [:10], and the per-symbol detail stored no time at all, so the
    minute was thrown away at write time and no display could recover it.
    """

    def test_the_broker_stamp_is_kept_whole(self):
        self.assertIn("def _trade_stamp_str(", APP)
        block = APP.split("def _trade_stamp_str(")[1].split("\ndef ")[0]
        self.assertIn("[:16]", block, "keep the minute, not just the day")

    def test_the_day_summary_records_when_it_opened_and_closed(self):
        self.assertIn('"first_buy": ""', APP)
        self.assertIn('"last_sell": ""', APP)
        self.assertIn('detail["first_buy"]', APP)
        self.assertIn('detail["last_sell"]', APP)

    def test_existing_rows_are_forced_to_be_rebuilt(self):
        """Rows written before this carry no times; only a schema bump makes
        the startup backfill re-pull them from Dhan."""
        self.assertIn("_TRADE_HISTORY_SCHEMA_VERSION = 5", APP)

    def test_the_payload_carries_both_stamps(self):
        block = helper_code()
        self.assertIn('"entry_time": str(leg.get("first_buy")', block)
        self.assertIn('"exit_time": str(leg.get("last_sell")', block)

    def test_the_display_keeps_the_year(self):
        fn = JS.split("const _cepeTime = ")[1][:600]
        self.assertIn("slice(0, 10)", fn, "the full date, not a month-day")
        self.assertNotIn("slice(5, 16)", fn, "that dropped the year")

    def test_a_missing_time_says_so_rather_than_showing_a_bare_date(self):
        fn = JS.split("const _cepeTime = ")[1][:600]
        self.assertIn("time not recorded", fn)

    def test_a_row_falls_back_to_the_day_when_the_broker_gave_no_time(self):
        body = JS.split("function renderCePe(data)")[1].split("\nasync function refreshCePeStatus")[0]
        self.assertIn("h.entry_time || h.date", body)
        self.assertIn("h.exit_time || h.date", body)
