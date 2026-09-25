"""Faults must be found at 09:10 with nothing at stake, not at 10:00 with money in.

25-Sep-2026, Phil: "Then daily I am catching a bug on your code even on the
live trades."

That is the complaint the watchdog does not answer. A watchdog reports faster;
it does not find things EARLIER. Every fault he has caught himself this week was
knowable before the first candle: a book whose session_date never rolled, a book
still holding yesterday's position, an engine registered but not running, an
indicator series that had not carried across the session break.

So the books are inspected at 09:10 and the result is sent either way. A silent
morning must never be indistinguishable from a broken check — that confusion is
what this entire day was about.
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_mod  # noqa: E402


class _Engine:
    def __init__(self, name, **kw):
        self.strategy = {"name": name}
        self.running = kw.get("running", True)
        self.session_date = kw.get("session_date", date(2026, 9, 25))
        self.in_trade = kw.get("in_trade", False)
        self.manual_intervention_required = kw.get("manual_intervention_required", False)
        self.indicator_divergence = kw.get("indicator_divergence", None)
        self.dhan = kw.get("dhan", object())


class TheBooksAreCheckedBeforeTheOpen(unittest.TestCase):
    TODAY = date(2026, 9, 25)

    def tearDown(self):
        app_mod.live_engines.clear()

    def test_a_ready_book_has_no_faults(self):
        self.assertEqual(app_mod._pre_open_faults(_Engine("PE_NoTarget"), self.TODAY), [])

    def test_a_stale_session_date_is_caught(self):
        """The known killer: an after-midnight restart leaves yesterday's date
        and the book skips the entire day without a word."""
        faults = app_mod._pre_open_faults(_Engine("PE", session_date=date(2026, 9, 24)), self.TODAY)
        self.assertTrue(any("session_date" in f for f in faults), faults)

    def test_a_book_left_in_trade_overnight_is_caught(self):
        faults = app_mod._pre_open_faults(_Engine("PE", in_trade=True), self.TODAY)
        self.assertTrue(any("in-trade" in f for f in faults), faults)

    def test_a_registered_but_stopped_book_is_caught(self):
        faults = app_mod._pre_open_faults(_Engine("PE", running=False), self.TODAY)
        self.assertTrue(any("not running" in f for f in faults), faults)

    def test_yesterdays_indicator_divergence_is_carried_into_the_morning(self):
        engine = _Engine("PE", indicator_divergence={"columns": {"EMA_20_5m": {}}})
        faults = app_mod._pre_open_faults(engine, self.TODAY)
        self.assertTrue(any("EMA_20_5m" in f for f in faults), faults)

    def test_a_missing_broker_is_caught(self):
        faults = app_mod._pre_open_faults(_Engine("PE", dhan=None), self.TODAY)
        self.assertTrue(any("broker" in f for f in faults), faults)

    def test_the_report_says_ready_when_every_book_is_ready(self):
        app_mod.live_engines[1]["a"] = _Engine("PE_NoTarget")
        app_mod.live_engines[1]["b"] = _Engine("CE_SL15")
        title, body, faulty = app_mod._pre_open_report(self.TODAY)
        self.assertEqual(faulty, 0)
        self.assertIn("ready", title.lower())
        self.assertIn("PE_NoTarget", body)
        self.assertIn("CE_SL15", body)

    def test_the_report_names_the_broken_book_and_the_reason(self):
        app_mod.live_engines[1]["a"] = _Engine("PE_NoTarget", session_date=date(2026, 9, 24))
        app_mod.live_engines[1]["b"] = _Engine("CE_SL15")
        title, body, faulty = app_mod._pre_open_report(self.TODAY)
        self.assertEqual(faulty, 1)
        self.assertIn("NOT ready", title)
        self.assertIn("PE_NoTarget", body)
        self.assertIn("2026-09-24", body)

    def test_no_book_at_all_is_itself_a_fault(self):
        """Silence must never be the same as health — the whole point."""
        title, _body, faulty = app_mod._pre_open_report(self.TODAY)
        self.assertEqual(faulty, 1)
        self.assertIn("No live book", title)

    def test_the_check_is_registered_so_a_deploy_brings_it_back(self):
        src = open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"), encoding="utf-8"
        ).read()
        block = src.split("def _ensure_auto_loops_running")[1].split("started: list[str] = []")[0]
        self.assertIn("_run_pre_open_check_loop", block)

    def test_it_runs_before_the_market_opens(self):
        self.assertLess(app_mod._PRE_OPEN_CHECK_AT.hour * 60 + app_mod._PRE_OPEN_CHECK_AT.minute, 9 * 60 + 15)


if __name__ == "__main__":
    unittest.main()
