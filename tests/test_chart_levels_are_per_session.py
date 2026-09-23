"""Each session on the chart carries ITS OWN CPR and pivots.

Phil, 2026-09-23: "The nifty chart shows only the current CPR... It has to
show the responding day CPR and indicators". One set of levels drawn flat
across a three-day window is only true for the last day: the first two days
were being read against today's CPR, which is not the frame those candles
traded in.
"""

import os
import random
import unittest
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
LEVELS = ("CPR TC", "CPR P", "CPR BC", "R1", "R2", "R3", "R4", "S1", "S2", "S3", "S4", "S5")


def _candles(days: int):
    random.seed(7)
    out, price = [], 23300.0
    for d in range(days):
        start = pd.Timestamp("2026-09-17 09:15") + pd.Timedelta(days=d)
        for step in range(75):
            price += random.gauss(0.5 if d % 2 else -0.5, 4)
            stamp = start + pd.Timedelta(minutes=5 * step)
            out.append(
                {
                    "t": int(stamp.tz_localize(IST).timestamp()),
                    "o": price,
                    "h": price + 6,
                    "l": price - 6,
                    "c": price,
                }
            )
    return out


def _level_overlays(analytics):
    return [o for o in analytics["overlays"] if o["label"] != "EMA 20"]


def _day_of(overlay):
    return datetime.fromtimestamp(overlay["points"][0]["t"], IST).date().isoformat()


class EverySessionGetsItsOwn(unittest.TestCase):
    def setUp(self):
        self.candles = _candles(3)
        self.analytics = app._chart_session_analytics(self.candles)
        self.levels = _level_overlays(self.analytics)

    def test_one_full_set_per_session_after_the_first(self):
        per_day = Counter(_day_of(o) for o in self.levels)
        self.assertEqual(sorted(per_day), ["2026-09-18", "2026-09-19"])
        self.assertEqual(set(per_day.values()), {len(LEVELS)})

    def test_the_first_session_has_none_because_nothing_precedes_it(self):
        self.assertNotIn("2026-09-17", {_day_of(o) for o in self.levels})

    def test_a_level_spans_only_its_own_day(self):
        for overlay in self.levels:
            days = {datetime.fromtimestamp(p["t"], IST).date().isoformat() for p in overlay["points"]}
            self.assertEqual(len(days), 1, overlay["label"] or "unlabelled")
            self.assertEqual(overlay["points"][0]["price"], overlay["points"][-1]["price"])

    def test_each_day_is_priced_from_the_day_before_it(self):
        sessions = {}
        for bar in self.candles:
            sessions.setdefault(datetime.fromtimestamp(bar["t"], IST).date().isoformat(), []).append(bar)
        days = sorted(sessions)
        for index in range(1, len(days)):
            prev = sessions[days[index - 1]]
            high = max(b["h"] for b in prev)
            low = min(b["l"] for b in prev)
            pivot = (high + low + prev[-1]["c"]) / 3.0
            drawn = [o for o in self.levels if _day_of(o) == days[index] and o["points"][0]["price"]]
            got = {round(o["points"][0]["price"], 2) for o in drawn}
            self.assertIn(round(pivot, 2), got, days[index])

    def test_the_days_really_differ(self):
        by_day = {}
        for overlay in self.levels:
            by_day.setdefault(_day_of(overlay), set()).add(overlay["points"][0]["price"])
        first, second = (by_day[k] for k in sorted(by_day))
        self.assertTrue(first.isdisjoint(second), "two sessions drew the same levels")

    def test_only_the_newest_session_labels_the_gutter(self):
        labelled = [o for o in self.levels if o["label"]]
        self.assertEqual(len(labelled), len(LEVELS))
        self.assertEqual({_day_of(o) for o in labelled}, {"2026-09-19"})
        for name in LEVELS:
            self.assertTrue(any(o["label"].startswith(name + " (") for o in labelled), name)

    def test_the_flat_full_width_lines_are_gone(self):
        self.assertEqual(self.analytics["lines"], [])

    def test_one_session_alone_draws_no_pivots(self):
        analytics = app._chart_session_analytics(_candles(1))
        self.assertEqual(_level_overlays(analytics), [])
        self.assertTrue(any(o["label"] == "EMA 20" for o in analytics["overlays"]))


if __name__ == "__main__":
    unittest.main()
