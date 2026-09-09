"""The options tearsheet must carry the compounding settings that are live.

Phil, 2026-09-09: "I want to create this and update these in the tearsheet".

It must also carry the warning that goes with them, because the five-year
multiple is one year in both books.
"""

import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = open(os.path.join(ROOT, "tools", "tearsheet", "build_report.py"), encoding="utf-8").read()
DATA = json.load(open(os.path.join(ROOT, "tools", "tearsheet", "report_data.json"), encoding="utf-8"))


class TheDataIsThere(unittest.TestCase):
    def test_the_block_exists(self):
        self.assertIn("compounding", DATA)

    def test_it_records_the_settings_that_are_live(self):
        live = DATA["compounding"]["live_settings"]
        self.assertEqual(
            live["ce"], {"lots": 2, "expiry_day_lots": 3, "step_pct": 25, "max_lots": 20, "base_capital": 100000}
        )
        self.assertEqual(
            live["pe"], {"lots": 1, "expiry_day_lots": 3, "step_pct": 75, "max_lots": 20, "base_capital": 100000}
        )

    def test_it_says_the_numbers_came_from_the_engine(self):
        """The invalid method must be named, so it is not repeated."""
        why = DATA["compounding"]["why_engine"]
        self.assertIn("flat", why)
        self.assertIn("brokerage", why.lower())


class TheDocumentSaysIt(unittest.TestCase):
    def test_the_section_is_rendered(self):
        self.assertIn("Sizing up as the book earns", BUILD)

    def test_the_concentration_warning_travels_with_the_multiple(self):
        """A 12.92x with no mention that 93% is one year would mislead."""
        block = BUILD.split("Sizing up as the book earns")[1].split("What is running today")[0]
        self.assertIn("top_year_pct", block)
        self.assertIn("y2026", block)

    def test_the_live_size_lines_match_the_live_settings(self):
        live = DATA["compounding"]["live_settings"]
        self.assertIn("1 lot, 3 on expiry, +1 lot per +%d%% banked" % live["pe"]["step_pct"], BUILD)
        self.assertIn("2 lots, 3 on expiry, +1 lot per +%d%% banked" % live["ce"]["step_pct"], BUILD)
        self.assertNotIn('{t("4 lots, BUY"', BUILD, "the stale 4-lot description is gone")

    def test_it_does_not_leak_into_the_shared_helper(self):
        """method_and_limits is shared by all five tearsheets."""
        helper = BUILD.split("def method_and_limits")[1].split("\ndef ")[0]
        for word in ("compound", "Sizing up", "12.92"):
            self.assertNotIn(word, helper, f"{word} would appear on Fib/Candle/Gap/Supertrend too")


class TheBuiltFileAgrees(unittest.TestCase):
    def test_the_html_carries_it(self):
        path = os.path.join(ROOT, "docs", "assets", "backtest-tearsheet-5yr.html")
        if not os.path.exists(path):
            self.skipTest("tearsheet not built here")
        html = open(path, encoding="utf-8").read()
        for needle in ("Sizing up as the book earns", "12.92", "4.25", "1 lot, 3 on expiry"):
            self.assertIn(needle, html, needle)


if __name__ == "__main__":
    unittest.main()
