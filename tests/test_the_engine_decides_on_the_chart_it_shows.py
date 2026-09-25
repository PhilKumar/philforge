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
not guess at which mechanism thins that series: it compares the numbers it is
about to trade on against the broker's own history and decides on the history.

AND IT COSTS THE ENTRY NOTHING. The first version fetched from Dhan inside the
decision path; Phil asked whether that landed "in that triggering fraction of a
second when taking an entry", and it did -- about a second at the moment of
signalling, and again on every candle while a divergence was unresolved. This
engine has been here before: sync broker calls in the loop are what made live
entries late (fdd253b). So the reference frame is built before the session and
refreshed in the background, and the check itself is pure arithmetic.
"""

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.indicators import compute_dynamic_indicators  # noqa: E402
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


def _ema(df: pd.DataFrame) -> pd.DataFrame:
    return compute_dynamic_indicators(
        df, ["EMA_20_5m"], default_timeframe_minutes=5, source_timeframe_minutes=5, execution_timeframe_minutes=5
    )


class _Engine:
    """The audit methods under test, on a bare object -- no broker, no loop."""

    def __init__(self):
        self.strategy = {"name": "PE_NoTarget", "instrument": "26000"}
        self.dhan = object()
        self.event_log = []
        self.indicator_divergence = None
        self._indicator_reference = None
        self._indicator_reference_at = None
        self._indicator_reference_task = None
        self._session_book = None
        self.fetches = []

    def _fetch_raw_history(self, instrument, fetch_timeframe, *, days=7):
        self.fetches.append(instrument)  # must never happen on a decision
        raise AssertionError("the decision path must not call the broker")

    def _apply_daily_averages(self, df):
        return df

    def log_event(self, event_type, message, data=None):
        self.event_log.append({"type": event_type, "message": message, "data": data or {}})

    _INDICATOR_AUDIT_TOL_PCT = LiveEngine._INDICATOR_AUDIT_TOL_PCT
    _INDICATOR_AUDIT_TOL_MIN = LiveEngine._INDICATOR_AUDIT_TOL_MIN
    _INDICATOR_REFERENCE_MAX_AGE_MIN = LiveEngine._INDICATOR_REFERENCE_MAX_AGE_MIN
    set_indicator_reference = LiveEngine.set_indicator_reference
    _indicator_reference_is_stale = LiveEngine._indicator_reference_is_stale
    _refresh_indicator_reference_soon = LiveEngine._refresh_indicator_reference_soon
    _audit_indicators_against_history = LiveEngine._audit_indicators_against_history


class TheEngineDecidesOnTheChartItShows(unittest.TestCase):
    def setUp(self):
        # A week sitting high, then a sharp fall in the last half hour -- the
        # shape of 24/25-Sep. An EMA carried across the whole series sits well
        # above price; one seeded only on the fall sits right on it.
        self.history = pd.concat(
            [_bars("2026-09-18 09:15", 294, 23200.0), _bars("2026-09-25 09:15", 6, 23180.0, step=-26.0)]
        )
        self.reference = _ema(self.history)
        self.starved = _ema(self.history.tail(6))
        self.at = self.starved.index[-1]

    def _engine(self, with_reference=True):
        e = _Engine()
        if with_reference:
            e.set_indicator_reference(self.reference)
        return e

    def _audit(self, engine, frame):
        return engine._audit_indicators_against_history(frame, ["EMA_20_5m"], 5, 5)

    def test_the_fixture_really_reproduces_a_starved_ema(self):
        gap = abs(float(self.starved.at[self.at, "EMA_20_5m"]) - float(self.reference.at[self.at, "EMA_20_5m"]))
        self.assertGreater(gap, 5.0)

    def test_a_starved_ema_is_caught_and_the_history_is_used_instead(self):
        engine = self._engine()
        out = self._audit(engine, self.starved)
        self.assertIsNotNone(engine.indicator_divergence)
        self.assertIn("EMA_20_5m", engine.indicator_divergence["columns"])
        self.assertTrue(any(e["type"] == "error" for e in engine.event_log), engine.event_log)
        self.assertAlmostEqual(
            float(out.at[self.at, "EMA_20_5m"]), float(self.reference.at[self.at, "EMA_20_5m"]), places=6
        )

    def test_the_decision_path_never_calls_the_broker(self):
        """The whole point of the rewrite: no I/O in the fraction of a second
        that takes an entry. _fetch_raw_history raises if it is ever reached."""
        engine = self._engine()
        for _ in range(5):
            self._audit(engine, self.starved)
        self.assertEqual(engine.fetches, [])

    def test_a_frame_that_agrees_is_left_alone(self):
        engine = self._engine()
        out = self._audit(engine, self.reference)
        self.assertIsNone(engine.indicator_divergence)
        self.assertEqual(len(out), len(self.reference))
        self.assertFalse([e for e in engine.event_log if e["type"] == "error"], engine.event_log)

    def test_without_a_reference_it_waits_rather_than_blocking(self):
        """Before the first reference exists the engine trades as it always did
        -- it must never stall an entry waiting for a check."""
        engine = self._engine(with_reference=False)
        out = self._audit(engine, self.starved)
        self.assertIs(out, self.starved)
        self.assertEqual(engine.fetches, [], "and it still must not fetch inline")

    def test_a_fresh_reference_is_not_refetched(self):
        engine = self._engine()
        self.assertFalse(engine._indicator_reference_is_stale())

    def test_an_old_reference_is_considered_stale(self):
        from datetime import timedelta

        from engine.live import _now_ist

        engine = self._engine()
        engine._indicator_reference_at = _now_ist() - timedelta(minutes=engine._INDICATOR_REFERENCE_MAX_AGE_MIN + 1)
        self.assertTrue(engine._indicator_reference_is_stale())

    def test_the_divergence_is_reported_to_phil_not_just_logged(self):
        """He should not have to open the app to find out."""
        import alerter

        sent = []
        real = alerter.alert
        alerter.alert = lambda title, body, level="error": sent.append((title, level))
        try:
            self._audit(self._engine(), self.starved)
        finally:
            alerter.alert = real
        self.assertTrue(sent, "a divergence must raise an alert")
        self.assertEqual(sent[0][1], "error")


if __name__ == "__main__":
    unittest.main()
