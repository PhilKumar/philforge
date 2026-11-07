"""Cut the option archive into one file per trading day, for the Scalp practice replay.

Phil, 2026-11-07: "Is it possible to create a same scalp page and simulate trades
and charts and prices to practice without a live price or data?" ... "go ahead
with NIFTY and sensex". The replay plays back a real past day. The server must
never read a whole month to serve one day (a month of NIFTY is ~2M rows; the
1 GB box has frozen on less), so each day is cut out once, here, into:

    <out>/<UNDERLYING>/<YYYY-MM-DD>.opt.parquet   ts, strike, side, open, high, low, close, spot, expiry
    <out>/<UNDERLYING>/<YYYY-MM-DD>.idx.parquet   ts, open, high, low, close   (the index, one row a minute)
    <out>/<UNDERLYING>/days.json                  [{day, expiries: [current week, next week]}, ...]

Option rows come from Dhan's rolling ATM +/-10 series: store e1 is the current
weekly expiry, e2 the next one. A row's `expiry` is the real contract date, from
the exchange calendar for that week (holiday shifts included), so the form shows
the expiry that was actually trading.

    python3 tools/practice/build_days.py --underlying NIFTY --e1 data/dhan_options \
        --e2 data/dhan_options_e2 --index tools/.nifty_cache/nifty_1m_clean.parquet \
        --index tools/.nifty_cache/NIFTY_1m_2026-09-21_2026-09-30.json \
        --index tools/.nifty_cache/NIFTY_1m_2026-10-01_2026-11-06.json --out data/practice

The index must be real OHLC: tools.nifty_index_from_dhan is one sampled price a
minute (open == high == low == close), so its candles have no wicks.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.nifty_expiry_calendar import weekly_expiries as nifty_weekly_expiries  # noqa: E402

try:  # the exchange's listed holidays: an expiry after the last archived day moves off them too
    from engine.live import _NSE_CAPITAL_MARKET_HOLIDAYS as _HOLIDAYS  # noqa: E402
except Exception:  # pragma: no cover - the builder still runs without the app
    _HOLIDAYS = frozenset()


def _trading_day(day: date) -> bool:
    return day.weekday() < 5 and day.isoformat() not in _HOLIDAYS


# SENSEX's real weekly expiries, as BSE listed them (the Upstox archive's
# folders, holiday shifts included). After the last one the weekday rule runs.
SENSEX_LISTED_EXPIRIES_DIR = REPO / "data" / "option_archive" / "upstox" / "sensex"
SENSEX_ERAS = [  # (from, weekday) Monday=0
    (date(2020, 1, 1), 4),  # Friday
    (date(2025, 1, 1), 1),  # Tuesday
    (date(2025, 9, 1), 3),  # Thursday, from SEBI's NSE-Tuesday / BSE-Thursday split
]


def _sensex_weekday(day: date) -> int:
    return [w for start, w in SENSEX_ERAS if start <= day][-1]


def sensex_expiries(sessions: list[date]) -> list[date]:
    listed = []
    if SENSEX_LISTED_EXPIRIES_DIR.is_dir():
        for child in SENSEX_LISTED_EXPIRIES_DIR.iterdir():
            try:
                listed.append(date.fromisoformat(child.name))
            except ValueError:
                continue
    listed = sorted(set(listed))
    last_listed = listed[-1] if listed else date(2000, 1, 1)
    trading = set(sessions)
    out = list(listed)
    week = (last_listed + timedelta(days=1)) - timedelta(days=(last_listed + timedelta(days=1)).weekday())
    end = (sessions[-1] if sessions else last_listed) + timedelta(days=21)
    while week <= end:
        target = week + timedelta(days=_sensex_weekday(week))
        settle = target
        known = target <= (sessions[-1] if sessions else target)
        for back in range(0, 6):
            cand = target - timedelta(days=back)
            if (cand in trading) if known else _trading_day(cand):
                settle = cand
                break
        if settle > last_listed and (not out or settle > out[-1]):
            out.append(settle)
        week += timedelta(days=7)
    return sorted(set(out))


def expiries_for(underlying: str, sessions: list[date]) -> list[date]:
    if underlying == "NIFTY":
        # Trading days after the last session stand in for future sessions, so
        # the last days still have a "next week" expiry (10-Nov-2026 is a
        # holiday: that week's NIFTY expired on the 9th).
        tail = [sessions[-1] + timedelta(days=i) for i in range(1, 22)]
        padded = sorted(set(sessions) | {d for d in tail if _trading_day(d)})
        return nifty_weekly_expiries(padded)
    if underlying == "SENSEX":
        return sensex_expiries(sessions)
    raise SystemExit(f"no expiry calendar for {underlying}")


def load_index(paths: list[str]) -> pd.DataFrame:
    frames = []
    for raw in paths:
        path = Path(raw)
        if path.suffix == ".parquet":
            frame = pd.read_parquet(path)[["open", "high", "low", "close"]]
            frame.index = pd.to_datetime(frame.index)
        else:
            rows = json.load(open(path))
            frame = pd.DataFrame([r[:5] for r in rows], columns=["ts", "open", "high", "low", "close"]).set_index("ts")
            frame.index = pd.to_datetime(frame.index).tz_localize(None)
        frames.append(frame)
    index = pd.concat(frames).sort_index()
    index = index[~index.index.duplicated(keep="first")]  # the first source given wins
    # Session minutes only: the raw caches carry bars stamped as late as 22:00,
    # which drew impossible ranges (see memory proj_nifty_clean_1m_series).
    minutes = index.index.hour * 60 + index.index.minute
    index = index[(minutes >= 9 * 60 + 15) & (minutes <= 15 * 60 + 29)]
    index.index.name = "ts"
    return index


def month_files(directory: str | None, underlying: str) -> dict[str, Path]:
    if not directory:
        return {}
    return {p.stem.split("_", 1)[1]: p for p in Path(directory).glob(f"{underlying}_*.parquet")}


def build(args) -> None:
    underlying = args.underlying.upper()
    out_dir = Path(args.out) / underlying
    out_dir.mkdir(parents=True, exist_ok=True)
    index = load_index(args.index)
    sessions = sorted({ts.date() for ts in index.index})
    expiries = expiries_for(underlying, sessions)

    def nth_expiry(day: date, n: int) -> date | None:
        ahead = [e for e in expiries if e >= day]
        return ahead[n] if len(ahead) > n else None

    stores = {"e1": month_files(args.e1, underlying), "e2": month_files(args.e2, underlying)}
    months = sorted(set(stores["e1"]) | set(stores["e2"]))
    lo = date.fromisoformat(args.from_date) if args.from_date else None
    hi = date.fromisoformat(args.to_date) if args.to_date else None
    manifest_path = out_dir / "days.json"
    manifest = {row["day"]: row for row in json.load(open(manifest_path))} if manifest_path.exists() else {}
    written = 0
    for month in months:
        parts = []
        for store, files in stores.items():
            path = files.get(month)
            if path is None:
                continue
            frame = pd.read_parquet(path, columns=["ts", "strike", "side", "open", "high", "low", "close", "spot"])
            frame["store"] = store
            parts.append(frame)
        if not parts:
            continue
        month_frame = pd.concat(parts, ignore_index=True)
        month_frame["ts"] = pd.to_datetime(month_frame["ts"])
        for day_ts, day_frame in month_frame.groupby(month_frame["ts"].dt.date):
            day = day_ts if isinstance(day_ts, date) else day_ts.date()
            if (lo and day < lo) or (hi and day > hi):
                continue
            idx = index.loc[str(day) : f"{day} 23:59"]
            if idx.empty:
                continue  # no index candles for the chart: not a replayable day
            e1, e2 = nth_expiry(day, 0), nth_expiry(day, 1)
            if e1 is None:
                continue
            day_frame = day_frame.copy()
            day_frame["expiry"] = day_frame["store"].map({"e1": e1.isoformat(), "e2": (e2 or e1).isoformat()})
            day_frame = (
                day_frame.drop(columns=["store"])
                .drop_duplicates(subset=["ts", "expiry", "strike", "side"], keep="last")
                .sort_values(["ts", "expiry", "strike", "side"])
            )
            day_frame["strike"] = day_frame["strike"].astype("int64")
            day_frame.to_parquet(out_dir / f"{day}.opt.parquet", index=False)
            idx.reset_index().to_parquet(out_dir / f"{day}.idx.parquet", index=False)
            manifest[str(day)] = {
                "day": str(day),
                "expiries": [e.isoformat() for e in (e1, e2) if e is not None],
                "rows": int(len(day_frame)),
            }
            written += 1
        print(f"{underlying} {month}: {written} days so far", flush=True)
    rows = [manifest[k] for k in sorted(manifest)]
    json.dump(rows, open(manifest_path, "w"), indent=0)
    print(f"{underlying}: {len(rows)} replayable days in {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--underlying", required=True)
    parser.add_argument("--e1", required=True, help="current-week store (monthly parquet)")
    parser.add_argument("--e2", help="next-week store (monthly parquet)")
    parser.add_argument("--index", action="append", required=True, help="index minute candles (repeatable)")
    parser.add_argument("--out", default=str(REPO / "data" / "practice"))
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    build(parser.parse_args())


if __name__ == "__main__":
    main()
