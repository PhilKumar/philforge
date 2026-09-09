"""Every line of "What is running today" must equal the deployed config.

Phil, 2026-09-09: "update the tearsheet with the correct data from this
strategy clearly line by line if we are going to have this from now on".

Three things were wrong when he asked. The call book card named
My_First_Run_CE, a strategy that is not deployed. The slippage paragraph
quoted 6/8/12 bps while the engine runs 10/14/18. And the cards omitted the
signal cutoff, the indicators and the compounding entirely.

The cure is that the card is rendered FROM report_data.json's live_config
block rather than typed, and this test asserts every value in that block
reaches the page. Correcting the config now means editing one place.
"""

import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = open(os.path.join(ROOT, "tools", "tearsheet", "build_report.py"), encoding="utf-8").read()
DATA = json.load(open(os.path.join(ROOT, "tools", "tearsheet", "report_data.json"), encoding="utf-8"))
LC = DATA.get("live_config") or {}
HTML_PATH = os.path.join(ROOT, "docs", "assets", "backtest-tearsheet-5yr.html")


class TheConfigIsRecorded(unittest.TestCase):
    def test_the_block_exists_and_says_when_it_was_read(self):
        self.assertIn("read_from", LC)
        self.assertIn("philforge.db", LC["read_from"])

    def test_both_books_are_named_as_deployed(self):
        self.assertEqual(LC["pe"]["run_name"], "PE_NoTarget")
        self.assertEqual(LC["ce"]["run_name"], "CE_SL15_NoMonTue")

    def test_the_two_books_keep_their_different_indicators(self):
        """They are not the same rule mirrored: the call book runs EMA 17 and
        Supertrend 10/2.7, the put book EMA 20 and Supertrend 10/2."""
        self.assertIn("Supertrend_10_2_3m", LC["pe"]["indicators"])
        self.assertIn("EMA_20_5m", LC["pe"]["indicators"])
        self.assertIn("Supertrend_10_2.7_3m", LC["ce"]["indicators"])
        self.assertIn("EMA_17_5m", LC["ce"]["indicators"])

    def test_the_sizing_matches_what_set_ladder_writes(self):
        live = DATA["compounding"]["live_settings"]
        for book in ("pe", "ce"):
            self.assertEqual(LC[book]["lots"], live[book]["lots"], book)
            self.assertEqual(LC[book]["expiry_day_lots"], live[book]["expiry_day_lots"], book)
            self.assertIn(str(live[book]["step_pct"]), LC[book]["ladder"], book)


class TheCardIsRenderedNotTyped(unittest.TestCase):
    def test_the_card_interpolates_the_block(self):
        # two sections share that heading; the one with the cards is the
        # second, so anchor on the cards themselves
        section = BUILD.split('<div class="cfg">')[1].split("</section>")[0]
        for token in (
            'LC["pe"]["run_name"]',
            'LC["ce"]["run_name"]',
            'LC["pe"]["indicators"]',
            'LC["ce"]["indicators"]',
            'LC["shared"]["signal_cutoff"]',
        ):
            self.assertIn(token, section, token)

    def test_the_dead_strategy_is_gone(self):
        self.assertNotIn("My_First_Run_CE", BUILD)

    def test_execution_numbers_come_from_one_place(self):
        """A paragraph quoting 6/8/12 beside a card quoting 10/14/18 is worse
        than either being wrong on its own."""
        self.assertIn("_SLIP", BUILD)
        self.assertNotIn('<span class="num">6 bps</span>', BUILD)


class TheBuiltPageCarriesIt(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(HTML_PATH):
            self.skipTest("tearsheet not built here")
        self.html = open(HTML_PATH, encoding="utf-8").read()

    def test_every_recorded_value_reaches_the_page(self):
        for book in ("pe", "ce"):
            for key in ("run_name", "strike", "ladder", "leg_stop", "cool_off"):
                self.assertIn(str(LC[book][key]), self.html, f"{book}.{key}")
            for ind in LC[book]["indicators"]:
                self.assertIn(ind, self.html, ind)

    def test_the_shared_lines_reach_it_too(self):
        for key in ("signal_cutoff", "square_off", "market", "expiry", "capital_check"):
            self.assertIn(str(LC["shared"][key]), self.html, key)

    def test_the_slippage_paragraph_agrees_with_the_card(self):
        s = LC["shared"]["slippage_bps"]
        for v in (s["entry"], s["exit"], s["spread"]):
            self.assertIn(f"{v} bps", self.html, str(v))

    def test_exactly_two_books_are_described(self):
        section = self.html.split("What is running today")[1].split("</section>")[0]
        self.assertEqual(section.count('<div class="cfg-card">'), 2)


if __name__ == "__main__":
    unittest.main()
