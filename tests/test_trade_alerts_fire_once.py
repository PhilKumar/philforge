"""A trade is announced on Telegram once, however many times the app restarts.

Phil, 2026-09-03: "Why again and again I getting this telegram message for the
exit trade?"

Because the alert tracker was an in-memory dict holding a COUNT:

    _alert_state: Dict[str, dict] = {}          # {"in_trade", "closed_count"}
    ...
    if new_count > prev["closed_count"]:
        for t in closed_trades[prev["closed_count"]:]:
            alerter.alert("Trade Exit", ...)

Nothing persisted it. Every process restart emptied the dict, the restored
engine came back with the day's `closed_trades` still in its list, and the
whole list read as new -- so each deploy re-announced every exit that had
already happened. There were sixteen process starts on 03-Sep, and the 10:45
exit went out again on the 11:40 restart.

A count is the wrong thing to remember for a second reason: it assumes the list
only ever grows, in order. What is remembered now is WHICH trades have been
announced, by their own identity, in `app_state`.
"""

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PHILFORGE_PIN", "test-pin-not-real")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_SCRIP_MASTER", "0")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")
os.environ.setdefault("DHAN_CLIENT_ID", "dummy")
os.environ.setdefault("DHAN_ACCESS_TOKEN", "dummy")

import app as app_module  # noqa: E402

EXIT = {
    "id": 1,
    "symbol": "NIFTY 23750 CE",
    "exit_time": "2026-09-03T10:45:02",
    "exit_reason": "EXIT_SIGNAL",
    "pnl": -1723.80,
}


class AlertHarness(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.store = {}
        self._alert = app_module.alerter.alert
        self._get = app_module._db_mod.get_app_state
        self._set = app_module._db_mod.set_app_state

        app_module.alerter.alert = lambda title, body, level="error": self.sent.append((title, body))

        async def _get(key):
            return self.store.get(key)

        async def _set(key, value):
            self.store[key] = value

        app_module._db_mod.get_app_state = _get
        app_module._db_mod.set_app_state = _set
        app_module._alert_state.clear()
        # The exits below happened on 03-Sep; an exit is only news on its day.
        self._today = app_module._ist_date_str
        app_module._ist_date_str = lambda value=None: "2026-09-03"

    def tearDown(self):
        app_module._ist_date_str = self._today
        app_module.alerter.alert = self._alert
        app_module._db_mod.get_app_state = self._get
        app_module._db_mod.set_app_state = self._set
        app_module._alert_state.clear()

    def fire(self, event, *, restart=False):
        if restart:
            app_module._alert_state.clear()  # what a process restart does
        asyncio.run(app_module._check_trade_alerts("PE_NoTarget", "Auto (LIVE)", dict(event), user_id=1))

    def event(self, *trades, in_trade=False):
        return {
            "type": "trade_closed",
            "in_trade": in_trade,
            "closed_trades": list(trades),
            "positions": [],
            "total_pnl": sum(t.get("pnl", 0) for t in trades),
        }

    def exits(self):
        return [b for t, b in self.sent if t == "Trade Exit"]


class OneExitIsOneMessage(AlertHarness):
    def test_the_exit_is_announced(self):
        self.fire(self.event(EXIT))
        self.assertEqual(len(self.exits()), 1)

    def test_further_status_events_in_the_same_process_say_nothing(self):
        for _ in range(4):
            self.fire(self.event(EXIT))
        self.assertEqual(len(self.exits()), 1)

    def test_sixteen_restarts_do_not_make_sixteen_messages(self):
        """03-Sep had sixteen process starts; the 10:45 exit went out twice."""
        self.fire(self.event(EXIT))
        for _ in range(16):
            self.fire(self.event(EXIT), restart=True)
        self.assertEqual(len(self.exits()), 1, "a restart must not re-announce a trade already sent")

    def test_a_genuinely_new_exit_still_gets_through(self):
        """The fix must not buy silence by suppressing real trades."""
        self.fire(self.event(EXIT))
        second = {
            "id": 2,
            "symbol": "NIFTY 23800 PE",
            "exit_time": "2026-09-03T14:10:00",
            "exit_reason": "STOP_LOSS",
            "pnl": -410.0,
        }
        self.fire(self.event(EXIT, second), restart=True)
        self.assertEqual(len(self.exits()), 2)
        self.assertIn("23800 PE", self.exits()[1])

    def test_a_reordered_list_does_not_replay_it(self):
        """A count assumed the list only grows, in order. This does not."""
        second = {"id": 2, "symbol": "NIFTY 23800 PE", "exit_time": "2026-09-03T14:10:00", "pnl": -410.0}
        self.fire(self.event(EXIT, second))
        self.assertEqual(len(self.exits()), 2)
        self.fire(self.event(second, EXIT), restart=True)  # same two, other order
        self.assertEqual(len(self.exits()), 2)


class TheEntryAlertToo(AlertHarness):
    def test_an_open_position_is_not_re_announced_on_restart(self):
        """`in_trade and not prev["in_trade"]` had the same amnesia."""
        self.fire(self.event(in_trade=True))
        self.fire(self.event(in_trade=True), restart=True)
        self.assertEqual(len([t for t, _ in self.sent if t == "Trade Entry"]), 1)


class WhatIsWrittenDown(AlertHarness):
    def test_it_is_persisted_under_its_own_key(self):
        self.fire(self.event(EXIT))
        self.assertIn("trade_alerts:1:PE_NoTarget", self.store)

    def test_the_record_names_the_trades_not_a_count(self):
        self.fire(self.event(EXIT))
        stored = json.loads(self.store["trade_alerts:1:PE_NoTarget"])
        self.assertNotIn("closed_count", stored)
        self.assertEqual(len(stored["seen"]), 1)
        self.assertIn("NIFTY 23750 CE", stored["seen"][0])

    def test_the_set_cannot_grow_without_bound(self):
        cap = app_module._ALERT_SEEN_CAP
        many = [dict(EXIT, id=i, exit_time=f"t{i}") for i in range(cap + 40)]
        self.fire(self.event(*many))
        stored = json.loads(self.store["trade_alerts:1:PE_NoTarget"])
        self.assertEqual(len(stored["seen"]), cap)

    def test_a_database_failure_costs_an_alert_not_the_run(self):
        async def _boom(*_a, **_k):
            raise RuntimeError("db down")

        app_module._db_mod.get_app_state = _boom
        app_module._db_mod.set_app_state = _boom
        self.fire(self.event(EXIT))  # must not raise
        self.assertEqual(len(self.exits()), 1)


class TheCallersAwaitIt(unittest.TestCase):
    def test_no_call_site_was_left_synchronous(self):
        """A forgotten `await` here is a coroutine that never runs: silence."""
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("await _check_trade_alerts("), 4)
        self.assertNotIn("\n    _check_trade_alerts(", src)
        self.assertNotIn("\n        _check_trade_alerts(", src)


if __name__ == "__main__":
    unittest.main()


class ACorrectedExitIsNotANewExit(AlertHarness):
    """Phil, 2026-09-15: a Telegram "Trade Exit" at 09:15 for the 10-Sep 23700PE.

    The record had been corrected after it was announced -- a broker exit first
    booked at the entry price was repriced from the trade book -- and the old
    fingerprint carried exit_time and pnl, so the corrected row read as new. The
    11-Sep exit, never announced while alerts were crashing, went out with it.
    """

    SEP10 = {
        "id": 1,
        "trading_symbol": "NIFTY 23700PE 2026-09-15",
        "entry_time": "2026-09-10 09:25:09",
        "exit_time": "2026-09-10 15:26:36",
        "exit_reason": "BROKER_MANUAL_EXIT",
        "pnl": 1763.21,
    }

    def test_a_repriced_exit_is_not_sent_again(self):
        app_module._ist_date_str = lambda value=None: "2026-09-10"
        booked_at_entry = dict(self.SEP10, exit_time="2026-09-10 09:25:09", pnl=-12.0)
        self.fire(self.event(booked_at_entry))
        self.fire(self.event(self.SEP10))
        self.assertEqual(len(self.exits()), 1)

    def test_charges_settling_next_day_is_not_sent_again(self):
        app_module._ist_date_str = lambda value=None: "2026-09-10"
        self.fire(self.event(self.SEP10))
        app_module._ist_date_str = lambda value=None: "2026-09-11"
        self.fire(self.event(dict(self.SEP10, pnl=1701.4)), restart=True)
        self.assertEqual(len(self.exits()), 1)

    def test_an_exit_from_an_earlier_day_is_never_news(self):
        app_module._ist_date_str = lambda value=None: "2026-09-15"
        self.fire(self.event(self.SEP10), restart=True)
        self.assertEqual(self.exits(), [])

    def test_and_it_is_remembered_so_it_stays_quiet(self):
        app_module._ist_date_str = lambda value=None: "2026-09-15"
        self.fire(self.event(self.SEP10))
        self.assertIn(app_module._closed_trade_identity(self.SEP10), self.store["trade_alerts:1:PE_NoTarget"])

    def test_a_record_written_the_old_way_still_counts_as_sent(self):
        """Prod's saved state holds old fingerprints; a deploy must not replay them."""
        old = app_module._closed_trade_fingerprint(self.SEP10)
        self.store["trade_alerts:1:PE_NoTarget"] = json.dumps({"in_trade": False, "seen": [old]})
        app_module._ist_date_str = lambda value=None: "2026-09-10"
        self.fire(self.event(self.SEP10), restart=True)
        self.assertEqual(self.exits(), [])

    def test_todays_exit_still_goes_out(self):
        """Silence must not be bought by suppressing a real exit."""
        app_module._ist_date_str = lambda value=None: "2026-09-10"
        self.fire(self.event(self.SEP10))
        self.assertEqual(len(self.exits()), 1)
        self.assertIn("23700PE", self.exits()[0])

    def test_an_exit_with_no_time_is_not_silenced(self):
        untimed = {k: v for k, v in self.SEP10.items() if k != "exit_time"}
        self.fire(self.event(untimed))
        self.assertEqual(len(self.exits()), 1)
