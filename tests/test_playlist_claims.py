"""The playlist-claims tool must count what it says it counts.

tools/playlist_claims.py turns five of his claims into numbers. If its swing
detector or its failed-candle test is wrong, every verdict built on it is
wrong too, so the two primitives are pinned here on bars small enough to
check by hand.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import playlist_claims as pc  # noqa: E402


def _bars(closes, spread=0.5):
    idx = pd.date_range("2026-01-05 09:15", periods=len(closes), freq="5min")
    return pd.DataFrame(
        {"open": closes, "high": [c + spread for c in closes], "low": [c - spread for c in closes], "close": closes},
        index=idx,
    )


def test_zigzag_finds_each_leg_and_knows_when_it_was_known():
    # 100 up to 120, down to 105, up to 125: two finished legs.
    closes = list(range(100, 121)) + list(range(119, 104, -1)) + list(range(106, 126))
    legs = pc.zigzag(_bars(closes), 0.05)
    assert [leg.up for leg in legs] == [True, False]
    up, down = legs
    assert up.end_px == 120.5 and down.end_px == 104.5
    # A swing is only known once price has come back 5% from it -- never on its own bar.
    assert up.confirmed > up.end and down.confirmed > down.end


def test_a_close_that_barely_clears_the_level_is_a_failed_candle():
    base = [100.0] * 12
    df = _bars(base + [100.0, 100.0, 100.0, 100.0], spread=1.0)
    # Bar 12 closes at 101.2 over a 101.0 high, but its range is 99.0-101.5: a fifth beyond.
    df.iloc[12] = [100.0, 101.5, 99.0, 101.2]
    r = pc.failed_candle(df)
    assert r["failed"][1] == 1 and r["full"][1] == 0
    assert r["failed"][0] == 1, "the next closes fall back under the level"


def test_violent_hours_shares_add_up_to_the_whole_session():
    idx = pd.date_range("2026-01-05 09:15", "2026-01-05 15:25", freq="5min")
    df = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.0}, index=idx)
    r = pc.violent_hours(df)
    assert r["days"] == 1
    assert abs(sum(r["share"].values()) - 1.0) < 1e-9
    # Flat bars everywhere: each window's share of movement is its share of time.
    assert abs(r["share"]["09:15-10:15"] - 12 / 75) < 1e-9
