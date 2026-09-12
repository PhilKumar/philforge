"""High Entry's history outlives Remove, and the replay window.

Phil, 2026-09-09, looking at the Book monitor: "The closed paper campaigns are
not populated or it is not permanent.. It gets removed when I click the remove
from the Book Monitor page."

Both halves of that were true. This strategy kept no record of its own: the
book is replayed from the mother dates on every poll and rendered straight into
the page, so Remove -- which drops the mother -- deleted the campaign's whole
history, and a mother ageing past the replay window did the same thing without
saying anything. Every other strategy on that page has archived its finished
campaigns to `paper_campaigns` since 25 August.

It now archives too, on every poll that changes something AND before a drop can
throw the campaign away, and the closed table reads the archive through the
same renderer the other four use.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PY = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
APP_JS = open(os.path.join(ROOT, "static", "philforge-app.js"), encoding="utf-8").read()

import app as _app_probe  # noqa: E402,F401  (import-time syntax guard)
from app import _recovery_campaign_row  # noqa: E402

MOTHER = "2026-09-08T09:15:00+05:30"


def _campaign(status="RECOVERED", trades=None, end_reason="target"):
    return {
        "campaign_id": "c1",
        "status": status,
        "end_reason": end_reason,
        "side": "CE",
        "timeframe": "5m",
        "mother": {"timestamp": MOTHER, "high": 23758.95, "low": 23600.0},
        "trades": trades
        if trades is not None
        else [
            {
                "entry_time": "2026-09-08T09:35:00+05:30",
                "exit_time": "2026-09-08T09:55:00+05:30",
                "strike": 23600,
                "side": "CE",
                "lots": 1,
                "quantity": 65,
                "entry_premium": 345.90,
                "exit_premium": 335.10,
                "exit_reason": "stop",
                "net_pnl": -702.0,
                "costs": 40.0,
            },
            {
                "entry_time": "2026-09-08T10:05:00+05:30",
                "exit_time": "2026-09-08T10:25:00+05:30",
                "strike": 23550,
                "side": "CE",
                "lots": 2,
                "quantity": 130,
                "entry_premium": 370.95,
                "exit_premium": 384.50,
                "exit_reason": "target",
                "net_pnl": 1531.0,
                "costs": 45.0,
            },
        ],
    }


class OnlyAFinishedCampaignIsArchived(unittest.TestCase):
    def test_a_running_campaign_is_not_written(self):
        for state in ("WATCHING", "ARMED", "IN_TRADE", "AWAIT_LOW", "ZONES"):
            with self.subTest(state=state):
                self.assertIsNone(_recovery_campaign_row(_campaign(status=state), "NIFTY"))

    def test_each_terminal_state_is_written(self):
        for state in ("RECOVERED", "ABANDONED", "ENDED"):
            with self.subTest(state=state):
                self.assertIsNotNone(_recovery_campaign_row(_campaign(status=state), "NIFTY"))


class TheRowSaysWhatHappened(unittest.TestCase):
    def setUp(self):
        self.row = _recovery_campaign_row(_campaign(), "NIFTY")

    def test_it_is_keyed_on_the_mother_so_it_cannot_double_up(self):
        self.assertEqual(self.row["campaign_key"], MOTHER)

    def test_the_money_adds_up(self):
        self.assertEqual(self.row["net_pnl"], 829.0)  # -702 + 1531
        self.assertEqual(self.row["buys"], 2)
        self.assertEqual(self.row["deployed_inr"], round(345.90 * 65 + 370.95 * 130, 2))

    def test_it_carries_the_contract_it_ended_on(self):
        self.assertEqual(self.row["contract"], "23550 CE")

    def test_it_spans_the_first_entry_to_the_last_exit(self):
        self.assertTrue(self.row["opened_at"].startswith("2026-09-08T09:35"))
        self.assertTrue(self.row["closed_at"].startswith("2026-09-08T10:25"))

    def test_an_unpriced_leg_makes_the_total_unknown_not_zero(self):
        """A leg with no premium is not a flat leg, and summing the ones that
        did price is how five stops once read as +Rs 0."""
        trades = _campaign()["trades"]
        trades[0]["net_pnl"] = None
        row = _recovery_campaign_row(_campaign(trades=trades), "NIFTY")
        self.assertIsNone(row["net_pnl"])
        self.assertEqual(row["payload"]["unpriced_legs"], 1)

    def test_a_mother_that_never_bought_is_still_recorded(self):
        row = _recovery_campaign_row(_campaign(trades=[], end_reason=None), "NIFTY")
        self.assertEqual(row["buys"], 0)
        self.assertEqual(row["exit_reason"], "no_buy")

    def test_the_trades_are_kept_for_the_record(self):
        self.assertEqual(len(self.row["payload"]["trades"]), 2)


class TheArchiveIsWrittenBeforeItCanBeLost(unittest.TestCase):
    def test_remove_archives_first(self):
        """The book replays from the mothers, so dropping one deletes the
        campaign everywhere. Anything not written before that point is gone."""
        handler = APP_PY.split('@app.post("/api/recovery/paper/drop")')[1].split("@app.post")[0]
        self.assertIn("_archive_recovery_campaigns", handler)
        self.assertLess(
            handler.index("_archive_recovery_campaigns"),
            handler.index("runtime.host.drop("),
            "the drop happens before the archive — the campaign would be lost",
        )

    def test_the_poll_loop_archives_as_it_goes(self):
        loop = APP_PY.split("async def _run_recovery_loop(")[1].split("\n\n\n")[0]
        self.assertIn("_archive_recovery_campaigns", loop)

    def test_status_reconciles_a_saved_terminal_campaign_after_restart(self):
        """A snapshot can survive a restart between save and the loop's write."""
        handler = APP_PY.split('@app.get("/api/recovery/paper/status")')[1].split("@app.post")[0]
        self.assertIn("_archive_recovery_campaigns", handler)
        self.assertLess(handler.index("_archive_recovery_campaigns"), handler.index("_recovery_status_payload"))

    def test_a_bad_campaign_cannot_stop_the_rest(self):
        fn = APP_PY.split("async def _archive_recovery_campaigns(")[1].split("\n\n\n")[0]
        self.assertIn("except Exception", fn)
        self.assertIn("continue", fn)

    def test_the_ledger_endpoint_serves_the_strategy(self):
        line = [x for x in APP_PY.splitlines() if x.startswith("_PAPER_LEDGER_STRATEGIES")][0]
        self.assertIn("candle_recovery", line)


class ThePageReadsTheArchive(unittest.TestCase):
    def test_the_closed_table_uses_the_shared_renderer(self):
        self.assertIn("candle_recovery: { wrap: 'oc-high-closed'", APP_JS)
        self.assertIn("_refreshPaperLedger('candle_recovery')", APP_JS)

    def test_the_old_live_book_table_is_gone(self):
        body = APP_JS.split("function renderRecovery(data)")[1].split("\n}")[0]
        self.assertNotIn("closedRows.innerHTML", body, "the table still renders from the live book")

    def test_history_still_loads_when_the_run_is_stopped(self):
        """A stopped run with no live campaign is when the history matters most."""
        body = APP_JS.split("function renderRecovery(data)")[1].split("if (!running && !campaigns.length)")[1][:600]
        self.assertIn("_refreshPaperLedger('candle_recovery')", body)


if __name__ == "__main__":
    unittest.main()
