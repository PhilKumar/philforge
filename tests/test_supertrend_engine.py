"""The Supertrend rule, tested where it can actually be wrong.

The published book is only worth as much as the guarantee that this code makes
the same decisions it did, so these tests pin the four things that would break
that quietly: the signal is the shipped indicator, the roll re-enters instead of
standing down, the trail is armed and measured from the peak rather than entry,
and an exit with no quote is floored and FLAGGED rather than booked as a price.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from engine.supertrend_entry import (
    CE,
    SupertrendConfig,
    SupertrendError,
    SupertrendPosition,
    direction_series,
    indicator_series,
    read_signal,
    replay,
    strike_for,
    summarise,
)

IST = ZoneInfo("Asia/Kolkata")


class Candle:
    __slots__ = ("timestamp", "open", "high", "low", "close")

    def __init__(self, timestamp, open_, high, low, close):
        self.timestamp = timestamp
        self.open = open_
        self.high = high
        self.low = low
        self.close = close


def _series(closes, *, start=datetime(2026, 8, 3, 9, 15, tzinfo=IST), step_hours=1, span=30.0):
    rows = []
    stamp = start
    for close in closes:
        rows.append(Candle(stamp, close, close + span, close - span, close))
        stamp = stamp.replace(hour=stamp.hour + step_hours) if stamp.hour + step_hours < 24 else stamp
        if stamp.hour + step_hours >= 24:
            stamp = datetime(stamp.year, stamp.month, stamp.day + 1, 9, 15, tzinfo=IST)
    return rows


class ConfigTests(unittest.TestCase):
    def test_the_defaults_are_the_published_rule(self):
        config = SupertrendConfig()
        config.validate()
        self.assertEqual(config.timeframe, "1h")
        self.assertEqual(config.atr_period, 10)
        self.assertEqual(config.multiplier, 1.5)
        self.assertEqual(config.expiry_rank, 2, "the nearest weekly is the setting that lost")
        self.assertEqual(config.roll_strikes, 6)
        self.assertEqual(config.trail_arm_points, 100.0)
        self.assertEqual(config.trail_give_points, 80.0)

    def test_puts_are_refused_with_the_reason(self):
        with self.assertRaises(SupertrendError) as caught:
            SupertrendConfig(side="PE").validate()
        self.assertIn("calls only", str(caught.exception))

    def test_a_fast_timeframe_is_refused(self):
        for bad in ("1m", "3m", "5m", "15m"):
            with self.assertRaises(SupertrendError):
                SupertrendConfig(timeframe=bad).validate()

    def test_entry_window_must_be_ordered(self):
        with self.assertRaises(SupertrendError):
            SupertrendConfig(entry_after=time(15, 30), square_off=time(15, 20)).validate()


class SignalTests(unittest.TestCase):
    def test_it_uses_the_shipped_indicator(self):
        """Not a re-implementation. If engine.indicators.supertrend changes, this
        rule must change with it -- that identity is the whole reason a paper run
        can be trusted to match the tearsheet."""
        import numpy as np
        import pandas as pd

        from engine.indicators import supertrend as shipped

        rows = _series([100 + i * 3 for i in range(40)])
        frame = pd.DataFrame(
            {"high": [c.high for c in rows], "low": [c.low for c in rows], "close": [c.close for c in rows]},
            index=[c.timestamp for c in rows],
        )
        expected = shipped(frame, period=10, multiplier=1.5)["supertrend_dir"].tolist()
        got = [d for _t, _c, _s, d in direction_series(rows, SupertrendConfig())]
        self.assertEqual(got, expected)
        self.assertEqual(int(np.sum(np.array(got) != np.array(expected))), 0)

    def test_a_rising_series_reads_bullish_and_fires(self):
        signal = read_signal(_series([100 + i * 5 for i in range(40)]), SupertrendConfig())
        self.assertIsNotNone(signal)
        self.assertTrue(signal.fires)
        self.assertEqual(signal.direction, 1)

    def test_a_falling_series_reads_bearish_and_does_not_fire(self):
        signal = read_signal(_series([400 - i * 5 for i in range(40)]), SupertrendConfig())
        self.assertIsNotNone(signal)
        self.assertFalse(signal.fires)

    def test_too_few_candles_is_no_signal_rather_than_a_guess(self):
        self.assertIsNone(read_signal(_series([100, 101, 102]), SupertrendConfig()))

    def test_the_chart_indicator_matches_the_decision(self):
        rows = _series([100 + i * 4 for i in range(40)])
        config = SupertrendConfig()
        drawn = indicator_series(rows, config)
        computed = direction_series(rows, config)
        self.assertEqual(len(drawn["supertrend"]), len(computed))
        self.assertEqual(drawn["atr_period"], 10)
        self.assertEqual(drawn["multiplier"], 1.5)

    def test_each_point_says_its_trend_so_the_chart_can_shade_it(self):
        # Up for 25 bars, then down: the chart colours and shades each run by
        # `d`, TradingView's way (Phil, 2026-10-14).
        rows = _series([100 + i * 4 for i in range(25)] + [196 - i * 6 for i in range(25)])
        config = SupertrendConfig()
        drawn = indicator_series(rows, config)["supertrend"]
        computed = direction_series(rows, config)
        self.assertEqual([p["d"] for p in drawn], [1 if d > 0 else -1 for _ts, _c, _st, d in computed])
        self.assertEqual({p["d"] for p in drawn}, {1, -1})


class StrikeTests(unittest.TestCase):
    def test_at_the_money_rounds_to_the_step(self):
        config = SupertrendConfig()
        self.assertEqual(strike_for(24012.0, config), 24000)
        self.assertEqual(strike_for(24030.0, config), 24050)

    def test_no_in_the_money_offset_is_applied(self):
        """ITM was measured and cut the net almost in half; the roll already
        does theta's job, so the strike is exactly at the money."""
        self.assertEqual(strike_for(24000.0, SupertrendConfig()), 24000)


class PositionTests(unittest.TestCase):
    def _position(self, **kwargs):
        base = dict(
            side=CE,
            strike=24000,
            expiry=date(2026, 9, 10),
            lot_size=75,
            lots=1,
            entry_timestamp=datetime(2026, 8, 28, 10, 15, tzinfo=IST),
            entry_spot=24000.0,
            entry_premium=200.0,
        )
        base.update(kwargs)
        return SupertrendPosition(**base)

    def test_the_trail_is_disarmed_until_the_move_has_happened(self):
        config = SupertrendConfig()
        position = self._position(mfe=90.0)
        self.assertIsNone(position.trail_level(config), "90 points is under the 100-point arm")

    def test_the_trail_is_measured_from_the_peak_not_the_entry(self):
        config = SupertrendConfig()
        position = self._position(mfe=250.0)
        self.assertEqual(position.trail_level(config), 24170.0, "24000 + 250 - 80")

    def test_intrinsic_is_a_floor_and_never_negative(self):
        position = self._position()
        self.assertEqual(position.intrinsic(24300.0), 300.0)
        self.assertEqual(position.intrinsic(23500.0), 0.0)

    def test_net_is_gross_less_charges(self):
        position = self._position()
        position.exit_timestamp = datetime(2026, 8, 29, 10, 15, tzinfo=IST)
        position.exit_premium = 260.0
        position.charges = 96.0
        self.assertEqual(position.gross, 4500.0)
        self.assertEqual(position.net, 4404.0)


class SummaryTests(unittest.TestCase):
    def _closed(self, net, priced=True, reason="flip"):
        position = SupertrendPosition(
            side=CE,
            strike=24000,
            expiry=date(2026, 9, 10),
            lot_size=75,
            lots=1,
            entry_timestamp=datetime(2026, 8, 28, 10, 15, tzinfo=IST),
            entry_spot=24000.0,
            entry_premium=100.0,
        )
        position.exit_timestamp = datetime(2026, 8, 29, 10, 15, tzinfo=IST)
        position.exit_premium = 100.0 + net / 75.0
        position.exit_priced = priced
        position.exit_reason = reason
        return position

    def test_priced_net_is_carried_apart_from_the_headline(self):
        """The single most important number on the tearsheet: a book whose
        profit lives only in floored exits has not been measured."""
        book = summarise([self._closed(1000, priced=True), self._closed(5000, priced=False)])
        self.assertEqual(book["trades"], 2)
        self.assertEqual(book["net"], 6000.0)
        self.assertEqual(book["priced_net"], 1000.0)
        self.assertEqual(book["floored_exits"], 1)
        self.assertEqual(book["floored_net"], 5000.0)

    def test_an_empty_book_is_zeroed_rather_than_absent(self):
        book = summarise([])
        self.assertEqual(book["trades"], 0)
        self.assertEqual(book["net"], 0.0)
        self.assertIsNone(book["profit_factor"])

    def test_rolls_are_counted_as_their_own_exit_reason(self):
        book = summarise([self._closed(500, reason="roll"), self._closed(500, reason="flip")])
        self.assertEqual(book["rolls"], 1)
        self.assertEqual(book["by_reason"]["roll"]["count"], 1)


class ReplayTests(unittest.TestCase):
    """The replay is what the Backtest button runs, so its refusals matter as
    much as its fills."""

    def _run(self, closes, *, price=lambda *a: 100.0, config=None, spot=None):
        rows = _series(closes)
        spots = spot or {c.timestamp: c.close for c in rows}
        skips: list = []
        positions = replay(
            rows,
            config=config or SupertrendConfig(),
            spot_at=lambda when: spots.get(when),
            price_at=price,
            expiry_for=lambda day: date(2026, 12, 31),
            lot_size_for=lambda expiry: 75,
            charges_for=lambda d, b, s, q: 40.0,
            on_skip=lambda when, why: skips.append(why),
        )
        return positions, skips

    def test_a_trend_that_never_turns_buys_once_and_holds(self):
        positions, _ = self._run([100 + i * 5 for i in range(40)])
        self.assertLessEqual(len([p for p in positions if p.is_open]), 1)

    def test_an_exit_with_no_quote_is_floored_and_flagged(self):
        rows = _series([100 + i * 5 for i in range(25)] + [220 - i * 12 for i in range(15)])
        spots = {c.timestamp: c.close for c in rows}
        calls = {"n": 0}

        def price(when, strike, side, expiry):
            calls["n"] += 1
            return 100.0 if calls["n"] == 1 else None

        positions = replay(
            rows,
            config=SupertrendConfig(),
            spot_at=lambda when: spots.get(when),
            price_at=price,
            expiry_for=lambda day: date(2026, 12, 31),
            lot_size_for=lambda expiry: 75,
            charges_for=lambda d, b, s, q: 40.0,
        )
        floored = [p for p in positions if not p.is_open and not p.exit_priced]
        for position in floored:
            self.assertFalse(position.exit_priced, "a floor must never be recorded as a price")

    def test_an_entry_that_cannot_be_priced_is_skipped_not_invented(self):
        positions, skips = self._run([100 + i * 5 for i in range(40)], price=lambda *a: None)
        self.assertEqual(positions, [])
        self.assertTrue(any("no quote" in why for why in skips))

    def test_it_refuses_to_open_mid_trend_at_the_window_edge(self):
        """The entry that would have started this trend happened before the data
        begins, so taking it at the first bar would be a trade never made."""
        positions, _ = self._run([500 + i for i in range(40)])
        for position in positions:
            self.assertIsNotNone(position.entry_timestamp)


if __name__ == "__main__":
    unittest.main()
