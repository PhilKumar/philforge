"""A Dhan live book warms up from Zerodha when Dhan's chart API refuses.

2026-10-23: from 08:35 Dhan answered every chart request DH-905. The 09:35
restart killed both Dhan books in their warm-up fetch ("the engine driver
stopped unexpectedly") while the Zerodha copies restarted and took the 09:50
PE; the CE's 20-day filter had no daily closes all morning.
"""

import os
import unittest
from unittest import mock

import pandas as pd

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402
from broker import zerodha  # noqa: E402
from engine.live import LiveEngine  # noqa: E402

FRAME = pd.DataFrame(
    {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [10]},
    index=pd.DatetimeIndex([pd.Timestamp("2026-10-23 09:15")], name="timestamp"),
)


class _Broker:
    def __init__(self, answer):
        self.answer, self.asked = answer, 0

    def get_historical_data(self, **kw):
        self.asked += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _engine(dhan, fallback):
    engine = LiveEngine.__new__(LiveEngine)
    engine.dhan = dhan
    engine.history_fallback = fallback
    engine.log_event = lambda *a, **k: None
    engine._indicator_context_raw = pd.DataFrame()
    return engine


class TheWarmUpFallsBack(unittest.TestCase):
    def test_dhan_refusing_is_answered_by_zerodha(self):
        kite = _Broker(FRAME)
        engine = _engine(_Broker(Exception('Dhan API error 400: {"errorCode":"DH-905"}')), kite)
        self.assertEqual(len(engine._fetch_raw_history("26000", 1)), 1)
        self.assertEqual(kite.asked, 1)

    def test_dhan_answering_never_asks_zerodha(self):
        kite = _Broker(FRAME)
        engine = _engine(_Broker(FRAME), kite)
        engine._fetch_raw_history("26000", 1)
        self.assertEqual(kite.asked, 0)

    def test_no_fallback_keeps_the_old_failure(self):
        engine = _engine(_Broker(Exception("DH-905")), None)
        with self.assertRaisesRegex(Exception, "DH-905"):
            engine._fetch_raw_history("26000", 1)


class TheAppAttachesIt(unittest.TestCase):
    def test_a_dhan_book_gets_the_zerodha_client(self):
        kite = object()
        engine = _engine(_Broker(FRAME), None)
        with mock.patch.object(app, "_user_zerodha_client", lambda user, require_today=False: (kite, "zerodha")):
            app._attach_history_fallback(engine, {"id": 1})
        self.assertIs(engine.history_fallback, kite)

    def test_a_zerodha_book_needs_none(self):
        engine = _engine(zerodha.ZerodhaClient("k", "t"), None)
        app._attach_history_fallback(engine, {"id": 1})
        self.assertIsNone(engine.history_fallback)


class ADriverThatNeverRanIsStartedAgain(unittest.TestCase):
    """09:35, 23-Oct-2026: both Dhan books died in their warm-up and stayed down."""

    def _engine(self):
        class _E:
            running = True
            positions = []
            in_trade = False
            _last_processed_candle_time = None

            def __init__(self):
                self.events = []

            def log_event(self, level, msg):
                self.events.append(msg)

            def _save_state(self):
                pass

        return _E()

    def _run(self, *, still_wanted=True, starts_fail=1):
        import asyncio

        engine = self._engine()
        calls = []

        async def start():
            calls.append(1)
            if len(calls) <= starts_fail:
                raise RuntimeError("Dhan API error 400: DH-905")
            engine.running = True

        async def scenario():
            bucket = {}
            with (
                mock.patch.object(app, "_ENGINE_RESTART_DELAY_SEC", 0.01),
                mock.patch.object(app.alerter, "alert", lambda *a, **k: None),
            ):
                bucket["run"] = app._supervise_engine_task(
                    asyncio.create_task(start()),
                    engine,
                    run_id="run",
                    restart=start,
                    bucket=bucket,
                    still_wanted=lambda: still_wanted,
                )
                await asyncio.sleep(0.2)
            return bucket

        asyncio.run(scenario())
        return engine, calls

    def test_it_is_started_again_and_runs(self):
        engine, calls = self._run()
        self.assertEqual(len(calls), 2)
        self.assertTrue(engine.running)
        self.assertTrue(any("Starting the engine driver again" in e for e in engine.events))

    def test_a_replaced_engine_is_left_alone(self):
        engine, calls = self._run(still_wanted=False)
        self.assertEqual(len(calls), 1)

    def test_it_gives_up_after_its_tries(self):
        engine, calls = self._run(starts_fail=99)
        self.assertEqual(len(calls), 1 + app._ENGINE_RESTART_TRIES)
        self.assertFalse(engine.running)

    def test_a_book_holding_a_position_is_not_restarted(self):
        import asyncio

        engine = self._engine()
        engine.positions = [{"status": "open"}]
        calls = []

        async def start():
            calls.append(1)
            raise RuntimeError("boom")

        async def scenario():
            with (
                mock.patch.object(app, "_ENGINE_RESTART_DELAY_SEC", 0.01),
                mock.patch.object(app.alerter, "alert", lambda *a, **k: None),
            ):
                app._supervise_engine_task(
                    asyncio.create_task(start()), engine, run_id="run", restart=start, still_wanted=lambda: True
                )
                await asyncio.sleep(0.1)

        asyncio.run(scenario())
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
