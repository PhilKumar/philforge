"""Everything that publishes the five-year book must agree with the book.

Written after a real miss: a commit regenerated report_data.json and rewrote the
landing from it, but never re-rendered docs/assets/backtest-tearsheet-5yr.html —
the document the Assets page actually serves. It still carried the August
figures. Playwright caught it in CI, the deploy was skipped, and Gap Carry did
not go live until the render was run by hand (57a8ca8).

The Playwright suite already guards the served tearsheet, but it needs a browser,
a running app and twelve minutes of CI. These checks are offline and take
milliseconds, so the same mistake is caught before it is ever committed. The
landing had no guard at all: its figures are hand-typed into forge.html and
dojima.js, which is exactly why they were able to sit fifteen days out of date.
"""

from __future__ import annotations

import json
import pathlib
import re
import unittest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_DATA = _REPO / "tools" / "tearsheet" / "report_data.json"
_LEDGERS = {
    "ce": _REPO / "tools" / "tearsheet" / "published_ce_snapshot_ledger.json",
    "pe": _REPO / "tools" / "tearsheet" / "published_pe_snapshot_ledger.json",
}


def _inr(value: float) -> str:
    """Indian grouping, the way both the page and the document print it."""
    number = int(round(value))
    digits = str(abs(number))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        digits = ",".join(parts + [tail])
    return ("-" if number < 0 else "") + digits


class PublishedFiguresMatchTheBook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(_DATA.read_text())
        cls.head = cls.data["headline"]["combined"]
        cls.charges = cls.data["charges"]
        cls.sizing = {row["lots"]: row for row in cls.data["sizing"]}
        cls.day = cls.data["daily"]

    def test_served_tearsheet_carries_the_current_net(self):
        """The document the Assets page serves, not the one in the repo root."""
        doc = _REPO / "docs" / "assets" / "backtest-tearsheet-5yr.html"
        self.assertTrue(doc.exists(), f"{doc} is missing")
        net = _inr(self.head["net"])
        # assertIn would print the whole 340KB document on failure; the useful
        # information is the figure and the command that fixes it.
        self.assertTrue(
            net in doc.read_text(),
            f"{doc.name} does not carry {net}. report_data.json was rebuilt without "
            f"re-running `python3 tools/tearsheet/build_report.py` — this is the exact "
            f"miss that skipped a deploy.",
        )

    def test_landing_stats_match_the_book(self):
        """forge.html's animated stats are typed by hand, so they can drift."""
        html = (_REPO / "static" / "landing" / "forge.html").read_text()
        for label, expected in (
            ("net", int(round(self.head["net"]))),
            ("trades", self.head["trades"]),
            ("profit factor x100", int(round(self.head["profit_factor"] * 100))),
            ("return/drawdown x100", int(round(self.head["return_over_dd"] * 100))),
            ("max drawdown", int(round(self.head["max_dd"]))),
        ):
            self.assertIn(
                f'data-count="{expected}"',
                html,
                f'forge.html has no data-count="{expected}" for {label}',
            )

    def test_landing_tape_matches_the_book(self):
        """dojima.js carries the same figures again, in its own tape."""
        js = (_REPO / "static" / "landing" / "dojima.js").read_text()
        for label, needle in (
            ("net", f'"₹{_inr(self.head["net"])}"'),
            ("trades", f'"{self.head["trades"]}"'),
            ("win rate", f'"{self.head["win_rate"]}%"'),
            ("max drawdown", f'"−₹{_inr(abs(self.head["max_dd"]))}"'),
            ("charges", f'"₹{_inr(self.charges["total"])}"'),
            ("trading days", f'"{self.day["trading_days"]}"'),
            ("green months", f'"{self.day["green_months"]} / {self.day["months"]}"'),
        ):
            self.assertIn(needle, js, f"dojima.js tape has no {label} {needle}")

    def test_landing_equity_curve_ends_on_the_net(self):
        """The drawn curve and the printed headline are the same money."""
        js = (_REPO / "static" / "landing" / "dojima.js").read_text()
        match = re.search(r'const SER=\[.*?\["[0-9-]+",-?\d+,(-?\d+)\]\];', js, re.S)
        self.assertIsNotNone(match, "could not find the SER curve in dojima.js")
        self.assertEqual(
            int(match.group(1)),
            int(round(self.head["net"])),
            "dojima.js's equity curve does not end on the published net",
        )

    def test_results_page_uses_read_only_runs_instead_of_a_standalone_panel(self):
        """The two books belong in the run ledger, backed by full trade records."""
        page = (_REPO / "strategy.html").read_text(encoding="utf-8")
        backend = (_REPO / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("pf-tearsheet-evidence", page)
        self.assertIn('id="runs-list-results"', page)
        for _, label in (("ce", "CE_SL15_NoMonTue"), ("pe", "PE_NoTarget")):
            self.assertIn(label, backend)
        self.assertIn('headline = report["headline"][book]', backend)
        self.assertIn("_PUBLISHED_RUNS_LEDGER_PATHS", backend)
        self.assertIn('raw_trades = ledger["trades_full"]', backend)
        self.assertIn('"entry_price": row["entry_price"]', backend)
        self.assertIn('"exit_price": row["exit_price"]', backend)
        self.assertIn('"qty": row["qty"]', backend)
        self.assertIn('"day_of_week": day_of_week', backend)
        self.assertIn('"yearly": yearly', backend)
        self.assertIn("published_historical", backend)
        self.assertIn("predates the newer S4/S5 exit-rule deployment", backend)

    def test_published_ledgers_reconcile_and_carry_real_fills(self):
        """Each Results row has the fills, timestamps, quantity and net it claims."""
        for book in ("ce", "pe"):
            headline = self.data["headline"][book]
            ledger = json.loads(_LEDGERS[book].read_text())
            rows = ledger["trades_full"]
            self.assertEqual(len(rows), headline["trades"])
            self.assertEqual(round(sum(float(row["pnl"]) for row in rows), 2), headline["net"])
            self.assertEqual(sum(float(row["pnl"]) > 0 for row in rows), headline["wins"])
            for row in rows:
                self.assertTrue(row["entry_time"] and row["exit_time"])
                self.assertGreater(float(row["entry_price"]), 0)
                self.assertGreater(float(row["exit_price"]), 0)
                self.assertGreater(int(row["qty"]), 0)
                self.assertTrue(row["strike"])
                self.assertTrue(row["exit_reason"])


if __name__ == "__main__":
    unittest.main()
