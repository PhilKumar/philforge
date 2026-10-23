"""Today's Dhan scalps re-priced from Dhan's own fills.

Phil, 2026-10-23: eight scalps were recorded at the screen's prices, not
Dhan's fills, and two exits the site never sent were sold by hand in Dhan.
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402

TODAY = app.datetime.now(app.IST).strftime("%Y-%m-%d")


def _trade(tid, order_id, at, exit_order_id="", entry=244.0, pnl=0.0):
    return {
        "trade_id": tid, "mode": "live", "broker": "dhan", "underlying": "NIFTY", "strike": 22800,
        "option_type": "PE", "expiry": "2026-10-27", "transaction_type": "BUY", "quantity": 130, "lots": 2,
        "lot_size": 65, "product_type": "MARGIN", "order_id": order_id, "exit_order_id": exit_order_id,
        "entry_time": f"{TODAY} {at}", "exit_time": f"{TODAY} {at}", "entry_premium": entry,
        "exit_premium": entry, "pnl": pnl, "exit_reason": "manual",
    }  # fmt: skip


def _fill(oid, side, qty, price, at, product="MARGIN", tid=None):
    return {
        "orderId": oid, "exchangeTradeId": tid or f"{oid}-{at}-{qty}", "securityId": "51340",
        "transactionType": side, "tradedQuantity": qty, "tradedPrice": price,
        "exchangeTime": f"{TODAY} {at}", "productType": product,
    }  # fmt: skip


class _Request:
    class state:
        current_user = {"id": 1}
        user_id = 1


class TheDhanMatch(unittest.TestCase):
    def _run(self, trades, fills):
        saved = {}

        async def update(uid, tid, changes):
            saved[tid] = changes
            return changes

        dhan = mock.MagicMock()
        dhan.get_trades.return_value = fills
        with (
            mock.patch.object(app._db_mod, "list_scalp_trades", mock.AsyncMock(return_value=trades)),
            mock.patch.object(app._db_mod, "update_scalp_trade", update),
            mock.patch.object(app, "_request_broker_context", mock.AsyncMock(return_value=({"id": 1}, dhan, "user"))),
            mock.patch.object(app, "_request_user_id", lambda r: 1),
            mock.patch.object(app.ScripMaster, "lookup", lambda *a, **k: "51340"),
        ):
            out = asyncio.run(app.match_scalp_trades_to_dhan(_Request()))
        return out, saved

    def test_hand_closes_go_to_the_right_trades_and_an_intraday_sell_is_ignored(self):
        trades = [
            _trade(272, "E272", "10:00:12", pnl=266.5),  # its exit never reached Dhan; sold by hand
            _trade(273, "E273", "10:08:00", entry=271.5, pnl=650.0),
        ]
        fills = [
            _fill("E272", "BUY", 130, 244.0, "10:00:12"),
            _fill("H1", "SELL", 65, 245.25, "10:02:35", tid="a"),
            _fill("H1", "SELL", 65, 245.25, "10:02:35", tid="b"),  # a partial fill, not a duplicate
            _fill("E273", "BUY", 130, 271.5, "10:08:00"),
            _fill("X", "SELL", 130, 300.0, "10:08:10", product="INTRADAY"),  # not a scalp
            _fill("H2", "SELL", 130, 277.05, "10:08:19"),
        ]
        out, saved = self._run(trades, fills)
        self.assertEqual(out["skipped"], [])
        self.assertEqual(
            (saved[272]["entry_premium"], saved[272]["exit_premium"], saved[272]["pnl"]), (244.0, 245.25, 162.5)
        )
        self.assertEqual((saved[273]["exit_premium"], saved[273]["pnl"]), (277.05, 721.5))
        self.assertEqual(saved[272]["broker_match"]["was"]["pnl"], 266.5)

    def test_a_trade_dhan_has_no_fill_for_is_skipped_not_guessed(self):
        out, saved = self._run([_trade(272, "E272", "10:00:12")], [])
        self.assertEqual(saved, {})
        self.assertEqual(out["skipped"][0]["trade_id"], 272)


if __name__ == "__main__":
    unittest.main()
