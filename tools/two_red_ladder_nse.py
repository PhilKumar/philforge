"""tools/two_red_ladder_nse.py -- the BUILT two-red ladder, on NSE shares.

The rule is not re-implemented here. `engine.candle_ladder.TwoRedLadder` is the
engine behind the Cascade page's Candle Entry card and Test Bench's "Two red
candles", and it already is Phil's rule exactly:

    two reds where each closes below the PRIOR CLOSE -> a buy-STOP at the FIRST
    red's close -> on the fill, mark the ultimate low, climb to the next chart
    and wait for two reds again -> 1, 2, 3 then 4 lots -> the whole basket
    leaves at avg entry + 0.25 x (mother high - avg entry). One mother, one
    trade, no re-arm.

This file supplies three things the engine deliberately does not own: the
candles, the mothers, and the money.

  * CANDLES  the 5m caches this session already built, regrouped into 15m and
    1H so a campaign can climb. Group boundaries are cut at the session open,
    so a bar never straddles two days.
  * MOTHERS  `find_run_mothers` -- the last bar of a run of higher highs, which
    is confirmed the instant it closes and so cannot peek. One ladder per
    mother, taken only while nothing is open, which is what "one mother, one
    trade" means for an account with one purse.
  * MONEY    the engine prices options; a share is not an option. Its
    `premium_lookup` returns None here, so it records the geometry (fills at
    their index prices, the exit and its reason) and leaves the P&L blank, and
    the rupees are worked out from those fills at NSE delivery rates.

    python3 tools/two_red_ladder_nse.py --start 5m
    python3 tools/two_red_ladder_nse.py --start 15m --symbols SILVERBEES ADANIENT
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

from engine.candle_ladder import LadderCandle, TwoRedLadder  # noqa: E402
from engine.cascade_mothers import find_run_mothers  # noqa: E402
from tools.rule3070_nse import DP_PER_SELL_DAY, FEE_BUY, FEE_SELL, rs  # noqa: E402

CACHE = "/Users/philipkumar/Documents/CryptoForge/tools/.history_cache"
BEES = ["SILVERBEES", "NIFTYBEES", "GOLDBEES", "BANKBEES", "JUNIORBEES"]
STOCKS = ["ETERNAL", "ADANIENT", "RELIANCE", "ICICIBANK", "INFY", "HDFCBANK", "TMPV"]
STEPS = {"5m": 1, "15m": 3, "1h": 12}  # how many 5m bars make one bar of each chart
# The ladders this file can run. The app's own ladder stops at 1H because that
# is all the live feed has; a backtest can keep climbing, and Phil asked what
# happens when it does.
LADDERS = {
    "5m": ("5m", "15m", "1h"),
    "15m": ("15m", "1h", "1d"),
    "1h": ("1h", "1d", "1w"),
}


class Row:
    """What find_run_mothers reads: a bar with an ATR-able range."""

    __slots__ = ("timestamp", "open", "high", "low", "close")

    def __init__(self, timestamp, o, h, low, c):
        self.timestamp, self.open, self.high, self.low, self.close = timestamp, o, h, low, c


def regroup(rows: list, step: int, timeframe: str) -> List[LadderCandle]:
    out: List[LadderCandle] = []
    bucket, day = [], None
    for stamp, o, h, low, c in rows:
        when = datetime.fromtimestamp(stamp)
        if day != when.date() or len(bucket) == step:
            if bucket:
                out.append(_fold(bucket, timeframe))
            bucket, day = [], when.date()
        bucket.append((when, o, h, low, c))
    if bucket:
        out.append(_fold(bucket, timeframe))
    return out


def regroup_sessions(rows: list, per_bar: int, timeframe: str) -> List[LadderCandle]:
    """Whole SESSIONS grouped into bars: one day, or one Mon-Fri week.

    A day is every 5m bar sharing a date; a week is every day sharing an ISO
    (year, week). Grouping by count would drift across holidays and start
    slicing weeks in half.
    """
    days: dict = {}
    for stamp, o, h, low, c in rows:
        when = datetime.fromtimestamp(stamp)
        days.setdefault(when.date(), []).append((when, o, h, low, c))
    keyed: dict = {}
    for day in sorted(days):
        key = day if per_bar == 1 else day.isocalendar()[:2]
        keyed.setdefault(key, []).extend(days[day])
    return [_fold(bars, timeframe) for _key, bars in sorted(keyed.items())]


def _fold(bucket: list, timeframe: str) -> LadderCandle:
    return LadderCandle(
        timeframe=timeframe,
        timestamp=bucket[0][0],
        open=bucket[0][1],
        high=max(b[2] for b in bucket),
        low=min(b[3] for b in bucket),
        close=bucket[-1][4],
    )


def run_symbol(
    symbol: str,
    start: str,
    capital: float,
    lot_inr: float,
    dp: float,
    run_bars: int,
    minors: int = 1,
    minor_gap: float = 0.01,
    fixed_lots: bool = False,
    mother_tf: str = "",
    new_low_gate: bool = True,
    min_fall: float = 0.0,
    target_fraction: float = 0.25,
    trail_fraction: float = 0.0,
) -> dict:
    path = os.path.join(CACHE, f"{symbol}_5m.json")
    if not os.path.exists(path):
        return {}
    raw = json.load(open(path))
    stages = LADDERS[start]
    charts = {
        tf: (regroup(raw, STEPS[tf], tf) if tf in STEPS else regroup_sessions(raw, 1 if tf == "1d" else 5, tf))
        for tf in stages
    }
    base = charts[start]
    if len(base) < 200:
        return {}
    # WHERE THE MOTHER COMES FROM. A mother read on the entry chart sits a
    # fraction of a percent above the buy, and Phil's funding rule then commits
    # a fraction of a percent of the purse -- Rs 300 positions the depository
    # charge eats alive. A mother read on a SLOWER chart is a real structure
    # top: price falls 5-20% from it, so the buy is 5-20% of the purse and the
    # 0.25 target is worth something.
    mother_chart = base
    if mother_tf and mother_tf != start:
        mother_chart = (
            regroup(raw, STEPS[mother_tf], mother_tf)
            if mother_tf in STEPS
            else regroup_sessions(raw, 1 if mother_tf == "1d" else 5, mother_tf)
        )

    # FUNDING. Phil's cash rule, 2026-08-11: "calculate the percent the market
    # is down from the mother candle high till the low and allocate the entry
    # percent -- if the market has fallen 1%, capital/100". So the fall itself
    # decides the money: 1% down buys 1% of the purse, 6% down buys 6%. The
    # 1/2/3/4 lot schedule is not used at all -- the deeper rung is bigger
    # because it is deeper, not because it is numbered higher.
    closes = sorted(c.close for c in base)
    typical = closes[len(closes) // 2] or 1.0
    lot_size = max(1, int(lot_inr / typical))  # only used when --fixed-lots is on

    def typical_fall(rows_, mothers_) -> float:
        """The median fall from a mother before it is broken -- this instrument's
        own idea of a real fall. A flat 5% gate suits nothing: NIFTYBEES normally
        falls 2%, ETERNAL 5%, so one number either never trades or never waits."""
        falls = []
        for m in mothers_:
            i = next((j for j, r in enumerate(rows_) if r.timestamp == m.timestamp), None)
            if i is None:
                continue
            low = rows_[i].low
            for r in rows_[i + 1 :]:
                if r.close > m.high:
                    break
                low = min(low, r.low)
            falls.append((m.high - low) / m.high)
        falls.sort()
        return falls[len(falls) // 2] if falls else 0.0

    mothers = find_run_mothers(
        [Row(c.timestamp, c.open, c.high, c.low, c.close) for c in mother_chart],
        run=run_bars,
        min_separation_bars=0,
        # A run of higher highs inside ONE session is the intraday reading. A
        # daily or weekly bar IS a session, so that guard finds nothing at all
        # on those charts -- the run has to be allowed to cross days there.
        same_session_only=(mother_tf or start) not in {"1d", "1w"},
    )
    every = sorted(
        (c for tf in stages for c in charts[tf]),
        key=lambda c: c.timestamp,
    )

    # MINOR MOTHERS. "One mother, one trade" is the engine's law and it stays
    # true -- each ladder still gets exactly one. What changes is how many
    # ladders may be alive at once: with `minors` above 1 a LOWER high can be
    # marked while an older campaign is still holding, which is what Phil marks
    # by hand on the cascade. Same guard the cascade uses, so the book cannot
    # fill up with the same trade at the same price: a new mother must sit at
    # least `minor_gap` BELOW every mother already working.
    if min_fall < 0:  # auto: this instrument's own median fall
        min_fall = typical_fall([Row(c.timestamp, c.open, c.high, c.low, c.close) for c in mother_chart], mothers)

    ladders, live = [], []  # live: (mother_high, ends_at)
    for mother in mothers:
        live = [row for row in live if row[1] > mother.timestamp]
        if len(live) >= max(1, minors):
            continue
        mother_bar = next((c for c in mother_chart if c.timestamp == mother.timestamp), None)
        if mother_bar is None:
            continue
        if any(mother_bar.high > high * (1 - minor_gap) for high, _ends in live):
            continue

        def fall_sized(price: float, _lots: int, high: float = mother_bar.high) -> int:
            fallen = (high - price) / high  # the percent it is down from the mother
            # THE FIRST BUY WAITS FOR A REAL FALL. Two reds right under the
            # mother is a 0.5% dip: it funds Rs 1,000, targets a quarter of
            # nothing, and closes in a day. Under `min_fall` the rung returns
            # no shares, which the engine reads as "not worth a share yet" and
            # keeps waiting -- so the same setup can fill later, lower down.
            if fallen < min_fall:
                return 0
            return int(capital * fallen / price) if fallen > 0 else 0

        ladder = TwoRedLadder(
            mother_bar,
            stages=stages,
            strike_for=lambda when, price: (0, "CE"),
            premium_lookup=lambda when, strike, option_type: None,
            lot_size=lot_size,
            quantity_for=None if fixed_lots else fall_sized,
            # THE GATE THAT STOPS THE CLIMB. On by default in the engine: after
            # a buy, the market must print a NEW low before the next rung may
            # arm. It is why every run so far averages one buy per campaign --
            # and with Phil's fall-sized funding that matters twice over, since
            # the first buy is always the shallowest and so the smallest.
            require_new_low=new_low_gate,
            target_fraction=target_fraction,
            trail_fraction=trail_fraction,
        )
        # THE MOTHER VOIDS THE SETUP. Fed the whole tape, an unfilled ladder
        # waits months, buys in a rally far ABOVE its mother, and then targets
        # avg + 0.25 x (mother high - avg) -- which is BELOW the entry, so it
        # "hits" instantly at a loss. That is not the rule failing, it is a
        # campaign that should never have been alive: a close back above the
        # mother high says the fall it was waiting for is over. Once something
        # is bought the basket stays, because its target sits under the mother
        # and is reachable.
        feed = []
        for candle in (c for c in every if c.timestamp > mother.timestamp):
            if not ladder.fills and candle.close > mother_bar.high:
                break
            feed.append(candle)
        ladder.run(feed)
        if ladder.fills:
            ladders.append(ladder)
            live.append((mother_bar.high, ladder.exit_timestamp or every[-1].timestamp))
    last_close = base[-1].close
    years = (base[-1].timestamp - base[0].timestamp).days / 365.25 or 1.0

    closed, bag, sell_days, peak_events, rungs = [], 0.0, {}, [], []
    for ladder in ladders:
        cost = sum(f.index_price * f.quantity for f in ladder.fills)
        qty = sum(f.quantity for f in ladder.fills)
        rungs.append(len(ladder.fills))
        for f in ladder.fills:
            peak_events.append((f.timestamp, f.index_price * f.quantity))
        exit_price = ladder.exit_index_price
        if exit_price is None:
            bag += qty * last_close - cost
            continue
        proceeds = qty * exit_price
        closed.append(proceeds - cost - FEE_BUY * cost - FEE_SELL * proceeds)
        sell_days.setdefault(ladder.exit_timestamp.date(), []).append(len(closed) - 1)
        peak_events.append((ladder.exit_timestamp, -cost))
    for _day, idxs in sell_days.items():
        for i in idxs:
            closed[i] -= dp / len(idxs)
    peak_events.sort(key=lambda e: e[0])
    cur = peak = 0.0
    for _ts, delta in peak_events:
        cur += delta
        peak = max(peak, cur)

    total = sum(closed)
    return {
        "symbol": symbol,
        "mothers": len(mothers),
        "ladders": len(ladders),
        "rungs": (sum(rungs) / len(rungs)) if rungs else 0.0,
        "closed": len(closed),
        "wins": sum(1 for n in closed if n > 0),
        "net": total,
        "bag": bag,
        "total": total + bag,
        "peak": peak,
        "pct_yr": (total + bag) / years / capital * 100,
        "scaled": ((total + bag) * (capital / peak) / years / capital * 100) if peak else 0.0,
        "lot": lot_size,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="5m", choices=sorted(LADDERS), help="the chart the ladder starts on")
    ap.add_argument("--symbols", nargs="+", default=BEES + STOCKS)
    ap.add_argument("--capital", type=float, default=200000.0)
    ap.add_argument("--lot-inr", type=float, default=50000.0, help="rupees in ONE lot (rung 1)")
    ap.add_argument("--run", type=int, default=5, help="higher highs in the run that marks a mother")
    ap.add_argument("--target", type=float, default=0.25, help="how far back to the mother the target sits")
    ap.add_argument("--trail", type=float, default=0.0, help="hitting the target arms a trail giving back this much")
    ap.add_argument(
        "--min-fall",
        type=float,
        default=0.0,
        help="no buy until price is this %% under the mother; -1 = the instrument's own median fall",
    )
    ap.add_argument("--no-new-low", action="store_true", help="let the ladder climb without waiting for a new low")
    ap.add_argument("--mother-tf", default="", choices=["", "1h", "1d", "1w"], help="read mothers on a slower chart")
    ap.add_argument("--fixed-lots", action="store_true", help="the old 1/2/3/4 lots instead of the fall percent")
    ap.add_argument("--minors", type=int, default=1, help="how many ladders may be alive at once (1 = today)")
    ap.add_argument("--minor-gap", type=float, default=1.0, help="a new mother must sit this %% under every live one")
    ap.add_argument("--dp", type=float, default=DP_PER_SELL_DAY)
    args = ap.parse_args()

    stages = LADDERS[args.start]
    print(
        f"\nTwo-red ladder (engine/candle_ladder.py) · {' -> '.join(stages)} · 1/2/3/4 lots · "
        f"target avg + 0.25 to the mother high\n{rs(args.capital)} account · "
        f"{'1/2/3/4 lots of ' + rs(args.lot_inr) if args.fixed_lots else 'buy = the % it has fallen from the mother'} · "
        f"{args.minors} ladder(s) at once, {args.minor_gap:g}% apart\n"
    )
    head = (
        f"  {'symbol':<12}{'mothers':>9}{'ladders':>9}{'rungs':>7}{'wins':>10}{'net':>13}"
        f"{'bag':>13}{'TOTAL':>13}{'%/yr':>8}{'peak':>13}"
    )
    print(head)
    print("  " + "-" * (len(head) - 2))
    grand = {"net": 0.0, "bag": 0.0, "total": 0.0, "ladders": 0, "wins": 0, "closed": 0}
    for symbol in args.symbols:
        r = run_symbol(
            symbol,
            args.start,
            args.capital,
            args.lot_inr,
            args.dp,
            args.run,
            minors=args.minors,
            minor_gap=args.minor_gap / 100.0,
            fixed_lots=args.fixed_lots,
            mother_tf=args.mother_tf,
            new_low_gate=not args.no_new_low,
            min_fall=args.min_fall / 100.0,
            target_fraction=args.target,
            trail_fraction=args.trail,
        )
        if not r:
            print(f"  {symbol:<12} no data")
            continue
        wins = f"{r['wins']}/{r['closed']}"
        print(
            f"  {r['symbol']:<12}{r['mothers']:>9}{r['ladders']:>9}{r['rungs']:>7.1f}{wins:>10}"
            f"{rs(r['net']):>13}{rs(r['bag']):>13}{rs(r['total']):>13}{r['pct_yr']:>7.1f}%{rs(r['peak']):>13}"
        )
        for key in ("net", "bag", "total", "ladders", "wins", "closed"):
            grand[key] += r[key]
    print("  " + "-" * (len(head) - 2))
    won = f"{grand['wins']}/{grand['closed']}"
    print(
        f"  {'ALL TWELVE':<12}{'':>9}{grand['ladders']:>9}{'':>7}{won:>10}"
        f"{rs(grand['net']):>13}{rs(grand['bag']):>13}{rs(grand['total']):>13}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
