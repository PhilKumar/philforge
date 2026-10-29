"""The Scalp desk's second chart prices itself from the WebSocket only.

Phil, 2026-10-29: the Scalp engine must have "the best priority in getting the
prices live without any delays". The second chart asks /api/option-ltp with
feed_only, which must never reach Dhan's REST quote -- the 429-bound endpoint.
"""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402


class _Feed:
    def __init__(self, ltp):
        self.ltp = ltp
        self.asked = []

    def live_option_ltp(self, underlying, strike, expiry, option_type):
        self.asked.append((underlying, strike, expiry, option_type))
        return self.ltp


async def _no_broker(_request):
    raise AssertionError("feed_only must not reach the broker")


class FeedOnly(unittest.TestCase):
    def _ask(self, feed):
        with (
            mock.patch.object(app, "_market_feed", feed),
            mock.patch.object(app, "_request_broker_context", _no_broker),
        ):
            return asyncio.run(app.get_option_ltp(None, "NIFTY", 22700, "2026-11-03", "PE", feed_only=True))

    def test_a_streamed_price_is_returned(self):
        feed = _Feed(123.45)
        self.assertEqual(self._ask(feed), {"status": "ok", "ltp": 123.45, "source": "dhan_feed"})
        self.assertEqual(feed.asked, [("NIFTY", 22700, "2026-11-03", "PE")])

    def test_no_tick_is_no_price_and_no_rest_call(self):
        self.assertEqual(self._ask(_Feed(0)), {"status": "none", "ltp": 0})

    def test_no_feed_at_all(self):
        self.assertEqual(self._ask(None), {"status": "none", "ltp": 0})


if __name__ == "__main__":
    unittest.main()
