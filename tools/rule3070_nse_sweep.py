"""tools/rule3070_nse_sweep.py -- the 30-70 Rule across NSE stocks and gates.

One row per (stock, rupee gate) so the flat depository charge can be seen
doing its damage: without a rupee gate the engine buys one share at a time and
the Rs 15.93 that leaves the demat on the selling day is bigger than the win.

    python3 tools/rule3070_nse_sweep.py --symbols ICICIBANK HDFCBANK RELIANCE INFY \
        --min-win 0 100 250 500

Every worker sets the engine's module globals ITSELF: a pool worker inherits
the parent's module state at fork time, so setting them in the parent and
trusting the children is how a sweep silently reports the same config four
times (learned the hard way on the cascade ladder-cap sweep).
"""

from __future__ import annotations

import argparse
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.rule3070_nse import DP_PER_SELL_DAY, FEE_BUY, FEE_SELL, load_candles, rs  # noqa: E402


def one_run(job: dict) -> dict:
    import tools.rule3070_engine as engine
    from tools.rule3070_engine import run_ladder

    engine.CAPITAL = job["capital"]
    engine.LOT_SIZE = 1
    engine.GAP_FILLS = True
    engine.MAX_BANDS = job["max_bands"]
    engine.MIN_NET_MARGIN = job["min_margin"] / 100.0
    engine.MIN_WIN_RS = job["min_win"]
    engine.TARGET_AT_FILL_ONLY = False
    engine.ENFORCE_BUDGET = True
    engine.COMPOUND_AT_HALF = job["compound"]
    engine.COMPOUND_SCHEDULE = tuple(float(x) / 100.0 for x in str(job["compound_at"]).split(",") if x.strip())
    engine.COMPOUND_PUMP = job["pump"]
    engine._PUMPED_TOTAL = 0.0
    engine.FEE_PER_SIDE = (FEE_BUY + FEE_SELL) / 2
    engine.MAX_ACTIVE_MINORS = 1 if job["one_line"] else 0

    df = load_candles(job["symbol"], job["months"])
    campaigns = run_ladder(df, minors=True)
    last_close = df["close"].iloc[-1]
    span_days = (df.index[-1] - df.index[0]).days

    closed, bag, bag_cost, sell_days, nets, peak_events = [], 0.0, 0.0, {}, [], []
    for c in campaigns:
        if not c.fills:
            continue
        cost = sum(f.amount for f in c.fills)
        qty = sum(f.amount / f.price for f in c.fills)
        for f in c.fills:
            peak_events.append((f.ts, f.amount))
        peak_events.append((c.end_ts, -cost))
        if c.status == "TARGET HIT":
            proceeds = qty * (c.exit_price or c.target)
            net = proceeds - cost - FEE_BUY * cost - FEE_SELL * proceeds
            closed.append(net)
            sell_days.setdefault(c.target_ts.date(), []).append(len(closed) - 1)
        else:
            bag += qty * last_close - cost
            bag_cost += cost
    for _day, idxs in sell_days.items():
        for i in idxs:
            closed[i] -= job["dp"] / len(idxs)
    nets = closed

    # Peak says how much the account MUST hold; the time-weighted average says
    # how much of it was ever actually working. A strategy that earns 4% on
    # the account while deploying a fifth of it is a different animal from one
    # that earns 4% fully invested.
    peak_events.sort(key=lambda e: (e[0], -e[1]))
    cur = peak = 0.0
    area = 0.0
    prev_ts = peak_events[0][0] if peak_events else None
    for ts, delta in peak_events:
        area += cur * (ts - prev_ts).total_seconds()
        prev_ts = ts
        cur += delta
        peak = max(peak, cur)
    seconds = (peak_events[-1][0] - peak_events[0][0]).total_seconds() if peak_events else 0
    avg_deployed = area / seconds if seconds else 0.0

    total = sum(nets)
    yr = (span_days / 365.25) or 1
    # Pumped money is a DEPOSIT, not profit: the return has to be measured
    # against every rupee put in, or a big enough pump makes any rule look good.
    invested = job["capital"] + engine._PUMPED_TOTAL
    return {
        "purse": engine.CAPITAL,
        "pumped": engine._PUMPED_TOTAL,
        "pct_invested": (total + bag) / yr / invested * 100,
        "symbol": job["symbol"],
        "min_win": job["min_win"],
        "trades": len(nets),
        "losers": sum(1 for n in nets if n < 0),
        "closed": total,
        "dp": job["dp"] * len(sell_days),
        "bag": bag,
        "bag_cost": bag_cost,
        "net": total + bag,
        "pct_yr": (total + bag) / yr / job["capital"] * 100,
        "peak": peak,
        "avg_deployed": avg_deployed,
    }


def pumped_note(r: dict) -> str:
    """Fresh money is a deposit, not a win: say how much went in and what the
    return looks like measured against every rupee of it."""
    if not r["pumped"]:
        return ""
    return f"  pumped {rs(r['pumped'])} -> {r['pct_invested']:.1f}%/yr on it all"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["ICICIBANK", "HDFCBANK", "RELIANCE", "INFY"])
    ap.add_argument("--min-win", nargs="+", type=float, default=[0.0, 100.0, 250.0, 500.0])
    ap.add_argument("--capital", type=float, default=200000.0)
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--min-margin", type=float, default=0.35)
    ap.add_argument("--max-bands", type=int, default=2)
    ap.add_argument("--dp", type=float, default=DP_PER_SELL_DAY)
    ap.add_argument("--no-compound", action="store_true")
    ap.add_argument("--compound-at", default="25", help="fold schedule as %% of capital, comma-separated")
    ap.add_argument("--pump", action="store_true", help="deposit 100%% of the purse each time profit grows it 50%%")
    ap.add_argument("--one-line", action="store_true", help="one working line at a time")
    args = ap.parse_args()

    jobs = [
        {
            "symbol": symbol,
            "min_win": win,
            "capital": args.capital,
            "months": args.months,
            "min_margin": args.min_margin,
            "max_bands": args.max_bands,
            "dp": args.dp,
            "compound_at": args.compound_at,
            "pump": args.pump,
            "one_line": args.one_line,
            "compound": not args.no_compound,
        }
        for symbol in args.symbols
        for win in args.min_win
    ]
    with Pool(min(len(jobs), os.cpu_count() or 4)) as pool:
        results = pool.map(one_run, jobs)

    print(
        f"\n{args.capital:,.0f} rupees, {args.months} months, minors + budget + {args.min_margin}% margin gate, "
        f"{'25% compounding' if not args.no_compound else 'no compounding'}\n"
    )
    head = f"{'stock':<11}{'gate Rs':>8}{'trades':>8}{'losers':>8}{'closed':>14}{'DP paid':>12}{'bag':>14}{'NET':>14}{'%/yr':>8}{'avg used':>12}{'purse' if not args.no_compound else '':>16}"
    print(head)
    print("-" * len(head))
    for r in results:
        print(
            f"{r['symbol']:<11}{r['min_win']:>8.0f}{r['trades']:>8}{r['losers']:>8}"
            f"{rs(r['closed']):>14}{rs(r['dp']):>12}{rs(r['bag']):>14}{rs(r['net']):>14}{r['pct_yr']:>7.1f}%"
            f"{r['avg_deployed'] / r['peak'] * 100 if r['peak'] else 0:>11.0f}%"
            f"{('  ' + rs(r['purse'])) if not args.no_compound else '':>18}"
            f"{pumped_note(r)}"
        )


if __name__ == "__main__":
    main()
