"""tools/fib_ladder_nse.py -- the swing touch ladder, run on NSE SHARES.

engine/fib_touch_ladder.py trades OPTIONS off an index: every rung resolves a
strike and an expiry, and the money is premium. None of that exists for a
share, so this runs the same GEOMETRY over a stock's own 5-minute candles and
buys the stock itself. The geometry is not re-implemented -- `find_swing_anchor`,
`level_price`, `HALVING_LEVELS` and the deep-target constants are imported from
the engine, so the swing this measures is the swing the engine measures.

What is the same:
  * mother, swing anchor (near anchor freezes on 2 green closes, far anchor on
    2 red closes), rungs at L2 L3 L4 L6 L8 L12 L16 below the swing,
  * one basket, one target: average buy + 0.25 of the span, or 0.5 once a rung
    at L4 or deeper has filled,
  * a close back above the mother ends it -- and if nothing is bought yet it
    REBASES instead, taking the best of the next 5 bars as the new mother,
  * no fill and exit on the same bar (the high that pays and the low that buys
    are unordered inside one candle).

What is different, and has to be:
  * money is shares, not lots of premium: an equal rupee slice per rung,
    rounded down to whole shares with one share as the floor, capped per
    ladder and again by the account,
  * no expiry, no DTE gate, no theta, no strike walk -- a share does not expire,
    which removes the single biggest loss in the option version,
  * costs are NSE delivery: 0.1185% buy, 0.1035% sell, plus a flat depository
    charge per selling day.

Two of the engine's later levers are ported too, because both are about money
rather than contracts and so mean something to a share: `--escalate` (the Nth
buy takes N slices, so the deeper the ladder is forced the more it commits) and
`--fill-on-close` (a touch only TRIGGERS the buy; the price paid is that
candle's close). The option-only ones -- time stop, expiry square-off, intraday
square-off -- are left out: a share has no expiry and no theta clock.

One ladder runs at a time, the way the engine's campaign does; when it ends,
the scan looks for the next mother.

    python3 tools/fib_ladder_nse.py --symbol ADANIENT --capital 200000
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.fib_touch_ladder import (  # noqa: E402
    DEEP_TARGET_FRACTION,
    DEEP_TARGET_FROM_LEVEL,
    HALVING_LEVELS,
    INVOLVEMENT_CANDLES,
    REBASE_WATCH_BARS,
    find_swing_anchor,
    level_price,
)
from tools.fetch_stock_5m import canonical  # noqa: E402
from tools.rule3070_nse import DP_PER_SELL_DAY, FEE_BUY, FEE_SELL, load_candles, rs  # noqa: E402


def to_bars(df, minutes: int = 5) -> List["Bar"]:
    """5m candles, optionally regrouped into a slower chart.

    The engine reads the mother on 1m/5m/15m/1h and Phil picks the slower ones
    "when he wants a wider swing under the ladder". On a share that is not a
    preference, it is the whole difference between a target worth Rs 4 and one
    worth Rs 40: the target is a quarter of the SPAN, and a 5-minute swing on a
    Rs 3,000 stock spans a few rupees. Groups are cut from the session open, so
    a bar never straddles two days.
    """
    step = max(minutes // 5, 1)
    out: List[Bar] = []
    bucket: List = []
    day = None
    for ts, row in zip(df.index, df.itertuples()):
        if day != ts.date() or len(bucket) == step:
            if bucket:
                out.append(_fold(bucket))
            bucket, day = [], ts.date()
        bucket.append((ts, row.open, row.high, row.low, row.close))
    if bucket:
        out.append(_fold(bucket))
    return out


def _fold(bucket: List) -> "Bar":
    return Bar(
        bucket[0][0],
        bucket[0][1],
        max(b[2] for b in bucket),
        min(b[3] for b in bucket),
        bucket[-1][4],
    )


class Bar:
    """What find_swing_anchor needs: timestamp, open, high, low, close."""

    __slots__ = ("timestamp", "open", "high", "low", "close")

    def __init__(self, timestamp, o, h, low, c):
        self.timestamp, self.open, self.high, self.low, self.close = timestamp, o, h, low, c


@dataclass
class Fill:
    ts: object
    level: int
    price: float
    shares: int


@dataclass
class Ladder:
    mother_ts: object
    mother_high: float
    anchor_high: float
    anchor_low: float
    confirmed_ts: object
    rung_prices: List[tuple]  # (level, price), shallow first
    filled_levels: set = field(default_factory=set)
    fills: List[Fill] = field(default_factory=list)
    exit_ts: object = None
    exit_price: Optional[float] = None
    reason: str = "OPEN"

    @property
    def span(self) -> float:
        return self.anchor_high - self.anchor_low

    @property
    def cost(self) -> float:
        return sum(f.price * f.shares for f in self.fills)

    @property
    def shares(self) -> int:
        return sum(f.shares for f in self.fills)

    @property
    def avg_buy(self) -> float:
        return self.cost / self.shares if self.shares else 0.0

    @property
    def target(self) -> Optional[float]:
        if not self.fills:
            return None
        deepest = max(f.level for f in self.fills)
        fraction = DEEP_TARGET_FRACTION if deepest >= DEEP_TARGET_FROM_LEVEL else 0.25
        return self.avg_buy + fraction * self.span


def run(
    bars: List[Bar],
    capital: float,
    ladder_cap: float,
    stop_loss: float,
    min_span_pct: float = 0.0,
    escalate: bool = False,
    fill_on_close: bool = False,
) -> List[Ladder]:
    """Walk the tape once. One ladder at a time; the next swing is hunted the
    moment the last one ends.

    Two positions are tracked, and they are not the same thing: the MOTHER is
    the standing structure top whose break ends a campaign, while the ORIGIN is
    where the swing search starts. They begin together, but after a campaign
    ends the origin moves to the next bar while the mother stands — searching
    from the old mother again would keep re-finding the same swing it already
    traded (which is how this first read 13 ladders in three years)."""
    rung_budget = ladder_cap / len(HALVING_LEVELS)
    done: List[Ladder] = []
    active: Optional[Ladder] = None

    mother_idx = 0  # the standing structure top: high-water mark since the
    mother_high = bars[0].high  # last candle that CLOSED above it
    origin = 0  # where the next swing search starts
    rebase_watch: List[int] = []

    for i in range(1, len(bars)):
        bar = bars[i]

        if active is not None:
            # ── the mother break is checked BEFORE any fill, so a bar that
            # breaks it never also buys on the way past (engine order) ──
            if rebase_watch:
                rebase_watch.append(i)
                if len(rebase_watch) >= REBASE_WATCH_BARS:
                    best = max(rebase_watch, key=lambda j: bars[j].high)
                    mother_idx, mother_high = best, bars[best].high
                    origin, rebase_watch, active = best, [], None
                continue
            if bar.close > active.mother_high:
                if not active.fills:
                    rebase_watch = [i]
                    continue
                active.exit_ts, active.exit_price = bar.timestamp, bar.close
                active.reason = "MOTHER BROKEN"
                done.append(active)
                active, origin = None, i + 1
                mother_idx, mother_high = i, bar.high
                continue

            target = active.target
            can_sell = active.fills and (active.fills[-1].ts < bar.timestamp)
            if can_sell and target is not None and bar.high >= target:
                # a resting limit sell: a gap through it sells at the open
                active.exit_ts = bar.timestamp
                active.exit_price = max(target, bar.open)
                active.reason = "TARGET"
                done.append(active)
                active, origin = None, i + 1
                continue
            if can_sell and stop_loss:
                value = active.shares * bar.close
                if value <= active.cost * (1 - stop_loss):
                    active.exit_ts, active.exit_price = bar.timestamp, bar.close
                    active.reason = "STOP LOSS"
                    done.append(active)
                    active, origin = None, i + 1
                    continue

            # ── buy every level this candle touched, shallowest first ──
            for level, line in active.rung_prices:
                if level in active.filled_levels:
                    continue
                if bar.low > line:
                    break  # levels are ordered shallow-first
                if fill_on_close:
                    # the touch is only the trigger; the price paid is where the
                    # candle actually closed, which is usually BELOW the line on
                    # a candle that punched through it
                    price = bar.close
                else:
                    # a rung is a limit buy resting BELOW the market, so an open
                    # under the line fills there, better than the line
                    price = min(line, bar.open)
                slices = len(active.fills) + 1 if escalate else 1
                shares = max(int(rung_budget * slices / price), 1)
                if active.cost + shares * price > ladder_cap or active.cost + shares * price > capital:
                    break
                active.filled_levels.add(level)
                active.fills.append(Fill(bar.timestamp, level, price, shares))
            continue

        # ── no ladder running: keep the standing mother and hunt a swing ──
        if bar.high > mother_high:
            # a new high-water mark is the new standing top, and the swing
            # search restarts under it (a close above it is the same event,
            # since the close cannot beat the high)
            mother_idx, mother_high = i, bar.high
            origin = i
            continue
        if i <= origin:
            continue
        window = bars[origin : i + 1]
        anchor = find_swing_anchor(window, bars[origin].timestamp, "CE", involvement=INVOLVEMENT_CANDLES)
        if anchor is None or anchor.confirmed_at != bar.timestamp:
            continue  # nothing may trade before both anchors have frozen
        if min_span_pct and anchor.span < min_span_pct * anchor.high:
            # SPAN FLOOR (the engine's min_span_points, as a percentage so it
            # ports across stocks): a swing this small asks for a target worth
            # less than the cost of getting in and out of it.
            origin = i + 1
            continue
        active = Ladder(
            mother_ts=bars[mother_idx].timestamp,
            mother_high=mother_high,
            anchor_high=anchor.high,
            anchor_low=anchor.low,
            confirmed_ts=anchor.confirmed_at,
            rung_prices=[(int(lv), level_price("CE", anchor.high, anchor.low, lv)) for lv in HALVING_LEVELS],
        )

    if active is not None and active.fills:
        active.reason = "OPEN"
        done.append(active)
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="ADANIENT")
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--capital", type=float, default=200000.0)
    ap.add_argument("--ladder-cap", type=float, default=75000.0, help="rupees one ladder may hold (Phil's 75k)")
    ap.add_argument("--stop-loss", type=float, default=0.0, help="close the basket this fraction down (0 = off)")
    ap.add_argument("--tf", type=int, default=5, help="chart the geometry is read on, in minutes (5, 15, 60)")
    ap.add_argument(
        "--min-span", type=float, default=0.0, help="span floor as %% of price (the engine's min_span_points)"
    )
    ap.add_argument("--escalate", action="store_true", help="the Nth buy takes N slices")
    ap.add_argument("--fill-on-close", action="store_true", help="a touch triggers; the close is the price")
    ap.add_argument("--dp", type=float, default=DP_PER_SELL_DAY)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    symbol = canonical(args.symbol)
    df = load_candles(symbol, args.months)
    bars = to_bars(df, args.tf)
    ladders = run(
        bars,
        args.capital,
        args.ladder_cap,
        args.stop_loss,
        args.min_span / 100.0,
        escalate=args.escalate,
        fill_on_close=args.fill_on_close,
    )

    traded = [lad for lad in ladders if lad.fills]
    last_close = df["close"].iloc[-1]
    span_days = (df.index[-1] - df.index[0]).days
    closed, open_, sell_days = [], [], {}
    for lad in traded:
        proceeds = lad.shares * (lad.exit_price if lad.exit_price else last_close)
        net = proceeds - lad.cost - FEE_BUY * lad.cost - FEE_SELL * proceeds
        if lad.reason == "OPEN":
            open_.append((lad, lad.cost, lad.shares * last_close - lad.cost))
        else:
            closed.append([lad, lad.cost, net])
            sell_days.setdefault(lad.exit_ts.date(), []).append(len(closed) - 1)
    for _day, idxs in sell_days.items():
        for i in idxs:
            closed[i][2] -= args.dp / len(idxs)

    total = sum(row[2] for row in closed)
    bag = sum(u for _, _, u in open_)
    yr = (span_days / 365.25) or 1
    by_reason: dict = {}
    for lad, _cost, net in closed:
        wins, amount = by_reason.get(lad.reason, (0, 0.0))
        by_reason[lad.reason] = (wins + 1, amount + net)

    print(f"{symbol}  {df.index[0].date()} -> {df.index[-1].date()}  ({span_days} days)")
    print(f"account {rs(args.capital)}, one ladder at a time, {rs(args.ladder_cap)} per ladder")
    print(f"ladders that bought something: {len(traded)}  (of {len(ladders)} built)")
    print(f"CLOSED net (all costs in): {rs(total)} over {len(closed)} ladders")
    for reason in sorted(by_reason):
        wins, amount = by_reason[reason]
        print(f"   {reason:<14} {wins:>4} ladders   {rs(amount)}")
    print(f"losing closed ladders: {sum(1 for row in closed if row[2] < 0)}")
    print(f"open at the end: {len(open_)} holding {rs(sum(c for _, c, _ in open_))}, marked {rs(bag)}")
    print(f"NET with the bag: {rs(total + bag)}  =  {(total + bag) / yr / args.capital * 100:.2f}%/yr")
    if closed and not args.quiet:
        rungs = sorted(len(lad.fills) for lad, _c, _n in closed)
        print(f"rungs filled per ladder: median {rungs[len(rungs) // 2]}, max {max(rungs)}")
        worst = min(closed, key=lambda row: row[2])
        print(f"worst ladder: {rs(worst[2])} ({worst[0].reason}, mother {worst[0].mother_ts:%Y-%m-%d %H:%M})")


if __name__ == "__main__":
    main()
