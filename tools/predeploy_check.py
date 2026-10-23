"""Is it safe to deploy (restart the live site) right now?

Run ON the server, read-only:  ./venv/bin/python tools/predeploy_check.py

A deploy restarts every engine. On 23-Oct-2026 a 09:35 push restarted the
live books while Dhan's chart API was refusing every request, and both Dhan
books died in their warm-up. Checking "no open position" was not enough: the
books must also be able to load their history again.

Prints one line per check and a verdict; exit code 0 = safe, 1 = hold.
Nothing here places, changes or cancels anything.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def _market_open(now: datetime) -> bool:
    return now.weekday() < 5 and time(9, 0) <= now.time() <= time(23, 55)


async def main() -> int:
    now = datetime.now(IST)
    hold: list[str] = []
    user = await app._db_mod.get_user_by_id(1)

    dhan, _ = app._resolve_user_broker_client(user, allow_admin_fallback=True)
    dhan._allow_token_refresh = False
    try:
        open_dhan = [
            (r.get("tradingSymbol"), r.get("netQty")) for r in dhan.get_positions() or [] if int(r.get("netQty") or 0)
        ]
    except Exception as exc:
        open_dhan = None
        hold.append(f"Dhan positions unreadable: {exc}")
    print("Dhan open positions:", open_dhan)
    if open_dhan:
        hold.append(f"{len(open_dhan)} open Dhan position(s)")

    kite, _ = app._user_zerodha_client(user, require_today=True)
    if kite is not None:
        try:
            open_kite = [
                (r.get("tradingSymbol"), r.get("netQty"))
                for r in kite.get_positions() or []
                if int(r.get("netQty") or 0)
            ]
        except Exception as exc:
            open_kite = None
            hold.append(f"Zerodha positions unreadable: {exc}")
        print("Zerodha open positions:", open_kite)
        if open_kite:
            hold.append(f"{len(open_kite)} open Zerodha position(s)")
    else:
        print("Zerodha: no login today")

    raw = await app._db_mod.get_app_state("scalp_open:1")
    scalps = len((json.loads(raw) if raw else {}).get("open_trades") or [])
    print("Scalp open trades:", scalps)
    if scalps:
        hold.append(f"{scalps} open Scalp trade(s)")
    raw = await app._db_mod.get_app_state("gap_carry_open:1")
    carry = ((json.loads(raw) if raw else {}).get("engine") or {}).get("position")
    print("Gap Carry holding:", bool(carry))
    if carry:
        hold.append("Gap Carry holds a position")

    # Can the live books load their warm-up history after a restart?
    today = now.date()
    ask = dict(security_id="13", exchange_segment="IDX_I", instrument_type="INDEX",
               from_date=(today - timedelta(days=5)).isoformat(), to_date=today.isoformat(), candle_type="5")  # fmt: skip
    try:
        dhan_rows = len(dhan.get_historical_data(**ask))
    except Exception as exc:
        dhan_rows = 0
        print("Dhan chart API:", str(exc)[:120])
    print("Dhan NIFTY 5m candles:", dhan_rows)
    if not dhan_rows:
        kite_rows = 0
        if kite is not None:
            try:
                kite_rows = len(kite.get_historical_data(**ask))
            except Exception as exc:
                print("Zerodha candles:", str(exc)[:120])
        print("Zerodha NIFTY 5m candles (the books' fallback):", kite_rows)
        if not kite_rows:
            hold.append("neither Dhan nor Zerodha gives candles: restarted books cannot warm up")

    market = _market_open(now)
    print(f"Market hours (NSE/MCX): {'OPEN' if market else 'closed'} at {now:%H:%M} IST")
    if hold:
        print("VERDICT: HOLD --", "; ".join(hold))
        return 1
    print("VERDICT: SAFE" + (" (market open: the Scalp engine needs ▶ Start after the deploy)" if market else ""))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
