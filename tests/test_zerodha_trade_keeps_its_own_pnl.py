"""A trade a book took at Zerodha is never shown Dhan's profit.

Phil, 13-Oct-2026: "The completed PE on zerodha trade is showing wrong." On
12-Oct the PE book ran at both brokers. Both bought NIFTY 22650 PE, 130 qty,
at 11:00:

    Dhan      in 245.05  out 289.15 (11:42)  net  5,599.61
    Zerodha   in 244.45  out 275.80 (11:35)  net  3,945.08 at Kite, 3,962.56 by the engine

The desk settles an engine's estimate against the broker account's own row
once that row carries real charges -- and the account rows are Dhan's. Matched
on side, strike, quantity and entry minute, Dhan's row was "the" twin of the
Zerodha trade too, so the Zerodha line read 244.45 -> 275.80 with a profit of
5,599.61: Zerodha's prices beside Dhan's money.
"""

import os
import unittest

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402

ACCOUNT = [
    dict(
        date="2026-10-12",
        side="PE",
        symbol="NIFTY 13 OCT 22650 PUT",
        quantity=130,
        entry_time="2026-10-12T11:00",
        exit_time="2026-10-12T11:42",
        entry_premium=245.05,
        exit_premium=289.15,
        gross_pnl=5733.0,
        charges=133.39,
        pnl=5599.61,
        costs_known=True,
        from_account=True,
    )
]


def _trade(entry, exit_, pnl, **more):
    return dict(
        id=1,
        trading_symbol="NIFTY 22650PE 2026-10-13",
        symbol="NIFTY 22650PE 2026-10-13",
        entry_time="2026-10-12 11:00:00",
        exit_time="2026-10-12 11:35:43",
        entry_premium=entry,
        exit_premium=exit_,
        quantity=130,
        pnl=pnl,
        exit_reason="BROKER_MANUAL_EXIT",
        **more,
    )


def at_zerodha(**more):
    return _trade(244.45, 275.80, 3962.56, **more)


def at_dhan(**more):
    return _trade(245.05, 289.15, 5617.83, **more)


PE_BOOK = {"run_name": "PE_NoTarget_Zerodha", "legs": [{"option_type": "PE"}]}


class TheTwinPaidTheSamePrice(unittest.TestCase):
    def test_dhans_row_is_the_dhan_trades_twin_and_not_the_zerodha_ones(self):
        self.assertIs(app._account_twin(at_dhan(), "PE", ACCOUNT), ACCOUNT[0])
        self.assertIsNone(app._account_twin(at_zerodha(), "PE", ACCOUNT))

    def test_a_row_with_no_price_still_pairs_as_it_always_did(self):
        row = {**ACCOUNT[0], "entry_premium": None}
        self.assertIs(app._account_twin(at_zerodha(), "PE", [row]), row)


class AZerodhaTradeIsNotSettledFromDhan(unittest.TestCase):
    def test_the_dhan_trade_takes_dhans_real_figure(self):
        trade = at_dhan()
        moved = app._settle_against_account([trade], "PE", ACCOUNT, "dhan")
        self.assertEqual((trade["pnl"], trade["charges"], moved), (5599.61, 133.39, -18.22))

    def test_the_zerodha_trade_keeps_its_own(self):
        trade = at_zerodha()
        self.assertEqual(app._settle_against_account([trade], "PE", ACCOUNT, "zerodha"), 0.0)
        self.assertEqual(trade["pnl"], 3962.56)
        self.assertNotIn("settled_by_broker", trade)

    def test_even_when_both_brokers_filled_at_the_same_price(self):
        by_book = _trade(245.05, 275.80, 3884.0)
        by_stamp = _trade(245.05, 275.80, 3884.0, broker="zerodha")
        self.assertEqual(app._settle_against_account([by_book], "PE", ACCOUNT, "zerodha"), 0.0)
        self.assertEqual(app._settle_against_account([by_stamp], "PE", ACCOUNT), 0.0)
        self.assertEqual((by_book["pnl"], by_stamp["pnl"]), (3884.0, 3884.0))

    def test_a_trade_that_names_its_broker_is_believed_over_its_book(self):
        # A book moved from Zerodha to Dhan keeps its Zerodha history as Zerodha's.
        trade = _trade(245.05, 275.80, 3884.0, broker="zerodha")
        app._settle_against_account([trade], "PE", ACCOUNT, "dhan")
        self.assertEqual(trade["pnl"], 3884.0)


class ThePagesAgree(unittest.TestCase):
    def test_the_live_page_shows_the_zerodha_books_own_profit(self):
        status = {
            "strategy": PE_BOOK,
            "deploy_config": {"broker": "zerodha"},
            "closed_trades": [at_zerodha()],
            "total_pnl": 3962.56,
        }
        out = app._with_account_history(status, ACCOUNT, [])
        self.assertEqual(out["closed_trades"][0]["pnl"], 3962.56)
        self.assertEqual(out["total_pnl"], 3962.56)

    def test_the_dhan_book_on_the_same_page_is_still_settled(self):
        status = {
            "strategy": {**PE_BOOK, "run_name": "PE_NoTarget"},
            "deploy_config": {},
            "closed_trades": [at_dhan()],
            "total_pnl": 5617.83,
        }
        out = app._with_account_history(status, ACCOUNT, [])
        self.assertEqual(out["closed_trades"][0]["pnl"], 5599.61)
        self.assertEqual(out["total_pnl"], 5599.61)

    def test_the_desk_corrects_the_dhan_book_and_leaves_the_zerodha_one(self):
        def run(name, broker, trade):
            return {
                "name": name,
                "real_orders": True,
                "side": "PE",
                "broker": broker,
                "recent": [trade],
                "booked_pnl": trade["pnl"],
            }

        runs = [run("PE_NoTarget", "dhan", at_dhan()), run("PE_NoTarget_Zerodha", "zerodha", at_zerodha())]
        app._one_row_per_trade(runs, [], ACCOUNT)
        self.assertEqual((runs[0]["recent"][0]["pnl"], runs[0]["booked_pnl"]), (5599.61, 5599.61))
        self.assertEqual((runs[1]["recent"][0]["pnl"], runs[1]["booked_pnl"]), (3962.56, 3962.56))

    def test_a_saved_zerodha_trade_from_before_the_stamp_takes_its_books_broker(self):
        runs = [{"name": "PE_NoTarget_Zerodha", "real_orders": True, "side": "PE", "broker": "zerodha", "recent": []}]
        saved = [{**_trade(245.05, 275.80, 3884.0), "book_name": "PE_NoTarget_Zerodha", "book_side": "PE"}]
        rows = app._one_row_per_trade(runs, saved, ACCOUNT)
        self.assertEqual((rows[0]["pnl"], rows[0]["broker"]), (3884.0, "zerodha"))


if __name__ == "__main__":
    unittest.main()
