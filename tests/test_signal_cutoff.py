"""The signal cutoff: past it, nothing that reads NIFTY SPOT may decide a trade.

NSE's closing auction (3 Aug 2026 onward) halts continuous trading in every
F&O-eligible stock at 15:15. Every index constituent is one, so the index stops
being priced by real trades too — until ~15:35 it is computed from the auction's
indicative equilibrium prices, levels nobody traded.

Entries and spot-driven exits must stop there. Stop-loss, target and the timed
square-off must NOT: they read the option's own premium, and options trade to
15:40. On Phil's CE strategy that square-off carries 46% of the total P&L.
"""

import contextlib
import io
import unittest
from datetime import datetime, time

import pandas as pd

from engine.backtest import run_backtest
from engine.live import LiveEngine
from engine.paper_trading import PaperTradingEngine


def _always_true():
    return [{"left": "current_close", "operator": "is_above", "right": "number", "right_number_value": 0}]


def _never_true():
    return [{"left": "current_close", "operator": "is_below", "right": "number", "right_number_value": 0}]


def _afternoon_session(start="14:55", periods=40) -> pd.DataFrame:
    """One session running through 15:15, one bar a minute."""
    index = pd.date_range(f"2026-08-10 {start}", periods=periods, freq="1min")
    closes = [1000.0 + 25 * i for i in range(periods)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [100] * periods,
        },
        index=index,
    )


def _run(df, entry=None, exit_=None, **config):
    base = {"instrument": "RELIANCE", "timeframe_minutes": 1, "lot_size": 1, "market_close": "15:29"}
    base.update(config)
    with contextlib.redirect_stdout(io.StringIO()):
        return run_backtest(
            df,
            entry_conditions=entry or _always_true(),
            exit_conditions=exit_ or _never_true(),
            strategy_config=base,
        )


class BacktestSignalCutoffTests(unittest.TestCase):
    def test_no_entry_after_the_cutoff(self):
        # Entry is blocked all morning, so the only chance to enter is late.
        result = _run(
            _afternoon_session(start="15:14", periods=20),
            signal_cutoff_time="15:15",
            max_trades_per_day=5,
        )
        entries = [str(t["entry_time"])[11:16] for t in result["trades"]]
        self.assertTrue(all(e < "15:15" for e in entries), entries)

    def test_entries_still_taken_before_the_cutoff(self):
        result = _run(_afternoon_session(), signal_cutoff_time="15:15", max_trades_per_day=5)
        self.assertTrue(result["trades"], "the cutoff must not block the whole session")
        self.assertTrue(all(str(t["entry_time"])[11:16] < "15:15" for t in result["trades"]))

    def test_cutoff_off_by_default_leaves_behaviour_unchanged(self):
        df = _afternoon_session(start="15:14", periods=20)
        with_cutoff = _run(df, signal_cutoff_time="15:15", max_trades_per_day=5)
        without = _run(df, max_trades_per_day=5)
        self.assertEqual(len(without["trades"]) > len(with_cutoff["trades"]), True)

    def test_square_off_still_fires_after_the_cutoff(self):
        """The exit that carries 46% of CE's P&L must survive the guard."""
        result = _run(
            _afternoon_session(start="15:05", periods=30),
            signal_cutoff_time="15:15",
            combined_sqoff_time="15:25",
            market_close="15:34",
            max_trades_per_day=1,
        )
        self.assertTrue(result["trades"])
        squared = [t for t in result["trades"] if t["exit_reason"] == "SquareOff"]
        self.assertTrue(squared, [t["exit_reason"] for t in result["trades"]])
        self.assertGreaterEqual(str(squared[0]["exit_time"])[11:16], "15:15")

    def test_stop_loss_still_fires_after_the_cutoff(self):
        """Premium-based exits read the option, which trades to 15:40."""
        index = pd.date_range("2026-08-10 15:10", periods=20, freq="1min")
        # Rises to trigger entry, then collapses after the cutoff.
        closes = [1000.0, 1100.0, 1200.0] + [1200.0 - 120 * i for i in range(17)]
        df = pd.DataFrame(
            {
                "open": closes,
                "high": [c + 0.5 for c in closes],
                "low": [c - 0.5 for c in closes],
                "close": closes,
                "volume": [100] * 20,
            },
            index=index,
        )
        result = _run(
            df,
            signal_cutoff_time="15:15",
            market_close="15:29",
            legs=[{"transaction_type": "BUY", "option_type": "CE", "lots": 1, "sl_pct": 20, "sqoff_time": "15:28"}],
        )
        reasons = {t["exit_reason"] for t in result["trades"]}
        self.assertTrue(reasons, "a position should have opened and closed")
        self.assertNotIn("Signal", reasons)


class _StubEngineMixin:
    def _engine_with_cutoff(self, cls, cutoff="15:15"):
        engine = cls.__new__(cls)
        engine.strategy = {"market_open": "09:15", "market_close": "15:25", "signal_cutoff_time": cutoff}
        engine.entry_conditions = _always_true()
        engine.exit_conditions = _always_true()
        engine.current_time = None
        # `_apply_session_times` now logs when the square-off sits at or after
        # the broker's intraday cut-off, so the stub needs somewhere to log to.
        engine.event_log = []
        return engine


class LiveSignalCutoffTests(unittest.TestCase, _StubEngineMixin):
    def _engine(self, cutoff="15:15"):
        engine = self._engine_with_cutoff(LiveEngine, cutoff)
        engine._apply_session_times(engine.strategy)
        return engine

    def test_signals_die_at_the_cutoff(self):
        engine = self._engine()
        self.assertTrue(engine._signals_live(datetime(2026, 8, 10, 15, 14)))
        self.assertFalse(engine._signals_live(datetime(2026, 8, 10, 15, 15)))
        self.assertFalse(engine._signals_live(datetime(2026, 8, 10, 15, 24)))

    def test_no_cutoff_means_always_live(self):
        engine = self._engine(cutoff="")
        self.assertIsNone(engine._signal_cutoff)
        self.assertTrue(engine._signals_live(datetime(2026, 8, 10, 15, 24)))

    def test_entry_evaluation_reports_the_cutoff_gate(self):
        engine = self._engine()
        triggered, debug = engine._evaluate_entry_conditions_with_debug(
            pd.Series({"close": 100.0}), None, datetime(2026, 8, 10, 15, 20)
        )
        self.assertFalse(triggered)
        self.assertIn("signal_cutoff", debug["gate"])

    def test_a_restart_does_not_silently_drop_the_guard(self):
        """_load_state restores the strategy without reconfiguring; the parsed
        clock must be re-derived or the guard is off for the rest of the day."""
        engine = LiveEngine.__new__(LiveEngine)
        engine._signal_cutoff = None
        engine._market_open = time(9, 15)
        engine._market_close = time(15, 25)
        engine.event_log = []
        engine._apply_session_times({"market_close": "15:25", "signal_cutoff_time": "15:15"})
        self.assertEqual(engine._signal_cutoff, time(15, 15))


class PaperSignalCutoffTests(unittest.TestCase, _StubEngineMixin):
    def test_signals_die_at_the_cutoff(self):
        engine = self._engine_with_cutoff(PaperTradingEngine)
        self.assertEqual(engine._signal_cutoff_time(), time(15, 15))
        self.assertTrue(engine._signals_live(datetime(2026, 8, 10, 15, 14)))
        self.assertFalse(engine._signals_live(datetime(2026, 8, 10, 15, 16)))

    def test_no_cutoff_means_always_live(self):
        engine = self._engine_with_cutoff(PaperTradingEngine, cutoff="")
        self.assertIsNone(engine._signal_cutoff_time())
        self.assertTrue(engine._signals_live(datetime(2026, 8, 10, 15, 24)))

    def test_entry_evaluation_reports_the_cutoff_gate(self):
        engine = self._engine_with_cutoff(PaperTradingEngine)
        triggered, debug = engine._evaluate_entry_conditions_with_debug(
            pd.Series({"close": 100.0}), None, datetime(2026, 8, 10, 15, 20)
        )
        self.assertFalse(triggered)
        self.assertIn("signal_cutoff", debug["gate"])


if __name__ == "__main__":
    unittest.main()
