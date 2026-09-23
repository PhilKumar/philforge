"""Every strategy's chart control is the same coloured glyph.

Phil, 2026-09-23: "do it for all strategies" -- after asking twice for the
chart button to stop being a blue pill reading "Chart" and to look like a
chart. Five consoles carried their own variant: "&#8599; Chart", "↗ Chart",
"↗ Full chart", "↗ Journal chart", and a bare "Chart".

So this test walks the markup rather than trusting a sweep: any control that
opens a chart must wear `ocp-icon-btn is-chart`, and none may carry a text
label. A sixth console added later fails here instead of being noticed on a
screenshot.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "strategy.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "philforge-app.css").read_text(encoding="utf-8")

# Every strategy console's own chart control, by the id or hook it is known by.
CONSOLE_CHART_BUTTONS = (
    'id="oc-st-chart-btn"',  # Supertrend
    'id="oc-st-backtest-chart-btn"',  # Supertrend, backtest
    'id="oc-candle-chart-btn"',  # Candle Entry
    'id="oc-gap-chart-btn"',  # Gap Carry
    'id="oc-high-chart-btn"',  # High Entry
    'data-fx="chart"',  # Fib Boundary
    'id="oc-fib-backtest-chart-btn"',  # Fib Boundary, backtest
)


def _button(marker: str) -> str:
    i = HTML.index(marker)
    return HTML[HTML.rindex("<button", 0, i) : HTML.index("</button>", i) + 9]


class EveryConsoleUsesTheSameControl(unittest.TestCase):
    def test_each_one_is_the_coloured_glyph(self):
        for marker in CONSOLE_CHART_BUTTONS:
            with self.subTest(marker):
                self.assertIn("ocp-icon-btn is-chart", _button(marker))

    def test_none_of_them_is_the_old_blue_pill(self):
        for marker in CONSOLE_CHART_BUTTONS:
            with self.subTest(marker):
                self.assertNotIn("cascade-options-control", _button(marker))

    def test_none_of_them_carries_a_text_label(self):
        for marker in CONSOLE_CHART_BUTTONS:
            with self.subTest(marker):
                button = _button(marker)
                for label in ("Chart</button>", "Full chart", "Journal chart"):
                    self.assertNotIn(label, button)

    def test_each_still_says_what_it_does_without_a_label(self):
        for marker in CONSOLE_CHART_BUTTONS:
            with self.subTest(marker):
                button = _button(marker)
                self.assertIn("aria-label=", button)
                self.assertIn("title=", button)

    def test_every_one_draws_the_bar_chart(self):
        for marker in CONSOLE_CHART_BUTTONS:
            with self.subTest(marker):
                self.assertIn('<polyline points="3.5 10.5 8 6 12 9.5 20.5 2"/>', _button(marker))


class TheRenderedControlsMatchToo(unittest.TestCase):
    """The ones built in JS, which the markup sweep cannot see."""

    def test_no_rendered_chart_button_is_a_blue_pill(self):
        for line in JS.splitlines():
            if "cascade-options-control" not in line:
                continue
            self.assertNotIn("Chart</button>", line, line.strip()[:120])

    def test_the_scalp_and_desk_charts_use_the_icon(self):
        self.assertIn("scalp-option-chart-btn", JS)
        for line in JS.splitlines():
            if "openScalpOptionChart(" in line and "<button" in line:
                self.assertIn("ocp-icon-btn is-chart", line)
            if "openLiveTradeJournal(" in line and "<button" in line:
                self.assertIn("ocp-icon-btn is-chart", line)

    def test_high_entrys_campaign_remove_matches_the_delete_glyph(self):
        line = next(ln for ln in JS.splitlines() if 'data-pf-action="recoveryDrop"' in ln)
        self.assertIn("ocp-icon-btn is-danger", line)
        self.assertIn("ICO.cross(15)", line)
        self.assertNotIn(">Remove</button>", line)


class TheIconExistsAndIsColoured(unittest.TestCase):
    def test_the_bar_chart_icon_is_in_the_house_set(self):
        self.assertIn("barchart: (s) => ICO._s(", JS)

    def test_chart_is_blue_and_remove_is_red_at_rest(self):
        self.assertIn(".ocp-icon-btn.is-chart { color: #60a5fa; }", CSS)
        self.assertIn(".ocp-icon-btn.is-danger { color: #ef4444; }", CSS)

    def test_a_disabled_chart_falls_back_to_muted(self):
        self.assertRegex(CSS, r"\.ocp-icon-btn:disabled \{ color: var\(--muted\);")


if __name__ == "__main__":
    unittest.main()
