"""The ladder's INTRADAY campaigns, re-priced as options instead of futures.

Why only the intraday ones. Costed on futures the ladder nets 3.6% a year on 5m
bars and 7.7% on 15m, and CARRY is the biggest single toll — a long future is a
financed position, and at 8x you pay that rate on eight times your money. But
the holding times are not spread evenly:

    closed same day   81%        over a month   2%
    1-7 days          13%        ... and that 2% holds 59% of all notional-days

So the carry is paid almost entirely by a handful of long campaigns, and the
81% that open and close inside one session pay it for nothing. An option has no
carry at all. This prices exactly those campaigns as options and asks whether
they come out ahead of the futures version of the same trades.

It does NOT touch the multi-day tail, and that is deliberate: a weekly option
cannot wait a month, and buying time value for a ladder that might hold two
hundred days is ruinous. The honest shape of the answer was always going to be
"options for the intraday part, futures or nothing for the tail".

Every leg is priced from the archives — Upstox real strikes from 2024-10, Dhan's
moneyness-keyed store back to 2021 — with statutory option charges on each round
trip. Legs the archive cannot price are dropped and counted, never guessed.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from options.charges import round_trip_charges  # noqa: E402
from options.dhan_listed import DhanListedSource  # noqa: E402
from options.upstox_archive import DEFAULT_ROOT as UPSTOX_ROOT  # noqa: E402
from options.upstox_archive import UpstoxArchiveSource  # noqa: E402
from tools.ema_options_backtest import STORES, Contract, HybridSource  # noqa: E402
from tools.ladder_backtest import run as run_ladder  # noqa: E402
from tools.nifty_expiry_calendar import STRIKE_STEP, weekly_expiries  # noqa: E402

SQUARE_OFF = time(15, 10)


def price_campaigns(campaigns: list, args) -> dict:
    days = sorted({c.open_ts.date() for c in campaigns})
    expiries = weekly_expiries(days)
    dhan = DhanListedSource(expiries, STORES, "NIFTY", nearest_within=0)
    upstox = UpstoxArchiveSource(UPSTOX_ROOT, "NIFTY")
    source = {"dhan": dhan, "upstox": upstox}.get(args.pricing) or HybridSource(dhan, upstox)

    def expiry_for(day: date) -> Optional[date]:
        return next((e for e in expiries if e >= day), None)

    out = {"net": 0.0, "traded": 0, "dropped": 0, "futures_net": 0.0, "legs": 0}
    slip = args.slippage_pct / 100.0
    for c in campaigns:
        expiry = expiry_for(c.open_ts.date())
        if expiry is None:
            out["dropped"] += 1
            continue
        # ONE contract for the whole campaign. The ladder is one position that
        # gets bigger, not a series of separate trades, so every leg buys the
        # same strike -- chosen at the first leg, in the money by --strike-offset
        # so the option actually tracks the index it is standing in for.
        first = c.legs[0]
        strike = int(round(first["price"] / STRIKE_STEP) * STRIKE_STEP) - args.strike_offset * int(STRIKE_STEP)
        contract = Contract(expiry=expiry, strike=strike, option_type="CE")

        units = 0.0
        cost = 0.0
        ok = True
        for leg in c.legs:
            px, _ = source.lookup_forward(leg["at"], contract, args.exit_search_minutes)
            if px is None or px <= 0:
                ok = False
                break
            spend = leg["qty"] * leg["price"]  # the same rupees the futures leg used
            buy = px * (1 + slip)
            units += spend / buy
            cost += spend
        if not ok or units <= 0:
            out["dropped"] += 1
            continue

        proceeds = 0.0
        for ex in c.exits:
            px, _ = source.lookup_forward(ex["at"], contract, args.exit_search_minutes)
            if px is None:
                px = max(0.0, ex["price"] - strike)  # intrinsic floor, never zero
            sell = px * (1 - slip)
            portion = ex["qty"] / (c.qty + sum(e["qty"] for e in c.exits))
            proceeds += sell * units * portion
        qty_for_charges = max(1, int(units))
        charges = round_trip_charges(
            trade_date=c.open_ts.date(),
            buy_premium=cost / units,
            sell_premium=(proceeds / units) if units else 0.0,
            quantity=qty_for_charges,
        ).total
        out["net"] += proceeds - cost - charges
        out["futures_net"] += c.realised * args.leverage
        out["traded"] += 1
        out["legs"] += len(c.legs)
    out["source"] = source
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2024-10-01")
    ap.add_argument("--to-date", default="2026-08-21")
    ap.add_argument("--bar-minutes", type=int, default=15)
    ap.add_argument("--mother-lookback", type=int, default=26)
    ap.add_argument("--min-unit", type=float, default=60.0)
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--trading-fraction", type=float, default=0.50)
    ap.add_argument("--first-exit-portion", type=float, default=0.50)
    ap.add_argument("--max-legs", type=int, default=5)
    ap.add_argument("--max-concurrent", type=int, default=50)
    ap.add_argument("--leverage", type=float, default=8.0, help="what the futures comparison used")
    ap.add_argument("--slippage-points", type=float, default=1.0)
    ap.add_argument("--slippage-pct", type=float, default=0.30, help="on the option premium")
    ap.add_argument("--strike-offset", type=int, default=2, help="strikes in the money")
    ap.add_argument("--exit-search-minutes", type=int, default=15)
    ap.add_argument("--pricing", choices=("hybrid", "dhan", "upstox"), default="hybrid")
    args = ap.parse_args()

    campaigns = run_ladder(args)
    intraday = [c for c in campaigns if c.legs and c.qty == 0 and c.closed_ts.date() == c.open_ts.date()]
    multi = [c for c in campaigns if c.legs and c.qty == 0 and c.closed_ts.date() != c.open_ts.date()]
    print(f"{args.from_date} -> {args.to_date}, {args.bar_minutes}m bars")
    print(
        f"campaigns closed: {len(intraday) + len(multi):,}   intraday {len(intraday):,}, "
        f"multi-day {len(multi):,} (left alone)\n"
    )

    res = price_campaigns(intraday, args)
    print(f"pricing: {res['source'].report() if hasattr(res['source'], 'report') else args.pricing}")
    print(f"priced {res['traded']:,} campaigns, dropped {res['dropped']:,} the archive could not price\n")
    print(f"{'':<26}{'net':>14}")
    print(f"{'as OPTIONS (ITM ' + str(args.strike_offset) + ')':<26}{res['net']:>14,.0f}")
    print(f"{'the same campaigns, 8x futures':<26}{res['futures_net']:>14,.0f}   (before carry, which they barely pay)")


if __name__ == "__main__":
    main()
