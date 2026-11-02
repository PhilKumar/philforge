"""A Super Order's stop or target is booked at Dhan's fill, not the leg's price.

Trade 339, 02-Nov-2026: CRUDEOILM 9550 PE bought at ₹519.45, stop leg at
₹499.25. The super-order list says only that the leg TRADED, at its set price;
Dhan's trade book shows the SELL filled at ₹498.70. The scalp was booked at
₹499.25 and −₹202 instead of −₹207.50.
"""

import unittest
from unittest.mock import AsyncMock, patch

from scalp import ScalpEngine, ScalpTrade


def _trade():
    trade = ScalpTrade(
        trade_id=339,
        underlying="CRUDEOILM",
        strike=9550,
        option_type="PE",
        expiry="2099-11-17",
        transaction_type="BUY",
        lots=1,
        lot_size=10,
        entry_premium=519.45,
        product_type="MARGIN",
        sl_premium=499.25,
        target_premium=619.3,
        mode="live",
        order_id="SO339",
    )
    trade.super_order_id = "SO339"
    trade.super_filled_qty = 10
    return trade


def _fill(order_id, side, price, at):
    return {
        "orderId": order_id,
        "transactionType": side,
        "securityId": "583427",
        "productType": "MARGIN",
        "tradedQuantity": 10,
        "tradedPrice": price,
        "exchangeTime": at,
    }


class Dhan:
    def __init__(self, fills):
        self.fills = fills

    def get_super_orders(self):
        return [
            {
                "orderId": "SO339",
                "orderStatus": "TRADED",
                "filledQty": 10,
                "averageTradedPrice": 519.45,
                "legDetails": [
                    {"legName": "STOP_LOSS_LEG", "price": 499.25, "orderStatus": "TRADED"},
                    {"legName": "TARGET_LEG", "price": 619.3, "orderStatus": "CANCELLED"},
                ],
            }
        ]

    def get_trades(self):
        return self.fills


class TheStopIsBookedAtDhansFill(unittest.IsolatedAsyncioTestCase):
    async def _sync(self, fills):
        engine = ScalpEngine(Dhan(fills))
        trade = _trade()
        engine.open_trades[trade.trade_id] = trade
        closed = AsyncMock()
        with (
            patch("scalp.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k))),
            patch("scalp.asyncio.sleep", new=AsyncMock()),
            patch("scalp.ScripMaster.lookup", return_value="583427"),
            patch.object(engine, "_close_trade", closed),
        ):
            await engine._sync_super_orders()
        args, kwargs = closed.call_args
        return args[1], kwargs["exit_prem_override"], kwargs["exit_order_id_override"]

    async def test_the_stop_is_booked_at_the_price_dhan_sold_at(self):
        reason, price, order_id = await self._sync(
            [
                _fill("SO339", "BUY", 519.45, "2099-11-02 18:49:53"),
                _fill("SL339", "SELL", 498.70, "2099-11-02 18:54:34"),
            ]
        )
        self.assertEqual((reason, price, order_id), ("sl_hit", 498.70, "SL339"))

    async def test_without_a_fill_in_the_trade_book_it_keeps_the_legs_price(self):
        reason, price, order_id = await self._sync([_fill("SO339", "BUY", 519.45, "2099-11-02 18:49:53")])
        self.assertEqual((reason, price, order_id), ("sl_hit", 499.25, "SO339"))

    async def test_a_sell_another_scalp_already_booked_is_not_taken(self):
        engine_fills = [
            _fill("SO339", "BUY", 519.45, "2099-11-02 18:49:53"),
            _fill("OLD1", "SELL", 470.00, "2099-11-02 18:50:10"),
            _fill("SL339", "SELL", 498.70, "2099-11-02 18:54:34"),
        ]
        engine = ScalpEngine(Dhan(engine_fills))
        engine.closed_trades.append({"trade_id": 338, "order_id": "OLD0", "exit_order_id": "OLD1"})
        trade = _trade()
        engine.open_trades[trade.trade_id] = trade
        closed = AsyncMock()
        with (
            patch("scalp.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k))),
            patch("scalp.asyncio.sleep", new=AsyncMock()),
            patch("scalp.ScripMaster.lookup", return_value="583427"),
            patch.object(engine, "_close_trade", closed),
        ):
            await engine._sync_super_orders()
        self.assertEqual(closed.call_args.kwargs["exit_prem_override"], 498.70)


if __name__ == "__main__":
    unittest.main()
