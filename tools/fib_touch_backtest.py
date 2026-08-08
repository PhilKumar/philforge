#!/usr/bin/env python3
"""Run the swing touch ladder over cached history, offline.

The console's Backtest button does this through Dhan and a live Upstox token.
This runs the SAME engine (`engine.fib_touch_ladder.FibTouchLadder`) against
the local caches instead, so a measurement can be taken without a network call
-- and, more importantly, without minting a Dhan token, which would kill the
one the live server is trading with.

Sources, both cache-only and both refusing rather than guessing:
  * index candles   tools/.nifty_cache/NIFTY_1m_*.json  (1m, naive IST)
  * option minutes  tools/.upstox_cache/                (expired contracts only)

Because Upstox records a contract's minutes only AFTER it expires, this can
only measure a mother whose contract has already expired. A recent mother has
to go through the console, which can reach Dhan for a still-listed strike.

    python3 tools/fib_touch_backtest.py --mother 2026-07-17T14:15 --timeframe 15m
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.cascade_upstox import UpstoxPremiumSource  # noqa: E402
from engine.fib_touch_ladder import (  # noqa: E402
    FibTouchConfig,
    FibTouchLadder,
    symbol_terms,
)

INDEX_CACHE = ROOT / "tools" / ".nifty_cache"


class Bar:
    __slots__ = ("timestamp", "open", "high", "low", "close")

    def __init__(self, timestamp, o, h, low, c):
        self.timestamp, self.open, self.high, self.low, self.close = timestamp, o, h, low, c


def load_index_1m(symbol: str) -> list[Bar]:
    """Every cached 1m bar for the symbol, oldest first, de-duplicated."""
    files = sorted(INDEX_CACHE.glob(f"{symbol}_1m_*.json"))
    if not files:
        raise SystemExit(f"No cached 1m {symbol} candles in {INDEX_CACHE}")
    seen: dict[datetime, Bar] = {}
    for path in files:
        for row in json.loads(path.read_text()):
            stamp = datetime.fromisoformat(row[0])
            if stamp.tzinfo is not None:
                stamp = stamp.replace(tzinfo=None)
            seen[stamp] = Bar(stamp, float(row[1]), float(row[2]), float(row[3]), float(row[4]))
    return [seen[k] for k in sorted(seen)]


def resample(bars: list[Bar], minutes: int) -> list[Bar]:
    """1m bars folded into NSE-aligned buckets of `minutes`.

    A session opens at 09:15, so buckets are measured from that offset -- not
    from the hour -- or a 15m bar would start at 09:00 and never match a mother
    the console would accept.
    """
    if minutes == 1:
        return bars
    out: list[Bar] = []
    current: Bar | None = None
    bucket_at: datetime | None = None
    for bar in bars:
        session_open = bar.timestamp.replace(hour=9, minute=15, second=0, microsecond=0)
        if bar.timestamp < session_open:
            continue
        offset = int((bar.timestamp - session_open).total_seconds() // 60)
        start = session_open + timedelta(minutes=(offset // minutes) * minutes)
        if bucket_at != start:
            if current is not None:
                out.append(current)
            current = Bar(start, bar.open, bar.high, bar.low, bar.close)
            bucket_at = start
        else:
            assert current is not None
            current.high = max(current.high, bar.high)
            current.low = min(current.low, bar.low)
            current.close = bar.close
    if current is not None:
        out.append(current)
    return out


def build_premium_lookup(underlying_key: str, symbol: str):
    """(when, strike, expiry, side) -> a real recorded trade, or None.

    Cache-only, and it never fabricates: `UpstoxPremiumSource.lookup` returns
    the exact minute's bar or nothing. A minute the option did not trade is
    searched forward up to ten minutes -- what an order resting at the level
    would actually get -- and then given up on as a gap.
    """
    source = UpstoxPremiumSource(underlying_key=underlying_key, cache_only=True)
    dead: set[tuple] = set()

    def lookup(when: datetime, strike: float, expiry: date, side: str):
        key = (float(strike), expiry, str(side).upper())
        if key in dead:
            return None
        contract = SimpleNamespace(
            symbol=symbol,
            underlying=symbol,
            strike=float(strike),
            expiry=expiry,
            option_type=str(side).upper(),
        )
        minute = when.replace(second=0, microsecond=0)
        for step in range(0, 11):
            try:
                bar = source.lookup(minute + timedelta(minutes=step), contract)
            except Exception:
                dead.add(key)
                return None
            if bar is not None:
                return float(bar.open)
        return None

    return lookup, source


def sweep(args) -> None:
    """Sequential, NON-OVERLAPPING campaigns across a date range.

    Every campaign starts at the first bar after the previous one ended, so the
    result is a real series rather than a pile of overlapping runs. That matters
    here: with the rebase rule, campaigns started at different times converge on
    the same setup, and counting each as a separate trade triple-counts the
    same rupees. The starting mother barely matters either -- a rebase walks it
    to wherever the market actually sets up.
    """
    terms = symbol_terms(args.symbol)
    tf_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}[args.timeframe]
    first, last = date.fromisoformat(args.from_day), date.fromisoformat(args.to_day)

    every = [b for b in load_index_1m(terms.symbol) if first <= b.timestamp.date() <= last]
    if not every:
        raise SystemExit(f"No cached 1m {terms.symbol} candles between {first} and {last}")
    lookup, source = build_premium_lookup(
        f"NSE_INDEX|Nifty {'Bank' if terms.symbol == 'BANKNIFTY' else '50'}", terms.symbol
    )
    expiries = sorted(source.available_expiries())

    rows: list[dict] = []
    index = 0
    while index < len(every) - 30:
        # Start on the next candle that opens a bar of the mother's chart.
        start = every[index]
        session_open = start.timestamp.replace(hour=9, minute=15, second=0, microsecond=0)
        offset = int((start.timestamp - session_open).total_seconds() // 60)
        if start.timestamp < session_open or offset % tf_minutes:
            index += 1
            continue

        config = FibTouchConfig(
            symbol=terms.symbol,
            side=args.side,
            mother_timestamp=start.timestamp,
            lot_size=65 if terms.symbol == "NIFTY" else terms.lot_size,
            strike_step=terms.strike_step,
            timeframe=args.timeframe,
            capital_cap_inr=args.cap,
            itm_steps=args.itm_steps,
            min_dte=args.min_dte,
            deep_target=not args.flat_target,
            trailing_stop=args.trail > 0,
            trail_span_multiple=args.trail or 1.0,
        )
        engine = FibTouchLadder(config, premium_lookup=lookup, expiry_source=lambda on: expiries)
        window = every[index : index + args.horizon_days * 400]
        if args.timeframe != "1m":
            for bar in resample(window, tf_minutes):
                engine.on_geometry_candle(bar)
        consumed = 0
        for bar in window:
            engine.on_candle(bar)
            consumed += 1
            if engine.status in {"CLOSED", "EXPIRED", "MOTHER_BROKEN"}:
                break
        st = engine.get_status()
        if st["fills"]:
            rows.append(
                {
                    "mother": start.timestamp,
                    "status": st["status"],
                    "exit": st["exit_reason"],
                    "fills": len(st["fills"]),
                    "net": st["net_pnl"],
                    "gaps": len(st["data_gaps"]),
                    "rebases": sum(1 for e in st["events"] if e["event"] == "mother_rebased"),
                }
            )
        # Next campaign begins after this one finished -- never overlapping.
        index += max(consumed, 1)

    priced = [r for r in rows if r["net"] is not None]
    unpriced = len(rows) - len(priced)
    wins = [r for r in priced if r["net"] > 0]
    losses = [r for r in priced if r["net"] <= 0]
    net = sum(r["net"] for r in priced)
    mode = "flat 0.25" if args.flat_target else "0.25/0.5 deep"
    mode += f" · trail {args.trail} span" if args.trail else " · no trail"
    print(f"\n=== {terms.symbol} {args.side} · {args.timeframe} mother · {mode} · {first} to {last} ===")
    print(f"campaigns that bought : {len(rows)}   priced {len(priced)}   unpriced {unpriced}")
    if priced:
        print(f"win / loss            : {len(wins)} / {len(losses)}  ({100 * len(wins) / len(priced):.0f}% green)")
        print(f"NET                   : Rs {net:,.2f}")
        print(
            f"best / worst          : Rs {max(r['net'] for r in priced):,.2f} / Rs {min(r['net'] for r in priced):,.2f}"
        )
        print(f"avg per trade         : Rs {net / len(priced):,.2f}")
        by_exit: dict = {}
        for r in priced:
            by_exit[r["exit"]] = by_exit.get(r["exit"], 0) + 1
        print(f"exits                 : {by_exit}")
        drop5 = sorted(r["net"] for r in priced)[:-5] if len(priced) > 5 else []
        if drop5:
            print(f"NET minus best 5      : Rs {sum(drop5):,.2f}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--mother", help="single run, IST, e.g. 2026-07-17T14:15")
    ap.add_argument("--side", default="CE", choices=["CE", "PE"])
    ap.add_argument("--timeframe", default="15m", choices=["1m", "5m", "15m", "1h"])
    ap.add_argument("--cap", type=float, default=75_000.0)
    ap.add_argument("--itm-steps", type=int, default=2)
    ap.add_argument("--min-dte", type=int, default=4)
    ap.add_argument("--horizon-days", type=int, default=10)
    ap.add_argument("--from", dest="from_day", help="sweep mode: first day, e.g. 2026-05-01")
    ap.add_argument("--to", dest="to_day", help="sweep mode: last day")
    ap.add_argument("--flat-target", action="store_true", help="keep 0.25 at every depth")
    ap.add_argument("--trail", type=float, default=0.0, help="trailing exit, in fib spans (0 = off)")
    args = ap.parse_args()

    if args.from_day:
        sweep(args)
        return
    if not args.mother:
        raise SystemExit("Give either --mother for one run, or --from/--to to sweep.")

    terms = symbol_terms(args.symbol)
    mother_at = datetime.fromisoformat(args.mother)
    tf_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}[args.timeframe]

    every = load_index_1m(terms.symbol)
    horizon = mother_at + timedelta(days=args.horizon_days)
    window = [b for b in every if mother_at <= b.timestamp <= horizon]
    if not window:
        raise SystemExit(f"No cached 1m candles between {mother_at} and {horizon}")
    geometry = resample(window, tf_minutes)
    if not any(b.timestamp == mother_at for b in geometry):
        near = [b.timestamp for b in geometry[:6]]
        raise SystemExit(f"No {args.timeframe} candle opens at {mother_at}. Nearby: {near}")

    lookup, source = build_premium_lookup(
        f"NSE_INDEX|Nifty {'Bank' if terms.symbol == 'BANKNIFTY' else '50'}", terms.symbol
    )
    expiries = sorted(source.available_expiries())
    if not expiries:
        raise SystemExit("The Upstox cache holds no expiries; nothing can be priced offline.")

    config = FibTouchConfig(
        symbol=terms.symbol,
        side=args.side,
        mother_timestamp=mother_at,
        lot_size=65 if terms.symbol == "NIFTY" else terms.lot_size,
        strike_step=terms.strike_step,
        timeframe=args.timeframe,
        capital_cap_inr=args.cap,
        itm_steps=args.itm_steps,
        min_dte=args.min_dte,
    )
    engine = FibTouchLadder(config, premium_lookup=lookup, expiry_source=lambda on: expiries)
    if args.timeframe != "1m":
        for bar in geometry:
            engine.on_geometry_candle(bar)
    for bar in window:
        engine.on_candle(bar)
        if engine.status in {"CLOSED", "EXPIRED", "MOTHER_BROKEN"}:
            break

    status = engine.get_status()
    print(json.dumps(status, indent=2, default=str))


if __name__ == "__main__":
    main()
