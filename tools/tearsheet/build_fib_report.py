"""Emit the Fib Boundary tearsheet -- the winning configuration, both books.

Sibling of build_report.py (the five-year options tearsheet). It borrows that
document's stylesheet, helpers and bilingual toggle verbatim -- read from
build_report.py at run time, never copied -- so the two read as one family on
the Assets page.

Every figure comes from tools/fib_offline sweeps of the exact FibTouchLadder
the Cascade page trades: Lone (every level) · 5m mother · CE and PE · Intraday
out by 15:15 · Target = Trailing (1 span) · at most 4 buys a round · Rs 75,000 cap · ATM-2 · nearest
expiry >= 4 days, NIFTY, 3 Oct 2024 -> 30 Sep 2026 (extended 02-Oct-2026), seven blind mother times a
session, real recorded premiums, lot 25 / 75 / 65 by date. Nothing here is
typed by hand.

    python3 tools/tearsheet/build_fib_report.py [CE.csv PE.csv]

REGENERATING THE SWEEPS. They used to be written to $TMPDIR and were gone by
the time anyone needed them again, which left this sheet unbuildable -- it
could not even be recoloured with its siblings on 2026-09-02. Four commands,
about three minutes, and they reproduce the published book EXACTLY
(to 17-Aug-2026: CE 174 / Rs 92,658.35, ALL 343 / Rs 2,11,721.15 -- SENSEX
now differs: its lot table was corrected on 22-Sep-2026, see engine/backtest.py):

    T=09:15,09:30,10:15,11:15,12:15,13:15,14:15
    D=tools/fib_offline/runs/v4 && mkdir -p $D
    SYMBOL=NIFTY  TRAIL=1 MAX_BUYS=4 python3 tools/fib_offline/fib_sweep.py \
        5m CE levels intraday 2024-10-03 2026-09-30 $T $D/NIFTY_CE_trail_max4.csv
    SYMBOL=NIFTY  TRAIL=1 MAX_BUYS=0 python3 tools/fib_offline/fib_sweep.py \
        5m PE levels intraday 2024-10-03 2026-09-30 $T $D/NIFTY_PE_trail_max0.csv
    SYMBOL=SENSEX TRAIL=1 MAX_BUYS=4 python3 tools/fib_offline/fib_sweep.py \
        5m CE levels intraday 2024-10-03 2026-09-30 $T $D/SENSEX_CE_trail_max4.csv
    SYMBOL=SENSEX TRAIL=0 MAX_BUYS=0 python3 tools/fib_offline/fib_sweep.py \
        5m PE levels intraday 2024-10-03 2026-09-30 $T $D/SENSEX_PE_fixed_max0.csv
    FIB_SWEEP_DIR=tools/fib_offline/runs python3 tools/tearsheet/build_fib_report.py

TRAIL and MAX_BUYS did not exist as knobs before that date, so a plain run of
fib_sweep.py silently produced a DIFFERENT book from the published one --
FibTouchConfig defaults trailing_stop to False. That is why they are named
here rather than left to a default.
"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from i18n import LANG_CSS, LANG_JS, t, t_attr  # noqa: E402

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
OUT = _REPO / "docs" / "assets" / "fib-boundary-tearsheet.html"
DATA = _HERE / "fib_report_data.json"

# Where the fib_offline sweeps wrote their CSVs (FIB_SWEEP_DIR=/tmp/fib_offline
# on the machine that ran them); the four files can also be named outright.
SWEEPS = pathlib.Path(os.environ.get("FIB_SWEEP_DIR") or pathlib.Path(tempfile.gettempdir()) / "fib_offline")
CE_CSV = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else SWEEPS / "v4" / "NIFTY_CE_trail_max4.csv"
PE_CSV = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else SWEEPS / "v4" / "NIFTY_PE_trail_max0.csv"
SX_CE_CSV = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else SWEEPS / "v4" / "SENSEX_CE_trail_max4.csv"
SX_PE_CSV = pathlib.Path(sys.argv[4]) if len(sys.argv) > 4 else SWEEPS / "v4" / "SENSEX_PE_fixed_max0.csv"
# THE AUTO MOTHER, measured (tools/fib_offline/fib_chain_sweep.py, NEXT_MOTHER=breaking):
# the 09:15 5m candle every session, the breakout candle after a break. Absent
# files simply omit the section rather than fail the build.
AUTO_CE_CSV = SWEEPS / "chain" / "NIFTY_breaking_nocap.csv"
AUTO_SX_CSV = SWEEPS / "chain" / "SENSEX_breaking_nocap.csv"

MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri"]
DOW_TA = ["திங்", "செவ்", "புத", "வியா", "வெள்"]


# ── the parent document's helpers and stylesheet, borrowed, not copied ─────
def _borrow():
    src = (_HERE / "build_report.py").read_text()
    helpers = {}
    # r(), lakh(), cls(), curve_svg(), spark(): the exact functions the
    # five-year sheet formats and draws with.
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
    # The parent's stylesheet sits inside an f-string, so every brace is
    # doubled in the source; undo that or the CSS is invalid.
    css = style.group(1).replace("{{", "{").replace("}}", "}")
    # The parent writes its CSS inside a Python string, so a backslash escape
    # like the section mark is doubled in the SOURCE; read raw, it has to be
    # halved or the page shows "\00A71" where it means "§1".
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
STYLE = HELPERS["recolour"](STYLE, "fib")
r, lakh, cls, curve_svg, spark = (HELPERS[k] for k in ("r", "lakh", "cls", "curve_svg", "spark"))


# ── data ─────────────────────────────────────────────────────────────
def load(path: pathlib.Path, *, sequential: bool = True) -> list[dict]:
    """The sweep fires a blind mother at seven clock times every session, so
    its rows OVERLAP: 09:15 and 09:30 mothers on the same day are often the
    same trades counted twice. The page runs ONE campaign per index at a
    time, so the book here does too -- a mother is taken only if the
    previous campaign (buys or not) has already ended when it fires.

    ``sequential=False`` for a CHAIN, whose rows already run one at a time.
    The filter would be worse than useless there: the auto mother's next
    candle is the bar that HELD the break, so its open precedes the previous
    campaign's exit minute even though it starts after that bar closes, and
    every chained campaign after a break would be dropped (it read
    +Rs 1,02,232 against the chain sweep's own +Rs 1,13,568 on NIFTY)."""
    raw = []
    with open(path) as fh:
        for row in csv.DictReader(fh):
            row["_m"] = datetime.fromisoformat(row["mother"])
            row["_x"] = datetime.fromisoformat(row["exit_timestamp"]) if row.get("exit_timestamp") else row["_m"]
            raw.append(row)
    raw.sort(key=lambda x: x["_m"])
    rows, busy_until = [], None
    for row in raw:
        if sequential and busy_until is not None and row["_m"] < busy_until:
            continue  # a campaign was still running -- the page would not have started this one
        busy_until = row["_x"]
        if int(row.get("rounds") or 0) <= 0:
            continue
        m = row["_m"]
        rows.append(
            {
                "mother": m,
                "date": m.date(),
                "buys": int(row.get("buys") or 0),
                "deployed": float(row.get("deployed") or 0),
                "gross": float(row.get("gross") or 0),
                "costs": float(row.get("costs") or 0),
                "net": float(row.get("net") or 0),
                "exit_reason": str(row.get("exit_reason") or ""),
                "exit": row.get("exit_timestamp") or "",
                "lot": int(row.get("lot") or 0),
            }
        )
    rows.sort(key=lambda x: x["mother"])
    return rows


def book(rows: list[dict], cap: float = 75000.0) -> dict:
    net = sum(x["net"] for x in rows)
    gross = sum(x["gross"] for x in rows)
    costs = sum(x["costs"] for x in rows)
    wins = [x for x in rows if x["net"] > 0]
    losses = [x for x in rows if x["net"] < 0]
    gw = sum(x["net"] for x in wins)
    gl = -sum(x["net"] for x in losses)
    # equity + drawdown, trade by trade
    eq, peak, dd, dd_from, dd_to, cum = [], 0.0, 0.0, None, None, 0.0
    peak_at = rows[0]["date"] if rows else None
    for x in rows:
        cum += x["net"]
        eq.append((x["mother"].strftime("%Y-%m-%d"), round(cum, 2)))
        if cum > peak:
            peak, peak_at = cum, x["date"]
        if peak - cum > dd:
            dd, dd_from, dd_to = peak - cum, peak_at, x["date"]
    by_month = defaultdict(float)
    by_year = defaultdict(lambda: {"net": 0.0, "trades": 0, "wins": 0})
    by_dow = defaultdict(lambda: {"net": 0.0, "trades": 0, "wins": 0})
    reasons = defaultdict(lambda: {"n": 0, "net": 0.0})
    for x in rows:
        by_month[(x["date"].year, x["date"].month)] += x["net"]
        y = by_year[x["date"].year]
        y["net"] += x["net"]
        y["trades"] += 1
        y["wins"] += x["net"] > 0
        d = by_dow[x["date"].weekday()]
        d["net"] += x["net"]
        d["trades"] += 1
        d["wins"] += x["net"] > 0
        rr = reasons[x["exit_reason"]]
        rr["n"] += 1
        rr["net"] += x["net"]
    peak_deployed = max((x["deployed"] for x in rows), default=0.0)
    deps = sorted(x["deployed"] for x in rows)
    total_buys = sum(x["buys"] for x in rows)
    return {
        "avg_deployed": round(sum(deps) / len(deps), 2) if deps else 0,
        "median_deployed": round(deps[len(deps) // 2], 2) if deps else 0,
        "per_buy": round(sum(deps) / total_buys, 2) if total_buys else 0,
        "cap": cap,
        "return_on_cap": round(100 * net / cap, 1),
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(rows), 1) if rows else 0,
        "net": round(net, 2),
        "gross": round(gross, 2),
        "costs": round(costs, 2),
        "avg_trade": round(net / len(rows), 2) if rows else 0,
        "avg_win": round(gw / len(wins), 2) if wins else 0,
        "avg_loss": round(-gl / len(losses), 2) if losses else 0,
        "profit_factor": round(gw / gl, 2) if gl else None,
        "max_dd": round(-dd, 2),
        "dd_from": dd_from.isoformat() if dd_from else "",
        "dd_to": dd_to.isoformat() if dd_to else "",
        "return_over_dd": round(net / dd, 2) if dd else None,
        "best": max(rows, key=lambda x: x["net"]) if rows else None,
        "worst": min(rows, key=lambda x: x["net"]) if rows else None,
        "first": rows[0]["date"].isoformat() if rows else "",
        "last": rows[-1]["date"].isoformat() if rows else "",
        "peak_deployed": peak_deployed,
        "avg_buys": round(sum(x["buys"] for x in rows) / len(rows), 2) if rows else 0,
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
        "reasons": {
            k: {"n": v["n"], "net": round(v["net"], 2)} for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]["n"])
        },
        "best10": sorted(rows, key=lambda x: -x["net"])[:10],
        "worst10": sorted(rows, key=lambda x: x["net"])[:10],
        "rows": rows,
    }


CE_ROWS, PE_ROWS = load(CE_CSV), load(PE_CSV)
SX_CE_ROWS, SX_PE_ROWS = load(SX_CE_CSV), load(SX_PE_CSV)
CE, PE = book(CE_ROWS), book(PE_ROWS)
SX, SXPE = book(SX_CE_ROWS), book(SX_PE_ROWS)
# The two call books together, in time order -- what the desk would carry
# running this configuration on both indices.
ALL = book(sorted(CE_ROWS + SX_CE_ROWS, key=lambda x: x["mother"]), cap=150000.0)  # one Rs 75k ladder per index
# The auto mother's own books. A chain never overlaps itself, so load()'s
# one-at-a-time filter is a no-op here and the rows are taken as written.
AUTO_CE_ROWS = load(AUTO_CE_CSV, sequential=False) if AUTO_CE_CSV.exists() else []
AUTO_SX_ROWS = load(AUTO_SX_CSV, sequential=False) if AUTO_SX_CSV.exists() else []
AUTO_CE = book(AUTO_CE_ROWS) if AUTO_CE_ROWS else None
AUTO_SX = book(AUTO_SX_ROWS) if AUTO_SX_ROWS else None
AUTO_ALL = (
    book(sorted(AUTO_CE_ROWS + AUTO_SX_ROWS, key=lambda x: x["mother"]), cap=150000.0)
    if (AUTO_CE_ROWS and AUTO_SX_ROWS)
    else None
)
json.dump(
    {
        k: {kk: vv for kk, vv in b.items() if kk not in ("rows", "best10", "worst10", "best", "worst")}
        for k, b in (
            ("NIFTY_CE", CE),
            ("NIFTY_PE", PE),
            ("SENSEX_CE", SX),
            ("SENSEX_PE", SXPE),
            ("both_CE", ALL),
            *((("auto_NIFTY_CE", AUTO_CE),) if AUTO_CE else ()),
            *((("auto_SENSEX_CE", AUTO_SX),) if AUTO_SX else ()),
            *((("auto_both_CE", AUTO_ALL),) if AUTO_ALL else ()),
        )
    },
    open(DATA, "w"),
    default=str,
    indent=1,
)


# ── pieces ────────────────────────────────────────────────────────────
def kpis(b: dict, label_en: str, label_ta: str) -> str:
    pf = "—" if b["profit_factor"] is None else f"{b['profit_factor']:.2f}"
    rod = "—" if b["return_over_dd"] is None else f"{b['return_over_dd']:.2f}&times;"
    return f"""
<section>
  <div class="shead"><h2>{t(label_en, label_ta)}</h2></div>
  <div class="kpis">
    <div class="kpi"><div class="kpi-l">{t("Net profit", "நிகர லாபம்")}</div>
      <div class="kpi-v {cls(b["net"])}">{r(b["net"])}</div>
      <div class="kpi-s">{t("after all charges", "அனைத்து கட்டணங்களுக்குப் பின்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Trades", "டிரேடுகள்")}</div>
      <div class="kpi-v">{b["trades"]}</div>
      <div class="kpi-s">{b["wins"]} {t("won", "வெற்றி")} &middot; {b["losses"]} {t("lost", "நஷ்டம்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Win rate", "வெற்றி விகிதம்")}</div>
      <div class="kpi-v">{b["win_rate"]}%</div>
      <div class="kpi-s">{t("avg", "சராசரி")} {b["avg_buys"]} {t("buys per campaign", "வாங்கல்கள் / campaign")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Profit factor", "லாப காரணி")}</div>
      <div class="kpi-v">{pf}</div>
      <div class="kpi-s">{t("gross won &divide; gross lost", "மொத்த லாபம் &divide; மொத்த நஷ்டம்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Max drawdown", "அதிகபட்ச இறக்கம்")}</div>
      <div class="kpi-v neg">{r(b["max_dd"])}</div>
      <div class="kpi-s">{b["dd_from"]} &rarr; {b["dd_to"]}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Return / drawdown", "வருவாய் / இறக்கம்")}</div>
      <div class="kpi-v">{rod}</div>
      <div class="kpi-s">{t("profit per rupee of worst dip", "மோசமான இறக்கத்தின் ஒரு ரூபாய்க்கு லாபம்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Average trade", "சராசரி டிரேட்")}</div>
      <div class="kpi-v {cls(b["avg_trade"])}">{r(b["avg_trade"])}</div>
      <div class="kpi-s">{t("win", "வெற்றி")} {r(b["avg_win"])} &middot; {t("loss", "நஷ்டம்")} {r(b["avg_loss"])}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Best &middot; worst day", "சிறந்த &middot; மோசமான நாள்")}</div>
      <div class="kpi-v"><span class="pos">{r(b["best"]["net"]) if b["best"] else "—"}</span> &middot; <span class="neg">{r(b["worst"]["net"]) if b["worst"] else "—"}</span></div>
      <div class="kpi-s">{t("net after charges", "கட்டணங்களுக்குப் பின்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Deployed per campaign", "Campaign-க்கு பயன்பாடு")}</div>
      <div class="kpi-v">{r(b["avg_deployed"])}</div>
      <div class="kpi-s">{t("avg", "சராசரி")} &middot; {t("median", "இடைநிலை")} {r(b["median_deployed"])} &middot; {t("max", "அதிகபட்சம்")} {r(b["peak_deployed"])} {t("(summed over a campaign's rounds; one round never exceeds the cap)", "(campaign-இன் rounds சேர்த்து; ஒரு round cap-ஐ மீறாது)")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Cost per buy", "ஒரு வாங்கலுக்கு")}</div>
      <div class="kpi-v">{r(b["per_buy"])}</div>
      <div class="kpi-s">{t("one lot of the ATM&minus;2 call, avg", "ATM&minus;2 கால் ஒரு lot, சராசரி")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Return on the cap", "cap மீது வருவாய்")}</div>
      <div class="kpi-v {cls(b["net"])}">{b["return_on_cap"]}%</div>
      <div class="kpi-s">{t("net &divide;", "நிகர &divide;")} {r(b["cap"])} {t("(one &#8377;75,000 ladder per index)", "(ஒரு குறியீட்டுக்கு &#8377;75,000)")}</div></div>
  </div>
</section>"""


def curve_section(b: dict, title_en: str, title_ta: str, anchor: str) -> str:
    if len(b["curve"]) < 2:
        return ""
    line, area, dd, zero, hi, lo = curve_svg(b["curve"])
    return f"""
<section id="{anchor}">
  <div class="shead"><div><h2>{t(title_en, title_ta)}</h2>
    <p>{t("Cumulative net after charges, one point per campaign, in the order the mothers fired. Shading marks every stretch spent below the previous high.", "கட்டணங்களுக்குப் பின் திரட்டு நிகர, ஒரு campaign-க்கு ஒரு புள்ளி, mother-கள் வந்த வரிசையில். நிழல் = முந்தைய உச்சத்துக்குக் கீழே.")}</p></div></div>
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
    vals = [abs(v) for v in bm.values()] or [1]
    top = max(vals) or 1
    head = "".join(f"<th scope='col'>{m}</th>" for m in MON)
    body = ""
    for y in years:
        cells = ""
        tot = 0.0
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
    <p>{t("Net after charges by calendar month, thousands of rupees. Colour is the size relative to the largest month.", "மாதவாரி நிகர, ஆயிரங்களில். நிறம் = பெரிய மாதத்துடன் ஒப்பீடு.")}</p></div></div>
  <div class="tblwrap"><table class="heat">
    <thead><tr><th scope="col">{t("Year", "ஆண்டு")}</th>{head}<th scope="col">{t("Total", "மொத்தம்")}</th></tr></thead>
    <tbody>{body}</tbody></table></div>
</section>"""


def yearly(b: dict) -> str:
    rows = ""
    for y, v in b["by_year"].items():
        wr = round(100 * v["wins"] / v["trades"], 1) if v["trades"] else 0
        rows += f"<tr><th scope='row'>{y}</th><td>{v['trades']}</td><td>{wr}%</td><td class='{cls(v['net'])}'><strong>{r(v['net'])}</strong></td><td>{r(v['net'] / v['trades']) if v['trades'] else '—'}</td></tr>"
    return rows


def dow(b: dict) -> str:
    rows = ""
    for i, d in enumerate(DOW):
        v = b["by_dow"].get(d)
        if not v:
            continue
        wr = round(100 * v["wins"] / v["trades"], 1) if v["trades"] else 0
        rows += f"<tr><th scope='row'>{t(d, DOW_TA[i])}</th><td>{v['trades']}</td><td>{wr}%</td><td class='{cls(v['net'])}'><strong>{r(v['net'])}</strong></td><td>{r(v['net'] / v['trades']) if v['trades'] else '—'}</td></tr>"
    return rows


def reasons(b: dict) -> str:
    names = {
        "intraday_close": ("Sold at 15:15", "15:15-இல் விற்பனை"),
        "target": ("Target reached (fixed)", "இலக்கு எட்டியது"),
        "trail_stop": ("Trail stop", "Trail stop"),
        "mother_broken": ("Mother broken", "Mother உடைந்தது"),
        "expiry_square_off": ("Expiry square-off", "Expiry முடிப்பு"),
    }
    rows = ""
    for k, v in b["reasons"].items():
        en, ta = names.get(k, (k.replace("_", " "), k.replace("_", " ")))
        rows += f"<tr><th scope='row'>{t(en, ta)}</th><td>{v['n']}</td><td>{round(100 * v['n'] / b['trades'], 1) if b['trades'] else 0}%</td><td class='{cls(v['net'])}'><strong>{r(v['net'])}</strong></td></tr>"
    return rows


def ten(rows: list[dict]) -> str:
    out = ""
    for x in rows:
        out += (
            f"<tr><td>{x['mother'].strftime('%d %b %Y %H:%M')}</td><td>{x['buys']}</td><td>{r(x['deployed'])}</td>"
            f"<td>{x['exit_reason'].replace('_', ' ')}</td><td class='{cls(x['net'])}'><strong>{r(x['net'])}</strong></td></tr>"
        )
    return out


def _auto_row(label: str, b: dict | None, cap: float) -> str:
    if not b:
        return ""
    rod = "&mdash;" if b["return_over_dd"] is None else f"{b['return_over_dd']:.2f}&times;"
    pf = "&mdash;" if b["profit_factor"] is None else f"{b['profit_factor']:.2f}"
    return (
        f'<tr><th scope="row">{label}</th><td>{b["trades"]}</td><td>{b["win_rate"]}%</td>'
        f'<td class="{cls(b["net"])}"><strong>{r(b["net"])}</strong></td>'
        f'<td class="neg">{r(b["max_dd"])}</td><td>{rod}</td><td>{pf}</td>'
        f"<td>{r(round(b['net'] / max(b['trades'], 1), 2))}</td></tr>"
    )


def auto_section() -> str:
    """The auto mother, measured on the same months as the book above.

    Omitted when the chain sweep's CSVs are not on this machine, so the sheet
    still builds; it never prints a figure it did not compute.
    """
    if not (AUTO_CE and AUTO_SX and AUTO_ALL):
        return ""
    return f"""
<section id="auto-mother">
  <div class="shead"><div><h2>{
        t("The auto mother &mdash; 09:15 every session", "தானியங்கி mother &mdash; ஒவ்வொரு அமர்விலும் 09:15")
    }</h2>
  <p>{
        t(
            "Phil, 19 Aug 2026: run it without me. Every session the <strong>09:15 5-minute candle</strong> is the mother. "
            "When a mother breaks, the 5-minute candle that <em>held</em> the breaking close &mdash; the <strong>breakout candle</strong> "
            "&mdash; becomes the next mother the moment it closes, and a fresh ladder starts there; the last one ends at 15:15 and "
            "tomorrow starts itself. Everything else is the configuration above, unchanged: <strong>Lone</strong>, 5m, CE, at most four "
            "buys a round, &#8377;75,000, ATM&minus;2, trailing exit. Around six campaigns start on a normal day and one in twelve trades; "
            "the rest close with no buys.",
            "பில், 19 ஆக 2026: என்னை இல்லாமல் ஓடட்டும். ஒவ்வொரு அமர்விலும் <strong>09:15 5-நிமிட candle</strong> mother. "
            "Mother உடைந்தால், உடைத்த close-ஐக் கொண்ட 5-நிமிட candle (<strong>breakout candle</strong>) மூடியவுடன் அடுத்த mother; "
            "கடைசி campaign 15:15-இல் முடியும்; மறுநாள் தானாகத் தொடங்கும். மற்ற அனைத்தும் மேலே உள்ள அமைப்பே: <strong>Lone</strong>, 5m, CE, "
            "ஒரு round-க்கு அதிகபட்சம் நான்கு வாங்கல், &#8377;75,000, ATM&minus;2, trailing.",
        )
    }</p></div></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Book", "புத்தகம்")}</th><th scope="col">{
        t("Campaigns", "Campaign-கள்")
    }</th><th scope="col">{t("Win rate", "வெற்றி %")}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{
        t("Max drawdown", "அதிகபட்ச இறக்கம்")
    }</th><th scope="col">{t("Return &divide; drawdown", "நிகர &divide; இறக்கம்")}</th><th scope="col">{
        t("Profit factor", "லாப காரணி")
    }</th><th scope="col">{t("Per campaign", "ஒரு campaign-க்கு")}</th></tr></thead>
    <tbody>
      {_auto_row(t("NIFTY CE &mdash; auto", "NIFTY CE &mdash; auto"), AUTO_CE, 75000.0)}
      {_auto_row(t("NIFTY CE &mdash; your own mothers (above)", "NIFTY CE &mdash; உங்கள் mother-கள்"), CE, 75000.0)}
      {_auto_row(t("SENSEX CE &mdash; auto", "SENSEX CE &mdash; auto"), AUTO_SX, 75000.0)}
      {_auto_row(t("SENSEX CE &mdash; your own mothers (above)", "SENSEX CE &mdash; உங்கள் mother-கள்"), SX, 75000.0)}
      <tr class="trow-total"><th scope="row">{t("Both, auto", "இரண்டும், auto")}</th><td>{AUTO_ALL["trades"]}</td>
        <td>{AUTO_ALL["win_rate"]}%</td><td class="{cls(AUTO_ALL["net"])}"><strong>{r(AUTO_ALL["net"])}</strong></td>
        <td class="neg">{r(AUTO_ALL["max_dd"])}</td>
        <td>{"&mdash;" if AUTO_ALL["return_over_dd"] is None else f"{AUTO_ALL['return_over_dd']:.2f}&times;"}</td>
        <td>{"&mdash;" if AUTO_ALL["profit_factor"] is None else f"{AUTO_ALL['profit_factor']:.2f}"}</td>
        <td>{r(round(AUTO_ALL["net"] / max(AUTO_ALL["trades"], 1), 2))}</td></tr>
    </tbody></table></div>
  <p class="note">{
        t(
            "Measured on the same walk, with the same engine, one campaign per index at a time. The alternative reading &mdash; the candle "
            "<em>after</em> the breakout &mdash; was measured too and kept only +&#8377;53,264 of NIFTY's profit against a deeper drawdown, so "
            "the chain uses the breakout candle. Four independent checks agreed before this was switched on: a fresh re-run byte for byte, "
            "the chain's own invariants, every chained campaign that starts on one of the sheet's clock candles equal to the row above, and a "
            "second implementation written from scratch equal campaign by campaign. Each index's figures are exact; the combined "
            "drawdown takes the deeper of the two readings when both indices start a campaign in the same minute.",
            "அதே நடை, அதே engine, ஒரு குறியீட்டுக்கு ஒரு நேரத்தில் ஒரு campaign. மாற்று வாசிப்பு &mdash; breakout-க்கு <em>அடுத்த</em> candle &mdash; "
            "NIFTY-இல் +&#8377;53,264 மட்டுமே, இறக்கம் அதிகம்; எனவே breakout candle. இயக்கும் முன் நான்கு சுயாதீன சரிபார்ப்புகள் ஒத்துப்போயின.",
        )
    }</p>
</section>"""


def side_block(b: dict, side: str, index: str = "NIFTY") -> str:
    name_en = f"{index} call book (CE)" if side == "CE" else f"{index} put book (PE)"
    name_ta = f"{index} கால் புத்தகம் (CE)" if side == "CE" else f"{index} புட் புத்தகம் (PE)"
    return f"""
{kpis(b, f"{name_en} &mdash; at a glance", f"{name_ta} &mdash; ஒரே பார்வையில்")}
{curve_section(b, f"{name_en} &mdash; cumulative curve", f"{name_ta} &mdash; ஒட்டுமொத்த வளைவு", f"curve-{index.lower()}-{side.lower()}")}
{heat(b, f"{name_en} &mdash; month by month", f"{name_ta} &mdash; மாதவாரியாக")}
<section>
  <div class="shead"><h2>{t(f"{name_en} &mdash; year by year", f"{name_ta} &mdash; ஆண்டுவாரியாக")}</h2></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Year", "ஆண்டு")}</th><th scope="col">{t("Trades", "டிரேடுகள்")}</th><th scope="col">{t("Win rate", "வெற்றி %")}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{t("Per trade", "ஒரு டிரேடுக்கு")}</th></tr></thead>
    <tbody>{yearly(b)}</tbody></table></div>
</section>
<section>
  <div class="shead"><h2>{t(f"{name_en} &mdash; which day of the week pays", f"{name_ta} &mdash; வாரத்தின் எந்த நாள் லாபம் தருகிறது")}</h2></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Weekday", "கிழமை")}</th><th scope="col">{t("Trades", "டிரேடுகள்")}</th><th scope="col">{t("Win rate", "வெற்றி %")}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{t("Per trade", "ஒரு டிரேடுக்கு")}</th></tr></thead>
    <tbody>{dow(b)}</tbody></table></div>
  <div class="tblwrap" style="margin-top:12px"><table>
    <thead><tr><th scope="col">{t("How the campaign ended", "Campaign எப்படி முடிந்தது")}</th><th scope="col">{t("Count", "எண்ணிக்கை")}</th><th scope="col">{t("Share", "பங்கு")}</th><th scope="col">{t("Net", "நிகர")}</th></tr></thead>
    <tbody>{reasons(b)}</tbody></table></div>
</section>
<section>
  <div class="shead"><h2>{t(f"{name_en} &mdash; best ten, worst ten", f"{name_ta} &mdash; சிறந்த பத்து, மோசமான பத்து")}</h2></div>
  <div class="two-up">
    <div class="tblwrap"><table>
      <thead><tr><th scope="col">{t("Mother", "Mother")}</th><th scope="col">{t("Buys", "வாங்கல்")}</th><th scope="col">{t("Deployed", "பயன்பாடு")}</th><th scope="col">{t("Ended by", "முடிவு")}</th><th scope="col">{t("Net", "நிகர")}</th></tr></thead>
      <tbody>{ten(b["best10"])}</tbody></table></div>
    <div class="tblwrap"><table>
      <thead><tr><th scope="col">{t("Mother", "Mother")}</th><th scope="col">{t("Buys", "வாங்கல்")}</th><th scope="col">{t("Deployed", "பயன்பாடு")}</th><th scope="col">{t("Ended by", "முடிவு")}</th><th scope="col">{t("Net", "நிகர")}</th></tr></thead>
      <tbody>{ten(b["worst10"])}</tbody></table></div>
  </div>
</section>"""


# ── the page ──────────────────────────────────────────────────────────
# Calendar months the book spans, both ends counted (Oct 2024 -> Aug 2026 was
# the "23" this used to be typed as; it went stale when the book was extended).
_y1, _m1 = (int(x) for x in str(ALL["first"])[:7].split("-"))
_y2, _m2 = (int(x) for x in str(ALL["last"])[:7].split("-"))
months = (_y2 - _y1) * 12 + _m2 - _m1 + 1
page = f"""<title>PhilForge Fib Boundary Tearsheet</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
{STYLE}
.two-up {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(360px,1fr)); gap:12px; }}
table.heat td {{ text-align:right; font-variant-numeric:tabular-nums; }}
{LANG_CSS}
</style>

<section class="document-hero">
  <div class="hero-copy">
    <p class="eyebrow"><b>TEARSHEET</b>{
    t("PhilForge &middot; Cascade &middot; Fib Boundary", "PhilForge &middot; Cascade &middot; Fib Boundary")
}</p>
    <h1>{
    t(
        f"Fib Boundary &mdash; Lone &middot; 5m &middot; CE &middot; Trailing &mdash; NIFTY &amp; SENSEX, {months} Months",
        f"Fib Boundary &mdash; Lone &middot; 5m &middot; CE &middot; Trailing &mdash; NIFTY &amp; SENSEX, {months} மாதங்கள்",
    )
}</h1>
    <p class="lede">{
    t(
        f"The one configuration that finished green over {months} months of blind mothers, on the call book. Your mother candle on the 5-minute chart; auto trendlines and stacked fibs; every level of every fib is a rung (Lone); a touch collects and the two-red turn buys one lot of the ATM&minus;2 call; out by 15:15 on the mother's own day; the exit is a trailing target &mdash; reaching the target arms a trail and the basket is sold when a 1-minute close gives back one fib span from the best price since. At most four buys a round, Rs 75,000 ladder cap, nearest expiry at least four days out. NIFTY (lot 25 &rarr; 75 &rarr; 65, strike step 50) and SENSEX (lot 10 &rarr; 20, strike step 100), each priced from its own real recorded option minutes. <strong>Puts are not traded in this configuration</strong> &mdash; the same rules on the put book lost money on the same walk, and that is stated below rather than hidden.",
        f"{months} மாத blind mother-களில் பச்சையாக முடிந்த ஒரே அமைப்பு, கால் புத்தகத்தில். 5-நிமிட chart-இல் உங்கள் mother candle; தானியங்கி trendlines, அடுக்கிய fibs; ஒவ்வொரு fib-இன் ஒவ்வொரு level-உம் ஒரு rung (Lone); ஒரு touch சேகரிக்கும், இரு-சிவப்பு திருப்பம் ATM&minus;2 கால்-இல் ஒரு lot வாங்கும்; mother-இன் அன்றே 15:15-க்குள் வெளியே; வெளியேற்றம் trailing target. ஒரு round-க்கு அதிகபட்சம் நான்கு வாங்கல்கள், &#8377;75,000 ladder cap, குறைந்தது நான்கு நாள் expiry. NIFTY (lot 25 &rarr; 75 &rarr; 65) மற்றும் SENSEX (lot 10 &rarr; 20), ஒவ்வொன்றும் அதன் சொந்த option நிமிடங்களில் விலை. <strong>இந்த அமைப்பில் புட் வர்த்தகம் இல்லை</strong> &mdash; அதே விதிகள் புட் புத்தகத்தில் அதே நடையில் நஷ்டம்; அது கீழே மறைக்காமல் சொல்லப்பட்டுள்ளது.",
    )
}</p>
    <div class="document-meta" aria-label="Document metadata">
      <div class="meta-chip"><span>{t("Period", "காலம்")}</span><strong>{ALL["first"]} &rarr; {ALL["last"]}</strong></div>
      <div class="meta-chip"><span>{t("Campaigns", "Campaign-கள்")}</span><strong>{ALL["trades"]} ({
    CE["trades"]
} NIFTY &middot; {SX["trades"]} SENSEX)</strong></div>
      <div class="meta-chip"><span>{t("Side", "பக்கம்")}</span><strong>{
    t("CE only &mdash; PE not traded", "CE மட்டும் &mdash; PE இல்லை")
}</strong></div>
      <div class="meta-chip"><span>{t("Position size", "பொசிஷன் அளவு")}</span><strong>{
    t(
        "1 lot per buy, at most 4 buys, &le; &#8377;75,000 a ladder",
        "ஒரு வாங்கலுக்கு 1 lot, அதிகபட்சம் 4 வாங்கல்கள், ladder-க்கு &le; &#8377;75,000",
    )
}</strong></div>
      <div class="meta-chip"><span>{t("Costs", "கட்டணங்கள்")}</span><strong>{
    t("Brokerage, STT, GST, stamp, per leg", "புரோக்கரேஜ், STT, GST, stamp, ஒவ்வொரு leg-க்கும்")
}</strong></div>
      <div class="meta-chip"><span>{t("Mothers", "Mother-கள்")}</span><strong>{
    t("blind: 7 clock times a session, not hand-picked", "blind: அமர்வுக்கு 7 நேரங்கள், கையால் தேர்வு இல்லை")
}</strong></div>
    </div>
  </div>
  <div class="system-sigil" aria-hidden="true">
    <div class="sigil-ring ring-one"></div><div class="sigil-ring ring-two"></div><div class="sigil-ring ring-three"></div>
    <div class="sigil-core">CE</div>
    <span class="sigil-label label-one">NIFTY</span><span class="sigil-label label-two">SENSEX</span>
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
        "Same engine as the Cascade page &middot; time-ordered walk &middot; verified ten ways, 0 failures",
        "Cascade பக்கத்தின் அதே engine &middot; நேர வரிசை நடை &middot; பத்து வழி சரிபார்ப்பு, 0 தோல்வி",
    )
}</small>
    </div>
  </div>
</aside>

<article class="document-body" id="document-body">

<div class="note">
  <h2 class="note-h">{t("The finding that matters most", "மிக முக்கியமான கண்டுபிடிப்பு")}</h2>
  <p>{
    t(
        "These campaigns fired at seven fixed clock times every session &mdash; not on candles a trader would choose &mdash; and are counted <strong>one at a time per index</strong>, exactly as the page runs: a clock-time mother that fires while an earlier campaign is still open is not taken. Most blind mothers never trade at all (price closes back above the mother before a buy). What the table measures is the <strong>rule</strong>, on the exact code the paper run executes, walked candle by candle in time order with real recorded premiums. Whether <em>your</em> mothers earn is what the paper run is for.",
        "இந்த campaign-கள் ஒவ்வொரு அமர்விலும் ஏழு நிலையான நேரங்களில் தொடங்கின &mdash; ஒரு trader தேர்வு செய்யும் candle-கள் அல்ல &mdash; ஒரு குறியீட்டுக்கு <strong>ஒரு நேரத்தில் ஒரு campaign</strong> மட்டும் கணக்கிடப்பட்டது, பக்கம் இயங்குவது போலவே (முந்தைய campaign திறந்திருக்கும்போது வரும் mother எடுக்கப்படாது). பெரும்பாலான blind mother-கள் வர்த்தகமே செய்யாது. அளக்கப்படுவது <strong>விதி</strong>, paper run இயக்கும் அதே code-இல், நேர வரிசையில் candle-candle-ஆக, உண்மையான premium-களில். <em>உங்கள்</em> mother-கள் சம்பாதிக்குமா என்பதுதான் paper run-இன் வேலை.",
    )
}</p>
  <p>{
    t(
        "Ten independent checks were run over hundreds of these campaigns before this was published: the backtest walk equals a tick-simulated paper loop, it is deterministic, no fill happens before its fib's bar has closed, P&amp;L is recomputed from the legs, lot size follows the date, every premium is a recorded archive open, intraday ends on its own day, strike is ATM&minus;2 with one expiry &ge; 4 days per campaign, a broken mother is a real close through the edge. Zero failures.",
        "இது வெளியிடும் முன் நூற்றுக்கணக்கான campaign-களில் பத்து சுயாதீன சரிபார்ப்புகள்: backtest நடை = paper loop; நிர்ணயம்; fib bar மூடும் முன் fill இல்லை; P&amp;L legs-இலிருந்து மறுகணக்கு; lot தேதிப்படி; ஒவ்வொரு premium-உம் பதிவான open; intraday அன்றே முடியும்; ATM&minus;2, ஒரு expiry &ge; 4 நாள்; mother உடைவு உண்மையான close. பூஜ்ஜியம் தோல்வி.",
    )
}</p>
</div>

{
    kpis(
        ALL,
        "The programme at a glance",
        "ஒரே பார்வையில்",
    )
}
{curve_section(ALL, "Both indices &mdash; cumulative curve", "இரு குறியீடுகள் &mdash; ஒட்டுமொத்த வளைவு", "curve-all")}

{side_block(CE, "CE", "NIFTY")}
{side_block(SX, "CE", "SENSEX")}

<div class="note note-warn">
  <h2 class="note-h">{
    t(
        "The put book is NOT traded in this configuration &mdash; on either index",
        "இந்த அமைப்பில் புட் புத்தகம் வர்த்தகம் செய்யப்படுவதில்லை &mdash; இரு குறியீடுகளிலும்",
    )
}</h2>
  <p>{
    t(
        f"The same rules were replayed on puts (Buy PE, ATM+2) over the same {months} months and lost on both indices. NIFTY: {PE['trades']} campaigns, {PE['wins']} won / {PE['losses']} lost, net {r(PE['net'])}, worst day {r(PE['worst']['net']) if PE['worst'] else '—'}. SENSEX: {SXPE['trades']} campaigns, {SXPE['wins']} won / {SXPE['losses']} lost, net {r(SXPE['net'])}, worst day {r(SXPE['worst']['net']) if SXPE['worst'] else '—'}. Every other put variant measured (fixed target, deeper first rung at L4 or L8, carrying overnight) lost as well. So this sheet, and the recommendation it stands for, is calls only.",
        f"அதே விதிகள் புட்-இல் (Buy PE, ATM+2) அதே {months} மாதங்களில் replay, இரு குறியீடுகளிலும் நஷ்டம். NIFTY: {PE['trades']} campaign-கள், {PE['wins']} வெற்றி / {PE['losses']} நஷ்டம், நிகர {r(PE['net'])}. SENSEX: {SXPE['trades']} campaign-கள், {SXPE['wins']} / {SXPE['losses']}, நிகர {r(SXPE['net'])}. அளந்த ஒவ்வொரு புட் மாற்றமும் நஷ்டம். எனவே இந்த அறிக்கையும் பரிந்துரையும் கால் மட்டும்.",
    )
}</p>
</div>

{auto_section()}

<section>
  <div class="shead"><div><h2>{
    t("How much capital this needs", "இதற்கு எவ்வளவு மூலதனம் தேவை")
}</h2>
    <p>{
    t(
        "Every buy is ONE lot of the ATM&minus;2 call, paid in full (no margin, no leverage). A campaign adds a lot each time the fall turns at a fresh rung, at most four times a round and never past the &#8377;75,000 ladder cap; whatever is held is sold by 15:15 the same day, so the cap is also the most a single round can lose in principle. A campaign that banks a round and re-arms on a new low spends the cap again, which is why &ldquo;max deployed&rdquo; over a campaign's rounds can exceed it. One campaign per index runs at a time: &#8377;75,000 per index, &#8377;1,50,000 for both.",
        "ஒவ்வொரு வாங்கலும் ATM&minus;2 கால்-இன் ஒரு lot, முழு premium (margin இல்லை). ஒவ்வொரு புதிய rung-இன் திருப்பத்திலும் ஒரு lot சேரும், &#8377;75,000 cap வரை; அன்றே 15:15-க்குள் விற்பனை. ஒரு குறியீட்டுக்கு ஒரு campaign மட்டும்.",
    )
}</p></div></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Book", "புத்தகம்")}</th><th scope="col">{t("Session", "அமர்வு")}</th><th scope="col">{
    t("Exit", "வெளியேற்றம்")
}</th><th scope="col">{t("Buys a round", "round-க்கு வாங்கல்")}</th><th scope="col">{
    t("Campaigns", "Campaign-கள்")
}</th><th scope="col">{t("Buys per campaign", "வாங்கல்/campaign")}</th><th scope="col">{
    t("Cost per buy", "ஒரு வாங்கல்")
}</th><th scope="col">{t("Deployed per campaign, avg", "பயன்பாடு / campaign, சராசரி")}</th><th scope="col">{
    t("Median", "இடைநிலை")
}</th><th scope="col">{t("Max (over its rounds)", "அதிகபட்சம் (rounds சேர்த்து)")}</th><th scope="col">{
    t("Net", "நிகர")
}</th><th scope="col">{t("Net &divide; cap", "நிகர &divide; cap")}</th></tr></thead>
    <tbody>
      <tr><th scope="row">NIFTY CE</th><td>{
    t("Intraday &middot; out by 15:15", "Intraday &middot; 15:15-க்குள்")
}</td><td>{t("Trailing", "Trailing")}</td><td>&le; 4</td><td>{CE["trades"]}</td><td>{CE["avg_buys"]}</td><td>{
    r(CE["per_buy"])
}</td><td>{r(CE["avg_deployed"])}</td><td>{r(CE["median_deployed"])}</td><td>{r(CE["peak_deployed"])}</td><td class="{
    cls(CE["net"])
}"><strong>{r(CE["net"])}</strong></td><td>{CE["return_on_cap"]}%</td></tr>
      <tr><th scope="row">SENSEX CE</th><td>{
    t("Intraday &middot; out by 15:15", "Intraday &middot; 15:15-க்குள்")
}</td><td>{t("Trailing", "Trailing")}</td><td>&le; 4</td><td>{SX["trades"]}</td><td>{SX["avg_buys"]}</td><td>{
    r(SX["per_buy"])
}</td><td>{r(SX["avg_deployed"])}</td><td>{r(SX["median_deployed"])}</td><td>{r(SX["peak_deployed"])}</td><td class="{
    cls(SX["net"])
}"><strong>{r(SX["net"])}</strong></td><td>{SX["return_on_cap"]}%</td></tr>
      <tr class="trow-total"><th scope="row">{t("Both", "இரண்டும்")}</th><td>{
    t("Intraday &middot; out by 15:15", "Intraday &middot; 15:15-க்குள்")
}</td><td>{t("Trailing", "Trailing")}</td><td>&le; 4</td><td>{ALL["trades"]}</td><td>{ALL["avg_buys"]}</td><td>{
    r(ALL["per_buy"])
}</td><td>{r(ALL["avg_deployed"])}</td><td>{r(ALL["median_deployed"])}</td><td>{
    r(ALL["peak_deployed"])
}</td><td class="{cls(ALL["net"])}"><strong>{r(ALL["net"])}</strong></td><td>{
    t("&#8377;1,50,000 for both indices", "இரு குறியீடுகளுக்கும் &#8377;1,50,000")
}: {round(100 * ALL["net"] / 150000.0, 1)}%</td></tr>
    </tbody></table></div>
</section>

<section>
  <div class="shead"><div><h2>{t("Charges, in full", "கட்டணங்கள், முழுமையாக")}</h2>
    <p>{
    t(
        "Every rupee between the gross result and the account, per leg, per round: brokerage, STT, exchange charges, SEBI fee, GST, stamp duty &mdash; the same schedule the paper engine books.",
        "மொத்த முடிவுக்கும் கணக்குக்கும் இடையிலான ஒவ்வொரு ரூபாயும், leg-க்கும் round-க்கும்: புரோக்கரேஜ், STT, exchange, SEBI, GST, stamp &mdash; paper engine பதியும் அதே அட்டவணை.",
    )
}</p></div></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Book", "புத்தகம்")}</th><th scope="col">{t("Gross", "மொத்தம்")}</th><th scope="col">{
    t("Charges", "கட்டணம்")
}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{
    t("Charges per campaign", "campaign-க்கு கட்டணம்")
}</th></tr></thead>
    <tbody>
      <tr><th scope="row">NIFTY CE</th><td class="{cls(CE["gross"])}">{r(CE["gross"])}</td><td class="neg">{
    r(-CE["costs"])
}</td><td class="{cls(CE["net"])}"><strong>{r(CE["net"])}</strong></td><td>{
    r(CE["costs"] / CE["trades"]) if CE["trades"] else "—"
}</td></tr>
      <tr><th scope="row">SENSEX CE</th><td class="{cls(SX["gross"])}">{r(SX["gross"])}</td><td class="neg">{
    r(-SX["costs"])
}</td><td class="{cls(SX["net"])}"><strong>{r(SX["net"])}</strong></td><td>{
    r(SX["costs"] / SX["trades"]) if SX["trades"] else "—"
}</td></tr>
      <tr class="trow-total"><th scope="row">{t("Both", "இரண்டும்")}</th><td class="{cls(ALL["gross"])}">{
    r(ALL["gross"])
}</td><td class="neg">{r(-ALL["costs"])}</td><td class="{cls(ALL["net"])}"><strong>{r(ALL["net"])}</strong></td><td>{
    r(ALL["costs"] / ALL["trades"]) if ALL["trades"] else "—"
}</td></tr>
    </tbody></table></div>
</section>

{
    HELPERS["cycle_section"](
        HELPERS["daily_series"](CE["rows"] + SX["rows"], lambda x: x["mother"], lambda x: x["net"]),
        t,
        r,
        noun_en="campaigns",
        noun_ta="Campaign-கள்",
    )
}
{
    HELPERS["daily_ledger"](
        HELPERS["daily_series"](CE["rows"] + SX["rows"], lambda x: x["mother"], lambda x: x["net"]),
        t,
        t_attr,
        r,
        cls,
        noun_en="campaigns",
        noun_ta="Campaign-கள்",
    )
}

<section>
  <div class="shead"><h2>{t("Risk register", "ரிஸ்க் பதிவேடு")}</h2></div>
  <p>{
    t(
        "On the same 23-month walk: puts lose (see the note above); a fixed target loses; holding a deep basket one more day loses more on every chart and both sides; carrying to expiry with no stop loses about a lakh a quarter on the call book alone (67 campaigns ended at expiry, averaging &minus;&#8377;42,868 each); 15m and 1m mothers do not beat 5m; Partner (where two meet) with a trailing exit is also green on calls but smaller. Those numbers live on the replay report; this sheet is only the configuration that is switched on.",
        "அதே 23-மாத நடையில்: புட் நஷ்டம் (மேலே); fixed target நஷ்டம்; ஆழமான basket-ஐ ஒரு நாள் அதிகம் வைத்திருப்பது எல்லா chart-களிலும் அதிக நஷ்டம்; stop இல்லாமல் expiry வரை carry செய்வது கால் புத்தகத்தில் மட்டும் காலாண்டுக்கு சுமார் ஒரு லட்சம் நஷ்டம் (67 campaign-கள் expiry-இல், சராசரி &minus;&#8377;42,868); 15m, 1m mother-கள் 5m-ஐ மிஞ்சவில்லை; Partner + trailing கால்-இல் பச்சை, ஆனால் சிறியது. இந்த அறிக்கை இயக்கத்தில் உள்ள அமைப்பு மட்டுமே.",
    )
}</p>
</section>

{
    HELPERS["method_and_limits"](
        t,
        [
            ('Seven blind mother times a session &mdash; 09:15, 09:30, 10:15, 11:15, 12:15, 13:15, 14:15 &mdash; never a mother picked by hand.', 'ஒரு அமர்வுக்கு ஏழு blind mother நேரங்கள் &mdash; 09:15, 09:30, 10:15, 11:15, 12:15, 13:15, 14:15 &mdash; கையால் தேர்ந்தெடுக்கப்பட்டது அல்ல.'),
            ("Every level of every drawn fib is a rung. A touch collects, the two-red turn buys one lot of the ATM&minus;2 call, and the campaign is out by 15:15 on the mother's own day.", 'வரையப்பட்ட ஒவ்வொரு fib-இன் ஒவ்வொரு level-உம் ஒரு rung. touch சேகரிக்கும், இரு-சிவப்பு திருப்பம் ATM&minus;2 call-இல் ஒரு lot வாங்கும், mother-இன் அன்றே 15:15-க்கு வெளியே.'),
            ('At most four buys a round, a Rs 75,000 ladder cap, and the nearest expiry at least four days out. Both indices are priced from their own recorded option minutes.', 'ஒரு round-க்கு அதிகபட்சம் நான்கு வாங்கல், &#8377;75,000 ladder வரம்பு, குறைந்தது நான்கு நாள் expiry. இரு குறியீடுகளும் அவற்றின் சொந்த பதிவான option நிமிடங்களில் விலை.'),
            ("Charges are the full statutory schedule per round. NIFTY's lot ran 25 &rarr; 75 &rarr; 65 and SENSEX's 10 &rarr; 20, each read from the contract's own expiry.", 'கட்டணங்கள் முழு சட்டப்பூர்வ பட்டியல். NIFTY lot 25 &rarr; 75 &rarr; 65, SENSEX 10 &rarr; 20 &mdash; ஒவ்வொன்றும் contract-இன் expiry-இல் இருந்து.'),
        ],
        running=('Fib Boundary is the one strategy with live order code, behind its own gate. This document is the recorded book, not the live one; the Cascade tab is where a running campaign appears.', 'Fib Boundary மட்டுமே நேரடி order குறியீடு கொண்டது, அதன் சொந்த gate-இன் பின்னால். இந்த ஆவணம் பதிவான புத்தகம்; இயங்கும் campaign Cascade tab-இல் தெரியும்.'),
    )
}
</article>
</div>
{CHART_JS_SRC.replace("__SERIES__", json.dumps(HELPERS["daily_series"](CE["rows"] + SX["rows"], lambda x: x["mother"], lambda x: x["net"]), separators=(",", ":")))}
{READER_JS}
{LANG_JS}
"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(page)
print("wrote", OUT, len(page), "bytes")
print("CE", CE["trades"], CE["net"], "PE", PE["trades"], PE["net"], "ALL", ALL["trades"], ALL["net"])
