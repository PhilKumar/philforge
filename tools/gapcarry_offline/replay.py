"""tools/gapcarry_offline/replay.py -- the Gap Carry book, rebuilt from the archives.

The book this repo publishes (runs/NIFTY_5m_rsi70_atm4.csv) had no generator.
It was produced once, in a session, by something that is gone -- the same way
the five-year options builder was, and for the same reason it was worth
reconstructing: a number nobody can regenerate is a number nobody can extend,
audit, or price differently.

    # prove the machinery first -- rebuild the published book and diff it
    python3 tools/gapcarry_offline/replay.py --check

    # then extend it to wherever the archives now reach
    python3 tools/gapcarry_offline/replay.py --csv runs/NIFTY_5m_rsi70_atm4.csv

    # and ask what the SAME trades cost on the other vendor
    python3 tools/gapcarry_offline/replay.py --pricing upstox --csv /tmp/upstox.csv

WHY THE VENDOR IS A FLAG. Dhan stops quoting a strike once it leaves the ATM
band, which happens precisely when the gap was large -- so the exits it loses
are mostly WINNERS, and `engine/gap_carry.py` floors those at intrinsic value.
Four of the published book's 179 trades are floored. Flooring understates them,
so the published net is a floor, not an estimate. Upstox holds a different set
of strikes (only what PhilForge once fetched), so it fails in the opposite
direction. `--pricing hybrid` asks Dhan first and Upstox second and counts who
answered, which is the only way to say how much of the gap is really rescued
rather than implying it all is.

NOTHING HERE IS A NEW RULE. Every trade comes out of `engine.gap_carry.replay`,
the same walk the paper loop and the backtest route drive, with the archives
injected in place of a broker. If this file and the page ever disagree, this
file is wrong.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from cascade_costs import calculate_nifty_option_round_costs  # noqa: E402
from engine import gap_carry as gap_carry_mod  # noqa: E402
from engine.gap_carry import GapCarryConfig  # noqa: E402
from options.dhan_listed import DhanListedSource  # noqa: E402
from tools.ema_options_backtest import STORES, HybridSource  # noqa: E402
from tools.nifty_expiry_calendar import lot_size, weekly_expiries  # noqa: E402
from tools.nifty_index_from_dhan import load_minutes, sessions, to_bars, to_daily  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
HERE = os.path.dirname(os.path.abspath(__file__))
PUBLISHED = os.path.join(HERE, "runs", "NIFTY_5m_rsi70_atm4.csv")

# The columns the published book carries, in its order. build_gapcarry_report.py
# reads these by name, so the order is cosmetic and the names are not.
COLUMNS = [
    "session",
    "exit_session",
    "side",
    "strike",
    "expiry",
    "lot",
    "rsi",
    "close",
    "ema",
    "entry_spot",
    "exit_spot",
    "entry_premium",
    "exit_premium",
    "priced",
    "capital",
    "charges",
    "net",
]


def _upstox_source(root: str):
    """The Upstox archive, cache-only.

    NEVER allowed to backfill from here. This tool runs while the live engines
    may be holding the broker session, and a replay that quietly fetches is a
    replay that can change someone else's trading day.
    """
    from tools.ema_options_backtest import UpstoxArchiveSource

    return UpstoxArchiveSource(root, "NIFTY")


def build_source(pricing: str, weeklies: list, index_close: dict, upstox_root: str):
    """Dhan, Upstox, or both -- and the object that can say who answered."""
    if pricing == "upstox":
        src = _upstox_source(upstox_root)
        if not src.expiries():
            raise SystemExit(f"no Upstox archive under {upstox_root}")
        return src
    dhan = DhanListedSource(weeklies, STORES, "NIFTY", nearest_within=0)
    if not dhan.stores:
        raise SystemExit(f"no Dhan option stores found under {os.path.join(ROOT, 'data')}")
    for store in dhan.stores.values():
        # The bleed filter is not optional. Dhan's expiryCode=2 series carries
        # other underlyings into the NIFTY files, and one of those priced as
        # NIFTY is the worst kind of silent error.
        if not hasattr(store, "dropped"):
            raise RuntimeError(
                "options/dhan_listed.py here does not filter rows by the index's own level. "
                "Commit that filter before trusting any number from this tool."
            )
        store.levels = index_close
    if pricing == "dhan":
        return dhan
    return HybridSource(dhan, _upstox_source(upstox_root))


def premium_reader(source, walk_forward: int, walk_back: int):
    """The app's own premium rule, not a stricter one.

    The archive holds a bar only for a minute the contract actually traded, so
    an exact-minute lookup throws away real fills. `_hybrid_premium_lookup`
    walks the minute itself, then FORWARD (an order resting at the level fills
    at the option's next trade), then BACK, never across a day. Copied here
    rather than tightened, because a replay that prices more strictly than the
    page books a different strategy.
    """
    stats = {"exact": 0, "forward": 0, "back": 0, "missed": 0}

    def price_at(when: datetime, strike: int, side: str, expiry: date):
        stamp = when.replace(tzinfo=None) if when.tzinfo is not None else when
        contract = SimpleNamespace(
            symbol="NIFTY", underlying="NIFTY", strike=float(strike), expiry=expiry, option_type=str(side).upper()
        )
        price = source.lookup(stamp, contract)
        if price is not None:
            stats["exact"] += 1
            return price
        for ahead in range(1, walk_forward + 1):
            later = stamp + timedelta(minutes=ahead)
            if later.date() != stamp.date() or later.time() > dt_time(15, 30):
                break
            price = source.lookup(later, contract)
            if price is not None:
                stats["forward"] += 1
                return price
        for back in range(1, walk_back + 1):
            earlier = stamp - timedelta(minutes=back)
            if earlier.date() != stamp.date() or earlier.time() < dt_time(9, 15):
                break
            price = source.lookup(earlier, contract)
            if price is not None:
                stats["back"] += 1
                return price
        stats["missed"] += 1
        return None

    return price_at, stats


def rows_for(positions: list) -> list:
    """One CSV row per position, in the published book's own shape."""
    out = []
    for p in positions:
        out.append(
            {
                "session": p.session.isoformat(),
                "exit_session": p.exit_timestamp.date().isoformat() if p.exit_timestamp else "",
                "side": p.side,
                "strike": int(p.strike),
                "expiry": p.expiry.isoformat(),
                "lot": int(p.lot_size),
                "rsi": round(float(p.signal.rsi), 1),
                "close": round(float(p.signal.close), 2),
                "ema": round(float(p.signal.ema), 2),
                "entry_spot": round(float(p.entry_spot), 2),
                "exit_spot": round(float(p.exit_spot), 2) if p.exit_spot is not None else "",
                "entry_premium": round(float(p.entry_premium), 2),
                "exit_premium": round(float(p.exit_premium), 2) if p.exit_premium is not None else "",
                "priced": str(bool(p.exit_priced)),
                "capital": round(float(p.capital), 2),
                "charges": round(float(p.charges), 2),
                "net": round(float(p.net), 2) if p.net is not None else "",
            }
        )
    return out


def _same_number(a, b, tol: float = 0.005) -> bool:
    """Compare as numbers, not strings.

    The published book wrote raw floats -- `13755.000000000002`,
    `556.4003999999986` -- so a string compare reports thirteen capital
    "mismatches" that are the same number typed differently. That noise buried
    the three trades that really do price differently.
    """
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def diff_against_published(rows: list) -> int:
    """Compare with the published book, separating what MUST match from what
    is expected to move.

    THE RULE must reproduce exactly: the sessions it fired on, and for each,
    the side, strike, expiry, lot and entry premium. If any of those drift,
    this rebuild is a different strategy wearing the old book's name, and
    nothing downstream of it can be trusted.

    THE COST MODEL is expected to differ. The published book's `charges`
    column is internally consistent (net == gross - charges on all 179 rows)
    but does not come from `cascade_costs.calculate_nifty_option_round_costs`,
    and the difference fits no schedule: it swings both ways, -6.74 to +4.86,
    with no relation to turnover. Whatever computed it went the way the
    generator went. So the rebuild charges what the PAGE charges -- the same
    function `engine/gap_carry_paper.py` books a paper round with -- and the
    divergence is reported rather than chased.

    EXIT PRICING may differ on a handful of trades, because an archive answers
    a strike it lost differently on different days. Those are named, not
    summarised, since each one is a real trade priced two ways.
    """
    if not os.path.exists(PUBLISHED):
        print(f"no published book at {PUBLISHED} to compare against")
        return 1
    with open(PUBLISHED) as fh:
        old_rows = list(csv.DictReader(fh))
    old = {r["session"]: r for r in old_rows}
    new = {r["session"]: r for r in rows if r["session"] in old}

    only_old = sorted(set(old) - set(new))
    only_new = sorted(set(new) - set(old))
    shared = sorted(set(old) & set(new))

    print()
    print(f"published trades     : {len(old_rows)}")
    print(f"rebuilt, same window : {len(new)}")
    if only_old:
        print(f"  MISSING from rebuild ({len(only_old)}): {only_old[:8]}")
    if only_new:
        print(f"  EXTRA in rebuild     ({len(only_new)}): {only_new[:8]}")

    rule_fields = ["side", "strike", "expiry", "lot", "rsi", "close", "ema", "entry_spot", "entry_premium"]
    rule_bad = []
    for key in shared:
        bad = [f for f in rule_fields if not _same_number(old[key].get(f), new[key].get(f))]
        if bad:
            rule_bad.append((key, bad))
    print()
    print("THE RULE (must be identical)")
    print(f"  fields checked     : {', '.join(rule_fields)}")
    print(f"  trades differing   : {len(rule_bad)}")
    for key, bad in rule_bad[:6]:
        for f in bad:
            print(f"     {key} {f}: published={old[key].get(f)!r} rebuilt={new[key].get(f)!r}")

    priced_bad = [k for k in shared if not _same_number(old[k].get("exit_premium"), new[k].get("exit_premium"))]
    print()
    print("EXIT PRICING (archive-dependent)")
    print(f"  trades priced differently : {len(priced_bad)}")
    for key in priced_bad:
        a, b = old[key], new[key]
        print(
            f"     {key}  published {float(a['exit_premium']):>9.2f} (priced={a['priced']})"
            f"   rebuilt {float(b['exit_premium']):>9.2f} (priced={b['priced']})"
        )

    old_charges = sum(float(r["charges"]) for r in old_rows)
    new_charges = sum(float(new[k]["charges"]) for k in shared)
    old_net = sum(float(r["net"]) for r in old_rows)
    new_net = sum(float(new[k]["net"]) for k in shared if new[k]["net"] != "")
    # What the rebuild would net if it kept the published book's cost model --
    # this isolates the pricing difference from the charging difference.
    net_on_old_costs = new_net + new_charges - old_charges
    print()
    print("THE MONEY")
    print(f"  charges published  : Rs {old_charges:>12,.2f}")
    print(f"  charges rebuilt    : Rs {new_charges:>12,.2f}   ({new_charges - old_charges:+,.2f})")
    print(f"  net published      : Rs {old_net:>12,.2f}")
    print(f"  net rebuilt        : Rs {new_net:>12,.2f}   ({new_net - old_net:+,.2f})")
    print(f"  of which pricing   : Rs {net_on_old_costs - old_net:>+12,.2f}")
    print(f"  of which charging  : Rs {old_charges - new_charges:>+12,.2f}")

    ok = not only_old and not only_new and not rule_bad
    print()
    if ok:
        print("RULE REPRODUCES — the rebuild fires on the same sessions, buys the same")
        print("contracts at the same prices. Safe to extend.")
    else:
        print("RULE DOES NOT REPRODUCE — do not publish this book.")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tf", default="5m", help="signal chart: 5m, 10m, 15m, 30m")
    ap.add_argument("--rsi", type=float, default=70.0, help="RSI threshold, read as a mirror (70 -> calls, 30 -> puts)")
    ap.add_argument("--offset", type=int, default=4, help="strikes IN the money")
    ap.add_argument("--lots", type=int, default=1)
    ap.add_argument(
        "--compound-step-pct",
        type=float,
        default=0.0,
        help="add one lot per this %% of the ladder capital BANKED; 0 is off",
    )
    ap.add_argument(
        "--compound-capital", type=float, default=0.0, help="the capital the compound step is a percentage of"
    )
    ap.add_argument("--compound-max-lots", type=int, default=20)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="", help="default: wherever the archives reach")
    ap.add_argument("--pricing", default="hybrid", choices=("dhan", "upstox", "hybrid"))
    ap.add_argument("--upstox-root", default=os.path.join(ROOT, "data", "option_archive", "upstox"))
    ap.add_argument("--warmup-sessions", type=int, default=6, help="trailing sessions fed to the indicators")
    ap.add_argument("--walk-forward", type=int, default=15, help="minutes to walk forward for a missing print")
    ap.add_argument("--walk-back", type=int, default=30, help="minutes to walk back for a missing print")
    ap.add_argument("--csv", default="", help="write the book here")
    ap.add_argument(
        "--exit-time",
        default="09:20",
        help=(
            "IST minute the carry is sold, HH:MM (default 09:20, the live rule). "
            "09:15 is the opening minute -- see the note in main() about walk-forward."
        ),
    )
    ap.add_argument(
        "--cut-losers-at-open",
        action="store_true",
        help="sell at --early-exit-time when the carry is already below its entry premium",
    )
    ap.add_argument("--early-exit-time", default="09:15", help="IST minute the losing carry is cut, HH:MM")
    ap.add_argument("--check", action="store_true", help="rebuild the published window and diff it, then stop")
    args = ap.parse_args(argv)

    # WHEN THE CARRY IS SOLD. The live rule is 09:20; Phil asked what the same
    # book looks like sold at 09:15, the opening minute (2026-09-03).
    #
    # READ THE WALK-FORWARD COUNT BEFORE BELIEVING A 09:15 RESULT. `price_at`
    # walks BACK for a missing print only as far as 09:15, so at 09:20 a hole
    # can be filled from either side, while at 09:15 there is nothing behind it
    # -- every miss is filled by walking FORWARD, i.e. by a later, different
    # price. A 09:15 book with many forward fills is not really a 09:15 book.
    def _hhmm(raw: str, flag: str) -> dt_time:
        try:
            hh, mm = (int(part) for part in str(raw).split(":")[:2])
            return dt_time(hh, mm)
        except (TypeError, ValueError):
            raise SystemExit(f"{flag} must be HH:MM, got {raw!r}") from None

    exit_at = _hhmm(args.exit_time, "--exit-time")
    config = GapCarryConfig(
        timeframe=args.tf,
        rsi_threshold=float(args.rsi),
        strike_offset_steps=int(args.offset),
        lots=int(args.lots),
        compound_step_pct=float(args.compound_step_pct),
        compound_base_capital=float(args.compound_capital),
        compound_max_lots=int(args.compound_max_lots),
        exit_time=exit_at,
        cut_losers_at_open=bool(args.cut_losers_at_open),
        early_exit_time=_hhmm(args.early_exit_time, "--early-exit-time"),
    )
    config.validate()

    minute = load_minutes()
    bar_rule = f"{int(args.tf.rstrip('m'))}min"
    bars = to_bars(minute, bar_rule)
    daily = to_daily(minute)
    session_days = sessions(minute)
    weeklies = weekly_expiries(session_days)
    index_close = {d.strftime("%Y-%m-%d"): float(c) for d, c in daily["close"].items()}

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else session_days[-1]
    if args.check:
        with open(PUBLISHED) as fh:
            published_sessions = [date.fromisoformat(r["session"]) for r in csv.DictReader(fh)]
        # One session past the last published entry, because the exit is the
        # NEXT session and the walk needs it present to close the last trade.
        end = min(end, max(published_sessions) + timedelta(days=7))

    # Bars grouped by session, once, so the trailing window is a slice.
    by_session: dict = {}
    for stamp, row in bars.iterrows():
        by_session.setdefault(stamp.date(), []).append(
            SimpleNamespace(
                timestamp=stamp.to_pydatetime().replace(tzinfo=IST),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
        )
    walk_days = [d for d in session_days if start <= d <= end]
    order = {d: i for i, d in enumerate(session_days)}

    def candles_for(day: date) -> list:
        i = order.get(day)
        if i is None:
            return []
        window = session_days[max(0, i - int(args.warmup_sessions)) : i + 1]
        return [bar for d in window for bar in by_session.get(d, [])]

    # The index at a minute, from the same series the bars are folded from.
    spot_series = minute["open"]

    def spot_at(when: datetime):
        stamp = when.replace(tzinfo=None) if when.tzinfo is not None else when
        try:
            return float(spot_series.asof(stamp))
        except (KeyError, ValueError):
            return None

    source = build_source(args.pricing, weeklies, index_close, args.upstox_root)
    price_at, price_stats = premium_reader(source, int(args.walk_forward), int(args.walk_back))

    def expiry_for(day: date):
        later = [e for e in weeklies if e >= day]
        return later[0] if later else None

    skips: list = []
    positions = gap_carry_mod.replay(
        walk_days,
        config=config,
        candles_for=candles_for,
        spot_at=spot_at,
        price_at=price_at,
        expiry_for=expiry_for,
        lot_size_for=lot_size,
        charges_for=lambda d, buy, sell, qty: float(
            calculate_nifty_option_round_costs(buy_price=buy, sell_price=sell, quantity=qty).total
        ),
        on_skip=lambda d, why: skips.append((d, why)),
    )

    rows = rows_for(positions)
    net = sum(float(r["net"]) for r in rows if r["net"] != "")
    floored = sum(1 for r in rows if r["priced"] != "True")
    print(f"window     : {start} -> {end}  ({len(walk_days)} sessions)")
    print(f"pricing    : {args.pricing}")
    print(f"trades     : {len(rows)}   net Rs {net:,.2f}   floored at intrinsic: {floored}")
    print(
        f"premium    : {price_stats['exact']:,} exact, {price_stats['forward']:,} forward, "
        f"{price_stats['back']:,} back, {price_stats['missed']:,} unanswered"
    )
    if hasattr(source, "report"):
        print(f"vendors    : {source.report()}")
    if skips:
        reasons: dict = {}
        for _d, why in skips:
            reasons[why] = reasons.get(why, 0) + 1
        print(f"skipped    : {len(skips)} sessions")
        for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"             {n:>4}  {why}")

    if args.check:
        return diff_against_published(rows)

    if args.csv:
        target = args.csv if os.path.isabs(args.csv) else os.path.join(HERE, args.csv)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {target}  ({len(rows)} trades)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
