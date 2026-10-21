"""Editing a Dhan Super Order scalp's exits sends only what changed.

Phil, 2026-10-21 18:06, trade 267 (CRUDEOILM 9350 PE): he moved the stop from
494.15 to 531.15 and saved. The row sends target AND stop on every save; the
unchanged target went to Dhan too and drew DH-906 "Nothing to Modify". Dhan
had taken the stop, but the engine undid both legs and showed 494.15 against
Dhan's 531.15.
"""

import asyncio
import unittest

from scalp import ScalpEngine, ScalpTrade


class _Dhan:
    def __init__(self, refuse=None):
        self.calls = []
        self.refuse = refuse or {}

    def modify_super_order(self, order_id, leg, **kw):
        self.calls.append((leg, kw.get("target_price") or kw.get("stop_loss_price")))
        if leg in self.refuse:
            raise Exception(f"Super order modify failed 400: {self.refuse[leg]}")
        return {"orderStatus": "TRADED"}


def _engine(dhan):
    engine = ScalpEngine(dhan)
    engine._log = lambda *a: None
    trade = ScalpTrade(
        trade_id=267, underlying="CRUDEOILM", strike=9350, option_type="PE", expiry="2026-11-17",
        transaction_type="BUY", lots=2, lot_size=10, entry_premium=544.15, mode="live",
        order_id="23326102121272",
    )  # fmt: skip
    trade.super_order_id = "23326102121272"
    trade.super_filled_qty = 20
    trade.super_order_status = "TRADED"
    trade.target_premium, trade.sl_premium = 644.15, 494.15
    engine.open_trades[267] = trade
    return engine, trade


class OnlyWhatChangedGoesToDhan(unittest.TestCase):
    def test_moving_the_stop_alone_sends_the_stop_alone(self):
        dhan = _Dhan()
        engine, trade = _engine(dhan)
        out = asyncio.run(engine.update_trade_targets(267, target_premium=644.15, sl_premium=531.15))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(dhan.calls, [("STOP_LOSS_LEG", 531.15)])
        self.assertEqual((trade.target_premium, trade.sl_premium), (644.15, 531.15))

    def test_nothing_to_modify_is_the_state_wanted(self):
        nothing = '{"errorType":"Order_Error","errorCode":"DH-906","errorMessage":"Nothing to Modify . "}'
        dhan = _Dhan(refuse={"TARGET_LEG": nothing})
        engine, trade = _engine(dhan)
        out = asyncio.run(engine.update_trade_targets(267, target_premium=650.0, sl_premium=531.15))
        self.assertEqual(out["status"], "ok")
        self.assertEqual((trade.target_premium, trade.sl_premium), (650.0, 531.15))

    def test_a_real_refusal_undoes_only_its_own_leg(self):
        dhan = _Dhan(refuse={"TARGET_LEG": '{"errorCode":"DH-906","errorMessage":"Profit Price must be greater"}'})
        engine, trade = _engine(dhan)
        out = asyncio.run(engine.update_trade_targets(267, target_premium=500.0, sl_premium=531.15))
        self.assertEqual(out["status"], "error")
        # The stop Dhan took stays; the refused target goes back.
        self.assertEqual((trade.target_premium, trade.sl_premium), (644.15, 531.15))


if __name__ == "__main__":
    unittest.main()
