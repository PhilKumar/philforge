"""A Zerodha scalp's exit never sells what Zerodha does not hold.

Phil, 2026-11-05: "zerodha scalp not exiting properly... check". Trade 408,
SENSEX 78200CE, 60 qty NRML, Kite's own order book:

    09:21:07  SELL SL     CANCELLED   (the stop, cancelled for the exit)
    09:21:07  SELL LIMIT  REJECTED    "Insufficient funds. Margin required: 607656"
    09:21:09  SELL LIMIT  REJECTED    (the same again, second press)
    09:21:12  SELL LIMIT  COMPLETE    (third press: the real exit)
    09:21:12  SELL LIMIT  REJECTED  x4  (a restored ghost of 408, position flat)

The exit went 40 ms after the stop's cancel, while Kite still counted the
stop's SELL, so the two together read as a short. And a press arriving after
the close found the engine empty, restored 408 from the saved copy, and sent
four SELLs for nothing -- naked shorts, refused only for want of margin --
then booked 408 a second time.
"""

import asyncio
import base64
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("PHILFORGE_PIN", "123456")
os.environ.setdefault("PHILFORGE_DB", "/tmp/philforge-zerodha-never-shorts.db")
os.environ.setdefault("PHILFORGE_USER_DATA_ROOT", "/tmp/philforge-zerodha-never-shorts-data")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")
os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())

from scalp import ScalpEngine  # noqa: E402
from tests.test_scalp_zerodha_exit_is_confirmed import Dhan, Kite, _trade  # noqa: E402


class _Book(Kite):
    """Kite with a position book and a stop that leaves it on its own time."""

    def __init__(self, held=80, stop_settles_after=0, **kw):
        super().__init__(**kw)
        self.held = held
        self.stop_reads = 0
        self.stop_settles_after = stop_settles_after

    def get_order_status(self, order_id):
        if order_id == "S1":
            self.stop_reads += 1
            gone = "S1" in self.cancelled and self.stop_reads > self.stop_settles_after
            return {"orderId": "S1", "orderStatus": "CANCELLED" if gone else "PENDING"}
        return super().get_order_status(order_id)

    def get_positions(self):
        return [{"securityId": "1134431", "netQty": self.held, "productType": "NRML"}]


class ExitNeverShorts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patches = [
            patch("scalp.ScripMaster.lookup", return_value="1134431"),
            patch("scalp.asyncio.sleep", AsyncMock()),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def make(self, kite, trade):
        engine = ScalpEngine(Dhan(), zerodha_client=kite)
        engine._log = lambda *a: None
        engine.open_trades[trade.trade_id] = trade
        return engine

    def sells(self, kite):
        return [o for o in kite.orders if o.get("transaction_type") == "SELL" and o.get("order_type") == "LIMIT"]

    async def test_the_exit_waits_for_the_stop_to_leave_the_book(self):
        kite = _Book(stop_settles_after=3)
        engine = self.make(kite, _trade(broker_sl_order_id="S1", sl_premium=450.0))
        result = await engine.exit_trade(1)
        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(kite.stop_reads, 4)
        self.assertEqual(len(self.sells(kite)), 1)

    async def test_a_stop_still_resting_sends_no_exit_and_stays_on_the_trade(self):
        kite = _Book(stop_settles_after=99)
        trade = _trade(broker_sl_order_id="S1", sl_premium=450.0)
        engine = self.make(kite, trade)
        result = await engine.exit_trade(1)
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.sells(kite), [])
        self.assertEqual(trade.broker_sl_order_id, "S1")
        self.assertIn(1, engine.open_trades)

    async def test_nothing_held_sends_no_sell_and_rests_no_stop(self):
        kite = _Book(held=0)
        engine = self.make(kite, _trade(broker_sl_order_id="S1", sl_premium=450.0))
        result = await engine.exit_trade(1)
        self.assertEqual(result["status"], "error")
        self.assertTrue(result.get("flat"))
        self.assertEqual(kite.orders, [])  # no SELL, and no stop put back on a flat position

    async def test_it_sells_only_what_zerodha_holds(self):
        kite = _Book(held=60)
        engine = self.make(kite, _trade())
        await engine.exit_trade(1)
        self.assertEqual([o["quantity"] for o in self.sells(kite)], [60])

    async def test_the_automations_mis_position_is_not_counted_as_the_scalps(self):
        kite = _Book(held=0)
        kite.get_positions = lambda: [
            {"securityId": "1134431", "netQty": 130, "productType": "MIS"},
            {"securityId": "1134431", "netQty": 0, "productType": "NRML"},
        ]
        engine = self.make(kite, _trade())
        result = await engine.exit_trade(1)
        self.assertTrue(result.get("flat"))
        self.assertEqual(self.sells(kite), [])


class RestoreOnce(unittest.IsolatedAsyncioTestCase):
    async def test_a_closed_trade_is_never_restored_from_the_saved_copy(self):
        import app

        engine = ScalpEngine(Dhan(), zerodha_client=Kite())
        saved = json.dumps({"running": True, "open_trades": [{"trade_id": 408, "underlying": "SENSEX"}]})
        with (
            patch.object(app._db_mod, "get_app_state", AsyncMock(return_value=saved)),
            patch.object(app, "_restore_scalp_trade_from_payload", lambda row: _trade(trade_id=row["trade_id"])),
        ):
            # Trade 408 was just closed here; the saved copy still lists it.
            engine.closed_trades.append({"trade_id": 408})
            self.assertFalse(await app._restore_scalp_open_state(1, engine))
            self.assertEqual(engine.open_trades, {})
            # And a fresh engine restores once only: the second request finds it done.
            fresh = ScalpEngine(Dhan(), zerodha_client=Kite())
            fresh._log = lambda *a: None
            with patch.object(fresh, "start"):
                self.assertTrue(await app._restore_scalp_open_state(1, fresh))
            fresh.open_trades.clear()
            self.assertFalse(await app._restore_scalp_open_state(1, fresh))
            self.assertEqual(fresh.open_trades, {})


if __name__ == "__main__":
    asyncio.run(unittest.main())
