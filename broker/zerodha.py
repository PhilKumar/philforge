"""Zerodha (Kite Connect v3) as a second broker for the live CE/PE books.

WHY IT SPEAKS DHAN. Every live engine names an option contract by Dhan's
security id and reads broker answers in Dhan's words (`orderId`, `orderStatus`
TRADED, `netQty`, `availabelBalance`...). Rather than teach the engines a second
vocabulary -- and risk the books that are trading real money on Dhan today --
this client answers the same eleven calls `engine/live.py` makes, translating
at the door: Dhan's contract (from its public scrip master) to Kite's
tradingsymbol on the way out, Kite's answer back to Dhan's shape on the way in.

WHAT KITE DOES DIFFERENTLY, and where it is handled here:
  * The access token dies every morning at about 06:00 IST, and the exchange
    requires a person to log in by hand once a day. `token_is_current` says
    whether today's login has happened; the engine cannot trade without it.
  * A MARKET order over the API must carry `market_protection`; -1 lets Kite
    choose the band.
  * Orders are accepted only from the static IP registered on the Kite
    developer console -- the same 13.205.6.8 Dhan uses (the EC2 server since 18-Sep-2026).
  * `tag` is at most 20 characters.

This file never logs a token, a secret or a request_token.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import re
import threading
import time as _time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from broker.dhan import (
    MCX_OPTION_MULTIPLIER,
    AmbiguousOrderSubmission,
    DhanOrderError,
    ScripMaster,
    _first_positive_price,
    mcx_lots,
    option_tick_size,
    round_to_tick,
)

log = logging.getLogger("zerodha")

KITE_API = "https://api.kite.trade"
KITE_LOGIN = "https://kite.zerodha.com/connect/login"
IST = ZoneInfo("Asia/Kolkata")

# Kite's token dies at about 06:00 IST; a token minted before the latest 06:00
# is yesterday's.
TOKEN_ROLLOVER_HOUR = 6

_RETRYABLE = {429, 500, 502, 503, 504}

# Dhan index ids (app.py `_get_instrument_map`) -> Kite index instrument tokens.
# Kite's tokens for these indices have not changed in years; they are also
# looked up by name in the instrument dump when it is loaded.
INDEX_TOKENS = {
    "13": ("NSE", "NIFTY 50", 256265),
    "25": ("NSE", "NIFTY BANK", 260105),
    "27": ("NSE", "NIFTY FIN SERVICE", 257801),
    "51": ("BSE", "SENSEX", 265),
}

_INTERVALS = {
    "1": "minute",
    "3": "3minute",
    "5": "5minute",
    "10": "10minute",
    "15": "15minute",
    "30": "30minute",
    "60": "60minute",
    "D": "day",
}

# Kite's status -> the status the engines already understand from Dhan.
_STATUS = {
    "COMPLETE": "TRADED",
    "REJECTED": "REJECTED",
    "CANCELLED": "CANCELLED",
    "OPEN": "PENDING",
    "TRIGGER PENDING": "PENDING",
}

_PRODUCT = {"INTRADAY": "MIS", "MIS": "MIS", "MARGIN": "NRML", "NRML": "NRML", "CNC": "CNC"}

# The Manual desk's stock products, in Kite's words. MARGIN, CO and BO are
# Dhan's and are refused here rather than quietly turned into something else.
_EQUITY_PRODUCT = {"CNC": "CNC", "INTRADAY": "MIS", "MIS": "MIS", "MTF": "MTF"}
_EQUITY_EXCHANGE = {"NSE_EQ": "NSE", "BSE_EQ": "BSE"}

# Kite's GTT status -> the desk's: "active" still waits on its trigger.
_GTT_STATUS = {
    "active": "PENDING",
    "triggered": "TRADED",
    "disabled": "DISABLED",
    "expired": "EXPIRED",
    "cancelled": "CANCELLED",
    "rejected": "REJECTED",
    "deleted": "CANCELLED",
}

_ORDER_TYPE = {
    "MARKET": "MARKET",
    "LIMIT": "LIMIT",
    "SL": "SL",
    "STOP_LOSS": "SL",
    "SL-M": "SL-M",
    "SLM": "SL-M",
    "STOP_LOSS_MARKET": "SL-M",
}


def login_url(api_key: str) -> str:
    return f"{KITE_LOGIN}?v=3&api_key={api_key}"


def session_checksum(api_key: str, request_token: str, api_secret: str) -> str:
    return hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()


def exchange_request_token(api_key: str, api_secret: str, request_token: str, *, timeout: float = 10.0) -> dict:
    """Trade the one-time request_token from the login redirect for today's
    access token. Returns Kite's `data` block (access_token, user_id, ...)."""
    resp = requests.post(
        f"{KITE_API}/session/token",
        data={
            "api_key": api_key,
            "request_token": request_token,
            "checksum": session_checksum(api_key, request_token, api_secret),
        },
        headers={"X-Kite-Version": "3"},
        timeout=timeout,
    )
    body = _json(resp)
    if resp.status_code != 200 or body.get("status") != "success":
        raise RuntimeError(f"Zerodha login failed: {body.get('message') or resp.status_code}")
    return body.get("data") or {}


def last_rollover(now: datetime | None = None) -> datetime:
    """The most recent 06:00 IST -- the moment yesterday's token died."""
    now = (now or datetime.now(IST)).astimezone(IST)
    cut = now.replace(hour=TOKEN_ROLLOVER_HOUR, minute=0, second=0, microsecond=0)
    return cut if now >= cut else cut - timedelta(days=1)


def token_is_current(saved_at: str | None, now: datetime | None = None) -> bool:
    """True when the token was minted after the latest 06:00 IST."""
    if not saved_at:
        return False
    try:
        minted = datetime.fromisoformat(str(saved_at))
    except ValueError:
        return False
    if minted.tzinfo is None:
        minted = minted.replace(tzinfo=IST)
    return minted >= last_rollover(now)


def kite_tag(tag: str) -> str:
    """Kite takes at most 20 characters; keep only the safe ones."""
    return re.sub(r"[^A-Za-z0-9]", "", str(tag or ""))[:20]


def _json(resp) -> dict:
    try:
        body = resp.json()
    except Exception:
        return {"status": "error", "message": (getattr(resp, "text", "") or "")[:200]}
    return body if isinstance(body, dict) else {"data": body}


def _exchange_for(underlying: str) -> str:
    return "BFO" if str(underlying).upper() == "SENSEX" else "NFO"


def _strike_key(strike) -> str:
    return ScripMaster.strike_key(strike)


class KiteInstruments:
    """Kite's option contracts, keyed the way Dhan's scrip master keys them.

    Loaded once a day from Kite's instrument dump (NFO, BFO, and MCX's crude and gas). The key
    `NIFTY_23600_2026-09-22_CE` is exactly `ScripMaster`'s, which is what lets
    a Dhan security id and a Kite tradingsymbol meet.
    """

    _by_key: dict[str, dict] = {}
    _by_symbol: dict[str, str] = {}  # "NFO:NIFTY2592223600CE" -> key
    _loaded_on: date | None = None
    _lock = threading.Lock()

    @classmethod
    def load_rows(cls, rows, loaded_on: date | None = None) -> int:
        by_key: dict[str, dict] = {}
        by_symbol: dict[str, str] = {}
        for row in rows:
            if str(row.get("instrument_type") or "") not in ("CE", "PE"):
                continue
            name = str(row.get("name") or "").strip().strip('"').upper()
            expiry = str(row.get("expiry") or "")[:10]
            try:
                strike = float(row.get("strike") or 0)
            except (TypeError, ValueError):
                continue
            if not name or not expiry or strike <= 0:
                continue
            exchange = str(row.get("exchange") or "NFO")
            if exchange == "MCX" and name not in MCX_OPTION_MULTIPLIER:
                continue  # MCX lists gold, silver, metals...; the desk trades crude and gas
            key = f"{name}_{_strike_key(strike)}_{expiry}_{row['instrument_type']}"
            info = {
                "name": name,
                # Kite lists an MCX option's lot as 1: quantity there is LOTS.
                "multiplier": MCX_OPTION_MULTIPLIER.get(name, 1) if exchange == "MCX" else 1,
                "tradingsymbol": str(row.get("tradingsymbol") or ""),
                "exchange": exchange,
                "instrument_token": int(float(row.get("instrument_token") or 0)),
                "lot_size": int(float(row.get("lot_size") or 0)),
                "tick_size": float(row.get("tick_size") or 0.05),
            }
            by_key[key] = info
            by_symbol[f"{exchange}:{info['tradingsymbol']}"] = key
        with cls._lock:
            cls._by_key, cls._by_symbol = by_key, by_symbol
            cls._loaded_on = loaded_on or datetime.now(IST).date()
        return len(by_key)

    @classmethod
    def ensure_loaded(cls, headers: dict) -> bool:
        today = datetime.now(IST).date()
        if cls._loaded_on == today and cls._by_key:
            return True
        rows: list[dict] = []
        for exchange in ("NFO", "BFO", "MCX"):
            resp = requests.get(f"{KITE_API}/instruments/{exchange}", headers=headers, timeout=30)
            if resp.status_code != 200:
                log.warning("[ZERODHA] instrument dump %s returned %s", exchange, resp.status_code)
                continue
            rows.extend(csv.DictReader(io.StringIO(resp.text)))
        if not rows:
            return bool(cls._by_key)
        count = cls.load_rows(rows, today)
        log.info("[ZERODHA] loaded %d option contracts", count)
        return count > 0

    @classmethod
    def contract(cls, underlying: str, strike, expiry: str, option_type: str) -> dict | None:
        key = f"{str(underlying).upper()}_{_strike_key(strike)}_{str(expiry)[:10]}_{str(option_type).upper()}"
        return cls._by_key.get(key)

    @classmethod
    def key_for_symbol(cls, exchange: str, tradingsymbol: str) -> str | None:
        return cls._by_symbol.get(f"{exchange}:{tradingsymbol}")

    @classmethod
    def multiplier_for(cls, exchange: str, tradingsymbol: str) -> int:
        """Units in one of Kite's counted quantities: an MCX lot's size, else 1."""
        if str(exchange or "").upper() != "MCX":
            return 1
        key = cls.key_for_symbol("MCX", tradingsymbol)
        info = cls._by_key.get(key) if key else None
        return int((info or {}).get("multiplier") or 1)


def _kite_units(row: dict, *fields: str) -> dict:
    """Kite's MCX quantities are LOTS; the engines count units (broker.dhan.mcx_lots)."""
    size = KiteInstruments.multiplier_for(row.get("exchange"), row.get("tradingsymbol"))
    if size <= 1:
        return row
    row = dict(row)
    for field in fields:
        try:
            row[field] = int(row.get(field) or 0) * size
        except (TypeError, ValueError):
            pass
    return row


def _dhan_id_to_key() -> dict[str, str]:
    """Dhan security id -> contract key, inverted from the scrip master."""
    ScripMaster.ensure_loaded()
    cache = ScripMaster._options_cache
    stamp = (ScripMaster._loaded_date, len(cache))
    if _dhan_id_to_key._stamp != stamp:
        _dhan_id_to_key._map = {str(sid): key for key, sid in cache.items()}
        _dhan_id_to_key._stamp = stamp
    return _dhan_id_to_key._map


_dhan_id_to_key._stamp = None
_dhan_id_to_key._map = {}


def _key_to_dhan_id(key: str) -> str:
    ScripMaster.ensure_loaded()
    return str(ScripMaster._options_cache.get(key) or "")


class ZerodhaSessionError(ConnectionError):
    """There is no Kite session to send anything through: no login yet today,
    or the 06:00 lapse. NOTHING WAS SENT -- unlike a timeout, where an order
    may be working. A caller can try again after the login instead of
    freezing as it must for an unknown outcome."""

    nothing_sent = True


class ZerodhaClient:
    """Kite Connect, answering the calls `engine/live.py` makes of `DhanClient`."""

    broker_name = "Zerodha"
    # Kite has no order that carries its own stop and is retired with the
    # position, which is what Dhan's Super Order is. The shared option executor
    # reads this and sends a plain buy here (engine/options_live_executor.py).
    supports_super_order = False

    def __init__(  # nosec B107 - empty defaults mean "not connected", not a password
        self,
        api_key: str = "",
        access_token: str = "",
        *,
        token_saved_at: str | None = None,
        user_id: str = "",
        session: requests.Session | None = None,
    ):
        self.api_key = str(api_key or "").strip()
        self._access_token = str(access_token or "").strip()
        self.token_saved_at = token_saved_at
        self.client_id = str(user_id or "").strip()
        self._http = session or requests.Session()

    # ── plumbing ──────────────────────────────────────────────
    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def headers(self) -> dict:
        return {
            "X-Kite-Version": "3",
            "Authorization": f"token {self.api_key}:{self._access_token}",
        }

    def _is_configured(self) -> bool:
        return bool(self.api_key and self._access_token)

    def token_is_current(self, now: datetime | None = None) -> bool:
        return self._is_configured() and token_is_current(self.token_saved_at, now)

    def _call(self, method: str, path: str, *, params=None, data=None, json=None, timeout: float = 10.0):
        if not self._is_configured():
            raise ZerodhaSessionError("Zerodha is not connected. Log in to Zerodha from Settings first.")
        extra = {"json": json} if json is not None else {}
        resp = self._http.request(
            method, f"{KITE_API}{path}", headers=self.headers, params=params, data=data, timeout=timeout, **extra
        )
        body = _json(resp)
        if resp.status_code == 403 and body.get("error_type") == "TokenException":
            raise ZerodhaSessionError("Zerodha session has expired. Log in to Zerodha again (it lapses daily at 6 AM).")
        return resp, body

    def _get_data(self, path: str, *, params=None, timeout: float = 10.0):
        resp, body = self._call("GET", path, params=params, timeout=timeout)
        if resp.status_code != 200 or body.get("status") == "error":
            raise RuntimeError(f"Zerodha {path} failed ({resp.status_code}): {body.get('message', '')}")
        return body.get("data")

    def _instruments(self) -> None:
        if not KiteInstruments.ensure_loaded(self.headers):
            raise RuntimeError("Zerodha instrument list could not be loaded.")

    def _contract(self, underlying: str, strike, expiry: str, option_type: str) -> dict:
        self._instruments()
        info = KiteInstruments.contract(underlying, strike, expiry, option_type)
        if not info:
            raise RuntimeError(f"Zerodha has no contract {underlying} {strike}{option_type} expiring {expiry}.")
        return info

    def _dhan_id_for(self, exchange: str, tradingsymbol: str) -> str:
        key = KiteInstruments.key_for_symbol(exchange, tradingsymbol)
        return _key_to_dhan_id(key) if key else ""

    # ── orders ────────────────────────────────────────────────
    def _place(
        self,
        info: dict,
        transaction_type: str,
        quantity: int,
        order_type: str,
        product_type: str,
        price: float,
        trigger_price: float,
        tag: str,
    ) -> dict:
        kite_type = _ORDER_TYPE.get(str(order_type or "MARKET").upper())
        if not kite_type:
            raise DhanOrderError("Order placement", 400, f"Order type {order_type!r} is not supported on Zerodha")
        tick = info.get("tick_size") or 0.05
        if info.get("exchange") == "MCX":
            # Zerodha blocks MARKET and SL-M on commodity options; only LIMIT and SL go.
            if kite_type in ("MARKET", "SL-M"):
                refusal = DhanOrderError(
                    "Order placement", 400, f"Zerodha takes only LIMIT and SL orders on MCX options, not {kite_type}"
                )
                refusal.rejected = True
                raise refusal
            quantity = mcx_lots(info.get("name"), quantity)
        form = {
            "tradingsymbol": info["tradingsymbol"],
            "exchange": info["exchange"],
            "transaction_type": str(transaction_type).upper(),
            "order_type": kite_type,
            "quantity": int(quantity),
            "product": _PRODUCT.get(str(product_type or "INTRADAY").upper(), "MIS"),
            "validity": "DAY",
        }
        if kite_type in ("LIMIT", "SL"):
            form["price"] = round_to_tick(float(price or 0), tick)
        if kite_type in ("SL", "SL-M"):
            form["trigger_price"] = round_to_tick(float(trigger_price or 0), tick)
        if kite_type in ("MARKET", "SL-M"):
            form["market_protection"] = -1
        if kite_tag(tag):
            form["tag"] = kite_tag(tag)
        log.info(
            "[ZERODHA] Submitting order side=%s exchange=%s type=%s product=%s",
            form["transaction_type"],
            form["exchange"],
            kite_type,
            form["product"],
        )
        try:
            resp, body = self._call("POST", "/orders/regular", data=form)
        except ConnectionError:
            raise
        except Exception as exc:
            raise AmbiguousOrderSubmission(
                "Order submission was not confirmed by Zerodha; reconcile the order book before retrying."
            ) from exc
        if resp.status_code in _RETRYABLE or body.get("error_type") == "NetworkException":
            raise AmbiguousOrderSubmission(
                "Zerodha returned a transient response after order submission; reconcile the order book before retrying."
            )
        if resp.status_code != 200 or body.get("status") != "success":
            refusal = DhanOrderError(
                "Order placement", resp.status_code, str(body.get("message") or "Zerodha rejected the order"), body
            )
            # Kite ANSWERED, and said no: no order exists. Marked so a caller
            # can tell this from a submission whose fate is unknown.
            refusal.rejected = True
            raise refusal
        order_id = str((body.get("data") or {}).get("order_id") or "")
        return {"orderId": order_id, "orderStatus": "TRANSIT", "broker": "zerodha"}

    def place_option_order(
        self,
        underlying: str,
        strike_price: int,
        option_type: str,
        expiry: str,
        transaction_type: str,
        quantity: int,
        order_type: str = "MARKET",
        product_type: str = "INTRADAY",
        price: float = 0,
        trigger_price: float = 0,
        tag: str = "PhilForge",
    ) -> dict:
        info = self._contract(underlying, strike_price, expiry, option_type)
        return self._place(info, transaction_type, quantity, order_type, product_type, price, trigger_price, tag)

    def place_sl_order(
        self,
        underlying: str,
        strike_price: int,
        option_type: str,
        expiry: str,
        transaction_type: str,
        quantity: int,
        trigger_price: float,
        price: float = 0,
        product_type: str = "INTRADAY",
        order_type: str = "SL",
        tag: str = "PhilForge_SL",
    ) -> dict:
        info = self._contract(underlying, strike_price, expiry, option_type)
        return self._place(info, transaction_type, quantity, order_type, product_type, price, trigger_price, tag)

    # ── equity: the Equity page's Manual desk ──────────────────
    # Phil, 2026-10-21: "I also need this Equity used in zerodha as well".
    # A stock is named the same on both brokers (RELIANCE, NIFTYBEES), so the
    # desk's symbol is Kite's tradingsymbol; only the exchange is renamed.
    # Kite has no Bracket Order, no Cover Order through this desk, no AMO
    # time slot and no slicing: those ask for Dhan.

    def _equity_exchange(self, exchange_segment: str) -> str:
        exchange = _EQUITY_EXCHANGE.get(str(exchange_segment or "").upper())
        if not exchange:
            raise DhanOrderError(
                "Order placement", 400, f"Zerodha's desk trades NSE and BSE stocks, not {exchange_segment}"
            )
        return exchange

    def place_equity_order(
        self,
        symbol: str,
        exchange_segment: str,
        transaction_type: str,
        quantity: int,
        order_type: str = "MARKET",
        product_type: str = "CNC",
        price: float = 0,
        trigger_price: float = 0,
        validity: str = "DAY",
        disclosed_quantity: int = 0,
        after_market_order: bool = False,
        tag: str = "PhilForge",
    ) -> dict:
        exchange = self._equity_exchange(exchange_segment)
        kite_type = _ORDER_TYPE.get(str(order_type or "MARKET").upper())
        product = _EQUITY_PRODUCT.get(str(product_type or "CNC").upper())
        if not kite_type:
            raise DhanOrderError("Order placement", 400, f"Order type {order_type!r} is not supported on Zerodha")
        if not product:
            raise DhanOrderError(
                "Order placement", 400, f"Zerodha's desk takes CNC, INTRADAY or MTF, not {product_type}"
            )
        form = {
            "tradingsymbol": str(symbol).strip().upper(),
            "exchange": exchange,
            "transaction_type": str(transaction_type).upper(),
            "order_type": kite_type,
            "quantity": int(quantity),
            "product": product,
            "validity": str(validity or "DAY").upper(),
        }
        # A stock's tick is 0.05 above Rs 250 and 0.01 under it: the price goes
        # as typed (to the paisa) and Kite says so if it is off the tick.
        if kite_type in ("LIMIT", "SL"):
            form["price"] = round(float(price or 0), 2)
        if kite_type in ("SL", "SL-M"):
            form["trigger_price"] = round(float(trigger_price or 0), 2)
        if kite_type in ("MARKET", "SL-M"):
            form["market_protection"] = -1
        if int(disclosed_quantity or 0) > 0:
            form["disclosed_quantity"] = int(disclosed_quantity)
        if kite_tag(tag):
            form["tag"] = kite_tag(tag)
        variety = "amo" if after_market_order else "regular"
        log.info(
            "[ZERODHA] Submitting equity order side=%s exchange=%s type=%s product=%s variety=%s",
            form["transaction_type"],
            exchange,
            kite_type,
            product,
            variety,
        )
        try:
            resp, body = self._call("POST", f"/orders/{variety}", data=form)
        except ConnectionError:
            raise
        except Exception as exc:
            raise AmbiguousOrderSubmission(
                "Order submission was not confirmed by Zerodha; check the Kite order book before retrying."
            ) from exc
        if resp.status_code in _RETRYABLE or body.get("error_type") == "NetworkException":
            raise AmbiguousOrderSubmission(
                "Zerodha returned a transient response after order submission; check the Kite order book before retrying."
            )
        if resp.status_code != 200 or body.get("status") != "success":
            refusal = DhanOrderError(
                "Order placement", resp.status_code, str(body.get("message") or "Zerodha rejected the order"), body
            )
            refusal.rejected = True
            raise refusal
        order_id = str((body.get("data") or {}).get("order_id") or "")
        return {"orderId": order_id, "orderStatus": "TRANSIT", "broker": "zerodha"}

    def cancel_equity_order(self, order_id: str) -> dict:
        """Cancel a desk order whatever its variety: an AMO is cancelled as one."""
        variety = "regular"
        try:
            history = self._get_data(f"/orders/{order_id}") or []
            if history:
                variety = str(history[-1].get("variety") or "regular")
        except Exception:
            pass
        resp, body = self._call("DELETE", f"/orders/{variety}/{order_id}")
        if resp.status_code != 200 or body.get("status") != "success":
            raise DhanOrderError("Order cancel", resp.status_code, str(body.get("message") or "Zerodha refused"), body)
        return {"orderId": str(order_id), "orderStatus": "CANCELLED", "broker": "zerodha"}

    def _equity_ltp(self, exchange: str, symbol: str) -> float:
        name = f"{exchange}:{str(symbol).strip().upper()}"
        row = (self._get_data("/quote/ltp", params=[("i", name)]) or {}).get(name) or {}
        price = float(row.get("last_price") or 0.0)
        if price <= 0:
            raise DhanOrderError("GTT", 400, f"Zerodha has no price for {name}; is the symbol listed there?")
        return price

    def place_gtt(
        self,
        symbol: str,
        exchange_segment: str,
        transaction_type: str,
        quantity: int,
        order_flag: str = "SINGLE",
        product_type: str = "CNC",
        order_type: str = "LIMIT",
        price: float = 0,
        trigger_price: float = 0,
        price1: float = 0,
        trigger_price1: float = 0,
        quantity1: int = 0,
    ) -> dict:
        """Kite's GTT, asked the way the desk asks Dhan for a Forever Order.

        SINGLE is one trigger. OCO is Kite's two-leg GTT: the desk's first leg
        (trigger_price/price) is the stop and its second (trigger_price1/price1)
        the target. Kite wants the two triggers low-to-high with each order in
        the same place, so the legs are sorted by trigger, not by name.
        Kite's GTT places a LIMIT order and holds CNC only.
        """
        exchange = self._equity_exchange(exchange_segment)
        if str(order_type or "LIMIT").upper() != "LIMIT":
            raise DhanOrderError("GTT", 400, "Zerodha's GTT places a LIMIT order; set Order Type to LIMIT and a price")
        if str(product_type or "CNC").upper() != "CNC":
            raise DhanOrderError("GTT", 400, f"Zerodha's GTT holds CNC (delivery) only, not {product_type}")
        side = str(transaction_type).upper()
        symbol = str(symbol).strip().upper()
        legs = [(float(trigger_price), float(price), int(quantity))]
        if str(order_flag or "SINGLE").upper() == "OCO":
            legs.append((float(trigger_price1), float(price1), int(quantity1 or quantity)))
            legs.sort(key=lambda leg: leg[0])
        condition = {
            "exchange": exchange,
            "tradingsymbol": symbol,
            "trigger_values": [round(leg[0], 2) for leg in legs],
            "last_price": self._equity_ltp(exchange, symbol),
        }
        orders = [
            {
                "exchange": exchange,
                "tradingsymbol": symbol,
                "transaction_type": side,
                "quantity": leg[2],
                "order_type": "LIMIT",
                "product": "CNC",
                "price": round(leg[1], 2),
            }
            for leg in legs
        ]
        form = {
            "type": "two-leg" if len(legs) == 2 else "single",
            "condition": json.dumps(condition),
            "orders": json.dumps(orders),
        }
        resp, body = self._call("POST", "/gtt/triggers", data=form)
        if resp.status_code != 200 or body.get("status") != "success":
            refusal = DhanOrderError(
                "GTT", resp.status_code, str(body.get("message") or "Zerodha refused the GTT"), body
            )
            refusal.rejected = True
            raise refusal
        trigger_id = str((body.get("data") or {}).get("trigger_id") or "")
        return {"orderId": trigger_id, "orderStatus": "PENDING", "broker": "zerodha"}

    def get_gtts(self) -> list:
        """Kite's GTTs in the rows the desk's Forever table reads."""
        rows = []
        for row in self._get_data("/gtt/triggers") or []:
            condition = row.get("condition") or {}
            orders = row.get("orders") or []
            first = orders[0] if orders else {}
            triggers = condition.get("trigger_values") or [0]
            status = str(row.get("status") or "").lower()
            rows.append(
                {
                    "orderId": str(row.get("id") or ""),
                    "tradingSymbol": str(condition.get("tradingsymbol") or ""),
                    "transactionType": str(first.get("transaction_type") or ""),
                    "orderFlag": "OCO" if row.get("type") == "two-leg" else "SINGLE",
                    "triggerPrice": float(triggers[0] or 0),
                    "triggerPrices": [float(t or 0) for t in triggers],
                    "quantity": int(first.get("quantity") or 0),
                    # "active" waits on the trigger -- cancellable, as Dhan's PENDING is.
                    "orderStatus": _GTT_STATUS.get(status, status.upper()),
                    "createTime": str(row.get("created_at") or ""),
                    "broker": "zerodha",
                }
            )
        return rows

    def delete_gtt(self, trigger_id: str) -> dict:
        resp, body = self._call("DELETE", f"/gtt/triggers/{trigger_id}")
        if resp.status_code != 200 or body.get("status") != "success":
            raise DhanOrderError("GTT cancel", resp.status_code, str(body.get("message") or "Zerodha refused"), body)
        return {"orderId": str(trigger_id), "orderStatus": "CANCELLED", "broker": "zerodha"}

    def cancel_order(self, order_id: str) -> dict:
        resp, body = self._call("DELETE", f"/orders/regular/{order_id}")
        if resp.status_code != 200 or body.get("status") != "success":
            return {"orderId": str(order_id), "orderStatus": "", "message": body.get("message", "")}
        # Kite's DELETE only says the request was taken; the engine then asks
        # get_order_status, exactly as it does with Dhan.
        return {"orderId": str(order_id), "orderStatus": ""}

    def modify_order(
        self,
        order_id: str,
        order_type: str = None,
        quantity: int = None,
        price: float = None,
        trigger_price: float = None,
        underlying: str = "",
    ) -> dict:
        """Move a resting order (Scalp moves its stop with this). Dhan's call,
        Dhan's answer shape. Option ticks are 0.05 on NFO and BFO alike;
        `underlying` names an MCX order's commodity -- crude ticks in 0.10, and
        its quantity goes as lots."""
        form: dict = {}
        tick = option_tick_size(underlying)
        if order_type:
            kite_type = _ORDER_TYPE.get(str(order_type).upper())
            if not kite_type:
                raise DhanOrderError("Order modify", 400, f"Order type {order_type!r} is not supported on Zerodha")
            form["order_type"] = kite_type
        if quantity:
            form["quantity"] = (
                mcx_lots(underlying, quantity)
                if str(underlying or "").upper() in MCX_OPTION_MULTIPLIER
                else int(quantity)
            )
        if price is not None:
            form["price"] = round_to_tick(float(price or 0), tick)
        if trigger_price is not None:
            form["trigger_price"] = round_to_tick(float(trigger_price or 0), tick)
        resp, body = self._call("PUT", f"/orders/regular/{order_id}", data=form)
        if resp.status_code != 200 or body.get("status") != "success":
            raise DhanOrderError(
                "Order modify", resp.status_code, str(body.get("message") or "Zerodha refused the change"), body
            )
        return {"orderId": str(order_id), "orderStatus": "TRANSIT", "broker": "zerodha"}

    def get_order_status(self, order_id: str) -> dict:
        if not str(order_id or "").strip():
            return {"orderStatus": "UNKNOWN", "message": "no order id"}
        try:
            history = self._get_data(f"/orders/{order_id}")
        except Exception as exc:
            return {"orderStatus": "UNKNOWN", "message": str(exc)}
        if not isinstance(history, list) or not history:
            return {"orderStatus": "UNKNOWN", "message": "order not found"}
        return self._dhan_order(history[-1])

    @staticmethod
    def _dhan_order(row: dict) -> dict:
        raw = str(row.get("status") or "").upper()
        row = _kite_units(row, "quantity", "filled_quantity")
        return {
            "orderId": str(row.get("order_id") or ""),
            "orderStatus": _STATUS.get(raw, "TRANSIT"),
            "kiteStatus": raw,
            "quantity": int(row.get("quantity") or 0),
            "filledQty": int(row.get("filled_quantity") or 0),
            "averageTradedPrice": float(row.get("average_price") or 0.0),
            "price": float(row.get("price") or 0.0),
            "triggerPrice": float(row.get("trigger_price") or 0.0),
            "transactionType": str(row.get("transaction_type") or ""),
            "tradingSymbol": str(row.get("tradingsymbol") or ""),
            "rejectionReason": str(row.get("status_message") or ""),
            "createTime": str(row.get("order_timestamp") or ""),
            # For the Equity desk's order table; the engines read none of these.
            "orderType": str(row.get("order_type") or ""),
            "productType": str(row.get("product") or ""),
            "exchange": str(row.get("exchange") or ""),
        }

    def get_order_book(self) -> list:
        return [self._dhan_order(r) for r in (self._get_data("/orders") or [])]

    def verify_order_fill(self, order_id: str, max_wait_sec: int = 15, poll_interval: float = 1.0) -> dict:
        start = _time.time()
        last: dict = {}
        while _time.time() - start < max_wait_sec:
            last = self.get_order_status(order_id)
            status = last.get("orderStatus", "UNKNOWN")
            requested = int(last.get("quantity") or 0)
            filled = int(last.get("filledQty") or 0)
            avg = _first_positive_price(last, ("averageTradedPrice",))
            if status == "TRADED" or (requested > 0 and filled >= requested):
                return {
                    "order_id": order_id,
                    "status": "FILLED",
                    "raw_status": last.get("kiteStatus", status),
                    "requested_qty": requested,
                    "filled_qty": filled or requested,
                    "avg_price": avg,
                    "message": "Order filled successfully",
                }
            if status in ("REJECTED", "CANCELLED"):
                return {
                    "order_id": order_id,
                    "status": status,
                    "raw_status": last.get("kiteStatus", status),
                    "requested_qty": requested,
                    "filled_qty": filled,
                    "avg_price": avg,
                    "message": last.get("rejectionReason") or f"Order {status}",
                }
            _time.sleep(poll_interval)
        return {
            "order_id": order_id,
            "status": "TIMEOUT",
            "raw_status": str(last.get("kiteStatus") or last.get("orderStatus") or "UNKNOWN"),
            "requested_qty": int(last.get("quantity") or 0),
            "filled_qty": int(last.get("filledQty") or 0),
            "avg_price": _first_positive_price(last, ("averageTradedPrice",)),
            "message": f"Order not filled within {max_wait_sec}s. Last status: {last.get('kiteStatus', 'UNKNOWN')}",
        }

    # ── account ───────────────────────────────────────────────
    def get_positions(self) -> list:
        data = self._get_data("/portfolio/positions") or {}
        rows = []
        for row in data.get("net") or []:
            exchange = str(row.get("exchange") or "")
            symbol = str(row.get("tradingsymbol") or "")
            row = _kite_units(row, "quantity", "buy_quantity", "sell_quantity")
            rows.append(
                {
                    "securityId": self._dhan_id_for(exchange, symbol),
                    "tradingSymbol": symbol,
                    "exchange": exchange,
                    "netQty": int(row.get("quantity") or 0),
                    "buyAvg": float(row.get("buy_price") or 0.0),
                    "sellAvg": float(row.get("sell_price") or 0.0),
                    "buyQty": int(row.get("buy_quantity") or 0),
                    "sellQty": int(row.get("sell_quantity") or 0),
                    "productType": str(row.get("product") or ""),
                    "realizedProfit": float(row.get("realised") or 0.0),
                    "unrealizedProfit": float(row.get("unrealised") or 0.0),
                    # Kite's own net figure for the position, booked and open together.
                    "dayPnl": float(row.get("pnl") or 0.0),
                }
            )
        return rows

    def get_trades(self, from_date: str = None, to_date: str = None) -> list:
        """Today's fills. Kite keeps only the current day here."""
        rows = []
        for row in self._get_data("/trades") or []:
            exchange = str(row.get("exchange") or "")
            symbol = str(row.get("tradingsymbol") or "")
            row = _kite_units(row, "quantity")
            rows.append(
                {
                    "securityId": self._dhan_id_for(exchange, symbol),
                    "tradingSymbol": symbol,
                    "orderId": str(row.get("order_id") or ""),
                    "transactionType": str(row.get("transaction_type") or ""),
                    "tradedPrice": float(row.get("average_price") or 0.0),
                    "tradedQuantity": int(row.get("quantity") or 0),
                    "exchangeTime": str(row.get("fill_timestamp") or row.get("exchange_timestamp") or ""),
                    # MIS or NRML: one option can carry both on one day.
                    "productType": str(row.get("product") or ""),
                }
            )
        return rows

    def get_funds(self) -> dict:
        data = self._get_data("/user/margins/equity") or {}
        available = data.get("available") or {}
        utilised = data.get("utilised") or {}
        return {
            "availabelBalance": float(data.get("net") or 0.0),
            "sodLimit": float(available.get("opening_balance") or 0.0),
            "utilizedAmount": float(utilised.get("debits") or 0.0),
            # Today's money moved: withdrawals asked for, and money added. Kite
            # keeps no fund history, so these are recorded day by day.
            "payout": float(utilised.get("payout") or 0.0),
            "payin": float(available.get("intraday_payin") or 0.0),
            "broker": "zerodha",
        }

    def get_order_charges(self) -> dict:
        """What today's filled orders cost, from Kite's own contract-note
        calculator: `total` is everything, `brokerage` the broker's part of it."""
        orders = [
            row
            for row in (self._get_data("/orders") or [])
            if str(row.get("status") or "").upper() == "COMPLETE" and int(row.get("filled_quantity") or 0) > 0
        ]
        if not orders:
            return {"total": 0.0, "brokerage": 0.0}
        body = [
            {
                "order_id": str(row.get("order_id") or ""),
                "exchange": str(row.get("exchange") or ""),
                "tradingsymbol": str(row.get("tradingsymbol") or ""),
                "transaction_type": str(row.get("transaction_type") or ""),
                "variety": str(row.get("variety") or "regular"),
                "product": str(row.get("product") or ""),
                "order_type": str(row.get("order_type") or ""),
                "quantity": int(row.get("filled_quantity") or 0),
                "average_price": float(row.get("average_price") or 0.0),
            }
            for row in orders
        ]
        resp, payload = self._call("POST", "/charges/orders", json=body)
        if resp.status_code != 200 or payload.get("status") == "error":
            raise RuntimeError(f"Zerodha /charges/orders failed ({resp.status_code}): {payload.get('message', '')}")
        total = brokerage = 0.0
        for row in payload.get("data") or []:
            charges = row.get("charges") or {}
            total += float(charges.get("total") or 0.0)
            brokerage += float(charges.get("brokerage") or 0.0)
        return {"total": round(total, 2), "brokerage": round(brokerage, 2)}

    # ── prices ────────────────────────────────────────────────
    def get_ltp_prices(self, security_ids: list, exchange_segment: str = "NSE_FNO") -> dict:
        """{dhan_security_id(int): last_price} for option contracts."""
        self._instruments()
        id_to_key = _dhan_id_to_key()
        wanted: dict[str, int] = {}
        for sid in security_ids:
            key = id_to_key.get(str(sid))
            if not key:
                continue
            underlying, strike, expiry, option_type = key.rsplit("_", 3)
            info = KiteInstruments.contract(underlying, strike, expiry, option_type)
            if info:
                wanted[f"{info['exchange']}:{info['tradingsymbol']}"] = int(sid)
        prices: dict[int, float] = {}
        names = list(wanted)
        for start in range(0, len(names), 500):
            data = self._get_data("/quote/ltp", params=[("i", n) for n in names[start : start + 500]]) or {}
            for name, quote in data.items():
                price = float((quote or {}).get("last_price") or 0.0)
                if price > 0 and name in wanted:
                    prices[wanted[name]] = price
        return prices

    def get_option_quote(self, underlying: str, strike: int, expiry: str, option_type: str) -> dict:
        """Last price and the best bid and offer for one contract, from Kite's book.

        {"ltp", "bid", "ask"}; a side with nothing on it is 0, and so is the
        whole answer when Kite cannot be asked.
        """
        try:
            info = self._contract(underlying, strike, expiry, option_type)
            name = f"{info['exchange']}:{info['tradingsymbol']}"
            row = (self._get_data("/quote", params=[("i", name)]) or {}).get(name) or {}
        except Exception as exc:
            log.warning("[ZERODHA] option quote failed: %s", exc)
            return {"ltp": 0.0, "bid": 0.0, "ask": 0.0}
        depth = row.get("depth") or {}

        def best(side: str) -> float:
            for level in depth.get(side) or []:
                price = float((level or {}).get("price") or 0.0)
                if price > 0 and int((level or {}).get("quantity") or 0) > 0:
                    return price
            return 0.0

        return {"ltp": float(row.get("last_price") or 0.0), "bid": best("buy"), "ask": best("sell")}

    def get_option_ltp(self, underlying: str, strike: int, expiry: str, option_type: str) -> float:
        try:
            info = self._contract(underlying, strike, expiry, option_type)
            name = f"{info['exchange']}:{info['tradingsymbol']}"
            data = self._get_data("/quote/ltp", params=[("i", name)]) or {}
            return float((data.get(name) or {}).get("last_price") or 0.0)
        except Exception as exc:
            log.warning("[ZERODHA] option LTP failed: %s", exc)
            return 0.0

    def get_historical_data(
        self,
        security_id: str,
        exchange_segment: str,
        instrument_type: str,
        expiry_code: int = 0,
        from_date: str = None,
        to_date: str = None,
        candle_type: str = "1",
    ) -> pd.DataFrame:
        """Index and option candles, in the frame `DhanClient.get_historical_data`
        returns: naive IST timestamps as the index; open/high/low/close/volume.

        An option is named by Dhan's security id and found in Kite's dump by the
        contract key both share -- the charts fall back here when Dhan's chart
        API refuses (2026-10-23: DH-905 on every request from 08:35)."""
        index = INDEX_TOKENS.get(str(security_id))
        if index:
            token = index[2]
        else:
            self._instruments()
            key = _dhan_id_to_key().get(str(security_id))
            info = KiteInstruments._by_key.get(key) if key else None
            if not info or not info.get("instrument_token"):
                raise RuntimeError(f"Zerodha has no contract for Dhan security {security_id}.")
            token = info["instrument_token"]
        interval = _INTERVALS.get(str(candle_type))
        if not interval:
            raise RuntimeError(f"Zerodha has no {candle_type}-minute candles.")
        # To midnight: MCX trades until 23:30 (23:55 in winter); NSE simply
        # has no bars after 15:30.
        params = {"from": f"{from_date} 09:00:00", "to": f"{to_date} 23:59:59"}
        data = self._get_data(f"/instruments/historical/{token}/{interval}", params=params, timeout=20) or {}
        return candles_frame(data.get("candles") or [])

    # ── async, as the engine calls them ───────────────────────
    async def async_place_option_order(self, *a, **kw) -> dict:
        return await asyncio.to_thread(lambda: self.place_option_order(*a, **kw))

    async def async_place_sl_order(self, *a, **kw) -> dict:
        return await asyncio.to_thread(lambda: self.place_sl_order(*a, **kw))

    async def async_cancel_order(self, order_id: str) -> dict:
        return await asyncio.to_thread(self.cancel_order, order_id)

    async def async_get_positions(self) -> list:
        return await asyncio.to_thread(self.get_positions)

    async def async_get_funds(self) -> dict:
        return await asyncio.to_thread(self.get_funds)

    async def async_get_order_book(self) -> list:
        return await asyncio.to_thread(self.get_order_book)

    async def async_verify_order_fill(self, order_id: str, max_wait_sec: int = 15) -> dict:
        return await asyncio.to_thread(self.verify_order_fill, order_id, max_wait_sec)

    async def async_get_ltp_prices(self, security_ids: list, exchange_segment: str = "NSE_FNO", ttl: float = 3.0):
        return await asyncio.to_thread(self.get_ltp_prices, security_ids, exchange_segment)

    async def async_get_option_ltp(
        self, underlying: str, strike: int, expiry: str, option_type: str, *, ttl: float = 0.0
    ) -> float:
        return await asyncio.to_thread(self.get_option_ltp, underlying, strike, expiry, option_type)

    async def async_get_historical_data(self, *a, **kw) -> pd.DataFrame:
        return await asyncio.to_thread(self.get_historical_data, *a, **kw)


def candles_frame(candles: list) -> pd.DataFrame:
    """Kite's [[ts, o, h, l, c, v], ...] -> Dhan's frame (naive IST index)."""
    columns = ["open", "high", "low", "close", "volume"]
    if not candles:
        return pd.DataFrame(columns=columns, index=pd.DatetimeIndex([], name="timestamp"))
    stamps = pd.to_datetime([c[0] for c in candles], utc=True).tz_convert(IST).tz_localize(None)
    frame = pd.DataFrame(
        {
            "open": [float(c[1]) for c in candles],
            "high": [float(c[2]) for c in candles],
            "low": [float(c[3]) for c in candles],
            "close": [float(c[4]) for c in candles],
            "volume": [int(c[5]) if len(c) > 5 else 0 for c in candles],
        },
        index=pd.DatetimeIndex(stamps, name="timestamp"),
    )
    return frame.sort_index()
