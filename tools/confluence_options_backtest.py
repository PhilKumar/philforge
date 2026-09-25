"""The confluence candle, priced as real weekly options, with FOUR exits.

The index-level result (25-Sep-2026) was +15.9 points held, six of six years
positive, for a confluence candle taken WITH the 20-day trend. Index points are
not money: you trade a weekly option, it decays, it costs to get in and out, and
the archive does not always have the strike. This prices the same signals.

TWO THINGS THIS DOES DIFFERENTLY FROM tools/ema_options_backtest.py
-------------------------------------------------------------------
1. The signals come from the CLEAN 1-minute series, not from the index lifted
   off the option rows' `spot` column. That series has one price per minute, so
   98.9% of its 1m bars and 25.6% of its 5m bars have no wick at all -- fatal
   for a rule about where a candle's base sits. See tools/nifty_clean_1m.py.

2. The exit is not one decision. Day20: "a partial trade -- we cut at 25, cut at
   50, cut at 75, cut at 100. We split the entry, and we split the selling as
   well." Every number measured so far assumed a single clip, so four exits are
   priced side by side:

       next        the whole position at the next level beyond (one clip)
       close       held to the 15:10 square-off (one clip)
       quarters    a quarter out at 25/50/75/100% of the way to that level
       halves      half out at 25%, the rest at 50%  (the "0.25 and 0.5" reading)

   A part that never reaches its station leaves at 15:10 with the remainder.

WHAT THIS CANNOT ANSWER. Phil's ladder ADDS against the move and exits 0.25/0.5
back toward the mother candle from the AVERAGE entry. That is accumulation, not
a directional trade with a target, and it needs the adds modelled before it can
be priced. These four are all single-entry exits; the ladder is the next job.

Coverage is the archives', not ours: Dhan 2021-01 to 2026-08 but keyed by
moneyness (~12 strikes), Upstox 2024-10 to 2026-08 with real strikes. A verdict
belongs to the Upstox window; the Dhan years are a sanity check.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from options.charges import round_trip_charges  # noqa: E402
from options.dhan_listed import DhanListedSource  # noqa: E402
from options.upstox_archive import DEFAULT_ROOT as UPSTOX_ROOT  # noqa: E402
from options.upstox_archive import UpstoxArchiveSource  # noqa: E402
from tools.ema_level_confluence import build, signals  # noqa: E402
from tools.ema_options_backtest import STORES, Contract, HybridSource  # noqa: E402
from tools.nifty_expiry_calendar import STRIKE_STEP, lot_size, weekly_expiries  # noqa: E402

SQUARE_OFF = time(15, 10)  # the broker refuses intraday orders by 15:25
EXIT_STYLES = ("next", "close", "quarters", "halves")
# Where each style takes money off, as a fraction of the entry->target distance,
# and how much of the position goes at each station.
LADDERS = {
    "next": [(1.00, 1.00)],
    "quarters": [(0.25, 0.25), (0.50, 0.25), (0.75, 0.25), (1.00, 0.25)],
    "halves": [(0.25, 0.50), (0.50, 0.50)],
}


@dataclass
class Leg:
    at: datetime
    spot: float
    premium: float
    portion: float
    reason: str
    priced: bool = True


@dataclass
class Trade:
    side: str  # CE or PE
    signal_ts: datetime
    entry_ts: datetime
    entry_spot: float
    target_spot: float
    target_name: str
    strike: int
    expiry: date
    lots: int
    lot: int
    entry_premium: float
    legs: list = field(default_factory=list)
    unpriceable: bool = False

    @property
    def qty(self) -> int:
        return self.lots * self.lot

    def net(self, slippage_pct: float) -> float:
        """Rupees after slippage and statutory charges, summed over the legs."""
        slip = slippage_pct / 100.0
        buy = self.entry_premium * (1 + slip)
        total = 0.0
        for leg in self.legs:
            sell = leg.premium * (1 - slip)
            qty = int(round(self.qty * leg.portion))
            if qty <= 0:
                continue
            gross = (sell - buy) * qty
            charges = round_trip_charges(
                trade_date=self.entry_ts.date(), buy_premium=buy, sell_premium=sell, quantity=qty
            ).total
            total += gross - charges
        return total


class ConfluenceOptionsBacktest:
    def __init__(self, args):
        self.args = args
        self.frame = build(args.from_date, args.to_date)
        self.minute_close = self.frame["close"]

        # THE TREND GATE. Long only above the 20-day average, short only below.
        # Not invented here: it is already the live CE filter, and it is what
        # turned the index result from +1.1 to +15.9 a trade.
        daily = self.frame.groupby("day")["close"].last()
        self.above20 = daily > daily.rolling(20).mean()

        session_days = sorted({d.date() for d in self.frame["day"].unique()})
        self.expiries = weekly_expiries(session_days)
        # WHICH ARCHIVE ANSWERS MATTERS. Dhan is deep but keyed by MONEYNESS --
        # about twelve strikes around the money -- so it can answer with a
        # contract that is not quite the one we asked for. Upstox holds real
        # strikes but starts 2024-10 and only ever kept what PhilForge fetched.
        # A rule that pays on one and not the other has not been measured.
        dhan = DhanListedSource(self.expiries, STORES, "NIFTY", nearest_within=0)
        upstox = UpstoxArchiveSource(UPSTOX_ROOT, "NIFTY")
        if args.pricing == "dhan":
            self.source = dhan
        elif args.pricing == "upstox":
            self.source = upstox
        else:
            self.source = HybridSource(dhan, upstox)
        self.skipped = {"no_expiry": 0, "against_trend": 0, "no_entry_price": 0, "too_late": 0, "unpriceable": 0}

    # -- contracts --------------------------------------------------------
    def expiry_for(self, day: date) -> Optional[date]:
        for e in self.expiries:
            if e >= day:
                return e
        return None

    def strike_for(self, spot: float, side: str) -> int:
        # In the money is a LOWER strike for a call and a HIGHER one for a put,
        # so the offset works against the trade's direction.
        want = 1 if side == "CE" else -1
        return int(round(spot / STRIKE_STEP) * STRIKE_STEP) - want * self.args.strike_offset * int(STRIKE_STEP)

    def premium(self, when: datetime, contract: Contract) -> Optional[float]:
        px, _ = self.source.lookup_forward(when, contract, int(self.args.exit_search_minutes))
        return px

    # -- the path after entry --------------------------------------------
    def session_after(self, entry_ts: datetime) -> pd.Series:
        day = entry_ts.normalize()
        path = self.minute_close[(self.frame["day"] == day) & (self.frame.index > entry_ts)]
        return path[path.index.time <= SQUARE_OFF]

    def walk(self, trade: Trade, style: str) -> None:
        """Fill this style's ladder along the index path, pricing each part."""
        path = self.session_after(trade.entry_ts)
        contract = Contract(expiry=trade.expiry, strike=trade.strike, option_type=trade.side)
        if path.empty:
            return
        if style == "close":
            stations = []
        else:
            span = trade.target_spot - trade.entry_spot
            stations = [(trade.entry_spot + span * frac, portion) for frac, portion in LADDERS[style]]

        taken = 0.0
        long_side = trade.side == "CE"
        for when, spot in path.items():
            while stations:
                level, portion = stations[0]
                reached = spot >= level if long_side else spot <= level
                if not reached:
                    break
                px = self.premium(when, contract)
                if px is None:
                    px = self.intrinsic(trade, spot)
                    trade.legs.append(Leg(when, float(spot), px, portion, f"{style}:target", priced=False))
                else:
                    trade.legs.append(Leg(when, float(spot), px, portion, f"{style}:target"))
                taken += portion
                stations.pop(0)
            if taken >= 0.999:
                return
        # Whatever is left leaves at the square-off, which is what the book does.
        when = path.index[-1]
        spot = float(path.iloc[-1])
        px = self.premium(when, contract)
        priced = px is not None
        if px is None:
            px = self.intrinsic(trade, spot)
        rest = round(1.0 - taken, 4)
        if rest > 0:
            trade.legs.append(Leg(when, spot, px, rest, "square_off", priced=priced))

    @staticmethod
    def intrinsic(trade: Trade, spot: float) -> float:
        """Off the edge of the archive an in-the-money option is worth at least
        its intrinsic value. A missing quote must never be booked as zero."""
        return max(0.0, (spot - trade.strike) if trade.side == "CE" else (trade.strike - spot))

    # -- the run ----------------------------------------------------------
    def shuffled(self, sig: pd.DataFrame) -> pd.DataFrame:
        """THE NULL TEST. Keep the side, the day and the distance to target, but
        move the entry to a random minute of the same session. If the result
        survives that, the confluence CANDLE is not what earned it -- being on
        that side on that day is, and the whole detector is decoration."""
        import random

        rng = random.Random(self.args.shuffle_seed)
        rows = []
        for _, s in sig.iterrows():
            day = pd.Timestamp(s["day"])
            session = self.minute_close[(self.frame["day"] == day)]
            session = session[session.index.time < SQUARE_OFF]
            if len(session) < 5:
                continue
            i = rng.randrange(len(session) - 1)
            when, spot = session.index[i], float(session.iloc[i])
            row = s.copy()
            row["entry_ts"] = when
            row["signal_ts"] = when
            row["entry"] = spot
            row["target"] = spot + (float(s["target"]) - float(s["entry"]))
            rows.append(row)
        return pd.DataFrame(rows)

    def run(self) -> dict:
        sig = signals(self.frame)
        if self.args.shuffle_seed:
            sig = self.shuffled(sig)
        out = {style: [] for style in EXIT_STYLES}
        for _, s in sig.iterrows():
            side = "CE" if s["side"] == "long" else "PE"
            if self.args.side != "both" and side != self.args.side:
                continue
            day = pd.Timestamp(s["day"])
            above = bool(self.above20.get(day, False))
            if (side == "CE") != above:
                self.skipped["against_trend"] += 1
                continue
            entry_ts = pd.Timestamp(s["entry_ts"])
            if entry_ts.time() >= SQUARE_OFF:
                self.skipped["too_late"] += 1
                continue
            expiry = self.expiry_for(entry_ts.date())
            if expiry is None:
                self.skipped["no_expiry"] += 1
                continue
            spot = float(s["entry"])
            strike = self.strike_for(spot, side)
            contract = Contract(expiry=expiry, strike=strike, option_type=side)
            prem = self.premium(entry_ts, contract)
            if prem is None or prem <= 0:
                self.skipped["no_entry_price"] += 1
                continue

            for style in EXIT_STYLES:
                trade = Trade(
                    side=side,
                    signal_ts=pd.Timestamp(s["signal_ts"]),
                    entry_ts=entry_ts,
                    entry_spot=spot,
                    target_spot=float(s["target"]),
                    target_name=str(s["target_name"]),
                    strike=strike,
                    expiry=expiry,
                    lots=self.args.lots,
                    lot=lot_size(expiry),
                    entry_premium=prem,
                )
                self.walk(trade, style)
                if trade.legs:
                    out[style].append(trade)
        return out


def summarise(trades: list, slippage_pct: float) -> dict:
    if not trades:
        return {"n": 0}
    nets = [t.net(slippage_pct) for t in trades]
    wins = [n for n in nets if n > 0]
    equity, peak, drawdown = 0.0, 0.0, 0.0
    for n in nets:
        equity += n
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    unpriced = sum(1 for t in trades for leg in t.legs if not leg.priced)
    return {
        "n": len(trades),
        "net": sum(nets),
        "avg": sum(nets) / len(nets),
        "win_rate": 100.0 * len(wins) / len(nets),
        "max_dd": drawdown,
        "legs_unpriced": unpriced,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2024-10-01")
    ap.add_argument("--to-date", default="2026-08-21")
    ap.add_argument("--lots", type=int, default=2)
    ap.add_argument("--strike-offset", type=int, default=0, help="strikes IN the money")
    ap.add_argument("--slippage-pct", type=float, default=0.30)
    ap.add_argument("--exit-search-minutes", type=int, default=15)
    ap.add_argument("--pricing", choices=("hybrid", "dhan", "upstox"), default="hybrid")
    ap.add_argument("--side", choices=("both", "CE", "PE"), default="both")
    ap.add_argument(
        "--shuffle-seed",
        type=int,
        default=0,
        help="null test: move every signal to a random minute of the same session",
    )
    args = ap.parse_args()

    bt = ConfluenceOptionsBacktest(args)
    books = bt.run()
    print(
        f"window {args.from_date} -> {args.to_date}   {args.lots} lots, "
        f"{args.strike_offset} strikes ITM, {args.slippage_pct}% slippage"
    )
    print(f"pricing[{args.pricing}]: {bt.source.report() if hasattr(bt.source, 'report') else 'single archive'}")
    print(f"skipped: {bt.skipped}\n")
    print(f"{'exit':<10}{'n':>6}{'net':>14}{'avg':>11}{'win%':>8}{'max dd':>13}{'unpriced legs':>15}")
    for style in EXIT_STYLES:
        s = summarise(books[style], args.slippage_pct)
        if not s["n"]:
            print(f"{style:<10}{'no trades':>6}")
            continue
        print(
            f"{style:<10}{s['n']:>6}{s['net']:>14,.0f}{s['avg']:>11,.0f}"
            f"{s['win_rate']:>8.0f}{s['max_dd']:>13,.0f}{s['legs_unpriced']:>15,}"
        )


if __name__ == "__main__":
    main()
