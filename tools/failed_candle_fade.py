"""tools/failed_candle_fade.py -- the two playlist ideas that held up, as one trade.

tools/playlist_claims.py tested seven of his claims on two years of NIFTY and
two survived: the opening hour is where the day's extremes are made, and a
close beyond a level with under half the candle beyond it (a FAILED candle,
T144) falls back more often than a full break. This asks the next question:
is that worth money?

THE RULE, fixed before any result was seen:

  * 5-minute NIFTY. A candle closes beyond the last 12 bars' high (or low),
    and under half of its range is beyond that level -- a failed candle.
  * Fade it at the close: sell a failed break up, buy a failed break down.
  * Stop at that candle's own extreme. Target: the same distance the other way
    (1:1). Out after 6 candles (30 minutes) if neither is hit, and never
    carried past 15:25.
  * One position at a time. Cost charged in index points per round trip.

Index points only -- the question is whether the edge exists after costs,
before any option or future is chosen to express it. Results are split into
the first and second halves of the data: a rule that only works in the half
it was looked at in has not been tested.

    python3 tools/failed_candle_fade.py --data DIR
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playlist_claims import load  # noqa: E402

LOOKBACK = 12
HOLD = 6
LAST_ENTRY = "15:00"
FLAT_BY = "15:25"


def trades(
    df: pd.DataFrame, window: tuple[str, str] | None, failed: bool = True, rr: float = 1.0, pad: bool = False
) -> pd.DataFrame:
    """Every fade the rule takes. `window` limits entries to a time of day;
    `failed=False` fades FULL breaks instead -- the control, which the idea
    says should lose. `pad=True` moves the stop one average candle range
    (last 12 bars) beyond the candle's extreme: the candle's own high sits a
    median 3 points from the close, and noise takes that out almost every time."""
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    hm = df.index.strftime("%H:%M").to_numpy()
    day = df.index.date
    out = []
    i = LOOKBACK
    n = len(df)
    while i < n - 1:
        # The level is the last 12 candles' high/low even when some are from the
        # previous session -- exactly as playlist_claims measured it. Requiring
        # 12 candles of TODAY would make the first hour (12 candles) untradeable.
        if hm[i] > LAST_ENTRY or (window and not (window[0] <= hm[i] < window[1])):
            i += 1
            continue
        rng = h[i] - lo[i]
        for side, level in ((1, h[i - LOOKBACK : i].max()), (-1, lo[i - LOOKBACK : i].min())):
            if rng <= 0 or side * (c[i] - level) <= 0 or side * (c[i - 1] - level) > 0:
                continue
            beyond = (h[i] - max(level, lo[i])) if side > 0 else (min(level, h[i]) - lo[i])
            if (beyond / rng < 0.5) != failed:
                continue
            # fade: the trade is AGAINST the break
            d = -side
            entry = c[i]
            stop = h[i] if d < 0 else lo[i]
            if pad:
                stop += -d * float(np.mean(h[i - LOOKBACK : i] - lo[i - LOOKBACK : i]))
            risk = abs(stop - entry)
            if risk <= 0:
                continue
            target = entry + d * rr * risk
            exit_px, j, why = None, i, "time"
            for j in range(i + 1, min(i + 1 + HOLD, n)):
                if day[j] != day[i] or hm[j] > FLAT_BY:
                    j -= 1
                    exit_px, why = c[j], "close"
                    break
                hit_s = (h[j] >= stop) if d < 0 else (lo[j] <= stop)
                hit_t = (lo[j] <= target) if d < 0 else (h[j] >= target)
                if hit_s:  # a bar that touches both is counted as the stop: the cautious reading
                    exit_px, why = stop, "stop"
                    break
                if hit_t:
                    exit_px, why = target, "target"
                    break
            if exit_px is None:
                exit_px = c[j]
            out.append(
                {
                    "t": df.index[i],
                    "dir": d,
                    "entry": entry,
                    "exit": exit_px,
                    "risk": risk,
                    "pts": d * (exit_px - entry),
                    "why": why,
                }
            )
            i = j  # one position at a time
            break
        i += 1
    return pd.DataFrame(out)


def summary(t: pd.DataFrame, cost: float) -> dict:
    if t.empty:
        return {"n": 0}
    net = t["pts"] - cost
    wins = net[net > 0].sum()
    losses = -net[net < 0].sum()
    eq = net.cumsum()
    return {
        "n": len(t),
        "win": float((net > 0).mean()),
        "avg": float(net.mean()),
        "total": float(net.sum()),
        "pf": float(wins / losses) if losses else float("inf"),
        "dd": float((eq.cummax() - eq).max()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--cost", type=float, default=1.5, help="index points per round trip")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    df = load(args.data, "5m")
    mid = df.index[len(df) // 2]
    halves = {"all": df, "1st half": df[df.index < mid], "2nd half": df[df.index >= mid]}
    variants = [
        ("**The rule**: failed candle, first hour", ("09:15", "10:15"), True, False),
        ("The rule, stop one average candle wider", ("09:15", "10:15"), True, True),
        ("Same signal, all day", None, True, False),
        ("Control: fade FULL breaks, first hour", ("09:15", "10:15"), False, False),
    ]

    L = [f"# Failed-candle fade — NIFTY 5m, {df.index[0].date()} to {df.index[-1].date()}", ""]
    L.append(f"Cost {args.cost} points per round trip. Split at {mid.date()}. Points are NIFTY index points.")
    L.append("")
    L.append("| variant | period | trades | win rate | avg / trade | total pts | profit factor | worst drawdown |")
    L.append("|---|---|---|---|---|---|---|---|")
    for name, window, failed, pad in variants:
        for label, part in halves.items():
            s = summary(trades(part, window, failed, pad=pad), args.cost)
            if not s["n"]:
                L.append(f"| {name} | {label} | 0 | – | – | – | – | – |")
                continue
            L.append(
                f"| {name} | {label} | {s['n']} | {s['win'] * 100:.1f}% | {s['avg']:+.1f} | {s['total']:+.0f} "
                f"| {s['pf']:.2f} | {s['dd']:.0f} |"
            )
    L.append("")
    L.append("Cost sensitivity — the rule over the whole period:")
    L.append("")
    L.append("| cost / trade | trades | avg / trade | total pts | profit factor |")
    L.append("|---|---|---|---|---|")
    t = trades(df, ("09:15", "10:15"), True)
    for cost in (0.0, 1.0, 1.5, 3.0):
        s = summary(t, cost)
        if not s["n"]:
            break
        L.append(f"| {cost} | {s['n']} | {s['avg']:+.1f} | {s['total']:+.0f} | {s['pf']:.2f} |")
    if not t.empty:
        L.append("")
        L.append("How the rule's trades ended: " + ", ".join(f"{k} {v}" for k, v in t["why"].value_counts().items()))
        L.append(f"Median risk (entry to stop): {t['risk'].median():.1f} points.")
    text = "\n".join(L) + "\n"
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
