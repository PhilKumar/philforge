"""The live engine must not trade on an indicator the broker's history contradicts.

25-Sep-2026. The live PE book entered at 09:45 instead of 09:20. Its rule is
"current_close is_below EMA_20_5m"; at the 09:15 candle it held the EMA at
23,050 while the SAME code on the SAME Dhan history put it at 23,094 -- the
value on Phil's chart. The engine's EMA had not carried across the session
break: 44 points low at the open, still 23 points low at 09:40, converging only
through the morning. That strategy only trades before 11:00, so the whole
decision window sat inside the error, and Phil bought a later, dearer PE.

Every offline reconstruction of the series -- 5m history, 1m resampled, any
window from 10 bars to 385, with and without the pre-open bar -- landed between
23,079 and 23,096. Only the running engine, which decides on WebSocket-built
candles merged with a remembered context, produced 23,066.7. So the audit does
not try to guess which mechanism thins that series: it compares the numbers it
is about to trade on against the broker's own history, says so loudly, and
decides on the history instead.
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.live import LiveEngine  # noqa: E402


def _bars(start: str, n: int, price: float, step: float = 0.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="5min")
    close = [price + step * i for i in range(n)]
    return pd.DataFrame(
        {
            "open": close,
            "high": [c + 2 for c in close],
            "low": [c - 2 for c in close],
            "close": close,
            "volume": [1000] * n,
        },
        index=idx,
    )


class _Engine:
    """The audit method under test, on a bare object -- no broker, no loop."""

    def __init__(self, history: pd.DataFrame):
        self.strategy = {"instrument": "26000"}
        self.dhan = object()  # merely "configured"
        self.event_log = []
        self.indicator_divergence = None
        self._indicator_audit_day = None
        self._session_book = None
        self._history = history

    # the two collaborators the audit uses
    def _fetch_raw_history(self, instrument, fetch_timeframe, *, days=7):
        return self._history

    def _apply_daily_averages(self, df):
        return df

    def log_event(self, event_type, message, data=None):
        self.event_log.append({"type": event_type, "message": message, "data": data or {}})

    _INDICATOR_AUDIT_TOL_PCT = LiveEngine._INDICATOR_AUDIT_TOL_PCT
    _INDICATOR_AUDIT_TOL_MIN = LiveEngine._INDICATOR_AUDIT_TOL_MIN
    _audit_indicators_against_history = LiveEngine._audit_indicators_against_history


class TheEngineDecidesOnTheChartItShows(unittest.TestCase):
    def setUp(self):
        # A week of history that sat high, then a sharp fall in the last half
        # hour -- exactly the shape of 24/25-Sep. An EMA carried across the
        # whole series still sits well above price; one seeded only on the fall
        # sits right on it. That gap is the bug.
        high = _bars("2026-09-18 09:15", 294, 23200.0)
        fall = _bars("2026-09-25 09:15", 6, 23180.0, step=-26.0)
        self.history = pd.concat([high, fall])

    def _audit(self, engine, frame):
        return engine._audit_indicators_against_history(frame, ["EMA_20_5m"], 5, 5)

    def test_a_starved_ema_is_caught_and_the_history_is_used_instead(self):
        """The real shape of the bug: the frame holds only this morning's bars."""
        from engine.indicators import compute_dynamic_indicators

        ref = compute_dynamic_indicators(
            self.history,
            ["EMA_20_5m"],
            default_timeframe_minutes=5,
            source_timeframe_minutes=5,
            execution_timeframe_minutes=5,
        )
        starved = compute_dynamic_indicators(
            self.history.tail(6),
            ["EMA_20_5m"],
            default_timeframe_minutes=5,
            source_timeframe_minutes=5,
            execution_timeframe_minutes=5,
        )
        at = starved.index[-1]
        gap = abs(float(starved.at[at, "EMA_20_5m"]) - float(ref.at[at, "EMA_20_5m"]))
        self.assertGreater(gap, 5.0, "the fixture must actually reproduce a starved EMA")

        engine = _Engine(self.history)
        out = self._audit(engine, starved)

        self.assertIsNotNone(engine.indicator_divergence, "the divergence must be recorded")
        self.assertIn("EMA_20_5m", engine.indicator_divergence["columns"])
        self.assertTrue(any(e["type"] == "error" for e in engine.event_log), engine.event_log)
        # and the decision is taken on the history, not the starved frame
        self.assertAlmostEqual(float(out.at[at, "EMA_20_5m"]), float(ref.at[at, "EMA_20_5m"]), places=6)

    def test_a_frame_that_agrees_is_left_alone(self):
        from engine.indicators import compute_dynamic_indicators

        good = compute_dynamic_indicators(
            self.history,
            ["EMA_20_5m"],
            default_timeframe_minutes=5,
            source_timeframe_minutes=5,
            execution_timeframe_minutes=5,
        )
        engine = _Engine(self.history)
        out = self._audit(engine, good)
        self.assertIsNone(engine.indicator_divergence)
        self.assertEqual(len(out), len(good))
        self.assertFalse([e for e in engine.event_log if e["type"] == "error"], engine.event_log)

    def test_it_checks_once_a_session_while_everything_agrees(self):
        from engine.indicators import compute_dynamic_indicators

        good = compute_dynamic_indicators(
            self.history,
            ["EMA_20_5m"],
            default_timeframe_minutes=5,
            source_timeframe_minutes=5,
            execution_timeframe_minutes=5,
        )
        engine = _Engine(self.history)
        calls = []
        real = engine._fetch_raw_history

        def counted(*a, **k):
            calls.append(1)
            return real(*a, **k)

        engine._fetch_raw_history = counted
        self._audit(engine, good)
        self._audit(engine, good)
        self._audit(engine, good)
        self.assertEqual(len(calls), 1, "a clean session must not refetch on every candle")

    def test_a_broken_audit_never_stops_the_engine_trading(self):
        from engine.indicators import compute_dynamic_indicators

        good = compute_dynamic_indicators(
            self.history,
            ["EMA_20_5m"],
            default_timeframe_minutes=5,
            source_timeframe_minutes=5,
            execution_timeframe_minutes=5,
        )
        engine = _Engine(self.history)

        def boom(*a, **k):
            raise RuntimeError("Dhan is down")

        engine._fetch_raw_history = boom
        out = self._audit(engine, good)
        self.assertEqual(len(out), len(good), "the frame must pass through untouched")
        self.assertTrue(any("audit could not run" in e["message"] for e in engine.event_log))

    def test_without_a_broker_it_does_nothing(self):
        from engine.indicators import compute_dynamic_indicators

        good = compute_dynamic_indicators(
            self.history,
            ["EMA_20_5m"],
            default_timeframe_minutes=5,
            source_timeframe_minutes=5,
            execution_timeframe_minutes=5,
        )
        engine = _Engine(self.history)
        engine.dhan = None
        out = self._audit(engine, good)
        self.assertIs(out, good)


if __name__ == "__main__":
    unittest.main()
