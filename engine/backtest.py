"""
engine/backtest.py — PhilForge Backtest Engine v3
- Accurate NIFTY lot sizes (75 before Jan 2026, 65 from Jan 2026)
- Entry earliest at 09:20 (skip only first candle for warmup)
- P&L starts from 0 (not initial capital)
- Strike computed as nearest 50 for NIFTY, nearest 100 for BANKNIFTY
- Day of week / Time of day indicators
"""

import math
import os
import sys
from collections import deque
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from engine.indicators import compute_dynamic_indicators, infer_execution_timeframe, normalize_strategy_indicators
from engine.strike_utils import round_to_nearest_step


def _dte_weekly(d):
    """Estimate calendar days to next Thursday (weekly expiry) for premium calc."""
    wd = d.weekday()  # Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    if wd <= 3:  # Mon-Thu: this Thursday
        dte = 3 - wd
    else:  # Fri-Sun: next Thursday
        dte = 3 + 7 - wd
    return max(0.5, dte)  # at least 0.5 to avoid edge cases on expiry day


# ── Lot Size Lookup (accurate) ────────────────────────────────────
LOT_SIZES = {
    # NIFTY's 2024 steps are ground truth from Upstox's real listed expired
    # contract chains (data/nse_contract_rules.json): lot 25 through the
    # 26-Dec-2024 expiry, 75 from 02-Jan-2025, 65 from 06-Jan-2026. The table
    # previously ran 50 straight through to Nov-2024, which sized every
    # Apr-Dec 2024 backtest at twice the real contract.
    #
    # The 75 -> 50 cut runs the other way and is older: NSE revised NIFTY from
    # 75 to 50 for weekly contracts from August 2021 (monthlies from the July
    # 2021 expiry). The table used to open at 50 in the year 2000, which sized
    # every trade before August 2021 at two thirds of the real contract.
    "NIFTY": [
        (date(2026, 1, 1), 65),
        (date(2025, 1, 1), 75),
        (date(2024, 4, 26), 25),
        (date(2021, 8, 1), 50),
        (date(2000, 1, 1), 75),
    ],
    "BANKNIFTY": [(date(2026, 1, 1), 30), (date(2024, 11, 20), 30), (date(2000, 1, 1), 25)],
    "FINNIFTY": [(date(2026, 1, 1), 65), (date(2024, 11, 20), 65), (date(2000, 1, 1), 40)],
    "MIDCPNIFTY": [(date(2026, 1, 1), 50), (date(2024, 11, 20), 75), (date(2000, 1, 1), 75)],
    "SENSEX": [(date(2026, 1, 1), 20), (date(2024, 11, 20), 20), (date(2000, 1, 1), 10)],
}

SELL_OPTION_MARGIN_PER_LOT = {
    "NIFTY": 100000.0,
    "BANKNIFTY": 150000.0,
    "FINNIFTY": 85000.0,
    "MIDCPNIFTY": 80000.0,
    "SENSEX": 75000.0,
}


def _instrument_family(instrument):
    token = str(instrument).upper()
    if "26009" in token or "BANK" in token:
        return "BANKNIFTY"
    if "26017" in token or "FIN" in token:
        return "FINNIFTY"
    if "26037" in token or "MID" in token:
        return "MIDCPNIFTY"
    if token == "1" or "SENSEX" in token:
        return "SENSEX"
    if "26000" in token or token == "NIFTY" or "NIFTY" in token:
        return "NIFTY"
    return None


def _instrument_label(instrument):
    labels = {
        "NIFTY": "NIFTY",
        "BANKNIFTY": "BANKNIFTY",
        "FINNIFTY": "NIFTY FINSVC",
        "MIDCPNIFTY": "MIDCPNIFTY",
        "SENSEX": "SENSEX",
    }
    family = _instrument_family(instrument)
    return labels.get(family, str(instrument))


def _release_closed_dynamic_histories(option_history_map, closed_positions, remaining_positions):
    """Keep only Upstox contract frames that an open position can still use.

    Fixed/rolling option histories are shared inputs and must remain available
    for later entries. Premium-target selections use ``upstox|`` keys and are
    re-resolvable from the durable candle cache, so retaining every completed
    trade's DataFrame only turns a two-year replay into a web-worker leak.
    """
    active_keys = {
        position.get("option_history_key") for position in remaining_positions if position.get("option_history_key")
    }
    for position in closed_positions:
        history_key = position.get("option_history_key")
        if history_key and history_key.startswith("upstox|") and history_key not in active_keys:
            option_history_map.pop(history_key, None)


def get_lot_size(instrument, trade_date):
    """Get correct lot size for instrument on a given date"""
    name = _instrument_family(instrument)
    if name is None:
        return 1

    for cutoff, ls in LOT_SIZES.get(name, [(date(2000, 1, 1), 75)]):
        if trade_date >= cutoff:
            return ls
    return 1


def _is_monthly_expiry(expiry: date) -> bool:
    """True when this expiry is the last one of its calendar month.

    Monthly and weekly contracts carried different lots through July 2021, and
    the monthly is simply the final weekly of the month -- so "is there another
    expiry weekday left in this month?" settles it without a calendar.
    """
    return (expiry + timedelta(days=7)).month != expiry.month


def get_option_contract_lot_size(instrument, contract_expiry):
    """Return the exchange lot attached to an option contract's expiry.

    NIFTY lot-size revisions transition by contract expiry cycle, not simply by
    the trade date. In particular, the 30-Jan-2025 monthly contract retained
    the old 25-lot while January weekly contracts already used 75.
    """
    name = _instrument_family(instrument)
    if name != "NIFTY":
        return get_lot_size(instrument, contract_expiry)

    expiry = contract_expiry
    if isinstance(expiry, datetime):
        expiry = expiry.date()
    if not isinstance(expiry, date):
        expiry = date.fromisoformat(str(expiry))

    # 75 until the 2021 cut. NSE moved weeklies to 50 from August 2021 and
    # monthlies from the July 2021 expiry, so through July 2021 the two cycles
    # disagree: the 29-Jul-2021 monthly was already 50 while that month's
    # weeklies were still 75. A monthly expiry is the last weekly of its month.
    if expiry < date(2021, 7, 1):
        return 75
    if expiry < date(2021, 8, 1):
        return 50 if _is_monthly_expiry(expiry) else 75
    if expiry <= date(2024, 4, 25):
        return 50
    if expiry <= date(2024, 12, 26):
        return 25
    if expiry == date(2025, 1, 30):
        return 25
    if expiry <= date(2025, 12, 30):
        return 75
    return 65


def get_strike_step(instrument):
    """ATM strike rounding: 50 for NIFTY, 100 for BANKNIFTY/SENSEX"""
    if "26009" in str(instrument) or "BANK" in str(instrument).upper():
        return 100
    elif "26037" in str(instrument) or "MID" in str(instrument).upper():
        return 25
    elif "1" == str(instrument) or "SENSEX" in str(instrument).upper():
        return 100
    return 50


def get_sell_option_margin_per_lot(instrument, override=0):
    if override and float(override) > 0:
        return float(override)
    family = _instrument_family(instrument)
    if family is None:
        return 100000.0
    return float(SELL_OPTION_MARGIN_PER_LOT.get(family, 100000.0))


# ── Time Parser ────────────────────────────────────────────────────
def _parse_time(val):
    if isinstance(val, time):
        return val
    if not isinstance(val, str):
        return time(9, 15)
    s = val.strip().upper()
    pm = "PM" in s
    am = "AM" in s
    s = s.replace("AM", "").replace("PM", "").strip()
    parts = s.split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    sec = int(parts[2]) if len(parts) > 2 else 0
    if pm and h < 12:
        h += 12
    elif am and h == 12:
        h = 0
    return time(h, m, sec)


def _time_to_seconds(val):
    parsed = _parse_time(val)
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second


# ── Condition Evaluator ────────────────────────────────────────────
_PRICE_MAP = {
    "current_open": "open",
    "current_high": "high",
    "current_low": "low",
    "current_close": "close",
    "current_volume": "volume",
}
_TOUCH_RANGE_KEYS = {"current_high", "current_low"}
_TOUCH_POINT_KEYS = {"current_open", "current_close"}
_ROW_INDEPENDENT_CONDITION_KEYS = {"Time_Of_Day", "Day_Of_Week", "number", "time", "days", "true", "false"}
_PREV_SENSITIVE_OPERATORS = {"crosses_above", "crosses_below"}

# Why a cross could not be decided, most recent last. A cross needs the
# previous bar; when there isn't one these operators used to fall back to a
# plain > or <, which answers a DIFFERENT question and answers it wrongly --
# "crossed above S1" became "is above S1", so every level under the current
# price reported a cross that never happened. They return False now, and drop
# a note here so the engines can say WHY rather than leaving it invisible.
#
# Written only on the skip path, so a clean backtest never touches it, and
# bounded so a dirty one cannot grow it without limit.
_CROSS_SKIPS: deque = deque(maxlen=64)


def _note_cross_skip(op: str, left, right, reason: str) -> None:
    try:
        _CROSS_SKIPS.append(
            {
                "operator": str(op),
                "left": str(left),
                "right": str(right) if not isinstance(right, dict) else str(right.get("value", right)),
                "reason": reason,
            }
        )
    except Exception:
        pass


def drain_cross_skips() -> list:
    """Take and clear the notes. Callers log them; nobody accumulates them."""
    out = list(_CROSS_SKIPS)
    _CROSS_SKIPS.clear()
    return out


def _prev_cross_operand(prev_row, key, cond, op, side: str):
    """One side of the previous bar, or None with a note saying what was wrong."""
    value = _resolve_value(prev_row, key, cond)
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        _note_cross_skip(op, key, key, f"previous bar has no {side} value")
        return None
    if as_float != as_float:  # NaN — an indicator still warming up
        _note_cross_skip(op, key, key, f"previous bar {side} value is NaN (indicator warming up)")
        return None
    return as_float


def _resolve_value(row, key, cond=None):
    """Map a condition field name to the actual DataFrame column value."""
    if key in _PRICE_MAP:
        return row.get(_PRICE_MAP[key])
    if key == "number":
        return float(cond.get("right_number_value", 0)) if cond else 0.0
    if key in ("true", "false"):
        return key == "true"
    return row.get(key)


def _touch_tolerance(a: float, b: float) -> float:
    scale = max(abs(a), abs(b), 1.0)
    return max(1e-9, scale * 1e-9)


def _touches_are_equal(a: float, b: float) -> bool:
    return abs(a - b) <= _touch_tolerance(a, b)


def _touch_range_price(row, target_value: float) -> float | None:
    candle_high = row.get("high")
    candle_low = row.get("low")
    if candle_high is None or candle_low is None:
        return None
    try:
        high_val = float(candle_high)
        low_val = float(candle_low)
    except (TypeError, ValueError):
        return None
    if low_val <= target_value <= high_val:
        return target_value
    return None


def _touch_fill_price(row, cond, prev_row=None):
    left = cond["left"]
    right = cond["right"]
    lv = _resolve_value(row, left)
    rv = _resolve_value(row, right, cond)
    try:
        if lv is None or rv is None:
            return None
        if isinstance(lv, float) and pd.isna(lv):
            return None
        if not isinstance(rv, bool) and isinstance(rv, float) and pd.isna(rv):
            return None
        lv_f = float(lv)
        rv_f = float(rv)
    except (TypeError, ValueError):
        return None

    if left in _TOUCH_RANGE_KEYS:
        return _touch_range_price(row, rv_f)
    if right in _TOUCH_RANGE_KEYS:
        return _touch_range_price(row, lv_f)

    if left in _TOUCH_POINT_KEYS:
        point_value = row.get(_PRICE_MAP[left])
        try:
            point_f = float(point_value)
        except (TypeError, ValueError):
            return None
        return rv_f if _touches_are_equal(point_f, rv_f) else None
    if right in _TOUCH_POINT_KEYS:
        point_value = row.get(_PRICE_MAP[right])
        try:
            point_f = float(point_value)
        except (TypeError, ValueError):
            return None
        return lv_f if _touches_are_equal(lv_f, point_f) else None

    if _touches_are_equal(lv_f, rv_f):
        return rv_f
    if prev_row is None:
        return None

    plv = _resolve_value(prev_row, left)
    prv = _resolve_value(prev_row, right, cond)
    try:
        if plv is None or prv is None:
            return None
        if isinstance(plv, float) and pd.isna(plv):
            return None
        if isinstance(prv, float) and pd.isna(prv):
            return None
        prev_diff = float(plv) - float(prv)
    except (TypeError, ValueError):
        return None

    cur_diff = lv_f - rv_f
    if _touches_are_equal(cur_diff, 0.0) or _touches_are_equal(prev_diff, 0.0):
        return rv_f
    if (prev_diff < 0 < cur_diff) or (prev_diff > 0 > cur_diff):
        return rv_f
    return None


def eval_condition(row, cond, prev_row=None):
    left = cond["left"]
    op = cond["operator"]

    # Special: Time Of Day — compare candle time vs HH:MM or HH:MM:SS
    if left == "Time_Of_Day":
        ts = row.name if hasattr(row, "name") else None
        if ts is None:
            return False
        cur_seconds = ts.hour * 3600 + ts.minute * 60 + ts.second
        rhs_seconds = _time_to_seconds(cond.get("right_time", cond.get("right", "09:15")))
        prev_seconds = None
        if prev_row is not None and hasattr(prev_row, "name") and prev_row.name is not None:
            prev_ts = prev_row.name
            if getattr(prev_ts, "date", None) and prev_ts.date() == ts.date():
                prev_seconds = prev_ts.hour * 3600 + prev_ts.minute * 60 + prev_ts.second

        if op in ("is_below", "<"):
            return cur_seconds < rhs_seconds
        elif op in ("is_above", ">"):
            return cur_seconds > rhs_seconds
        elif op == ">=":
            return cur_seconds >= rhs_seconds
        elif op == "<=":
            return cur_seconds <= rhs_seconds
        elif op == "==":
            return cur_seconds == rhs_seconds
        elif op == "crosses_above":
            return prev_seconds is not None and prev_seconds < rhs_seconds <= cur_seconds
        elif op == "crosses_below":
            return prev_seconds is not None and prev_seconds > rhs_seconds >= cur_seconds
        return False

    # Special: Day Of Week — check if current day is in selected days
    if left == "Day_Of_Week":
        ts = row.name if hasattr(row, "name") else None
        if ts is None:
            return False
        day_name = ts.strftime("%A")  # Monday, Tuesday, etc.
        if op == "contains":
            selected = cond.get("right_days", [])
            if isinstance(selected, str):
                selected = [selected]
            return day_name in selected
        elif op == "not_contains":
            selected = cond.get("right_days", [])
            if isinstance(selected, str):
                selected = [selected]
            return day_name not in selected
        return False

    # Standard indicator conditions
    lv = _resolve_value(row, left)
    r = cond["right"]
    rv = _resolve_value(row, r, cond)
    try:
        if lv is None or rv is None:
            return False
        if isinstance(lv, float) and pd.isna(lv):
            return False
        if not isinstance(rv, bool) and isinstance(rv, float) and pd.isna(rv):
            return False
    except Exception:
        return False

    lv_f = float(lv)
    rv_f = float(rv)

    # Crossover detection: needs the previous bar. Without it there is no
    # answer -- NOT a plain comparison. Falling back to `lv > rv` turned
    # "crossed above S1" into "is above S1", so on 2026-09-01 a NIFTY 24300PE
    # exit showed S1, S2 and S3 all crossed on one bar with the index sitting
    # above all three and never having dipped below any of them.
    if op in ("crosses_above", "crosses_below"):
        if prev_row is None:
            _note_cross_skip(op, left, r, "no previous bar to compare against")
            return False
        plv_f = _prev_cross_operand(prev_row, left, None, op, "left")
        prv_f = _prev_cross_operand(prev_row, r, cond, op, "right")
        if plv_f is None or prv_f is None:
            return False
        if op == "crosses_above":
            return plv_f <= prv_f and lv_f > rv_f
        return plv_f >= prv_f and lv_f < rv_f
    if op == "touches":
        return _touch_fill_price(row, cond, prev_row) is not None
    if op == "is_above":
        return lv_f > rv_f
    elif op == "is_below":
        return lv_f < rv_f
    elif op == "==":
        return bool(lv) == rv if isinstance(rv, bool) else lv_f == rv_f
    elif op == ">=":
        return lv_f >= rv_f
    elif op == "<=":
        return lv_f <= rv_f
    elif op == "is_true":
        return bool(lv)
    elif op == "is_false":
        return not bool(lv)
    return False


def eval_condition_group(row, conditions, prev_row=None):
    if not conditions:
        return False
    result = eval_condition(row, conditions[0], prev_row)
    for c in conditions[1:]:
        v = eval_condition(row, c, prev_row)
        conn = c.get("logic", c.get("connector", "AND")).upper()
        if conn in ("AND", "IF"):
            result = result and v
        elif conn == "OR":
            result = result or v
    return result


def _condition_operand_requires_row_value(key):
    return isinstance(key, str) and key not in _ROW_INDEPENDENT_CONDITION_KEYS


def _is_missing_runtime_value(value):
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except Exception:
        return False
    if isinstance(missing, (np.ndarray, pd.Series, list, tuple)):
        return False
    return bool(missing)


def _collect_condition_missing_fields(row, cond, prev_row=None):
    missing_fields = []
    seen = set()

    def record(label, value):
        if label in seen:
            return
        if _is_missing_runtime_value(value):
            missing_fields.append(label)
            seen.add(label)

    left = cond.get("left")
    right = cond.get("right")
    operator = cond.get("operator")

    if _condition_operand_requires_row_value(left):
        record(str(left), _resolve_value(row, left))
    if _condition_operand_requires_row_value(right):
        record(str(right), _resolve_value(row, right, cond))

    if prev_row is not None and operator in _PREV_SENSITIVE_OPERATORS:
        if _condition_operand_requires_row_value(left):
            record(f"{left} (prev)", _resolve_value(prev_row, left))
        if _condition_operand_requires_row_value(right):
            record(f"{right} (prev)", _resolve_value(prev_row, right, cond))

    return missing_fields


def inspect_condition_group(row, conditions, prev_row=None):
    """Evaluate conditions, return debug rows, and surface missing row-backed operands."""
    if not conditions:
        return False, [{"condition": "(none)", "result": False}], []

    details = []
    overall = None
    missing_fields = []
    seen_missing = set()

    for index, c in enumerate(conditions):
        lv = _resolve_value(row, c["left"])
        rv = _resolve_value(row, c["right"], c)
        passed = eval_condition(row, c, prev_row)
        label = f"{c['left']} {c['operator']} {c['right']}"
        cond_missing = _collect_condition_missing_fields(row, c, prev_row)
        for field in cond_missing:
            if field not in seen_missing:
                missing_fields.append(field)
                seen_missing.add(field)
        try:
            lv_str = f"{float(lv):,.4f}" if lv is not None else "None"
        except (TypeError, ValueError):
            lv_str = str(lv)
        try:
            rv_str = f"{float(rv):,.4f}" if rv is not None else "None"
        except (TypeError, ValueError):
            rv_str = str(rv)
        item = {
            "condition": label,
            "left_value": lv_str,
            "right_value": rv_str,
            "result": passed,
        }
        if cond_missing:
            item["missing_fields"] = cond_missing
        details.append(item)

        if index == 0:
            overall = passed
        else:
            conn = c.get("logic", c.get("connector", "AND")).upper()
            if conn in ("AND", "IF"):
                overall = overall and passed
            elif conn == "OR":
                overall = overall or passed

    return bool(overall), details, missing_fields


# Which indicator columns a journal chart quotes at the moment of a decision.
# Anything present on the row is captured; absent ones are simply skipped.
DECISION_WHY_INDICATOR_KEYS = (
    "CPR_TC",
    "CPR_P",
    "CPR_BC",
    "CPR_R1",
    "CPR_R2",
    "CPR_R3",
    "CPR_R4",
    "CPR_S1",
    "CPR_S2",
    "CPR_S3",
    "CPR_S4",
    "CPR_is_wide",
    "EMA_20_5m",
    "EMA_20",
    "EMA_9",
    "EMA_50",
    "VWAP",
    "RSI_14",
    "ATR_14",
)


def decision_why(row, conditions, debug=None, prev_row=None, reason=""):
    """The WHY of an entry or exit, frozen so a journal chart can say it later.

    Phil, 2026-08-17: every live trade's chart has to show "where, when, why
    the trade was taken and exited, with all CPR and indicators". Both engines
    already produce per-condition verdicts (`inspect_condition_group`) for the
    debug panel -- and throw them away on the next bar. This keeps them, with
    the bar's own indicator readings, so the trade record carries its reasons
    forever. Read-only over `row`; never raises -- the journal must never be
    the thing that breaks a trade.
    """
    why = {"reason": str(reason or ""), "conditions": [], "indicators": {}, "bar_time": None, "spot": None}
    try:
        stamp = row.name if row is not None and hasattr(row, "name") else None
        if stamp is not None and hasattr(stamp, "isoformat"):
            why["bar_time"] = stamp.isoformat()
        if row is not None:
            for key in ("close", "current_close", "Close"):
                if key in row and row[key] is not None:
                    why["spot"] = float(row[key])
                    break
            for key in DECISION_WHY_INDICATOR_KEYS:
                if key in row and row[key] is not None:
                    value = row[key]
                    try:
                        why["indicators"][key] = bool(value) if isinstance(value, (bool, np.bool_)) else float(value)
                    except (TypeError, ValueError):
                        continue
        details = list((debug or {}).get("conditions") or [])
        if not details and conditions and row is not None:
            _overall, details, _missing = inspect_condition_group(row, conditions, prev_row)
        why["conditions"] = [
            {
                "condition": str(d.get("condition", "")),
                "left_value": d.get("left_value"),
                "right_value": d.get("right_value"),
                "result": bool(d.get("result", False)),
            }
            for d in details
            if isinstance(d, dict)
        ]
    except Exception as exc:
        why["error"] = str(exc)[:200]
    return why


def debug_condition_group(row, conditions, prev_row=None):
    """Evaluate conditions and return per-condition results for debugging."""
    overall, details, _missing_fields = inspect_condition_group(row, conditions, prev_row)
    return overall, details


DEFAULT_ENTRY_CONDITIONS = [{"left": "current_close", "operator": "is_above", "right": "EMA_20_5m", "connector": "AND"}]
DEFAULT_EXIT_CONDITIONS = [{"left": "current_close", "operator": "is_below", "right": "EMA_20_5m", "connector": "AND"}]


# ── Option Helpers ─────────────────────────────────────────────────
def _est_prem(ci, ei, ep, ot, atm_prem=None):
    """Estimate current option premium given index move.
    Uses improved delta model: d = 1 - 1/(1 + r^2.5) where r = ep/atm_prem.
    This gives higher delta for ITM options, matching real weekly option behavior.
    """
    if atm_prem and atm_prem > 0 and ep > 0:
        r = ep / atm_prem
        d = min(0.95, 1.0 - 1.0 / (1.0 + r**2.5))
    else:
        d = 0.5  # fallback ATM delta
    if ot == "PE":
        d = -d
    return max(0.05, ep + (ci - ei) * d)


def _est_prem_gaussian(atm_prem, moneyness):
    """Estimate extrinsic value using Gaussian decay (matches weekly option reality).
    Returns estimated total premium for given moneyness relative to ATM.
    moneyness > 0 = ITM, moneyness < 0 = OTM.
    """
    m_ratio = abs(moneyness) / max(atm_prem, 1)
    extrinsic = atm_prem * math.exp(-1.0 * m_ratio * m_ratio)
    if moneyness > 0:  # ITM
        return max(1, moneyness + extrinsic)
    else:  # OTM
        return max(0.5, extrinsic)


def _opt_pnl(ep, xp, lots, ls, txn):
    d = xp - ep
    if txn == "SELL":
        d = -d
    return d * lots * ls


def _idx_pnl(e, x, lots, ls):
    return (x - e) * lots * ls


# ── Fee / Charges Model (Indian F&O) ──────────────────────────────
def _calc_fees(turnover, pnl, fee_pct=0):
    """Calculate total transaction costs for an F&O trade.
    If fee_pct > 0, use simple percentage model.
    Otherwise apply realistic Indian F&O charges:
      STT: 0.0125% sell-side (options), Brokerage: flat ₹40/order,
      Exchange txn: 0.053%, GST 18% on (brokerage+exchange), SEBI: ₹10/cr, Stamp: 0.003%
    Returns fee amount (always positive).
    """
    if fee_pct > 0:
        return abs(turnover) * fee_pct / 100.0
    # Realistic charges (per-leg, simplified for backtest)
    brokerage = 80  # flat per order, charged once here for the full entry+exit trade
    stt = abs(turnover) * 0.0125 / 100  # sell-side STT on options
    exchange_txn = abs(turnover) * 0.053 / 100
    gst = (brokerage + exchange_txn) * 0.18
    sebi = abs(turnover) * 10 / 1e7  # ₹10 per crore
    stamp = abs(turnover) * 0.003 / 100
    return brokerage + stt + exchange_txn + gst + sebi + stamp


def _trade_duration_str(et, xt):
    """Return human-readable duration between entry and exit."""
    try:
        t1 = pd.Timestamp(str(et))
        t2 = pd.Timestamp(str(xt))
        delta = t2 - t1
        mins = int(delta.total_seconds() / 60)
        if mins < 60:
            return f"{mins}m"
        h, m = divmod(mins, 60)
        return f"{h}h {m}m"
    except:
        return "-"


def _mk(id_, et, xt, ep, xp, pnl, reason, cum, ot=None, strike=None, qty=0, txn=None, fees=0, **extra):
    trade = {
        "id": id_,
        "entry_time": str(et)[:16],
        "exit_time": str(xt)[:16],
        "entry_price": round(ep, 2),
        "exit_price": round(xp, 2),
        "pnl": round(pnl, 2),
        "exit_reason": reason,
        "cumulative": round(cum, 2),
        "option_type": ot,
        "strike": strike or "",
        "qty": qty,
        "txn_type": txn or "",
        "fees": round(fees, 2),
        "duration": _trade_duration_str(et, xt),
    }
    trade.update(extra)
    return trade


def _resolve_option_entry(instrument, option_type, strike_type, strike_value, entry_spot, trade_date, strike_step):
    inst_label = _instrument_label(instrument)
    atm = round_to_nearest_step(entry_spot, strike_step)
    dte = _dte_weekly(trade_date)
    base_pct = 0.009 if "BANK" in inst_label else 0.007
    atm_prem = round(entry_spot * base_pct * math.sqrt(max(0.5, dte) / 3.0), 2)

    if strike_type in ("premium_near", "premium_above", "premium_below") and strike_value > 0:
        best_strike = atm
        best_diff = float("inf")
        for offset in range(-10, 11):
            test_strike = atm + (offset * strike_step)
            moneyness = (entry_spot - test_strike) if option_type == "CE" else (test_strike - entry_spot)
            test_premium = _est_prem_gaussian(atm_prem, moneyness)
            diff = abs(test_premium - strike_value)
            if diff < best_diff:
                best_diff = diff
                best_strike = test_strike
        strike_used = int(best_strike)
        moneyness = (entry_spot - strike_used) if option_type == "CE" else (strike_used - entry_spot)
        entry_premium = max(1, round(_est_prem_gaussian(atm_prem, moneyness), 2))
    elif strike_type == "strike_price" and strike_value > 0:
        strike_used = round_to_nearest_step(strike_value, strike_step)
        moneyness = (entry_spot - strike_used) if option_type == "CE" else (strike_used - entry_spot)
        entry_premium = max(1, round(_est_prem_gaussian(atm_prem, moneyness), 2))
    elif strike_type in ("otm", "itm") and strike_value > 0:
        offset = round_to_nearest_step(strike_value, strike_step)
        if strike_type == "otm":
            strike_used = atm + offset if option_type == "CE" else atm - offset
            moneyness = -offset
        else:
            strike_used = atm - offset if option_type == "CE" else atm + offset
            moneyness = offset
        entry_premium = max(1, round(_est_prem_gaussian(atm_prem, moneyness), 2))
    elif strike_type == "spot_price" and strike_value != 0:
        offset = round_to_nearest_step(strike_value, strike_step)
        strike_used = int(entry_spot + offset) if strike_value > 0 else int(entry_spot - abs(offset))
        strike_used = round_to_nearest_step(strike_used, strike_step)
        moneyness = (entry_spot - strike_used) if option_type == "CE" else (strike_used - entry_spot)
        entry_premium = max(1, round(_est_prem_gaussian(atm_prem, moneyness), 2))
    else:
        strike_used = int(atm)
        entry_premium = atm_prem

    return strike_used, entry_premium, f"{inst_label} {int(strike_used)} {option_type}", atm_prem


def _group_trade_records(trades):
    grouped = {}
    for trade in trades:
        group_id = trade.get("entry_group", trade["id"])
        entry_ts = pd.Timestamp(trade["entry_time"])
        exit_ts = pd.Timestamp(trade["exit_time"])
        group = grouped.setdefault(
            group_id,
            {
                "id": group_id,
                "_entry_ts": entry_ts,
                "_exit_ts": exit_ts,
                "pnl": 0.0,
                "fees": 0.0,
                "legs": 0,
                "exit_reason": trade.get("exit_reason", ""),
            },
        )
        group["_entry_ts"] = min(group["_entry_ts"], entry_ts)
        if exit_ts >= group["_exit_ts"]:
            group["_exit_ts"] = exit_ts
            group["exit_reason"] = trade.get("exit_reason", "")
        group["pnl"] += float(trade.get("pnl", 0) or 0)
        group["fees"] += float(trade.get("fees", 0) or 0)
        group["legs"] += 1

    running = 0.0
    records = []
    for _, group in sorted(grouped.items(), key=lambda item: (item[1]["_exit_ts"], item[0])):
        running += group["pnl"]
        records.append(
            {
                "id": group["id"],
                "entry_time": str(group["_entry_ts"])[:16],
                "exit_time": str(group["_exit_ts"])[:16],
                "pnl": round(group["pnl"], 2),
                "cumulative": round(running, 2),
                "fees": round(group["fees"], 2),
                "legs": group["legs"],
                "exit_reason": group["exit_reason"],
            }
        )
    return records


# ── Backtest Runner ────────────────────────────────────────────────
def run_backtest(df_raw, entry_conditions=None, exit_conditions=None, strategy_config=None):
    if entry_conditions is None:
        entry_conditions = DEFAULT_ENTRY_CONDITIONS
    if exit_conditions is None:
        exit_conditions = DEFAULT_EXIT_CONDITIONS
    sc = strategy_config or {}

    mkt_open = _parse_time(sc.get("market_open", "09:15"))
    mkt_close = _parse_time(sc.get("market_close", "15:25"))
    # Spot-signal cutoff. NSE's closing auction (3 Aug 2026 onward) halts
    # continuous trading in every F&O-eligible stock at 15:15, and since all
    # index constituents are F&O stocks, NIFTY and BANKNIFTY spot stop being
    # priced by real trades then too — until roughly 15:35 the index is computed
    # from the auction's indicative equilibrium prices, levels nobody traded.
    # Past this time no entry and no spot-driven exit may be decided. Exits
    # priced off the OPTION — stop-loss, target, trailing, the timed square-off —
    # are untouched, because options trade continuously until 15:40.
    signal_cutoff_raw = str(sc.get("signal_cutoff_time") or "").strip()
    signal_cutoff = _parse_time(signal_cutoff_raw) if signal_cutoff_raw else None
    combined_sqoff = _parse_time(sc.get("combined_sqoff_time") or sc.get("market_close", "15:20"))
    base_lots = int(sc.get("lots", 1) or 1)
    user_lot_size = int(sc.get("lot_size", 0) or 0)
    sl_pct = float(sc.get("stoploss_pct", 0) or 0)
    sl_rupees = float(sc.get("stoploss_rupees", 0) or 0)
    tp_pct = float(sc.get("target_profit_pct", 0) or 0)
    tp_rupees = float(sc.get("target_profit_rupees", 0) or 0)
    combined_sl_rupees = float(sc.get("combined_sl_rupees", 0) or 0)
    combined_target_rupees = float(sc.get("combined_target_rupees", 0) or 0)
    fee_pct = float(sc.get("fee_pct", 0) or 0)
    initial_capital = float(sc.get("initial_capital", 500000) or 500000)
    trailing_sl_pct = float(sc.get("trailing_sl_pct", 0) or 0)
    max_tpd = int(sc.get("max_trades_per_day", config.MAX_TRADES_PER_DAY))
    max_daily_loss = float(sc.get("max_daily_loss", 0) or 0)
    skip_days_after_profit = max(0, int(sc.get("skip_days_after_profit", 0) or 0))
    skip_profit_threshold = float(sc.get("skip_profit_threshold_rupees", 20000) or 20000)
    indicators = normalize_strategy_indicators(
        sc.get("indicators", []) or [],
        entry_conditions=entry_conditions,
        exit_conditions=exit_conditions,
    )
    sc["indicators"] = indicators
    explicit_execution_timeframe = int(sc.get("execution_timeframe_minutes", 0) or 0)
    execution_timeframe = explicit_execution_timeframe or infer_execution_timeframe(
        indicators,
        entry_conditions,
        default=int(sc.get("timeframe_minutes", 5) or 5),
    )
    sc["timeframe_minutes"] = execution_timeframe
    entry_evaluation_timeframe = max(
        1,
        int(sc.get("entry_evaluation_timeframe_minutes", execution_timeframe) or execution_timeframe),
    )
    legs = sc.get("legs", []) or []
    option_legs = [leg for leg in legs if leg.get("option_type") in ("CE", "PE")]
    instrument = sc.get("instrument", "26000")
    strike_step = get_strike_step(instrument)
    option_history_map = sc.get("_option_history", {}) or {}
    historical_premium_selector = sc.get("_upstox_premium_selector")
    option_data_gaps = sc.setdefault("_option_data_gaps", [])
    spread_bps = max(0.0, float(sc.get("spread_bps", 0) or 0))
    entry_slippage_bps = max(0.0, float(sc.get("entry_slippage_bps", 0) or 0))
    exit_slippage_bps = max(0.0, float(sc.get("exit_slippage_bps", 0) or 0))
    entry_delay_candles = max(0, int(sc.get("entry_delay_candles", 0) or 0))
    signal_exit_delay_candles = max(0, int(sc.get("signal_exit_delay_candles", 0) or 0))
    signal_exit_next_open = bool(sc.get("signal_exit_next_open", False))
    enforce_capital = bool(sc.get("enforce_capital", False))
    capital_buffer_pct = min(99.0, max(0.0, float(sc.get("capital_buffer_pct", 0) or 0)))
    sell_option_margin_per_lot = get_sell_option_margin_per_lot(instrument, sc.get("sell_option_margin_per_lot", 0))

    df = compute_dynamic_indicators(
        df_raw.copy(),
        indicators,
        default_timeframe_minutes=execution_timeframe,
        source_timeframe_minutes=int(sc.get("fetch_timeframe_minutes", 0) or 0) or None,
        execution_timeframe_minutes=execution_timeframe,
    )
    is_daily = len(df) >= 2 and (df.index[1] - df.index[0]).total_seconds() >= 86400
    # No entry before the first 5-minute candle of the session has closed. A
    # strategy running on 1-minute execution can legitimately want an earlier
    # floor, so the time is configurable; the default keeps every existing run
    # byte-identical.
    entry_earliest_raw = str(sc.get("entry_earliest_time") or "").strip()
    entry_earliest = _parse_time(entry_earliest_raw) if entry_earliest_raw else time(9, 20)

    total_pnl = 0.0
    total_fees = 0.0
    daily_pnl = 0.0
    trades = []
    equity = []
    open_positions = []
    signal_candle = None
    trade_entry_time = None
    trade_group_id = 0
    strategy_sl_val = 0.0
    strategy_tp_val = 0.0
    trade_peak_pnl = 0.0
    trades_today = 0
    max_daily_loss_hit = False
    skip_sessions_left = 0
    cooldown_sessions_skipped = 0
    ld = None
    lot_size = user_lot_size if user_lot_size > 0 else 1
    capital_rejections = 0
    reserved_capital = 0.0
    pending_entry = None
    pending_signal_exit = None

    print(
        f"[BT] open={mkt_open} close={mkt_close} lots={base_lots} user_lot_size={user_lot_size} "
        f"sl={sl_pct}%/₹{sl_rupees} tp={tp_pct}%/₹{tp_rupees} combined_sl=₹{combined_sl_rupees} "
        f"combined_tp=₹{combined_target_rupees} max_daily_loss=₹{max_daily_loss} "
        f"profit_cooldown={skip_days_after_profit}d>₹{skip_profit_threshold:.0f}"
    )
    print(f"[BT] option_legs={len(option_legs)} instrument={instrument} sqoff={combined_sqoff}")
    print(
        f"[BT] spread={spread_bps}bps entry_slip={entry_slippage_bps}bps exit_slip={exit_slippage_bps}bps "
        f"entry_delay={entry_delay_candles} signal_exit_delay={signal_exit_delay_candles} "
        f"capital_check={enforce_capital} buffer={capital_buffer_pct}%"
    )

    raw_price_df = df_raw.copy().sort_index()
    raw_day_cache = {trade_date: day_df for trade_date, day_df in raw_price_df.groupby(raw_price_df.index.date)}

    # Sessions that end before the square-off are not tradeable intraday. NSE's
    # muhurat session is about an hour, so 15:20 never arrives, the timed exit
    # never fires, and a position opened there is carried to the next session --
    # it surfaces as a single enormous overnight "EOD" trade that nobody could
    # have taken. No entry is taken on such a day. Set skip_stub_sessions False
    # to keep them.
    stub_sessions = set()
    if not is_daily and bool(sc.get("skip_stub_sessions", True)):
        for trade_date, day_df in raw_day_cache.items():
            if len(day_df) and day_df.index[-1].time() < combined_sqoff:
                stub_sessions.add(trade_date)
        if stub_sessions:
            print(
                f"[BT] skipping {len(stub_sessions)} stub session(s): "
                f"{', '.join(str(d) for d in sorted(stub_sessions))}"
            )

    def _history_frame(position, raw=False):
        history_key = position.get("option_history_key")
        if not history_key:
            return None
        history_obj = option_history_map.get(history_key)
        if isinstance(history_obj, dict):
            history_df = history_obj.get("raw" if raw else "execution")
            if history_df is None:
                history_df = history_obj.get("execution") or history_obj.get("raw")
            return history_df
        return history_obj

    def _history_row(position, ts, raw=False):
        if ts is None:
            return None
        history_df = _history_frame(position, raw=raw)
        if history_df is None or history_df.empty or ts not in history_df.index:
            return None
        row = history_df.loc[ts]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        return row

    def _history_value(position, ts, field, default=None, raw=False):
        row = _history_row(position, ts, raw=raw)
        if row is None and ts is not None:
            # Illiquid historical options do not print in every execution
            # bucket. Mark them at the most recent REAL Upstox close from the
            # same session; never fall through to the synthetic spot model.
            history_df = _history_frame(position, raw=raw)
            if history_df is not None and not history_df.empty:
                prior = history_df.loc[history_df.index <= ts]
                if not prior.empty and prior.index[-1].date() == ts.date():
                    carried = prior.iloc[-1].get("close")
                    try:
                        if carried is not None and not pd.isna(carried):
                            return float(carried)
                    except (TypeError, ValueError):
                        pass
        if row is None:
            return default
        value = row.get(field, default)
        try:
            if value is None or (isinstance(value, float) and pd.isna(value)):
                return default
            return float(value)
        except Exception:
            return default

    def _raw_row_at_or_after(trade_date, trigger_time):
        if is_daily:
            return None, None
        day_df = raw_day_cache.get(trade_date)
        if day_df is None or day_df.empty:
            return None, None
        mask = pd.Index(day_df.index.time) >= trigger_time
        if not mask.any():
            return None, None
        raw_match = day_df.loc[mask]
        if raw_match.empty:
            return None, None
        raw_ts = raw_match.index[0]
        return raw_ts, raw_match.iloc[0]

    def _sqoff_exit_snapshot(position, trade_date, trigger_time, fallback_ts, fallback_price):
        raw_ts, raw_row = _raw_row_at_or_after(trade_date, trigger_time)
        if raw_ts is None or raw_row is None:
            return fallback_ts, fallback_price

        raw_spot = float(raw_row.get("close", raw_row.get("open", fallback_price)))
        if position["is_option"] and position.get("pricing_mode") == "historical":
            hist_price = _history_value(position, raw_ts, "close", raw=True)
            if hist_price is not None:
                return raw_ts, hist_price
        return raw_ts, _position_price(position, raw_spot, raw_ts, "close")

    def _position_price(position, spot_price, ts=None, field="close"):
        if position["is_option"] and position.get("pricing_mode") == "historical":
            hist_price = _history_value(position, ts, field)
            if hist_price is not None:
                return hist_price
            raise ValueError(
                f"Missing real historical option price for {position.get('display_symbol', 'option')} "
                f"at {ts}; synthetic fallback is disabled for Upstox-priced trades."
            )
        if position["is_option"]:
            return _est_prem(
                spot_price,
                position["entry_spot"],
                position.get("model_entry_price", position["entry_price"]),
                position["option_type"],
                position["atm_prem_ref"],
            )
        return spot_price

    def _position_snapshots(position, ts, close_spot, high_spot, low_spot):
        if position["is_option"] and position.get("pricing_mode") == "historical":
            current_price = _position_price(position, close_spot, ts, "close")
            high_price = _position_price(position, high_spot, ts, "high")
            low_price = _position_price(position, low_spot, ts, "low")
            actual_strike = _history_value(position, ts, "strike")
            if actual_strike is not None:
                position["strike"] = int(round(actual_strike))
                position["display_symbol"] = (
                    f"{position['underlying_symbol']} {position['strike']} {position['option_type']}"
                )
        else:
            current_price = _position_price(position, close_spot)
            high_price = _position_price(position, high_spot)
            low_price = _position_price(position, low_spot)
        if position["transaction_type"] == "BUY":
            worst_price = min(current_price, high_price, low_price)
            best_price = max(current_price, high_price, low_price)
            position["peak_premium"] = max(position["peak_premium"], current_price)
        else:
            worst_price = max(current_price, high_price, low_price)
            best_price = min(current_price, high_price, low_price)
            position["peak_premium"] = min(position["peak_premium"], current_price)
        return {"current": current_price, "worst": worst_price, "best": best_price}

    def _position_pnl(position, price):
        direction = 1 if position["transaction_type"] == "BUY" else -1
        return (price - position["entry_price"]) * direction * position["qty"]

    def _apply_execution_costs(price, transaction_type, stage):
        base_price = max(0.05, float(price))
        half_spread = spread_bps / 20000.0
        slip_bps = entry_slippage_bps if stage == "entry" else exit_slippage_bps
        adverse_move = half_spread + (slip_bps / 10000.0)
        if transaction_type == "BUY":
            multiplier = 1.0 + adverse_move if stage == "entry" else max(0.0, 1.0 - adverse_move)
        else:
            multiplier = max(0.0, 1.0 - adverse_move) if stage == "entry" else 1.0 + adverse_move
        return max(0.05, round(base_price * multiplier, 4))

    def _capital_required(position):
        if position["is_option"] and position["transaction_type"] == "SELL":
            return float(position["lots"]) * sell_option_margin_per_lot
        return float(position["entry_price"]) * float(position["qty"])

    def _free_capital():
        return max(0.0, initial_capital + total_pnl - reserved_capital)

    def _capital_limit():
        return _free_capital() * max(0.0, 1.0 - capital_buffer_pct / 100.0)

    def _close_selected_positions(selected_positions, exit_ts, exit_price_fn, reason):
        nonlocal daily_pnl, open_positions, signal_candle, strategy_sl_val, strategy_tp_val
        nonlocal total_fees, total_pnl, trade_entry_time, trade_peak_pnl, reserved_capital, pending_signal_exit

        selected_ids = {id(position) for position in selected_positions}
        remaining = []
        for position in open_positions:
            if id(position) not in selected_ids:
                remaining.append(position)
                continue

            market_exit_price = float(exit_price_fn(position))
            exit_price = _apply_execution_costs(market_exit_price, position["transaction_type"], "exit")
            raw_pnl = _position_pnl(position, exit_price)
            fee = _calc_fees((position["entry_price"] + exit_price) * position["qty"], raw_pnl, fee_pct)
            pnl = raw_pnl - fee
            total_fees += fee
            total_pnl += pnl
            daily_pnl += pnl
            reserved_capital = max(0.0, reserved_capital - float(position.get("capital_required", 0.0) or 0.0))
            trades.append(
                _mk(
                    len(trades) + 1,
                    position["entry_time"],
                    exit_ts,
                    position["entry_price"],
                    exit_price,
                    pnl,
                    reason,
                    total_pnl,
                    position.get("option_type"),
                    position["display_symbol"],
                    position["qty"],
                    position["transaction_type"],
                    fee,
                    leg_num=position["leg_num"],
                    entry_group=position["entry_group"],
                    symbol=position["display_symbol"],
                    lots=position["lots"],
                    lot_size=position["lot_size"],
                    contract_expiry=position.get("contract_expiry", ""),
                )
            )

        open_positions = remaining
        _release_closed_dynamic_histories(option_history_map, selected_positions, remaining)
        if not open_positions:
            signal_candle = None
            trade_entry_time = None
            strategy_sl_val = 0.0
            strategy_tp_val = 0.0
            trade_peak_pnl = 0.0
            pending_signal_exit = None

    def _open_entry_positions(candidate_positions):
        nonlocal open_positions, trades_today, trade_peak_pnl, reserved_capital, capital_rejections
        nonlocal signal_candle, trade_entry_time, pending_entry, pending_signal_exit

        required_capital = 0.0
        for position in candidate_positions:
            position["capital_required"] = _capital_required(position)
            required_capital += position["capital_required"]

        if enforce_capital and required_capital > _capital_limit() + 1e-9:
            capital_rejections += 1
            signal_candle = None
            trade_entry_time = None
            pending_entry = None
            pending_signal_exit = None
            return False

        reserved_capital += required_capital
        open_positions = candidate_positions
        trades_today += 1
        trade_peak_pnl = 0.0
        pending_entry = None
        pending_signal_exit = None
        return True

    def _touch_spot(touch_row):
        for condition in exit_conditions:
            if condition.get("operator") != "touches":
                continue
            touch_price = _touch_fill_price(touch_row, condition, prev_row)
            if touch_price is not None:
                return float(touch_price)
        return None

    def _build_entry_positions(entry_spot, entry_ts, trade_date, day_lot_size):
        nonlocal strategy_sl_val, strategy_tp_val

        positions = []
        if option_legs:
            for leg_num, leg in enumerate(option_legs, start=1):
                leg_lots = int(leg.get("lots", base_lots) or base_lots or 1)
                position_lot_size = day_lot_size
                contract_expiry = None
                history_key = leg.get("_bt_option_history_key")
                strike_used, entry_price, display_symbol, atm_prem = _resolve_option_entry(
                    instrument,
                    leg["option_type"],
                    leg.get("strike_type", "atm") or "atm",
                    float(leg.get("strike_value", 0) or 0),
                    entry_spot,
                    trade_date,
                    strike_step,
                )
                pricing_mode = "synthetic"
                premium_target = str(leg.get("strike_type") or "").lower() in {
                    "premium_near",
                    "premium_above",
                    "premium_below",
                }
                if premium_target and historical_premium_selector is not None:
                    resolution = historical_premium_selector.select(
                        entry_ts,
                        entry_spot,
                        leg,
                        execution_timeframe,
                    )
                    if resolution is None:
                        option_data_gaps.append(
                            {
                                "timestamp": entry_ts.isoformat(timespec="minutes"),
                                "leg": leg_num,
                                "reason": historical_premium_selector.last_gap
                                or "Upstox premium selection unavailable",
                            }
                        )
                        return []
                    history_key = resolution.history_key
                    option_history_map[history_key] = resolution.history
                    strike_used = resolution.strike
                    contract_expiry = resolution.expiry
                    if user_lot_size <= 0:
                        position_lot_size = get_option_contract_lot_size(instrument, resolution.expiry)
                    entry_price = resolution.entry_price
                    display_symbol = f"{_instrument_label(instrument)} {strike_used} {leg['option_type']}"
                    pricing_mode = "historical"
                if history_key:
                    history_row = None
                    history_df = _history_frame({"option_history_key": history_key})
                    if history_df is not None and not history_df.empty and entry_ts in history_df.index:
                        history_row = history_df.loc[entry_ts]
                        if isinstance(history_row, pd.DataFrame):
                            history_row = history_row.iloc[-1]
                    if history_row is not None:
                        hist_open = history_row.get("open")
                        hist_strike = history_row.get("strike")
                        if hist_open is not None and not pd.isna(hist_open):
                            entry_price = float(hist_open)
                            pricing_mode = "historical"
                        if hist_strike is not None and not pd.isna(hist_strike):
                            strike_used = int(round(float(hist_strike)))
                            display_symbol = f"{_instrument_label(instrument)} {strike_used} {leg['option_type']}"
                # SIZE FOR THE DAY THE EDGE IS ON. Measured on the PE book, the
                # expiry session carries the whole result and the other four
                # days are a net drag -- and the expiry WEEKDAY moved from
                # Thursday to Tuesday in September 2025, so a weekday rule
                # would have silently pointed at the wrong day. Comparing the
                # trade's date with its own contract's expiry needs no calendar
                # and follows any future change on its own.
                expiry_day_lots = int(leg.get("expiry_day_lots", 0) or 0)
                if expiry_day_lots > 0 and contract_expiry is not None and contract_expiry == trade_date:
                    leg_lots = expiry_day_lots

                # COMPOUND AS THE BOOK GROWS. One more lot for every
                # `compound_step_pct` per cent of the starting capital the book
                # has actually banked, on top of whatever this day's base is.
                #
                # It reads total_pnl, which is REALISED money only -- an open
                # position never sizes the next one. It ratchets: a book that
                # gives money back drops rungs with it, and never goes below the
                # configured lots.
                #
                # This lives here, in the engine, on purpose. Sizing was
                # previously estimated by dividing a finished book's P&L by its
                # lot count, which is wrong whenever fees do not scale with lots
                # -- brokerage is flat per order, so they never do.
                step_pct = float(sc.get("compound_step_pct", 0) or 0)
                if step_pct > 0:
                    ladder_capital = float(sc.get("compound_base_capital", 0) or initial_capital or 0)
                    if ladder_capital > 0:
                        rung = ladder_capital * step_pct / 100.0
                        extra = int(max(0.0, total_pnl) // rung)
                        max_lots = int(sc.get("compound_max_lots", 20) or 20)
                        leg_lots = max(1, min(leg_lots + extra, max_lots))

                positions.append(
                    {
                        "entry_group": trade_group_id,
                        "leg_num": leg_num,
                        "is_option": True,
                        "option_type": leg["option_type"],
                        "transaction_type": leg.get("transaction_type", "BUY"),
                        "entry_time": entry_ts,
                        "entry_spot": entry_spot,
                        "entry_price": _apply_execution_costs(
                            entry_price,
                            leg.get("transaction_type", "BUY"),
                            "entry",
                        ),
                        "model_entry_price": entry_price,
                        "display_symbol": display_symbol,
                        "underlying_symbol": _instrument_label(instrument),
                        "strike": strike_used,
                        "lots": leg_lots,
                        "lot_size": position_lot_size,
                        "qty": leg_lots * position_lot_size,
                        "contract_expiry": contract_expiry.isoformat() if contract_expiry else "",
                        "sl_pct": float(leg.get("sl_pct", 0) or 0),
                        "target_pct": float(leg.get("target_pct", 0) or 0),
                        "sl_points": float(leg.get("sl_points", 0) or 0),
                        "target_points": float(leg.get("target_points", 0) or 0),
                        "sl_rupees": float(leg.get("sl_rupees", 0) or 0),
                        "target_rupees": float(leg.get("target_rupees", 0) or 0),
                        "trail_pct": float(leg.get("trail_pct", 0) or 0),
                        "sqoff_time": _parse_time(leg.get("sqoff_time", combined_sqoff.strftime("%H:%M"))),
                        "peak_premium": entry_price,
                        "atm_prem_ref": atm_prem,
                        "pricing_mode": pricing_mode,
                        "option_history_key": history_key if pricing_mode == "historical" else None,
                    }
                )
        else:
            positions.append(
                {
                    "entry_group": trade_group_id,
                    "leg_num": 1,
                    "is_option": False,
                    "option_type": None,
                    "transaction_type": "BUY",
                    "entry_time": entry_ts,
                    "entry_spot": entry_spot,
                    "entry_price": _apply_execution_costs(entry_spot, "BUY", "entry"),
                    "model_entry_price": entry_spot,
                    "display_symbol": _instrument_label(instrument),
                    "underlying_symbol": _instrument_label(instrument),
                    "strike": None,
                    "lots": base_lots,
                    "lot_size": day_lot_size,
                    "qty": base_lots * day_lot_size,
                    "sl_pct": 0.0,
                    "target_pct": 0.0,
                    "sl_points": 0.0,
                    "target_points": 0.0,
                    "sl_rupees": 0.0,
                    "target_rupees": 0.0,
                    "trail_pct": 0.0,
                    "sqoff_time": combined_sqoff,
                    "peak_premium": entry_spot,
                    "atm_prem_ref": 0.0,
                    "pricing_mode": "spot",
                    "option_history_key": None,
                }
            )

        entry_notional = sum(position["entry_price"] * position["qty"] for position in positions)
        sl_rupee_limit = combined_sl_rupees if combined_sl_rupees > 0 else sl_rupees
        tp_rupee_limit = combined_target_rupees if combined_target_rupees > 0 else tp_rupees
        strategy_sl_val = sl_rupee_limit if sl_rupee_limit > 0 else entry_notional * sl_pct / 100 if sl_pct > 0 else 0.0
        strategy_tp_val = tp_rupee_limit if tp_rupee_limit > 0 else entry_notional * tp_pct / 100 if tp_pct > 0 else 0.0
        return positions

    prev_row = None
    prev_prev_row = None
    for ts, row in df.iterrows():
        ct = ts.time()
        cd = ts.date()
        exited_this_candle = False
        # Every decision that reads SPOT is gated on this. A daily bar has no
        # intraday clock to cut off.
        signals_live = is_daily or signal_cutoff is None or ct < signal_cutoff

        if cd != ld:
            if open_positions and ld is not None and not is_daily:
                day_open = float(row["open"])
                _close_selected_positions(
                    list(open_positions),
                    ts,
                    lambda position, spot=day_open, exit_ts=ts: _position_price(position, spot, exit_ts, "open"),
                    "EOD",
                )
                exited_this_candle = True
            if skip_days_after_profit > 0 and ld is not None:
                # daily_pnl still holds the finished session's realized P&L here
                if daily_pnl > skip_profit_threshold:
                    skip_sessions_left = skip_days_after_profit
                elif skip_sessions_left > 0:
                    skip_sessions_left -= 1
                if skip_sessions_left > 0:
                    cooldown_sessions_skipped += 1
            trades_today = 0
            daily_pnl = 0.0
            ld = cd
            prev_row = None
            prev_prev_row = None
            lot_size = user_lot_size if user_lot_size > 0 else get_lot_size(instrument, cd)
            pending_entry = None

        snapshots = {}
        if open_positions:
            close_spot = float(row["close"])
            high_spot = float(row.get("high", close_spot))
            low_spot = float(row.get("low", close_spot))

            portfolio_cur_pnl = 0.0
            portfolio_worst_pnl = 0.0
            portfolio_best_pnl = 0.0
            for position in open_positions:
                snap = _position_snapshots(position, ts, close_spot, high_spot, low_spot)
                snapshots[id(position)] = snap
                portfolio_cur_pnl += _position_pnl(position, snap["current"])
                portfolio_worst_pnl += _position_pnl(position, snap["worst"])
                portfolio_best_pnl += _position_pnl(position, snap["best"])

            trade_peak_pnl = max(trade_peak_pnl, portfolio_cur_pnl)

            if strategy_sl_val > 0 and portfolio_worst_pnl <= -strategy_sl_val:
                _close_selected_positions(
                    list(open_positions),
                    ts,
                    lambda position, snap_map=snapshots: snap_map[id(position)]["worst"],
                    "StrategySL",
                )
                exited_this_candle = True
            if not exited_this_candle and strategy_tp_val > 0 and portfolio_best_pnl >= strategy_tp_val:
                # Fill AT the target, never at the candle's best price. The
                # target is touched somewhere inside the candle; booking the
                # extreme instead books a price no order can get, and on the
                # live PE book that was 66% of the backtested profit. P&L is
                # linear in price, so interpolate current -> best to the exact
                # point the target is met. If the candle already opened past
                # the target the fraction is 0 and the fill is the current
                # price, which is the honest worst case of the two.
                span = portfolio_best_pnl - portfolio_cur_pnl
                reach = (strategy_tp_val - portfolio_cur_pnl) / span if span > 0 else 0.0
                reach = min(1.0, max(0.0, reach))

                def exit_at_target(position, snap_map=snapshots, f=reach):
                    snap = snap_map[id(position)]
                    return snap["current"] + (snap["best"] - snap["current"]) * f

                _close_selected_positions(list(open_positions), ts, exit_at_target, "StrategyTP")
                exited_this_candle = True
            if not exited_this_candle and trailing_sl_pct > 0 and trade_peak_pnl > 0:
                if portfolio_cur_pnl <= trade_peak_pnl * (1 - trailing_sl_pct / 100):
                    _close_selected_positions(
                        list(open_positions),
                        ts,
                        lambda position, snap_map=snapshots: snap_map[id(position)]["current"],
                        "StratTrailSL",
                    )
                    exited_this_candle = True
            if (
                not exited_this_candle
                and signals_live
                and any(condition.get("operator") == "touches" for condition in exit_conditions)
            ):
                touch_row = row.copy() if signal_candle else row
                if signal_candle:
                    for key, value in signal_candle.items():
                        touch_row[key] = value
                if eval_condition_group(touch_row, exit_conditions, prev_row):
                    touch_spot = _touch_spot(touch_row)
                    if touch_spot is not None:
                        _close_selected_positions(
                            list(open_positions),
                            ts,
                            lambda position, spot=touch_spot, exit_ts=ts: _position_price(
                                position, spot, exit_ts, "close"
                            ),
                            "Touch",
                        )
                        exited_this_candle = True
            if not exited_this_candle and pending_signal_exit:
                pending_signal_exit["remaining"] -= 1
                if pending_signal_exit["remaining"] <= 0:
                    if pending_signal_exit.get("at_open"):

                        def exit_price_fn(position, spot=float(row["open"]), exit_ts=ts):
                            return _position_price(position, spot, exit_ts, "open")
                    else:

                        def exit_price_fn(position, snap_map=snapshots):
                            return snap_map[id(position)]["current"]

                    _close_selected_positions(
                        list(open_positions),
                        ts,
                        exit_price_fn,
                        "Signal",
                    )
                    exited_this_candle = True
            # A pending signal exit already decided before the cutoff still
            # executes: the decision was made on real prices, and it is filled
            # at the option's own premium.
            if not exited_this_candle and not pending_signal_exit and signals_live:
                exit_row = row.copy() if signal_candle else row
                if signal_candle:
                    for key, value in signal_candle.items():
                        exit_row[key] = value
                if eval_condition_group(exit_row, exit_conditions, prev_row):
                    if signal_exit_next_open:
                        pending_signal_exit = {"remaining": 1, "at_open": True}
                    elif signal_exit_delay_candles > 0:
                        pending_signal_exit = {"remaining": signal_exit_delay_candles}
                    else:
                        _close_selected_positions(
                            list(open_positions),
                            ts,
                            lambda position, snap_map=snapshots: snap_map[id(position)]["current"],
                            "Signal",
                        )
                        exited_this_candle = True
            if not exited_this_candle and not is_daily and ct >= combined_sqoff:
                sqoff_price_map = {
                    id(position): _sqoff_exit_snapshot(
                        position, cd, combined_sqoff, ts, snapshots[id(position)]["current"]
                    )
                    for position in open_positions
                }
                exit_ts = min(snapshot[0] for snapshot in sqoff_price_map.values())
                _close_selected_positions(
                    list(open_positions),
                    exit_ts,
                    lambda position, snapshot_map=sqoff_price_map: snapshot_map[id(position)][1],
                    "SquareOff",
                )
                exited_this_candle = True

            if open_positions and not exited_this_candle:
                leg_exits = []
                for position in list(open_positions):
                    snap = snapshots[id(position)]
                    direction = 1 if position["transaction_type"] == "BUY" else -1
                    exit_reason = None
                    exit_price = None

                    if position["trail_pct"] > 0:
                        threshold = position["peak_premium"] * (
                            1 - position["trail_pct"] / 100
                            if position["transaction_type"] == "BUY"
                            else 1 + position["trail_pct"] / 100
                        )
                        if (position["transaction_type"] == "BUY" and snap["worst"] <= threshold) or (
                            position["transaction_type"] == "SELL" and snap["worst"] >= threshold
                        ):
                            exit_reason = "TrailingSL"
                            exit_price = threshold

                    if exit_reason is None and position["sl_pct"] > 0:
                        threshold = position["entry_price"] * (
                            1 - position["sl_pct"] / 100
                            if position["transaction_type"] == "BUY"
                            else 1 + position["sl_pct"] / 100
                        )
                        if (position["transaction_type"] == "BUY" and snap["worst"] <= threshold) or (
                            position["transaction_type"] == "SELL" and snap["worst"] >= threshold
                        ):
                            exit_reason = "StopLoss"
                            exit_price = threshold

                    if exit_reason is None and position["target_pct"] > 0:
                        threshold = position["entry_price"] * (
                            1 + position["target_pct"] / 100
                            if position["transaction_type"] == "BUY"
                            else 1 - position["target_pct"] / 100
                        )
                        if (position["transaction_type"] == "BUY" and snap["best"] >= threshold) or (
                            position["transaction_type"] == "SELL" and snap["best"] <= threshold
                        ):
                            exit_reason = "Target"
                            exit_price = threshold

                    if exit_reason is None and position["sl_points"] > 0:
                        threshold = position["entry_price"] - position["sl_points"] * direction
                        if _position_pnl(position, snap["worst"]) <= _position_pnl(position, threshold):
                            exit_reason = "SL_POINTS"
                            exit_price = threshold

                    if exit_reason is None and position["target_points"] > 0:
                        threshold = position["entry_price"] + position["target_points"] * direction
                        if _position_pnl(position, snap["best"]) >= _position_pnl(position, threshold):
                            exit_reason = "TARGET_POINTS"
                            exit_price = threshold

                    if exit_reason is None and position["sl_rupees"] > 0:
                        threshold = position["entry_price"] - (position["sl_rupees"] / position["qty"]) * direction
                        if _position_pnl(position, snap["worst"]) <= -position["sl_rupees"]:
                            exit_reason = "SL_RUPEES"
                            exit_price = threshold

                    if exit_reason is None and position["target_rupees"] > 0:
                        threshold = position["entry_price"] + (position["target_rupees"] / position["qty"]) * direction
                        if _position_pnl(position, snap["best"]) >= position["target_rupees"]:
                            exit_reason = "TARGET_RUPEES"
                            exit_price = threshold

                    if exit_reason is None and not is_daily and ct >= position["sqoff_time"]:
                        exit_reason = "SquareOff"
                        _, exit_price = _sqoff_exit_snapshot(position, cd, position["sqoff_time"], ts, snap["current"])

                    if exit_reason and exit_price is not None:
                        leg_exits.append((position, exit_price, exit_reason))

                if leg_exits:
                    for position, exit_price, exit_reason in leg_exits:
                        _close_selected_positions(
                            [position],
                            ts,
                            lambda _position, price=exit_price: price,
                            exit_reason,
                        )
                    exited_this_candle = True

        if not is_daily and (ct < mkt_open or ct >= mkt_close):
            equity.append({"time": str(ts)[:16], "equity": round(total_pnl, 2)})
            prev_prev_row = prev_row
            prev_row = row
            continue

        if not open_positions and not exited_this_candle:
            daily_loss_hit = max_daily_loss > 0 and daily_pnl <= -max_daily_loss
            if daily_loss_hit:
                max_daily_loss_hit = True
            if trades_today >= max_tpd or daily_loss_hit or skip_sessions_left > 0:
                equity.append({"time": str(ts)[:16], "equity": round(total_pnl, 2)})
                prev_prev_row = prev_row
                prev_row = row
                continue
            if cd in stub_sessions:
                equity.append({"time": str(ts)[:16], "equity": round(total_pnl, 2)})
                prev_prev_row = prev_row
                prev_row = row
                continue
            if not is_daily and ct < entry_earliest:
                equity.append({"time": str(ts)[:16], "equity": round(total_pnl, 2)})
                prev_prev_row = prev_row
                prev_row = row
                continue
            if pending_entry is not None:
                pending_entry["remaining"] -= 1
                if pending_entry["remaining"] <= 0:
                    signal_candle = dict(pending_entry["signal_candle"])
                    trade_entry_time = ts
                    positions = _build_entry_positions(float(row["open"]), ts, cd, lot_size)
                    if positions:
                        _open_entry_positions(positions)
                    else:
                        # A missing premium chain consumes the day's one signal;
                        # do not repeatedly re-enter on each following candle.
                        trades_today += 1
                equity.append({"time": str(ts)[:16], "equity": round(total_pnl, 2)})
                prev_prev_row = prev_row
                prev_row = row
                continue
            open_minutes = mkt_open.hour * 60 + mkt_open.minute
            current_minutes = ct.hour * 60 + ct.minute
            entry_boundary = is_daily or (
                current_minutes >= open_minutes and (current_minutes - open_minutes) % entry_evaluation_timeframe == 0
            )
            if (
                entry_boundary
                and signals_live
                and prev_row is not None
                and eval_condition_group(prev_row, entry_conditions, prev_prev_row)
            ):
                trade_group_id += 1
                next_signal_candle = {
                    "Signal_Candle_Open": float(prev_row["open"]),
                    "Signal_Candle_High": float(prev_row["high"]),
                    "Signal_Candle_Low": float(prev_row["low"]),
                    "Signal_Candle_Close": float(prev_row["close"]),
                }
                if entry_delay_candles > 0:
                    pending_entry = {"remaining": entry_delay_candles, "signal_candle": next_signal_candle}
                    signal_candle = dict(next_signal_candle)
                    trade_entry_time = ts
                else:
                    signal_candle = dict(next_signal_candle)
                    trade_entry_time = ts
                    positions = _build_entry_positions(float(row["open"]), ts, cd, lot_size)
                    if positions:
                        _open_entry_positions(positions)
                    else:
                        # See pending-entry handling above.
                        trades_today += 1

        equity.append({"time": str(ts)[:16], "equity": round(total_pnl, 2)})
        prev_prev_row = prev_row
        prev_row = row

    if open_positions and prev_row is not None:
        final_spot = float(prev_row["close"])
        if not is_daily:
            final_closures = []
            for position in list(open_positions):
                fallback_price = _position_price(position, final_spot, prev_row.name, "close")
                exit_ts, exit_price = _sqoff_exit_snapshot(
                    position,
                    prev_row.name.date(),
                    position.get("sqoff_time", combined_sqoff),
                    prev_row.name,
                    fallback_price,
                )
                exit_reason = "SquareOff" if exit_ts != prev_row.name else "EOD/Data"
                final_closures.append((position, exit_ts, exit_price, exit_reason))

            for position, exit_ts, exit_price, exit_reason in final_closures:
                _close_selected_positions(
                    [position],
                    exit_ts,
                    lambda _position, price=exit_price: price,
                    exit_reason,
                )
        else:
            _close_selected_positions(
                list(open_positions),
                prev_row.name,
                lambda position, spot=final_spot, exit_ts=prev_row.name: _position_price(
                    position, spot, exit_ts, "close"
                ),
                "EOD/Data",
            )

    if not trades:
        return {
            "status": "no_trades",
            "message": "No trades generated.",
            "trades": [],
            "equity": equity[-500:],
            "stats": {
                "capital_rejections": capital_rejections,
                "max_daily_loss_hit": max_daily_loss_hit,
            },
            "monthly": [],
            "day_of_week": [],
            "yearly": [],
        }

    summary_trades = _group_trade_records(trades)
    pnls = [trade["pnl"] for trade in summary_trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl <= 0]
    run = [trade["cumulative"] for trade in summary_trades]

    peak = run[0]
    max_drawdown_pct = 0.0
    max_drawdown_val = 0.0
    dd_days = 0
    max_dd_days = 0
    in_dd = False
    dd_start_idx = 0
    for idx, cumulative in enumerate(run):
        peak = max(peak, cumulative)
        drawdown_val = peak - cumulative
        max_drawdown_val = max(max_drawdown_val, drawdown_val)
        if peak != 0:
            max_drawdown_pct = max(max_drawdown_pct, drawdown_val / abs(peak) * 100)
        if drawdown_val > 0:
            if not in_dd:
                in_dd = True
                dd_start_idx = idx
            dd_days = idx - dd_start_idx + 1
            max_dd_days = max(max_dd_days, dd_days)
        else:
            in_dd = False
            dd_days = 0

    win_streak = 0
    loss_streak = 0
    current_win = 0
    current_loss = 0
    for pnl in pnls:
        if pnl > 0:
            current_win += 1
            current_loss = 0
            win_streak = max(win_streak, current_win)
        else:
            current_loss += 1
            current_win = 0
            loss_streak = max(loss_streak, current_loss)

    roi_pct = round(sum(pnls) / initial_capital * 100, 2) if initial_capital > 0 else 0

    durations_min = []
    for trade in summary_trades:
        try:
            start_ts = pd.Timestamp(trade["entry_time"])
            end_ts = pd.Timestamp(trade["exit_time"])
            durations_min.append((end_ts - start_ts).total_seconds() / 60)
        except Exception:
            pass
    avg_duration_min = round(float(np.mean(durations_min)), 1) if durations_min else 0
    if avg_duration_min < 60:
        avg_duration_str = f"{int(avg_duration_min)}m"
    else:
        hours, minutes = divmod(int(avg_duration_min), 60)
        avg_duration_str = f"{hours}h {minutes}m"

    pnl_arr = np.array(pnls)
    if len(pnl_arr) > 1 and np.std(pnl_arr) > 0:
        sharpe_ratio = round(float(np.mean(pnl_arr) / np.std(pnl_arr) * np.sqrt(252)), 2)
    else:
        sharpe_ratio = 0.0

    try:
        first_dt = pd.Timestamp(summary_trades[0]["entry_time"])
        last_dt = pd.Timestamp(summary_trades[-1]["exit_time"])
        years = max(0.01, (last_dt - first_dt).days / 365.25)
        annual_return = sum(pnls) / years
        calmar_ratio = round(annual_return / max_drawdown_val, 2) if max_drawdown_val > 0 else 999.0
    except Exception:
        calmar_ratio = 0.0

    win_rate = len(wins) / len(pnls) if pnls else 0
    avg_win = float(np.mean(wins)) if wins else 0
    avg_loss = abs(float(np.mean(losses))) if losses else 0
    expectancy = round(win_rate * avg_win - (1.0 - win_rate) * avg_loss, 2)

    risk_per_trade = round(float(np.std(pnls)), 2) if len(pnls) > 1 else 0
    risk_per_trade_pct = round(risk_per_trade / initial_capital * 100, 2) if initial_capital > 0 else 0

    stats = {
        "total_trades": len(summary_trades),
        "closed_legs": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(win_rate * 100, 2),
        "total_pnl": round(sum(pnls), 2),
        "avg_profit": round(avg_win, 2),
        "avg_loss": round(float(np.mean(losses)) if losses else 0, 2),
        "max_drawdown": round(max_drawdown_pct, 2),
        "max_drawdown_val": round(max_drawdown_val, 2),
        "max_drawdown_days": max_dd_days,
        "roi_pct": roi_pct,
        "profit_factor": round(sum(wins) / abs(sum(losses)) if losses and abs(sum(losses)) > 0 else 999.0, 2),
        "max_profit": round(max(pnls), 2),
        "max_loss": round(min(pnls), 2),
        "win_streak": win_streak,
        "loss_streak": loss_streak,
        "risk_per_trade": risk_per_trade,
        "risk_per_trade_pct": risk_per_trade_pct,
        "sharpe_ratio": sharpe_ratio,
        "calmar_ratio": calmar_ratio,
        "expectancy": expectancy,
        "avg_duration": avg_duration_str,
        "avg_duration_min": avg_duration_min,
        "total_fees": round(total_fees, 2),
        "initial_capital": initial_capital,
        "net_pnl_after_fees": round(sum(pnls), 2),
        "max_daily_loss_hit": max_daily_loss_hit,
        "capital_rejections": capital_rejections,
        "cooldown_sessions_skipped": cooldown_sessions_skipped,
    }

    monthly = {}
    for trade in summary_trades:
        month_key = str(trade["entry_time"])[:7]
        monthly[month_key] = monthly.get(month_key, 0) + trade["pnl"]

    day_map = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday"}
    day_stats = {}
    for trade in summary_trades:
        try:
            day_name = day_map[datetime.strptime(str(trade["entry_time"])[:10], "%Y-%m-%d").weekday()]
        except Exception:
            day_name = "Unknown"
        if day_name not in day_stats:
            day_stats[day_name] = {"hits": 0, "miss": 0, "profit": 0, "loss": 0}
        if trade["pnl"] > 0:
            day_stats[day_name]["hits"] += 1
            day_stats[day_name]["profit"] += trade["pnl"]
        else:
            day_stats[day_name]["miss"] += 1
            day_stats[day_name]["loss"] += trade["pnl"]

    yearly = {}
    for trade in summary_trades:
        year_key = str(trade["entry_time"])[:4]
        if year_key not in yearly:
            yearly[year_key] = {"hits": 0, "miss": 0, "profit": 0, "loss": 0}
        if trade["pnl"] > 0:
            yearly[year_key]["hits"] += 1
            yearly[year_key]["profit"] += trade["pnl"]
        else:
            yearly[year_key]["miss"] += 1
            yearly[year_key]["loss"] += trade["pnl"]

    step = max(1, len(equity) // 800)
    return {
        "status": "success",
        "trades": trades,
        "equity": equity[::step],
        "stats": stats,
        "monthly": [{"month": key, "pnl": round(value, 2)} for key, value in sorted(monthly.items())],
        "day_of_week": [{"day": key, **value} for key, value in day_stats.items()],
        "yearly": [{"year": key, **value} for key, value in sorted(yearly.items())],
    }
