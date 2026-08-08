"""
engine/scalp.py — Scalp Mode Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Hybrid manual/auto trading:
  • Manual entry  → click BUY/SELL → broker order placed immediately
  • Auto exit     → exits when premium target or SL is hit
  • OR auto entry → runs entry conditions, but user can also exit manually

Completely isolated from LiveEngine and PaperTradingEngine.
Does NOT touch any existing code.
"""

import asyncio
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from broker.dhan import ScripMaster, enable_marketfeed_throttle

IST = timezone(timedelta(hours=5, minutes=30))

SCALP_EXIT_GRACE_SEC = 1.0
SCALP_MONITOR_INTERVAL_SEC = 0.25
SCALP_IDLE_SLEEP_SEC = 0.5
SCALP_SUPER_SYNC_INTERVAL_SEC = 0.75
SCALP_POSITION_SYNC_INTERVAL_SEC = 1.5
SCALP_POSITION_CACHE_TTL_SEC = 1.0
SCALP_REST_LTP_REUSE_SEC = 1.0
SCALP_REST_LTP_BACKOFF_BASE_SEC = 2.0
SCALP_REST_LTP_BACKOFF_MAX_SEC = 16.0
SCALP_REST_LTP_SNAPSHOT_SEC = 5.0
SCALP_REST_LTP_LOG_COOLDOWN_SEC = 5.0


def _now_ist():
    return datetime.now(IST).replace(tzinfo=None)


def _normalize_scalp_product_type(product_type: str | None) -> str:
    normalized = str(product_type or "").strip().upper()
    if normalized in ("", "MIS", "INTRADAY"):
        return "INTRADAY"
    if normalized in ("NRML", "NORMAL", "MARGIN"):
        return "MARGIN"
    return "INTRADAY"


class ScalpTrade:
    """Represents a single open scalp position."""

    def __init__(
        self,
        trade_id: int,
        underlying: str,
        strike: int,
        option_type: str,  # CE or PE
        expiry: str,
        transaction_type: str,  # BUY or SELL
        lots: int,
        lot_size: int,
        entry_premium: float,
        product_type: str = "INTRADAY",
        # Exit rules (all optional — at least one should be set)
        target_premium: float = 0.0,  # absolute option price to exit at
        sl_premium: float = 0.0,  # absolute SL option price
        target_pct: float = 0.0,  # % gain target on entry premium
        sl_pct: float = 0.0,  # % loss SL on entry premium
        target_rupees: float = 0.0,  # fixed ₹ profit target (across all lots)
        sl_rupees: float = 0.0,  # fixed ₹ loss SL
        sqoff_time: str = "",  # retained for legacy payloads; no time-based scalp exit
        order_id: str = "",
        entry_time: Optional[datetime] = None,
        mode: str = "live",  # "live" or "paper"
        # Stop-limit entry: wait for premium to enter [limit_price, limit_max] before placing order
        entry_limit_price: float = 0.0,
        entry_limit_max: float = 0.0,
    ):
        self.trade_id = trade_id
        self.mode = mode
        self.underlying = underlying
        self.strike = strike
        self.option_type = option_type
        self.expiry = expiry
        self.transaction_type = transaction_type
        self.lots = lots
        self.lot_size = lot_size
        self.quantity = lots * lot_size
        self.product_type = _normalize_scalp_product_type(product_type)
        self.entry_premium = entry_premium
        self.current_premium = entry_premium

        # Stop-limit entry fields
        self.entry_limit_price = entry_limit_price
        self.entry_limit_max = entry_limit_max

        # Store pct values for deferred computation (pending trades have no entry price yet)
        self.target_pct = target_pct
        self.sl_pct = sl_pct

        # Compute absolute target/SL premiums if only % given
        self.target_premium = target_premium
        self.sl_premium = sl_premium
        self.target_from_pct = target_premium <= 0 and target_pct > 0
        self.sl_from_pct = sl_premium <= 0 and sl_pct > 0

        if entry_premium > 0:
            if self.target_from_pct:
                if transaction_type == "BUY":
                    self.target_premium = round(entry_premium * (1 + target_pct / 100), 2)
                else:
                    self.target_premium = round(entry_premium * (1 - target_pct / 100), 2)

            if self.sl_from_pct:
                if transaction_type == "BUY":
                    self.sl_premium = round(entry_premium * (1 - sl_pct / 100), 2)
                else:
                    self.sl_premium = round(entry_premium * (1 + sl_pct / 100), 2)

        self.target_rupees = target_rupees
        self.sl_rupees = sl_rupees
        self.sqoff_time = (sqoff_time or "").strip()
        self.order_id = order_id
        self.entry_time = entry_time or _now_ist()
        self.exit_time: Optional[datetime] = None
        self.exit_premium: float = 0.0
        self.exit_reason: str = ""
        self.exit_order_id: str = ""
        self.pnl: float = 0.0
        self.broker_order_model: str = ""
        self.super_order_id: str = ""
        self.super_order_status: str = ""
        self.super_filled_qty: int = 0
        self.super_target_status: str = ""
        self.super_sl_status: str = ""
        # Broker-side SL/TP order IDs (safety net — placed after entry fills)
        self.broker_sl_order_id: str = ""
        self.broker_tp_order_id: str = ""
        # Pending = waiting for stop-limit trigger; open = actively trading
        self.status: str = "pending" if (entry_limit_price > 0 and entry_limit_max > 0) else "open"

    def _compute_pnl(self, current_prem: float) -> float:
        mult = 1 if self.transaction_type == "BUY" else -1
        return mult * (current_prem - self.entry_premium) * self.quantity

    def check_exit(self, current_prem: float) -> Optional[str]:
        """Returns exit reason string if an exit rule is triggered, else None."""
        now = _now_ist()

        # Don't auto-exit until entry price is known (backfill pending)
        if self.entry_premium <= 0:
            return None

        # Brief debounce: avoid evaluating exit on the same instant as entry fill.
        elapsed = (now - self.entry_time).total_seconds()
        if elapsed < SCALP_EXIT_GRACE_SEC:
            return None

        pnl = self._compute_pnl(current_prem)

        # For live Super Orders, broker-native TP/SL manages premium exits.
        if not (self.mode == "live" and self.super_order_id):
            if self.transaction_type == "BUY":
                # Target: price reached or exceeded
                if self.target_premium > 0 and current_prem >= self.target_premium:
                    return "target_hit"
                # SL: price dropped to or below
                if self.sl_premium > 0 and current_prem <= self.sl_premium:
                    return "sl_hit"
            else:  # SELL
                if self.target_premium > 0 and current_prem <= self.target_premium:
                    return "target_hit"
                if self.sl_premium > 0 and current_prem >= self.sl_premium:
                    return "sl_hit"

        # ₹ targets
        if self.target_rupees > 0 and pnl >= self.target_rupees:
            return "target_rupees_hit"
        if self.sl_rupees > 0 and pnl <= -self.sl_rupees:
            return "sl_rupees_hit"

        return None

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "underlying": self.underlying,
            "symbol": f"{self.underlying} {self.strike}{self.option_type} {self.expiry}",
            "strike": self.strike,
            "option_type": self.option_type,
            "expiry": self.expiry,
            "transaction_type": self.transaction_type,
            "lots": self.lots,
            "lot_size": self.lot_size,
            "quantity": self.quantity,
            "product_type": self.product_type,
            "entry_premium": self.entry_premium,
            "current_premium": self.current_premium,
            "target_premium": self.target_premium,
            "sl_premium": self.sl_premium,
            "target_rupees": self.target_rupees,
            "sl_rupees": self.sl_rupees,
            "sqoff_time": self.sqoff_time,
            "order_id": self.order_id,
            "entry_time": str(self.entry_time),
            "exit_time": str(self.exit_time) if self.exit_time else None,
            "exit_premium": self.exit_premium,
            "exit_reason": self.exit_reason,
            "exit_order_id": self.exit_order_id,
            "pnl": round(self._compute_pnl(self.current_premium), 2) if self.status != "pending" else 0.0,
            "status": self.status,
            "mode": self.mode,
            "entry_limit_price": self.entry_limit_price,
            "entry_limit_max": self.entry_limit_max,
            "broker_order_model": self.broker_order_model,
            "super_order_id": self.super_order_id,
            "super_order_status": self.super_order_status,
            "super_filled_qty": self.super_filled_qty,
            "super_target_status": self.super_target_status,
            "super_sl_status": self.super_sl_status,
            "broker_sl_order_id": self.broker_sl_order_id,
            "broker_tp_order_id": self.broker_tp_order_id,
        }


class ScalpEngine:
    """
    Manages all active scalp trades.
    • Runs a low-latency background monitoring loop.
    • Uses _market_feed LTP cache for zero-latency price checks.
    • Falls back to REST `get_option_ltp` every 2s if no WS feed.
    """

    def __init__(self, dhan_client, market_feed=None, on_trade_close=None):
        self.dhan = dhan_client
        self.feed = market_feed  # LiveMarketFeed instance or None
        self.on_trade_close = on_trade_close  # callback(trade_dict) for persistence

        self.open_trades: Dict[int, ScalpTrade] = {}
        self.closed_trades: list = []
        self.event_log: list = []
        self._trade_counter: int = 0
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._ws_subs: Dict[int, str] = {}  # trade_id → ws_sec_id
        self._broker_sync_tasks: Dict[int, asyncio.Task] = {}
        self._ltp_cache: Dict[tuple[str, int], tuple[float, float]] = {}
        self._ltp_backoff_until_mono: float = 0.0
        self._ltp_backoff_delay_sec: float = 0.0
        self._ltp_last_rate_limit_log_mono: float = 0.0

    # ── Public API ───────────────────────────────────────────────

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._monitor_loop())

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        for task in self._broker_sync_tasks.values():
            task.cancel()
        self._broker_sync_tasks.clear()
        enable_marketfeed_throttle(False)

    def _sync_marketfeed_throttle(self):
        enable_marketfeed_throttle(bool(self.open_trades))

    async def enter_trade(
        self,
        underlying: str,
        strike: int,
        option_type: str,
        expiry: str,
        transaction_type: str,
        lots: int,
        lot_size: int,
        target_premium: float = 0.0,
        sl_premium: float = 0.0,
        target_pct: float = 0.0,
        sl_pct: float = 0.0,
        target_rupees: float = 0.0,
        sl_rupees: float = 0.0,
        sqoff_time: str = "",
        product_type: str = "INTRADAY",
        order_type: str = "MARKET",
        mode: str = "live",  # "live" or "paper"
        entry_limit_price: float = 0.0,
        entry_limit_max: float = 0.0,
    ) -> Dict[str, Any]:
        """Place a broker order (or simulate in paper mode) and register the scalp trade.
        If entry_limit_price and entry_limit_max are set, the trade goes into 'pending' state
        and waits for the premium to enter [limit_price, limit_max] before placing the order."""
        underlying = str(underlying or "").strip().upper()
        option_type = str(option_type or "").strip().upper()
        transaction_type = str(transaction_type or "").strip().upper()
        mode = str(mode or "").strip().lower()
        if option_type not in {"CE", "PE"}:
            return {"status": "error", "message": "option_type must be CE or PE"}
        if transaction_type not in {"BUY", "SELL"}:
            return {"status": "error", "message": "transaction_type must be BUY or SELL"}
        if mode not in {"paper", "live"}:
            return {"status": "error", "message": "mode must be paper or live"}
        if int(lots or 0) <= 0 or int(lot_size or 0) <= 0:
            return {"status": "error", "message": "lots and lot_size must be positive"}
        if bool(entry_limit_price) != bool(entry_limit_max):
            return {"status": "error", "message": "Stop-limit entry requires both premium boundaries"}
        quantity = lots * lot_size
        product_type = _normalize_scalp_product_type(product_type)
        if mode == "live" and (target_premium <= 0 or sl_premium <= 0):
            return {
                "status": "error",
                "message": "Live scalp now uses Dhan Super Order and requires both Target Premium and SL Premium",
            }

        # ── Stop-limit entry: create pending trade, no order yet ──
        if entry_limit_price > 0 and entry_limit_max > 0:
            # Ensure min <= max
            lo = min(entry_limit_price, entry_limit_max)
            hi = max(entry_limit_price, entry_limit_max)
            self._trade_counter += 1
            trade = ScalpTrade(
                trade_id=self._trade_counter,
                underlying=underlying,
                strike=strike,
                option_type=option_type,
                expiry=expiry,
                transaction_type=transaction_type,
                lots=lots,
                lot_size=lot_size,
                entry_premium=0.0,  # unknown until triggered
                product_type=product_type,
                target_premium=target_premium,
                sl_premium=sl_premium,
                target_pct=target_pct,
                sl_pct=sl_pct,
                target_rupees=target_rupees,
                sl_rupees=sl_rupees,
                sqoff_time=sqoff_time,
                mode=mode,
                entry_limit_price=lo,
                entry_limit_max=hi,
            )
            self.open_trades[self._trade_counter] = trade
            self._sync_marketfeed_throttle()

            # Subscribe to WS feed for LTP monitoring
            if self.feed:
                try:
                    ws_sec_id = self.feed.subscribe_option(underlying, strike, expiry, option_type)
                    if ws_sec_id:
                        self._ws_subs[self._trade_counter] = ws_sec_id
                except Exception:
                    pass

            mode_label = "[PAPER] " if mode == "paper" else ""
            self._log(
                "info",
                f"{mode_label}⏳ STOP-LIMIT PENDING: {transaction_type} {underlying} {strike}{option_type} "
                f"| trigger range ₹{lo:.2f}–₹{hi:.2f} "
                f"| product={product_type} | target=₹{target_premium or 'none'} SL=₹{sl_premium or 'none'}",
            )

            if not self._running:
                self.start()

            return {"status": "ok", "trade_id": self._trade_counter, "trade": trade.to_dict()}

        # ── Immediate (market) entry ──
        if mode == "paper":
            # Paper mode: no real order — snapshot current LTP as entry price.
            # Run LTP fetch off the event loop so it never blocks concurrent entries.
            order_id = "PAPER"
            order_status = "TRADED"
            entry_premium = 0.0
            for _attempt in range(3):
                try:
                    ltp = await asyncio.to_thread(self.dhan.get_option_ltp, underlying, strike, expiry, option_type)
                    if ltp and ltp > 0:
                        entry_premium = float(ltp)
                        break
                except Exception:
                    pass
                if _attempt < 2:
                    await asyncio.sleep(0.3)  # brief pause between retries
            if entry_premium <= 0:
                return {
                    "status": "error",
                    "message": "Paper entry was not created because no positive option premium was available.",
                }
        else:
            # Place broker-native Super Order so TP and SL live inside Dhan.
            try:
                result = self.dhan.place_super_order(
                    underlying=underlying,
                    strike_price=strike,
                    option_type=option_type,
                    expiry=expiry,
                    transaction_type=transaction_type,
                    quantity=quantity,
                    target_price=target_premium,
                    stop_loss_price=sl_premium,
                    order_type=order_type,
                    product_type=product_type,
                    tag="AF_SCALP_SO",
                )
                order_id = result.get("orderId", "")
                order_status = str(result.get("orderStatus", result.get("status", ""))).upper()
                if order_status in ("REJECTED", "CANCELLED", "FAILED"):
                    reason = result.get("remarks", result.get("message", result.get("rejectedReason", "Unknown")))
                    return {"status": "error", "message": f"Super Order rejected by broker: {reason}"}
                if not order_id:
                    return {"status": "error", "message": f"No orderId returned: {result}"}
            except Exception as e:
                return {"status": "error", "message": str(e)}

            # Use a live premium snapshot for immediate UI feedback; sync later replaces it with actual fill.
            try:
                entry_premium = self.dhan.get_option_ltp(underlying, strike, expiry, option_type) or 0.0
            except Exception:
                entry_premium = 0.0

        self._trade_counter += 1
        trade = ScalpTrade(
            trade_id=self._trade_counter,
            underlying=underlying,
            strike=strike,
            option_type=option_type,
            expiry=expiry,
            transaction_type=transaction_type,
            lots=lots,
            lot_size=lot_size,
            entry_premium=entry_premium,
            product_type=product_type,
            target_premium=target_premium,
            sl_premium=sl_premium,
            target_pct=target_pct,
            sl_pct=sl_pct,
            target_rupees=target_rupees,
            sl_rupees=sl_rupees,
            sqoff_time=sqoff_time,
            order_id=order_id,
            mode=mode,
        )
        if mode == "live":
            trade.broker_order_model = "super"
            trade.super_order_id = str(order_id)
            trade.super_order_status = order_status
        self.open_trades[self._trade_counter] = trade
        self._sync_marketfeed_throttle()

        # Subscribe to WS feed if available
        if self.feed:
            try:
                ws_sec_id = self.feed.subscribe_option(underlying, strike, expiry, option_type)
                if ws_sec_id:
                    self._ws_subs[self._trade_counter] = ws_sec_id
            except Exception:
                pass

        mode_label = "[PAPER] " if mode == "paper" else ""
        self._log(
            "entry",
            f"{mode_label}✅ SCALP ENTER: {transaction_type} {underlying} {strike}{option_type} "
            f"@ ₹{entry_premium:.2f} | product={product_type} | orderId={order_id} "
            f"| target=₹{trade.target_premium or 'none'} SL=₹{trade.sl_premium or 'none'}",
        )

        if not self._running:
            self.start()

        return {"status": "ok", "trade_id": self._trade_counter, "trade": trade.to_dict()}

    async def exit_trade(self, trade_id: int, reason: str = "manual") -> Dict[str, Any]:
        """Manually exit an open scalp trade."""
        trade = self.open_trades.get(trade_id)
        if not trade:
            return {"status": "error", "message": f"Trade {trade_id} not found or already closed"}
        await self._close_trade(trade, reason)
        return {"status": "ok", "trade": trade.to_dict()}

    async def kill_all_trades(self) -> Dict[str, Any]:
        """Emergency exit ALL open trades immediately."""
        trades_to_close = list(self.open_trades.values())
        if not trades_to_close:
            return {"status": "ok", "closed": 0, "message": "No open trades"}
        self._log("info", f"🔴 KILL ALL — closing {len(trades_to_close)} trade(s)...")
        closed = 0
        for trade in trades_to_close:
            try:
                await self._close_trade(trade, "kill")
                closed += 1
            except Exception as e:
                self._log("error", f"Kill failed for trade {trade.trade_id}: {e}")
        return {"status": "ok", "closed": closed}

    async def update_trade_targets(self, trade_id: int, **kwargs) -> Dict[str, Any]:
        """Update target/SL for an open trade, or edit pending stop-limit settings before fill."""
        trade = self.open_trades.get(trade_id)
        if not trade:
            return {"status": "error", "message": f"Trade {trade_id} not found"}
        prev_values = {
            "target_premium": trade.target_premium,
            "sl_premium": trade.sl_premium,
            "target_rupees": trade.target_rupees,
            "sl_rupees": trade.sl_rupees,
            "sqoff_time": trade.sqoff_time,
            "entry_limit_price": trade.entry_limit_price,
            "entry_limit_max": trade.entry_limit_max,
            "target_from_pct": trade.target_from_pct,
            "sl_from_pct": trade.sl_from_pct,
        }
        new_entry_min = kwargs.get("entry_limit_price")
        new_entry_max = kwargs.get("entry_limit_max")
        if trade.status != "pending" and (new_entry_min is not None or new_entry_max is not None):
            return {
                "status": "error",
                "message": "Entry trigger range can only be changed while the trade is pending",
                "trade": trade.to_dict(),
            }
        if (new_entry_min is None) ^ (new_entry_max is None):
            return {
                "status": "error",
                "message": "Provide both trigger start and trigger end to update the pending entry range",
                "trade": trade.to_dict(),
            }
        if new_entry_min is not None and new_entry_max is not None:
            if float(new_entry_min) <= 0 or float(new_entry_max) <= 0:
                return {
                    "status": "error",
                    "message": "Pending trigger range must be greater than 0",
                    "trade": trade.to_dict(),
                }
            if float(new_entry_max) < float(new_entry_min):
                return {
                    "status": "error",
                    "message": "Trigger end must be greater than or equal to trigger start",
                    "trade": trade.to_dict(),
                }
        for attr in (
            "target_premium",
            "sl_premium",
            "target_rupees",
            "sl_rupees",
            "sqoff_time",
            "entry_limit_price",
            "entry_limit_max",
        ):
            if attr in kwargs and kwargs[attr] is not None:
                setattr(trade, attr, kwargs[attr])
        if "target_premium" in kwargs and kwargs["target_premium"] is not None:
            trade.target_from_pct = False
        if "sl_premium" in kwargs and kwargs["sl_premium"] is not None:
            trade.sl_from_pct = False
        if trade.status == "pending":
            self._log("info", f"⏳ Pending trade {trade_id} updated: {kwargs}")
            return {"status": "ok", "trade": trade.to_dict()}
        if trade.mode == "live" and self._is_super_order_trade(trade):
            if trade.target_premium <= 0 or trade.sl_premium <= 0:
                for key, value in prev_values.items():
                    setattr(trade, key, value)
                return {
                    "status": "error",
                    "message": "Live Super Order requires both Target Premium and SL Premium",
                    "trade": trade.to_dict(),
                }
        if trade.mode == "live" and ("sl_premium" in kwargs or "target_premium" in kwargs):
            errors = await self._modify_broker_sl_tp(trade, **kwargs)
            if errors:
                for key, value in prev_values.items():
                    setattr(trade, key, value)
                return {"status": "error", "message": "; ".join(errors), "trade": trade.to_dict()}
            if not self._is_super_order_trade(trade):
                needs_sl = trade.sl_premium > 0 and not trade.broker_sl_order_id
                needs_tp = trade.target_premium > 0 and not trade.broker_tp_order_id
                if needs_sl or needs_tp:
                    self._schedule_broker_sync(trade_id)
        self._log("info", f"🎯 Trade {trade_id} targets updated: {kwargs}")
        return {"status": "ok", "trade": trade.to_dict()}

    def get_status(self) -> dict:
        open_trades = [t.to_dict() for t in self.open_trades.values()]
        closed_pnl = round(sum(t.get("pnl", 0) for t in self.closed_trades), 2)
        open_pnl = round(sum(t.get("pnl", 0) for t in open_trades), 2)
        session_pnl = round(closed_pnl + open_pnl, 2)
        return {
            "running": self._running,
            "open_trades": open_trades,
            "closed_trades": list(reversed(self.closed_trades[-50:])),
            "event_log": list(reversed(self.event_log[-100:])),
            "total_pnl": closed_pnl,
            "closed_pnl": closed_pnl,
            "open_pnl": open_pnl,
            "session_pnl": session_pnl,
        }

    # ── Internal monitoring ───────────────────────────────────────

    async def _monitor_loop(self):
        """Poll/WS prices with scalp-friendly latency and trigger auto-exits."""
        _last_rest_call = 0.0
        _last_ws_health_check = 0.0
        _last_super_sync = 0.0
        _last_position_sync = 0.0
        while self._running:
            # Periodic WS health check — every 30s, verify feed is alive
            now_mono = asyncio.get_event_loop().time()
            if self.feed and (now_mono - _last_ws_health_check) > 30:
                _last_ws_health_check = now_mono
                try:
                    if hasattr(self.feed, "check_health") and not self.feed.check_health():
                        self._log("info", "📡 WS feed stale — triggering reconnect")
                except Exception:
                    pass
            if now_mono - _last_super_sync > SCALP_SUPER_SYNC_INTERVAL_SEC:
                _last_super_sync = now_mono
                try:
                    await self._sync_super_orders()
                except Exception as e:
                    self._log("error", f"Super Order monitor sync failed: {e}")
            if now_mono - _last_position_sync > SCALP_POSITION_SYNC_INTERVAL_SEC:
                _last_position_sync = now_mono
                try:
                    await self._sync_broker_positions()
                except Exception as e:
                    self._log("error", f"Broker position monitor sync failed: {e}")
            try:
                trades = list(self.open_trades.items())
                if not trades:
                    await asyncio.sleep(SCALP_IDLE_SLEEP_SEC)
                    continue

                # Batch-fetch all LTPs in ONE non-blocking call
                price_map = await self._fetch_all_ltps(trades)

                for tid, trade in trades:
                    current_prem = price_map.get(tid, 0.0)
                    if current_prem <= 0:
                        continue

                    # ── Handle pending stop-limit trades ──
                    if trade.status == "pending":
                        trade.current_premium = current_prem
                        if trade.entry_limit_price <= current_prem <= trade.entry_limit_max:
                            # Premium entered the trigger range — activate!
                            await self._activate_pending_trade(trade)
                        continue  # Don't check exit for pending trades

                    # ── Handle open trades ──
                    # Backfill entry price if it was 0 at entry time
                    if trade.entry_premium == 0:
                        trade.entry_premium = current_prem
                        # Reset grace period so check_exit waits 3s from backfill
                        trade.entry_time = _now_ist()
                        if trade.target_from_pct:
                            mult = 1 if trade.transaction_type == "BUY" else -1
                            trade.target_premium = round(current_prem * (1 + mult * trade.target_pct / 100), 2)
                        if trade.sl_from_pct:
                            mult = -1 if trade.transaction_type == "BUY" else 1
                            trade.sl_premium = round(current_prem * (1 + mult * trade.sl_pct / 100), 2)
                        self._log("info", f"📌 Trade {tid} entry price backfilled @ ₹{current_prem:.2f}")
                    trade.current_premium = current_prem
                    reason = trade.check_exit(trade.current_premium)
                    if reason:
                        if tid not in self.open_trades:
                            continue  # Already closed by manual exit during LTP fetch
                        await self._close_trade(trade, reason)
            except Exception as e:
                self._log("error", f"Monitor error: {e}")
            await asyncio.sleep(SCALP_MONITOR_INTERVAL_SEC)

    async def _activate_pending_trade(self, trade: ScalpTrade):
        """Place broker order for a pending stop-limit trade when premium enters the trigger range."""
        tid = trade.trade_id
        mode_label = "[PAPER] " if trade.mode == "paper" else ""
        self._log(
            "entry",
            f"{mode_label}🎯 STOP-LIMIT TRIGGERED: {trade.transaction_type} {trade.underlying} "
            f"{trade.strike}{trade.option_type} | LTP ₹{trade.current_premium:.2f} "
            f"in range ₹{trade.entry_limit_price:.2f}–₹{trade.entry_limit_max:.2f} | product={trade.product_type}",
        )

        if trade.mode == "paper":
            order_id = "PAPER"
            order_status = "TRADED"
            entry_premium = trade.current_premium
        else:
            # Place broker-native Super Order once the local trigger range is hit.
            try:
                result = self.dhan.place_super_order(
                    underlying=trade.underlying,
                    strike_price=trade.strike,
                    option_type=trade.option_type,
                    expiry=trade.expiry,
                    transaction_type=trade.transaction_type,
                    quantity=trade.quantity,
                    target_price=trade.target_premium,
                    stop_loss_price=trade.sl_premium,
                    order_type="MARKET",
                    product_type=trade.product_type,
                    tag="AF_SCALP_SO",
                )
                order_id = result.get("orderId", "")
                order_status = str(result.get("orderStatus", result.get("status", ""))).upper()
                if order_status in ("REJECTED", "CANCELLED", "FAILED"):
                    reason = result.get("remarks", result.get("message", result.get("rejectedReason", "Unknown")))
                    self._log("error", f"❌ Stop-limit order rejected: {reason}")
                    # Remove the pending trade
                    self.open_trades.pop(tid, None)
                    self._sync_marketfeed_throttle()
                    return
                if not order_id:
                    self._log("error", f"❌ No orderId returned for stop-limit: {result}")
                    self.open_trades.pop(tid, None)
                    self._sync_marketfeed_throttle()
                    return
            except Exception as e:
                self._log("error", f"❌ Stop-limit order placement failed: {e}")
                self.open_trades.pop(tid, None)
                self._sync_marketfeed_throttle()
                return

            try:
                entry_premium = (
                    self.dhan.get_option_ltp(trade.underlying, trade.strike, trade.expiry, trade.option_type)
                    or trade.current_premium
                )
            except Exception:
                entry_premium = trade.current_premium

        # Activate the trade
        trade.status = "open"
        trade.order_id = order_id
        trade.entry_premium = entry_premium
        trade.entry_time = _now_ist()
        trade.current_premium = entry_premium
        if trade.mode == "live":
            trade.broker_order_model = "super"
            trade.super_order_id = str(order_id)
            trade.super_order_status = order_status

        # Compute pct-based targets now that we have an actual entry price
        if trade.target_from_pct:
            if trade.transaction_type == "BUY":
                trade.target_premium = round(entry_premium * (1 + trade.target_pct / 100), 2)
            else:
                trade.target_premium = round(entry_premium * (1 - trade.target_pct / 100), 2)
        if trade.sl_from_pct:
            if trade.transaction_type == "BUY":
                trade.sl_premium = round(entry_premium * (1 - trade.sl_pct / 100), 2)
            else:
                trade.sl_premium = round(entry_premium * (1 + trade.sl_pct / 100), 2)

        self._log(
            "entry",
            f"{mode_label}✅ SCALP ENTER (stop-limit): {trade.transaction_type} {trade.underlying} "
            f"{trade.strike}{trade.option_type} @ ₹{entry_premium:.2f} | orderId={order_id} "
            f"| product={trade.product_type} | target=₹{trade.target_premium or 'none'} SL=₹{trade.sl_premium or 'none'}",
        )

    @staticmethod
    def _is_super_order_trade(trade: ScalpTrade) -> bool:
        return trade.mode == "live" and bool(trade.super_order_id)

    @staticmethod
    def _super_leg_triggered(leg: Dict[str, Any]) -> bool:
        status = str(leg.get("orderStatus", "")).upper()
        if status in ("TRIGGERED", "TRADED", "CLOSED"):
            return True
        try:
            return float(leg.get("triggeredQuantity", 0) or 0) > 0
        except Exception:
            return False

    @staticmethod
    def _position_net_qty(position: Dict[str, Any]) -> int:
        for key in ("netQty", "netQuantity", "net_quantity", "quantity"):
            try:
                if key in position and position.get(key) not in (None, ""):
                    return int(float(position.get(key) or 0))
            except Exception:
                pass
        try:
            buy_qty = int(float(position.get("buyQty", 0) or 0))
            sell_qty = int(float(position.get("sellQty", 0) or 0))
            return buy_qty - sell_qty
        except Exception:
            return 0

    @staticmethod
    def _position_security_id(position: Dict[str, Any]) -> str:
        for key in ("securityId", "security_id", "securityID"):
            value = position.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    @staticmethod
    def _flatten_positions(positions: Any) -> list[Dict[str, Any]]:
        if not isinstance(positions, list):
            return [positions] if isinstance(positions, dict) else []
        flat: list[Dict[str, Any]] = []
        queue = list(positions)
        while queue:
            item = queue.pop(0)
            if isinstance(item, dict):
                flat.append(item)
            elif isinstance(item, list):
                queue[:0] = item
        return flat

    async def _sync_broker_positions(self):
        live_trades = [t for t in self.open_trades.values() if t.status == "open" and t.mode == "live"]
        if not live_trades:
            return
        try:
            positions = await asyncio.to_thread(self.dhan.get_positions_cached, SCALP_POSITION_CACHE_TTL_SEC)
        except Exception as e:
            self._log("error", f"Broker position sync failed: {e}")
            return

        position_rows = self._flatten_positions(positions)
        open_security_ids = {
            self._position_security_id(pos)
            for pos in position_rows
            if self._position_security_id(pos) and self._position_net_qty(pos) != 0
        }
        now = _now_ist()
        for trade in list(live_trades):
            if trade.trade_id not in self.open_trades:
                continue
            if (now - trade.entry_time).total_seconds() < 15:
                continue
            sec_id = ScripMaster.lookup(trade.underlying, trade.strike, trade.expiry, trade.option_type)
            if not sec_id or str(sec_id) in open_security_ids:
                continue
            exit_prem = self._get_ltp(trade, trade.trade_id) or trade.current_premium or trade.entry_premium or 0.0
            self._log(
                "info",
                f"🔄 Broker position missing — closing local scalp trade as externally exited: "
                f"{trade.underlying} {trade.strike}{trade.option_type}",
            )
            await self._close_trade(
                trade,
                "broker_manual_exit",
                skip_broker_exit=True,
                exit_prem_override=exit_prem,
                exit_order_id_override=trade.super_order_id or trade.order_id,
            )

    async def _sync_super_orders(self):
        trades = [t for t in self.open_trades.values() if t.status == "open" and self._is_super_order_trade(t)]
        if not trades:
            return
        try:
            orders = await asyncio.to_thread(self.dhan.get_super_orders)
        except Exception as e:
            self._log("error", f"Super Order sync failed: {e}")
            return

        order_map = {str(o.get("orderId", "")): o for o in orders if o.get("orderId")}
        for trade in list(trades):
            order = order_map.get(trade.super_order_id)
            if not order or trade.trade_id not in self.open_trades:
                continue

            trade.super_order_status = str(order.get("orderStatus", "")).upper()
            try:
                trade.super_filled_qty = int(order.get("filledQty", trade.super_filled_qty) or 0)
            except Exception:
                pass

            avg_entry = float(order.get("averageTradedPrice", 0) or 0)
            if avg_entry > 0:
                old_entry = trade.entry_premium
                trade.entry_premium = avg_entry
                if trade.current_premium <= 0:
                    trade.current_premium = avg_entry
                if abs(avg_entry - old_entry) >= 0.05:
                    self._log(
                        "info",
                        f"📌 Super Order fill verified: ₹{avg_entry:.2f} (was ₹{old_entry:.2f}) "
                        f"| target=₹{trade.target_premium or 'none'} SL=₹{trade.sl_premium or 'none'}",
                    )

            leg_map = {
                str(leg.get("legName", "")).upper(): leg for leg in order.get("legDetails", []) if leg.get("legName")
            }
            target_leg = leg_map.get("TARGET_LEG", {})
            sl_leg = leg_map.get("STOP_LOSS_LEG", {})
            try:
                if float(target_leg.get("price", 0) or 0) > 0:
                    trade.target_premium = float(target_leg.get("price"))
            except Exception:
                pass
            try:
                if float(sl_leg.get("price", 0) or 0) > 0:
                    trade.sl_premium = float(sl_leg.get("price"))
            except Exception:
                pass
            trade.super_target_status = str(target_leg.get("orderStatus", "")).upper()
            trade.super_sl_status = str(sl_leg.get("orderStatus", "")).upper()

            if trade.super_order_status in ("REJECTED", "CANCELLED") and trade.super_filled_qty <= 0:
                self._log(
                    "error",
                    f"❌ Super Order {trade.super_order_id} {trade.super_order_status}: "
                    f"{order.get('omsErrorDescription', 'entry not accepted')}",
                )
                self.open_trades.pop(trade.trade_id, None)
                self._ws_subs.pop(trade.trade_id, None)
                self._sync_marketfeed_throttle()
                continue

            exit_reason = ""
            exit_premium = 0.0
            if self._super_leg_triggered(target_leg):
                exit_reason = "target_hit"
                exit_premium = float(target_leg.get("price", 0) or trade.target_premium or 0)
            elif self._super_leg_triggered(sl_leg):
                exit_reason = "sl_hit"
                exit_premium = float(sl_leg.get("price", 0) or trade.sl_premium or 0)
            elif trade.super_order_status == "CLOSED":
                exit_reason = "broker_closed"
                exit_premium = float(order.get("ltp", 0) or trade.current_premium or trade.entry_premium or 0)

            if exit_reason:
                await self._close_trade(
                    trade,
                    exit_reason,
                    skip_broker_exit=True,
                    exit_prem_override=exit_premium,
                    exit_order_id_override=trade.super_order_id,
                )

    async def _cancel_super_order(self, trade: ScalpTrade):
        if not trade.super_order_id:
            return
        try:
            result = await asyncio.to_thread(self.dhan.cancel_super_order, trade.super_order_id, "ENTRY_LEG")
            broker_status = str((result or {}).get("orderStatus", "")).upper()
            if broker_status in ("TRADED", "CLOSED"):
                trade.super_order_status = broker_status
                self._log("info", f"ℹ️ Super Order already traded on broker: orderId={trade.super_order_id}")
            else:
                trade.super_order_status = "CANCELLED"
                self._log("info", f"🚫 Super Order cancelled: orderId={trade.super_order_id}")
        except Exception as e:
            self._log("error", f"Super Order cancel failed ({trade.super_order_id}): {e}")

    def _schedule_broker_sync(self, trade_id: int):
        trade = self.open_trades.get(trade_id)
        if not trade or trade.mode != "live" or not trade.order_id or trade.order_id == "PAPER":
            return
        if self._is_super_order_trade(trade):
            return
        needs_sl = trade.sl_premium > 0 and not trade.broker_sl_order_id
        needs_tp = trade.target_premium > 0 and not trade.broker_tp_order_id
        if not (needs_sl or needs_tp):
            return
        task = self._broker_sync_tasks.get(trade_id)
        if task and not task.done():
            return
        self._broker_sync_tasks[trade_id] = asyncio.create_task(self._verify_fill_and_sync_broker_orders(trade_id))

    async def _verify_fill_and_sync_broker_orders(self, trade_id: int):
        task = asyncio.current_task()
        try:
            trade = self.open_trades.get(trade_id)
            if not trade or trade.mode != "live" or not trade.order_id:
                return

            fill = await asyncio.to_thread(self.dhan.verify_order_fill, trade.order_id, 20, 1.0)
            trade = self.open_trades.get(trade_id)
            if not trade or trade.status != "open":
                return

            status = str(fill.get("status", "")).upper()
            if status == "FILLED":
                actual = float(fill.get("avg_price") or 0.0)
                if actual > 0:
                    old_entry = trade.entry_premium
                    trade.entry_premium = actual
                    if trade.current_premium <= 0:
                        trade.current_premium = actual
                    if trade.target_from_pct and trade.target_pct > 0:
                        mult = 1 if trade.transaction_type == "BUY" else -1
                        trade.target_premium = round(actual * (1 + mult * trade.target_pct / 100), 2)
                    if trade.sl_from_pct and trade.sl_pct > 0:
                        mult = -1 if trade.transaction_type == "BUY" else 1
                        trade.sl_premium = round(actual * (1 + mult * trade.sl_pct / 100), 2)
                    if abs(actual - old_entry) >= 0.05:
                        self._log(
                            "info",
                            f"📌 Entry fill verified: ₹{actual:.2f} (was ₹{old_entry:.2f}) "
                            f"| target=₹{trade.target_premium or 'none'} SL=₹{trade.sl_premium or 'none'}",
                        )
            elif status in ("REJECTED", "CANCELLED"):
                message = fill.get("message", f"Order {status}")
                self.open_trades.pop(trade_id, None)
                self._ws_subs.pop(trade_id, None)
                self._sync_marketfeed_throttle()
                self._log("error", f"❌ Entry order {trade.order_id} {status}: {message}")
                return
            else:
                fallback_entry = float(trade.entry_premium or 0.0)
                if fallback_entry <= 0:
                    try:
                        fallback_entry = float(
                            await asyncio.to_thread(
                                self.dhan.get_option_ltp,
                                trade.underlying,
                                trade.strike,
                                trade.expiry,
                                trade.option_type,
                            )
                            or 0.0
                        )
                    except Exception:
                        fallback_entry = 0.0
                if fallback_entry > 0 and trade.entry_premium <= 0:
                    trade.entry_premium = fallback_entry
                    trade.current_premium = fallback_entry
                    if trade.target_from_pct and trade.target_pct > 0:
                        mult = 1 if trade.transaction_type == "BUY" else -1
                        trade.target_premium = round(fallback_entry * (1 + mult * trade.target_pct / 100), 2)
                    if trade.sl_from_pct and trade.sl_pct > 0:
                        mult = -1 if trade.transaction_type == "BUY" else 1
                        trade.sl_premium = round(fallback_entry * (1 + mult * trade.sl_pct / 100), 2)
                self._log(
                    "warning",
                    f"⚠ Entry fill not fully confirmed for trade {trade_id}: {fill.get('message', 'timeout')} "
                    f"— placing missing broker protection with latest known premium",
                )

            await self._place_broker_sl_tp(trade)
        except Exception as e:
            self._log("error", f"Broker protection sync failed for trade {trade_id}: {e}")
        finally:
            current = self._broker_sync_tasks.get(trade_id)
            if current is task:
                self._broker_sync_tasks.pop(trade_id, None)

    def _broker_order_error(self, result: Dict[str, Any]) -> str:
        status = str(result.get("orderStatus", result.get("status", ""))).upper()
        if status in ("REJECTED", "FAILED", "CANCELLED"):
            return str(result.get("remarks", result.get("message", result.get("rejectedReason", status))))
        if not result.get("orderId"):
            return str(result.get("message", result))
        return ""

    async def _place_broker_sl_tp(self, trade: ScalpTrade, place_sl: bool = True, place_tp: bool = True):
        """Place SL and/or TP orders on the broker as a safety net (live mode only).
        These protect the position even if the server goes down."""
        if trade.mode != "live" or not trade.entry_premium:
            return
        exit_txn = "SELL" if trade.transaction_type == "BUY" else "BUY"

        # ── SL order (Stop-Loss Limit on broker) ──
        if place_sl and trade.sl_premium > 0 and not trade.broker_sl_order_id:
            try:
                if exit_txn == "SELL":
                    sl_price = round(max(0.05, trade.sl_premium * 0.95), 2)
                else:
                    sl_price = round(trade.sl_premium * 1.05, 2)
                result = await asyncio.to_thread(
                    self.dhan.place_option_order,
                    underlying=trade.underlying,
                    strike_price=trade.strike,
                    option_type=trade.option_type,
                    expiry=trade.expiry,
                    transaction_type=exit_txn,
                    quantity=trade.quantity,
                    order_type="SL",
                    product_type=trade.product_type,
                    price=sl_price,
                    trigger_price=trade.sl_premium,
                    tag="AF_SC_SL",
                )
                err = self._broker_order_error(result)
                oid = result.get("orderId", "")
                if oid and not err:
                    trade.broker_sl_order_id = str(oid)
                    self._log("info", f"🛡️ Broker SL placed: {exit_txn} trigger=₹{trade.sl_premium} orderId={oid}")
                else:
                    self._log("error", f"Broker SL placement failed: {err or result}")
            except Exception as e:
                self._log("error", f"Broker SL placement failed: {e}")

        # ── TP order (Limit order on broker) ──
        if place_tp and trade.target_premium > 0 and not trade.broker_tp_order_id:
            try:
                result = await asyncio.to_thread(
                    self.dhan.place_option_order,
                    underlying=trade.underlying,
                    strike_price=trade.strike,
                    option_type=trade.option_type,
                    expiry=trade.expiry,
                    transaction_type=exit_txn,
                    quantity=trade.quantity,
                    order_type="LIMIT",
                    product_type=trade.product_type,
                    price=trade.target_premium,
                    tag="AF_SC_TP",
                )
                err = self._broker_order_error(result)
                oid = result.get("orderId", "")
                if oid and not err:
                    trade.broker_tp_order_id = str(oid)
                    self._log("info", f"🎯 Broker TP placed: {exit_txn} limit=₹{trade.target_premium} orderId={oid}")
                else:
                    self._log("error", f"Broker TP placement failed: {err or result}")
            except Exception as e:
                self._log("error", f"Broker TP placement failed: {e}")

    async def _cancel_broker_orders(self, trade: ScalpTrade):
        """Cancel pending broker-side SL/TP orders (called before software-triggered exit)."""

        async def _cancel_one(label: str, oid_attr: str, oid: str):
            try:
                await asyncio.to_thread(self.dhan.cancel_order, oid)
                self._log("info", f"🚫 Broker {label} cancelled: orderId={oid}")
            except Exception as e:
                self._log("error", f"Broker {label} cancel failed ({oid}): {e}")
            finally:
                setattr(trade, oid_attr, "")

        tasks = []
        for label, oid_attr in [("SL", "broker_sl_order_id"), ("TP", "broker_tp_order_id")]:
            oid = getattr(trade, oid_attr, "")
            if oid:
                tasks.append(asyncio.create_task(_cancel_one(label, oid_attr, oid)))
        if tasks:
            await asyncio.gather(*tasks)

    async def _modify_broker_sl_tp(self, trade: ScalpTrade, **kwargs):
        """Modify broker-side SL/TP orders when user updates targets (live mode only)."""
        errors = []
        if trade.mode != "live":
            return errors
        new_sl = kwargs.get("sl_premium")
        new_tp = kwargs.get("target_premium")
        exit_txn = "SELL" if trade.transaction_type == "BUY" else "BUY"

        if self._is_super_order_trade(trade):
            entry_pending = trade.super_filled_qty <= 0 and trade.super_order_status in (
                "",
                "TRANSIT",
                "PENDING",
                "PART_TRADED",
            )
            if entry_pending:
                try:
                    resp = await asyncio.to_thread(
                        self.dhan.modify_super_order,
                        trade.super_order_id,
                        "ENTRY_LEG",
                        order_type="MARKET",
                        quantity=trade.quantity,
                        price=0.0,
                        target_price=trade.target_premium,
                        stop_loss_price=trade.sl_premium,
                    )
                    trade.super_order_status = str(resp.get("orderStatus", trade.super_order_status)).upper()
                    self._log(
                        "info",
                        f"🛡️ Super Order updated before fill: TP=₹{trade.target_premium} SL=₹{trade.sl_premium} "
                        f"orderId={trade.super_order_id}",
                    )
                except Exception as e:
                    errors.append(f"Super Order modify failed: {e}")
                    self._log("error", errors[-1])
                return errors

            if new_tp is not None:
                try:
                    resp = await asyncio.to_thread(
                        self.dhan.modify_super_order,
                        trade.super_order_id,
                        "TARGET_LEG",
                        target_price=new_tp,
                    )
                    trade.super_order_status = str(resp.get("orderStatus", trade.super_order_status)).upper()
                    self._log("info", f"🎯 Super Order TP modified: limit=₹{new_tp} orderId={trade.super_order_id}")
                except Exception as e:
                    errors.append(f"Super Order TP modify failed: {e}")
                    self._log("error", errors[-1])

            if new_sl is not None:
                try:
                    resp = await asyncio.to_thread(
                        self.dhan.modify_super_order,
                        trade.super_order_id,
                        "STOP_LOSS_LEG",
                        stop_loss_price=new_sl,
                    )
                    trade.super_order_status = str(resp.get("orderStatus", trade.super_order_status)).upper()
                    self._log("info", f"🛡️ Super Order SL modified: trigger=₹{new_sl} orderId={trade.super_order_id}")
                except Exception as e:
                    errors.append(f"Super Order SL modify failed: {e}")
                    self._log("error", errors[-1])
            return errors

        if new_sl is not None and trade.broker_sl_order_id:
            try:
                if exit_txn == "SELL":
                    sl_price = round(max(0.05, new_sl * 0.95), 2)
                else:
                    sl_price = round(new_sl * 1.05, 2)
                await asyncio.to_thread(
                    self.dhan.modify_order,
                    order_id=trade.broker_sl_order_id,
                    price=sl_price,
                    trigger_price=new_sl,
                )
                self._log("info", f"🛡️ Broker SL modified: trigger=₹{new_sl} orderId={trade.broker_sl_order_id}")
            except Exception as e:
                errors.append(f"Broker SL modify failed: {e}")
                self._log("error", errors[-1])
        elif new_sl is not None and new_sl > 0 and not trade.broker_sl_order_id:
            await self._place_broker_sl_tp(trade, place_sl=True, place_tp=False)

        if new_tp is not None and trade.broker_tp_order_id:
            try:
                await asyncio.to_thread(
                    self.dhan.modify_order,
                    order_id=trade.broker_tp_order_id,
                    price=new_tp,
                )
                self._log("info", f"🎯 Broker TP modified: limit=₹{new_tp} orderId={trade.broker_tp_order_id}")
            except Exception as e:
                errors.append(f"Broker TP modify failed: {e}")
                self._log("error", errors[-1])
        elif new_tp is not None and new_tp > 0 and not trade.broker_tp_order_id:
            await self._place_broker_sl_tp(trade, place_sl=False, place_tp=True)
        return errors

    @staticmethod
    def _segment_for_underlying(underlying: str) -> str:
        return "BSE_FNO" if underlying == "SENSEX" else "NSE_FNO"

    @staticmethod
    def _is_marketfeed_rate_limited(error: Exception) -> bool:
        text = str(error).lower()
        return (
            "429" in text
            or "too many requests" in text
            or "rate limit" in text
            or "dh-904" in text
            or "being blocked" in text
        )

    def _ltp_cache_key(self, segment: str, security_id: int) -> tuple[str, int]:
        return (segment, int(security_id))

    def _remember_ltp(self, segment: str, security_id: int, ltp: float, *, now_mono: float | None = None) -> None:
        if ltp <= 0:
            return
        if now_mono is None:
            now_mono = _time.monotonic()
        self._ltp_cache[self._ltp_cache_key(segment, security_id)] = (float(ltp), now_mono)

    def _cached_ltp(
        self, segment: str, security_id: int, max_age_sec: float, *, now_mono: float | None = None
    ) -> float:
        if now_mono is None:
            now_mono = _time.monotonic()
        entry = self._ltp_cache.get(self._ltp_cache_key(segment, security_id))
        if not entry:
            return 0.0
        ltp, seen_mono = entry
        if now_mono - seen_mono <= max_age_sec:
            return float(ltp)
        return 0.0

    def _ltp_backoff_active(self, *, now_mono: float | None = None) -> bool:
        if now_mono is None:
            now_mono = _time.monotonic()
        return now_mono < self._ltp_backoff_until_mono

    def _clear_ltp_backoff(self) -> None:
        self._ltp_backoff_until_mono = 0.0
        self._ltp_backoff_delay_sec = 0.0

    def _enter_ltp_backoff(self, error: Exception, *, now_mono: float | None = None) -> None:
        if now_mono is None:
            now_mono = _time.monotonic()
        delay = self._ltp_backoff_delay_sec * 2 if self._ltp_backoff_delay_sec else SCALP_REST_LTP_BACKOFF_BASE_SEC
        delay = min(SCALP_REST_LTP_BACKOFF_MAX_SEC, max(SCALP_REST_LTP_REUSE_SEC, delay))
        self._ltp_backoff_delay_sec = delay
        self._ltp_backoff_until_mono = max(self._ltp_backoff_until_mono, now_mono + delay)
        if now_mono - self._ltp_last_rate_limit_log_mono >= SCALP_REST_LTP_LOG_COOLDOWN_SEC:
            self._ltp_last_rate_limit_log_mono = now_mono
            remaining = max(0.0, self._ltp_backoff_until_mono - now_mono)
            self._log(
                "warn",
                f"Batch LTP rate-limited by Dhan; pausing REST fallback for {remaining:.1f}s and waiting for WS/cache. Last error: {error}",
            )

    async def _fetch_all_ltps(self, trades: list) -> dict:
        """Fetch LTPs for all open trades in a single batched API call.
        Returns {trade_id: ltp_float}.
        WS cache is checked first; remaining trades are batched into one REST call.
        The REST call runs in a thread pool so it never blocks the event loop."""
        result = {}
        nse_ids: Dict[int, int] = {}  # trade_id -> security_id
        bse_ids: Dict[int, int] = {}  # trade_id -> security_id
        now_mono = _time.monotonic()

        for tid, trade in trades:
            segment = self._segment_for_underlying(trade.underlying)
            # Try WS cache first
            ws_sec_id = self._ws_subs.get(tid)
            if ws_sec_id and self.feed:
                try:
                    ltp = self.feed.get_ltp(ws_sec_id)
                    if ltp and ltp > 0:
                        result[tid] = float(ltp)
                        try:
                            self._remember_ltp(segment, int(ws_sec_id), float(ltp), now_mono=now_mono)
                        except Exception:
                            pass
                        continue
                except Exception:
                    pass
                # WS cache returned 0 — try re-subscribing (silent recovery)
                try:
                    new_id = self.feed.subscribe_option(trade.underlying, trade.strike, trade.expiry, trade.option_type)
                    if new_id:
                        self._ws_subs[tid] = new_id
                except Exception:
                    pass
            elif self.feed and tid not in self._ws_subs:
                # Trade has no WS subscription yet — subscribe now
                try:
                    ws_sec_id = self.feed.subscribe_option(
                        trade.underlying, trade.strike, trade.expiry, trade.option_type
                    )
                    if ws_sec_id:
                        self._ws_subs[tid] = ws_sec_id
                except Exception:
                    pass
            # Queue for batch REST fetch (fallback)
            sec_id = ScripMaster.lookup(trade.underlying, trade.strike, trade.expiry, trade.option_type)
            if sec_id:
                cached_ltp = self._cached_ltp(segment, int(sec_id), SCALP_REST_LTP_REUSE_SEC, now_mono=now_mono)
                if cached_ltp > 0:
                    result[tid] = cached_ltp
                    continue
                if trade.underlying == "SENSEX":
                    bse_ids[tid] = int(sec_id)
                else:
                    nse_ids[tid] = int(sec_id)

        if self._ltp_backoff_active(now_mono=now_mono):
            return result

        if nse_ids or bse_ids:
            segments: Dict[str, list] = {}
            if nse_ids:
                segments["NSE_FNO"] = list(set(nse_ids.values()))
            if bse_ids:
                segments["BSE_FNO"] = list(set(bse_ids.values()))
            try:
                data = await asyncio.to_thread(self.dhan.get_ltp_multi, segments)

                def _extract(seg_data: dict, sec_id: int) -> float:
                    for key in (str(sec_id), int(sec_id)):
                        v = seg_data.get(key, {})
                        if isinstance(v, dict):
                            return float(v.get("last_price", v.get("ltp", 0)))
                        if isinstance(v, (int, float)):
                            return float(v)
                    return 0.0

                for tid, sec_id in nse_ids.items():
                    ltp = _extract(data.get("NSE_FNO", {}), sec_id)
                    if ltp > 0:
                        self._remember_ltp("NSE_FNO", sec_id, ltp, now_mono=now_mono)
                        result[tid] = ltp
                for tid, sec_id in bse_ids.items():
                    ltp = _extract(data.get("BSE_FNO", {}), sec_id)
                    if ltp > 0:
                        self._remember_ltp("BSE_FNO", sec_id, ltp, now_mono=now_mono)
                        result[tid] = ltp
                self._clear_ltp_backoff()
            except Exception as e:
                if self._is_marketfeed_rate_limited(e):
                    self._enter_ltp_backoff(e, now_mono=now_mono)
                else:
                    self._log("error", f"Batch LTP fetch failed: {e}")

        return result

    def _get_ltp(self, trade: ScalpTrade, trade_id: int) -> float:
        """Synchronous LTP helper — used only for exit price snapshots."""
        segment = self._segment_for_underlying(trade.underlying)
        ws_sec_id = self._ws_subs.get(trade_id)
        if ws_sec_id and self.feed:
            try:
                ltp = self.feed.get_ltp(ws_sec_id)
                if ltp and ltp > 0:
                    try:
                        self._remember_ltp(segment, int(ws_sec_id), float(ltp))
                    except Exception:
                        pass
                    return float(ltp)
            except Exception:
                pass
        sec_id = ScripMaster.lookup(trade.underlying, trade.strike, trade.expiry, trade.option_type)
        if sec_id:
            cached_ltp = self._cached_ltp(segment, int(sec_id), SCALP_REST_LTP_SNAPSHOT_SEC)
            if cached_ltp > 0:
                return cached_ltp
        if self._ltp_backoff_active():
            return 0.0
        try:
            ltp = self.dhan.get_option_ltp(trade.underlying, trade.strike, trade.expiry, trade.option_type)
            if sec_id and ltp and ltp > 0:
                self._remember_ltp(segment, int(sec_id), float(ltp))
            return ltp
        except Exception as e:
            if self._is_marketfeed_rate_limited(e):
                self._enter_ltp_backoff(e)
            return 0.0

    async def _close_trade(
        self,
        trade: ScalpTrade,
        reason: str,
        *,
        skip_broker_exit: bool = False,
        exit_prem_override: float = 0.0,
        exit_order_id_override: str = "",
    ):
        """Place exit order (or simulate in paper mode) and move trade to closed_trades."""
        # Guard against double-close (race between manual exit and auto-exit monitor)
        if trade.trade_id not in self.open_trades or trade.status == "closed":
            self._log("info", f"⚠️ Trade {trade.trade_id} already closed, skipping duplicate exit")
            return

        sync_task = self._broker_sync_tasks.pop(trade.trade_id, None)
        if sync_task and not sync_task.done():
            sync_task.cancel()

        # Cancel pending stop-limit trade — no position to exit
        if trade.status == "pending":
            trade.status = "closed"
            trade.exit_time = _now_ist()
            trade.exit_reason = "cancelled"
            self.open_trades.pop(trade.trade_id, None)
            self._ws_subs.pop(trade.trade_id, None)
            self._sync_marketfeed_throttle()
            self._log("info", f"🚫 STOP-LIMIT CANCELLED: {trade.underlying} {trade.strike}{trade.option_type}")
            return

        exit_txn = "SELL" if trade.transaction_type == "BUY" else "BUY"
        exit_order_id = exit_order_id_override
        if skip_broker_exit:
            exit_prem = exit_prem_override or self._get_ltp(trade, trade.trade_id) or trade.current_premium
        elif trade.mode == "paper":
            exit_order_id = "PAPER"
            exit_prem = exit_prem_override or self._get_ltp(trade, trade.trade_id) or trade.current_premium
        elif self._is_super_order_trade(trade) and trade.super_filled_qty <= 0:
            try:
                await self._sync_super_orders()
            except Exception:
                pass
            if trade.trade_id not in self.open_trades or trade.status == "closed":
                return
            if trade.super_filled_qty <= 0:
                await self._cancel_super_order(trade)
                exit_order_id = trade.super_order_id
                exit_prem = trade.entry_premium or trade.current_premium or 0.0
                if reason in ("manual", "kill"):
                    reason = "cancelled"
            else:
                await self._cancel_super_order(trade)
                try:
                    ltp = self._get_ltp(trade, trade.trade_id) or trade.current_premium
                    if exit_txn == "SELL":
                        exit_price = round(max(0.05, ltp * 0.95), 2)
                    else:
                        exit_price = round(ltp * 1.05, 2)
                    self._log("info", f"Exit {exit_txn} LIMIT @ ₹{exit_price} (LTP=₹{ltp})")
                    result = self.dhan.place_option_order(
                        underlying=trade.underlying,
                        strike_price=trade.strike,
                        option_type=trade.option_type,
                        expiry=trade.expiry,
                        transaction_type=exit_txn,
                        quantity=trade.quantity,
                        order_type="LIMIT",
                        product_type=trade.product_type,
                        price=exit_price,
                        tag=f"AF_SCALP_EXIT_{reason.upper()[:8]}",
                    )
                    exit_order_id = result.get("orderId", "")
                except Exception as e:
                    self._log("error", f"Exit order failed for trade {trade.trade_id}: {e}")
                exit_prem = exit_prem_override or self._get_ltp(trade, trade.trade_id) or trade.current_premium
        else:
            if self._is_super_order_trade(trade):
                await self._cancel_super_order(trade)
            elif trade.broker_sl_order_id or trade.broker_tp_order_id:
                await self._cancel_broker_orders(trade)
            try:
                # Use LIMIT order with aggressive fill price — Dhan converts
                # F&O MARKET orders to LIMIT with a bad price buffer for SELLs,
                # causing exit orders to hang as pending instead of filling.
                ltp = self._get_ltp(trade, trade.trade_id) or trade.current_premium
                if exit_txn == "SELL":
                    # Sell at 5% below LTP to guarantee immediate fill
                    exit_price = round(max(0.05, ltp * 0.95), 2)
                else:
                    # Buy at 5% above LTP to guarantee immediate fill
                    exit_price = round(ltp * 1.05, 2)
                self._log("info", f"Exit {exit_txn} LIMIT @ ₹{exit_price} (LTP=₹{ltp})")
                result = self.dhan.place_option_order(
                    underlying=trade.underlying,
                    strike_price=trade.strike,
                    option_type=trade.option_type,
                    expiry=trade.expiry,
                    transaction_type=exit_txn,
                    quantity=trade.quantity,
                    order_type="LIMIT",
                    product_type=trade.product_type,
                    price=exit_price,
                    tag=f"AF_SCALP_EXIT_{reason.upper()[:8]}",
                )
                exit_order_id = result.get("orderId", "")
            except Exception as e:
                self._log("error", f"Exit order failed for trade {trade.trade_id}: {e}")
            exit_prem = exit_prem_override or self._get_ltp(trade, trade.trade_id) or trade.current_premium
        pnl = trade._compute_pnl(exit_prem)

        trade.exit_time = _now_ist()
        trade.exit_premium = exit_prem
        trade.exit_reason = reason
        trade.exit_order_id = exit_order_id
        trade.pnl = round(pnl, 2)
        trade.status = "closed"
        trade.current_premium = exit_prem

        trade_dict = trade.to_dict()
        self.closed_trades.append(trade_dict)
        self.open_trades.pop(trade.trade_id, None)
        self._ws_subs.pop(trade.trade_id, None)
        self._sync_marketfeed_throttle()

        # Persist via callback (auto-exits + manual exits all go through here)
        if self.on_trade_close:
            try:
                self.on_trade_close(trade_dict)
            except Exception as e:
                self._log("error", f"on_trade_close callback failed: {e}")

        pnl_sign = "+" if pnl >= 0 else ""
        self._log(
            "exit" if pnl >= 0 else "stop",
            f"{'✅' if pnl >= 0 else '🛑'} SCALP EXIT [{reason}]: "
            f"{trade.underlying} {trade.strike}{trade.option_type} "
            f"entry=₹{trade.entry_premium:.2f} exit=₹{exit_prem:.2f} "
            f"P&L={pnl_sign}₹{pnl:.2f}",
        )

    def _log(self, evt_type: str, message: str):
        entry = {
            "time": _now_ist().strftime("%H:%M:%S"),
            "type": evt_type,
            "message": message,
        }
        self.event_log.append(entry)
        print(f"[SCALP][{evt_type.upper()}] {message}")
