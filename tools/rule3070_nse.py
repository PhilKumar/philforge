"""tools/rule3070_nse.py -- the 30-70 Rule on NSE stocks: the money verdict.

Runs tools/rule3070_engine.py over cached 5-minute stock candles and prices
every campaign the way a delivery (CNC) account is actually charged, which is
NOT one flat percentage:

    buy  = STT 0.10% + exchange txn 0.00297% + SEBI 0.0001% + stamp 0.015%
           + 18% GST on the charges          ~= 0.1185% of turnover
    sell = STT 0.10% + exchange txn 0.00297% + SEBI 0.0001%
           + 18% GST on the charges          ~= 0.1035% of turnover
    plus a FLAT depository charge (~Rs 15.93) per scrip per selling DAY,
    however many shares leave the demat that day.

Brokerage is taken as zero (Zerodha/Groww charge nothing for delivery). The
flat DP charge is the one that decides whether this strategy works on stocks:
a percentage fee scales with the trade, Rs 15.93 does not, so a round that
nets Rs 8 on one share is a LOSS once the share is sold out of demat.

    python3 tools/rule3070_nse.py --symbol ICICIBANK --capital 200000 \
        --minors --budget --min-margin 0.35 --compound --compound-at 25

Open campaigns are marked to the last close and reported separately: a bag is
a cost, not a rounding note.
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.rule3070_engine as engine  # noqa: E402
from tools.fetch_stock_5m import canonical, load  # noqa: E402
from tools.rule3070_engine import run_ladder  # noqa: E402

FEE_BUY = 0.001185
FEE_SELL = 0.001035
DP_PER_SELL_DAY = 15.93


def rs(amount: float) -> str:
    """Rupees the way they are written here: Rs 1,23,456.78 (lakh grouping)."""
    sign = "-" if amount < 0 else ""
    whole, frac = divmod(round(abs(amount), 2), 1)
    digits = f"{int(whole)}"
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        digits = ",".join(parts + [tail])
    return f"{sign}Rs {digits}.{int(round(frac * 100)):02d}"


def load_candles(symbol: str, months: int) -> pd.DataFrame:
    candles = load(symbol, months)
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("ts"), unit="s", utc=True)).tz_convert("Asia/Kolkata")
    df.index.name = "datetime"
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="ICICIBANK")
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--capital", type=float, default=200000.0, help="rupees in the account")
    ap.add_argument("--minors", action="store_true", help="minor Vs under bounce tops while the major is busy")
    ap.add_argument("--budget", action="store_true", help="no buy past the capital total committed")
    ap.add_argument("--max-bands", type=int, default=2, help="crash brake: stop buying past this band (0 = unlimited)")
    ap.add_argument("--min-margin", type=float, default=0.0, help="fee gate: expected win at least this %% of price")
    ap.add_argument(
        "--min-win",
        type=float,
        default=0.0,
        help="rupee gate: a buy waits until the whole position's expected win is at least "
        "this many rupees (the flat DP charge is invisible to a percentage gate)",
    )
    ap.add_argument("--target-at-fill", action="store_true", help="target moves only when a buy fills")
    ap.add_argument("--compound", action="store_true", help="closed profit folds into the purse")
    ap.add_argument("--compound-at", default="25", help="fold schedule as %% of capital, comma-separated")
    ap.add_argument("--lot", type=int, default=1, help="shares per lot (1 = cash equity; 0 = allow fractions)")
    ap.add_argument("--dp", type=float, default=DP_PER_SELL_DAY, help="depository charge per scrip per selling day")
    ap.add_argument("--no-gap-fills", action="store_true", help="fill at the line even when the session gaps past it")
    ap.add_argument("--one-line", action="store_true", help="one working line at a time")
    args = ap.parse_args()

    symbol = canonical(args.symbol)
    engine.CAPITAL = args.capital
    engine.LOT_SIZE = args.lot
    engine.GAP_FILLS = not args.no_gap_fills
    engine.MAX_BANDS = args.max_bands
    engine.MIN_NET_MARGIN = args.min_margin / 100.0
    engine.MIN_WIN_RS = args.min_win
    engine.TARGET_AT_FILL_ONLY = args.target_at_fill
    engine.ENFORCE_BUDGET = args.budget
    engine.COMPOUND_AT_HALF = args.compound
    engine.COMPOUND_SCHEDULE = tuple(float(x) / 100.0 for x in str(args.compound_at).split(",") if x.strip())
    engine.FEE_PER_SIDE = (FEE_BUY + FEE_SELL) / 2  # what the compounding bank books
    if args.one_line:
        engine.MAX_ACTIVE_MINORS = 1

    df = load_candles(symbol, args.months)
    campaigns = run_ladder(df, minors=args.minors)
    last_close = df["close"].iloc[-1]
    span_days = (df.index[-1] - df.index[0]).days

    closed, open_ = [], []
    events = []  # (ts, +cost) on fill, (ts, -cost) at end -- concurrency
    sell_days: dict = {}
    for c in campaigns:
        if not c.fills:
            continue
        cost = sum(f.amount for f in c.fills)
        qty = sum(f.amount / f.price for f in c.fills)
        for f in c.fills:
            events.append((f.ts, f.amount))
        events.append((c.end_ts, -cost))
        if c.status == "TARGET HIT":
            exit_px = c.exit_price or c.target
            proceeds = qty * exit_px
            net = proceeds - cost - FEE_BUY * cost - FEE_SELL * proceeds
            days = (c.target_ts - c.fills[0].ts).total_seconds() / 86400
            closed.append([c, cost, net, days, qty])
            sell_days.setdefault(c.target_ts.date(), []).append(len(closed) - 1)
        else:
            open_.append((c, cost, qty * last_close - cost))

    # The flat DP charge lands once per selling day; split it across that day's
    # sells so a per-trade number carries its real share of it.
    dp_total = args.dp * len(sell_days)
    for day, idxs in sell_days.items():
        for i in idxs:
            closed[i][2] -= args.dp / len(idxs)

    total_net = sum(row[2] for row in closed)
    total_cost = sum(row[1] for row in closed)
    losers = [row for row in closed if row[2] < 0]
    bag = sum(u for _, _, u in open_)
    bag_cost = sum(cst for _, cst, _ in open_)

    events.sort(key=lambda e: (e[0], -e[1]))
    cur = peak = 0.0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)

    shares = [row[4] for row in closed]
    yr = span_days / 365.25 or 1
    print(f"{symbol}  {df.index[0].date()} -> {df.index[-1].date()}  ({span_days} days, {len(df):,} 5m bars)")
    print(
        f"account: {rs(args.capital)}   costs: {FEE_BUY * 100:.4f}% buy / {FEE_SELL * 100:.4f}% sell + {rs(args.dp)} per selling day"
    )
    print(
        f"trades with fills: {len(closed) + len(open_)}  |  closed at target: {len(closed)}  |  still open: {len(open_)}"
    )
    print(f"CLOSED net (all costs in): {rs(total_net)}  on {rs(total_cost)} bought")
    print(f"   of which depository charges: {rs(dp_total)} across {len(sell_days)} selling days")
    print(f"losing closed trades (cost ate the win): {len(losers)}")
    print(f"open bag at the end: {len(open_)} trades holding {rs(bag_cost)}, marked {rs(bag)}")
    print(
        f"NET with the bag: {rs(total_net + bag)}  =  {(total_net + bag) / yr / args.capital * 100:.2f}%/yr on {rs(args.capital)}"
    )
    print(f"max capital ever committed: {rs(peak)}   ({peak / args.capital * 100:.0f}% of the account)")
    if closed:
        med = sorted(row[2] for row in closed)[len(closed) // 2]
        hold = sorted(row[3] for row in closed)
        print(f"per-trade net: median {rs(med)}, average {rs(total_net / len(closed))}")
        print(f"shares per trade: median {sorted(shares)[len(shares) // 2]:.0f}, max {max(shares):.0f}")
        print(f"hold time: median {hold[len(hold) // 2]:.1f} days, longest {max(hold):.0f} days")
    if args.compound:
        print(
            f"purse after reinvestment: {rs(engine.CAPITAL)} (started {rs(args.capital)}, bank {rs(engine._PROFIT_BANK)})"
        )

    by_year: dict = {}
    for c, _cost, net, _d, _q in closed:
        year = c.target_ts.year
        wins, total = by_year.get(year, (0, 0.0))
        by_year[year] = (wins + 1, total + net)
    for year in sorted(by_year):
        wins, total = by_year[year]
        print(f"   {year}: {wins} closed, net {rs(total)}")
    for c, cost, unreal in sorted(open_, key=lambda r: r[2])[:5]:
        print(f"   OPEN since {c.fills[0].ts:%Y-%m-%d}: cost {rs(cost)}, marked {rs(unreal)} ({c.status})")


if __name__ == "__main__":
    main()
