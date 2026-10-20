"""Why Dhan Scalp exits lagged Zerodha's, and the second SELL it nearly sent.

Phil, 2026-10-20: "why the exit of Dhan is very delayed when compared to
zerodha". The 19-Oct server journal: every Dhan exit first cancels the Super
Order's ENTRY_LEG; the entry has long traded, so Dhan answers DH-906 "Order
Has Traded". DH-906 is Dhan's code for ANY order error, and the token check
read it as a dead token and minted a new one -- on every exit. Twice the mint
was refused ("Invalid TOTP") after ~10s, and the exit waited all that time.
On the second of those Phil clicked Exit again; that click closed the trade,
and when the stalled first exit woke it sent its own SELL (Dhan's trade book
shows it never filled, but only because a naked sell had no margin).
"""

import asyncio
import unittest
from unittest.mock import patch

import broker.dhan as dhan
from scalp import ScalpEngine, ScalpTrade


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class OnlyATokenMessageIsADeadToken(unittest.TestCase):
    def test_order_errors_under_dh906_are_not_token_errors(self):
        for message in ("Order Has Traded", "Nothing to Cancel . ", "Profit Price must be greater than LTP"):
            self.assertFalse(
                dhan._is_invalid_token_response(_Resp(400, {"errorCode": "DH-906", "errorMessage": message})),
                message,
            )

    def test_an_invalid_token_still_is(self):
        self.assertTrue(
            dhan._is_invalid_token_response(_Resp(400, {"errorCode": "DH-906", "errorMessage": "Invalid Token"}))
        )
        self.assertTrue(dhan._is_invalid_token_response(_Resp(400, {"errorCode": "DH-906"})))
        self.assertTrue(dhan._is_invalid_token_response(_Resp(400, {"data": {"808": "Authentication Failed - x"}})))


def _trade():
    trade = ScalpTrade(
        trade_id=246,
        underlying="NIFTY",
        strike=23100,
        option_type="PE",
        expiry="2099-01-01",
        transaction_type="BUY",
        lots=2,
        lot_size=65,
        entry_premium=212.1,
        mode="live",
        order_id="SO246",
    )
    trade.super_order_id = "SO246"
    trade.super_filled_qty = 130
    trade.current_premium = 217.4
    return trade


class _Broker:
    def __init__(self):
        self.sells = []

    def place_option_order(self, **kw):
        self.sells.append(kw)
        return {"orderId": f"X{len(self.sells)}"}


class OneExitOrderPerTrade(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.broker = _Broker()
        self.engine = ScalpEngine(self.broker)
        self.engine._log = lambda *a: None
        self.trade = _trade()
        self.engine.open_trades[self.trade.trade_id] = self.trade
        self.release = asyncio.Event()

        async def slow_cancel(trade):  # the 10s token stall of 19-Oct
            await self.release.wait()

        self.patches = [
            patch.object(self.engine, "_cancel_super_order", slow_cancel),
            patch.object(self.engine, "_get_ltp", lambda *a: 217.4),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()

    async def test_a_second_click_while_the_first_is_stuck_sends_nothing(self):
        first = asyncio.create_task(self.engine.exit_trade(246))
        await asyncio.sleep(0)
        await self.engine.exit_trade(246)
        self.release.set()
        await first
        self.assertEqual(len(self.broker.sells), 1)
        self.assertNotIn(246, self.engine.open_trades)
        self.assertEqual(len(self.engine.closed_trades), 1)

    async def test_a_broker_close_during_the_exit_stops_its_sell(self):
        first = asyncio.create_task(self.engine.exit_trade(246))
        await asyncio.sleep(0)
        # The target leg filled at Dhan while our exit waited.
        await self.engine._close_trade(self.trade, "target_hit", skip_broker_exit=True, exit_prem_override=230.0)
        self.release.set()
        await first
        self.assertEqual(self.broker.sells, [])
        self.assertEqual([t["exit_reason"] for t in self.engine.closed_trades], ["target_hit"])

    async def test_the_guard_lets_the_next_exit_through_afterwards(self):
        self.release.set()
        await self.engine.exit_trade(246)
        self.assertEqual(self.engine._exits_in_flight, set())


if __name__ == "__main__":
    unittest.main()
