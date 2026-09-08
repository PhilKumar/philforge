"""
engine/indicators.py — Technical Indicators
Fixed:
  - SuperTrend now uses numpy arrays (no pandas .iloc chained assignment)
  - CPR/Yesterday handle both daily AND intraday DataFrames correctly
  - Added: SMA, MACD, Bollinger Bands, VWAP, ATR, Stochastic RSI, ADX
"""

import re
from collections import defaultdict

import numpy as np
import pandas as pd

from engine.timeframes import collect_strategy_timeframes, resample_ohlcv, resolve_strategy_timeframe


def _clean(s):
    """Replace ±Inf with NaN so they never propagate into condition evaluation."""
    if isinstance(s, pd.DataFrame):
        return s.replace([np.inf, -np.inf], np.nan)
    return s.replace([np.inf, -np.inf], np.nan)


def _assign_indicator_outputs(df: pd.DataFrame, ind_string: str, outputs: dict, primary_key: str) -> None:
    """Expose both the raw UI indicator id and per-component output columns."""
    df[ind_string] = outputs[primary_key]
    for suffix, series in outputs.items():
        df[f"{ind_string}_{suffix}"] = series


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return _clean(100 - (100 / (1 + rs)))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD indicator returning line, signal, histogram."""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame(
        {
            "macd_line": macd_line,
            "macd_signal": signal_line,
            "macd_histogram": histogram,
        },
        index=series.index,
    )


def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands: upper, middle, lower."""
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    width = (upper - lower) / middle.replace(0, np.nan) * 100
    return _clean(
        pd.DataFrame(
            {
                "bb_upper": upper,
                "bb_middle": middle,
                "bb_lower": lower,
                "bb_width": width,
            },
            index=series.index,
        )
    )


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Volume Weighted Average Price — resets daily for intraday data."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    if "volume" not in df.columns:
        return typical  # fallback if no volume
    tp_vol = typical * df["volume"]
    # Reset cumsum daily for intraday data
    if _is_intraday(df):
        groups = df.index.date
        cum_tp_vol = tp_vol.groupby(groups).cumsum()
        cum_vol = df["volume"].groupby(groups).cumsum()
    else:
        cum_tp_vol = tp_vol.cumsum()
        cum_vol = df["volume"].cumsum()
    return _clean(cum_tp_vol / cum_vol.replace(0, np.nan))


def stochastic_rsi(
    series: pd.Series, rsi_period: int = 14, stoch_period: int = 14, k_smooth: int = 3, d_smooth: int = 3
) -> pd.DataFrame:
    """Stochastic RSI."""
    rsi_val = rsi(series, rsi_period)
    min_rsi = rsi_val.rolling(window=stoch_period).min()
    max_rsi = rsi_val.rolling(window=stoch_period).max()
    denom = (max_rsi - min_rsi).replace(0, np.nan)
    stoch_k = 100 * (rsi_val - min_rsi) / denom
    stoch_k = stoch_k.rolling(window=k_smooth).mean()
    stoch_d = stoch_k.rolling(window=d_smooth).mean()
    return _clean(pd.DataFrame({"stoch_rsi_k": stoch_k, "stoch_rsi_d": stoch_d}, index=series.index))


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Average Directional Index (ADX) with +DI / -DI."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    atr_val = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    atr_safe = atr_val.replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_safe
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_safe
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx_val = dx.ewm(alpha=1.0 / period, adjust=False).mean()

    return _clean(
        pd.DataFrame(
            {
                "ADX": adx_val,
                "ADX_plus_di": plus_di,
                "ADX_minus_di": minus_di,
            },
            index=df.index,
        )
    )


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 2.7) -> pd.DataFrame:
    """Bug 1 fixed: pure numpy — no pandas .iloc chained assignment."""
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    close = df["close"].values.astype(float)
    n = len(close)

    # True Range
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))

    # ATR via RMA (Wilder's smoothing — matches TradingView)
    alpha = 1.0 / period
    atr = np.zeros(n)
    atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = alpha * tr[i] + (1 - alpha) * atr[i - 1]

    hl2 = (high + low) / 2.0
    upper_raw = hl2 + multiplier * atr
    lower_raw = hl2 - multiplier * atr

    upper = upper_raw.copy()
    lower = lower_raw.copy()
    st = np.zeros(n)
    st_dir = np.zeros(n, dtype=int)

    st[0] = lower[0]
    st_dir[0] = 1

    for i in range(1, n):
        lower[i] = lower_raw[i] if (lower_raw[i] > lower[i - 1] or close[i - 1] < lower[i - 1]) else lower[i - 1]
        upper[i] = upper_raw[i] if (upper_raw[i] < upper[i - 1] or close[i - 1] > upper[i - 1]) else upper[i - 1]

        if st[i - 1] == upper[i - 1]:
            if close[i] > upper[i]:
                st[i] = lower[i]
                st_dir[i] = 1
            else:
                st[i] = upper[i]
                st_dir[i] = -1
        else:
            if close[i] < lower[i]:
                st[i] = upper[i]
                st_dir[i] = -1
            else:
                st[i] = lower[i]
                st_dir[i] = 1

    result = df.copy()
    result["supertrend"] = st
    result["supertrend_dir"] = st_dir
    return result


def _is_intraday(df: pd.DataFrame) -> bool:
    if len(df) < 2:
        return False
    return (df.index[1] - df.index[0]).total_seconds() < 86400


# NSE cash and index hours. A daily bar built for a pivot calculation is the
# SESSION's bar and nothing else.
_SESSION_FIRST_MINUTE = "09:15"
_SESSION_LAST_MINUTE = "15:29"


def _session_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Intraday rows inside NSE hours, for aggregating a true daily bar.

    Dhan's history sometimes carries bars stamped outside the session, and one
    of them is enough to corrupt every pivot for the following day: the levels
    are built from the previous day's high, low and close, so a stray 15:45 bar
    printing above the session high moves S1, S3 and TC by the whole excess.

    Measured on the archive, a single stray bar 25 points above the high shifts
    S1, S3 and TC by 16.67 points each. And because a stray bar may be in one
    broker response and gone from the next, the levels then MOVE during the
    session -- so a rule reading "close crosses above S3" can fire because the
    LINE moved rather than the price (Phil, 2026-09-08). The live PE book exits
    on exactly those crossings.

    Session-filtering here makes the levels a property of yesterday's session
    alone. Frames that are already clean -- every backtest archive is -- are
    unchanged by it.
    """
    try:
        inside = df.between_time(_SESSION_FIRST_MINUTE, _SESSION_LAST_MINUTE)
    except (TypeError, ValueError):
        return df  # not a time index; nothing to filter
    # Never hand back an empty frame: an instrument that genuinely trades other
    # hours should keep its own bars rather than lose its pivots altogether.
    return inside if not inside.empty else df


def cpr(df: pd.DataFrame, narrow_pct: float = 0.2, moderate_pct: float = 0.5, wide_pct: float = 0.5) -> pd.DataFrame:
    """Bug 2 fixed: handles both intraday and daily DataFrames."""
    intraday = _is_intraday(df)
    daily = (
        _session_bars(df).resample("D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        if intraday
        else df.copy()
    )

    daily["pivot"] = (daily["high"] + daily["low"] + daily["close"]) / 3
    daily["bc"] = (daily["high"] + daily["low"]) / 2
    daily["tc"] = daily["pivot"] * 2 - daily["bc"]
    lower_band = daily[["bc", "tc"]].min(axis=1)
    upper_band = daily[["bc", "tc"]].max(axis=1)
    daily["bc"] = lower_band
    daily["tc"] = upper_band
    daily["cpr_range"] = daily["tc"] - daily["bc"]
    daily["cpr_width_pct"] = daily["cpr_range"] / daily["close"].replace(0, np.nan) * 100

    # Floor Pivot Support & Resistance Levels
    daily["R1"] = daily["pivot"] * 2 - daily["low"]
    daily["S1"] = daily["pivot"] * 2 - daily["high"]
    daily["R2"] = daily["pivot"] + (daily["high"] - daily["low"])
    daily["S2"] = daily["pivot"] - (daily["high"] - daily["low"])
    daily["R3"] = daily["high"] + 2 * (daily["pivot"] - daily["low"])
    daily["S3"] = daily["low"] - 2 * (daily["high"] - daily["pivot"])
    daily["R4"] = daily["R3"] + (daily["high"] - daily["low"])
    daily["S4"] = daily["S3"] - (daily["high"] - daily["low"])
    daily["R5"] = daily["R4"] + (daily["high"] - daily["low"])
    daily["S5"] = daily["S4"] - (daily["high"] - daily["low"])

    # Half-levels (midpoints between consecutive levels)
    daily["R0.5"] = (daily["pivot"] + daily["R1"]) / 2
    daily["R1.5"] = (daily["R1"] + daily["R2"]) / 2
    daily["R2.5"] = (daily["R2"] + daily["R3"]) / 2
    daily["R3.5"] = (daily["R3"] + daily["R4"]) / 2
    daily["R4.5"] = (daily["R4"] + daily["R5"]) / 2
    daily["S0.5"] = (daily["pivot"] + daily["S1"]) / 2
    daily["S1.5"] = (daily["S1"] + daily["S2"]) / 2
    daily["S2.5"] = (daily["S2"] + daily["S3"]) / 2
    daily["S3.5"] = (daily["S3"] + daily["S4"]) / 2
    daily["S4.5"] = (daily["S4"] + daily["S5"]) / 2

    daily["cpr_type"] = daily["cpr_width_pct"].apply(
        lambda w: "narrow" if w <= narrow_pct else ("moderate" if w <= moderate_pct else "wide")
    )

    pivot_cols = [
        "pivot",
        "bc",
        "tc",
        "cpr_width_pct",
        "cpr_type",
        "R0.5",
        "R1",
        "R1.5",
        "R2",
        "R2.5",
        "R3",
        "R3.5",
        "R4",
        "R4.5",
        "R5",
        "S0.5",
        "S1",
        "S1.5",
        "S2",
        "S2.5",
        "S3",
        "S3.5",
        "S4",
        "S4.5",
        "S5",
    ]
    shifted = daily[pivot_cols].shift(1)

    result = df.copy()
    if intraday:
        result = result.join(shifted.reindex(result.index, method="ffill"))
    else:
        for col in pivot_cols:
            result[col] = shifted[col].reindex(result.index, method="ffill")

    result["cpr_is_narrow"] = result["cpr_type"] == "narrow"
    return result


def cpr_timeframe(
    df: pd.DataFrame,
    timeframe: str = "D",
    narrow_pct: float = 0.2,
    moderate_pct: float = 0.5,
) -> pd.DataFrame:
    """Compute CPR + floor pivots + half-levels for 4H / Weekly / Monthly timeframes.

    Parameters
    ----------
    timeframe : str
        '4h' | '4H' → 4-hour bars
        'W' → weekly bars
        'M' → monthly bars
        'D' → daily (delegates to existing cpr())
    """
    tf = timeframe.upper()
    if tf == "D":
        return cpr(df, narrow_pct=narrow_pct, moderate_pct=moderate_pct)

    # Resample rule
    rule_map = {"4H": "4h", "W": "W", "M": "ME"}
    rule = rule_map.get(tf, tf)

    bars = df.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()

    # Standard Floor Pivot calculations
    bars["pivot"] = (bars["high"] + bars["low"] + bars["close"]) / 3
    bars["bc"] = (bars["high"] + bars["low"]) / 2
    bars["tc"] = bars["pivot"] * 2 - bars["bc"]
    lower_band = bars[["bc", "tc"]].min(axis=1)
    upper_band = bars[["bc", "tc"]].max(axis=1)
    bars["bc"] = lower_band
    bars["tc"] = upper_band
    bars["cpr_range"] = bars["tc"] - bars["bc"]
    bars["cpr_width_pct"] = bars["cpr_range"] / bars["close"].replace(0, np.nan) * 100

    bars["R1"] = bars["pivot"] * 2 - bars["low"]
    bars["S1"] = bars["pivot"] * 2 - bars["high"]
    bars["R2"] = bars["pivot"] + (bars["high"] - bars["low"])
    bars["S2"] = bars["pivot"] - (bars["high"] - bars["low"])
    bars["R3"] = bars["high"] + 2 * (bars["pivot"] - bars["low"])
    bars["S3"] = bars["low"] - 2 * (bars["high"] - bars["pivot"])
    bars["R4"] = bars["R3"] + (bars["high"] - bars["low"])
    bars["S4"] = bars["S3"] - (bars["high"] - bars["low"])
    bars["R5"] = bars["R4"] + (bars["high"] - bars["low"])
    bars["S5"] = bars["S4"] - (bars["high"] - bars["low"])

    # Half-levels
    bars["R0.5"] = (bars["pivot"] + bars["R1"]) / 2
    bars["R1.5"] = (bars["R1"] + bars["R2"]) / 2
    bars["R2.5"] = (bars["R2"] + bars["R3"]) / 2
    bars["R3.5"] = (bars["R3"] + bars["R4"]) / 2
    bars["R4.5"] = (bars["R4"] + bars["R5"]) / 2
    bars["S0.5"] = (bars["pivot"] + bars["S1"]) / 2
    bars["S1.5"] = (bars["S1"] + bars["S2"]) / 2
    bars["S2.5"] = (bars["S2"] + bars["S3"]) / 2
    bars["S3.5"] = (bars["S3"] + bars["S4"]) / 2
    bars["S4.5"] = (bars["S4"] + bars["S5"]) / 2

    bars["cpr_type"] = bars["cpr_width_pct"].apply(
        lambda w: "narrow" if w <= narrow_pct else ("moderate" if w <= moderate_pct else "wide")
    )

    pivot_cols = [
        "pivot",
        "bc",
        "tc",
        "cpr_width_pct",
        "cpr_type",
        "R0.5",
        "R1",
        "R1.5",
        "R2",
        "R2.5",
        "R3",
        "R3.5",
        "R4",
        "R4.5",
        "R5",
        "S0.5",
        "S1",
        "S1.5",
        "S2",
        "S2.5",
        "S3",
        "S3.5",
        "S4",
        "S4.5",
        "S5",
    ]
    shifted = bars[pivot_cols].shift(1)

    result = df.copy()
    result = result.join(shifted.reindex(result.index, method="ffill"), rsuffix="_tf")
    # Drop any duplicate columns from join
    for c in result.columns:
        if c.endswith("_tf"):
            result.drop(columns=[c], inplace=True)

    result["cpr_is_narrow"] = result["cpr_type"] == "narrow"
    return result


def yesterday_candle(df: pd.DataFrame) -> pd.DataFrame:
    """Bug 2 fixed: handles both intraday and daily DataFrames."""
    intraday = _is_intraday(df)
    daily = (
        df.resample("D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        if intraday
        else df.copy()
    )

    daily["yesterday_high"] = daily["high"].shift(1)
    daily["yesterday_low"] = daily["low"].shift(1)
    daily["yesterday_close"] = daily["close"].shift(1)
    daily["yesterday_open"] = daily["open"].shift(1)

    yest_cols = ["yesterday_high", "yesterday_low", "yesterday_close", "yesterday_open"]
    result = df.copy()
    if intraday:
        result = result.join(daily[yest_cols].reindex(result.index, method="ffill"))
    else:
        for col in yest_cols:
            result[col] = daily[col].reindex(result.index, method="ffill")

    return result


def orb(df: pd.DataFrame, window_minutes: int = 15, market_open_str: str = "09:15") -> pd.DataFrame:
    """Opening Range Breakout — computes ORB high/low from the first N minutes of each day."""
    intraday = _is_intraday(df)
    if not intraday:
        # For daily data, ORB doesn't apply — return empty columns
        result = df.copy()
        result["ORB_High"] = np.nan
        result["ORB_Low"] = np.nan
        result["ORB_Range"] = np.nan
        return result

    result = df.copy()
    result["ORB_High"] = np.nan
    result["ORB_Low"] = np.nan

    # Group by date, find high/low of candles within the opening window
    from datetime import time as dtime
    from datetime import timedelta

    mo_h, mo_m = map(int, market_open_str.split(":"))
    market_open = dtime(mo_h, mo_m)
    orb_end = (pd.Timestamp(f"2000-01-01 {market_open_str}") + timedelta(minutes=window_minutes)).time()

    for date, group in result.groupby(result.index.date):
        orb_mask = (group.index.time >= market_open) & (group.index.time < orb_end)
        orb_candles = group[orb_mask]
        if len(orb_candles) > 0:
            orb_high = orb_candles["high"].max()
            orb_low = orb_candles["low"].min()
            # Only fill AFTER opening range closes (prevent look-ahead)
            post_orb = (result.index.date == date) & (result.index.time >= orb_end)
            result.loc[post_orb, "ORB_High"] = orb_high
            result.loc[post_orb, "ORB_Low"] = orb_low

    result["ORB_Range"] = result["ORB_High"] - result["ORB_Low"]
    result["ORB_is_breakout_up"] = result["close"] > result["ORB_High"]
    result["ORB_is_breakout_down"] = result["close"] < result["ORB_Low"]
    result["ORB_is_inside"] = (~result["ORB_is_breakout_up"]) & (~result["ORB_is_breakout_down"])
    return result


def _extract_indicator_timeframe(ind_string: str) -> int | None:
    if not isinstance(ind_string, str) or "_" not in ind_string:
        return None
    for part in reversed(ind_string.split("_")):
        if part.endswith("m") and part[:-1].isdigit():
            return int(part[:-1])
    return None


_CONDITION_CONTEXT_FIELDS = {
    "",
    "number",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "oi",
    "current_open",
    "current_high",
    "current_low",
    "current_close",
    "current_volume",
    "time_of_day",
    "Time_Of_Day",
    "Time_HHMM",
    "Day_Of_Week",
    "Day_of_Week",
    "Day_Name",
    "Hour",
    "Minute",
    "Is_Monday",
    "Is_Tuesday",
    "Is_Wednesday",
    "Is_Thursday",
    "Is_Friday",
    "Yesterday_Open",
    "Yesterday_High",
    "Yesterday_Low",
    "Yesterday_Close",
    "Signal_Candle_Open",
    "Signal_Candle_High",
    "Signal_Candle_Low",
    "Signal_Candle_Close",
}

_DIRECT_INDICATOR_PATTERNS = (
    re.compile(r"^(EMA|SMA|RSI|ATR|VWAP)_\d+_\d+m$"),
    re.compile(r"^Supertrend_\d+_(?:\d+(?:\.\d+)?)_\d+m$"),
    re.compile(r"^MACD_\d+_\d+_\d+_\d+m$"),
    re.compile(r"^BB_\d+_(?:\d+(?:\.\d+)?)_\d+m$"),
    re.compile(r"^StochRSI_\d+_\d+m$"),
    re.compile(r"^ADX_\d+_\d+m$"),
    re.compile(r"^ORB_\d+min$"),
)

_INDICATOR_OUTPUT_PATTERNS = (
    re.compile(r"^(MACD_\d+_\d+_\d+_\d+m)_(?:signal|histogram)$"),
    re.compile(r"^(BB_\d+_(?:\d+(?:\.\d+)?)_\d+m)_(?:upper|lower|width)$"),
    re.compile(r"^(StochRSI_\d+_\d+m)_(?:K|D)$"),
    re.compile(r"^(ADX_\d+_\d+m)_(?:plus_di|minus_di)$"),
)

_CPR_LEVEL_SUFFIXES = {
    "Pivot",
    "TC",
    "BC",
    "width_pct",
    "is_narrow",
    "is_moderate",
    "is_wide",
    "R0.5",
    "R1",
    "R1.5",
    "R2",
    "R2.5",
    "R3",
    "R3.5",
    "R4",
    "R4.5",
    "R5",
    "S0.5",
    "S1",
    "S1.5",
    "S2",
    "S2.5",
    "S3",
    "S3.5",
    "S4",
    "S4.5",
    "S5",
}

_GENERIC_DAILY_CPR_FIELDS = {
    "pivot",
    "bc",
    "tc",
    "cpr_width_pct",
    "cpr_is_narrow",
    "cpr_is_moderate",
    "cpr_is_wide",
    "R0.5",
    "R1",
    "R1.5",
    "R2",
    "R2.5",
    "R3",
    "R3.5",
    "R4",
    "R4.5",
    "R5",
    "S0.5",
    "S1",
    "S1.5",
    "S2",
    "S2.5",
    "S3",
    "S3.5",
    "S4",
    "S4.5",
    "S5",
}


def _parse_cpr_indicator_timeframe(indicator_id: str) -> str | None:
    if not isinstance(indicator_id, str) or not indicator_id.startswith("CPR"):
        return None
    parts = indicator_id.split("_")
    if len(parts) > 1 and parts[1].endswith("m"):
        return "D"
    if len(parts) > 3:
        return parts[3].upper()
    return "D"


def _default_cpr_indicator_id(timeframe: str) -> str:
    tf = (timeframe or "D").upper()
    return "CPR_0.2_0.5" if tf == "D" else f"CPR_0.2_0.5_{tf}"


def _infer_cpr_indicator_id(field: str, indicators: list[str]) -> str | None:
    if not isinstance(field, str):
        return None
    if field in _GENERIC_DAILY_CPR_FIELDS:
        target_tf = "D"
    elif field.startswith("CPR_4H_"):
        target_tf = "4H"
    elif field.startswith("CPR_W_"):
        target_tf = "W"
    elif field.startswith("CPR_M_"):
        target_tf = "M"
    elif field.startswith("CPR_"):
        target_tf = "D"
    else:
        return None

    for indicator_id in indicators:
        if _parse_cpr_indicator_timeframe(indicator_id) == target_tf:
            return indicator_id
    return _default_cpr_indicator_id(target_tf)


def _infer_indicator_dependency(field: str, indicators: list[str]) -> str | None:
    if not isinstance(field, str) or field in _CONDITION_CONTEXT_FIELDS:
        return None

    cpr_indicator = _infer_cpr_indicator_id(field, indicators)
    if cpr_indicator:
        return cpr_indicator

    if field in {"ORB_High", "ORB_Low", "ORB_Range", "ORB_is_breakout_up", "ORB_is_breakout_down", "ORB_is_inside"}:
        for indicator_id in indicators:
            if isinstance(indicator_id, str) and indicator_id.startswith("ORB_"):
                return indicator_id
        return "ORB_15min"

    for pattern in _INDICATOR_OUTPUT_PATTERNS:
        match = pattern.match(field)
        if match:
            return match.group(1)

    for pattern in _DIRECT_INDICATOR_PATTERNS:
        if pattern.match(field):
            return field

    return None


def normalize_strategy_indicators(
    indicators: list[str] | tuple[str, ...] | None,
    entry_conditions: list[dict] | None = None,
    exit_conditions: list[dict] | None = None,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for indicator_id in indicators or []:
        if not isinstance(indicator_id, str) or not indicator_id:
            continue
        if indicator_id in seen:
            continue
        normalized.append(indicator_id)
        seen.add(indicator_id)

    for condition in list(entry_conditions or []) + list(exit_conditions or []):
        if not isinstance(condition, dict):
            continue
        for side in ("left", "right"):
            dependency = _infer_indicator_dependency(condition.get(side), normalized)
            if dependency and dependency not in seen:
                normalized.append(dependency)
                seen.add(dependency)

    return normalized


def collect_condition_indicator_dependencies(
    conditions: list[dict] | tuple[dict, ...] | None,
    indicators: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    dependencies: list[str] = []
    seen: set[str] = set()
    known_indicators = [indicator_id for indicator_id in indicators or [] if isinstance(indicator_id, str)]

    for condition in conditions or []:
        if not isinstance(condition, dict):
            continue
        for side in ("left", "right"):
            dependency = _infer_indicator_dependency(condition.get(side), known_indicators)
            if dependency and dependency not in seen:
                dependencies.append(dependency)
                seen.add(dependency)

    return dependencies


def infer_execution_timeframe(
    indicators: list[str] | tuple[str, ...] | None,
    entry_conditions: list[dict] | tuple[dict, ...] | None = None,
    default: int = 5,
) -> int:
    explicit_indicators = [
        indicator_id for indicator_id in indicators or [] if isinstance(indicator_id, str) and indicator_id
    ]
    entry_dependencies = collect_condition_indicator_dependencies(entry_conditions, explicit_indicators)
    entry_frames = collect_strategy_timeframes(entry_dependencies)
    if entry_frames:
        return min(entry_frames)

    explicit_frames = collect_strategy_timeframes(explicit_indicators)
    if len(explicit_frames) == 1:
        return explicit_frames[0]
    if default and int(default) > 0:
        return int(default)
    if explicit_frames:
        return min(explicit_frames)
    return 5


def merge_indicator_context(
    raw_df: pd.DataFrame,
    context_df: pd.DataFrame | None,
    *,
    max_rows: int = 800,
) -> pd.DataFrame:
    """Prepend cached raw candles when the current snapshot lacks warm-up history."""
    frame = raw_df.copy().sort_index()
    if frame.empty or context_df is None or context_df.empty:
        return frame

    if len(pd.Index(frame.index.normalize()).unique()) > 1:
        return frame

    context = context_df.copy().sort_index()
    context = context[context.index < frame.index.min()]
    if context.empty:
        return frame

    merged = pd.concat([context, frame])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    if max_rows > 0 and len(merged) > max_rows:
        merged = merged.tail(max_rows)
    return merged


def _infer_timeframe_minutes(df: pd.DataFrame) -> int | None:
    if df is None or df.empty or len(df.index) < 2:
        return None
    diffs = df.index.to_series().diff().dropna().dt.total_seconds().div(60)
    diffs = diffs[diffs > 0]
    if diffs.empty:
        return None
    return int(round(float(diffs.mode().iloc[0])))


def _attach_execution_context(df: pd.DataFrame) -> pd.DataFrame:
    result = yesterday_candle(df.copy())
    result["time_of_day"] = result.index.time
    result["Day_of_Week"] = result.index.dayofweek
    result["Day_Name"] = result.index.strftime("%A")
    result["Hour"] = result.index.hour
    result["Minute"] = result.index.minute
    result["Time_HHMM"] = result.index.strftime("%H:%M")
    result["Is_Monday"] = (result.index.dayofweek == 0).astype(float)
    result["Is_Tuesday"] = (result.index.dayofweek == 1).astype(float)
    result["Is_Wednesday"] = (result.index.dayofweek == 2).astype(float)
    result["Is_Thursday"] = (result.index.dayofweek == 3).astype(float)
    result["Is_Friday"] = (result.index.dayofweek == 4).astype(float)

    result["current_open"] = result["open"]
    result["current_high"] = result["high"]
    result["current_low"] = result["low"]
    result["current_close"] = result["close"]
    result["current_volume"] = result["volume"] if "volume" in result.columns else 0

    result["Yesterday_Open"] = result["yesterday_open"]
    result["Yesterday_High"] = result["yesterday_high"]
    result["Yesterday_Low"] = result["yesterday_low"]
    result["Yesterday_Close"] = result["yesterday_close"]
    return result


def _align_to_execution_index(
    frame_df: pd.DataFrame,
    frame_minutes: int,
    execution_index: pd.Index,
    execution_minutes: int,
) -> pd.DataFrame:
    if frame_df.empty:
        return pd.DataFrame(index=execution_index)
    if frame_minutes == execution_minutes:
        return frame_df.reindex(execution_index)

    aligned = frame_df.sort_index().copy()
    aligned.index = aligned.index + pd.to_timedelta(frame_minutes, unit="m")
    execution_close_index = execution_index + pd.to_timedelta(execution_minutes, unit="m")
    union_index = aligned.index.union(execution_close_index)
    aligned = aligned.reindex(union_index).sort_index().ffill().reindex(execution_close_index)
    aligned.index = execution_index
    return aligned


def _compute_indicator_columns(df: pd.DataFrame, ui_indicators: list, *, assign_generic: bool) -> pd.DataFrame:
    frame = df.copy()

    for ind_string in ui_indicators:
        parts = ind_string.split("_")
        name = parts[0]

        if name == "EMA":
            period = int(parts[1])
            frame[ind_string] = ema(frame["close"], period)

        elif name == "SMA":
            period = int(parts[1])
            frame[ind_string] = sma(frame["close"], period)

        elif name == "RSI":
            period = int(parts[1])
            frame[ind_string] = rsi(frame["close"], period)

        elif name == "MACD":
            fast = int(parts[1]) if len(parts) > 1 else 12
            slow = int(parts[2]) if len(parts) > 2 else 26
            sig = int(parts[3]) if len(parts) > 3 else 9
            macd_df = macd(frame["close"], fast, slow, sig)
            if assign_generic:
                frame["MACD_line"] = macd_df["macd_line"]
                frame["MACD_signal"] = macd_df["macd_signal"]
                frame["MACD_histogram"] = macd_df["macd_histogram"]
            _assign_indicator_outputs(
                frame,
                ind_string,
                {
                    "line": macd_df["macd_line"],
                    "signal": macd_df["macd_signal"],
                    "histogram": macd_df["macd_histogram"],
                },
                primary_key="line",
            )

        elif name == "BB":
            period = int(parts[1]) if len(parts) > 1 else 20
            std = float(parts[2]) if len(parts) > 2 else 2.0
            bb_df = bollinger_bands(frame["close"], period, std)
            if assign_generic:
                frame["BB_upper"] = bb_df["bb_upper"]
                frame["BB_middle"] = bb_df["bb_middle"]
                frame["BB_lower"] = bb_df["bb_lower"]
                frame["BB_width"] = bb_df["bb_width"]
            _assign_indicator_outputs(
                frame,
                ind_string,
                {
                    "upper": bb_df["bb_upper"],
                    "middle": bb_df["bb_middle"],
                    "lower": bb_df["bb_lower"],
                    "width": bb_df["bb_width"],
                },
                primary_key="middle",
            )

        elif name == "VWAP":
            if assign_generic:
                frame["VWAP"] = vwap(frame)
                frame[ind_string] = frame["VWAP"]
            else:
                frame[ind_string] = vwap(frame)

        elif name == "ATR":
            period = int(parts[1]) if len(parts) > 1 else 14
            frame[ind_string] = atr(frame, period)

        elif name == "StochRSI":
            period = int(parts[1]) if len(parts) > 1 else 14
            srsi = stochastic_rsi(frame["close"], period)
            if assign_generic:
                frame["StochRSI_K"] = srsi["stoch_rsi_k"]
                frame["StochRSI_D"] = srsi["stoch_rsi_d"]
            _assign_indicator_outputs(
                frame,
                ind_string,
                {
                    "K": srsi["stoch_rsi_k"],
                    "D": srsi["stoch_rsi_d"],
                },
                primary_key="K",
            )

        elif name == "ADX":
            period = int(parts[1]) if len(parts) > 1 else 14
            adx_df = adx(frame, period)
            if assign_generic:
                frame["ADX"] = adx_df["ADX"]
                frame["ADX_plus_di"] = adx_df["ADX_plus_di"]
                frame["ADX_minus_di"] = adx_df["ADX_minus_di"]
            frame[ind_string] = adx_df["ADX"]
            frame[f"{ind_string}_plus_di"] = adx_df["ADX_plus_di"]
            frame[f"{ind_string}_minus_di"] = adx_df["ADX_minus_di"]

        elif name == "Supertrend":
            period = int(parts[1])
            multiplier = float(parts[2])
            st_df = supertrend(frame, period=period, multiplier=multiplier)
            if assign_generic:
                frame["supertrend_dir"] = st_df["supertrend_dir"]
            frame[ind_string] = st_df["supertrend"]

        elif name == "CPR":
            # Supported formats:
            #   CPR_0.2_0.5       -> daily CPR with explicit thresholds
            #   CPR_0.2_0.5_W     -> weekly CPR with explicit thresholds
            #   CPR_5m            -> legacy builder id, use default thresholds on execution frame
            if len(parts) > 1 and parts[1].endswith("m"):
                narrow_pct = 0.2
                moderate_pct = 0.5
                tf = "D"
            else:
                narrow_pct = float(parts[1]) if len(parts) > 1 else 0.2
                moderate_pct = float(parts[2]) if len(parts) > 2 else 0.5
                tf = parts[3] if len(parts) > 3 else "D"
            tf_upper = tf.upper()
            tf_prefix = f"CPR_{tf_upper}_" if tf_upper != "D" else "CPR_"

            if tf_upper in ("4H", "W", "M", "ME"):
                frame = cpr_timeframe(frame, timeframe=tf_upper, narrow_pct=narrow_pct, moderate_pct=moderate_pct)
            else:
                frame = cpr(frame, narrow_pct=narrow_pct, moderate_pct=moderate_pct, wide_pct=moderate_pct)

            frame[f"{tf_prefix}Pivot"] = frame["pivot"]
            frame[f"{tf_prefix}TC"] = frame["tc"]
            frame[f"{tf_prefix}BC"] = frame["bc"]
            frame[f"{tf_prefix}width_pct"] = frame["cpr_width_pct"]
            frame[f"{tf_prefix}is_narrow"] = frame["cpr_type"] == "narrow"
            frame[f"{tf_prefix}is_moderate"] = frame["cpr_type"] == "moderate"
            frame[f"{tf_prefix}is_wide"] = frame["cpr_type"] == "wide"
            for lvl in [
                "R0.5",
                "R1",
                "R1.5",
                "R2",
                "R2.5",
                "R3",
                "R3.5",
                "R4",
                "R4.5",
                "R5",
                "S0.5",
                "S1",
                "S1.5",
                "S2",
                "S2.5",
                "S3",
                "S3.5",
                "S4",
                "S4.5",
                "S5",
            ]:
                frame[f"{tf_prefix}{lvl}"] = frame[lvl]
            if tf_upper == "D":
                frame["CPR_Pivot"] = frame["pivot"]
                frame["CPR_TC"] = frame["tc"]
                frame["CPR_BC"] = frame["bc"]
            frame[ind_string] = frame["pivot"]

        elif name == "ORB":
            window_str = parts[1] if len(parts) > 1 else "15min"
            window_minutes = int(window_str.replace("min", ""))
            frame = orb(frame, window_minutes=window_minutes)
            frame["ORB_Breakout_Up"] = frame["ORB_is_breakout_up"]
            frame["ORB_Breakout_Down"] = frame["ORB_is_breakout_down"]
            frame["ORB_Inside"] = frame["ORB_is_inside"]

        elif name in ("Current", "Previous"):
            pass

        elif name == "Signal":
            pass

    return frame


def compute_dynamic_indicators(
    df: pd.DataFrame,
    ui_indicators: list,
    default_timeframe_minutes: int = 5,
    source_timeframe_minutes: int | None = None,
    execution_timeframe_minutes: int | None = None,
) -> pd.DataFrame:
    """
    Compute indicators across one or more strategy timeframes and align them to the
    execution timeframe using last-closed-candle semantics.
    """
    if df is None or df.empty:
        return df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()

    raw_df = df.copy().sort_index()
    source_tf = source_timeframe_minutes or _infer_timeframe_minutes(raw_df) or default_timeframe_minutes
    execution_hint = int(execution_timeframe_minutes or 0) or None
    tf_spec = resolve_strategy_timeframe(
        ui_indicators, default=default_timeframe_minutes, execution_hint=execution_hint
    )
    execution_tf = tf_spec.requested

    if _is_intraday(raw_df) and execution_tf != source_tf:
        execution_df = resample_ohlcv(
            raw_df,
            execution_tf,
            source_timeframe_minutes=source_tf,
            drop_incomplete=True,
        )
    else:
        execution_df = raw_df.copy()

    result = _attach_execution_context(execution_df)

    grouped_indicators: dict[int | None, list[str]] = defaultdict(list)
    for ind_string in ui_indicators or []:
        grouped_indicators[_extract_indicator_timeframe(ind_string)].append(ind_string)

    execution_group = grouped_indicators.pop(execution_tf, [])
    execution_group.extend(grouped_indicators.pop(None, []))
    if execution_group:
        execution_with_indicators = _compute_indicator_columns(result.copy(), execution_group, assign_generic=True)
        result = execution_with_indicators.copy()

    for frame_tf, indicators in grouped_indicators.items():
        if frame_tf is None:
            continue
        if _is_intraday(raw_df) and frame_tf != source_tf:
            frame_df = resample_ohlcv(
                raw_df,
                frame_tf,
                source_timeframe_minutes=source_tf,
                drop_incomplete=True,
            )
        else:
            frame_df = raw_df.copy()

        if frame_df.empty:
            continue

        base_columns = set(frame_df.columns)
        frame_with_indicators = _compute_indicator_columns(frame_df, indicators, assign_generic=False)
        added_columns = [col for col in frame_with_indicators.columns if col not in base_columns]
        if not added_columns:
            continue

        aligned = _align_to_execution_index(
            frame_with_indicators[added_columns],
            frame_tf,
            result.index,
            execution_tf,
        )
        overwrite_cols = [column for column in aligned.columns if column in result.columns]
        if overwrite_cols:
            result = result.drop(columns=overwrite_cols)
        result = pd.concat([result, aligned], axis=1)

    return result
