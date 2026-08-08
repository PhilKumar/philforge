import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PHILFORGE_PIN", "test-pin-not-real")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_SCRIP_MASTER", "0")
os.environ.setdefault("PHILFORGE_STARTUP_TRADE_BACKFILL", "0")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")
os.environ.setdefault("DHAN_CLIENT_ID", "dummy")
os.environ.setdefault("DHAN_ACCESS_TOKEN", "dummy")

import app as app_module
from scalp import ScalpEngine


class OrderInputValidationTests(unittest.TestCase):
    def test_raw_broker_order_is_normalized_and_allowlisted(self):
        request = app_module.OrderRequest(
            security_id="123",
            exchange_segment="nse_eq",
            transaction_type="buy",
            quantity=1,
            order_type="market",
            product_type="intraday",
        )
        values = app_module._validated_order_values(request)
        self.assertEqual(values["transaction_type"], "BUY")
        self.assertEqual(values["exchange_segment"], "NSE_EQ")

        amo = app_module.OrderRequest(
            security_id="123",
            transaction_type="BUY",
            quantity=1,
            after_market_order=True,
            amo_time="pre_open",
        )
        self.assertEqual(app_module._validated_order_values(amo)["amo_time"], "PRE_OPEN")

    def test_order_rejects_impossible_disclosure_and_incomplete_stop(self):
        with self.assertRaises(app_module.HTTPException):
            app_module._validated_order_values(
                app_module.OrderRequest(
                    security_id="123",
                    transaction_type="BUY",
                    quantity=1,
                    disclosed_quantity=2,
                )
            )
        with self.assertRaises(app_module.HTTPException):
            app_module._validated_order_values(
                app_module.OrderRequest(
                    security_id="123",
                    transaction_type="BUY",
                    quantity=1,
                    order_type="STOP_LOSS_MARKET",
                )
            )

    def test_scalp_model_rejects_non_positive_quantity(self):
        with self.assertRaises(ValidationError):
            app_module.ScalpEntryReq(
                underlying="NIFTY",
                strike=25000,
                option_type="CE",
                expiry="2099-01-01",
                lots=0,
            )

    def test_scalp_route_rejects_unknown_mode_and_partial_stop_limit(self):
        expiry = (app_module.datetime.now(app_module.IST).date() + timedelta(days=7)).isoformat()
        unknown_mode = app_module.ScalpEntryReq(
            underlying="NIFTY",
            strike=25000,
            option_type="CE",
            expiry=expiry,
            mode="other",
        )
        with self.assertRaises(app_module.HTTPException):
            app_module._validate_scalp_entry_request(unknown_mode)

        partial_stop = app_module.ScalpEntryReq(
            underlying="NIFTY",
            strike=25000,
            option_type="CE",
            expiry=expiry,
            mode="paper",
            entry_limit_price=100,
        )
        with self.assertRaises(app_module.HTTPException):
            app_module._validate_scalp_entry_request(partial_stop)


class ScalpEngineInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_paper_entry_without_a_real_premium_is_not_created(self):
        broker = type("Broker", (), {"get_option_ltp": lambda self, *args, **kwargs: 0.0})()
        engine = ScalpEngine(broker)
        with patch("scalp.asyncio.sleep", new=AsyncMock()):
            result = await engine.enter_trade(
                underlying="NIFTY",
                strike=25000,
                option_type="CE",
                expiry="2099-01-01",
                transaction_type="BUY",
                lots=1,
                lot_size=65,
                mode="paper",
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("no positive option premium", result["message"])
        self.assertEqual(engine.open_trades, {})

    async def test_engine_rejects_unknown_side_and_mode_before_broker_use(self):
        broker = type("Broker", (), {})()
        engine = ScalpEngine(broker)
        result = await engine.enter_trade(
            underlying="NIFTY",
            strike=25000,
            option_type="CE",
            expiry="2099-01-01",
            transaction_type="HOLD",
            lots=1,
            lot_size=65,
            mode="live",
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(engine.open_trades, {})


if __name__ == "__main__":
    unittest.main()
