"""Every live buffer must hold whole sessions, on any timeframe.

Live on 2026-09-28 the count of bars before today slid 354 -> 353 -> ... -> 329
through the morning, under the 375 an NSE session needs. The candle aggregator
and the indicator context were both capped at a flat 500/800 candles, chosen when
the live clock was 5-minute. On the ONE-MINUTE clock 500 candles is 1.3 sessions,
so as today filled up, yesterday fell off the back.

A previous session that is not whole is what makes CPR levels move, and a moving
level can be read as a cross — which is how a live PE book exited on
"crosses_below CPR_S3" with S3 never crossed.
"""

from engine.indicators import LIVE_CONTEXT_ROWS
from engine.market_feed import NSE_SESSION_MINUTES, session_buffer_size


def test_every_timeframe_holds_at_least_three_whole_past_sessions():
    for tf in (1, 2, 3, 5, 10, 15, 30, 60):
        size = session_buffer_size(tf)
        sessions = size * tf / NSE_SESSION_MINUTES
        assert sessions >= 4.0, f"{tf}m holds only {sessions:.1f} sessions ({size} candles)"


def test_the_one_minute_clock_is_the_case_that_broke():
    """500 candles on 1m is 1.3 sessions — the live failure. It must be 4+."""
    assert session_buffer_size(1) >= 4 * NSE_SESSION_MINUTES
    assert 500 * 1 / NSE_SESSION_MINUTES < 1.4  # what it used to be


def test_the_indicator_context_also_covers_four_sessions_of_one_minute():
    assert LIVE_CONTEXT_ROWS >= 4 * NSE_SESSION_MINUTES


def test_coarser_clocks_keep_their_old_depth():
    """A 5m or 60m book needs depth for long indicator windows, not for sessions."""
    assert session_buffer_size(5) >= 500
    assert session_buffer_size(60) >= 500
