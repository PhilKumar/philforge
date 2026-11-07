"""Scalp practice: a real past trading day, replayed through the real Scalp engine.

Phil, 2026-11-07: "Is it possible to create a same scalp page and simulate trades
and charts and prices to practice without a live price or data?" ... "I want to
feel like the same live experience as like I am doing a live trade" ... "go ahead
with NIFTY and sensex. Also add that 'at real speed or faster (2x, 5x, 10x),
with Pause.'"

What is the same as live, because it is the same code:
  * every entry, stop, target, Supertrend trail, square-off and exit runs in
    `scalp.ScalpEngine` -- `PracticeScalpEngine` swaps only where prices and the
    time come from, never what is done with them;
  * prices are the day's real one-minute option and index candles (Dhan's
    rolling ATM +/-10 band, cut per day by tools/practice/build_days.py);
  * fills are the price at the click moved by a slippage drawn from Phil's own
    live scalp fills (tools/practice/fill_slippage.json), Dhan's and Zerodha's
    separately.

What cannot be the same, and is said on the page: inside a minute the price
walks open -> low/high -> high/low -> close (the archive keeps four prices a
minute, not every tick), and no money is at risk.
"""

from __future__ import annotations

import asyncio
import bisect
import contextlib
import hashlib
import json
import os
import random
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

import scalp as _scalp
from scalp import IST, ScalpEngine, ScalpTrade

_HERE = Path(__file__).resolve().parent
DATA_ROOT = Path(os.getenv("PHILFORGE_PRACTICE_DATA") or (_HERE / "data" / "practice"))
FILL_MODEL_PATH = _HERE / "tools" / "practice" / "fill_slippage.json"
UNDERLYINGS = ("NIFTY", "SENSEX")
SPEEDS = (1, 2, 5, 10)
TICK = 0.05
OPEN_MINUTE = 9 * 60 + 15
INDEX_LAST_MINUTE = 15 * 60 + 29  # the index's last bar; options trade on to 15:39 (NSE since Aug-2026)


def _tick(price: float) -> float:
    return round(round(float(price) / TICK) * TICK, 2)


def _unit(*parts) -> float:
    """A repeatable number in [0, 1) for these parts (the minute's path shape)."""
    digest = hashlib.blake2b("|".join(str(p) for p in parts).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


# ── The days on disk ─────────────────────────────────────────────────────


def available_days(underlying: str) -> list[dict]:
    path = DATA_ROOT / underlying.upper() / "days.json"
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return []


class _Series:
    """One instrument's minute bars for one day: open/high/low/close by minute of day."""

    __slots__ = ("minutes", "o", "h", "l", "c")

    def __init__(self, minutes, o, h, low, c):
        self.minutes, self.o, self.h, self.l, self.c = minutes, o, h, low, c

    def bar_at(self, minute: int) -> Optional[int]:
        """Index of the bar for `minute`, or of the last one before it (a quiet minute)."""
        i = bisect.bisect_right(self.minutes, minute) - 1
        return i if i >= 0 else None


class ReplayDay:
    """A day's option and index minutes, held as plain arrays for fast lookups."""

    def __init__(self, underlying: str, day: date):
        self.underlying = underlying.upper()
        self.day = day
        base = DATA_ROOT / self.underlying
        opt = pd.read_parquet(base / f"{day}.opt.parquet")
        idx = pd.read_parquet(base / f"{day}.idx.parquet")
        opt["minute"] = opt["ts"].dt.hour * 60 + opt["ts"].dt.minute
        self.options: dict[tuple[str, int, str], _Series] = {}
        for (expiry, strike, side), part in opt.groupby(["expiry", "strike", "side"], sort=False):
            part = part.sort_values("minute")
            self.options[(str(expiry), int(strike), str(side))] = _Series(
                part["minute"].tolist(),
                part["open"].astype(float).tolist(),
                part["high"].astype(float).tolist(),
                part["low"].astype(float).tolist(),
                part["close"].astype(float).tolist(),
            )
        self.expiries = sorted({key[0] for key in self.options})
        idx = idx.sort_values("ts")
        idx_minutes = (idx["ts"].dt.hour * 60 + idx["ts"].dt.minute).tolist()
        self.index = _Series(
            idx_minutes,
            idx["open"].astype(float).tolist(),
            idx["high"].astype(float).tolist(),
            idx["low"].astype(float).tolist(),
            idx["close"].astype(float).tolist(),
        )
        last_option_minute = int(opt["minute"].max()) if len(opt) else INDEX_LAST_MINUTE
        self.last_minute = max(last_option_minute, idx_minutes[-1] if idx_minutes else INDEX_LAST_MINUTE)
        self.strikes = {
            (expiry, side): sorted({k[1] for k in self.options if k[0] == expiry and k[2] == side})
            for expiry in self.expiries
            for side in ("CE", "PE")
        }


_DAY_CACHE: dict[tuple[str, str], ReplayDay] = {}
_DAY_CACHE_LOCK = threading.Lock()


def load_day(underlying: str, day: date) -> ReplayDay:
    key = (underlying.upper(), day.isoformat())
    with _DAY_CACHE_LOCK:
        cached = _DAY_CACHE.get(key)
    if cached is not None:
        return cached
    loaded = ReplayDay(underlying, day)
    with _DAY_CACHE_LOCK:
        if len(_DAY_CACHE) >= 8:  # today plus a few chart days; each is a few MB
            _DAY_CACHE.pop(next(iter(_DAY_CACHE)))
        _DAY_CACHE[key] = loaded
    return loaded


# ── The price inside a minute ────────────────────────────────────────────


def path_price(series: _Series, i: int, seconds: float, *seed) -> float:
    """The price `seconds` into bar `i`, walking open -> one extreme -> the other -> close.

    A rising bar dips first and a falling one spikes first, the commoner shape;
    when inside the minute each extreme comes varies with the minute, so no two
    minutes move alike. Always within the bar's real high and low.
    """
    o, h, low, c = series.o[i], series.h[i], series.l[i], series.c[i]
    first, second = (low, h) if c >= o else (h, low)
    t1 = 0.15 + 0.25 * _unit(*seed, "a")
    t2 = t1 + 0.15 + (0.75 - t1) * _unit(*seed, "b")
    f = min(max(seconds / 60.0, 0.0), 1.0)
    if f <= t1:
        price = o + (first - o) * (f / t1)
    elif f <= t2:
        price = first + (second - first) * ((f - t1) / (t2 - t1))
    else:
        price = second + (c - second) * ((f - t2) / (1.0 - t2))
    return _tick(min(max(price, low), h))


# ── The session: one replayed day, its clock and its engine ──────────────


def _load_fill_model() -> dict:
    try:
        model = json.load(open(FILL_MODEL_PATH))
    except (OSError, ValueError):
        model = {}
    fallback = [0.0]
    return {
        "entry": {b: model.get("entry_pct", {}).get(b) or fallback for b in ("dhan", "zerodha")},
        "exit": {b: model.get("exit_pct", {}).get(b) or fallback for b in ("dhan", "zerodha")},
    }


class PracticeSession:
    def __init__(
        self, user_id: int, underlying: str, day: date, *, hidden: bool, speed: int = 1, start_minute: int = OPEN_MINUTE
    ):
        self.user_id = int(user_id)
        self.underlying = underlying.upper()
        self.day = day
        self.hidden = bool(hidden)
        self.data = load_day(self.underlying, day)
        self.speed = int(speed) if int(speed) in SPEEDS else 1
        start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=int(start_minute))
        self._replay_at = start
        self._clock = time.monotonic  # real seconds; a test winds its own
        self._anchor = self._clock()
        self.paused = True  # starts paused: the clock runs when Play is pressed
        self.end_at = datetime.combine(day, datetime.min.time()) + timedelta(minutes=self.data.last_minute + 1)
        self.started_at = datetime.now(IST).replace(tzinfo=None)
        self.ended = False
        self.closed_trades: list[dict] = []
        self.fill_model = _load_fill_model()
        self._rng = random.Random(f"{self.user_id}|{self.underlying}|{day}|{time.time()}")
        self.next_entry_broker = "dhan"
        self._past_rows: dict[tuple, list[tuple]] = {}
        self._all_days = [date.fromisoformat(row["day"]) for row in available_days(self.underlying)]
        self.engine = PracticeScalpEngine(self)

    # -- the clock -------------------------------------------------------
    def now(self) -> datetime:
        if self.paused or self.ended:
            return self._replay_at
        moment = self._replay_at + timedelta(seconds=(self._clock() - self._anchor) * self.speed)
        if moment >= self.end_at:
            self._replay_at, self._anchor, self.paused = self.end_at, self._clock(), True
            return self.end_at
        return moment

    def play(self) -> None:
        if self.ended:
            return
        self._replay_at = self.now()
        self._anchor = self._clock()
        self.paused = False

    def pause(self) -> None:
        self._replay_at = self.now()
        self._anchor = self._clock()
        self.paused = True

    def set_speed(self, speed: int) -> None:
        if int(speed) not in SPEEDS:
            raise ValueError(f"speed must be one of {SPEEDS}")
        self._replay_at = self.now()
        self._anchor = self._clock()
        self.speed = int(speed)

    # -- prices ----------------------------------------------------------
    def option_price(self, expiry: str, strike: float, side: str, at: Optional[datetime] = None) -> float:
        key = (str(expiry), int(round(float(strike))), str(side).upper())
        series = self.data.options.get(key)
        if series is None:
            return 0.0
        at = at or self.now()
        minute = at.hour * 60 + at.minute
        i = series.bar_at(minute)
        if i is None:
            return 0.0
        if series.minutes[i] != minute:
            return _tick(series.c[i])  # no trade that minute: the last price stands
        return path_price(series, i, at.second + at.microsecond / 1e6, self.day, *key, minute)

    def index_price(self, at: Optional[datetime] = None) -> float:
        at = at or self.now()
        minute = at.hour * 60 + at.minute
        i = self.data.index.bar_at(minute)
        if i is None:
            return float(self.data.index.o[0]) if self.data.index.o else 0.0
        if self.data.index.minutes[i] != minute:
            return float(self.data.index.c[i])
        return path_price(self.data.index, i, at.second + at.microsecond / 1e6, self.day, "index", minute)

    def fill(self, price: float, stage: str, broker: str) -> float:
        """The price a click at `price` fills at: Phil's own live slippage, drawn."""
        if price <= 0:
            return price
        samples = self.fill_model.get(stage, {}).get(broker if broker in ("dhan", "zerodha") else "dhan") or [0.0]
        return max(TICK, _tick(price * (1 + self._rng.choice(samples) / 100.0)))

    # -- candles for the charts and the Supertrend trail ------------------
    def _days_back(self, n: int) -> list[date]:
        i = bisect.bisect_left(self._all_days, self.day)
        return self._all_days[max(0, i - n) : i]

    def _past(self, key: tuple, sessions_back: int, pick) -> list[tuple]:
        """The finished days before the replayed one: read once, kept for the session."""
        cache_key = (key, sessions_back)
        rows = self._past_rows.get(cache_key)
        if rows is not None:
            return rows
        rows = []
        for past in self._days_back(sessions_back):
            try:
                series = pick(load_day(self.underlying, past))
            except OSError:
                series = None
            if series is None:
                continue
            base = datetime.combine(past, datetime.min.time())
            rows += [
                (base + timedelta(minutes=m), series.o[j], series.h[j], series.l[j], series.c[j])
                for j, m in enumerate(series.minutes)
            ]
        self._past_rows[cache_key] = rows
        return rows

    def previous_close(self) -> float:
        """The index's close the session before the replayed day (the ticker's change)."""
        rows = self._past(("index",), 1, lambda day: day.index)
        return float(rows[-1][4]) if rows else 0.0

    def _partial(self, series: _Series, i: int, upto_seconds: float, *seed) -> tuple[float, float, float, float]:
        """The bar so far, `upto_seconds` into its minute."""
        o = _tick(series.o[i])
        prices = [o] + [
            path_price(series, i, s, *seed)
            for s in [x for x in (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55) if x <= upto_seconds]
        ]
        prices.append(path_price(series, i, upto_seconds, *seed))
        return o, max(prices), min(prices), prices[-1]

    def _frame(self, rows: list[tuple]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
        return frame.set_index("ts")

    def option_rows(self, expiry: str, strike: float, side: str, sessions_back: int = 3) -> list[tuple]:
        """The contract's minute candles: the days before, then today up to the replay clock.

        A past day's contract is the same expiry and strike: the days before an
        expiry traded it too (as the live chart's twelve days back do)."""
        key = (str(expiry), int(round(float(strike))), str(side).upper())
        past = self._past(key, sessions_back, lambda day: day.options.get(key))
        return past + self._today_rows(self.data.options.get(key), *key)

    def index_rows(self, sessions_back: int = 3) -> list[tuple]:
        past = self._past(("index",), sessions_back, lambda day: day.index)
        return past + self._today_rows(self.data.index, "index")

    def option_minutes(self, expiry: str, strike: float, side: str, sessions_back: int = 3) -> pd.DataFrame:
        return self._frame(self.option_rows(expiry, strike, side, sessions_back))

    def index_minutes(self, sessions_back: int = 3) -> pd.DataFrame:
        return self._frame(self.index_rows(sessions_back))

    def _today_rows(self, series: Optional[_Series], *seed) -> list[tuple]:
        """Today's minutes up to the clock, never one beyond it: the last is the forming one."""
        if series is None:
            return []
        now = self.now()
        minute_now = now.hour * 60 + now.minute
        base = datetime.combine(self.day, datetime.min.time())
        rows = []
        for j, m in enumerate(series.minutes):
            if m < minute_now:
                rows.append((base + timedelta(minutes=m), series.o[j], series.h[j], series.l[j], series.c[j]))
            elif m == minute_now:
                o, h, low, c = self._partial(series, j, now.second + now.microsecond / 1e6, self.day, *seed, m)
                rows.append((base + timedelta(minutes=m), o, h, low, c))
            else:
                break
        return rows

    # -- the day's record -------------------------------------------------
    def summary(self) -> dict:
        trades = list(self.closed_trades)
        pnls = [float(t.get("pnl") or 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        return {
            "underlying": self.underlying,
            "day": self.day.isoformat(),
            "trades": len(trades),
            "net": round(sum(pnls), 2),
            "wins": len(wins),
            "losses": len(losses),
            "best": round(max(pnls), 2) if pnls else 0.0,
            "worst": round(min(pnls), 2) if pnls else 0.0,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "replayed_to": self.now().strftime("%H:%M:%S"),
            "started_at": self.started_at.strftime("%Y-%m-%d %H:%M"),
            "closed_trades": trades,
        }

    def state(self) -> dict:
        now = self.now()
        return {
            "status": "ok",
            "underlying": self.underlying,
            "day": None if self.hidden and not self.ended else self.day.isoformat(),
            "weekday": now.strftime("%A") if not self.hidden or self.ended else None,
            "hidden": self.hidden and not self.ended,
            "replay_time": now.strftime("%H:%M:%S"),
            # Epoch seconds of the replay moment (IST), and the speed, so the
            # page's own clock can run between polls.
            "replay_epoch": now.replace(tzinfo=IST).timestamp(),
            "speed": self.speed,
            "paused": self.paused,
            "ended": self.ended,
            "at_close": now >= self.end_at,
            "expiries": self.data.expiries,
            "spot": self.index_price(now),
        }


# ── The engine: the real one, with the replay's prices and clock ─────────


class _ReplayQuotes:
    """Stands in for the broker client; a practice trade never reaches a broker."""

    def __init__(self, session: PracticeSession):
        self.session = session

    def get_option_ltp(self, underlying, strike, expiry, option_type):
        # The paper entry's price: the click's price, filled as Phil's are.
        price = self.session.option_price(expiry, strike, option_type)
        return self.session.fill(price, "entry", self.session.next_entry_broker)

    def __getattr__(self, name):  # anything else a broker would do: refuse loudly
        def refuse(*_a, **_k):
            raise RuntimeError(f"practice has no broker ({name})")

        return refuse


class PracticeScalpEngine(ScalpEngine):
    def __init__(self, session: PracticeSession):
        super().__init__(_ReplayQuotes(session), market_feed=None, on_trade_close=self._booked)
        self.session = session

    def _booked(self, trade_dict: dict) -> None:
        self.session.closed_trades.append(dict(trade_dict))

    # The clock: every call the engine makes for "now" reads the replay. The
    # monitor task takes the clock with it (a task copies its context).
    def start(self):
        if not self._running:
            self._running = True
            with replay_clock(self.session):
                self._task = asyncio.create_task(self._monitor_loop())

    def stop(self):
        # Not the real stop: that also switches the site-wide Dhan rate limiter,
        # which belongs to the live engine.
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    def _sync_marketfeed_throttle(self):
        return None  # the live engine's switch, not practice's

    # At 10x a quarter-second real check would be 2.5 replay seconds: check as
    # often in replay time as the live engine does in real time.
    def _monitor_interval_override_sec(self) -> float:
        return max(0.025, _scalp.SCALP_MONITOR_INTERVAL_SEC / max(1, self.session.speed))

    def _st_check_override_sec(self) -> float:
        return max(1.0, _scalp.SCALP_ST_CHECK_SEC / max(1, self.session.speed))

    # The prices.
    def _price(self, trade: ScalpTrade) -> float:
        return self.session.option_price(trade.expiry, trade.strike, trade.option_type)

    async def _fetch_all_ltps(self, trades: list) -> dict:
        return {tid: self._price(trade) for tid, trade in trades}

    def _known_ltp(self, trade: ScalpTrade, trade_id: int) -> float:
        return self._price(trade)

    def _get_ltp(self, trade: ScalpTrade, trade_id: int) -> float:
        # Used for the exit's price: filled as Phil's exits are.
        return self.session.fill(self._price(trade), "exit", getattr(trade, "broker", "dhan"))

    async def _exit_ltp(self, trade: ScalpTrade) -> float:
        return self._get_ltp(trade, trade.trade_id)

    async def _st_trail_bars(self, trade: ScalpTrade) -> list:
        frame = self.session.option_minutes(trade.expiry, trade.strike, trade.option_type, sessions_back=2)
        if frame.empty:
            return []
        bars = (
            frame.resample("5min", origin=pd.Timestamp("2021-01-01 09:15"))
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna(subset=["open"])
        )
        return [
            {
                "t": int(stamp.to_pydatetime().replace(tzinfo=IST).timestamp()),
                "h": r["high"],
                "l": r["low"],
                "c": r["close"],
            }
            for stamp, r in bars.iterrows()
        ]

    # Nothing to reconcile: no broker holds a practice trade.
    async def _sync_super_orders(self):
        return None

    async def _sync_zerodha_stops(self):
        return None

    async def _sync_broker_positions(self):
        return None


@contextlib.contextmanager
def replay_clock(session: PracticeSession):
    """Run the engine's calls on the replay's clock (see scalp.REPLAY_CLOCK)."""
    token = _scalp.REPLAY_CLOCK.set(session.now)
    try:
        yield
    finally:
        _scalp.REPLAY_CLOCK.reset(token)


# ── One session per user ─────────────────────────────────────────────────

_SESSIONS: dict[int, PracticeSession] = {}


def session_for(user_id: int) -> Optional[PracticeSession]:
    return _SESSIONS.get(int(user_id))


def pick_day(underlying: str, day: str) -> tuple[str, date, bool]:
    """(underlying, the day, hidden?) for a start request; "random" picks one and hides it."""
    underlying = str(underlying or "").strip().upper()
    if underlying not in UNDERLYINGS:
        raise ValueError(f"Practice replays {', '.join(UNDERLYINGS)}")
    days = available_days(underlying)
    if not days:
        raise ValueError(f"No {underlying} practice days are on this server yet")
    hidden = str(day or "").strip().lower() in ("", "random")
    if hidden:
        return underlying, date.fromisoformat(random.choice(days)["day"]), True
    try:
        chosen = date.fromisoformat(str(day)[:10])
    except ValueError as exc:
        raise ValueError(f"{day} is not a date") from exc
    if str(chosen) not in {row["day"] for row in days}:
        raise ValueError(f"{chosen} is not a replayable {underlying} day")
    return underlying, chosen, False


def start_minute(start: str) -> int:
    try:
        hh, mm = (int(x) for x in str(start or "09:15").split(":")[:2])
    except ValueError as exc:
        raise ValueError("start must be HH:MM") from exc
    minute = hh * 60 + mm
    if not OPEN_MINUTE <= minute <= INDEX_LAST_MINUTE:
        raise ValueError("start must be between 09:15 and 15:29")
    return minute


def start_session(
    user_id: int, underlying: str, day: date, *, hidden: bool, speed: int = 1, start: str = "09:15"
) -> PracticeSession:
    """Open a session on the event loop (the engine's task lives there); load_day first, off it."""
    minute = start_minute(start)
    end_session(user_id)
    session = PracticeSession(user_id, underlying, day, hidden=hidden, speed=speed, start_minute=minute)
    _SESSIONS[int(user_id)] = session
    return session


def end_session(user_id: int) -> Optional[dict]:
    session = _SESSIONS.pop(int(user_id), None)
    if session is None:
        return None
    session.pause()
    session.engine.stop()
    session.ended = True
    return session.summary()


__all__ = [
    "DATA_ROOT",
    "SPEEDS",
    "UNDERLYINGS",
    "PracticeScalpEngine",
    "PracticeSession",
    "available_days",
    "end_session",
    "load_day",
    "pick_day",
    "replay_clock",
    "session_for",
    "start_session",
]
