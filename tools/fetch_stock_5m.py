"""tools/fetch_stock_5m.py -- 5-minute NSE cash-equity candles, from Upstox.

tools/fetch_index_candles.py does this for an INDEX and resamples 1-minute
bars by hand; Upstox's v3 endpoint serves 5-minute bars for a stock directly,
one month per request (a wider range is a 400), back to at least 2022. Same
token as the option pricing -- Upstox allows several live tokens, so a
backfill here does NOT kill a running session the way a local Dhan mint does
(data/cascade_dhan.py explains that trap).

    python3 tools/fetch_stock_5m.py ICICIBANK HDFCBANK RELIANCE INFY --months 36

Writes tools/.stock_cache/<SYMBOL>_5m.json as [epoch_seconds, o, h, l, c],
ascending, de-duplicated -- traded slots only, nothing invented for the
overnight or the weekend. Upstox serves these prices ADJUSTED for splits and
bonuses (RELIANCE's Oct-2024 1:1 and HDFCBANK's Aug-2025 1:1 both read as
continuous series), which is what any geometry engine needs; the flip side is
that they are not the rupee prices printed on a contract note from that day.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.cascade_upstox import _read_token  # noqa: E402

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".stock_cache")
BASE = "https://api.upstox.com/v3/historical-candle/{key}/minutes/5/{to}/{frm}"

# Instrument keys from Upstox's NSE master
# (https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz).
# All cash equity, lot size 1.
KEYS: Dict[str, str] = {
    "ICICIBANK": "NSE_EQ|INE090A01021",
    "HDFCBANK": "NSE_EQ|INE040A01034",
    "RELIANCE": "NSE_EQ|INE002A01018",
    "INFY": "NSE_EQ|INE009A01021",
    "TCS": "NSE_EQ|INE467B01029",
    "SBIN": "NSE_EQ|INE062A01020",
    "AXISBANK": "NSE_EQ|INE238A01034",
    "ITC": "NSE_EQ|INE154A01025",
    "LT": "NSE_EQ|INE018A01030",
    "NIFTYBEES": "NSE_EQ|INF204KB14I2",
    # The volatile names -- the ones that actually fall far enough to pay a
    # quarter-of-the-fall target.
    "ADANIENT": "NSE_EQ|INE423A01024",
    # Zomato renamed itself ETERNAL in 2025; same ISIN, so the history is one
    # continuous series under either name.
    "ETERNAL": "NSE_EQ|INE758T01015",
    # Tata Motors demerged in Oct 2025. The ORIGINAL ISIN (INE155A01022) went
    # to the passenger-vehicle company, so TMPV carries the continuous chart;
    # TMCV (INE1TAE01010) is the new commercial-vehicle listing.
    "TMPV": "NSE_EQ|INE155A01022",
    "TMCV": "NSE_EQ|INE1TAE01010",
    # A second set, picked for variety of temperament rather than size: metals
    # and a power name that swing hard, two quality compounders, and three
    # that have had violent single-name moves.
    "TATASTEEL": "NSE_EQ|INE081A01020",
    "HINDALCO": "NSE_EQ|INE038A01020",
    "JSWSTEEL": "NSE_EQ|INE019A01038",
    "VEDL": "NSE_EQ|INE205A01025",
    "TATAPOWER": "NSE_EQ|INE245A01021",
    "BAJFINANCE": "NSE_EQ|INE296A01032",
    "TITAN": "NSE_EQ|INE280A01028",
    "MARUTI": "NSE_EQ|INE585B01010",
    "DMART": "NSE_EQ|INE192R01011",
    "PAYTM": "NSE_EQ|INE982J01020",
    "IRCTC": "NSE_EQ|INE335Y01020",
}
ALIASES = {
    "INFOSYS": "INFY",
    "ICICI": "ICICIBANK",
    "HDFC": "HDFCBANK",
    "RIL": "RELIANCE",
    "ZOMATO": "ETERNAL",
    "TATAMOTORS": "TMPV",
}


def canonical(symbol: str) -> str:
    symbol = symbol.upper()
    return ALIASES.get(symbol, symbol)


def months_back(count: int, end: date) -> List[Tuple[date, date]]:
    """The `count` complete months before `end`'s month, as (first, last) day
    pairs -- the endpoint's one-month window."""
    out: List[Tuple[date, date]] = []
    year, month = end.year, end.month
    for _ in range(count):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        first = date(year, month, 1)
        last = (first.replace(day=28) + timedelta(days=6)).replace(day=1) - timedelta(days=1)
        out.append((first, last))
    return list(reversed(out))


def fetch_month(key: str, token: str, first: date, last: date) -> List[tuple]:
    url = BASE.format(key=urllib.parse.quote(key, safe=""), to=last.isoformat(), frm=first.isoformat())
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=120)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    rows = []
    for row in (resp.json().get("data") or {}).get("candles") or []:
        stamp = datetime.fromisoformat(row[0])
        rows.append((int(stamp.timestamp()), float(row[1]), float(row[2]), float(row[3]), float(row[4])))
    return rows


def back_adjust(rows: List[tuple], threshold: float = 0.25) -> Tuple[List[tuple], List[tuple]]:
    """Splice a series across a corporate action Upstox did NOT adjust.

    Upstox adjusts splits and bonuses, but not a DEMERGER: Tata Motors' price
    drops 39.9% on 2025-10-14 because half the company left, and every holder
    was handed the other listing. Left alone that reads as a 40% crash, which
    is exactly the shape this strategy hunts -- it would trade a fall that no
    holder ever suffered.

    The vendor fix, applied here: multiply every bar BEFORE the break by the
    ratio (open after / close before), so percentage moves are untouched and
    the series is continuous. Returns (rows, breaks found).
    """
    breaks = []
    for i in range(1, len(rows)):
        ratio = rows[i][1] / rows[i - 1][4]
        if abs(ratio - 1.0) > threshold:
            breaks.append((i, ratio))
    if not breaks:
        return rows, []
    adjusted = list(rows)
    for i, ratio in reversed(breaks):
        adjusted = [(t, o * ratio, h * ratio, low * ratio, c * ratio) for t, o, h, low, c in adjusted[:i]] + adjusted[
            i:
        ]
    return adjusted, [(datetime.fromtimestamp(rows[i][0]), ratio) for i, ratio in breaks]


def load(symbol: str, months: int = 36, refetch: bool = False, adjust: bool = True) -> List[tuple]:
    """Cached 5m bars for one stock, oldest first. A cache deeper than the ask
    is trimmed; a cache shallower than the ask is refetched rather than
    silently short-changing the caller."""
    symbol = canonical(symbol)
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{symbol}_5m.json")
    windows = months_back(months, date.today())
    start_ts = int(datetime(windows[0][0].year, windows[0][0].month, 1).timestamp())
    if os.path.exists(path) and not refetch:
        with open(path, "r", encoding="utf-8") as handle:
            rows = [tuple(row) for row in json.load(handle)]
        if rows and rows[0][0] <= start_ts + 5 * 86400:  # a month may open on a holiday
            rows = [row for row in rows if row[0] >= start_ts]
            return back_adjust(rows)[0] if adjust else rows
    key = KEYS.get(symbol)
    if not key:
        raise SystemExit(f"No Upstox instrument key for {symbol}; add it to KEYS.")
    token = _read_token()
    seen: dict = {}
    for first, last in windows:
        rows = fetch_month(key, token, first, last)
        print(f"  {symbol} {first:%Y-%m}: {len(rows):>5,} bars")
        for row in rows:
            seen[row[0]] = row
    ordered = [seen[ts] for ts in sorted(seen)]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(ordered, handle)  # the cache keeps the RAW prices Upstox sent
    return back_adjust(ordered)[0] if adjust else ordered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--months", type=int, default=36)
    parser.add_argument("--refetch", action="store_true")
    args = parser.parse_args()
    for raw in args.symbols:
        rows = load(raw, args.months, refetch=args.refetch)
        symbol = canonical(raw)
        if not rows:
            print(f"{symbol}: nothing fetched")
            continue
        first, last = datetime.fromtimestamp(rows[0][0]), datetime.fromtimestamp(rows[-1][0])
        print(f"{symbol}: {len(rows):,} bars, {first:%Y-%m-%d} .. {last:%Y-%m-%d}")
        # An UNadjusted corporate action prints as a one-bar cliff and reads as
        # a crash to the engine, so say so rather than trade it.
        worst = None
        for prev, cur in zip(rows, rows[1:]):
            move = cur[1] / prev[4] - 1.0
            if abs(move) > 0.15 and (worst is None or abs(move) > abs(worst[1])):
                worst = (datetime.fromtimestamp(cur[0]), move)
        if worst:
            print(
                f"   WARNING possible unadjusted corporate action: {worst[0]:%Y-%m-%d %H:%M} moved {worst[1] * 100:+.1f}%"
            )
        for when, ratio in back_adjust(load(raw, args.months, adjust=False))[1]:
            print(f"   SPLICED at {when:%Y-%m-%d %H:%M}: everything before it scaled by {ratio:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
