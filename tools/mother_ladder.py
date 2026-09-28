"""tools/mother_ladder.py -- his ladder, as Phil trades it, on spot crypto.

Phil, 28-Sep-2026, on the first backtests: "you might be wrong somewhere" --
and he was right. They priced one rule at a time with a stop and a 30-minute
exit, which is not how the method is traded. His answers fix the four things
those tests invented:

  * Measured from      THE MOTHER CANDLE.
  * Sized by           THE FUND FORMULA -- capital / 100 per 1% of distance (§3.1).
  * Exit               0.25 FIRST -- of the way from the average back to the mother.
  * Timeframes         ALL, 5m to 1W: "we see the move in candles and the price;
                       it moves and makes the TFs." Each runs with its own fund (T5).

THE RULES, each from the playbook:

  MOTHER. A candle that closes at a new high of the last `lookback` candles --
      the starting point the chart is reset to (Part 4). Known at its own
      close: tools/ladder_backtest.py chose mothers with a CENTRED rolling
      window, which reads candles that have not happened yet. Here nothing is
      decided on a candle that is still to come.
  WHERE TO BUY. Only BELOW the mother's low -- the visible low (T4) -- at the
      boundaries 0, 1, 2, 4, 8 mother-ranges under it (T18), and only when a
      candle actually trades there (§3.3).
  HOW MUCH. The fund sheet: at a level L the money deployed so far should be
      capital x (mother_high - L) / mother_high -- "capital divided by 100 ...
      how much you should spend for one percentage" (§3.1). Each fill tops the
      book up to that, never past the trading ceiling (T6, 50%).
  NO STOP (T11). The book waits.
  EXIT ON THE BOOK (T7, T10). First exit at 0.25 of the way from the AVERAGE
      price back to the mother high. `--split` sells half there and the rest at
      0.5; the default sells the whole book at 0.25.

Spot, no leverage: a coin that keeps falling cannot liquidate the account, it
can only hold its money. So the headline is not only the win rate -- it is how
long money sat in a book, how deep the book went against the average, and how
many books were still open when the data ended.

One campaign at a time per timeframe. A candle that bought may not also sell
(its high and low order are unknown). Fees: Binance spot, per fill.

    python3 tools/mother_ladder.py --data DIR [--lookback 20] [--split]
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BOUNDARIES = (0.0, 1.0, 2.0, 4.0, 8.0)  # T18, mother ranges under the visible low
TRADING_CEILING = 0.50  # T6
FEE = 0.001  # Binance spot, 0.1% of each fill


# ── data ──────────────────────────────────────────────────────────────────────


def load_btc(data_dir: str) -> dict[str, pd.DataFrame]:
    """5m from BTCUSDT_5m.pkl, resampled up to 4h; daily and weekly from
    BTCUSDT_1d.json (Binance kline rows)."""
    frames: dict[str, pd.DataFrame] = {}
    pkl = os.path.join(data_dir, "BTCUSDT_5m.pkl")
    if os.path.exists(pkl):
        m5 = pd.read_pickle(pkl)[["open", "high", "low", "close"]].astype(float)
        m5.index = m5.index.tz_convert("Asia/Kolkata").tz_localize(None)
        frames["5m"] = m5
        for tf, rule in (("15m", "15min"), ("1h", "1h"), ("4h", "4h")):
            frames[tf] = _resample(m5, rule)
    daily = os.path.join(data_dir, "BTCUSDT_1d.json")
    if os.path.exists(daily):
        raw = json.load(open(daily))
        rows = raw if isinstance(raw, list) else next(v for v in raw.values() if isinstance(v, list))
        d = pd.DataFrame([r[:5] for r in rows], columns=["t", "open", "high", "low", "close"])
        unit = "ms" if float(d["t"].iloc[0]) > 1e11 else "s"
        d["t"] = pd.to_datetime(d["t"].astype(float), unit=unit)
        d = d.set_index("t").astype(float).sort_index()
        frames["1d"] = d
        frames["1w"] = _resample(d, "W")
    return frames


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


# ── the ladder ────────────────────────────────────────────────────────────────


@dataclass
class Book:
    opened: pd.Timestamp
    mother_high: float
    levels: list
    capital: float
    qty: float = 0.0
    cost: float = 0.0
    fees: float = 0.0
    fills: list = field(default_factory=list)
    pnl: float = 0.0
    closed: pd.Timestamp | None = None
    first_fill: pd.Timestamp | None = None
    worst: float = 0.0  # deepest mark-to-market loss, as a fraction of the money in the book
    peak_deployed: float = 0.0
    took_first: bool = False

    @property
    def average(self) -> float:
        return self.cost / self.qty if self.qty else 0.0

    def target(self, frac: float) -> float:
        return self.average + (self.mother_high - self.average) * frac

    def buy(self, when, price: float) -> None:
        want = self.capital * (self.mother_high - price) / self.mother_high  # the fund sheet
        want = min(want, self.capital * TRADING_CEILING)
        spend = want - self.cost
        if spend <= 0:
            return
        self.qty += spend / price
        self.cost += spend
        self.fees += spend * FEE
        self.fills.append((when, price, spend))
        self.peak_deployed = max(self.peak_deployed, self.cost)
        self.first_fill = self.first_fill or when

    def sell(self, when, price: float, portion: float) -> None:
        q = self.qty * portion
        out_cost = self.average * q
        self.pnl += q * price - out_cost - q * price * FEE
        self.qty -= q
        self.cost -= out_cost
        if self.qty <= 1e-12:
            self.qty, self.cost, self.closed = 0.0, 0.0, when


def run(df: pd.DataFrame, lookback: int, capital: float, split: bool) -> list[Book]:
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    t = df.index
    books: list[Book] = []
    live: Book | None = None
    for i in range(lookback, len(df)):
        if live is not None:
            bought = False
            for k, lvl in enumerate(live.levels):
                if lvl is not None and lo[i] <= lvl:
                    live.buy(t[i], min(lvl, o[i]))
                    live.levels[k] = None
                    bought = True
                    break  # one level a candle
            if live.qty:
                live.worst = min(live.worst, (lo[i] - live.average) / live.average)
            if live.qty and not bought:
                first = live.target(0.25)
                if not live.took_first and h[i] >= first:
                    live.sell(t[i], first, 0.5 if split else 1.0)
                    live.took_first = True
                if live.qty and split and h[i] >= live.target(0.5):
                    live.sell(t[i], live.target(0.5), 1.0)
            if live.closed is not None:
                live = None
            elif not live.fills and h[i] > live.mother_high:
                # Price went over the mother before it ever came under the
                # visible low: nothing was bought, and THIS candle is the new
                # starting point -- "the mother candle resets" (Day22).
                live = None
            if live is not None:
                continue
        # a new mother: this candle makes the highest high of the last `lookback`
        if h[i] >= h[i - lookback : i].max():
            unit = h[i] - lo[i]
            if unit <= 0:
                continue
            levels = [lo[i] - b * unit for b in BOUNDARIES]
            live = Book(t[i], h[i], levels, capital)
            books.append(live)
    return [b for b in books if b.fills]


# ── the FINAL system: 30/70 after buyer involvement (Day31, Day33-35) ─────────
#
# `run` above is the boundary ladder (0/1/2/4/8) the class was taught first.
# The playbook records that it was superseded:
#   T66  two orders, 30/70: 30% "at how far the SELLER brought it" (the low of the
#        fall), 70% "as far as the buyer took it, TWICE that distance below".
#   T68  never approach a market merely falling -- only after BUYER INVOLVEMENT,
#        a bounce off the seller's low.
#   T63  SIZE = (capital / 100) x distance in percent, the distance being the
#        structure's: mother high down to the deeper order.
#   Day34 "two bought, average them, and 0.25 to the mother candle overall."
#   Day35 "if you could not place the 30% order, there is no talk of the target."
# `scale` multiplies T63's base: Phil's own live tickets ran several times the
# plain formula (e.g. $20 at a 1% dip on a $200 account), so both are shown.


def run_3070(df: pd.DataFrame, lookback: int, capital: float, bounce: float = 0.25, scale: float = 1.0) -> list[Book]:
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    t = df.index
    books: list[Book] = []
    i = lookback
    n = len(df)
    while i < n:
        if h[i] < h[i - lookback : i].max():
            i += 1
            continue
        mother, j = h[i], i + 1
        low, low_j, top = np.inf, None, -np.inf
        book = None
        while j < n:
            if book is None:
                if h[j] > mother:
                    break  # a new mother before any order filled: reset (Day22)
                if lo[j] < low:
                    low, low_j, top = lo[j], j, -np.inf  # the seller is still bringing it down
                    j += 1
                    continue
                top = max(top, h[j])
                fall = mother - low
                if fall <= 0 or top - low < bounce * fall:
                    j += 1
                    continue  # no buyer involvement yet (T68)
                d = top - low  # how far the buyer took it
                first, second = low, low - 2 * d
                money = capital / 100 * (mother - second) / mother * 100 * scale  # T63
                money = min(money, capital * TRADING_CEILING)
                book = Book(t[i], mother, [first, second], capital)
                book.plan = [(first, 0.3 * money), (second, 0.7 * money)]
            # orders fill as limits, one a candle; the 30% must fill first (Day35)
            bought = False
            for k, (lvl, amt) in enumerate(book.plan):
                if amt and lo[j] <= lvl:
                    px = min(lvl, o[j])
                    book.qty += amt / px
                    book.cost += amt
                    book.fees += amt * FEE
                    book.fills.append((t[j], px, amt))
                    book.peak_deployed = max(book.peak_deployed, book.cost)
                    book.first_fill = book.first_fill or t[j]
                    book.plan[k] = (lvl, 0.0)
                    bought = True
                    break
            if not book.fills and h[j] > mother:
                book = None
                break
            if book.qty:
                book.worst = min(book.worst, (lo[j] - book.average) / book.average)
                if not bought and h[j] >= book.target(0.25):
                    book.sell(t[j], book.target(0.25), 1.0)
                    books.append(book)
                    break
            j += 1
        else:
            if book is not None and book.fills:
                books.append(book)  # still open when the data ended
        i = max(j, i + 1)
    return books


# ── report ────────────────────────────────────────────────────────────────────


def summarise(books: list[Book], capital: float, span_days: float) -> dict:
    if not books:
        return {"n": 0}
    closed = [b for b in books if b.closed is not None]
    open_ = [b for b in books if b.closed is None]
    wins = [b for b in closed if b.pnl > 0]
    held = [(b.closed - b.first_fill).total_seconds() / 3600 for b in closed]
    total = sum(b.pnl for b in closed)
    return {
        "n": len(books),
        "closed": len(closed),
        "open": len(open_),
        "win": len(wins) / len(closed) if closed else 0.0,
        "pnl_pct": 100 * total / capital,
        "per_year": 100 * total / capital * 365 / span_days if span_days else 0.0,
        "avg_roi": 100 * float(np.mean([b.pnl / b.peak_deployed for b in closed])) if closed else 0.0,
        "hold_med_h": float(np.median(held)) if held else 0.0,
        "hold_max_h": float(max(held)) if held else 0.0,
        "worst": 100 * min(b.worst for b in books),
        "max_deployed": 100 * max(b.peak_deployed for b in books) / capital,
        "open_loss_pct": 100 * sum(b.worst * b.cost for b in open_) / capital,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--capital", type=float, default=200.0)
    ap.add_argument("--split", action="store_true", help="half at 0.25, rest at 0.5")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    frames = load_btc(args.data)
    L = ["# His ladder on BTC spot — mother candle, fund formula, exit at 0.25", ""]
    L.append(
        f"Capital ${args.capital:.0f}. Mother = new {args.lookback}-candle high, known at its close. "
        f"Fees {FEE * 100:.1f}% per fill. Exit: {'half at 0.25, rest at 0.5' if args.split else 'whole book at 0.25'}."
    )
    L.append("")
    L.append(
        "| TF | data | books | closed | still open | win rate | profit on capital | per year | "
        "avg profit on money used | median hold | longest hold | worst dip vs average | most capital in use |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for tf in ("5m", "15m", "1h", "4h", "1d", "1w"):
        df = frames.get(tf)
        if df is None or len(df) <= args.lookback:
            continue
        span = (df.index[-1] - df.index[0]).total_seconds() / 86400
        s = summarise(run(df, args.lookback, args.capital, args.split), args.capital, span)
        if not s["n"]:
            L.append(f"| {tf} | {df.index[0].date()}–{df.index[-1].date()} | 0 | | | | | | | | | | |")
            continue
        L.append(
            f"| {tf} | {df.index[0].date()}–{df.index[-1].date()} | {s['n']} | {s['closed']} | {s['open']} "
            f"| {s['win'] * 100:.0f}% | {s['pnl_pct']:+.2f}% | {s['per_year']:+.1f}% | {s['avg_roi']:+.2f}% "
            f"| {s['hold_med_h']:.0f} h | {s['hold_max_h']:.0f} h | {s['worst']:.1f}% | {s['max_deployed']:.1f}% |"
        )
    text = "\n".join(L) + "\n"
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
