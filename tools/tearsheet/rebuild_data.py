"""Rebuild report_data.json — the only input build_report.py reads.

The original builder lived in a session scratchpad and is gone, so this
reconstructs it from the source CSVs.

    python3 rebuild_data.py --check    # prove the machinery is faithful
    python3 rebuild_data.py --write    # rewrite report_data.json
    python3 build_report.py            # render the HTML

--check does NOT validate today's book. It rebuilds the OLD target book from
PE_TARGET_FILE and requires it to reproduce the figures published in Aug 2026 —
net, win rate, profit factor, drawdown, charges, turnover, peak capital, the
1-lot sizing ROI and the break-even slippage. If all of those come back
identical, the loaders, the effective-dated lot rule, the fee model and every
derived statistic are faithful, so the current book can be trusted too. Run it
before any publish; if it does not say MATCHES, do not publish.

WHY THE PUT BOOK CHANGED. It used to exit at a Rs 10,000 target, and that
target was being recorded at the best price the option touched inside the
candle rather than at the target — worth Rs 2.12 lakh of fiction across 90 of
563 trades. The target has since been removed from the strategy altogether, so
PE_FILE is the no-target export and there is no fill left to cap. The CE book
never had a target: verified against real option minute bars, 2% of its exits
land on a candle high against 40% of the old put targets.

--honest-fill still exists and caps a PE exit at entry + Rs 10,000/qty. It is
only meaningful against PE_TARGET_FILE, and is not used to build the page.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from engine.backtest import _calc_fees, get_option_contract_lot_size  # noqa: E402


def _legacy_nifty_lot(instrument, contract_expiry):
    """The lot rule as it stood when the Aug-2026 baseline was published.

    c6f13e2 (2026-08-21) corrected NIFTY's pre-cut lot from 50 to 75 — the table
    used to open at 50 in the year 2000, sizing every contract before August
    2021 at two thirds of the real lot. That correction is deliberate and
    tested, so the machinery can no longer reproduce the baseline's rupee
    figures, and --check would report six mismatches for ever.

    --check exists to prove the LOADERS and the FEE MODEL are still faithful,
    not to re-litigate a fixed bug. So it rebuilds the historical book with the
    historical lot rule. Everything from August 2021 onward is identical to the
    current rule; only pre-cut expiries differ. Verified: under this rule the
    rebuild reproduces PE net, CE net, combined net, charges, turnover and
    slip@50bps EXACTLY, to the rupee.
    """
    from engine.backtest import _instrument_family, get_lot_size

    if _instrument_family(instrument) != "NIFTY":
        return get_lot_size(instrument, contract_expiry)
    expiry = contract_expiry
    if isinstance(expiry, datetime):
        expiry = expiry.date()
    if not isinstance(expiry, date):
        expiry = date.fromisoformat(str(expiry))
    if expiry <= date(2024, 4, 25):
        return 50
    if expiry <= date(2024, 12, 26):
        return 25
    if expiry == date(2025, 1, 30):
        return 25
    if expiry <= date(2025, 12, 30):
        return 75
    return 65


# The runs ship WITH the document, so anything published here can be rebuilt
# from the repo alone. ~/Downloads is only a fallback for a freshly exported
# file that has not been copied in yet.
DL = _HERE.parent.parent / "docs" / "assets" / "data"
FALLBACK = Path.home() / "Downloads"
# The put book as it is being traded: no profit target. The old target export
# is kept only to show what the previously published figure was, and why.
PE_FILE = "pe_no_target_5yr.csv"
PE_TARGET_FILE = "pe_target10000_5yr.csv"
CE_FILE = "ce_5yr.csv"
PE_UPSTOX_FILE = "pe_upstox_real.csv"
CE_UPSTOX_FILE = "ce_upstox_real.csv"
# From this date the book is priced on real Upstox option premiums by our own
# engine; before it, only the external export exists. The published book is the
# SPLICE of the two: export up to the eve of the cut, engine from the cut on.
SPLICE_FROM = "2024-10-03"
PE_ENGINE_FILE = "pe_engine_real.csv"  # Results run 376: PE_NoTarget, the saved strategy
CE_ENGINE_FILE = "ce_engine_real.csv"  # Results run 355: CE_SL15_NoMonTue on real premiums


def src(name: str) -> Path:
    """Prefer the copy shipped in the repo; fall back to a fresh export."""
    here = DL / name
    return here if here.exists() else FALLBACK / name


MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
SYM = re.compile(r"^NIFTY(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$")
LOTS = 4
PE_TARGET_RUPEES = 10000.0
REGIME_CUT = "2024-11-01"
BROKERAGE = 80.0


def parse_symbol(s):
    dd, mmm, yy, strike, side = SYM.match(s.strip()).groups()
    return date(2000 + int(yy), MON[mmm], int(dd)), int(strike), side


def load_external(path, side_label, honest_fill=False):
    out = []
    for r in csv.DictReader(open(path, newline="")):
        expiry, strike, _ = parse_symbol(r["Instrument"])
        entry, exit_ = float(r["Entry Price"]), float(r["Exit Price"])
        lot = get_option_contract_lot_size("26000", expiry)
        qty = LOTS * lot
        if honest_fill and side_label == "PE":
            exit_ = min(exit_, entry + PE_TARGET_RUPEES / qty)
        gross = (exit_ - entry) * qty
        fee = _calc_fees((entry + exit_) * qty, gross)
        et = datetime.strptime(r["Entry Time"], "%d %b %Y %H:%M:%S")
        xt = datetime.strptime(r["Exit Time"], "%d %b %Y %H:%M:%S")
        out.append(
            {
                "side": side_label,
                "symbol": r["Instrument"],
                "entry": entry,
                "exit": exit_,
                "qty": qty,
                "lot": lot,
                "date": et.date().isoformat(),
                "entry_time": et.isoformat(sep=" "),
                "net": round(gross - fee, 2),
                "fee": round(fee, 2),
                "turnover": (entry + exit_) * qty,
                "premium": entry * qty,
                "as_exported": float(r["Profit"]),
                "hold_min": int((xt - et).total_seconds() // 60),
                "source": "export",
            }
        )
    return out


def load_engine_run(path, side_label):
    """Our engine's real-premium run, exported by the app (already net of fees).

    Older exports stamped the flat lot of the day; the lot is re-read from each
    contract's expiry so Oct-Dec 2024 is 25 and not 50."""
    out = []
    for r in csv.DictReader(open(path, newline="")):
        et = datetime.strptime(r["entry_time"], "%Y-%m-%d %H:%M")
        xt = datetime.strptime(r["exit_time"], "%Y-%m-%d %H:%M")
        entry, exit_ = float(r["entry_price"]), float(r["exit_price"])
        expiry = date.fromisoformat(r["contract_expiry"][:10]) if r.get("contract_expiry") else None
        lot = get_option_contract_lot_size("26000", expiry) if expiry else int(r["qty"]) // LOTS
        qty = LOTS * lot
        gross = (exit_ - entry) * qty
        fee = _calc_fees((entry + exit_) * qty, gross)
        out.append(
            {
                "side": side_label,
                "symbol": r["strike"],
                "entry": entry,
                "exit": exit_,
                "qty": qty,
                "lot": lot,
                "date": et.date().isoformat(),
                "entry_time": et.isoformat(sep=" "),
                "net": round(gross - fee, 2),
                "fee": round(fee, 2),
                "turnover": (entry + exit_) * qty,
                "premium": entry * qty,
                "as_exported": float(r["pnl"]),
                "hold_min": int((xt - et).total_seconds() // 60),
                "source": "engine",
            }
        )
    return out


def _seam_days(export_trades, spliced, tag):
    """How the engine's trading days sit inside the export's over the shared window.

    engine-only days should be zero (the engine never trades a day the export did
    not); export-only days are either the call book's 2-day cool-off, which the
    export does not apply, or contracts Upstox holds no minute history for."""
    ex = {t["date"] for t in export_trades if t["date"] >= SPLICE_FROM}
    en = {t["date"] for t in spliced if t["source"] == "engine"}
    big = sorted(date.fromisoformat(t["date"]) for t in spliced if t["source"] == "engine" and t["net"] > 20000)
    cool = 0
    for d in sorted(ex - en):
        dd = date.fromisoformat(d)
        if any(0 < (dd - b).days <= 4 for b in big):
            cool += 1
    return {
        f"{tag}_engine_only_days": len(en - ex),
        f"{tag}_export_only_days": len(ex - en),
        f"{tag}_export_only_cooloff": cool,
        f"{tag}_export_only_net": round(sum(t["net"] for t in export_trades if t["date"] in (ex - en)), 2),
    }


def splice(export_trades, engine_trades):
    """Export before SPLICE_FROM, engine from it. One book, two sources."""
    return [t for t in export_trades if t["date"] < SPLICE_FROM] + [
        t for t in engine_trades if t["date"] >= SPLICE_FROM
    ]


def load_upstox(path, side_label):
    out = []
    for r in csv.DictReader(open(path, newline="")):
        et = datetime.strptime(r["entry_time"], "%Y-%m-%d %H:%M")
        xt = datetime.strptime(r["exit_time"], "%Y-%m-%d %H:%M")
        entry, exit_, qty = float(r["entry_price"]), float(r["exit_price"]), int(r["qty"])
        out.append(
            {
                "side": side_label,
                "symbol": r["strike"],
                "entry": entry,
                "exit": exit_,
                "qty": qty,
                "date": et.date().isoformat(),
                "entry_time": et.isoformat(sep=" "),
                "net": float(r["pnl"]),
                "fee": 0.0,
                "turnover": (entry + exit_) * qty,
                "premium": entry * qty,
                "hold_min": int((xt - et).total_seconds() // 60),
            }
        )
    return out


def drawdown(daily):
    peak = cum = worst = 0.0
    pa = dd_from = dd_to = None
    for d, p in daily:
        cum += p
        if cum > peak:
            peak, pa = cum, d
        if cum - peak < worst:
            worst, dd_from, dd_to = cum - peak, pa, d
    return round(worst, 2), dd_from, dd_to


def streaks(seq):
    bw = bl = cw = cl = 0
    for p in seq:
        cw, cl = (cw + 1, 0) if p > 0 else ((0, cl + 1) if p < 0 else (0, 0))
        bw, bl = max(bw, cw), max(bl, cl)
    return bw, bl


def summarise(trades, label):
    ordered = sorted(trades, key=lambda t: t["entry_time"])
    nets = [t["net"] for t in ordered]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    by_day = defaultdict(float)
    for t in ordered:
        by_day[t["date"]] += t["net"]
    daily = sorted(by_day.items())
    dd, dd_from, dd_to = drawdown(daily)
    bw, bl = streaks(nets)
    total = sum(nets)
    buckets = {
        "by_year": defaultdict(lambda: {"net": 0.0, "n": 0, "w": 0}),
        "by_month": defaultdict(lambda: {"net": 0.0, "n": 0, "w": 0}),
        "by_dow": defaultdict(lambda: {"net": 0.0, "n": 0, "w": 0}),
    }
    for t in ordered:
        keys = (t["date"][:4], t["date"][:7], datetime.fromisoformat(t["date"]).strftime("%A"))
        for name, key in zip(buckets, keys):
            buckets[name][key]["net"] += t["net"]
            buckets[name][key]["n"] += 1
            buckets[name][key]["w"] += 1 if t["net"] > 0 else 0

    def fin(d):
        return {k: {"net": round(v["net"], 2), "n": v["n"], "w": v["w"]} for k, v in sorted(d.items())}

    gw, gl = sum(wins), -sum(losses)
    return {
        "label": label,
        "trades": len(ordered),
        "first": daily[0][0],
        "last": daily[-1][0],
        "net": round(total, 2),
        "fees": round(sum(t["fee"] for t in ordered), 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(nets), 1),
        "avg_trade": round(total / len(nets), 2),
        "avg_win": round(gw / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
        "max_gain": round(max(nets), 2),
        "max_loss": round(min(nets), 2),
        "profit_factor": round(gw / gl, 2) if gl else None,
        "expectancy": round(total / len(nets), 2),
        "max_dd": dd,
        "dd_from": dd_from,
        "dd_to": dd_to,
        "return_over_dd": round(total / abs(dd), 2) if dd else None,
        "streak_win": bw,
        "streak_loss": bl,
        "median_hold_min": sorted(t["hold_min"] for t in ordered)[len(ordered) // 2],
        "trading_days": len(daily),
    }


def best_worst(trades):
    def one(t):
        return {
            "symbol": t["symbol"],
            "date": t["date"],
            "net": round(t["net"], 2),
            "entry": t["entry"],
            "exit": t["exit"],
            "qty": t["qty"],
        }

    return {"best": one(max(trades, key=lambda t: t["net"])), "worst": one(min(trades, key=lambda t: t["net"]))}


def regime_split(trades):
    out = {}
    for name, sel in (("before", lambda d: d < REGIME_CUT), ("after", lambda d: d >= REGIME_CUT)):
        s = [t for t in trades if sel(t["date"])]
        n = len(s) or 1
        out[name] = {
            "n": len(s),
            "win": round(100 * sum(1 for t in s if t["net"] > 0) / n, 1),
            "net": round(sum(t["net"] for t in s)),
            "avg": round(sum(t["net"] for t in s) / n),
        }
    return out


def curve(trades):
    cum = 0.0
    out = []
    for t in sorted(trades, key=lambda t: t["entry_time"]):
        cum += t["net"]
        out.append([t["date"], round(cum)])
    return out


def resize(trades, lots):
    """Re-size the whole book to `lots`, recomputing fees — brokerage is FLAT,
    so it does not scale and small sizes are punished."""
    out = []
    for t in trades:
        q = int(t["qty"] / LOTS * lots)
        gross = (t["exit"] - t["entry"]) * q
        fee = _calc_fees((t["entry"] + t["exit"]) * q, gross)
        out.append({**t, "qty": q, "net": gross - fee, "fee": fee, "premium": t["entry"] * q})
    return out


def slipped_net(trades, bps):
    tot = 0.0
    wins = 0
    for t in trades:
        e = t["entry"] * (1 + bps / 10000)
        x = t["exit"] * (1 - bps / 10000)
        gross = (x - e) * t["qty"]
        net = gross - _calc_fees((e + x) * t["qty"], gross)
        tot += net
        wins += net > 0
    return tot, round(100 * wins / len(trades), 1)


def peak_day_premium(trades):
    by_day = defaultdict(float)
    for t in trades:
        by_day[t["date"]] += t["premium"]
    return max(by_day.values())


def build(honest_fill: bool, pe_file: str = PE_FILE, spliced: bool = True) -> dict:
    pe_export = load_external(src(pe_file), "PE", False)
    ce_export = load_external(src(CE_FILE), "CE", honest_fill)
    upe = load_upstox(src(PE_UPSTOX_FILE), "PE")
    uce = load_upstox(src(CE_UPSTOX_FILE), "CE")
    if spliced:
        # Export before the cut, real-premium engine run from it. Both engine
        # runs were checked against the export day-for-day: PE 207/207 days in
        # common and 0 extra, CE 79/79 and 0 extra -- the same strategies, so the
        # seam joins one book to itself, not two different books.
        pe = splice(pe_export, load_engine_run(src(PE_ENGINE_FILE), "PE"))
        ce = splice(ce_export, load_engine_run(src(CE_ENGINE_FILE), "CE"))
    else:
        pe, ce = pe_export, ce_export
    both = pe + ce

    by_day = defaultdict(float)
    n_day = defaultdict(int)
    for t in both:
        by_day[t["date"]] += t["net"]
        n_day[t["date"]] += 1
    daily = sorted(by_day.items())
    series = []
    cum = 0.0
    for d, p in daily:
        cum += p
        series.append([d, round(p), round(cum), n_day[d]])
    by_month = defaultdict(lambda: {"net": 0.0, "n": 0, "w": 0})
    for t in both:
        m = by_month[t["date"][:7]]
        m["net"] += t["net"]
        m["n"] += 1
        m["w"] += 1 if t["net"] > 0 else 0
    months = {k: {"net": round(v["net"], 2), "n": v["n"], "w": v["w"]} for k, v in sorted(by_month.items())}
    dstreak_w, dstreak_l = streaks([p for _, p in daily])

    turnover = sum(t["turnover"] for t in both)
    fees = sum(t["fee"] for t in both)
    net = sum(t["net"] for t in both)
    years = 5.61
    peak_day = peak_day_premium(both)
    sizing = []
    for lots in (1, 2, 3, 4, 6, 8):
        s = resize(both, lots)
        s_net = sum(t["net"] for t in s)
        s_daily = sorted(
            {d: v for d, v in ((d, sum(t["net"] for t in s if t["date"] == d)) for d in {t["date"] for t in s})}.items()
        )
        s_dd, _, _ = drawdown(s_daily)
        s_peak = peak_day_premium(s)
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
                "per_year": round(s_net / years),
                "roi": round(100 * s_net / years / funded),
            }
        )

    slip = []
    for bps in (0, 6, 10, 14, 25, 50, 100):
        n, w = slipped_net(both, bps)
        slip.append({"bps": bps, "net": round(n), "win": w})
    lo, hi = 0.0, 5.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if slipped_net(both, mid * 100)[0] > 0:
            lo = mid
        else:
            hi = mid
    top = sorted(daily, key=lambda kv: -kv[1])[:10]
    bot = sorted(daily, key=lambda kv: kv[1])[:10]

    # The third correction, measured rather than asserted, so the page can
    # interpolate it instead of quoting hand-typed numbers.
    pe_old = load_external(src(PE_TARGET_FILE), "PE", False)
    pe_old_honest = load_external(src(PE_TARGET_FILE), "PE", True)
    overpaid = [t for t, u in zip(pe_old_honest, pe_old) if u["exit"] > t["entry"] + PE_TARGET_RUPEES / t["qty"] + 1e-9]
    pe_published = sum(t["net"] for t in pe_old)

    return {
        "generated": date.today().isoformat(),
        "honest_fill": honest_fill,
        "splice": {
            "from": SPLICE_FROM,
            "pe_export_before": sum(1 for t in pe if t["source"] == "export"),
            "pe_engine_from": sum(1 for t in pe if t["source"] == "engine"),
            "ce_export_before": sum(1 for t in ce if t["source"] == "export"),
            "ce_engine_from": sum(1 for t in ce if t["source"] == "engine"),
            "pe_engine_net": round(sum(t["net"] for t in pe if t["source"] == "engine"), 2),
            "ce_engine_net": round(sum(t["net"] for t in ce if t["source"] == "engine"), 2),
            "pe_export_net_same_window": round(sum(t["net"] for t in pe_export if t["date"] >= SPLICE_FROM), 2),
            "ce_export_net_same_window": round(sum(t["net"] for t in ce_export if t["date"] >= SPLICE_FROM), 2),
            **_seam_days(pe_export, pe, "pe"),
            **_seam_days(ce_export, ce, "ce"),
        },
        "fill_correction": {
            "applied": honest_fill,
            "pe_published": round(pe_published, 2),
            "pe_corrected": round(sum(t["net"] for t in pe_old_honest), 2),
            "pe_no_target": round(sum(t["net"] for t in pe), 2),
            "pe_removed": round(pe_published - sum(t["net"] for t in pe_old_honest), 2),
            "trades_overpaid": len(overpaid),
            "pe_trades": len(pe_old),
            "combined_published": round(pe_published + sum(t["net"] for t in ce), 2),
            "combined_old_honest": round(sum(t["net"] for t in pe_old_honest) + sum(t["net"] for t in ce), 2),
            "ce_exits_on_candle_high_pct": 2,
            "pe_target_exits_on_candle_high_pct": 40,
        },
        "window": {"from": "2021-01-01", "to": max(t["date"] for t in both)},
        "lots": LOTS,
        "capital": {
            "avg": round(sum(t["premium"] for t in both) / len(both)),
            "median": round(sorted(t["premium"] for t in both)[len(both) // 2]),
            "max_trade": round(max(t["premium"] for t in both)),
            "peak_day": round(peak_day),
        },
        "as_exported": {
            "pe": round(sum(t["as_exported"] for t in pe), 2),
            "ce": round(sum(t["as_exported"] for t in ce), 2),
        },
        "headline": {
            "pe": summarise(pe, "PE (5-year, restated)"),
            "ce": summarise(ce, "CE (5-year, restated)"),
            "combined": summarise(both, "PE + CE combined"),
            "upstox_pe": summarise(upe, "PE (Upstox real prices)"),
            "upstox_ce": summarise(uce, "CE (Upstox real prices)"),
            "upstox_combined": summarise(upe + uce, "Upstox combined"),
        },
        "best_worst": {
            "pe": best_worst(pe),
            "ce": best_worst(ce),
            "upstox_pe": best_worst(upe),
            "upstox_ce": best_worst(uce),
        },
        "by_year": {
            "pe": summarise_bucket(pe, 4),
            "ce": summarise_bucket(ce, 4),
            "combined": summarise_bucket(both, 4),
        },
        "by_month": months,
        "by_dow": dow_bucket(both),
        "regime": {"pe": regime_split(pe), "ce": regime_split(ce), "both": regime_split(both)},
        "curve": {
            "combined": [[d, round(c)] for d, _, c, _ in series],
            "pe": curve(pe),
            "ce": curve(ce),
            "upstox": curve(upe + uce),
        },
        "daily": {
            "trading_days": len(daily),
            "green_days": sum(1 for _, p in daily if p > 0),
            "best_day": [top[0][0], round(top[0][1], 2)],
            "worst_day": [bot[0][0], round(bot[0][1], 2)],
            "avg_day": round(net / len(daily)),
            "median_day": round(sorted(p for _, p in daily)[len(daily) // 2]),
            "day_streak_w": dstreak_w,
            "day_streak_l": dstreak_l,
            "months": len(months),
            "green_months": sum(1 for v in months.values() if v["net"] > 0),
            "best_month": max(months.items(), key=lambda kv: kv[1]["net"]),
            "worst_month": min(months.items(), key=lambda kv: kv[1]["net"]),
        },
        "charges": {
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
            "pct_turnover": round(100 * fees / turnover, 3),
            "per_trade": round(fees / len(both)),
        },
        "series": series,
        "sizing": sizing,
        "slip": slip,
        "breakeven_slip_pct": round(lo, 2),
        "best10": [[d, round(p), n_day[d]] for d, p in top],
        "worst10": [[d, round(p), n_day[d]] for d, p in bot],
    }


# Measured on this repo's own engine over the window where real Upstox premiums
# exist, 230 PE trades, lot pinned at 65 so a flat rupee target is not distorted
# by lot history, every row filled at the threshold. Reproduce with:
#   python3 tools/pe_entry_minute_sweep.py --from-date 2024-10-01 --to-date 2026-07-30 \
#     --execution 5 --entry 09:20 --lot-size 65 --target legrupees:10000 pct:15 pct:20 ...
TARGET_SWEEP = [
    {"label": "12%", "net": 67180},
    {"label": "15%", "net": 184647},
    {"label": "live", "net": 220409},
    {"label": "18%", "net": 343743},
    {"label": "20%", "net": 372600},
    {"label": "22%", "net": 407772},
    {"label": "25%", "net": 363079},
    {"label": "30%", "net": 350516},
    {"label": "40%", "net": 438956},
    {"label": "50%", "net": 467310},
    {"label": "none", "net": 496709},
]


def book_stats(trades):
    ordered = sorted(trades, key=lambda t: t["entry_time"])
    by_day = defaultdict(float)
    for t in ordered:
        by_day[t["date"]] += t["net"]
    dd, dd_from, dd_to = drawdown(sorted(by_day.items()))
    net = sum(t["net"] for t in ordered)
    _, loss_run = streaks([t["net"] for t in ordered])
    return {
        "trades": len(ordered),
        "net": round(net),
        "dd": round(abs(dd)),
        "rdd": round(net / abs(dd), 2) if dd else None,
        "win": round(100 * sum(1 for t in ordered if t["net"] > 0) / len(ordered), 1),
        "avg": round(net / len(ordered)),
        "worst": round(min(t["net"] for t in ordered)),
        "streak": loss_run,
        "dd_from": dd_from,
        "dd_to": dd_to,
    }


def summarise_bucket(trades, keylen):
    b = defaultdict(lambda: {"net": 0.0, "n": 0, "w": 0})
    for t in trades:
        v = b[t["date"][:keylen]]
        v["net"] += t["net"]
        v["n"] += 1
        v["w"] += 1 if t["net"] > 0 else 0
    return {k: {"net": round(v["net"], 2), "n": v["n"], "w": v["w"]} for k, v in sorted(b.items())}


def dow_bucket(trades):
    b = defaultdict(lambda: {"net": 0.0, "n": 0, "w": 0})
    for t in trades:
        v = b[datetime.fromisoformat(t["date"]).strftime("%A")]
        v["net"] += t["net"]
        v["n"] += 1
        v["w"] += 1 if t["net"] > 0 else 0
    return {k: {"net": round(v["net"], 2), "n": v["n"], "w": v["w"]} for k, v in sorted(b.items())}


def _guard_current_report(path):
    """The legacy splice builder cannot reproduce the newer ladder dataset."""
    if path.exists():
        current = json.loads(path.read_text())
        if current.get("deployed") or current.get("live_config"):
            raise SystemExit(
                "Refusing to overwrite the ladder/configuration report with the legacy "
                "splice dataset. Rebuild from the matching archived replay and configuration; "
                "the legacy baseline check does not validate the current report."
            )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="compare against the published report_data.json")
    ap.add_argument("--honest-fill", action="store_true", help="cap PE target exits at the target")
    ap.add_argument("--write", action="store_true", help="overwrite report_data.json")
    args = ap.parse_args()
    if args.write:
        _guard_current_report(_HERE / "report_data.json")

    if args.check:
        # Validate the machinery, not today's book: rebuild the OLD target book
        # and require it to reproduce what was published in Aug 2026. If that
        # matches, the loaders, lot rule, fee model and every derived statistic
        # are faithful, and the current book can be trusted too.
        global get_option_contract_lot_size
        _live_lot = get_option_contract_lot_size
        get_option_contract_lot_size = _legacy_nifty_lot
        try:
            data = build(False, PE_TARGET_FILE, spliced=False)
        finally:
            get_option_contract_lot_size = _live_lot
    else:
        data = build(args.honest_fill)
    if args.check:
        # Compare against the ORIGINAL published file, not the live one — once
        # report_data.json has been rewritten it is no longer a baseline, and
        # checking against it would only prove the correction changed something.
        baseline = _HERE / "report_data.json.published-inflated"
        if not baseline.exists():
            print(f"no baseline at {baseline.name} — cannot validate")
            return 1
        old = json.load(open(baseline))
        print(f"rebuilding {PE_TARGET_FILE} to reproduce the Aug-2026 published figures\n")
        checks = [
            ("PE net", old["headline"]["pe"]["net"], data["headline"]["pe"]["net"]),
            ("CE net", old["headline"]["ce"]["net"], data["headline"]["ce"]["net"]),
            ("combined net", old["headline"]["combined"]["net"], data["headline"]["combined"]["net"]),
            ("combined trades", old["headline"]["combined"]["trades"], data["headline"]["combined"]["trades"]),
            ("win rate", old["headline"]["combined"]["win_rate"], data["headline"]["combined"]["win_rate"]),
            (
                "profit factor",
                old["headline"]["combined"]["profit_factor"],
                data["headline"]["combined"]["profit_factor"],
            ),
            ("max drawdown", old["headline"]["combined"]["max_dd"], data["headline"]["combined"]["max_dd"]),
            ("charges total", old["charges"]["total"], data["charges"]["total"]),
            ("turnover", old["charges"]["turnover"], data["charges"]["turnover"]),
            ("peak day capital", old["capital"]["peak_day"], data["capital"]["peak_day"]),
            ("trading days", old["daily"]["trading_days"], data["daily"]["trading_days"]),
            ("green months", old["daily"]["green_months"], data["daily"]["green_months"]),
            ("breakeven slip", old["breakeven_slip_pct"], data["breakeven_slip_pct"]),
            ("sizing 1-lot roi", old["sizing"][0]["roi"], data["sizing"][0]["roi"]),
            ("slip @50bps", old["slip"][5]["net"], data["slip"][5]["net"]),
        ]
        bad = 0
        for name, want, got in checks:
            ok = want == got or (isinstance(want, (int, float)) and abs(want - got) <= max(2, abs(want) * 0.002))
            bad += not ok
            print(f"  {'ok ' if ok else 'DIFF'}  {name:<20} published {want!s:>14}   rebuilt {got!s:>14}")
        print(
            f"\n{'MATCHES' if not bad else str(bad) + ' MISMATCH(ES)'} — "
            f"{'safe to rebuild' if not bad else 'do NOT publish until these agree'}"
        )
        return int(bool(bad))
    if args.write:
        json.dump(data, open(_HERE / "report_data.json", "w"), indent=1)
        print(
            f"wrote report_data.json  honest_fill={args.honest_fill}  "
            f"combined net Rs {data['headline']['combined']['net']:,.0f}"
        )
    else:
        print(json.dumps({k: data[k] for k in ("window", "capital", "charges", "breakeven_slip_pct")}, indent=1))
        print("combined net", data["headline"]["combined"]["net"])


if __name__ == "__main__":
    raise SystemExit(main())
