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
    developer console -- the same 13.205.229.208 Dhan uses.
  * `tag` is at most 20 characters.

This file never logs a token, a secret or a request_token.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import logging
import re
import threading
import time as _time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from broker.dhan import (
    AmbiguousOrderSubmission,
    DhanOrderError,
    ScripMaster,
    _first_positive_price,
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

    Loaded once a day from Kite's instrument dump (NFO and BFO). The key
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
            key = f"{name}_{_strike_key(strike)}_{expiry}_{row['instrument_type']}"
            exchange = str(row.get("exchange") or "NFO")
            info = {
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
        for exchange in ("NFO", "BFO"):
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


class ZerodhaClient:
    """Kite Connect, answering the calls `engine/live.py` makes of `DhanClient`."""

    broker_name = "Zerodha"

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

    def _call(self, method: str, path: str, *, params=None, data=None, timeout: float = 10.0):
        if not self._is_configured():
            raise ConnectionError("Zerodha is not connected. Log in to Zerodha from Settings first.")
        resp = self._http.request(
            method, f"{KITE_API}{path}", headers=self.headers, params=params, data=data, timeout=timeout
        )
        body = _json(resp)
        if resp.status_code == 403 and body.get("error_type") == "TokenException":
            raise ConnectionError("Zerodha session has expired. Log in to Zerodha again (it lapses daily at 6 AM).")
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
            raise DhanOrderError(
                "Order placement", resp.status_code, str(body.get("message") or "Zerodha rejected the order"), body
            )
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

    def cancel_order(self, order_id: str) -> dict:
        resp, body = self._call("DELETE", f"/orders/regular/{order_id}")
        if resp.status_code != 200 or body.get("status") != "success":
            return {"orderId": str(order_id), "orderStatus": "", "message": body.get("message", "")}
        # Kite's DELETE only says the request was taken; the engine then asks
        # get_order_status, exactly as it does with Dhan.
        return {"orderId": str(order_id), "orderStatus": ""}

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
                }
            )
        return rows

    def get_trades(self, from_date: str = None, to_date: str = None) -> list:
        """Today's fills. Kite keeps only the current day here."""
        rows = []
        for row in self._get_data("/trades") or []:
            exchange = str(row.get("exchange") or "")
            symbol = str(row.get("tradingsymbol") or "")
            rows.append(
                {
                    "securityId": self._dhan_id_for(exchange, symbol),
                    "tradingSymbol": symbol,
                    "orderId": str(row.get("order_id") or ""),
                    "transactionType": str(row.get("transaction_type") or ""),
                    "tradedPrice": float(row.get("average_price") or 0.0),
                    "tradedQuantity": int(row.get("quantity") or 0),
                    "exchangeTime": str(row.get("fill_timestamp") or row.get("exchange_timestamp") or ""),
                }
            )
        return rows

    def get_funds(self) -> dict:
        data = self._get_data("/user/margins/equity") or {}
        available = data.get("available") or {}
        return {
            "availabelBalance": float(data.get("net") or 0.0),
            "sodLimit": float(available.get("opening_balance") or 0.0),
            "utilizedAmount": float((data.get("utilised") or {}).get("debits") or 0.0),
            "broker": "zerodha",
        }

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
        """Index candles, in the frame `DhanClient.get_historical_data` returns:
        naive IST timestamps as the index; open/high/low/close/volume."""
        index = INDEX_TOKENS.get(str(security_id))
        if not index:
            raise RuntimeError(f"Zerodha candles are wired for the index books only, not security {security_id}.")
        interval = _INTERVALS.get(str(candle_type))
        if not interval:
            raise RuntimeError(f"Zerodha has no {candle_type}-minute candles.")
        params = {"from": f"{from_date} 09:00:00", "to": f"{to_date} 15:35:00"}
        data = self._get_data(f"/instruments/historical/{index[2]}/{interval}", params=params, timeout=20) or {}
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
