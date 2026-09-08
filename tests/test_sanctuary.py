"""Sanctuary: the EMI document parser and the ledger's monthly automation.

The parser tests mirror the three shapes banks actually produce: a dated
CSV with Indian formatting, an HDFC-style table whose date column is an
installment number, and a Kotak-style PDF whose rows collapse into single
text lines. The automation tests prove recurring items post exactly once
per elapsed month and that settling a backdated schedule clears its alerts.
"""

import asyncio
import io
import os
import tempfile
import unittest
import zipfile
from datetime import date

os.environ.setdefault("PHILFORGE_DB", os.path.join(tempfile.gettempdir(), "sanctuary-test.db"))

from sanctuary_emi import _add_months, parse_emi_document  # noqa: E402

CSV_BLOB = """HDFC Bank Personal Loan
Sr No,Due Date,EMI Amount,Principal Component,Interest Component,Outstanding Principal
1,05/09/2026,"12,345","8,100.50","4,244.50","4,91,899.50"
2,05-Oct-2026,12345,8182,4163,483717.5
bad row,,,,,
""".encode()

PERIOD_CSV = """EMI Schedule
Period,Outstanding,Principal (P),Interest (I),EMI Amount (P + I)
1,212000,0.00,2614.00,6228.50
2,212000.00,3047.50,3180.50,6228.50
3,208952.50,3093.21,3134.79,6228.50
""".encode()

LINE_ROWS = [
    ["Repayment Schedule for Period: 02 Jun 2022 to 02 May 2026"],
    ["Date Type Rate Total Amount Principal Interest Closing Balance"],
    ["02 Jun 2022 Installment 17.0 6,576 3,851.49 2,724.51 2,24,063.51"],
    ["02 Jul 2022 Installment 17.0 6,576 3,378.78 3,197.22 2,20,684.73"],
]


class TestEmiParser(unittest.TestCase):
    def test_dated_csv_with_indian_formats(self):
        result = parse_emi_document("sched.csv", CSV_BLOB)
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0]["due_date"], "2026-09-05")
        self.assertEqual(result["rows"][0]["amount"], 12345.0)
        self.assertEqual(result["rows"][0]["outstanding"], 491899.5)
        self.assertEqual(result["rows"][1]["due_date"], "2026-10-05")

    def test_period_numbered_sheet_needs_anchor_then_anchors(self):
        blind = parse_emi_document("sched.csv", PERIOD_CSV)
        self.assertTrue(blind["needs_first_due"])
        self.assertEqual(blind["rows"], [])
        anchored = parse_emi_document("sched.csv", PERIOD_CSV, first_due=date(2025, 9, 5))
        self.assertEqual(len(anchored["rows"]), 3)
        self.assertEqual(anchored["rows"][0]["due_date"], "2025-09-05")
        self.assertEqual(anchored["rows"][2]["due_date"], "2025-11-05")
        self.assertEqual(anchored["rows"][2]["amount"], 6228.5)

    def test_single_cell_lines_self_validate(self):
        from sanctuary_emi import _parse_lines_fallback

        result = _parse_lines_fallback(LINE_ROWS, [])
        self.assertEqual(len(result["rows"]), 2)
        first = result["rows"][0]
        self.assertEqual(first["due_date"], "2022-06-02")
        self.assertEqual(first["amount"], 6576.0)
        self.assertEqual(first["principal_part"], 3851.49)
        self.assertEqual(first["outstanding"], 224063.51)

    def test_xlsx_shared_strings_and_serial_dates(self):
        shared = (
            '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><si><t>Due Date</t></si><si><t>EMI</t></si></sst>'
        )
        sheet = (
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2"><v>46300</v></c><c r="B2"><v>25000</v></c></row>'
            "</sheetData></worksheet>"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("xl/sharedStrings.xml", shared)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        result = parse_emi_document("loan.xlsx", buf.getvalue())
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["due_date"], "2026-10-05")

    def test_counter_column_is_never_read_as_money(self):
        """An HDFC key-fact sheet numbers its rows AND dates them."""
        kfs = (
            "Instalment No.,Due Date,Instalment Amt (Rs),Principal (Rs),Interest (Rs),Outstanding Principal (Rs)\n"
            "1,06/09/2025,42677,30881,11796,2382701\n"
            "2,06/10/2025,51698,31147,20551,2351554\n"
        ).encode()
        result = parse_emi_document("kfs.csv", kfs)
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["rows"][0]["amount"], 42677.0)  # not 1
        self.assertEqual(result["rows"][1]["amount"], 51698.0)  # not 2
        self.assertEqual(result["rows"][0]["outstanding"], 2382701.0)

    def test_a_stray_two_cell_row_cannot_pose_as_the_header(self):
        """ "Installment Frequency | Monthly" once captured the whole parse."""
        sheet = (
            "Installment Frequency,Monthly\n"
            "Sanction Date,19/08/2025\n"
            "Instalment No.,Due Date,Instalment Amt,Principal,Interest,Outstanding\n"
            "1,06/09/2025,42677,30881,11796,2382701\n"
            "2,06/10/2025,51698,31147,20551,2351554\n"
        ).encode()
        result = parse_emi_document("kfs.csv", sheet)
        self.assertEqual([row["amount"] for row in result["rows"]], [42677.0, 51698.0])

    def test_a_bank_statement_is_not_read_as_a_schedule(self):
        """Every line of an account statement is dated too, and the number
        after the date is a value date's day or a reference — read as money
        it becomes instalments of ten rupees, and committing that would
        replace a real schedule and reset the EMI to ten."""
        from sanctuary_emi import _parse_lines_fallback

        statement = [
            ["Txn Date Value Date Txn Reference Narration Component Debit Credit Running Balance"],
            ["11 April 2025 10 Apr 2025 634936 Repayment Received via Nach Principal 0.00 9,999.00 1,270,088.00"],
            ["30 April 2025 30 Apr 2025 Interest Debited for the month of April 2025 Interest 4,321.00 0.00"],
            ["12 May 2025 10 May 2025 717085 Repayment Received via Nach Principal 0.00 9,999.00 1,255,076.00"],
            ["31 May 2025 31 May 2025 Interest Debited for the month of May 2025 Interest 4,456.00 0.00"],
        ]
        result = _parse_lines_fallback(statement, [])
        self.assertEqual(result["rows"], [])
        self.assertTrue(result["warnings"])

    def test_a_quarterly_summary_line_is_not_one_giant_instalment(self):
        from sanctuary_emi import _parse_lines_fallback

        result = _parse_lines_fallback([["Sanction Date 11-09-2012 Principal Repaid Rs. 1,111,111.00"]], [])
        self.assertEqual(result["rows"], [], "one line is a summary, never a schedule")

    def test_two_lines_a_month_is_a_statement_not_a_schedule(self):
        """A year-end certificate prints the EMI and then the month's
        interest debit. Taken as instalments, half of them are not."""
        from sanctuary_emi import _parse_lines_fallback

        paid = []
        for month in (4, 5, 6):
            paid.append([f"12-{month:02d}-2019 EMI Received 9,999"])
            paid.append([f"30-{month:02d}-2019 Interest Debited 4,321"])
        result = _parse_lines_fallback(paid, [])
        self.assertEqual(result["rows"], [])
        self.assertTrue(any("statement" in w for w in result["warnings"]), result["warnings"])

    def test_a_plain_monthly_list_still_reads(self):
        """The tightening must not cost an honest sheet that simply has no
        principal and interest columns to check itself against."""
        from sanctuary_emi import _parse_lines_fallback

        result = _parse_lines_fallback([[f"05-{m:02d}-2025 9,999"] for m in (9, 10, 11)], [])
        self.assertEqual([row["amount"] for row in result["rows"]], [9999.0, 9999.0, 9999.0])

    def test_a_line_whose_date_follows_a_counter_still_reads(self):
        from sanctuary_emi import _parse_lines_fallback

        result = _parse_lines_fallback([["21 06/05/2027 51698 36668 15030 1705975"]], [])
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["due_date"], "2027-05-06")
        self.assertEqual(row["amount"], 51698.0)
        self.assertEqual(row["principal_part"], 36668.0)

    def test_add_months_clamps_month_ends(self):
        self.assertEqual(_add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(_add_months(date(2024, 1, 31), 1), date(2024, 2, 29))
        self.assertEqual(_add_months(date(2026, 8, 5), -6), date(2026, 2, 5))


class TestLedgerAutomation(unittest.TestCase):
    """Recurring materialization and past-EMI settlement on a scratch DB."""

    def setUp(self):
        self._db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(self._db_fd)
        os.environ["PHILFORGE_DB"] = self._db_path
        import importlib

        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        import sanctuary_db

        sanctuary_db.config = config
        self.sanctuary_db = sanctuary_db

    def tearDown(self):
        os.unlink(self._db_path)

    def test_recurring_posts_once_per_elapsed_month(self):
        import sanctuary

        async def run():
            await self.sanctuary_db.set_json_state(
                1,
                "recurring",
                [
                    {
                        "id": "r1",
                        "name": "NPS",
                        "amount": 5000,
                        "day": 1,
                        "category": "NPS",
                        "start_month": "2026-06",
                        "active": True,
                    }
                ],
            )
            await sanctuary._materialize_recurring(1)
            await sanctuary._materialize_recurring(1)  # idempotent
            months = await self.sanctuary_db.ledger_month_trend(1, ["2026-06", "2026-07", "2026-08"])
            return months

        months = asyncio.run(run())
        self.assertEqual(months.get("2026-06"), 5000.0)
        self.assertEqual(months.get("2026-07"), 5000.0)
        # current month present because today (day >= 1) has passed the post day
        self.assertEqual(months.get("2026-08"), 5000.0)

    def test_the_standing_panel_counts_the_current_account_not_the_overdraft(self):
        """The overdraft's limit is money the bank would lend, not money he has.

        His banking app prints one "balance" that folds the sweep account's
        three lakh thirty in, and this panel repeated it — telling him he owned
        four and a half lakh when the overdraft's headroom was most of it.
        """
        import sanctuary

        async def run():
            await self.sanctuary_db.add_ledger_many(
                1,
                [
                    {
                        "entry_date": "2026-09-03",
                        "category": "Groceries",
                        "amount": 100.0,
                        "note": "shop",
                        "source": "statement",
                        "ref_id": "r-bank-1",
                        "balance": 117234.54,
                    }
                ],
            )
            await self.sanctuary_db.set_json_state(
                1, sanctuary.OD_HELD_STATE, {"amount": 330000.0, "on": "2026-09-03", "limit": 330000.0}
            )
            return await sanctuary.finance_standing(user={"id": 1})

        st = asyncio.run(run())
        self.assertEqual(st["in_bank"], 117234.54)
        self.assertEqual(st["od_headroom"], 330000.0)
        # And the headroom must not have crept into the net by another door.
        self.assertEqual(st["net"], round(st["held"] + 117234.54 - st["total_debt"], 2))

    def test_settle_past_marks_only_past_unpaid(self):
        async def run():
            loan_id = await self.sanctuary_db.create_loan(1, {"name": "Test Loan"})
            rows = [
                {"due_date": "2026-07-05", "amount": 1000},
                {"due_date": "2026-08-05", "amount": 1000},
                {"due_date": "2027-01-05", "amount": 1000},
            ]
            await self.sanctuary_db.replace_schedule(1, loan_id, rows)
            settled = await self.sanctuary_db.settle_past_emis(1, loan_id, "2026-08-25")
            unpaid = await self.sanctuary_db.unpaid_emis_through(1, "2027-12-31")
            return settled, unpaid

        settled, unpaid = asyncio.run(run())
        self.assertEqual(settled, 2)
        self.assertEqual(len(unpaid), 1)
        self.assertEqual(unpaid[0]["due_date"], "2027-01-05")

    def test_a_revolving_loan_keeps_its_drawn_amount(self):
        async def run():
            loan_id = await self.sanctuary_db.create_loan(
                1, {"name": "ICICI OD", "emi_amount": 0, "drawn_amount": 318572.84}
            )
            await self.sanctuary_db.update_loan(1, loan_id, {"drawn_amount": 300000.0})
            loans = await self.sanctuary_db.list_loans(1)
            return next(loan for loan in loans if loan["id"] == loan_id)

        loan = asyncio.run(run())
        self.assertEqual(loan["emi_amount"], 0)
        self.assertEqual(loan["drawn_amount"], 300000.0)

    def test_replace_schedule_preserves_paid_marks(self):
        async def run():
            loan_id = await self.sanctuary_db.create_loan(1, {"name": "Test Loan"})
            rows = [{"due_date": "2026-07-05", "amount": 1000}]
            await self.sanctuary_db.replace_schedule(1, loan_id, rows)
            emis = await self.sanctuary_db.emis_for_loan(1, loan_id)
            await self.sanctuary_db.set_emi_paid(1, emis[0]["id"], "2026-07-05")
            await self.sanctuary_db.replace_schedule(
                1, loan_id, [{"due_date": "2026-07-05", "amount": 1000}, {"due_date": "2026-08-05", "amount": 1000}]
            )
            return await self.sanctuary_db.emis_for_loan(1, loan_id)

        emis = asyncio.run(run())
        self.assertEqual(emis[0]["paid_on"], "2026-07-05")
        self.assertEqual(emis[1]["paid_on"], "")


class StatementParserTests(unittest.TestCase):
    """The statement importer: an ICICI-shaped table, the dedupe key, and the
    rule that deposits and self-transfers never count as spending."""

    CSV = (
        "DETAILED STATEMENT\n"
        "Account Number,000099887766 ( INR ) - USER\n"
        "S No.,Value Date,Transaction Date,Cheque Number,Transaction Remarks,"
        "Withdrawal Amount(INR),Deposit Amount(INR),Balance(INR)\n"
        "1,01/01/2021,01/01/2021,,MPS/APOLLO PHAR/2021,1001.00,0.00,69010.49\n"
        "2,01/01/2021,01/01/2021,,UPI/1001/salary in,0.00,50000.00,119010.49\n"
        "3,02/01/2021,02/01/2021,,UPI/1002/Sweep to OD ac,5000.00,0.00,114010.49\n"
        "4,02/01/2021,02/01/2021,,UPI/1003/You are paying tea,100.00,0.00,113910.49\n"
        "5,02/01/2021,02/01/2021,,UPI/1004/You are paying tea,100.00,0.00,113810.49\n"
    ).encode()

    def parse(self):
        from sanctuary_statements import parse_statement

        return parse_statement("2021.csv", self.CSV)

    def test_both_directions_post_and_carry_their_direction(self):
        result = self.parse()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["rows"]), 5)
        by_dir = {"in": 0, "out": 0}
        for row in result["rows"]:
            by_dir[row["dir"]] += 1
        self.assertEqual(by_dir, {"in": 1, "out": 4})
        self.assertEqual(result["deposits_count"], 1)
        self.assertAlmostEqual(result["deposits_total"], 50000.00)
        self.assertEqual(result["account_tail"], "887766")

    def test_same_day_same_amount_rows_get_distinct_refs(self):
        rows = self.parse()["rows"]
        refs = {r["ref_id"] for r in rows}
        self.assertEqual(len(refs), 5, "the serial+balance must split the twin tea payments")

    def test_reparsing_yields_identical_refs(self):
        first = {r["ref_id"] for r in self.parse()["rows"]}
        second = {r["ref_id"] for r in self.parse()["rows"]}
        self.assertEqual(first, second)

    def test_both_halves_of_a_sweep_belong_to_the_overdraft(self):
        """The OD is a loan account, not a place money is parked.

        Both directions move that debt's balance, so both carry its name.
        Filing them as "Self transfer" said one of the two accounts was his
        savings, when what it really is, is what he owes.
        """
        from sanctuary_statements import categorise, od_account, parse_statement

        self.assertEqual(categorise("000012345678: Rev Sweep From"), "OD loan")
        self.assertEqual(categorise("Sweep from OD Ac"), "OD loan")
        self.assertEqual(categorise("Sweep to OD Ac"), "OD loan")
        self.assertEqual(od_account("035005008452: Rev Sweep From"), "035005008452")
        self.assertEqual(od_account("Sweep to OD Ac"), "", "only the reverse sweep names the account")
        blob = (
            "Account Number,000099887766 ( INR )\n"
            "S No.,Value Date,Transaction Date,Cheque Number,Transaction Remarks,"
            "Withdrawal Amount(INR),Deposit Amount(INR),Balance(INR)\n"
            "1,05/01/2021,05/01/2021,,000012345678: Rev Sweep From,0.00,5000.00,10000.00\n"
            "2,06/01/2021,06/01/2021,,Sweep to OD Ac,2500.00,0.00,7500.00\n"
        ).encode()
        result = parse_statement("x.csv", blob)
        self.assertEqual(
            result["linked_accounts"],
            [{"number": "000012345678", "kind": "Sweep-linked overdraft (OD)"}],
        )

    def test_an_inf_transfer_names_its_linked_account(self):
        from sanctuary_statements import parse_statement

        blob = (
            "Account Number,000099887766 ( INR )\n"
            "S No.,Value Date,Transaction Date,Cheque Number,Transaction Remarks,"
            "Withdrawal Amount(INR),Deposit Amount(INR),Balance(INR)\n"
            "1,05/01/2021,05/01/2021,,INF/308164322648/ 000055667788/A NAME,0.00,900.00,1000.00\n"
        ).encode()
        result = parse_statement("x.csv", blob)
        self.assertEqual(
            result["linked_accounts"],
            [{"number": "000055667788", "kind": "Linked account (internal transfer)"}],
        )

    def test_the_drcr_dialect_reads_direction_from_the_flag(self):
        from sanctuary_statements import parse_statement

        # Kotak's shape: one Amount column, direction in a Dr / Cr flag,
        # a second flag after Balance, and a timestamp on the date.
        blob = (
            '"","","Account Statement"\n'
            '"X","","","","Account No.","000012345678"\n'
            '"Sl. No.","Transaction Date","Value Date","Description","Chq /Ref No.",'
            '"Amount","Dr / Cr","Balance","Dr / Cr"\n'
            '"1","02-01-2023 02:58:07","02-01-2023","Ins Debit SPLN 1","CLIN-1","2,018.15","DR","0.00","CR"\n'
            '"2","02-01-2023 10:40:30","02-01-2023","UPI/incoming","UPI-2","14,000.00","CR","14,000.00","CR"\n'
        ).encode()
        result = parse_statement("kotak.csv", blob)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["account_tail"], "345678")
        dirs = {r["dir"]: r["amount"] for r in result["rows"]}
        self.assertEqual(dirs, {"out": 2018.15, "in": 14000.00})
        self.assertEqual(result["rows"][0]["entry_date"], "2023-01-02")

    def test_a_reexport_with_new_serials_keeps_the_same_refs(self):
        from sanctuary_statements import parse_statement

        # The same Dec-30 deposit exported twice: the year file numbers it
        # 462, a top-up file numbers it 1. One transaction, one ref.
        row = ",30/12/2020,30/12/2020,,UPI/1/refill,0.00,491.00,547.43\n"
        header = (
            "Account Number,000099887766 ( INR )\n"
            "S No.,Value Date,Transaction Date,Cheque Number,Transaction Remarks,"
            "Withdrawal Amount(INR),Deposit Amount(INR),Balance(INR)\n"
        )
        year_file = (header + "462" + row).encode()
        topup_file = (header + "1" + row).encode()
        ref_a = parse_statement("year.csv", year_file)["rows"][0]["ref_id"]
        ref_b = parse_statement("topup.csv", topup_file)["rows"][0]["ref_id"]
        self.assertEqual(ref_a, ref_b)

    def test_default_rules_reach_the_obvious_payees(self):
        from sanctuary_statements import categorise

        self.assertEqual(categorise("MPS/APOLLO PHAR/2021"), "Health")
        self.assertEqual(categorise("UPI/1002/Sweep to OD ac"), "OD loan")
        self.assertEqual(categorise("UPI/419/payment on CRED/cred.club@axisb"), "HDFC Creditcard")
        self.assertEqual(categorise("UPI/9/completely new shop"), "Uncategorised")

    def test_user_rule_wins_over_the_default(self):
        from sanctuary_statements import categorise

        rules = [{"match": "apollo", "category": "Pharmacy run"}]
        self.assertEqual(categorise("MPS/APOLLO PHAR/2021", rules), "Pharmacy run")

    def test_payee_key_prefers_the_upi_handle(self):
        from sanctuary_statements import payee_key

        # The bank half churns (okax, oki, okic are one person) — the key
        # is the name half, marked as a handle by its trailing @.
        self.assertEqual(payee_key("UPI/1001/Jan/emman.joy@okici/IDFC FIRST Bank/"), "emman.joy@")
        self.assertEqual(payee_key("UPI/9/x/someone@okax/A/"), payee_key("UPI/8/y/someone@oki/B/"))
        self.assertEqual(payee_key("BIL/ONL/000123456789/Indian Oil/998877/Cylinder"), "indian oil")

    def test_bulk_insert_skips_what_is_already_posted(self):
        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        os.environ["PHILFORGE_DB"] = db_path
        self.addCleanup(os.unlink, db_path)
        import importlib

        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        import sanctuary_db

        sanctuary_db.config = config

        async def run():
            rows = [
                {"entry_date": "2021-01-01", "amount": 10.0, "note": "a", "ref_id": "stmt:x:1"},
                {"entry_date": "2021-01-02", "amount": 20.0, "note": "b", "ref_id": "stmt:x:2"},
            ]
            first = await sanctuary_db.add_ledger_many(1, rows)
            second = await sanctuary_db.add_ledger_many(
                1,
                rows
                + [
                    {"entry_date": "2021-01-03", "amount": 30.0, "note": "c", "ref_id": "stmt:x:3"},
                ],
            )
            return first, second

        first, second = asyncio.run(run())
        self.assertEqual(first, 2)
        self.assertEqual(second, 1, "the re-upload must add only the new row")


class LoanDiscoveryTests(unittest.TestCase):
    """EMI-shaped streams found in statement rows, keyed on the number that
    repeats — the loan account — not the per-debit reference."""

    def rows(self, notes_dates_amounts):
        return [{"note": n, "entry_date": d, "amount": a} for n, d, a in notes_dates_amounts]

    def test_a_stream_keyed_on_the_repeating_number_survives_reference_churn(self):
        from datetime import date

        from sanctuary_statements import discover_loans

        rows = self.rows(
            [
                (f"ACH/SOME FINANCE/REF{90000 + i}11/00998877665", f"2023-{m:02d}-05", 5000.0)
                for i, m in enumerate(range(1, 9))
            ]
        )
        found = discover_loans(rows, date(2024, 6, 1))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["count"], 8)
        self.assertTrue(found[0]["closed"])

    def test_two_narration_eras_of_one_loan_merge(self):
        from datetime import date

        from sanctuary_statements import discover_loans

        era1 = [("ACH/LENDER/112233445566", f"2023-{m:02d}-10", 9000.0) for m in range(1, 6)]
        era2 = [(f"ACH/LENDER/NEWFMT{770 + m}/112233445566", f"2023-{m:02d}-10", 9000.0) for m in range(6, 12)]
        found = discover_loans(self.rows(era1 + era2), date(2024, 6, 1))
        self.assertEqual(len(found), 1, "one loan, not two")
        self.assertEqual(found[0]["count"], 11)

    def test_unrepeating_references_fall_back_to_the_lender_name(self):
        from datetime import date

        from sanctuary_statements import discover_loans

        # Every debit carries a fresh reference; the lender's name, long and
        # short across formats, is the only constant. 68 such debits were
        # invisible until the name became the key.
        rows = self.rows(
            [
                (f"DECS DR/{6631286928 + i * 7}/TP LENDER CO", f"2017-{m:02d}-10", 20600.0)
                for i, m in enumerate(range(1, 7))
            ]
            + [
                (f"ACH/TP LENDER CO HOMES LTD/{1573707511 + i * 999}", f"2017-{m:02d}-10", 20600.0)
                for i, m in enumerate(range(7, 13))
            ]
        )
        found = discover_loans(rows, date(2018, 6, 1))
        self.assertEqual(len(found), 1, "two narration formats, one loan")
        self.assertEqual(found[0]["count"], 12)

    def test_parallel_loans_on_one_mandate_stay_two(self):
        from datetime import date

        from sanctuary_statements import discover_loans

        rows = self.rows(
            [("ACH/LENDER/556677889900", f"2024-{m:02d}-05", 20000.0) for m in range(1, 13)]
            + [("ACH/LENDER/556677889900", f"2024-{m:02d}-15", 5000.0) for m in range(5, 13)]
        )
        found = discover_loans(rows, date(2025, 1, 1))
        self.assertEqual(sorted((c["count"], c["emi"]) for c in found), [(8, 5000.0), (12, 20000.0)])

    def test_a_restructured_emi_is_one_loan_ending_on_its_last_amount(self):
        from datetime import date

        from sanctuary_statements import discover_loans

        rows = self.rows(
            [("ACH/LENDER/556677889900", f"2019-{m:02d}-05", 20000.0) for m in range(1, 13)]
            + [("ACH/LENDER/556677889900", f"2020-{m:02d}-05", 9000.0) for m in range(1, 13)]
        )
        found = discover_loans(rows, date(2021, 6, 1))
        self.assertEqual([(c["count"], c["emi"]) for c in found], [(24, 9000.0)])

    def test_a_recent_stream_reads_as_running(self):
        from datetime import date

        from sanctuary_statements import discover_loans

        rows = self.rows([("ACH/LENDER/445566778899", f"2024-{m:02d}-07", 12000.0) for m in range(1, 7)])
        found = discover_loans(rows, date(2024, 7, 1))
        self.assertFalse(found[0]["closed"])


class DocumentNamingTests(unittest.TestCase):
    """Reading a paper's own filename for its title, kind, series and date."""

    def read(self, name, folder=""):
        from sanctuary_docs import classify_document

        return classify_document(name, folder)

    def test_a_payslip_is_named_by_its_month(self):
        r = self.read("Payslips/July 2020.pdf", "Payslips")
        self.assertEqual((r["category"], r["series"], r["doc_date"]), ("Work", "Payslips", "2020-07-01"))

    def test_a_tight_month_year_still_reads(self):
        # "Apr2021shiftAll" carries no separators at all.
        self.assertEqual(self.read("Apr2021shiftAll.pdf", "Payslips")["doc_date"], "2021-04-01")

    def test_the_employer_folder_joins_the_title(self):
        r = self.read("Payslips/Kyndryl/Sep2021.pdf", "Payslips/Kyndryl")
        self.assertEqual(r["title"], "Kyndryl · Sep2021")
        self.assertEqual(r["series"], "Payslips")

    def test_shift_allowances_are_not_swallowed_by_their_parent(self):
        # They live INSIDE the payslips folder; the word in their own path
        # must not claim them for Payslips.
        r = self.read("Payslips/Shift Allowances/Aug2020.pdf", "Payslips/Shift Allowances")
        self.assertEqual(r["series"], "Shift allowances")

    def test_his_own_folder_names_file_their_papers(self):
        # The folders he keeps are the strongest signal in the whole folder.
        self.assertEqual(self.read("Evin1.pdf", "IT Submission")["series"], "Tax declarations")
        self.assertEqual(self.read("6thStd_1st_term_fees.pdf", "2022_IT_Proof_Submission")["series"], "School fees")
        self.assertEqual(self.read("e-Nomination.pdf", "EPF")["series"], "EPF")
        self.assertEqual(self.read("Invoice Dec.pdf", "Broadband")["series"], "Utilities")
        self.assertEqual(self.read("2016_ITRV.pdf")["series"], "Tax returns")

    def test_a_file_with_no_extension_is_read_by_its_first_bytes(self):
        import sanctuary

        self.assertEqual(sanctuary._sniff_content_type(b"%PDF-1.4 x", ""), "application/pdf")
        self.assertEqual(
            sanctuary._sniff_content_type(b"\x89PNG\r\n\x1a\nx", "application/octet-stream"),
            "image/png",
        )
        self.assertEqual(sanctuary._sniff_content_type("a note".encode(), ""), "text/plain")
        self.assertEqual(sanctuary._sniff_content_type(b"\x00\x01\x02\xfe", ""), "")

    def test_an_unreadable_name_waits_as_other(self):
        # A name that says nothing must not be guessed at — it waits for a
        # tap. ("Onward to paramakudi" is no longer such a name: the travel
        # rule reads it, which is the point of the rules.)
        r = self.read("Export.pdf")
        self.assertEqual((r["category"], r["series"]), ("Other", ""))


class CardLoanScheduleTests(unittest.TestCase):
    """A credit card's linked-loan table, read into a debt with real dates.

    The figures here are invented — the shape is what the bank prints.
    """

    TABLE = """Loan EMI Table
Principal Tenure Outstanding
Loan Number Loan Booked Date Loan Type Interest Rate (%)
Amount (Rs.) (months) Principal (Rs.)
0000000000999888777 14 Oct 2025 INSTALOAN 300000.00 11.88 3 250000.00
Principal Amount (Rs) Interest Amount (Rs) EMI Date
7370.00 9900.00 13 Nov 2025
7443.00 5867.00 13 Dec 2025
7500.00 5800.00 13 Jan 2026
This is a system generated document and does not require signature."""

    def read(self, text=None, filename="LINKED LOANS_1234_27-08-26_13.35.pdf"):
        import sanctuary_statements

        return sanctuary_statements.parse_card_loan_schedule(text if text is not None else self.TABLE, filename)

    def test_the_header_is_read_whole(self):
        r = self.read()
        self.assertEqual(r["loan_number"], "999888777")
        self.assertEqual(r["booked"], "2025-10-14")
        self.assertEqual((r["principal"], r["rate"], r["tenure"]), (300000.0, 11.88, 3))
        self.assertEqual(r["outstanding"], 250000.0)

    def test_each_instalment_is_principal_plus_interest(self):
        r = self.read()
        self.assertEqual(len(r["emis"]), 3)
        self.assertEqual(r["emis"][0]["amount"], 17270.0, "7370 principal + 9900 interest")
        self.assertEqual((r["first"], r["last"]), ("2025-11-13", "2026-01-13"))
        self.assertEqual(r["emi"], 17270.0)

    def test_every_row_knows_what_is_still_owed(self):
        """The bank prints no running balance, so it is computed — what was
        borrowed less every principal rupee paid through that row. The loan
        card reads its OUTSTANDING from exactly this column; without it the
        card showed a dash."""
        r = self.read()
        self.assertEqual(r["emis"][0]["outstanding"], 292630.0, "300000 - 7370 principal")
        self.assertEqual(r["emis"][1]["outstanding"], 285187.0, "then - 7443")
        self.assertEqual(r["emis"][2]["outstanding"], 277687.0, "then - 7500")

    def test_the_card_is_named_by_the_file_the_bank_hands_over(self):
        self.assertEqual(self.read()["card_tail"], "1234")
        self.assertEqual(self.read(filename="schedule.pdf")["card_tail"], "", "no four-digit token, no card claimed")

    def test_a_half_read_table_is_refused(self):
        """A schedule with a header but no instalments would put a wrong
        debt on the shelf — worse than leaving him to type it."""
        header_only = "\n".join(self.TABLE.splitlines()[:5])
        self.assertIsNone(self.read(header_only))
        self.assertIsNone(self.read("a letter about nothing at all"))
        self.assertIsNone(self.read(""))


class CarryForwardTests(unittest.TestCase):
    """His pay lands on the last days of a month, so what funds August is
    whatever July still held on the 31st."""

    def setUp(self):
        db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        os.environ["PHILFORGE_DB"] = self._db_path
        import importlib

        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        import sanctuary_db

        sanctuary_db.config = config
        self.sanctuary_db = sanctuary_db

    def tearDown(self):
        os.unlink(self._db_path)

    def test_unsorted_rows_carry_their_direction(self):
        """The review row says which way each payee's money went, so the
        query MUST return `source` — omitting it took the whole panel down
        with a 500 that the page then hid as 'nothing to sort'."""

        async def run():
            await self.sanctuary_db.add_ledger_many(
                1,
                [
                    {
                        "entry_date": "2026-08-01",
                        "category": "Uncategorised",
                        "amount": 100.0,
                        "note": "BRK/NA/1",
                        "source": "statement",
                        "ref_id": "stmt:111111:x1",
                    },
                    {
                        "entry_date": "2026-08-02",
                        "category": "Uncategorised",
                        "amount": 200.0,
                        "note": "MMT/Withdrawal/BROKER",
                        "source": "statement-in",
                        "ref_id": "stmt:111111:x2",
                    },
                ],
            )
            return await self.sanctuary_db.uncategorised_ledger(1)

        rows = asyncio.run(run())
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIn("source", row, "the review row cannot tell in from out without it")
        self.assertEqual({r["source"] for r in rows}, {"statement", "statement-in"})

    def test_the_month_before_january_is_last_december(self):
        import sanctuary

        self.assertEqual(sanctuary._previous_month("2026-01"), "2025-12")
        self.assertEqual(sanctuary._previous_month("2026-08"), "2026-07")

    def test_what_arrives_at_month_end_is_the_next_months_envelope(self):
        """His pay lands on the 31st, so it is the NEXT month that spends it.
        The month it landed in must not count it as money to spend."""
        import sanctuary

        async def run():
            await self.sanctuary_db.add_ledger_many(
                1,
                [
                    {
                        "entry_date": "2026-07-31",
                        "category": "Salary",
                        "amount": 120000.0,
                        "note": "NEFT-KYNDRYL",
                        "source": "statement-in",
                        "ref_id": "stmt:111111:a",
                    },
                    {
                        "entry_date": "2026-08-05",
                        "category": "Groceries",
                        "amount": 30000.0,
                        "note": "g",
                        "source": "statement",
                        "ref_id": "stmt:111111:c",
                    },
                ],
            )
            july = await sanctuary._pay_that_arrived(1, "2026-07", {}, 0.0)
            august = await sanctuary._pay_that_arrived(1, "2026-08", {}, 0.0)
            return july, august

        july, august = asyncio.run(run())
        self.assertEqual(july, 120000.0, "July's pay is what August will live on")
        self.assertEqual(august, 0.0, "August's own pay has not landed yet")

    def test_a_month_he_has_priced_himself_is_believed(self):
        """A salary he set by hand outranks the statement, in the carry too."""
        import sanctuary

        async def run():
            return await sanctuary._pay_that_arrived(1, "2026-07", {"2026-07": {"salary": 90000.0}}, 0.0)

        self.assertEqual(asyncio.run(run()), 90000.0)

    def test_the_envelope_travels_whole_not_net_of_spending(self):
        """What August lives on is July's pay ENTIRE. Subtracting July's own
        spending would take the same rupees out twice — July was funded by
        June, not by the pay that arrived on the 31st."""
        import sanctuary

        async def run():
            await self.sanctuary_db.add_ledger_many(
                1,
                [
                    {
                        "entry_date": "2026-07-31",
                        "category": "Salary",
                        "amount": 120000.0,
                        "note": "NEFT-KYNDRYL",
                        "source": "statement-in",
                        "ref_id": "stmt:111111:s",
                    },
                    {
                        "entry_date": "2026-07-09",
                        "category": "Groceries",
                        "amount": 45000.0,
                        "note": "g",
                        "source": "statement",
                        "ref_id": "stmt:111111:g",
                    },
                ],
            )
            return await sanctuary._pay_that_arrived(1, "2026-07", {}, 0.0)

        self.assertEqual(asyncio.run(run()), 120000.0, "the whole pay travels, not 120000-45000")


class CounterpartyAccountTests(unittest.TestCase):
    """An RTGS narration names the other side in full; the IFSC that closes
    it says which bank. Account numbers here are invented."""

    def read(self, note):
        import sanctuary_statements

        return sanctuary_statements.counterparty_accounts(note)

    def test_the_account_before_an_ifsc_is_read_with_its_bank(self):
        found = self.read("RTGS-HDFCR52025110881191896-A NAME-12345678901234  -HDFC0000240")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["number"], "12345678901234")
        self.assertEqual(found[0]["bank"], "HDFC")

    def test_each_bank_is_named_by_its_ifsc(self):
        for ifsc, bank in (("KKBK0001234", "Kotak"), ("ICIC0000123", "ICICI"), ("UTIB0000456", "Axis")):
            found = self.read(f"NEFT-REF999-A NAME-987654321098-{ifsc}")
            self.assertEqual(found[0]["bank"], bank, ifsc)

    def test_a_reference_number_is_never_mistaken_for_an_account(self):
        """A transfer reference is a long digit run too — inventing an
        account he does not hold would be worse than missing one."""
        self.assertEqual(self.read("MMT/IMPS/609257357027/Withdrawal/RAISESECUR/Axis Bank"), [])
        self.assertEqual(self.read("BRK/Raise Securities/20260323024820"), [])
        self.assertEqual(self.read(""), [])


class TaughtRuleTests(unittest.TestCase):
    """A taught rule outlives the row it was taught from, so he has to be
    able to see it and take it back."""

    def setUp(self):
        db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        os.environ["PHILFORGE_DB"] = self._db_path
        import importlib

        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        import sanctuary_db

        sanctuary_db.config = config
        self.sanctuary_db = sanctuary_db

    def tearDown(self):
        os.unlink(self._db_path)

    def test_a_rule_taught_from_one_row_can_be_counted_and_taken_back(self):
        """ "kaliraj r means Alpha school", learned from a term fee, then
        claimed the flour and the rice from the same shop. Correcting a row
        by hand did nothing — the rule was still there."""

        async def run():
            rows = [
                ("UPI/KALIRAJ R/paytmqr2810050/oliver term fee/YES BANK", "Uncategorised"),
                ("UPI/KALIRAJ R/paytmqr2810050/flour/YES BANK", "Uncategorised"),
                ("UPI/KALIRAJ R/paytmqr2810050/rice/YES BANK", "Uncategorised"),
                ("UPI/SOMEONE ELSE/vegetables/HDFC", "Uncategorised"),
            ]
            await self.sanctuary_db.add_ledger_many(
                1,
                [
                    {
                        "entry_date": "2026-08-04",
                        "category": cat,
                        "amount": 100.0,
                        "note": note,
                        "source": "statement",
                        "ref_id": f"stmt:3204:{note[:9]}",
                    }
                    for note, cat in rows
                ],
            )
            held = await self.sanctuary_db.count_rows_matching(1, "kaliraj r")
            claimed = await self.sanctuary_db.recategorise_matching(1, "kaliraj r", "Alpha school", "Uncategorised")
            # taking it back hands its rows to the pile, not to nowhere
            freed = await self.sanctuary_db.recategorise_matching(1, "kaliraj r", "Uncategorised", "Alpha school")
            left = await self.sanctuary_db.uncategorised_ledger(1)
            return held, claimed, freed, len(left)

        held, claimed, freed, left = asyncio.run(run())
        self.assertEqual(held, 3, "the shop's every row, not only the school one")
        self.assertEqual(claimed, 3)
        self.assertEqual(freed, 3, "forgetting the rule puts its rows back")
        self.assertEqual(left, 4, "and the row it never touched was never moved")

    def test_a_match_that_catches_nothing_counts_nothing(self):
        async def run():
            return await self.sanctuary_db.count_rows_matching(1, "  ")

        self.assertEqual(asyncio.run(run()), 0)


class KnownAccountsTests(unittest.TestCase):
    """The accounts the sanctuary can prove he banks through, read back
    from the reference each imported row carries."""

    def setUp(self):
        db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        os.environ["PHILFORGE_DB"] = self._db_path
        import importlib

        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        import sanctuary_db

        sanctuary_db.config = config
        self.sanctuary_db = sanctuary_db

    def tearDown(self):
        os.unlink(self._db_path)

    def test_accounts_are_read_from_the_import_reference(self):
        async def run():
            await self.sanctuary_db.add_ledger_many(
                1,
                [
                    {
                        "entry_date": "2026-03-01",
                        "category": "Uncategorised",
                        "amount": 10.0,
                        "note": "a",
                        "source": "statement",
                        "ref_id": "stmt:503204:aa",
                    },
                    {
                        "entry_date": "2026-03-05",
                        "category": "Uncategorised",
                        "amount": 20.0,
                        "note": "b",
                        "source": "statement",
                        "ref_id": "stmt:503204:bb",
                    },
                    {
                        "entry_date": "2025-01-05",
                        "category": "Salary",
                        "amount": 30.0,
                        "note": "c",
                        "source": "statement-in",
                        "ref_id": "stmt:258400:cc",
                    },
                    # typed by hand — it belongs to no account
                    {
                        "entry_date": "2026-02-02",
                        "category": "Milk",
                        "amount": 40.0,
                        "note": "d",
                        "source": "manual",
                        "ref_id": "",
                    },
                ],
            )
            return await self.sanctuary_db.statement_account_summary(1)

        rows = {r["tail"]: r for r in asyncio.run(run())}
        self.assertEqual(set(rows), {"503204", "258400"}, "a hand-typed row is not an account")
        self.assertEqual(rows["503204"]["entries"], 2)
        self.assertEqual((rows["503204"]["first"], rows["503204"]["last"]), ("2026-03-01", "2026-03-05"))


class PlannerReadingTests(unittest.TestCase):
    """One written line in, a task and a date out. Thursday 27 Aug 2026 is
    'today' throughout, so every expectation below is a real calendar date
    and not a relative guess."""

    TODAY = date(2026, 8, 27)

    def read(self, text):
        import sanctuary_plan

        return sanctuary_plan.read_plan(text, self.TODAY)

    def test_the_phrases_he_actually_writes(self):
        cases = [
            ("call the bank before next wednesday", "call the bank", "2026-09-02", "by"),
            ("school fees before 15th of October", "school fees", "2026-10-15", "by"),
            ("renew the policy after this month", "renew the policy", "2026-08-31", "after"),
            ("Oliver's results tomo", "Oliver's results", "2026-08-28", "on"),
            ("send the photos next week", "send the photos", "2026-08-31", "on"),
            ("review insurance next year", "review insurance", "2027-01-01", "on"),
            ("holiday planning in 3 weeks", "holiday planning", "2026-09-17", "on"),
            ("talk to Beulah day after tomorrow", "talk to Beulah", "2026-08-29", "on"),
            ("pay rent before the 5th", "pay rent", "2026-09-05", "by"),
        ]
        for text, title, due, kind in cases:
            with self.subTest(text=text):
                read = self.read(text)
                self.assertEqual((read["title"], read["due"], read["kind"]), (title, due, kind))

    def test_next_wednesday_is_next_weeks_wednesday(self):
        """Said on a Thursday, 'next wednesday' is six days away, not the
        one in this same week."""
        self.assertEqual(self.read("x next wednesday")["due"], "2026-09-02")
        self.assertEqual(self.read("x on wednesday")["due"], "2026-09-02")

    def test_the_longest_phrase_wins(self):
        """'15th of October' must not lose to the bare '15th' inside it."""
        self.assertEqual(self.read("fees before 15th of October")["due"], "2026-10-15")

    def test_a_verb_that_looks_like_a_month_is_left_alone(self):
        """'march the kids to school tomo' is due tomorrow, not in March."""
        read = self.read("march the kids to school tomo")
        self.assertEqual(read["due"], "2026-08-28")
        self.assertEqual(read["title"], "march the kids to school")

    def test_may_is_a_word_before_it_is_a_month(self):
        read = self.read("I may need to call the plumber")
        self.assertEqual(read["due"], "")
        self.assertEqual(read["title"], "I may need to call the plumber")

    def test_an_unreadable_line_keeps_all_of_itself(self):
        """A wrong date on a school fee is worse than no date, so a line
        with nothing to read stays whole and waits under someday."""
        read = self.read("think about the thing we discussed")
        self.assertEqual((read["title"], read["due"]), ("think about the thing we discussed", ""))

    def test_the_object_of_the_task_is_not_stripped(self):
        """Trimming the dangling preposition must not eat the word the task
        is about — 'clear this' and 'do it' are whole tasks."""
        self.assertEqual(self.read("clear this by 2nd")["title"], "clear this")
        self.assertEqual(self.read("do it before friday")["title"], "do it")

    def test_a_past_date_this_year_means_next_year(self):
        """In late August, '15 March' is next March."""
        self.assertEqual(self.read("audit on 15 March")["due"], "2027-03-15")

    def test_the_words_he_wrote_travel_with_the_date(self):
        read = self.read("call the bank before next wednesday")
        self.assertEqual(read["said"], "before next wednesday")

    def test_horizons_sort_the_week_the_way_it_feels(self):
        import sanctuary_plan as plan

        self.assertEqual(plan.horizon("2026-08-26", self.TODAY), "overdue")
        self.assertEqual(plan.horizon("2026-08-27", self.TODAY), "today")
        self.assertEqual(plan.horizon("2026-08-28", self.TODAY), "tomorrow")
        self.assertEqual(plan.horizon("2026-08-30", self.TODAY), "this week")
        self.assertEqual(plan.horizon("2026-09-02", self.TODAY), "next week")
        self.assertEqual(plan.horizon("2026-12-01", self.TODAY), "later")
        self.assertEqual(plan.horizon("", self.TODAY), "someday")


class OverdraftTests(unittest.TestCase):
    """The sweep-linked overdraft read as a debt: what it drew, what went
    back, and — the load-bearing part — that neither is spending."""

    def rows(self):
        return [
            {"category": "OD loan", "source": "statement-in", "amount": 959.39, "note": "035005008452: Rev Sweep From"},
            {"category": "OD loan", "source": "statement-in", "amount": 3613.0, "note": "035005008452: Rev Sweep From"},
            {"category": "OD loan", "source": "statement", "amount": 96169.0, "note": "Sweep to OD Ac"},
            {"category": "Groceries", "source": "statement", "amount": 576.0, "note": "UPI/shop"},
        ]

    def test_the_overdraft_reports_both_directions_and_its_account(self):
        import sanctuary

        od = sanctuary._od_movement(self.rows())
        self.assertEqual(od["drawn"], 4572.39, "money out of the OD is the debt growing")
        self.assertEqual(od["repaid"], 96169.0)
        self.assertEqual(od["deeper"], -91596.61, "he ended the month owing it less")
        self.assertEqual(od["moves"], 3)
        self.assertEqual(od["account"], "035005008452", "the reverse sweep names it")

    def test_neither_half_of_a_sweep_is_spending(self):
        """This is the whole point. What the overdraft funded is already in
        the ledger line by line; counting a ninety-six thousand rupee sweep
        on top would bury a month's groceries in bank housekeeping."""
        import sanctuary

        excluded = {"Self transfer", sanctuary.OD_CATEGORY}
        outgo = [r for r in self.rows() if r["source"] != "statement-in"]
        spent = sum(r["amount"] for r in outgo if r["category"] not in excluded)
        self.assertEqual(spent, 576.0)

    def test_what_is_owed_and_what_moved_are_kept_apart(self):
        """He read ₹1.55L off the card and said the data was wrong — it was
        the month's movement, and the card let it be read as a balance. The
        balance is HIS figure, off the bank, and it now leads."""
        import sanctuary

        loans = [
            {
                "id": 4,
                "name": "Sweep overdraft ··8452",
                "account_no": "035005008452",
                "drawn_amount": 327000.0,
                # The day it was true. Without one it is not a balance at all
                # — see the test below.
                "stated_on": "2026-01-31",
            }
        ]
        view = sanctuary._od_view(self.rows(), loans)
        self.assertEqual(view["owed"], 327000.0)
        self.assertTrue(view["owed_said"])
        self.assertEqual(view["loan_id"], 4)
        self.assertEqual(view["drawn"], 4572.39, "the movement is still reported, as movement")

    def test_a_figure_with_no_day_on_it_is_not_a_balance(self):
        """His card carried three lakh nineteen thousand with no date, so
        nothing could carry it forward and the sweeps ran on underneath it
        for months. The panel must ask for it, not assert it."""
        import sanctuary

        loans = [{"id": 4, "name": "Sweep overdraft ··8452", "drawn_amount": 319267.84, "stated_on": ""}]
        view = sanctuary._od_view(self.rows(), loans)
        self.assertFalse(view["owed_said"])
        self.assertEqual(view["owed"], 0.0)
        self.assertTrue(view["owed_unanchored"], "and it says why it is asking")

    def test_the_stated_balance_is_carried_forward_by_later_sweeps(self):
        """He asked whether the figure updates itself. On its own it never
        would; every sweep since the day he stated it now carries it."""
        import sanctuary

        loans = [
            {
                "id": 4,
                "name": "Sweep overdraft",
                "account_no": "035005008452",
                "drawn_amount": 327000.0,
                "stated_on": "2026-08-28",
            }
        ]
        since = [
            {"amount": 18000.0, "source": "statement", "note": "Sweep to OD Ac"},  # paid down
            {"amount": 5000.0, "source": "statement-in", "note": "Rev Sweep From"},  # drawn again
        ]
        view = sanctuary._od_view(self.rows(), loans, since)
        self.assertEqual(view["moved_since"], -13000.0, "5,000 drawn less 18,000 repaid")
        self.assertEqual(view["now"], 314000.0)
        self.assertEqual(view["sweeps_since"], 2)
        self.assertEqual(view["owed"], 327000.0, "his own figure is never written over")

    def test_accepting_the_carried_figure_does_not_apply_the_same_sweep_twice(self):
        """Pinned at today alone, a sweep dated ahead of today stayed
        "since" for ever: accepting ₹3,09,000 immediately offered
        ₹2,91,000, then ₹2,73,000 — the same eighteen thousand every
        time the card was opened."""
        import sanctuary

        rows = [{"entry_date": "2026-09-03"}, {"entry_date": "2026-08-30"}]
        self.assertEqual(sanctuary._od_anchor("2026-08-28", rows), "2026-09-03")

    def test_the_anchor_is_today_when_every_sweep_is_behind_it(self):
        import sanctuary

        rows = [{"entry_date": "2026-08-02"}, {"entry_date": "2026-08-19"}]
        self.assertEqual(sanctuary._od_anchor("2026-08-28", rows), "2026-08-28")
        self.assertEqual(sanctuary._od_anchor("2026-08-28", []), "2026-08-28")

    def test_without_a_stated_balance_there_is_nothing_to_carry(self):
        import sanctuary

        view = sanctuary._od_view(self.rows(), [], [{"amount": 9.0, "source": "statement", "note": "x"}])
        self.assertEqual(view["now"], 0.0, "an estimate off no anchor would be a guess")

    def test_an_unstated_balance_says_so_rather_than_guessing(self):
        """A partial ledger cannot know what the overdraft stood at before
        the months it holds, so it must not imply that it does."""
        import sanctuary

        view = sanctuary._od_view(self.rows(), [])
        self.assertFalse(view["owed_said"])
        self.assertEqual(view["owed"], 0.0)
        self.assertEqual(view["loan_id"], 0)

    def test_the_overdrafts_loan_card_is_found_by_its_account(self):
        import sanctuary

        loans = [
            {"id": 1, "name": "Home loan", "account_no": "137201002859", "drawn_amount": 0},
            {"id": 2, "name": "Sweep overdraft ··8452", "account_no": "035005008452", "drawn_amount": 5.0},
        ]
        self.assertEqual(sanctuary._od_loan(loans, "035005008452")["id"], 2)
        self.assertIsNone(sanctuary._od_loan([loans[0]], "035005008452"))

    def test_sweeps_already_filed_the_old_way_are_moved(self):
        """The resort button reads only the UNSORTED pile, so rows already
        filed as "Self transfer" were invisible to it and nothing moved."""
        import sanctuary

        self.assertEqual(sanctuary._recategorised("035005008452: Rev Sweep From", "Self transfer"), "OD loan")
        self.assertEqual(sanctuary._recategorised("Sweep to OD Ac", "Self transfer"), "OD loan")
        self.assertEqual(sanctuary._recategorised("Sweep to OD Ac", "Uncategorised"), "OD loan")

    def test_a_row_he_filed_himself_is_left_where_he_put_it(self):
        """A correction may only take from the categories it names. If he
        decided a sweep was Giving, that is his answer and it stands."""
        import sanctuary

        self.assertEqual(sanctuary._recategorised("Sweep to OD Ac", "Giving"), "")
        self.assertEqual(sanctuary._recategorised("UPI/veg shop", "Self transfer"), "", "not a sweep at all")

    def test_a_row_already_right_is_not_moved_again(self):
        import sanctuary

        self.assertEqual(sanctuary._recategorised("Sweep to OD Ac", "OD loan"), "")

    def test_a_month_that_never_touched_the_overdraft_says_nothing(self):
        import sanctuary

        od = sanctuary._od_movement([{"category": "Groceries", "source": "statement", "amount": 10.0, "note": ""}])
        self.assertEqual(od["moves"], 0, "no card is drawn when there were no sweeps")


class PageStampTests(unittest.TestCase):
    """A sanctuary tab stays open for days, running the copy of the page it
    was born with. A fix lands at seven and is still missing at eight, and
    the only evidence is him saying it is not fixed."""

    def test_the_page_carries_the_stamp_the_status_reports(self):
        import sanctuary

        page, version = sanctuary._page_and_version()
        self.assertRegex(version, r"^[0-9a-f]{10}$")
        self.assertIn("__SANCT_VERSION__", page, "the served copy has somewhere to put it")
        self.assertEqual(sanctuary._page_and_version()[1], version, "same file, same stamp")

    def test_a_changed_page_is_a_changed_stamp(self):
        import hashlib

        import sanctuary

        page, version = sanctuary._page_and_version()
        moved = hashlib.sha1((page + "<!-- a fix -->").encode("utf-8"), usedforsecurity=False).hexdigest()[:10]
        self.assertNotEqual(moved, version)

    def test_the_page_asks_again_when_he_comes_back(self):
        """The check is wired to the tab returning, not to a timer — a
        sanctuary that polls all day is a sanctuary that never rests."""
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.html"), encoding="utf-8") as handle:
            page = handle.read()
        self.assertIn('const PAGE_VERSION = "__SANCT_VERSION__"', page)
        self.assertIn("visibilitychange", page)
        self.assertIn("checkPageVersion", page)


class AnOverdraftNeverHoldsMoneyTests(unittest.TestCase):
    """An overdraft's floor is zero. Every figure invented.

    Adding both directions of the sweeps up produced sixty thousand "in
    credit", which is not a thing an overdraft can be. The ledger says why
    itself: the first sweep it holds is a REPAYMENT, so the account was
    alive before these rows begin and its opening balance is missing.
    """

    def standing(self, rows):
        import sanctuary

        return sanctuary._od_standing(rows)

    def sweep(self, day, amount, out_of_the_od):
        return {"entry_date": day, "amount": amount, "source": "statement-in" if out_of_the_od else "statement"}

    def test_a_whole_life_of_sweeps_can_say_where_it_stands(self):
        s = self.standing([self.sweep("2025-01-02", 100000.0, True), self.sweep("2025-02-02", 40000.0, False)])
        self.assertTrue(s["knowable"])
        self.assertEqual(s["owing"], 60000.0)

    def test_repaid_in_full_is_clear_and_never_a_credit(self):
        s = self.standing([self.sweep("2025-01-02", 100000.0, True), self.sweep("2025-02-02", 100000.0, False)])
        self.assertTrue(s["knowable"])
        self.assertEqual(s["owing"], 0.0)
        self.assertNotIn("in_credit", s)

    def test_sweeps_that_begin_mid_life_cannot_say_anything(self):
        # Repaying before ever drawing means the drawing happened earlier,
        # somewhere this ledger has never seen.
        s = self.standing([self.sweep("2024-12-07", 13538.82, False), self.sweep("2024-12-11", 1199.86, True)])
        self.assertFalse(s["knowable"])
        self.assertIsNone(s["owing"])

    def test_dipping_under_water_at_any_point_disqualifies_the_whole_run(self):
        rows = [
            self.sweep("2025-01-02", 100.0, True),
            self.sweep("2025-01-03", 500.0, False),
            self.sweep("2025-01-04", 900.0, True),
        ]
        self.assertFalse(self.standing(rows)["knowable"])

    def test_an_overdraft_with_no_sweeps_says_nothing(self):
        s = self.standing([])
        self.assertFalse(s["knowable"])
        self.assertEqual(s["sweeps"], 0)


class AClaimIsAnsweredByTheBankTests(unittest.TestCase):
    """When a fund claim stops being money on its way. Figures invented.

    The passbook that would prove a claim was paid arrives months later, so
    until then the page promised money that had already been spent.
    """

    def settle(self, claims, credits, today="2026-09-02"):
        from datetime import date

        import sanctuary_epf

        return sanctuary_epf.settle_claims({"claims": claims}, credits, date.fromisoformat(today))

    def claim(self, asked, wanted):
        return {"kind": "claim", "asked_on": asked, "requested": wanted, "awaiting": True}

    def credit(self, day, amount, note="MMT/IMPS/1/EPF transfer/A N OTHER/Bank"):
        return {"entry_date": day, "amount": amount, "note": note, "source": "statement-in"}

    def test_the_money_arriving_settles_the_claim(self):
        out = self.settle([self.claim("2026-08-29", 450000.0)], [self.credit("2026-09-01", 450000.0)])
        self.assertFalse(out["claims"][0]["awaiting"])
        self.assertEqual(out["claims"][0]["paid_on"], "2026-09-01")
        self.assertEqual(out["claimed_pending"], 0.0)

    def test_a_payout_shaved_by_tax_still_settles_it(self):
        out = self.settle([self.claim("2026-08-29", 450000.0)], [self.credit("2026-09-05", 414000.0)])
        self.assertFalse(out["claims"][0]["awaiting"])

    def test_a_credit_of_a_different_size_settles_nothing(self):
        out = self.settle([self.claim("2026-08-29", 450000.0)], [self.credit("2026-09-01", 12000.0)])
        self.assertTrue(out["claims"][0]["awaiting"])
        self.assertEqual(out["claimed_pending"], 450000.0)

    def test_money_that_is_not_the_fund_settles_nothing(self):
        rent = self.credit("2026-09-01", 450000.0, "NEFT/SOMEONE/rent for the year")
        self.assertTrue(self.settle([self.claim("2026-08-29", 450000.0)], [rent])["claims"][0]["awaiting"])

    def test_money_that_arrived_before_he_asked_settles_nothing(self):
        out = self.settle([self.claim("2026-08-29", 450000.0)], [self.credit("2026-08-01", 450000.0)])
        self.assertTrue(out["claims"][0]["awaiting"])

    def test_a_claim_still_open_after_half_a_year_is_not_settled_by_a_later_credit(self):
        out = self.settle([self.claim("2025-01-01", 450000.0)], [self.credit("2026-09-01", 450000.0)])
        self.assertTrue(out["claims"][0]["awaiting"])

    def test_one_payment_does_not_settle_two_claims_of_the_same_size(self):
        two = [self.claim("2026-08-01", 450000.0), self.claim("2026-08-29", 450000.0)]
        out = self.settle(two, [self.credit("2026-09-01", 450000.0)])
        self.assertEqual(sum(1 for c in out["claims"] if c["awaiting"]), 1)


class MoneyTheFundHasPaidLeavesTheFundTests(unittest.TestCase):
    """A passbook dated March cannot still hold what left in September.

    The fund was read off a 31-Mar passbook and the advance was paid on the
    1st of September into his current account. Both places counted it, so
    the page said he owned four and a half lakh twice over.
    """

    def settle(self, *, as_of, paid_on, fund=605889.0, pension=160000.0, member="A"):
        from datetime import date

        import sanctuary_epf

        summary = {
            "accounts": [{"member": member, "as_of": as_of, "fund": fund, "pension": pension}],
            "fund": fund,
            "pension": pension,
            "worth": fund + pension,
            "claims": [
                {"kind": "claim", "member": member, "asked_on": "2026-08-29", "requested": 450000.0, "awaiting": True}
            ],
        }
        credit = {
            "entry_date": paid_on,
            "amount": 450000.0,
            "note": "MMT/IMPS/1/EPF transfer/A N OTHER/Bank",
            "source": "statement-in",
        }
        return sanctuary_epf.settle_claims(summary, [credit], date.fromisoformat("2026-09-03"))

    def test_a_payout_after_the_passbook_comes_off_the_fund(self):
        out = self.settle(as_of="2026-03-31", paid_on="2026-09-01")
        self.assertEqual(out["fund"], 155889.0)
        self.assertEqual(out["paid_out_since"], 450000.0)
        self.assertEqual(out["accounts"][0]["fund"], 155889.0)

    def test_the_pension_is_untouched_and_the_worth_follows_the_fund(self):
        out = self.settle(as_of="2026-03-31", paid_on="2026-09-01")
        self.assertEqual(out["pension"], 160000.0)
        self.assertEqual(out["worth"], 315889.0)

    def test_a_payout_the_passbook_already_knows_about_is_not_taken_off_twice(self):
        out = self.settle(as_of="2026-09-30", paid_on="2026-09-01")
        self.assertEqual(out["fund"], 605889.0)
        self.assertNotIn("paid_out_since", out)

    def test_the_fund_is_emptied_rather_than_driven_below_zero(self):
        out = self.settle(as_of="2026-03-31", paid_on="2026-09-01", fund=100000.0)
        self.assertEqual(out["fund"], 0.0)
        self.assertEqual(out["paid_out_since"], 100000.0)

    def test_a_claim_still_awaiting_takes_nothing_off(self):
        from datetime import date

        import sanctuary_epf

        summary = {
            "accounts": [{"member": "A", "as_of": "2026-03-31", "fund": 605889.0, "pension": 0.0}],
            "fund": 605889.0,
            "pension": 0.0,
            "worth": 605889.0,
            "claims": [
                {"kind": "claim", "member": "A", "asked_on": "2026-08-29", "requested": 450000.0, "awaiting": True}
            ],
        }
        out = sanctuary_epf.settle_claims(summary, [], date.fromisoformat("2026-09-03"))
        self.assertEqual(out["fund"], 605889.0)
        self.assertEqual(out["claimed_pending"], 450000.0)


class TheOverdraftCardReadsLikeACardTests(unittest.TestCase):
    """What the loan card puts in front of him.

    Every value in that grid is one clipped line — right for a figure, wrong
    for a sentence. A sentence put in one of those slots came out as "Its
    sweeps begin 7 Dec 2024, after the account was already run…", beside a
    balance reading "—" and a highlighted button offering to upload a
    schedule, which an overdraft can never have.
    """

    @classmethod
    def setUpClass(cls):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.html"), encoding="utf-8") as handle:
            cls.page = handle.read()

    def test_the_fields_hold_values_short_enough_to_be_read(self):
        self.assertIn("<span>Balance</span>", self.page)
        self.assertIn("<b>Revolving</b>", self.page)
        self.assertIn("<b>No schedule</b>", self.page)
        self.assertIn("<span>Sweeps seen</span>", self.page)
        self.assertNotIn("<b>Revolving — no fixed EMI</b>", self.page)

    def test_a_balance_says_what_it_is_rather_than_a_dash(self):
        # And a stated zero is an answer — "clear" — not a missing figure.
        self.assertIn(
            "l.drawn_amount ? inr(l.drawn_amount) : (l.od_by_sweeps || l.stated_on) "
            '? "Clear \u2014 nothing drawn" : "Not stated"',
            self.page,
        )

    def test_an_explanation_gets_its_own_line_and_wraps(self):
        self.assertIn(".loan-said{", self.page)
        self.assertIn('<p class="loan-said">', self.page)
        self.assertNotIn("<span>Not known here</span>", self.page)

    def test_a_revolving_debt_is_never_offered_a_schedule(self):
        # It has none and can have none; the question was whether it had a
        # figure typed in, so clearing the figure started offering one.
        self.assertIn("function revolving(l){", self.page)
        self.assertIn("return !l.schedule_count && !l.emi_amount;", self.page)
        self.assertIn('${revolving(l) ? `<button class="btn small" data-odset="${l.id}">', self.page)

    def test_the_balance_can_be_set_from_the_card_itself(self):
        self.assertIn("data-odset", self.page)
        self.assertIn('const odset = t.closest("[data-odset]");', self.page)
        self.assertIn('odOwedForm({owed: loan.drawn_amount || 0, account: loan.account_no || ""});', self.page)


class OneNameForOneThingTests(unittest.TestCase):
    """Seventy-eight categories had grown, several of them the same thing
    twice — one by a capital letter, one by a typo, the rest because a new
    name was easier to type than finding the old one."""

    def merges(self):
        import sanctuary

        return sanctuary._RENAMED_CATEGORIES

    def test_anything_eaten_lands_in_one_place(self):
        for was in ("Eatables", "Food & Dining", "Zepto"):
            self.assertEqual(self.merges()[was], "Eating out", was)

    def test_provisions_for_the_house_are_not_a_meal_bought(self):
        # Deliberately left out of it.
        self.assertNotIn("Groceries", self.merges())
        self.assertNotIn("Milk", self.merges())

    def test_the_chemist_the_hospital_and_the_doctor_are_one_word(self):
        self.assertEqual(self.merges()["Medical"], "Health")
        self.assertEqual(self.merges()["Medicines"], "Health")

    def test_the_school_takes_the_name_he_gave_it(self):
        for was in ("Alpha school", "School Fees", "School fees"):
            self.assertEqual(self.merges()[was], "Fees for Oliver and Evin", was)

    def test_a_capital_and_a_typo_are_not_two_categories(self):
        self.assertEqual(self.merges()["Kotak Loan"], "Kotak loan")
        self.assertEqual(self.merges()["Maduari Karthi"], "Madurai Karthi")
        self.assertEqual(self.merges()["Citi loan"], "CitiBank loan")
        self.assertEqual(self.merges()["Home repairs"], "Household Repair")

    def test_no_merge_points_at_a_name_that_is_itself_retired(self):
        # "A" -> "B" -> "C" would leave rows sitting under B for ever.
        merges = self.merges()
        for was, now in merges.items():
            self.assertNotIn(now, merges, "%s lands on %s, which is itself retired" % (was, now))

    def test_no_built_in_rule_still_files_into_a_retired_name(self):
        import sanctuary_statements as st

        retired = set(self.merges())
        stragglers = [r["match"] for r in st.DEFAULT_RULES if r["category"] in retired]
        self.assertEqual(stragglers, [], "these would rebuild the category on the next import")

    def test_the_renaming_happens_before_anything_is_sorted(self):
        # Run after the sorting passes, a rule still pointing at a retired
        # name files fresh rows into it — and the category is put back in the
        # list by the very pass that was meant to retire it. Five of them
        # came straight back that way.
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.py"), encoding="utf-8") as handle:
            code = handle.read()
        renames = code.index("for was, now in _RENAMED_CATEGORIES.items():")
        sorting = code.index("for row in await sanctuary_db.uncategorised_ledger(user_id):")
        self.assertLess(renames, sorting, "the renaming must come first")

    def test_his_own_rules_are_carried_over_too(self):
        # Moving only the rows left his rules pointing at the retired name,
        # and the next import would quietly build it again.
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.py"), encoding="utf-8") as handle:
            code = handle.read()
        self.assertIn('landing = _RENAMED_CATEGORIES.get(str(rule.get("category") or ""))', code)
        self.assertIn("gone = {was for was in _RENAMED_CATEGORIES if was not in _RENAMED_CATEGORIES.values()}", code)


class TheSceneRestsWhenNobodyCanSeeItTests(unittest.TestCase):
    """The shimmer on a page that is not even scrolling.

    The scene is fixed behind everything and never leaves the viewport, so
    the existing pause — which only held it still WHILE he scrolled — did
    nothing for a page at rest. Below the first screen every pane of glass
    is over it, and each frame of a drifting cloud makes all of them blur
    their backdrop again for scenery nobody can see through the tint.
    """

    @classmethod
    def setUpClass(cls):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.html"), encoding="utf-8") as handle:
            cls.page = handle.read()

    def test_it_holds_still_once_he_is_past_the_first_screen(self):
        self.assertIn("html.covered #scene *,html.covered .leaf-float{animation-play-state:paused}", self.page)
        self.assertIn('document.documentElement.classList.toggle("covered", past);', self.page)

    def test_the_scene_is_fixed_so_being_in_view_proves_nothing(self):
        # Which is why this asks how far down he is, not whether the element
        # is on screen — it always is.
        self.assertIn("#scene{position:fixed", self.page)
        self.assertIn("> innerHeight * 0.55", self.page)

    def test_at_the_top_it_still_moves(self):
        # Where the scene IS the view, nothing is taken away.
        self.assertNotIn("#scene *{animation-play-state:paused}", self.page)

    def test_it_is_asked_again_on_scroll_and_on_resize(self):
        self.assertIn("  markCovered();\n}, {passive: true});", self.page)
        self.assertIn('addEventListener("resize", markCovered, {passive: true});', self.page)


class WhatIsLeftToBreatheTests(unittest.TestCase):
    """It is what is in the current account, and nothing else.

    It used to be the month's envelope less what had gone out of it. That was
    a fiction here: his bank sweeps the leftover into the overdraft the same
    night the salary lands, so August's pay was out of the current account
    within hours and the envelope described money that had already moved. It
    also docked him for what he put ASIDE.
    """

    def test_it_is_the_bank_less_what_is_swept_aside(self):
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.py"), encoding="utf-8") as handle:
            code = handle.read()
        self.assertIn('round(bank["current"], 2)', code)
        self.assertIn('if bank.get("current") is not None', code)

    def test_the_old_reckoning_still_answers_where_no_balance_is_known(self):
        # So the tile is never blank before a statement has printed one.
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.py"), encoding="utf-8") as handle:
            code = handle.read()
        self.assertIn("else round(carried_in - spent - emi_total - saved, 2)", code)


class MoneyPutAsideIsNotSpentTests(unittest.TestCase):
    def test_investments_counts_as_a_saving(self):
        import sanctuary

        self.assertIn("Investments", sanctuary._SAVING_CATEGORIES)

    def test_the_four_brokers_are_one_activity_and_it_is_not_spending(self):
        # Trading is not investing — he keeps them apart — but money that
        # goes to a broker is still his when it gets there.
        import sanctuary

        for was in ("AliceBlue", "Fyers", "Zerodha", "Trading_AngelOne"):
            self.assertEqual(sanctuary._RENAMED_CATEGORIES[was], "Trading", was)
        self.assertIn("Trading", sanctuary._SAVING_CATEGORIES)
        self.assertIn("Trading", {c["name"] for c in sanctuary.DEFAULT_CATEGORIES})

    def test_the_kind_is_corrected_on_the_stored_list(self):
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.py"), encoding="utf-8") as handle:
            code = handle.read()
        self.assertIn('if str(c.get("name") or "") in _SAVING_CATEGORIES:', code)
        self.assertIn('c["kind"] = "saving"', code)


class OneBillTwoCardsTests(unittest.TestCase):
    """He holds two cards and one bucket was holding both. Figures invented.

    A year of card spending that cannot say which card it was on is not worth
    much. The narrations name the card themselves, and where the wording does
    not, the card's own number does.
    """

    def filed(self, note):
        import sanctuary_statements as st

        return st.categorise(note)

    def moved(self, note, was="Credit card bill"):
        import sanctuary

        return sanctuary._recategorised(note, was)

    def test_paying_cred_settles_the_hdfc_card(self):
        for note in (
            "UPI/CRED Club/cred.club@axis/payment on/AXIS BANK/1/ABC",
            "UPI/CRED WALLE/cred.wallet@ax/payment on/AXIS BANK/1/ABC",
            "UPI/1/payment on CRED/credclub@icici/ICICI Bank/2/DEF",
        ):
            self.assertEqual(self.filed(note), "HDFC Creditcard", note[:36])

    def test_the_citi_card_is_an_axis_card_now(self):
        self.assertEqual(self.filed("BIL/1/NEFTCC-CITICARD payment/9999"), "AxisBank Creditcard")

    def test_a_bill_that_names_neither_is_told_apart_by_the_card_number(self):
        # "NEFTCC-April Due" says nothing at all; the number says everything.
        self.assertEqual(self.moved("BIL/1/NEFTCC-April Due/524133052031443"), "AxisBank Creditcard")
        self.assertEqual(self.moved("BIL/1/NEFTCC-Credit card c/524216000022"), "HDFC Creditcard")

    def test_the_older_direct_payments_go_to_their_own_card(self):
        self.assertEqual(self.moved("BIL/1/NEFTCC-HDFC Due/524216000022763"), "HDFC Creditcard")
        self.assertEqual(self.moved("BIL/1/NEFTCC-CitiBankdue/5241330520314"), "AxisBank Creditcard")

    def test_only_the_one_bucket_is_touched(self):
        # A row he filed somewhere himself is never pulled into a card.
        self.assertEqual(self.moved("UPI/CRED Club/cred.club@axis/payment on/1", was="Groceries"), "")

    def test_both_cards_are_pickable_by_hand(self):
        import sanctuary

        names = {c["name"] for c in sanctuary.DEFAULT_CATEGORIES}
        self.assertIn("HDFC Creditcard", names)
        self.assertIn("AxisBank Creditcard", names)

    def test_the_split_reads_words_and_not_the_machine(self):
        # "cred" lives inside "Credit card", which is how the first reading of
        # this put three hundred and seventy-five rows in the wrong pile.
        import sanctuary

        self.assertIn("sanctuary_statements.rule_matches(lowered, word)", open(sanctuary.__file__).read())


class ANameThatHoldsNothingTests(unittest.TestCase):
    """Which categories are worth keeping in the list he files from."""

    def retired(self):
        import sanctuary

        return sanctuary._RETIRED_CATEGORIES

    def test_the_empty_and_unspoken_for_are_named_one_by_one(self):
        # Named rather than found by a rule: a rule would come round again on
        # every sort and take away a category he had just made for something
        # he has not spent on yet.
        self.assertIn("Autorickshaw", self.retired())
        self.assertIn("MF SIP", self.retired())

    def test_a_card_he_does_hold_is_not_taken_away_with_the_rule_that_misfilled_it(self):
        # It read as a card he had never owned, and was nearly removed on
        # that reading. Axis took over Citi's cards in India, so his Citi
        # card became an Axis one and the name is right. Only the rule was
        # wrong — it read the bank printed in every UPI line.
        self.assertNotIn("AxisBank Creditcard", self.retired())

    def test_a_name_a_rule_files_into_is_never_taken_away(self):
        # NPS holds nothing, but three built-in rules file into it, and a
        # category a rule files into while the dropdown does not offer it
        # leaves him a row he cannot correct by hand.
        self.assertNotIn("NPS", self.retired())

    def test_it_asks_again_before_removing_rather_than_trusting_the_list(self):
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.py"), encoding="utf-8") as handle:
            code = handle.read()
        self.assertIn("async def _empty_and_unspoken_for(", code)
        self.assertIn("if await _empty_and_unspoken_for(user_id, name, user_rules):", code)

    def test_a_category_holding_rows_is_always_pickable_by_hand(self):
        # Eleven of them were not — Cash withdrawal, Shopping, Fuel, Tithe,
        # Home loan — eight hundred rows he could see and could not correct.
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.py"), encoding="utf-8") as handle:
            code = handle.read()
        self.assertIn("holding = await sanctuary_db.categories_in_use(user_id)", code)
        self.assertIn("for name in sorted(moved) + sorted(holding - set(moved)):", code)

    def test_a_name_nothing_has_ever_used_is_not_added_on_his_behalf(self):
        # A rule that could one day file into Dividend does not earn Dividend
        # a place in the list today.
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.py"), encoding="utf-8") as handle:
            code = handle.read()
        self.assertNotIn('wanted |= {rule["category"] for rule in sanctuary_statements.DEFAULT_RULES}', code)


class MoneyParkedInTheSweepAccountTests(unittest.TestCase):
    """A sweep-linked overdraft is an account, not an abstraction.

    The statement's running balance is the current account, and it is the
    figure AFTER a sweep has taken money across — so what went across
    appeared nowhere at all. Every figure invented.
    """

    def setUp(self):
        db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        os.environ["PHILFORGE_DB"] = self._db_path
        import importlib

        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        import sanctuary
        import sanctuary_db

        sanctuary_db.config = config
        self.db = sanctuary_db
        self.s = sanctuary

    def tearDown(self):
        os.unlink(self._db_path)

    def sweep(self, day, amount, out_of_the_od):
        return {
            "entry_date": day,
            "category": "OD loan",
            "amount": amount,
            "note": "Rev Sweep From" if out_of_the_od else "Sweep to OD Ac",
            "source": "statement-in" if out_of_the_od else "statement",
            "ref_id": "stmt:8452:%s:%s" % (day, amount),
        }

    def held(self):
        return asyncio.run(self.s._money_sitting_in_the_od(1))

    def state(self, amount, on):
        asyncio.run(self.db.set_json_state(1, self.s.OD_HELD_STATE, {"amount": amount, "on": on}))

    def test_nothing_stated_is_nothing_counted(self):
        self.assertIsNone(self.held()["held"])

    def test_what_he_stated_stands_until_a_sweep_moves_it(self):
        self.state(330000.0, "2026-09-03")
        self.assertEqual(self.held()["held"], 330000.0)

    def test_money_swept_in_afterwards_adds_to_it(self):
        self.state(330000.0, "2026-09-03")
        asyncio.run(self.db.add_ledger_many(1, [self.sweep("2026-09-05", 20000.0, False)]))
        self.assertEqual(self.held()["held"], 350000.0)

    def test_money_swept_back_out_takes_from_it(self):
        self.state(330000.0, "2026-09-03")
        asyncio.run(self.db.add_ledger_many(1, [self.sweep("2026-09-05", 30000.0, True)]))
        self.assertEqual(self.held()["held"], 300000.0)

    def test_sweeps_from_before_he_stated_it_are_already_inside_the_figure(self):
        self.state(330000.0, "2026-09-03")
        asyncio.run(self.db.add_ledger_many(1, [self.sweep("2026-08-01", 99999.0, False)]))
        self.assertEqual(self.held()["held"], 330000.0)

    def test_what_it_lends_is_recorded_and_never_counted_as_his(self):
        # A bank's "available balance" adds the limit to the account. A page
        # that did the same would tell him he owns money he must borrow.
        asyncio.run(self.db.set_json_state(1, self.s.OD_HELD_STATE, {"limit": 330000.0}))
        held = self.held()
        self.assertEqual(held["limit"], 330000.0)
        self.assertIsNone(held["held"], "a limit is not money in the account")

        self.state(330000.0, "2026-09-03")
        asyncio.run(
            self.db.set_json_state(1, self.s.OD_HELD_STATE, {"amount": 330000.0, "on": "2026-09-03", "limit": 330000.0})
        )
        held = self.held()
        self.assertEqual((held["held"], held["limit"]), (330000.0, 330000.0), "held once, not twice")

    def test_the_tile_shows_the_two_parts_so_the_sum_can_be_checked(self):
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.html"), encoding="utf-8") as handle:
            page = handle.read()
        self.assertIn("${inr(f.bank_current)} account + ${inr(f.bank_in_od)} sweep", page)
        self.assertIn("function odHeldForm()", page)


class FromTheFileToTheTileTests(unittest.TestCase):
    """The whole chain in one test: a statement in, a balance on the tile.

    It took him three re-imports to find out that nothing happened, because
    each part of this worked and the parts did not meet. The reader read the
    balance and dropped it; the table had nowhere to put it; the page filtered
    the rows out before sending them; and the button that would have sent them
    was only ever drawn when a statement had something new in it. Every figure
    below is invented.
    """

    CSV = (
        "S No,Value Date,Transaction Date,Transaction Remarks,"
        "Withdrawal Amount (INR ),Deposit Amount (INR ),Balance (INR )\n"
        "1,01/09/2026,01/09/2026,UPI/SOMEONE/aaa@ok/rent/YES BANK/1111/AAA,3000.00,,90000.00\n"
        "2,01/09/2026,01/09/2026,NEFT-X-EMPLOYER-SALARY,,80000.00,170000.00\n"
        "3,02/09/2026,02/09/2026,UPI/SHOP/bbb@ok/flour/HDFC/2222/BBB,550.00,,169450.00\n"
    )

    def setUp(self):
        db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        os.environ["PHILFORGE_DB"] = self._db_path
        import importlib

        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        import sanctuary_db

        sanctuary_db.config = config
        self.db = sanctuary_db

    def tearDown(self):
        os.unlink(self._db_path)

    def offer(self):
        """What the preview would say about this statement right now."""
        import sanctuary_statements as st

        parsed = st.parse_statement("stmt.csv", self.CSV.encode())
        self.assertEqual(parsed.get("status"), "ok")
        rows = parsed["rows"]
        existing = asyncio.run(self.db.existing_ledger_refs(1, [r["ref_id"] for r in rows]))
        for row in rows:
            row["posted"] = row["ref_id"] in existing
            row["category"] = "Uncategorised"
        bare = asyncio.run(self.db.refs_missing_balance(1, sorted(existing)))
        fillable = sum(1 for r in rows if r["posted"] and r.get("balance") is not None and r["ref_id"] in bare)
        return rows, len(rows) - len(existing), fillable

    def test_the_reader_hands_the_balance_on(self):
        rows, _new, _fill = self.offer()
        self.assertEqual([r["balance"] for r in rows], [90000.0, 170000.0, 169450.0])

    def test_a_first_import_stores_them_and_the_tile_reads_the_newest(self):
        rows, new_count, _ = self.offer()
        self.assertEqual(new_count, 3)
        self.assertEqual(asyncio.run(self.db.add_ledger_many(1, rows)), 3)
        known = asyncio.run(self.db.balance_last_known(1))
        self.assertEqual((known["balance"], known["as_of"]), (169450.0, "2026-09-02"))

    def test_rows_posted_before_balances_were_kept_are_offered_and_filled(self):
        # exactly his situation: every row posted, not one carrying a balance
        rows, _new, _ = self.offer()
        asyncio.run(self.db.add_ledger_many(1, [{k: v for k, v in r.items() if k != "balance"} for r in rows]))
        self.assertIsNone(asyncio.run(self.db.balance_last_known(1))["balance"])

        rows, new_count, fillable = self.offer()
        self.assertEqual(new_count, 0, "nothing new — the old page stopped here")
        self.assertEqual(fillable, 3, "but three balances are being carried")

        self.assertEqual(asyncio.run(self.db.add_ledger_many(1, rows)), 0)
        self.assertEqual(asyncio.run(self.db.backfill_balances(1, rows)), 3)
        known = asyncio.run(self.db.balance_last_known(1))
        self.assertEqual((known["balance"], known["as_of"]), (169450.0, "2026-09-02"))

    def test_offering_it_a_third_time_has_nothing_left_to_do(self):
        rows, _new, _ = self.offer()
        asyncio.run(self.db.add_ledger_many(1, rows))
        _rows, new_count, fillable = self.offer()
        self.assertEqual((new_count, fillable), (0, 0), "and the page says so plainly")


class WhatIsActuallyInTheAccountTests(unittest.TestCase):
    """The tile that used to hold his pay. Every figure invented."""

    def keep(self, value):
        import sanctuary

        return sanctuary._a_balance(value)

    def test_a_printed_balance_is_kept(self):
        self.assertEqual(self.keep("12345.67"), 12345.67)
        self.assertEqual(self.keep(-880.5), -880.5)

    def test_a_row_that_printed_none_keeps_none(self):
        self.assertIsNone(self.keep(None))
        self.assertIsNone(self.keep(""))
        self.assertIsNone(self.keep("not a number"))

    def test_a_figure_no_account_could_hold_is_refused(self):
        self.assertIsNone(self.keep(10**12))

    def test_a_statement_offered_again_is_sent_whole(self):
        # The browser used to strip out every row already posted, so on a
        # re-import it sent nothing at all and the balances the first
        # reading threw away could never be recovered.
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.html"), encoding="utf-8") as handle:
            page = handle.read()
        self.assertIn("const rows = pv.rows;", page)
        self.assertNotIn("const rows = pv.rows.filter((r) => !r.posted);", page)

    def test_offering_the_statement_again_fills_the_balances_in(self):
        """The whole point, proved against a real table. Figures invented."""
        import asyncio
        import importlib
        import os
        import tempfile

        db_fd, path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        os.environ["PHILFORGE_DB"] = path
        try:
            import config
            import db as core_db

            importlib.reload(config)
            core_db.config = config
            core_db._initialized = False
            core_db._init_db_sync()
            import sanctuary_db

            sanctuary_db.config = config

            first = [
                {"entry_date": "2026-09-01", "amount": 100.0, "note": "a", "ref_id": "stmt:1", "source": "statement"},
                {"entry_date": "2026-09-02", "amount": 200.0, "note": "b", "ref_id": "stmt:2", "source": "statement"},
            ]
            self.assertEqual(asyncio.run(sanctuary_db.add_ledger_many(1, first)), 2)
            self.assertIsNone(asyncio.run(sanctuary_db.balance_last_known(1))["balance"])

            # the same statement offered again, this time carrying balances
            again = [dict(r, balance=b) for r, b in zip(first, (9000.0, 8800.0))]
            self.assertEqual(asyncio.run(sanctuary_db.add_ledger_many(1, again)), 0, "nothing new")
            self.assertEqual(asyncio.run(sanctuary_db.backfill_balances(1, again)), 2)
            known = asyncio.run(sanctuary_db.balance_last_known(1))
            self.assertEqual((known["balance"], known["as_of"]), (8800.0, "2026-09-02"))

            # and it never overwrites one already known
            asyncio.run(sanctuary_db.backfill_balances(1, [dict(again[1], balance=1.0)]))
            self.assertEqual(asyncio.run(sanctuary_db.balance_last_known(1))["balance"], 8800.0)
        finally:
            os.unlink(path)

    def test_a_statement_with_nothing_new_still_offers_its_balances(self):
        # "Everything here is already in the ledger" was the whole blockade:
        # the button never appeared, so the commit route could not be reached
        # and the balances these rows carry could never be handed over.
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.html"), encoding="utf-8") as handle:
            page = handle.read()
        self.assertIn("Fill in ${pv.fillable} running balance", page)
        self.assertIn("pv.fillable", page)

    def test_the_ledger_keeps_a_column_for_it(self):
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "db.py"), encoding="utf-8") as handle:
            schema = handle.read()
        self.assertIn("ALTER TABLE sanctuary_ledger ADD COLUMN balance REAL", schema)


class RulesReadWordsNotReferencesTests(unittest.TestCase):
    """A rule may read the narration's words. It may not read its machine.

    Every narration below is his own shape with the figures and references
    invented. A bank line ends in a reference number and a long hexadecimal
    trace, and a rule of two or three letters finds itself in there every
    single time — "dd" lived inside a trace, so the chemist and the chicken
    shop were both filed as school fees, and correcting either row by hand
    never held because the rule was still there and still reading the trace.
    """

    def filed(self, note, taught=None):
        import sanctuary_statements as st

        return st.categorise(note, taught or [])

    CHEMIST = "UPI/APOLLO PHA/apollopharmacy/PAYMENT FO/YES BANK L/661002306628/YCDDC4D55C94D9241BA8571B646B7B1"
    CHICKEN = "UPI/MADHEENA C/vyapar.1747424/chicken/HDFC BANK/661018315283/YCD1E4923ACB2474B1FB6AF2CA33CAE7DDB"

    def test_a_short_rule_does_not_reach_into_a_reference(self):
        taught = [{"match": "dd", "category": "Alpha school"}]
        self.assertEqual(self.filed(self.CHEMIST, taught), "Health")
        self.assertEqual(self.filed(self.CHICKEN, taught), "Groceries")

    def test_but_a_rule_still_reads_a_name_written_straight_through(self):
        # "pharmacy" sits in the middle of "apollopharmacy" and must be found:
        # the test is not where a word begins, it is what the match landed in.
        self.assertEqual(self.filed("UPI/SOMEONE/apollopharmacy/xx/BANK/1/ABC123"), "Health")

    def test_a_rule_may_name_the_start_of_a_reference_on_purpose(self):
        # He has taught rules that ARE references. Those still hold.
        taught = [{"match": "moblt0310083269", "category": "Amma Account"}]
        self.assertEqual(self.filed("MMT/IMPS/moblt0310083269/something", taught), "Amma Account")

    def test_letters_and_digits_together_are_machine_writing(self):
        taught = [{"match": "ips", "category": "McRennett"}]
        self.assertEqual(self.filed("UPI/731610769535/YCDIPS9F2A1/x", taught), "Uncategorised")

    def test_plain_letters_are_words_wherever_the_match_lands(self):
        taught = [{"match": "chick", "category": "Poultry"}]
        self.assertEqual(self.filed("UPI/SOMEONE/thechickenplace/x", taught), "Poultry")


class ARuleTaughtFromTheRailTests(unittest.TestCase):
    """A rule can be an accident of the narration. Figures invented.

    "axis bank" was taught from one true credit-card payment, and then
    matched the BENEFICIARY BANK printed in every UPI line — so a haircut, a
    dress, a dinner and a vehicle service were all filed as an Axis credit
    card, for a card he has never held. A hundred and twenty-two rows of it.
    """

    def filed(self, note, taught=None):
        import sanctuary_statements as st

        return st.categorise(note, taught or [])

    def test_paying_cred_is_settling_a_card_not_paying_a_bank(self):
        # The line names Axis because that is where CRED collects. Both
        # shapes of it — the club and the wallet — and the bare wording.
        for note in (
            "UPI/CRED WALLE/cred.wallet@ax/payment on/AXIS BANK/661018344311/ACD81d1a25",
            "UPI/CRED Club/cred.club@axis/payment on/AXIS BANK/661018326953/ACD738c1",
            "UPI/419/payment on CRED/somewhere@else/HDFC BANK/1/ABC",
        ):
            self.assertEqual(self.filed(note), "HDFC Creditcard", note[:34])

    def test_a_haircut_paid_into_an_axis_account_stops_being_a_credit_card(self):
        # Nothing here knows what a haircut is, and that is the honest
        # answer — it goes to the pile he sorts, not to a card he never had.
        note = "UPI/004117084052/Hair cut/7358336285@okbi/Axis Bank Ltd./XYZ"
        self.assertEqual(self.filed(note), "Uncategorised")
        self.assertEqual(
            self.filed(note, [{"match": "axis bank", "category": "AxisBank Creditcard"}]), "AxisBank Creditcard"
        )

    def test_every_retired_rule_is_named_with_its_reason(self):
        import sanctuary

        gone = {r["match"]: r for r in sanctuary._RETIRED_RULES}
        self.assertEqual(gone["axis bank"]["category"], "AxisBank Creditcard")
        # Paying CRED settles a card. He holds no loan with them.
        self.assertEqual(gone["cred club"]["category"], "CRED Loan")
        self.assertEqual(gone["credclub"]["category"], "CRED Loan")
        for rule in sanctuary._RETIRED_RULES:
            self.assertTrue(rule["why"], rule["match"])

    def test_a_retired_rule_is_asked_directly_for_its_own_rows(self):
        # The repair everywhere else asks what the rulebook USED to say. That
        # cannot find these: the rule that filed them has just been taken out
        # of the book, so it can no longer own up to them. The first sort
        # retired the rules and moved nothing, and no later sort would ever
        # have freed them.
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.py"), encoding="utf-8") as handle:
            code = handle.read()
        self.assertIn("for gone in _RETIRED_RULES:", code)
        self.assertIn('if not sanctuary_statements.rule_matches(row["note"], gone["match"]):', code)

    def test_a_rule_can_be_asked_whether_it_takes_hold(self):
        import sanctuary_statements as st

        # by the same reading the categoriser uses: words, never the machine
        self.assertTrue(st.rule_matches("UPI/X/cred.club@axis/payment on/AXIS BANK/1", "axis bank"))
        self.assertFalse(st.rule_matches("UPI/X/shop@ok/thing/HDFC/1/YCDDC4D5", "dd"))
        self.assertFalse(st.rule_matches("anything", ""))

    def test_a_retired_rules_categories_may_be_emptied_back_to_the_pile(self):
        # Normally a row is never un-filed — the pile is worse than an
        # imperfect label. But a row filed by a rule that has just been
        # retired does not have a label, it has a lie.
        import os.path

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.py"), encoding="utf-8") as handle:
            code = handle.read()
        self.assertIn('voided = {str(gone["category"]) for gone in _RETIRED_RULES}', code)
        self.assertIn(
            'if now == sanctuary_statements.UNCATEGORISED and row["category"] not in voided:',
            code,
        )

    def test_every_shape_of_paying_cred_reads_the_same_way(self):
        # Two rows of the same kind landing in two categories is what sent
        # him looking in the first place.
        for note in (
            "UPI/CRED WALLE/cred.wallet@ax/payment on/AXIS BANK/6610183/ACD8",
            "UPI/CRED Club/cred.club@axis/payment on/AXIS BANK/6610183/ACD7",
            "UPI/305037500186/cred/credclub@icici/ICICI Bank/MNOZ54dz",
            "UPI/419/payment on CRED/somewhere@else/HDFC BANK/1/ABC",
        ):
            self.assertEqual(self.filed(note), "HDFC Creditcard", note[:38])

    def test_only_that_exact_rule_is_retired(self):
        # A rule of the same words pointing somewhere else is his own and
        # must survive.
        import sanctuary

        keep = {"match": "axis bank", "category": "Axis Bank Loan"}
        matches = any(
            str(keep["match"]) == g["match"] and str(keep["category"]) == g["category"]
            for g in sanctuary._RETIRED_RULES
        )
        self.assertFalse(matches)


class TheRuleThatKnowsMostWinsTests(unittest.TestCase):
    """Which rule takes a row when more than one fits. Figures invented."""

    def filed(self, note, taught=None):
        import sanctuary_statements as st

        return st.categorise(note, taught or [])

    def test_the_rule_that_recognised_more_of_the_line_wins(self):
        # Taught first used to mean taught wins, however little it knew:
        # "philip" filed his own transfers as a broking account because it
        # had been taught before "philip ranjith".
        taught = [
            {"match": "philip", "category": "Kotak Neo"},
            {"match": "philip ranjith", "category": "Self transfer"},
        ]
        self.assertEqual(self.filed("INF/308164322648/PHILIP RANJITH KUMAR", taught), "Self transfer")

    def test_order_taught_does_not_decide_it(self):
        both = [
            {"match": "philip ranjith", "category": "Self transfer"},
            {"match": "philip", "category": "Kotak Neo"},
        ]
        self.assertEqual(self.filed("INF/308164322648/PHILIP RANJITH KUMAR", both), "Self transfer")

    def test_a_rule_he_taught_outranks_anything_built_in(self):
        # He is correcting this page. A correction a built-in rule can
        # overturn is not a correction — however much more the built-in knew.
        taught = [{"match": "decs dr", "category": "CanFin Loan Repayment"}]
        self.assertEqual(self.filed("DECS DR/6631286928/TP CAN FIN", taught), "CanFin Loan Repayment")

    def test_two_rules_about_different_things_keep_the_order_he_chose(self):
        # A Jio bill that went over CRED is still a Jio bill. Neither rule
        # spells out the other, so nothing here decides for him.
        taught = [
            {"match": "jiofiber", "category": "Mobile & Internet"},
            {"match": "paid via cred", "category": "Credit card bill"},
        ]
        note = "UPI/327794801694/Paid via CRED/jiofiber-paytm@/Paytm Payme/1/ABC"
        self.assertEqual(self.filed(note, taught), "Mobile & Internet")
        self.assertEqual(self.filed(note, list(reversed(taught))), "Credit card bill")

    def test_the_longest_built_in_wins_among_built_ins(self):
        # A statement truncates the payee to ten characters, so Apple Media
        # Services arrives as "APPLE MEDI" and was read as a chemist.
        note = "UPI/APPLE MEDI/appleservices./Mandate Re/HDFC BANK/103973029527/HDF11C82D725F1F49279"
        self.assertEqual(self.filed(note), "Subscriptions")


class UndoingTheOldReadingTests(unittest.TestCase):
    """Which already-filed rows may be moved when the rulebook is mended.

    A row still sitting under exactly what the broken reading said is a row
    a rule put there. A row under anything else is one he filed himself.
    """

    def old_said(self, note, category, taught=None):
        import sanctuary_statements as st

        return st.filed_by_the_old_reading(note, category, taught or [])

    CHEMIST = "UPI/APOLLO PHA/apollopharmacy/PAY/YES BANK L/661002306628/YCDDC4D55C94D9241BA8571B646B7B1"

    def test_a_row_the_broken_rule_filed_is_recognised(self):
        taught = [{"match": "dd", "category": "Alpha school"}]
        self.assertTrue(self.old_said(self.CHEMIST, "Alpha school", taught))

    def test_a_row_he_moved_himself_is_left_alone(self):
        taught = [{"match": "dd", "category": "Alpha school"}]
        self.assertFalse(self.old_said(self.CHEMIST, "Medical", taught))
        self.assertFalse(self.old_said(self.CHEMIST, "Health", taught))

    def test_a_row_no_rule_ever_claimed_counts_as_unsorted(self):
        self.assertTrue(self.old_said("QQQ/zzz/nothing here", "Uncategorised", []))


class WideNetTests(unittest.TestCase):
    """A rule is a substring, not a name. "shop" sits inside every UPI line
    that mentions one, so a rule taught from one bag of flour can hold a
    year of newspapers, juice and biriyani. The catch is counted and shown
    before it is cast."""

    def setUp(self):
        self._db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(self._db_fd)
        os.environ["PHILFORGE_DB"] = self._db_path
        import importlib

        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        import sanctuary_db

        sanctuary_db.config = config
        self.sanctuary_db = sanctuary_db
        notes = [
            ("UPI/322955983957/Coolers/q72954147@ybl/Yes Bank Ltd/", "Uncategorised"),
            ("UPI/358116614688/Newspaper/saraswathym872@/ICICI Bank/", "Uncategorised"),
            ("UPI/321450701485/Juice/Q332971713@ybl/Yes Bank Ltd/", "Uncategorised"),
            ("UPI/318898423083/Biriyani/jeevanselvabhar/Indian Bank/", "Uncategorised"),
            ("NEFT/ALPHA EDUCATIONAL TRUST/term fee", "Uncategorised"),
            ("UPI/999999999999/Coolers/q72954147@ybl/Yes Bank Ltd/", "Groceries"),
        ]

        async def seed():
            for note, category in notes:
                await self.sanctuary_db.add_ledger(
                    1, {"entry_date": "2026-08-19", "category": category, "amount": 100.0, "note": note}
                )

        asyncio.run(seed())

    def tearDown(self):
        os.unlink(self._db_path)

    def preview(self, match, source="Uncategorised"):
        import sanctuary

        class _Req:
            def __init__(self, params):
                from starlette.datastructures import QueryParams

                self.query_params = QueryParams(params)

        return asyncio.run(sanctuary.statement_rule_preview(_Req({"match": match, "from": source}), {"id": 1}))

    def test_a_bank_name_catches_every_bank(self):
        """The word he would have taught, and the four unrelated rows it
        would have taken with it."""
        seen = self.preview("bank")
        self.assertEqual(seen["claims"], 4)
        self.assertTrue(seen["wide"])
        self.assertTrue(any("Newspaper" in s for s in seen["samples"]), "he sees what it caught")

    def test_the_school_itself_is_not_a_wide_net(self):
        seen = self.preview("alpha educational")
        self.assertEqual(seen["claims"], 1)
        self.assertFalse(seen["wide"], "one long word, one row — no question needed")

    def test_only_the_category_it_draws_from_is_counted(self):
        """The same payee sits in Groceries too, and a rule that splits the
        unsorted pile must not count — or claim — him."""
        self.assertEqual(self.preview("coolers")["claims"], 1)
        self.assertEqual(self.preview("coolers", "Groceries")["claims"], 1)
        self.assertEqual(self.preview("coolers", "Fuel")["claims"], 0)

    def test_a_handle_is_asked_about_by_its_stem(self):
        """Teaching stores "saraswathym872@" as its name half, so the
        preview has to ask the same question the rule will answer."""
        seen = self.preview("saraswathym872@")
        self.assertEqual(seen["match"], "saraswathym872")
        self.assertEqual(seen["claims"], 1)

    def test_a_single_letter_is_refused_a_count(self):
        self.assertEqual(self.preview("a")["claims"], 0, "too short to be a rule at all")


class ForgetTheRuleHeMeantTests(unittest.TestCase):
    """Forget one rule and every rule below it slides up a place. A list
    drawn a minute ago — or a second click — then names a position that now
    belongs to a neighbour, and forgetting a rule also releases its rows.
    So the word he saw travels with the number, and the word wins."""

    def setUp(self):
        self._db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(self._db_fd)
        os.environ["PHILFORGE_DB"] = self._db_path
        import importlib

        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        import sanctuary_db

        sanctuary_db.config = config
        self.sanctuary_db = sanctuary_db

    def tearDown(self):
        os.unlink(self._db_path)

    def forget(self, at, match=None):
        import sanctuary

        class _Req:
            def __init__(self, params):
                from starlette.datastructures import QueryParams

                self.query_params = QueryParams(params)

        params = {"release": "0"}
        if match is not None:
            params["match"] = match
        return asyncio.run(sanctuary.statement_rule_delete(at, _Req(params), {"id": 1}))

    def seed(self):
        asyncio.run(
            self.sanctuary_db.set_json_state(
                1,
                "stmt_rules",
                [
                    {"match": "kaliraj r", "category": "Alpha school"},
                    {"match": "aavin", "category": "Milk"},
                    {"match": "0000055944", "category": "Home loan"},
                ],
            )
        )

    def rules(self):
        return [r["match"] for r in asyncio.run(self.sanctuary_db.get_json_state(1, "stmt_rules", []))]

    def test_the_word_beats_a_stale_position(self):
        self.seed()
        gone = self.forget(0, "0000055944")  # the list said 2, the page says 0
        self.assertEqual(gone["forgot"], "0000055944")
        self.assertEqual(self.rules(), ["kaliraj r", "aavin"], "the neighbour was not touched")

    def test_a_second_click_forgets_nothing_more(self):
        self.seed()
        self.forget(0, "kaliraj r")
        with self.assertRaises(Exception) as caught:
            self.forget(0, "kaliraj r")
        self.assertEqual(getattr(caught.exception, "status_code", None), 404)
        self.assertEqual(self.rules(), ["aavin", "0000055944"], "aavin did not inherit the click")

    def test_a_bare_position_still_works(self):
        """Nothing else calls it, but the number alone must keep its meaning."""
        self.seed()
        self.assertEqual(self.forget(1)["forgot"], "aavin")
        self.assertEqual(self.rules(), ["kaliraj r", "0000055944"])


class CategoryNamingTests(unittest.TestCase):
    """One category, one name. Two spellings of it is two bars on the chart
    and a row he can see but cannot choose."""

    def test_no_category_goes_by_two_names(self):
        """The rules filed the school's fees under "School fees" while the
        category list said "School Fees", so Alpha School's money landed in
        a category the dropdown never offered."""
        import sanctuary
        import sanctuary_statements

        names = {c["name"] for c in sanctuary.DEFAULT_CATEGORIES}
        names |= {r["category"] for r in sanctuary_statements.DEFAULT_RULES}
        by_case: dict = {}
        for name in names:
            by_case.setdefault(name.lower(), set()).add(name)
        clashes = {k: sorted(v) for k, v in by_case.items() if len(v) > 1}
        self.assertEqual(clashes, {}, "two spellings of one category")

    def test_the_school_files_where_the_dropdown_offers(self):
        import sanctuary
        import sanctuary_statements

        offered = {c["name"] for c in sanctuary.DEFAULT_CATEGORIES}
        for note in ("UPI/ALPHA SCHOOL/oliver fees", "NEFT/ALPHA EDUCATIONAL TRUST/2026"):
            with self.subTest(note=note):
                filed = sanctuary_statements.categorise(note)
                self.assertEqual(filed, "Fees for Oliver and Evin")
                self.assertIn(filed, offered, "a rule must file where he can also file by hand")

    def test_the_old_spellings_are_gathered_up(self):
        import sanctuary

        # The school's own name has since been chosen by him, so every one of
        # its old spellings lands there.
        self.assertEqual(sanctuary._RENAMED_CATEGORIES["School fees"], "Fees for Oliver and Evin")
        self.assertEqual(sanctuary._RENAMED_CATEGORIES["EB bill"], "EB Bill")


class CategoryRestoreTests(unittest.TestCase):
    """A rule that files into a category the dropdown does not offer leaves
    him a row he cannot correct."""

    def test_a_category_a_rule_needs_is_restored_to_an_old_list(self):
        import sanctuary

        older = [c for c in sanctuary.DEFAULT_CATEGORIES if c["name"] != "OD loan"]
        self.assertEqual(
            [c["name"] for c in sanctuary._missing_rule_categories(older)],
            ["OD loan"],
            "his list was saved before OD loan existed, and a rule now files into it",
        )

    def test_a_category_he_deleted_stays_deleted(self):
        """Only categories a RULE names come back. No rule files into
        "Music Class", so removing it is his decision and it stands."""
        import sanctuary

        older = [c for c in sanctuary.DEFAULT_CATEGORIES if c["name"] != "Music Class"]
        self.assertEqual(sanctuary._missing_rule_categories(older), [])

    def test_a_complete_list_gains_nothing(self):
        import sanctuary

        self.assertEqual(sanctuary._missing_rule_categories(sanctuary.DEFAULT_CATEGORIES), [])


class PlanNudgeTests(unittest.TestCase):
    """The morning reminder. What it says, and — more importantly — the
    mornings on which it says nothing at all."""

    TODAY = date(2026, 8, 27)

    def text(self, rows):
        import sanctuary

        return sanctuary.plan_nudge_text(rows, self.TODAY)

    def row(self, title, due, done=0, kind="on"):
        return {"title": title, "due_date": due, "due_kind": kind, "done": done}

    def test_a_clear_morning_sends_nothing(self):
        """Only tomorrow's work, and nothing owed today: silence. A reminder
        that arrives when nothing is due teaches him to ignore the rest."""
        self.assertEqual(self.text([self.row("dentist", "2026-08-28")]), "")
        self.assertEqual(self.text([]), "")

    def test_nothing_dated_is_never_a_reminder(self):
        self.assertEqual(self.text([self.row("sort the garage", "")]), "")

    def test_a_finished_task_is_not_owed(self):
        self.assertEqual(self.text([self.row("call the bank", "2026-08-20", done=1)]), "")

    def test_overdue_leads_and_tomorrow_follows(self):
        said = self.text(
            [
                self.row("school fees", "2026-08-20", kind="by"),
                self.row("call the bank", "2026-08-27"),
                self.row("dentist", "2026-08-28"),
            ]
        )
        self.assertIn("<b>Overdue</b>", said)
        self.assertIn("school fees (by 2026-08-20)", said)
        self.assertIn("<b>Today</b>", said)
        self.assertIn("<b>Tomorrow</b>", said)
        self.assertLess(said.index("Overdue"), said.index("Today"), "the late ones come first")
        self.assertLess(said.index("Today"), said.index("Tomorrow"))

    def test_tomorrow_alone_does_not_summon_a_message(self):
        """Tomorrow rides along with a message that was already going out —
        it never causes one on its own."""
        self.assertEqual(self.text([self.row("dentist", "2026-08-28")]), "")

    def test_a_long_list_is_trimmed_rather_than_dumped(self):
        rows = [self.row(f"thing {n}", "2026-08-27") for n in range(9)]
        said = self.text(rows)
        self.assertIn("…and 3 more", said)

    def test_a_title_cannot_smuggle_markup_into_the_message(self):
        said = self.text([self.row("<b>pay</b> & run", "2026-08-27")])
        self.assertIn("&lt;b&gt;pay&lt;/b&gt; &amp; run", said)


class IdentityReadingTests(unittest.TestCase):
    """Reading the registrations off a payslip. Every number below is made
    up — his own never appear in this repository."""

    def find(self, text):
        import sanctuary_identity

        return {item["kind"]: item["number"] for item in sanctuary_identity.find_identifiers(text)}

    def test_an_older_payslip_gives_up_everything(self):
        text = (
            "|Emp No : 08255T PERNR : 12345678 |Department : |\n"
            "|Pay period : 01.09.2020 - 30.09.2020 |PF No. : PY/KRP/12345/123456 |\n"
            "| |UAN No : 100200300400 | |\n"
        )
        self.assertEqual(
            self.find(text),
            {"uan": "100200300400", "pf": "PY/KRP/12345/123456", "pernr": "12345678"},
        )

    def test_a_masked_number_is_refused(self):
        """The newer payslips print 'UAN Number : ******1234'. Storing that
        would leave the panel looking answered when it is not."""
        text = "UAN Number : ******1234\nBank Account No : ******123456\nEmployee ID : 1234567\n"
        found = self.find(text)
        self.assertNotIn("uan", found, "a masked UAN is not a UAN")
        self.assertEqual(found.get("employee_id"), "1234567", "the unmasked ones still come through")

    def test_the_lenders_pan_is_not_his(self):
        """His Form 12BB names the housing company's PAN four lines below
        his own. Taking the wrong one would put a stranger's number on the
        card the family is told to trust."""
        text = (
            "1. Permanent Account Number of the employee: AAAAA1111A\n"
            "(iv) Permanent Account Number of the lender BBBBB2222B\n"
        )
        self.assertEqual(self.find(text).get("pan"), "AAAAA1111A")

    def test_a_page_of_only_someone_elses_pan_yields_nothing(self):
        text = "Permanent Account Number (PAN) of the Lender BBBBB2222B\nCanfin Homes Ltd., Pan :BBBBB2222B\n"
        self.assertNotIn("pan", self.find(text))

    def test_the_papers_vote_and_the_scan_stops_when_they_agree(self):
        import sanctuary_identity

        tally: dict = {}
        for month in ("Sep", "Oct", "Nov"):
            sanctuary_identity.merge_findings(
                tally,
                [
                    {"kind": "uan", "label": "UAN", "number": "100200300400"},
                    {"kind": "pan", "label": "PAN", "number": "AAAAA1111A"},
                    {"kind": "pf", "label": "Provident fund", "number": "PY/KRP/12345/123456"},
                ],
                f"{month} 2020",
            )
        # one stray payslip belonging to somebody else
        sanctuary_identity.merge_findings(tally, [{"kind": "uan", "label": "UAN", "number": "999888777666"}], "a stray")
        best = {item["kind"]: (item["number"], item["papers"]) for item in sanctuary_identity.best_of(tally)}
        self.assertEqual(best["uan"], ("100200300400", 3), "the number the most papers agree on wins")
        self.assertEqual(best["pan"][1], 3)
        self.assertTrue(sanctuary_identity.is_complete(tally), "three papers each is enough to stop reading")

    def test_one_paper_alone_is_not_enough_to_stop(self):
        import sanctuary_identity

        tally: dict = {}
        sanctuary_identity.merge_findings(
            tally, [{"kind": "uan", "label": "UAN", "number": "100200300400"}], "one payslip"
        )
        self.assertFalse(sanctuary_identity.is_complete(tally))


class ImportantInfoTests(unittest.TestCase):
    """What Important info is allowed to say — the numbers a family would
    have to telephone, and never a number that is not one."""

    def test_running_loans_carry_their_account_number(self):
        import sanctuary

        lines = sanctuary._loan_account_lines(
            [
                {"id": 2, "lender": "Zebra Finance", "account_no": "111111", "active": 1},
                {"id": 1, "lender": "Alpha Bank", "account_no": "222222", "active": 1},
                # settled, and one still running but with no number known
                {"id": 3, "lender": "Closed Lender", "account_no": "333333", "active": 0},
                {"id": 4, "lender": "Nameless", "account_no": "", "active": 1},
            ]
        )
        self.assertEqual(
            [(x["lender"], x["number"]) for x in lines],
            [("Alpha Bank", "222222"), ("Zebra Finance", "111111")],
            "only running loans with a number, sorted by lender",
        )
        self.assertEqual(lines[0]["id"], "loan-1", "the row id keys the Show button")

    def test_a_number_that_will_not_decrypt_is_not_shown(self):
        """decrypt_value hands the ciphertext back when the key cannot open
        it, which used to reach the page as an account ending 'xKVg=='."""
        import sanctuary

        view = sanctuary._account_view({"kind": "bank", "bank": "HDFC", "number": "gAAAAABqkAkQnotakey"})
        self.assertEqual(view["number"], "")
        self.assertEqual(view["tail"], "")


class SalaryFromBankTests(unittest.TestCase):
    """A month with no payslip still knows its pay: the employer's credit
    in the bank statement is the salary."""

    def salary(self, rows):
        import sanctuary

        return sanctuary._salary_from_ledger(rows)

    def test_employer_credit_is_the_salary(self):
        """His March pay arrives named by the employer, not by "salary"."""
        rows = [
            {
                "amount": 118505.19,
                "source": "statement-in",
                "category": "Uncategorised",
                "note": "NEFT-DEUTH0260-KYNDRYL SOLUTIONS-0504790",
            },
        ]
        self.assertEqual(self.salary(rows), 118505.19)

    def test_pay_split_across_two_credits_is_summed(self):
        rows = [
            {"amount": 100000.0, "source": "statement-in", "category": "Salary", "note": "NEFT-KYNDRYL"},
            {"amount": 18505.19, "source": "statement-in", "category": "Salary", "note": "NEFT-KYNDRYL ARREARS"},
        ]
        self.assertEqual(self.salary(rows), 118505.19)

    def test_sweeps_and_outgoings_are_not_pay(self):
        """The month's big movements are his own money and his own bills —
        only an employer credit counts, and only when it comes IN."""
        rows = [
            {"amount": 103540.94, "source": "statement-in", "category": "OD loan", "note": "Rev Sweep From"},
            {"amount": 100000.0, "source": "statement-in", "category": "Uncategorised", "note": "BRK/Raise Securities"},
            {"amount": 51698.0, "source": "statement", "category": "HDFC loan", "note": "ACH/HDFC BANK"},
            {"amount": 5000.0, "source": "statement", "category": "Uncategorised", "note": "PAID TO KYNDRYL CANTEEN"},
        ]
        self.assertEqual(self.salary(rows), 0, "a sweep, a broker and a debit are not pay")

    def test_employer_narration_is_categorised_as_salary(self):
        import sanctuary_statements

        self.assertEqual(
            sanctuary_statements.categorise("NEFT-DEUTH0260-KYNDRYL SOLUTIONS-0504790"),
            "Salary",
        )

    def test_his_own_idioms_are_default_rules(self):
        """The brokers, the card bill by NEFT, his wallet and his own Axis
        account — the shapes his statements actually print, so the resort
        button can file them without a taught rule."""
        import sanctuary_statements as st

        for note, want in (
            ("BRK/Raise Securities/20260323024820", "Investments"),
            ("MMT/IMPS/609257357027/Withdrawal/RAISESECUR/Axis Bank", "Investments"),
            ("MMT/IMPS/505410637502/Withdrawal/MONEYLICIO/Yes Bank Ltd", "Investments"),
            ("CMS/ CMS3125142229/ANGEL ONE LIMITED CLIENT", "Investments"),
            ("BIL/NEFT/001589486941/NEFTCC-/PHILIPR/CITI0000003", "Credit card bill"),
            ("MMT/IMPS/818019811931/PayZapp - Phili/PAYZAPP WA/HDFC BANK LTD", "Self transfer"),
            ("MMT/IMPS/523716694159/PHILIPRANJ/UTIB0005390", "Self transfer"),
            ("RTGS-HDFCR52023021482685717-HDFC BANK LTD RA OPS-14", "HDFC loan"),
            # the word he typed at the counter is the truest thing in the row
            ("UPI/MADHEENA CHICKE/551722241235/chicken", "Groceries"),
            ("UPI/SUSHAANTH HOMOE/551005321873/medicine", "Health"),
            ("UPI/CHINNADURAI MUR/551031307144/snacks", "Eating out"),
            ("Int.Pd:3712258400:01-01-2021 to 31-03-2021", "Interest"),
            ("ACH/KISETSUSAISONFINANCE/ICIC70221062430/KISETSUSAI", "Kisetsu loan"),
            ("DECS DR/2630439134/TP CAN FIN", "Home loan"),
            ("BIL/ONL/001675706445/Alpha Educ/760315248/Oliver 1st term", "Fees for Oliver and Evin"),
            ("UPI/phil.shiny@/912713537635/To self", "Self transfer"),
        ):
            self.assertEqual(st.categorise(note), want, note)

    def test_the_traps_that_look_like_rules_but_are_not(self):
        """Patterns I measured and refused: 'lab' catches PineLabs POS
        terminals and Divi's Laboratories, and one handle called
        'paytmqr' stands behind 445 rows of every kind of shop."""
        import sanctuary_statements as st

        # neither may be dragged into Health by a three-letter "lab"
        self.assertNotEqual(st.categorise("UPI/349645/UPI/shoes.425/HDFC BANK LTD/PINELABPOSDQR42"), "Health")
        self.assertNotEqual(st.categorise("NACH-10-CR-DIVISLABORATORIES-000000002702843"), "Health")
        self.assertEqual(st.categorise("UPI/032011257798/UPI/paytmqr28100505/Paytm Payments/"), "Uncategorised")
        # His second handle's remarks say Coin — money going INTO an
        # investment, not a transfer to himself. It must not be swept up
        # by the rule that claims "phil.shiny@".
        self.assertEqual(st.categorise("UPI/300403051101/Coin/phil.shiny-1@ok/Kotak"), "Uncategorised")


class PayslipReadingTests(unittest.TestCase):
    """A payslip's own text, read for the month it belongs to and what
    actually reached the bank."""

    HEAD = "Kyndryl Solutions Pvt. Ltd.\nPAYSLIP FOR THE MONTH OF September 2022\n"

    def read(self, text):
        from sanctuary_docs import read_payslip

        return read_payslip(text)

    def test_a_payslip_gives_its_month_and_take_home(self):
        r = self.read(
            self.HEAD + "|30.09.2022 ICICI BANK 0350 95,170.45 = 109,659.45 - 14,489.00 + 0.00 |\n"
            "|Take Home Pay 95,170.45 |"
        )
        self.assertEqual(r["month"], "2022-09")
        self.assertEqual(r["net"], 95170.45)
        self.assertEqual(r["paid_on"], "2022-09-30")
        self.assertEqual(r["employer"], "Kyndryl Solutions Pvt. Ltd.")

    def test_the_european_format_of_the_older_slips_is_not_read_as_rupees_68(self):
        # IBM's older payslips print "68.026,49" — sixty-eight THOUSAND. Read
        # naively that is Rs 68, a thousandfold lie in a salary field.
        r = self.read(
            "IBM India Pvt. Ltd.\nPAYSLIP FOR THE MONTH OF April 2016\n"
            "|30.04.2016 ICICI BANK 0350 68.026,49 = 75.739,49 - 7.713,00 + 0,00 |\n"
            "|Take Home Pay 68.026,49 |"
        )
        self.assertEqual(r["net"], 68026.49)
        self.assertEqual(r["paid_on"], "2016-04-30")

    def test_the_transfer_date_is_not_the_pay_period_start(self):
        r = self.read(
            self.HEAD + "|Pay period : 01.09.2022 - 30.09.2022 |\n"
            "|30.09.2022 ICICI BANK 0350 95,170.45 = 1.00 - 0.00 + 0.00 |\n"
            "|Take Home Pay 95,170.45 |"
        )
        self.assertEqual(r["paid_on"], "2022-09-30")

    def test_half_a_payslip_reads_as_none(self):
        self.assertIsNone(self.read(self.HEAD + "|no figures here|"))
        self.assertIsNone(self.read("Take Home Pay 95,170.45"))
        self.assertIsNone(self.read("a shopping list"))

    # ── the layout that replaced the one above ──────────────────
    #
    # Every figure below is invented. The employer's newer slip writes "Pay
    # Slip" as two words, rules nothing into a table, and calls the figure
    # NET PAY. The reader knew only the older shape, so each of these was
    # refused without a word and the pay tile kept an out-of-date number.
    NEW = (
        "Acme Systems Private Limited\n"
        "RMZ Titanium, No 175, First Floor\n"
        "Pay Slip for the Month of August 2026\n"
        "Employee Name : A N OTHER Pay Date : 31.08.2026\n"
        "EARNINGS & ALLOWANCES UNITS INR DEDUCTIONS INR\n"
        "Monthly Basic Salary 40,000.00 EE Provident Fund 1,800.00\n"
        "Total Earnings 90,000.00 Total Deductions 12,345.00\n"
        "NET PAY: 77,655.00\n"
        "NET PAY (in words): SEVENTY SEVEN THOUSAND SIX HUNDRED FIFTY FIVE RUPEES ONLY\n"
    )

    def test_the_two_word_heading_and_net_pay_are_read(self):
        r = self.read(self.NEW)
        self.assertEqual(r["month"], "2026-08")
        self.assertEqual(r["net"], 77655.00)
        self.assertEqual(r["employer"], "Acme Systems Private Limited")

    def test_the_stated_pay_date_stands_in_for_a_transfer_row(self):
        self.assertEqual(self.read(self.NEW)["paid_on"], "2026-08-31")

    def test_the_unruled_totals_line_is_read(self):
        r = self.read(self.NEW)
        self.assertEqual(r["gross"], 90000.00)
        self.assertEqual(r["deductions"], 12345.00)

    def test_the_figure_in_words_is_never_mistaken_for_the_figure(self):
        # "NET PAY (in words): SEVENTY SEVEN..." carries no digits. A reader
        # that matched it would set the salary from whatever came next.
        words_first = self.NEW.replace("NET PAY: 77,655.00\n", "")
        self.assertIsNone(self.read(words_first))

    def test_take_home_still_wins_where_a_slip_prints_both(self):
        both = self.NEW + "Take Home Pay 66,000.00\n"
        self.assertEqual(self.read(both)["net"], 66000.00)

    def test_a_paper_that_calls_itself_a_payslip_is_recognisable_as_one(self):
        from sanctuary_docs import looks_like_a_payslip

        self.assertTrue(looks_like_a_payslip("Pay Slip for the Month of August 2026"))
        self.assertTrue(looks_like_a_payslip("PAYSLIP FOR THE MONTH OF April 2016"))
        self.assertFalse(looks_like_a_payslip("Statement of Account for August 2026"))


class TurningOutOfAMonthTests(unittest.TestCase):
    """The book turns a day at a time, and the day after the last of a month
    is the first of the next. It could only ever be told which month to
    show, so it stopped at the month's edge and waited to be told again —
    and the months between two entries can be empty, so stepping one along
    blindly would land on a blank spread.
    """

    def setUp(self):
        db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        os.environ["PHILFORGE_DB"] = self._db_path
        import importlib

        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        import sanctuary_db

        sanctuary_db.config = config
        self.db = sanctuary_db

    def tearDown(self):
        os.unlink(self._db_path)

    def write(self, *days, kind="note", body="something"):
        async def run():
            for day in days:
                await self.db.create_entry(
                    1,
                    {
                        "entry_date": day,
                        "kind": kind,
                        "title": "",
                        "body": body,
                        "music": "",
                        "mood": None,
                        "photos": [],
                    },
                )

        asyncio.run(run())

    def months(self, **kw):
        return asyncio.run(self.db.entry_months(1, **kw))

    def test_the_months_that_hold_writing_come_back_oldest_first(self):
        self.write("2026-03-14", "2026-01-02", "2026-01-29")
        self.assertEqual(self.months(), ["2026-01", "2026-03"])

    def test_a_month_is_named_once_however_many_days_it_holds(self):
        self.write("2026-05-01", "2026-05-02", "2026-05-31")
        self.assertEqual(self.months(), ["2026-05"])

    def test_an_empty_journal_names_no_months(self):
        self.assertEqual(self.months(), [])

    def test_the_months_answer_under_the_same_filter_the_book_is_showing(self):
        # Turning must not carry him into a month that holds nothing the
        # filter would show — the spread would arrive blank.
        self.write("2026-01-05", kind="note")
        self.write("2026-02-05", kind="achievement")
        self.assertEqual(self.months(kind="achievement"), ["2026-02"])
        self.assertEqual(self.months(kind="note"), ["2026-01"])

    def test_a_search_narrows_the_months_too(self):
        self.write("2026-06-01", body="the harbour at dawn")
        self.write("2026-07-01", body="nothing to report")
        self.assertEqual(self.months(query="harbour"), ["2026-06"])


class TheBookTurnsLikePaperTests(unittest.TestCase):
    """What the page itself must do, checked in the page it serves.

    All four of these were his own report: the turn stopped dead at the end
    of a month, arriving in a month it opened at the wrong end, and browsing
    a day's photographs walked the book to the end of the month underneath
    so that closing them never gave the day back.
    """

    @classmethod
    def setUpClass(cls):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.html"), encoding="utf-8") as handle:
            cls.page = handle.read()

    def test_turning_off_the_edge_of_a_month_looks_for_the_month_beside_it(self):
        self.assertIn("function monthBeside(delta)", self.page)
        self.assertIn("const beside = crossing ? monthBeside(delta) : null;", self.page)

    def test_it_arrives_at_the_edge_it_crossed(self):
        self.assertIn('S.dWant = delta > 0 ? "first" : "last";', self.page)

    def test_a_month_he_has_not_written_in_is_not_a_dead_end(self):
        # The journal opens on today's month, and today's month is usually
        # empty. Both arrows were shut off before anything was asked about
        # the months either side, so opening the book stranded him on a
        # blank page with no way out but the month picker.
        self.assertIn('$("d-older").disabled = !back;', self.page)
        self.assertIn('$("d-newer").disabled = !on;', self.page)
        self.assertNotIn('$("d-older").disabled = $("d-newer").disabled = true;', self.page)

    def test_a_blank_month_says_which_way_the_writing_is(self):
        self.assertIn("const back = monthBeside(-1), on = monthBeside(1);", self.page)
        self.assertIn("Turn back for ", self.page)
        self.assertIn("turn on for ", self.page)

    def test_the_arrow_stays_live_while_a_month_beside_it_holds_writing(self):
        self.assertIn('$("d-older").disabled = S.dPage === 0 && !monthBeside(-1);', self.page)
        self.assertIn('$("d-newer").disabled = S.dPage === spreads - 1 && !monthBeside(1);', self.page)

    def test_the_pictures_keep_the_arrows_to_themselves(self):
        self.assertIn("S.deckOpen = true;", self.page)
        self.assertIn("if (S.deckOpen) return;", self.page)

    def test_a_refresh_keeps_the_day_he_was_reading(self):
        self.assertIn("if (S.dWant == null){", self.page)
        self.assertIn("if (here) S.dWant = {date: here.date};", self.page)

    def test_a_day_can_be_asked_for_by_its_date(self):
        self.assertIn("const found = leaves.findIndex((leaf) => leaf.date === want.date);", self.page)

    def test_the_leaf_peels_rather_than_pivoting(self):
        # A leaf does not swing like a door. The free edge lifts first and
        # the paper wraps round a roll that travels towards the spine, so
        # part of the page is still flat while part has already come over.
        # Nothing in CSS bends a rectangle, so it is cut into upright strips
        # and each is stood where its own column of the page would be.
        self.assertIn("function curlLeaf(delta, frontHtml, backHtml)", self.page)
        self.assertIn("const CURL_STRIPS", self.page)
        self.assertIn('strip.className = "curl-strip";', self.page)
        self.assertNotIn("function flipLeaf", self.page)

    def test_the_roll_is_a_cylinder_and_the_page_wraps_around_it(self):
        # Arc length along the paper, turned into an angle by one radius —
        # which is what makes the curvature even instead of a crease.
        self.assertIn("const along = a - fold;", self.page)
        self.assertIn("const angle = along / R;", self.page)
        self.assertIn("x = fold + R * Math.sin(angle);", self.page)
        self.assertIn("z = R * (1 - Math.cos(angle));", self.page)

    def test_the_flat_part_of_the_page_stays_flat(self):
        # Everything between the spine and the fold has not been reached yet.
        self.assertIn("if (a <= fold){ x = a; z = 0; turn = 0; }", self.page)

    def test_paper_folded_right_over_lies_down_instead_of_rolling_on(self):
        # Past half a turn the sheet is face down; carrying on round would
        # roll it into a tube.
        self.assertIn("if (angle <= Math.PI){", self.page)
        self.assertIn("x = fold - (along - Math.PI * R);", self.page)
        self.assertIn("turn = -Math.PI;", self.page)

    def test_each_strip_turns_about_the_edge_nearer_the_spine(self):
        self.assertIn('strip.style.transformOrigin = delta > 0 ? "left center" : "right center";', self.page)

    def test_both_sides_of_the_sheet_are_there(self):
        # What folds over is the other side of the page in his hand, and it
        # is seen from behind, so its columns run the other way.
        self.assertIn("const back = CURL_STRIPS - 1 - i;", self.page)
        self.assertIn(".curl-face.back{transform:rotateY(180deg)}", self.page)

    def test_turning_paper_is_opaque_paper(self):
        # A strip that blurs what is behind it blurs a different patch of
        # the world from its neighbour, and the curve breaks into tiles.
        self.assertIn("--page-solid:", self.page)
        self.assertIn("backdrop-filter:none", self.page)

    def test_the_rising_page_takes_the_light_and_the_fold_takes_the_shadow(self):
        # Evenly lit paper reads as a board. It brightens as it turns its
        # face to the light, and darkens once it is carried past.
        self.assertIn("const lit = 1 + 0.15 * Math.sin(ang) - 0.46 * (1 - Math.cos(ang)) / 2;", self.page)

    def test_the_leaf_never_leaves_the_notebook(self):
        # A page lifted towards the reader grows as it rises. Unpenned it
        # swept up out of the book and over the card above.
        self.assertIn(".leaf-stage{position:absolute;z-index:6;overflow:hidden", self.page)
        self.assertIn('stage.style.width = spread.width + "px";', self.page)
        self.assertIn('stage.style.height = spread.height + "px";', self.page)

    def test_the_leaf_is_driven_by_a_clock_and_not_by_frames(self):
        # Asked for frames while the page was busy, the browser gave twenty
        # a second and the last landed late: a leaf meant to be gone in
        # under half a second lay across the writing for very nearly one,
        # and a day had to be read through yesterday.
        self.assertIn("ticking = setInterval(step, 16);", self.page)
        self.assertIn("const t = Math.min(1, (performance.now() - begun) / TURN_MS);", self.page)
        self.assertNotIn("requestAnimationFrame(step)", self.page)

    def test_a_leaf_is_never_left_standing_over_the_book(self):
        # Once, and only once, however it ends.
        self.assertIn("if (done) return;\n    done = true;\n    clearInterval(ticking);", self.page)
        self.assertIn("setTimeout(finish, TURN_MS + 260);", self.page)

    def test_the_turn_is_short_enough_to_read_through(self):
        # Long enough to read as paper, short enough not to be in the way.
        import re

        ms = int(re.search(r"const TURN_MS = (\d+);", self.page).group(1))
        self.assertLessEqual(ms, 480, "a page he is reading past should not linger")
        self.assertGreaterEqual(ms, 260, "faster than this and it is a cut, not a turn")

    def test_the_destination_is_already_lying_underneath(self):
        # The book is redrawn first; what turns over it is the paper coming
        # off, not a transition between two drawings.
        self.assertIn('const frontHtml = $(delta > 0 ? "page-right" : "page-left").innerHTML;', self.page)
        self.assertIn('const landing = $(delta > 0 ? "page-left" : "page-right").innerHTML;', self.page)

    def test_a_still_page_is_turned_without_the_flight(self):
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)").matches', self.page)
        self.assertIn("if (still) return settle();", self.page)

    def test_one_leaf_at_a_time_and_the_second_press_is_kept(self):
        self.assertIn('if (book.classList.contains("turning")){ S.turnQueued = delta; return; }', self.page)
        self.assertIn("if (S.turnQueued){ const q = S.turnQueued; S.turnQueued = 0; turnPage(q); }", self.page)

    def test_the_month_never_wraps_onto_a_second_line(self):
        # "September 2024" is the longest a month gets, and it wrapped inside
        # the field — a two-storey box among one-storey ones.
        self.assertIn(".cal-face .cal-txt{white-space:nowrap", self.page)


class PayIsWhatAnEmployerPaidTests(unittest.TestCase):
    """Which credits in a month count as his pay. Every figure invented.

    A transfer of somebody else's pay into the house is filed under Salary
    too — the narration even says so. Counting it made a month read five and
    ten thousand rupees richer than the payslip did, and in a month with no
    payslip to argue there would have been nothing to notice it by.
    """

    def sum_of(self, rows):
        import sanctuary

        return sanctuary._salary_from_ledger(rows)

    def credit(self, amount, note, category="Salary"):
        return {"source": "statement-in", "amount": amount, "category": category, "note": note}

    EMPLOYER = "NEFT-0001-KYNDRYL SOLUTIONS-500284000000101 SALARY"
    HOUSEHOLD = "UPI/012310092089/Salary/someone@okbank/Other Bank"

    def test_an_employers_credit_is_his_pay(self):
        self.assertEqual(self.sum_of([self.credit(90000.0, self.EMPLOYER)]), 90000.0)

    def test_a_household_transfer_filed_under_salary_is_not_his_pay(self):
        rows = [self.credit(90000.0, self.EMPLOYER), self.credit(5000.0, self.HOUSEHOLD)]
        self.assertEqual(self.sum_of(rows), 90000.0)

    def test_two_employers_in_the_month_they_changed_are_both_his_pay(self):
        # The month the employer split, both of them paid him.
        rows = [
            self.credit(80000.0, "NEFT-1-KYNDRYL SOLUTIONS-SALARY"),
            self.credit(3875.0, "NEFT-2-IBM INDIA PRIVATE LIMITED-SALARY SEP"),
        ]
        self.assertEqual(self.sum_of(rows), 83875.0)

    def test_old_imports_that_name_no_employer_still_answer(self):
        # Statements imported before the payer was known name nobody. The
        # filing answers for those months so nothing needs re-importing.
        self.assertEqual(self.sum_of([self.credit(72000.0, "NEFT-SALARY CREDIT")]), 72000.0)

    def test_but_only_when_no_employer_is_named_that_month(self):
        rows = [self.credit(90000.0, self.EMPLOYER), self.credit(72000.0, "NEFT-SALARY CREDIT")]
        self.assertEqual(self.sum_of(rows), 90000.0)

    def test_money_going_out_is_never_pay(self):
        rows = [{"source": "statement", "amount": 90000.0, "category": "Salary", "note": self.EMPLOYER}]
        self.assertEqual(self.sum_of(rows), 0.0)

    def test_a_month_with_no_credits_at_all(self):
        self.assertEqual(self.sum_of([]), 0.0)


class WhoSaysWhatThePayWasTests(unittest.TestCase):
    """Which figure the pay tile trusts, and whose it is.

    Every amount here is invented. His own correction and a payslip outrank
    the bank; a figure with nobody behind it does not — that one was written
    before this page recorded its sources, and it is exactly the stale one.
    A month whose salary was stored without a source used to shut the bank
    out for good: the statement carried the true credit and the tile went on
    showing an old employer's number, calling it "your usual pay".
    """

    def stated(self, entry):
        import sanctuary

        return sanctuary._stated_salary(entry)

    def outranks(self, said):
        import sanctuary

        return sanctuary._salary_outranks_the_bank(said)

    def test_a_month_never_spoken_for_has_nothing_stated(self):
        self.assertEqual(self.stated({}), (None, ""))

    def test_his_own_figure_and_a_payslip_outrank_the_bank(self):
        self.assertEqual(self.stated({"salary": 90000.0, "salary_source": "manual"}), (90000.0, "manual"))
        self.assertEqual(self.stated({"salary": 90000.0, "salary_source": "payslip"}), (90000.0, "payslip"))
        self.assertTrue(self.outranks("manual"))
        self.assertTrue(self.outranks("payslip"))

    def test_a_figure_with_nobody_behind_it_does_not_outrank_the_bank(self):
        figure, said = self.stated({"salary": 120000.0})
        self.assertEqual(figure, 120000.0)
        self.assertEqual(said, "kept")
        self.assertFalse(self.outranks("kept"))

    def test_a_statement_never_outranks_itself_into_the_stored_month(self):
        # The bank's figure is never written down, so it can never become
        # the thing that blocks a later, corrected import.
        self.assertFalse(self.outranks("statement"))
        self.assertFalse(self.outranks(""))

    def test_an_unreadable_figure_is_treated_as_nothing_stated(self):
        self.assertEqual(self.stated({"salary": None}), (None, ""))
        self.assertEqual(self.stated({"salary": "not a number"}), (None, ""))


class VaultTests(unittest.TestCase):
    """The vault: encrypted at rest, refusing to store plaintext, and the
    document number encrypted like the file it belongs to."""

    def setUp(self):
        db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        os.environ["PHILFORGE_DB"] = self._db_path
        os.environ["ENCRYPTION_KEY"] = (
            __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()
        )
        import importlib

        import auth
        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        auth.config = config
        auth._fernet = None
        self.auth = auth
        import sanctuary_db

        sanctuary_db.config = config
        self.sanctuary_db = sanctuary_db

    def tearDown(self):
        os.unlink(self._db_path)
        self.auth._fernet = None
        os.environ.pop("ENCRYPTION_KEY", None)

    def test_bytes_round_trip_and_ciphertext_differs(self):
        blob = b"%PDF-1.4 the licence"
        sealed = self.auth.encrypt_bytes(blob)
        self.assertIsNotNone(sealed)
        self.assertNotIn(b"licence", sealed)
        self.assertEqual(self.auth.decrypt_bytes(sealed), blob)

    def test_no_key_means_refusal_not_plaintext(self):
        self.auth._fernet = None
        self.auth.config.ENCRYPTION_KEY = ""
        self.assertIsNone(self.auth.encrypt_bytes(b"secret"))

    def test_document_row_round_trip(self):
        async def run():
            doc_id = await self.sanctuary_db.create_document(
                1,
                {
                    "title": "Driving licence",
                    "category": "Identity",
                    "doc_number": self.auth.encrypt_value("TN-00 1234"),
                    "filename": "dl.pdf",
                    "content_type": "application/pdf",
                    "size": 1234,
                    "file_token": "ab" * 16,
                },
            )
            return await self.sanctuary_db.get_document(1, doc_id)

        doc = asyncio.run(run())
        self.assertEqual(doc["title"], "Driving licence")
        self.assertNotEqual(doc["doc_number"], "TN-00 1234", "the number must not rest in clear")
        self.assertEqual(self.auth.decrypt_value(doc["doc_number"]), "TN-00 1234")

    def test_same_content_is_found_by_its_fingerprint(self):
        """The same paper offered twice must be findable, so it is stored once."""
        import hashlib

        sha = hashlib.sha1(b"%PDF-1.4 the same paper").hexdigest()

        async def run():
            await self.sanctuary_db.create_document(
                1, {"title": "Form 16", "filename": "f16.pdf", "size": 23, "content_sha": sha}
            )
            hit = await self.sanctuary_db.find_document_by_sha(1, sha)
            miss = await self.sanctuary_db.find_document_by_sha(1, "0" * 40)
            other_user = await self.sanctuary_db.find_document_by_sha(2, sha)
            blank = await self.sanctuary_db.find_document_by_sha(1, "")
            return hit, miss, other_user, blank

        hit, miss, other_user, blank = asyncio.run(run())
        self.assertEqual(hit["title"], "Form 16")
        self.assertIsNone(miss)
        self.assertIsNone(other_user, "one user's fingerprints must not answer for another")
        self.assertIsNone(blank, "a blank fingerprint matches nothing, not everything")

    def test_old_rows_without_fingerprint_are_narrowed_by_name_and_size(self):
        async def run():
            await self.sanctuary_db.create_document(1, {"title": "Old", "filename": "a.pdf", "size": 10})
            await self.sanctuary_db.create_document(1, {"title": "Other name", "filename": "b.pdf", "size": 10})
            await self.sanctuary_db.create_document(
                1, {"title": "Stamped", "filename": "a.pdf", "size": 10, "content_sha": "ff" * 20}
            )
            rows = await self.sanctuary_db.documents_without_sha(1, "a.pdf", 10)
            await self.sanctuary_db.set_document_sha(1, rows[0]["id"], "ab" * 20)
            after = await self.sanctuary_db.documents_without_sha(1, "a.pdf", 10)
            return rows, after

        rows, after = asyncio.run(run())
        self.assertEqual([r["title"] for r in rows], ["Old"], "name+size narrows, a stamped row is excluded")
        self.assertEqual(after, [], "once stamped, the row leaves the unfingerprinted set")


class HoldingsTests(unittest.TestCase):
    """A broker's holdings export — what he owns, read from the file he
    downloads. The workbook is built here, so the figures are invented."""

    def book(self, sheets):
        """A minimal .xlsx in the shape Zerodha's Console writes."""
        import zipfile

        NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            for index, rows in enumerate(sheets, start=1):
                body = "".join(
                    "<row>" + "".join(f'<c t="inlineStr"><is><t>{c}</t></is></c>' for c in row) + "</row>"
                    for row in rows
                )
                archive.writestr(
                    f"xl/worksheets/sheet{index}.xml",
                    f'<?xml version="1.0"?><worksheet {NS}><sheetData>{body}</sheetData></worksheet>',
                )
        return buf.getvalue()

    MF = [
        ["Mutual Funds Holdings Statement as on 2026-08-27"],
        ["Summary"],
        ["Invested Value", "50000.0000"],
        ["Present Value", "60000.0000"],
        ["Unrealized P&amp;L", "10000.0000"],
        ["Symbol", "ISIN", "Instrument Type", "Quantity Available", "Average Price", "Previous Closing Price"],
        ["SOME ELSS FUND - DIRECT PLAN", "INF000X01AA1", "Equity - ELSS", "500.0000", "100.0000", "120.0000"],
    ]

    def test_a_holdings_sheet_is_read_whole(self):
        import sanctuary_holdings

        r = sanctuary_holdings.parse_holdings(self.book([self.MF]))
        self.assertEqual(r["as_on"], "2026-08-27")
        self.assertEqual((r["invested"], r["present"], r["gain"]), (50000.0, 60000.0, 10000.0))
        held = r["sheets"][0]["holdings"][0]
        self.assertEqual(held["name"], "SOME ELSS FUND - DIRECT PLAN")
        self.assertEqual((held["units"], held["invested"], held["value"]), (500.0, 50000.0, 60000.0))

    def test_the_combined_sheet_is_a_total_not_a_holding(self):
        """Zerodha repeats every figure under 'Combined'. Counting it would
        double what he owns."""
        import sanctuary_holdings

        combined = [
            ["Combined Holdings Statement as on 2026-08-27"],
            ["Summary"],
            ["Invested Value", "50000.0000"],
            ["Present Value", "60000.0000"],
        ]
        r = sanctuary_holdings.parse_holdings(self.book([self.MF, combined]))
        self.assertEqual(r["present"], 60000.0, "not 120000 — Combined is the same money said twice")
        self.assertEqual([s["kind"] for s in r["sheets"]], ["Mutual Funds"])

    def test_an_empty_side_is_not_news(self):
        """His Equity sheet holds nothing; a row of zeroes is noise."""
        import sanctuary_holdings

        empty = [
            ["Equity Holdings Statement as on 2026-08-27"],
            ["Summary"],
            ["Invested Value", "0.0000"],
            ["Present Value", "0.0000"],
        ]
        r = sanctuary_holdings.parse_holdings(self.book([empty, self.MF]))
        self.assertEqual([s["kind"] for s in r["sheets"]], ["Mutual Funds"])

    def test_a_file_that_is_not_a_holdings_export_is_refused(self):
        import sanctuary_holdings

        self.assertIsNone(sanctuary_holdings.parse_holdings(b"not a workbook at all"))
        self.assertIsNone(sanctuary_holdings.parse_holdings(self.book([[["nothing", "here"]]])))


class PrincipalOwedTests(unittest.TestCase):
    """What he owes is the principal, not the sum of the instalments left.

    An EMI carries interest the lender has not charged yet. Counting it as
    debt made the home loan card say one number and "where I stand" say a
    larger one for the same loan, and inflated the whole total.
    """

    @staticmethod
    def _emi(due, amount, principal, interest, outstanding, paid=""):
        return {
            "due_date": due,
            "amount": amount,
            "principal_part": principal,
            "interest_part": interest,
            "outstanding": outstanding,
            "paid_on": paid,
        }

    def setUp(self):
        import sanctuary

        self.owed = sanctuary.principal_owed
        # Three instalments of 10,000 against a balance of 27,000: 1,000 of
        # what is still to be handed over is interest, not debt.
        self.schedule = [
            self._emi("2026-09-10", 10000.0, 9100.0, 900.0, 17900.0),
            self._emi("2026-10-10", 10000.0, 9400.0, 600.0, 8500.0),
            self._emi("2026-11-10", 8800.0, 8500.0, 300.0, 0.0),
        ]

    def test_the_balance_after_the_last_paid_instalment_is_the_debt(self):
        schedule = list(self.schedule)
        schedule[0] = {**schedule[0], "paid_on": "2026-09-10"}
        self.assertEqual(self.owed(schedule), 17900.0)

    def test_before_the_first_payment_the_opening_balance_is_owed(self):
        self.assertEqual(self.owed(self.schedule), 17900.0 + 9100.0)

    def test_the_interest_not_yet_charged_is_not_debt(self):
        to_hand_over = sum(row["amount"] for row in self.schedule)
        self.assertEqual(to_hand_over, 28800.0)
        self.assertLess(self.owed(self.schedule), to_hand_over, "1,800 of that is unearned interest")

    def test_a_schedule_with_no_balance_column_cannot_say(self):
        bare = [self._emi("2026-09-10", 10000.0, None, None, None)]
        self.assertIsNone(self.owed(bare), "the caller falls back to the instalments left")

    def test_a_repaid_schedule_owes_nothing(self):
        paid = [{**row, "paid_on": row["due_date"]} for row in self.schedule]
        self.assertEqual(self.owed(paid), 0.0)

    def test_no_schedule_at_all(self):
        self.assertIsNone(self.owed([]))


if __name__ == "__main__":
    unittest.main()


class ARuleReadsANameNotAFragmentTests(unittest.TestCase):
    """Two letters taught once, then reading every name that contains them.

    "dd" was taught off a single school row and thereafter found itself in
    the middle of Moinuddin, a mutton shop, which the page filed under the
    boys' school fees.
    """

    def matched(self, note, rule):
        import sanctuary_statements

        return sanctuary_statements.rule_matches(note, rule)

    def test_two_letters_inside_a_name_take_nothing(self):
        self.assertFalse(self.matched("UPI/Moinuddin/Q370010648@ybl/mutton/YES BANK L/661", "dd"))

    def test_two_letters_still_work_where_the_word_begins_with_them(self):
        self.assertTrue(self.matched("BIL/DD charges/12345", "dd"))

    def test_a_three_letter_name_written_into_a_longer_one_still_counts(self):
        """The reason the floor is two and not four: this is a real Jio bill."""
        self.assertTrue(self.matched("UPI/443444334822/Paid via CRED/reliancejioinfo/HDFC BANK LTD", "jio"))

    def test_a_merchant_run_together_with_its_app_still_counts(self):
        self.assertTrue(self.matched("UPI/307806551614/paid via CRED P/gedditzepto.bdp/ICICI Bank", "zepto"))

    def test_the_hexadecimal_trace_is_still_never_read(self):
        self.assertFalse(self.matched("UPI/x/YCDDC4D55C94D9241BA8571B646B7B184CA/", "dd"))


class WhatOneSearchComesToTests(unittest.TestCase):
    """ "How much have I spent on Zepto" is a sum, not a row count.

    The all-years search listed entries and stopped at four hundred of
    them, so the one question he was asking it had no answer on the page.
    """

    def setUp(self):
        self._db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(self._db_fd)
        os.environ["PHILFORGE_DB"] = self._db_path
        import importlib

        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        import sanctuary_db

        sanctuary_db.config = config
        self.sanctuary_db = sanctuary_db

    def tearDown(self):
        os.unlink(self._db_path)

    def rows(self):
        return [
            {
                "entry_date": "2025-02-01",
                "category": "Eating out",
                "amount": 300.0,
                "note": "UPI/zepto/a",
                "source": "statement",
                "ref_id": "z1",
            },
            {
                "entry_date": "2025-06-02",
                "category": "Groceries",
                "amount": 200.0,
                "note": "UPI/gedditzepto/b",
                "source": "statement",
                "ref_id": "z2",
            },
            {
                "entry_date": "2026-01-03",
                "category": "Eating out",
                "amount": 500.0,
                "note": "UPI/zepto/c",
                "source": "statement",
                "ref_id": "z3",
            },
            {
                "entry_date": "2026-01-04",
                "category": "Refund",
                "amount": 120.0,
                "note": "UPI/zepto refund",
                "source": "statement-in",
                "ref_id": "z4",
            },
            {
                "entry_date": "2026-01-05",
                "category": "Fuel",
                "amount": 900.0,
                "note": "UPI/petrol",
                "source": "statement",
                "ref_id": "z5",
            },
        ]

    def tally(self, term="zepto"):
        async def run():
            await self.sanctuary_db.add_ledger_many(1, self.rows())
            return await self.sanctuary_db.search_ledger_tally(1, term)

        return asyncio.run(run())

    def test_it_adds_up_what_went_out_and_leaves_the_unrelated_row_alone(self):
        t = self.tally()
        self.assertEqual(t["spent"], 1000.0)
        self.assertEqual(t["count"], 4)

    def test_money_that_came_back_is_counted_apart_never_netted_off(self):
        self.assertEqual(self.tally()["received"], 120.0)

    def test_it_says_across_what_span(self):
        t = self.tally()
        self.assertEqual((t["first"], t["last"]), ("2025-02-01", "2026-01-04"))

    def test_the_years_break_the_total_down_and_add_back_up_to_it(self):
        t = self.tally()
        self.assertEqual({y["year"]: y["spent"] for y in t["by_year"]}, {"2025": 500.0, "2026": 500.0})
        self.assertEqual(round(sum(y["spent"] for y in t["by_year"]), 2), t["spent"])

    def test_a_term_nothing_answers_is_zero_not_an_error(self):
        t = self.tally("nowhere")
        self.assertEqual((t["count"], t["spent"]), (0, 0.0))


class AnInstalmentBilledToACardIsNotSpentTwiceTests(unittest.TestCase):
    """His two Instaloans are charged to card ··4838.

    The money leaves the bank once, when CRED settles that card — a row the
    ledger already holds. The schedule was spending it a second time,
    because the debit it looks for never appears on its own.
    """

    def setUp(self):
        self._db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        os.close(self._db_fd)
        os.environ["PHILFORGE_DB"] = self._db_path
        import importlib

        import config
        import db as core_db

        importlib.reload(config)
        core_db.config = config
        core_db._initialized = False
        core_db._init_db_sync()
        import sanctuary_db

        sanctuary_db.config = config
        self.sanctuary_db = sanctuary_db

    def tearDown(self):
        os.unlink(self._db_path)

    def month(self, on_a_card):
        import sanctuary

        async def run():
            loan_id = await self.sanctuary_db.create_loan(1, {"name": "Instaloan on card 4838", "emi_amount": 4702})
            # Dated in the past: a month never counts an instalment that
            # has not come due, so a future date would read zero either way
            # and prove nothing about the card.
            await self.sanctuary_db.replace_schedule(1, loan_id, [{"due_date": "2020-09-13", "amount": 4702}])
            if on_a_card:
                await self.sanctuary_db.update_loan(1, loan_id, {"on_a_card": 1})
            return await sanctuary.finance_month(month="2020-09", user={"id": 1})

        return asyncio.run(run())

    def test_a_card_billed_instalment_is_not_counted_as_its_own_spending(self):
        f = self.month(on_a_card=True)
        self.assertEqual(f["totals"]["emis"], 0)
        self.assertTrue(all(e["in_ledger"] for e in f["emis"]))

    def test_the_same_instalment_off_a_card_still_counts(self):
        f = self.month(on_a_card=False)
        self.assertEqual(f["totals"]["emis"], 4702.0)

    def test_the_migration_marks_the_loans_the_importer_named_for_a_card(self):
        """A column added to a table that already holds them must not need
        him to go and tick two boxes it could read off their own names."""

        async def run():
            return await self.sanctuary_db.list_loans(1)

        asyncio.run(self.sanctuary_db.create_loan(1, {"name": "Instaloan on card 4838"}))
        loans = asyncio.run(run())
        self.assertTrue(any("on card" in loan["name"].lower() for loan in loans))


class TheRoomKeepsTheHoursTests(unittest.TestCase):
    """Night from six in the evening, day from six in the morning.

    A hand-made choice holds, but only until the light itself turns — a
    page pinned to dark at nine in the morning is last night's decision,
    not this morning's.
    """

    @classmethod
    def setUpClass(cls):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.html"), encoding="utf-8") as handle:
            cls.page = handle.read()

    def test_the_boundaries_are_six_and_eighteen(self):
        self.assertIn("const NIGHT_FROM = 18, DAY_FROM = 6", self.page)

    def test_a_saved_choice_is_stamped_with_the_hour_it_was_made(self):
        self.assertIn("JSON.stringify({t: t, at: Date.now()})", self.page)

    def test_the_choice_is_only_honoured_within_the_stretch_it_was_made_in(self):
        self.assertIn("saved.at >= thisStretchBegan()", self.page)

    def test_a_page_left_open_turns_on_its_own(self):
        self.assertIn("}, 60000);", self.page)

    def test_loading_paints_without_stamping_a_choice(self):
        """applyTheme on load would record a hand-made choice every time the
        page opened, and the clock would never get another turn."""
        self.assertIn("paintTheme(document.documentElement.dataset.theme)", self.page)
        self.assertNotIn("applyTheme(document.documentElement.dataset.theme);", self.page)


class WhereIStandSaysThreeThingsTests(unittest.TestCase):
    """Thirteen rows of arithmetic is the working-out, not the picture."""

    @classmethod
    def setUpClass(cls):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "sanctuary.html"), encoding="utf-8") as handle:
            cls.page = handle.read()

    def test_the_three_figures_are_owed_have_and_the_gap(self):
        for label in (">Owed <", ">You have <", ">The gap<"):
            self.assertIn(label, self.page)

    def test_what_he_has_is_one_figure_with_its_parts_beneath_it(self):
        self.assertIn("st.in_bank + st.held + st.fund_epf", self.page)
        self.assertIn('class="sf-parts"', self.page)

    def test_the_rows_that_were_not_part_of_the_sum_became_a_footnote(self):
        self.assertIn('class="sf-aside"', self.page)
        for gone in ("Owed, less what you own", "Owed, less the fund as well", "In the provident fund$"):
            self.assertNotIn(gone, self.page)
        # The pension keeps its own row on the FUND's card; what it lost is
        # a second row in this panel, where it was never part of the sum.
        self.assertIn("of pension held with the fund, not spendable", self.page)

    def test_the_overdraft_is_still_said_and_still_not_counted(self):
        self.assertIn("of overdraft you could draw, not counted", self.page)
