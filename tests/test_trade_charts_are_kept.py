"""A closed live trade's chart is kept on the server, opened or not.

Phil, 13-Oct-2026: "After some years it should not go to Dhan to fetch the
chart.... Make it on the server". The journal route already kept a chart the
first time someone OPENED it -- which is why an old trade took 30 to 90 seconds
on its first open (Dhan serves an expired contract one strike at a time) and no
time on its second. Two things were missing:

  * a trade nobody opens was never kept, and years on Dhan may not have it;
  * a kept chart still needed a broker connection, and asked Dhan again for
    the index to redraw the Supertrend turns.

So a keeper walks every live book's closed trades outside the trading day and
builds whichever chart is not on disk; and a chart that is on disk is answered
with no broker at all.
"""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

from fastapi import HTTPException  # noqa: E402

import app as app_module  # noqa: E402

IST = app_module.IST
UID = 7
ENTRY = datetime(2026, 10, 12, 11, 0, tzinfo=IST)
EXIT = datetime(2026, 10, 12, 11, 42, tzinfo=IST)
EVENING = datetime(2026, 10, 12, 16, 5, tzinfo=IST)  # a Monday, after the close


def _trade(trade_id=4, entry=ENTRY, exit_=EXIT, **more):
    return {
        "id": trade_id,
        "symbol": "NIFTY 22650 PE",
        "underlying": "NIFTY",
        "strike": 22650,
        "option_type": "PE",
        "expiry": "2026-10-13",
        "transaction_type": "BUY",
        "quantity": 130,
        "entry_time": entry,
        "exit_time": exit_,
        "entry_premium": 245.05,
        "exit_premium": 289.15,
        "pnl": 5599.61,
        **more,
    }


def _candles(day=ENTRY):
    start = int(day.replace(hour=9, minute=15).timestamp())
    return [{"t": start + i * 300, "o": 240 + i, "h": 244 + i, "l": 238 + i, "c": 242 + i} for i in range(40)]


class _Dhan:
    """Counts what it is asked. `dead` is Dhan years later: it has nothing."""

    def __init__(self):
        self.candle_calls = 0
        self.turn_calls = 0
        self.dead = False


class _Kept(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dhan = _Dhan()
        self.engine = SimpleNamespace(
            dhan=self.dhan, strategy={"run_name": "PE_NoTarget"}, closed_trades=[], positions=[]
        )
        app_module.live_engines.clear()
        app_module.live_engines[UID] = {"PE_NoTarget": self.engine}
        app_module._trade_chart_keep_failed.clear()
        dhan = self.dhan

        async def candles(broker_client, trade, *, cache_path="", **_kw):
            dhan.candle_calls += 1
            if dhan.dead:
                raise HTTPException(status_code=404, detail="Dhan has no candles left for this contract")
            rows = _candles()
            if cache_path:
                app_module._write_trade_chart_cache(cache_path, rows)
            return rows

        async def turns(broker_client, *_a, **_kw):
            dhan.turn_calls += 1
            return None if dhan.dead else {"flips": [{"t": int(EXIT.timestamp()) - 360, "dir": "down"}]}

        self.patches = [
            patch.object(app_module, "_engine_state_dir", lambda user_id, create=True: self.tmp.name),
            patch.object(app_module, "_contract_chart_candles", candles),
            patch.object(app_module, "_index_supertrend_flips", turns),
            patch.object(app_module, "_strategy_supertrend_spec", lambda strategy: (10, 2.7, 3) if strategy else None),
            patch.object(app_module, "_live_run_history_trades", lambda run_id, user_id=None: []),
            patch.object(app_module, "_engine_restore_owner_is_active_instance", lambda: True),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        app_module.live_engines.clear()
        app_module._trade_chart_keep_failed.clear()
        self.tmp.cleanup()

    def build(self, trade, timeframe="5m", broker=None, now=EVENING):
        async def default_broker():
            return self.dhan

        with patch.object(app_module, "datetime", _Clock(now)):
            return asyncio.run(
                app_module._build_trade_chart(
                    UID, "PE_NoTarget", trade, self.engine, broker or default_broker, timeframe
                )
            )

    def keep(self, now=EVENING):
        with patch.object(app_module, "datetime", _Clock(now)):
            return asyncio.run(app_module._keep_one_trade_chart(UID, now))

    def files(self):
        folder = os.path.join(self.tmp.name, "trade_charts")
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def _Clock(now):
    """app.datetime, with `now` pinned."""

    class Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    return Pinned


class AKeptChartNeedsNoBroker(_Kept):
    def test_the_first_build_asks_dhan_and_the_second_does_not(self):
        first = self.build(_trade())
        self.assertEqual((self.dhan.candle_calls, self.dhan.turn_calls), (1, 1))
        again = self.build(_trade())
        self.assertEqual((self.dhan.candle_calls, self.dhan.turn_calls), (1, 1))
        self.assertEqual(again["candles"], first["candles"])
        self.assertEqual(again["supertrend"], first["supertrend"])

    def test_years_later_it_opens_with_no_broker_and_a_dead_archive(self):
        kept = self.build(_trade())
        self.dhan.dead = True

        async def no_broker():
            return None

        years_on = self.build(_trade(), broker=no_broker, now=datetime(2031, 3, 4, 10, 0, tzinfo=IST))
        self.assertEqual(years_on["status"], "ok")
        self.assertEqual(years_on["candles"], kept["candles"])
        self.assertEqual(years_on["supertrend"], kept["supertrend"])
        self.assertEqual(years_on["exits"][0]["price"], 289.15)

    def test_a_chart_that_was_never_kept_still_says_it_needs_the_broker(self):
        async def no_broker():
            return None

        with self.assertRaises(HTTPException) as caught:
            self.build(_trade(), broker=no_broker)
        self.assertEqual(caught.exception.status_code, 503)

    def test_a_trade_still_inside_its_day_is_not_kept(self):
        self.build(_trade(), now=datetime(2026, 10, 12, 12, 30, tzinfo=IST))
        self.assertEqual(self.files(), [])
        self.build(_trade(), now=datetime(2026, 10, 12, 12, 31, tzinfo=IST))
        self.assertEqual(self.dhan.candle_calls, 2)  # asked afresh while the day is open

    def test_complete_means_the_day_is_over(self):
        ts = int(EXIT.timestamp())
        done = app_module._trade_chart_is_complete
        self.assertFalse(done(ts, datetime(2026, 10, 12, 15, 45, tzinfo=IST)))
        self.assertTrue(done(ts, datetime(2026, 10, 12, 15, 50, tzinfo=IST)))
        self.assertTrue(done(ts, datetime(2026, 10, 13, 9, 0, tzinfo=IST)))
        self.assertFalse(done(None, EVENING))


class TheKeeperSavesWhatNobodyOpened(_Kept):
    def test_it_keeps_all_three_timeframes_one_a_pass(self):
        self.engine.closed_trades = [_trade()]
        self.assertEqual([self.keep() for _ in range(4)], ["kept", "kept", "kept", "nothing-owed"])
        self.assertEqual(
            self.files(),
            sorted(
                f"PE_NoTarget__4_{int(ENTRY.timestamp())}__{kind}.json"
                for kind in ("5", "15", "60", "st5", "st15", "st60")
            ),
        )

    def test_every_trades_5m_comes_before_anyones_15m_newest_first(self):
        older = _trade(3, ENTRY - timedelta(days=3), EXIT - timedelta(days=3))
        self.engine.closed_trades = [older, _trade()]
        owed = app_module._trade_charts_still_owed(UID, EVENING)
        self.assertEqual(
            [(row["trade"]["id"], row["timeframe"]) for row in owed],
            [(4, "5m"), (3, "5m"), (4, "15m"), (3, "15m"), (4, "1h"), (3, "1h")],
        )

    def test_todays_trade_waits_for_the_close_and_an_open_one_is_never_owed(self):
        self.engine.closed_trades = [_trade(), _trade(5, ENTRY, None)]
        mid_session = datetime(2026, 10, 12, 13, 0, tzinfo=IST)
        self.assertEqual(app_module._trade_charts_still_owed(UID, mid_session), [])
        self.assertEqual({row["trade"]["id"] for row in app_module._trade_charts_still_owed(UID, EVENING)}, {4})

    def test_a_stopped_books_history_file_is_walked_too(self):
        app_module.live_engines[UID] = {}
        open(os.path.join(self.tmp.name, "live_history_Old_Book.json"), "w").write("[]")
        with patch.object(app_module, "_live_run_history_trades", lambda run_id, user_id=None: [_trade(9)]):
            owed = app_module._trade_charts_still_owed(UID, EVENING)
        self.assertEqual({row["run_id"] for row in owed}, {"Old_Book"})
        self.assertEqual(len(owed), 3)  # no engine, so no Supertrend rule to keep turns for

    def test_a_chart_dhan_cannot_serve_is_asked_again_later_not_every_minute(self):
        self.engine.closed_trades = [_trade()]
        self.dhan.dead = True
        self.assertEqual(self.keep(), "failed")
        self.assertEqual(self.dhan.candle_calls, 1)
        # The next pass moves on to the next chart instead of hammering this one.
        self.assertEqual(self.keep(), "failed")
        self.assertEqual(self.keep(), "failed")
        self.assertEqual(self.keep(), "nothing-owed")
        self.assertEqual(self.dhan.candle_calls, 3)  # 5m, 15m and 1h, once each

    def test_it_only_works_outside_the_trading_day(self):
        may = app_module._trade_chart_keeper_may_work
        self.assertFalse(may(datetime(2026, 10, 12, 9, 20, tzinfo=IST)))
        self.assertFalse(may(datetime(2026, 10, 12, 15, 35, tzinfo=IST)))
        self.assertTrue(may(datetime(2026, 10, 12, 15, 50, tzinfo=IST)))
        self.assertTrue(may(datetime(2026, 10, 12, 7, 0, tzinfo=IST)))
        self.assertTrue(may(datetime(2026, 10, 17, 11, 0, tzinfo=IST)))  # a Saturday


if __name__ == "__main__":
    unittest.main()
