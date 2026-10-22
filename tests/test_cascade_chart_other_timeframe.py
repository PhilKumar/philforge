"""A campaign's chart viewed on a timeframe other than its own.

Phil, 2026-10-21, on the Equity page: "Something went wrong on our end." The
ADANIENSOL campaign's mother is a 13:15 bar; its chart's 1D button asked for
that stamp on daily candles. Dhan was asked for one day as from=to, refused
it (DH-907 "no data present"), and the refusal went up as a 500. Had the day
come back, its one bar is stamped 09:15 and the 13:15 stamp matched nothing.
"""

import asyncio
import base64
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PHILFORGE_PIN", "123456")
os.environ.setdefault("PHILFORGE_DB", "/tmp/philforge-test-chart-other-tf.db")
os.environ.setdefault("PHILFORGE_USER_DATA_ROOT", "/tmp/philforge-test-chart-other-tf-data")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
# Computed, not a literal, so the secret scanner doesn't flag a dummy test key.
os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())

import app as app_module  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
MOTHER = datetime(2026, 7, 21, 13, 15, tzinfo=IST)


class TheMotherIsTheBarThatHoldsIt(unittest.TestCase):
    def _align(self, timeframe):
        interval, minutes, _tf = app_module._terminal_cascade_timeframe_parts(timeframe)
        return app_module._terminal_cascade_align_mother(MOTHER, interval, minutes)

    def test_on_1d_it_is_that_days_bar(self):
        self.assertEqual(self._align("1d"), datetime(2026, 7, 21, 9, 15, tzinfo=IST))

    def test_on_its_own_timeframe_it_is_unchanged(self):
        self.assertEqual(self._align("1h"), MOTHER)
        self.assertEqual(self._align("15m"), MOTHER)

    def test_inside_a_longer_bar_it_is_that_bars_start(self):
        late = datetime(2026, 7, 21, 13, 40, tzinfo=IST)
        interval, minutes, _tf = app_module._terminal_cascade_timeframe_parts("1h")
        self.assertEqual(
            app_module._terminal_cascade_align_mother(late, interval, minutes),
            datetime(2026, 7, 21, 13, 15, tzinfo=IST),
        )


class TheRouteIsTheChart(unittest.TestCase):
    def test_the_chart_url_serves_the_chart(self):
        route = next(r for r in app_module.app.routes if getattr(r, "path", "") == "/api/terminal/cascade/chart")
        self.assertIs(route.endpoint, app_module.terminal_cascade_chart)


class _Dhan:
    def __init__(self):
        self.asked = []

    def get_historical_data(self, **kw):
        self.asked.append((kw["from_date"], kw["to_date"], kw["candle_type"]))
        if kw["from_date"] == kw["to_date"] and kw["candle_type"] == "D":
            raise Exception('Dhan API error 400: {"errorCode":"DH-907"}')
        stamp = pd.Timestamp("2026-07-21 09:15", tz=IST)
        return pd.DataFrame(
            {"open": [100.0], "high": [102.0], "low": [99.0], "close": [101.0], "volume": [1000]}, index=[stamp]
        )


class OneDayOfDailyIsAskedAsTwo(unittest.TestCase):
    def test_a_single_day_reaches_past_itself(self):
        dhan = _Dhan()
        rows = asyncio.run(
            app_module._terminal_cascade_load_candles(
                dhan,
                {"security_id": "1", "exchange_segment": "NSE_EQ", "instrument_type": "EQUITY"},
                "1d",
                from_date=MOTHER.date(),
                to_date=MOTHER.date(),
            )
        )
        self.assertEqual(dhan.asked, [("2026-07-21", "2026-07-22", "D")])
        self.assertEqual([row.timestamp for row in rows], [datetime(2026, 7, 21, 9, 15, tzinfo=IST)])


class AHolidayChunkIsEmptyNotAFailure(unittest.TestCase):
    """20-Oct-2026: a 1h replay from 21-Jul ran 91 days, so its last 90-day
    chunk was 20-Oct alone -- Dussehra -- and Dhan's "no candles" for it was a
    500 for the whole chart."""

    def test_the_holiday_chunk_is_skipped(self):
        asked = []

        class Dhan:
            def get_historical_data(self, **kw):
                asked.append((kw["from_date"], kw["to_date"]))
                if kw["from_date"] == "2026-10-20":
                    raise Exception("Dhan returned no candles for security 10217 in NSE_EQ (EQUITY) 2026-10-20")
                stamp = pd.Timestamp("2026-07-21 13:15", tz=IST)
                return pd.DataFrame(
                    {"open": [100.0], "high": [102.0], "low": [99.0], "close": [101.0], "volume": [1000]},
                    index=[stamp],
                )

        rows = asyncio.run(
            app_module._terminal_cascade_load_candles(
                Dhan(),
                {"security_id": "10217", "exchange_segment": "NSE_EQ", "instrument_type": "EQUITY"},
                "1h",
                from_date=MOTHER.date(),
                to_date=datetime(2026, 10, 20).date(),
            )
        )
        self.assertEqual(len(asked), 2)
        self.assertEqual([row.timestamp for row in rows], [MOTHER])

    def test_a_real_refusal_still_fails(self):
        class Dhan:
            def get_historical_data(self, **kw):
                if kw["from_date"] == "2026-10-20":
                    raise Exception("Dhan API error 401: DH-901 invalid token")
                return pd.DataFrame(
                    {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]},
                    index=[pd.Timestamp("2026-07-21 13:15", tz=IST)],
                )

        with self.assertRaises(Exception):
            asyncio.run(
                app_module._terminal_cascade_load_candles(
                    Dhan(),
                    {"security_id": "10217", "exchange_segment": "NSE_EQ", "instrument_type": "EQUITY"},
                    "1h",
                    from_date=MOTHER.date(),
                    to_date=datetime(2026, 10, 20).date(),
                )
            )


if __name__ == "__main__":
    unittest.main()
