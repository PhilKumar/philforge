import os
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_DB = Path("/tmp/philforge-test-dashboard-summary.db")
TEST_USER_DATA = Path("/tmp/philforge-test-dashboard-summary-data")

os.environ["PHILFORGE_PIN"] = "123456"
os.environ["PHILFORGE_DB"] = str(TEST_DB)
os.environ["PHILFORGE_USER_DATA_ROOT"] = str(TEST_USER_DATA)
os.environ["PHILFORGE_SKIP_STARTUP_JOBS"] = "1"
os.environ["ENCRYPTION_KEY"] = "QmG8YWqLPtWFDn7gCAiHJXoX7zHn5zi89kUnkkMvibU="
os.environ["DHAN_PIN"] = ""
os.environ["DHAN_TOTP_SECRET"] = ""

import app as app_module


class _DummyRequest:
    def __init__(self, user_id: int = 7):
        self.state = SimpleNamespace(user_id=user_id)


class _DummyScalpEngine:
    def __init__(self, payload: dict):
        self._payload = payload

    def get_status(self):
        return self._payload


class DashboardSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if TEST_DB.exists():
            TEST_DB.unlink()
        if TEST_USER_DATA.exists():
            shutil.rmtree(TEST_USER_DATA)
        app_module.config.DB_PATH = str(TEST_DB)
        app_module.config.USER_DATA_ROOT = str(TEST_USER_DATA)
        app_module._USER_DATA_ROOT = str(TEST_USER_DATA)
        app_module._db_mod.config.DB_PATH = str(TEST_DB)
        app_module._db_mod.config.USER_DATA_ROOT = str(TEST_USER_DATA)
        app_module._db_mod._initialized = False
        app_module._DASHBOARD_REAL_CACHE.clear()
        app_module._FII_DII_CACHE["timestamp"] = 0
        app_module._FII_DII_CACHE["data"] = None
        app_module._FII_DII_HISTORY_ROWS = None
        app_module._scalp_engines.clear()
        await app_module._db_mod.init_db()

    async def asyncTearDown(self):
        app_module._scalp_engines.clear()

    async def test_dashboard_summary_aggregates_strategy_scalp_and_dhan_views(self):
        today = app_module._ist_date_str()
        request = _DummyRequest(user_id=7)
        paper_status = {
            "strategy_name": "Paper Alpha",
            "total_pnl": 250.0,
            "trades_today": 3,
            "closed_trades": [],
        }
        live_status = {
            "strategy_name": "Live Beta",
            "total_pnl": 80.0,
            "trades_today": 2,
            "closed_trades": [],
        }
        scalp_status = {
            "running": True,
            "open_trades": [
                {
                    "trade_id": 101,
                    "mode": "paper",
                    "underlying": "NIFTY",
                    "entry_time": f"{today} 10:15:00",
                    "pnl": 50.0,
                },
                {
                    "trade_id": 102,
                    "mode": "live",
                    "underlying": "BANKNIFTY",
                    "entry_time": f"{today} 10:20:00",
                    "pnl": 30.0,
                },
            ],
            "closed_trades": [],
            "total_pnl": 0.0,
        }
        persisted_scalp = [
            {
                "trade_id": 201,
                "mode": "paper",
                "underlying": "NIFTY",
                "exit_time": f"{today} 09:45:00",
                "pnl": 100.0,
            },
            {
                "trade_id": 202,
                "mode": "live",
                "underlying": "BANKNIFTY",
                "exit_time": f"{today} 09:50:00",
                "pnl": -40.0,
            },
        ]
        fii_dii_payload = {
            "status": "partial",
            "source": "Official NSE combined feed",
            "as_of": "25 Mar",
            "latest": {"date": "2026-03-25", "display_date": "25 Mar", "fii_net": -1805.37, "dii_net": 5429.78},
            "rolling_30d": {"fii_net": -1805.37, "dii_net": 5429.78, "days": 1},
            "trend": [{"date": "2026-03-25", "display_date": "25 Mar", "fii_net": -1805.37, "dii_net": 5429.78}],
            "message": "Rolling history builds from the official NSE daily feed.",
        }

        app_module._scalp_engines[7] = _DummyScalpEngine(scalp_status)

        with (
            patch.object(app_module, "_get_session_token", return_value="tok"),
            patch.object(app_module, "_validate_session_async", AsyncMock(return_value={"user_id": 7})),
            patch.object(app_module._db_mod, "list_strategies", AsyncMock(return_value=[{"name": "Paper Alpha"}])),
            patch.object(app_module._db_mod, "list_runs", AsyncMock(return_value=[{"id": 1, "mode": "backtest"}])),
            patch.object(app_module._db_mod, "list_scalp_trades", AsyncMock(return_value=persisted_scalp)),
            patch.object(app_module, "_running_statuses_for_user", side_effect=[[paper_status], [live_status]]),
            patch.object(
                app_module,
                "_request_broker_context",
                AsyncMock(return_value=({"id": 7, "role": "user"}, object(), "user")),
            ),
            patch.object(
                app_module,
                "_load_dashboard_real_snapshot",
                AsyncMock(
                    return_value={
                        "available": True,
                        "source": "dhan",
                        "source_label": "Dhan today",
                        "gross_pnl": 720.0,
                        "net_pnl": 700.0,
                        "charges": 20.0,
                        "brokerage": 0.0,
                        "trades": 9,
                        "message": "Dhan tradebook",
                        "stale": False,
                    }
                ),
            ),
            patch.object(app_module, "_load_dashboard_fii_dii_snapshot", AsyncMock(return_value=fii_dii_payload)),
        ):
            result = await app_module.dashboard_summary(request)

        self.assertEqual(result["paper_total_pnl"], 400.0)
        self.assertEqual(result["paper_total_trades"], 5)
        self.assertEqual(result["paper_scalp_pnl"], 150.0)
        self.assertEqual(result["real_total_pnl"], 700.0)
        self.assertEqual(result["real_total_trades"], 9)
        self.assertEqual(result["real_scalp_pnl"], -10.0)
        self.assertEqual(result["active_count"], 3)
        self.assertEqual(result["paper_flow"]["name"], "Paper Alpha · SCALP NIFTY")
        self.assertEqual(result["real_flow"]["name"], "Live Beta · SCALP BANKNIFTY")
        self.assertEqual(result["paper_strategy_flow"]["pnl"], 250.0)
        self.assertEqual(result["live_strategy_flow"]["pnl"], 710.0)
        self.assertEqual(result["scalp_flow"]["paper_pnl"], 150.0)
        self.assertEqual(result["scalp_flow"]["real_pnl"], -10.0)
        self.assertEqual(result["real_source_label"], "Dhan today")
        self.assertEqual(result["fii_dii"]["latest"]["display_date"], "25 Mar")

    async def test_load_dashboard_real_snapshot_prefers_order_count_over_fill_count(self):
        user_id = 7
        broker_client = SimpleNamespace(
            get_trades=lambda: [
                {
                    "exchangeTime": "2026-03-27 09:16:00",
                    "orderId": "A1",
                    "exchangeTradeId": "1",
                    "transactionType": "BUY",
                    "securityId": "123",
                    "tradingSymbol": "NIFTY XYZ",
                    "tradedPrice": 100,
                    "tradedQuantity": 50,
                },
                {
                    "exchangeTime": "2026-03-27 09:16:01",
                    "orderId": "A1",
                    "exchangeTradeId": "2",
                    "transactionType": "BUY",
                    "securityId": "123",
                    "tradingSymbol": "NIFTY XYZ",
                    "tradedPrice": 100,
                    "tradedQuantity": 15,
                },
                {
                    "exchangeTime": "2026-03-27 09:20:00",
                    "orderId": "B1",
                    "exchangeTradeId": "3",
                    "transactionType": "SELL",
                    "securityId": "123",
                    "tradingSymbol": "NIFTY XYZ",
                    "tradedPrice": 110,
                    "tradedQuantity": 50,
                },
                {
                    "exchangeTime": "2026-03-27 09:20:01",
                    "orderId": "B1",
                    "exchangeTradeId": "4",
                    "transactionType": "SELL",
                    "securityId": "123",
                    "tradingSymbol": "NIFTY XYZ",
                    "tradedPrice": 110,
                    "tradedQuantity": 15,
                },
            ]
        )

        with patch.object(app_module, "_ist_date_str", return_value="2026-03-27"):
            result = app_module._load_dashboard_real_snapshot_sync(user_id, broker_client)

        self.assertTrue(result["available"])
        self.assertEqual(result["trades"], 2)
        self.assertEqual(result["fill_count"], 4)

    async def test_dashboard_summary_dedupes_recent_transactions_from_active_and_saved_runs(self):
        today = app_module._ist_date_str()
        request = _DummyRequest(user_id=7)
        closed_trade_status = {
            "symbol": "NIFTY 23000 CE",
            "transaction_type": "BUY",
            "entry_time": f"{today} 09:15:00",
            "exit_time": f"{today} 09:20:00",
            "entry_premium": 100.0,
            "exit_premium": 112.0,
            "lots": 1,
            "pnl": 780.0,
            "exit_reason": "TARGET",
        }
        closed_trade_run = {
            "symbol": "NIFTY 23000 CE",
            "transaction_type": "BUY",
            "entry_time": f"{today} 09:15:00",
            "exit_time": f"{today} 09:20:00",
            "entry_price": 100.0,
            "exit_price": 112.0,
            "quantity": 65,
            "pnl": 780.0,
            "reason": "TARGET",
        }
        paper_status = {
            "run_id": "Paper Alpha",
            "strategy_name": "Paper Alpha",
            "total_pnl": 780.0,
            "trades_today": 1,
            "closed_trades": [closed_trade_status],
        }

        with (
            patch.object(app_module, "_get_session_token", return_value="tok"),
            patch.object(app_module, "_validate_session_async", AsyncMock(return_value={"user_id": 7})),
            patch.object(app_module._db_mod, "list_strategies", AsyncMock(return_value=[{"name": "Paper Alpha"}])),
            patch.object(
                app_module._db_mod,
                "list_runs",
                AsyncMock(
                    return_value=[
                        {
                            "id": 11,
                            "mode": "paper",
                            "run_name": "Paper Alpha",
                            "total_pnl": 780.0,
                            "trades": [closed_trade_run],
                        }
                    ]
                ),
            ),
            patch.object(app_module._db_mod, "list_scalp_trades", AsyncMock(return_value=[])),
            patch.object(app_module, "_running_statuses_for_user", side_effect=[[paper_status], []]),
            patch.object(
                app_module,
                "_request_broker_context",
                AsyncMock(return_value=({"id": 7, "role": "user"}, None, "user")),
            ),
            patch.object(
                app_module,
                "_load_dashboard_real_snapshot",
                AsyncMock(return_value=app_module._empty_dashboard_real_snapshot()),
            ),
            patch.object(
                app_module, "_load_dashboard_fii_dii_snapshot", AsyncMock(return_value={"status": "unavailable"})
            ),
        ):
            result = await app_module.dashboard_summary(request)

        self.assertEqual(len(result["recent_transactions"]), 1)
        self.assertEqual(result["recent_transactions"][0]["run_name"], "Paper Alpha")
        self.assertEqual(result["recent_transactions"][0]["pnl"], 780.0)
        self.assertFalse(result["recent_transactions"][0]["deletable"])
        self.assertEqual(result["recent_transactions"][0]["source_kind"], "active_engine_trade")

    async def test_dashboard_summary_marks_saved_recent_transactions_as_deletable(self):
        today = app_module._ist_date_str()
        request = _DummyRequest(user_id=7)
        saved_trade = {
            "symbol": "NIFTY 22800 PE",
            "transaction_type": "BUY",
            "entry_time": f"{today} 10:00:00",
            "exit_time": f"{today} 10:05:00",
            "entry_price": 120.0,
            "exit_price": 128.5,
            "quantity": 65,
            "pnl": 552.5,
            "reason": "TARGET",
        }

        with (
            patch.object(app_module, "_get_session_token", return_value="tok"),
            patch.object(app_module, "_validate_session_async", AsyncMock(return_value={"user_id": 7})),
            patch.object(app_module._db_mod, "list_strategies", AsyncMock(return_value=[])),
            patch.object(
                app_module._db_mod,
                "list_runs",
                AsyncMock(
                    return_value=[
                        {
                            "id": 27,
                            "mode": "paper",
                            "run_name": "Saved Paper",
                            "total_pnl": 552.5,
                            "trades": [saved_trade],
                        }
                    ]
                ),
            ),
            patch.object(app_module._db_mod, "list_scalp_trades", AsyncMock(return_value=[])),
            patch.object(app_module, "_running_statuses_for_user", side_effect=[[], []]),
            patch.object(
                app_module,
                "_request_broker_context",
                AsyncMock(return_value=({"id": 7, "role": "user"}, None, "user")),
            ),
            patch.object(
                app_module,
                "_load_dashboard_real_snapshot",
                AsyncMock(return_value=app_module._empty_dashboard_real_snapshot()),
            ),
            patch.object(
                app_module, "_load_dashboard_fii_dii_snapshot", AsyncMock(return_value={"status": "unavailable"})
            ),
        ):
            result = await app_module.dashboard_summary(request)

        self.assertEqual(len(result["recent_transactions"]), 1)
        txn = result["recent_transactions"][0]
        self.assertTrue(txn["deletable"])
        self.assertEqual(txn["source_kind"], "run_trade")
        self.assertEqual(txn["source_id"], 27)
        self.assertEqual(txn["trade_occurrence"], 1)
        self.assertTrue(txn["trade_signature"])
        self.assertEqual(txn["symbol"], "NIFTY 22800 PE")

    async def test_dashboard_summary_includes_scalp_recent_transactions(self):
        today = app_module._ist_date_str()
        request = _DummyRequest(user_id=7)
        scalp_status = {
            "running": True,
            "open_trades": [],
            "closed_trades": [
                {
                    "trade_id": 901,
                    "mode": "paper",
                    "symbol": "SENSEX76000PE2026-04-02",
                    "underlying": "SENSEX",
                    "strike": 76000,
                    "option_type": "PE",
                    "entry_time": f"{today} 10:00:00",
                    "exit_time": f"{today} 10:05:00",
                    "entry_premium": 210.0,
                    "exit_premium": 236.0,
                    "lots": 1,
                    "pnl": 520.0,
                    "exit_reason": "TARGET",
                }
            ],
            "total_pnl": 520.0,
        }
        persisted_scalp = [
            {
                "trade_id": 901,
                "mode": "paper",
                "symbol": "SENSEX76000PE2026-04-02",
                "underlying": "SENSEX",
                "strike": 76000,
                "option_type": "PE",
                "entry_time": f"{today} 10:00:00",
                "exit_time": f"{today} 10:05:00",
                "entry_premium": 210.0,
                "exit_premium": 236.0,
                "lots": 1,
                "pnl": 520.0,
                "exit_reason": "TARGET",
            }
        ]

        app_module._scalp_engines[7] = _DummyScalpEngine(scalp_status)

        with (
            patch.object(app_module, "_get_session_token", return_value="tok"),
            patch.object(app_module, "_validate_session_async", AsyncMock(return_value={"user_id": 7})),
            patch.object(app_module._db_mod, "list_strategies", AsyncMock(return_value=[])),
            patch.object(app_module._db_mod, "list_runs", AsyncMock(return_value=[])),
            patch.object(app_module._db_mod, "list_scalp_trades", AsyncMock(return_value=persisted_scalp)),
            patch.object(app_module, "_running_statuses_for_user", side_effect=[[], []]),
            patch.object(
                app_module,
                "_request_broker_context",
                AsyncMock(return_value=({"id": 7, "role": "user"}, None, "user")),
            ),
            patch.object(
                app_module,
                "_load_dashboard_real_snapshot",
                AsyncMock(return_value=app_module._empty_dashboard_real_snapshot()),
            ),
            patch.object(
                app_module, "_load_dashboard_fii_dii_snapshot", AsyncMock(return_value={"status": "unavailable"})
            ),
        ):
            result = await app_module.dashboard_summary(request)

        self.assertEqual(len(result["recent_transactions"]), 1)
        txn = result["recent_transactions"][0]
        self.assertEqual(txn["mode"], "scalp")
        self.assertEqual(txn["source_kind"], "scalp_trade")
        self.assertEqual(txn["source_id"], 901)
        self.assertEqual(txn["symbol"], "SENSEX 76000 PE")
        self.assertTrue(txn["deletable"])

    async def test_delete_dashboard_recent_transaction_items_updates_saved_run(self):
        user_id = 7
        run = await app_module._db_mod.create_run_record(
            user_id,
            {
                "mode": "paper",
                "run_name": "Paper Delete Test",
                "trade_count": 2,
                "total_pnl": 300.0,
                "stats": {"total_trades": 2, "winning_trades": 2, "losing_trades": 0, "total_pnl": 300.0},
                "trades": [
                    {
                        "symbol": "NIFTY 22800 PE",
                        "transaction_type": "BUY",
                        "entry_time": "2026-03-27 09:15:00",
                        "exit_time": "2026-03-27 09:20:00",
                        "entry_price": 100.0,
                        "exit_price": 120.0,
                        "quantity": 65,
                        "pnl": 1300.0,
                        "reason": "TARGET",
                    },
                    {
                        "symbol": "NIFTY 22900 PE",
                        "transaction_type": "BUY",
                        "entry_time": "2026-03-27 09:30:00",
                        "exit_time": "2026-03-27 09:35:00",
                        "entry_price": 90.0,
                        "exit_price": 74.62,
                        "quantity": 65,
                        "pnl": -1000.0,
                        "reason": "STOP",
                    },
                ],
            },
        )

        delete_result = await app_module._delete_dashboard_recent_transaction_items(
            user_id,
            [
                {
                    "source_kind": "run_trade",
                    "source_id": run["id"],
                    "trade_signature": app_module._dashboard_trade_signature_text(run["trades"][0]),
                    "trade_occurrence": 1,
                }
            ],
        )

        updated_run = await app_module._db_mod.get_run(user_id, run["id"])

        self.assertEqual(delete_result["deleted"], 1)
        self.assertEqual(delete_result["skipped"], 0)
        self.assertIsNotNone(updated_run)
        self.assertEqual(updated_run["trade_count"], 1)
        self.assertEqual(len(updated_run["trades"]), 1)
        self.assertEqual(updated_run["total_pnl"], -1000.0)
        self.assertEqual(updated_run["stats"]["total_trades"], 1)
        self.assertEqual(updated_run["stats"]["losing_trades"], 1)

    def test_normalize_fii_dii_snapshot_rows_collapses_categories_into_one_day(self):
        records = [
            {"category": "FII/FPI", "date": "25-Mar-2026", "netValue": "-1805.37"},
            {"category": "DII", "date": "25-Mar-2026", "netValue": "5429.78"},
        ]

        rows = app_module._normalize_fii_dii_snapshot_rows(records)

        self.assertEqual(
            rows,
            [
                {
                    "date": "2026-03-25",
                    "display_date": "25 Mar",
                    "fii_net": -1805.37,
                    "dii_net": 5429.78,
                }
            ],
        )


class DashboardZerodhaRealTests(unittest.IsolatedAsyncioTestCase):
    """The Real card is Dhan's day plus Zerodha's (Phil, 2026-10-29)."""

    def setUp(self):
        app_module._DASHBOARD_ZERODHA_CACHE.clear()

    def _dhan(self, **over):
        return {
            "available": True,
            "source": "dhan",
            "source_label": "Dhan today",
            "gross_pnl": 720.0,
            "net_pnl": 700.0,
            "charges": 20.0,
            "trades": 4,
            "stale": False,
            **over,
        }

    def test_connected_zerodha_adds_to_dhan(self):
        merged = app_module._merge_dashboard_zerodha(
            self._dhan(),
            {"connected": True, "net_pnl": -150.5, "gross_pnl": -120.0, "charges": 30.5, "trades": 2},
        )
        self.assertEqual(merged["net_pnl"], 549.5)
        self.assertEqual(merged["trades"], 6)
        self.assertEqual(merged["charges"], 50.5)
        self.assertEqual(merged["source_label"], "Dhan + Zerodha today")

    def test_zerodha_not_logged_in_says_so_and_keeps_dhan(self):
        merged = app_module._merge_dashboard_zerodha(
            self._dhan(), {"connected": False, "reason": "zerodha_login_needed"}
        )
        self.assertEqual(merged["net_pnl"], 700.0)
        self.assertEqual(merged["source_label"], "Dhan today · Zerodha not logged in")

    def test_no_kite_app_leaves_dhan_alone(self):
        merged = app_module._merge_dashboard_zerodha(self._dhan(), {"connected": False, "reason": "zerodha_missing"})
        self.assertEqual(merged, self._dhan())

    def test_engine_fallback_is_not_double_counted(self):
        fallback = app_module._empty_dashboard_real_snapshot("Broker not connected")
        merged = app_module._merge_dashboard_zerodha(fallback, {"connected": True, "net_pnl": 500.0, "trades": 3})
        self.assertEqual(merged, fallback)

    async def test_yesterdays_fills_before_kite_clears_are_not_today(self):
        class _Kite:
            def get_positions(self):
                return [{"dayPnl": 900.0, "netQty": 0}]

            def get_trades(self):
                return [{"orderId": "1", "exchangeTime": "2001-01-01 10:00:00"}]

            def get_order_charges(self):
                return {"total": 10.0, "brokerage": 5.0}

        with patch.object(app_module, "_user_zerodha_client", return_value=(_Kite(), "zerodha")):
            snap = await app_module._load_dashboard_zerodha_snapshot({"id": 7})
        self.assertTrue(snap["connected"])
        self.assertEqual((snap["net_pnl"], snap["trades"]), (0.0, 0))

    async def test_todays_fills_count_net_of_charges(self):
        today = app_module._ist_date_str()

        class _Kite:
            def get_positions(self):
                return [{"dayPnl": 900.0, "netQty": 0}]

            def get_trades(self):
                return [
                    {"orderId": "1", "exchangeTime": f"{today} 10:00:00"},
                    {"orderId": "2", "exchangeTime": f"{today} 10:05:00"},
                ]

            def get_order_charges(self):
                return {"total": 40.0, "brokerage": 40.0}

        with patch.object(app_module, "_user_zerodha_client", return_value=(_Kite(), "zerodha")):
            snap = await app_module._load_dashboard_zerodha_snapshot({"id": 7})
        self.assertEqual((snap["net_pnl"], snap["gross_pnl"], snap["trades"]), (860.0, 900.0, 2))


if __name__ == "__main__":
    unittest.main()
