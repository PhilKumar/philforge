"""The "no campaign yet" chart draws instead of failing.

Phil, 2026-09-23, on an empty Book monitor: "Why I am seeing this?" -- the
overlay opened and reported "Something went wrong on our end."

`/api/recovery/paper/chart` answers an idle tab with plain NIFTY, built through
the SAME `_recovery_chart` as a real campaign and handed a stub carrying only a
timestamp. The builder read `row["mother"]["high"]` by key, so it raised
KeyError and returned 500 -- on the one path that exists BECAUSE there is
nothing to draw yet.
"""

import datetime as dt
import unittest
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import app as app_module

IST = ZoneInfo("Asia/Kolkata")
JS = (Path(app_module.__file__).parent / "static" / "philforge-app.js").read_text(encoding="utf-8")
HTML = (Path(app_module.__file__).parent / "strategy.html").read_text(encoding="utf-8")


def _candles(n=12):
    base = dt.datetime(2026, 9, 23, 9, 15, tzinfo=IST)
    return [
        SimpleNamespace(
            timestamp=base + dt.timedelta(minutes=5 * i), open=100 + i, high=105 + i, low=95 + i, close=102 + i
        )
        for i in range(n)
    ]


class TheStubMotherDoesNotRaise(unittest.TestCase):
    def test_the_idle_chart_builds(self):
        """Exactly the payload the route passes when no mother is named."""
        out = app_module._recovery_chart({"mother": {"timestamp": ""}, "trades": []}, _candles(), "5m")
        self.assertEqual(out["timeframe"], "5m")
        self.assertEqual(len(out["candles"]), 12)
        self.assertIsNone(out["mother"]["high"])
        self.assertIsNone(out["mother"]["low"])

    def test_it_survives_no_mother_key_at_all(self):
        out = app_module._recovery_chart({"trades": []}, _candles(), "5m")
        self.assertEqual(len(out["candles"]), 12)

    def test_a_real_mother_still_comes_through(self):
        row = {"mother": {"timestamp": "2026-09-23T09:15:00", "high": 23489.0, "low": 23400.0}, "trades": []}
        out = app_module._recovery_chart(row, _candles(), "5m")
        self.assertAlmostEqual(out["mother"]["high"], 23489.0)
        self.assertAlmostEqual(out["mother"]["low"], 23400.0)

    def test_the_mother_candle_is_still_marked_when_there_is_one(self):
        stamp = _candles()[0].timestamp.replace(tzinfo=None).isoformat()
        row = {"mother": {"timestamp": stamp, "high": 105.0, "low": 95.0}, "trades": []}
        out = app_module._recovery_chart(row, _candles(), "5m")
        self.assertEqual(sum(1 for c in out["candles"] if c["is_mother"]), 1)

    def test_no_candle_is_marked_when_there_is_no_mother(self):
        out = app_module._recovery_chart({"mother": {"timestamp": ""}, "trades": []}, _candles(), "5m")
        self.assertEqual(sum(1 for c in out["candles"] if c["is_mother"]), 0)


class AControlThatCannotWorkSaysSo(unittest.TestCase):
    def test_the_header_chart_button_greys_out_with_no_campaign(self):
        self.assertIn("chartBtn.disabled = !campaigns.length;", JS)

    def test_it_explains_why_rather_than_just_being_dead(self):
        self.assertIn("there is nothing to draw yet", JS)


class TheRowControlsAreOneStyle(unittest.TestCase):
    """Chart and remove sit in the same cell and must read as one kind."""

    def test_no_chart_button_is_a_blue_pill_any_more(self):
        for marker in ("openArchivedFibChart", "openFrozenCampaignChart", "loadRecoveryChart"):
            for line in JS.splitlines():
                if f'data-pf-action="{marker}"' in line:
                    self.assertIn("ocp-icon-btn", line, f"{marker} should be the quiet glyph")

    def test_the_header_button_matches_too(self):
        line = next(ln for ln in HTML.splitlines() if 'id="oc-high-chart-btn"' in ln)
        self.assertIn("ocp-icon-btn", line)
        self.assertNotIn("Chart</button>", line)

    def test_no_row_control_carries_a_text_label(self):
        body = JS[JS.index("const chartCell = (() => {") :]
        body = body[: body.index("return `<tr>")]
        self.assertNotIn("&#8599; Chart", body)
        self.assertNotIn("Del</button>", body)

    def test_a_row_with_nothing_to_draw_shows_a_disabled_button_not_a_dash(self):
        body = JS[JS.index("const chartCell = (() => {") :]
        body = body[: body.index("return `<tr>")]
        self.assertIn('class="ocp-icon-btn" disabled', body)

    def test_the_style_exists_and_dims_when_disabled(self):
        css = (Path(app_module.__file__).parent / "static" / "philforge-app.css").read_text(encoding="utf-8")
        self.assertIn(".ocp-icon-btn:disabled", css)
        self.assertIn(".ocp-icon-btn.is-danger:hover", css)


if __name__ == "__main__":
    unittest.main()
