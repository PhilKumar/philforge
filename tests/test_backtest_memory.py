"""A 1-minute backtest must not need several copies of itself in memory.

Phil, 2026-09-21: a Strategy Builder backtest (CE_SL15_NoMonTue, 1-minute
candles, Oct-2024 to Aug-2026, ~172,000 rows) froze philforge.in and the live
books for 35 minutes. The replay itself takes 14 seconds. Its memory peaked at
627 MB -- the indicator step joined whole-table copies to add once-a-day
columns, and the replay loop's iterrows() boxed all fifteen million values at
once -- which pushed the single website process past its MemoryHigh, and the
throttled process stopped answering anything. Same trades, 349 MB now.
"""

import tracemalloc
import unittest

import numpy as np
import pandas as pd

import engine.backtest as backtest
import engine.indicators as indicators

LIVE_CE_INDICATORS = [
    "Current_Candle_5m",
    "Supertrend_10_2.7_3m",
    "EMA_17_5m",
    "CPR_0.2_0.5",
    "Previous_Day",
    "RSI_14_5m",
    "Previous_Candle_10m",
]


def _minutes(days: int = 80) -> pd.DataFrame:
    sessions = pd.bdate_range("2025-01-01", periods=days)
    stamps = [d + pd.Timedelta(hours=9, minutes=15 + m) for d in sessions for m in range(375)]
    close = 23000 + np.cumsum(np.random.default_rng(7).normal(0, 3, len(stamps)))
    return pd.DataFrame(
        {"open": close, "high": close + 2, "low": close - 2, "close": close, "volume": 1000},
        index=pd.DatetimeIndex(stamps),
    )


def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
    return indicators.compute_dynamic_indicators(
        frame,
        LIVE_CE_INDICATORS,
        default_timeframe_minutes=1,
        source_timeframe_minutes=1,
        execution_timeframe_minutes=1,
    )


class TheIndicatorStepStaysLean(unittest.TestCase):
    def test_working_memory_is_not_a_multiple_of_the_result(self):
        frame = _minutes()
        tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            result = _indicators(frame)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        size = result.memory_usage(deep=True).sum()
        # Measured on the live CE book's inputs: 3.9x before, 1.5x after.
        self.assertLess(peak / size, 2.5, f"peak {peak / 1e6:.0f} MB for a {size / 1e6:.0f} MB result")

    def test_the_input_is_never_written_into(self):
        frame = _minutes(days=12)
        before = frame.copy()
        _indicators(frame)
        pd.testing.assert_frame_equal(frame, before)

    def test_once_a_day_columns_are_attached_row_for_row(self):
        result = _indicators(_minutes(days=12))
        for column in ("Yesterday_High", "Daily_SMA_10", "pivot", "S3", "cpr_type"):
            self.assertIn(column, result.columns)
        day = result.index.normalize()
        # Every bar of a session carries that session's one value.
        self.assertTrue((result.groupby(day)["Yesterday_High"].nunique(dropna=False) <= 1).all())

    def test_a_misaligned_attachment_is_refused(self):
        frame = _minutes(days=2)
        with self.assertRaises(ValueError):
            indicators._attach_columns(frame, frame[["close"]].iloc[::-1])


class TheReplayLoopSeesTheSameRows(unittest.TestCase):
    def test_slices_yield_exactly_what_iterrows_yields(self):
        frame = pd.DataFrame(
            {
                "close": np.arange(12, dtype=float),
                "flag": [True, False] * 6,
                "name": list("abcdefghijkl"),
                "n": np.arange(12, dtype="int32"),
            },
            index=pd.date_range("2025-01-01 09:15", periods=12, freq="min"),
        )
        whole = list(frame.iterrows())
        sliced = list(backtest._iterrows_in_chunks(frame, size=5))  # boundaries at 5 and 10
        self.assertEqual([ts for ts, _ in whole], [ts for ts, _ in sliced])
        for (_, a), (_, b) in zip(whole, sliced):
            pd.testing.assert_series_equal(a, b)

    def test_the_replay_does_not_iterrows_the_whole_table(self):
        source = open(backtest.__file__, encoding="utf-8").read()
        self.assertNotIn("in df.iterrows()", source)


if __name__ == "__main__":
    unittest.main()
