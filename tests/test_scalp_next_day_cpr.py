"""Tomorrow's CPR on the Scalp charts, once the day has closed.

Phil, 2026-10-29: "add next day CPR for the options chart... I only need it
after the market closed.. after 3:30 and 11:30 for comodity". Tomorrow's levels
come from today's finished bars: never drawn while the session is open, drawn
past the last candle once it closes, and never for an option that expired today.
"""

import os
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def _candles(first_day: str, days: int, open_hm: str = "09:15", bars: int = 75):
    out, price = [], 200.0
    for d in range(days):
        start = pd.Timestamp(f"{first_day} {open_hm}") + pd.Timedelta(days=d)
        for step in range(bars):
            price += 1 if step % 3 else -2
            stamp = start + pd.Timedelta(minutes=5 * step)
            out.append(
                {"t": int(stamp.tz_localize(IST).timestamp()), "o": price, "h": price + 3, "l": price - 3, "c": price}
            )
    return out


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=IST)


class TheOptionsSessionRunsTo1540(unittest.TestCase):
    """Phil, 2026-11-03: "There is a 10m window from 3:30 till 3:40.. why it is
    not visible in chart". NSE F&O trades to 15:40; the index does not."""

    def test_nse_and_bse_options_close_at_1540(self):
        for name in ("NIFTY", "SENSEX", "RELIANCE"):
            self.assertEqual(app._scalp_session(name)["close"], "15:40", name)

    def test_the_underlyings_own_chart_closes_at_1530(self):
        self.assertEqual(app._scalp_session("NIFTY", options=False)["close"], "15:30")

    def test_mcx_is_unchanged(self):
        day = datetime(2026, 10, 28).date()
        self.assertEqual(
            app._scalp_session("CRUDEOIL", day)["close"], app._scalp_session("CRUDEOIL", day, options=False)["close"]
        )


class NextDayCpr(unittest.TestCase):
    def setUp(self):
        # Tue 27 and Wed 28 Oct 2026, 5-minute bars to 15:25.
        self.candles = _candles("2026-10-27", 2)

    def test_nothing_while_the_session_is_open(self):
        # NSE options trade to 15:40, so 15:39 is still the session.
        out = app._chart_next_day_cpr(self.candles, "NIFTY", 5, now=_at("2026-10-28T15:39:00"))
        self.assertEqual(out, {"overlays": [], "future_bars": 0})

    def test_the_index_chart_still_closes_at_1530(self):
        # The index itself stops at 15:30; only its options run on.
        early = app._chart_next_day_cpr(self.candles, "NIFTY", 5, now=_at("2026-10-28T15:30:00"), options=False)
        self.assertEqual(len(early["overlays"]), 12)
        self.assertEqual(early["future_bars"], app._NEXT_DAY_CPR_GAP_BARS + 75)

    def test_drawn_past_the_last_bar_after_the_close(self):
        out = app._chart_next_day_cpr(self.candles, "NIFTY", 5, now=_at("2026-10-28T15:40:00"))
        self.assertEqual(len(out["overlays"]), 12)
        last_t = self.candles[-1]["t"]
        for ov in out["overlays"]:
            self.assertGreater(ov["points"][0]["t"], last_t)
            self.assertTrue(ov["label"])
            self.assertEqual(ov["group"], "cpr")
        # The whole 77-bar session (09:15-15:40), plus the gap.
        self.assertEqual(out["future_bars"], app._NEXT_DAY_CPR_GAP_BARS + 77)

    def test_levels_come_from_the_last_session_only(self):
        out = app._chart_next_day_cpr(self.candles, "NIFTY", 5, now=_at("2026-10-28T18:00:00"))
        today = self.candles[75:]
        high = max(b["h"] for b in today)
        low = min(b["l"] for b in today)
        pivot = (high + low + today[-1]["c"]) / 3.0
        p = next(ov for ov in out["overlays"] if ov["label"].startswith("CPR P "))
        self.assertAlmostEqual(p["points"][0]["price"], round(pivot, 4))

    def test_a_weekend_still_shows_mondays(self):
        out = app._chart_next_day_cpr(self.candles, "NIFTY", 5, now=_at("2026-10-31T10:00:00"))
        self.assertEqual(len(out["overlays"]), 12)

    def test_the_block_is_timed_at_the_next_session_open(self):
        # The crosshair over the block reads tomorrow 09:15, not today 15:40.
        out = app._chart_next_day_cpr(self.candles, "NIFTY", 5, now=_at("2026-10-28T18:00:00"))
        self.assertEqual(out["future_t0"], int(_at("2026-10-29T09:15:00").timestamp()))
        self.assertEqual(out["future_gap"], app._NEXT_DAY_CPR_GAP_BARS)
        # A Friday's block opens on Monday.
        friday = _candles("2026-10-29", 2)
        out = app._chart_next_day_cpr(friday, "NIFTY", 5, now=_at("2026-10-31T10:00:00"))
        self.assertEqual(out["future_t0"], int(_at("2026-11-02T09:15:00").timestamp()))

    def test_no_tomorrow_for_an_option_that_expired_today(self):
        out = app._chart_next_day_cpr(self.candles, "NIFTY", 5, "2026-10-28", now=_at("2026-10-28T16:00:00"))
        self.assertEqual(out["overlays"], [])
        out = app._chart_next_day_cpr(self.candles, "NIFTY", 5, "2026-11-03", now=_at("2026-10-28T16:00:00"))
        self.assertEqual(len(out["overlays"]), 12)

    def test_commodity_waits_for_the_mcx_close(self):
        mcx = _candles("2026-10-27", 2, open_hm="09:00", bars=174)
        close = app._scalp_session("CRUDEOIL", datetime(2026, 10, 28).date())["close"]
        self.assertIn(close, {"23:30", "23:55"})
        self.assertEqual(app._chart_next_day_cpr(mcx, "CRUDEOIL", 5, now=_at("2026-10-28T20:00:00"))["overlays"], [])
        later = app._chart_next_day_cpr(mcx, "CRUDEOIL", 5, now=_at(f"2026-10-28T{close}:00"))
        self.assertEqual(len(later["overlays"]), 12)
        self.assertEqual(later["future_t0"], int(_at("2026-10-29T09:00:00").timestamp()))

    def test_the_payload_moves_the_labels_to_tomorrow(self):
        analytics = app._chart_session_analytics(self.candles)
        payload = {"overlays": list(analytics["overlays"])}
        app._with_next_day_cpr(payload, self.candles, "NIFTY", 5, "", now=_at("2026-10-28T18:00:00"))
        labelled = [ov for ov in payload["overlays"] if ov.get("group") == "cpr" and ov["label"]]
        self.assertEqual(len(labelled), 12)
        self.assertTrue(all(ov.get("next_day") for ov in labelled))
        # Today's set is still drawn, just unlabelled; the EMA keeps its label.
        self.assertEqual(sum(1 for ov in payload["overlays"] if ov.get("group") == "cpr"), 24)
        self.assertTrue(any(ov.get("group") == "ema" and ov["label"] for ov in payload["overlays"]))
        self.assertEqual(payload["future_bars"], app._NEXT_DAY_CPR_GAP_BARS + 77)

    def test_the_payload_is_untouched_while_open(self):
        analytics = app._chart_session_analytics(self.candles)
        payload = {"overlays": list(analytics["overlays"])}
        app._with_next_day_cpr(payload, self.candles, "NIFTY", 5, "", now=_at("2026-10-28T11:00:00"))
        self.assertEqual(payload["overlays"], analytics["overlays"])
        self.assertNotIn("future_bars", payload)


if __name__ == "__main__":
    unittest.main()
