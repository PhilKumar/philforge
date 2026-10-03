"""Emit the Candle Entry tearsheet -- Phil's box mother, the two-red ladder, the call book.

Third of the family (build_report.py is the five-year options sheet,
build_fib_report.py the Fib Boundary sheet). It borrows the parent document's
stylesheet, helpers and bilingual toggle verbatim -- read from build_report.py
at run time, never copied -- so the three read as one family on the Assets page.

Every figure comes from tools/candle_entry_offline/sweep.py runs of the exact
TwoRedLadder the Candle Entry tab trades: 5m start, mother = the bar that makes
the 278-bar high, buys only in the bottom quarter of that box, two red closes
stepping down then a buy-stop on the first red's close, 5m -> 15m with 1 then
2 lots, one NIFTY CE ATM-2 on the monthly, a quarter-way target that arms a
30% give-back trail. 1 Oct 2024 -> 30 Sep 2026 (extended 02-Oct-2026), one campaign at a time, real
recorded premiums, lot 25 / 75 / 65 by date, muhurat session excluded. Nothing
here is typed by hand.

    python3 tools/tearsheet/build_candle_report.py [CE_trail.csv CE_fixed.csv PE_trail.csv]
"""

from __future__ import annotations

import csv
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
OUT = _REPO / "docs" / "assets" / "candle-entry-tearsheet.html"
DATA = _HERE / "candle_report_data.json"
RUNS = _REPO / "tools" / "candle_entry_offline" / "runs"
CE_TRAIL = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else RUNS / "NIFTY_CE_5m_box278_q_trail.csv"
CE_FIXED = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else RUNS / "NIFTY_CE_5m_box278_q_fixed.csv"
PE_TRAIL = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else RUNS / "NIFTY_PE_5m_box278_q_trail.csv"

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
STYLE = HELPERS["recolour"](STYLE, "candle")
r, lakh, cls, curve_svg, spark = (HELPERS[k] for k in ("r", "lakh", "cls", "curve_svg", "spark"))


# ── data ─────────────────────────────────────────────────────────────
def load(path: pathlib.Path) -> list[dict]:
    """One row per campaign, as the sweep wrote them: already one campaign at a
    time (a new box high is taken only once the previous campaign has ended),
    so no de-overlapping is needed here. Campaigns that never bought, or that
    the archive could not price, are kept out of the book and counted."""
    rows = []
    with open(path) as fh:
        for row in csv.DictReader(fh):
            m = datetime.fromisoformat(row["mother"])
            legs = json.loads(row.get("legs") or "[]")
            exit_detail = json.loads(row.get("exit_detail") or "{}")
            rows.append(
                {
                    "mother": m,
                    "date": m.date(),
                    "buys": int(row.get("buys") or 0),
                    "lots": int(row.get("lots") or 0),
                    "deployed": float(row.get("deployed") or 0),
                    "gross": float(row["gross"]) if row.get("gross") else None,
                    "costs": float(row["costs"]) if row.get("costs") else None,
                    "net": float(row["net"]) if row.get("net") else None,
                    "exit_reason": str(row.get("reason") or ""),
                    "exit": row.get("exit") or "",
                    "lot": int(row.get("lot") or 0),
                    "strike": int(row.get("strike") or 0),
                    "expiry": row.get("expiry") or "",
                    "first_fill": row.get("first_fill") or "",
                    "legs": legs,
                    "exit_detail": exit_detail,
                    "unpriced": int(row.get("unpriced") or 0),
                }
            )
    rows.sort(key=lambda x: x["mother"])
    return rows


def book(all_rows: list[dict]) -> dict:
    rows = [x for x in all_rows if x["buys"] and x["net"] is not None]
    no_buy = [x for x in all_rows if not x["buys"]]
    unpriced = [x for x in all_rows if x["buys"] and x["net"] is None]
    net = sum(x["net"] for x in rows)
    gross = sum(x["gross"] for x in rows)
    costs = sum(x["costs"] for x in rows)
    wins = [x for x in rows if x["net"] > 0]
    losses = [x for x in rows if x["net"] < 0]
    gw = sum(x["net"] for x in wins)
    gl = -sum(x["net"] for x in losses)
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
    by_buys = defaultdict(lambda: {"net": 0.0, "trades": 0, "wins": 0})
    reasons = defaultdict(lambda: {"n": 0, "net": 0.0})
    hold_days = []
    for x in rows:
        by_month[(x["date"].year, x["date"].month)] += x["net"]
        for table, key in ((by_year, x["date"].year), (by_dow, x["date"].weekday()), (by_buys, x["buys"])):
            y = table[key]
            y["net"] += x["net"]
            y["trades"] += 1
            y["wins"] += x["net"] > 0
        rr = reasons[x["exit_reason"]]
        rr["n"] += 1
        rr["net"] += x["net"]
        if x["first_fill"] and x["exit"]:
            hold_days.append(
                (datetime.fromisoformat(x["exit"]) - datetime.fromisoformat(x["first_fill"])).total_seconds() / 86400
            )
    deps = sorted(x["deployed"] for x in rows)
    total_buys = sum(x["buys"] for x in rows)
    ranked = sorted(x["net"] for x in rows)
    return {
        "avg_deployed": round(sum(deps) / len(deps), 2) if deps else 0,
        "median_deployed": round(deps[len(deps) // 2], 2) if deps else 0,
        "peak_deployed": max(deps) if deps else 0,
        "per_buy": round(sum(deps) / total_buys, 2) if total_buys else 0,
        "trades": len(rows),
        "no_buy": len(no_buy),
        "unpriced": len(unpriced),
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
        "minus_best5": round(sum(ranked[:-5]), 2) if len(ranked) > 5 else None,
        "median_trade": round(ranked[len(ranked) // 2], 2) if ranked else 0,
        "best": max(rows, key=lambda x: x["net"]) if rows else None,
        "worst": min(rows, key=lambda x: x["net"]) if rows else None,
        "first": rows[0]["date"].isoformat() if rows else "",
        "last": rows[-1]["date"].isoformat() if rows else "",
        "avg_buys": round(total_buys / len(rows), 2) if rows else 0,
        "avg_hold_days": round(sum(hold_days) / len(hold_days), 1) if hold_days else 0,
        "max_hold_days": round(max(hold_days), 1) if hold_days else 0,
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
        "by_buys": {
            str(k): {"net": round(v["net"], 2), "trades": v["trades"], "wins": v["wins"]}
            for k, v in sorted(by_buys.items())
        },
        "reasons": {
            k: {"n": v["n"], "net": round(v["net"], 2)} for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]["n"])
        },
        "best10": sorted(rows, key=lambda x: -x["net"])[:10],
        "worst10": sorted(rows, key=lambda x: x["net"])[:10],
        "rows": rows,
    }


TRAIL_ROWS, FIXED_ROWS, PE_ROWS = load(CE_TRAIL), load(CE_FIXED), load(PE_TRAIL)
TRAIL, FIXED, PE = book(TRAIL_ROWS), book(FIXED_ROWS), book(PE_ROWS)

# Calendar months the walk spans, both ends counted: Oct 2024 -> the last
# mother. Typed as "22" it went stale when the book was extended (02-Oct-2026).
_y2, _m2 = (int(x) for x in TRAIL["last"][:7].split("-"))
MONTHS = (_y2 - 2024) * 12 + _m2 - 10 + 1
json.dump(
    {
        k: {kk: vv for kk, vv in b.items() if kk not in ("rows", "best10", "worst10", "best", "worst")}
        for k, b in (("CE_trail", TRAIL), ("CE_fixed", FIXED), ("PE_trail", PE))
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
      <div class="kpi-s">{t("after all charges", "அனைத்து கட்டணங்களுக்குப் பின்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Campaigns", "Campaign-கள்")}</div>
      <div class="kpi-v">{b["trades"]}</div>
      <div class="kpi-s">{b["wins"]} {t("won", "வெற்றி")} &middot; {b["losses"]} {t("lost", "நஷ்டம்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Win rate", "வெற்றி விகிதம்")}</div>
      <div class="kpi-v">{b["win_rate"]}%</div>
      <div class="kpi-s">{t("avg", "சராசரி")} {b["avg_buys"]} {t("buys a campaign", "வாங்கல்கள் / campaign")}</div></div>
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
      <div class="kpi-s">{t("win", "வெற்றி")} {r(b["avg_win"])} &middot; {t("loss", "நஷ்டம்")} {r(b["avg_loss"])}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Best &middot; worst campaign", "சிறந்த &middot; மோசமான campaign")}</div>
      <div class="kpi-v"><span class="pos">{r(b["best"]["net"]) if b["best"] else "—"}</span> &middot; <span class="neg">{r(b["worst"]["net"]) if b["worst"] else "—"}</span></div>
      <div class="kpi-s">{t("net after charges", "கட்டணங்களுக்குப் பின்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Deployed per campaign", "Campaign-க்கு பயன்பாடு")}</div>
      <div class="kpi-v">{r(b["avg_deployed"])}</div>
      <div class="kpi-s">{t("avg", "சராசரி")} &middot; {t("median", "இடைநிலை")} {r(b["median_deployed"])} &middot; {t("max", "அதிகபட்சம்")} {r(b["peak_deployed"])}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Held, first buy to exit", "வைத்திருந்த காலம்")}</div>
      <div class="kpi-v">{b["avg_hold_days"]} {t("days", "நாட்கள்")}</div>
      <div class="kpi-s">{t("avg", "சராசரி")} &middot; {t("longest", "நீளமானது")} {b["max_hold_days"]} {t("days", "நாட்கள்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Mothers that never traded", "வர்த்தகமே செய்யாத mother-கள்")}</div>
      <div class="kpi-v">{b["no_buy"]}</div>
      <div class="kpi-s">{t("the two reds never came", "இரண்டு சிவப்பு வரவில்லை")} &middot; {b["unpriced"]} {t("unpriced, kept out", "விலையில்லாதவை, வெளியே")}</div></div>
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
    <p>{t("Net after charges by the month the mother fired, thousands of rupees. Colour is the size relative to the largest month. A month with no campaign is a dot.", "Mother வந்த மாதவாரி நிகர, ஆயிரங்களில். நிறம் = பெரிய மாதத்துடன் ஒப்பீடு. Campaign இல்லாத மாதம் ஒரு புள்ளி.")}</p></div></div>
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


def reasons(b: dict) -> str:
    names = {
        "trail": ("Trail stop, after the target was touched", "இலக்கு தொட்ட பின் trail stop"),
        "target": ("Target reached (fixed)", "இலக்கு எட்டியது"),
        "expiry": ("Held to expiry, sold at 15:15", "Expiry வரை, 15:15-இல் விற்பனை"),
        "time_stop": ("Time stop", "Time stop"),
    }
    out = ""
    for k, v in b["reasons"].items():
        en, ta = names.get(k, (k.replace("_", " "), k.replace("_", " ")))
        out += f"<tr><th scope='row'>{t(en, ta)}</th><td>{v['n']}</td><td>{round(100 * v['n'] / b['trades'], 1) if b['trades'] else 0}%</td><td class='{cls(v['net'])}'><strong>{r(v['net'])}</strong></td></tr>"
    return out


def ten(rows: list[dict]) -> str:
    out = ""
    for x in rows:
        out += (
            f"<tr><td>{x['mother'].strftime('%d %b %Y %H:%M')}</td><td>{x['strike']} CE &middot; {x['expiry']}</td><td>{x['buys']} &middot; {x['lots']} {'lot' if x['lots'] == 1 else 'lots'}</td><td>{r(x['deployed'])}</td>"
            f"<td>{x['exit_reason'].replace('_', ' ')}</td><td class='{cls(x['net'])}'><strong>{r(x['net'])}</strong></td></tr>"
        )
    return out


def every_campaign(rows: list[dict]) -> str:
    out = ""
    for x in rows:
        legs = "<br>".join(
            f"<span class='leg-when'>{lg['t'][:16].replace('T', ' ')}</span> &middot; {lg['tf']} @ {r(lg['index'])} "
            + (
                f"(&#8377;{lg['premium']:.2f} &times; {lg['qty']}) &middot; {lg['strike']}"
                if lg.get("premium") is not None
                else f"(unpriced) &middot; {lg['strike']}"
            )
            for lg in x["legs"]
        )
        ex = x["exit_detail"] or {}
        exit_txt = (
            f"{x['exit'][:16].replace('T', ' ')} &middot; {x['exit_reason']} @ {r(ex.get('index_price') or 0)} (&#8377;{(ex.get('option_premium') or 0):.2f})"
            if x["exit"]
            else "—"
        )
        out += f"<tr><td>{x['mother'].strftime('%d %b %Y %H:%M')}</td><td>{x['strike']} &middot; {x['expiry']}</td><td style='white-space:normal;min-width:260px'>{legs}</td><td style='white-space:normal;min-width:220px'>{exit_txt}</td><td>{r(x['deployed'])}</td><td class='{cls(x['net'])}'><strong>{r(x['net'])}</strong></td></tr>"
    return out


def sizing_section(rows: list[dict], b: dict) -> str:
    """The family's capital table (tools/tearsheet/sizing.py), so all four sheets
    answer "how much does this need" the same way and from their own book."""
    from datetime import date as _d

    priced = [x for x in rows if x["gross"] is not None and x["costs"] is not None and x["deployed"]]
    if not priced:
        return ""
    first, last = priced[0]["date"], priced[-1]["date"]
    years = max((last - first).days / 365.25, 0.25)
    del _d
    scaled = [{"gross": x["gross"], "costs": x["costs"], "capital": x["deployed"]} for x in priced]
    # A campaign is up to three lots across its rungs, so a "multiple" here
    # multiplies the WHOLE ladder -- 2x means one-then-two becomes two-then-four.
    return sizing.section(
        sizing.scale(scaled, years=years),
        r=r,
        cls=cls,
        live_lots=1,
        anchor="capital-needed",
        note_en="A multiple here scales the WHOLE ladder: at 2x the first rung is two lots and the second is four. One campaign at a time, so the peak is a single campaign's deepest moment, never two overlapping.",
        note_ta="இங்கு ஒரு multiple முழு ladder-ஐயும் பெருக்குகிறது: 2x-இல் முதல் rung இரண்டு lots, இரண்டாவது நான்கு. ஒரே நேரத்தில் ஒரு campaign மட்டுமே.",
    )


def side_block(b: dict, name_en: str, name_ta: str, anchor: str) -> str:
    return f"""
{kpis(b, "The programme at a glance", "ஒரே பார்வையில்")}
<!--SPINE-->
{curve_section(b, f"{name_en} &mdash; cumulative curve", f"{name_ta} &mdash; ஒட்டுமொத்த வளைவு", f"curve-{anchor}")}
{heat(b, f"{name_en} &mdash; month by month", f"{name_ta} &mdash; மாதவாரியாக")}
<section>
  <div class="shead"><h2>{t(f"{name_en} &mdash; year by year", f"{name_ta} &mdash; ஆண்டுவாரியாக")}</h2></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Year", "ஆண்டு")}</th><th scope="col">{t("Campaigns", "Campaign-கள்")}</th><th scope="col">{t("Win rate", "வெற்றி %")}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{t("Per campaign", "ஒரு campaign-க்கு")}</th></tr></thead>
    <tbody>{rows_of(b["by_year"], lambda k: k)}</tbody></table></div>
</section>
<section>
  <div class="shead"><h2>{t(f"{name_en} &mdash; which day of the week pays", f"{name_ta} &mdash; வாரத்தின் எந்த நாள் லாபம் தருகிறது")}</h2></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Mother's weekday", "Mother கிழமை")}</th><th scope="col">{t("Campaigns", "Campaign-கள்")}</th><th scope="col">{t("Win rate", "வெற்றி %")}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{t("Per campaign", "ஒரு campaign-க்கு")}</th></tr></thead>
    <tbody>{rows_of(b["by_dow"], lambda k: t(k, DOW_TA[DOW.index(k)]))}</tbody></table></div>
  <div class="tblwrap" style="margin-top:12px"><table>
    <thead><tr><th scope="col">{t("Rungs bought", "வாங்கிய rungs")}</th><th scope="col">{t("Campaigns", "Campaign-கள்")}</th><th scope="col">{t("Win rate", "வெற்றி %")}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{t("Per campaign", "ஒரு campaign-க்கு")}</th></tr></thead>
    <tbody>{rows_of(b["by_buys"], lambda k: t(f"{k} of 3", f"3-இல் {k}"))}</tbody></table></div>
  <div class="tblwrap" style="margin-top:12px"><table>
    <thead><tr><th scope="col">{t("How the campaign ended", "Campaign எப்படி முடிந்தது")}</th><th scope="col">{t("Count", "எண்ணிக்கை")}</th><th scope="col">{t("Share", "பங்கு")}</th><th scope="col">{t("Net", "நிகர")}</th></tr></thead>
    <tbody>{reasons(b)}</tbody></table></div>
</section>
<section>
  <div class="shead"><h2>{t(f"{name_en} &mdash; best ten, worst ten", f"{name_ta} &mdash; சிறந்த பத்து, மோசமான பத்து")}</h2></div>
  <div class="two-up">
    <div class="tblwrap"><table>
      <thead><tr><th scope="col">{t("Mother", "Mother")}</th><th scope="col">{t("Contract", "ஒப்பந்தம்")}</th><th scope="col">{t("Buys", "வாங்கல்")}</th><th scope="col">{t("Deployed", "பயன்பாடு")}</th><th scope="col">{t("Ended by", "முடிவு")}</th><th scope="col">{t("Net", "நிகர")}</th></tr></thead>
      <tbody>{ten(b["best10"])}</tbody></table></div>
    <div class="tblwrap"><table>
      <thead><tr><th scope="col">{t("Mother", "Mother")}</th><th scope="col">{t("Contract", "ஒப்பந்தம்")}</th><th scope="col">{t("Buys", "வாங்கல்")}</th><th scope="col">{t("Deployed", "பயன்பாடு")}</th><th scope="col">{t("Ended by", "முடிவு")}</th><th scope="col">{t("Net", "நிகர")}</th></tr></thead>
      <tbody>{ten(b["worst10"])}</tbody></table></div>
  </div>
</section>"""


# ── the page ──────────────────────────────────────────────────────────
page = f"""<title>PhilForge Candle Entry Tearsheet</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
{STYLE}
.two-up {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(360px,1fr)); gap:12px; }}
table.heat td {{ text-align:right; font-variant-numeric:tabular-nums; }}
{sizing.SIZING_CSS}
/* Every buy says WHEN it went on, one line each (Phil, 2026-08-20). */
.leg-when {{ font-variant-numeric:tabular-nums; opacity:.72; }}
{LANG_CSS}
</style>

<section class="document-hero">
  <div class="hero-copy">
    <p class="eyebrow"><b>TEARSHEET</b>{
    t("PhilForge &middot; Cascade &middot; Candle Entry", "PhilForge &middot; Cascade &middot; Candle Entry")
}</p>
    <h1>{
    t(
        f"Candle Entry &mdash; Box Mother &middot; 5m &middot; CE &middot; Trailing &mdash; NIFTY, {MONTHS} Months",
        f"Candle Entry &mdash; Box Mother &middot; 5m &middot; CE &middot; Trailing &mdash; NIFTY, {MONTHS} மாதங்கள்",
    )
}</h1>
    <p class="lede">{
    t(
        "Phil's rule, measured as the page runs it. On the 5-minute chart the last 278 bars make a box; the <strong>mother</strong> is the bar that made the box high, and the ladder may buy only while price sits in the <strong>bottom quarter</strong> of that box. Two red closes stepping down put a buy-stop on the first red's close; the recovery is bought, 1 lot, and the second buy moves the watch one chart up &mdash; 5m &rarr; 15m, 2 lots &mdash; after a new low, and the ladder stops there. One NIFTY call, ATM&minus;2 on the monthly, for the whole ladder. The target is a quarter of the way from the average entry back to the mother's high; touching it arms a trail, and the basket is sold on a 5m close that gives back 30% of the gain. No stop loss, no time stop. One campaign at a time, and <strong>one mother, one trade</strong>: however a campaign ends, the next mother is the next bar to make a new 278-bar high after it. A mother that has been traded is finished. Real recorded premiums, 1 Oct 2024 to 17 Aug 2026. <strong>Puts lost under the same rule and are not traded.</strong>",
        "Phil-இன் விதி, பக்கம் இயக்குவது போலவே அளக்கப்பட்டது. 5-நிமிட chart-இல் கடைசி 278 bars ஒரு box; box-இன் high-ஐ உருவாக்கிய bar-தான் <strong>mother</strong>; விலை அந்த box-இன் <strong>கீழ் கால் பகுதியில்</strong> இருக்கும்போது மட்டுமே ladder வாங்கும். இறங்கும் இரண்டு சிவப்பு close முதல் சிவப்பின் close-இல் buy-stop வைக்கும்; மீட்சி வாங்கப்படும், 1 lot, இரண்டாவது வாங்கல் கண்காணிப்பை ஒரு chart மேலே நகர்த்தும் &mdash; 5m &rarr; 15m, 2 lots &mdash; புதிய low-க்குப் பின்; ladder அங்கேயே நிற்கும். முழு ladder-க்கும் ஒரே NIFTY கால், monthly-இல் ATM&minus;2. இலக்கு சராசரி நுழைவிலிருந்து mother-இன் high நோக்கி கால் பங்கு; அதைத் தொட்டதும் trail, லாபத்தில் 30% திரும்பக் கொடுக்கும் 5m close-இல் விற்பனை. Stop loss இல்லை, time stop இல்லை. ஒரு நேரத்தில் ஒரு campaign, <strong>ஒரு mother-க்கு ஒரு trade</strong>: campaign எப்படி முடிந்தாலும், அதற்குப் பின் புதிய 278-bar high செய்யும் அடுத்த bar-தான் அடுத்த mother. வர்த்தகம் செய்யப்பட்ட mother முடிந்தது. உண்மையான premium-கள், 1 அக் 2024 &ndash; 17 ஆக 2026. <strong>புட் அதே விதியில் நஷ்டம், வர்த்தகம் இல்லை.</strong>",
    )
}</p>
    <div class="document-meta" aria-label="Document metadata">
      <div class="meta-chip"><span>{t("Period", "காலம்")}</span><strong>{TRAIL["first"]} &rarr; {
    TRAIL["last"]
}</strong></div>
      <div class="meta-chip"><span>{t("Campaigns", "Campaign-கள்")}</span><strong>{TRAIL["trades"]} {
    t("priced", "விலையிடப்பட்டவை")
} &middot; {TRAIL["no_buy"]} {t("never traded", "வர்த்தகம் இல்லை")} &middot; {TRAIL["unpriced"]} {
    t("unpriced", "விலையில்லை")
}</strong></div>
      <div class="meta-chip"><span>{t("Side", "பக்கம்")}</span><strong>{
    t("CE only &mdash; PE not traded", "CE மட்டும் &mdash; PE இல்லை")
}</strong></div>
      <div class="meta-chip"><span>{t("Position size", "பொசிஷன் அளவு")}</span><strong>{
    t(
        "1 lot then 2 &middot; ATM&minus;2 monthly call",
        "1 lot, பின் 2 &middot; ATM&minus;2 monthly கால்",
    )
}</strong></div>
      <div class="meta-chip"><span>{t("Costs", "கட்டணங்கள்")}</span><strong>{
    t("Brokerage, STT, GST, stamp, per leg", "புரோக்கரேஜ், STT, GST, stamp, ஒவ்வொரு leg-க்கும்")
}</strong></div>
      <div class="meta-chip"><span>{t("Mothers", "Mother-கள்")}</span><strong>{
    t("every 278-bar high, not hand-picked", "ஒவ்வொரு 278-bar high-உம், கையால் தேர்வு இல்லை")
}</strong></div>
    </div>
  </div>
  <div class="system-sigil" aria-hidden="true">
    <div class="sigil-ring ring-one"></div><div class="sigil-ring ring-two"></div><div class="sigil-ring ring-three"></div>
    <div class="sigil-core">CE</div>
    <span class="sigil-label label-one">5m</span><span class="sigil-label label-two">278</span>
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
        "Same engine as the Cascade page &middot; time-ordered walk &middot; checked eleven ways, 0 failures",
        "Cascade பக்கத்தின் அதே engine &middot; நேர வரிசை நடை &middot; பதினொரு வழி சரிபார்ப்பு, 0 தோல்வி",
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
        "Every mother here is the high of the last 278 bars on the 5-minute chart &mdash; the rule picks it, not a trader &mdash; and campaigns are counted <strong>one at a time</strong>, exactly as the auto mother runs: a new box high that fires while a campaign is open is not taken, and a mother is never traded twice &mdash; whatever a campaign paid, the next one waits for a new high. A rung is <strong>never bought at or above the mother's own high</strong> &mdash; the target is measured back toward that high, so such a buy would be past its target on arrival &mdash; and a rung whose strike has no recorded price is re-struck <strong>at the money</strong>, then the lines either side of it, so a quiet minute does not blank a campaign's money. A campaign that never saw its two reds never traded and is not in the book (counted above). What the tables measure is the <strong>rule</strong>, on the exact code the paper run executes, walked candle by candle in time order with real recorded premiums. Whether <em>your</em> campaigns earn is what the paper run is for.",
        "இங்குள்ள ஒவ்வொரு mother-உம் 5-நிமிட chart-இல் புதிய 278-bar high செய்த bar &mdash; விதி தேர்வு செய்கிறது, trader அல்ல &mdash; campaign-கள் <strong>ஒரு நேரத்தில் ஒன்று</strong> என கணக்கிடப்பட்டன, பக்கம் இயங்குவது போலவே: முந்தைய campaign திறந்திருக்கும்போது வரும் புதிய box high எடுக்கப்படாது; ஒரு mother இரண்டாவது முறை வர்த்தகம் செய்யப்படுவதில்லை &mdash; campaign எவ்வளவு கொடுத்தாலும், அடுத்தது புதிய high-க்காகக் காத்திருக்கும். Mother-இன் சொந்த high-க்கு மேல் (அல்லது அதற்குச் சமமாக) எந்த rung-உம் வாங்கப்படுவதில்லை &mdash; இலக்கு அந்த high-ஐ நோக்கியே அளக்கப்படுவதால் அப்படி வாங்கினால் வந்தவுடனே இலக்கைத் தாண்டியிருக்கும்; ஒரு rung-இன் strike-க்கு விலை பதிவில்லையெனில் <strong>at the money</strong>-இல், பின் அதன் அருகிலுள்ள வரிகளில் மறு-strike. இரண்டு சிவப்பு வராத campaign வர்த்தகமே செய்யவில்லை, புத்தகத்தில் இல்லை (மேலே எண்ணப்பட்டது). அளக்கப்படுவது <strong>விதி</strong>, paper run இயக்கும் அதே code-இல், நேர வரிசையில், உண்மையான premium-களில். <em>உங்கள்</em> campaign-கள் சம்பாதிக்குமா என்பதுதான் paper run-இன் வேலை.",
    )
}</p>
  <p>{
    t(
        "Eleven independent checks were run before this was published: the backtest walk equals a tick-fed paper loop (1,285 checks); an audit written from the rule rather than the engine re-derives every mother, every fill, every premium and the money from the raw candle cache and the raw option archive (377 checks); a rerun is byte-identical; no campaign overlaps another; lot size follows the date; every expiry is 13&ndash;43 days out; no fill is priced outside its own bar; the muhurat session is excluded; every leg was repriced at the minute the index actually crossed its level and the trailing book came out <em>higher</em>, not lower; the same rule on puts, on 1m, with a bigger target, with a time stop or a stop loss was measured and lost. Zero failures.",
        "வெளியிடும் முன் பதினொரு சுயாதீன சரிபார்ப்புகள்: backtest நடை = tick-fed paper loop (1,285 சரிபார்ப்புகள்); engine-இல் அல்ல, விதியிலிருந்து எழுதப்பட்ட audit ஒவ்வொரு mother, fill, premium, பணத்தையும் மூல candle cache-இலும் மூல option archive-இலும் இருந்து மறு-வருவித்தது (377); மறு-இயக்கம் byte-அளவில் ஒன்றே; campaign-கள் மேற்பொருந்தவில்லை; lot தேதிப்படி; ஒவ்வொரு expiry-உம் 13&ndash;43 நாள்; எந்த fill-உம் அதன் bar-க்கு வெளியே விலையிடப்படவில்லை; muhurat அமர்வு நீக்கம்; ஒவ்வொரு leg-உம் index உண்மையில் கடந்த நிமிடத்தில் மறுவிலை, trailing புத்தகம் <em>உயர்ந்தது</em>; அதே விதி புட்-இல், 1m-இல், பெரிய இலக்குடன், time stop / stop loss-உடன் அளக்கப்பட்டு நஷ்டம். பூஜ்ஜியம் தோல்வி.",
    )
}</p>
</div>

{
    side_block(
        TRAIL,
        "NIFTY call book &mdash; &frac14; target, 30% trail (the rule the page runs)",
        "NIFTY கால் புத்தகம் &mdash; &frac14; இலக்கு, 30% trail (பக்கம் இயக்கும் விதி)",
        "trail",
    ).split("<!--SPINE-->")[0]
}
<section>
  <div class="shead"><div><h2>{t("Charges, in full", "கட்டணங்கள், முழுமையாக")}</h2>
    <p>{
    t(
        "Every rupee between the gross result and the account, per leg: brokerage, STT, exchange charges, SEBI fee, GST, stamp duty &mdash; the same schedule the paper engine books.",
        "மொத்த முடிவுக்கும் கணக்குக்கும் இடையிலான ஒவ்வொரு ரூபாயும், leg-க்கு: புரோக்கரேஜ், STT, exchange, SEBI, GST, stamp &mdash; paper engine பதியும் அதே அட்டவணை.",
    )
}</p></div></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Book", "புத்தகம்")}</th><th scope="col">{t("Gross", "மொத்தம்")}</th><th scope="col">{
    t("Charges", "கட்டணம்")
}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{
    t("Charges per campaign", "campaign-க்கு கட்டணம்")
}</th></tr></thead>
    <tbody>
      <tr class="trow-total"><th scope="row">NIFTY CE &middot; {t("trail", "trail")}</th><td class="{
    cls(TRAIL["gross"])
}">{r(TRAIL["gross"])}</td><td class="neg">{r(-TRAIL["costs"])}</td><td class="{cls(TRAIL["net"])}"><strong>{
    r(TRAIL["net"])
}</strong></td><td>{r(TRAIL["costs"] / TRAIL["trades"]) if TRAIL["trades"] else "—"}</td></tr>
      <tr><th scope="row">NIFTY CE &middot; {t("fixed", "fixed")}</th><td class="{cls(FIXED["gross"])}">{
    r(FIXED["gross"])
}</td><td class="neg">{r(-FIXED["costs"])}</td><td class="{cls(FIXED["net"])}"><strong>{
    r(FIXED["net"])
}</strong></td><td>{r(FIXED["costs"] / FIXED["trades"]) if FIXED["trades"] else "—"}</td></tr>
    </tbody></table></div>
</section>
{
    HELPERS["cycle_section"](
        HELPERS["daily_series"](TRAIL["rows"], lambda x: x["mother"], lambda x: x["net"]),
        t,
        r,
        noun_en="campaigns",
        noun_ta="Campaign-கள்",
    )
}
{
    HELPERS["daily_ledger"](
        HELPERS["daily_series"](TRAIL["rows"], lambda x: x["mother"], lambda x: x["net"]),
        t,
        t_attr,
        r,
        cls,
        noun_en="campaigns",
        noun_ta="Campaign-கள்",
    )
}

{
    side_block(
        TRAIL,
        "NIFTY call book &mdash; &frac14; target, 30% trail (the rule the page runs)",
        "NIFTY கால் புத்தகம் &mdash; &frac14; இலக்கு, 30% trail (பக்கம் இயக்கும் விதி)",
        "trail",
    ).split("<!--SPINE-->")[1]
}

<section>
  <div class="shead"><div><h2>{
    t("The same rule with a fixed target, for comparison", "ஒப்பீட்டுக்கு: அதே விதி, fixed இலக்கு")
}</h2>
    <p>{
    t(
        "Everything identical except the exit: the basket is sold the moment the index touches the quarter-way target. It is the page's Fixed switch.",
        "வெளியேற்றம் மட்டும் வேறு: கால்-பங்கு இலக்கைத் தொட்டதும் விற்பனை. பக்கத்தின் Fixed switch.",
    )
}</p></div></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Exit", "வெளியேற்றம்")}</th><th scope="col">{
    t("Campaigns", "Campaign-கள்")
}</th><th scope="col">{t("Win rate", "வெற்றி %")}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{
    t("Minus best five", "சிறந்த ஐந்து நீக்கி")
}</th><th scope="col">{t("Max drawdown", "அதிகபட்ச இறக்கம்")}</th><th scope="col">{
    t("Profit factor", "லாப காரணி")
}</th><th scope="col">{t("Avg held", "சராசரி காலம்")}</th></tr></thead>
    <tbody>
      <tr class="trow-total"><th scope="row">{
    t("&frac14; target &rarr; 30% trail", "&frac14; இலக்கு &rarr; 30% trail")
}</th><td>{TRAIL["trades"]}</td><td>{TRAIL["win_rate"]}%</td><td class="{cls(TRAIL["net"])}"><strong>{
    r(TRAIL["net"])
}</strong></td><td class="{cls(TRAIL["minus_best5"] or 0)}">{r(TRAIL["minus_best5"] or 0)}</td><td class="neg">{
    r(TRAIL["max_dd"])
}</td><td>{TRAIL["profit_factor"]}</td><td>{TRAIL["avg_hold_days"]} {t("days", "நாட்கள்")}</td></tr>
      <tr><th scope="row">{t("&frac14; target, sold at the touch", "&frac14; இலக்கு, தொட்டதும் விற்பனை")}</th><td>{
    FIXED["trades"]
}</td><td>{FIXED["win_rate"]}%</td><td class="{cls(FIXED["net"])}"><strong>{r(FIXED["net"])}</strong></td><td class="{
    cls(FIXED["minus_best5"] or 0)
}">{r(FIXED["minus_best5"] or 0)}</td><td class="neg">{r(FIXED["max_dd"])}</td><td>{FIXED["profit_factor"]}</td><td>{
    FIXED["avg_hold_days"]
} {t("days", "நாட்கள்")}</td></tr>
    </tbody></table></div>
</section>

<div class="note note-warn">
  <h2 class="note-h">{t("The put book is NOT traded", "புட் புத்தகம் வர்த்தகம் செய்யப்படுவதில்லை")}</h2>
  <p>{
    t(
        f"The same rule in a mirror &mdash; the mother is the bar that makes the 278-bar LOW, two GREEN closes stepping up, a sell-stop on the first green's close, a put bought when price breaks back down, the box asking for the top quarter, ATM+2 at each buy, the same one-mother-one-trade rule &mdash; was replayed over the same {MONTHS} months: {PE['trades']} campaigns, {PE['wins']} won / {PE['losses']} lost ({PE['win_rate']}%), net {r(PE['net'])}, minus the best five {r(PE['minus_best5'] or 0)}, worst drawdown {r(PE['max_dd'])}. "
        + (
            "It loses outright."
            if PE["net"] <= 0
            else "Whatever its headline net, it fails the test every book here must pass: without its five best campaigns it is a loss, and its drawdown is many times the call book's."
        )
        + " Every put setting measured (the midpoint, 15m, 1H, fixed exit) was the same or worse. This sheet, and the page, is calls only.",
        f"அதே விதி கண்ணாடியில் &mdash; 278-bar LOW-ஐ உருவாக்கிய bar mother, மேலேறும் இரண்டு பச்சை close, முதல் பச்சையின் close-இல் sell-stop, விலை கீழே உடைந்தால் புட், box-இன் மேல் கால் பகுதி, ஒவ்வொரு வாங்கலிலும் ATM+2, அதே ஒரு-mother-ஒரு-trade விதி &mdash; அதே {MONTHS} மாதங்களில்: {PE['trades']} campaign-கள், {PE['wins']} வெற்றி / {PE['losses']} நஷ்டம் ({PE['win_rate']}%), நிகர {r(PE['net'])}, சிறந்த ஐந்து நீக்கி {r(PE['minus_best5'] or 0)}, அதிகபட்ச இறக்கம் {r(PE['max_dd'])}. "
        + (
            "அது நேரடியாக நஷ்டம்."
            if PE["net"] <= 0
            else "தலைப்பு நிகர எதுவாயினும், இங்குள்ள ஒவ்வொரு புத்தகமும் கடக்க வேண்டிய சோதனையில் தோல்வி: சிறந்த ஐந்து இல்லாமல் அது நஷ்டம், இறக்கம் கால் புத்தகத்தின் பல மடங்கு."
        )
        + " அளந்த ஒவ்வொரு புட் அமைப்பும் அதே அல்லது மோசம். இந்த அறிக்கையும் பக்கமும் கால் மட்டும்.",
    )
}</p>
</div>

{sizing_section(TRAIL_ROWS, TRAIL)}

<section>
  <div class="shead"><div><h2>{
    t("Capital at work &mdash; what one campaign costs", "பயன்பாட்டில் மூலதனம் &mdash; ஒரு campaign-இன் செலவு")
}</h2>
    <p>{
    t(
        "Every buy is paid in full (no margin). The first rung is one lot of the ATM&minus;2 monthly call and the second is two lots &mdash; a fully built ladder holds three, and the ladder stops there. The deployed figures below sum what each campaign actually paid: the median is what a typical one needs, the maximum is what the deepest one needed and therefore the capital this rule asks you to have.",
        "ஒவ்வொரு வாங்கலும் முழு premium (margin இல்லை). முதல் rung ATM&minus;2 monthly கால்-இன் ஒரு lot, இரண்டாவது இரண்டு &mdash; முழு ladder மூன்று lots, அத்துடன் முடிவு. கீழே உள்ள எண்கள் ஒவ்வொரு campaign-உம் உண்மையில் செலுத்தியது: நடுநிலை வழக்கமானது, அதிகபட்சம் ஆழமானது &mdash; அதுவே இந்த விதி கேட்கும் மூலதனம்.",
    )
}</p></div></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Book", "புத்தகம்")}</th><th scope="col">{
    t("Campaigns", "Campaign-கள்")
}</th><th scope="col">{t("Buys per campaign", "வாங்கல்/campaign")}</th><th scope="col">{
    t("Cost per buy, avg", "ஒரு வாங்கல், சராசரி")
}</th><th scope="col">{t("Deployed per campaign, avg", "பயன்பாடு / campaign, சராசரி")}</th><th scope="col">{
    t("Median", "இடைநிலை")
}</th><th scope="col">{t("Max", "அதிகபட்சம்")}</th><th scope="col">{t("Net", "நிகர")}</th><th scope="col">{
    t("Net &divide; max deployed", "நிகர &divide; அதிகபட்ச பயன்பாடு")
}</th></tr></thead>
    <tbody>
      <tr class="trow-total"><th scope="row">NIFTY CE &middot; {t("trail", "trail")}</th><td>{TRAIL["trades"]}</td><td>{
    TRAIL["avg_buys"]
}</td><td>{r(TRAIL["per_buy"])}</td><td>{r(TRAIL["avg_deployed"])}</td><td>{r(TRAIL["median_deployed"])}</td><td>{
    r(TRAIL["peak_deployed"])
}</td><td class="{cls(TRAIL["net"])}"><strong>{r(TRAIL["net"])}</strong></td><td>{
    round(100 * TRAIL["net"] / TRAIL["peak_deployed"], 1) if TRAIL["peak_deployed"] else "—"
}%</td></tr>
      <tr><th scope="row">NIFTY CE &middot; {t("fixed", "fixed")}</th><td>{FIXED["trades"]}</td><td>{
    FIXED["avg_buys"]
}</td><td>{r(FIXED["per_buy"])}</td><td>{r(FIXED["avg_deployed"])}</td><td>{r(FIXED["median_deployed"])}</td><td>{
    r(FIXED["peak_deployed"])
}</td><td class="{cls(FIXED["net"])}"><strong>{r(FIXED["net"])}</strong></td><td>{
    round(100 * FIXED["net"] / FIXED["peak_deployed"], 1) if FIXED["peak_deployed"] else "—"
}%</td></tr>
    </tbody></table></div>
</section>


<section>
  <div class="shead"><h2>{t("Risk register", "ரிஸ்க் பதிவேடு")}</h2></div>
  <p>{
    t(
        "On the 22-month walk to August 2026 (measured then, not re-run since): puts lose (above); a mother picked at fixed clock times instead of the box high loses on every chart, with or without a fall filter; 1m loses badly (278 one-minute bars is a day and a half, so the box high is just yesterday's high); 15m and 1H produce 3 to 15 campaigns in two years &mdash; too few to mean anything; a target further back than a quarter loses its robustness at once and its drawdown grows thirty-fold; a time stop of 1 to 5 days only turns winners into losses (the slowest campaigns pay the most); a stop loss on the premium did not help; weekly expiry is worse than monthly everywhere. Around the chosen setting the result holds at 278&ndash;350 bars and the bottom fifth to quarter, and falls off at the 30% line and at 200 bars &mdash; it is a hill, not a plateau, and that is stated rather than hidden. This sheet is only the configuration that is switched on.",
        "ஆகஸ்ட் 2026 வரையிலான 22-மாத நடையில் (அப்போது அளந்தது, மீண்டும் ஓட்டப்படவில்லை): புட் நஷ்டம் (மேலே); box high-க்குப் பதில் நிலையான நேரங்களில் mother எல்லா chart-இலும் நஷ்டம்; 1m கடும் நஷ்டம்; 15m, 1H இரண்டு ஆண்டுகளில் 3&ndash;15 campaign-கள் மட்டும்; கால்-பங்கை விடப் பெரிய இலக்கு உறுதியை இழக்கிறது; 1&ndash;5 நாள் time stop வெற்றிகளை நஷ்டமாக்குகிறது; premium stop loss உதவவில்லை; weekly expiry எங்கும் monthly-ஐ விட மோசம். தேர்ந்த அமைப்பைச் சுற்றி 278&ndash;350 bars, கீழ் ஐந்தில்-ஒன்று முதல் கால் வரை நிற்கிறது; 30% கோட்டிலும் 200 bars-இலும் வீழ்கிறது &mdash; அது ஒரு மலை, சமவெளி அல்ல; மறைக்காமல் சொல்லப்பட்டுள்ளது. இந்த அறிக்கை இயக்கத்தில் உள்ள அமைப்பு மட்டுமே.",
    )
}</p>
</section>


{
    HELPERS["method_and_limits"](
        t,
        [
            ('The mother is the 5-minute bar that makes a 278-bar high, taken blind: it is whichever bar qualifies, never one picked by hand.', 'Mother என்பது 278-bar உயர்வை உருவாக்கும் 5-நிமிட bar; கையால் தேர்ந்தெடுக்கப்படுவதில்லை.'),
            ("Buying happens only in the bottom quarter of that box, on two red closes stepping down and then a buy-stop on the first red's close. At most four buys a round.", 'அந்த box-இன் கீழ் கால்பங்கில் மட்டுமே வாங்கல்; இரு சிவப்பு இறக்கம், பின் முதல் சிவப்பின் close-இல் buy-stop. ஒரு round-க்கு அதிகபட்சம் நான்கு.'),
            ('Premiums are recorded minutes from the local archive, zero broker calls. A campaign that cannot be priced is left OUT of the book rather than valued at a guess.', 'பிரீமியங்கள் உள்ளூர் காப்பகத்தின் பதிவான நிமிடங்கள்; broker அழைப்பு இல்லை. விலை தர முடியாத campaign ஊகத்தில் மதிப்பிடாமல் புத்தகத்திற்கு வெளியே விடப்படுகிறது.'),
            ("Charges are the full statutory schedule per round, and the lot is the one in force on the contract's own expiry &mdash; 25, then 75, then 65.", 'கட்டணங்கள் முழு சட்டப்பூர்வ பட்டியல்; lot என்பது contract-இன் expiry-இல் அமலில் இருந்தது &mdash; 25, பின் 75, பின் 65.'),
        ],
        running=('Candle Entry runs on its own tab behind a live gate that is still shut. This document is the recorded book; nothing here has traded real money.', 'Candle Entry அதன் சொந்த tab-இல், இன்னும் மூடிய நேரடி gate-இன் பின்னால். இந்த ஆவணம் பதிவான புத்தகம்; இதில் எதுவும் உண்மையான பணத்தில் வர்த்தகம் ஆகவில்லை.'),
    )
}
</article>
</div>
{CHART_JS_SRC.replace("__SERIES__", json.dumps(HELPERS["daily_series"](TRAIL["rows"], lambda x: x["mother"], lambda x: x["net"]), separators=(",", ":")))}
{READER_JS}
{LANG_JS}
"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(page)
print("wrote", OUT, len(page), "bytes")
print("TRAIL", TRAIL["trades"], TRAIL["net"], "FIXED", FIXED["trades"], FIXED["net"], "PE", PE["trades"], PE["net"])
