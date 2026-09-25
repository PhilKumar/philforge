"""Phil must not have to watch a live book to know it is working.

25-Sep-2026: "I am really annoyed in this monitoring a live trade daily.. For
this I can do trade manually.."

Every failure that made him watch was a SILENT one -- an engine that stops
taking candles, a token that dies mid-session, a strategy quietly deciding on a
number the broker disagrees with. None of them raise; they just stop doing
anything, and doing nothing looks exactly like a quiet market.

So silence is the alarm. These pin the three things that have to hold for
"no news" to mean "the books are fine": a quiet book speaks, it speaks ONCE,
and it says so when it recovers.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_mod  # noqa: E402
from alerter import IST  # noqa: E402


class _Engine:
    def __init__(self, name, last_candle=None, running=True):
        self.strategy = {"name": name}
        self.current_time = last_candle
        self.running = running


class AQuietBookRaisesTheAlarm(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._real_alert = app_mod.alerter.alert
        app_mod.alerter.alert = lambda title, body, level="error": self.sent.append((title, level, body))
        app_mod._live_watchdog_quiet.clear()
        self.now = datetime(2026, 9, 25, 11, 0, tzinfo=IST)  # a Friday, mid-session

    def tearDown(self):
        app_mod.alerter.alert = self._real_alert
        app_mod._live_watchdog_quiet.clear()

    def test_a_book_that_stopped_taking_candles_is_reported(self):
        engine = _Engine("PE_NoTarget", self.now - timedelta(minutes=20))
        quiet = app_mod._live_watchdog_silence_minutes(engine, self.now)
        self.assertGreaterEqual(quiet, app_mod._LIVE_WATCHDOG_SILENCE_MIN)

    def test_a_book_on_the_current_candle_is_not_reported(self):
        engine = _Engine("PE_NoTarget", self.now - timedelta(minutes=2))
        quiet = app_mod._live_watchdog_silence_minutes(engine, self.now)
        self.assertLess(quiet, app_mod._LIVE_WATCHDOG_SILENCE_MIN)

    def test_a_book_that_never_started_this_session_counts_as_quiet(self):
        """The 09:15 failure mode: running, registered, and never ticked once."""
        self.assertIsNone(app_mod._live_watchdog_silence_minutes(_Engine("PE", None), self.now))

    def test_a_naive_timestamp_is_read_as_IST_not_as_UTC(self):
        """Prod runs on UTC. A naive stamp read as UTC makes every book look
        five and a half hours silent, which is a false alarm every minute."""
        naive = (self.now - timedelta(minutes=3)).replace(tzinfo=None)
        quiet = app_mod._live_watchdog_silence_minutes(_Engine("PE", naive), self.now)
        self.assertLess(quiet, app_mod._LIVE_WATCHDOG_SILENCE_MIN)

    def test_a_garbled_timestamp_does_not_crash_the_watchdog(self):
        engine = _Engine("PE", "not a datetime")
        self.assertIsNone(app_mod._live_watchdog_silence_minutes(engine, self.now))

    def test_only_running_books_are_watched(self):
        app_mod.live_engines.clear()
        app_mod.live_engines[1]["run-a"] = _Engine("running one", self.now)
        app_mod.live_engines[1]["run-b"] = _Engine("stopped one", self.now, running=False)
        try:
            watched = [name for name, _e in app_mod._live_watchdog_targets()]
            self.assertEqual(watched, ["run-a"])
        finally:
            app_mod.live_engines.clear()

    def test_the_window_stops_before_the_square_off(self):
        """After 15:20 the books stop by design; that silence is not a fault."""
        self.assertLess(
            app_mod._LIVE_WATCHDOG_LAST_CHECK.hour * 60 + app_mod._LIVE_WATCHDOG_LAST_CHECK.minute, 15 * 60 + 25
        )
        self.assertGreater(
            app_mod._LIVE_WATCHDOG_FIRST_CHECK.hour * 60 + app_mod._LIVE_WATCHDOG_FIRST_CHECK.minute, 9 * 60 + 15
        )

    def test_the_watchdog_is_registered_so_a_deploy_brings_it_back(self):
        """A loop started only at startup dies at every deploy: a standby worker
        skips the startup block, so it must live in _ensure_auto_loops_running."""
        src = open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"), encoding="utf-8"
        ).read()
        block = src.split("def _ensure_auto_loops_running")[1].split("started: list[str] = []")[0]
        self.assertIn("_run_live_watchdog_loop", block)


if __name__ == "__main__":
    unittest.main()
