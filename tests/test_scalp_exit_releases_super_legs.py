"""A manual exit must take the target and stop with it.

Phil, 2026-09-23: "if I exit manually, the target order is not cancelled
automatically and it stays there". Cancelling ENTRY_LEG takes the whole Super
Order only while the entry is still PENDING; once it has traded Dhan answers
DH-906 and leaves the exit legs working. A target resting on a position that
has already been sold is a live SELL that can fill later and open a SHORT.
"""

import unittest
from unittest.mock import AsyncMock, patch

from scalp import ScalpEngine, ScalpTrade


def _trade(**over):
    trade = ScalpTrade(
        trade_id=1,
        underlying="NIFTY",
        strike=23600,
        option_type="PE",
        expiry="2099-01-01",
        transaction_type="BUY",
        lots=1,
        lot_size=65,
        entry_premium=200.0,
        mode="live",
        order_id="SO1",
    )
    trade.super_order_id = "SO1"
    trade.super_filled_qty = 65
    for key, value in over.items():
        setattr(trade, key, value)
    return trade


class Broker:
    def __init__(self, entry_answer=None, entry_raises=None):
        self.cancelled = []
        self._entry_answer = entry_answer or {"orderStatus": "TRADED"}
        self._entry_raises = entry_raises

    def cancel_super_order(self, order_id, leg_name="ENTRY_LEG"):
        self.cancelled.append(leg_name)
        if leg_name == "ENTRY_LEG":
            if self._entry_raises:
                raise Exception(self._entry_raises)
            return self._entry_answer
        return {"orderStatus": "CANCELLED"}


class TheExitLegsAreReleased(unittest.IsolatedAsyncioTestCase):
    async def _cancel(self, broker, trade):
        engine = ScalpEngine(broker)
        with patch("scalp.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k))):
            await engine._cancel_super_order(trade)
        return broker.cancelled

    async def test_a_traded_entry_releases_target_and_stop(self):
        cancelled = await self._cancel(Broker(), _trade())
        self.assertEqual(cancelled, ["ENTRY_LEG", "TARGET_LEG", "STOP_LOSS_LEG"])

    async def test_dhan_refusing_the_entry_cancel_still_releases_them(self):
        """DH-906 "Order Has Traded" is the case that left them resting."""
        broker = Broker(entry_raises="Super order cancel failed 400: DH-906 Order Has Traded")
        cancelled = await self._cancel(broker, _trade())
        self.assertEqual(cancelled, ["ENTRY_LEG", "TARGET_LEG", "STOP_LOSS_LEG"])

    async def test_a_filled_quantity_is_enough_to_release_them(self):
        broker = Broker(entry_answer={"orderStatus": "CANCELLED"})
        cancelled = await self._cancel(broker, _trade(super_filled_qty=65))
        self.assertIn("TARGET_LEG", cancelled)

    async def test_an_unfilled_entry_needs_no_leg_cancels(self):
        broker = Broker(entry_answer={"orderStatus": "CANCELLED"})
        cancelled = await self._cancel(broker, _trade(super_filled_qty=0))
        self.assertEqual(cancelled, ["ENTRY_LEG"])

    async def test_a_leg_that_was_not_resting_is_not_an_error(self):
        class Fussy(Broker):
            def cancel_super_order(self, order_id, leg_name="ENTRY_LEG"):
                self.cancelled.append(leg_name)
                if leg_name == "ENTRY_LEG":
                    return {"orderStatus": "TRADED"}
                raise Exception('{"errorCode":"DH-906","errorMessage":"Nothing to Cancel . "}')

        broker = Fussy()
        engine = ScalpEngine(broker)
        logged = []
        engine._log = lambda level, msg: logged.append((level, msg))
        with patch("scalp.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k))):
            await engine._cancel_super_order(_trade())
        self.assertEqual(broker.cancelled, ["ENTRY_LEG", "TARGET_LEG", "STOP_LOSS_LEG"])
        self.assertFalse([m for level, m in logged if level == "error"], logged)

    async def test_no_super_order_id_does_nothing(self):
        broker = Broker()
        await self._cancel(broker, _trade(super_order_id=""))
        self.assertEqual(broker.cancelled, [])


class TheReleaseRunsBeforeOurOwnExitOrder(unittest.TestCase):
    def test_the_close_path_cancels_first(self):
        from pathlib import Path

        src = Path(__file__).resolve().parent.parent.joinpath("scalp.py").read_text(encoding="utf-8")
        block = src.split("elif self._is_super_order_trade(trade) and trade.super_filled_qty <= 0:")[1]
        block = block.split("exit_prem = exit_prem_override")[0]
        self.assertLess(block.index("_cancel_super_order(trade)"), block.index("place_option_order("))


if __name__ == "__main__":
    unittest.main()
