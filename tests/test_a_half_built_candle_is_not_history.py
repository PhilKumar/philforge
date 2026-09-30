"""30-Sep-2026, 09:50: "indicator divergence ... current_close engine 22702.1 vs
history 22711.55". The reference had been fetched at 09:47, mid-candle, so it
held the 09:45 candle half-built; the engine held it finished. Two books, two
fetch moments, two different "broker" closes (22711.55 and 22711.2) for one bar.
A candle that had not closed when the reference was fetched is not history."""

import unittest
from datetime import datetime

import pandas as pd

from engine import live
from tests.test_the_engine_decides_on_the_chart_it_shows import _Engine


def _frame(closes, start="2026-09-30 09:15"):
    idx = pd.date_range(start, periods=len(closes), freq="5min")
    return pd.DataFrame({"close": closes, "current_close": closes}, index=idx)


class AHalfBuiltCandleIsNotHistory(unittest.TestCase):
    def setUp(self):
        live._DIVERGENCE_TOLD.clear()
        finished = [22750.0, 22740.0, 22730.0, 22720.0, 22715.0, 22712.0, 22702.1]  # 09:15 .. 09:45
        self.engine_frame = _frame(finished)
        # Fetched at 09:47:30: identical history, but the 09:45 candle stops at its 09:47 price.
        self.reference = _frame(finished[:-1] + [22711.55])
        self.fetched_at = datetime(2026, 9, 30, 9, 47, 30)

    def _audit(self, engine):
        return engine._audit_indicators_against_history(self.engine_frame, [], 5, 5)

    def test_the_fixture_reproduces_the_false_alarm_without_the_cutoff(self):
        e = _Engine(conditions=[{"left": "current_close", "operator": "is_below", "right": "S1"}])
        e.set_indicator_reference(self.reference)  # no timeframe: the old behaviour
        self._audit(e)
        self.assertIsNotNone(e.indicator_divergence)

    def test_the_half_built_candle_is_dropped_and_nothing_fires(self):
        e = _Engine(conditions=[{"left": "current_close", "operator": "is_below", "right": "S1"}])
        e.set_indicator_reference(self.reference, timeframe_minutes=5, fetched_at=self.fetched_at)
        self.assertEqual(e._indicator_reference.index[-1], pd.Timestamp("2026-09-30 09:40"))
        out = self._audit(e)
        self.assertIsNone(e.indicator_divergence)
        self.assertIs(out, self.engine_frame)

    def test_a_candle_that_closed_before_the_fetch_is_kept(self):
        e = _Engine()
        e.set_indicator_reference(self.reference, timeframe_minutes=5, fetched_at=datetime(2026, 9, 30, 9, 50, 30))
        self.assertEqual(e._indicator_reference.index[-1], pd.Timestamp("2026-09-30 09:45"))

    def test_a_real_divergence_on_a_closed_candle_still_fires(self):
        e = _Engine(conditions=[{"left": "current_close", "operator": "is_below", "right": "S1"}])
        wrong = self.engine_frame.copy()
        wrong.loc[wrong.index[-2], ["close", "current_close"]] = 22690.0  # 09:40 off by 22 points
        e.set_indicator_reference(self.reference, timeframe_minutes=5, fetched_at=self.fetched_at)
        e._audit_indicators_against_history(wrong, [], 5, 5)
        self.assertIsNotNone(e.indicator_divergence)


if __name__ == "__main__":
    unittest.main()
