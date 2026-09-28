"""A level built on a session we never saw whole must not produce a signal.

THE CHAIN, end to end (28-Sep-2026):
  1. 2026-03-04 (9e878d3b) made paper and live SHARE one candle aggregator, and
     the second book to register had its `history_df` silently discarded.
  2. With no history, SessionBook never sees the previous session whole, so it
     never pins it.
  3. An unpinned session kept its partially-resampled high and low, so every
     pivot built on it MOVED as the websocket filled the gap in.
  4. A moving level read from both bars is indistinguishable from a cross, so a
     live PE book exited on "crosses_below CPR_S3" with S3 never crossed.
  5. The same missing history is why EMA_20_5m did not carry across the session
     break and raised divergence alerts.

One root cause, several symptoms. This test covers the last link: a level whose
session was never seen whole is NaN, so no condition can fire on it, and the book
waits instead of trading a guess.
"""

import pandas as pd

from engine.indicators import SessionBook, cpr, pinned_sessions


def _session(day: str, first="09:15", last="15:25", high=100.0, low=90.0):
    idx = pd.date_range(f"{day} {first}", f"{day} {last}", freq="5min")
    return pd.DataFrame(
        {"open": 95.0, "high": high, "low": low, "close": 96.0, "volume": 1},
        index=idx,
    )


def test_a_session_seen_whole_is_pinned_and_gives_levels():
    book = SessionBook()
    df = pd.concat([_session("2026-09-25"), _session("2026-09-28")])
    with pinned_sessions(book):
        out = cpr(df)
    assert book.is_pinned(pd.Timestamp("2026-09-25").date())
    assert out["S3"].notna().any(), "a whole session must produce usable levels"


def test_a_partial_previous_session_produces_NO_level():
    """Yesterday starts at 12:00 — the engine never saw its morning."""
    book = SessionBook()
    partial = _session("2026-09-25", first="12:00", last="15:25", high=100.0, low=95.0)
    df = pd.concat([partial, _session("2026-09-28")])
    with pinned_sessions(book):
        out = cpr(df)
    assert not book.is_pinned(pd.Timestamp("2026-09-25").date())
    today = out.index.max().date()
    todays = out[out.index.map(lambda t: t.date() == today)]
    assert todays["S3"].isna().all(), (
        "S3 came from a session never seen whole — it is a guess and must be NaN, " "so no condition can fire on it"
    )


def test_the_level_cannot_move_once_the_session_is_pinned():
    """The partial view arrives first, the whole view second: levels settle once."""
    book = SessionBook()
    partial = _session("2026-09-25", first="12:00", high=100.0, low=95.0)
    whole = _session("2026-09-25", high=100.0, low=90.0)

    with pinned_sessions(book):
        cpr(pd.concat([partial, _session("2026-09-28")]))
        first = cpr(pd.concat([whole, _session("2026-09-28")]))["S3"].dropna().iloc[-1]
        # a thinner view arriving later must not move it back
        second = cpr(pd.concat([partial, _session("2026-09-28")]))["S3"].dropna().iloc[-1]
    assert first == second, "a level that settles must never move again"
