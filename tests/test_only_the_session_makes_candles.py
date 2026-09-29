"""A tick outside 09:15-15:30 must never become a candle.

On 2026-09-29 the live engine held 66 bars of "today" at 09:15:04 — before the
market opened. Dhan's websocket streams from the moment it connects (pre-open
indicative values, the previous close repeated), and every tick became a candle.
Dhan's historical candles are session-only, so the engine's EMA_20_5m and
Supertrend were dragged by flat pre-open bars every morning: wrong at the open,
converging by late morning — the recurring divergence alerts (29-Sep: -25.5 on
EMA and +29.6 on Supertrend at the 09:20 candle).
"""

from datetime import datetime

from engine.market_feed import CandleAggregator, in_nse_session


def _ts(h, m, s=0):
    return datetime(2026, 9, 29, h, m, s)


def test_pre_open_ticks_make_no_candle():
    agg = CandleAggregator(timeframe_minutes=1, max_candles=100)
    for minute in range(0, 15):  # 09:00 .. 09:14
        agg.feed_tick(22700.0 + minute, ts=_ts(9, minute, 30))
    agg.feed_tick(22750.0, ts=_ts(9, 15, 5))  # first real tick
    agg.feed_tick(22760.0, ts=_ts(9, 16, 5))  # closes the 09:15 candle
    df = agg.to_dataframe()
    assert len(df) == 1, f"expected only the 09:15 candle, got {list(df.index)}"
    assert df.index[0] == _ts(9, 15)
    assert df.iloc[0]["open"] == 22750.0, "the 09:15 open must be the first SESSION tick"


def test_post_close_ticks_make_no_candle():
    agg = CandleAggregator(timeframe_minutes=1, max_candles=100)
    agg.feed_tick(22800.0, ts=_ts(15, 29, 10))
    agg.feed_tick(22801.0, ts=_ts(15, 31, 0))  # after the close: ignored
    agg.feed_tick(22802.0, ts=_ts(15, 40, 0))
    assert agg.get_current()["timestamp"] == _ts(15, 29)
    assert agg.get_current()["close"] == 22800.0


def test_bars_already_stored_out_of_session_are_dropped():
    """A buffer seeded before the filter must not leak pre-open bars either."""
    agg = CandleAggregator(timeframe_minutes=1, max_candles=100)
    agg.candles = [
        {"timestamp": _ts(9, 10), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0},
        {"timestamp": _ts(9, 15), "open": 2, "high": 2, "low": 2, "close": 2, "volume": 0},
    ]
    df = agg.to_dataframe()
    assert list(df.index) == [_ts(9, 15)]


def test_the_session_window_edges():
    assert not in_nse_session(_ts(9, 14, 59))
    assert in_nse_session(_ts(9, 15, 0))
    assert in_nse_session(_ts(15, 29, 59))
    assert not in_nse_session(_ts(15, 30, 0))
