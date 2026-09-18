"""Zerodha as a second broker for the live CE/PE books.

Phil, 2026-09-18: "I need to add zerodha to my website" -- chosen as a full
second broker. The live engine names contracts by Dhan's security id and reads
Dhan's words, so `broker/zerodha.py` answers the engine's eleven calls in those
words. These tests hold it to that, against a fake Kite, and pin the two rules
that keep today's Dhan books exactly where they are.
"""

import asyncio
import os
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402
from broker import zerodha  # noqa: E402
from broker.dhan import AmbiguousOrderSubmission, DhanOrderError, ScripMaster  # noqa: E402
from engine.live import LiveEngine  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
SRC = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")

CONTRACT = "NIFTY_23600_2026-09-22_CE"
DHAN_ID = "54880"
SYMBOL = "NIFTY2592223600CE"


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class FakeKite:
    """Answers by (method, path); records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, headers=None, params=None, data=None, timeout=None):
        path = url.replace(zerodha.KITE_API, "")
        self.calls.append({"method": method, "path": path, "params": params, "data": data, "headers": headers})
        answer = self.routes.get((method, path))
        if answer is None:
            return FakeResponse(404, {"status": "error", "message": "no route"})
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(*answer)


def _client(routes):
    fake = FakeKite(routes)
    client = zerodha.ZerodhaClient("key123", "tok456", token_saved_at="2026-09-18T08:00:00+05:30", session=fake)
    return client, fake


class _WithContracts(unittest.TestCase):
    def setUp(self):
        zerodha.KiteInstruments.load_rows(
            [
                {
                    "instrument_token": "12345",
                    "tradingsymbol": SYMBOL,
                    "name": '"NIFTY"',
                    "expiry": "2026-09-22",
                    "strike": "23600.0",
                    "tick_size": "0.05",
                    "lot_size": "65",
                    "instrument_type": "CE",
                    "exchange": "NFO",
                },
                {"instrument_type": "FUT", "name": "NIFTY", "expiry": "2026-09-29", "strike": "0"},
            ],
            loaded_on=datetime.now(IST).date(),
        )
        self._saved = (ScripMaster._options_cache, ScripMaster._loaded_date)
        ScripMaster._options_cache = {CONTRACT: DHAN_ID}
        ScripMaster._loaded_date = datetime.now().strftime("%Y-%m-%d")

    def tearDown(self):
        ScripMaster._options_cache, ScripMaster._loaded_date = self._saved


class TheDailyLogin(unittest.TestCase):
    def test_a_token_from_after_six_this_morning_is_current(self):
        now = datetime(2026, 9, 18, 9, 10, tzinfo=IST)
        self.assertTrue(zerodha.token_is_current("2026-09-18T08:30:00+05:30", now))

    def test_yesterdays_token_is_dead_after_six(self):
        now = datetime(2026, 9, 18, 9, 10, tzinfo=IST)
        self.assertFalse(zerodha.token_is_current("2026-09-17T09:00:00+05:30", now))

    def test_yesterdays_token_still_lives_before_six(self):
        now = datetime(2026, 9, 18, 2, 0, tzinfo=IST)
        self.assertTrue(zerodha.token_is_current("2026-09-17T09:00:00+05:30", now))

    def test_no_token_is_not_current(self):
        self.assertFalse(zerodha.token_is_current(""))
        self.assertFalse(zerodha.token_is_current("garbage"))

    def test_the_checksum_is_sha256_of_key_token_secret(self):
        import hashlib

        self.assertEqual(zerodha.session_checksum("k", "r", "s"), hashlib.sha256(b"krs").hexdigest())

    def test_the_login_url(self):
        self.assertEqual(zerodha.login_url("abc"), "https://kite.zerodha.com/connect/login?v=3&api_key=abc")


class PlacingAnOrder(_WithContracts):
    def test_a_market_buy_goes_to_kite_in_kites_words(self):
        client, fake = _client(
            {("POST", "/orders/regular"): (200, {"status": "success", "data": {"order_id": "9001"}})}
        )
        result = client.place_option_order("NIFTY", 23600, "CE", "2026-09-22", "BUY", 130, tag="AF_CE_23600")
        self.assertEqual(result["orderId"], "9001")
        form = fake.calls[0]["data"]
        self.assertEqual(form["tradingsymbol"], SYMBOL)
        self.assertEqual(form["exchange"], "NFO")
        self.assertEqual(form["product"], "MIS")
        self.assertEqual(form["order_type"], "MARKET")
        self.assertEqual(form["market_protection"], -1)
        self.assertEqual(form["quantity"], 130)
        self.assertEqual(form["tag"], "AFCE23600")
        self.assertNotIn("price", form)

    def test_margin_is_nrml(self):
        client, fake = _client({("POST", "/orders/regular"): (200, {"status": "success", "data": {"order_id": "1"}})})
        client.place_option_order("NIFTY", 23600, "CE", "2026-09-22", "SELL", 65, product_type="MARGIN")
        self.assertEqual(fake.calls[0]["data"]["product"], "NRML")

    def test_the_stop_carries_trigger_and_limit_on_the_tick(self):
        client, fake = _client({("POST", "/orders/regular"): (200, {"status": "success", "data": {"order_id": "2"}})})
        client.place_sl_order("NIFTY", 23600, "CE", "2026-09-22", "SELL", 130, trigger_price=250.03, price=247.52)
        form = fake.calls[0]["data"]
        self.assertEqual(form["order_type"], "SL")
        self.assertAlmostEqual(form["trigger_price"], 250.05)
        self.assertAlmostEqual(form["price"], 247.5)

    def test_a_refusal_is_an_order_error_with_kites_reason(self):
        client, _ = _client(
            {
                ("POST", "/orders/regular"): (
                    400,
                    {"status": "error", "message": "Insufficient funds", "error_type": "MarginException"},
                )
            }
        )
        with self.assertRaises(DhanOrderError) as caught:
            client.place_option_order("NIFTY", 23600, "CE", "2026-09-22", "BUY", 130)
        self.assertIn("Insufficient funds", caught.exception.reason)

    def test_a_429_is_ambiguous_never_a_clean_refusal(self):
        client, _ = _client({("POST", "/orders/regular"): (429, {"status": "error", "message": "Too many requests"})})
        with self.assertRaises(AmbiguousOrderSubmission):
            client.place_option_order("NIFTY", 23600, "CE", "2026-09-22", "BUY", 130)

    def test_a_dropped_connection_is_ambiguous(self):
        client, _ = _client({("POST", "/orders/regular"): TimeoutError("read timed out")})
        with self.assertRaises(AmbiguousOrderSubmission):
            client.place_option_order("NIFTY", 23600, "CE", "2026-09-22", "BUY", 130)

    def test_an_unknown_contract_is_refused_before_kite_is_asked(self):
        client, fake = _client({})
        with self.assertRaises(RuntimeError):
            client.place_option_order("NIFTY", 99999, "CE", "2026-09-22", "BUY", 130)
        self.assertEqual(fake.calls, [])

    def test_the_engine_calls_it_with_keywords_through_async(self):
        client, _ = _client({("POST", "/orders/regular"): (200, {"status": "success", "data": {"order_id": "7"}})})
        result = asyncio.run(
            client.async_place_option_order(
                underlying="NIFTY",
                strike_price=23600,
                option_type="CE",
                expiry="2026-09-22",
                transaction_type="BUY",
                quantity=130,
                order_type="MARKET",
                product_type="INTRADAY",
                tag="PhilForge",
            )
        )
        self.assertEqual(result["orderId"], "7")


class ReadingTheOrderBack(_WithContracts):
    def _history(self, status, filled=130):
        row = {
            "order_id": "9001",
            "status": status,
            "quantity": 130,
            "filled_quantity": filled,
            "average_price": 252.6,
            "status_message": "RMS: margin exceeds" if status == "REJECTED" else None,
        }
        return {("GET", "/orders/9001"): (200, {"status": "success", "data": [{"status": "OPEN PENDING"}, row]})}

    def test_complete_reads_as_traded(self):
        client, _ = _client(self._history("COMPLETE"))
        self.assertEqual(client.get_order_status("9001")["orderStatus"], "TRADED")

    def test_the_fill_is_confirmed_with_its_price(self):
        client, _ = _client(self._history("COMPLETE"))
        fill = client.verify_order_fill("9001", max_wait_sec=2)
        self.assertEqual((fill["status"], fill["filled_qty"], fill["avg_price"]), ("FILLED", 130, 252.6))

    def test_a_rejection_carries_its_reason(self):
        client, _ = _client(self._history("REJECTED", filled=0))
        fill = client.verify_order_fill("9001", max_wait_sec=2)
        self.assertEqual(fill["status"], "REJECTED")
        self.assertIn("margin", fill["message"])

    def test_a_resting_stop_is_pending(self):
        client, _ = _client(self._history("TRIGGER PENDING", filled=0))
        self.assertEqual(client.get_order_status("9001")["orderStatus"], "PENDING")

    def test_a_cancelled_stop_is_one_the_engine_recognises(self):
        client, _ = _client(self._history("CANCELLED", filled=0))
        self.assertEqual(client.get_order_status("9001")["orderStatus"], "CANCELLED")


class TheAccountInDhansWords(_WithContracts):
    def test_a_position_is_keyed_by_dhans_security_id(self):
        positions = {
            "net": [
                {
                    "tradingsymbol": SYMBOL,
                    "exchange": "NFO",
                    "quantity": 130,
                    "buy_price": 252.6,
                    "sell_price": 0,
                    "product": "MIS",
                }
            ]
        }
        client, _ = _client({("GET", "/portfolio/positions"): (200, {"status": "success", "data": positions})})
        row = client.get_positions()[0]
        self.assertEqual(row["securityId"], DHAN_ID)
        self.assertEqual(LiveEngine._broker_position_quantity(row), 130)

    def test_a_fill_in_the_trade_book_is_keyed_the_same_way(self):
        trades = [
            {
                "tradingsymbol": SYMBOL,
                "exchange": "NFO",
                "transaction_type": "SELL",
                "average_price": 280.0,
                "quantity": 130,
                "fill_timestamp": "2026-09-18 10:05:00",
                "order_id": "9002",
            }
        ]
        client, _ = _client({("GET", "/trades"): (200, {"status": "success", "data": trades})})
        fill = client.get_trades()[0]
        self.assertEqual((fill["securityId"], fill["transactionType"], fill["tradedPrice"]), (DHAN_ID, "SELL", 280.0))

    def test_the_engine_reads_the_available_margin(self):
        margins = {"net": 212345.5, "available": {"opening_balance": 250000}, "utilised": {"debits": 37654.5}}
        client, _ = _client({("GET", "/user/margins/equity"): (200, {"status": "success", "data": margins})})
        self.assertEqual(LiveEngine._extract_available_balance(client.get_funds()), 212345.5)

    def test_prices_come_back_by_dhan_id(self):
        quote = {f"NFO:{SYMBOL}": {"instrument_token": 12345, "last_price": 251.35}}
        client, fake = _client({("GET", "/quote/ltp"): (200, {"status": "success", "data": quote})})
        self.assertEqual(client.get_ltp_prices([int(DHAN_ID), 11111]), {int(DHAN_ID): 251.35})
        self.assertEqual(fake.calls[0]["params"], [("i", f"NFO:{SYMBOL}")])

    def test_one_option_price(self):
        quote = {f"NFO:{SYMBOL}": {"last_price": 251.35}}
        client, _ = _client({("GET", "/quote/ltp"): (200, {"status": "success", "data": quote})})
        self.assertEqual(asyncio.run(client.async_get_option_ltp("NIFTY", 23600, "2026-09-22", "CE")), 251.35)

    def test_an_expired_session_says_log_in_again(self):
        client, _ = _client(
            {
                ("GET", "/user/margins/equity"): (
                    403,
                    {"status": "error", "error_type": "TokenException", "message": "x"},
                )
            }
        )
        with self.assertRaises(ConnectionError) as caught:
            client.get_funds()
        self.assertIn("Log in to Zerodha again", str(caught.exception))


class IndexCandles(unittest.TestCase):
    def test_nifty_candles_come_back_in_dhans_frame(self):
        candles = [
            ["2026-09-18T09:15:00+0530", 25000, 25010, 24990, 25005, 0],
            ["2026-09-18T09:20:00+0530", 1, 2, 0.5, 1.5, 0],
        ]
        client, fake = _client(
            {
                ("GET", "/instruments/historical/256265/5minute"): (
                    200,
                    {"status": "success", "data": {"candles": candles}},
                )
            }
        )
        frame = client.get_historical_data(
            "13", "IDX_I", "INDEX", from_date="2026-09-11", to_date="2026-09-18", candle_type="5"
        )
        self.assertEqual(str(frame.index[0]), "2026-09-18 09:15:00")
        self.assertIsNone(frame.index.tz)
        self.assertEqual(list(frame.columns), ["open", "high", "low", "close", "volume"])
        self.assertEqual(fake.calls[0]["params"]["from"], "2026-09-11 09:00:00")

    def test_a_stock_is_refused_plainly(self):
        client, _ = _client({})
        with self.assertRaises(RuntimeError):
            client.get_historical_data("2885", "NSE_EQ", "EQUITY", from_date="2026-09-11", to_date="2026-09-18")


class EveryBookSavedBeforeZerodhaStaysOnDhan(unittest.TestCase):
    def test_no_broker_key_is_dhan(self):
        self.assertEqual(app._book_broker_name({}), "dhan")
        self.assertEqual(app._book_broker_name(None), "dhan")
        self.assertEqual(app._book_broker_name({"broker": "Zerodha"}), "zerodha")
        self.assertEqual(app._book_broker_name({"broker": "anything"}), "dhan")

    def test_a_dhan_book_resolves_exactly_as_before(self):
        user = {"id": 1, "role": "user", "dhan_client_id": "110", "dhan_access_token": "abc"}
        client, source = app._resolve_book_broker_client(user, {"product_type": "MIS"})
        self.assertEqual(source, "user")
        self.assertNotIsInstance(client, zerodha.ZerodhaClient)

    def test_both_live_paths_choose_the_broker_per_book(self):
        start = SRC.split("async def live_start(")[1].split("\nasync def ")[0]
        self.assertIn("_resolve_book_broker_client(user, requested_deploy", start)
        restore = SRC.split("async def _restore_live_engines(")[1].split("\nasync def ")[0]
        self.assertIn("_resolve_book_broker_client(", restore)
        self.assertIn("require_today=False", restore)


def _zerodha_user(token_at):
    return {
        "id": 7,
        "role": "user",
        "zerodha_api_key": "key123",
        "zerodha_api_secret": "sec",
        "zerodha_access_token": "tok",
        "zerodha_user_id": "AB1234",
        "zerodha_token_at": token_at,
    }


class AZerodhaBook(unittest.TestCase):
    def test_a_new_book_needs_todays_login(self):
        client, source = app._resolve_book_broker_client(
            _zerodha_user("2026-01-01T09:00:00+05:30"), {"broker": "zerodha"}
        )
        self.assertIsNone(client)
        self.assertEqual(source, "zerodha_login_needed")
        self.assertIn("Log in to Zerodha", app._book_broker_missing_message(None, source))

    def test_a_restored_book_comes_back_on_the_old_token(self):
        """Dropping it after a 07:00 deploy would be the overnight bug again."""
        client, source = app._resolve_book_broker_client(
            _zerodha_user("2026-01-01T09:00:00+05:30"), {"broker": "zerodha"}, require_today=False
        )
        self.assertIsInstance(client, zerodha.ZerodhaClient)

    def test_todays_login_is_enough(self):
        now = datetime.now(IST).isoformat(timespec="seconds")
        client, source = app._resolve_book_broker_client(_zerodha_user(now), {"broker": "zerodha"})
        if zerodha.token_is_current(now):
            self.assertEqual(source, "zerodha")
            self.assertIsInstance(client, zerodha.ZerodhaClient)

    def test_no_app_saved(self):
        self.assertEqual(app._resolve_book_broker_client({"id": 1}, {"broker": "zerodha"}), (None, "zerodha_missing"))

    def test_the_morning_login_reaches_a_running_book(self):
        stale = zerodha.ZerodhaClient("key123", "old", token_saved_at="2026-09-17T09:00:00+05:30")
        engine = LiveEngine(stale, run_id="Z_TEST", state_dir="/tmp")
        bucket = app._registry_bucket(app.live_engines, 99901)
        bucket["Z_TEST"] = engine
        try:
            handed = app._hand_zerodha_token_to_running_books(99901, "new", "2026-09-18T08:40:00+05:30")
        finally:
            bucket.pop("Z_TEST", None)
        self.assertEqual(handed, 1)
        self.assertEqual(stale.access_token, "new")

    def test_the_profile_never_shows_the_secret_or_token(self):
        payload = app._zerodha_profile_payload(_zerodha_user("2026-09-18T08:00:00+05:30"))
        text = str(payload)
        self.assertNotIn("sec", text.replace("secret_saved", ""))
        self.assertNotIn("'tok'", text)


class TheCredentialsAreGuarded(unittest.TestCase):
    def test_saving_or_clearing_needs_the_broker_credentials_gate(self):
        auth_src = (Path(__file__).resolve().parent.parent / "auth.py").read_text(encoding="utf-8")
        self.assertIn('("PUT", re.compile(r"^/api/user/zerodha$"), "broker_credentials")', auth_src)
        self.assertIn('("DELETE", re.compile(r"^/api/user/zerodha$"), "broker_credentials")', auth_src)

    def test_key_secret_and_token_are_encrypted_at_rest(self):
        import db

        for field in ("zerodha_api_key", "zerodha_api_secret", "zerodha_access_token"):
            self.assertIn(field, db._SENSITIVE_USER_FIELDS)

    def test_the_callback_never_prints_the_request_token(self):
        body = SRC.split("async def zerodha_callback(")[1].split("\n@app.")[0]
        self.assertNotIn("{request_token", body)
        self.assertNotIn("{exc}", body)


if __name__ == "__main__":
    unittest.main()


class TheMorningReminder(unittest.TestCase):
    def test_it_rings_between_0830_and_0912_on_a_weekday(self):
        self.assertTrue(app._zerodha_reminder_due(datetime(2026, 9, 21, 8, 45, tzinfo=IST)))
        self.assertFalse(app._zerodha_reminder_due(datetime(2026, 9, 21, 8, 0, tzinfo=IST)))
        self.assertFalse(app._zerodha_reminder_due(datetime(2026, 9, 21, 9, 30, tzinfo=IST)))
        self.assertFalse(app._zerodha_reminder_due(datetime(2026, 9, 20, 8, 45, tzinfo=IST)))  # Sunday

    def test_it_names_only_running_zerodha_books_with_no_login(self):
        now = datetime(2026, 9, 21, 8, 45, tzinfo=IST)
        stale = LiveEngine(zerodha.ZerodhaClient("k", "t", token_saved_at="2026-09-18T08:00:00+05:30"), run_id="Z_OLD")
        fresh = LiveEngine(zerodha.ZerodhaClient("k", "t", token_saved_at="2026-09-21T08:40:00+05:30"), run_id="Z_NEW")
        dhan_book = LiveEngine(object(), run_id="D_BOOK")
        for engine in (stale, fresh, dhan_book):
            engine.running = True
        bucket = app._registry_bucket(app.live_engines, 99902)
        bucket.update({"Z_OLD": stale, "Z_NEW": fresh, "D_BOOK": dhan_book})
        try:
            self.assertEqual(app._zerodha_books_waiting_for_login(now), ["Z_OLD"])
        finally:
            for key in ("Z_OLD", "Z_NEW", "D_BOOK"):
                bucket.pop(key, None)


class OneSavedStrategyIsOneLiveBook(unittest.TestCase):
    """Deploying #48 again under another name would rename #48 and move it to
    the other broker; two books from one strategy means a copy."""

    USER = 99903

    def _book(self, run, sid, running=True):
        engine = LiveEngine(object(), run_id=run, state_dir="/tmp")
        engine.strategy = {"strategy_id": sid, "run_name": run}
        engine.running = running
        app._registry_bucket(app.live_engines, self.USER)[run] = engine

    def tearDown(self):
        app._registry_bucket(app.live_engines, self.USER).clear()

    def test_a_second_name_for_the_same_strategy_is_refused(self):
        self._book("CE_SL15_NoMonTue", 48)
        twin = app._live_book_running_same_strategy(self.USER, 48, "CE_SL15_NoMonTue_Z")
        self.assertEqual(twin, "CE_SL15_NoMonTue")
        self.assertIn("make a copy", app._same_strategy_refusal(48, twin))

    def test_redeploying_the_same_book_is_allowed(self):
        self._book("CE_SL15_NoMonTue", 48)
        self.assertEqual(app._live_book_running_same_strategy(self.USER, 48, "CE_SL15_NoMonTue"), "")

    def test_a_copy_is_allowed(self):
        self._book("CE_SL15_NoMonTue", 48)
        self.assertEqual(app._live_book_running_same_strategy(self.USER, 51, "CE_SL15_NoMonTue_Z"), "")

    def test_a_stopped_book_does_not_block(self):
        self._book("CE_SL15_NoMonTue", 48, running=False)
        self.assertEqual(app._live_book_running_same_strategy(self.USER, 48, "CE_SL15_NoMonTue_Z"), "")

    def test_an_unsaved_strategy_is_not_checked(self):
        self._book("Adhoc", 0)
        self.assertEqual(app._live_book_running_same_strategy(self.USER, 0, "Adhoc2"), "")

    def test_the_check_runs_before_the_saved_strategy_is_rewritten(self):
        start = SRC.split("async def live_start(")[1].split("\nasync def ")[0]
        self.assertLess(
            start.index("_live_book_running_same_strategy("),
            start.index("await _sync_saved_strategy_from_runtime("),
        )
