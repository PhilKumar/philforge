"""The option chain is warmed only on a bar that could enter.

2026-09-15: the CE book trades Wednesday to Friday only, yet it scanned 31
strikes on every Tuesday bar. Together with the PE book and the paper books,
that drew a 429 from Dhan at 09:25 and the scan ran on estimated prices. A
warm-up for a bar that cannot enter spends the shared rate budget on nothing,
and a warm-up skipped on a bar that CAN enter delays a real order -- so the
gate may only say no when no market move could make the entry fire.
"""

import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

from engine.live import IST, LiveEngine


def _row(when: str) -> pd.Series:
    return pd.Series({"close": 23500.0}, name=pd.Timestamp(when))


# The live CE book's entry conditions, as saved on prod on 2026-09-15.
CE_BOOK = [
    {"logic": "IF", "left": "current_close", "operator": "is_above", "right": "EMA_17_5m"},
    {"logic": "AND", "left": "RSI_14_5m", "operator": "is_above", "right": "number", "right_number_value": 60},
    {"logic": "AND", "left": "CPR_is_wide", "operator": "is_false", "right": "false"},
    {"logic": "AND", "left": "current_close", "operator": "is_above", "right": "Yesterday_High"},
    {"logic": "AND", "left": "Time_Of_Day", "operator": "is_below", "right": "time", "right_time": "10:00"},
    {"logic": "OR", "left": "Time_Of_Day", "operator": "is_above", "right": "time", "right_time": "13:00"},
    {"logic": "AND", "left": "current_close", "operator": "is_above", "right": "EMA_17_5m"},
    {"logic": "AND", "left": "RSI_14_5m", "operator": "is_above", "right": "number", "right_number_value": 60},
    {"logic": "AND", "left": "CPR_is_wide", "operator": "is_false", "right": "false"},
    {"logic": "AND", "left": "current_close", "operator": "is_above", "right": "Yesterday_High"},
    {
        "logic": "AND",
        "left": "Day_Of_Week",
        "operator": "contains",
        "right": "days",
        "right_days": ["Wednesday", "Thursday", "Friday"],
    },
]

# The live PE book's.
PE_BOOK = [
    {"logic": "IF", "left": "current_close", "operator": "is_below", "right": "EMA_20_5m"},
    {"logic": "AND", "left": "CPR_is_wide", "operator": "is_false", "right": "false"},
    {
        "logic": "AND",
        "left": "Day_Of_Week",
        "operator": "contains",
        "right": "days",
        "right_days": ["Monday", "Tuesday", "Thursday", "Friday"],
    },
    {"logic": "AND", "left": "current_close", "operator": "is_below", "right": "CPR_BC"},
    {"logic": "AND", "left": "Time_Of_Day", "operator": "is_below", "right": "time", "right_time": "11:00"},
]

TUE_0925 = "2026-09-15 09:25:00"
WED_0925 = "2026-09-16 09:25:00"
TUE_1130 = "2026-09-15 11:30:00"


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = LiveEngine(dhan=object(), run_id="warm-gate", state_dir=self._tmp.name)
        self.engine.strategy = {"max_trades_per_day": 1}

    def tearDown(self):
        self._tmp.cleanup()

    def possible(self, conditions, when):
        self.engine.entry_conditions = conditions
        now = datetime.fromisoformat(when).replace(tzinfo=IST)
        return self.engine._entry_possible_this_bar(_row(when), None, now)


class TheCalendarDecides(_Case):
    def test_ce_on_a_tuesday_cannot_enter(self):
        self.assertFalse(self.possible(CE_BOOK, TUE_0925))

    def test_ce_on_a_wednesday_can(self):
        self.assertTrue(self.possible(CE_BOOK, WED_0925))

    def test_pe_on_a_tuesday_morning_can(self):
        self.assertTrue(self.possible(PE_BOOK, TUE_0925))

    def test_pe_after_its_window_cannot(self):
        self.assertFalse(self.possible(PE_BOOK, TUE_1130))

    def test_market_conditions_never_say_no(self):
        """Only the calendar is decided up front; EMA, RSI and CPR could move."""
        market_only = [c for c in PE_BOOK if c["left"] not in ("Day_Of_Week", "Time_Of_Day")]
        self.assertTrue(self.possible(market_only, TUE_1130))


class TheEntrysOwnGates(_Case):
    def test_max_trades_done(self):
        self.engine.trades_today = 1
        self.assertFalse(self.possible(PE_BOOK, TUE_0925))

    def test_in_a_trade(self):
        self.engine.in_trade = True
        self.assertFalse(self.possible(PE_BOOK, TUE_0925))

    def test_an_armed_entry_always_gets_its_chain(self):
        self.engine._pending_order = {"signal_candle_time": datetime(2026, 9, 15, 9, 20)}
        self.assertTrue(self.possible(CE_BOOK, TUE_0925))

    def test_a_doubt_says_yes(self):
        """A broken condition must not cost a real entry its head start."""
        with patch("engine.live.eval_condition", side_effect=RuntimeError("bad condition")):
            self.assertTrue(self.possible(CE_BOOK, TUE_0925))

    def test_no_conditions_no_entry(self):
        self.assertFalse(self.possible([], TUE_0925))


if __name__ == "__main__":
    unittest.main()
