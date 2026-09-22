"""SENSEX in the strategy-builder backtest: its lots and its strike grid.

Phil, 2026-09-22: "test SENSEX for CE PE and gap carry". The premium-target
selector served NIFTY only, and the lot table doubled SENSEX from 20-Nov-2024
when the contracts themselves (Upstox's records) stayed at 10 until the
07-Jan-2025 weekly -- and the 28-Jan-2025 monthly, listed earlier, kept 10.
"""

import unittest
from datetime import date
from unittest import mock

import engine.backtest as backtest


class SensexLots(unittest.TestCase):
    def test_every_contract_to_the_3_january_weekly_is_ten(self):
        for expiry in (date(2024, 10, 4), date(2024, 11, 22), date(2024, 11, 29), date(2025, 1, 3)):
            self.assertEqual(backtest.get_option_contract_lot_size("1", expiry), 10, expiry)

    def test_twenty_from_the_7_january_weekly(self):
        for expiry in (date(2025, 1, 7), date(2025, 2, 4), date(2025, 9, 4), date(2026, 9, 10)):
            self.assertEqual(backtest.get_option_contract_lot_size("SENSEX", expiry), 20, expiry)

    def test_the_january_monthly_listed_before_the_change_kept_ten(self):
        self.assertEqual(backtest.get_option_contract_lot_size("1", date(2025, 1, 28)), 10)

    def test_nifty_is_untouched(self):
        self.assertEqual(backtest.get_option_contract_lot_size("26000", date(2025, 1, 30)), 25)
        self.assertEqual(backtest.get_option_contract_lot_size("26000", date(2026, 9, 15)), 65)


class SensexStrikeGrid(unittest.TestCase):
    def _selector(self, instrument):
        import tempfile
        from pathlib import Path

        from data import backtest_upstox

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with mock.patch.object(backtest_upstox, "UpstoxPremiumSource") as source:
            source.return_value.available_expiries.return_value = set()
            source.return_value._meta_dir = Path(folder.name)
            selector = backtest_upstox.UpstoxHistoricalPremiumSelector(instrument, cache_only=True)
            return selector, source.call_args.kwargs["underlying_key"]

    def test_sensex_prices_on_bse_with_a_100_point_grid(self):
        selector, key = self._selector("1")
        self.assertEqual((key, selector.strike_step), ("BSE_INDEX|SENSEX", 100))

    def test_nifty_keeps_its_50_point_grid(self):
        selector, key = self._selector("26000")
        self.assertEqual((key, selector.strike_step), ("NSE_INDEX|Nifty 50", 50))

    def test_an_unsupported_index_is_refused(self):
        from data import backtest_upstox

        with self.assertRaises(ValueError):
            backtest_upstox.UpstoxHistoricalPremiumSelector("26009", cache_only=True)


if __name__ == "__main__":
    unittest.main()
