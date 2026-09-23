"""The condition row fits on one line, and the days are all seven.

Phil, 2026-09-23: "for day selection and texts make the panels horrible...
Make it complete and compact". The day picker was a dropdown of six full-width
rows that opened over the Save button and hid its own selection; the condition
row carried typed-in widths (140/110/140) that pushed the delete button onto a
second line inside the builder's middle column and squeezed the leg builder.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "philforge-app.css").read_text(encoding="utf-8")


class TheDaysAreComplete(unittest.TestCase):
    def test_every_day_the_exchange_can_open(self):
        self.assertIn(
            'const DAY_CHOICES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]', JS
        )

    def test_sunday_is_there_because_the_exchange_has_used_it(self):
        """NSE ran a full session on Sunday 1 February 2026 (the Budget)."""
        self.assertIn("Sunday", JS.split("const DAY_CHOICES")[1][:200])


class TheDaysAreCompact(unittest.TestCase):
    def test_the_dropdown_is_gone(self):
        self.assertNotIn("day-picker-dd", JS)
        self.assertNotIn("Can select multiple days", JS.split("function updateDayLabel")[0])

    def test_the_chips_are_rendered_in_the_row(self):
        block = JS.split("lhsVal === 'Day_Of_Week'")[1][:900]
        self.assertIn("day-chips", block)
        self.assertIn("DAY_CHOICES.map", block)
        self.assertIn('class="day-opt"', block)

    def test_a_chip_shows_its_state_without_being_opened(self):
        self.assertIn(".day-chips .day-opt:has(input:checked)", CSS)

    def test_the_old_toggle_still_survives_a_click(self):
        """A row rendered from an older saved strategy must not throw."""
        self.assertIn("if (dd) dd.style.display", JS)
        self.assertIn("if (!label) return;", JS)


class TheRowFitsOnOneLine(unittest.TestCase):
    def test_no_typed_widths_in_the_markup(self):
        row = JS.split("row.className = 'flex-row condition-row'")[1][:1200]
        self.assertNotIn("min-width:140px", row)
        self.assertNotIn("min-width:110px", row)

    def test_the_widths_come_from_the_stylesheet(self):
        for rule in (".condition-row .left-op", ".condition-row .operator", ".condition-row .rhs-wrap"):
            self.assertIn(rule, CSS)

    def test_the_day_row_takes_its_own_line_and_keeps_the_bin_up_top(self):
        self.assertIn(".condition-row:has(.day-chips) .rhs-wrap", CSS)
        self.assertIn(".condition-row:has(.day-chips) > button", CSS)

    def test_the_connector_is_a_pill_not_a_bar(self):
        self.assertIn(".condition-connector select.logic-op { width: auto;", CSS)


class TheBooleanFieldsReadPlainly(unittest.TestCase):
    def test_cpr_width_flags_say_true_false(self):
        self.assertIn("Wide (true/false)", JS)
        self.assertIn("Narrow (true/false)", JS)
        self.assertNotIn("— Is Wide`", JS)


if __name__ == "__main__":
    unittest.main()
