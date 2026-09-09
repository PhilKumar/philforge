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
