"""The five-year tearsheet carries the stricter replay beside its own numbers.

The published sheet reports Rs 12,45,087 over 920 trades. Replaying the same
two books on 8 September 2026 returned Rs 3,57,012. Both are right, and the
difference is entirely explained: FOUR lots against two, an archive that prices
the same contract 21% higher, an as-filled basis against a costed one, and a
first three and three-quarter years spliced in from broker exports rather than
from any replay.

Where the two CAN be measured identically -- October 2024 onward, four lots, no
execution costs on either side -- they agree to 3.1%: Rs 3,76,361 on 215 trades
against the document's own Rs 3,88,450 on 211.

Phil, 2026-09-08: "Don't under estimate the data which is already there." So the
published figures are not touched; the replay is added beside them.

This file also pins the near-miss. `method_and_limits` is SHARED by all five
tearsheet builders, and a section written into it would have leaked CE/PE
reconciliation text into the Fib, Candle Entry, Gap Carry and Supertrend sheets.
"""

import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = open(os.path.join(ROOT, "docs", "assets", "backtest-tearsheet-5yr.html"), encoding="utf-8").read()
BUILDER = open(os.path.join(ROOT, "tools", "tearsheet", "build_report.py"), encoding="utf-8").read()
DATA = json.load(open(os.path.join(ROOT, "tools", "tearsheet", "report_data.json"), encoding="utf-8"))


class ThePublishedFiguresAreUntouched(unittest.TestCase):
    def test_the_headline_still_says_what_it_said(self):
        self.assertEqual(DATA["headline"]["combined"]["net"], 1245086.92)
        self.assertEqual(DATA["headline"]["combined"]["trades"], 920)
        self.assertIn("₹12,45,087", DOC)

    def test_the_document_still_declares_four_lots(self):
        self.assertEqual(DATA["lots"], 4)


class TheReconciliationIsThereAndAddsUp(unittest.TestCase):
    def setUp(self):
        self.bridge = DATA["reconciliation"]["bridge"]

    def test_the_data_block_exists(self):
        self.assertIn("reconciliation", DATA)
        self.assertEqual(DATA["reconciliation"]["lots"], 2)

    def test_doubling_the_lots_is_exactly_linear(self):
        self.assertEqual(self.bridge["pe_dhan_4lot_costed"], 2 * self.bridge["pe_dhan_2lot_costed"])

    def test_the_two_measurements_agree_once_made_the_same_way(self):
        mine = self.bridge["engine_window_uncosted"]
        theirs = self.bridge["tearsheet_engine_portion"]
        gap = abs(theirs - mine) / theirs * 100
        self.assertLess(gap, 5.0, f"the reconciliation no longer closes: {gap:.1f}% apart")
        self.assertAlmostEqual(gap, self.bridge["residual_pct"], delta=0.6)

    def test_removing_costs_only_ever_helps(self):
        self.assertGreater(self.bridge["engine_window_uncosted"], self.bridge["engine_window_costed"])

    def test_the_archive_gap_is_priced_not_substituted(self):
        """93% of the overlap picks the SAME strike — so it is a price gap."""
        self.assertGreaterEqual(self.bridge["same_strike_share_pct"], 90)
        pair = self.bridge["dhan_vs_upstox_same_strike"]
        self.assertGreater(pair["upstox"], pair["dhan"])

    def test_the_section_renders_into_the_document(self):
        for figure in ("3,76,361", "3,88,450", "2,76,348", "+21.4%"):
            self.assertIn(figure, DOC, f"{figure} is missing from the rendered sheet")
        self.assertIn("second measurement", DOC)

    def test_it_carries_the_findings_that_change_behaviour(self):
        for figure in ("2,11,125", "97,343", "79,519"):
            self.assertIn(figure, DOC, figure)


class TheSharedHelperIsNotPolluted(unittest.TestCase):
    def test_the_section_is_not_written_into_the_shared_builder_helper(self):
        """method_and_limits is used by all five tearsheets."""
        helper = BUILDER[BUILDER.index("def method_and_limits(") :]
        helper = helper[: helper.index("\ndef ")]
        for leak in ("second measurement", "Dhan archive", "reconciliation"):
            self.assertNotIn(leak, helper, f"{leak!r} would leak into the other four tearsheets")

    def test_the_other_four_builders_do_not_read_the_reconciliation(self):
        for name in ("build_fib_report", "build_candle_report", "build_gapcarry_report", "build_supertrend_report"):
            src = open(os.path.join(ROOT, "tools", "tearsheet", f"{name}.py"), encoding="utf-8").read()
            self.assertNotIn("reconciliation", src, name)


if __name__ == "__main__":
    unittest.main()
