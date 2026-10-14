"""Supertrend on the hourly chart, expressed in NIFTY calls.

The rule, as measured and published in the Supertrend tearsheet
(docs/assets/supertrend-tearsheet.html, built from the eight-way-validated
CryptoForge sweep):

    On the 1-hour chart a supertrend (ATR 10, multiplier 1.5) sits below price
    while NIFTY trends up. The first hourly close back above it buys ONE NIFTY
    call at the money, on the NEXT week's expiry -- never this week's. The
    position is HELD OVERNIGHT. When price sits six strikes in the money the
    contract is ROLLED: sold, and a fresh at-the-money call bought the same
    minute. Once the index has run 100 points in the trade's favour a TRAIL
    arms; give back 80 points from the best level since entry and the trade is
    over. Otherwise it ends when the line flips bearish, or at expiry, 15:20.
    No stop loss. Puts lose under the mirror rule and are not traded.

WHY EACH OF THOSE IS THERE, since every one of them was measured against the
alternative and the alternatives are in the tearsheet:

  next-week expiry   a third of all entries land within a day of expiry on the
                     nearest weekly, and every one of those buckets lost.
  held overnight     squaring off daily loses at all 70 settings measured --
                     the overnight gaps are where a trend actually pays.
  the roll           it banks the gain at a real quoted price instead of
                     letting the strike drift out of reach, and it is what a
                     desk does for liquidity anyway.
  the trail, loose   every stop loss and every tight trail LOST money and RAISED
                     drawdown, because a stopped trade re-enters the same
                     still-bullish trend and pays another round trip.

This module is the rule and nothing else: no broker, no database, no clock of
its own. Candles, spot, premiums, expiries, lot sizes and charges all arrive as
callbacks, exactly as engine/gap_carry.py takes them, so the backtest route,
the paper loop and the tests all drive the same code.

The supertrend itself is NOT reimplemented here. It comes from
engine.indicators.supertrend -- the same function the Builder and the live
engine already use, and the same algorithm the published book was measured on.
Computing it twice is how a paper run quietly stops matching its own tearsheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Callable, Optional
from zoneinfo import ZoneInfo

CE, PE = "CE", "PE"

# Measured on 1h. The faster frames are in the tearsheet's "what was NOT chosen"
# section: 1m loses Rs 10-66 lakh at every multiplier, and 3m/5m lose on the
# trades that carry a real market quote at both ends. 30m is the one neighbour
# that also finished green, so it is offered and nothing faster is.
TIMEFRAMES = ("1h", "30m")

IST = ZoneInfo("Asia/Kolkata")


def _ist(value: datetime) -> datetime:
    """Stamp a naive wall-clock datetime as IST; leave an aware one alone."""
    return value if value.tzinfo is not None else value.replace(tzinfo=IST)


SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


class SupertrendError(ValueError):
    """A setting this strategy cannot run with."""


@dataclass(frozen=True)
class SupertrendConfig:
    """Everything the rule needs, and nothing it does not.

    The defaults ARE the published configuration. Changing one of them makes a
    run that the tearsheet does not describe, which is why the panel labels the
    fold "rule as measured" and why every field is echoed back in the status.
    """

    timeframe: str = "1h"
    atr_period: int = 10
    multiplier: float = 1.5
    side: str = CE
    strike_step: int = 50
    lots: int = 1
    # 2 = the week after the nearest weekly. 1 is the nearest, and it is the
    # setting that put a third of all entries within a day of expiry.
    expiry_rank: int = 2
    # Strikes in the money at which the contract is rolled to a fresh ATM one.
    roll_strikes: int = 6
    # The trail stays disarmed until the index has run this far in favour...
    trail_arm_points: float = 100.0
    # ...and then exits on this much given back from the best level since entry.
    trail_give_points: float = 80.0
    entry_after: time = time(9, 20)
    square_off: time = time(15, 20)

    def validate(self) -> None:
        if self.timeframe not in TIMEFRAMES:
            raise SupertrendError(f"timeframe must be one of {', '.join(TIMEFRAMES)}.")
        if self.atr_period < 2 or self.atr_period > 50:
            raise SupertrendError("ATR period must be between 2 and 50.")
        if self.multiplier <= 0 or self.multiplier > 10:
            raise SupertrendError("multiplier must be above 0 and at most 10.")
        if self.side not in (CE, PE):
            raise SupertrendError("side must be CE or PE.")
        if self.side == PE:
            # Not a preference. On the five-year book every put variant measured
            # -- every multiplier, every timeframe, target or trail -- leaves the
            # priced-only column negative. Sixty-plus variants, no exception.
            raise SupertrendError(
                "The put book loses on trades with a real quote at both ends. This strategy is calls only."
            )
        if self.lots < 1 or self.lots > 20:
            raise SupertrendError("lots must be between 1 and 20.")
        if self.expiry_rank < 1 or self.expiry_rank > 3:
            raise SupertrendError("expiry rank must be 1, 2 or 3.")
        if self.roll_strikes < 0 or self.roll_strikes > 20:
            raise SupertrendError("roll distance must be between 0 and 20 strikes.")
        if self.trail_arm_points < 0 or self.trail_give_points < 0:
            raise SupertrendError("trail distances cannot be negative.")
        if self.strike_step <= 0:
            raise SupertrendError("strike step must be positive.")
        if self.entry_after >= self.square_off:
            raise SupertrendError("entry cutoff must fall before the square-off time.")

    @property
    def rolls_enabled(self) -> bool:
        return self.roll_strikes > 0

    @property
    def trail_enabled(self) -> bool:
        return self.trail_give_points > 0

    def as_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "atr_period": self.atr_period,
            "multiplier": self.multiplier,
            "side": self.side,
            "strike_step": self.strike_step,
            "lots": self.lots,
            "expiry_rank": self.expiry_rank,
            "roll_strikes": self.roll_strikes,
            "trail_arm_points": self.trail_arm_points,
            "trail_give_points": self.trail_give_points,
            "entry_after": self.entry_after.strftime("%H:%M"),
            "square_off": self.square_off.strftime("%H:%M"),
        }


@dataclass(frozen=True)
class SignalReading:
    """What the closed bar said, and whether it can be acted on."""

    timestamp: datetime
    close: float
    supertrend: float
    direction: int
    flipped: bool
    reason: str

    @property
    def fires(self) -> bool:
        return self.direction > 0

    def as_dict(self) -> dict:
        return {
            "timestamp": _ist(self.timestamp).isoformat(),
            "close": round(float(self.close), 2),
            "supertrend": round(float(self.supertrend), 2),
            "direction": int(self.direction),
            "flipped": bool(self.flipped),
            "reason": self.reason,
            "fires": self.fires,
        }


# ── the line ─────────────────────────────────────────────────────────
def _frame(candles: list):
    """Candles -> the DataFrame engine.indicators.supertrend expects.

    Imported inside the call because pandas is heavy and every caller here is
    already off the request thread.
    """
    import pandas as pd

    if not candles:
        return None
    rows = {
        "high": [float(getattr(c, "high", 0.0)) for c in candles],
        "low": [float(getattr(c, "low", 0.0)) for c in candles],
        "close": [float(getattr(c, "close", 0.0)) for c in candles],
    }
    index = [_ist(getattr(c, "timestamp")) for c in candles]
    return pd.DataFrame(rows, index=index)


def direction_series(candles: list, config: SupertrendConfig) -> list:
    """(timestamp, close, supertrend, direction) for every candle given.

    One computation, used by read_signal, by the chart and by the replay, so a
    picture can never disagree with the book drawn beside it.
    """
    from engine.indicators import supertrend as _supertrend

    frame = _frame(candles)
    if frame is None or len(frame) < 2:
        return []
    out = _supertrend(frame, period=int(config.atr_period), multiplier=float(config.multiplier))
    return [
        (index.to_pydatetime(), float(row["close"]), float(row["supertrend"]), int(row["supertrend_dir"]))
        for index, row in out.iterrows()
    ]


def indicator_series(candles: list, config: SupertrendConfig) -> dict:
    """The chart payload's indicator block: the line itself, and the flips."""
    series = direction_series(candles, config)
    # `d` is the trend at that bar (1 up, -1 down): the chart colours and
    # shades each run by it, as TradingView draws its Supertrend.
    line = [{"t": int(_ist(ts).timestamp()), "v": round(st, 2), "d": 1 if d > 0 else -1} for ts, _c, st, d in series]
    flips = []
    previous = None
    for ts, close, _st, direction in series:
        if previous is not None and direction != previous:
            flips.append(
                {
                    "t": int(_ist(ts).timestamp()),
                    "v": round(close, 2),
                    "dir": int(direction),
                }
            )
        previous = direction
    return {
        "supertrend": line,
        "flips": flips,
        "atr_period": int(config.atr_period),
        "multiplier": float(config.multiplier),
    }


def read_signal(candles: list, config: SupertrendConfig, *, at: Optional[datetime] = None) -> Optional[SignalReading]:
    """The last CLOSED bar's reading.

    ``at`` exists so a replay can ask what the rule said at a past moment. Bars
    are used exactly as handed over: the caller is responsible for passing only
    closed ones, because a bar still forming knows its own future and that is
    the single most expensive mistake available here.
    """
    if not candles:
        return None
    rows = [c for c in candles if getattr(c, "timestamp", None) is not None]
    if at is not None:
        limit = _ist(at)
        rows = [c for c in rows if _ist(c.timestamp) <= limit]
    if len(rows) < max(int(config.atr_period) + 2, 4):
        return None
    series = direction_series(rows, config)
    if not series:
        return None
    ts, close, line, direction = series[-1]
    previous = series[-2][3] if len(series) > 1 else direction
    flipped = direction != previous
    if direction > 0:
        reason = "supertrend flipped bullish" if flipped else "supertrend is bullish"
    else:
        reason = "supertrend flipped bearish" if flipped else "supertrend is bearish"
    return SignalReading(
        timestamp=ts,
        close=close,
        supertrend=line,
        direction=direction,
        flipped=flipped,
        reason=reason,
    )


def strike_for(spot: float, config: SupertrendConfig) -> int:
    """At the money, rounded to the strike step. No offset: buying in the money
    cut the net almost in half, because the roll already does theta's job."""
    step = int(config.strike_step)
    return int(round(float(spot) / step) * step)


# ── a position ───────────────────────────────────────────────────────
@dataclass
class SupertrendPosition:
    """One contract, from the bar that bought it to the reason it ended."""

    side: str
    strike: int
    expiry: date
    lot_size: int
    lots: int
    entry_timestamp: datetime
    entry_spot: float
    entry_premium: float
    signal: Optional[SignalReading] = None
    # The best the INDEX has been since entry, counted only on closed bars, so
    # the trail can never see an extreme and sell it inside the same candle.
    mfe: float = 0.0
    rolled_from: Optional[int] = None
    exit_timestamp: Optional[datetime] = None
    exit_spot: float = 0.0
    exit_premium: float = 0.0
    exit_reason: str = ""
    exit_priced: bool = True
    charges: float = 0.0
    notes: list = field(default_factory=list)
    # REAL ORDERS, when this campaign is a live one. Both stay None on paper.
    order_id: Optional[str] = None
    bracket_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None

    @property
    def quantity(self) -> int:
        return int(self.lots) * int(self.lot_size)

    @property
    def capital(self) -> float:
        return round(float(self.entry_premium) * self.quantity, 2)

    @property
    def is_open(self) -> bool:
        return self.exit_timestamp is None

    @property
    def gross(self) -> float:
        if self.is_open:
            return 0.0
        return round((float(self.exit_premium) - float(self.entry_premium)) * self.quantity, 2)

    @property
    def net(self) -> float:
        if self.is_open:
            return 0.0
        return round(self.gross - float(self.charges), 2)

    def intrinsic(self, spot: float) -> float:
        """What the contract is worth at minimum. A floor, never a price."""
        if self.side == CE:
            return max(0.0, float(spot) - float(self.strike))
        return max(0.0, float(self.strike) - float(spot))

    def trail_level(self, config: SupertrendConfig) -> Optional[float]:
        """The index level that would end this trade, or None while disarmed."""
        if not config.trail_enabled:
            return None
        if self.mfe <= max(float(config.trail_give_points), float(config.trail_arm_points)):
            return None
        return round(float(self.entry_spot) + self.mfe - float(config.trail_give_points), 2)

    def as_dict(self, config: Optional[SupertrendConfig] = None) -> dict:
        return {
            "side": self.side,
            "strike": int(self.strike),
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "lots": int(self.lots),
            "lot_size": int(self.lot_size),
            "quantity": self.quantity,
            "capital": self.capital,
            "mfe": round(float(self.mfe), 2),
            "rolled_from": self.rolled_from,
            "trail_level": self.trail_level(config) if config else None,
            "entry": {
                "timestamp": _ist(self.entry_timestamp).isoformat(),
                "spot": round(float(self.entry_spot), 2),
                "premium": round(float(self.entry_premium), 2),
            },
            "exit": (
                None
                if self.is_open
                else {
                    "timestamp": _ist(self.exit_timestamp).isoformat(),
                    "spot": round(float(self.exit_spot), 2),
                    "premium": round(float(self.exit_premium), 2),
                    "reason": self.exit_reason,
                    "priced": bool(self.exit_priced),
                }
            ),
            "charges": round(float(self.charges), 2),
            "gross": self.gross,
            "net": self.net,
            "open": self.is_open,
            "notes": list(self.notes),
        }


def summarise(positions: list) -> dict:
    """The book, counted the way the tearsheet counts it.

    ``priced_net`` is carried separately and deliberately: an exit the archive
    could not quote is floored at intrinsic, and a floor is not a price. A book
    whose profit lives only in floored exits has not been measured, and the
    tearsheet says so in its own words.
    """
    import statistics

    closed = [p for p in positions if not p.is_open]
    open_positions = [p for p in positions if p.is_open]
    if not closed:
        return {
            "trades": 0,
            "net": 0.0,
            "wins": 0,
            "win_rate": 0.0,
            "profit_factor": None,
            "average": 0.0,
            "median": 0.0,
            "best": 0.0,
            "worst": 0.0,
            "max_drawdown": 0.0,
            "peak_capital": 0.0,
            "charges": 0.0,
            "floored_exits": 0,
            "floored_net": 0.0,
            "priced_net": 0.0,
            "rolls": 0,
            "by_reason": {},
            "open": len(open_positions),
        }
    nets = [p.net for p in closed]
    wins = [n for n in nets if n > 0]
    losses = [-n for n in nets if n <= 0]
    equity, peak, drawdown = 0.0, 0.0, 0.0
    for value in nets:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    floored = [p for p in closed if not p.exit_priced]
    by_reason: dict = {}
    for position in closed:
        bucket = by_reason.setdefault(position.exit_reason or "?", {"count": 0, "net": 0.0})
        bucket["count"] += 1
        bucket["net"] = round(bucket["net"] + position.net, 2)
    return {
        "trades": len(closed),
        "net": round(sum(nets), 2),
        "wins": len(wins),
        "win_rate": round(100.0 * len(wins) / len(closed), 1),
        "profit_factor": round(sum(wins) / sum(losses), 2) if losses and sum(losses) else None,
        "average": round(sum(nets) / len(nets), 2),
        "median": round(statistics.median(nets), 2),
        "best": round(max(nets), 2),
        "worst": round(min(nets), 2),
        "max_drawdown": round(drawdown, 2),
        "peak_capital": round(max((p.capital for p in closed), default=0.0), 2),
        "charges": round(sum(p.charges for p in closed), 2),
        "floored_exits": len(floored),
        "floored_net": round(sum(p.net for p in floored), 2),
        "priced_net": round(sum(p.net for p in closed if p.exit_priced), 2),
        "rolls": sum(1 for p in closed if p.exit_reason == "roll"),
        "by_reason": by_reason,
        "open": len(open_positions),
    }


# ── the replay ───────────────────────────────────────────────────────
def replay(
    candles: list,
    *,
    config: SupertrendConfig,
    spot_at: Callable[[datetime], Optional[float]],
    price_at: Callable[[datetime, int, str, date], Optional[float]],
    expiry_for: Callable[[date], Optional[date]],
    lot_size_for: Callable[[date], int],
    charges_for: Callable[[date, float, float, int], float],
    on_skip: Optional[Callable[[datetime, str], None]] = None,
) -> list:
    """Walk closed bars in time order and return the book.

    Every fill is taken at the bar AFTER the one that decided it -- the same
    convention the published run used and the reason its numbers survived an
    independently written replayer trade-for-trade.
    """
    config.validate()
    series = direction_series(candles, config)
    if len(series) < 3:
        return []

    def _skip(when: datetime, why: str) -> None:
        if on_skip is not None:
            on_skip(when, why)

    positions: list = []
    position: Optional[SupertrendPosition] = None

    for i in range(1, len(series)):
        stamp, close, line, direction = series[i]
        previous_direction = series[i - 1][3]
        stamp = _ist(stamp)
        day = stamp.date()
        # The decision is made on THIS closed bar; the fill is the next one.
        fill_stamp = _ist(series[i + 1][0]) if i + 1 < len(series) else None

        if position is not None:
            spot = spot_at(stamp)
            if spot is not None:
                position.mfe = max(position.mfe, float(spot) - float(position.entry_spot))
            reason = None
            if day >= position.expiry and (day > position.expiry or stamp.time() >= config.square_off):
                reason = "expiry"
            elif (
                config.trail_enabled
                and spot is not None
                and position.trail_level(config) is not None
                and float(spot) <= float(position.trail_level(config))
            ):
                reason = "trail"
            elif (
                config.rolls_enabled
                and spot is not None
                and abs(float(spot) - float(position.strike)) >= config.roll_strikes * config.strike_step
            ):
                reason = "roll"
            elif direction <= 0:
                reason = "flip"

            if reason is not None:
                exit_stamp = fill_stamp if reason != "expiry" else stamp
                if exit_stamp is None:
                    exit_stamp = stamp
                exit_spot = spot_at(exit_stamp) or spot or position.entry_spot
                premium = price_at(exit_stamp, position.strike, position.side, position.expiry)
                priced = premium is not None
                if premium is None:
                    premium = position.intrinsic(float(exit_spot))
                    if premium <= 0:
                        # Out of the money with no quote. Booking it at zero
                        # invents a total loss out of a missing tick, so the
                        # trade leaves the book and is counted as skipped.
                        _skip(exit_stamp, "exit had no quote and no intrinsic value")
                        position = None
                        continue
                buy = float(position.entry_premium)
                sell = float(premium)
                position.exit_timestamp = exit_stamp
                position.exit_spot = float(exit_spot)
                position.exit_premium = sell
                position.exit_reason = reason
                position.exit_priced = priced
                position.charges = float(charges_for(position.entry_timestamp.date(), buy, sell, position.quantity))
                positions.append(position)
                rolled_from = position.strike if reason == "roll" else None
                position = None
                # A roll is not an exit from the trend: the same bar that sold
                # the old contract buys a fresh at-the-money one.
                if reason == "roll" and direction > 0 and fill_stamp is not None:
                    position = _open(
                        fill_stamp,
                        config,
                        spot_at,
                        price_at,
                        expiry_for,
                        lot_size_for,
                        signal=None,
                        rolled_from=rolled_from,
                        on_skip=_skip,
                    )
                continue

        if position is not None or direction <= 0 or fill_stamp is None:
            continue
        if previous_direction > 0 and not positions and i > 1:
            # Mid-trend at the very start of the window: the entry that would
            # have opened this trend happened before the data begins, so taking
            # it here would be a trade the rule never actually made.
            continue
        if fill_stamp.date() != day:
            continue
        if not (config.entry_after <= fill_stamp.time() < config.square_off):
            continue
        position = _open(
            fill_stamp,
            config,
            spot_at,
            price_at,
            expiry_for,
            lot_size_for,
            signal=SignalReading(stamp, close, line, direction, direction != previous_direction, "supertrend bullish"),
            rolled_from=None,
            on_skip=_skip,
        )

    return positions


def _open(
    when: datetime,
    config: SupertrendConfig,
    spot_at,
    price_at,
    expiry_for,
    lot_size_for,
    *,
    signal,
    rolled_from,
    on_skip,
) -> Optional[SupertrendPosition]:
    """Buy one contract at ``when``, or explain why it could not be bought."""
    spot = spot_at(when)
    if spot is None:
        on_skip(when, "no index price at the fill minute")
        return None
    expiry = expiry_for(when.date())
    if expiry is None or expiry < when.date():
        on_skip(when, "no expiry this archive holds")
        return None
    strike = strike_for(float(spot), config)
    premium = price_at(when, strike, config.side, expiry)
    if premium is None or premium <= 0:
        on_skip(when, f"no quote for {strike} {config.side} {expiry}")
        return None
    return SupertrendPosition(
        side=config.side,
        strike=strike,
        expiry=expiry,
        lot_size=int(lot_size_for(expiry)),
        lots=int(config.lots),
        entry_timestamp=when,
        entry_spot=float(spot),
        entry_premium=float(premium),
        signal=signal,
        rolled_from=rolled_from,
    )
