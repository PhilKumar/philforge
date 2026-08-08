"""
app.py — PhilForge FastAPI Backend
Fixed:
  - Bug 4: yfinance MultiIndex columns flattened correctly
  - Bug 5: live engine uses asyncio.create_task (not background_tasks)
  - Added /logo.jpg route for the frontend
"""

import asyncio
import base64
import hashlib
import inspect
import io
import json
import re
import shutil
from copy import deepcopy
from html import escape as _escape_html
from urllib.parse import urlparse as _urlparse

try:
    import orjson as _orjson
except ImportError:
    _orjson = None
import logging
import os
import secrets
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import Any, Dict, List, Mapping, Optional
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
_logger = logging.getLogger(__name__)

try:
    from prometheus_fastapi_instrumentator import Instrumentator as _PFI

    _PROMETHEUS_ENABLED = True
except ImportError:
    _PFI = None
    _PROMETHEUS_ENABLED = False

import pandas as pd
import requests

# ── Guaranteed path fix ───────────────────────────────────────────
# inspect.getfile() works even when uvicorn reload corrupts __file__
_HERE = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
os.chdir(_HERE)
# ─────────────────────────────────────────────────────────────────

import fcntl
from types import SimpleNamespace

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

import auth as _auth_mod
import config
import db as _db_mod
from broker.dhan import DhanClient, DhanOrderError, ScripMaster
from engine.backtest import DEFAULT_ENTRY_CONDITIONS, DEFAULT_EXIT_CONDITIONS, get_strike_step, run_backtest
from engine.candle_ladder import LADDER_TIMEFRAMES, TIMEFRAME_MINUTES
from engine.candle_recovery import RecoveryConfig
from engine.candle_recovery_host import MODES as RECOVERY_MODES
from engine.candle_recovery_host import CandleRecoveryHost
from engine.cascade_calendar import ContractCalendar
from engine.cascade_equity import (
    CashCascadeInstrument,
    CashCascadePaperConfig,
    CashCascadePaperEngine,
    cash_cascade_reference_symbol,
)
from engine.cascade_fib_geometry import boundaries_for_timeframe
from engine.cascade_options import (
    CascadeConfig,
    CascadeOptionsAdapter,
    FixedCampaignOption,
    IndexCandle,
    LadderCandleEntryPaper,
    NiftyContractResolver,
    NiftyOptionsPaperCascade,
    OneHourCascade,
    PaperCascadeConfig,
)
from engine.cascade_scanner import HIGH_LOOKBACK as CASCADE_SCAN_HIGH_LOOKBACK
from engine.cascade_scanner import ScanInput
from engine.cascade_scanner import scan as cascade_scan
from engine.fib_space_cascade import SpaceCascadeConfig
from engine.fib_space_host import DEFAULT_POLL_SECONDS as FIB_SPACE_POLL_SECONDS
from engine.fib_space_host import LIVE_SYMBOLS as FIB_SPACE_SYMBOLS
from engine.fib_space_host import FibSpacePaperHost
from engine.fib_touch_ladder import FIB_TOUCH_LIVE_EXECUTION_ENABLED as _FIB_TOUCH_LIVE_EXECUTION_ENABLED
from engine.fib_touch_ladder import GEOMETRY_TIMEFRAMES as _FIB_TOUCH_GEOMETRY_TF
from engine.fib_touch_ladder import (
    HALVING_LEVELS,
    FibTouchConfig,
    FibTouchError,
    FibTouchLadder,
    symbol_terms,
)
from engine.fib_touch_ladder import SYMBOL_TERMS as _FIB_TOUCH_SYMBOLS
from engine.fib_touch_ladder import TIMEFRAME_MINUTES as _FIB_TOUCH_TF_MINUTES
from engine.fib_touch_ladder import LiveExecutor as _FibTouchLiveExecutor
from engine.fib_touch_ladder import PaperExecutor as _FibTouchPaperExecutor
from engine.fib_touch_ladder import SwingAnchor as _FibTouchSwingAnchor
from engine.fib_touch_ladder import find_swing_anchor as _fib_touch_find_anchor
from engine.fib_touch_ladder import find_trendline as _fib_touch_find_trendline
from engine.fib_touch_ladder import level_price as _fib_touch_level_price
from engine.indicators import infer_execution_timeframe, normalize_strategy_indicators
from engine.live import LiveEngine
from engine.market_feed import HAS_DHAN_FEED, get_market_feed, shutdown_feed
from engine.paper_trading import PaperTradingEngine
from engine.strategy_contract import validate_strategy_contract
from engine.strike_utils import round_half_up
from engine.timeframes import (
    INTRADAY_CHUNK_DAYS,
    MAX_INTRADAY_HISTORY_DAYS,
    derived_timeframe_warning,
    describe_timeframe,
    resolve_strategy_timeframe,
)
from image_uploads import ImageValidationError, sanitize_image
from journal_validation import JournalValidationError, clean_journal_payload, validate_journal_date
from market_movers import get_nifty50_market_movers_snapshot
from request_security import request_client_ip as _request_client_ip
from request_security import request_rate_subject as _request_rate_subject
from study_content import get_study_library, sanitize_study_asset

try:
    from scalp import ScalpEngine as _ScalpEngineClass
    from scalp import ScalpTrade as _ScalpTradeClass

    _HAS_SCALP = True
except ImportError:
    _HAS_SCALP = False
    _ScalpEngineClass = None
    _ScalpTradeClass = None
import alerter
from token_manager import auto_generate_token, token_renewal_loop


def _generate_startup_token_once():
    """Generate or share a Dhan token for the real app startup.

    This must not run during import-time smoke checks, otherwise deploy pre-flight
    burns a token refresh before the standby instance actually starts.
    """
    if not config.AUTO_TOKEN_ENABLED:
        print("ℹ️  [TokenManager] Auto-token disabled (set DHAN_PIN + DHAN_TOTP_SECRET in .env to enable)")
        return

    lock_file = os.path.join(_HERE, ".token_lock")
    token_file = os.path.join(_HERE, ".current_token")
    try:
        lock_handle = open(lock_file, "w")
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print("🔑 [TokenManager] Auto-token enabled — generating fresh Dhan token...")
        try:
            new_token = auto_generate_token()
        except Exception as tok_err:
            print(f"⚠️  [TokenManager] Token generation error: {tok_err}")
            new_token = None
        if new_token:
            with open(token_file, "w") as f:
                f.write(new_token)
            print("✅ [TokenManager] Token generated successfully")
        else:
            print("⚠️  [TokenManager] Auto-token failed, using existing DHAN_ACCESS_TOKEN from .env")
        fcntl.flock(lock_handle, fcntl.LOCK_UN)
        lock_handle.close()
    except (IOError, OSError):
        import time as _t

        _t.sleep(3)
        if os.path.exists(token_file):
            with open(token_file) as f:
                shared_token = f.read().strip()
            if shared_token:
                config.DHAN_ACCESS_TOKEN = shared_token
                print("✅ [TokenManager] Loaded token from first worker")


# Initialize FastAPI app
app = FastAPI(title="PhilForge", version="1.0.0")
_CORS_ALLOWED_ORIGINS = [
    "https://philforge.in",
    "https://www.philforge.in",
    # Local dev only — the loopback UI. Plain-HTTP and third-party-hosted
    # origins were dropped: an unencrypted origin carries no identity an
    # on-path attacker can't forge, and a credentialed CORS grant to a page
    # hosted elsewhere is a standing account-access handoff.
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID", "X-PhilForge-Action-Token"],
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        "philforge.in",
        "www.philforge.in",
        "philforge.test",
        "e2e.local",
        "127.0.0.1",
        "localhost",
        "testserver",
    ],
)

from error_handlers import register_error_handlers

register_error_handlers(app)

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


_ASSET_MANIFEST_PATH = os.path.join(_HERE, "static", "asset-manifest.json")
_ASSET_VERSION_CACHE: tuple[int | None, str] | None = None
_ASSET_FINGERPRINT_CACHE: str | None = None
_ASSET_FINGERPRINT_SUFFIXES = (".js", ".css")


def _asset_fingerprint() -> str:
    """A short digest of every served JS/CSS file's CONTENT.

    The manifest string is a hand-typed label, and between 2026-07-30 and
    2026-08-01 eighteen front-end commits shipped without it moving.  When it
    does not move nothing else does either: every `?v=` URL stays byte-identical
    AND the service worker's CACHE_NAME stays the same, so its activate-purge
    never fires and it answers static requests cache-first.  Users keep running
    the previous release's JavaScript and no amount of deploying changes that.

    Hashing the files removes the hand-step: change any JS or CSS and the
    version changes with it.  Computed once per process, so a deploy (which
    restarts the app) is what re-reads them.
    """

    global _ASSET_FINGERPRINT_CACHE
    if _ASSET_FINGERPRINT_CACHE is not None:
        return _ASSET_FINGERPRINT_CACHE
    digest = hashlib.blake2b(digest_size=6)
    static_root = os.path.join(_HERE, "static")
    try:
        for folder, _dirs, files in sorted(os.walk(static_root)):
            for name in sorted(files):
                if not name.endswith(_ASSET_FINGERPRINT_SUFFIXES):
                    continue
                path = os.path.join(folder, name)
                digest.update(os.path.relpath(path, static_root).encode())
                with open(path, "rb") as handle:
                    digest.update(handle.read())
    except OSError:
        # A partially readable static tree must not take the app down; the
        # manifest label alone still versions the assets, just less reliably.
        return ""
    _ASSET_FINGERPRINT_CACHE = digest.hexdigest()
    return _ASSET_FINGERPRINT_CACHE


def _asset_version() -> str:
    """Return the single frontend cache-bust version for HTML/PWA templates."""
    global _ASSET_VERSION_CACHE
    try:
        manifest_mtime = os.stat(_ASSET_MANIFEST_PATH).st_mtime_ns
    except OSError:
        manifest_mtime = None
    if _ASSET_VERSION_CACHE and _ASSET_VERSION_CACHE[0] == manifest_mtime:
        return _ASSET_VERSION_CACHE[1]
    fallback = os.getenv("PHILFORGE_ASSET_VERSION") or "dev"
    try:
        with open(_ASSET_MANIFEST_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        version = str(data.get("version") or "").strip()
    except Exception:
        version = ""
    label = version or fallback
    fingerprint = _asset_fingerprint()
    _ASSET_VERSION_CACHE = (manifest_mtime, f"{label}-{fingerprint}" if fingerprint else label)
    return _ASSET_VERSION_CACHE[1]


def _inject_asset_version(content: str) -> str:
    return content.replace("__ASSET_VERSION__", _asset_version())


def _read_frontend_template(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return _inject_asset_version(handle.read())


# Initialize custom client ONCE and pass to engine
dhan = DhanClient()
IST = ZoneInfo("Asia/Kolkata")

# ── Multi-Engine Registries (scoped by user_id, then run_id) ────
live_engines: Dict[int, Dict[str, LiveEngine]] = defaultdict(dict)
paper_engines: Dict[int, Dict[str, PaperTradingEngine]] = defaultdict(dict)
_live_tasks: Dict[int, Dict[str, asyncio.Task]] = defaultdict(dict)
_paper_tasks: Dict[int, Dict[str, asyncio.Task]] = defaultdict(dict)


def _registry_bucket(registry: dict, user_id: int) -> dict:
    return registry.setdefault(int(user_id), {})


def _iter_registry_items(registry: dict):
    for owner_id, bucket in registry.items():
        for run_id, engine in bucket.items():
            yield int(owner_id), run_id, engine


def _get_engine_owner_id(engine) -> int:
    try:
        return int(getattr(engine, "_user_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _find_user_engine(registry: dict, user_id: int, run_id: str = ""):
    bucket = _registry_bucket(registry, user_id)
    if run_id:
        return run_id, bucket.get(run_id)
    for candidate_run_id, engine in bucket.items():
        if getattr(engine, "running", False):
            return candidate_run_id, engine
    return "", None


def _now_ist() -> datetime:
    return datetime.now(IST)


def _ist_date_str(value: datetime | None = None) -> str:
    dt = value or _now_ist()
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d")
    return dt.astimezone(IST).strftime("%Y-%m-%d")


def _new_portfolio_day_bucket() -> dict:
    return {
        "real_pnl": 0.0,
        "real_net_pnl": 0.0,
        "real_charges": 0.0,
        "real_brokerage": 0.0,
        "real_total_costs": 0.0,
        "paper_pnl": 0.0,
        "real_trades": 0,
        "real_trade_legs": 0,
        "real_order_count": 0,
        "paper_trades": 0,
        "real_wins": 0,
        "paper_wins": 0,
    }


def _new_portfolio_period_bucket() -> dict:
    return {
        "real_pnl": 0.0,
        "real_net_pnl": 0.0,
        "real_charges": 0.0,
        "real_brokerage": 0.0,
        "real_total_costs": 0.0,
        "paper_pnl": 0.0,
        "total_pnl": 0.0,
        "total_net_pnl": 0.0,
        "trades": 0,
        "wins": 0,
    }


def _aggregate_portfolio_history(real_history: dict[str, dict] | None, runs: list[dict] | None):
    """Combine persisted real trade history and paper runs into daily/monthly/yearly buckets."""

    daily: dict[str, dict] = {}

    for date_str, entry in (real_history or {}).items():
        bucket = daily.setdefault(str(date_str), _new_portfolio_day_bucket())
        bucket["real_pnl"] = round(float(entry.get("pnl", 0) or 0), 2)
        bucket["real_net_pnl"] = round(float(entry.get("net_pnl", entry.get("pnl", 0)) or 0), 2)
        bucket["real_charges"] = round(float(entry.get("charges", 0) or 0), 2)
        bucket["real_brokerage"] = round(float(entry.get("brokerage", 0) or 0), 2)
        bucket["real_total_costs"] = round(
            float(entry.get("total_costs", bucket["real_charges"] + bucket["real_brokerage"]) or 0),
            2,
        )
        bucket["real_trade_legs"] = int(entry.get("trade_legs", entry.get("trades", 0)) or 0)
        bucket["real_trades"] = bucket["real_trade_legs"]
        bucket["real_order_count"] = int(entry.get("order_count", 0) or 0)
        bucket["real_wins"] = int(entry.get("wins", 0) or 0)

    for run in runs or []:
        if run.get("mode") != "paper":
            continue

        run_date = None
        started = run.get("started_at", run.get("created_at", ""))
        if started:
            run_date = str(started)[:10]

        trades = run.get("trades", [])
        if trades:
            paper_by_date: dict[str, dict] = {}
            for trade in trades:
                trade_date = str(trade.get("exit_time", trade.get("entry_time", "")))[:10]
                if not trade_date or len(trade_date) < 10:
                    trade_date = run_date or ""
                if not trade_date:
                    continue
                if trade_date not in paper_by_date:
                    paper_by_date[trade_date] = {"pnl": 0.0, "count": 0, "wins": 0}
                pnl = float(trade.get("pnl", 0) or 0)
                paper_by_date[trade_date]["pnl"] += pnl
                paper_by_date[trade_date]["count"] += 1
                if pnl > 0:
                    paper_by_date[trade_date]["wins"] += 1

            for trade_date, trade_data in paper_by_date.items():
                bucket = daily.setdefault(trade_date, _new_portfolio_day_bucket())
                bucket["paper_pnl"] += round(float(trade_data["pnl"]), 2)
                bucket["paper_trades"] += int(trade_data["count"])
                bucket["paper_wins"] += int(trade_data["wins"])
        elif run_date:
            bucket = daily.setdefault(run_date, _new_portfolio_day_bucket())
            bucket["paper_pnl"] += round(float(run.get("total_pnl", 0) or 0), 2)
            bucket["paper_trades"] += int(run.get("trade_count", 0) or 0)
            stats = run.get("stats", {})
            bucket["paper_wins"] += int(stats.get("winning_trades", 0) or 0)

    monthly: dict[str, dict] = {}
    yearly: dict[str, dict] = {}
    for date_str, day in daily.items():
        ym = date_str[:7]
        year = date_str[:4]
        monthly_bucket = monthly.setdefault(ym, _new_portfolio_period_bucket())
        yearly_bucket = yearly.setdefault(year, _new_portfolio_period_bucket())

        real_pnl = float(day.get("real_pnl", 0) or 0)
        real_net_pnl = float(day.get("real_net_pnl", real_pnl) or 0)
        real_charges = float(day.get("real_charges", 0) or 0)
        real_brokerage = float(day.get("real_brokerage", 0) or 0)
        real_total_costs = float(day.get("real_total_costs", real_charges + real_brokerage) or 0)
        paper_pnl = float(day.get("paper_pnl", 0) or 0)
        total_trades = int(day.get("real_trades", 0) or 0) + int(day.get("paper_trades", 0) or 0)
        total_wins = int(day.get("real_wins", 0) or 0) + int(day.get("paper_wins", 0) or 0)

        for bucket in (monthly_bucket, yearly_bucket):
            bucket["real_pnl"] += real_pnl
            bucket["real_net_pnl"] += real_net_pnl
            bucket["real_charges"] += real_charges
            bucket["real_brokerage"] += real_brokerage
            bucket["real_total_costs"] += real_total_costs
            bucket["paper_pnl"] += paper_pnl
            bucket["total_pnl"] += real_pnl + paper_pnl
            bucket["total_net_pnl"] += real_net_pnl + paper_pnl
            bucket["trades"] += total_trades
            bucket["wins"] += total_wins

    for period in (monthly, yearly):
        for bucket in period.values():
            for key in (
                "real_pnl",
                "real_net_pnl",
                "real_charges",
                "real_brokerage",
                "real_total_costs",
                "paper_pnl",
                "total_pnl",
                "total_net_pnl",
            ):
                bucket[key] = round(float(bucket.get(key, 0) or 0), 2)

    return daily, monthly, yearly


_TRADE_STATUTORY_CHARGE_FIELDS = (
    "sebiTax",
    "stt",
    "serviceTax",
    "exchangeTransactionCharges",
    "stampDuty",
)
_TRADE_BROKERAGE_FIELDS = ("brokerageCharges", "brokerage")
_TRADE_HISTORY_SCHEMA_VERSION = 4
_TRADE_HISTORY_REPAIR_COOLDOWN_SECONDS = 300
_trade_history_repair_attempts: dict[int, float] = {}


def _trade_fill_id_value(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in {"0", "0.0", "na", "none", "null"}:
        return None
    return text


def _trade_fill_dedupe_key(trade: dict) -> str:
    for key in ("exchangeTradeId", "tradeId", "tradeNumber"):
        value = _trade_fill_id_value(trade.get(key))
        if value is not None:
            return f"{key}:{value}"
    parts = [
        trade.get("orderId", ""),
        trade.get("exchangeOrderId", ""),
        trade.get("transactionType", ""),
        trade.get("securityId", ""),
        trade.get("tradedQuantity", ""),
        trade.get("tradedPrice", ""),
        trade.get("exchangeTime", ""),
        trade.get("createTime", ""),
        trade.get("updateTime", ""),
    ]
    return "|".join(str(part) for part in parts)


def _dedupe_trade_fills(trades: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for trade in trades or []:
        key = _trade_fill_dedupe_key(trade)
        if key in seen:
            continue
        seen.add(key)
        unique.append(trade)
    return unique


def _trade_sort_key(trade: dict):
    return (
        str(trade.get("exchangeTime") or trade.get("createTime") or trade.get("updateTime") or ""),
        str(trade.get("orderId") or trade.get("exchangeOrderId") or ""),
        str(trade.get("exchangeTradeId") or trade.get("tradeId") or trade.get("tradeNumber") or ""),
        str(trade.get("transactionType") or ""),
        str(trade.get("securityId") or trade.get("tradingSymbol") or ""),
        str(trade.get("tradedPrice") or ""),
        str(trade.get("tradedQuantity") or ""),
    )


def _trade_order_key(trade: dict) -> str:
    for key in ("orderId", "exchangeOrderId"):
        value = trade.get(key)
        if value not in (None, ""):
            return str(value)
    return _trade_fill_dedupe_key(trade)


def _trade_statutory_charge_total(trade: dict) -> float:
    return sum(float(trade.get(field, 0) or 0) for field in _TRADE_STATUTORY_CHARGE_FIELDS)


def _trade_brokerage_total(trade: dict) -> float:
    return sum(float(trade.get(field, 0) or 0) for field in _TRADE_BROKERAGE_FIELDS)


def _trade_total_costs(trade: dict) -> float:
    return _trade_statutory_charge_total(trade) + _trade_brokerage_total(trade)


def _trade_qty(trade: dict) -> float:
    return abs(float(trade.get("tradedQuantity", 0) or 0))


def _trade_price(trade: dict) -> float:
    return float(trade.get("tradedPrice", 0) or 0)


def _trade_symbol_key(trade: dict) -> str:
    return str(trade.get("securityId") or trade.get("tradingSymbol") or "unknown")


def _trade_symbol_label(trade: dict) -> str:
    return str(trade.get("customSymbol") or trade.get("tradingSymbol") or _trade_symbol_key(trade))


def _trade_date_str(trade: dict) -> str:
    raw_time = trade.get("exchangeTime") or trade.get("createTime") or trade.get("updateTime") or ""
    date_str = str(raw_time)[:10]
    return date_str if date_str and len(date_str) >= 10 else ""


def _trade_history_entry_needs_refresh(
    entry: dict | None,
    *,
    trade_date: str | None = None,
    today_str: str | None = None,
) -> bool:
    if not isinstance(entry, dict) or not entry:
        return True
    try:
        if int(entry.get("schema_version") or 0) < _TRADE_HISTORY_SCHEMA_VERSION:
            return True
    except Exception:
        return True
    if trade_date and trade_date != (today_str or _ist_date_str()):
        if str(entry.get("source") or "") != "historical_fifo":
            return True
    return False


def _trade_history_needs_repair(user_id: int, history: dict[str, dict]) -> bool:
    if not history:
        return True
    today_str = _ist_date_str()
    if not any(
        _trade_history_entry_needs_refresh(entry, trade_date=date_str, today_str=today_str)
        for date_str, entry in history.items()
    ):
        return False
    last_attempt = float(_trade_history_repair_attempts.get(int(user_id), 0) or 0)
    return (time.monotonic() - last_attempt) >= _TRADE_HISTORY_REPAIR_COOLDOWN_SECONDS


def _trade_history_refresh_start(
    history: dict[str, dict] | None,
    default_from_date: str = "2024-01-01",
    *,
    today_str: str | None = None,
    recent_window_days: int = 120,
) -> str:
    try:
        refresh_floor = date.fromisoformat(str(default_from_date)[:10])
    except ValueError:
        refresh_floor = date(2024, 1, 1)

    today_value = str(today_str or _ist_date_str())[:10]
    try:
        today_date = date.fromisoformat(today_value)
    except ValueError:
        today_date = datetime.now(_IST).date()

    stale_dates: list[date] = []
    for trade_date, entry in (history or {}).items():
        trade_date_str = str(trade_date)[:10]
        try:
            parsed_date = date.fromisoformat(trade_date_str)
        except ValueError:
            continue
        if _trade_history_entry_needs_refresh(entry, trade_date=trade_date_str, today_str=today_value):
            stale_dates.append(parsed_date)

    if not stale_dates:
        return refresh_floor.isoformat()

    recent_cutoff = today_date - timedelta(days=max(int(recent_window_days or 0), 0))
    recent_stale_dates = [value for value in stale_dates if value >= recent_cutoff]
    refresh_start = min(recent_stale_dates) if recent_stale_dates else max(stale_dates)
    refresh_start = max(refresh_floor, refresh_start.replace(day=1))
    return refresh_start.isoformat()


def _new_trade_history_entry(*, source: str, calculation_mode: str) -> dict:
    return {
        "schema_version": _TRADE_HISTORY_SCHEMA_VERSION,
        "source": source,
        "calculation_mode": calculation_mode,
        "pnl": 0.0,
        "net_pnl": 0.0,
        "charges": 0.0,
        "brokerage": 0.0,
        "total_costs": 0.0,
        "trades": 0,
        "trade_legs": 0,
        "order_count": 0,
        "wins": 0,
        "mode": "real",
        "details": [],
    }


def _summarize_real_trade_history(
    trades: list[dict],
    *,
    source: str,
    carry_inventory: bool,
) -> dict[str, dict]:
    unique_trades = _dedupe_trade_fills(trades)
    if not unique_trades:
        return {}

    sorted_trades = sorted(unique_trades, key=_trade_sort_key)
    open_longs: dict[str, deque] = defaultdict(deque)
    open_shorts: dict[str, deque] = defaultdict(deque)
    entries: dict[str, dict] = {}
    order_keys_by_day: dict[str, set[str]] = defaultdict(set)
    symbol_details_by_day: dict[str, dict[str, dict]] = defaultdict(dict)
    current_date = ""

    for trade in sorted_trades:
        date_str = _trade_date_str(trade)
        if not date_str:
            continue
        if not carry_inventory and date_str != current_date:
            open_longs = defaultdict(deque)
            open_shorts = defaultdict(deque)
            current_date = date_str
        entry = entries.setdefault(
            date_str,
            _new_trade_history_entry(
                source=source,
                calculation_mode="cross_day_fifo" if carry_inventory else "day_fifo",
            ),
        )
        entry["trade_legs"] += 1
        entry["trades"] += 1
        order_keys_by_day[date_str].add(_trade_order_key(trade))

        side = str(trade.get("transactionType") or "").upper()
        qty = _trade_qty(trade)
        price = _trade_price(trade)

        symbol_key = _trade_symbol_key(trade)
        detail = symbol_details_by_day[date_str].setdefault(
            symbol_key,
            {
                "symbol": _trade_symbol_label(trade),
                "pnl": 0.0,
                "charges": 0.0,
                "brokerage": 0.0,
                "total_costs": 0.0,
                "qty": 0.0,
                "buy_qty": 0.0,
                "buy_value": 0.0,
                "sell_qty": 0.0,
                "sell_value": 0.0,
                "closed_segments": 0,
                "fill_count": 0,
            },
        )
        statutory_charges = _trade_statutory_charge_total(trade)
        brokerage = _trade_brokerage_total(trade)
        entry["charges"] += statutory_charges
        entry["brokerage"] += brokerage
        detail["charges"] += statutory_charges
        detail["brokerage"] += brokerage
        detail["total_costs"] += statutory_charges + brokerage
        detail["fill_count"] += 1

        if side not in {"BUY", "SELL"} or qty <= 0:
            continue

        remaining = qty
        if side == "BUY":
            detail["buy_qty"] += qty
            detail["buy_value"] += qty * price
            while remaining > 1e-9 and open_shorts[symbol_key]:
                open_fill = open_shorts[symbol_key][0]
                matched = min(remaining, open_fill["qty"])
                pnl = (open_fill["price"] - price) * matched
                entry["pnl"] += pnl
                detail["pnl"] += pnl
                detail["qty"] += matched
                detail["closed_segments"] += 1
                if pnl > 0:
                    entry["wins"] += 1
                open_fill["qty"] -= matched
                remaining -= matched
                if open_fill["qty"] <= 1e-9:
                    open_shorts[symbol_key].popleft()
            if remaining > 1e-9:
                open_longs[symbol_key].append({"qty": remaining, "price": price})
        else:
            detail["sell_qty"] += qty
            detail["sell_value"] += qty * price
            while remaining > 1e-9 and open_longs[symbol_key]:
                open_fill = open_longs[symbol_key][0]
                matched = min(remaining, open_fill["qty"])
                pnl = (price - open_fill["price"]) * matched
                entry["pnl"] += pnl
                detail["pnl"] += pnl
                detail["qty"] += matched
                detail["closed_segments"] += 1
                if pnl > 0:
                    entry["wins"] += 1
                open_fill["qty"] -= matched
                remaining -= matched
                if open_fill["qty"] <= 1e-9:
                    open_longs[symbol_key].popleft()
            if remaining > 1e-9:
                open_shorts[symbol_key].append({"qty": remaining, "price": price})

    for date_str, entry in entries.items():
        entry["pnl"] = round(float(entry.get("pnl", 0) or 0), 2)
        entry["charges"] = round(float(entry.get("charges", 0) or 0), 2)
        entry["brokerage"] = round(float(entry.get("brokerage", 0) or 0), 2)
        entry["total_costs"] = round(entry["charges"] + entry["brokerage"], 2)
        entry["net_pnl"] = round(entry["pnl"] - entry["total_costs"], 2)
        entry["trades"] = int(entry.get("trades", 0) or 0)
        entry["trade_legs"] = int(entry.get("trade_legs", entry["trades"]) or 0)
        entry["order_count"] = len(order_keys_by_day.get(date_str) or set())

        details = []
        for detail in symbol_details_by_day.get(date_str, {}).values():
            details.append(
                {
                    "symbol": detail["symbol"],
                    "pnl": round(detail["pnl"], 2),
                    "qty": int(round(detail["qty"])),
                    "buy_avg": round(detail["buy_value"] / detail["buy_qty"], 2) if detail["buy_qty"] else 0.0,
                    "sell_avg": round(detail["sell_value"] / detail["sell_qty"], 2) if detail["sell_qty"] else 0.0,
                    "charges": round(detail["charges"], 2),
                    "brokerage": round(detail["brokerage"], 2),
                    "total_costs": round(detail["total_costs"], 2),
                    "fill_count": int(detail["fill_count"]),
                    "closed_segments": int(detail["closed_segments"]),
                }
            )
        details.sort(key=lambda item: item["symbol"])
        entry["details"] = details

    return entries


def _summarize_real_trade_fills(trades: list[dict]) -> dict | None:
    """Summarize the latest live-trade day from Dhan fills.

    Live get_trades() snapshots are treated as day-local. Completed dates are
    later rebuilt from historical trade history using cross-day FIFO.
    """

    entries = _summarize_real_trade_history(trades, source="live_day_fifo", carry_inventory=False)
    if not entries:
        return None
    latest_date = max(entries.keys())
    return entries.get(latest_date)


def _running_statuses_for_user(registry: dict, user_id: int) -> list[dict]:
    return [engine.get_status() for engine in _registry_bucket(registry, user_id).values() if engine.running]


def _any_running(registry: dict, user_id: int | None = None) -> bool:
    if user_id is None:
        return any(engine.running for _, _, engine in _iter_registry_items(registry))
    return any(engine.running for engine in _registry_bucket(registry, user_id).values())


def _engine_state_dir(user_id: int, create: bool = True) -> str:
    state_dir = os.path.join(config.USER_DATA_ROOT, str(int(user_id or 0)), "engine_state")
    if create:
        os.makedirs(state_dir, exist_ok=True)
    return state_dir


def _iter_user_state_files(prefix: str):
    if not os.path.isdir(config.USER_DATA_ROOT):
        return
    for user_folder in sorted(os.listdir(config.USER_DATA_ROOT)):
        if not str(user_folder).isdigit():
            continue
        user_id = int(user_folder)
        state_dir = _engine_state_dir(user_id, create=False)
        if not os.path.isdir(state_dir):
            continue
        for fname in os.listdir(state_dir):
            if fname.startswith(prefix) and fname.endswith(".json"):
                yield user_id, state_dir, fname, os.path.join(state_dir, fname)


_DASHBOARD_REAL_CACHE: Dict[int, dict] = {}
_DASHBOARD_REAL_CACHE_TTL_SECONDS = 20.0
_FII_DII_CACHE = {"timestamp": 0.0, "ttl": 1800.0, "data": None}
_FII_DII_HISTORY_ROWS: list[dict] | None = None


def _market_cache_dir(create: bool = True) -> str:
    cache_dir = os.path.join(_HERE, "market_cache")
    if create:
        os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _fii_dii_history_file() -> str:
    return os.path.join(_market_cache_dir(), "fii_dii_history.json")


def _trade_day_text(trade: dict, *, prefer_exit: bool = True) -> str:
    if not isinstance(trade, dict):
        return ""
    keys = (
        ("exit_time", "closed_at", "entry_time", "created_at")
        if prefer_exit
        else (
            "entry_time",
            "created_at",
            "exit_time",
            "closed_at",
        )
    )
    for key in keys:
        value = str(trade.get(key) or "").strip()
        if len(value) >= 10:
            return value[:10]
    return ""


def _trade_unique_key(trade: dict) -> str:
    if not isinstance(trade, dict):
        return ""
    trade_id = str(trade.get("trade_id") or "").strip()
    if trade_id:
        return f"trade_id:{trade_id}"
    parts = [
        str(trade.get("mode") or ""),
        str(trade.get("symbol") or trade.get("underlying") or ""),
        str(trade.get("entry_time") or ""),
        str(trade.get("exit_time") or ""),
        str(trade.get("transaction_type") or ""),
        str(trade.get("lots") or trade.get("quantity") or ""),
    ]
    return "|".join(parts)


def _empty_scalp_flow_bucket() -> dict:
    return {
        "pnl": 0.0,
        "trades": 0,
        "open_count": 0,
        "closed_count": 0,
        "underlyings": [],
        "active": False,
    }


def _collect_dashboard_scalp_snapshot(
    today_str: str,
    persisted_trades: list[dict] | None,
    scalp_status: dict | None,
) -> dict:
    buckets = {
        "paper": _empty_scalp_flow_bucket(),
        "live": _empty_scalp_flow_bucket(),
    }

    def _register_trade(trade: dict, *, bucket_name: str, open_trade: bool):
        bucket = buckets[bucket_name]
        bucket["pnl"] += float(trade.get("pnl") or 0)
        bucket["trades"] += 1
        bucket["open_count"] += 1 if open_trade else 0
        bucket["closed_count"] += 0 if open_trade else 1
        underlying = str(trade.get("underlying") or "").strip()
        if underlying and underlying not in bucket["underlyings"]:
            bucket["underlyings"].append(underlying)

    seen_closed: set[str] = set()
    for trade in list(persisted_trades or []) + list((scalp_status or {}).get("closed_trades") or []):
        if not isinstance(trade, dict):
            continue
        if _trade_day_text(trade, prefer_exit=True) != today_str:
            continue
        mode = _trade_mode_value(trade)
        if mode not in buckets:
            continue
        trade_key = _trade_unique_key(trade)
        if trade_key in seen_closed:
            continue
        seen_closed.add(trade_key)
        _register_trade(trade, bucket_name=mode, open_trade=False)

    seen_open: set[str] = set()
    for trade in list((scalp_status or {}).get("open_trades") or []):
        if not isinstance(trade, dict):
            continue
        if _trade_day_text(trade, prefer_exit=False) != today_str:
            continue
        mode = _trade_mode_value(trade)
        if mode not in buckets:
            continue
        trade_key = _trade_unique_key(trade)
        if trade_key in seen_open or trade_key in seen_closed:
            continue
        seen_open.add(trade_key)
        _register_trade(trade, bucket_name=mode, open_trade=True)

    engine_running = bool((scalp_status or {}).get("running"))
    for bucket in buckets.values():
        bucket["pnl"] = round(bucket["pnl"], 2)
        bucket["active"] = bool(bucket["open_count"] or (engine_running and bucket["trades"]))
    return buckets


def _compact_label_list(labels: list[str], default_label: str) -> str:
    unique = [str(label).strip() for label in labels if str(label).strip()]
    unique = list(dict.fromkeys(unique))
    if not unique:
        return default_label
    if len(unique) <= 2:
        return " · ".join(unique)
    return " · ".join(unique[:2]) + f" +{len(unique) - 2}"


def _scalp_label(underlyings: list[str]) -> str:
    unique = [str(item).strip() for item in underlyings if str(item).strip()]
    unique = list(dict.fromkeys(unique))
    if not unique:
        return "SCALP"
    if len(unique) == 1:
        return f"SCALP {unique[0]}"
    return f"SCALP {unique[0]} +{len(unique) - 1}"


def _empty_dashboard_real_snapshot(message: str = "") -> dict:
    return {
        "available": False,
        "source": "engine_fallback",
        "source_label": "Engine view",
        "gross_pnl": 0.0,
        "net_pnl": 0.0,
        "charges": 0.0,
        "brokerage": 0.0,
        "trades": 0,
        "message": message,
        "stale": False,
    }


def _load_dashboard_real_snapshot_sync(user_id: int, broker_client: DhanClient | None) -> dict:
    today_str = _ist_date_str()
    now = time.time()
    cached = _DASHBOARD_REAL_CACHE.get(int(user_id))
    if (
        cached
        and cached.get("date") == today_str
        and (now - float(cached.get("timestamp") or 0)) < _DASHBOARD_REAL_CACHE_TTL_SECONDS
    ):
        return deepcopy(cached.get("data") or _empty_dashboard_real_snapshot())

    if broker_client is None:
        if cached and cached.get("date") == today_str:
            payload = deepcopy(cached.get("data") or _empty_dashboard_real_snapshot())
            payload["stale"] = True
            return payload
        return _empty_dashboard_real_snapshot("Broker not connected")

    try:
        trades = broker_client.get_trades()
        summary = _summarize_real_trade_fills(trades or []) or {}
        payload = {
            "available": True,
            "source": "dhan",
            "source_label": "Dhan today",
            "gross_pnl": round(float(summary.get("pnl", 0) or 0), 2),
            "net_pnl": round(float(summary.get("net_pnl", 0) or 0), 2),
            "charges": round(float(summary.get("charges", 0) or 0), 2),
            "brokerage": round(float(summary.get("brokerage", 0) or 0), 2),
            "trades": int(summary.get("order_count", summary.get("trades", 0)) or 0),
            "fill_count": int(summary.get("trade_legs", summary.get("trades", 0)) or 0),
            "message": "Dhan tradebook",
            "stale": False,
        }
        if not summary:
            payload["message"] = "No Dhan trades today"
        _DASHBOARD_REAL_CACHE[int(user_id)] = {
            "date": today_str,
            "timestamp": now,
            "data": deepcopy(payload),
        }
        return payload
    except Exception as exc:
        if cached and cached.get("date") == today_str:
            payload = deepcopy(cached.get("data") or _empty_dashboard_real_snapshot())
            payload["stale"] = True
            payload["message"] = "Using cached Dhan tradebook"
            return payload
        return _empty_dashboard_real_snapshot(str(exc))


async def _load_dashboard_real_snapshot(user_id: int, broker_client: DhanClient | None) -> dict:
    return await asyncio.to_thread(_load_dashboard_real_snapshot_sync, int(user_id), broker_client)


def _parse_fii_dii_date(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _display_fii_dii_date(value: str) -> str:
    parsed = _parse_fii_dii_date(value)
    if parsed is None:
        return str(value or "").strip()
    return parsed.strftime("%d %b")


def _load_fii_dii_history_rows() -> list[dict]:
    global _FII_DII_HISTORY_ROWS
    if _FII_DII_HISTORY_ROWS is not None:
        return deepcopy(_FII_DII_HISTORY_ROWS)
    file_path = _fii_dii_history_file()
    rows: list[dict] = []
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, list):
                rows = [row for row in payload if isinstance(row, dict)]
        except Exception:
            rows = []
    _FII_DII_HISTORY_ROWS = rows
    return deepcopy(rows)


def _save_fii_dii_history_rows(rows: list[dict]) -> None:
    global _FII_DII_HISTORY_ROWS
    _FII_DII_HISTORY_ROWS = deepcopy(rows)
    file_path = _fii_dii_history_file()
    tmp_path = file_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=True)
    os.replace(tmp_path, file_path)


def _normalize_fii_dii_snapshot_rows(records: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        raw_date = str(record.get("date") or "").strip()
        parsed_date = _parse_fii_dii_date(raw_date)
        if parsed_date is None:
            continue
        iso_date = parsed_date.isoformat()
        row = grouped.setdefault(
            iso_date,
            {
                "date": iso_date,
                "display_date": parsed_date.strftime("%d %b"),
                "fii_net": 0.0,
                "dii_net": 0.0,
            },
        )
        category = str(record.get("category") or "").upper()
        try:
            net_value = float(record.get("netValue") or 0)
        except (TypeError, ValueError):
            net_value = 0.0
        if "FII" in category or "FPI" in category:
            row["fii_net"] = round(net_value, 2)
        elif "DII" in category:
            row["dii_net"] = round(net_value, 2)
    rows = list(grouped.values())
    rows.sort(key=lambda item: item["date"], reverse=True)
    return rows


def _merge_fii_dii_history_rows(existing: list[dict], snapshot_rows: list[dict]) -> list[dict]:
    merged = {str(row.get("date") or ""): dict(row) for row in existing if isinstance(row, dict)}
    for row in snapshot_rows:
        if isinstance(row, dict) and row.get("date"):
            merged[str(row["date"])] = dict(row)
    rows = [row for row in merged.values() if row.get("date")]
    rows.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    return rows[:45]


def _load_dashboard_fii_dii_snapshot_sync() -> dict:
    now = time.time()
    cached = _FII_DII_CACHE.get("data")
    if cached and (now - float(_FII_DII_CACHE.get("timestamp") or 0)) < float(_FII_DII_CACHE.get("ttl") or 0):
        return deepcopy(cached)

    history_rows = _load_fii_dii_history_rows()
    snapshot_rows: list[dict] = []
    error_text = ""

    try:
        import httpx

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com/reports/fii-dii",
        }
        with httpx.Client(headers=headers, follow_redirects=True, timeout=8) as client:
            response = client.get("https://www.nseindia.com/api/fiidiiTradeReact")
            if response.status_code == 200:
                snapshot_rows = _normalize_fii_dii_snapshot_rows(response.json())
            else:
                error_text = f"NSE returned {response.status_code}"
    except Exception as exc:
        error_text = str(exc)

    if snapshot_rows:
        merged_rows = _merge_fii_dii_history_rows(history_rows, snapshot_rows)
        if merged_rows != history_rows:
            _save_fii_dii_history_rows(merged_rows)
        history_rows = merged_rows

    if not history_rows:
        payload = {
            "status": "unavailable",
            "source": "Official NSE combined feed",
            "as_of": "",
            "latest": None,
            "rolling_30d": {"fii_net": 0.0, "dii_net": 0.0, "days": 0},
            "trend": [],
            "message": error_text or "FII / DII data unavailable",
        }
        _FII_DII_CACHE["timestamp"] = now
        _FII_DII_CACHE["data"] = deepcopy(payload)
        return payload

    window_start = _now_ist().date() - timedelta(days=31)
    rolling_rows = []
    for row in history_rows:
        parsed_date = _parse_fii_dii_date(str(row.get("date") or ""))
        if parsed_date is None:
            continue
        if parsed_date >= window_start:
            rolling_rows.append(row)

    latest = history_rows[0]
    payload = {
        "status": "ok" if len(rolling_rows) >= 10 else "partial",
        "source": "Official NSE combined feed",
        "as_of": latest.get("display_date") or _display_fii_dii_date(latest.get("date") or ""),
        "latest": latest,
        "rolling_30d": {
            "fii_net": round(sum(float(row.get("fii_net") or 0) for row in rolling_rows), 2),
            "dii_net": round(sum(float(row.get("dii_net") or 0) for row in rolling_rows), 2),
            "days": len(rolling_rows),
        },
        "rolling_daily": rolling_rows[:30],
        "trend": history_rows[:10],
        "message": "" if len(rolling_rows) >= 10 else "Rolling history builds from the official NSE daily feed.",
    }
    _FII_DII_CACHE["timestamp"] = now
    _FII_DII_CACHE["data"] = deepcopy(payload)
    return payload


async def _load_dashboard_fii_dii_snapshot() -> dict:
    return await asyncio.to_thread(_load_dashboard_fii_dii_snapshot_sync)


# Backfill status — read by /api/backfill/status
_backfill_state: Dict[str, object] = {
    "status": "idle",  # idle | running | done | error
    "message": "",
    "new_dates": 0,
}

# Stopped engine snapshots — persisted per user under engine_state/
_stopped_engines: Dict[int, Dict[str, dict]] = {}


def _stopped_engines_file(user_id: int) -> str:
    return os.path.join(_engine_state_dir(user_id), "stopped_engines.json")


def _normalize_engine_mode(mode: str | None) -> str:
    return "paper" if str(mode or "").strip().casefold() == "paper" else "auto"


def _engine_snapshot_key(run_id: str, mode: str | None) -> str:
    return f"{_normalize_engine_mode(mode)}:{str(run_id or '').strip()}"


def _engine_status_key(status: dict) -> str:
    return _engine_snapshot_key(status.get("run_id", ""), status.get("mode"))


def _load_stopped_engines(user_id: int) -> dict:
    cached = _stopped_engines.get(int(user_id))
    if cached is not None:
        return cached
    data: dict = {}
    normalized = False
    file_path = _stopped_engines_file(user_id)
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for raw_key, snapshot in loaded.items():
                    if not isinstance(snapshot, dict):
                        continue
                    raw_key_str = str(raw_key or "")
                    key_mode = None
                    key_run_id = raw_key_str
                    if ":" in raw_key_str:
                        maybe_mode, maybe_run_id = raw_key_str.split(":", 1)
                        if maybe_run_id:
                            key_mode = maybe_mode
                            key_run_id = maybe_run_id
                    run_id = str(snapshot.get("run_id") or key_run_id or "").strip()
                    if not run_id:
                        continue
                    mode = _normalize_engine_mode(snapshot.get("mode") or key_mode)
                    snapshot["run_id"] = run_id
                    snapshot["mode"] = mode
                    new_key = _engine_snapshot_key(run_id, mode)
                    data[new_key] = snapshot
                    if new_key != raw_key_str:
                        normalized = True
        except Exception:
            data = {}
    _stopped_engines[int(user_id)] = data
    if normalized:
        _save_stopped_engines(user_id)
    return data


def _save_stopped_engines(user_id: int):
    try:
        data = _stopped_engines.get(int(user_id), {})
        file_path = _stopped_engines_file(user_id)
        tmp = file_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, file_path)
    except Exception:
        pass


def _state_has_open_positions(state: dict) -> bool:
    if not isinstance(state, dict):
        return False
    positions = state.get("positions") or []
    return any((position or {}).get("status") != "closed" for position in positions)


def _state_file_snapshots(user_id: int) -> list[dict]:
    """Build Live-page snapshots from persisted engine state files.

    This preserves previously visible Live tabs after migration/redeploy,
    including valid cross-day carry positions.
    """

    state_dir = _engine_state_dir(user_id, create=False)
    if not os.path.isdir(state_dir):
        return []

    today = str(date.today())
    snapshots: list[dict] = []

    def _snapshot_from_state(fname: str, state: dict, mode: str) -> dict | None:
        strategy = state.get("strategy") or {}
        run_id = strategy.get("run_name") or state.get("strategy_name") or fname.rsplit(".", 1)[0]
        if not run_id:
            return None

        closed_trades = state.get("closed_trades") or []
        total_pnl = state.get("daily_pnl")
        if total_pnl is None:
            total_pnl = sum((t or {}).get("pnl", 0) for t in closed_trades)

        return {
            "run_id": run_id,
            "mode": mode,
            "running": False,
            "in_trade": bool(state.get("in_trade", False)),
            "positions": state.get("positions") or [],
            "closed_trades": closed_trades,
            "total_pnl": round(float(total_pnl or 0), 2),
            "trades_today": int(state.get("trades_today") or len(closed_trades)),
            "strategy_name": state.get("strategy_name") or run_id,
            "instrument": state.get("instrument") or strategy.get("instrument") or "",
            "current_candle": state.get("current_candle") or {},
            "current_indicators": state.get("current_indicators") or {},
            "event_log": state.get("event_log") or [],
            "current_time": state.get("current_time") or "",
            "strategy": strategy,
            "_snapshot_source": "state_file",
            "_snapshot_saved_at": state.get("saved_at") or "",
        }

    for fname in sorted(os.listdir(state_dir)):
        if not fname.endswith(".json"):
            continue
        if fname.startswith("paper_state_"):
            mode = "paper"
        elif fname.startswith("live_state_"):
            mode = "auto"
        else:
            continue

        fpath = os.path.join(state_dir, fname)
        try:
            with open(fpath, "r") as f:
                state = json.load(f)
        except Exception as e:
            _logger.warning("[LivePanels] Failed to read state snapshot %s for user %s: %s", fpath, user_id, e)
            continue

        if not isinstance(state, dict):
            continue
        if state.get("session_date") != today and not _state_has_open_positions(state):
            continue

        snap = _snapshot_from_state(fname, state, mode)
        if snap:
            snapshots.append(snap)

    snapshots.sort(key=lambda s: (s.get("_snapshot_saved_at") or "", s.get("run_id") or ""), reverse=True)
    return snapshots


# Trade state tracker for Telegram alerts (keyed by run_id)
_alert_state: Dict[str, dict] = {}  # {"in_trade": bool, "closed_count": int}


def _alert_state_key(user_id: int | None, run_id: str) -> str:
    return f"{int(user_id or 0)}:{run_id}"


def _check_trade_alerts(run_id: str, mode_label: str, event: dict, user_id: int | None = None):
    """Detect trade entry/exit from engine status updates and fire Telegram alerts."""
    if event.get("type") in ("status", "price_update"):
        return  # Skip non-status-change events
    in_trade = event.get("in_trade", False)
    closed_trades = event.get("closed_trades", [])
    positions = event.get("positions", [])
    total_pnl = event.get("total_pnl", 0)
    state_key = _alert_state_key(user_id, run_id)
    prev = _alert_state.get(state_key, {"in_trade": False, "closed_count": 0})

    # Detect entry: was not in trade, now in trade
    if in_trade and not prev["in_trade"]:
        pos_lines = []
        for p in positions:
            sym = p.get("symbol") or p.get("trading_symbol") or "—"
            txn = p.get("transaction_type", "")
            premium = p.get("entry_premium", 0)
            pos_lines.append(f"  {txn} {sym} @ ₹{premium:.2f}")
        body = f"Strategy: {run_id}\nMode: {mode_label}\n" + "\n".join(pos_lines)
        alerter.alert("Trade Entry", body, level="info")

    # Detect exit: closed_trades count increased
    new_count = len(closed_trades)
    if new_count > prev["closed_count"]:
        new_trades = closed_trades[prev["closed_count"] :]
        for t in new_trades:
            sym = t.get("symbol") or t.get("trading_symbol") or "—"
            pnl = round(t.get("pnl", 0), 2)
            reason = t.get("exit_reason") or t.get("reason") or "—"
            level = "info" if pnl >= 0 else "warn"
            body = (
                f"Strategy: {run_id}\nMode: {mode_label}\n"
                f"Symbol: {sym}\nP&L: ₹{pnl:.2f}\nReason: {reason}\n"
                f"Total P&L: ₹{round(total_pnl, 2):.2f}"
            )
            alerter.alert("Trade Exit", body, level=level)

    _alert_state[state_key] = {"in_trade": in_trade, "closed_count": new_count}


# Global WebSocket market feed (singleton — shared by paper + live engines)
_market_feed = get_market_feed(dhan) if HAS_DHAN_FEED else None
_scalp_engines: Dict[int, "_ScalpEngineClass"] = {}
_scalp_open_state_last_save: Dict[int, float] = defaultdict(float)
_SCALP_OPEN_STATE_SAVE_INTERVAL_SEC = 5.0
_SKIP_STARTUP_JOBS = (os.getenv("PHILFORGE_SKIP_STARTUP_JOBS") or "").lower() in {"1", "true", "yes"}


# NIFTY Options Cascade is deliberately isolated from the generic paper/live
# engine registries.  It has one fixed CE contract and index-space geometry, so
# treating it as a generic strategy would blur its safety rules.
@dataclass
class _CascadeRuntime:
    engine: NiftyOptionsPaperCascade
    adapter: CascadeOptionsAdapter
    broker: DhanClient
    last_candle_timestamp: datetime
    task: asyncio.Task | None = None
    running: bool = True


@dataclass
class _TerminalCascadeRuntime:
    engine: CashCascadePaperEngine
    broker: DhanClient
    signal_instrument: dict
    trade_instrument: dict
    last_candle_timestamp: datetime
    task: asyncio.Task | None = None
    running: bool = True


_cascade_engines: Dict[int, _CascadeRuntime] = {}
_candle_entry_engines: Dict[int, _CascadeRuntime] = {}
# One ladder PER SYMBOL per user: picking an instrument in the form is what a
# second Start is for, so five instruments can run five ladders side by side.
# Starting the same symbol twice is still a 409.
_fib_boundary_engines: Dict[int, Dict[str, _CascadeRuntime]] = {}
_terminal_cascade_engines: Dict[int, Dict[str, _TerminalCascadeRuntime]] = {}
_cascade_open_state_last_save: Dict[int, float] = defaultdict(float)
_candle_entry_open_state_last_save: Dict[int, float] = defaultdict(float)
_fib_boundary_open_state_last_save: Dict[int, float] = defaultdict(float)
_terminal_cascade_open_state_last_save: Dict[int, float] = defaultdict(float)
_CASCADE_OPEN_STATE_SAVE_INTERVAL_SEC = 5.0
_TERMINAL_CASCADE_OPEN_STATE_SAVE_INTERVAL_SEC = 5.0
_CASCADE_LIVE_FLAG = (os.getenv("PHILFORGE_CASCADE_OPTIONS_LIVE") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_TERMINAL_CASCADE_LIVE_FLAG = (os.getenv("PHILFORGE_TERMINAL_CASCADE_LIVE") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_CANDLE_ENTRY_HISTORY_DAYS = 15


def _cascade_open_state_key(user_id: int) -> str:
    return f"cascade_options_open:{int(user_id)}"


def _terminal_cascade_open_state_key(user_id: int) -> str:
    return f"terminal_cash_cascade_open:{int(user_id)}"


def _candle_entry_open_state_key(user_id: int) -> str:
    return f"candle_entry_open:{int(user_id)}"


def _fib_boundary_open_state_key(user_id: int) -> str:
    return f"fib_boundary_open:{int(user_id)}"


def _cascade_premium_lookup(broker: DhanClient):
    """Return only a current premium; never invent a historical option fill."""

    def lookup(candle_timestamp: datetime, contract: FixedCampaignOption) -> float | None:
        now = datetime.now(IST)
        timestamp = (
            candle_timestamp.replace(tzinfo=IST)
            if candle_timestamp.tzinfo is None
            else candle_timestamp.astimezone(IST)
        )
        # Dhan's one-contract LTP is a current quote.  It must not be silently
        # used to fill a candle missed during a server outage.
        if abs((now - timestamp).total_seconds()) > 7 * 60:
            return None
        try:
            value = broker.get_option_ltp(
                contract.underlying, contract.strike, contract.expiry.isoformat(), contract.option_type
            )
            return float(value) if float(value or 0) > 0 else None
        except Exception:
            return None

    return lookup


async def _notify_cascade_ws(user_id: int) -> None:
    runtime = _cascade_engines.get(int(user_id))
    if runtime is None:
        return
    await _broadcast_user_ws_json(
        int(user_id),
        {"type": "cascade_status", "cascade": {**runtime.engine.get_status(), "running": runtime.running}},
    )


async def _save_cascade_open_state(
    user_id: int, runtime: _CascadeRuntime | None = None, *, force: bool = False
) -> None:
    runtime = runtime or _cascade_engines.get(int(user_id))
    if runtime is None:
        return
    now = time.time()
    if not force and now - _cascade_open_state_last_save[int(user_id)] < _CASCADE_OPEN_STATE_SAVE_INTERVAL_SEC:
        return
    payload = {
        "running": bool(runtime.running),
        "last_candle_timestamp": runtime.last_candle_timestamp.isoformat(),
        "saved_at": datetime.now(IST).isoformat(),
        "engine": runtime.engine.to_dict(),
    }
    await _db_mod.set_app_state(_cascade_open_state_key(user_id), json.dumps(payload, default=str))
    _cascade_open_state_last_save[int(user_id)] = now


async def _restore_cascade_open_state(user_id: int, broker: DhanClient | None) -> _CascadeRuntime | None:
    existing = _cascade_engines.get(int(user_id))
    if existing is not None:
        return existing
    if broker is None:
        return None
    raw = await _db_mod.get_app_state(_cascade_open_state_key(user_id))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not payload.get("engine"):
            return None
        adapter = CascadeOptionsAdapter(broker, paper_only=True)
        engine = NiftyOptionsPaperCascade.from_dict(
            payload["engine"], adapter=adapter, option_premium_lookup=_cascade_premium_lookup(broker)
        )
        last_text = str(payload.get("last_candle_timestamp") or "")
        last = (
            datetime.fromisoformat(last_text.replace("Z", "+00:00"))
            if last_text
            else engine.geometry.history[-1].timestamp
        )
        if last.tzinfo is None:
            last = last.replace(tzinfo=IST)
        runtime = _CascadeRuntime(
            engine=engine,
            adapter=adapter,
            broker=broker,
            last_candle_timestamp=last,
            running=bool(payload.get("running")),
        )
        _cascade_engines[int(user_id)] = runtime
        if runtime.running and _engine_restore_owner_is_active_instance():
            runtime.task = asyncio.create_task(_run_cascade_paper_loop(int(user_id), runtime))
        return runtime
    except Exception as exc:
        _logger.warning("[CASCADE] Skipping invalid persisted paper campaign for user %s: %s", user_id, exc)
        return None


async def _save_specialized_cascade_state(
    user_id: int,
    registry: Dict[int, _CascadeRuntime],
    state_key: str,
    last_save: Dict[int, float],
    *,
    force: bool = False,
) -> None:
    runtime = registry.get(int(user_id))
    if runtime is None:
        return
    now = time.time()
    if not force and now - last_save[int(user_id)] < _CASCADE_OPEN_STATE_SAVE_INTERVAL_SEC:
        return
    payload = {
        "running": bool(runtime.running),
        "last_candle_timestamp": runtime.last_candle_timestamp.isoformat(),
        "saved_at": datetime.now(IST).isoformat(),
        "engine": runtime.engine.to_dict(),
    }
    await _db_mod.set_app_state(state_key, json.dumps(payload, default=str))
    last_save[int(user_id)] = now


async def _save_candle_entry_open_state(user_id: int, *, force: bool = False) -> None:
    await _save_specialized_cascade_state(
        user_id,
        _candle_entry_engines,
        _candle_entry_open_state_key(user_id),
        _candle_entry_open_state_last_save,
        force=force,
    )


async def _save_fib_boundary_open_state(user_id: int, *, force: bool = False) -> None:
    """Persist every ladder this user has running, keyed by its own symbol.

    One row still holds the lot, as a ``campaigns`` list rather than the single
    snapshot it used to be -- see the restore for how the old shape survives.
    """
    runtimes = _fib_boundary_engines.get(int(user_id), {})
    if not runtimes:
        return
    now = time.time()
    if not force and now - _fib_boundary_open_state_last_save[int(user_id)] < _CASCADE_OPEN_STATE_SAVE_INTERVAL_SEC:
        return
    payload = {
        "campaigns": [
            {
                "running": bool(runtime.running),
                "last_candle_timestamp": runtime.last_candle_timestamp.isoformat(),
                "engine": runtime.engine.to_dict(),
            }
            for _symbol, runtime in sorted(runtimes.items())
        ],
        "saved_at": datetime.now(IST).isoformat(),
    }
    await _db_mod.set_app_state(_fib_boundary_open_state_key(user_id), json.dumps(payload, default=str))
    _fib_boundary_open_state_last_save[int(user_id)] = now


async def _restore_candle_entry_open_state(
    user_id: int, broker: DhanClient | None, *, activate: bool = True
) -> _CascadeRuntime | None:
    existing = _candle_entry_engines.get(int(user_id))
    if existing is not None:
        return existing
    if broker is None:
        return None
    raw = await _db_mod.get_app_state(_candle_entry_open_state_key(user_id))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        adapter = CascadeOptionsAdapter(broker, paper_only=True)
        engine = LadderCandleEntryPaper.from_dict(
            payload["engine"], adapter=adapter, option_premium_lookup=_cascade_premium_lookup(broker)
        )
        last = datetime.fromisoformat(str(payload.get("last_candle_timestamp") or "").replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=IST)
        running = bool(payload.get("running")) and engine.status not in {"CLOSED", "EXPIRED", "KILLED"}
        runtime = _CascadeRuntime(engine, adapter, broker, last, running=running)
        _candle_entry_engines[int(user_id)] = runtime
        if running and activate and _engine_restore_owner_is_active_instance():
            runtime.task = asyncio.create_task(_run_candle_entry_paper_loop(int(user_id), runtime))
        return runtime
    except Exception as exc:
        _logger.warning("[CANDLE ENTRY] Skipping invalid persisted campaign for user %s: %s", user_id, exc)
        return None


async def _restore_fib_boundary_open_state(
    user_id: int, broker: DhanClient | None, *, activate: bool = True
) -> Dict[str, _CascadeRuntime]:
    """Bring every ladder back after a restart, all of them UNARMED.

    A deploy restarts this process, and a restart is not a person deciding to
    trade real money.  So a live ladder resumes with its executor closed and the
    console showing LIVE / NOT ARMED again -- the gated arm route is the only
    way back to sending, exactly as it was the first time.

    One malformed campaign is skipped on its own; it does not cost the user the
    other ladders in the same row.
    """
    existing = _fib_boundary_engines.get(int(user_id))
    if existing is not None:
        return existing
    if broker is None:
        return {}
    raw = await _db_mod.get_app_state(_fib_boundary_open_state_key(user_id))
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {}
    except Exception as exc:
        _logger.warning("[FIB TOUCH] Unreadable persisted ladders for user %s: %s", user_id, exc)
        return {}
    # A row written before ladders were per-symbol holds ONE snapshot at the top
    # level. Read it as a one-entry list so a ladder in flight survives the
    # upgrade; from the next save on it is always the `campaigns` shape.
    records = payload.get("campaigns")
    if not isinstance(records, list):
        records = [payload] if payload.get("engine") else []

    runtimes: Dict[str, _CascadeRuntime] = {}
    for record in records:
        if not isinstance(record, dict) or not record.get("engine"):
            continue
        try:
            engine_state = record["engine"]
            if int(engine_state.get("version") or 0) != 1:
                # A snapshot from the retired typed-mother engine. Left in place
                # rather than guessed at; the ladder simply starts fresh.
                continue
            symbol = str(engine_state["config"]["symbol"])
            adapter = CascadeOptionsAdapter(broker, paper_only=True)
            executor = (
                _FibTouchLiveExecutor(broker, symbol)
                if str(engine_state.get("mode")) == "live"
                else _FibTouchPaperExecutor()
            )
            mother_day = datetime.fromisoformat(engine_state["config"]["mother_timestamp"]).date()
            # Same rule as the start route: only an old mother pays for the
            # blocking Upstox construction.
            history = (
                _fib_touch_history_lookup(broker, symbol, mother_day, datetime.now(IST).date())
                if mother_day != datetime.now(IST).date()
                else None
            )
            engine = FibTouchLadder.from_dict(
                engine_state,
                premium_lookup=_fib_touch_premium_lookup(broker, symbol, history),
                expiry_source=_fib_touch_expiry_source(broker, symbol),
                executor=executor,
            )
            last = datetime.fromisoformat(str(record.get("last_candle_timestamp") or "").replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=IST)
            running = bool(record.get("running")) and engine.status not in {"CLOSED", "EXPIRED", "KILLED"}
            runtimes[symbol] = _CascadeRuntime(engine, adapter, broker, last, running=running)
        except Exception as exc:
            _logger.warning("[FIB TOUCH] Skipping invalid persisted ladder for user %s: %s", user_id, exc)
    if not runtimes:
        return {}
    _fib_boundary_engines[int(user_id)] = runtimes
    if activate and _engine_restore_owner_is_active_instance():
        for runtime in runtimes.values():
            if runtime.running:
                runtime.task = asyncio.create_task(_run_fib_boundary_paper_loop(int(user_id), runtime))
    return runtimes


async def _run_cascade_paper_loop(user_id: int, runtime: _CascadeRuntime) -> None:
    """Poll closed NIFTY 5m bars; all CE orders remain in-memory paper records."""

    while runtime.running and _cascade_engines.get(int(user_id)) is runtime:
        try:
            today = datetime.now(IST).date()
            campaign_start = runtime.engine.geometry.history[0].timestamp.date()
            candles = await runtime.adapter.async_get_candles("NIFTY", "5m", from_date=campaign_start, to_date=today)
            for candle in candles:
                if candle.timestamp <= runtime.last_candle_timestamp:
                    continue
                runtime.last_candle_timestamp = candle.timestamp
                runtime.engine.on_candle(candle)
                await _save_cascade_open_state(user_id, runtime)
                await _notify_cascade_ws(user_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.warning("[CASCADE] NIFTY paper poll failed for user %s: %s", user_id, exc)
        await asyncio.sleep(12)


# ── Candle-entry recovery run ─────────────────────────────────────
# Phil's stop-loss recovery rules (engine/candle_recovery.py), driven forward in
# paper.  Two reds where the second closes below the first's LOW, buy the
# recovery at the second red's HIGH, stop on a CLOSE below the entry candle's
# low, and repeat -- every later entry having to break lower -- until one trade
# nets back every booked loss plus a margin.  Intraday: positions square off at
# the day's last bar, only the ledger carries.
#
# Two modes, both measured (tools/candle_recovery_sweep.py, Jan-Jul 2026):
#   ladder    every stop re-arms; needs ~10 sessions to pay (15m: +Rs 82,896)
#   fib-zone  entries only in the 2-2 and 4-4 zones between the buyer and seller
#             fibs, two per campaign (5m intraday: +Rs 6,732, worst -Rs 3,562)
# Paper only: CascadeOptionsAdapter is constructed paper_only and refuses
# otherwise, and no route here can reach a Dhan order path.

RECOVERY_POLL_SECONDS = 30
RECOVERY_SYMBOLS = {
    "nifty": dict(dhan_symbol="NIFTY", strike_step=50, itm_steps=2, min_dte=4, max_dte=45),
    "banknifty": dict(dhan_symbol="BANKNIFTY", strike_step=100, itm_steps=2, min_dte=4, max_dte=45),
}
RECOVERY_TIMEFRAMES = ("1m", "5m", "15m", "1h")


@dataclass
class _RecoveryRuntime:
    host: CandleRecoveryHost
    adapter: CascadeOptionsAdapter
    broker: DhanClient
    symbol: str
    started_at: datetime
    task: asyncio.Task | None = None
    running: bool = True
    last_error: str | None = None


_recovery_engines: Dict[int, _RecoveryRuntime] = {}


def _recovery_state_key(user_id: int) -> str:
    return f"candle_recovery:{int(user_id)}"


def _build_recovery_host(
    symbol: str,
    adapter: CascadeOptionsAdapter,
    broker: DhanClient,
    *,
    timeframe: str,
    mode: str,
    config_overrides: dict | None = None,
) -> CandleRecoveryHost:
    """Wire the measured rules to the broker's real chain.

    Strike selection goes through the adapter's own `select_campaign_contract`
    at EVERY fill, so a deeper entry buys the contract Dhan actually lists for
    that index level -- real security id, real lot size -- rather than holding
    the strike the campaign opened on.
    """
    terms = RECOVERY_SYMBOLS[symbol]
    quote = _cascade_premium_lookup(broker)

    def select_contract(when: datetime, index_price: float):
        return adapter.select_campaign_contract(
            mother_spot=float(index_price),
            selected_at=when,
            ce_offset_steps=-int(terms["itm_steps"]),
            strike_step=int(terms["strike_step"]),
            option_type="CE",
            symbol=terms["dhan_symbol"],
        )

    def premium_lookup(when: datetime, strike: int, expiry) -> float | None:
        # the lookup keys on .underlying, not .symbol
        return quote(
            when, SimpleNamespace(underlying=terms["dhan_symbol"], strike=int(strike), expiry=expiry, option_type="CE")
        )

    lot_size = int(
        adapter.select_campaign_contract(
            mother_spot=float(adapter.get_ticker(terms["dhan_symbol"])["last_price"]),
            selected_at=datetime.now(IST),
            ce_offset_steps=-int(terms["itm_steps"]),
            strike_step=int(terms["strike_step"]),
            option_type="CE",
            symbol=terms["dhan_symbol"],
        ).lot_size
    )

    overrides = dict(config_overrides or {})
    config = RecoveryConfig(
        timeframe=timeframe,
        lots_schedule=tuple(overrides.get("lots_schedule") or (1, 2)),
        min_profit_inr=float(overrides.get("min_profit_inr", 500.0)),
        sl_source=str(overrides.get("sl_source", "entry")),
        itm_steps=int(terms["itm_steps"]),
        min_dte=int(terms["min_dte"]),
        max_dte=int(terms["max_dte"]),
        horizon_sessions=int(overrides.get("horizon_sessions", 10)),
    )
    return CandleRecoveryHost(
        symbol,
        adapter,
        premium_lookup=premium_lookup,
        select_contract=select_contract,
        config=config,
        mode=mode,
        lot_size=lot_size,
        dhan_symbol=terms["dhan_symbol"],
    )


async def _save_recovery_state(user_id: int, runtime: _RecoveryRuntime) -> None:
    """Persist the run and its NAMED MOTHERS; the campaigns themselves replay.

    A named mother is the one thing the replay cannot rediscover -- the same
    trap that lost the fib-space book its BankNifty campaign every night, so it
    is written on every acceptance, not only when something changed.
    """
    await _db_mod.set_app_state(
        _recovery_state_key(user_id),
        json.dumps(
            {
                "symbol": runtime.symbol,
                "running": bool(runtime.running),
                "started_at": runtime.started_at.isoformat(),
                "timeframe": runtime.host.config.timeframe,
                "mode": runtime.host.mode,
                "config": {
                    "lots_schedule": list(runtime.host.config.lots_schedule),
                    "min_profit_inr": runtime.host.config.min_profit_inr,
                    "sl_source": runtime.host.config.sl_source,
                    "horizon_sessions": runtime.host.config.horizon_sessions,
                },
                "mothers": sorted(c.mother.timestamp.isoformat() for c in runtime.host.campaigns.values()),
            }
        ),
    )


def _recovery_status_payload(runtime: _RecoveryRuntime) -> dict:
    return {
        "status": "ok",
        "mode": "paper",
        "symbol": runtime.symbol,
        "running": runtime.running,
        "started_at": runtime.started_at.isoformat(),
        "last_error": runtime.last_error,
        "poll_seconds": RECOVERY_POLL_SECONDS,
        "book": runtime.host.snapshot(),
    }


async def _readopt_recovery_mothers(user_id: int, host: CandleRecoveryHost, symbol: str) -> int:
    raw = await _db_mod.get_app_state(_recovery_state_key(user_id))
    if not raw:
        return 0
    try:
        saved = json.loads(raw)
    except Exception:
        return 0
    if str(saved.get("symbol") or "").strip().lower() != symbol:
        return 0
    if str(saved.get("timeframe") or "") != host.config.timeframe:
        return 0  # a mother is a bar of ITS timeframe; it does not carry across
    now = datetime.now(IST).replace(tzinfo=None)
    adopted = 0
    for stamp in saved.get("mothers") or []:
        try:
            await host.start_named_mother(datetime.fromisoformat(str(stamp)), now=now)
            adopted += 1
        except Exception as exc:
            _logger.warning("[RECOVERY] Could not re-adopt mother %s for user %s: %s", stamp, user_id, exc)
    return adopted


async def _restore_recovery_run(user_id: int, broker: DhanClient | None) -> _RecoveryRuntime | None:
    if broker is None or _recovery_engines.get(int(user_id)) is not None:
        return None
    raw = await _db_mod.get_app_state(_recovery_state_key(user_id))
    if not raw:
        return None
    try:
        saved = json.loads(raw)
    except Exception:
        return None
    if not saved.get("running"):
        return None
    symbol = str(saved.get("symbol") or "").strip().lower()
    if symbol not in RECOVERY_SYMBOLS:
        return None
    adapter = CascadeOptionsAdapter(broker, paper_only=True)
    try:
        host = _build_recovery_host(
            symbol,
            adapter,
            broker,
            timeframe=str(saved.get("timeframe") or "15m"),
            mode=str(saved.get("mode") or "ladder"),
            config_overrides=saved.get("config") or {},
        )
    except Exception as exc:
        _logger.warning("[RECOVERY] Cannot restore %s run for user %s: %s", symbol, user_id, exc)
        return None
    started_at = datetime.now(IST).replace(tzinfo=None)
    try:
        started_at = datetime.fromisoformat(str(saved.get("started_at")))
    except Exception:
        pass
    runtime = _RecoveryRuntime(host=host, adapter=adapter, broker=broker, symbol=symbol, started_at=started_at)
    _recovery_engines[int(user_id)] = runtime
    await _readopt_recovery_mothers(int(user_id), host, symbol)
    runtime.task = asyncio.create_task(_run_recovery_loop(int(user_id), runtime))
    _logger.info("[RECOVERY] Restored %s run for user %s", symbol, user_id)
    return runtime


async def _run_recovery_loop(user_id: int, runtime: _RecoveryRuntime) -> None:
    """Poll closed bars and replay every campaign.

    The host gates itself on the NSE session, so an out-of-hours tick costs no
    broker call -- the Dhan rate budget is shared with the live engine.
    """
    while runtime.running and _recovery_engines.get(int(user_id)) is runtime:
        try:
            report = await runtime.host.poll(now=datetime.now(IST).replace(tzinfo=None))
            runtime.last_error = report.error
            if report.error:
                _logger.warning("[RECOVERY] %s poll error for user %s: %s", runtime.symbol, user_id, report.error)
            if report.changed:
                for fill in report.fills:
                    _logger.info("[RECOVERY] %s FILL %s", runtime.symbol, fill)
                for exit_ in report.exits:
                    _logger.info("[RECOVERY] %s EXIT %s", runtime.symbol, exit_)
                await _save_recovery_state(user_id, runtime)
                await _broadcast_user_ws_json(
                    int(user_id),
                    {"type": "recovery_status", "recovery": _recovery_status_payload(runtime)},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            runtime.last_error = str(exc)
            _logger.warning("[RECOVERY] poll failed for user %s: %s", user_id, exc)
        await asyncio.sleep(RECOVERY_POLL_SECONDS)


# ── Fib-space paper run ───────────────────────────────────────────
# The converging-fib space design (engine/fib_space_*), driven forward in paper.
# Unlike the campaigns above it takes no typed mother: it finds its own swing
# pivots on the 15m series and runs every one it accepts, which is exactly what
# tools/fib_space_book.py measured.  Nothing here can reach a Dhan order path --
# CascadeOptionsAdapter is constructed paper_only and refuses otherwise.


@dataclass
class _FibSpaceRuntime:
    host: FibSpacePaperHost
    adapter: CascadeOptionsAdapter
    broker: DhanClient
    symbol: str
    started_at: datetime
    task: asyncio.Task | None = None
    running: bool = True
    last_error: str | None = None


_fib_space_engines: Dict[int, _FibSpaceRuntime] = {}


def _fib_space_state_key(user_id: int) -> str:
    return f"fib_space_paper:{int(user_id)}"


def _build_fib_space_host(
    symbol: str, adapter: CascadeOptionsAdapter, broker: DhanClient, *, auto_scan: bool = False
) -> FibSpacePaperHost:
    """Wire the design's contract rules to the broker's real chain.

    Strike selection goes through the adapter's own `select_campaign_contract`,
    so the paper run buys the contract Dhan actually lists -- real security id,
    real lot size -- rather than the flat lot the backtest had to assume.  The
    terms it is asked for (monthly, 15+ DTE, ATM-2) are the measured ones, held
    in FIB_SPACE_SYMBOLS and asserted against the sweep's config in the tests.
    """
    terms = FIB_SPACE_SYMBOLS[symbol]
    quote = _cascade_premium_lookup(broker)

    def select_contract(when: datetime, index_price: float):
        return adapter.select_campaign_contract(
            mother_spot=float(index_price),
            selected_at=when,
            ce_offset_steps=-int(terms["itm_steps"]),
            strike_step=int(terms["strike_step"]),
            option_type="CE",
            symbol=terms["dhan_symbol"],
        )

    # The lot comes from the chain, not from the backtest's assumption.  It is
    # resolved once, now, so every campaign this run starts is sized alike and a
    # mid-run lot revision cannot silently restate earlier quantities.
    lot_size = int(
        adapter.select_campaign_contract(
            mother_spot=float(adapter.get_ticker(terms["dhan_symbol"])["last_price"]),
            selected_at=datetime.now(IST),
            ce_offset_steps=-int(terms["itm_steps"]),
            strike_step=int(terms["strike_step"]),
            option_type="CE",
            symbol=terms["dhan_symbol"],
        ).lot_size
    )

    return FibSpacePaperHost(
        symbol,
        adapter,
        premium_lookup=quote,
        select_contract=select_contract,
        config=SpaceCascadeConfig(lot_size=lot_size),
        entry_timeframe="5m",
        geometry_timeframe="15m",
        cooldown_days=int(terms["cooldown_days"]),
        dhan_symbol=terms["dhan_symbol"],
        auto_scan=auto_scan,
    )


async def _save_fib_space_state(user_id: int, runtime: _FibSpaceRuntime) -> None:
    """Persist enough to describe the run; the campaigns themselves replay.

    Deliberately NOT an engine dump.  The driver rebuilds every decision from
    the bars on the next poll, so the only thing that must survive a restart is
    which symbol was running -- see engine/fib_space_live.py.
    """
    await _db_mod.set_app_state(
        _fib_space_state_key(user_id),
        json.dumps(
            {
                "symbol": runtime.symbol,
                "running": bool(runtime.running),
                "started_at": runtime.started_at.isoformat(),
                # Without this a restart would silently flip a scanning run to
                # manual, and the book would quietly stop finding its own
                # mothers with nothing on screen to say so.
                "auto_scan": bool(runtime.host.auto_scan),
                # Mothers named by hand are the only thing here the driver
                # cannot rediscover by replaying bars, so they are the one piece
                # of campaign state worth persisting.
                "manual_mothers": sorted(
                    c.mother.timestamp.isoformat() for c in runtime.host.book.campaigns.values() if c.source == "manual"
                ),
            }
        ),
    )


async def _readopt_saved_manual_mothers(user_id: int, host, symbol: str) -> int:
    """Re-open the hand-named mothers saved for this symbol.

    A named mother is the one thing the replay cannot rediscover, so it has to
    survive a restart AND a stop/start. Without this, building a fresh host and
    saving its empty book wrote `manual_mothers: []` over the record and every
    mother the trader had named was gone for good.
    """
    raw = await _db_mod.get_app_state(_fib_space_state_key(user_id))
    if not raw:
        return 0
    try:
        saved = json.loads(raw)
    except Exception:
        return 0
    # Mothers belong to the symbol they were named on; starting a run on a
    # different underlying must not drag them across.
    if str(saved.get("symbol") or "").strip().lower() != symbol:
        return 0
    now = datetime.now(IST).replace(tzinfo=None)
    adopted = 0
    for stamp in saved.get("manual_mothers") or []:
        try:
            await host.start_named_mother(datetime.fromisoformat(str(stamp)), now=now)
            adopted += 1
        except Exception as exc:
            _logger.warning("[FIBSPACE] Could not re-adopt manual mother %s for user %s: %s", stamp, user_id, exc)
    return adopted


def _fib_space_status_payload(runtime: _FibSpaceRuntime) -> dict:
    """One shape for the status route and the websocket push.

    The panel renders whichever arrives first, so they must agree -- a push
    carrying only the book snapshot would leave the panel unable to tell a
    running run from a stopped one.
    """
    return {
        "status": "ok",
        "mode": "paper",
        "symbol": runtime.symbol,
        "running": runtime.running,
        "started_at": runtime.started_at.isoformat(),
        "last_error": runtime.last_error,
        "lot_size": runtime.host.book.config.lot_size,
        "book": runtime.host.snapshot(),
    }


async def _restore_fib_space_paper_run(user_id: int, broker: DhanClient | None) -> _FibSpaceRuntime | None:
    """Bring a paper run back after a restart.

    Deploys are frequent, and a run that quietly died on one would leave a gap
    in the record exactly where the design is being judged.  Nothing about the
    campaigns is restored from disk -- the driver rebuilds every decision by
    replaying the bars -- so this only has to know which symbol was going.
    """
    if broker is None or _fib_space_engines.get(int(user_id)) is not None:
        return None
    raw = await _db_mod.get_app_state(_fib_space_state_key(user_id))
    if not raw:
        return None
    try:
        saved = json.loads(raw)
    except Exception:
        return None
    if not saved.get("running"):
        return None
    symbol = str(saved.get("symbol") or "").strip().lower()
    if symbol not in FIB_SPACE_SYMBOLS:
        _logger.warning("[FIBSPACE] Not restoring unknown symbol %r for user %s", symbol, user_id)
        return None

    adapter = CascadeOptionsAdapter(broker, paper_only=True)
    try:
        host = _build_fib_space_host(symbol, adapter, broker, auto_scan=bool(saved.get("auto_scan")))
    except Exception as exc:
        # The chain could not size a campaign right now (often before the day's
        # ScripMaster load). Leave the saved state alone so the next restart can
        # try again, rather than starting a run whose quantities are a guess.
        _logger.warning("[FIBSPACE] Cannot restore %s paper run for user %s: %s", symbol, user_id, exc)
        return None

    started_at = datetime.now(IST).replace(tzinfo=None)
    try:
        started_at = datetime.fromisoformat(str(saved.get("started_at")))
    except Exception:
        pass
    runtime = _FibSpaceRuntime(host=host, adapter=adapter, broker=broker, symbol=symbol, started_at=started_at)
    _fib_space_engines[int(user_id)] = runtime

    # Re-adopt the mothers that were named by hand. A scanned mother comes back
    # on its own from the next poll; a named one exists only because somebody
    # said so, so losing it on a deploy would silently drop a live campaign.
    await _readopt_saved_manual_mothers(int(user_id), host, symbol)

    runtime.task = asyncio.create_task(_run_fib_space_paper_loop(int(user_id), runtime))
    _logger.info("[FIBSPACE] Restored %s paper run for user %s", symbol, user_id)
    return runtime


async def _run_fib_space_paper_loop(user_id: int, runtime: _FibSpaceRuntime) -> None:
    """Poll closed BankNifty bars and advance every live fib-space campaign.

    The host gates itself on the NSE session, so an out-of-hours tick costs no
    broker calls -- the Dhan rate budget is shared with the live engine.
    """
    while runtime.running and _fib_space_engines.get(int(user_id)) is runtime:
        try:
            report = await runtime.host.poll(now=datetime.now(IST).replace(tzinfo=None))
            runtime.last_error = report.error
            if report.error:
                _logger.warning("[FIBSPACE] %s poll error for user %s: %s", runtime.symbol, user_id, report.error)
            if report.changed:
                for fill in report.fills:
                    _logger.info(
                        "[FIBSPACE] %s FILL %s round %s @ index %.2f, %s lots, strike %s, premium %s",
                        runtime.symbol,
                        fill.campaign_id,
                        fill.round_no,
                        fill.index_price,
                        fill.lots,
                        fill.strike,
                        "UNPRICED" if fill.premium is None else f"{fill.premium:.2f}",
                    )
                for exit_ in report.exits:
                    _logger.info(
                        "[FIBSPACE] %s EXIT %s round %s (%s) @ index %.2f",
                        runtime.symbol,
                        exit_.campaign_id,
                        exit_.round_no,
                        exit_.exit_reason,
                        exit_.exit_index,
                    )
                for halted in report.halted:
                    _logger.error("[FIBSPACE] %s campaign HALTED: %s", runtime.symbol, halted)
                await _save_fib_space_state(user_id, runtime)
                await _broadcast_user_ws_json(
                    int(user_id),
                    {"type": "fib_space_status", "fib_space": _fib_space_status_payload(runtime)},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            runtime.last_error = str(exc)
            _logger.warning("[FIBSPACE] paper poll failed for user %s: %s", user_id, exc)
        await asyncio.sleep(FIB_SPACE_POLL_SECONDS)


def _scalp_open_state_key(user_id: int) -> str:
    return f"scalp_open:{int(user_id)}"


def _parse_scalp_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _restore_scalp_trade_from_payload(payload: dict):
    if not _ScalpTradeClass or not isinstance(payload, dict):
        return None
    status = str(payload.get("status") or "open").lower()
    if status == "closed":
        return None
    try:
        lots = max(1, int(float(payload.get("lots") or 1)))
        quantity = int(float(payload.get("quantity") or 0))
        lot_size_raw = payload.get("lot_size") or (quantity / lots if quantity > 0 else 1)
        trade = _ScalpTradeClass(
            trade_id=int(payload.get("trade_id") or 0),
            underlying=str(payload.get("underlying") or "").strip().upper(),
            strike=int(float(payload.get("strike") or 0)),
            option_type=str(payload.get("option_type") or "").strip().upper(),
            expiry=str(payload.get("expiry") or "").strip(),
            transaction_type=str(payload.get("transaction_type") or "BUY").strip().upper(),
            lots=lots,
            lot_size=max(1, int(float(lot_size_raw or 1))),
            entry_premium=float(payload.get("entry_premium") or 0),
            product_type=str(payload.get("product_type") or "INTRADAY"),
            target_premium=float(payload.get("target_premium") or 0),
            sl_premium=float(payload.get("sl_premium") or 0),
            target_pct=float(payload.get("target_pct") or 0),
            sl_pct=float(payload.get("sl_pct") or 0),
            target_rupees=float(payload.get("target_rupees") or 0),
            sl_rupees=float(payload.get("sl_rupees") or 0),
            sqoff_time=str(payload.get("sqoff_time") or ""),
            order_id=str(payload.get("order_id") or ""),
            entry_time=_parse_scalp_datetime(payload.get("entry_time")),
            mode=str(payload.get("mode") or "paper"),
            entry_limit_price=float(payload.get("entry_limit_price") or 0),
            entry_limit_max=float(payload.get("entry_limit_max") or 0),
        )
        trade.current_premium = float(payload.get("current_premium") or trade.entry_premium or 0)
        trade.status = "pending" if status == "pending" else "open"
        for attr in (
            "broker_order_model",
            "super_order_id",
            "super_order_status",
            "super_target_status",
            "super_sl_status",
            "broker_sl_order_id",
            "broker_tp_order_id",
        ):
            if attr in payload:
                setattr(trade, attr, str(payload.get(attr) or ""))
        trade.super_filled_qty = int(float(payload.get("super_filled_qty") or 0))
        return trade
    except Exception as exc:
        print(f"[SCALP] Skipping persisted open trade restore: {exc}")
        return None


async def _restore_scalp_open_state(user_id: int, eng) -> bool:
    if not eng or getattr(eng, "open_trades", None):
        return False
    raw = await _db_mod.get_app_state(_scalp_open_state_key(user_id))
    if not raw:
        return False
    try:
        payload = json.loads(raw)
    except Exception:
        return False
    rows = payload.get("open_trades") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return False

    restored = 0
    for row in rows:
        trade = _restore_scalp_trade_from_payload(row)
        if not trade or trade.trade_id <= 0:
            continue
        eng.open_trades[trade.trade_id] = trade
        eng._trade_counter = max(eng._trade_counter, trade.trade_id)
        restored += 1

    if not restored:
        return False
    if isinstance(payload.get("event_log"), list):
        eng.event_log = payload.get("event_log", [])[-100:]
    if bool(payload.get("running")) and _engine_restore_owner_is_active_instance():
        eng.start()
    print(f"[SCALP] Restored {restored} open scalp trade(s) for user {user_id}")
    return True


async def _save_scalp_open_state(user_id: int, eng, *, force: bool = False) -> None:
    if not eng:
        return
    now = time.time()
    if not force and now - _scalp_open_state_last_save[int(user_id)] < _SCALP_OPEN_STATE_SAVE_INTERVAL_SEC:
        return
    status = eng.get_status()
    payload = {
        "running": bool(status.get("running")),
        "trade_counter": int(getattr(eng, "_trade_counter", 0) or 0),
        "open_trades": status.get("open_trades") or [],
        "event_log": status.get("event_log") or [],
        "saved_at": datetime.now().isoformat(),
    }
    await _db_mod.set_app_state(_scalp_open_state_key(user_id), json.dumps(payload, default=str))
    _scalp_open_state_last_save[int(user_id)] = now


def _startup_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


_STARTUP_TOKEN_ENABLED = _startup_flag("PHILFORGE_STARTUP_TOKEN", True)
_STARTUP_SCRIP_MASTER_ENABLED = _startup_flag("PHILFORGE_STARTUP_SCRIP_MASTER", True)
_STARTUP_TRADE_BACKFILL_ENABLED = _startup_flag("PHILFORGE_STARTUP_TRADE_BACKFILL", True)
_STARTUP_EMPTY_RUN_CLEANUP_ENABLED = _startup_flag("PHILFORGE_STARTUP_EMPTY_RUN_CLEANUP", True)
_STARTUP_ENGINE_RESTORE_ENABLED = _startup_flag("PHILFORGE_STARTUP_ENGINE_RESTORE", True)
_STARTUP_EXAMPLE_SEED_ENABLED = _startup_flag("PHILFORGE_STARTUP_EXAMPLE_SEED", True)


def _engine_restore_owner_is_active_instance() -> bool:
    """Return whether this blue/green worker currently owns engine restore.

    Standby and active workers share engine-state files. Restoring from both
    would create duplicate broker orders, so a template-service standby stays
    passive until cd-deploy has stopped the old worker and handed over.
    Direct/local starts keep the previous restore behaviour.
    """
    instance_port = str(os.getenv("PHILFORGE_INSTANCE_PORT", "") or "").strip()
    if not instance_port:
        return True
    port_file = os.getenv(
        "PHILFORGE_ACTIVE_PORT_FILE",
        os.path.join(os.path.expanduser("~"), ".philforge-active-port"),
    )
    try:
        with open(port_file, encoding="utf-8") as handle:
            return handle.read().strip() == instance_port
    except OSError:
        # A first non-blue/green boot has no ownership file yet.
        return True


def _is_loopback_request(request: Request) -> bool:
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        return False
    # A public request proxied by Nginx reaches Uvicorn from loopback, but
    # carries the original client address in X-Real-IP. Direct local deploy
    # calls have no such header and remain allowed.
    real_ip = str(request.headers.get("x-real-ip", "") or "").strip()
    return not real_ip or real_ip in {"127.0.0.1", "::1"}


async def _restore_auxiliary_engines() -> dict[str, int]:
    """Restore every non-main runtime only on the active blue/green worker."""
    restored = {
        "scalp": 0,
        "cascade": 0,
        "candle_entry": 0,
        "recovery": 0,
        "fib_boundary": 0,
        "terminal_cascade": 0,
        "fib_space": 0,
    }
    if not _engine_restore_owner_is_active_instance():
        return restored
    for user in await _db_mod.list_users():
        user_id = int(user["id"])
        broker_client, _source = _resolve_user_broker_client(user, allow_admin_fallback=True)
        if broker_client is None:
            continue
        try:
            scalp_raw = await _db_mod.get_app_state(_scalp_open_state_key(user_id))
            if scalp_raw:
                scalp = _get_scalp_engine(user_id, broker_client)
                if await _restore_scalp_open_state(user_id, scalp):
                    restored["scalp"] += 1
            if await _restore_cascade_open_state(user_id, broker_client) is not None:
                restored["cascade"] += 1
            if await _restore_candle_entry_open_state(user_id, broker_client, activate=True) is not None:
                restored["candle_entry"] += 1
            fib_ladders = await _restore_fib_boundary_open_state(user_id, broker_client, activate=True)
            restored["fib_boundary"] += len(fib_ladders)
            terminal = await _restore_terminal_cascade_open_state(user_id, broker_client)
            if terminal:
                restored["terminal_cascade"] += len(terminal)
            if await _restore_fib_space_paper_run(user_id, broker_client) is not None:
                restored["fib_space"] += 1
            if await _restore_recovery_run(user_id, broker_client) is not None:
                restored["recovery"] += 1
        except Exception as exc:
            _logger.warning("[Restore] Auxiliary engine restore failed for user %s: %s", user_id, exc)
    return restored


ws_clients: Dict[int, List[WebSocket]] = defaultdict(list)


def _user_ws_clients(user_id: int) -> List[WebSocket]:
    return ws_clients.setdefault(int(user_id), [])


async def _broadcast_user_ws_json(user_id: int, payload: dict):
    for ws in _user_ws_clients(user_id).copy():
        try:
            await ws.send_json(payload)
        except Exception:
            if ws in _user_ws_clients(user_id):
                _user_ws_clients(user_id).remove(ws)


# ── Authentication ────────────────────────────────────────────────
# Legacy PIN kept as fallback for first-run admin bootstrap only
AUTH_PASSWORD = (os.getenv("PHILFORGE_PIN") or os.getenv("PHILFORGE_PASSWORD") or "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_hex(32))
_SESSION_COOKIE_NAME = "philforge_session"

_redis_client = None
_redis_checked = False


def _get_redis():
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    try:
        import redis as _redis_lib

        r = _redis_lib.Redis(host="localhost", port=6379, db=0, decode_responses=True, socket_timeout=1)
        r.ping()
        _redis_client = r
    except Exception:
        _redis_client = None
    return _redis_client


async def _get_preferred_admin_user() -> dict | None:
    """Return the configured admin account, with fallback for legacy installs."""
    return await _db_mod.get_admin_user(config.ADMIN_USERNAME)


def _get_bootstrap_admin_password() -> str:
    """Return the bootstrap admin password for first-run provisioning."""
    return AUTH_PASSWORD


def _request_user_id(request: Request) -> int:
    """Return the authenticated user id from middleware state."""
    user_id = getattr(request.state, "user_id", 0)
    return int(user_id or 0)


async def _resolve_history_user_id(explicit_user_id: int | None = None, source: dict | None = None) -> int:
    """Resolve a run-history owner from request context, engine state, or admin fallback."""
    candidates: list[object] = [explicit_user_id]
    if isinstance(source, dict):
        candidates.append(source.get("_user_id"))
        candidates.append(source.get("user_id"))
        strategy = source.get("strategy")
        if isinstance(strategy, dict):
            candidates.append(strategy.get("_user_id"))
            candidates.append(strategy.get("user_id"))
    for candidate in candidates:
        try:
            if candidate is not None and str(candidate).strip():
                return int(candidate)
        except (TypeError, ValueError):
            continue
    admin = await _get_preferred_admin_user()
    if admin:
        return int(admin["id"])
    raise RuntimeError("No user context available for run history persistence")


def _default_history_user_id_sync() -> int:
    """Resolve the admin user id for sync startup/backfill helpers."""
    admin = _db_mod.get_admin_user_sync(config.ADMIN_USERNAME)
    if admin:
        return int(admin["id"])
    raise RuntimeError("No admin user available for trade-history persistence")


def _user_broker_credentials(user: dict | None) -> tuple[str, str, str, str]:
    if not user:
        return "", "", "", ""
    return (
        str(user.get("dhan_client_id", "") or "").strip(),
        str(user.get("dhan_access_token", "") or "").strip(),
        str(user.get("dhan_pin", "") or "").strip(),
        str(user.get("dhan_totp_secret", "") or "").strip(),
    )


def _user_broker_fields(user: dict | None) -> tuple[str, str]:
    client_id, access_token, _, _ = _user_broker_credentials(user)
    return client_id, access_token


def _user_broker_auto_refresh_ready(user: dict | None) -> bool:
    client_id, access_token, pin, totp = _user_broker_credentials(user)
    return bool(client_id and access_token and pin and totp)


def _persist_user_access_token_sync(user_id: int, access_token: str) -> None:
    token = str(access_token or "").strip()
    if not token:
        return
    _db_mod.update_user_sync(int(user_id), dhan_access_token=token)


def _broker_not_configured_message(user: dict | None, source: str) -> str:
    if source == "partial":
        return "Broker credentials are incomplete for this user. Add both Client ID and Access Token."
    if user and user.get("role") == "admin":
        return "Dhan API credentials not configured. Add user broker credentials or keep the admin .env fallback configured."
    return "Broker credentials are not configured for this user."


def _broker_order_failure_detail(exc: Exception, fallback_message: str = "Order failed") -> dict[str, Any]:
    reason = str(getattr(exc, "reason", "") or exc or fallback_message).strip()
    detail: dict[str, Any] = {
        "message": fallback_message,
        "reason": reason,
        "error": str(exc),
    }
    status_code = getattr(exc, "status_code", None)
    if status_code:
        detail["broker_status_code"] = status_code
    payload = getattr(exc, "payload", None)
    if isinstance(payload, dict):
        detail["broker_response"] = payload
    return detail


def _looks_like_broker_auth_error(message: str) -> bool:
    text = str(message or "").lower()
    return any(
        part in text
        for part in (
            "authentication failed",
            "invalid token",
            "dh-906",
            "unauthorized",
            "api returned 400",
        )
    )


def _market_probe_has_instruments(payload) -> bool:
    """Return True when a market-feed payload contains at least one instrument quote."""
    if isinstance(payload, dict):
        if any(key in payload for key in ("last_price", "ltp", "LTP", "ohlc")):
            return True
        return any(_market_probe_has_instruments(value) for value in payload.values())
    if isinstance(payload, list):
        if not payload:
            return False
        return any(_market_probe_has_instruments(value) for value in payload)
    return payload not in (None, "", 0, 0.0, False)


def _probe_market_data_connection(broker_client: DhanClient) -> bool:
    """Check whether market-data APIs are reachable without treating empty probe data as fatal."""
    probe_segments = {"IDX_I": [13]}
    probe_calls = (
        lambda: broker_client.get_ltp_multi(probe_segments),
        lambda: broker_client.get_ohlc_multi(probe_segments),
    )
    saw_empty_payload = False
    last_non_auth_error = None

    for probe_call in probe_calls:
        try:
            payload = probe_call()
        except Exception as exc:
            if _looks_like_broker_auth_error(str(exc)):
                raise
            last_non_auth_error = exc
            continue
        if _market_probe_has_instruments(payload):
            return True
        saw_empty_payload = True

    if saw_empty_payload and last_non_auth_error is None:
        return False
    if last_non_auth_error is not None:
        raise last_non_auth_error
    return False


def _mask_value(value: str, *, prefix: int = 3, suffix: int = 2) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= prefix + suffix:
        return "•" * len(text)
    return f"{text[:prefix]}{'•' * max(4, len(text) - (prefix + suffix))}{text[-suffix:]}"


_PUBLIC_IP_CACHE: dict[str, object] = {"value": "", "error": "", "expires": 0.0}
_DHAN_IP_CACHE: dict[str, dict[str, object]] = {}
_PUBLIC_IP_CACHE_TTL_SEC = 120.0
_DHAN_IP_CACHE_TTL_SEC = 90.0
_IP_TEXT_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _extract_ip_from_text(value: str) -> str:
    match = _IP_TEXT_RE.search(str(value or ""))
    return match.group(0) if match else ""


def _clean_ip_literal(value: str) -> str:
    text = str(value or "").strip()
    if not text or text.upper() == "NA":
        return ""
    return text


def _extract_ip_from_payload(payload) -> str:
    if isinstance(payload, str):
        return _extract_ip_from_text(payload)
    if isinstance(payload, dict):
        preferred_keys = (
            "primaryIP",
            "primaryIp",
            "ip",
            "savedIp",
            "saved_ip",
            "whitelistedIp",
            "whitelistedIP",
            "staticIp",
            "staticIP",
            "publicIp",
            "publicIP",
            "userIp",
            "userIP",
            "egressIp",
            "egressIP",
        )
        for key in preferred_keys:
            value = payload.get(key)
            ip = _extract_ip_from_payload(value)
            if ip:
                return ip
        for value in payload.values():
            ip = _extract_ip_from_payload(value)
            if ip:
                return ip
        return ""
    if isinstance(payload, list):
        for item in payload:
            ip = _extract_ip_from_payload(item)
            if ip:
                return ip
    return ""


def _execution_ip_source_label(source: str) -> str:
    if source == "user":
        return "Stored per-user broker account"
    if source == "global":
        return "Admin server fallback (.env)"
    if source == "partial":
        return "Partial broker credentials"
    return "No active broker source"


def _get_server_public_ip() -> tuple[str, str]:
    now = time.time()
    expires = float(_PUBLIC_IP_CACHE.get("expires") or 0.0)
    if expires > now:
        return str(_PUBLIC_IP_CACHE.get("value") or ""), str(_PUBLIC_IP_CACHE.get("error") or "")

    providers = (
        ("https://api.ipify.org?format=json", "json"),
        ("https://checkip.amazonaws.com", "text"),
        ("https://ifconfig.me/ip", "text"),
    )
    errors: list[str] = []
    for url, mode in providers:
        try:
            resp = requests.get(url, timeout=4)
            resp.raise_for_status()
            if mode == "json":
                ip = _extract_ip_from_payload(resp.json())
            else:
                ip = _extract_ip_from_text(resp.text)
            if ip:
                _PUBLIC_IP_CACHE.update({"value": ip, "error": "", "expires": now + _PUBLIC_IP_CACHE_TTL_SEC})
                return ip, ""
        except Exception as exc:
            errors.append(str(exc))
    error = errors[-1] if errors else "Unable to determine server public IP"
    _PUBLIC_IP_CACHE.update({"value": "", "error": error, "expires": now + 20.0})
    return "", error


def _get_cached_dhan_saved_ip_payload(broker_client: DhanClient) -> tuple[dict, str]:
    client_key = str(getattr(broker_client, "client_id", "") or "").strip() or "unknown"
    cache_entry = _DHAN_IP_CACHE.get(client_key) or {}
    now = time.time()
    if float(cache_entry.get("expires") or 0.0) > now:
        return dict(cache_entry.get("payload") or {}), str(cache_entry.get("error") or "")

    payload: dict = {}
    error = ""
    try:
        raw_payload = broker_client.get_whitelisted_ip()
        payload = raw_payload if isinstance(raw_payload, dict) else {"data": raw_payload}
    except Exception as exc:
        error = str(exc)
    _DHAN_IP_CACHE[client_key] = {
        "payload": payload,
        "error": error,
        "expires": now + (_DHAN_IP_CACHE_TTL_SEC if not error else 20.0),
    }
    return payload, error


def _build_execution_ip_status(user: dict | None, broker_client: DhanClient | None, source: str) -> dict:
    client_id = str(getattr(broker_client, "client_id", "") or "").strip()
    access_token = str(getattr(broker_client, "access_token", "") or "").strip()
    status = {
        "source": source,
        "source_label": _execution_ip_source_label(source),
        "client_id_masked": _mask_value(client_id),
        "check_ready": bool(broker_client and client_id and access_token),
        "server_public_ip": "",
        "dhan_saved_ip": "",
        "dhan_detected_ip": "",
        "dhan_ip_match_status": "",
        "orders_allowed": None,
        "match": False,
        "warning": "",
        "error": "",
        "checked_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
    }

    if not status["check_ready"]:
        if source == "partial":
            status["error"] = "Save both Dhan Client ID and Access Token before checking the static IP."
        elif source == "missing":
            status["error"] = "No broker account is active for this user right now."
        else:
            status["error"] = "Broker credentials are not ready for IP verification."
        return status

    server_public_ip, server_error = _get_server_public_ip()
    status["server_public_ip"] = server_public_ip
    if server_error:
        status["warning"] = server_error

    dhan_payload, dhan_error = _get_cached_dhan_saved_ip_payload(broker_client)
    status["dhan_saved_ip"] = _clean_ip_literal(
        dhan_payload.get("primaryIP") or dhan_payload.get("savedIp") or _extract_ip_from_payload(dhan_payload)
    )
    status["dhan_detected_ip"] = _clean_ip_literal(dhan_payload.get("detectedIP"))
    status["dhan_ip_match_status"] = str(dhan_payload.get("ipMatchStatus") or "").strip().upper()
    if "ordersAllowed" in dhan_payload:
        try:
            status["orders_allowed"] = bool(dhan_payload.get("ordersAllowed"))
        except Exception:
            status["orders_allowed"] = None
    if dhan_error:
        status["error"] = dhan_error
    elif not status["dhan_saved_ip"]:
        status["error"] = "Dhan did not return a whitelisted IP for this account."

    if status["dhan_ip_match_status"]:
        status["match"] = status["dhan_ip_match_status"] == "MATCH" and status["orders_allowed"] is not False
    else:
        status["match"] = bool(
            status["server_public_ip"]
            and status["dhan_saved_ip"]
            and status["server_public_ip"] == status["dhan_saved_ip"]
        )
    if not status["match"] and not status["error"]:
        if (
            status["dhan_detected_ip"]
            and status["dhan_saved_ip"]
            and status["dhan_detected_ip"] != status["dhan_saved_ip"]
        ):
            status["warning"] = (
                f"Dhan currently detects outbound IP {status['dhan_detected_ip']}, "
                f"not the saved static IP {status['dhan_saved_ip']}."
            )
        elif status["server_public_ip"] and status["dhan_saved_ip"]:
            status["warning"] = "Server public IP and Dhan static IP do not match yet."
    return status


def _trade_mode_value(trade) -> str:
    if isinstance(trade, dict):
        return str(trade.get("mode", "") or "").lower()
    return str(getattr(trade, "mode", "") or "").lower()


def _user_broker_settings_lock(user_id: int) -> tuple[bool, str]:
    if _any_running(live_engines, user_id):
        return True, "Stop live strategies before editing broker credentials."
    eng = _scalp_engines.get(int(user_id))
    if eng:
        live_scalp_open = any(_trade_mode_value(trade) == "live" for trade in getattr(eng, "open_trades", {}).values())
        if live_scalp_open:
            return True, "Close live scalp trades before editing broker credentials."
    return False, ""


def _broker_profile_payload(user: dict | None) -> dict:
    client_id, access_token, pin, totp = _user_broker_credentials(user)
    _, source = _resolve_user_broker_client(user)
    locked, lock_reason = _user_broker_settings_lock(int(user["id"])) if user else (False, "")
    return {
        "configured": bool(client_id and access_token),
        "partial": bool((client_id and not access_token) or (access_token and not client_id)),
        "source": source,
        "client_id": client_id,
        "client_id_masked": _mask_value(client_id),
        "access_token_saved": bool(access_token),
        "pin_saved": bool(pin),
        "totp_saved": bool(totp),
        "auto_refresh_ready": bool(client_id and access_token and pin and totp),
        "encryption_ready": bool(config.ENCRYPTION_KEY),
        "manage_locked": locked,
        "manage_lock_reason": lock_reason,
    }


def _resolve_user_broker_client(
    user: dict | None,
    *,
    allow_admin_fallback: bool = True,
) -> tuple[DhanClient | None, str]:
    client_id, access_token, pin, totp = _user_broker_credentials(user)
    if client_id and access_token:
        token_update_cb = None
        if user and user.get("id"):
            user_id = int(user["id"])

            def _token_update_cb(new_token: str, *, _user_id: int = user_id) -> None:
                _persist_user_access_token_sync(_user_id, new_token)

            token_update_cb = _token_update_cb
        return (
            DhanClient(
                client_id=client_id,
                access_token=access_token,
                pin=pin,
                totp_secret=totp,
                token_update_cb=token_update_cb,
            ),
            "user",
        )
    if client_id or access_token:
        return None, "partial"
    if allow_admin_fallback and user and user.get("role") == "admin" and dhan._is_configured():
        return dhan, "global"
    return None, "missing"


async def _request_broker_context(
    request: Request,
    *,
    allow_admin_fallback: bool = True,
) -> tuple[dict, DhanClient | None, str]:
    user = getattr(request.state, "current_user", None)
    if not user:
        user = await _auth_mod.get_current_user(request)
    broker_client, source = _resolve_user_broker_client(user, allow_admin_fallback=allow_admin_fallback)
    return user, broker_client, source


def _engine_status_summary(engine, run_id: str, default_mode: str) -> dict:
    try:
        status = engine.get_status() or {}
    except Exception:
        status = {}
    return {
        "run_id": run_id,
        "mode": status.get("mode") or default_mode,
        "strategy_name": status.get("strategy_name") or run_id,
        "instrument": status.get("instrument") or "",
        "in_trade": bool(status.get("in_trade")),
        "trades_today": int(status.get("trades_today") or 0),
        "total_pnl": float(status.get("total_pnl") or 0),
    }


def _live_exit_reason_for_stop(reason: str) -> str:
    normalized = str(reason or "").strip().upper()
    if normalized in {"EMERGENCY_STOP", "ENGINE_REPLACE"}:
        return normalized
    return "ENGINE_STOP"


async def _live_engine_broadcast(user_id: int, run_id: str, event: dict):
    await _broadcast_user_ws_json(user_id, {"source": "live", "run_id": run_id, **event})
    _check_trade_alerts(run_id, "Auto", event, user_id=user_id)
    if event.get("type") == "exit" and event.get("trade"):
        await _save_single_trade_to_history(event["trade"], "live", run_name=run_id, explicit_user_id=user_id)


async def _square_off_live_engine_positions(engine, user_id: int, run_id: str, *, reason: str = "ENGINE_STOP") -> dict:
    """Attempt broker-aware square-off for all open live positions.

    Returns a summary without stopping the engine when exits cannot be confirmed.
    """

    async def broadcast(event: dict):
        await _live_engine_broadcast(user_id, run_id, event)

    try:
        await engine._reconcile_broker_positions(callback=broadcast)
    except Exception:
        pass

    positions = list(getattr(engine, "positions", []) or [])
    if not positions:
        return {"status": "ok", "ok": True, "attempted": 0, "remaining": 0, "results": []}

    results = []
    exit_reason = _live_exit_reason_for_stop(reason)
    for pos in positions:
        current_premium = pos.get("current_premium", pos.get("entry_premium", 0))
        result = await engine._exit_position(pos, exit_reason, current_premium, callback=broadcast)
        results.append(
            {
                "symbol": pos.get("trading_symbol", pos.get("symbol", "")),
                "leg_num": pos.get("leg_num"),
                "status": str((result or {}).get("status") or "error").lower(),
                "message": str((result or {}).get("message") or ""),
            }
        )

    reconcile_error = ""
    try:
        await engine._reconcile_broker_positions(callback=broadcast)
    except Exception as exc:
        reconcile_error = str(exc)

    remaining_positions = list(getattr(engine, "positions", []) or [])
    remaining = len(remaining_positions)
    pending = any(item["status"] == "pending" for item in results)
    errored = any(item["status"] == "error" for item in results) or bool(reconcile_error)
    if remaining <= 0:
        status = "ok"
    elif pending:
        status = "pending"
    else:
        status = "error" if errored else "pending"
    summary = {
        "status": status,
        "ok": remaining <= 0,
        "attempted": len(positions),
        "remaining": remaining,
        "results": results,
    }
    if reconcile_error:
        summary["reconcile_error"] = reconcile_error
    return summary


async def _square_off_scalp_engine_trades(eng) -> dict:
    """Attempt to close all open scalp trades without stopping the engine on failure."""
    open_before = len(getattr(eng, "open_trades", {}) or {})
    if open_before <= 0:
        return {"status": "ok", "ok": True, "attempted": 0, "remaining": 0, "closed": 0}

    result = await eng.kill_all_trades()
    remaining = len(getattr(eng, "open_trades", {}) or {})
    status = "ok" if remaining <= 0 else "error"
    summary = {
        "status": status,
        "ok": remaining <= 0,
        "attempted": open_before,
        "closed": int((result or {}).get("closed", 0) or 0),
        "remaining": remaining,
        "message": str((result or {}).get("message") or ""),
    }
    _notify_scalp_ws()
    return summary


# ── DB-backed session helpers (thin wrappers for sync-style code paths) ──
# These bridge the old middleware (sync-ish) to the async DB via asyncio


async def _validate_session_async(token: str) -> dict | None:
    """Validate session token via DB. Returns session dict or None."""
    return await _auth_mod.validate_session(token)


def _get_session_token(request: Request) -> str:
    """Extract session token from cookie or Authorization header."""
    return _auth_mod.get_session_token(request)


def _clear_session_cookie(response) -> None:
    response.delete_cookie(_SESSION_COOKIE_NAME)


async def _get_page_user(request: Request) -> dict | None:
    """Resolve the logged-in user for HTML page routes, treating disabled users as logged out."""
    token = _get_session_token(request)
    session = await _validate_session_async(token)
    if not session:
        return None
    user = await _db_mod.get_user_by_id(session["user_id"])
    if not user or not user["is_active"]:
        if user:
            await _db_mod.delete_sessions_for_user(user["id"])
        elif token:
            await _db_mod.delete_session(token)
        return None
    return user


def _request_is_https(request: Request) -> bool:
    proto = str(request.headers.get("x-forwarded-proto", "") or "").split(",")[0].strip().lower()
    if proto == "https":
        return True
    forwarded = str(request.headers.get("forwarded", "") or "").lower()
    if "proto=https" in forwarded:
        return True
    return str(getattr(request.url, "scheme", "") or "").lower() == "https"


def _normalize_origin_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = _urlparse(text)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _allowed_request_origins(request: Request) -> set[str]:
    allowed = {_normalize_origin_value(origin) for origin in _CORS_ALLOWED_ORIGINS}
    allowed.discard("")
    host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or "").strip().lower()
    if host:
        allowed.add(f"https://{host}")
        allowed.add(f"http://{host}")
        if _request_is_https(request):
            allowed.add(f"https://{host}")
        else:
            allowed.add(f"http://{host}")
    return allowed


def _browser_origin_allowed(request: Request) -> bool:
    sec_fetch_site = str(request.headers.get("sec-fetch-site", "") or "").strip().lower()
    if sec_fetch_site and sec_fetch_site not in ("same-origin", "same-site", "none"):
        return False

    allowed = _allowed_request_origins(request)
    origin = _normalize_origin_value(request.headers.get("origin", ""))
    if origin:
        return origin in allowed

    referer = _normalize_origin_value(request.headers.get("referer", ""))
    if referer:
        return referer in allowed

    # Non-browser/API clients commonly omit Origin and Referer.
    return True


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Attach a unique request-id to every request for log tracing."""
    import uuid

    rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


@app.middleware("http")
async def browser_origin_guard_middleware(request: Request, call_next):
    """Best-effort CSRF/origin guard for browser-driven mutating requests."""
    path = request.url.path
    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return await call_next(request)
    if path == "/api/save-state":
        return await call_next(request)
    if not path.startswith("/api/"):
        return await call_next(request)
    if not _browser_origin_allowed(request):
        return JSONResponse(status_code=403, content={"detail": "Blocked cross-origin request"})
    return await call_next(request)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Global auth — all routes require login unless whitelisted."""
    path = request.url.path
    if path.startswith("/static/notebooklm"):
        return PlainTextResponse("Not found", status_code=404)

    protected_public_paths = path == "/api/study-library" or path.startswith("/study-assets/")

    # Allow login, health, static, and WebSocket without auth
    if not protected_public_paths and path in (
        "/api/auth/login",
        "/api/auth/status",
        "/api/health",
        "/api/save-state",
        "/api/restore-engines",
        "/login",
        # "/" is the public landing page. "/app" is the terminal, and it is
        # listed here for the same reason "/" used to be: the route does its
        # own session check and renders the LOGIN PAGE when there is no user.
        # Without this the middleware answers a browser navigation with a JSON
        # 401 and nobody can reach the login form.
        "/",
        "/app",
        # Public marketing page, same as "/". Without this the middleware
        # answers a browser navigation with a JSON 401.
        "/equities",
        "/equities/",
        "/charts-viewer",
        "/market-movers",
        "/study-lounge",
        "/logo.jpg",
        "/logo.png",
        "/favicon.ico",
        "/manifest.webmanifest",
        "/site.webmanifest",
        "/sw.js",
        "/apple-touch-icon.png",
        "/robots.txt",
        "/sitemap.xml",
    ):
        return await call_next(request)
    if not protected_public_paths and (path.startswith("/static") or path.startswith("/ws")):
        return await call_next(request)
    # Admin routes have their own Depends() guard, but still need basic session check
    token = _get_session_token(request)
    session = await _validate_session_async(token)
    if not session:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    user = await _db_mod.get_user_by_id(session["user_id"])
    if not user or not user["is_active"]:
        if user:
            await _db_mod.delete_sessions_for_user(user["id"])
        elif token:
            await _db_mod.delete_session(token)
        response = JSONResponse(status_code=401, content={"detail": "Account disabled or not found"})
        _clear_session_cookie(response)
        return response
    # Stash current user on request state to avoid repeated lookups downstream
    request.state.user_id = user["id"]
    request.state.current_user = user
    action_class = _auth_mod.classify_sensitive_action(request.method, path)
    if action_class:
        if not bool(user.get("mfa_enabled")):
            return JSONResponse(
                status_code=428,
                content={
                    "detail": "Set up an authenticator in Account Settings before this protected action.",
                    "code": "mfa_enrollment_required",
                    "action_class": action_class,
                    "target_method": request.method.upper(),
                    "target_path": path,
                    "mfa_enrolled": False,
                },
            )
        action_token = str(request.headers.get("X-PhilForge-Action-Token", "") or "")
        if not action_token:
            return JSONResponse(
                status_code=428,
                content={
                    "detail": "Confirm your password and authenticator code to continue.",
                    "code": "action_authorization_required",
                    "action_class": action_class,
                    "target_method": request.method.upper(),
                    "target_path": path,
                    "mfa_enrolled": True,
                },
            )
        authorized = await _auth_mod.consume_action_authorization(
            token=action_token,
            user_id=int(user["id"]),
            session_token=token,
            action_class=action_class,
            method=request.method,
            path=path,
        )
        if not authorized:
            _logger.warning(
                "[Auth] Rejected action authorization user_id=%s class=%s method=%s request_id=%s",
                user["id"],
                action_class,
                request.method.upper(),
                getattr(request.state, "request_id", ""),
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "This one-time action authorization is invalid, expired, or already used.",
                    "code": "invalid_action_authorization",
                },
            )
        _logger.info(
            "[Auth] Consumed action authorization user_id=%s class=%s method=%s request_id=%s",
            user["id"],
            action_class,
            request.method.upper(),
            getattr(request.state, "request_id", ""),
        )
    return await call_next(request)


@app.middleware("http")
async def privacy_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet, noimageindex"
    if request.url.path == "/robots.txt":
        response.headers["Cache-Control"] = "public, max-age=3600"
    elif request.url.path == "/api/health":
        response.headers.setdefault("Cache-Control", "no-store")
    else:
        response.headers.setdefault("Cache-Control", "private, no-store, max-age=0")
    return response


# ── Rate Limiting ─────────────────────────────────────────────────
_rate_limits: dict = defaultdict(list)  # "endpoint:ip" -> [timestamps] (fallback)
_RL_PREFIX = "philforge:rl:"


def check_rate_limit(endpoint: str, client_ip: str = "global", max_calls: int = 5, window_sec: int = 10):
    """Per-IP rate limiter — Redis sliding window when available, in-memory fallback."""
    key = f"{_RL_PREFIX}{endpoint}:{client_ip}"
    r = _get_redis()
    if r is not None:
        try:
            now_ms = int(time.time() * 1000)
            pipe = r.pipeline()
            pipe.zremrangebyscore(key, 0, now_ms - window_sec * 1000)
            pipe.zcard(key)
            pipe.zadd(key, {f"{now_ms}:{secrets.token_hex(8)}": now_ms})
            pipe.expire(key, window_sec + 1)
            _, count, *_ = pipe.execute()
            if count >= max_calls:
                raise HTTPException(
                    status_code=429, detail=f"Rate limit exceeded. Max {max_calls} calls per {window_sec}s."
                )
            return
        except HTTPException:
            raise
        except Exception as e:
            _logger.warning(f"[Redis] check_rate_limit failed, using in-memory: {e}")
    # In-memory fallback (bounded to 50k keys)
    now = time.time()
    mem_key = f"{endpoint}:{client_ip}"
    calls = _rate_limits[mem_key]
    _rate_limits[mem_key] = [t for t in calls if now - t < window_sec]
    if len(_rate_limits[mem_key]) >= max_calls:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded. Max {max_calls} calls per {window_sec}s.")
    _rate_limits[mem_key].append(now)
    if len(_rate_limits) > 50_000:
        stale = [k for k, v in _rate_limits.items() if not v or now - v[-1] > window_sec]
        for k in stale[:5_000]:
            del _rate_limits[k]


# ── Models ────────────────────────────────────────────────────────
class BacktestRequest(BaseModel):
    from_date: str = config.DEFAULT_FROM
    to_date: str = config.DEFAULT_TO
    symbol: str = "NIFTY"
    initial_capital: float = Field(default=config.DEFAULT_CAPITAL, gt=0)
    entry_conditions: Optional[List[dict]] = None
    exit_conditions: Optional[List[dict]] = None
    strategy_config: Optional[dict] = None


class LiveStartRequest(BaseModel):
    entry_conditions: Optional[List[dict]] = None
    exit_conditions: Optional[List[dict]] = None
    strategy_config: Optional[dict] = None
    # Full strategy fields (used when deploying from modal)
    run_name: str = ""
    instrument: str = ""
    indicators: List[str] = []
    legs: Optional[List[dict]] = None
    deploy_config: Optional[dict] = None
    max_trades_per_day: int = Field(default=1, ge=1, le=100)
    market_open: str = "09:15"
    market_close: str = "15:25"
    max_daily_loss: float = Field(default=0, ge=0)
    lots: int = Field(default=1, ge=1, le=500)
    stoploss_pct: float = Field(default=0.0, ge=0)
    stoploss_rupees: float = Field(default=0.0, ge=0)
    sl_type: str = "pct"
    target_profit_pct: float = Field(default=0.0, ge=0)
    target_profit_rupees: float = Field(default=0.0, ge=0)
    tp_type: str = "pct"
    initial_capital: float = Field(default=500000.0, gt=0)
    execution_profile: str = "auto"
    enforce_capital: bool = False
    capital_buffer_pct: float = Field(default=0.0, ge=0, lt=100)
    sell_option_margin_per_lot: float = Field(default=0.0, ge=0)
    strategy_id: int = Field(default=0, ge=0)


class OrderRequest(BaseModel):
    security_id: str = Field(min_length=1, max_length=64)
    exchange_segment: str = Field(default="NSE_EQ", min_length=1, max_length=32)
    transaction_type: str = Field(min_length=3, max_length=4)
    quantity: int = Field(ge=1, le=100_000)
    order_type: str = Field(default="MARKET", min_length=2, max_length=32)
    product_type: str = Field(default="INTRADAY", min_length=2, max_length=32)
    price: float = Field(default=0, ge=0)
    trigger_price: float = Field(default=0, ge=0)
    validity: str = Field(default="DAY", min_length=3, max_length=3)
    disclosed_quantity: int = Field(default=0, ge=0)
    after_market_order: bool = False
    amo_time: str = Field(default="", max_length=16)
    bo_profit_value: float = Field(default=0, ge=0)
    bo_stop_loss_value: float = Field(default=0, ge=0)
    slice_order: bool = False


class StockTerminalOrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    transaction_type: str = Field(min_length=3, max_length=4)
    quantity: int = Field(ge=1, le=100_000)
    order_type: str = Field(default="MARKET", min_length=2, max_length=32)
    product_type: str = Field(default="INTRADAY", min_length=2, max_length=32)
    price: float = Field(default=0, ge=0)
    trigger_price: float = Field(default=0, ge=0)
    validity: str = Field(default="DAY", min_length=3, max_length=3)
    disclosed_quantity: int = Field(default=0, ge=0)
    after_market_order: bool = False
    amo_time: str = Field(default="", max_length=16)
    bo_profit_value: float = Field(default=0, ge=0)
    bo_stop_loss_value: float = Field(default=0, ge=0)
    slice_order: bool = False


class StockTerminalGttRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    transaction_type: str = Field(min_length=3, max_length=4)
    quantity: int = Field(ge=1, le=100_000)
    order_flag: str = "SINGLE"
    order_type: str = "LIMIT"
    product_type: str = "CNC"
    validity: str = "DAY"
    price: float = Field(default=0, ge=0)
    trigger_price: float = Field(default=0, ge=0)
    price1: float = Field(default=0, ge=0)
    trigger_price1: float = Field(default=0, ge=0)
    quantity1: int = Field(default=0, ge=0)
    disclosed_quantity: int = Field(default=0, ge=0)


_ORDER_EXCHANGES = {"NSE_EQ", "NSE_FNO", "NSE_CURRENCY", "BSE_EQ", "BSE_FNO", "BSE_CURRENCY", "MCX_COMM"}
_ORDER_PRODUCTS = {"CNC", "INTRADAY", "MARGIN", "MTF", "CO", "BO"}
_ORDER_TYPES = {"MARKET", "LIMIT", "STOP_LOSS", "STOP_LOSS_MARKET"}
_ORDER_VALIDITIES = {"DAY", "IOC"}
_AMO_TIMES = {"", "PRE_OPEN", "OPEN", "OPEN_30", "OPEN_60"}


def _validated_order_values(req: OrderRequest) -> dict[str, str]:
    values = {
        "transaction_type": str(req.transaction_type or "").upper(),
        "exchange_segment": str(req.exchange_segment or "").upper(),
        "order_type": str(req.order_type or "").upper(),
        "product_type": str(req.product_type or "").upper(),
        "validity": str(req.validity or "").upper(),
        "amo_time": str(req.amo_time or "").upper(),
    }
    if values["transaction_type"] not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="transaction_type must be BUY or SELL")
    if values["exchange_segment"] not in _ORDER_EXCHANGES:
        raise HTTPException(status_code=400, detail="Unsupported exchange_segment")
    if values["order_type"] not in _ORDER_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported order_type")
    if values["product_type"] not in _ORDER_PRODUCTS:
        raise HTTPException(status_code=400, detail="Unsupported product_type")
    if values["validity"] not in _ORDER_VALIDITIES:
        raise HTTPException(status_code=400, detail="validity must be DAY or IOC")
    if req.disclosed_quantity > req.quantity:
        raise HTTPException(status_code=400, detail="disclosed_quantity cannot exceed quantity")
    if values["order_type"] in {"LIMIT", "STOP_LOSS"} and req.price <= 0:
        raise HTTPException(status_code=400, detail=f"{values['order_type']} requires price")
    if values["order_type"] in {"STOP_LOSS", "STOP_LOSS_MARKET"} and req.trigger_price <= 0:
        raise HTTPException(status_code=400, detail=f"{values['order_type']} requires trigger_price")
    if req.after_market_order and values["amo_time"] not in _AMO_TIMES:
        raise HTTPException(status_code=400, detail="Unsupported AMO time")
    return values


class StrategyPayload(BaseModel):
    strategy_id: int = Field(default=0, ge=0)
    run_name: str = ""
    folder: str = "Intraday"
    segment: str = "indices"
    instrument: str = "26000"
    from_date: str = config.DEFAULT_FROM
    to_date: str = config.DEFAULT_TO
    initial_capital: float = Field(default=500000.0, gt=0)
    lots: int = Field(default=1, ge=1, le=500)
    lot_size: int = Field(default=0, ge=0)
    stoploss_pct: float = Field(default=0.0, ge=0)
    stoploss_rupees: float = Field(default=0.0, ge=0)
    sl_type: str = "pct"
    target_profit_pct: float = Field(default=0.0, ge=0)
    target_profit_rupees: float = Field(default=0.0, ge=0)
    tp_type: str = "pct"
    market_open: str = "09:15"
    market_close: str = "15:25"
    max_trades_per_day: int = Field(default=1, ge=1, le=100)
    max_daily_loss: float = Field(default=0.0, ge=0)
    indicators: List[str] = []
    entry_conditions: Optional[List[dict]] = None
    exit_conditions: Optional[List[dict]] = None
    legs: Optional[List[dict]] = None
    deploy_config: Optional[dict] = None
    combined_sl_rupees: float = 0
    combined_target_rupees: float = 0
    combined_sqoff_time: str = "15:20"
    fee_pct: float = 0.0
    trailing_sl_pct: float = 0.0
    execution_profile: str = "auto"
    spread_bps: float = Field(default=0.0, ge=0)
    entry_slippage_bps: float = Field(default=0.0, ge=0)
    exit_slippage_bps: float = Field(default=0.0, ge=0)
    entry_delay_candles: int = Field(default=0, ge=0, le=25)
    signal_exit_delay_candles: int = Field(default=0, ge=0, le=25)
    enforce_capital: bool = False
    capital_buffer_pct: float = Field(default=0.0, ge=0, lt=100)
    sell_option_margin_per_lot: float = Field(default=0.0, ge=0)
    allow_synthetic_option_fallback: bool = False


class CascadeBacktestPayload(BaseModel):
    """Manual 1H NIFTY cascade replay request.

    This intentionally does not accept a live-order flag: the first release is
    backtest-only and is kept separate from the execution engines.
    """

    mother_timestamp: str
    mother_high: float = Field(gt=0)
    mother_low: float = Field(gt=0)
    option_type: str = "CE"
    timeframe: str = "1h"
    to_date: str = ""


class CascadePaperStartPayload(BaseModel):
    """A 5m NIFTY mother used to begin a paper campaign.

    OHLC is optional: when omitted together the server loads the exact closed
    NIFTY candle selected by timestamp.  Keeping a full manual override is
    useful for investigating a corrected/recorded candle, but mixed values are
    deliberately rejected so the campaign never starts from a half-manual bar.
    """

    mother_timestamp: str
    mother_open: Optional[float] = Field(default=None, gt=0)
    mother_high: Optional[float] = Field(default=None, gt=0)
    mother_low: Optional[float] = Field(default=None, gt=0)
    mother_close: Optional[float] = Field(default=None, gt=0)
    rung_inr: float = Field(default=13000, gt=0, le=1_000_000)
    ce_offset_steps: int = Field(default=-2, ge=-10, le=0)


class CandleEntryPaperStartPayload(BaseModel):
    """Mother timestamp and starting chart for the two-red ladder campaign.

    The timeframe is where the ladder STARTS; it climbs from there through
    every slower chart up to 1H (1m -> 1+2+3+4 lots, 15m -> 3+4, and so on).
    The default keeps the old single-rung 1H behaviour for anything that
    still posts without a timeframe.
    """

    mother_timestamp: str
    timeframe: str = "1h"
    ce_offset_steps: int = Field(default=-2, ge=-10, le=0)


class FibBoundaryPaperStartPayload(BaseModel):
    """A named mother candle for the fib-boundary paper campaign.

    The candle's high and low come from Dhan — the same "nothing typed by
    hand" rule as the Test Bench, per Phil (2026-07-30).  The optional typed
    values remain only as an explicit override for a bar Dhan cannot serve.
    The timeframe decides which levels trade -- (4, 8) on 1m/5m, (2, 4, 8) on
    15m/1h -- and the side (CE below support, PE above resistance) is chosen
    per mother.
    """

    mother_timestamp: str
    mother_high: Optional[float] = Field(default=None, gt=0)
    mother_low: Optional[float] = Field(default=None, gt=0)
    side: str = Field(default="CE")
    timeframe: str = Field(default="5m")
    rung_inr: float = Field(default=75000, gt=0, le=1_000_000)
    itm_steps: int = Field(default=2, ge=0, le=10)


class FibTouchStartPayload(BaseModel):
    """Phil's locked swing-anchored touch ladder, 2026-08-06.

    The mother candle names where to start looking; its high and low are NOT
    the ladder's anchors any more -- the first involvement on each side is.
    Entries are 1m touches on the halving ladder, one lot each, and the whole
    ladder stops at ``capital_cap_inr`` rather than per rung.
    """

    symbol: str = Field(default="NIFTY")
    side: str = Field(default="CE")
    mother_timestamp: str
    # The chart the mother is read on. Touches are always watched on 1m.
    timeframe: str = Field(default="1m")
    capital_cap_inr: float = Field(default=75_000, gt=0, le=10_000_000)
    itm_steps: int = Field(default=2, ge=0, le=10)
    min_dte: int = Field(default=4, ge=0, le=45)
    # "paper" or "live". Live is built but refuses to send; see LiveExecutor.
    mode: str = Field(default="paper")


class TestBenchPayload(BaseModel):
    """One mother candle, named rather than described.

    Deliberately has no mother_high / mother_low.  The whole point of the Test
    Bench is that the system fetches the candle: a typed high and low is a place
    for a typo to become a P&L figure.
    """

    instrument: str = Field(default="NIFTY")
    strategy: str = Field(default="fib")
    timeframe: str = Field(default="5m")
    mother_timestamp: str
    side: str = Field(default="CE")
    rung_inr: float = Field(default=25000, gt=0, le=1_000_000)
    itm_steps: int = Field(default=2, ge=0, le=10)
    # Replay even when this exact question already has a stored answer.
    force: bool = Field(default=False)


class FibTouchBacktestPayload(BaseModel):
    """A past mother replayed through the SAME ladder the Start button trades.

    Deliberately mirrors FibTouchStartPayload field for field, plus a horizon,
    so a backtest and a live run differ only in where their prices come from.
    """

    symbol: str = Field(default="NIFTY")
    side: str = Field(default="CE")
    mother_timestamp: str
    timeframe: str = Field(default="1m")
    capital_cap_inr: float = Field(default=75_000, gt=0, le=10_000_000)
    itm_steps: int = Field(default=2, ge=0, le=10)
    min_dte: int = Field(default=4, ge=0, le=45)
    # The ladder ends at its target, a broken mother or expiry. Ten days covers
    # the overwhelming majority; the ceiling allows a contract held to expiry.
    horizon_days: int = Field(default=10, ge=1, le=60)


class FibBoundaryBacktestPayload(BaseModel):
    """A past mother replayed with REAL fixed-strike Upstox premiums.

    Same manual mother as the paper start, but instead of the current-quote
    paper engine this runs the batch ``FibBoundaryCascade`` and prices every leg
    off Upstox's expired-instrument 1-minute history -- so an old mother returns
    real per-round P&L, not the signal-only geometry the live paper engine
    withholds.  A strike/expiry Upstox never listed is a recorded gap, never a
    fabricated zero.
    """

    mother_timestamp: str
    mother_high: Optional[float] = Field(default=None, gt=0)
    mother_low: Optional[float] = Field(default=None, gt=0)
    side: str = Field(default="CE")
    timeframe: str = Field(default="5m")
    rung_inr: float = Field(default=75000, gt=0, le=1_000_000)
    itm_steps: int = Field(default=2, ge=0, le=10)
    # A monthly bought at 15-45 DTE with no stop loss can only end at its target
    # or at expiry, so the replay has to be able to reach 45 days out; the old
    # 20-day default guaranteed an unfinished answer on half the contracts.
    horizon_days: int = Field(default=50, ge=1, le=70)


class TerminalCascadePaperStartPayload(BaseModel):
    symbol: str
    mother_timestamp: str
    capital_inr: float = Field(default=100000, gt=0, le=50_000_000)
    timeframe: str = "5m"
    target_fraction: float = Field(default=0.25, gt=0, le=1)
    product_type: str = "CNC"


class OhlcvExportPayload(BaseModel):
    instrument: str = "26000"
    segment: str = "indices"
    from_date: str = config.DEFAULT_FROM
    to_date: str = config.DEFAULT_TO
    candle_interval: str = "1"
    split_by_day: bool = True
    export_name: str = ""


def _referral_qr_data_uri(referral_url: str) -> str:
    """Render the Dhan invite QR ourselves as an inline SVG data URI.

    Previously the login page pointed an <img> at api.qrserver.com, which handed
    the referral URL — and every unlock-page viewer's IP — to a third party on
    each render. Generating it in-process keeps it entirely first-party. If the
    QR library is missing or generation fails, return "" so the caller hides the
    QR (the text link still works) rather than falling back to a leaky host.
    """
    if not referral_url:
        return ""
    try:
        import segno

        buf = io.BytesIO()
        segno.make(referral_url, error="m").save(buf, kind="svg", scale=4, border=1)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}"
    except Exception:
        return ""


def _render_login_page() -> HTMLResponse:
    login_path = os.path.join(_HERE, "login.html")
    if not os.path.exists(login_path):
        return HTMLResponse("<h2>login.html not found</h2>")
    login_html = _read_frontend_template(login_path)
    referral_url = config.DHAN_REFERRAL_URL
    referral_qr_url = _referral_qr_data_uri(referral_url)
    # The block (with its text link) shows whenever a referral URL exists; the
    # QR sub-div hides on its own if we couldn't render a first-party image, so
    # a QR failure never costs the working link.
    login_html = login_html.replace("__DHAN_REFERRAL_URL__", _escape_html(referral_url or "#", quote=True))
    login_html = login_html.replace("__DHAN_REFERRAL_QR_URL__", _escape_html(referral_qr_url, quote=True))
    login_html = login_html.replace("__DHAN_REFERRAL_HIDDEN_CLASS__", "" if referral_url else " hidden")
    login_html = login_html.replace("__DHAN_REFERRAL_QR_HIDDEN_CLASS__", "" if referral_qr_url else " hidden")
    return HTMLResponse(login_html)


# ── Serve Frontend ────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def serve_landing():
    """Public front door for philforge.in. No session, no secrets.

    This is the shared landing for both desks: it tells the Homma story once
    and then forks to the equities terminal here at /app and to the crypto
    terminal on crypto.philforge.in. The terminal itself moved to /app, and
    every CTA on this page points there.
    """
    landing_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "landing", "forge.html")
    if os.path.exists(landing_path):
        resp = HTMLResponse(_read_frontend_template(landing_path))
        # Short, revalidated: the page is public but changes with deploys.
        resp.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
        return resp
    # Losing the landing file must not lock anyone out of the terminal.
    return RedirectResponse("/app", status_code=307)


@app.get("/equities", response_class=HTMLResponse)
@app.get("/equities/", response_class=HTMLResponse)
async def serve_equities_landing():
    """The equities story page — the mirror of crypto.philforge.in's own.

    Public, like "/". Both spellings are registered because the shared front
    door and any hand-typed URL may or may not carry the trailing slash, and a
    404 on a marketing link is worse than one extra route.
    """
    page_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "landing", "equities.html")
    if os.path.exists(page_path):
        resp = HTMLResponse(_read_frontend_template(page_path))
        resp.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
        return resp
    # A missing story page should land on the shared front door, not a 404.
    return RedirectResponse("/", status_code=307)


@app.get("/app", response_class=HTMLResponse)
async def serve_frontend(request: Request):
    user = await _get_page_user(request)
    if not user:
        return _render_login_page()
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy.html")
    if os.path.exists(html_path):
        return HTMLResponse(_read_frontend_template(html_path))
    return HTMLResponse("<h2>strategy.html not found. Place it beside app.py</h2>")


@app.get("/logo.jpg")
async def serve_logo():
    """Serves the main application logo."""
    return FileResponse("logo.jpg")


@app.get("/logo.png")
async def serve_logo_png():
    """Serves the PNG application logo."""
    return FileResponse("logo.png")


@app.get("/favicon.ico")
async def serve_favicon():
    """Serves the application favicon for browsers and installed app shells."""
    return FileResponse(os.path.join(_HERE, "static", "pwa-icons", "favicon-32.png"), media_type="image/png")


@app.get("/apple-touch-icon.png")
async def serve_apple_touch_icon():
    """Serves the application touch icon for iOS/macOS install surfaces."""
    return FileResponse(os.path.join(_HERE, "static", "pwa-icons", "apple-touch-icon.png"), media_type="image/png")


@app.get("/manifest.webmanifest")
async def serve_manifest():
    path = os.path.join(_HERE, "static", "manifest.webmanifest")
    return Response(_read_frontend_template(path), media_type="application/manifest+json")


@app.get("/site.webmanifest")
async def serve_site_manifest():
    path = os.path.join(_HERE, "static", "manifest.webmanifest")
    return Response(_read_frontend_template(path), media_type="application/manifest+json")


@app.get("/sw.js")
async def serve_service_worker():
    path = os.path.join(_HERE, "static", "sw.js")
    return Response(_read_frontend_template(path), media_type="application/javascript")


# ── Chart Viewer ──────────────────────────────────────────────────
import calendar as _cal
import re as _re

CHARTS_DIR = os.getenv("CHARTS_DIR", os.path.join(_HERE, "Daily Charts"))
_USER_DATA_ROOT = config.USER_DATA_ROOT

# Build month-name lookup: JAN→1, JANUARY→1, FEB→2, FEBRUARY→2, …
_MONTH_MAP: dict[str, int] = {}
for _i in range(1, 13):
    _MONTH_MAP[_cal.month_abbr[_i].upper()] = _i
    _MONTH_MAP[_cal.month_name[_i].upper()] = _i


def _parse_month_folder(name: str):
    """Parse 'APR_2023' / 'Apr-2024' / 'JULY_2023' → (month_num, label) or None."""
    parts = _re.split(r"[_-]", name, maxsplit=1)
    if len(parts) < 2:
        return None
    num = _MONTH_MAP.get(parts[0].upper()) or _MONTH_MAP.get(parts[0].upper()[:3])
    if num is None:
        return None
    return num, _cal.month_abbr[num]


def _parse_day_folder(name: str, *, year_hint: int | None = None, month_hint: int | None = None):
    """Parse day folder → (sort_key, display_label) or fallback to name itself."""

    def _valid_date_parts(year_value: int, month_value: int, day_value: int) -> bool:
        try:
            date(year_value, month_value, day_value)
            return True
        except (TypeError, ValueError):
            return False

    # DD_MM_YYYY or DD-MM-YYYY (all numeric)
    m = _re.match(r"^(\d{1,2})[\s_-](\d{1,2})[\s_-](\d{4})$", name)
    if m:
        dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid_date_parts(yyyy, mm, dd):
            return f"{yyyy:04d}-{mm:02d}-{dd:02d}", f"{dd:02d} {_cal.month_abbr[mm]}"
    # DD-Mon-YYYY (e.g. 01-Feb-2026)
    m = _re.match(r"^(\d{1,2})[\s_-]([A-Za-z]+)[\s_-](\d{4})$", name)
    if m:
        dd = int(m.group(1))
        num = _MONTH_MAP.get(m.group(2).upper()) or _MONTH_MAP.get(m.group(2).upper()[:3])
        yyyy = int(m.group(3))
        if num and _valid_date_parts(yyyy, num, dd):
            return f"{yyyy:04d}-{num:02d}-{dd:02d}", f"{dd:02d} {_cal.month_abbr[num]}"
    # DD-Mon (no year, e.g. 13-Feb)
    m = _re.match(r"^(\d{1,2})[\s_-]([A-Za-z]+)$", name)
    if m:
        dd = int(m.group(1))
        num = _MONTH_MAP.get(m.group(2).upper()) or _MONTH_MAP.get(m.group(2).upper()[:3])
        if num and _valid_date_parts(int(year_hint) if year_hint else 2000, num, dd):
            yyyy = int(year_hint) if year_hint else 9999
            return f"{yyyy:04d}-{num:02d}-{dd:02d}", f"{dd:02d} {_cal.month_abbr[num]}"
    # Mon-DD-DD or Mon-DD-DD-DD (ranges like Feb-12-15, Feb-4-5-6)
    m = _re.match(r"^([A-Za-z]+)[\s_-](\d{1,2})", name)
    if m:
        num = _MONTH_MAP.get(m.group(1).upper()) or _MONTH_MAP.get(m.group(1).upper()[:3])
        dd = int(m.group(2))
        if num and _valid_date_parts(int(year_hint) if year_hint else 2000, num, dd):
            yyyy = int(year_hint) if year_hint else 9999
            return f"{yyyy:04d}-{num:02d}-{dd:02d}", name
    # DD only — infer month/year from the enclosing folder when possible
    m = _re.match(r"^(\d{1,2})$", name)
    if m and year_hint and month_hint and _valid_date_parts(int(year_hint), int(month_hint), int(m.group(1))):
        dd = int(m.group(1))
        return f"{int(year_hint):04d}-{int(month_hint):02d}-{dd:02d}", f"{dd:02d} {_cal.month_abbr[int(month_hint)]}"
    # Fallback — sort after all dated entries
    return f"9999-99-{name}", name


def _canonicalize_chart_day_folder_name(year: str, month_folder: str, day_name: str) -> str:
    safe_name = _re.sub(r"[^\w\s._-]", "", str(day_name or "").strip())[:80]
    if not safe_name:
        return ""
    parsed_month = _parse_month_folder(str(month_folder or ""))
    if parsed_month is None:
        return ""
    month_num, _month_label = parsed_month
    if not _re.fullmatch(r"\d{4}", str(year or "")):
        return ""
    year_int = int(year)
    month_parts = _re.split(r"[_-]", str(month_folder or ""), maxsplit=1)
    if len(month_parts) != 2 or month_parts[1] != str(year_int):
        return ""

    def _canonical_date(day_value: int, month_value: int, year_value: int) -> str:
        try:
            parsed = date(year_value, month_value, day_value)
        except ValueError:
            return ""
        if parsed.year != year_int or parsed.month != month_num:
            return ""
        return f"{parsed.day:02d}-{_cal.month_abbr[parsed.month]}-{parsed.year:04d}"

    m = _re.match(r"^(\d{1,2})[\s_-](\d{1,2})[\s_-](\d{4})$", safe_name)
    if m:
        dd, mm, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _canonical_date(dd, mm, yyyy)
    m = _re.match(r"^(\d{1,2})[\s_-]([A-Za-z]+)[\s_-](\d{4})$", safe_name)
    if m:
        dd = int(m.group(1))
        mon = _MONTH_MAP.get(m.group(2).upper()) or _MONTH_MAP.get(m.group(2).upper()[:3]) or month_num
        return _canonical_date(dd, int(mon), int(m.group(3)))
    m = _re.match(r"^(\d{1,2})[\s_-]([A-Za-z]+)$", safe_name)
    if m:
        dd = int(m.group(1))
        mon = _MONTH_MAP.get(m.group(2).upper()) or _MONTH_MAP.get(m.group(2).upper()[:3]) or month_num
        return _canonical_date(dd, int(mon), year_int)
    m = _re.match(r"^([A-Za-z]+)[\s_-](\d{1,2})$", safe_name)
    if m:
        mon = _MONTH_MAP.get(m.group(1).upper()) or _MONTH_MAP.get(m.group(1).upper()[:3]) or month_num
        dd = int(m.group(2))
        return _canonical_date(dd, int(mon), year_int)
    m = _re.match(r"^(\d{1,2})$", safe_name)
    if m:
        dd = int(m.group(1))
        return _canonical_date(dd, month_num, year_int)
    return ""


def _resolve_legacy_chart_date(user_id: int, date_str: str) -> str:
    text = str(date_str or "")
    match = _re.match(r"^9999-(\d{2})-(\d{2})$", text)
    if not match:
        return text

    target_month = int(match.group(1))
    target_day = int(match.group(2))
    charts_root = _user_charts_root(user_id)
    if not os.path.isdir(charts_root):
        return text

    best_match = ""
    for year in sorted(os.listdir(charts_root), reverse=True):
        year_path = os.path.join(charts_root, year)
        if not os.path.isdir(year_path) or not str(year).isdigit():
            continue
        for month_folder in os.listdir(year_path):
            month_path = os.path.join(year_path, month_folder)
            if not os.path.isdir(month_path):
                continue
            parsed_month = _parse_month_folder(month_folder)
            if parsed_month is None:
                continue
            month_num, _month_label = parsed_month
            if int(month_num) != target_month:
                continue
            for day_folder in os.listdir(month_path):
                day_path = os.path.join(month_path, day_folder)
                if not os.path.isdir(day_path):
                    continue
                sort_key, _day_label = _parse_day_folder(day_folder, year_hint=int(year), month_hint=int(month_num))
                if sort_key == f"{int(year):04d}-{target_month:02d}-{target_day:02d}":
                    if sort_key > best_match:
                        best_match = sort_key
    return best_match or text


async def _normalize_journal_date_for_user(user_id: int, date_str: str) -> str:
    text = str(date_str or "")
    normalized = _resolve_legacy_chart_date(user_id, text)
    if normalized == text:
        return text
    payload = await _db_mod.get_journal_entry(user_id, text)
    if payload:
        existing = await _db_mod.get_journal_entry(user_id, normalized)
        if not existing:
            await _db_mod.upsert_journal_entry(user_id, normalized, payload)
        await _db_mod.delete_journal_entry(user_id, text)
    return normalized


def _user_storage_root(user_id: int) -> str:
    return os.path.join(_USER_DATA_ROOT, str(int(user_id or 0)))


def _user_exports_root(user_id: int) -> str:
    return os.path.join(_user_storage_root(user_id), "exports")


def _safe_export_name(value: str, default: str = "export") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or default


def _user_charts_root(user_id: int) -> str:
    return os.path.join(_user_storage_root(user_id), "charts")


def _safe_charts_subpath(user_id: int, *parts: str, create_root: bool = False) -> str | None:
    """Resolve path under the current user's charts root; return None on traversal."""
    for p in parts:
        if "/" in p or "\\" in p or ".." in p:
            return None
    root = _user_charts_root(user_id)
    if create_root:
        os.makedirs(root, exist_ok=True)
    candidate = os.path.join(root, *parts)
    if not os.path.realpath(candidate).startswith(os.path.realpath(root)):
        return None
    return candidate


@app.get("/charts-viewer", response_class=HTMLResponse)
async def serve_charts_viewer(request: Request):
    """Serve the historical chart viewer page (auth-protected)."""
    user = await _get_page_user(request)
    if not user:
        return _render_login_page()
    html_path = os.path.join(_HERE, "charts.html")
    if os.path.exists(html_path):
        return HTMLResponse(_read_frontend_template(html_path))
    return HTMLResponse("<h2>charts.html not found. Place it beside app.py</h2>")


@app.get("/market-movers", response_class=HTMLResponse)
async def serve_market_movers(request: Request):
    """Serve the standalone Nifty 50 market movers page (auth-protected)."""
    user = await _get_page_user(request)
    if not user:
        return _render_login_page()
    html_path = os.path.join(_HERE, "market_movers.html")
    if os.path.exists(html_path):
        return HTMLResponse(_read_frontend_template(html_path))
    return HTMLResponse("<h2>market_movers.html not found. Place it beside app.py</h2>")


@app.get("/study-lounge", response_class=HTMLResponse)
async def serve_study_lounge(request: Request):
    """Serve the standalone study page (auth-protected)."""
    user = await _get_page_user(request)
    if not user:
        return _render_login_page()
    html_path = os.path.join(_HERE, "study_lounge.html")
    if os.path.exists(html_path):
        return HTMLResponse(_read_frontend_template(html_path))
    return HTMLResponse("<h2>study_lounge.html not found. Place it beside app.py</h2>")


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    """The landing pages exist to be found; the terminal does not. The old
    body was `Disallow: /` — a marketing site whose robots file told every
    crawler to leave, which quietly unlists it from search entirely."""
    body = "\n".join(
        (
            "User-agent: *",
            "Allow: /",
            "Disallow: /app",
            "Disallow: /api/",
            "Disallow: /charts-viewer",
            "Disallow: /study-lounge",
            "",
            "Sitemap: https://philforge.in/sitemap.xml",
            "",
        )
    )
    return PlainTextResponse(body, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    """The two public story pages. Tiny, but a sitemap that exists beats the
    401 this path used to answer with."""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url><loc>https://philforge.in/</loc><changefreq>monthly</changefreq></url>\n"
        "  <url><loc>https://philforge.in/equities</loc><changefreq>monthly</changefreq></url>\n"
        "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/study-assets/{asset_path:path}")
async def serve_study_asset(asset_path: str, request: Request):
    user = await _get_page_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    static_root = os.path.join(_HERE, "static")
    rel_parts = [part for part in asset_path.replace("\\", "/").split("/") if part]
    if not rel_parts or any(part.startswith(".") for part in rel_parts):
        raise HTTPException(status_code=404, detail="Asset not found")
    base_dir = os.path.abspath(os.path.join(static_root, "notebooklm"))
    full_path = os.path.abspath(os.path.normpath(os.path.join(base_dir, asset_path)))
    if not full_path.startswith(base_dir + os.sep):
        raise HTTPException(status_code=404, detail="Asset not found")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Asset not found")
    await asyncio.to_thread(sanitize_study_asset, static_root, full_path)
    return FileResponse(full_path)


@app.get("/api/market-movers/nifty50")
async def api_market_movers_nifty50(request: Request):
    """Standalone Nifty 50 cash-stock snapshot used by the market movers page."""
    broker_client = None
    try:
        _, broker_client, _ = await _request_broker_context(request)
    except Exception:
        broker_client = None
    fallback_client = dhan if dhan._is_configured() and dhan is not broker_client else None
    return await asyncio.to_thread(
        get_nifty50_market_movers_snapshot,
        broker_client,
        fallback_client,
    )


@app.get("/api/study-library")
async def api_study_library(request: Request):
    """Return standalone study assets for the study lounge."""
    user = await _get_page_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await asyncio.to_thread(get_study_library, os.path.join(_HERE, "static"))


@app.get("/api/charts/tree")
async def charts_tree(request: Request):
    """Return directory tree adapted to Daily Charts/ folder structure."""
    user_id = _request_user_id(request)
    charts_root = _user_charts_root(user_id)
    print(f"[CHARTS] Scanning user charts dir for user {user_id}: {charts_root}")
    print(f"[CHARTS] Exists: {os.path.isdir(charts_root)}")
    if not os.path.isdir(charts_root):
        print("[CHARTS] Directory NOT found – returning empty tree")
        return {"years": {}}
    tree: dict = {}
    for year in sorted(os.listdir(charts_root)):
        year_path = os.path.join(charts_root, year)
        if not os.path.isdir(year_path) or not year.isdigit():
            continue
        months_list = []
        for mfolder in os.listdir(year_path):
            month_path = os.path.join(year_path, mfolder)
            if not os.path.isdir(month_path):
                continue
            parsed = _parse_month_folder(mfolder)
            if parsed is None:
                continue
            month_num, month_label = parsed
            days_list = []
            for dfolder in os.listdir(month_path):
                day_path = os.path.join(month_path, dfolder)
                if not os.path.isdir(day_path):
                    continue
                files = os.listdir(day_path)
                has_img = any(f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) for f in files)
                has_keep = ".keep" in files
                if not has_img and not has_keep:
                    continue
                sort_key, day_label = _parse_day_folder(dfolder, year_hint=int(year), month_hint=int(month_num))
                days_list.append(
                    {
                        "folder": dfolder,
                        "label": day_label,
                        "sort": sort_key,
                    }
                )
            if not days_list:
                continue
            # Check for custom sort order
            _sort_file = os.path.join(month_path, "_sort_order.json")
            if os.path.isfile(_sort_file):
                try:
                    with open(_sort_file, "r") as _sf:
                        _custom_order = json.load(_sf)  # list of folder names
                    _order_map = {name: i for i, name in enumerate(_custom_order)}
                    days_list.sort(key=lambda d: _order_map.get(d["folder"], 9999))
                except Exception:
                    days_list.sort(key=lambda d: d["sort"])
            else:
                days_list.sort(key=lambda d: d["sort"])
            months_list.append(
                {
                    "folder": mfolder,
                    "label": month_label,
                    "num": month_num,
                    "days": days_list,
                }
            )
        if not months_list:
            continue
        months_list.sort(key=lambda m: m["num"])
        tree[year] = months_list
    print(
        f"[CHARTS] Tree result: {len(tree)} years, total days: {sum(sum(len(m['days']) for m in ms) for ms in tree.values())}"
    )
    return {"years": tree}


@app.get("/api/charts/images/{year}/{month}/{day}")
async def charts_images(year: str, month: str, day: str, request: Request):
    """Return list of image URLs for a specific date folder."""
    day_path = _safe_charts_subpath(_request_user_id(request), year, month, day)
    if day_path is None:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isdir(day_path):
        return {"images": [], "urls": [], "date": day}
    images = sorted(f for f in os.listdir(day_path) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
    from urllib.parse import quote

    return {
        "images": images,
        "date": day,
        "urls": [f"/charts-static/{quote(year)}/{quote(month)}/{quote(day)}/{quote(img)}" for img in images],
    }


@app.get("/charts-static/{year}/{month}/{day}/{filename}")
async def serve_chart_image(year: str, month: str, day: str, filename: str, request: Request):
    """Serve a single chart image file."""
    safe_name = os.path.basename(filename)
    if not safe_name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        raise HTTPException(status_code=400, detail="Invalid file type")
    file_path = _safe_charts_subpath(_request_user_id(request), year, month, day, safe_name)
    if file_path is None or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)


# ── Chart Upload (Ctrl+V paste) ──────────────────────────────────
_ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


async def _read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
        chunks.append(chunk)
    return b"".join(chunks)


def _chart_upload_target(
    target_year: str | None,
    target_month: str | None,
    target_day: str | None,
) -> tuple[str, str, str, date]:
    supplied = [target_year is not None, target_month is not None, target_day is not None]
    if any(supplied) and not all(supplied):
        raise HTTPException(status_code=400, detail="target_year, target_month, and target_day are required together")

    if not any(supplied):
        target_date = datetime.now(IST).date()
        month_abbr = _cal.month_abbr[target_date.month]
        return (
            str(target_date.year),
            f"{month_abbr}-{target_date.year}",
            f"{target_date.day:02d}-{month_abbr}-{target_date.year}",
            target_date,
        )

    year_str = str(target_year or "")
    month_folder = str(target_month or "")
    day_folder = str(target_day or "")
    if not _re.fullmatch(r"\d{4}", year_str):
        raise HTTPException(status_code=400, detail="Invalid target year")
    parsed_month = _parse_month_folder(month_folder)
    if parsed_month is None:
        raise HTTPException(status_code=400, detail="Invalid target month")
    month_num, _month_abbr = parsed_month
    month_parts = _re.split(r"[_-]", month_folder, maxsplit=1)
    if len(month_parts) != 2 or month_parts[1] != year_str:
        raise HTTPException(status_code=400, detail="Target month does not match its year")
    sort_key, _day_label = _parse_day_folder(day_folder, year_hint=int(year_str), month_hint=month_num)
    try:
        target_date = date.fromisoformat(sort_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid target day") from exc
    if target_date.year != int(year_str) or target_date.month != month_num:
        raise HTTPException(status_code=400, detail="Invalid target day")
    return year_str, month_folder, day_folder, target_date


def _write_unique_chart(data: bytes, day_path: str, date_tag: str, extension: str) -> tuple[str, str]:
    for counter in range(10_000):
        suffix = "" if counter == 0 else f"_{counter}"
        filename = f"Nifty_{date_tag}{suffix}{extension}"
        file_path = os.path.join(day_path, filename)
        try:
            fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
        except Exception:
            try:
                os.unlink(file_path)
            except OSError:
                pass
            raise
        return filename, file_path
    raise HTTPException(status_code=409, detail="Too many chart files exist for this date")


@app.post("/api/upload-chart")
async def upload_chart(
    request: Request,
    file: UploadFile,
    target_year: str | None = Form(None),
    target_month: str | None = Form(None),
    target_day: str | None = Form(None),
):
    """Receive a pasted screenshot, save to Daily Charts/YYYY/Mon-YYYY/DD-Mon-YYYY/."""
    from urllib.parse import quote

    data = await _read_upload_limited(file, _MAX_UPLOAD_SIZE)
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        sanitized = await asyncio.to_thread(sanitize_image, data, file.content_type or "")
    except ImageValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if len(sanitized.data) > _MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="Sanitized image is too large (max 10 MB)")
    data = sanitized.data
    ext = sanitized.extension

    year_str, month_folder, day_folder, target_date = _chart_upload_target(
        target_year,
        target_month,
        target_day,
    )

    day_path = _safe_charts_subpath(_request_user_id(request), year_str, month_folder, day_folder, create_root=True)
    if day_path is None:
        raise HTTPException(status_code=400, detail="Invalid target path")
    os.makedirs(day_path, exist_ok=True)
    print(f"[CHARTS] Upload target dir: {day_path}")

    date_tag = target_date.strftime("%d-%m-%Y")
    filename, file_path = _write_unique_chart(data, day_path, date_tag, ext)
    # Remove .keep placeholder if present (created by create-folder)
    keep_file = os.path.join(day_path, ".keep")
    if os.path.isfile(keep_file):
        os.remove(keep_file)
    print(f"[CHARTS] Saved upload: {file_path} ({len(data)} bytes)")

    url = f"/charts-static/{quote(year_str)}/{quote(month_folder)}/{quote(day_folder)}/{quote(filename)}"
    return {
        "status": "ok",
        "filename": filename,
        "url": url,
        "year": year_str,
        "month_folder": month_folder,
        "day_folder": day_folder,
    }


# ── Delete a chart image ─────────────────────────────────────────
@app.delete("/api/charts/delete/{year}/{month}/{day}/{filename}")
async def delete_chart(year: str, month: str, day: str, filename: str, request: Request):
    """Delete a single chart image file."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail="Invalid file type")
    file_path = _safe_charts_subpath(_request_user_id(request), year, month, day, filename)
    if file_path is None:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(file_path)
    print(f"[CHARTS] Deleted: {file_path}")
    return {"status": "ok", "deleted": filename}


# ── Rename a chart image ─────────────────────────────────────────
@app.patch("/api/charts/rename/{year}/{month}/{day}/{filename}")
async def rename_chart(year: str, month: str, day: str, filename: str, request: Request):
    """Rename a chart image file."""
    body = await request.json()
    new_name = body.get("new_name", "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="New name is required")
    # Validate old file
    old_ext = os.path.splitext(filename)[1].lower()
    if old_ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail="Invalid file type")
    user_id = _request_user_id(request)
    old_path = _safe_charts_subpath(user_id, year, month, day, filename)
    if old_path is None:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not os.path.isfile(old_path):
        raise HTTPException(status_code=404, detail="File not found")
    # Sanitize new name: keep extension, strip dangerous chars
    new_base = _re.sub(r"[^\w\s._-]", "", os.path.splitext(new_name)[0])[:80]
    if not new_base:
        raise HTTPException(status_code=400, detail="Invalid new name")
    new_filename = f"{new_base}{old_ext}"
    new_path = _safe_charts_subpath(user_id, year, month, day, new_filename)
    if new_path is None:
        raise HTTPException(status_code=400, detail="Invalid new path")
    if os.path.exists(new_path):
        raise HTTPException(status_code=409, detail="A file with that name already exists")
    os.rename(old_path, new_path)
    from urllib.parse import quote

    new_url = f"/charts-static/{quote(year)}/{quote(month)}/{quote(day)}/{quote(new_filename)}"
    print(f"[CHARTS] Renamed: {filename} → {new_filename}")
    return {"status": "ok", "old_name": filename, "new_name": new_filename, "url": new_url}


@app.patch("/api/charts/rename-folder")
async def rename_chart_folder(request: Request):
    """Rename a day folder in Chart History."""
    body = await request.json()
    year = body.get("year", "")
    month = body.get("month", "")
    old_day = body.get("old_day", "")
    new_day = body.get("new_day", "").strip()
    if not all([year, month, old_day, new_day]):
        raise HTTPException(status_code=400, detail="year, month, old_day, new_day required")
    user_id = _request_user_id(request)
    old_path = _safe_charts_subpath(user_id, year, month, old_day)
    if old_path is None or not os.path.isdir(old_path):
        raise HTTPException(status_code=404, detail="Folder not found")
    safe_new = _canonicalize_chart_day_folder_name(year, month, new_day)
    if not safe_new:
        raise HTTPException(status_code=400, detail="Invalid new name")
    new_path = _safe_charts_subpath(user_id, year, month, safe_new)
    if new_path is None:
        raise HTTPException(status_code=400, detail="Invalid new path")
    if os.path.exists(new_path):
        raise HTTPException(status_code=409, detail="Folder already exists")
    os.rename(old_path, new_path)
    print(f"[CHARTS] Renamed folder: {old_day} → {safe_new}")
    return {"status": "ok", "old_name": old_day, "new_name": safe_new}


@app.post("/api/charts/create-folder")
async def create_chart_folder(request: Request):
    """Create a new day folder in Chart History."""
    body = await request.json()
    year = body.get("year", "")
    month = body.get("month", "")
    day_name = body.get("day_name", "").strip()
    if not all([year, month, day_name]):
        raise HTTPException(status_code=400, detail="year, month, day_name required")
    user_id = _request_user_id(request)
    safe_name = _canonicalize_chart_day_folder_name(year, month, day_name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid folder name")
    # Ensure year and month directories exist
    year_path = _safe_charts_subpath(user_id, year, create_root=True)
    if year_path is None:
        raise HTTPException(status_code=400, detail="Invalid year")
    month_path = _safe_charts_subpath(user_id, year, month, create_root=True)
    if month_path is None:
        raise HTTPException(status_code=400, detail="Invalid month")
    os.makedirs(month_path, exist_ok=True)
    folder_path = os.path.join(month_path, safe_name)
    if os.path.exists(folder_path):
        raise HTTPException(status_code=409, detail="Folder already exists")
    os.makedirs(folder_path)
    # Create a placeholder so it shows in the tree (tree requires at least one image)
    placeholder = os.path.join(folder_path, ".keep")
    with open(placeholder, "w") as f:
        f.write("")
    print(f"[CHARTS] Created folder: {year}/{month}/{safe_name}")
    return {"status": "ok", "folder": safe_name}


@app.post("/api/charts/reorder")
async def reorder_chart_folders(request: Request):
    """Save custom sort order for day folders within a month."""
    body = await request.json()
    year = body.get("year", "")
    month = body.get("month", "")
    order = body.get("order", [])  # list of folder names in desired order
    if not all([year, month]) or not isinstance(order, list):
        raise HTTPException(status_code=400, detail="year, month, order[] required")
    month_path = _safe_charts_subpath(_request_user_id(request), year, month)
    if month_path is None or not os.path.isdir(month_path):
        raise HTTPException(status_code=404, detail="Month folder not found")
    sort_file = os.path.join(month_path, "_sort_order.json")
    with open(sort_file, "w") as f:
        json.dump(order, f)
    print(f"[CHARTS] Saved custom order for {year}/{month}: {len(order)} folders")
    return {"status": "ok"}


# ── Daily Journal (localStorage-backed on frontend, JSON file backup) ─
def _validated_journal_date(date_str: str) -> str:
    try:
        return validate_journal_date(date_str)
    except JournalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/journal/list")
async def list_journals(request: Request):
    """Return list of all journal dates that have entries."""
    user_id = _request_user_id(request)
    raw_entries = await _db_mod.list_journal_entries(user_id)
    normalized_entries: list[dict] = []
    seen_dates: set[str] = set()
    for entry in raw_entries:
        normalized_date = await _normalize_journal_date_for_user(user_id, str(entry.get("date") or ""))
        if not normalized_date or normalized_date in seen_dates:
            continue
        seen_dates.add(normalized_date)
        normalized_entry = dict(entry)
        normalized_entry["date"] = normalized_date
        normalized_entries.append(normalized_entry)
    normalized_entries.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    return {"entries": normalized_entries}


@app.get("/api/journal/{date_str}")
async def get_journal(date_str: str, request: Request):
    """Load journal entry for a date (YYYY-MM-DD)."""
    date_str = _validated_journal_date(date_str)
    user_id = _request_user_id(request)
    normalized_date = await _normalize_journal_date_for_user(user_id, date_str)
    data = await _db_mod.get_journal_entry(user_id, normalized_date)
    return {"date": normalized_date, "data": data}


@app.put("/api/journal/{date_str}")
async def save_journal(date_str: str, request: Request):
    """Save journal entry for a date (YYYY-MM-DD)."""
    date_str = _validated_journal_date(date_str)
    user_id = _request_user_id(request)
    normalized_date = await _normalize_journal_date_for_user(user_id, date_str)
    body = await request.json()
    try:
        clean = clean_journal_payload(body)
    except JournalValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _db_mod.upsert_journal_entry(user_id, normalized_date, clean)
    return {"status": "ok", "date": normalized_date}


@app.delete("/api/journal/{date_str}")
async def delete_journal(date_str: str, request: Request):
    """Delete a journal entry for a date (YYYY-MM-DD)."""
    date_str = _validated_journal_date(date_str)
    user_id = _request_user_id(request)
    normalized_date = await _normalize_journal_date_for_user(user_id, date_str)
    deleted = await _db_mod.delete_journal_entry(user_id, normalized_date)
    if not deleted:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    print(f"[JOURNAL] Deleted entry for {user_id}: {normalized_date}")
    return {"status": "ok", "deleted": normalized_date}


def _default_financial_plan() -> dict:
    return {
        "monthly_expense": 0.0,
        "assets_value": 0.0,
        "years_to_reserve": 10,
        "years_to_ffv": 10,
        "monthly_income": 0.0,
        "phv_increase": 0.0,
    }


def _sanitize_financial_plan(body: dict) -> dict:
    default = _default_financial_plan()
    body = body if isinstance(body, dict) else {}

    def _clean_money(field: str) -> float:
        try:
            return round(max(0.0, float(body.get(field, default[field]) or 0.0)), 2)
        except (TypeError, ValueError):
            return float(default[field])

    def _clean_years(field: str) -> int:
        try:
            return min(max(1, int(body.get(field, default[field]) or default[field])), 50)
        except (TypeError, ValueError):
            return int(default[field])

    return {
        "monthly_expense": _clean_money("monthly_expense"),
        "assets_value": _clean_money("assets_value"),
        "years_to_reserve": _clean_years("years_to_reserve"),
        "years_to_ffv": _clean_years("years_to_ffv"),
        "monthly_income": _clean_money("monthly_income"),
        "phv_increase": _clean_money("phv_increase"),
    }


@app.get("/api/financial-plan")
async def get_financial_plan(request: Request):
    """Load the saved financial planner for the current user."""
    saved = await _db_mod.get_financial_plan(_request_user_id(request))
    if not saved:
        plan = _default_financial_plan()
    else:
        plan = _sanitize_financial_plan(saved)
        if saved.get("updated_at"):
            plan["updated_at"] = saved["updated_at"]
    return {"status": "ok", "plan": plan}


@app.put("/api/financial-plan")
async def save_financial_plan(request: Request):
    """Save the embedded financial planner for the current user."""
    body = await request.json()
    clean = _sanitize_financial_plan(body if isinstance(body, dict) else {})
    await _db_mod.upsert_financial_plan(_request_user_id(request), clean)
    return {"status": "ok", "plan": clean}


# ── Brute-Force Protection ────────────────────────────────────────
_login_attempts: dict = defaultdict(list)  # login-key -> [timestamps] (fallback)
_LOGIN_MAX_ATTEMPTS = config.MAX_LOGIN_ATTEMPTS
_LOGIN_LOCKOUT_SEC = config.LOGIN_LOCKOUT_MINUTES * 60
_LOGIN_RL_PREFIX = "philforge:login:"
_LEGACY_PIN_LENGTH = 6


def _password_policy_message(label: str = "Password") -> str:
    return f"{label} must be at least 8 characters"


def _is_valid_account_password(password: str) -> bool:
    password = str(password or "")
    return len(password) >= 8


def _require_valid_account_password(password: str, label: str = "Password") -> None:
    if not _is_valid_account_password(password):
        raise HTTPException(status_code=400, detail=_password_policy_message(label))


def _login_lockout_message() -> str:
    minutes = config.LOGIN_LOCKOUT_MINUTES
    return f"Too many failed attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}."


def _login_key(username: str, client_ip: str) -> str:
    username = (username or "").strip().lower()
    if username:
        return f"user:{username}:ip:{client_ip or 'unknown'}"
    return f"ip:{client_ip or 'unknown'}"


def _login_rate_dimensions(username: str, client_ip: str) -> list[tuple[str, int]]:
    """Limit one account/IP pair and also slow distributed spraying."""
    username = (username or "").strip().lower()
    ip = client_ip or "unknown"
    broad_limit = max(_LOGIN_MAX_ATTEMPTS * 4, 20)
    dimensions = [(_login_key(username, ip), _LOGIN_MAX_ATTEMPTS), (f"ip:{ip}", broad_limit)]
    if username:
        dimensions.append((f"account:{username}", broad_limit))
    return dimensions


def _check_login_rate(login_key: str, max_attempts: int = _LOGIN_MAX_ATTEMPTS):
    r = _get_redis()
    if r is not None:
        try:
            key = f"{_LOGIN_RL_PREFIX}{login_key}"
            count = int(r.get(key) or 0)
            if count >= max_attempts:
                raise HTTPException(status_code=429, detail=_login_lockout_message())
            return
        except HTTPException:
            raise
        except Exception as e:
            _logger.warning(f"[Redis] _check_login_rate failed, using in-memory: {e}")
    now = time.time()
    _login_attempts[login_key] = [t for t in _login_attempts[login_key] if now - t < _LOGIN_LOCKOUT_SEC]
    if len(_login_attempts[login_key]) >= max_attempts:
        raise HTTPException(status_code=429, detail=_login_lockout_message())


def _record_failed_login(login_key: str):
    r = _get_redis()
    if r is not None:
        try:
            key = f"{_LOGIN_RL_PREFIX}{login_key}"
            pipe = r.pipeline()
            pipe.incr(key)
            pipe.expire(key, _LOGIN_LOCKOUT_SEC)
            pipe.execute()
            return
        except Exception as e:
            _logger.warning(f"[Redis] _record_failed_login failed, using in-memory: {e}")
    _login_attempts[login_key].append(time.time())


def _clear_login_attempts(login_key: str):
    r = _get_redis()
    if r is not None:
        try:
            r.delete(f"{_LOGIN_RL_PREFIX}{login_key}")
            return
        except Exception:
            pass
    _login_attempts.pop(login_key, None)


# ── Authentication Endpoints ──────────────────────────────────────
@app.post("/api/auth/login")
async def auth_login(request: Request):
    ip = _request_client_ip(request)
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", body.get("pin", ""))
    login_dimensions = _login_rate_dimensions(username or config.ADMIN_USERNAME, ip)
    for login_key, max_attempts in login_dimensions:
        _check_login_rate(login_key, max_attempts)

    # If no username provided, treat as legacy PIN login → look up configured admin user
    if username:
        user = await _db_mod.get_user_by_username(username)
    else:
        user = await _get_preferred_admin_user()
    if not user:
        for login_key, _max_attempts in login_dimensions:
            _record_failed_login(login_key)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account is disabled")

    if not _auth_mod.verify_password(password, user["password_hash"]):
        for login_key, _max_attempts in login_dimensions:
            _record_failed_login(login_key)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if bool(user.get("mfa_enabled")):
        totp_code = str(body.get("totp", "") or "").strip()
        if not totp_code:
            return JSONResponse(
                status_code=428,
                content={
                    "detail": "Enter the 6-digit code from your authenticator app.",
                    "code": "mfa_required",
                },
            )
        if not await _auth_mod.verify_user_totp(user, totp_code):
            for login_key, _max_attempts in login_dimensions:
                _record_failed_login(login_key)
            raise HTTPException(status_code=401, detail="Invalid credentials or authenticator code")

    # Success — create DB session
    for login_key, _max_attempts in login_dimensions:
        if not login_key.startswith("ip:"):
            _clear_login_attempts(login_key)
    await _db_mod.cleanup_expired_sessions()
    token = await _auth_mod.create_session(user["id"])
    await _db_mod.update_last_login(user["id"])

    resp = JSONResponse(
        {
            "status": "ok",
            "message": "Login successful",
            "username": user["username"],
            "role": user["role"],
        }
    )
    resp.set_cookie(
        _SESSION_COOKIE_NAME,
        token,
        max_age=config.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=_request_is_https(request),
    )
    return resp


@app.get("/api/auth/status")
async def auth_status(request: Request):
    token = _get_session_token(request)
    session = await _validate_session_async(token)
    if not session:
        return {"authenticated": False}
    user = await _db_mod.get_user_by_id(session["user_id"])
    if not user or not user["is_active"]:
        if user:
            await _db_mod.delete_sessions_for_user(user["id"])
        elif token:
            await _db_mod.delete_session(token)
        resp = JSONResponse({"authenticated": False})
        _clear_session_cookie(resp)
        return resp
    return {
        "authenticated": True,
        "username": user["username"],
        "role": user["role"],
        "user_id": user["id"],
        "mfa_enabled": bool(user.get("mfa_enabled")),
    }


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    token = _get_session_token(request)
    await _auth_mod.destroy_session(token)
    resp = JSONResponse({"status": "ok"})
    _clear_session_cookie(resp)
    return resp


@app.post("/api/auth/mfa/enroll/start")
async def auth_mfa_enroll_start(request: Request):
    """Start authenticator enrollment without replacing a working factor."""
    user = await _auth_mod.get_current_user(request)
    if not _auth_mod.encryption_enabled():
        raise HTTPException(status_code=503, detail="Encrypted secret storage is not configured")
    body = await request.json()
    ip = _request_client_ip(request)
    manage_key = _login_key(f"mfa-manage:{user['username']}", ip)
    _check_login_rate(manage_key)
    password = str(body.get("password", "") or "")
    if not _auth_mod.verify_password(password, user["password_hash"]):
        _record_failed_login(manage_key)
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if bool(user.get("mfa_enabled")):
        if not await _auth_mod.verify_user_totp(user, str(body.get("totp", "") or "")):
            _record_failed_login(manage_key)
            raise HTTPException(status_code=401, detail="Current authenticator code is incorrect or already used")
    enrollment = _auth_mod.generate_totp_enrollment(str(user["username"]))
    await _db_mod.update_user(user["id"], mfa_pending_secret=enrollment["secret"])
    _clear_login_attempts(manage_key)
    _logger.info("[Auth] MFA enrollment started user_id=%s", user["id"])
    return {
        "status": "pending",
        "secret": enrollment["secret"],
        "otpauth_uri": enrollment["otpauth_uri"],
        "qr_data_uri": enrollment["qr_data_uri"],
        "issuer": "PhilForge",
        "account": user["username"],
    }


@app.post("/api/auth/mfa/enroll/verify")
async def auth_mfa_enroll_verify(request: Request):
    """Activate a pending authenticator secret after proving one fresh code."""
    user = await _auth_mod.get_current_user(request)
    body = await request.json()
    ip = _request_client_ip(request)
    manage_key = _login_key(f"mfa-manage:{user['username']}", ip)
    _check_login_rate(manage_key)
    password = str(body.get("password", "") or "")
    code = str(body.get("totp", "") or "")
    if not _auth_mod.verify_password(password, user["password_hash"]):
        _record_failed_login(manage_key)
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    pending_secret = str(user.get("mfa_pending_secret") or "")
    if not pending_secret:
        raise HTTPException(status_code=409, detail="Start authenticator setup first")
    if not await _auth_mod.verify_totp_enrollment(int(user["id"]), pending_secret, code):
        _record_failed_login(manage_key)
        raise HTTPException(status_code=401, detail="Authenticator code is invalid or already used")
    await _db_mod.update_user(  # nosec B106 - deliberately clearing the pending MFA secret
        user["id"],
        mfa_totp_secret=pending_secret,
        mfa_pending_secret="",
        mfa_enabled=1,
        mfa_enrolled_at=datetime.now(ZoneInfo("UTC")).isoformat(),
    )
    _clear_login_attempts(manage_key)
    _logger.info("[Auth] MFA enabled user_id=%s", user["id"])
    # Rotate this browser's session and revoke every other logged-in browser.
    # This closes the common gap where a stolen pre-enrollment session survives
    # after the account gains a second factor.
    await _db_mod.delete_sessions_for_user(int(user["id"]))
    new_session = await _auth_mod.create_session(int(user["id"]))
    response = JSONResponse({"status": "ok", "mfa_enabled": True})
    response.set_cookie(
        _SESSION_COOKIE_NAME,
        new_session,
        max_age=config.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=_request_is_https(request),
    )
    return response


@app.delete("/api/auth/mfa")
async def auth_mfa_disable(request: Request):
    """Disable MFA only after fresh password and current-factor proof."""
    user = await _auth_mod.get_current_user(request)
    body = await request.json()
    ip = _request_client_ip(request)
    manage_key = _login_key(f"mfa-manage:{user['username']}", ip)
    _check_login_rate(manage_key)
    password = str(body.get("password", "") or "")
    code = str(body.get("totp", "") or "")
    if not bool(user.get("mfa_enabled")):
        return {"status": "ok", "mfa_enabled": False}
    if not _auth_mod.verify_password(password, user["password_hash"]):
        _record_failed_login(manage_key)
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if not await _auth_mod.verify_user_totp(user, code):
        _record_failed_login(manage_key)
        raise HTTPException(status_code=401, detail="Authenticator code is invalid or already used")
    await _db_mod.update_user(  # nosec B106 - deliberately clearing enrolled and pending MFA secrets
        user["id"],
        mfa_totp_secret="",
        mfa_pending_secret="",
        mfa_enabled=0,
        mfa_enrolled_at=None,
        mfa_last_counter=-1,
    )
    _clear_login_attempts(manage_key)
    _logger.info("[Auth] MFA disabled user_id=%s", user["id"])
    await _db_mod.delete_sessions_for_user(int(user["id"]))
    response = JSONResponse({"status": "ok", "mfa_enabled": False, "reauthenticate": True})
    _clear_session_cookie(response)
    return response


@app.post("/api/auth/action-token")
async def auth_action_token(request: Request):
    """Create a session-bound, single-use token for one sensitive mutation."""
    user = await _auth_mod.get_current_user(request)
    body = await request.json()
    action_class = str(body.get("action_class", "") or "")
    target_method = str(body.get("target_method", "") or "").upper()
    target_path = str(body.get("target_path", "") or "")
    if _auth_mod.classify_sensitive_action(target_method, target_path) != action_class:
        raise HTTPException(status_code=400, detail="Unsupported action authorization target")
    if not bool(user.get("mfa_enabled")):
        raise HTTPException(status_code=428, detail="Set up an authenticator before this protected action")

    ip = _request_client_ip(request)
    step_key = _login_key(f"stepup:{user['username']}", ip)
    _check_login_rate(step_key)
    password = str(body.get("password", "") or "")
    code = str(body.get("totp", "") or "")
    if not _auth_mod.verify_password(password, user["password_hash"]):
        _record_failed_login(step_key)
        raise HTTPException(status_code=401, detail="Password or authenticator code is incorrect")
    if not await _auth_mod.verify_user_totp(user, code):
        _record_failed_login(step_key)
        raise HTTPException(status_code=401, detail="Password or authenticator code is incorrect or already used")
    _clear_login_attempts(step_key)
    session_token = _get_session_token(request)
    token, ttl = await _auth_mod.create_action_authorization(
        user_id=int(user["id"]),
        session_token=session_token,
        action_class=action_class,
        method=target_method,
        path=target_path,
    )
    _logger.info(
        "[Auth] Step-up authorized user_id=%s class=%s method=%s request_id=%s",
        user["id"],
        action_class,
        target_method,
        getattr(request.state, "request_id", ""),
    )
    return {"status": "ok", "action_token": token, "expires_in": ttl}


# ── Admin Routes ──────────────────────────────────────────────────

_ADMIN_EXAMPLE_SUFFIX = " (Admin Example)"
_ADMIN_EXAMPLE_SEED_KEY = "_example_seed"
_ADMIN_EXAMPLE_BACKTEST_LIMIT = 2
_ADMIN_EXAMPLE_MAX_BACKTEST_LIMIT = 20
_ADMIN_EXAMPLE_FOLDER = "Default"
_DEFAULT_EXAMPLES_BACKFILL_STATE_KEY = "default_examples_backfill_v2"
_ADMIN_EXAMPLE_CHART_MANIFEST = ".admin_example_chart_seed.json"


def _example_seed_meta(item: dict | None, kind: str, source_user_id: int) -> dict | None:
    if not isinstance(item, dict):
        return None
    meta = item.get(_ADMIN_EXAMPLE_SEED_KEY)
    if not isinstance(meta, dict):
        return None
    if str(meta.get("kind") or "").strip().lower() != kind:
        return None
    try:
        if int(meta.get("source_user_id") or 0) != int(source_user_id):
            return None
    except (TypeError, ValueError):
        return None
    return meta


def _normalize_admin_example_name(name: str | None, fallback: str) -> str:
    base = str(name or "").strip() or fallback
    pattern = rf"{_re.escape(_ADMIN_EXAMPLE_SUFFIX)}(?:\s+\d+)?$"
    base = _re.sub(pattern, "", base, flags=_re.IGNORECASE).strip()
    base = _re.sub(r"\s+", " ", base).strip()
    return base or fallback


def _build_admin_example_name(base_name: str | None, occupied_names: set[str], fallback: str) -> str:
    root = _normalize_admin_example_name(base_name, fallback)
    candidate = f"{root}{_ADMIN_EXAMPLE_SUFFIX}"
    index = 2
    while candidate.casefold() in occupied_names:
        candidate = f"{root}{_ADMIN_EXAMPLE_SUFFIX} {index}"
        index += 1
    return candidate


def _coerce_admin_example_ids(values) -> list[int] | None:
    if values is None:
        return None
    if not isinstance(values, list):
        raise HTTPException(status_code=400, detail="Selection ids must be a list of integers")
    ids: list[int] = []
    for value in values:
        try:
            ids.append(int(value))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Selection ids must be a list of integers") from exc
    return ids


def _is_admin_example_folder(value: str | None) -> bool:
    return str(value or "").strip().casefold() == _ADMIN_EXAMPLE_FOLDER.casefold()


def _chart_seed_manifest_path(user_id: int) -> str:
    return os.path.join(_user_charts_root(user_id), _ADMIN_EXAMPLE_CHART_MANIFEST)


def _load_chart_seed_manifest(user_id: int) -> dict:
    path = _chart_seed_manifest_path(user_id)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_chart_seed_manifest(user_id: int, data: dict) -> None:
    root = _user_charts_root(user_id)
    os.makedirs(root, exist_ok=True)
    path = _chart_seed_manifest_path(user_id)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data or {}, handle, indent=2)


def _clear_chart_seed_manifest(user_id: int) -> None:
    path = _chart_seed_manifest_path(user_id)
    if os.path.isfile(path):
        os.remove(path)


def _remove_seeded_chart_copy(user_id: int) -> None:
    manifest = _load_chart_seed_manifest(user_id)
    year = str(manifest.get("year") or "")
    month = str(manifest.get("month") or "")
    day = str(manifest.get("day") or "")
    filenames = [str(name) for name in (manifest.get("filenames") or []) if str(name)]
    for filename in filenames:
        file_path = _safe_charts_subpath(user_id, year, month, day, filename)
        if file_path and os.path.isfile(file_path):
            os.remove(file_path)

    day_path = _safe_charts_subpath(user_id, year, month, day)
    if day_path and os.path.isdir(day_path):
        leftovers = [name for name in os.listdir(day_path) if name not in {".keep", ".DS_Store"}]
        if not leftovers:
            keep_path = os.path.join(day_path, ".keep")
            if os.path.isfile(keep_path):
                os.remove(keep_path)
            if not os.listdir(day_path):
                os.rmdir(day_path)

    month_path = _safe_charts_subpath(user_id, year, month)
    if month_path and os.path.isdir(month_path) and not os.listdir(month_path):
        os.rmdir(month_path)

    year_path = _safe_charts_subpath(user_id, year)
    if year_path and os.path.isdir(year_path) and not os.listdir(year_path):
        os.rmdir(year_path)

    _clear_chart_seed_manifest(user_id)


def _latest_chart_day_snapshot(user_id: int) -> dict | None:
    charts_root = _user_charts_root(user_id)
    if not os.path.isdir(charts_root):
        return None

    latest: dict | None = None
    for year in sorted(os.listdir(charts_root)):
        year_path = os.path.join(charts_root, year)
        if not os.path.isdir(year_path) or not str(year).isdigit():
            continue
        for month_folder in os.listdir(year_path):
            month_path = os.path.join(year_path, month_folder)
            if not os.path.isdir(month_path):
                continue
            parsed_month = _parse_month_folder(month_folder)
            if parsed_month is None:
                continue
            month_num, month_label = parsed_month
            for day_folder in os.listdir(month_path):
                day_path = os.path.join(month_path, day_folder)
                if not os.path.isdir(day_path):
                    continue
                images = sorted(
                    name for name in os.listdir(day_path) if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                )
                if not images:
                    continue
                day_sort, day_label = _parse_day_folder(day_folder, year_hint=int(year), month_hint=int(month_num))
                day_num = 0
                match = _re.match(r"^\d{4}-\d{2}-(\d{2})$", str(day_sort))
                if match:
                    day_num = int(match.group(1))
                sort_tuple = (int(year), int(month_num), int(day_num), str(day_folder).casefold())
                snapshot = {
                    "year": str(year),
                    "month": str(month_folder),
                    "month_label": month_label,
                    "day": str(day_folder),
                    "day_label": day_label,
                    "images": images,
                    "path": day_path,
                    "sort_tuple": sort_tuple,
                }
                if latest is None or snapshot["sort_tuple"] > latest["sort_tuple"]:
                    latest = snapshot
    return latest


async def _copy_example_strategies(
    source_user_id: int,
    target_user_id: int,
    strategy_ids: list[int] | None = None,
) -> dict:
    selected_ids = {int(item_id) for item_id in (strategy_ids or [])}
    source_items = await _db_mod.list_strategies(source_user_id)
    source_items = [
        item for item in source_items if not item.get("_placeholder") and _is_admin_example_folder(item.get("folder"))
    ]
    if selected_ids:
        source_items = [item for item in source_items if int(item.get("id") or 0) in selected_ids]

    target_items = await _db_mod.list_strategies(target_user_id)
    occupied_names = {
        str(item.get("run_name") or item.get("name") or "").strip().casefold()
        for item in target_items
        if str(item.get("run_name") or item.get("name") or "").strip()
    }
    seeded_by_source: dict[int, dict] = {}
    for item in target_items:
        meta = _example_seed_meta(item, "strategy", source_user_id)
        if not meta:
            continue
        try:
            seeded_by_source[int(meta.get("source_id") or 0)] = item
        except (TypeError, ValueError):
            continue

    copied = 0
    names: list[str] = []
    now = str(datetime.now())

    for source_item in source_items:
        source_id = int(source_item.get("id") or 0)
        existing = seeded_by_source.get(source_id)
        reserved_names = set(occupied_names)
        if existing:
            current_name = str(existing.get("run_name") or existing.get("name") or "").strip().casefold()
            if current_name:
                reserved_names.discard(current_name)
                occupied_names.discard(current_name)

        source_name = str(source_item.get("run_name") or source_item.get("name") or "").strip()
        target_name = _build_admin_example_name(source_name, reserved_names, "Untitled Strategy")

        payload = deepcopy(source_item)
        payload.pop("id", None)
        payload["run_name"] = target_name
        payload["name"] = target_name
        payload["updated_at"] = now
        payload[_ADMIN_EXAMPLE_SEED_KEY] = {
            "kind": "strategy",
            "source_user_id": int(source_user_id),
            "source_id": source_id,
        }

        if existing:
            payload["created_at"] = existing.get("created_at") or now
            await _db_mod.replace_strategy_record(target_user_id, int(existing["id"]), payload)
        else:
            payload["created_at"] = now
            await _db_mod.create_strategy_record(target_user_id, payload)

        occupied_names.add(target_name.casefold())
        names.append(target_name)
        copied += 1

    return {"copied": copied, "names": names}


async def _copy_example_backtests(
    source_user_id: int,
    target_user_id: int,
    run_ids: list[int] | None = None,
    backtest_limit: int = _ADMIN_EXAMPLE_BACKTEST_LIMIT,
) -> dict:
    selected_ids = {int(item_id) for item_id in (run_ids or [])}
    source_runs = [
        run
        for run in await _db_mod.list_runs(source_user_id)
        if str(run.get("mode") or "").lower() == "backtest" and _is_admin_example_folder(run.get("folder"))
    ]
    if selected_ids:
        source_runs = [run for run in source_runs if int(run.get("id") or 0) in selected_ids]
    else:
        source_runs.sort(key=lambda run: (str(run.get("created_at") or ""), int(run.get("id") or 0)), reverse=True)
        source_runs = source_runs[:backtest_limit]

    target_runs = [
        run for run in await _db_mod.list_runs(target_user_id) if str(run.get("mode") or "").lower() == "backtest"
    ]
    occupied_names = {
        str(run.get("run_name") or run.get("strategy_name") or "").strip().casefold()
        for run in target_runs
        if str(run.get("run_name") or run.get("strategy_name") or "").strip()
    }
    seeded_by_source: dict[int, dict] = {}
    for run in target_runs:
        meta = _example_seed_meta(run, "run", source_user_id)
        if not meta:
            continue
        try:
            seeded_by_source[int(meta.get("source_id") or 0)] = run
        except (TypeError, ValueError):
            continue

    copied = 0
    names: list[str] = []
    now = str(datetime.now())

    for source_run in source_runs:
        source_id = int(source_run.get("id") or 0)
        existing = seeded_by_source.get(source_id)
        reserved_names = set(occupied_names)
        if existing:
            current_name = str(existing.get("run_name") or existing.get("strategy_name") or "").strip().casefold()
            if current_name:
                reserved_names.discard(current_name)
                occupied_names.discard(current_name)

        source_name = str(source_run.get("run_name") or source_run.get("strategy_name") or "").strip()
        target_name = _build_admin_example_name(source_name, reserved_names, "Untitled Backtest")

        payload = deepcopy(source_run)
        payload.pop("id", None)
        payload["run_name"] = target_name
        payload["strategy_name"] = target_name
        payload["mode"] = "backtest"
        payload["created_at"] = existing.get("created_at") if existing else now
        payload[_ADMIN_EXAMPLE_SEED_KEY] = {
            "kind": "run",
            "source_user_id": int(source_user_id),
            "source_id": source_id,
        }

        if existing:
            await _db_mod.replace_run_record(target_user_id, int(existing["id"]), payload)
        else:
            await _db_mod.create_run_record(target_user_id, payload)

        occupied_names.add(target_name.casefold())
        names.append(target_name)
        copied += 1

    return {"copied": copied, "names": names}


async def _remove_seeded_journal_examples(source_user_id: int, target_user_id: int) -> int:
    removed = 0
    for entry in await _db_mod.list_journal_entries(target_user_id):
        entry_date = str(entry.get("date") or "")
        if not entry_date:
            continue
        data = await _db_mod.get_journal_entry(target_user_id, entry_date) or {}
        if _example_seed_meta(data, "journal", source_user_id):
            deleted = await _db_mod.delete_journal_entry(target_user_id, entry_date)
            if deleted:
                removed += 1
    return removed


async def _copy_latest_chart_day_example(
    source_user_id: int,
    target_user_id: int,
) -> dict:
    source_snapshot = _latest_chart_day_snapshot(source_user_id)
    if not source_snapshot:
        _remove_seeded_chart_copy(target_user_id)
        return {"copied": 0, "source_date": None, "target_date": None, "images": 0}

    _remove_seeded_chart_copy(target_user_id)

    target_day_path = _safe_charts_subpath(
        target_user_id,
        source_snapshot["year"],
        source_snapshot["month"],
        source_snapshot["day"],
        create_root=True,
    )
    if target_day_path is None:
        raise HTTPException(status_code=400, detail="Invalid chart target path")
    os.makedirs(target_day_path, exist_ok=True)

    copied_filenames: list[str] = []
    for image_name in source_snapshot["images"]:
        source_file_path = os.path.join(source_snapshot["path"], image_name)
        seeded_name = f"AdminExample_{os.path.basename(image_name)}"
        candidate_name = seeded_name
        counter = 2
        while os.path.exists(os.path.join(target_day_path, candidate_name)):
            candidate_name = f"AdminExample_{counter}_{os.path.basename(image_name)}"
            counter += 1
        shutil.copy2(source_file_path, os.path.join(target_day_path, candidate_name))
        copied_filenames.append(candidate_name)

    if copied_filenames:
        _save_chart_seed_manifest(
            target_user_id,
            {
                "source_user_id": int(source_user_id),
                "year": source_snapshot["year"],
                "month": source_snapshot["month"],
                "day": source_snapshot["day"],
                "filenames": copied_filenames,
            },
        )
    else:
        _clear_chart_seed_manifest(target_user_id)

    source_date = f"{source_snapshot['year']}-{source_snapshot['month']}/{source_snapshot['day']}"
    return {
        "copied": 1 if copied_filenames else 0,
        "source_date": source_date,
        "target_date": source_date,
        "images": len(copied_filenames),
    }


async def _copy_admin_examples_to_user(
    source_user_id: int,
    target_user_id: int,
    *,
    strategy_ids: list[int] | None = None,
    run_ids: list[int] | None = None,
    include_strategies: bool = True,
    include_backtests: bool = True,
    include_charts: bool = True,
    backtest_limit: int = _ADMIN_EXAMPLE_BACKTEST_LIMIT,
) -> dict:
    result = {
        "strategies": {"copied": 0, "names": []},
        "backtests": {"copied": 0, "names": []},
        "charts": {"copied": 0, "source_date": None, "target_date": None, "images": 0},
        "removed_seeded_journals": 0,
    }
    if include_strategies:
        result["strategies"] = await _copy_example_strategies(source_user_id, target_user_id, strategy_ids)
    if include_backtests:
        result["backtests"] = await _copy_example_backtests(
            source_user_id,
            target_user_id,
            run_ids,
            backtest_limit=backtest_limit,
        )
    result["removed_seeded_journals"] = await _remove_seeded_journal_examples(source_user_id, target_user_id)
    if include_charts:
        result["charts"] = await _copy_latest_chart_day_example(source_user_id, target_user_id)
    return result


async def _backfill_default_examples_for_existing_users_once() -> dict:
    """Seed Default-folder examples to pre-existing non-admin users once per deployment version."""
    existing_state = await _db_mod.get_app_state(_DEFAULT_EXAMPLES_BACKFILL_STATE_KEY)
    if str(existing_state or "").strip().lower() == "done":
        return {"status": "skipped", "processed_users": 0, "seeded_users": 0}

    admin = await _get_preferred_admin_user()
    if not admin:
        raise RuntimeError("No admin user available for Default example backfill")

    users = await _db_mod.list_users()
    processed_users = 0
    seeded_users = 0

    for user in users:
        if str(user.get("role") or "").lower() == "admin":
            continue
        processed_users += 1
        copied = await _copy_admin_examples_to_user(int(admin["id"]), int(user["id"]))
        copied_total = (
            int(copied["strategies"]["copied"]) + int(copied["backtests"]["copied"]) + int(copied["charts"]["copied"])
        )
        if copied_total > 0:
            seeded_users += 1

    await _db_mod.set_app_state(_DEFAULT_EXAMPLES_BACKFILL_STATE_KEY, "done")
    return {"status": "done", "processed_users": processed_users, "seeded_users": seeded_users}


@app.get("/api/admin/users")
async def admin_list_users(request: Request):
    """List all users (admin only)."""
    await _auth_mod.require_admin(request)
    users = await _db_mod.list_users()
    return {"users": users}


@app.post("/api/admin/users")
async def admin_create_user(request: Request):
    """Create a new user (admin only)."""
    admin = await _auth_mod.require_admin(request)
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    role = body.get("role", "user")
    email = body.get("email", "").strip() or None

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")
    _require_valid_account_password(password)

    # Check if username already exists
    existing = await _db_mod.get_user_by_username(username)
    if existing:
        raise HTTPException(status_code=409, detail=f"Username '{username}' already taken")

    hashed = _auth_mod.hash_password(password)
    user_id = await _db_mod.create_user(username, hashed, role=role, email=email)
    copied = {
        "strategies": {"copied": 0, "names": []},
        "backtests": {"copied": 0, "names": []},
        "charts": {"copied": 0, "source_date": None, "target_date": None, "images": 0},
        "removed_seeded_journals": 0,
    }
    if role == "user":
        copied = await _copy_admin_examples_to_user(int(admin["id"]), int(user_id))
    _logger.info(f"[Admin] User '{username}' created by '{admin['username']}' (id={user_id})")
    return {
        "status": "ok",
        "user_id": user_id,
        "username": username,
        "role": role,
        "copied": {
            "strategies": copied["strategies"]["copied"],
            "backtests": copied["backtests"]["copied"],
            "charts": copied["charts"]["copied"],
            "journal": 0,
        },
        "chart_source_date": copied["charts"]["source_date"],
        "chart_target_date": copied["charts"]["target_date"],
    }


@app.put("/api/admin/users/{user_id}/toggle")
async def admin_toggle_user(user_id: int, request: Request):
    """Enable or disable a user account (admin only)."""
    admin = await _auth_mod.require_admin(request)
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot disable your own account")
    user = await _db_mod.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    new_state = not bool(user["is_active"])
    await _db_mod.set_user_active(user_id, new_state)
    if not new_state:
        await _db_mod.delete_sessions_for_user(user_id)
    action = "enabled" if new_state else "disabled"
    _logger.info(f"[Admin] User '{user['username']}' {action} by '{admin['username']}'")
    return {"status": "ok", "user_id": user_id, "is_active": new_state}


@app.put("/api/admin/users/{user_id}/password")
async def admin_reset_password(user_id: int, request: Request):
    """Reset a user's password (admin only)."""
    await _auth_mod.require_admin(request)
    body = await request.json()
    new_password = body.get("password", "")
    _require_valid_account_password(new_password)
    user = await _db_mod.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    hashed = _auth_mod.hash_password(new_password)
    await _db_mod.update_user(user_id, password_hash=hashed)
    await _db_mod.delete_sessions_for_user(user_id)
    return {"status": "ok", "message": f"Password reset for '{user['username']}'"}


@app.post("/api/admin/users/{user_id}/copy-examples")
async def admin_copy_examples_to_user(user_id: int, request: Request):
    """Copy admin-owned example strategies, latest chart day, and backtests to another user."""
    admin = await _auth_mod.require_admin(request)
    if int(user_id) == int(admin["id"]):
        raise HTTPException(status_code=400, detail="Choose another user account")

    target_user = await _db_mod.get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid request body")

    include_strategies = bool(body.get("include_strategies", True))
    include_backtests = bool(body.get("include_backtests", True))
    include_charts = bool(body.get("include_charts", body.get("include_journal", True)))
    if not any((include_strategies, include_backtests, include_charts)):
        raise HTTPException(status_code=400, detail="Select at least one example type to copy")

    strategy_ids = _coerce_admin_example_ids(body.get("strategy_ids"))
    run_ids = _coerce_admin_example_ids(body.get("run_ids"))

    try:
        backtest_limit = int(body.get("backtest_limit", _ADMIN_EXAMPLE_BACKTEST_LIMIT) or _ADMIN_EXAMPLE_BACKTEST_LIMIT)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="backtest_limit must be an integer") from exc
    backtest_limit = max(1, min(backtest_limit, _ADMIN_EXAMPLE_MAX_BACKTEST_LIMIT))

    copied = await _copy_admin_examples_to_user(
        int(admin["id"]),
        int(user_id),
        strategy_ids=strategy_ids,
        run_ids=run_ids,
        include_strategies=include_strategies,
        include_backtests=include_backtests,
        include_charts=include_charts,
        backtest_limit=backtest_limit,
    )
    return {
        "status": "ok",
        "target_user_id": int(target_user["id"]),
        "target_username": target_user["username"],
        "source_user_id": int(admin["id"]),
        "source_username": admin["username"],
        "copied": {
            "strategies": copied["strategies"]["copied"],
            "backtests": copied["backtests"]["copied"],
            "charts": copied["charts"]["copied"],
            "journal": 0,
        },
        "chart_source_date": copied["charts"]["source_date"],
        "chart_target_date": copied["charts"]["target_date"],
        "removed_seeded_journals": copied["removed_seeded_journals"],
    }


# ── User Self-Service Routes ─────────────────────────────────────


@app.put("/api/user/password")
async def change_own_password(request: Request):
    """Change your own password."""
    user = await _auth_mod.get_current_user(request)
    body = await request.json()
    current = body.get("current_password", "")
    new_pw = body.get("new_password", "")

    if not _auth_mod.verify_password(current, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    _require_valid_account_password(new_pw, "New password")

    hashed = _auth_mod.hash_password(new_pw)
    await _db_mod.update_user(user["id"], password_hash=hashed)
    await _db_mod.delete_sessions_for_user(user["id"])
    resp = JSONResponse({"status": "ok", "message": "Password changed. Please log in again."})
    _clear_session_cookie(resp)
    return resp


@app.get("/api/user/profile")
async def get_user_profile(request: Request):
    """Return authenticated user profile + broker settings metadata."""
    user = await _auth_mod.get_current_user(request)
    return {
        "status": "ok",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user.get("email"),
            "role": user["role"],
            "is_active": bool(user.get("is_active", 1)),
            "created_at": user.get("created_at"),
            "last_login": user.get("last_login"),
            "mfa_enabled": bool(user.get("mfa_enabled")),
            "mfa_enrolled_at": user.get("mfa_enrolled_at"),
        },
        "broker": _broker_profile_payload(user),
    }


@app.get("/api/user/execution-ip-status")
async def get_user_execution_ip_status(request: Request):
    """Return the active broker source plus server-vs-Dhan static IP status."""
    user = await _auth_mod.get_current_user(request)
    broker_client, source = _resolve_user_broker_client(user, allow_admin_fallback=True)
    status = await asyncio.to_thread(_build_execution_ip_status, user, broker_client, source)
    return {"status": "ok", **status}


@app.put("/api/user/broker")
async def update_own_broker_settings(request: Request):
    """Create or update stored broker credentials for the current user."""
    user = await _auth_mod.get_current_user(request)
    locked, reason = _user_broker_settings_lock(int(user["id"]))
    if locked:
        raise HTTPException(status_code=409, detail=reason)
    if not _auth_mod.encryption_enabled():
        raise HTTPException(
            status_code=503,
            detail="Broker credential storage is disabled until ENCRYPTION_KEY is configured on the server.",
        )

    body = await request.json()
    client_id_input = body.get("client_id")
    access_token_input = body.get("access_token")
    pin_input = body.get("pin")
    totp_input = body.get("totp_secret")

    current_client_id = str(user.get("dhan_client_id", "") or "").strip()
    current_access_token = str(user.get("dhan_access_token", "") or "").strip()
    current_pin = str(user.get("dhan_pin", "") or "").strip()
    current_totp = str(user.get("dhan_totp_secret", "") or "").strip()

    new_client_id = current_client_id if client_id_input is None else str(client_id_input or "").strip()
    new_access_token = current_access_token if access_token_input is None else str(access_token_input or "").strip()
    new_pin = current_pin if pin_input is None else str(pin_input or "").strip()
    new_totp = current_totp if totp_input is None else str(totp_input or "").strip()

    if not (new_client_id or new_access_token or new_pin or new_totp):
        raise HTTPException(status_code=400, detail="Provide broker credentials to save, or use Clear to remove them.")
    if bool(new_client_id) != bool(new_access_token):
        raise HTTPException(status_code=400, detail="Both Client ID and Access Token are required together.")
    if (new_pin or new_totp) and not (new_client_id and new_access_token):
        raise HTTPException(
            status_code=400, detail="PIN/TOTP can only be saved together with Client ID and Access Token."
        )

    await _db_mod.update_user(
        user["id"],
        dhan_client_id=new_client_id,
        dhan_access_token=new_access_token,
        dhan_pin=new_pin,
        dhan_totp_secret=new_totp,
    )
    fresh_user = await _db_mod.get_user_by_id(user["id"])
    return {
        "status": "ok",
        "message": "Broker credentials saved.",
        "broker": _broker_profile_payload(fresh_user),
    }


@app.delete("/api/user/broker")
async def clear_own_broker_settings(request: Request):
    """Remove stored broker credentials for the current user."""
    user = await _auth_mod.get_current_user(request)
    locked, reason = _user_broker_settings_lock(int(user["id"]))
    if locked:
        raise HTTPException(status_code=409, detail=reason)

    cleared_fields = {key: str() for key in ("dhan_client_id", "dhan_access_token", "dhan_pin", "dhan_totp_secret")}
    await _db_mod.update_user(user["id"], **cleared_fields)
    fresh_user = await _db_mod.get_user_by_id(user["id"])
    return {
        "status": "ok",
        "message": "Stored broker credentials cleared.",
        "broker": _broker_profile_payload(fresh_user),
    }


def _runtime_owner_ids() -> set[int]:
    return (
        set(paper_engines)
        | set(live_engines)
        | set(_scalp_engines)
        | set(_cascade_engines)
        | set(_candle_entry_engines)
        | set(_fib_boundary_engines)
        | set(_terminal_cascade_engines)
    )


def _runtime_control_summary(owner_id: int) -> dict:
    paper_running = sum(
        1 for engine in _registry_bucket(paper_engines, owner_id).values() if getattr(engine, "running", False)
    )
    live_running = sum(
        1 for engine in _registry_bucket(live_engines, owner_id).values() if getattr(engine, "running", False)
    )
    scalp = _scalp_engines.get(owner_id)
    scalp_open = list(getattr(scalp, "open_trades", {}).values()) if scalp else []
    scalp_running = bool(scalp and getattr(scalp, "_running", False))
    cascade_running = bool(_cascade_engines.get(owner_id) and _cascade_engines[owner_id].running)
    candle_running = bool(_candle_entry_engines.get(owner_id) and _candle_entry_engines[owner_id].running)
    fib_running = sum(1 for runtime in _fib_boundary_engines.get(owner_id, {}).values() if runtime.running)
    terminal_running = sum(1 for runtime in _terminal_cascade_engines.get(owner_id, {}).values() if runtime.running)
    any_running = bool(
        paper_running
        or live_running
        or scalp_running
        or scalp_open
        or cascade_running
        or candle_running
        or fib_running
        or terminal_running
    )
    return {
        "user_id": owner_id,
        "paper_running": paper_running,
        "live_running": live_running,
        "scalp_running": scalp_running,
        "scalp_open_trades": len(scalp_open),
        "scalp_live_open_trades": sum(1 for trade in scalp_open if _trade_mode_value(trade) == "live"),
        "cascade_running": cascade_running,
        "candle_entry_running": candle_running,
        "fib_boundary_running": fib_running,
        "terminal_cascade_running": terminal_running,
        "any_running": any_running,
    }


@app.get("/api/engine-control/status")
async def engine_control_status(request: Request):
    """Pure, canonical kill-switch visibility status across every runtime family."""
    user = getattr(request.state, "current_user", {}) or {}
    caller_id = _request_user_id(request)
    owner_ids = sorted(_runtime_owner_ids()) if user.get("role") == "admin" else [caller_id]
    if caller_id not in owner_ids:
        owner_ids.append(caller_id)
    users = [_runtime_control_summary(owner_id) for owner_id in sorted(owner_ids)]
    return {"status": "ok", "any_running": any(row["any_running"] for row in users), "users": users}


@app.get("/api/admin/engines")
async def admin_list_engine_status(request: Request):
    """Summarize running engines across users (admin only)."""
    await _auth_mod.require_admin(request)
    known_users = {int(user["id"]): user for user in await _db_mod.list_users()}
    owner_ids = sorted(set(known_users) | _runtime_owner_ids())
    rows: list[dict] = []

    for owner_id in owner_ids:
        user = known_users.get(owner_id) or {
            "id": owner_id,
            "username": f"User {owner_id}",
            "role": "user",
            "is_active": True,
        }
        paper_runs = [
            _engine_status_summary(engine, run_id, "paper")
            for run_id, engine in _registry_bucket(paper_engines, owner_id).items()
            if getattr(engine, "running", False)
        ]
        live_runs = [
            _engine_status_summary(engine, run_id, "live")
            for run_id, engine in _registry_bucket(live_engines, owner_id).items()
            if getattr(engine, "running", False)
        ]
        scalp_engine = _scalp_engines.get(owner_id)
        scalp_open = list(getattr(scalp_engine, "open_trades", {}).values()) if scalp_engine else []
        scalp_live_open = sum(1 for trade in scalp_open if _trade_mode_value(trade) == "live")
        control = _runtime_control_summary(owner_id)
        rows.append(
            {
                "user_id": owner_id,
                "username": user["username"],
                "role": user.get("role", "user"),
                "is_active": bool(user.get("is_active", 1)),
                "paper_running": len(paper_runs),
                "live_running": len(live_runs),
                "scalp_running": bool(scalp_engine and getattr(scalp_engine, "_running", False)),
                "scalp_open_trades": len(scalp_open),
                "scalp_live_open_trades": scalp_live_open,
                "cascade_running": control["cascade_running"],
                "candle_entry_running": control["candle_entry_running"],
                "fib_boundary_running": control["fib_boundary_running"],
                "terminal_cascade_running": control["terminal_cascade_running"],
                "any_running": control["any_running"],
                "paper_runs": paper_runs,
                "live_runs": live_runs,
            }
        )

    return {"status": "ok", "users": rows}


# ── Emergency Stop (Kill Switch) ─────────────────────────────────
@app.post("/api/emergency-stop")
async def emergency_stop(request: Request):
    """Kill switch: stop ALL running strategies immediately"""
    token = _get_session_token(request)
    if not await _validate_session_async(token):
        raise HTTPException(status_code=401, detail="Unauthorized")

    results = {}
    stopped_count = 0
    user = getattr(request.state, "current_user", {}) or {}
    caller_id = _request_user_id(request)
    if user.get("role") == "admin":
        target_user_ids = sorted(_runtime_owner_ids())
    else:
        target_user_ids = [caller_id]

    # Stop all paper engines for target users
    for owner_id in target_user_ids:
        paper_bucket = _registry_bucket(paper_engines, owner_id)
        for run_id, engine in list(paper_bucket.items()):
            try:
                if engine.running:
                    engine.stop()
                    results[f"paper:{owner_id}:{run_id}"] = "stopped"
                    stopped_count += 1
                else:
                    results[f"paper:{owner_id}:{run_id}"] = "not_running"
                _alert_state.pop(_alert_state_key(owner_id, run_id), None)
            except Exception as e:
                results[f"paper:{owner_id}:{run_id}"] = f"error: {str(e)}"

    # Stop all live engines for target users
    for owner_id in target_user_ids:
        live_bucket = _registry_bucket(live_engines, owner_id)
        for run_id, engine in list(live_bucket.items()):
            try:
                if engine.running:
                    sqoff = await _square_off_live_engine_positions(engine, owner_id, run_id, reason="EMERGENCY_STOP")
                    if not sqoff.get("ok"):
                        results[f"live:{owner_id}:{run_id}"] = {
                            "status": sqoff.get("status", "error"),
                            "message": "Emergency stop could not confirm broker square-off. Engine left running.",
                            "square_off": sqoff,
                        }
                        continue
                    engine.stop()
                    results[f"live:{owner_id}:{run_id}"] = {"status": "stopped", "square_off": sqoff}
                    stopped_count += 1
                else:
                    results[f"live:{owner_id}:{run_id}"] = "not_running"
                _alert_state.pop(_alert_state_key(owner_id, run_id), None)
            except Exception as e:
                results[f"live:{owner_id}:{run_id}"] = f"error: {str(e)}"

    # Stop all scalp engines for target users
    for owner_id in target_user_ids:
        eng = _scalp_engines.get(owner_id)
        if not eng:
            continue
        try:
            had_open_trades = bool(getattr(eng, "open_trades", {}) or {})
            if getattr(eng, "_running", False) or had_open_trades:
                sqoff = await _square_off_scalp_engine_trades(eng)
                if not sqoff.get("ok"):
                    results[f"scalp:{owner_id}"] = {
                        "status": sqoff.get("status", "error"),
                        "message": "Emergency stop could not confirm scalp broker exits. Engine left running.",
                        "square_off": sqoff,
                    }
                    await _save_scalp_open_state(owner_id, eng, force=True)
                    continue
                eng.stop()
                await _save_scalp_open_state(owner_id, eng, force=True)
                results[f"scalp:{owner_id}"] = {"status": "stopped", "square_off": sqoff}
                stopped_count += 1
            else:
                results[f"scalp:{owner_id}"] = "not_running"
        except Exception as e:
            results[f"scalp:{owner_id}"] = f"error: {str(e)}"

    # Stop every paper-only Cascade family. A missing exit quote must leave an
    # open paper basket monitored instead of reporting a false successful kill.
    for owner_id in target_user_ids:
        runtime = _cascade_engines.get(owner_id)
        if runtime and runtime.running:
            try:
                try:
                    ticker = await asyncio.to_thread(runtime.adapter.get_ticker, "NIFTY")
                    index_price = float(ticker["last_price"])
                except Exception:
                    index_price = float(runtime.engine.geometry.history[-1].close)
                now = datetime.now(IST)
                outcome = runtime.engine.kill_and_close(
                    IndexCandle(now, index_price, index_price, index_price, index_price)
                )
                if not outcome.get("closed"):
                    results[f"cascade:{owner_id}"] = "exit_quote_unavailable_engine_left_running"
                else:
                    runtime.running = False
                    if runtime.task and not runtime.task.done():
                        runtime.task.cancel()
                    stopped_count += 1
                    results[f"cascade:{owner_id}"] = "stopped"
                await _save_cascade_open_state(owner_id, runtime, force=True)
            except Exception as exc:
                results[f"cascade:{owner_id}"] = f"error: {exc}"

        candle = _candle_entry_engines.get(owner_id)
        if candle and candle.running:
            try:
                now = datetime.now(IST)
                try:
                    ticker = await asyncio.to_thread(candle.adapter.get_ticker, "NIFTY")
                    index_price = float(ticker["last_price"])
                except Exception:
                    index_price = float(candle.engine.last_index_close)
                if candle.engine.kill_and_close(IndexCandle(now, index_price, index_price, index_price, index_price)):
                    candle.running = False
                    if candle.task and not candle.task.done():
                        candle.task.cancel()
                    stopped_count += 1
                    results[f"candle-entry:{owner_id}"] = "stopped"
                else:
                    results[f"candle-entry:{owner_id}"] = "exit_quote_unavailable_engine_left_running"
                await _save_candle_entry_open_state(owner_id, force=True)
            except Exception as exc:
                results[f"candle-entry:{owner_id}"] = f"error: {exc}"

        fib_ladders = _fib_boundary_engines.get(owner_id, {})
        for fib_symbol, fib in list(fib_ladders.items()):
            if not fib.running:
                continue
            try:
                now = datetime.now(IST)
                try:
                    # The ladder's OWN index, not NIFTY: a BANKNIFTY basket
                    # closed at a NIFTY print is a fabricated exit.
                    ticker = await asyncio.to_thread(fib.adapter.get_ticker, fib_symbol)
                    index_price = float(ticker["last_price"])
                except Exception:
                    index_price = float(fib.engine.history[-1].close)
                if fib.engine.kill_and_close(IndexCandle(now, index_price, index_price, index_price, index_price)):
                    fib.running = False
                    if fib.task and not fib.task.done():
                        fib.task.cancel()
                    stopped_count += 1
                    results[f"fib-boundary:{owner_id}:{fib_symbol}"] = "stopped"
                else:
                    results[f"fib-boundary:{owner_id}:{fib_symbol}"] = "exit_quote_unavailable_engine_left_running"
            except Exception as exc:
                results[f"fib-boundary:{owner_id}:{fib_symbol}"] = f"error: {exc}"
        if fib_ladders:
            await _save_fib_boundary_open_state(owner_id, force=True)

        terminal = _terminal_cascade_engines.get(owner_id, {})
        for symbol, terminal_runtime in list(terminal.items()):
            if not terminal_runtime.running:
                continue
            try:
                signal, trade = await _terminal_cascade_quote_pair(terminal_runtime)
                terminal_runtime.engine.kill_and_close(signal, trade)
                terminal_runtime.running = False
                if terminal_runtime.task and not terminal_runtime.task.done():
                    terminal_runtime.task.cancel()
                stopped_count += 1
                results[f"terminal-cascade:{owner_id}:{symbol}"] = "stopped"
            except Exception as exc:
                results[f"terminal-cascade:{owner_id}:{symbol}"] = {
                    "status": "error",
                    "message": "Exit quote unavailable; campaign left running.",
                    "detail": str(exc),
                }
        if terminal:
            await _save_terminal_cascade_open_state(owner_id, force=True)

    # Cancel background tasks and clear stopped registries for target users
    for owner_id in target_user_ids:
        for tasks_dict in (_live_tasks, _paper_tasks):
            task_bucket = _registry_bucket(tasks_dict, owner_id)
            engine_bucket = _registry_bucket(live_engines if tasks_dict is _live_tasks else paper_engines, owner_id)
            for run_id, task_ref in list(task_bucket.items()):
                engine = engine_bucket.get(run_id)
                if engine and getattr(engine, "running", False):
                    continue
                if task_ref and not task_ref.done():
                    task_ref.cancel()
                    try:
                        await task_ref
                    except asyncio.CancelledError:
                        pass
                task_bucket.pop(run_id, None)
        for registry in (live_engines, paper_engines):
            bucket = _registry_bucket(registry, owner_id)
            for run_id, engine in list(bucket.items()):
                if not getattr(engine, "running", False):
                    bucket.pop(run_id, None)
        scalp_engine = _scalp_engines.get(owner_id)
        if (
            scalp_engine
            and not getattr(scalp_engine, "_running", False)
            and not (getattr(scalp_engine, "open_trades", {}) or {})
        ):
            _scalp_engines.pop(owner_id, None)

    return {
        "status": "ok",
        "stopped": stopped_count,
        "message": f"Emergency stop executed — {stopped_count} engine(s) stopped",
        "results": results,
        "timestamp": str(datetime.now()),
    }


# ── Dashboard Summary ─────────────────────────────────────────────
def _dashboard_trade_signature_text(trade: dict) -> str:
    sig = _history_trade_signature(trade)
    if not sig:
        return ""
    return json.dumps(sig, sort_keys=True, default=str)


def _dashboard_trade_display_symbol(trade: dict) -> str:
    if not isinstance(trade, dict):
        return "—"
    underlying = str(trade.get("underlying") or trade.get("underlying_symbol") or "").strip()
    strike = str(trade.get("strike") or "").strip()
    option_type = str(trade.get("option_type") or "").strip().upper()
    if underlying and strike and option_type:
        return f"{underlying} {strike} {option_type}".strip()

    if underlying and strike:
        return f"{underlying} {strike}".strip()

    raw = str(trade.get("symbol") or trade.get("trading_symbol") or trade.get("customSymbol") or "").strip()
    if not raw:
        return "—"

    cleaned = re.sub(r"\d{4}-\d{2}-\d{2}$", "", raw).strip()
    m = re.match(r"^([A-Z]+)\s*(\d{3,6})\s*(CE|PE)$", cleaned.replace(" ", ""), re.IGNORECASE)
    if m:
        return f"{m.group(1).upper()} {m.group(2)} {m.group(3).upper()}"
    return cleaned


def _recompute_run_trade_summary(run: dict, trades: list[dict]) -> dict:
    closed = [dict(trade or {}) for trade in (trades or []) if isinstance(trade, dict)]
    pnls = [round(float((trade or {}).get("pnl") or 0), 2) for trade in closed]
    winners = [pnl for pnl in pnls if pnl > 0]
    losers = [pnl for pnl in pnls if pnl <= 0]
    total_pnl = round(sum(pnls), 2)
    win_rate = round(len(winners) / len(closed) * 100, 2) if closed else 0.0
    profit_factor = round(sum(winners) / abs(sum(losers)), 2) if losers and abs(sum(losers)) > 0 else 999.0
    avg_win = round(sum(winners) / len(winners), 2) if winners else 0.0
    avg_loss = round(sum(losers) / len(losers), 2) if losers else 0.0

    win_streak = 0
    loss_streak = 0
    current_win = 0
    current_loss = 0
    for pnl in pnls:
        if pnl > 0:
            current_win += 1
            current_loss = 0
        else:
            current_loss += 1
            current_win = 0
        win_streak = max(win_streak, current_win)
        loss_streak = max(loss_streak, current_loss)

    summary = dict(run.get("stats") or run.get("summary") or {})
    summary.update(
        {
            "total_trades": len(closed),
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_profit": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "max_profit": round(max(pnls), 2) if pnls else 0.0,
            "max_loss": round(min(pnls), 2) if pnls else 0.0,
            "win_streak": win_streak,
            "loss_streak": loss_streak,
        }
    )
    run["trade_count"] = len(closed)
    run["total_pnl"] = total_pnl
    run["trades"] = closed
    run["stats"] = summary
    run["summary"] = summary
    return run


async def _delete_dashboard_recent_transaction_items(user_id: int, items: list[dict]) -> dict:
    deleted = 0
    skipped = 0
    scalp_deleted_ids: list[int] = []
    for raw in items or []:
        item = raw if isinstance(raw, dict) else {}
        source_kind = str(item.get("source_kind") or "").strip().lower()
        if source_kind == "run_trade":
            try:
                run_id = int(item.get("source_id") or 0)
                occurrence = max(1, int(item.get("trade_occurrence") or 1))
            except (TypeError, ValueError):
                skipped += 1
                continue
            trade_signature = str(item.get("trade_signature") or "").strip()
            if run_id <= 0 or not trade_signature:
                skipped += 1
                continue
            run = await _db_mod.get_run(user_id, run_id)
            if not run:
                skipped += 1
                continue
            remaining: list[dict] = []
            matches = 0
            removed = False
            for trade in run.get("trades") or []:
                current_signature = _dashboard_trade_signature_text(trade)
                if current_signature == trade_signature:
                    matches += 1
                    if not removed and matches == occurrence:
                        removed = True
                        continue
                remaining.append(trade)
            if not removed:
                skipped += 1
                continue
            if remaining:
                await _db_mod.replace_run_record(user_id, run_id, _recompute_run_trade_summary(run, remaining))
            else:
                await _db_mod.delete_run_record(user_id, run_id)
            deleted += 1
            continue

        if source_kind == "scalp_trade":
            try:
                trade_id = int(item.get("source_id") or 0)
            except (TypeError, ValueError):
                skipped += 1
                continue
            if trade_id <= 0:
                skipped += 1
                continue
            if await _db_mod.delete_scalp_trade(user_id, trade_id):
                deleted += 1
                scalp_deleted_ids.append(trade_id)
            else:
                skipped += 1
            continue

        skipped += 1

    if scalp_deleted_ids:
        eng = _scalp_engines.get(int(user_id))
        if eng is not None:
            id_set = set(scalp_deleted_ids)
            eng.closed_trades = [t for t in eng.closed_trades if t.get("trade_id") not in id_set]
        _notify_scalp_ws()

    return {"deleted": deleted, "skipped": skipped}


@app.get("/api/dashboard/summary")
async def dashboard_summary(request: Request):
    """Aggregated dashboard data for the homepage"""
    token = _get_session_token(request)
    if not await _validate_session_async(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    user_id = _request_user_id(request)
    today_str = _ist_date_str()

    # Strategies count
    strats = await _db_mod.list_strategies(user_id)
    runs = await _db_mod.list_runs(user_id)
    real_strats = [
        s for s in strats if not s.get("_placeholder") and str(s.get("run_name") or s.get("name") or "").strip()
    ]
    strategy_by_id = {int(s.get("id") or 0): s for s in real_strats if int(s.get("id") or 0)}
    strategy_name_matches: dict[str, list[dict]] = defaultdict(list)
    for strategy in real_strats:
        strategy_name = str(strategy.get("run_name") or strategy.get("name") or "").strip().casefold()
        if strategy_name:
            strategy_name_matches[strategy_name].append(strategy)

    def _attach_strategy_folder(status: dict) -> dict:
        if not isinstance(status, dict):
            return status
        strategy_payload = status.get("strategy") if isinstance(status.get("strategy"), dict) else None
        strategy_id = int(status.get("strategy_id") or (strategy_payload or {}).get("strategy_id") or 0)
        explicit_folder = str(status.get("folder") or (strategy_payload or {}).get("folder") or "").strip()
        strategy_name = str(
            status.get("strategy_name")
            or (strategy_payload or {}).get("run_name")
            or (strategy_payload or {}).get("name")
            or ""
        ).strip()
        matched_strategy = strategy_by_id.get(strategy_id) if strategy_id else None
        if not matched_strategy and strategy_name:
            matches = list(strategy_name_matches.get(strategy_name.casefold(), []))
            if explicit_folder:
                folder_key = (explicit_folder or "Intraday").strip().casefold()
                folder_matches = [
                    s for s in matches if (str(s.get("folder") or "").strip() or "Intraday").casefold() == folder_key
                ]
                if len(folder_matches) == 1:
                    matched_strategy = folder_matches[0]
            if not matched_strategy and len(matches) == 1:
                matched_strategy = matches[0]
        resolved_folder = explicit_folder
        if matched_strategy:
            resolved_folder = str(matched_strategy.get("folder") or "").strip() or "Intraday"
            status["strategy_id"] = int(matched_strategy.get("id") or strategy_id or 0)
            if strategy_payload is not None:
                strategy_payload.setdefault("strategy_id", status["strategy_id"])
        if resolved_folder:
            status["folder"] = resolved_folder
            if strategy_payload is not None and not strategy_payload.get("folder"):
                strategy_payload["folder"] = resolved_folder
        return status

    # Active engines
    paper_statuses = _running_statuses_for_user(paper_engines, user_id)
    live_statuses = _running_statuses_for_user(live_engines, user_id)
    paper_running = bool(paper_statuses)
    live_running = bool(live_statuses)
    scalp_running = False
    scalp_name = ""

    # Today's P&L from strategy engines
    paper_strategy_pnl_val = 0.0
    paper_strategy_trades_val = 0
    live_strategy_pnl_val = 0.0
    live_strategy_trades_val = 0

    if paper_statuses:
        paper_strategy_pnl_val = sum(float(s.get("total_pnl", 0) or 0) for s in paper_statuses)
        paper_strategy_trades_val = sum(int(s.get("trades_today", 0) or 0) for s in paper_statuses)
    else:
        # Show last paper run P&L from today (from runs.json)
        from datetime import date as _date

        paper_today_str = str(_date.today())
        for r in reversed(runs):
            if r.get("mode") == "paper":
                created = r.get("created_at", "")
                if created.startswith(paper_today_str):
                    paper_strategy_pnl_val = float(r.get("total_pnl", 0) or 0)
                    paper_strategy_trades_val = int(r.get("trade_count", len(r.get("trades", []))) or 0)
                break

    if live_statuses:
        live_strategy_pnl_val = sum(float(s.get("total_pnl", 0) or 0) for s in live_statuses)
        live_strategy_trades_val = sum(int(s.get("trades_today", 0) or 0) for s in live_statuses)

    scalp_engine = _scalp_engines.get(int(user_id))
    scalp_status = None
    if scalp_engine is not None:
        try:
            scalp_status = scalp_engine.get_status()
        except Exception:
            scalp_status = None
    if isinstance(scalp_status, dict) and scalp_status.get("running"):
        scalp_running = True
        scalp_trades = list(scalp_status.get("open_trades") or []) + list(scalp_status.get("closed_trades") or [])
        scalp_underlyings = list(
            dict.fromkeys(
                str(t.get("underlying") or "").strip() for t in scalp_trades if str(t.get("underlying") or "").strip()
            )
        )
        scalp_name = "Scalp Session"
        if scalp_underlyings:
            scalp_name = "Scalp — " + ", ".join(scalp_underlyings[:3])

    persisted_scalp_trades = await _db_mod.list_scalp_trades(user_id)
    scalp_flow = _collect_dashboard_scalp_snapshot(today_str, persisted_scalp_trades, scalp_status)
    paper_scalp_pnl = float(scalp_flow["paper"]["pnl"] or 0)
    paper_scalp_trades = int(scalp_flow["paper"]["trades"] or 0)
    live_scalp_pnl = float(scalp_flow["live"]["pnl"] or 0)
    live_scalp_trades = int(scalp_flow["live"]["trades"] or 0)

    _user, broker_client, broker_source = await _request_broker_context(request)
    real_snapshot = await _load_dashboard_real_snapshot(user_id, broker_client)
    fii_dii_snapshot = await _load_dashboard_fii_dii_snapshot()

    paper_total_pnl = round(paper_strategy_pnl_val + paper_scalp_pnl, 2)
    paper_total_trades = paper_strategy_trades_val + paper_scalp_trades
    real_total_pnl = round(
        float(real_snapshot.get("net_pnl", 0) or 0)
        if real_snapshot.get("available")
        else (live_strategy_pnl_val + live_scalp_pnl),
        2,
    )
    real_total_trades = int(
        real_snapshot.get("trades", 0) or 0
        if real_snapshot.get("available")
        else (live_strategy_trades_val + live_scalp_trades)
    )
    real_live_pnl = round(real_total_pnl - live_scalp_pnl, 2)
    real_live_trades = max(real_total_trades - live_scalp_trades, 0)
    scalp_pnl_val = round(paper_scalp_pnl + live_scalp_pnl, 2)
    scalp_trades_val = paper_scalp_trades + live_scalp_trades
    today_pnl = round(paper_total_pnl + real_total_pnl, 2)
    active_count = len(paper_statuses) + len(live_statuses) + (1 if scalp_running else 0)

    paper_labels = [str(s.get("strategy_name") or s.get("run_id") or "Paper Strategy") for s in paper_statuses]
    live_labels = [str(s.get("strategy_name") or s.get("run_id") or "Live Strategy") for s in live_statuses]
    if paper_scalp_trades or scalp_flow["paper"]["active"]:
        paper_labels.append(_scalp_label(scalp_flow["paper"]["underlyings"]))
    if live_scalp_trades or scalp_flow["live"]["active"]:
        live_labels.append(_scalp_label(scalp_flow["live"]["underlyings"]))
    paper_flow_name = _compact_label_list(paper_labels, "Paper Flow")
    real_flow_name = _compact_label_list(live_labels, "Real Flow")
    scalp_labels = scalp_flow["paper"]["underlyings"] + scalp_flow["live"]["underlyings"]
    scalp_card_name = _compact_label_list([_scalp_label(scalp_labels)], "SCALP")
    active_detail_parts = []
    if paper_statuses or scalp_flow["paper"]["active"]:
        active_detail_parts.append("Paper active")
    if live_statuses:
        active_detail_parts.append("Auto active")
    if scalp_flow["live"]["active"]:
        active_detail_parts.append("SCALP active")
    active_detail = " · ".join(active_detail_parts) if active_detail_parts else "No strategies running"

    # Best/worst across persisted runs + currently running engines/scalp session
    best_run = None
    worst_run = None
    total_backtests = len(runs)
    recent_transactions: list[dict] = []
    recent_seen: set[tuple] = set()
    active_paper_run_keys = {
        str(status.get("run_id") or status.get("strategy_name") or "").strip()
        for status in paper_statuses
        if str(status.get("run_id") or status.get("strategy_name") or "").strip()
    }
    active_live_run_keys = {
        str(status.get("run_id") or status.get("strategy_name") or "").strip()
        for status in live_statuses
        if str(status.get("run_id") or status.get("strategy_name") or "").strip()
    }

    def _consider_leader(candidate: dict | None):
        nonlocal best_run, worst_run
        if not isinstance(candidate, dict):
            return
        pnl = round(float(candidate.get("pnl") or 0), 2)
        candidate["pnl"] = pnl
        if best_run is None or pnl > float(best_run.get("pnl") or 0):
            best_run = candidate
        if worst_run is None or pnl < float(worst_run.get("pnl") or 0):
            worst_run = candidate

    def _add_recent_trade(
        trade: dict,
        run_name: str,
        mode: str,
        *,
        source_kind: str = "",
        source_id: int | str | None = None,
        deletable: bool = False,
        trade_signature: str = "",
        trade_occurrence: int = 1,
    ):
        if not isinstance(trade, dict):
            return
        symbol = _dashboard_trade_display_symbol(trade)
        trade_identifier = str(
            trade.get("trade_id") or trade.get("id") or trade.get("entry_order_id") or trade.get("exit_order_id") or ""
        ).strip()
        time_value = trade.get("exit_time") or trade.get("entry_time") or ""
        record = {
            "time": time_value,
            "run_name": run_name,
            "mode": mode,
            "symbol": symbol or "—",
            "transaction_type": str(trade.get("transaction_type") or "TRADE").upper(),
            "entry_time": trade.get("entry_time") or "",
            "exit_time": trade.get("exit_time") or "",
            "entry_price": float(
                trade.get("entry_premium") or trade.get("entry_price") or trade.get("current_premium") or 0
            ),
            "exit_price": float(
                trade.get("exit_premium") or trade.get("exit_price") or trade.get("current_premium") or 0
            ),
            "quantity": trade.get("lots") or trade.get("quantity") or "—",
            "pnl": float(trade.get("pnl") or 0),
            "reason": trade.get("exit_reason") or trade.get("reason") or "—",
            "source_kind": source_kind,
            "source_id": source_id,
            "deletable": bool(deletable),
            "trade_signature": trade_signature,
            "trade_occurrence": int(trade_occurrence or 1),
        }
        dedupe_key = (
            ("trade_id", record["mode"], trade_identifier)
            if trade_identifier
            else (
                record["mode"],
                record["symbol"],
                record["transaction_type"],
                str(record["entry_time"]),
                str(record["exit_time"]),
                round(record["entry_price"], 2),
                round(record["exit_price"], 2),
                round(record["pnl"], 2),
                str(record["reason"]),
            )
        )
        if dedupe_key in recent_seen:
            return
        recent_seen.add(dedupe_key)
        recent_transactions.append(record)

    for status in paper_statuses:
        _consider_leader(
            {
                "kind": "engine",
                "mode": "paper",
                "run_id": str(status.get("run_id") or status.get("strategy_name") or ""),
                "name": status.get("strategy_name") or status.get("run_id") or "Paper Strategy",
                "pnl": status.get("total_pnl") or 0,
            }
        )
        for trade in status.get("closed_trades", []) or []:
            _add_recent_trade(
                trade,
                status.get("strategy_name") or "Paper Run",
                "paper",
                source_kind="active_engine_trade",
                source_id=str(status.get("run_id") or status.get("strategy_name") or ""),
                deletable=False,
            )
    for status in live_statuses:
        _consider_leader(
            {
                "kind": "engine",
                "mode": "auto",
                "run_id": str(status.get("run_id") or status.get("strategy_name") or ""),
                "name": status.get("strategy_name") or status.get("run_id") or "Live Strategy",
                "pnl": status.get("total_pnl") or 0,
            }
        )
        for trade in status.get("closed_trades", []) or []:
            _add_recent_trade(
                trade,
                status.get("strategy_name") or "Live Run",
                "live",
                source_kind="active_engine_trade",
                source_id=str(status.get("run_id") or status.get("strategy_name") or ""),
                deletable=False,
            )

    persisted_scalp_seen_ids: set[int] = set()
    if isinstance(scalp_status, dict):
        for trade in scalp_status.get("closed_trades", []) or []:
            trade_id = trade.get("trade_id")
            if trade_id is not None:
                try:
                    persisted_scalp_seen_ids.add(int(trade_id))
                except (TypeError, ValueError):
                    pass
            _add_recent_trade(
                trade,
                scalp_name or "Scalp Session",
                "scalp",
                source_kind="scalp_trade",
                source_id=trade.get("trade_id"),
                deletable=bool(trade.get("trade_id")),
            )

    for trade in persisted_scalp_trades or []:
        trade_id = trade.get("trade_id")
        if trade_id is not None:
            try:
                if int(trade_id) in persisted_scalp_seen_ids:
                    continue
            except (TypeError, ValueError):
                pass
        _add_recent_trade(
            trade,
            trade.get("run_name") or scalp_name or "Scalp Session",
            "scalp",
            source_kind="scalp_trade",
            source_id=trade_id,
            deletable=bool(trade_id),
        )

    if scalp_running and isinstance(scalp_status, dict):
        _consider_leader(
            {
                "kind": "scalp",
                "mode": "scalp",
                "name": scalp_name or "Scalp Session",
                "pnl": scalp_status.get("total_pnl") or 0,
            }
        )

    if runs:
        for r in runs:
            pnl = r.get("total_pnl", 0)
            _consider_leader(
                {
                    "kind": "run",
                    "id": r.get("id"),
                    "mode": str(r.get("mode") or "backtest"),
                    "run_id": str(r.get("run_name") or ""),
                    "name": r.get("run_name", "") or f"Run #{r.get('id')}",
                    "pnl": pnl,
                }
            )
            if r.get("mode") not in ("paper", "live"):
                continue
            run_key = str(r.get("run_name") or r.get("strategy_name") or "").strip()
            if (r.get("mode") == "paper" and run_key in active_paper_run_keys) or (
                r.get("mode") == "live" and run_key in active_live_run_keys
            ):
                continue
            run_trade_occurrences: Counter[str] = Counter()
            for trade in r.get("trades", []) or []:
                trade_signature = _dashboard_trade_signature_text(trade)
                trade_occurrence = 1
                if trade_signature:
                    run_trade_occurrences[trade_signature] += 1
                    trade_occurrence = run_trade_occurrences[trade_signature]
                _add_recent_trade(
                    trade,
                    r.get("run_name") or r.get("strategy_name") or f"Run #{r.get('id')}",
                    str(r.get("mode") or "paper"),
                    source_kind="run_trade",
                    source_id=r.get("id"),
                    deletable=True,
                    trade_signature=trade_signature,
                    trade_occurrence=trade_occurrence,
                )
        recent_transactions.sort(key=lambda item: str(item.get("time") or ""), reverse=True)
        recent_transactions = recent_transactions[:100]

    return {
        "strategy_count": len(real_strats),
        "backtest_count": total_backtests,
        "active_count": active_count,
        "active_detail": active_detail,
        "paper_running": paper_running,
        "live_running": live_running,
        "scalp_running": scalp_running,
        "paper_strategy": ", ".join(s.get("strategy_name", "") for s in paper_statuses) if paper_statuses else "",
        "live_strategy": ", ".join(s.get("strategy_name", "") for s in live_statuses) if live_statuses else "",
        "scalp_strategy": scalp_name,
        "today_pnl": today_pnl,
        "paper_pnl": paper_total_pnl,
        "paper_total_pnl": paper_total_pnl,
        "paper_strategy_pnl": round(paper_strategy_pnl_val, 2),
        "paper_scalp_pnl": round(paper_scalp_pnl, 2),
        "live_pnl": round(live_strategy_pnl_val, 2),
        "live_strategy_pnl": round(live_strategy_pnl_val, 2),
        "real_pnl": real_total_pnl,
        "real_total_pnl": real_total_pnl,
        "real_strategy_pnl": round(live_strategy_pnl_val, 2),
        "real_scalp_pnl": round(live_scalp_pnl, 2),
        "scalp_pnl": scalp_pnl_val,
        "paper_trades": paper_total_trades,
        "paper_total_trades": paper_total_trades,
        "paper_strategy_trades": paper_strategy_trades_val,
        "paper_scalp_trades": paper_scalp_trades,
        "live_trades": live_strategy_trades_val,
        "live_strategy_trades": live_strategy_trades_val,
        "real_trades": real_total_trades,
        "real_total_trades": real_total_trades,
        "real_scalp_trades": live_scalp_trades,
        "scalp_trades": scalp_trades_val,
        "real_source": str(real_snapshot.get("source") or "engine_fallback"),
        "real_source_label": str(real_snapshot.get("source_label") or "Engine view"),
        "real_available": bool(real_snapshot.get("available")),
        "real_message": str(real_snapshot.get("message") or ""),
        "real_stale": bool(real_snapshot.get("stale")),
        "broker_source": broker_source,
        "paper_flow": {
            "active": bool(paper_statuses or scalp_flow["paper"]["active"]),
            "name": paper_flow_name,
            "pnl": paper_total_pnl,
            "trades": paper_total_trades,
            "strategy_pnl": round(paper_strategy_pnl_val, 2),
            "strategy_trades": paper_strategy_trades_val,
            "scalp_pnl": round(paper_scalp_pnl, 2),
            "scalp_trades": paper_scalp_trades,
        },
        "paper_strategy_flow": {
            "active": bool(
                paper_running or paper_strategy_trades_val or abs(float(paper_strategy_pnl_val or 0)) > 1e-9
            ),
            "name": _compact_label_list(paper_labels[: len(paper_statuses)] or ["Paper Strategy"], "Paper Strategy"),
            "pnl": round(paper_strategy_pnl_val, 2),
            "trades": paper_strategy_trades_val,
        },
        "real_flow": {
            "active": bool(live_statuses or scalp_flow["live"]["active"]),
            "name": real_flow_name,
            "pnl": real_total_pnl,
            "trades": real_total_trades,
            "strategy_pnl": round(live_strategy_pnl_val, 2),
            "strategy_trades": live_strategy_trades_val,
            "scalp_pnl": round(live_scalp_pnl, 2),
            "scalp_trades": live_scalp_trades,
            "source_label": str(real_snapshot.get("source_label") or "Engine view"),
            "available": bool(real_snapshot.get("available")),
        },
        "live_strategy_flow": {
            "active": bool(live_running or real_live_trades or abs(float(real_live_pnl or 0)) > 1e-9),
            "name": _compact_label_list(live_labels[: len(live_statuses)] or ["Live Trades"], "Live Trades"),
            "pnl": real_live_pnl,
            "trades": real_live_trades,
            "engine_pnl": round(live_strategy_pnl_val, 2),
            "engine_trades": live_strategy_trades_val,
            "source_label": str(real_snapshot.get("source_label") or "Engine view"),
        },
        "running_engines": [_attach_strategy_folder({**status, "mode": "paper"}) for status in paper_statuses]
        + [_attach_strategy_folder({**status, "mode": "auto"}) for status in live_statuses],
        "scalp_flow": {
            "active": bool(scalp_running or paper_scalp_trades or live_scalp_trades),
            "name": scalp_card_name,
            "paper_pnl": round(paper_scalp_pnl, 2),
            "paper_trades": paper_scalp_trades,
            "real_pnl": round(live_scalp_pnl, 2),
            "real_trades": live_scalp_trades,
        },
        "fii_dii": fii_dii_snapshot,
        "best_run": best_run,
        "worst_run": worst_run,
        "recent_transactions": recent_transactions,
    }


@app.post("/api/dashboard/recent-transactions/bulk-delete")
async def bulk_delete_dashboard_recent_transactions(request: Request):
    token = _get_session_token(request)
    if not await _validate_session_async(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    body = await request.json()
    items = body.get("items", [])
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="items must be a non-empty list")
    result = await _delete_dashboard_recent_transaction_items(_request_user_id(request), items)
    return result


# ── Strategy Validation ──────────────────────────────────────────
@app.post("/api/validate-strategy")
async def validate_strategy(request: Request):
    """Deep validation of strategy before deployment"""
    token = _get_session_token(request)
    if not await _validate_session_async(token):
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    errors = []
    warnings = []

    # Instrument
    instrument = body.get("instrument", "")
    if not instrument:
        errors.append("No instrument selected")

    # Conditions
    entry = body.get("entry_conditions", [])
    exit_conds = body.get("exit_conditions", [])
    if not entry:
        errors.append("No entry conditions defined")
    if not exit_conds:
        warnings.append("No exit conditions — trades will only close at square-off time or SL/target")

    # Legs validation
    legs = body.get("legs", [])
    if legs:
        for i, leg in enumerate(legs):
            if not leg.get("lots"):
                errors.append(f"Leg {i + 1}: lot size not specified")
            sl = leg.get("sl_points", 0)
            tp = leg.get("tp_points", 0)
            if sl and tp and tp <= sl:
                warnings.append(f"Leg {i + 1}: target ({tp}) is less than stop-loss ({sl}) — poor risk:reward")

    # Contradictory conditions check
    for c in entry:
        lhs = c.get("lhs", "")
        op = c.get("operator", "")
        rhs = c.get("rhs", "")
        # Check if same indicator has contradictory conditions
        for c2 in entry:
            if c2 is c:
                continue
            if c2.get("lhs") == lhs and c2.get("rhs") == rhs:
                if op in ("is_above", "crosses_above") and c2.get("operator") in ("is_below", "crosses_below"):
                    errors.append(f"Contradictory conditions: {lhs} cannot be both above and below {rhs}")

    # Risk checks
    sl_pct = body.get("stoploss_pct", 0)
    tp_pct = body.get("target_profit_pct", 0)
    if sl_pct and tp_pct and tp_pct < sl_pct:
        warnings.append(f"Risk:Reward unfavorable — SL {sl_pct}% vs Target {tp_pct}%")
    if sl_pct == 0:
        warnings.append("No strategy-level stop-loss set — unlimited downside risk")

    max_trades = body.get("max_trades_per_day", 1)
    if max_trades > 5:
        warnings.append(f"High trade frequency ({max_trades}/day) — check for overtrading")

    # Lot size / capital validation (#13)
    from engine.backtest import get_lot_size

    lots = int(body.get("lots", 1) or 1)
    user_lot_size = int(body.get("lot_size", 0) or 0)
    initial_capital = float(body.get("initial_capital", 500000) or 500000)
    if instrument:
        inst_name = "NIFTY"
        if "26009" in str(instrument) or "BANK" in str(instrument).upper():
            inst_name = "BANKNIFTY"
        elif "26017" in str(instrument) or "FIN" in str(instrument).upper():
            inst_name = "FINNIFTY"
        current_lot = get_lot_size(instrument, date.today())
        if user_lot_size > 0 and user_lot_size != current_lot:
            warnings.append(f"Custom lot size ({user_lot_size}) differs from current {inst_name} lot ({current_lot})")
        effective_lot = user_lot_size if user_lot_size > 0 else current_lot
        total_qty = lots * effective_lot
        # Estimate margin: rough NIFTY option margin ~₹1.5L per lot
        est_margin_per_lot = 150000 if "BANK" in inst_name else 100000
        est_margin = lots * est_margin_per_lot
        if est_margin > initial_capital * 0.8:
            warnings.append(
                f"Estimated margin ₹{est_margin:,.0f} for {lots} lot(s) may exceed 80% of capital ₹{initial_capital:,.0f}"
            )

    contract = validate_strategy_contract(body.get("indicators"), entry, exit_conds)
    errors.extend(contract["errors"])
    warnings.extend(contract["warnings"])

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "normalized_indicators": contract["normalized_indicators"],
        "summary": {
            "instrument": instrument,
            "entry_conditions": len(entry),
            "exit_conditions": len(exit_conds),
            "legs": len(legs),
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "indicator_count": len(contract["normalized_indicators"]),
        },
    }


def _normalized_indicator_bundle(
    indicators: list[str] | None,
    entry_conditions: list[dict] | None,
    exit_conditions: list[dict] | None,
) -> tuple[list[str], list[dict], list[dict]]:
    entry = entry_conditions if entry_conditions is not None else DEFAULT_ENTRY_CONDITIONS
    exit_conds = exit_conditions if exit_conditions is not None else DEFAULT_EXIT_CONDITIONS
    merged = normalize_strategy_indicators(indicators or [], entry_conditions=entry, exit_conditions=exit_conds)
    return merged, entry, exit_conds


_RUNTIME_STRATEGY_SYNC_FIELDS = {
    "run_name",
    "name",
    "folder",
    "segment",
    "instrument",
    "from_date",
    "to_date",
    "lots",
    "lot_size",
    "stoploss_pct",
    "stoploss_rupees",
    "sl_type",
    "target_profit_pct",
    "target_profit_rupees",
    "tp_type",
    "market_open",
    "market_close",
    "max_trades_per_day",
    "max_daily_loss",
    "legs",
    "deploy_config",
    "combined_sl_rupees",
    "combined_target_rupees",
    "combined_sqoff_time",
    "fee_pct",
    "trailing_sl_pct",
    "initial_capital",
    "execution_profile",
    "spread_bps",
    "entry_slippage_bps",
    "exit_slippage_bps",
    "entry_delay_candles",
    "signal_exit_delay_candles",
    "enforce_capital",
    "capital_buffer_pct",
    "sell_option_margin_per_lot",
    "allow_synthetic_option_fallback",
}


def _runtime_strategy_persistable_view(strategy: dict) -> dict:
    return {
        key: deepcopy(strategy.get(key))
        for key in sorted(_RUNTIME_STRATEGY_SYNC_FIELDS | {"indicators", "entry_conditions", "exit_conditions"})
        if key in strategy
    }


async def _sync_saved_strategy_from_runtime(
    user_id: int,
    strategy_id: int,
    runtime_source: dict | None,
    entry_conditions: list[dict] | None,
    exit_conditions: list[dict] | None,
    *,
    source_label: str,
) -> bool:
    sid = int(strategy_id or 0)
    if sid <= 0 or not isinstance(runtime_source, dict):
        return False

    existing = await _db_mod.get_strategy(user_id, sid)
    if not existing:
        return False

    updated = dict(existing)
    for field in _RUNTIME_STRATEGY_SYNC_FIELDS:
        if field in runtime_source:
            updated[field] = deepcopy(runtime_source.get(field))

    if runtime_source.get("run_name"):
        updated["run_name"] = runtime_source["run_name"]
        updated["name"] = runtime_source.get("name") or runtime_source["run_name"]

    normalized_indicators, normalized_entry, normalized_exit = _normalized_indicator_bundle(
        runtime_source.get("indicators"),
        entry_conditions,
        exit_conditions,
    )
    updated["indicators"] = normalized_indicators
    updated["entry_conditions"] = normalized_entry
    updated["exit_conditions"] = normalized_exit

    if _runtime_strategy_persistable_view(existing) == _runtime_strategy_persistable_view(updated):
        return False

    ver = int(existing.get("version", 1) or 1) + 1
    versions = list(existing.get("versions", []))
    versions.append(
        {
            "version": ver,
            "saved_at": str(datetime.now()),
            "changes": f"Synced from {source_label}",
        }
    )
    if len(versions) > 20:
        versions = versions[-20:]

    updated["version"] = ver
    updated["versions"] = versions
    updated["updated_at"] = str(datetime.now())

    await _db_mod.replace_strategy_record(user_id, sid, updated)
    return True


# ── Portfolio Summary API (#8) ────────────────────────────────────
@app.get("/api/portfolio/summary")
async def portfolio_summary(request: Request):
    """Aggregated portfolio: balance + positions + unrealized P&L in one call"""
    token = _get_session_token(request)
    if not await _validate_session_async(token):
        raise HTTPException(status_code=401, detail="Unauthorized")

    result = {"funds": None, "positions": [], "unrealized_pnl": 0, "total_value": 0, "errors": []}
    user, broker_client, source = await _request_broker_context(request)
    if not broker_client:
        result["errors"].append(_broker_not_configured_message(user, source))
        return result
    # Funds
    try:
        funds = await asyncio.to_thread(broker_client.get_funds)
        result["funds"] = funds
        if isinstance(funds, dict):
            result["total_value"] = float(funds.get("availabelBalance", funds.get("available_balance", 0)))
    except Exception as e:
        result["errors"].append(f"Funds: {str(e)}")

    # Positions + unrealized P&L
    try:
        positions = await asyncio.to_thread(broker_client.get_positions)
        result["positions"] = positions
        unrealized = 0
        for pos in positions if isinstance(positions, list) else []:
            unrealized += float(pos.get("unrealizedProfit", pos.get("dayProfit", 0)))
        result["unrealized_pnl"] = round(unrealized, 2)
        result["total_value"] = round(result["total_value"] + unrealized, 2)
    except Exception as e:
        result["errors"].append(f"Positions: {str(e)}")

    return result


# ── Strategy Versioning ──────────────────────────────────────────
@app.get("/api/strategies/{sid}/versions")
async def get_strategy_versions(sid: int, request: Request):
    strategy = await _db_mod.get_strategy(_request_user_id(request), sid)
    if strategy:
        return {"versions": strategy.get("versions", [])}
    raise HTTPException(status_code=404, detail="Strategy not found")


# ── Health ────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    runtime_running = any(_runtime_control_summary(owner_id)["any_running"] for owner_id in _runtime_owner_ids())
    return {
        "status": "ok",
        "time": str(datetime.now()),
        "dhan_configured": (
            config.DHAN_CLIENT_ID != "YOUR_CLIENT_ID_HERE" and config.DHAN_ACCESS_TOKEN != "YOUR_ACCESS_TOKEN_HERE"
        ),
        "live_running": _any_running(live_engines),
        "runtime_running": runtime_running,
    }


@app.post("/api/save-state")
async def save_state(request: Request):
    """Persist all running engine states to disk (called by deploy script before restart)."""
    if not _is_loopback_request(request):
        return JSONResponse(status_code=403, content={"error": "localhost only"})
    saved = []
    for owner_id, run_id, engine in _iter_registry_items(live_engines):
        if engine.running:
            engine._save_state()
            saved.append(f"live:{owner_id}:{run_id}")
    for owner_id, run_id, engine in _iter_registry_items(paper_engines):
        if engine.running:
            engine._save_state()
            saved.append(f"paper:{owner_id}:{run_id}")
    for owner_id, runtime in list(_cascade_engines.items()):
        await _save_cascade_open_state(owner_id, runtime, force=True)
        saved.append(f"cascade-paper:{owner_id}")
    for owner_id, engine in list(_scalp_engines.items()):
        await _save_scalp_open_state(owner_id, engine, force=True)
        saved.append(f"scalp:{owner_id}")
    for owner_id in list(_candle_entry_engines):
        await _save_candle_entry_open_state(owner_id, force=True)
        saved.append(f"candle-entry:{owner_id}")
    for owner_id in list(_fib_boundary_engines):
        await _save_fib_boundary_open_state(owner_id, force=True)
        saved.append(f"fib-boundary:{owner_id}")
    for owner_id in list(_terminal_cascade_engines):
        await _save_terminal_cascade_open_state(owner_id, force=True)
        saved.append(f"terminal-cascade:{owner_id}")
    return {"status": "ok", "saved": saved}


@app.post("/api/restore-engines")
async def restore_engines_after_handover(request: Request):
    """Restore engines only after blue/green ownership has moved here."""
    if not _is_loopback_request(request):
        return JSONResponse(status_code=403, content={"error": "localhost only"})
    if not _engine_restore_owner_is_active_instance():
        return JSONResponse(status_code=409, content={"error": "this worker is not the active instance"})
    await _restore_live_engines()
    await _restore_paper_engines()
    auxiliary = await _restore_auxiliary_engines()
    return {
        "status": "ok",
        "live_running": _any_running(live_engines),
        "paper_running": _any_running(paper_engines),
        "auxiliary": auxiliary,
    }


@app.get("/api/token-status")
async def token_status():
    """Check Dhan API token expiry"""
    return config.get_token_expiry()


@app.post("/api/refresh-token")
async def refresh_token(request: Request):
    """Force-refresh the current broker token for this user or the admin fallback."""
    try:
        user, broker_client, source = await _request_broker_context(request)
        if not broker_client:
            return {
                "status": "not_configured",
                "message": _broker_not_configured_message(user, source),
            }

        new_tok = await asyncio.to_thread(broker_client.refresh_access_token, force=True)
        if new_tok:
            fresh_user = await _db_mod.get_user_by_id(user["id"]) if user else None
            return {
                "status": "ok",
                "message": "Token refreshed successfully",
                "source": source,
                "broker": _broker_profile_payload(fresh_user or user),
            }

        if source == "user":
            if _user_broker_auto_refresh_ready(user):
                message = "User broker token refresh failed. Re-save your Dhan broker credentials and try again."
            else:
                message = "Save Dhan PIN and TOTP Secret in Account Settings to enable per-user token refresh."
            return {"status": "error", "message": message, "source": source}

        return {"status": "error", "message": "Token generation failed — check TOTP secret", "source": source}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── Broker Connection Validation ──────────────────────────────────
@app.post("/api/broker/check")
async def check_broker(request: Request):
    """Check if broker connection is active and valid"""
    try:
        user, broker_client, source = await _request_broker_context(request)
        if not broker_client:
            return {
                "status": "not_configured",
                "broker": "Dhan",
                "message": _broker_not_configured_message(user, source),
            }

        auto_refresh_ready = _user_broker_auto_refresh_ready(user)

        # Test connection by fetching account funds
        funds = await asyncio.to_thread(broker_client.get_funds)
        available_balance = float(funds.get("availabelBalance", 0) or 0) if isinstance(funds, dict) else 0.0

        if funds and isinstance(funds, dict):
            try:
                market_ok = await asyncio.to_thread(_probe_market_data_connection, broker_client)
            except Exception as probe_error:
                probe_msg = str(probe_error)
                if _looks_like_broker_auth_error(probe_msg):
                    if auto_refresh_ready:
                        message = "Market-data auth failed even after auto-refresh. Re-save your Dhan credentials."
                    else:
                        message = "Market-data auth failed. Save Dhan PIN and TOTP Secret in Account Settings for auto-refresh."
                    return {
                        "status": "auth_error",
                        "broker": "Dhan",
                        "message": message,
                        "source": source,
                        "available_balance": available_balance,
                        "funds": funds,
                        "market_data_ok": False,
                        "auto_refresh_ready": auto_refresh_ready,
                    }
                _logger.warning("[BrokerCheck] Market-data probe failed after funds load: %s", probe_msg)
                market_ok = False

            return {
                "status": "connected",
                "broker": "Dhan",
                "message": "Broker connection active",
                "source": source,
                "available_balance": available_balance,
                "funds": funds,
                "market_data_ok": market_ok,
                "auto_refresh_ready": auto_refresh_ready,
            }
        else:
            # No data returned
            return {"status": "error", "broker": "Dhan", "message": "Invalid response from broker API"}

    except Exception as e:
        error_msg = str(e)
        if _looks_like_broker_auth_error(error_msg):
            auto_refresh_ready = _user_broker_auto_refresh_ready(user if "user" in locals() else None)
            detail = (
                "Invalid broker credentials or expired token."
                if auto_refresh_ready
                else "Invalid broker credentials or expired token. Save Dhan PIN and TOTP Secret for auto-refresh."
            )
            return {
                "status": "auth_error",
                "broker": "Dhan",
                "message": detail,
                "source": source if "source" in locals() else "missing",
                "auto_refresh_ready": auto_refresh_ready,
            }
        elif "401" in error_msg or "Unauthorized" in error_msg:
            return {"status": "error", "broker": "Dhan", "message": "Invalid API credentials (401 Unauthorized)"}
        elif "403" in error_msg or "Forbidden" in error_msg:
            return {"status": "error", "broker": "Dhan", "message": "Access forbidden - check API permissions (403)"}
        elif "timeout" in error_msg.lower():
            return {"status": "error", "broker": "Dhan", "message": "Connection timeout - network issue"}
        else:
            return {"status": "error", "broker": "Dhan", "message": f"Connection error: {error_msg[:100]}"}


@app.get("/api/broker/trades")
async def get_broker_trades(request: Request):
    """Fetch executed trades from Dhan broker account"""
    try:
        user, broker_client, source = await _request_broker_context(request)
        if not broker_client:
            return {
                "status": "not_configured",
                "message": _broker_not_configured_message(user, source),
                "trades": [],
            }

        # Fetch trades from Dhan API
        trades_result = await asyncio.to_thread(broker_client.get_trades)
        trades = trades_result if isinstance(trades_result, list) else []

        # Auto-persist daily trade summary for portfolio history
        if trades:
            try:
                await _persist_daily_trades(trades, _request_user_id(request))
            except Exception as pe:
                print(f"[TRADE_HISTORY] Persist error: {pe}")

        return {"status": "success", "broker": "Dhan", "source": source, "count": len(trades), "trades": trades}

    except Exception as e:
        error_msg = str(e)
        return {
            "status": "error",
            "broker": "Dhan",
            "message": f"Failed to fetch trades: {error_msg[:100]}",
            "trades": [],
        }


def _backfill_trade_history(
    from_date: str = "2024-01-01",
    force: bool = False,
    user_id: int | None = None,
    broker_client: DhanClient | None = None,
):
    """Fetch historical trades from Dhan and backfill a user's persisted trade history.

    Args:
        from_date: Start date in YYYY-MM-DD format.
        force: If True, overwrite existing dates with fresh data from Dhan.
        user_id: Trade-history owner. Defaults to the configured admin user.
    """
    import time as _time

    try:
        client = broker_client or dhan
        owner_id = int(user_id or _default_history_user_id_sync())
        history = _db_mod.list_trade_history_sync(owner_id)
        today_str = _ist_date_str()
        existing_dates = set(history.keys())

        # Dhan API returns 20 trades per page, paginate through all
        DHAN_PAGE_SIZE = 20
        MAX_PAGES = 500  # Safety limit (up to 10,000 trades)
        RATE_LIMIT_RETRIES = 3
        PAGE_DELAY = 0.3  # seconds between pages to avoid rate-limit
        all_trades = []
        page = 0
        consecutive_empty = 0
        while page < MAX_PAGES:
            result = client.get_trade_history(from_date, today_str, page)

            # Handle rate-limit: retry with exponential backoff
            if result == client.RATE_LIMITED:
                retried = False
                for attempt in range(1, RATE_LIMIT_RETRIES + 1):
                    wait = 2**attempt  # 2, 4, 8 seconds
                    print(f"[BACKFILL] Rate limited on page {page}, retry {attempt}/{RATE_LIMIT_RETRIES} after {wait}s")
                    _time.sleep(wait)
                    result = client.get_trade_history(from_date, today_str, page)
                    if result != client.RATE_LIMITED:
                        retried = True
                        break
                if not retried and result == client.RATE_LIMITED:
                    print(f"[BACKFILL] Rate limit persists after {RATE_LIMIT_RETRIES} retries on page {page}, stopping")
                    break

            trades = result if isinstance(result, list) else []
            if not trades:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break  # 3 consecutive empty pages = truly done
                page += 1
                _time.sleep(PAGE_DELAY)
                continue

            consecutive_empty = 0
            all_trades.extend(trades)
            print(f"[BACKFILL] Page {page}: {len(trades)} trades (total so far: {len(all_trades)})")
            if len(trades) < DHAN_PAGE_SIZE:  # Last page
                break
            page += 1
            _time.sleep(PAGE_DELAY)  # Throttle to avoid Dhan rate-limit

        if not all_trades:
            print(f"[BACKFILL] No historical trades returned from Dhan for {from_date} to {today_str}")
            return 0

        print(f"[BACKFILL] Fetched {len(all_trades)} total historical trades from Dhan ({page + 1} pages)")

        # De-duplicate by exchange trade id (or a strict fill fingerprint fallback)
        unique_trades = _dedupe_trade_fills(all_trades)
        if len(unique_trades) < len(all_trades):
            print(f"[BACKFILL] De-duplicated: {len(all_trades)} → {len(unique_trades)} unique trades")
        all_trades = unique_trades

        daily_entries = _summarize_real_trade_history(all_trades, source="historical_fifo", carry_inventory=True)
        if force:
            _db_mod.clear_trade_history_sync(owner_id)

        new_dates = 0
        updated_entries = {}
        for date_str, entry in sorted(daily_entries.items()):
            if date_str == today_str and not force:
                continue
            if not force and date_str in existing_dates:
                existing_entry = history.get(date_str)
                if not _trade_history_entry_needs_refresh(existing_entry, trade_date=date_str, today_str=today_str):
                    continue
            if entry and (
                entry.get("trades", 0) > 0
                or entry.get("charges", 0) > 0
                or entry.get("brokerage", 0) > 0
                or entry.get("pnl", 0) != 0
            ):
                history[date_str] = entry
                updated_entries[date_str] = entry
                new_dates += 1

        if updated_entries:
            for date_str, entry in updated_entries.items():
                _db_mod.upsert_trade_history_entry_sync(owner_id, date_str, entry)
            print(f"[BACKFILL] {'Refreshed' if force else 'Added'} {new_dates} dates in SQLite trade history")
        else:
            print("[BACKFILL] No new dates to add (all existing)")

        return new_dates
    except Exception as e:
        print(f"[BACKFILL] Error: {e}")
        import traceback

        traceback.print_exc()
        return 0


@app.get("/api/portfolio/backfill")
async def portfolio_backfill(request: Request, force: bool = False):
    """Manually trigger historical trade backfill from Dhan.

    Args:
        force: If true, re-fetch ALL trades and overwrite existing data.
    """
    try:
        user, broker_client, source = await _request_broker_context(request)
        if not broker_client:
            return {"status": "not_configured", "message": _broker_not_configured_message(user, source)}
        count = await asyncio.to_thread(
            _backfill_trade_history,
            "2024-01-01",
            force,
            _request_user_id(request),
            broker_client,
        )
        return {"status": "success", "new_dates": count, "force": force}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/backfill/status")
async def backfill_status():
    """Return current background backfill state (polled by frontend)."""
    return _backfill_state


@app.get("/api/portfolio/history")
async def get_portfolio_history(request: Request):
    """Return historical real and paper P&L with daily/monthly/yearly aggregates."""
    try:
        user_id = _request_user_id(request)
        real_history = await _db_mod.list_trade_history(user_id)
        if _trade_history_needs_repair(user_id, real_history):
            _trade_history_repair_attempts[user_id] = time.monotonic()
            try:
                _, broker_client, _ = await _request_broker_context(request)
                if broker_client:
                    refresh_from_date = _trade_history_refresh_start(real_history, "2024-01-01")
                    await asyncio.to_thread(_backfill_trade_history, refresh_from_date, False, user_id, broker_client)
                    real_history = await _db_mod.list_trade_history(user_id)
            except Exception as repair_error:
                print(f"[PORTFOLIO] Trade-history repair skipped: {repair_error}")
        runs = await _db_mod.list_runs(user_id)
        daily, monthly, yearly = _aggregate_portfolio_history(real_history, runs)
        return {"status": "success", "daily": daily, "monthly": monthly, "yearly": yearly}
    except Exception as e:
        print(f"[PORTFOLIO] History error: {e}")
        return {"status": "error", "message": str(e), "daily": {}, "monthly": {}, "yearly": {}}


@app.post("/api/broker/connect")
async def connect_broker(request: Request):
    """Establish and validate broker connection"""
    try:
        user, broker_client, source = await _request_broker_context(request)
        if not broker_client:
            return {
                "status": "not_configured",
                "broker": "Dhan",
                "message": _broker_not_configured_message(user, source),
            }

        # Test connection by attempting to fetch account funds
        funds = await asyncio.to_thread(broker_client.get_funds)

        if funds and isinstance(funds, dict):
            # Successfully connected and validated
            return {
                "status": "connected",
                "broker": "Dhan",
                "message": "Successfully connected to Dhan broker",
                "source": source,
                "available_balance": funds.get("availabelBalance", 0),
                "client_id": broker_client.client_id,
            }
        else:
            # Connection made but no valid data
            return {"status": "error", "broker": "Dhan", "message": "Broker returned empty or invalid response"}

    except Exception as e:
        error_msg = str(e)
        alerter.alert("Broker Connect Failed", f"Error: {error_msg[:200]}", level="warn")

        # Provide specific error messages based on error type
        if "401" in error_msg or "Unauthorized" in error_msg:
            return {
                "status": "error",
                "broker": "Dhan",
                "message": "Invalid API credentials. Please check your Client ID and Access Token.",
            }
        elif "403" in error_msg or "Forbidden" in error_msg:
            return {
                "status": "error",
                "broker": "Dhan",
                "message": "Access forbidden. Your API token may have expired or lacks permissions.",
            }
        elif "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            return {
                "status": "error",
                "broker": "Dhan",
                "message": "Connection timeout. Please check your internet connection.",
            }
        elif "connection" in error_msg.lower():
            return {"status": "error", "broker": "Dhan", "message": "Network error. Unable to reach Dhan API servers."}
        else:
            return {"status": "error", "broker": "Dhan", "message": f"Connection failed: {error_msg[:100]}"}


# ── Instrument Mapping ────────────────────────────────────────────
# Maps frontend values to Dhan API params
# IMPORTANT: Dhan security IDs for indices are DIFFERENT from scrip IDs
# Use Dhan's scrip master CSV to find correct security IDs
INSTRUMENT_MAP = {
    # Indices — Dhan security IDs (from Dhan scrip master)
    "26000": {"name": "NIFTY 50", "dhan_id": "13", "dhan_seg": "IDX_I", "dhan_type": "INDEX"},
    "26009": {"name": "BANK NIFTY", "dhan_id": "25", "dhan_seg": "IDX_I", "dhan_type": "INDEX"},
    "1": {
        "name": "SENSEX",
        "dhan_id": "51",
        "dhan_seg": "IDX_I",
        "dhan_type": "INDEX",
    },  # BSE SENSEX: Try ID 51 for BSE
    "26017": {"name": "NIFTY FIN SVC", "dhan_id": "27", "dhan_seg": "IDX_I", "dhan_type": "INDEX"},
    "26037": {"name": "NIFTY MIDCAP", "dhan_id": "49", "dhan_seg": "IDX_I", "dhan_type": "INDEX"},
    "26074": {"name": "NIFTY NEXT 50", "dhan_id": "26", "dhan_seg": "IDX_I", "dhan_type": "INDEX"},
    "26013": {"name": "NIFTY IT", "dhan_id": "30", "dhan_seg": "IDX_I", "dhan_type": "INDEX"},
    # Stocks — Dhan NSE security IDs
    "RELIANCE": {"name": "Reliance", "dhan_id": "2885", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "TCS": {"name": "TCS", "dhan_id": "11536", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "HDFCBANK": {"name": "HDFC Bank", "dhan_id": "1333", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "INFY": {"name": "Infosys", "dhan_id": "1594", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "ICICIBANK": {"name": "ICICI Bank", "dhan_id": "4963", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "HINDUNILVR": {"name": "HUL", "dhan_id": "1394", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "ITC": {"name": "ITC", "dhan_id": "1660", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "SBIN": {"name": "SBI", "dhan_id": "3045", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "BHARTIARTL": {"name": "Bharti Airtel", "dhan_id": "10604", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "BAJFINANCE": {"name": "Bajaj Finance", "dhan_id": "317", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "KOTAKBANK": {"name": "Kotak Bank", "dhan_id": "1922", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "LT": {"name": "L&T", "dhan_id": "11483", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "HCLTECH": {"name": "HCL Tech", "dhan_id": "7229", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "ASIANPAINT": {"name": "Asian Paints", "dhan_id": "236", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "AXISBANK": {"name": "Axis Bank", "dhan_id": "5900", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "MARUTI": {"name": "Maruti", "dhan_id": "10999", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "SUNPHARMA": {"name": "Sun Pharma", "dhan_id": "3351", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "TITAN": {"name": "Titan", "dhan_id": "3506", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "ULTRACEMCO": {"name": "UltraTech", "dhan_id": "11532", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "BAJAJFINSV": {"name": "Bajaj Finserv", "dhan_id": "16675", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "WIPRO": {"name": "Wipro", "dhan_id": "3787", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "NESTLEIND": {"name": "Nestle", "dhan_id": "17963", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "TATAMOTORS": {"name": "Tata Motors", "dhan_id": "3456", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "M_M": {"name": "M&M", "dhan_id": "2031", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "POWERGRID": {"name": "Power Grid", "dhan_id": "14977", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    # Nippon ETF symbols for the terminal add-on list
    "AUTOBEES": {"name": "Nippon Auto ETF", "dhan_id": "7880", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "BANKBEES": {"name": "Nippon Nifty Bank ETF", "dhan_id": "11439", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "CONSUMBEES": {
        "name": "Nippon Nifty Consumption ETF",
        "dhan_id": "2435",
        "dhan_seg": "NSE_EQ",
        "dhan_type": "EQUITY",
    },
    "DIVOPPBEES": {
        "name": "Nippon Nifty 50 Dividend Opportunities ETF",
        "dhan_id": "2636",
        "dhan_seg": "NSE_EQ",
        "dhan_type": "EQUITY",
    },
    "GILT5YBEES": {"name": "Nippon 5 Year G-Sec ETF", "dhan_id": "3172", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "GOLDBEES": {"name": "Nippon Gold ETF", "dhan_id": "14428", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "HNGSNGBEES": {"name": "Nippon Hang Seng ETF", "dhan_id": "18284", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "INFRABEES": {"name": "Nippon Nifty Infra ETF", "dhan_id": "20072", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "ITBEES": {"name": "Nippon Nifty IT ETF", "dhan_id": "19084", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "JUNIORBEES": {"name": "Nippon Nifty Next 50 ETF", "dhan_id": "10939", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "LIQGRWBEES": {
        "name": "Nippon Nifty 1D Rate Liquid ETF",
        "dhan_id": "757725",
        "dhan_seg": "NSE_EQ",
        "dhan_type": "EQUITY",
    },
    "LIQUIDBEES": {"name": "Nippon Nifty Liquid ETF", "dhan_id": "11006", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "LTGILTBEES": {
        "name": "Nippon 8-13 Year G-Sec ETF",
        "dhan_id": "17700",
        "dhan_seg": "NSE_EQ",
        "dhan_type": "EQUITY",
    },
    "MANUFGBEES": {
        "name": "Nippon Nifty India Manufacturing ETF",
        "dhan_id": "758667",
        "dhan_seg": "NSE_EQ",
        "dhan_type": "EQUITY",
    },
    "MID150BEES": {
        "name": "Nippon Nifty Midcap 150 ETF",
        "dhan_id": "8506",
        "dhan_seg": "NSE_EQ",
        "dhan_type": "EQUITY",
    },
    "NIF100BEES": {"name": "Nippon Nifty 100 ETF", "dhan_id": "29577", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "NIFTYBEES": {"name": "Nippon Nifty 50 ETF", "dhan_id": "10576", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "NV20BEES": {
        "name": "Nippon Nifty 50 Value 20 ETF",
        "dhan_id": "9847",
        "dhan_seg": "NSE_EQ",
        "dhan_type": "EQUITY",
    },
    "PHARMABEES": {"name": "Nippon Pharma ETF", "dhan_id": "4973", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "PSUBNKBEES": {
        "name": "Nippon Nifty PSU Bank ETF",
        "dhan_id": "15032",
        "dhan_seg": "NSE_EQ",
        "dhan_type": "EQUITY",
    },
    "SHARIABEES": {
        "name": "Nippon Nifty 50 Shariah ETF",
        "dhan_id": "17044",
        "dhan_seg": "NSE_EQ",
        "dhan_type": "EQUITY",
    },
    "SILVERBEES": {"name": "Nippon Silver ETF", "dhan_id": "8080", "dhan_seg": "NSE_EQ", "dhan_type": "EQUITY"},
    "SNXT30BEES": {
        "name": "Nippon BSE Sensex Next 30 ETF",
        "dhan_id": "757455",
        "dhan_seg": "NSE_EQ",
        "dhan_type": "EQUITY",
    },
}

NIFTY200_STOCKS = [
    {"symbol": "ABB", "name": "ABB India"},
    {"symbol": "ADANIENSOL", "name": "Adani Energy Solutions"},
    {"symbol": "ADANIENT", "name": "Adani Enterprises"},
    {"symbol": "ADANIGREEN", "name": "Adani Green Energy"},
    {"symbol": "ADANIPORTS", "name": "Adani Ports & SEZ"},
    {"symbol": "ADANIPOWER", "name": "Adani Power"},
    {"symbol": "AMBUJACEM", "name": "Ambuja Cements"},
    {"symbol": "APOLLOHOSP", "name": "Apollo Hospitals Enterprise"},
    {"symbol": "ASIANPAINT", "name": "Asian Paints"},
    {"symbol": "DMART", "name": "Avenue Supermarts"},
    {"symbol": "AXISBANK", "name": "Axis Bank"},
    {"symbol": "BAJAJ-AUTO", "name": "Bajaj Auto"},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance"},
    {"symbol": "BAJAJFINSV", "name": "Bajaj Finserv"},
    {"symbol": "BAJAJHLDNG", "name": "Bajaj Holdings & Investment"},
    {"symbol": "BANKBARODA", "name": "Bank of Baroda"},
    {"symbol": "BEL", "name": "Bharat Electronics"},
    {"symbol": "BPCL", "name": "Bharat Petroleum Corporation"},
    {"symbol": "BHARTIARTL", "name": "Bharti Airtel"},
    {"symbol": "BOSCHLTD", "name": "Bosch"},
    {"symbol": "BRITANNIA", "name": "Britannia Industries"},
    {"symbol": "CANBK", "name": "Canara Bank"},
    {"symbol": "CGPOWER", "name": "CG Power and Industrial Solutions"},
    {"symbol": "CHOLAFIN", "name": "Cholamandalam Investment & Finance"},
    {"symbol": "CIPLA", "name": "Cipla"},
    {"symbol": "COALINDIA", "name": "Coal India"},
    {"symbol": "CUMMINSIND", "name": "Cummins India"},
    {"symbol": "DIVISLAB", "name": "Divi's Laboratories"},
    {"symbol": "DLF", "name": "DLF"},
    {"symbol": "DRREDDY", "name": "Dr Reddy's Laboratories"},
    {"symbol": "EICHERMOT", "name": "Eicher Motors"},
    {"symbol": "ETERNAL", "name": "Eternal"},
    {"symbol": "GAIL", "name": "GAIL (India)"},
    {"symbol": "GODREJCP", "name": "Godrej Consumer Products"},
    {"symbol": "GRASIM", "name": "Grasim Industries"},
    {"symbol": "HCLTECH", "name": "HCL Technologies"},
    {"symbol": "HDFCAMC", "name": "HDFC Asset Management Company"},
    {"symbol": "HDFCBANK", "name": "HDFC Bank"},
    {"symbol": "HDFCLIFE", "name": "HDFC Life Insurance"},
    {"symbol": "HINDALCO", "name": "Hindalco Industries"},
    {"symbol": "HAL", "name": "Hindustan Aeronautics"},
    {"symbol": "HINDUNILVR", "name": "Hindustan Unilever"},
    {"symbol": "HINDZINC", "name": "Hindustan Zinc"},
    {"symbol": "HYUNDAI", "name": "Hyundai Motor India"},
    {"symbol": "ICICIBANK", "name": "ICICI Bank"},
    {"symbol": "INDHOTEL", "name": "Indian Hotels Company"},
    {"symbol": "IOC", "name": "Indian Oil Corporation"},
    {"symbol": "IRFC", "name": "Indian Railway Finance Corporation"},
    {"symbol": "INFY", "name": "Infosys"},
    {"symbol": "INDIGO", "name": "InterGlobe Aviation"},
    {"symbol": "ITC", "name": "ITC"},
    {"symbol": "JINDALSTEL", "name": "Jindal Steel"},
    {"symbol": "JIOFIN", "name": "Jio Financial Services"},
    {"symbol": "JSWSTEEL", "name": "JSW Steel"},
    {"symbol": "KOTAKBANK", "name": "Kotak Mahindra Bank"},
    {"symbol": "LT", "name": "Larsen & Toubro"},
    {"symbol": "LODHA", "name": "Lodha Developers"},
    {"symbol": "LTIM", "name": "LTIMindtree"},
    {"symbol": "M&M", "name": "Mahindra & Mahindra"},
    {"symbol": "MARUTI", "name": "Maruti Suzuki India"},
    {"symbol": "MAXHEALTH", "name": "Max Healthcare Institute"},
    {"symbol": "MAZDOCK", "name": "Mazagon Dock Shipbuilders"},
    {"symbol": "MUTHOOTFIN", "name": "Muthoot Finance"},
    {"symbol": "NESTLEIND", "name": "Nestle India"},
    {"symbol": "NTPC", "name": "NTPC"},
    {"symbol": "ONGC", "name": "Oil & Natural Gas Corporation"},
    {"symbol": "PIDILITIND", "name": "Pidilite Industries"},
    {"symbol": "PFC", "name": "Power Finance Corporation"},
    {"symbol": "POWERGRID", "name": "Power Grid Corporation of India"},
    {"symbol": "PNB", "name": "Punjab National Bank"},
    {"symbol": "RECLTD", "name": "REC"},
    {"symbol": "RELIANCE", "name": "Reliance Industries"},
    {"symbol": "MOTHERSON", "name": "Samvardhana Motherson International"},
    {"symbol": "SBILIFE", "name": "SBI Life Insurance Company"},
    {"symbol": "SHREECEM", "name": "Shree Cement"},
    {"symbol": "SHRIRAMFIN", "name": "Shriram Finance"},
    {"symbol": "ENRIN", "name": "Siemens Energy India"},
    {"symbol": "SIEMENS", "name": "Siemens"},
    {"symbol": "SOLARINDS", "name": "Solar Industries India"},
    {"symbol": "SBIN", "name": "State Bank of India"},
    {"symbol": "SUNPHARMA", "name": "Sun Pharmaceutical Industries"},
    {"symbol": "TATACAP", "name": "Tata Capital"},
    {"symbol": "TCS", "name": "Tata Consultancy Services"},
    {"symbol": "TATACONSUM", "name": "Tata Consumer Products"},
    {"symbol": "TMCV", "name": "Tata Motors Commercial Vehicles"},
    {"symbol": "TMPV", "name": "Tata Motors Passenger Vehicles"},
    {"symbol": "TATAPOWER", "name": "Tata Power Company"},
    {"symbol": "TATASTEEL", "name": "Tata Steel"},
    {"symbol": "TECHM", "name": "Tech Mahindra"},
    {"symbol": "TITAN", "name": "Titan Company"},
    {"symbol": "TORNTPHARM", "name": "Torrent Pharmaceuticals"},
    {"symbol": "TRENT", "name": "Trent"},
    {"symbol": "TVSMOTOR", "name": "TVS Motor Company"},
    {"symbol": "ULTRACEMCO", "name": "UltraTech Cement"},
    {"symbol": "UNIONBANK", "name": "Union Bank of India"},
    {"symbol": "UNITDSPR", "name": "United Spirits"},
    {"symbol": "VBL", "name": "Varun Beverages"},
    {"symbol": "VEDL", "name": "Vedanta"},
    {"symbol": "WIPRO", "name": "Wipro"},
    {"symbol": "ZYDUSLIFE", "name": "Zydus Lifesciences"},
    {"symbol": "360ONE", "name": "360 ONE WAM"},
    {"symbol": "ABCAPITAL", "name": "Aditya Birla Capital"},
    {"symbol": "ALKEM", "name": "Alkem Laboratories"},
    {"symbol": "APLAPOLLO", "name": "APL Apollo Tubes"},
    {"symbol": "ASHOKLEY", "name": "Ashok Leyland"},
    {"symbol": "ASTRAL", "name": "Astral"},
    {"symbol": "ATGL", "name": "Adani Total Gas"},
    {"symbol": "AUBANK", "name": "AU Small Finance Bank"},
    {"symbol": "AUROPHARMA", "name": "Aurobindo Pharma"},
    {"symbol": "BANKINDIA", "name": "Bank of India"},
    {"symbol": "BDL", "name": "Bharat Dynamics"},
    {"symbol": "BHARATFORG", "name": "Bharat Forge"},
    {"symbol": "BHEL", "name": "Bharat Heavy Electricals"},
    {"symbol": "BIOCON", "name": "Biocon"},
    {"symbol": "BLUESTARCO", "name": "Blue Star"},
    {"symbol": "BSE", "name": "BSE"},
    {"symbol": "COCHINSHIP", "name": "Cochin Shipyard"},
    {"symbol": "COFORGE", "name": "Coforge"},
    {"symbol": "COLPAL", "name": "Colgate-Palmolive (India)"},
    {"symbol": "CONCOR", "name": "Container Corporation of India"},
    {"symbol": "COROMANDEL", "name": "Coromandel International"},
    {"symbol": "DABUR", "name": "Dabur India"},
    {"symbol": "DIXON", "name": "Dixon Technologies (India)"},
    {"symbol": "EXIDEIND", "name": "Exide Industries"},
    {"symbol": "FEDERALBNK", "name": "Federal Bank"},
    {"symbol": "FORTIS", "name": "Fortis Healthcare"},
    {"symbol": "GLENMARK", "name": "Glenmark Pharmaceuticals"},
    {"symbol": "GMRAIRPORT", "name": "GMR Airports"},
    {"symbol": "GODFRYPHLP", "name": "Godfrey Phillips India"},
    {"symbol": "GODREJPROP", "name": "Godrej Properties"},
    {"symbol": "GROWW", "name": "Groww"},
    {"symbol": "GVT&D", "name": "GE Vernova T&D India"},
    {"symbol": "HAVELLS", "name": "Havells India"},
    {"symbol": "HEROMOTOCO", "name": "Hero MotoCorp"},
    {"symbol": "HINDPETRO", "name": "Hindustan Petroleum Corporation"},
    {"symbol": "HUDCO", "name": "Housing and Urban Development Corporation"},
    {"symbol": "ICICIAMC", "name": "ICICI Prudential Asset Management"},
    {"symbol": "ICICIGI", "name": "ICICI Lombard General Insurance"},
    {"symbol": "IDFCFIRSTB", "name": "IDFC First Bank"},
    {"symbol": "IDEA", "name": "Vodafone Idea"},
    {"symbol": "INDIANB", "name": "Indian Bank"},
    {"symbol": "INDUSINDBK", "name": "IndusInd Bank"},
    {"symbol": "INDUSTOWER", "name": "Indus Towers"},
    {"symbol": "IREDA", "name": "Indian Renewable Energy Development Agency"},
    {"symbol": "IRCTC", "name": "Indian Railway Catering & Tourism Corporation"},
    {"symbol": "JSWENERGY", "name": "JSW Energy"},
    {"symbol": "JUBLFOOD", "name": "Jubilant Foodworks"},
    {"symbol": "KALYANKJIL", "name": "Kalyan Jewellers India"},
    {"symbol": "KEI", "name": "KEI Industries"},
    {"symbol": "KPITTECH", "name": "KPIT Technologies"},
    {"symbol": "LAURUSLABS", "name": "Laurus Labs"},
    {"symbol": "LENSKART", "name": "Lenskart Solutions"},
    {"symbol": "LGEINDIA", "name": "LG Electronics India"},
    {"symbol": "LICHSGFIN", "name": "LIC Housing Finance"},
    {"symbol": "LTF", "name": "L&T Finance"},
    {"symbol": "LUPIN", "name": "Lupin"},
    {"symbol": "M&MFIN", "name": "Mahindra & Mahindra Financial Services"},
    {"symbol": "MANKIND", "name": "Mankind Pharma"},
    {"symbol": "MARICO", "name": "Marico"},
    {"symbol": "MCX", "name": "Multi Commodity Exchange of India"},
    {"symbol": "MFSL", "name": "Max Financial Services"},
    {"symbol": "MOTILALOFS", "name": "Motilal Oswal Financial Services"},
    {"symbol": "MPHASIS", "name": "Mphasis"},
    {"symbol": "MRF", "name": "MRF"},
    {"symbol": "NATIONALUM", "name": "National Aluminium Company"},
    {"symbol": "NAUKRI", "name": "Info Edge (India)"},
    {"symbol": "NHPC", "name": "NHPC"},
    {"symbol": "NMDC", "name": "NMDC"},
    {"symbol": "NYKAA", "name": "FSN E-Commerce Ventures"},
    {"symbol": "OBEROIRLTY", "name": "Oberoi Realty"},
    {"symbol": "OFSS", "name": "Oracle Financial Services Software"},
    {"symbol": "OIL", "name": "Oil India"},
    {"symbol": "PAGEIND", "name": "Page Industries"},
    {"symbol": "PATANJALI", "name": "Patanjali Foods"},
    {"symbol": "PAYTM", "name": "One97 Communications"},
    {"symbol": "PERSISTENT", "name": "Persistent Systems"},
    {"symbol": "PHOENIXLTD", "name": "Phoenix Mills"},
    {"symbol": "PIIND", "name": "PI Industries"},
    {"symbol": "POLICYBZR", "name": "PB Fintech"},
    {"symbol": "POLYCAB", "name": "Polycab India"},
    {"symbol": "POWERINDIA", "name": "Hitachi Energy India"},
    {"symbol": "PREMIERENE", "name": "Premier Energies"},
    {"symbol": "PRESTIGE", "name": "Prestige Estates Projects"},
    {"symbol": "RADICO", "name": "Radico Khaitan"},
    {"symbol": "RVNL", "name": "Rail Vikas Nigam"},
    {"symbol": "SAIL", "name": "Steel Authority of India"},
    {"symbol": "SBICARD", "name": "SBI Cards and Payment Services"},
    {"symbol": "SRF", "name": "SRF"},
    {"symbol": "SUPREMEIND", "name": "Supreme Industries"},
    {"symbol": "SUZLON", "name": "Suzlon Energy"},
    {"symbol": "SWIGGY", "name": "Swiggy"},
    {"symbol": "TATACOMM", "name": "Tata Communications"},
    {"symbol": "TATAELXSI", "name": "Tata Elxsi"},
    {"symbol": "TATAINVEST", "name": "Tata Investment Corporation"},
    {"symbol": "TIINDIA", "name": "Tube Investments of India"},
    {"symbol": "UPL", "name": "UPL"},
    {"symbol": "VMM", "name": "Vishal Mega Mart"},
    {"symbol": "VOLTAS", "name": "Voltas"},
    {"symbol": "WAAREEENER", "name": "Waaree Energies"},
    {"symbol": "YESBANK", "name": "YES Bank"},
]

BEES_ETFS = [
    {"symbol": "AUTOBEES", "name": "Nippon Auto ETF"},
    {"symbol": "BANKBEES", "name": "Nippon Nifty Bank ETF"},
    {"symbol": "CONSUMBEES", "name": "Nippon Nifty Consumption ETF"},
    {"symbol": "DIVOPPBEES", "name": "Nippon Nifty 50 Dividend Opportunities ETF"},
    {"symbol": "GILT5YBEES", "name": "Nippon 5 Year G-Sec ETF"},
    {"symbol": "GOLDBEES", "name": "Nippon Gold ETF"},
    {"symbol": "HNGSNGBEES", "name": "Nippon Hang Seng ETF"},
    {"symbol": "INFRABEES", "name": "Nippon Nifty Infra ETF"},
    {"symbol": "ITBEES", "name": "Nippon Nifty IT ETF"},
    {"symbol": "JUNIORBEES", "name": "Nippon Nifty Next 50 ETF"},
    {"symbol": "LIQGRWBEES", "name": "Nippon Nifty 1D Rate Liquid ETF"},
    {"symbol": "LIQUIDBEES", "name": "Nippon Nifty Liquid ETF"},
    {"symbol": "LTGILTBEES", "name": "Nippon 8-13 Year G-Sec ETF"},
    {"symbol": "MANUFGBEES", "name": "Nippon Nifty India Manufacturing ETF"},
    {"symbol": "MID150BEES", "name": "Nippon Nifty Midcap 150 ETF"},
    {"symbol": "NIF100BEES", "name": "Nippon Nifty 100 ETF"},
    {"symbol": "NIFTYBEES", "name": "Nippon Nifty 50 ETF"},
    {"symbol": "NV20BEES", "name": "Nippon Nifty 50 Value 20 ETF"},
    {"symbol": "PHARMABEES", "name": "Nippon Pharma ETF"},
    {"symbol": "PSUBNKBEES", "name": "Nippon Nifty PSU Bank ETF"},
    {"symbol": "SHARIABEES", "name": "Nippon Nifty 50 Shariah ETF"},
    {"symbol": "SILVERBEES", "name": "Nippon Silver ETF"},
    {"symbol": "SNXT30BEES", "name": "Nippon BSE Sensex Next 30 ETF"},
]

TERMINAL_STOCKS = NIFTY200_STOCKS + BEES_ETFS
# The scanner's min-price gate is a stock-quality heuristic; BEES ETFs are
# index proxies and cheap by design, so they are exempted from it by flag.
_BEES_SYMBOLS = frozenset(row["symbol"] for row in BEES_ETFS)
_TERMINAL_BY_SYMBOL = {ScripMaster.normalize_equity_symbol(stock["symbol"]): stock for stock in TERMINAL_STOCKS}
_NIFTY200_FALLBACK_ALIASES = {"M&M": "M_M"}

_TERMINAL_CASCADE_REFERENCE_INDEX = {
    "NIFTY": {
        "symbol": "NIFTY",
        "name": "NIFTY 50 Index",
        "security_id": "13",
        "exchange_segment": "IDX_I",
        "instrument_type": "INDEX",
    },
    "BANKNIFTY": {
        "symbol": "BANKNIFTY",
        "name": "BANK NIFTY Index",
        "security_id": "25",
        "exchange_segment": "IDX_I",
        "instrument_type": "INDEX",
    },
}
_TERMINAL_CASCADE_TIMEFRAMES = {
    "5m": ("5", 5),
    "15m": ("15", 15),
    "1h": ("60", 60),
    "1d": ("D", CashCascadePaperEngine.DAILY_BAR_MINUTES),
}
# How far back a mother may sit is a bar budget, not a calendar rule: the same
# replay cost buys ~2 weeks of 5m or a year of daily. Sessions per timeframe
# times 7/5 turns the budget into calendar days, capped at a year for sanity.
_TERMINAL_CASCADE_BARS_PER_SESSION = {"5m": 75, "15m": 25, "1h": 7, "1d": 1}
_TERMINAL_CASCADE_REPLAY_BAR_BUDGET = 800


def _terminal_cascade_max_mother_age_days(timeframe: str) -> int:
    _interval, _minutes, normalised = _terminal_cascade_timeframe_parts(timeframe)
    sessions = _TERMINAL_CASCADE_REPLAY_BAR_BUDGET / _TERMINAL_CASCADE_BARS_PER_SESSION[normalised]
    return min(365, int(sessions * 7 / 5) + 3)


def _resolve_terminal_stock(symbol: str) -> dict:
    """Resolve a terminal symbol to Dhan NSE_EQ metadata."""
    normalized = ScripMaster.normalize_equity_symbol(symbol)
    stock = _TERMINAL_BY_SYMBOL.get(normalized)
    if not stock:
        raise HTTPException(status_code=404, detail=f"Unknown terminal symbol: {symbol}")

    equity = {}
    try:
        equity = ScripMaster.lookup_equity(normalized) or {}
    except Exception as exc:
        print(f"[TERMINAL] Equity lookup failed for {normalized}: {exc}")

    fallback_key = _NIFTY200_FALLBACK_ALIASES.get(normalized, normalized)
    fallback = INSTRUMENT_MAP.get(fallback_key, {})
    security_id = str(equity.get("security_id") or fallback.get("dhan_id") or "")
    resolved = {
        "symbol": normalized,
        "name": stock["name"],
        "security_id": security_id,
        "exchange_segment": equity.get("exchange_segment") or fallback.get("dhan_seg") or "NSE_EQ",
        "instrument_type": equity.get("instrument_type") or fallback.get("dhan_type") or "EQUITY",
        "tradable": bool(security_id),
    }
    signal_symbol = cash_cascade_reference_symbol(normalized)
    reference = _TERMINAL_CASCADE_REFERENCE_INDEX.get(signal_symbol)
    resolved["cascade_reference"] = {
        "mode": "reference_index" if reference else "own_scrip",
        "symbol": reference["symbol"] if reference else normalized,
        "name": reference["name"] if reference else stock["name"],
    }
    return resolved


def _extract_marketfeed_ltp(data: dict, exchange_segment: str, security_id: str) -> float:
    if not isinstance(data, dict):
        return 0.0
    seg_data = data.get(exchange_segment, {})
    if isinstance(seg_data, dict):
        sid_data = seg_data.get(str(security_id), seg_data.get(int(security_id), {}))
        if isinstance(sid_data, dict):
            return float(sid_data.get("last_price", sid_data.get("ltp", 0)) or 0)
        if isinstance(sid_data, (int, float)):
            return float(sid_data)
    for val in data.values():
        if isinstance(val, dict):
            for nested in val.values():
                if isinstance(nested, dict):
                    return float(nested.get("last_price", nested.get("ltp", 0)) or 0)
                if isinstance(nested, (int, float)):
                    return float(nested)
    return 0.0


def _terminal_cascade_live_gate_status() -> dict:
    """What the Terminal can actually do live, stated accurately.

    This used to read as though live were built and merely awaiting approval --
    "blocked until explicitly wired and approved", with the env flag implying a
    switch that turns it on. There is no such switch. engine/cascade_equity_live.py
    is complete and tested (resting SL buys, cancel-and-replace targets, gap-up
    skip, guardrails) and is imported by NOTHING but its own test file: no route
    constructs an executor, so no code path can place a cash order at all.
    Setting PHILFORGE_TERMINAL_CASCADE_LIVE changes nothing.

    Saying so plainly matters more than it looks. A gate that reads "locked"
    invites someone to go looking for the key; a gate that says the road is not
    built does not.
    """
    return {
        "enabled": False,
        "armed": False,
        "flag_set": bool(_TERMINAL_CASCADE_LIVE_FLAG),
        "wired": False,
        "reason": (
            "Terminal Cascade is PAPER ONLY. The live executor exists but is not connected to any "
            "route, so no code path can submit a cash order — the server flag does not change that. "
            "Wiring it is deliberately pending the held-position problem: the 2-year backtest banks "
            "+Rs 36k on closed rounds but carries -Rs 88k of unsold stock, so live execution would "
            "faithfully deliver a losing strategy."
        ),
    }


def _terminal_cascade_timeframe_parts(timeframe: str) -> tuple[str, int, str]:
    normalised = str(timeframe or "5m").lower()
    if normalised not in _TERMINAL_CASCADE_TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Terminal Cascade timeframe must be 5m, 15m, 1h, or 1d.")
    interval, minutes = _TERMINAL_CASCADE_TIMEFRAMES[normalised]
    return interval, minutes, normalised


def _terminal_cascade_instruments(symbol: str) -> tuple[CashCascadeInstrument, dict, dict]:
    trade = _resolve_terminal_stock(symbol)
    if not trade["security_id"]:
        raise HTTPException(status_code=400, detail=f"No Dhan security ID found for {trade['symbol']}")
    signal_symbol = cash_cascade_reference_symbol(trade["symbol"])
    reference = _TERMINAL_CASCADE_REFERENCE_INDEX.get(signal_symbol)
    signal = (
        reference
        if reference
        else {
            "symbol": trade["symbol"],
            "name": trade["name"],
            "security_id": trade["security_id"],
            "exchange_segment": trade["exchange_segment"],
            "instrument_type": trade["instrument_type"],
        }
    )
    instrument = CashCascadeInstrument(
        symbol=trade["symbol"],
        name=trade["name"],
        security_id=trade["security_id"],
        exchange_segment=trade["exchange_segment"],
        instrument_type=trade["instrument_type"],
        signal_symbol=signal["symbol"],
        signal_name=signal["name"],
        signal_security_id=signal["security_id"],
        signal_exchange_segment=signal["exchange_segment"],
        signal_instrument_type=signal["instrument_type"],
    )
    return instrument, signal, trade


async def _terminal_cascade_load_candles(
    broker: DhanClient,
    instrument: Mapping[str, Any],
    timeframe: str,
    *,
    from_date: date,
    to_date: date,
) -> list[IndexCandle]:
    interval, minutes, _normalised = _terminal_cascade_timeframe_parts(timeframe)

    def _fetch(chunk_from: date, chunk_to: date):
        return broker.get_historical_data(
            security_id=str(instrument["security_id"]),
            exchange_segment=str(instrument["exchange_segment"]),
            instrument_type=str(instrument["instrument_type"]),
            from_date=chunk_from.isoformat(),
            to_date=chunk_to.isoformat(),
            candle_type=interval,
        )

    # Dhan's intraday endpoint serves a bounded range per request; the daily
    # endpoint takes years in one call. Chunk intraday spans the same way the
    # backtest data path does (INTRADAY_CHUNK_DAYS) and stitch the frames.
    if interval == "D" or (to_date - from_date).days <= INTRADAY_CHUNK_DAYS:
        frame = await asyncio.to_thread(_fetch, from_date, to_date)
    else:
        frames = []
        cursor = from_date
        while cursor <= to_date:
            chunk_end = min(cursor + timedelta(days=INTRADAY_CHUNK_DAYS), to_date)
            chunk = await asyncio.to_thread(_fetch, cursor, chunk_end)
            if chunk is not None and not getattr(chunk, "empty", True):
                frames.append(chunk)
            cursor = chunk_end + timedelta(days=1)
        if not frames:
            return []
        frame = pd.concat(frames)
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    return CashCascadePaperEngine.normalise_frame(frame, datetime.now(IST), timeframe_minutes=minutes)


async def _terminal_cascade_load_mother_pair(
    broker: DhanClient,
    signal_instrument: Mapping[str, Any],
    trade_instrument: Mapping[str, Any],
    timeframe: str,
    mother_timestamp: datetime,
) -> tuple[IndexCandle, IndexCandle]:
    signal_candles, trade_candles = await asyncio.gather(
        _terminal_cascade_load_candles(
            broker, signal_instrument, timeframe, from_date=mother_timestamp.date(), to_date=mother_timestamp.date()
        ),
        _terminal_cascade_load_candles(
            broker, trade_instrument, timeframe, from_date=mother_timestamp.date(), to_date=mother_timestamp.date()
        ),
    )
    signal = next((row for row in signal_candles if row.timestamp == mother_timestamp), None)
    trade = next((row for row in trade_candles if row.timestamp == mother_timestamp), None)
    if signal is None:
        raise HTTPException(
            status_code=404,
            detail=f"No closed {signal_instrument['symbol']} {timeframe} signal candle at that IST timestamp.",
        )
    if trade is None:
        raise HTTPException(
            status_code=404,
            detail=f"No closed {trade_instrument['symbol']} {timeframe} traded candle at that IST timestamp.",
        )
    return signal, trade


def _terminal_cascade_pair_candles(
    signal_candles: list[IndexCandle], trade_candles: list[IndexCandle], after: datetime
) -> list[tuple[IndexCandle, IndexCandle]]:
    trade_by_time = {row.timestamp: row for row in trade_candles}
    pairs: list[tuple[IndexCandle, IndexCandle]] = []
    for signal in sorted(signal_candles, key=lambda row: row.timestamp):
        if signal.timestamp <= after:
            continue
        trade = trade_by_time.get(signal.timestamp)
        if trade is not None:
            pairs.append((signal, trade))
    return pairs


def _terminal_cascade_ltp(broker: DhanClient, instrument: Mapping[str, Any]) -> float:
    payload = broker.get_ltp([str(instrument["security_id"])], exchange_segment=str(instrument["exchange_segment"]))
    return float(_extract_marketfeed_ltp(payload, str(instrument["exchange_segment"]), str(instrument["security_id"])))


async def _terminal_cascade_quote_pair(runtime: _TerminalCascadeRuntime) -> tuple[IndexCandle, IndexCandle]:
    now = datetime.now(IST)
    signal_price, trade_price = await asyncio.gather(
        asyncio.to_thread(_terminal_cascade_ltp, runtime.broker, runtime.signal_instrument),
        asyncio.to_thread(_terminal_cascade_ltp, runtime.broker, runtime.trade_instrument),
    )
    signal = IndexCandle(now, signal_price, signal_price, signal_price, signal_price)
    trade = IndexCandle(now, trade_price, trade_price, trade_price, trade_price)
    return signal, trade


async def _notify_terminal_cascade_ws(user_id: int) -> None:
    runtimes = _terminal_cascade_engines.get(int(user_id), {})
    campaigns = [
        {**runtime.engine.get_status(), "running": runtime.running} for _symbol, runtime in sorted(runtimes.items())
    ]
    await _broadcast_user_ws_json(
        int(user_id),
        {
            "type": "terminal_cascade_status",
            "terminal_cascade": {"campaigns": campaigns},
        },
    )


async def _save_terminal_cascade_open_state(
    user_id: int, _runtime: _TerminalCascadeRuntime | None = None, *, force: bool = False
) -> None:
    runtimes = _terminal_cascade_engines.get(int(user_id), {})
    if not runtimes:
        return
    now = time.time()
    if (
        not force
        and now - _terminal_cascade_open_state_last_save[int(user_id)] < _TERMINAL_CASCADE_OPEN_STATE_SAVE_INTERVAL_SEC
    ):
        return
    payload = {
        "campaigns": [
            {
                "running": bool(runtime.running),
                "last_candle_timestamp": runtime.last_candle_timestamp.isoformat(),
                "signal_instrument": dict(runtime.signal_instrument),
                "trade_instrument": dict(runtime.trade_instrument),
                "engine": runtime.engine.to_dict(),
            }
            for _symbol, runtime in sorted(runtimes.items())
        ],
        "saved_at": datetime.now(IST).isoformat(),
    }
    await _db_mod.set_app_state(_terminal_cascade_open_state_key(user_id), json.dumps(payload, default=str))
    _terminal_cascade_open_state_last_save[int(user_id)] = now


async def _restore_terminal_cascade_open_state(
    user_id: int, broker: DhanClient | None
) -> Dict[str, _TerminalCascadeRuntime]:
    existing = _terminal_cascade_engines.get(int(user_id))
    if existing is not None:
        return existing
    if broker is None:
        return {}
    raw = await _db_mod.get_app_state(_terminal_cascade_open_state_key(user_id))
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {}
        # Accept the prior single-campaign record once, then always persist the
        # per-scrip shape.  This keeps existing paper campaigns recoverable.
        records = payload.get("campaigns")
        if not isinstance(records, list):
            records = [payload] if payload.get("engine") else []
        runtimes: Dict[str, _TerminalCascadeRuntime] = {}
        for record in records:
            if not isinstance(record, dict) or not record.get("engine"):
                continue
            engine = CashCascadePaperEngine.from_dict(record["engine"])
            last_text = str(record.get("last_candle_timestamp") or "")
            last = (
                datetime.fromisoformat(last_text.replace("Z", "+00:00"))
                if last_text
                else engine.geometry.history[-1].timestamp
            )
            if last.tzinfo is None:
                last = last.replace(tzinfo=IST)
            runtime = _TerminalCascadeRuntime(
                engine=engine,
                broker=broker,
                signal_instrument=dict(record.get("signal_instrument") or {}),
                trade_instrument=dict(record.get("trade_instrument") or {}),
                last_candle_timestamp=last,
                running=bool(record.get("running")),
            )
            symbol = ScripMaster.normalize_equity_symbol(runtime.engine.instrument.symbol)
            runtimes[symbol] = runtime
        if not runtimes:
            return {}
        _terminal_cascade_engines[int(user_id)] = runtimes
        for runtime in runtimes.values():
            if runtime.running and _engine_restore_owner_is_active_instance():
                runtime.task = asyncio.create_task(_run_terminal_cascade_paper_loop(int(user_id), runtime))
        return runtimes
    except Exception as exc:
        _logger.warning("[TERMINAL CASCADE] Skipping invalid persisted campaign for user %s: %s", user_id, exc)
        return {}


def _terminal_cascade_offsession_sleep_sec() -> float:
    """Seconds to idle when the NSE cash session is closed; 0 while it is open.

    The paper loop was polling Dhan for new candles every 12 seconds all
    night, every night — two history calls per campaign per tick. Nothing can
    change outside the session, and that standing load is what earned the
    account sustained 429s. A small margin around 09:15–15:30 keeps the first
    and last bars prompt.
    """
    now = datetime.now(IST)
    if now.weekday() < 5 and dt_time(9, 10) <= now.time() <= dt_time(15, 35):
        return 0.0
    return 300.0


async def _run_terminal_cascade_paper_loop(user_id: int, runtime: _TerminalCascadeRuntime) -> None:
    symbol = ScripMaster.normalize_equity_symbol(runtime.engine.instrument.symbol)
    while runtime.running and _terminal_cascade_engines.get(int(user_id), {}).get(symbol) is runtime:
        idle = _terminal_cascade_offsession_sleep_sec()
        if idle:
            await asyncio.sleep(idle)
            continue
        try:
            today = datetime.now(IST).date()
            start = runtime.last_candle_timestamp.date()
            signal_candles, trade_candles = await asyncio.gather(
                _terminal_cascade_load_candles(
                    runtime.broker,
                    runtime.signal_instrument,
                    runtime.engine.config.timeframe,
                    from_date=start,
                    to_date=today,
                ),
                _terminal_cascade_load_candles(
                    runtime.broker,
                    runtime.trade_instrument,
                    runtime.engine.config.timeframe,
                    from_date=start,
                    to_date=today,
                ),
            )
            for signal, trade in _terminal_cascade_pair_candles(
                signal_candles, trade_candles, runtime.last_candle_timestamp
            ):
                runtime.last_candle_timestamp = signal.timestamp
                runtime.engine.on_candle(signal, trade)
                await _save_terminal_cascade_open_state(user_id, runtime)
                await _notify_terminal_cascade_ws(user_id)
            # An ended campaign (mother broken/retested with nothing held) has
            # no further use for this loop — leave the record on the page and
            # stop polling Dhan for it.
            if not runtime.engine.get_status()["running"]:
                runtime.running = False
                await _save_terminal_cascade_open_state(user_id, runtime, force=True)
                await _notify_terminal_cascade_ws(user_id)
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.warning(
                "[TERMINAL CASCADE] %s paper poll failed for user %s: %s",
                runtime.engine.instrument.symbol,
                user_id,
                exc,
            )
        await asyncio.sleep(12)


async def _terminal_cascade_replay_to_now(
    broker: DhanClient,
    engine: CashCascadePaperEngine,
    signal_instrument: Mapping[str, Any],
    trade_instrument: Mapping[str, Any],
    mother_timestamp: datetime,
) -> datetime:
    today = datetime.now(IST).date()
    signal_candles, trade_candles = await asyncio.gather(
        _terminal_cascade_load_candles(
            broker, signal_instrument, engine.config.timeframe, from_date=mother_timestamp.date(), to_date=today
        ),
        _terminal_cascade_load_candles(
            broker, trade_instrument, engine.config.timeframe, from_date=mother_timestamp.date(), to_date=today
        ),
    )
    last = mother_timestamp
    for signal, trade in _terminal_cascade_pair_candles(signal_candles, trade_candles, mother_timestamp):
        engine.on_candle(signal, trade)
        last = signal.timestamp
    return last


async def _terminal_cascade_replay_with_candles(
    broker: DhanClient,
    engine: CashCascadePaperEngine,
    signal_instrument: Mapping[str, Any],
    trade_instrument: Mapping[str, Any],
    mother_timestamp: datetime,
) -> list[IndexCandle]:
    """Replay to now and return every signal candle from the mother onward.

    The chart draws from this list rather than ``engine.geometry.history``:
    the geometry stops recording the moment the mother breaks or retests, and
    a chart built from it freezes at that candle forever — which reads as
    "the chart is not refreshing" when the campaign is simply over.
    """
    today = datetime.now(IST).date()
    signal_candles, trade_candles = await asyncio.gather(
        _terminal_cascade_load_candles(
            broker, signal_instrument, engine.config.timeframe, from_date=mother_timestamp.date(), to_date=today
        ),
        _terminal_cascade_load_candles(
            broker, trade_instrument, engine.config.timeframe, from_date=mother_timestamp.date(), to_date=today
        ),
    )
    for signal, trade in _terminal_cascade_pair_candles(signal_candles, trade_candles, mother_timestamp):
        engine.on_candle(signal, trade)
    return [row for row in signal_candles if row.timestamp >= mother_timestamp]


# ── Data Fetch (Dhan only — variable timeframe via chunking) ──────────
INTRADAY_MAX_DAYS = MAX_INTRADAY_HISTORY_DAYS
ROLLING_OPTION_CHUNK_DAYS = 30
ROLLING_EXPIRY_SELECTIONS = {
    "current_week": ("WEEK", 0),
    "next_week": ("WEEK", 1),
    "current_month": ("MONTH", 0),
    "next_month": ("MONTH", 1),
}


def _fetch_data(
    instrument: str,
    from_date: str,
    to_date: str,
    segment: str = "indices",
    candle_interval: str = "5",
    broker_client: DhanClient | None = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV candles from Dhan at the requested raw interval.
    Mixed/derived strategy timeframes are handled later inside indicator computation;
    this function should return the raw candles exactly as fetched from Dhan.
    """
    inst_info = INSTRUMENT_MAP.get(instrument)
    if not inst_info:
        raise Exception(f"Unknown instrument: {instrument}. Not found in instrument map.")

    from datetime import datetime as dt
    from datetime import timedelta

    from_dt = dt.strptime(from_date, "%Y-%m-%d")
    to_dt = dt.strptime(to_date, "%Y-%m-%d")
    day_span = (to_dt - from_dt).days

    # Auto-detect: if range exceeds Dhan intraday history window, use daily candles.
    use_daily = day_span > INTRADAY_MAX_DAYS
    effective_interval = "D" if use_daily else str(candle_interval)

    if use_daily:
        print(
            f"[DATA] ⚠️  Date range is {day_span} days (>{INTRADAY_MAX_DAYS}d). "
            f"Auto-switching to DAILY candles for full coverage."
        )

    print(
        f"[DATA] Instrument={instrument} ({inst_info['name']}), DhanID={inst_info['dhan_id']}, "
        f"Segment={inst_info['dhan_seg']}, Interval={'Daily' if use_daily else f'{effective_interval}m raw'}, "
        f"From={from_date}, To={to_date}, Span={day_span}d"
    )

    client = broker_client or dhan

    if use_daily:
        # Daily candles — single request, no chunking needed
        try:
            df = client.get_historical_data(
                security_id=inst_info["dhan_id"],
                exchange_segment=inst_info["dhan_seg"],
                instrument_type=inst_info["dhan_type"],
                from_date=from_date,
                to_date=to_date,
                candle_type="D",
            )
            if df is not None and not df.empty:
                df = df[~df.index.duplicated(keep="first")]
                print(f"[DATA] ✅ Total: {len(df)} daily candles, {df.index[0]} → {df.index[-1]}")
                return df
        except Exception as e:
            raise Exception(f"Daily data fetch failed: {str(e)}")
        raise Exception(f"No daily data from Dhan for {inst_info['name']}.")

    # Intraday candles — chunk into 90-day windows
    # Dhan rate limit: ~10 requests/second. We add delay + retry on 429.
    import time as _time

    CHUNK_DAYS = INTRADAY_CHUNK_DAYS
    RATE_LIMIT_DELAY = 0.5  # seconds between API calls
    MAX_RETRIES = 3  # retry on 429 rate-limit errors
    all_dfs = []
    chunk_start = from_dt
    chunk_num = 0
    last_error = None

    while chunk_start <= to_dt:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), to_dt)
        chunk_num += 1

        cs = chunk_start.strftime("%Y-%m-%d")
        ce = chunk_end.strftime("%Y-%m-%d")

        print(f"[DATA] Chunk {chunk_num}: {cs} → {ce}")

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                df_chunk = client.get_historical_data(
                    security_id=inst_info["dhan_id"],
                    exchange_segment=inst_info["dhan_seg"],
                    instrument_type=inst_info["dhan_type"],
                    from_date=cs,
                    to_date=ce,
                    candle_type=effective_interval,
                )
                if df_chunk is not None and not df_chunk.empty:
                    all_dfs.append(df_chunk)
                    print(f"[DATA]   → {len(df_chunk)} candles")
                else:
                    print("[DATA]   → 0 candles (empty or None)")
                success = True
                break
            except Exception as e:
                last_error = str(e)
                if "429" in str(e) or "Rate_Limit" in str(e) or "DH-904" in str(e):
                    wait = RATE_LIMIT_DELAY * (2**attempt)  # exponential backoff: 1s, 2s, 4s
                    print(f"[DATA]   → Rate limited (attempt {attempt}/{MAX_RETRIES}), waiting {wait:.1f}s...")
                    _time.sleep(wait)
                else:
                    print(f"[DATA]   → Error: {last_error}")
                    break  # non-rate-limit error, skip this chunk

        if not success and attempt == MAX_RETRIES:
            print(f"[DATA]   → Failed after {MAX_RETRIES} retries")

        # Throttle between chunks to avoid rate limiting
        _time.sleep(RATE_LIMIT_DELAY)

        chunk_start = chunk_end + timedelta(days=1)

    if not all_dfs:
        error_detail = f"No intraday data from Dhan for {inst_info['name']}. Check API subscription and date range."
        if last_error:
            error_detail += f" Last error: {last_error}"
        raise Exception(error_detail)

    df = pd.concat(all_dfs).sort_index()
    # Remove duplicates (overlapping chunk boundaries)
    df = df[~df.index.duplicated(keep="first")]

    print(
        f"[DATA] ✅ Total: {len(df)} {'daily' if use_daily else f'{effective_interval}m raw'} candles across {chunk_num} chunks, "
        f"{df.index[0]} → {df.index[-1]}"
    )
    return df


def _normalize_ohlcv_export_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    normalized = df.copy().sort_index()
    rename_map = {str(column).lower(): column for column in normalized.columns}
    selected = pd.DataFrame(index=pd.to_datetime(normalized.index))
    for column in ("open", "high", "low", "close"):
        source = rename_map.get(column)
        if not source:
            raise ValueError(f"Fetched data is missing required OHLC column '{column}'")
        selected[column] = pd.to_numeric(normalized[source], errors="coerce")
    volume_source = rename_map.get("volume")
    if volume_source:
        selected["volume"] = pd.to_numeric(normalized[volume_source], errors="coerce").fillna(0)
    else:
        selected["volume"] = 0
    selected = selected.dropna(subset=["open", "high", "low", "close"])
    selected.index.name = "timestamp"
    return selected


def _write_ohlcv_export_files(
    df: pd.DataFrame,
    *,
    export_dir: str,
    split_by_day: bool,
    base_filename: str,
) -> list[dict]:
    os.makedirs(export_dir, exist_ok=True)
    files: list[dict] = []

    def write_one(path: str, frame: pd.DataFrame) -> None:
        out = frame.copy().reset_index()
        out["timestamp"] = pd.to_datetime(out["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        out.to_csv(path, index=False)
        files.append(
            {
                "name": os.path.basename(path),
                "path": path,
                "rows": int(len(frame)),
                "from": str(frame.index[0]),
                "to": str(frame.index[-1]),
            }
        )

    if split_by_day:
        for session_date, day_df in df.groupby(df.index.date):
            write_one(os.path.join(export_dir, f"{session_date.isoformat()}.csv"), day_df)
    else:
        write_one(os.path.join(export_dir, f"{base_filename}.csv"), df)

    return files


@app.post("/api/replay/export-ohlcv")
async def export_replay_ohlcv(payload: OhlcvExportPayload, request: Request):
    token = _get_session_token(request)
    session = await _validate_session_async(token)
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user, broker_client, source = await _request_broker_context(request)
    if not broker_client:
        raise HTTPException(status_code=400, detail=_broker_not_configured_message(user, source))

    try:
        from_dt = datetime.strptime(payload.from_date, "%Y-%m-%d")
        to_dt = datetime.strptime(payload.to_date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {exc}") from exc

    if from_dt > to_dt:
        raise HTTPException(status_code=400, detail="from_date cannot be after to_date")

    inst_info = INSTRUMENT_MAP.get(payload.instrument)
    if not inst_info:
        raise HTTPException(status_code=400, detail=f"Unknown instrument: {payload.instrument}")

    day_span = (to_dt - from_dt).days
    effective_interval = "D" if day_span > INTRADAY_MAX_DAYS else str(payload.candle_interval or "1")
    user_id = int(session.get("user_id") or _request_user_id(request) or 0)
    export_root = os.path.join(_user_exports_root(user_id), "ohlcv_sessions")
    os.makedirs(export_root, exist_ok=True)

    name_seed = (
        payload.export_name or f"{payload.instrument}_{payload.from_date}_{payload.to_date}_{effective_interval}"
    )
    export_name = _safe_export_name(name_seed, default="ohlcv_export")
    timestamp_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_dir = os.path.join(export_root, f"{export_name}_{timestamp_tag}")

    try:
        df_raw = await asyncio.to_thread(
            _fetch_data,
            instrument=payload.instrument,
            from_date=payload.from_date,
            to_date=payload.to_date,
            segment=payload.segment,
            candle_interval=str(payload.candle_interval or "1"),
            broker_client=broker_client,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Data fetch failed: {exc}") from exc

    if df_raw is None or df_raw.empty:
        raise HTTPException(status_code=404, detail="No OHLCV data returned for the requested range")

    try:
        export_df = _normalize_ohlcv_export_frame(df_raw)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    files = _write_ohlcv_export_files(
        export_df,
        export_dir=export_dir,
        split_by_day=payload.split_by_day,
        base_filename=f"{payload.instrument}_{payload.from_date}_to_{payload.to_date}_{effective_interval}",
    )

    manifest = {
        "instrument": payload.instrument,
        "instrument_name": inst_info["name"],
        "segment": payload.segment,
        "from_date": payload.from_date,
        "to_date": payload.to_date,
        "requested_interval": str(payload.candle_interval or "1"),
        "effective_interval": effective_interval,
        "split_by_day": payload.split_by_day,
        "rows": int(len(export_df)),
        "files": [{k: v for k, v in item.items() if k != "path"} for item in files],
    }
    manifest_path = os.path.join(export_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    return {
        "status": "ok",
        "instrument": payload.instrument,
        "instrument_name": inst_info["name"],
        "rows": int(len(export_df)),
        "split_by_day": payload.split_by_day,
        "effective_interval": effective_interval,
        "export_dir": export_dir,
        "manifest_path": manifest_path,
        "files": files,
    }


def _format_rolling_strike(offset_steps: int) -> str:
    if offset_steps == 0:
        return "ATM"
    sign = "+" if offset_steps > 0 else "-"
    return f"ATM{sign}{abs(offset_steps)}"


_OPTION_HISTORY_CACHE_DIR = os.getenv(
    "PHILFORGE_OPTION_HISTORY_CACHE_DIR",
    os.path.join(_HERE, "data", "option_history_cache"),
)
_OPTION_REAL_DATA_MAX_DAYS = 730


def _option_history_cache_path(history_key: str) -> str:
    digest = hashlib.sha256(history_key.encode("utf-8")).hexdigest()
    os.makedirs(_OPTION_HISTORY_CACHE_DIR, exist_ok=True)
    return os.path.join(_OPTION_HISTORY_CACHE_DIR, f"{digest}.csv")


def _load_option_history_cache(history_key: str) -> pd.DataFrame:
    path = _option_history_cache_path(history_key)
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if df.empty:
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        return df[~df.index.duplicated(keep="first")]
    except Exception as exc:
        print(f"[BACKTEST] ⚠️  Failed to read option cache {path}: {exc}")
        return pd.DataFrame()


def _save_option_history_cache(history_key: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    path = _option_history_cache_path(history_key)
    try:
        cache_df = df.sort_index()
        cache_df.to_csv(path, index_label="timestamp")
    except Exception as exc:
        print(f"[BACKTEST] ⚠️  Failed to write option cache {path}: {exc}")


def _resolve_rolling_strike_alias(leg: dict, strike_step: int, max_offset: int) -> tuple[str | None, str | None]:
    strike_type = str(leg.get("strike_type", "atm") or "atm").lower()
    strike_value = float(leg.get("strike_value", 0) or 0)
    option_type = str(leg.get("option_type", "CE") or "CE").upper()

    if strike_type == "atm":
        return "ATM", None

    if strike_type in ("otm", "itm"):
        offset_steps = round_half_up(abs(strike_value) / strike_step) if strike_step > 0 else 0
        if offset_steps == 0:
            return "ATM", None
        signed_steps = offset_steps if strike_type == "otm" else -offset_steps
        if option_type == "PE":
            signed_steps *= -1
        if abs(signed_steps) > max_offset:
            return None, f"rolling options support up to ATM±{max_offset}, requested {strike_type} {offset_steps}"
        return _format_rolling_strike(signed_steps), None

    if strike_type == "spot_price":
        offset_steps = round_half_up(strike_value / strike_step) if strike_step > 0 else 0
        if abs(offset_steps) > max_offset:
            return None, f"rolling options support up to ATM±{max_offset}, requested spot offset {offset_steps}"
        return _format_rolling_strike(offset_steps), None

    if strike_type == "strike_price":
        return None, "fixed strike backtests are not representable on Dhan rolling options API"
    if strike_type in ("premium_near", "premium_above", "premium_below"):
        return None, "premium-target strike selection is not representable on Dhan rolling options API"
    return None, f"unsupported strike type '{strike_type}' for historical option pricing"


def _resample_option_history(df: pd.DataFrame, timeframe_minutes: int) -> pd.DataFrame:
    if df is None or df.empty:
        return df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()

    agg_map = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in df.columns:
        agg_map["volume"] = "sum"
    for field in ("oi", "iv", "strike", "spot"):
        if field in df.columns:
            agg_map[field] = "last"

    return (
        df.sort_index()
        .resample(
            f"{timeframe_minutes}min",
            label="left",
            closed="left",
            origin="start_day",
            offset="15min",
        )
        .agg(agg_map)
        .dropna(subset=["open"])
    )


def _fetch_backtest_option_histories(strategy_config: dict, tf_spec, from_date: str, to_date: str) -> dict:
    legs = strategy_config.get("legs") or []
    option_legs = [leg for leg in legs if leg.get("option_type") in ("CE", "PE")]
    allow_synthetic = bool(strategy_config.get("allow_synthetic_option_fallback", False))
    pricing_info = {
        "historical_legs": 0,
        "synthetic_legs": len(option_legs),
        "allow_synthetic": allow_synthetic,
        "warnings": [],
        "errors": [],
    }
    if not option_legs:
        return pricing_info

    if tf_spec.requested <= 0:
        target = pricing_info["warnings"] if allow_synthetic else pricing_info["errors"]
        target.append("Invalid timeframe for option history fetch.")
        return pricing_info

    inst_info = INSTRUMENT_MAP.get(strategy_config.get("instrument", ""))
    if not inst_info:
        target = pricing_info["warnings"] if allow_synthetic else pricing_info["errors"]
        target.append("Unknown instrument for option history fetch.")
        return pricing_info

    if str(tf_spec.requested).upper() == "D":
        target = pricing_info["warnings"] if allow_synthetic else pricing_info["errors"]
        target.append("Daily option candles are not available on Dhan rolling options API.")
        return pricing_info

    option_exchange_segment = "BSE_FNO" if strategy_config.get("instrument") == "1" else "NSE_FNO"
    option_instrument_type = "OPTIDX" if inst_info["dhan_type"] == "INDEX" else "OPTSTK"
    strike_step = get_strike_step(strategy_config.get("instrument", "26000"))
    max_offset = 10 if option_instrument_type == "OPTIDX" else 3

    from_dt = datetime.strptime(from_date, "%Y-%m-%d")
    to_dt = datetime.strptime(to_date, "%Y-%m-%d")
    requested_days = max(1, (to_dt - from_dt).days + 1)
    history_cache = {}

    if requested_days >= _OPTION_REAL_DATA_MAX_DAYS:
        pricing_info["allow_synthetic"] = True
        pricing_info["warnings"].append(
            f"Date range is {requested_days} days (>= {_OPTION_REAL_DATA_MAX_DAYS}); using synthetic option pricing by rule."
        )
        strategy_config["_option_history"] = {}
        return pricing_info

    import time as _time

    for leg_index, leg in enumerate(legs):
        if leg.get("option_type") not in ("CE", "PE"):
            continue

        expiry_selection = str(leg.get("expiry") or "current_week")
        expiry_params = ROLLING_EXPIRY_SELECTIONS.get(expiry_selection)
        if not expiry_params:
            target = pricing_info["warnings"] if allow_synthetic else pricing_info["errors"]
            target.append(
                f"Leg {leg_index + 1}: expiry '{expiry_selection}' is not supported for rolling option history."
            )
            continue

        strike_alias, reason = _resolve_rolling_strike_alias(leg, strike_step, max_offset)
        if not strike_alias:
            target = pricing_info["warnings"] if allow_synthetic else pricing_info["errors"]
            target.append(f"Leg {leg_index + 1}: {reason}.")
            continue

        expiry_flag, expiry_code = expiry_params
        option_side = "CALL" if str(leg.get("option_type", "CE")).upper() == "CE" else "PUT"
        history_key = (
            f"{inst_info['dhan_id']}|{option_exchange_segment}|{option_instrument_type}|"
            f"{expiry_flag}|{expiry_code}|{strike_alias}|{option_side}|{tf_spec.fetch}"
        )

        if history_key not in history_cache:
            cached_raw = _load_option_history_cache(history_key)
            all_dfs = [cached_raw] if cached_raw is not None and not cached_raw.empty else []
            chunk_start = from_dt
            last_error = None
            cache_covers_range = (
                cached_raw is not None
                and not cached_raw.empty
                and cached_raw.index.min() <= from_dt
                and cached_raw.index.max() >= (to_dt + timedelta(hours=23, minutes=59))
            )
            if not cache_covers_range:
                while chunk_start <= to_dt:
                    chunk_end_exclusive = min(
                        chunk_start + timedelta(days=ROLLING_OPTION_CHUNK_DAYS), to_dt + timedelta(days=1)
                    )
                    try:
                        df_chunk = dhan.get_rolling_option_data(
                            security_id=inst_info["dhan_id"],
                            exchange_segment=option_exchange_segment,
                            instrument_type=option_instrument_type,
                            expiry_flag=expiry_flag,
                            expiry_code=expiry_code,
                            strike=strike_alias,
                            option_type=option_side,
                            from_date=chunk_start.strftime("%Y-%m-%d"),
                            to_date=chunk_end_exclusive.strftime("%Y-%m-%d"),
                            interval=str(tf_spec.fetch),
                        )
                        if df_chunk is not None and not df_chunk.empty:
                            all_dfs.append(df_chunk)
                    except Exception as exc:
                        last_error = str(exc)
                        break
                    _time.sleep(0.25)
                    chunk_start = chunk_end_exclusive

            if all_dfs:
                df_hist_raw = pd.concat(all_dfs).sort_index()
                df_hist_raw = df_hist_raw[~df_hist_raw.index.duplicated(keep="first")]
                _save_option_history_cache(history_key, df_hist_raw)
                coverage_end = to_dt + timedelta(days=1)
                df_hist_raw = df_hist_raw[(df_hist_raw.index >= from_dt) & (df_hist_raw.index < coverage_end)]
                df_hist_exec = df_hist_raw
                if tf_spec.derived:
                    df_hist_exec = _resample_option_history(df_hist_raw, tf_spec.requested)
                history_cache[history_key] = {
                    "raw": df_hist_raw,
                    "execution": df_hist_exec,
                }
            else:
                history_cache[history_key] = pd.DataFrame()
                warning = (
                    f"Leg {leg_index + 1}: no rolling option data returned for {strike_alias} {option_side} "
                    f"({expiry_selection})."
                )
                if last_error:
                    warning += f" Last error: {last_error}"
                target = pricing_info["warnings"] if allow_synthetic else pricing_info["errors"]
                target.append(warning)

        df_hist = history_cache.get(history_key)
        if isinstance(df_hist, dict):
            df_hist = df_hist.get("execution")
        if df_hist is None or df_hist.empty:
            continue

        leg["_bt_option_history_key"] = history_key
        leg["_bt_option_pricing"] = "historical"
        leg["_bt_option_history_label"] = strike_alias
        pricing_info["historical_legs"] += 1

    pricing_info["synthetic_legs"] = max(0, len(option_legs) - pricing_info["historical_legs"])
    strategy_config["_option_history"] = history_cache
    return pricing_info


# ── Manual NIFTY cascade backtest ─────────────────────────────────
_CASCADE_RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nse_contract_rules.json")


def _cascade_contract_calendar() -> ContractCalendar:
    """Dated NSE contract rules, overridable without a code change."""
    if os.path.exists(_CASCADE_RULES_FILE):
        return ContractCalendar.from_json(_CASCADE_RULES_FILE)
    return ContractCalendar()


def _cascade_weekly_expiries(from_day: date, to_day: date, session_days: set[date]) -> list[date]:
    """Build NIFTY's weekly expiry calendar from actual trading sessions.

    The expiry weekday is exchange policy and it has moved: a replay spanning a
    change cannot assume today's weekday throughout.  It comes from a dated
    rule table, while the holiday shift (expiry moves to the preceding session)
    comes from the trading days Dhan's own candles show.

    A range starting before the earliest known rule raises rather than
    backdating current rules, because a silently wrong expiry weekday
    reprices every trade in the replay instead of failing.
    """

    return _cascade_contract_calendar().weekly_expiries(from_day, to_day, session_days)


def _run_cascade_feasibility(
    broker_client: DhanClient,
    config: CascadeConfig,
    from_date: str,
    to_date: str,
) -> dict:
    """Replay Dhan's index signal path and price exact contracts from cache.

    Dhan supplies only NIFTY index candles.  Every option price is looked up
    against the fixed strike/expiry selected by the replay in the existing
    Upstox expired-options cache.  This intentionally runs offline: a cache
    miss is a recorded data gap, never a network fetch or synthetic price.
    """

    from data.cascade_dhan import DhanOneHourSource
    from data.cascade_upstox import UpstoxPremiumSource

    source = DhanOneHourSource(broker_client)
    index_candles = source.fetch_index_cascade(from_date, to_date, config.stage_timeframes)
    base_candles = index_candles.get(config.timeframe, [])
    if not base_candles:
        raise ValueError(f"Dhan returned no NIFTY {config.timeframe} candles for the selected date range.")
    resolver = NiftyContractResolver(
        _cascade_weekly_expiries(
            config.mother_timestamp.date(),
            datetime.strptime(to_date, "%Y-%m-%d").date(),
            {candle.timestamp.date() for candle in base_candles},
        ),
        lot_size=config.lot_size,
        strike_step=config.strike_step,
    )

    def replay_with(premium_source: UpstoxPremiumSource):
        return OneHourCascade(config, resolver, premium_source.lookup).run(index_candles)

    # First pass is cache-only, so a fully-covered backtest stays offline even
    # when an Upstox token happens to be present on the server.
    premium_source = UpstoxPremiumSource(cache_only=True)
    result = replay_with(premium_source)
    backfill = {
        "attempted": False,
        "status": "cache_hit" if result.fully_priced else "cache_gap",
        "network_requests": 0,
        "initial_gaps": list(result.data_gaps),
        "detail": "All required historical option candles were already cached."
        if result.fully_priced
        else "One or more exact historical option candles are absent from the local cache.",
    }
    if not result.fully_priced:
        backfill["attempted"] = True
        try:
            # Upstox access tokens are daily. Refresh when the server has the
            # configured headless credentials; otherwise use a still-valid token
            # as-is and let the premium source report its own access result.
            try:
                from upstox_token_manager import ensure_fresh_token

                ensure_fresh_token()
            except Exception as exc:
                _logger.warning("[cascade-backtest] Upstox token pre-check skipped: %s", exc)
            premium_source = UpstoxPremiumSource(backfill_missing=True)
            result = replay_with(premium_source)
            backfill.update(
                {
                    "status": "backfilled" if result.fully_priced else "still_incomplete",
                    "network_requests": premium_source.requests_made,
                    "detail": (
                        "Missing Upstox history was refreshed and the replay was priced in full."
                        if result.fully_priced
                        else "Upstox was checked, but one or more exact contract candles are still unavailable."
                    ),
                }
            )
        except Exception as exc:
            backfill.update(
                {
                    "status": "unavailable",
                    "detail": f"Automatic Upstox backfill could not run: {exc}",
                }
            )
    fully_priced = result.fully_priced
    data_gaps = list(result.data_gaps)
    pricing_warning = (
        (
            "Missing Upstox history was backfilled, then exact fixed-strike net P&L was calculated. "
            "No live order was sent."
            if backfill["attempted"]
            else "Exact fixed-strike premiums and net P&L are calculated from the local Upstox cache. "
            "No live order or Upstox network request was made."
        )
        if fully_priced
        else (f"{backfill['detail']} The NIFTY signal is shown, but P&L is withheld rather than estimated.")
    )

    return {
        "status": "ok",
        "pricing_mode": "contract_exact_upstox_cache" if fully_priced else "contract_partial_upstox_cache",
        "pricing_warning": pricing_warning,
        "expiry_calendar_warning": (
            "Expiry selection follows Tuesday weekly expiry and shifts a market-holiday Tuesday to the "
            "previous Dhan-confirmed NIFTY trading session. Each selected contract is then priced separately."
        ),
        "data": {
            "provider": "Dhan index candles + cached/backfilled Upstox option candles",
            "source": "Dhan NIFTY index candles; exact fixed-strike Upstox cache with targeted backfill",
            "index_candles": {timeframe: len(rows) for timeframe, rows in index_candles.items()},
            "option_candles": len(result.entries) + len(result.exit_option_prices),
            "upstox_cache_only": not backfill["attempted"],
            "upstox_network_requests": premium_source.requests_made,
            "missing_contracts": premium_source.missing_contracts,
            "missing_minutes": premium_source.missing_minutes,
            "upstox_backfill": backfill,
            "from_date": from_date,
            "to_date": to_date,
            "stage_timeframes": list(config.stage_timeframes),
        },
        "result": {
            "state": result.status,
            "target_index": result.target_index,
            "average_spot": result.average_spot,
            "exit_timestamp": result.exit_timestamp.isoformat() if result.exit_timestamp else None,
            "exit_reason": result.exit_reason,
            "fully_priced": fully_priced,
            "realized_option_pnl": result.realized_pnl if fully_priced else None,
            "net_option_pnl": result.net_pnl if fully_priced else None,
            "option_costs": result.costs_total if fully_priced else None,
            "data_gap": result.data_gap,
            "data_gaps": data_gaps,
            "entries": [
                {
                    "stage": entry.stage,
                    "timestamp": entry.timestamp.isoformat(),
                    "spot": entry.spot,
                    "lots": entry.lots,
                    "quantity": entry.quantity,
                    "entry_option_price": entry.option_price,
                    "strike": entry.contract.strike,
                    "expiry": entry.contract.expiry.isoformat(),
                    "option_type": entry.contract.option_type,
                }
                for entry in result.entries
            ],
            "events": result.events,
        },
    }


@app.post("/api/cascade/backtest")
async def api_run_cascade_backtest(payload: CascadeBacktestPayload, request: Request):
    """Run one manually supplied CE or PE 1H mother-candle replay.

    This endpoint performs no order placement and does not create a live engine.

    NOTE (2026-07-30): the Signal Ladder tab that called this was retired from
    the Cascade page on Phil's decision — history replays live on the Test
    Bench.  Nothing in the UI reaches this route anymore; it stays only until
    its unique replay mode (the 1+2+3 stage ladder) is ported to the bench,
    after which it and `_run_cascade_feasibility` can go.
    """

    try:
        mother_timestamp = datetime.fromisoformat(payload.mother_timestamp.strip()).replace(tzinfo=None)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="Mother timestamp must be an IST datetime (YYYY-MM-DDTHH:MM)."
        ) from exc
    side = payload.option_type.upper().strip()
    if side not in {"CE", "PE"}:
        raise HTTPException(status_code=400, detail="Option type must be CE or PE.")
    if payload.mother_high <= payload.mother_low:
        raise HTTPException(status_code=400, detail="Mother high must be greater than mother low.")
    timeframe = payload.timeframe.lower().strip()
    valid_timeframe_minutes = {"5m": 5, "15m": 15, "1h": 60}
    if timeframe not in valid_timeframe_minutes:
        raise HTTPException(status_code=400, detail="Mother timeframe must be 5m, 15m, or 1h.")
    # NSE hourly index bars are session-aligned at :15, unlike 5m/15m bars.
    # Check the hourly offset separately; testing ``minute % 60`` here would
    # incorrectly reject the only valid 1H open (:15) before the specific
    # validation below is reached.
    if (
        mother_timestamp.second
        or mother_timestamp.microsecond
        or (timeframe != "1h" and mother_timestamp.minute % valid_timeframe_minutes[timeframe])
    ):
        raise HTTPException(status_code=400, detail=f"Mother timestamp must align to a {timeframe} candle open in IST.")
    if timeframe == "1h" and mother_timestamp.minute != 15:
        raise HTTPException(status_code=400, detail="A NIFTY 1H mother candle must open at :15 IST.")

    latest_day = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    try:
        end_day = datetime.strptime(payload.to_date, "%Y-%m-%d").date() if payload.to_date else latest_day
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="End date must use YYYY-MM-DD.") from exc
    if end_day < mother_timestamp.date():
        raise HTTPException(status_code=400, detail="End date cannot be before the mother candle.")
    if (end_day - mother_timestamp.date()).days > 370:
        raise HTTPException(status_code=400, detail="The initial cascade replay is limited to 370 calendar days.")

    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(status_code=400, detail="Connect a Dhan account before running a cascade replay.")
    config = CascadeConfig(
        mother_timestamp=mother_timestamp,
        mother_high=payload.mother_high,
        mother_low=payload.mother_low,
        option_type=side,
        timeframe=timeframe,
        # Dhan rolling data cannot provide an exact premium for every fixed
        # contract/timeframe. Signal execution remains valid; premium P&L is
        # intentionally omitted whenever a matching candle is unavailable.
        strict_option_data=False,
    )
    try:
        return await asyncio.to_thread(
            _run_cascade_feasibility,
            broker_client,
            config,
            mother_timestamp.date().isoformat(),
            end_day.isoformat(),
        )
    except Exception as exc:
        from data.cascade_dhan import DhanDataAccessError

        if isinstance(exc, DhanDataAccessError):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _logger.exception("Cascade backtest failed")
        raise HTTPException(status_code=500, detail="Cascade backtest failed. Check Dhan access and retry.") from exc


# ── NIFTY Options Cascade: current-session paper campaign ─────────
def _cascade_live_gate_status() -> dict:
    """Expose the Phase-4 boundary without providing a hidden live path."""

    if not _CASCADE_LIVE_FLAG:
        return {
            "enabled": False,
            "armed": False,
            "reason": (
                "Live Cascade is server-locked. Paper validation, explicit account confirmation, "
                "one-lot approval and an expiry-day square-off proof are required before it can be armed."
            ),
        }
    return {
        "enabled": True,
        "armed": False,
        "reason": (
            "Server flag is present, but live arming remains blocked until the Dhan partial-fill/reconciliation "
            "adapter is implemented and explicitly approved."
        ),
    }


def _parse_cascade_mother_timestamp(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="Mother timestamp must be an IST datetime (YYYY-MM-DDTHH:MM)."
        ) from exc
    return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed.astimezone(IST)


def _historical_candle_entry_contract(
    mother: IndexCandle, candles: list[IndexCandle], ce_offset_steps: int
) -> FixedCampaignOption:
    """Resolve the contract that was valid at a historical mother candle.

    Today's ScripMaster cannot be used as an expiry calendar for a past trade:
    it drops expired weekly contracts.  The index sessions returned by Dhan let
    us derive the correct Tuesday/holiday-shifted next-weekly expiry instead.
    The empty security id is deliberate—historical replay never requests an
    LTP or submits even a paper broker order for that expired contract.
    """

    session_days = {row.timestamp.date() for row in candles}
    end_day = max(session_days) if session_days else mother.timestamp.date()
    expiries = _cascade_weekly_expiries(mother.timestamp.date(), end_day, session_days)
    expiry = next(
        (value for value in expiries if 6 <= (value - mother.timestamp.date()).days <= 13),
        None,
    )
    if expiry is None:
        raise HTTPException(
            status_code=422,
            detail="Dhan did not return enough NIFTY sessions to resolve this mother candle's next-weekly expiry.",
        )
    atm = int(float(mother.close) / 50.0 + 0.5) * 50
    strike = atm + int(ce_offset_steps) * 50
    return FixedCampaignOption("NIFTY", strike, expiry, "CE", 65, "")


def _cascade_gap_adjusted_candles(
    candles: list[IndexCandle], mother_timestamp: Optional[datetime] = None
) -> list[dict]:
    """Return display candles with overnight gaps joined to the prior close.

    This is intentionally a chart-only transform.  IndexCandle values are not
    changed and the paper engine always consumes the real exchange OHLC.
    """

    rows: list[dict] = []
    prior: Optional[IndexCandle] = None
    selected = (
        mother_timestamp.replace(tzinfo=IST)
        if mother_timestamp and mother_timestamp.tzinfo is None
        else mother_timestamp.astimezone(IST)
        if mother_timestamp
        else None
    )
    for candle in sorted(candles, key=lambda item: item.timestamp):
        display_open = candle.open
        display_high = candle.high
        display_low = candle.low
        gap_direction: Optional[str] = None
        if prior is not None and candle.timestamp.date() != prior.timestamp.date():
            if candle.open > prior.close:
                gap_direction = "up"
            elif candle.open < prior.close:
                gap_direction = "down"
            if gap_direction:
                display_open = prior.close
                display_high = max(candle.high, display_open)
                display_low = min(candle.low, display_open)
        rows.append(
            {
                "t": candle.timestamp.isoformat(),
                "o": display_open,
                "h": display_high,
                "l": display_low,
                "c": candle.close,
                "native_open": candle.open,
                "native_high": candle.high,
                "native_low": candle.low,
                "native_close": candle.close,
                "gap_direction": gap_direction,
                "is_mother": bool(selected and candle.timestamp == selected),
            }
        )
        prior = candle
    return rows


def _cascade_native_candles(candles: list[IndexCandle], mother_timestamp: Optional[datetime] = None) -> list[dict]:
    selected = (
        mother_timestamp.replace(tzinfo=IST)
        if mother_timestamp and mother_timestamp.tzinfo is None
        else mother_timestamp.astimezone(IST)
        if mother_timestamp
        else None
    )
    return [
        {
            "t": candle.timestamp.isoformat(),
            "o": candle.open,
            "h": candle.high,
            "l": candle.low,
            "c": candle.close,
            "native_open": candle.open,
            "native_high": candle.high,
            "native_low": candle.low,
            "native_close": candle.close,
            "gap_direction": None,
            "is_mother": bool(selected and candle.timestamp == selected),
        }
        for candle in sorted(candles, key=lambda item: item.timestamp)
    ]


async def _load_cascade_mother_candle(adapter: CascadeOptionsAdapter, mother_timestamp: datetime) -> IndexCandle:
    candles = await adapter.async_get_candles(
        "NIFTY", "5m", from_date=mother_timestamp.date(), to_date=mother_timestamp.date()
    )
    for candle in candles:
        if candle.timestamp == mother_timestamp:
            return candle
    raise HTTPException(
        status_code=404,
        detail="No closed NIFTY 5m candle exists at that IST timestamp. Choose a completed market-session candle.",
    )


@app.get("/api/cascade/paper/status")
async def cascade_paper_status(request: Request):
    user_id = _request_user_id(request)
    # Status is deliberately side-effect free. Recovery happens only during
    # active-worker startup/handover or an explicit mutating action.
    runtime = _cascade_engines.get(user_id)
    if runtime is None:
        return {"status": "not_started", "mode": "paper", "live_gate": _cascade_live_gate_status()}
    return {
        "status": "ok",
        "mode": "paper",
        "live_gate": _cascade_live_gate_status(),
        "campaign": {**runtime.engine.get_status(), "running": runtime.running},
    }


@app.get("/api/cascade/paper/chart")
async def cascade_paper_chart(mother_timestamp: str, request: Request):
    """Serve the current paper window as a chart-safe, gap-adjusted display."""

    mother = _parse_cascade_mother_timestamp(mother_timestamp)
    now = datetime.now(IST)
    if mother.date() > now.date() or (now.date() - mother.date()).days > 14:
        raise HTTPException(status_code=400, detail="Chart is available for mothers in the last 14 calendar days.")
    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(status_code=400, detail="Connect a Dhan account to load the NIFTY chart.")
    adapter = CascadeOptionsAdapter(broker_client, paper_only=True)
    try:
        candles = await adapter.async_get_candles("NIFTY", "5m", from_date=mother.date(), to_date=now.date())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Unable to load NIFTY 5m candles: {exc}") from exc
    rows = _cascade_gap_adjusted_candles(candles, mother)
    mother_row = next((row for row in rows if row["is_mother"]), None)
    if mother_row is None:
        raise HTTPException(status_code=404, detail="The selected mother candle was not returned by Dhan.")
    return {
        "status": "ok",
        "timeframe": "5m",
        "chart_mode": "visual_gap_adjusted",
        "candles": rows,
        "mother": mother_row,
        "note": "Gap adjustment is visual only; paper geometry uses native Dhan OHLC.",
    }


async def _run_candle_entry_paper_loop(user_id: int, runtime: _CascadeRuntime) -> None:
    """Poll closed NIFTY bars on every ladder chart for the Candle Entry campaign.

    The engine dedupes what it has already seen and interleaves the charts by
    close time itself; this loop only keeps fresh candles flowing in.
    """

    while runtime.running and _candle_entry_engines.get(int(user_id)) is runtime:
        try:
            today = datetime.now(IST).date()
            start = runtime.engine.mother.timestamp.date()
            batches = {}
            for timeframe in runtime.engine.stages:
                batches[timeframe] = await runtime.adapter.async_get_candles(
                    "NIFTY", timeframe, from_date=start, to_date=today
                )
            runtime.engine.ingest(batches)
            if runtime.engine.status in {"CLOSED", "EXPIRED", "KILLED"}:
                runtime.running = False
            await _save_candle_entry_open_state(user_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.warning("[CANDLE ENTRY] NIFTY ladder paper poll failed for user %s: %s", user_id, exc)
        await asyncio.sleep(20)


@app.get("/api/candle-entry/paper/status")
async def candle_entry_paper_status(request: Request):
    runtime = _candle_entry_engines.get(_request_user_id(request))
    if runtime is None:
        return {"status": "not_started", "mode": "paper"}
    return {"status": "ok", "mode": "paper", "campaign": {**runtime.engine.get_status(), "running": runtime.running}}


@app.post("/api/candle-entry/paper/start")
async def candle_entry_paper_start(payload: CandleEntryPaperStartPayload, request: Request):
    timeframe = str(payload.timeframe or "1h").strip().lower()
    if timeframe not in LADDER_TIMEFRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Timeframe must be one of {', '.join(LADDER_TIMEFRAMES)}.",
        )
    bar_minutes = TIMEFRAME_MINUTES[timeframe]
    mother_timestamp = _parse_cascade_mother_timestamp(payload.mother_timestamp)
    now = datetime.now(IST)
    if mother_timestamp.date() > now.date():
        raise HTTPException(status_code=400, detail="Mother timestamp cannot be in the future (IST).")
    if (now.date() - mother_timestamp.date()).days > _CANDLE_ENTRY_HISTORY_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Choose a completed mother candle from the last {_CANDLE_ENTRY_HISTORY_DAYS} calendar days.",
        )
    minutes_since_open = (mother_timestamp.hour * 60 + mother_timestamp.minute) - (9 * 60 + 15)
    if minutes_since_open < 0 or mother_timestamp.time() >= dt_time(15, 30) or minutes_since_open % bar_minutes != 0:
        raise HTTPException(
            status_code=400,
            detail=f"Mother timestamp must be an NSE-aligned {timeframe} candle open (on the 09:15 IST grid).",
        )
    # The last bar of the day closes at 15:30 whatever chart it is read on.
    mother_close_at = min(
        mother_timestamp + timedelta(minutes=bar_minutes),
        mother_timestamp.replace(hour=15, minute=30, second=0, microsecond=0),
    )
    if mother_close_at > now:
        raise HTTPException(status_code=400, detail=f"Mother timestamp must be a completed {timeframe} candle.")
    user_id = _request_user_id(request)
    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(status_code=400, detail="Connect a Dhan account before starting the Candle Entry campaign.")
    old = await _restore_candle_entry_open_state(user_id, broker_client, activate=True)
    if old is not None and old.running:
        raise HTTPException(
            status_code=409,
            detail="A Candle Entry campaign is already running. Kill it before replacing its mother.",
        )
    adapter = CascadeOptionsAdapter(broker_client, paper_only=True)
    stages = tuple(LADDER_TIMEFRAMES[LADDER_TIMEFRAMES.index(timeframe) :])
    batches: dict[str, list[IndexCandle]] = {}
    for stage_timeframe in stages:
        batches[stage_timeframe] = await adapter.async_get_candles(
            "NIFTY", stage_timeframe, from_date=mother_timestamp.date(), to_date=now.date()
        )
    candles = batches[timeframe]
    mother = next((row for row in candles if row.timestamp == mother_timestamp), None)
    if mother is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dhan did not return a closed NIFTY {timeframe} candle at that timestamp.",
        )
    is_historical_replay = mother_timestamp.date() != now.date()
    try:
        contract = (
            _historical_candle_entry_contract(mother, candles, payload.ce_offset_steps)
            if is_historical_replay
            else await asyncio.to_thread(
                adapter.select_campaign_contract,
                mother_spot=mother.close,
                selected_at=mother.timestamp,
                ce_offset_steps=payload.ce_offset_steps,
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Unable to select fixed next-weekly CE: {exc}") from exc
    engine = LadderCandleEntryPaper(
        mother,
        timeframe,
        contract,
        adapter,
        _cascade_premium_lookup(broker_client),
        signal_only=is_historical_replay,
    )
    if is_historical_replay:
        # A replay must not run past the option's own life.
        replay = {
            stage_timeframe: [row for row in rows if row.timestamp.date() <= contract.expiry]
            for stage_timeframe, rows in batches.items()
        }
        engine.ingest(replay)
        remaining = [rows[-1] for rows in replay.values() if rows]
        final_candle = max(remaining, key=lambda row: row.timestamp) if remaining else mother
        engine.finish_replay(final_candle, reached_expiry=now.date() >= contract.expiry)
    runtime = _CascadeRuntime(
        engine=engine,
        adapter=adapter,
        broker=broker_client,
        last_candle_timestamp=mother.timestamp,
        running=not is_historical_replay,
    )
    _candle_entry_engines[user_id] = runtime
    if runtime.running:
        runtime.task = asyncio.create_task(_run_candle_entry_paper_loop(user_id, runtime))
    await _save_candle_entry_open_state(user_id, force=True)
    return {
        "status": "replayed" if is_historical_replay else "started",
        "mode": "paper",
        "campaign": {**engine.get_status(), "running": runtime.running},
    }


@app.post("/api/candle-entry/paper/kill")
async def candle_entry_paper_kill(request: Request):
    runtime = _candle_entry_engines.get(_request_user_id(request))
    if runtime is None:
        raise HTTPException(status_code=404, detail="No Candle Entry campaign is active.")
    now = datetime.now(IST)
    try:
        quote = await asyncio.to_thread(runtime.adapter.get_ticker, "NIFTY")
        price = float(quote["last_price"])
    except Exception:
        price = float(runtime.engine.last_index_close)
    if not runtime.engine.kill_and_close(IndexCandle(now, price, price, price, price)):
        raise HTTPException(
            status_code=409, detail="Current option quote unavailable; open paper basket remains monitored."
        )
    runtime.running = False
    if runtime.task and not runtime.task.done():
        runtime.task.cancel()
    await _save_candle_entry_open_state(_request_user_id(request), force=True)
    return {"status": "killed", "mode": "paper", "campaign": {**runtime.engine.get_status(), "running": False}}


# ── Fib-boundary paper strategy (manual mother, CE/PE, one target then done) ──

_FIB_BOUNDARY_HISTORY_DAYS = 15
_FIB_TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
_FIB_TIMEFRAME_POLL_SEC = {"1m": 10, "5m": 15, "15m": 30, "1h": 60}


def _historical_fib_contract(
    mother: IndexCandle, candles: list[IndexCandle], side: str, itm_steps: int
) -> FixedCampaignOption:
    """Resolve the contract valid at a historical mother, without today's ScripMaster.

    Same reasoning as :func:`_historical_candle_entry_contract`: the live
    ScripMaster drops expired weeklies, so the next-weekly expiry is derived from
    the NIFTY sessions Dhan returned.  The empty security id is deliberate --
    signal-only replay never requests an LTP or places even a paper order.
    """

    session_days = {row.timestamp.date() for row in candles}
    end_day = max(session_days) if session_days else mother.timestamp.date()
    expiries = _cascade_weekly_expiries(mother.timestamp.date(), end_day, session_days)
    expiry = next((value for value in expiries if 6 <= (value - mother.timestamp.date()).days <= 13), None)
    if expiry is None:
        raise HTTPException(
            status_code=422,
            detail="Dhan did not return enough NIFTY sessions to resolve this mother candle's next-weekly expiry.",
        )
    atm = int(float(mother.close) / 50.0 + 0.5) * 50
    # ATM-N toward ITM: a CE drops strikes, a PE raises them.
    strike = atm + (-int(itm_steps) if side == "CE" else int(itm_steps)) * 50
    return FixedCampaignOption("NIFTY", strike, expiry, side, 65, "")


def _fib_touch_expiry_source(broker: DhanClient, symbol: str):
    """The expiry chain as the scrip master lists it for this symbol.

    Asked rather than tabulated, because which expiries exist is exactly what
    changes: NSE withdrew the BANKNIFTY / FINNIFTY / MIDCPNIFTY weeklies, and a
    hard-coded rhythm would have kept selecting contracts that stopped existing.
    """

    def source(on: date) -> list[date]:
        rows = ScripMaster.get_expiries(symbol) or []
        return [date.fromisoformat(str(value)[:10]) for value in rows]

    return source


# A quote is "current" for this long. Past it, the LTP describes a different
# minute than the bar being priced, and using it would be a fabrication.
_FIB_TOUCH_LIVE_QUOTE_SECONDS = 7 * 60


def _fib_touch_premium_lookup(broker: DhanClient, symbol: str, history=None):
    """Price a fill by the AGE of its bar, never by what is convenient.

    A minute recent enough to still have a live quote gets the LTP. Anything
    older is priced from RECORDED history -- Upstox for a contract that has
    expired, Dhan's own option candles for one still listed -- which is what
    lets a paper campaign start on a mother from an earlier day at all.

    ``history`` is that hybrid lookup, built per symbol by the caller because
    constructing it blocks. Without one, an old bar simply has no price and the
    engine records a gap; it never falls back to today's quote.
    """

    def lookup(when: datetime, strike: float, expiry: date, side: str) -> float | None:
        now = datetime.now(IST)
        stamp = when.replace(tzinfo=IST) if when.tzinfo is None else when.astimezone(IST)
        if abs((now - stamp).total_seconds()) <= _FIB_TOUCH_LIVE_QUOTE_SECONDS:
            try:
                value = broker.get_option_ltp(symbol, strike, expiry.isoformat(), side)
                if float(value or 0) > 0:
                    return float(value)
            except Exception:
                pass
            # A live quote that failed still must not fall through to history
            # for a bar this recent -- history has not recorded it yet.
            return None
        if history is None:
            return None
        try:
            contract = SimpleNamespace(
                symbol=symbol,
                underlying=symbol,
                strike=float(strike),
                expiry=expiry,
                option_type=str(side).upper(),
            )
            value = history(stamp, contract)
            return float(value) if value is not None and float(value) > 0 else None
        except Exception as exc:
            _logger.warning("[FIB TOUCH] %s history lookup failed at %s: %s", symbol, stamp, exc)
            return None

    return lookup


async def _run_fib_boundary_paper_loop(user_id: int, runtime: _CascadeRuntime) -> None:
    """Poll closed 1m index bars for the campaign's symbol; paper-only throughout."""

    engine = runtime.engine
    symbol = engine.config.symbol
    timeframe = engine.config.timeframe
    # Touches are watched on 1m however slow the mother's chart is, so the poll
    # runs at the ENTRY cadence, not the geometry one.
    poll = _FIB_TIMEFRAME_POLL_SEC.get(engine.config.entry_timeframe, 15)
    while runtime.running and _fib_boundary_engines.get(int(user_id), {}).get(symbol) is runtime:
        try:
            today = datetime.now(IST).date()
            start = engine.config.mother_timestamp.date()
            # The slow stream only matters until the swing is frozen; after that
            # it is settled geometry and re-fetching it every tick is waste.
            if timeframe != "1m" and engine.anchor is None:
                for row in await runtime.adapter.async_get_candles(symbol, timeframe, from_date=start, to_date=today):
                    engine.on_geometry_candle(row)
            candles = await runtime.adapter.async_get_candles(symbol, "1m", from_date=start, to_date=today)
            for candle in candles:
                if candle.timestamp <= runtime.last_candle_timestamp:
                    continue
                runtime.last_candle_timestamp = candle.timestamp
                engine.on_candle(candle)
            if engine.status in {"CLOSED", "EXPIRED"}:
                runtime.running = False
            await _save_fib_boundary_open_state(user_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _logger.warning("[FIB TOUCH] %s %s paper poll failed for user %s: %s", symbol, timeframe, user_id, exc)
        await asyncio.sleep(poll)


@app.get("/api/fib-boundary/symbols")
async def fib_touch_symbols(_request: Request):
    """What the ladder can be run on, and what is honestly true of each.

    The console reads `backtestable` and `has_weeklies` off this rather than
    assuming, so a symbol with no premium history says so in the form instead
    of returning a backtest full of zeros.
    """
    return {
        "levels": list(HALVING_LEVELS),
        "live_available": _FIB_TOUCH_LIVE_EXECUTION_ENABLED,
        "symbols": [
            {
                "symbol": terms.symbol,
                "label": terms.label,
                "lot_size": terms.lot_size,
                "strike_step": terms.strike_step,
                "has_weeklies": terms.has_weeklies,
                "backtestable": terms.backtestable,
                "note": terms.note,
            }
            for terms in _FIB_TOUCH_SYMBOLS.values()
        ],
    }


@app.get("/api/fib-boundary/paper/status")
async def fib_boundary_paper_status(request: Request):
    """Every ladder this user has, one entry per symbol, ordered by symbol."""
    runtimes = _fib_boundary_engines.get(_request_user_id(request), {})
    campaigns = [
        {**runtime.engine.get_status(), "running": runtime.running} for _symbol, runtime in sorted(runtimes.items())
    ]
    modes = {str(campaign.get("mode") or "paper").lower() for campaign in campaigns}
    board_mode = modes.pop() if len(modes) == 1 else ("mixed" if modes else "paper")
    return {
        "status": "ok" if campaigns else "not_started",
        "mode": board_mode,
        "live_available": _FIB_TOUCH_LIVE_EXECUTION_ENABLED,
        "campaigns": campaigns,
    }


@app.post("/api/fib-boundary/paper/start")
async def fib_boundary_paper_start(payload: FibTouchStartPayload, request: Request):
    """Start a swing-anchored paper ladder; fail closed for unavailable live mode.

    The mother candle only names where to look; the ladder's anchors are the
    first involvement on each side of it, found by the engine.  Everything from
    the levels to the lot count to the expiry follows Phil's locked spec --
    see engine/fib_touch_ladder.py for why each number is what it is.
    """

    try:
        terms = symbol_terms(payload.symbol)
    except FibTouchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    side = str(payload.side).upper()
    if side not in {"CE", "PE"}:
        raise HTTPException(status_code=400, detail="side must be CE or PE.")
    timeframe = str(payload.timeframe).lower()
    if timeframe not in _FIB_TOUCH_GEOMETRY_TF:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {', '.join(_FIB_TOUCH_GEOMETRY_TF)}.")
    mode = str(payload.mode).lower()
    if mode not in {"paper", "live"}:
        raise HTTPException(status_code=400, detail="mode must be paper or live.")
    if mode == "live" and not _FIB_TOUCH_LIVE_EXECUTION_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "Fib Boundary live execution is temporarily disabled until Dhan fill verification, "
                "partial-fill handling and restart reconciliation are complete. Use Paper or Backtest."
            ),
        )

    mother_timestamp = _parse_cascade_mother_timestamp(payload.mother_timestamp)
    now = datetime.now(IST)
    if mother_timestamp.second or mother_timestamp.microsecond:
        raise HTTPException(status_code=400, detail=f"Mother timestamp must be a {timeframe} candle open in IST.")
    if not (dt_time(9, 15) <= mother_timestamp.time() <= dt_time(15, 30)):
        raise HTTPException(status_code=400, detail="Mother candle must be within the 09:15-15:30 session.")
    tf_minutes = _FIB_TOUCH_TF_MINUTES[timeframe]
    # NSE 1H bars open at :15 only; the rest open on a multiple of their size.
    if timeframe == "1h":
        if mother_timestamp.minute != 15:
            raise HTTPException(status_code=400, detail="A 1H mother opens at 09:15, 10:15 ... 15:15 IST.")
    elif mother_timestamp.minute % tf_minutes:
        raise HTTPException(
            status_code=400, detail=f"Mother timestamp must be an NSE-aligned {timeframe} candle open in IST."
        )
    # The last 1H bar of the day is a 15-minute stub, so it completes at 15:30.
    effective = 15 if (timeframe == "1h" and mother_timestamp.hour == 15) else tf_minutes
    if mother_timestamp + timedelta(minutes=effective) > now:
        raise HTTPException(status_code=400, detail=f"Mother timestamp must be a completed {timeframe} candle.")
    # A past mother is allowed. What must never happen is pairing a past minute
    # with today's LTP -- so the premium lookup below routes by the BAR'S AGE:
    # a live quote only for a minute recent enough to have one, real recorded
    # history for everything older. How far back is not a number invented here;
    # it is however far Dhan still serves candles, and the fetch below says so
    # plainly when it runs out.

    user_id = _request_user_id(request)
    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(status_code=400, detail="Connect a Dhan account before starting a ladder.")
    # One ladder per symbol: a NIFTY ladder no longer blocks a BANKNIFTY start.
    # The same instrument twice is still refused -- two mothers on one symbol
    # would compete for the same cap and the same strikes.
    existing = _fib_boundary_engines.get(user_id, {}).get(terms.symbol)
    if existing is not None and existing.running:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A {terms.symbol} {existing.engine.side} ladder is already running on this "
                "instrument. Kill it to start a new mother."
            ),
        )

    adapter = CascadeOptionsAdapter(broker_client, paper_only=True)
    # Two streams: the mother's own chart carries the geometry, 1m carries the
    # touches. When the mother IS a 1m candle they are the same fetch.
    geometry_candles = await adapter.async_get_candles(
        terms.symbol, timeframe, from_date=mother_timestamp.date(), to_date=now.date()
    )
    if not any(row.timestamp == mother_timestamp for row in geometry_candles):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Dhan has no {terms.symbol} {timeframe} candle opening at "
                f"{mother_timestamp.strftime('%d %b %Y %H:%M')} IST. Check the date, the time and the timeframe."
            ),
        )
    candles = (
        geometry_candles
        if timeframe == "1m"
        else await adapter.async_get_candles(terms.symbol, "1m", from_date=mother_timestamp.date(), to_date=now.date())
    )

    # Lot size is asked of the scrip master per expiry, because it changes on
    # effective dates; the registry value is only the fallback.
    lot_size = terms.lot_size
    try:
        from broker.dhan import ScripMaster

        chain = [date.fromisoformat(str(v)[:10]) for v in (ScripMaster.get_expiries(terms.symbol) or [])]
        if chain:
            live_lot = int(ScripMaster.get_lot_size(terms.symbol, min(chain).isoformat()) or 0)
            if live_lot > 0:
                lot_size = live_lot
    except Exception as exc:
        _logger.warning("[FIB TOUCH] %s lot size fell back to %s: %s", terms.symbol, lot_size, exc)

    config = FibTouchConfig(
        symbol=terms.symbol,
        side=side,
        mother_timestamp=mother_timestamp,
        lot_size=lot_size,
        strike_step=terms.strike_step,
        timeframe=timeframe,
        capital_cap_inr=float(payload.capital_cap_inr),
        itm_steps=int(payload.itm_steps),
        min_dte=int(payload.min_dte),
    )
    # Live is built and deliberately NOT armed -- the toggle and the whole code
    # path exist with the exchange call still closed, so arming stays a separate
    # explicit act rather than something a payload can flip.
    executor = _FibTouchLiveExecutor(broker_client, terms.symbol) if mode == "live" else _FibTouchPaperExecutor()
    # A mother from an earlier day needs RECORDED prices; today's needs none,
    # and building the Upstox source blocks, so it is only paid for when the
    # campaign will actually read from it.
    history = None
    if mother_timestamp.date() != now.date():
        history = await asyncio.to_thread(
            _fib_touch_history_lookup, broker_client, terms.symbol, mother_timestamp.date(), now.date()
        )
    engine = FibTouchLadder(
        config,
        premium_lookup=_fib_touch_premium_lookup(broker_client, terms.symbol, history),
        expiry_source=_fib_touch_expiry_source(broker_client, terms.symbol),
        executor=executor,
    )
    last_candle_timestamp = mother_timestamp
    if timeframe != "1m":
        for candle in geometry_candles:
            engine.on_geometry_candle(candle)
    for candle in candles:
        if candle.timestamp < mother_timestamp:
            continue
        engine.on_candle(candle)
        last_candle_timestamp = candle.timestamp

    runtime = _CascadeRuntime(
        engine=engine,
        adapter=adapter,
        broker=broker_client,
        last_candle_timestamp=last_candle_timestamp,
        running=True,
    )
    _fib_boundary_engines.setdefault(user_id, {})[terms.symbol] = runtime
    runtime.task = asyncio.create_task(_run_fib_boundary_paper_loop(user_id, runtime))
    await _save_fib_boundary_open_state(user_id, force=True)
    return {
        "status": "started",
        "mode": str(getattr(engine.executor, "mode", "paper")),
        "campaign": {**engine.get_status(), "running": runtime.running},
    }


def _fib_boundary_runtime(request: Request, symbol: str) -> tuple[str, _CascadeRuntime]:
    """Resolve the one ladder named by a route or query-string instrument."""
    try:
        terms = symbol_terms(symbol)
    except FibTouchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime = _fib_boundary_engines.get(_request_user_id(request), {}).get(terms.symbol)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"No {terms.symbol} ladder is active.")
    return terms.symbol, runtime


@app.post("/api/fib-boundary/live/{symbol}/arm")
async def fib_boundary_live_arm(symbol: str, request: Request):
    """Arm a LIVE ladder so its next decision reaches the exchange.

    This is the deliberate step the executor refuses without. It is a separate
    route on purpose: no payload, config value or environment variable can open
    live execution, and it sits on `_SENSITIVE_ACTION_RULES` so it costs a
    password and an authenticator code exactly like starting live trading does.

    Arming does NOT retro-fill anything. Rungs the ladder decided on while it
    was refused stay refused; only decisions made from here on are sent.

    It arms exactly ONE ladder -- the named symbol's. A user running four is
    arming one instrument, not the whole board.
    """
    _symbol, runtime = _fib_boundary_runtime(request, symbol)
    executor = getattr(runtime.engine, "executor", None)
    if not getattr(executor, "is_live", False):
        raise HTTPException(
            status_code=400,
            detail="This ladder is running in paper. Kill it and start one in live mode before arming.",
        )
    if not _FIB_TOUCH_LIVE_EXECUTION_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Fib Boundary live arming is disabled until its broker order lifecycle is verified.",
        )
    if getattr(executor, "armed", False):
        return {"status": "already_armed", "campaign": runtime.engine.get_status()}
    executor.armed = True
    # A refusal parked the campaign; arming releases it to act on the next bar.
    if runtime.engine.status in {"EXECUTION_REFUSED", "EXIT_REFUSED"}:
        runtime.engine.status = "OPEN" if runtime.engine.fills else "ARMED"
    _logger.warning(
        "[FIB TOUCH] LIVE ARMED for user %s on %s %s -- real orders will now be sent",
        _request_user_id(request),
        runtime.engine.config.symbol,
        runtime.engine.side,
    )
    return {"status": "armed", "campaign": {**runtime.engine.get_status(), "running": runtime.running}}


@app.post("/api/fib-boundary/paper/arm")
async def fib_boundary_legacy_arm(_request: Request, symbol: str = "NIFTY"):
    """Fail closed for cached clients that pre-date symbol-bound live routes."""
    del symbol
    raise HTTPException(status_code=410, detail="Reload PhilForge and use the symbol-bound live arm control.")


async def _kill_fib_boundary_runtime(user_id: int, symbol: str, runtime: _CascadeRuntime) -> dict:
    """Price and close one ladder only after its executor confirms the exit."""
    now = datetime.now(IST)
    try:
        quote = await asyncio.to_thread(runtime.adapter.get_ticker, symbol)
        price = float(quote["last_price"])
    except Exception:
        price = float(runtime.engine.history[-1].close) if runtime.engine.history else 0.0
    if not runtime.engine.kill_and_close(IndexCandle(now, price, price, price, price)):
        raise HTTPException(
            status_code=409, detail="Current option quote or broker exit unavailable; the basket remains monitored."
        )
    runtime.running = False
    if runtime.task and not runtime.task.done():
        runtime.task.cancel()
    await _save_fib_boundary_open_state(user_id, force=True)
    mode = str(getattr(runtime.engine.executor, "mode", "paper"))
    return {"status": "killed", "mode": mode, "campaign": {**runtime.engine.get_status(), "running": False}}


@app.post("/api/fib-boundary/paper/kill")
async def fib_boundary_paper_kill(request: Request, symbol: str = "NIFTY"):
    """Kill one paper ladder; live exits use the MFA-gated live route."""
    symbol, runtime = _fib_boundary_runtime(request, symbol)
    if bool(getattr(runtime.engine.executor, "is_live", False)):
        raise HTTPException(
            status_code=409,
            detail="This is a live ladder. Reload PhilForge and use its MFA-gated live Kill & close control.",
        )
    return await _kill_fib_boundary_runtime(_request_user_id(request), symbol, runtime)


@app.post("/api/fib-boundary/live/{symbol}/kill")
async def fib_boundary_live_kill(symbol: str, request: Request):
    """Exit one live ladder through its broker executor, then stop it."""
    symbol, runtime = _fib_boundary_runtime(request, symbol)
    if not bool(getattr(runtime.engine.executor, "is_live", False)):
        raise HTTPException(status_code=400, detail="This ladder is paper-only; use its paper Kill control.")
    if not _FIB_TOUCH_LIVE_EXECUTION_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "Automatic Fib Boundary live exit is disabled because multi-leg fills are not yet reconciled. "
                "No PhilForge state was changed; manage any real position in Dhan and reconcile before stopping."
            ),
        )
    return await _kill_fib_boundary_runtime(_request_user_id(request), symbol, runtime)


@app.get("/api/fib-boundary/paper/chart")
async def fib_boundary_paper_chart(
    mother_timestamp: str,
    request: Request,
    symbol: str = "NIFTY",
    side: str = "CE",
    timeframe: str = "1m",
):
    """The swing ladder's own window: real 1m candles, the swing, every level.

    The anchor is recomputed here with the SAME `find_swing_anchor` the engine
    runs, rather than read off the live campaign. That is deliberate: the
    function is pure over (candles, mother, side), so a chart drawn from it
    cannot drift from the ladder being traded -- and the chart still works
    before a campaign is started, which is when it is most useful.
    """

    try:
        terms = symbol_terms(symbol)
    except FibTouchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    side = str(side).upper()
    if side not in {"CE", "PE"}:
        raise HTTPException(status_code=400, detail="side must be CE or PE.")
    timeframe = str(timeframe).lower()
    if timeframe not in _FIB_TOUCH_GEOMETRY_TF:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {', '.join(_FIB_TOUCH_GEOMETRY_TF)}.")
    mother = _parse_cascade_mother_timestamp(mother_timestamp)
    now = datetime.now(IST)
    if mother.date() > now.date():
        raise HTTPException(status_code=400, detail="Mother timestamp cannot be in the future (IST).")
    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(status_code=400, detail=f"Connect a Dhan account to load the {terms.symbol} chart.")
    adapter = CascadeOptionsAdapter(broker_client, paper_only=True)
    try:
        # Drawn on the MOTHER's chart: that is the chart the swing was read on,
        # and a 15m mother rendered over days of 1m bars is unreadable.
        candles = await adapter.async_get_candles(terms.symbol, timeframe, from_date=mother.date(), to_date=now.date())
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Unable to load {terms.symbol} {timeframe} candles: {exc}"
        ) from exc
    rows = _cascade_gap_adjusted_candles(candles, mother)
    if not any(row["is_mother"] for row in rows):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Dhan has no {terms.symbol} {timeframe} candle opening at "
                f"{mother.strftime('%d %b %Y %H:%M')} IST. Check the date, the time and the timeframe."
            ),
        )

    anchor = _fib_touch_find_anchor(candles, mother, side)
    # Drawn only. Phil asked for CryptoForge's trendline on this chart but kept
    # the fib on the swing, so the line is rendered and consulted by nothing.
    trendline = _fib_touch_find_trendline(candles, mother, side, anchor) if anchor is not None else None
    levels: list[dict] = []
    if anchor is not None:
        levels = [
            {
                "level": level,
                "price": round(_fib_touch_level_price(side, anchor.high, anchor.low, level), 2),
            }
            for level in HALVING_LEVELS
        ]
    return {
        "status": "ok",
        "symbol": terms.symbol,
        "timeframe": timeframe,
        "side": side,
        "chart_mode": "visual_gap_adjusted",
        "candles": rows,
        "anchor": (
            {
                "high": anchor.high,
                "low": anchor.low,
                "span": round(anchor.span, 2),
                "high_timestamp": anchor.high_timestamp.isoformat(),
                "low_timestamp": anchor.low_timestamp.isoformat(),
                "confirmed_at": anchor.confirmed_at.isoformat(),
            }
            if anchor
            else None
        ),
        "levels": levels,
        "trendline": trendline.as_dict() if trendline is not None else None,
        "note": (
            "Gap adjustment is visual only; the ladder's geometry uses native Dhan OHLC."
            if anchor
            else "No involvement has closed after this mother yet, so the swing is not frozen and no level can be priced."
        ),
    }


def _nifty_lot_size_on(day: date) -> int:
    """NIFTY's lot size as it stood on a given trade date.

    This constant used to be a hardcoded 75 sitting under a comment promising
    "whichever it used, so an old mother's P&L magnitude is never silently
    wrong" -- the intent was right and the implementation never followed it.
    NIFTY stepped 50 -> 75 on 2024-11-20 and 75 -> 65 on 2026-01-01, so one
    number cannot be correct for a replay that crosses either boundary: every
    2026 mother was being sized 15% too large.

    The date is the mother's, not the expiry's: a lot-size revision applies to
    contracts introduced after it, so a December 2025 mother holding a January
    2026 expiry still traded 75s.  `engine.backtest.LOT_SIZES` is the one table
    (the Test Bench already reads it) -- add new steps there, never here.
    """

    from engine.backtest import get_lot_size

    return int(get_lot_size("NIFTY", day))


def _serialize_cascade_backtest(engine: NiftyOptionsPaperCascade) -> dict:
    """Flatten a real-geometry cascade backtest into the JSON the UI reads.

    Same shape the fib backtest panel already renders, but sourced from the
    CryptoForge geometry engine: entries are the priced fills (each with its own
    per-entry strike), P&L sums across every round, and a missing Upstox bar is
    a recorded quote gap rather than a fabricated price.
    """

    def _iso(value):
        return value.isoformat() if value is not None else None

    def _rung(fill, part: int) -> Optional[int]:
        # A rung key is "<leg_id>:<level>", so part 0 names the leg the buy came
        # from and part 1 the fib line it sat on.  Both are needed to put a spend
        # figure back on the right line of the chart.
        for key in fill.rung_keys:
            try:
                return int(str(key).split(":")[part])
            except (IndexError, ValueError):
                continue
        return None

    rounds = engine.rounds
    all_fills = [fill for row in rounds for fill in row.fills] + list(engine.open_fills)
    entries = []
    for fill in all_fills:
        contract = fill.contract or engine.contract
        premium = fill.option_premium
        entries.append(
            {
                "timestamp": _iso(fill.timestamp),
                "spot": fill.index_price,
                "option_price": premium,
                "lots": fill.lots,
                "quantity": fill.quantity,
                "level": _rung(fill, 1),
                "leg_id": _rung(fill, 0),
                # What this entry actually cost in premium.  None when Upstox had
                # no bar for it: an unpriced fill is a gap, never a free trade.
                "spend_inr": (round(float(premium) * int(fill.quantity), 2) if premium is not None else None),
                "strike": contract.strike,
                "option_type": contract.option_type,
                "expiry": contract.expiry.isoformat(),
            }
        )
    quantity = sum(fill.quantity for fill in all_fills)
    average_spot = (
        round(sum(fill.index_price * fill.quantity for fill in all_fills) / quantity, 2) if quantity else None
    )
    last = rounds[-1] if rounds else None
    target_index = last.target_index if last else engine.target_index
    net_pnl = round(sum(row.net_pnl for row in rounds), 2) if rounds else None
    gross_pnl = round(sum(row.gross_pnl for row in rounds), 2) if rounds else None
    costs_total = round(sum(row.costs.total for row in rounds), 2) if rounds else 0.0
    index_move = (
        round(target_index - average_spot, 2) if (target_index is not None and average_spot is not None) else None
    )
    data_gaps = [
        f"missing {event.get('action', '?')} premium at {event.get('timestamp')}"
        for event in engine.events
        if event.get("event") == "option_quote_missing"
    ]
    contract = None
    if all_fills:
        # Read the underlying and lot size off the contract that was actually
        # filled rather than the NIFTY constants: the same engine now runs
        # BankNifty and Sensex, whose lots are nothing like 65.
        first = all_fills[0].contract or engine.contract
        contract = {
            "underlying": first.underlying,
            "strike": entries[0]["strike"],
            "option_type": entries[0]["option_type"],
            "expiry": entries[0]["expiry"],
            "lot_size": first.lot_size,
        }
    fully_priced = bool(rounds) and not data_gaps and all(entry["option_price"] is not None for entry in entries)
    return {
        "status": "closed" if rounds else str(engine.status).lower(),
        "fully_priced": fully_priced,
        "gross_pnl": gross_pnl,
        "costs_total": costs_total,
        "net_pnl": net_pnl,
        "target_index": target_index,
        "average_spot": average_spot,
        "index_move": index_move,
        "exit_reason": last.exit_reason if last else ("open" if engine.open_fills else str(engine.status).lower()),
        "exit_timestamp": _iso(last.closed_at) if last else None,
        "exit_option_price": last.exit_option_premium if last else None,
        "exit_option_prices": [],
        "data_gaps": data_gaps,
        "entries": entries,
        "contract": contract,
        "rounds_count": len(rounds),
    }


def _serialize_cascade_geometry(engine: NiftyOptionsPaperCascade, mother: IndexCandle, candles: list) -> dict:
    """Freeze the index-space geometry into a journal chart payload.

    Everything the CryptoForge state machine actually drew -- the mother frame,
    each auto trendline (mother_high -> touch anchor), and each leg's fib ladder
    (touch high/low anchors plus the 2/4/8 deep boundaries the rungs sit on) --
    alongside the replayed candle series and the per-round targets/exits.  It is
    a picture of the same geometry the fills came from, nothing recomputed.
    """

    def _iso(value):
        return value.isoformat() if value is not None else None

    campaign = engine.geometry.campaign
    # Mother first, then every forward candle, de-duplicated and time-ordered so
    # the chart's bar sequence matches the engine's.
    by_ts = {mother.timestamp: mother}
    for row in candles:
        by_ts.setdefault(row.timestamp, row)
    ordered = [by_ts[key] for key in sorted(by_ts)]
    candle_rows = [
        {
            "t": _iso(row.timestamp),
            "o": row.open,
            "h": row.high,
            "l": row.low,
            "c": row.close,
            "is_mother": row.timestamp == mother.timestamp,
        }
        for row in ordered
    ]
    trendlines = [
        {
            "id": line.trendline_id,
            "a1t": _iso(line.anchor1_timestamp),
            "a1p": line.anchor1_price,
            "a2t": _iso(line.anchor2_timestamp),
            "a2p": line.anchor2_price,
        }
        for line in campaign.trendlines
    ]
    legs = []
    for leg in campaign.legs:
        fib = leg.fib
        legs.append(
            {
                "leg_id": leg.leg_id,
                "trendline_id": leg.trendline_id,
                "touch_t": _iso(leg.touch_timestamp),
                "touch_high": leg.touch_high,
                "low": leg.low,
                # 0 = the touch high anchor, 1 = the touch low anchor, and 2/4/8
                # are the deep boundaries the cascade rungs fire on.
                "levels": {str(level): fib.level_price(level) for level in (0, 1, 2, 4, 8)},
            }
        )
    rounds = [
        {
            "round_id": row.round_id,
            "opened_at": _iso(row.opened_at),
            "closed_at": _iso(row.closed_at),
            "target_index": row.target_index,
            "exit_index_price": row.exit_index_price,
            "exit_reason": row.exit_reason,
            "net_pnl": row.net_pnl,
            "fills": [
                {"timestamp": _iso(fill.timestamp), "index_price": fill.index_price, "lots": fill.lots}
                for fill in row.fills
            ],
        }
        for row in engine.rounds
    ]
    open_fills = [
        {"timestamp": _iso(fill.timestamp), "index_price": fill.index_price, "lots": fill.lots}
        for fill in engine.open_fills
    ]
    return {
        "candles": candle_rows,
        "mother": {"t": _iso(mother.timestamp), "high": mother.high, "low": mother.low},
        "trendlines": trendlines,
        "legs": legs,
        "rounds": rounds,
        "open_fills": open_fills,
    }


def _serialize_fib_boundary_backtest(result, lot_size: int) -> dict:
    """Flatten a typed-mother `FibBoundaryCascade` replay into the panel's JSON.

    Deliberately the same field names `_serialize_cascade_backtest` produces, so
    the panel renders either without a branch.  `level` is the fib boundary the
    buy sat on -- which this engine carries as the entry's stage, because a typed
    ladder has no legs to key a rung against.
    """

    def _iso(value):
        return value.isoformat() if value is not None else None

    entries = []
    for entry in result.entries:
        premium = entry.option_price
        entries.append(
            {
                "timestamp": _iso(entry.timestamp),
                "spot": entry.spot,
                "option_price": premium,
                "lots": entry.lots,
                "quantity": entry.quantity,
                "level": entry.stage,
                "leg_id": None,
                # An unpriced fill is a gap, never a free trade.
                "spend_inr": (round(float(premium) * int(entry.quantity), 2) if premium is not None else None),
                "strike": entry.contract.strike,
                "option_type": entry.contract.option_type,
                "expiry": entry.contract.expiry.isoformat(),
            }
        )
    # Each rung re-selects its own strike as the index falls, so the campaign
    # holds several. The first one names the campaign; the table shows them all.
    first = result.entries[0].contract if result.entries else None
    return {
        "status": result.status,
        "contract": (
            {
                "strike": first.strike,
                "expiry": first.expiry.isoformat(),
                "option_type": first.option_type,
                "lot_size": getattr(first, "lot_size", lot_size),
            }
            if first is not None
            else None
        ),
        "entries": entries,
        "exit_timestamp": _iso(result.exit_timestamp),
        "exit_reason": result.exit_reason,
        "target_index": result.target_index,
        "average_spot": result.average_spot,
        "index_move": result.index_move,
        "gross_pnl": result.realized_pnl,
        "costs_total": result.costs_total,
        "net_pnl": result.net_pnl,
        "fully_priced": result.fully_priced,
        "data_gaps": list(result.data_gaps or []),
    }


@app.post("/api/fib-boundary/backtest")
async def fib_boundary_backtest(payload: FibTouchBacktestPayload, request: Request):
    """Replay a past mother through the SWING TOUCH LADDER, on real premiums.

    This is the same engine the Start button trades -- `FibTouchLadder` -- fed
    historical candles and priced from recorded option history rather than a
    live quote.  That identity is the whole point: the tab spent a day carrying
    a backtest of one ladder beside a Start button trading another, and the two
    could not be compared.  They run the same code now.

    Nothing is placed: the executor is the paper one, and the route never
    touches an order API.
    """

    try:
        terms = symbol_terms(payload.symbol)
    except FibTouchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    side = str(payload.side).upper()
    if side not in {"CE", "PE"}:
        raise HTTPException(status_code=400, detail="side must be CE or PE.")
    timeframe = str(payload.timeframe).lower()
    if timeframe not in _FIB_TOUCH_GEOMETRY_TF:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {', '.join(_FIB_TOUCH_GEOMETRY_TF)}.")

    mother_timestamp = _parse_cascade_mother_timestamp(payload.mother_timestamp)
    now = datetime.now(IST)
    if mother_timestamp.date() > now.date():
        raise HTTPException(status_code=400, detail="Mother timestamp cannot be in the future (IST).")
    if not (dt_time(9, 15) <= mother_timestamp.time() <= dt_time(15, 30)):
        raise HTTPException(status_code=400, detail="Mother candle must be within the 09:15-15:30 session.")
    tf_minutes = _FIB_TOUCH_TF_MINUTES[timeframe]
    if timeframe == "1h":
        if mother_timestamp.minute != 15:
            raise HTTPException(status_code=400, detail="A 1H mother opens at 09:15, 10:15 ... 15:15 IST.")
    elif mother_timestamp.minute % tf_minutes:
        raise HTTPException(
            status_code=400, detail=f"Mother timestamp must be an NSE-aligned {timeframe} candle open in IST."
        )

    if not terms.backtestable:
        # FINNIFTY and MIDCPNIFTY have no premium source at all. Returning a
        # zero-filled replay would look like a result; saying so is the honest
        # answer, and the same fact the form already shows on the picker.
        raise HTTPException(
            status_code=400,
            detail=(
                f"{terms.symbol} has no recorded option history, so a backtest of it would have no prices. "
                f"Priced today: {', '.join(t.symbol for t in _FIB_TOUCH_SYMBOLS.values() if t.backtestable)}."
            ),
        )

    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(status_code=400, detail=f"Connect a Dhan account to load {terms.symbol} candles.")

    horizon_to = min(now.date(), mother_timestamp.date() + timedelta(days=int(payload.horizon_days)))
    adapter = CascadeOptionsAdapter(broker_client, paper_only=True)
    try:
        geometry_candles = await adapter.async_get_candles(
            terms.symbol, timeframe, from_date=mother_timestamp.date(), to_date=horizon_to
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Unable to load {terms.symbol} {timeframe} candles: {exc}"
        ) from exc
    if not any(row.timestamp == mother_timestamp for row in geometry_candles):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Dhan has no {terms.symbol} {timeframe} candle opening at "
                f"{mother_timestamp.strftime('%d %b %Y %H:%M')} IST. Check the date, the time and the timeframe."
            ),
        )
    entry_candles = (
        geometry_candles
        if timeframe == "1m"
        else await adapter.async_get_candles(terms.symbol, "1m", from_date=mother_timestamp.date(), to_date=horizon_to)
    )

    lot_size = _nifty_lot_size_on(mother_timestamp.date()) if terms.symbol == "NIFTY" else terms.lot_size
    config = FibTouchConfig(
        symbol=terms.symbol,
        side=side,
        mother_timestamp=mother_timestamp,
        lot_size=lot_size,
        strike_step=terms.strike_step,
        timeframe=timeframe,
        capital_cap_inr=float(payload.capital_cap_inr),
        itm_steps=int(payload.itm_steps),
        min_dte=int(payload.min_dte),
    )

    def _run() -> dict:
        # Upstox construction and every per-contract fetch block, so the whole
        # replay runs off the event loop.
        history = _fib_touch_history_lookup(broker_client, terms.symbol, mother_timestamp.date(), horizon_to)
        expiries = _fib_touch_expiry_source(broker_client, terms.symbol)

        def priced(when: datetime, strike: float, expiry: date, option_side: str) -> float | None:
            if history is None:
                return None
            try:
                value = history(
                    when,
                    SimpleNamespace(
                        symbol=terms.symbol,
                        underlying=terms.symbol,
                        strike=float(strike),
                        expiry=expiry,
                        option_type=str(option_side).upper(),
                    ),
                )
                return float(value) if value is not None and float(value) > 0 else None
            except Exception:
                return None

        engine = FibTouchLadder(config, premium_lookup=priced, expiry_source=expiries)
        if timeframe != "1m":
            for row in geometry_candles:
                engine.on_geometry_candle(row)
        for row in entry_candles:
            engine.on_candle(row)
            if engine.status in {"CLOSED", "EXPIRED", "MOTHER_BROKEN"}:
                break
        return {
            "campaign": engine.get_status(),
            "priced": history is not None,
            "last_bar": (engine.history[-1].timestamp.isoformat() if engine.history else None),
            # A dead token or a rate-limited fetch is NOT a market gap. The
            # hybrid lookup separates the two, and the panel has to keep
            # saying which happened -- a failed replay must never be able to
            # masquerade as "the market simply did not trade".
            "premium_failures": list(getattr(history, "source_failures", []) or []),
            "premium_stale_fills": list(getattr(history, "stale_fills", []) or []),
        }

    try:
        outcome = await asyncio.to_thread(_run)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Backtest failed: {exc}") from exc

    campaign = outcome["campaign"]
    anchor = campaign.get("anchor")
    # The chart speaks the SAME payload the live chart route does, so the
    # console draws both through one translator.
    chart = {
        "status": "ok",
        "symbol": terms.symbol,
        "timeframe": timeframe,
        "side": side,
        "chart_mode": "visual_gap_adjusted",
        "candles": _cascade_gap_adjusted_candles(geometry_candles, mother_timestamp),
        "anchor": anchor,
        "levels": [{"level": row["level"], "price": row["index_price"]} for row in (campaign.get("levels") or [])],
        "trendline": None,
    }
    if anchor is not None:
        line = _fib_touch_find_trendline(
            geometry_candles,
            mother_timestamp,
            side,
            _FibTouchSwingAnchor(
                high=float(anchor["high"]),
                low=float(anchor["low"]),
                high_timestamp=datetime.fromisoformat(anchor["high_timestamp"]),
                low_timestamp=datetime.fromisoformat(anchor["low_timestamp"]),
                confirmed_at=datetime.fromisoformat(anchor["confirmed_at"]),
                involvement_candles=int(anchor.get("involvement_candles") or 2),
            ),
        )
        chart["trendline"] = line.as_dict() if line is not None else None

    return {
        "status": "ok",
        "mode": "backtest",
        "engine": "fib_touch_ladder",
        "pricing": "recorded_history" if outcome["priced"] else "unpriced",
        "symbol": terms.symbol,
        "side": side,
        "timeframe": timeframe,
        "lot_size": lot_size,
        "mother": {"timestamp": mother_timestamp.isoformat()},
        "candles_replayed": len(entry_candles),
        "horizon_to": horizon_to.isoformat(),
        "campaign": campaign,
        "premium_failures": outcome["premium_failures"],
        "premium_stale_fills": outcome["premium_stale_fills"],
        "chart": chart,
        "note": (
            f"{terms.symbol} {side} swing touch ladder on a {timeframe} mother, 1m entries. "
            f"Same engine the Start button trades; every price is a recorded option trade, "
            f"and a minute nothing printed is a listed gap, never a fabricated zero."
            if outcome["priced"]
            else "No recorded option history was reachable, so this replay is geometry only — no prices, no P&L."
        ),
    }


@app.get("/api/fib-boundary/backtests")
async def list_fib_boundary_backtests(request: Request, limit: int = 50):
    return {"status": "ok", "runs": await _db_mod.list_fib_backtest_runs(_request_user_id(request), limit)}


async def _owned_fib_backtest(request: Request, run_id: int) -> dict:
    run = await _db_mod.get_fib_backtest_run(_request_user_id(request), run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Fib Boundary backtest not found.")
    return run


@app.get("/api/fib-boundary/backtests/{run_id}/export.json")
async def export_fib_boundary_backtest_json(run_id: int, request: Request):
    run = await _owned_fib_backtest(request, run_id)
    body = json.dumps(run["payload"], ensure_ascii=False, indent=2, default=str)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="fib-boundary-{run_id}.json"'},
    )


@app.get("/api/fib-boundary/backtests/{run_id}/export.csv")
async def export_fib_boundary_backtest_csv(run_id: int, request: Request):
    import csv

    run = await _owned_fib_backtest(request, run_id)
    payload = run["payload"]
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    output = io.StringIO()
    fields = [
        "row_type",
        "mother_timestamp",
        "side",
        "timeframe",
        "timestamp",
        "level",
        "index_price",
        "strike",
        "option_type",
        "expiry",
        "option_price",
        "lots",
        "quantity",
        "net_pnl",
        "gap",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    common = {
        "mother_timestamp": (payload.get("mother") or {}).get("timestamp"),
        "side": payload.get("side"),
        "timeframe": payload.get("timeframe"),
        "net_pnl": result.get("net_pnl"),
    }
    for entry in result.get("entries") or []:
        writer.writerow(
            {
                **common,
                "row_type": "priced_leg",
                "timestamp": entry.get("timestamp"),
                "level": entry.get("level"),
                "index_price": entry.get("spot"),
                "strike": entry.get("strike"),
                "option_type": entry.get("option_type"),
                "expiry": entry.get("expiry"),
                "option_price": entry.get("option_price"),
                "lots": entry.get("lots"),
                "quantity": entry.get("quantity"),
            }
        )
    for gap in result.get("data_gaps") or []:
        writer.writerow({**common, "row_type": "premium_gap", "gap": gap})
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="fib-boundary-{run_id}.csv"'},
    )


@app.get("/api/options/archive")
async def option_archive_inventory(request: Request, provider: str = "", underlying: str = "", limit: int = 500):
    _request_user_id(request)
    from data.option_archive import OptionDataArchive

    rows = await asyncio.to_thread(
        OptionDataArchive().inventory,
        provider=provider,
        underlying=underlying,
        limit=limit,
    )
    return {"status": "ok", "contracts": rows, "count": len(rows)}


@app.get("/api/options/archive/export.csv")
async def option_archive_export_csv(
    request: Request,
    provider: str,
    underlying: str,
    expiry: date,
    strike: int,
    option_type: str,
):
    _request_user_id(request)
    from data.option_archive import OptionDataArchive

    side = str(option_type).upper()
    if side not in {"CE", "PE"}:
        raise HTTPException(status_code=400, detail="option_type must be CE or PE.")
    rows = await asyncio.to_thread(
        OptionDataArchive().export_rows,
        provider=provider,
        underlying=underlying,
        expiry=expiry,
        strike=strike,
        option_type=side,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="No archived bars found for that exact contract.")
    import csv

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["timestamp", "open", "high", "low", "close"])
    writer.writeheader()
    writer.writerows({key: row.get(key) for key in writer.fieldnames} for row in rows)
    safe_provider = re.sub(r"[^A-Za-z0-9_-]+", "-", provider).strip("-") or "provider"
    safe_underlying = re.sub(r"[^A-Za-z0-9_-]+", "-", underlying).strip("-") or "option"
    filename = f"{safe_provider}-{safe_underlying}-{expiry.isoformat()}-{int(strike)}{side}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _premium_minute(moment: datetime) -> datetime:
    """One canonical minute key: naive IST wall clock.

    The engine's index candles are IST-aware while Dhan's option frame is
    stamped naive — an aware and a naive datetime never compare equal, so
    keying both sides through here is what lets a premium lookup hit at all.
    """
    if moment.tzinfo is not None:
        moment = moment.astimezone(IST).replace(tzinfo=None)
    return moment.replace(second=0, microsecond=0)


# An illiquid strike can go a few minutes without a trade; a market buy there
# would still fill near the last traded price.  How many minutes back a lookup
# may reach for that last real bar before the minute is an honest gap.
_PREMIUM_STALE_LIMIT_MINUTES = 10


def _hybrid_premium_lookup(
    broker: DhanClient,
    instrument: str,
    upstox_source,
    upstox_expiries: set,
    from_day: date,
    to_day: date,
    forward_minutes: int = 5,
):
    """Upstox bar first, Dhan's own option candles when Upstox has nothing.

    Upstox records a contract's minutes only after it EXPIRES; Dhan can serve
    a still-listed contract's minutes right now.  Between them a replay can
    price any contract the resolver picks.  A minute neither source recorded
    is priced from a real neighbouring trade — first scanning FORWARD through
    the rest of the fill's own candle (``forward_minutes`` is the replay
    timeframe: an order resting at the level fills at the option's next trade,
    which on a gap-up session open can be a minute or two into the candle),
    then back up to ``_PREMIUM_STALE_LIMIT_MINUTES`` the same day — each one
    disclosed on ``lookup.stale_fills`` — and stays None past both.  One Dhan
    fetch per contract, cached for the whole replay.

    A contract whose Dhan fetch FAILED (dead token, rate limit, no security
    id) is not a data gap: the reason lands on ``lookup.source_failures`` so
    the caller can say what actually broke instead of "minute has no bar".
    """

    from data.option_archive import OptionDataArchive

    archive = OptionDataArchive()
    dhan_minutes: dict[tuple, dict] = {}
    source_failures: list[str] = []
    stale_fills: list[str] = []

    def _dhan_series(contract) -> dict:
        key = (int(contract.strike), contract.expiry, str(contract.option_type))
        if key not in dhan_minutes:
            label = f"{instrument} {int(contract.strike)}{contract.option_type} {contract.expiry.isoformat()}"
            series: dict = {}
            archived = archive.load(
                provider="dhan",
                underlying=instrument,
                expiry=contract.expiry,
                strike=contract.strike,
                option_type=contract.option_type,
            )
            archive_start = min(archived, default=None)
            archive_end = max(archived, default=None)
            required_end = min(to_day, contract.expiry)
            if (
                archive_start is not None
                and archive_start.date() <= from_day
                and archive_end is not None
                and archive_end.date() >= required_end
            ):
                series = {minute: float(row["open"]) for minute, row in archived.items()}
            try:
                if not series:
                    security_id = ScripMaster.lookup(
                        instrument, int(contract.strike), contract.expiry.isoformat(), contract.option_type
                    )
                    if security_id:
                        frame = broker.get_historical_data(
                            security_id,
                            "NSE_FNO",
                            "OPTIDX",
                            0,
                            from_day.isoformat(),
                            required_end.isoformat(),
                            "1",
                        )
                        raw_bars = {
                            _premium_minute(index.to_pydatetime()): {
                                "open": float(row["open"]),
                                "high": float(row.get("high", row["open"])),
                                "low": float(row.get("low", row["open"])),
                                "close": float(row.get("close", row["open"])),
                            }
                            for index, row in frame.iterrows()
                        }
                        series = {minute: float(row["open"]) for minute, row in raw_bars.items()}
                        if raw_bars:
                            archive.store(
                                provider="dhan",
                                underlying=instrument,
                                expiry=contract.expiry,
                                strike=contract.strike,
                                option_type=contract.option_type,
                                bars=raw_bars,
                                instrument_key=str(security_id),
                            )
                        else:
                            source_failures.append(f"Dhan returned no candles at all for {label}")
                    else:
                        source_failures.append(f"Dhan's scrip master has no security id for {label}")
            except Exception as exc:
                source_failures.append(f"Dhan option candles unavailable for {label}: {exc}")
                _logger.warning("[premiums] Dhan option candles unavailable for %s: %s", label, exc)
            dhan_minutes[key] = series
        return dhan_minutes[key]

    def _price_at(minute: datetime, contract):
        if upstox_source is not None and contract.expiry in upstox_expiries:
            bar = upstox_source.lookup(minute, contract)
            if bar is not None:
                return float(bar.open)
        return _dhan_series(contract).get(minute)

    def lookup(timestamp, contract):
        minute = _premium_minute(timestamp)
        price = _price_at(minute, contract)
        if price is not None:
            return price
        # Forward, inside the fill's own candle: the 2026-07-27 09:15 gap-up
        # crossed the target on the opening candle before the deep strike had
        # printed its first trade of the day — the next trade IS the fill.
        for ahead in range(1, max(int(forward_minutes), 1)):
            later = minute + timedelta(minutes=ahead)
            if later.date() != minute.date():
                break
            price = _price_at(later, contract)
            if price is not None:
                stale_fills.append(
                    f"{int(contract.strike)}{contract.option_type} at {minute.strftime('%H:%M')} priced from "
                    f"its next trade {ahead} min into the candle ({later.strftime('%H:%M')} bar, ₹{price:,.2f})"
                )
                return price
        for back in range(1, _PREMIUM_STALE_LIMIT_MINUTES + 1):
            earlier = minute - timedelta(minutes=back)
            if earlier.date() != minute.date():
                break
            price = _price_at(earlier, contract)
            if price is not None:
                stale_fills.append(
                    f"{int(contract.strike)}{contract.option_type} at {minute.strftime('%H:%M')} priced from "
                    f"the last trade {back} min earlier ({earlier.strftime('%H:%M')} bar, ₹{price:,.2f})"
                )
                return price
        return None

    lookup.source_failures = source_failures
    lookup.stale_fills = stale_fills
    return lookup


def _fib_touch_history_lookup(broker: DhanClient, symbol: str, from_day: date, to_day: date):
    """Recorded option prices for a symbol over a window, or None.

    Upstox records a contract's minutes only once it has EXPIRED; Dhan serves a
    still-listed one now. Between them a replay can price most contracts the
    resolver picks. BLOCKING (Upstox construction, per-contract fetches) -- call
    it off the event loop.

    Returns None rather than raising when no source is reachable, so starting a
    campaign on an old mother degrades to "no price, recorded gap" instead of
    failing outright.
    """
    try:
        from data.cascade_upstox import UpstoxAccessError, UpstoxPremiumSource

        try:
            from upstox_token_manager import ensure_fresh_token

            ensure_fresh_token()
        except Exception as exc:
            _logger.warning("[FIB TOUCH] Upstox token pre-check skipped: %s", exc)
        premium_source = None
        upstox_expiries: list = []
        try:
            premium_source = UpstoxPremiumSource(backfill_missing=True)
            upstox_expiries = sorted(premium_source.available_expiries())
        except UpstoxAccessError as exc:
            _logger.warning("[FIB TOUCH] %s Upstox history unavailable, Dhan only: %s", symbol, exc)
        return _hybrid_premium_lookup(
            broker,
            symbol,
            premium_source,
            set(upstox_expiries),
            from_day,
            to_day,
            forward_minutes=1,
        )
    except Exception as exc:
        _logger.warning("[FIB TOUCH] %s history lookup unavailable: %s", symbol, exc)
        return None


def _fib_replay_premium_lookup(broker: DhanClient, from_day: date, to_day: date, timeframe: str):
    """The hybrid history lookup, packaged for a fib-boundary paper replay.

    Blocking (Upstox construction + per-contract Dhan fetches) — call it, and
    the replay that uses it, off the event loop.  Returns None when no source
    is reachable so a Start never fails just because the replay can't price.
    """
    try:
        from data.cascade_upstox import UpstoxAccessError, UpstoxPremiumSource

        try:
            from upstox_token_manager import ensure_fresh_token

            ensure_fresh_token()
        except Exception as exc:
            _logger.warning("[fib-replay] Upstox token pre-check skipped: %s", exc)
        premium_source = None
        upstox_expiries: list = []
        try:
            premium_source = UpstoxPremiumSource(backfill_missing=True)
            upstox_expiries = sorted(premium_source.available_expiries())
        except UpstoxAccessError as exc:
            _logger.warning("[fib-replay] Upstox premium history unavailable, using Dhan only: %s", exc)
        return _hybrid_premium_lookup(
            broker,
            "NIFTY",
            premium_source,
            set(upstox_expiries),
            from_day,
            to_day,
            forward_minutes=_FIB_TIMEFRAME_MINUTES.get(timeframe, 5),
        )
    except Exception as exc:
        _logger.warning("[fib-replay] premium history unavailable, replay stays index-only: %s", exc)
        return None


def _known_option_expiries(instrument: str, upstox_expiries: list) -> list:
    """Every expiry either source can price: Upstox's expired ∪ ScripMaster's live."""
    live: list = []
    try:
        live = [date.fromisoformat(str(value)) for value in ScripMaster.get_expiries(instrument) or []]
    except Exception as exc:
        _logger.warning("[premiums] ScripMaster expiries unavailable for %s: %s", instrument, exc)
    return sorted(set(upstox_expiries) | set(live))


async def _test_bench_two_red(
    *,
    instrument: str,
    timeframe: str,
    side: str,
    mother,
    mother_timestamp: datetime,
    horizon_to: date,
    adapter,
    resolver_config,
    underlying_key: str,
    lot_size: int,
    strike_step: float,
    window,
) -> dict:
    """Replay the two-red ladder: buy, climb a timeframe, buy again.

    Unlike the fib strategy this needs the index on SEVERAL charts at once --
    rung 1 watches 1m while rung 2 watches 5m -- so every timeframe in the
    ladder is fetched and the bars are merged into one stream ordered by when
    each closed.  Deriving the slower bars from the 1m series would be cheaper
    but would also mean this screen disagrees with Dhan's own 5m candle, which
    is the thing the live engine will actually trade off.
    """

    from engine.candle_ladder import LadderCandle, TwoRedLadder, ladder_from
    from engine.test_bench import ladder_chart, ladder_result

    stages = ladder_from(timeframe, 4)
    stream: list = []
    for stage_tf in stages:
        try:
            rows = await adapter.async_get_candles(
                instrument, stage_tf, from_date=mother_timestamp.date(), to_date=horizon_to
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail=f"Unable to load {instrument} {stage_tf} candles: {exc}"
            ) from exc
        stream.extend(LadderCandle(stage_tf, row.timestamp, row.open, row.high, row.low, row.close) for row in rows)
    if not stream:
        raise HTTPException(status_code=400, detail=f"No {instrument} candles after that mother.")

    mother_bar = LadderCandle(timeframe, mother.timestamp, mother.open, mother.high, mother.low, mother.close)

    def _run() -> tuple:
        from data.cascade_upstox import UpstoxAccessError, UpstoxPremiumSource

        try:
            from upstox_token_manager import ensure_fresh_token

            ensure_fresh_token()
        except Exception as exc:
            _logger.warning("[test-bench] Upstox token pre-check skipped: %s", exc)

        # Two premium sources, split by whether the contract still exists.
        # Upstox holds priced history for EXPIRED contracts only; a recent
        # mother buys a contract that is still trading, and Dhan can serve
        # that one's own minute candles.  Between them there is no date gap,
        # so — unlike the fib path — this strategy never refuses a mother for
        # being too recent.
        upstox_expiries: list[date] = []
        premium_source = None
        try:
            premium_source = UpstoxPremiumSource(underlying_key=underlying_key, backfill_missing=True)
            upstox_expiries = sorted(premium_source.available_expiries())
        except UpstoxAccessError as exc:
            _logger.warning("[test-bench] Upstox premium history unavailable, using Dhan only: %s", exc)

        expiries = _known_option_expiries(instrument, upstox_expiries)
        if not expiries:
            raise HTTPException(
                status_code=503,
                detail=f"Neither Upstox nor Dhan could name an option expiry for {instrument}.",
            )

        resolver = NiftyContractResolver(
            expiries=expiries, strike_step=strike_step, lot_size=lot_size, symbol=instrument
        )
        try:
            # The expiry is fixed at the mother for the whole ladder; only the
            # strike follows the index down as later rungs buy lower.
            anchor = resolver.select(mother.timestamp, mother.close, side, resolver_config)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"No {side} contract could be bought at that mother: {exc}"
            ) from exc
        expiry = anchor.expiry

        def strike_for(_timestamp, index_price) -> tuple[int, str]:
            contract = resolver.select(mother.timestamp, index_price, side, resolver_config)
            return int(contract.strike), contract.option_type

        hybrid = _hybrid_premium_lookup(
            adapter.dhan,
            instrument,
            premium_source,
            set(upstox_expiries),
            mother_timestamp.date(),
            horizon_to,
            forward_minutes=_FIB_TIMEFRAME_MINUTES.get(timeframe, 5),
        )

        def premium_lookup(timestamp, strike, option_type):
            return hybrid(timestamp, FixedCampaignOption(instrument, int(strike), expiry, option_type, lot_size, ""))

        # Same rule as the fib path: past its own expiry the contract does not
        # exist, so neither does the replay.
        replay = [row for row in stream if row.timestamp.date() <= expiry and row.timestamp > mother_timestamp]
        ladder = TwoRedLadder(
            mother_bar,
            stages=stages,
            strike_for=strike_for,
            premium_lookup=premium_lookup,
            lot_size=lot_size,
        ).run(replay)
        # Only a window that actually REACHED the expiry may close the trade
        # as "held to expiry".  A window that simply ran out of history —
        # today, on a live contract — leaves the trade honestly OPEN.
        if ladder.fills and ladder.status not in {"CLOSED", "EXPIRED"} and replay and horizon_to >= expiry:
            last = max(replay, key=lambda row: row.timestamp)
            ladder.close_at_expiry(last, last.close)
        return ladder, replay, expiry

    try:
        ladder, replay, expiry = await asyncio.to_thread(_run)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Test Bench run failed: {exc}") from exc

    reported = ladder_result(
        ladder,
        instrument=instrument,
        timeframe=timeframe,
        mother_timestamp=mother_timestamp.isoformat(),
        lot_size=lot_size,
    )
    reported["summary"]["expiry"] = expiry.isoformat()
    return {
        "status": "ok",
        "strategy": "two_red",
        "summary": reported["summary"],
        "mother": {"timestamp": mother_timestamp.isoformat(), "high": mother.high, "low": mother.low},
        "ladder": list(stages),
        "lot_size": lot_size,
        "strike_step": strike_step,
        "candles_replayed": len(replay),
        "expiry": expiry.isoformat(),
        "horizon_to": horizon_to.isoformat(),
        "entries": reported["entries"],
        # The mother bar leads the chart candles: the replay stream itself
        # starts strictly after the mother, so without this the one candle
        # the whole trade is anchored to would never be drawn.
        "chart": ladder_chart(ladder, [mother_bar, *replay], timeframe=timeframe),
    }


@app.post("/api/test-bench/run")
async def test_bench_run(payload: TestBenchPayload, request: Request):
    """Replay one mother candle, or hand back the run already stored for it.

    Every distinct question -- instrument, strategy, timeframe, mother,
    rupees per level, ITM steps -- is stored once.  Asking it again returns
    the stored answer with ``duplicate: true`` rather than spending another
    minute of Dhan and Upstox calls on a result that cannot have changed.
    ``force`` replays anyway and overwrites, which is what a run that had
    premium gaps at the time needs.
    """

    user_id = _request_user_id(request)
    query_key = _db_mod.test_bench_query_key(
        instrument=payload.instrument,
        strategy=payload.strategy,
        timeframe=payload.timeframe,
        mother_timestamp=payload.mother_timestamp,
        rung_inr=payload.rung_inr,
        itm_steps=payload.itm_steps,
    )
    if not payload.force:
        stored = await _db_mod.find_test_bench_run(user_id, query_key)
        if stored and isinstance(stored.get("payload"), dict) and stored["payload"]:
            return {
                **stored["payload"],
                "duplicate": True,
                "run_id": stored["id"],
                "stored_at": stored["created_at"],
            }

    result = await _test_bench_execute(payload, request)
    summary = result.get("summary") or {}
    run_id = await _db_mod.save_test_bench_run(
        user_id,
        query_key,
        {
            **summary,
            "strategy": result.get("strategy") or payload.strategy,
            "rung_inr": payload.rung_inr,
            "itm_steps": payload.itm_steps,
        },
        result,
    )
    return {**result, "duplicate": False, "run_id": run_id}


@app.get("/api/test-bench/results")
async def test_bench_results(request: Request, search: str = "", page: int = 1, per_page: int = 10):
    """One page of stored Test Bench runs, newest mother first."""
    return {
        "status": "ok",
        **(await _db_mod.list_test_bench_runs(_request_user_id(request), search=search, page=page, per_page=per_page)),
    }


@app.get("/api/test-bench/results/{run_id}")
async def test_bench_result(run_id: int, request: Request):
    """Reopen one stored run in full, without replaying it."""
    stored = await _db_mod.get_test_bench_run(_request_user_id(request), run_id)
    if not stored:
        raise HTTPException(status_code=404, detail="That saved run no longer exists.")
    payload = stored.get("payload") or {}
    return {**payload, "duplicate": True, "run_id": stored["id"], "stored_at": stored["created_at"]}


@app.delete("/api/test-bench/results/{run_id}")
async def test_bench_result_delete(run_id: int, request: Request):
    if not await _db_mod.delete_test_bench_run(_request_user_id(request), run_id):
        raise HTTPException(status_code=404, detail="That saved run no longer exists.")
    return {"status": "ok", "deleted": run_id}


async def _test_bench_execute(payload: TestBenchPayload, request: Request):
    """Replay ONE mother candle and report everything that happened to it.

    This is the Test Bench: the caller names an instrument, a strategy, a
    timeframe and a mother candle, and gets back the trade in full -- when it
    entered, when it left, whether the target or the expiry ended it, which
    strike it bought, at what premium, and what each level cost -- with the
    geometry drawn on a chart.

    Three things make it different from the older fib backtest it grew out of:

    * the mother's high and low are **read from the market**, never typed. A
      mother Dhan cannot produce is an error, not something to work around;
    * it is **one trade**.  ``max_rounds=1`` stops the new-low re-arm, so the
      mother buys its levels, rides to target or expiry, and is finished;
    * the lot size comes from the **effective-dated** table, so a 2025 mother is
      sized at the lot that was real in 2025 rather than today's.
    """

    from engine.backtest import get_lot_size as dated_lot_size
    from engine.backtest import get_strike_step as dated_strike_step
    from engine.cascade_instruments import InstrumentError, index_spec
    from engine.cascade_instruments import premium_key as upstox_key_for
    from engine.test_bench import CONTRACT_WINDOWS, bench_chart, bench_summary

    strategy = str(payload.strategy).strip().lower()
    if strategy not in {"fib", "two_red"}:
        raise HTTPException(status_code=400, detail="strategy must be 'fib' or 'two_red'.")
    side = str(payload.side).upper()
    if side != "CE":
        raise HTTPException(status_code=400, detail="The Test Bench replays CE campaigns only for now.")
    timeframe = str(payload.timeframe).lower()
    if timeframe not in _FIB_TIMEFRAME_MINUTES:
        raise HTTPException(status_code=400, detail="timeframe must be 1m, 5m, 15m or 1h.")

    try:
        instrument = index_spec(payload.instrument).symbol
        underlying_key = upstox_key_for(instrument)
    except InstrumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    window = CONTRACT_WINDOWS.get(instrument)
    if window is None:
        raise HTTPException(status_code=400, detail=f"No contract window is defined for {instrument} yet.")

    mother_timestamp = _parse_cascade_mother_timestamp(payload.mother_timestamp)
    now = datetime.now(IST)
    if mother_timestamp.date() > now.date():
        raise HTTPException(status_code=400, detail="Mother timestamp cannot be in the future (IST).")
    if not (dt_time(9, 15) <= mother_timestamp.time() <= dt_time(15, 30)):
        raise HTTPException(status_code=400, detail="Mother candle must be within the NSE 09:15–15:30 session.")

    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(status_code=400, detail=f"Connect a Dhan account to load the {instrument} index candles.")

    adapter = CascadeOptionsAdapter(broker_client, paper_only=True)
    horizon_to = min(now.date(), mother_timestamp.date() + timedelta(days=window.horizon_days))
    try:
        index_candles = await adapter.async_get_candles(
            instrument, timeframe, from_date=mother_timestamp.date(), to_date=horizon_to
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Unable to load {instrument} {timeframe} candles: {exc}") from exc

    # No typed fallback.  If the exact bar is not in what Dhan returned, the
    # mother is wrong (a holiday, a misaligned minute, a date before the data
    # starts) and saying so beats inventing a candle around a guess.
    mother = next((row for row in index_candles if row.timestamp == mother_timestamp), None)
    if mother is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Dhan has no {instrument} {timeframe} candle opening at "
                f"{mother_timestamp.strftime('%d %b %Y %H:%M')} IST. Check the date, the time and the timeframe."
            ),
        )
    forward = [row for row in index_candles if row.timestamp > mother_timestamp]
    if not forward:
        raise HTTPException(status_code=400, detail=f"No {instrument} candles after that mother.")

    lot_size = int(dated_lot_size(instrument, mother_timestamp.date()))
    strike_step = float(dated_strike_step(instrument))
    resolver_config = CascadeConfig(
        mother_timestamp=mother_timestamp,
        mother_high=mother.high,
        mother_low=mother.low,
        option_type=side,
        timeframe=timeframe,
        itm_steps=int(payload.itm_steps),
        strike_step=strike_step,
        lot_size=lot_size,
        min_dte=window.min_dte,
        max_dte=window.max_dte,
    )
    if strategy == "two_red":
        return await _test_bench_two_red(
            instrument=instrument,
            timeframe=timeframe,
            side=side,
            mother=mother,
            mother_timestamp=mother_timestamp,
            horizon_to=horizon_to,
            adapter=adapter,
            resolver_config=resolver_config,
            underlying_key=underlying_key,
            lot_size=lot_size,
            strike_step=strike_step,
            window=window,
        )

    # Phil's timeframe rule: a 1m or 5m mother buys only the two deepest lines,
    # because a shallow bounce on a fast chart is noise; 15m and 1h are
    # structural enough to start one level earlier.
    fib_levels = boundaries_for_timeframe(timeframe)

    def _run() -> dict:
        from data.cascade_upstox import UpstoxAccessError, UpstoxPremiumSource

        try:
            from upstox_token_manager import ensure_fresh_token

            ensure_fresh_token()
        except Exception as exc:  # a dead refresher must not mask the real error
            _logger.warning("[test-bench] Upstox token pre-check skipped: %s", exc)

        # Upstox only prices EXPIRED contracts, and a recent mother buys one
        # that is still trading — that one Dhan prices from its own candles.
        # Union the expiry calendars, split the pricing per contract, and no
        # mother is ever too recent to run.
        upstox_expiries: list[date] = []
        premium_source = None
        try:
            premium_source = UpstoxPremiumSource(underlying_key=underlying_key, backfill_missing=True)
            upstox_expiries = sorted(premium_source.available_expiries())
        except UpstoxAccessError as exc:
            _logger.warning("[test-bench] Upstox premium history unavailable, using Dhan only: %s", exc)

        expiries = _known_option_expiries(instrument, upstox_expiries)
        if not expiries:
            raise HTTPException(
                status_code=503,
                detail=f"Neither Upstox nor Dhan could name an option expiry for {instrument}.",
            )

        resolver = NiftyContractResolver(
            expiries=expiries, strike_step=strike_step, lot_size=lot_size, symbol=instrument
        )

        def select(_timestamp, index_price) -> FixedCampaignOption:
            # The strike follows the index down at each fill, but the EXPIRY is
            # fixed at the mother's -- re-resolving it per fill would drift the
            # contract as the campaign ages and eventually land on one Upstox
            # never priced.
            contract = resolver.select(mother.timestamp, index_price, side, resolver_config)
            return FixedCampaignOption(
                instrument, int(contract.strike), contract.expiry, contract.option_type, int(contract.lot_size), ""
            )

        premium_lookup = _hybrid_premium_lookup(
            broker_client,
            instrument,
            premium_source,
            set(upstox_expiries),
            mother_timestamp.date(),
            horizon_to,
            forward_minutes=_FIB_TIMEFRAME_MINUTES.get(timeframe, 5),
        )

        try:
            initial = select(mother.timestamp, mother.close)
        except Exception as exc:
            # A 400, not a 503: nothing is down. This mother has no contract, and
            # a 503 gets rewritten into "temporarily offline" before it reaches
            # the screen, hiding the only sentence worth reading.
            raise HTTPException(
                status_code=400, detail=f"No {side} contract could be bought at that mother: {exc}"
            ) from exc

        # The trade ends at expiry, so the replay does too.  Past that date the
        # contract does not exist: every further fib crossing would try to buy an
        # option nobody can price, and the run would end up reported as "awaiting
        # a quote" when what actually happened is that the mother expired unused.
        replay = [row for row in forward if row.timestamp.date() <= initial.expiry]

        engine = NiftyOptionsPaperCascade(
            mother,
            initial,
            CascadeOptionsAdapter(broker_client, paper_only=True),
            premium_lookup,
            PaperCascadeConfig(
                rung_inr=float(payload.rung_inr),
                ce_offset_steps=-int(payload.itm_steps),
                target_fraction=0.25,
                lot_ladder=True,
                per_entry_strike=True,
                fib_levels=fib_levels,
                # One mother, one trade.  No re-arm on a new low.
                max_rounds=1,
            ),
            contract_selector=select,
        ).run(replay)
        result = _serialize_cascade_backtest(engine)
        result["geometry"] = _serialize_cascade_geometry(engine, mother, replay)
        result["expiry"] = initial.expiry.isoformat()
        result["candles_replayed"] = len(replay)
        return result

    try:
        outcome = await asyncio.to_thread(_run)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Test Bench run failed: {exc}") from exc

    geometry = outcome.pop("geometry", {})
    summary = bench_summary(
        outcome, instrument=instrument, timeframe=timeframe, mother_timestamp=mother_timestamp.isoformat()
    )
    return {
        "status": "ok",
        "strategy": "fib",
        "summary": summary,
        "mother": {"timestamp": mother_timestamp.isoformat(), "high": mother.high, "low": mother.low},
        "fib_levels": list(fib_levels),
        "lot_size": lot_size,
        "strike_step": strike_step,
        "candles_replayed": outcome.get("candles_replayed", 0),
        "expiry": outcome.get("expiry"),
        "horizon_to": horizon_to.isoformat(),
        "entries": outcome.get("entries") or [],
        "chart": bench_chart(geometry, outcome, timeframe=timeframe),
    }


@app.post("/api/cascade/paper/start")
async def cascade_paper_start(payload: CascadePaperStartPayload, request: Request):
    """Start a new current-session CE campaign.  This never calls Dhan order APIs."""

    mother_timestamp = _parse_cascade_mother_timestamp(payload.mother_timestamp)
    now = datetime.now(IST)
    if mother_timestamp.date() > now.date():
        raise HTTPException(
            status_code=400,
            detail="Mother timestamp cannot be in the future.",
        )
    if (now.date() - mother_timestamp.date()).days > 14:
        raise HTTPException(
            status_code=400,
            detail="Mother candle is outside the 14-day paper replay window. Use Signal Replay for older history.",
        )
    if (
        mother_timestamp + timedelta(minutes=5) > now
        or mother_timestamp.minute % 5
        or mother_timestamp.second
        or mother_timestamp.microsecond
    ):
        raise HTTPException(status_code=400, detail="Mother timestamp must be a completed NIFTY 5m candle open in IST.")
    if not (dt_time(9, 15) <= mother_timestamp.time() < dt_time(15, 30)):
        raise HTTPException(status_code=400, detail="Mother candle must be within the NSE 09:15–15:30 session.")
    user_id = _request_user_id(request)
    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(status_code=400, detail="Connect a Dhan account before starting a paper Cascade campaign.")
    old_runtime = await _restore_cascade_open_state(user_id, broker_client)
    if old_runtime is not None and old_runtime.running:
        raise HTTPException(
            status_code=409, detail="A paper Cascade campaign is already running. Stop it before replacing its mother."
        )
    adapter = CascadeOptionsAdapter(broker_client, paper_only=True)
    supplied_ohlc = [payload.mother_open, payload.mother_high, payload.mother_low, payload.mother_close]
    if any(value is not None for value in supplied_ohlc) and not all(value is not None for value in supplied_ohlc):
        raise HTTPException(
            status_code=400, detail="Enter all mother OHLC values, or leave all four blank to load the selected candle."
        )
    if all(value is None for value in supplied_ohlc):
        mother = await _load_cascade_mother_candle(adapter, mother_timestamp)
    else:
        assert payload.mother_open is not None and payload.mother_high is not None
        assert payload.mother_low is not None and payload.mother_close is not None
        if not (payload.mother_low <= payload.mother_open <= payload.mother_high):
            raise HTTPException(status_code=400, detail="Mother open must be within the entered high/low range.")
        if not (payload.mother_low <= payload.mother_close <= payload.mother_high):
            raise HTTPException(status_code=400, detail="Mother close must be within the entered high/low range.")
        if payload.mother_high <= payload.mother_low:
            raise HTTPException(status_code=400, detail="Mother high must exceed mother low.")
        mother = IndexCandle(
            timestamp=mother_timestamp,
            open=float(payload.mother_open),
            high=float(payload.mother_high),
            low=float(payload.mother_low),
            close=float(payload.mother_close),
        )
    try:
        contract = await asyncio.to_thread(
            adapter.select_campaign_contract,
            mother_spot=mother.close,
            selected_at=mother.timestamp,
            ce_offset_steps=payload.ce_offset_steps,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Unable to select the fixed next-weekly CE: {exc}") from exc
    engine = NiftyOptionsPaperCascade(
        mother,
        contract,
        adapter,
        _cascade_premium_lookup(broker_client),
        PaperCascadeConfig(rung_inr=payload.rung_inr, ce_offset_steps=payload.ce_offset_steps),
    )
    runtime = _CascadeRuntime(
        engine=engine, adapter=adapter, broker=broker_client, last_candle_timestamp=mother.timestamp
    )
    _cascade_engines[user_id] = runtime
    runtime.task = asyncio.create_task(_run_cascade_paper_loop(user_id, runtime))
    await _save_cascade_open_state(user_id, runtime, force=True)
    await _notify_cascade_ws(user_id)
    return {"status": "started", "mode": "paper", "campaign": {**engine.get_status(), "running": True}}


@app.post("/api/cascade/paper/stop")
async def cascade_paper_stop(request: Request):
    user_id = _request_user_id(request)
    runtime = _cascade_engines.get(user_id)
    if runtime is None:
        return {"status": "not_running"}
    runtime.running = False
    runtime.engine.status = "STOPPED"
    if runtime.task and not runtime.task.done():
        runtime.task.cancel()
    await _save_cascade_open_state(user_id, runtime, force=True)
    await _notify_cascade_ws(user_id)
    return {"status": "stopped", "mode": "paper"}


@app.post("/api/fib-space/paper/start")
async def fib_space_paper_start(request: Request):
    """Start the converging-fib space paper run for one underlying.

    Starting the run only opens the book; it takes no mother.  Name mothers with
    /api/fib-space/paper/mother, one per campaign -- that is the intended way in.
    Pass auto_scan to let the pivot scanner find its own instead, which is what
    the backtest did and the only way to compare a forward run to it like for
    like.  Paper only: there is no live counterpart to this route and the adapter
    refuses to build one.
    """
    body = await request.json() if await request.body() else {}
    symbol = str(body.get("symbol") or "banknifty").strip().lower()
    auto_scan = bool(body.get("auto_scan"))
    if symbol not in FIB_SPACE_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported underlying. Measured symbols: {', '.join(sorted(FIB_SPACE_SYMBOLS))}.",
        )

    user_id = _request_user_id(request)
    existing = _fib_space_engines.get(user_id)
    if existing is not None and existing.running:
        raise HTTPException(
            status_code=409,
            detail=f"A fib-space paper run is already going on {existing.symbol.upper()}. Stop it first.",
        )

    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(status_code=400, detail="Connect a Dhan account before starting a fib-space paper run.")

    adapter = CascadeOptionsAdapter(broker_client, paper_only=True)
    try:
        host = _build_fib_space_host(symbol, adapter, broker_client, auto_scan=auto_scan)
    except Exception as exc:
        # Usually a missing monthly contract or lot size in ScripMaster.  Refuse
        # rather than start a run whose every quantity is a guess.
        raise HTTPException(status_code=400, detail=f"Cannot size a {symbol.upper()} campaign: {exc}") from exc

    runtime = _FibSpaceRuntime(
        host=host,
        adapter=adapter,
        broker=broker_client,
        symbol=symbol,
        started_at=datetime.now(IST).replace(tzinfo=None),
    )
    _fib_space_engines[user_id] = runtime
    # Pick the named mothers back up BEFORE saving. This host's book is empty,
    # so saving first wrote `manual_mothers: []` over the record -- which is how
    # a run that was restarted (or whose restore failed on a deploy, leaving the
    # panel showing "not running") silently lost every campaign the trader had
    # started. Restoring here also makes Stop then Start non-destructive.
    readopted = await _readopt_saved_manual_mothers(user_id, host, symbol)
    runtime.task = asyncio.create_task(_run_fib_space_paper_loop(user_id, runtime))
    await _save_fib_space_state(user_id, runtime)
    if readopted:
        _logger.info("[FIBSPACE] Re-adopted %s named mother(s) on start for user %s", readopted, user_id)
    return {
        "status": "started",
        "readopted_mothers": readopted,
        "mode": "paper",
        "symbol": symbol,
        "lot_size": host.book.config.lot_size,
        "poll_seconds": FIB_SPACE_POLL_SECONDS,
        "auto_scan": host.auto_scan,
    }


# How far back a named mother may reach. A mother older than this has usually
# already had its ladder fill, and those fills cannot be quoted now -- a premium
# is a live number, so the driver records them unpriced rather than guessing.
_FIB_SPACE_MOTHER_HISTORY_DAYS = 15


@app.post("/api/fib-space/paper/mother")
async def fib_space_paper_mother(request: Request):
    """Run the fib-space design on a mother candle the trader names.

    This is the primary way in. The pivot scanner is what a backtest has to do
    because it cannot ask anybody; here there is somebody to ask, and the record
    says asking is better -- every rule measured so far reproduces trades from
    Phil's own charts and then loses money applied mechanically, because he
    selects among the mothers the geometry offers.

    Only the timestamp is taken. High and low come from the market bar, exactly
    as the fib-boundary tab and the Test Bench do.
    """
    body = await request.json() if await request.body() else {}
    # The parser hands back a tz-aware IST datetime; everything downstream here
    # -- the geometry bars, the driver, the campaign keys -- is naive IST, so it
    # is flattened once, at the boundary, rather than compared across the two.
    when = _parse_cascade_mother_timestamp(body.get("mother_timestamp")).replace(tzinfo=None)
    now = datetime.now(IST).replace(tzinfo=None)

    if when > now:
        raise HTTPException(status_code=400, detail="Mother timestamp cannot be in the future (IST).")
    if not (dt_time(9, 15) <= when.time() <= dt_time(15, 30)):
        raise HTTPException(status_code=400, detail="Mother candle must be within the NSE 09:15–15:30 session.")
    # Geometry runs on 15m, so a mother is a 15m candle open and nothing else.
    if when.minute % 15 or when.second or when.microsecond:
        raise HTTPException(
            status_code=400, detail="Mother must be a 15m candle open in IST — 09:15, 09:30, 09:45 and so on."
        )
    if when + timedelta(minutes=15) > now:
        raise HTTPException(status_code=400, detail="That 15m candle has not closed yet.")
    if (now.date() - when.date()).days > _FIB_SPACE_MOTHER_HISTORY_DAYS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Choose a mother from the last {_FIB_SPACE_MOTHER_HISTORY_DAYS} days. "
                "Older ladders have usually already filled, and those fills cannot be quoted now."
            ),
        )

    user_id = _request_user_id(request)
    runtime = _fib_space_engines.get(user_id)
    if runtime is None or not runtime.running:
        raise HTTPException(
            status_code=409, detail="Start the fib-space paper run first, then give it a mother candle."
        )

    try:
        campaign = await runtime.host.start_named_mother(when, now=now)
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:  # already running on this mother
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        # 400, deliberately, not 502.  error_handlers only passes the specific
        # reason through for 4xx -- a 5xx is rewritten to "The broker API is
        # temporarily unreachable", which hid a real bug here behind a message
        # that suggested waiting would fix it.  The user can act on the actual
        # text; a generic one wastes their time and mine.
        _logger.warning("[FIBSPACE] Candle fetch failed for mother %s: %s", when, exc, exc_info=True)
        raise HTTPException(status_code=400, detail=f"Could not read that candle from Dhan — {exc}") from exc

    # PERSIST IT NOW. The poll loop only saves when something CHANGED -- a fill,
    # an exit, a halt, or an auto-scanned mother -- so with auto-scan off a named
    # mother that had not filled yet was never written to disk at all, and the
    # next restart came back with an empty book. Name a mother in the evening and
    # it was gone by morning.
    await _save_fib_space_state(user_id, runtime)

    _logger.info(
        "[FIBSPACE] %s manual mother %s (high %.2f) accepted and saved for user %s",
        runtime.symbol,
        campaign.mother.timestamp,
        campaign.mother.high,
        user_id,
    )
    return {
        "status": "accepted",
        "mode": "paper",
        "symbol": runtime.symbol,
        "campaign_id": campaign.campaign_id,
        "mother": campaign.mother.timestamp.isoformat(),
        "mother_high": round(campaign.mother.high, 2),
        "mother_low": round(campaign.mother.low, 2),
    }


def _fib_space_campaign_or_404(request: Request, campaign_id: str):
    """The runtime and campaign behind an id, or the right refusal."""
    user_id = _request_user_id(request)
    runtime = _fib_space_engines.get(user_id)
    if runtime is None:
        raise HTTPException(status_code=409, detail="No fib-space paper run is going.")
    campaign = runtime.host.book.campaigns.get(str(campaign_id or "").strip())
    if campaign is None:
        raise HTTPException(status_code=404, detail="No such campaign in this run.")
    return runtime, campaign


@app.get("/api/fib-space/paper/campaign")
async def fib_space_paper_campaign(campaign_id: str, request: Request):
    """One campaign's money: every fill's premium, what it cost, what it is worth."""
    runtime, campaign = _fib_space_campaign_or_404(request, campaign_id)
    return {"status": "ok", "mode": "paper", "campaign": runtime.host.book.campaign_detail(campaign)}


@app.get("/api/fib-space/paper/chart")
async def fib_space_paper_chart(campaign_id: str, request: Request):
    """The campaign drawn: geometry, fills and target, for the shared renderer."""
    runtime, campaign = _fib_space_campaign_or_404(request, campaign_id)
    # A campaign that has not been polled yet still HAS geometry -- it just has
    # not been computed. Build it on demand rather than refusing, which is what
    # made a mother named after the close look broken all evening.
    try:
        await runtime.host.ensure_drawable(campaign, now=datetime.now(IST).replace(tzinfo=None))
    except Exception as exc:
        _logger.warning("[FIBSPACE] Could not build a chart for %s: %s", campaign.campaign_id, exc, exc_info=True)
        raise HTTPException(status_code=400, detail=f"Could not read candles for this chart — {exc}") from exc

    payload = runtime.host.book.campaign_chart(campaign)
    if payload.get("status") != "ok":
        raise HTTPException(status_code=409, detail=payload.get("reason") or "This campaign has nothing to draw yet.")
    return payload


@app.post("/api/fib-space/paper/stop")
async def fib_space_paper_stop(request: Request):
    user_id = _request_user_id(request)
    runtime = _fib_space_engines.get(user_id)
    if runtime is None:
        return {"status": "not_running"}
    runtime.running = False
    if runtime.task and not runtime.task.done():
        runtime.task.cancel()
    await _save_fib_space_state(user_id, runtime)
    return {"status": "stopped", "mode": "paper", "symbol": runtime.symbol}


@app.get("/api/fib-space/paper/status")
async def fib_space_paper_status(request: Request):
    user_id = _request_user_id(request)
    runtime = _fib_space_engines.get(user_id)
    if runtime is None:
        return {"status": "not_started", "mode": "paper"}
    return _fib_space_status_payload(runtime)


# ── Candle-entry recovery routes ──────────────────────────────────
# Paper only. The adapter is constructed paper_only and there is no live
# counterpart to any of these -- no code path here can submit a Dhan order.


@app.post("/api/recovery/paper/start")
async def recovery_paper_start(request: Request):
    """Start a recovery paper run. Mothers are named afterwards, one per campaign."""
    body = await request.json() if await request.body() else {}
    symbol = str(body.get("symbol") or "nifty").strip().lower()
    timeframe = str(body.get("timeframe") or "15m").strip().lower()
    mode = str(body.get("mode") or "ladder").strip().lower()
    if symbol not in RECOVERY_SYMBOLS:
        raise HTTPException(status_code=400, detail=f"Measured symbols: {', '.join(sorted(RECOVERY_SYMBOLS))}.")
    if timeframe not in RECOVERY_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Timeframe must be one of {', '.join(RECOVERY_TIMEFRAMES)}.")
    if mode not in RECOVERY_MODES:
        raise HTTPException(status_code=400, detail=f"Mode must be one of {', '.join(RECOVERY_MODES)}.")

    user_id = _request_user_id(request)
    existing = _recovery_engines.get(user_id)
    if existing is not None and existing.running:
        raise HTTPException(
            status_code=409,
            detail=f"A recovery run is already going on {existing.symbol.upper()}. Stop it first.",
        )
    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(status_code=400, detail="Connect a Dhan account before starting a recovery run.")

    overrides = {}
    if body.get("lots_schedule"):
        overrides["lots_schedule"] = [int(x) for x in body["lots_schedule"]]
    for key in ("min_profit_inr", "horizon_sessions"):
        if body.get(key) is not None:
            overrides[key] = body[key]
    if body.get("sl_source"):
        overrides["sl_source"] = str(body["sl_source"])

    adapter = CascadeOptionsAdapter(broker_client, paper_only=True)
    try:
        host = _build_recovery_host(
            symbol, adapter, broker_client, timeframe=timeframe, mode=mode, config_overrides=overrides
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cannot size a {symbol.upper()} campaign: {exc}") from exc

    runtime = _RecoveryRuntime(
        host=host,
        adapter=adapter,
        broker=broker_client,
        symbol=symbol,
        started_at=datetime.now(IST).replace(tzinfo=None),
    )
    _recovery_engines[user_id] = runtime
    # Pick the named mothers back up BEFORE saving, so a stop/start or a restore
    # that failed on a deploy cannot write an empty book over the record.
    readopted = await _readopt_recovery_mothers(user_id, host, symbol)
    runtime.task = asyncio.create_task(_run_recovery_loop(user_id, runtime))
    await _save_recovery_state(user_id, runtime)
    return {
        "status": "started",
        "mode": "paper",
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_mode": mode,
        "lot_size": host.lot_size,
        "poll_seconds": RECOVERY_POLL_SECONDS,
        "readopted_mothers": readopted,
    }


@app.post("/api/recovery/paper/mother")
async def recovery_paper_mother(request: Request):
    """Run the recovery rules on a mother candle the trader names.

    Only the timestamp is taken; the high and low come from the market bar.
    """
    body = await request.json() if await request.body() else {}
    user_id = _request_user_id(request)
    runtime = _recovery_engines.get(user_id)
    if runtime is None or not runtime.running:
        raise HTTPException(status_code=409, detail="Start the recovery run first, then give it a mother candle.")

    when = _parse_cascade_mother_timestamp(body.get("mother_timestamp")).replace(tzinfo=None)
    now = datetime.now(IST).replace(tzinfo=None)
    if when > now:
        raise HTTPException(status_code=400, detail="Mother timestamp cannot be in the future (IST).")
    if not (dt_time(9, 15) <= when.time() <= dt_time(15, 30)):
        raise HTTPException(status_code=400, detail="Mother candle must be within the NSE 09:15-15:30 session.")

    try:
        campaign = await runtime.host.start_named_mother(when, now=now)
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        # 400 deliberately: error_handlers only passes the real reason through
        # for 4xx, and a 5xx is rewritten to "the broker is unreachable".
        _logger.warning("[RECOVERY] mother %s failed: %s", when, exc, exc_info=True)
        raise HTTPException(status_code=400, detail=f"Could not read that candle from Dhan - {exc}") from exc

    # PERSIST IT NOW, not on the next change -- a named mother that has not
    # filled yet is exactly the thing a restart would otherwise lose.
    await _save_recovery_state(user_id, runtime)
    _logger.info(
        "[RECOVERY] %s mother %s (high %.2f) accepted and saved for user %s",
        runtime.symbol,
        campaign.mother.timestamp,
        campaign.mother.high,
        user_id,
    )
    return {
        "status": "accepted",
        "mode": "paper",
        "campaign_id": campaign.campaign_id,
        "mother": campaign.mother.timestamp.isoformat(),
        "mother_high": round(campaign.mother.high, 2),
        "mother_low": round(campaign.mother.low, 2),
        "campaign": runtime.host.campaign_row(campaign),
    }


@app.post("/api/recovery/paper/drop")
async def recovery_paper_drop(request: Request):
    """Remove one campaign from the book. Paper: nothing is sold, it stops being tracked."""
    body = await request.json() if await request.body() else {}
    user_id = _request_user_id(request)
    runtime = _recovery_engines.get(user_id)
    if runtime is None:
        return {"status": "not_running"}
    campaign_id = str(body.get("campaign_id") or "")
    if not runtime.host.drop(campaign_id):
        raise HTTPException(status_code=404, detail="No such campaign in this run.")
    await _save_recovery_state(user_id, runtime)
    return {"status": "dropped", "campaign_id": campaign_id}


@app.post("/api/recovery/paper/stop")
async def recovery_paper_stop(request: Request):
    user_id = _request_user_id(request)
    runtime = _recovery_engines.get(user_id)
    if runtime is None:
        return {"status": "not_running"}
    runtime.running = False
    if runtime.task and not runtime.task.done():
        runtime.task.cancel()
    await _save_recovery_state(user_id, runtime)
    return {"status": "stopped", "mode": "paper", "symbol": runtime.symbol}


@app.get("/api/recovery/paper/status")
async def recovery_paper_status(request: Request):
    user_id = _request_user_id(request)
    runtime = _recovery_engines.get(user_id)
    if runtime is None:
        return {"status": "not_started", "mode": "paper"}
    return _recovery_status_payload(runtime)


@app.post("/api/cascade/paper/kill")
async def cascade_paper_kill(request: Request):
    """Paper-only emergency close: cancel rungs and close the paper basket."""

    user_id = _request_user_id(request)
    _user, broker_client, _source = await _request_broker_context(request)
    runtime = await _restore_cascade_open_state(user_id, broker_client)
    if runtime is None:
        raise HTTPException(status_code=404, detail="No paper Cascade campaign is active.")
    now = datetime.now(IST)
    try:
        ticker = await asyncio.to_thread(runtime.adapter.get_ticker, "NIFTY")
        index_price = float(ticker["last_price"])
    except Exception:
        # The option exit itself still requires a fresh quote.  This fallback
        # only lets us cancel unfunded rungs and preserves an honest response
        # if Dhan's index quote is momentarily unavailable.
        index_price = float(runtime.engine.geometry.history[-1].close)
    kill_candle = IndexCandle(now, index_price, index_price, index_price, index_price)
    result = runtime.engine.kill_and_close(kill_candle)
    if not result["closed"]:
        await _save_cascade_open_state(user_id, runtime, force=True)
        await _notify_cascade_ws(user_id)
        raise HTTPException(
            status_code=409,
            detail="Pending paper rungs were cancelled, but Dhan did not provide a current option quote. "
            "The open paper basket remains monitored; retry Kill & close when a quote is available.",
        )
    runtime.running = False
    if runtime.task and not runtime.task.done():
        runtime.task.cancel()
    await _save_cascade_open_state(user_id, runtime, force=True)
    await _notify_cascade_ws(user_id)
    return {
        "status": "killed",
        "mode": "paper",
        "cancelled_rungs": result["cancelled_rungs"],
        "campaign": {**runtime.engine.get_status(), "running": False},
    }


@app.delete("/api/cascade/paper")
async def cascade_paper_delete(request: Request):
    user_id = _request_user_id(request)
    runtime = _cascade_engines.pop(user_id, None)
    if runtime is not None:
        runtime.running = False
        if runtime.task and not runtime.task.done():
            runtime.task.cancel()
    await _db_mod.set_app_state(_cascade_open_state_key(user_id), "")
    return {"status": "deleted"}


@app.get("/api/cascade/live-gate")
async def cascade_live_gate(request: Request):
    # Request authentication is intentionally retained even though this is
    # read-only: it prevents exposing a trader's deployment posture publicly.
    _request_user_id(request)
    return _cascade_live_gate_status()


# ── Backtest ──────────────────────────────────────────────────────
@app.post("/api/backtest")
async def api_run_backtest(payload: StrategyPayload, request: Request):
    try:
        from_date = payload.from_date or config.DEFAULT_FROM
        to_date = payload.to_date or config.DEFAULT_TO
        entry_conditions = payload.entry_conditions or DEFAULT_ENTRY_CONDITIONS
        exit_conditions = payload.exit_conditions or DEFAULT_EXIT_CONDITIONS
        contract = validate_strategy_contract(payload.indicators, entry_conditions, exit_conditions)
        normalized_indicators = contract["normalized_indicators"]
        if contract["errors"]:
            return {
                "status": "error",
                "message": "Strategy validation failed:\n- " + "\n- ".join(contract["errors"]),
                "validation": contract,
            }

        execution_timeframe = infer_execution_timeframe(normalized_indicators, entry_conditions, default=5)
        try:
            tf_spec = resolve_strategy_timeframe(
                normalized_indicators,
                default=execution_timeframe,
                execution_hint=execution_timeframe,
            )
        except ValueError as tf_err:
            return {"status": "error", "message": str(tf_err)}
        candle_interval = str(tf_spec.fetch)

        _logger.info(
            "[BACKTEST] Validated request timeframe=%s indicator_count=%s entry_condition_count=%s "
            "exit_condition_count=%s leg_count=%s",
            describe_timeframe(tf_spec),
            len(normalized_indicators),
            len(entry_conditions),
            len(exit_conditions),
            len(payload.legs or []),
        )

        # 1. Fetch data with segment-aware routing + fallback
        print(f"[BACKTEST] Fetching data from {from_date} to {to_date}...")
        try:
            df_raw = await asyncio.to_thread(
                _fetch_data,
                instrument=payload.instrument,
                from_date=from_date,
                to_date=to_date,
                segment=payload.segment,
                candle_interval=candle_interval,
            )
        except Exception as fetch_err:
            error_msg = f"Data fetch failed: {str(fetch_err)}"
            print(f"[BACKTEST] {error_msg}")
            return {"status": "error", "message": error_msg}

        if df_raw is None or df_raw.empty:
            error_msg = "No data returned. Check credentials and date range."
            print(f"[BACKTEST] {error_msg}")
            return {"status": "error", "message": error_msg}

        print(f"[BACKTEST] Data: {len(df_raw)} candles, {df_raw.index[0]} → {df_raw.index[-1]}")

        # Warn if actual data range is shorter than requested, or if using daily candles
        data_range_warning = None
        timeframe_warning = derived_timeframe_warning(tf_spec)
        from datetime import datetime as _dtw

        _from_dt = _dtw.strptime(from_date, "%Y-%m-%d")
        _to_dt = _dtw.strptime(to_date, "%Y-%m-%d")
        _day_span = (_to_dt - _from_dt).days
        if _day_span > INTRADAY_MAX_DAYS:
            data_range_warning = (
                f"📊 Date range is {_day_span} days — automatically using DAILY candles "
                f"for full {from_date} → {to_date} coverage. "
                f"(Dhan intraday history is limited to about 5 years. Daily candles go back further.)"
            )
            print(f"[BACKTEST] {data_range_warning}")
        else:
            actual_start = (
                str(df_raw.index[0].date()) if hasattr(df_raw.index[0], "date") else str(df_raw.index[0])[:10]
            )
            if actual_start > from_date:
                data_range_warning = (
                    f"⚠️ Data starts from {actual_start} (requested {from_date}). "
                    f"Some data may not be available for the requested period."
                )
                print(f"[BACKTEST] {data_range_warning}")

        # 2. Build strategy_config
        strategy_config = payload.model_dump()
        strategy_config["indicators"] = normalized_indicators
        strategy_config["timeframe_minutes"] = tf_spec.requested
        strategy_config["fetch_timeframe_minutes"] = tf_spec.fetch
        requested_days = max(1, (_to_dt - _from_dt).days + 1)
        strategy_config["allow_synthetic_option_fallback"] = requested_days >= _OPTION_REAL_DATA_MAX_DAYS
        # Runs off the event loop: this fetch is synchronous, network-bound and
        # throttles itself with time.sleep between Dhan chunks. Left inline it
        # stalls every live/paper engine task and the /ws stream for its whole
        # duration, since they all share this loop and the app runs one worker.
        option_pricing = await asyncio.to_thread(
            _fetch_backtest_option_histories, strategy_config, tf_spec, from_date, to_date
        )
        if option_pricing["errors"]:
            error_msg = "Historical option data unavailable for this backtest:\n- " + "\n- ".join(
                option_pricing["errors"]
            )
            print(f"[BACKTEST] ❌ {error_msg}")
            return {
                "status": "error",
                "message": error_msg,
                "option_pricing": option_pricing,
            }
        if option_pricing["historical_legs"] > 0:
            print(
                f"[BACKTEST] Option pricing: stored/historical candles for {option_pricing['historical_legs']} leg(s)"
            )
        elif any((leg or {}).get("option_type") in ("CE", "PE") for leg in (payload.legs or [])):
            if strategy_config["allow_synthetic_option_fallback"]:
                print(
                    f"[BACKTEST] ⚠️  Option pricing: synthetic-only by range rule "
                    f"({requested_days} days >= {_OPTION_REAL_DATA_MAX_DAYS})"
                )
            else:
                print("[BACKTEST] ⚠️  Option pricing: no usable historical option data")
        for warning in option_pricing["warnings"]:
            print(f"[BACKTEST] ⚠️  {warning}")

        # 3. Run backtest
        print("[BACKTEST] Running backtest engine...")
        try:
            results = await asyncio.to_thread(
                run_backtest,
                df_raw=df_raw,
                entry_conditions=entry_conditions,
                exit_conditions=exit_conditions,
                strategy_config=strategy_config,
            )
        except Exception as bt_err:
            error_msg = f"Backtest execution failed: {str(bt_err)}"
            print(f"[BACKTEST] {error_msg}")
            import traceback

            traceback.print_exc()
            return {"status": "error", "message": error_msg}

        print(f"[BACKTEST] Result: {results.get('status')}, Trades: {results.get('stats', {}).get('total_trades', 0)}")

        # Save the run
        if results.get("status") == "success":
            run_entry = {
                "mode": "backtest",
                "run_name": payload.run_name,
                "folder": payload.folder,
                "segment": payload.segment,
                "instrument": payload.instrument,
                "from_date": from_date,
                "to_date": to_date,
                "lots": payload.lots,
                "lot_size": payload.lot_size,
                "stoploss_pct": payload.stoploss_pct,
                "stoploss_rupees": getattr(payload, "stoploss_rupees", 0),
                "sl_type": getattr(payload, "sl_type", "pct"),
                "target_profit_pct": getattr(payload, "target_profit_pct", 0),
                "target_profit_rupees": getattr(payload, "target_profit_rupees", 0),
                "tp_type": getattr(payload, "tp_type", "pct"),
                "indicators": normalized_indicators,
                "entry_conditions": entry_conditions,
                "exit_conditions": exit_conditions,
                "legs": payload.legs,
                "market_open": getattr(payload, "market_open", "09:15") or "09:15",
                "market_close": getattr(payload, "market_close", "15:25") or "15:25",
                "max_trades_per_day": getattr(payload, "max_trades_per_day", 1),
                "max_daily_loss": getattr(payload, "max_daily_loss", 0),
                "initial_capital": getattr(payload, "initial_capital", config.DEFAULT_CAPITAL),
                "combined_sl_rupees": getattr(payload, "combined_sl_rupees", 0),
                "combined_target_rupees": getattr(payload, "combined_target_rupees", 0),
                "combined_sqoff_time": getattr(payload, "combined_sqoff_time", "15:20") or "15:20",
                "fee_pct": getattr(payload, "fee_pct", 0.0),
                "trailing_sl_pct": getattr(payload, "trailing_sl_pct", 0.0),
                "execution_profile": getattr(payload, "execution_profile", "auto"),
                "spread_bps": getattr(payload, "spread_bps", 0.0),
                "entry_slippage_bps": getattr(payload, "entry_slippage_bps", 0.0),
                "exit_slippage_bps": getattr(payload, "exit_slippage_bps", 0.0),
                "entry_delay_candles": getattr(payload, "entry_delay_candles", 0),
                "signal_exit_delay_candles": getattr(payload, "signal_exit_delay_candles", 0),
                "enforce_capital": getattr(payload, "enforce_capital", False),
                "capital_buffer_pct": getattr(payload, "capital_buffer_pct", 0.0),
                "sell_option_margin_per_lot": getattr(payload, "sell_option_margin_per_lot", 0.0),
                "deploy_config": getattr(payload, "deploy_config", None),
                "option_pricing": {
                    "historical_legs": option_pricing["historical_legs"],
                    "synthetic_legs": option_pricing["synthetic_legs"],
                },
                "option_pricing_warnings": option_pricing["warnings"],
                "stats": results["stats"],
                "monthly": results.get("monthly", []),
                "day_of_week": results.get("day_of_week", []),
                "yearly": results.get("yearly", []),
                "trade_count": results["stats"]["total_trades"],
                "total_pnl": results["stats"]["total_pnl"],
                "created_at": str(datetime.now()),
            }
            # Store all trades (no need to trim)
            all_trades = results.get("trades", [])
            run_entry["trades"] = all_trades
            run_entry["equity"] = results.get("equity", [])
            saved_run = await _db_mod.create_run_record(_request_user_id(request), run_entry)
            results["run_id"] = saved_run["id"]
            print(f"[BACKTEST] Saved as Run #{saved_run['id']}")

        if data_range_warning:
            results["data_range_warning"] = data_range_warning
        if timeframe_warning:
            results["timeframe_warning"] = timeframe_warning
        if option_pricing["warnings"]:
            results["option_pricing_warnings"] = option_pricing["warnings"]
        results["option_pricing"] = {
            "historical_legs": option_pricing["historical_legs"],
            "synthetic_legs": option_pricing["synthetic_legs"],
        }
        results["timeframe_info"] = {
            "requested_minutes": tf_spec.requested,
            "fetch_minutes": tf_spec.fetch,
            "derived": tf_spec.derived,
            "all_frames": list(tf_spec.all_frames),
        }

        return results

    except Exception as e:
        import traceback

        error_msg = f"Backtest failed: {str(e)}"
        print(f"[BACKTEST] FATAL ERROR: {error_msg}")
        traceback.print_exc()
        return {"status": "error", "message": error_msg, "details": str(e)}


# ── Live Engine ───────────────────────────────────────────────────
@app.post("/api/live/start")
async def live_start(req: LiveStartRequest, request: Request):
    """Start live auto-trading with full strategy configuration."""
    user_id = _request_user_id(request)
    user = getattr(request.state, "current_user", None) or await _auth_mod.get_current_user(request)
    broker_client, broker_source = _resolve_user_broker_client(user, allow_admin_fallback=True)
    if not broker_client:
        return {"status": "error", "message": _broker_not_configured_message(user, broker_source)}
    live_bucket = _registry_bucket(live_engines, user_id)
    live_task_bucket = _registry_bucket(_live_tasks, user_id)
    stopped_engines = _load_stopped_engines(user_id)
    # Build strategy dict from the request
    strategy_dict = {}
    if req.strategy_config:
        strategy_dict = dict(req.strategy_config)
    else:
        strategy_dict = {
            "strategy_id": int(req.strategy_id or 0),
            "run_name": req.run_name or "Live Strategy",
            "instrument": req.instrument or "26000",
            "indicators": req.indicators or [],
            "max_trades_per_day": int(req.max_trades_per_day or 1),
            "market_open": req.market_open or "09:15",
            "market_close": req.market_close or "15:25",
            "legs": req.legs or [],
            "deploy_config": req.deploy_config or {},
            "max_daily_loss": float(req.max_daily_loss or 0),
            "lots": req.lots,
            "stoploss_pct": req.stoploss_pct,
            "stoploss_rupees": req.stoploss_rupees,
            "sl_type": req.sl_type,
            "target_profit_pct": req.target_profit_pct,
            "target_profit_rupees": req.target_profit_rupees,
            "tp_type": req.tp_type,
            "initial_capital": req.initial_capital,
            "execution_profile": req.execution_profile,
            "enforce_capital": req.enforce_capital,
            "capital_buffer_pct": req.capital_buffer_pct,
            "sell_option_margin_per_lot": req.sell_option_margin_per_lot,
            "poll_interval": 10,
        }
    entry_conditions, exit_conditions = (
        req.entry_conditions or DEFAULT_ENTRY_CONDITIONS,
        req.exit_conditions or DEFAULT_EXIT_CONDITIONS,
    )
    contract = validate_strategy_contract(
        strategy_dict.get("indicators", req.indicators), entry_conditions, exit_conditions
    )
    if contract["errors"]:
        return {
            "status": "error",
            "message": "Strategy validation failed:\n- " + "\n- ".join(contract["errors"]),
            "validation": contract,
        }
    strategy_dict["indicators"] = contract["normalized_indicators"]
    execution_timeframe = infer_execution_timeframe(strategy_dict["indicators"], entry_conditions, default=5)
    try:
        tf_spec = resolve_strategy_timeframe(
            strategy_dict["indicators"],
            default=execution_timeframe,
            execution_hint=execution_timeframe,
        )
    except ValueError as tf_err:
        return {"status": "error", "message": str(tf_err)}
    strategy_dict["strategy_id"] = int(strategy_dict.get("strategy_id") or req.strategy_id or 0)
    strategy_dict["timeframe_minutes"] = tf_spec.requested
    strategy_dict["_user_id"] = user_id
    strategy_dict["fetch_timeframe_minutes"] = tf_spec.fetch

    await _sync_saved_strategy_from_runtime(
        user_id,
        strategy_dict.get("strategy_id", 0),
        dict(req.strategy_config or strategy_dict),
        entry_conditions,
        exit_conditions,
        source_label="live start",
    )

    deploy_config = req.deploy_config or strategy_dict.get("deploy_config", {})

    # Generate run_id from strategy name
    run_id = strategy_dict.get("run_name", "live") or "live"

    # Clear only the stopped snapshot for this exact engine identity
    stopped_engines.pop(_engine_snapshot_key(run_id, "auto"), None)
    _save_stopped_engines(user_id)

    # If an engine with same run_id exists, save its results before replacing
    old_engine = live_bucket.get(run_id)
    if old_engine:
        if old_engine.running and getattr(old_engine, "positions", None):
            return {
                "status": "error",
                "message": "An active live engine with open broker positions already exists for this run. Stop it and wait for square-off before redeploying.",
            }
        try:
            old_status = old_engine.get_status()
            if old_engine.running:
                old_engine.stop()
                task = live_task_bucket.pop(run_id, None)
                if task and not task.done():
                    task.cancel()
            await _save_live_run_to_history(old_status, explicit_user_id=getattr(old_engine, "_user_id", None))
        except Exception as e:
            print(f"[LIVE] Failed to save old engine {run_id}: {e}")
        live_bucket.pop(run_id, None)

    # Create a new engine instance for this strategy
    engine = LiveEngine(broker_client, run_id=run_id, state_dir=_engine_state_dir(user_id))
    engine.configure(
        strategy=strategy_dict,
        entry_conditions=entry_conditions,
        exit_conditions=exit_conditions,
        deploy_config=deploy_config,
    )
    engine._user_id = user_id

    # Inject WebSocket feed if available — starts WS + subscribes index
    if _market_feed and HAS_DHAN_FEED:
        instrument = strategy_dict.get("instrument", "26000")
        _market_feed.subscribe_index(instrument)
        if not _market_feed.is_running:
            _market_feed.start()
        engine.set_feed(_market_feed)

    # Set running IMMEDIATELY so UI never sees a stale "stopped" state
    engine.running = True
    engine.event_log = []
    engine.positions = []
    # Preserve historical closed trades so "Completed Trades" panel shows past results
    engine.closed_trades = engine._load_trade_history() if hasattr(engine, "_load_trade_history") else []
    engine.in_trade = False
    engine.trades_today = 0

    _alert_state[_alert_state_key(user_id, run_id)] = {"in_trade": False, "closed_count": 0}

    async def broadcast(event: dict):
        await _broadcast_user_ws_json(user_id, {"source": "live", "run_id": run_id, **event})
        _check_trade_alerts(run_id, "Auto", event, user_id=user_id)
        # Save each closed trade to the user's run history for the Results page.
        if event.get("type") == "exit" and event.get("trade"):
            await _save_single_trade_to_history(event["trade"], "live", run_name=run_id, explicit_user_id=user_id)

    # Store engine and start task
    live_bucket[run_id] = engine
    live_task_bucket[run_id] = asyncio.create_task(engine.start(callback=broadcast))

    # Persist config + state immediately so it survives server restarts
    engine.session_date = date.today()
    engine._save_state()

    alerter.alert("Engine Started", f"Strategy: {run_id}\nMode: Auto (LIVE)", level="info")
    return {"status": "started", "run_id": run_id, "message": "Auto trading started with REAL orders"}


@app.post("/api/live/stop")
async def live_stop(request: Request):
    user_id = _request_user_id(request)
    live_bucket = _registry_bucket(live_engines, user_id)
    live_task_bucket = _registry_bucket(_live_tasks, user_id)
    stopped_engines = _load_stopped_engines(user_id)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    run_id = body.get("run_id", "")

    # If no run_id, stop the first (or only) running engine
    if not run_id:
        running = [rid for rid, e in live_bucket.items() if e.running]
        if running:
            run_id = running[0]
        else:
            return {"status": "not_running"}

    engine = live_bucket.get(run_id)
    if not engine:
        return {"status": "not_found", "run_id": run_id}

    if getattr(engine, "positions", None):
        sqoff = await _square_off_live_engine_positions(engine, user_id, run_id, reason="ENGINE_STOP")
        if not sqoff.get("ok"):
            return {
                "status": sqoff.get("status", "error"),
                "run_id": run_id,
                "message": "Live engine stop aborted because broker square-off is not fully confirmed yet.",
                "square_off": sqoff,
                "engine_status": engine.get_status(),
            }

    # Capture results AFTER any required square-off and BEFORE stopping
    status_before = engine.get_status()

    engine.stop()
    task = live_task_bucket.pop(run_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    live_bucket.pop(run_id, None)

    # Delete state file so engine doesn't auto-restore on next startup
    engine._delete_state_file()

    # Persist live run to the user's history (same as paper)
    await _save_live_run_to_history(status_before, explicit_user_id=getattr(engine, "_user_id", None))

    # Keep snapshot on Live page so panel persists after stop
    status_before["running"] = False
    status_before["run_id"] = run_id
    status_before["mode"] = "auto"
    stopped_engines[_engine_snapshot_key(run_id, "auto")] = status_before
    _save_stopped_engines(user_id)
    _alert_state.pop(_alert_state_key(user_id, run_id), None)

    pnl = round(status_before.get("total_pnl", 0), 2)
    trades = len(status_before.get("closed_trades", []))
    alerter.alert(
        "Engine Stopped",
        f"Strategy: {run_id}\nMode: Auto (LIVE)\nTrades: {trades}\nTotal P&L: \u20b9{pnl:.2f}",
        level="warn",
    )

    return {"status": "stopped", "run_id": run_id}


@app.get("/api/live/status")
async def live_status(request: Request, run_id: str = ""):
    """Get live engine status. If run_id empty, returns first running engine."""
    user_id = _request_user_id(request)
    live_bucket = _registry_bucket(live_engines, user_id)
    if run_id and run_id in live_bucket:
        return live_bucket[run_id].get_status()
    # Return first running engine's status
    for rid, engine in live_bucket.items():
        if engine.running:
            return engine.get_status()
    # Nothing running — return idle status
    return {
        "running": False,
        "run_id": "",
        "mode": "auto",
        "in_trade": False,
        "positions": [],
        "closed_trades": [],
        "total_pnl": 0,
        "trades_today": 0,
        "strategy_name": "",
        "instrument": "",
        "current_candle": {},
        "current_indicators": {},
        "event_log": [],
    }


@app.get("/api/live/debug")
async def live_debug(request: Request, run_id: str = ""):
    """Deep diagnostic of live engine state — call when trades aren't triggering."""
    user_id = _request_user_id(request)
    live_bucket = _registry_bucket(live_engines, user_id)
    engine = None
    if run_id and run_id in live_bucket:
        engine = live_bucket[run_id]
    else:
        for e in live_bucket.values():
            if e.running:
                engine = e
                break
    if not engine:
        return {"error": "No live engine running", "engines": list(live_bucket.keys())}
    return engine.debug_engine_state()


@app.get("/api/live/trades/csv")
async def export_live_trades_csv(request: Request, run_id: str = ""):
    """Export live auto-trading trades to CSV"""
    import csv as csv_mod
    import io

    live_bucket = _registry_bucket(live_engines, _request_user_id(request))
    engine = live_bucket.get(run_id) if run_id else None
    if not engine:
        # Find first engine with trades
        for e in live_bucket.values():
            if e.closed_trades:
                engine = e
                break
    if not engine or not engine.closed_trades:
        raise HTTPException(status_code=404, detail="No live trades available")
    output = io.StringIO()
    fields = [
        "id",
        "leg_num",
        "transaction_type",
        "option_type",
        "strike",
        "entry_time",
        "exit_time",
        "entry_premium",
        "exit_premium",
        "lots",
        "lot_size",
        "pnl",
        "exit_reason",
        "entry_order_id",
        "exit_order_id",
    ]
    writer = csv_mod.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for t in engine.closed_trades:
        row = {k: (str(v) if k in ("entry_time", "exit_time") else v) for k, v in t.items() if k in fields}
        writer.writerow(row)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=live_trades_{datetime.now().strftime('%Y%m%d')}.csv"},
    )


# ── Paper Trading (Real Market Data) ──────────────────────────────
@app.post("/api/paper/start")
async def paper_start(payload: StrategyPayload, request: Request):
    """Start paper trading with real live market data"""
    try:
        return await _paper_start_impl(payload, _request_user_id(request))
    except Exception:
        _logger.exception(
            "[PAPER] Start failed request_id=%s",
            getattr(request.state, "request_id", "-"),
        )
        raise


async def _paper_start_impl(payload: StrategyPayload, user_id: int):
    paper_bucket = _registry_bucket(paper_engines, user_id)
    paper_task_bucket = _registry_bucket(_paper_tasks, user_id)
    stopped_engines = _load_stopped_engines(user_id)
    entry_conditions = payload.entry_conditions or DEFAULT_ENTRY_CONDITIONS
    exit_conditions = payload.exit_conditions or DEFAULT_EXIT_CONDITIONS
    contract = validate_strategy_contract(payload.indicators, entry_conditions, exit_conditions)
    normalized_indicators = contract["normalized_indicators"]
    if contract["errors"]:
        return {
            "status": "error",
            "message": "Strategy validation failed:\n- " + "\n- ".join(contract["errors"]),
            "validation": contract,
        }
    execution_timeframe = infer_execution_timeframe(normalized_indicators, entry_conditions, default=5)
    try:
        tf_spec = resolve_strategy_timeframe(
            normalized_indicators,
            default=execution_timeframe,
            execution_hint=execution_timeframe,
        )
    except ValueError as tf_err:
        return {"status": "error", "message": str(tf_err)}
    # Configure strategy — pass ALL fields needed for SL/TP/strike logic
    strategy_dict = {
        "strategy_id": int(payload.strategy_id or 0),
        "run_name": payload.run_name,
        "instrument": payload.instrument,
        "indicators": normalized_indicators,
        "max_trades_per_day": int(payload.max_trades_per_day or 1),
        "market_open": payload.market_open or "09:15",
        "market_close": payload.market_close or "15:25",
        "legs": payload.legs or [],
        "deploy_config": payload.deploy_config or {},
        "poll_interval": 10,  # Check every 10 seconds
        # Strategy-level SL/TP
        "lots": payload.lots,
        "lot_size": payload.lot_size,
        "stoploss_pct": payload.stoploss_pct,
        "stoploss_rupees": payload.stoploss_rupees,
        "sl_type": payload.sl_type,
        "target_profit_pct": payload.target_profit_pct,
        "target_profit_rupees": payload.target_profit_rupees,
        "tp_type": payload.tp_type,
        "initial_capital": payload.initial_capital,
        "execution_profile": payload.execution_profile,
        "spread_bps": payload.spread_bps,
        "entry_slippage_bps": payload.entry_slippage_bps,
        "exit_slippage_bps": payload.exit_slippage_bps,
        "enforce_capital": payload.enforce_capital,
        "capital_buffer_pct": payload.capital_buffer_pct,
        "sell_option_margin_per_lot": payload.sell_option_margin_per_lot,
        "max_daily_loss": payload.max_daily_loss,
        "combined_sqoff_time": payload.combined_sqoff_time,
        "timeframe_minutes": tf_spec.requested,
        "fetch_timeframe_minutes": tf_spec.fetch,
    }
    strategy_dict["_user_id"] = user_id

    await _sync_saved_strategy_from_runtime(
        user_id,
        strategy_dict.get("strategy_id", 0),
        payload.model_dump(),
        entry_conditions,
        exit_conditions,
        source_label="paper start",
    )

    # Generate run_id from strategy name
    run_id = strategy_dict.get("run_name", "paper") or "paper"

    # Clear only the stopped snapshot for this exact engine identity
    stopped_engines.pop(_engine_snapshot_key(run_id, "paper"), None)
    _save_stopped_engines(user_id)

    # If an engine with same run_id exists, save its results before replacing
    old_engine = paper_bucket.get(run_id)
    if old_engine:
        try:
            old_status = old_engine.get_status()
            if old_engine.running:
                old_engine.stop()
                task = paper_task_bucket.pop(run_id, None)
                if task and not task.done():
                    task.cancel()
            await _save_paper_run_to_history(old_status, explicit_user_id=getattr(old_engine, "_user_id", None))
        except Exception as e:
            print(f"[PAPER] Failed to save old engine {run_id}: {e}")
        paper_bucket.pop(run_id, None)

    # Create a new engine instance for this strategy
    engine = PaperTradingEngine(dhan, run_id=run_id, state_dir=_engine_state_dir(user_id))
    engine.configure(
        strategy=strategy_dict,
        entry_conditions=entry_conditions,
        exit_conditions=exit_conditions,
    )
    engine._user_id = user_id

    # Inject WebSocket feed if available — starts WS + subscribes index
    if _market_feed and HAS_DHAN_FEED:
        instrument = strategy_dict.get("instrument", "26000")
        _market_feed.subscribe_index(instrument)
        if not _market_feed.is_running:
            _market_feed.start()
        engine.set_feed(_market_feed)

    # Set running IMMEDIATELY so UI never sees a stale "stopped" state
    engine.running = True
    engine.event_log = []
    engine.positions = []
    # Preserve historical closed trades so "Completed Trades" panel shows past results
    engine.closed_trades = engine._load_trade_history() if hasattr(engine, "_load_trade_history") else []
    engine.in_trade = False
    engine.trades_today = 0

    # Broadcast updates to WebSocket clients + Telegram alerts
    _alert_state[_alert_state_key(user_id, run_id)] = {"in_trade": False, "closed_count": 0}

    async def broadcast(event: dict):
        await _broadcast_user_ws_json(user_id, {"source": "paper", "run_id": run_id, **event})
        _check_trade_alerts(run_id, "Paper", event, user_id=user_id)
        # Save each closed trade to the user's run history for the Results page.
        if event.get("type") == "exit" and event.get("trade"):
            await _save_single_trade_to_history(event["trade"], "paper", run_name=run_id, explicit_user_id=user_id)

    # Store engine and start task
    paper_bucket[run_id] = engine
    paper_task_bucket[run_id] = asyncio.create_task(engine.start(callback=broadcast))

    alerter.alert("Engine Started", f"Strategy: {run_id}\nMode: Paper", level="info")
    return {"status": "started", "run_id": run_id, "message": "Paper trading started with LIVE market data"}


@app.post("/api/paper/stop")
async def paper_stop(request: Request):
    """Stop paper trading and persist results to runs.json"""
    user_id = _request_user_id(request)
    paper_bucket = _registry_bucket(paper_engines, user_id)
    paper_task_bucket = _registry_bucket(_paper_tasks, user_id)
    stopped_engines = _load_stopped_engines(user_id)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    run_id = body.get("run_id", "")

    # If no run_id, stop the first (or only) running engine
    if not run_id:
        running = [rid for rid, e in paper_bucket.items() if e.running]
        if running:
            run_id = running[0]
        else:
            return {"status": "not_running"}

    engine = paper_bucket.get(run_id)
    if not engine:
        return {"status": "not_found", "run_id": run_id}

    # Capture results BEFORE stopping (stop() may close positions)
    status_before = engine.get_status()

    engine.stop()

    task = paper_task_bucket.pop(run_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    paper_bucket.pop(run_id, None)

    # Delete state file so engine doesn't auto-restore on next startup
    engine._delete_state_file()

    # Save paper run to the user's history so it persists across restarts.
    await _save_paper_run_to_history(status_before, explicit_user_id=getattr(engine, "_user_id", None))

    # Keep snapshot on Live page so panel persists after stop
    status_before["running"] = False
    status_before["run_id"] = run_id
    status_before["mode"] = "paper"
    stopped_engines[_engine_snapshot_key(run_id, "paper")] = status_before
    _save_stopped_engines(user_id)
    _alert_state.pop(_alert_state_key(user_id, run_id), None)

    pnl = round(status_before.get("total_pnl", 0), 2)
    trades = len(status_before.get("closed_trades", []))
    alerter.alert(
        "Engine Stopped", f"Strategy: {run_id}\nMode: Paper\nTrades: {trades}\nTotal P&L: \u20b9{pnl:.2f}", level="warn"
    )

    return {"status": "stopped", "run_id": run_id}


@app.post("/api/paper/exit-position")
async def paper_exit_position(request: Request):
    """Force-exit an open position in a running paper engine."""
    user_id = _request_user_id(request)
    paper_bucket = _registry_bucket(paper_engines, user_id)
    body = await request.json()
    run_id = body.get("run_id", "")
    pos_index = body.get("position_index", 0)

    engine = paper_bucket.get(run_id)
    if not engine:
        # Try first running engine
        for rid, eng in paper_bucket.items():
            if eng.running:
                engine = eng
                run_id = rid
                break
    if not engine or not engine.running:
        return {"status": "error", "message": "No running paper engine found"}

    if pos_index >= len(engine.positions):
        return {"status": "error", "message": f"Position index {pos_index} out of range"}

    pos = engine.positions[pos_index]
    current_premium = pos.get("current_premium", pos.get("entry_premium", 0))
    engine._close_position(pos, "MANUAL_EXIT", current_premium)
    return {"status": "ok", "message": f"Position {pos.get('trading_symbol', pos.get('symbol', ''))} exited manually"}


@app.post("/api/live/exit-position")
async def live_exit_position(request: Request):
    """Force-exit an open position in a running live engine."""
    user_id = _request_user_id(request)
    live_bucket = _registry_bucket(live_engines, user_id)
    body = await request.json()
    run_id = body.get("run_id", "")
    pos_index = body.get("position_index", 0)

    engine = live_bucket.get(run_id)
    if not engine:
        for rid, eng in live_bucket.items():
            if eng.running:
                engine = eng
                run_id = rid
                break
    if not engine or not engine.running:
        return {"status": "error", "message": "No running live engine found"}

    if pos_index >= len(engine.positions):
        return {"status": "error", "message": f"Position index {pos_index} out of range"}

    pos = engine.positions[pos_index]
    current_premium = pos.get("current_premium", pos.get("entry_premium", 0))

    async def broadcast(event: dict):
        await _broadcast_user_ws_json(user_id, {"source": "live", "run_id": run_id, **event})
        _check_trade_alerts(run_id, "Auto", event, user_id=user_id)
        if event.get("type") == "exit" and event.get("trade"):
            await _save_single_trade_to_history(event["trade"], "live", run_name=run_id, explicit_user_id=user_id)

    result = await engine._exit_position(pos, "MANUAL_EXIT", current_premium, callback=broadcast)
    status = str((result or {}).get("status") or "error").lower()
    symbol = pos.get("trading_symbol", pos.get("symbol", "position"))

    if status == "ok":
        return {
            "status": "ok",
            "message": result.get("message") or f"Position {symbol} exited",
            "engine_status": engine.get_status(),
            "trade": result.get("trade"),
        }
    if status == "partial":
        return {
            "status": "partial",
            "message": result.get("message") or f"Position {symbol} partially exited",
            "remaining_qty": result.get("remaining_qty", 0),
            "engine_status": engine.get_status(),
            "trade": result.get("trade"),
        }
    if status == "pending":
        return {
            "status": "pending",
            "message": result.get("message") or f"Position {symbol} exit retry pending",
            "engine_status": engine.get_status(),
        }
    return {
        "status": "error",
        "message": result.get("message") or f"Position {symbol} exit failed",
        "engine_status": engine.get_status(),
    }


def _history_trade_signature(trade: dict) -> dict | None:
    if not isinstance(trade, dict):
        return None
    return {
        "symbol": trade.get("symbol") or trade.get("trading_symbol") or "",
        "transaction_type": trade.get("transaction_type") or trade.get("side") or "",
        "option_type": trade.get("option_type") or "",
        "strike": trade.get("strike"),
        "entry_time": str(trade.get("entry_time") or ""),
        "exit_time": str(trade.get("exit_time") or ""),
        "entry_premium": round(float(trade.get("entry_premium") or trade.get("entry_price") or 0), 4),
        "exit_premium": round(float(trade.get("exit_premium") or trade.get("exit_price") or 0), 4),
        "quantity": trade.get("quantity") or trade.get("lots") or "",
        "pnl": round(float(trade.get("pnl") or 0), 4),
        "reason": trade.get("exit_reason") or trade.get("reason") or "",
    }


def _history_trade_counter(trades: list[dict]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for trade in trades or []:
        sig = _history_trade_signature(trade)
        if sig:
            counter[json.dumps(sig, sort_keys=True, default=str)] += 1
    return counter


def _history_run_signature(run: dict) -> tuple:
    mode = str(run.get("mode") or "")
    run_name = str(run.get("run_name") or run.get("strategy_name") or "")
    trades = run.get("trades") or []
    total_pnl = round(float(run.get("total_pnl") or 0), 2)
    normalized = [sig for trade in trades if (sig := _history_trade_signature(trade))]
    normalized.sort(
        key=lambda item: (
            item["entry_time"],
            item["exit_time"],
            item["symbol"],
            item["transaction_type"],
        )
    )
    return (
        mode,
        run_name,
        int(run.get("trade_count") or len(normalized)),
        total_pnl,
        json.dumps(normalized, sort_keys=True, default=str),
    )


async def _save_single_trade_to_history(
    trade: dict, mode: str, run_name: str = "", explicit_user_id: int | None = None
) -> None:
    """Save a single closed trade (paper/live) to user-scoped run history in real time."""
    try:
        user_id = await _resolve_history_user_id(explicit_user_id, trade)
        pnl = round(trade.get("pnl", 0), 2)
        instrument = trade.get("instrument", trade.get("symbol", ""))
        side = trade.get("side", trade.get("trade_side", ""))
        label = mode.title()
        name = run_name or f"{label} {instrument} {side}"
        run_entry = {
            "mode": mode,
            "run_name": name,
            "instrument": instrument,
            "status": "completed",
            "started_at": str(trade.get("entry_time", "")),
            "stopped_at": str(trade.get("exit_time", "")),
            "trade_count": 1,
            "total_pnl": pnl,
            "stats": {
                "total_trades": 1,
                "winning_trades": 1 if pnl > 0 else 0,
                "losing_trades": 1 if pnl <= 0 else 0,
                "win_rate": 100.0 if pnl > 0 else 0.0,
                "total_pnl": pnl,
            },
            "trades": [trade],
            "created_at": str(datetime.now()),
        }
        target_sig = _history_run_signature(run_entry)
        runs = await _db_mod.list_runs(user_id)
        if any(_history_run_signature(r) == target_sig for r in runs):
            print(f"[{mode.upper()}] Identical single-trade history already exists — skipping duplicate save")
            return
        saved = await _db_mod.create_run_record(user_id, run_entry)
        _logger.info("[%s] Saved one trade to run history record_id=%s", mode.upper(), saved["id"])
    except Exception as e:
        print(f"[{mode.upper()}] Failed to save trade to history: {e}")


async def _save_paper_run_to_history(status: dict, explicit_user_id: int | None = None):
    """Save a completed paper trading run to user-scoped history."""
    try:
        closed = status.get("closed_trades", [])
        if not closed:
            print("[PAPER] No closed trades — skipping history save")
            return

        user_id = await _resolve_history_user_id(explicit_user_id, status)
        run_name = status.get("strategy_name", "Paper Run")
        runs = await _db_mod.list_runs(user_id)
        existing = Counter()
        for run in runs:
            trade_count = int(run.get("trade_count") or len(run.get("trades") or []))
            if run.get("mode") != "paper" or run.get("run_name") != run_name or trade_count != 1:
                continue
            existing += _history_trade_counter(run.get("trades") or [])
        closed_sigs = _history_trade_counter(closed)
        if closed_sigs and all(existing[key] >= count for key, count in closed_sigs.items()):
            print(f"[PAPER] All {len(closed)} trades already saved individually — skipping bulk save")
            return

        total_pnl = round(sum(t.get("pnl", 0) for t in closed), 2)
        winners = [t for t in closed if t.get("pnl", 0) > 0]
        losers = [t for t in closed if t.get("pnl", 0) <= 0]
        win_rate = round(len(winners) / len(closed) * 100, 2) if closed else 0

        paper_run = {
            "mode": "paper",
            "run_name": status.get("strategy_name", "Paper Run"),
            "instrument": status.get("instrument", ""),
            "status": "completed",
            "started_at": str(datetime.now()),
            "stopped_at": str(datetime.now()),
            "trade_count": len(closed),
            "total_pnl": total_pnl,
            "stats": {
                "total_trades": len(closed),
                "winning_trades": len(winners),
                "losing_trades": len(losers),
                "win_rate": win_rate,
                "total_pnl": total_pnl,
                "avg_profit": round(sum(t["pnl"] for t in winners) / len(winners), 2) if winners else 0,
                "avg_loss": round(sum(t["pnl"] for t in losers) / len(losers), 2) if losers else 0,
            },
            "trades": closed,
            "created_at": str(datetime.now()),
            **{
                k: v
                for k, v in (status.get("strategy") or {}).items()
                if k
                in (
                    "indicators",
                    "entry_conditions",
                    "exit_conditions",
                    "legs",
                    "lots",
                    "lot_size",
                    "stoploss_pct",
                    "stoploss_rupees",
                    "sl_type",
                    "target_profit_pct",
                    "target_profit_rupees",
                    "tp_type",
                    "market_open",
                    "market_close",
                    "folder",
                    "max_trades_per_day",
                )
            },
        }

        target_sig = _history_run_signature(paper_run)
        if any(_history_run_signature(r) == target_sig for r in runs):
            print("[PAPER] Identical completed run already exists — skipping duplicate history save")
            return

        saved = await _db_mod.create_run_record(user_id, paper_run)
        _logger.info("[PAPER] Saved completed run record_id=%s trade_count=%s", saved["id"], len(closed))
    except Exception as e:
        print(f"[PAPER] Failed to save run to history: {e}")


async def _save_scalp_run_to_history(eng, explicit_user_id: int | None = None) -> None:
    """Persist a completed scalp session to user-scoped run history."""
    try:
        status = eng.get_status()
        closed = status.get("closed_trades", [])
        if not closed:
            print("[SCALP] No closed trades — skipping history save")
            return

        user_id = await _resolve_history_user_id(explicit_user_id, status)
        total_pnl = round(sum(t.get("pnl", 0) for t in closed), 2)
        winners = [t for t in closed if t.get("pnl", 0) > 0]
        losers = [t for t in closed if t.get("pnl", 0) <= 0]
        win_rate = round(len(winners) / len(closed) * 100, 2) if closed else 0

        underlyings = list(dict.fromkeys(t.get("underlying", "") for t in closed if t.get("underlying")))
        run_name = "Scalp — " + ", ".join(underlyings) if underlyings else "Scalp Session"

        scalp_run = {
            "mode": "scalp",
            "run_name": run_name,
            "instrument": underlyings[0] if underlyings else "",
            "status": "completed",
            "started_at": closed[-1].get("entry_time", str(datetime.now())),
            "stopped_at": str(datetime.now()),
            "trade_count": len(closed),
            "total_pnl": total_pnl,
            "stats": {
                "total_trades": len(closed),
                "winning_trades": len(winners),
                "losing_trades": len(losers),
                "win_rate": win_rate,
                "total_pnl": total_pnl,
                "avg_profit": round(sum(t["pnl"] for t in winners) / len(winners), 2) if winners else 0,
                "avg_loss": round(sum(t["pnl"] for t in losers) / len(losers), 2) if losers else 0,
            },
            "trades": closed,
            "created_at": str(datetime.now()),
        }

        saved = await _db_mod.create_run_record(user_id, scalp_run)
        _logger.info("[SCALP] Saved completed run record_id=%s trade_count=%s", saved["id"], len(closed))
    except Exception as e:
        print(f"[SCALP] Failed to save run to history: {e}")


async def _save_live_run_to_history(status: dict, explicit_user_id: int | None = None):
    """Save a completed live (auto) trading run to user-scoped history."""
    try:
        closed = status.get("closed_trades", [])
        if not closed:
            print("[LIVE] No closed trades — skipping history save")
            return

        user_id = await _resolve_history_user_id(explicit_user_id, status)
        run_name = status.get("strategy_name", "Live Run")
        runs = await _db_mod.list_runs(user_id)
        existing = Counter()
        for run in runs:
            trade_count = int(run.get("trade_count") or len(run.get("trades") or []))
            if run.get("mode") != "live" or run.get("run_name") != run_name or trade_count != 1:
                continue
            existing += _history_trade_counter(run.get("trades") or [])
        closed_sigs = _history_trade_counter(closed)
        if closed_sigs and all(existing[key] >= count for key, count in closed_sigs.items()):
            print(f"[LIVE] All {len(closed)} trades already saved individually — skipping bulk save")
            return

        total_pnl = round(sum(t.get("pnl", 0) for t in closed), 2)
        winners = [t for t in closed if t.get("pnl", 0) > 0]
        losers = [t for t in closed if t.get("pnl", 0) <= 0]
        win_rate = round(len(winners) / len(closed) * 100, 2) if closed else 0

        live_run = {
            "mode": "live",
            "run_name": status.get("strategy_name", "Live Run"),
            "instrument": status.get("instrument", ""),
            "status": "completed",
            "started_at": str(datetime.now()),
            "stopped_at": str(datetime.now()),
            "trade_count": len(closed),
            "total_pnl": total_pnl,
            "stats": {
                "total_trades": len(closed),
                "winning_trades": len(winners),
                "losing_trades": len(losers),
                "win_rate": win_rate,
                "total_pnl": total_pnl,
                "avg_profit": round(sum(t["pnl"] for t in winners) / len(winners), 2) if winners else 0,
                "avg_loss": round(sum(t["pnl"] for t in losers) / len(losers), 2) if losers else 0,
            },
            "trades": closed,
            "created_at": str(datetime.now()),
            **{
                k: v
                for k, v in (status.get("strategy") or {}).items()
                if k
                in (
                    "indicators",
                    "entry_conditions",
                    "exit_conditions",
                    "legs",
                    "lots",
                    "lot_size",
                    "stoploss_pct",
                    "stoploss_rupees",
                    "sl_type",
                    "target_profit_pct",
                    "target_profit_rupees",
                    "tp_type",
                    "market_open",
                    "market_close",
                    "folder",
                    "max_trades_per_day",
                )
            },
        }

        target_sig = _history_run_signature(live_run)
        if any(_history_run_signature(r) == target_sig for r in runs):
            print("[LIVE] Identical completed run already exists — skipping duplicate history save")
            return

        saved = await _db_mod.create_run_record(user_id, live_run)
        _logger.info("[LIVE] Saved completed run record_id=%s trade_count=%s", saved["id"], len(closed))
    except Exception as e:
        print(f"[LIVE] Failed to save run to history: {e}")


@app.get("/api/paper/status")
async def paper_status(request: Request, run_id: str = ""):
    """Get paper trading status. If run_id empty, returns first running engine."""
    user_id = _request_user_id(request)
    paper_bucket = _registry_bucket(paper_engines, user_id)
    if run_id and run_id in paper_bucket:
        return paper_bucket[run_id].get_status()

    # Return first running engine's status
    for rid, engine in paper_bucket.items():
        if engine.running:
            return engine.get_status()

    # No running engines — check for last saved paper run from history
    status = {
        "running": False,
        "run_id": "",
        "mode": "paper",
        "in_trade": False,
        "positions": [],
        "closed_trades": [],
        "total_pnl": 0,
        "trades_today": 0,
        "strategy_name": "",
        "instrument": "",
        "current_candle": {},
        "current_indicators": {},
        "event_log": [],
    }
    try:
        runs = await _db_mod.list_runs(_request_user_id(request))
        paper_runs = [r for r in runs if r.get("mode") == "paper"]
        if paper_runs:
            last = paper_runs[-1]
            trades = last.get("trades", [])
            status["strategy_name"] = last.get("run_name", "Last Paper Run")
            status["instrument"] = last.get("instrument", "")
            status["closed_trades"] = trades
            status["trades_today"] = len(trades)
            status["total_pnl"] = last.get("total_pnl", 0)
            status["_from_history"] = True
    except Exception:
        pass

    return status


# ── Combined Engines Status (Multi-Strategy Monitor) ─────────────
@app.get("/api/engines/all")
async def engines_all(request: Request):
    """Return status of the current user's running engines for the Live page."""
    user_id = _request_user_id(request)
    engines = []
    stopped_engines = _load_stopped_engines(user_id)
    strategy_rows = await _db_mod.list_strategies(user_id)
    strategy_folder_map: dict[str, str] = {}
    strategy_by_id: dict[int, dict] = {}
    strategy_name_matches: dict[str, list[dict]] = defaultdict(list)
    for strategy in strategy_rows:
        strategy_name = str(strategy.get("run_name") or strategy.get("name") or "").strip().casefold()
        strategy_id = int(strategy.get("id") or 0)
        if strategy_id:
            strategy_by_id[strategy_id] = strategy
        if strategy_name and not strategy.get("_placeholder"):
            strategy_name_matches[strategy_name].append(strategy)
            strategy_folder_map[strategy_name] = str(strategy.get("folder") or "").strip() or "Intraday"

    def _attach_strategy_folder(status: dict) -> dict:
        if not isinstance(status, dict):
            return status
        strategy_payload = status.get("strategy") if isinstance(status.get("strategy"), dict) else None
        strategy_id = int(status.get("strategy_id") or (strategy_payload or {}).get("strategy_id") or 0)
        explicit_folder = str(status.get("folder") or (strategy_payload or {}).get("folder") or "").strip()
        strategy_name = str(
            status.get("strategy_name")
            or (strategy_payload or {}).get("run_name")
            or (strategy_payload or {}).get("name")
            or ""
        ).strip()
        matched_strategy = strategy_by_id.get(strategy_id) if strategy_id else None
        if not matched_strategy and strategy_name:
            matches = list(strategy_name_matches.get(strategy_name.casefold(), []))
            if explicit_folder:
                folder_key = (explicit_folder or "Intraday").strip().casefold()
                folder_matches = [
                    s for s in matches if (str(s.get("folder") or "").strip() or "Intraday").casefold() == folder_key
                ]
                if len(folder_matches) == 1:
                    matched_strategy = folder_matches[0]
            if not matched_strategy and len(matches) == 1:
                matched_strategy = matches[0]
        resolved_folder = explicit_folder
        if matched_strategy:
            resolved_folder = str(matched_strategy.get("folder") or "").strip() or "Intraday"
            status["strategy_id"] = int(matched_strategy.get("id") or strategy_id or 0)
            if strategy_payload is not None:
                strategy_payload.setdefault("strategy_id", status["strategy_id"])
        elif not resolved_folder and strategy_name:
            resolved_folder = strategy_folder_map.get(strategy_name.casefold(), "")
        if resolved_folder:
            status["folder"] = resolved_folder
            if strategy_payload is not None and not strategy_payload.get("folder"):
                strategy_payload["folder"] = resolved_folder
        return status

    # Add all paper engines
    for run_id, engine in _registry_bucket(paper_engines, user_id).items():
        if engine.running:
            st = _attach_strategy_folder(engine.get_status())
            st["run_id"] = run_id
            st["mode"] = "paper"
            engines.append(st)

    # Add all live engines
    for run_id, engine in _registry_bucket(live_engines, user_id).items():
        if engine.running:
            st = _attach_strategy_folder(engine.get_status())
            st["run_id"] = run_id
            st["mode"] = "auto"
            engines.append(st)

    # Add stopped engine snapshots (persisted panels)
    active_ids = {_engine_status_key(e) for e in engines}
    for snapshot in stopped_engines.values():
        snapshot_key = _engine_status_key(snapshot)
        if snapshot_key not in active_ids:
            engines.append(_attach_strategy_folder(snapshot))
            active_ids.add(snapshot_key)

    # Fallback for migrated/admin sessions: if today's persisted engine state
    # exists but restore/stopped snapshots are absent, synthesize idle panels so
    # the Live page does not appear blank.
    if not engines:
        for snapshot in _state_file_snapshots(user_id):
            snapshot_key = _engine_status_key(snapshot)
            if snapshot.get("run_id") and snapshot_key not in active_ids:
                engines.append(_attach_strategy_folder(snapshot))
                active_ids.add(snapshot_key)

    return {"engines": engines, "count": len(engines)}


@app.post("/api/engines/dismiss")
async def engines_dismiss(request: Request):
    """Remove a stopped engine snapshot from the Live page."""
    user_id = _request_user_id(request)
    stopped_engines = _load_stopped_engines(user_id)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    run_id = body.get("run_id", "")
    mode = body.get("mode", "")
    if run_id and mode:
        snapshot_key = _engine_snapshot_key(run_id, mode)
        if snapshot_key not in stopped_engines:
            return {"status": "not_found", "run_id": run_id, "mode": _normalize_engine_mode(mode)}
        stopped_engines.pop(snapshot_key, None)
        _save_stopped_engines(user_id)
        return {"status": "dismissed", "run_id": run_id, "mode": _normalize_engine_mode(mode)}
    if run_id:
        removed = [key for key, snapshot in stopped_engines.items() if str(snapshot.get("run_id") or "") == str(run_id)]
        for key in removed:
            stopped_engines.pop(key, None)
        if not removed:
            return {"status": "not_found", "run_id": run_id}
        _save_stopped_engines(user_id)
        return {"status": "dismissed", "run_id": run_id}
    return {"status": "not_found", "run_id": run_id}


# ── WebSocket ─────────────────────────────────────────────────────


# Event-driven signal: set whenever scalp state changes (entry/exit/modify)
_scalp_ws_event: asyncio.Event | None = None


def _get_scalp_ws_event() -> asyncio.Event:
    global _scalp_ws_event
    if _scalp_ws_event is None:
        _scalp_ws_event = asyncio.Event()
    return _scalp_ws_event


def _notify_scalp_ws():
    """Signal all WS clients to push scalp update immediately."""
    evt = _get_scalp_ws_event()
    evt.set()


def _ws_serialize(payload: dict) -> bytes:
    """Serialize WS payload using orjson (fast) with stdlib json fallback."""
    if _orjson is not None:
        return _orjson.dumps(payload)
    return json.dumps(payload).encode("utf-8")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    origin = _normalize_origin_value(ws.headers.get("origin", ""))
    websocket_origins = {_normalize_origin_value(value) for value in _CORS_ALLOWED_ORIGINS}
    if origin and origin not in websocket_origins:
        await ws.close(code=4003, reason="Forbidden origin")
        return
    # Authenticate WebSocket via session cookie (DB-backed)
    token = ws.cookies.get(_SESSION_COOKIE_NAME, "")
    session = await _validate_session_async(token)
    if not session:
        await ws.close(code=4001, reason="Unauthorized")
        return
    user_id = int(session["user_id"])
    user = await _db_mod.get_user_by_id(user_id)
    if not user or not user.get("is_active"):
        await ws.close(code=4001, reason="Account disabled or not found")
        return
    await ws.accept()
    _user_ws_clients(user_id).append(ws)

    scalp_evt = _get_scalp_ws_event()
    engine_tick = 0  # counter: send full engine status every 20 cycles (~5s)
    authorization_tick = 0

    try:
        while True:
            # Wait for either: scalp event fires OR 250ms timeout
            try:
                await asyncio.wait_for(scalp_evt.wait(), timeout=0.25)
                scalp_evt.clear()
            except asyncio.TimeoutError:
                pass

            # Scalp status — every cycle (250ms)
            scalp_data = None
            scalp_engine = _scalp_engines.get(int(user_id))
            if _HAS_SCALP and scalp_engine is not None:
                try:
                    scalp_data = scalp_engine.get_status()
                except Exception:
                    pass

            payload = {"type": "status", "_ts": time.time()}

            if scalp_data is not None:
                payload["scalp"] = scalp_data

            # Engine status — every ~5s (20 × 250ms) to avoid waste
            engine_tick += 1
            authorization_tick += 1
            if authorization_tick >= 240:
                authorization_tick = 0
                current_session = await _validate_session_async(token)
                current_user = await _db_mod.get_user_by_id(user_id) if current_session else None
                if not current_user or not current_user.get("is_active"):
                    await ws.close(code=4001, reason="Session expired or account disabled")
                    break
            if engine_tick >= 20:
                engine_tick = 0
                paper_sts = {
                    rid: e.get_status() for rid, e in _registry_bucket(paper_engines, user_id).items() if e.running
                }
                live_sts = {
                    rid: e.get_status() for rid, e in _registry_bucket(live_engines, user_id).items() if e.running
                }
                payload["paper_engines"] = paper_sts
                payload["live_engines"] = live_sts
                payload["paper_running"] = any(s.get("running") for s in paper_sts.values())
                payload["live_running"] = any(s.get("running") for s in live_sts.values())
                cascade_runtime = _cascade_engines.get(int(user_id))
                if cascade_runtime is not None:
                    payload["cascade"] = {**cascade_runtime.engine.get_status(), "running": cascade_runtime.running}

            await ws.send_bytes(_ws_serialize(payload))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        if ws in _user_ws_clients(user_id):
            _user_ws_clients(user_id).remove(ws)


# ── Orders / Positions / Funds ────────────────────────────────────
@app.post("/api/orders/place")
async def place_order(req: OrderRequest, request: Request):
    check_rate_limit("place_order", _request_rate_subject(request), max_calls=3, window_sec=5)
    values = _validated_order_values(req)
    user, broker_client, source = await _request_broker_context(request)
    if not broker_client:
        raise HTTPException(status_code=400, detail=_broker_not_configured_message(user, source))
    try:
        return broker_client.place_order(
            security_id=req.security_id,
            exchange_segment=values["exchange_segment"],
            transaction_type=values["transaction_type"],
            quantity=req.quantity,
            order_type=values["order_type"],
            product_type=values["product_type"],
            price=req.price,
            trigger_price=req.trigger_price,
            validity=values["validity"],
            disclosed_quantity=req.disclosed_quantity,
            after_market_order=req.after_market_order,
            amo_time=values["amo_time"],
            bo_profit_value=req.bo_profit_value,
            bo_stop_loss_value=req.bo_stop_loss_value,
            slice_order=req.slice_order,
        )
    except Exception as e:
        detail = _broker_order_failure_detail(e, "Order failed")
        alerter.alert(
            "Order Failed",
            f"Security: {req.security_id}\nType: {req.transaction_type}\nQty: {req.quantity}\nError: {detail['reason']}",
        )
        raise HTTPException(status_code=502 if isinstance(e, DhanOrderError) else 500, detail=detail)


@app.get("/api/orders")
async def get_orders(request: Request):
    try:
        user, broker_client, source = await _request_broker_context(request)
        if not broker_client:
            return {"status": "not_configured", "message": _broker_not_configured_message(user, source), "data": []}
        orders = broker_client.get_order_book()
        return {"status": "success", "data": orders if isinstance(orders, list) else []}
    except Exception as e:
        return {"status": "error", "message": str(e)[:100], "data": []}


@app.get("/api/orders/{order_id}/status")
async def get_order_status(order_id: str, request: Request):
    user, broker_client, source = await _request_broker_context(request)
    if not broker_client:
        raise HTTPException(status_code=400, detail=_broker_not_configured_message(user, source))
    try:
        status = broker_client.get_order_status(order_id)
        if isinstance(status, dict) and not (status.get("orderId") or status.get("order_id")):
            status["orderId"] = order_id
        return {"status": "success", "data": status if isinstance(status, dict) else {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions")
async def get_positions(request: Request):
    try:
        user, broker_client, source = await _request_broker_context(request)
        if not broker_client:
            return {"status": "not_configured", "message": _broker_not_configured_message(user, source), "data": []}
        positions = broker_client.get_positions()
        return {"status": "success", "data": positions if isinstance(positions, list) else []}
    except Exception as e:
        return {"status": "error", "message": str(e)[:100], "data": []}


@app.get("/api/funds")
async def get_funds(request: Request):
    user, broker_client, source = await _request_broker_context(request)
    if not broker_client:
        raise HTTPException(status_code=400, detail=_broker_not_configured_message(user, source))
    try:
        return broker_client.get_funds()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/orders/{order_id}")
async def cancel_order(order_id: str, request: Request):
    user, broker_client, source = await _request_broker_context(request)
    if not broker_client:
        raise HTTPException(status_code=400, detail=_broker_not_configured_message(user, source))
    try:
        return broker_client.cancel_order(order_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/terminal/cascade/status")
async def terminal_cascade_status(request: Request):
    user_id = _request_user_id(request)
    # Passive monitoring must never resurrect a poll loop.
    runtimes = _terminal_cascade_engines.get(user_id, {})
    if not runtimes:
        return {"status": "not_started", "mode": "paper", "live_gate": _terminal_cascade_live_gate_status()}
    return {
        "status": "ok",
        "mode": "paper",
        "live_gate": _terminal_cascade_live_gate_status(),
        "campaigns": [
            {**runtime.engine.get_status(), "running": runtime.running} for _symbol, runtime in sorted(runtimes.items())
        ],
    }


@app.get("/api/terminal/cascade/chart")
async def terminal_cascade_chart(request: Request, symbol: str, mother_timestamp: str, timeframe: str = "5m"):
    mother = _parse_cascade_mother_timestamp(mother_timestamp)
    _interval, minutes, normalised_tf = _terminal_cascade_timeframe_parts(timeframe)
    now = datetime.now(IST)
    max_age_days = _terminal_cascade_max_mother_age_days(timeframe)
    if mother.date() > now.date() or (now.date() - mother.date()).days > max_age_days:
        raise HTTPException(
            status_code=400,
            detail=f"Chart is available for {normalised_tf} mothers in the last {max_age_days} calendar days.",
        )
    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(status_code=400, detail="Connect a Dhan account to load the Terminal Cascade chart.")
    instrument, signal_instrument, trade_instrument = _terminal_cascade_instruments(symbol)
    mother_signal, mother_trade = await _terminal_cascade_load_mother_pair(
        broker_client, signal_instrument, trade_instrument, normalised_tf, mother
    )
    engine = CashCascadePaperEngine(
        mother_signal, mother_trade, instrument, CashCascadePaperConfig(capital_inr=100000, timeframe=normalised_tf)
    )
    chart_candles = await _terminal_cascade_replay_with_candles(
        broker_client, engine, signal_instrument, trade_instrument, mother
    )
    rows = _cascade_native_candles(chart_candles, mother)
    mother_row = next((row for row in rows if row["is_mother"]), None)
    if mother_row is None:
        raise HTTPException(status_code=404, detail="The selected mother candle was not returned by Dhan.")
    return {
        "status": "ok",
        "timeframe": normalised_tf,
        "bar_minutes": minutes,
        "chart_mode": "native_ohlc",
        "instrument": instrument.to_dict(),
        "candles": rows,
        "mother": mother_row,
        "geometry": engine.get_status()["geometry"],
        "geometry_state": engine.geometry.campaign.state,
        "note": "Terminal Cascade charts use native Dhan OHLC; paper geometry uses the same exchange candles.",
    }


@app.post("/api/terminal/cascade/start")
async def terminal_cascade_start(payload: TerminalCascadePaperStartPayload, request: Request):
    check_rate_limit("terminal_cascade_start", _request_rate_subject(request), max_calls=3, window_sec=5)
    mother_timestamp = _parse_cascade_mother_timestamp(payload.mother_timestamp)
    _interval, minutes, normalised_tf = _terminal_cascade_timeframe_parts(payload.timeframe)
    now = datetime.now(IST)
    if mother_timestamp.date() > now.date():
        raise HTTPException(status_code=400, detail="Mother timestamp cannot be in the future.")
    max_age_days = _terminal_cascade_max_mother_age_days(payload.timeframe)
    if (now.date() - mother_timestamp.date()).days > max_age_days:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Mother candle is outside the {normalised_tf} replay window "
                f"({max_age_days} days — the window is a bar budget, so higher timeframes reach further back)."
            ),
        )
    if mother_timestamp + timedelta(minutes=minutes) > now or mother_timestamp.second or mother_timestamp.microsecond:
        raise HTTPException(
            status_code=400, detail="Mother timestamp must be a completed Terminal Cascade candle open."
        )
    if normalised_tf == "1d":
        if mother_timestamp.time() != dt_time(9, 15):
            raise HTTPException(status_code=400, detail="1D mother timestamp must be the 09:15 IST session open.")
    elif normalised_tf == "1h":
        if mother_timestamp.minute != 15:
            raise HTTPException(status_code=400, detail="1H mother timestamp must be NSE aligned at :15 IST.")
    elif mother_timestamp.minute % minutes:
        raise HTTPException(status_code=400, detail=f"{normalised_tf} mother timestamp is not aligned.")
    if not (dt_time(9, 15) <= mother_timestamp.time() < dt_time(15, 30)):
        raise HTTPException(status_code=400, detail="Mother candle must be within the NSE 09:15-15:30 session.")
    user_id = _request_user_id(request)
    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(
            status_code=400, detail="Connect a Dhan account before starting Terminal Cascade paper mode."
        )
    runtimes = await _restore_terminal_cascade_open_state(user_id, broker_client)
    instrument, signal_instrument, trade_instrument = _terminal_cascade_instruments(payload.symbol)
    symbol = ScripMaster.normalize_equity_symbol(instrument.symbol)
    old_runtime = runtimes.get(symbol)
    if old_runtime is not None and old_runtime.running:
        raise HTTPException(
            status_code=409, detail=f"A Terminal Cascade paper campaign is already running for {symbol}."
        )
    mother_signal, mother_trade = await _terminal_cascade_load_mother_pair(
        broker_client, signal_instrument, trade_instrument, normalised_tf, mother_timestamp
    )
    engine = CashCascadePaperEngine(
        mother_signal,
        mother_trade,
        instrument,
        CashCascadePaperConfig(
            capital_inr=payload.capital_inr,
            target_fraction=payload.target_fraction,
            timeframe=normalised_tf,
            product_type=payload.product_type,
        ),
    )
    last = await _terminal_cascade_replay_to_now(
        broker_client, engine, signal_instrument, trade_instrument, mother_timestamp
    )
    runtime = _TerminalCascadeRuntime(
        engine=engine,
        broker=broker_client,
        signal_instrument=signal_instrument,
        trade_instrument=trade_instrument,
        last_candle_timestamp=last,
    )
    runtimes[symbol] = runtime
    _terminal_cascade_engines[user_id] = runtimes
    runtime.task = asyncio.create_task(_run_terminal_cascade_paper_loop(user_id, runtime))
    await _save_terminal_cascade_open_state(user_id, runtime, force=True)
    await _notify_terminal_cascade_ws(user_id)
    return {"status": "started", "mode": "paper", "campaign": {**engine.get_status(), "running": True}}


@app.post("/api/terminal/cascade/stop")
async def terminal_cascade_stop(request: Request, symbol: str):
    user_id = _request_user_id(request)
    runtimes = _terminal_cascade_engines.get(user_id, {})
    runtime = runtimes.get(ScripMaster.normalize_equity_symbol(symbol))
    if runtime is None:
        return {"status": "not_running"}
    runtime.running = False
    runtime.engine.status = "STOPPED"
    if runtime.task and not runtime.task.done():
        runtime.task.cancel()
    await _save_terminal_cascade_open_state(user_id, runtime, force=True)
    await _notify_terminal_cascade_ws(user_id)
    return {"status": "stopped", "mode": "paper"}


@app.post("/api/terminal/cascade/kill")
async def terminal_cascade_kill(request: Request, symbol: str):
    user_id = _request_user_id(request)
    _user, broker_client, _source = await _request_broker_context(request)
    runtimes = await _restore_terminal_cascade_open_state(user_id, broker_client)
    runtime = runtimes.get(ScripMaster.normalize_equity_symbol(symbol))
    if runtime is None:
        raise HTTPException(status_code=404, detail="No Terminal Cascade paper campaign is active.")
    signal, trade = await _terminal_cascade_quote_pair(runtime)
    result = runtime.engine.kill_and_close(signal, trade)
    runtime.running = False
    if runtime.task and not runtime.task.done():
        runtime.task.cancel()
    await _save_terminal_cascade_open_state(user_id, runtime, force=True)
    await _notify_terminal_cascade_ws(user_id)
    return {
        "status": "killed",
        "mode": "paper",
        "cancelled_rungs": result["cancelled_rungs"],
        "campaign": {**runtime.engine.get_status(), "running": False},
    }


_TERMINAL_CASCADE_CLOSED_LIMIT = 100


def _terminal_cascade_closed_state_key(user_id: int) -> str:
    return f"terminal_cash_cascade_closed:{int(user_id)}"


async def _load_terminal_cascade_closed(user_id: int) -> list:
    raw = await _db_mod.get_app_state(_terminal_cascade_closed_state_key(user_id))
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _terminal_cascade_closed_summary(engine: CashCascadePaperEngine) -> dict:
    """What a deleted campaign leaves behind: its identity, its rounds and the
    money story — but not the candle history, which would bloat the archive."""
    import uuid

    status = engine.get_status()
    rounds = status.get("rounds") or []
    realised = round(sum(float(row.get("net_pnl") or 0) for row in rounds), 2)
    costs = round(sum(float((row.get("costs") or {}).get("total") or 0) for row in rounds), 2)
    return {
        "archive_id": uuid.uuid4().hex[:10],
        "deleted_at": datetime.now(IST).isoformat(),
        "status": status.get("status"),
        "instrument": status.get("instrument"),
        "config": status.get("config"),
        "mother": status.get("mother"),
        "open_quantity": status.get("open_quantity"),
        "open_invested_inr": status.get("open_invested_inr"),
        "rounds": rounds,
        "realised_net_inr": realised,
        "costs_inr": costs,
        "events": (status.get("events") or [])[-40:],
    }


@app.delete("/api/terminal/cascade")
async def terminal_cascade_delete(request: Request, symbol: str):
    user_id = _request_user_id(request)
    _user, broker_client, _source = await _request_broker_context(request)
    runtimes = await _restore_terminal_cascade_open_state(user_id, broker_client)
    runtime = runtimes.pop(ScripMaster.normalize_equity_symbol(symbol), None)
    if runtime is None:
        # Nothing in memory and nothing restorable for this symbol. Refusing
        # here also protects saved campaigns we could not load (no broker):
        # the old behaviour wiped the whole open-state key in that case.
        raise HTTPException(status_code=404, detail=f"No Terminal Cascade campaign found for {symbol}.")
    runtime.running = False
    if runtime.task and not runtime.task.done():
        runtime.task.cancel()
    archived = False
    try:
        closed = await _load_terminal_cascade_closed(user_id)
        closed.insert(0, _terminal_cascade_closed_summary(runtime.engine))
        await _db_mod.set_app_state(
            _terminal_cascade_closed_state_key(user_id),
            json.dumps(closed[:_TERMINAL_CASCADE_CLOSED_LIMIT], default=str),
        )
        archived = True
    except Exception as exc:
        # The delete still proceeds — history is a courtesy, not a gate.
        _logger.warning("[TERMINAL CASCADE] Could not archive deleted campaign for user %s: %s", user_id, exc)
    if runtimes:
        await _save_terminal_cascade_open_state(user_id, force=True)
    else:
        _terminal_cascade_engines.pop(user_id, None)
        await _db_mod.set_app_state(_terminal_cascade_open_state_key(user_id), "")
    await _notify_terminal_cascade_ws(user_id)
    return {"status": "deleted", "archived": archived}


@app.get("/api/terminal/cascade/closed")
async def terminal_cascade_closed(request: Request):
    """Deleted campaigns, newest first — the Terminal's closed-campaign history."""
    user_id = _request_user_id(request)
    return {"status": "ok", "campaigns": await _load_terminal_cascade_closed(user_id)}


@app.delete("/api/terminal/cascade/closed/{archive_id}")
async def terminal_cascade_closed_purge(request: Request, archive_id: str):
    user_id = _request_user_id(request)
    closed = await _load_terminal_cascade_closed(user_id)
    kept = [row for row in closed if str(row.get("archive_id")) != str(archive_id)]
    if len(kept) == len(closed):
        raise HTTPException(status_code=404, detail="No archived campaign with that id.")
    await _db_mod.set_app_state(_terminal_cascade_closed_state_key(user_id), json.dumps(kept, default=str))
    return {"status": "purged"}


# ── Terminal Cascade: instrument scanner ──────────────────────────
_CASCADE_SCAN_CACHE: Dict[str, tuple] = {}
# The cache key includes the IST date. Keeping it for a full day means a scan is
# stable for that day's campaign decisions, while the next IST day naturally
# gets a fresh universe and daily candles.
_CASCADE_SCAN_TTL_SEC = 24 * 60 * 60
_CASCADE_SCAN_CONCURRENCY = 8
_CASCADE_SCAN_HISTORY_DAYS = 200


def _terminal_cascade_scan_state_key(user_id: int, cache_key: str) -> str:
    """Persistent, per-user snapshot for one day's scanner settings."""
    return f"terminal_cash_cascade_scan:{int(user_id)}:{cache_key}"


async def _load_terminal_cascade_scan_snapshot(user_id: int, cache_key: str) -> dict | None:
    raw = await _db_mod.get_app_state(_terminal_cascade_scan_state_key(user_id, cache_key))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "ok"
        or not isinstance(payload.get("candidates"), list)
    ):
        return None
    return payload


async def _cascade_scan_history(broker: DhanClient, stock: dict, semaphore: asyncio.Semaphore) -> Optional[ScanInput]:
    """Daily candles for one scrip, or None if Dhan cannot supply them.

    A scrip that fails to load is dropped rather than scanned on partial data:
    a short history silently changes what "performing" and "off its high" mean.
    """
    if not stock.get("security_id"):
        return None
    today = datetime.now(IST).date()
    async with semaphore:
        try:
            frame = await asyncio.to_thread(
                broker.get_historical_data,
                security_id=str(stock["security_id"]),
                exchange_segment=str(stock["exchange_segment"]),
                instrument_type=str(stock["instrument_type"]),
                from_date=(today - timedelta(days=_CASCADE_SCAN_HISTORY_DAYS)).isoformat(),
                to_date=today.isoformat(),
                candle_type="D",
            )
        except Exception:
            return None
    if frame is None or getattr(frame, "empty", True):
        return None
    closes = [float(value) for value in frame["close"].tolist()]
    highs = [float(value) for value in frame["high"].tolist()]
    if not closes:
        return None
    return ScanInput(
        symbol=stock["symbol"],
        name=stock.get("name") or stock["symbol"],
        closes=closes,
        highs=highs,
        last_price=closes[-1],
        etf=stock["symbol"] in _BEES_SYMBOLS,
    )


@app.get("/api/terminal/cascade/scan")
async def terminal_cascade_scan(
    request: Request,
    capital_inr: float = 100000.0,
    limit: int = 30,
    min_price: float = 200.0,
    refresh: bool = False,
    load_only: bool = False,
):
    """Rank Nifty 200 scrips by how tradeable a Cascade on them would be today.

    Read-only: it places no orders and starts no campaign.  The result is cached
    because it costs a couple of hundred Dhan calls to build, and daily candles
    only change once a day.
    """
    if capital_inr <= 0:
        raise HTTPException(status_code=400, detail="Capital must be positive.")
    user, broker_client, _source = await _request_broker_context(request)
    user_id = int((user or {}).get("id") or _request_user_id(request))
    scan_date = datetime.now(IST).date().isoformat()
    settings_key = f"{scan_date}:{capital_inr:.0f}:{min_price:.0f}:{limit}"
    cache_key = f"{user_id}:{settings_key}"
    cached = _CASCADE_SCAN_CACHE.get(cache_key)
    if cached and not refresh and time.time() - cached[0] < _CASCADE_SCAN_TTL_SEC:
        return {**cached[1], "cached": True}

    if not refresh:
        snapshot = await _load_terminal_cascade_scan_snapshot(user_id, settings_key)
        if snapshot is not None:
            _CASCADE_SCAN_CACHE[cache_key] = (time.time(), snapshot)
            return {**snapshot, "cached": True}

    if load_only:
        return {"status": "empty", "cached": False, "scan_date": scan_date}

    if broker_client is None:
        raise HTTPException(status_code=400, detail="Connect a Dhan account to run the Cascade scanner.")

    semaphore = asyncio.Semaphore(_CASCADE_SCAN_CONCURRENCY)
    stocks = [_resolve_terminal_stock(row["symbol"]) for row in TERMINAL_STOCKS]
    loaded = await asyncio.gather(*(_cascade_scan_history(broker_client, row, semaphore) for row in stocks))
    rows = [row for row in loaded if row is not None]

    candidates, rejected = cascade_scan(
        rows,
        capital_inr=float(capital_inr),
        min_price=float(min_price),
        limit=int(limit),
    )
    payload = {
        "status": "ok",
        "scanned_at": datetime.now(IST).isoformat(),
        "scan_date": scan_date,
        "capital_inr": float(capital_inr),
        "universe": len(stocks),
        "with_history": len(rows),
        "no_history": len(stocks) - len(rows),
        "candidates": [
            {
                "symbol": row.symbol,
                "name": row.name,
                "last_price": row.last_price,
                "strength_pct": row.strength_pct,
                "pullback_pct": row.pullback_pct,
                "recent_high": row.recent_high,
                "affordable_shares": row.affordable_shares,
                "rungs_fundable": row.rungs_fundable,
                "score": row.score,
                "etf": row.etf,
            }
            for row in candidates
        ],
        # Kept so an empty list can explain itself instead of looking like a bug.
        "rejected_sample": [{"symbol": row.symbol, "reason": row.reason} for row in rejected[:12]],
        "rejected_total": len(rejected),
        "cached": False,
    }
    _CASCADE_SCAN_CACHE[cache_key] = (time.time(), payload)
    try:
        await _db_mod.set_app_state(
            _terminal_cascade_scan_state_key(user_id, settings_key),
            json.dumps(payload, default=str),
        )
    except Exception as exc:
        # The fresh result remains useful even if durable storage is briefly
        # unavailable; the in-memory day cache still prevents another scan.
        _logger.warning("[TERMINAL CASCADE] Could not save scanner snapshot for user %s: %s", user_id, exc)
    return payload


@app.get("/api/terminal/cascade/scan/chart")
async def terminal_cascade_scan_chart(request: Request, symbol: str, sessions: int = 90):
    """Real daily OHLC for one scanned scrip, plus the levels the rank is based on.

    Native exchange OHLC only.  The whole point of the scanner is to justify a
    ranking, and a smoothed or synthesised series would be justifying something
    other than what the ranking was computed from.
    """
    stock = _resolve_terminal_stock(symbol)
    if not stock.get("security_id"):
        raise HTTPException(status_code=400, detail=f"No Dhan security ID found for {stock['symbol']}")
    _user, broker_client, _source = await _request_broker_context(request)
    if broker_client is None:
        raise HTTPException(status_code=400, detail="Connect a Dhan account to load the scanner chart.")

    sessions = max(20, min(int(sessions or 90), 250))
    today = datetime.now(IST).date()
    try:
        frame = await asyncio.to_thread(
            broker_client.get_historical_data,
            security_id=str(stock["security_id"]),
            exchange_segment=str(stock["exchange_segment"]),
            instrument_type=str(stock["instrument_type"]),
            from_date=(today - timedelta(days=int(sessions * 1.6) + 30)).isoformat(),
            to_date=today.isoformat(),
            candle_type="D",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Dhan did not return candles for {stock['symbol']}: {exc}"
        ) from exc
    if frame is None or getattr(frame, "empty", True):
        raise HTTPException(status_code=404, detail=f"No daily candles returned for {stock['symbol']}.")

    rows = [
        {
            "t": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
            "o": float(row["open"]),
            "h": float(row["high"]),
            "l": float(row["low"]),
            "c": float(row["close"]),
        }
        for timestamp, row in frame.iterrows()
    ][-sessions:]

    # The same 20-session window the ranking measures the pullback against, so
    # the line on the chart is the number in the table and not a lookalike.
    window = rows[-CASCADE_SCAN_HIGH_LOOKBACK:] if len(rows) >= CASCADE_SCAN_HIGH_LOOKBACK else rows
    recent_high = max(item["h"] for item in window)
    last_price = rows[-1]["c"]
    return {
        "status": "ok",
        "symbol": stock["symbol"],
        "name": stock.get("name") or stock["symbol"],
        "chart_mode": "native_ohlc",
        "candles": rows,
        "recent_high": round(recent_high, 2),
        "recent_high_lookback": CASCADE_SCAN_HIGH_LOOKBACK,
        "last_price": round(last_price, 2),
        "pullback_pct": round((recent_high - last_price) / recent_high * 100.0, 2) if recent_high else 0.0,
    }


@app.get("/api/terminal/nifty200")
@app.get("/api/terminal/nifty100")
@app.get("/api/terminal/nifty50")
async def terminal_nifty200():
    stocks = [_resolve_terminal_stock(stock["symbol"]) for stock in TERMINAL_STOCKS]
    return {"status": "ok", "count": len(stocks), "data": stocks}


@app.get("/api/terminal/quote")
async def terminal_quote(symbol: str, request: Request):
    stock = _resolve_terminal_stock(symbol)
    if not stock["security_id"]:
        return {"status": "error", "message": f"No Dhan security ID found for {stock['symbol']}", "stock": stock}
    _, broker_client, _ = await _request_broker_context(request)
    if not broker_client:
        return {"status": "error", "message": "Broker not configured", "stock": stock}
    try:
        data = broker_client.get_ltp([stock["security_id"]], exchange_segment=stock["exchange_segment"])
        ltp = _extract_marketfeed_ltp(data, stock["exchange_segment"], stock["security_id"])
        return {"status": "ok", "stock": stock, "ltp": ltp}
    except Exception as e:
        return {"status": "error", "message": str(e), "stock": stock}


@app.post("/api/terminal/order")
async def terminal_place_order(req: StockTerminalOrderRequest, request: Request):
    check_rate_limit("terminal_place_order", _request_rate_subject(request), max_calls=3, window_sec=5)
    stock = _resolve_terminal_stock(req.symbol)
    if not stock["security_id"]:
        raise HTTPException(status_code=400, detail=f"No Dhan security ID found for {stock['symbol']}")

    transaction_type = str(req.transaction_type or "").upper()
    order_type = str(req.order_type or "MARKET").upper()
    product_type = str(req.product_type or "INTRADAY").upper()
    validity = str(req.validity or "DAY").upper()
    if transaction_type not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="transaction_type must be BUY or SELL")
    if order_type not in {"MARKET", "LIMIT", "STOP_LOSS", "STOP_LOSS_MARKET"}:
        raise HTTPException(status_code=400, detail="Unsupported Dhan order_type")
    if product_type not in {"CNC", "INTRADAY", "MARGIN", "MTF", "CO", "BO"}:
        raise HTTPException(status_code=400, detail="Unsupported Dhan product_type")
    if validity not in {"DAY", "IOC"}:
        raise HTTPException(status_code=400, detail="validity must be DAY or IOC")
    if req.disclosed_quantity > req.quantity:
        raise HTTPException(status_code=400, detail="disclosed_quantity cannot exceed quantity")
    amo_time = str(req.amo_time or "").upper()
    if req.after_market_order and amo_time not in _AMO_TIMES:
        raise HTTPException(status_code=400, detail="Unsupported AMO time")
    if order_type in {"LIMIT", "STOP_LOSS"} and req.price <= 0:
        raise HTTPException(status_code=400, detail=f"{order_type} requires price")
    if order_type in {"STOP_LOSS", "STOP_LOSS_MARKET"} and req.trigger_price <= 0:
        raise HTTPException(status_code=400, detail=f"{order_type} requires trigger_price")

    user, broker_client, source = await _request_broker_context(request)
    if not broker_client:
        raise HTTPException(status_code=400, detail=_broker_not_configured_message(user, source))
    try:
        result = broker_client.place_order(
            security_id=stock["security_id"],
            exchange_segment=stock["exchange_segment"],
            transaction_type=transaction_type,
            quantity=req.quantity,
            order_type=order_type,
            product_type=product_type,
            price=req.price,
            trigger_price=req.trigger_price,
            validity=validity,
            disclosed_quantity=req.disclosed_quantity,
            after_market_order=req.after_market_order,
            amo_time=amo_time,
            bo_profit_value=req.bo_profit_value,
            bo_stop_loss_value=req.bo_stop_loss_value,
            slice_order=req.slice_order,
            tag=f"PFSTK_{stock['symbol']}",
        )
        return {"status": "ok", "stock": stock, "response": result}
    except Exception as e:
        detail = _broker_order_failure_detail(e, "Stock terminal order failed")
        alerter.alert(
            "Stock Terminal Order Failed",
            f"Symbol: {stock['symbol']}\nType: {transaction_type}\nQty: {req.quantity}\nError: {detail['reason']}",
        )
        raise HTTPException(status_code=502 if isinstance(e, DhanOrderError) else 500, detail=detail)


@app.post("/api/terminal/gtt")
async def terminal_place_gtt(req: StockTerminalGttRequest, request: Request):
    check_rate_limit("terminal_place_gtt", _request_rate_subject(request), max_calls=3, window_sec=5)
    stock = _resolve_terminal_stock(req.symbol)
    if not stock["security_id"]:
        raise HTTPException(status_code=400, detail=f"No Dhan security ID found for {stock['symbol']}")

    transaction_type = str(req.transaction_type or "").upper()
    order_flag = str(req.order_flag or "SINGLE").upper()
    order_type = str(req.order_type or "LIMIT").upper()
    product_type = str(req.product_type or "CNC").upper()
    validity = str(req.validity or "DAY").upper()
    if transaction_type not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="transaction_type must be BUY or SELL")
    if order_flag not in {"SINGLE", "OCO"}:
        raise HTTPException(status_code=400, detail="order_flag must be SINGLE or OCO")
    if order_type not in {"MARKET", "LIMIT"}:
        raise HTTPException(status_code=400, detail="Forever orders support MARKET or LIMIT")
    if product_type not in {"CNC", "MTF"}:
        raise HTTPException(status_code=400, detail="Forever orders support CNC or MTF")
    if validity not in {"DAY", "IOC"}:
        raise HTTPException(status_code=400, detail="validity must be DAY or IOC")
    if req.trigger_price <= 0:
        raise HTTPException(status_code=400, detail="GTT trigger_price is required")
    if order_type == "LIMIT" and req.price <= 0:
        raise HTTPException(status_code=400, detail="GTT LIMIT requires price")
    if order_flag == "OCO" and (req.trigger_price1 <= 0 or req.price1 <= 0):
        raise HTTPException(status_code=400, detail="OCO requires target price and target trigger")
    if req.disclosed_quantity > req.quantity:
        raise HTTPException(status_code=400, detail="disclosed_quantity cannot exceed quantity")
    if req.quantity1 > req.quantity:
        raise HTTPException(status_code=400, detail="quantity1 cannot exceed quantity")

    user, broker_client, source = await _request_broker_context(request)
    if not broker_client:
        raise HTTPException(status_code=400, detail=_broker_not_configured_message(user, source))
    try:
        result = broker_client.place_forever_order(
            security_id=stock["security_id"],
            exchange_segment=stock["exchange_segment"],
            transaction_type=transaction_type,
            quantity=req.quantity,
            order_flag=order_flag,
            product_type=product_type,
            order_type=order_type,
            validity=validity,
            price=req.price,
            trigger_price=req.trigger_price,
            price1=req.price1,
            trigger_price1=req.trigger_price1,
            quantity1=req.quantity1,
            disclosed_quantity=req.disclosed_quantity,
            tag=f"PFGTT_{stock['symbol']}",
        )
        return {"status": "ok", "stock": stock, "response": result}
    except Exception as e:
        detail = _broker_order_failure_detail(e, "Stock terminal GTT failed")
        alerter.alert(
            "Stock Terminal GTT Failed",
            f"Symbol: {stock['symbol']}\nType: {transaction_type}\nQty: {req.quantity}\nError: {detail['reason']}",
        )
        raise HTTPException(status_code=502 if isinstance(e, DhanOrderError) else 500, detail=detail)


@app.get("/api/terminal/forever")
async def terminal_forever_orders(request: Request):
    try:
        user, broker_client, source = await _request_broker_context(request)
        if not broker_client:
            return {"status": "not_configured", "message": _broker_not_configured_message(user, source), "data": []}
        orders = broker_client.get_forever_orders()
        return {"status": "success", "data": orders if isinstance(orders, list) else []}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200], "data": []}


@app.delete("/api/terminal/forever/{order_id}")
async def terminal_cancel_forever(order_id: str, request: Request):
    user, broker_client, source = await _request_broker_context(request)
    if not broker_client:
        raise HTTPException(status_code=400, detail=_broker_not_configured_message(user, source))
    try:
        return broker_client.cancel_forever_order(order_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Strategy CRUD ─────────────────────────────────────────────────
STRAT_FILE = "strategies.json"
RUNS_FILE = "runs.json"


async def _persist_daily_trades(trades: list, user_id: int):
    """Auto-save today's real Dhan trade P&L summary to SQLite.

    Only overwrites existing entry if the new data has MORE trade legs
    (i.e., more complete data from later in the day).
    """
    if not trades:
        return
    today_str = _ist_date_str()
    entry = _summarize_real_trade_fills(trades)
    if not entry:
        return
    trade_legs = entry.get("trade_legs", 0)

    # Only overwrite if new data has more trade legs (more complete)
    existing = await _db_mod.get_trade_history_entry(user_id, today_str) or {}
    existing_legs = existing.get("trade_legs", existing.get("trades", 0))
    if existing_legs > trade_legs or (
        str(existing.get("source") or "") == "historical_fifo" and existing_legs >= trade_legs
    ):
        print(f"[TRADE_HISTORY] Skipping update — existing has {existing_legs} legs vs new {trade_legs}")
        return

    # Preserve historical cost splits when live get_trades() still has zero charges.
    if entry.get("total_costs", 0) == 0 and existing.get("total_costs", 0) > 0:
        entry["charges"] = existing.get("charges", 0)
        entry["brokerage"] = existing.get("brokerage", 0)
        entry["total_costs"] = existing.get("total_costs", 0)
        entry["net_pnl"] = round(float(entry.get("pnl", 0) or 0) - float(entry["total_costs"] or 0), 2)
        # Also preserve per-trade costs where possible.
        for detail in entry.get("details", []):
            for old_detail in existing.get("details", []):
                if detail["symbol"] != old_detail.get("symbol"):
                    continue
                if detail.get("charges", 0) == 0:
                    detail["charges"] = old_detail.get("charges", 0)
                if detail.get("brokerage", 0) == 0:
                    detail["brokerage"] = old_detail.get("brokerage", 0)
                detail["total_costs"] = round(
                    float(detail.get("charges", 0) or 0) + float(detail.get("brokerage", 0) or 0),
                    2,
                )
                break

    await _db_mod.upsert_trade_history_entry(user_id, today_str, entry)
    print(
        "[TRADE_HISTORY] Saved "
        f"{today_str}: {entry.get('trades', 0)} trades ({trade_legs} fills), "
        f"P&L=₹{float(entry.get('pnl', 0) or 0):.2f}, "
        f"costs=₹{float(entry.get('total_costs', 0) or 0):.2f}"
    )


def _load():
    if os.path.exists(STRAT_FILE):
        try:
            with open(STRAT_FILE, "r") as f:
                return json.load(f)
        except:
            return []
    return []


def _save(d):
    # Atomic write (tmp + rename) so a crash mid-write won't corrupt the file
    tmp = STRAT_FILE + ".tmp"
    with open(tmp, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(d, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)
    os.replace(tmp, STRAT_FILE)


def _load_runs():
    if os.path.exists(RUNS_FILE):
        try:
            with open(RUNS_FILE, "r") as f:
                return json.load(f)
        except:
            return []
    return []


def _save_runs(d):
    # Atomic write with exclusive lock so concurrent workers don't interleave
    tmp = RUNS_FILE + ".tmp"
    with open(tmp, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(d, f, indent=2, default=str)
        fcntl.flock(f, fcntl.LOCK_UN)
    os.replace(tmp, RUNS_FILE)


@app.get("/api/strategies")
async def get_strategies(request: Request):
    return await _db_mod.list_strategies(_request_user_id(request))


@app.post("/api/strategies")
async def save_strategy(strategy: dict, request: Request):
    now = str(datetime.now())
    normalized_indicators, entry_conditions, exit_conditions = _normalized_indicator_bundle(
        strategy.get("indicators"),
        strategy.get("entry_conditions"),
        strategy.get("exit_conditions"),
    )
    strategy = {
        **strategy,
        "indicators": normalized_indicators,
        "entry_conditions": entry_conditions,
        "exit_conditions": exit_conditions,
        "created_at": strategy.get("created_at") or now,
        "updated_at": strategy.get("updated_at") or now,
        "version": int(strategy.get("version", 1) or 1),
        "versions": strategy.get("versions") or [{"version": 1, "saved_at": now, "changes": "Initial save"}],
    }
    return await _db_mod.create_strategy_record(_request_user_id(request), strategy)


@app.post("/api/strategies/folders")
async def create_strategy_folder(body: dict, request: Request):
    folder = (body.get("folder") or "").strip()
    if not folder:
        raise HTTPException(status_code=400, detail="Folder name required")
    # Create a placeholder strategy so the folder persists
    now = str(datetime.now())
    placeholder = {
        "run_name": "",
        "folder": folder,
        "instrument": "",
        "legs": [],
        "entry_conditions": [],
        "exit_conditions": [],
        "created_at": now,
        "updated_at": now,
        "version": 1,
        "versions": [{"version": 1, "saved_at": now, "changes": "Folder created"}],
        "_placeholder": True,
    }
    result = await _db_mod.create_strategy_record(_request_user_id(request), placeholder)
    return {"status": "ok", "folder": folder, "id": result.get("id") if isinstance(result, dict) else None}


@app.delete("/api/strategies/{sid}")
async def delete_strategy(sid: int, request: Request):
    deleted = await _db_mod.delete_strategy_record(_request_user_id(request), sid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return {"deleted": sid}


@app.put("/api/strategies/{sid}")
async def update_strategy(sid: int, updates: dict, request: Request):
    user_id = _request_user_id(request)
    strategy = await _db_mod.get_strategy(user_id, sid)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    ver = int(strategy.get("version", 1) or 1) + 1
    versions = list(strategy.get("versions", []))
    versions.append(
        {
            "version": ver,
            "saved_at": str(datetime.now()),
            "changes": updates.get("_change_note", f"Updated to v{ver}"),
        }
    )
    if len(versions) > 20:
        versions = versions[-20:]
    updates.pop("_change_note", None)
    strategy.update(updates)
    normalized_indicators, entry_conditions, exit_conditions = _normalized_indicator_bundle(
        strategy.get("indicators"),
        strategy.get("entry_conditions"),
        strategy.get("exit_conditions"),
    )
    strategy["indicators"] = normalized_indicators
    strategy["entry_conditions"] = entry_conditions
    strategy["exit_conditions"] = exit_conditions
    strategy["version"] = ver
    strategy["versions"] = versions
    strategy["updated_at"] = str(datetime.now())
    await _db_mod.replace_strategy_record(user_id, sid, strategy)
    return {"updated": sid}


# ── Backtest Runs CRUD ────────────────────────────────────────────
@app.get("/api/runs")
async def get_runs(request: Request):
    runs = await _db_mod.list_runs(_request_user_id(request))
    result = []
    for r in runs:
        summary = {k: v for k, v in r.items() if k not in ("trades", "equity")}
        trades = r.get("trades") or []
        if trades:
            summary["first_entry_time"] = str(trades[0].get("entry_time") or "")
            summary["last_exit_time"] = str(trades[-1].get("exit_time") or "")
        result.append(summary)
    return result


@app.post("/api/runs/bulk-delete")
async def bulk_delete_runs(request: Request):
    user_id = _request_user_id(request)
    body = await request.json()
    ids = body.get("ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="ids must be a non-empty list")
    deleted = await _db_mod.bulk_delete_run_records(user_id, ids)
    return {"deleted": deleted}


@app.post("/api/runs/cleanup-empty")
async def cleanup_empty_runs(request: Request):
    """Remove all 0-trade paper/live runs for the current user."""
    user_id = _request_user_id(request)
    removed = await _db_mod.cleanup_empty_runs(user_id)
    remaining = len(await _db_mod.list_runs(user_id))
    return {"removed": removed, "remaining": remaining}


@app.get("/api/runs/{rid}")
async def get_run(rid: int, request: Request):
    run = await _db_mod.get_run(_request_user_id(request), rid)
    if run:
        return run
    raise HTTPException(status_code=404, detail="Run not found")


@app.delete("/api/runs/{rid}")
async def delete_run(rid: int, request: Request):
    deleted = await _db_mod.delete_run_record(_request_user_id(request), rid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"deleted": rid}


@app.put("/api/runs/{rid}")
async def update_run(rid: int, request: Request):
    """Update run metadata (run_name, folder)."""
    user_id = _request_user_id(request)
    body = await request.json()
    run = await _db_mod.get_run(user_id, rid)
    if run:
        if "run_name" in body:
            run["run_name"] = str(body["run_name"]).strip()
        if "folder" in body:
            run["folder"] = str(body["folder"]).strip()
        await _db_mod.replace_run_record(user_id, rid, run)
        return {"updated": rid, "run_name": run.get("run_name"), "folder": run.get("folder")}
    raise HTTPException(status_code=404, detail="Run not found")


@app.get("/api/runs/{rid}/csv")
async def export_run_csv(rid: int, request: Request):
    """Export backtest trades to CSV"""
    import csv
    import io

    run = await _db_mod.get_run(_request_user_id(request), rid)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    trades = run.get("trades", [])
    if not trades:
        raise HTTPException(status_code=404, detail="No trades in this run")
    output = io.StringIO()
    fields = [
        "id",
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "pnl",
        "cumulative",
        "exit_reason",
        "option_type",
        "strike",
        "qty",
        "txn_type",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for t in trades:
        writer.writerow(t)
    output.seek(0)
    name = run.get("run_name", f"run_{rid}").replace(" ", "_")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={name}_trades.csv"},
    )


# ── Scalp Trades CRUD (SQLite-backed) ────────────────────────────
@app.get("/api/scalp/trades")
async def get_scalp_trades(request: Request):
    """Return all persisted closed scalp trades for the current user."""
    return await _db_mod.list_scalp_trades(_request_user_id(request))


@app.post("/api/scalp/trades/bulk-delete")
async def bulk_delete_scalp_trades(request: Request):
    """Bulk-delete scalp trades by trade_id list."""
    user_id = _request_user_id(request)
    body = await request.json()
    ids = body.get("ids", [])
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="ids must be a non-empty list")
    deleted = await _db_mod.bulk_delete_scalp_trades(user_id, ids)
    eng = _scalp_engines.get(int(user_id))
    if eng is not None:
        id_set = {int(tid) for tid in ids}
        eng.closed_trades = [t for t in eng.closed_trades if t.get("trade_id") not in id_set]
    _notify_scalp_ws()
    return {"deleted": deleted}


@app.delete("/api/scalp/trades/{tid}")
async def delete_scalp_trade(tid: int, request: Request):
    """Delete a single persisted scalp trade by trade_id."""
    user_id = _request_user_id(request)
    await _db_mod.delete_scalp_trade(user_id, tid)
    eng = _scalp_engines.get(int(user_id))
    if eng is not None:
        eng.closed_trades = [t for t in eng.closed_trades if t.get("trade_id") != tid]
    _notify_scalp_ws()
    return {"deleted": tid}


# ── Scalp Engine (live session, in-memory) ───────────────────────


def _get_scalp_engine(user_id: int | None = None, broker_client: DhanClient | None = None):
    if not _HAS_SCALP:
        raise HTTPException(status_code=503, detail="scalp.py not available")
    owner_id = int(user_id or 0)
    if owner_id <= 0:
        raise HTTPException(status_code=400, detail="Missing scalp engine user context")
    eng = _scalp_engines.get(owner_id)
    if eng is None:

        async def _persist_closed_trade_async(owner_id: int, trade_dict: dict):
            try:
                await _db_mod.create_scalp_trade(owner_id, trade_dict)
            except Exception as e:
                print(f"[SCALP] Failed to persist closed trade for user {owner_id}: {e}")
            finally:
                await _save_scalp_open_state(owner_id, _scalp_engines.get(owner_id), force=True)
                _notify_scalp_ws()

        def _persist_closed_trade(trade_dict):
            if owner_id:
                asyncio.create_task(_persist_closed_trade_async(owner_id, trade_dict))
            else:
                print("[SCALP] Skipping closed-trade persistence — no owner user_id available")
            # Telegram alert for every scalp exit (manual, target, SL, sqoff)
            pnl = trade_dict.get("pnl", 0)
            sym = (
                f"{trade_dict.get('underlying', '?')} {trade_dict.get('strike', '')}{trade_dict.get('option_type', '')}"
            )
            reason = trade_dict.get("exit_reason", "unknown")
            entry_p = trade_dict.get("entry_premium", 0)
            exit_p = trade_dict.get("exit_premium", 0)
            pnl_sign = "+" if pnl >= 0 else ""
            level = "info" if pnl >= 0 else "error"
            alerter.alert(
                f"Scalp Exit [{reason}]",
                f"Symbol: {sym}\n"
                f"Entry: \u20b9{entry_p:.2f} \u2192 Exit: \u20b9{exit_p:.2f}\n"
                f"P&L: {pnl_sign}\u20b9{pnl:.2f}",
                level=level,
            )

        eng = _ScalpEngineClass(broker_client or dhan, _market_feed, on_trade_close=_persist_closed_trade)
        eng._user_id = owner_id
        eng._trade_counter = max(eng._trade_counter, _db_mod.get_max_scalp_trade_id_sync(owner_id))
        _scalp_engines[owner_id] = eng
    elif broker_client is not None:
        eng.dhan = broker_client
    return eng


@app.get("/api/scalp/status")
async def get_scalp_status(request: Request):
    user_id = _request_user_id(request)
    eng = _scalp_engines.get(user_id)
    if eng is not None:
        status = eng.get_status()
    else:
        status = {
            "running": False,
            "open_trades": [],
            "closed_trades": [],
            "event_log": [],
            "total_pnl": 0.0,
            "closed_pnl": 0.0,
            "open_pnl": 0.0,
            "session_pnl": 0.0,
        }
        # A broker connection can be temporarily unavailable during startup.
        # Show the saved positions without constructing or starting an engine;
        # the active worker will perform the real monitored restore separately.
        raw = await _db_mod.get_app_state(_scalp_open_state_key(user_id))
        try:
            persisted = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            persisted = {}
        rows = persisted.get("open_trades") if isinstance(persisted, dict) else None
        if isinstance(rows, list) and rows:
            status["open_trades"] = rows
            status["event_log"] = list(persisted.get("event_log") or [])[-100:]
            status["recovery_pending"] = True
    file_trades = await _db_mod.list_scalp_trades(user_id)
    status["file_trades"] = list(reversed(file_trades))
    return status


@app.post("/api/scalp/start")
async def start_scalp_engine(request: Request):
    user_id = _request_user_id(request)
    eng = _get_scalp_engine(user_id)
    await _restore_scalp_open_state(user_id, eng)
    eng.start()
    await _save_scalp_open_state(user_id, eng, force=True)
    _notify_scalp_ws()
    return {"status": "started"}


@app.post("/api/scalp/stop")
async def stop_scalp_engine(request: Request):
    user_id = _request_user_id(request)
    eng = _get_scalp_engine(user_id)
    await _restore_scalp_open_state(user_id, eng)
    sqoff = await _square_off_scalp_engine_trades(eng)
    if not sqoff.get("ok"):
        return {
            "status": sqoff.get("status", "error"),
            "message": "Scalp stop aborted because broker exits are not fully confirmed yet.",
            "square_off": sqoff,
            "engine_status": eng.get_status(),
        }
    await _save_scalp_run_to_history(eng, explicit_user_id=user_id)
    eng.stop()
    await _save_scalp_open_state(user_id, eng, force=True)
    _notify_scalp_ws()
    return {"status": "stopped", "square_off": sqoff}


class ScalpEntryReq(BaseModel):
    underlying: str = Field(min_length=1, max_length=20)
    strike: int = Field(gt=0, le=1_000_000)
    option_type: str = Field(min_length=2, max_length=2)
    expiry: str = Field(min_length=10, max_length=10)
    product_type: str = Field(default="INTRADAY", min_length=2, max_length=16)
    transaction_type: str = Field(default="BUY", min_length=3, max_length=4)
    lots: int = Field(default=1, ge=1, le=500)
    lot_size: int = Field(default=75, ge=1, le=10_000)
    target_premium: float = Field(default=0.0, ge=0, le=1_000_000)
    sl_premium: float = Field(default=0.0, ge=0, le=1_000_000)
    target_pct: float = Field(default=0.0, ge=0, le=1_000)
    sl_pct: float = Field(default=0.0, ge=0, le=1_000)
    target_rupees: float = Field(default=0.0, ge=0, le=1_000_000_000)
    sl_rupees: float = Field(default=0.0, ge=0, le=1_000_000_000)
    sqoff_time: str = Field(default="", max_length=8)
    mode: str = Field(default="live", min_length=4, max_length=5)
    entry_limit_price: float = Field(default=0.0, ge=0, le=1_000_000)
    entry_limit_max: float = Field(default=0.0, ge=0, le=1_000_000)


_scalp_entry_locks: Dict[int, asyncio.Lock] = {}
_last_scalp_entry_ts: Dict[int, float] = {}

_SCALP_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}


def _validate_scalp_entry_request(req: ScalpEntryReq) -> None:
    req.underlying = str(req.underlying or "").strip().upper()
    req.option_type = str(req.option_type or "").strip().upper()
    req.transaction_type = str(req.transaction_type or "").strip().upper()
    req.product_type = str(req.product_type or "").strip().upper()
    req.mode = str(req.mode or "").strip().lower()
    if req.underlying not in _SCALP_UNDERLYINGS:
        raise HTTPException(status_code=400, detail="Unsupported scalp underlying")
    if req.option_type not in {"CE", "PE"}:
        raise HTTPException(status_code=400, detail="option_type must be CE or PE")
    if req.transaction_type not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="transaction_type must be BUY or SELL")
    if req.product_type in {"NORMAL", "NRML"}:
        req.product_type = "MARGIN"
    if req.product_type not in {"INTRADAY", "MARGIN"}:
        raise HTTPException(status_code=400, detail="Scalp product must be INTRADAY or MARGIN")
    if req.mode not in {"paper", "live"}:
        raise HTTPException(status_code=400, detail="Scalp mode must be paper or live")
    try:
        expiry_date = date.fromisoformat(req.expiry)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="expiry must be a real YYYY-MM-DD date") from exc
    if expiry_date < datetime.now(IST).date():
        raise HTTPException(status_code=400, detail="expiry cannot be in the past")
    if bool(req.entry_limit_price) != bool(req.entry_limit_max):
        raise HTTPException(status_code=400, detail="Stop-limit entry requires both minimum and maximum premiums")
    if req.mode == "live" and (req.target_premium <= 0 or req.sl_premium <= 0):
        raise HTTPException(status_code=400, detail="Live scalp requires both Target Premium and SL Premium")


def _get_scalp_entry_lock(user_id: int) -> asyncio.Lock:
    return _scalp_entry_locks.setdefault(int(user_id), asyncio.Lock())


@app.post("/api/scalp/entry")
async def scalp_entry(req: ScalpEntryReq, request: Request):
    _validate_scalp_entry_request(req)
    user_id = _request_user_id(request)
    lock = _get_scalp_entry_lock(user_id)
    async with lock:
        # Cooldown guard INSIDE lock to prevent race condition
        now = asyncio.get_event_loop().time()
        last_ts = _last_scalp_entry_ts.get(user_id, 0.0)
        if now - last_ts < 2.0:
            return {"status": "error", "message": "Duplicate entry blocked — please wait 2 seconds between entries"}
        _last_scalp_entry_ts[user_id] = now
        broker_client = None
        if str(req.mode or "live").lower() == "live":
            user, broker_client, source = await _request_broker_context(request)
            if not broker_client:
                return {"status": "error", "message": _broker_not_configured_message(user, source)}
        eng = _get_scalp_engine(user_id, broker_client=broker_client)
        try:
            product_type = req.product_type
            result = await eng.enter_trade(
                underlying=req.underlying,
                strike=req.strike,
                option_type=req.option_type,
                expiry=req.expiry,
                product_type=product_type,
                transaction_type=req.transaction_type,
                lots=req.lots,
                lot_size=req.lot_size,
                target_premium=req.target_premium,
                sl_premium=req.sl_premium,
                target_pct=req.target_pct,
                sl_pct=req.sl_pct,
                target_rupees=req.target_rupees,
                sl_rupees=req.sl_rupees,
                sqoff_time=req.sqoff_time,
                mode=req.mode,
                entry_limit_price=req.entry_limit_price,
                entry_limit_max=req.entry_limit_max,
            )
            if result.get("status") == "error":
                alerter.alert(
                    "Scalp Entry Failed",
                    f"Symbol: {req.underlying} {req.strike}{req.option_type}\nMode: {req.mode}\nError: {result.get('message', 'unknown')}",
                )
            else:
                trade_info = result.get("trade", {})
                entry_p = trade_info.get("entry_premium", 0)
                is_pending = trade_info.get("status") == "pending"
                if is_pending:
                    alerter.alert(
                        "Scalp Stop-Limit Pending",
                        f"Symbol: {req.underlying} {req.strike}{req.option_type}\n"
                        f"Side: {req.transaction_type} | Lots: {req.lots}\n"
                        f"Trigger: ₹{req.entry_limit_price:.2f}–₹{req.entry_limit_max:.2f} | Product: {product_type} | Mode: {req.mode}",
                        level="info",
                    )
                else:
                    alerter.alert(
                        "Scalp Entry",
                        f"Symbol: {req.underlying} {req.strike}{req.option_type}\n"
                        f"Side: {req.transaction_type} | Lots: {req.lots}\n"
                        f"Entry: \u20b9{entry_p:.2f} | Product: {product_type} | Mode: {req.mode}",
                        level="info",
                    )
                await _save_scalp_open_state(user_id, eng, force=True)
            _notify_scalp_ws()
            return result
        except Exception as e:
            alerter.alert(
                "Scalp Entry Error",
                f"Symbol: {req.underlying} {req.strike}{req.option_type}\nMode: {req.mode}\nError: {e}",
            )
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/scalp/exit/{trade_id}")
async def scalp_exit(trade_id: int, request: Request):
    user_id = _request_user_id(request)
    eng = _get_scalp_engine(user_id)
    try:
        await _restore_scalp_open_state(user_id, eng)
        result = await eng.exit_trade(trade_id, reason="manual")
        if result.get("status") == "error":
            alerter.alert("Scalp Exit Failed", f"Trade ID: {trade_id}\nError: {result.get('message', 'unknown')}")
        await _save_scalp_open_state(user_id, eng, force=True)
        _notify_scalp_ws()
        return result
    except Exception as e:
        alerter.alert("Scalp Exit Error", f"Trade ID: {trade_id}\nError: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/scalp/kill-all")
async def scalp_kill_all(request: Request):
    user_id = _request_user_id(request)
    eng = _get_scalp_engine(user_id)
    try:
        await _restore_scalp_open_state(user_id, eng)
        result = await eng.kill_all_trades()
        closed = result.get("closed", 0)
        if closed > 0:
            alerter.alert("Scalp Kill All", f"Emergency exit: {closed} trade(s) closed", level="warning")
        await _save_scalp_open_state(user_id, eng, force=True)
        _notify_scalp_ws()
        return result
    except Exception as e:
        alerter.alert("Scalp Kill All Error", f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ScalpTargetsReq(BaseModel):
    target_premium: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    sl_premium: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    target_rupees: Optional[float] = Field(default=None, ge=0, le=1_000_000_000)
    sl_rupees: Optional[float] = Field(default=None, ge=0, le=1_000_000_000)
    sqoff_time: Optional[str] = Field(default=None, max_length=8)
    entry_limit_price: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    entry_limit_max: Optional[float] = Field(default=None, ge=0, le=1_000_000)


@app.put("/api/scalp/trades/{trade_id}/targets")
async def update_scalp_targets(trade_id: int, req: ScalpTargetsReq, request: Request):
    user_id = _request_user_id(request)
    eng = _get_scalp_engine(user_id)
    await _restore_scalp_open_state(user_id, eng)
    result = await eng.update_trade_targets(trade_id, **{k: v for k, v in req.dict().items() if v is not None})
    await _save_scalp_open_state(user_id, eng, force=True)
    _notify_scalp_ws()
    return result


@app.get("/api/option-ltp")
async def get_option_ltp(request: Request, underlying: str, strike: int, expiry: str, option_type: str):
    """Get live LTP for a specific option contract."""
    _, broker_client, _ = await _request_broker_context(request)
    if not broker_client:
        return {"status": "error", "message": "Broker not configured"}
    try:
        ltp = broker_client.get_option_ltp(underlying, strike, expiry, option_type)
        return {"status": "ok", "ltp": ltp}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/paper/trades/csv")
async def export_paper_trades_csv(request: Request, run_id: str = ""):
    """Export paper trading trades to CSV"""
    import csv
    import io

    paper_bucket = _registry_bucket(paper_engines, _request_user_id(request))
    engine = paper_bucket.get(run_id) if run_id else None
    if not engine:
        # Find first engine with trades
        for e in paper_bucket.values():
            if e.closed_trades:
                engine = e
                break
    if not engine or not engine.closed_trades:
        raise HTTPException(status_code=404, detail="No paper trades available")
    output = io.StringIO()
    fields = [
        "id",
        "leg_num",
        "transaction_type",
        "option_type",
        "strike",
        "entry_time",
        "exit_time",
        "entry_premium",
        "exit_premium",
        "lots",
        "lot_size",
        "pnl",
        "exit_reason",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for t in engine.closed_trades:
        row = {k: (str(v) if k in ("entry_time", "exit_time") else v) for k, v in t.items() if k in fields}
        writer.writerow(row)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=paper_trades_{datetime.now().strftime('%Y%m%d')}.csv"},
    )


# ── Live Ticker (Dhan LTP) ───────────────────────────────────────


# Ticker caching
_ticker_cache = {"data": None, "timestamp": 0, "ttl": 30}  # Cache for 30 seconds
_prev_close_cache = {"data": {}, "date": None}  # Cache prev close for the day
_vix_cache = {"price": 0, "prev_close": 0, "timestamp": 0, "ttl": 60}  # NSE VIX cache (60s)


def _ticker_json_response(payload: dict) -> JSONResponse:
    """Return ticker payloads with explicit no-store headers.

    The topbar ticker is time-sensitive and should never be served from a stale
    browser/intermediate cache after deploys or after-hours fallbacks.
    """

    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _fetch_nse_vix() -> dict:
    """Fetch India VIX from NSE allIndices API. Returns {price, prev_close} or cached."""
    now = time.time()
    if _vix_cache["price"] > 0 and (now - _vix_cache["timestamp"]) < _vix_cache["ttl"]:
        return {"price": _vix_cache["price"], "prev_close": _vix_cache["prev_close"]}
    try:
        import httpx

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        with httpx.Client(headers=headers, follow_redirects=True, timeout=8) as client:
            client.get("https://www.nseindia.com")  # get cookies
            r = client.get("https://www.nseindia.com/api/allIndices")
            if r.status_code == 200:
                for idx in r.json().get("data", []):
                    if idx.get("indexSymbol") == "INDIA VIX":
                        price = float(idx.get("last", 0))
                        prev = float(idx.get("previousClose", 0))
                        if price > 0:
                            _vix_cache["price"] = price
                            _vix_cache["prev_close"] = prev
                            _vix_cache["timestamp"] = now
                            print(f"[TICKER] NSE VIX={price} (prev={prev})")
                            return {"price": price, "prev_close": prev}
    except Exception as e:
        print(f"[TICKER] NSE VIX fetch failed: {e}")
    return {"price": _vix_cache["price"], "prev_close": _vix_cache["prev_close"]}


def _get_prev_close(preferred_client=None):
    """Get previous trading-day close for indices. Cached per day.

    Prefer Dhan daily candles when a broker client is available; fall back to
    yfinance only if Dhan history cannot be fetched.
    """
    from datetime import date

    today = date.today()
    if _prev_close_cache["date"] == str(today) and _prev_close_cache["data"]:
        return _prev_close_cache["data"]

    result = {}

    if preferred_client and preferred_client._is_configured():
        try:
            from_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            to_date = today.strftime("%Y-%m-%d")
            index_specs = {"nifty": "13", "sensex": "51"}
            for key, security_id in index_specs.items():
                df = preferred_client.get_historical_data(
                    security_id=security_id,
                    exchange_segment="IDX_I",
                    instrument_type="INDEX",
                    from_date=from_date,
                    to_date=to_date,
                    candle_type="D",
                )
                if df is None or df.empty:
                    continue
                df = df.sort_index()
                latest_close = float(df["close"].iloc[-1])
                latest_bar = df.index[-1]
                latest_bar_date = latest_bar.date() if hasattr(latest_bar, "date") else today
                if len(df) >= 2 and latest_bar_date >= today:
                    prev_close = float(df["close"].iloc[-2])
                else:
                    prev_close = latest_close
                result[key] = prev_close
                result[f"{key}_ltp"] = latest_close
            if result:
                _prev_close_cache["data"] = result
                _prev_close_cache["date"] = str(today)
                print(f"[TICKER] Prev close from Dhan daily candles (cached for today): {result}")
                return result
        except Exception as e:
            print(f"[TICKER] Dhan prev close fetch failed: {e}")

    try:
        import yfinance as yf

        for sym, key in [("^NSEI", "nifty"), ("^BSESN", "sensex")]:
            hist = yf.Ticker(sym).history(period="5d")
            hist = hist.dropna(subset=["Close"])
            if hist.empty:
                continue
            latest_close = float(hist["Close"].iloc[-1])
            latest_bar = hist.index[-1]
            latest_bar_date = latest_bar.date() if hasattr(latest_bar, "date") else today
            if len(hist) >= 2 and latest_bar_date >= today:
                prev_close = float(hist["Close"].iloc[-2])
            else:
                prev_close = latest_close
            result[key] = prev_close
            result[f"{key}_ltp"] = latest_close
        _prev_close_cache["data"] = result
        _prev_close_cache["date"] = str(today)
        print(f"[TICKER] Prev close from yfinance (cached for today): {result}")
        return result
    except Exception as e:
        print(f"[TICKER] Prev close fetch failed: {e}")
        return {}


def _is_cash_market_closed_ist() -> bool:
    """True outside normal Indian cash-market hours (used for after-hours ticker fallback)."""

    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    if now.weekday() >= 5:
        return True
    current = now.time()
    return current < dt_time(9, 15) or current > dt_time(15, 30)


def _historical_price_snapshot(
    client,
    *,
    security_id: int | str,
    exchange_segment: str,
    instrument_type: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """Return the latest close and change from Dhan historical candles."""

    try:
        df = client.get_historical_data(
            security_id=str(security_id),
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
            from_date=from_date,
            to_date=to_date,
            candle_type="D",
        )
    except Exception as e:
        print(f"[TICKER] Historical snapshot failed for {security_id} ({exchange_segment}/{instrument_type}): {e}")
        return {"price": 0.0, "change": 0.0, "pct": 0.0}

    if df is None or df.empty or "close" not in df:
        return {"price": 0.0, "change": 0.0, "pct": 0.0}

    df = df.sort_index()
    latest_close = float(df["close"].iloc[-1] or 0)
    if latest_close <= 0:
        return {"price": 0.0, "change": 0.0, "pct": 0.0}
    prev_close = float(df["close"].iloc[-2] or latest_close) if len(df) >= 2 else latest_close
    change = round(latest_close - prev_close, 2)
    pct = round(((latest_close - prev_close) / prev_close) * 100, 2) if prev_close > 0 else 0.0
    return {"price": round(latest_close, 2), "change": change, "pct": pct}


def _build_historical_ticker_payload(ticker_client, ticker_source: str, *, ce_sid=None, pe_sid=None) -> dict | None:
    """Build a topbar ticker payload from Dhan historical candles."""

    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    from_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")

    index_specs = {
        "nifty": ("13", "IDX_I", "INDEX"),
        "banknifty": ("25", "IDX_I", "INDEX"),
        "midcpnifty": ("49", "IDX_I", "INDEX"),
        "sensex": ("51", "IDX_I", "INDEX"),
    }
    snapshots = {
        key: _historical_price_snapshot(
            ticker_client,
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
            from_date=from_date,
            to_date=to_date,
        )
        for key, (security_id, exchange_segment, instrument_type) in index_specs.items()
    }
    if snapshots["nifty"]["price"] <= 0:
        return None

    atm_ce = {"price": 0.0, "change": 0.0, "pct": 0.0}
    atm_pe = {"price": 0.0, "change": 0.0, "pct": 0.0}
    if ce_sid:
        atm_ce = _historical_price_snapshot(
            ticker_client,
            security_id=ce_sid,
            exchange_segment="NSE_FNO",
            instrument_type="OPTIDX",
            from_date=from_date,
            to_date=to_date,
        )
    if pe_sid:
        atm_pe = _historical_price_snapshot(
            ticker_client,
            security_id=pe_sid,
            exchange_segment="NSE_FNO",
            instrument_type="OPTIDX",
            from_date=from_date,
            to_date=to_date,
        )

    vix_data = _fetch_nse_vix()
    vix_ltp = float(vix_data.get("price", 0) or 0)
    vix_prev = float(vix_data.get("prev_close", 0) or 0)
    v_chg = round(vix_ltp - vix_prev, 2) if vix_prev > 0 else 0
    v_pct = round(((vix_ltp - vix_prev) / vix_prev) * 100, 2) if vix_prev > 0 else 0

    return {
        "status": "ok",
        "source": "dhan_historical",
        "broker_source": ticker_source,
        "nifty": snapshots["nifty"],
        "banknifty": snapshots["banknifty"],
        "midcpnifty": snapshots["midcpnifty"],
        "sensex": snapshots["sensex"],
        "vix": {"price": round(vix_ltp, 2), "change": v_chg, "pct": v_pct},
        "atmCE": atm_ce,
        "atmPE": atm_pe,
    }


@app.get("/api/ticker")
async def get_ticker(request: Request):
    """Fetch live index + ATM prices — Dhan OHLC (single call), change% from yfinance prev close"""
    global _ticker_cache

    # Return cached data if still valid
    if _ticker_cache["data"] and (time.time() - _ticker_cache["timestamp"]) < _ticker_cache["ttl"]:
        return _ticker_json_response(_ticker_cache["data"])

    # ── PRIMARY: Dhan OHLC API (one call for LTP + ATM CE/PE) ──
    broker_client = None
    try:
        _, broker_client, _ = await _request_broker_context(request)
    except Exception as e:
        print(f"[TICKER] Broker context unavailable: {e}")

    ticker_clients = []
    if broker_client and broker_client._is_configured():
        ticker_clients.append(("user", broker_client))
    if dhan._is_configured() and dhan is not broker_client:
        ticker_clients.append(("global", dhan))

    market_closed = _is_cash_market_closed_ist()

    for ticker_source, ticker_client in ticker_clients:
        try:
            print(f"[TICKER] Fetching from Dhan OHLC API ({ticker_source})...")

            # Resolve ATM option security IDs FIRST (no API call)
            ce_sid, pe_sid, atm_strike = None, None, 0
            try:
                ScripMaster.ensure_loaded()
                expiry = ScripMaster.get_nearest_expiry("NIFTY")
                if expiry:
                    last_nifty = 0
                    if _ticker_cache["data"]:
                        last_nifty = _ticker_cache["data"].get("nifty", {}).get("price", 0)
                    if last_nifty <= 0 and market_closed:
                        prev = _get_prev_close(ticker_client)
                        last_nifty = prev.get("nifty_ltp", 0) or prev.get("nifty", 0)
                    if last_nifty <= 0:
                        last_nifty = 24500
                    atm_strike = round(last_nifty / 50) * 50
                    ce_sid = ScripMaster.lookup("NIFTY", atm_strike, expiry, "CE")
                    pe_sid = ScripMaster.lookup("NIFTY", atm_strike, expiry, "PE")
                    print(f"[TICKER] ATM strike={atm_strike}, CE_sid={ce_sid}, PE_sid={pe_sid}, expiry={expiry}")
            except Exception as e:
                print(f"[TICKER] ATM lookup error: {e}")

            # SINGLE Dhan API call: IDX_I + NSE_FNO together
            # sid 13=NIFTY, 25=BANKNIFTY, 49=MIDCPNIFTY, 51=SENSEX (IDX_I). VIX from yfinance.
            segments = {"IDX_I": [13, 25, 49, 51]}
            if ce_sid and pe_sid:
                segments["NSE_FNO"] = [int(ce_sid), int(pe_sid)]

            all_data = ticker_client.get_ohlc_multi(segments)

            idx = all_data.get("IDX_I", {})
            fno = all_data.get("NSE_FNO", {})

            def _extract_ltp(d, sid):
                info = d.get(str(sid), {})
                if isinstance(info, dict):
                    return float(info.get("last_price", 0))
                return 0.0

            def _extract_prev_close(d, sid):
                """Extract previous day close from Dhan OHLC response (ohlc.close = prev day close)."""
                info = d.get(str(sid), {})
                if isinstance(info, dict):
                    ohlc = info.get("ohlc", {})
                    if isinstance(ohlc, dict):
                        return float(ohlc.get("close", 0))
                return 0.0

            nifty_ltp = _extract_ltp(idx, 13)
            banknifty_ltp = _extract_ltp(idx, 25)
            midcpnifty_ltp = _extract_ltp(idx, 49)
            sensex_ltp = _extract_ltp(idx, 51)

            if nifty_ltp > 0:
                # ATM check
                correct_atm = round(nifty_ltp / 50) * 50
                if correct_atm != atm_strike and ce_sid and pe_sid:
                    print(f"[TICKER] ATM shifted {atm_strike} → {correct_atm}, will correct next cycle")

                # ATM CE/PE from same response (with change% from ohlc.close)
                atm_ce = {"price": 0, "change": 0, "pct": 0}
                atm_pe = {"price": 0, "change": 0, "pct": 0}
                if ce_sid:
                    ce_p = _extract_ltp(fno, ce_sid)
                    ce_prev = _extract_prev_close(fno, ce_sid)
                    if ce_p > 0:
                        ce_chg = round(ce_p - ce_prev, 2) if ce_prev > 0 else 0
                        ce_pct = round(((ce_p - ce_prev) / ce_prev) * 100, 2) if ce_prev > 0 else 0
                        atm_ce = {"price": round(ce_p, 2), "change": ce_chg, "pct": ce_pct}
                if pe_sid:
                    pe_p = _extract_ltp(fno, pe_sid)
                    pe_prev = _extract_prev_close(fno, pe_sid)
                    if pe_p > 0:
                        pe_chg = round(pe_p - pe_prev, 2) if pe_prev > 0 else 0
                        pe_pct = round(((pe_p - pe_prev) / pe_prev) * 100, 2) if pe_prev > 0 else 0
                        atm_pe = {"price": round(pe_p, 2), "change": pe_chg, "pct": pe_pct}
                if ce_sid or pe_sid:
                    print(f"[TICKER] ATM {atm_strike}: CE={atm_ce['price']}, PE={atm_pe['price']}")

                # Index change% from Dhan OHLC prev close (ohlc.close = prev day close)
                # Fallback to yfinance if Dhan prev close is missing
                def _chg_from_ohlc(ltp, d, sid):
                    pc = _extract_prev_close(d, sid)
                    if pc > 0:
                        return round(ltp - pc, 2), round(((ltp - pc) / pc) * 100, 2)
                    return 0, 0

                n_chg, n_pct = _chg_from_ohlc(nifty_ltp, idx, 13)
                s_chg, s_pct = _chg_from_ohlc(sensex_ltp, idx, 51)
                bn_chg, bn_pct = _chg_from_ohlc(banknifty_ltp, idx, 25)
                mc_chg, mc_pct = _chg_from_ohlc(midcpnifty_ltp, idx, 49)

                # Dhan's after-hours prev-close can flatten change to 0.00.
                # Outside market hours, prefer yfinance previous close for NIFTY/SENSEX.
                prev = (
                    _get_prev_close(ticker_client)
                    if (_is_cash_market_closed_ist() or (nifty_ltp > 0 and n_chg == 0 and n_pct == 0))
                    else {}
                )

                def _chg_yf(ltp, key, fallback_chg=0, fallback_pct=0):
                    pc = prev.get(key, 0)
                    if pc > 0 and ltp > 0:
                        return round(ltp - pc, 2), round(((ltp - pc) / pc) * 100, 2)
                    return fallback_chg, fallback_pct

                if prev:
                    if _is_cash_market_closed_ist():
                        n_chg, n_pct = _chg_yf(nifty_ltp, "nifty", n_chg, n_pct)
                        s_chg, s_pct = _chg_yf(sensex_ltp, "sensex", s_chg, s_pct)
                    else:
                        if nifty_ltp > 0 and n_chg == 0 and n_pct == 0:
                            n_chg, n_pct = _chg_yf(nifty_ltp, "nifty", n_chg, n_pct)
                        if sensex_ltp > 0 and s_chg == 0 and s_pct == 0:
                            s_chg, s_pct = _chg_yf(sensex_ltp, "sensex", s_chg, s_pct)

                # VIX from NSE India (yfinance ^INDIAVIX delisted)
                vix_data = _fetch_nse_vix()
                vix_ltp = vix_data["price"]
                vix_prev = vix_data["prev_close"]
                v_chg = round(vix_ltp - vix_prev, 2) if vix_prev > 0 else 0
                v_pct = round(((vix_ltp - vix_prev) / vix_prev) * 100, 2) if vix_prev > 0 else 0

                result = {
                    "status": "ok",
                    "source": "dhan",
                    "broker_source": ticker_source,
                    "nifty": {"price": round(nifty_ltp, 2), "change": n_chg, "pct": n_pct},
                    "banknifty": {"price": round(banknifty_ltp, 2), "change": bn_chg, "pct": bn_pct},
                    "midcpnifty": {"price": round(midcpnifty_ltp, 2), "change": mc_chg, "pct": mc_pct},
                    "sensex": {"price": round(sensex_ltp, 2), "change": s_chg, "pct": s_pct},
                    "vix": {"price": round(vix_ltp, 2), "change": v_chg, "pct": v_pct},
                    "atmCE": atm_ce,
                    "atmPE": atm_pe,
                }
                _ticker_cache["data"] = result
                _ticker_cache["timestamp"] = time.time()
                print(
                    f"[TICKER] Dhan: NIFTY={nifty_ltp} ({n_chg:+.2f}, {n_pct:+.2f}%), SENSEX={sensex_ltp}, VIX={vix_ltp}"
                )
                return _ticker_json_response(result)
            else:
                historical_result = _build_historical_ticker_payload(
                    ticker_client,
                    ticker_source,
                    ce_sid=ce_sid,
                    pe_sid=pe_sid,
                )
                if historical_result:
                    _ticker_cache["data"] = historical_result
                    _ticker_cache["timestamp"] = time.time()
                    print(
                        f"[TICKER] Historical fallback via {ticker_source}: "
                        f"NIFTY={historical_result['nifty']['price']}, "
                        f"SENSEX={historical_result['sensex']['price']}"
                    )
                    return _ticker_json_response(historical_result)
                print(f"[TICKER] Dhan returned 0 for NIFTY via {ticker_source} — trying next source...")
        except Exception as e:
            print(f"[TICKER] Dhan API failed via {ticker_source}: {type(e).__name__}: {str(e)[:100]}")
            historical_result = _build_historical_ticker_payload(
                ticker_client,
                ticker_source,
                ce_sid=ce_sid if "ce_sid" in locals() else None,
                pe_sid=pe_sid if "pe_sid" in locals() else None,
            )
            if historical_result:
                _ticker_cache["data"] = historical_result
                _ticker_cache["timestamp"] = time.time()
                print(
                    f"[TICKER] Historical fallback after error via {ticker_source}: "
                    f"NIFTY={historical_result['nifty']['price']}, "
                    f"SENSEX={historical_result['sensex']['price']}"
                )
                return _ticker_json_response(historical_result)

    # ── FALLBACK: yfinance ────────────────────────────────────
    try:
        import yfinance as yf

        print("[TICKER] Fetching from yfinance (fallback)...")

        def _last_close_and_change(symbol: str):
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="2d")
            if hist.empty:
                return 0.0, 0.0, 0.0
            close = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
            change = close - prev
            pct = (change / prev * 100) if prev else 0.0
            return close, change, pct

        nifty_price, nifty_chg, nifty_pct = _last_close_and_change("^NSEI")
        sensex_price, sensex_chg, sensex_pct = _last_close_and_change("^BSESN")
        vix_data = _fetch_nse_vix()
        vix_price = vix_data["price"]
        vix_prev = vix_data["prev_close"]
        vix_chg = round(vix_price - vix_prev, 2) if vix_prev > 0 else 0
        vix_pct = round(((vix_price - vix_prev) / vix_prev) * 100, 2) if vix_prev > 0 else 0

        if nifty_price > 0:
            result = {
                "status": "ok",
                "source": "yfinance",
                "nifty": {"price": round(nifty_price, 2), "change": round(nifty_chg, 2), "pct": round(nifty_pct, 2)},
                "sensex": {
                    "price": round(sensex_price, 2),
                    "change": round(sensex_chg, 2),
                    "pct": round(sensex_pct, 2),
                },
                "vix": {"price": round(vix_price, 2), "change": round(vix_chg, 2), "pct": round(vix_pct, 2)},
                "atmCE": {"price": 0, "change": 0, "pct": 0},
                "atmPE": {"price": 0, "change": 0, "pct": 0},
            }
            _ticker_cache["data"] = result
            _ticker_cache["timestamp"] = time.time()
            print(f"[TICKER] yfinance: NIFTY={nifty_price}, SENSEX={sensex_price}")
            return _ticker_json_response(result)

        print("[TICKER] yfinance also returned no data")
    except Exception as yf_err:
        print(f"[TICKER] yfinance fallback failed: {yf_err}")

    if _ticker_cache["data"]:
        stale = dict(_ticker_cache["data"])
        stale["stale"] = True
        print("[TICKER] Serving stale cached topbar data")
        return _ticker_json_response(stale)

    return _ticker_json_response({"status": "error", "msg": "No price data available from any source"})


# ── Expiry Dates ──────────────────────────────────────────────────
@app.get("/api/expiry-dates")
async def get_expiry_dates():
    """Return nearest expiry dates for NIFTY, BANKNIFTY, SENSEX"""
    try:
        ScripMaster.ensure_loaded()
        nifty_exp = ScripMaster.get_nearest_expiry("NIFTY") or ""
        bn_exp = ScripMaster.get_nearest_expiry("BANKNIFTY") or ""
        sensex_exp = ScripMaster.get_nearest_expiry("SENSEX") or ""
        return {
            "status": "ok",
            "nifty": nifty_exp,
            "banknifty": bn_exp,
            "sensex": sensex_exp,
        }
    except Exception as e:
        return {"status": "error", "msg": str(e)}


@app.get("/api/expiry-list/{symbol}")
async def get_expiry_list(symbol: str):
    """Return all available expiry dates for a given underlying symbol."""
    try:
        symbol = symbol.upper()
        ScripMaster.ensure_loaded()
        expiries = ScripMaster.get_expiries(symbol)
        # Only return future expiries (>= today)
        today = _ist_date_str()
        future = [e for e in expiries if e >= today]
        return {"status": "ok", "symbol": symbol, "expiries": future}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


def _refresh_recent_charges(history: dict, user_id: int, broker_client: DhanClient | None = None):
    """Re-fetch today & yesterday from Dhan historical API to fill in charges.

    The live get_trades() endpoint doesn't return charge fields (stt, sebiTax etc).
    Once those trades appear in get_trade_history(), we can update charges.
    """
    import time as _time

    try:
        client = broker_client or dhan
        today = _now_ist()
        yesterday = today - timedelta(days=1)
        # Check last 3 days (in case of weekends)
        dates_to_check = []
        for delta in range(3):
            d = (today - timedelta(days=delta)).strftime("%Y-%m-%d")
            entry = history.get(d, {})
            # Only re-fetch if entry exists but has 0 charges
            if entry and entry.get("charges", 0) == 0 and entry.get("trades", 0) > 0:
                dates_to_check.append(d)

        if not dates_to_check:
            return

        from_date = min(dates_to_check)
        to_date = max(dates_to_check)
        print(f"📊 [CHARGES] Refreshing charges for {dates_to_check}...")

        result = client.get_trade_history(from_date, to_date, 0)
        if not isinstance(result, list) or not result:
            print(f"📊 [CHARGES] No historical data available yet for {from_date} to {to_date}")
            return

        # Paginate to get all trades
        all_trades = list(result)
        page = 1
        while len(result) >= 20:  # Dhan page size
            _time.sleep(0.3)
            result = client.get_trade_history(from_date, to_date, page)
            if not isinstance(result, list) or not result:
                break
            all_trades.extend(result)
            page += 1

        # Group by date
        trades_by_date = {}
        for t in all_trades:
            raw_time = t.get("exchangeTime") or t.get("createTime") or ""
            d = str(raw_time)[:10]
            if d in dates_to_check:
                if d not in trades_by_date:
                    trades_by_date[d] = []
                trades_by_date[d].append(t)

        updated = 0
        for date_str, day_trades in trades_by_date.items():
            entry = _summarize_real_trade_fills(day_trades)
            if entry and entry.get("charges", 0) > 0:
                history[date_str] = entry
                updated += 1
                print(
                    "📊 [CHARGES] Updated "
                    f"{date_str}: charges=₹{float(entry.get('charges', 0) or 0):.2f}, "
                    f"P&L=₹{float(entry.get('pnl', 0) or 0):.2f} "
                    f"({entry.get('trades', 0)} trades, {entry.get('trade_legs', 0)} fills)"
                )

        if updated > 0:
            for date_str in trades_by_date:
                entry = history.get(date_str)
                if entry:
                    _db_mod.upsert_trade_history_entry_sync(user_id, date_str, entry)
            print(f"📊 [CHARGES] Refreshed charges for {updated} dates")
    except Exception as e:
        print(f"📊 [CHARGES] Refresh failed: {e}")


# ── Token renewal background task ────────────────────────────────
_token_renewal_task = None


async def _prefetch_scrip_master():
    """Download/refresh Scrip Master cache in background — non-blocking."""
    try:
        loaded = await asyncio.to_thread(ScripMaster.ensure_loaded)
        if loaded:
            _logger.info(f"[SCRIP] Background prefetch complete ({len(ScripMaster._options_cache)} contracts)")
        else:
            _logger.warning("[SCRIP] Background prefetch returned False — will retry on first order")
    except Exception as e:
        _logger.warning(f"[SCRIP] Background prefetch failed: {e}")


async def _bootstrap_token_renewal():
    """Refresh the startup token without blocking app readiness."""
    global _token_renewal_task
    try:
        await asyncio.to_thread(_generate_startup_token_once)
    except Exception as e:
        _logger.warning(f"[TokenManager] Startup token bootstrap failed: {e}")
    finally:
        if _token_renewal_task is None or _token_renewal_task.done():
            _token_renewal_task = asyncio.create_task(token_renewal_loop())
            print("🔄 [TokenManager] Background token renewal scheduled (every 12h)")


async def _backfill_in_background():
    """Run the blocking backfill in a thread so the event loop stays free."""
    global _backfill_state
    _backfill_state["status"] = "running"
    _backfill_state["message"] = "Fetching historical trades from Dhan..."
    loop = asyncio.get_event_loop()
    try:
        admin = await _get_preferred_admin_user()
        if not admin:
            raise RuntimeError("No admin user available for startup trade-history backfill")
        admin_id = int(admin["id"])
        broker_client, source = _resolve_user_broker_client(admin, allow_admin_fallback=True)
        if not broker_client:
            raise RuntimeError(_broker_not_configured_message(admin, source))
        history = await _db_mod.list_trade_history(admin_id)
        force = len(history) <= 2
        refresh_from_date = "2024-01-01" if force else _trade_history_refresh_start(history, "2024-01-01")
        if force:
            _backfill_state["message"] = "First-run: full backfill in progress..."
            print("📊 [BACKFILL] Auto-backfilling trade history from Dhan (force)...")
        elif refresh_from_date != "2024-01-01":
            _backfill_state["message"] = f"Refreshing trade history from {refresh_from_date}..."
        count = await loop.run_in_executor(
            None,
            lambda: _backfill_trade_history(
                refresh_from_date,
                force=force,
                user_id=admin_id,
                broker_client=broker_client,
            ),
        )
        if force:
            print(f"📊 [BACKFILL] Done — loaded {count} days of historical trades")
        else:
            loaded = await _db_mod.list_trade_history(admin_id)
            print(f"📊 [TRADE_HISTORY] {len(loaded)} days of trade data ({count} refreshed)")
        _backfill_state.update({"status": "done", "message": "Trade history up to date.", "new_dates": count})
    except Exception as e:
        print(f"📊 [BACKFILL] Startup backfill failed: {e}")
        _backfill_state.update({"status": "error", "message": str(e)})


# ── Prometheus instrumentation (must run before app starts) ────
if _PROMETHEUS_ENABLED:
    _PFI(app).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    _logger.info("[Prometheus] Metrics exposed at /metrics")


@app.on_event("startup")
async def _init_database():
    """Initialize SQLite database and auto-create admin user if needed."""
    await _db_mod.init_db()
    await _db_mod.cleanup_expired_sessions()
    admin = await _get_preferred_admin_user()
    if not admin:
        pin = _get_bootstrap_admin_password()
        if not pin:
            raise RuntimeError(
                "No admin account exists. Set PHILFORGE_PIN or PHILFORGE_PASSWORD for first-run bootstrap."
            )
        hashed = _auth_mod.hash_password(pin)
        uid = await _db_mod.create_user(config.ADMIN_USERNAME, hashed, role="admin")
        print(f"🔐 [Auth] Created admin user '{config.ADMIN_USERNAME}' (id={uid})")
    else:
        print(f"🔐 [Auth] Admin user '{admin['username']}' exists (id={admin['id']})")
    if _STARTUP_EXAMPLE_SEED_ENABLED:
        try:
            backfill = await _backfill_default_examples_for_existing_users_once()
            if backfill["status"] == "done":
                print(
                    "🧩 [Startup] Default example backfill complete "
                    f"({backfill['seeded_users']}/{backfill['processed_users']} non-admin users seeded)"
                )
        except Exception as exc:
            print(f"🧩 [Startup] Default example backfill failed: {exc}")
    else:
        print("🧩 [Startup] Default example seeding disabled (PHILFORGE_STARTUP_EXAMPLE_SEED=0)")


@app.on_event("startup")
async def _start_token_renewal():
    if _SKIP_STARTUP_JOBS:
        print("🧪 [Startup] Skipping network-heavy startup jobs (PHILFORGE_SKIP_STARTUP_JOBS=1)")
        return
    if config.AUTO_TOKEN_ENABLED and _STARTUP_TOKEN_ENABLED:
        asyncio.create_task(_bootstrap_token_renewal())
        print("🔄 [TokenManager] Startup token bootstrap running in background")
    elif config.AUTO_TOKEN_ENABLED:
        print("🔄 [TokenManager] Startup token bootstrap disabled (PHILFORGE_STARTUP_TOKEN=0)")
    if _market_feed:
        print(f"⚡ [MarketFeed] WebSocket feed ready (dhanhq {'available' if HAS_DHAN_FEED else 'NOT available'})")
    if _STARTUP_SCRIP_MASTER_ENABLED:
        asyncio.create_task(_prefetch_scrip_master())
    else:
        print("📚 [SCRIP] Startup prefetch disabled (PHILFORGE_STARTUP_SCRIP_MASTER=0)")

    if _STARTUP_TRADE_BACKFILL_ENABLED:
        asyncio.create_task(_backfill_in_background())
    else:
        _backfill_state.update({"status": "skipped", "message": "Startup trade-history backfill disabled."})
        print("📊 [BACKFILL] Startup trade-history backfill disabled (PHILFORGE_STARTUP_TRADE_BACKFILL=0)")

    if _STARTUP_EMPTY_RUN_CLEANUP_ENABLED:
        removed = await _db_mod.cleanup_empty_runs()
        if removed:
            print(f"🧹 [STARTUP] Removed {removed} empty 0-trade runs from history")
    else:
        print("🧹 [STARTUP] Empty-run cleanup disabled (PHILFORGE_STARTUP_EMPTY_RUN_CLEANUP=0)")

    if _STARTUP_ENGINE_RESTORE_ENABLED and _engine_restore_owner_is_active_instance():
        asyncio.create_task(_restore_live_engines())
        asyncio.create_task(_restore_paper_engines())
        asyncio.create_task(_restore_auxiliary_engines())
    elif _STARTUP_ENGINE_RESTORE_ENABLED:
        print("♻️ [Startup] Standby instance detected — engine restore deferred until handover")
    else:
        print("♻️ [Startup] Engine restore disabled (PHILFORGE_STARTUP_ENGINE_RESTORE=0)")


async def _restore_live_engines():
    """Scan for live_state_*.json files and re-start engines that were running."""
    import json as _json
    from datetime import date as date_type

    today = str(date_type.today())
    restored = 0

    for user_id, state_dir, fname, fpath in _iter_user_state_files("live_state_"):
        try:
            with open(fpath, "r") as f:
                state = _json.load(f)

            if state.get("manual_intervention_required"):
                print(f"🔄 [Restore] Skipping unsafe live state: {fname} requires broker reconciliation")
                continue

            if state.get("session_date") != today and not _state_has_open_positions(state):
                print(f"🔄 [Restore] Skipping stale state: {fname} (date={state.get('session_date')})")
                continue

            strategy = state.get("strategy", {})
            entry_conditions = state.get("entry_conditions", [])
            exit_conditions = state.get("exit_conditions", [])
            deploy_config = state.get("deploy_config", {})
            run_id = strategy.get("run_name", "live") or "live"
            live_bucket = _registry_bucket(live_engines, user_id)
            live_task_bucket = _registry_bucket(_live_tasks, user_id)

            # Skip if an engine with this run_id already exists
            if run_id in live_bucket:
                print(f"🔄 [Restore] Engine '{run_id}' already running — skipping")
                continue

            # Reconstruct engine with full config
            user = await _db_mod.get_user_by_id(user_id)
            broker_client, broker_source = _resolve_user_broker_client(user, allow_admin_fallback=True)
            if not broker_client:
                print(
                    f"🔄 [Restore] Skipping live restore for user {user_id} / {fname}: "
                    f"{_broker_not_configured_message(user, broker_source)}"
                )
                continue

            engine = LiveEngine(broker_client, run_id=run_id, state_dir=state_dir)
            engine.configure(
                strategy=strategy,
                entry_conditions=entry_conditions or DEFAULT_ENTRY_CONDITIONS,
                exit_conditions=exit_conditions or DEFAULT_EXIT_CONDITIONS,
                deploy_config=deploy_config,
            )
            engine._user_id = int(strategy.get("_user_id") or user_id)

            # Inject WebSocket feed if available
            if _market_feed and HAS_DHAN_FEED:
                instrument = strategy.get("instrument", "26000")
                _market_feed.subscribe_index(instrument)
                if not _market_feed.is_running:
                    _market_feed.start()
                engine.set_feed(_market_feed)

            # Restore trading state (positions, in_trade, closed trades, P&L, etc.)
            engine._load_state()
            engine.running = True

            async def broadcast(event: dict, _rid=run_id, _user_id=getattr(engine, "_user_id", None)):
                await _broadcast_user_ws_json(_user_id, {"source": "live", "run_id": _rid, **event})
                if event.get("type") == "exit" and event.get("trade"):
                    await _save_single_trade_to_history(
                        event["trade"],
                        "live",
                        run_name=_rid,
                        explicit_user_id=_user_id,
                    )

            live_bucket[run_id] = engine
            live_task_bucket[run_id] = asyncio.create_task(engine.start(callback=broadcast))
            restored += 1
            print(f"✅ [Restore] Live engine '{run_id}' restored and started")

        except Exception as e:
            print(f"❌ [Restore] Failed to restore {fname}: {e}")

    if restored:
        print(f"🔄 [Restore] {restored} live engine(s) auto-restored from saved state")


async def _restore_paper_engines():
    """Scan for paper_state_*.json files and re-start engines that were running."""
    import json as _json
    from datetime import date as date_type

    today = str(date_type.today())
    restored = 0

    for user_id, state_dir, fname, fpath in _iter_user_state_files("paper_state_"):
        try:
            with open(fpath, "r") as f:
                state = _json.load(f)

            if state.get("session_date") != today and not _state_has_open_positions(state):
                print(f"🔄 [Restore] Skipping stale paper state: {fname} (date={state.get('session_date')})")
                continue

            strategy = state.get("strategy", {})
            entry_conditions = state.get("entry_conditions", [])
            exit_conditions = state.get("exit_conditions", [])

            # Require full config — can't restore from legacy format
            if not strategy:
                print(f"🔄 [Restore] Skipping {fname}: no full strategy config saved")
                continue

            run_id = strategy.get("run_name", "paper") or "paper"
            paper_bucket = _registry_bucket(paper_engines, user_id)
            paper_task_bucket = _registry_bucket(_paper_tasks, user_id)

            # Skip if already running
            if run_id in paper_bucket:
                print(f"🔄 [Restore] Paper engine '{run_id}' already running — skipping")
                continue

            engine = PaperTradingEngine(dhan, run_id=run_id, state_dir=state_dir)
            engine.configure(
                strategy=strategy,
                entry_conditions=entry_conditions or DEFAULT_ENTRY_CONDITIONS,
                exit_conditions=exit_conditions or DEFAULT_EXIT_CONDITIONS,
            )
            engine._user_id = int(strategy.get("_user_id") or user_id)

            # Inject WebSocket feed if available
            if _market_feed and HAS_DHAN_FEED:
                instrument = strategy.get("instrument", "26000")
                _market_feed.subscribe_index(instrument)
                if not _market_feed.is_running:
                    _market_feed.start()
                engine.set_feed(_market_feed)

            # Restore trading state (positions, in_trade, closed trades, P&L, etc.)
            engine._load_state()
            engine.running = True

            async def broadcast(event: dict, _rid=run_id, _user_id=getattr(engine, "_user_id", None)):
                await _broadcast_user_ws_json(_user_id, {"source": "paper", "run_id": _rid, **event})
                if event.get("type") == "exit" and event.get("trade"):
                    await _save_single_trade_to_history(
                        event["trade"],
                        "paper",
                        run_name=_rid,
                        explicit_user_id=_user_id,
                    )

            paper_bucket[run_id] = engine
            paper_task_bucket[run_id] = asyncio.create_task(engine.start(callback=broadcast))
            restored += 1
            print(f"✅ [Restore] Paper engine '{run_id}' restored and started")

        except Exception as e:
            print(f"❌ [Restore] Failed to restore paper {fname}: {e}")

    if restored:
        print(f"🔄 [Restore] {restored} paper engine(s) auto-restored from saved state")


@app.on_event("shutdown")
async def _shutdown_cleanup():
    """Save all running engine results and clean up."""
    # Save all running scalp engines
    for owner_id, engine in list(_scalp_engines.items()):
        try:
            await _save_scalp_open_state(owner_id, engine, force=True)
            await _save_scalp_run_to_history(engine, explicit_user_id=owner_id)
            engine.stop()
            print(f"🛑 [Shutdown] Saved scalp engine: {owner_id}")
        except Exception as e:
            print(f"🛑 [Shutdown] Failed to save scalp engine {owner_id}: {e}")
    # Save all running paper engines
    for owner_id, run_id, engine in list(_iter_registry_items(paper_engines)):
        try:
            status = engine.get_status()
            if engine.running:
                engine.stop()
            await _save_paper_run_to_history(status, explicit_user_id=getattr(engine, "_user_id", None))
            print(f"🛑 [Shutdown] Saved paper engine: {owner_id}:{run_id}")
        except Exception as e:
            print(f"🛑 [Shutdown] Failed to save paper engine {owner_id}:{run_id}: {e}")
    # Save all running live engines (state file for auto-restore + runs.json for history)
    for owner_id, run_id, engine in list(_iter_registry_items(live_engines)):
        try:
            status = engine.get_status()
            if engine.running:
                engine.stop()  # stop() calls _save_state() internally
            await _save_live_run_to_history(status, explicit_user_id=getattr(engine, "_user_id", None))
            print(f"🛑 [Shutdown] Saved live engine: {owner_id}:{run_id}")
        except Exception as e:
            print(f"🛑 [Shutdown] Failed to save live engine {owner_id}:{run_id}: {e}")
    for owner_id, runtime in list(_cascade_engines.items()):
        try:
            await _save_cascade_open_state(owner_id, runtime, force=True)
            runtime.running = False
            if runtime.task and not runtime.task.done():
                runtime.task.cancel()
            print(f"🛑 [Shutdown] Saved paper Cascade campaign: {owner_id}")
        except Exception as e:
            print(f"🛑 [Shutdown] Failed to save paper Cascade campaign {owner_id}: {e}")
    for owner_id, runtime in list(_candle_entry_engines.items()):
        try:
            await _save_candle_entry_open_state(owner_id, force=True)
            runtime.running = False
            if runtime.task and not runtime.task.done():
                runtime.task.cancel()
            print(f"🛑 [Shutdown] Saved Candle Entry campaign: {owner_id}")
        except Exception as e:
            print(f"🛑 [Shutdown] Failed to save Candle Entry campaign {owner_id}: {e}")
    # Nested one level deeper than Candle Entry: a user can hold one ladder per
    # instrument, and each has its own poll task to cancel.
    for owner_id, ladders in list(_fib_boundary_engines.items()):
        try:
            await _save_fib_boundary_open_state(owner_id, force=True)
            for runtime in ladders.values():
                runtime.running = False
                if runtime.task and not runtime.task.done():
                    runtime.task.cancel()
            print(f"🛑 [Shutdown] Saved Fib Boundary campaigns: {owner_id}")
        except Exception as e:
            print(f"🛑 [Shutdown] Failed to save Fib Boundary campaigns {owner_id}: {e}")
    for owner_id, runtimes in list(_terminal_cascade_engines.items()):
        try:
            await _save_terminal_cascade_open_state(owner_id, force=True)
            for runtime in runtimes.values():
                runtime.running = False
                if runtime.task and not runtime.task.done():
                    runtime.task.cancel()
            print(f"🛑 [Shutdown] Saved Terminal Cascade campaigns: {owner_id}")
        except Exception as e:
            print(f"🛑 [Shutdown] Failed to save Terminal Cascade campaigns {owner_id}: {e}")
    shutdown_feed()
    await alerter.shutdown()
    await _db_mod.close_db()
    print("🛑 [Shutdown] MarketFeed + DB closed")


# ── Feed Status ───────────────────────────────────────────────────
@app.get("/api/feed/status")
async def feed_status():
    """Get WebSocket market feed status."""
    if not _market_feed:
        return {"status": "unavailable", "reason": "dhanhq MarketFeed not installed"}
    return {
        "status": "running" if _market_feed.is_running else "stopped",
        "has_dhan_feed": HAS_DHAN_FEED,
        "subscriptions": len(_market_feed._subscriptions),
        "ltp_cache_size": len(_market_feed._ltp_cache),
        "aggregators": list(_market_feed._aggregators.keys()),
    }


# ── Run ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    # Phase 2: Install uvloop for C-level event-loop speed (~2-4x faster I/O scheduling)
    try:
        import uvloop

        uvloop.install()
        _loop_name = "uvloop"
    except ImportError:
        _loop_name = "asyncio (install uvloop for +30% speed)"

    print("=" * 60)
    print("  PhilForge — Starting Backend")
    print(f"  Event loop : {_loop_name}")
    print(f"  Open: http://{config.APP_HOST}:{config.APP_PORT}")
    print("=" * 60)
    uvicorn.run(
        "app:app",
        host=config.APP_HOST,
        port=config.APP_PORT,
        reload=False,
        log_level="info",
        loop="uvloop" if _loop_name == "uvloop" else "auto",
    )
