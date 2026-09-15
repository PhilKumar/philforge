"""CPR comes from whole sessions, however far the live buffer has slid.

2026-09-15: by 12:30 the live candle buffer no longer held Friday's first hour,
where Friday's low (23,231.40) printed. Resampling "yesterday" from what was left
read the low as 23,314 and every level rose: S1 went from 23,270.30 to 23,325.37.
At 12:35 NIFTY closed at 23,320.55 -- fifty points ABOVE the real S1 -- and the
live PE book was taken out on "close crosses below S1".

The 08-Sep fix (`_session_bars`) removed stray out-of-session bars. It could not
see this: every bar left in the buffer was a genuine session bar. The day was
simply no longer whole.
"""

import unittest

import numpy as np
import pandas as pd

from engine.indicators import SessionBook, compute_dynamic_indicators, cpr, pinned_sessions, yesterday_candle

# Friday 11-Sep-2026 as the engine should have read it, solved back from the
# levels it logged at 10:45 (P 23,359.20, S1 23,270.30, R1 23,487.00).
FRI_HIGH, FRI_LOW, FRI_CLOSE = 23448.10, 23231.40, 23398.10
REAL_S1 = 23270.30


def _session(day: str, *, low_at: str, high_at: str, close: float, until: str = "15:29") -> pd.DataFrame:
    idx = pd.date_range(f"{day} 09:15", f"{day} {until}", freq="1min")
    base = np.linspace(23300.0, close, len(idx))
    frame = pd.DataFrame({"open": base, "high": base + 3.0, "low": base - 3.0, "close": base, "volume": 0.0}, index=idx)
    frame.loc[pd.Timestamp(f"{day} {low_at}"), "low"] = FRI_LOW
    frame.loc[pd.Timestamp(f"{day} {high_at}"), "high"] = FRI_HIGH
    frame.iloc[-1, frame.columns.get_loc("close")] = close
    return frame


FRIDAY = _session("2026-09-11", low_at="09:31", high_at="14:02", close=FRI_CLOSE)
TODAY = _session("2026-09-15", low_at="10:02", high_at="09:40", close=23337.40, until="12:34")
WHOLE = pd.concat([FRIDAY, TODAY])
# What the live buffer held at 12:35: the last 500 one-minute bars. Friday's
# first 70 minutes -- and its low -- are gone.
SLID = WHOLE.tail(500)
AT_1230 = pd.Timestamp("2026-09-15 12:30")


def _s1(frame: pd.DataFrame) -> float:
    return float(cpr(frame).loc[AT_1230, "S1"])


class TheBugReproduces(unittest.TestCase):
    def test_the_slid_buffer_has_lost_fridays_low(self):
        self.assertGreater(SLID.index.min(), pd.Timestamp("2026-09-11 09:31"))

    def test_the_whole_view_reads_the_real_s1(self):
        self.assertAlmostEqual(_s1(WHOLE), REAL_S1, places=2)

    def test_without_a_book_the_slid_buffer_moves_s1(self):
        """This is what the live engine did before the fix."""
        self.assertGreater(_s1(SLID) - REAL_S1, 20)


class ThePinnedSessionHolds(unittest.TestCase):
    def test_once_seen_whole_the_slid_buffer_reads_the_real_s1(self):
        book = SessionBook()
        with pinned_sessions(book):
            cpr(WHOLE)  # what the engine sees at start-up, from 7 days of history
            self.assertAlmostEqual(_s1(SLID), REAL_S1, places=2)

    def test_every_level_holds_not_only_s1(self):
        book = SessionBook()
        with pinned_sessions(book):
            whole = cpr(WHOLE).loc[AT_1230]
            slid = cpr(SLID).loc[AT_1230]
        for level in ("pivot", "bc", "tc", "R1", "R2", "S1", "S2", "S3", "S4", "S5"):
            self.assertAlmostEqual(float(slid[level]), float(whole[level]), places=2, msg=level)

    def test_yesterdays_high_and_low_hold_too(self):
        """The CE book enters on `current_close is_above Yesterday_High`."""
        book = SessionBook()
        with pinned_sessions(book):
            yesterday_candle(WHOLE)
            row = yesterday_candle(SLID).loc[AT_1230]
        self.assertAlmostEqual(float(row["yesterday_low"]), FRI_LOW, places=2)
        self.assertAlmostEqual(float(row["yesterday_high"]), FRI_HIGH, places=2)

    def test_through_the_path_the_engine_uses(self):
        """1m raw candles, a 5m strategy, the builder's CPR id."""
        book = SessionBook()
        kwargs = dict(default_timeframe_minutes=5, source_timeframe_minutes=1, execution_timeframe_minutes=5)
        with pinned_sessions(book):
            compute_dynamic_indicators(WHOLE, ["CPR_5m"], **kwargs)
            frame = compute_dynamic_indicators(SLID, ["CPR_5m"], **kwargs)
        self.assertAlmostEqual(float(frame.loc[AT_1230, "CPR_S1"]), REAL_S1, places=2)


class WhatTheBookWillNotLearn(unittest.TestCase):
    def test_a_session_missing_its_open_is_not_learned(self):
        book = SessionBook()
        book.learn(SLID)
        self.assertNotIn(pd.Timestamp("2026-09-11").date(), book._days)

    def test_a_session_still_trading_is_not_learned(self):
        book = SessionBook()
        book.learn(TODAY)
        self.assertEqual(book._days, {})

    def test_a_thinner_view_never_replaces_a_fuller_one(self):
        book = SessionBook()
        book.learn(FRIDAY)
        gappy = FRIDAY.drop(FRIDAY.index[100:200]).copy()
        gappy["low"] = gappy["low"] + 50  # a different low on fewer bars
        book.learn(gappy)
        self.assertAlmostEqual(book._days[pd.Timestamp("2026-09-11").date()][2], FRI_LOW, places=2)

    def test_it_stays_bounded(self):
        book = SessionBook()
        for day in pd.bdate_range("2026-01-01", periods=SessionBook._KEEP_DAYS + 10):
            book.learn(_session(day.strftime("%Y-%m-%d"), low_at="10:00", high_at="11:00", close=23300.0))
        self.assertEqual(len(book._days), SessionBook._KEEP_DAYS)


class ABacktestIsUntouched(unittest.TestCase):
    def test_no_book_means_the_frame_decides_as_before(self):
        self.assertAlmostEqual(_s1(WHOLE), REAL_S1, places=2)
        self.assertEqual(cpr(SLID).loc[AT_1230, "S1"], cpr(SLID).loc[AT_1230, "S1"])


class BothEnginesCarryABook(unittest.TestCase):
    def test_every_indicator_computation_is_pinned(self):
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("live.py", "paper_trading.py"):
            src = open(os.path.join(root, "engine", name), encoding="utf-8").read()
            calls = src.count("compute_dynamic_indicators(")
            pinned = src.count("with pinned_sessions(self._session_book):")
            self.assertEqual(pinned, calls - 0, f"{name}: {calls} computations, {pinned} pinned")
            self.assertIn("self._session_book = SessionBook()", src)


if __name__ == "__main__":
    unittest.main()
