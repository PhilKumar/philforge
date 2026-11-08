"""Diwali's Muhurat session: an hour on a Sunday the calendar calls closed.

Phil, 2026-11-08: "today muhurat trading is there.. check this". Every
"market open?" check said no to a weekend, so the Scalp monitor would have
left a Muhurat trade's stop, target and Supertrend exit unwatched.
"""

import unittest
from datetime import date, datetime

import app
import scalp
from engine.market_feed import LiveMarketFeed
from scalp import IST
from special_sessions import in_special_session, special_session

SUNDAY = date(2026, 11, 8)


def at(day, hh, mm):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


class TheMuhuratHour(unittest.TestCase):
    def test_the_table_knows_it(self):
        self.assertEqual(special_session(SUNDAY), ((14, 5), (14, 20), (15, 40), (15, 30)))
        self.assertIsNone(special_session(date(2026, 11, 15)))
        self.assertTrue(in_special_session(at(SUNDAY, 14, 30)))
        self.assertFalse(in_special_session(at(SUNDAY, 13, 0)))
        self.assertFalse(in_special_session(at(SUNDAY, 15, 50)))

    def test_the_scalp_monitor_watches_it_and_no_other_sunday(self):
        self.assertTrue(scalp._scalp_market_watch_open(at(SUNDAY, 14, 25)))
        self.assertTrue(scalp._scalp_market_watch_open(at(SUNDAY, 15, 40)))
        self.assertFalse(scalp._scalp_market_watch_open(at(SUNDAY, 12, 0)))
        self.assertFalse(scalp._scalp_market_watch_open(at(SUNDAY, 16, 0)))
        self.assertFalse(scalp._scalp_market_watch_open(at(date(2026, 11, 15), 14, 25)))
        self.assertFalse(scalp._scalp_market_watch_open(at(date(2026, 11, 7), 14, 25)))  # Saturday

    def test_the_feed_expects_ticks_in_it(self):
        self.assertTrue(LiveMarketFeed._market_watch_open(at(SUNDAY, 14, 30)))
        self.assertFalse(LiveMarketFeed._market_watch_open(at(date(2026, 11, 15), 14, 30)))

    def test_the_chart_draws_its_own_hours_and_grid(self):
        self.assertEqual(
            app._scalp_session("NIFTY", SUNDAY), {"open": "14:20", "close": "15:40", "exchange": "NSE", "special": True}
        )
        self.assertEqual(app._scalp_session("NIFTY", SUNDAY, options=False)["close"], "15:30")
        self.assertEqual(app._scalp_session("NIFTY", date(2026, 11, 9))["open"], "09:15")
        ones = [{"t": int(at(SUNDAY, 14, 20 + i).timestamp()), "o": 1, "h": 2, "l": 0, "c": 1} for i in range(6)]
        threes = app._regroup_chart_candles(ones, 3)
        self.assertEqual([datetime.fromtimestamp(b["t"], IST).strftime("%H:%M") for b in threes], ["14:20", "14:23"])
        # A normal day keeps the 09:15 grid in the same call.
        friday = [
            {"t": int(at(date(2026, 11, 6), 9, 15 + i).timestamp()), "o": 1, "h": 2, "l": 0, "c": 1} for i in range(3)
        ]
        self.assertEqual(
            datetime.fromtimestamp(app._regroup_chart_candles(friday, 3)[0]["t"], IST).strftime("%H:%M"), "09:15"
        )

    def test_the_next_days_cpr_is_the_muhurat_hour_then_monday(self):
        self.assertFalse(app._nse_session_closed(SUNDAY))
        friday = [{"t": int(at(date(2026, 11, 6), 15, 35).timestamp()), "o": 100, "h": 101, "l": 99, "c": 100}]
        before = app._chart_next_day_cpr(friday, "NIFTY", 5, "", at(SUNDAY, 11, 0))
        self.assertEqual(datetime.fromtimestamp(before["future_t0"], IST).strftime("%m-%d %H:%M"), "11-08 14:20")
        muhurat = [{"t": int(at(SUNDAY, 15, 35).timestamp()), "o": 100, "h": 101, "l": 99, "c": 100}]
        after = app._chart_next_day_cpr(muhurat, "NIFTY", 5, "", at(SUNDAY, 16, 0))
        self.assertEqual(datetime.fromtimestamp(after["future_t0"], IST).strftime("%m-%d %H:%M"), "11-09 09:15")


if __name__ == "__main__":
    unittest.main()
