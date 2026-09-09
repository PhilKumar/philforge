"""Emit the Gap Carry tearsheet -- one candle at 15:10, one contract, one night.

Fourth of the family (build_report.py is the five-year options sheet,
build_fib_report.py the Fib Boundary sheet, build_candle_report.py the Candle
Entry one). It borrows the parent document's stylesheet, helpers, reader
chrome and bilingual toggle verbatim -- read from build_report.py at run time,
never copied -- so the four read as one family on the Assets page.

Every figure comes from a replay of the exact rule the Gap Carry tab trades: at
15:10 read the last closed 5m candle, a close above its EMA20 with RSI(14) at or
over 70 buys an ATM+4 ITM call, a close below with RSI at or under 30 buys the
put; the nearest weekly that survives the night; cut at 09:15 if it opens below
what it cost, otherwise sold at 09:20.
NIFTY, 2021-01-05 -> 2026-07-09, one lot, real recorded premiums from two
archives, lot size by expiry date. Nothing here is typed by hand.

The replay this reads reproduces through engine/gap_carry.py to the rupee, which
is the only reason a sheet may quote it.

    python3 tools/tearsheet/build_gapcarry_report.py [book.csv]
"""

from __future__ import annotations

import csv
import json
import pathlib
import re
import statistics
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sizing  # noqa: E402
from i18n import LANG_CSS, LANG_JS, t, t_attr  # noqa: E402

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
OUT = _REPO / "docs" / "assets" / "gap-carry-tearsheet.html"
RUNS = _REPO / "tools" / "gapcarry_offline" / "runs"
BOOK = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else RUNS / "NIFTY_5m_rsi70_atm4.csv"

MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DOW_TA = ["திங்", "செவ்", "புத", "வியா", "வெள்", "சனி", "ஞாயி"]


# ── the parent document's helpers, stylesheet and reader chrome, borrowed ──
def _borrow():
    src = (_HERE / "build_report.py").read_text()
    helpers: dict = {}
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
    # survives into the borrowed copy and would render as a literal.
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
STYLE = HELPERS["recolour"](STYLE, "gapcarry")
r, lakh, cls, curve_svg, spark = (HELPERS[k] for k in ("r", "lakh", "cls", "curve_svg", "spark"))


# ── data ──────────────────────────────────────────────────────────────
def load(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"no replay at {path}; run the Gap Carry sweep first")
    out = []
    with path.open() as fh:
        for row in csv.DictReader(fh):
            f = lambda k: float(row[k]) if row.get(k) not in (None, "") else None  # noqa: E731
            out.append(
                {
                    "session": date.fromisoformat(row["session"]),
                    "exit_session": date.fromisoformat(row["exit_session"]) if row.get("exit_session") else None,
                    "side": row["side"],
                    "strike": int(float(row["strike"])),
                    "expiry": row["expiry"],
                    "lot": int(float(row["lot"])),
                    "rsi": f("rsi"),
                    "close": f("close"),
                    "ema": f("ema"),
                    "entry_spot": f("entry_spot"),
                    "exit_spot": f("exit_spot"),
                    "entry_premium": f("entry_premium"),
                    "exit_premium": f("exit_premium"),
                    "priced": str(row.get("priced", "")).strip().lower() in {"true", "1", "yes"},
                    "capital": f("capital") or 0.0,
                    "charges": f("charges") or 0.0,
                    "net": f("net") or 0.0,
                    # gross BEFORE charges, which is what re-costing scales
                    "gross": (f("net") or 0.0) + (f("charges") or 0.0),
                }
            )
    out.sort(key=lambda x: x["session"])
    return out


def book(rows: list[dict]) -> dict:
    nets = [x["net"] for x in rows]
    wins = [x for x in rows if x["net"] > 0]
    losses = [x for x in rows if x["net"] <= 0]
    gross_won, gross_lost = sum(x["net"] for x in wins), -sum(x["net"] for x in losses)

    equity = peak = 0.0
    max_dd, dd_from, dd_to, peak_at = 0.0, None, None, None
    curve = []
    for x in rows:
        equity += x["net"]
        if equity > peak:
            peak, peak_at = equity, x["session"]
        if equity - peak < max_dd:
            max_dd, dd_from, dd_to = equity - peak, peak_at, x["session"]
        curve.append((x["session"].isoformat(), equity))

    by_month: dict = defaultdict(float)
    by_year: dict = {}
    by_dow: dict = {}
    by_side: dict = {}
    by_rsi: dict = {}
    for x in rows:
        by_month[f"{x['session'].year}-{x['session'].month:02d}"] += x["net"]
        for table, key in (
            (by_year, x["session"].year),
            (by_dow, x["session"].weekday()),
            (by_side, x["side"]),
            (by_rsi, _rsi_bucket(x)),
        ):
            cell = table.setdefault(key, {"trades": 0, "wins": 0, "net": 0.0})
            cell["trades"] += 1
            cell["wins"] += 1 if x["net"] > 0 else 0
            cell["net"] += x["net"]

    floored = [x for x in rows if not x["priced"]]
    best5 = sorted(nets, reverse=True)[:5]
    gaps = [
        x["exit_spot"] - x["entry_spot"] for x in rows if x["exit_spot"] is not None and x["entry_spot"] is not None
    ]
    held = [(x["exit_session"] - x["session"]).days for x in rows if x["exit_session"]]

    return {
        "rows": rows,
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "net": sum(nets),
        "gross_won": gross_won,
        "gross_lost": gross_lost,
        "win_rate": round(100 * len(wins) / len(rows), 1) if rows else 0,
        "profit_factor": (gross_won / gross_lost) if gross_lost else None,
        "avg_trade": (sum(nets) / len(nets)) if nets else 0,
        "median_trade": statistics.median(nets) if nets else 0,
        "avg_win": (sum(x["net"] for x in wins) / len(wins)) if wins else 0,
        "avg_loss": (sum(x["net"] for x in losses) / len(losses)) if losses else 0,
        "best": max(rows, key=lambda x: x["net"]) if rows else None,
        "worst": min(rows, key=lambda x: x["net"]) if rows else None,
        "max_dd": max_dd,
        "dd_from": dd_from.isoformat() if dd_from else "—",
        "dd_to": dd_to.isoformat() if dd_to else "—",
        "return_over_dd": (sum(nets) / abs(max_dd)) if max_dd else None,
        "minus_best5": (sum(nets) - sum(best5)) if nets else None,
        "best5": sum(best5),
        "curve": curve,
        "by_month": dict(by_month),
        "by_year": dict(sorted(by_year.items())),
        "by_dow": dict(sorted(by_dow.items())),
        "by_side": dict(sorted(by_side.items())),
        "by_rsi": dict(sorted(by_rsi.items())),
        "avg_deployed": (sum(x["capital"] for x in rows) / len(rows)) if rows else 0,
        "median_deployed": statistics.median([x["capital"] for x in rows]) if rows else 0,
        "peak_deployed": max((x["capital"] for x in rows), default=0),
        "charges": sum(x["charges"] for x in rows),
        "avg_charges": (sum(x["charges"] for x in rows) / len(rows)) if rows else 0,
        "gross_before_charges": sum(nets) + sum(x["charges"] for x in rows),
        "floored": len(floored),
        "floored_net": sum(x["net"] for x in floored),
        "avg_gap": (sum(gaps) / len(gaps)) if gaps else 0,
        "avg_hold_days": round(sum(held) / len(held), 2) if held else 0,
        "max_hold_days": max(held) if held else 0,
        "lot_eras": _lot_eras(rows),
        "years": ((rows[-1]["session"] - rows[0]["session"]).days / 365.25) if len(rows) > 1 else 1.0,
        "months_green": sum(1 for v in by_month.values() if v > 0),
        "months_total": len(by_month),
        "top_months": sorted(by_month.items(), key=lambda kv: -kv[1])[:3],
        "first": rows[0]["session"].isoformat() if rows else "—",
        "last": rows[-1]["session"].isoformat() if rows else "—",
    }


def _lot_eras(rows: list[dict]) -> list[dict]:
    """Contiguous stretches of one lot size, in the order they were traded.

    NIFTY's lot is not a constant and it is keyed to the EXPIRY, not the trade
    date. Over this window it is 75, then 50, then 25, then 75 again, then 65 --
    so "one lot" was 7,770 rupees of premium at one point in this book and
    33,304 at another. Grouping by size alone would merge the two 75 eras three
    years apart and hide exactly that.
    """
    eras: list[dict] = []
    for x in rows:
        if eras and eras[-1]["lot"] == x["lot"]:
            era = eras[-1]
        else:
            era = {"lot": x["lot"], "n": 0, "net": 0.0, "cap": 0.0, "first": x["session"], "last": x["session"]}
            eras.append(era)
        era["n"] += 1
        era["net"] += x["net"]
        era["cap"] = max(era["cap"], x["capital"])
        era["last"] = x["session"]
    return eras


def _rsi_bucket(x: dict) -> str:
    """The reading that fired it, in bands -- the one setting with a live edge."""
    v = x["rsi"]
    if v is None:
        return "—"
    if x["side"] == "PE":
        return "PE &le;30" if v <= 30 else "PE 30&ndash;35"
    if v >= 80:
        return "CE &ge;80"
    if v >= 75:
        return "CE 75&ndash;80"
    return "CE 70&ndash;75"


# ── sections ──────────────────────────────────────────────────────────
def kpis(b: dict) -> str:
    pf = "—" if b["profit_factor"] is None else f"{b['profit_factor']:.2f}"
    rod = "—" if b["return_over_dd"] is None else f"{b['return_over_dd']:.2f}&times;"
    m5 = "—" if b["minus_best5"] is None else r(b["minus_best5"])
    return f"""
<section id="glance">
  <div class="shead"><h2>{t("The programme at a glance", "ஒரே பார்வையில்")}</h2></div>
  <div class="kpis">
    <div class="kpi"><div class="kpi-l">{t("Net profit", "நிகர லாபம்")}</div>
      <div class="kpi-v {cls(b["net"])}">{r(b["net"])}</div>
      <div class="kpi-s">{t("after all charges", "அனைத்து கட்டணங்களுக்குப் பின்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Nights", "இரவுகள்")}</div>
      <div class="kpi-v">{b["trades"]}</div>
      <div class="kpi-s">{b["wins"]} {t("won", "வெற்றி")} &middot; {b["losses"]} {t("lost", "நஷ்டம்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Win rate", "வெற்றி விகிதம்")}</div>
      <div class="kpi-v">{b["win_rate"]}%</div>
      <div class="kpi-s">{t("one contract, every night it fires", "ஒரு contract, ஒவ்வொரு இரவும்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Profit factor", "லாப காரணி")}</div>
      <div class="kpi-v">{pf}</div>
      <div class="kpi-s">{r(b["gross_won"])} &divide; {r(b["gross_lost"])}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Max drawdown", "அதிகபட்ச இறக்கம்")}</div>
      <div class="kpi-v neg">{r(b["max_dd"])}</div>
      <div class="kpi-s">{b["dd_from"]} &rarr; {b["dd_to"]}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Return / drawdown", "வருவாய் / இறக்கம்")}</div>
      <div class="kpi-v">{rod}</div>
      <div class="kpi-s">{t("profit per rupee of worst dip", "மோசமான இறக்கத்தின் ஒரு ரூபாய்க்கு லாபம்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Minus the best five", "சிறந்த ஐந்து நீக்கி")}</div>
      <div class="kpi-v {cls(b["minus_best5"] or 0)}">{m5}</div>
      <div class="kpi-s">{t("the five biggest nights are", "ஐந்து பெரிய இரவுகள்")} {r(b["best5"])}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Average &middot; median night", "சராசரி &middot; இடைநிலை இரவு")}</div>
      <div class="kpi-v {cls(b["avg_trade"])}">{r(b["avg_trade"])} &middot; {r(b["median_trade"])}</div>
      <div class="kpi-s">{t("win", "வெற்றி")} {r(b["avg_win"])} &middot; {t("loss", "நஷ்டம்")} {r(b["avg_loss"])}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Best &middot; worst night", "சிறந்த &middot; மோசமான இரவு")}</div>
      <div class="kpi-v"><span class="pos">{r(b["best"]["net"]) if b["best"] else "—"}</span> &middot; <span class="neg">{r(b["worst"]["net"]) if b["worst"] else "—"}</span></div>
      <div class="kpi-s">{t("net after charges", "கட்டணங்களுக்குப் பின்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Capital per night", "இரவுக்கு மூலதனம்")}</div>
      <div class="kpi-v">{r(b["avg_deployed"])}</div>
      <div class="kpi-s">{t("median", "இடைநிலை")} {r(b["median_deployed"])} &middot; {t("max", "அதிகபட்சம்")} {r(b["peak_deployed"])}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Held", "வைத்திருந்த காலம்")}</div>
      <div class="kpi-v">{b["avg_hold_days"]} {t("days", "நாட்கள்")}</div>
      <div class="kpi-s">{t("one night, but a Friday is three", "ஒரு இரவு, வெள்ளி என்றால் மூன்று")} &middot; {t("longest", "நீளமானது")} {b["max_hold_days"]}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Floored at intrinsic", "உள்ளார்ந்த மதிப்பில்")}</div>
      <div class="kpi-v">{b["floored"]}</div>
      <div class="kpi-s">{r(b["floored_net"])} {t("of the net &middot; a floor is not a price", "நிகரத்தில் &middot; தளம் ஒரு விலை அல்ல")}</div></div>
  </div>
</section>"""


def curve_section(b: dict) -> str:
    if len(b["curve"]) < 2:
        return ""
    line, area, dd, zero, hi, lo = curve_svg(b["curve"])
    return f"""
<section id="curve-gapcarry">
  <div class="shead"><div><h2>{t("Cumulative curve", "ஒட்டுமொத்த வளைவு")}</h2>
    <p>{t("Cumulative net after charges, one point per night, in the order they were taken. Shading marks every stretch spent below the previous high.", "கட்டணங்களுக்குப் பின் திரட்டு நிகர, ஒரு இரவுக்கு ஒரு புள்ளி, எடுத்த வரிசையில். நிழல் = முந்தைய உச்சத்துக்குக் கீழே.")}</p></div></div>
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


def heat(b: dict) -> str:
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
<section id="months">
  <div class="shead"><div><h2>{t("Month by month", "மாதவாரியாக")}</h2>
    <p>{t("Net after charges by the month the candle was read, thousands of rupees. Colour is the size relative to the largest month. A month the rule never fired in is a dot.", "Candle படித்த மாதவாரி நிகர, ஆயிரங்களில். நிறம் = பெரிய மாதத்துடன் ஒப்பீடு. விதி செயல்படாத மாதம் ஒரு புள்ளி.")}</p></div></div>
  <div class="tblwrap"><table class="heat">
    <thead><tr><th scope="col">{t("Year", "ஆண்டு")}</th>{head}<th scope="col">{t("Total", "மொத்தம்")}</th></tr></thead>
    <tbody>{body}</tbody></table></div>
</section>"""


def _rows_of(table: dict, label_fn) -> str:
    out = ""
    for k, v in table.items():
        wr = round(100 * v["wins"] / v["trades"], 1) if v["trades"] else 0
        avg = r(v["net"] / v["trades"]) if v["trades"] else "—"
        out += (
            f"<tr><th scope='row'>{label_fn(k)}</th><td>{v['trades']}</td><td>{wr}%</td>"
            f"<td class='{cls(v['net'])}'><strong>{r(v['net'])}</strong></td><td>{avg}</td></tr>"
        )
    return out


def cuts(b: dict) -> str:
    head = (
        f"<thead><tr><th scope='col'></th><th scope='col'>{t('Nights', 'இரவுகள்')}</th>"
        f"<th scope='col'>{t('Won', 'வெற்றி')}</th><th scope='col'>{t('Net', 'நிகர')}</th>"
        f"<th scope='col'>{t('Avg', 'சராசரி')}</th></tr></thead>"
    )
    fri = b["by_dow"].get(4, {"net": 0.0, "trades": 0})
    thu = b["by_dow"].get(3, {"net": 0.0, "trades": 0})
    share = (fri["net"] / b["net"] * 100) if b["net"] else 0
    return f"""
<section id="years">
  <div class="shead"><div><h2>{t("Year by year", "ஆண்டுவாரியாக")}</h2>
    <p>{
        t(
            "The same book cut several ways, starting with the year. Nothing here is a separate run — every night appears once in each table.",
            "அதே புத்தகம் பல வழிகளில், ஆண்டில் தொடங்கி. இங்கு தனி ரன் இல்லை — ஒவ்வொரு இரவும் ஒவ்வொரு அட்டவணையிலும் ஒரு முறை.",
        )
    }</p></div></div>
  <div class="tblwrap"><table>{head}<tbody>{_rows_of(b["by_year"], lambda k: str(k))}</tbody></table></div>
</section>
<!--CUT-->
<section id="cuts">
  <div class="shead"><div><h2>{t("Which day of the week pays", "வாரத்தின் எந்த நாள் லாபம் தருகிறது")}</h2></div></div>
  <div class="split">
    <div class="tblwrap"><table>{head}<tbody>{
        _rows_of(b["by_dow"], lambda k: t(DOW[k], DOW_TA[k]))
    }</tbody></table></div>
    <div class="tblwrap"><table>{head}<tbody>{
        _rows_of(b["by_side"], lambda k: "Call (CE)" if k == "CE" else "Put (PE)")
    }</tbody></table></div>
  </div>
  <div class="split">
    <div class="tblwrap"><table>{head}<tbody>{_rows_of(b["by_rsi"], lambda k: k)}</tbody></table></div>
  </div>
  <p class="note note-warn"><strong>{t("Friday alone is", "வெள்ளி மட்டும்")} {share:.0f}% {
        t("of the net", "நிகரத்தில்")
    }</strong>
  {t("from", "மொத்தம்")} {fri["n"] if "n" in fri else fri["trades"]} {t("of", "இல்")} {b["trades"]} {
        t(
            "nights. A Friday entry is held over the weekend, so part of this book is a three-day gap wearing a one-night rule's clothes.",
            "இரவுகள். வெள்ளி நுழைவு வார இறுதி வரை. எனவே இந்தப் புத்தகத்தின் ஒரு பகுதி மூன்று நாள் gap.",
        )
    }
  <strong>{t("Thursday loses", "வியாழன் நஷ்டம்")} ({r(thu["net"])}).</strong>
  {
        t(
            "Neither fact is settled, and the live tab exists to prove them forward rather than assume them.",
            "இரண்டும் இன்னும் உறுதி இல்லை. நேரடி tab இதை முன்னோக்கி நிரூபிக்கவே உள்ளது.",
        )
    }</p>
</section>"""


def _night_rows(rows: list[dict]) -> str:
    out = ""
    for x in rows:
        gap = (x["exit_spot"] - x["entry_spot"]) if (x["exit_spot"] and x["entry_spot"]) else None
        prem = (
            f"&#8377;{x['entry_premium']:.2f} &rarr; &#8377;{x['exit_premium']:.2f}"
            if x["exit_premium"] is not None
            else f"&#8377;{x['entry_premium']:.2f} &rarr; —"
        )
        floor = "" if x["priced"] else f" <span class='neg'>{t('floored', 'தளம்')}</span>"
        # The ternary must guard ONLY the gap cell. Wrapped around the whole
        # f-string it dropped the entire row whenever a gap was missing.
        gap_cell = f"<td>{gap:+.0f}</td>" if gap is not None else "<td>—</td>"
        rsi_cell = f"<td>{x['rsi']:.1f}</td>" if x["rsi"] is not None else "<td>—</td>"
        out += (
            f'<tr data-year="{x["session"].year}"><td>{x["session"].strftime("%d %b %Y")}</td>'
            f"<td>{x['exit_session'].strftime('%d %b') if x['exit_session'] else '—'}</td>"
            f"<td>{x['strike']} {x['side']} &middot; {x['expiry']}</td>"
            f"{rsi_cell}{gap_cell}"
            f"<td>{prem}{floor}</td><td>{r(x['capital'])}</td><td>{r(x['charges'])}</td>"
            f"<td class='{cls(x['net'])}'><strong>{r(x['net'])}</strong></td></tr>"
        )
    return out


def ten(b: dict) -> str:
    head = (
        f"<thead><tr><th scope='col'>{t('Read', 'படித்தது')}</th><th scope='col'>{t('Sold', 'விற்றது')}</th>"
        f"<th scope='col'>{t('Contract', 'Contract')}</th><th scope='col'>RSI</th>"
        f"<th scope='col'>{t('Gap', 'Gap')}</th><th scope='col'>{t('Premium', 'பிரீமியம்')}</th>"
        f"<th scope='col'>{t('Capital', 'மூலதனம்')}</th><th scope='col'>{t('Charges', 'கட்டணம்')}</th>"
        f"<th scope='col'>{t('Net', 'நிகர')}</th></tr></thead>"
    )
    best = sorted(b["rows"], key=lambda x: x["net"], reverse=True)[:10]
    worst = sorted(b["rows"], key=lambda x: x["net"])[:10]
    return f"""
<section id="tenten">
  <div class="shead"><div><h2>{t("Best ten, worst ten", "சிறந்த பத்து, மோசமான பத்து")}</h2>
    <p>{t("The tails, in full. The gap column is the index move from the 15:10 close to the morning sale — the thing the rule is actually buying.", "இரு முனைகளும் முழுமையாக. Gap நெடுவரிசை = 15:10 முதல் காலை விற்பனை வரை index நகர்வு — விதி உண்மையில் வாங்குவது இதுதான்.")}</p></div></div>
  <div class="tblwrap"><table>{head}<tbody>{_night_rows(best)}</tbody></table></div>
  <p class="note">{t("And the ten worst:", "மோசமான பத்து:")}</p>
  <div class="tblwrap"><table>{head}<tbody>{_night_rows(worst)}</tbody></table></div>
</section>"""


def capital(b: dict) -> str:
    lots = sorted({x["lot"] for x in b["rows"]})
    return f"""
<section id="capital">
  <div class="shead"><div><h2>{t("Capital at work — what one night costs", "மூலதனம் — ஒரு இரவுக்கு எவ்வளவு")}</h2>
    <p>{t("Every night is ONE lot of a single in-the-money contract, paid in full. There is no margin, no second leg and never more than one position open, so the largest single night is the whole account requirement.", "ஒவ்வொரு இரவும் ஒரே ஒரு lot, முழுமையாக செலுத்தப்படுகிறது. Margin இல்லை, இரண்டாவது leg இல்லை, ஒரே நேரத்தில் ஒன்றுக்கு மேற்பட்ட position இல்லை.")}</p></div></div>
  <div class="kpis">
    <div class="kpi"><div class="kpi-l">{t("Most ever at risk", "அதிகபட்ச ஆபத்து")}</div>
      <div class="kpi-v">{r(b["peak_deployed"])}</div>
      <div class="kpi-s">{t("one night, one contract", "ஒரு இரவு, ஒரு contract")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Typical night", "வழக்கமான இரவு")}</div>
      <div class="kpi-v">{r(b["median_deployed"])}</div>
      <div class="kpi-s">{t("median &middot; average", "இடைநிலை &middot; சராசரி")} {r(b["avg_deployed"])}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Return on the worst night's capital", "மூலதனத்தின் மீதான வருவாய்")}</div>
      <div class="kpi-v pos">{b["net"] / b["peak_deployed"] * 100:.0f}%</div>
      <div class="kpi-s">{t("net &divide; largest single deployment", "நிகர &divide; அதிகபட்ச பயன்பாடு")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Lot sizes met", "Lot அளவுகள்")}</div>
      <div class="kpi-v">{" &middot; ".join(str(x) for x in lots)}</div>
      <div class="kpi-s">{t("by expiry date, not by trade date", "Expiry தேதி வாரியாக, trade தேதி அல்ல")}</div></div>
  </div>
</section>"""


def lots(b: dict) -> str:
    eras = b["lot_eras"]
    body = "".join(
        f"<tr><th scope='row'>{e['lot']}</th><td>{e['first']} &rarr; {e['last']}</td>"
        f"<td>{e['n']}</td><td>{r(e['cap'])}</td>"
        f"<td class='{cls(e['net'])}'><strong>{r(e['net'])}</strong></td>"
        f"<td>{r(e['net'] / e['n']) if e['n'] else '—'}</td></tr>"
        for e in eras
    )
    smallest = min(eras, key=lambda e: e["cap"])
    biggest = max(eras, key=lambda e: e["cap"])
    return f"""
<section id="lots">
  <div class="shead"><div><h2>{
        t("One lot, always — but the lot changed size", "எப்போதும் ஒரு lot — ஆனால் lot அளவு மாறியது")
    }</h2>
    <p>{
        t(
            "Every figure in this document is ONE lot. The rule buys one lot when the candle qualifies and nothing when it does not — no ramp on a winning streak, no cut after a loss, and never two positions open at once. The engine will now also size up as the book banks money, the way the CE and PE books do, and that is measured separately below; it is OFF unless it is switched on, and nothing above it uses it.",
            "இந்த ஆவணத்தின் ஒவ்வொரு எண்ணும் ஒரு lot. Candle தகுதி பெற்றால் ஒரு lot, இல்லையெனில் ஒன்றுமில்லை — வெற்றித் தொடரில் அதிகரிப்பு இல்லை, நஷ்டத்திற்குப் பின் குறைப்பு இல்லை. இப்போது என்ஜின், CE மற்றும் PE புத்தகங்களைப் போல, சேர்த்த பணத்திற்கு ஏற்ப அளவை உயர்த்தவும் முடியும்; அது கீழே தனியாக அளக்கப்பட்டுள்ளது. இயக்காதவரை அது OFF; மேலுள்ள எண்கள் அதைப் பயன்படுத்தவில்லை.",
        )
    }</p></div></div>
  <p>{
        t(
            "What DID change is the lot itself. NIFTY's lot size is set by the exchange and is keyed to the contract's EXPIRY, not to the day you trade it — so the same unchanged rule was risking very different money at different times.",
            "மாறியது lot-இன் அளவு. NIFTY lot அளவை exchange நிர்ணயிக்கிறது, அது contract-இன் EXPIRY-ஐ ஒட்டியது, நீங்கள் வர்த்தகம் செய்யும் நாளை அல்ல.",
        )
    }</p>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Lot", "Lot")}</th><th scope="col">{t("From &rarr; to", "முதல் &rarr; வரை")}</th>
      <th scope="col">{t("Nights", "இரவுகள்")}</th><th scope="col">{t("Most at risk", "அதிகபட்ச ஆபத்து")}</th>
      <th scope="col">{t("Net", "நிகர")}</th><th scope="col">{t("Per night", "இரவுக்கு")}</th></tr></thead>
    <tbody>{body}</tbody></table></div>
  <p class="note note-warn">{t("One lot meant", "ஒரு lot என்பது")} <strong>{r(smallest["cap"])}</strong>
  {t("of premium at its smallest and", "மிகக் குறைந்தபோது, மேலும்")} <strong>{r(biggest["cap"])}</strong>
  {t("at its largest — a", "அதிகபட்சமாக —")} {biggest["cap"] / smallest["cap"]:.1f}&times;
  {
        t(
            "swing in capital at risk for an identical rule. Read every rupee figure in this document against the lot that was live at the time, not against today's 65.",
            "அதே விதிக்கு மூலதன ஆபத்தில் இவ்வளவு வேறுபாடு. இந்த ஆவணத்தின் ஒவ்வொரு ரூபாய் எண்ணையும் அப்போது இருந்த lot-ஐ ஒட்டிப் படியுங்கள்.",
        )
    }</p>
</section>"""


# Measured through the engine, one run per step, not scaled from the one-lot
# book -- a ladder changes the charges as well as the size, and a per-lot
# figure recovered by division is wrong whenever a fee does not scale.
LADDER = [
    {"label": "flat, one lot", "step": 0, "net": 277173, "dd": -17965, "peak": 33304},
    {"label": "+1 lot per +100% banked", "step": 100, "net": 680111, "dd": -116952, "peak": 171717},
    {"label": "+1 lot per +50% banked", "step": 50, "net": 2299104, "dd": -292168, "peak": 490620},
    {"label": "+1 lot per +25% banked", "step": 25, "net": 3597754, "dd": -351236, "peak": 666075},
]


def ladder_section() -> str:
    """What compounding does to this book, and what it costs to get it."""
    rows = "".join(
        f'<tr><td>{t(row["label"], row["label"])}</td>'
        f'<td class="num">{r(row["net"])}</td>'
        f'<td class="num neg">{r(row["dd"])}</td>'
        f'<td class="num">{r(row["peak"])}</td>'
        f'<td class="num">{row["net"] / row["peak"]:.2f}</td></tr>'
        for row in LADDER
    )
    return f"""
<section>
  <div class="shead"><div><h2>{t("Sizing up as the book earns", "புத்தகம் சம்பாதிக்கும்போது அளவை உயர்த்துதல்")}</h2>
    <p>{t("One more lot for every rung of profit actually banked. Each row is its own engine run over the same nights, so the charges are computed at the size actually traded rather than scaled from the one-lot book.", "உண்மையில் சேர்த்த ஒவ்வொரு படி லாபத்துக்கும் ஒரு lot கூடுதல். ஒவ்வொரு வரிசையும் அதே இரவுகள் மீது தனித்தனி என்ஜின் இயக்கம்; கட்டணங்கள் உண்மையில் வர்த்தகம் செய்த அளவில் கணக்கிடப்பட்டவை.")}</p></div></div>
  <table class="tbl">
    <thead><tr><th>{t("Sizing", "அளவு")}</th><th class="num">{t("Net", "நிகரம்")}</th><th class="num">{t("Worst fall", "மோசமான வீழ்ச்சி")}</th><th class="num">{t("Peak capital", "உச்ச மூலதனம்")}</th><th class="num">{t("Net per rupee", "ரூபாய்க்கு நிகரம்")}</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <div class="note" style="margin-top:14px">
    <h2 class="note-h">{t("The last column is the one to read", "கடைசி நெடுவரிசையே படிக்க வேண்டியது")}</h2>
    <p>{t("Compounding earns thirteen times the money and needs twenty times the capital, so per rupee committed the flat book is the most efficient of the four at 8.32 against 5.40. What the ladder buys is a bigger absolute result from an account that is willing to grow into it, not a better use of the money.", "Compounding பதின்மூன்று மடங்கு பணம் தருகிறது, ஆனால் இருபது மடங்கு மூலதனம் கேட்கிறது; எனவே ஒரு ரூபாய்க்கு நிலையான புத்தகமே சிறந்தது &mdash; 8.32 எதிராக 5.40. Ladder தருவது பெரிய முழுமையான முடிவு, பணத்தின் சிறந்த பயன்பாடு அல்ல.")}</p>
    <p>{t("The ordering of the steps is not reliable. Across 179 nights +75% measured WORSE per rupee than not compounding at all, which is a middle setting losing to both its neighbours &mdash; a sign these thresholds rest on too few trades to be structure. The engine therefore ships with compounding OFF and the step as a setting, and picks none of them.", "படிகளின் வரிசை நம்பகமானது அல்ல. 179 இரவுகளில் +75%, compounding செய்யாததை விட ஒரு ரூபாய்க்கு மோசமாக அளந்தது &mdash; இரு பக்கத்து அமைப்புகளையும் விட நடுவில் உள்ளது மோசமாக இருப்பது, இந்த வரம்புகள் மிகச் சில வர்த்தகங்களில் நிற்கின்றன என்பதன் அறிகுறி. எனவே என்ஜின் compounding OFF ஆக வருகிறது; படி ஒரு அமைப்பு, தேர்வு அல்ல.")}</p>
  </div>
</section>
"""


def sizing_section(b: dict) -> str:
    """The family's capital table, from the shared module so all four agree."""
    rows = [{"gross": x["gross"], "costs": x["charges"], "capital": x["capital"]} for x in b["rows"]]
    sizes = sizing.scale(rows, years=b["years"])
    return sizing.section(
        sizes,
        r=r,
        cls=cls,
        live_lots=1,
        note_en="Gap Carry holds ONE position at a time and the tab offers 1, 2 or 3 lots, so the deeper rows are what the same rule would have done at a size it has never actually traded.",
        note_ta="Gap Carry ஒரே நேரத்தில் ஒரு position மட்டுமே; tab 1, 2 அல்லது 3 lot தருகிறது. ஆழமான வரிசைகள் = அதே விதி, இதுவரை வர்த்தகம் செய்யாத அளவில்.",
    )


def charges(b: dict) -> str:
    pct = (b["charges"] / b["gross_before_charges"] * 100) if b["gross_before_charges"] else 0
    return f"""
<section id="charges">
  <div class="shead"><div><h2>{t("Charges, in full", "கட்டணங்கள், முழுமையாக")}</h2>
    <p>{t("Every rupee between the gross result and the account. Brokerage, STT, exchange fees, GST, SEBI and stamp, on both legs of every night. No figure anywhere in this document is quoted before them.", "மொத்த முடிவுக்கும் கணக்குக்கும் இடையிலான ஒவ்வொரு ரூபாயும். இந்த ஆவணத்தில் எந்த எண்ணும் கட்டணங்களுக்கு முன் தரப்படவில்லை.")}</p></div></div>
  <div class="kpis">
    <div class="kpi"><div class="kpi-l">{t("Gross, before charges", "கட்டணத்திற்கு முன்")}</div>
      <div class="kpi-v {cls(b["gross_before_charges"])}">{r(b["gross_before_charges"])}</div>
      <div class="kpi-s">{t("what the moves alone were worth", "நகர்வுகள் மட்டும்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Charges paid", "செலுத்திய கட்டணம்")}</div>
      <div class="kpi-v neg">&minus;{r(b["charges"])}</div>
      <div class="kpi-s">{pct:.1f}% {t("of the gross", "மொத்தத்தில்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Per night", "இரவுக்கு")}</div>
      <div class="kpi-v">{r(b["avg_charges"])}</div>
      <div class="kpi-s">{t("both legs, averaged", "இரண்டு leg-களும், சராசரி")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Net, after charges", "கட்டணத்திற்குப் பின் நிகர")}</div>
      <div class="kpi-v {cls(b["net"])}">{r(b["net"])}</div>
      <div class="kpi-s">{t("the number every other figure here uses", "இங்குள்ள ஒவ்வொரு எண்ணும் இதுவே")}</div></div>
  </div>
</section>"""


def honesty(b: dict) -> str:
    top3 = sorted((x["net"] for x in b["rows"]), reverse=True)[:3]
    tm = b["top_months"]
    top3_pct = sum(v for _, v in tm) / b["net"] * 100 if b["net"] else 0
    top_line = t(
        f"{tm[0][0]} alone is {r(tm[0][1])}, {tm[0][1] / b['net'] * 100:.0f}% of everything this rule made in 5.5 years.",
        f"{tm[0][0]} மட்டும் {r(tm[0][1])} — 5.5 ஆண்டுகளில் இந்த விதி ஈட்டியதில் {tm[0][1] / b['net'] * 100:.0f}%.",
    )
    return f"""
<section id="honesty">
  <div class="shead"><h2>{t("Risk register", "ரிஸ்க் பதிவேடு")}</h2></div>
  <p><strong>{b["floored"]} {t("of", "இல்")} {b["trades"]} {t("exits are floored at intrinsic value", "வெளியேற்றங்கள் உள்ளார்ந்த மதிப்பில்")}</strong>
  ({r(b["floored_net"])} {t("of the net", "நிகரத்தில்")}). {t("Those are contracts that gapped far enough to leave what the archives carry — which happens precisely when the night went well, so the floor UNDERSTATES them. A floor is not a price, and they are counted apart everywhere they appear.", "இவை archive வரம்பை விட்டு வெளியேறிய contract-கள் — இரவு நன்றாக சென்றபோதுதான் இது நடக்கும், எனவே தளம் அவற்றைக் குறைத்தே காட்டுகிறது.")}</p>
  <p><strong>{t("The top three nights are", "மேல் மூன்று இரவுகள்")} {r(sum(top3))}</strong>, {sum(top3) / b["net"] * 100:.0f}% {t("of the net; without them the book still makes", "நிகரத்தில். அவை இல்லாமலும் புத்தகம் ஈட்டுவது")} {r(b["net"] - sum(top3))}.</p>
  <p><strong>{t("One month is a third of the book.", "ஒரு மாதம் புத்தகத்தின் மூன்றில் ஒரு பங்கு.")}</strong>
  {top_line} {t("The three best months together are", "மூன்று சிறந்த மாதங்கள் சேர்ந்து")} {top3_pct:.0f}% {t("of the net, and only", "நிகரத்தில், மேலும்")} {b["months_green"]} {t("of", "இல்")} {b["months_total"]} {t("months finished green. Strip the single best month and the book still makes", "மாதங்கள் பச்சையில் முடிந்தன. சிறந்த மாதத்தை நீக்கினாலும் புத்தகம் ஈட்டுவது")} {r(b["net"] - b["top_months"][0][1])} &mdash; {t("so it does not rest on that month alone, but the shape of this book is a few violent gaps and a long quiet middle, not a steady drip.", "எனவே அது அந்த ஒரு மாதத்தை மட்டும் நம்பியில்லை. ஆனால் இந்தப் புத்தகத்தின் வடிவம் சில வலுவான gap-களும் நீண்ட அமைதியான நடுப்பகுதியும்தான்.")}</p>
  <p><strong>{t("RSI 70 is the top of a band, not a plateau.", "RSI 70 ஒரு வரம்பின் உச்சம், சமவெளி அல்ல.")}</strong>
  {t("Every threshold from 65 to 76 is positive with both halves of the window green, but 70 is the best cell in it. Read the strength as profit factor ~1.35–1.40 rather than the", "65 முதல் 76 வரை ஒவ்வொரு threshold-ம் நேர்மறை, இரு பாதிகளும் பச்சை. ஆனால் 70 சிறந்த கலம். வலிமையை ~1.35–1.40 எனப் படியுங்கள், மேலே உள்ள")} {b["profit_factor"]:.2f} {t("above.", "அல்ல.")}</p>
  <p><strong>{t("10m looked better and cannot be traded.", "10m சிறப்பாக இருந்தது, ஆனால் வர்த்தகம் செய்ய முடியாது.")}</strong>
  {t("Dhan serves 1m, 5m, 15m and 1h only, so the live tab offers 5m and 15m. 5m is also the safer of the two on the weekday concentration — 46% Friday against 67% on 10m.", "Dhan 1m, 5m, 15m, 1h மட்டுமே தருகிறது. 5m வாரநாள் குவிப்பிலும் பாதுகாப்பானது — வெள்ளி 46%, 10m-இல் 67%.")}</p>
  <p class="note">{t("Twelve of twelve combinations of 5m/10m/15m/30m against RSI 68/70/72 were profitable over this window, eleven of them green in both halves — the candle is read once and both ends are clock times, so there is very little for a fit to grip.", "5m/10m/15m/30m × RSI 68/70/72 — பன்னிரண்டில் பன்னிரண்டும் லாபகரம், பதினொன்று இரு பாதிகளிலும் பச்சை. Candle ஒரு முறை படிக்கப்படுகிறது, இரு முனைகளும் கடிகார நேரம்.")}</p>
</section>"""


def every_night(b: dict) -> str:
    # LANG_JS carries a year filter keyed to a section with id="ledger". It
    # guards on that section existing but NOT on #ledger-years, so naming this
    # section "ledger" without supplying the control threw on every load.
    years = sorted({x["session"].year for x in b["rows"]})
    years_btns = "".join(f'<button type="button" data-year="{y}" aria-pressed="false">{y}</button>' for y in years)
    head = (
        f"<thead><tr><th scope='col'>{t('Read', 'படித்தது')}</th><th scope='col'>{t('Sold', 'விற்றது')}</th>"
        f"<th scope='col'>{t('Contract', 'Contract')}</th><th scope='col'>RSI</th>"
        f"<th scope='col'>{t('Gap', 'Gap')}</th><th scope='col'>{t('Premium', 'பிரீமியம்')}</th>"
        f"<th scope='col'>{t('Capital', 'மூலதனம்')}</th><th scope='col'>{t('Charges', 'கட்டணம்')}</th>"
        f"<th scope='col'>{t('Net', 'நிகர')}</th></tr></thead>"
    )
    return f"""
{
        HELPERS["cycle_section"](
            HELPERS["daily_series"](b["rows"], lambda x: x["exit_session"] or x["session"], lambda x: x["net"]),
            t,
            r,
            noun_en="nights",
            noun_ta="இரவுகள்",
        )
}
{
        HELPERS["daily_ledger"](
            HELPERS["daily_series"](b["rows"], lambda x: x["exit_session"] or x["session"], lambda x: x["net"]),
            t,
            t_attr,
            r,
            cls,
            noun_en="nights",
            noun_ta="இரவுகள்",
        )
    }"""


# ── the page ──────────────────────────────────────────────────────────
def build() -> str:
    b = book(load(BOOK))
    if b["trades"] < 2:
        return ""
    return f"""<title>Gap Carry — the overnight book</title>
<style>
{STYLE}
/* The one rule this sheet adds, the same one the Fib and Candle sheets add:
   the parent's stylesheet has no `table.heat`, because the month grid is the
   one thing each sheet lays out for itself. */
table.heat td {{ text-align:right; font-variant-numeric:tabular-nums; }}
{sizing.SIZING_CSS}
{LANG_CSS}
</style>

<main class="wrap">

<section class="document-hero">
  <div class="hero-copy">
    <p class="eyebrow"><b>TEARSHEET</b>{
        t(
            "PhilForge &middot; Options Cascade &middot; Gap Carry",
            "PhilForge &middot; Options Cascade &middot; Gap Carry",
        )
    }</p>
    <h1>{
        t(
            "Gap Carry &mdash; NIFTY, EMA20 + RSI, 15:10 in, losers cut at 09:15, 5.5 years",
            "Gap Carry &mdash; NIFTY, EMA20 + RSI, 15:10 நுழைவு, நஷ்டம் 09:15-இல் வெட்டப்படும், 5.5 ஆண்டுகள்",
        )
    }</h1>
    <p class="lede">{
        t(
            "One candle is read at 15:10. If it closed on the strong side of its EMA20 with momentum to match, one in-the-money contract is bought and held overnight, then sold into the next morning's open. No stop, no target, and nothing on the way out but the clock.",
            "15:10-இல் ஒரு candle படிக்கப்படுகிறது. அது தன் EMA20-இன் வலுவான பக்கத்தில், பொருந்தும் momentum-உடன் மூடினால், ஒரு in-the-money contract வாங்கப்பட்டு இரவு முழுவதும் வைக்கப்படுகிறது; அடுத்த காலை விற்கப்படுகிறது. Stop இல்லை, இலக்கு இல்லை, கடிகாரம் மட்டுமே.",
        )
    }</p>
    <div class="document-meta">
      <div class="meta-chip"><span>{t("Window", "காலம்")}</span><strong>{b["first"]} &rarr; {b["last"]}</strong></div>
      <div class="meta-chip"><span>{t("Nights", "இரவுகள்")}</span><strong>{b["trades"]}</strong></div>
      <div class="meta-chip"><span>{t("Size", "அளவு")}</span><strong>{
        t("one lot &middot; ATM+4 in the money", "ஒரு lot &middot; ATM+4 in the money")
    }</strong></div>
      <div class="meta-chip"><span>{t("Expiry", "Expiry")}</span><strong>{
        t("nearest weekly that survives the night", "இரவைத் தாண்டும் அருகிலுள்ள weekly")
    }</strong></div>
      <div class="meta-chip"><span>{t("Prices", "விலைகள்")}</span><strong>{
        t("recorded premiums, two archives", "பதிவான premium-கள், இரண்டு archive")
    }</strong></div>
      <div class="meta-chip"><span>{t("Costs", "கட்டணங்கள்")}</span><strong>{
        t("Brokerage, STT, GST, stamp, both legs", "புரோக்கரேஜ், STT, GST, stamp, இரு leg")
    }</strong></div>
    </div>
  </div>
  <div class="system-sigil" aria-hidden="true">
    <div class="sigil-ring ring-one"></div><div class="sigil-ring ring-two"></div><div class="sigil-ring ring-three"></div>
    <div class="sigil-core">GC</div>
    <span class="sigil-label label-one">5m</span><span class="sigil-label label-two">70</span>
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
        t("Paper only &middot; net of every charge", "Paper மட்டும் &middot; அனைத்து கட்டணங்களுக்குப் பின்")
    }</strong>
      <small>{
        t(
            "Reproduces through engine/gap_carry.py to the rupee &middot; the live tab has taken no money forward yet",
            "engine/gap_carry.py வழியாக ரூபாய் வரை மீளுருவாக்கம் &middot; நேரடி tab இன்னும் பணம் ஈட்டவில்லை",
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
            "This is a REPLAY, not a record. Nothing here was traded with money. It is the same rule the Gap Carry tab runs, walked forward over recorded option premiums, and it reproduces through the engine the tab uses to the rupee — which is the only reason a sheet may quote it. Two things about it are unsettled and are stated plainly below rather than buried: Friday carries far more of the book than one weekday should, and RSI 70 is the best cell in a band rather than a plateau.",
            "இது ஒரு REPLAY, பதிவு அல்ல. இங்கு எதுவும் பணத்தில் வர்த்தகம் செய்யப்படவில்லை. Gap Carry tab இயக்கும் அதே விதி, பதிவான premium-கள் மீது முன்னோக்கி நடத்தப்பட்டது. இரண்டு விஷயங்கள் உறுதி இல்லை: வெள்ளி புத்தகத்தின் பெரும்பகுதியைச் சுமக்கிறது, மேலும் RSI 70 ஒரு சமவெளி அல்ல.",
        )
    }</p>
</div>

{kpis(b)}
{charges(b)}
{every_night(b)}
{curve_section(b)}
{cuts(b).split("<!--CUT-->")[0]}
{heat(b)}
{sizing_section(b)}
{ladder_section()}
{honesty(b)}
{cuts(b).split("<!--CUT-->")[1]}
{ten(b)}
{capital(b)}
{lots(b)}

{
    HELPERS["method_and_limits"](
        t,
        [
            ('Read the last closed candle at 15:10 and take the close, the EMA20 and the RSI(14). Nothing else on the chart is used.', '15:10-க்கு கடைசி மூடிய candle-இல் close, EMA20, RSI(14) மட்டும் படிக்கப்படுகிறது. chart-இல் வேறெதுவும் பயன்படுத்தப்படவில்லை.'),
            ('Buy one in-the-money contract four strikes deep and hold it overnight. At 09:15 a contract already worth less than it cost is sold there; one in profit runs to 09:20. Still clock times, with one question asked at the open.', 'நான்கு strike உள்ளே உள்ள ஒரு contract வாங்கி இரவு முழுவதும் வைக்கப்படுகிறது. 09:15-இல் வாங்கிய விலையை விடக் குறைவாக இருந்தால் அங்கேயே விற்கப்படுகிறது; லாபத்தில் இருந்தால் 09:20 வரை. கடிகார நேரங்களே, ஆனால் திறப்பில் ஒரு கேள்வி.'),
            ('Every premium is a recorded minute from the Dhan archive, with Upstox asked when Dhan has lost the strike. An exit neither can quote is valued at intrinsic and counted separately.', 'ஒவ்வொரு பிரீமியமும் Dhan காப்பகத்தின் பதிவான நிமிடம்; Dhan strike-ஐ இழந்தால் Upstox கேட்கப்படுகிறது. இரண்டுமே தர முடியாத வெளியேற்றம் intrinsic-இல் மதிப்பிடப்பட்டு தனியாக எண்ணப்படுகிறது.'),
            ("Charges are the full statutory schedule per round &mdash; brokerage, STT, exchange, GST, SEBI and stamp &mdash; and the lot is the one in force on the contract's own expiry.", 'கட்டணங்கள் முழு சட்டப்பூர்வ பட்டியல் &mdash; brokerage, STT, exchange, GST, SEBI, stamp. lot என்பது contract-இன் expiry-இல் அமலில் இருந்தது.'),
        ],
        running=("Gap Carry runs on its own tab with a live gate of its own. This document is the recorded book behind it, not the live one; the tab is where today's position appears.", 'Gap Carry அதன் சொந்த tab-இல் இயங்குகிறது. இந்த ஆவணம் அதன் பின்னால் உள்ள பதிவான புத்தகம், நேரடியானது அல்ல; இன்றைய நிலை tab-இல் தெரியும்.'),
    )
}
</article>
</div>
</main>
{CHART_JS_SRC.replace("__SERIES__", json.dumps(HELPERS["daily_series"](b["rows"], lambda x: x["exit_session"] or x["session"], lambda x: x["net"]), separators=(",", ":")))}
{READER_JS}
{LANG_JS}
"""


if __name__ == "__main__":
    html = build()
    if not html:
        raise SystemExit("not enough nights to draw a curve")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    print(f"wrote {OUT} ({len(html):,} bytes)")
