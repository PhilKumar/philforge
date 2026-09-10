"""A day the broker cannot close yet must not read as zero.

2026-09-10: the PE book bought 130 of NIFTY 23700 PE at Rs 265.00 and Dhan
squared it off at Rs 279.45 — a real +Rs 1,878.50. The Closed trades table
showed:

    2026-09-10  NIFTY-Sep2026-23700-PE  qty 0  in 265.00  out 0.00  ₹0*

because the row was written from the broker's trade book while it still held
only the two BUY fills, and nothing rewrote it: the backfill deliberately skips
today, and `_persist_daily_trades` only runs when somebody opens the trades page.

So two rules. A stored row with a buy and no sell is always worth replacing.
And when the broker still cannot complete the day, the engines' own P&L stands
in — tagged provisional, so the settled figures replace it tomorrow.
"""

import unittest
from pathlib import Path

import app

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "app.py").read_text(encoding="utf-8")


def _detail(buy, sell):
    return {"symbol": "NIFTY-Sep2026-23700-PE", "buy_avg": buy, "sell_avg": sell}


class AnUnclosedDayIsRecognised(unittest.TestCase):
    def test_a_buy_with_no_sell_is_incomplete(self):
        self.assertTrue(app._today_row_is_incomplete({"details": [_detail(265.0, 0.0)]}))

    def test_a_closed_round_trip_is_complete(self):
        self.assertFalse(app._today_row_is_incomplete({"details": [_detail(265.0, 279.45)]}))

    def test_one_unclosed_contract_among_several_still_counts(self):
        entry = {"details": [_detail(265.0, 279.45), _detail(112.0, 0.0)]}
        self.assertTrue(app._today_row_is_incomplete(entry))

    def test_nothing_at_all_is_incomplete(self):
        self.assertTrue(app._today_row_is_incomplete(None))
        self.assertTrue(app._today_row_is_incomplete({}))

    def test_a_day_with_no_buys_is_not_called_incomplete(self):
        """A sold-only day is a short. Not this test's business, and not a zero."""
        self.assertFalse(app._today_row_is_incomplete({"details": [_detail(0.0, 279.45)]}))


class _Engine:
    def __init__(self, trades):
        self.closed_trades = trades


class TheEnginesFillTheGap(unittest.TestCase):
    def setUp(self):
        self._saved = dict(app.live_engines)
        app.live_engines.clear()

    def tearDown(self):
        app.live_engines.clear()
        app.live_engines.update(self._saved)

    def _install(self, trades, user_id=1):
        app._registry_bucket(app.live_engines, user_id)["PE_NoTarget"] = _Engine(trades)

    def test_the_days_real_pnl_comes_back(self):
        self._install(
            [
                {
                    "trading_symbol": "NIFTY 23700PE 2026-09-15",
                    "entry_time": "2026-09-10 09:25:16",
                    "exit_time": "2026-09-10 15:26:36",
                    "entry_premium": 265.0,
                    "exit_premium": 279.45,
                    "quantity": 130,
                    "pnl": 1798.5,
                    "charges": 80.0,
                }
            ]
        )
        summary = app._engine_day_summary(1, "2026-09-10")
        self.assertIsNotNone(summary)
        self.assertEqual(summary["net_pnl"], 1798.5)
        self.assertEqual(summary["pnl"], 1878.5)  # gross, before the Rs 80 of charges
        self.assertEqual(summary["trades"], 1)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["details"][0]["sell_avg"], 279.45)

    def test_it_stays_a_provisional_row(self):
        """Tagged `live_day_fifo` on purpose: the settled figures must still
        replace it, and the duplicate-day sweep must still be able to drop it."""
        self._install(
            [
                {
                    "trading_symbol": "NIFTY 23700PE 2026-09-15",
                    "exit_time": "2026-09-10 15:26:36",
                    "entry_premium": 265.0,
                    "exit_premium": 279.45,
                    "quantity": 130,
                    "pnl": 1798.5,
                    "charges": 80.0,
                }
            ]
        )
        summary = app._engine_day_summary(1, "2026-09-10")
        self.assertEqual(summary["source"], "live_day_fifo")
        self.assertEqual(summary["basis"], "engine")
        self.assertEqual(summary["schema_version"], app._TRADE_HISTORY_SCHEMA_VERSION)

    def test_another_days_trades_are_not_borrowed(self):
        self._install(
            [
                {
                    "trading_symbol": "NIFTY 23950PE 2026-09-08",
                    "exit_time": "2026-09-08 10:15:00",
                    "entry_premium": 250.53,
                    "exit_premium": 229.45,
                    "quantity": 130,
                    "pnl": -2739.75,
                    "charges": 80.0,
                }
            ]
        )
        self.assertIsNone(app._engine_day_summary(1, "2026-09-10"))

    def test_no_engines_means_no_claim(self):
        self.assertIsNone(app._engine_day_summary(1, "2026-09-10"))

    def test_a_losing_day_is_not_counted_as_a_win(self):
        self._install(
            [
                {
                    "trading_symbol": "NIFTY 23950PE 2026-09-08",
                    "exit_time": "2026-09-10 10:15:00",
                    "entry_premium": 250.53,
                    "exit_premium": 229.45,
                    "quantity": 130,
                    "pnl": -2739.75,
                    "charges": 119.43,
                }
            ]
        )
        summary = app._engine_day_summary(1, "2026-09-10")
        self.assertEqual(summary["wins"], 0)
        self.assertEqual(summary["net_pnl"], -2739.75)


class ThePersistPathUsesIt(unittest.TestCase):
    def test_an_incomplete_stored_row_is_always_replaced(self):
        body = SRC.split("async def _persist_daily_trades")[1].split("\nasync def ")[0]
        self.assertIn("_today_row_is_incomplete(existing)", body)
        self.assertIn("_engine_day_summary", body)

    def test_today_is_refreshed_at_startup_not_only_on_a_page_view(self):
        """The backfill skips today by design, so without this the row is
        written once and left alone however wrong it turns out to be."""
        self.assertIn("Could not refresh today from the broker", SRC)


class TheFlaggedEngineIsAskedNotAssumed(unittest.TestCase):
    def test_the_restore_reconciles_before_it_gives_up(self):
        body = SRC.split("async def _restore_live_engines")[1].split("\nasync def ")[0]
        self.assertIn("needs_reconcile", body)
        self.assertIn("_reconcile_flagged_engine", body)

    def test_an_unreachable_broker_keeps_the_book_shut(self):
        """Unknown is not flat. Starting a book that might hold a real position
        is the one outcome worse than staying off the page."""
        body = SRC.split("async def _reconcile_flagged_engine")[1].split("\nasync def ")[0]
        self.assertIn("return True  # unknown is not the same as flat", body)

    def test_a_still_open_position_keeps_the_flag(self):
        body = SRC.split("async def _reconcile_flagged_engine")[1].split("\nasync def ")[0]
        open_check = body.index('p.get("status") != "closed"')
        cleared = body.index("manual_intervention_required = False")
        self.assertLess(open_check, cleared)


if __name__ == "__main__":
    unittest.main()
