import os
import shutil
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_DB = Path("/tmp/philforge-test-portfolio.db")
if TEST_DB.exists():
    TEST_DB.unlink()
TEST_USER_DATA = Path("/tmp/philforge-test-portfolio-data")
if TEST_USER_DATA.exists():
    shutil.rmtree(TEST_USER_DATA)

os.environ["PHILFORGE_PIN"] = "123456"
os.environ["PHILFORGE_DB"] = str(TEST_DB)
os.environ["PHILFORGE_USER_DATA_ROOT"] = str(TEST_USER_DATA)
os.environ["PHILFORGE_SKIP_STARTUP_JOBS"] = "1"
os.environ["ENCRYPTION_KEY"] = "QmG8YWqLPtWFDn7gCAiHJXoX7zHn5zi89kUnkkMvibU="
os.environ["DHAN_PIN"] = ""
os.environ["DHAN_TOTP_SECRET"] = ""

import app as app_module
from app import (
    _TRADE_HISTORY_SCHEMA_VERSION,
    _aggregate_portfolio_history,
    _ist_date_str,
    _summarize_real_trade_fills,
    _summarize_real_trade_history,
    _trade_history_entry_needs_refresh,
    _trade_history_needs_repair,
    _trade_history_refresh_start,
)


class PortfolioHistoryRegressionTests(unittest.TestCase):
    def test_summarize_real_trade_fills_uses_fifo_not_day_average(self):
        entry = _summarize_real_trade_fills(
            [
                {
                    "orderId": "B1",
                    "exchangeTradeId": "1",
                    "transactionType": "BUY",
                    "securityId": "NIFTY-1",
                    "tradedQuantity": 10,
                    "tradedPrice": 100,
                    "exchangeTime": "2026-03-12 09:15:00",
                },
                {
                    "orderId": "S1",
                    "exchangeTradeId": "2",
                    "transactionType": "SELL",
                    "securityId": "NIFTY-1",
                    "tradedQuantity": 10,
                    "tradedPrice": 110,
                    "exchangeTime": "2026-03-12 09:20:00",
                },
                {
                    "orderId": "B2",
                    "exchangeTradeId": "3",
                    "transactionType": "BUY",
                    "securityId": "NIFTY-1",
                    "tradedQuantity": 10,
                    "tradedPrice": 120,
                    "exchangeTime": "2026-03-12 09:25:00",
                },
                {
                    "orderId": "S2",
                    "exchangeTradeId": "4",
                    "transactionType": "SELL",
                    "securityId": "NIFTY-1",
                    "tradedQuantity": 10,
                    "tradedPrice": 121,
                    "exchangeTime": "2026-03-12 09:30:00",
                },
            ]
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry["pnl"], 110.0)
        self.assertEqual(entry["net_pnl"], 110.0)
        self.assertEqual(entry["trades"], 4)
        self.assertEqual(entry["trade_legs"], 4)
        self.assertEqual(entry["order_count"], 4)

    def test_summarize_real_trade_history_carries_inventory_across_days(self):
        entries = _summarize_real_trade_history(
            [
                {
                    "orderId": "B1",
                    "exchangeTradeId": "day-1",
                    "transactionType": "BUY",
                    "securityId": "NIFTY-1",
                    "tradedQuantity": 10,
                    "tradedPrice": 100,
                    "exchangeTime": "2026-03-11 15:20:00",
                    "stt": 1.5,
                    "brokerageCharges": 10,
                },
                {
                    "orderId": "S1",
                    "exchangeTradeId": "day-2",
                    "transactionType": "SELL",
                    "securityId": "NIFTY-1",
                    "tradedQuantity": 10,
                    "tradedPrice": 120,
                    "exchangeTime": "2026-03-12 09:20:00",
                    "stt": 2.0,
                    "brokerageCharges": 10,
                },
            ],
            source="historical_fifo",
            carry_inventory=True,
        )

        self.assertEqual(entries["2026-03-11"]["pnl"], 0.0)
        self.assertEqual(entries["2026-03-11"]["charges"], 1.5)
        self.assertEqual(entries["2026-03-11"]["brokerage"], 10.0)
        self.assertEqual(entries["2026-03-11"]["net_pnl"], -11.5)
        self.assertEqual(entries["2026-03-12"]["pnl"], 200.0)
        self.assertEqual(entries["2026-03-12"]["charges"], 2.0)
        self.assertEqual(entries["2026-03-12"]["brokerage"], 10.0)
        self.assertEqual(entries["2026-03-12"]["net_pnl"], 188.0)

    def test_summarize_real_trade_fills_counts_fill_trades_and_preserves_partial_fills(self):
        entry = _summarize_real_trade_fills(
            [
                {
                    "orderId": "B1",
                    "exchangeTradeId": "fill-1",
                    "transactionType": "BUY",
                    "securityId": "BANK-1",
                    "tradedQuantity": 5,
                    "tradedPrice": 100,
                    "exchangeTime": "2026-03-16 09:15:00",
                    "brokerageCharges": 5,
                },
                {
                    "orderId": "B1",
                    "exchangeTradeId": "fill-2",
                    "transactionType": "BUY",
                    "securityId": "BANK-1",
                    "tradedQuantity": 5,
                    "tradedPrice": 100,
                    "exchangeTime": "2026-03-16 09:15:01",
                    "brokerageCharges": 5,
                },
                {
                    "orderId": "S1",
                    "exchangeTradeId": "fill-3",
                    "transactionType": "SELL",
                    "securityId": "BANK-1",
                    "tradedQuantity": 5,
                    "tradedPrice": 110,
                    "exchangeTime": "2026-03-16 09:20:00",
                    "brokerageCharges": 5,
                },
                {
                    "orderId": "S1",
                    "exchangeTradeId": "fill-4",
                    "transactionType": "SELL",
                    "securityId": "BANK-1",
                    "tradedQuantity": 5,
                    "tradedPrice": 110,
                    "exchangeTime": "2026-03-16 09:20:01",
                    "brokerageCharges": 5,
                },
            ]
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry["pnl"], 100.0)
        self.assertEqual(entry["charges"], 0.0)
        self.assertEqual(entry["brokerage"], 20.0)
        self.assertEqual(entry["total_costs"], 20.0)
        self.assertEqual(entry["net_pnl"], 80.0)
        self.assertEqual(entry["trades"], 4)
        self.assertEqual(entry["trade_legs"], 4)
        self.assertEqual(entry["order_count"], 2)

    def test_summarize_real_trade_fills_ignores_zero_exchange_trade_id(self):
        entry = _summarize_real_trade_fills(
            [
                {
                    "orderId": "B1",
                    "exchangeTradeId": "0",
                    "transactionType": "BUY",
                    "securityId": "SENSEX-1",
                    "tradedQuantity": 10,
                    "tradedPrice": 100,
                    "exchangeTime": "2026-03-20 09:15:00",
                    "brokerageCharges": 10,
                },
                {
                    "orderId": "B2",
                    "exchangeTradeId": "0",
                    "transactionType": "BUY",
                    "securityId": "SENSEX-1",
                    "tradedQuantity": 10,
                    "tradedPrice": 101,
                    "exchangeTime": "2026-03-20 09:15:01",
                    "brokerageCharges": 10,
                },
                {
                    "orderId": "S1",
                    "exchangeTradeId": "0",
                    "transactionType": "SELL",
                    "securityId": "SENSEX-1",
                    "tradedQuantity": 10,
                    "tradedPrice": 110,
                    "exchangeTime": "2026-03-20 09:20:00",
                    "brokerageCharges": 10,
                },
                {
                    "orderId": "S2",
                    "exchangeTradeId": "0",
                    "transactionType": "SELL",
                    "securityId": "SENSEX-1",
                    "tradedQuantity": 10,
                    "tradedPrice": 111,
                    "exchangeTime": "2026-03-20 09:20:01",
                    "brokerageCharges": 10,
                },
            ]
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry["pnl"], 200.0)
        self.assertEqual(entry["brokerage"], 40.0)
        self.assertEqual(entry["net_pnl"], 160.0)
        self.assertEqual(entry["trades"], 4)
        self.assertEqual(entry["trade_legs"], 4)
        self.assertEqual(entry["order_count"], 4)

    def test_summarize_real_trade_history_rounds_charges_after_daily_sum(self):
        entries = _summarize_real_trade_history(
            [
                {
                    "orderId": "B1",
                    "exchangeTradeId": "1",
                    "transactionType": "BUY",
                    "securityId": "NIFTY-1",
                    "tradedQuantity": 1,
                    "tradedPrice": 100,
                    "exchangeTime": "2026-03-20 09:15:00",
                    "serviceTax": 0.005,
                },
                {
                    "orderId": "S1",
                    "exchangeTradeId": "2",
                    "transactionType": "SELL",
                    "securityId": "NIFTY-1",
                    "tradedQuantity": 1,
                    "tradedPrice": 101,
                    "exchangeTime": "2026-03-20 09:20:00",
                    "serviceTax": 0.005,
                },
            ],
            source="historical_fifo",
            carry_inventory=False,
        )

        self.assertEqual(entries["2026-03-20"]["charges"], 0.01)
        self.assertEqual(entries["2026-03-20"]["net_pnl"], 0.99)

    def test_aggregate_portfolio_history_tracks_gross_net_and_cost_totals(self):
        real_history = {
            "2026-03-31": {
                "pnl": 100.0,
                "net_pnl": 90.0,
                "charges": 7.0,
                "brokerage": 3.0,
                "total_costs": 10.0,
                "trades": 1,
                "trade_legs": 2,
                "wins": 1,
            },
            "2026-04-01": {
                "pnl": -50.0,
                "net_pnl": -55.0,
                "charges": 4.0,
                "brokerage": 1.0,
                "total_costs": 5.0,
                "trades": 1,
                "trade_legs": 2,
                "wins": 0,
            },
        }
        runs = [
            {
                "mode": "paper",
                "started_at": "2026-03-31 09:15:00",
                "trades": [
                    {"entry_time": "2026-03-31 09:15:00", "exit_time": "2026-03-31 09:20:00", "pnl": 20.0},
                    {"entry_time": "2026-04-01 09:15:00", "exit_time": "2026-04-01 09:20:00", "pnl": -5.0},
                ],
            }
        ]

        daily, monthly, yearly = _aggregate_portfolio_history(real_history, runs)

        self.assertEqual(daily["2026-03-31"]["real_net_pnl"], 90.0)
        self.assertEqual(daily["2026-03-31"]["real_brokerage"], 3.0)
        self.assertEqual(daily["2026-03-31"]["paper_pnl"], 20.0)
        self.assertEqual(monthly["2026-03"]["real_pnl"], 100.0)
        self.assertEqual(monthly["2026-03"]["real_net_pnl"], 90.0)
        self.assertEqual(monthly["2026-03"]["real_charges"], 7.0)
        self.assertEqual(monthly["2026-03"]["real_brokerage"], 3.0)
        self.assertEqual(monthly["2026-03"]["paper_pnl"], 20.0)
        self.assertEqual(monthly["2026-03"]["total_pnl"], 120.0)
        self.assertEqual(monthly["2026-03"]["total_net_pnl"], 110.0)
        self.assertEqual(monthly["2026-04"]["total_pnl"], -55.0)
        self.assertEqual(monthly["2026-04"]["total_net_pnl"], -60.0)
        self.assertEqual(yearly["2026"]["real_pnl"], 50.0)
        self.assertEqual(yearly["2026"]["real_net_pnl"], 35.0)
        self.assertEqual(yearly["2026"]["paper_pnl"], 15.0)
        self.assertEqual(yearly["2026"]["total_pnl"], 65.0)
        self.assertEqual(yearly["2026"]["total_net_pnl"], 50.0)
        self.assertEqual(yearly["2026"]["trades"], 6)
        self.assertEqual(yearly["2026"]["wins"], 2)
        # The broker's fills alone, without the paper trades (Phil, 2026-10-21:
        # the year table's Sep read 424T, 235 of them paper).
        self.assertEqual(monthly["2026-03"]["real_trades"], 2)
        self.assertEqual(yearly["2026"]["real_trades"], 4)

    def test_aggregate_portfolio_history_falls_back_to_run_level_paper_totals(self):
        daily, monthly, yearly = _aggregate_portfolio_history(
            {},
            [
                {
                    "mode": "paper",
                    "started_at": "2026-05-04 09:15:00",
                    "trade_count": 2,
                    "total_pnl": 12.5,
                    "stats": {"winning_trades": 1},
                }
            ],
        )

        self.assertEqual(daily["2026-05-04"]["paper_pnl"], 12.5)
        self.assertEqual(daily["2026-05-04"]["paper_trades"], 2)
        self.assertEqual(monthly["2026-05"]["paper_pnl"], 12.5)
        self.assertEqual(monthly["2026-05"]["total_net_pnl"], 12.5)
        self.assertEqual(yearly["2026"]["wins"], 1)

    def test_ist_date_str_uses_india_calendar_day(self):
        boundary = datetime(2026, 3, 31, 18, 45, tzinfo=timezone.utc)
        self.assertEqual(_ist_date_str(boundary), "2026-04-01")

    def test_trade_history_entry_requires_refresh_when_schema_missing(self):
        self.assertTrue(_trade_history_entry_needs_refresh({"pnl": 10}))
        self.assertTrue(
            _trade_history_entry_needs_refresh(
                {"schema_version": _TRADE_HISTORY_SCHEMA_VERSION, "source": "live_day_fifo", "pnl": 10},
                trade_date="2026-03-12",
                today_str="2026-03-26",
            )
        )
        self.assertFalse(
            _trade_history_entry_needs_refresh(
                {"schema_version": _TRADE_HISTORY_SCHEMA_VERSION, "source": "historical_fifo", "pnl": 10},
                trade_date="2026-03-12",
                today_str="2026-03-26",
            )
        )

    def test_trade_history_repair_uses_cooldown_for_legacy_rows(self):
        history = {"2026-03-12": {"pnl": 10}}
        self.assertTrue(_trade_history_needs_repair(1, history))
        app_module._trade_history_repair_attempts[1] = app_module.time.monotonic()
        self.assertFalse(_trade_history_needs_repair(1, history))
        app_module._trade_history_repair_attempts.pop(1, None)

    def test_trade_history_refresh_start_prefers_recent_stale_month(self):
        history = {
            "2024-06-21": {"schema_version": _TRADE_HISTORY_SCHEMA_VERSION, "source": "historical_fifo"},
            "2026-03-02": {"pnl": -10},
            "2026-03-25": {
                "schema_version": _TRADE_HISTORY_SCHEMA_VERSION,
                "source": "historical_fifo",
            },
        }

        refresh_from = _trade_history_refresh_start(history, "2024-01-01", today_str="2026-03-26")

        self.assertEqual(refresh_from, "2026-03-01")


class PortfolioHistoryRouteRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_portfolio_history_repairs_legacy_rows_before_aggregation(self):
        app_module._trade_history_repair_attempts.pop(1, None)
        legacy_history = {
            "2026-03-12": {
                "pnl": 100.0,
                "net_pnl": 90.0,
                "charges": 10.0,
                "trades": 1,
                "trade_legs": 2,
                "wins": 1,
            }
        }
        refreshed_history = {
            "2026-03-12": {
                "schema_version": _TRADE_HISTORY_SCHEMA_VERSION,
                "source": "historical_fifo",
                "pnl": 10048.50,
                "net_pnl": 4402.01,
                "charges": 4346.49,
                "brokerage": 1300.0,
                "total_costs": 5646.49,
                "trades": 65,
                "trade_legs": 65,
                "order_count": 18,
                "wins": 5,
            }
        }

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        with (
            patch.object(app_module, "_request_user_id", return_value=1),
            patch.object(
                app_module, "_request_broker_context", return_value=({"id": 1, "role": "admin"}, object(), "user")
            ),
            patch.object(app_module.asyncio, "to_thread", side_effect=fake_to_thread),
            patch.object(app_module, "_backfill_trade_history", return_value=1) as backfill_mock,
            patch.object(app_module._db_mod, "list_trade_history", side_effect=[legacy_history, refreshed_history]),
            patch.object(app_module._db_mod, "list_runs", return_value=[]),
        ):
            result = await app_module.get_portfolio_history(None)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["daily"]["2026-03-12"]["real_pnl"], 10048.50)
        self.assertEqual(result["daily"]["2026-03-12"]["real_net_pnl"], 4402.01)
        self.assertEqual(result["daily"]["2026-03-12"]["real_brokerage"], 1300.0)
        self.assertEqual(result["monthly"]["2026-03"]["real_pnl"], 10048.50)
        backfill_mock.assert_called_once()

    async def test_get_portfolio_history_skips_repair_for_current_rows(self):
        app_module._trade_history_repair_attempts.pop(1, None)
        current_history = {
            "2026-03-12": {
                "schema_version": _TRADE_HISTORY_SCHEMA_VERSION,
                "source": "historical_fifo",
                "pnl": 100.0,
                "net_pnl": 80.0,
                "charges": 20.0,
                "brokerage": 10.0,
                "total_costs": 30.0,
                "trades": 2,
                "trade_legs": 4,
                "wins": 1,
            }
        }

        with (
            patch.object(app_module, "_request_user_id", return_value=1),
            patch.object(app_module._db_mod, "list_trade_history", return_value=current_history),
            patch.object(app_module._db_mod, "list_runs", return_value=[]),
            patch.object(app_module, "_backfill_trade_history") as backfill_mock,
        ):
            result = await app_module.get_portfolio_history(None)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["monthly"]["2026-03"]["real_pnl"], 100.0)
        backfill_mock.assert_not_called()


class McxQuantitiesInTheLedger(unittest.TestCase):
    """Phil, 2026-10-21: "why yesterday's commodity trade is not displayed yet?"
    Dhan's trade-history report counts MCX in units (its live book counts
    lots); converting it again made 20-Oct's crude trade 100 units, Rs 3,270,
    where Dhan said Rs 327."""

    def test_history_rows_are_left_in_units(self):
        from broker.dhan import DhanClient

        rows = [
            {"exchangeSegment": "MCX_COMM", "securityId": "1", "tradedQuantity": 10},
            {"exchangeSegment": "NSE_FNO", "securityId": "2", "tradedQuantity": 65},
        ]
        DhanClient._rows_already_units(rows)
        DhanClient._rows_in_units(rows)  # a later conversion leaves them alone
        self.assertEqual([r["tradedQuantity"] for r in rows], [10, 65])

    def test_a_v6_row_is_rebuilt_only_when_it_holds_mcx(self):
        import app as app_module

        crude = {"schema_version": 6, "source": "historical_fifo", "details": [{"symbol": "CRUDEOILM 17 NOV 9600 PUT"}]}
        nifty = {"schema_version": 6, "source": "historical_fifo", "details": [{"symbol": "NIFTY 27 OCT 22700 CALL"}]}
        need = app_module._trade_history_entry_needs_refresh
        self.assertTrue(need(crude, trade_date="2026-10-20", today_str="2026-10-21"))
        self.assertFalse(need(nifty, trade_date="2026-10-19", today_str="2026-10-21"))
        self.assertTrue(need({**nifty, "schema_version": 5}, trade_date="2026-10-19", today_str="2026-10-21"))
        self.assertFalse(need({**crude, "schema_version": 7}, trade_date="2026-10-20", today_str="2026-10-21"))


if __name__ == "__main__":
    unittest.main()
