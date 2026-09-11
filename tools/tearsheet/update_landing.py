"""Rewrite the Dojima landing's figures from report_data.json.

The landing's numbers used to be typed in by hand, in two files, which is how
they came to sit fifteen days behind the book: nothing regenerated them and
nothing compared them to anything. This makes them generated, so the weekly
refresh is a command rather than a careful afternoon.

    python3 update_landing.py --check   # non-zero if the page is behind
    python3 update_landing.py --write   # rewrite it

Every value is anchored on the label beside it, never on the old number, so this
is idempotent and safe to re-run. The headline tape uses only its own dataset;
legacy re-pricing and reconciliation figures belong to their separate scenarios.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_LANDING = _HERE.parent.parent / "static" / "landing"
DATA = _HERE / "report_data.json"

_WORDS = {
    18: "eighteen",
    19: "nineteen",
    20: "twenty",
    21: "twenty-one",
    22: "twenty-two",
    23: "twenty-three",
    24: "twenty-four",
    25: "twenty-five",
}
_CARDINAL = {
    46: "Forty-six",
    47: "Forty-seven",
    48: "Forty-eight",
    49: "Forty-nine",
    50: "Fifty",
    51: "Fifty-one",
    52: "Fifty-two",
}


def inr(value: float) -> str:
    """Indian digit grouping, the way the page prints it."""
    number = int(round(value))
    digits = str(abs(number))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        digits = ",".join(parts + [tail])
    return ("-" if number < 0 else "") + digits


def _stat(html: str, label: str, value: int) -> str:
    """Set the data-count of the stat whose <div class="k"> reads `label`."""
    pattern = re.compile(
        r'(data-count=")(-?\d+)(")((?:(?!</div>).)*>(?:(?!</div>).)*</div>\s*'
        r'<div class="k">' + re.escape(label) + r"</div>)",
        re.S,
    )
    out, n = pattern.subn(lambda m: f"{m.group(1)}{value}{m.group(3)}{m.group(4)}", html, count=1)
    if n != 1:
        raise SystemExit(f"could not find the stat labelled {label!r} in forge.html")
    return out


def _sub(text: str, pattern: str, replacement: str, what: str) -> str:
    out, n = re.subn(pattern, replacement.replace("\\", "\\\\"), text, count=1, flags=re.S)
    if n != 1:
        raise SystemExit(f"could not rewrite {what} (pattern did not match once)")
    return out


def _row(html: str, label: str, value: str) -> str:
    """Rewrite the <b> of the table row whose <span> reads `label`.

    The rows are all `<span>LABEL</span><b>VALUE</b>`, so matching the label and
    replacing the whole row keeps the value out of re's replacement grammar --
    a figure containing a backslash or a group-looking token cannot leak into
    the page as literal text.
    """
    pattern = re.compile(r"<span>" + re.escape(label) + r"</span><b>[^<]*</b>")
    out, n = pattern.subn(lambda _m: f"<span>{label}</span><b>{value}</b>", html, count=1)
    if n != 1:
        raise SystemExit(f"could not find the row labelled {label!r} in forge.html")
    return out


def render(data: dict) -> dict[str, str]:
    """Return the new text of each landing file."""
    head = data["headline"]["combined"]
    charges = data["charges"]
    sizing = {row["lots"]: row for row in data["sizing"]}
    day = data["daily"]
    regime = data["regime"]["both"]
    dow_name, dow = min(data["by_dow"].items(), key=lambda kv: kv[1]["net"])
    before, after = regime["before"], regime["after"]

    net = head["net"]
    lost_months = day["months"] - day["green_months"]
    best = sum(row[1] for row in data["best10"])
    worst = sum(row[1] for row in data["worst10"])
    tail_pct = round((best + worst) / net * 100)
    eaten = {n: round(sizing[n]["charges"] / (sizing[n]["net"] + sizing[n]["charges"]) * 100) for n in (1, 4)}

    html = (_LANDING / "forge.html").read_text()
    for label, value in (
        (f"{dow_name}, net", int(round(dow["net"]))),
        ("Deepest drawdown", int(round(head["max_dd"]))),
        ("Longest losing run", head["streak_loss"]),
        ("Months that lost", lost_months),
        ("Net, after every charge", int(round(net))),
        ("Trades", head["trades"]),
        ("Profit factor", int(round(head["profit_factor"] * 100))),
        ("Return over drawdown", int(round(head["return_over_dd"] * 100))),
    ):
        html = _stat(html, label, value)

    html = _sub(html, r'data-suf=" of \d+"', f'data-suf=" of {day["months"]}"', "months-that-lost suffix")
    html = _sub(
        html,
        r"Across \d+ trades, the one weekday",
        f'Across {dow["n"]} trades, the one weekday',
        "worst-weekday trade count",
    )
    html = _sub(
        html,
        r"₹[\d,]+ gross less ₹[\d,]+ in charges\.",
        f"₹{inr(charges['gross'])} gross less ₹{inr(charges['total'])} in charges.",
        "gross/charges line",
    )
    html = _sub(html, r"[\d.]+% won\.", f"{head['win_rate']}% won.", "win rate")
    html = _sub(
        html,
        r"₹[\d.]+ (?:returned|won) for every ₹1\.00",
        f"₹{head['profit_factor']:.2f} won for every ₹1.00",
        "profit-factor line",
    )
    html = _sub(
        html, r"Five years\. \d+ trading days\.", f"Five years. {day['trading_days']} trading days.", "trading days"
    )
    html = _sub(
        html,
        r"(?:Flat ₹80|Recorded) brokerage is \d+% of all charges",
        f"Recorded brokerage is {round(charges['brokerage'] / charges['total'] * 100)}% of all charges",
        "brokerage share",
    )
    html = _row(html, "Turnover traded", f"₹{charges['turnover'] / 1e7:.2f} cr")
    html = _row(html, "Total charges", f"₹{inr(charges['total'])}")
    html = _row(html, "Charges / turnover", f"{charges['total'] / charges['turnover'] * 100:.3f}%")
    html = _row(html, "Per trade", f"₹{charges['per_trade']}")
    html = _row(html, "1 lot — starting comparison", f"₹{inr(sizing[1]['funded'])} · {sizing[1]['roi']}% total")
    html = _row(html, "4 lots — sizing comparison", f"₹{inr(sizing[4]['funded'])} · {sizing[4]['roi']}% total")
    html = _row(html, "Charges eaten, 1 lot", f"{eaten[1]}% of gross")
    html = _row(html, "Charges eaten, 4 lots", f"{eaten[4]}% of gross")
    html = _sub(html, r"<b>5 yrs · \d+ trades</b>", f"<b>5 yrs · {head['trades']} trades</b>", "evidence row")
    html = _sub(html, r"smaller than −₹[\d,]*\d", f"smaller than −₹{inr(abs(head['max_dd']))}", "drawdown prose")
    html = _sub(
        html,
        r"Regime split disclosed: \d+% of profit from \d+% of trades",
        f"Regime split disclosed: {round(after['net'] / net * 100)}% of profit from {round(after['n'] / head['trades'] * 100)}% of trades",
        "regime split",
    )
    html = _sub(
        html,
        r"best and worst ten days are \d+% of the result",
        f"best and worst ten days are {tail_pct}% of the result",
        "tail concentration",
    )
    html = _sub(
        html,
        r"\w+(?:-\w+)? were green\. The other [\w-]+ are in the ledger",
        f"{_CARDINAL.get(day['green_months'], str(day['green_months']))} were green. "
        f"The other {_WORDS.get(lost_months, str(lost_months))} are in the ledger",
        "green/lost months prose",
    )
    html = _sub(
        html,
        r"The first \d+ months earned ₹[\d,]+ across \d+ trades —\s*\n?\s*₹[\d,]+ a trade\. The last \d+ months earned ₹[\d,]+ across \d+ — ₹[\d,]+ a trade\.",
        f"The first 46 months earned ₹{inr(before['net'])} across {before['n']} trades —\n        "
        f"₹{inr(before['avg'])} a trade. The last 21 months earned ₹{inr(after['net'])} across {after['n']} — ₹{inr(after['avg'])} a trade.",
        "regime prose",
    )

    js = (_LANDING / "dojima.js").read_text()
    tape = [
        (f"₹{inr(net)}", "Net, 5 years", "up"),
        (f"{head['trades']}", "Trades", ""),
        (f"{head['win_rate']}%", "Win rate", ""),
        (f"{head['profit_factor']:.2f}", "Profit factor", ""),
        (f"−₹{inr(abs(head['max_dd']))}", "Max drawdown", "dn"),
        (f"{head['return_over_dd']:.2f}×", "Return / drawdown", ""),
        (f"₹{charges['turnover'] / 1e7:.2f} cr", "Turnover", ""),
        (f"₹{inr(charges['total'])}", "Charges paid", "dn"),
        (f"{day['green_months']} / {day['months']}", "Months green", ""),
        (f"{day['trading_days']}", "Trading days", ""),
        (head["last"], "Last recorded trade", ""),
    ]
    js = _sub(
        js,
        r"const TAPE=\[.*?\];",
        "const TAPE=[" + ",".join(json.dumps(list(row), ensure_ascii=False) for row in tape) + "];",
        "TAPE",
    )
    series = [[row[0], int(round(row[1])), int(round(row[2]))] for row in data["series"]]
    js = _sub(
        js, r"  const SER=\[.*?\];", "  const SER=" + json.dumps(series, separators=(",", ":")) + ";", "SER curve"
    )
    return {"forge.html": html, "dojima.js": js}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit non-zero if the landing is behind the book")
    ap.add_argument("--write", action="store_true", help="rewrite the landing files")
    args = ap.parse_args()
    if not (args.check or args.write):
        ap.error("pass --check or --write")

    data = json.loads(DATA.read_text())
    rendered = render(data)
    behind = [name for name, text in rendered.items() if (_LANDING / name).read_text() != text]

    if args.check:
        if behind:
            print("landing is BEHIND report_data.json: " + ", ".join(behind))
            print("run: python3 tools/tearsheet/update_landing.py --write")
            return 1
        print(f"landing matches report_data.json (net ₹{inr(data['headline']['combined']['net'])})")
        return 0

    for name, text in rendered.items():
        (_LANDING / name).write_text(text)
    print("landing rewritten" if behind else "landing already current")
    print(f"  {', '.join(behind) if behind else 'no change'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
