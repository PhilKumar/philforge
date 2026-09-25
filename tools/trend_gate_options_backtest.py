"""The 20-day trend gate, alone, with no pattern at all — and its controls.

The confluence detector failed its null test (25-Sep-2026): random entries on
the same day and side beat it two to one. What was left standing was the part
that had nothing to do with candles — trade WITH the 20-day trend and hold to
the 15:10 square-off. This measures that on its own.

One trade a day. Direction from the 20-day average of DAILY CLOSES STRICTLY
BEFORE today; above it buys a call, below it buys a put. Entry at a fixed
minute, exit at 15:10. Nothing else.

THE CONTROLS ARE THE POINT
--------------------------
A rule that makes money in a rising market has proved nothing. Four arms run on
the identical days, contracts and costs:

    trend    long above the 20-day average, short below it
    long     always a call
    short    always a put
    flip     direction by coin toss, seeded

If `trend` does not beat `long` over the same window, the gate is decoration and
the money came from the market going up. If it does not beat `flip` it is not a
gate at all.

Coverage belongs to the archives: Upstox 2024-10 onward holds real strikes,
Dhan reaches back to 2021 but keyed by moneyness. Quote the Upstox window.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from options.charges import round_trip_charges  # noqa: E402
from options.dhan_listed import DhanListedSource  # noqa: E402
from options.upstox_archive import DEFAULT_ROOT as UPSTOX_ROOT  # noqa: E402
from options.upstox_archive import UpstoxArchiveSource  # noqa: E402
from tools.ema_level_confluence import build  # noqa: E402
from tools.ema_options_backtest import STORES, Contract, HybridSource  # noqa: E402
from tools.nifty_expiry_calendar import STRIKE_STEP, lot_size, weekly_expiries  # noqa: E402

SQUARE_OFF = time(15, 10)
ARMS = ("trend", "long", "short", "flip")


def run(args) -> dict:
    frame = build(args.from_date, args.to_date)
    close = frame["close"]
    daily = frame.groupby("day")["close"].last()
    # STRICTLY BEFORE TODAY. Including today's close in its own average is a
    # look-ahead that would quietly decide the direction using the answer.
    sma20 = daily.shift(1).rolling(20).mean()

    days = sorted(frame["day"].unique())
    expiries = weekly_expiries([pd.Timestamp(d).date() for d in days])
    dhan = DhanListedSource(expiries, STORES, "NIFTY", nearest_within=0)
    upstox = UpstoxArchiveSource(UPSTOX_ROOT, "NIFTY")
    source = {"dhan": dhan, "upstox": upstox}.get(args.pricing) or HybridSource(dhan, upstox)

    rng = random.Random(args.seed)
    books = {arm: [] for arm in ARMS}
    skipped = {"no_trend": 0, "no_session": 0, "no_price": 0, "no_expiry": 0}

    entry_at = time(*(int(x) for x in args.entry_time.split(":")))
    for day in days:
        day = pd.Timestamp(day)
        above = sma20.get(day)
        if above is None or pd.isna(above):
            skipped["no_trend"] += 1
            continue
        # The gate reads YESTERDAY's close against the average of the twenty
        # before it. Today's close is not knowable at 09:20.
        prev_close = daily.shift(1).get(day)
        if prev_close is None or pd.isna(prev_close):
            skipped["no_trend"] += 1
            continue
        is_up = bool(prev_close > above)

        session = close[frame["day"] == day]
        entry_bars = session[session.index.time >= entry_at]
        exit_bars = session[session.index.time <= SQUARE_OFF]
        if entry_bars.empty or exit_bars.empty:
            skipped["no_session"] += 1
            continue
        entry_ts, entry_spot = entry_bars.index[0], float(entry_bars.iloc[0])
        exit_ts, exit_spot = exit_bars.index[-1], float(exit_bars.iloc[-1])
        if entry_ts >= exit_ts:
            skipped["no_session"] += 1
            continue

        expiry = next((e for e in expiries if e >= entry_ts.date()), None)
        if expiry is None:
            skipped["no_expiry"] += 1
            continue

        # ALL FOUR ARMS MUST TRADE THE SAME DAYS, or the comparison is a lie.
        # The first run of this had trend on 260 days, long on 394 and short on
        # 163, because a missing strike skips one arm and not another -- so the
        # arms were being judged on different markets. A day either prices for
        # every arm or it counts for none.
        coin = rng.random() < 0.5
        priced = {}
        for arm in ARMS:
            side = {
                "trend": "CE" if is_up else "PE",
                "long": "CE",
                "short": "PE",
                "flip": "CE" if coin else "PE",
            }[arm]
            want = 1 if side == "CE" else -1
            strike = int(round(entry_spot / STRIKE_STEP) * STRIKE_STEP) - want * args.strike_offset * int(STRIKE_STEP)
            contract = Contract(expiry=expiry, strike=strike, option_type=side)
            entry_px, _ = source.lookup_forward(entry_ts, contract, args.exit_search_minutes)
            if entry_px is None or entry_px <= 0:
                priced = None
                break
            exit_px, _ = source.lookup_forward(exit_ts, contract, args.exit_search_minutes)
            if exit_px is None:
                exit_px = max(0.0, (exit_spot - strike) if side == "CE" else (strike - exit_spot))

            slip = args.slippage_pct / 100.0
            buy, sell = entry_px * (1 + slip), exit_px * (1 - slip)
            qty = args.lots * lot_size(expiry)
            charges = round_trip_charges(
                trade_date=entry_ts.date(), buy_premium=buy, sell_premium=sell, quantity=qty
            ).total
            priced[arm] = (sell - buy) * qty - charges

        if priced is None:
            skipped["no_price"] += 1
            continue
        for arm, net in priced.items():
            books[arm].append(net)
    return books, skipped, source


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2024-10-01")
    ap.add_argument("--to-date", default="2026-08-21")
    ap.add_argument("--entry-time", default="09:20")
    ap.add_argument("--lots", type=int, default=2)
    ap.add_argument("--strike-offset", type=int, default=0)
    ap.add_argument("--slippage-pct", type=float, default=0.30)
    ap.add_argument("--exit-search-minutes", type=int, default=15)
    ap.add_argument("--pricing", choices=("hybrid", "dhan", "upstox"), default="upstox")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    books, skipped, source = run(args)
    print(
        f"{args.from_date} -> {args.to_date}  enter {args.entry_time}, exit 15:10, "
        f"{args.lots} lots, {args.slippage_pct}% slippage, pricing={args.pricing}"
    )
    print(f"skipped: {skipped}\n")
    print(f"{'arm':<8}{'n':>6}{'net':>13}{'avg':>10}{'win%':>7}{'max dd':>12}")
    for arm in ARMS:
        nets = books[arm]
        if not nets:
            print(f"{arm:<8}{'none':>6}")
            continue
        eq = peak = dd = 0.0
        for n in nets:
            eq += n
            peak = max(peak, eq)
            dd = min(dd, eq - peak)
        wins = sum(1 for n in nets if n > 0)
        print(
            f"{arm:<8}{len(nets):>6}{sum(nets):>13,.0f}{sum(nets)/len(nets):>10,.0f}"
            f"{100.0*wins/len(nets):>7.0f}{dd:>12,.0f}"
        )


if __name__ == "__main__":
    main()
