"""Zerodha Console tradebook -> the Zerodha month panel.

Phil, 2026-10-16: "Why old trades are not visible in zerodha like Aug 2021?"
Kite gives today's fills only; Console's tradebook (CSV or Excel) holds the
rest. Fills are matched first in, first out per contract and booked on the
day they close; a day PhilForge recorded itself is never overwritten.
"""

import io
import unittest
import zipfile
from xml.sax.saxutils import escape

import zerodha_tradebook as ztb

HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time,expiry_date"


def _csv(*rows: str) -> bytes:
    return ("\n".join((HEADER,) + rows) + "\n").encode()


ROWS = (
    # An intraday call: bought and sold the same day, two fills on the sell.
    "NIFTY21AUG16400CE,,2021-08-10,NFO,NFO-OPT,,buy,false,50,100.00,T1,O1,2021-08-10T09:20:00,2021-08-12",
    "NIFTY21AUG16400CE,,2021-08-10,NFO,NFO-OPT,,sell,false,25,110.00,T2,O2,2021-08-10T10:00:00,2021-08-12",
    "NIFTY21AUG16400CE,,2021-08-10,NFO,NFO-OPT,,sell,false,25,120.00,T3,O2,2021-08-10T10:00:01,2021-08-12",
    # Held overnight: bought on the 10th, sold on the 11th -- booked on the 11th.
    "NIFTY21AUG16500PE,,2021-08-10,NFO,NFO-OPT,,buy,false,50,80.00,T4,O3,2021-08-10T14:00:00,2021-08-12",
    "NIFTY21AUG16500PE,,2021-08-11,NFO,NFO-OPT,,sell,false,50,70.00,T5,O4,2021-08-11T09:30:00,2021-08-12",
    # A written put: sold first, bought back cheaper.
    "NIFTY21AUG16000PE,,2021-08-11,NFO,NFO-OPT,,sell,false,50,40.00,T6,O5,2021-08-11T11:00:00,2021-08-12",
    "NIFTY21AUG16000PE,,2021-08-11,NFO,NFO-OPT,,buy,false,50,15.00,T7,O6,2021-08-11T15:00:00,2021-08-12",
    # The same fill listed twice is read once.
    "NIFTY21AUG16000PE,,2021-08-11,NFO,NFO-OPT,,buy,false,50,15.00,T7,O6,2021-08-11T15:00:00,2021-08-12",
    # Bought and left to expire: no closing fill exists in a tradebook.
    "NIFTY21AUG16800CE,,2021-08-12,NFO,NFO-OPT,,buy,false,50,5.00,T8,O7,2021-08-12T09:16:00,2021-08-12",
)


class TheTradebookBecomesDays(unittest.TestCase):
    def setUp(self):
        self.days, self.summary = ztb.daily_from_rows(ztb.read_rows(_csv(*ROWS), "tradebook.csv"))

    def test_an_intraday_trade_is_booked_on_its_day(self):
        day = self.days["2021-08-10"]
        self.assertEqual(day["real_pnl"], 25 * 10 + 25 * 20)  # +750
        self.assertEqual(day["real_trade_legs"], 4)
        self.assertEqual(day["real_trades"], 3)  # orders O1, O2, O3

    def test_an_overnight_hold_books_on_the_day_it_closes(self):
        day = self.days["2021-08-11"]
        # PE: (70 - 80) x 50 = -500 ; written PE: (40 - 15) x 50 = +1,250
        self.assertEqual(day["real_pnl"], 750.0)
        self.assertEqual(day["real_trade_legs"], 3)  # the duplicate is not counted

    def test_imported_days_are_gross_and_say_where_they_came_from(self):
        day = self.days["2021-08-10"]
        self.assertEqual(day["real_net_pnl"], day["real_pnl"])
        self.assertFalse(day["charges_known"])
        self.assertEqual(day["source"], "console")
        self.assertEqual(day["broker"], "zerodha")

    def test_a_contract_left_to_expire_is_listed_not_guessed(self):
        self.assertEqual(
            self.summary["still_open"], [{"symbol": "NIFTY21AUG16800CE", "qty": 50.0, "expiry": "2021-08-12"}]
        )
        self.assertEqual(self.days["2021-08-12"]["real_pnl"], 0.0)

    def test_the_summary(self):
        self.assertEqual(
            (self.summary["first"], self.summary["last"], self.summary["fills"]), ("2021-08-10", "2021-08-12", 8)
        )
        self.assertEqual(self.summary["gross"], 1500.0)


def _xlsx(rows: list[list[str]]) -> bytes:
    """A minimal .xlsx with inline strings, as Excel writes one."""

    def col(i):
        return chr(65 + i)

    body = "".join(
        f'<row r="{r + 1}">'
        + "".join(f'<c r="{col(i)}{r + 1}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for i, v in enumerate(row))
        + "</row>"
        for r, row in enumerate(rows)
    )
    sheet = f'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{body}</sheetData></worksheet>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


class ConsoleExcel(unittest.TestCase):
    def test_the_header_below_the_account_lines_is_found(self):
        table = [["Client ID", "AB1234"], ["Tradebook for F&O"], [], HEADER.split(",")]
        table += [row.split(",") for row in ROWS[:3]]
        days, summary = ztb.daily_from_rows(ztb.read_rows(_xlsx(table), "tradebook.xlsx"))
        self.assertEqual(days["2021-08-10"]["real_pnl"], 750.0)
        self.assertEqual(summary["fills"], 3)

    def test_a_file_that_is_not_a_tradebook_says_so(self):
        with self.assertRaisesRegex(ztb.TradebookError, "No tradebook header"):
            ztb.read_rows(b"date,amount\n2021-01-01,5\n", "statement.csv")
        with self.assertRaisesRegex(ztb.TradebookError, "empty"):
            ztb.read_rows(b"", "x.csv")
        broken = io.BytesIO()
        with zipfile.ZipFile(broken, "w") as z:
            z.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData><row>&</row>")
        with self.assertRaisesRegex(ztb.TradebookError, "could not be read"):
            ztb.read_rows(broken.getvalue(), "broken.xlsx")


class MergingIntoTheLedger(unittest.TestCase):
    def test_a_day_philforge_recorded_is_kept_and_an_earlier_import_replaced(self):
        recorded = {"broker": "zerodha", "real_pnl": 3308.5, "charges_known": True}
        history = {"2021-08-11": recorded, "2021-08-10": {"source": "console", "real_pnl": 1.0}}
        days, _ = ztb.daily_from_rows(ztb.read_rows(_csv(*ROWS), "t.csv"))
        merged, written, kept = ztb.merge_into_history(history, days)
        self.assertIs(merged["2021-08-11"], recorded)
        self.assertEqual(merged["2021-08-10"]["real_pnl"], 750.0)
        self.assertEqual((written, kept), (2, 1))


if __name__ == "__main__":
    unittest.main()
