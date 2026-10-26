"""One scalp at Dhan and Zerodha together, and one Exit for both.

Phil, 2026-10-26: "Is there any way to place scalp order in both brokers at
once?" ... "put just a check box like both".
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402
from scalp import ScalpTrade  # noqa: E402

REQ = app.ScalpEntryReq(
    underlying="NIFTY", strike=22800, option_type="PE", expiry="2026-10-27", lots=1, lot_size=65,
    mode="live", both_brokers=True,
)  # fmt: skip


class _Engine:
    def __init__(self, refuse=(), exit_refuse=()):
        self.open_trades, self.refuse, self.exit_refuse, self.exited = {}, refuse, exit_refuse, []

    async def enter_trade(self, broker="dhan", **kw):
        if broker in self.refuse:
            return {"status": "error", "message": f"{broker} said no"}
        tid = len(self.open_trades) + 1
        trade = ScalpTrade(
            trade_id=tid, underlying="NIFTY", strike=22800, option_type="PE", expiry="2026-10-27",
            transaction_type="BUY", lots=1, lot_size=65, entry_premium=244.0, mode="live", broker=broker,
        )  # fmt: skip
        self.open_trades[tid] = trade
        return {"status": "ok", "trade_id": tid, "trade": trade.to_dict()}

    async def exit_trade(self, trade_id, reason="manual"):
        trade = self.open_trades[trade_id]
        if trade.broker in self.exit_refuse:
            return {"status": "error", "message": "RMS reject"}
        self.exited.append(trade.broker)
        del self.open_trades[trade_id]
        return {"status": "ok", "trade": trade.to_dict()}


def _enter(engine):
    with mock.patch.object(app.alerter, "alert", lambda *a, **k: None):
        return asyncio.run(app._enter_scalp_on_both_brokers(engine, REQ, {}))


def _exit(engine, tid):
    with mock.patch.object(app.alerter, "alert", lambda *a, **k: None):
        return asyncio.run(app._exit_scalp_pair(engine, tid))


class TheBothEntry(unittest.TestCase):
    def test_both_legs_are_placed_and_linked(self):
        engine = _Engine()
        out = _enter(engine)
        self.assertEqual(out["status"], "ok")
        self.assertEqual({t["broker"] for t in out["trades"]}, {"dhan", "zerodha"})
        self.assertTrue(out["pair_id"])
        self.assertEqual({t.pair_id for t in engine.open_trades.values()}, {out["pair_id"]})

    def test_one_broker_refusing_still_places_the_other(self):
        engine = _Engine(refuse=("zerodha",))
        out = _enter(engine)
        self.assertEqual(out["status"], "partial")
        self.assertIn("Zerodha refused", out["message"])
        self.assertEqual([t.broker for t in engine.open_trades.values()], ["dhan"])
        # A lone leg is an ordinary scalp, with an ordinary Exit.
        self.assertEqual(engine.open_trades[1].pair_id, "")

    def test_both_refusing_is_an_error(self):
        out = _enter(_Engine(refuse=("dhan", "zerodha")))
        self.assertEqual(out["status"], "error")


class TheBothExit(unittest.TestCase):
    def test_one_exit_closes_both_legs(self):
        engine = _Engine()
        _enter(engine)
        out = _exit(engine, 1)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(sorted(engine.exited), ["dhan", "zerodha"])
        self.assertEqual(engine.open_trades, {})

    def test_a_refused_leg_stays_open_and_the_other_closes(self):
        engine = _Engine(exit_refuse=("zerodha",))
        _enter(engine)
        out = _exit(engine, 1)
        self.assertEqual(out["status"], "partial")
        self.assertEqual(engine.exited, ["dhan"])
        self.assertEqual([t.broker for t in engine.open_trades.values()], ["zerodha"])

    def test_the_pair_survives_a_restart(self):
        trade = ScalpTrade(
            trade_id=7, underlying="NIFTY", strike=22800, option_type="PE", expiry="2026-10-27",
            transaction_type="BUY", lots=1, lot_size=65, entry_premium=244.0, mode="live", broker="zerodha",
        )  # fmt: skip
        trade.pair_id = "P1"
        self.assertEqual(app._restore_scalp_trade_from_payload(trade.to_dict()).pair_id, "P1")


if __name__ == "__main__":
    unittest.main()
