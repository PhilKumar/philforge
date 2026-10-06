"""The Option Builder's server side: the chain, the margin, and the basket.

The page builds a multi-leg option strategy (like Sensibull's or Dhan's own
strategy builder) on live data from the broker. Everything the payoff chart
draws is computed in the browser from what this module serves, so the chart
can move with a slider without a round trip; what has to be TRUE -- prices,
greeks, open interest, margin, the orders -- comes from Dhan here.

DHAN'S OPTION-CHAIN LIMIT is one unique request every 3 seconds for the whole
account, and the account's budget is shared with every running strategy
(see the Dhan rate-budget note). So:
  * every caller goes through `ChainCache.get`, which answers from a cache that
    is at most CHAIN_TTL_SEC old in session and OFF_SESSION_TTL_SEC outside;
  * concurrent askers for the same chain wait for ONE request (single flight);
  * all chain requests leave at least CHAIN_MIN_GAP_SEC apart.
"""

from __future__ import annotations

import math
import threading
import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from broker.dhan import round_to_tick

IST = timezone(timedelta(hours=5, minutes=30))

# Dhan index scrip ids (the same ids app.py's rolling-option code uses).
UNDERLYINGS: dict[str, dict[str, Any]] = {
    "NIFTY": {"scrip": 13, "seg": "IDX_I", "fno": "NSE_FNO", "label": "NIFTY 50"},
    "BANKNIFTY": {"scrip": 25, "seg": "IDX_I", "fno": "NSE_FNO", "label": "BANK NIFTY"},
    "FINNIFTY": {"scrip": 27, "seg": "IDX_I", "fno": "NSE_FNO", "label": "FIN NIFTY"},
    "MIDCPNIFTY": {"scrip": 442, "seg": "IDX_I", "fno": "NSE_FNO", "label": "MIDCAP NIFTY"},
    "SENSEX": {"scrip": 51, "seg": "IDX_I", "fno": "BSE_FNO", "label": "SENSEX"},
}

CHAIN_TTL_SEC = 3.0
OFF_SESSION_TTL_SEC = 60.0
CHAIN_MIN_GAP_SEC = 3.05
EXPIRY_TTL_SEC = 15 * 60.0

MAX_LEGS = 10
MAX_LOTS_PER_LEG = 50

# An aggressive LIMIT, not a MARKET: Dhan turns an F&O MARKET order into a
# LIMIT with a poor buffer and SELLs then hang unfilled (Scalp learnt this).
# The order is priced through the touch so it fills now, at the touch.
ENTRY_SLIP = 0.02
ENTRY_SLIP_NO_QUOTE = 0.05


class OptionBuilderError(ValueError):
    """A request the builder refuses, in words a trader reads."""


def normalize_underlying(name: str) -> str:
    key = str(name or "").strip().upper()
    if key not in UNDERLYINGS:
        raise OptionBuilderError(f"{name or 'That underlying'} is not offered. Pick one of {', '.join(UNDERLYINGS)}.")
    return key


def nse_session_open(now: datetime | None = None) -> bool:
    moment = now or datetime.now(IST)
    if moment.weekday() >= 5:
        return False
    minutes = moment.hour * 60 + moment.minute
    return 9 * 60 <= minutes <= 15 * 60 + 35


# ── the chain ───────────────────────────────────────────────────────────────
def _num(value, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _side(raw: dict | None) -> dict | None:
    if not isinstance(raw, dict) or not raw:
        return None
    greeks = raw.get("greeks") or {}
    oi = int(_num(raw.get("oi")))
    prev_oi = int(_num(raw.get("previous_oi"), oi))
    return {
        "security_id": str(raw.get("security_id") or ""),
        "ltp": _num(raw.get("last_price")),
        "prev_close": _num(raw.get("previous_close_price")),
        "bid": _num(raw.get("top_bid_price")),
        "ask": _num(raw.get("top_ask_price")),
        "iv": _num(raw.get("implied_volatility")),
        "oi": oi,
        "oi_change": oi - prev_oi,
        "volume": int(_num(raw.get("volume"))),
        "delta": _num(greeks.get("delta")),
        "gamma": _num(greeks.get("gamma")),
        "theta": _num(greeks.get("theta")),
        "vega": _num(greeks.get("vega")),
    }


def max_pain(rows: list[dict]) -> float:
    """The strike at which option writers pay out least at expiry."""
    strikes = [r["strike"] for r in rows]
    best, best_cost = 0.0, None
    for settle in strikes:
        cost = 0.0
        for r in rows:
            k = r["strike"]
            if r.get("ce"):
                cost += max(0.0, settle - k) * r["ce"]["oi"]
            if r.get("pe"):
                cost += max(0.0, k - settle) * r["pe"]["oi"]
        if best_cost is None or cost < best_cost:
            best, best_cost = settle, cost
    return best


def shape_chain(raw: dict, underlying: str, expiry: str, lot_size: int = 0) -> dict:
    """Dhan's chain as the page reads it: rows by strike, ATM and summary."""
    spot = _num((raw or {}).get("last_price"))
    rows = []
    for key, pair in ((raw or {}).get("oc") or {}).items():
        strike = _num(key, -1)
        if strike <= 0 or not isinstance(pair, dict):
            continue
        ce, pe = _side(pair.get("ce")), _side(pair.get("pe"))
        if not ce and not pe:
            continue
        rows.append({"strike": strike, "ce": ce, "pe": pe})
    rows.sort(key=lambda r: r["strike"])
    atm = min(rows, key=lambda r: abs(r["strike"] - spot))["strike"] if rows and spot > 0 else 0.0
    ce_oi = sum(r["ce"]["oi"] for r in rows if r["ce"])
    pe_oi = sum(r["pe"]["oi"] for r in rows if r["pe"])
    atm_row = next((r for r in rows if r["strike"] == atm), None)
    ivs = [s["iv"] for s in ((atm_row or {}).get("ce"), (atm_row or {}).get("pe")) if s and s["iv"] > 0]
    gaps = [b["strike"] - a["strike"] for a, b in zip(rows, rows[1:])]
    step = max(set(gaps), key=gaps.count) if gaps else 0.0
    return {
        "underlying": underlying,
        "label": UNDERLYINGS.get(underlying, {}).get("label", underlying),
        "expiry": expiry,
        "spot": spot,
        "atm": atm,
        "atm_iv": round(sum(ivs) / len(ivs), 2) if ivs else 0.0,
        "pcr": round(pe_oi / ce_oi, 2) if ce_oi else 0.0,
        "max_pain": max_pain(rows) if rows else 0.0,
        "strike_step": step,
        "lot_size": int(lot_size or 0),
        "segment": UNDERLYINGS.get(underlying, {}).get("fno", "NSE_FNO"),
        "rows": rows,
    }


class ChainCache:
    """Option chains, at most one Dhan request per chain per TTL, ≥3s apart."""

    def __init__(
        self,
        clock: Callable[[], float] = _time.monotonic,
        sleep: Callable[[float], None] = _time.sleep,
        session_open: Callable[[], bool] = nse_session_open,
    ):
        self._clock, self._sleep, self._session_open = clock, sleep, session_open
        self._lock = threading.Lock()
        self._gap_lock = threading.Lock()
        self._key_locks: dict[tuple, threading.Lock] = {}
        self._chains: dict[tuple, tuple[float, dict]] = {}
        self._expiries: dict[str, tuple[float, list]] = {}
        self._last_call = -1e9

    def _ttl(self) -> float:
        return CHAIN_TTL_SEC if self._session_open() else OFF_SESSION_TTL_SEC

    def _paced(self, fn: Callable[[], Any]) -> Any:
        with self._gap_lock:
            wait = CHAIN_MIN_GAP_SEC - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            try:
                return fn()
            finally:
                self._last_call = self._clock()

    def get(self, client, underlying: str, expiry: str, lot_size: int = 0) -> dict:
        underlying = normalize_underlying(underlying)
        key = (underlying, expiry)
        with self._lock:
            lock = self._key_locks.setdefault(key, threading.Lock())
        with lock:
            hit = self._chains.get(key)
            if hit and self._clock() - hit[0] < self._ttl():
                return hit[1]
            spec = UNDERLYINGS[underlying]
            try:
                raw = self._paced(lambda: client.get_option_chain(spec["scrip"], spec["seg"], expiry))
            except Exception:
                if hit:  # a stale chain, labelled as such, beats an empty page
                    return {**hit[1], "stale": True}
                raise
            shaped = shape_chain(raw, underlying, expiry, lot_size)
            shaped["fetched_at"] = datetime.now(IST).isoformat(timespec="seconds")
            self._chains[key] = (self._clock(), shaped)
            return shaped

    def expiries(self, client, underlying: str) -> list:
        underlying = normalize_underlying(underlying)
        hit = self._expiries.get(underlying)
        if hit and self._clock() - hit[0] < EXPIRY_TTL_SEC:
            return hit[1]
        spec = UNDERLYINGS[underlying]
        found = self._paced(lambda: client.get_option_chain_expiries(spec["scrip"], spec["seg"]))
        today = datetime.now(IST).date().isoformat()
        found = sorted(e for e in found if e >= today)
        self._expiries[underlying] = (self._clock(), found)
        return found


# ── legs ────────────────────────────────────────────────────────────────────
def normalize_leg(leg: dict) -> dict:
    """One leg as the server trusts it, or a plain refusal."""
    try:
        underlying = normalize_underlying(leg.get("underlying"))
        strike = float(leg.get("strike"))
        expiry = str(leg.get("expiry") or "")
        date.fromisoformat(expiry)
        option_type = str(leg.get("option_type") or "").upper()
        side = str(leg.get("side") or "").upper()
        lots = int(leg.get("lots") or 0)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, OptionBuilderError):
            raise
        raise OptionBuilderError("A leg is missing its strike, expiry, type, side or lots.") from exc
    if option_type not in ("CE", "PE"):
        raise OptionBuilderError("Each leg must be a CE or a PE.")
    if side not in ("BUY", "SELL"):
        raise OptionBuilderError("Each leg must be a BUY or a SELL.")
    if strike <= 0:
        raise OptionBuilderError("A strike must be above zero.")
    if not 1 <= lots <= MAX_LOTS_PER_LEG:
        raise OptionBuilderError(f"Lots per leg must be between 1 and {MAX_LOTS_PER_LEG}.")
    return {
        "underlying": underlying,
        "strike": strike,
        "expiry": expiry,
        "option_type": option_type,
        "side": side,
        "lots": lots,
        "security_id": str(leg.get("security_id") or ""),
        "price": _num(leg.get("price")),
    }


def normalize_legs(legs: Any) -> list[dict]:
    if not isinstance(legs, list) or not legs:
        raise OptionBuilderError("Add at least one leg first.")
    if len(legs) > MAX_LEGS:
        raise OptionBuilderError(f"A strategy can have at most {MAX_LEGS} legs.")
    out = [normalize_leg(leg) for leg in legs]
    if len({leg["underlying"] for leg in out}) > 1:
        raise OptionBuilderError("Every leg must be on the same underlying.")
    return out


def margin_scrip_list(legs: list[dict], lot_size: int, product: str) -> list[dict]:
    product_type = "MARGIN" if str(product).upper() in ("MARGIN", "NRML") else "INTRADAY"
    return [
        {
            "exchangeSegment": UNDERLYINGS[leg["underlying"]]["fno"],
            "transactionType": leg["side"],
            "quantity": int(leg["lots"]) * int(lot_size),
            "productType": product_type,
            "securityId": leg["security_id"],
            "price": round(leg["price"], 2),
            "triggerPrice": 0,
        }
        for leg in legs
    ]


def shape_margin(raw: dict) -> dict:
    raw = raw or {}

    def pick(*names):
        for name in names:
            if name in raw and raw[name] not in (None, ""):
                return _num(raw[name])
        return 0.0

    return {
        "total": pick("total_margin", "totalMargin"),
        "span": pick("span_margin", "spanMargin"),
        "exposure": pick("exposure_margin", "exposureMargin"),
        "hedge_benefit": pick("hedge_benefit", "hedgeBenefit"),
    }


def entry_price(leg: dict, quote: dict | None) -> float:
    """The aggressive limit that fills a leg now (through the touch)."""
    quote = quote or {}
    ltp, bid, ask = _num(quote.get("ltp")), _num(quote.get("bid")), _num(quote.get("ask"))
    if leg["side"] == "BUY":
        price = ask * (1 + ENTRY_SLIP) if ask > 0 else ltp * (1 + ENTRY_SLIP_NO_QUOTE)
    else:
        base = bid if bid > 0 else ltp
        slip = ENTRY_SLIP if bid > 0 else ENTRY_SLIP_NO_QUOTE
        price = base * (1 - slip)
    return round_to_tick(max(0.05, price))


def basket_order(legs: list[dict]) -> list[dict]:
    """BUYS FIRST. A spread's short legs are hedged by its long ones; sent the
    other way round a SELL fills naked and draws full margin (or is refused),
    and if a later BUY then fails the account is left short and unhedged."""
    return [leg for leg in legs if leg["side"] == "BUY"] + [leg for leg in legs if leg["side"] == "SELL"]


def quote_for(chain: dict | None, strike: float, option_type: str) -> dict | None:
    for row in (chain or {}).get("rows") or []:
        if abs(row["strike"] - strike) < 1e-6:
            return row.get(option_type.lower())
    return None


# ── positions ───────────────────────────────────────────────────────────────
def legs_from_dhan_positions(rows: list[dict]) -> list[dict]:
    """Open option positions as builder legs, at their own average price."""
    legs = []
    for row in rows or []:
        if str(row.get("exchangeSegment") or "") not in ("NSE_FNO", "BSE_FNO"):
            continue
        kind = str(row.get("drvOptionType") or "").upper()
        option_type = {"CALL": "CE", "PUT": "PE", "CE": "CE", "PE": "PE"}.get(kind)
        net = int(_num(row.get("netQty")))
        if not option_type or net == 0:
            continue
        underlying = str(row.get("tradingSymbol") or "").split("-")[0].upper()
        if underlying not in UNDERLYINGS:
            continue
        legs.append(
            {
                "underlying": underlying,
                "strike": _num(row.get("drvStrikePrice")),
                "expiry": str(row.get("drvExpiryDate") or "")[:10],
                "option_type": option_type,
                "side": "BUY" if net > 0 else "SELL",
                "quantity": abs(net),
                "price": _num(row.get("buyAvg") if net > 0 else row.get("sellAvg")) or _num(row.get("costPrice")),
                "security_id": str(row.get("securityId") or ""),
                "broker": "dhan",
                "product": str(row.get("productType") or ""),
            }
        )
    return legs


def legs_from_zerodha_positions(rows: list[dict], key_for_symbol: Callable[[str, str], str | None]) -> list[dict]:
    legs = []
    for row in rows or []:
        net = int(_num(row.get("netQty")))
        if net == 0:
            continue
        key = key_for_symbol(str(row.get("exchange") or ""), str(row.get("tradingSymbol") or ""))
        if not key:
            continue
        try:
            underlying, strike, expiry, option_type = key.rsplit("_", 3)
        except ValueError:
            continue
        if underlying not in UNDERLYINGS or option_type not in ("CE", "PE"):
            continue
        legs.append(
            {
                "underlying": underlying,
                "strike": _num(strike),
                "expiry": expiry,
                "option_type": option_type,
                "side": "BUY" if net > 0 else "SELL",
                "quantity": abs(net),
                "price": _num(row.get("buyAvg") if net > 0 else row.get("sellAvg")),
                "security_id": str(row.get("securityId") or ""),
                "broker": "zerodha",
                "product": str(row.get("productType") or ""),
            }
        )
    return legs


# ── paper baskets ───────────────────────────────────────────────────────────
def leg_pnl(leg: dict, ltp: float, lot_size: int) -> float:
    sign = 1 if leg["side"] == "BUY" else -1
    return sign * (ltp - leg["entry"]) * leg["lots"] * lot_size


def new_paper_basket(basket_id: int, name: str, legs: list[dict], lot_size: int, now: datetime) -> dict:
    priced = []
    for leg in legs:
        if leg["price"] <= 0:
            raise OptionBuilderError(
                f"{leg['strike']:g} {leg['option_type']} has no price yet -- wait for the chain to load."
            )
        priced.append({**leg, "entry": round(leg["price"], 2)})
    return {
        "id": basket_id,
        "name": (str(name or "").strip() or "Strategy")[:60],
        "underlying": legs[0]["underlying"],
        "lot_size": int(lot_size),
        "legs": priced,
        "status": "open",
        "opened_at": now.isoformat(timespec="seconds"),
        "closed_at": None,
        "realised": None,
    }


def close_paper_basket(basket: dict, prices: dict[tuple, float], now: datetime) -> dict:
    """Close at the given LTPs ({(strike, type, expiry): ltp}); refuse a guess."""
    total = 0.0
    legs = []
    for leg in basket["legs"]:
        ltp = prices.get((leg["strike"], leg["option_type"], leg["expiry"]))
        if not ltp or ltp <= 0:
            raise OptionBuilderError(
                f"No price for {leg['strike']:g} {leg['option_type']} {leg['expiry']} -- try again in a moment."
            )
        pnl = leg_pnl(leg, ltp, basket["lot_size"])
        total += pnl
        legs.append({**leg, "exit": round(ltp, 2), "pnl": round(pnl, 2)})
    return {
        **basket,
        "legs": legs,
        "status": "closed",
        "closed_at": now.isoformat(timespec="seconds"),
        "realised": round(total, 2),
    }
