"""The ledger's own controls look and behave like the rest of the site.

Phil, 2026-09-23, on the closed-campaign table:
  "THe pop up has to match site specifics... Everytime you make me to tell you
   this... where are the charts?... Del buttons are in blue colour, just give a
   X enough... Why can't you do this uniformly all things on every strategy
   page"

Three separate faults, and the third was the bad one: the frozen-chart ROUTE
learned to draw a High Entry campaign in 154dce97, but `has_chart` only counted
payloads carrying an engine or chart params, and High Entry stores a mother --
so the button was never drawn and the fix was unreachable. Shipped as done.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")
DB = (ROOT / "db.py").read_text(encoding="utf-8")


class TheBrowsersOwnDialogIsNotUsed(unittest.TestCase):
    """It says "philforge.in says" in a system font and belongs to no page."""

    def test_nothing_calls_the_native_confirm(self):
        offenders = [
            line.strip()
            for line in JS.splitlines()
            if ("window.confirm(" in line or (" confirm(" in line and "customConfirm" not in line))
            and not line.strip().startswith("//")
        ]
        self.assertEqual(offenders, [], f"use customConfirm instead: {offenders}")

    def test_the_delete_uses_the_house_dialog_and_names_the_action(self):
        body = JS[JS.index("async function deleteClosedCampaign(") :]
        body = body[: body.index("\n// A finished campaign")]
        self.assertIn("await customConfirm(", body)
        self.assertIn("okText: 'Remove'", body)
        self.assertIn("danger: true", body)

    def test_a_booked_row_still_names_its_money_in_the_dialog(self):
        body = JS[JS.index("async function deleteClosedCampaign(") :]
        self.assertIn("booked net of", body)


class TheRemoveControlIsAGlyphNotAPill(unittest.TestCase):
    def test_it_uses_the_house_remove_style(self):
        self.assertIn('class="ocp-icon-btn is-danger" data-pf-action="deleteClosedCampaign"', JS)

    def test_it_is_not_the_blue_primary_control(self):
        body = JS[JS.index("const delCell =") :]
        body = body[: body.index("return `<tr>")]
        self.assertNotIn("cascade-options-control", body)

    def test_it_carries_no_label_text(self):
        body = JS[JS.index("const delCell =") :]
        body = body[: body.index("return `<tr>")]
        # A drawn cross from the house icon set, not a text glyph.
        self.assertIn("${ICO.cross(15)}</button>", body)
        self.assertNotIn("Del</button>", body)

    def test_it_still_says_what_it_does_to_a_screen_reader(self):
        body = JS[JS.index("const delCell =") :]
        body = body[: body.index("return `<tr>")]
        self.assertIn("aria-label=", body)


class EveryStrategyWithAChartOffersOne(unittest.TestCase):
    def test_high_entry_is_in_the_frozen_chart_list(self):
        self.assertIn("|| strategy === 'candle_recovery') && row.has_chart", JS)

    def test_a_stored_mother_counts_as_drawable(self):
        """The fault that made the route's fix unreachable."""
        self.assertIn("json_extract(payload, '$.mother.timestamp') IS NOT NULL", DB)

    def test_the_older_two_shapes_still_count(self):
        self.assertIn("json_extract(payload, '$.engine') IS NOT NULL", DB)
        self.assertIn("json_extract(payload, '$.chart') IS NOT NULL", DB)


if __name__ == "__main__":
    unittest.main()
