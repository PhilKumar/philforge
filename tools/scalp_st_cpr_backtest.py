"""Phil's Scalp rule, 2026-11-10, on the real 1m option archive.

"I want to make a trade when it is above supertrend and the target has to be
the next CPR line like R1 or R2 ... SL ... it has to stop once the candle
close below the ST."

Each day: the ATM CE and the ATM PE at the 09:15 spot, nearest expiry.
  entry   flat, and a bar closes ABOVE Supertrend (10, 2) -> buy the next 1m open
  target  the first CPR line above the entry (the option's own pivots, from its
          previous session, as the Scalp chart draws them); a touch fills it
  stop    a bar CLOSES below Supertrend -> sell the next 1m open
  flat by 15:20
Costs: Rs 20 an order, STT, exchange, GST, stamp, and slippage a side.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1] / "data" / "practice"


def bars(df1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    g = df1m.set_index("ts").resample(f"{minutes}min", origin="start_day", offset="9h15min")
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last")).dropna()
    return out


def supertrend(b: pd.DataFrame, period=10, mult=2.0):
    h, lo_, c = b["high"].values, b["low"].values, b["close"].values
    n = len(b)
    atr = np.zeros(n)
    for i in range(n):
        tr = h[i] - lo_[i] if i == 0 else max(h[i] - lo_[i], abs(h[i] - c[i - 1]), abs(lo_[i] - c[i - 1]))
        atr[i] = tr if i == 0 else atr[i - 1] + (tr - atr[i - 1]) / period
    up = np.zeros(n)
    dn = np.zeros(n)
    d = np.ones(n, dtype=int)
    for i in range(n):
        mid = (h[i] + lo_[i]) / 2
        u, low_band = mid + mult * atr[i], mid - mult * atr[i]
        if i == 0:
            up[i], dn[i] = u, low_band
            continue
        dn[i] = low_band if (low_band > dn[i - 1] or c[i - 1] < dn[i - 1]) else dn[i - 1]
        up[i] = u if (u < up[i - 1] or c[i - 1] > up[i - 1]) else up[i - 1]
        d[i] = d[i - 1]
        if d[i - 1] < 0 and c[i] > up[i]:
            d[i] = 1
        elif d[i - 1] > 0 and c[i] < dn[i]:
            d[i] = -1
    return d  # +1: price above the line


def cpr_levels(prev: pd.DataFrame) -> list[float]:
    H, L, C = prev["high"].max(), prev["low"].min(), prev["close"].iloc[-1]
    p = (H + L + C) / 3
    bc = (H + L) / 2
    tc = 2 * p - bc
    r = H - L
    lv = [
        p,
        bc,
        tc,
        2 * p - L,
        p + r,
        H + 2 * (p - L),
        3 * p + (H - 3 * L),
        2 * p - H,
        p - r,
        L - 2 * (H - p),
        3 * p - (3 * H - L),
        4 * p - (4 * H - L),
    ]
    return sorted(x for x in lv if x > 0)


def costs(buy: float, sell: float, qty: int) -> float:
    bt, st = buy * qty, sell * qty
    brokerage = 40.0
    exch = 0.0003503 * (bt + st)
    stt = 0.001 * st
    stamp = 0.00003 * bt
    sebi = 0.000001 * (bt + st)
    return brokerage + exch + stt + stamp + sebi + 0.18 * (brokerage + exch + sebi)


def run(underlying: str, tf: int, qty: int, slip: float, start: str, end: str):
    days = [d["day"] for d in json.load(open(ROOT / underlying / "days.json")) if start <= d["day"] <= end]
    trades = []
    prev_df = None
    prev_day = None
    for day in days:
        try:
            df = pd.read_parquet(ROOT / underlying / f"{day}.opt.parquet")
        except Exception:
            prev_df, prev_day = None, None
            continue
        if prev_df is not None:
            first = df[df["ts"] == df["ts"].min()]
            spot = float(first["spot"].iloc[0])
            step = 50 if underlying == "NIFTY" else 100
            atm = int(round(spot / step) * step)
            expiry = sorted(df["expiry"].unique())[0]
            for side in ("CE", "PE"):
                today = df[(df.strike == atm) & (df.side == side) & (df.expiry == expiry)].sort_values("ts")
                prev = prev_df[
                    (prev_df.strike == atm) & (prev_df.side == side) & (prev_df.expiry == expiry)
                ].sort_values("ts")
                if len(today) < 200 or len(prev) < 200:
                    continue
                levels = cpr_levels(prev)
                b = pd.concat([bars(prev, tf), bars(today, tf)])
                d = supertrend(b)
                tb = b.index >= pd.Timestamp(day)
                closes_above = {ts + pd.Timedelta(minutes=tf): bool(d[i] > 0) for i, ts in enumerate(b.index) if tb[i]}
                m = today.set_index("ts")
                pos = None
                for ts, row in m.iterrows():
                    hm = ts.hour * 60 + ts.minute
                    sig = closes_above.get(ts)  # the bar that just closed, known at this minute's open
                    if pos is not None:
                        reason = None
                        px = None
                        if hm >= 15 * 60 + 20:
                            reason, px = "time", row.open - slip
                        elif sig is False:
                            reason, px = "st_close", row.open - slip
                        elif pos["target"] and row.high >= pos["target"]:
                            # At the target, never better: a resting limit, and
                            # a bad tick in the archive must not pay.
                            reason, px = "target", pos["target"]
                        if reason:
                            px = max(px, 0.05)
                            gross = (px - pos["entry"]) * qty
                            c = costs(pos["entry"], px, qty)
                            trades.append(
                                dict(
                                    day=day,
                                    side=side,
                                    strike=atm,
                                    entry_t=str(pos["t"]),
                                    exit_t=str(ts),
                                    entry=round(pos["entry"], 2),
                                    exit=round(px, 2),
                                    target=pos["target"],
                                    reason=reason,
                                    gross=round(gross, 2),
                                    cost=round(c, 2),
                                    net=round(gross - c, 2),
                                )
                            )
                            pos = None
                            continue  # no new entry in the minute an exit filled
                    if pos is None and sig is True and 9 * 60 + 20 <= hm <= 15 * 60:
                        entry = row.open + slip
                        above = [x for x in levels if x > entry]
                        pos = dict(t=ts, entry=entry, target=round(above[0], 2) if above else 0.0)
                if pos is not None:
                    last = m.iloc[-1]
                    px = max(last.close - slip, 0.05)
                    gross = (px - pos["entry"]) * qty
                    c = costs(pos["entry"], px, qty)
                    trades.append(
                        dict(
                            day=day,
                            side=side,
                            strike=atm,
                            entry_t=str(pos["t"]),
                            exit_t=str(m.index[-1]),
                            entry=round(pos["entry"], 2),
                            exit=round(px, 2),
                            target=pos["target"],
                            reason="eod",
                            gross=round(gross, 2),
                            cost=round(c, 2),
                            net=round(gross - c, 2),
                        )
                    )
        prev_df, prev_day = df, day
    return pd.DataFrame(trades)


def report(t: pd.DataFrame, label: str):
    if t.empty:
        print(label, "no trades")
        return
    t = t.sort_values("exit_t")
    eq = t["net"].cumsum()
    dd = (eq - eq.cummax()).min()
    w, lo = t[t.net > 0], t[t.net <= 0]
    print(f"\n== {label} ==")
    print(f"trades {len(t)}  days {t.day.nunique()}  per day {len(t) / t.day.nunique():.1f}")
    print(f"gross {t.gross.sum():,.0f}  costs {t.cost.sum():,.0f}  NET {t.net.sum():,.0f}")
    print(
        f"win rate {len(w) / len(t) * 100:.1f}%  avg win {w.net.mean():,.0f}  avg loss {lo.net.mean():,.0f}  max drawdown {dd:,.0f}"
    )
    print("by exit:", {k: (int(v["net"].count()), round(float(v["net"].sum()))) for k, v in t.groupby("reason")})
    print("by side:", {k: round(float(v["net"].sum())) for k, v in t.groupby("side")})
    y = t.groupby(t.day.str[:4])["net"].agg(["count", "sum"])
    print("by year:", {k: (int(r["count"]), round(float(r["sum"]))) for k, r in y.iterrows()})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--tf", type=int, default=5)
    ap.add_argument("--qty", type=int, default=130)
    ap.add_argument("--slip", type=float, default=0.30)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    t = run(a.underlying, a.tf, a.qty, a.slip, a.start, a.end)
    report(t, f"{a.underlying} {a.tf}m ST(10,2) -> next CPR line, qty {a.qty}, slip {a.slip}")
    if a.out:
        t.to_csv(a.out, index=False)
    sys.exit(0)
