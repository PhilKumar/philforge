"""
engine/paper_trading.py — Real Paper Trading Engine with Live Market Data
Uses actual live option chain data from Dhan to simulate trading with REAL prices.
No mock data - this is forward testing with actual market conditions.
Two modes:
  1. WebSocket mode (fast): LiveMarketFeed pushes ticks → candles aggregate live
     → conditions evaluated on candle close → ~1-2 second latency
  2. REST polling mode (fallback): polls Dhan REST API every N seconds
     → conditions evaluated per poll → ~30-90 second latency"""

import asyncio
import json as _json
import math
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from typing import Optional

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist() -> datetime:
    """Return current time in IST (naive datetime)."""
    return datetime.now(IST).replace(tzinfo=None)


import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from broker.dhan import DhanClient, ScripMaster
from engine.backtest import (
    decision_why,
    drain_cross_skips,
    eval_condition_group,
    get_lot_size,
    get_sell_option_margin_per_lot,
    get_strike_step,
    inspect_condition_group,
)
from engine.execution_profiles import resolve_execution_costs

try:
    from cascade_costs import calculate_nifty_option_round_costs
except Exception:  # pragma: no cover - the app always has it
    calculate_nifty_option_round_costs = None


def statutory_round_charges(*, entry_premium, exit_premium, quantity, lots, option_type) -> float:
    """Brokerage, STT, exchange fees, GST and stamp duty on one round trip.

    Neither of these engines charged a rupee of this until 2026-09-01: paper
    reported a gross number and live reported the same, so every figure on the
    console -- including the one being read to decide whether to go live --
    was better than the account would ever be. The cascade strategies have
    charged this all along, through the same schedule.

    Options only. The model is NSE's option schedule (STT on the SELL side of
    the premium, and so on); charging it to a cash equity leg would invent a
    different wrong number, so an equity leg is charged nothing here and says
    so rather than pretending.
    """
    if str(option_type or "").upper() not in ("CE", "PE"):
        return 0.0
    if calculate_nifty_option_round_costs is None:
        return 0.0
    try:
        buy, sell = (entry_premium, exit_premium)
        lots = max(1, int(lots or 1))
        return round(
            float(
                calculate_nifty_option_round_costs(
                    buy_price=float(buy),
                    sell_price=float(sell),
                    quantity=int(quantity),
                    lots_bought=lots,
                    lots_sold=lots,
                ).total
            ),
            2,
        )
    except Exception:
        # A cost model that cannot answer must not silently report zero cost.
        return 0.0


from engine.indicators import (
    compute_dynamic_indicators,
    infer_execution_timeframe,
    merge_indicator_context,
    normalize_strategy_indicators,
)
from engine.strike_utils import round_to_nearest_step
from engine.timeframes import (
    candle_close_time,
    describe_timeframe,
    drop_incomplete_candle,
    next_entry_ready_at,
    resample_ohlcv,
    resolve_strategy_timeframe,
)

# ── State File ────────────────────────────────────────────────
_STATE_DIR = os.path.dirname(os.path.dirname(__file__))
_DEFAULT_STATE_FILE = os.path.join(_STATE_DIR, "paper_state.json")
_NSE_CAPITAL_MARKET_HOLIDAYS = {
    "2024-01-26",
    "2024-03-08",
    "2024-03-25",
    "2024-03-29",
    "2024-04-11",
    "2024-04-17",
    "2024-05-01",
    "2024-06-17",
    "2024-07-17",
    "2024-08-15",
    "2024-10-02",
    "2024-11-01",
    "2024-11-15",
    "2024-12-25",
    "2025-02-26",
    "2025-03-14",
    "2025-03-31",
    "2025-04-10",
    "2025-04-14",
    "2025-04-18",
    "2025-05-01",
    "2025-08-15",
    "2025-08-27",
    "2025-10-02",
    "2025-10-21",
    "2025-10-22",
    "2025-11-05",
    "2025-12-25",
    "2026-01-26",
    "2026-03-03",
    "2026-03-26",
    "2026-03-31",
    "2026-04-03",
    "2026-04-14",
    "2026-05-01",
    "2026-05-28",
    "2026-06-26",
    "2026-09-14",
    "2026-10-02",
    "2026-10-20",
    "2026-11-10",
    "2026-11-24",
    "2026-12-25",
}


# Import INSTRUMENT_MAP lazily to avoid circular imports
def _get_instrument_map():
    from app import INSTRUMENT_MAP

    return INSTRUMENT_MAP


class PaperTradingEngine:
    """
    Paper trading engine that uses REAL live market data.
    - Fetches actual option chain prices from Dhan
    - Executes trades in paper mode (no real orders)
    - Records all trades for analysis
    - Can optionally save price data for historical backtesting later
    """

    def __init__(self, dhan: DhanClient = None, run_id: str = None, state_dir: str | None = None):
        self.dhan = dhan or DhanClient()
        self.running = False
        self.session_date = None
        self.run_id = run_id  # Unique ID for multi-engine support
        base_state_dir = state_dir or _STATE_DIR
        os.makedirs(base_state_dir, exist_ok=True)

        # Per-instance state file + persistent trade history
        if run_id:
            safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in run_id)
            self._state_file = os.path.join(base_state_dir, f"paper_state_{safe_id}.json")
            self._history_file = os.path.join(base_state_dir, f"paper_history_{safe_id}.json")
        else:
            self._state_file = os.path.join(base_state_dir, "paper_state.json")
            self._history_file = os.path.join(base_state_dir, "paper_history.json")

        # WebSocket feed (injected from app.py — if available, use event-driven mode)
        self._feed = None  # LiveMarketFeed instance
        self._ws_mode = False  # True when using WebSocket
        self._option_sec_id = None  # subscribed option security_id for LTP
        self._candle_event = None  # asyncio.Event set on each candle close
        self._latest_candle_df = None  # DataFrame from candle close callback
        self._latest_candle = None  # Last closed candle dict

        # Strategy configuration
        self.strategy = {}
        self.entry_conditions = []
        self.exit_conditions = []

        # Trading state
        self.in_trade = False
        self.positions = []  # List of open positions
        self.closed_trades = []  # Historical trades
        self.trades_today = 0
        self.daily_pnl = 0.0  # Realized P&L for today
        self.max_daily_loss = 0.0  # Will be set from strategy config
        # Date of the last session whose profit armed the skip-N-days cooldown
        self.profit_cooldown_trigger_date = None

        # Strategy-level SL/TP (₹ amounts)
        self.strat_sl_val = 0.0  # e.g. 13000 (20% of 250*260)
        self.strat_tp_val = 0.0  # e.g. 6600
        self.trade_entry_prem = 0.0  # entry premium for strategy-level PnL calc
        self.initial_capital = 500000.0
        self._enforce_capital = False
        self._capital_buffer_pct = 0.0
        self._sell_option_margin_per_lot = 0.0
        self.capital_rejections = 0
        self.last_capital_check = {}
        self.execution_profile = "auto"
        self._spread_bps = 0.0
        self._entry_slippage_bps = 0.0
        self._exit_slippage_bps = 0.0

        # Live data
        self.current_spot = 0.0
        self.current_time = None
        self.candle_buffer = pd.DataFrame()
        self._latest_raw_candles = pd.DataFrame()
        self._indicator_context_raw = pd.DataFrame()
        self.current_indicators = {}  # Latest indicator values for UI
        self.current_candle = {}  # Latest OHLCV candle for UI
        self._prev_row = None  # Previous candle row for crossover detection
        self._entry_signal_pending = False  # True = signal fired, enter on NEXT candle
        self._pending_entry_ready_at = None  # Earliest timestamp when the pending entry may execute
        self._pending_signal_candle_time = None
        self._signal_candle = None  # OHLC of the candle that triggered entry signal
        self._condition_debug = {}  # Last condition evaluation details for UI
        self._last_strategy_candle_time = None  # Last closed execution-timeframe candle seen

        # Logging
        self.event_log = []
        self.price_recordings = []  # Optional: record prices for later analysis

        # Restore last session (if server restarted while a run was active)
        self._load_state()

    def set_feed(self, feed):
        """Inject a LiveMarketFeed for WebSocket-driven mode."""
        self._feed = feed

    # ── STATE PERSISTENCE ─────────────────────────────────────
    def _save_state(self):
        """Persist current session state to disk so it survives restarts."""
        try:
            state = {
                "session_date": str(self.session_date) if self.session_date else None,
                # Full configuration — enough to reconstruct the engine on restore
                "strategy": self.strategy,
                "entry_conditions": self.entry_conditions,
                "exit_conditions": self.exit_conditions,
                # Legacy compat keys
                "strategy_name": self.strategy.get("run_name", ""),
                "instrument": self.strategy.get("instrument", ""),
                "in_trade": self.in_trade,
                "positions": self.positions,
                "closed_trades": self.closed_trades,
                "trades_today": self.trades_today,
                "daily_pnl": self.daily_pnl,
                "profit_cooldown_trigger_date": str(self.profit_cooldown_trigger_date)
                if self.profit_cooldown_trigger_date
                else None,
                "strat_sl_val": self.strat_sl_val,
                "strat_tp_val": self.strat_tp_val,
                "trade_entry_prem": self.trade_entry_prem,
                "capital_rejections": self.capital_rejections,
                "last_capital_check": self.last_capital_check,
                "execution_profile": self.execution_profile,
                "spread_bps": self._spread_bps,
                "entry_slippage_bps": self._entry_slippage_bps,
                "exit_slippage_bps": self._exit_slippage_bps,
                "current_spot": self.current_spot,
                "current_time": str(self.current_time) if self.current_time else None,
                "current_candle": self.current_candle,
                "current_indicators": {
                    k: (v if not isinstance(v, float) or not math.isnan(v) else None)
                    for k, v in self.current_indicators.items()
                },
                "event_log": [
                    {
                        "time": e["time"].strftime("%Y-%m-%d %H:%M:%S")
                        if isinstance(e["time"], datetime)
                        else str(e["time"]),
                        "type": e["type"],
                        "message": e["message"],
                    }
                    for e in self.event_log[-100:]
                ],
                "saved_at": _now_ist().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(self._state_file, "w") as f:
                _json.dump(state, f, indent=2, default=str)
        except Exception as e:
            print(f"[PAPER] State save failed: {e}")

    def _load_trade_history(self) -> list:
        """Load cumulative trade history from persistent file."""
        try:
            if os.path.exists(self._history_file):
                with open(self._history_file, "r") as f:
                    return _json.load(f)
        except Exception as e:
            print(f"[PAPER] Trade history load failed: {e}")
        return []

    def _save_trade_history(self, trades: list):
        """Append trades to persistent history file (deduplicates by entry_time+strike)."""
        try:
            existing = self._load_trade_history()
            # Build a set of existing trade keys for dedup
            seen = set()
            for t in existing:
                key = (str(t.get("entry_time", "")), str(t.get("strike", "")), str(t.get("option_type", "")))
                seen.add(key)
            # Append only new trades
            added = 0
            for t in trades:
                key = (str(t.get("entry_time", "")), str(t.get("strike", "")), str(t.get("option_type", "")))
                if key not in seen:
                    existing.append(t)
                    seen.add(key)
                    added += 1
            if added:
                with open(self._history_file, "w") as f:
                    _json.dump(existing, f, indent=2, default=str)
                print(f"[PAPER] Saved {added} trades to history ({len(existing)} total)")
        except Exception as e:
            print(f"[PAPER] Trade history save failed: {e}")

    def _load_state(self):
        """Load last session state from disk (called on __init__)."""
        try:
            if not os.path.exists(self._state_file):
                # Even without state file, load historical trades
                hist = self._load_trade_history()
                if hist:
                    self.closed_trades = hist
                    print(f"[PAPER] Loaded {len(hist)} historical trades from trade history")
                return
            with open(self._state_file, "r") as f:
                state = _json.load(f)

            saved_date = state.get("session_date")
            today = str(_now_ist().date())
            saved_positions = [
                position for position in (state.get("positions") or []) if (position or {}).get("status") != "closed"
            ]
            restoring_stale_positions = saved_date != today and bool(saved_positions)

            # The profit cooldown spans sessions by design — restore it even when the
            # rest of the state is stale, or a weekend restart would forget it.
            saved_strategy = state.get("strategy") or {}
            saved_skip_n = int(saved_strategy.get("skip_days_after_profit", 0) or 0)
            if saved_skip_n > 0:
                trigger_raw = state.get("profit_cooldown_trigger_date")
                if trigger_raw:
                    try:
                        self.profit_cooldown_trigger_date = date_type.fromisoformat(str(trigger_raw))
                    except ValueError:
                        pass
                if saved_date and saved_date != today:
                    saved_threshold = float(saved_strategy.get("skip_profit_threshold_rupees", 20000) or 20000)
                    if float(state.get("daily_pnl", 0) or 0) > saved_threshold:
                        try:
                            stale_session = date_type.fromisoformat(str(saved_date))
                            if (
                                self.profit_cooldown_trigger_date is None
                                or stale_session > self.profit_cooldown_trigger_date
                            ):
                                self.profit_cooldown_trigger_date = stale_session
                        except ValueError:
                            pass

            if saved_date != today and not restoring_stale_positions:
                # Stale session — save any closed trades to history before discarding
                stale_trades = state.get("closed_trades", [])
                if stale_trades:
                    self._save_trade_history(stale_trades)
                # Load all historical trades for display
                self.closed_trades = self._load_trade_history()
                n = len(self.closed_trades)
                print(f"[PAPER] Stale state from {saved_date} — loaded {n} historical trades")
                return

            # Restore fields
            self.session_date = _now_ist().date()
            self.positions = state.get("positions", [])
            open_positions = [position for position in self.positions if (position or {}).get("status") != "closed"]
            self.in_trade = bool(open_positions) or bool(state.get("in_trade", False))
            self.closed_trades = state.get("closed_trades", [])
            self.trades_today = state.get("trades_today", 0)
            self.daily_pnl = state.get("daily_pnl", 0.0)
            self.strat_sl_val = state.get("strat_sl_val", 0.0)
            self.strat_tp_val = state.get("strat_tp_val", 0.0)
            self.trade_entry_prem = state.get("trade_entry_prem", 0.0)
            self.capital_rejections = state.get("capital_rejections", 0)
            self.last_capital_check = state.get("last_capital_check", {}) or {}
            self.execution_profile = state.get("execution_profile", self.execution_profile)
            self._spread_bps = float(state.get("spread_bps", 0.0) or 0.0)
            self._entry_slippage_bps = float(state.get("entry_slippage_bps", 0.0) or 0.0)
            self._exit_slippage_bps = float(state.get("exit_slippage_bps", 0.0) or 0.0)
            self.current_spot = state.get("current_spot", 0.0)
            self.current_candle = state.get("current_candle", {})
            self.current_indicators = state.get("current_indicators", {})

            # Restore full strategy config (new format) or fallback to legacy keys
            if state.get("strategy"):
                self.strategy = state["strategy"]
            else:
                if state.get("strategy_name"):
                    self.strategy["run_name"] = state["strategy_name"]
                if state.get("instrument"):
                    self.strategy["instrument"] = state["instrument"]
            if state.get("entry_conditions"):
                self.entry_conditions = state["entry_conditions"]
            if state.get("exit_conditions"):
                self.exit_conditions = state["exit_conditions"]
            self.strategy = dict(self.strategy or {})
            self.strategy["indicators"] = normalize_strategy_indicators(
                self.strategy.get("indicators", []),
                entry_conditions=self.entry_conditions,
                exit_conditions=self.exit_conditions,
            )

            # Restore event log (convert time strings back to datetime)
            raw_log = state.get("event_log", [])
            for entry in raw_log:
                try:
                    t = datetime.strptime(entry["time"], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    t = _now_ist()
                self.event_log.append({"time": t, "type": entry["type"], "message": entry["message"], "data": {}})

            if restoring_stale_positions:
                self.trades_today = 0
                self.daily_pnl = 0.0
                if self._is_intraday_product(self.strategy):
                    self.current_time = _now_ist()
                    self.log_event(
                        "warning",
                        f"Stale intraday paper session restored from {saved_date} — closing {len(open_positions)} open position(s)",
                    )
                    for position in list(self.positions):
                        if position.get("status") == "closed":
                            continue
                        exit_px = self._safe_float(position.get("current_premium"), 0.0)
                        if exit_px <= 0:
                            exit_px = self._safe_float(position.get("entry_premium"), 0.0)
                        self._close_position(position, "MIS_SESSION_ROLLOVER", exit_px)
                    self._reset_intraday_status("restored_stale_intraday")
                    self._save_state()
                    print(
                        f"[PAPER] Restored stale intraday state from {saved_date}: "
                        f"{len(open_positions)} position(s) closed"
                    )
                else:
                    self.log_event(
                        "info",
                        f"Carry paper position restored from {saved_date} — daily counters reset, "
                        f"{len(open_positions)} position(s) remain open",
                    )
                    self._save_state()
                    print(
                        f"[PAPER] Restored carry state from {saved_date}: "
                        f"{len(open_positions)} open position(s) preserved"
                    )

            n_trades = len(self.closed_trades)
            n_pos = len(self.positions)
            pnl = sum(t.get("pnl", 0) for t in self.closed_trades)
            print(f"[PAPER] Restored state: {n_trades} trades, {n_pos} positions, P&L=₹{pnl:,.2f}")
        except Exception as e:
            print(f"[PAPER] State load failed: {e}")

    def _delete_state_file(self):
        """Remove state file (called when engine is manually stopped)."""
        try:
            if os.path.exists(self._state_file):
                os.remove(self._state_file)
        except Exception as e:
            print(f"[PAPER] State file delete failed: {e}")

    def configure(self, strategy: dict, entry_conditions: list, exit_conditions: list):
        """Configure the paper trading strategy"""
        self.entry_conditions = entry_conditions
        self.exit_conditions = exit_conditions
        strategy = dict(strategy or {})
        strategy["indicators"] = normalize_strategy_indicators(
            strategy.get("indicators", []),
            entry_conditions=entry_conditions,
            exit_conditions=exit_conditions,
        )
        self.strategy = strategy
        self._indicator_context_raw = pd.DataFrame()

        # Pre-compute strategy-level SL/TP values
        sl_pct = float(strategy.get("stoploss_pct", 0) or 0)
        sl_rupees = float(strategy.get("stoploss_rupees", 0) or 0)
        tp_pct = float(strategy.get("target_profit_pct", 0) or 0)
        tp_rupees = float(strategy.get("target_profit_rupees", 0) or 0)

        # These will be finalized when trade enters (needs entry premium)
        self._sl_pct = sl_pct
        self._sl_rupees = sl_rupees
        self._tp_pct = tp_pct
        self._tp_rupees = tp_rupees
        self.initial_capital = float(strategy.get("initial_capital", 500000.0) or 500000.0)
        # "auto" means the INSTRUMENT decides, not whatever basis points the
        # payload happens to carry. Three running strategies were under-costed
        # because a stored number outlived the profile that should have set it.
        costs = resolve_execution_costs(strategy)
        self.execution_profile = costs["execution_profile"]
        self._enforce_capital = bool(costs["enforce_capital"])
        self._capital_buffer_pct = min(99.0, costs["capital_buffer_pct"])
        self._sell_option_margin_per_lot = get_sell_option_margin_per_lot(
            strategy.get("instrument", "26000"),
            costs["sell_option_margin_per_lot"],
        )
        self._spread_bps = costs["spread_bps"]
        self._entry_slippage_bps = costs["entry_slippage_bps"]
        self._exit_slippage_bps = costs["exit_slippage_bps"]
        self.log_event(
            "info",
            f"Execution profile: {self.execution_profile} ({costs['profile_label']}) - "
            f"spread {self._spread_bps:g}bps, entry slip {self._entry_slippage_bps:g}bps, "
            f"exit slip {self._exit_slippage_bps:g}bps, capital check "
            f"{'ON' if self._enforce_capital else 'OFF'}",
        )
        # A CUSTOM PROFILE OF ZEROS IS FREE TRADING, and it does not announce
        # itself: PE_NoTarget ran seven trades at spread 0 / slip 0 and its
        # +Rs 13,975 was read as if the market had charged for them. Zero cost
        # is a legitimate thing to ask for -- but it must be visible in the
        # log the P&L is read beside, not buried in a saved profile.
        if not (self._spread_bps or self._entry_slippage_bps or self._exit_slippage_bps):
            self.log_event(
                "warning",
                "⚠ ZERO execution costs: no spread and no slippage are being charged, so this "
                "P&L is better than the account would be. Switch the profile to Auto for the "
                "instrument's measured numbers.",
            )

        self.log_event("info", f"Strategy configured: {strategy.get('run_name', 'Unnamed')}")
        self.log_event("info", f"Product: {self._product_type(strategy)}")
        if sl_rupees > 0 or sl_pct > 0:
            self.log_event("info", f"Strategy SL: ₹{sl_rupees:,.0f}" if sl_rupees > 0 else f"Strategy SL: {sl_pct}%")
        if tp_rupees > 0 or tp_pct > 0:
            self.log_event("info", f"Strategy TP: ₹{tp_rupees:,.0f}" if tp_rupees > 0 else f"Strategy TP: {tp_pct}%")

    @staticmethod
    def _format_missing_condition_gate(missing_fields: list[str]) -> str:
        preview = ", ".join(missing_fields[:3])
        extra = len(missing_fields) - 3
        if extra > 0:
            preview = f"{preview} +{extra} more"
        return f"missing_condition_data ({preview})"

    def _log_cross_skips(self, stage: str) -> None:
        """Say when a cross could not be decided, instead of failing silently.

        A cross with no usable previous bar is FALSE now, not a plain > or <.
        That is correct -- but it is also invisible: the rule simply stops
        firing and nothing says why. One line per evaluation that hit it,
        summarising the reasons, is enough to find the cause without filling
        the log on every bar.
        """
        skips = drain_cross_skips()
        if not skips:
            return
        reasons = sorted({skip["reason"] for skip in skips})
        fields = sorted({f"{skip['operator']} {skip['left']}/{skip['right']}" for skip in skips})[:4]
        self.log_event(
            "warning",
            f"{stage}: {len(skips)} cross condition(s) could not be decided and were treated as FALSE — "
            f"{'; '.join(reasons)} [{', '.join(fields)}]",
        )

    def _evaluate_entry_conditions_with_debug(self, latest_row, prev_row, now: datetime):
        if not self._signals_live(now):
            cutoff = self._signal_cutoff_time()
            return False, {
                "time": now.strftime("%H:%M:%S"),
                "overall": False,
                "raw_overall": False,
                "gate": f"signal_cutoff ({cutoff.strftime('%H:%M')})",
                "conditions": [],
            }
        raw_overall, cond_details, missing_fields = inspect_condition_group(latest_row, self.entry_conditions, prev_row)
        entry_triggered = raw_overall and not missing_fields
        self._log_cross_skips("entry")
        debug_payload = {
            "time": now.strftime("%H:%M:%S"),
            "overall": entry_triggered,
            "raw_overall": raw_overall,
            "gate": "evaluating" if not missing_fields else self._format_missing_condition_gate(missing_fields),
            "conditions": cond_details,
        }
        if missing_fields:
            debug_payload["missing_fields"] = missing_fields
        return entry_triggered, debug_payload

    def _position_unrealized_pnl(self, position: dict, current_premium: float | None = None) -> float:
        premium = float(
            current_premium
            if current_premium is not None
            else position.get("current_premium", position.get("entry_premium", 0.0)) or 0.0
        )
        direction = 1 if position.get("transaction_type") == "BUY" else -1
        quantity = self._position_quantity(position)
        return (premium - float(position.get("entry_premium", 0.0))) * direction * quantity

    def _portfolio_unrealized_pnl(self) -> float:
        return sum(
            self._position_unrealized_pnl(position) for position in self.positions if position.get("status") != "closed"
        )

    def _set_strategy_thresholds(self, positions: list[dict]):
        entry_notional = sum(
            float(position.get("entry_premium", 0.0)) * self._position_quantity(position) for position in positions
        )
        self.trade_entry_prem = round(entry_notional, 2)

        if self._sl_rupees > 0:
            self.strat_sl_val = self._sl_rupees
        elif self._sl_pct > 0:
            self.strat_sl_val = entry_notional * self._sl_pct / 100.0
        else:
            self.strat_sl_val = 0.0

        if self._tp_rupees > 0:
            self.strat_tp_val = self._tp_rupees
        elif self._tp_pct > 0:
            self.strat_tp_val = entry_notional * self._tp_pct / 100.0
        else:
            self.strat_tp_val = 0.0

    def _check_strategy_exit(self) -> Optional[str]:
        if not self.positions:
            return None
        if self.strat_sl_val <= 0 and self.strat_tp_val <= 0:
            return None

        portfolio_pnl = self._portfolio_unrealized_pnl()
        if self.strat_sl_val > 0 and portfolio_pnl <= -self.strat_sl_val:
            self.log_event("exit", f"Strategy SL hit: PnL ₹{portfolio_pnl:,.0f} <= -₹{self.strat_sl_val:,.0f}")
            return "STRATEGY_SL"
        if self.strat_tp_val > 0 and portfolio_pnl >= self.strat_tp_val:
            self.log_event("exit", f"Strategy TP hit: PnL ₹{portfolio_pnl:,.0f} >= ₹{self.strat_tp_val:,.0f}")
            return "STRATEGY_TP"
        return None

    @staticmethod
    def _position_quantity(position: dict) -> int:
        quantity = position.get("quantity")
        if quantity is not None:
            try:
                return max(0, int(round(float(quantity))))
            except Exception:
                pass
        try:
            return max(0, int(round(float(position.get("lots", 0)) * float(position.get("lot_size", 0)))))
        except Exception:
            return 0

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _normalize_product_type(product_type: str | None) -> str:
        normalized = str(product_type or "").strip().upper()
        if normalized in ("", "MIS", "INTRADAY"):
            return "MIS"
        if normalized in ("NRML", "NORMAL", "MARGIN"):
            return "NRML"
        return normalized

    def _product_type(self, strategy: dict | None = None) -> str:
        strategy = strategy if isinstance(strategy, dict) else (self.strategy or {})
        deploy_config = strategy.get("deploy_config", {}) or {}
        raw = deploy_config.get("product_type") or strategy.get("product_type")
        return self._normalize_product_type(raw)

    def _is_intraday_product(self, strategy: dict | None = None) -> bool:
        return self._product_type(strategy) == "MIS"

    def _signal_cutoff_time(self):
        """Clock after which SPOT is no longer real traded price.

        NSE's closing auction halts continuous trading in every F&O-eligible
        stock at 15:15, and every index constituent is one — so the index itself
        stops being priced by trades. Past this time no entry and no spot-driven
        exit may be decided. Stop-loss, target and the timed square-off are
        untouched: they read the option's own premium, and options run to 15:40.
        """
        from datetime import time as time_class

        raw = self.strategy.get("signal_cutoff_time") or ""
        if isinstance(raw, time_class):
            return raw
        if isinstance(raw, str) and raw.strip():
            h, m = map(int, raw.strip().split(":"))
            return time_class(h, m)
        return None

    def _signals_live(self, at: datetime | None = None) -> bool:
        cutoff = self._signal_cutoff_time()
        if cutoff is None:
            return True
        moment = at or self.current_time or _now_ist()
        return moment.time() < cutoff

    def _market_close_time(self):
        market_close = self.strategy.get("market_close", "15:25")
        from datetime import time as time_class

        if isinstance(market_close, str):
            h, m = map(int, market_close.split(":"))
            market_close = time_class(h, m)
        return market_close

    def _market_open_time(self):
        market_open = self.strategy.get("market_open", "09:15")
        from datetime import time as time_class

        if isinstance(market_open, str):
            h, m = map(int, market_open.split(":"))
            market_open = time_class(h, m)
        return market_open

    def _strategy_candle_is_current_session(
        self,
        strategy_candle_time: datetime | None,
        current_dt: datetime | None = None,
    ) -> bool:
        if strategy_candle_time is None:
            return False
        session_dt = current_dt or self.current_time or _now_ist()
        session_date = session_dt.date() if isinstance(session_dt, datetime) else self.session_date
        if not isinstance(session_date, date_type):
            session_date = _now_ist().date()
        return strategy_candle_time.date() == session_date

    def _strategy_candle_closes_in_session(self, strategy_candle_time) -> bool:
        if not self._strategy_candle_is_current_session(strategy_candle_time):
            return False
        close_time = candle_close_time(strategy_candle_time, self._get_timeframe_spec().requested)
        return self._market_open_time() < close_time.time() <= self._market_close_time()

    def _entry_can_be_scheduled(self, signal_candle_time) -> bool:
        if not self._strategy_candle_is_current_session(signal_candle_time):
            return False
        ready_at = next_entry_ready_at(signal_candle_time, self._get_timeframe_spec().requested)
        return ready_at.date() == signal_candle_time.date() and ready_at.time() <= self._market_close_time()

    @staticmethod
    def _is_closed_market_day(target_date: date_type | None) -> bool:
        if target_date is None:
            return False
        return target_date.weekday() >= 5 or target_date.isoformat() in _NSE_CAPITAL_MARKET_HOLIDAYS

    def _profit_cooldown_config(self) -> tuple[int, float]:
        strategy = self.strategy or {}
        n = max(0, int(strategy.get("skip_days_after_profit", 0) or 0))
        threshold = float(strategy.get("skip_profit_threshold_rupees", 20000) or 20000)
        return n, threshold

    def _arm_profit_cooldown(self):
        n, threshold = self._profit_cooldown_config()
        if n <= 0 or self.daily_pnl <= threshold:
            return
        session = self.session_date or _now_ist().date()
        if self.profit_cooldown_trigger_date != session:
            self.profit_cooldown_trigger_date = session
            self.log_event(
                "info",
                f"🧊 Profit cooldown armed: ₹{self.daily_pnl:,.0f} today > ₹{threshold:,.0f} — "
                f"skipping the next {n} trading session(s)",
            )

    def _profit_cooldown_active(self, target_date: date_type | None = None) -> bool:
        n, _ = self._profit_cooldown_config()
        trigger = self.profit_cooldown_trigger_date
        if n <= 0 or trigger is None:
            return False
        day = target_date or self.session_date or _now_ist().date()
        if day <= trigger:
            return False
        # Count trading sessions strictly after the trigger day, up to `day`
        sessions = 0
        cursor = trigger
        while cursor < day and sessions <= n and (cursor - trigger).days <= 30:
            cursor += timedelta(days=1)
            if not self._is_closed_market_day(cursor):
                sessions += 1
        return 0 < sessions <= n

    async def _resolve_stale_positions(
        self,
        current_dt: datetime,
        reason: str,
        context_label: str,
        callback=None,
    ) -> bool:
        changed = False
        if self._entry_signal_pending:
            self.log_event("info", f"Pending entry cleared during {context_label.lower()}")
            self._clear_pending_entry()
            changed = True

        if not self.positions:
            if changed:
                self._save_state()
            return changed
        if not self._is_intraday_product():
            if changed:
                self._save_state()
            return changed

        self.log_event(
            "warning",
            f"{context_label} — resolving {len(self.positions)} stale paper position(s)",
        )

        self.current_time = current_dt
        for position in list(self.positions):
            if position.get("status") == "closed":
                continue

            exit_px = self._safe_float(position.get("current_premium"), 0.0)
            if exit_px <= 0:
                exit_px = self._safe_float(self._get_premium_from_feed(position), 0.0)
            if exit_px <= 0:
                try:
                    exit_px = self._safe_float(await self._get_current_premium(position), 0.0)
                except Exception as exc:
                    self.log_event(
                        "warning",
                        f"{context_label} premium fetch failed for Leg {position.get('leg_num')}: {exc}",
                    )
            if exit_px <= 0:
                exit_px = self._safe_float(position.get("entry_premium"), 0.0)

            self._close_position(position, reason, exit_px)

        if callback:
            await self._emit_callback(callback, {"type": "status", "message": context_label})
        return True

    async def _force_market_close_if_needed(self, current_dt: datetime, callback=None) -> bool:
        market_close = self._market_close_time()
        if current_dt.time() < market_close:
            return False

        if self._entry_signal_pending:
            self.log_event("info", "Pending entry cleared at market close")
            self._clear_pending_entry()

        if not self.positions:
            return False
        if not self._is_intraday_product():
            return False

        self.log_event(
            "warning",
            f"⏰ Market close reached ({market_close.strftime('%H:%M')}) — force closing {len(self.positions)} open position(s)",
        )

        for position in list(self.positions):
            if position.get("status") == "closed":
                continue

            exit_px = self._safe_float(position.get("current_premium"), 0.0)
            if exit_px <= 0:
                exit_px = self._safe_float(self._get_premium_from_feed(position), 0.0)
            if exit_px <= 0:
                try:
                    exit_px = self._safe_float(await self._get_current_premium(position), 0.0)
                except Exception as exc:
                    self.log_event(
                        "warning", f"Market-close premium fetch failed for Leg {position.get('leg_num')}: {exc}"
                    )
            if exit_px <= 0:
                exit_px = self._safe_float(position.get("entry_premium"), 0.0)

            self._close_position(position, "MARKET_CLOSE", exit_px)

        if callback:
            await self._emit_callback(callback, {"type": "status", "message": "Market closed — positions squared off"})
        return True

    def _apply_execution_costs(self, price: float, transaction_type: str, stage: str) -> float:
        base_price = max(0.05, float(price or 0.0))
        half_spread = self._spread_bps / 20000.0
        slip_bps = self._entry_slippage_bps if stage == "entry" else self._exit_slippage_bps
        adverse_move = half_spread + (slip_bps / 10000.0)
        if transaction_type == "BUY":
            multiplier = 1.0 + adverse_move if stage == "entry" else max(0.0, 1.0 - adverse_move)
        else:
            multiplier = max(0.0, 1.0 - adverse_move) if stage == "entry" else 1.0 + adverse_move
        return max(0.05, round(base_price * multiplier, 4))

    def _capital_required_for_entry(
        self, transaction_type: str, entry_premium: float, lots: int, lot_size: int
    ) -> float:
        if transaction_type == "SELL":
            return float(lots) * self._sell_option_margin_per_lot
        quantity = int(round(float(lots) * float(lot_size)))
        return max(0.0, float(entry_premium)) * float(quantity)

    def _capital_limit(self, available_capital: float) -> float:
        return max(0.0, float(available_capital)) * max(0.0, 1.0 - self._capital_buffer_pct / 100.0)

    def _paper_available_capital(self) -> float:
        return max(0.0, self.initial_capital + float(self.daily_pnl))

    def _can_enter_trade(self, planned_positions: list[dict]) -> bool:
        if not self._enforce_capital:
            self.last_capital_check = {"enforced": False, "passed": True}
            return True

        required_capital = sum(
            self._capital_required_for_entry(
                position.get("transaction_type", "BUY"),
                float(position.get("entry_premium", 0.0) or 0.0),
                int(position.get("lots", 0) or 0),
                int(position.get("lot_size", 0) or 0),
            )
            for position in planned_positions
        )
        available_capital = self._paper_available_capital()
        capital_limit = self._capital_limit(available_capital)
        passed = required_capital <= capital_limit + 1e-9
        self.last_capital_check = {
            "enforced": True,
            "passed": passed,
            "required": round(required_capital, 2),
            "available": round(available_capital, 2),
            "limit": round(capital_limit, 2),
            "buffer_pct": self._capital_buffer_pct,
            "mode": "paper",
        }
        if not passed:
            self.capital_rejections += 1
            self.log_event(
                "warning",
                f"Capital check blocked paper entry: required ₹{required_capital:,.0f} > usable ₹{capital_limit:,.0f}",
            )
            return False
        return True

    def log_event(self, event_type: str, message: str, data: dict = None):
        """Log an event with timestamp"""
        event = {"time": _now_ist(), "type": event_type, "message": message, "data": data or {}}
        self.event_log.append(event)
        timestamp = event["time"].strftime("%H:%M:%S")
        print(f"[PAPER] [{timestamp}] [{event_type.upper()}] {message}")

    async def _emit_callback(self, callback, event: dict):
        """Emit callback, handling both async and sync functions"""
        if not callback:
            return
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)
        except Exception as e:
            print(f"[PAPER] Callback error: {e}")

    def _compute_candle_latency(self, candle_time, now: Optional[datetime] = None) -> float:
        """Normalize candle latency around session boundaries."""
        if not isinstance(candle_time, datetime):
            return 0.0
        now = now or _now_ist()
        market_open = self.strategy.get("market_open", "09:15")
        if isinstance(market_open, str):
            hour, minute = map(int, market_open.split(":"))
        else:
            hour, minute = market_open.hour, market_open.minute
        session_open = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candle_time.date() != now.date() or candle_time < session_open:
            candle_time = session_open
        return max(0.0, (now - candle_time).total_seconds())

    def _prepare_ws_strategy_frame(
        self,
        candle_df: pd.DataFrame,
        indicators: list,
        execution_timeframe: int,
        fetch_timeframe: int,
        now: datetime,
    ) -> pd.DataFrame:
        """Drop any still-forming strategy candle before WS signal evaluation."""
        candle_df = merge_indicator_context(candle_df, self._indicator_context_raw, max_rows=800)
        df_with_indicators = compute_dynamic_indicators(
            candle_df,
            indicators,
            default_timeframe_minutes=execution_timeframe,
            source_timeframe_minutes=fetch_timeframe,
            execution_timeframe_minutes=execution_timeframe,
        )
        if df_with_indicators.empty:
            return df_with_indicators
        return drop_incomplete_candle(df_with_indicators, execution_timeframe, now)

    def _remember_indicator_context(self, raw_df: pd.DataFrame, *, max_rows: int = 800) -> None:
        if not isinstance(raw_df, pd.DataFrame) or raw_df.empty:
            return
        merged = pd.concat([self._indicator_context_raw, raw_df]).sort_index()
        merged = merged[~merged.index.duplicated(keep="last")]
        if max_rows > 0 and len(merged) > max_rows:
            merged = merged.tail(max_rows)
        self._indicator_context_raw = merged.copy()

    def _fetch_raw_history(self, instrument: str, fetch_timeframe: int, *, days: int = 7) -> pd.DataFrame:
        from_date = (_now_ist() - timedelta(days=days)).strftime("%Y-%m-%d")
        to_date = _now_ist().strftime("%Y-%m-%d")
        inst_map = _get_instrument_map()
        inst_info = inst_map.get(instrument, {})
        df_raw = self.dhan.get_historical_data(
            security_id=inst_info.get("dhan_id", "13"),
            exchange_segment=inst_info.get("dhan_seg", "IDX_I"),
            instrument_type=inst_info.get("dhan_type", "INDEX"),
            from_date=from_date,
            to_date=to_date,
            candle_type=str(fetch_timeframe),
        )
        self._remember_indicator_context(df_raw)
        return df_raw

    def _get_ws_history_seed(self, instrument: str, fetch_timeframe: int, *, days: int = 7) -> pd.DataFrame:
        history_df = self._feed.bootstrap_history(instrument, fetch_timeframe, days=days)
        if not history_df.empty:
            self._remember_indicator_context(history_df)
            return history_df
        self.log_event("warning", "WS bootstrap returned no history; using direct historical warm-up fetch")
        return self._fetch_raw_history(instrument, fetch_timeframe, days=days)

    def _arm_pending_entry(self, signal_candle_time: datetime, latest_row: pd.Series) -> None:
        self._entry_signal_pending = True
        self._pending_signal_candle_time = signal_candle_time
        self._pending_entry_ready_at = next_entry_ready_at(signal_candle_time, self._get_timeframe_spec().requested)
        self._signal_candle = {
            "Signal_Candle_Open": float(latest_row["open"]),
            "Signal_Candle_High": float(latest_row["high"]),
            "Signal_Candle_Low": float(latest_row["low"]),
            "Signal_Candle_Close": float(latest_row["close"]),
        }

    def _clear_pending_entry(self) -> None:
        self._entry_signal_pending = False
        self._pending_entry_ready_at = None
        self._pending_signal_candle_time = None
        self._signal_candle = None

    def _pending_entry_is_current_session(self, now: datetime) -> bool:
        if not self._strategy_candle_is_current_session(self._pending_signal_candle_time, now):
            return False
        if isinstance(self._pending_entry_ready_at, datetime) and self._pending_entry_ready_at.date() != now.date():
            return False
        return True

    def _pending_entry_is_ready(self, now: datetime) -> bool:
        return (
            self._pending_entry_ready_at is not None
            and self._pending_entry_is_current_session(now)
            and now >= self._pending_entry_ready_at
        )

    def _reset_intraday_status(self, gate: str = "waiting_for_first_candle") -> None:
        """Clear stale UI/evaluation state when a session starts or a new day rolls over."""
        self.current_spot = 0.0
        self.current_time = None
        self.candle_buffer = pd.DataFrame()
        self._latest_raw_candles = pd.DataFrame()
        self.current_candle = {}
        self.current_indicators = {}
        self._prev_row = None
        self._last_strategy_candle_time = None
        self._clear_pending_entry()
        self._condition_debug = {"gate": gate, "conditions": []}

    async def start(self, callback=None):
        """Start the paper trading engine"""
        self.running = True
        self.session_date = _now_ist().date()
        self.daily_pnl = 0.0
        self.max_daily_loss = float(self.strategy.get("max_daily_loss", 0) or 0)
        self._reset_intraday_status()
        self.log_event("start", "🚀 Paper Trading Engine Started (LIVE DATA MODE)")
        self.log_event("info", f"Instrument: {self._get_instrument_name()}")
        self.log_event("info", f"Timeframe: {describe_timeframe(self._get_timeframe_spec())}")
        self.log_event("info", f"Max trades/day: {self.strategy.get('max_trades_per_day', 1)}")
        if self.max_daily_loss > 0:
            self.log_event("info", f"Max daily loss: ₹{self.max_daily_loss:,.0f}")

        # ── Choose mode: WebSocket (fast) or REST polling (fallback) ──
        self._ws_mode = self._feed is not None and self._feed.is_running

        if self._ws_mode:
            self.log_event("info", "⚡ Mode: WebSocket (event-driven, ~1-2s latency)")
            await self._run_ws_mode(callback)
        else:
            self.log_event("info", "🔄 Mode: REST polling (fallback, ~30-60s latency)")
            poll_interval = self.strategy.get("poll_interval", 10)
            while self.running:
                try:
                    await self._tick(callback)
                except Exception as e:
                    self.log_event("error", f"Tick error: {str(e)}")
                await asyncio.sleep(poll_interval)

    def stop(self, *, close_positions: bool = True):
        """Stop the paper trading engine.

        `close_positions=False` SUSPENDS instead: the run goes quiet with its
        position intact so a restart can pick it up where it left off.

        Phil, 2026-08-17. A deploy restarted the app at 09:58 and this method
        force-closed his open PE at the last quote it happened to hold, booked
        it as ENGINE_STOP, and spent the day's single allowed entry -- a trade
        ended by a deploy, not by his strategy. The live engine has always got
        this right: it leaves the position alone and `_restore_live_engines`
        picks it back up. Paper has the same state file and the same restore
        (`_restore_paper_engines`); all it lacked was a way to be told the
        process is going away rather than that the user pressed Stop.
        """
        self.running = False

        if not close_positions:
            if self.positions:
                self.log_event(
                    "stop",
                    f"⏸ Suspended with {len(self.positions)} open position(s) — resuming after restart",
                )
            self._save_state()
            return

        # Close all open positions
        if self.positions:
            self.log_event("warning", f"Force closing {len(self.positions)} open positions")
            for pos in self.positions:
                # Use last known option LTP, NOT the spot price
                exit_px = pos.get("current_premium") or pos.get("entry_premium", 0)
                self._close_position(pos, "ENGINE_STOP", exit_px)

        # Final summary
        total_pnl = sum(t["pnl"] for t in self.closed_trades)
        win_trades = len([t for t in self.closed_trades if t["pnl"] > 0])

        self.log_event("stop", "🛑 Paper Trading Engine Stopped")
        self.log_event(
            "info", f"Trades: {len(self.closed_trades)} | Winners: {win_trades} | Total P&L: ₹{total_pnl:,.2f}"
        )
        self._save_state()  # Persist final state

    # ── WebSocket Event-Driven Mode ───────────────────────────
    async def _run_ws_mode(self, callback=None):
        """
        WebSocket-driven loop: waits for candle-close events instead of polling.
        On each candle close:
          1. Compute indicators on the candle DataFrame
          2. If in trade: check exit conditions using feed's instant LTP
          3. If not in trade: check entry conditions
        Between candles: monitor positions every 1s using cached LTP.
        """
        from engine.indicators import compute_dynamic_indicators

        instrument = self.strategy.get("instrument", "26000")
        tf_spec = self._get_timeframe_spec()
        execution_timeframe = tf_spec.requested
        fetch_timeframe = tf_spec.fetch

        # Set up candle-close event (asyncio-safe from thread)
        loop = asyncio.get_event_loop()
        self._candle_event = asyncio.Event()

        def _on_candle_close(df, candle):
            """Called from WebSocket thread when a candle closes."""
            self._latest_candle_df = df
            self._latest_candle = candle
            self._remember_indicator_context(df)
            self._latest_raw_candles = merge_indicator_context(df, self._indicator_context_raw, max_rows=500).copy()
            # Set asyncio event from the WS thread
            loop.call_soon_threadsafe(self._candle_event.set)

        # Configure candle aggregation on the feed
        # First bootstrap with historical data for indicator warm-up
        history_df = self._get_ws_history_seed(instrument, fetch_timeframe, days=7)
        indicators = self.strategy.get("indicators", [])

        self._feed.set_candle_config(
            instrument_id=instrument,
            timeframe=fetch_timeframe,
            callback=_on_candle_close,
            history_df=history_df,
        )

        if tf_spec.mixed or tf_spec.derived:
            agg_label = f"{fetch_timeframe}m raw -> {execution_timeframe}m strategy"
        else:
            agg_label = f"{execution_timeframe}m"
        self.log_event("info", f"📊 Candle aggregation: {agg_label} (including {len(history_df)} historical candles)")

        # ── Immediately populate UI data from bootstrap history ──
        if not history_df.empty:
            try:
                self._remember_indicator_context(history_df)
                self._latest_raw_candles = history_df.tail(500).copy()
                df_init = compute_dynamic_indicators(
                    merge_indicator_context(history_df.copy(), self._indicator_context_raw, max_rows=800),
                    indicators,
                    default_timeframe_minutes=execution_timeframe,
                    source_timeframe_minutes=fetch_timeframe,
                    execution_timeframe_minutes=execution_timeframe,
                )
                if not df_init.empty:
                    self.candle_buffer = df_init
                    self.current_spot = float(df_init.iloc[-1].get("close", 0))
                    self._last_strategy_candle_time = df_init.index[-1]
                    self._update_ui_data(df_init.iloc[-1])
                    self.log_event("info", f"📈 Initial UI data: spot={self.current_spot:.2f}")
            except Exception as e:
                self.log_event("warning", f"Bootstrap UI init failed: {e}")

        # Main event loop
        while self.running:
            try:
                now = _now_ist()
                self.current_time = now

                # Check if new day
                if now.date() != self.session_date:
                    await self._resolve_stale_positions(now, "SESSION_ROLLOVER", "Session rollover", callback)
                    self.trades_today = 0
                    self.daily_pnl = 0.0
                    self.session_date = now.date()
                    self._reset_intraday_status()
                    self.log_event("info", f"📅 New trading day: {self.session_date}")
                    # PERSIST THE ROLLOVER. The date moved in memory only, so a
                    # flat engine's file still read yesterday -- and the restore
                    # skips a stale file that holds no position. PhilForge was
                    # stopped at 15:32 on 2026-08-21 for a Dhan backfill and
                    # started again at 00:33, 33 minutes past midnight: all three
                    # paper engines were dropped as stale and vanished from the
                    # Live page. Saving here costs one write a day.
                    self._save_state()

                # Check market hours
                from datetime import time as time_class

                market_open = self.strategy.get("market_open", "09:15")
                market_close = self.strategy.get("market_close", "15:25")
                if isinstance(market_open, str):
                    h, m = map(int, market_open.split(":"))
                    market_open = time_class(h, m)
                if isinstance(market_close, str):
                    h, m = map(int, market_close.split(":"))
                    market_close = time_class(h, m)

                if self._is_closed_market_day(now.date()):
                    await self._resolve_stale_positions(now, "NON_TRADING_DAY", "Market closed today", callback)
                    await asyncio.sleep(5)
                    if callback:
                        await self._emit_callback(callback, {"type": "status", "message": "Outside market hours"})
                    continue

                after_market_close = now.time() >= market_close
                allow_final_candle_grace = now <= now.replace(
                    hour=market_close.hour,
                    minute=market_close.minute,
                    second=2,
                    microsecond=0,
                )

                if after_market_close and not allow_final_candle_grace and not self._candle_event.is_set():
                    await self._force_market_close_if_needed(now, callback)
                    await asyncio.sleep(5)
                    if callback:
                        await self._emit_callback(callback, {"type": "status", "message": "Outside market hours"})
                    continue

                if now.time() < market_open:
                    await asyncio.sleep(5)
                    if callback:
                        await self._emit_callback(callback, {"type": "status", "message": "Outside market hours"})
                    continue

                # Update current spot from feed cache
                from engine.market_feed import LiveMarketFeed

                idx_info = LiveMarketFeed.INDEX_MAP.get(instrument)
                if idx_info:
                    spot = self._feed.get_ltp(int(idx_info[0]))
                    if spot > 0:
                        self.current_spot = spot
                        # Keep UI candle fresh between closes
                        if self.current_candle:
                            self.current_candle["close"] = spot
                            self.current_candle["updated_at"] = _now_ist().strftime("%Y-%m-%d %I:%M:%S %p")
                            if spot > self.current_candle.get("high", 0):
                                self.current_candle["high"] = spot
                            if spot < self.current_candle.get("low", float("inf")):
                                self.current_candle["low"] = spot

                # ── Monitor positions (fast: every 1 second) ──
                if self.in_trade:
                    for position in list(self.positions):
                        if position["status"] == "closed":
                            continue
                        # Get LTP from feed cache (instant, no API call)
                        current_premium = self._get_premium_from_feed(position)
                        # REST fallback every ~5s if WS has no data yet
                        if current_premium <= 0:
                            rest_counter = position.get("_rest_counter", 0) + 1
                            position["_rest_counter"] = rest_counter
                            if rest_counter % 5 == 1:  # First try + every 5 iterations
                                try:
                                    symbol_name = self._get_symbol_name()
                                    current_premium = (
                                        await self.dhan.async_get_option_ltp(
                                            symbol_name,
                                            int(position["strike"]),
                                            position["expiry"],
                                            position["option_type"],
                                        )
                                        or 0.0
                                    )
                                except Exception:
                                    current_premium = 0.0
                        if current_premium > 0:
                            position["current_premium"] = current_premium
                            direction = 1 if position["transaction_type"] == "BUY" else -1
                            position["unrealized_pnl"] = (
                                (current_premium - position["entry_premium"])
                                * direction
                                * position["lots"]
                                * position["lot_size"]
                            )

                    latest_row = self.candle_buffer.iloc[-1] if not self.candle_buffer.empty else None
                    if latest_row is not None:
                        strategy_exit = self._check_strategy_exit()
                        if strategy_exit:
                            for position in list(self.positions):
                                if position.get("status") == "closed":
                                    continue
                                self._close_position(position, strategy_exit, position.get("current_premium", 0.0))
                        else:
                            for position in list(self.positions):
                                if position.get("status") == "closed":
                                    continue
                                # Between candle closes, only intrabar/price exits should fire.
                                # Never evaluate full signal-exit logic on the same closed row that
                                # just armed/executed the pending entry, or a fresh trade can close
                                # immediately as EXIT_SIGNAL.
                                exit_triggered = self._check_exit_conditions(
                                    position,
                                    latest_row,
                                    position["current_premium"],
                                    allow_signal_exit=False,
                                )
                                if exit_triggered:
                                    self._close_position(position, exit_triggered, position["current_premium"])

                # ── Execute pending entry only after the next candle boundary ──
                pending_now = _now_ist()
                if self._entry_signal_pending and not self.in_trade:
                    if not self._pending_entry_is_current_session(pending_now):
                        self.log_event("info", "Pending entry cleared — stale session signal")
                        self._clear_pending_entry()
                        if callback:
                            await self._emit_callback(callback, self.get_status())
                        continue
                    if self._pending_entry_is_ready(pending_now):
                        if pending_now.time() >= market_close:
                            self.log_event("info", "Pending entry cleared at session end")
                            self._clear_pending_entry()
                            if callback:
                                await self._emit_callback(callback, self.get_status())
                            continue
                        max_trades = self.strategy.get("max_trades_per_day", 1)
                        daily_loss_hit = self.max_daily_loss > 0 and self.daily_pnl <= -self.max_daily_loss
                        if self.trades_today < max_trades and not daily_loss_hit and not self._profit_cooldown_active():
                            self._clear_pending_entry()
                            latest_row = self.candle_buffer.iloc[-1] if not self.candle_buffer.empty else None
                            if latest_row is not None:
                                self.log_event(
                                    "entry",
                                    f"🚀 Executing pending entry at {pending_now.strftime('%H:%M:%S')} (next candle open)",
                                )
                                await self._enter_trade(latest_row)
                        if callback:
                            await self._emit_callback(callback, self.get_status())
                        continue

                # ── Wait for candle close event (with timeout for position monitoring) ──
                try:
                    await asyncio.wait_for(self._candle_event.wait(), timeout=1.0)
                    self._candle_event.clear()
                except asyncio.TimeoutError:
                    # Periodic feed health check — detect dead WebSocket and reconnect
                    if self._feed and hasattr(self._feed, "last_tick_age_seconds"):
                        age = self._feed.last_tick_age_seconds
                        if age > 60 and int(age) % 60 < 2:
                            self.log_event("warning", f"⚠ No ticks for {age:.0f}s — feed may be dead, checking health")
                            self._feed.check_health()

                    if callback:
                        await self._emit_callback(callback, self.get_status())
                    continue

                # ── Candle closed! Evaluate conditions ──
                candle_df = self._latest_candle_df
                if candle_df is None or candle_df.empty:
                    continue

                now = _now_ist()
                self.current_time = now
                # Compute indicators only on fully closed strategy candles
                df_with_indicators = self._prepare_ws_strategy_frame(
                    candle_df,
                    indicators,
                    execution_timeframe,
                    fetch_timeframe,
                    now,
                )
                self.candle_buffer = df_with_indicators

                if df_with_indicators.empty:
                    continue

                latest_row = df_with_indicators.iloc[-1]
                strategy_candle_time = latest_row.name
                if self._last_strategy_candle_time == strategy_candle_time:
                    if now.time() >= market_close:
                        await self._force_market_close_if_needed(now, callback)
                    if callback:
                        await self._emit_callback(callback, self.get_status())
                    continue

                self.current_spot = float(latest_row.get("close", self.current_spot))

                # Store candle + indicators for UI
                self._update_ui_data(latest_row)

                candle_close_time = strategy_candle_time + timedelta(minutes=execution_timeframe)
                latency = self._compute_candle_latency(candle_close_time, now)
                self.log_event(
                    "candle",
                    f"🕯️ {execution_timeframe}m candle @ {self.current_spot:.2f} (latency: {latency:.1f}s)",
                )

                candle_in_session = self._strategy_candle_closes_in_session(strategy_candle_time)
                if (
                    not candle_in_session
                    and not self.in_trade
                    and not self._strategy_candle_is_current_session(strategy_candle_time, now)
                ):
                    self._condition_debug = {"gate": "waiting_for_first_candle", "conditions": []}

                if candle_in_session and self.in_trade:
                    strategy_exit = self._check_strategy_exit()
                    if strategy_exit:
                        for position in list(self.positions):
                            if position.get("status") == "closed":
                                continue
                            self._close_position(position, strategy_exit, position.get("current_premium", 0.0))
                    else:
                        for position in list(self.positions):
                            if position.get("status") == "closed":
                                continue
                            current_premium = position.get("current_premium", 0.0)
                            exit_triggered = self._check_exit_conditions(position, latest_row, current_premium)
                            if exit_triggered:
                                self._close_position(position, exit_triggered, current_premium)

                # Check entry conditions
                max_trades = self.strategy.get("max_trades_per_day", 1)
                daily_loss_hit = self.max_daily_loss > 0 and self.daily_pnl <= -self.max_daily_loss
                cooldown_hit = self._profit_cooldown_active()

                if (
                    not self.in_trade
                    and self.trades_today < max_trades
                    and not daily_loss_hit
                    and not cooldown_hit
                    and candle_in_session
                ):
                    # Execute pending signal from previous candle (enter on THIS candle's open)
                    if self._entry_signal_pending:
                        if not self._pending_entry_is_current_session(now):
                            self.log_event("info", "Pending entry cleared — stale session signal")
                            self._clear_pending_entry()
                        elif self._pending_entry_is_ready(now):
                            self._clear_pending_entry()
                            self.log_event(
                                "entry", f"🚀 Executing pending entry at {now.strftime('%H:%M:%S')} (next candle open)"
                            )
                            await self._enter_trade(latest_row)
                    else:
                        prev_row = df_with_indicators.iloc[-2] if len(df_with_indicators) >= 2 else None
                        entry_triggered, self._condition_debug = self._evaluate_entry_conditions_with_debug(
                            latest_row,
                            prev_row,
                            now,
                        )
                        if entry_triggered:
                            if self._entry_can_be_scheduled(strategy_candle_time):
                                self._arm_pending_entry(strategy_candle_time, latest_row)
                                self.log_event(
                                    "signal",
                                    f"⚡ ENTRY SIGNAL at {now.strftime('%H:%M:%S')} — will enter on NEXT candle open @ {self._pending_entry_ready_at.strftime('%H:%M:%S')}",
                                )
                            else:
                                self._condition_debug = {"gate": "market_close_boundary", "conditions": []}
                                self.log_event(
                                    "info", "Entry signal ignored — no next tradable candle before market close"
                                )
                elif self.in_trade:
                    self._condition_debug = {"gate": "in_trade", "conditions": []}
                elif self.trades_today >= max_trades:
                    self._condition_debug = {
                        "gate": f"max_trades_reached ({self.trades_today}/{max_trades})",
                        "conditions": [],
                    }
                elif daily_loss_hit:
                    self._condition_debug = {"gate": f"daily_loss_limit (₹{self.daily_pnl:,.2f})", "conditions": []}
                elif cooldown_hit:
                    self._condition_debug = {
                        "gate": f"profit_cooldown (big-profit day {self.profit_cooldown_trigger_date})",
                        "conditions": [],
                    }

                # Store previous row for crossover detection
                self._prev_row = latest_row
                self._last_strategy_candle_time = strategy_candle_time

                if now.time() >= market_close:
                    await self._force_market_close_if_needed(now, callback)

                # Send status update
                if callback:
                    await self._emit_callback(callback, self.get_status())

            except Exception as e:
                self.log_event("error", f"WS mode error: {str(e)}")
                await asyncio.sleep(1)

    def _get_premium_from_feed(self, position: dict) -> float:
        """Get option premium from WebSocket feed's LTP cache (instant, no API call)."""
        if not self._feed:
            return 0.0

        sec_id = position.get("ws_sec_id")
        if sec_id:
            ltp = self._feed.get_ltp(sec_id)
            if ltp > 0:
                return ltp
        return 0.0

    def _update_ui_data(self, row):
        """Store latest candle + indicator values for the live monitor UI."""
        try:
            self.current_candle = {
                "open": round(float(row.get("open", 0)), 2),
                "high": round(float(row.get("high", 0)), 2),
                "low": round(float(row.get("low", 0)), 2),
                "close": round(float(row.get("close", 0)), 2),
                "volume": int(row.get("volume", 0)),
                "updated_at": _now_ist().strftime("%Y-%m-%d %I:%M:%S %p"),
            }
            ohlcv_cols = {
                "open",
                "high",
                "low",
                "close",
                "volume",
                "oi",
                "timestamp",
                "date",
                "datetime",
                "time_of_day",
                "current_open",
                "current_high",
                "current_low",
                "current_close",
                "yesterday_open",
                "yesterday_high",
                "yesterday_low",
                "yesterday_close",
                "cpr_type",
                "pivot",
                "bc",
                "tc",
                "cpr_range",
                "cpr_width_pct",
                "cpr_is_narrow",
                "supertrend_dir",
            }
            self.current_indicators = {}
            for col in self.candle_buffer.columns:
                if col in ohlcv_cols:
                    continue
                try:
                    val = row[col]
                    if pd.isna(val):
                        continue
                    self.current_indicators[col] = round(float(val), 2)
                except (TypeError, ValueError):
                    pass
        except Exception:
            pass

    def _get_touch_raw_snapshot(self) -> pd.DataFrame:
        """Return the latest raw candle snapshot, including the forming candle when possible."""
        tf_spec = self._get_timeframe_spec()
        instrument = self.strategy.get("instrument", "26000")

        if self._ws_mode and self._feed:
            try:
                snapshot = self._feed.get_candle_snapshot(instrument, tf_spec.fetch, include_current=True)
            except Exception:
                snapshot = pd.DataFrame()
            if snapshot is not None and not snapshot.empty:
                return snapshot.sort_index().copy()

        if isinstance(self._latest_raw_candles, pd.DataFrame) and not self._latest_raw_candles.empty:
            return self._latest_raw_candles.sort_index().copy()

        return pd.DataFrame()

    def _build_live_touch_row(self, row: pd.Series) -> pd.Series:
        """Overlay live OHLC values onto the last closed indicator row for touch exits."""
        live_row = row.copy()
        tf_spec = self._get_timeframe_spec()
        touch_ts = self.current_time or _now_ist()
        raw_snapshot = self._get_touch_raw_snapshot()
        intrabar_row = None

        if not raw_snapshot.empty:
            try:
                if tf_spec.requested == tf_spec.fetch:
                    intrabar_df = raw_snapshot
                else:
                    intrabar_df = resample_ohlcv(
                        raw_snapshot,
                        tf_spec.requested,
                        source_timeframe_minutes=tf_spec.fetch,
                        drop_incomplete=False,
                    )
                if not intrabar_df.empty:
                    intrabar_row = intrabar_df.iloc[-1]
            except Exception:
                intrabar_row = None

        intrabar_values = intrabar_row.to_dict() if intrabar_row is not None else dict(self.current_candle or {})
        if self.current_spot > 0 and "close" not in intrabar_values:
            intrabar_values["close"] = self.current_spot

        for key in ("open", "high", "low", "close", "volume", "oi"):
            value = intrabar_values.get(key)
            if value is None:
                continue
            try:
                if pd.isna(value):
                    continue
            except Exception:
                pass
            live_row[key] = value

        if self.current_spot > 0:
            live_row["close"] = float(self.current_spot)

        live_row["current_open"] = live_row.get("open")
        live_row["current_high"] = live_row.get("high")
        live_row["current_low"] = live_row.get("low")
        live_row["current_close"] = live_row.get("close")
        live_row["current_volume"] = live_row.get("volume", 0)
        live_row["time_of_day"] = touch_ts.time()
        live_row["Day_Of_Week"] = touch_ts.weekday()
        live_row["Day_of_Week"] = touch_ts.weekday()
        live_row["Day_Name"] = touch_ts.strftime("%A")
        live_row["Hour"] = touch_ts.hour
        live_row["Minute"] = touch_ts.minute
        live_row["Time_HHMM"] = touch_ts.strftime("%H:%M")
        live_row["Is_Monday"] = float(touch_ts.weekday() == 0)
        live_row["Is_Tuesday"] = float(touch_ts.weekday() == 1)
        live_row["Is_Wednesday"] = float(touch_ts.weekday() == 2)
        live_row["Is_Thursday"] = float(touch_ts.weekday() == 3)
        live_row["Is_Friday"] = float(touch_ts.weekday() == 4)
        live_row.name = touch_ts
        return live_row

    # ── REST Polling Mode (original _tick) ────────────────────
    async def _tick(self, callback=None):
        """Single tick - check market, evaluate conditions, manage trades"""
        now = _now_ist()
        self.current_time = now
        current_time = now.time()

        # Check if new day
        if now.date() != self.session_date:
            await self._resolve_stale_positions(now, "SESSION_ROLLOVER", "Session rollover", callback)
            self.trades_today = 0
            self.daily_pnl = 0.0
            self.session_date = now.date()
            self._reset_intraday_status()
            self.log_event("info", f"📅 New trading day: {self.session_date}")
            # PERSIST THE ROLLOVER. The date moved in memory only, so a
            # flat engine's file still read yesterday -- and the restore
            # skips a stale file that holds no position. PhilForge was
            # stopped at 15:32 on 2026-08-21 for a Dhan backfill and
            # started again at 00:33, 33 minutes past midnight: all three
            # paper engines were dropped as stale and vanished from the
            # Live page. Saving here costs one write a day.
            self._save_state()

        # Check market hours
        market_open = self.strategy.get("market_open", "09:15")
        market_close = self.strategy.get("market_close", "15:25")

        from datetime import time as time_class

        if isinstance(market_open, str):
            h, m = map(int, market_open.split(":"))
            market_open = time_class(h, m)
        if isinstance(market_close, str):
            h, m = map(int, market_close.split(":"))
            market_close = time_class(h, m)

        if self._is_closed_market_day(now.date()):
            await self._resolve_stale_positions(now, "NON_TRADING_DAY", "Market closed today", callback)
            if callback:
                await self._emit_callback(callback, {"type": "status", "message": "Outside market hours"})
            return

        if current_time < market_open:
            if callback:
                await self._emit_callback(callback, {"type": "status", "message": "Outside market hours"})
            return

        after_market_close = current_time >= market_close

        # Fetch live candle data
        try:
            df = await self._fetch_live_data()
            if df.empty:
                return

            self.candle_buffer = df
            self.current_spot = float(df["close"].iloc[-1])

            if callback:
                await self._emit_callback(
                    callback, {"type": "price_update", "spot": self.current_spot, "time": str(now)}
                )
        except Exception as e:
            self.log_event("error", f"Failed to fetch live data: {e}")
            return

        # Get latest closed strategy candle for condition evaluation
        execution_timeframe = self._get_timeframe_spec().requested
        eval_df = drop_incomplete_candle(df, execution_timeframe, now)
        if eval_df.empty:
            if callback:
                await self._emit_callback(callback, self.get_status())
            return

        latest_row = eval_df.iloc[-1]
        strategy_candle_time = latest_row.name if hasattr(latest_row, "name") else None
        is_new_strategy_candle = strategy_candle_time != self._last_strategy_candle_time
        candle_in_session = self._strategy_candle_closes_in_session(strategy_candle_time)
        if (
            not candle_in_session
            and not self.in_trade
            and not self._strategy_candle_is_current_session(strategy_candle_time, now)
        ):
            self._condition_debug = {"gate": "waiting_for_first_candle", "conditions": []}

        # Manage existing positions
        for position in list(self.positions):
            if position["status"] == "closed":
                continue

            # Fetch current option price
            current_premium = await self._get_current_premium(position)
            position["current_premium"] = current_premium

            # Calculate unrealized P&L
            direction = 1 if position["transaction_type"] == "BUY" else -1
            position["unrealized_pnl"] = (
                (current_premium - position["entry_premium"]) * direction * self._position_quantity(position)
            )

        if candle_in_session:
            strategy_exit = self._check_strategy_exit()
            if strategy_exit:
                for position in list(self.positions):
                    if position.get("status") == "closed":
                        continue
                    self._close_position(position, strategy_exit, position.get("current_premium", 0.0))
            else:
                for position in list(self.positions):
                    if position.get("status") == "closed":
                        continue
                    current_premium = position.get("current_premium", 0.0)
                    exit_triggered = self._check_exit_conditions(position, latest_row, current_premium)
                    if exit_triggered:
                        self._close_position(position, exit_triggered, current_premium)

        # Check entry conditions (if not in trade)
        max_trades = self.strategy.get("max_trades_per_day", 1)
        # Check max daily loss limit
        daily_loss_hit = self.max_daily_loss > 0 and self.daily_pnl <= -self.max_daily_loss
        if daily_loss_hit and not self.in_trade:
            self._condition_debug = {"gate": f"daily_loss_limit (₹{self.daily_pnl:,.2f})", "conditions": []}
        elif self._profit_cooldown_active() and not self.in_trade:
            self._condition_debug = {
                "gate": f"profit_cooldown (big-profit day {self.profit_cooldown_trigger_date})",
                "conditions": [],
            }
        elif self.trades_today < max_trades and not self.in_trade and candle_in_session:
            # Execute pending signal from previous tick (enter on THIS candle)
            if self._entry_signal_pending:
                if not self._pending_entry_is_current_session(now):
                    self.log_event("info", "Pending entry cleared — stale session signal")
                    self._clear_pending_entry()
                elif self._pending_entry_is_ready(now):
                    if current_time >= market_close:
                        self.log_event("info", "Pending entry cleared at session end")
                        self._clear_pending_entry()
                    else:
                        self._clear_pending_entry()
                        self.log_event(
                            "entry", f"🚀 Executing pending entry @ {now.strftime('%H:%M:%S')} (next candle open)"
                        )
                        await self._enter_trade(latest_row)
            elif is_new_strategy_candle:
                prev_row = eval_df.iloc[-2] if len(eval_df) >= 2 else None
                entry_triggered, self._condition_debug = self._evaluate_entry_conditions_with_debug(
                    latest_row,
                    prev_row,
                    now,
                )
                if entry_triggered:
                    if self._entry_can_be_scheduled(strategy_candle_time):
                        self._arm_pending_entry(strategy_candle_time, latest_row)
                        self.log_event(
                            "signal",
                            f"⚡ ENTRY SIGNAL — will enter on NEXT candle open @ {self._pending_entry_ready_at.strftime('%H:%M:%S')}",
                        )
                    else:
                        self._condition_debug = {"gate": "market_close_boundary", "conditions": []}
                        self.log_event("info", "Entry signal ignored — no next tradable candle before market close")
        elif self.in_trade:
            self._condition_debug = {"gate": "in_trade", "conditions": []}
        elif self.trades_today >= max_trades:
            self._condition_debug = {"gate": f"max_trades_reached ({self.trades_today}/{max_trades})", "conditions": []}

        # Store previous row for crossover detection in exit conditions
        if is_new_strategy_candle:
            self._prev_row = latest_row
            self._last_strategy_candle_time = strategy_candle_time

        if after_market_close:
            await self._force_market_close_if_needed(now, callback)
            if callback:
                await self._emit_callback(callback, {"type": "status", "message": "Outside market hours"})
            return

        # Send status update
        if callback:
            await self._emit_callback(callback, self.get_status())

    async def _fetch_live_data(self) -> pd.DataFrame:
        """Fetch live candle data with indicators"""

        tf_spec = self._get_timeframe_spec()
        execution_timeframe = tf_spec.requested

        instrument = self.strategy.get("instrument", "26000")

        # Fetch last 7 days to have enough data for indicators
        df_raw = self._fetch_raw_history(instrument, tf_spec.fetch, days=7)

        # Apply indicators
        indicators = self.strategy.get("indicators", [])
        df = compute_dynamic_indicators(
            df_raw,
            indicators,
            default_timeframe_minutes=execution_timeframe,
            source_timeframe_minutes=tf_spec.fetch,
            execution_timeframe_minutes=execution_timeframe,
        )
        self._latest_raw_candles = df_raw.tail(500).copy()

        # Store current candle + indicator values for live monitor UI
        if not df.empty:
            last = df.iloc[-1]
            self.current_candle = {
                "open": round(float(last.get("open", 0)), 2),
                "high": round(float(last.get("high", 0)), 2),
                "low": round(float(last.get("low", 0)), 2),
                "close": round(float(last.get("close", 0)), 2),
                "volume": int(last.get("volume", 0)),
                "openInterest": int(last.get("oi", 0)),
                "updated_at": _now_ist().strftime("%Y-%m-%d %I:%M:%S %p"),
            }
            ohlcv_cols = {
                "open",
                "high",
                "low",
                "close",
                "volume",
                "oi",
                "timestamp",
                "date",
                "datetime",
                "time_of_day",
                "current_open",
                "current_high",
                "current_low",
                "current_close",
                "yesterday_open",
                "yesterday_high",
                "yesterday_low",
                "yesterday_close",
                "cpr_type",
                "pivot",
                "bc",
                "tc",
                "cpr_range",
                "cpr_width_pct",
                "cpr_is_narrow",
                "supertrend_dir",
            }
            self.current_indicators = {}
            for col in df.columns:
                if col in ohlcv_cols:
                    continue
                try:
                    val = last[col]
                    if pd.isna(val):
                        continue
                    fval = float(val)
                    self.current_indicators[col] = round(fval, 2)
                except (TypeError, ValueError):
                    pass  # skip datetime.time, bool strings, etc.

        return df.tail(200)  # Keep last 200 candles

    async def _get_current_premium(self, position: dict) -> float:
        """
        Fetch current premium for an option position from Dhan.
        Uses REAL LTP from Dhan option chain API.
        Falls back to delta estimation if API fails.
        """
        try:
            underlying = self._get_symbol_name()
            strike = position.get("strike", 0)
            option_type = position.get("option_type", "PE")
            expiry = position.get("expiry", "")

            if underlying and strike and expiry:
                ltp = await self.dhan.async_get_option_ltp(underlying, int(strike), expiry, option_type)
                if ltp > 0:
                    return ltp
        except Exception as e:
            self.log_event("warning", f"LTP fetch failed, using estimate: {e}")

        # Fallback: delta-based estimation
        try:
            spot_change = self.current_spot - position["entry_spot"]
            ot = position.get("option_type", "PE")
            direction = -1 if ot == "PE" else 1
            moneyness = direction * spot_change

            ep = position["entry_premium"]
            atm_prem = self.current_spot * 0.007
            r = ep / max(atm_prem, 1)
            delta = min(0.95, 1.0 - 1.0 / (1.0 + r**2.5))

            premium_change = (
                abs(spot_change)
                * delta
                * (-1 if (ot == "PE" and spot_change > 0) or (ot == "CE" and spot_change < 0) else 1)
            )
            return max(0.5, ep + premium_change)
        except Exception as e:
            self.log_event("error", f"Premium estimation failed: {e}")
            return position.get("current_premium", position["entry_premium"])

    def _check_exit_conditions(
        self,
        position: dict,
        row: pd.Series,
        current_premium: float,
        *,
        allow_signal_exit: bool = True,
    ) -> Optional[str]:
        """Check if any leg-level or signal-based exit condition is met."""
        # Update peak premium for trailing SL
        if position["transaction_type"] == "BUY":
            position["peak_premium"] = max(position.get("peak_premium", position["entry_premium"]), current_premium)
        else:
            position["peak_premium"] = min(position.get("peak_premium", position["entry_premium"]), current_premium)

        # Trailing stop loss check (takes priority over static SL)
        trail_pct = position.get("trail_pct", 0)
        if trail_pct > 0:
            peak = position["peak_premium"]
            if position["transaction_type"] == "BUY":
                trail_sl = peak * (1 - trail_pct / 100)
                if current_premium <= trail_sl:
                    self.log_event(
                        "exit", f"Trailing SL hit: peak={peak:.2f} trail={trail_sl:.2f} current={current_premium:.2f}"
                    )
                    return "TRAILING_SL"
            else:  # SELL
                trail_sl = peak * (1 + trail_pct / 100)
                if current_premium >= trail_sl:
                    self.log_event(
                        "exit", f"Trailing SL hit: peak={peak:.2f} trail={trail_sl:.2f} current={current_premium:.2f}"
                    )
                    return "TRAILING_SL"

        # Static stop loss check (leg-level)
        sl_pct = position.get("sl_pct", 0)
        if sl_pct > 0:
            sl_threshold = position["entry_premium"] * (1 - sl_pct / 100)
            if position["transaction_type"] == "BUY" and current_premium <= sl_threshold:
                return "STOP_LOSS"
            elif position["transaction_type"] == "SELL" and current_premium >= (
                position["entry_premium"] * (1 + sl_pct / 100)
            ):
                return "STOP_LOSS"

        # Target check (leg-level)
        target_pct = position.get("target_pct", 0)
        if target_pct > 0:
            target_threshold = position["entry_premium"] * (1 + target_pct / 100)
            if position["transaction_type"] == "BUY" and current_premium >= target_threshold:
                return "TARGET"
            elif position["transaction_type"] == "SELL" and current_premium <= (
                position["entry_premium"] * (1 - target_pct / 100)
            ):
                return "TARGET"

        # SL Points check (leg-level, absolute premium points)
        sl_points = position.get("sl_points", 0)
        if sl_points > 0:
            ep = position["entry_premium"]
            if position["transaction_type"] == "BUY" and current_premium <= ep - sl_points:
                return "SL_POINTS"
            elif position["transaction_type"] == "SELL" and current_premium >= ep + sl_points:
                return "SL_POINTS"

        # Target Points check (leg-level, absolute premium points)
        target_points = position.get("target_points", 0)
        if target_points > 0:
            ep = position["entry_premium"]
            if position["transaction_type"] == "BUY" and current_premium >= ep + target_points:
                return "TARGET_POINTS"
            elif position["transaction_type"] == "SELL" and current_premium <= ep - target_points:
                return "TARGET_POINTS"

        # SL ₹ Total check (leg-level, total rupee loss)
        sl_rupees = position.get("sl_rupees", 0)
        if sl_rupees > 0:
            qty = self._position_quantity(position)
            direction = 1 if position["transaction_type"] == "BUY" else -1
            cur_pnl = (current_premium - position["entry_premium"]) * direction * qty
            if cur_pnl <= -sl_rupees:
                return "SL_RUPEES"

        # Target ₹ Total check (leg-level, total rupee profit)
        target_rupees = position.get("target_rupees", 0)
        if target_rupees > 0:
            qty = self._position_quantity(position)
            direction = 1 if position["transaction_type"] == "BUY" else -1
            cur_pnl = (current_premium - position["entry_premium"]) * direction * qty
            if cur_pnl >= target_rupees:
                return "TARGET_RUPEES"

        # Both exits below read SPOT, so both stop at the signal cutoff. The
        # square-off further down still fires — it is a clock rule filled at the
        # option's own premium.
        spot_signals_live = self._signals_live()

        # Touch-based exit — evaluated on CURRENT row (no 1-candle delay)
        if spot_signals_live and any(c.get("operator") == "touches" for c in self.exit_conditions):
            _touch_row = self._build_live_touch_row(row)
            if self._signal_candle:
                for _k, _v in self._signal_candle.items():
                    _touch_row[_k] = _v
            if eval_condition_group(_touch_row, self.exit_conditions, self._prev_row):
                return "TOUCH_EXIT"

        # Signal exit — inject Signal Candle values into evaluation row
        if allow_signal_exit and spot_signals_live:
            _exit_row = row.copy() if self._signal_candle else row
            if self._signal_candle:
                for _k, _v in self._signal_candle.items():
                    _exit_row[_k] = _v
            signal_exit = eval_condition_group(_exit_row, self.exit_conditions, self._prev_row)
            # Report before returning: an exit that could not be decided is the
            # case worth seeing, and returning first would swallow it.
            self._log_cross_skips("exit")
            if signal_exit:
                return "EXIT_SIGNAL"

        if self._is_intraday_product():
            # Square off time
            sqoff_time = self.strategy.get("combined_sqoff_time", "15:20")
            if not sqoff_time:
                sqoff_time = position.get("sqoff_time", "15:20")
            if isinstance(sqoff_time, str):
                h, m = map(int, sqoff_time.split(":"))
                from datetime import time as time_class

                sqoff_time = time_class(h, m)

            if self.current_time.time() >= sqoff_time:
                return "SQUARE_OFF"

        return None

    async def _enter_trade(self, row: pd.Series):
        """Enter trade based on strategy legs — uses REAL option LTP from Dhan"""
        self.log_event("signal", "✅ ENTRY CONDITIONS MET", {"spot": self.current_spot, "time": str(self.current_time)})
        # The why, frozen at the instant of decision, attached to every leg below.
        entry_why = decision_why(row, self.entry_conditions, self._condition_debug, self._prev_row, "ENTRY_SIGNAL")

        legs = self.strategy.get("legs", [])
        if not legs:
            self.log_event("warning", "No legs configured - cannot enter trade")
            return

        instrument = self.strategy.get("instrument", "26000")
        strike_step = get_strike_step(instrument)

        # Use user-configured lot_size if set, else from ScripMaster
        user_lot_size = int(self.strategy.get("lot_size", 0) or 0)
        symbol = self._get_symbol_name()
        session_date_str = self.session_date.strftime("%Y-%m-%d") if self.session_date else None
        entry_spot = float(self.current_spot)
        entry_time = self.current_time or _now_ist()

        default_lots = int(self.strategy.get("lots", 1) or 1)
        planned_positions = []

        for i, leg in enumerate(legs):
            expiry = ScripMaster.resolve_expiry(symbol, leg.get("expiry"), session_date_str)
            if not expiry:
                self.log_event("error", f"No expiry found for {symbol} leg {i + 1} — cannot enter trade")
                continue

            if user_lot_size > 0:
                lot_size = user_lot_size
            else:
                # ScripMaster answers 0 when Dhan does not carry the lot size.
                # Fall back to the effective-dated table rather than sizing this
                # leg at zero, which is what a bare 0 would silently do here.
                lot_size = ScripMaster.get_lot_size(symbol, expiry) if expiry else 0
                if lot_size <= 0:
                    lot_size = get_lot_size(instrument, self.session_date)

            option_type = leg.get("option_type", "PE")
            strike_type = leg.get("strike_type", "atm")
            strike_value = leg.get("strike_value", 0)
            leg_lots = leg.get("lots", default_lots)

            self.log_event("info", f"📊 Leg {i + 1} expiry: {expiry} | Lot size: {lot_size} | Lots: {leg_lots}")

            # Calculate strike — handle premium-based types by scanning real LTP
            scanned_premium = 0.0
            if strike_type in ("premium_near", "premium_above", "premium_below") and expiry:
                mode = strike_type.split("_")[1]  # "near", "above", "below"
                strike, scanned_premium = await self._find_premium_strike(
                    symbol, expiry, option_type, float(strike_value), entry_spot, strike_step, mode=mode
                )
                self.log_event("info", f"🎯 {strike_type} target=₹{strike_value} → strike={strike}")
            else:
                strike = self._calculate_strike(leg, entry_spot, strike_step)

            # Get entry premium — reuse from scan if available, else fetch fresh
            entry_premium = scanned_premium if scanned_premium > 0 else 0.0
            if entry_premium <= 0 and expiry:
                try:
                    entry_premium = await self.dhan.async_get_option_ltp(symbol, int(strike), expiry, option_type)
                except Exception as e:
                    self.log_event("warning", f"LTP fetch failed: {e}")

            if entry_premium <= 0:
                # Fallback to estimation
                entry_premium = await self._estimate_premium(strike, entry_spot, option_type, strike_step)
                self.log_event("warning", f"Using estimated premium: ₹{entry_premium:.2f}")

            quoted_entry_premium = float(entry_premium)
            entry_premium = self._apply_execution_costs(
                quoted_entry_premium,
                leg["transaction_type"],
                "entry",
            )
            quantity = int(leg_lots) * int(lot_size)

            option_name = f"{symbol} {strike} {option_type}"
            planned_positions.append(
                {
                    "id": len(self.positions) + len(self.closed_trades) + len(planned_positions) + 1,
                    "leg_num": i + 1,
                    "symbol": option_name,
                    "transaction_type": leg["transaction_type"],
                    "option_type": option_type,
                    "strike": strike,
                    "expiry": expiry,
                    "entry_time": entry_time,
                    "entry_why": entry_why,
                    "entry_spot": entry_spot,
                    "entry_quote_premium": quoted_entry_premium,
                    "entry_premium": entry_premium,
                    "current_premium": entry_premium,
                    "lots": leg_lots,
                    "lot_size": lot_size,
                    "quantity": quantity,
                    "sl_pct": leg.get("sl_pct", 0),
                    "target_pct": leg.get("target_pct", 0),
                    "sl_points": leg.get("sl_points", 0),
                    "target_points": leg.get("target_points", 0),
                    "sl_rupees": leg.get("sl_rupees", 0),
                    "target_rupees": leg.get("target_rupees", 0),
                    "trail_pct": leg.get("trail_pct", 0),
                    "sqoff_time": leg.get("sqoff_time", "15:20"),
                    "unrealized_pnl": 0,
                    "peak_premium": entry_premium,
                    "status": "open",
                    "ws_sec_id": None,
                }
            )

        if not planned_positions:
            self.log_event("warning", "No legs could be planned — cannot enter trade")
            return

        if not self._can_enter_trade(planned_positions):
            return

        for position in planned_positions:
            option_type = position["option_type"]
            strike = position["strike"]
            expiry = position["expiry"]

            if self._ws_mode and self._feed and expiry:
                ws_sec_id = self._feed.subscribe_option(symbol, int(strike), expiry, option_type)
                if ws_sec_id:
                    position["ws_sec_id"] = ws_sec_id
                    self._option_sec_id = ws_sec_id
                    self.log_event("info", f"⚡ Option subscribed to WebSocket: sec_id={ws_sec_id}")

            self.positions.append(position)
            self.log_event(
                "entry",
                f"📝 Leg {position['leg_num']}: {position['transaction_type']} {symbol} {strike} {option_type} @ ₹{position['entry_premium']:.2f}",
                {
                    "premium": position["entry_premium"],
                    "lots": position["lots"],
                    "lot_size": position["lot_size"],
                    "strike": strike,
                    "expiry": expiry,
                },
            )

        self.in_trade = True
        self.trades_today += 1
        self._set_strategy_thresholds(self.positions)
        if self.strat_sl_val > 0:
            self.log_event("info", f"🛡️ Strategy SL: ₹{self.strat_sl_val:,.0f}")
        if self.strat_tp_val > 0:
            self.log_event("info", f"🎯 Strategy TP: ₹{self.strat_tp_val:,.0f}")
        self._save_state()  # Persist after trade entry

    async def _find_premium_strike(
        self,
        symbol: str,
        expiry: str,
        option_type: str,
        target_prem: float,
        spot: float,
        strike_step: int,
        mode: str = "near",
    ) -> int:
        """
        Find the strike whose premium matches the target based on mode:
          near  -> closest premium to target (either side)
          above -> cheapest premium that is >= target (min premium constraint)
          below -> most expensive premium that is <= target (max premium constraint)
        Fetches ALL strikes in a single batched LTP call so coverage is complete.
        Falls back to estimated premium only for strikes missing from ScripMaster.
        """
        from broker.dhan import ScripMaster

        atm = round_to_nearest_step(spot, strike_step)
        exchange_seg = "BSE_FNO" if symbol == "SENSEX" else "NSE_FNO"

        # ── 1. Resolve security IDs for all strikes ────────────────────────
        strikes_to_scan = [
            int(atm + offset * strike_step) for offset in range(-15, 16) if atm + offset * strike_step > 0
        ]
        sec_id_map = {}  # strike -> security_id
        for s in strikes_to_scan:
            sid = ScripMaster.lookup(symbol, s, expiry, option_type)
            if sid:
                sec_id_map[s] = int(sid)

        # ── 2. Prices, from the shelf every engine shares ──────────────────
        # THE SAME SHELF THE LIVE ENGINE READS. Paper scans the chain a second
        # or two before live does, and until 2026-09-03 the two fetched
        # separately: paper got its prices, live was refused with a 429, fell
        # back to modelled premiums and bought a different strike. Paper asking
        # through here means paper's answer IS live's answer.
        live_ltps = {}  # strike -> ltp
        if sec_id_map:
            try:
                prices = await self.dhan.async_get_ltp_prices(list(sec_id_map.values()), exchange_segment=exchange_seg)
                live_ltps = {s: prices[sid] for s, sid in sec_id_map.items() if sid in prices}
            except Exception as e:
                self.log_event("warning", f"Batch LTP fetch failed: {e}, using estimates")

        # ── 3. Build full candidates list ──────────────────────────────────
        candidates = []  # (strike, premium, source)
        for s in strikes_to_scan:
            if s in live_ltps:
                candidates.append((s, live_ltps[s], "live"))
            else:
                est = await self._estimate_premium(s, spot, option_type, strike_step)
                candidates.append((s, est, "est"))

        if not candidates:
            return int(atm), 0.0

        live_count = sum(1 for _, _, src in candidates if src == "live")
        self.log_event(
            "info",
            f"🔍 premium_{mode}: {len(candidates)} strikes ({live_count} live LTPs, {len(candidates) - live_count} estimated)",
        )

        if mode == "above":
            valid = [(s, p) for s, p, _ in candidates if p >= target_prem]
            if valid:
                best = min(valid, key=lambda x: x[1])  # cheapest that still meets min
                self.log_event(
                    "info",
                    f"   {len(valid)} qualify ≥₹{target_prem} → selected strike={best[0]} (premium ₹{best[1]:.2f})",
                )
                return best[0], best[1]
            self.log_event("warning", f"⚠️ premium_above: no strike with premium ≥₹{target_prem}, using closest")
            best = min([(s, p) for s, p, _ in candidates], key=lambda x: abs(x[1] - target_prem))
            return best[0], best[1]

        elif mode == "below":
            valid = [(s, p) for s, p, _ in candidates if p <= target_prem]
            if valid:
                best = max(valid, key=lambda x: x[1])  # most expensive under limit
                self.log_event(
                    "info",
                    f"   {len(valid)} qualify ≤₹{target_prem} → selected strike={best[0]} (premium ₹{best[1]:.2f})",
                )
                return best[0], best[1]
            self.log_event("warning", f"⚠️ premium_below: no strike with premium ≤₹{target_prem}, using closest")
            best = min([(s, p) for s, p, _ in candidates], key=lambda x: abs(x[1] - target_prem))
            return best[0], best[1]

        else:  # near
            best = min([(s, p) for s, p, _ in candidates], key=lambda x: abs(x[1] - target_prem))
            self.log_event("info", f"   selected strike={best[0]} (premium ₹{best[1]:.2f}, target ₹{target_prem})")
            return best[0], best[1]

    async def _find_premium_near_strike(
        self, symbol: str, expiry: str, option_type: str, target_prem: float, spot: float, strike_step: int
    ) -> int:
        """Backward compat wrapper — delegates to _find_premium_strike."""
        strike, _ = await self._find_premium_strike(
            symbol, expiry, option_type, target_prem, spot, strike_step, mode="near"
        )
        return strike

    def _close_position(self, position: dict, reason: str, exit_premium: float):
        """Close a position and calculate realized P&L from the actual exit premium."""
        position["status"] = "closed"
        position["exit_time"] = self.current_time
        position["exit_reason"] = reason
        position["exit_quote_premium"] = float(exit_premium)
        # The exit's why: the reason always; the exit conditions' verdicts when
        # a signal decided it (a stop, target or clock needs no conditions).
        try:
            latest = self.candle_buffer.iloc[-1] if not self.candle_buffer.empty else None
        except Exception:
            latest = None
        position["exit_why"] = decision_why(
            latest,
            self.exit_conditions if str(reason) in ("EXIT_SIGNAL", "TOUCH_EXIT") else [],
            None,
            self._prev_row,
            reason,
        )

        adjusted_exit_premium = self._apply_execution_costs(exit_premium, position["transaction_type"], "exit")

        direction = 1 if position["transaction_type"] == "BUY" else -1
        qty = self._position_quantity(position)
        ep = position["entry_premium"]
        gross = round((adjusted_exit_premium - ep) * direction * qty, 2)
        # WHAT THE ROUND TRIP COSTS. Slippage was already modelled above; this
        # is the statutory half -- brokerage, STT, exchange, GST, stamp -- and
        # neither engine charged a rupee of it before 2026-09-01. A paper
        # number without it is not the number the account would have shown.
        charges = statutory_round_charges(
            entry_premium=ep,
            exit_premium=adjusted_exit_premium,
            quantity=qty,
            lots=position.get("lots", 1),
            option_type=position.get("option_type"),
        )
        pnl = round(gross - charges, 2)

        position["exit_premium"] = adjusted_exit_premium
        position["gross_pnl"] = gross
        position["charges"] = charges
        position["pnl"] = pnl
        self.daily_pnl += pnl
        self._arm_profit_cooldown()

        closed_pos = position.copy()
        self.closed_trades.append(closed_pos)
        self.positions.remove(position)

        self.log_event(
            "exit",
            f"📊 Exit Leg {position['leg_num']}: {reason} | PnL: ₹{pnl:,.2f}",
            {
                "entry_premium": ep,
                "exit_quote_premium": position["exit_quote_premium"],
                "exit_premium": adjusted_exit_premium,
                "pnl": pnl,
            },
        )

        # Check if all positions are closed
        if not self.positions:
            self.in_trade = False
            self._signal_candle = None
            self.strat_sl_val = 0
            self.strat_tp_val = 0
            total_pnl = sum(t["pnl"] for t in self.closed_trades if t.get("exit_time") == self.current_time)
            self.log_event("info", f"✅ All legs closed. Trade P&L: ₹{total_pnl:,.2f}")
        self._save_state()  # Persist after trade close
        self._save_trade_history([closed_pos])  # Persist to cumulative history

    def _calculate_strike(self, leg: dict, spot: float, strike_step: int) -> int:
        """Calculate strike price based on strike_type"""
        atm = round_to_nearest_step(spot, strike_step)

        strike_type = leg.get("strike_type", "atm")
        strike_value = leg.get("strike_value", 0)
        option_type = leg.get("option_type", "CE")

        if strike_type == "atm":
            return int(atm)
        elif strike_type == "strike_price":
            return round_to_nearest_step(strike_value, strike_step)
        elif strike_type == "otm":
            offset = round_to_nearest_step(strike_value, strike_step)
            return int(atm + offset if option_type == "CE" else atm - offset)
        elif strike_type == "itm":
            offset = round_to_nearest_step(strike_value, strike_step)
            return int(atm - offset if option_type == "CE" else atm + offset)
        elif strike_type == "spot_price":
            offset = round_to_nearest_step(strike_value, strike_step)
            return round_to_nearest_step(spot + offset, strike_step)
        else:
            return int(atm)

    async def _estimate_premium(self, strike: int, spot: float, option_type: str, strike_step: int) -> float:
        """
        Estimate premium for an option.
        TODO: Replace with actual option chain fetch from Dhan API
        """
        atm = round_to_nearest_step(spot, strike_step)
        moneyness = (spot - strike) if option_type == "CE" else (strike - spot)

        # Base ATM premium (0.5% - 0.6% of spot)
        atm_prem = spot * 0.005

        if moneyness > 0:  # ITM
            intrinsic = moneyness
            extrinsic = atm_prem * 0.5 * (1 - abs(moneyness) / (spot * 0.2))
            return max(1, round(intrinsic + extrinsic, 2))
        else:  # OTM
            distance_pct = abs(moneyness) / spot
            return max(1, round(atm_prem * max(0.05, (1 - distance_pct * 5)), 2))

    def _get_instrument_name(self) -> str:
        """Get instrument display name"""
        inst_map = {
            "26000": "NIFTY 50",
            "26009": "BANK NIFTY",
            "1": "SENSEX",
            "26017": "NIFTY FIN SVC",
            "26037": "NIFTY MIDCAP",
        }
        return inst_map.get(self.strategy.get("instrument", "26000"), "Unknown")

    def _get_symbol_name(self) -> str:
        """Get ScripMaster symbol name (NIFTY, BANKNIFTY, etc.)"""
        sym_map = {"26000": "NIFTY", "26009": "BANKNIFTY", "1": "SENSEX", "26017": "FINNIFTY", "26037": "MIDCPNIFTY"}
        return sym_map.get(self.strategy.get("instrument", "26000"), "NIFTY")

    def _get_timeframe_spec(self):
        default = int(self.strategy.get("timeframe_minutes", 5) or 5)
        execution_timeframe = infer_execution_timeframe(
            self.strategy.get("indicators", []),
            self.entry_conditions,
            default=default,
        )
        return resolve_strategy_timeframe(
            self.strategy.get("indicators", []),
            default=execution_timeframe,
            execution_hint=execution_timeframe,
        )

    def _get_timeframe(self) -> int:
        """Extract the execution timeframe from strategy indicators."""
        return self._get_timeframe_spec().requested

    def get_status(self) -> dict:
        """Get current status for UI"""
        total_pnl = sum(p.get("unrealized_pnl", 0) for p in self.positions)
        total_pnl += sum(t.get("pnl", 0) for t in self.closed_trades)

        return {
            "running": self.running,
            "run_id": self.run_id or "",
            "mode": "paper",
            "in_trade": self.in_trade,
            "current_spot": self.current_spot,
            "current_time": str(self.current_time) if self.current_time else None,
            "trades_today": self.trades_today,
            "daily_pnl": round(self.daily_pnl, 2),
            "profit_cooldown_active": self._profit_cooldown_active(),
            "profit_cooldown_trigger_date": str(self.profit_cooldown_trigger_date)
            if self.profit_cooldown_trigger_date
            else None,
            "capital_rejections": self.capital_rejections,
            "last_capital_check": self.last_capital_check,
            "execution_realism": {
                "profile": self.execution_profile,
                "spread_bps": self._spread_bps,
                "entry_slippage_bps": self._entry_slippage_bps,
                "exit_slippage_bps": self._exit_slippage_bps,
            },
            "positions": self.positions,
            "closed_trades": self.closed_trades,
            "total_pnl": round(total_pnl, 2),
            "strategy_name": self.strategy.get("run_name", "Paper Strategy"),
            "instrument": self.strategy.get("instrument", ""),
            "strategy": {
                **self.strategy,
                "entry_conditions": self.entry_conditions,
                "exit_conditions": self.exit_conditions,
            },
            "current_candle": self.current_candle,
            "current_indicators": self.current_indicators,
            "event_log": [
                {"time": e["time"].strftime("%H:%M:%S"), "type": e["type"], "message": e["message"]}
                for e in self.event_log[-50:]  # Last 50 events
            ],
            "condition_debug": self._condition_debug,
        }
