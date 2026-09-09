"""tools/fib_ladder_nse_sweep.py -- the share ladder across stocks, charts and
span floors, one row each.

    python3 tools/fib_ladder_nse_sweep.py --tf 15 60 --min-span 0 1 2

Workers set nothing global (the ladder takes all its settings as arguments),
but they each load their own candles so the parent never ships a DataFrame
through a pipe.
"""

from __future__ import annotations

import argparse
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.fib_ladder_nse import run, to_bars  # noqa: E402
from tools.rule3070_nse import DP_PER_SELL_DAY, FEE_BUY, FEE_SELL, load_candles, rs  # noqa: E402


def one_run(job: dict) -> dict:
    df = load_candles(job["symbol"], job["months"])
    bars = to_bars(df, job["tf"])
    ladders = run(
        bars,
        job["capital"],
        job["ladder_cap"],
        job["stop_loss"],
        job["min_span"] / 100.0,
        escalate=job["escalate"],
        fill_on_close=job["fill_on_close"],
    )
    last_close = df["close"].iloc[-1]
    span_days = (df.index[-1] - df.index[0]).days

    closed, open_cost, bag, sell_days = [], 0.0, 0.0, {}
    for lad in ladders:
        if not lad.fills:
            continue
        if lad.reason == "OPEN":
            open_cost += lad.cost
            bag += lad.shares * last_close - lad.cost
            continue
        proceeds = lad.shares * lad.exit_price
        net = proceeds - lad.cost - FEE_BUY * lad.cost - FEE_SELL * proceeds
        closed.append([net, lad.reason])
        sell_days.setdefault(lad.exit_ts.date(), []).append(len(closed) - 1)
    for _day, idxs in sell_days.items():
        for i in idxs:
            closed[i][0] -= job["dp"] / len(idxs)

    total = sum(row[0] for row in closed)
    yr = (span_days / 365.25) or 1
    return {
        "symbol": job["symbol"],
        "tf": job["tf"],
        "min_span": job["min_span"],
        "ladders": len(closed),
        "losers": sum(1 for row in closed if row[0] < 0),
        "broken": sum(1 for row in closed if row[1] == "MOTHER BROKEN"),
        "closed": total,
        "bag": bag,
        "open_cost": open_cost,
        "net": total + bag,
        "pct_yr": (total + bag) / yr / job["capital"] * 100,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--symbols", nargs="+", default=["ICICIBANK", "HDFCBANK", "RELIANCE", "INFY", "ADANIENT", "ETERNAL", "TMPV"]
    )
    ap.add_argument("--tf", nargs="+", type=int, default=[15, 60])
    ap.add_argument("--min-span", nargs="+", type=float, default=[0.0, 1.0, 2.0])
    ap.add_argument("--capital", type=float, default=200000.0)
    ap.add_argument("--ladder-cap", type=float, default=75000.0)
    ap.add_argument("--stop-loss", type=float, default=0.0)
    ap.add_argument("--months", type=int, default=36)
    ap.add_argument("--escalate", action="store_true")
    ap.add_argument("--fill-on-close", action="store_true")
    ap.add_argument("--dp", type=float, default=DP_PER_SELL_DAY)
    args = ap.parse_args()

    jobs = [
        {
            "symbol": symbol,
            "tf": tf,
            "min_span": span,
            "capital": args.capital,
            "ladder_cap": args.ladder_cap,
            "stop_loss": args.stop_loss,
            "months": args.months,
            "dp": args.dp,
            "escalate": args.escalate,
            "fill_on_close": args.fill_on_close,
        }
        for symbol in args.symbols
        for tf in args.tf
        for span in args.min_span
    ]
    with Pool(min(len(jobs), os.cpu_count() or 4)) as pool:
        results = pool.map(one_run, jobs)

    print(
        f"\nFib touch ladder on shares · {args.capital:,.0f} rupees · {rs(args.ladder_cap)} per ladder · "
        f"{args.months} months\n"
    )
    head = (
        f"{'stock':<11}{'chart':>7}{'span floor':>12}{'ladders':>9}{'losers':>8}{'broken':>8}"
        f"{'closed':>14}{'bag':>14}{'NET':>14}{'%/yr':>8}"
    )
    print(head)
    print("-" * len(head))
    for r in results:
        print(
            f"{r['symbol']:<11}{str(r['tf']) + 'm':>7}{r['min_span']:>11.1f}%{r['ladders']:>9}{r['losers']:>8}"
            f"{r['broken']:>8}{rs(r['closed']):>14}{rs(r['bag']):>14}{rs(r['net']):>14}{r['pct_yr']:>7.1f}%"
        )


if __name__ == "__main__":
    main()
