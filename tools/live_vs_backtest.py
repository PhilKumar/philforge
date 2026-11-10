"""Phil's live CE/PE books against their own backtest over the same days.

Phil, 2026-11-10: "show live CE PE vs backtest". The 5-year replay's Dhan
option store ends in September 2026; the Scalp Practice archive
(data/practice/NIFTY, real 1-minute index OHLC and the nearest two weekly
expiries' strikes around the money) runs to 06-Nov-2026. This replays a saved
strategy config through the SAME engine (`engine.backtest.run_backtest`) with a
selector that reads that archive.

Usage:
    python3 tools/live_vs_backtest.py <config.json> <out.json> <from> <to>
"""

import json
import os
import sys
from datetime import date, datetime

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from engine.backtest import run_backtest  # noqa: E402
from tools.philforge_dhan_selector import PREMIUM_STRIKE_TYPES, HistoricalOptionSelection  # noqa: E402

ROOT = os.path.join(REPO, "data", "practice", "NIFTY")


class PracticeArchiveSelector:
    """The `_upstox_premium_selector` seam, answered from the practice archive."""

    def __init__(self) -> None:
        self.served = 0
        self.gaps: dict = {}
        self.last_gap = ""
        self._days: dict = {}

    def _day(self, day: date) -> pd.DataFrame:
        key = day.isoformat()
        if key not in self._days:
            path = os.path.join(ROOT, f"{key}.opt.parquet")
            self._days[key] = pd.read_parquet(path) if os.path.exists(path) else pd.DataFrame()
        return self._days[key]

    def _gap(self, why: str):
        self.gaps[why] = self.gaps.get(why, 0) + 1
        self.last_gap = why
        return None

    def select(self, entry_time: datetime, entry_spot: float, leg: dict, timeframe_minutes: int):
        self.last_gap = ""
        option_type = str(leg.get("option_type") or "CE").upper()
        strike_type = str(leg.get("strike_type") or "").lower()
        target = float(leg.get("strike_value") or 0)
        if option_type not in {"CE", "PE"} or strike_type not in PREMIUM_STRIKE_TYPES or target <= 0:
            return self._gap("invalid premium-target leg")
        if str(leg.get("expiry") or "current_week").lower() not in ("current_week", "weekly", "near_week"):
            return self._gap("only the nearest weekly is in the archive")
        stamp = entry_time.replace(second=0, microsecond=0, tzinfo=None)
        frame = self._day(stamp.date())
        if frame.empty:
            return self._gap("no archive day")
        expiry = sorted(frame["expiry"].unique())[0]
        side = frame[(frame["side"] == option_type) & (frame["expiry"] == expiry)].set_index("ts")
        if stamp not in side.index:
            return self._gap("no bar at entry minute")
        at_entry = side.loc[[stamp]]
        at_entry = at_entry[at_entry["open"] > 0]
        if at_entry.empty:
            return self._gap("no priced strike")
        if strike_type == "premium_above":
            valid = at_entry[at_entry["open"] >= target]
            row = (
                valid.iloc[int(valid["open"].values.argmin())]
                if len(valid)
                else at_entry.iloc[(at_entry["open"] - target).abs().argsort()[:1]].iloc[0]
            )
        elif strike_type == "premium_below":
            valid = at_entry[at_entry["open"] <= target]
            row = (
                valid.iloc[int(valid["open"].values.argmax())]
                if len(valid)
                else at_entry.iloc[(at_entry["open"] - target).abs().argsort()[:1]].iloc[0]
            )
        else:
            row = at_entry.iloc[(at_entry["open"] - target).abs().argsort()[:1]].iloc[0]
        strike = int(row["strike"])
        history = side[side["strike"] == strike].loc[stamp:][["open", "high", "low", "close"]].copy()
        history["strike"] = strike
        history = history[~history.index.duplicated(keep="last")]
        if int(timeframe_minutes or 1) > 1:
            history = (
                history.resample(f"{int(timeframe_minutes)}min", origin=pd.Timestamp("2021-01-01 09:15"))
                .agg({"open": "first", "high": "max", "low": "min", "close": "last", "strike": "last"})
                .dropna(subset=["open"])
            )
        if history.empty:
            return self._gap("no series after entry")
        self.served += 1
        return HistoricalOptionSelection(
            f"practice|{expiry}|{strike}|{option_type}",
            history,
            strike,
            date.fromisoformat(str(expiry)),
            float(row["open"]),
        )

    def report(self) -> str:
        return f"served={self.served} gaps={self.gaps}"


def index_minutes(start: str, end: str) -> pd.DataFrame:
    days = [d["day"] for d in json.load(open(os.path.join(ROOT, "days.json"))) if start <= d["day"] <= end]
    parts = [pd.read_parquet(os.path.join(ROOT, f"{d}.idx.parquet")) for d in days]
    minute = pd.concat(parts).set_index("ts").sort_index()
    minute = minute[~minute.index.duplicated(keep="last")]
    if "volume" not in minute:
        minute["volume"] = 0
    return minute


def main() -> None:
    cfg = dict(json.load(open(sys.argv[1])))
    out_path, from_date, to_date = sys.argv[2], sys.argv[3], sys.argv[4]
    warm = (pd.Timestamp(from_date) - pd.Timedelta(days=70)).date().isoformat()  # Daily SMA 20, EMAs
    minute = index_minutes(warm, to_date)
    cfg["mode"] = "backtest"
    cfg["from_date"], cfg["to_date"] = from_date, to_date
    selector = PracticeArchiveSelector()
    cfg["_upstox_premium_selector"] = selector
    result = run_backtest(minute, cfg["entry_conditions"], cfg["exit_conditions"], cfg)
    trades = [t for t in (result.get("trades") or []) if str(t.get("entry_time"))[:10] >= from_date]
    rows = [
        {
            "entry_time": str(t.get("entry_time"))[:16],
            "exit_time": str(t.get("exit_time"))[:16],
            "option_type": t.get("option_type"),
            "strike": t.get("strike"),
            "qty": t.get("qty", t.get("quantity")),
            "entry_price": t.get("entry_price"),
            "exit_price": t.get("exit_price"),
            "pnl": round(float(t.get("pnl", 0) or 0), 2),
            "fees": round(float(t.get("fees", 0) or 0), 2),
            "exit_reason": t.get("exit_reason"),
        }
        for t in trades
    ]
    json.dump(
        {"range": [from_date, to_date], "selector": selector.report(), "trades": rows}, open(out_path, "w"), indent=1
    )
    print(f"trades={len(rows)} net={sum(r['pnl'] for r in rows):,.2f} selector: {selector.report()}")


if __name__ == "__main__":
    main()
