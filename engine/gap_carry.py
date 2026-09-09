"""Gap Carry — buy the close's momentum, sell into the next morning's open.

The rule, measured before it was built:

    At 15:10 read the last closed candle. If it closed ABOVE its EMA20 with
    RSI(14) at or above the threshold, buy one in-the-money CALL. If it closed
    BELOW with RSI at or below the mirror of that threshold, buy the PUT.
    Hold overnight. Sell at 09:20 the next session. Nothing else — no stop, no
    target, and no EMA condition on the way out.

WHY THE EXIT IS A CLOCK AND NOT A LINE
--------------------------------------
Its sibling on this page, the intraday EMA rule, exits when a candle closes
back through the EMA20. That exit loses: measured over 2021-2026 it wins 9% of
the time and gives back a mean 63 index points, because an EMA is a lagging
average and waiting for it to be crossed means waiting out the entire round
trip of the move. Fixed targets are worse -- a 40-point target lifts the win
rate to 57% and turns +Rs 1,12,953 into -Rs 31,511, because every rupee of
profit lives in moves LARGER than the target.

So this strategy does not try to fix the exit. It changes the trade: the
position is held across the one window where a bought option is not fighting
theta minute by minute, and sold into the open. Selling at 09:20 beat 09:45 by
a wide margin in every configuration measured -- the gap is the payoff, and
holding into the session gives it back.

WHY THE CANDLE BARELY MATTERS
-----------------------------
The chart is read ONCE, at 15:10, purely to get three numbers: the close, the
EMA20 and the RSI. The entry and exit are clock times. That is why every
timeframe from 5m to 30m and every RSI threshold from 68 to 72 came out
profitable on the same history -- twelve of twelve, eleven of them green in
both halves of the window. A rule whose result does not hinge on the candle is
not a rule that was fitted to one.

Measured on NIFTY, 2021-01 -> 2026-08, ATM+4 ITM, one lot, priced on recorded
premiums from two archives:

    5m  / RSI 70   179 trades   +Rs 2,11,624   57.0% won   PF 1.86
    10m / RSI 70   161 trades   +Rs 1,92,070   59.6% won   PF 1.98

Both survive a full one percent of spread a leg, which the intraday rule never
did -- one round trip per trade instead of many is the whole reason.

WHAT IS NOT SETTLED, AND IS ON THE PAGE FOR THAT REASON
-------------------------------------------------------
Friday is 46% of the 5m book and 67% of the 10m one: a Friday entry is held
over the weekend, so part of this is a three-day gap wearing a one-day rule's
clothes. Thursday LOSES on both. And a handful of exits -- 4 of 179 on 5m --
are contracts that gapped far enough to leave the archives' strike coverage;
those are floored at intrinsic value, which understates them, and they are
counted separately everywhere they appear. This tab paper-proves the rule
forward. It does not assume it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Callable, Optional
from zoneinfo import ZoneInfo

CE, PE = "CE", "PE"

# The chart is only ever read at one moment a day, so the choice of candle is a
# preference rather than a lever. These are the ones measured.
TIMEFRAMES = ("5m", "10m", "15m", "30m")

IST = ZoneInfo("Asia/Kolkata")


def _ist(value: datetime) -> datetime:
    """Stamp a naive wall-clock datetime as IST; leave an aware one alone."""
    return value if value.tzinfo is not None else value.replace(tzinfo=IST)


SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


class GapCarryError(ValueError):
    """A setting this strategy cannot run with."""


@dataclass(frozen=True)
class GapCarryConfig:
    """Everything the rule needs, and nothing it does not."""

    timeframe: str = "5m"
    ema_period: int = 20
    rsi_period: int = 14
    # 70 means "70 or above buys a call, 30 or below buys a put". One number,
    # read as a mirror, because a rule with two thresholds invites two fits.
    rsi_threshold: float = 70.0
    strike_offset_steps: int = 4  # strikes IN the money; ITM is LOWER for a call
    strike_step: int = 50
    lots: int = 1
    # SIZE UP AS THE BOOK BANKS MONEY -- the same ladder CE and PE run, reading
    # the same three keys, so one rule is learned once and applies everywhere.
    # One extra lot per `compound_step_pct` per cent of `compound_base_capital`
    # actually banked; 0 turns it off, which is the default.
    #
    # Measured over the 5.6-year book (2026-09-10) against CE+PE beside it:
    # +25% returned Rs 36.6L on a Rs 11.0L peak, +50% Rs 23.9L on Rs 9.3L,
    # +75% Rs 10.2L on Rs 7.8L. Per rupee of capital, +25% is the best of them
    # and +75% is worse than not compounding at all -- a middle setting losing
    # to both neighbours over 179 nights is a sign these thresholds sit on few
    # enough trades to be noise, so the step is a SETTING and not a default.
    compound_step_pct: float = 0.0
    compound_base_capital: float = 0.0
    compound_max_lots: int = 20
    entry_time: time = time(15, 10)
    exit_time: time = time(9, 20)
    # CUT A LOSING CARRY AT THE OPEN. Measured on 5.6 years of the archive
    # (2026-09-03): between 09:15 and 09:20 a carry that opened DOWN kept
    # falling and one that opened UP kept rising. Selling five minutes earlier
    # was worth +Rs 1,068 on a losing trade and -Rs 569 on a winning one, so
    # cutting everything early throws the winners away and holding everything
    # gives the losers back.
    #
    # Judged on the PREMIUM, not the index. The index tested slightly better
    # (+Rs 77,991 against +Rs 62,121, six years of six against five) but it is
    # not reliably in hand at 09:15: the opening candle has not closed, so the
    # engine's `last_index_close` is still yesterday's, and reading it would
    # score every morning as flat. The premium is fetched fresh on the same
    # tick that takes the decision, and is the thing the money actually
    # follows.
    #
    # ON since 2026-09-03, at Phil's instruction, after the measurement above.
    # It shipped OFF first so the change could be read before it moved money;
    # `tools/gapcarry_offline/replay.py` still passes it explicitly, so the
    # published book rebuilds byte-for-byte with the old rule.
    cut_losers_at_open: bool = True
    early_exit_time: time = time(9, 15)
    # A contract that expires before the position is sold cannot be held, so a
    # weekly expiring tonight is refused rather than settled at intrinsic.
    min_days_to_expiry: int = 1
    sides: tuple = (CE, PE)

    def validate(self) -> None:
        if self.timeframe not in TIMEFRAMES:
            raise GapCarryError(f"Chart must be one of {', '.join(TIMEFRAMES)}.")
        if not 50.0 <= float(self.rsi_threshold) <= 95.0:
            raise GapCarryError("RSI threshold must sit between 50 and 95.")
        if not 1 <= int(self.lots) <= 20:
            raise GapCarryError("Lots must be between 1 and 20.")
        if float(self.compound_step_pct) < 0:
            raise GapCarryError("Compound step cannot be negative.")
        if float(self.compound_step_pct) > 0 and float(self.compound_base_capital) <= 0:
            raise GapCarryError("Compounding needs the capital it is a percentage of.")
        if not 1 <= int(self.compound_max_lots) <= 20:
            raise GapCarryError("Compound lot cap must be between 1 and 20.")
        if not 0 <= int(self.strike_offset_steps) <= 10:
            raise GapCarryError("Strike offset must be between ATM and ATM+10.")
        if not (SESSION_OPEN < self.entry_time < SESSION_CLOSE):
            raise GapCarryError("The entry time must fall inside the session.")
        if not (SESSION_OPEN <= self.exit_time < SESSION_CLOSE):
            raise GapCarryError("The exit time must fall inside the session.")
        if not (SESSION_OPEN <= self.early_exit_time <= self.exit_time):
            raise GapCarryError("The early exit must fall between the open and the exit time.")

    @property
    def rsi_floor_for_call(self) -> float:
        return float(self.rsi_threshold)

    @property
    def rsi_ceiling_for_put(self) -> float:
        return 100.0 - float(self.rsi_threshold)


@dataclass(frozen=True)
class SignalReading:
    """What the 15:10 candle said, whether or not it asked for a trade."""

    timestamp: datetime
    close: float
    ema: float
    rsi: float
    side: Optional[str]  # CE, PE, or None when nothing qualified
    reason: str

    @property
    def fires(self) -> bool:
        return self.side is not None

    def as_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "close": round(self.close, 2),
            "ema": round(self.ema, 2),
            "rsi": round(self.rsi, 1),
            "side": self.side,
            "reason": self.reason,
        }


def _wilder_rsi(closes: list, period: int) -> list:
    """Wilder's RSI, the same smoothing engine/indicators.py draws with.

    Recomputed here in plain Python so the engine can run on a list of candles
    without dragging pandas into the paper loop; it agrees with the chart to
    the decimal because both use alpha = 1/period.
    """
    if len(closes) <= period:
        return [float("nan")] * len(closes)
    out = [float("nan")] * len(closes)
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    alpha = 1.0 / period
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (1 - alpha) * avg_gain + alpha * max(change, 0.0)
        avg_loss = (1 - alpha) * avg_loss + alpha * max(-change, 0.0)
        if avg_loss <= 0:
            out[i] = 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def _ema(closes: list, period: int) -> list:
    if not closes:
        return []
    k = 2.0 / (period + 1.0)
    out = [closes[0]]
    for price in closes[1:]:
        out.append(price * k + out[-1] * (1 - k))
    return out


def indicator_series(candles: list, config: GapCarryConfig) -> dict:
    """The EMA and RSI the chart draws, from the SAME two functions the rule
    reads with.

    A chart is only evidence if it is drawn from the numbers that made the
    decision. Recomputing an EMA in the route -- or in JavaScript -- would give
    a picture that agrees with the rule right up until one of them is changed.
    NaN is emitted as None so the renderer can leave the warm-up unpainted
    instead of drawing a line to zero.
    """
    rows = [c for c in candles if c is not None]
    if not rows:
        return {
            "ema": [],
            "rsi": [],
            "ema_period": int(config.ema_period),
            "rsi_period": int(config.rsi_period),
            "rsi_upper": float(config.rsi_floor_for_call),
            "rsi_lower": float(config.rsi_ceiling_for_put),
        }
    closes = [float(c.close) for c in rows]
    ema = _ema(closes, int(config.ema_period))
    rsi = _wilder_rsi(closes, int(config.rsi_period))

    def clean(value):
        number = float(value)
        return None if number != number else round(number, 2)  # NaN != NaN

    # The EMA's own warm-up: seeded from the first close, it is not an average
    # of `period` bars until it has seen that many, and a chart that draws the
    # seed implies a level the rule would never have read.
    warm = int(config.ema_period) - 1
    stamps = [int(c.timestamp.timestamp()) for c in rows]
    return {
        "ema": [{"t": s, "v": None if i < warm else clean(v)} for i, (s, v) in enumerate(zip(stamps, ema))],
        "rsi": [{"t": s, "v": clean(v)} for s, v in zip(stamps, rsi)],
        "ema_period": int(config.ema_period),
        "rsi_period": int(config.rsi_period),
        "rsi_upper": float(config.rsi_floor_for_call),
        "rsi_lower": float(config.rsi_ceiling_for_put),
    }


def read_signal(candles: list, config: GapCarryConfig, *, at: Optional[datetime] = None) -> Optional[SignalReading]:
    """The last candle closed at or before the entry time, and what it asks for.

    `candles` is any sequence of objects carrying `.timestamp` and `.close` in
    ascending order -- LadderCandle, Candle, or a plain namespace. Bars after
    the entry time are ignored rather than trimmed by the caller, because a
    paper loop polling at 15:12 and a backtest replaying the whole day must
    read the same bar.
    """
    if not candles:
        return None
    cutoff = config.entry_time
    day = at.date() if at is not None else candles[-1].timestamp.date()
    usable = [c for c in candles if c.timestamp.date() <= day]
    same_day = [c for c in usable if c.timestamp.date() == day and c.timestamp.time() <= cutoff]
    if not same_day:
        return None
    closes = [float(c.close) for c in usable]
    idx = len(usable) - 1
    while idx >= 0 and (usable[idx].timestamp.date() != day or usable[idx].timestamp.time() > cutoff):
        idx -= 1
    if idx < 0:
        return None
    emas = _ema(closes, config.ema_period)
    rsis = _wilder_rsi(closes, config.rsi_period)
    close, ema_v, rsi_v = closes[idx], emas[idx], rsis[idx]
    if rsi_v != rsi_v:  # NaN: not enough history to judge
        return None
    bar = usable[idx]
    side, reason = None, ""
    if close > ema_v and rsi_v >= config.rsi_floor_for_call and CE in config.sides:
        side = CE
        reason = f"close {close:,.2f} above EMA{config.ema_period} {ema_v:,.2f}, RSI {rsi_v:.1f} >= {config.rsi_floor_for_call:g}"
    elif close < ema_v and rsi_v <= config.rsi_ceiling_for_put and PE in config.sides:
        side = PE
        reason = f"close {close:,.2f} below EMA{config.ema_period} {ema_v:,.2f}, RSI {rsi_v:.1f} <= {config.rsi_ceiling_for_put:g}"
    else:
        where = "above" if close > ema_v else "below"
        need = config.rsi_floor_for_call if close > ema_v else config.rsi_ceiling_for_put
        reason = f"close {where} EMA{config.ema_period}, but RSI {rsi_v:.1f} has not reached {need:g}"
    return SignalReading(timestamp=bar.timestamp, close=close, ema=ema_v, rsi=rsi_v, side=side, reason=reason)


def strike_for(spot: float, side: str, config: GapCarryConfig) -> int:
    """In the money is a LOWER strike for a call and a HIGHER one for a put.

    Applying the offset with the trade's direction instead of against it buys
    the cheap out-of-the-money wing and calls it delta. That mistake inverted a
    whole finding once already, so the sign lives in one place.
    """
    step = int(config.strike_step)
    atm = int(round(float(spot) / step) * step)
    return atm - (1 if side == CE else -1) * int(config.strike_offset_steps) * step


# ─────────────────────────────── the position ───────────────────────────────
@dataclass
class GapCarryPosition:
    """One overnight carry: bought yesterday at the close, sold this morning.

    There is only ever one leg. No ladder, no rungs, no averaging down -- the
    whole rule is a single contract held across one night, and a class that
    could hold more would invite a road that has not been measured.
    """

    session: date
    side: str
    strike: int
    expiry: date
    lot_size: int
    lots: int
    signal: SignalReading
    entry_timestamp: Optional[datetime] = None
    entry_spot: float = 0.0
    entry_premium: Optional[float] = None
    exit_timestamp: Optional[datetime] = None
    exit_spot: float = 0.0
    exit_premium: Optional[float] = None
    exit_reason: str = ""
    # An exit the archives could not quote is floored at intrinsic value and
    # said so. A floor is not a price, and a book that hides the difference is
    # not a book.
    exit_priced: bool = True
    charges: float = 0.0
    notes: list = field(default_factory=list)
    # REAL ORDERS, when this campaign is a live one. `order_id` is the entry
    # at Dhan and `bracket_order_id` the Super Order carrying its stop; both
    # stay None on paper, which is every run until live is proven.
    order_id: Optional[str] = None
    bracket_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None

    @property
    def quantity(self) -> int:
        return int(self.lots) * int(self.lot_size)

    @property
    def capital(self) -> float:
        return float(self.entry_premium or 0.0) * self.quantity

    @property
    def is_open(self) -> bool:
        return self.entry_premium is not None and self.exit_premium is None

    @property
    def gross(self) -> Optional[float]:
        if self.entry_premium is None or self.exit_premium is None:
            return None
        return (self.exit_premium - self.entry_premium) * self.quantity

    @property
    def net(self) -> Optional[float]:
        gross = self.gross
        return None if gross is None else gross - float(self.charges or 0.0)

    def intrinsic(self, spot: float) -> float:
        return max(0.0, (spot - self.strike) if self.side == CE else (self.strike - spot))

    def as_dict(self) -> dict:
        return {
            "session": self.session.isoformat(),
            "side": self.side,
            "strike": self.strike,
            "expiry": self.expiry.isoformat(),
            "lots": self.lots,
            "lot_size": self.lot_size,
            "quantity": self.quantity,
            "signal": self.signal.as_dict() if self.signal else None,
            "entry": None
            if self.entry_premium is None
            else {
                "timestamp": self.entry_timestamp.isoformat() if self.entry_timestamp else None,
                "spot": round(self.entry_spot, 2),
                "premium": round(float(self.entry_premium), 2),
                "capital": round(self.capital, 2),
            },
            "exit": None
            if self.exit_premium is None
            else {
                "timestamp": self.exit_timestamp.isoformat() if self.exit_timestamp else None,
                "spot": round(self.exit_spot, 2),
                "premium": round(float(self.exit_premium), 2),
                "reason": self.exit_reason,
                "priced": bool(self.exit_priced),
            },
            "charges": round(float(self.charges or 0.0), 2),
            "gross": None if self.gross is None else round(self.gross, 2),
            "net": None if self.net is None else round(self.net, 2),
            "open": self.is_open,
            "notes": list(self.notes),
        }


def summarise(positions: list) -> dict:
    """The book, said plainly, with the floored exits kept visible."""
    closed = [p for p in positions if p.net is not None]
    if not closed:
        return {
            "trades": 0,
            "net": 0.0,
            "wins": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "peak_capital": 0.0,
            "floored_exits": 0,
            "floored_net": 0.0,
            "open": sum(1 for p in positions if p.is_open),
        }
    nets = [float(p.net) for p in closed]
    wins = [n for n in nets if n > 0]
    gains = sum(wins)
    losses = -sum(n for n in nets if n <= 0)
    equity = peak = drawdown = 0.0
    for n in nets:
        equity += n
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    floored = [p for p in closed if not p.exit_priced]
    by_side: dict = {}
    for p in closed:
        row = by_side.setdefault(p.side, {"trades": 0, "net": 0.0, "wins": 0})
        row["trades"] += 1
        row["net"] += float(p.net)
        row["wins"] += 1 if float(p.net) > 0 else 0
    for row in by_side.values():
        row["net"] = round(row["net"], 2)
        row["win_rate"] = round(row["wins"] / row["trades"], 4) if row["trades"] else 0.0
    return {
        "trades": len(closed),
        "net": round(sum(nets), 2),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(closed), 4),
        "profit_factor": round(gains / losses, 3) if losses else None,
        "average": round(sum(nets) / len(nets), 2),
        "median": round(sorted(nets)[len(nets) // 2], 2),
        "best": round(max(nets), 2),
        "worst": round(min(nets), 2),
        "max_drawdown": round(drawdown, 2),
        "peak_capital": round(max((p.capital for p in closed), default=0.0), 2),
        "charges": round(sum(float(p.charges or 0.0) for p in closed), 2),
        "floored_exits": len(floored),
        "floored_net": round(sum(float(p.net) for p in floored), 2),
        "by_side": by_side,
        "open": sum(1 for p in positions if p.is_open),
    }


# ──────────────────────────────── the replay ────────────────────────────────
def laddered_lots(config: "GapCarryConfig", banked: float) -> int:
    """How many lots the next carry takes, given what this book has BANKED.

    The same arithmetic as engine/backtest.py and engine/live.py: one extra lot
    per rung of realised profit on top of the configured base, clamped. Only
    realised money counts -- a carry still open, however green, never sizes the
    next one, because it can still turn overnight.
    """
    base = int(config.lots)
    step = float(getattr(config, "compound_step_pct", 0) or 0)
    if step <= 0:
        return base
    capital = float(getattr(config, "compound_base_capital", 0) or 0)
    if capital <= 0:
        return base
    rung = capital * step / 100.0
    extra = int(max(0.0, float(banked)) // rung)
    cap = int(getattr(config, "compound_max_lots", 20) or 20)
    return max(1, min(base + extra, cap))


def replay(
    sessions: list,
    *,
    config: GapCarryConfig,
    candles_for: Callable,
    spot_at: Callable,
    price_at: Callable,
    expiry_for: Callable,
    lot_size_for: Callable,
    charges_for: Callable,
    on_skip: Optional[Callable] = None,
) -> list:
    """Walk the sessions in order and carry one position across each night.

    Every callback is injected rather than imported so that the paper loop, the
    backtest route and the offline verifier all drive the SAME walk. A rule
    that is one thing on the monitor and another in the replay is two rules.

    Skips are reported, never silently dropped: a session whose contract cannot
    be priced at either end is not a flat trade, it is an unanswered question,
    and a book that quietly swallows those flatters itself.
    """
    positions: list = []
    banked = 0.0  # realised profit so far, which is what the ladder sizes on
    ordered = sorted(sessions)
    for i, session in enumerate(ordered[:-1]):
        nxt = ordered[i + 1]
        rows = candles_for(session)
        if not rows:
            continue
        signal = read_signal(rows, config, at=datetime.combine(session, config.entry_time))
        if signal is None or not signal.fires:
            continue
        expiry = expiry_for(session)
        if expiry is None or expiry < nxt:
            # The contract settles before the position would be sold.
            if on_skip:
                on_skip(session, "expiry lands before the exit")
            continue
        entry_ts = _ist(datetime.combine(session, config.entry_time))
        exit_ts = _ist(datetime.combine(nxt, config.exit_time))
        spot_in = spot_at(entry_ts)
        if spot_in is None:
            if on_skip:
                on_skip(session, "no index level at the entry minute")
            continue
        strike = strike_for(spot_in, signal.side, config)
        lot = int(lot_size_for(expiry))
        premium_in = price_at(entry_ts, strike, signal.side, expiry)
        if premium_in is None:
            if on_skip:
                on_skip(session, "no premium at the entry minute")
            continue
        position = GapCarryPosition(
            session=session,
            side=signal.side,
            strike=strike,
            expiry=expiry,
            lot_size=lot,
            lots=laddered_lots(config, banked),
            signal=signal,
            entry_timestamp=entry_ts,
            entry_spot=float(spot_in),
            entry_premium=float(premium_in),
        )
        # THE OPEN GETS A LOOK FIRST, when the rule is on. A carry already down
        # at 09:15 is sold there rather than held to 09:20 -- see
        # `cut_losers_at_open`. A carry that is up, or that has no price at the
        # open to judge by, goes the normal route.
        cut_early = False
        if config.cut_losers_at_open and config.early_exit_time < config.exit_time:
            early_ts = _ist(datetime.combine(nxt, config.early_exit_time))
            premium_early = price_at(early_ts, strike, signal.side, expiry)
            if premium_early is not None and float(premium_early) < float(premium_in):
                exit_ts, cut_early = early_ts, True
        spot_out = spot_at(exit_ts)
        premium_out = price_at(exit_ts, strike, signal.side, expiry)
        if premium_out is None:
            # Off the edge of the archives' strike coverage, which happens
            # precisely when the gap was large -- so these are mostly winners,
            # and flooring them at intrinsic UNDERSTATES the book.
            if spot_out is None:
                if on_skip:
                    on_skip(session, "no price and no index level at the exit")
                continue
            floor = position.intrinsic(float(spot_out))
            if floor <= 0:
                # Out of the money with no quote. It is not worth zero; booking
                # it as zero would invent a total loss out of a missing tick.
                if on_skip:
                    on_skip(session, "exit out of the money with no quote — dropped, not zeroed")
                continue
            premium_out, position.exit_priced = floor, False
        position.exit_timestamp = exit_ts
        position.exit_spot = float(spot_out if spot_out is not None else position.entry_spot)
        position.exit_premium = float(premium_out)
        if position.exit_priced:
            position.exit_reason = "MORNING_CUT" if cut_early else "MORNING_EXIT"
        else:
            position.exit_reason = "MORNING_EXIT_AT_INTRINSIC"
        position.charges = float(charges_for(session, float(premium_in), float(premium_out), position.quantity))
        positions.append(position)
        # Banked only once the carry has settled, so the next night sizes on
        # money that is actually in hand.
        if position.net is not None:
            banked += float(position.net)
    return positions
