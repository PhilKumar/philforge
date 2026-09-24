"""The PE book's proposed S5 breakdown leg, priced over five years.

The rule, as Phil gave it (24-Sep-2026):

    When NIFTY closes a 5-minute candle below the previous day's S5 pivot, buy
    a PE. Hold it to the 15:10 square-off. No level exits, no target, no stop.

This is an ADDITION to the existing put book, not a replacement, so it is
modelled standalone: its trades neither open nor close the book's own trade,
and the two P&Ls add.

Three honesty rules, carried over from tools/cpr_options_backtest.py:

  * A contract is priced only from the store that holds it, at the minute asked
    for. A miss is COUNTED, never quietly filled from a neighbouring minute.
  * Dhan carries about twelve strikes either side of the money. A put held while
    NIFTY falls hard walks off the edge of the archive — and that is the winning
    trade, so losing it would flatter nothing and cost everything. Those exits
    are floored at intrinsic value, which an in-the-money put is worth at
    minimum, and are reported separately.
  * Costs are the deployed engine's: the same spread and slippage the live PE
    book is replayed with, plus real statutory charges for the trade's date.

The S5 close happens about 8.5 times a year, so this is 48 trades in 5.6 years —
an observation, not a verdict. VERDICT as run 24-Sep-2026: net -Rs 1,03,246,
44% win, max drawdown -Rs 2,48,066. Not adopted.

Two traps found building it, both worth keeping:
  * options.dhan_listed prices at the minute's OPEN, not its close. A hand-check
    against the close looks like a mismatch and is not one.
  * tools/.nifty_cache is CONTAMINATED — 16 weekend days and 50 days with an
    impossible intraday range over 2021-2026. Its daily LOW, which feeds S4/S5
    directly, disagrees with the Dhan archive on 103 of 1,389 weekdays. Use
    tools.nifty_index_from_dhan.load_minutes() for anything pivot-shaped.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.indicators import cpr  # noqa: E402
from options.charges import round_trip_charges  # noqa: E402
from options.dhan_listed import DhanListedSource  # noqa: E402
from tools.nifty_expiry_calendar import STRIKE_STEP, lot_size, weekly_expiries  # noqa: E402
from tools.nifty_index_from_dhan import load_minutes, sessions, to_bars  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORES = {
    "e1": os.path.join(REPO, "data", "dhan_options"),
    "e2": os.path.join(REPO, "data", "dhan_options_e2"),
    "m1": os.path.join(REPO, "data", "dhan_options_m1"),
    "m2": os.path.join(REPO, "data", "dhan_options_m2"),
}

# The deployed PE book's execution costs (tools/pe_entry_minute_sweep.py).
SPREAD_BPS = 12.0
ENTRY_SLIP_BPS = 6.0
EXIT_SLIP_BPS = 8.0
# The book buys the strike trading nearest this premium.
TARGET_PREMIUM = 250.0
SQUARE_OFF = time(15, 10)


@dataclass(frozen=True)
class Contract:
    expiry: date
    strike: int
    option_type: str


def buy_fill(px: float) -> float:
    return px * (1.0 + (SPREAD_BPS / 2.0 + ENTRY_SLIP_BPS) / 10_000.0)


def sell_fill(px: float) -> float:
    return px * (1.0 - (SPREAD_BPS / 2.0 + EXIT_SLIP_BPS) / 10_000.0)


class Run:
    def __init__(self, args):
        self.args = args
        self.minute = load_minutes()
        self.minute = self.minute.loc[args.from_date : args.to_date + " 23:59"]
        self.days = sessions(self.minute)
        self.weeklies = weekly_expiries(self.days)
        self.source = DhanListedSource(self.weeklies, STORES, "NIFTY", nearest_within=0)
        self.intrinsic_exits = 0
        self.skipped_no_entry_price = 0

    # -- the signal --------------------------------------------------------
    def triggers(self) -> list:
        """First 5-minute close below the previous day's S5, per day."""
        bars = to_bars(self.minute, "5min").between_time("09:15", "15:29")
        piv = cpr(bars)
        piv["day"] = piv.index.normalize()
        out = []
        for day, g in piv.groupby("day"):
            g = g.dropna(subset=["S5"])
            if g.empty:
                continue
            hit = g[(g["close"] < g["S5"]) & (g.index.time < SQUARE_OFF)]
            if hit.empty:
                continue
            out.append((hit.index[0], float(hit["close"].iloc[0]), float(hit["S5"].iloc[0])))
        return out

    # -- pricing -----------------------------------------------------------
    def expiry_for(self, day: date):
        """The current weekly expiry, which is what the PE book trades."""
        for e in self.weeklies:
            if e >= day:
                return e
        return None

    def pick_strike(self, when: datetime, expiry: date, spot: float):
        """The listed put trading nearest TARGET_PREMIUM at the entry minute."""
        atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
        best = None
        for step in range(-14, 15):
            strike = atm + step * int(STRIKE_STEP)
            px = self.source.lookup(when, Contract(expiry, strike, "PE"))
            if px is None or px <= 0:
                continue
            gap = abs(px - TARGET_PREMIUM)
            if best is None or gap < best[0]:
                best = (gap, strike, px)
        return (best[1], best[2]) if best else (None, None)

    def exit_price(self, when: datetime, contract: Contract, spot: float):
        px = self.source.lookup(when, contract)
        if px is not None and px > 0:
            return px, False
        # Off the edge of the archive. An in-the-money put is worth at least
        # its intrinsic value; floor it there and say so.
        intrinsic = max(0.0, contract.strike - spot)
        self.intrinsic_exits += 1
        return intrinsic, True

    # -- the run -----------------------------------------------------------
    def go(self) -> pd.DataFrame:
        rows = []
        by_minute = self.minute
        for ts, close, s5 in self.triggers():
            day = ts.date()
            expiry = self.expiry_for(day)
            if expiry is None:
                continue
            # The live book signals on the 5-minute close and fills at the NEXT
            # minute's open, so that is the entry minute here too.
            entry_min = ts + pd.Timedelta(minutes=5)
            if entry_min not in by_minute.index:
                nxt = by_minute.loc[by_minute.index > ts]
                if nxt.empty:
                    continue
                entry_min = nxt.index[0]
            spot_in = float(by_minute.loc[entry_min, "open"])
            strike, raw_in = self.pick_strike(entry_min, expiry, spot_in)
            if strike is None:
                self.skipped_no_entry_price += 1
                continue

            sq = by_minute.loc[(by_minute.index.date == day) & (by_minute.index.time <= SQUARE_OFF)]
            if sq.empty:
                continue
            exit_min = sq.index[-1]
            spot_out = float(sq["close"].iloc[-1])
            contract = Contract(expiry, strike, "PE")
            raw_out, floored = self.exit_price(exit_min, contract, spot_out)

            qty = 4 * lot_size(day)
            entry = buy_fill(raw_in)
            exit_ = sell_fill(raw_out)
            charges = round_trip_charges(trade_date=day, buy_premium=entry, sell_premium=exit_, quantity=qty)
            gross = (exit_ - entry) * qty
            rows.append(
                {
                    "date": day,
                    "trigger": ts,
                    "entry_time": entry_min,
                    "exit_time": exit_min,
                    "s5": round(s5, 2),
                    "spot_in": round(spot_in, 2),
                    "spot_out": round(spot_out, 2),
                    "index_move": round(spot_out - spot_in, 2),
                    "strike": strike,
                    "expiry": expiry,
                    "premium_in": round(entry, 2),
                    "premium_out": round(exit_, 2),
                    "qty": qty,
                    "gross": round(gross, 2),
                    "charges": round(float(charges.total), 2),
                    "pnl": round(gross - float(charges.total), 2),
                    "exit_floored": floored,
                }
            )
        return pd.DataFrame(rows)


def report(trades: pd.DataFrame, run: Run) -> None:
    if trades.empty:
        print("no trades")
        return
    net = trades["pnl"].sum()
    wins = trades[trades["pnl"] > 0]
    peak = cum = dd = 0.0
    for p in trades.sort_values("entry_time")["pnl"]:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    print(f"\n{'trades':>8}{'win%':>8}{'net':>14}{'avg':>11}{'maxDD':>13}")
    print(
        f"{len(trades):>8}{100 * len(wins) / len(trades):>7.0f}%{net:>14,.0f}{net / len(trades):>11,.0f}{-dd:>13,.0f}"
    )

    trades = trades.copy()
    trades["yr"] = pd.to_datetime(trades["date"]).dt.year
    print(f"\n{'year':<8}{'trades':>8}{'win%':>8}{'net':>14}")
    for yr, g in trades.groupby("yr"):
        print(f"{yr:<8}{len(g):>8}{100 * (g.pnl > 0).mean():>7.0f}%{g.pnl.sum():>14,.0f}")

    floored = int(trades["exit_floored"].sum())
    print(
        f"\npricing: {run.source.served:,} lookups served"
        f" ({run.source.served_exact:,} at the exact minute)"
        f"; misses {run.source.misses}"
    )
    print(f"exits floored at intrinsic (off the archive's strike band): {floored} of {len(trades)}")
    if floored:
        fl = trades[trades["exit_floored"]]
        print(f"  those {floored} contribute Rs {fl.pnl.sum():,.0f} of the Rs {trades.pnl.sum():,.0f} net")
    print(f"days skipped for no entry price: {run.skipped_no_entry_price}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2021-01-01")
    ap.add_argument("--to-date", default="2026-08-31")
    ap.add_argument("--out", default="", help="write the per-trade CSV here")
    args = ap.parse_args()

    run = Run(args)
    print(f"[data] {len(run.minute):,} index minutes, {len(run.days):,} sessions")
    trades = run.go()
    report(trades, run)
    if args.out and not trades.empty:
        trades.to_csv(args.out, index=False)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
