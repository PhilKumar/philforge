"""The CE + PE desk lists what the live books traded — once each, and nothing else.

2026-09-16, Phil: "Why the Live CE PE is still showing wrong data?" The ALL
TIME table under the two books held, besides the books' own trades:

    PE  old closed  16-Sep 09:28  NIFTY-Sep2026-23250-PE   520  ₹1,823*   Phil's own scalp
    PE  old closed  16-Sep  —     NIFTY-Sep2026-23350-PE     0  ₹0*       Gap Carry's exit, half a trade
    PE  old closed  15-Sep 15:15  NIFTY 22 SEP 23350 PUT     0  -₹31      Gap Carry's entry, the other half
    PE  old closed  15-Sep 13:11  NIFTY 15 SEP 23500 PUT   260  ₹914      a scalp (and three more like it)
    PE  old closed  15-Sep 10:50  NIFTY 15 SEP 23600 PUT   195  ₹3,209    the PE book's trade...
    PE  PE_NoTarget 15-Sep 10:50  NIFTY 23600PE 2026-09-15 195  ₹3,235    ...printed a second time

The desk printed the whole broker account, which records no strategy, and
matched it to the books by day and side -- so a day with five PE rows was
"ambiguous" and everything was shown. The books' own saved runs DO know which
trades are theirs (mode "live", with the Dhan order id); the Scalp desk saves
Phil's hand trades separately under "scalp". So the earlier rows now come from
the books' saved runs, and the broker is used only for the charges it books
overnight, matched to one contract at one minute.
"""

import asyncio
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402

# ── the broker account as _account_option_history returns it (real, 10..16 Sep) ──
ACCOUNT = [
    # 16-Sep: Phil's scalp, and the Gap Carry's exit half (quantity 0)
    dict(
        date="2026-09-16",
        side="PE",
        symbol="NIFTY-Sep2026-23250-PE",
        quantity=520,
        entry_time="2026-09-16 09:28",
        exit_time="2026-09-16 09:38",
        entry_premium=176.48,
        exit_premium=179.99,
        gross_pnl=1823.25,
        charges=0.0,
        pnl=1823.25,
        costs_known=False,
    ),
    dict(
        date="2026-09-16",
        side="PE",
        symbol="NIFTY-Sep2026-23350-PE",
        quantity=0,
        entry_time="",
        exit_time="2026-09-16 09:15",
        entry_premium=0.0,
        exit_premium=202.8,
        gross_pnl=0.0,
        charges=0.0,
        pnl=0.0,
        costs_known=False,
    ),
    # 15-Sep: four scalps, the PE book's trade, the Gap Carry's entry half
    dict(
        date="2026-09-15",
        side="CE",
        symbol="NIFTY 15 SEP 23100 CALL",
        quantity=260,
        entry_time="2026-09-15T13:36",
        exit_time="2026-09-15T13:40",
        entry_premium=183.45,
        exit_premium=173.55,
        gross_pnl=-2574.0,
        charges=155.33,
        pnl=-2729.33,
        costs_known=True,
    ),
    dict(
        date="2026-09-15",
        side="PE",
        symbol="NIFTY 15 SEP 23300 PUT",
        quantity=260,
        entry_time="2026-09-15T14:48",
        exit_time="2026-09-15T14:48",
        entry_premium=141.9,
        exit_premium=146.95,
        gross_pnl=1313.0,
        charges=136.84,
        pnl=1176.16,
        costs_known=True,
    ),
    dict(
        date="2026-09-15",
        side="PE",
        symbol="NIFTY 15 SEP 23400 PUT",
        quantity=260,
        entry_time="2026-09-15T13:41",
        exit_time="2026-09-15T13:43",
        entry_premium=151.95,
        exit_premium=144.55,
        gross_pnl=-1924.0,
        charges=137.17,
        pnl=-2061.17,
        costs_known=True,
    ),
    dict(
        date="2026-09-15",
        side="PE",
        symbol="NIFTY 15 SEP 23500 PUT",
        quantity=260,
        entry_time="2026-09-15T13:11",
        exit_time="2026-09-15T13:11",
        entry_premium=208.15,
        exit_premium=212.35,
        gross_pnl=1092.0,
        charges=177.61,
        pnl=914.39,
        costs_known=True,
    ),
    dict(
        date="2026-09-15",
        side="PE",
        symbol="NIFTY 15 SEP 23600 PUT",
        quantity=195,
        entry_time="2026-09-15T10:50",
        exit_time="2026-09-15T12:32",
        entry_premium=260.75,
        exit_premium=278.1,
        gross_pnl=3383.25,
        charges=174.73,
        pnl=3208.52,
        costs_known=True,
    ),
    dict(
        date="2026-09-15",
        side="PE",
        symbol="NIFTY 22 SEP 23350 PUT",
        quantity=0,
        entry_time="2026-09-15T15:15",
        exit_time="",
        entry_premium=251.8,
        exit_premium=0.0,
        gross_pnl=0.0,
        charges=30.97,
        pnl=-30.97,
        costs_known=True,
    ),
    # 11-Sep: two scalps and the PE book's trade
    dict(
        date="2026-09-11",
        side="CE",
        symbol="NIFTY 15 SEP 23250 CALL",
        quantity=260,
        entry_time="2026-09-11T14:00",
        exit_time="2026-09-11T14:00",
        entry_premium=207.6,
        exit_premium=211.45,
        gross_pnl=1001.0,
        charges=177.08,
        pnl=823.92,
        costs_known=True,
    ),
    dict(
        date="2026-09-11",
        side="PE",
        symbol="NIFTY 15 SEP 23450 PUT",
        quantity=130,
        entry_time="2026-09-11T09:20",
        exit_time="2026-09-11T10:08",
        entry_premium=229.2,
        exit_premium=212.85,
        gross_pnl=-2125.5,
        charges=113.76,
        pnl=-2239.26,
        costs_known=True,
    ),
    # 10-Sep: the PE book's trade
    dict(
        date="2026-09-10",
        side="PE",
        symbol="NIFTY 15 SEP 23700 PUT",
        quantity=130,
        entry_time="2026-09-10T09:25",
        exit_time="2026-09-10T15:26",
        entry_premium=265.0,
        exit_premium=279.45,
        gross_pnl=1878.5,
        charges=131.96,
        pnl=1746.54,
        costs_known=True,
    ),
    # 03-Sep: the CE book's first live trade
    dict(
        date="2026-09-03",
        side="CE",
        symbol="NIFTY 08 SEP 23750 CALL",
        quantity=130,
        entry_time="2026-09-03T09:20",
        exit_time="2026-09-03T10:45",
        entry_premium=294.73,
        exit_premium=282.38,
        gross_pnl=-1605.5,
        charges=134.75,
        pnl=-1740.25,
        costs_known=True,
    ),
]


def _t(entry, exit_, symbol, qty, pin, pout, pnl, reason, order_id=None):
    return dict(
        entry_time=entry,
        exit_time=exit_,
        trading_symbol=symbol,
        quantity=qty,
        entry_premium=pin,
        exit_premium=pout,
        pnl=pnl,
        exit_reason=reason,
        entry_order_id=order_id,
    )


T_03 = _t(
    "2026-09-03 09:20:05",
    "2026-09-03 10:45:00",
    "NIFTY 23750CE 2026-09-08",
    130,
    294.73,
    282.38,
    -1723.8,
    "EXIT_SIGNAL",
    "34226090300001",
)
T_10_STALE = _t(
    "2026-09-10 09:25:09",
    "2026-09-10 19:32:04",
    "NIFTY 23700PE 2026-09-15",
    130,
    265.0,
    265.0,
    -112.94,
    "BROKER_MANUAL_EXIT",
    "34226091013711",
)
T_10 = _t(
    "2026-09-10 09:25:09",
    "2026-09-10 15:26:36",
    "NIFTY 23700PE 2026-09-15",
    130,
    265.0,
    279.45,
    1763.21,
    "BROKER_MANUAL_EXIT",
    "34226091013711",
)
T_11 = _t(
    "2026-09-11 09:20:12",
    "2026-09-11 10:08:04",
    "NIFTY 23450PE 2026-09-15",
    130,
    229.2,
    212.85,
    -2226.89,
    "MANUAL_EXIT",
    "34226091113911",
)
T_15 = _t(
    "2026-09-15 10:50:02",
    "2026-09-15 12:32:44",
    "NIFTY 23600PE 2026-09-15",
    195,
    260.75,
    278.1,
    3234.79,
    "BROKER_MANUAL_EXIT",
    "23226091529911",
)

# ── the saved runs as db.list_runs_by_mode returns them (legs empty on early saves) ──
SAVED = [
    {"id": 406, "run_name": "CE_SL15_NoMonTue", "legs": [], "trades": [T_03]},
    {"id": 418, "run_name": "PE_NoTarget", "legs": [{"option_type": "PE"}], "trades": [T_10_STALE]},
    {"id": 419, "run_name": "PE_NoTarget", "legs": [{"option_type": "PE"}], "trades": [T_10]},
    {"id": 425, "run_name": "PE_NoTarget", "legs": [{"option_type": "PE"}], "trades": [T_10, T_11, T_15]},
]


def _recent(t):
    """A trade as /api/live/runs puts it in a run's `recent` list."""
    return {
        "id": 7,
        "symbol": t["trading_symbol"],
        "entry_time": t["entry_time"],
        "exit_time": t["exit_time"],
        "entry_premium": t["entry_premium"],
        "exit_premium": t["exit_premium"],
        "quantity": t["quantity"],
        "exit_reason": t["exit_reason"],
        "pnl": t["pnl"],
    }


def _runs(pe_recent=None, ce_recent=None):
    pe = [_recent(t) for t in (pe_recent if pe_recent is not None else [T_10, T_11, T_15])]
    ce = [_recent(t) for t in (ce_recent or [])]
    return [
        {
            "name": "CE_SL15_NoMonTue",
            "side": "CE",
            "real_orders": True,
            "recent": ce,
            "booked_pnl": round(sum(r["pnl"] for r in ce), 2),
        },
        {
            "name": "PE_NoTarget",
            "side": "PE",
            "real_orders": True,
            "recent": pe,
            "booked_pnl": round(sum(r["pnl"] for r in pe), 2),
        },
    ]


def _book_trades():
    app._live_book_trades_cache.clear()

    async def fake(_uid, mode):
        assert mode == "live"
        return SAVED

    with patch.object(app._db_mod, "list_runs_by_mode", fake, create=True):
        return asyncio.run(app._live_book_trades(1))


def _desk(runs=None):
    runs = runs if runs is not None else _runs()
    return runs, app._one_row_per_trade(runs, _book_trades(), ACCOUNT)


class OnlyTheBooksOwnTradesAppear(unittest.TestCase):
    def test_no_scalp_is_listed(self):
        _runs_, rows = _desk()
        symbols = {r["symbol"] for r in rows}
        for scalp in (
            "NIFTY-Sep2026-23250-PE",
            "NIFTY 15 SEP 23100 CALL",
            "NIFTY 15 SEP 23300 PUT",
            "NIFTY 15 SEP 23400 PUT",
            "NIFTY 15 SEP 23500 PUT",
            "NIFTY 15 SEP 23250 CALL",
        ):
            self.assertNotIn(scalp, symbols)

    def test_no_gap_carry_leg_is_listed(self):
        _runs_, rows = _desk()
        self.assertFalse([r for r in rows if "23350" in str(r["symbol"])])
        self.assertFalse([r for r in rows if not r.get("quantity")])

    def test_the_15_sep_trade_is_printed_once(self):
        """It is in the engine's own list, so the saved copy is not added."""
        _runs_, rows = _desk()
        self.assertFalse([r for r in rows if r["date"] == "2026-09-15"])

    def test_what_remains_is_the_ce_books_first_trade(self):
        """The CE engine's own list is empty since its restart; its 03-Sep trade
        comes from the saved run, which recorded no legs."""
        _runs_, rows = _desk()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["book"], "CE_SL15_NoMonTue")
        self.assertEqual(rows[0]["side"], "CE")
        self.assertEqual(rows[0]["exit_reason"], "EXIT_SIGNAL")

    def test_a_restarted_pe_book_gets_its_trades_back_once_each(self):
        _runs_, rows = _desk(_runs(pe_recent=[]))
        pe = [r for r in rows if r["side"] == "PE"]
        self.assertEqual([r["date"] for r in pe], ["2026-09-15", "2026-09-11", "2026-09-10"])
        for r in pe:
            self.assertEqual(r["book"], "PE_NoTarget")

    def test_the_latest_save_of_a_trade_wins(self):
        """10-Sep was once saved at ₹-112.94 with a 19:32 exit."""
        _runs_, rows = _desk(_runs(pe_recent=[]))
        ten = [r for r in rows if r["date"] == "2026-09-10"][0]
        self.assertNotEqual(ten["exit_time"][11:16], "19:32")
        self.assertEqual(ten["pnl"], 1746.54)  # and then Dhan's settled net


class DhansMoneyWinsOnlyForTheSameContract(unittest.TestCase):
    def test_the_15_sep_book_trade_takes_its_own_account_row(self):
        runs, _rows = _desk()
        fifteen = [t for t in runs[1]["recent"] if t["entry_time"].startswith("2026-09-15")][0]
        self.assertEqual(fifteen["pnl"], 3208.52)
        self.assertTrue(fifteen["settled_by_broker"])

    def test_the_booked_total_moves_by_exactly_the_corrections(self):
        runs, _rows = _desk()
        engine = 1763.21 - 2226.89 + 3234.79
        settled = 1746.54 - 2239.26 + 3208.52
        self.assertAlmostEqual(runs[1]["booked_pnl"], round(settled, 2), places=2)
        self.assertNotAlmostEqual(runs[1]["booked_pnl"], round(engine, 2), places=2)

    def test_a_scalp_on_the_same_day_is_never_its_twin(self):
        """Five PE rows on 15-Sep: only the 23600 at 10:50, 195 qty, matches."""
        trade = dict(_recent(T_15))
        twin = app._account_twin(trade, "PE", ACCOUNT)
        self.assertEqual(twin["symbol"], "NIFTY 15 SEP 23600 PUT")

    def test_no_twin_means_the_engines_figure_stands(self):
        trade = dict(_recent(T_15), quantity=260)
        self.assertEqual(app._settle_against_account([trade], "PE", ACCOUNT), 0.0)
        self.assertEqual(trade["pnl"], 3234.79)

    def test_an_unsettled_account_row_does_not_overwrite(self):
        account = [dict(ACCOUNT[6], costs_known=False, pnl=3383.25)]
        trade = dict(_recent(T_15))
        self.assertEqual(app._settle_against_account([trade], "PE", account), 0.0)
        self.assertEqual(trade["pnl"], 3234.79)

    def test_two_candidate_rows_are_left_alone(self):
        account = [ACCOUNT[6], dict(ACCOUNT[6], entry_time="2026-09-15T10:51", pnl=1.0)]
        trade = dict(_recent(T_15))
        self.assertIsNone(app._account_twin(trade, "PE", account))

    def test_the_other_side_is_never_a_twin(self):
        self.assertIsNone(app._account_twin(dict(_recent(T_15)), "CE", ACCOUNT))


class PaperBooksNeverTouchTheBroker(unittest.TestCase):
    def test_a_paper_trade_keeps_its_own_pnl(self):
        runs = _runs()
        runs[1]["real_orders"] = False
        app._one_row_per_trade(runs, _book_trades(), ACCOUNT)
        fifteen = [t for t in runs[1]["recent"] if t["entry_time"].startswith("2026-09-15")][0]
        self.assertEqual(fifteen["pnl"], 3234.79)


class Edges(unittest.TestCase):
    def test_an_empty_desk_is_not_an_error(self):
        self.assertEqual(app._one_row_per_trade([], [], []), [])
        self.assertEqual(app._one_row_per_trade([], None, None), [])

    def test_newest_first(self):
        _runs_, rows = _desk(_runs(pe_recent=[]))
        exits = [r["exit_time"] for r in rows]
        self.assertEqual(exits, sorted(exits, reverse=True))

    def test_the_strike_is_read_from_every_spelling(self):
        for text in ("NIFTY 23600PE 2026-09-15", "NIFTY 15 SEP 23600 PUT", "NIFTY-Sep2026-23600-PE"):
            self.assertEqual(app._contract_strike(text), 23600)

    def test_a_trade_without_legs_still_knows_its_side(self):
        self.assertEqual(app._trade_option_side({"trading_symbol": "NIFTY 23750CE 2026-09-08"}), "CE")
        self.assertEqual(app._trade_option_side({"symbol": "NIFTY 15 SEP 23600 PUT"}), "PE")
        self.assertEqual(app._trade_option_side({"symbol": "unknown"}), "")


class ItIsWiredIntoTheDesk(unittest.TestCase):
    def test_live_runs_reads_the_saved_books_and_the_account(self):
        src = open(app.__file__, encoding="utf-8").read()
        body = src.split("async def live_runs(")[1].split("\n@app.")[0]
        self.assertIn(
            "_one_row_per_trade(runs, await _live_book_trades(user_id), await _account_option_history(user_id))", body
        )

    def test_only_live_runs_are_read(self):
        src = open(app.__file__, encoding="utf-8").read()
        body = src.split("async def _live_book_trades(")[1].split("\n\n\n")[0]
        self.assertIn('list_runs_by_mode(uid, "live")', body)


if __name__ == "__main__":
    unittest.main()
