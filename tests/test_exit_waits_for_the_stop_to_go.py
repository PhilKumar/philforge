"""The stop must be gone before the exit is sent.

2026-09-08, 10:15:00, NIFTY 23950PE, live money. The engine cancelled the stop
and placed the exit in the SAME instant (`asyncio.gather`), so Dhan still had a
resting SELL 130 when a second SELL 130 arrived. RMS priced the pair as a naked
short and refused it:

    REJECTED RMS:222260908203711:You have insufficient funds.
    Please add Rs.400396.92 to trade.

Two seconds later the identical order needed no margin at all and filled at
229.45 -- the cancel had landed by then. The account was never short of money.

The cost of losing that race is not the two seconds. It is `attempt 1/3`: three
of them in a row stops the engine with the position still open at the broker.
"""

import asyncio
import base64
import os
import sys
import tempfile
import unittest

os.environ.setdefault("PHILFORGE_PIN", "123456")
os.environ.setdefault("PHILFORGE_DB", "/tmp/philforge-exit-order-test.db")
os.environ.setdefault("PHILFORGE_USER_DATA_ROOT", "/tmp/philforge-exit-order-data")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.live import LiveEngine  # noqa: E402


class _RmsBroker:
    """Dhan's RMS rule, as it actually behaved that morning.

    A SELL that arrives while the stop is still resting is rejected for margin;
    the same SELL once the stop is gone is a square-off and needs none.
    """

    def __init__(self):
        self.calls: list[str] = []
        self.sl_resting = True
        self.place_saw_resting_sl: bool | None = None

    async def async_cancel_order(self, order_id):
        self.calls.append("cancel")
        # A cancel is a network round trip, not an assignment. THIS await is
        # the whole bug: under asyncio.gather the exit ran in this gap.
        await asyncio.sleep(0)
        self.sl_resting = False
        return {"orderStatus": "CANCELLED"}

    async def async_place_option_order(self, **kw):
        self.calls.append("place")
        # Was the stop still resting at the instant this arrived? That, and
        # not the order of two log lines, is what RMS actually judges.
        self.place_saw_resting_sl = self.sl_resting
        return {"orderId": "REJ-1" if self.sl_resting else "OK-1"}

    async def async_verify_order_fill(self, order_id, max_wait_sec=15):
        if order_id == "REJ-1":
            return {
                "status": "REJECTED",
                "filled_qty": 0,
                "avg_price": 0.0,
                "message": "RMS:222260908203711:You have insufficient funds. Please add Rs.400396.92 to trade.",
            }
        return {"status": "FILLED", "filled_qty": 130, "avg_price": 229.45}


def _position() -> dict:
    return {
        "leg_num": 1,
        "underlying": "NIFTY",
        "strike": 23950,
        "option_type": "PE",
        "expiry": "2026-09-08",
        "trading_symbol": "NIFTY 23950PE 2026-09-08",
        "transaction_type": "BUY",
        "quantity": 130,
        "entry_price": 250.53,
        "entry_premium": 250.53,
        "lot_size": 65,
        "lots": 2,
        "entry_time": "2026-09-08T09:20:12+05:30",
        "symbol": "NIFTY 23950PE 2026-09-08",
        "sl_order_id": "32226090835711",
        "status": "open",
    }


def _run_exit(broker):
    with tempfile.TemporaryDirectory() as tmp:
        engine = LiveEngine(dhan=broker, run_id="exit-order", state_dir=tmp)
        engine.strategy = {"run_name": "exit-order"}
        engine.deploy_config = {"exit_order": "MARKET", "product_type": "INTRADAY"}
        engine._sl_cancel_confirm_sec = 0.2
        pos = _position()
        engine.positions = [pos]
        result = asyncio.run(engine._exit_position(pos, "EXIT_SIGNAL", 229.45))
        return result, pos, engine


class TheStopGoesFirst(unittest.TestCase):
    def test_the_stop_is_gone_before_the_exit_reaches_the_broker(self):
        broker = _RmsBroker()
        _run_exit(broker)
        self.assertEqual(broker.calls[:2], ["cancel", "place"])
        self.assertFalse(broker.place_saw_resting_sl, "the exit arrived while the stop was still resting")

    def test_the_exit_is_not_rejected_for_margin(self):
        broker = _RmsBroker()
        result, _pos, _engine = _run_exit(broker)
        self.assertTrue(result.get("closed"), f"the exit did not close the position: {result}")

    def test_one_attempt_is_enough(self):
        broker = _RmsBroker()
        _result, pos, _engine = _run_exit(broker)
        self.assertEqual(pos.get("_exit_attempts", 0), 0, "a clean exit must not burn a retry")

    def test_an_unconfirmed_cancel_never_blocks_the_exit(self):
        """Being flat matters more than being tidy."""

        class _SilentCancel(_RmsBroker):
            async def async_cancel_order(self, order_id):
                self.calls.append("cancel")
                await asyncio.sleep(0)
                self.sl_resting = False
                return {}  # Dhan said nothing about what became of it

            def get_order_status(self, order_id):
                return {"orderStatus": "UNKNOWN"}

        broker = _SilentCancel()
        _run_exit(broker)
        self.assertIn("place", broker.calls)


if __name__ == "__main__":
    unittest.main()
