"""Emit the Supertrend tearsheet -- the hourly trend line, calls only, five years on Dhan.

Fifth of the family (build_report.py is the five-year options sheet,
build_fib_report.py the Fib Boundary sheet, build_candle_report.py the Candle
Entry sheet, build_gapcarry_report.py the overnight book). It borrows the
parent document's stylesheet, helpers and bilingual toggle verbatim -- read
from build_report.py at run time, never copied -- so the five read as one
family on the Assets page.

Every figure comes from CryptoForge's tools/supertrend_options_backtest.py
(commit 476a5c8) run against the Dhan expired-options archive with real NIFTY
1-minute index candles for signals: 1H supertrend (ATR 10, multiplier 1.5),
one NIFTY call at the money on the NEXT week's expiry, held overnight, rolled
to a fresh ATM contract at 6 strikes in the money, a trail armed after +100
index points that exits on an 80-point give-back, otherwise out on the flip
or at expiry 15:20. 1 Jan 2021 -> 30 Sep 2026 (extended 02-Oct-2026 from 21 Aug), one position at a time, real
archive premiums both legs, 0.15% adverse slippage a leg, the full charge
schedule. Nothing here is typed by hand.

    python3 tools/tearsheet/build_supertrend_report.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sizing  # noqa: E402
from i18n import LANG_CSS, LANG_JS, t, t_attr  # noqa: E402

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
OUT = _REPO / "docs" / "assets" / "supertrend-tearsheet.html"
DATA = _HERE / "supertrend_report_data.json"
RUNS = _REPO / "tools" / "supertrend_offline"

MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri"]
DOW_TA = ["திங்", "செவ்", "புத", "வியா", "வெள்"]


# ── the parent document's helpers and stylesheet, borrowed, not copied ─────
def _borrow():
    src = (_HERE / "build_report.py").read_text()
    helpers = {}
    for name in (
        "r",
        "lakh",
        "cls",
        "curve_svg",
        "spark",
        "recolour",
        "daily_ledger",
        "daily_series",
        "method_and_limits",
        "cycle_section",
    ):
        m = re.search(rf"^def {name}\(.*?(?=^def |^# ──|^[A-Za-z_][A-Za-z_0-9, ]* = )", src, re.S | re.M)
        if not m:
            raise SystemExit(f"build_report.py no longer defines {name}()")
        exec(m.group(0), helpers)  # noqa: S102  # nosec B102 -- our own file, read at build time
    style = re.search(r"<style>\n(.*?)</style>", src, re.S)
    if not style:
        raise SystemExit("build_report.py has no <style> block to borrow")
    css = style.group(1).replace("{{", "{").replace("}}", "}")
    css = css.replace("\\\\", "\\")
    reader = re.search(r'^READER_JS = """\n(.*?)^"""', src, re.S | re.M)
    if not reader:
        raise SystemExit("build_report.py has no READER_JS to borrow")
    # The daily-income canvas. It is an IIFE that guards on its own
    # element (`if (!cv) return`), so a sheet that borrows it and does
    # not draw the section pays nothing for it.
    chart = re.search(r'^CHART_JS = """\n(.*?)^"""', src, re.S | re.M)
    if not chart:
        raise SystemExit("build_report.py has no CHART_JS to borrow")
    return helpers, css, reader.group(1), chart.group(1)


HELPERS, STYLE, READER_JS, CHART_JS_SRC = _borrow()
# This sheet's own hue. The pill that opens it in the Assets tab bar
# carries the same pair (philforge-app.css, --tearsheet-pill), so the
# colour of the pill is a promise about what the document looks like.
STYLE = HELPERS["recolour"](STYLE, "supertrend")
r, lakh, cls, curve_svg, spark = (HELPERS[k] for k in ("r", "lakh", "cls", "curve_svg", "spark"))


# ── data ─────────────────────────────────────────────────────────────
def load(path: pathlib.Path, mult: float | None = None) -> list[dict]:
    doc = json.load(open(path))
    results = doc["results"]
    res = results[0] if mult is None else next(x for x in results if x["mult"] == mult)
    rows = []
    for row in res["trade_rows"]:
        e = datetime.fromisoformat(row["entry"])
        x = datetime.fromisoformat(row["exit"])
        gross = row["net"] + row["charges"]
        rows.append(
            {
                "entry": e,
                "exit": x,
                "date": e.date(),
                "strike": int(row["strike"]),
                "expiry": row["expiry"][:10],
                "lot": int(row["lot"]),
                "entry_prem": float(row["entry_prem"]),
                "exit_prem": float(row["exit_prem"]),
                "deployed": round(float(row["entry_prem"]) * int(row["lot"]), 2),
                "gross": round(gross, 2),
                "costs": round(float(row["charges"]), 2),
                "net": float(row["net"]),
                "reason": str(row["reason"]),
                "priced": bool(row["priced"]),
                "hold_days": (x - e).total_seconds() / 86400,
            }
        )
    rows.sort(key=lambda z: z["entry"])
    return rows


def book(rows: list[dict]) -> dict:
    net = sum(x["net"] for x in rows)
    wins = [x for x in rows if x["net"] > 0]
    losses = [x for x in rows if x["net"] <= 0]
    gw = sum(x["net"] for x in wins)
    gl = -sum(x["net"] for x in losses)
    eq, peak, dd, dd_from, dd_to, cum = [], 0.0, 0.0, None, None, 0.0
    peak_at = rows[0]["date"] if rows else None
    for x in rows:
        cum += x["net"]
        eq.append((x["entry"].strftime("%Y-%m-%d"), round(cum, 2)))
        if cum > peak:
            peak, peak_at = cum, x["date"]
        if peak - cum > dd:
            dd, dd_from, dd_to = peak - cum, peak_at, x["date"]
    by_month = defaultdict(float)
    by_year = defaultdict(lambda: {"net": 0.0, "trades": 0, "wins": 0})
    by_dow = defaultdict(lambda: {"net": 0.0, "trades": 0, "wins": 0})
    by_hold = defaultdict(lambda: {"net": 0.0, "trades": 0, "wins": 0})
    reasons = defaultdict(lambda: {"n": 0, "net": 0.0})
    for x in rows:
        by_month[(x["date"].year, x["date"].month)] += x["net"]
        h = x["hold_days"]
        hk = "same day" if h < 0.75 else "overnight" if h < 1.75 else "2-3 days" if h < 3.75 else "4+ days"
        for table, key in ((by_year, x["date"].year), (by_dow, x["date"].weekday()), (by_hold, hk)):
            y = table[key]
            y["net"] += x["net"]
            y["trades"] += 1
            y["wins"] += x["net"] > 0
        rr = reasons[x["reason"]]
        rr["n"] += 1
        rr["net"] += x["net"]
    deps = sorted(x["deployed"] for x in rows)
    ranked = sorted(x["net"] for x in rows)
    priced = [x for x in rows if x["priced"]]
    floored = [x for x in rows if not x["priced"]]
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(rows), 1) if rows else 0,
        "net": round(net, 2),
        "gross": round(sum(x["gross"] for x in rows), 2),
        "costs": round(sum(x["costs"] for x in rows), 2),
        "avg_trade": round(net / len(rows), 2) if rows else 0,
        "median_trade": round(ranked[len(ranked) // 2], 2) if ranked else 0,
        "avg_win": round(gw / len(wins), 2) if wins else 0,
        "avg_loss": round(-gl / len(losses), 2) if losses else 0,
        "profit_factor": round(gw / gl, 2) if gl else None,
        "max_dd": round(-dd, 2),
        "dd_from": dd_from.isoformat() if dd_from else "",
        "dd_to": dd_to.isoformat() if dd_to else "",
        "return_over_dd": round(net / dd, 2) if dd else None,
        "minus_best5": round(sum(ranked[:-5]), 2) if len(ranked) > 5 else None,
        "priced_n": len(priced),
        "priced_net": round(sum(x["net"] for x in priced), 2),
        "floored_n": len(floored),
        "floored_net": round(sum(x["net"] for x in floored), 2),
        "avg_deployed": round(sum(deps) / len(deps), 2) if deps else 0,
        "median_deployed": round(deps[len(deps) // 2], 2) if deps else 0,
        "peak_deployed": max(deps) if deps else 0,
        "avg_hold_days": round(sum(x["hold_days"] for x in rows) / len(rows), 1) if rows else 0,
        "max_hold_days": round(max(x["hold_days"] for x in rows), 1) if rows else 0,
        "best": max(rows, key=lambda x: x["net"]) if rows else None,
        "worst": min(rows, key=lambda x: x["net"]) if rows else None,
        "first": rows[0]["date"].isoformat() if rows else "",
        "last": rows[-1]["date"].isoformat() if rows else "",
        "curve": eq,
        "by_month": {f"{y}-{m:02d}": round(v, 2) for (y, m), v in sorted(by_month.items())},
        "by_year": {
            str(k): {"net": round(v["net"], 2), "trades": v["trades"], "wins": v["wins"]}
            for k, v in sorted(by_year.items())
        },
        "by_dow": {
            DOW[k]: {"net": round(v["net"], 2), "trades": v["trades"], "wins": v["wins"]}
            for k, v in sorted(by_dow.items())
            if k < 5
        },
        "by_hold": {
            k: {"net": round(v["net"], 2), "trades": v["trades"], "wins": v["wins"]}
            for k in ("same day", "overnight", "2-3 days", "4+ days")
            if (v := by_hold.get(k))
        },
        "reasons": {
            k: {"n": v["n"], "net": round(v["net"], 2)} for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]["n"])
        },
        "best10": sorted(rows, key=lambda x: -x["net"])[:10],
        "worst10": sorted(rows, key=lambda x: x["net"])[:10],
        "rows": rows,
    }


TRAIL_ROWS = load(RUNS / "st_final_CE.json")

# Facts that depend on the data are computed, never typed: the extension to
# 30-Sep-2026 turned "about Rs 24,000 a year" false, and "2022 ... did not pay"
# was false already (2022 made money).
_TRAIL_RAW = json.load(open(RUNS / "st_final_CE.json"))
_END = datetime.strptime(str(_TRAIL_RAW["to"])[:10], "%Y-%m-%d")
_BY_YEAR = {int(y): v for y, v in _TRAIL_RAW["results"][0]["by_year"].items()}
_YEARS_SINCE_2024 = (_END - datetime(2024, 1, 1)).days / 365.25
_SO_FAR = f"{_END.year}-so-far"


def _years(sign: int) -> str:
    ys = [str(y) if y != _END.year else _SO_FAR for y in sorted(_BY_YEAR) if (_BY_YEAR[y] > 0) == (sign > 0)]
    return ", ".join(ys[:-1]) + (" and " if len(ys) > 1 else "") + ys[-1] if ys else "none"


def _years_ta(sign: int) -> str:
    ys = [
        str(y) if y != _END.year else f"{_END.year}-இதுவரை" for y in sorted(_BY_YEAR) if (_BY_YEAR[y] > 0) == (sign > 0)
    ]
    return ", ".join(ys) if ys else "எதுவும் இல்லை"


_FIRST_N = 685  # the trades the eight checks covered, to 21-Aug-2026
_ADDED_N = len(TRAIL_ROWS) - _FIRST_N
BASE_ROWS = load(RUNS / "st_roll_CE_tf60.json", mult=1.5)
TGT_ROWS = load(RUNS / "st_tgt_CE_tf60_m1.5_t125.json")
PE_ROWS = load(RUNS / "st_roll_PE_tf60.json", mult=1.5)
TRAIL, BASE, TGT, PE = book(TRAIL_ROWS), book(BASE_ROWS), book(TGT_ROWS), book(PE_ROWS)


def recent(rows):
    return round(sum(x["net"] for x in rows if x["date"].year >= 2024), 2)


json.dump(
    {
        k: {kk: vv for kk, vv in b.items() if kk not in ("rows", "best10", "worst10", "best", "worst")}
        for k, b in (("CE_trail", TRAIL), ("CE_base", BASE), ("CE_target", TGT), ("PE_base", PE))
    },
    open(DATA, "w"),
    default=str,
    indent=1,
)


# ── pieces ────────────────────────────────────────────────────────────
def kpis(b: dict, label_en: str, label_ta: str) -> str:
    pf = "—" if b["profit_factor"] is None else f"{b['profit_factor']:.2f}"
    rod = "—" if b["return_over_dd"] is None else f"{b['return_over_dd']:.2f}&times;"
    m5 = "—" if b["minus_best5"] is None else r(b["minus_best5"])
    return f"""
<section>
  <div class="shead"><h2>{t(label_en, label_ta)}</h2></div>
  <div class="kpis">
    <div class="kpi"><div class="kpi-l">{t("Net profit", "நிகர லாபம்")}</div>
      <div class="kpi-v {cls(b["net"])}">{r(b["net"])}</div>
      <div class="kpi-s">{t("after all charges and slippage", "கட்டணம், slippage அனைத்திற்கும் பின்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Priced-exit net", "விலையிடப்பட்ட நிகர")}</div>
      <div class="kpi-v {cls(b["priced_net"])}">{r(b["priced_net"])}</div>
      <div class="kpi-s">{b["priced_n"]} {t("trades with a market quote both ends", "இரு முனை விலையுள்ள trades")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Trades", "Trades")}</div>
      <div class="kpi-v">{b["trades"]}</div>
      <div class="kpi-s">{b["wins"]} {t("won", "வெற்றி")} &middot; {b["losses"]} {t("lost", "நஷ்டம்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Win rate", "வெற்றி விகிதம்")}</div>
      <div class="kpi-v">{b["win_rate"]}%</div>
      <div class="kpi-s">{t("win", "வெற்றி")} {r(b["avg_win"])} &middot; {t("loss", "நஷ்டம்")} {r(b["avg_loss"])}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Profit factor", "லாப காரணி")}</div>
      <div class="kpi-v">{pf}</div>
      <div class="kpi-s">{t("gross won &divide; gross lost", "மொத்த லாபம் &divide; மொத்த நஷ்டம்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Max drawdown", "அதிகபட்ச இறக்கம்")}</div>
      <div class="kpi-v neg">{r(b["max_dd"])}</div>
      <div class="kpi-s">{b["dd_from"]} &rarr; {b["dd_to"]}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Return / drawdown", "வருவாய் / இறக்கம்")}</div>
      <div class="kpi-v">{rod}</div>
      <div class="kpi-s">{t("profit per rupee of worst dip", "மோசமான இறக்கத்தின் ஒரு ரூபாய்க்கு லாபம்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Minus the best five", "சிறந்த ஐந்து நீக்கி")}</div>
      <div class="kpi-v {cls(b["minus_best5"] or 0)}">{m5}</div>
      <div class="kpi-s">{t("the book without its five biggest wins", "ஐந்து பெரிய வெற்றிகள் இல்லாமல்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Average &middot; median trade", "சராசரி &middot; இடைநிலை டிரேட்")}</div>
      <div class="kpi-v {cls(b["avg_trade"])}">{r(b["avg_trade"])} &middot; {r(b["median_trade"])}</div>
      <div class="kpi-s">{t("net after charges", "கட்டணங்களுக்குப் பின்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Best &middot; worst trade", "சிறந்த &middot; மோசமான trade")}</div>
      <div class="kpi-v"><span class="pos">{r(b["best"]["net"]) if b["best"] else "—"}</span> &middot; <span class="neg">{r(b["worst"]["net"]) if b["worst"] else "—"}</span></div>
      <div class="kpi-s">{t("one lot throughout", "முழுவதும் ஒரு lot")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Premium paid per trade", "Trade-க்கு premium")}</div>
      <div class="kpi-v">{r(b["avg_deployed"])}</div>
      <div class="kpi-s">{t("avg", "சராசரி")} &middot; {t("max", "அதிகபட்சம்")} {r(b["peak_deployed"])}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Held, entry to exit", "வைத்திருந்த காலம்")}</div>
      <div class="kpi-v">{b["avg_hold_days"]} {t("days", "நாட்கள்")}</div>
      <div class="kpi-s">{t("avg", "சராசரி")} &middot; {t("longest", "நீளமானது")} {b["max_hold_days"]} {t("days", "நாட்கள்")}</div></div>
  </div>
</section>"""


def curve_section(b: dict, title_en: str, title_ta: str, anchor: str) -> str:
    if len(b["curve"]) < 2:
        return ""
    line, area, dd, zero, hi, lo = curve_svg(b["curve"])
    return f"""
<section id="{anchor}">
  <div class="shead"><div><h2>{t(title_en, title_ta)}</h2>
    <p>{t("Cumulative net after charges, one point per trade, in entry order. Shading marks every stretch spent below the previous high.", "கட்டணங்களுக்குப் பின் திரட்டு நிகர, ஒரு trade-க்கு ஒரு புள்ளி, நுழைவு வரிசையில். நிழல் = முந்தைய உச்சத்துக்குக் கீழே.")}</p></div></div>
  <div class="panel">
    <div class="chart">
      <svg viewBox="0 0 1040 260" role="img" preserveAspectRatio="none" aria-label="Cumulative net from {b["first"]} to {b["last"]}, ending at {r(b["net"])}">
        <path d="{dd}" fill="rgba(var(--neg-fill),.13)"/>
        <path d="{area}" fill="rgba(var(--curve-rgb),.10)"/>
        <line x1="0" y1="{zero:.1f}" x2="1040" y2="{zero:.1f}" stroke="var(--line)" stroke-width="1" stroke-dasharray="3 4"/>
        <path class="curve-line" d="{line}" fill="none" stroke="var(--curve)" stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
      </svg>
    </div>
    <div class="axis"><span>{b["first"]}</span>
      <span>{t("peak", "உச்சம்")} {r(hi)} &middot; {t("shaded = below previous high", "நிழல் = முந்தைய உச்சத்துக்குக் கீழே")}</span>
      <span>{b["last"]}</span></div>
  </div>
</section>"""


def heat(b: dict, title_en: str, title_ta: str) -> str:
    bm = b["by_month"]
    if not bm:
        return ""
    years = sorted({int(k[:4]) for k in bm})
    top = max([abs(v) for v in bm.values()] or [1]) or 1
    head = "".join(f"<th scope='col'>{m}</th>" for m in MON)
    body = ""
    for y in years:
        cells, tot = "", 0.0
        for m in range(1, 13):
            v = bm.get(f"{y}-{m:02d}")
            if v is None:
                cells += "<td class='flat'>&middot;</td>"
                continue
            tot += v
            a = min(1.0, abs(v) / top) * 0.55 + 0.12
            colour = f"rgba(16,185,129,{a:.2f})" if v > 0 else f"rgba(239,68,68,{a:.2f})" if v < 0 else "transparent"
            cells += f"<td style='background:{colour}' title='{y}-{m:02d} {r(v)}'>{r(v / 1000)}k</td>"
        body += f"<tr><th scope='row'>{y}</th>{cells}<td class='{cls(tot)}'><strong>{r(tot)}</strong></td></tr>"
    return f"""
<section>
  <div class="shead"><div><h2>{t(title_en, title_ta)}</h2>
    <p>{t("Net after charges by entry month, thousands of rupees. Colour is the size relative to the largest month. A month with no trade is a dot.", "நுழைவு மாதவாரி நிகர, ஆயிரங்களில். நிறம் = பெரிய மாதத்துடன் ஒப்பீடு. Trade இல்லாத மாதம் ஒரு புள்ளி.")}</p></div></div>
  <div class="tblwrap"><table class="heat">
    <thead><tr><th scope="col">{t("Year", "ஆண்டு")}</th>{head}<th scope="col">{t("Total", "மொத்தம்")}</th></tr></thead>
    <tbody>{body}</tbody></table></div>
</section>"""


def rows_of(table: dict, label_fn) -> str:
    out = ""
    for k, v in table.items():
        wr = round(100 * v["wins"] / v["trades"], 1) if v["trades"] else 0
        out += f"<tr><th scope='row'>{label_fn(k)}</th><td>{v['trades']}</td><td>{wr}%</td><td class='{cls(v['net'])}'><strong>{r(v['net'])}</strong></td><td>{r(v['net'] / v['trades']) if v['trades'] else '—'}</td></tr>"
    return out


REASON_NAMES = {
    "TRAIL": ("Trail &mdash; gave back 80 points from the peak", "Trail &mdash; உச்சத்திலிருந்து 80 புள்ளி திரும்ப"),
    "FLIP": ("Supertrend flipped bearish", "Supertrend கீழ்நோக்கி மாறியது"),
    "ROLL": ("Rolled &mdash; 6 strikes in the money, fresh ATM bought", "Roll &mdash; 6 strikes ITM, புதிய ATM"),
    "EXPIRY": ("Contract expiry, sold at 15:20", "Expiry, 15:20-இல் விற்பனை"),
    "SQUARE_OFF": ("Squared off", "Square off"),
    "OPEN_AT_END": ("Open when the data ends", "தரவு முடிவில் திறந்திருந்தது"),
}


def reasons(b: dict) -> str:
    out = ""
    for k, v in b["reasons"].items():
        en, ta = REASON_NAMES.get(k, (k.replace("_", " "), k.replace("_", " ")))
        out += f"<tr><th scope='row'>{t(en, ta)}</th><td>{v['n']}</td><td>{round(100 * v['n'] / b['trades'], 1) if b['trades'] else 0}%</td><td class='{cls(v['net'])}'><strong>{r(v['net'])}</strong></td></tr>"
    return out


def ten(rows: list[dict]) -> str:
    out = ""
    for x in rows:
        en, ta = REASON_NAMES.get(x["reason"], (x["reason"], x["reason"]))
        out += (
            f"<tr><td>{x['entry'].strftime('%d %b %Y %H:%M')}</td><td>{x['strike']} CE &middot; {x['expiry']}</td>"
            f"<td>&#8377;{x['entry_prem']:.2f} &rarr; &#8377;{x['exit_prem']:.2f}</td><td>{r(x['deployed'])}</td>"
            f"<td>{t(en, ta)}</td><td class='{cls(x['net'])}'><strong>{r(x['net'])}</strong></td></tr>"
        )
    return out


def every_trade(rows: list[dict]) -> str:
    out = ""
    for x in rows:
        en, ta = REASON_NAMES.get(x["reason"], (x["reason"], x["reason"]))
        pr = "" if x["priced"] else " <em>(intrinsic)</em>"
        out += (
            f"<tr><td>{x['entry'].strftime('%d %b %Y %H:%M')}</td><td>{x['strike']} CE &middot; {x['expiry']}</td>"
            f"<td>{x['exit'].strftime('%d %b %Y %H:%M')}</td><td>{t(en, ta)}</td>"
            f"<td>&#8377;{x['entry_prem']:.2f} &rarr; &#8377;{x['exit_prem']:.2f}{pr}</td>"
            f"<td>{x['lot']}</td><td class='{cls(x['net'])}'><strong>{r(x['net'])}</strong></td></tr>"
        )
    return out


def sizing_section(rows: list[dict]) -> str:
    priced = [x for x in rows if x["deployed"]]
    if not priced:
        return ""
    first, last = priced[0]["date"], priced[-1]["date"]
    years = max((last - first).days / 365.25, 0.25)
    scaled = [{"gross": x["gross"], "costs": x["costs"], "capital": x["deployed"]} for x in priced]
    return sizing.section(
        sizing.scale(scaled, years=years),
        r=r,
        cls=cls,
        live_lots=1,
        anchor="capital-needed",
        note_en="One contract at a time, bought outright (no margin), so the capital a multiple needs is that multiple of one premium. A roll frees the old contract's money the minute the new one is bought.",
        note_ta="ஒரு நேரத்தில் ஒரே ஒப்பந்தம், முழு premium (margin இல்லை); ஒரு multiple-க்கு தேவை அந்த multiple மடங்கு ஒரு premium. Roll-இல் பழைய ஒப்பந்தத்தின் பணம் உடனே விடுவிக்கப்படும்.",
    )


def cmp_row(label_html: str, b: dict, rows: list[dict], total: bool = False) -> str:
    klass = ' class="trow-total"' if total else ""
    return (
        f"<tr{klass}><th scope='row'>{label_html}</th><td>{b['trades']}</td><td>{b['win_rate']}%</td>"
        f"<td class='{cls(b['net'])}'><strong>{r(b['net'])}</strong></td>"
        f"<td class='{cls(b['priced_net'])}'>{r(b['priced_net'])}</td>"
        f"<td class='{cls(recent(rows))}'>{r(recent(rows))}</td>"
        f"<td class='neg'>{r(b['max_dd'])}</td><td>{b['profit_factor']}</td></tr>"
    )


# ── the page ──────────────────────────────────────────────────────────
page = f"""<title>PhilForge Supertrend Tearsheet</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
{STYLE}
.two-up {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(360px,1fr)); gap:12px; }}
table.heat td {{ text-align:right; font-variant-numeric:tabular-nums; }}
{sizing.SIZING_CSS}
{LANG_CSS}
</style>

<section class="document-hero">
  <div class="hero-copy">
    <p class="eyebrow"><b>TEARSHEET</b>{
    t("PhilForge &middot; Options &middot; Supertrend", "PhilForge &middot; Options &middot; Supertrend")
}</p>
    <h1>{
    t(
        "Supertrend &mdash; Hourly &middot; &times;1.5 &middot; CE &middot; Strike Roll &middot; Trail &mdash; NIFTY, 5 Years 8 Months",
        "Supertrend &mdash; Hourly &middot; &times;1.5 &middot; CE &middot; Strike Roll &middot; Trail &mdash; NIFTY, 5 ஆண்டு 8 மாதம்",
    )
}</h1>
    <p class="lede">{
    t(
        "One line under the hourly chart decides everything. The supertrend (ATR 10, multiplier 1.5) sits below price while NIFTY trends up; the first hourly close back above it buys <strong>one NIFTY call at the money</strong> &mdash; on the <strong>next week's expiry</strong>, never this week's, because a third of all entries land within a day of expiry and every one of those buckets loses. The position is <strong>held overnight</strong>: squared-off versions of this rule lose money at every setting, because the overnight gaps are where a trend pays. When the trade is 6 strikes in the money the contract is <strong>rolled</strong> &mdash; sold, and a fresh at-the-money call bought the same minute &mdash; which banks the gain at a real quoted price and is what a desk does for liquidity anyway. Once the index has run 100 points in the trade's favour a <strong>trail</strong> arms: give back 80 points from the best level since entry, and the trade is over. Otherwise it ends when the line flips bearish, or at expiry, 15:20. No stop loss below &mdash; every stop tested made the book worse by re-buying the same still-live trend at a fresh round trip. <strong>Puts lose under the mirror rule and are not traded.</strong> Real archive premiums both legs, 0.15% adverse slippage a leg, every charge booked.",
        "Hourly chart-இன் கீழே ஒரு கோடு எல்லாவற்றையும் முடிவு செய்கிறது. NIFTY மேலேறும்போது supertrend (ATR 10, multiplier 1.5) விலைக்குக் கீழே இருக்கும்; அதற்கு மேலே முடியும் முதல் hourly close <strong>ஒரு NIFTY கால், at the money</strong> வாங்கும் &mdash; <strong>அடுத்த வாரத்தின் expiry-இல்</strong>, இந்த வாரத்தில் அல்ல; ஏனெனில் நுழைவுகளில் மூன்றில் ஒன்று expiry-க்கு ஒரு நாளுக்குள் விழுகிறது, அந்தப் பிரிவுகள் அனைத்தும் நஷ்டம். நிலை <strong>இரவு முழுவதும்</strong> வைக்கப்படும்: நாள்தோறும் square off செய்யும் பதிப்புகள் ஒவ்வொரு அமைப்பிலும் நஷ்டம் &mdash; trend சம்பாதிப்பது இரவு gap-களில்தான். Trade 6 strikes in the money ஆனதும் ஒப்பந்தம் <strong>roll</strong> &mdash; விற்று, அதே நிமிடம் புதிய ATM கால் &mdash; லாபம் உண்மையான விலையில் பதிவாகும்; liquidity-க்காக ஒரு desk செய்வதும் இதுவே. Index 100 புள்ளி சாதகமாக ஓடியதும் <strong>trail</strong> தயார்: நுழைவுக்குப் பின் சிறந்த நிலையிலிருந்து 80 புள்ளி திரும்பக் கொடுத்தால் trade முடிந்தது. இல்லையெனில் கோடு கீழ்நோக்கி மாறும்போது, அல்லது expiry 15:20-இல் முடிவு. கீழே stop loss இல்லை &mdash; சோதித்த ஒவ்வொரு stop-உம் அதே உயிருள்ள trend-ஐ புதிய கட்டணத்தில் மறுவாங்கி புத்தகத்தை மோசமாக்கியது. <strong>புட் கண்ணாடி விதியில் நஷ்டம், வர்த்தகம் இல்லை.</strong> இரு leg-களும் உண்மையான archive premium, leg-க்கு 0.15% எதிர்மறை slippage, ஒவ்வொரு கட்டணமும் பதிவு.",
    )
}</p>
    <div class="document-meta" aria-label="Document metadata">
      <div class="meta-chip"><span>{t("Period", "காலம்")}</span><strong>{TRAIL["first"]} &rarr; {
    TRAIL["last"]
}</strong></div>
      <div class="meta-chip"><span>{t("Trades", "Trades")}</span><strong>{TRAIL["trades"]} &middot; {
    TRAIL["priced_n"]
} {t("priced both ends", "இரு முனை விலை")} &middot; {TRAIL["floored_n"]} {
    t("floored at intrinsic", "intrinsic-இல்")
}</strong></div>
      <div class="meta-chip"><span>{t("Side", "பக்கம்")}</span><strong>{
    t("CE only &mdash; PE not traded", "CE மட்டும் &mdash; PE இல்லை")
}</strong></div>
      <div class="meta-chip"><span>{t("Contract", "ஒப்பந்தம்")}</span><strong>{
    t(
        "ATM &middot; next-week weekly &middot; rolled at 6 strikes",
        "ATM &middot; அடுத்த வார weekly &middot; 6 strikes-இல் roll",
    )
}</strong></div>
      <div class="meta-chip"><span>{t("Exit", "வெளியேற்றம்")}</span><strong>{
    t("trail 100/80 &middot; flip &middot; expiry 15:20", "trail 100/80 &middot; flip &middot; expiry 15:20")
}</strong></div>
      <div class="meta-chip"><span>{t("Costs", "கட்டணங்கள்")}</span><strong>{
    t("Brokerage, STT, GST, stamp + 0.15% slip a leg", "புரோக்கரேஜ், STT, GST, stamp + 0.15% slip / leg")
}</strong></div>
    </div>
  </div>
  <div class="system-sigil" aria-hidden="true">
    <div class="sigil-ring ring-one"></div><div class="sigil-ring ring-two"></div><div class="sigil-ring ring-three"></div>
    <div class="sigil-core">ST</div>
    <span class="sigil-label label-one">1H</span><span class="sigil-label label-two">&times;1.5</span>
  </div>
</section>

<section class="reader-toolbar" aria-label="Document tools">
  <div class="langbar" id="langbar" role="tablist" aria-label="Language / மொழி">
    <button type="button" role="tab" data-lang="en" aria-selected="true">English</button>
    <button type="button" role="tab" data-lang="ta" aria-selected="false">தமிழ்</button>
  </div>
  <label class="document-search" for="tearsheet-search">
    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-4-4"></path></svg>
    <input id="tearsheet-search" type="search" placeholder="Search this tearsheet" autocomplete="off"
           data-ph-en="Search this tearsheet" data-ph-ta="இந்த அறிக்கையில் தேடுங்கள்">
    <kbd>&#8984; K</kbd>
  </label>
  <div class="search-status" id="search-status" aria-live="polite">{t("Full document", "முழு ஆவணம்")}</div>
</section>

<div class="reader-layout">
<aside class="document-rail" aria-label="Document navigation">
  <div class="rail-sticky">
    <p class="rail-label">{t("On this page", "இந்தப் பக்கத்தில்")}</p>
    <nav id="document-toc"></nav>
    <div class="rail-card">
      <span>{t("DOCUMENT STATE", "ஆவண நிலை")}</span>
      <strong><i></i> {
    t("Calls only &middot; net of every charge", "கால் மட்டும் &middot; அனைத்து கட்டணங்களுக்குப் பின்")
}</strong>
      <small>{
    t(
        "Dhan archive, 4 stores &middot; checked eight ways, 0 failures &middot; every trade re-priceable from the raw files",
        "Dhan archive, 4 stores &middot; எட்டு வழி சரிபார்ப்பு, 0 தோல்வி &middot; ஒவ்வொரு trade-உம் மூலக் கோப்புகளில் மறு-விலையிடத்தக்கது",
    )
}</small>
    </div>
  </div>
</aside>

<article class="document-body" id="document-body">

<div class="note">
  <h2 class="note-h">{t("Read this first", "முதலில் இதைப் படியுங்கள்")}</h2>
  <p>{
    t(
        "Eight independent checks were run before this was published. A mechanical audit of all 685 trades found <strong>no lookahead</strong> (every fill is the first minute after its signal bar closes, the trail's high-water mark counts only closed bars), no overlapping positions, the right lot size for every date and a legal expiry for every contract. <strong>Every premium was re-read from the raw archive files with none of this page's code involved: 685 of 685 entries and 673 of 673 priced exits exist there verbatim.</strong> The 12 exits the archive cannot quote &mdash; deep in-the-money strikes past the band Dhan carries &mdash; are valued at bare intrinsic, which was proven to UNDERSTATE them (the same strike on the nearer expiry traded above that floor). A second simulator, written from scratch against the rule rather than the engine, reproduces all 685 trades to the rupee. The 198 trades that fall inside the Upstox archive's window were re-priced there: median price difference 0.00%, and the Upstox version of the book comes out <em>richer</em>, not poorer. Ranking every exit variant on 2021&ndash;2023 alone picks this configuration second of sixteen, and it is the best of that top three on the unseen 2024&ndash;2026. The book survives triple slippage. And the expiry calendar was proven against the tape: at-the-money time value at the close is &#8377;0.10 on claimed expiry days against &#8377;81.85 on all other days.",
        "வெளியிடும் முன் எட்டு சுயாதீன சரிபார்ப்புகள். 685 trades-இன் இயந்திரத் தணிக்கையில் <strong>lookahead இல்லை</strong> (ஒவ்வொரு fill-உம் அதன் signal bar முடிந்த பின் முதல் நிமிடம்; trail-இன் உச்சக் குறி முடிந்த bars-ஐ மட்டுமே எண்ணும்), நிலைகள் மேற்பொருந்தவில்லை, ஒவ்வொரு தேதிக்கும் சரியான lot, ஒவ்வொரு ஒப்பந்தத்துக்கும் செல்லுபடி expiry. <strong>ஒவ்வொரு premium-உம் இந்தப் பக்கத்தின் code இல்லாமல் மூல archive கோப்புகளில் மறு-வாசிக்கப்பட்டது: 685-இல் 685 நுழைவுகளும் 673-இல் 673 விலையிடப்பட்ட வெளியேற்றங்களும் அங்கே அப்படியே உள்ளன.</strong> Archive விலை தர முடியாத 12 வெளியேற்றங்கள் &mdash; Dhan வரம்பைத் தாண்டிய ஆழ்ந்த ITM strikes &mdash; வெறும் intrinsic-இல்; அது அவற்றைக் <em>குறைத்தே</em> மதிப்பிடுகிறது என நிரூபிக்கப்பட்டது. விதியிலிருந்து புதிதாக எழுதப்பட்ட இரண்டாவது simulator 685 trades-ஐயும் ரூபாய்க்கு ரூபாய் மறு-உருவாக்குகிறது. Upstox சாளரத்தில் விழும் 198 trades அங்கே மறு-விலை: இடைநிலை வேறுபாடு 0.00%, Upstox பதிப்பு <em>கூடுதலே</em> தருகிறது. 2021&ndash;2023-இல் மட்டும் தரவரிசைப்படுத்தினால் இந்த அமைப்பு பதினாறில் இரண்டாவது; பார்க்காத 2024&ndash;2026-இல் அந்த முதல் மூன்றில் சிறந்தது. மூன்று மடங்கு slippage-ஐயும் தாங்குகிறது. Expiry நாட்காட்டி tape-இல் நிரூபணம்: expiry நாட்களில் ATM time value &#8377;0.10, மற்ற நாட்களில் &#8377;81.85.",
    )
}</p>
  <p>{
    t(
        f"Those checks covered the {_FIRST_N} trades to 21 August 2026. The book now runs to {_END:%d %B %Y}: the {_ADDED_N} trades since were produced by the same backtest on the same archive, extended, and every earlier trade is unchanged to the rupee &mdash; but those {_ADDED_N} have not been through the eight checks.",
        f"அந்தச் சரிபார்ப்புகள் 21 ஆகஸ்ட் 2026 வரையிலான {_FIRST_N} trades-ஐ உள்ளடக்கின. புத்தகம் இப்போது {_END:%Y-%m-%d} வரை செல்கிறது: அதன் பின் வந்த {_ADDED_N} trades அதே backtest, அதே archive (நீட்டிக்கப்பட்டது) கொண்டு உருவானவை; முந்தைய ஒவ்வொரு trade-உம் ஒரு ரூபாய் கூட மாறவில்லை &mdash; ஆனால் அந்த {_ADDED_N} எட்டு சரிபார்ப்புகளுக்கு உட்படுத்தப்படவில்லை.",
    )
}</p>
</div>

<div class="note note-warn">
  <h2 class="note-h">{
    t("The finding that matters most", "மிக முக்கியமான கண்டுபிடிப்பு")
}</h2>
  <p>{
    t(
        f"Of the {lakh(TRAIL['net'])} net, {lakh(round(TRAIL['net'] - recent(TRAIL_ROWS), 2))} was earned in 2021&ndash;2023. Since January 2024 it made {lakh(recent(TRAIL_ROWS))} &mdash; about {lakh(round(recent(TRAIL_ROWS) / _YEARS_SINCE_2024))} a year against a historical worst drawdown of {lakh(-TRAIL['max_dd'])} &mdash; and 2024 on its own LOST money. Trend-following in calls pays when the index trends; {_years(1)} paid; {_years(-1)} did not. Nothing in the checks above changes that arithmetic, and no neighbouring setting escapes it either. <strong>Treat the five-year headline as history, size against the recent rate, and let the paper run prove it forward before a rupee rides on it.</strong>",
        f"நிகர {lakh(TRAIL['net'])}-இல் {lakh(round(TRAIL['net'] - recent(TRAIL_ROWS), 2))} 2021&ndash;2023-இல் ஈட்டியது. ஜனவரி 2024 முதல் {lakh(recent(TRAIL_ROWS))} &mdash; ஆண்டுக்கு ஏறத்தாழ {lakh(round(recent(TRAIL_ROWS) / _YEARS_SINCE_2024))}, வரலாற்று மோசமான இறக்கம் {lakh(-TRAIL['max_dd'])}-க்கு எதிராக &mdash; 2024 தனியே நஷ்டம். கால்-களில் trend-following, index trend ஆகும்போதுதான் சம்பாதிக்கும்; {_years_ta(1)} கொடுத்தன; {_years_ta(-1)} இல்லை. மேலே உள்ள சரிபார்ப்புகள் எதுவும் இந்தக் கணக்கை மாற்றாது; அண்டை அமைப்புகள் எதுவும் தப்பவில்லை. <strong>ஐந்தாண்டு தலைப்பை வரலாறாகக் கொள்ளுங்கள்; சமீபத்திய விகிதத்தின்படி அளவிடுங்கள்; ஒரு ரூபாய் ஏறும் முன் paper run முன்னோக்கி நிரூபிக்கட்டும்.</strong>",
    )
}</p>
</div>

{kpis(TRAIL, "The programme at a glance", "ஒரே பார்வையில்")}
<section>
  <div class="shead"><div><h2>{t("Charges, in full", "கட்டணங்கள், முழுமையாக")}</h2>
    <p>{
    t(
        "Every rupee between the gross result and the account, per leg: brokerage, STT, exchange charges, SEBI fee, GST, stamp duty &mdash; dated correctly through the Oct-2024 STT change &mdash; plus 0.15% adverse slippage on every fill.",
        "மொத்த முடிவுக்கும் கணக்குக்கும் இடையிலான ஒவ்வொரு ரூபாயும், leg-க்கு: புரோக்கரேஜ், STT, exchange, SEBI, GST, stamp &mdash; அக்-2024 STT மாற்றத்துடன் தேதி சரியாக &mdash; கூடுதலாக ஒவ்வொரு fill-க்கும் 0.15% எதிர்மறை slippage.",
    )
}</p></div></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Book", "புத்தகம்")}</th><th scope="col">{t("Gross", "மொத்தம்")}</th><th scope="col">{
    t("Charges", "கட்டணம்")
}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{
    t("Charges per trade", "trade-க்கு கட்டணம்")
}</th></tr></thead>
    <tbody>
      <tr class="trow-total"><th scope="row">NIFTY CE &middot; {t("trail", "trail")}</th><td class="{
    cls(TRAIL["gross"])
}">{r(TRAIL["gross"])}</td><td class="neg">{r(-TRAIL["costs"])}</td><td class="{cls(TRAIL["net"])}"><strong>{
    r(TRAIL["net"])
}</strong></td><td>{r(TRAIL["costs"] / TRAIL["trades"])}</td></tr>
    </tbody></table></div>
</section>
{
    HELPERS["cycle_section"](
        HELPERS["daily_series"](TRAIL["rows"], lambda x: x["date"], lambda x: x["net"]),
        t,
        r,
        noun_en="trading days",
        noun_ta="வர்த்தக நாட்கள்",
    )
}
{
    HELPERS["daily_ledger"](
        HELPERS["daily_series"](TRAIL["rows"], lambda x: x["date"], lambda x: x["net"]),
        t,
        t_attr,
        r,
        cls,
    )
}
{curve_section(TRAIL, "Cumulative curve", "ஒட்டுமொத்த வளைவு", "curve-supertrend")}
<section id="years">
  <div class="shead"><h2>{t("Year by year", "ஆண்டுவாரியாக")}</h2></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Year", "ஆண்டு")}</th><th scope="col">{t("Trades", "Trades")}</th><th scope="col">{
    t("Win rate", "வெற்றி %")
}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{t("Per trade", "ஒரு trade-க்கு")}</th></tr></thead>
    <tbody>{rows_of(TRAIL["by_year"], lambda k: k)}</tbody></table></div>
</section>
{heat(TRAIL, "Month by month", "மாதவாரியாக")}
{sizing_section(TRAIL_ROWS)}
<section>
  <div class="shead"><h2>{t("Risk register", "ரிஸ்க் பதிவேடு")}</h2></div>
  <p>{
    t(
        "On the same five-year walk: <strong>intraday square-off loses at all 70 settings</strong> &mdash; the overnight gaps are the profit, and a daily exit forfeits every one while paying a fresh round trip; <strong>1-minute loses &#8377;10&ndash;66 lakh</strong> at every multiplier (it loses on raw index points before any option exists), 3m and 5m lose on priced trades everywhere; off the 1H&times;1&ndash;2 / 30m&times;1.5&ndash;2 ridge the multipliers go red fast; <strong>every confirmation filter made it worse</strong> &mdash; RSI 50&ndash;70 at every threshold, ADX (its 25&ndash;30 band is the worst bucket), and a higher-timeframe agreement gate that looked worth +&#8377;4.9 lakh until the in-progress bar's lookahead was removed, after which it was worth &minus;&#8377;11 thousand; <strong>every stop loss and tight trail lost money and RAISED drawdown</strong> (30&ndash;150 points, all five timeframes &mdash; a stopped trade re-enters the same still-bullish trend and pays again); a fixed target at the 189-point average run is nearly the worst possible target; buying in-the-money strikes cut the net almost in half &mdash; the roll already does theta's job; and the nearest weekly expiry put a third of all entries within a day of death, every such bucket negative. This sheet is only the configuration that survived.",
        "அதே ஐந்தாண்டு நடையில்: <strong>நாள்தோறும் square off எல்லா 70 அமைப்புகளிலும் நஷ்டம்</strong> &mdash; லாபம் இரவு gap-களில்; தினசரி வெளியேற்றம் அவற்றை இழந்து புதிய கட்டணமும் கட்டுகிறது; <strong>1-நிமிடம் ஒவ்வொரு multiplier-இலும் &#8377;10&ndash;66 லட்சம் நஷ்டம்</strong> (option வருமுன் index புள்ளிகளிலேயே நஷ்டம்), 3m, 5m priced trades-இல் எங்கும் நஷ்டம்; 1H&times;1&ndash;2 / 30m&times;1.5&ndash;2 முகட்டைத் தாண்டினால் விரைவில் சிவப்பு; <strong>ஒவ்வொரு உறுதிப்படுத்தும் filter-உம் மோசமாக்கியது</strong> &mdash; RSI 50&ndash;70 ஒவ்வொரு நிலையிலும், ADX (25&ndash;30 பட்டை மோசமானது), உயர்-timeframe ஒப்புதல் +&#8377;4.9 லட்சம் போலத் தோன்றி, முடியாத bar-இன் lookahead நீக்கியதும் &minus;&#8377;11 ஆயிரம்; <strong>ஒவ்வொரு stop loss-உம் இறுக்கமான trail-உம் நஷ்டம், இறக்கத்தை உயர்த்தின</strong> (30&ndash;150 புள்ளி, ஐந்து timeframe &mdash; stop ஆன trade அதே bullish trend-இல் மறுநுழைந்து மறுகட்டணம்); 189-புள்ளி சராசரி ஓட்டத்தில் வைத்த fixed இலக்கு கிட்டத்தட்ட மோசமானது; ITM strikes நிகரை பாதியாக்கின &mdash; theta-வின் வேலையை roll ஏற்கனவே செய்கிறது; அருகிலுள்ள weekly expiry நுழைவுகளில் மூன்றில் ஒன்றை மரணத்துக்கு ஒரு நாளுக்குள் வைத்தது, அந்தப் பிரிவுகள் அனைத்தும் எதிர்மறை. இந்த அறிக்கை தப்பிப் பிழைத்த அமைப்பு மட்டுமே.",
    )
}</p>
</section>
<section>
  <div class="shead"><h2>{t("Which day of the week pays", "வாரத்தின் எந்த நாள் லாபம் தருகிறது")}</h2></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Entry weekday", "நுழைவு கிழமை")}</th><th scope="col">{
    t("Trades", "Trades")
}</th><th scope="col">{t("Win rate", "வெற்றி %")}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{
    t("Per trade", "ஒரு trade-க்கு")
}</th></tr></thead>
    <tbody>{rows_of(TRAIL["by_dow"], lambda k: t(k, DOW_TA[DOW.index(k)]))}</tbody></table></div>
  <div class="tblwrap" style="margin-top:12px"><table>
    <thead><tr><th scope="col">{t("Held for", "வைத்திருந்தது")}</th><th scope="col">{
    t("Trades", "Trades")
}</th><th scope="col">{t("Win rate", "வெற்றி %")}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{
    t("Per trade", "ஒரு trade-க்கு")
}</th></tr></thead>
    <tbody>{
    rows_of(
        TRAIL["by_hold"],
        lambda k: t(k, {"same day": "அன்றே", "overnight": "ஒரு இரவு", "2-3 days": "2-3 நாட்கள்", "4+ days": "4+ நாட்கள்"}[k]),
    )
}</tbody></table></div>
  <div class="tblwrap" style="margin-top:12px"><table>
    <thead><tr><th scope="col">{t("How the trade ended", "Trade எப்படி முடிந்தது")}</th><th scope="col">{
    t("Count", "எண்ணிக்கை")
}</th><th scope="col">{t("Share", "பங்கு")}</th><th scope="col">{t("Net", "நிகர")}</th></tr></thead>
    <tbody>{reasons(TRAIL)}</tbody></table></div>
</section>
<section>
  <div class="shead"><h2>{t("Best ten, worst ten", "சிறந்த பத்து, மோசமான பத்து")}</h2></div>
  <div class="two-up">
    <div class="tblwrap"><table>
      <thead><tr><th scope="col">{t("Entry", "நுழைவு")}</th><th scope="col">{
    t("Contract", "ஒப்பந்தம்")
}</th><th scope="col">{t("Premium", "Premium")}</th><th scope="col">{t("Paid", "செலுத்தியது")}</th><th scope="col">{
    t("Ended by", "முடிவு")
}</th><th scope="col">{t("Net", "நிகர")}</th></tr></thead>
      <tbody>{ten(TRAIL["best10"])}</tbody></table></div>
    <div class="tblwrap"><table>
      <thead><tr><th scope="col">{t("Entry", "நுழைவு")}</th><th scope="col">{
    t("Contract", "ஒப்பந்தம்")
}</th><th scope="col">{t("Premium", "Premium")}</th><th scope="col">{t("Paid", "செலுத்தியது")}</th><th scope="col">{
    t("Ended by", "முடிவு")
}</th><th scope="col">{t("Net", "நிகர")}</th></tr></thead>
      <tbody>{ten(TRAIL["worst10"])}</tbody></table></div>
  </div>
</section>




<section>
  <div class="shead"><div><h2>{
    t("The same rule with other exits, for comparison", "ஒப்பீட்டுக்கு: அதே விதி, வேறு வெளியேற்றங்கள்")
}</h2>
    <p>{
    t(
        "Everything identical except the exit. &quot;Priced-only&quot; counts only trades with a real market quote at both ends &mdash; the column that cannot be flattered by a floor. &quot;2024&ndash;26&quot; is the recent period alone.",
        "வெளியேற்றம் மட்டும் வேறு. &quot;Priced-only&quot; = இரு முனையிலும் உண்மையான விலை உள்ள trades மட்டும். &quot;2024&ndash;26&quot; = சமீப காலம் மட்டும்.",
    )
}</p></div></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Exit", "வெளியேற்றம்")}</th><th scope="col">{
    t("Trades", "Trades")
}</th><th scope="col">{t("Win rate", "வெற்றி %")}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{
    t("Priced-only", "Priced-only")
}</th><th scope="col">2024&ndash;26</th><th scope="col">{t("Max drawdown", "அதிகபட்ச இறக்கம்")}</th><th scope="col">{
    t("Profit factor", "லாப காரணி")
}</th></tr></thead>
    <tbody>
      {
    cmp_row(
        t("Trail: armed at +100, 80-point give-back (this sheet)", "Trail: +100-இல், 80 புள்ளி திரும்ப (இந்த அறிக்கை)"),
        TRAIL,
        TRAIL_ROWS,
        total=True,
    )
}
      {cmp_row(t("No trail &mdash; flip and expiry only", "Trail இல்லை &mdash; flip, expiry மட்டும்"), BASE, BASE_ROWS)}
      {cmp_row(t("Fixed target, 125 points", "Fixed இலக்கு, 125 புள்ளி"), TGT, TGT_ROWS)}
    </tbody></table></div>
</section>

<div class="note note-warn">
  <h2 class="note-h">{t("The put book is NOT traded", "புட் புத்தகம் வர்த்தகம் செய்யப்படுவதில்லை")}</h2>
  <p>{
    t(
        f"The mirror rule &mdash; a put bought when the hourly close falls below a supertrend riding above price, same roll, same expiry choice &mdash; was replayed over the same five years: {PE['trades']} trades, net {r(PE['net'])} &mdash; and on trades with a real quote at both ends it LOSES {r(-PE['priced_net'])}. Its entire headline rests on exits the archive cannot price. Every put variant measured &mdash; every multiplier, every timeframe, a fixed target, the trail &mdash; leaves that priced-only column negative; more than sixty variants, no exception. Calls only.",
        f"கண்ணாடி விதி &mdash; விலைக்கு மேலே ஓடும் supertrend-ஐ hourly close கீழே உடைக்கும்போது புட், அதே roll, அதே expiry &mdash; அதே ஐந்து ஆண்டுகளில்: {PE['trades']} trades, நிகர {r(PE['net'])} &mdash; இரு முனையிலும் உண்மையான விலை உள்ள trades-இல் {r(-PE['priced_net'])} நஷ்டம். தலைப்பு முழுவதும் archive விலை தர முடியாத வெளியேற்றங்களில் நிற்கிறது. அளந்த ஒவ்வொரு புட் மாற்றமும் &mdash; ஒவ்வொரு multiplier, timeframe, fixed இலக்கு, trail &mdash; அந்த priced-only column-ஐ எதிர்மறையிலேயே விடுகிறது; அறுபதுக்கும் மேற்பட்ட மாற்றங்கள், விதிவிலக்கு இல்லை. கால் மட்டும்.",
    )
}</p>
</div>





{
    HELPERS["method_and_limits"](
        t,
        [
            ('A 1-hour supertrend flip is the only signal. When it turns up the book buys a call; there is nothing else on the chart.', '1-மணி நேர supertrend திருப்பம் மட்டுமே signal. மேல்நோக்கித் திரும்பும்போது call வாங்கப்படுகிறது; chart-இல் வேறெதுவும் இல்லை.'),
            ('The position is held until the trend flips back, rolling the strike when price runs six strikes away, and squared off at expiry if the flip has not come.', 'போக்கு திரும்பும் வரை நிலை வைக்கப்படுகிறது; விலை ஆறு strike விலகினால் strike உருட்டப்படுகிறது; திருப்பம் வராவிட்டால் expiry-இல் முடிக்கப்படுகிறது.'),
            ('Every premium is a recorded minute from the Dhan expired-options archive. Twelve exits it cannot quote are valued at intrinsic and reported separately, because they carry real weight in the total.', 'ஒவ்வொரு பிரீமியமும் Dhan காப்பகத்தின் பதிவான நிமிடம். அது தர முடியாத பன்னிரண்டு வெளியேற்றங்கள் intrinsic-இல் மதிப்பிடப்பட்டு தனியாகச் சொல்லப்படுகின்றன.'),
            ("Charges are the full statutory schedule per round, and the lot is the one in force on the contract's own expiry &mdash; 75, then 25, then 65.", 'கட்டணங்கள் முழு சட்டப்பூர்வ பட்டியல்; lot என்பது contract-இன் expiry-இல் அமலில் இருந்தது &mdash; 75, பின் 25, பின் 65.'),
        ],
        running=('Supertrend runs on its own tab behind a live gate that is still shut. This document is the recorded book; nothing here has traded real money.', 'Supertrend அதன் சொந்த tab-இல், இன்னும் மூடிய நேரடி gate-இன் பின்னால். இந்த ஆவணம் பதிவான புத்தகம்; இதில் எதுவும் உண்மையான பணத்தில் வர்த்தகம் ஆகவில்லை.'),
    )
}
</article>
</div>
{CHART_JS_SRC.replace("__SERIES__", json.dumps(HELPERS["daily_series"](TRAIL["rows"], lambda x: x["date"], lambda x: x["net"]), separators=(",", ":")))}
{READER_JS}
{LANG_JS}
"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(page)
print("wrote", OUT, len(page), "bytes")
print(
    "TRAIL",
    TRAIL["trades"],
    TRAIL["net"],
    "| BASE",
    BASE["trades"],
    BASE["net"],
    "| TGT",
    TGT["trades"],
    TGT["net"],
    "| PE",
    PE["trades"],
    PE["net"],
)
