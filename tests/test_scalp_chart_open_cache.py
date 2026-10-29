"""At the open, a Scalp chart without a bar of today is asked again within seconds.

Phil, 2026-10-29: "The first few seconds when the market starts, I am not
seeing any candles". A chart cached before 09:15 held only yesterday's bars and
was served for 20 seconds; in the first minutes of a session it lives 3.
"""

import os
import time
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
NSE = {"open": "09:15", "close": "15:30", "exchange": "NSE"}


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=IST)


def _cached(age: float, last_bar: str, session=NSE):
    payload = {"session": session, "candles": [{"t": int(_at(last_bar).timestamp()), "c": 100}]}
    return (time.monotonic() - age, payload)


class OpenCache(unittest.TestCase):
    def setUp(self):
        # Thu 29 Oct 2026, a trading day.
        patcher = mock.patch.object(app, "_nse_session_closed", lambda day: day.weekday() >= 5)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_yesterdays_chart_is_asked_again_after_3s_at_the_open(self):
        now = _at("2026-10-29T09:15:30")
        self.assertTrue(app._scalp_chart_cache_fresh(_cached(2, "2026-10-28T15:29:00"), now))
        self.assertFalse(app._scalp_chart_cache_fresh(_cached(4, "2026-10-28T15:29:00"), now))

    def test_a_chart_with_todays_bar_keeps_its_20s(self):
        now = _at("2026-10-29T09:16:00")
        self.assertTrue(app._scalp_chart_cache_fresh(_cached(15, "2026-10-29T09:15:00"), now))
        self.assertFalse(app._scalp_chart_cache_fresh(_cached(21, "2026-10-29T09:15:00"), now))

    def test_only_in_the_first_minutes_of_a_session(self):
        stale = _cached(10, "2026-10-28T15:29:00")
        self.assertTrue(app._scalp_chart_cache_fresh(stale, _at("2026-10-29T09:10:00")))
        self.assertTrue(app._scalp_chart_cache_fresh(stale, _at("2026-10-29T09:21:00")))
        # A Saturday is no open at all.
        self.assertTrue(app._scalp_chart_cache_fresh(_cached(10, "2026-10-30T15:29:00"), _at("2026-10-31T09:16:00")))

    def test_mcx_opens_at_nine(self):
        mcx = {"open": "09:00", "close": "23:30", "exchange": "MCX"}
        now = _at("2026-10-29T09:00:40")
        self.assertFalse(app._scalp_chart_cache_fresh(_cached(5, "2026-10-28T23:29:00", mcx), now))


if __name__ == "__main__":
    unittest.main()
