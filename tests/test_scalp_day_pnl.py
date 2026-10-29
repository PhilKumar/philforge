"""The Scalp header's P&L is the DAY's, and a restart does not reset it.

Phil, 2026-10-29: "Make this session PL as current day PL... Because if engine
restarts, it resets and start from beginning". The engine's closed list lives
in memory and empties on every restart; the saved ledger does not.
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402

TODAY = "2026-10-29"


def _trade(tid, pnl, exit_time=f"{TODAY}T10:00:00", mode="live"):
    return {"trade_id": tid, "pnl": pnl, "exit_time": exit_time, "entry_time": exit_time, "mode": mode}


def _run(status, saved):
    app._SCALP_DAY_CLOSED_CACHE.clear()
    with (
        mock.patch.object(app._db_mod, "list_scalp_trades", mock.AsyncMock(return_value=saved)),
        mock.patch.object(app, "_ist_date_str", return_value=TODAY),
    ):
        return asyncio.run(app._with_scalp_day_pnl(7, status))


class TheDaysPnl(unittest.TestCase):
    def test_a_fresh_engine_still_shows_the_days_closed_trades(self):
        # After a restart the engine holds nothing; the ledger holds the day.
        status = {"closed_trades": [], "open_pnl": 0.0, "session_pnl": 0.0}
        saved = [_trade(305, 64.0), _trade(306, 295.0), _trade(300, 500.0, exit_time="2026-10-28T14:00:00")]
        out = _run(status, saved)
        self.assertEqual(out["session_pnl"], 359.0)
        self.assertEqual(out["day_closed_pnl"], 359.0)

    def test_open_positions_add_to_it(self):
        status = {"closed_trades": [], "open_pnl": -20.5}
        self.assertEqual(_run(status, [_trade(305, 64.0)])["session_pnl"], 43.5)

    def test_a_trade_is_never_counted_twice(self):
        # The engine's copy and the saved one are the same trade; the saved
        # one wins (a Dhan re-pricing rewrites it).
        status = {"closed_trades": [_trade(306, 300.0)], "open_pnl": 0.0}
        self.assertEqual(_run(status, [_trade(306, 295.0)])["session_pnl"], 295.0)

    def test_a_trade_closed_before_the_ledger_refresh_still_counts(self):
        status = {"closed_trades": [_trade(307, 50.0)], "open_pnl": 0.0}
        self.assertEqual(_run(status, [_trade(306, 295.0)])["session_pnl"], 345.0)

    def test_yesterdays_engine_trades_are_left_out(self):
        status = {"closed_trades": [_trade(299, 999.0, exit_time="2026-10-28T15:00:00")], "open_pnl": 0.0}
        self.assertEqual(_run(status, [])["session_pnl"], 0.0)

    def test_a_delete_drops_the_cached_day(self):
        app._SCALP_DAY_CLOSED_CACHE[7] = (0.0, TODAY, {"trade_id:1": 1.0})
        app._forget_scalp_day_pnl(7)
        self.assertNotIn(7, app._SCALP_DAY_CLOSED_CACHE)


if __name__ == "__main__":
    unittest.main()
