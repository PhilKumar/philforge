"""Serve PhilForge's premium-target selector out of the Dhan parquet stores.

PhilForge's backtest resolves a `premium_near 250` leg through
`_upstox_premium_selector` -- a config key, not a vendor lock. Upstox only
reaches back to Sep 2024, so judging a strategy on it is judging it on the
recent period. The Dhan archive is five and a half years of strike-keyed minute
bars (ts, strike, side, OHLC, spot) -- so it can answer the same question over
a much longer window, once someone routes a contract to the store that holds
it.

Dhan sells MONEYNESS: its stores are keyed by *which* expiry was nearest, not
by the expiry itself. The bridge is the calendar, the same one
`options.dhan_listed` uses -- e1 is the nearest weekly, e2 the second, m1/m2
the monthlies. A contract further out than the stores reach is reported
missing rather than served from the wrong instrument.
"""

from __future__ import annotations

import os
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import pandas as pd

from options.dhan_listed import index_levels, monthly_expiries


@dataclass(frozen=True)
class HistoricalOptionSelection:
    """The five fields engine/backtest.py reads off a resolution.

    Declared here rather than imported from data.backtest_upstox, which pulls
    the whole Upstox chain in for five fields this selector does not need.
    """

    history_key: str
    history: pd.DataFrame
    strike: int
    expiry: date
    entry_price: float


PREMIUM_STRIKE_TYPES = {"premium_near", "premium_above", "premium_below"}
SPOT_TOLERANCE = 0.05


class DhanHistoricalPremiumSelector:
    """`select()` with the signature PhilForge's backtest already calls."""

    def __init__(self, instrument: str, stores: dict, weeklies: list, underlying: str = "NIFTY") -> None:
        if str(instrument or "26000") not in {"26000", "NIFTY"}:
            raise ValueError("The Dhan archive here carries NIFTY only.")
        self.stores = {k: v for k, v in stores.items() if os.path.isdir(v)}
        self.weeklies = sorted(weeklies)
        self.monthlies = monthly_expiries(self.weeklies)
        self.expiries = list(self.weeklies)
        self.underlying = underlying
        self.levels = index_levels(underlying)
        self.last_gap = ""
        self._months: dict = {}
        self._order: list = []
        self.gaps = {"no_store": 0, "no_month": 0, "no_bar": 0, "no_candidate": 0}
        self.served = 0

    # -- calendar ---------------------------------------------------------
    @staticmethod
    def _nth_after(days: list, day: date, n: int) -> Optional[date]:
        i = bisect_left(days, day) + n - 1
        return days[i] if 0 <= i < len(days) else None

    def _store_for(self, day: date, expiry: date) -> Optional[str]:
        if expiry == self._nth_after(self.weeklies, day, 1):
            return "e1"
        if expiry == self._nth_after(self.weeklies, day, 2):
            return "e2"
        if expiry == self._nth_after(self.monthlies, day, 1):
            return "m1"
        if expiry == self._nth_after(self.monthlies, day, 2):
            return "m2"
        return None

    def _expiry_for(self, day: date, rule: str) -> Optional[date]:
        rule = str(rule or "current_week").lower()
        if rule in ("current_week", "weekly", "near_week"):
            return self._nth_after(self.weeklies, day, 1)
        if rule in ("next_week", "second_week"):
            return self._nth_after(self.weeklies, day, 2)
        if rule in ("current_month", "monthly"):
            return self._nth_after(self.monthlies, day, 1)
        if rule in ("next_month",):
            return self._nth_after(self.monthlies, day, 2)
        return self._nth_after(self.weeklies, day, 1)

    # -- data -------------------------------------------------------------
    def _month(self, which: str, key: str) -> pd.DataFrame:
        """A month of one store, full OHLC, with the wrong-underlying rows cut.

        Dhan's expiryCode=2 series bleeds other underlyings in; a contract from
        a different instrument priced as this one is the worst kind of silent
        error, so rows whose spot is nowhere near the index that day go.
        """
        cache_key = (which, key)
        if cache_key in self._months:
            return self._months[cache_key]
        path = os.path.join(self.stores.get(which, ""), f"{self.underlying}_{key}.parquet")
        if not self.stores.get(which) or not os.path.exists(path):
            frame = pd.DataFrame()
        else:
            frame = pd.read_parquet(path, columns=["ts", "strike", "side", "open", "high", "low", "close", "spot"])
            if len(frame):
                ref = frame["ts"].dt.strftime("%Y-%m-%d").map(self.levels)
                keep = ref.isna() | frame["spot"].isna() | ((frame["spot"] - ref).abs() / ref <= SPOT_TOLERANCE)
                frame = frame[keep]
            frame = frame.set_index("ts").sort_index()
        self._months[cache_key] = frame
        self._order.append(cache_key)
        while len(self._order) > 3:  # three months is enough for any weekly
            self._months.pop(self._order.pop(0), None)
        return frame

    # -- the interface ----------------------------------------------------
    def select(self, entry_time: datetime, entry_spot: float, leg: dict, timeframe_minutes: int):
        self.last_gap = ""
        option_type = str(leg.get("option_type") or "CE").upper()
        strike_type = str(leg.get("strike_type") or "").lower()
        target = float(leg.get("strike_value") or 0)
        if option_type not in {"CE", "PE"} or strike_type not in PREMIUM_STRIKE_TYPES or target <= 0:
            self.last_gap = "invalid premium-target leg"
            return None

        stamp = entry_time.replace(second=0, microsecond=0, tzinfo=None)
        expiry = self._expiry_for(stamp.date(), leg.get("expiry") or "current_week")
        if expiry is None:
            self.last_gap = "no Dhan expiry in the calendar for that day"
            return None
        which = self._store_for(stamp.date(), expiry)
        if which is None or which not in self.stores:
            self.gaps["no_store"] += 1
            self.last_gap = f"no Dhan store holds the {expiry} contract on {stamp.date()}"
            return None

        frame = self._month(which, f"{stamp:%Y-%m}")
        if frame.empty:
            self.gaps["no_month"] += 1
            self.last_gap = f"Dhan store {which} has no {stamp:%Y-%m} file"
            return None

        side = frame[frame["side"].str.upper() == option_type]
        if stamp not in side.index:
            self.gaps["no_bar"] += 1
            self.last_gap = f"no Dhan {option_type} bar at {stamp:%Y-%m-%d %H:%M}"
            return None

        at_entry = side.loc[[stamp]]
        at_entry = at_entry[at_entry["open"] > 0]
        if at_entry.empty:
            self.gaps["no_candidate"] += 1
            self.last_gap = f"no priced Dhan {option_type} strike at {stamp:%Y-%m-%d %H:%M}"
            return None

        # POSITIONAL, never .loc. `at_entry` holds one row per STRIKE at a
        # single timestamp, so the index is full of duplicates -- and
        # `.loc[idxmin()]` looks up by that timestamp and hands back every
        # strike at it. `int(row["strike"])` on the resulting frame raises
        # "cannot convert the series to int", which is what a five-year CE
        # replay died on. `premium_near` below was always positional and so
        # never hit it, which is why only the premium_above/below strike types
        # were affected.
        if strike_type == "premium_above":
            valid = at_entry[at_entry["open"] >= target]
            row = (
                valid.iloc[int(valid["open"].values.argmin())]
                if len(valid)
                else at_entry.iloc[(at_entry["open"] - target).abs().argsort()[:1]].iloc[0]
            )
        elif strike_type == "premium_below":
            valid = at_entry[at_entry["open"] <= target]
            row = (
                valid.iloc[int(valid["open"].values.argmax())]
                if len(valid)
                else at_entry.iloc[(at_entry["open"] - target).abs().argsort()[:1]].iloc[0]
            )
        else:
            row = at_entry.iloc[(at_entry["open"] - target).abs().argsort()[:1]].iloc[0]

        strike = int(row["strike"])
        entry_price = float(row["open"])

        history = side[side["strike"] == strike].loc[stamp:]
        history = history[["open", "high", "low", "close"]].copy()
        history["strike"] = strike
        history = history[~history.index.duplicated(keep="last")]
        if int(timeframe_minutes or 1) > 1:
            rule = f"{int(timeframe_minutes)}min"
            history = (
                history.resample(rule, origin=pd.Timestamp("2021-01-01 09:15"))
                .agg({"open": "first", "high": "max", "low": "min", "close": "last", "strike": "last"})
                .dropna(subset=["open"])
            )
        if history.empty:
            self.gaps["no_bar"] += 1
            self.last_gap = f"no Dhan series for {strike}{option_type} from {stamp:%Y-%m-%d %H:%M}"
            return None

        self.served += 1
        return HistoricalOptionSelection(
            f"dhan|{which}|{expiry.isoformat()}|{strike}|{option_type}", history, strike, expiry, entry_price
        )

    def select_strike(
        self, entry_time: datetime, strike: float, expiry: date, option_type: str, timeframe_minutes: int
    ):
        """One named contract -- a spread's sold leg, N points from the bought one.

        Same store routing and the same series shape as `select`; the strike
        is given rather than found by premium. None (with last_gap) when the
        archive has no priced bar for it at the entry minute.
        """
        self.last_gap = ""
        option_type = str(option_type or "CE").upper()
        stamp = entry_time.replace(second=0, microsecond=0, tzinfo=None)
        which = self._store_for(stamp.date(), expiry)
        if which is None or which not in self.stores:
            self.gaps["no_store"] += 1
            self.last_gap = f"no Dhan store holds the {expiry} contract on {stamp.date()}"
            return None
        frame = self._month(which, f"{stamp:%Y-%m}")
        side = (
            frame[(frame["side"].str.upper() == option_type) & (frame["strike"] == int(strike))]
            if len(frame)
            else frame
        )
        if side.empty or stamp not in side.index:
            self.gaps["no_bar"] += 1
            self.last_gap = f"no Dhan {int(strike)}{option_type} bar at {stamp:%Y-%m-%d %H:%M}"
            return None
        at_entry = side.loc[[stamp]]
        at_entry = at_entry[at_entry["open"] > 0]
        if at_entry.empty:
            self.gaps["no_candidate"] += 1
            self.last_gap = f"no priced Dhan {int(strike)}{option_type} at {stamp:%Y-%m-%d %H:%M}"
            return None
        entry_price = float(at_entry.iloc[-1]["open"])
        history = side.loc[stamp:][["open", "high", "low", "close"]].copy()
        history["strike"] = int(strike)
        history = history[~history.index.duplicated(keep="last")]
        if int(timeframe_minutes or 1) > 1:
            rule = f"{int(timeframe_minutes)}min"
            history = (
                history.resample(rule, origin=pd.Timestamp("2021-01-01 09:15"))
                .agg({"open": "first", "high": "max", "low": "min", "close": "last", "strike": "last"})
                .dropna(subset=["open"])
            )
        self.served += 1
        return HistoricalOptionSelection(
            f"dhan|{which}|{expiry.isoformat()}|{int(strike)}|{option_type}", history, int(strike), expiry, entry_price
        )

    def report(self) -> str:
        return f"served={self.served} gaps={self.gaps}"
