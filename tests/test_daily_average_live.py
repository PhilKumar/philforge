"""The 20-day filter on the live CE (Phil, 2026-09-22).

Three things had to be true for "close is above Daily_SMA_20" to work live:
the form must accept the field, the builder must offer it, and the live engine
must be able to compute it -- it holds two sessions of 1-minute bars, so from its
own candles every daily average was NaN and the book would never have entered.
"""

import os
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import pandas as pd

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")

import engine.live as live  # noqa: E402
from engine.strategy_contract import collect_condition_fields  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")

RULE = {
    "logic": "AND",
    "left": "current_close",
    "operator": "is_above",
    "right": "Daily_SMA_20",
    "right_number_value": "",
}


class TheFormAcceptsIt(unittest.TestCase):
    def test_the_daily_averages_are_always_available(self):
        fields = collect_condition_fields([])
        for period in (10, 20, 50):
            self.assertIn(f"Daily_SMA_{period}", fields)

    def test_the_builder_offers_them_on_both_sides(self):
        self.assertIn('value: "Daily_SMA_20"', JS)
        self.assertEqual(JS.count("html += _dailyAvgOptionsHtml();"), 2)


def _engine(conditions):
    """Configured the way the app does it: configure() keeps the rules on the
    engine itself, not inside `strategy` -- reading them from `strategy` would
    never see the rule, leave every average NaN, and the book would never enter."""
    eng = live.LiveEngine.__new__(live.LiveEngine)
    eng.dhan = mock.Mock()
    eng.log_event = mock.Mock()
    eng.configure({"instrument": "26000", "indicators": []}, conditions, [], {})
    assert "entry_conditions" not in eng.strategy
    return eng


def _daily(closes_by_day):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in closes_by_day])
    return pd.DataFrame({"close": list(closes_by_day.values())}, index=idx)


class TheLiveEngineFillsThem(unittest.TestCase):
    def setUp(self):
        days = pd.bdate_range("2026-07-01", "2026-09-22")
        self.closes = {d.date(): 100.0 + i for i, d in enumerate(days)}
        self.today = date(2026, 9, 22)

    def _run(self, conditions, frame):
        eng = _engine(conditions)
        eng.dhan.get_historical_data.return_value = _daily(self.closes)
        with mock.patch.object(live, "_now_ist", return_value=datetime(2026, 9, 22, 10, 0)):
            return eng, eng._apply_daily_averages(frame)

    def test_only_closes_before_the_session_count(self):
        frame = pd.DataFrame({"close": [1.0, 2.0]}, index=pd.DatetimeIndex(["2026-09-22 09:20", "2026-09-22 09:25"]))
        _, out = self._run([RULE], frame)
        before = [v for d, v in sorted(self.closes.items()) if d < self.today]
        self.assertAlmostEqual(out["Daily_SMA_20"].iloc[-1], sum(before[-20:]) / 20)
        self.assertAlmostEqual(out["Daily_SMA_50"].iloc[-1], sum(before[-50:]) / 50)
        self.assertNotIn(self.closes[self.today], before[-20:])

    def test_yesterdays_rows_read_the_day_before_yesterday(self):
        frame = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex(["2026-09-21 15:00"]))
        _, out = self._run([RULE], frame)
        before = [v for d, v in sorted(self.closes.items()) if d < date(2026, 9, 21)]
        self.assertAlmostEqual(out["Daily_SMA_20"].iloc[0], sum(before[-20:]) / 20)

    def test_fetched_once_a_day(self):
        eng = _engine([RULE])
        eng.dhan.get_historical_data.return_value = _daily(self.closes)
        frame = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex(["2026-09-22 09:20"]))
        with mock.patch.object(live, "_now_ist", return_value=datetime(2026, 9, 22, 10, 0)):
            eng._apply_daily_averages(frame.copy())
            eng._apply_daily_averages(frame.copy())
        self.assertEqual(eng.dhan.get_historical_data.call_count, 1)
        self.assertEqual(eng.dhan.get_historical_data.call_args.kwargs["candle_type"], "D")

    def test_a_book_without_the_rule_never_asks(self):
        frame = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex(["2026-09-22 09:20"]))
        eng, out = self._run([{"left": "current_close", "operator": "is_above", "right": "EMA_20_5m"}], frame)
        eng.dhan.get_historical_data.assert_not_called()
        self.assertNotIn("Daily_SMA_20", out.columns)

    def test_a_failed_fetch_leaves_the_rule_false_and_retries(self):
        eng = _engine([RULE])
        eng.dhan.get_historical_data.side_effect = RuntimeError("DH-905")
        frame = pd.DataFrame(
            {"close": [1.0], "Daily_SMA_20": [float("nan")]}, index=pd.DatetimeIndex(["2026-09-22 09:20"])
        )
        with mock.patch.object(live, "_now_ist", return_value=datetime(2026, 9, 22, 10, 0)):
            out = eng._apply_daily_averages(frame)
            eng._apply_daily_averages(frame)
        self.assertTrue(pd.isna(out["Daily_SMA_20"].iloc[0]))
        self.assertEqual(eng.dhan.get_historical_data.call_count, 2)


class EveryLivePathAppliesThem(unittest.TestCase):
    def test_the_three_places_indicators_are_computed(self):
        src = (ROOT / "engine" / "live.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("self._apply_daily_averages(df"), 3)


if __name__ == "__main__":
    unittest.main()
