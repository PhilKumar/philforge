"""My exits against the rule's: the live CE/PE books, trade by trade.

Phil, 2026-10-31: "I need to make a note of this on my site in assets section
what really happened on Live with 2 lots and 3 lots on expiry every month from
september comparing with my exits in detail".

Every live trade has a real entry. Where the book closed it itself (exit
signal, stop, square-off) the rule's exit IS what happened. Where Phil closed
it by hand -- the Exit button, or straight in the broker's app -- this walks
the same position forward from the same entry, on the book's own exit
conditions, its stop and its square-off, and says where the rule would have
let go:

- the exit conditions are judged by the backtest's own evaluator on NIFTY
  candles built by the engine's own indicator code, at each 5m close, exactly
  as the live engine does (checked against every October live and paper exit:
  09:25, 11:00, 11:20, 14:15, 14:30 all land on the same bar);
- the stop is the leg's per cent below the real entry, judged on the
  contract's own 5m candles; a candle that opens through it fills at its open;
- the square-off is the leg's time, at the contract's price then.

The rule's exit is priced off 5m candles, not a fill, so it can sit a few
rupees from what an order would have got.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from html import escape

import pandas as pd

# Phil closed these by hand: the site's Exit button, or the broker's own app.
MANUAL_REASONS = {"MANUAL_EXIT", "BROKER_MANUAL_EXIT"}

_HOW = {
    "MANUAL_EXIT": "Exit button",
    "BROKER_MANUAL_EXIT": "closed in Dhan",
    "EXIT_SIGNAL": "exit signal",
    "STOP_LOSS": "stop",
    "SQUARE_OFF": "square-off",
    "TARGET": "target",
    "TRAILING_SL": "trailing stop",
}


def how(reason: str) -> str:
    return _HOW.get(str(reason or "").upper(), str(reason or "").replace("_", " ").lower() or "-")


def _naive_ist(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
    try:
        stamp = pd.Timestamp(str(value))
    except (ValueError, TypeError):
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("Asia/Kolkata").tz_localize(None)
    return stamp.to_pydatetime()


def _hhmm(value) -> dt_time | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        hour, minute = text.split(":")[:2]
        return dt_time(int(hour), int(minute))
    except ValueError:
        return None


def round_charges(buy: float, sell: float, quantity: int, lots: float) -> float:
    """The live engine's own charge schedule for one round trip."""
    from engine.live import statutory_round_charges

    return statutory_round_charges(entry_premium=buy, exit_premium=sell, quantity=quantity, lots=lots, option_type="CE")


def option_bars(candles: list[dict]) -> pd.DataFrame:
    """A kept trade chart ({t, o, h, l, c} in epoch seconds) as IST-naive bars."""
    rows = []
    for c in candles or []:
        try:
            rows.append(
                {
                    "ts": _naive_ist(int(c["t"])),
                    "open": float(c["o"]),
                    "high": float(c["h"]),
                    "low": float(c["l"]),
                    "close": float(c["c"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    return pd.DataFrame(rows).set_index("ts").sort_index()


def _say(cond: dict) -> str:
    """One exit condition, the way Phil reads it."""

    def name(key: str) -> str:
        key = str(key or "")
        if key == "current_close":
            return "NIFTY close"
        if key.startswith("CPR_"):
            return key[4:]
        if key.startswith("Supertrend_"):
            parts = key.split("_")
            if len(parts) >= 4:
                return f"Supertrend {parts[1]}/{parts[2]} ({parts[3]})"
        return key.replace("_", " ")

    verb = str(cond.get("operator") or "").replace("_", " ").replace("crosses", "crossed")
    return f"{name(cond.get('left'))} {verb} {name(cond.get('right'))}"


def stop_level(trade: dict) -> float:
    entry = float(trade.get("entry_premium") or 0)
    sl_pct = float(trade.get("sl_pct") or 0)
    return round(entry * (1 - sl_pct / 100.0), 2) if entry > 0 and sl_pct > 0 else 0.0


def was_its_own_stop(trade: dict) -> bool:
    """A "closed in Dhan" that filled at the book's own resting stop.

    Until 6dac7f39 (25-Sep-2026) the engine booked ANY close it had not sent as
    BROKER_MANUAL_EXIT, its own stop included: 25-Sep's 23300PE "manual" exit
    was the stop at 194.08 filling at 193.85. Nobody closed that by hand.
    """
    if str(trade.get("exit_reason") or "").upper() != "BROKER_MANUAL_EXIT":
        return False
    stop = stop_level(trade)
    exit_px = float(trade.get("exit_premium") or 0)
    return bool(stop and exit_px) and abs(exit_px - stop) <= stop * 0.015


def twin_exit(trade: dict, paper: list[dict]) -> dict | None:
    """The paper twin's exit, when the same book traded the same contract from
    the same entry on paper.

    The paper book runs the same rules on the contract's live prices, so where
    it held the same position it says, from the real tape, where the rule let
    go. That beats a replay on an expired weekly's candles, which Dhan only
    serves from its rolling archive and which can be wrong: 10-Sep's archive
    chart dips to the stop at 11:10, while the paper twin -- on the real
    prices -- held to the square-off.
    """
    entry_at = _naive_ist(trade.get("entry_time"))
    if entry_at is None:
        return None
    strike = int(float(trade.get("strike") or 0))
    side = str(trade.get("option_type") or "").upper()
    for twin in paper or []:
        if str(twin.get("book_name") or "") != str(trade.get("book_name") or ""):
            continue
        if int(float(twin.get("strike") or 0)) != strike or str(twin.get("option_type") or "").upper() != side:
            continue
        if str(twin.get("expiry") or "") != str(trade.get("expiry") or ""):
            continue
        twin_at, twin_out = _naive_ist(twin.get("entry_time")), _naive_ist(twin.get("exit_time"))
        if twin_at is None or twin_out is None or abs((twin_at - entry_at).total_seconds()) > 300:
            continue
        reason = str(twin.get("exit_reason") or "").upper()
        price = float(twin.get("exit_premium") or 0)
        when = twin_out
        if reason == "STOP_LOSS":
            # Its stop sat under ITS entry; this trade's sits under the real one.
            price = stop_level(trade) or price
        sqoff = _hhmm(trade.get("sqoff_time"))
        if reason == "SQUARE_OFF" and sqoff and twin_out.time() > sqoff:
            when = datetime.combine(twin_out.date(), sqoff)
        if price <= 0:
            return None
        why = {"STOP_LOSS": stop_reason(trade), "SQUARE_OFF": "square-off"}.get(reason, how(reason))
        return {
            "time": when.strftime("%H:%M"),
            "price": round(price, 2),
            "reason": reason,
            "why": why,
            "source": "paper twin",
        }
    return None


def stop_reason(trade: dict) -> str:
    return f"{float(trade.get('sl_pct') or 0):g}% stop at {stop_level(trade):,.2f}"


def candles_match(trade: dict, bars: pd.DataFrame, tf: int = 5) -> bool:
    """The candles are this contract's: the real entry price is inside the
    entry candle. An archive chart of the wrong strike fails this."""
    entry_at = _naive_ist(trade.get("entry_time"))
    entry = float(trade.get("entry_premium") or 0)
    if entry_at is None or entry <= 0 or bars is None or bars.empty:
        return False
    start = pd.Timestamp(entry_at).floor(f"{tf}min")
    bar = bars[(bars.index >= start - pd.Timedelta(minutes=tf)) & (bars.index < start + pd.Timedelta(minutes=tf))]
    if bar.empty:
        return False
    return float(bar["low"].min()) * 0.98 <= entry <= float(bar["high"].max()) * 1.02


def rule_exit(trade: dict, strategy: dict, index_1m: pd.DataFrame, bars: pd.DataFrame) -> dict | None:
    """Where the book's own rules close this trade, from its real entry.

    `index_1m` is NIFTY 1m from a week before the trade (CPR needs the prior
    day, the averages need warm-up); `bars` is the contract's 5m candles.
    None when the candles do not cover the trade.
    """
    from engine.backtest import eval_condition, eval_condition_group
    from engine.indicators import compute_dynamic_indicators, infer_execution_timeframe, normalize_strategy_indicators

    entry_at = _naive_ist(trade.get("entry_time"))
    entry = float(trade.get("entry_premium") or 0)
    if entry_at is None or entry <= 0 or index_1m is None or index_1m.empty or bars is None or bars.empty:
        return None
    if not candles_match(trade, bars):
        return None
    exits = list(strategy.get("exit_conditions") or [])
    entries = list(strategy.get("entry_conditions") or [])
    indicators = normalize_strategy_indicators(
        strategy.get("indicators") or [], entry_conditions=entries, exit_conditions=exits
    )
    tf = int(strategy.get("execution_timeframe_minutes") or 0) or infer_execution_timeframe(
        indicators, entries, default=int(strategy.get("timeframe_minutes") or 5)
    )
    frame = compute_dynamic_indicators(
        index_1m,
        indicators,
        default_timeframe_minutes=tf,
        source_timeframe_minutes=1,
        execution_timeframe_minutes=tf,
    )
    step = timedelta(minutes=tf)
    day = entry_at.date()
    session = frame[frame.index.date == day]
    if session.empty:
        return None

    stop = stop_level(trade)
    target_pct = float(trade.get("target_pct") or 0)
    target = round(entry * (1 + target_pct / 100.0), 2) if target_pct > 0 else 0.0
    sqoff = _hhmm(trade.get("sqoff_time")) or _hhmm(strategy.get("market_close")) or dt_time(15, 10)
    cutoff = _hhmm(strategy.get("signal_cutoff_time"))
    sqoff_at = datetime.combine(day, sqoff)

    def price_at(moment: datetime) -> float | None:
        """The contract's last close at or before this moment."""
        done = bars[bars.index + step <= moment]
        return float(done["close"].iloc[-1]) if not done.empty else None

    # The entry happens at a bar's close, so the first bar the exit can act on
    # is the one starting then.
    first = pd.Timestamp(entry_at).floor(f"{tf}min").to_pydatetime()
    prev = None
    for start, row in session.iterrows():
        start = start.to_pydatetime()
        if start < first:
            prev = row
            continue
        close_at = start + step
        if start >= sqoff_at:
            px = price_at(sqoff_at)
            return {
                "time": sqoff_at.strftime("%H:%M"),
                "price": px,
                "reason": "SQUARE_OFF",
                "why": "square-off",
                "source": "replay",
            }
        bar = bars[(bars.index >= start) & (bars.index < close_at)]
        if not bar.empty:
            lo, hi, op = float(bar["low"].min()), float(bar["high"].max()), float(bar["open"].iloc[0])
            if stop and lo <= stop:
                return {
                    "time": start.strftime("%H:%M"),
                    "price": round(min(stop, op), 2),
                    "reason": "STOP_LOSS",
                    "why": stop_reason(trade),
                    "source": "replay",
                }
            if target and hi >= target:
                return {
                    "time": start.strftime("%H:%M"),
                    "price": round(max(target, op), 2),
                    "reason": "TARGET",
                    "why": f"{target_pct:g}% target at {target:,.2f}",
                    "source": "replay",
                }
        may_signal = cutoff is None or close_at.time() <= cutoff
        if prev is not None and exits and may_signal and eval_condition_group(row, exits, prev):
            fired = [_say(c) for c in exits if eval_condition(row, c, prev)]
            px = price_at(close_at)
            if px is not None:
                return {
                    "time": close_at.strftime("%H:%M"),
                    "price": px,
                    "reason": "EXIT_SIGNAL",
                    "why": "; ".join(fired) or "exit signal",
                    "source": "replay",
                }
        prev = row
        if close_at >= sqoff_at:
            px = price_at(sqoff_at)
            return {
                "time": sqoff_at.strftime("%H:%M"),
                "price": px,
                "reason": "SQUARE_OFF",
                "why": "square-off",
                "source": "replay",
            }
    return None


def review_row(trade: dict, rule: dict | None) -> dict:
    """One trade: what happened, and what the rule would have done."""
    entry_at = _naive_ist(trade.get("entry_time"))
    exit_at = _naive_ist(trade.get("exit_time"))
    qty = int(float(trade.get("quantity") or 0))
    lots = float(trade.get("lots") or 0)
    entry = float(trade.get("entry_premium") or 0)
    mine = round(float(trade.get("pnl") or 0), 2)
    reason = str(trade.get("exit_reason") or "").upper()
    own_stop = was_its_own_stop(trade)
    manual = reason in MANUAL_REASONS and not own_stop
    row = {
        "day": entry_at.date().isoformat() if entry_at else "",
        "book": str(trade.get("book_name") or ""),
        "side": str(trade.get("book_side") or trade.get("option_type") or "").upper(),
        "contract": f"{int(float(trade.get('strike') or 0))} {str(trade.get('option_type') or '').upper()}",
        "expiry": str(trade.get("expiry") or ""),
        "lots": lots,
        "qty": qty,
        "entry_time": entry_at.strftime("%H:%M") if entry_at else "",
        "entry": entry,
        "my_time": exit_at.strftime("%H:%M") if exit_at else "",
        "my_price": float(trade.get("exit_premium") or 0),
        "my_how": "its own stop (booked as closed in Dhan)" if own_stop else how(reason),
        "mine": mine,
        "manual": manual,
    }
    row["rule_source"] = ""
    if not manual:
        row.update(rule_time=row["my_time"], rule_price=row["my_price"], rule_why=row["my_how"], rule=mine)
    elif rule and rule.get("price"):
        px = float(rule["price"])
        net = (px - entry) * qty - round_charges(entry, px, qty, lots)
        row.update(rule_time=rule.get("time", ""), rule_price=px, rule_why=rule.get("why") or how(rule.get("reason")))
        row["rule_source"] = str(rule.get("source") or "")
        row["rule"] = round(net, 2)
    else:
        row.update(
            rule_time="", rule_price=0.0, rule_why="not worked out yet: no candles that match this contract", rule=None
        )
    return row


def by_month(rows: list[dict]) -> list[dict]:
    """Months newest first, each with its trades oldest first and its totals."""
    months: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda r: (r["day"], r["entry_time"])):
        months.setdefault(row["day"][:7], []).append(row)
    out = []
    for key in sorted(months, reverse=True):
        trades = months[key]
        out.append({"month": key, "trades": trades, **totals(trades)})
    return out


def totals(rows: list[dict]) -> dict:
    known = [r for r in rows if r.get("rule") is not None]
    mine = round(sum(r["mine"] for r in rows), 2)
    rule = round(sum(r["rule"] for r in known), 2)
    manual = [r for r in known if r["manual"]]
    return {
        "mine": mine,
        "rule": rule,
        "pending": len(rows) - len(known),
        "count": len(rows),
        "manual": len([r for r in rows if r["manual"]]),
        "i_won": len([r for r in manual if r["mine"] > r["rule"]]),
        "by_side": {
            side: {
                "mine": round(sum(r["mine"] for r in rows if r["side"] == side), 2),
                "rule": round(sum(r["rule"] for r in known if r["side"] == side), 2),
            }
            for side in ("CE", "PE")
        },
    }


# ── The document ─────────────────────────────────────────────────────────
def _inr(value: float | None, sign: bool = True) -> str:
    if value is None:
        return "–"
    v = round(float(value))
    digits = f"{abs(v)}"
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    mark = "+" if v > 0 and sign else ("−" if v < 0 else "")
    return f"{mark}₹{digits}"


def _tone(value: float | None) -> str:
    if value is None:
        return "flat"
    return "pos" if value > 0 else ("neg" if value < 0 else "flat")


def _month_name(key: str) -> str:
    return datetime.strptime(key + "-01", "%Y-%m-%d").strftime("%B %Y")


def _day_name(day: str) -> str:
    return datetime.strptime(day, "%Y-%m-%d").strftime("%a %d %b")


_STYLE = """
:root {
  color-scheme: light dark;
  --paper:#eef3f8; --surface:#fafcfe; --surface-2:#eef3f8;
  --ink:#182436; --ink-2:#33415a; --muted:#596a7f;
  --line:rgba(38,65,93,.16); --line-2:rgba(38,65,93,.10);
  --grid:rgba(31,55,82,.055);
  --accent:#1f5fae; --accent-rgb:31,95,174; --accent-soft:rgba(31,95,174,.09);
  --pos:#146B4C; --neg:#A2382A; --flat:#5D6874;
  --shadow:0 1px 2px rgba(15,21,28,.05), 0 8px 24px rgba(15,21,28,.05);
  --sans:"Outfit",ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper:#070b13; --surface:#0c1320; --surface-2:#101a2a;
    --ink:#e5ecf6; --ink-2:#bcc8d4; --muted:#8b98aa;
    --line:rgba(126,157,196,.18); --line-2:rgba(126,157,196,.11);
    --grid:rgba(104,138,178,.045);
    --accent:#75adff; --accent-rgb:117,173,255; --accent-soft:rgba(117,173,255,.10);
    --pos:#43C48D; --neg:#E67D6B; --flat:#8792A0;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px rgba(0,0,0,.35);
  }
}
:root[data-theme="dark"] {
  --paper:#070b13; --surface:#0c1320; --surface-2:#101a2a;
  --ink:#e5ecf6; --ink-2:#bcc8d4; --muted:#8b98aa;
  --line:rgba(126,157,196,.18); --line-2:rgba(126,157,196,.11);
  --grid:rgba(104,138,178,.045);
  --accent:#75adff; --accent-rgb:117,173,255; --accent-soft:rgba(117,173,255,.10);
  --pos:#43C48D; --neg:#E67D6B; --flat:#8792A0;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px rgba(0,0,0,.35);
}
* { box-sizing:border-box; }
body { margin:0; color:var(--ink); font-family:var(--sans); font-size:15px; line-height:1.55;
  -webkit-text-size-adjust:100%; background-color:var(--paper);
  background-image:linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:64px 64px,64px 64px; }
.wrap { width:min(1280px, calc(100% - 32px)); margin:0 auto; padding:40px 0 72px; }
.eyebrow { margin:0 0 14px; font-family:var(--mono); font-size:9px; font-weight:600; letter-spacing:.13em;
  text-transform:uppercase; color:var(--muted); }
.eyebrow b { color:var(--accent); margin-right:8px; font-weight:600; }
h1 { font-size:clamp(32px,4.6vw,56px); line-height:1.02; letter-spacing:-.045em; margin:0 0 14px; font-weight:600; }
h2 { font-size:20px; letter-spacing:-.015em; margin:0; font-weight:700; }
p { margin:0 0 12px; max-width:72ch; color:var(--ink-2); }
.num { font-family:var(--mono); font-variant-numeric:tabular-nums; }
.pos { color:var(--pos); } .neg { color:var(--neg); } .flat { color:var(--flat); }
.chips { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; margin:22px 0 34px; }
.chip { background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:14px 16px; box-shadow:var(--shadow); }
.chip span { display:block; font-family:var(--mono); font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }
.chip strong { display:block; font-family:var(--mono); font-size:22px; margin-top:4px; letter-spacing:-.02em; }
.chip small { display:block; color:var(--muted); font-size:12px; margin-top:2px; }
.month { background:var(--surface); border:1px solid var(--line); border-radius:18px; box-shadow:var(--shadow);
  padding:20px 20px 8px; margin:0 0 22px; }
.month-head { display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:10px 18px; margin-bottom:14px; }
.month-sum { display:flex; flex-wrap:wrap; gap:6px 18px; font-family:var(--mono); font-size:12.5px; color:var(--muted); }
.month-sum b { font-weight:700; }
.scroll { overflow-x:auto; margin:0 -20px; padding:0 20px 12px; }
table { border-collapse:collapse; width:100%; min-width:900px; font-size:13px; }
th { text-align:left; font-family:var(--mono); font-size:10px; font-weight:600; letter-spacing:.07em; text-transform:uppercase;
  color:var(--muted); padding:8px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }
td { padding:9px 10px; border-bottom:1px solid var(--line-2); vertical-align:top; }
.nw, td.r { white-space:nowrap; }
td.r, th.r { text-align:right; }
tr.split td { background:var(--surface-2); font-weight:600; }
.tag { display:inline-block; font-family:var(--mono); font-size:10px; font-weight:700; letter-spacing:.06em;
  padding:2px 7px; border-radius:999px; border:1px solid var(--line); color:var(--muted); }
.tag.ce { color:var(--pos); border-color:rgba(67,196,141,.35); }
.tag.pe { color:var(--neg); border-color:rgba(230,125,107,.35); }
.tag.hand { color:var(--accent); border-color:rgba(var(--accent-rgb),.35); background:var(--accent-soft); font-size:9px; padding:1px 6px; margin-right:4px; }
.sub { display:block; color:var(--muted); font-size:11.5px; margin-top:2px; }
.notes { background:var(--surface); border:1px solid var(--line); border-radius:18px; padding:20px; margin-top:30px; }
.notes li { color:var(--ink-2); margin:0 0 8px; max-width:90ch; }
@media (max-width:640px) {
  .wrap { width:calc(100% - 32px); padding-top:24px; }
  .chips { grid-template-columns:1fr 1fr; gap:8px; }
  .chip { padding:12px; }
  .chip strong { font-size:18px; }
  .month { padding:16px 14px 8px; }
  /* Each trade a card on a phone: what it was on top, then mine beside the rule. */
  .scroll { overflow:visible; margin:0; padding:0; }
  table, tbody { display:block; min-width:0; }
  thead { display:none; }
  tbody tr { display:grid; grid-template-columns:1fr 1fr; gap:8px 12px; padding:12px 0;
    border-bottom:1px solid var(--line);
    grid-template-areas:"day trade" "lots entry" "mx rx" "mine rule" "gap gap"; }
  td { display:block; padding:0; border:0; text-align:left !important; }
  td::before { content:attr(data-l); display:block; font-family:var(--mono); font-size:9px;
    letter-spacing:.08em; text-transform:uppercase; color:var(--muted); margin-bottom:2px; }
  td.c-day { grid-area:day; font-weight:700; }
  td.c-day::before, td.c-trade::before { display:none; }
  td.c-trade { grid-area:trade; }
  td.c-lots { grid-area:lots; }
  td.c-entry { grid-area:entry; }
  td.c-mine-exit { grid-area:mx; }
  td.c-rule-exit { grid-area:rx; }
  td.c-mine { grid-area:mine; font-size:15px; }
  td.c-rule { grid-area:rule; font-size:15px; }
  td.c-gap { grid-area:gap; }
}
"""


def render(months: list[dict], *, generated: str, broker: str = "Dhan") -> str:
    """The document fragment the Assets tearsheet frame serves."""
    every = [t for m in months for t in m["trades"]]
    all_t = totals(every) if every else totals([])
    diff = round(all_t["mine"] - all_t["rule"], 2)
    first = _month_name(months[-1]["month"]) if months else "September 2026"
    parts = [
        "<title>My exits vs the rule</title>",
        f"<style>{_STYLE}</style>",
        '<div class="wrap">',
        f'<p class="eyebrow"><b>Live · {escape(broker)}</b>CE + PE books · real money · since {escape(first)}</p>',
        "<h1>My exits vs the rule</h1>",
        "<p>Every trade the live CE and PE books took on "
        f"{escape(broker)}, at the size it really traded (2 lots, 3 on expiry). <b>Mine</b> is what the account "
        "booked. <b>Rule</b> is what the book would have booked from the same entry had nobody touched it: its own "
        "exit signal, its stop, or its square-off. Where the book closed a trade itself, the two are the same.</p>",
        '<div class="chips">',
        f'<div class="chip"><span>Mine</span><strong class="num {_tone(all_t["mine"])}">{_inr(all_t["mine"])}</strong>'
        f"<small>{all_t['count']} trades</small></div>",
        f'<div class="chip"><span>Rule</span><strong class="num {_tone(all_t["rule"])}">{_inr(all_t["rule"])}</strong>'
        f"<small>{'all worked out' if not all_t['pending'] else str(all_t['pending']) + ' not worked out yet'}</small></div>",
        f'<div class="chip"><span>My exits added</span><strong class="num {_tone(diff)}">{_inr(diff)}</strong>'
        f"<small>mine minus rule</small></div>",
        f'<div class="chip"><span>Closed by hand</span><strong class="num">{all_t["manual"]}</strong>'
        f"<small>beat the rule on {all_t['i_won']}</small></div>",
        "</div>",
    ]
    if not months:
        parts.append('<div class="month"><p>No live trades yet.</p></div>')
    for month in months:
        mdiff = round(month["mine"] - month["rule"], 2)
        ce, pe = month["by_side"]["CE"], month["by_side"]["PE"]
        parts += [
            f'<section class="month" id="m-{month["month"]}">',
            '<div class="month-head">',
            f"<h2>{_month_name(month['month'])}</h2>",
            '<div class="month-sum">',
            f'<span>Mine <b class="{_tone(month["mine"])}">{_inr(month["mine"])}</b></span>',
            f'<span>Rule <b class="{_tone(month["rule"])}">{_inr(month["rule"])}</b></span>',
            f'<span>My exits added <b class="{_tone(mdiff)}">{_inr(mdiff)}</b></span>',
            f"<span>CE {_inr(ce['mine'])} / {_inr(ce['rule'])}</span>",
            f"<span>PE {_inr(pe['mine'])} / {_inr(pe['rule'])}</span>",
            f"<span>{month['count']} trades · {month['manual']} by hand</span>",
            "</div></div>",
            '<div class="scroll"><table>',
            "<thead><tr><th>Day</th><th>Trade</th><th class=r>Lots</th><th>Entry</th>"
            "<th>My exit</th><th class=r>Mine</th><th>Rule's exit</th><th class=r>Rule</th>"
            "<th class=r>Mine − rule</th></tr></thead><tbody>",
        ]
        for t in month["trades"]:
            gap = None if t["rule"] is None else round(t["mine"] - t["rule"], 2)
            side = t["side"].lower()
            hand = '<span class="tag hand">BY HAND</span>' if t["manual"] else ""
            lots = f"{t['lots']:g}" if t["lots"] else "–"
            rule_cell = (
                f'<span class="nw">{escape(t["rule_time"])} @ <span class="num">{t["rule_price"]:,.2f}</span></span>'
                f'<span class="sub">{escape(t["rule_why"])}'
                f"{' · from the paper twin' if t.get('rule_source') == 'paper twin' else ''}</span>"
                if t["rule"] is not None
                else f'<span class="sub">{escape(t["rule_why"])}</span>'
            )
            parts.append(
                f'<tr class="{"by-hand" if t["manual"] else ""}">'
                f'<td class="nw c-day" data-l="Day">{_day_name(t["day"])}</td>'
                f'<td class="nw c-trade" data-l="Trade"><span class="tag {side}">{escape(t["side"])}</span> {escape(t["contract"])}'
                f'<span class="sub">expiry {escape(t["expiry"])}</span></td>'
                f'<td class="r num c-lots" data-l="Lots">{lots}</td>'
                f'<td class="nw c-entry" data-l="Entry">{escape(t["entry_time"])} @ <span class="num">{t["entry"]:,.2f}</span></td>'
                f'<td class="c-mine-exit" data-l="My exit"><span class="nw">{escape(t["my_time"])} @ <span class="num">{t["my_price"]:,.2f}</span></span>'
                f'<span class="sub">{hand}{escape(t["my_how"])}</span></td>'
                f'<td class="r num c-mine {_tone(t["mine"])}" data-l="Mine">{_inr(t["mine"])}</td>'
                f'<td class="c-rule-exit" data-l="Rule\'s exit">{rule_cell}</td>'
                f'<td class="r num c-rule {_tone(t["rule"])}" data-l="Rule">{_inr(t["rule"])}</td>'
                f'<td class="r num c-gap {_tone(gap)}" data-l="Mine − rule">{_inr(gap)}</td>'
                "</tr>"
            )
        parts.append("</tbody></table></div></section>")
    parts += [
        '<div class="notes"><h2>How the rule column is worked out</h2><ul>',
        "<li>The entry is the real one: same contract, same time, same price, same lots.</li>",
        "<li>A trade the book closed itself keeps its real exit in both columns. Before 25 Sep 2026 the engine "
        'booked its own stop filling as "closed in Dhan"; a close at the stop\'s price is counted as the stop.</li>',
        "<li>Where the paper copy of the same book held the same contract from the same entry, its exit is used: "
        "it ran the same rules on the real prices. Its stop is moved to sit under the real entry.</li>",
        "<li>Otherwise a trade closed by hand is walked forward from that entry on the book's own exit conditions, judged "
        "at each 5-minute close by the backtest's own evaluator on NIFTY, with the engine's own indicators. The "
        "first of these wins: the leg's stop (per cent below the real entry, on the contract's 5-minute candles), "
        "the exit signal, or the square-off.</li>",
        "<li>The rule's price is the contract's 5-minute candle at that moment, not an order fill, so it can be a "
        "few rupees off what an order would have got. Candles that do not contain the real entry price are not "
        "this contract's and are not used. Charges use the live engine's own schedule.</li>",
        f"<li>{escape(broker)} books only. Worked out {escape(generated)}.</li>",
        "</ul></div></div>",
    ]
    return "\n".join(parts)
