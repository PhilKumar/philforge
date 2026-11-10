"""A silent live feed is not a price (2026-11-10).

On 09-Nov-2026 the Dhan feed dropped five times, six seconds each, and the
Scalp engine went on checking stops and targets against the feed's last tick.
While the feed is silent the engine prices open trades from Dhan's quote, and
its status says how long the feed has been quiet so the page can warn.
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

import scalp
from scalp import ScalpEngine, ScalpTrade


def _trade():
    return ScalpTrade(
        trade_id=1,
        underlying="NIFTY",
        strike=25000,
        option_type="PE",
        expiry="2026-11-17",
        transaction_type="BUY",
        lots=1,
        lot_size=65,
        entry_premium=100.0,
        target_premium=200.0,
        sl_premium=50.0,
    )


class SilentFeed(unittest.TestCase):
    def _engine(self, age):
        feed = MagicMock()
        feed.last_tick_age_seconds = age
        feed.get_ltp.return_value = 111.0  # the frozen tick
        dhan = MagicMock()
        dhan.get_ltp_multi.return_value = {"NSE_FNO": {"555": {"last_price": 95.0}}}
        eng = ScalpEngine(dhan, market_feed=feed)
        eng._ws_subs[1] = 555
        return eng, feed, dhan

    def _prices(self, eng):
        with patch.object(scalp.ScripMaster, "lookup", return_value="555"):
            return asyncio.run(eng._fetch_all_ltps([(1, _trade())]))

    def test_a_ticking_feed_prices_the_trade(self):
        eng, _feed, dhan = self._engine(0.3)
        self.assertEqual(self._prices(eng), {1: 111.0})
        dhan.get_ltp_multi.assert_not_called()

    def test_a_silent_feed_is_not_trusted_and_the_quote_prices_it(self):
        eng, feed, dhan = self._engine(6.0)
        self.assertEqual(self._prices(eng), {1: 95.0})
        feed.get_ltp.assert_not_called()
        dhan.get_ltp_multi.assert_called_once()

    def test_status_says_how_long_the_feed_has_been_silent(self):
        eng, _feed, _dhan = self._engine(6.04)
        self.assertEqual(eng.get_status()["feed_silent_sec"], 6.0)

    def test_no_feed_is_not_a_silent_feed(self):
        eng = ScalpEngine(MagicMock(), market_feed=None)
        self.assertEqual(eng.feed_silent_sec(), 0.0)


class FirstReconnectIsFast(unittest.TestCase):
    def test_the_first_try_waits_one_second(self):
        import inspect

        from engine import market_feed

        src = inspect.getsource(market_feed.LiveMarketFeed._schedule_reconnect)
        self.assertIn("delay = 1 if self._reconnect_count == 1", src)


if __name__ == "__main__":
    unittest.main()
