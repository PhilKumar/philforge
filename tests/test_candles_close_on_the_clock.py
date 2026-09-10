"""A candle ends when its slot ends, not when the next tick happens to arrive.

Phil, 2026-09-10: "the entry signal for today's live CE PE is 09:25:01... But
it executed 17 sec later".

Seven point nine of those seconds were spent before the engine knew anything.
feed_tick() was the only thing that could end a candle, so the 09:20 bar --
complete at 09:25:00 -- was not closed until 09:25:09, because that is when
NIFTY next printed. The engine was not slow; it was blind until the market
spoke.
"""

import threading
import unittest
from datetime import datetime, timedelta

from engine.market_feed import IST, CandleAggregator


class TheClockEndsTheCandle(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 9, 10, 9, 20, 0, tzinfo=IST)
        self.agg = CandleAggregator(timeframe_minutes=5)
        self.fired = []
        self.agg.on_candle_close = [lambda df, c: self.fired.append(c)]

    def test_it_closes_at_the_boundary_with_no_tick_to_prompt_it(self):
        """The exact case that cost the entry."""
        self.agg.feed_tick(100.0, ts=self.t0 + timedelta(seconds=8))
        self.assertEqual(len(self.fired), 0, "not before its time")
        self.assertTrue(self.agg.close_due_candles(now=self.t0 + timedelta(minutes=5)))
        self.assertEqual(len(self.fired), 1)

    def test_it_does_not_close_early(self):
        self.agg.feed_tick(100.0, ts=self.t0 + timedelta(seconds=1))
        self.assertFalse(self.agg.close_due_candles(now=self.t0 + timedelta(minutes=4, seconds=59)))
        self.assertEqual(len(self.fired), 0)

    def test_a_late_tick_cannot_reopen_a_closed_candle(self):
        """Ticks arrive out of order. Re-emitting a bar an entry rule has
        already seen would enter on it twice."""
        self.agg.feed_tick(100.0, ts=self.t0 + timedelta(seconds=8))
        self.agg.close_due_candles(now=self.t0 + timedelta(minutes=5))
        self.agg.feed_tick(999.0, ts=self.t0 + timedelta(seconds=290))  # inside the closed slot
        self.assertEqual(len(self.fired), 1)
        self.assertEqual(len(self.agg.candles), 1)
        self.assertNotIn(999.0, [c["high"] for c in self.agg.candles])

    def test_the_next_slot_still_forms_normally(self):
        self.agg.feed_tick(100.0, ts=self.t0 + timedelta(seconds=8))
        self.agg.close_due_candles(now=self.t0 + timedelta(minutes=5))
        self.agg.feed_tick(102.0, ts=self.t0 + timedelta(minutes=5, seconds=3))
        self.agg.close_due_candles(now=self.t0 + timedelta(minutes=10))
        self.assertEqual(len(self.fired), 2)
        self.assertEqual(self.fired[1]["open"], 102.0)

    def test_a_tick_driven_close_still_works(self):
        """The clock is an addition, not a replacement -- a busy market must
        behave exactly as before."""
        self.agg.feed_tick(100.0, ts=self.t0 + timedelta(seconds=8))
        self.agg.feed_tick(101.0, ts=self.t0 + timedelta(minutes=5, seconds=1))
        self.assertEqual(len(self.fired), 1, "the tick rolled the slot itself")


class TheCloseCarriesItsOwnAge(unittest.TestCase):
    """A clock-closed candle can hold a price that is minutes old. The reader
    is given the number rather than a flag, because the first attempt at this
    was a boolean that could never be true."""

    def setUp(self):
        self.t0 = datetime(2026, 9, 10, 9, 20, 0, tzinfo=IST)
        self.agg = CandleAggregator(timeframe_minutes=5)
        self.fired = []
        self.agg.on_candle_close = [lambda df, c: self.fired.append(c)]

    def test_a_tick_on_the_boundary_is_fresh(self):
        self.agg.feed_tick(100.0, ts=self.t0 + timedelta(seconds=10))
        self.agg.feed_tick(101.0, ts=self.t0 + timedelta(minutes=4, seconds=59))
        self.agg.close_due_candles(now=self.t0 + timedelta(minutes=5))
        self.assertLessEqual(self.fired[0]["close_age_s"], 2)

    def test_a_quiet_candle_says_how_far_behind_its_close_is(self):
        self.agg.feed_tick(100.0, ts=self.t0 + timedelta(seconds=8))
        self.agg.close_due_candles(now=self.t0 + timedelta(minutes=5))
        self.assertAlmostEqual(self.fired[0]["close_age_s"], 292.0, places=0)

    def test_every_candle_carries_the_field(self):
        self.agg.feed_tick(100.0, ts=self.t0 + timedelta(seconds=8))
        self.agg.feed_tick(101.0, ts=self.t0 + timedelta(minutes=5, seconds=1))
        self.assertIn("close_age_s", self.fired[0])


class TheClockRunsOnItsOwn(unittest.TestCase):
    def test_the_closer_thread_ends_a_candle_without_a_tick(self):
        import engine.market_feed as mf

        agg = CandleAggregator(timeframe_minutes=1)
        fired = []
        agg.on_candle_close = [lambda df, c: fired.append(c)]

        class Host:
            def __init__(self):
                self._aggregators = {"x": agg}
                self._closer_thread = None
                self._closer_stop = threading.Event()

        Host._run_candle_closer = mf.LiveMarketFeed._run_candle_closer
        Host.start_candle_closer = mf.LiveMarketFeed.start_candle_closer
        Host.stop_candle_closer = mf.LiveMarketFeed.stop_candle_closer

        host = Host()
        now = mf._now_ist()
        # a slot that has ALREADY ended, so the closer fires on its first pass
        agg.feed_tick(100.0, ts=now - timedelta(minutes=3))
        host.start_candle_closer()
        try:
            for _ in range(40):
                if fired:
                    break
                threading.Event().wait(0.1)
        finally:
            host.stop_candle_closer()
        self.assertEqual(len(fired), 1, "the clock closed it with no tick")

    def test_a_broken_callback_does_not_kill_the_clock(self):
        """One strategy's bad callback must not stop every other book's
        candles from closing."""
        import inspect

        import engine.market_feed as mf

        src = inspect.getsource(mf.LiveMarketFeed._run_candle_closer)
        self.assertIn("except Exception", src)
        self.assertIn("while not self._closer_stop.wait", src)


if __name__ == "__main__":
    unittest.main()


class TheShelfIsWarmBeforeTheEntryReadsIt(unittest.TestCase):
    """The other three seconds.

    The entry's strike scan is ONE batched LTP call -- not 31 round trips, as
    I first said -- sitting behind a 3-second cache. The entry lands about two
    seconds after the candle closes, so asking at candle close means the scan
    reads memory rather than the network.
    """

    import os as _os

    SRC = open(
        _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "engine", "live.py"),
        encoding="utf-8",
    ).read()

    def test_the_shelf_is_warmed_when_a_candle_closes(self):
        self.assertIn("self._warm_option_shelf()", self.SRC)

    def test_only_while_flat(self):
        """A book already in a trade is not about to enter one."""
        i = self.SRC.index("self._warm_option_shelf()")
        self.assertIn("if not self.in_trade:", self.SRC[i - 200 : i])

    def test_it_resolves_the_expiry_the_way_the_entry_does(self):
        """Warming a different expiry's shelf would warm the wrong prices."""
        block = self.SRC.split("def _warm_option_shelf")[1].split("\n    def ")[0]
        self.assertIn("ScripMaster.resolve_expiry", block)
        self.assertIn("ScripMaster.instrument_to_symbol", block)

    def test_it_only_warms_for_rules_that_scan(self):
        block = self.SRC.split("def _warm_option_shelf")[1].split("\n    def ")[0]
        self.assertIn('strike_type.startswith("premium_")', block)

    def test_it_cannot_break_the_entry(self):
        """It is an optimisation. A failure must be silent and total."""
        block = self.SRC.split("def _warm_option_shelf")[1].split("\n    def ")[0]
        self.assertIn("except Exception:", block)
        self.assertIn("_spawn_quiet", block, "fire and forget, never awaited")
        self.assertNotIn("_spawn_tracked", block, "a warm-up failing is not CRITICAL")
        self.assertNotIn("raise", block)

    def test_the_quiet_spawn_is_actually_quiet(self):
        """`_spawn_quiet` is what makes the warm-up harmless: it holds a strong
        reference so the task is not collected mid-flight, and it swallows what
        the task raises. `_spawn_tracked` does the opposite -- it logs CRITICAL,
        which is right for a stop order and wrong for a pre-fetch."""
        block = self.SRC.split("def _spawn_quiet")[1].split("\n    def ")[0]
        self.assertIn("except Exception:", block)
        self.assertIn("asyncio.create_task", block)
        self.assertIn("_background_tasks.add", block, "a dropped task can be collected part-way")
        self.assertNotIn("log_event", block)

    def test_the_funds_call_is_warmed_too(self):
        """The capital check sits between the strike and the order, and it asks
        the broker for the balance. Warming it moves that round trip -- and its
        share of Dhan's rate budget -- off the critical path."""
        block = self.SRC.split("def _warm_option_shelf")[1].split("\n    def ")[0]
        self.assertIn("_warm_funds", block)
        warm = self.SRC.split("def _warm_funds")[1].split("\n    def ")[0]
        self.assertIn("async_get_funds", warm)
        self.assertIn("except Exception:", warm, "a failed warm-up must not stop an entry")
