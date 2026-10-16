"""NSE stock options on the Scalp desk, paper and live.

Phil, 2026-10-16: "add all F&O stocks both live and paper". Dhan's contract
file lists them as OPTSTK, on NSE and again on BSE; only NSE's are traded. A
stock's name can carry a hyphen (BAJAJ-AUTO), its strikes can be fractional
(CROMPTON 147.5), and its lot differs stock by stock -- so all three are read
from the file, never assumed. Held into expiry in the money, a stock option
is settled by DELIVERY of the shares, so a carried live one is refused close
to its expiry.
"""

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import broker.dhan as dhan
import broker.zerodha as zerodha

TODAY = datetime.now(ZoneInfo("Asia/Kolkata")).date()
NEAR = (TODAY + timedelta(days=3)).isoformat()
FAR = (TODAY + timedelta(days=30)).isoformat()


def _mon(day: str) -> str:
    return datetime.fromisoformat(day).strftime("%b%Y")


HEADER = (
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,"
    "SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,"
    "SEM_EXPIRY_FLAG,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME"
)


def _row(exch, sid, inst, tsym, lot, expiry, strike, opt):
    return f"{exch},D,{sid},{inst},0,{tsym},{lot},X,{expiry} 14:30:00,{strike},{opt},5.0000,M,OP,NA,"


ROWS = [
    _row("NSE", 40705, "OPTIDX", f"NIFTY-{_mon(FAR)}-22700-CE", "65.0", FAR, "22700.00000", "CE"),
    _row("NSE", 106474, "OPTSTK", f"RELIANCE-{_mon(FAR)}-1400-CE", "500.0", FAR, "1400.00000", "CE"),
    _row("NSE", 106475, "OPTSTK", f"RELIANCE-{_mon(FAR)}-1420-CE", "500.0", FAR, "1420.00000", "CE"),
    _row("NSE", 106476, "OPTSTK", f"RELIANCE-{_mon(FAR)}-1440-CE", "500.0", FAR, "1440.00000", "CE"),
    _row("NSE", 106477, "OPTSTK", f"RELIANCE-{_mon(FAR)}-1460-CE", "500.0", FAR, "1460.00000", "CE"),
    _row("NSE", 106478, "OPTSTK", f"RELIANCE-{_mon(NEAR)}-1400-PE", "500.0", NEAR, "1400.00000", "PE"),
    _row("NSE", 110001, "OPTSTK", f"BAJAJ-AUTO-{_mon(FAR)}-12600-PE", "75.0", FAR, "12600.00000", "PE"),
    _row("NSE", 120001, "OPTSTK", f"CROMPTON-{_mon(FAR)}-147.5-CE", "1800.0", FAR, "147.50000", "CE"),
    # BSE lists the same stocks; they are not traded here.
    _row("BSE", 1100002, "OPTSTK", f"BAJAJ-AUTO-{_mon(FAR)}-12600-PE", "75.0", FAR, "12600.00000", "PE"),
    _row("BSE", 1100026, "OPTSTK", f"IDEA-{_mon(FAR)}-22-PE", "71475.0", FAR, "22.00000", "PE"),
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
    _module["saved"] = (
        S._options_cache,
        S._expiry_cache,
        S._lot_cache,
        S._futures_cache,
        S._stock_cache,
        S._loaded_date,
    )
    S._options_cache, S._loaded_date = {}, None
    assert S.ensure_loaded()


def tearDownModule():
    S = dhan.ScripMaster
    (S._options_cache, S._expiry_cache, S._lot_cache, S._futures_cache, S._stock_cache, S._loaded_date) = _module[
        "saved"
    ]
    for p in _module["patches"]:
        p.stop()
    _module["tmp"].cleanup()


class TheContractListLoadsNSEStocks(unittest.TestCase):
    def test_stocks_are_named_whole_hyphen_and_all(self):
        S = dhan.ScripMaster
        self.assertEqual(S.stock_underlyings(), ["BAJAJ-AUTO", "CROMPTON", "RELIANCE"])
        self.assertEqual(S.lookup("BAJAJ-AUTO", 12600, FAR, "PE"), "110001")  # NSE's, not BSE's 1100002
        self.assertEqual(S.get_lot_size("RELIANCE", FAR), 500)
        self.assertEqual(S.get_lot_size("BAJAJ-AUTO", FAR), 75)
        self.assertNotIn("IDEA", S.stock_underlyings())  # listed on BSE only here

    def test_a_fractional_strike_is_found_however_it_is_asked(self):
        S = dhan.ScripMaster
        for asked in (147.5, "147.5", "147.50000"):
            self.assertEqual(S.lookup("CROMPTON", asked, FAR, "CE"), "120001")
        self.assertEqual(dhan.strike_text(24350.0), "24350")
        self.assertEqual(dhan.strike_text("147.50000"), "147.5")

    def test_a_stocks_strike_gap_is_measured(self):
        self.assertEqual(dhan.ScripMaster.get_strike_step("RELIANCE", FAR), 20.0)

    def test_index_options_are_untouched(self):
        self.assertEqual(dhan.ScripMaster.lookup("NIFTY", 22700, FAR, "CE"), "40705")
        self.assertEqual(dhan.option_instrument_type("NIFTY"), "OPTIDX")

    def test_a_stock_option_is_an_optstk_on_nse_fno(self):
        self.assertTrue(dhan.is_stock_underlying("RELIANCE"))
        self.assertFalse(dhan.is_stock_underlying("NIFTY"))
        self.assertFalse(dhan.is_stock_underlying("CRUDEOIL"))
        self.assertEqual(dhan.option_instrument_type("RELIANCE"), "OPTSTK")
        self.assertEqual(dhan.option_exchange_segment("RELIANCE"), "NSE_FNO")
        self.assertEqual(dhan.option_feed_exchange("M&M"), 2)


class ZerodhaFindsTheSameContracts(unittest.TestCase):
    def setUp(self):
        K = zerodha.KiteInstruments
        self.saved = (K._by_key, K._by_symbol, K._loaded_on)
        K.load_rows(
            [
                {
                    "instrument_token": "1",
                    "tradingsymbol": "CROMPTON26OCT147.5CE",
                    "name": "CROMPTON",
                    "expiry": FAR,
                    "strike": "147.5",
                    "tick_size": "0.05",
                    "lot_size": "1800",
                    "instrument_type": "CE",
                    "exchange": "NFO",
                },
                {
                    "instrument_token": "2",
                    "tradingsymbol": "BAJAJ-AUTO26OCT12600PE",
                    "name": "BAJAJ-AUTO",
                    "expiry": FAR,
                    "strike": "12600",
                    "tick_size": "0.05",
                    "lot_size": "75",
                    "instrument_type": "PE",
                    "exchange": "NFO",
                },
            ]  # fmt: skip
        )

    def tearDown(self):
        K = zerodha.KiteInstruments
        K._by_key, K._by_symbol, K._loaded_on = self.saved

    def test_kite_and_dhan_meet_on_a_fractional_strike(self):
        info = zerodha.KiteInstruments.contract("CROMPTON", 147.5, FAR, "CE")
        self.assertEqual(
            (info["tradingsymbol"], info["lot_size"], info["multiplier"]), ("CROMPTON26OCT147.5CE", 1800, 1)
        )
        key = zerodha.KiteInstruments.key_for_symbol("NFO", "CROMPTON26OCT147.5CE")
        self.assertEqual(zerodha._key_to_dhan_id(key), "120001")
        self.assertIsNotNone(zerodha.KiteInstruments.contract("BAJAJ-AUTO", 12600, FAR, "PE"))


class TheEntryTakesTheStocksOwnTerms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app

        cls.app = app

    def _req(self, **kw):
        base = dict(
            underlying="crompton", strike=147.5, expiry=FAR, option_type="CE", transaction_type="BUY",
            lots=1, lot_size=65, mode="paper", product_type="INTRADAY",
        )  # fmt: skip
        base.update(kw)
        return self.app.ScalpEntryReq(**base)

    def test_the_lot_comes_from_the_contract_file_and_the_half_strike_stays(self):
        req = self._req()
        self.app._validate_scalp_entry_request(req)
        self.assertEqual((req.underlying, req.lot_size, req.strike), ("CROMPTON", 1800, 147.5))
        req = self._req(underlying="RELIANCE", strike=1400)
        self.app._validate_scalp_entry_request(req)
        self.assertEqual((req.lot_size, req.strike), (500, 1400))
        self.assertIsInstance(req.strike, int)

    def test_a_carried_live_stock_option_is_refused_near_its_expiry(self):
        from fastapi import HTTPException

        with self.assertRaisesRegex(HTTPException, "") as caught:
            self.app._validate_scalp_entry_request(
                self._req(
                    underlying="RELIANCE",
                    strike=1400,
                    option_type="PE",
                    expiry=NEAR,
                    mode="live",
                    product_type="MARGIN",
                )
            )
        self.assertIn("delivery", caught.exception.detail)
        # Intraday, or paper, or a later expiry, it goes ahead.
        for kw in (
            dict(expiry=NEAR, option_type="PE", mode="live", product_type="INTRADAY"),
            dict(expiry=NEAR, option_type="PE", mode="paper", product_type="MARGIN"),
            dict(expiry=FAR, mode="live", product_type="MARGIN"),
        ):
            self.app._validate_scalp_entry_request(self._req(underlying="RELIANCE", strike=1400, **kw))

    def test_an_unknown_stock_or_expiry_is_refused(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException):
            self.app._validate_scalp_entry_request(self._req(underlying="NOTASTOCK"))
        with self.assertRaises(HTTPException):
            self.app._validate_scalp_entry_request(
                self._req(underlying="RELIANCE", expiry=(TODAY + timedelta(days=60)).isoformat())
            )

    def test_the_picker_list_and_a_stocks_terms(self):
        listed = asyncio.run(self.app.scalp_underlyings())
        self.assertEqual(
            listed["stocks"],
            [
                {"symbol": "BAJAJ-AUTO", "lot": 75},
                {"symbol": "CROMPTON", "lot": 1800},
                {"symbol": "RELIANCE", "lot": 500},
            ],
        )
        spec = asyncio.run(self.app.scalp_contract_spec(underlying="reliance", expiry=FAR))
        self.assertEqual((spec["lot"], spec["step"]), (500, 20.0))


if __name__ == "__main__":
    unittest.main()
