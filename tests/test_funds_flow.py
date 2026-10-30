"""Money in and out of each broker, for the Portfolio page (Phil, 2026-10-30)."""

from datetime import date

import pytest

import app as app_module
import funds_flow as ff

# Dhan's ledger rows, in the shape and narrations the live account returned on
# 30-Oct-2026 (amounts made up).
DHAN_ROWS = [
    {
        "voucherdate": "Oct 30, 2026",
        "narration": "Funds Withdrawal",
        "voucherdesc": "Money withdrawn to your Bank Account",
        "debit": "5000.0",
        "credit": "0.00",
        "vouchernumber": "V1",
    },
    {
        "voucherdate": "Oct 19, 2026",
        "narration": "Funds Deposited",
        "voucherdesc": "Money added to your Trading Account",
        "debit": "0.00",
        "credit": "71023.0",
        "vouchernumber": "V2",
    },
    {
        "voucherdate": "Oct 16, 2026",
        "narration": "Monthly Settlement",
        "voucherdesc": "Money transferred to your Bank Account due to Monthly Settlement",
        "debit": "71023.11",
        "credit": "0.00",
        "vouchernumber": "V3",
    },
    {
        "voucherdate": "Oct 16, 2026",
        "narration": "Trades Executed",
        "debit": "1200.0",
        "credit": "0.00",
        "vouchernumber": "V4",
    },
    {
        "voucherdate": "Oct 01, 2026",
        "narration": "Data API Charges",
        "debit": "499.0",
        "credit": "0.00",
        "vouchernumber": "V5",
    },
    {"voucherdate": "Dec 31, 2025", "narration": "OPENING BALANCE", "debit": "0.00", "credit": "368398.73"},
]


def test_dhan_keeps_withdrawals_deposits_and_settlements_apart():
    flows = ff.dhan_flows(DHAN_ROWS)
    assert [(f["date"], f["kind"], f["amount"]) for f in flows] == [
        ("2026-10-30", "withdrawal", 5000.0),
        ("2026-10-19", "deposit", 71023.0),
        ("2026-10-16", "settlement", 71023.11),
    ]
    # Trades, charges and the opening balance are not money moved.
    assert all(f["broker"] == "dhan" for f in flows)
    assert [f["ref"] for f in flows] == ["dhan-V1", "dhan-V2", "dhan-V3"]


ZERODHA_STATEMENT = [
    ["Client ID", "AB1234"],
    [],
    ["particulars", "posting_date", "cost_center", "voucher_type", "debit", "credit", "net_balance"],
    ["Opening Balance", "2026-04-01", "", "", "0", "0", "1000"],
    ["Funds added using UPI from HDFC Bank", "2026-04-02", "NSE-EQ", "Bank Receipts", "0", "50,000.00", "51000"],
    [
        "Net settlement for Equity with settlement number: 2026123",
        "2026-04-03",
        "NSE-EQ",
        "Book Voucher",
        "1200",
        "0",
        "49800",
    ],
    ["Payout of 20000 to HDFC Bank", "2026-04-10", "NSE-EQ", "Bank Payments", "20000", "0", "29800"],
    [
        "Funds transferred back as part of quarterly settlement",
        "2026-07-04",
        "NSE-EQ",
        "Bank Payments",
        "9800",
        "0",
        "20000",
    ],
    ["Closing Balance", "2026-10-30", "", "", "0", "0", "20000"],
]


def test_zerodha_statement_reads_its_bank_receipts_and_payments():
    flows = ff.zerodha_flows(ZERODHA_STATEMENT)
    assert [(f["date"], f["kind"], f["amount"]) for f in flows] == [
        ("2026-04-02", "deposit", 50000.0),
        ("2026-04-10", "withdrawal", 20000.0),
        ("2026-07-04", "settlement", 9800.0),
    ]


def test_a_file_that_is_not_a_funds_statement_is_refused():
    with pytest.raises(ff.LedgerError):
        ff.zerodha_flows([["symbol", "trade_date", "trade_type"], ["NIFTY", "2026-04-02", "buy"]])


def test_reading_the_same_movements_twice_adds_nothing():
    first, added = ff.merge([], ff.dhan_flows(DHAN_ROWS))
    assert added == 3
    again, added = ff.merge(first, ff.dhan_flows(DHAN_ROWS))
    assert added == 0 and again == first
    assert [f["date"] for f in again] == sorted((f["date"] for f in again), reverse=True)


class _Ledger:
    RATE_LIMITED = "__RATE_LIMITED__"

    def __init__(self, limit_after=None):
        self.asked = []
        self.limit_after = limit_after

    def get_ledger(self, start, end):
        if self.limit_after is not None and len(self.asked) >= self.limit_after:
            return self.RATE_LIMITED
        self.asked.append((start, end))
        return [r for r in DHAN_ROWS if start <= ff._parse_date(r["voucherdate"]) <= end]


def test_the_first_read_asks_a_year_at_a_time_and_later_reads_only_the_tail(monkeypatch):
    client = _Ledger()
    flows, through, complete = app_module._fetch_dhan_funds_flow(client, {})
    assert complete
    years = [a[:4] for a, _ in client.asked]
    assert years[0] == "2024" and years == sorted(set(years))
    assert all(a[:4] == b[:4] for a, b in client.asked)
    assert {f["kind"] for f in flows} == {"withdrawal", "deposit", "settlement"}
    # The next read starts ten days before the last one ended.
    client = _Ledger()
    app_module._fetch_dhan_funds_flow(client, {"through": through})
    start = date.fromisoformat(client.asked[0][0])
    assert (date.fromisoformat(through) - start).days == 10


def test_a_rate_limit_stops_the_read_and_says_where():
    client = _Ledger(limit_after=1)
    _flows, through, complete = app_module._fetch_dhan_funds_flow(client, {})
    assert not complete
    assert through == "2025-01-01"


# ── Zerodha, recorded day by day from Kite (Kite keeps no fund history) ──
def test_kite_margins_carry_todays_payout_and_payin(monkeypatch):
    from broker import zerodha

    client = zerodha.ZerodhaClient("k", "t", token_saved_at="2026-10-30T08:00:00+05:30")
    monkeypatch.setattr(
        client,
        "_get_data",
        lambda path, **kw: {
            "net": 152340.5,
            "available": {"opening_balance": 170000, "intraday_payin": 2500},
            "utilised": {"debits": 1000, "payout": 20000},
        },
    )
    funds = client.get_funds()
    assert (funds["payout"], funds["payin"], funds["availabelBalance"]) == (20000.0, 2500.0, 152340.5)


def test_a_days_kite_figures_replace_themselves_and_a_cancelled_request_goes():
    day = "2026-10-30"
    flows = ff.with_kite_day([], day, ff.kite_day_flows(day, 20000, 0))
    assert [(f["kind"], f["amount"], f["source"]) for f in flows] == [("withdrawal", 20000.0, "kite")]
    # Later that day: a second request and money added.
    flows = ff.with_kite_day(flows, day, ff.kite_day_flows(day, 25000, 5000))
    assert sorted((f["kind"], f["amount"]) for f in flows) == [("deposit", 5000.0), ("withdrawal", 25000.0)]
    # The request is cancelled: nothing left to say that day.
    assert ff.with_kite_day(flows, day, ff.kite_day_flows(day, 0, 0)) == []


def test_a_statement_takes_over_the_days_it_covers():
    kite = ff.kite_day_flows("2026-04-10", 20000, 0) + ff.kite_day_flows("2026-11-02", 3000, 0)
    first, last = ff.statement_span(ZERODHA_STATEMENT)
    assert (first, last) == ("2026-04-01", "2026-10-30")
    kept = ff.without_kite_between(kite, first, last)
    assert [f["date"] for f in kept] == ["2026-11-02"]
    merged, _ = ff.merge(kept, ff.zerodha_flows(ZERODHA_STATEMENT))
    # 10-Apr's withdrawal is counted once, from the statement.
    assert [f["amount"] for f in merged if f["date"] == "2026-04-10"] == [20000.0]
    assert all("source" not in f for f in merged if f["date"] == "2026-04-10")


def test_recording_a_zerodha_day_writes_once_and_keeps_imported_rows(monkeypatch):
    import asyncio
    import json

    store = {}

    async def get_state(key):
        return store.get(key)

    async def set_state(key, value):
        store[key] = value

    monkeypatch.setattr(app_module._db_mod, "get_app_state", get_state)
    monkeypatch.setattr(app_module._db_mod, "set_app_state", set_state)
    key = app_module._funds_flow_key("zerodha", 7)
    imported = ff.zerodha_flows(ZERODHA_STATEMENT)
    store[key] = json.dumps({"flows": imported, "imported_at": 1.0})
    asyncio.run(app_module._record_zerodha_funds_day(7, {"payout": 15000.0, "payin": 0.0}))
    state = json.loads(store[key])
    assert state["imported_at"] == 1.0
    kite = [f for f in state["flows"] if f.get("source") == "kite"]
    assert len(kite) == 1 and kite[0]["kind"] == "withdrawal" and kite[0]["amount"] == 15000.0
    assert len(state["flows"]) == len(imported) + 1
    # A Dhan-shaped funds answer (no payout field) records nothing.
    before = store[key]
    asyncio.run(app_module._record_zerodha_funds_day(7, {"availabelBalance": 1.0}))
    assert store[key] == before
