"""Money in and out of each broker account, for the Portfolio page.

Phil, 2026-10-30: "I want a withdrawn amount from each broker on the portfolio
page". Three kinds of movement, kept apart because they mean different things:

- ``withdrawal``: money Phil took out to his bank.
- ``deposit``: money he added.
- ``settlement``: the broker's own payout of idle funds to the bank (SEBI's
  running-account settlement). Dhan settled Rs 71,023 on 16-Oct-2026 and Phil
  put the same back on 19-Oct; counting it as a withdrawal would have doubled
  the month's money out.

Dhan answers from its ledger statement (GET /v2/ledger); its narrations, read
from the live account on 30-Oct-2026, are "Funds Withdrawal", "Funds
Deposited" and "Monthly Settlement". Kite keeps no fund history, so Zerodha's
comes from the funds statement (ledger) downloaded from Console.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime

KINDS = ("withdrawal", "deposit", "settlement")


class LedgerError(ValueError):
    """A file that is not a funds statement this module can read."""


def _money(value) -> float:
    text = str(value if value is not None else "").replace(",", "").replace("₹", "").strip()
    try:
        return round(float(text or 0), 2)
    except ValueError:
        return 0.0


def _parse_date(value) -> str:
    text = str(value or "").strip()
    # The whole text, then its date part alone ("2026-10-30 00:00:00").
    for candidate in (text, text[:10], text.split(" ")[0]):
        for fmt in ("%b %d, %Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    # An Excel serial day (Console's .xlsx keeps dates as numbers).
    try:
        serial = float(text)
        if 20000 < serial < 80000:
            return date.fromordinal(date(1899, 12, 30).toordinal() + int(serial)).isoformat()
    except ValueError:
        pass
    return ""


def _flow(broker: str, day: str, kind: str, amount: float, note: str, ref: str = "") -> dict:
    # A fingerprint to know a movement again, not a secret.
    key = (
        ref
        or hashlib.sha1(f"{broker}|{day}|{kind}|{amount:.2f}|{note}".encode(), usedforsecurity=False).hexdigest()[:16]
    )
    return {"broker": broker, "date": day, "kind": kind, "amount": round(amount, 2), "note": note[:120], "ref": key}


# ── Dhan ─────────────────────────────────────────────────────────────────
_DHAN_KIND = {
    "funds withdrawal": "withdrawal",
    "funds deposited": "deposit",
    "monthly settlement": "settlement",
    "quarterly settlement": "settlement",
}


def dhan_flows(rows: list[dict]) -> list[dict]:
    """The fund movements in Dhan ledger rows; trades and charges are not."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        kind = _DHAN_KIND.get(str(row.get("narration") or "").strip().lower())
        if not kind:
            continue
        amount = _money(row.get("credit")) if kind == "deposit" else _money(row.get("debit"))
        day = _parse_date(row.get("voucherdate"))
        if amount <= 0 or not day:
            continue
        ref = f"dhan-{row.get('vouchernumber')}" if row.get("vouchernumber") else ""
        out.append(_flow("dhan", day, kind, amount, str(row.get("voucherdesc") or row.get("narration") or ""), ref))
    return out


# ── Zerodha (Console funds statement) ────────────────────────────────────
def _norm(cell) -> str:
    return re.sub(r"[^a-z]", "", str(cell or "").lower())


def zerodha_flows(table: list[list[str]]) -> list[dict]:
    """Fund movements from Console's funds statement (Funds -> Statement).

    Its rows carry particulars, a posting date, a voucher type, debit and
    credit. Money in is a Bank Receipt, money out a Bank Payment; a payout the
    broker made for the quarterly settlement says so in its particulars.
    """
    header_at, cols = -1, {}
    for i, row in enumerate(table[:40]):
        names = [_norm(c) for c in row]
        if "particulars" in names and "debit" in names and "credit" in names:
            header_at = i
            cols = {name: j for j, name in enumerate(names)}
            break
    if header_at < 0:
        raise LedgerError(
            "That is not a Zerodha funds statement: no Particulars, Debit and Credit columns. "
            "Download it from Console -> Funds -> Statement."
        )
    date_col = next((cols[c] for c in ("postingdate", "date", "voucherdate", "transactiondate") if c in cols), None)
    if date_col is None:
        raise LedgerError("The funds statement has no date column.")
    vtype_col = cols.get("vouchertype")
    pcol, dcol, ccol = cols["particulars"], cols["debit"], cols["credit"]

    out = []
    for row in table[header_at + 1 :]:
        cell = lambda j: row[j] if j is not None and j < len(row) else ""  # noqa: E731
        note = str(cell(pcol) or "").strip()
        text = note.lower()
        vtype = str(cell(vtype_col) or "").strip().lower()
        debit, credit = _money(cell(dcol)), _money(cell(ccol))
        day = _parse_date(cell(date_col))
        if not day or "opening balance" in text or "closing balance" in text:
            continue
        # The running-account payout names its period; "Net settlement for
        # Equity" is a trade's settlement, not money to the bank.
        if debit > 0 and re.search(r"(quarterly|monthly) settlement|running account", text):
            kind, amount = "settlement", debit
        elif vtype == "bank receipts" or (credit > 0 and re.search(r"funds? added|pay[- ]?in|deposit", text)):
            kind, amount = "deposit", credit
        elif vtype == "bank payments" or (
            debit > 0 and re.search(r"payout|withdraw|transferred to (your )?bank", text)
        ):
            kind, amount = "withdrawal", debit
        else:
            continue
        if amount > 0:
            out.append(_flow("zerodha", day, kind, amount, note))
    return out


def statement_span(table: list[list[str]]) -> tuple[str, str]:
    """The first and last day a funds statement covers, from every dated row."""
    days = sorted(d for d in (_parse_date(c) for row in table for c in row[:3] if c) if d)
    return (days[0], days[-1]) if days else ("", "")


def kite_day_flows(day: str, payout: float, payin: float) -> list[dict]:
    """Today's movements as Kite's margins show them: a withdrawal asked for
    (utilised.payout) and money added (available.intraday_payin)."""
    out = []
    for kind, amount in (("withdrawal", payout), ("deposit", payin)):
        if amount and amount > 0:
            out.append(
                {
                    **_flow("zerodha", day, kind, amount, "Recorded from Kite", f"zerodha-kite-{day}-{kind}"),
                    "source": "kite",
                }
            )
    return out


def with_kite_day(flows: list[dict], day: str, today: list[dict]) -> list[dict]:
    """The day's Kite entries replaced by today's figures; the rest kept."""
    kept = [f for f in flows or [] if not (f.get("source") == "kite" and f.get("date") == day)]
    rows = kept + today
    rows.sort(key=lambda f: (str(f.get("date")), str(f.get("ref"))), reverse=True)
    return rows


def without_kite_between(flows: list[dict], first: str, last: str) -> list[dict]:
    """Kite's day-by-day entries give way to a statement covering their days."""
    if not first:
        return list(flows or [])
    return [f for f in flows or [] if not (f.get("source") == "kite" and first <= str(f.get("date")) <= last)]


def read_table(data: bytes, filename: str = "") -> list[list[str]]:
    """A CSV or Excel (.xlsx) file as rows of cells."""
    import zerodha_tradebook as _ztb

    if not data:
        raise LedgerError("The file is empty.")
    if data[:2] == b"PK" or str(filename).lower().endswith(".xlsx"):
        try:
            return _ztb._xlsx_rows(data)
        except _ztb.TradebookError as exc:
            raise LedgerError(str(exc)) from exc
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) > 50000:
        raise LedgerError("The file has too many rows.")
    return rows


def merge(existing: list[dict], new: list[dict]) -> tuple[list[dict], int]:
    """Add movements not already held (by their ref); newest first."""
    held = {str(f.get("ref")) for f in existing or []}
    added = [f for f in new if str(f.get("ref")) not in held]
    rows = list(existing or []) + added
    rows.sort(key=lambda f: (str(f.get("date")), str(f.get("ref"))), reverse=True)
    return rows, len(added)
