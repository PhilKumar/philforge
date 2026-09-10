"""A strike is chosen from real prices, or it is not chosen at all.

Phil, 2026-09-03. The live engine entered at 09:20 on a strike neither he nor
paper expected:

    [PAPER] 09:20:01  premium_above: 31 strikes (31 live LTPs, 0 estimated)
                      13 qualify >= Rs 250 -> strike=23800 (Rs 252.60)
    [LIVE]  09:20:03  Batch LTP fetch failed: Dhan API 429, using estimates
                      premium_above: 31 strikes (0 live LTPs, 31 estimated)
                      12 qualify >= Rs 250 -> strike=23750 (Rs 276.73)

Two engines, one rule, different answers -- because live never got prices.
Paper asked first and used the quota; live asked two seconds later and was
refused; `_estimate_premium` then modelled 23800 CE at Rs 227.35 when the
market had it at Rs 252.60, which dropped it under the Rs 250 rule. Live
bought 23750 and filled at Rs 294.73 -- about Rs 5,460 more on 130 qty.

The logs say this is structural, not luck. Across all history the scan ran 20
times: paper 19, all with real prices; live once, with none. Three paper
engines scan at :01 and live scans at :03, so live is last in the queue every
time and is always the one refused.

Two fixes, and they work together:

  1. The prices go on a shelf every caller shares, keyed per contract, so
     paper's fetch answers live's question instead of competing with it. This
     is what `/charts` already does with `_candles_cache`.
  2. A chain with NO real prices in it cannot choose a strike. The entry is
     abandoned and retried rather than placed on a guess.
"""

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from broker import dhan as dhan_mod  # noqa: E402
from engine.live import LiveEngine, PremiumScanUnavailable  # noqa: E402

# The real 09:20 chain, from the log and the fill.
REAL = {42629: 294.73, 42631: 252.60, 42633: 201.10}  # 23750, 23800, 23850


class CountingClient(dhan_mod.DhanClient):
    """A Dhan that counts how many times it is actually asked."""

    def __init__(self, prices=None, fail=False):
        self.calls = 0
        self._prices = REAL if prices is None else prices
        self._fail = fail

    def _payload(self, ids, exchange_segment):
        self.calls += 1
        if self._fail:
            raise Exception("Dhan API 429: Too many requests")
        return {exchange_segment: {str(i): {"last_price": self._prices[i]} for i in ids if i in self._prices}}

    async def async_get_ltp(self, ids, exchange_segment="NSE_FNO"):
        return self._payload(ids, exchange_segment)

    def get_ltp(self, ids, exchange_segment="NSE_FNO"):
        return self._payload(ids, exchange_segment)


class TheShelfIsShared(unittest.TestCase):
    def setUp(self):
        dhan_mod._api_cache.clear()

    def tearDown(self):
        dhan_mod._api_cache.clear()

    def test_the_second_caller_costs_no_request(self):
        client = CountingClient()

        async def go():
            first = await client.async_get_ltp_prices(list(REAL), "NSE_FNO")
            second = await client.async_get_ltp_prices(list(REAL), "NSE_FNO")
            return first, second

        first, second = asyncio.run(go())
        self.assertEqual(client.calls, 1, "paper and live asking the same question must cost one call")
        self.assertEqual(first, second, "live must see exactly the prices paper saw")

    def test_the_live_engine_would_have_seen_23800_at_its_real_price(self):
        """The whole point: Rs 252.60, not the modelled Rs 227.35."""
        client = CountingClient()
        prices = asyncio.run(client.async_get_ltp_prices(list(REAL), "NSE_FNO"))
        self.assertEqual(prices[42631], 252.60)

    def test_an_overlapping_but_different_range_still_reuses_what_it_can(self):
        """Keyed per contract, so a caller scanning other strikes still shares."""
        client = CountingClient()

        async def go():
            await client.async_get_ltp_prices([42629, 42631], "NSE_FNO")
            return await client.async_get_ltp_prices([42631, 42633], "NSE_FNO")

        both = asyncio.run(go())
        self.assertEqual(client.calls, 2)
        self.assertEqual(set(both), {42631, 42633})

    def test_a_stale_shelf_is_refetched(self):
        client = CountingClient()

        async def go():
            await client.async_get_ltp_prices(list(REAL), "NSE_FNO", ttl=0.01)
            await asyncio.sleep(0.05)
            await client.async_get_ltp_prices(list(REAL), "NSE_FNO", ttl=0.01)

        asyncio.run(go())
        self.assertEqual(client.calls, 2, "prices must not be served past their ttl")

    def test_a_quote_of_zero_is_not_shelved_as_a_price(self):
        client = CountingClient(prices={42629: 0.0, 42631: 252.60})
        prices = asyncio.run(client.async_get_ltp_prices([42629, 42631], "NSE_FNO"))
        self.assertNotIn(42629, prices, "absent is how the caller knows a price is missing")
        self.assertIn(42631, prices)


class PaperStocksTheShelfLiveReadsFrom(unittest.TestCase):
    """The sequence that actually happened, in the order it happened.

    Paper scans SYNCHRONOUSLY (`engine/paper_trading.py`) and live scans
    ASYNCHRONOUSLY (`engine/live.py`). Caching only one side would have left
    them on separate shelves -- paper stocking nothing, live still fetching
    alone, and the 429 unchanged. This is the test that would have caught that.
    """

    def setUp(self):
        dhan_mod._api_cache.clear()

    def tearDown(self):
        dhan_mod._api_cache.clear()

    def test_papers_sync_fetch_answers_lives_async_question(self):
        client = CountingClient()

        def paper_scan():  # 09:20:01
            return client.get_ltp_prices(list(REAL), "NSE_FNO")

        async def live_scan():  # 09:20:03
            return await client.async_get_ltp_prices(list(REAL), "NSE_FNO")

        paper = paper_scan()
        live = asyncio.run(live_scan())
        self.assertEqual(client.calls, 1, "live must not re-ask what paper already asked")
        self.assertEqual(paper, live, "paper and live must trade off the same prices")

    def test_both_helpers_use_one_key_namespace(self):
        """Two namespaces would look right and share nothing.

        Five uses: the sync helper reads and writes, the async helper reads and
        writes, and a caller that waited on someone else's fetch reads the shelf
        that fetch filled.
        """
        src = (ROOT / "broker" / "dhan.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('f"ltp1:{exchange_segment}:{sid}"'), 5)


class AGuessedChainCannotChooseAStrike(unittest.TestCase):
    """ScripMaster is STUBBED here.

    The scan resolves strikes through the on-disk scrip master, which exists on
    a developer box and may not in CI. Left real, `test_a_partly_priced_chain_
    still_trades` would pass locally and fail on a machine with no cache -- for
    a reason that has nothing to do with what it is testing.
    """

    STRIKE_IDS = {23750: "42629", 23800: "42631", 23850: "42633"}

    def _engine(self, client):
        engine = LiveEngine.__new__(LiveEngine)
        engine.dhan = client
        engine.logs = []
        engine.log_event = lambda kind, msg: engine.logs.append((kind, msg))
        return engine

    def setUp(self):
        dhan_mod._api_cache.clear()
        self._lookup = dhan_mod.ScripMaster.lookup
        dhan_mod.ScripMaster.lookup = staticmethod(
            lambda symbol, strike, expiry, option_type: self.STRIKE_IDS.get(int(strike))
        )

    def tearDown(self):
        dhan_mod.ScripMaster.lookup = self._lookup

    def test_no_live_price_anywhere_refuses_rather_than_estimating(self):
        engine = self._engine(CountingClient(fail=True))
        with self.assertRaises(PremiumScanUnavailable):
            asyncio.run(engine._find_premium_strike("NIFTY", "2026-09-08", "CE", 250.0, 23969.55, 50, mode="above"))

    def test_it_retries_before_giving_up(self):
        """A 429 is a moment's contention, not an outage."""
        client = CountingClient(fail=True)
        engine = self._engine(client)
        with self.assertRaises(PremiumScanUnavailable):
            asyncio.run(engine._find_premium_strike("NIFTY", "2026-09-08", "CE", 250.0, 23969.55, 50, mode="above"))
        self.assertGreaterEqual(client.calls, 2, "the entry is worth one more ask")

    def test_the_entry_is_abandoned_not_placed_on_a_guess(self):
        src = (ROOT / "engine" / "live.py").read_text(encoding="utf-8")
        self.assertIn("except PremiumScanUnavailable as exc:", src)
        self.assertIn("Entry abandoned", src)

    def test_a_partly_priced_chain_still_trades(self):
        """A model filling ONE gap is fine; being the whole basis is not."""
        client = CountingClient(prices={42631: 252.60})  # only 23800 quotes
        engine = self._engine(client)
        strike, premium = asyncio.run(
            engine._find_premium_strike("NIFTY", "2026-09-08", "CE", 250.0, 23969.55, 50, mode="above")
        )
        self.assertTrue(strike > 0)


if __name__ == "__main__":
    unittest.main()
