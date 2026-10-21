"""Where the Scalp form's premium comes from.

Phil, 2026-10-20: "Where we fetch the live data? from Dhan or zerodha?", then
"yes.. Please do that... Also why NA on nifty?". The form's premium came from
Dhan alone; two devices polling it drew a stream of 429s and it read N/A. Now
it is priced by the broker the trade will go to, falls back to the other, then
to the contract's last chart close, and one answer is shared for a moment by
every screen asking.
"""

import asyncio
import os
import time
import unittest
from unittest import mock

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app as app_module  # noqa: E402


class _Broker:
    def __init__(self, ltp):
        self.ltp, self.asked = ltp, 0

    def get_option_ltp(self, *a):
        self.asked += 1
        return self.ltp


class _Request:
    class state:
        current_user = {"id": 1}


class WhereThePremiumComesFrom(unittest.TestCase):
    def setUp(self):
        app_module._OPTION_LTP_CACHE.clear()
        app_module._SCALP_CONTRACT_CHART_CACHE.clear()

    def _ask(self, dhan, kite, broker=""):
        ctx = mock.AsyncMock(return_value=({"id": 1}, dhan, "user"))
        with (
            mock.patch.object(app_module, "_request_broker_context", ctx),
            mock.patch.object(app_module, "_user_zerodha_client", lambda user, require_today=True: (kite, "zerodha")),
        ):
            return asyncio.run(app_module.get_option_ltp(_Request(), "NIFTY", 22850, "2026-10-27", "CE", broker=broker))

    def test_dhan_first_for_a_dhan_trade(self):
        dhan, kite = _Broker(120.5), _Broker(121.0)
        self.assertEqual(self._ask(dhan, kite), {"status": "ok", "ltp": 120.5, "source": "dhan"})
        self.assertEqual(kite.asked, 0)

    def test_zerodha_first_for_a_zerodha_trade(self):
        dhan, kite = _Broker(120.5), _Broker(121.0)
        self.assertEqual(self._ask(dhan, kite, broker="zerodha")["source"], "zerodha")
        self.assertEqual(dhan.asked, 0)

    def test_a_refusing_dhan_falls_back_to_zerodha(self):
        got = self._ask(_Broker(0.0), _Broker(119.75))
        self.assertEqual((got["ltp"], got["source"]), (119.75, "zerodha"))

    def test_with_neither_the_last_chart_close(self):
        app_module._SCALP_CONTRACT_CHART_CACHE[(1, "NIFTY", 22850, "2026-10-27", "CE", "5m")] = (
            time.monotonic(),
            {"candles": [{"t": 1, "c": 101.0}, {"t": 2, "c": 104.35}]},
        )
        got = self._ask(_Broker(0.0), None)
        self.assertEqual((got["ltp"], got["source"]), (104.35, "last_close"))

    def test_nothing_anywhere_is_zero_not_another_contracts_price(self):
        app_module._SCALP_CONTRACT_CHART_CACHE[(1, "NIFTY", 22900, "2026-10-27", "CE", "5m")] = (
            time.monotonic(),
            {"candles": [{"t": 2, "c": 88.0}]},
        )
        self.assertEqual(self._ask(_Broker(0.0), None)["ltp"], 0.0)

    def test_a_second_screen_shares_the_answer(self):
        dhan = _Broker(120.5)
        self._ask(dhan, None)
        again = self._ask(dhan, None)
        self.assertEqual((again["ltp"], again.get("cached")), (120.5, True))
        self.assertEqual(dhan.asked, 1)

    def test_a_refusal_is_not_re_asked_for_three_seconds(self):
        """21-Oct-2026 open: every screen re-asking Dhan the moment it said 429
        kept it saying 429, and the site stopped loading."""
        dhan = _Broker(0.0)
        self._ask(dhan, None)
        again = self._ask(dhan, None)
        self.assertEqual((again["ltp"], again.get("cached")), (0.0, True))
        self.assertEqual(dhan.asked, 1)


if __name__ == "__main__":
    unittest.main()
