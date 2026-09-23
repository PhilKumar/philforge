"""NIFTY's official previous close, for the TradingView AF indicator.

Phil, 2026-09-22: TradingView's feed never carries NSE's official close
(21-Sep-2026: 23,428.90 there, 23,414.30 official), so AF takes it as a typed
number -- "Where can I get that number easily". PhilForge now sends it on
Telegram at 09:00 on trading days and shows it on the CE/PE desk.
"""

import os
import unittest
from datetime import date, datetime
from pathlib import Path

import pandas as pd

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")
HTML = (ROOT / "strategy.html").read_text(encoding="utf-8")


def _frame(rows):
    index = pd.DatetimeIndex([pd.Timestamp(t) for t, _ in rows], name="timestamp")
    return pd.DataFrame({"close": [c for _, c in rows]}, index=index)


class ThePreviousSessionsLastBar(unittest.TestCase):
    def test_monday_21_september_2026(self):
        frame = _frame(
            [
                ("2026-09-18 15:25", 23350.0),
                ("2026-09-21 09:15", 23330.2),
                ("2026-09-21 15:20", 23429.0),
                ("2026-09-21 15:25", 23414.3),  # the official close lands on the last bar
                ("2026-09-22 09:15", 23390.0),  # today's bars are not yesterday's close
            ]
        )
        found = app._prev_session_close_from_frame(frame, date(2026, 9, 22))
        self.assertEqual(found, {"session": "2026-09-21", "close": 23414.3, "for_session": "2026-09-22"})

    def test_a_stray_bar_after_the_session_is_not_the_close(self):
        frame = _frame([("2026-09-21 15:25", 23414.3), ("2026-09-21 15:45", 23500.0)])
        self.assertEqual(app._prev_session_close_from_frame(frame, date(2026, 9, 22))["close"], 23414.3)

    def test_a_monday_reads_friday(self):
        frame = _frame([("2026-09-18 15:25", 23350.0)])
        self.assertEqual(app._prev_session_close_from_frame(frame, date(2026, 9, 21))["session"], "2026-09-18")

    def test_no_bars_is_no_answer(self):
        self.assertIsNone(app._prev_session_close_from_frame(pd.DataFrame(), date(2026, 9, 22)))


class TheNineOClockMessage(unittest.TestCase):
    def test_a_trading_morning_inside_the_window(self):
        self.assertTrue(app._official_close_telegram_due(datetime(2026, 9, 22, 9, 0, tzinfo=app.IST), ""))

    def test_once_a_day(self):
        self.assertFalse(app._official_close_telegram_due(datetime(2026, 9, 22, 9, 5, tzinfo=app.IST), "2026-09-22"))

    def test_not_before_nine_nor_after_the_window(self):
        self.assertFalse(app._official_close_telegram_due(datetime(2026, 9, 22, 8, 59, tzinfo=app.IST), ""))
        self.assertFalse(app._official_close_telegram_due(datetime(2026, 9, 22, 9, 13, tzinfo=app.IST), ""))

    def test_not_on_a_weekend_or_an_nse_holiday(self):
        self.assertFalse(app._official_close_telegram_due(datetime(2026, 9, 26, 9, 0, tzinfo=app.IST), ""))
        self.assertFalse(app._official_close_telegram_due(datetime(2026, 10, 2, 9, 0, tzinfo=app.IST), ""))


class TheMessageJobStaysAlive(unittest.TestCase):
    """2026-09-23: the 09:00 job never ran and logged nothing.

    asyncio holds only a weak reference to a task, so the bare
    `create_task(...)` that started this loop could be -- and was -- collected
    mid-sleep. Phil got no message and the journal had not one line to grep.
    """

    SRC = (ROOT / "app.py").read_text(encoding="utf-8")

    def test_the_loop_is_started_with_a_kept_reference(self):
        self.assertIn('_spawn_background_loop(_run_official_close_telegram_loop(), "official previous close', self.SRC)
        self.assertNotIn("asyncio.create_task(_run_official_close_telegram_loop())", self.SRC)

    def test_the_zerodha_reminder_is_held_the_same_way(self):
        self.assertIn("_spawn_background_loop(_run_zerodha_login_reminder_loop()", self.SRC)
        self.assertNotIn("asyncio.create_task(_run_zerodha_login_reminder_loop())", self.SRC)

    def test_a_kept_task_survives_a_collection_sweep(self):
        import asyncio
        import gc

        async def scenario():
            ticks = []

            async def loop():
                while True:
                    ticks.append(1)
                    await asyncio.sleep(0.01)

            app._spawn_background_loop(loop(), "test loop")
            await asyncio.sleep(0.02)
            gc.collect()
            await asyncio.sleep(0.05)
            held = [t for t in app._BACKGROUND_LOOPS if t.get_name() == "test loop"]
            for t in held:
                t.cancel()
            return len(ticks), held

        count, held = asyncio.run(scenario())
        self.assertTrue(held, "the task must still be referenced after a collection")
        self.assertGreater(count, 1, "the loop must still be ticking after a collection")

    def test_an_empty_lookup_says_so(self):
        body = self.SRC.split("async def _run_official_close_telegram_loop")[1][:1400]
        self.assertIn("[AF CLOSE] no official close", body)
        self.assertIn("[AF CLOSE] sent", body)


class TheDeskShowsIt(unittest.TestCase):
    def test_the_button_sits_with_the_nifty_chart_buttons(self):
        bar = HTML.split('class="cepe-chartbar"')[1].split("</div>")[0]
        self.assertIn('id="oc-cepe-prevclose"', bar)
        self.assertIn('data-pf-action="copyCePePrevClose"', bar)

    def test_the_copy_action_is_allowed(self):
        self.assertIn("'copyCePePrevClose',", JS)

    def test_the_desk_paints_it(self):
        body = JS.split("function renderCePe(data) {")[1][:200]
        self.assertIn("_cepePaintPrevClose();", body)


if __name__ == "__main__":
    unittest.main()
