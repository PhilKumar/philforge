"""A chart Dhan will not draw is drawn from Zerodha.

Phil, 2026-10-23: "still not able to see the charts". From 08:35 Dhan's chart
API answered every request DH-905 while its prices and account calls still
worked. With the day's Zerodha login the same candles come from Kite.
"""

import asyncio
import os
import unittest
from unittest import mock

import pandas as pd

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402
from broker import zerodha  # noqa: E402

FRAME = pd.DataFrame(
    {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [10]},
    index=pd.DatetimeIndex([pd.Timestamp("2026-10-23 09:15")], name="timestamp"),
)
ASK = dict(security_id="51338", exchange_segment="NSE_FNO", instrument_type="OPTIDX", candle_type="5")


class _Broker:
    def __init__(self, answer):
        self.answer, self.asked = answer, 0

    def get_historical_data(self, **kw):
        self.asked += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _Request:
    class state:
        current_user = {"id": 1}


def _run(dhan, kite):
    with (
        mock.patch.object(app._auth_mod, "get_current_user", mock.AsyncMock(return_value={"id": 1})),
        mock.patch.object(
            app, "_user_zerodha_client", lambda user, require_today=True: (kite, "zerodha" if kite else "missing")
        ),
    ):
        return asyncio.run(app._chart_candles(_Request(), dhan, **ASK))


class TheChartFallsBack(unittest.TestCase):
    def test_dhan_first_when_dhan_answers(self):
        kite = _Broker(FRAME)
        frame, source = _run(_Broker(FRAME), kite)
        self.assertEqual(source, "dhan")
        self.assertEqual(kite.asked, 0)

    def test_zerodha_when_dhan_refuses(self):
        refusal = Exception('Dhan API error 400: {"errorCode":"DH-905"}')
        frame, source = _run(_Broker(refusal), _Broker(FRAME))
        self.assertEqual((source, len(frame)), ("zerodha", 1))

    def test_no_zerodha_login_keeps_dhans_reason(self):
        with self.assertRaisesRegex(Exception, "DH-905"):
            _run(_Broker(Exception("DH-905")), None)

    def test_both_failing_says_both(self):
        with self.assertRaisesRegex(RuntimeError, "Zerodha could not either"):
            _run(_Broker(Exception("DH-905")), _Broker(RuntimeError("no contract")))


class KiteFindsTheOption(unittest.TestCase):
    def test_an_option_is_found_by_dhans_id(self):
        asked = []

        class Kite:
            def request(self, method, url, headers=None, params=None, data=None, timeout=None, json=None):
                asked.append(url)

                class R:
                    status_code = 200

                    def json(self):
                        return {
                            "status": "success",
                            "data": {"candles": [["2026-10-23T09:15:00+0530", 1, 2, 0.5, 1.5, 10]]},
                        }

                return R()

        client = zerodha.ZerodhaClient("k", "t", session=Kite())
        info = {"instrument_token": 12345}
        with (
            mock.patch.object(client, "_instruments", lambda: None),
            mock.patch.object(zerodha, "_dhan_id_to_key", lambda: {"51338": "NIFTY_22650_2026-10-27_CE"}),
            mock.patch.dict(zerodha.KiteInstruments._by_key, {"NIFTY_22650_2026-10-27_CE": info}),
        ):
            frame = client.get_historical_data(**ASK, from_date="2026-10-20", to_date="2026-10-23")
        self.assertIn("/instruments/historical/12345/5minute", asked[0])
        self.assertEqual(len(frame), 1)


if __name__ == "__main__":
    unittest.main()
