"""The Scalp desk's spot chart follows the form's underlying.

Phil, 2026-10-21: "I need the respective spot chart on this place instead of
only nifty. If I view Crudeoil, I need to have the spot chart on that, and if
it is banknifty options then ... that particular spot".
"""

import asyncio
import os
import unittest
from unittest import mock

import pandas as pd

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app as app_module  # noqa: E402


class _Client:
    def __init__(self):
        self.asked = []

    def get_historical_data(self, **kw):
        self.asked.append((kw["security_id"], kw["exchange_segment"], kw["instrument_type"], kw["candle_type"]))
        idx = pd.date_range("2026-10-20 09:15", periods=60, freq="5min")
        return pd.DataFrame(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": [100 + i * 0.1 for i in range(60)], "volume": 1},
            index=idx,
        )


class WhatTheSpotChartAsksFor(unittest.TestCase):
    def setUp(self):
        app_module._SCALP_UNDERLYING_CHART_CACHE.clear()

    def _ask(self, underlying, spec, expiry=""):
        client = _Client()
        with (
            mock.patch.object(app_module, "_request_broker_context", mock.AsyncMock(return_value=({}, client, "user"))),
            mock.patch.object(app_module._ob, "underlying_spec", lambda name, exp="": spec),
            mock.patch.object(app_module, "_is_scalp_underlying", lambda name: True),
        ):
            out = asyncio.run(app_module.scalp_underlying_chart(None, underlying=underlying, expiry=expiry))
        return out, client.asked

    def test_banknifty_is_its_index(self):
        out, asked = self._ask("banknifty", {"scrip": 25, "seg": "IDX_I", "label": "BANK NIFTY", "kind": "index"})
        self.assertEqual(asked, [("25", "IDX_I", "INDEX", "5")])
        self.assertEqual((out["instrument"]["label"], out["timeframe"], out["fit_sessions"]), ("BANK NIFTY", "5m", 3))
        self.assertTrue(out["candles"])

    def test_crude_is_the_future_its_options_are_struck_on(self):
        _, asked = self._ask(
            "CRUDEOILM", {"scrip": 573422, "seg": "MCX_COMM", "label": "CRUDE OIL MINI", "kind": "mcx"}, "2026-11-17"
        )
        self.assertEqual(asked, [("573422", "MCX_COMM", "FUTCOM", "5")])

    def test_a_stock_is_its_share(self):
        _, asked = self._ask("RELIANCE", {"scrip": 2885, "seg": "NSE_EQ", "label": "RELIANCE", "kind": "stock"})
        self.assertEqual(asked, [("2885", "NSE_EQ", "EQUITY", "5")])

    def test_cpr_and_the_ema_ride_along(self):
        out, _ = self._ask("NIFTY", {"scrip": 13, "seg": "IDX_I", "label": "NIFTY 50", "kind": "index"})
        self.assertTrue(any(o.get("group") == "ema" for o in out["overlays"]))


if __name__ == "__main__":
    unittest.main()
