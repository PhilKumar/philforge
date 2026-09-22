"""Exact historical premium-target option selection for generic backtests.

The strategy builder permits ``premium_above``, ``premium_below`` and
``premium_near`` legs. A rolling option series cannot represent those rules,
because every entry may choose a different fixed strike. This adapter resolves
the strike from Upstox expired-contract minute history and returns the selected
fixed contract's OHLC data to the existing backtest engine.
"""

from __future__ import annotations

import json
import weakref
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

import pandas as pd

from data.cascade_upstox import NIFTY_INDEX_KEY, UpstoxPremiumSource
from engine.strike_utils import round_to_nearest_step

PREMIUM_STRIKE_TYPES = {"premium_near", "premium_above", "premium_below"}

# Which Upstox underlying and strike grid a builder instrument prices on.
# NIFTY first, and alone, for a year; SENSEX added 22-Sep-2026 when Phil asked
# for the CE/PE books to be tested on it. Upstox holds SENSEX weekly expiries
# from 04-Oct-2024, the same span as NIFTY, with the lot on each contract.
_UNDERLYINGS = {
    "26000": (NIFTY_INDEX_KEY, 50),
    "NIFTY": (NIFTY_INDEX_KEY, 50),
    "1": ("BSE_INDEX|SENSEX", 100),
    "SENSEX": ("BSE_INDEX|SENSEX", 100),
}


@dataclass(frozen=True)
class HistoricalOptionSelection:
    history_key: str
    history: pd.DataFrame
    strike: int
    expiry: date
    entry_price: float


class UpstoxHistoricalPremiumSelector:
    """Resolve premium-target NIFTY or SENSEX option legs with exact historical OHLC."""

    def __init__(
        self,
        instrument: str,
        *,
        cache_only: bool = False,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        spec = _UNDERLYINGS.get(str(instrument or "26000").upper())
        if spec is None:
            raise ValueError("Upstox premium-target backtests support NIFTY 50 and SENSEX only.")
        underlying_key, self.strike_step = spec
        self.source = UpstoxPremiumSource(
            underlying_key=underlying_key,
            cache_only=cache_only,
            backfill_missing=not cache_only,
        )
        self.expiries = sorted(self.source.available_expiries())
        # The engine owns a selected frame only while its trade is open. A weak
        # cache lets concurrent legs reuse that frame without pinning every
        # contract selected during the current expiry in web-worker RAM.
        self._frames: weakref.WeakValueDictionary[tuple[str, date, int], pd.DataFrame] = weakref.WeakValueDictionary()
        # Per underlying, so SENSEX and NIFTY choices never share a file. NIFTY's
        # folder IS the cache root, so its existing file is the one it reads.
        meta_dir = getattr(self.source, "_meta_dir", None) or self.source._cache_dir
        self._selection_cache_path = meta_dir / "premium_target_selections_v1.json"
        self._selection_cache = self._load_selection_cache()
        self.selection_cache_hits = 0
        self.selection_cache_misses = 0
        self.last_gap = ""
        self._progress = progress
        self._active_expiry: date | None = None

    def _load_selection_cache(self) -> dict[str, dict]:
        """Load resolved strikes from earlier identical historical replays.

        The minute-candle cache keeps every contract response.  This small
        companion cache prevents a repeat backtest from reopening and
        resampling a whole ATM-to-ITM strike ladder merely to rediscover the
        same contract at the same signal candle.
        """
        try:
            raw = json.loads(self._selection_cache_path.read_text())
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write_selection_cache(self) -> None:
        tmp_path = self._selection_cache_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(self._selection_cache, separators=(",", ":"), sort_keys=True))
            tmp_path.replace(self._selection_cache_path)
        except OSError:
            # The raw candle cache remains the source of truth.  A failure to
            # record this optional speed-up must never affect backtest prices.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _selection_cache_key(
        entry_time: datetime,
        atm: int,
        expiry: date,
        option_type: str,
        strike_type: str,
        target: float,
        timeframe: int,
    ) -> str:
        return "|".join(
            (
                "v1",
                entry_time.isoformat(timespec="minutes"),
                str(atm),
                expiry.isoformat(),
                option_type,
                strike_type,
                f"{target:.8f}",
                str(int(timeframe)),
            )
        )

    @staticmethod
    def _expiry_for(expiries: list[date], on: date, selection: str) -> date | None:
        future = [value for value in expiries if value >= on]
        if not future:
            return None
        selection = str(selection or "current_week").lower()
        if selection == "next_week":
            return future[1] if len(future) > 1 else None
        if selection in {"current_month", "next_month"}:
            months = sorted({(value.year, value.month) for value in future})
            offset = 1 if selection == "next_month" else 0
            if len(months) <= offset:
                return None
            wanted = months[offset]
            return max(value for value in future if (value.year, value.month) == wanted)
        # A missing weekly expiry must be a gap. Without this guard, asking for
        # a date before Upstox coverage silently selects the oldest available
        # contract even when it expires many weeks later.
        return future[0] if (future[0] - on).days <= 7 else None

    def _activate_expiry(self, expiry: date) -> None:
        if self._active_expiry == expiry:
            return
        self._frames.clear()
        release = getattr(self.source, "release_memory", None)
        if callable(release):
            release()
        self._active_expiry = expiry

    def _frame(self, instrument_key: str, expiry: date, strike: int, timeframe: int) -> pd.DataFrame:
        cache_key = (instrument_key, expiry, int(timeframe))
        cached = self._frames.get(cache_key)
        if cached is not None:
            return cached
        if self._progress is not None:
            self._progress(
                f"Resolving real Upstox options — expiry {expiry.isoformat()}, "
                f"strike {strike:,} · {self.source.requests_made} request(s)"
            )
        series = self.source._minute_series(instrument_key, expiry)
        try:
            if not series:
                return pd.DataFrame()
            rows = [
                {"timestamp": stamp, "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close}
                for stamp, bar in series.items()
            ]
        finally:
            release = getattr(self.source, "release_series", None)
            if callable(release):
                release(instrument_key)
        frame = pd.DataFrame(rows).set_index("timestamp").sort_index()
        frame = (
            frame.resample(f"{timeframe}min", label="left", closed="left", origin="start_day", offset="15min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna(subset=["open"])
        )
        del rows, series
        trim = getattr(self.source, "trim_memory", None)
        if callable(trim):
            trim()
        self._frames[cache_key] = frame
        return frame

    def _entry_price(self, instrument_key: str, expiry: date, entry_time: datetime) -> float | None:
        """Read one candidate's entry minute without retaining its full life.

        Premium-near can inspect 31 strikes. Building and retaining a full
        DataFrame for every candidate is unnecessary: selection needs only the
        entry-minute open. The chosen contract is loaded once afterwards for
        exit simulation.
        """
        series = self.source._minute_series(instrument_key, expiry)
        try:
            bar = series.get(entry_time)
            return float(bar.open) if bar is not None else None
        finally:
            release = getattr(self.source, "release_series", None)
            if callable(release):
                release(instrument_key)

    def select(self, entry_time: datetime, entry_spot: float, leg: dict, timeframe_minutes: int):
        """Return the exact selected fixed contract, or None with ``last_gap``."""
        self.last_gap = ""
        option_type = str(leg.get("option_type") or "CE").upper()
        strike_type = str(leg.get("strike_type") or "").lower()
        target = float(leg.get("strike_value") or 0)
        if option_type not in {"CE", "PE"} or strike_type not in PREMIUM_STRIKE_TYPES or target <= 0:
            self.last_gap = "invalid premium-target leg"
            return None
        expiry = self._expiry_for(self.expiries, entry_time.date(), leg.get("expiry") or "current_week")
        if expiry is None:
            self.last_gap = "no eligible Upstox expiry within the requested weekly window"
            return None
        self._activate_expiry(expiry)
        contracts = self.source._contract_index(expiry)
        atm = round_to_nearest_step(entry_spot, self.strike_step)
        cache_key = self._selection_cache_key(
            entry_time, atm, expiry, option_type, strike_type, target, int(timeframe_minutes)
        )
        cached_selection = self._selection_cache.get(cache_key)
        if cached_selection:
            instrument_key = str(cached_selection.get("instrument_key") or "")
            try:
                strike = int(cached_selection.get("strike") or 0)
            except (TypeError, ValueError):
                strike = 0
            if instrument_key and strike and contracts.get((strike, option_type)) == instrument_key:
                frame = self._frame(instrument_key, expiry, strike, int(timeframe_minutes))
                if not frame.empty and entry_time in frame.index:
                    self.selection_cache_hits += 1
                    return HistoricalOptionSelection(
                        f"upstox|{instrument_key}|{expiry.isoformat()}|{strike}|{option_type}",
                        frame,
                        strike,
                        expiry,
                        float(frame.loc[entry_time, "open"]),
                    )
            # Do not retain stale entries after an archive/cache migration.
            self._selection_cache.pop(cache_key, None)
        self.selection_cache_misses += 1
        if strike_type == "premium_above":
            offsets = range(0, -16, -1) if option_type == "CE" else range(0, 16)
        elif strike_type == "premium_below":
            offsets = range(0, 16) if option_type == "CE" else range(0, -16, -1)
        else:
            offsets = range(-15, 16)
        candidates = []
        for offset in offsets:
            strike = atm + offset * self.strike_step
            instrument_key = contracts.get((strike, option_type))
            if not instrument_key:
                continue
            price = self._entry_price(instrument_key, expiry, entry_time)
            if price is None:
                continue
            candidate = (price, strike, instrument_key)
            candidates.append(candidate)
            if (strike_type == "premium_above" and price >= target) or (
                strike_type == "premium_below" and price <= target
            ):
                frame = self._frame(instrument_key, expiry, strike, int(timeframe_minutes))
                if frame.empty or entry_time not in frame.index:
                    continue
                self._selection_cache[cache_key] = {"instrument_key": instrument_key, "strike": strike}
                self._write_selection_cache()
                return HistoricalOptionSelection(
                    f"upstox|{instrument_key}|{expiry.isoformat()}|{strike}|{option_type}",
                    frame,
                    strike,
                    expiry,
                    price,
                )
        if not candidates:
            self.last_gap = f"no complete Upstox {option_type} candle series at {entry_time:%Y-%m-%d %H:%M}"
            return None
        price, strike, instrument_key = min(candidates, key=lambda item: abs(item[0] - target))
        frame = self._frame(instrument_key, expiry, strike, int(timeframe_minutes))
        if frame.empty or entry_time not in frame.index:
            self.last_gap = f"selected Upstox {option_type} candle series unavailable at {entry_time:%Y-%m-%d %H:%M}"
            return None
        self._selection_cache[cache_key] = {"instrument_key": instrument_key, "strike": strike}
        self._write_selection_cache()
        return HistoricalOptionSelection(
            f"upstox|{instrument_key}|{expiry.isoformat()}|{strike}|{option_type}", frame, strike, expiry, price
        )

    def cache_summary(self) -> dict[str, int | str]:
        summary: dict[str, int | str] = {
            "selection_hits": self.selection_cache_hits,
            "selection_misses": self.selection_cache_misses,
            "stored_selections": len(self._selection_cache),
            "candle_requests": self.source.requests_made,
        }
        if self.expiries:
            summary["first_expiry"] = self.expiries[0].isoformat()
            summary["last_expiry"] = self.expiries[-1].isoformat()
        return summary
