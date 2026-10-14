"""A closed Zerodha scalp can be re-priced from Kite's own fills.

14-Oct-2026: the site's exit SELL was refused, the entry BUY filled late at
510.60, and Phil sold by hand in Kite at 521.60. The row said 505.55 -> 514.65,
+728; Kite said +880. The match books Kite's numbers and keeps the old ones.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

import config
import db
from scalp import match_scalp_to_broker_fills

SYM = "SENSEX26O1573000PE"


def _trade(**kw):
    t = {
        "trade_id": 216,
        "underlying": "SENSEX",
        "strike": 73000,
        "option_type": "PE",
        "expiry": "2026-10-15",
        "transaction_type": "BUY",
        "lots": 4,
        "lot_size": 20,
        "quantity": 80,
        "entry_premium": 505.55,
        "exit_premium": 514.65,
        "exit_time": "2026-10-14 13:06:40.041143",
        "pnl": 728.0,
        "order_id": "261014220870046",
        "exit_order_id": "261014220870787",
        "mode": "live",
        "broker": "zerodha",
    }
    t.update(kw)
    return t


def _fill(order_id, side, qty, price, at, symbol=SYM):
    return {
        "tradingSymbol": symbol,
        "orderId": order_id,
        "transactionType": side,
        "tradedQuantity": qty,
        "tradedPrice": price,
        "exchangeTime": at,
    }


KITE_14_OCT = [
    _fill("261014220870046", "BUY", 80, 510.6, "2026-10-14 13:06:40"),
    _fill("261014220872491", "SELL", 80, 521.6, "2026-10-14 13:07:14"),
]


class MatchScalpToZerodhaFills(unittest.TestCase):
    def test_the_14_oct_scalp_books_what_kite_filled(self):
        out = match_scalp_to_broker_fills(_trade(), KITE_14_OCT, SYM)
        self.assertEqual(out["entry_premium"], 510.6)
        self.assertEqual(out["exit_premium"], 521.6)
        self.assertEqual(out["pnl"], 880.0)
        self.assertEqual(out["exit_time"], "2026-10-14 13:07:14")
        self.assertEqual(out["broker_match"]["exit_order_ids"], ["261014220872491"])
        self.assertEqual(out["broker_match"]["was"]["pnl"], 728.0)
        self.assertEqual(out["broker_match"]["was"]["entry_premium"], 505.55)

    def test_a_sell_another_scalp_accounts_for_is_never_booked_twice(self):
        with self.assertRaisesRegex(ValueError, "0 of 80"):
            match_scalp_to_broker_fills(_trade(), KITE_14_OCT, SYM, claimed_order_ids={"261014220872491"})

    def test_a_position_still_open_at_the_broker_is_refused(self):
        fills = [KITE_14_OCT[0], _fill("X2", "SELL", 40, 521.6, "2026-10-14 13:07:14")]
        with self.assertRaisesRegex(ValueError, "40 of 80"):
            match_scalp_to_broker_fills(_trade(), fills, SYM)

    def test_no_entry_fill_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no fill"):
            match_scalp_to_broker_fills(_trade(), KITE_14_OCT[1:], SYM)

    def test_its_own_exit_order_comes_first_and_other_contracts_are_ignored(self):
        fills = [
            KITE_14_OCT[0],
            _fill("HAND", "SELL", 80, 530.0, "2026-10-14 13:06:50"),
            _fill("261014220870787", "SELL", 80, 520.0, "2026-10-14 13:07:30"),
            _fill("OTHER", "SELL", 80, 1.0, "2026-10-14 13:06:45", symbol="SENSEX26O1573100PE"),
        ]
        out = match_scalp_to_broker_fills(_trade(), fills, SYM)
        self.assertEqual(out["exit_premium"], 520.0)
        self.assertEqual(out["broker_match"]["exit_order_ids"], ["261014220870787"])

    def test_split_exits_average_and_a_sell_before_the_entry_does_not_count(self):
        fills = [
            _fill("EARLY", "SELL", 80, 900.0, "2026-10-14 13:00:00"),
            KITE_14_OCT[0],
            _fill("A", "SELL", 40, 520.0, "2026-10-14 13:07:00"),
            _fill("B", "SELL", 60, 524.0, "2026-10-14 13:08:00"),
        ]
        out = match_scalp_to_broker_fills(_trade(), fills, SYM)
        self.assertEqual(out["exit_premium"], 522.0)  # 40 @ 520 + 40 @ 524
        self.assertEqual(out["broker_match"]["exit_order_ids"], ["A", "B"])

    def test_a_short_scalp_profits_when_it_buys_back_lower(self):
        fills = [
            _fill("E", "SELL", 80, 500.0, "2026-10-14 10:00:00"),
            _fill("C", "BUY", 80, 480.0, "2026-10-14 10:05:00"),
        ]
        out = match_scalp_to_broker_fills(_trade(transaction_type="SELL", order_id="E", exit_order_id=""), fills, SYM)
        self.assertEqual(out["pnl"], 1600.0)

    def test_the_match_is_written_to_the_saved_row(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = config.DB_PATH
        self.addCleanup(setattr, config, "DB_PATH", old)
        config.DB_PATH = str(Path(tmp.name) / "t.db")

        async def run():
            await db.init_db()
            await db.create_scalp_trade(1, _trade())
            changes = match_scalp_to_broker_fills(_trade(), KITE_14_OCT, SYM)
            updated = await db.update_scalp_trade(1, 216, changes)
            saved = await db.list_scalp_trades(1)
            missing = await db.update_scalp_trade(1, 999, {"pnl": 1})
            return updated, saved, missing

        updated, saved, missing = asyncio.run(run())
        self.assertEqual(updated["pnl"], 880.0)
        self.assertEqual(saved[0]["pnl"], 880.0)
        self.assertEqual(saved[0]["exit_premium"], 521.6)
        self.assertEqual(saved[0]["broker_match"]["was"]["pnl"], 728.0)
        self.assertIsNone(missing)


if __name__ == "__main__":
    unittest.main()
