"""Zerodha Console tradebook -> PhilForge's day-by-day Zerodha ledger.

Phil, 2026-10-16: "Why old trades are not visible in zerodha like Aug 2021?"
Kite's API answers for the current day only, so the Zerodha month panel holds
only the days PhilForge has recorded itself (from 2026-10-11). Older history
lives in Zerodha Console, which has no API: Reports -> Tradebook downloads it
as CSV or Excel, one row per fill. This turns that file into ledger days.

Fills are matched first in, first out per contract, so a position carried
overnight books its result on the day it was CLOSED; selling first (a written
option) opens a short the later buy closes. The tradebook carries no charges,
so every imported day is gross and says so (`charges_known: False`).

What a tradebook cannot show is a contract that expired without a closing
trade -- Zerodha settles it, there is no fill -- so those stay open and are
listed back to the importer rather than guessed at.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from xml.etree import (
    ElementTree as ET,  # nosec B405 - owner-only upload, 10 MB cap; expat >= 2.4.1 refuses entity bombs
)

MAX_ROWS = 200_000
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class TradebookError(ValueError):
    """The file is not a tradebook this can read; the message says why."""


def _norm(name) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")


def _xlsx_rows(data: bytes) -> list[list[str]]:
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise TradebookError("That is not a CSV or an Excel (.xlsx) file.") from exc
    names = book.namelist()
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        root = _parse_xml(book.read("xl/sharedStrings.xml"))
        for si in root.iter(_NS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(_NS + "t")))
    sheets = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
    if not sheets:
        raise TradebookError("The Excel file has no sheet.")
    root = _parse_xml(book.read(sheets[0]))
    rows: list[list[str]] = []
    for row in root.iter(_NS + "row"):
        cells: dict[int, str] = {}
        for cell in row.iter(_NS + "c"):
            ref = re.match(r"([A-Z]+)", cell.get("r") or "")
            col = 0
            for ch in ref.group(1) if ref else "A":
                col = col * 26 + (ord(ch) - 64)
            kind, value = cell.get("t"), cell.find(_NS + "v")
            if kind == "s" and value is not None:
                text = shared[int(value.text)]
            elif kind == "inlineStr":
                text = "".join(t.text or "" for t in cell.iter(_NS + "t"))
            else:
                text = value.text if value is not None and value.text is not None else ""
            cells[col - 1] = text
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        if len(rows) > MAX_ROWS:
            raise TradebookError("The file has too many rows.")
    return rows


def _parse_xml(blob: bytes):
    try:
        return ET.fromstring(blob)  # nosec B314 - see the import
    except ET.ParseError as exc:
        raise TradebookError("The Excel file could not be read; download the tradebook again, or as CSV.") from exc


def read_rows(data: bytes, filename: str = "") -> list[dict]:
    """The tradebook's rows as dicts with normalised column names."""
    if not data:
        raise TradebookError("The file is empty.")
    is_xlsx = data[:2] == b"PK" or str(filename).lower().endswith(".xlsx")
    if is_xlsx:
        table = _xlsx_rows(data)
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        table = [row for row in csv.reader(io.StringIO(text))]
        if len(table) > MAX_ROWS:
            raise TradebookError("The file has too many rows.")
    # Console's Excel opens with a few lines about the account before the
    # header; the header is the first row naming a trade date and a side.
    for start, row in enumerate(table):
        names = [_norm(c) for c in row]
        if "trade_date" in names and "trade_type" in names:
            break
    else:
        raise TradebookError(
            "No tradebook header found (it needs Trade Date and Trade Type columns). Download it from Console -> Reports -> Tradebook."
        )
    out = []
    for row in table[start + 1 :]:
        if not any(str(c).strip() for c in row):
            continue
        out.append({names[i]: (row[i] if i < len(row) else "") for i in range(len(names)) if names[i]})
    return out


def _day(value) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):  # an Excel serial date
        return date(1899, 12, 30) + timedelta(days=int(float(text)))
    for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(text[:11].strip(), pattern).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "")).date()
    except ValueError:
        return None


def _num(value) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def daily_from_rows(rows: list[dict]) -> tuple[dict, dict]:
    """(ledger days keyed YYYY-MM-DD, a summary of what was read)."""
    fills, seen, skipped = [], set(), 0
    for row in rows:
        day = _day(row.get("trade_date"))
        side = str(row.get("trade_type") or "").strip().lower()
        qty, price = abs(_num(row.get("quantity"))), _num(row.get("price"))
        symbol = str(row.get("symbol") or row.get("tradingsymbol") or "").strip().upper()
        if day is None or side not in ("buy", "sell") or qty <= 0 or not symbol:
            skipped += 1
            continue
        trade_id = str(row.get("trade_id") or "").strip()
        exchange = str(row.get("exchange") or "").strip().upper()
        if trade_id and (exchange, trade_id) in seen:  # the same fill listed twice
            continue
        seen.add((exchange, trade_id))
        stamp = str(row.get("order_execution_time") or row.get("trade_time") or "").strip()
        fills.append(
            {
                "day": day,
                "stamp": stamp or day.isoformat(),
                "trade_id": trade_id,
                "order_id": str(row.get("order_id") or "").strip(),
                "key": f"{exchange}:{symbol}",
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "expiry": _day(row.get("expiry_date")),
            }
        )
    if not fills:
        raise TradebookError("The file has no fills this can read.")
    fills.sort(key=lambda f: (f["day"], f["stamp"], f["trade_id"]))

    longs: dict[str, deque] = defaultdict(deque)
    shorts: dict[str, deque] = defaultdict(deque)
    pnl: dict[str, float] = defaultdict(float)
    legs: dict[str, int] = defaultdict(int)
    orders: dict[str, set] = defaultdict(set)
    expiry: dict[str, date | None] = {}
    for f in fills:
        key, qty, price, day = f["key"], f["qty"], f["price"], f["day"].isoformat()
        expiry[key] = f["expiry"] or expiry.get(key)
        legs[day] += 1
        orders[day].add(f["order_id"] or f["trade_id"] or f["stamp"])
        closing, opening = (shorts[key], longs[key]) if f["side"] == "buy" else (longs[key], shorts[key])
        while qty > 1e-9 and closing:
            lot = closing[0]
            take = min(qty, lot[0])
            # A buy closing a short gains what it sold above; a sell closing a
            # long gains what it sells above what it paid.
            pnl[day] += (lot[1] - price) * take if f["side"] == "buy" else (price - lot[1]) * take
            lot[0] -= take
            qty -= take
            if lot[0] <= 1e-9:
                closing.popleft()
        if qty > 1e-9:
            opening.append([qty, price])

    days = {}
    for day in sorted(legs):
        days[day] = {
            "broker": "zerodha",
            "real_pnl": round(pnl[day], 2),
            "real_net_pnl": round(pnl[day], 2),
            "real_charges": 0.0,
            "real_brokerage": 0.0,
            "real_total_costs": 0.0,
            "real_trades": len(orders[day]),
            "real_order_count": len(orders[day]),
            "real_trade_legs": legs[day],
            "charges_known": False,
            "source": "console",
        }
    still_open = []
    for key in sorted(set(longs) | set(shorts)):
        qty = sum(lot[0] for lot in longs[key]) - sum(lot[0] for lot in shorts[key])
        if abs(qty) > 1e-9:
            exp = expiry.get(key)
            still_open.append(
                {"symbol": key.split(":", 1)[1], "qty": round(qty, 2), "expiry": exp.isoformat() if exp else None}
            )
    summary = {
        "fills": len(fills),
        "skipped_rows": skipped,
        "first": fills[0]["day"].isoformat(),
        "last": fills[-1]["day"].isoformat(),
        "days": len(days),
        "gross": round(sum(pnl.values()), 2),
        "still_open": still_open,
    }
    return days, summary


def merge_into_history(history: dict, days: dict) -> tuple[dict, int, int]:
    """Imported days join the ledger; a day PhilForge recorded itself is kept.

    Returns (history, days written, days kept as recorded). A day an earlier
    import wrote is replaced, so importing the same file twice is harmless.
    """
    merged, written, kept = dict(history or {}), 0, 0
    for day, entry in days.items():
        current = merged.get(day)
        if current and current.get("source") != "console":
            kept += 1
            continue
        merged[day] = entry
        written += 1
    return merged, written, kept
