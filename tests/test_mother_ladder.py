"""His ladder, as Phil trades it, must follow his rules exactly.

tools/mother_ladder.py is the backtest Phil's own answers defined (28-Sep-2026):
mother candle, the fund formula, exit at 0.25. These pin the three rules on
bars small enough to check by hand, so a later edit cannot quietly change what
"his ladder" means.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import mother_ladder as ml  # noqa: E402


def _df(rows):
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def test_the_fund_formula_sizes_the_book_by_the_percent_fallen():
    b = ml.Book(pd.Timestamp("2026-01-01"), 100.0, [90.0], capital=1000.0)
    b.buy(pd.Timestamp("2026-01-02"), 90.0)
    # capital / 100 per 1% of distance: 10% under the mother = 10% of capital
    assert round(b.cost, 6) == 100.0


def test_the_book_never_spends_past_the_trading_ceiling():
    b = ml.Book(pd.Timestamp("2026-01-01"), 100.0, [20.0], capital=1000.0)
    b.buy(pd.Timestamp("2026-01-02"), 20.0)  # an 80% fall
    assert b.cost == 1000.0 * ml.TRADING_CEILING


def test_the_exit_is_a_quarter_of_the_way_back_to_the_mother():
    b = ml.Book(pd.Timestamp("2026-01-01"), 100.0, [80.0], capital=1000.0)
    b.buy(pd.Timestamp("2026-01-02"), 80.0)
    assert b.target(0.25) == 85.0  # the playbook's own example: mother 100, average 80 -> 85


def test_a_mother_is_chosen_without_looking_ahead():
    # Rising to a high on day 3, then falling. With lookback 2 the mother must be
    # the day-3 candle, decided on day 3 -- and buys only come after, below its low.
    rows = [
        (100, 101, 99, 100),
        (100, 103, 99, 102),
        (102, 110, 104, 108),  # new high: the mother, low 104, range 6
        (108, 108, 103, 104),  # trades under the visible low 104 -> first buy
        (104, 107, 103.5, 106),
        (106, 112, 105, 111),
    ]
    books = ml.run(_df(rows), lookback=2, capital=1000.0, split=False)
    assert books, "a campaign must start"
    first = books[0]
    assert first.mother_high == 110
    assert first.fills[0][0] == pd.Timestamp("2026-01-04")
    assert first.closed is not None and first.pnl > 0


def test_the_final_system_waits_for_the_buyer_then_buys_30_then_70():
    rows = [
        (100, 101, 99, 100),
        (100, 103, 99, 102),
        (102, 110, 104, 108),  # mother, high 110
        (108, 108, 100, 101),  # the seller brings it to 100
        (101, 104, 100.5, 103),  # the buyer takes it to 104: 4 of a 10 fall, involvement
        (103, 103, 99.5, 100),  # back to the seller's low: the 30% fills at 100
        (100, 112, 100, 111),  # recovers through the 0.25 target
    ]
    books = ml.run_3070(_df(rows), lookback=2, capital=1000.0)
    assert len(books) == 1
    b = books[0]
    assert [round(f[1], 2) for f in b.fills] == [100.0], "only the 30% at the seller's low"
    assert b.fills[0][0] == pd.Timestamp("2026-01-06"), "not before the buyer showed up"
    assert b.closed is not None and b.pnl > 0
