"""The Option Builder's server side: the chain it serves, how often it asks
Dhan, the margin it reads, the positions it imports, and above all the order
in which a LIVE basket reaches the broker.

The basket rule is the one with money on it: BUY legs go first, and a failed
BUY holds every SELL back -- a short leg without its hedge is the outcome a
spread exists to prevent.
"""

import asyncio
import base64
import json
import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

os.environ.setdefault("PHILFORGE_PIN", "123456")
os.environ.setdefault("PHILFORGE_DB", "/tmp/philforge-option-builder.db")
os.environ.setdefault("PHILFORGE_USER_DATA_ROOT", "/tmp/philforge-option-builder-data")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())

import app as app_module  # noqa: E402
import option_builder as ob  # noqa: E402

IST = ob.IST


def _raw_chain():
    side = lambda ltp, iv, oi, prev, sid: {  # noqa: E731
        "last_price": ltp,
        "implied_volatility": iv,
        "oi": oi,
        "previous_oi": prev,
        "security_id": sid,
        "top_bid_price": ltp - 0.5,
        "top_ask_price": ltp + 0.5,
        "volume": 1000,
        "greeks": {"delta": 0.5, "gamma": 0.001, "theta": -10.0, "vega": 12.0},
    }
    return {
        "last_price": 25012.4,
        "oc": {
            "24900.000000": {"ce": side(190, 12.1, 500_000, 400_000, 1), "pe": side(80, 12.9, 2_000_000, 1_900_000, 2)},
            "25000.000000": {
                "ce": side(130, 11.6, 3_000_000, 2_500_000, 3),
                "pe": side(118, 11.4, 2_500_000, 2_600_000, 4),
            },
            "25100.000000": {"ce": side(85, 11.2, 2_200_000, 2_000_000, 5), "pe": side(170, 11.9, 400_000, 300_000, 6)},
            "junk": {"ce": side(1, 1, 1, 1, 9)},
            "25200.000000": {},
        },
    }


class TheChainIsShapedForThePage(unittest.TestCase):
    def setUp(self):
        self.chain = ob.shape_chain(_raw_chain(), "NIFTY", "2026-10-13", 75)

    def test_rows_are_sorted_strikes_and_junk_is_dropped(self):
        self.assertEqual([r["strike"] for r in self.chain["rows"]], [24900.0, 25000.0, 25100.0])

    def test_atm_spot_step_and_lot(self):
        self.assertEqual(self.chain["atm"], 25000.0)
        self.assertEqual(self.chain["spot"], 25012.4)
        self.assertEqual(self.chain["strike_step"], 100.0)
        self.assertEqual(self.chain["lot_size"], 75)
        self.assertEqual(self.chain["segment"], "NSE_FNO")

    def test_atm_iv_is_the_mean_of_the_atm_pair(self):
        self.assertAlmostEqual(self.chain["atm_iv"], 11.5)

    def test_oi_change_and_pcr(self):
        ce = self.chain["rows"][1]["ce"]
        self.assertEqual(ce["oi_change"], 500_000)
        self.assertEqual(self.chain["rows"][1]["pe"]["oi_change"], -100_000)
        self.assertAlmostEqual(self.chain["pcr"], round(4_900_000 / 5_700_000, 2))

    def test_max_pain_is_where_writers_pay_least(self):
        # writers' payout: 700 settling at 100, 200 at 110, 700 at 120
        rows = [
            {"strike": 100.0, "ce": {"oi": 10}, "pe": {"oi": 0}},
            {"strike": 110.0, "ce": {"oi": 50}, "pe": {"oi": 50}},
            {"strike": 120.0, "ce": {"oi": 0}, "pe": {"oi": 10}},
        ]
        self.assertEqual(ob.max_pain(rows), 110.0)

    def test_sensex_trades_on_bse(self):
        self.assertEqual(ob.shape_chain(_raw_chain(), "SENSEX", "2026-10-08")["segment"], "BSE_FNO")


class DhanIsAskedSparingly(unittest.TestCase):
    """One unique chain request per 3s for the whole account."""

    def setUp(self):
        self.now = [1000.0]
        self.slept = []

        def sleep(sec):
            self.slept.append(sec)
            self.now[0] += sec

        self.cache = ob.ChainCache(clock=lambda: self.now[0], sleep=sleep, session_open=lambda: True)

        class Client:
            calls = []

            def get_option_chain(self, scrip, seg, expiry):
                self.calls.append((scrip, seg, expiry))
                return _raw_chain()

        self.client = Client()

    def test_a_repeat_inside_the_ttl_costs_nothing(self):
        self.cache.get(self.client, "NIFTY", "2026-10-13", 75)
        self.now[0] += 1.0
        self.cache.get(self.client, "NIFTY", "2026-10-13", 75)
        self.assertEqual(len(self.client.calls), 1)

    def test_a_second_chain_waits_out_the_gap(self):
        self.cache.get(self.client, "NIFTY", "2026-10-13", 75)
        self.cache.get(self.client, "NIFTY", "2026-10-20", 75)
        self.assertEqual(len(self.client.calls), 2)
        self.assertTrue(self.slept and self.slept[0] >= 3.0)

    def test_the_index_ids_are_dhans(self):
        self.cache.get(self.client, "BANKNIFTY", "2026-10-28", 30)
        self.assertEqual(self.client.calls[-1][:2], (25, "IDX_I"))

    def test_a_failed_refresh_serves_the_last_chain_marked_stale(self):
        self.cache.get(self.client, "NIFTY", "2026-10-13", 75)
        self.now[0] += 10
        self.client.get_option_chain = lambda *a: (_ for _ in ()).throw(RuntimeError("429"))
        chain = self.cache.get(self.client, "NIFTY", "2026-10-13", 75)
        self.assertTrue(chain["stale"])

    def test_an_unknown_index_is_refused_in_words(self):
        with self.assertRaises(ob.OptionBuilderError):
            self.cache.get(self.client, "NASDAQ", "2026-10-13")

    def test_off_session_the_chain_is_kept_a_minute(self):
        self.cache._session_open = lambda: False
        self.cache.get(self.client, "NIFTY", "2026-10-13", 75)
        self.now[0] += 30
        self.cache.get(self.client, "NIFTY", "2026-10-13", 75)
        self.assertEqual(len(self.client.calls), 1)


class AnExpiryEndsAtItsClose(unittest.TestCase):
    def test_todays_expiry_is_offered_until_1530_ist(self):
        exps = ["2026-10-06", "2026-10-13"]
        morning = datetime(2026, 10, 6, 15, 29, tzinfo=IST)
        evening = datetime(2026, 10, 6, 15, 30, tzinfo=IST)
        self.assertEqual(ob.open_expiries(exps, morning), exps)
        self.assertEqual(ob.open_expiries(exps, evening), ["2026-10-13"])

    def test_a_past_expiry_is_never_offered(self):
        self.assertEqual(ob.open_expiries(["2026-10-01"], datetime(2026, 10, 6, 9, 0, tzinfo=IST)), [])


class LegsAndOrders(unittest.TestCase):
    def leg(self, **over):
        base = {
            "underlying": "nifty",
            "strike": 25000,
            "expiry": "2026-10-13",
            "option_type": "ce",
            "side": "buy",
            "lots": 1,
        }
        base.update(over)
        return base

    def test_a_leg_is_normalised(self):
        leg = ob.normalize_leg(self.leg())
        self.assertEqual((leg["underlying"], leg["option_type"], leg["side"]), ("NIFTY", "CE", "BUY"))

    def test_bad_legs_are_refused(self):
        for bad in (
            {"side": "hold"},
            {"option_type": "XX"},
            {"lots": 0},
            {"lots": 51},
            {"strike": -1},
            {"expiry": "soon"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ob.OptionBuilderError):
                ob.normalize_leg(self.leg(**bad))

    def test_mixed_indices_are_refused(self):
        with self.assertRaises(ob.OptionBuilderError):
            ob.normalize_legs([self.leg(), self.leg(underlying="BANKNIFTY")])

    def test_buys_go_first(self):
        legs = ob.normalize_legs(
            [
                self.leg(side="SELL", strike=25100),
                self.leg(side="BUY", strike=25300),
                self.leg(side="SELL", strike=24900),
            ]
        )
        self.assertEqual([leg["side"] for leg in ob.basket_order(legs)], ["BUY", "SELL", "SELL"])

    def test_entry_prices_go_through_the_touch(self):
        buy = ob.normalize_leg(self.leg())
        sell = ob.normalize_leg(self.leg(side="SELL"))
        self.assertEqual(ob.entry_price(buy, {"ltp": 100, "bid": 99.5, "ask": 100.5}), 102.5)  # ask +2%
        self.assertEqual(ob.entry_price(sell, {"ltp": 100, "bid": 99.5, "ask": 100.5}), 97.5)  # bid -2%
        self.assertEqual(ob.entry_price(buy, {"ltp": 100}), 105.0)  # no quote: ltp +5%
        self.assertEqual(ob.entry_price(sell, {"ltp": 0.06}), 0.05)  # never below a tick

    def test_margin_list_carries_quantity_product_and_segment(self):
        legs = ob.normalize_legs([self.leg(lots=2, security_id="42528", price=130)])
        row = ob.margin_scrip_list(legs, 75, "NRML")[0]
        self.assertEqual(
            (row["quantity"], row["productType"], row["exchangeSegment"], row["securityId"]),
            (150, "MARGIN", "NSE_FNO", "42528"),
        )

    def test_margin_answers_in_either_spelling(self):
        self.assertEqual(ob.shape_margin({"total_margin": "1500.5", "hedge_benefit": ""})["total"], 1500.5)
        self.assertEqual(ob.shape_margin({"totalMargin": 900})["total"], 900.0)


class PositionsBecomeLegs(unittest.TestCase):
    def test_dhan_option_positions(self):
        rows = [
            {
                "exchangeSegment": "NSE_FNO",
                "tradingSymbol": "NIFTY-Oct2026-25100-CE",
                "drvOptionType": "CALL",
                "drvStrikePrice": 25100,
                "drvExpiryDate": "2026-10-13 14:30:00.0",
                "netQty": -150,
                "sellAvg": 142.5,
                "buyAvg": 0,
                "securityId": "42529",
                "productType": "INTRADAY",
            },
            {"exchangeSegment": "NSE_EQ", "tradingSymbol": "RELIANCE", "netQty": 5},
            {
                "exchangeSegment": "NSE_FNO",
                "tradingSymbol": "NIFTY-Oct2026-25000-PE",
                "drvOptionType": "PUT",
                "netQty": 0,
            },
        ]
        legs = ob.legs_from_dhan_positions(rows)
        self.assertEqual(len(legs), 1)
        self.assertEqual(
            (legs[0]["side"], legs[0]["option_type"], legs[0]["quantity"], legs[0]["price"], legs[0]["expiry"]),
            ("SELL", "CE", 150, 142.5, "2026-10-13"),
        )

    def test_zerodha_positions_through_the_kite_symbol(self):
        rows = [{"exchange": "NFO", "tradingSymbol": "NIFTY26O1325000PE", "netQty": 75, "buyAvg": 101.0}]
        legs = ob.legs_from_zerodha_positions(rows, lambda ex, sym: "NIFTY_25000_2026-10-13_PE")
        self.assertEqual((legs[0]["side"], legs[0]["strike"], legs[0]["broker"]), ("BUY", 25000.0, "zerodha"))


class PaperBaskets(unittest.TestCase):
    def legs(self):
        return ob.normalize_legs(
            [
                {
                    "underlying": "NIFTY",
                    "strike": 25000,
                    "expiry": "2026-10-13",
                    "option_type": "CE",
                    "side": "BUY",
                    "lots": 1,
                    "price": 130,
                },
                {
                    "underlying": "NIFTY",
                    "strike": 25200,
                    "expiry": "2026-10-13",
                    "option_type": "CE",
                    "side": "SELL",
                    "lots": 1,
                    "price": 60,
                },
            ]
        )

    def test_a_basket_opens_at_its_prices_and_closes_at_the_ltps(self):
        now = datetime(2026, 10, 6, 10, 0, tzinfo=IST)
        basket = ob.new_paper_basket(1, "Bull call", self.legs(), 75, now)
        closed = ob.close_paper_basket(
            basket, {(25000.0, "CE", "2026-10-13"): 170.0, (25200.0, "CE", "2026-10-13"): 80.0}, now
        )
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(closed["realised"], (170 - 130) * 75 - (80 - 60) * 75)

    def test_no_price_no_close(self):
        basket = ob.new_paper_basket(1, "x", self.legs(), 75, datetime.now(IST))
        with self.assertRaises(ob.OptionBuilderError):
            ob.close_paper_basket(basket, {(25000.0, "CE", "2026-10-13"): 170.0}, datetime.now(IST))

    def test_an_unpriced_leg_cannot_open(self):
        legs = self.legs()
        legs[0]["price"] = 0
        with self.assertRaises(ob.OptionBuilderError):
            ob.new_paper_basket(1, "x", legs, 75, datetime.now(IST))


class Broker:
    def __init__(self, fail_on=()):
        self.sent = []
        self.fail_on = set(fail_on)

    def place_option_order(self, **kw):
        key = (kw["transaction_type"], kw["strike_price"], kw["option_type"])
        self.sent.append(key)
        if key in self.fail_on:
            raise RuntimeError("RMS: insufficient funds")
        return {"orderId": f"O{len(self.sent)}", "orderStatus": "TRANSIT"}


class TheLiveBasket(unittest.TestCase):
    """POST /api/option-builder/execute, with the broker faked."""

    def run_basket(self, legs, broker):
        chain = ob.shape_chain(_raw_chain(), "NIFTY", "2026-10-13", 75)
        req = app_module.OptionBuilderBasketReq(legs=legs, product="INTRADAY", broker="dhan", name="t")
        with (
            patch.object(app_module, "_request_broker_context", AsyncMock(return_value=({"id": 1}, broker, "user"))),
            patch.object(app_module, "_ob_lot_size", lambda u, e: 75),
            patch.object(app_module, "_ob_chain", AsyncMock(return_value=chain)),
            patch.object(app_module.alerter, "alert") as alert,
        ):
            result = asyncio.run(app_module.option_builder_execute(req, request=None))
        return result, alert

    def legs(self):
        return [
            {
                "underlying": "NIFTY",
                "strike": 25000,
                "expiry": "2026-10-13",
                "option_type": "PE",
                "side": "SELL",
                "lots": 1,
            },
            {
                "underlying": "NIFTY",
                "strike": 24900,
                "expiry": "2026-10-13",
                "option_type": "PE",
                "side": "BUY",
                "lots": 1,
            },
        ]

    def test_the_buy_reaches_the_broker_before_the_sell(self):
        broker = Broker()
        result, alert = self.run_basket(self.legs(), broker)
        self.assertEqual(broker.sent, [("BUY", 24900, "PE"), ("SELL", 25000, "PE")])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sent"], 2)
        alert.assert_called_once()

    def test_a_failed_buy_holds_every_sell_back(self):
        broker = Broker(fail_on={("BUY", 24900, "PE")})
        result, alert = self.run_basket(self.legs(), broker)
        self.assertEqual(broker.sent, [("BUY", 24900, "PE")])
        self.assertEqual(result["status"], "partial")
        statuses = {(r["side"], r["status"]) for r in result["results"]}
        self.assertEqual(statuses, {("BUY", "failed"), ("SELL", "not_sent")})
        self.assertEqual(alert.call_args.kwargs.get("level"), "error")

    def test_a_failed_sell_does_not_stop_the_other_sells(self):
        legs = self.legs() + [
            {
                "underlying": "NIFTY",
                "strike": 25100,
                "expiry": "2026-10-13",
                "option_type": "CE",
                "side": "SELL",
                "lots": 1,
            }
        ]
        broker = Broker(fail_on={("SELL", 25000, "PE")})
        result, _ = self.run_basket(legs, broker)
        self.assertEqual(len(broker.sent), 3)
        self.assertEqual(result["sent"], 2)

    def test_orders_are_limits_through_the_touch(self):
        captured = []

        class Spy(Broker):
            def place_option_order(self, **kw):
                captured.append(kw)
                return super().place_option_order(**kw)

        self.run_basket(self.legs()[1:], Spy())
        self.assertEqual(captured[0]["order_type"], "LIMIT")
        self.assertEqual(captured[0]["price"], ob.round_to_tick(80.5 * 1.02))  # PE 24900 ask 80.5
        self.assertEqual(captured[0]["quantity"], 75)
        self.assertEqual(captured[0]["tag"], "AF_OPT_BUILDER")

    def test_a_bad_basket_never_reaches_the_broker(self):
        broker = Broker()
        result, _ = self.run_basket([{"underlying": "NIFTY", "side": "BUY"}], broker)
        self.assertEqual(broker.sent, [])
        self.assertEqual(result.status_code, 400)


class TheBasketIsAProtectedAction(unittest.TestCase):
    def test_execute_needs_the_authenticator(self):
        import auth

        self.assertEqual(auth.classify_sensitive_action("POST", "/api/option-builder/execute"), "broker_order")

    def test_reads_and_paper_do_not(self):
        import auth

        for method, path in (
            ("GET", "/api/option-builder/chain"),
            ("POST", "/api/option-builder/margin"),
            ("POST", "/api/option-builder/paper"),
        ):
            self.assertIsNone(auth.classify_sensitive_action(method, path))


class PaperRoutes(unittest.TestCase):
    def test_open_then_close_round_trip(self):
        store = {}

        async def get_state(key):
            return store.get(key)

        async def set_state(key, value):
            store[key] = value

        chain = ob.shape_chain(_raw_chain(), "NIFTY", "2026-10-13", 75)
        legs = [
            {
                "underlying": "NIFTY",
                "strike": 25000,
                "expiry": "2026-10-13",
                "option_type": "CE",
                "side": "BUY",
                "lots": 1,
                "price": 120,
            },
        ]
        with (
            patch.object(app_module._db_mod, "get_app_state", get_state),
            patch.object(app_module._db_mod, "set_app_state", set_state),
            patch.object(app_module, "_request_user_id", lambda r: 7),
            patch.object(app_module, "_request_broker_context", AsyncMock(return_value=({"id": 7}, object(), "user"))),
            patch.object(app_module, "_ob_lot_size", lambda u, e: 75),
            patch.object(app_module, "_ob_chain", AsyncMock(return_value=chain)),
            patch.object(app_module.alerter, "alert"),
        ):
            opened = asyncio.run(
                app_module.option_builder_paper_open(
                    app_module.OptionBuilderBasketReq(legs=legs, name="One call"), request=None
                )
            )
            closed = asyncio.run(app_module.option_builder_paper_close(opened["basket"]["id"], request=None))
            listed = asyncio.run(app_module.option_builder_paper_list(request=None))
        self.assertEqual(opened["basket"]["legs"][0]["entry"], 120.0)
        self.assertEqual(closed["basket"]["realised"], (130 - 120) * 75)
        self.assertEqual(listed["baskets"][0]["status"], "closed")
        self.assertEqual(json.loads(store["option_builder_paper_7"])[0]["name"], "One call")


if __name__ == "__main__":
    unittest.main()


class Portfolios(unittest.TestCase):
    """Draft portfolios hold paper strategies; saved strategies are just legs."""

    def setUp(self):
        self.now = datetime(2026, 10, 6, 16, 0, tzinfo=IST)
        self.book = ob.ensure_default_portfolio({}, self.now)

    def test_a_new_book_starts_with_paper(self):
        self.assertEqual([p["name"] for p in self.book["portfolios"]], ["Paper"])

    def test_names_are_unique_ignoring_case_and_trimmed(self):
        row = ob.create_portfolio(self.book, "  Buying   CPR ", self.now)
        self.assertEqual((row["id"], row["name"]), (2, "Buying CPR"))
        with self.assertRaises(ob.OptionBuilderError):
            ob.create_portfolio(self.book, "buying cpr", self.now)
        with self.assertRaises(ob.OptionBuilderError):
            ob.create_portfolio(self.book, "   ", self.now)

    def test_rename(self):
        ob.create_portfolio(self.book, "Weekly", self.now)
        ob.rename_portfolio(self.book, 2, "Weekly NIFTY")
        self.assertEqual(self.book["portfolios"][1]["name"], "Weekly NIFTY")
        with self.assertRaises(ob.OptionBuilderError):
            ob.rename_portfolio(self.book, 2, "paper")

    def test_an_old_strategy_belongs_to_the_first_portfolio(self):
        self.assertEqual(ob.basket_portfolio({"id": 1}, self.book), 1)
        ob.create_portfolio(self.book, "Two", self.now)
        self.assertEqual(ob.basket_portfolio({"portfolio_id": 2}, self.book), 2)
        self.assertEqual(ob.basket_portfolio({"portfolio_id": 99}, self.book), 1)

    def test_a_saved_strategy_keeps_legs_not_prices(self):
        legs = ob.normalize_legs(
            [
                {
                    "underlying": "NIFTY",
                    "strike": 25000,
                    "expiry": "2026-10-13",
                    "option_type": "CE",
                    "side": "BUY",
                    "lots": 2,
                    "price": 130,
                }
            ]
        )
        row = ob.save_strategy(self.book, "Morning call", legs, self.now)
        self.assertEqual(
            row["legs"], [{"strike": 25000.0, "expiry": "2026-10-13", "option_type": "CE", "side": "BUY", "lots": 2}]
        )
        self.assertEqual(row["underlying"], "NIFTY")


class PortfolioRoutes(unittest.TestCase):
    def run_routes(self, steps):
        store = {}

        async def get_state(key):
            return store.get(key)

        async def set_state(key, value):
            store[key] = value

        chain = ob.shape_chain(_raw_chain(), "NIFTY", "2026-10-13", 75)
        with (
            patch.object(app_module._db_mod, "get_app_state", get_state),
            patch.object(app_module._db_mod, "set_app_state", set_state),
            patch.object(app_module, "_request_user_id", lambda r: 9),
            patch.object(app_module, "_ob_lot_size", lambda u, e: 75),
            patch.object(app_module, "_ob_chain", AsyncMock(return_value=chain)),
            patch.object(app_module.alerter, "alert"),
        ):
            return [asyncio.run(step()) for step in steps]

    def test_a_strategy_lands_in_the_chosen_portfolio_and_blocks_its_deletion(self):
        legs = [
            {
                "underlying": "NIFTY",
                "strike": 25000,
                "expiry": "2026-10-13",
                "option_type": "CE",
                "side": "BUY",
                "lots": 1,
                "price": 120,
            }
        ]
        req = app_module.OptionBuilderBasketReq
        created, opened, refused, listed = self.run_routes(
            [
                lambda: app_module.option_builder_portfolio_create(
                    app_module.OptionBuilderNameReq(name="CPR"), request=None
                ),
                lambda: app_module.option_builder_paper_open(req(legs=legs, name="One", portfolio_id=2), request=None),
                lambda: app_module.option_builder_portfolio_delete(2, request=None),
                lambda: app_module.option_builder_paper_list(request=None),
            ]
        )
        self.assertEqual(created["portfolio"]["id"], 2)
        self.assertEqual(opened["basket"]["portfolio_id"], 2)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual([p["name"] for p in listed["portfolios"]], ["Paper", "CPR"])
        self.assertEqual(listed["baskets"][0]["portfolio_id"], 2)

    def test_an_unknown_portfolio_is_refused(self):
        legs = [
            {
                "underlying": "NIFTY",
                "strike": 25000,
                "expiry": "2026-10-13",
                "option_type": "CE",
                "side": "BUY",
                "lots": 1,
                "price": 120,
            }
        ]
        (result,) = self.run_routes(
            [
                lambda: app_module.option_builder_paper_open(
                    app_module.OptionBuilderBasketReq(legs=legs, portfolio_id=7), request=None
                )
            ]
        )
        self.assertEqual(result.status_code, 400)

    def test_save_then_delete_a_strategy(self):
        legs = [
            {
                "underlying": "NIFTY",
                "strike": 25000,
                "expiry": "2026-10-13",
                "option_type": "PE",
                "side": "SELL",
                "lots": 1,
            }
        ]
        saved, listed, deleted, after = self.run_routes(
            [
                lambda: app_module.option_builder_saved_create(
                    app_module.OptionBuilderBasketReq(legs=legs, name="Put sell"), request=None
                ),
                lambda: app_module.option_builder_paper_list(request=None),
                lambda: app_module.option_builder_saved_delete(1, request=None),
                lambda: app_module.option_builder_paper_list(request=None),
            ]
        )
        self.assertEqual(saved["saved"]["name"], "Put sell")
        self.assertEqual(len(listed["saved"]), 1)
        self.assertEqual(deleted["status"], "ok")
        self.assertEqual(after["saved"], [])
