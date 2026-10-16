"""Crude oil and natural gas options on the Scalp desk, paper and live.

Phil, 2026-10-16: "I need crude oil and natural gas options to be added to the
scalp section" (full and mini contracts), then "build live MCX for both".
MCX options are options on the FUTURES, on Dhan's MCX_COMM segment, trading to
23:30 (23:55 while the US is off daylight time). The desk counts units -- one
crude lot is 100 barrels -- while Dhan and Zerodha both count MCX in LOTS, so
the brokers convert at their edge, both ways.
"""

import asyncio
import json
import unittest
from datetime import date, datetime
from unittest import mock

import broker.dhan as dhan
import broker.zerodha as zerodha
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


_module = {}


def setUpModule():
    import tempfile

    _module["tmp"] = tempfile.TemporaryDirectory()
    _module["patches"] = [
        mock.patch.object(dhan, "SCRIP_CACHE_DIR", _module["tmp"].name),
        mock.patch.object(dhan._http_session, "get", lambda *a, **k: _Response()),
    ]
    for p in _module["patches"]:
        p.start()
    S = dhan.ScripMaster
    _module["saved"] = (S._options_cache, S._expiry_cache, S._lot_cache, S._futures_cache, S._loaded_date)
    S._options_cache, S._loaded_date = {}, None
    assert S.ensure_loaded()


def tearDownModule():
    S = dhan.ScripMaster
    S._options_cache, S._expiry_cache, S._lot_cache, S._futures_cache, S._loaded_date = _module["saved"]
    for p in _module["patches"]:
        p.stop()
    _module["tmp"].cleanup()


class TheContractListLoadsTheCommodities(unittest.TestCase):
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


class _Resp:
    def __init__(self, body, status=200):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


def _dhan_client(answer):
    """A DhanClient whose every request is recorded and answered with `answer`."""
    sent = []

    def fake(method, url, **kw):
        sent.append({"method": method, "url": url, "json": kw.get("json")})
        return _Resp(answer(method, url) if callable(answer) else answer)

    client = dhan.DhanClient.__new__(dhan.DhanClient)
    client.base_url, client.client_id = "https://x", "1"
    client._allow_token_refresh, client.refresh_access_token = False, None
    patches = [
        mock.patch.object(dhan, "_request_with_retry", fake),
        mock.patch.object(dhan.DhanClient, "headers", new_callable=mock.PropertyMock, return_value={}),
        mock.patch.object(dhan.DhanClient, "_is_configured", lambda self: True),
    ]
    return client, sent, patches


class DhanCountsMCXInLots(unittest.TestCase):
    def _run(self, answer, call):
        client, sent, patches = _dhan_client(answer)
        for p in patches:
            p.start()
        try:
            return call(client), sent
        finally:
            for p in patches:
                p.stop()

    def test_a_super_order_for_one_crude_lot_sends_one_on_crudes_tick(self):
        _, sent = self._run(
            {"orderId": "9", "orderStatus": "PENDING"},
            lambda c: c.place_super_order(
                underlying="CRUDEOIL",
                strike_price=9100,
                option_type="CE",
                expiry="2026-11-17",
                transaction_type="BUY",
                quantity=100,  # one lot of 100 barrels, as the desk holds it
                target_price=600.07,
                stop_loss_price=499.96,
            ),
        )
        body = sent[0]["json"]
        self.assertEqual((body["exchangeSegment"], body["securityId"], body["quantity"]), ("MCX_COMM", "582932", 1))
        self.assertEqual((body["targetPrice"], body["stopLossPrice"]), (600.1, 500.0))

    def test_an_index_order_is_untouched(self):
        _, sent = self._run(
            {"orderId": "9"},
            lambda c: c.place_option_order(
                underlying="NIFTY",
                strike_price=22700,
                option_type="CE",
                expiry="2026-10-19",
                transaction_type="SELL",
                quantity=65,
                order_type="LIMIT",
                price=101.52,
            ),
        )
        self.assertEqual((sent[0]["json"]["quantity"], sent[0]["json"]["price"]), (65, 101.5))

    def test_a_stop_and_an_exit_go_as_lots(self):
        _, sent = self._run(
            {"orderId": "9"},
            lambda c: c.place_sl_order(
                underlying="NATURALGAS",
                strike_price=305,
                option_type="CE",
                expiry="2026-10-23",
                transaction_type="SELL",
                quantity=2500,
                trigger_price=18.02,
                price=17.13,
            ),
        )
        body = sent[0]["json"]
        self.assertEqual((body["exchangeSegment"], body["quantity"]), ("MCX_COMM", 2))
        self.assertEqual((body["triggerPrice"], body["price"]), (18.0, 17.15))

    def test_part_of_a_lot_or_an_unknown_contract_is_never_sent(self):
        with self.assertRaisesRegex(ValueError, "whole number of CRUDEOIL lots"):
            self._run({}, lambda c: c.place_option_order("CRUDEOIL", 9100, "CE", "2026-11-17", "BUY", 150))
        with self.assertRaisesRegex(ValueError, "Unknown MCX contract"):
            self._run({}, lambda c: c.place_order("777777", "MCX_COMM", "BUY", 1))
        with self.assertRaises(ValueError):
            self._run({}, lambda c: c.place_forever_order("582932", "MCX_COMM", "BUY", 100))

    def test_a_modify_for_an_mcx_order_names_its_commodity(self):
        _, sent = self._run(
            {"orderId": "9"},
            lambda c: c.modify_super_order(
                "9", "ENTRY_LEG", quantity=200, stop_loss_price=499.96, underlying="CRUDEOIL"
            ),
        )
        self.assertEqual((sent[0]["json"]["quantity"], sent[0]["json"]["stopLossPrice"]), (2, 500.0))
        _, sent = self._run(
            {"orderId": "9"}, lambda c: c.modify_order("9", trigger_price=17.13, underlying="NATURALGAS")
        )
        self.assertEqual(sent[0]["json"]["triggerPrice"], 17.15)

    def test_what_dhan_reports_comes_back_as_units(self):
        positions = [
            {"exchangeSegment": "MCX_COMM", "securityId": "582932", "netQty": 1, "buyQty": 1, "sellQty": 0},
            {"exchangeSegment": "NSE_FNO", "securityId": "40705", "netQty": 65, "buyQty": 65, "sellQty": 0},
        ]
        rows, _ = self._run(positions, lambda c: c.get_positions())
        self.assertEqual([r["netQty"] for r in rows], [100, 65])
        order = [{"exchangeSegment": "MCX_COMM", "securityId": "582932", "quantity": 2, "filledQty": 1}]
        row, _ = self._run(order, lambda c: c.get_order_status("9"))
        self.assertEqual((row["quantity"], row["filledQty"]), (200, 100))
        supers = [
            {
                "exchangeSegment": "MCX_COMM",
                "securityId": "581193",
                "quantity": 1,
                "filledQty": 1,
                "legDetails": [{"legName": "STOP_LOSS_LEG", "remainingQuantity": 1}],
            }
        ]
        rows, _ = self._run(supers, lambda c: c.get_super_orders())
        self.assertEqual((rows[0]["filledQty"], rows[0]["legDetails"][0]["remainingQuantity"]), (1250, 1250))
        trades, _ = self._run(
            [{"exchangeSegment": "MCX_COMM", "securityId": "582932", "tradedQuantity": 1}], lambda c: c.get_trades()
        )
        self.assertEqual(trades[0]["tradedQuantity"], 100)

    def test_a_row_is_converted_once(self):
        row = {"quantity": 1}
        dhan.mcx_row_to_units(row, 100)
        dhan.mcx_row_to_units(row, 100)
        self.assertEqual(row["quantity"], 100)


KITE_ROWS = [
    {"instrument_token": "149230599", "exchange_token": "582932", "tradingsymbol": "CRUDEOIL26NOV9100CE",
     "name": "CRUDEOIL", "expiry": "2026-11-17", "strike": "9100", "tick_size": "0.1", "lot_size": "1",
     "instrument_type": "CE", "segment": "MCX-OPT", "exchange": "MCX"},
    {"instrument_token": "148785415", "exchange_token": "581193", "tradingsymbol": "NATURALGAS26OCT305CE",
     "name": "NATURALGAS", "expiry": "2026-10-23", "strike": "305", "tick_size": "0.05", "lot_size": "1",
     "instrument_type": "CE", "segment": "MCX-OPT", "exchange": "MCX"},
    {"instrument_token": "1", "exchange_token": "2", "tradingsymbol": "GOLD26DEC150000CE",
     "name": "GOLD", "expiry": "2026-12-24", "strike": "150000", "tick_size": "1", "lot_size": "1",
     "instrument_type": "CE", "segment": "MCX-OPT", "exchange": "MCX"},
    {"instrument_token": "12345", "exchange_token": "40705", "tradingsymbol": "NIFTY26O1922700CE",
     "name": "NIFTY", "expiry": "2026-10-19", "strike": "22700", "tick_size": "0.05", "lot_size": "65",
     "instrument_type": "CE", "segment": "NFO-OPT", "exchange": "NFO"},
]  # fmt: skip


class _KiteSession:
    def __init__(self, answers):
        self.answers, self.sent = answers, []

    def request(self, method, url, **kw):
        self.sent.append({"method": method, "url": url, "data": kw.get("data")})
        path = url.split("api.kite.trade", 1)[1]
        return _Resp(self.answers.get((method, path), {"status": "success", "data": {"order_id": "Z1"}}))


class ZerodhaCountsMCXInLots(unittest.TestCase):
    def setUp(self):
        K = zerodha.KiteInstruments
        self.saved = (K._by_key, K._by_symbol, K._loaded_on)
        K.load_rows(KITE_ROWS)

    def tearDown(self):
        K = zerodha.KiteInstruments
        K._by_key, K._by_symbol, K._loaded_on = self.saved

    def _client(self, answers=None):
        session = _KiteSession(answers or {})
        return zerodha.ZerodhaClient("k", "t", session=session), session

    def test_the_contract_list_keeps_crude_and_gas_and_leaves_gold(self):
        info = zerodha.KiteInstruments.contract("CRUDEOIL", 9100, "2026-11-17", "CE")
        self.assertEqual((info["exchange"], info["multiplier"], info["tick_size"]), ("MCX", 100, 0.1))
        self.assertIsNone(zerodha.KiteInstruments.contract("GOLD", 150000, "2026-12-24", "CE"))
        self.assertEqual(zerodha.KiteInstruments.contract("NIFTY", 22700, "2026-10-19", "CE")["multiplier"], 1)

    def test_a_limit_for_one_crude_lot_sends_one_on_crudes_tick(self):
        client, session = self._client()
        client.place_option_order("CRUDEOIL", 9100, "CE", "2026-11-17", "BUY", 100, order_type="LIMIT", price=535.07)
        form = session.sent[0]["data"]
        self.assertEqual((form["exchange"], form["tradingsymbol"], form["quantity"]), ("MCX", "CRUDEOIL26NOV9100CE", 1))
        self.assertEqual((form["price"], form["product"]), (535.1, "MIS"))

    def test_zerodha_takes_no_market_or_sl_m_on_mcx_options(self):
        client, session = self._client()
        for kind in ("MARKET", "SL-M"):
            with self.assertRaisesRegex(zerodha.DhanOrderError, "only LIMIT and SL"):
                client.place_option_order("NATURALGAS", 305, "CE", "2026-10-23", "SELL", 1250, order_type=kind)
        self.assertEqual(session.sent, [])

    def test_a_stop_modify_goes_on_the_commoditys_tick(self):
        client, session = self._client()
        client.modify_order("Z1", price=477.12, trigger_price=502.04, underlying="CRUDEOIL")
        self.assertEqual((session.sent[0]["data"]["price"], session.sent[0]["data"]["trigger_price"]), (477.1, 502.0))

    def test_what_zerodha_reports_comes_back_as_units(self):
        mcx = {"exchange": "MCX", "tradingsymbol": "CRUDEOIL26NOV9100CE"}
        nfo = {"exchange": "NFO", "tradingsymbol": "NIFTY26O1922700CE"}
        client, _ = self._client(
            {
                ("GET", "/orders/Z1"): {
                    "status": "success",
                    "data": [{**mcx, "order_id": "Z1", "status": "COMPLETE", "quantity": 1, "filled_quantity": 1}],
                },
                ("GET", "/portfolio/positions"): {
                    "status": "success",
                    "data": {"net": [{**mcx, "quantity": 1, "buy_quantity": 1}, {**nfo, "quantity": 65}]},
                },
                ("GET", "/trades"): {"status": "success", "data": [{**mcx, "quantity": 1}]},
            }
        )
        with mock.patch.object(zerodha, "_key_to_dhan_id", lambda key: "582932" if "CRUDE" in key else "40705"):
            row = client.get_order_status("Z1")
            positions = client.get_positions()
            trades = client.get_trades()
        self.assertEqual((row["quantity"], row["filledQty"]), (100, 100))
        self.assertEqual([(p["securityId"], p["netQty"]) for p in positions], [("582932", 100), ("40705", 65)])
        self.assertEqual(trades[0]["tradedQuantity"], 100)


class LiveMCXScalps(unittest.TestCase):
    def test_a_live_crude_scalp_goes_to_dhan_as_a_super_order(self):
        placed = {}

        class _Dhan:
            def get_option_ltp(self, *a):
                return 524.4

            def place_super_order(self, **kw):
                placed.update(kw)
                return {"orderId": "SO1", "orderStatus": "PENDING"}

        engine = scalp.ScalpEngine(_Dhan())
        engine.start = lambda: None
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
                target_points=20,
                sl_points=10,
            )
        )
        self.assertEqual(out["status"], "ok", out)
        # The engine hands Dhan units; DhanClient turns them into the one lot.
        self.assertEqual((placed["underlying"], placed["quantity"]), ("CRUDEOIL", 100))
        self.assertEqual((placed["target_price"], placed["stop_loss_price"]), (544.4, 514.4))

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
