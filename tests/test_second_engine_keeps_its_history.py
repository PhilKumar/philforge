"""The SECOND engine to register must not lose its history.

Live PE and paper PE both run NIFTY 5m, so they share the aggregator key
"NIFTY_5m". set_candle_config used to `return` as soon as the key existed,
which threw away the second caller's `history_df`. One engine then had a seeded
buffer and correct CPR/EMA and the other computed indicators from whatever had
accumulated since startup — paper right, live wrong, on the same strategy, and
the same root cause as the EMA_20_5m divergence alerts.

Introduced 2026-03-04 in 9e878d3b "Multi-strategy live monitoring".
"""

import pandas as pd
import pytest

from engine.market_feed import LiveMarketFeed


def _frame(start: str, periods: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq="5min")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10},
        index=idx,
    )


@pytest.fixture()
def feed():
    f = LiveMarketFeed.__new__(LiveMarketFeed)  # no broker, no sockets
    f._aggregators = {}
    f._index_sec_ids = {26000: "NIFTY"}
    f.INDEX_MAP = {"26000": (26000, "NIFTY")}
    f.start_candle_closer = lambda: None
    return f


def test_the_second_registration_keeps_the_deeper_history(feed):
    """Paper registers with 20 bars, live registers with 200 that start earlier."""
    feed.set_candle_config("26000", 5, lambda *a: None, _frame("2026-09-25 14:00", 20))
    assert len(feed._aggregators["NIFTY_5m"].candles) == 20

    deeper = _frame("2026-09-25 09:15", 200)
    feed.set_candle_config("26000", 5, lambda *a: None, deeper)

    agg = feed._aggregators["NIFTY_5m"]
    assert agg.candles[0]["timestamp"] == deeper.index[0], (
        "the second engine's earlier history was discarded — CPR and any EMA that "
        "must carry across the session break will both be wrong for that engine"
    )
    assert len(agg.candles) >= 200


def test_a_thinner_history_never_replaces_a_fuller_one(feed):
    feed.set_candle_config("26000", 5, lambda *a: None, _frame("2026-09-25 09:15", 200))
    feed.set_candle_config("26000", 5, lambda *a: None, _frame("2026-09-25 14:00", 20))

    agg = feed._aggregators["NIFTY_5m"]
    assert len(agg.candles) >= 200
    assert agg.candles[0]["timestamp"] == pd.Timestamp("2026-09-25 09:15")


def test_both_callbacks_still_fire(feed):
    feed.set_candle_config("26000", 5, lambda *a: None, _frame("2026-09-25 09:15", 50))
    feed.set_candle_config("26000", 5, lambda *a: None, _frame("2026-09-25 09:15", 50))
    assert len(feed._aggregators["NIFTY_5m"].on_candle_close) == 2
