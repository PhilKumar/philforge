"""Deploying from the builder must not drop the sizing settings.

The trap: expiry_day_lots and the compounding keys were written straight into
the saved strategy, but the builder had no field for any of them. gatherLegs()
and buildPayload() construct the payload from form inputs alone, so pressing
Deploy would have sent a leg with no expiry_day_lots and no compound_* keys --
silently reverting a live book to its base size with nothing to say so.

Every one of these settings must survive the round trip: load a saved
strategy, press Deploy, and get the same numbers back.
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = open(os.path.join(ROOT, "static", "philforge-app.js"), encoding="utf-8").read()
HTML = open(os.path.join(ROOT, "strategy.html"), encoding="utf-8").read()

LEG_KEY = "expiry_day_lots"
LADDER_KEYS = ("compound_step_pct", "compound_base_capital", "compound_max_lots")
LADDER_FIELDS = ("compound-step-pct", "compound-base-capital", "compound-max-lots")


class TheBuilderHasTheFields(unittest.TestCase):
    def test_a_leg_can_be_given_an_expiry_size(self):
        self.assertIn("leg-${id}-expiry-day-lots", JS)

    def test_the_ladder_has_its_three_inputs(self):
        for field in LADDER_FIELDS:
            self.assertIn(f'id="{field}"', HTML, field)


class DeployCarriesThemOut(unittest.TestCase):
    def test_gather_legs_sends_the_expiry_size(self):
        block = JS.split("function gatherLegs()")[1].split("\n}")[0]
        self.assertIn(LEG_KEY, block, "a deploy would drop it")

    def test_build_payload_sends_the_ladder(self):
        block = JS.split("function buildPayload()")[1].split("\n}")[0]
        for key in LADDER_KEYS:
            self.assertIn(key, block, key)

    def test_the_payload_reads_the_fields_defensively(self):
        """These inputs live on one page; a missing element must not throw."""
        block = JS.split("function buildPayload()")[1].split("\n}")[0]
        for field in LADDER_FIELDS:
            self.assertRegex(
                block,
                re.escape(f"document.getElementById('{field}') || {{}}"),
                f"{field} should tolerate being absent",
            )


class LoadingBringsThemBack(unittest.TestCase):
    def test_a_saved_strategy_repopulates_the_ladder(self):
        self.assertIn("function _fillCompoundFields(", JS)
        # called from every place initial_capital is restored, or the fields
        # stay at zero and the next deploy wipes the ladder
        restores = JS.count("initial-capital').value = ")
        self.assertEqual(
            JS.count("_fillCompoundFields("), restores + 1, "every restore site must also refill the ladder"
        )

    def test_a_leg_repopulates_its_expiry_size(self):
        self.assertEqual(
            JS.count("setVal(`leg-${id}-expiry-day-lots`"),
            JS.count("setVal(`leg-${id}-lots`, leg.lots);"),
            "every place lots is restored must restore the expiry size too",
        )


class TheEnginesReadWhatTheBuilderSends(unittest.TestCase):
    def test_the_names_match_end_to_end(self):
        live = open(os.path.join(ROOT, "engine", "live.py"), encoding="utf-8").read()
        bt = open(os.path.join(ROOT, "engine", "backtest.py"), encoding="utf-8").read()
        for key in LADDER_KEYS + (LEG_KEY,):
            self.assertIn(key, live, f"live engine does not read {key}")
            self.assertIn(key, bt, f"backtest does not read {key}")


if __name__ == "__main__":
    unittest.main()
