"""Telegram speaks for LIVE trades only.

Phil, 2026-10-27: "also no need to alert for paper trades.... Let only Live
have telegram alerts....".
"""

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402
from scalp import ScalpTrade  # noqa: E402

SRC = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")


def _req(mode):
    return app.ScalpEntryReq(
        underlying="NIFTY", strike=22800, option_type="PE", expiry="2026-10-27", lots=1, lot_size=65, mode=mode
    )


def _trade(mode):
    return ScalpTrade(
        trade_id=1, underlying="NIFTY", strike=22800, option_type="PE", expiry="2026-10-27",
        transaction_type="BUY", lots=1, lot_size=65, entry_premium=244.0, mode=mode,
    )  # fmt: skip


class TheScalpAlerts(unittest.TestCase):
    def _sent(self, mode):
        sent = []
        result = {"status": "ok", "trade_id": 1, "trade": {"entry_premium": 244.0}}
        with mock.patch.object(app.alerter, "alert", lambda *a, **k: sent.append(a[0])):
            app._alert_scalp_entry(_req(mode), result, "dhan")
        return sent

    def test_a_paper_entry_sends_nothing(self):
        self.assertEqual(self._sent("paper"), [])

    def test_a_live_entry_still_sends(self):
        self.assertEqual(self._sent("live"), ["Scalp Entry"])

    def test_a_paper_scalp_is_not_live(self):
        class Eng:
            open_trades = {1: _trade("paper")}

        self.assertFalse(app._scalp_trade_is_live(Eng(), 1))
        Eng.open_trades = {1: _trade("live")}
        self.assertTrue(app._scalp_trade_is_live(Eng(), 1))

    def test_a_paper_scalp_exit_is_not_announced(self):
        body = SRC.split("Telegram alert for every LIVE scalp exit")[1][:200]
        self.assertIn('!= "live"', body)


class ThePaperBooksAndBaskets(unittest.TestCase):
    def test_paper_books_send_no_trade_alerts(self):
        self.assertNotIn('_check_trade_alerts(run_id, "Paper"', SRC)

    def test_paper_engine_start_and_stop_are_quiet(self):
        self.assertNotIn("Mode: Paper", SRC)

    def test_option_builder_paper_is_quiet(self):
        self.assertNotIn('"Option Builder · Paper', SRC)
        self.assertIn('"Option Builder · LIVE basket"', SRC)


if __name__ == "__main__":
    unittest.main()
