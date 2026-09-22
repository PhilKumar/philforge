"""Rebuild the engine-derived half of report_data.json from saved-strategy replays.

The five-year Options tearsheet was rebuilt on 2026-09-15 from 34 replays of
the saved CE and PE strategies through tools/philforge_strategy_on_dhan.py,
stitched together by a script that lived only in a session scratchpad and is
gone. This is that script, reconstructed so the book can be re-derived when a
rule changes (Phil, 2026-09-22: the 20-day trend filter on CE).

    python3 ladder_report.py --runs DIR --tag N --check   # must reproduce the published file
    python3 ladder_report.py --runs DIR --tag F --write   # rewrite report_data.json

DIR holds `out_<tag>_<name>.json` files written by philforge_strategy_on_dhan.py:

    ce_size{1,2,3,4,6,8}   CE at N lots, N+1 on expiry, +25% ladder on Rs 50,000 x N, cap 20
    pe_size{...}           PE the same with the +75% ladder (tag N only: PE is unchanged)
    ce_slip{0,...,100}     CE at 4 lots with entry and exit slippage set to that many bps
    pe_slip{...}           PE the same
    ce_cap{4,6,8,12,20}    CE as running now (2 lots, 3 on expiry, Rs 1,00,000) with that lot cap
    ce_flat4, ce_exp4      CE at 4 lots flat, and 4 lots with 5 on expiry, no ladder
    pe_live                PE as running now (2 lots, 3 on expiry, Rs 1,00,000)

The book itself is size4 (4 lots, 5 on expiry, both ladders on Rs 2,00,000).
PE files are always read from tag N. Fields this script does not derive
(the Upstox comparison, the 2026-09-08 reconciliation, the historical
corrections) are carried over unchanged.

--check rebuilds every field below from tag N and compares it with the
checked-in report_data.json; it must say MATCHES before --write is trusted.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from rebuild_data import (  # noqa: E402
    BROKERAGE,
    best_worst,
    curve,
    dow_bucket,
    drawdown,
    regime_split,
    streaks,
    summarise,
    summarise_bucket,
)

REPORT = _HERE / "report_data.json"
YEARS = 5.63  # the window the published per-year figures divide by
SIZES = (1, 2, 3, 4, 6, 8)
SLIPS = (0, 6, 10, 14, 25, 50, 100)
CAPS = (4, 6, 8, 12, 20)
HEADLINE_KEYS = (
    "label trades first last net fees wins losses win_rate avg_trade avg_win avg_loss profit_factor "
    "max_dd dd_from dd_to return_over_dd streak_win streak_loss median_hold_min"
).split()


def load(runs: Path, tag: str, name: str, side: str) -> tuple[list[dict], dict]:
    raw = json.loads((runs / f"out_{tag}_{name}.json").read_text())
    out = []
    for r in raw["trades_full"]:
        et = datetime.strptime(r["entry_time"], "%Y-%m-%d %H:%M")
        xt = datetime.strptime(r["exit_time"], "%Y-%m-%d %H:%M")
        entry, exit_, qty = float(r["entry_price"]), float(r["exit_price"]), int(r["qty"])
        out.append(
            {
                "side": side,
                "symbol": r["strike"],
                "entry": entry,
                "exit": exit_,
                "qty": qty,
                "date": et.date().isoformat(),
                "entry_time": et.isoformat(sep=" "),
                "exit_date": xt.date().isoformat(),
                "net": float(r["pnl"]),
                "fee": float(r["fees"]),
                "turnover": (entry + exit_) * qty,
                "premium": entry * qty,
                "hold_min": int((xt - et).total_seconds() // 60),
            }
        )
    return out, raw.get("capital") or {}


def headline(trades, label):
    s = summarise(trades, label)
    return {k: s[k] for k in HEADLINE_KEYS}


def daily_of(trades):
    by_day = defaultdict(float)
    for t in trades:
        by_day[t["date"]] += t["net"]
    return sorted(by_day.items())


def peak_day_premium(trades):
    by_day = defaultdict(float)
    for t in trades:
        by_day[t["date"]] += t["premium"]
    return max(by_day.values())


def equity_path(trades, capital):
    """The worst fall in rupees, that fall against the highest the account ever
    reached, and the lowest the account ever sat at.

    The percentage is the rupee fall over the PEAK account, not over the account
    at the moment of the fall -- that is how the published page reads (pair
    -Rs 3,70,639 on a peak of Rs 26.1 lakh is -14.2%).
    """
    eq = peak = float(capital)
    dd_rs = 0.0
    low = eq
    for _, p in daily_of(trades):
        eq += p
        low = min(low, eq)
        peak = max(peak, eq)
        dd_rs = min(dd_rs, eq - peak)
    return dd_rs, 100 * dd_rs / peak, low


def book_row(trades, capital, label, **extra):
    net = sum(t["net"] for t in trades)
    dd_rs, dd_pct, _ = equity_path(trades, capital)
    row = {
        "label": label,
        "final": round(capital + net),
        "multiple": round((capital + net) / capital, 2),
        "dd_rs": round(dd_rs),
        "dd_pct": round(dd_pct, 1),
        "trades": len(trades),
    }
    row.update(extra)
    return row


def by_year_net(trades):
    y = defaultdict(float)
    for t in trades:
        y[t["date"][:4]] += t["net"]
    return y


def build(runs: Path, tag: str, base: dict) -> dict:
    d = copy.deepcopy(base)
    ce, ce_cap = load(runs, tag, "ce_size4", "CE")
    pe, pe_cap = load(runs, "N", "pe_size4", "PE")
    both = pe + ce

    d["headline"]["pe"] = headline(pe, base["headline"]["pe"]["label"])
    d["headline"]["ce"] = headline(ce, base["headline"]["ce"]["label"])
    d["headline"]["combined"] = headline(both, base["headline"]["combined"]["label"])
    d["best_worst"]["pe"] = best_worst(pe)
    d["best_worst"]["ce"] = best_worst(ce)
    d["by_year"] = {
        "pe": summarise_bucket(pe, 4),
        "ce": summarise_bucket(ce, 4),
        "combined": summarise_bucket(both, 4),
    }
    by_day = defaultdict(float)
    n_day = defaultdict(int)
    for t in both:
        by_day[t["date"]] += t["net"]
        n_day[t["date"]] += 1
    daily = sorted(by_day.items())
    series, cum = [], 0.0
    for day, p in daily:
        cum += p
        series.append([day, round(p), round(cum), n_day[day]])
    months = summarise_bucket(both, 7)
    d["by_month"] = months
    d["by_dow"] = dow_bucket(both)
    d["regime"] = {"pe": regime_split(pe), "ce": regime_split(ce), "both": regime_split(both)}
    d["curve"]["combined"] = [[day, c] for day, _, c, _ in series]
    d["curve"]["pe"] = curve(pe)
    d["curve"]["ce"] = curve(ce)
    top = sorted(daily, key=lambda kv: -kv[1])[:10]
    bot = sorted(daily, key=lambda kv: kv[1])[:10]
    net = sum(t["net"] for t in both)
    fees = sum(t["fee"] for t in both)
    turnover = sum(t["turnover"] for t in both)
    w_days, l_days = streaks([p for _, p in daily])
    d["daily"] = {
        "trading_days": len(daily),
        "green_days": sum(1 for _, p in daily if p > 0),
        "best_day": [top[0][0], round(top[0][1], 2)],
        "worst_day": [bot[0][0], round(bot[0][1], 2)],
        "avg_day": round(net / len(daily)),
        "median_day": round(sorted(p for _, p in daily)[len(daily) // 2]),
        "day_streak_w": w_days,
        "day_streak_l": l_days,
        "months": len(months),
        "green_months": sum(1 for v in months.values() if v["net"] > 0),
        "best_month": list(max(months.items(), key=lambda kv: kv[1]["net"])),
        "worst_month": list(min(months.items(), key=lambda kv: kv[1]["net"])),
    }
    d["charges"] = {
        "brokerage": round(BROKERAGE * len(both)),
        "stt": round(turnover * 0.0125 / 100),
        "exchange": round(turnover * 0.053 / 100),
        "gst": round((BROKERAGE * len(both) + turnover * 0.053 / 100) * 0.18),
        "sebi": round(turnover * 10 / 1e7),
        "stamp": round(turnover * 0.003 / 100),
        "total": round(fees),
        "gross": round(net + fees),
        "net": round(net),
        "turnover": round(turnover),
        "pct_turnover": round(100 * fees / turnover, 2),
        "per_trade": round(fees / len(both)),
    }
    d["series"] = series
    d["best10"] = [[day, round(p), n_day[day]] for day, p in top]
    d["worst10"] = [[day, round(p), n_day[day]] for day, p in bot]
    d["capital"] = {
        "avg": round(sum(t["premium"] for t in both) / len(both)),
        "median": round(sorted(t["premium"] for t in both)[len(both) // 2]),
        "max_trade": round(max(t["premium"] for t in both)),
        # Never the sum of a day: the two books do not overlap in time.
        "peak_day": max(ce_cap["peak_deployed"], pe_cap["peak_deployed"]),
    }
    d["window"]["to"] = json.loads((runs / f"out_{tag}_ce_size4.json").read_text())["range"][1]

    sizing = []
    for lots in SIZES:
        ce_s, ce_c = load(runs, tag, f"ce_size{lots}", "CE")
        pe_s, pe_c = load(runs, "N", f"pe_size{lots}", "PE")
        s = ce_s + pe_s
        s_net = sum(t["net"] for t in s)
        s_dd, _, _ = drawdown(daily_of(s))
        # The two books never hold a position at the same moment, so the account
        # carries the LARGER of their peaks, never the sum of a day's premium.
        s_peak = max(ce_c["peak_deployed"], pe_c["peak_deployed"])
        floor = s_peak + abs(s_dd)
        funded = floor * 1.3
        sizing.append(
            {
                "lots": lots,
                "net": round(s_net),
                "per_trade": round(s_net / len(s)),
                "charges": round(sum(t["fee"] for t in s)),
                "peak": round(s_peak),
                "dd": round(s_dd),
                "floor": round(floor),
                "funded": round(funded),
                "per_year": round(s_net / YEARS),
                "roi": round(100 * s_net / funded),
            }
        )
    d["sizing"] = sizing

    slip = []
    for bps in SLIPS:
        s = load(runs, tag, f"ce_slip{bps}", "CE")[0] + load(runs, "N", f"pe_slip{bps}", "PE")[0]
        slip.append(
            {
                "bps": bps,
                "net": round(sum(t["net"] for t in s)),
                "win": round(100 * sum(t["net"] > 0 for t in s) / len(s), 1),
            }
        )
    d["slip"] = slip
    # How much slippage the book absorbs before it stops paying: the widest
    # level MEASURED by a replay that still ends green. Re-pricing finished
    # trades instead would double-count the 10/14 bps already inside them.
    green = [row["bps"] for row in slip if row["net"] > 0]
    worst_green = max(green) if green else 0
    d["breakeven_slip_pct"] = round(worst_green / 100, 2)
    d["slip_margin"]["breakeven_bps"] = worst_green
    d["slip_margin"]["multiple_inside"] = round(worst_green / d["slip_margin"]["live_bps_per_side"], 1)

    comp = d["compounding"]
    flat, _ = load(runs, tag, "ce_flat4", "CE")
    exp, _ = load(runs, tag, "ce_exp4", "CE")
    ce_years = by_year_net(ce)
    top_year = max(ce_years, key=ce_years.get)
    comp["ce"] = {
        "flat": book_row(flat, 200000, comp["ce"]["flat"]["label"]),
        "ladder": {**book_row(ce, 200000, comp["ce"]["ladder"]["label"]), "capital": 200000},
        "worst_single_trade": round(min(t["net"] for t in ce)),
        "top_year_pct": round(100 * ce_years[top_year] / sum(ce_years.values())),
        "top_year": top_year,
        "y2026": round(ce_years.get("2026", 0)),
        "expiry": book_row(exp, 200000, comp["ce"]["expiry"]["label"]),
    }
    caps = []
    for cap in CAPS:
        c, _ = load(runs, tag, f"ce_cap{cap}", "CE")
        row = book_row(c, 100000, "")
        caps.append(
            {
                "cap": cap,
                "final": row["final"],
                "multiple": row["multiple"],
                "dd_rs": row["dd_rs"],
                "dd_pct": row["dd_pct"],
                "worst_trade": round(min(t["net"] for t in c)),
                "y2026": round(by_year_net(c).get("2026", 0)),
            }
        )
    comp["ce_caps"] = caps

    pair = comp["pair"]
    p_net = net
    p_dd_rs, p_dd_pct, p_low = equity_path(both, pair["capital"])
    p_years = by_year_net(both)
    pair.update(
        {
            "trades": len(both),
            "final": round(pair["capital"] + p_net),
            "multiple": round((pair["capital"] + p_net) / pair["capital"], 2),
            "dd_rs": round(p_dd_rs),
            "dd_pct": round(p_dd_pct, 1),
            "lowest_equity": round(p_low),
            "green_years": sum(1 for v in p_years.values() if v > 0),
            "years": len(p_years),
            "by_year": {k: round(v) for k, v in sorted(p_years.items())},
            "note": (
                f"Each book falls harder alone than the pair does together: CE {comp['ce']['ladder']['dd_pct']}%, "
                f"PE {comp['pe']['ladder']['dd_pct']}%, pair {round(p_dd_pct, 1)}%. They lose in different months."
            ),
        }
    )

    live_ce, live_ce_cap = load(runs, tag, "ce_cap20", "CE")
    live_pe, live_pe_cap = load(runs, "N", "pe_live", "PE")
    now = comp["running_now"]
    live_both = live_ce + live_pe
    l_net = sum(t["net"] for t in live_both)
    _, l_dd_pct, l_low = equity_path(live_both, now["pair_capital"])
    now.update(
        {
            "pair_final": round(now["pair_capital"] + l_net),
            "pair_multiple": round((now["pair_capital"] + l_net) / now["pair_capital"], 2),
            "pair_dd_pct": round(l_dd_pct, 1),
            "pair_lowest_equity": round(l_low),
            "pair_trades": len(live_both),
        }
    )

    def cap_block(c):
        return {
            "avg": c["avg_per_trade"],
            "median": c["median_per_trade"],
            "worst_single": c["max_single_trade"],
            "peak": c["peak_deployed"],
        }

    cp = d["capital_profile"]
    cp["live"]["ce"] = cap_block(live_ce_cap)
    cp["live"]["pe"] = cap_block(live_pe_cap)
    cp["documented"]["ce"] = cap_block(ce_cap)
    cp["documented"]["pe"] = cap_block(pe_cap)

    # What the call book's rules say now. The filter is the only rule that
    # changed on 2026-09-22, and the archive comparison below was measured
    # before it, so it is dated rather than silently re-used as current.
    if tag == "F":
        d["live_config"]["ce"]["trend_filter"] = "only when NIFTY closed above its 20-day average"
        d["live_config"]["ce"]["trend_filter_ta"] = "NIFTY தனது 20-நாள் சராசரிக்கு மேல் இருக்கும்போது மட்டும்"
        d["live_config"]["read_from"] = (
            "philforge.db strategies table (#48 CE_SL15_NoMonTue with the 20-day trend filter, "
            "#50 PE_NoTarget with S4/S5 exits removed), 2026-09-22 IST"
        )
        d["source_comparison"]["dated"] = (
            "Measured on 15 September 2026, on the call book before its 20-day trend filter."
        )
        d["source_comparison"]["dated_ta"] = (
            "15 செப்டம்பர் 2026 அன்று, 20-நாள் போக்கு வடிகட்டிக்கு முந்தைய CE புத்தகத்தில் அளவிடப்பட்டது."
        )

    comb = d["headline"]["combined"]
    dep = d["deployed"]
    dep.update(
        {
            "max_drawdown": round(comb["max_dd"]),
            "dd_from": comb["dd_from"],
            "dd_to": comb["dd_to"],
            "return_over_drawdown": comb["return_over_dd"],
            "peak_deployed": max(ce_cap["peak_deployed"], pe_cap["peak_deployed"]),
            "return_on_capital": round(100 * comb["net"] / max(ce_cap["peak_deployed"], pe_cap["peak_deployed"])),
            "median_hold_min": comb["median_hold_min"],
        }
    )
    return d


def four_books(ce, pe, gap_csv: Path, fib_parts: list[tuple[Path, str, str]]) -> dict:
    """CE + PE + Gap Carry + Fib Boundary on one calendar (Phil, 2026-09-22).

    Each book is booked on the day its money lands: CE and PE on their
    session, Gap Carry on the morning it exits, Fib Boundary on its session.
    Gap Carry is its replay CSV (tools/gapcarry_offline/replay.py); Fib is its
    chain-sweep CSVs, each used only inside its own date window.
    """
    day = defaultdict(lambda: defaultdict(float))
    for t in ce:
        day[t["date"]]["ce"] += t["net"]
    for t in pe:
        day[t["date"]]["pe"] += t["net"]
    for row in csv.DictReader(open(gap_csv)):
        if row["net"]:
            day[row["exit_session"]]["gap"] += float(row["net"])
    for path, lo, hi in fib_parts:
        for row in csv.DictReader(open(path)):
            if lo <= row["day"] <= hi and int(row["buys"] or 0) > 0:
                day[row["day"]]["fib"] += float(row["net"])
    end = max(t["date"] for t in ce + pe)
    days = sorted(d for d in day if d <= end)

    def line(keys):
        eq = peak = dd = 0.0
        months = defaultdict(float)
        years = defaultdict(float)
        for d in days:
            v = sum(day[d][k] for k in keys)
            eq += v
            peak = max(peak, eq)
            dd = min(dd, eq - peak)
            months[d[:7]] += v
            years[d[:4]] += v
        return {
            "net": round(eq),
            "dd": round(dd),
            "red_months": sum(1 for v in months.values() if v < 0),
            "months": len(months),
            "by_year": {k: round(v) for k, v in sorted(years.items())},
        }

    return {
        "window": [days[0], days[-1]],
        "ce": line(["ce"]),
        "pe": line(["pe"]),
        "gap": line(["gap"]),
        "fib": line(["fib"]),
        "three": line(["ce", "pe", "gap"]),
        "four": line(["ce", "pe", "gap", "fib"]),
    }


# The published file carries two blocks its own numbers no longer support: the
# charge split does not add up from its own turnover (brokerage Rs 97,301 is not
# Rs 80 x 923 trades, and its percentage rows are not this book's turnover), and
# the average/median capital per trade is the older, larger book's. Both are
# recomputed here from the model the engine actually charges, so --check reports
# them as corrections rather than failing on them.
KNOWN_CORRECTIONS = {
    ".capital.avg": "average premium per trade, recomputed from this book",
    ".capital.median": "median premium per trade, recomputed from this book",
    ".charges.brokerage": "Rs 80 a trade, as the engine charges",
    ".charges.stt": "0.0125% of this book's turnover",
    ".charges.exchange": "0.053% of this book's turnover",
    ".charges.gst": "18% of brokerage + exchange",
    ".charges.sebi": "Rs 10 per crore of this book's turnover",
    ".charges.stamp": "0.003% of this book's turnover",
}


def diff(a, b, path=""):
    """Every leaf where the rebuilt and published reports disagree."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'published' if k in b else 'rebuilt'}")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} vs {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out += diff(x, y, f"{path}[{i}]")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if abs(float(a) - float(b)) > 0.011:
            out.append(f"{path}: rebuilt {a} vs published {b}")
    elif a != b:
        out.append(f"{path}: rebuilt {a!r} vs published {b!r}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True, type=Path)
    ap.add_argument("--tag", default="N")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    ap.add_argument("--out", type=Path, default=REPORT)
    ap.add_argument("--baseline", type=Path, help="the published report to check against (default: report_data.json)")
    ap.add_argument("--gap", type=Path, help="Gap Carry replay CSV (4-lot ladder)")
    ap.add_argument("--fib", action="append", default=[], help="PATH:FROM:TO, a Fib chain-sweep CSV and its window")
    args = ap.parse_args()
    base = json.loads((args.baseline or REPORT).read_text())
    rebuilt = build(args.runs, args.tag, base)
    if args.gap and args.fib:
        parts = [(Path(x.split(":")[0]), x.split(":")[1], x.split(":")[2]) for x in args.fib]
        ce, _ = load(args.runs, args.tag, "ce_size4", "CE")
        pe, _ = load(args.runs, "N", "pe_size4", "PE")
        rebuilt["four_books"] = four_books(ce, pe, args.gap, parts)
    if args.check:
        problems = diff(json.loads(json.dumps(rebuilt)), base)
        corrected = [p for p in problems if p.split(":")[0] in KNOWN_CORRECTIONS]
        problems = [p for p in problems if p.split(":")[0] not in KNOWN_CORRECTIONS]
        for c in corrected:
            print("CORRECTED", c, "--", KNOWN_CORRECTIONS[c.split(":")[0]])
        if problems:
            print(f"DIFFERS in {len(problems)} places:")
            for p in problems[:60]:
                print("  ", p)
            sys.exit(1)
        print("MATCHES")
        return
    args.out.write_text(json.dumps(rebuilt, indent=1))
    print("wrote", args.out)


if __name__ == "__main__":
    main()
