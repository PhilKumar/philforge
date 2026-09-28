"""tools/playlist_claims.py -- five claims from the playlist, measured on NIFTY.

docs/CLASS_PLAYBOOK.md ends with "five claims to test first". Each is his most
repeated idea, stated precisely enough to count. This measures them on the
index itself -- no options, no costs, no sizing -- because the first question
is whether the market behaves the way he says, before any trade is built on it.

Every claim is set against a CONTROL: the same count on the same bars without
his condition, or what a random walk would give for the same geometry. A rule
that scores 60% means nothing until you know the coin-toss version scores 58%.

    python3 tools/playlist_claims.py --data DIR [--out docs/playlist-claims-results.md]

DIR holds the NSE_INDEX_Nifty_50_{5m,15m,1h,1d}_*.json files the fetch tools
write (rows of [iso-time, open, high, low, close, volume, oi]).

The five, with the playbook rules they test:

  1. THE 3-CANDLE TEST (T154, T552). A close beyond a level either holds for the
     next 3 candles or it does not. Held should continue; not held should hand
     control to the other side.
  2. BUY DEEPER, WIN MORE (T114, T188, T533). Entries in a pullback, bucketed by
     how deep into the prior leg they are: 0-25, 25-50, 50-75, 75-100.
  3. EVERY LEG RETURNS TO ITS 50% within 1/3x to 1.5x its own duration (T364).
  4. THE FAILED CANDLE (T144, T174). A close beyond a level with under half the
     candle beyond it is a trap and should reverse more than a full break.
  5. VIOLENT HOURS (T260, T538). The opening and closing windows carry most of
     the day's movement.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

TIMEFRAMES = ("5m", "15m", "1h", "1d")


# ── data ──────────────────────────────────────────────────────────────────────


def load(data_dir: str, tf: str) -> pd.DataFrame:
    paths = sorted(glob.glob(os.path.join(data_dir, f"NSE_INDEX_Nifty_50_{tf}_*.json")))
    if not paths:
        raise SystemExit(f"no {tf} file in {data_dir}")
    rows = json.load(open(paths[-1]))
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume", "oi"])
    df["t"] = pd.to_datetime(df["t"]).dt.tz_localize(None)
    df = df.drop_duplicates("t").sort_values("t").set_index("t")
    return df[["open", "high", "low", "close"]].astype(float)


# ── swings ────────────────────────────────────────────────────────────────────


@dataclass
class Leg:
    start: int  # bar index of the swing the leg starts from
    end: int  # bar index of the swing it ends at
    start_px: float
    end_px: float
    confirmed: int  # bar index at which the END swing became known

    @property
    def up(self) -> bool:
        return self.end_px > self.start_px


def zigzag(df: pd.DataFrame, pct: float) -> list[Leg]:
    """Swings that reverse by at least `pct` (a fraction). A swing is only KNOWN
    when price has come back `pct` from it -- `confirmed` records that bar, so
    anything that trades on a swing can wait for it."""
    hi, lo = df["high"].to_numpy(), df["low"].to_numpy()
    legs: list[Leg] = []
    # Before the first swing: wait for a `pct` move away from the extreme seen so far.
    max_i, min_i = 0, 0
    direction = 0
    for i in range(1, len(df)):
        if hi[i] > hi[max_i]:
            max_i = i
        if lo[i] < lo[min_i]:
            min_i = i
        if hi[i] >= lo[min_i] * (1 + pct):
            direction, piv_i, piv_px, ext_i, ext_px = 1, min_i, lo[min_i], i, hi[i]
            break
        if lo[i] <= hi[max_i] * (1 - pct):
            direction, piv_i, piv_px, ext_i, ext_px = -1, max_i, hi[max_i], i, lo[i]
            break
    if direction == 0:
        return legs
    for i in range(ext_i + 1, len(df)):
        if direction > 0:
            if hi[i] > ext_px:
                ext_i, ext_px = i, hi[i]
            elif lo[i] <= ext_px * (1 - pct):
                legs.append(Leg(piv_i, ext_i, piv_px, ext_px, i))
                direction, piv_i, piv_px, ext_i, ext_px = -1, ext_i, ext_px, i, lo[i]
        else:
            if lo[i] < ext_px:
                ext_i, ext_px = i, lo[i]
            elif hi[i] >= ext_px * (1 + pct):
                legs.append(Leg(piv_i, ext_i, piv_px, ext_px, i))
                direction, piv_i, piv_px, ext_i, ext_px = 1, ext_i, ext_px, i, hi[i]
    return legs


# ── 1. the 3-candle test ──────────────────────────────────────────────────────


def three_candle(df: pd.DataFrame, lookback: int = 12, horizon: int = 12) -> dict:
    """Breaks of the last `lookback` bars' high (and low). HELD = the next 3
    closes all stay beyond the level. Outcome: did price, `horizon` bars after
    the third candle, sit further in the break's direction than at the third
    candle's close? The control is every bar's `horizon`-bar direction."""
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    n = len(df)
    out = {"held": [0, 0], "failed": [0, 0]}
    for i in range(lookback, n - 3 - horizon):
        up_lvl = h[i - lookback : i].max()
        dn_lvl = lo[i - lookback : i].min()
        for side, level in ((1, up_lvl), (-1, dn_lvl)):
            if side * (c[i] - level) <= 0 or side * (c[i - 1] - level) > 0:
                continue  # not a fresh close beyond the level
            nxt = c[i + 1 : i + 4]
            key = "held" if np.all(side * (nxt - level) > 0) else "failed"
            ref = c[i + 3]
            went_on = side * (c[i + 3 + horizon] - ref) > 0
            out[key][0] += int(went_on)
            out[key][1] += 1
    fwd = c[horizon:] - c[:-horizon]
    out["control_up"] = float(np.mean(fwd > 0))
    return out


# ── 2. win rate by pullback depth ─────────────────────────────────────────────

ZONES = ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0))


def depth_win_rate(df: pd.DataFrame, legs: list[Leg], step: float = 0.25) -> dict:
    """After each confirmed leg, the counter-move is an entry for the leg's
    direction. Enter at the first touch of each zone's middle (12.5/37.5/62.5/
    87.5% back into the leg). WIN = price moves `step` of the leg in the leg's
    direction before moving `step` against -- symmetric, so a random walk wins
    50%, and a deeper zone can only win more if the market really does bounce
    harder from deep in. A second tally scores the same entries against the
    leg's own 50% line as target and its origin as the stop."""
    h, lo = df["high"].to_numpy(), df["low"].to_numpy()
    sym = {z: [0, 0] for z in ZONES}
    to50 = {z: [0, 0, 0.0] for z in ZONES}
    for k, leg in enumerate(legs):
        size = abs(leg.end_px - leg.start_px)
        stop_at = legs[k + 1].confirmed if k + 1 < len(legs) else len(df)
        sign = 1 if leg.up else -1
        # The leg's end is only known at `confirmed`, by which time price has
        # already come back part of the way. A zone it passed on the way is not
        # an entry anyone could have taken -- skip it rather than fill at a
        # price the market has already left.
        passed = lo[leg.end + 1 : leg.confirmed + 1].min() if sign > 0 else h[leg.end + 1 : leg.confirmed + 1].max()
        for z in ZONES:
            d = (z[0] + z[1]) / 2
            px = leg.end_px - sign * d * size
            if (sign > 0 and passed <= px) or (sign < 0 and passed >= px):
                continue
            entry = None
            for i in range(leg.confirmed + 1, min(stop_at + 1, len(df))):
                if (sign > 0 and lo[i] <= px) or (sign < 0 and h[i] >= px):
                    entry = i
                    break
            if entry is None:
                continue
            tgt, stp = px + sign * step * size, px - sign * step * size
            tgt50, stp0 = leg.end_px - sign * 0.5 * size, leg.start_px
            r1 = _first_touch(h, lo, entry + 1, tgt, stp, sign)
            if r1 is not None:
                sym[z][0] += r1
                sym[z][1] += 1
            if d > 0.5:
                r2 = _first_touch(h, lo, entry + 1, tgt50, stp0, sign)
                if r2 is not None:
                    to50[z][0] += r2
                    to50[z][1] += 1
                    to50[z][2] = (1 - d) / 0.5  # random walk: distance to stop / (target..stop span)
    return {"symmetric": sym, "to_50": to50}


def _first_touch(h, lo, start, target, stop, sign):
    for i in range(start, len(h)):
        hit_t = h[i] >= target if sign > 0 else lo[i] <= target
        hit_s = lo[i] <= stop if sign > 0 else h[i] >= stop
        if hit_t and hit_s:
            return None  # both inside one bar: order unknown, not counted
        if hit_t:
            return 1
        if hit_s:
            return 0
    return None


# ── 3. back to the 50% ────────────────────────────────────────────────────────


def retrace_reached(df: pd.DataFrame, legs: list[Leg], pct: float) -> dict:
    """The control for "it WILL come back to 50%": how often each deeper
    retracement is reached too, and how often the 50% was already reached by
    the time the swing itself became known (then it says nothing)."""
    h, lo = df["high"].to_numpy(), df["low"].to_numpy()
    reached = {f: 0 for f in (0.5, 0.618, 0.75, 1.0)}
    automatic = 0
    for leg in legs:
        size = abs(leg.end_px - leg.start_px)
        automatic += int(0.5 * size <= leg.end_px * pct)
        seg = slice(leg.end + 1, len(df))
        for f in reached:
            lvl = leg.end_px - (1 if leg.up else -1) * f * size
            reached[f] += int((lo[seg] <= lvl).any() if leg.up else (h[seg] >= lvl).any())
    n = max(1, len(legs))
    return {"automatic": automatic / n, **{f: v / n for f, v in reached.items()}}


def return_to_half(df: pd.DataFrame, legs: list[Leg]) -> dict:
    """For every leg: bars until price retraces to the leg's 50%, counted from
    the leg's end, set against D = the leg's own length in bars."""
    h, lo = df["high"].to_numpy(), df["low"].to_numpy()
    before, inside, after, never = 0, 0, 0, 0
    for leg in legs:
        dur = max(1, leg.end - leg.start)
        half = (leg.start_px + leg.end_px) / 2
        took = None
        for i in range(leg.end + 1, len(df)):
            if (leg.up and lo[i] <= half) or (not leg.up and h[i] >= half):
                took = i - leg.end
                break
        if took is None:
            never += 1
        elif took < dur / 3:
            before += 1
        elif took <= 1.5 * dur:
            inside += 1
        else:
            after += 1
    return {"before": before, "inside": inside, "after": after, "never": never}


# ── 4. the failed candle ──────────────────────────────────────────────────────


def failed_candle(df: pd.DataFrame, lookback: int = 12) -> dict:
    """Closes beyond the last `lookback` bars' high/low, split by how much of
    the breaking candle's range lies beyond the level. FAILED = under half.
    Outcome: within the next 3 candles, does a close come back through the
    level (the trap springs)?"""
    h, lo, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    res = {"failed": [0, 0], "full": [0, 0]}
    rows = []
    for i in range(lookback, len(df) - 3):
        rng = h[i] - lo[i]
        if rng <= 0:
            continue
        for side, level in ((1, h[i - lookback : i].max()), (-1, lo[i - lookback : i].min())):
            if side * (c[i] - level) <= 0 or side * (c[i - 1] - level) > 0:
                continue
            beyond = (h[i] - max(level, lo[i])) if side > 0 else (min(level, h[i]) - lo[i])
            key = "failed" if beyond / rng < 0.5 else "full"
            back = np.any(side * (c[i + 1 : i + 4] - level) <= 0)
            res[key][0] += int(back)
            res[key][1] += 1
            # A failed candle closes nearer the level BY DEFINITION, and a close
            # near a level comes back more often whatever the candle looks like.
            # So the fair comparison is at the same distance: how far the close
            # is beyond the level, in units of the recent average bar range.
            atr = float(np.mean(h[i - lookback : i] - lo[i - lookback : i])) or 1.0
            rows.append((key == "failed", side * (c[i] - level) / atr, bool(back)))
    r = pd.DataFrame(rows, columns=["failed", "dist", "back"])
    res["matched"] = {}
    if r["dist"].nunique() < 4:
        return res  # too few distinct breaks to split into quarters
    r["bucket"] = pd.qcut(r["dist"], 4, labels=["closest", "2nd", "3rd", "furthest"])
    by = r.groupby(["bucket", "failed"], observed=True)["back"].agg(["mean", "count"])
    res["matched"] = {(b, f): (float(by.loc[(b, f), "mean"]), int(by.loc[(b, f), "count"])) for b, f in by.index}
    return res


# ── 5. violent hours ──────────────────────────────────────────────────────────

WINDOWS = (("09:15-10:15", "09:15", "10:15"), ("10:15-13:30", "10:15", "13:30"), ("13:30-15:30", "13:30", "15:30"))


def violent_hours(df5: pd.DataFrame) -> dict:
    """Share of each session's movement (sum of 5m bar ranges) in each window,
    and how often the day's high or low is printed there. Each window's share
    of the session's MINUTES is the control."""
    d = df5.copy()
    d["rng"] = d["high"] - d["low"]
    d["hm"] = d.index.strftime("%H:%M")
    d["day"] = d.index.date
    share = {w[0]: [] for w in WINDOWS}
    extreme = {w[0]: 0 for w in WINDOWS}
    days = 0
    for _, g in d.groupby("day"):
        if len(g) < 70:
            continue  # half sessions and gaps
        days += 1
        tot = g["rng"].sum()
        hi_t, lo_t = g["high"].idxmax().strftime("%H:%M"), g["low"].idxmin().strftime("%H:%M")
        for name, a, b in WINDOWS:
            m = (g["hm"] >= a) & (g["hm"] < b)
            share[name].append(g.loc[m, "rng"].sum() / tot)
            extreme[name] += int(a <= hi_t < b) + int(a <= lo_t < b)
    minutes = {"09:15-10:15": 60, "10:15-13:30": 195, "13:30-15:30": 120}
    # Half-hour detail: where inside the windows the movement actually sits.
    full = d.groupby("day").size()
    dd = d[d["day"].isin(full[full >= 70].index)].copy()
    dd["slot"] = (dd.index.hour * 60 + dd.index.minute - 555) // 30
    tot = dd.groupby("day")["rng"].sum()
    slot = dd.groupby(["day", "slot"])["rng"].sum().div(tot, level="day").groupby("slot").mean()
    return {
        "slots": {int(s): float(v) for s, v in slot.items()},
        "days": days,
        "share": {k: float(np.mean(v)) for k, v in share.items()},
        "extremes": {k: v / (2 * days) for k, v in extreme.items()},
        "time_share": {k: v / 375 for k, v in minutes.items()},
    }


# ── round 2: at HIS line — the last swing's 50% ───────────────────────────────
#
# Round 1 tested the 3-candle hold at any 12-bar high and depth against any
# swing, and neither showed an edge. He applies both at one place: the 50% line
# of the last swing (T297, T312, T455). These redo them there. The swing used
# is always the last one KNOWN at that bar -- its `confirmed` index -- so no
# decision reads a swing it could not yet have seen.


def _current_leg(n: int, legs: list[Leg]) -> np.ndarray:
    cur = np.full(n, -1)
    for k, leg in enumerate(legs):
        end = legs[k + 1].confirmed if k + 1 < len(legs) else n
        cur[leg.confirmed : end] = k
    return cur


def half_line_break(df: pd.DataFrame, legs: list[Leg], step: float = 0.25) -> dict:
    """A close through the last swing's 50% line. HELD = the next 3 closes stay
    on the new side. Scored from the third candle's close: a quarter of the
    swing further in the break's direction before a quarter against (a random
    walk wins 50%)."""
    h, lo, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    cur = _current_leg(len(df), legs)
    out = {"held": [0, 0], "failed": [0, 0]}
    for i in range(1, len(df) - 4):
        k = cur[i]
        if k < 0 or cur[i - 1] != k:
            continue
        leg = legs[k]
        mid = (leg.start_px + leg.end_px) / 2
        size = abs(leg.end_px - leg.start_px)
        if c[i - 1] >= mid > c[i]:
            side = -1
        elif c[i - 1] <= mid < c[i]:
            side = 1
        else:
            continue
        key = "held" if np.all(side * (c[i + 1 : i + 4] - mid) > 0) else "failed"
        ref = c[i + 3]
        r = _first_touch(h, lo, i + 4, ref + side * step * size, ref - side * step * size, side)
        if r is not None:
            out[key][0] += r
            out[key][1] += 1
    return out


def value_side_entries(df: pd.DataFrame, legs: list[Leg]) -> dict:
    """Under value / over value (T297). In the pullback after a swing, the
    first candle in the swing's own colour (green after an up swing) that
    closes BELOW the 50% line is an under-value entry; the first above it, an
    over-value entry. One of each per swing, so one swing cannot vote twice.
    Scored two ways: 1:1 (a quarter of the swing either way, random walk 50%)
    and his 2:1 (half the swing in favour before a quarter against, random
    walk 33%)."""
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    res = {s: {"1:1": [0, 0], "2:1": [0, 0]} for s in ("over", "under")}
    for k, leg in enumerate(legs):
        sign = 1 if leg.up else -1
        size = abs(leg.end_px - leg.start_px)
        mid = (leg.start_px + leg.end_px) / 2
        stop_at = legs[k + 1].confirmed if k + 1 < len(legs) else len(df)
        taken = set()
        for i in range(leg.confirmed + 1, min(stop_at, len(df) - 1)):
            if sign * (c[i] - o[i]) <= 0:
                continue  # not a candle of the swing's own side
            # inside the swing's range only; beyond its origin it is a new swing
            if sign * (c[i] - leg.start_px) <= 0 or sign * (c[i] - leg.end_px) >= 0:
                continue
            side = "under" if sign * (c[i] - mid) < 0 else "over"
            if side in taken:
                continue
            taken.add(side)
            for name, up, dn in (("1:1", 0.25, 0.25), ("2:1", 0.5, 0.25)):
                r = _first_touch(h, lo, i + 1, c[i] + sign * up * size, c[i] - sign * dn * size, sign)
                if r is not None:
                    res[side][name][0] += r
                    res[side][name][1] += 1
            if len(taken) == 2:
                break
    return res


# ── report ────────────────────────────────────────────────────────────────────


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "–"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    frames = {tf: load(args.data, tf) for tf in TIMEFRAMES}
    swing = {"5m": 0.004, "15m": 0.006, "1h": 0.01, "1d": 0.03}
    legs = {tf: zigzag(frames[tf], swing[tf]) for tf in TIMEFRAMES}
    first, last = frames["5m"].index[0].date(), frames["5m"].index[-1].date()
    L = [f"# Playlist claims, measured on NIFTY ({first} – {last})", ""]
    L.append(
        "Generated by `tools/playlist_claims.py`. Index only — no options, no costs. "
        "Each result sits next to its control: the same count without his condition, "
        "or what a random walk gives for the same geometry."
    )
    L.append("")
    L.append("| TF | bars | swing size | legs |")
    L.append("|---|---|---|---|")
    for tf in TIMEFRAMES:
        L.append(f"| {tf} | {len(frames[tf]):,} | {swing[tf] * 100:.1f}% | {len(legs[tf]):,} |")

    L += ["", "## 1. The 3-candle test (T154, T552)", ""]
    L.append("A close beyond the last 12 bars' high or low. **Held** = the next 3 closes stay beyond it.")
    L.append("Scored: 12 bars after the third candle, is price further in the break's direction?")
    L.append("")
    L.append("| TF | held → went on | not held → went on | breaks | control (any bar goes up) |")
    L.append("|---|---|---|---|---|")
    for tf in ("5m", "15m", "1h"):
        r = three_candle(frames[tf])
        L.append(
            f"| {tf} | {pct(*r['held'])} of {r['held'][1]:,} | {pct(*r['failed'])} of {r['failed'][1]:,} "
            f"| {r['held'][1] + r['failed'][1]:,} | {r['control_up'] * 100:.1f}% |"
        )

    L += ["", "## 2. Buy deeper, win more (T114, T188, T533)", ""]
    L.append("After each swing leg, an entry for the leg's direction at the middle of each pullback zone.")
    L.append("**A**: win = a quarter of the leg in your favour before a quarter against (random walk = 50%).")
    L.append("**B**: win = back to the leg's 50% before the leg's origin (random walk given alongside).")
    L.append("")
    L.append("| TF | zone | A: win | A: n | B: win | B: random walk | B: n |")
    L.append("|---|---|---|---|---|---|---|")
    for tf in ("5m", "15m", "1h", "1d"):
        r = depth_win_rate(frames[tf], legs[tf])
        for z in ZONES:
            a = r["symmetric"][z]
            b = r["to_50"][z]
            bw = pct(b[0], b[1]) if b[1] else "–"
            rw = f"{b[2] * 100:.0f}%" if b[1] else "–"
            L.append(f"| {tf} | {int(z[0] * 100)}–{int(z[1] * 100)} | {pct(*a)} | {a[1]:,} | {bw} | {rw} | {b[1]:,} |")

    L += ["", "## 3. Every leg returns to its 50% within ⅓× to 1.5× its duration (T364)", ""]
    L.append("D = the leg's own length in bars; time counted from the leg's end.")
    L.append("")
    L.append("| TF | legs | faster than ⅓D | inside ⅓D–1.5D | slower | never (in the data) |")
    L.append("|---|---|---|---|---|---|")
    for tf in TIMEFRAMES:
        r = return_to_half(frames[tf], legs[tf])
        n = sum(r.values())
        L.append(
            f"| {tf} | {n:,} | {pct(r['before'], n)} | {pct(r['inside'], n)} | {pct(r['after'], n)} | {pct(r['never'], n)} |"
        )
    L.append("")
    L.append("Control — the same legs, any time later in the data. If the 100% is reached nearly as often")
    L.append('as the 50%, then "it always comes back to 50%" is true of every level and says nothing.')
    L.append("")
    L.append("| TF | 50% already reached when the swing became known | 50% | 61.8% | 75% | 100% |")
    L.append("|---|---|---|---|---|---|")
    for tf in TIMEFRAMES:
        r = retrace_reached(frames[tf], legs[tf], swing[tf])
        L.append(
            f"| {tf} | {r['automatic'] * 100:.0f}% | {r[0.5] * 100:.0f}% | {r[0.618] * 100:.0f}% "
            f"| {r[0.75] * 100:.0f}% | {r[1.0] * 100:.0f}% |"
        )

    L += ["", "## 4. The failed candle (T144, T174)", ""]
    L.append("A close beyond the last 12 bars' high or low. **Failed** = under half the candle's range beyond it.")
    L.append("Scored: does a close come back through the level within the next 3 candles?")
    L.append("")
    matched = {}
    L.append("| TF | failed → came back | full break → came back | failed n | full n |")
    L.append("|---|---|---|---|---|")
    for tf in ("5m", "15m", "1h"):
        r = failed_candle(frames[tf])
        L.append(f"| {tf} | {pct(*r['failed'])} | {pct(*r['full'])} | {r['failed'][1]:,} | {r['full'][1]:,} |")
        matched[tf] = r["matched"]
    L.append("")
    L.append("Control — a failed candle closes nearer the level by definition. Same comparison, at the same")
    L.append("distance: breaks split into quarters by how far the close is beyond the level.")
    L.append("")
    L.append("| TF | close distance | failed → came back | full → came back |")
    L.append("|---|---|---|---|")
    for tf, m in matched.items():
        for b in ("closest", "2nd", "3rd", "furthest"):
            f, u = m.get((b, True)), m.get((b, False))
            fs = f"{f[0] * 100:.1f}% (n {f[1]:,})" if f else "–"
            us = f"{u[0] * 100:.1f}% (n {u[1]:,})" if u else "–"
            L.append(f"| {tf} | {b} | {fs} | {us} |")

    L += ["", "## Round 2 — the same two claims at his 50% line", ""]
    L.append("The line is the 50% of the last swing known at that bar (never one still forming).")
    L.append("")
    L.append("### 1b. The 3-candle test at the 50% line (T154, T312, T455)")
    L.append("")
    L.append("A close through the line. Scored from the third candle: a quarter of the swing onward")
    L.append("before a quarter back. A random walk wins 50%.")
    L.append("")
    L.append("| TF | held 3 candles → went on | not held → went on | held n | not held n |")
    L.append("|---|---|---|---|---|")
    for tf in ("5m", "15m", "1h", "1d"):
        r = half_line_break(frames[tf], legs[tf])
        L.append(f"| {tf} | {pct(*r['held'])} | {pct(*r['failed'])} | {r['held'][1]:,} | {r['failed'][1]:,} |")
    L.append("")
    L.append("### 2b. Under value vs over value (T297)")
    L.append("")
    L.append("First candle of the swing's own colour in the pullback, below the 50% (under value) or")
    L.append("above it (over value). He says under value wins ~7 in 10, over value ~3 in 10.")
    L.append("")
    L.append("| TF | side | 1:1 win (random 50%) | n | 2:1 win (random 33%) | n |")
    L.append("|---|---|---|---|---|---|")
    for tf in ("5m", "15m", "1h", "1d"):
        r = value_side_entries(frames[tf], legs[tf])
        for side in ("under", "over"):
            a, b = r[side]["1:1"], r[side]["2:1"]
            L.append(f"| {tf} | {side} value | {pct(*a)} | {a[1]:,} | {pct(*b)} | {b[1]:,} |")

    L += ["", "## 5. Violent hours (T260, T538)", ""]
    r = violent_hours(frames["5m"])
    L.append(f"{r['days']} full sessions. Movement = sum of 5-minute bar ranges.")
    L.append("")
    L.append("| window | share of the minutes | share of the movement | share of day highs + lows |")
    L.append("|---|---|---|---|")
    for name, _, _ in WINDOWS:
        L.append(
            f"| {name} | {r['time_share'][name] * 100:.0f}% | {r['share'][name] * 100:.1f}% | {r['extremes'][name] * 100:.1f}% |"
        )
    L.append("")
    L.append("Half-hour detail (a 30-minute slot is 8% of the session; the last slot is 15 minutes):")
    L.append("")
    L.append("| slot | share of the movement |")
    L.append("|---|---|")
    for sl, v in sorted(r["slots"].items()):
        m0 = 555 + 30 * sl
        L.append(f"| {m0 // 60:02d}:{m0 % 60:02d} | {v * 100:.1f}% |")

    text = "\n".join(L) + "\n"
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
