"""01-Oct-2026, ~15:20: the live CE book's 1-minute window began at Friday
25-Sep 15:22, so its first gap was the weekend. `_is_intraday` read only the
first two bars, took the frame for DAILY bars, and "yesterday" became the
previous candle: Yesterday_High 22433 instead of 22809, R5 22518 instead of
23129. The same frame must give the same yesterday wherever the window starts."""

import unittest

import numpy as np
import pandas as pd

from engine.indicators import _is_intraday, compute_dynamic_indicators


def _session(day, base, high_bump):
    idx = pd.date_range(f"{day} 09:15", f"{day} 15:29", freq="1min")
    close = base + np.linspace(0, 20, len(idx))
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 0}, index=idx)
    df.iloc[100, df.columns.get_loc("high")] = base + high_bump
    return df


class AWeekendAtTheWindowEdgeKeepsYesterday(unittest.TestCase):
    def setUp(self):
        self.full = pd.concat(
            [
                _session("2026-09-25", 23100, 60),  # Friday
                _session("2026-09-28", 22800, 280),  # Monday
                _session("2026-09-29", 22600, 150),  # Tuesday
                _session("2026-09-30", 22650, 160),  # Wednesday: high 22810
                _session("2026-10-01", 22300, 50).loc[:"2026-10-01 15:20"],  # Thursday, live
            ]
        )

    def _yesterday_high(self, frame):
        out = compute_dynamic_indicators(
            frame,
            ["CPR_0.2_0.5", "Previous_Day", "EMA_17_5m"],
            default_timeframe_minutes=5,
            source_timeframe_minutes=1,
            execution_timeframe_minutes=5,
        )
        return out.loc["2026-10-01 15:10", ["Yesterday_High", "R5"]]

    def test_a_window_starting_late_on_friday_is_still_intraday(self):
        frame = self.full.loc["2026-09-25 15:28":]  # 2 Friday bars, then the weekend
        self.assertGreater((frame.index[1] - frame.index[0]).total_seconds(), 0)
        self.assertTrue(_is_intraday(frame.resample("5min").last().dropna()))

    def test_yesterday_does_not_depend_on_where_the_window_starts(self):
        expected = self._yesterday_high(self.full)
        self.assertAlmostEqual(float(expected["Yesterday_High"]), 22810.0, places=2)
        for start in ("2026-09-25 15:22", "2026-09-25 15:26", "2026-09-25 15:28", "2026-09-28 09:15"):
            got = self._yesterday_high(self.full.loc[start:])
            self.assertAlmostEqual(float(got["Yesterday_High"]), float(expected["Yesterday_High"]), places=2, msg=start)
            self.assertAlmostEqual(float(got["R5"]), float(expected["R5"]), places=2, msg=start)

    def test_daily_bars_are_still_daily(self):
        daily = pd.DataFrame(
            {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
            index=pd.bdate_range("2026-09-01", periods=20),
        )
        self.assertFalse(_is_intraday(daily))


if __name__ == "__main__":
    unittest.main()
