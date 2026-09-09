"""Supertrend-only sweep: ATR period 10, multiplier 1.0-4.0, on 1/3/5/15/60m.

Phil's ask (2026-08-26): backtest ONLY the supertrend strategy across
timeframes 1m, 3m, 5m, 15m, 1H with ATR period pinned at 10 and the ATR
multiplier swept 1, 1.5, 2, 2.5, 3, 3.5, 4.

The strategy is the plain always-in supertrend, expressed in options the way
every PhilForge book is:

  * CE side: in the market while supertrend_dir is +1, out on the flip down.
  * PE side: mirror.
  * BUY, current-week NIFTY, premium-near-250 contract (the live book's leg),
    real Upstox archive premiums, cache_only — a signal with no cached
    contract is SKIPPED and counted, never invented.
  * No stop, no target: the flip is the only exit, plus the 15:20 square-off.
  * Deployed engine's execution costs (12bps spread, 6/8bps slippage).

Execution timeframe == the supertrend's timeframe, so the flip is decided on
that timeframe's CLOSED candle (supertrend_dir is only attached to the
execution frame; off-frame supertrend exposes just the line, not the dir).
"""

from __future__ import annotations

import argparse
import glob
import itertools
import json
import os
import sys
from datetime import time as dtime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.backtest_upstox import UpstoxHistoricalPremiumSelector  # noqa: E402
from engine.backtest import run_backtest  # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".nifty_cache")


def load_index_1m(from_date: str, to_date: str) -> pd.DataFrame:
    """Every cached NIFTY 1-minute bar, de-duplicated and session-filtered.

    The Dhan cache carries out-of-session bars (some past 19:00); a supertrend
    resampled over those would see candles no session ever printed.
    """
    rows: dict[str, list] = {}
    for path in sorted(glob.glob(os.path.join(CACHE, "NIFTY_1m_*.json"))):
        for ts, o, h, low, c, *rest in json.load(open(path)):
            rows[str(ts)[:19]] = [o, h, low, c, (rest[0] if rest else 0)]
    frame = pd.DataFrame.from_dict(rows, orient="index", columns=["open", "high", "low", "close", "volume"])
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    frame = frame[(frame.index.time >= dtime(9, 15)) & (frame.index.time <= dtime(15, 29))]
    return frame.loc[from_date : to_date + " 23:59"]


# The engine's square-off trigger compares the BAR START time against the
# square-off clock, so a frame whose last complete bar starts before 15:20
# never squares off and the position is carried overnight (which then dies on
# a missing premium at the next open). Each frame squares off on the last bar
# it actually prints; the exit itself is priced off the raw minute tape.
SQOFF_BY_TF = {1: "15:20", 3: "15:15", 5: "15:15", 15: "15:00", 60: "14:15"}

# Entries fill at the NEXT bar's open and the engine checks exits before
# entries, so a fill landing on the day's last bar can never square off and is
# carried overnight. market_close gates the FILL row's start time; each frame
# stops entering early enough that at least one later bar remains to exit on.
CLOSE_BY_TF = {1: "15:25", 3: "15:15", 5: "15:15", 15: "15:00", 60: "14:15"}


def build_config(tf_minutes: int, multiplier: float, side: str, max_tpd: int) -> tuple[dict, list, list]:
    mult_token = f"{multiplier:g}"  # 1 -> "1", 1.5 -> "1.5" (indicator id regex wants no trailing zeros)
    ind = f"Supertrend_10_{mult_token}_{tf_minutes}m"
    in_trend = "is_above" if side == "CE" else "is_below"
    out_trend = "is_below" if side == "CE" else "is_above"
    entry = [
        {"logic": "IF", "left": "supertrend_dir", "operator": in_trend, "right": "number", "right_number_value": 0}
    ]
    exit_ = [
        {"logic": "IF", "left": "supertrend_dir", "operator": out_trend, "right": "number", "right_number_value": 0}
    ]
    config = {
        "mode": "backtest",
        "segment": "indices",
        "instrument": "26000",
        "lots": 1,
        "lot_size": 0,  # historical NIFTY lot for the trade date
        "market_open": "09:15",
        "market_close": CLOSE_BY_TF[tf_minutes],
        "combined_sqoff_time": SQOFF_BY_TF[tf_minutes],
        "max_trades_per_day": max_tpd,
        "indicators": [ind],
        "timeframe_minutes": tf_minutes,
        "fetch_timeframe_minutes": 1,
        "execution_timeframe_minutes": tf_minutes,
        "entry_evaluation_timeframe_minutes": tf_minutes,
        "signal_exit_next_open": True,
        # The deployed engine's execution costs.
        "spread_bps": 12,
        "entry_slippage_bps": 6,
        "exit_slippage_bps": 8,
        "legs": [
            {
                "transaction_type": "BUY",
                "option_type": side,
                "expiry": "current_week",
                "strike_type": "premium_near",
                "strike_value": 250,
                "lots": 1,
                "sl_pct": 0,
                "target_pct": 0,
                "trail_pct": 0,
                "sqoff_time": SQOFF_BY_TF[tf_minutes],
            }
        ],
    }
    return config, entry, exit_


def _max_dd(trades) -> float:
    peak = cum = dd = 0.0
    for t in sorted(trades, key=lambda r: str(r.get("entry_time"))):
        cum += float(t.get("pnl", 0) or 0)
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default="2024-10-01")
    ap.add_argument("--to-date", default="2026-07-30")
    ap.add_argument("--timeframes", nargs="+", type=int, default=[1, 3, 5, 15, 60])
    ap.add_argument("--multipliers", nargs="+", type=float, default=[1, 1.5, 2, 2.5, 3, 3.5, 4])
    ap.add_argument("--sides", nargs="+", default=["CE", "PE"])
    ap.add_argument("--max-tpd", type=int, default=30)
    ap.add_argument("--out", default="tools/supertrend_sweep_results.json")
    args = ap.parse_args()

    df = load_index_1m(args.from_date, args.to_date)
    print(f"[data] {len(df):,} 1-minute index bars, {df.index[0]} -> {df.index[-1]}", flush=True)

    results = []
    hdr = f"{'tf':>4}{'mult':>6}{'side':>5}{'trades':>8}{'win%':>7}{'net':>13}{'avg':>9}{'maxDD':>11}{'gaps':>6}"
    print("\n" + hdr, flush=True)
    for tf, mult, side in itertools.product(args.timeframes, args.multipliers, args.sides):
        config, entry, exit_ = build_config(tf, mult, side, args.max_tpd)
        config["_upstox_premium_selector"] = UpstoxHistoricalPremiumSelector("26000", cache_only=True)
        result = run_backtest(df_raw=df.copy(), entry_conditions=entry, exit_conditions=exit_, strategy_config=config)
        if result.get("status") == "error":
            print(f"{tf:>4}{mult:>6}{side:>5}  ERROR: {result.get('message')}", flush=True)
            results.append({"tf": tf, "mult": mult, "side": side, "error": str(result.get("message"))})
            continue
        trades = result.get("trades", []) or []
        net = sum(float(t.get("pnl", 0) or 0) for t in trades)
        wins = sum(1 for t in trades if float(t.get("pnl", 0) or 0) > 0)
        n = len(trades) or 1
        gaps = len(config.get("_option_data_gaps") or [])
        print(
            f"{tf:>4}{mult:>6g}{side:>5}{len(trades):>8}{wins / n * 100:>6.0f}%{net:>13,.0f}"
            f"{net / n:>9,.0f}{_max_dd(trades):>11,.0f}{gaps:>6}",
            flush=True,
        )
        results.append(
            {
                "tf": tf,
                "mult": mult,
                "side": side,
                "trades": len(trades),
                "wins": wins,
                "net": round(net, 2),
                "avg": round(net / n, 2),
                "max_dd": round(_max_dd(trades), 2),
                "gaps": gaps,
                "trade_rows": [
                    {
                        "entry_time": str(t.get("entry_time")),
                        "exit_time": str(t.get("exit_time")),
                        "pnl": round(float(t.get("pnl", 0) or 0), 2),
                        "exit_reason": t.get("exit_reason"),
                        "strike": t.get("strike"),
                    }
                    for t in trades
                ],
            }
        )
        with open(args.out, "w") as fh:
            json.dump({"from": args.from_date, "to": args.to_date, "results": results}, fh)
    print(f"\nwrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
