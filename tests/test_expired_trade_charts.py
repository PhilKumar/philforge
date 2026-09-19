"""A closed trade keeps its chart after its weekly contract expires.

Phil, 2026-09-18, on the Live page's frozen trade charts: "This trade's
contract is no longer in the Scrip Master (expired?)". Both chart endpoints
looked the contract up in today's scrip master, which lists only live
contracts. The trade's own security id goes first now, Dhan's rolling-option
archive is the fallback, and a finished trade's candles are kept on disk.
"""

import asyncio
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import pandas as pd

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

from fastapi import HTTPException  # noqa: E402

import app  # noqa: E402
import config  # noqa: E402

SRC = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")

TRADE = {
    "id": 1,
    "security_id": "47306",
    "underlying": "NIFTY",
    "strike": 23700,
    "option_type": "PE",
    "expiry": "2026-09-15",
    "entry_time": "2026-09-10 09:25:09",
    "exit_time": "2026-09-10 15:26:36",
}


def _frame(stamps, closes, strikes=None):
    index = pd.DatetimeIndex(pd.to_datetime(stamps), name="timestamp")
    data = {"open": closes, "high": closes, "low": closes, "close": closes}
    if strikes is not None:
        data["strike"] = strikes
    return pd.DataFrame(data, index=index)


class FakeDhan:
    def __init__(self, history=None, history_error=None, rolling=None):
        self.history = history
        self.history_error = history_error
        self.rolling = rolling or {}
        self.history_calls = []
        self.rolling_calls = []

    def _is_configured(self):
        return True

    def get_historical_data(self, **kw):
        self.history_calls.append(kw)
        if self.history_error:
            raise self.history_error
        return self.history if self.history is not None else pd.DataFrame()

    def get_rolling_option_data(self, **kw):
        self.rolling_calls.append(kw)
        return self.rolling.get((kw["expiry_code"], kw["strike"]), pd.DataFrame())


def _run(client, trade=TRADE, cache_path=""):
    return asyncio.run(
        app._contract_chart_candles(
            client,
            trade,
            underlying="NIFTY",
            strike=23700,
            expiry="2026-09-15",
            option_type="PE",
            from_date=date(2026, 9, 6),
            to_date=date(2026, 9, 10),
            candle_type="5",
            cache_path=cache_path,
        )
    )


class TheTradesOwnIdComesFirst(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app.ScripMaster, "lookup", return_value="")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_saved_security_id_is_asked_even_when_the_scrip_master_forgot_it(self):
        client = FakeDhan(history=_frame(["2026-09-10 09:25", "2026-09-10 09:30"], [265.0, 270.0]))
        candles = _run(client)
        self.assertEqual([c["c"] for c in candles], [265.0, 270.0])
        self.assertEqual(client.history_calls[0]["security_id"], "47306")
        self.assertEqual(client.rolling_calls, [])

    def test_the_old_refusal_is_gone(self):
        self.assertNotIn("detail=\"This trade's contract is no longer in the Scrip Master", SRC)
        self.assertNotIn('detail="Entered contract is unavailable in the current Scrip Master', SRC)


class TheArchiveIsTheFallback(unittest.TestCase):
    """Expiry 15-Sep: the week 09-Sep..15-Sep is code 1 (nearest), the week before code 2."""

    def setUp(self):
        patcher = mock.patch.object(app.ScripMaster, "lookup", return_value="")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _archive(self):
        # ATM is 23650 at first, so 23700 is ATM+1. At 09:35 the market jumps
        # two strikes to 23750 and 23700 becomes ATM-1.
        stamps = ["2026-09-10 09:25", "2026-09-10 09:30", "2026-09-10 09:35"]
        return {
            (1, "ATM"): _frame(stamps, [300.0, 301.0, 302.0], [23650.0, 23650.0, 23750.0]),
            (1, "ATM+1"): _frame(stamps, [265.0, 270.0, 999.0], [23700.0, 23700.0, 23800.0]),
            (1, "ATM+2"): _frame(stamps, [230.0, 231.0, 272.0], [23750.0, 23750.0, 23850.0]),
            (1, "ATM-1"): _frame(stamps, [340.0, 341.0, 275.0], [23600.0, 23600.0, 23700.0]),
        }

    def test_only_the_traded_strike_is_kept_from_every_alias(self):
        client = FakeDhan(history_error=RuntimeError("DH-905 no data"), rolling=self._archive())
        candles = _run(client)
        self.assertEqual([c["c"] for c in candles], [265.0, 270.0, 275.0])

    def test_a_strike_far_from_the_weeks_middle_is_still_found(self):
        """11-Sep-2026: 23450PE was five strikes off the week's median ATM that
        morning, and the chart began at 11:10 on a trade closed at 10:08."""
        stamps = ["2026-09-10 09:25", "2026-09-10 09:30", "2026-09-10 09:35", "2026-09-10 09:40"]
        atm = [23700.0, 23950.0, 23950.0, 23950.0]  # median 23950, so 23700 is ATM-5 there
        archive = {
            (1, "ATM"): _frame(stamps, [300.0, 90.0, 91.0, 92.0], atm),
            (1, "ATM-5"): _frame(stamps, [30.0, 150.0, 151.0, 152.0], [23450.0, 23700.0, 23700.0, 23700.0]),
        }
        client = FakeDhan(history=pd.DataFrame(), rolling=archive)
        candles = _run(client)
        self.assertEqual([c["c"] for c in candles], [300.0, 150.0, 151.0, 152.0])
        asked = {c["strike"] for c in client.rolling_calls}
        self.assertEqual(asked, {"ATM", "ATM-5"}, "only the aliases the strike actually sat in")

    def test_the_contract_is_pinned_by_its_expiry_week(self):
        client = FakeDhan(history=pd.DataFrame(), rolling=self._archive())
        _run(client)
        calls = {(c["expiry_code"], c["from_date"], c["to_date"]) for c in client.rolling_calls}
        # 06-Sep..08-Sep belongs to the week this contract was the NEXT weekly.
        self.assertIn((2, "2026-09-06", "2026-09-09"), calls)
        self.assertIn((1, "2026-09-09", "2026-09-11"), calls)
        # Dhan refuses 0 on this API ("expiryCode is required").
        self.assertNotIn(0, {c["expiry_code"] for c in client.rolling_calls})
        self.assertTrue(all(c["security_id"] == "13" and c["strike"] for c in client.rolling_calls))

    def test_nothing_anywhere_says_so_plainly(self):
        client = FakeDhan(history=pd.DataFrame())
        with self.assertRaises(HTTPException) as caught:
            _run(client)
        self.assertEqual(caught.exception.status_code, 404)
        self.assertIn("23700PE", caught.exception.detail)

    def test_a_zerodha_book_reads_the_archive_through_dhan(self):
        class NoArchive:
            def get_historical_data(self, **kw):
                return pd.DataFrame()

        fallback = FakeDhan(rolling=self._archive())
        with mock.patch.object(app, "dhan", fallback):
            candles = _run(NoArchive())
        self.assertEqual(len(candles), 3)


class AFinishedChartIsKept(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(app.ScripMaster, "lookup", return_value="")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "trade_charts", "PE_NoTarget__1_x__5.json")

    def test_the_second_view_never_asks_the_broker(self):
        first = FakeDhan(history=_frame(["2026-09-10 09:25"], [265.0]))
        _run(first, cache_path=self.path)
        self.assertTrue(os.path.exists(self.path))
        second = FakeDhan(history_error=RuntimeError("expired"))
        candles = _run(second, cache_path=self.path)
        self.assertEqual([c["c"] for c in candles], [265.0])
        self.assertEqual(second.history_calls, [])

    def test_the_trade_chart_keeps_only_a_finished_trade(self):
        body = SRC.split("async def live_trade_chart(")[1].split("\n@app.")[0]
        self.assertIn("if exit_ts and end_day < datetime.now(IST).date()", body)

    def test_the_key_carries_the_entry_time_so_a_reused_id_cannot_collide(self):
        body = SRC.split("async def live_trade_chart(")[1].split("\n@app.")[0]
        self.assertIn("_{int(entry_ts)}", body)


class AStoppedRunsTradesAreFound(unittest.TestCase):
    def test_the_history_file_in_the_users_engine_state_is_read(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(config, "USER_DATA_ROOT", root):
            folder = os.path.join(root, "5", "engine_state")
            os.makedirs(folder)
            with open(os.path.join(folder, "live_history_PE_NoTarget.json"), "w") as handle:
                json.dump([TRADE], handle)
            rows = app._live_run_history_trades("PE_NoTarget", 5)
        self.assertEqual([r["security_id"] for r in rows], ["47306"])


class DhansUnevenArrays(unittest.TestCase):
    """Phil, 2026-09-19: "Last answer: All arrays must be of the same length"."""

    def _read(self, series):
        from broker import dhan as dhan_module

        class Offline(dhan_module.DhanClient):
            headers = {}

        client = Offline.__new__(Offline)
        client.base_url = "https://api.dhan.co"
        client._allow_token_refresh, client._is_configured = False, lambda: True
        reply = mock.Mock(status_code=200)
        reply.json.return_value = {"data": {"pe": series, "ce": None}}
        with (
            mock.patch.object(dhan_module, "_request_with_retry", return_value=reply),
            mock.patch.object(dhan_module, "_throttle_charts"),
        ):
            return client.get_rolling_option_data(
                security_id="13",
                exchange_segment="NSE_FNO",
                instrument_type="OPTIDX",
                expiry_flag="WEEK",
                expiry_code=1,
                strike="ATM",
                option_type="PE",
                from_date="2026-09-11",
                to_date="2026-09-16",
                interval="5",
            )

    def _series(self, **extra):
        stamps = [1789530300, 1789530600, 1789530900]  # 2026-09-11 09:15.. IST
        base = {
            "timestamp": stamps,
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "volume": [10, 20, 30],
            "strike": [23600.0, 23600.0, 23650.0],
            "spot": [23610.0, 23605.0, 23660.0],
        }
        base.update(extra)
        return base

    def test_fields_nobody_asked_for_come_back_empty_and_are_left_out(self):
        frame = self._read(self._series(oi=[], iv=[]))
        self.assertEqual(len(frame), 3)
        self.assertNotIn("oi", frame)
        self.assertEqual(list(frame["strike"]), [23600.0, 23600.0, 23650.0])

    def test_a_short_field_is_padded_blank_and_a_long_one_cut(self):
        frame = self._read(self._series(spot=[23610.0], oi=[1, 2, 3, 4]))
        self.assertEqual(len(frame), 3)
        self.assertEqual(frame["spot"].isna().tolist(), [False, True, True])
        self.assertEqual(list(frame["oi"]), [1, 2, 3])

    def test_the_traded_strike_survives_to_the_chart(self):
        frame = self._read(self._series(oi=[], iv=[]))
        mine = frame[(frame["strike"] - 23600.0).abs() < 0.01]
        self.assertEqual([c["c"] for c in app._frame_to_chart_candles(mine)], [100.5, 101.5])


class CandleRows(unittest.TestCase):
    def test_a_blank_archive_bar_is_skipped(self):
        frame = _frame(["2026-09-10 09:25", "2026-09-10 09:30"], [265.0, float("nan")])
        self.assertEqual(len(app._frame_to_chart_candles(frame)), 1)

    def test_stamps_are_epoch_seconds(self):
        candle = app._frame_to_chart_candles(_frame(["2026-09-10 09:25"], [1.0]))[0]
        self.assertIsInstance(candle["t"], (int, float))
        self.assertGreater(candle["t"], datetime(2026, 1, 1).timestamp())


if __name__ == "__main__":
    unittest.main()
