"""The five-year tearsheet says what its data says (Phil, 2026-09-22).

The call book lost money in 2026 and the page said so in typed words. The
20-day trend filter turned that year green, and typed words would have gone on
saying LOSE under a positive number. Every clause whose truth depends on a sign
is now chosen from report_data.json, and the page carries the four-book section
and the call book's filter.
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEARSHEET = ROOT / "tools" / "tearsheet"
BUILDER = (TEARSHEET / "build_report.py").read_text(encoding="utf-8")
DATA = json.loads((TEARSHEET / "report_data.json").read_text(encoding="utf-8"))
PAGE = (ROOT / "docs" / "assets" / "backtest-tearsheet-5yr.html").read_text(encoding="utf-8")


class NoTypedClaimAboutASign(unittest.TestCase):
    def test_the_2026_clause_is_chosen_from_the_number(self):
        self.assertIn("CE_2026_CLAUSE", BUILDER)
        self.assertIn("_ce_2026 < 0", BUILDER)

    def test_the_page_agrees_with_the_call_books_2026(self):
        y2026 = DATA["compounding"]["ce"]["y2026"]
        self.assertIn("eight months of 2026 LOSE" if y2026 < 0 else "eight months of 2026 add", PAGE)
        self.assertNotIn("eight months of 2026 add" if y2026 < 0 else "eight months of 2026 LOSE", PAGE)

    def test_the_cap_sentence_agrees_with_the_caps(self):
        caps = [c["y2026"] for c in DATA["compounding"]["ce_caps"]]
        if all(v > 0 for v in caps):
            self.assertIn("2026 is in profit at every cap", PAGE)
            self.assertNotIn("does not reduce the 2026 loss", PAGE)
        elif all(v < 0 for v in caps):
            self.assertIn("does not reduce the 2026 loss", PAGE)


class TheFilterIsOnThePage(unittest.TestCase):
    def test_the_call_book_card_shows_its_trend_filter(self):
        trend = DATA["live_config"]["ce"].get("trend_filter")
        self.assertTrue(trend, "the published CE config should record its trend filter")
        self.assertIn(trend, PAGE)
        self.assertIn("Trend filter", PAGE)

    def test_the_archive_comparison_says_when_it_was_measured(self):
        self.assertIn(DATA["source_comparison"]["dated"], PAGE)


class TheFourBooks(unittest.TestCase):
    def test_the_section_is_rendered(self):
        self.assertIn("All four books together", PAGE)

    def test_every_book_and_both_totals_are_shown(self):
        fb = DATA["four_books"]
        for key in ("ce", "pe", "gap", "fib", "three", "four"):
            self.assertIn(f"{abs(fb[key]['net']):,}".replace(",", ""), PAGE.replace(",", ""))

    def test_the_totals_are_the_sum_of_the_books(self):
        fb = DATA["four_books"]
        self.assertEqual(fb["three"]["net"], fb["ce"]["net"] + fb["pe"]["net"] + fb["gap"]["net"])
        self.assertEqual(fb["four"]["net"], fb["three"]["net"] + fb["fib"]["net"])
        for key in ("three", "four"):
            # each year is rounded on its own, so the parts may sit a rupee off
            self.assertAlmostEqual(sum(fb[key]["by_year"].values()), fb[key]["net"], delta=len(fb[key]["by_year"]))

    def test_the_pair_of_option_books_still_reconciles(self):
        fb = DATA["four_books"]
        self.assertEqual(fb["ce"]["net"], round(DATA["headline"]["ce"]["net"]))
        self.assertEqual(fb["pe"]["net"], round(DATA["headline"]["pe"]["net"]))


class TheBookRebuildIsInTheRepo(unittest.TestCase):
    def test_the_builder_is_not_a_scratchpad_script_any_more(self):
        self.assertTrue((TEARSHEET / "ladder_report.py").exists())
        doc = (TEARSHEET / "ladder_report.py").read_text(encoding="utf-8")
        self.assertIn("--check", doc)
        self.assertIn("philforge_strategy_on_dhan.py", doc)


if __name__ == "__main__":
    unittest.main()
