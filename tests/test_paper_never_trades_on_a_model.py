"""Paper must refuse to trade on modelled prices, exactly where live refuses.

2026-09-29 09:20: Dhan answered 429, paper scanned "31 strikes (0 live LTPs,
31 estimated)", bought NIFTY 22900 PE at a MODELLED Rs 273, then read the real
price a second later — roughly Rs 57 lower — and booked STOP_LOSS exits of
Rs -14,954.88 and Rs -15,407.07 on two books. Nothing had moved. Live has refused
estimate-only scans since 2026-09-03; paper never got the rule.
"""

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from broker import dhan as dhan_mod  # noqa: E402
from engine.paper_trading import PaperPriceUnavailable, PaperTradingEngine  # noqa: E402
from tests.test_strike_scan_uses_real_prices import CountingClient  # noqa: E402


class PaperRefusesAModel(unittest.TestCase):
    STRIKE_IDS = {22850: "50001", 22900: "50002", 22950: "50003"}

    def _engine(self, client):
        engine = PaperTradingEngine.__new__(PaperTradingEngine)
        engine.dhan = client
        engine.logs = []
        engine.log_event = lambda kind, msg, *a, **k: engine.logs.append((kind, msg))
        return engine

    def setUp(self):
        dhan_mod._api_cache.clear()
        self._lookup = dhan_mod.ScripMaster.lookup
        dhan_mod.ScripMaster.lookup = staticmethod(
            lambda symbol, strike, expiry, option_type: self.STRIKE_IDS.get(int(strike))
        )

    def tearDown(self):
        dhan_mod.ScripMaster.lookup = self._lookup

    def test_a_429_with_no_live_price_anywhere_refuses(self):
        engine = self._engine(CountingClient(fail=True))
        with self.assertRaises(PaperPriceUnavailable):
            asyncio.run(engine._find_premium_strike("NIFTY", "2026-09-29", "PE", 250.0, 22900.0, 50, mode="near"))

    def test_a_partly_priced_chain_still_trades(self):
        """A model filling a gap in a real chain is fine; being the whole basis is not."""
        engine = self._engine(CountingClient(prices={50002: 263.0}))
        strike, premium = asyncio.run(
            engine._find_premium_strike("NIFTY", "2026-09-29", "PE", 250.0, 22900.0, 50, mode="near")
        )
        self.assertTrue(strike > 0)

    def test_the_entry_is_abandoned_not_booked_on_a_guess(self):
        src = (ROOT / "engine" / "paper_trading.py").read_text(encoding="utf-8")
        self.assertIn("except PaperPriceUnavailable as exc:", src)
        self.assertIn("Paper entry abandoned", src)
        self.assertNotIn("Using estimated premium", src, "the entry premium must never come from the model")


if __name__ == "__main__":
    unittest.main()


class TheTwoFakeTradesAreVoided(unittest.TestCase):
    """The exact records read off prod on 2026-09-29, and their neighbours."""

    REAL = [
        {
            "entry_time": "2026-09-29 09:20:01.819878",
            "strike": 22900,
            "option_type": "PE",
            "exit_reason": "STOP_LOSS",
            "pnl": -15407.07,
        },
        {
            "entry_time": "2026-09-29 09:20:01.003327",
            "strike": 22900,
            "option_type": "PE",
            "exit_reason": "STOP_LOSS",
            "pnl": -14954.88,
        },
    ]

    def test_both_are_voided(self):
        from engine.paper_trading import is_voided_paper_trade

        for t in self.REAL:
            self.assertTrue(is_voided_paper_trade(t), t)

    def test_nothing_else_is(self):
        from engine.paper_trading import is_voided_paper_trade

        near_misses = [
            {**self.REAL[0], "pnl": -15407.08},  # a paisa off
            {**self.REAL[0], "strike": 22950},  # another strike
            {**self.REAL[0], "option_type": "CE"},  # the other side
            {**self.REAL[0], "entry_time": "2026-09-30 09:20:01"},  # another day
            {"entry_time": "2026-03-19 09:15:04.699394", "strike": 23400, "option_type": "PE", "pnl": 6600.0},
        ]
        for t in near_misses:
            self.assertFalse(is_voided_paper_trade(t), t)


class TheLateEntryDayIsNoTrade(unittest.TestCase):
    """The 10:40 late entries on 29-Sep, read off prod: voided, and the day stays shut."""

    LATE = [
        {"entry_time": "2026-09-29 10:40:01.594877", "strike": 22850, "option_type": "PE", "pnl": -15190.07},
        {"entry_time": "2026-09-29 10:40:01.689839", "strike": 22850, "option_type": "PE", "pnl": -15008.72},
    ]

    def test_both_are_voided_and_close_the_day(self):
        from engine.paper_trading import is_voided_paper_trade, voided_trade_closes_day

        for t in self.LATE:
            self.assertTrue(is_voided_paper_trade(t), t)
            self.assertTrue(voided_trade_closes_day(t), t)

    def test_the_0920_voids_do_not_close_the_day(self):
        from engine.paper_trading import voided_trade_closes_day

        for t in TheTwoFakeTradesAreVoided.REAL:
            self.assertFalse(voided_trade_closes_day(t), t)

    def test_a_restart_keeps_the_day_shut(self):
        import json
        import os
        import tempfile
        from unittest import mock

        import engine.paper_trading as pt

        state = {
            "session_date": "2026-09-29",
            "positions": [],
            "closed_trades": [self.LATE[0]],
            "trades_today": 1,
            "daily_pnl": -15190.07,
            "event_log": [],
        }
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "paper_state_x.json")
            with open(path, "w") as f:
                json.dump(state, f)
            eng = pt.PaperTradingEngine.__new__(pt.PaperTradingEngine)
            eng._state_file = path
            eng._load_trade_history = lambda: []
            eng.event_log = []
            eng.strategy = {}
            eng.entry_conditions = []
            eng.exit_conditions = []
            eng.execution_profile = None
            eng.profit_cooldown_trigger_date = None
            eng.log_event = lambda kind, msg, *a, **k: eng.event_log.append({"type": kind, "message": msg})
            eng._is_intraday_product = lambda s: True
            with mock.patch.object(pt, "_now_ist", return_value=pt.datetime(2026, 9, 29, 11, 30)):
                eng._load_state()
        self.assertEqual(eng.closed_trades, [])
        self.assertEqual(round(eng.daily_pnl, 2), 0.0)
        self.assertEqual(eng.trades_today, 1, "the slot stays used: no later entry today")
        self.assertIn(pt.NO_TRADE_DAY_NOTE, [e["message"] for e in eng.event_log])
