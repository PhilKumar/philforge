"""A live book's record must outlive the session it was made in.

On 2026-09-10 the Live page showed CE_SL15_NoMonTue with "0 trades" and ₹0.00.
That book had traded a real CE on 2026-09-03 — bought, exited at 10:45, booked.
Nothing was wrong with the trade; the record was simply never kept. `_load_state`
drops everything from a previous session ("Stale state ... ignoring"), so a live
book emptied at every rollover and at every deploy that crossed one.

The paper engines have had a cumulative history file since the beginning, which
is exactly why the paper tabs show eleven trades and the live tabs showed none.
This gives the live engine the same file.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from engine.live import LiveEngine

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "engine" / "live.py").read_text(encoding="utf-8")


def _trade(entry_time, strike, pnl, option_type="CE"):
    return {
        "entry_time": entry_time,
        "exit_time": entry_time,
        "strike": strike,
        "option_type": option_type,
        "trading_symbol": f"NIFTY {strike}{option_type}",
        "entry_premium": 294.73,
        "exit_premium": 282.38,
        "quantity": 130,
        "pnl": pnl,
    }


class _Book(LiveEngine):
    def __init__(self, directory):
        self._history_file = str(Path(directory) / "live_history_CE.json")


class TheRecordSurvives(unittest.TestCase):
    def test_a_trade_written_once_comes_back(self):
        with tempfile.TemporaryDirectory() as d:
            book = _Book(d)
            book._save_trade_history([_trade("2026-09-03 09:20", 23750, -1605.5)])
            self.assertEqual(len(book._load_trade_history()), 1)
            self.assertEqual(book._load_trade_history()[0]["strike"], 23750)

    def test_saving_the_same_session_twice_does_not_double_it(self):
        with tempfile.TemporaryDirectory() as d:
            book = _Book(d)
            trades = [_trade("2026-09-03 09:20", 23750, -1605.5)]
            book._save_trade_history(trades)
            book._save_trade_history(trades)
            self.assertEqual(len(book._load_trade_history()), 1)

    def test_different_days_accumulate(self):
        with tempfile.TemporaryDirectory() as d:
            book = _Book(d)
            book._save_trade_history([_trade("2026-09-03 09:20", 23750, -1605.5)])
            book._save_trade_history([_trade("2026-09-08 09:20", 23950, -2739.75)])
            self.assertEqual(len(book._load_trade_history()), 2)

    def test_the_same_strike_on_two_days_is_two_trades(self):
        """Deduplicating on strike alone would have eaten one of them."""
        with tempfile.TemporaryDirectory() as d:
            book = _Book(d)
            book._save_trade_history([_trade("2026-09-03 09:20", 23750, -1605.5)])
            book._save_trade_history([_trade("2026-09-10 09:25", 23750, 1878.5)])
            self.assertEqual(len(book._load_trade_history()), 2)

    def test_a_missing_file_is_an_empty_record_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_Book(d)._load_trade_history(), [])

    def test_a_corrupt_file_does_not_stop_the_engine(self):
        with tempfile.TemporaryDirectory() as d:
            book = _Book(d)
            Path(book._history_file).write_text("{ not json")
            self.assertEqual(book._load_trade_history(), [])

    def test_a_file_holding_the_wrong_shape_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            book = _Book(d)
            Path(book._history_file).write_text(json.dumps({"trades": []}))
            self.assertEqual(book._load_trade_history(), [])


class ItIsWiredIntoTheEngine(unittest.TestCase):
    def test_every_live_book_gets_its_own_file(self):
        body = SRC.split("def __init__")[1].split("\n    def ")[0]
        self.assertIn("live_history_{safe_id}.json", body)
        self.assertIn('"live_history.json"', body)

    def test_a_closed_trade_is_written_the_moment_it_closes(self):
        """Waiting for the rollover means a process that dies overnight takes
        the day's record with it."""
        body = SRC.split("async def _record_closed_trade")[1].split("\n    def ")[0]
        self.assertIn("_save_trade_history([closed_trade])", body)

    def test_the_rollover_keeps_the_record_instead_of_dropping_it(self):
        body = SRC.split("def _load_state")[1].split("\n    def ")[0]
        stale = body.index("Stale state from")
        self.assertIn("_save_trade_history(stale_trades)", body[:stale])
        self.assertIn("self.closed_trades = self._load_trade_history()", body[:stale])

    def test_the_ladder_still_sizes_on_banked_money_not_on_this_list(self):
        """Loading a book's whole history into `closed_trades` must not change
        what it buys. The compounding rung reads `banked_pnl`, and that is the
        only thing standing between this change and a book that sizes up on
        five years of trades the moment it restarts."""
        entry = SRC.split("async def _enter_trade")[1].split("\n    def ")[0]
        ladder = entry[entry.index("compound_step_pct") : entry.index("quantity = lots * lot_size")]
        self.assertIn("self.banked_pnl", ladder)
        self.assertNotIn("closed_trades", ladder)


class TheStatusShowsIt(unittest.TestCase):
    def test_a_restored_book_reports_its_historical_trades(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as d:
                book = _Book(d)
                book._save_trade_history(
                    [_trade("2026-09-03 09:20", 23750, -1605.5), _trade("2026-09-08 09:20", 23950, -2739.75)]
                )
                book.closed_trades = book._load_trade_history()
                self.assertEqual(len(book.closed_trades), 2)
                self.assertEqual(round(sum(t["pnl"] for t in book.closed_trades), 2), -4345.25)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
