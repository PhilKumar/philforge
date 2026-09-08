"""Two features the tuning study asked for, and the trap each one has.

A DAILY AVERAGE the rules can read. Both books are intraday and neither knows
what kind of market it is in; 2022 is the one year they both lost. The trap is
look-ahead: an average that includes today's close would be reading the candle
it is deciding on, and would backtest beautifully and fail live.

SIZING BY EXPIRY DAY. On the PE book the expiry session carries the result and
the other four days are a net drag. The trap is the calendar: NIFTY's weekly
expiry was Thursday until August 2025 and Tuesday afterwards, so a rule written
against a weekday silently points at the wrong day from September 2025. Sizing
is decided by comparing the trade's date with its own contract's expiry, which
needs no calendar and follows any future change by itself.
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.indicators import _CONDITION_CONTEXT_FIELDS, compute_dynamic_indicators  # noqa: E402


def _sessions(days: int, start_close: float = 100.0) -> pd.DataFrame:
    """`days` NSE sessions of 5-minute bars, each closing 1 point higher."""
    frames = []
    day = pd.Timestamp("2026-01-05")  # a Monday
    made = 0
    while made < days:
        if day.weekday() < 5:
            idx = pd.date_range(f"{day.date()} 09:15", f"{day.date()} 15:25", freq="5min")
            close = start_close + made
            frames.append(
                pd.DataFrame(
                    {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 10}, index=idx
                )
            )
            made += 1
        day += pd.Timedelta(days=1)
    return pd.concat(frames)


class TheDailyAverageIsAvailableAndHonest(unittest.TestCase):
    def setUp(self):
        self.df = compute_dynamic_indicators(_sessions(30), ["Current_Candle_5m"], default_timeframe_minutes=5)

    def test_the_rule_builder_accepts_the_field(self):
        for period in (10, 20, 50):
            self.assertIn(f"Daily_SMA_{period}", _CONDITION_CONTEXT_FIELDS)

    def test_it_is_computed(self):
        self.assertIn("Daily_SMA_10", self.df.columns)
        self.assertTrue(self.df["Daily_SMA_10"].notna().any(), "never resolved a value")

    def test_it_never_reads_today(self):
        """Closes rise 1/day, so the 10-day average as of day N must be the mean
        of days N-10..N-1 — never touching day N."""
        last = self.df.index[-1].normalize()
        today_rows = self.df[self.df.index >= last]
        value = float(today_rows["Daily_SMA_10"].iloc[0])
        today_close = float(today_rows["close"].iloc[0])
        prior = [today_close - k for k in range(1, 11)]
        self.assertAlmostEqual(value, sum(prior) / 10, places=6)
        self.assertLess(value, today_close, "the average includes today — that is look-ahead")

    def test_it_holds_still_for_the_whole_session(self):
        last = self.df.index[-1].normalize()
        today = self.df[self.df.index >= last]["Daily_SMA_20"].dropna()
        self.assertEqual(today.nunique(), 1, "the daily average moved during the session")

    def test_it_is_absent_rather_than_wrong_before_enough_history(self):
        short = compute_dynamic_indicators(_sessions(6), ["Current_Candle_5m"], default_timeframe_minutes=5)
        self.assertTrue(short["Daily_SMA_20"].isna().all(), "20-day average invented from 6 days")


class SizingFollowsTheContractNotTheCalendar(unittest.TestCase):
    def test_the_engine_reads_expiry_day_lots(self):
        source = open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine", "backtest.py")
        ).read()
        self.assertIn('leg.get("expiry_day_lots"', source)
        # the day is decided by the contract's own expiry, never by a weekday
        block = source[source.index("expiry_day_lots = int(") :][:400]
        self.assertIn("contract_expiry == trade_date", block)
        self.assertNotIn("weekday", block)


if __name__ == "__main__":
    unittest.main()
