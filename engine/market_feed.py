"""
engine/market_feed.py — Real-time WebSocket Market Feed + Candle Aggregator

Uses Dhan's MarketFeed WebSocket (wss://api-feed.dhan.co) to get tick-level
market data and aggregates into OHLCV candles of ANY timeframe.

Architecture:
  ┌────────────────┐      ticks       ┌─────────────────┐   candle_close   ┌──────────────┐
  │ Dhan WebSocket │  ──────────────► │ CandleAggregator│ ────────────────►│ Paper / Live │
  │ (background    │   LTP updates    │ (any timeframe)  │   + indicators   │   Engine     │
  │  thread)       │                  │  1m, 3m, 5m...  │                  │              │
  └────────────────┘                  └─────────────────┘                  └──────────────┘

Does NOT affect backtesting at all — this is a pure live-data module.
"""

import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

# Phase 4: Fast JSON deserialization for WebSocket tick payloads
try:
    import orjson as _json_mod

    _json_loads = _json_mod.loads  # ~5-10x faster than stdlib json.loads
    _json_dumps = _json_mod.dumps
    _FAST_JSON = True
except ImportError:
    import json as _json_mod

    _json_loads = _json_mod.loads
    _json_dumps = _json_mod.dumps
    _FAST_JSON = False

# IST timezone (UTC+5:30) — Dhan operates on Indian market time
IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist() -> datetime:
    """Return current time in IST (naive, for slot/log use)."""
    return datetime.now(IST).replace(tzinfo=None)


def _looks_like_disconnect_error(error) -> bool:
    """Heuristic for WebSocket errors that mean the connection is effectively dead."""
    msg = str(error or "").lower()
    markers = (
        "closed",
        "connection",
        "close frame",
        "broken pipe",
        "going away",
        "keepalive ping timeout",
        "socket is already closed",
        "1000",
        "1005",
        "1011",
    )
    return any(marker in msg for marker in markers)


import pandas as pd

import config
from broker.dhan import DhanClient, ScripMaster
from engine.timeframes import (
    aligned_candle_start,
    drop_incomplete_candle,
    get_fetch_timeframe,
    is_supported_timeframe,
    resample_ohlcv,
)

# Try importing dhanhq MarketFeed, fall back gracefully
_DHAN_FEED_V2 = False  # True = v2.2.0+ MarketFeed (dhan_context), False = v2.0.x DhanFeed (client_id, access_token)
try:
    try:
        from dhanhq.marketfeed import MarketFeed  # v2.2.0+

        _DHAN_FEED_V2 = True
    except ImportError:
        from dhanhq.marketfeed import DhanFeed as MarketFeed  # v2.0.x
    HAS_DHAN_FEED = True
    # v2.0.x DhanFeed doesn't have Ticker/Quote/Full constants — define fallbacks
    _TICKER = getattr(MarketFeed, "Ticker", 15)
    _QUOTE = getattr(MarketFeed, "Quote", 17)
    _FULL = getattr(MarketFeed, "Full", 21)
except ImportError:
    HAS_DHAN_FEED = False
    _TICKER = 15
    _QUOTE = 17
    _FULL = 21
    MarketFeed = None
    print("[FEED] ⚠ dhanhq not installed — WebSocket feed unavailable, REST polling fallback active")


# ════════════════════════════════════════════════════════════════
#  Candle Aggregator — aggregates ticks into OHLCV candles
# ════════════════════════════════════════════════════════════════


class CandleAggregator:
    """
    Aggregates streaming LTP ticks into OHLCV candles of arbitrary timeframe.

    Usage:
        agg = CandleAggregator(timeframe_minutes=5)
        agg.on_candle_close = my_callback   # called with (candle_df, latest_candle)
        agg.feed_tick(price=24500.5, volume=100, ts=datetime.now())
    """

    def __init__(self, timeframe_minutes: int = 5, max_candles: int = 500):
        self.tf = timeframe_minutes
        self.max_candles = max_candles

        # Current forming candle
        self._current: Optional[dict] = None
        self._current_slot: Optional[datetime] = None
        # When the forming candle last actually saw a price. A clock-closed
        # candle whose last tick is old is carrying a stale close, and the
        # reader can measure that for itself rather than trust a flag.
        self._last_tick_ts: Optional[datetime] = None
        # The newest slot already emitted, so a late tick cannot resurrect it.
        self._last_closed_slot: Optional[datetime] = None

        # Completed candles
        self.candles: List[dict] = []

        # Callbacks: fired when a candle closes (with full DataFrame + latest)
        self.on_candle_close: List[Callable] = []

    def _get_slot(self, ts: datetime) -> datetime:
        """Get the candle slot start time for a given timestamp."""
        return aligned_candle_start(ts, self.tf)

    def feed_tick(self, price: float, volume: int = 0, ts: datetime = None):
        """Feed a tick into the aggregator. Call this on every LTP update."""
        if ts is None:
            ts = _now_ist()

        slot = self._get_slot(ts)

        # A tick from a slot the clock has ALREADY closed must not reopen it.
        # Ticks can arrive out of order and a moment late, and re-emitting a
        # closed candle would hand the strategy the same bar twice -- which on
        # an entry rule means acting on it twice.
        if self._last_closed_slot is not None and slot <= self._last_closed_slot:
            return

        # New candle slot?
        if self._current_slot is None or slot > self._current_slot:
            # Close previous candle
            if self._current is not None:
                self._close_candle()

            # Start new candle
            self._current_slot = slot
            self._last_tick_ts = ts
            self._current = {
                "timestamp": slot,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
            }
        else:
            self._last_tick_ts = ts
            # Update forming candle
            self._current["high"] = max(self._current["high"], price)
            self._current["low"] = min(self._current["low"], price)
            self._current["close"] = price
            self._current["volume"] += volume

    def close_due_candles(self, now: Optional[datetime] = None) -> bool:
        """Close the forming candle if its slot has ENDED, by the clock.

        feed_tick() used to be the only thing that could end a candle, so a
        candle closed when the NEXT tick arrived rather than when it was
        actually over. On a quiet minute the engine stayed blind: on
        2026-09-10 the 09:20 candle was complete at 09:25:00 and the engine
        did not learn of it until 09:25:09, because that was when NIFTY next
        printed. Seven point nine seconds of a fifteen-second entry, spent
        waiting for the market to speak.

        Called on a timer, this ends the candle on time. Ticks then only shape
        the candle that is forming. Returns True if a candle was closed.
        """
        if self._current is None or self._current_slot is None:
            return False
        now = now or _now_ist()
        slot_ends = self._current_slot + timedelta(minutes=self.tf)
        if now < slot_ends:
            return False
        # How old the close price is at the moment the clock ends the candle.
        # Zero means a tick landed on the boundary; several seconds means the
        # market went quiet and this close is that many seconds behind.
        if self._last_tick_ts is not None:
            self._current["close_age_s"] = round((slot_ends - self._last_tick_ts).total_seconds(), 1)
        self._close_candle()
        self._current = None
        self._current_slot = None
        self._last_tick_ts = None
        return True

    def _close_candle(self):
        """Close the current candle and fire callback."""
        if self._current is None:
            return

        candle = self._current.copy()
        candle.setdefault("close_age_s", 0.0)
        slot = candle.get("timestamp")
        if slot is not None and (self._last_closed_slot is None or slot > self._last_closed_slot):
            self._last_closed_slot = slot
        self.candles.append(candle)

        # Trim to max
        if len(self.candles) > self.max_candles:
            self.candles = self.candles[-self.max_candles :]

        # Fire all callbacks
        if self.on_candle_close:
            df = self.to_dataframe()
            for cb in self.on_candle_close:
                try:
                    cb(df, candle)
                except Exception as e:
                    print(f"[FEED] Candle close callback error: {e}")

    def force_close(self):
        """Force-close the current forming candle (e.g. at EOD)."""
        if self._current is not None:
            self._close_candle()
            self._current = None
            self._current_slot = None

    def get_current(self) -> Optional[dict]:
        """Get the currently forming (incomplete) candle."""
        return self._current.copy() if self._current else None

    def to_dataframe(self, include_current: bool = False) -> pd.DataFrame:
        """Convert completed candles to DataFrame with timestamp index."""
        rows = list(self.candles)
        if include_current and self._current is not None:
            rows.append(self._current.copy())
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df


# ════════════════════════════════════════════════════════════════
#  DhanContext wrapper — dhanhq MarketFeed needs a context object
# ════════════════════════════════════════════════════════════════


class _DhanContext:
    """Minimal context object that dhanhq.MarketFeed expects."""

    def __init__(self, client_id: str, access_token: str):
        self._cid = client_id
        self._token = access_token

    def get_client_id(self) -> str:
        return self._cid

    def get_access_token(self) -> str:
        return self._token


# ════════════════════════════════════════════════════════════════
#  LiveMarketFeed — manages WebSocket connection + subscriptions
# ════════════════════════════════════════════════════════════════


class LiveMarketFeed:
    """
    Wrapper around Dhan's MarketFeed WebSocket.

    Provides:
    - Streaming LTP for any subscribed instrument (index + options)
    - Tick-driven candle aggregation (any timeframe)
    - Thread-safe LTP cache
    - Auto-reconnect

    Usage:
        feed = LiveMarketFeed()
        feed.set_candle_callback(timeframe=5, callback=my_func)
        feed.subscribe_index("26000")       # NIFTY
        feed.subscribe_option("NIFTY", 25000, "2026-03-05", "PE")
        feed.start()

        # Later:
        ltp = feed.get_ltp(security_id)   # instant, from cache
    """

    # Map instrument_id → (dhan_security_id, exchange_segment_code)
    INDEX_MAP = {
        "26000": ("13", 0),  # NIFTY 50 on IDX
        "26009": ("25", 0),  # BANK NIFTY on IDX
        "26017": ("27", 0),  # FIN NIFTY on IDX
        "26037": ("442", 0),  # MIDCAP NIFTY on IDX
        "1": ("1", 4),  # SENSEX on BSE
    }

    def __init__(self, dhan: DhanClient = None):
        self.dhan = dhan or DhanClient()
        self._lock = threading.Lock()

        # LTP cache: security_id (int) → {"ltp": float, "time": datetime, ...}
        self._ltp_cache: Dict[int, dict] = {}

        # Subscriptions: list of (exchange_code, security_id, request_type)
        self._subscriptions: List[Tuple[int, int, int]] = []

        # Candle aggregators (keyed by instrument label, e.g. "NIFTY_IDX")
        self._aggregators: Dict[str, CandleAggregator] = {}
        self._closer_thread: Optional[threading.Thread] = None
        self._closer_stop = threading.Event()

        # Index security IDs we need to feed into aggregators
        self._index_sec_ids: Dict[int, str] = {}  # sec_id → label
        # Option security IDs for LTP tracking
        self._option_sec_ids: Dict[int, str] = {}  # sec_id → label

        # Tick callbacks: called on EVERY tick (for live premium tracking)
        self._tick_callbacks: List[Callable] = []

        # WebSocket thread
        self._ws_thread: Optional[threading.Thread] = None
        self._feed: Optional[MarketFeed] = None
        self._running = False

        # Historical data bootstrap (for indicators that need history)
        self._bootstrap_done = False

        # Reconnect state
        self._reconnect_lock = threading.Lock()
        self._reconnecting = False
        self._connected_at: Optional[datetime] = None
        self._last_tick_time: Optional[datetime] = None
        self._reconnect_count = 0

    # ── Subscription Management ───────────────────────────────

    def subscribe_index(self, instrument_id: str, label: str = None):
        """Subscribe to index LTP (NIFTY, BANKNIFTY, etc.)."""
        info = self.INDEX_MAP.get(instrument_id)
        if not info:
            print(f"[FEED] Unknown instrument: {instrument_id}")
            return
        sec_id, exchange = info
        sec_id_int = int(sec_id)
        if label is None:
            label = f"IDX_{instrument_id}"

        self._index_sec_ids[sec_id_int] = label
        # Subscribe as Ticker (type 15) for speed — just LTP
        self._subscriptions.append((exchange, sec_id_int, _TICKER))
        print(f"[FEED] Subscribed index: {label} (sec_id={sec_id}, exchange={exchange})")

    def subscribe_option(
        self, underlying: str, strike: int, expiry: str, option_type: str, label: str = None
    ) -> Optional[int]:
        """Subscribe to an option contract's LTP. Returns security_id or None."""
        sec_id_str = ScripMaster.lookup(underlying, strike, expiry, option_type)
        if not sec_id_str:
            print(f"[FEED] Cannot subscribe: {underlying} {strike} {option_type} {expiry} — not in ScripMaster")
            return None

        sec_id = int(sec_id_str)
        exchange = 8 if underlying == "SENSEX" else 2  # BSE_FNO or NSE_FNO

        if label is None:
            label = f"{underlying}_{strike}_{option_type}"

        sub_type = _TICKER
        self._option_sec_ids[sec_id] = label
        self._subscriptions.append((exchange, sec_id, sub_type))

        # Dynamically subscribe on the live WebSocket if already running
        if self._running and self._feed:
            try:
                self._feed.subscribe_symbols([(exchange, str(sec_id), sub_type)])
                print(f"[FEED] ✅ Live-subscribed option: {label} (sec_id={sec_id})")
            except Exception as e:
                print(f"[FEED] ⚠ Live subscribe failed ({e}), will get LTP via REST fallback")
        else:
            print(f"[FEED] Queued option: {label} (sec_id={sec_id})")
        return sec_id

    def unsubscribe_option(self, sec_id: int):
        """Remove an option from LTP tracking."""
        with self._lock:
            self._option_sec_ids.pop(sec_id, None)
            self._ltp_cache.pop(sec_id, None)

    # ── Candle Aggregation ────────────────────────────────────

    # ── closing candles on the clock ──────────────────────────────
    def _run_candle_closer(self):
        """End every aggregator's candle when its slot ends, not when the next
        tick happens to arrive.

        A tick-driven close makes the engine blind for as long as the market is
        quiet. On 2026-09-10 that was 7.9 seconds of a 15-second entry. This
        checks four times a second, which is far finer than any timeframe here
        and costs nothing measurable.
        """
        while not self._closer_stop.wait(0.25):
            try:
                for agg in list(self._aggregators.values()):
                    agg.close_due_candles()
            except Exception as exc:  # a bad callback must not kill the clock
                print(f"[FEED] candle closer: {exc}")

    def start_candle_closer(self):
        if self._closer_thread and self._closer_thread.is_alive():
            return
        self._closer_stop.clear()
        self._closer_thread = threading.Thread(target=self._run_candle_closer, name="candle-closer", daemon=True)
        self._closer_thread.start()

    def stop_candle_closer(self):
        self._closer_stop.set()

    def set_candle_config(
        self, instrument_id: str, timeframe: int, callback: Callable, history_df: pd.DataFrame = None
    ):
        """
        Set up candle aggregation for an index instrument.

        Args:
            instrument_id: e.g. "26000" for NIFTY
            timeframe: candle width in minutes (1, 3, 5, 7, etc.)
            callback: called with (df, latest_candle) on each candle close
            history_df: optional historical candles to seed the aggregator
        """
        info = self.INDEX_MAP.get(instrument_id)
        if not info:
            return
        sec_id_int = int(info[0])
        label = self._index_sec_ids.get(sec_id_int, f"IDX_{instrument_id}")

        # Use composite key so same instrument with same timeframe shares aggregator
        agg_key = f"{label}_{timeframe}m"

        if agg_key in self._aggregators:
            # Aggregator exists — just add the callback
            agg = self._aggregators[agg_key]
            if callback not in agg.on_candle_close:
                agg.on_candle_close.append(callback)
            print(f"[FEED] Added callback to existing aggregator: {agg_key}")
            return

        agg = CandleAggregator(timeframe_minutes=timeframe, max_candles=500)
        agg.on_candle_close = [callback]

        # Pre-seed with historical candles if provided
        if history_df is not None and not history_df.empty:
            for ts, row in history_df.iterrows():
                agg.candles.append(
                    {
                        "timestamp": ts,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": int(row.get("volume", 0)),
                    }
                )
            if agg.candles:
                agg.candles = agg.candles[-agg.max_candles :]

        self._aggregators[agg_key] = agg
        # The clock that ends candles on time, started with the first
        # aggregator and shared by all of them.
        self.start_candle_closer()
        print(f"[FEED] Candle aggregator set: {agg_key}")

    def remove_candle_callback(self, instrument_id: str, timeframe: int, callback: Callable):
        """Remove a specific callback from a candle aggregator."""
        info = self.INDEX_MAP.get(instrument_id)
        if not info:
            return
        sec_id_int = int(info[0])
        label = self._index_sec_ids.get(sec_id_int, f"IDX_{instrument_id}")
        agg_key = f"{label}_{timeframe}m"
        agg = self._aggregators.get(agg_key)
        if agg and callback in agg.on_candle_close:
            agg.on_candle_close.remove(callback)
            print(f"[FEED] Removed callback from aggregator: {agg_key}")

    def get_current_candle(self, instrument_id: str, timeframe: int) -> Optional[dict]:
        """Get the currently forming candle for an instrument/timeframe, if available."""
        info = self.INDEX_MAP.get(instrument_id)
        if not info:
            return None
        sec_id_int = int(info[0])
        label = self._index_sec_ids.get(sec_id_int, f"IDX_{instrument_id}")
        agg_key = f"{label}_{timeframe}m"
        agg = self._aggregators.get(agg_key)
        if not agg:
            return None
        return agg.get_current()

    def get_candle_snapshot(self, instrument_id: str, timeframe: int, *, include_current: bool = False) -> pd.DataFrame:
        """Get a candle DataFrame snapshot for an instrument/timeframe."""
        info = self.INDEX_MAP.get(instrument_id)
        if not info:
            return pd.DataFrame()
        sec_id_int = int(info[0])
        label = self._index_sec_ids.get(sec_id_int, f"IDX_{instrument_id}")
        agg_key = f"{label}_{timeframe}m"
        agg = self._aggregators.get(agg_key)
        if not agg:
            return pd.DataFrame()
        return agg.to_dataframe(include_current=include_current)

    # ── LTP Access ────────────────────────────────────────────

    def get_ltp(self, sec_id: int) -> float:
        """Get cached LTP for a security. Returns 0 if not available."""
        with self._lock:
            entry = self._ltp_cache.get(sec_id)
            return float(entry["ltp"]) if entry else 0.0

    def get_all_ltp(self) -> dict:
        """Get copy of all cached LTPs."""
        with self._lock:
            return {k: v.copy() for k, v in self._ltp_cache.items()}

    # ── Tick Callbacks ────────────────────────────────────────

    def add_tick_callback(self, cb: Callable):
        """Register a callback for every tick. cb(security_id, ltp, timestamp)"""
        self._tick_callbacks.append(cb)

    # ── WebSocket Lifecycle ───────────────────────────────────

    def start(self):
        """Start the WebSocket feed in a background thread."""
        if not HAS_DHAN_FEED:
            print("[FEED] ⚠ dhanhq MarketFeed not available — cannot start WebSocket")
            return False

        if self._running:
            print("[FEED] Already running")
            return True

        if not self._subscriptions:
            print("[FEED] No subscriptions — nothing to stream")
            return False

        self._running = True
        self._reconnect_count = 0
        ok = self._connect_ws()
        if ok:
            print(f"[FEED] ✅ WebSocket started — {len(self._subscriptions)} instruments subscribed")
        return ok

    def _connect_ws(self) -> bool:
        """Create a fresh DhanFeed/MarketFeed and start it. Reusable for reconnect."""
        # Build instrument list for MarketFeed
        instruments = [(ex, str(sid), rtype) for ex, sid, rtype in self._subscriptions]

        # Get fresh token
        token = self.dhan.access_token
        client_id = config.DHAN_CLIENT_ID

        try:
            if _DHAN_FEED_V2:
                ctx = _DhanContext(client_id, token)
                self._feed = MarketFeed(
                    dhan_context=ctx,
                    instruments=instruments,
                    version="v2",
                    on_connect=self._on_connect,
                    on_message=self._on_message,
                    on_close=self._on_close,
                    on_error=self._on_error,
                )
                self._ws_thread = self._feed.start()
            else:
                # DhanFeed v2.0.x: __init__ captures asyncio.get_event_loop(),
                # and run_forever() calls self.loop.run_until_complete().
                # Both MUST happen in a dedicated thread with its own event loop
                # to avoid "this event loop is already running" from uvicorn's loop.
                _on_msg = self._on_message_v1
                _parent = self

                def _run_forever_with_reconnect():
                    import asyncio
                    import time as _time

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        # Construct DhanFeed HERE so it captures this thread's loop
                        feed = MarketFeed(
                            client_id,
                            token,
                            instruments,
                            version="v2",
                        )
                        feed.on_ticks = _on_msg
                        _parent._feed = feed
                        # DhanFeed v2.0.x: run_forever() only connects + subscribes,
                        # it does NOT have an internal receive loop.
                        # We must poll get_data() ourselves.
                        feed.run_forever()
                        print("[FEED] ✅ WebSocket connected, starting receive loop")
                        while _parent._running:
                            try:
                                data = feed.get_data()
                                if data and _on_msg:
                                    _on_msg(data)
                            except Exception as recv_err:
                                if _looks_like_disconnect_error(recv_err):
                                    print(f"[FEED] ❌ WebSocket closed: {recv_err}")
                                    break
                                # Transient error — continue
                                print(f"[FEED] ⚠ recv error: {recv_err}")
                                _time.sleep(0.25)
                    except Exception as e:
                        print(f"[FEED] ❌ run_forever crashed: {e}")
                    finally:
                        loop.close()
                    # run_forever exited — means WS disconnected
                    if _parent._running:
                        print(f"[FEED] ⚠ run_forever exited at {_now_ist().strftime('%H:%M:%S')}")
                        _parent._schedule_reconnect()

                self._ws_thread = threading.Thread(target=_run_forever_with_reconnect, daemon=True)
                self._ws_thread.start()
            return True
        except Exception as e:
            print(f"[FEED] ❌ _connect_ws failed: {e}")
            return False

    def stop(self):
        """Stop the WebSocket feed."""
        self._running = False
        self._connected_at = None
        self._last_tick_time = None
        if self._feed:
            try:
                self._feed.close_connection()
            except Exception as e:
                print(f"[FEED] Close error: {e}")
            self._feed = None

        # Force-close all aggregators
        for agg in self._aggregators.values():
            agg.force_close()

        print("[FEED] 🛑 WebSocket stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_tick_age_seconds(self) -> float:
        """Seconds since the last real tick was received.

        Before the first market tick of the day, age is anchored to market open
        rather than the raw WebSocket connect time so pre-open connections don't
        look thousands of seconds stale at 09:16.
        """
        now = _now_ist()
        if self._last_tick_time is not None:
            return (now - self._last_tick_time).total_seconds()
        if self._connected_at is None:
            return -1.0
        session_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        if now < session_open:
            return -1.0
        anchor = self._connected_at if self._connected_at > session_open else session_open
        return max(0.0, (now - anchor).total_seconds())

    @staticmethod
    def _market_watch_open(now: datetime | None = None) -> bool:
        """09:00-15:45 IST on weekdays — when tick silence actually means stale.

        Outside this window a silent feed is what a CLOSED market sounds like.
        Before this gate, every health check after 15:45 read "no ticks for
        60s", declared the feed stale and reconnected — forever, every 30
        seconds, all night (Phil's 22:00 event log, 2026-08-13). 09:00 gives
        the feed its sanity check before the 09:15 open; 15:45 covers the
        close and the closing-auction tail.
        """
        moment = now or datetime.now(IST)
        if moment.weekday() >= 5:  # Saturday/Sunday
            return False
        minutes = moment.hour * 60 + moment.minute
        return 9 * 60 <= minutes <= 15 * 60 + 45

    def check_health(self) -> bool:
        """
        Returns True if the feed is healthy (received a tick within last 60s).
        If stale and running DURING market hours, triggers a reconnect.
        """
        if not self._running:
            return False
        if not self._market_watch_open():
            # No ticks because there is no market. Healthy, and no reconnect.
            return True
        age = self.last_tick_age_seconds
        if age < 0:
            # Never received a tick — give it time after start
            return True
        if age > 60:
            print(f"[FEED] ⚠ No ticks for {age:.0f}s — triggering reconnect")
            self._schedule_reconnect()
            return False
        return True

    # ── WebSocket Callbacks ───────────────────────────────────

    def _on_connect(self, ws):
        self._reconnecting = False
        self._connected_at = _now_ist()
        self._last_tick_time = None
        print(f"[FEED] ✅ WebSocket connected at {self._connected_at.strftime('%H:%M:%S')}")

    def _on_close(self, ws):
        print(f"[FEED] ⚠ WebSocket closed at {_now_ist().strftime('%H:%M:%S')}")
        if self._running:
            self._schedule_reconnect()

    def _on_error(self, ws, error):
        print(f"[FEED] ❌ WebSocket error: {error}")
        if self._running:
            self._schedule_reconnect()

    def _schedule_reconnect(self):
        """Schedule a reconnect in a background thread (with backoff)."""
        # Don't reconnect outside market hours (IST 09:00-15:35) to avoid tight loop
        now = _now_ist()
        if now.hour < 9 or (now.hour >= 15 and now.minute >= 35):
            with self._reconnect_lock:
                self._reconnecting = False
            # Schedule wake-up at next market open (09:00 IST)
            if now.hour >= 15:
                # After market close — wake up tomorrow at 09:00
                tomorrow = now + timedelta(days=1)
                wake_at = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
            else:
                # Before market open today
                wake_at = now.replace(hour=9, minute=0, second=0, microsecond=0)
            delay_secs = (wake_at - now).total_seconds()
            if delay_secs > 0:
                print(
                    f"[FEED] ⏸ Outside market hours ({now.strftime('%H:%M')}) — will reconnect at 09:00 IST (in {delay_secs / 3600:.1f}h)"
                )

                def _wake_up():
                    import time as _time

                    _time.sleep(delay_secs)
                    if self._running:
                        print("[FEED] ⏰ Market hours wake-up — reconnecting")
                        self._schedule_reconnect()

                t = threading.Thread(target=_wake_up, daemon=True)
                t.start()
            else:
                print(f"[FEED] ⏸ Outside market hours ({now.strftime('%H:%M')}) — skipping reconnect")
            return

        with self._reconnect_lock:
            if self._reconnecting:
                return
            self._reconnecting = True

        self._reconnect_count += 1
        delay = min(5 * self._reconnect_count, 30)  # 5s, 10s, 15s... max 30s
        print(f"[FEED] 🔄 Reconnecting in {delay}s (attempt #{self._reconnect_count})...")

        def _do_reconnect():
            import asyncio
            import time

            time.sleep(delay)
            if not self._running:
                return
            # Close old feed
            if self._feed:
                try:
                    self._feed.close_connection()
                except Exception:
                    pass
                self._feed = None

            # For v2.0.x DhanFeed: construct + run_forever in THIS thread
            # with a dedicated event loop (same pattern as _connect_ws).
            if not _DHAN_FEED_V2:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    instruments = [(ex, str(sid), rtype) for ex, sid, rtype in self._subscriptions]
                    token = self.dhan.access_token
                    client_id = config.DHAN_CLIENT_ID
                    feed = MarketFeed(client_id, token, instruments, version="v2")
                    feed.on_ticks = self._on_message_v1
                    self._feed = feed
                    self._reconnecting = False
                    # DhanFeed v2.0.x: run_forever() only connects + subscribes
                    feed.run_forever()
                    print(f"[FEED] ✅ Reconnected successfully (attempt #{self._reconnect_count})")
                    self._reconnect_count = 0
                    # Poll get_data() for messages
                    while self._running:
                        try:
                            data = feed.get_data()
                            if data and self._on_message_v1:
                                self._on_message_v1(data)
                        except Exception as recv_err:
                            if _looks_like_disconnect_error(recv_err):
                                print(f"[FEED] ❌ WebSocket closed: {recv_err}")
                                break
                            print(f"[FEED] ⚠ recv error: {recv_err}")
                            time.sleep(0.25)
                except Exception as e:
                    print(f"[FEED] ❌ Reconnect crashed: {e}")
                finally:
                    loop.close()
                # receive loop exited
                if self._running:
                    print(f"[FEED] ⚠ run_forever exited at {_now_ist().strftime('%H:%M:%S')}")
                    self._reconnecting = False
                    self._schedule_reconnect()
                return

            # v2.2.0+ MarketFeed path
            ok = self._connect_ws()
            if ok:
                print(f"[FEED] ✅ Reconnected successfully (attempt #{self._reconnect_count})")
                self._reconnect_count = 0
            else:
                self._reconnecting = False
                if self._running:
                    self._schedule_reconnect()

        t = threading.Thread(target=_do_reconnect, daemon=True)
        t.start()

    def _on_message_v1(self, data):
        """Adapter for v2.0.x DhanFeed on_ticks callback (no ws arg)."""
        self._on_message(None, data)

    def _on_message(self, ws, data):
        """Process incoming tick data from Dhan WebSocket."""
        if not data or not isinstance(data, dict):
            return

        # Extract security_id and LTP from the tick
        sec_id = data.get("security_id")
        ltp_raw = data.get("LTP", data.get("ltp"))
        if sec_id is None or ltp_raw is None:
            return

        try:
            sec_id = int(sec_id)
            ltp = float(ltp_raw)
        except (ValueError, TypeError):
            return

        if ltp <= 0:
            return

        now = _now_ist()
        self._last_tick_time = now

        # Update LTP cache (thread-safe)
        with self._lock:
            self._ltp_cache[sec_id] = {
                "ltp": ltp,
                "time": now,
                "type": data.get("type", "Ticker Data"),
            }

        # Feed into candle aggregators (for index instruments)
        # Aggregator keys are "{label}_{timeframe}m" — feed all matching this label
        label = self._index_sec_ids.get(sec_id)
        if label:
            vol = int(data.get("volume", 0))
            for agg_key, agg in self._aggregators.items():
                if agg_key.startswith(label + "_"):
                    agg.feed_tick(price=ltp, volume=vol, ts=now)

        # Fire tick callbacks
        for cb in self._tick_callbacks:
            try:
                cb(sec_id, ltp, now)
            except Exception as e:
                print(f"[FEED] Tick callback error: {e}")

    # ── Bootstrap with Historical Data ────────────────────────

    def bootstrap_history(self, instrument_id: str, timeframe: int, days: int = 7) -> pd.DataFrame:
        """
        Fetch historical candle data via REST API to seed indicators.
        Returns the DataFrame for external use too.

        For derived timeframes (3m, 30m, 45m, etc.), fetches the closest exact
        lower native Dhan interval and resamples to the target timeframe.
        """

        try:
            # Lazy import
            inst_map_fn = None
            try:
                from app import INSTRUMENT_MAP

                inst_map_fn = INSTRUMENT_MAP
            except ImportError:
                pass

            info = self.INDEX_MAP.get(instrument_id, {})
            dhan_id = info[0] if info else "13"
            dhan_seg = "IDX_I"
            dhan_type = "INDEX"

            if inst_map_fn:
                iinfo = inst_map_fn.get(instrument_id, {})
                dhan_id = iinfo.get("dhan_id", dhan_id)
                dhan_seg = iinfo.get("dhan_seg", dhan_seg)
                dhan_type = iinfo.get("dhan_type", dhan_type)

            from_date = (_now_ist() - timedelta(days=days)).strftime("%Y-%m-%d")
            to_date = _now_ist().strftime("%Y-%m-%d")

            fetch_tf = str(get_fetch_timeframe(timeframe))

            df_raw = self.dhan.get_historical_data(
                security_id=dhan_id,
                exchange_segment=dhan_seg,
                instrument_type=dhan_type,
                from_date=from_date,
                to_date=to_date,
                candle_type=fetch_tf,
            )

            if df_raw.empty:
                print(f"[FEED] Bootstrap returned empty data for {instrument_id}")
                return df_raw

            df_raw = drop_incomplete_candle(df_raw, int(fetch_tf), _now_ist())
            if df_raw.empty:
                print(f"[FEED] Bootstrap dropped incomplete latest candle for {instrument_id}")
                return df_raw

            # Resample if needed
            if not is_supported_timeframe(timeframe) and not df_raw.empty:
                df_raw = self._resample(df_raw, timeframe)

            self._bootstrap_done = True
            print(f"[FEED] Bootstrap: {len(df_raw)} candles @ {timeframe}m for instrument {instrument_id}")
            return df_raw

        except Exception as e:
            print(f"[FEED] Bootstrap error: {e}")
            return pd.DataFrame()

    @staticmethod
    def _resample(df: pd.DataFrame, tf_minutes: int) -> pd.DataFrame:
        """Resample a lower-timeframe DataFrame to a custom timeframe."""
        return resample_ohlcv(df, tf_minutes)


# ════════════════════════════════════════════════════════════════
#  Singleton Feed Manager (one per app)
# ════════════════════════════════════════════════════════════════

_global_feed: Optional[LiveMarketFeed] = None


def get_market_feed(dhan: DhanClient = None) -> LiveMarketFeed:
    """Get or create the global LiveMarketFeed instance."""
    global _global_feed
    if _global_feed is None:
        _global_feed = LiveMarketFeed(dhan)
    return _global_feed


def shutdown_feed():
    """Shutdown the global feed."""
    global _global_feed
    if _global_feed:
        _global_feed.stop()
        _global_feed = None
