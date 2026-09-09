"""Replay any saved strategy-builder config over the full Dhan archive.

Usage:
    python3 tools/philforge_strategy_on_dhan.py <config.json> <out.json> [from] [to]

<config.json> is a saved strategy's `config` column, verbatim.
"""

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from engine.backtest import run_backtest  # noqa: E402
from tools.nifty_expiry_calendar import weekly_expiries  # noqa: E402
from tools.nifty_index_from_dhan import load_minutes, sessions  # noqa: E402
from tools.philforge_dhan_selector import DhanHistoricalPremiumSelector  # noqa: E402

STORES = {
    "e1": os.path.join(REPO, "data", "dhan_options"),
    "e2": os.path.join(REPO, "data", "dhan_options_e2"),
    "m1": os.path.join(REPO, "data", "dhan_options_m1"),
    "m2": os.path.join(REPO, "data", "dhan_options_m2"),
}


def _capital_profile(trades: list) -> dict:
    """Premium outstanding per trade, and the most ever deployed at once.

    A bought option ties up its premium: entry_price * quantity, which is what
    the engine's own capital check reserves. Trades that overlap in time are
    summed, because the account has to carry them together.
    """
    per_trade = []
    events = []
    for t in trades:
        price = float(t.get("entry_price", 0) or 0)
        qty = float(t.get("quantity", t.get("qty", 0)) or 0)
        used = round(price * qty, 2)
        if used <= 0:
            continue
        per_trade.append(used)
        events.append((str(t.get("entry_time")), used))
        events.append((str(t.get("exit_time")), -used))
    if not per_trade:
        return {}
    events.sort()
    running = peak = 0.0
    for _stamp, delta in events:
        running += delta
        peak = max(peak, running)
    ordered = sorted(per_trade)
    return {
        "trades": len(per_trade),
        "avg_per_trade": round(sum(per_trade) / len(per_trade)),
        "median_per_trade": round(ordered[len(ordered) // 2]),
        "max_single_trade": round(max(per_trade)),
        "peak_deployed": round(peak),
    }


def main() -> None:
    cfg = dict(json.load(open(sys.argv[1])))
    out_path = sys.argv[2]
    from_date = sys.argv[3] if len(sys.argv) > 3 else "2021-01-01"
    to_date = sys.argv[4] if len(sys.argv) > 4 else "2026-08-31"

    minute = load_minutes()
    minute = minute.loc[from_date : to_date + " 23:59"]
    if minute.empty:
        raise SystemExit("no NIFTY minutes in that range")

    weeklies = weekly_expiries(sessions(minute))
    cfg["mode"] = "backtest"
    cfg["from_date"], cfg["to_date"] = from_date, to_date
    selector = DhanHistoricalPremiumSelector("26000", STORES, weeklies)
    cfg["_upstox_premium_selector"] = selector  # the key the engine reads

    result = run_backtest(minute, cfg["entry_conditions"], cfg["exit_conditions"], cfg)
    trades = result.get("trades", []) or []
    net = sum(float(t.get("pnl", 0) or 0) for t in trades)
    wins = sum(1 for t in trades if float(t.get("pnl", 0) or 0) > 0)
    reasons: dict = {}
    for t in trades:
        reasons[str(t.get("exit_reason", "?"))] = reasons.get(str(t.get("exit_reason", "?")), 0) + 1

    summary = {
        "range": [from_date, to_date],
        "bars": int(len(minute)),
        "trades": len(trades),
        "wins": wins,
        "net": round(net, 2),
        "exit_reasons": reasons,
        "selector": selector.report(),
        "trade_keys": [
            f"{str(t.get('entry_time'))[:16]}|{t.get('strike')}|{str(t.get('exit_time'))[:16]}|{round(float(t.get('pnl', 0) or 0), 2)}"
            for t in trades
        ],
        # What each trade actually tied up. Without this a funding figure has to
        # be guessed from a nominal premium, and a laddered book's requirement
        # grows with the book -- the one number a reader most needs is the peak.
        "capital": _capital_profile(trades),
        # The full trade list, so a report can be rebuilt on this configuration
        # instead of describing an older one. trade_keys carries only net P&L,
        # which is not enough for fees, win/loss detail or hold time.
        "trades_full": [
            {
                "entry_time": str(t.get("entry_time"))[:16],
                "exit_time": str(t.get("exit_time"))[:16],
                "option_type": t.get("option_type"),
                "strike": t.get("strike"),
                "qty": t.get("qty"),
                "entry_price": t.get("entry_price"),
                "exit_price": t.get("exit_price"),
                "pnl": round(float(t.get("pnl", 0) or 0), 2),
                "fees": round(float(t.get("fees", 0) or 0), 2),
                "exit_reason": t.get("exit_reason"),
            }
            for t in trades
        ],
    }
    json.dump(summary, open(out_path, "w"), indent=1)
    print(f"bars={summary['bars']} trades={summary['trades']} wins={wins} net={summary['net']:,.2f}")
    print("exit reasons:", reasons)
    print("selector:", selector.report())


if __name__ == "__main__":
    main()
