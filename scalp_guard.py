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

A LIMIT CANNOT BE LOOSENED IN THE MIDDLE OF A DAY. Tightening applies at once;
loosening (a bigger number, zero, or switching the guard off) waits for the
next day. A limit he can lift while he is losing is not a limit.

No imports from the app: the route and the tests both read it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

LIMITS = ("max_trades", "max_daily_loss", "max_lots", "max_stop_points", "cooldown_sec")

# What the desk starts with. He changes them from the Scalp page.
DEFAULTS: dict = {
    "enabled": True,
    "max_trades": 10,
    "max_daily_loss": 5000.0,
    "max_lots": 2,
    "max_stop_points": 20.0,
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


def _looser(key: str, old, new) -> bool:
    """True when `new` lets him do more than `old` did."""
    if key == "enabled":
        return bool(old) and not bool(new)
    old, new = float(old or 0), float(new or 0)
    if old <= 0:  # it was off: nothing is looser than no limit
        return False
    return new <= 0 or new > old


def effective(state: dict | None, today: date) -> dict:
    """The limits in force today: a pending change takes over on its day."""
    state = state if isinstance(state, dict) else {}
    active = _clean(state.get("active"))
    pending = state.get("pending")
    if isinstance(pending, dict) and str(state.get("pending_from") or "9999") <= today.isoformat():
        active = _clean({**active, **pending})
    return active


def settle(state: dict | None, today: date) -> dict:
    """The stored state with a pending change that has come due folded in."""
    state = state if isinstance(state, dict) else {}
    pending = state.get("pending")
    if isinstance(pending, dict) and str(state.get("pending_from") or "9999") <= today.isoformat():
        return {"active": effective(state, today), "pending": None, "pending_from": ""}
    return {
        "active": _clean(state.get("active")),
        "pending": pending if isinstance(pending, dict) and pending else None,
        "pending_from": str(state.get("pending_from") or "") if pending else "",
    }


def apply_change(state: dict | None, wanted: dict, today: date) -> dict:
    """Save new limits: the tighter ones now, the looser ones from tomorrow."""
    state = settle(state, today)
    active = dict(state["active"])
    wanted = _clean({**active, **(wanted if isinstance(wanted, dict) else {})})
    pending = dict(state.get("pending") or {})
    for key in ("enabled", *LIMITS):
        if wanted[key] == active[key]:
            pending.pop(key, None)  # asked back to what it is: nothing waits
        elif _looser(key, active[key], wanted[key]):
            pending[key] = wanted[key]
        else:
            active[key] = wanted[key]
            pending.pop(key, None)
    return {
        "active": active,
        "pending": pending or None,
        "pending_from": (today + timedelta(days=1)).isoformat() if pending else "",
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
    cap = float(limits.get("max_stop_points") or 0) if limits.get("enabled") else 0.0
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


def may_move_stop(limits: dict, *, entry: float, new_stop: float, old_stop: float, buying: bool) -> str:
    """Why a stop may not be moved there, or "". Closer is always allowed."""
    cap = float(limits.get("max_stop_points") or 0) if limits.get("enabled") else 0.0
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
