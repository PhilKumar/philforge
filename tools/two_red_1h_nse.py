"""tools/two_red_1h_nse.py -- the two-red entry on 1-HOUR candles, offline.

Same engine as tools/equity_recovery_sweep.py (`engine.candle_recovery`), same
rules; only the candle source changes. That runner fetches from Upstox and
knows six symbols, so it cannot answer "how do the BEES do" without a token and
a wider universe. This one reads the 1H caches already sitting in CryptoForge's
`tools/.history_cache/<SYMBOL>1H_5m.json`, so every instrument tested this
session can be run at once, repeatedly, with no network.

The entry is Phil's, 2026-08-11: **two reds where the second closes below the
first's low, and the position is taken at that candle's close** (filled at the
next bar's open, which is the first price the order can actually meet) --
`entry_source="close"`, against the engine's original `"high"`, where price had
to rise back through the second red's high before anything was bought.

Everything else is the shipped rule set: SL at the entry candle's low, never
trailed; a close below it stops; the next trade may only arm below the standing
low; the target is booked losses + a margin, not a price; 1 lot then 2;
intraday, so the day's last bar squares off.

    python3 tools/two_red_1h_nse.py --entry close
    python3 tools/two_red_1h_nse.py --entry high --symbols SILVERBEES ADANIENT
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.candle_recovery import RecoveryBar  # noqa: E402
from tools import equity_recovery_sweep as esw  # noqa: E402

CACHE = "/Users/philipkumar/Documents/CryptoForge/tools/.history_cache"
BEES = ["SILVERBEES", "NIFTYBEES", "GOLDBEES", "BANKBEES", "JUNIORBEES"]
STOCKS = ["ETERNAL", "ADANIENT", "RELIANCE", "ICICIBANK", "INFY", "HDFCBANK", "TMPV"]


def load_local(symbol: str, timeframe: str, years: float):
    """(shared, by_trade, by_signal, signal_symbol), the shape run_symbol wants.

    The instrument is its own signal here: these are the stocks and ETFs
    themselves, not a BEES taking geometry from its index.
    """
    path = os.path.join(CACHE, f"{symbol}1H_5m.json")
    if not os.path.exists(path):
        return [], {}, {}, symbol
    bars = {}
    for stamp, o, h, low, c in json.load(open(path)):
        when = datetime.fromtimestamp(stamp)
        bars[when] = RecoveryBar(when, float(o), float(h), float(low), float(c))
    shared = sorted(bars)
    return shared, bars, bars, symbol


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry", choices=["close", "high"], default="close")
    ap.add_argument("--sl", choices=["entry", "previous", "ultimate"], default="entry")
    ap.add_argument("--symbols", nargs="+", default=BEES + STOCKS)
    ap.add_argument("--trade-inr", type=float, default=50_000.0)
    ap.add_argument("--min-profit", type=float, default=500.0)
    ap.add_argument("--run", type=int, default=5, help="bars in the run that marks a mother")
    ap.add_argument("--horizon-sessions", type=int, default=10)
    args = ap.parse_args()

    esw.load = load_local  # the one thing that differs from the shipped runner
    config_kwargs = dict(
        lots_schedule=(1, 2),
        min_profit_inr=args.min_profit,
        sl_source=args.sl,
        entry_source=args.entry,
        horizon_sessions=args.horizon_sessions,
    )
    entry_text = "at the second red's CLOSE" if args.entry == "close" else "at the second red's HIGH"
    print(f"\ntwo-red entry {entry_text} · 1H candles · SL {args.sl} · Rs {args.trade_inr:,.0f} per lot\n")
    head = (
        f"  {'symbol':<12}{'mothers':>9}{'campaigns':>11}{'trades':>8}{'green':>7}{'NET':>13}{'worst':>11}{'shares':>8}"
    )
    print(head)
    print("  " + "-" * (len(head) - 2))

    grand_net, grand_trades = 0.0, 0
    for symbol in args.symbols:
        got = esw.run_symbol(
            symbol,
            "1h",
            0.0,
            mode="ladder",
            side="CE",
            mother_rule="run",
            run_bars=args.run,
            trade_inr=args.trade_inr,
            config_kwargs=config_kwargs,
        )
        if not got:
            print(f"  {symbol:<12} no data")
            continue
        engines, lot_size, _typical, mothers = got
        nets, trades = [], 0
        for engine in engines:
            priced = [t for t in engine.trades if t.entry_time is not None]
            if not priced or not engine.fully_priced:
                continue
            trades += len(priced)
            nets.append(engine.booked_net)
        if not nets:
            print(f"  {symbol:<12}{mothers:>9}{0:>11}")
            continue
        green = 100.0 * sum(1 for n in nets if n > 0) / len(nets)
        print(
            f"  {symbol:<12}{mothers:>9}{len(nets):>11}{trades:>8}{green:>6.0f}%"
            f"{sum(nets):>13,.0f}{min(nets):>11,.0f}{lot_size:>8}"
        )
        grand_net += sum(nets)
        grand_trades += trades
    print(f"\n  TOTAL {grand_trades} trades, net Rs {grand_net:,.0f}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
