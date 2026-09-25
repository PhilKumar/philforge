"""The ladder: accumulate against the move, no stop, exit on the BOOK.

This is the part of the course nothing we own has ever modelled. Every backtest
so far priced ONE entry, ONE exit and ONE direction — and his method is none of
those. Day17 puts it plainly: taking the trade is about 20% of the work, the
80% is what happens after. This measures the 80%.

THE RULES, as written in CLASS_PLAYBOOK.md, not as I imagine them
-----------------------------------------------------------------
  * A campaign starts from a MOTHER CANDLE. "Reset the chart so that your
    starting point becomes the first candle." Its high is the recovery
    reference; its range is the unit everything is measured in.
  * Buys go in BELOW, at the boundary indices he names (T18: 0, 1, 2, 4, 8
    units under the mother's low), never above.
  * Sizes ladder 20 / 30 / 50 within a zone (T3), and trading capital is capped
    at 50% of the account (T6) — the rest is not a trading fund.
  * There is NO STOP (T11). A ladder is defended with unspent capital, not with
    a price.
  * Exit is measured off the BOOK, not the leg (T7): 0.25 and 0.5 of the
    distance from the AVERAGE entry back toward the mother candle (T10).
    Worked example from the playbook — mother 100, average 80: exits at 85 and
    90. How much leaves at each is a free parameter Phil says the backtest
    should decide, so it is swept, not assumed.

WHAT THIS IS REALLY ASKING. A system with no stop only works if the position
always comes back. So the headline is not the profit — it is how deep it went,
how long it took, how much capital it demanded, and HOW OFTEN IT NEVER CAME
BACK AT ALL. A ladder that returns money on 98% of campaigns and is still
holding the other 2% at the end of the data has not made money; it has borrowed
it from a campaign that has not finished losing yet.

Index points and index-sized capital, deliberately. Option pricing would add
decay and spread on top of a mechanic that has never been measured alone.
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.ema_level_confluence import load_clean_minutes  # noqa: E402

BOUNDARIES = (0.0, 1.0, 2.0, 4.0, 8.0)  # T18, in mother-candle ranges below the low
ZONE_SPLIT = (0.20, 0.30, 0.50)  # T3, within each boundary's own fund


# ─────────────────────────────────────────────────────────── the real bill ──
# A leveraged ladder pays three separate tolls and the cash test paid none of
# them. In order of how much they bite:
#
#   CARRY.  A long futures position is a financed position: the future trades
#           above spot by roughly the cost of money, and that premium decays to
#           zero by expiry. Hold for a month and you have paid a month of
#           interest on the NOTIONAL, not on your capital. At 8x that is eight
#           times the rate on your own money.
#   ROLL.   NIFTY futures expire monthly. A campaign that outlives its contract
#           must sell the near and buy the far: two more orders, two more
#           spreads, every month it stays open.
#   TICKET. Brokerage, STT on the sell, exchange and SEBI charges, GST on top of
#           those, stamp duty on the buy. Small per trade, not small over 647.
CARRY_ANNUAL = 0.065  # cost of carry built into the futures basis
BROKERAGE_PER_ORDER = 20.0
STT_SELL = 0.000125  # 0.0125% of sell notional, futures
TXN_CHARGE = 0.000019
SEBI_CHARGE = 0.000001
STAMP_BUY = 0.00002
GST = 0.18


def ticket_costs(buy_notional: float, sell_notional: float) -> float:
    brokerage = BROKERAGE_PER_ORDER * 2
    txn = (buy_notional + sell_notional) * TXN_CHARGE
    sebi = (buy_notional + sell_notional) * SEBI_CHARGE
    gst = (brokerage + txn + sebi) * GST
    return brokerage + txn + sebi + gst + sell_notional * STT_SELL + buy_notional * STAMP_BUY


def rolls_between(start, end) -> int:
    """Monthly expiries crossed — the last Thursday of each month."""
    if start is None or end is None:
        return 0
    n, cur = 0, pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    while cur < end:
        month_end = (cur + pd.offsets.MonthEnd(0)).normalize()
        last_thu = month_end - pd.Timedelta(days=(month_end.weekday() - 3) % 7)
        if cur <= last_thu < end:
            n += 1
        cur = (month_end + pd.Timedelta(days=1)).normalize()
    return n


class Campaign:
    """T4: "Find the VISIBLE LOW on that timeframe. Deploy that timeframe's
    money only below that low." The first version bought at the mother candle's
    own low, one bar under the high -- so the book's average sat a few points
    below the mother and the 0.25 exit was a quarter of ONE CANDLE's range.
    A target that small is hit by noise, which is why 567 of 569 campaigns
    "recovered". That was measuring the market's tendency to wobble, not his
    ladder."""

    def __init__(self, mother_ts, mother_high, visible_low, unit, capital):
        self.mother_ts = mother_ts
        self.mother_high = mother_high
        self.unit = unit
        self.levels = [visible_low - k * unit for k in BOUNDARIES]
        self.filled = [False] * len(self.levels)
        self.capital = capital
        self.qty = 0.0
        self.cost = 0.0
        self.legs = []
        self.exits = []
        self.low_water = visible_low
        self.open_ts = None
        self.closed_ts = None

    @property
    def average(self) -> float:
        return self.cost / self.qty if self.qty else 0.0

    def target(self, fraction: float) -> float:
        """0.25 or 0.5 of the way from the average entry back to the mother."""
        avg = self.average
        return avg + (self.mother_high - avg) * fraction

    def buy(self, when, price, level_index, share_of_capital):
        # Size in "units of index", so one unit of capital buys one point of
        # index. The absolute size is arbitrary; what matters is the RATIO
        # between legs and the capital ceiling, which is what the rules fix.
        spend = self.capital * share_of_capital
        qty = spend / price
        self.qty += qty
        self.cost += qty * price
        self.filled[level_index] = True
        self.legs.append({"at": when, "price": price, "qty": qty, "level": level_index})
        if self.open_ts is None:
            self.open_ts = when

    def sell(self, when, price, portion, reason):
        qty = self.qty * portion
        if qty <= 0:
            return
        proceeds = qty * price
        cost_out = self.average * qty
        self.exits.append({"at": when, "price": price, "qty": qty, "pnl": proceeds - cost_out, "reason": reason})
        self.qty -= qty
        self.cost -= cost_out
        if self.qty <= 1e-9:
            self.qty = 0.0
            self.cost = 0.0
            self.closed_ts = when

    @property
    def realised(self) -> float:
        return sum(e["pnl"] for e in self.exits)

    @property
    def deployed(self) -> float:
        return sum(leg["qty"] * leg["price"] for leg in self.legs)

    # -- LEVERAGE. The cash test said the mechanic is safe but earns 0.1-0.6% of
    # the money used. He trades on margin, which multiplies the return AND the
    # adverse excursion -- and a ladder with no stop cannot answer a margin call
    # by cutting. So the number that matters is not the profit, it is whether
    # the account ever goes to zero while waiting to be right.
    def equity(self, capital: float, mark: float, leverage: float) -> float:
        """Capital plus realised plus the open position marked to market."""
        unrealised = (mark - self.average) * self.qty * leverage if self.qty else 0.0
        return capital + self.realised * leverage + unrealised


def find_mothers(bars: pd.DataFrame, lookback: int) -> pd.Series:
    """A mother candle is a local swing HIGH — the "starting point" you reset the
    chart to. Taking every bar would start a campaign every five minutes; taking
    the highest bar of a rolling window takes the ones a person would mark."""
    high = bars["high"]
    return high == high.rolling(lookback, center=True, min_periods=1).max()


def run(args) -> list:
    minute = load_clean_minutes()
    minute = minute[(minute.index >= args.from_date) & (minute.index <= args.to_date)]
    bars = (
        minute.resample(f"{args.bar_minutes}min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    bars = bars.between_time("09:15", "15:29")
    is_mother = find_mothers(bars, args.mother_lookback)

    campaigns: list = []
    live: list = []
    for ts, bar in bars.iterrows():
        low, high, close = float(bar["low"]), float(bar["high"]), float(bar["close"])

        # 1. existing campaigns: fill deeper legs, then look for the book's exits
        #
        # INTRABAR LOOK-AHEAD, found 26-Sep-2026 by pricing these campaigns as
        # options: the buys and the exit were landing in the SAME bar — filled at
        # its low, exited at its high — and the option carried one price for both
        # ends, which is what exposed it. Inside a bar we do not know the order
        # of the low and the high, so a book may not be opened and closed in one.
        # One level a bar, and no exit on a bar that bought.
        for c in list(live):
            bought_this_bar = False
            for i, level in enumerate(c.levels):
                if bought_this_bar:
                    break
                if not c.filled[i] and low <= level:
                    if len(c.legs) >= args.max_legs:
                        break
                    share = ZONE_SPLIT[min(len(c.legs), len(ZONE_SPLIT) - 1)] * args.trading_fraction
                    # T6 IS A CEILING, NOT A SUGGESTION. The first version let
                    # five legs of 10/15/25/25/25% spend 100% of the account --
                    # the half that "is never a trading fund" was being traded.
                    room = c.capital * args.trading_fraction - c.deployed
                    if room <= 0:
                        break
                    spend = min(c.capital * share, room)
                    c.buy(ts, min(level, float(bar["open"])), i, spend / c.capital)
                    bought_this_bar = True
            c.low_water = min(c.low_water, low)
            if c.qty:
                eq = c.equity(args.capital, low, args.leverage)
                if eq < getattr(c, "worst_equity", args.capital):
                    c.worst_equity = eq
                if eq <= 0 and not getattr(c, "blown", False):
                    c.blown = True
            if c.qty > 0 and not bought_this_bar:
                first, second = c.target(0.25), c.target(0.5)
                if high >= first and not any(e["reason"] == "0.25" for e in c.exits):
                    c.sell(ts, first, args.first_exit_portion, "0.25")
                if c.qty > 0 and high >= second:
                    c.sell(ts, second, 1.0, "0.5")
            if c.qty <= 0:
                live.remove(c)

        # 2. a new campaign starts at a marked mother candle
        if is_mother.get(ts, False) and len(live) < args.max_concurrent:
            # PHIL, 25-Sep-2026: "The visible low is on the same timeframe
            # you're trading." With the chart reset so the mother IS the first
            # candle, the visible low at that instant is the mother's own low —
            # and the boundaries walk down from there in units of the mother's
            # range (T18: 0, 1, 2, 4, 8). Each timeframe does this on its own
            # bars with its own fund, which is what the 1m -> 1H progression in
            # Part 4 means.
            #
            # A mother has to be worth marking. Without a floor on its range the
            # 0.25 exit is a quarter of a nothing-candle and noise closes every
            # campaign, which is exactly how the first run got 567 of 569.
            unit = high - low
            if unit < args.min_unit:
                continue
            c = Campaign(ts, high, low, unit, args.capital)
            campaigns.append(c)
            live.append(c)

    return campaigns


def report(campaigns: list, args) -> None:
    started = [c for c in campaigns if c.legs]
    closed = [c for c in started if c.qty == 0]
    open_still = [c for c in started if c.qty > 0]
    never = len(open_still)
    print(f"mother candles marked      {len(campaigns):>8,}")
    print(f"campaigns that opened      {len(started):>8,}")
    print(f"  closed (book recovered)  {len(closed):>8,}")
    print(f"  STILL OPEN at the end    {never:>8,}   <- the ones the ladder never rescued")
    if not started:
        return
    blown = [c for c in started if getattr(c, "blown", False)]
    if args.leverage > 1:
        worst = min((getattr(c, "worst_equity", args.capital) for c in started), default=args.capital)
        print(f"\nAT {args.leverage:g}x LEVERAGE")
        print(f"  campaigns that took the account to ZERO   {len(blown):>6,}")
        print(f"  worst equity seen in any campaign         {worst:>12,.0f} of {args.capital:,.0f}")
    gross = sum(c.realised for c in closed) * args.leverage
    carry = tickets = slip = roll_cost = 0.0
    n_rolls = 0
    for c in closed:
        notional = c.deployed * args.leverage
        days = max(0.0, (c.closed_ts - c.open_ts).total_seconds() / 86400.0)
        carry += notional * CARRY_ANNUAL * days / 365.0
        tickets += ticket_costs(notional, notional)
        rolls = rolls_between(c.open_ts, c.closed_ts)
        n_rolls += rolls
        roll_cost += rolls * (ticket_costs(notional, notional) + notional * args.slippage_points / 20000.0)
        slip += 2 * notional * args.slippage_points / 20000.0
    bill = carry + tickets + slip + roll_cost
    realised = gross - bill
    deployed = sum(c.deployed for c in closed)
    if args.leverage > 1 or args.slippage_points:
        print(f"  gross before costs {gross:>14,.0f}")
        print(f"     carry           {carry:>14,.0f}")
        print(f"     rolls ({n_rolls:>3})      {roll_cost:>14,.0f}")
        print(f"     slippage        {slip:>14,.0f}")
        print(f"     brokerage/STT   {tickets:>14,.0f}")
        print(f"  costs charged      {bill:>14,.0f}")
    # RETURN ON THE MONEY ACTUALLY USED. The ladder spends a tenth of the
    # account on a typical campaign and holds it for hours, so measuring against
    # the whole account for the whole period flatters nothing and explains
    # nothing. Both numbers belong in the open.
    print(
        f"\nrealised on the closed ones {realised:>12,.0f}   on {deployed:>12,.0f} deployed"
        f"   = {100.0 * realised / deployed if deployed else 0:>5.2f}% of money used"
    )
    if closed:
        days = [(c.closed_ts - c.open_ts).total_seconds() / 86400.0 for c in closed]
        legs = [len(c.legs) for c in closed]
        print(f"  median hold                {sorted(days)[len(days)//2]:>8.1f} days" f"   longest {max(days):>6.1f}")
        print(f"  median legs used           {sorted(legs)[len(legs)//2]:>8}" f"   most {max(legs):>6}")
    if open_still:
        under = [(c.average - c.low_water) for c in open_still]
        held = [len(c.legs) for c in open_still]
        print("\nthe unrescued ones:")
        print(f"  median legs spent          {sorted(held)[len(held)//2]:>8}")
        print(f"  median depth below average {sorted(under)[len(under)//2]:>8.0f} points")
        worst = max(open_still, key=lambda c: c.average - c.low_water)
        print(
            f"  worst: opened {worst.open_ts:%Y-%m-%d}, {len(worst.legs)} legs, "
            f"average {worst.average:,.0f}, went to {worst.low_water:,.0f}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2021-01-01")
    ap.add_argument("--to-date", default="2026-08-21")
    ap.add_argument("--bar-minutes", type=int, default=5)
    ap.add_argument("--mother-lookback", type=int, default=78, help="bars each side of a swing high")
    ap.add_argument("--min-unit", type=float, default=30.0, help="the mother candle's own range must be at least this")
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--trading-fraction", type=float, default=0.50, help="T6: half is never a trading fund")
    ap.add_argument("--first-exit-portion", type=float, default=0.50, help="how much leaves at 0.25")
    ap.add_argument("--max-legs", type=int, default=len(BOUNDARIES))
    ap.add_argument("--max-concurrent", type=int, default=50)
    ap.add_argument(
        "--slippage-points",
        type=float,
        default=1.0,
        help="index points given up per side (20000 is a rough NIFTY level)",
    )
    ap.add_argument(
        "--leverage", type=float, default=1.0, help="NIFTY futures margin is roughly 12% of notional, so about 8x"
    )
    args = ap.parse_args()

    campaigns = run(args)
    print(
        f"{args.from_date} -> {args.to_date}, {args.bar_minutes}m bars, "
        f"mother = swing high over {args.mother_lookback} bars, "
        f"{args.first_exit_portion:.0%} out at 0.25\n"
    )
    report(campaigns, args)


if __name__ == "__main__":
    main()
