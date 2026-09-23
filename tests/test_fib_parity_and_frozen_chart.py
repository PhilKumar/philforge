"""Fib Boundary looks like its siblings, says its state, and a closed trade charts.

Phil, 2026-08-26: "Add the frozen chart on it as well / Why Fib boundary is
different from other strategy panels... Make it same like others and also, THe
live status of the run is not displaying".

Three separate holes:

  * Fib Boundary had NO status badge. Every other strategy carries one, so the
    run's state was the single thing that tab would not tell you.
  * Its panel was a four-child grid, so the monitor flowed UNDERNEATH the form
    instead of beside it, and the column never collapsed on a narrow screen.
  * A closed campaign could not be drawn at all. The chart is built from the
    engine, and the engine was gone the moment the next mother started.
"""

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "strategy.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")
APP_PY = (ROOT / "app.py").read_text(encoding="utf-8")
DB_PY = (ROOT / "db.py").read_text(encoding="utf-8")

STRATEGY_TABS = ("gapcarry", "fib", "recovery", "candle")


def _tab_block(name: str) -> str:
    starts = sorted((HTML.find(f'<div id="oc-tab-{t}"'), t) for t in STRATEGY_TABS + ("bench",))
    starts = [(p, t) for p, t in starts if p >= 0]
    for i, (p, t) in enumerate(starts):
        if t == name:
            end = starts[i + 1][0] if i + 1 < len(starts) else len(HTML)
            return HTML[p:end]
    raise AssertionError(f"no tab {name}")


class LiveStatusBadgeTests(unittest.TestCase):
    def test_fib_boundary_finally_has_a_badge(self):
        self.assertIn('id="oc-fib-badge"', HTML)

    def test_every_strategy_carries_one(self):
        for tab, badge in (
            ("fib", "oc-fib-badge"),
            ("candle", "oc-candle-badge"),
            ("gapcarry", "oc-gap-badge"),
            ("recovery", "oc-high-badge"),
        ):
            self.assertIn(f'id="{badge}"', _tab_block(tab), tab)

    def test_the_badge_reports_the_loudest_of_several_ladders(self):
        """Fib Boundary is the only tab that can run more than one at a time."""
        body = APP_JS.split("const badge = document.getElementById('oc-fib-badge');")[1]
        body = body.split("\n  }")[0]
        self.assertIn("campaigns.filter(row => row && row.running)", body)
        self.assertIn("ladders", body)
        self.assertIn("AUTO · WATCHING", body)
        self.assertIn("ENDED", body)


class PanelParityTests(unittest.TestCase):
    def test_every_strategy_panel_uses_the_two_pane_layout(self):
        for tab in STRATEGY_TABS:
            block = _tab_block(tab)
            self.assertIn('class="ocp-panes"', block, tab)
            self.assertIn("ocp-pane ocp-pane-form", block, tab)
            self.assertIn("ocp-pane ocp-pane-live", block, tab)

    def test_fib_no_longer_uses_the_old_grid(self):
        block = _tab_block("fib")
        self.assertNotIn("cascade-options-workspace", block)
        self.assertNotIn("cascade-options-setup-card", block)

    def test_the_two_panes_are_the_only_pane_children(self):
        """A third child would flow into the grid and land under the form again."""
        block = _tab_block("fib")
        i = block.index('class="ocp-panes"')
        panes = re.findall(r'<div class="(ocp-pane [a-z-]+)"', block[i:])
        self.assertEqual(panes[:2], ["ocp-pane ocp-pane-form", "ocp-pane ocp-pane-live"])


class FrozenChartTests(unittest.TestCase):
    def test_a_closed_campaign_keeps_the_engine_it_ended_as(self):
        # `engine_snapshot` alone is not proof -- the name is used elsewhere in
        # app.py for something unrelated, so this pins the ledger's own line.
        self.assertIn('"engine": dict(engine_snapshot) if engine_snapshot else None', APP_PY)
        self.assertIn("engine_snapshot: Mapping[str, Any] | None = None", APP_PY)

    def test_the_chart_route_exists_and_is_frozen(self):
        self.assertIn('@app.get("/api/paper-campaigns/{strategy}/{campaign_id}/chart")', APP_PY)
        body = APP_PY.split("async def paper_campaign_chart(")[1].split("\n@app.")[0]
        self.assertIn('"frozen": True', body)
        # It reuses the one renderer rather than drawing a second way.
        self.assertIn("_candle_entry_charts(engine", body)

    def test_a_rebuilt_campaign_is_refused_rather_than_guessed(self):
        body = APP_PY.split("async def paper_campaign_chart(")[1].split("\n@app.")[0]
        self.assertIn("status_code=409", body)
        self.assertIn("no ladder to draw", body)

    def test_the_listing_says_which_rows_can_be_drawn(self):
        self.assertIn("has_chart", DB_PY)
        self.assertIn("json_extract(payload, '$.engine')", DB_PY)

    def test_the_button_appears_only_where_there_is_something_to_draw(self):
        body = APP_JS.split("function _renderPaperLedger(strategy)")[1].split("\n}\n")[0]
        self.assertIn("row.has_chart", body)
        self.assertIn("openFrozenCampaignChart", body)
        # The class has to be one that exists, or the button renders as bare text.
        # The row controls became one quiet glyph on 2026-09-23; what matters
        # here is that a button is offered, not which skin it wears.
        self.assertIn('class="ocp-icon-btn is-chart"', body)

    def test_the_action_is_wired_through_the_allowlist(self):
        self.assertIn("'openFrozenCampaignChart',", APP_JS)
        self.assertIn("window.openFrozenCampaignChart = openFrozenCampaignChart;", APP_JS)


if __name__ == "__main__":
    unittest.main()
