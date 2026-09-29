"""An expiry-day or compounding size-up that the account cannot carry must fall
back to the book's own lot count — not skip the trade.

2026-09-29, expiry day: leg 1 was sized at 3 lots instead of 2, needed
Rs 51,207 against Rs 39,581 usable, and the entry was abandoned after two
attempts. At 2 lots (~Rs 34,100) it fitted.
"""

import inspect

from engine import live
from engine.live import step_down_to_base_lots

LOT = 65


def _plan(i, own_lots, sized_lots):
    leg = {"lots": own_lots}
    return (i, leg, 22900, 263.0, sized_lots * LOT, "PE", "BUY", sized_lots, "2026-09-29", LOT)


def _cap(i, sized_lots):
    return {
        "leg_num": i + 1,
        "transaction_type": "BUY",
        "entry_premium": 263.0,
        "lots": sized_lots,
        "quantity": sized_lots * LOT,
        "lot_size": LOT,
    }


def test_an_upsized_leg_comes_back_to_its_own_lots():
    plans, caps, stepped = step_down_to_base_lots([_plan(0, 2, 3)], [_cap(0, 3)])
    assert stepped == ["leg 1 3->2"]
    assert plans[0][7] == 2 and plans[0][4] == 2 * LOT
    assert caps[0]["lots"] == 2 and caps[0]["quantity"] == 2 * LOT
    # and the smaller size is what fits: 2 x 65 x 263 = Rs 34,190 < Rs 39,581
    assert caps[0]["quantity"] * caps[0]["entry_premium"] < 39_581


def test_a_leg_already_at_its_own_size_is_untouched():
    plans, caps, stepped = step_down_to_base_lots([_plan(0, 2, 2)], [_cap(0, 2)])
    assert stepped == []
    assert plans[0][7] == 2


def test_the_entry_path_retries_at_the_stepped_down_size():
    src = inspect.getsource(live.LiveEngine)
    fail = src.index("if not await self._can_enter_trade(capital_plans):")
    after = src[fail : fail + 2500]
    assert "step_down_to_base_lots(leg_plans, capital_plans)" in after
    assert "await self._can_enter_trade(fallback_capital)" in after
    assert "leg_plans = fallback_plans" in after
