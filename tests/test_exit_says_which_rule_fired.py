"""An exit has to name the rule that fired, on the bar that fired it.

Phil, 2026-09-08: "I want to know why the exit signal triggers and on what
condition.. in the logs I am not able to get that."

He could not, and neither could the engine. The live trade that closed at
10:15:02 that morning stored an `exit_why` whose SEVEN conditions were all
false -- an EXIT_SIGNAL that, by its own record, had no reason. The record was
not taken at the decision: `_record_closed_trade` rebuilt it afterwards from
`candle_buffer.iloc[-1]` and whatever `_prev_row` had become by then, without
the Signal Candle values the decision itself used. A cross needs the pair of
bars that crossed; replay it against the wrong pair and it reads false.

The reasons are now captured where the decision is made, and the event log
names the rule instead of only the family it belongs to.
"""

import base64
import os
import sys
import tempfile
import unittest

os.environ.setdefault("PHILFORGE_PIN", "123456")
os.environ.setdefault("PHILFORGE_DB", "/tmp/philforge-exit-why-test.db")
os.environ.setdefault("PHILFORGE_USER_DATA_ROOT", "/tmp/philforge-exit-why-data")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from engine.live import LiveEngine  # noqa: E402

# The rule that actually closed NIFTY 23950PE that morning, and the two 5m
# closes that crossed it: 23,681.65 -> 23,707.90 through S3 at ~23,700.
EXIT_RULE = [{"logic": "IF", "left": "current_close", "operator": "crosses_above", "right": "CPR_S3"}]


def _bar(close: float, stamp: str) -> pd.Series:
    row = pd.Series(
        {"open": close, "high": close, "low": close, "close": close, "current_close": close, "CPR_S3": 23700.45}
    )
    row.name = pd.Timestamp(stamp)
    return row


def _engine(tmp):
    engine = LiveEngine(dhan=object(), run_id="exit-why", state_dir=tmp)
    engine.strategy = {"run_name": "exit-why"}
    engine.exit_conditions = EXIT_RULE
    engine._prev_row = _bar(23681.65, "2026-09-08 10:05:00")
    return engine


def _position() -> dict:
    return {
        "leg_num": 1,
        "transaction_type": "BUY",
        "entry_premium": 250.525,
        "peak_premium": 279.0,
        "quantity": 130,
        "lot_size": 65,
        "lots": 2,
        "sl_pct": 20,
        "status": "open",
    }


class TheExitNamesItsRule(unittest.TestCase):
    def test_the_cross_is_recorded_from_the_bars_that_crossed(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(tmp)
            pos = _position()
            reason = engine._check_exit_conditions(pos, _bar(23707.90, "2026-09-08 10:10:00"), 229.45)
            self.assertEqual(reason, "EXIT_SIGNAL")
            why = pos.get("_exit_why")
            self.assertIsNotNone(why, "the decision must keep its own reasons")
            fired = [c for c in why["conditions"] if c["result"]]
            self.assertTrue(fired, f"an EXIT_SIGNAL with every condition false is the bug: {why['conditions']}")
            self.assertIn("CPR_S3", fired[0]["condition"])

    def test_the_log_line_says_which_condition(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine(tmp)
            pos = _position()
            engine._check_exit_conditions(pos, _bar(23707.90, "2026-09-08 10:10:00"), 229.45)
            spoken = LiveEngine._conditions_that_fired(pos["_exit_why"])
            self.assertEqual(len(spoken), 1)
            self.assertIn("crosses_above", spoken[0])
            self.assertIn("CPR_S3", spoken[0])

    def test_nothing_is_spoken_when_nothing_fired(self):
        self.assertEqual(LiveEngine._conditions_that_fired(None), [])
        self.assertEqual(
            LiveEngine._conditions_that_fired({"conditions": [{"condition": "x is_below y", "result": False}]}),
            [],
        )


if __name__ == "__main__":
    unittest.main()
