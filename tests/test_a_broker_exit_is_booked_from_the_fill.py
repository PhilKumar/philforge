"""Book a broker-made exit at the price and the time the broker actually got.

The Completed Trades row for 2026-09-10 read:

    10-09-2026  NIFTY 23700PE  09:25:09 → 19:32:04  BUY  ₹265.00  ₹265.00
                qty 2  -₹112.94  BROKER_MANUAL_EXIT

Three things wrong in one row. The exit price was the ENTRY price (`buyAvg`
read as the exit of a long). The P&L was a +₹1,763 trade written down as its own
charges. And the exit time was 19:32:04 — the moment a deploy happened to
reconcile it — when Dhan actually sold at 15:26:36, four hours earlier.

A position row carries averages and no time at all. The TRADE BOOK carries the
fill itself, with its timestamp. So that is what a broker-made close is booked
from, and the position row is only the fallback.

The rows already written are repriced once, at start-up, from the same source.
"""

import asyncio
import unittest
from datetime import datetime

from engine.live import LiveEngine

FILLS = [
    {
        "securityId": "47306",
        "transactionType": "BUY",
        "tradedQuantity": 65,
        "tradedPrice": 265.0,
        "exchangeTime": "2026-09-10 09:25:16",
    },
    {
        "securityId": "47306",
        "transactionType": "SELL",
        "tradedQuantity": 65,
        "tradedPrice": 279.45,
        "exchangeTime": "2026-09-10 15:26:36",
    },
]


class _Dhan:
    def __init__(self, fills=None, boom=False):
        self._fills = FILLS if fills is None else fills
        self._boom = boom

    def get_trades(self):
        if self._boom:
            raise RuntimeError("Dhan API 500")
        return self._fills


def _engine(dhan=None):
    engine = LiveEngine.__new__(LiveEngine)
    engine.dhan = dhan if dhan is not None else _Dhan()
    engine.logs = []
    engine.log_event = lambda t, m, d=None: engine.logs.append((t, m))
    return engine


def _pos():
    return {
        "security_id": "47306",
        "transaction_type": "BUY",
        "entry_premium": 265.0,
        "trading_symbol": "NIFTY 23700PE 2026-09-15",
    }


class TheFillIsRead(unittest.TestCase):
    def test_the_price_and_the_time_both_come_from_the_trade_book(self):
        price, stamp = asyncio.run(_engine()._broker_closing_fill(_pos()))
        self.assertEqual(price, 279.45)
        self.assertEqual(stamp, datetime(2026, 9, 10, 15, 26, 36))

    def test_the_entry_fill_is_not_mistaken_for_the_exit(self):
        """Both sides are in the book. A long is closed by the SELL."""
        price, _ = asyncio.run(_engine()._broker_closing_fill(_pos()))
        self.assertNotEqual(price, 265.0)

    def test_a_short_is_closed_by_the_buy(self):
        pos = {**_pos(), "transaction_type": "SELL"}
        price, stamp = asyncio.run(_engine()._broker_closing_fill(pos))
        self.assertEqual(price, 265.0)
        self.assertEqual(stamp, datetime(2026, 9, 10, 9, 25, 16))

    def test_the_last_closing_fill_wins(self):
        """A position closed in pieces is flattened by the final one."""
        fills = FILLS + [
            {
                "securityId": "47306",
                "transactionType": "SELL",
                "tradedPrice": 281.0,
                "exchangeTime": "2026-09-10 15:26:40",
            }
        ]
        price, stamp = asyncio.run(_engine(_Dhan(fills))._broker_closing_fill(_pos()))
        self.assertEqual(price, 281.0)
        self.assertEqual(stamp, datetime(2026, 9, 10, 15, 26, 40))

    def test_another_contracts_fills_are_ignored(self):
        fills = [{**FILLS[1], "securityId": "99999", "tradedPrice": 999.0}]
        price, _ = asyncio.run(_engine(_Dhan(fills))._broker_closing_fill(_pos()))
        self.assertEqual(price, 0.0)

    def test_an_iso_timestamp_is_understood_too(self):
        fills = [{**FILLS[1], "exchangeTime": "2026-09-10T15:26:36"}]
        _price, stamp = asyncio.run(_engine(_Dhan(fills))._broker_closing_fill(_pos()))
        self.assertEqual(stamp, datetime(2026, 9, 10, 15, 26, 36))

    def test_a_broker_that_will_not_answer_gives_nothing_rather_than_a_guess(self):
        price, stamp = asyncio.run(_engine(_Dhan(boom=True))._broker_closing_fill(_pos()))
        self.assertEqual((price, stamp), (0.0, None))

    def test_a_position_with_no_security_id_is_not_looked_up(self):
        engine = _engine()
        price, _ = asyncio.run(engine._broker_closing_fill({"transaction_type": "BUY"}))
        self.assertEqual(price, 0.0)


class TheRecorderTakesTheTime(unittest.TestCase):
    def test_a_given_exit_time_beats_the_clock(self):
        import inspect

        signature = inspect.signature(LiveEngine._record_closed_trade)
        self.assertIn("exit_time", signature.parameters)
        source = inspect.getsource(LiveEngine._record_closed_trade)
        self.assertIn('closed_trade["exit_time"] = exit_time or self.current_time', source)


class TheOldRowsAreRepriced(unittest.TestCase):
    """The wrong records are already on disk and nothing revisits a closed
    position, so the repair has to come to them."""

    def _book(self, trade):
        engine = _engine()
        engine.closed_trades = [trade]
        engine.banked_pnl = trade["pnl"]
        engine.daily_pnl = trade["pnl"]
        engine._rewrite_trade_history = lambda trades: None
        engine._save_state = lambda: None
        return engine

    def _wrong_trade(self):
        today = datetime.now().date().isoformat()
        return {
            "exit_reason": "BROKER_MANUAL_EXIT",
            "entry_time": f"{today} 09:25:09",
            "entry_premium": 265.0,
            "exit_premium": 265.0,
            "quantity": 130,
            "lots": 2,
            "option_type": "PE",
            "transaction_type": "BUY",
            "security_id": "47306",
            "trading_symbol": "NIFTY 23700PE 2026-09-15",
            "pnl": -112.94,
        }

    def test_the_price_the_pnl_and_the_time_are_all_corrected(self):
        engine = self._book(self._wrong_trade())
        repaired = asyncio.run(engine._repair_broker_exits_booked_at_entry())
        self.assertEqual(repaired, 1)
        fixed = engine.closed_trades[0]
        self.assertEqual(fixed["exit_premium"], 279.45)
        self.assertEqual(fixed["gross_pnl"], 1878.5)
        self.assertGreater(fixed["pnl"], 1700)
        self.assertEqual(fixed["exit_time"], datetime(2026, 9, 10, 15, 26, 36))

    def test_the_banked_total_moves_by_the_correction_not_to_it(self):
        """`banked_pnl` drives the compounding ladder; it must be adjusted by
        the difference, never overwritten with one trade's P&L."""
        engine = self._book(self._wrong_trade())
        engine.banked_pnl = 50000.0  # other trades came before this one
        asyncio.run(engine._repair_broker_exits_booked_at_entry())
        self.assertAlmostEqual(engine.banked_pnl, 50000.0 + engine.closed_trades[0]["pnl"] - (-112.94), places=2)

    def test_a_trade_that_really_closed_at_its_entry_is_left_alone(self):
        engine = _engine(_Dhan([{**FILLS[1], "tradedPrice": 265.0}]))
        engine.closed_trades = [self._wrong_trade()]
        engine.banked_pnl = engine.daily_pnl = -112.94
        engine._rewrite_trade_history = lambda trades: None
        engine._save_state = lambda: None
        self.assertEqual(asyncio.run(engine._repair_broker_exits_booked_at_entry()), 0)

    def test_an_ordinary_exit_is_never_touched(self):
        trade = {**self._wrong_trade(), "exit_reason": "STOP_LOSS"}
        engine = self._book(trade)
        self.assertEqual(asyncio.run(engine._repair_broker_exits_booked_at_entry()), 0)

    def test_a_trade_from_another_day_is_left_alone(self):
        """The trade book only covers today, so an older one cannot be proven —
        and a guess is worse than a wrong number that is at least stable."""
        trade = {**self._wrong_trade(), "entry_time": "2026-09-03 09:20:00"}
        engine = self._book(trade)
        self.assertEqual(asyncio.run(engine._repair_broker_exits_booked_at_entry()), 0)


if __name__ == "__main__":
    unittest.main()


class TheLadderRemembersAcrossADeploy(unittest.TestCase):
    """Re-deploying a strategy from the Strategy builder builds a BRAND NEW
    engine whose `banked_pnl` starts at zero and is written straight over the
    saved one. On 2026-09-10 that erased the ladder's memory mid-afternoon:
    the book's banked total went to 0 while its trade record still held the
    money. The permanent record cannot be zeroed by a redeploy, so it wins.
    """

    def _book(self, history):
        engine = _engine()
        engine._load_trade_history = lambda: history
        return engine

    def test_the_total_comes_from_the_record(self):
        book = self._book([{"pnl": 1763.21}, {"pnl": -2739.75}])
        self.assertEqual(book._banked_from_trade_history(), -976.54)

    def test_an_empty_record_defers_to_the_state_file(self):
        """None, not 0.0 — a book with no record yet must not be told it has
        banked nothing when the state file knows better."""
        self.assertIsNone(self._book([])._banked_from_trade_history())

    def test_a_losing_book_reports_a_loss(self):
        self.assertEqual(self._book([{"pnl": -4345.25}])._banked_from_trade_history(), -4345.25)

    def test_an_unreadable_row_does_not_poison_the_total(self):
        book = self._book([{"pnl": 1763.21}, {"pnl": "nonsense"}, {}])
        self.assertEqual(book._banked_from_trade_history(), 1763.21)

    def test_the_load_path_prefers_it_over_the_state_file(self):
        import inspect

        source = inspect.getsource(LiveEngine._load_state)
        state_read = source.index('state.get("banked_pnl"')
        record_read = source.index("_banked_from_trade_history()")
        self.assertLess(state_read, record_read, "the record must be applied AFTER the state file, not before")
