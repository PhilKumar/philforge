"""Gap Carry reads the candle that has CLOSED by 15:10, and buys at 15:10.

2026-09-15: the first live entry fired at 15:15:03. Bars are labelled by their
start, and the rule read the bar starting at or before 15:10 -- the 15:10-15:15
bar, which only exists at 15:15. The replay priced the same entry at 15:10, five
minutes before its signal existed. Over 2021-2026 on recorded premiums:

    published, read 15:10 bar, buy 15:10   179 trades  Rs 2,77,173  (impossible)
    live then, read 15:10 bar, buy 15:15   179 trades  Rs 1,69,027
    now,       read 15:05 bar, buy 15:10   168 trades  Rs 2,00,264  no losing year

Phil chose the last. These pin it so the replay and the live loop cannot drift
back apart.
"""

import os
import unittest
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402
from engine import gap_carry  # noqa: E402
from engine.gap_carry import PE, GapCarryConfig, closes_by, read_signal
from engine.gap_carry_paper import HOLDING, WAITING, GapCarryPaper

DAY = date(2026, 3, 10)


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


def _day(closes: list, day: date = DAY) -> list:
    base = datetime.combine(day, time(9, 15))
    return [Candle(base + timedelta(minutes=5 * i), c, c, c, c) for i, c in enumerate(closes)]


def _index_of(hhmm: str) -> int:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return ((hh * 60 + mm) - (9 * 60 + 15)) // 5


FALLING = [24500.0 - i for i in range(80)]


class WhichBarDecides(unittest.TestCase):
    def test_the_15_05_bar_closes_by_15_10(self):
        cfg = GapCarryConfig(timeframe="5m")
        self.assertTrue(closes_by(datetime.combine(DAY, time(15, 5)), cfg))
        self.assertFalse(closes_by(datetime.combine(DAY, time(15, 10)), cfg), "that bar runs to 15:15")

    def test_a_slower_chart_steps_back_a_whole_bar(self):
        cfg = GapCarryConfig(timeframe="15m")
        self.assertTrue(closes_by(datetime.combine(DAY, time(14, 45)), cfg))
        self.assertFalse(closes_by(datetime.combine(DAY, time(15, 0)), cfg))

    def test_the_reading_is_the_15_05_bar(self):
        sig = read_signal(_day(FALLING), GapCarryConfig(), at=datetime.combine(DAY, time(15, 10)))
        self.assertEqual(sig.timestamp.time(), time(15, 5))

    def test_the_15_10_bar_cannot_move_the_decision(self):
        """A violent spike in the 15:10-15:15 bar is five minutes in the future."""
        closes = list(FALLING)
        for i in range(_index_of("15:10"), len(closes)):
            closes[i] = 99999.0
        sig = read_signal(_day(closes), GapCarryConfig(), at=datetime.combine(DAY, time(15, 10)))
        self.assertEqual(sig.side, PE)
        self.assertLess(sig.close, 30000)


class TheLiveCampaignDecidesAt1510(unittest.TestCase):
    def _engine(self) -> GapCarryPaper:
        return GapCarryPaper(
            config=GapCarryConfig(timeframe="5m"),
            option_premium_lookup=lambda *_a: 250.0,
            expiry_lookup=lambda _s: date(2026, 3, 17),
            lot_size_lookup=lambda _e: 65,
        )

    def test_before_the_15_05_bar_has_closed_it_waits(self):
        eng = self._engine()
        eng.ingest({"5m": _day(FALLING)[: _index_of("15:05")]})  # last bar 15:00-15:05
        self.assertEqual(eng.status, WAITING)
        self.assertIsNone(eng.position)

    def test_the_moment_the_15_05_bar_exists_it_buys(self):
        eng = self._engine()
        eng.ingest({"5m": _day(FALLING)[: _index_of("15:05") + 1]})  # last bar 15:05-15:10
        self.assertEqual(eng.status, HOLDING)
        self.assertEqual(eng.position.signal.timestamp.time(), time(15, 5))


class TheReplayPricesWhenTheSignalExists(unittest.TestCase):
    def test_spot_and_premium_are_asked_at_15_10(self):
        asked = []
        next_day = DAY + timedelta(days=1)

        def spot_at(when):
            asked.append(("spot", when.time()))
            return 24400.0

        def price_at(when, *_a):
            asked.append(("price", when.time()))
            return 250.0

        gap_carry.replay(
            [DAY, next_day],
            config=GapCarryConfig(),
            candles_for=lambda d: _day(FALLING, d),
            spot_at=spot_at,
            price_at=price_at,
            expiry_for=lambda _d: date(2026, 3, 17),
            lot_size_for=lambda _e: 65,
            charges_for=lambda *_a: 0.0,
        )
        entry = [t for kind, t in asked if t >= time(15, 0)]
        self.assertTrue(entry)
        self.assertTrue(all(t == time(15, 10) for t in entry), entry)


class ThePollTightensAroundTheClock(unittest.TestCase):
    def test_every_two_seconds_near_15_10_and_twenty_otherwise(self):
        import os

        os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
        import app

        eng = GapCarryPaper(config=GapCarryConfig())
        at = lambda hh, mm, ss=0: datetime(2026, 3, 10, hh, mm, ss, tzinfo=app.IST)  # noqa: E731
        self.assertEqual(app._gap_carry_poll_sleep(eng, at(15, 9, 45)), app._GAP_CARRY_ENTRY_POLL_SEC)
        self.assertEqual(app._gap_carry_poll_sleep(eng, at(15, 11, 0)), app._GAP_CARRY_ENTRY_POLL_SEC)
        self.assertEqual(app._gap_carry_poll_sleep(eng, at(14, 0)), app._GAP_CARRY_POLL_SEC)
        self.assertEqual(app._gap_carry_poll_sleep(eng, at(15, 20)), app._GAP_CARRY_POLL_SEC)


if __name__ == "__main__":
    unittest.main()


class TheMorningExitIsWatchedJustAsClosely(unittest.TestCase):
    """16-Sep-2026: the cut at the open was taken 39 seconds late.

    A held carry slept the full twenty seconds between looks, so the exit could
    only be decided on the next tick after 09:15 -- and the first look after the
    bell came back without a quote, which cost another whole cycle.
    """

    def _engine(self, holding: bool):
        config = GapCarryConfig(cut_losers_at_open=True)

        class _E:
            has_open_position = holding

        e = _E()
        e.config = config
        return e

    def test_a_held_carry_looks_every_two_seconds_at_the_cut(self):
        e = self._engine(True)
        at = datetime(2026, 9, 16, 9, 15, 3, tzinfo=app.IST)
        self.assertEqual(app._gap_carry_poll_sleep(e, at), app._GAP_CARRY_EXIT_POLL_SEC)

    def test_and_at_the_normal_exit(self):
        e = self._engine(True)
        at = datetime(2026, 9, 16, 9, 20, 1, tzinfo=app.IST)
        self.assertEqual(app._gap_carry_poll_sleep(e, at), app._GAP_CARRY_EXIT_POLL_SEC)

    def test_it_is_already_looking_before_the_bell(self):
        e = self._engine(True)
        at = datetime(2026, 9, 16, 9, 14, 30, tzinfo=app.IST)
        self.assertEqual(app._gap_carry_poll_sleep(e, at), app._GAP_CARRY_EXIT_POLL_SEC)

    def test_the_rest_of_the_night_is_still_the_slow_cadence(self):
        e = self._engine(True)
        at = datetime(2026, 9, 16, 11, 30, tzinfo=app.IST)
        self.assertEqual(app._gap_carry_poll_sleep(e, at), app._GAP_CARRY_POLL_SEC)

    def test_a_carry_without_the_cut_rule_still_watches_its_own_exit(self):
        class _E:
            has_open_position = True

        e = _E()
        e.config = GapCarryConfig(cut_losers_at_open=False)
        self.assertEqual(
            app._gap_carry_poll_sleep(e, datetime(2026, 9, 16, 9, 15, 3, tzinfo=app.IST)), app._GAP_CARRY_POLL_SEC
        )
        self.assertEqual(
            app._gap_carry_poll_sleep(e, datetime(2026, 9, 16, 9, 20, 3, tzinfo=app.IST)), app._GAP_CARRY_EXIT_POLL_SEC
        )

    def test_a_missing_quote_at_the_open_is_retried_in_the_same_tick(self):
        import inspect

        src = inspect.getsource(app._run_gap_carry_paper_loop)
        self.assertIn("_gap_carry_exit_is_near(engine, now)", src)
        self.assertLess(src.index("_gap_carry_exit_is_near"), src.index("engine.mark(now"))
