"""The Scalp guard: limits the desk holds him to on a live day.

Phil, 2026-11-10, the day after 86 live scalps and Rs 17,131 lost: "do both"
-- a simpler desk, and protection. The review of that day found no fault in
his direction (45 wins, 41 losses) and every fault in the management: one
trade every three minutes, losses larger than wins, stops 100 points away,
size raised after losing, and a Rs 6,110 lead given back with nothing to stop
the day. Each limit here answers one of those:

    max_trades          entries in a day
    max_daily_loss      rupees lost (booked + open) after which no new entry
    max_lots            lots on one entry
    max_stop_points     the furthest a stop may sit from the entry
    cooldown_sec        the wait after a losing exit

Zero switches a limit off. LIVE entries only: paper and practice are free.

Every change he saves applies at once (2026-11-12; a first build made looser
limits wait a day, and it locked him out of his own settings).

No imports from the app: the route and the tests both read it.
"""

from __future__ import annotations

from datetime import date, datetime

LIMITS = ("max_trades", "max_daily_loss", "max_lots", "max_stop_points", "max_stop_points_sensex", "cooldown_sec")

# SENSEX runs about 3.3 times NIFTY in points, and its options move with it: a
# 20-point stop that suits a NIFTY option stopped every SENSEX trade (Phil,
# 2026-11-12: "This is ridiculous for SENSEX.. 20 points is very small"). Left
# at 0, SENSEX's limit is this many times the NIFTY one.
SENSEX_STOP_SCALE = 3.0

# What the desk starts with. He changes them from the Scalp page.
DEFAULTS: dict = {
    "enabled": True,
    "max_trades": 10,
    "max_daily_loss": 5000.0,
    "max_lots": 2,
    "max_stop_points": 20.0,
    "max_stop_points_sensex": 0.0,
    "cooldown_sec": 120,
}


def _clean(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    if "enabled" in raw:
        out["enabled"] = bool(raw["enabled"])
    for key in LIMITS:
        if key not in raw:
            continue
        try:
            value = float(raw[key] or 0)
        except (TypeError, ValueError):
            continue
        value = max(0.0, value)
        out[key] = int(value) if key in ("max_trades", "max_lots", "cooldown_sec") else round(value, 2)
    return out


def stop_cap(limits: dict, underlying: str = "") -> float:
    """How far from the entry a stop may sit, in points, on this underlying."""
    base = float(limits.get("max_stop_points") or 0)
    if str(underlying or "").upper() == "SENSEX":
        own = float(limits.get("max_stop_points_sensex") or 0)
        return own if own > 0 else round(base * SENSEX_STOP_SCALE, 2)
    return base


def effective(state: dict | None, today: date) -> dict:
    """The limits in force. A change saved under the old next-day rule counts now."""
    return settle(state, today)["active"]


def settle(state: dict | None, today: date) -> dict:
    """The stored state, with anything left waiting from the old rule folded in."""
    state = state if isinstance(state, dict) else {}
    active = _clean(state.get("active"))
    pending = state.get("pending")
    if isinstance(pending, dict) and pending:
        active = _clean({**active, **pending})
    return {"active": active, "pending": None, "pending_from": ""}


def apply_change(state: dict | None, wanted: dict, today: date) -> dict:
    """Save new limits. EVERY change applies at once (Phil, 2026-11-12: "I
    can't even change the guard"). The first build held a looser limit back to
    the next day; that was my idea, not his, and it locked him out of his own
    settings mid-session. They are his limits."""
    active = settle(state, today)["active"]
    return {
        "active": _clean({**active, **(wanted if isinstance(wanted, dict) else {})}),
        "pending": None,
        "pending_from": "",
    }


def day_stats(closed: list[dict], open_trades: list[dict], today: date) -> dict:
    """Today's LIVE scalps: entries made, rupees booked and open, the last loss."""
    day = today.isoformat()
    seen: set = set()
    trades = 0
    booked = 0.0
    last_loss_at = ""
    for t in closed or []:
        if not isinstance(t, dict) or str(t.get("mode") or "live") != "live":
            continue
        if not str(t.get("entry_time") or "").startswith(day):
            continue
        if str(t.get("exit_reason") or "") == "cancelled":
            continue  # an order that never filled is not a trade taken
        key = ("id", t.get("trade_id"))
        if key in seen:
            continue
        seen.add(key)
        pair = str(t.get("pair_id") or "")
        if not pair or ("pair", pair) not in seen:
            trades += 1
            if pair:
                seen.add(("pair", pair))
        pnl = float(t.get("pnl") or 0)
        booked += pnl
        if pnl < 0:
            last_loss_at = max(last_loss_at, str(t.get("exit_time") or ""))
    open_pnl = 0.0
    for t in open_trades or []:
        if not isinstance(t, dict) or str(t.get("mode") or "live") != "live":
            continue
        key = ("id", t.get("trade_id"))
        if key in seen:
            continue
        seen.add(key)
        pair = str(t.get("pair_id") or "")
        if not pair or ("pair", pair) not in seen:
            trades += 1
            if pair:
                seen.add(("pair", pair))
        if str(t.get("status") or "") != "pending":
            open_pnl += float(t.get("pnl") or 0)
    return {
        "trades": trades,
        "booked": round(booked, 2),
        "open_pnl": round(open_pnl, 2),
        "pnl": round(booked + open_pnl, 2),
        "last_loss_at": last_loss_at,
    }


def _money(value: float) -> str:
    return f"Rs {abs(float(value)):,.0f}"


def refusal(limits: dict, stats: dict, lots: int, now: datetime) -> str:
    """Why this live entry is refused, or "" when it may go."""
    if not limits.get("enabled"):
        return ""
    cap = float(limits.get("max_daily_loss") or 0)
    if cap > 0 and float(stats.get("pnl") or 0) <= -cap:
        return (
            f"Guard: today's loss is {_money(stats['pnl'])}, at your limit of {_money(cap)}. "
            "No more live trades today. Practice and paper stay open."
        )
    most = int(limits.get("max_trades") or 0)
    if most > 0 and int(stats.get("trades") or 0) >= most:
        return f"Guard: {stats['trades']} live trades taken today, your limit is {most}. No more live trades today."
    max_lots = int(limits.get("max_lots") or 0)
    if max_lots > 0 and int(lots or 0) > max_lots:
        return f"Guard: {lots} lots is over your limit of {max_lots} per trade."
    wait = int(limits.get("cooldown_sec") or 0)
    last = str(stats.get("last_loss_at") or "")
    if wait > 0 and last:
        try:
            lost_at = datetime.fromisoformat(last)
            if lost_at.tzinfo is None and now.tzinfo is not None:
                lost_at = lost_at.replace(tzinfo=now.tzinfo)
            left = wait - (now - lost_at).total_seconds()
        except (TypeError, ValueError):
            left = 0
        if left > 0:
            return f"Guard: wait {int(left) + 1}s after a loss before the next trade."
    return ""


def clamp_stop(
    limits: dict,
    *,
    underlying: str = "",
    sl_points: float,
    sl_premium: float,
    sl_pct: float,
    sl_rupees: float,
    quantity: int,
    price: float,
    buying: bool,
) -> dict:
    """The entry's stop fields, brought inside max_stop_points.

    A stop already inside the limit is left exactly as he set it. A wider one,
    a blank, or one that cannot be measured becomes max_stop_points from the
    entry. Returns the four fields plus `note` ("" when nothing changed).
    """
    out = {
        "sl_points": float(sl_points or 0),
        "sl_premium": float(sl_premium or 0),
        "sl_pct": float(sl_pct or 0),
        "sl_rupees": float(sl_rupees or 0),
        "note": "",
    }
    cap = stop_cap(limits, underlying) if limits.get("enabled") else 0.0
    if cap <= 0:
        return out
    quantity = max(int(quantity or 0), 1)
    price = float(price or 0)
    if out["sl_points"] > 0:
        distance = out["sl_points"]
    elif out["sl_premium"] > 0 and price > 0:
        distance = (price - out["sl_premium"]) if buying else (out["sl_premium"] - price)
        distance = distance if distance > 0 else cap + 1  # the wrong side of the price is no stop
    elif out["sl_rupees"] > 0:
        distance = out["sl_rupees"] / quantity
    elif out["sl_pct"] > 0 and price > 0:
        distance = price * out["sl_pct"] / 100.0
    else:
        distance = 0.0  # blank, or not measurable without a price
    if 0 < distance <= cap + 1e-9:
        return out
    was = f"{distance:g} points" if distance > 0 else "not set"
    out.update(sl_points=cap, sl_premium=0.0, sl_pct=0.0, sl_rupees=0.0)
    out["note"] = f"Guard: stop set {cap:g} points from the entry (it was {was}; your limit is {cap:g})."
    return out


def may_move_stop(
    limits: dict, *, underlying: str = "", entry: float, new_stop: float, old_stop: float, buying: bool
) -> str:
    """Why a stop may not be moved there, or "". Closer is always allowed."""
    cap = stop_cap(limits, underlying) if limits.get("enabled") else 0.0
    entry, new_stop, old_stop = float(entry or 0), float(new_stop or 0), float(old_stop or 0)
    if cap <= 0 or entry <= 0:
        return ""
    if new_stop <= 0:
        return f"Guard: a live trade keeps its stop. It can sit up to {cap:g} points from the entry."
    distance = (entry - new_stop) if buying else (new_stop - entry)
    if distance <= cap + 1e-9:
        return ""
    old_distance = (entry - old_stop) if buying else (old_stop - entry)
    if old_stop > 0 and distance <= old_distance + 1e-9:
        return ""  # still too wide, but he is bringing it in
    return f"Guard: that stop is {distance:g} points from the entry; your limit is {cap:g}."
