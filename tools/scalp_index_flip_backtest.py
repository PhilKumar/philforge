"""Phil's Scalp rule as he corrected it, 2026-11-10: "each flip diff option..
for CE and PE" -- the INDEX's flip picks the option and the moment, the
OPTION's own chart exits it.

  entry   NIFTY 5m Supertrend (10, 2) flips UP   -> buy the ATM CE of that moment
          NIFTY 5m Supertrend flips DOWN         -> buy the ATM PE of that moment
          (nearest expiry; bought at the next 1m open)
  target  the first CPR line above the entry on that option's own chart
  stop    that option's 5m candle CLOSES below its own Supertrend -> next 1m open
  flat by 15:20
"""

import argparse
import json

import pandas as pd
from scalp_st_cpr_backtest import ROOT, bars, costs, cpr_levels, report, supertrend

RIDE = False
TARGET_PTS = 0.0
ABOVE_CPR = False
TARGET_R = False
STOP_PTS = 0.0


def run(underlying, tf, qty, slip, start, end):
    days = [d["day"] for d in json.load(open(ROOT / underlying / "days.json")) if start <= d["day"] <= end]
    step = 50 if underlying == "NIFTY" else 100
    trades = []
    prev_opt = prev_idx = None
    for day in days:
        try:
            opt = pd.read_parquet(ROOT / underlying / f"{day}.opt.parquet")
            idx = pd.read_parquet(ROOT / underlying / f"{day}.idx.parquet")
        except Exception:
            prev_opt = prev_idx = None
            continue
        if prev_opt is not None and prev_idx is not None and len(idx) > 200:
            ib = pd.concat([bars(prev_idx, tf), bars(idx, tf)])
            d = supertrend(ib)
            expiry = sorted(opt["expiry"].unique())[0]
            flips = []  # (minute the flip is known, "CE"/"PE", index close)
            for i, ts in enumerate(ib.index):
                if i == 0 or ts < pd.Timestamp(day) or d[i] == d[i - 1]:
                    continue
                known = ts + pd.Timedelta(minutes=tf)
                hm = known.hour * 60 + known.minute
                if 9 * 60 + 20 <= hm <= 15 * 60:
                    flips.append((known, "CE" if d[i] > 0 else "PE", float(ib["close"].iloc[i])))
            for n, (known, side, spot) in enumerate(flips):
                atm = int(round(spot / step) * step)
                today = opt[(opt.strike == atm) & (opt.side == side) & (opt.expiry == expiry)].sort_values("ts")
                prev = prev_opt[
                    (prev_opt.strike == atm) & (prev_opt.side == side) & (prev_opt.expiry == expiry)
                ].sort_values("ts")
                m = today.set_index("ts")
                if known not in m.index or len(today) < 100:
                    continue
                has_prev = len(prev) >= 200
                levels = cpr_levels(prev) if has_prev else []
                ob = pd.concat([bars(prev, tf), bars(today, tf)]) if has_prev else bars(today, tf)
                od = supertrend(ob)
                below = {ts + pd.Timedelta(minutes=tf): bool(od[i] < 0) for i, ts in enumerate(ob.index)}
                entry = float(m.loc[known, "open"]) + slip
                if ABOVE_CPR:
                    # Only above the option's own CPR band: past its top line
                    # (TC or BC, whichever is higher). No CPR, no trade.
                    if not has_prev:
                        continue
                    H, L, C = prev["high"].max(), prev["low"].min(), prev["close"].iloc[-1]
                    pivot = (H + L + C) / 3
                    bc = (H + L) / 2
                    if entry - slip <= max(bc, 2 * pivot - bc):
                        continue
                above = [x for x in levels if x > entry]
                target = round(above[0], 2) if above else 0.0
                if TARGET_PTS > 0:  # a fixed distance instead of the CPR line
                    target = round(entry + TARGET_PTS, 2)
                if TARGET_R and has_prev:
                    # The option chart's R1; already past it, the next R up
                    # (R2, R3, R4); past R4, no target -- Supertrend alone.
                    H, L, C = prev["high"].max(), prev["low"].min(), prev["close"].iloc[-1]
                    pv = (H + L + C) / 3
                    rs = [2 * pv - L, pv + (H - L), H + 2 * (pv - L), 3 * pv + (H - 3 * L)]
                    nxt = [x for x in rs if x > entry]
                    target = round(nxt[0], 2) if nxt else 0.0
                reason = px = when = None
                floor = 0.0  # ride: the highest line reached, now a resting stop
                for ts, row in m.loc[known:].iterrows():
                    hm = ts.hour * 60 + ts.minute
                    if ts > known and hm >= 15 * 60 + 20:
                        reason, px = "time", row.open - slip
                    elif ts > known and below.get(ts):
                        reason, px = "st_close", row.open - slip
                    elif STOP_PTS > 0 and row.low <= entry - STOP_PTS:
                        # A resting stop: filled at the stop, or at the open if
                        # the minute opened through it. Checked before the
                        # target, so a minute that touches both is a loss.
                        reason, px = "stop", min(entry - STOP_PTS, row.open) - slip
                    elif RIDE and floor and row.low <= floor:
                        # The floor from BEFORE this bar: a line crossed in
                        # this same bar is not assumed to have come first.
                        reason, px = "floor", min(floor, row.open) - slip
                    elif not RIDE and target and row.high >= target:
                        reason, px = "target", target
                    if reason:
                        when = ts
                        break
                    if RIDE:
                        reached = [x for x in above if x <= row.high]
                        if reached:
                            floor = max(floor, reached[-1])
                if reason is None:
                    reason, px, when = "eod", float(m["close"].iloc[-1]) - slip, m.index[-1]
                px = max(px, 0.05)
                gross = (px - entry) * qty
                c = costs(entry, px, qty)
                trades.append(
                    dict(
                        day=day,
                        n=n,
                        side=side,
                        strike=atm,
                        entry_t=str(known),
                        exit_t=str(when),
                        entry=round(entry, 2),
                        exit=round(px, 2),
                        target=target,
                        has_cpr=has_prev,
                        reason=reason,
                        gross=round(gross, 2),
                        cost=round(c, 2),
                        net=round(gross - c, 2),
                    )
                )
        prev_opt, prev_idx = opt, idx
    return pd.DataFrame(trades)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--tf", type=int, default=5)
    ap.add_argument("--qty", type=int, default=130)
    ap.add_argument("--slip", type=float, default=0.30)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--out", default="")
    ap.add_argument(
        "--target-pts", type=float, default=0.0, help="target this many points above the entry, not the CPR line"
    )
    ap.add_argument("--stop-pts", type=float, default=0.0, help="a resting stop this many points below the entry")
    ap.add_argument(
        "--target-r1", action="store_true", help="target the option chart's R1 (the next R line if already past it)"
    )
    ap.add_argument("--above-cpr", action="store_true", help="enter only when the option trades above its own CPR band")
    ap.add_argument("--ride", action="store_true", help="ride line to line: each CPR line reached becomes the stop")
    a = ap.parse_args()
    RIDE = a.ride
    TARGET_PTS = a.target_pts
    ABOVE_CPR = a.above_cpr
    TARGET_R = a.target_r1
    STOP_PTS = a.stop_pts
    t = run(a.underlying, a.tf, a.qty, a.slip, a.start, a.end)
    if a.out:
        t.to_csv(a.out, index=False)
    report(
        t,
        f"{a.underlying} index {a.tf}m flip -> ATM option, every flip{' (ride line to line)' if RIDE else ''}{f' (target +{TARGET_PTS:g} pts)' if TARGET_PTS else ''}{' (only above CPR)' if ABOVE_CPR else ''}{' (target R1)' if TARGET_R else ''}{f' (stop -{STOP_PTS:g} pts)' if STOP_PTS else ''}",
    )
    for cap in (1, 2):
        report(t[t.n < cap], f"first {cap} flip(s) of the day")
    print("\ntrades with no CPR (option had no previous session in the archive):", int((~t.has_cpr).sum()))
