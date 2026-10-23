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


if __name__ == "__main__":
    unittest.main()
