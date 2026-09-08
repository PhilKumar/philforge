"""
engine/live.py — Live Auto-Trading Engine
Places REAL orders via Dhan API.
Two modes:
  1. WebSocket mode (fast): LiveMarketFeed pushes ticks → candles aggregate
     → conditions evaluated on candle close → ~1-2 second latency
  2. REST polling mode (fallback): polls Dhan REST API every N seconds
     → conditions evaluated per poll → ~30-90 second latency"""

import asyncio
import json as _json
import math
from datetime import date as date_type
from datetime import datetime, time, timedelta, timezone
from typing import List, Optional

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist() -> datetime:
    """Return current time in IST (naive datetime)."""
    return datetime.now(IST).replace(tzinfo=None)


import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from broker.dhan import UNDERLYING_MAP, AmbiguousOrderSubmission, DhanClient, ScripMaster
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


# Lazy import to avoid circular dependency
def _get_instrument_map():
    from app import INSTRUMENT_MAP

    return INSTRUMENT_MAP


class PremiumScanUnavailable(Exception):
    """The option chain could not be priced, so no strike may be chosen.

    Raised rather than returning a strike picked from `_estimate_premium`:
    a modelled chain is a guess, and on 2026-09-03 that guess priced 23800 CE
    at Rs 227.35 when the market had it at Rs 252.60, dropped it under the
    Rs 250 rule and bought a deeper strike for Rs 5,460 more. The entry is
    abandoned instead, and the caller's existing attempt 2/2 retries it --
    usually all a moment of 429 contention needs.
    """


class LiveEngine:
    """
    Live auto-trading engine.
    - Fetches real market data from Dhan
    - Evaluates entry/exit conditions
    - Places REAL option orders via Dhan API
    - Manages stop-loss orders at broker level
    - Tracks positions, P&L and order status
    """

    def __init__(self, dhan: DhanClient = None, run_id: str = None, state_dir: str | None = None):
        self.dhan = dhan or DhanClient()
        self.running = False
        self.session_date = None
        self.mode = "auto"  # "auto" for real orders
        self.run_id = run_id  # Unique ID for multi-engine support
        base_state_dir = state_dir or _STATE_DIR
        os.makedirs(base_state_dir, exist_ok=True)

        # Per-instance state file for persistence
        if run_id:
            safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in run_id)
            self._state_file = os.path.join(base_state_dir, f"live_state_{safe_id}.json")
        else:
            self._state_file = os.path.join(base_state_dir, "live_state.json")

        # WebSocket feed (injected from app.py — if available, use event-driven mode)
        self._feed = None  # LiveMarketFeed instance
        self._ws_mode = False  # True when using WebSocket
        self._option_sec_id = None  # subscribed option security_id for LTP
        self._candle_event = None  # asyncio.Event set on each candle close
        self._latest_candle_df = None  # DataFrame from candle close callback
        self._latest_candle = None  # Last closed candle dict

        # Strategy + execution config
        self.strategy: dict = {}
        self.deploy_config: dict = {}
        self.entry_conditions: list = []
        self.exit_conditions: list = []

        # Trading state
        self.in_trade = False
        self.positions: List[dict] = []  # Open positions with order info
        self.closed_trades: List[dict] = []  # Completed trades
        self._trades_lock = asyncio.Lock()  # guards positions + closed_trades mutations
        # Strong references to detached tasks. A task nobody holds can be
        # collected mid-await, and the one thing this engine detaches is the
        # broker stop-loss -- losing that silently is the worst way to lose it.
        self._background_tasks: set = set()
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.max_daily_loss = 0.0
        # Date of the last session whose profit armed the skip-N-days cooldown
        self.profit_cooldown_trigger_date: date_type | None = None

        # Strategy-level SL/TP (₹ amounts)
        self.strat_sl_val = 0.0
        self.strat_tp_val = 0.0
        self.trade_entry_prem = 0.0
        self._sl_pct = 0.0
        self._sl_rupees = 0.0
        self._tp_pct = 0.0
        self._tp_rupees = 0.0
        self.initial_capital = 0.0
        self._enforce_capital = False
        self._capital_buffer_pct = 0.0
        self._sell_option_margin_per_lot = 0.0
        self.capital_rejections = 0
        self.last_capital_check: dict = {}
        self.order_verification_failures = 0
        self.last_order_verification: dict = {}
        self.manual_intervention_required = False
        self._entry_fill_timeout_sec = 15
        self._exit_fill_timeout_sec = 15
        # How long an exit will wait for the broker to confirm the stop is
        # gone before sending the exit anyway. Short on purpose: the cost of
        # waiting is slippage, the cost of not waiting is an RMS rejection.
        self._sl_cancel_confirm_sec = 2.0
        self._market_open = time(9, 15)
        self._market_close = time(15, 25)
        self._signal_cutoff: Optional[time] = None

        # Market data
        self.current_spot = 0.0
        self.current_time: Optional[datetime] = None
        self.candle_buffer = pd.DataFrame()
        self._latest_raw_candles = pd.DataFrame()
        self._indicator_context_raw = pd.DataFrame()
        self.current_candle: dict = {}
        self.current_indicators: dict = {}
        self._prev_row = None
        self._entry_signal_pending = False  # LEGACY compat — now driven by _pending_order
        self._signal_candle = None  # OHLC of the candle that triggered entry signal

        # ── "Quantman Way" — 1-second candle-boundary execution ──
        # Set when a leg failed in a state where the broker might still hold
        # it. Blocks the retry-once path: re-sending then is how you end up
        # holding twice what the engine thinks it holds.
        self._entry_retry_blocked = False
        self._pending_order: Optional[dict] = None  # Rich signal context for next-candle entry
        self._last_processed_candle_time: Optional[datetime] = None  # Double-trigger guard
        self._last_strategy_candle_time: Optional[datetime] = None  # Last closed execution-timeframe candle seen

        # Condition debug for UI
        self._condition_debug: dict = {}

        # Logging
        self.event_log: List[dict] = []

    def set_feed(self, feed):
        """Inject a LiveMarketFeed for WebSocket-driven mode."""
        self._feed = feed

    # ── Configuration ─────────────────────────────────────────
    def configure(self, strategy: dict, entry_conditions: list, exit_conditions: list, deploy_config: dict = None):
        """Configure the live trading engine with strategy + execution settings."""
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
        # Rebind the local too: everything below reads `deploy_config`, and a
        # None default made configure() raise on the fill-timeout lookup.
        deploy_config = deploy_config or strategy.get("deploy_config", {}) or {}
        self.deploy_config = deploy_config
        self.max_daily_loss = float(strategy.get("max_daily_loss", 0) or 0)

        # Pre-compute strategy-level SL/TP values
        self._sl_pct = float(strategy.get("stoploss_pct", 0) or 0)
        self._sl_rupees = float(strategy.get("stoploss_rupees", 0) or 0)
        self._tp_pct = float(strategy.get("target_profit_pct", 0) or 0)
        self._tp_rupees = float(strategy.get("target_profit_rupees", 0) or 0)
        self.initial_capital = float(strategy.get("initial_capital", 0) or 0)
        # Same rule as paper: on "auto" the instrument decides. Live does not
        # simulate slippage -- it gets real fills -- but the capital check and
        # its buffer come from the same row, and that check is what stands
        # between a signal and an order the account cannot fund.
        costs = resolve_execution_costs(strategy)
        self.execution_profile = costs["execution_profile"]
        self._enforce_capital = bool(costs["enforce_capital"])
        self._capital_buffer_pct = min(99.0, costs["capital_buffer_pct"])
        self._sell_option_margin_per_lot = get_sell_option_margin_per_lot(
            strategy.get("instrument", "26000"),
            costs["sell_option_margin_per_lot"],
        )
        fill_timeout = int(
            deploy_config.get("order_fill_timeout_sec", strategy.get("order_fill_timeout_sec", 15)) or 15
        )
        self._entry_fill_timeout_sec = max(
            3,
            int(deploy_config.get("entry_fill_timeout_sec", fill_timeout) or fill_timeout),
        )
        self._exit_fill_timeout_sec = max(
            3,
            int(deploy_config.get("exit_fill_timeout_sec", fill_timeout) or fill_timeout),
        )

        # Pre-parse market hours once (avoid per-tick string parsing)
        self._apply_session_times(strategy)

        self.log_event("info", f"Strategy configured: {strategy.get('run_name', 'Unnamed')}")
        self.log_event(
            "info",
            f"Mode: {'Auto Trading (REAL ORDERS)' if self.deploy_config.get('order_type') == 'auto' else 'Paper Testing'}",
        )
        self.log_event("info", f"Product: {self.deploy_config.get('product_type', 'MIS')}")
        self.log_event("info", f"Entry order: {self.deploy_config.get('entry_order', 'MARKET')}")
        if self._sl_rupees > 0 or self._sl_pct > 0:
            self.log_event(
                "info",
                f"Strategy SL: ₹{self._sl_rupees:,.0f}" if self._sl_rupees > 0 else f"Strategy SL: {self._sl_pct}%",
            )
        if self._tp_rupees > 0 or self._tp_pct > 0:
            self.log_event(
                "info",
                f"Strategy TP: ₹{self._tp_rupees:,.0f}" if self._tp_rupees > 0 else f"Strategy TP: {self._tp_pct}%",
            )

    @staticmethod
    def _format_missing_condition_gate(missing_fields: list[str]) -> str:
        preview = ", ".join(missing_fields[:3])
        extra = len(missing_fields) - 3
        if extra > 0:
            preview = f"{preview} +{extra} more"
        return f"missing_condition_data ({preview})"

    def _apply_session_times(self, strategy: dict):
        """Pre-parse the session clock once (avoids per-tick string parsing).

        Called from configure() AND from _load_state(), because a restart
        restores the strategy dict without reconfiguring. Miss the second call
        and the signal cutoff silently reverts to None — the guard would be off
        for the rest of the day with nothing in the log to say so.
        """
        mo = strategy.get("market_open", "09:15")
        mc = strategy.get("market_close", "15:25")
        self._market_open = time(*map(int, mo.split(":"))) if isinstance(mo, str) else mo
        self._market_close = time(*map(int, mc.split(":"))) if isinstance(mc, str) else mc
        # After this time no entry and no spot-driven exit may be decided: NSE's
        # closing auction means the index is no longer priced by real trades.
        # Stop-loss, target and the timed square-off are unaffected — they read
        # the option's own premium, and options trade until 15:40.
        sco = strategy.get("signal_cutoff_time") or ""
        if isinstance(sco, time):
            self._signal_cutoff = sco
        elif isinstance(sco, str) and sco.strip():
            self._signal_cutoff = time(*map(int, sco.strip().split(":")))
        else:
            self._signal_cutoff = None

    def _signals_live(self, at: datetime | None = None) -> bool:
        """False once the spot feed stops being real traded prices."""
        if self._signal_cutoff is None:
            return True
        moment = at or self.current_time or _now_ist()
        return moment.time() < self._signal_cutoff

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
            return False, {
                "time": now.strftime("%H:%M:%S"),
                "overall": False,
                "raw_overall": False,
                "gate": f"signal_cutoff ({self._signal_cutoff.strftime('%H:%M')})",
                "conditions": [],
            }
        raw_overall, cond_details, missing_fields = inspect_condition_group(latest_row, self.entry_conditions, prev_row)
        entry_sig = raw_overall and not missing_fields
        self._log_cross_skips("entry")
        debug_payload = {
            "time": now.strftime("%H:%M:%S"),
            "overall": entry_sig,
            "raw_overall": raw_overall,
            "gate": "evaluating" if not missing_fields else self._format_missing_condition_gate(missing_fields),
            "conditions": cond_details,
        }
        if missing_fields:
            debug_payload["missing_fields"] = missing_fields
        return entry_sig, debug_payload

    def _position_unrealized_pnl(self, position: dict, current_premium: float | None = None) -> float:
        premium = float(
            current_premium
            if current_premium is not None
            else position.get("current_premium", position.get("entry_premium", 0.0)) or 0.0
        )
        direction = 1 if position.get("transaction_type") == "BUY" else -1
        quantity = int(position.get("quantity") or (position.get("lots", 0) * position.get("lot_size", 0)))
        return (premium - float(position.get("entry_premium", 0.0))) * direction * quantity

    def _portfolio_unrealized_pnl(self) -> float:
        return sum(
            self._position_unrealized_pnl(position) for position in self.positions if position.get("status") != "closed"
        )

    def _set_strategy_thresholds(self, positions: List[dict]):
        entry_notional = sum(
            float(position.get("entry_premium", 0.0))
            * int(position.get("quantity") or (position.get("lots", 0) * position.get("lot_size", 0)))
            for position in positions
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
    def _position_lots(position: dict) -> float:
        try:
            lot_size = float(position.get("lot_size", 0) or 0)
            quantity = float(LiveEngine._position_quantity(position))
            if lot_size > 0:
                return quantity / lot_size
            return float(position.get("lots", 0) or 0)
        except Exception:
            return float(position.get("lots", 0) or 0)

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

    def _product_type(self, strategy: dict | None = None, deploy_config: dict | None = None) -> str:
        strategy = strategy if isinstance(strategy, dict) else (self.strategy or {})
        deploy_config = (
            deploy_config
            if isinstance(deploy_config, dict)
            else (self.deploy_config or strategy.get("deploy_config", {}) or {})
        )
        raw = (deploy_config or {}).get("product_type") or strategy.get("product_type")
        return self._normalize_product_type(raw)

    def _is_intraday_product(self, strategy: dict | None = None, deploy_config: dict | None = None) -> bool:
        return self._product_type(strategy, deploy_config) == "MIS"

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

    def _mark_intraday_rollover_positions(self, reason: str = "MIS_SESSION_ROLLOVER") -> bool:
        if not self.positions or not self._is_intraday_product():
            return False
        marked = 0
        for pos in self.positions:
            if pos.get("status") == "closed":
                continue
            if pos.get("_force_exit_reason") == reason:
                continue
            pos["_force_exit_reason"] = reason
            marked += 1
        if marked:
            self.in_trade = True
            self.log_event(
                "warning",
                f"Intraday position carried past session close — {marked} open leg(s) will exit when the market opens",
            )
            self._save_state()
        return marked > 0

    @staticmethod
    def _broker_position_quantity(position: dict | None) -> int:
        if not isinstance(position, dict):
            return 0
        for key in ("netQty", "netQuantity", "quantity", "qty"):
            value = position.get(key)
            if value is None or value == "":
                continue
            parsed_qty = None
            try:
                parsed_qty = int(round(abs(float(value))))
            except (TypeError, ValueError):
                parsed_qty = None
            if parsed_qty is None:
                continue
            return max(0, parsed_qty)
        return 0

    def _resolve_position_security_id(self, pos: dict) -> str:
        security_id = str(pos.get("security_id") or pos.get("securityId") or "").strip()
        if security_id:
            return security_id
        try:
            security_id = str(
                ScripMaster.lookup(
                    pos.get("underlying", ""),
                    int(pos.get("strike") or 0),
                    pos.get("expiry", ""),
                    pos.get("option_type", ""),
                )
                or ""
            ).strip()
        except Exception:
            security_id = ""
        if security_id:
            pos["security_id"] = security_id
        return security_id

    def _broker_exit_premium(self, pos: dict, broker_position: dict | None = None) -> float:
        candidates = []
        if isinstance(broker_position, dict):
            candidates.extend(
                [
                    broker_position.get("lastTradedPrice"),
                    broker_position.get("lastPrice"),
                    broker_position.get("ltp"),
                    broker_position.get("averagePrice"),
                    broker_position.get("buyAvg"),
                    broker_position.get("sellAvg"),
                    broker_position.get("buyAvgPrice"),
                    broker_position.get("sellAvgPrice"),
                    broker_position.get("costPrice"),
                ]
            )
        candidates.extend([pos.get("current_premium"), pos.get("entry_premium")])
        for candidate in candidates:
            premium = self._safe_float(candidate, 0.0)
            if premium > 0:
                return premium
        return 0.0

    async def _reconcile_broker_positions(self, callback=None) -> bool:
        if not self.positions:
            return False
        try:
            broker_positions = await self.dhan.async_get_positions()
        except Exception as exc:
            self.log_event("warning", f"Broker position sync failed: {exc}")
            return False

        broker_map: dict[str, dict] = {}
        for broker_position in broker_positions or []:
            if not isinstance(broker_position, dict):
                continue
            security_id = str(broker_position.get("securityId") or broker_position.get("security_id") or "").strip()
            if security_id:
                broker_map[security_id] = broker_position

        changed = False
        for pos in list(self.positions):
            if pos.get("status") == "closed":
                continue
            if pos.get("_exit_in_flight"):
                continue
            engine_qty = self._position_quantity(pos)
            if engine_qty <= 0:
                continue

            security_id = self._resolve_position_security_id(pos)
            broker_position = broker_map.get(security_id) if security_id else None
            broker_qty = self._broker_position_quantity(broker_position)
            if broker_qty >= engine_qty:
                continue

            exit_premium = self._broker_exit_premium(pos, broker_position)
            if exit_premium <= 0:
                exit_premium = self._safe_float(pos.get("entry_premium"), 0.0)

            if broker_qty <= 0:
                closed_trade = await self._record_closed_trade(pos, "BROKER_MANUAL_EXIT", exit_premium, engine_qty)
                async with self._trades_lock:
                    if pos in self.positions:
                        self.positions.remove(pos)
                self.log_event(
                    "warning",
                    f"Broker exit detected outside engine for Leg {pos.get('leg_num')}: synced full close",
                )
            else:
                closed_qty = max(0, engine_qty - broker_qty)
                if closed_qty <= 0:
                    continue
                closed_trade = await self._record_closed_trade(pos, "BROKER_PARTIAL_EXIT", exit_premium, closed_qty)
                pos["quantity"] = broker_qty
                pos["lots"] = broker_qty / pos["lot_size"] if pos.get("lot_size") else pos.get("lots", 0)
                pos["current_premium"] = exit_premium
                pos["unrealized_pnl"] = round(self._position_unrealized_pnl(pos, exit_premium), 2)
                pos.pop("_force_exit_reason", None)
                pos["partial_exit_count"] = int(pos.get("partial_exit_count", 0) or 0) + 1
                self.log_event(
                    "warning",
                    f"Broker partial exit detected for Leg {pos.get('leg_num')}: synced {closed_qty} qty, {broker_qty} remaining",
                )

            changed = True
            self.in_trade = bool(self.positions)
            if not self.positions:
                self._signal_candle = None
                self.strat_sl_val = 0
                self.strat_tp_val = 0
            self._save_state()
            if callback and closed_trade:
                await self._emit(callback, {"type": "exit", "trade": closed_trade, **self.get_status()})

        return changed

    async def _force_market_close_if_needed(self, current_dt: datetime, callback=None) -> bool:
        if current_dt.time() < self._market_close:
            return False

        if self._pending_order:
            self.log_event("info", "Pending order cleared at market close")
            self._clear_pending_order()

        if not self.positions:
            return False
        if not self._is_intraday_product():
            return False

        self.log_event(
            "warning",
            f"⏰ Market close reached ({self._market_close.strftime('%H:%M')}) — force exiting {len(self.positions)} open position(s)",
        )

        for pos in list(self.positions):
            if pos.get("status") == "closed":
                continue

            exit_px = self._safe_float(pos.get("current_premium"), 0.0)
            if exit_px <= 0:
                exit_px = self._safe_float(self._get_premium_from_feed(pos), 0.0)
            if exit_px <= 0:
                try:
                    exit_px = self._safe_float(await self._get_current_premium(pos), 0.0)
                except Exception as exc:
                    self.log_event("warning", f"Market-close premium fetch failed for Leg {pos.get('leg_num')}: {exc}")
            if exit_px <= 0:
                exit_px = self._safe_float(pos.get("entry_premium"), 0.0)

            await self._exit_position(pos, "MARKET_CLOSE", exit_px, callback)

        return True

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
        return self._market_open < close_time.time() <= self._market_close

    def _entry_can_be_scheduled(self, signal_candle_time) -> bool:
        if not self._strategy_candle_is_current_session(signal_candle_time):
            return False
        ready_at = self._pending_order_ready_at(signal_candle_time)
        return ready_at.date() == signal_candle_time.date() and ready_at.time() <= self._market_close

    async def _verify_order_execution(
        self,
        order_id: str,
        expected_qty: int,
        stage: str,
        label: str,
        timeout_sec: int,
    ) -> dict:
        verification = {
            "order_id": order_id,
            "stage": stage,
            "label": label,
            "expected_qty": int(expected_qty or 0),
            "status": "UNKNOWN",
            "filled_qty": 0,
            "avg_price": 0.0,
            "passed": False,
            "partial_fill": False,
            "safe_to_retry": False,
            "message": "",
        }

        try:
            result = await self.dhan.async_verify_order_fill(order_id, max_wait_sec=max(3, int(timeout_sec or 15)))
        except Exception as exc:
            verification["message"] = f"verification_failed: {exc}"
            self.order_verification_failures += 1
            self.last_order_verification = verification
            self.log_event("error", f"{stage.title()} verification failed for {label}: {exc}")
            return verification

        status = str(result.get("status", "UNKNOWN") or "UNKNOWN").upper()
        filled_qty = int(result.get("filled_qty") or 0)
        if status == "FILLED" and filled_qty <= 0 and expected_qty > 0:
            filled_qty = expected_qty
        avg_price = self._safe_float(result.get("avg_price"), 0.0)
        partial_fill = 0 < filled_qty < int(expected_qty or 0)
        passed = status == "FILLED" and not partial_fill and (expected_qty <= 0 or filled_qty >= expected_qty)

        verification.update(
            {
                "status": status,
                "raw_status": str(result.get("raw_status", status) or status).upper(),
                "requested_qty": int(result.get("requested_qty") or expected_qty or 0),
                "filled_qty": filled_qty,
                "avg_price": avg_price,
                "partial_fill": partial_fill,
                "passed": passed,
                "message": str(result.get("message", "") or ""),
            }
        )

        if not passed:
            self.order_verification_failures += 1
            # May this leg be sent again? Only when the broker is certain
            # nothing happened. A clean refusal is certain; a timeout is not.
            safe_to_retry = status in {"REJECTED", "CANCELLED", "EXPIRED"} and filled_qty <= 0
            if status in {"TIMEOUT", "UNKNOWN", "OPEN", "PENDING"} or partial_fill:
                try:
                    await self.dhan.async_cancel_order(order_id)
                    verification["cancelled_after_failure"] = True
                    # The cancel was ACCEPTED, so the order was still resting
                    # unfilled when we pulled it. That -- and only that --
                    # makes an unfilled market order safe to send again.
                    safe_to_retry = not partial_fill
                except Exception as cancel_exc:
                    verification["cancel_after_failure_error"] = str(cancel_exc)
                    # A cancel that will not go through is the signature of an
                    # order that already traded. Never re-send after this.
                    safe_to_retry = False
            verification["safe_to_retry"] = bool(safe_to_retry)
            self.log_event(
                "warning",
                f"{stage.title()} order not fully filled for {label}: {status} qty {filled_qty}/{expected_qty} {verification['message']}".strip(),
            )
        else:
            self.log_event(
                "order",
                f"{stage.title()} fill verified for {label}: qty {filled_qty}/{expected_qty} @ ₹{avg_price:.2f}",
            )

        self.last_order_verification = verification
        return verification

    async def _emergency_flatten_partial_entry(
        self,
        *,
        underlying: str,
        strike: int,
        expiry: str,
        option_type: str,
        transaction_type: str,
        filled_qty: int,
        product_type: str,
        label: str,
    ) -> bool:
        if filled_qty <= 0:
            return True

        opposite_txn = "SELL" if transaction_type == "BUY" else "BUY"
        self.log_event(
            "warning",
            f"Partial entry fill detected for {label}: flattening {filled_qty} qty with emergency {opposite_txn} order",
        )
        try:
            result = await self.dhan.async_place_option_order(
                underlying=underlying,
                strike_price=strike,
                option_type=option_type,
                expiry=expiry,
                transaction_type=opposite_txn,
                quantity=filled_qty,
                order_type="MARKET",
                product_type=product_type,
                tag=f"AF_EMER_{option_type}_{strike}",
            )
            flatten_order_id = result.get("orderId", "")
            verification = await self._verify_order_execution(
                flatten_order_id,
                filled_qty,
                stage="emergency_exit",
                label=label,
                timeout_sec=self._exit_fill_timeout_sec,
            )
            if verification.get("passed"):
                self.log_event("warning", f"Emergency flatten succeeded for {label}")
                return True
        except Exception as exc:
            self.log_event("error", f"Emergency flatten failed for {label}: {exc}")

        self.manual_intervention_required = True
        self.running = False
        self.log_event("error", f"CRITICAL: Manual intervention required for partial entry fill on {label}")
        return False

    @staticmethod
    def _extract_available_balance(funds: dict) -> float:
        if not isinstance(funds, dict):
            return 0.0
        for key in (
            "availabelBalance",
            "availableBalance",
            "available_balance",
            "availableMargin",
            "available_margin",
            "netMarginAvailable",
        ):
            value = funds.get(key)
            try:
                if value is not None:
                    return max(0.0, float(value))
            except (TypeError, ValueError):
                continue
        return 0.0

    def _capital_required_for_entry(
        self, transaction_type: str, entry_premium: float, lots: int, quantity: int
    ) -> float:
        if transaction_type == "SELL":
            return float(lots) * self._sell_option_margin_per_lot
        return max(0.0, float(entry_premium)) * float(quantity)

    def _capital_limit(self, available_capital: float) -> float:
        return max(0.0, float(available_capital)) * max(0.0, 1.0 - self._capital_buffer_pct / 100.0)

    async def _can_enter_trade(self, planned_entries: list[dict]) -> bool:
        if not self._enforce_capital:
            self.last_capital_check = {"enforced": False, "passed": True}
            return True

        required_capital = sum(
            self._capital_required_for_entry(
                entry.get("transaction_type", "BUY"),
                float(entry.get("entry_premium", 0.0) or 0.0),
                int(entry.get("lots", 0) or 0),
                int(entry.get("quantity", 0) or 0),
            )
            for entry in planned_entries
        )

        try:
            funds = await self.dhan.async_get_funds()
        except Exception as exc:
            self.capital_rejections += 1
            self.last_capital_check = {
                "enforced": True,
                "passed": False,
                "required": round(required_capital, 2),
                "available": 0.0,
                "limit": 0.0,
                "reason": f"funds_fetch_failed: {exc}",
            }
            self.log_event("error", f"Capital check failed: unable to fetch broker funds ({exc})")
            return False

        available_capital = self._extract_available_balance(funds)
        capital_limit = self._capital_limit(available_capital)
        passed = required_capital <= capital_limit + 1e-9
        self.last_capital_check = {
            "enforced": True,
            "passed": passed,
            "required": round(required_capital, 2),
            "available": round(available_capital, 2),
            "limit": round(capital_limit, 2),
            "buffer_pct": self._capital_buffer_pct,
            "mode": "broker",
        }
        if not passed:
            self.capital_rejections += 1
            self.log_event(
                "warning",
                f"Capital check blocked entry: required ₹{required_capital:,.0f} > usable ₹{capital_limit:,.0f}",
            )
            return False
        return True

    # ── STATE PERSISTENCE ─────────────────────────────────────
    def _save_state(self):
        """Persist full engine config + trading state to disk so it survives restarts."""
        try:
            state = {
                "session_date": str(self.session_date) if self.session_date else None,
                # Full configuration — enough to reconstruct the engine
                "strategy": self.strategy,
                "entry_conditions": self.entry_conditions,
                "exit_conditions": self.exit_conditions,
                "deploy_config": self.deploy_config,
                # Trading state
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
                "order_verification_failures": self.order_verification_failures,
                "last_order_verification": self.last_order_verification,
                "manual_intervention_required": self.manual_intervention_required,
                # Market data snapshot
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
            print(f"[LIVE] State save failed: {e}")

    def _load_state(self):
        """Load last session state from disk (called on __init__)."""
        try:
            if not os.path.exists(self._state_file):
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
                print(f"[LIVE] Stale state from {saved_date} (today={today}) — ignoring")
                return

            # Restore full configuration
            self.session_date = _now_ist().date()
            if state.get("strategy"):
                self.strategy = state["strategy"]
            if state.get("entry_conditions"):
                self.entry_conditions = state["entry_conditions"]
            if state.get("exit_conditions"):
                self.exit_conditions = state["exit_conditions"]
            if state.get("deploy_config"):
                self.deploy_config = state["deploy_config"]
            self.strategy = dict(self.strategy or {})
            self.strategy["indicators"] = normalize_strategy_indicators(
                self.strategy.get("indicators", []),
                entry_conditions=self.entry_conditions,
                exit_conditions=self.exit_conditions,
            )
            self._apply_session_times(self.strategy)

            # Restore trading state
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
            self.order_verification_failures = state.get("order_verification_failures", 0)
            self.last_order_verification = state.get("last_order_verification", {}) or {}
            self.manual_intervention_required = bool(state.get("manual_intervention_required", False))

            # Restore market data snapshot
            self.current_spot = state.get("current_spot", 0.0)
            self.current_candle = state.get("current_candle", {})
            self.current_indicators = state.get("current_indicators", {})

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
                if self._is_intraday_product(self.strategy, self.deploy_config):
                    self._mark_intraday_rollover_positions()
                    print(
                        f"[LIVE] Restored stale intraday state from {saved_date}: "
                        f"{len(open_positions)} position(s) queued for next-session exit"
                    )
                else:
                    self.log_event(
                        "info",
                        f"Carry product restored from {saved_date} — daily counters reset, "
                        f"{len(open_positions)} position(s) remain open",
                    )
                    self._save_state()
                    print(
                        f"[LIVE] Restored carry state from {saved_date}: "
                        f"{len(open_positions)} open position(s) preserved"
                    )

            n_trades = len(self.closed_trades)
            n_pos = len(self.positions)
            pnl = sum(t.get("pnl", 0) for t in self.closed_trades)
            print(f"[LIVE] Restored state: {n_trades} trades, {n_pos} open positions, P&L=₹{pnl:,.2f}")
        except Exception as e:
            print(f"[LIVE] State load failed: {e}")

    def _delete_state_file(self):
        """Remove state file (called when engine is manually stopped)."""
        try:
            if os.path.exists(self._state_file):
                os.remove(self._state_file)
        except Exception as e:
            print(f"[LIVE] State file delete failed: {e}")

    # ── Logging ───────────────────────────────────────────────
    def _spawn_tracked(self, coro, label: str) -> asyncio.Task:
        """Run a coroutine detached, but keep a strong reference to it.

        asyncio only holds a weak reference to a running task, so a task the
        caller drops can be garbage-collected part-way through. For the leg
        stop-loss that means the protective order simply never arrives, and
        because _place_sl_order handles its own exceptions there would be
        nothing in the log to say so. Holding the task here and reporting what
        it raised is the difference between a stop that failed and a stop that
        never happened.
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _finished(done: asyncio.Task, _label=label):
            self._background_tasks.discard(done)
            if done.cancelled():
                self.log_event("error", f"CRITICAL: {_label} was cancelled before it completed")
                return
            exc = done.exception()
            if exc is not None:
                self.log_event("error", f"CRITICAL: {_label} raised {type(exc).__name__}: {exc}")

        task.add_done_callback(_finished)
        return task

    def _unprotected_legs(self) -> list[dict]:
        """Open legs that carry a stop percentage with nothing resting at Dhan.

        Either the deploy asked for no broker stop, or the order that would
        have placed one never came back with an id. Both leave the stop living
        only inside this process, which is exactly the state the panel has to
        show rather than imply.
        """
        out = []
        for pos in self.positions:
            if pos.get("status") == "closed":
                continue
            if self._safe_float(pos.get("sl_pct"), 0.0) <= 0:
                continue
            if str(pos.get("sl_order_id") or "").strip():
                continue
            out.append(pos)
        return out

    def log_event(self, event_type: str, message: str, data: dict = None):
        event = {"time": _now_ist(), "type": event_type, "message": message, "data": data or {}}
        self.event_log.append(event)
        ts = event["time"].strftime("%H:%M:%S")
        print(f"[LIVE] [{ts}] [{event_type.upper()}] {message}")

    async def _emit(self, callback, event: dict):
        if not callback:
            return
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)
        except Exception as e:
            print(f"[LIVE] Callback error: {e}")

    def _compute_candle_latency(self, candle_time, now: Optional[datetime] = None) -> float:
        """Normalize candle latency around session boundaries."""
        if not isinstance(candle_time, datetime):
            return 0.0
        now = now or _now_ist()
        market_open = getattr(self, "_market_open", time(9, 15))
        session_open = datetime.combine(now.date(), market_open)
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
        """
        Normalize WS candle snapshots to closed strategy candles only.

        The feed callback should fire on candle close, but shared/reconnecting feed
        state can occasionally hand us a snapshot that still includes the first
        forming strategy candle. Never evaluate entry/exit conditions on that bar.
        """
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
        self._last_processed_candle_time = None
        self._entry_signal_pending = False
        self._pending_order = None
        self._signal_candle = None
        self._condition_debug = {"gate": gate, "conditions": []}

    # ── Diagnostic / Debug ─────────────────────────────────────
    def debug_engine_state(self) -> dict:
        """Return a comprehensive snapshot of engine state for debugging silent failures.
        Call from /api endpoint or logging to diagnose why trades aren't triggering."""
        feed_status = "not_injected"
        last_tick_age = -1.0
        ws_connected = False
        if self._feed:
            ws_connected = getattr(self._feed, "is_running", False)
            last_tick_age = getattr(self._feed, "last_tick_age_seconds", -1.0)
            feed_status = "running" if ws_connected else "stopped"

        pending_info = None
        if self._pending_order:
            po = self._pending_order
            pending_info = {
                "signal_candle_time": str(po.get("signal_candle_time")),
                "created_at": str(po.get("created_at")),
                "attempts": po.get("attempts", 0),
                "retry_at": str(po.get("retry_at")) if po.get("retry_at") else None,
                "age_seconds": (_now_ist() - po["created_at"]).total_seconds() if po.get("created_at") else 0,
            }

        return {
            "timestamp": _now_ist().isoformat(),
            "is_running": self.running,
            "is_trade_active": self.in_trade,
            "ws_mode": self._ws_mode,
            "is_websocket_connected": ws_connected,
            "feed_status": feed_status,
            "last_tick_age_seconds": round(last_tick_age, 1),
            "current_spot": self.current_spot,
            "current_signal_status": {
                "entry_signal_pending": self._entry_signal_pending,
                "pending_order": pending_info,
                "last_processed_candle_time": str(self._last_processed_candle_time),
            },
            "trading_state": {
                "trades_today": self.trades_today,
                "max_trades_per_day": self.strategy.get("max_trades_per_day", 1),
                "daily_pnl": round(self.daily_pnl, 2),
                "max_daily_loss": self.max_daily_loss,
                "daily_loss_hit": self.max_daily_loss > 0 and self.daily_pnl <= -self.max_daily_loss,
                "profit_cooldown_active": self._profit_cooldown_active(),
                "profit_cooldown_trigger_date": str(self.profit_cooldown_trigger_date)
                if self.profit_cooldown_trigger_date
                else None,
                "open_positions": len(self.positions),
                "closed_trades": len(self.closed_trades),
                "manual_intervention_required": self.manual_intervention_required,
                "order_verification_failures": self.order_verification_failures,
            },
            "data_state": {
                "candle_buffer_rows": len(self.candle_buffer),
                "candle_buffer_empty": self.candle_buffer.empty,
                "has_indicators": bool(self.current_indicators),
                "has_candle": bool(self.current_candle),
            },
            "positions": [
                {
                    "id": p.get("id"),
                    "symbol": p.get("display_symbol"),
                    "status": p.get("status"),
                    "entry_premium": p.get("entry_premium"),
                    "current_premium": p.get("current_premium"),
                    "unrealized_pnl": p.get("unrealized_pnl"),
                    "exit_attempts": p.get("_exit_attempts", 0),
                }
                for p in self.positions
            ],
        }

    # ── Main Loop ─────────────────────────────────────────────
    async def start(self, callback=None):
        """Start the live trading engine."""
        self.running = True
        self.session_date = _now_ist().date()
        self.daily_pnl = 0.0
        self._reset_intraday_status()

        self.log_event("start", "🚀 Live Auto-Trading Engine Started (REAL ORDERS)")
        self.log_event("info", f"Instrument: {self._get_instrument_name()}")
        self.log_event("info", f"Timeframe: {describe_timeframe(self._get_timeframe_spec())}")
        self.log_event("info", f"Max trades/day: {self.strategy.get('max_trades_per_day', 1)}")
        if self.max_daily_loss > 0:
            self.log_event("info", f"Max daily loss: ₹{self.max_daily_loss:,.0f}")

        # Pre-load scrip master for option lookup
        self.log_event("info", "Loading scrip master for option security IDs...")
        try:
            loaded = ScripMaster.ensure_loaded()
            if loaded:
                symbol = self._get_underlying_symbol()
                expiry = ScripMaster.get_nearest_expiry(symbol)
                self.log_event("info", f"Scrip master loaded. Nearest {symbol} expiry: {expiry}")
            else:
                self.log_event("warning", "Scrip master not loaded — option orders may fail")
        except Exception as e:
            self.log_event("error", f"Scrip master load error: {e}")

        # ── Choose mode: WebSocket (fast) or REST polling (fallback) ──
        self._ws_mode = self._feed is not None and self._feed.is_running

        if self._ws_mode:
            self.log_event("info", "⚡ Mode: WebSocket (event-driven, ~1-2s latency)")
            await self._run_ws_mode(callback)
        else:
            self.log_event("info", "🔄 Mode: REST polling (fallback)")
            poll_interval = self.strategy.get("poll_interval", config.POLL_INTERVAL_SEC)
            while self.running:
                try:
                    await self._tick(callback)
                except Exception as e:
                    self.log_event("error", f"Tick error: {str(e)}")
                    import traceback

                    traceback.print_exc()
                await asyncio.sleep(poll_interval)

    def stop(self):
        """Stop the live trading engine."""
        self.running = False

        # Force close open positions
        if self.positions:
            self.log_event("warning", f"Engine stopping with {len(self.positions)} open positions!")
            for pos in self.positions:
                self.log_event(
                    "warning",
                    f"⚠ Open position: {pos.get('underlying', '')} "
                    f"{pos.get('strike', '')}{pos.get('option_type', '')} "
                    f"Order ID: {pos.get('entry_order_id', '?')}",
                )

        total_pnl = sum(t.get("pnl", 0) for t in self.closed_trades)
        win_count = len([t for t in self.closed_trades if t.get("pnl", 0) > 0])

        self.log_event("stop", "🛑 Live Auto-Trading Engine Stopped")
        self.log_event(
            "info", f"Session: {len(self.closed_trades)} trades | Winners: {win_count} | P&L: ₹{total_pnl:,.2f}"
        )
        self._save_state()  # Persist final state

    # ── WebSocket Event-Driven Mode ───────────────────────────
    async def _run_ws_mode(self, callback=None):
        """
        WebSocket-driven loop — same logic as paper_trading._run_ws_mode()
        but places REAL orders on entry/exit.
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
            loop.call_soon_threadsafe(self._candle_event.set)

        # Bootstrap history for indicator warm-up
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
        self.log_event("info", f"📊 Candle aggregation: {agg_label} ({len(history_df)} historical candles)")

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

                # New day reset
                if now.date() != self.session_date:
                    if self.positions:
                        if self._is_intraday_product():
                            self._mark_intraday_rollover_positions()
                        else:
                            self.log_event(
                                "info", f"Carry position preserved into new session ({self._product_type()})"
                            )
                            self._save_state()
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

                if self._is_closed_market_day(now.date()):
                    if self._pending_order:
                        self.log_event("info", "Pending order cleared on closed market day")
                        self._clear_pending_order()
                        self._save_state()
                    await asyncio.sleep(5)
                    if callback:
                        await self._emit(callback, {"type": "status", "message": "Outside market hours"})
                    continue

                # Market hours check (pre-parsed in configure())
                allow_final_candle_grace = now <= now.replace(
                    hour=self._market_close.hour,
                    minute=self._market_close.minute,
                    second=2,
                    microsecond=0,
                )

                if (
                    now.time() >= self._market_close
                    and not allow_final_candle_grace
                    and not self._candle_event.is_set()
                ):
                    await self._force_market_close_if_needed(now, callback)
                    await asyncio.sleep(5)
                    if callback:
                        await self._emit(callback, {"type": "status", "message": "Outside market hours"})
                    continue

                if now.time() < self._market_open:
                    await asyncio.sleep(5)
                    if callback:
                        await self._emit(callback, {"type": "status", "message": "Outside market hours"})
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

                # ── Monitor positions every 1 second ──
                if self.in_trade:
                    for pos in list(self.positions):
                        if pos.get("status") == "closed":
                            continue
                        # Get LTP from feed cache (instant)
                        current_premium = self._get_premium_from_feed(pos)
                        # REST fallback every ~5s if WS has no data yet
                        if current_premium <= 0:
                            rest_counter = pos.get("_rest_counter", 0) + 1
                            pos["_rest_counter"] = rest_counter
                            if rest_counter % 5 == 1:
                                try:
                                    current_premium = (
                                        self.dhan.get_option_ltp(
                                            pos.get("underlying", ""),
                                            int(pos["strike"]),
                                            pos["expiry"],
                                            pos["option_type"],
                                        )
                                        or 0.0
                                    )
                                except Exception:
                                    current_premium = 0.0
                        if current_premium > 0:
                            pos["current_premium"] = current_premium
                            direction = 1 if pos["transaction_type"] == "BUY" else -1
                            qty = self._position_quantity(pos)
                            pos["unrealized_pnl"] = round((current_premium - pos["entry_premium"]) * direction * qty, 2)

                    if self.in_trade:
                        await self._reconcile_broker_positions(callback)

                    latest_row = self.candle_buffer.iloc[-1] if not self.candle_buffer.empty else None
                    if self.in_trade and latest_row is not None:
                        strategy_exit = self._check_strategy_exit()
                        if strategy_exit:
                            for pos in list(self.positions):
                                if pos.get("status") == "closed":
                                    continue
                                await self._exit_position(pos, strategy_exit, pos.get("current_premium", 0.0), callback)
                        else:
                            for pos in list(self.positions):
                                if pos.get("status") == "closed":
                                    continue
                                # Between candle closes, only intrabar/price exits should fire.
                                # Never evaluate full signal-exit logic on the same closed row that
                                # just armed/executed the pending entry, or a fresh trade can close
                                # immediately as EXIT_SIGNAL.
                                exit_reason = pos.get("_force_exit_reason") or self._check_exit_conditions(
                                    pos,
                                    latest_row,
                                    pos["current_premium"],
                                    allow_signal_exit=False,
                                )
                                if exit_reason:
                                    await self._exit_position(pos, exit_reason, pos["current_premium"], callback)

                # ── Wait for candle close event ──
                try:
                    await asyncio.wait_for(self._candle_event.wait(), timeout=1.0)
                    self._candle_event.clear()
                except asyncio.TimeoutError:
                    # Periodic feed health check (~every 60s, not every 1s loop)
                    if self._feed and hasattr(self._feed, "last_tick_age_seconds"):
                        age = self._feed.last_tick_age_seconds
                        if age > 60 and int(age) % 60 < 2:
                            self.log_event("warning", f"⚠ No ticks for {age:.0f}s — feed may be dead, checking health")
                            self._feed.check_health()

                    # ── "Quantman Way" — flush pending order at 1st second of new candle ──
                    await self._try_flush_pending_order(callback)

                    if callback:
                        await self._emit(callback, self.get_status())
                    continue

                # ── Candle closed — evaluate conditions ──
                candle_df = self._latest_candle_df
                if candle_df is None or candle_df.empty:
                    continue

                now = _now_ist()
                self.current_time = now
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
                    if now.time() >= self._market_close:
                        await self._force_market_close_if_needed(now, callback)
                    if callback:
                        await self._emit(callback, self.get_status())
                    continue

                self.current_spot = float(latest_row.get("close", self.current_spot))

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
                        for pos in list(self.positions):
                            if pos.get("status") == "closed":
                                continue
                            await self._exit_position(pos, strategy_exit, pos.get("current_premium", 0.0), callback)
                    else:
                        for pos in list(self.positions):
                            if pos.get("status") == "closed":
                                continue
                            exit_reason = pos.get("_force_exit_reason") or self._check_exit_conditions(
                                pos,
                                latest_row,
                                pos["current_premium"],
                            )
                            if exit_reason:
                                await self._exit_position(pos, exit_reason, pos["current_premium"], callback)

                # ── Check entry (Quantman Way — signal → pendingOrder → flush on next candle) ──
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
                    if self._pending_order:
                        po = self._pending_order
                        # Double-trigger guard
                        if now.time() >= self._market_close:
                            self.log_event("info", "Pending order cleared at session end")
                            self._clear_pending_order()
                        elif not self._pending_order_is_current_session(po, now):
                            self.log_event("info", "Pending order cleared — stale session signal")
                            self._clear_pending_order()
                        elif not self._is_pending_order_ready(po, now):
                            pass
                        elif self._last_processed_candle_time == po.get("signal_candle_time"):
                            self.log_event("debug", "Duplicate pending order for already-processed candle — discarding")
                            self._clear_pending_order()
                        else:
                            self.log_event(
                                "entry",
                                f"🚀 Executing pending entry (candle boundary @ {_now_ist().strftime('%H:%M:%S')})",
                            )
                            await self._flush_pending_order(latest_row, callback)
                    else:
                        # Evaluate new signal on this closed candle
                        prev_row = df_with_indicators.iloc[-2] if len(df_with_indicators) >= 2 else None
                        entry_sig, self._condition_debug = self._evaluate_entry_conditions_with_debug(
                            latest_row,
                            prev_row,
                            now,
                        )
                        if entry_sig:
                            if self._entry_can_be_scheduled(strategy_candle_time):
                                ready_at = self._pending_order_ready_at(strategy_candle_time)
                                self._pending_order = {
                                    "signal_candle_time": strategy_candle_time,
                                    "created_at": _now_ist(),
                                    "ready_at": ready_at,
                                    "row": latest_row,
                                    "attempts": 0,
                                    "retry_at": None,
                                }
                                self._entry_signal_pending = True
                                self._signal_candle = {
                                    "Signal_Candle_Open": float(latest_row["open"]),
                                    "Signal_Candle_High": float(latest_row["high"]),
                                    "Signal_Candle_Low": float(latest_row["low"]),
                                    "Signal_Candle_Close": float(latest_row["close"]),
                                }
                                self.log_event(
                                    "signal",
                                    f"⚡ ENTRY SIGNAL @ candle {strategy_candle_time} — will enter on NEXT candle open @ {ready_at.strftime('%H:%M:%S')}",
                                )
                            else:
                                self._condition_debug = {"gate": "market_close_boundary", "conditions": []}
                                self.log_event(
                                    "info", "Entry signal ignored — no next tradable candle before market close"
                                )
                elif self.in_trade:
                    self._condition_debug = {"gate": "in_trade", "conditions": []}
                    if self._pending_order:
                        self.log_event("warning", "⚠ Pending order cleared — cannot execute: in_trade=True")
                        self._clear_pending_order()
                elif self.trades_today >= max_trades:
                    self._condition_debug = {
                        "gate": f"max_trades_reached ({self.trades_today}/{max_trades})",
                        "conditions": [],
                    }
                    if self._pending_order:
                        self.log_event(
                            "warning",
                            f"⚠ Pending order cleared — cannot execute: trades_today({self.trades_today})>=max({max_trades})",
                        )
                        self._clear_pending_order()
                elif daily_loss_hit:
                    self._condition_debug = {"gate": f"daily_loss_limit (PnL={self.daily_pnl:.2f})", "conditions": []}
                    if self._pending_order:
                        self.log_event(
                            "warning",
                            f"⚠ Pending order cleared — cannot execute: daily_loss_hit(PnL={self.daily_pnl:.0f})",
                        )
                        self._clear_pending_order()
                elif cooldown_hit:
                    self._condition_debug = {
                        "gate": f"profit_cooldown (big-profit day {self.profit_cooldown_trigger_date})",
                        "conditions": [],
                    }
                    if self._pending_order:
                        self.log_event("info", "Pending order cleared — profit cooldown session")
                        self._clear_pending_order()

                self._prev_row = latest_row
                self._last_strategy_candle_time = strategy_candle_time

                if now.time() >= self._market_close:
                    await self._force_market_close_if_needed(now, callback)

                if callback:
                    await self._emit(callback, self.get_status())

            except Exception as e:
                self.log_event("error", f"WS mode error: {str(e)}")
                import traceback

                traceback.print_exc()
                await asyncio.sleep(1)

    def _get_premium_from_feed(self, pos: dict) -> float:
        """Get option premium from WebSocket feed's LTP cache (instant)."""
        if not self._feed:
            return 0.0
        sec_id = pos.get("ws_sec_id")
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

    # ── Pending Order Management (Quantman Way) ──────────────
    def _clear_pending_order(self):
        """Clear pending order state."""
        self._pending_order = None
        self._entry_signal_pending = False
        self._signal_candle = None

    def _pending_order_ready_at(self, signal_candle_time: datetime) -> datetime:
        return next_entry_ready_at(signal_candle_time, self._get_timeframe_spec().requested)

    def _pending_order_is_current_session(self, pending_order: dict, now: datetime) -> bool:
        signal_candle_time = pending_order.get("signal_candle_time")
        if not self._strategy_candle_is_current_session(signal_candle_time, now):
            return False
        ready_at = pending_order.get("ready_at")
        if isinstance(ready_at, datetime) and ready_at.date() != now.date():
            return False
        return True

    def _is_pending_order_ready(self, pending_order: dict, now: datetime) -> bool:
        if not self._pending_order_is_current_session(pending_order, now):
            return False
        ready_at = pending_order.get("ready_at")
        return ready_at is None or now >= ready_at

    async def _flush_pending_order(self, row: pd.Series, callback=None):
        """Execute the pending order with retry-once logic.
        Returns True if entry succeeded, False otherwise."""
        po = self._pending_order
        if not po:
            return False

        po["attempts"] = po.get("attempts", 0) + 1
        attempt = po["attempts"]

        self.log_event("entry", f"🚀 Firing entry (attempt {attempt}/2) at {_now_ist().strftime('%H:%M:%S.%f')[:12]}")

        await self._enter_trade(row, callback)

        if self.in_trade:
            # Success — mark candle as processed
            self._last_processed_candle_time = po.get("signal_candle_time")
            self.log_event("entry", f"✅ Entry succeeded on attempt {attempt}")
            self._clear_pending_order()
            return True
        else:
            # Entry failed
            if self._entry_retry_blocked:
                self.log_event(
                    "error",
                    "Entry retry refused — a leg could not be proven flat at the broker. "
                    "Reconcile Dhan before this strategy trades again.",
                )
                self._clear_pending_order()
                self._save_state()
                return False
            if attempt >= 2:
                self.log_event(
                    "error", f"❌ Entry FAILED after {attempt} attempts — giving up. Check debug_engine_state()."
                )
                self._clear_pending_order()
                return False
            else:
                # Schedule retry in ~4 seconds
                po["retry_at"] = _now_ist() + timedelta(seconds=4)
                self.log_event(
                    "warning",
                    f"⚠ Entry failed (attempt {attempt}). Retry scheduled at {po['retry_at'].strftime('%H:%M:%S')}",
                )
                return False

    async def _try_flush_pending_order(self, callback=None):
        """Check and execute pending order from the 1-second poll loop.
        Implements: Quantman Way timing, double-trigger guard, retry-once, stale expiry."""
        if not self._pending_order:
            return

        po = self._pending_order
        now = _now_ist()

        if now.time() >= self._market_close:
            self.log_event("info", "Pending order cleared at session end")
            self._clear_pending_order()
            return

        if not self._pending_order_is_current_session(po, now):
            self.log_event("info", "Pending order cleared — stale session signal")
            self._clear_pending_order()
            return

        # ── Stale signal expiry (older than 2 candle periods) ──
        if po.get("created_at"):
            age = (now - po["created_at"]).total_seconds()
            max_age = self._get_timeframe() * 60 * 2
            if age > max_age:
                self.log_event("warning", f"⚠ Stale pending order expired ({age:.0f}s old, max={max_age}s)")
                self._clear_pending_order()
                return

        # ── Guard: can we trade? ──
        if self.in_trade:
            # Don't silently hang — log it
            if po.get("_in_trade_warn", 0) == 0:
                self.log_event("warning", "⚠ Pending order waiting — in_trade=True (position still open)")
                po["_in_trade_warn"] = 1
            return

        max_trades = self.strategy.get("max_trades_per_day", 1)
        daily_loss_hit = self.max_daily_loss > 0 and self.daily_pnl <= -self.max_daily_loss
        if self.trades_today >= max_trades:
            self.log_event("info", f"Pending order cleared — max trades reached ({self.trades_today}/{max_trades})")
            self._clear_pending_order()
            return
        if daily_loss_hit:
            self.log_event("info", f"Pending order cleared — daily loss limit hit (PnL: ₹{self.daily_pnl:.0f})")
            self._clear_pending_order()
            return
        if self._profit_cooldown_active():
            self.log_event("info", "Pending order cleared — profit cooldown session")
            self._clear_pending_order()
            return

        # ── Double-trigger guard ──
        if self._last_processed_candle_time == po.get("signal_candle_time"):
            self.log_event("debug", "Double-trigger blocked — candle already processed")
            self._clear_pending_order()
            return

        # ── Retry timing check ──
        if po.get("retry_at") and now < po["retry_at"]:
            return  # Wait for retry window

        if not self._is_pending_order_ready(po, now):
            return

        # ── FIRE THE ORDER ──
        latest_row = po.get("row")
        if latest_row is None:
            latest_row = self.candle_buffer.iloc[-1] if not self.candle_buffer.empty else None
        if latest_row is None:
            self.log_event("error", "Cannot execute pending order — no candle data available")
            self._clear_pending_order()
            return

        await self._flush_pending_order(latest_row, callback)

    # ── Tick ──────────────────────────────────────────────────
    async def _tick(self, callback=None):
        """Single tick — fetch data, evaluate conditions, manage trades (REST fallback)."""
        now = _now_ist()
        self.current_time = now
        cur_time = now.time()

        # New day reset
        if now.date() != self.session_date:
            if self.positions:
                if self._is_intraday_product():
                    self._mark_intraday_rollover_positions()
                else:
                    self.log_event("info", f"Carry position preserved into new session ({self._product_type()})")
                    self._save_state()
            self.trades_today = 0
            self.daily_pnl = 0.0
            self.session_date = now.date()
            self.log_event("info", f"📅 New trading day: {self.session_date}")
            # PERSIST THE ROLLOVER. The date moved in memory only, so a
            # flat engine's file still read yesterday -- and the restore
            # skips a stale file that holds no position. PhilForge was
            # stopped at 15:32 on 2026-08-21 for a Dhan backfill and
            # started again at 00:33, 33 minutes past midnight: all three
            # paper engines were dropped as stale and vanished from the
            # Live page. Saving here costs one write a day.
            self._save_state()

        if self._is_closed_market_day(now.date()):
            if self._pending_order:
                self.log_event("info", "Pending order cleared on closed market day")
                self._clear_pending_order()
                self._save_state()
            if callback:
                await self._emit(callback, {"type": "status", "message": "Outside market hours"})
            return

        # Market hours check (pre-parsed in configure())
        if cur_time < self._market_open:
            if callback:
                await self._emit(callback, {"type": "status", "message": "Outside market hours"})
            return

        after_market_close = cur_time >= self._market_close

        # Fetch live candle data
        try:
            df = await self._fetch_live_data()
            if df.empty:
                return
            self.candle_buffer = df
            self.current_spot = float(df["close"].iloc[-1])
            if callback:
                await self._emit(callback, {"type": "price_update", "spot": self.current_spot, "time": str(now)})
        except Exception as e:
            self.log_event("error", f"Data fetch error: {e}")
            return

        execution_timeframe = self._get_timeframe_spec().requested
        eval_df = drop_incomplete_candle(df, execution_timeframe, now)
        if eval_df.empty:
            if callback:
                await self._emit(callback, self.get_status())
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
        prev_row = eval_df.iloc[-2] if len(eval_df) >= 2 else self._prev_row

        # ── Manage open positions ──
        for pos in list(self.positions):
            if pos.get("status") == "closed":
                continue

            # Get current option premium
            current_premium = await self._get_current_premium(pos)
            pos["current_premium"] = current_premium

            # Calculate unrealized P&L
            direction = 1 if pos["transaction_type"] == "BUY" else -1
            qty = self._position_quantity(pos)
            pos["unrealized_pnl"] = round((current_premium - pos["entry_premium"]) * direction * qty, 2)

        if self.in_trade:
            await self._reconcile_broker_positions(callback)

        if candle_in_session and self.in_trade:
            strategy_exit = self._check_strategy_exit()
            if strategy_exit:
                for pos in list(self.positions):
                    if pos.get("status") == "closed":
                        continue
                    await self._exit_position(pos, strategy_exit, pos.get("current_premium", 0.0), callback)
            else:
                for pos in list(self.positions):
                    if pos.get("status") == "closed":
                        continue
                    current_premium = pos.get("current_premium", 0.0)
                    exit_reason = pos.get("_force_exit_reason") or self._check_exit_conditions(
                        pos, latest_row, current_premium
                    )
                    if exit_reason:
                        await self._exit_position(pos, exit_reason, current_premium, callback)

        # ── Check entry conditions (REST mode — same Quantman Way logic) ──
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
            # Execute pending signal from previous tick
            if self._pending_order:
                po = self._pending_order
                # Retry timing check
                if cur_time >= self._market_close:
                    self.log_event("info", "Pending order cleared at session end")
                    self._clear_pending_order()
                elif not self._pending_order_is_current_session(po, now):
                    self.log_event("info", "Pending order cleared — stale session signal")
                    self._clear_pending_order()
                elif po.get("retry_at") and now < po["retry_at"]:
                    pass  # Wait for retry window
                elif not self._is_pending_order_ready(po, now):
                    pass
                elif self._last_processed_candle_time == po.get("signal_candle_time"):
                    self.log_event("debug", "Double-trigger blocked (REST mode)")
                    self._clear_pending_order()
                else:
                    self.log_event("entry", f"🚀 Executing pending entry at {now.strftime('%H:%M:%S')} (REST mode)")
                    await self._flush_pending_order(latest_row, callback)
            elif is_new_strategy_candle:
                entry_sig, self._condition_debug = self._evaluate_entry_conditions_with_debug(
                    latest_row,
                    prev_row,
                    now,
                )
                if entry_sig:
                    signal_candle_time = latest_row.name if hasattr(latest_row, "name") else now
                    if self._entry_can_be_scheduled(signal_candle_time):
                        ready_at = self._pending_order_ready_at(signal_candle_time)
                        self._pending_order = {
                            "signal_candle_time": signal_candle_time,
                            "created_at": now,
                            "ready_at": ready_at,
                            "row": latest_row,
                            "attempts": 0,
                            "retry_at": None,
                        }
                        self._entry_signal_pending = True
                        self._signal_candle = {
                            "Signal_Candle_Open": float(latest_row["open"]),
                            "Signal_Candle_High": float(latest_row["high"]),
                            "Signal_Candle_Low": float(latest_row["low"]),
                            "Signal_Candle_Close": float(latest_row["close"]),
                        }
                        self.log_event(
                            "signal",
                            f"⚡ ENTRY SIGNAL — will enter on or after {ready_at.strftime('%H:%M:%S')} (REST mode)",
                        )
                    else:
                        self._condition_debug = {"gate": "market_close_boundary", "conditions": []}
                        self.log_event("info", "Entry signal ignored — no next tradable candle before market close")
        elif self.in_trade:
            self._condition_debug = {"gate": "in_trade", "conditions": []}
            if self._pending_order:
                self.log_event("warning", "⚠ Pending order cleared (REST): in_trade=True")
                self._clear_pending_order()
        elif self.trades_today >= max_trades:
            self._condition_debug = {"gate": f"max_trades_reached ({self.trades_today}/{max_trades})", "conditions": []}
            if self._pending_order:
                self.log_event(
                    "warning", f"⚠ Pending order cleared (REST): trades_today({self.trades_today})>=max({max_trades})"
                )
                self._clear_pending_order()
        elif daily_loss_hit:
            self._condition_debug = {"gate": f"daily_loss_limit (PnL={self.daily_pnl:.2f})", "conditions": []}
            if self._pending_order:
                self.log_event("warning", f"⚠ Pending order cleared (REST): daily_loss_hit(PnL={self.daily_pnl:.0f})")
                self._clear_pending_order()
        elif cooldown_hit:
            self._condition_debug = {
                "gate": f"profit_cooldown (big-profit day {self.profit_cooldown_trigger_date})",
                "conditions": [],
            }
            if self._pending_order:
                self.log_event("info", "Pending order cleared (REST) — profit cooldown session")
                self._clear_pending_order()

        if is_new_strategy_candle:
            self._prev_row = latest_row
            self._last_strategy_candle_time = strategy_candle_time

        if after_market_close:
            await self._force_market_close_if_needed(now, callback)
            if callback:
                await self._emit(callback, {"type": "status", "message": "Outside market hours"})
            return

        # Send status update
        if callback:
            await self._emit(callback, self.get_status())

    # ── Data Fetch ────────────────────────────────────────────
    async def _fetch_live_data(self) -> pd.DataFrame:
        """Fetch live candle data with indicators applied."""
        tf_spec = self._get_timeframe_spec()
        execution_timeframe = tf_spec.requested

        instrument = self.strategy.get("instrument", "26000")
        df_raw = self._fetch_raw_history(instrument, tf_spec.fetch, days=7)

        indicators = self.strategy.get("indicators", [])
        df = compute_dynamic_indicators(
            df_raw,
            indicators,
            default_timeframe_minutes=execution_timeframe,
            source_timeframe_minutes=tf_spec.fetch,
            execution_timeframe_minutes=execution_timeframe,
        )
        self._latest_raw_candles = df_raw.tail(500).copy()

        # Store current candle + indicators for UI
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
                    self.current_indicators[col] = round(float(val), 2)
                except (TypeError, ValueError):
                    pass

        return df.tail(200)

    # ── Option Premium ────────────────────────────────────────
    async def _get_current_premium(self, pos: dict) -> float:
        """Fetch current premium for an option position from Dhan LTP API."""
        # Try WebSocket feed first (instant, no API call)
        if self._ws_mode and self._feed:
            ws_ltp = self._get_premium_from_feed(pos)
            if ws_ltp > 0:
                return ws_ltp

        try:
            ltp = await self.dhan.async_get_option_ltp(
                underlying=pos["underlying"],
                strike=pos["strike"],
                expiry=pos["expiry"],
                option_type=pos["option_type"],
            )
            if ltp > 0:
                return ltp
        except Exception as e:
            self.log_event(
                "error", f"LTP fetch failed for {pos['underlying']} {pos['strike']}{pos['option_type']}: {e}"
            )

        # Fallback: estimate based on spot movement
        spot_change_pct = (self.current_spot - pos["entry_spot"]) / pos["entry_spot"] if pos["entry_spot"] else 0
        delta = 0.5 if pos["option_type"] == "CE" else -0.5
        est = pos["entry_premium"] * (1 + spot_change_pct * delta * 2)
        return max(0.5, round(est, 2))

    # ── Entry ─────────────────────────────────────────────────
    async def _enter_trade(self, row: pd.Series, callback=None):
        """Enter trade: place real orders for each leg.
        Uses true-async httpx calls + parallel leg placement for minimum latency.
        """
        self._entry_submission_ambiguous = False
        self._entry_retry_blocked = False
        # The why, frozen at the instant of decision, attached to every leg below.
        entry_why = decision_why(row, self.entry_conditions, self._condition_debug, self._prev_row, "ENTRY_SIGNAL")
        self.log_event(
            "signal",
            "✅ ENTRY CONDITIONS MET",
            {"spot": self.current_spot, "time": str(self.current_time)},
        )

        legs = self.strategy.get("legs", [])
        if not legs:
            self.log_event("warning", "No legs configured — cannot enter trade")
            return

        instrument = self.strategy.get("instrument", "26000")
        underlying = ScripMaster.instrument_to_symbol(instrument)
        strike_step = get_strike_step(instrument)
        session_date_str = self.session_date.strftime("%Y-%m-%d") if self.session_date else None
        entry_spot = float(self.current_spot)
        entry_time = self.current_time or _now_ist()

        product_type = self.deploy_config.get("product_type", "INTRADAY")
        # Map common aliases to Dhan API values
        _pt_map = {"MIS": "INTRADAY", "NRML": "MARGIN"}
        product_type = _pt_map.get(product_type, product_type)
        entry_order_type = self.deploy_config.get("entry_order", "MARKET")
        place_leg_sl = self.deploy_config.get("place_leg_sl", "no") == "yes"
        sqoff_on_fail = self.deploy_config.get("sqoff_on_fail", "no") == "yes"

        # ── Phase 1: Resolve all strikes (may need premium scan) ──
        leg_plans = []  # (leg_idx, leg, strike, scanned_premium, quantity, opt_type, txn_type, lots, expiry, lot_size)
        for i, leg in enumerate(legs):
            expiry = ScripMaster.resolve_expiry(underlying, leg.get("expiry"), session_date_str)
            if not expiry:
                self.log_event("error", f"No expiry found for {underlying} leg {i + 1} — cannot place order")
                return

            lot_size = ScripMaster.get_lot_size(underlying, expiry)
            if lot_size == 0:
                lot_size = get_lot_size(instrument, self.session_date)

            strike_type = leg.get("strike_type", "atm")
            strike_value = leg.get("strike_value", 0)
            opt_type = leg.get("option_type", "CE")
            txn_type = leg.get("transaction_type", "BUY")
            lots = leg.get("lots", 1)
            # SIZE UP ON EXPIRY DAY. The same rule the backtest applies, decided
            # the same way: by asking whether the contract being bought expires
            # today, never by naming a weekday. NIFTY's weekly expiry moved from
            # Thursday to Tuesday in September 2025, and a weekday rule written
            # before that would now be sizing up on the wrong session.
            expiry_day_lots = int(leg.get("expiry_day_lots", 0) or 0)
            if expiry_day_lots > 0 and str(expiry) == str(session_date_str):
                lots = expiry_day_lots
                self.log_event("info", f"Expiry day — leg {i + 1} sized at {lots} lots instead of {leg.get('lots', 1)}")
            quantity = lots * lot_size

            scanned_premium = 0.0
            if (
                strike_type in ("premium_near", "premium_above", "premium_below")
                and expiry
                and float(strike_value or 0) > 0
            ):
                mode = strike_type.split("_")[1]
                try:
                    strike, scanned_premium = await self._find_premium_strike(
                        underlying, expiry, opt_type, float(strike_value), entry_spot, strike_step, mode=mode
                    )
                except PremiumScanUnavailable as exc:
                    # No entry beats the wrong entry. `in_trade` stays False, so
                    # the caller's attempt 2/2 asks again a moment later.
                    self.log_event("error", f"⛔ Entry abandoned — {exc}")
                    return
                self.log_event("info", f"🎯 {strike_type} target=₹{strike_value} → strike={strike}")
            else:
                strike = self._calculate_strike(leg, entry_spot, strike_step)

            leg_plans.append((i, leg, strike, scanned_premium, quantity, opt_type, txn_type, lots, expiry, lot_size))

        capital_plans = []
        for plan in leg_plans:
            i, leg, strike, scanned_premium, quantity, opt_type, txn_type, lots, expiry, lot_size = plan
            preview_premium = scanned_premium if scanned_premium > 0 else 0.0
            if preview_premium <= 0:
                try:
                    preview_premium = await self.dhan.async_get_option_ltp(underlying, strike, expiry, opt_type)
                except Exception:
                    preview_premium = 0.0
            if preview_premium <= 0:
                preview_premium = self._estimate_premium(strike, entry_spot, opt_type, strike_step)
            capital_plans.append(
                {
                    "leg_num": i + 1,
                    "transaction_type": txn_type,
                    "entry_premium": preview_premium,
                    "lots": lots,
                    "quantity": quantity,
                    "lot_size": lot_size,
                }
            )

        if not await self._can_enter_trade(capital_plans):
            return

        # ── Phase 2: Fire all leg orders in parallel (asyncio.gather) ──
        async def _place_one_leg(plan):
            i, leg, strike, scanned_premium, quantity, opt_type, txn_type, lots, expiry, lot_size = plan
            trading_symbol = f"{underlying} {strike}{opt_type} {expiry}"
            self.log_event(
                "entry", f"🦿 Leg {i + 1}: {txn_type} {lots}x {strike}{opt_type} ({entry_order_type}, {product_type})"
            )
            try:
                result = await self.dhan.async_place_option_order(
                    underlying=underlying,
                    strike_price=strike,
                    option_type=opt_type,
                    expiry=expiry,
                    transaction_type=txn_type,
                    quantity=quantity,
                    order_type=entry_order_type,
                    product_type=product_type,
                    tag=f"AF_E{i + 1}_{opt_type}_{strike}",
                )
                order_id = result.get("orderId", "")
                self.log_event("order", f"✅ Order placed: {txn_type} {trading_symbol} | OrderID: {order_id}")
                verification = await self._verify_order_execution(
                    order_id,
                    quantity,
                    stage="entry",
                    label=f"Leg {i + 1} {trading_symbol}",
                    timeout_sec=self._entry_fill_timeout_sec,
                )
                if verification.get("partial_fill"):
                    await self._emergency_flatten_partial_entry(
                        underlying=underlying,
                        strike=strike,
                        expiry=expiry,
                        option_type=opt_type,
                        transaction_type=txn_type,
                        filled_qty=int(verification.get("filled_qty") or 0),
                        product_type=product_type,
                        label=f"Leg {i + 1} {trading_symbol}",
                    )
                return (
                    bool(verification.get("passed")),
                    i,
                    leg,
                    strike,
                    scanned_premium,
                    quantity,
                    opt_type,
                    txn_type,
                    lots,
                    expiry,
                    lot_size,
                    order_id,
                    trading_symbol,
                    verification,
                )
            except AmbiguousOrderSubmission as e:
                self.log_event(
                    "error",
                    f"CRITICAL: Order state UNKNOWN for Leg {i + 1}; broker reconciliation is required before retrying: {e}",
                )
                return (
                    False,
                    i,
                    leg,
                    strike,
                    scanned_premium,
                    quantity,
                    opt_type,
                    txn_type,
                    lots,
                    expiry,
                    lot_size,
                    None,
                    trading_symbol,
                    {"status": "AMBIGUOUS", "message": str(e), "filled_qty": 0, "avg_price": 0.0, "ambiguous": True},
                )
            except Exception as e:
                self.log_event("error", f"❌ Order FAILED for Leg {i + 1}: {e}")
                return (
                    False,
                    i,
                    leg,
                    strike,
                    scanned_premium,
                    quantity,
                    opt_type,
                    txn_type,
                    lots,
                    expiry,
                    lot_size,
                    None,
                    trading_symbol,
                    {"status": "ERROR", "message": str(e), "filled_qty": 0, "avg_price": 0.0},
                )

        results = await asyncio.gather(*[_place_one_leg(p) for p in leg_plans])

        if any(bool(result[-1].get("ambiguous")) for result in results):
            self._entry_submission_ambiguous = True
            self.manual_intervention_required = True
            self.running = False
            self._clear_pending_order()
            self.log_event(
                "error",
                "CRITICAL: Entry submission was not confirmed. Engine stopped; reconcile Dhan orders and positions before restart.",
            )
            self._save_state()
            return

        unsafe = [r for r in results if not r[0] and not (r[-1] or {}).get("safe_to_retry")]
        if unsafe:
            # Not necessarily a stop -- some legs may have filled fine, and the
            # basket below still handles those. What it does mean is that this
            # entry must never be fired a second time.
            self._entry_retry_blocked = True
            self.manual_intervention_required = True
            for res in unsafe:
                v = res[-1] or {}
                self.log_event(
                    "error",
                    f"CRITICAL: Leg {res[1] + 1} ended {v.get('status')} and could not be proven flat "
                    f"({v.get('message') or 'no detail'}). This entry will NOT be retried; "
                    f"check the Dhan order book for a live order before restarting.",
                )

        # ── Phase 3: Build positions from results ──
        entered_positions = []
        any_failed = False
        for res in results:
            (
                ok,
                i,
                leg,
                strike,
                scanned_premium,
                quantity,
                opt_type,
                txn_type,
                lots,
                expiry,
                lot_size,
                order_id,
                trading_symbol,
                verification,
            ) = res
            if not ok:
                any_failed = True
                continue

            ws_sec_id = None
            if self._ws_mode and self._feed:
                ws_sec_id = self._feed.subscribe_option(underlying, strike, expiry, opt_type)
            security_id = str(ScripMaster.lookup(underlying, strike, expiry, opt_type) or "").strip()

            entry_premium = self._safe_float(verification.get("avg_price"), 0.0)
            if entry_premium <= 0 and scanned_premium > 0:
                entry_premium = scanned_premium
            if entry_premium <= 0:
                try:
                    entry_premium = await self.dhan.async_get_option_ltp(
                        underlying, strike, expiry, opt_type
                    ) or self._estimate_premium(strike, entry_spot, opt_type, strike_step)
                except Exception:
                    entry_premium = self._estimate_premium(strike, entry_spot, opt_type, strike_step)

            position = {
                "id": len(self.positions) + len(self.closed_trades) + 1,
                "leg_num": i + 1,
                "display_symbol": f"{underlying} {strike} {opt_type}",
                "underlying": underlying,
                "transaction_type": txn_type,
                "option_type": opt_type,
                "strike": strike,
                "expiry": expiry,
                "entry_time": entry_time,
                "entry_why": entry_why,
                "entry_spot": entry_spot,
                "entry_premium": entry_premium,
                "current_premium": entry_premium,
                "lots": lots,
                "lot_size": lot_size,
                "quantity": quantity,
                "sl_pct": leg.get("sl_pct", 0),
                "target_pct": leg.get("target_pct", 0),
                "sl_points": leg.get("sl_points", 0),
                "target_points": leg.get("target_points", 0),
                "sl_rupees": leg.get("sl_rupees", 0),
                "target_rupees": leg.get("target_rupees", 0),
                "trail_pct": leg.get("trail_pct", 0),
                "sqoff_time": leg.get("sqoff_time", self.strategy.get("market_close", "15:20")),
                "unrealized_pnl": 0,
                "peak_premium": entry_premium,
                "entry_order_id": order_id,
                "sl_order_id": None,
                "exit_order_id": None,
                "entry_fill_verified": True,
                "entry_fill_status": verification.get("status"),
                "entry_quote_premium": scanned_premium if scanned_premium > 0 else None,
                "trading_symbol": trading_symbol,
                "symbol": trading_symbol,
                "status": "open",
                "ws_sec_id": ws_sec_id,
                "security_id": security_id,
                "_exit_attempts": 0,
            }

            if ws_sec_id:
                self._option_sec_id = ws_sec_id
                self.log_event("info", f"⚡ Option subscribed to WebSocket: sec_id={ws_sec_id}")

            async with self._trades_lock:
                self.positions.append(position)
            entered_positions.append(position)

        # ── Phase 4: Fire SL orders in background (don't block entry) ──
        if place_leg_sl and entered_positions:

            async def _bg_sl():
                for pos in entered_positions:
                    if pos.get("sl_pct", 0) > 0:
                        await self._place_sl_order(pos)

            self._spawn_tracked(_bg_sl(), "Broker stop-loss placement")
        elif entered_positions and any(self._safe_float(p.get("sl_pct"), 0.0) > 0 for p in entered_positions):
            # Say it out loud, once, at the moment the exposure opens. The leg
            # carries a stop percentage but nothing rests at the broker to
            # enforce it, so the stop dies with this process.
            self.log_event(
                "warning",
                "Leg stop-loss is engine-side only (deploy option 'Place leg SL' is No) - "
                "no protective order rests at Dhan; if this process stops, the position is unprotected "
                "until the exchange squares off",
            )

        # Handle sqoff_on_fail
        if any_failed and sqoff_on_fail and entered_positions:
            self.log_event("warning", "Square-off triggered due to failed entry")
            for pos in entered_positions:
                await self._exit_position(pos, "ENTRY_FAIL_SQOFF", pos["entry_premium"], callback)
            return

        if entered_positions:
            self.in_trade = True
            self.trades_today += 1

            self._set_strategy_thresholds(entered_positions)

            if self.strat_sl_val > 0:
                self.log_event("info", f"🛡️ Strategy SL: ₹{self.strat_sl_val:,.0f}")
            if self.strat_tp_val > 0:
                self.log_event("info", f"🎯 Strategy TP: ₹{self.strat_tp_val:,.0f}")

            self.log_event("info", f"📊 Trade #{self.trades_today}: {len(entered_positions)} legs opened")
            self._save_state()  # Persist after trade entry
        else:
            self.log_event("error", "No legs could be entered")

    # ── SL Order Placement ────────────────────────────────────
    async def _place_sl_order(self, pos: dict):
        """Place a stop-loss order at the broker for a position (async)."""
        sl_pct = pos.get("sl_pct", 0)
        if sl_pct <= 0:
            return

        opposite_txn = "SELL" if pos["transaction_type"] == "BUY" else "BUY"

        if pos["transaction_type"] == "BUY":
            trigger = round(pos["entry_premium"] * (1 - sl_pct / 100), 2)
        else:
            trigger = round(pos["entry_premium"] * (1 + sl_pct / 100), 2)

        sl_limit_diff = float(self.deploy_config.get("sl_limit_diff_pct", 1) or 1)
        if pos["transaction_type"] == "BUY":
            limit_price = round(trigger * (1 - sl_limit_diff / 100), 2)
        else:
            limit_price = round(trigger * (1 + sl_limit_diff / 100), 2)

        product_type = self.deploy_config.get("product_type", "INTRADAY")
        # Map common aliases to Dhan API values
        _pt_map = {"MIS": "INTRADAY", "NRML": "MARGIN"}
        product_type = _pt_map.get(product_type, product_type)

        try:
            result = await self.dhan.async_place_sl_order(
                underlying=pos["underlying"],
                strike_price=pos["strike"],
                option_type=pos["option_type"],
                expiry=pos["expiry"],
                transaction_type=opposite_txn,
                quantity=pos["quantity"],
                trigger_price=trigger,
                price=limit_price,
                product_type=product_type,
                order_type="SL",
                tag=f"AF_SL_{pos['option_type']}_{pos['strike']}",
            )
            order_id = str(result.get("orderId", "") or "").strip()
            pos["sl_order_id"] = order_id
            if not order_id:
                # A 200 is not acceptance. Without an id there is nothing
                # resting at Dhan and nothing to cancel on the way out.
                pos["sl_order_status"] = "UNCONFIRMED"
                self.log_event(
                    "error",
                    f"CRITICAL: SL order for Leg {pos['leg_num']} returned no order id - "
                    f"the position is UNPROTECTED at the broker. Place the stop manually.",
                )
                self._save_state()
                return
            pos["sl_order_status"] = "PLACED"
            self.log_event(
                "order",
                f"🛡 SL order placed for Leg {pos['leg_num']}: "
                f"trigger=₹{trigger} limit=₹{limit_price} "
                f"OrderID: {order_id}",
            )
            self._save_state()
        except Exception as e:
            pos["sl_order_status"] = "FAILED"
            self.log_event(
                "error",
                f"CRITICAL: SL order FAILED for Leg {pos['leg_num']} ({e}) - "
                f"the position is UNPROTECTED at the broker. Place the stop manually.",
            )
            self._save_state()

    # ── Exit ──────────────────────────────────────────────────
    @staticmethod
    def _conditions_that_fired(why) -> list[str]:
        """The conditions that were TRUE, written the way the builder shows them.

        A group is OR'd, so more than one can be true and any one of them is a
        complete answer; an AND group only reads correctly with all of them.
        Both cases are served by listing every true condition with the two
        numbers it compared.
        """
        out = []
        for cond in (why or {}).get("conditions") or []:
            if not isinstance(cond, dict) or not cond.get("result"):
                continue
            text = str(cond.get("condition") or "").strip()
            left, right = cond.get("left_value"), cond.get("right_value")
            if text:
                out.append(f"{text} ({left} vs {right})" if left is not None else text)
        return out

    async def _record_closed_trade(self, pos: dict, reason: str, exit_premium: float, quantity: int):
        quantity = max(0, int(quantity or 0))
        if quantity <= 0:
            return None

        closed_trade = pos.copy()
        closed_trade["status"] = "closed"
        closed_trade["exit_time"] = self.current_time
        closed_trade["exit_reason"] = reason
        try:
            latest = self.candle_buffer.iloc[-1] if not self.candle_buffer.empty else None
        except Exception:
            latest = None
        decided = pos.pop("_exit_why", None)
        closed_trade.pop("_exit_why", None)
        if isinstance(decided, dict) and str(decided.get("reason") or "") == str(reason):
            closed_trade["exit_why"] = decided
        else:
            closed_trade["exit_why"] = decision_why(
                latest,
                self.exit_conditions if str(reason) in ("EXIT_SIGNAL", "TOUCH_EXIT") else [],
                None,
                self._prev_row,
                reason,
            )
        closed_trade["quantity"] = quantity
        closed_trade["lots"] = quantity / pos["lot_size"] if pos.get("lot_size") else pos.get("lots", 0)
        closed_trade["exit_premium"] = exit_premium
        closed_trade.pop("_force_exit_reason", None)

        direction = 1 if pos["transaction_type"] == "BUY" else -1
        gross = round((exit_premium - pos["entry_premium"]) * direction * quantity, 2)
        # The same statutory schedule paper now charges, so the two books can
        # be compared at all. Live crosses the spread on top of this: its exits
        # go out as MARKET orders, which is the cost paper models as exit
        # slippage rather than one it can avoid.
        charges = statutory_round_charges(
            entry_premium=pos["entry_premium"],
            exit_premium=exit_premium,
            quantity=quantity,
            lots=closed_trade.get("lots", 1),
            option_type=pos.get("option_type"),
        )
        pnl = round(gross - charges, 2)
        closed_trade["gross_pnl"] = gross
        closed_trade["charges"] = charges
        closed_trade["pnl"] = pnl
        self.daily_pnl += pnl
        self._arm_profit_cooldown()

        async with self._trades_lock:
            self.closed_trades.append(closed_trade)

        self.log_event(
            "exit",
            f"🔚 Leg {pos['leg_num']} closed ({reason}): "
            f"Entry ₹{pos['entry_premium']:.2f} → Exit ₹{exit_premium:.2f} | Qty {quantity} | P&L: ₹{pnl:,.2f}",
        )
        # AND WHICH RULE DID IT. "EXIT_SIGNAL" names the family, not the rule;
        # with seven OR'd conditions on a strategy that is no answer at all.
        fired = self._conditions_that_fired(closed_trade.get("exit_why"))
        if fired:
            self.log_event("exit", f"   ↳ {reason} fired on: " + "; ".join(fired))
        return closed_trade

    async def _handle_partial_exit_fill(self, pos: dict, reason: str, verification: dict):
        filled_qty = min(self._position_quantity(pos), int(verification.get("filled_qty") or 0))
        if filled_qty <= 0:
            return None

        exit_premium = self._safe_float(verification.get("avg_price"), pos.get("current_premium", pos["entry_premium"]))
        closed_trade = await self._record_closed_trade(pos, reason, exit_premium, filled_qty)

        remaining_qty = max(0, self._position_quantity(pos) - filled_qty)
        if remaining_qty <= 0:
            async with self._trades_lock:
                if pos in self.positions:
                    self.positions.remove(pos)
            self.in_trade = bool(self.positions)
            if not self.positions:
                self._signal_candle = None
                self.strat_sl_val = 0
                self.strat_tp_val = 0
            self._save_state()
            return {"trade": closed_trade, "remaining_qty": 0}

        pos["quantity"] = remaining_qty
        pos["lots"] = remaining_qty / pos["lot_size"] if pos.get("lot_size") else pos.get("lots", 0)
        pos["current_premium"] = exit_premium
        pos["unrealized_pnl"] = round(self._position_unrealized_pnl(pos, exit_premium), 2)
        pos["_force_exit_reason"] = reason
        pos["partial_exit_count"] = int(pos.get("partial_exit_count", 0) or 0) + 1

        self.log_event(
            "warning",
            f"Partial exit fill for Leg {pos['leg_num']}: closed {filled_qty}, remaining {remaining_qty}. Retrying remaining exposure.",
        )
        self._save_state()
        return {"trade": closed_trade, "remaining_qty": remaining_qty}

    async def _exit_position(self, pos: dict, reason: str, exit_premium: float, callback=None):
        """Exit a position: cancel SL + place exit order (async, non-blocking)."""
        retry_after = pos.get("_exit_retry_after")
        if isinstance(retry_after, datetime) and _now_ist() < retry_after:
            return {
                "status": "pending",
                "closed": False,
                "message": f"Exit retry throttled until {retry_after.strftime('%H:%M:%S')}",
            }
        if pos.get("_exit_in_flight"):
            return {
                "status": "pending",
                "closed": False,
                "message": f"Exit already in progress for {pos.get('trading_symbol', pos.get('symbol', 'position'))}",
            }

        opposite_txn = "SELL" if pos["transaction_type"] == "BUY" else "BUY"
        pos["_force_exit_reason"] = reason
        pos["_exit_in_flight"] = True

        exit_order_type = self.deploy_config.get("exit_order", "MARKET")
        product_type = self.deploy_config.get("product_type", "INTRADAY")
        # Map common aliases to Dhan API values
        _pt_map = {"MIS": "INTRADAY", "NRML": "MARGIN"}
        product_type = _pt_map.get(product_type, product_type)

        # CANCEL THE STOP FIRST, AND WAIT FOR THE BROKER TO SAY IT IS GONE.
        # These two used to be fired together with asyncio.gather, and on
        # 2026-09-08 at 10:15:00 the exit lost the race: the stop was still a
        # live SELL 130 at Dhan when the exit SELL 130 arrived, so RMS priced
        # the pair as a naked short and refused it --
        #
        #   REJECTED RMS: You have insufficient funds. Please add Rs.400396.92
        #
        # Two seconds later the identical order needed no margin at all and
        # filled, because by then the cancel had landed. The account was never
        # short of money; it was being asked to margin a short it would never
        # take. Losing that race three times in a row stops the engine with the
        # position still OPEN, which is the outcome this ordering removes.
        #
        # An unconfirmed cancel must never BLOCK the exit -- being flat matters
        # more than being tidy -- so the confirmation is on a short budget and
        # the exit goes out regardless when it runs out.
        _SL_GONE = {"CANCELLED", "TRADED", "REJECTED", "EXPIRED"}

        async def _cancel_sl_and_confirm() -> str:
            sl_id = pos.get("sl_order_id")
            if not sl_id:
                return "NONE"
            status = ""
            try:
                result = await self.dhan.async_cancel_order(sl_id)
                status = str((result or {}).get("orderStatus") or "").upper()
            except Exception as e:
                self.log_event("warning", f"SL cancel failed (may already be triggered): {e}")
            if status in _SL_GONE:
                self.log_event("order", f"🚫 SL order cancelled: {sl_id} ({status})")
                return status
            # Dhan's DELETE did not say what became of it. Ask, briefly.
            deadline = _now_ist() + timedelta(seconds=self._sl_cancel_confirm_sec)
            while _now_ist() < deadline:
                await asyncio.sleep(0.2)
                try:
                    info = await asyncio.to_thread(self.dhan.get_order_status, sl_id)
                    status = str((info or {}).get("orderStatus") or "").upper()
                except Exception:
                    status = ""
                if status in _SL_GONE:
                    self.log_event("order", f"🚫 SL order cancelled: {sl_id} ({status})")
                    return status
            self.log_event(
                "warning",
                f"SL cancel for order {sl_id} not confirmed in {self._sl_cancel_confirm_sec}s "
                f"(last status: {status or 'unknown'}) — exiting anyway",
            )
            return "UNCONFIRMED"

        async def _place_exit():
            return await self.dhan.async_place_option_order(
                underlying=pos["underlying"],
                strike_price=pos["strike"],
                option_type=pos["option_type"],
                expiry=pos["expiry"],
                transaction_type=opposite_txn,
                quantity=pos["quantity"],
                order_type=exit_order_type,
                product_type=product_type,
                tag=f"AF_X_{pos['option_type']}_{pos['strike']}",
            )

        try:
            try:
                # The stop goes first and is confirmed; only then the exit.
                sl_state = await _cancel_sl_and_confirm()
                if sl_state == "TRADED":
                    # The stop had already filled, so the broker may be flat
                    # and this SELL could open a short instead of closing a
                    # long. Nothing in this engine reconciles a triggered
                    # broker stop -- it is a net, not a rule -- so the exit
                    # still goes out exactly as it did before, and this line
                    # exists so the situation is visible in the log instead of
                    # silent. Reconciling the stop's own fill is the next fix.
                    self.log_event(
                        "error",
                        f"CHECK THE BROKER: the stop for Leg {pos['leg_num']} ({pos['trading_symbol']}) had "
                        f"already TRADED. The exit below may leave a short position — reconcile the fills.",
                    )
                result = await _place_exit()
                order_id = result.get("orderId", "")
                pos["exit_order_id"] = order_id
                pos.pop("_exit_retry_after", None)
                self.log_event(
                    "order",
                    f"✅ Exit order placed: {opposite_txn} {pos['trading_symbol']} | OrderID: {order_id}",
                )
                verification = await self._verify_order_execution(
                    order_id,
                    self._position_quantity(pos),
                    stage="exit",
                    label=f"Leg {pos['leg_num']} {pos['trading_symbol']}",
                    timeout_sec=self._exit_fill_timeout_sec,
                )
            except AmbiguousOrderSubmission as e:
                self.manual_intervention_required = True
                self.running = False
                pos["_exit_retry_after"] = _now_ist() + timedelta(hours=24)
                self._save_state()
                self.log_event(
                    "error",
                    f"CRITICAL: Exit order state UNKNOWN for Leg {pos['leg_num']}. Engine stopped; reconcile Dhan before retrying: {e}",
                )
                return {
                    "status": "error",
                    "closed": False,
                    "message": "Exit submission is unconfirmed. Reconcile the broker position before retrying.",
                }
            except Exception as e:
                pos["_exit_attempts"] = pos.get("_exit_attempts", 0) + 1
                attempt = pos["_exit_attempts"]
                self.log_event("error", f"❌ Exit order FAILED for Leg {pos['leg_num']} (attempt {attempt}/3): {e}")
                if attempt >= 3:
                    self.manual_intervention_required = True
                    pos["_exit_retry_after"] = _now_ist() + timedelta(seconds=5)
                    if reason == "MANUAL_EXIT":
                        pos.pop("_force_exit_reason", None)
                        self._save_state()
                        self.log_event(
                            "error",
                            f"CRITICAL: Manual exit failed 3 times for Leg {pos['leg_num']} "
                            f"({pos['trading_symbol']}). Manual intervention required. Engine continues running.",
                        )
                        return {
                            "status": "error",
                            "closed": False,
                            "message": "Manual exit could not be confirmed after 3 attempts. Check broker position.",
                        }
                    self.log_event(
                        "error",
                        f"CRITICAL: Exit failed 3 times for Leg {pos['leg_num']} "
                        f"({pos['trading_symbol']}). "
                        f"MANUAL INTERVENTION REQUIRED. Engine stopping.",
                    )
                    self.running = False
                    self._save_state()
                return {
                    "status": "error",
                    "closed": False,
                    "message": f"Exit order failed for {pos.get('trading_symbol', pos.get('symbol', 'position'))}",
                }  # position stays open — next cycle will retry

            if verification.get("partial_fill"):
                partial_result = await self._handle_partial_exit_fill(pos, reason, verification)
                if partial_result and callback and partial_result.get("trade"):
                    await self._emit(callback, {"type": "exit", "trade": partial_result["trade"], **self.get_status()})
                if partial_result:
                    return {
                        "status": "partial" if partial_result.get("remaining_qty", 0) > 0 else "ok",
                        "closed": partial_result.get("remaining_qty", 0) <= 0,
                        "trade": partial_result.get("trade"),
                        "remaining_qty": partial_result.get("remaining_qty", 0),
                        "message": "Exit partially filled"
                        if partial_result.get("remaining_qty", 0) > 0
                        else "Position exited",
                    }
                return {"status": "error", "closed": False, "message": "Exit fill could not be confirmed"}
            if not verification.get("passed"):
                pos["_exit_attempts"] = pos.get("_exit_attempts", 0) + 1
                attempt = pos["_exit_attempts"]
                self.log_event(
                    "error",
                    f"❌ Exit verification FAILED for Leg {pos['leg_num']} (attempt {attempt}/3): {verification.get('status')} {verification.get('message', '')}".strip(),
                )
                if attempt >= 3:
                    self.manual_intervention_required = True
                    pos["_exit_retry_after"] = _now_ist() + timedelta(seconds=5)
                    if reason == "MANUAL_EXIT":
                        pos.pop("_force_exit_reason", None)
                        self._save_state()
                        self.log_event(
                            "error",
                            f"CRITICAL: Manual exit not confirmed after {attempt} attempts for Leg {pos['leg_num']} ({pos['trading_symbol']}). Manual intervention required. Engine continues running.",
                        )
                        return {
                            "status": "error",
                            "closed": False,
                            "message": "Manual exit was placed but not confirmed. Check broker position.",
                        }
                    self.running = False
                    self.log_event(
                        "error",
                        f"CRITICAL: Exit not confirmed after {attempt} attempts for Leg {pos['leg_num']} ({pos['trading_symbol']}). Manual intervention required.",
                    )
                    self._save_state()
                return {
                    "status": "error",
                    "closed": False,
                    "message": verification.get("message") or "Exit order not confirmed",
                }

            pos["_exit_attempts"] = 0
            pos.pop("_exit_retry_after", None)
            actual_exit_premium = self._safe_float(verification.get("avg_price"), exit_premium)
            closed_trade = await self._record_closed_trade(
                pos, reason, actual_exit_premium, self._position_quantity(pos)
            )

            async with self._trades_lock:
                if pos in self.positions:
                    self.positions.remove(pos)

            # Check if all legs closed
            if not self.positions:
                self.in_trade = False
                self._signal_candle = None
                self.strat_sl_val = 0
                self.strat_tp_val = 0
                trade_pnl = sum(t["pnl"] for t in self.closed_trades if t.get("exit_time") == self.current_time)
                self.log_event(
                    "info", f"📊 All legs closed. Trade P&L: ₹{trade_pnl:,.2f} | Daily P&L: ₹{self.daily_pnl:,.2f}"
                )
            else:
                self.in_trade = True
            self._save_state()  # Persist after trade close
            if callback and closed_trade:
                await self._emit(callback, {"type": "exit", "trade": closed_trade, **self.get_status()})
            return {
                "status": "ok",
                "closed": True,
                "trade": closed_trade,
                "message": f"Position {pos.get('trading_symbol', pos.get('symbol', ''))} exited",
            }
        finally:
            pos.pop("_exit_in_flight", None)

    # ── Exit Condition Check ──────────────────────────────────
    def _check_exit_conditions(
        self,
        pos: dict,
        row: pd.Series,
        current_premium: float,
        *,
        allow_signal_exit: bool = True,
    ) -> Optional[str]:
        """Check if any exit condition is met for a position."""
        # Update peak premium for trailing SL
        if pos["transaction_type"] == "BUY":
            pos["peak_premium"] = max(pos.get("peak_premium", pos["entry_premium"]), current_premium)
        else:
            pos["peak_premium"] = min(pos.get("peak_premium", pos["entry_premium"]), current_premium)

        # Trailing stop loss
        trail_pct = pos.get("trail_pct", 0)
        if trail_pct > 0:
            peak = pos["peak_premium"]
            if pos["transaction_type"] == "BUY":
                trail_sl = peak * (1 - trail_pct / 100)
                if current_premium <= trail_sl:
                    return "TRAILING_SL"
            else:
                trail_sl = peak * (1 + trail_pct / 100)
                if current_premium >= trail_sl:
                    return "TRAILING_SL"

        # Static stop loss (engine-level — backup if broker SL not placed)
        sl_pct = pos.get("sl_pct", 0)
        if sl_pct > 0:
            if pos["transaction_type"] == "BUY":
                if current_premium <= pos["entry_premium"] * (1 - sl_pct / 100):
                    return "STOP_LOSS"
            else:
                if current_premium >= pos["entry_premium"] * (1 + sl_pct / 100):
                    return "STOP_LOSS"

        # Target profit
        target_pct = pos.get("target_pct", 0)
        if target_pct > 0:
            if pos["transaction_type"] == "BUY":
                if current_premium >= pos["entry_premium"] * (1 + target_pct / 100):
                    return "TARGET"
            else:
                if current_premium <= pos["entry_premium"] * (1 - target_pct / 100):
                    return "TARGET"

        # SL Points (absolute premium points)
        sl_points = pos.get("sl_points", 0)
        if sl_points > 0:
            ep = pos["entry_premium"]
            if pos["transaction_type"] == "BUY" and current_premium <= ep - sl_points:
                return "SL_POINTS"
            elif pos["transaction_type"] == "SELL" and current_premium >= ep + sl_points:
                return "SL_POINTS"

        # Target Points (absolute premium points)
        target_points = pos.get("target_points", 0)
        if target_points > 0:
            ep = pos["entry_premium"]
            if pos["transaction_type"] == "BUY" and current_premium >= ep + target_points:
                return "TARGET_POINTS"
            elif pos["transaction_type"] == "SELL" and current_premium <= ep - target_points:
                return "TARGET_POINTS"

        # SL ₹ Total (leg-level rupee loss)
        sl_rupees = pos.get("sl_rupees", 0)
        if sl_rupees > 0:
            qty = self._position_quantity(pos)
            direction = 1 if pos["transaction_type"] == "BUY" else -1
            cur_pnl = (current_premium - pos["entry_premium"]) * direction * qty
            if cur_pnl <= -sl_rupees:
                return "SL_RUPEES"

        # Target ₹ Total (leg-level rupee profit)
        target_rupees = pos.get("target_rupees", 0)
        if target_rupees > 0:
            qty = self._position_quantity(pos)
            direction = 1 if pos["transaction_type"] == "BUY" else -1
            cur_pnl = (current_premium - pos["entry_premium"]) * direction * qty
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
                pos["_exit_why"] = decision_why(_touch_row, self.exit_conditions, None, self._prev_row, "TOUCH_EXIT")
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
                # WHY, TAKEN HERE AND NOT LATER. The record used to be rebuilt
                # in _record_closed_trade from `candle_buffer.iloc[-1]` and
                # whatever `_prev_row` had become by then -- a different pair of
                # bars, and without the Signal Candle values injected below. On
                # 2026-09-08 that produced the worst possible answer: a live
                # trade exited on EXIT_SIGNAL whose stored reasons showed all
                # SEVEN conditions false, so neither the log nor the journal
                # could say what had fired (Phil: "why the exit signal triggers
                # and on what condition.. in the logs I am not able to get
                # that"). The row that decided is the only row that can answer.
                pos["_exit_why"] = decision_why(_exit_row, self.exit_conditions, None, self._prev_row, "EXIT_SIGNAL")
                return "EXIT_SIGNAL"

        if self._is_intraday_product():
            # Square-off time — check strategy-level combined_sqoff_time first
            sqoff = self.strategy.get("combined_sqoff_time", "15:20")
            if not sqoff:
                sqoff = pos.get("sqoff_time", "15:20")
            if isinstance(sqoff, str):
                h, m = map(int, sqoff.split(":"))
                sqoff = time(h, m)
            if self.current_time and self.current_time.time() >= sqoff:
                return "SQUARE_OFF"

        return None

    # ── Helpers ───────────────────────────────────────────────
    def _calculate_strike(self, leg: dict, spot: float, strike_step: int) -> int:
        """Calculate strike price based on leg configuration."""
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
        return int(atm)

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
        Find strike whose premium matches target:
          near  -> closest to target (either side)
          above -> cheapest strike with premium >= target
          below -> most expensive strike with premium <= target
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
        # One retry before giving up: a 429 is a moment's contention, not an
        # outage, and the entry is worth one more ask.
        live_ltps = {}  # strike -> ltp
        fetch_error = None
        if sec_id_map:
            for attempt in (1, 2):
                try:
                    prices = await self.dhan.async_get_ltp_prices(
                        list(sec_id_map.values()), exchange_segment=exchange_seg
                    )
                    live_ltps = {s: prices[sid] for s, sid in sec_id_map.items() if sid in prices}
                    if live_ltps:
                        fetch_error = None
                        break
                except Exception as e:  # noqa: BLE001 - reported below, never raised at the caller
                    fetch_error = e
                if attempt == 1:
                    self.log_event("warning", f"LTP fetch gave nothing ({fetch_error or 'empty'}), retrying once")
                    await asyncio.sleep(0.4)
            if fetch_error is not None and not live_ltps:
                self.log_event("warning", f"Batch LTP fetch failed: {fetch_error}")

        # ── 3. Build full candidates list ──────────────────────────────────
        candidates = []  # (strike, premium, source)
        for s in strikes_to_scan:
            if s in live_ltps:
                candidates.append((s, live_ltps[s], "live"))
            else:
                est = self._estimate_premium(s, spot, option_type, strike_step)
                candidates.append((s, est, "est"))

        if not candidates:
            return int(atm), 0.0

        live_count = sum(1 for _, _, src in candidates if src == "live")
        self.log_event(
            "info",
            f"🔍 premium_{mode}: {len(candidates)} strikes ({live_count} live LTPs, {len(candidates) - live_count} estimated)",
        )

        # NO REAL PRICE, NO STRIKE. `_estimate_premium` is a rough model -- on
        # 2026-09-03 it priced 23800 CE at Rs 227.35 when the market had it at
        # Rs 252.60, which put it under the Rs 250 rule and bought a different
        # contract for Rs 5,460 more. A model is fine for filling a gap in an
        # otherwise real chain; it must not be the whole basis for choosing
        # what to buy. Skipping an entry is recoverable, buying the wrong
        # contract is not.
        if live_count == 0:
            raise PremiumScanUnavailable(
                f"no live prices for any of {len(candidates)} {symbol} {option_type} strikes; "
                "refusing to pick a strike from estimates alone"
            )

        if mode == "above":
            valid = [(s, p) for s, p, _ in candidates if p >= target_prem]
            if valid:
                best = min(valid, key=lambda x: x[1])
                self.log_event("info", f"   {len(valid)} qualify ≥₹{target_prem} → selected {best[0]} (₹{best[1]:.2f})")
                return best[0], best[1]
            self.log_event("warning", f"⚠️ premium_above: no strike ≥₹{target_prem}, using closest")
            best = min([(s, p) for s, p, _ in candidates], key=lambda x: abs(x[1] - target_prem))
            return best[0], best[1]
        elif mode == "below":
            valid = [(s, p) for s, p, _ in candidates if p <= target_prem]
            if valid:
                best = max(valid, key=lambda x: x[1])
                self.log_event("info", f"   {len(valid)} qualify ≤₹{target_prem} → selected {best[0]} (₹{best[1]:.2f})")
                return best[0], best[1]
            self.log_event("warning", f"⚠️ premium_below: no strike ≤₹{target_prem}, using closest")
            best = min([(s, p) for s, p, _ in candidates], key=lambda x: abs(x[1] - target_prem))
            return best[0], best[1]
        else:  # near
            best = min([(s, p) for s, p, _ in candidates], key=lambda x: abs(x[1] - target_prem))
            self.log_event("info", f"   selected {best[0]} (₹{best[1]:.2f}, target ₹{target_prem})")
            return best[0], best[1]

    def _estimate_premium(self, strike: int, spot: float, option_type: str, strike_step: int) -> float:
        """Estimate option premium (fallback when LTP unavailable)."""
        moneyness = (spot - strike) if option_type == "CE" else (strike - spot)
        atm_prem = spot * 0.005
        if moneyness > 0:
            intrinsic = moneyness
            extrinsic = atm_prem * 0.5 * max(0, 1 - abs(moneyness) / (spot * 0.2))
            return max(1, round(intrinsic + extrinsic, 2))
        else:
            distance_pct = abs(moneyness) / spot
            return max(1, round(atm_prem * max(0.05, 1 - distance_pct * 5), 2))

    def _get_underlying_symbol(self) -> str:
        """Get underlying symbol for scrip master lookup."""
        inst = self.strategy.get("instrument", "26000")
        return UNDERLYING_MAP.get(inst, "NIFTY")

    def _get_instrument_name(self) -> str:
        names = {
            "26000": "NIFTY 50",
            "26009": "BANK NIFTY",
            "1": "SENSEX",
            "26017": "NIFTY FIN SVC",
            "26037": "NIFTY MIDCAP",
        }
        return names.get(self.strategy.get("instrument", "26000"), "Unknown")

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

    # ── Status ────────────────────────────────────────────────
    def get_status(self) -> dict:
        """Get current engine status for UI display."""
        total_pnl = sum(p.get("unrealized_pnl", 0) for p in self.positions)
        total_pnl += sum(t.get("pnl", 0) for t in self.closed_trades)

        # Serialize positions (convert datetime objects)
        positions_out = []
        for p in self.positions:
            out = {k: v for k, v in p.items()}
            if isinstance(out.get("entry_time"), datetime):
                out["entry_time"] = str(out["entry_time"])
            positions_out.append(out)

        closed_out = []
        for t in self.closed_trades:
            out = {k: v for k, v in t.items()}
            if isinstance(out.get("entry_time"), datetime):
                out["entry_time"] = str(out["entry_time"])
            if isinstance(out.get("exit_time"), datetime):
                out["exit_time"] = str(out["exit_time"])
            closed_out.append(out)

        return {
            "running": self.running,
            "run_id": self.run_id or "",
            "mode": self.mode,
            "in_trade": self.in_trade,
            "current_spot": self.current_spot,
            "current_time": str(self.current_time) if self.current_time else None,
            "trades_today": self.trades_today,
            "daily_pnl": round(self.daily_pnl, 2),
            "capital_rejections": self.capital_rejections,
            "last_capital_check": self.last_capital_check,
            "order_verification_failures": self.order_verification_failures,
            "last_order_verification": self.last_order_verification,
            "manual_intervention_required": self.manual_intervention_required,
            "broker_stop": {
                "requested": str((self.deploy_config or {}).get("place_leg_sl", "no")).lower() == "yes",
                "unprotected_legs": len(self._unprotected_legs()),
            },
            "positions": positions_out,
            "closed_trades": closed_out,
            "total_pnl": round(total_pnl, 2),
            "strategy_name": self.strategy.get("run_name", "Live Strategy"),
            "instrument": self.strategy.get("instrument", ""),
            "strategy": {
                **self.strategy,
                "entry_conditions": self.entry_conditions,
                "exit_conditions": self.exit_conditions,
                "deploy_config": self.deploy_config,
            },
            "deploy_config": self.deploy_config,
            "current_candle": self.current_candle,
            "current_indicators": self.current_indicators,
            "event_log": [
                {
                    "time": e["time"].strftime("%H:%M:%S"),
                    "type": e["type"],
                    "message": e["message"],
                }
                for e in self.event_log[-50:]
            ],
            "condition_debug": self._condition_debug,
        }
