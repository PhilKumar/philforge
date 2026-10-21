"""The Supertrend trailing stop on a Scalp trade.

Phil, 2026-10-21: "Can you make the supertrend as the trailing stoploss and
exit if it closes below supertrend?" -- on the option's 5-minute candles, the
broker stop moved up to the line AND an exit on a 5m close below it.
"""

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest import mock

from scalp import IST, ScalpEngine, ScalpTrade, scalp_supertrend


def _bars(closes, start=None):
    """5-minute bars ending well before now, so every one has closed."""
    start = start or (datetime.now(IST) - timedelta(minutes=5 * (len(closes) + 2)))
    t0 = int(start.timestamp()) // 300 * 300
    return [{"t": t0 + i * 300, "h": c + 2, "l": c - 2, "c": c} for i, c in enumerate(closes)]


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
    def test_the_stop_moves_up_to_the_line_and_never_down(self):
        engine, trade = _engine([100 + i for i in range(30)], sl=90.0, price=131.0)
        asyncio.run(engine._st_trail_check())
        line = scalp_supertrend(_bars([100 + i for i in range(30)]))[-1]["value"]
        self.assertGreater(trade.sl_premium, 90.0)
        self.assertLessEqual(trade.sl_premium, line)
        self.assertTrue(trade.st_trail_armed)
        # A stop already above the line is left alone.
        trade.sl_premium, trade.st_last_bar = 130.0, 0
        asyncio.run(engine._st_trail_check())
        self.assertEqual(trade.sl_premium, 130.0)

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
        engine, trade = _engine([100 + i for i in range(30)], sl=90.0, price=131.0)
        calls = []
        real = engine.update_trade_targets

        async def spy(*a, **kw):
            calls.append(kw)
            return await real(*a, **kw)

        engine.update_trade_targets = spy
        asyncio.run(engine._st_trail_check())
        asyncio.run(engine._st_trail_check())
        self.assertEqual(len(calls), 1)

    def test_it_survives_a_restart(self):
        trade = ScalpTrade(
            trade_id=7, underlying="NIFTY", strike=22700, option_type="CE", expiry="2099-01-01",
            transaction_type="BUY", lots=1, lot_size=65, entry_premium=110.0, st_trail=True,
        )  # fmt: skip
        trade.st_trail_armed, trade.st_last_bar = True, 1792000000
        import app

        back = app._restore_scalp_trade_from_payload(trade.to_dict())
        self.assertEqual((back.st_trail, back.st_trail_armed, back.st_last_bar), (True, True, 1792000000))


if __name__ == "__main__":
    unittest.main()
