"""A live book reads only its own product's line in the broker's position book.

15-Oct-2026, 13:10:01. The Zerodha CE book bought 130 NIFTY 22800CE (MIS) at
Rs 276.30, its stop resting at Rs 234.85. One second later it booked the leg
"closed by hand at Rs 261.65", -Rs 2,017.85, and stopped watching it. Kite was
listing that option twice: the book's MIS line, 130 open, and an NRML line,
flat, from the morning's scalps on the same strike. Keyed by security id
alone, the NRML row overwrote the MIS one. Rs 261.65 was the 12:39 scalp exit,
a sell from before the book's own entry.
"""

import asyncio
import unittest
from datetime import datetime

from engine.live import LiveEngine

SEC = "47153"

# Kite's two rows for the option at 13:10:02, as ZerodhaClient.get_positions
# hands them to the engine (MIS first, then the scalps' NRML).
KITE_ROWS = [
    {
        "securityId": SEC,
        "tradingSymbol": "NIFTY26O1922800CE",
        "productType": "MIS",
        "netQty": 130,
        "buyAvg": 276.3,
        "sellAvg": 0.0,
    },
    {
        "securityId": SEC,
        "tradingSymbol": "NIFTY26O1922800CE",
        "productType": "NRML",
        "netQty": 0,
        "buyAvg": 212.73,
        "sellAvg": 209.24,
    },
]

# Today's closing-side fills for the option: the scalps' exits, all before 13:10.
KITE_SELLS = [
    {
        "securityId": SEC,
        "orderId": "261015220944389",
        "transactionType": "SELL",
        "tradedPrice": 261.65,
        "exchangeTime": "2026-10-15 12:39:15",
    },
    {
        "securityId": SEC,
        "orderId": "261015220731696",
        "transactionType": "SELL",
        "tradedPrice": 231.7,
        "exchangeTime": "2026-10-15 11:29:24",
    },
]


class _Broker:
    def __init__(self, rows, fills):
        self.rows, self.fills = rows, fills

    async def async_get_positions(self):
        return self.rows

    def get_trades(self):
        return self.fills


class _Engine(LiveEngine):
    """A live engine with the broker and the disk taken out."""

    def __init__(self, product, rows, fills=()):
        self.strategy = {"product_type": product}
        self.deploy_config = {"product_type": product}
        self.dhan = _Broker(rows, list(fills))
        self.positions = [
            {
                "leg_num": 1,
                "status": "open",
                "transaction_type": "BUY",
                "security_id": SEC,
                "trading_symbol": "NIFTY 22800CE 2026-10-19",
                "quantity": 130,
                "lot_size": 65,
                "lots": 2,
                "entry_premium": 276.3,
                "entry_time": "2026-10-15 13:10:01.300000",
                "sl_order_id": "261015221036840",
            }
        ]
        self.logs, self.recorded = [], []
        self._trades_lock = asyncio.Lock()
        self._signal_candle = None
        self.strat_sl_val = self.strat_tp_val = 0
        self.in_trade = True

    def log_event(self, event_type, message, data=None):
        self.logs.append(message)

    def _save_state(self):
        pass

    def _resolve_position_security_id(self, pos):
        return pos.get("security_id")

    async def _record_closed_trade(self, pos, reason, exit_premium, qty, exit_time=None):
        self.recorded.append((reason, exit_premium, qty))
        return {"reason": reason}


class TheBookReadsItsOwnLine(unittest.TestCase):
    def test_the_15_oct_mis_entry_is_not_closed_by_the_scalps_nrml_line(self):
        engine = _Engine("MIS", KITE_ROWS, KITE_SELLS)
        changed = asyncio.run(engine._reconcile_broker_positions())
        self.assertFalse(changed)
        self.assertEqual(engine.recorded, [])
        self.assertEqual(len(engine.positions), 1)

    def test_the_order_of_kites_rows_does_not_matter(self):
        engine = _Engine("MIS", list(reversed(KITE_ROWS)), KITE_SELLS)
        self.assertFalse(asyncio.run(engine._reconcile_broker_positions()))
        self.assertEqual(len(engine.positions), 1)

    def test_a_carry_book_reads_the_nrml_line_not_the_mis_one(self):
        rows = [dict(KITE_ROWS[0], netQty=0), dict(KITE_ROWS[1], netQty=130)]
        engine = _Engine("NRML", rows)
        self.assertFalse(asyncio.run(engine._reconcile_broker_positions()))

    def test_dhans_product_names_are_understood(self):
        rows = [dict(KITE_ROWS[0], productType="INTRADAY"), dict(KITE_ROWS[1], productType="MARGIN")]
        self.assertFalse(asyncio.run(_Engine("MIS", rows)._reconcile_broker_positions()))

    def test_a_real_close_of_its_own_line_is_still_seen(self):
        rows = [dict(KITE_ROWS[0], netQty=0, sellAvg=290.0), KITE_ROWS[1]]
        fills = KITE_SELLS + [
            {
                "securityId": SEC,
                "orderId": "HAND1",
                "transactionType": "SELL",
                "tradedPrice": 290.0,
                "exchangeTime": "2026-10-15 13:40:00",
            }
        ]
        engine = _Engine("MIS", rows, fills)
        self.assertTrue(asyncio.run(engine._reconcile_broker_positions()))
        reason, price, qty = engine.recorded[0]
        self.assertEqual((price, qty), (290.0, 130))
        self.assertEqual(engine.positions, [])

    def test_a_row_naming_no_product_is_still_read(self):
        rows = [{"securityId": SEC, "netQty": 130}]
        self.assertFalse(asyncio.run(_Engine("MIS", rows)._reconcile_broker_positions()))


class ASellFromBeforeTheEntryIsNotItsExit(unittest.TestCase):
    def test_the_scalps_1239_exit_is_skipped(self):
        engine = _Engine("MIS", KITE_ROWS, KITE_SELLS)
        price, stamp, order = asyncio.run(engine._broker_closing_fill_detail(engine.positions[0]))
        self.assertEqual((price, stamp, order), (0.0, None, ""))

    def test_a_sell_after_the_entry_is_taken(self):
        fills = KITE_SELLS + [
            {
                "securityId": SEC,
                "orderId": "261015221036840",
                "transactionType": "SELL",
                "tradedPrice": 234.6,
                "exchangeTime": "2026-10-15 13:52:10",
            }
        ]
        engine = _Engine("MIS", KITE_ROWS, fills)
        price, stamp, order = asyncio.run(engine._broker_closing_fill_detail(engine.positions[0]))
        self.assertEqual((price, order), (234.6, "261015221036840"))
        self.assertEqual(stamp, datetime(2026, 10, 15, 13, 52, 10))


if __name__ == "__main__":
    unittest.main()
