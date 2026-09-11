"""The big-win cooldown: after a day whose net profit beats the threshold,
the next N trading sessions take no new entries — backtest and live alike."""

import contextlib
import io
import json
import tempfile
import unittest
from datetime import date

import pandas as pd

from engine.backtest import run_backtest
from engine.live import LiveEngine
from engine.paper_trading import PaperTradingEngine


def _always_true_conditions():
    return [{"left": "current_close", "operator": "is_above", "right": "number", "right_number_value": 0}]


def _four_rising_days() -> pd.DataFrame:
    frames = []
    for day in ("2026-03-16", "2026-03-17", "2026-03-18", "2026-03-19"):  # Mon–Thu
        index = pd.date_range(f"{day} 09:20", periods=6, freq="1min")
        # Steps large enough that a day's net P&L clears the fee stack by far
        closes = [100.0, 600.0, 1100.0, 1600.0, 2100.0, 2600.0]
        frames.append(
            pd.DataFrame(
                {
                    "open": closes,
                    "high": [c + 0.5 for c in closes],
                    "low": [c - 0.5 for c in closes],
                    "close": closes,
                    "volume": [100] * 6,
                },
                index=index,
            )
        )
    return pd.concat(frames)


def _run(df, **config):
    base = {"instrument": "RELIANCE", "timeframe_minutes": 1, "lot_size": 1, "skip_stub_sessions": False}
    base.update(config)
    with contextlib.redirect_stdout(io.StringIO()):
        return run_backtest(
            df,
            entry_conditions=_always_true_conditions(),
            exit_conditions=_always_true_conditions(),
            strategy_config=base,
        )


class BacktestCooldownTests(unittest.TestCase):
    def test_skip_two_days_after_profit_day(self):
        result = _run(_four_rising_days(), skip_days_after_profit=2, skip_profit_threshold_rupees=1)

        trade_days = sorted({str(t["entry_time"])[:10] for t in result["trades"]})
        self.assertEqual(trade_days, ["2026-03-16", "2026-03-19"])
        self.assertEqual(result["stats"]["cooldown_sessions_skipped"], 2)

    def test_cooldown_off_trades_every_day(self):
        result = _run(_four_rising_days(), skip_days_after_profit=0, skip_profit_threshold_rupees=1)

        trade_days = sorted({str(t["entry_time"])[:10] for t in result["trades"]})
        self.assertEqual(len(trade_days), 4)
        self.assertEqual(result["stats"]["cooldown_sessions_skipped"], 0)

    def test_day_below_threshold_does_not_arm(self):
        result = _run(_four_rising_days(), skip_days_after_profit=2, skip_profit_threshold_rupees=10_000_000)

        trade_days = sorted({str(t["entry_time"])[:10] for t in result["trades"]})
        self.assertEqual(len(trade_days), 4)
        self.assertEqual(result["stats"]["cooldown_sessions_skipped"], 0)


class LiveCooldownTests(unittest.TestCase):
    def _engine(self, state_dir: str) -> LiveEngine:
        engine = LiveEngine(dhan=object(), run_id="cooldown-test", state_dir=state_dir)
        engine.strategy = {"skip_days_after_profit": 2, "skip_profit_threshold_rupees": 20000}
        return engine

    def test_weekend_does_not_consume_the_skip(self):
        with tempfile.TemporaryDirectory() as state_dir:
            engine = self._engine(state_dir)
            engine.profit_cooldown_trigger_date = date(2026, 3, 20)  # a Friday

            self.assertFalse(engine._profit_cooldown_active(date(2026, 3, 21)))  # Sat: closed anyway
            self.assertTrue(engine._profit_cooldown_active(date(2026, 3, 23)))  # Mon: session 1
            self.assertTrue(engine._profit_cooldown_active(date(2026, 3, 24)))  # Tue: session 2
            self.assertFalse(engine._profit_cooldown_active(date(2026, 3, 25)))  # Wed: free

    def test_arms_only_above_threshold(self):
        with tempfile.TemporaryDirectory() as state_dir:
            engine = self._engine(state_dir)
            engine.session_date = date(2026, 3, 20)

            engine.daily_pnl = 19999.0
            engine._arm_profit_cooldown()
            self.assertIsNone(engine.profit_cooldown_trigger_date)

            engine.daily_pnl = 20001.0
            engine._arm_profit_cooldown()
            self.assertEqual(engine.profit_cooldown_trigger_date, date(2026, 3, 20))

    def test_trigger_survives_a_stale_state_restart(self):
        with tempfile.TemporaryDirectory() as state_dir:
            engine = self._engine(state_dir)
            engine.session_date = date(2026, 3, 20)
            engine.daily_pnl = 25000.0
            engine._arm_profit_cooldown()
            engine._save_state()

            # Stale state (saved Friday, restarted Monday, no open positions):
            # everything else is discarded, the cooldown must not be.
            state_file = engine._state_file
            with open(state_file) as f:
                state = json.load(f)
            state["session_date"] = "2026-03-20"
            with open(state_file, "w") as f:
                json.dump(state, f, default=str)

            restarted = self._engine(state_dir)
            restarted._load_state()  # app.py calls this right after constructing the engine
            self.assertEqual(restarted.profit_cooldown_trigger_date, date(2026, 3, 20))

    def test_disabled_config_never_blocks(self):
        with tempfile.TemporaryDirectory() as state_dir:
            engine = self._engine(state_dir)
            engine.strategy = {"skip_days_after_profit": 0}
            engine.profit_cooldown_trigger_date = date(2026, 3, 20)
            self.assertFalse(engine._profit_cooldown_active(date(2026, 3, 23)))


class PaperCooldownTests(unittest.TestCase):
    def _engine(self, state_dir: str) -> PaperTradingEngine:
        engine = PaperTradingEngine(dhan=object(), run_id="cooldown-test", state_dir=state_dir)
        engine.strategy = {"skip_days_after_profit": 2, "skip_profit_threshold_rupees": 20000}
        return engine

    def test_weekend_does_not_consume_the_skip(self):
        with tempfile.TemporaryDirectory() as state_dir:
            engine = self._engine(state_dir)
            engine.profit_cooldown_trigger_date = date(2026, 3, 20)  # a Friday

            self.assertTrue(engine._profit_cooldown_active(date(2026, 3, 23)))  # Mon
            self.assertTrue(engine._profit_cooldown_active(date(2026, 3, 24)))  # Tue
            self.assertFalse(engine._profit_cooldown_active(date(2026, 3, 25)))  # Wed

    def test_arm_and_stale_restart(self):
        with tempfile.TemporaryDirectory() as state_dir:
            engine = self._engine(state_dir)
            engine.session_date = date(2026, 3, 20)
            engine.daily_pnl = 25000.0
            engine._arm_profit_cooldown()
            self.assertEqual(engine.profit_cooldown_trigger_date, date(2026, 3, 20))
            engine._save_state()

            state_file = engine._state_file
            with open(state_file) as f:
                state = json.load(f)
            state["session_date"] = "2026-03-20"
            with open(state_file, "w") as f:
                json.dump(state, f, default=str)

            restarted = self._engine(state_dir)
            restarted._load_state()
            self.assertEqual(restarted.profit_cooldown_trigger_date, date(2026, 3, 20))


if __name__ == "__main__":
    unittest.main()
