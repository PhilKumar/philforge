"""Gap Carry sizes up on money it has banked, the same way CE and PE do.

Phil, 2026-09-10: "Just add this methodology in Gap carry on the engine to
trade".

One helper serves the replay and the live engine, because a size decided in a
backtest and a size decided at 15:10 with real money must not be able to
disagree. The step is a SETTING with compounding OFF by default: measured over
179 nights, +75% came out worse per rupee of capital than not compounding at
all, which is a sample small enough that the ordering of the thresholds may be
noise rather than structure.
"""

import os
import unittest

from engine.gap_carry import GapCarryConfig, GapCarryError, laddered_lots

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER = open(os.path.join(ROOT, "engine", "gap_carry_paper.py"), encoding="utf-8").read()
REPLAY = open(os.path.join(ROOT, "engine", "gap_carry.py"), encoding="utf-8").read()


def cfg(**kw):
    base = {"lots": 1, "compound_step_pct": 25.0, "compound_base_capital": 100_000}
    base.update(kw)
    return GapCarryConfig(**base)


class TheRule(unittest.TestCase):
    def test_nothing_banked_is_the_base_size(self):
        self.assertEqual(laddered_lots(cfg(), 0), 1)

    def test_a_rung_adds_exactly_one_lot(self):
        self.assertEqual(laddered_lots(cfg(), 24_999), 1)
        self.assertEqual(laddered_lots(cfg(), 25_000), 2)

    def test_a_loss_never_sizes_below_the_base(self):
        self.assertEqual(laddered_lots(cfg(), -500_000), 1)

    def test_it_ratchets_back_down(self):
        self.assertEqual(laddered_lots(cfg(), 100_000), 5)
        self.assertEqual(laddered_lots(cfg(), 30_000), 2)

    def test_the_cap_holds(self):
        self.assertEqual(laddered_lots(cfg(), 10_000_000), 20)
        self.assertEqual(laddered_lots(cfg(compound_max_lots=6), 10_000_000), 6)

    def test_off_by_default(self):
        self.assertEqual(GapCarryConfig().compound_step_pct, 0.0)
        self.assertEqual(laddered_lots(GapCarryConfig(lots=1), 5_000_000), 1)

    def test_a_step_without_capital_is_refused_rather_than_ignored(self):
        """Silently sizing at base would look like the ladder simply never
        fired, which is the failure that is hardest to notice."""
        with self.assertRaises(GapCarryError):
            cfg(compound_base_capital=0).validate()

    def test_a_negative_step_is_refused(self):
        with self.assertRaises(GapCarryError):
            cfg(compound_step_pct=-5).validate()


class BothPathsUseTheOneHelper(unittest.TestCase):
    def test_the_replay_sizes_from_banked_money(self):
        self.assertIn("lots=laddered_lots(config, banked)", REPLAY)
        self.assertIn("banked += float(position.net)", REPLAY)

    def test_the_live_engine_sizes_from_closed_carries_only(self):
        self.assertIn("laddered_lots(self.config", PAPER)
        block = PAPER.split("laddered_lots(self.config")[1][:200]
        self.assertIn("self.history", block, "history holds CLOSED carries")

    def test_the_position_records_the_size_it_actually_took(self):
        """Recording config.lots while trading a laddered size would make the
        book disagree with the broker."""
        self.assertNotIn("lots=int(self.config.lots),\n            signal=signal,", PAPER)

    def test_the_live_engine_says_when_it_changed_size(self):
        self.assertIn("compounding —", PAPER)

    def test_neither_path_sizes_on_an_open_position(self):
        for name, src in (("replay", REPLAY), ("live", PAPER)):
            block = src.split("laddered_lots(")[1][:400]
            self.assertNotIn("unrealised", block, name)
            self.assertNotIn("self.position", block, name)


if __name__ == "__main__":
    unittest.main()


class ItReachesTheEngineFromThePage(unittest.TestCase):
    """Phil, 2026-09-10: "so I get it on UI correct?"

    Not until this. The engine took the settings and the API never sent them,
    so anything set on the page stopped at the request boundary and the book
    traded flat -- the failure that looks like the ladder simply never firing.
    """

    APP = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    HTML = open(os.path.join(ROOT, "strategy.html"), encoding="utf-8").read()
    JS = open(os.path.join(ROOT, "static", "philforge-app.js"), encoding="utf-8").read()
    KEYS = ("compound_step_pct", "compound_base_capital", "compound_max_lots")

    def test_both_payload_models_accept_it(self):
        for model in ("GapCarryPaperStartPayload", "GapCarryBacktestPayload"):
            i = self.APP.find(f"class {model}")
            self.assertGreater(i, 0, model)
            block = self.APP[i : i + 1400]
            for k in self.KEYS:
                self.assertIn(k, block, f"{model}.{k}")

    def test_every_place_that_builds_the_engine_config_passes_it(self):
        built = self.APP.count("_gap_carry_mod.GapCarryConfig(")
        passed = self.APP.count("compound_step_pct=float(")
        self.assertEqual(passed, built, "a config built without the ladder trades flat")

    def test_the_pinned_automation_rule_keeps_it_off(self):
        block = self.APP.split("_GAP_CARRY_AUTO_RULE = {")[1].split("}")[0]
        self.assertIn('"compound_step_pct": 0.0', block)

    def test_the_page_sends_it(self):
        block = self.JS.split("function _gapCarryPayload(")[1][:900]
        for k in self.KEYS:
            self.assertIn(k, block, k)

    def test_the_page_has_controls_for_it(self):
        self.assertIn('id="oc-gap-compound-step"', self.HTML)
        self.assertIn('id="oc-gap-compound-capital"', self.HTML)
        self.assertIn("setGapCarryCompound", self.HTML)

    def test_the_action_is_registered(self):
        """An unregistered action renders and then does nothing when clicked."""
        self.assertIn("'setGapCarryCompound'", self.JS)
        self.assertIn("function setGapCarryCompound(", self.JS)

    def test_the_recipe_says_whether_the_ladder_will_fire(self):
        block = self.JS.split("function _syncGapCarryRecipe()")[1][:1200]
        self.assertIn("+1 lot per +", block)
        self.assertIn("ladder needs a capital", block)


class TheTearsheetSaysIt(unittest.TestCase):
    """The document claimed "There is no position sizing in this rule" and
    "Every figure in this document is one lot". The second is still true; the
    first stopped being true the moment the engine grew a ladder."""

    BUILD = open(os.path.join(ROOT, "tools", "tearsheet", "build_gapcarry_report.py"), encoding="utf-8").read()
    OUT = os.path.join(ROOT, "docs", "assets", "gap-carry-tearsheet.html")

    def test_the_contradicted_claim_is_gone(self):
        self.assertNotIn("There is no position sizing in this rule", self.BUILD)

    def test_the_document_still_says_its_own_figures_are_one_lot(self):
        self.assertIn("Every figure in this document is ONE lot", self.BUILD)

    def test_each_row_was_its_own_engine_run(self):
        """Scaling a one-lot book by the lot count is the invalid method that
        produced two wrong CE/PE figures; the comment must say these are not."""
        block = self.BUILD.split("LADDER = [")[0][-500:]
        self.assertIn("not scaled", block)

    def test_the_rows_match_what_the_engine_returned(self):
        block = self.BUILD.split("LADDER = [")[1].split("]")[0]
        for net in ("277173", "680111", "2299104", "3597754"):
            self.assertIn(net, block, net)

    def test_it_reports_capital_efficiency_not_just_the_headline(self):
        self.assertIn('row["net"] / row["peak"]', self.BUILD)

    def test_the_page_carries_the_section(self):
        if not os.path.exists(self.OUT):
            self.skipTest("tearsheet not built here")
        html = open(self.OUT, encoding="utf-8").read()
        self.assertIn("Sizing up as the book earns", html)
        for net in ("2,77,173", "35,97,754"):
            self.assertIn(net, html, net)
        self.assertIn("too few trades", html, "the noise caveat must travel with the numbers")
