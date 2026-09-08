"""A pivot is built from the session, not from whatever the broker returned.

Phil, 2026-09-08, looking at one live trade's recorded indicators: the CPR
levels his PE book exits on were not the same at 09:15 and at 10:10 of the SAME
session. Solving the pivot formulas backwards from the two records showed the
previous day's low and close identical and only its HIGH different --
23,788.40 at 09:15, 23,786.80 an hour later.

Dhan's history sometimes carries bars stamped outside 09:15-15:29, and it does
not always carry the same ones twice. `cpr()` resampled every bar it was handed
into a daily bar, so one stray print above the session high moved that day's
pivots -- and moved them AGAIN when the next fetch came back without it.

That matters because the rule reads "current_close crosses_above CPR_S3". A
level that moves can cross a price that has not, so the strategy could exit on
the line moving rather than the market. On the archive, a single stray bar 25
points above the high moves S1, S3 and TC by 16.67 points each.

The daily bar behind a pivot is now taken from session hours only.
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.indicators import cpr  # noqa: E402

LEVELS = ("pivot", "bc", "tc", "S1", "S2", "S3", "R1", "R2", "R3")


def _session(day: str, base: float) -> pd.DataFrame:
    """One ordinary NSE session of 1-minute bars."""
    idx = pd.date_range(f"{day} 09:15", f"{day} 15:29", freq="1min")
    step = pd.Series(range(len(idx)), index=idx) * 0.1
    return pd.DataFrame(
        {"open": base + step, "high": base + step + 2, "low": base + step - 2, "close": base + step, "volume": 100},
        index=idx,
    )


def _two_days() -> pd.DataFrame:
    return pd.concat([_session("2026-08-19", 24000.0), _session("2026-08-20", 24050.0)])


def _levels(df: pd.DataFrame) -> dict:
    row = cpr(df).iloc[-1]
    return {k: round(float(row[k]), 4) for k in LEVELS}


class APivotIsBuiltFromTheSession(unittest.TestCase):
    def test_a_bar_after_the_close_cannot_move_the_levels(self):
        clean = _two_days()
        stray = clean.iloc[[-1]].copy()
        stray.index = [pd.Timestamp("2026-08-19 15:45")]
        for col in ("open", "high", "low", "close"):
            stray[col] = float(clean.loc["2026-08-19"]["high"].max()) + 25.0
        dirty = pd.concat([clean, stray]).sort_index()
        self.assertEqual(_levels(clean), _levels(dirty), "an out-of-session bar moved the pivots")

    def test_a_bar_before_the_open_cannot_move_the_levels(self):
        clean = _two_days()
        stray = clean.iloc[[0]].copy()
        stray.index = [pd.Timestamp("2026-08-19 08:30")]
        for col in ("open", "high", "low", "close"):
            stray[col] = float(clean.loc["2026-08-19"]["low"].min()) - 25.0
        dirty = pd.concat([stray, clean]).sort_index()
        self.assertEqual(_levels(clean), _levels(dirty))

    def test_the_levels_do_not_depend_on_how_much_history_precedes_them(self):
        """The same session must price the same however far back the buffer runs."""
        full = pd.concat([_session("2026-08-18", 23950.0), _two_days()])
        self.assertEqual(_levels(full), _levels(_two_days()))

    def test_a_clean_frame_is_completely_unaffected(self):
        """Every backtest archive is already session-only; none of them may move."""
        clean = _two_days()
        levels = _levels(clean)
        self.assertTrue(all(v == v for v in levels.values()), "levels went NaN")
        # yesterday's real session values, computed independently
        prev = clean.loc["2026-08-19"]
        high, low, close = prev["high"].max(), prev["low"].min(), prev["close"].iloc[-1]
        self.assertAlmostEqual(levels["pivot"], (high + low + close) / 3, places=4)
        self.assertAlmostEqual(levels["S1"], 2 * ((high + low + close) / 3) - high, places=4)

    def test_an_instrument_that_trades_other_hours_keeps_its_pivots(self):
        """Filtering must never leave a frame with no daily bar at all."""
        idx = pd.date_range("2026-08-19 18:00", "2026-08-20 23:00", freq="30min")
        odd = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1}, index=idx)
        out = cpr(odd)
        self.assertEqual(len(out), len(odd))


if __name__ == "__main__":
    unittest.main()
