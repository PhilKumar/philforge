"""A Dhan exit prices without holding the event loop, and is booked once.

Phil, 2026-11-04: "Dhan exit is seems slow compared to zerodha.. When I click
exit it exit 2-3 sec later". The exit read its price with _get_ltp on the event
loop; with no live tick that is Dhan's REST LTP, which answered 429 all morning
and slept a second per retry. It now prices as the entry has since 23-Oct: the
feed, then Kite, and Dhan's REST last and off the loop.

The same morning trade 379 (SENSEX 76800CE) was booked twice. Our SELL had
flattened the position while the exit waited for Dhan to confirm the fill; the
position check read the empty book in that gap and booked a "closed by hand"
exit at the LTP (+Rs 458), and the exit then booked its real fill (+Rs 306).
"""

import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from scalp import ScalpEngine, ScalpTrade, _now_ist


def _trade():
    trade = ScalpTrade(
        trade_id=379,
        underlying="SENSEX",
        strike=76800,
        option_type="CE",
        expiry="2099-11-05",
        transaction_type="BUY",
        lots=4,
        lot_size=20,
        entry_premium=574.27,
        product_type="MARGIN",
        mode="live",
        order_id="SO379",
    )
    trade.super_order_id = "SO379"
    trade.super_filled_qty = 80
    trade.current_premium = 579.0
    trade.entry_time = _now_ist() - timedelta(minutes=1)
    return trade


class _Dhan:
    def __init__(self):
        self.sells = []
        self.rest_ltp_calls = 0
        self.positions = []

    def place_option_order(self, **kw):
        self.sells.append(kw)
        return {"orderId": "X379"}

    def get_option_ltp(self, *a, **k):
        self.rest_ltp_calls += 1
        raise RuntimeError("Dhan API 429")

    def get_positions_cached(self, *_a):
        return self.positions


class _Kite:
    def get_option_quote(self, *a, **k):
        return {"ltp": 580.0}


class DhanExit(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dhan = _Dhan()
        self.engine = ScalpEngine(self.dhan)
        self.engine._log = lambda *a: None
        self.engine.zerodha = _Kite()
        self.trade = _trade()
        self.engine.open_trades[self.trade.trade_id] = self.trade
        self.patches = [
            patch.object(self.engine, "_cancel_super_order", AsyncMock()),
            patch.object(self.engine, "_dhan_entry_fill", AsyncMock(return_value=0.0)),
            patch("scalp.ScripMaster.lookup", return_value="1145"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    async def test_the_exit_prices_from_kite_not_dhans_rest_ltp(self):
        filled = {"status": "FILLED", "avg_price": 578.1, "filled_qty": 80}
        with patch.object(self.engine, "_dhan_exit_fill", AsyncMock(return_value=filled)):
            await self.engine.exit_trade(379)
        self.assertEqual(self.dhan.rest_ltp_calls, 0)
        self.assertEqual(self.dhan.sells[0]["price"], round(580.0 * 0.95, 2))
        self.assertEqual([t["exit_premium"] for t in self.engine.closed_trades], [578.1])

    async def test_the_position_check_mid_exit_does_not_book_it_again(self):
        async def fill_while_the_book_is_read(_order_id):
            # Our SELL has flattened the position; the check runs in the gap.
            await self.engine._sync_broker_positions()
            return {"status": "FILLED", "avg_price": 578.1, "filled_qty": 80}

        with patch.object(self.engine, "_dhan_exit_fill", fill_while_the_book_is_read):
            await self.engine.exit_trade(379)
        self.assertEqual([t["exit_reason"] for t in self.engine.closed_trades], ["manual"])
        self.assertEqual(len(self.dhan.sells), 1)

    async def test_a_position_gone_with_no_exit_of_ours_is_still_booked(self):
        await self.engine._sync_broker_positions()
        self.assertEqual([t["exit_reason"] for t in self.engine.closed_trades], ["broker_manual_exit"])


if __name__ == "__main__":
    unittest.main()
