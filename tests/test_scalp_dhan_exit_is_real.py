"""A Dhan scalp is only booked closed when Dhan took the exit.

Phil, 2026-10-23: "If I start a scalp trade in Dhan it get executed and the
charts go off and not able to exit on the broker... it exits only on the engine
and not on the broker". Trades 272 and 273: the Super Order's target and stop
were cancelled for the exit, the SELL was refused ("circuit breaker is OPEN" --
the chart API's DH-905 refusals held the breaker open and it gated orders too),
and the trade was booked closed anyway. Dhan still held the position, with no
stop.
"""

import asyncio
import unittest
from unittest import mock

import broker.dhan as dhan_mod
from scalp import ScalpEngine, ScalpTrade


class _Dhan:
    def __init__(self, refuse):
        self.refuse, self.orders = refuse, []

    def place_option_order(self, **kw):
        self.orders.append((kw["order_type"], kw["transaction_type"]))
        if kw["order_type"] == "LIMIT" and self.refuse:
            raise Exception("Dhan API circuit breaker is OPEN — broker unavailable, retrying in 60s")
        return {"orderId": f"OID{len(self.orders)}", "orderStatus": "TRANSIT"}


def _engine(refuse):
    dhan = _Dhan(refuse)
    engine = ScalpEngine(dhan)
    engine._log = lambda *a: None
    trade = ScalpTrade(
        trade_id=272, underlying="NIFTY", strike=22800, option_type="PE", expiry="2026-10-27",
        transaction_type="BUY", lots=2, lot_size=65, entry_premium=244.35, mode="live",
        order_id="323261023113772",
    )  # fmt: skip
    trade.super_order_id = "323261023113772"
    trade.super_filled_qty = 130
    trade.super_order_status = "TRADED"
    trade.target_premium, trade.sl_premium = 344.35, 194.35
    trade.current_premium = 246.40
    engine.open_trades[272] = trade

    async def cancel_super(t):
        return None

    engine._cancel_super_order = cancel_super
    engine._get_ltp = lambda t, tid: 246.40
    return engine, trade, dhan


class TheExitIsReal(unittest.TestCase):
    def test_a_refused_exit_books_nothing_and_puts_the_stop_back(self):
        engine, trade, dhan = _engine(refuse=True)
        out = asyncio.run(engine._close_trade_once(trade, "manual"))
        self.assertEqual(out["status"], "error")
        self.assertIn(272, engine.open_trades)
        self.assertNotEqual(trade.status, "closed")
        self.assertEqual(engine.closed_trades, [])
        # The stop that came off for the exit is back on at Dhan.
        self.assertIn(("SL", "SELL"), dhan.orders)
        self.assertTrue(trade.broker_sl_order_id)

    def test_an_exit_dhan_takes_is_booked(self):
        engine, trade, dhan = _engine(refuse=False)
        asyncio.run(engine._close_trade_once(trade, "manual"))
        self.assertNotIn(272, engine.open_trades)
        self.assertEqual(engine.closed_trades[-1]["exit_order_id"], "OID1")

    def test_the_retry_cancels_the_restored_stop_first(self):
        engine, trade, dhan = _engine(refuse=True)
        asyncio.run(engine._close_trade_once(trade, "manual"))
        cancelled = []

        async def cancel_broker(t):
            cancelled.append(t.broker_sl_order_id)
            t.broker_sl_order_id = ""

        engine._cancel_broker_orders = cancel_broker
        dhan.refuse = False
        asyncio.run(engine._close_trade_once(trade, "manual"))
        self.assertEqual(len(cancelled), 1)
        self.assertNotIn(272, engine.open_trades)


class TheBreakerIsForCharts(unittest.TestCase):
    def setUp(self):
        dhan_mod._circuit_breaker.record_success()

    def tearDown(self):
        dhan_mod._circuit_breaker.record_success()

    def test_an_open_breaker_does_not_stop_an_order(self):
        for _ in range(dhan_mod._circuit_breaker._threshold):
            dhan_mod._circuit_breaker.record_failure()
        self.assertFalse(dhan_mod._circuit_breaker.call_allowed())
        client = dhan_mod.DhanClient("1100000000", "token")
        client._is_configured = lambda: True

        class R:
            status_code = 200

            def json(self):
                return {"orderId": "X1", "orderStatus": "TRANSIT"}

        with mock.patch.object(dhan_mod, "_request_with_retry", lambda *a, **k: R()):
            out = client.place_order(
                security_id="1", exchange_segment="NSE_FNO", transaction_type="SELL", quantity=65,
                order_type="LIMIT", product_type="MARGIN", price=200.0,
            )  # fmt: skip
        self.assertEqual(out["orderId"], "X1")

    def test_a_refused_chart_request_does_not_count(self):
        client = dhan_mod.DhanClient("1100000000", "token")
        client._is_configured = lambda: True

        class R:
            status_code = 400
            text = '{"errorType":"Input_Exception","errorCode":"DH-905"}'

        with (
            mock.patch.object(dhan_mod, "_request_with_retry", lambda *a, **k: R()),
            mock.patch.object(dhan_mod, "_candles_cache_get", lambda key: None),
            mock.patch.object(dhan_mod, "_throttle_charts", lambda: None),
        ):
            for _ in range(dhan_mod._circuit_breaker._threshold + 2):
                with self.assertRaises(Exception):
                    client.get_historical_data(
                        security_id="13", exchange_segment="IDX_I", instrument_type="INDEX",
                        from_date="2026-10-20", to_date="2026-10-23", candle_type="5",
                    )  # fmt: skip
        self.assertTrue(dhan_mod._circuit_breaker.call_allowed())


class TheRecordIsDhansFills(unittest.TestCase):
    """23-Oct-2026: trades were booked at the screen price, not Dhan's fills --
    trade 278 recorded ₹1,121 where Dhan's fills made ₹406."""

    def _engine(self, exit_fill):
        engine, trade, dhan = _engine(refuse=False)
        dhan.get_order_status = lambda oid: {"orderStatus": "TRADED", "averageTradedPrice": 248.85}
        dhan.verify_order_fill = lambda oid, wait, poll: exit_fill
        trade.entry_premium = 244.75  # the quote the order was sent at
        return engine, trade

    def test_the_trade_is_booked_at_both_fills(self):
        engine, trade = self._engine({"status": "FILLED", "avg_price": 250.93, "filled_qty": 130})
        asyncio.run(engine._close_trade_once(trade, "manual"))
        booked = engine.closed_trades[-1]
        self.assertEqual((booked["entry_premium"], booked["exit_premium"]), (248.85, 250.93))
        self.assertAlmostEqual(booked["pnl"], round((250.93 - 248.85) * 130, 2), places=2)

    def test_an_exit_dhan_cancelled_is_not_booked(self):
        engine, trade = self._engine({"status": "CANCELLED", "filled_qty": 0, "message": "RMS reject"})
        out = asyncio.run(engine._close_trade_once(trade, "manual"))
        self.assertEqual(out["status"], "error")
        self.assertIn(272, engine.open_trades)

    def test_the_sync_reads_the_fill_the_list_leaves_out(self):
        engine, trade = self._engine({"status": "FILLED", "avg_price": 250.0, "filled_qty": 130})
        self.assertEqual(asyncio.run(engine._dhan_entry_fill(trade)), 248.85)
        # Asked once per order, not on every 0.75s sync.
        self.assertEqual(asyncio.run(engine._dhan_entry_fill(trade)), 0.0)


if __name__ == "__main__":
    unittest.main()
