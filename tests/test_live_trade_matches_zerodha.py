"""A live Zerodha book's trade can be re-booked from Kite's own fills.

15-Oct-2026: the Zerodha CE book bought 130 NIFTY 22800CE MIS at Rs 276.30
(13:10:01) and a reconcile fault booked it closed at Rs 261.65 (-Rs 2,017.85).
Phil sold the real position in Kite at Rs 301.75 (13:37:07): +Rs 3,308.50
gross. The same option traded NRML all morning in his scalps, and those fills
must not be read as this book's.
"""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import app
from engine.live import LiveEngine, statutory_round_charges

SYM = "NIFTY26O1922800CE"
TODAY = datetime.now(app.IST).strftime("%Y-%m-%d")


def _fill(order_id, side, qty, price, at, product):
    return {
        "tradingSymbol": SYM,
        "orderId": order_id,
        "transactionType": side,
        "tradedQuantity": qty,
        "tradedPrice": price,
        "exchangeTime": f"{TODAY} {at}",
        "productType": product,
    }


KITE = [
    _fill("261015220935574", "BUY", 195, 255.45, "12:36:58", "NRML"),
    _fill("261015220944389", "SELL", 195, 261.65, "12:39:15", "NRML"),
    _fill("261015221036833", "BUY", 65, 276.3, "13:10:01", "MIS"),
    _fill("261015221036833", "BUY", 65, 276.3, "13:10:01", "MIS"),
    _fill("261015221126950", "SELL", 65, 301.75, "13:37:07", "MIS"),
    _fill("261015221126950", "SELL", 65, 301.75, "13:37:07", "MIS"),
]


def _booked():
    return {
        "id": 1,
        "symbol": "NIFTY 22800CE 2026-10-19",
        "underlying": "NIFTY",
        "strike": 22800,
        "expiry": "2026-10-19",
        "option_type": "CE",
        "transaction_type": "BUY",
        "entry_time": f"{TODAY} 13:10:00.590894",
        "exit_time": f"{TODAY} 12:39:15",
        "entry_premium": 276.3,
        "exit_premium": 261.65,
        "quantity": 130,
        "lots": 2.0,
        "lot_size": 65,
        "entry_order_id": "261015221036833",
        "exit_order_id": None,
        "gross_pnl": -1904.5,
        "charges": 113.35,
        "pnl": -2017.85,
        "exit_reason": "BROKER_MANUAL_EXIT",
        "broker": "zerodha",
    }


class _Engine(LiveEngine):
    def __init__(self, history_file):
        self.strategy = {"product_type": "MIS"}
        self.deploy_config = {"broker": "zerodha", "product_type": "MIS"}
        self.closed_trades = [_booked()]
        self.positions = []
        self.banked_pnl = -2017.85
        self.daily_pnl = -2017.85
        self.session_date = datetime.strptime(TODAY, "%Y-%m-%d").date()
        self.profit_cooldown_trigger_date = None
        self._trades_lock = asyncio.Lock()
        self._history_file = history_file
        self.saved = 0
        self.logs = []

    def log_event(self, event_type, message, data=None):
        self.logs.append(message)

    def _save_state(self):
        self.saved += 1


class _Kite:
    def _contract(self, *a):
        return {"tradingsymbol": SYM}

    def get_trades(self):
        return list(KITE)


class TheTradeIsReBookedFromKite(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.history = Path(tmp.name) / "live_history_CE_Z.json"
        self.history.write_text(json.dumps([_booked()]))
        self.engine = _Engine(str(self.history))
        self.run = {
            "id": 478,
            "mode": "live",
            "run_name": "CE_Z",
            "trades": [_booked()],
            "stats": {},
            "total_pnl": -2017.85,
        }
        self.replaced = []

    def _call(self):
        async def replace(user_id, run_id, run):
            self.replaced.append((run_id, run))
            return run

        async def runs(user_id, mode):
            return [json.loads(json.dumps(self.run))]

        request = SimpleNamespace(state=SimpleNamespace(current_user={"id": 1}))
        with (
            mock.patch.object(app, "_request_user_id", lambda r: 1),
            mock.patch.object(app, "_user_zerodha_client", lambda u: (_Kite(), "zerodha")),
            mock.patch.dict(app.live_engines, {1: {"CE_Z": self.engine}}),
            mock.patch.object(app._db_mod, "list_runs_by_mode", runs),
            mock.patch.object(app._db_mod, "replace_run_record", replace),
        ):
            return asyncio.run(
                app.match_live_trade_to_broker(app.LiveBrokerMatchReq(run_id="CE_Z", trade_id="1"), request)
            )

    def test_the_15_oct_trade_books_kites_prices(self):
        out = self._call()
        t = out["trade"]
        self.assertEqual((t["entry_premium"], t["exit_premium"], t["quantity"]), (276.3, 301.75, 130))
        self.assertEqual(t["gross_pnl"], 3308.5)
        charges = statutory_round_charges(
            entry_premium=276.3, exit_premium=301.75, quantity=130, lots=2.0, option_type="CE"
        )
        self.assertEqual(t["charges"], charges)
        self.assertEqual(t["pnl"], round(3308.5 - charges, 2))
        self.assertEqual(t["exit_time"], f"{TODAY} 13:37:07")
        self.assertEqual(t["broker_match"]["exit_order_ids"], ["261015221126950"])
        self.assertEqual(t["broker_match"]["was"]["pnl"], -2017.85)

    def test_the_books_totals_move_by_the_difference(self):
        t = self._call()["trade"]
        self.assertAlmostEqual(self.engine.banked_pnl, t["pnl"], places=2)
        self.assertAlmostEqual(self.engine.daily_pnl, t["pnl"], places=2)
        self.assertGreaterEqual(self.engine.saved, 1)

    def test_the_history_file_and_the_saved_run_are_corrected(self):
        t = self._call()["trade"]
        kept = json.loads(self.history.read_text())
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["exit_premium"], 301.75)
        run_id, run = self.replaced[0]
        self.assertEqual(run_id, 478)
        self.assertEqual(run["total_pnl"], t["pnl"])
        self.assertEqual(run["trades"][0]["exit_premium"], 301.75)

    def test_a_second_match_keeps_the_first_figures(self):
        self._call()
        t = self._call()["trade"]
        self.assertEqual(t["broker_match"]["was"]["exit_premium"], 261.65)
        self.assertAlmostEqual(self.engine.banked_pnl, t["pnl"], places=2)

    def test_a_dhan_book_is_refused(self):
        self.engine.deploy_config = {"broker": "dhan"}
        with self.assertRaises(app.HTTPException) as caught:
            self._call()
        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
