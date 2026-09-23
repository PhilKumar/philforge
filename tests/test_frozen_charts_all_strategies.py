"""A finished campaign can be drawn, whichever strategy ran it.

Only Candle Entry had a frozen chart; the route answered 501 for everything
else (Phil, 2026-08-26: "Now do the frozen chart for the other 3"). Each
strategy is drawn by the renderer it ALREADY owns rather than a second one:

- Gap Carry rebuilds the night from its snapshot and goes through the same
  payload builder the live chart uses.
- Fib Boundary recomputes geometry from the candle stream, so a past mother
  reproduces exactly -- the archived row carries the four parameters that
  takes, and the client opens the tab's own chart with them.
- High Entry keeps its campaigns in its own book and charts them from there.
"""

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENCRYPTION_KEY", "dGVzdC1rZXktZm9yLXVuaXQtdGVzdHMtMzJieXRlIQ==")

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
DB = (ROOT / "db.py").read_text(encoding="utf-8")
SCRIPT = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")


class GapCarryFrozenChartTests(unittest.TestCase):
    def test_the_frozen_route_rebuilds_the_night_from_its_snapshot(self):
        self.assertIn('if key == "gap_carry":', APP)
        self.assertIn("GapCarryPaper.from_dict(snapshot)", APP)

    def test_it_calls_the_module_that_actually_defines_the_class(self):
        """The bare name passed for months while the route raised AttributeError.

        GapCarryPaper lives in engine.gap_carry_paper; engine.gap_carry neither
        defines nor re-exports it, so `_gap_carry_mod.GapCarryPaper` compiled
        fine, raised at request time, and the surrounding try/except turned
        every archived night's chart into a permanent 422. Asserting the
        *string* was present is what let it through, so assert the owner too.
        """
        self.assertIn("_gap_carry_paper.GapCarryPaper.from_dict(snapshot)", APP)
        self.assertNotIn("_gap_carry_mod.GapCarryPaper", APP)
        import engine.gap_carry as rule_module

        self.assertFalse(
            hasattr(rule_module, "GapCarryPaper"),
            "engine.gap_carry now exports GapCarryPaper; this guard needs rewriting",
        )

    def test_it_uses_the_same_builder_as_the_live_chart(self):
        # One builder, called twice -- a second one would be free to drift.
        self.assertEqual(APP.count("def _gap_carry_chart_payload("), 1)
        calls = len(re.findall(r"(?<!def )_gap_carry_chart_payload\(engine, rows", APP))
        self.assertEqual(calls, 2, "the live route and the frozen one must share it")

    def test_a_night_that_cannot_be_read_back_says_so(self):
        self.assertIn("Stored night could not be read back", APP)


class FibFrozenChartTests(unittest.TestCase):
    def test_the_archived_row_carries_what_a_redraw_needs(self):
        block = APP[APP.index("def _fib_boundary_campaign_row") :][:4000]
        for field in ("symbol", "side", "timeframe", "buy_mode", "mother_timestamp"):
            with self.subTest(field=field):
                self.assertRegex(block, rf'"chart": \{{[\s\S]{{0,600}}"{field}"')

    def test_the_listing_ships_those_parameters(self):
        self.assertIn("chart_params", DB)
        self.assertIn("$.chart", DB)

    def test_a_row_with_chart_parameters_counts_as_drawable(self):
        self.assertIn("json_extract(payload, '$.chart') IS NOT NULL", DB)

    def test_the_client_opens_the_tab_s_own_chart(self):
        self.assertIsNotNone(re.search(r"async function openArchivedFibChart\(", SCRIPT))
        opener = SCRIPT[SCRIPT.index("async function openArchivedFibChart(") :][:900]
        # It hands the archived mother to the existing chart, not a new one.
        self.assertIn("_fibxChartCtx = {", opener)
        self.assertIn("await loadFibBoundaryChart()", opener)
        self.assertIn("'openArchivedFibChart'", SCRIPT, "not in the pf-action allowlist")


class LedgerChartRoutingTests(unittest.TestCase):
    def test_each_strategy_routes_to_its_own_renderer(self):
        cell = SCRIPT[SCRIPT.index("const chartCell = (() => {") :][:1800]
        self.assertIn("strategy === 'fib_boundary'", cell)
        self.assertIn("openArchivedFibChart", cell)
        self.assertIn("strategy === 'candle_entry' || strategy === 'gap_carry'", cell)
        self.assertIn("openFrozenCampaignChart", cell)

    def test_a_rebuilt_row_still_refuses_a_chart(self):
        # It kept no state; drawing it would be a guess dressed as a record.
        # Since 2026-09-23 it says so with a DISABLED button rather than a
        # dash -- a dash where a control belongs reads as a missing feature --
        # but it still offers nothing to press.
        cell = SCRIPT[SCRIPT.index("const chartCell = (() => {") :][:2200]
        self.assertIn('class="ocp-icon-btn is-chart" disabled', cell)
        self.assertIn("its engine state was overwritten", cell)


if __name__ == "__main__":
    unittest.main()


class GapCarryRoundTripTests(unittest.TestCase):
    """The snapshot really does come back as something drawable.

    The checks above read the source; this one runs it -- a night is traded,
    snapshotted, rebuilt from that snapshot alone, and drawn.
    """

    def _closed_night(self):
        from dataclasses import dataclass
        from datetime import date, datetime, time, timedelta

        from engine.gap_carry import GapCarryConfig
        from engine.gap_carry_paper import GapCarryPaper

        @dataclass(frozen=True)
        class Candle:
            timestamp: datetime
            open: float
            high: float
            low: float
            close: float

        def day(on, closes, start=time(9, 15), step=5):
            base = datetime.combine(on, start)
            return [Candle(base + timedelta(minutes=step * i), c, c, c, c) for i, c in enumerate(closes)]

        engine = GapCarryPaper(
            config=GapCarryConfig(timeframe="5m", lots=1),
            option_premium_lookup=lambda *_a: 300.0,
            expiry_lookup=lambda _s: date(2026, 3, 17),
            lot_size_lookup=lambda _e: 75,
        )
        engine.ingest({"5m": day(date(2026, 3, 10), [24000.0 + i for i in range(80)])})
        return engine, day(date(2026, 3, 10), [24000.0 + i for i in range(80)])

    def test_a_night_survives_the_round_trip_and_draws(self):
        import app as app_module
        from engine.gap_carry_paper import GapCarryPaper

        engine, rows = self._closed_night()
        self.assertIsNotNone(engine.position, "fixture must hold a position to draw")
        snapshot = engine.to_dict()

        rebuilt = GapCarryPaper.from_dict(snapshot)
        payload = app_module._gap_carry_chart_payload(rebuilt, rows, "5m")

        self.assertEqual(payload["status"], "ok")
        chart = payload["chart"]
        self.assertEqual(len(chart["candles"]), len(rows))
        # The strike line and the entry marker are what make it a RECORD of a
        # trade rather than a plain price chart.
        self.assertTrue(chart["lines"], "the strike belongs on the chart")
        self.assertTrue(chart["entries"], "the entry belongs on the chart")
        self.assertIn("indicators", chart)

    def test_the_rebuilt_night_draws_the_same_picture_as_the_live_one(self):
        import app as app_module
        from engine.gap_carry_paper import GapCarryPaper

        engine, rows = self._closed_night()
        live = app_module._gap_carry_chart_payload(engine, rows, "5m")
        frozen = app_module._gap_carry_chart_payload(GapCarryPaper.from_dict(engine.to_dict()), rows, "5m")
        self.assertEqual(live["chart"]["lines"], frozen["chart"]["lines"])
        self.assertEqual(live["chart"]["entries"], frozen["chart"]["entries"])
        self.assertEqual(live["chart"]["exits"], frozen["chart"]["exits"])
