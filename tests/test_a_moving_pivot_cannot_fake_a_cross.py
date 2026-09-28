"""A pivot that moves must not be readable as a cross.

2026-09-28: a live PE book exited on "crosses_below CPR_S3" with S3 never having
been crossed. eval_condition reads the level from BOTH bars, which is right for a
genuinely moving indicator and wrong for a pivot — a pivot is one number for the
whole session. When its value shifted between two bars the comparison flipped
while price stood still, and the LINE crossed the PRICE.

The 08-Sep (`6b7e58f`) and 15-Sep (`73fbbb7`) fixes stopped the live buffer from
sliding, which is what made the levels move. They did not stop a moving level from
faking a cross, which is what this guards.
"""

import pandas as pd

from engine.backtest import eval_condition


def _row(close, s3):
    return pd.Series({"close": close, "CPR_S3": s3})


def _cond(op="crosses_below", left="current_close", right="CPR_S3"):
    return {"left": left, "operator": op, "right": right}


def test_a_pivot_that_moves_up_past_a_still_price_is_not_a_cross():
    prev = _row(23_100.0, 23_050.0)  # price above S3
    now = _row(23_100.0, 23_150.0)  # same price, S3 has moved ABOVE it
    assert (
        eval_condition(now, _cond(), prev_row=prev) is False
    ), "price never moved — only the pivot did; this must not read as a cross"


def test_a_real_price_cross_of_a_steady_pivot_still_fires():
    prev = _row(23_100.0, 23_050.0)
    now = _row(23_040.0, 23_050.0)  # price actually fell through S3
    assert eval_condition(now, _cond(), prev_row=prev) is True


def test_a_real_cross_fires_even_if_the_pivot_wobbled():
    prev = _row(23_100.0, 23_055.0)
    now = _row(23_040.0, 23_050.0)  # price crossed; the level drifted slightly
    assert eval_condition(now, _cond(), prev_row=prev) is True


def test_crosses_above_is_guarded_the_same_way():
    prev = _row(23_100.0, 23_150.0)  # price below S3
    now = _row(23_100.0, 23_050.0)  # same price, S3 dropped BELOW it
    assert eval_condition(now, _cond(op="crosses_above"), prev_row=prev) is False


def test_a_genuinely_moving_indicator_is_left_alone():
    """An EMA is supposed to move; both bars must still use their own value."""
    prev = pd.Series({"close": 100.0, "EMA_20_5m": 101.0})  # price below EMA
    now = pd.Series({"close": 100.0, "EMA_20_5m": 99.0})  # EMA fell below price
    cond = {"left": "current_close", "operator": "crosses_above", "right": "EMA_20_5m"}
    assert eval_condition(now, cond, prev_row=prev) is True
