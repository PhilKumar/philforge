"""The Equity page's Manual desk can send its orders to Zerodha.

Phil, 2026-10-21: "I also need this Equity used in zerodha as well". The desk
names a stock as both brokers do (RELIANCE), so `ZerodhaClient` places it by
symbol; Dhan stays the default and every Dhan order goes exactly where it did.
These tests hold the form Kite receives, the refusals that keep a Dhan-only
choice from becoming something else at Kite, and the GTT's leg order.
"""

import asyncio
import json
import os
import unittest
from unittest import mock

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402
from broker import zerodha  # noqa: E402
from broker.dhan import DhanOrderError  # noqa: E402


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class _Kite:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, headers=None, params=None, data=None, timeout=None, json=None):
        path = url.replace(zerodha.KITE_API, "")
        self.calls.append({"method": method, "path": path, "params": params, "data": data})
        answer = self.routes.get((method, path))
        return _Resp(*answer) if answer else _Resp(404, {"status": "error", "message": "no route"})


def _client(routes):
    kite = _Kite(routes)
    return zerodha.ZerodhaClient("key", "token", session=kite), kite


OK_ORDER = (200, {"status": "success", "data": {"order_id": "250101000001"}})


class AStockOrderAtKite(unittest.TestCase):
    def test_a_market_cnc_buy_is_sent_by_symbol(self):
        client, kite = _client({("POST", "/orders/regular"): OK_ORDER})
        out = client.place_equity_order("reliance", "NSE_EQ", "BUY", 5, "MARKET", "CNC", tag="PFSTK_RELIANCE")
        self.assertEqual(out, {"orderId": "250101000001", "orderStatus": "TRANSIT", "broker": "zerodha"})
        form = kite.calls[0]["data"]
        self.assertEqual(
            {
                k: form[k]
                for k in ("tradingsymbol", "exchange", "transaction_type", "order_type", "quantity", "product")
            },
            {
                "tradingsymbol": "RELIANCE",
                "exchange": "NSE",
                "transaction_type": "BUY",
                "order_type": "MARKET",
                "quantity": 5,
                "product": "CNC",
            },
        )
        self.assertEqual(form["market_protection"], -1)
        self.assertEqual(form["tag"], "PFSTKRELIANCE")

    def test_intraday_is_mis_and_a_stop_limit_keeps_its_paisa(self):
        client, kite = _client({("POST", "/orders/regular"): OK_ORDER})
        client.place_equity_order(
            "ITC", "NSE_EQ", "SELL", 10, "STOP_LOSS", "INTRADAY", price=231.37, trigger_price=231.5
        )
        form = kite.calls[0]["data"]
        self.assertEqual((form["product"], form["order_type"]), ("MIS", "SL"))
        # Under Rs 250 a stock ticks in paise; rounding to 0.05 would move the price.
        self.assertEqual((form["price"], form["trigger_price"]), (231.37, 231.5))
        self.assertNotIn("market_protection", form)

    def test_an_amo_goes_as_kites_amo_variety(self):
        client, kite = _client({("POST", "/orders/amo"): OK_ORDER})
        client.place_equity_order("TCS", "NSE_EQ", "BUY", 1, "LIMIT", "CNC", price=3000, after_market_order=True)
        self.assertEqual(kite.calls[0]["path"], "/orders/amo")

    def test_a_bse_stock_goes_to_bse(self):
        client, kite = _client({("POST", "/orders/regular"): OK_ORDER})
        client.place_equity_order("SBIN", "BSE_EQ", "BUY", 1)
        self.assertEqual(kite.calls[0]["data"]["exchange"], "BSE")

    def test_dhans_own_products_are_refused_not_converted(self):
        client, kite = _client({("POST", "/orders/regular"): OK_ORDER})
        for product in ("BO", "CO", "MARGIN"):
            with self.assertRaises(DhanOrderError):
                client.place_equity_order("TCS", "NSE_EQ", "BUY", 1, "MARKET", product)
        self.assertEqual(kite.calls, [])

    def test_kites_no_is_a_refusal(self):
        client, _kite = _client(
            {("POST", "/orders/regular"): (400, {"status": "error", "message": "Insufficient funds"})}
        )
        with self.assertRaises(DhanOrderError) as caught:
            client.place_equity_order("TCS", "NSE_EQ", "BUY", 1)
        self.assertTrue(getattr(caught.exception, "rejected", False))

    def test_cancel_uses_the_orders_own_variety(self):
        client, kite = _client(
            {
                ("GET", "/orders/9"): (200, {"status": "success", "data": [{"order_id": "9", "variety": "amo"}]}),
                ("DELETE", "/orders/amo/9"): (200, {"status": "success", "data": {"order_id": "9"}}),
            }
        )
        self.assertEqual(client.cancel_equity_order("9")["orderStatus"], "CANCELLED")
        self.assertEqual(kite.calls[-1]["path"], "/orders/amo/9")


class AGttAtKite(unittest.TestCase):
    LTP = (200, {"status": "success", "data": {"NSE:INFY": {"last_price": 1500.0}}})
    MADE = (200, {"status": "success", "data": {"trigger_id": 777}})

    def test_oco_legs_go_low_to_high_with_their_own_prices(self):
        client, kite = _client({("GET", "/quote/ltp"): self.LTP, ("POST", "/gtt/triggers"): self.MADE})
        # The desk's first leg is the stop, its second the target.
        out = client.place_gtt(
            "INFY", "NSE_EQ", "SELL", 10, "OCO", "CNC", "LIMIT",
            price=1395, trigger_price=1400, price1=1705, trigger_price1=1700, quantity1=10,
        )  # fmt: skip
        self.assertEqual(out["orderId"], "777")
        form = kite.calls[-1]["data"]
        self.assertEqual(form["type"], "two-leg")
        condition, orders = json.loads(form["condition"]), json.loads(form["orders"])
        self.assertEqual(condition["trigger_values"], [1400.0, 1700.0])
        self.assertEqual(condition["last_price"], 1500.0)
        self.assertEqual([o["price"] for o in orders], [1395.0, 1705.0])
        self.assertEqual({o["transaction_type"] for o in orders}, {"SELL"})

    def test_a_single_is_one_trigger(self):
        client, kite = _client({("GET", "/quote/ltp"): self.LTP, ("POST", "/gtt/triggers"): self.MADE})
        client.place_gtt("INFY", "NSE_EQ", "BUY", 3, "SINGLE", "CNC", "LIMIT", price=1405, trigger_price=1400)
        form = kite.calls[-1]["data"]
        self.assertEqual((form["type"], json.loads(form["condition"])["trigger_values"]), ("single", [1400.0]))

    def test_market_and_mtf_are_refused_before_kite_is_asked(self):
        client, kite = _client({("GET", "/quote/ltp"): self.LTP, ("POST", "/gtt/triggers"): self.MADE})
        with self.assertRaises(DhanOrderError):
            client.place_gtt("INFY", "NSE_EQ", "BUY", 1, "SINGLE", "CNC", "MARKET", trigger_price=1400)
        with self.assertRaises(DhanOrderError):
            client.place_gtt("INFY", "NSE_EQ", "BUY", 1, "SINGLE", "MTF", "LIMIT", price=1, trigger_price=1)
        self.assertEqual(kite.calls, [])

    def test_the_list_reads_as_the_desks_forever_rows(self):
        rows = [
            {
                "id": 777,
                "type": "two-leg",
                "status": "active",
                "condition": {"tradingsymbol": "INFY", "trigger_values": [1400, 1700]},
                "orders": [{"transaction_type": "SELL", "quantity": 10}],
            }
        ]
        client, _kite = _client({("GET", "/gtt/triggers"): (200, {"status": "success", "data": rows})})
        row = client.get_gtts()[0]
        self.assertEqual(
            (row["orderId"], row["tradingSymbol"], row["orderFlag"], row["triggerPrice"], row["orderStatus"]),
            ("777", "INFY", "OCO", 1400.0, "PENDING"),
        )


class _Request:
    class state:
        current_user = {"id": 1}


class TheDeskPicksItsBroker(unittest.TestCase):
    def _order(self, **kw):
        body = {"symbol": "RELIANCE", "transaction_type": "BUY", "quantity": 1, "broker": "zerodha", **kw}
        return app.StockTerminalOrderRequest(**body)

    def _run(self, req, client):
        stock = {"symbol": "RELIANCE", "security_id": "2885", "exchange_segment": "NSE_EQ"}
        with (
            mock.patch.object(app, "_resolve_terminal_stock", lambda symbol: stock),
            mock.patch.object(app, "check_rate_limit", lambda *a, **k: None),
            mock.patch.object(app._auth_mod, "get_current_user", mock.AsyncMock(return_value={"id": 1})),
            mock.patch.object(app, "_user_zerodha_client", lambda user, require_today=True: (client, "zerodha")),
        ):
            return asyncio.run(app.terminal_place_order(req, _Request()))

    def test_a_zerodha_order_goes_to_zerodha(self):
        client = mock.MagicMock()
        client.place_equity_order.return_value = {"orderId": "1", "orderStatus": "TRANSIT"}
        out = self._run(self._order(product_type="CNC"), client)
        self.assertEqual(out["broker"], "zerodha")
        self.assertEqual(client.place_equity_order.call_args.kwargs["symbol"], "RELIANCE")

    def test_a_bracket_order_is_refused_at_zerodha(self):
        client = mock.MagicMock()
        with self.assertRaises(app.HTTPException) as caught:
            self._run(self._order(product_type="BO"), client)
        self.assertEqual(caught.exception.status_code, 400)
        client.place_equity_order.assert_not_called()

    def test_no_login_today_says_so(self):
        with (
            mock.patch.object(app._auth_mod, "get_current_user", mock.AsyncMock(return_value={"id": 1})),
            mock.patch.object(
                app, "_user_zerodha_client", lambda user, require_today=True: (None, "zerodha_login_needed")
            ),
        ):
            client, name, missing = asyncio.run(app._terminal_desk_broker(_Request(), "zerodha"))
        self.assertIsNone(client)
        self.assertIn("Log in to Zerodha", missing)

    def test_no_broker_named_is_dhan(self):
        dhan = object()
        with mock.patch.object(app, "_request_broker_context", mock.AsyncMock(return_value=({"id": 1}, dhan, "user"))):
            client, name, _missing = asyncio.run(app._terminal_desk_broker(_Request(), ""))
        self.assertEqual((client, name), (dhan, "dhan"))
        self.assertEqual(app.StockTerminalOrderRequest(symbol="X", transaction_type="BUY", quantity=1).broker, "dhan")


if __name__ == "__main__":
    unittest.main()
