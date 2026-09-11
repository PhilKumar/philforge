"""Gap Carry's campaign events must outlive the campaign that noted them.

Phil, 2026-09-11: "Where are the other old events? Only yesterday's update is
there ... Even though I ran it on paper I want these old events".

Gap Carry has always archived the NIGHTS it settles, which is why "Closed paper
nights" survived the flip from paper to live on 2026-09-09. Its EVENTS lived
only on the running campaign, capped at 20 in the saved state and 8 on the page,
and a fresh campaign starts with none — so ten sessions of readings became one.
They were never lost from disk: a backup taken that morning still held all ten.
"""

import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app

JS = (Path(__file__).resolve().parent.parent / "static" / "philforge-app.js").read_text(encoding="utf-8")

PAPER_ERA = [
    "2026-08-26: close below EMA20, but RSI 48.2 has not reached 30",
    "2026-08-27: close below EMA20, but RSI 39.7 has not reached 30",
    "2026-09-08: close below EMA20, but RSI 43.3 has not reached 30",
]
LIVE_ERA = ["2026-09-10: close below EMA20, but RSI 33.6 has not reached 30"]


class TheMergeKeepsEverything(unittest.TestCase):
    def test_old_and_new_come_out_together_oldest_first(self):
        merged = app._merge_gap_carry_events(LIVE_ERA, PAPER_ERA, "2026-09-11")
        self.assertEqual(merged, PAPER_ERA + LIVE_ERA)

    def test_saving_the_same_campaign_again_adds_nothing(self):
        once = app._merge_gap_carry_events([], PAPER_ERA, "2026-09-11")
        self.assertEqual(app._merge_gap_carry_events(once, PAPER_ERA, "2026-09-11"), once)

    def test_an_undated_note_gets_the_day_it_was_kept(self):
        """A broker refusal or a bracket-leg close has no session prefix; without
        one it could neither be ordered nor shown under a session."""
        merged = app._merge_gap_carry_events([], ["broker rejected the exit; it will be tried again"], "2026-09-11")
        self.assertEqual(merged, ["2026-09-11: broker rejected the exit; it will be tried again"])

    def test_one_sessions_events_keep_their_order(self):
        day = ["2026-09-09: signal fired", "2026-09-09: compounding — 2 lots instead of 1"]
        self.assertEqual(app._merge_gap_carry_events([], day, "2026-09-11"), day)

    def test_blank_and_missing_notes_are_ignored(self):
        self.assertEqual(app._merge_gap_carry_events(None, ["", None, "  "], "2026-09-11"), [])

    def test_it_is_bounded(self):
        many = [f"2026-01-01: n{i}" for i in range(app._GAP_CARRY_EVENTS_KEEP + 50)]
        self.assertEqual(len(app._merge_gap_carry_events([], many, "2026-09-11")), app._GAP_CARRY_EVENTS_KEEP)


def _backup_db(path, notes, user_id=1):
    con = sqlite3.connect(path)
    con.execute("create table app_state (key text primary key, value text)")
    con.execute(
        "insert into app_state values (?, ?)",
        (f"gap_carry_open:{user_id}", json.dumps({"engine": {"notes": notes}, "running": False})),
    )
    con.commit()
    con.close()


class TheBackupsGiveThemBack(unittest.TestCase):
    def test_a_raw_backup_beside_the_database_is_read(self):
        with tempfile.TemporaryDirectory() as folder:
            live = os.path.join(folder, "philforge.db")
            _backup_db(live, LIVE_ERA)
            _backup_db(os.path.join(folder, "philforge-backup-before-ladder-20260909-080248.db"), PAPER_ERA)
            with patch.object(app.config, "DB_PATH", live):
                self.assertEqual(app._recover_gap_carry_events_from_backups(1), PAPER_ERA)

    def test_the_live_database_is_not_counted_as_a_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            live = os.path.join(folder, "philforge.db")
            _backup_db(live, LIVE_ERA)
            with patch.object(app.config, "DB_PATH", live):
                self.assertEqual(app._recover_gap_carry_events_from_backups(1), [])

    def test_it_never_opens_a_tarball(self):
        """A 1 GB archive on the 1 GB box that runs the live books."""
        with tempfile.TemporaryDirectory() as folder:
            live = os.path.join(folder, "philforge.db")
            _backup_db(live, [])
            Path(folder, "philforge-backup-20260910.tar.gz").write_bytes(b"not read")
            with patch.object(app.config, "DB_PATH", live):
                self.assertEqual(app._recover_gap_carry_events_from_backups(1), [])

    def test_a_file_that_is_not_a_database_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as folder:
            live = os.path.join(folder, "philforge.db")
            _backup_db(live, [])
            Path(folder, "junk.db").write_bytes(b"definitely not sqlite")
            _backup_db(os.path.join(folder, "good.db"), PAPER_ERA)
            with patch.object(app.config, "DB_PATH", live):
                self.assertEqual(app._recover_gap_carry_events_from_backups(1), PAPER_ERA)

    def test_another_users_events_are_not_taken(self):
        with tempfile.TemporaryDirectory() as folder:
            live = os.path.join(folder, "philforge.db")
            _backup_db(live, [])
            _backup_db(os.path.join(folder, "old.db"), PAPER_ERA, user_id=2)
            with patch.object(app.config, "DB_PATH", live):
                self.assertEqual(app._recover_gap_carry_events_from_backups(1), [])

    def test_backups_are_opened_read_only(self):
        import inspect

        self.assertIn("mode=ro", inspect.getsource(app._gap_carry_notes_in_backup))

    def test_an_unreadable_backup_is_said_not_swallowed(self):
        """A silent skip makes "none recovered" look like "none to recover"."""
        import inspect

        src = inspect.getsource(app._gap_carry_notes_in_backup)
        self.assertIn("_logger.info", src)


class ThePageGetsTheWholeHistory(unittest.TestCase):
    def test_the_log_comes_newest_first_with_the_running_campaigns_notes(self):
        store = {"gap_carry_events:1": json.dumps(PAPER_ERA)}

        async def get_state(key):
            return store.get(key)

        async def set_state(key, value):
            store[key] = value

        class _Engine:
            notes = LIVE_ERA

        class _Runtime:
            engine = _Engine()

        with (
            patch.object(app._db_mod, "get_app_state", get_state),
            patch.object(app._db_mod, "set_app_state", set_state),
            patch.dict(app._gap_carry_engines, {1: _Runtime()}, clear=False),
        ):
            log = asyncio.run(app._gap_carry_event_log(1))
        self.assertEqual(log[0], LIVE_ERA[0])
        self.assertEqual(log[-1], PAPER_ERA[0])
        self.assertEqual(len(log), 4)

    def test_the_status_sends_it_even_with_no_campaign(self):
        """At the function's own indentation — never inside `if runtime`."""
        import inspect

        lines = inspect.getsource(app.gap_carry_paper_status).splitlines()
        assign = [ln for ln in lines if 'body["event_log"]' in ln]
        self.assertEqual(len(assign), 1)
        self.assertTrue(assign[0].startswith("    body["), assign[0])
        self.assertFalse(assign[0].startswith("        "), "it sits inside a branch")

    def test_the_poll_ships_a_year_not_the_archive(self):
        import inspect

        self.assertIn("event_log[:250]", inspect.getsource(app.gap_carry_paper_status))


class ThePanelDrawsItOnItsOwn(unittest.TestCase):
    def test_events_are_drawn_outside_the_campaign_render(self):
        """`_renderGapCarryStatus` returns early with no campaign running."""
        status = JS.split("function _renderGapCarryStatus(")[1].split("\nfunction ")[0]
        self.assertNotIn("oc-gap-events", status)
        poll = JS.split("async function refreshGapCarryStatus")[1].split("\nfunction ")[0]
        self.assertIn("_renderGapCarryEvents(data)", poll)

    def test_ten_to_a_page(self):
        self.assertIn("const _GC_EVENTS_PAGE = 10;", JS)


if __name__ == "__main__":
    unittest.main()
