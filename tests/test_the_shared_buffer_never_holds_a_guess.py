"""The three 28-Sep fixes, re-checked — and the holes they left, closed.

9a54201 let a second book merge its history into the shared NIFTY buffer, but:
  - a history fetched mid-candle carries the bar that is still forming. Seeded
    into the buffer, the live bar for that slot was appended beside it: two
    candles with ONE timestamp, counted twice by every EMA and every level;
  - the merge swapped the candle list from the event loop while the websocket
    and candle-closer threads were appending to it, with no lock.

aec7fdf held session levels still inside a cross, but only recognised R1-R3 /
S1-S3 on the RIGHT of a rule: S4, R1.5, S3.5 ... and a level written on the
left still let a moving line fake a cross, and "touches" was not covered at all.
"""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from engine.backtest import eval_condition
from engine.market_feed import CandleAggregator, LiveMarketFeed


def _frame(start, periods, tf=5, tz=None):
    idx = pd.date_range(start, periods=periods, freq=f"{tf}min", tz=tz)
    return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10}, index=idx)


def _rows(df):
    return LiveMarketFeed._rows_from_history(df)


@pytest.fixture()
def feed():
    f = LiveMarketFeed.__new__(LiveMarketFeed)  # no broker, no sockets
    f._aggregators = {}
    f._index_sec_ids = {26000: "NIFTY"}
    f.INDEX_MAP = {"26000": (26000, "NIFTY")}
    f.start_candle_closer = lambda: None
    return f


# ── the buffer ────────────────────────────────────────────────────────────────


def test_a_bar_that_has_not_finished_is_never_seeded():
    agg = CandleAggregator(timeframe_minutes=5)
    now = datetime(2026, 9, 28, 10, 2)
    agg.seed(_rows(_frame("2026-09-28 09:15", 10)), now=now)  # 09:15 .. 10:00
    assert agg.candles[-1]["timestamp"] == pd.Timestamp("2026-09-28 09:55"), "10:00 is still forming at 10:02"


def test_the_slot_the_websocket_is_building_is_never_seeded():
    agg = CandleAggregator(timeframe_minutes=5)
    agg.feed_tick(100.0, ts=datetime(2026, 9, 28, 10, 0, 5))
    agg.seed(_rows(_frame("2026-09-28 09:15", 10)), now=datetime(2026, 9, 28, 10, 30))
    assert all(c["timestamp"] < pd.Timestamp("2026-09-28 10:00") for c in agg.candles)


def test_closing_a_slot_the_buffer_already_holds_replaces_it_not_duplicates_it():
    agg = CandleAggregator(timeframe_minutes=5)
    agg.candles = [{"timestamp": datetime(2026, 9, 28, 10, 0), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}]
    agg.feed_tick(200.0, ts=datetime(2026, 9, 28, 10, 0, 5))
    agg.close_due_candles(now=datetime(2026, 9, 28, 10, 5))
    df = agg.to_dataframe()
    assert not df.index.duplicated().any()
    assert float(df["close"].iloc[-1]) == 200.0, "the live bar wins"


def test_a_second_book_with_a_forming_bar_leaves_no_duplicate(feed):
    """The fallback history fetch keeps the forming bar; seeding it must not
    leave two rows for that slot once the live bar closes."""
    feed.set_candle_config("26000", 5, lambda *a: None, _frame("2026-09-25 09:15", 3))
    agg = feed._aggregators["NIFTY_5m"]
    agg.feed_tick(150.0, ts=datetime.now().replace(microsecond=0))
    forming = agg._current_slot
    history = _frame(str(forming - timedelta(minutes=5 * 20)), 21)  # ends ON the forming slot
    feed.set_candle_config("26000", 5, lambda *a: None, history)
    agg.close_due_candles(now=forming + timedelta(minutes=5))
    assert not agg.to_dataframe().index.duplicated().any()


def test_a_timezone_aware_history_lands_on_the_naive_ist_clock():
    agg = CandleAggregator(timeframe_minutes=5)
    agg.seed(_rows(_frame("2026-09-25 09:15", 3, tz="Asia/Kolkata")), now=datetime(2026, 9, 28, 9, 0))
    assert agg.candles[0]["timestamp"] == pd.Timestamp("2026-09-25 09:15")
    assert agg.candles[0]["timestamp"].tzinfo is None


def test_the_buffer_keeps_its_own_bar_on_a_clash():
    agg = CandleAggregator(timeframe_minutes=5)
    ts = pd.Timestamp("2026-09-25 09:15")
    agg.candles = [{"timestamp": ts, "open": 7, "high": 7, "low": 7, "close": 7.0, "volume": 0}]
    agg.seed(_rows(_frame("2026-09-25 09:15", 3)), now=datetime(2026, 9, 28, 9, 0))
    assert agg.candles[0]["close"] == 7.0
    assert len(agg.candles) == 3


# ── the levels ────────────────────────────────────────────────────────────────


def _cross(level, op="crosses_below", level_on_left=False):
    if level_on_left:
        flipped = "crosses_above" if op == "crosses_below" else "crosses_below"
        return {"left": level, "operator": flipped, "right": "current_close"}
    return {"left": "current_close", "operator": op, "right": level}


@pytest.mark.parametrize("level", ["S4", "S5", "R1.5", "S3.5", "R0.5", "pivot", "tc", "bc", "Yesterday_Low", "CPR_W_S3"])
def test_every_pivot_level_holds_still(level):
    prev = pd.Series({"close": 23_100.0, level: 23_050.0})
    now = pd.Series({"close": 23_100.0, level: 23_150.0})  # the line moved, price did not
    assert eval_condition(now, _cross(level), prev_row=prev) is False, level


def test_a_level_written_on_the_left_holds_still_too():
    prev = pd.Series({"close": 23_100.0, "CPR_S3": 23_050.0})
    now = pd.Series({"close": 23_100.0, "CPR_S3": 23_150.0})
    assert eval_condition(now, _cross("CPR_S3", level_on_left=True), prev_row=prev) is False


def test_a_real_cross_with_the_level_on_the_left_still_fires():
    prev = pd.Series({"close": 23_100.0, "CPR_S3": 23_050.0})
    now = pd.Series({"close": 23_040.0, "CPR_S3": 23_050.0})
    assert eval_condition(now, _cross("CPR_S3", level_on_left=True), prev_row=prev) is True


def test_a_moving_pivot_cannot_fake_a_touch():
    """No high/low on the row, so touches falls back to 'did the sign flip'."""
    prev = pd.Series({"close": 23_100.0, "CPR_S3": 23_050.0})
    now = pd.Series({"close": 23_100.0, "CPR_S3": 23_150.0})
    cond = {"left": "close", "operator": "touches", "right": "CPR_S3"}
    assert eval_condition(now, cond, prev_row=prev) is False
