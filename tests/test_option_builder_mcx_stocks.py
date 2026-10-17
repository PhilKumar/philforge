"""The Option Builder beyond the indices: MCX's crude and gas, and F&O stocks.

Phil, 2026-10-16: "work on the option builder as well with these commodity
and stocks addition". Dhan's option chain answers for both (read only on
prod, 17-Oct-2026): a stock is asked by its NSE share id on NSE_EQ (RELIANCE
2885, spot 1211.2, 97 strikes); a commodity by its FUTURE on MCX_COMM (crude's
19-Nov future 573422: expiries 17-Nov and 16-Dec). The margin calculator
counts MCX in lots.
"""

import unittest
from datetime import datetime
from unittest import mock

import option_builder as ob

IST = ob.IST


class _Client:
    def __init__(self):
        self.asked = []

    def get_option_chain(self, scrip, seg, expiry):
        self.asked.append(("chain", scrip, seg, expiry))
        side = {"last_price": 30.15, "security_id": "1", "oi": 10, "previous_oi": 8, "greeks": {}}
        return {
            "last_price": 1211.2,
            "oc": {f"{k}.000000": {"ce": side, "pe": side} for k in (1180, 1190, 1200, 1210, 1220)},
        }

    def get_option_chain_expiries(self, scrip, seg):
        self.asked.append(("expiries", scrip, seg))
        return ["2026-11-17", "2026-12-16"]


class _Base(unittest.TestCase):
    def _loaded(self, *a, **k):
        self.loads += 1
        return True

    def setUp(self):
        self.loads = 0
        S = ob.ScripMaster
        futures = {"CRUDEOIL": {"2026-10-19": "569900", "2026-11-19": "573422", "2026-12-17": "580001"}}
        self.patches = [
            mock.patch.object(S, "_stock_cache", {"RELIANCE", "BAJAJ-AUTO"}),
            mock.patch.object(S, "_futures_cache", futures),
            mock.patch.object(S, "ensure_loaded", self._loaded),
            mock.patch.object(S, "lookup_equity", lambda sym: {"security_id": {"RELIANCE": "2885"}.get(sym, "")}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()


class WhatIsOffered(_Base):
    def test_indices_commodities_and_listed_stocks(self):
        for name in ("nifty", "CRUDEOIL", "natgasmini", "reliance", "BAJAJ-AUTO"):
            self.assertEqual(ob.normalize_underlying(name), name.upper())

    def test_anything_else_is_refused_without_a_download(self):
        with self.assertRaisesRegex(ob.OptionBuilderError, "F&O stock"):
            ob.normalize_underlying("NASDAQ")
        self.assertEqual(self.loads, 0)

    def test_where_each_chain_is_asked(self):
        self.assertEqual(ob.underlying_spec("NIFTY")["scrip"], 13)
        stock = ob.underlying_spec("RELIANCE")
        self.assertEqual(
            (stock["scrip"], stock["seg"], stock["fno"], stock["kind"]), (2885, "NSE_EQ", "NSE_FNO", "stock")
        )
        # A commodity is asked by the future its options expire into.
        self.assertEqual(ob.underlying_spec("CRUDEOIL", "2026-11-17")["scrip"], 573422)
        self.assertEqual(ob.underlying_spec("CRUDEOIL", "2026-12-16")["scrip"], 580001)
        mcx = ob.underlying_spec("CRUDEOIL", "2026-11-17")
        self.assertEqual((mcx["seg"], mcx["fno"], mcx["kind"]), ("MCX_COMM", "MCX_COMM", "mcx"))


class TheChain(_Base):
    def _cache(self, open_=True):
        return ob.ChainCache(clock=lambda: 0.0, sleep=lambda s: None, session_open=lambda: open_)

    def test_a_stock_chain_comes_off_the_share(self):
        client = _Client()
        chain = self._cache().get(client, "RELIANCE", "2026-10-27", 500)
        self.assertEqual(client.asked[-1], ("chain", 2885, "NSE_EQ", "2026-10-27"))
        self.assertEqual((chain["label"], chain["kind"], chain["expiry_close"]), ("RELIANCE", "stock", "15:30"))

    def test_a_crude_chain_comes_off_its_future_and_closes_at_2330(self):
        client = _Client()
        chain = self._cache().get(client, "CRUDEOIL", "2026-12-16", 100)
        self.assertEqual(client.asked[-1], ("chain", 580001, "MCX_COMM", "2026-12-16"))
        self.assertEqual((chain["label"], chain["expiry_close"]), ("CRUDE OIL", "23:30"))

    def test_crude_expiring_today_stays_listed_after_nses_close(self):
        evening = datetime(2026, 11, 17, 21, 0, tzinfo=IST)
        self.assertEqual(
            ob.open_expiries(["2026-11-17", "2026-12-16"], evening, close=ob.expiry_close("CRUDEOIL")),
            ["2026-11-17", "2026-12-16"],
        )
        self.assertEqual(ob.open_expiries(["2026-11-17", "2026-12-16"], evening), ["2026-12-16"])

    def test_mcx_is_live_into_the_night(self):
        night = datetime(2026, 10, 16, 22, 0, tzinfo=IST)
        self.assertTrue(ob.session_open_for("CRUDEOIL", night))
        self.assertFalse(ob.session_open_for("NIFTY", night))
        self.assertFalse(ob.session_open_for("RELIANCE", night))


class MarginAndPositions(_Base):
    def test_the_margin_calculator_gets_mcx_in_lots_and_the_rest_in_units(self):
        crude = {"underlying": "CRUDEOIL", "side": "SELL", "lots": 2, "security_id": "582932", "price": 580.0}
        stock = {"underlying": "RELIANCE", "side": "BUY", "lots": 1, "security_id": "125847", "price": 30.15}
        self.assertEqual(ob.margin_scrip_list([crude], 100, "INTRADAY")[0]["quantity"], 2)
        self.assertEqual(ob.margin_scrip_list([crude], 100, "INTRADAY")[0]["exchangeSegment"], "MCX_COMM")
        row = ob.margin_scrip_list([stock], 500, "MARGIN")[0]
        self.assertEqual((row["quantity"], row["exchangeSegment"], row["productType"]), (500, "NSE_FNO", "MARGIN"))

    def test_stock_and_commodity_positions_become_legs(self):
        rows = [
            {"exchangeSegment": "NSE_FNO", "drvOptionType": "PUT", "netQty": -75, "tradingSymbol": "BAJAJ-AUTO-Oct2026-12600-PE",
             "drvStrikePrice": 12600, "drvExpiryDate": "2026-10-27", "sellAvg": 210.0, "securityId": "110001", "productType": "MARGIN"},
            {"exchangeSegment": "MCX_COMM", "drvOptionType": "CALL", "netQty": 100, "tradingSymbol": "CRUDEOIL-Nov2026-9100-CE",
             "drvStrikePrice": 9100, "drvExpiryDate": "2026-11-17", "buyAvg": 580.0, "securityId": "582932", "productType": "INTRADAY"},
            {"exchangeSegment": "NSE_FNO", "drvOptionType": "CALL", "netQty": 10, "tradingSymbol": "NOTOURS-Oct2026-10-CE"},
        ]  # fmt: skip
        legs = ob.legs_from_dhan_positions(rows)
        self.assertEqual(
            [(leg["underlying"], leg["side"], leg["quantity"]) for leg in legs],
            [("BAJAJ-AUTO", "SELL", 75), ("CRUDEOIL", "BUY", 100)],
        )


if __name__ == "__main__":
    unittest.main()
