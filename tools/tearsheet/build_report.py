"""Emit the shareable tearsheet. Every figure is interpolated from
report_data.json — nothing in the page is typed by hand.

This IS the source of the published document: running it reproduces
docs/assets/backtest-tearsheet-5yr.html byte for byte, so fixes belong here and
never in the HTML alone. Paths are resolved from this file, not the working
directory, so it can be run from anywhere in the repo.
"""

import html
import json
import pathlib

from i18n import LANG_CSS, LANG_JS, t, t_attr

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent.parent

D = json.load(open(_HERE / "report_data.json"))
H = D["headline"]
BW = D["best_worst"]
DAY = D["daily"]
FC = D["fill_correction"]
SP = D["splice"]
OUT = str(_REPO / "docs" / "assets" / "backtest-tearsheet-5yr.html")

MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def r(n, dp=0):
    """Indian-format a rupee figure with a sign."""
    n = float(n)
    sign = "-" if n < 0 else ""
    v = abs(n)
    if dp:
        whole, frac = divmod(round(v * 100), 100)
        tail = f".{frac:02d}"
    else:
        whole, tail = int(round(v)), ""
    s = str(whole)
    if len(s) > 3:
        head, last3 = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [last3])
    return f"{sign}₹{s}{tail}"


def lakh(n):
    return f"{n / 100000:.2f}L"


def cls(n):
    return "pos" if n > 0 else ("neg" if n < 0 else "flat")


def recolour(css, doc):
    """Repaint a borrowed stylesheet in one of the five sheets' own hues.

    THE FIVE ARE A SET, and they were not distinct: `options` and `supertrend`
    carried the identical light pill (#6d28d9) while `fib` and `candle` were
    two blues a shade apart, so five documents wore three colours and two
    pairs were indistinguishable (Phil, 2026-09-02).

    Everything a reader sees tinted already resolves through `--accent`: the
    selected language button (i18n.py), the `.note` explanation panel and its
    soft fill, links, the hero rings, the section rule. So one substitution
    here moves the whole document, and the matching pill in the Assets tab bar
    (philforge-app.css, `--tearsheet-pill`) is set to the SAME pair -- which is
    the point: the pill's colour is a promise about what opens.

    CONTRAST IS MEASURED AGAINST THE CHIP, NOT THE CARD, because the accent's
    worst light ground is the accent-tinted risk chip (--accent-soft over the
    card) and not the card itself. Every light value below clears 4.5:1 there:
    violet 4.93, cyan 4.62, lime 6.02, amber 6.01, rose 5.25. Re-measure
    against the chip if one is ever changed.

    The table lives INSIDE the function on purpose: the siblings borrow this
    by extracting the `def` and exec'ing it on its own, so a module-level
    constant beside it would not travel and every sibling would raise.
    """
    accents = {
        "options": ("#6d4bd8", "#a78bfa"),  # violet — the parent document
        "fib": ("#0e7490", "#22d3ee"),  # cyan
        "candle": ("#3f6212", "#a3e635"),  # lime
        "gapcarry": ("#92400e", "#fbbf24"),  # amber — it was already amber
        "supertrend": ("#be123c", "#fb7185"),  # rose
    }
    light, dark = accents[doc]
    out = css
    for old, new in ((accents["options"][0], light), (accents["options"][1], dark)):
        if old == new:
            continue
        rgb = ",".join(str(int(new[i : i + 2], 16)) for i in (1, 3, 5))
        old_rgb = ",".join(str(int(old[i : i + 2], 16)) for i in (1, 3, 5))
        out = out.replace(f"--accent:{old}", f"--accent:{new}")
        out = out.replace(f"--accent-rgb:{old_rgb}", f"--accent-rgb:{rgb}")
        # Catches --accent-soft, and any literal the accent already tints.
        out = out.replace(f"rgba({old_rgb},", f"rgba({rgb},")
    return out


def daily_ledger(series, t, t_attr, r, cls, noun_en="trades", noun_ta="டிரேடுகள்"):
    """The five-year sheet's compact ledger: ONE ROW PER DAY, not per trade.

    Phil, 2026-09-02: "I don't want that long list of day trades but cutshort
    like the one in Options". The siblings each carried the whole book instead
    -- every night with its contract, RSI, strike and gap -- which is a
    different document from a ledger. Four columns, a year filter, and a
    running balance is what the template means by one.

    `series` is [date, day_net, running_total, n_trades] per trading day, which
    is what every book can produce whatever its rows look like underneath.
    """
    days = len(series)
    green = sum(1 for d in series if d[1] > 0)
    years = sorted({str(d[0])[:4] for d in series})
    btns = "".join(f'<button type="button" data-year="{y}" aria-pressed="false">{y}</button>' for y in years)
    rows = "".join(
        f'<tr data-year="{str(d[0])[:4]}">'
        f'<th scope="row">{d[0]}</th>'
        f"<td>{d[3]}</td>"
        f'<td class="{cls(d[1])}">{r(d[1])}</td>'
        f"<td>{r(d[2])}</td></tr>"
        for d in series
    )
    best = max(series, key=lambda d: d[1]) if series else ("", 0, 0, 0)
    worst = min(series, key=lambda d: d[1]) if series else ("", 0, 0, 0)
    avg = (sum(d[1] for d in series) / days) if days else 0.0
    return f"""
<section id="ledger">
  <div class="shead"><div><h2>{t("Daily P&amp;L ledger", "தினசரி லாப-நஷ்ட பதிவேடு")}</h2>
    <p>{t(f"Every trading day in the record, with the running balance beside it. Filter by year, or scroll the whole {days} rows.", f"பதிவில் உள்ள ஒவ்வொரு வர்த்தக நாளும், அதனுடன் ஓடும் இருப்பும். ஆண்டு வாரியாக வடிகட்டலாம், அல்லது {days} வரிகளையும் உருட்டிப் பார்க்கலாம்.")}</p></div></div>
  <div class="ledger-controls" id="ledger-years">
    <button type="button" data-year="all" aria-pressed="true">{t("All", "அனைத்தும்")}</button>
    {btns}
  </div>
  <div class="ledger-scroll" id="ledger-body" tabindex="0" role="region"
       {t_attr("aria-label", "Daily profit and loss ledger", "தினசரி லாப நஷ்ட பதிவு")} data-total="{days}">
    <table id="ledger-table" data-total="{days}">
      <thead><tr>
        <th scope="col">{t("Date", "தேதி")}</th>
        <th scope="col">{t(noun_en.capitalize(), noun_ta)}</th>
        <th scope="col">{t("Day net", "நாளின் நிகரம்")}</th>
        <th scope="col">{t("Running total", "ஓடும் மொத்தம்")}</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <div class="ledger-foot">
    <span><span id="ledger-count">{days}</span> {t("days shown", "நாட்கள் காட்டப்படுகின்றன")}</span>
    <span>{t("green", "பச்சை")} {green} &middot; {t("red", "சிவப்பு")} {days - green}</span>
    <span>{t("average", "சராசரி")} {r(avg)}</span>
    <span style="margin-left:auto">{t("best", "சிறந்தது")} {r(best[1])} &middot; {t("worst", "மோசமானது")} {r(worst[1])}</span>
  </div>
</section>"""


def daily_series(rows, date_of, net_of):
    """Fold any book's rows into [date, day_net, running, n] per trading day."""
    from collections import defaultdict

    by_day = defaultdict(lambda: [0.0, 0])
    for row in rows:
        d = str(date_of(row))[:10]
        by_day[d][0] += float(net_of(row) or 0.0)
        by_day[d][1] += 1
    out, run = [], 0.0
    for d in sorted(by_day):
        run += by_day[d][0]
        out.append([d, round(by_day[d][0], 2), round(run, 2), by_day[d][1]])
    return out


def method_and_limits(t, steps, running=None):
    """The three sections every sheet in this family ends with.

    Method, what is running today, and what the document is not. The last was
    the same paragraph on all five and is kept here rather than copied four
    times -- a disclaimer that drifts between documents is worse than none.
    `steps` is [(english, tamil)] describing how THAT book was produced, which
    is the only part that differs.
    """
    items = "".join(f"<li>{t(en, ta)}</li>" for en, ta in steps)
    today = ""
    if running:
        today = f"""
<section>
  <div class="shead"><div><h2>{t("What is running today", "இன்று இயங்குவது என்ன")}</h2></div></div>
  <div class="panel"><p>{t(running[0], running[1])}</p></div>
</section>
"""
    return f"""{today}
<section>
  <div class="shead"><div><h2>{t("Method", "முறை")}</h2></div></div>
  <div class="panel"><ol class="method">{items}</ol></div>
  <div class="note note-warn" style="margin-top:14px">
    <h2 class="note-h">{t("What this document is not", "இந்த ஆவணம் எது அல்ல")}</h2>
    <p>{t("It is a backtest. It assumes every signal was filled at the recorded premium, with no rejection, no partial fill and no slippage beyond the modelled costs. Live execution adds all three. Past behaviour of an index, its lot size and its expiry calendar is not a commitment that any of them stay put &mdash; and this record already contains two such changes. Nothing here is investment advice or an offer to manage money.", "இது ஒரு பேக்டெஸ்ட். ஒவ்வொரு சிக்னலும் பதிவான பிரீமியத்தில் நிறைவேறியதாக, நிராகரிப்பு இல்லாமல், பகுதி நிறைவேற்றம் இல்லாமல், கணக்கிட்ட கட்டணங்களுக்கு மேல் ஸ்லிப்பேஜ் இல்லாமல் கருதுகிறது. லைவ் செயல்பாடு இந்த மூன்றையும் சேர்க்கிறது. ஒரு குறியீட்டின் கடந்தகால நடத்தை, அதன் லாட் அளவு, எக்ஸ்பயரி நாட்காட்டி ஆகியவை அப்படியே நீடிக்கும் என்பதற்கு உத்தரவாதம் இல்லை &mdash; இந்தப் பதிவிலேயே அத்தகைய இரண்டு மாற்றங்கள் உள்ளன. இங்குள்ள எதுவும் முதலீட்டு ஆலோசனை அல்ல, பணத்தை நிர்வகிக்கும் சலுகையும் அல்ல.")}</p>
  </div>
</section>"""


def cycle_section(series, t, r, noun_en="trading days", noun_ta="வர்த்தக நாட்கள்"):
    """The daily-income canvas: a bar a day, a line for the running total.

    The last section of the five-year sheet the siblings did not have, and the
    only one that was a build rather than a rename -- its drawing code lives in
    CHART_JS, which they did not borrow. They do now, and it guards on the
    canvas existing (`if (!cv) return`), so borrowing it costs a sheet nothing
    if it ever drops the section.

    `series` is the same [date, day_net, running, n] the ledger takes, so a
    book that can draw one can draw the other.
    """
    days = len(series)
    green = sum(1 for d in series if d[1] > 0)
    avg = (sum(d[1] for d in series) / days) if days else 0.0
    best = max(series, key=lambda d: d[1]) if series else ("", 0)
    worst = min(series, key=lambda d: d[1]) if series else ("", 0)
    first = series[0][0] if series else ""
    last = series[-1][0] if series else ""
    total = series[-1][2] if series else 0
    return f"""
<section>
  <div class="shead">
    <div><h2>{t("Daily income across the whole cycle", "முழு சுழற்சியின் தினசரி வருமானம்")}</h2>
    <p>{t(f"Every one of the {days} {noun_en} in the record. Bars are that day's net income; the line is the running total. Hover any day for its figure.", f"பதிவில் உள்ள {days} {noun_ta} அனைத்தும். கம்பிகள் அந்நாளின் நிகர வருமானம்; கோடு ஓடும் மொத்தம். எந்த நாளின் மீதும் சுட்டியை வையுங்கள்.")}</p></div>
  </div>
  <div class="panel">
    <div class="canvas-wrap">
      <canvas id="cycle" role="img"
        aria-label="Daily profit bars and cumulative net profit for all {days} {noun_en} from {first} to {last}, ending at {r(total)}"></canvas>
      <div class="tip" id="cycle-tip" role="status"></div>
    </div>
    <div class="legend">
      <span><i style="background:var(--curve)"></i>{t("cumulative net", "ஒட்டுமொத்த நிகரம்")}</span>
      <span><i class="bar" style="background:rgba(var(--pos-fill),.55)"></i>{t("profitable day", "லாப நாள்")}</span>
      <span><i class="bar" style="background:rgba(var(--neg-fill),.55)"></i>{t("losing day", "நஷ்ட நாள்")}</span>
      <span style="margin-left:auto">{t("hover or drag for any single day", "ஒரு நாளைப் பார்க்க நகர்த்துங்கள்")}</span>
    </div>
    <div class="axis"><span>{days} {t(noun_en, noun_ta)}</span>
      <span>{green} {t("green", "பச்சை")} ({100 * green / days if days else 0:.0f}%)</span>
      <span>{t("average day", "சராசரி நாள்")} {r(avg)} &middot; {t("best", "சிறந்தது")} {r(best[1])} &middot; {t("worst", "மோசமானது")} {r(worst[1])}</span></div>
  </div>
</section>"""


# ── equity curve as an SVG path ──────────────────────────────────────
def curve_svg(points, w=1040, h=260, pad=1):
    ys = [p[1] for p in points]
    lo, hi = min(ys + [0]), max(ys)
    span = (hi - lo) or 1
    n = len(points) - 1

    def X(i):
        return i / n * w

    def Y(v):
        return h - (v - lo) / span * (h - pad * 2) - pad

    line = " ".join(f"{'M' if i == 0 else 'L'}{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(points))
    area = line + f" L{w},{Y(lo):.1f} L0,{Y(lo):.1f} Z"
    zero = Y(0)
    # running peak, to shade the underwater stretches
    peak, under = -1e18, []
    for i, (_, v) in enumerate(points):
        peak = max(peak, v)
        under.append((X(i), Y(v), Y(peak)))
    dd = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{yp:.1f}" for i, (x, _, yp) in enumerate(under))
    dd += " " + " ".join(f"L{x:.1f},{y:.1f}" for x, y, _ in reversed(under)) + " Z"
    return line, area, dd, zero, hi, lo


def spark(points, w=330, h=84):
    ys = [p[1] for p in points]
    lo, hi = min(ys + [0]), max(ys)
    span = (hi - lo) or 1
    n = len(points) - 1
    pts = [(i / n * w, h - (v - lo) / span * (h - 6) - 3) for i, (_, v) in enumerate(points)]
    line = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    zero = h - (0 - lo) / span * (h - 6) - 3
    return line, zero


c_line, c_area, c_dd, c_zero, c_hi, c_lo = curve_svg(D["curve"]["combined"])
pe_line, pe_zero = spark(D["curve"]["pe"])
ce_line, ce_zero = spark(D["curve"]["ce"])
up_line, up_zero = spark(D["curve"]["upstox"])

# ── monthly heatmap ──────────────────────────────────────────────────
months = D["by_month"]
mvals = [v["net"] for v in months.values()]
mmax = max(abs(min(mvals)), abs(max(mvals)))
years = sorted({k[:4] for k in months})

rows = []
for y in years:
    cells = []
    tot = 0.0
    for i in range(1, 13):
        k = f"{y}-{i:02d}"
        v = months.get(k)
        if not v:
            cells.append('<div class="mcell mcell-void" aria-hidden="true"></div>')
            continue
        net = v["net"]
        tot += net
        w = min(1.0, abs(net) / mmax) ** 0.62
        tone = "pos" if net > 0 else "neg"
        title = f"{MON[i - 1]} {y} — {r(net)} on {v['n']} trades, {v['w']} winners"
        cells.append(
            f'<div class="mcell {tone}" style="--w:{w:.3f}" title="{html.escape(title)}">'
            f'<span class="mcell-m">{MON[i - 1][0]}</span>'
            f'<span class="mcell-v">{net / 1000:+.0f}</span></div>'
        )
    rows.append(
        f'<div class="mrow"><div class="mrow-y">{y}</div>'
        f'<div class="mrow-cells">{"".join(cells)}</div>'
        f'<div class="mrow-t {cls(tot)}">{r(tot)}</div></div>'
    )
month_grid = "".join(rows)

# ── yearly table ─────────────────────────────────────────────────────
yr_rows = []
for y in years:
    pe = D["by_year"]["pe"].get(y, {"net": 0, "n": 0, "w": 0})
    ce = D["by_year"]["ce"].get(y, {"net": 0, "n": 0, "w": 0})
    co = D["by_year"]["combined"].get(y, {"net": 0, "n": 0, "w": 0})
    wr = 100 * co["w"] / co["n"] if co["n"] else 0
    yr_rows.append(
        f"<tr><th scope='row'>{y}</th>"
        f"<td class='{cls(pe['net'])}'>{r(pe['net'])}</td>"
        f"<td class='{cls(ce['net'])}'>{r(ce['net'])}</td>"
        f"<td class='{cls(co['net'])}'><strong>{r(co['net'])}</strong></td>"
        f"<td>{co['n']}</td><td>{wr:.0f}%</td>"
        f"<td>{r(co['net'] / co['n'] if co['n'] else 0)}</td></tr>"
    )
yr_rows.append(
    f"<tr class='trow-total'><th scope='row'>All</th>"
    f"<td class='pos'>{r(H['pe']['net'])}</td>"
    f"<td class='pos'>{r(H['ce']['net'])}</td>"
    f"<td class='pos'><strong>{r(H['combined']['net'])}</strong></td>"
    f"<td>{H['combined']['trades']}</td><td>{H['combined']['win_rate']:.0f}%</td>"
    f"<td>{r(H['combined']['avg_trade'])}</td></tr>"
)

# ── day of week ──────────────────────────────────────────────────────
ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
dow = D["by_dow"]
dmax = max(abs(v["net"]) for v in dow.values())
dow_rows = []
for d in ORDER:
    v = dow.get(d)
    if not v:
        continue
    pct = abs(v["net"]) / dmax * 100
    side = "pos" if v["net"] > 0 else "neg"
    dow_rows.append(
        f"<tr><th scope='row'>{d}</th><td>{v['n']}</td>"
        f"<td>{100 * v['w'] / v['n']:.0f}%</td>"
        f"<td class='{side}'>{r(v['net'])}</td>"
        f"<td class='bar-cell'><span class='bar bar-{side}' style='--p:{pct:.1f}%'></span></td></tr>"
    )

reg = D["regime"]["both"]
cap = D["capital"]
peak = cap["peak_day"]
CH = D["charges"]
RC = D.get("reconciliation") or {}
CP = D.get("compounding") or {}
LC = D.get("live_config") or {}
KP = D.get("capital_profile") or {}
# One source for the execution numbers, so a paragraph cannot quote 6/8/12
# while the config card beside it quotes 10/14/18.
_SLIP = (LC.get("shared") or {}).get("slippage_bps") or {"entry": 0, "exit": 0, "spread": 0}
SRC = D.get("source_comparison") or {}
SZ = D["sizing"]
SL = D["slip"]
SERIES = json.dumps(D["series"], separators=(",", ":"))

CHARGE_ROWS = "".join(
    f"<tr><th scope='row'>{lbl}</th><td>{basis}</td>"
    f"<td class='neg'>{r(CH[k])}</td><td>{100 * CH[k] / CH['total']:.1f}%</td>"
    f"<td>{r(CH[k] / D['headline']['combined']['trades'])}</td></tr>"
    for k, lbl, basis in [
        ("brokerage", "Brokerage", "&#8377;80 flat per round trip"),
        ("exchange", "Exchange transaction", "0.053% of turnover"),
        ("gst", "GST", "18% on brokerage + exchange"),
        ("stt", "STT", "0.0125%, sell side only"),
        ("stamp", "Stamp duty", "0.003% of turnover"),
        ("sebi", "SEBI turnover fee", "&#8377;10 per crore"),
    ]
)

LIVE_ROW = " class='trow-live'"
SIZE_ROWS = "".join(
    f"<tr{LIVE_ROW if s['lots'] == 4 else ''}>"
    f"<th scope='row'>{s['lots']} lot{'s' if s['lots'] > 1 else ''}"
    f"{' &larr; live' if s['lots'] == 4 else ''}</th>"
    f"<td>{r(s['peak'])}</td><td class='neg'>{r(s['dd'])}</td>"
    f"<td><strong>{r(s['funded'])}</strong></td>"
    f"<td class='pos'>{r(s['net'])}</td><td>{r(s['per_year'])}</td>"
    f"<td>{s['roi']}%</td></tr>"
    for s in SZ
)

SLIP_ROWS = "".join(
    f"<tr{LIVE_ROW if x['bps'] == 14 else ''}>"
    f"<th scope='row'>{x['bps'] / 100:.2f}%{' &larr; live model' if x['bps'] == 14 else ''}</th>"
    f"<td class='{cls(x['net'])}'>{r(x['net'])}</td>"
    f"<td class='{'neg' if x['net'] < SL[0]['net'] else 'flat'}'>"
    f"{100 * (x['net'] - SL[0]['net']) / SL[0]['net']:+.0f}%</td>"
    f"<td>{x['win']}%</td></tr>"
    for x in SL
)

DAYS_ROWS = "".join(
    f"<tr><th scope='row'>{b[0]}</th><td class='pos'>{r(b[1])}</td><td>{b[2]}</td>"
    f"<th scope='row' style='text-align:left'>{w[0]}</th><td class='neg'>{r(w[1])}</td>"
    f"<td>{w[2]}</td></tr>"
    for b, w in zip(D["best10"], D["worst10"])
)

EXTRA_CSS = """
.trow-live th, .trow-live td { background:var(--accent-soft); font-weight:700; }
.canvas-wrap { position:relative; width:100%; }
canvas { display:block; width:100%; height:340px; touch-action:pan-y; }
.tip { position:absolute; pointer-events:none; opacity:0; transform:translate(-50%,-100%);
       background:var(--surface); border:1px solid var(--line); border-radius:8px;
       padding:8px 11px; box-shadow:var(--shadow); font-family:var(--mono);
       font-size:11.5px; line-height:1.55; white-space:nowrap; z-index:5;
       transition:opacity .12s ease; }
.tip b { display:block; font-size:10px; letter-spacing:.1em; text-transform:uppercase;
         color:var(--muted); margin-bottom:3px; font-weight:800; }
.legend { display:flex; flex-wrap:wrap; gap:6px 18px; margin-top:12px;
          font-family:var(--mono); font-size:11px; color:var(--muted); }
.legend span { display:inline-flex; align-items:center; gap:7px; }
/* `:not([lang])` matters: the bilingual helper emits <i lang="en">/<i lang="ta">,
   and an unscoped `.legend i` rule collapsed those TEXT spans into a 14x3px
   swatch, so the labels spilled out of their own box and pushed the page
   sideways. Any `i` selector on this page must exclude the language tags. */
.legend i:not([lang]) { width:14px; height:3px; border-radius:2px; display:block; }
.legend i.bar:not([lang]) { height:9px; width:7px; border-radius:2px; }
.risk { display:grid; gap:12px; }
.risk > *, .split > *, .smalls > *, .cfg > * { min-width:0; }
.risk-item, .cfg-card, .panel { min-width:0; }
.tblwrap { max-width:100%; }
.risk-item { border:1px solid var(--line); border-radius:12px; background:var(--surface);
             padding:16px 18px; }
.risk-head { display:flex; align-items:baseline; justify-content:space-between;
             gap:14px; flex-wrap:wrap; margin-bottom:8px; }
.risk-head h3 { margin:0; }
.tag { font-family:var(--mono); font-size:9.5px; font-weight:800; letter-spacing:.13em;
       text-transform:uppercase; padding:3px 9px; border-radius:999px; border:1px solid; }
.tag-hi { color:var(--neg); border-color:rgba(var(--neg-fill),.45); background:rgba(var(--neg-fill),.10); }
.tag-md { color:var(--accent); border-color:var(--line); background:var(--accent-soft); }
.tag-lo { color:var(--muted); border-color:var(--line); background:var(--surface-2); }
.risk-item p { margin:0 0 8px; font-size:13.5px; }
.risk-item p:last-child { margin-bottom:0; }
.risk-item .mit { font-size:13px; color:var(--ink-2); }
.risk-item .mit strong { color:var(--ink); }
.cfg { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }
.cfg-card { border:1px solid var(--line); border-radius:12px; background:var(--surface); overflow:hidden; }
.cfg-card > h3 { margin:0; padding:12px 16px; border-bottom:1px solid var(--line);
                 background:var(--surface-2); font-size:13px; }
.cfg-card dl { margin:0; padding:6px 16px 12px; }
.cfg-rule { font-family:var(--mono); font-size:11.5px; line-height:1.75; color:var(--ink-2);
            padding:10px 16px; border-top:1px solid var(--line-2); }
.cfg-rule b { color:var(--muted); font-weight:800; letter-spacing:.1em; font-size:9.5px;
              text-transform:uppercase; display:block; margin-bottom:4px; }
"""

CHART_JS = """
<script>
(function () {
  var DATA = __SERIES__;
  var cv = document.getElementById('cycle'), tip = document.getElementById('cycle-tip');
  if (!cv) return;
  var box = cv.parentNode, hover = -1, geom = null;

  function tok(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
  function rupee(v) {
    var s = Math.abs(Math.round(v)).toString(), o = '';
    if (s.length > 3) {
      var h = s.slice(0, -3), t = s.slice(-3), p = [];
      while (h.length > 2) { p.unshift(h.slice(-2)); h = h.slice(0, -2); }
      if (h) p.unshift(h);
      o = p.join(',') + ',' + t;
    } else o = s;
    return (v < 0 ? '-' : '') + '\\u20B9' + o;
  }

  function draw() {
    var dpr = window.devicePixelRatio || 1;
    var w = box.clientWidth, h = 340;
    cv.width = w * dpr; cv.height = h * dpr;
    cv.style.height = h + 'px';
    var g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);

    var padL = 66, padR = 14, padT = 14, padB = 108;
    var iw = w - padL - padR, ih = h - padT - padB;
    var cums = DATA.map(function (d) { return d[2]; });
    var days = DATA.map(function (d) { return d[1]; });
    var cMin = Math.min(0, Math.min.apply(null, cums)), cMax = Math.max.apply(null, cums);
    var dMax = Math.max.apply(null, days.map(Math.abs));
    var line = tok('--curve'), muted = tok('--muted'), grid = tok('--line');
    var pos = tok('--pos-fill'), neg = tok('--neg-fill');

    var X = function (i) { return padL + i / (DATA.length - 1) * iw; };
    var Y = function (v) { return padT + (cMax - v) / ((cMax - cMin) || 1) * ih; };

    // grid + rupee axis
    g.font = '10px ui-monospace, Menlo, monospace';
    g.textAlign = 'right'; g.textBaseline = 'middle';
    var steps = 5;
    for (var s = 0; s <= steps; s++) {
      var v = cMin + (cMax - cMin) * s / steps, y = Y(v);
      g.strokeStyle = grid; g.lineWidth = 1;
      g.beginPath(); g.moveTo(padL, y + 0.5); g.lineTo(w - padR, y + 0.5); g.stroke();
      g.fillStyle = muted;
      g.fillText(Math.round(v / 1000) + 'k', padL - 8, y);
    }

    // daily bars, hugging the zero line of their own half-height band
    var bw = Math.max(1, iw / DATA.length * 0.7);
    var barH = 38, barZero = h - 46;
    g.strokeStyle = grid; g.lineWidth = 1;
    g.beginPath(); g.moveTo(padL, barZero + 0.5); g.lineTo(w - padR, barZero + 0.5); g.stroke();
    for (var i = 0; i < DATA.length; i++) {
      var p = DATA[i][1];
      if (!p) continue;
      var hgt = Math.abs(p) / (dMax || 1) * barH;
      g.fillStyle = 'rgba(' + (p > 0 ? pos : neg) + ',' + (i === hover ? 0.95 : 0.45) + ')';
      g.fillRect(X(i) - bw / 2, p > 0 ? barZero - hgt : barZero, bw, hgt);
    }
    g.fillStyle = muted; g.textAlign = 'right'; g.textBaseline = 'middle';
    g.fillText('day', padL - 8, barZero);

    // cumulative line
    g.beginPath();
    for (var j = 0; j < DATA.length; j++) {
      var x = X(j), y = Y(DATA[j][2]);
      j ? g.lineTo(x, y) : g.moveTo(x, y);
    }
    g.strokeStyle = line; g.lineWidth = 1.8; g.lineJoin = 'round'; g.stroke();

    // year ticks
    g.textAlign = 'center'; g.textBaseline = 'top'; g.fillStyle = muted;
    var seen = {};
    for (var k = 0; k < DATA.length; k++) {
      var yr = DATA[k][0].slice(0, 4);
      if (seen[yr]) continue;
      seen[yr] = 1;
      g.strokeStyle = grid;
      g.beginPath(); g.moveTo(X(k) + 0.5, padT); g.lineTo(X(k) + 0.5, h - padB); g.stroke();
      g.fillText(yr, X(k), h - padB + 6);
    }

    if (hover >= 0) {
      g.strokeStyle = muted; g.lineWidth = 1; g.setLineDash([3, 3]);
      g.beginPath(); g.moveTo(X(hover) + 0.5, padT); g.lineTo(X(hover) + 0.5, h - padB); g.stroke();
      g.setLineDash([]);
      g.fillStyle = line;
      g.beginPath(); g.arc(X(hover), Y(DATA[hover][2]), 3.5, 0, 6.284); g.fill();
    }
    geom = { padL: padL, iw: iw, X: X, Y: Y };
  }

  function at(ev) {
    var rect = cv.getBoundingClientRect();
    var x = (ev.touches ? ev.touches[0].clientX : ev.clientX) - rect.left;
    var i = Math.round((x - geom.padL) / geom.iw * (DATA.length - 1));
    return Math.max(0, Math.min(DATA.length - 1, i));
  }
  function show(ev) {
    hover = at(ev); draw();
    var d = DATA[hover];
    tip.innerHTML = '<b>' + d[0] + '</b>' +
      'day ' + rupee(d[1]) + '  &middot; ' + d[3] + ' trade' + (d[3] > 1 ? 's' : '') + '<br>' +
      'cumulative ' + rupee(d[2]);
    tip.style.opacity = 1;
    tip.style.left = Math.min(box.clientWidth - 20, Math.max(70, geom.X(hover))) + 'px';
    tip.style.top = (geom.Y(d[2]) - 12) + 'px';
  }
  function hide() { hover = -1; tip.style.opacity = 0; draw(); }

  cv.addEventListener('mousemove', show);
  cv.addEventListener('mouseleave', hide);
  cv.addEventListener('touchstart', function (e) { show(e); }, { passive: true });
  cv.addEventListener('touchmove', function (e) { show(e); }, { passive: true });
  cv.addEventListener('touchend', hide);
  window.addEventListener('resize', draw);
  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    (mq.addEventListener ? mq.addEventListener.bind(mq, 'change') : mq.addListener.bind(mq))(draw);
  }
  new MutationObserver(draw).observe(document.documentElement,
    { attributes: true, attributeFilter: ['data-theme'] });
  draw();
})();
</script>
""".replace("__SERIES__", SERIES)


# ── full daily ledger ────────────────────────────────────────────────
LEDGER_YEARS = sorted({d[0][:4] for d in D["series"]})
LEDGER_ROWS = "".join(
    f'<tr data-year="{d[0][:4]}">'
    f'<th scope="row">{d[0]}</th>'
    f"<td>{d[3]}</td>"
    f'<td class="{cls(d[1])}">{r(d[1])}</td>'
    f"<td>{r(d[2])}</td></tr>"
    for d in D["series"]
)
LEDGER_BTNS = "".join(f'<button type="button" data-year="{y}" aria-pressed="false">{y}</button>' for y in LEDGER_YEARS)


# ── prose blocks, bilingual ──────────────────────────────────────────
PARA01 = t(
    f"""Restating all three moves the headline from <span class="num">{r(D["as_exported"]["pe"] + D["as_exported"]["ce"])}</span> as exported to <span class="num"><strong>{r(H["combined"]["net"])}</strong></span> net &mdash; <span class="num">{r(H["combined"]["fees"])}</span> of that gap is transaction cost and <span class="num">{r(FC["pe_removed"])}</span> is the unreachable target fill described below. The lower number is the one this document uses throughout.""",
    f"""மூன்றையும் திருத்தியதும் தலைப்பு எண் <span class='num'>{r(D["as_exported"]["pe"] + D["as_exported"]["ce"])}</span> என்பதிலிருந்து <span class='num'><strong>{r(H["combined"]["net"])}</strong></span> நிகரமாக மாறுகிறது &mdash; அந்த இடைவெளியில் <span class='num'>{r(H["combined"]["fees"])}</span> பரிவர்த்தனைக் கட்டணம், <span class='num'>{r(FC["pe_removed"])}</span> கீழே விவரிக்கப்பட்ட அடைய முடியாத இலக்கு விலை. இந்த ஆவணம் முழுவதும் சிறிய எண்ணையே பயன்படுத்துகிறது.""",
)

PARA02 = t(
    f"""<span class="num">{r(CH["brokerage"])}</span> of the <span class="num">{r(CH["total"])}</span> taken &mdash; <strong>{100 * CH["brokerage"] / CH["total"]:.0f}%</strong> &mdash; is the flat &#8377;80 per round trip. It does not shrink with position size, so it is a fixed toll on every trade regardless of how many lots are behind it. Turnover-linked charges (exchange, STT, GST, stamp, SEBI) come to <span class="num">{r(CH["total"] - CH["brokerage"])}</span>, or <span class="num">{100 * (CH["total"] - CH["brokerage"]) / CH["turnover"]:.3f}%</span> of the <span class="num">{r(CH["turnover"])}</span> traded. This is exactly why the small-size rows in the capital table below earn a lower return per rupee.""",
    f"""பிடிக்கப்பட்ட <span class='num'>{r(CH["total"])}</span>-இல் <span class='num'>{r(CH["brokerage"])}</span> &mdash; <strong>{100 * CH["brokerage"] / CH["total"]:.0f}%</strong> &mdash; ஒரு டிரேடுக்கு &#8377;80 என்ற நிலையான புரோக்கரேஜ். பொசிஷன் அளவு கூடினாலும் அது குறையாது; எத்தனை லாட் இருந்தாலும் ஒவ்வொரு டிரேடுக்கும் அது நிலையான சுங்கம். டர்ன்ஓவர் சார்ந்த கட்டணங்கள் (எக்ஸ்சேஞ்ச், STT, GST, ஸ்டாம்ப், SEBI) மொத்தம் <span class='num'>{r(CH["total"] - CH["brokerage"])}</span>, அதாவது வர்த்தகமான <span class='num'>{r(CH["turnover"])}</span>-இல் <span class='num'>{100 * (CH["total"] - CH["brokerage"]) / CH["turnover"]:.3f}%</span>. கீழே உள்ள மூலதன அட்டவணையில் சிறிய அளவுகள் ஏன் குறைவான வருவாய் தருகின்றன என்பதற்கு இதுவே காரணம்.""",
)

PARA03 = t(
    f"""The ten best days are worth {r(sum(b[1] for b in D["best10"]))} and the ten worst {r(sum(w[1] for w in D["worst10"]))} &mdash; a net {r(sum(b[1] for b in D["best10"]) + sum(w[1] for w in D["worst10"]))} from twenty of {DAY["trading_days"]} days, or {100 * (sum(b[1] for b in D["best10"]) + sum(w[1] for w in D["worst10"])) / H["combined"]["net"]:.0f}% of the entire five-year result. This is a fat-tailed return stream and should be sized like one.""",
    f"""சிறந்த பத்து நாட்கள் {r(sum(b[1] for b in D["best10"]))}, மோசமான பத்து நாட்கள் {r(sum(w[1] for w in D["worst10"]))} &mdash; {DAY["trading_days"]} நாட்களில் இருபது நாட்களிலிருந்து நிகரமாக {r(sum(b[1] for b in D["best10"]) + sum(w[1] for w in D["worst10"]))}, அதாவது ஐந்தாண்டு முடிவின் {100 * (sum(b[1] for b in D["best10"]) + sum(w[1] for w in D["worst10"])) / H["combined"]["net"]:.0f}%. இது தடித்த வால் (fat-tailed) வருவாய்; அதற்கேற்பவே அளவு நிர்ணயிக்க வேண்டும்.""",
)

PARA04 = t(
    f"""Both books carry <span class="num">{_SLIP["entry"]} bps</span> entry slippage, <span class="num">{_SLIP["exit"]} bps</span> exit slippage and a <span class="num">{_SLIP["spread"]} bps</span> spread allowance in the engine itself, on top of the charges above. Orders go out as MARKET on both sides under MIS, with a leg stop placed at the broker. Signals are read only from a <strong>closed</strong> 5-minute bar and the fill goes in one second into the next bar, so no trade can act on a candle that has not finished.""",
    f"""இரண்டு புத்தகங்களும் என்ஜினிலேயே <span class='num'>{_SLIP["entry"]} bps</span> நுழைவு ஸ்லிப்பேஜ், <span class='num'>{_SLIP["exit"]} bps</span> வெளியேற்ற ஸ்லிப்பேஜ், <span class='num'>{_SLIP["spread"]} bps</span> ஸ்ப்ரெட் ஒதுக்கீடு ஆகியவற்றை மேற்கண்ட கட்டணங்களுக்கு மேல் கணக்கிடுகின்றன. ஆர்டர்கள் MIS-இல் இரு பக்கமும் MARKET ஆக செல்கின்றன, ஸ்டாப் லாஸ் புரோக்கரிடம் வைக்கப்படுகிறது. சிக்னல் <strong>முடிந்த</strong> 5 நிமிட கேண்டிலிலிருந்து மட்டுமே படிக்கப்படுகிறது; அடுத்த கேண்டில் தொடங்கிய ஒரு வினாடியில் நுழைவு நிகழ்கிறது. எனவே முடியாத கேண்டிலின் மீது எந்த டிரேடும் செயல்பட முடியாது.""",
)

PARA05 = t(
    """<strong>Account to fund</strong> is peak premium outstanding plus the worst drawdown, plus a 30% buffer. It is the number that keeps the strategy alive through its own worst stretch without a top-up.""",
    """<strong>தேவையான கணக்கு</strong> என்பது ஒரு நாளில் நிலுவையில் இருந்த உச்ச பிரீமியம் + மோசமான இறக்கம் + 30% இருப்பு. மேலும் பணம் போடாமல், உத்தி தன் மோசமான காலகட்டத்தையும் தாண்டி உயிர்வாழ இதுவே தேவையான தொகை.""",
)

PARA06 = t(
    f"""<strong>The floor is 1 lot at about {r(SZ[0]["funded"])}.</strong> Below that the position cannot be split further &mdash; one NIFTY lot is the smallest tradable unit, and a single trade needs roughly {r(cap["median"] // 4)} of premium at today's prices.""",
    f"""<strong>அடிமட்டம் 1 லாட், சுமார் {r(SZ[0]["funded"])}.</strong> அதற்குக் கீழே பொசிஷனைப் பிரிக்க முடியாது &mdash; ஒரு NIFTY லாட்தான் மிகச்சிறிய அலகு, ஒரு டிரேடுக்கு இன்றைய விலையில் சுமார் {r(cap["median"] // 4)} பிரீமியம் தேவை.""",
)

PARA07 = t(
    f"""<strong>Live today is 4 lots</strong>, which wants about {r(SZ[3]["funded"])} funded. The engine is set to a &#8377;5,00,000 initial capital with a 4% buffer and capital enforcement on, so it refuses a trade it cannot fund rather than over-committing.""",
    f"""<strong>இன்று லைவ் 4 லாட்</strong>, அதற்கு சுமார் {r(SZ[3]["funded"])} தேவை. என்ஜின் &#8377;5,00,000 தொடக்க மூலதனம், 4% இருப்பு, மூலதன கட்டுப்பாடு ஆன் என அமைக்கப்பட்டுள்ளது &mdash; எனவே பணம் போதாத டிரேடை அது மறுக்கிறதே தவிர அதிகமாக ஈடுபடுத்தாது.""",
)

PARA08 = t(
    f"""Return per rupee funded is <strong>{SZ[0]["roi"]}%</strong> a year at 1 lot and <strong>{SZ[3]["roi"]}%</strong> at 4 &mdash; not because the edge changes, but because &#8377;80 of flat brokerage is the same on both. At 1 lot, charges eat {100 * SZ[0]["charges"] / (SZ[0]["net"] + SZ[0]["charges"]):.0f}% of gross profit; at 4 lots, {100 * SZ[3]["charges"] / (SZ[3]["net"] + SZ[3]["charges"]):.0f}%.""",
    f"""ஒரு ரூபாய்க்கான வருவாய் 1 லாட்டில் ஆண்டுக்கு <strong>{SZ[0]["roi"]}%</strong>, 4 லாட்டில் <strong>{SZ[3]["roi"]}%</strong> &mdash; லாபத் திறன் மாறுவதால் அல்ல, &#8377;80 நிலையான புரோக்கரேஜ் இரண்டிலும் ஒன்றுதான் என்பதால். 1 லாட்டில் கட்டணங்கள் மொத்த லாபத்தில் {100 * SZ[0]["charges"] / (SZ[0]["net"] + SZ[0]["charges"]):.0f}% உண்கின்றன; 4 லாட்டில் {100 * SZ[3]["charges"] / (SZ[3]["net"] + SZ[3]["charges"]):.0f}%.""",
)

PARA09 = t(
    """Practical reading: <strong>1 lot is viable but inefficient</strong> and is best treated as a proving size. Every added lot improves the cost ratio, with most of the gain captured by 3&ndash;4 lots.""",
    """நடைமுறை முடிவு: <strong>1 லாட் சாத்தியம், ஆனால் திறனற்றது</strong> &mdash; அதை சோதனை அளவாகவே கருதுங்கள். ஒவ்வொரு கூடுதல் லாட்டும் கட்டண விகிதத்தை மேம்படுத்துகிறது; பெரும்பாலான பயன் 3&ndash;4 லாட்டிலேயே கிடைத்துவிடுகிறது.""",
)

PARA10 = t(
    f"""<strong>Where it breaks:</strong> the strategy goes to zero at about <strong>{D["breakeven_slip_pct"]}% per side</strong> at 4 lots, and at <strong>0.87%</strong> at 1 lot &mdash; smaller size has less room because flat brokerage is already eating more. The live engine models 6&ndash;8 bps plus a 12 bps spread, so the deployed assumption sits at roughly 0.14% per side, about <strong>8&times; inside</strong> the break-even point. That is the margin of safety; it is not unlimited.""",
    f"""<strong>எங்கே உடைகிறது:</strong> 4 லாட்டில் ஒரு பக்கம் சுமார் <strong>{D["breakeven_slip_pct"]}%</strong> ஸ்லிப்பேஜில் உத்தி பூஜ்ஜியமாகிறது; 1 லாட்டில் <strong>0.87%</strong> &mdash; சிறிய அளவுக்கு இடம் குறைவு, ஏனெனில் நிலையான புரோக்கரேஜ் ஏற்கனவே அதிகம் உண்கிறது. லைவ் என்ஜின் 6&ndash;8 bps + 12 bps ஸ்ப்ரெட் கணக்கிடுகிறது, அதாவது ஒரு பக்கம் சுமார் 0.14% &mdash; உடையும் புள்ளியிலிருந்து சுமார் <strong>8 மடங்கு</strong> உள்ளே. அதுவே பாதுகாப்பு வரம்பு; அது எல்லையற்றது அல்ல.""",
)

PARA11 = t(
    """<strong>What widens it in practice:</strong> a fast-moving open, an event day, a strike further from the money, or a broker routing to a thin book. The &#8377;250-premium strike rule keeps the position near the money where NIFTY weeklies are deepest, which is the main defence.""",
    """<strong>நடைமுறையில் எது அதிகரிக்கும்:</strong> வேகமான ஓப்பனிங், நிகழ்வு நாள், பணத்திலிருந்து தொலைவான ஸ்ட்ரைக், அல்லது மெல்லிய புத்தகத்துக்கு ஆர்டரை அனுப்பும் புரோக்கர். &#8377;250 பிரீமியம் ஸ்ட்ரைக் விதி பொசிஷனை பணத்துக்கு அருகில் வைக்கிறது &mdash; அங்குதான் NIFTY வாராந்திரங்கள் மிக ஆழமானவை. அதுவே முக்கிய பாதுகாப்பு.""",
)

PARA12 = t(
    f"""{100 * reg["after"]["net"] / (reg["after"]["net"] + reg["before"]["net"]):.0f}% of the profit arrives after November 2024, on {100 * reg["after"]["n"] / (reg["after"]["n"] + reg["before"]["n"]):.0f}% of the trades. The four years before that returned {r(reg["before"]["net"])} on {reg["before"]["n"]} trades &mdash; {r(reg["before"]["avg"])} a trade, barely above costs.""",
    f"""லாபத்தில் {100 * reg["after"]["net"] / (reg["after"]["net"] + reg["before"]["net"]):.0f}% நவம்பர் 2024-க்குப் பிறகு வருகிறது, டிரேடுகளில் {100 * reg["after"]["n"] / (reg["after"]["n"] + reg["before"]["n"]):.0f}% மட்டுமே கொண்டு. அதற்கு முந்தைய நான்கு ஆண்டுகள் {reg["before"]["n"]} டிரேடுகளில் {r(reg["before"]["net"])} மட்டுமே தந்தன &mdash; ஒரு டிரேடுக்கு {r(reg["before"]["avg"])}, கட்டணங்களை விட சற்றே அதிகம்.""",
)

PARA13 = t(
    """<strong>The mechanism is structural, not statistical:</strong> NSE cut index weeklies to one per exchange per week and the NIFTY lot went 25 &rarr; 75 in that month. The option's own behaviour did not change &mdash; median win and loss sit near +15% and &minus;16% of premium in every year &mdash; only how often the trade is right.""",
    """<strong>இது கட்டமைப்பு மாற்றம், புள்ளிவிவர தற்செயல் அல்ல:</strong> அந்த மாதத்தில் NSE வாராந்திர இண்டெக்ஸ் எக்ஸ்பயரிகளை ஒரு எக்ஸ்சேஞ்சுக்கு வாரம் ஒன்றாகக் குறைத்தது, NIFTY லாட் 25 &rarr; 75 ஆனது. ஆப்ஷனின் சொந்த நடத்தை மாறவில்லை &mdash; இடைநிலை வெற்றியும் நஷ்டமும் ஒவ்வொரு ஆண்டும் பிரீமியத்தில் +15%, &minus;16% அருகிலேயே இருக்கின்றன &mdash; மாறியது டிரேட் எத்தனை முறை சரியாகிறது என்பதுதான்.""",
)

PARA14 = t(
    """<strong>The exposure:</strong> the current parameters were tuned on post-Nov-2024 data and have never been validated in the weak regime. If the structure shifts again &mdash; and it already has twice, with the weekly expiry moving Thu &rarr; Tue in Sep 2025 and the closing auction changing in Aug 2026 &mdash; the pre-2024 numbers are the better guide to what follows.""",
    """<strong>வெளிப்பாடு:</strong> தற்போதைய அளவுருக்கள் நவம்பர் 2024-க்குப் பிந்தைய தரவில் மட்டுமே சரிசெய்யப்பட்டவை; பலவீனமான காலகட்டத்தில் அவை ஒருபோதும் சோதிக்கப்படவில்லை. கட்டமைப்பு மீண்டும் மாறினால் &mdash; ஏற்கனவே இரண்டு முறை மாறியுள்ளது, செப்டம்பர் 2025-இல் வாராந்திர எக்ஸ்பயரி வியாழன் &rarr; செவ்வாய், ஆகஸ்ட் 2026-இல் இறுதி ஏலம் &mdash; 2024-க்கு முந்தைய எண்களே அடுத்து வருவதற்கான சிறந்த வழிகாட்டி.""",
)

PARA15 = t(
    f"""Twenty days out of {DAY["trading_days"]} account for {100 * (sum(b[1] for b in D["best10"]) + sum(w[1] for w in D["worst10"])) / H["combined"]["net"]:.0f}% of the five-year result. The single best call trade, {r(BW["ce"]["best"]["net"])} on {BW["ce"]["best"]["date"]}, is {BW["ce"]["best"]["net"] / H["ce"]["avg_trade"]:.0f}&times; the average call trade.""",
    f"""{DAY["trading_days"]} நாட்களில் இருபது நாட்கள் ஐந்தாண்டு முடிவின் {100 * (sum(b[1] for b in D["best10"]) + sum(w[1] for w in D["worst10"])) / H["combined"]["net"]:.0f}% ஆகும். {BW["ce"]["best"]["date"]} அன்றைய ஒற்றை சிறந்த CALL டிரேட் {r(BW["ce"]["best"]["net"])}, அது சராசரி CALL டிரேடை விட {BW["ce"]["best"]["net"] / H["ce"]["avg_trade"]:.0f} மடங்கு.""",
)

PARA16 = t(
    """<strong>What that means for a year:</strong> a twelve-month stretch that happens to contain none of those gap days looks nothing like the average. Size against the drawdown column, never against the profit column, and judge the programme over multiple years rather than one.""",
    """<strong>ஓர் ஆண்டுக்கு இதன் பொருள்:</strong> அத்தகைய கேப் நாட்கள் இல்லாத பன்னிரண்டு மாதம் சராசரியைப் போல இருக்காது. லாபப் பத்தியை வைத்து அல்ல, இறக்கப் பத்தியை வைத்தே அளவு நிர்ணயியுங்கள்; ஓராண்டு அல்ல, பல ஆண்டுகளாக இந்த உத்தியை மதிப்பிடுங்கள்.""",
)

PARA17 = t(
    """This is software placing orders on a schedule. The failure modes are ordinary and each has a known mitigation.""",
    """இது ஒரு அட்டவணைப்படி ஆர்டர் இடும் மென்பொருள். தோல்வி வழிகள் சாதாரணமானவை, ஒவ்வொன்றுக்கும் அறியப்பட்ட தீர்வு உண்டு.""",
)

PARA18 = t(
    """<strong>Missed fill window.</strong> A signal arms on the closed bar and must fire one second later. Anything that blocks the process at that instant &mdash; a long backtest running on the same event loop is the known case &mdash; delays or drops the entry. Mitigation: do not run heavy jobs while an engine is armed.""",
    """<strong>தவறிய நுழைவு நேரம்.</strong> சிக்னல் முடிந்த கேண்டிலில் தயாராகி, ஒரு வினாடி கழித்து செயல்பட வேண்டும். அந்த கணத்தில் செயல்பாட்டைத் தடுக்கும் எதுவும் &mdash; அதே இவென்ட் லூப்பில் நீண்ட பேக்டெஸ்ட் ஓடுவது அறியப்பட்ட வழக்கு &mdash; நுழைவை தாமதப்படுத்தும் அல்லது தவறவிடும். தீர்வு: என்ஜின் தயாராக இருக்கும்போது கனமான வேலைகளை ஓட்டாதீர்கள்.""",
)

PARA19 = t(
    """<strong>Broker session.</strong> A single broker token drives quotes and orders; a restart forces a token regeneration and the broker rate-limits that to once every two minutes. Deploys therefore restart trading. Mitigation: no deploys inside market hours.""",
    """<strong>புரோக்கர் அமர்வு.</strong> ஒரே புரோக்கர் டோக்கன்தான் விலைகளையும் ஆர்டர்களையும் இயக்குகிறது; மறுதொடக்கம் டோக்கனை மீண்டும் உருவாக்கச் செய்கிறது, அதை புரோக்கர் இரண்டு நிமிடத்துக்கு ஒரு முறை மட்டுமே அனுமதிக்கிறார். எனவே டெப்ளாய் செய்தால் வர்த்தகம் மறுதொடக்கம் ஆகும். தீர்வு: சந்தை நேரத்தில் டெப்ளாய் வேண்டாம்.""",
)

PARA20 = t(
    """<strong>Data feed.</strong> Quotes arrive over a WebSocket with a REST fallback. A stalled feed means a stale candle, and a stale candle means a signal read off the wrong price.""",
    """<strong>தரவு ஊட்டம்.</strong> விலைகள் WebSocket வழியாக வருகின்றன, REST மாற்று உண்டு. ஊட்டம் நின்றால் கேண்டில் பழையதாகும்; பழைய கேண்டில் என்றால் தவறான விலையில் சிக்னல் படிக்கப்படும்.""",
)

PARA21 = t(
    """<strong>Order rejection.</strong> A market order can be rejected or partially filled. The engine retries once and then abandons the entry, so a rejected trade is a missed trade, not a broken position.""",
    """<strong>ஆர்டர் நிராகரிப்பு.</strong> MARKET ஆர்டர் நிராகரிக்கப்படலாம் அல்லது பகுதியாக நிறைவேறலாம். என்ஜின் ஒரு முறை மீண்டும் முயன்று பின் நுழைவைக் கைவிடுகிறது &mdash; எனவே நிராகரிக்கப்பட்ட டிரேட் என்பது தவறவிட்ட டிரேடே தவிர, உடைந்த பொசிஷன் அல்ல.""",
)

PARA22 = t(
    """<strong>State on restart.</strong> Open positions and the day's counters are persisted and restored on boot. A restart mid-position resumes rather than abandons &mdash; but the session day must be stamped in IST, which was a live defect until 14 Aug 2026 and is now pinned by tests.""",
    """<strong>மறுதொடக்கத்தில் நிலை.</strong> திறந்த பொசிஷன்களும் அன்றைய எண்ணிக்கைகளும் சேமிக்கப்பட்டு துவக்கத்தில் மீட்கப்படுகின்றன. பொசிஷன் நடுவே மறுதொடக்கம் ஆனாலும் அது தொடரும், கைவிடாது &mdash; ஆனால் அமர்வு நாள் IST-இல் பதிக்கப்பட வேண்டும். 14 ஆகஸ்ட் 2026 வரை அது ஒரு நிஜமான குறைபாடாக இருந்தது; இப்போது சோதனைகளால் பூட்டப்பட்டுள்ளது.""",
)

PARA23 = t(
    """The put book exits on a close crossing CPR S1, S2, S3 or TC <em>in either direction</em>. An entry that triggers a few points below S1 is therefore born sitting on its own exit line, and any drift back over it closes the trade. This is by design, but it caps the runway on exactly the entries that trigger closest to support.""",
    """PUT புத்தகம் CPR S1, S2, S3 அல்லது TC-ஐ <em>இரு திசையிலும்</em> கடக்கும் க்ளோஸில் வெளியேறுகிறது. S1-க்கு சில புள்ளிகள் கீழே தூண்டப்படும் நுழைவு, தன் சொந்த வெளியேற்ற கோட்டின் மீதே பிறக்கிறது; சிறிது திரும்பினாலும் டிரேட் மூடப்படுகிறது. இது வடிவமைப்புப்படிதான், ஆனால் ஆதரவுக்கு அருகில் தூண்டப்படும் நுழைவுகளின் இடத்தையே இது சுருக்குகிறது.""",
)

PARA24 = t(
    f"""<strong>Also worth knowing:</strong> both books allow one trade a day, so a stopped-out morning cannot be recovered that session. Monday is the only weekday that loses money over the full record ({r(D["by_dow"]["Monday"]["net"])} on {D["by_dow"]["Monday"]["n"]} trades) and it is reported here rather than quietly filtered out, because removing it after the fact is curve-fitting.""",
    f"""<strong>மேலும் அறிய வேண்டியது:</strong> இரண்டு புத்தகங்களும் நாளுக்கு ஒரு டிரேடே அனுமதிக்கின்றன, எனவே காலையில் ஸ்டாப் ஆனால் அந்த அமர்வில் மீட்க முடியாது. முழு பதிவிலும் திங்கள் மட்டுமே நஷ்டம் தரும் நாள் ({r(D["by_dow"]["Monday"]["net"])}, {D["by_dow"]["Monday"]["n"]} டிரேடுகள்) &mdash; அதை மறைக்காமல் இங்கு தெரிவிக்கிறோம், ஏனெனில் பின்னால் நீக்குவது கர்வ்-ஃபிட்டிங்.""",
)

PARA25 = t(
    """The five-year record assumes every signal was filled at the recorded premium. Live adds rejections, partial fills, queue position and latency. The independent re-pricing section is the partial answer &mdash; the same rules against Upstox's real expired-option history agree with the source on 211 of 211 trading days and on entry premiums to &plusmn;&#8377;0.51 &mdash; but agreement on price is not proof of fill.""",
    """ஐந்தாண்டு பதிவு ஒவ்வொரு சிக்னலும் பதிவான பிரீமியத்தில் நிறைவேறியதாக கருதுகிறது. லைவ் என்பது நிராகரிப்பு, பகுதி நிறைவேற்றம், வரிசை நிலை, தாமதம் ஆகியவற்றைச் சேர்க்கிறது. தனி சரிபார்ப்புப் பகுதி பகுதி பதில் தருகிறது &mdash; அதே விதிகள் Upstox-இன் உண்மையான காலாவதி ஆப்ஷன் வரலாற்றில் 211-இல் 211 வர்த்தக நாட்களிலும், நுழைவு பிரீமியங்கள் &plusmn;&#8377;0.51 வரையிலும் பொருந்துகின்றன &mdash; ஆனால் விலை ஒத்துப்போவது நிறைவேற்றத்துக்கான சான்று அல்ல.""",
)

PARA26 = t(
    """<strong>Honest status:</strong> the put and call books are running as paper engines today. The order path to the broker is built and shares the same timing code, but it has not yet been exercised with real money at size.""",
    """<strong>நேர்மையான நிலை:</strong> PUT மற்றும் CALL புத்தகங்கள் இன்று பேப்பர் என்ஜின்களாக இயங்குகின்றன. புரோக்கருக்கான ஆர்டர் பாதை கட்டப்பட்டுள்ளது, அதே நேரக் குறியீட்டையே பயன்படுத்துகிறது &mdash; ஆனால் அது இன்னும் நிஜப் பணத்தில் அளவோடு சோதிக்கப்படவில்லை.""",
)

PARA27 = t(
    """Options are bought outright, so there is no short-option margin, no margin call and no assignment. Losses are bounded by the premium paid on each trade. Positions are squared off by 15:25 every day, so there is no overnight gap exposure and no carry. There is no leverage beyond the option's own.""",
    """ஆப்ஷன்கள் நேரடியாக வாங்கப்படுகின்றன, எனவே ஷார்ட்-ஆப்ஷன் மார்ஜின், மார்ஜின் கால், அசைன்மென்ட் எதுவும் இல்லை. ஒவ்வொரு டிரேடிலும் நஷ்டம் கட்டிய பிரீமியத்துக்குள் வரையறுக்கப்பட்டது. ஒவ்வொரு நாளும் 15:25-க்குள் பொசிஷன் முடிக்கப்படுகிறது, எனவே இரவு கேப் ரிஸ்க் இல்லை, கேரி இல்லை. ஆப்ஷனுக்கு அப்பால் கூடுதல் லெவரேஜ் இல்லை.""",
)

PARA28 = t(
    """Weekly expiry sits on Thursday for most of this record and moved to Tuesday in Sep 2025, so this table mixes two calendars. Read it as a caution, not a filter.""",
    """இந்தப் பதிவின் பெரும்பகுதியில் வாராந்திர எக்ஸ்பயரி வியாழன்; செப்டம்பர் 2025-இல் அது செவ்வாய்க்கு மாறியது. எனவே இந்த அட்டவணை இரண்டு நாட்காட்டிகளைக் கலக்கிறது. இதை ஒரு எச்சரிக்கையாகப் படியுங்கள், வடிகட்டியாக அல்ல.""",
)

PARA29 = t(
    """Monday is the one weekday that loses money over the full record. It is also the day whose win rate moved most between regimes, which is why it is reported rather than quietly excluded.""",
    """முழுப் பதிவிலும் நஷ்டம் தரும் ஒரே வார நாள் திங்கள். காலகட்டங்களுக்கு இடையே வெற்றி விகிதம் மிக அதிகமாக மாறிய நாளும் அதுவே &mdash; அதனால்தான் அமைதியாக நீக்காமல் இங்கு தெரிவிக்கப்படுகிறது.""",
)

PARA30 = t(
    f"""<strong>{100 * reg["after"]["net"] / (reg["after"]["net"] + reg["before"]["net"]):.0f}% of the profit comes from the last 21 months</strong>, on {100 * reg["after"]["n"] / (reg["after"]["n"] + reg["before"]["n"]):.0f}% of the trades. Per-trade return went from <span class="num">{r(reg["before"]["avg"])}</span> to <span class="num">{r(reg["after"]["avg"])}</span> &mdash; a <span class="num">{reg["after"]["avg"] / reg["before"]["avg"]:.1f}&times;</span> step, not a drift.""",
    f"""<strong>கடைசி 21 மாதங்களிலிருந்து லாபத்தில் {100 * reg["after"]["net"] / (reg["after"]["net"] + reg["before"]["net"]):.0f}% வருகிறது</strong>, டிரேடுகளில் {100 * reg["after"]["n"] / (reg["after"]["n"] + reg["before"]["n"]):.0f}% கொண்டு. ஒரு டிரேடுக்கான வருவாய் <span class='num'>{r(reg["before"]["avg"])}</span> என்பதிலிருந்து <span class='num'>{r(reg["after"]["avg"])}</span> ஆக &mdash; <span class='num'>{reg["after"]["avg"] / reg["before"]["avg"]:.1f} மடங்கு</span> படியேற்றம், மெல்லிய நகர்வு அல்ல.""",
)

PARA31 = t(
    """The option's own behaviour did not change: median win and median loss sit near +15% and &minus;16% of premium in every one of the six years. What changed is how often the trade is right. November 2024 is when NSE cut index weeklies to one per exchange per week and the NIFTY lot went 25 &rarr; 75. That is a market-structure change, not a parameter change.""",
    """ஆப்ஷனின் சொந்த நடத்தை மாறவில்லை: ஆறு ஆண்டுகளிலும் இடைநிலை வெற்றியும் இடைநிலை நஷ்டமும் பிரீமியத்தில் +15%, &minus;16% அருகிலேயே உள்ளன. மாறியது டிரேட் எத்தனை முறை சரியாகிறது என்பதே. நவம்பர் 2024-இல்தான் NSE வாராந்திர இண்டெக்ஸ் எக்ஸ்பயரிகளை ஒரு எக்ஸ்சேஞ்சுக்கு வாரம் ஒன்றாகக் குறைத்தது, NIFTY லாட் 25 &rarr; 75 ஆனது. அது சந்தைக் கட்டமைப்பு மாற்றம், அளவுரு மாற்றம் அல்ல.""",
)

PARA32 = t(
    f"""<strong>The caveat that follows from it:</strong> the current configuration was tuned on post-Nov-2024 data and has never been validated in the weak regime. The 2021&ndash;2024 stretch is what this strategy looks like when the structure does not favour it &mdash; <span class="num">{r(reg["before"]["net"])}</span> over {reg["before"]["n"]} trades and nearly four years.""",
    f"""<strong>அதிலிருந்து வரும் எச்சரிக்கை:</strong> தற்போதைய அமைப்பு நவம்பர் 2024-க்குப் பிந்தைய தரவில் சரிசெய்யப்பட்டது; பலவீனமான காலகட்டத்தில் சோதிக்கப்படவே இல்லை. கட்டமைப்பு சாதகமாக இல்லாதபோது இந்த உத்தி எப்படி இருக்கும் என்பதை 2021&ndash;2024 காலம் காட்டுகிறது &mdash; ஏறக்குறைய நான்கு ஆண்டுகளில் {reg["before"]["n"]} டிரேடுகளில் <span class='num'>{r(reg["before"]["net"])}</span>.""",
)

PARA33 = t(
    f"""Real option premiums exist from {SP["from"]}: Upstox's expired-contract minute bars. Before that date the only record is the exported backtest, so the book on this page is the two joined at that date &mdash; export up to the eve of it, our own engine's real-premium run from it onward, on the same rules. The join is not a guess: each engine run was checked against the export day by day before it was used, and the two agree on which days the strategy trades.""",
    f"""உண்மையான ஆப்ஷன் பிரீமியங்கள் {SP["from"]} முதல் உள்ளன: Upstox இன் காலாவதியான காண்ட்ராக்ட் நிமிட பார்கள். அதற்கு முன் ஏற்றுமதி செய்யப்பட்ட பேக்டெஸ்ட் மட்டுமே பதிவு. எனவே இப்பக்கத்தின் புத்தகம் அந்தத் தேதியில் இரண்டும் இணைந்தது &mdash; அதற்கு முந்தைய நாள் வரை ஏற்றுமதி, அதிலிருந்து அதே விதிகளில் நமது என்ஜினின் உண்மையான பிரீமிய ஓட்டம். இணைப்பு ஊகம் அல்ல: ஒவ்வொரு என்ஜின் ஓட்டமும் பயன்படுத்தும் முன் ஏற்றுமதியுடன் நாள்வாரியாக ஒப்பிடப்பட்டது; உத்தி எந்த நாட்களில் வர்த்தகம் செய்கிறது என்பதில் இரண்டும் ஒத்துப்போகின்றன.""",
)

PARA34 = t(
    f"""Over the window both can see, the put book's real-premium run trades on {SP["pe_engine_from"]} days and the call book's on {SP["ce_engine_from"]}; every one of them is a day the export also traded, and neither trades a day the export did not. The export has {SP["pe_export_only_days"]} put days and {SP["ce_export_only_days"]} call days the engine does not. On the call side {SP["ce_export_only_cooloff"]} of those are the two-day cool-off after a &#8377;20,000 day, which the live engine applies and the export never did; the rest, on both sides, are contracts Upstox holds no minute history for, and they are simply absent rather than invented. On money the two disagree where they should: the export paid its exits at prices no order could get, and the real premiums do not. Put book over the window: {r(SP["pe_engine_net"])} on real premiums against {r(SP["pe_export_net_same_window"])} in the export. Call book: {r(SP["ce_engine_net"])} against {r(SP["ce_export_net_same_window"])} &mdash; the export's extra days lost {r(-SP["ce_export_only_net"])} between them, and on the days both traded the two books are within two percent, because the call book never had a target to be misfilled on.""",
    f"""இரண்டும் காணக்கூடிய காலத்தில், PUT புத்தகத்தின் உண்மையான பிரீமிய ஓட்டம் {SP["pe_engine_from"]} நாட்களிலும் CALL புத்தகம் {SP["ce_engine_from"]} நாட்களிலும் வர்த்தகம் செய்கின்றன; அவை ஒவ்வொன்றும் ஏற்றுமதியும் வர்த்தகம் செய்த நாட்கள்; ஏற்றுமதி செய்யாத நாளில் எதுவும் வர்த்தகம் செய்யவில்லை. என்ஜினில் இல்லாத {SP["pe_export_only_days"]} PUT நாட்களும் {SP["ce_export_only_days"]} CALL நாட்களும் ஏற்றுமதியில் உள்ளன. CALL பக்கத்தில் அவற்றில் {SP["ce_export_only_cooloff"]} நாட்கள் &#8377;20,000 நாளுக்குப் பின்னான இரு நாள் ஓய்வு &mdash; லைவ் என்ஜின் அதைப் பின்பற்றுகிறது, ஏற்றுமதி ஒருபோதும் இல்லை; மீதமுள்ளவை, இரு பக்கமும், Upstox இல் நிமிட வரலாறு இல்லாத காண்ட்ராக்டுகள்; அவை கற்பனை செய்யப்படாமல் விடப்பட்டுள்ளன. பணத்தில் இரண்டும் வேறுபட வேண்டிய இடத்தில் வேறுபடுகின்றன: ஏற்றுமதி எந்த ஆர்டரும் பெற முடியாத விலையில் வெளியேற்றியது; உண்மையான பிரீமியங்கள் அப்படிச் செய்வதில்லை. இக்காலத்தில் PUT புத்தகம்: உண்மையான பிரீமியத்தில் {r(SP["pe_engine_net"])}, ஏற்றுமதியில் {r(SP["pe_export_net_same_window"])}. CALL புத்தகம்: {r(SP["ce_engine_net"])} எதிர் {r(SP["ce_export_net_same_window"])} &mdash; ஏற்றுமதியின் கூடுதல் நாட்கள் மொத்தம் {r(-SP["ce_export_only_net"])} இழந்தன; இரண்டும் வர்த்தகம் செய்த நாட்களில் இரு புத்தகங்களும் இரண்டு சதவீதத்திற்குள், ஏனெனில் CALL புத்தகத்தில் தவறாக நிறைவேற்ற இலக்கே இருந்ததில்லை.""",
)

PARA35 = t(
    """<strong>Sizing.</strong> Four lots per trade. Each trade is sized on the exchange lot in force for <em>that contract's expiry</em> &mdash; 50 through Apr 2024, 25 through Dec 2024 (including the 30 Jan 2025 monthly, which kept the old lot), 75 through Dec 2025, 65 thereafter.""",
    """<strong>அளவு.</strong> ஒரு டிரேடுக்கு நான்கு லாட். ஒவ்வொரு டிரேடும் <em>அந்த ஒப்பந்தத்தின் எக்ஸ்பயரிக்கு</em> அமலில் இருந்த எக்ஸ்சேஞ்ச் லாட்டில் கணக்கிடப்படுகிறது &mdash; ஏப்ரல் 2024 வரை 50, டிசம்பர் 2024 வரை 25 (30 ஜனவரி 2025 மாதாந்திரம் உட்பட, அது பழைய லாட்டையே தக்கவைத்தது), டிசம்பர் 2025 வரை 75, அதன்பின் 65.""",
)

PARA36 = t(
    f"""<strong>Costs.</strong> Flat &#8377;80 brokerage per round trip, STT 0.0125% sell side, exchange transaction 0.053%, GST 18% on brokerage and exchange, SEBI &#8377;10/crore, stamp 0.003%. Total charged: <span class="num">{r(H["combined"]["fees"])}</span> across {H["combined"]["trades"]} trades.""",
    f"""<strong>கட்டணங்கள்.</strong> ஒரு முழு டிரேடுக்கு &#8377;80 நிலையான புரோக்கரேஜ், விற்பனைப் பக்கம் STT 0.0125%, எக்ஸ்சேஞ்ச் பரிவர்த்தனை 0.053%, புரோக்கரேஜ் + எக்ஸ்சேஞ்ச் மீது GST 18%, SEBI கோடிக்கு &#8377;10, ஸ்டாம்ப் 0.003%. {H["combined"]["trades"]} டிரேடுகளில் மொத்தம் வசூலிக்கப்பட்டது: <span class='num'>{r(H["combined"]["fees"])}</span>.""",
)

PARA37 = t(
    f"""<strong>Capital.</strong> Options are bought, not written, so capital at risk is the premium paid. Average <span class="num">{r(cap["avg"])}</span> per trade, peak <span class="num">{r(peak)}</span> deployed on any single day. Return on capital above uses that peak.""",
    f"""<strong>மூலதனம்.</strong> ஆப்ஷன்கள் வாங்கப்படுகின்றன, விற்கப்படுவதில்லை; எனவே ரிஸ்கில் இருக்கும் மூலதனம் கட்டிய பிரீமியமே. ஒரு டிரேடுக்கு சராசரி <span class='num'>{r(cap["avg"])}</span>, ஒரு நாளில் உச்சமாக <span class='num'>{r(peak)}</span> பயன்பாடு. மேலே உள்ள மூலதன வருவாய் அந்த உச்சத்தையே அடிப்படையாகக் கொண்டது.""",
)

PARA38 = t(
    """<strong>Drawdown</strong> is peak-to-trough on the cumulative daily curve, in rupees. Streaks are counted on closed trades in entry order, and separately on net days.""",
    """<strong>இறக்கம்</strong> என்பது ஒட்டுமொத்த தினசரி வளைவில் உச்சத்திலிருந்து பள்ளம் வரை, ரூபாயில். தொடர்ச்சிகள் நுழைவு வரிசைப்படி முடிந்த டிரேடுகளிலும், தனியாக நிகர நாட்களிலும் கணக்கிடப்படுகின்றன.""",
)

PARA39 = t(
    """<strong>No survivorship editing.</strong> Every trade the rules produced is included, including the losing weekday and the losing months.""",
    """<strong>தேர்ந்தெடுத்த திருத்தம் இல்லை.</strong> விதிகள் உருவாக்கிய ஒவ்வொரு டிரேடும் சேர்க்கப்பட்டுள்ளது &mdash; நஷ்டம் தரும் வார நாளும், நஷ்ட மாதங்களும் உட்பட.""",
)

PARA40 = t(
    """It is a backtest. It assumes every signal was filled at the recorded premium, with no rejection, no partial fill and no slippage beyond the modelled costs. Live execution adds all three. Past behaviour of an index, its lot size and its expiry calendar is not a commitment that any of them stay put &mdash; and this record already contains two such changes. Nothing here is investment advice or an offer to manage money.""",
    """இது ஒரு பேக்டெஸ்ட். ஒவ்வொரு சிக்னலும் பதிவான பிரீமியத்தில் நிறைவேறியதாக, நிராகரிப்பு இல்லாமல், பகுதி நிறைவேற்றம் இல்லாமல், கணக்கிட்ட கட்டணங்களுக்கு மேல் ஸ்லிப்பேஜ் இல்லாமல் கருதுகிறது. லைவ் செயல்பாடு இந்த மூன்றையும் சேர்க்கிறது. ஒரு குறியீட்டின் கடந்தகால நடத்தை, அதன் லாட் அளவு, எக்ஸ்பயரி நாட்காட்டி ஆகியவை அப்படியே நீடிக்கும் என்பதற்கு உத்தரவாதம் இல்லை &mdash; இந்தப் பதிவிலேயே அத்தகைய இரண்டு மாற்றங்கள் உள்ளன. இங்குள்ள எதுவும் முதலீட்டு ஆலோசனை அல்ல, பணத்தை நிர்வகிக்கும் சலுகையும் அல்ல.""",
)


PARA44 = t(
    f"""A version of this document published on 14 August 2026 showed {r(FC["combined_published"])} and is superseded. That figure had the target in it, and the target exit was being recorded at the <strong>best price the option touched inside that candle</strong> rather than at the target &mdash; a fill no order can achieve. It affected {FC["trades_overpaid"]} of the put book's {FC["pe_trades"]} trades and overstated the put book by {r(FC["pe_removed"])}. Corrected, that configuration was worth {r(FC["combined_old_honest"])}; without the target it is the {r(H["combined"]["net"])} shown here. The call book never had a target and never carried the error &mdash; checked against real one-minute option prices, {FC["ce_exits_on_candle_high_pct"]}% of its exits land on a candle high against {FC["pe_target_exits_on_candle_high_pct"]}% of the old put targets.""",
    f"""14 ஆகஸ்ட் 2026 அன்று வெளியிடப்பட்ட பதிப்பு {r(FC["combined_published"])} எனக் காட்டியது; அது இப்போது செல்லாது. அந்த எண்ணில் இலக்கு இருந்தது, மேலும் இலக்கு வெளியேற்றம் இலக்கு விலையில் அல்லாமல் <strong>அந்த கேண்டிலில் ஆப்ஷன் தொட்ட உயர்ந்த விலையில்</strong> பதிவாகியது &mdash; எந்த ஆர்டரும் அடைய முடியாத விலை. இது PUT புத்தகத்தின் {FC["pe_trades"]} டிரேடுகளில் {FC["trades_overpaid"]} ஐப் பாதித்து, புத்தகத்தை {r(FC["pe_removed"])} அளவுக்கு மிகைப்படுத்தியது. திருத்தியபின் அந்த அமைப்பு {r(FC["combined_old_honest"])}; இலக்கு இல்லாமல் அது இங்கே காட்டப்படும் {r(H["combined"]["net"])}. CALL புத்தகத்தில் இலக்கு ஒருபோதும் இல்லை, பிழையும் இல்லை &mdash; உண்மையான ஒரு நிமிட விலைகளுடன் சரிபார்க்கையில், அதன் வெளியேற்றங்களில் {FC["ce_exits_on_candle_high_pct"]}% மட்டுமே கேண்டில் உச்சத்தில் விழுகின்றன; பழைய PUT இலக்குகளில் {FC["pe_target_exits_on_candle_high_pct"]}%.""",
)

READER_JS = """
<script>
/* The blueprint reader's behaviours, on this document's own markup: a contents
   rail built from the section headings, scroll-spy, section search and the
   reading-progress bar. Nothing here is loaded from the site — the tearsheet is
   also published as a standalone file, so it has to carry its own copy. */
(function () {
  var body = document.getElementById('document-body');
  var toc = document.getElementById('document-toc');
  var status = document.getElementById('search-status');
  var input = document.getElementById('tearsheet-search');
  if (!body || !toc) return;

  var sections = [].filter.call(body.children, function (el) { return el.tagName === 'SECTION'; });
  var pairs = [];

  sections.forEach(function (sec, i) {
    if (!sec.id) sec.id = 'sec-' + (i + 1);
    var heading = sec.querySelector('.shead h2');
    if (!heading) return;
    var link = document.createElement('a');
    link.href = '#' + sec.id;
    /* The heading is bilingual markup — two <i> elements, one hidden by CSS.
       Copying it wholesale keeps the contents list in whichever language the
       reader has chosen, with no second translation table to maintain. */
    link.innerHTML = heading.innerHTML;
    link.addEventListener('click', function (e) {
      e.preventDefault();
      sec.scrollIntoView({ block: 'start' });
    });
    toc.appendChild(link);
    pairs.push({ section: sec, link: link });
  });

  if ('IntersectionObserver' in window) {
    var spy = new IntersectionObserver(function (entries) {
      var seen = entries.filter(function (e) { return e.isIntersecting; })
        .sort(function (a, b) { return a.boundingClientRect.top - b.boundingClientRect.top; })[0];
      if (!seen) return;
      pairs.forEach(function (p) {
        p.link.classList.toggle('active', p.section === seen.target);
      });
    }, { rootMargin: '-20px 0px -70% 0px', threshold: 0 });
    pairs.forEach(function (p) { spy.observe(p.section); });
  }

  function label(en, ta) {
    return '<span class="tr"><i lang="en">' + en + '</i><i lang="ta">' + ta + '</i></span>';
  }

  function search() {
    var q = input.value.trim().toLowerCase();
    var shown = 0;
    pairs.forEach(function (p) {
      var hit = !q || p.section.textContent.toLowerCase().indexOf(q) !== -1;
      p.section.hidden = !hit;
      p.link.hidden = !hit;
      if (hit) shown += 1;
    });
    status.innerHTML = q
      ? label(shown + ' section' + (shown === 1 ? '' : 's') + ' found', shown + ' \\u0baa\\u0bbf\\u0bb0\\u0bbf\\u0bb5\\u0bc1\\u0b95\\u0bb3\\u0bcd')
      : label('Full document', '\\u0bae\\u0bc1\\u0bb4\\u0bc1 \\u0b86\\u0bb5\\u0ba3\\u0bae\\u0bcd');
    var empty = body.querySelector('.empty-search');
    if (empty) empty.remove();
    if (q && shown === 0) {
      var note = document.createElement('div');
      note.className = 'empty-search';
      note.textContent = 'No section of this tearsheet contains \\u201C' + input.value.trim() + '\\u201D.';
      body.appendChild(note);
    }
  }

  if (input) {
    input.addEventListener('input', search);
    document.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        input.focus();
      }
    });
  }

  var bar = document.getElementById('reading-progress-bar');
  if (bar) {
    var tick = function () {
      var room = document.documentElement.scrollHeight - window.innerHeight;
      bar.style.width = (room > 0 ? Math.min(100, (window.scrollY / room) * 100) : 0) + '%';
    };
    addEventListener('scroll', tick, { passive: true });
    addEventListener('resize', tick, { passive: true });
    tick();
  }
})();
</script>
"""


page = f"""<title>PhilForge Options Tearsheet</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
/* The palette, the grid ground, the type and the card geometry below are the
   PhilForge blueprint reader's (static/architecture-document.css), so this
   document reads as one more page of the site rather than a foreign PDF
   dropped into a frame. Two deliberate departures from that stylesheet:

   1. The accent is VIOLET, not the reader's teal. The Assets viewbar marks each
      document with its own tint — amber for CryptoForge, teal for PhilForge —
      and the tearsheet is a third document, so it takes the reader palette's
      third hue and the tab carries a matching dot.
      Light mode cannot use the bright violet: #a78bfa is 2.65:1 on a white card.
      The light value is set for the WORST light ground it lands on, which is
      not the card but the accent-tinted risk chip (--accent-soft over the card).
      #6d4bd8 clears the card at 5.6:1 and the chip at 4.93:1. Re-check against
      the CHIP, not the card, if it is ever changed.
   2. The cumulative-profit line does NOT use the accent. Teal beside the
      profit-green day bars is two neighbouring hues carrying different meanings;
      the line gets --curve (blue, also from the reader's palette) instead. */
:root {{
  color-scheme: light dark;
  --paper:#eef3f8; --surface:#fafcfe; --surface-2:#eef3f8; --surface-3:#e5edf5;
  --ink:#182436; --ink-2:#33415a; --muted:#596a7f; --dim:#596a7f;
  --line:rgba(38,65,93,.16); --line-2:rgba(38,65,93,.10); --line-strong:rgba(38,65,93,.28);
  --grid:rgba(31,55,82,.055);
  --accent:#6d4bd8; --accent-rgb:109,75,216; --accent-soft:rgba(109,75,216,.09);
  --curve:#2f6fd0; --curve-rgb:47,111,208;
  --pos:#146B4C; --neg:#A2382A; --flat:#5D6874;
  --pos-fill:20,107,76; --neg-fill:162,56,42;
  --shadow:0 1px 2px rgba(15,21,28,.05), 0 8px 24px rgba(15,21,28,.05);
  --sans:"Outfit",ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --paper:#070b13; --surface:#0c1320; --surface-2:#101a2a; --surface-3:#142033;
    --ink:#e5ecf6; --ink-2:#bcc8d4; --muted:#8b98aa; --dim:#8b98aa;
    --line:rgba(126,157,196,.18); --line-2:rgba(126,157,196,.11); --line-strong:rgba(126,157,196,.32);
    --grid:rgba(104,138,178,.045);
    --accent:#a78bfa; --accent-rgb:167,139,250; --accent-soft:rgba(167,139,250,.10);
    --curve:#75adff; --curve-rgb:117,173,255;
    --pos:#43C48D; --neg:#E67D6B; --flat:#8792A0;
    --pos-fill:67,196,141; --neg-fill:230,125,107;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px rgba(0,0,0,.35);
  }}
}}
:root[data-theme="dark"] {{
  --paper:#070b13; --surface:#0c1320; --surface-2:#101a2a; --surface-3:#142033;
  --ink:#e5ecf6; --ink-2:#bcc8d4; --muted:#8b98aa; --dim:#8b98aa;
  --line:rgba(126,157,196,.18); --line-2:rgba(126,157,196,.11); --line-strong:rgba(126,157,196,.32);
  --grid:rgba(104,138,178,.045);
  --accent:#a78bfa; --accent-rgb:167,139,250; --accent-soft:rgba(167,139,250,.10);
  --curve:#75adff; --curve-rgb:117,173,255;
  --pos:#43C48D; --neg:#E67D6B; --flat:#8792A0;
  --pos-fill:67,196,141; --neg-fill:230,125,107;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 30px rgba(0,0,0,.35);
}}
* {{ box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{
  margin:0; color:var(--ink);
  font-family:var(--sans); font-size:15px; line-height:1.6;
  -webkit-text-size-adjust:100%;
  background-color:var(--paper);
  background-image:linear-gradient(var(--grid) 1px,transparent 1px),
                   linear-gradient(90deg,var(--grid) 1px,transparent 1px),
                   radial-gradient(circle at 75% 0%,rgba(var(--accent-rgb),.075),transparent 28%);
  background-size:64px 64px,64px 64px,auto;
}}
.wrap {{ width:min(1500px, calc(100% - 40px)); margin:0 auto; padding:0 0 72px; }}
/* Eyebrow and h1 are set to the reader's exact values (9px/.13em, and a 64px
   cap on a 1-line height), so the two documents measure the same side by side
   rather than merely looking similar. */
.eyebrow {{
  margin:0 0 14px;
  font-family:var(--mono); font-size:9px; font-weight:600;
  letter-spacing:.13em; text-transform:uppercase; color:var(--muted);
}}
.eyebrow > .tr > i, .eyebrow b {{ font-style:normal; }}
.eyebrow b {{ color:var(--accent); margin-right:8px; font-weight:600; }}
h1 {{ max-width:850px; font-size:clamp(38px,5vw,64px); line-height:1; letter-spacing:-.05em;
     margin:0; font-weight:600; text-wrap:balance; }}
h2 {{ font-size:20px; letter-spacing:-.015em; margin:0 0 4px; font-weight:700; text-wrap:balance; }}
h3, .note-h {{ font-size:14px; letter-spacing:-.005em; margin:0 0 10px; font-weight:700; }}
p {{ margin:0 0 12px; max-width:68ch; color:var(--ink-2); }}
a {{ color:var(--accent); }}
.lede {{ font-size:16.5px; color:var(--ink-2); max-width:70ch; }}
.num {{ font-family:var(--mono); font-variant-numeric:tabular-nums; }}
.pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }} .flat {{ color:var(--flat); }}

/* ── Reader chrome: hero, toolbar, rail, section cards ─────────────────────
   Geometry copied from static/architecture-document.css so a reader who
   switches tabs between a blueprint and this tearsheet sees one design. */
.reading-progress {{ position:fixed; inset:0 0 auto; height:2px; z-index:200; background:transparent; }}
.reading-progress span {{ display:block; width:0; height:100%;
  background:linear-gradient(90deg,var(--accent),var(--curve));
  box-shadow:0 0 8px rgba(var(--accent-rgb),.7); }}

.document-hero {{ min-height:340px; display:grid; grid-template-columns:1fr 400px;
  align-items:center; gap:48px; border-bottom:1px solid var(--line); }}
.hero-copy {{ padding:48px 0; min-width:0; }}
.hero-copy .lede {{ max-width:70ch; margin:20px 0 0; color:var(--muted);
  font-size:16px; line-height:1.72; }}
.document-meta {{ margin-top:27px; display:flex; flex-wrap:wrap; gap:8px; }}
.meta-chip {{ min-height:44px; padding:8px 12px; display:grid; align-content:center; gap:4px;
  border:1px solid var(--line); border-radius:8px; background:var(--surface); }}
.meta-chip > span {{ color:var(--dim); font:500 9px var(--mono); letter-spacing:.1em; text-transform:uppercase; }}
.meta-chip strong {{ font:600 11px/1.45 var(--mono); }}

.system-sigil {{ position:relative; width:340px; height:260px; justify-self:center;
  display:grid; place-items:center; }}
.sigil-ring {{ position:absolute; border:1px solid var(--line); border-radius:50%; }}
.ring-one {{ width:250px; height:250px; }}
.ring-two {{ width:205px; height:120px; transform:rotate(-24deg); border-color:rgba(var(--accent-rgb),.26); }}
.ring-three {{ width:138px; height:138px; border-color:rgba(var(--accent-rgb),.34); }}
.sigil-core {{ width:82px; height:82px; display:grid; place-items:center;
  border:1px solid rgba(var(--accent-rgb),.42); border-radius:50%;
  background:rgba(var(--accent-rgb),.1); color:var(--accent); font:600 15px var(--mono);
  box-shadow:0 0 50px rgba(var(--accent-rgb),.09); }}
.sigil-label {{ position:absolute; padding:6px 8px; border:1px solid var(--line); border-radius:6px;
  background:var(--surface); color:var(--muted); font:500 7px var(--mono); letter-spacing:.12em; }}
.label-one {{ right:4px; top:56px; }}
.label-two {{ left:12px; bottom:52px; }}

/* A body on each orbit (Phil, 2026-08-24: "a small planet rotating on the GC
   orbit in every direction ... something sensible and meaningful").

   No new markup: the rings ALREADY exist on every sheet, so each one spins and
   carries a dot on its own edge as ::after. That is why this lives here in the
   parent stylesheet -- all four tearsheets borrow this block, so all four get
   it without touching four builders.

   The directions and speeds are not arbitrary. The inner body runs faster than
   the outer one and the other way round, which is how real orbits read: closer
   in, shorter period. The middle body follows its ellipse with a motion path so
   the ring keeps its deliberate -24 degree tilt.

   `prefers-reduced-motion` stops both, and the whole sigil is already hidden in
   print and on small screens. */
@keyframes sigil-orbit-cw {{ from {{ transform:rotate(0deg); }} to {{ transform:rotate(360deg); }} }}
@keyframes sigil-orbit-ccw {{ from {{ transform:rotate(360deg); }} to {{ transform:rotate(0deg); }} }}
@keyframes sigil-orbit-ellipse {{ from {{ offset-distance:0%; }} to {{ offset-distance:100%; }} }}
.ring-one {{ animation:sigil-orbit-cw 32s linear infinite; }}
.ring-three {{ animation:sigil-orbit-ccw 17s linear infinite; }}
.ring-one::after, .ring-two::after, .ring-three::after {{
  content:''; position:absolute; left:50%; border-radius:50%;
  background:var(--accent); }}
.ring-one::after {{ width:7px; height:7px; top:-4px; margin-left:-3.5px;
  box-shadow:0 0 10px 2px rgba(var(--accent-rgb),.45); opacity:.85; }}
.ring-three::after {{ width:5px; height:5px; top:-3px; margin-left:-2.5px;
  box-shadow:0 0 8px 1px rgba(var(--accent-rgb),.55); }}
.ring-two::after {{ width:6px; height:6px; left:0; top:0;
  offset-path:ellipse(50% 50% at 50% 50%); offset-distance:0%; offset-rotate:0deg;
  animation:sigil-orbit-ellipse 24s linear infinite;
  box-shadow:0 0 9px 1px rgba(var(--accent-rgb),.5); opacity:.92; }}
@media (prefers-reduced-motion: reduce) {{
  .ring-one, .ring-three, .ring-two::after {{ animation:none; }}
}}

/* THE SEARCH BAR FREEZES (Phil, 2026-08-24). The language toggle, the search
   box and the document-state readout are the controls you reach for WHILE
   reading, so they have to stay reachable while the document moves under them.
   `--surface` is translucent, so an opaque backdrop is needed or the text of
   the section scrolling beneath shows through the bar. */
.reader-toolbar {{ min-height:74px; margin:24px 0; padding:11px; display:flex; align-items:center;
  justify-content:space-between; gap:20px; flex-wrap:wrap;
  position:sticky; top:0; z-index:40;
  background-color:var(--paper); background-image:linear-gradient(var(--surface), var(--surface));
  border:1px solid var(--line); border-radius:13px;
  box-shadow:0 6px 18px rgba(0,0,0,.18); }}
@media print {{ .reader-toolbar {{ position:static; box-shadow:none; }} }}
.document-search {{ width:min(420px,100%); min-height:48px; padding:0 12px; display:flex;
  align-items:center; gap:10px; border:1px solid var(--line); border-radius:9px; background:var(--surface-2); }}
.document-search svg {{ width:18px; height:18px; flex:none; fill:none; stroke:var(--dim); stroke-width:1.7; }}
.document-search input {{ min-width:0; flex:1; border:0; outline:0; color:var(--ink);
  background:transparent; font-size:14px; font-family:var(--sans); }}
.document-search input::placeholder {{ color:var(--dim); }}
kbd {{ padding:4px 7px; border:1px solid var(--line-strong); border-radius:5px; color:var(--muted);
  background:var(--surface); font:500 10px var(--mono); }}
.search-status {{ padding-right:10px; color:var(--muted); font:500 10px var(--mono);
  text-transform:uppercase; letter-spacing:.08em; }}

.reader-layout {{ display:grid; grid-template-columns:250px minmax(0,1fr); gap:30px; align-items:start; }}
.document-rail {{ position:sticky; top:98px; align-self:start; min-width:0; }}
.rail-sticky {{ max-height:calc(100vh - 116px); overflow-y:auto; overscroll-behavior:contain;
  padding-right:8px; scrollbar-width:thin; scrollbar-gutter:stable; }}
.rail-label {{ margin:0 0 12px; color:var(--dim); font:600 10px var(--mono); letter-spacing:.12em;
  text-transform:uppercase; }}
#document-toc {{ display:grid; gap:3px; }}
#document-toc a {{ padding:10px 11px; border-left:1px solid var(--line); color:var(--muted);
  text-decoration:none; font-size:12px; line-height:1.45; transition:color .15s, border-color .15s, background .15s; }}
#document-toc a:hover {{ color:var(--ink); background:var(--surface-2); }}
#document-toc a.active {{ color:var(--accent); border-left-color:var(--accent);
  background:rgba(var(--accent-rgb),.055); }}
#document-toc a[hidden] {{ display:none; }}
.rail-card {{ margin-top:20px; padding:14px; display:grid; gap:8px; border:1px solid var(--line);
  border-radius:10px; background:var(--surface); }}
.rail-card > span {{ color:var(--muted); font:500 9px var(--mono); letter-spacing:.1em; }}
.rail-card strong {{ font-size:12px; line-height:1.45; }}
.rail-card strong i:not([lang]) {{ display:inline-block; width:6px; height:6px; margin-right:5px;
  border-radius:50%; background:var(--accent); box-shadow:0 0 8px rgba(var(--accent-rgb),.7); }}
.rail-card small {{ color:var(--muted); font-size:10px; line-height:1.5; }}

.document-body {{ min-width:0; display:grid; gap:15px; counter-reset:sec; }}
.document-body > section {{ min-width:0; max-width:100%; counter-increment:sec;
  scroll-margin-top:18px; padding:28px; border:1px solid var(--line); border-radius:15px;
  background:var(--surface); }}
.document-body > section[hidden] {{ display:none; }}
.document-body > .note {{ counter-increment:none; }}
.shead {{ display:flex; align-items:flex-start; gap:13px;
          border-bottom:1px solid var(--line); padding-bottom:17px; margin-bottom:20px; }}
.shead::before {{ content:"\\00A7" counter(sec); flex:none; min-width:34px; padding:6px 8px;
  border:1px solid rgba(var(--accent-rgb),.3); border-radius:6px; color:var(--accent);
  font:600 10px var(--mono); text-align:center; }}
.shead > div {{ min-width:0; }}
.shead h2 {{ font-size:clamp(22px,2.3vw,30px); letter-spacing:-.03em; line-height:1.18; margin:0; }}
.shead p {{ margin:8px 0 0; font-size:13.5px; color:var(--muted); }}
.empty-search {{ min-height:200px; display:grid; place-items:center; padding:30px;
  border:1px dashed var(--line-strong); border-radius:14px; color:var(--muted); text-align:center; }}

.kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:1px;
         background:var(--line); border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
@media (max-width:900px) {{ .kpis {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
@media (max-width:420px) {{ .kpis {{ grid-template-columns:minmax(0,1fr); }} }}
.kpi {{ background:var(--surface); padding:16px 18px 18px; }}
.kpi-l {{ font-family:var(--mono); font-size:9.5px; font-weight:700; letter-spacing:.16em;
          text-transform:uppercase; color:var(--muted); }}
.kpi-v {{ font-family:var(--mono); font-variant-numeric:tabular-nums;
          font-size:26px; font-weight:700; letter-spacing:-.02em; margin-top:8px; line-height:1.1; }}
.kpi-s {{ font-size:12px; color:var(--muted); margin-top:5px; }}

.panel {{ background:var(--surface); border:1px solid var(--line); border-radius:12px;
          padding:20px 22px; box-shadow:var(--shadow); }}
.panel + .panel {{ margin-top:14px; }}
.note {{ border-left:3px solid var(--accent); background:var(--accent-soft);
         border-radius:0 10px 10px 0; padding:14px 18px; }}
.note p:last-child {{ margin-bottom:0; }}
.note-warn {{ border-left-color:var(--neg); background:rgba(var(--neg-fill),.08); }}

.chart {{ width:100%; overflow-x:auto; }}
svg {{ display:block; width:100%; height:auto; }}
.axis {{ display:flex; justify-content:space-between; flex-wrap:wrap; gap:3px 14px;
         font-family:var(--mono); font-size:10.5px; color:var(--muted); margin-top:8px; }}
.smalls {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:14px; margin-top:14px; }}
.small h3 {{ display:flex; justify-content:space-between; align-items:baseline; gap:10px; margin-bottom:8px; }}
.small h3 span {{ font-family:var(--mono); font-size:13px; font-weight:700; }}

.tblwrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:12px; background:var(--surface); }}
table {{ border-collapse:collapse; width:100%; min-width:520px; }}
th, td {{ padding:9px 14px; text-align:right; font-size:13px; white-space:nowrap;
          font-family:var(--mono); font-variant-numeric:tabular-nums; border-bottom:1px solid var(--line-2); }}
thead th {{ position:sticky; top:0; background:var(--surface-2); color:var(--muted);
            font-size:9.5px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }}
tbody th, td:first-child {{ text-align:left; }}
tbody th {{ font-weight:600; color:var(--ink); }}
tbody tr:last-child td, tbody tr:last-child th {{ border-bottom:0; }}
.trow-total th, .trow-total td {{ border-top:2px solid var(--line); background:var(--surface-2); }}
.bar-cell {{ width:34%; min-width:120px; }}
.bar {{ display:block; height:8px; width:var(--p); border-radius:999px; }}
.bar-pos {{ background:rgba(var(--pos-fill),.55); }}
.bar-neg {{ background:rgba(var(--neg-fill),.55); }}

.mgrid {{ border:1px solid var(--line); border-radius:12px; background:var(--surface);
          padding:14px 16px; overflow-x:auto; }}
.mrow {{ display:grid; grid-template-columns:52px minmax(560px,1fr) 108px; gap:12px; align-items:center; }}
.mrow + .mrow {{ margin-top:6px; }}
.mrow-y {{ font-family:var(--mono); font-size:12px; font-weight:700; color:var(--muted); }}
.mrow-cells {{ display:grid; grid-template-columns:repeat(12,1fr); gap:4px; }}
.mrow-t {{ font-family:var(--mono); font-variant-numeric:tabular-nums;
           font-size:12.5px; font-weight:700; text-align:right; }}
.mcell {{ border-radius:6px; padding:7px 4px 6px; text-align:center; line-height:1.15;
          border:1px solid transparent; }}
.mcell-void {{ background:repeating-linear-gradient(135deg,var(--line-2) 0 4px,transparent 4px 8px);
               border-radius:6px; opacity:.55; }}
/* Alpha is capped at .46, not .88: the value sits ON this tint, and a
   full-strength fill drops the ink to 2.35:1 in dark mode. */
.mcell.pos {{ background:rgba(var(--pos-fill),calc(var(--w) * .40 + .06)); }}
.mcell.neg {{ background:rgba(var(--neg-fill),calc(var(--w) * .40 + .06)); }}
.mcell-m {{ display:block; font-family:var(--mono); font-size:8.5px; font-weight:700;
            letter-spacing:.1em; color:var(--muted); }}
.mcell-v {{ display:block; font-family:var(--mono); font-variant-numeric:tabular-nums;
            font-size:11px; font-weight:700; color:var(--ink); margin-top:2px; }}
.mlegend {{ display:flex; align-items:center; gap:8px; margin-top:12px;
            font-family:var(--mono); font-size:10.5px; color:var(--muted); }}
.mlegend i:not([lang]) {{ display:block; width:34px; height:9px; border-radius:3px; }}

.split {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; }}
.deflist {{ margin:0; }}
.deflist div {{ display:flex; justify-content:space-between; gap:14px; padding:7px 0;
                border-bottom:1px solid var(--line-2); }}
.deflist div:last-child {{ border-bottom:0; }}
.deflist dt {{ font-size:13px; color:var(--ink-2); }}
.deflist dd {{ margin:0; font-family:var(--mono); font-variant-numeric:tabular-nums;
               font-size:13px; font-weight:700; text-align:right; }}
ol.method {{ padding-left:20px; margin:0; }}
ol.method li {{ margin-bottom:10px; color:var(--ink-2); }}
ol.method li:last-child {{ margin-bottom:0; }}
footer {{ margin-top:52px; padding-top:20px; border-top:1px solid var(--line);
          font-size:12.5px; color:var(--muted); }}
@media (max-width:1050px) {{
  .document-hero {{ grid-template-columns:1fr 300px; }}
  .system-sigil {{ width:280px; }}
  .reader-layout {{ grid-template-columns:210px minmax(0,1fr); gap:20px; }}
}}
@media (max-width:760px) {{
  .wrap {{ width:calc(100% - 24px); }}
  .document-hero {{ min-height:0; grid-template-columns:1fr; gap:0; }}
  .hero-copy {{ padding:36px 0 8px; }}
  .system-sigil {{ display:none; }}
  .reader-toolbar {{ display:grid; }}
  .document-search {{ width:100%; }}
  .search-status {{ display:none; }}
  .reader-layout {{ grid-template-columns:minmax(0,1fr); }}
  .document-rail {{ display:none; }}
  .document-body > section {{ padding:20px; }}
}}
@media (max-width:640px) {{
  .wrap {{ padding-bottom:56px; }}
  .mrow {{ grid-template-columns:44px minmax(520px,1fr) 92px; }}
  .kpi-v {{ font-size:22px; }}
  .document-meta {{ display:grid; grid-template-columns:1fr 1fr; }}
  .document-body > section {{ padding:16px; }}
  .shead {{ gap:9px; }}
}}
@media (prefers-reduced-motion:no-preference) {{
  .curve-line {{ stroke-dasharray:4200; stroke-dashoffset:4200; animation:draw 1.5s ease-out forwards; }}
  @keyframes draw {{ to {{ stroke-dashoffset:0; }} }}
}}
@media (prefers-reduced-motion:reduce) {{
  html {{ scroll-behavior:auto; }}
  *, *::before, *::after {{ animation-duration:.01ms !important; transition-duration:.01ms !important; }}
}}
:focus-visible {{ outline:2px solid var(--accent); outline-offset:3px; }}
@media print {{
  body {{ background:#fff; background-image:none; }}
  .reading-progress, .reader-toolbar, .document-rail, .system-sigil, .skip-link {{ display:none !important; }}
  .wrap {{ width:100%; }}
  .reader-layout, .document-body {{ display:block; }}
  .document-body > section {{ break-inside:avoid; margin:0 0 14px; box-shadow:none; }}
}}
{EXTRA_CSS}
{LANG_CSS}
</style>

<div class="reading-progress" aria-hidden="true"><span id="reading-progress-bar"></span></div>

<main class="wrap">

<section class="document-hero">
  <div class="hero-copy">
    <p class="eyebrow"><b>TEARSHEET</b>{t("PhilForge &middot; Strategy Research", "PhilForge &middot; உத்தி ஆய்வு")}</p>
    <h1>{t("NIFTY Weekly Options &mdash; Five-Year Tearsheet", "NIFTY வாராந்திர ஆப்ஷன்ஸ் &mdash; ஐந்தாண்டு அறிக்கை")}</h1>
    <p class="lede">{t("A directional intraday options-buying programme on the NIFTY 50 index, trading a put book and a call book side by side. From October 2024 every trade is priced on the real Upstox option premium of the contract it traded; before that, on the exported backtest. Every trade is on the exchange lot size actually in force on its expiry, and charged real Indian F&amp;O costs. Figures are net.", "NIFTY 50 குறியீட்டில் இன்ட்ராடே ஆப்ஷன் வாங்கும் உத்தி &mdash; ஒரு PUT புத்தகமும் ஒரு CALL புத்தகமும் இணையாக இயங்குகின்றன. அக்டோபர் 2024 முதல் ஒவ்வொரு டிரேடும் அது வர்த்தகம் செய்த காண்ட்ராக்டின் உண்மையான Upstox பிரீமியத்தில்; அதற்கு முன் ஏற்றுமதி செய்யப்பட்ட பேக்டெஸ்டில். ஒவ்வொரு டிரேடும் அதன் எக்ஸ்பயரி அன்று அமலில் இருந்த லாட் அளவில், இந்திய F&amp;O கட்டணங்கள் கழிக்கப்பட்டு. எல்லா எண்களும் நிகரம் (net).")}</p>
    <div class="document-meta" aria-label="Document metadata">
      <div class="meta-chip"><span>{t("Period", "காலம்")}</span><strong>{H["combined"]["first"]} &rarr; {H["combined"]["last"]}</strong></div>
      <div class="meta-chip"><span>{t("Trades", "டிரேடுகள்")}</span><strong>{H["combined"]["trades"]}</strong></div>
      <div class="meta-chip"><span>{t("Position size", "பொசிஷன் அளவு")}</span><strong>{t("4 lots, index weeklies", "4 லாட், வாராந்திர இண்டெக்ஸ்")}</strong></div>
      <div class="meta-chip"><span>{t("Capital at work", "பயன்பட்ட மூலதனம்")}</span><strong>{r(peak)} {t("peak", "உச்சம்")}</strong></div>
      <div class="meta-chip"><span>{t("Costs", "கட்டணங்கள்")}</span><strong>{t("Brokerage, STT, GST, stamp", "புரோக்கரேஜ், STT, GST, ஸ்டாம்ப்")}</strong></div>
    </div>
  </div>
  <div class="system-sigil" aria-hidden="true">
    <div class="sigil-ring ring-one"></div><div class="sigil-ring ring-two"></div><div class="sigil-ring ring-three"></div>
    <div class="sigil-core">5Y</div>
    <span class="sigil-label label-one">PUT BOOK</span><span class="sigil-label label-two">CALL BOOK</span>
  </div>
</section>

<section class="reader-toolbar" aria-label="Document tools">
  <div class="langbar" id="langbar" role="tablist" aria-label="Language / மொழி">
    <button type="button" role="tab" data-lang="en" aria-selected="true">English</button>
    <button type="button" role="tab" data-lang="ta" aria-selected="false">தமிழ்</button>
  </div>
  <label class="document-search" for="tearsheet-search">
    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-4-4"></path></svg>
    <!-- The placeholder is an attribute, so the bilingual helper (which emits
         two <i> elements) cannot be used here; the language script sets it. -->
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
      <strong><i></i> {t("Net of every charge", "அனைத்து கட்டணங்களுக்குப் பின்")}</strong>
      <small>{H["combined"]["trades"]} {t("trades &middot; lot sizes restated &middot; real Upstox premiums from Oct 2024", "டிரேடுகள் &middot; லாட் அளவுகள் திருத்தப்பட்டவை &middot; அக் 2024 முதல் உண்மையான Upstox பிரீமியங்கள்")}</small>
    </div>
  </div>
</aside>

<article class="document-body" id="document-body">

<div class="note">
  <h2 class="note-h">{t("Two corrections were mandatory before any figure here could be quoted", "இங்கு எந்த எண்ணையும் சொல்வதற்கு முன் இரண்டு திருத்தங்கள் கட்டாயம்")}</h2>
  <p>{t("The source export stamps <strong>quantity 260 on every trade in all five years</strong> &mdash; four lots at <em>today's</em> NIFTY lot size &mdash; and reports profit <strong>gross of costs</strong>. The real lot was 50 until Apr 2024, 25 until Dec 2024, then 75, then 65. Left uncorrected, that alone overstates the early years.", "மூல ஏற்றுமதி (export) <strong>ஐந்து ஆண்டுகளின் ஒவ்வொரு டிரேடுக்கும் 260 என்ற அளவையே</strong> பதிக்கிறது &mdash; அதாவது <em>இன்றைய</em> NIFTY லாட் அளவில் நான்கு லாட் &mdash; மேலும் லாபத்தை <strong>கட்டணங்களுக்கு முன்</strong> காட்டுகிறது. உண்மையான லாட் ஏப்ரல் 2024 வரை 50, டிசம்பர் 2024 வரை 25, பின்னர் 75, பின்னர் 65. திருத்தாமல் விட்டால் இதுவே ஆரம்ப ஆண்டுகளை மிகைப்படுத்திக் காட்டும்.")}</p>
  <p>{PARA01}</p>
</div>

<div class="note note-warn">
  <h2 class="note-h">{t("The put book no longer takes a profit target, and an earlier version of this document was overstated", "PUT புத்தகம் இனி இலக்கு எடுப்பதில்லை; இந்த ஆவணத்தின் முந்தைய பதிப்பு மிகைப்படுத்தப்பட்டிருந்தது")}</h2>
  <p>{t("The put book previously exited at a &#8377;10,000 profit target. It has been removed &mdash; the 20% stop, the CPR level exits and the call book are unchanged &mdash; and every put figure on this page is the book running without it.", "PUT புத்தகம் முன்பு &#8377;10,000 இலக்கில் வெளியேறியது. அது நீக்கப்பட்டுவிட்டது &mdash; 20% ஸ்டாப், CPR லெவல் வெளியேற்றங்கள், CALL புத்தகம் அனைத்தும் மாறவில்லை &mdash; இப்பக்கத்தின் ஒவ்வொரு PUT எண்ணும் இலக்கு இல்லாமல் இயங்கிய புத்தகமே.")}</p>
  <p>{PARA44}</p>
</div>

<section>
  <div class="shead"><h2>{t("The programme at a glance", "ஒரே பார்வையில்")}</h2></div>
  <div class="kpis">
    <div class="kpi"><div class="kpi-l">{t("Net profit", "நிகர லாபம்")}</div>
      <div class="kpi-v pos">{r(H["combined"]["net"])}</div>
      <div class="kpi-s">{t("after all charges, 5.6 years", "அனைத்து கட்டணங்களுக்குப் பின், 5.6 ஆண்டுகள்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Return on capital", "மூலதன வருவாய்")}</div>
      <div class="kpi-v pos">{H["combined"]["net"] / peak * 100:.0f}%</div>
      <div class="kpi-s">{t("on", "உச்ச")} {r(peak)} {t("peak deployed", "பயன்பாட்டின் மீது")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Win rate", "வெற்றி விகிதம்")}</div>
      <div class="kpi-v">{H["combined"]["win_rate"]}%</div>
      <div class="kpi-s">{H["combined"]["trades"]} {t("trades, of which", "டிரேடுகளில்")} {H["combined"]["wins"]} {t("won", "வெற்றி")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Profit factor", "லாப காரணி")}</div>
      <div class="kpi-v">{H["combined"]["profit_factor"]}</div>
      <div class="kpi-s">{t("gross won &divide; gross lost", "மொத்த லாபம் &divide; மொத்த நஷ்டம்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Max drawdown", "அதிகபட்ச இறக்கம்")}</div>
      <div class="kpi-v neg">{r(H["combined"]["max_dd"])}</div>
      <div class="kpi-s">{H["combined"]["dd_from"]} &rarr; {H["combined"]["dd_to"]}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Return / drawdown", "வருவாய் / இறக்கம்")}</div>
      <div class="kpi-v">{H["combined"]["return_over_dd"]}&times;</div>
      <div class="kpi-s">{t("profit per rupee of worst dip", "மோசமான இறக்கத்தின் ஒரு ரூபாய்க்கு லாபம்")}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Average trade", "சராசரி டிரேட்")}</div>
      <div class="kpi-v">{r(H["combined"]["avg_trade"])}</div>
      <div class="kpi-s">{t("win", "வெற்றி")} {r(H["combined"]["avg_win"])} &middot; {t("loss", "நஷ்டம்")} {r(H["combined"]["avg_loss"])}</div></div>
    <div class="kpi"><div class="kpi-l">{t("Median hold", "இடைநிலை வைத்திருப்பு")}</div>
      <div class="kpi-v">{H["combined"]["median_hold_min"]}m</div>
      <div class="kpi-s">{t("intraday, squared off same day", "இன்ட்ராடே, அன்றே முடிக்கப்படும்")}</div></div>
  </div>
</section>

<section>
  <div class="shead"><div><h2>{t("Charges, in full", "கட்டணங்கள், முழுமையாக")}</h2>
    <p>{t("Every rupee taken out between the gross result and the money that reaches the account. Nothing here is an estimate of &quot;costs&quot; &mdash; each line is a published rate, applied per trade.", "மொத்த லாபத்துக்கும் கணக்கில் சேரும் பணத்துக்கும் இடையே பிடிக்கப்படும் ஒவ்வொரு ரூபாயும். இங்கு எதுவும் மதிப்பீடு அல்ல &mdash; ஒவ்வொரு வரியும் அறிவிக்கப்பட்ட விகிதம், ஒவ்வொரு டிரேடுக்கும் பயன்படுத்தப்பட்டது.")}</p></div></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Charge", "கட்டணம்")}</th><th scope="col">{t("Basis", "அடிப்படை")}</th><th scope="col">{t("5-year total", "5 ஆண்டு மொத்தம்")}</th>
      <th scope="col">{t("Share", "பங்கு")}</th><th scope="col">{t("Per trade", "ஒரு டிரேடுக்கு")}</th></tr></thead>
    <tbody>
      <tr class="trow-live"><th scope="row">{t("Gross profit", "மொத்த லாபம்")}</th><td>{t("before any charge", "கட்டணங்களுக்கு முன்")}</td>
        <td class="pos">{r(CH["gross"])}</td><td>&mdash;</td><td>{r(CH["gross"] / H["combined"]["trades"])}</td></tr>
      {CHARGE_ROWS}
      <tr class="trow-total"><th scope="row">{t("Total charges", "மொத்த கட்டணம்")}</th><td>{CH["pct_turnover"]}% {t("of turnover", "டர்ன்ஓவரில்")}</td>
        <td class="neg"><strong>{r(CH["total"])}</strong></td><td>100%</td>
        <td class="neg">{r(CH["per_trade"])}</td></tr>
      <tr class="trow-live"><th scope="row">{t("Real net income", "உண்மையான நிகர வருமானம்")}</th><td>{t("what reaches the account", "கணக்கில் சேரும் தொகை")}</td>
        <td class="pos"><strong>{r(CH["net"])}</strong></td><td>&mdash;</td>
        <td class="pos">{r(H["combined"]["avg_trade"])}</td></tr>
    </tbody>
  </table></div>
  <div class="note" style="margin-top:14px">
    <h2 class="note-h">{t("Flat brokerage is the single largest charge, and it is the one that scales badly", "நிலையான புரோக்கரேஜ்தான் மிகப்பெரிய கட்டணம் &mdash; அளவு கூடினாலும் அது குறையாது")}</h2>
    <p>{PARA02}</p>
  </div>
</section>

<section>
  <div class="shead">
    <div><h2>{t("Daily income across the whole cycle", "முழு சுழற்சியின் தினசரி வருமானம்")}</h2>
    <p>{t(f"Every one of the {DAY['trading_days']} trading days in the record. Bars are that day's net income; the line is the running total. Hover any day for its figure.", f"பதிவில் உள்ள {DAY['trading_days']} வர்த்தக நாட்கள் அனைத்தும். கம்பிகள் அந்நாளின் நிகர வருமானம்; கோடு ஓடும் மொத்தம். எந்த நாளின் மீதும் சுட்டியை வையுங்கள்.")}</p></div>
  </div>
  <div class="panel">
    <div class="canvas-wrap">
      <canvas id="cycle" role="img"
        aria-label="Daily profit bars and cumulative net profit for all {DAY["trading_days"]} trading days from {H["combined"]["first"]} to {H["combined"]["last"]}, ending at {r(H["combined"]["net"])}"></canvas>
      <div class="tip" id="cycle-tip" role="status"></div>
    </div>
    <div class="legend">
      <span><i style="background:var(--curve)"></i>{t("cumulative net", "ஒட்டுமொத்த நிகரம்")}</span>
      <span><i class="bar" style="background:rgba(var(--pos-fill),.55)"></i>{t("profitable day", "லாப நாள்")}</span>
      <span><i class="bar" style="background:rgba(var(--neg-fill),.55)"></i>{t("losing day", "நஷ்ட நாள்")}</span>
      <span style="margin-left:auto">{t("hover or drag for any single day", "ஒரு நாளைப் பார்க்க நகர்த்துங்கள்")}</span>
    </div>
    <div class="axis"><span>{DAY["trading_days"]} {t("trading days", "வர்த்தக நாட்கள்")}</span>
      <span>{DAY["green_days"]} {t("green", "பச்சை")} ({100 * DAY["green_days"] / DAY["trading_days"]:.0f}%)</span>
      <span>{t("average day", "சராசரி நாள்")} {r(DAY["avg_day"])} &middot; {t("median", "இடைநிலை")} {r(DAY["median_day"])}</span></div>
  </div>
  <div class="panel" style="margin-top:14px">
    <h3>{t("Ten best and ten worst days in the cycle", "சுழற்சியின் சிறந்த பத்து மற்றும் மோசமான பத்து நாட்கள்")}</h3>
    <div class="tblwrap" style="border:0">
      <table style="min-width:560px">
        <thead><tr><th scope="col">{t("Best day", "சிறந்த நாள்")}</th><th scope="col">{t("Net", "நிகரம்")}</th><th scope="col">{t("Trades", "டிரேடுகள்")}</th>
          <th scope="col">{t("Worst day", "மோசமான நாள்")}</th><th scope="col">{t("Net", "நிகரம்")}</th><th scope="col">{t("Trades", "டிரேடுகள்")}</th></tr></thead>
        <tbody>{DAYS_ROWS}</tbody>
      </table>
    </div>
    <p style="margin:12px 0 0;font-size:13px">{PARA03}</p>
  </div>
</section>

<section>
  <div class="shead"><div><h2>{t("Daily P&amp;L ledger", "தினசரி லாப-நஷ்ட பதிவேடு")}</h2>
    <p>{t(f"Every trading day in the record, with the running balance beside it. Filter by year, or scroll the whole {DAY['trading_days']} rows.", f"பதிவில் உள்ள ஒவ்வொரு வர்த்தக நாளும், அதனுடன் ஓடும் இருப்பும். ஆண்டு வாரியாக வடிகட்டலாம், அல்லது {DAY['trading_days']} வரிகளையும் உருட்டிப் பார்க்கலாம்.")}</p></div></div>
  <div class="ledger-controls" id="ledger-years">
    <button type="button" data-year="all" aria-pressed="true">{t("All", "அனைத்தும்")}</button>
    {LEDGER_BTNS}
  </div>
  <div class="ledger-scroll" id="ledger" tabindex="0" role="region"
       {t_attr("aria-label", "Daily profit and loss ledger", "தினசரி லாப நஷ்ட பதிவு")} data-total="{DAY["trading_days"]}">
    <table>
      <thead><tr>
        <th scope="col">{t("Date", "தேதி")}</th>
        <th scope="col">{t("Trades", "டிரேடுகள்")}</th>
        <th scope="col">{t("Day net", "நாளின் நிகரம்")}</th>
        <th scope="col">{t("Running total", "ஓடும் மொத்தம்")}</th>
      </tr></thead>
      <tbody>{LEDGER_ROWS}</tbody>
    </table>
  </div>
  <div class="ledger-foot">
    <span><span id="ledger-count">{DAY["trading_days"]}</span> {t("days shown", "நாட்கள் காட்டப்படுகின்றன")}</span>
    <span>{t("green", "பச்சை")} {DAY["green_days"]} &middot; {t("red", "சிவப்பு")} {DAY["trading_days"] - DAY["green_days"]}</span>
    <span>{t("average", "சராசரி")} {r(DAY["avg_day"])} &middot; {t("median", "இடைநிலை")} {r(DAY["median_day"])}</span>
    <span style="margin-left:auto">{t("best", "சிறந்தது")} {r(DAY["best_day"][1])} &middot; {t("worst", "மோசமானது")} {r(DAY["worst_day"][1])}</span>
  </div>
</section>

<section>
  <div class="shead">
    <div><h2>{t("Cumulative curve, and each book on its own", "ஒட்டுமொத்த வளைவு, மற்றும் ஒவ்வொரு புத்தகமும் தனித்தனியே")}</h2>
    <p>{t("Shading marks every stretch spent below the previous high &mdash; the flat middle years are as much a part of the record as the climb.", "நிழலிடப்பட்ட பகுதிகள் முந்தைய உச்சத்துக்குக் கீழே கழிந்த காலம். இடையில் உள்ள தட்டையான ஆண்டுகளும் ஏற்றம் போலவே இந்தப் பதிவின் பகுதிதான்.")}</p></div>
  </div>
  <div class="panel">
    <div class="chart">
      <svg viewBox="0 0 1040 260" role="img" preserveAspectRatio="none"
           aria-label="Cumulative net profit from {H["combined"]["first"]} to {H["combined"]["last"]}, ending at {r(H["combined"]["net"])}">
        <path d="{c_dd}" fill="rgba(var(--neg-fill),.13)"/>
        <path d="{c_area}" fill="rgba(var(--curve-rgb),.10)"/>
        <line x1="0" y1="{c_zero:.1f}" x2="1040" y2="{c_zero:.1f}"
              stroke="var(--line)" stroke-width="1" stroke-dasharray="3 4"/>
        <path class="curve-line" d="{c_line}" fill="none" stroke="var(--curve)"
              stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
      </svg>
    </div>
    <div class="axis"><span>{H["combined"]["first"]}</span>
      <span>{t("peak", "உச்சம்")} {r(c_hi)} &middot; {t("shaded = below previous high", "நிழல் = முந்தைய உச்சத்துக்குக் கீழே")}</span>
      <span>{H["combined"]["last"]}</span></div>
  </div>
  <div class="smalls">
    <div class="panel small"><h3>{t("Put book", "PUT புத்தகம்")} <span class="pos">{r(H["pe"]["net"])}</span></h3>
      <svg viewBox="0 0 330 84" role="img" aria-label="Put book cumulative profit, ending {r(H["pe"]["net"])}">
        <line x1="0" y1="{pe_zero:.1f}" x2="330" y2="{pe_zero:.1f}" stroke="var(--line)" stroke-dasharray="3 4"/>
        <path d="{pe_line}" fill="none" stroke="var(--curve)" stroke-width="1.6" vector-effect="non-scaling-stroke"/>
      </svg>
      <div class="axis"><span>{H["pe"]["trades"]} {t("trades", "டிரேடுகள்")}</span><span>{H["pe"]["win_rate"]}% {t("win", "வெற்றி")}</span>
        <span>DD {r(H["pe"]["max_dd"])}</span></div></div>
    <div class="panel small"><h3>{t("Call book", "CALL புத்தகம்")} <span class="pos">{r(H["ce"]["net"])}</span></h3>
      <svg viewBox="0 0 330 84" role="img" aria-label="Call book cumulative profit, ending {r(H["ce"]["net"])}">
        <line x1="0" y1="{ce_zero:.1f}" x2="330" y2="{ce_zero:.1f}" stroke="var(--line)" stroke-dasharray="3 4"/>
        <path d="{ce_line}" fill="none" stroke="var(--curve)" stroke-width="1.6" vector-effect="non-scaling-stroke"/>
      </svg>
      <div class="axis"><span>{H["ce"]["trades"]} {t("trades", "டிரேடுகள்")}</span><span>{H["ce"]["win_rate"]}% {t("win", "வெற்றி")}</span>
        <span>DD {r(H["ce"]["max_dd"])}</span></div></div>
  </div>
</section>

<section>
  <div class="shead"><div><h2>{t("Year by year", "ஆண்டுவாரியாக")}</h2>
    <p>{t("The put and call books are run together; neither is a hedge for the other.", "PUT மற்றும் CALL புத்தகங்கள் ஒன்றாக இயக்கப்படுகின்றன; எதுவும் மற்றொன்றுக்கு ஹெட்ஜ் அல்ல.")}</p></div></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Year", "ஆண்டு")}</th><th scope="col">{t("Put book", "PUT")}</th><th scope="col">{t("Call book", "CALL")}</th>
      <th scope="col">{t("Combined", "மொத்தம்")}</th><th scope="col">{t("Trades", "டிரேடுகள்")}</th><th scope="col">{t("Win", "வெற்றி")}</th>
      <th scope="col">{t("Avg trade", "சராசரி")}</th></tr></thead>
    <tbody>{"".join(yr_rows)}</tbody>
  </table></div>
</section>

<section>
  <div class="shead"><div><h2>{t("Month by month", "மாதவாரியாக")}</h2>
    <p>{t(f"Each cell is that month's net profit in thousands of rupees. {DAY['green_months']} of {DAY['months']} months closed green ({100 * DAY['green_months'] / DAY['months']:.0f}%).", f"ஒவ்வொரு கட்டமும் அந்த மாதத்தின் நிகர லாபம், ஆயிரம் ரூபாயில். {DAY['months']} மாதங்களில் {DAY['green_months']} மாதங்கள் லாபத்தில் முடிந்தன ({100 * DAY['green_months'] / DAY['months']:.0f}%).")}</p></div></div>
  <div class="mgrid">
    {month_grid}
    <div class="mlegend">
      <span>{t("loss", "நஷ்டம்")}</span><i style="background:rgba(var(--neg-fill),.7)"></i>
      <i style="background:rgba(var(--neg-fill),.18)"></i>
      <i style="background:rgba(var(--pos-fill),.18)"></i>
      <i style="background:rgba(var(--pos-fill),.7)"></i><span>{t("profit", "லாபம்")}</span>
      <span style="margin-left:auto">best {D["daily"]["best_month"][0]} {r(D["daily"]["best_month"][1]["net"])}
        &middot; worst {D["daily"]["worst_month"][0]} {r(D["daily"]["worst_month"][1]["net"])}</span>
    </div>
  </div>
</section>


<section>
  <div class="shead"><div><h2>{t("A second measurement, and why it is smaller", "இரண்டாவது அளவீடு &mdash; அது ஏன் சிறியது")}</h2>
    <p>{t("On 8 September 2026 both books were replayed end to end on a different archive, at the size actually being traded, with execution costs charged. It returns a much smaller number than the one above. Every rupee of the difference is named below rather than explained away &mdash; and where the two can be measured the same way, they agree to 3.1%.", "2026 செப்டம்பர் 8 அன்று இரண்டு புத்தகங்களும் வேறு archive-இல், உண்மையில் வர்த்தகம் செய்யப்படும் அளவில், execution செலவுகளுடன் முழுமையாக மீண்டும் இயக்கப்பட்டன. மேலே உள்ளதை விட மிகச் சிறிய எண் வருகிறது. அந்த வித்தியாசத்தின் ஒவ்வொரு ரூபாயும் கீழே பெயரிடப்பட்டுள்ளது &mdash; ஒரே முறையில் அளந்தால் இரண்டும் 3.1% வித்தியாசத்தில் ஒத்துப்போகின்றன.")}</p></div></div>
  <div class="tblwrap" tabindex="0" role="region" {t_attr("aria-label", "Reconciliation between this document and the 8 September replay", "இந்த ஆவணத்துக்கும் செப்டம்பர் 8 மறு-இயக்கத்துக்கும் இடையேயான ஒப்பீடு")}><table>
    <thead><tr><th scope="col">{t("Step", "படி")}</th><th scope="col">{t("What changes", "என்ன மாறுகிறது")}</th><th scope="col">{t("Put book", "PUT புத்தகம்")}</th></tr></thead>
    <tbody>
      <tr><th scope="row">{t("The replay", "மறு-இயக்கம்")}</th>
        <td>{t("Dhan archive, 2 lots, spread and slippage charged, 2021-01 to 2026-08", "Dhan archive, 2 lots, spread மற்றும் slippage பிடிக்கப்பட்டது, 2021-01 முதல் 2026-08 வரை")}</td>
        <td class="pos">{r(RC["bridge"]["pe_dhan_2lot_costed"])}</td></tr>
      <tr><th scope="row">{t("At this document's size", "இந்த ஆவணத்தின் அளவில்")}</th>
        <td>{t("2 lots &rarr; 4 lots. Nothing else changes; the result is linear in size.", "2 lots &rarr; 4 lots. வேறு எதுவும் மாறவில்லை; முடிவு அளவுக்கு நேர்விகிதம்.")}</td>
        <td class="pos">{r(RC["bridge"]["pe_dhan_4lot_costed"])}</td></tr>
      <tr><th scope="row">{t("At this document's prices", "இந்த ஆவணத்தின் விலைகளில்")}</th>
        <td>{t("Dhan prices the SAME contract lower. Where both archives overlap, 93% of trades pick an identical strike, and on those Dhan reads", "Dhan அதே ஒப்பந்தத்தை குறைவாக விலை மதிப்பிடுகிறது. இரு archive-களும் ஒன்றுபடும் இடத்தில் 93% வர்த்தகங்கள் ஒரே strike; அவற்றில் Dhan")} {r(RC["bridge"]["dhan_vs_upstox_same_strike"]["dhan"])} {t("against Upstox's", "&mdash; Upstox-இல்")} {r(RC["bridge"]["dhan_vs_upstox_same_strike"]["upstox"])}. {t("Strike substitution is NOT the cause.", "Strike மாற்றம் காரணம் அல்ல.")}</td>
        <td>+{RC["bridge"]["dhan_vs_upstox_same_strike"]["uplift_pct"]}%</td></tr>
      <tr><th scope="row">{t("On this document's fill basis", "இந்த ஆவணத்தின் fill அடிப்படையில்")}</th>
        <td>{t("This document is an as-filled book. The replay charges 18bps of spread and 10/14bps of entry and exit slippage; removing them on the same window gives", "இந்த ஆவணம் as-filled. மறு-இயக்கம் 18bps spread மற்றும் 10/14bps slippage பிடிக்கிறது; அதே காலத்தில் அவற்றை நீக்கினால்")}</td>
        <td>{r(RC["bridge"]["engine_window_costed"])} &rarr; <strong>{r(RC["bridge"]["engine_window_uncosted"])}</strong></td></tr>
      <tr class="trow-total"><th scope="row">{t("Against this document's own engine half", "இந்த ஆவணத்தின் engine பகுதிக்கு எதிராக")}</th>
        <td>{t("Same window, same archive, same four lots, no costs on either side", "அதே காலம், அதே archive, அதே நான்கு lots, இரு பக்கமும் கட்டணம் இல்லை")} &mdash; {RC["bridge"]["engine_window_trades"]} {t("trades against", "வர்த்தகங்கள்; இங்கே")} {RC["bridge"]["tearsheet_engine_trades"]}</td>
        <td><strong>{r(RC["bridge"]["engine_window_uncosted"])}</strong> {t("vs", "எதிராக")} <strong>{r(RC["bridge"]["tearsheet_engine_portion"])}</strong></td></tr>
    </tbody>
  </table></div>
  <div class="note" style="margin-top:14px">
    <h2 class="note-h">{t("What separates the two numbers is fully named", "இரண்டு எண்களையும் பிரிப்பது முழுமையாக பெயரிடப்பட்டுள்ளது")}</h2>
    <p>{t("Four lots rather than two; an archive that prices the same contract 21% higher; an as-filled basis rather than a costed one; and a first three and three-quarter years taken from broker exports rather than from any replay. None of those is a disagreement about whether the rules work &mdash; on the one window where both are measured identically they differ by 3.1%. Read the headline as this book at four lots on real fills, and the replay as the same book at live size with costs charged. The second is the number to expect from a fresh deployment.", "இரண்டுக்கு பதிலாக நான்கு lots; அதே ஒப்பந்தத்தை 21% அதிகமாக விலை மதிப்பிடும் archive; கட்டணம் பிடிக்காத as-filled அடிப்படை; மற்றும் முதல் மூன்றே முக்கால் ஆண்டுகள் broker export-களிலிருந்து &mdash; மறு-இயக்கத்திலிருந்து அல்ல. இவற்றில் எதுவும் விதிகள் வேலை செய்கின்றனவா என்பதில் கருத்து வேறுபாடு அல்ல &mdash; ஒரே முறையில் அளந்த ஒரே காலத்தில் வித்தியாசம் 3.1%. தலைப்பு எண்ணை நான்கு lots, உண்மையான fills என்றும், மறு-இயக்கத்தை லைவ் அளவில் கட்டணங்களுடன் என்றும் படியுங்கள். புதிய deployment-இல் எதிர்பார்க்க வேண்டியது இரண்டாவது எண்.")}</p>
  </div>
  <div class="note" style="margin-top:14px">
    <h2 class="note-h">{t("What the stricter replay found", "கடுமையான மறு-இயக்கம் கண்டறிந்தது")}</h2>
    <p>{t("Ten trades carry 86% of the call book, and ten carry more than all of the put book &mdash; strip them and it loses", "பத்து வர்த்தகங்கள் CE புத்தகத்தின் 86%; PE புத்தகத்தில் பத்து வர்த்தகங்கள் மொத்தத்தையும் விட அதிகம் &mdash; அவற்றை நீக்கினால் நஷ்டம்")} {r(abs(RC["findings"]["pe_net_without_top10"]))}. {t("So a profit target, a trailing stop, a spread and a half-booking were each measured and each rejected: a 30% target turns the put book into a LOSS while raising its win rate. Weekly expiry moved from Thursday to Tuesday in September 2025, and the put book's money sits on the expiry session in both eras &mdash; Tuesday was", "எனவே target, trailing stop, spread, பாதி booking ஒவ்வொன்றும் அளக்கப்பட்டு நிராகரிக்கப்பட்டன: 30% target PE புத்தகத்தை நஷ்டமாக்குகிறது, வெற்றி விகிதம் உயர்ந்தாலும். Weekly expiry 2025 செப்டம்பரில் வியாழனிலிருந்து செவ்வாய்க்கு மாறியது; இரு காலகட்டங்களிலும் PE-இன் பணம் expiry நாளில் &mdash; செவ்வாய்")} {r(RC["findings"]["expiry_thu_era"]["tue"])} {t("as an ordinary day and", "சாதாரண நாளாக;")} {r(RC["findings"]["expiry_tue_era"]["tue"])} {t("once it became expiry. Three lots on expiry and one otherwise returns", "expiry ஆனபின். Expiry-இல் 3 lots, மற்ற நாட்களில் 1 lot &rarr;")} {r(RC["findings"]["sizing_3on1off"]["net"])} {t("against", "எதிராக")} {r(RC["bridge"]["pe_dhan_2lot_costed"])}{t(", with a smaller worst drawdown and one more green year. All 127 combinations of the exit rules were searched, and choosing the best on history LOST", " &mdash; drawdown குறைவு, ஒரு ஆண்டு கூடுதல் பச்சை. Exit விதிகளின் 127 சேர்க்கைகளும் தேடப்பட்டன; வரலாற்றின் அடிப்படையில் சிறந்ததைத் தேர்ந்தெடுப்பது")} {r(abs(RC["findings"]["rejected"]["exit_walk_forward_vs_unchanged"]))} {t("against changing nothing, so the exits stay exactly as they are.", "நஷ்டம் தந்தது &mdash; எனவே exits அப்படியே இருக்கின்றன.")}</p>
  </div>
</section>


<section>
  <div class="shead"><div><h2>{t("Sizing up as the book earns", "புத்தகம் சம்பாதிக்கும்போது அளவை உயர்த்துதல்")}</h2>
    <p>{t(f"Written at {CP['documented_lots']} lots, which is the size these books are meant to run at. The engine is set to half that today. One more lot for every rung of profit actually banked, on top of the expiry-day size.", f"இந்த புத்தகங்கள் இயங்க வேண்டிய அளவான {CP['documented_lots']} lots-இல் எழுதப்பட்டது. இன்று என்ஜின் அதில் பாதியாக அமைக்கப்பட்டுள்ளது. உண்மையில் சேர்த்த ஒவ்வொரு படி லாபத்துக்கும் ஒரு lot கூடுதல், expiry நாள் அளவுக்கு மேல்.")} {t(" Every number below is produced by the engine sizing and charging each trade &mdash; not by dividing a finished book by its lot count, which is invalid while brokerage is a flat fee per order.", "உண்மையில் சேர்த்த ஒவ்வொரு படி லாபத்துக்கும் ஒரு lot கூடுதல், expiry நாள் அளவுக்கு மேல். கீழுள்ள ஒவ்வொரு எண்ணும் என்ஜின் ஒவ்வொரு வர்த்தகத்தையும் அளவிட்டு கட்டணம் பிடித்து உருவாக்கியது &mdash; முடிந்த புத்தகத்தை lot எண்ணிக்கையால் வகுத்து அல்ல; brokerage ஒரு ஆர்டருக்கு நிலையான கட்டணமாக இருக்கும்வரை அது தவறானது.")}</p></div></div>
  <table class="tbl">
    <thead><tr><th>{t("Book", "புத்தகம்")}</th><th class="num">{t("Trades", "வர்த்தகங்கள்")}</th><th class="num">{t("From &#8377;2,00,000", "&#8377;2,00,000-இலிருந்து")}</th><th class="num">&times;</th><th class="num">{t("Worst fall", "மோசமான வீழ்ச்சி")}</th></tr></thead>
    <tbody>
      <tr><td>CE &mdash; {t(CP["ce"]["flat"]["label"], "2 lots, flat")}</td><td class="num">{CP["ce"]["flat"]["trades"]}</td><td class="num">{r(CP["ce"]["flat"]["final"])}</td><td class="num">{CP["ce"]["flat"]["multiple"]}</td><td class="num neg">{r(CP["ce"]["flat"]["dd_rs"])} ({CP["ce"]["flat"]["dd_pct"]}%)</td></tr>
      <tr><td>CE &mdash; {t(CP["ce"]["expiry"]["label"], "4 lots, expiry-இல் 5")}</td><td class="num">{CP["ce"]["expiry"]["trades"]}</td><td class="num">{r(CP["ce"]["expiry"]["final"])}</td><td class="num">{CP["ce"]["expiry"]["multiple"]}</td><td class="num neg">{r(CP["ce"]["expiry"]["dd_rs"])} ({CP["ce"]["expiry"]["dd_pct"]}%)</td></tr>
      <tr><td><strong>CE &mdash; {t(CP["ce"]["ladder"]["label"], "2 lots, expiry-இல் 3, +25%-க்கு +1")}</strong></td><td class="num">{CP["ce"]["ladder"]["trades"]}</td><td class="num"><strong>{r(CP["ce"]["ladder"]["final"])}</strong></td><td class="num"><strong>{CP["ce"]["ladder"]["multiple"]}</strong></td><td class="num neg">{r(CP["ce"]["ladder"]["dd_rs"])} ({CP["ce"]["ladder"]["dd_pct"]}%)</td></tr>
      <tr><td>PE &mdash; {t(CP["pe"]["flat"]["label"], "2 lots, flat")}</td><td class="num">{CP["pe"]["flat"]["trades"]}</td><td class="num">{r(CP["pe"]["flat"]["final"])}</td><td class="num">{CP["pe"]["flat"]["multiple"]}</td><td class="num neg">{r(CP["pe"]["flat"]["dd_rs"])} ({CP["pe"]["flat"]["dd_pct"]}%)</td></tr>
      <tr><td>PE &mdash; {t(CP["pe"]["expiry"]["label"], "1 lot, expiry-இல் 3")}</td><td class="num">{CP["pe"]["expiry"]["trades"]}</td><td class="num">{r(CP["pe"]["expiry"]["final"])}</td><td class="num">{CP["pe"]["expiry"]["multiple"]}</td><td class="num neg">{r(CP["pe"]["expiry"]["dd_rs"])} ({CP["pe"]["expiry"]["dd_pct"]}%)</td></tr>
      <tr><td><strong>PE &mdash; {t(CP["pe"]["ladder"]["label"], "1 lot, expiry-இல் 3, +75%-க்கு +1")}</strong></td><td class="num">{CP["pe"]["ladder"]["trades"]}</td><td class="num"><strong>{r(CP["pe"]["ladder"]["final"])}</strong></td><td class="num"><strong>{CP["pe"]["ladder"]["multiple"]}</strong></td><td class="num neg">{r(CP["pe"]["ladder"]["dd_rs"])} ({CP["pe"]["ladder"]["dd_pct"]}%)</td></tr>
    </tbody>
  </table>
  <div class="note" style="margin-top:14px">
    <h2 class="note-h">{t("Read this before believing the multiple", "இந்த பெருக்கத்தை நம்புவதற்கு முன் இதைப் படியுங்கள்")}</h2>
    <p>{t("The call book's whole result is one year:", "CE புத்தகத்தின் முழு முடிவும் ஒரே ஆண்டு:")} <strong>{CP["ce"]["top_year_pct"]}%</strong> {t("of it lands in", "லாபம்")} {CP["ce"]["top_year"]}{t(", and the eight months of 2026 LOSE", "-இல்; 2026-இன் எட்டு மாதங்கள்")} {r(abs(CP["ce"]["y2026"]))}{t(" &mdash; at every lot cap tested, while carrying the largest size the ladder had reached. The put book is", " நஷ்டம் &mdash; சோதித்த ஒவ்வொரு lot வரம்பிலும், ladder எட்டிய மிகப்பெரிய அளவை வைத்திருக்கும்போது. PE புத்தகம்")} <strong>{CP["pe"]["top_year_pct"]}%</strong> {t("concentrated in", "செறிவு")} {CP["pe"]["top_year"]}. {t("A ladder multiplies whichever year it is standing in, so it enlarges this concentration rather than diluting it. Switched on today it starts from nothing banked, at the configured base size, and grows only with money actually earned.", "Ladder எந்த ஆண்டில் நிற்கிறதோ அதைப் பெருக்குகிறது; எனவே இந்த செறிவைக் குறைக்காமல் பெரிதாக்குகிறது. இன்று இயக்கினால் எதுவும் சேராத நிலையில், அமைக்கப்பட்ட அடிப்படை அளவில் தொடங்கி, உண்மையில் சம்பாதித்த பணத்துடன் மட்டுமே வளரும்.")}</p>
    <p><strong>{t("How the two are actually traded &mdash; together, on one account.", "இரண்டும் உண்மையில் எப்படி வர்த்தகம் செய்யப்படுகின்றன &mdash; ஒரே கணக்கில், சேர்ந்து.")}</strong> {t("From", "தொடக்கம்")} {r(CP["pair"]["capital"])} {t("across", "&mdash;")} {CP["pair"]["trades"]} {t("trades the pair ends at", "வர்த்தகங்களில் இணை முடிவடைகிறது")} <strong>{r(CP["pair"]["final"])}</strong> ({CP["pair"]["multiple"]}&times;){t(", worst fall", "; மோசமான வீழ்ச்சி")} {r(CP["pair"]["dd_rs"])} ({CP["pair"]["dd_pct"]}%){t(", and the lowest the account ever reached was", "; கணக்கு எட்டிய மிகக் குறைந்த நிலை")} <strong>{r(CP["pair"]["lowest_equity"])}</strong> &mdash; {t("six per cent below where it started, and every large fall in the record gives back profit rather than capital.", "தொடங்கிய இடத்திற்கு ஆறு சதவீதம் கீழே; பதிவில் உள்ள ஒவ்வொரு பெரிய வீழ்ச்சியும் மூலதனத்தை அல்ல, லாபத்தையே திருப்பித் தருகிறது.")} {CP["pair"]["green_years"]}/{CP["pair"]["years"]} {t("green years.", "பச்சை ஆண்டுகள்.")} {t(CP["pair"]["note"], "தனித்தனியாக ஒவ்வொரு புத்தகமும் இணையை விட ஆழமாக விழுகிறது: CE -25.6%, PE -51.1%, இணை -20.3%. அவை வெவ்வேறு மாதங்களில் நஷ்டமடைகின்றன.")}</p>
    <p><strong>{t("What the engine is set to today.", "இன்று என்ஜின் எதற்கு அமைக்கப்பட்டுள்ளது.")}</strong> {CP["running_now"]["lots"]} {t("lots daily and", "lots தினமும்;")} {CP["running_now"]["expiry_day_lots"]} {t("on expiry, the same ladders, capped at", "expiry-இல்; அதே ladder-கள்; வரம்பு")} {CP["running_now"]["max_lots"]}. {t("At that size the pair returns", "அந்த அளவில் இணை தருகிறது")} <strong>{r(CP["running_now"]["pair_final"])}</strong> ({CP["running_now"]["pair_multiple"]}&times;) {t("from", "தொடக்கம்")} {r(CP["running_now"]["pair_capital"])}{t(", worst fall", "; மோசமான வீழ்ச்சி")} {CP["running_now"]["pair_dd_pct"]}%{t(", lowest point", "; மிகக் குறைந்த நிலை")} {r(CP["running_now"]["pair_lowest_equity"])}. {t(CP["running_now"]["note"], "ஆவணப்படுத்தப்பட்ட அளவில் பாதி, முதல் சில மாதங்களுக்கு. விதிகளும் ladder-ம் ஒன்றே; அடிப்படை lot எண்ணிக்கை மட்டுமே வேறு.")}</p>
    <p><strong>{t("Which archive priced this.", "எந்த archive இதற்கு விலை நிர்ணயித்தது.")}</strong> {t("Over the window where both exist, the same rules at four lots return", "இரண்டும் இருக்கும் காலத்தில், அதே விதிகள் நான்கு lots-இல்")} {r(SRC["ce"]["upstox"])} {t("on Upstox against", "Upstox-இல்;")} {r(SRC["ce"]["dhan"])} {t("on Dhan for the call book &mdash; a difference of", "Dhan-இல் CE புத்தகத்துக்கு &mdash; வித்தியாசம்")} {SRC["ce"]["gap_pct"]}%{t(". The put book differs by", ". PE புத்தகம்")} {SRC["pe"]["gap_pct"]}% ({r(SRC["pe"]["upstox"])} {t("against", "எதிராக")} {r(SRC["pe"]["dhan"])}). {t(SRC["finding"], "இரு archive-களும் CE-இல் இரண்டு ஆண்டுகளில் ரூ 152 வித்தியாசத்துக்குள் ஒத்துப்போகின்றன. வித்தியாசம் PE புத்தகத்தில்; அதில் பெரும்பகுதி 2025. 2026 ரூ 1,195 வித்தியாசத்துக்குள் ஒத்துப்போகிறது.")} {t("Priced the better way for each era &mdash;", "ஒவ்வொரு காலத்துக்கும் சிறந்த முறையில் விலை &mdash;")} {t(SRC["splice"]["rule"], "2024-09-30 வரை Dhan, 2024-10-01 முதல் Upstox, முழுவதும் கட்டணங்களுடன்")} &mdash; {t("the two books return", "இரு புத்தகங்களும்")} <strong>{r(SRC["splice"]["combined"])}</strong> {t("at four lots. Every compounding figure above is priced from Dhan alone, which is the more conservative of the two.", "நான்கு lots-இல். மேலுள்ள ஒவ்வொரு compounding எண்ணும் Dhan-இலிருந்து மட்டுமே &mdash; இரண்டில் பழமைவாத தேர்வு.")}</p>
    <p>{t("The put book stops climbing on its own at", "PE புத்தகம் தானாகவே நிற்கிறது")} <strong>{CP["pe"]["ceiling_lots"]} {t("lots", "lots")}</strong>{t(" &mdash; caps above that change nothing. For the call book the cap is a real dial: 4 lots returns", " &mdash; அதற்கு மேல் வரம்பு எதையும் மாற்றாது. CE-க்கு வரம்பு உண்மையான dial: 4 lots")} {r(CP["ce_caps"][0]["final"])} {t("at", "&mdash;")} {CP["ce_caps"][0]["dd_pct"]}%{t(", 20 lots returns", "; 20 lots")} {r(CP["ce_caps"][-1]["final"])} {t("at", "&mdash;")} {CP["ce_caps"][-1]["dd_pct"]}%{t(", and the worst single trade grows from", "; மோசமான ஒற்றை வர்த்தகம்")} {r(abs(CP["ce_caps"][0]["worst_trade"]))} {t("to", "இலிருந்து")} {r(abs(CP["ce_caps"][-1]["worst_trade"]))}. {t("The cap does not reduce the 2026 loss.", "வரம்பு 2026 நஷ்டத்தைக் குறைக்காது.")}</p>
  </div>
</section>


<section>
  <div class="shead"><div><h2>{t("What is running today", "இன்று இயங்குவது என்ன")}</h2>
    <p>{t("The two books as they are configured on the live engine right now, read straight off the deployed state &mdash; not a description of an idealised version.", "இரண்டு புத்தகங்களும் இப்போது லைவ் என்ஜினில் எப்படி அமைக்கப்பட்டுள்ளனவோ அப்படியே &mdash; இயங்கும் நிலையிலிருந்து நேரடியாக எடுக்கப்பட்டது, கற்பனையான பதிப்பு அல்ல.")}</p></div></div>
  <div class="cfg">
    <div class="cfg-card">
      <h3>{t("Put book", "PUT புத்தகம்")} &mdash; {LC["pe"]["run_name"]}</h3>
      <dl class="deflist" style="padding:6px 16px 12px">
        <div><dt>{t("Instrument &amp; expiry", "கருவி &amp; எக்ஸ்பயரி")}</dt><dd>{LC["shared"]["instrument"]}, {LC["shared"]["expiry"]}</dd></div>
        <div><dt>{t("Strike", "ஸ்ட்ரைக்")}</dt><dd>{LC["pe"]["strike"]}, {LC["pe"]["transaction"]}</dd></div>
        <div><dt>{t("Size", "அளவு")}</dt><dd>{LC["pe"]["lots"]} {t("lots", "லாட்")}, {LC["pe"]["expiry_day_lots"]} {t("on expiry", "expiry-இல்")}</dd></div>
        <div><dt>{t("Compounding", "கூட்டு வளர்ச்சி")}</dt><dd>{LC["pe"]["ladder"]}, {t("cap", "வரம்பு")} {LC["shared"]["ladder_cap_lots"]}</dd></div>
        <div><dt>{t("Leg stop", "கால் ஸ்டாப்")}</dt><dd>{LC["pe"]["leg_stop"]}</dd></div>
        <div><dt>{t("Target / trail", "இலக்கு / டிரெயில்")}</dt><dd>{LC["pe"]["target"]} / {LC["pe"]["trail"]}</dd></div>
        <div><dt>{t("Trades per day", "நாளொன்றுக்கு")}</dt><dd>{LC["shared"]["trades_per_day"]} {t("maximum", "அதிகபட்சம்")}</dd></div>
        <div><dt>{t("Cool-off", "ஓய்வு")}</dt><dd>{LC["pe"]["cool_off"]}</dd></div>
        <div><dt>{t("Signal cutoff", "சிக்னல் கட்-ஆஃப்")}</dt><dd>{LC["shared"]["signal_cutoff"]}</dd></div>
        <div><dt>{t("Square-off", "ஸ்கொயர்-ஆஃப்")}</dt><dd>{LC["shared"]["square_off"]}</dd></div>
        <div><dt>{t("Indicators", "இண்டிகேட்டர்கள்")}</dt><dd class="mono">{" &middot; ".join(LC["pe"]["indicators"])}</dd></div>
      </dl>
      <div class="cfg-rule"><b>{t("Exit", "வெளியேற்றம்")}</b>close below Supertrend_10_2 (3m), the leg stop, or 15:25</div>
    </div>
    <div class="cfg-card">
      <h3>{t("Call book", "CALL புத்தகம்")} &mdash; {LC["ce"]["run_name"]}</h3>
      <dl class="deflist" style="padding:6px 16px 12px">
        <div><dt>{t("Instrument &amp; expiry", "கருவி &amp; எக்ஸ்பயரி")}</dt><dd>{LC["shared"]["instrument"]}, {LC["shared"]["expiry"]}</dd></div>
        <div><dt>{t("Strike", "ஸ்ட்ரைக்")}</dt><dd>{LC["ce"]["strike"]}, {LC["ce"]["transaction"]}</dd></div>
        <div><dt>{t("Size", "அளவு")}</dt><dd>{LC["ce"]["lots"]} {t("lots", "லாட்")}, {LC["ce"]["expiry_day_lots"]} {t("on expiry", "expiry-இல்")}</dd></div>
        <div><dt>{t("Compounding", "கூட்டு வளர்ச்சி")}</dt><dd>{LC["ce"]["ladder"]}, {t("cap", "வரம்பு")} {LC["shared"]["ladder_cap_lots"]}</dd></div>
        <div><dt>{t("Leg stop", "கால் ஸ்டாப்")}</dt><dd>{LC["ce"]["leg_stop"]}</dd></div>
        <div><dt>{t("Target / trail", "இலக்கு / டிரெயில்")}</dt><dd>{LC["ce"]["target"]} / {LC["ce"]["trail"]}</dd></div>
        <div><dt>{t("Trades per day", "நாளொன்றுக்கு")}</dt><dd>{LC["shared"]["trades_per_day"]} {t("maximum", "அதிகபட்சம்")}</dd></div>
        <div><dt>{t("Cool-off", "ஓய்வு")}</dt><dd>{LC["ce"]["cool_off"]}</dd></div>
        <div><dt>{t("Signal cutoff", "சிக்னல் கட்-ஆஃப்")}</dt><dd>{LC["shared"]["signal_cutoff"]}</dd></div>
        <div><dt>{t("Square-off", "ஸ்கொயர்-ஆஃப்")}</dt><dd>{LC["shared"]["square_off"]}</dd></div>
        <div><dt>{t("Indicators", "இண்டிகேட்டர்கள்")}</dt><dd class="mono">{" &middot; ".join(LC["ce"]["indicators"])}</dd></div>
      </dl>
      <div class="cfg-rule"><b>{t("Exit", "வெளியேற்றம்")}</b>close below Supertrend_10_2.7 (3m), the leg stop, or 15:25</div>
    </div>
  </div>
  <div class="note" style="margin-top:12px">
    <h2 class="note-h">{t("What it actually ties up", "உண்மையில் என்ன தொகை பிடிபடுகிறது")}</h2>
    <table class="tbl">
      <thead><tr><th>{t("Book", "புத்தகம்")}</th><th class="num">{t("Typical trade", "சராசரி வர்த்தகம்")}</th><th class="num">{t("Average", "சராசரி")}</th><th class="num">{t("Largest single trade", "மிகப்பெரிய ஒற்றை வர்த்தகம்")}</th></tr></thead>
      <tbody>
        <tr><td>{t("Put book", "PUT")} &mdash; {t(KP["live"]["label"], "இயங்கும் அளவு")}</td>
            <td class="num">{r(KP["live"]["pe"]["median"])}</td><td class="num">{r(KP["live"]["pe"]["avg"])}</td><td class="num">{r(KP["live"]["pe"]["worst_single"])}</td></tr>
        <tr><td>{t("Call book", "CALL")} &mdash; {t(KP["live"]["label"], "இயங்கும் அளவு")}</td>
            <td class="num">{r(KP["live"]["ce"]["median"])}</td><td class="num">{r(KP["live"]["ce"]["avg"])}</td><td class="num"><strong>{r(KP["live"]["ce"]["worst_single"])}</strong></td></tr>
      </tbody>
    </table>
    <p>{t("Premium paid is the money at risk, and it is measured here from the engine rather than assumed from a nominal price.", "கட்டிய பிரீமியமே ஆபத்தில் உள்ள பணம்; இது நாமமாத்திர விலையிலிருந்து ஊகிக்கப்படாமல், என்ஜினிலிருந்து அளக்கப்பட்டது.")}
       {t(KP["note"], "இரு புத்தகங்களும் ஒரே நேரத்தில் பொசிஷன் வைத்திருப்பதில்லை &mdash; ஐந்து ஆண்டுகளில் 48 பொது நாட்கள், ஒரு முறை கூட மேற்பொருந்தல் இல்லை &mdash; எனவே கணக்கு இரண்டின் கூட்டுத்தொகையை அல்ல, பெரியதை மட்டுமே சுமக்கிறது. உச்சம் தொடக்க தேவை அல்ல: ladder அதை எட்டுவது சம்பாதித்த பிறகே; என்ஜினின் மூலதன சோதனை சேர்த்த லாபத்தையும் நிதியாகக் கணக்கிடுகிறது. 2 lots-இல் தொடங்கும்போது சராசரி வர்த்தகம் சுமார் ரூ 28,600.")}</p>
  </div>
  <div class="note" style="margin-top:12px">
    <h2 class="note-h">{t("What both books share", "இரு புத்தகங்களும் பகிர்வது")}</h2>
    <p>{t("Account", "கணக்கு")} {r(LC["shared"]["account_capital"])} &middot; {t("capital check", "மூலதன சோதனை")} {LC["shared"]["capital_check"]} &middot;
       {t("ladder measured against", "ladder அளவீடு")} {r(LC["shared"]["ladder_capital"])} &middot;
       {t("market", "சந்தை")} {LC["shared"]["market"]} &middot; {LC["shared"]["orders"]} &middot;
       {t("slippage", "ஸ்லிப்பேஜ்")} {LC["shared"]["slippage_bps"]["entry"]}bps {t("in", "நுழைவு")} / {LC["shared"]["slippage_bps"]["exit"]}bps {t("out", "வெளியேற்றம்")} &middot;
       {t("charges", "கட்டணங்கள்")}: {LC["shared"]["charges"]}.
       {t("Read from the live database on", "லைவ் தரவுத்தளத்திலிருந்து படிக்கப்பட்டது")} {LC["read_from"]}.</p>
  </div>
  <div class="note" style="margin-top:14px">
    <h2 class="note-h">{t("The live engine already prices slippage &mdash; it is not assumed away", "லைவ் என்ஜின் ஏற்கனவே ஸ்லிப்பேஜை கணக்கிடுகிறது &mdash; அது புறக்கணிக்கப்படவில்லை")}</h2>
    <p>{PARA04}</p>
  </div>
</section>

<section>
  <div class="shead"><div><h2>{t("How much capital this needs", "இதற்கு எவ்வளவு மூலதனம் தேவை")}</h2>
    <p>{t("Options are bought, not written, so the money at risk is the premium paid &mdash; there is no margin call. The account still has to carry two things at once: the largest premium ever outstanding in a single day, and the deepest drawdown.", "ஆப்ஷன்கள் வாங்கப்படுகின்றன, விற்கப்படுவதில்லை. எனவே ரிஸ்கில் இருக்கும் பணம் கட்டிய பிரீமியம் மட்டுமே &mdash; மார்ஜின் கால் கிடையாது. இருப்பினும் கணக்கு இரண்டையும் ஒரே நேரத்தில் தாங்க வேண்டும்: ஒரு நாளில் நிலுவையில் இருந்த மிகப்பெரிய பிரீமியம், மற்றும் மிக ஆழமான இறக்கம்.")}</p></div></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Size", "அளவு")}</th><th scope="col">{t("Peak deployed", "உச்ச பயன்பாடு")}</th><th scope="col">{t("Max drawdown", "அதிகபட்ச இறக்கம்")}</th>
      <th scope="col">{t("Account to fund", "தேவையான கணக்கு")}</th><th scope="col">{t("5-yr net", "5 ஆண்டு நிகரம்")}</th><th scope="col">{t("Per year", "ஆண்டுக்கு")}</th>
      <th scope="col">{t("Return", "வருவாய்")}</th></tr></thead>
    <tbody>{SIZE_ROWS}</tbody>
  </table></div>
  <div class="split" style="margin-top:14px">
    <div class="panel">
      <h3>{t("Reading the table", "அட்டவணையை எப்படிப் படிப்பது")}</h3>
      <p style="font-size:13.5px">{PARA05}</p>
      <p style="font-size:13.5px">{PARA06}</p>
      <p style="font-size:13.5px;margin-bottom:0">{PARA07}</p>
    </div>
    <div class="panel">
      <h3>{t("Small size is punished, and by how much", "சிறிய அளவு தண்டிக்கப்படுகிறது &mdash; எவ்வளவு என்பதுடன்")}</h3>
      <p style="font-size:13.5px">{PARA08}</p>
      <p style="font-size:13.5px;margin-bottom:0">{PARA09}</p>
    </div>
  </div>
</section>

<section>
  <div class="shead"><div><h2>{t("Risk register", "ரிஸ்க் பதிவேடு")}</h2>
    <p>{t("What can take this result away, ordered by how much damage each can do. Every figure is measured from the same 935 trades, not asserted.", "இந்த முடிவை எது பறிக்கக்கூடும் என்பது, ஏற்படுத்தும் சேதத்தின் அளவுப்படி வரிசைப்படுத்தப்பட்டுள்ளது. ஒவ்வொரு எண்ணும் இதே 935 டிரேடுகளிலிருந்து அளக்கப்பட்டது, வெறும் கூற்று அல்ல.")}</p></div></div>

  <div class="split" style="margin-bottom:14px">
    <div class="panel">
      <h3>{t("Streaks and depth", "தொடர்ச்சியும் ஆழமும்")}</h3>
      <dl class="deflist">
        <div><dt>{t("Longest losing run, trades", "நீளமான தொடர் நஷ்டம், டிரேடுகள்")}</dt><dd>{H["combined"]["streak_loss"]} in a row</dd></div>
        <div><dt>{t("Longest losing run, days", "நீளமான தொடர் நஷ்டம், நாட்கள்")}</dt><dd>{DAY["day_streak_l"]} in a row</dd></div>
        <div><dt>{t("Longest winning run, trades", "நீளமான தொடர் வெற்றி, டிரேடுகள்")}</dt><dd>{H["combined"]["streak_win"]} in a row</dd></div>
        <div><dt>{t("Max drawdown", "அதிகபட்ச இறக்கம்")}</dt><dd class="neg">{r(H["combined"]["max_dd"])}</dd></div>
        <div><dt>{t("Drawdown vs peak deployed", "இறக்கம் / உச்ச பயன்பாடு")}</dt><dd class="neg">{abs(H["combined"]["max_dd"]) / peak * 100:.0f}%</dd></div>
        <div><dt>{t("Time under water", "மீளாத காலம்")}</dt><dd>{H["combined"]["dd_from"]} &rarr; {H["combined"]["dd_to"]}</dd></div>
      </dl>
    </div>
    <div class="panel">
      <h3>{t("Biggest single outcomes", "மிகப்பெரிய தனி முடிவுகள்")}</h3>
      <dl class="deflist">
        <div><dt>{t("Best put", "சிறந்த PUT")} &mdash; {BW["pe"]["best"]["date"]}</dt><dd class="pos">{r(BW["pe"]["best"]["net"])}</dd></div>
        <div><dt>{t("Worst put", "மோசமான PUT")} &mdash; {BW["pe"]["worst"]["date"]}</dt><dd class="neg">{r(BW["pe"]["worst"]["net"])}</dd></div>
        <div><dt>{t("Best call", "சிறந்த CALL")} &mdash; {BW["ce"]["best"]["date"]}</dt><dd class="pos">{r(BW["ce"]["best"]["net"])}</dd></div>
        <div><dt>{t("Worst call", "மோசமான CALL")} &mdash; {BW["ce"]["worst"]["date"]}</dt><dd class="neg">{r(BW["ce"]["worst"]["net"])}</dd></div>
        <div><dt>{t("Best day", "சிறந்த நாள்")}</dt><dd class="pos">{r(DAY["best_day"][1])}</dd></div>
        <div><dt>{t("Worst day", "மோசமான நாள்")}</dt><dd class="neg">{r(DAY["worst_day"][1])}</dd></div>
      </dl>
    </div>
  </div>

  <div class="risk">

    <div class="risk-item">
      <div class="risk-head"><h3>{t("Slippage &mdash; the largest controllable risk", "ஸ்லிப்பேஜ் &mdash; கட்டுப்படுத்தக்கூடிய மிகப்பெரிய ரிஸ்க்")}</h3>
        <span class="tag tag-hi">{t("High impact", "பெரிய தாக்கம்")}</span></div>
      <p>{t(f"Both books trade MARKET orders on entry and exit. Every basis point given up on the fill comes straight off the result, and it compounds across {H['combined']['trades']} round trips.", f"இரண்டு புத்தகங்களும் நுழைவிலும் வெளியேற்றத்திலும் MARKET ஆர்டர் பயன்படுத்துகின்றன. ஃபில்லில் இழக்கும் ஒவ்வொரு பேசிஸ் பாயிண்டும் நேரடியாக முடிவிலிருந்து கழிக்கப்படுகிறது; அது {H['combined']['trades']} டிரேடுகளிலும் சேர்ந்து பெருகுகிறது.")}</p>
      <div class="tblwrap" style="border:0;margin:10px 0"><table style="min-width:420px">
        <thead><tr><th scope="col">{t("Slippage per side", "ஒரு பக்கம் ஸ்லிப்பேஜ்")}</th><th scope="col">{t("5-year net", "5 ஆண்டு நிகரம்")}</th>
          <th scope="col">{t("Change", "மாற்றம்")}</th><th scope="col">{t("Win rate", "வெற்றி விகிதம்")}</th></tr></thead>
        <tbody>{SLIP_ROWS}</tbody>
      </table></div>
      <p class="mit">{PARA10}</p>
      <p class="mit">{PARA11}</p>
    </div>

    <div class="risk-item">
      <div class="risk-head"><h3>{t("Regime risk &mdash; the edge is not evenly distributed", "சந்தை அமைப்பு ரிஸ்க் &mdash; லாபம் சமமாகப் பரவவில்லை")}</h3>
        <span class="tag tag-hi">{t("High impact", "பெரிய தாக்கம்")}</span></div>
      <p>{PARA12}</p>
      <p class="mit">{PARA13}</p>
      <p class="mit">{PARA14}</p>
    </div>

    <div class="risk-item">
      <div class="risk-head"><h3>{t("Tail concentration &mdash; a few days carry the record", "சில நாட்களே முடிவைத் தாங்குகின்றன")}</h3>
        <span class="tag tag-hi">{t("High impact", "பெரிய தாக்கம்")}</span></div>
      <p>{PARA15}</p>
      <p class="mit">{PARA16}</p>
    </div>

    <div class="risk-item">
      <div class="risk-head"><h3>{t("Mechanical and operational risk", "இயந்திர மற்றும் இயக்க ரிஸ்க்")}</h3>
        <span class="tag tag-md">{t("Real, and mitigable", "நிஜம், ஆனால் தணிக்கக்கூடியது")}</span></div>
      <p>{PARA17}</p>
      <p class="mit">{PARA18}</p>
      <p class="mit">{PARA19}</p>
      <p class="mit">{PARA20}</p>
      <p class="mit">{PARA21}</p>
      <p class="mit">{PARA22}</p>
    </div>

    <div class="risk-item">
      <div class="risk-head"><h3>{t("Rule risk &mdash; where the strategy fights itself", "விதி ரிஸ்க் &mdash; உத்தி தன்னுடனேயே மோதும் இடம்")}</h3>
        <span class="tag tag-md">{t("Structural", "கட்டமைப்புசார்")}</span></div>
      <p>{PARA23}</p>
      <p class="mit">{PARA24}</p>
    </div>

    <div class="risk-item">
      <div class="risk-head"><h3>{t("Backtest-to-live gap", "பேக்டெஸ்ட்-லைவ் இடைவெளி")}</h3>
        <span class="tag tag-md">{t("Inherent", "இயல்பானது")}</span></div>
      <p>{PARA25}</p>
      <p class="mit">{PARA26}</p>
    </div>

    <div class="risk-item">
      <div class="risk-head"><h3>{t("What is <em>not</em> a risk here", "இங்கு ரிஸ்க் <em>இல்லாதவை</em>")}</h3>
        <span class="tag tag-lo">{t("Excluded by design", "வடிவமைப்பாலேயே விலக்கப்பட்டவை")}</span></div>
      <p class="mit">{PARA27}</p>
    </div>

  </div>
</section>


<section>
  <div class="shead"><div><h2>{t("Which day of the week pays", "வாரத்தின் எந்த நாள் லாபம் தருகிறது")}</h2>
    <p>{PARA28}</p></div></div>
  <div class="tblwrap"><table>
    <thead><tr><th scope="col">{t("Day", "நாள்")}</th><th scope="col">{t("Trades", "டிரேடுகள்")}</th><th scope="col">{t("Win", "வெற்றி")}</th>
      <th scope="col">{t("Net", "நிகரம்")}</th><th scope="col">{t("Share", "பங்கு")}</th></tr></thead>
    <tbody>{"".join(dow_rows)}</tbody>
  </table></div>
  <p style="margin-top:12px;font-size:13.5px">{PARA29}</p>
</section>

<section>
  <div class="shead"><div><h2>{t("The finding that matters most", "மிக முக்கியமான கண்டுபிடிப்பு")}</h2>
    <p>{t("Where the profit actually comes from.", "லாபம் உண்மையில் எங்கிருந்து வருகிறது.")}</p></div></div>
  <div class="panel">
    <div class="tblwrap" style="border:0"><table style="min-width:460px">
      <thead><tr><th scope="col">{t("Regime", "காலகட்டம்")}</th><th scope="col">{t("Trades", "டிரேடுகள்")}</th><th scope="col">{t("Win", "வெற்றி")}</th>
        <th scope="col">{t("Net", "நிகரம்")}</th><th scope="col">{t("Per trade", "ஒரு டிரேடுக்கு")}</th></tr></thead>
      <tbody>
        <tr><th scope="row">Jan 2021 &rarr; Oct 2024</th><td>{reg["before"]["n"]}</td>
          <td>{reg["before"]["win"]}%</td><td class="pos">{r(reg["before"]["net"])}</td>
          <td>{r(reg["before"]["avg"])}</td></tr>
        <tr><th scope="row">Nov 2024 &rarr; Aug 2026</th><td>{reg["after"]["n"]}</td>
          <td>{reg["after"]["win"]}%</td><td class="pos">{r(reg["after"]["net"])}</td>
          <td>{r(reg["after"]["avg"])}</td></tr>
      </tbody>
    </table></div>
    <p style="margin-top:16px">{PARA30}</p>
    <p>{PARA31}</p>
    <p>{PARA32}</p>
  </div>
</section>

<section>
  <div class="shead"><div><h2>{t("Two sources, one book: where the seam is", "இரு மூலங்கள், ஒரு புத்தகம்: இணைப்பு எங்கே")}</h2>
    <p>{PARA33}</p></div></div>
  <div class="split">
    <div class="panel">
      <h3>{t("Before", "முன்")} {SP["from"]}: {t("exported backtest", "ஏற்றுமதி செய்யப்பட்ட பேக்டெஸ்ட்")}</h3>
      <dl class="deflist">
        <div><dt>{t("Put trades", "PUT டிரேடுகள்")}</dt><dd>{SP["pe_export_before"]}</dd></div>
        <div><dt>{t("Call trades", "CALL டிரேடுகள்")}</dt><dd>{SP["ce_export_before"]}</dd></div>
        <div><dt>{t("Priced on", "விலை")}</dt><dd>{t("platform export, restated", "தள ஏற்றுமதி, திருத்தப்பட்டது")}</dd></div>
        <div><dt>{t("Why not real premiums", "ஏன் உண்மையான பிரீமியம் இல்லை")}</dt><dd>{t("none exist before Oct 2024", "அக் 2024க்கு முன் இல்லை")}</dd></div>
      </dl>
    </div>
    <div class="panel">
      <h3>{t("From", "இருந்து")} {SP["from"]}: {t("real Upstox premiums", "உண்மையான Upstox பிரீமியங்கள்")}</h3>
      <dl class="deflist">
        <div><dt>{t("Put trades", "PUT டிரேடுகள்")}</dt><dd>{SP["pe_engine_from"]}</dd></div>
        <div><dt>{t("Call trades", "CALL டிரேடுகள்")}</dt><dd>{SP["ce_engine_from"]}</dd></div>
        <div><dt>{t("Priced on", "விலை")}</dt><dd>{t("1-minute contract bars", "1 நிமிட காண்ட்ராக்ட் பார்கள்")}</dd></div>
        <div><dt>{t("Put, this window", "PUT, இந்தக் காலம்")}</dt><dd>{r(SP["pe_engine_net"])} <span class="flat">{t("vs export", "ஏற்றுமதியில்")} {r(SP["pe_export_net_same_window"])}</span></dd></div>
        <div><dt>{t("Call, this window", "CALL, இந்தக் காலம்")}</dt><dd>{r(SP["ce_engine_net"])} <span class="flat">{t("vs export", "ஏற்றுமதியில்")} {r(SP["ce_export_net_same_window"])}</span></dd></div>
      </dl>
    </div>
  </div>
  <div class="note" style="margin-top:14px">
    <h2 class="note-h">{t("The seam joins one strategy to itself", "இணைப்பு ஒரே உத்தியை அதனுடனேயே சேர்க்கிறது")}</h2>
    <p>{PARA34}</p>
  </div>
</section>

<section>
  <div class="shead"><div><h2>{t("Method", "முறை")}</h2></div></div>
  <div class="panel"><ol class="method">
    <li>{PARA35}</li>
    <li>{PARA36}</li>
    <li>{PARA37}</li>
    <li>{PARA38}</li>
    <li>{PARA39}</li>
  </ol></div>
  <div class="note note-warn" style="margin-top:14px">
    <h2 class="note-h">{t("What this document is not", "இந்த ஆவணம் எது அல்ல")}</h2>
    <p>{PARA40}</p>
  </div>
</section>

</article>
</div>

<footer>
  PhilForge strategy research &middot; generated {D["generated"]} &middot;
  {H["combined"]["trades"]} trades restated from source exports and re-priced against
  Upstox expired-option history. Figures in Indian rupees, net of charges.
</footer>

</main>
{CHART_JS}
{LANG_JS}
{READER_JS}
"""

open(OUT, "w").write(page)
print("wrote", OUT, len(page), "bytes")
