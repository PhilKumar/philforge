"""A book that was running last night is running this morning, with its trades.

17-Sep-2026 00:01 IST the server was stopped and started again for a plan
upgrade. Both live books -- CE_SL15_NoMonTue and PE_NoTarget, flat, last saved
at 23:30 on 16-Sep -- were skipped on startup:

    [Restore] Skipping stale state: live_state_CE_SL15_NoMonTue.json (date=2026-09-16)
    [Restore] Skipping stale state: live_state_PE_NoTarget.json (date=2026-09-16)

because the restore only took a state file dated TODAY or holding a position,
and nothing started them again before the 09:15 open. A manual Stop deletes the
state file, so a file that exists means the book was running; only an old
leftover should stay shut. Phil: "I want my live trades opened yesterday to be
there" -- so a restored book also keeps its whole closed-trade record.
"""

import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402
from engine import live as live_mod  # noqa: E402
from engine.live import LiveEngine  # noqa: E402

SRC = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
TODAY = date(2026, 9, 17)


def _state(session_date, open_position=False):
    positions = [{"status": "open" if open_position else "closed", "strike": 23600}]
    return {"session_date": session_date, "positions": positions}


class WhichSavedBooksComeBack(unittest.TestCase):
    def test_last_nights_flat_book_comes_back(self):
        """The exact case of 17-Sep 00:01."""
        self.assertTrue(app._state_is_resumable(_state("2026-09-16"), TODAY))

    def test_todays_book_comes_back(self):
        self.assertTrue(app._state_is_resumable(_state("2026-09-17"), TODAY))

    def test_a_fridays_book_comes_back_on_monday(self):
        self.assertTrue(app._state_is_resumable(_state("2026-09-11"), date(2026, 9, 14)))

    def test_a_long_weekend_with_a_holiday_still_comes_back(self):
        self.assertTrue(app._state_is_resumable(_state("2026-09-11"), date(2026, 9, 16)))

    def test_an_abandoned_leftover_stays_shut(self):
        """Paper state files from March still sit on the server."""
        self.assertFalse(app._state_is_resumable(_state("2026-03-05"), TODAY))
        self.assertFalse(app._state_is_resumable(_state("2026-09-10"), TODAY))

    def test_an_open_position_always_comes_back(self):
        self.assertTrue(app._state_is_resumable(_state("2026-03-05", open_position=True), TODAY))

    def test_a_state_without_a_date_stays_shut(self):
        self.assertFalse(app._state_is_resumable({"positions": []}, TODAY))
        self.assertFalse(app._state_is_resumable({"session_date": "garbage"}, TODAY))

    def test_a_date_from_the_future_stays_shut(self):
        self.assertFalse(app._state_is_resumable(_state("2026-09-18"), TODAY))

    def test_today_may_arrive_as_text(self):
        """The restore functions hold `today` as str(_ist_today())."""
        self.assertTrue(app._state_is_resumable(_state("2026-09-16"), "2026-09-17"))


class BothRestoresUseIt(unittest.TestCase):
    def _body(self, name):
        return SRC.split(f"async def {name}(")[1].split("\nasync def ")[0]

    def test_the_live_restore(self):
        body = self._body("_restore_live_engines")
        self.assertIn("if not _state_is_resumable(state, today):", body)
        self.assertNotIn('state.get("session_date") != today and not _state_has_open_positions', body)

    def test_the_paper_restore(self):
        body = self._body("_restore_paper_engines")
        self.assertIn("if not _state_is_resumable(state, today):", body)

    def test_a_manual_stop_still_deletes_the_state_file(self):
        """The whole rule rests on this: a stopped book leaves no file behind."""
        self.assertGreaterEqual(SRC.count("engine._delete_state_file()"), 2)


class TheRestoredBookKeepsItsTrades(unittest.TestCase):
    """Load yesterday's flat state into a fresh engine, as the restore does."""

    TRADES = [
        {
            "id": "a",
            "entry_time": "2026-09-10 09:25:09",
            "exit_time": "2026-09-10 15:26:36",
            "trading_symbol": "NIFTY 23700PE 2026-09-15",
            "pnl": 1763.21,
        },
        {
            "id": "b",
            "entry_time": "2026-09-11 09:20:12",
            "exit_time": "2026-09-11 10:08:04",
            "trading_symbol": "NIFTY 23450PE 2026-09-15",
            "pnl": -2226.89,
        },
        {
            "id": "c",
            "entry_time": "2026-09-15 10:50:02",
            "exit_time": "2026-09-15 12:32:44",
            "trading_symbol": "NIFTY 23600PE 2026-09-15",
            "pnl": 3234.79,
        },
    ]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        with open(os.path.join(self.dir, "live_history_PE_NoTarget.json"), "w") as f:
            json.dump(self.TRADES[:2], f)
        state = {
            "session_date": "2026-09-16",
            "strategy": {"run_name": "PE_NoTarget", "legs": [{"option_type": "PE"}]},
            "positions": [],
            "closed_trades": [self.TRADES[2]],
            "daily_pnl": 0.0,
            "trades_today": 0,
            "banked_pnl": 2771.11,
        }
        with open(os.path.join(self.dir, "live_state_PE_NoTarget.json"), "w") as f:
            json.dump(state, f)
        self.real_now = live_mod._now_ist
        live_mod._now_ist = lambda: self.real_now().replace(year=2026, month=9, day=17, hour=0, minute=1)

    def tearDown(self):
        live_mod._now_ist = self.real_now
        self.tmp.cleanup()

    def _engine(self):
        engine = LiveEngine(object(), run_id="PE_NoTarget", state_dir=self.dir)
        engine._load_state()
        return engine

    def test_every_earlier_trade_is_on_the_book(self):
        ids = sorted(t["id"] for t in self._engine().closed_trades)
        self.assertEqual(ids, ["a", "b", "c"])

    def test_yesterdays_trades_are_written_to_the_permanent_record(self):
        self._engine()
        with open(os.path.join(self.dir, "live_history_PE_NoTarget.json")) as f:
            self.assertEqual(sorted(t["id"] for t in json.load(f)), ["a", "b", "c"])

    def test_the_new_day_starts_with_nothing_traded(self):
        engine = self._engine()
        self.assertEqual(engine.trades_today, 0)
        self.assertFalse([p for p in engine.positions if p.get("status") != "closed"])


if __name__ == "__main__":
    unittest.main()
