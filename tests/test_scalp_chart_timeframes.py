"""The Scalp option chart offers 1m and 3m, and the chart wears TradingView's colours.

Phil, 13-Oct-2026: "Add 1m and 3m to the scalp options chart just created and
make the standard default chart like trading view colours".

Dhan serves 1, 5, 15 and 60-minute candles and no 3-minute one, so 3m is built
from 1m bars on the session's own 09:15 grid -- the grid TradingView draws.
"""

import os
import unittest
from datetime import datetime

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app as app_module  # noqa: E402

IST = app_module.IST


def _minute_bars(start: datetime, count: int) -> list[dict]:
    t0 = int(start.timestamp())
    return [{"t": t0 + i * 60, "o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100.5 + i, "v": 10} for i in range(count)]


class TheTimeframes(unittest.TestCase):
    def test_each_label_names_what_dhan_is_asked_for(self):
        tf = app_module._scalp_chart_timeframe
        self.assertEqual(tf("1m"), ("1m", "1", 1))
        self.assertEqual(tf("3m"), ("3m", "1", 3))  # built from 1m
        self.assertEqual(tf("5m"), ("5m", "5", 5))
        self.assertEqual(tf("15M"), ("15m", "15", 15))
        self.assertEqual(tf("1h"), ("1h", "60", 60))
        self.assertEqual(tf("nonsense"), ("5m", "5", 5))
        self.assertEqual(tf(""), ("5m", "5", 5))

    def test_three_minute_bars_sit_on_the_0915_grid(self):
        bars = _minute_bars(datetime(2026, 10, 13, 9, 15, tzinfo=IST), 7)
        made = app_module._regroup_chart_candles(bars, 3)
        stamps = [datetime.fromtimestamp(b["t"], IST).strftime("%H:%M") for b in made]
        self.assertEqual(stamps, ["09:15", "09:18", "09:21"])
        first = made[0]
        self.assertEqual((first["o"], first["h"], first["l"], first["c"], first["v"]), (100, 103, 99, 102.5, 30))
        self.assertEqual((made[2]["o"], made[2]["c"]), (106, 106.5))  # the bar still forming

    def test_a_new_session_starts_a_new_bar(self):
        late = _minute_bars(datetime(2026, 10, 12, 15, 28, tzinfo=IST), 2)
        early = _minute_bars(datetime(2026, 10, 13, 9, 15, tzinfo=IST), 2)
        made = app_module._regroup_chart_candles(late + early, 3)
        stamps = [datetime.fromtimestamp(b["t"], IST).strftime("%d %H:%M") for b in made]
        self.assertEqual(stamps, ["12 15:27", "13 09:15"])

    def test_bars_out_of_order_are_put_right(self):
        bars = _minute_bars(datetime(2026, 10, 13, 9, 15, tzinfo=IST), 3)
        made = app_module._regroup_chart_candles(list(reversed(bars)), 3)
        self.assertEqual((made[0]["o"], made[0]["c"]), (100, 102.5))


class ThreeDaysEachWithItsOwnCpr(unittest.TestCase):
    """Phil, 20-Oct-2026: "Put the CPR EMA at least for 3 days including current
    day in the options chart.... let the chart shows 3 days of data"."""

    def _days(self, *days):
        bars = []
        for day in days:
            bars += _minute_bars(datetime.fromisoformat(f"{day}T09:15:00").replace(tzinfo=IST), 30)
        return bars

    def test_the_newest_four_sessions_are_kept(self):
        bars = self._days("2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16")
        kept = app_module._scalp_chart_last_sessions(bars)
        days = sorted({datetime.fromtimestamp(b["t"], IST).date().isoformat() for b in kept})
        self.assertEqual(days, ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"])

    def test_three_days_carry_their_own_cpr_and_the_ema_runs_through(self):
        # Thu, Fri, then a long weekend, Tue: today and the two before it.
        kept = app_module._scalp_chart_last_sessions(self._days("2026-10-08", "2026-10-09", "2026-10-16", "2026-10-20"))
        overlays = app_module._chart_session_analytics(kept)["overlays"]
        pivots = [o for o in overlays if o.get("group") == "cpr" and o["color"] == "#d946ef"]
        starts = sorted(datetime.fromtimestamp(o["points"][0]["t"], IST).date().isoformat() for o in pivots)
        self.assertEqual(starts, ["2026-10-09", "2026-10-16", "2026-10-20"])
        ema = next(o for o in overlays if o["label"] == "EMA 20")
        self.assertLess(ema["points"][0]["t"], pivots[0]["points"][0]["t"])

    def test_the_fetch_reaches_back_past_a_long_weekend(self):
        from datetime import date

        self.assertEqual(app_module._scalp_chart_from_date(date(2026, 10, 20), "5"), "2026-10-08")
        self.assertEqual(app_module._scalp_chart_from_date(date(2026, 10, 20), "60"), "2026-10-04")


class TheLevelsWearTradingViewsColours(unittest.TestCase):
    def test_cpr_is_blue_its_pivot_magenta_resistance_red_support_green(self):
        bars = []
        for day in (12, 13):
            bars += [{**b, "t": b["t"]} for b in _minute_bars(datetime(2026, 10, day, 9, 15, tzinfo=IST), 30)]
        analytics = app_module._chart_session_analytics(bars)
        colours = {str(o["label"]).split(" (")[0]: o["color"] for o in analytics["overlays"] if o.get("label")}
        self.assertEqual(colours["EMA 20"], "#2962ff")
        self.assertEqual((colours["CPR TC"], colours["CPR BC"]), ("#2962ff", "#2962ff"))
        self.assertEqual(colours["CPR P"], "#d946ef")
        self.assertEqual({colours[k] for k in ("R1", "R2", "R3", "R4")}, {"#ef3340"})
        self.assertEqual({colours[k] for k in ("S1", "S2", "S3", "S4", "S5")}, {"#34a853"})

    def test_todays_levels_run_to_the_close_from_the_first_bar(self):
        bars = _minute_bars(datetime(2026, 10, 13, 9, 15, tzinfo=IST), 30)
        bars += _minute_bars(datetime(2026, 10, 14, 9, 15, tzinfo=IST), 3)  # three minutes into the day
        levels = [o for o in app_module._chart_session_analytics(bars)["overlays"] if o.get("label") != "EMA 20"]
        ends = {datetime.fromtimestamp(o["points"][-1]["t"], IST).strftime("%d %H:%M") for o in levels}
        starts = {datetime.fromtimestamp(o["points"][0]["t"], IST).strftime("%d %H:%M") for o in levels}
        self.assertEqual((starts, ends), ({"14 09:15"}, {"14 15:29"}))

    def test_every_level_is_a_solid_line(self):
        bars = []
        for day in (12, 13):
            bars += _minute_bars(datetime(2026, 10, day, 9, 15, tzinfo=IST), 30)
        levels = [o for o in app_module._chart_session_analytics(bars)["overlays"] if o.get("label") != "EMA 20"]
        self.assertTrue(levels)
        self.assertEqual({tuple(o.get("dash") or []) for o in levels}, {()})

    def test_they_are_the_journal_charts_own_colours(self):
        import journal_charts

        prev = [journal_charts.JournalCandle(datetime(2026, 10, 12, 9, 15), 100, 110, 95, 105)]
        journal = {name: colour for name, _price, colour, _dotted in journal_charts.previous_session_levels(prev)}
        self.assertEqual(
            (journal["TC"], journal["PP"], journal["R1"], journal["S1"]), ("#2962ff", "#d946ef", "#ef3340", "#34a853")
        )


if __name__ == "__main__":
    unittest.main()
