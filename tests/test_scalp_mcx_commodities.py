"""Crude oil and natural gas options on the Scalp desk -- step one, paper.

Phil, 2026-10-16: "I need crude oil and natural gas options to be added to the
scalp section" (paper first; full and mini contracts). MCX options are options
on the FUTURES, on Dhan's MCX_COMM segment, trading to 23:30 (23:55 while the
US is off daylight time). Dhan lists their quantity as one per lot, so the
contract size -- 100 barrels, 1,250 mmBtu -- is the lot size the desk uses.
"""

import asyncio
import unittest
from datetime import date, datetime
from unittest import mock

import broker.dhan as dhan
import scalp

HEADER = (
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,"
    "SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
    "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME"
)
ROWS = [
    "NSE,D,40704,OPTIDX,0,NIFTY-Oct2026-22650-CE,65.0,NIFTY 19 OCT 22650 CALL,2026-10-19 14:30:00,22650.00000,CE,5.0000,W,OP,NA,NIFTY",
    "NSE,D,40705,OPTIDX,0,NIFTY-Oct2026-22700-CE,65.0,NIFTY 19 OCT 22700 CALL,2026-10-19 14:30:00,22700.00000,CE,5.0000,W,OP,NA,NIFTY",
    "MCX,M,582829,OPTFUT,0,CRUDEOIL-17Nov2026-7900-CE,1.0,CRUDEOIL 17 NOV 7900 CALL,2026-11-17 23:30:00,7900.00000,CE,10.0000,M,OPTFUT,NA,CRUDEOIL",
    "MCX,M,582932,OPTFUT,0,CRUDEOIL-17Nov2026-9100-CE,1.0,CRUDEOIL 17 NOV 9100 CALL,2026-11-17 23:30:00,9100.00000,CE,10.0000,M,OPTFUT,NA,CRUDEOIL",
    "MCX,M,581193,OPTFUT,0,NATURALGAS-23Oct2026-305-CE,1.0,NATURALGAS 23 OCT 305 CALL,2026-10-23 23:30:00,305.00000,CE,5.0000,M,OPTFUT,NA,NATURALGAS",
    "MCX,M,581195,OPTFUT,0,NATGASMINI-23Oct2026-305-CE,1.0,NATURALGASM 23 OCT 305 CALL,2026-10-23 23:30:00,305.00000,CE,5.0000,M,OPTFUT,NA,NATGASMINI",
    "MCX,M,569900,FUTCOM,0,CRUDEOIL-19Oct2026-FUT,1.0,CRUDEOIL OCT FUT,2026-10-19 23:30:00,0,XX,100.0000,M,FUTCOM,NA,CRUDEOIL",
    "MCX,M,573422,FUTCOM,0,CRUDEOIL-19Nov2026-FUT,1.0,CRUDEOIL NOV FUT,2026-11-19 23:30:00,0,XX,100.0000,M,FUTCOM,NA,CRUDEOIL",
    "MCX,M,570750,FUTCOM,0,NATURALGAS-27Oct2026-FUT,1.0,NATURALGAS OCT FUT,2026-10-27 23:30:00,0,XX,10.0000,M,FUTCOM,NA,NATURALGAS",
    # An MCX index option is not one of ours.
    "MCX,M,600001,OPTIDX,0,MCXBULLDEX-17Nov2026-20000-CE,1.0,BULLDEX,2026-11-17 23:30:00,20000.00000,CE,5.0000,M,OPTIDX,NA,MCXBULLDEX",
]


class _Response:
    def raise_for_status(self):
        pass

    def iter_lines(self):
        for line in [HEADER, *ROWS]:
            yield line.encode()


class TheContractListLoadsTheCommodities(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile

        cls.tmp = tempfile.TemporaryDirectory()
        cls.patches = [
            mock.patch.object(dhan, "SCRIP_CACHE_DIR", cls.tmp.name),
            mock.patch.object(dhan._http_session, "get", lambda *a, **k: _Response()),
        ]
        for p in cls.patches:
            p.start()
        S = dhan.ScripMaster
        cls.saved = (S._options_cache, S._expiry_cache, S._lot_cache, S._futures_cache, S._loaded_date)
        S._options_cache, S._loaded_date = {}, None
        assert S.ensure_loaded()

    @classmethod
    def tearDownClass(cls):
        S = dhan.ScripMaster
        S._options_cache, S._expiry_cache, S._lot_cache, S._futures_cache, S._loaded_date = cls.saved
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def test_mcx_options_are_found_with_their_contract_size(self):
        S = dhan.ScripMaster
        self.assertEqual(S.lookup("CRUDEOIL", 9100, "2026-11-17", "CE"), "582932")
        self.assertEqual(S.lookup("NATURALGAS", 305, "2026-10-23", "CE"), "581193")
        self.assertEqual(S.get_lot_size("CRUDEOIL", "2026-11-17"), 100)
        self.assertEqual(S.get_lot_size("NATGASMINI", "2026-10-23"), 250)
        self.assertEqual(S.get_expiries("CRUDEOIL"), ["2026-11-17"])

    def test_index_options_are_unchanged_and_mcx_index_options_left_out(self):
        S = dhan.ScripMaster
        self.assertEqual(S.lookup("NIFTY", 22700, "2026-10-19", "CE"), "40705")
        self.assertEqual(S.get_lot_size("NIFTY", "2026-10-19"), 65)
        self.assertNotIn("MCXBULLDEX", S._expiry_cache)

    def test_each_option_finds_the_future_it_is_struck_against(self):
        S = dhan.ScripMaster
        self.assertEqual(S.mcx_future_for("CRUDEOIL", "2026-11-17"), "573422")  # the 19-Nov future
        self.assertEqual(S.mcx_future_for("NATURALGAS", "2026-10-23"), "570750")


class ExchangeRouting(unittest.TestCase):
    def test_segments(self):
        self.assertEqual(dhan.option_exchange_segment("CRUDEOIL"), "MCX_COMM")
        self.assertEqual(dhan.option_exchange_segment("SENSEX"), "BSE_FNO")
        self.assertEqual(dhan.option_exchange_segment("NIFTY"), "NSE_FNO")
        self.assertEqual([dhan.option_feed_exchange(u) for u in ("NATGASMINI", "SENSEX", "NIFTY")], [5, 8, 2])
        self.assertEqual(dhan.option_instrument_type("NATURALGAS"), "OPTFUT")
        self.assertEqual(dhan.option_instrument_type("NIFTY"), "OPTIDX")

    def test_the_price_calls_carry_mcx_comm(self):
        sent = []

        class _R:
            status_code = 200

            def json(self):
                return {"data": {}}

        def fake(method, url, **kw):
            sent.append(kw["json"])
            return _R()

        client = dhan.DhanClient.__new__(dhan.DhanClient)
        client.base_url = "https://x"
        client._allow_token_refresh, client.refresh_access_token = False, None
        with (
            mock.patch.object(dhan, "_request_with_retry", fake),
            mock.patch.object(dhan, "_throttle_marketfeed", lambda: None),
            mock.patch.object(dhan.DhanClient, "headers", new_callable=mock.PropertyMock, return_value={}),
        ):
            client.get_ltp([582932], exchange_segment="MCX_COMM")
            client.get_ltp_multi({"MCX_COMM": [573422], "NSE_FNO": [40705]})
        self.assertEqual(sent[0]["MCX_COMM"], [582932])
        self.assertEqual(sent[1]["MCX_COMM"], [573422])
        self.assertEqual(sent[1]["NSE_FNO"], [40705])


class ThePaperEngineReadsMCXPrices(unittest.TestCase):
    def test_one_call_carries_each_trade_under_its_own_segment(self):
        from types import SimpleNamespace

        asked = []

        class _Dhan:
            def get_ltp_multi(self, segments):
                asked.append(segments)
                return {"MCX_COMM": {"582932": {"last_price": 524.4}}, "NSE_FNO": {"40705": {"last_price": 101.5}}}

        engine = scalp.ScalpEngine(_Dhan())
        trades = [
            (1, SimpleNamespace(underlying="CRUDEOIL", strike=9100, expiry="2026-11-17", option_type="CE")),
            (2, SimpleNamespace(underlying="NIFTY", strike=22700, expiry="2026-10-19", option_type="CE")),
        ]
        ids = {"CRUDEOIL": "582932", "NIFTY": "40705"}
        with mock.patch.object(scalp.ScripMaster, "lookup", lambda u, *a: ids[u]):
            out = asyncio.run(engine._fetch_all_ltps(trades))
        self.assertEqual(out, {1: 524.4, 2: 101.5})
        self.assertEqual(asked[0], {"MCX_COMM": [582932], "NSE_FNO": [40705]})


class MCXHours(unittest.TestCase):
    def test_the_close_follows_us_daylight_time(self):
        self.assertEqual(scalp.mcx_session_close(date(2026, 10, 16)), (23, 30))
        self.assertEqual(scalp.mcx_session_close(date(2026, 10, 31)), (23, 30))
        self.assertEqual(scalp.mcx_session_close(date(2026, 11, 2)), (23, 55))  # US clocks went back on 1-Nov
        self.assertEqual(scalp.mcx_session_close(date(2027, 3, 15)), (23, 30))  # and forward on 14-Mar-2027

    def test_the_watch_runs_into_the_night_only_with_an_mcx_scalp_open(self):
        night = datetime(2026, 10, 16, 22, 5, tzinfo=scalp.IST)
        self.assertFalse(scalp._scalp_market_watch_open(night))
        self.assertTrue(scalp._scalp_market_watch_open(night, mcx=True))
        self.assertFalse(scalp._scalp_market_watch_open(datetime(2026, 10, 16, 23, 50, tzinfo=scalp.IST), mcx=True))
        self.assertFalse(
            scalp._scalp_market_watch_open(datetime(2026, 10, 17, 12, 0, tzinfo=scalp.IST), mcx=True)
        )  # Saturday


class StepOneIsPaper(unittest.TestCase):
    def test_a_live_mcx_scalp_is_refused(self):
        engine = scalp.ScalpEngine.__new__(scalp.ScalpEngine)
        engine.zerodha = None
        out = asyncio.run(
            engine.enter_trade(
                underlying="CRUDEOIL",
                strike=9100,
                expiry="2026-11-17",
                option_type="CE",
                transaction_type="BUY",
                lots=1,
                lot_size=100,
                mode="live",
            )
        )
        self.assertEqual(out["status"], "error")
        self.assertIn("paper", out["message"])

    def test_the_entry_takes_the_contract_size_not_the_form(self):
        import app

        req = app.ScalpEntryReq(
            underlying="naturalgas",
            strike=305,
            expiry="2026-10-23",
            option_type="CE",
            transaction_type="BUY",
            lots=1,
            lot_size=65,
            mode="paper",
        )
        app._validate_scalp_entry_request(req)
        self.assertEqual((req.underlying, req.lot_size), ("NATURALGAS", 1250))


if __name__ == "__main__":
    unittest.main()
