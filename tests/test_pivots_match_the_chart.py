"""The floor pivots are the ones on Phil's chart, level for level.

2026-09-15: S1-S3 and R1-R3 matched TradingView to the paisa, but S4 and S5 did
not. The engine stepped each level past the third by the whole of yesterday's
range (H - L); the chart steps by (H - P) down and (P - L) up. S4 read 22,836.90
against the chart's 22,964.70, and the live PE book exits on S4 and S5.

Re-run on the 5.7-year Dhan archive with PE_NoTarget's saved config (#50):
573 trades, Rs 1,67,457.72 before; 575 trades, Rs 1,72,331.24 after. 29 trades
changed, 2026 moved by Rs -1,130.
"""

import unittest

import pandas as pd

from engine.indicators import cpr

# Friday 11-Sep-2026, and today's levels as the chart printed them.
HIGH, LOW, CLOSE = 23448.10, 23231.40, 23398.10
CHART = {
    "pivot": 23359.20,
    "tc": 23378.65,
    "bc": 23339.75,
    "R0.5": 23423.10,
    "R1": 23487.00,
    "R1.5": 23531.45,
    "R2": 23575.90,
    "S0.5": 23314.75,
    "S1": 23270.30,
    "S1.5": 23206.40,
    "S2": 23142.50,
    "S2.5": 23098.05,
    "S3": 23053.60,
    "S3.5": 23009.15,
    "S4": 22964.70,
}


def _levels() -> pd.Series:
    friday = pd.date_range("2026-09-11 09:15", "2026-09-11 15:29", freq="1min")
    today = pd.date_range("2026-09-15 09:15", "2026-09-15 10:00", freq="1min")
    frame = pd.DataFrame(
        {"open": 23300.0, "high": 23300.0, "low": 23300.0, "close": 23300.0}, index=friday.append(today)
    )
    frame.loc[pd.Timestamp("2026-09-11 14:02"), "high"] = HIGH
    frame.loc[pd.Timestamp("2026-09-11 09:31"), "low"] = LOW
    frame.loc[pd.Timestamp("2026-09-11 15:29"), "close"] = CLOSE
    return cpr(frame).loc[pd.Timestamp("2026-09-15 09:30")]


class EveryLevelOnTheChart(unittest.TestCase):
    def test_each_matches_to_the_paisa(self):
        levels = _levels()
        for name, value in CHART.items():
            self.assertAlmostEqual(float(levels[name]), value, places=2, msg=name)

    def test_the_levels_past_the_third_use_the_traditional_steps(self):
        levels = _levels()
        pivot = float(levels["pivot"])
        self.assertAlmostEqual(float(levels["S4"]), 3 * pivot - (3 * HIGH - LOW), places=2)
        self.assertAlmostEqual(float(levels["S5"]), 4 * pivot - (4 * HIGH - LOW), places=2)
        self.assertAlmostEqual(float(levels["R4"]), 3 * pivot + (HIGH - 3 * LOW), places=2)
        self.assertAlmostEqual(float(levels["R5"]), 4 * pivot + (HIGH - 4 * LOW), places=2)

    def test_the_old_whole_range_step_is_gone(self):
        self.assertNotAlmostEqual(float(_levels()["S4"]), 22836.90, places=0)


if __name__ == "__main__":
    unittest.main()
