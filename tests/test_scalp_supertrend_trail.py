"""The Supertrend trailing stop on a Scalp trade.

Phil, 2026-10-21: "Can you make the supertrend as the trailing stoploss and
exit if it closes below supertrend?" -- on the option's 5-minute candles.
Phil, 2026-11-07: "it hits the SL when it touches the SL.. but it has to close
below" -- so the line is NOT a stop: only a 5m close through it exits.
"""

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest import mock

from scalp import IST, ScalpEngine, ScalpTrade, scalp_regroup_bars, scalp_st_minutes, scalp_supertrend


def _bars(closes, start=None, minutes=5):
    """Bars ending well before now, so every one has closed."""
    step = minutes * 60
    start = start or (datetime.now(IST) - timedelta(minutes=minutes * (len(closes) + 2)))
    t0 = int(start.timestamp()) // step * step
    return [{"t": t0 + i * step, "h": c + 2, "l": c - 2, "c": c} for i, c in enumerate(closes)]


class TheLineIsTheCharts(unittest.TestCase):
    def test_a_rising_series_is_up_and_the_line_sits_under_it(self):
        line = scalp_supertrend(_bars([100 + i for i in range(30)]))
        self.assertTrue(line)
        self.assertEqual(line[-1]["dir"], 1)
        self.assertLess(line[-1]["value"], 129)

    def test_a_drop_through_the_line_flips_it(self):
        closes = [100 + i for i in range(30)] + [100]
        self.assertEqual(scalp_supertrend(_bars(closes))[-1]["dir"], -1)

    def test_too_few_bars_is_no_line(self):
        self.assertEqual(scalp_supertrend(_bars([100] * 10)), [])


def _engine(closes, *, side="BUY", sl=90.0, price=None, armed=False):
    engine = ScalpEngine(mock.MagicMock())
    engine._log = lambda *a: None
    trade = ScalpTrade(
        trade_id=7, underlying="NIFTY", strike=22700, option_type="CE", expiry="2099-01-01",
        transaction_type=side, lots=1, lot_size=65, entry_premium=110.0, mode="paper",
        st_trail=True,
    )  # fmt: skip
    trade.sl_premium = sl
    trade.current_premium = price if price is not None else closes[-1]
    trade.entry_time = (datetime.now(IST) - timedelta(minutes=5 * (len(closes) + 2))).replace(tzinfo=None)
    trade.st_trail_armed = armed
    engine.open_trades[7] = trade
    bars = _bars(closes)

    async def fake_bars(_trade):
        return bars

    engine._st_trail_bars = fake_bars
    return engine, trade


class TheTrail(unittest.TestCase):
    def test_the_stop_is_never_moved_onto_the_line(self):
        # A touch of the line must not exit, so the touch stop stays his own.
        engine, trade = _engine([100 + i for i in range(30)], sl=90.0, price=131.0)
        calls = []

        async def spy(*a, **kw):
            calls.append(kw)

        engine.update_trade_targets = spy
        asyncio.run(engine._st_trail_check())
        self.assertEqual(trade.sl_premium, 90.0)
        self.assertEqual(calls, [])  # nothing goes to the broker
        self.assertTrue(trade.st_trail_armed)

    def test_the_st_sl_rides_the_line_up_with_each_bar(self):
        # "now the ST is not at all moving on the SL": st_stop follows it.
        closes = [100 + i for i in range(30)]
        engine, trade = _engine(closes, sl=90.0, price=131.0)
        asyncio.run(engine._st_trail_check())
        first = trade.st_stop
        line = scalp_supertrend(_bars(closes))[-1]["value"]
        self.assertGreater(first, 90.0)
        self.assertLessEqual(first, line)
        self.assertGreater(line - first, -0.06)
        bars = _bars(closes + [131, 133, 135])

        async def fake_bars(_trade):
            return bars

        engine._st_trail_bars = fake_bars
        trade.st_last_bar = 0
        asyncio.run(engine._st_trail_check())
        self.assertGreater(trade.st_stop, first)
        self.assertEqual(trade.sl_premium, 90.0)

    def test_a_wick_through_the_line_that_closes_above_does_not_exit(self):
        closes = [100 + i for i in range(30)]
        engine, trade = _engine(closes, armed=True, price=100.0)
        line = scalp_supertrend(_bars(closes))[-1]["value"]
        bars = _bars(closes)
        bars[-1]["l"] = line - 5  # the last bar dipped under the line, closed above
        trade.current_premium = line - 5

        async def fake_bars(_trade):
            return bars

        engine._st_trail_bars = fake_bars
        closed = []

        async def close(t, reason, **kw):
            closed.append(reason)

        engine._close_trade = close
        asyncio.run(engine._st_trail_check())
        self.assertEqual(closed, [])
        self.assertIsNone(trade.check_exit(trade.current_premium))

    def test_a_close_below_the_line_exits_an_armed_trade(self):
        engine, trade = _engine([100 + i for i in range(30)] + [100], armed=True)
        closed = []

        async def close(t, reason, **kw):
            closed.append(reason)

        engine._close_trade = close
        asyncio.run(engine._st_trail_check())
        self.assertEqual(closed, ["st_close"])

    def test_a_trade_entered_against_the_line_is_not_thrown_out(self):
        # Never armed: the trend was never on its side, so no exit.
        engine, trade = _engine([130 - i for i in range(30)], armed=False)
        closed = []

        async def close(t, reason, **kw):
            closed.append(reason)

        engine._close_trade = close
        asyncio.run(engine._st_trail_check())
        self.assertEqual(closed, [])

    def test_a_bar_is_acted_on_once(self):
        engine, trade = _engine([100 + i for i in range(30)] + [100], armed=True)
        closed = []

        async def close(t, reason, **kw):
            closed.append(reason)

        engine._close_trade = close
        asyncio.run(engine._st_trail_check())
        asyncio.run(engine._st_trail_check())
        self.assertEqual(closed, ["st_close"])

    def test_it_survives_a_restart(self):
        trade = ScalpTrade(
            trade_id=7, underlying="NIFTY", strike=22700, option_type="CE", expiry="2099-01-01",
            transaction_type="BUY", lots=1, lot_size=65, entry_premium=110.0, st_trail=True,
        )  # fmt: skip
        trade.st_trail_armed, trade.st_last_bar = True, 1792000000
        import app

        trade.st_tf, trade.st_tf_since, trade.st_stop = 3, 1792000100.0, 151.25
        back = app._restore_scalp_trade_from_payload(trade.to_dict())
        self.assertEqual(back.st_stop, 151.25)
        self.assertEqual((back.st_trail, back.st_trail_armed, back.st_last_bar), (True, True, 1792000000))
        self.assertEqual((back.st_tf, back.st_tf_since), (3, 1792000100.0))


class TheChartsTimeframe(unittest.TestCase):
    """Phil, 2026-11-07: "If I am viewing 3m chart, make the ST to be 3m and vice versa"."""

    def test_the_charts_labels_read_as_minutes(self):
        self.assertEqual([scalp_st_minutes(v) for v in ("1m", "3m", "5m", "15m", "1h")], [1, 3, 5, 15, 60])
        self.assertEqual((scalp_st_minutes("7m"), scalp_st_minutes(""), scalp_st_minutes("x")), (5, 5, 5))

    def test_3m_bars_are_built_from_1m_on_the_0915_grid(self):
        start = datetime(2026, 11, 6, 9, 15, tzinfo=IST)
        ones = _bars([100, 101, 99, 102, 103, 104], start=start, minutes=1)
        threes = scalp_regroup_bars(ones, 3)
        self.assertEqual([b["t"] for b in threes], [ones[0]["t"], ones[3]["t"]])
        self.assertEqual((threes[0]["h"], threes[0]["l"], threes[0]["c"]), (103, 97, 99))

    def test_a_3m_trade_exits_on_a_3m_close_below(self):
        closes = [100 + i for i in range(30)] + [100]
        engine, trade = _engine(closes, armed=True)
        trade.st_tf = 3
        bars = _bars(closes, minutes=3)

        async def fake_bars(_trade):
            return bars

        engine._st_trail_bars = fake_bars
        closed = []

        async def close(t, reason, **kw):
            closed.append(reason)

        engine._close_trade = close
        asyncio.run(engine._st_trail_check())
        self.assertEqual(closed, ["st_close"])

    def test_switching_the_chart_moves_the_open_trades(self):
        engine, trade = _engine([100 + i for i in range(30)])
        other = ScalpTrade(
            trade_id=8, underlying="NIFTY", strike=22700, option_type="PE", expiry="2099-01-01",
            transaction_type="BUY", lots=1, lot_size=65, entry_premium=110.0, st_trail=False,
        )  # fmt: skip
        engine.open_trades[8] = other
        trade.st_last_bar = 123
        result = engine.set_st_timeframe("3m")
        self.assertEqual(result["trades"], [7])
        self.assertEqual((trade.st_tf, trade.st_last_bar), (3, 0))
        self.assertGreater(trade.st_tf_since, 0)
        self.assertEqual(other.st_tf, 5)  # no Supertrend exit: left alone
        self.assertEqual(engine.set_st_timeframe("3m")["trades"], [])  # already there

    def test_a_bar_that_closed_before_the_switch_never_exits(self):
        # The new bar's trend is already against the trade, but that close
        # happened before the switch: only a later one may exit.
        closes = [100 + i for i in range(30)] + [100]
        engine, trade = _engine(closes, armed=True)
        engine.set_st_timeframe("3m")
        bars = _bars(closes, minutes=3)

        async def fake_bars(_trade):
            return bars

        engine._st_trail_bars = fake_bars
        closed = []

        async def close(t, reason, **kw):
            closed.append(reason)

        engine._close_trade = close
        asyncio.run(engine._st_trail_check())
        self.assertEqual(closed, [])


class TheChartDrawsIt(unittest.TestCase):
    def test_the_st_sl_line_is_drawn_and_read_fresh_off_a_cached_chart(self):
        import app

        cached = {
            "lines": [{"label": "STOP", "price": 90.0}, {"label": "ST SL", "price": 120.0}, {"label": "PP", "price": 1}]
        }
        trade = {"st_trail": True, "st_stop": 125.5, "sl_premium": 92.0, "target_premium": 0, "current_premium": 0}
        lines = app._scalp_option_chart_with_live_price(cached, trade)["lines"]
        self.assertEqual([(ln["label"], ln["price"]) for ln in lines], [("ST SL", 125.5), ("STOP", 92.0), ("PP", 1)])
        self.assertEqual(app._scalp_st_stop_lines({"st_trail": False, "st_stop": 125.5}), [])
        # A price alone (the practice chart's call) leaves the built lines be.
        built = {"lines": [{"label": "STOP", "price": 90.0}, {"label": "ST SL", "price": 120.0}]}
        kept = app._scalp_option_chart_with_live_price(built, {"current_premium": 0})["lines"]
        self.assertEqual([(ln["label"], ln["price"]) for ln in kept], [("STOP", 90.0), ("ST SL", 120.0)])


if __name__ == "__main__":
    unittest.main()
