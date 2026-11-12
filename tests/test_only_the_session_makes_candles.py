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
    # The last pre-open quote (09:14:30, 22714) is the settled opening price
    # and the 09:15 bar opens at it, as the broker's does -- see
    # test_first_candle_opens_at_the_pre_open_price below.
    assert df.iloc[0]["open"] == 22714.0


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


def test_first_candle_opens_at_the_pre_open_price():
    """11-Nov-2026: NIFTY's pre-open settled at 25337.8 (quoted 09:09:37) and
    Dhan's 09:15 bar opens there; the live bar opened at the first traded tick,
    25317.65, so on 12-Nov yesterday_open was 20.15 points off all morning."""
    day = datetime(2026, 11, 11)
    agg = CandleAggregator(timeframe_minutes=5, max_candles=100)
    agg.feed_tick(25290.0, ts=day.replace(hour=9, minute=3))  # indicative: not the open
    agg.feed_tick(25335.6, ts=day.replace(hour=9, minute=8, second=39))
    agg.feed_tick(25337.8, ts=day.replace(hour=9, minute=9, second=37))
    agg.feed_tick(25317.65, ts=day.replace(hour=9, minute=15, second=2))
    agg.feed_tick(25263.8, ts=day.replace(hour=9, minute=16))
    first = agg.get_current()
    assert first["open"] == 25337.8
    assert first["high"] == 25337.8
    assert first["low"] == 25263.8
    assert first["close"] == 25263.8
    # Only the day's first candle takes it.
    agg.feed_tick(25300.0, ts=day.replace(hour=9, minute=20, second=1))
    assert agg.get_current()["open"] == 25300.0


def test_yesterdays_pre_open_never_opens_today():
    agg = CandleAggregator(timeframe_minutes=5, max_candles=100)
    agg.feed_tick(25337.8, ts=datetime(2026, 11, 11, 9, 10))
    agg.feed_tick(25213.5, ts=datetime(2026, 11, 12, 9, 15, 3))
    assert agg.get_current()["open"] == 25213.5
