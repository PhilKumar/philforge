"""Scalp practice: a real past day replayed through the real Scalp engine.

Phil, 2026-11-07: "I want to feel like the same live experience as like I am
doing a live trade" ... "go ahead with NIFTY and sensex. Also add that 'at real
speed or faster (2x, 5x, 10x), with Pause.'"

Pinned here, on a two-day synthetic archive:
  * the price inside a minute stays inside that minute's real high and low,
    and starts at its open and ends at its close;
  * the clock is paused until Play, runs at the chosen speed, and stops at the close;
  * a practice entry goes through ScalpEngine in paper and its stop fires on
    the replayed price, on the replayed clock;
  * no chart ever shows a candle beyond the replay clock (no look-ahead);
  * a practice engine never reaches a broker, and never changes the live
    engine's pace.
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import practice  # noqa: E402
import scalp  # noqa: E402

DAY1, DAY2 = date(2025, 4, 15), date(2025, 4, 16)
EXPIRY = "2025-04-17"
STRIKE = 23300


def _write_day(base: Path, day: date, start_price: float, step: float) -> None:
    opt, idx = [], []
    for i in range(9 * 60 + 15, 15 * 60 + 40):
        ts = datetime.combine(day, datetime.min.time()) + timedelta(minutes=i)
        k = i - (9 * 60 + 15)
        o = start_price + step * k
        c = o + step
        opt.append(
            {
                "ts": ts,
                "strike": STRIKE,
                "side": "CE",
                "open": o,
                "high": max(o, c) + 0.5,
                "low": min(o, c) - 0.5,
                "close": c,
                "spot": 23300.0,
                "expiry": EXPIRY,
            }
        )
        if i <= 15 * 60 + 29:
            idx.append({"ts": ts, "open": 23300 + k, "high": 23302 + k, "low": 23299 + k, "close": 23301 + k})
    pd.DataFrame(opt).to_parquet(base / f"{day}.opt.parquet", index=False)
    pd.DataFrame(idx).to_parquet(base / f"{day}.idx.parquet", index=False)


class _Archive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name) / "NIFTY"
        base.mkdir(parents=True)
        _write_day(base, DAY1, 200.0, 0.1)
        _write_day(base, DAY2, 240.0, -0.5)  # falls half a rupee a minute
        json.dump(
            [{"day": str(d), "expiries": [EXPIRY], "rows": 1} for d in (DAY1, DAY2)], open(base / "days.json", "w")
        )
        self._root = patch.object(practice, "DATA_ROOT", Path(self.tmp.name))
        self._root.start()
        practice._DAY_CACHE.clear()
        practice._SESSIONS.clear()
        self.fake_now = [1000.0]

    def tearDown(self):
        self._root.stop()
        practice._DAY_CACHE.clear()
        practice._SESSIONS.clear()
        self.tmp.cleanup()

    def session(self, start="10:00", speed=1, hidden=False):
        s = practice.start_session(1, "NIFTY", DAY2, hidden=hidden, speed=speed, start=start)
        s._clock = lambda: self.fake_now[0]
        s._anchor = self.fake_now[0]
        s.fill_model = {"entry": {"dhan": [0.0], "zerodha": [0.0]}, "exit": {"dhan": [0.0], "zerodha": [0.0]}}
        return s

    def wind(self, real_seconds: float):
        self.fake_now[0] += real_seconds


class ThePriceInsideAMinute(_Archive):
    def test_it_opens_at_the_open_closes_at_the_close_and_never_leaves_the_range(self):
        day = practice.load_day("NIFTY", DAY2)
        series = day.options[(EXPIRY, STRIKE, "CE")]
        for i in (0, 37, 200):
            o, h, low, c = series.o[i], series.h[i], series.l[i], series.c[i]
            self.assertAlmostEqual(practice.path_price(series, i, 0, "seed", i), round(o / 0.05) * 0.05, places=2)
            self.assertAlmostEqual(practice.path_price(series, i, 60, "seed", i), round(c / 0.05) * 0.05, places=2)
            for second in range(0, 61, 3):
                price = practice.path_price(series, i, second, "seed", i)
                self.assertGreaterEqual(price, low - 0.05)
                self.assertLessEqual(price, h + 0.05)


class TheClock(_Archive):
    def test_paused_until_play_then_runs_at_the_speed_and_stops_at_the_close(self):
        s = self.session(start="10:00", speed=10)
        self.wind(30)
        self.assertEqual(s.now().strftime("%H:%M:%S"), "10:00:00")  # paused at the start
        s.play()
        self.wind(6)
        self.assertEqual(s.now().strftime("%H:%M:%S"), "10:01:00")  # 6 real s at 10x
        s.pause()
        self.wind(100)
        self.assertEqual(s.now().strftime("%H:%M:%S"), "10:01:00")
        s.set_speed(1)
        s.play()
        self.wind(30)
        self.assertEqual(s.now().strftime("%H:%M:%S"), "10:01:30")
        self.wind(10 * 3600)
        self.assertEqual(s.now(), s.end_at)
        self.assertTrue(s.paused)

    def test_15x_and_20x_run_at_their_speed(self):
        # Phil, 07-Nov-2026: "can you add 15x and 20x?"
        s = self.session(start="10:00", speed=15)
        s.play()
        self.wind(4)
        self.assertEqual(s.now().strftime("%H:%M:%S"), "10:01:00")  # 4 real s at 15x
        s.set_speed(20)
        self.wind(3)
        self.assertEqual(s.now().strftime("%H:%M:%S"), "10:02:00")  # 3 real s at 20x
        with self.assertRaises(ValueError):
            s.set_speed(25)

    def test_a_hidden_day_is_not_named_until_it_ends(self):
        s = self.session(hidden=True)
        self.assertIsNone(s.state()["day"])
        s.ended = True
        self.assertEqual(s.state()["day"], str(DAY2))


class NoLookAhead(_Archive):
    def test_the_charts_stop_at_the_replay_clock(self):
        s = self.session(start="10:00", speed=1)
        s.play()
        self.wind(20)  # 10:00:20
        rows = s.option_rows(EXPIRY, STRIKE, "CE")
        today = [r for r in rows if r[0].date() == DAY2]
        self.assertEqual(today[-1][0].strftime("%H:%M"), "10:00")  # the forming minute, nothing after
        self.assertTrue(any(r[0].date() == DAY1 for r in rows))  # the day before rides along
        idx = [r for r in s.index_rows() if r[0].date() == DAY2]
        self.assertEqual(idx[-1][0].strftime("%H:%M"), "10:00")


class APracticeTradeRunsOnTheRealEngine(_Archive):
    def test_entry_then_the_stop_fires_on_the_replayed_price(self):
        async def run():
            s = self.session(start="10:00", speed=10)
            s.play()
            with practice.replay_clock(s):
                result = await s.engine.enter_trade(
                    underlying="NIFTY",
                    strike=STRIKE,
                    option_type="CE",
                    expiry=EXPIRY,
                    transaction_type="BUY",
                    lots=1,
                    lot_size=75,
                    sl_points=5,
                    mode="paper",
                    broker="dhan",
                )
            self.assertEqual(result.get("status"), "ok", result)
            trade = s.engine.open_trades[result["trade_id"]]
            self.assertEqual(trade.entry_time.date(), DAY2)  # stamped on the replay's clock
            entry = trade.entry_premium
            self.assertGreater(entry, 0)
            # The option falls 0.5 a minute: 5 points is 10 replay minutes, 60 real s at 10x.
            for _ in range(80):
                self.wind(1)
                await asyncio.sleep(0.03)
                if not s.engine.open_trades:
                    break
            s.engine.stop()
            self.assertFalse(s.engine.open_trades, "the stop never fired")
            closed = s.closed_trades[-1]
            self.assertLess(float(closed["pnl"]), 0)
            self.assertLessEqual(float(closed["exit_premium"]), entry - 5 + 0.5)
            self.assertEqual(s.summary()["trades"], 1)

        asyncio.run(run())


class FillsAreDrawnFromHisOwn(_Archive):
    def test_the_fill_moves_the_price_by_a_drawn_slippage(self):
        s = self.session()
        s.fill_model = {"entry": {"dhan": [1.0], "zerodha": [-1.0]}, "exit": {"dhan": [0.0], "zerodha": [0.0]}}
        self.assertAlmostEqual(s.fill(200.0, "entry", "dhan"), 202.0)
        self.assertAlmostEqual(s.fill(200.0, "entry", "zerodha"), 198.0)

    def test_the_shipped_model_has_both_brokers(self):
        model = json.load(open(practice.FILL_MODEL_PATH))
        for stage in ("entry_pct", "exit_pct"):
            for broker in ("dhan", "zerodha"):
                self.assertTrue(model[stage][broker], f"{stage}/{broker} is empty")


class NeverABroker(_Archive):
    def test_any_broker_call_refuses(self):
        s = self.session()
        with self.assertRaises(RuntimeError):
            s.engine.dhan.place_order(security_id="1")
        with self.assertRaises(RuntimeError):
            s.engine.dhan.place_super_order()

    def test_the_live_engine_keeps_its_own_pace(self):
        live = scalp.ScalpEngine.__new__(scalp.ScalpEngine)
        self.assertIsNone(live._monitor_interval_override_sec())
        self.assertIsNone(live._st_check_override_sec())
        s = self.session(speed=10)
        self.assertAlmostEqual(s.engine._monitor_interval_override_sec(), scalp.SCALP_MONITOR_INTERVAL_SEC / 10)
        self.assertAlmostEqual(s.engine._st_check_override_sec(), scalp.SCALP_ST_CHECK_SEC / 10)

    def test_a_practice_engine_never_touches_the_feed_throttle(self):
        s = self.session()
        with patch.object(scalp, "enable_marketfeed_throttle", side_effect=AssertionError("touched")) as hook:
            s.engine._sync_marketfeed_throttle()
            s.engine.stop()
            hook.assert_not_called()


class ASundaySession(_Archive):
    """01-Feb-2026, Budget day, was a Sunday session. The engine's market-hours
    gate read the replay's weekday and paused all monitoring: the position's
    price froze at its entry (Phil, 07-Nov-2026)."""

    def test_the_monitor_watches_a_replayed_weekend_day(self):
        sunday = datetime(2026, 2, 1, 9, 20)
        with practice.replay_clock(type("S", (), {"now": lambda self: sunday})()):
            self.assertTrue(scalp._scalp_market_watch_open())
        self.assertFalse(scalp._scalp_market_watch_open(now=sunday.replace(tzinfo=scalp.IST)))  # live keeps its weekend


class TheHistoryButtons(_Archive):
    def test_delete_and_chart_a_closed_practice_trade(self):
        import app as app_module

        s = self.session()
        s.closed_trades.extend([{"trade_id": 1, "pnl": 10.0}, {"trade_id": 2, "pnl": -5.0}])
        s.engine.closed_trades = list(s.closed_trades)
        self.assertEqual(app_module._practice_forget_trades(s, {1}), 1)
        self.assertEqual([t["trade_id"] for t in s.closed_trades], [2])
        self.assertEqual([t["trade_id"] for t in s.engine.closed_trades], [2])
        self.assertEqual(s.summary()["trades"], 1)


if __name__ == "__main__":
    unittest.main()
