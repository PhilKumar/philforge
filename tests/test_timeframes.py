import time
import unittest
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import pandas as pd

import engine.live as live_module
import engine.paper_trading as paper_module
from broker.dhan import ScripMaster as TestScripMaster
from engine.indicators import (
    compute_dynamic_indicators,
    cpr,
    cpr_timeframe,
    infer_execution_timeframe,
    normalize_strategy_indicators,
    yesterday_candle,
)
from engine.live import LiveEngine
from engine.market_feed import _looks_like_disconnect_error
from engine.paper_trading import PaperTradingEngine
from engine.timeframes import drop_incomplete_candle, next_entry_ready_at, resolve_strategy_timeframe
from scalp import ScalpEngine, ScalpTrade

_TEST_STATE_DIR = None
_TEST_PATCHES = []


def setUpModule():
    global _TEST_STATE_DIR, _TEST_PATCHES
    _TEST_STATE_DIR = TemporaryDirectory()
    _TEST_PATCHES = [
        patch.object(live_module, "_STATE_DIR", _TEST_STATE_DIR.name),
        patch.object(paper_module, "_STATE_DIR", _TEST_STATE_DIR.name),
        patch.object(paper_module, "_DEFAULT_STATE_FILE", f"{_TEST_STATE_DIR.name}/paper_state.json"),
        patch.object(TestScripMaster, "ensure_loaded", return_value=True),
        patch.object(TestScripMaster, "lookup", return_value="555"),
        patch.object(TestScripMaster, "get_nearest_expiry", return_value="2026-03-26"),
        patch.object(TestScripMaster, "resolve_expiry", return_value="2026-03-26"),
        patch.object(TestScripMaster, "get_expiries", return_value=["2026-03-26", "2026-04-02"]),
        patch.object(TestScripMaster, "get_lot_size", return_value=50),
    ]
    for patcher in _TEST_PATCHES:
        patcher.start()


def tearDownModule():
    global _TEST_STATE_DIR, _TEST_PATCHES
    for patcher in reversed(_TEST_PATCHES):
        patcher.stop()
    _TEST_PATCHES = []
    if _TEST_STATE_DIR is not None:
        _TEST_STATE_DIR.cleanup()
        _TEST_STATE_DIR = None


def _make_ohlcv(start: str, closes: list[float], freq: str = "1min") -> pd.DataFrame:
    index = pd.date_range(start, periods=len(closes), freq=freq)
    return pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.5 for value in closes],
            "low": [value - 0.5 for value in closes],
            "close": closes,
            "volume": [100] * len(closes),
        },
        index=index,
    )


class TimeframeRegressionTests(unittest.TestCase):
    def test_resolve_mixed_timeframe_uses_lowest_execution_frame(self):
        spec = resolve_strategy_timeframe(["EMA_5_3m", "SMA_20_5m"])
        self.assertEqual(spec.requested, 3)
        self.assertEqual(spec.fetch, 1)
        self.assertTrue(spec.mixed)
        self.assertEqual(spec.all_frames, (3, 5))

    def test_infer_execution_timeframe_ignores_exit_only_lower_indicator(self):
        execution = infer_execution_timeframe(
            ["EMA_17_5m", "RSI_14_5m", "Supertrend_10_2.7_3m"],
            entry_conditions=[
                {"left": "current_close", "operator": "is_above", "right": "EMA_17_5m"},
                {"left": "RSI_14_5m", "operator": "is_above", "right": "number", "right_number_value": 60},
            ],
            default=5,
        )
        self.assertEqual(execution, 5)

        spec = resolve_strategy_timeframe(
            ["EMA_17_5m", "RSI_14_5m", "Supertrend_10_2.7_3m"],
            default=execution,
            execution_hint=execution,
        )
        self.assertEqual(spec.requested, 5)
        self.assertEqual(spec.fetch, 1)
        self.assertEqual(spec.all_frames, (3, 5))

    def test_mixed_timeframe_alignment_uses_last_closed_higher_frame(self):
        raw = _make_ohlcv("2026-03-18 09:15", list(range(100, 110)))
        result = compute_dynamic_indicators(
            raw,
            ["SMA_1_3m", "SMA_1_5m"],
            default_timeframe_minutes=3,
            source_timeframe_minutes=1,
        )

        self.assertEqual(list(result.index.strftime("%H:%M")), ["09:15", "09:18", "09:21"])
        self.assertTrue(pd.isna(result.loc[pd.Timestamp("2026-03-18 09:15"), "SMA_1_5m"]))
        self.assertEqual(result.loc[pd.Timestamp("2026-03-18 09:18"), "SMA_1_5m"], 104)
        self.assertEqual(result.loc[pd.Timestamp("2026-03-18 09:21"), "SMA_1_5m"], 104)

    def test_compute_dynamic_indicators_respects_explicit_execution_timeframe(self):
        raw = _make_ohlcv("2026-03-18 09:15", list(range(100, 111)))
        result = compute_dynamic_indicators(
            raw,
            ["SMA_1_3m", "SMA_1_5m"],
            default_timeframe_minutes=5,
            source_timeframe_minutes=1,
            execution_timeframe_minutes=5,
        )

        self.assertEqual(list(result.index.strftime("%H:%M")), ["09:15", "09:20"])
        self.assertEqual(result.loc[pd.Timestamp("2026-03-18 09:20"), "SMA_1_3m"], 108)

    def test_drop_incomplete_candle_removes_forming_bar(self):
        raw = _make_ohlcv("2026-03-19 09:10", [100, 101], freq="5min")
        trimmed = drop_incomplete_candle(raw, 5, pd.Timestamp("2026-03-19 09:16").to_pydatetime())
        self.assertEqual(list(trimmed.index.strftime("%H:%M")), ["09:10"])


class CprRegressionTests(unittest.TestCase):
    def test_normalize_strategy_indicators_infers_daily_cpr_dependency_from_condition(self):
        indicators = normalize_strategy_indicators(
            ["EMA_20_5m"],
            entry_conditions=[{"left": "current_close", "operator": "is_below", "right": "CPR_BC"}],
            exit_conditions=[],
        )

        self.assertIn("EMA_20_5m", indicators)
        self.assertIn("CPR_0.2_0.5", indicators)

    def test_live_ws_frame_populates_cpr_levels_from_condition_dependency(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="live-cpr-dependency")
        engine.configure(
            strategy={"instrument": "26000", "timeframe_minutes": 5, "indicators": []},
            entry_conditions=[{"left": "current_close", "operator": "is_below", "right": "CPR_BC"}],
            exit_conditions=[],
            deploy_config={},
        )
        raw = pd.DataFrame(
            {
                "open": [23000.0, 23020.0, 22980.0, 22960.0],
                "high": [23040.0, 23060.0, 23010.0, 22990.0],
                "low": [22980.0, 23000.0, 22940.0, 22930.0],
                "close": [23020.0, 23010.0, 22970.0, 22950.0],
                "volume": [100, 120, 90, 95],
            },
            index=pd.to_datetime(
                [
                    "2026-03-26 09:15",
                    "2026-03-26 09:20",
                    "2026-03-27 09:15",
                    "2026-03-27 09:20",
                ]
            ),
        )

        result = engine._prepare_ws_strategy_frame(
            raw,
            indicators=engine.strategy["indicators"],
            execution_timeframe=5,
            fetch_timeframe=5,
            now=pd.Timestamp("2026-03-27 09:25:01").to_pydatetime(),
        )

        self.assertIn("CPR_BC", result.columns)
        self.assertFalse(pd.isna(result.iloc[-1]["CPR_BC"]))

    def test_daily_cpr_normalizes_bc_below_tc(self):
        df = pd.DataFrame(
            {
                "open": [111.0, 112.0],
                "high": [120.0, 121.0],
                "low": [100.0, 101.0],
                "close": [105.0, 110.0],
            },
            index=pd.to_datetime(["2030-01-02", "2030-01-03"]),
        )

        result = cpr(df)
        row = result.loc[pd.Timestamp("2030-01-03")]

        self.assertLessEqual(row["bc"], row["tc"])
        self.assertAlmostEqual(float(row["bc"]), 106.6666666667, places=4)
        self.assertAlmostEqual(float(row["tc"]), 110.0, places=4)

    def test_higher_timeframe_cpr_normalizes_bc_below_tc(self):
        df = pd.DataFrame(
            {
                "open": [119.0, 116.0, 111.0, 107.0, 110.0, 111.0, 112.0, 113.0],
                "high": [120.0, 118.0, 116.0, 112.0, 114.0, 115.0, 116.0, 117.0],
                "low": [110.0, 108.0, 104.0, 100.0, 109.0, 110.0, 111.0, 112.0],
                "close": [118.0, 114.0, 109.0, 105.0, 111.0, 112.0, 113.0, 114.0],
            },
            index=pd.date_range("2030-01-02 08:00", periods=8, freq="1h"),
        )

        result = cpr_timeframe(df, timeframe="4H")
        row = result.loc[pd.Timestamp("2030-01-02 12:00")]

        self.assertLessEqual(row["bc"], row["tc"])
        self.assertAlmostEqual(float(row["bc"]), 106.6666666667, places=4)
        self.assertAlmostEqual(float(row["tc"]), 110.0, places=4)

    def test_intraday_cpr_does_not_leak_same_day_levels_into_last_historical_session(self):
        df = _make_ohlcv("2025-03-03 09:15", [100, 101, 102, 103], freq="5min")

        result = cpr(df)

        self.assertTrue(pd.isna(result.iloc[-1]["bc"]))
        self.assertTrue(pd.isna(result.iloc[-1]["tc"]))

    def test_higher_timeframe_cpr_does_not_leak_same_bar_levels_into_last_historical_bar(self):
        df = pd.DataFrame(
            {
                "open": [119.0, 116.0, 111.0, 107.0],
                "high": [120.0, 118.0, 116.0, 112.0],
                "low": [110.0, 108.0, 104.0, 100.0],
                "close": [118.0, 114.0, 109.0, 105.0],
            },
            index=pd.date_range("2025-03-03 08:00", periods=4, freq="1h"),
        )

        result = cpr_timeframe(df, timeframe="4H")

        self.assertTrue(pd.isna(result.iloc[-1]["bc"]))
        self.assertTrue(pd.isna(result.iloc[-1]["tc"]))


class YesterdayRegressionTests(unittest.TestCase):
    def test_yesterday_candle_does_not_leak_same_day_values_into_last_historical_session(self):
        df = _make_ohlcv("2025-03-03 09:15", [100, 101, 102, 103], freq="5min")

        result = yesterday_candle(df)

        self.assertTrue(pd.isna(result.iloc[-1]["yesterday_high"]))
        self.assertTrue(pd.isna(result.iloc[-1]["yesterday_low"]))
        self.assertTrue(pd.isna(result.iloc[-1]["yesterday_close"]))
        self.assertTrue(pd.isna(result.iloc[-1]["yesterday_open"]))


class DummyBroker:
    def __init__(
        self,
        funds: dict | None = None,
        option_ltp: float = 100.0,
        place_order_results: list[dict] | None = None,
        verify_results: list[dict] | None = None,
    ):
        self.funds = funds or {"availabelBalance": 0.0}
        self.option_ltp = option_ltp
        self.place_order_results = list(place_order_results or [])
        self.verify_results = list(verify_results or [])
        self.cancelled_orders = []
        self.placed_orders = []

    async def async_get_funds(self):
        return self.funds

    async def async_get_option_ltp(self, *args, **kwargs):
        return self.option_ltp

    def get_option_ltp(self, *args, **kwargs):
        return self.option_ltp

    async def async_place_option_order(self, **kwargs):
        self.placed_orders.append(kwargs)
        if self.place_order_results:
            return self.place_order_results.pop(0)
        return {"orderId": f"ORD{len(self.placed_orders)}"}

    async def async_verify_order_fill(self, order_id: str, max_wait_sec: int = 15):
        if self.verify_results:
            return self.verify_results.pop(0)
        return {
            "order_id": order_id,
            "status": "FILLED",
            "filled_qty": 0,
            "avg_price": self.option_ltp,
            "message": "Order filled successfully",
        }

    async def async_cancel_order(self, order_id: str):
        self.cancelled_orders.append(order_id)
        return {"orderId": order_id, "status": "CANCELLED"}


class DummyFeed:
    def __init__(self, snapshot: pd.DataFrame):
        self.snapshot = snapshot

    def get_candle_snapshot(self, instrument_id: str, timeframe: int, include_current: bool = False) -> pd.DataFrame:
        return self.snapshot.copy()


class SessionCloseRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_paper_tick_force_closes_open_position_at_market_close(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(option_ltp=112.0), run_id="paper-market-close")

        engine.strategy = {"market_open": "09:15", "market_close": "15:25"}
        engine.positions = [
            {
                "status": "open",
                "leg_num": 1,
                "transaction_type": "BUY",
                "entry_premium": 100.0,
                "current_premium": 112.0,
                "quantity": 50,
                "lot_size": 50,
                "lots": 1,
            }
        ]
        engine.in_trade = True
        engine.current_time = pd.Timestamp("2026-03-27 15:25:00").to_pydatetime()

        await engine._force_market_close_if_needed(engine.current_time)

        self.assertFalse(engine.in_trade)
        self.assertEqual(len(engine.positions), 0)
        self.assertEqual(len(engine.closed_trades), 1)
        self.assertEqual(engine.closed_trades[0]["exit_reason"], "MARKET_CLOSE")
        self.assertEqual(engine.closed_trades[0]["exit_premium"], 112.0)

    async def test_live_tick_force_exits_open_position_at_market_close(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="live-market-close")
        engine.configure(
            strategy={"instrument": "26000", "market_open": "09:15", "market_close": "15:25", "legs": []},
            entry_conditions=[],
            exit_conditions=[],
            deploy_config={},
        )
        position = {
            "status": "open",
            "leg_num": 1,
            "transaction_type": "BUY",
            "entry_premium": 100.0,
            "current_premium": 118.0,
            "quantity": 50,
            "lot_size": 50,
            "lots": 1,
            "underlying": "NIFTY",
            "strike": 22950,
            "option_type": "PE",
            "expiry": "2026-03-27",
            "trading_symbol": "NIFTY 22950 PE",
        }
        engine.positions = [position]
        engine.in_trade = True
        engine.current_time = pd.Timestamp("2026-03-27 15:25:00").to_pydatetime()

        async def _fake_exit(pos, reason, exit_premium, callback=None):
            pos["status"] = "closed"
            pos["exit_reason"] = reason
            pos["exit_premium"] = exit_premium
            if pos in engine.positions:
                engine.positions.remove(pos)
            engine.closed_trades.append(pos.copy())
            engine.in_trade = bool(engine.positions)

        engine._exit_position = AsyncMock(side_effect=_fake_exit)

        await engine._force_market_close_if_needed(engine.current_time)

        engine._exit_position.assert_awaited_once()
        _, reason, exit_premium, _ = engine._exit_position.await_args.args
        self.assertEqual(reason, "MARKET_CLOSE")
        self.assertEqual(exit_premium, 118.0)
        self.assertFalse(engine.in_trade)
        self.assertEqual(len(engine.positions), 0)

    async def test_paper_tick_uses_last_in_session_candle_for_exit_before_market_close_force(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(option_ltp=112.0), run_id="paper-close-candle-exit")

        engine.strategy = {"market_open": "09:15", "market_close": "15:25", "timeframe_minutes": 5, "indicators": []}
        engine.positions = [
            {
                "status": "open",
                "leg_num": 1,
                "transaction_type": "BUY",
                "entry_premium": 100.0,
                "current_premium": 112.0,
                "quantity": 50,
                "lot_size": 50,
                "lots": 1,
            }
        ]
        engine.in_trade = True
        engine.session_date = pd.Timestamp("2026-03-27").date()
        df = _make_ohlcv("2026-03-27 15:15", [100.0, 101.0], freq="5min")

        with (
            patch("engine.paper_trading._now_ist", return_value=pd.Timestamp("2026-03-27 15:25:00").to_pydatetime()),
            patch.object(engine, "_fetch_live_data", AsyncMock(return_value=df)),
            patch.object(engine, "_get_current_premium", AsyncMock(return_value=112.0)),
            patch.object(engine, "_check_strategy_exit", return_value=None),
            patch.object(engine, "_check_exit_conditions", return_value="EXIT_SIGNAL") as exit_check,
        ):
            await engine._tick()

        exit_check.assert_called()
        self.assertEqual(len(engine.closed_trades), 1)
        self.assertEqual(engine.closed_trades[0]["exit_reason"], "EXIT_SIGNAL")

    async def test_paper_tick_does_not_arm_entry_on_last_candle_of_session(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="paper-close-candle-entry")

        engine.strategy = {
            "market_open": "09:15",
            "market_close": "15:25",
            "timeframe_minutes": 5,
            "indicators": [],
            "max_trades_per_day": 1,
        }
        df = _make_ohlcv("2026-03-27 15:15", [100.0, 99.0], freq="5min")

        with (
            patch("engine.paper_trading._now_ist", return_value=pd.Timestamp("2026-03-27 15:25:00").to_pydatetime()),
            patch.object(engine, "_fetch_live_data", AsyncMock(return_value=df)),
            patch.object(engine, "_evaluate_entry_conditions_with_debug", return_value=(True, {"gate": "pass"})),
        ):
            await engine._tick()

        self.assertFalse(engine._entry_signal_pending)
        self.assertIsNone(engine._pending_signal_candle_time)
        self.assertEqual(engine._condition_debug.get("gate"), "market_close_boundary")

    async def test_live_tick_uses_last_in_session_candle_for_exit_before_market_close_force(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="live-close-candle-exit")
        engine.configure(
            strategy={"instrument": "26000", "market_open": "09:15", "market_close": "15:25", "timeframe_minutes": 5},
            entry_conditions=[],
            exit_conditions=[],
            deploy_config={},
        )
        position = {
            "status": "open",
            "leg_num": 1,
            "transaction_type": "BUY",
            "entry_premium": 100.0,
            "current_premium": 118.0,
            "quantity": 50,
            "lot_size": 50,
            "lots": 1,
            "underlying": "NIFTY",
            "strike": 22950,
            "option_type": "PE",
            "expiry": "2026-03-27",
            "trading_symbol": "NIFTY 22950 PE",
        }
        engine.positions = [position]
        engine.in_trade = True
        engine.session_date = pd.Timestamp("2026-03-27").date()
        df = _make_ohlcv("2026-03-27 15:15", [100.0, 101.0], freq="5min")

        async def _fake_exit(pos, reason, exit_premium, callback=None):
            pos["status"] = "closed"
            pos["exit_reason"] = reason
            pos["exit_premium"] = exit_premium
            if pos in engine.positions:
                engine.positions.remove(pos)
            engine.closed_trades.append(pos.copy())
            engine.in_trade = bool(engine.positions)

        engine._exit_position = AsyncMock(side_effect=_fake_exit)

        with (
            patch("engine.live._now_ist", return_value=pd.Timestamp("2026-03-27 15:25:00").to_pydatetime()),
            patch.object(engine, "_fetch_live_data", AsyncMock(return_value=df)),
            patch.object(engine, "_get_current_premium", AsyncMock(return_value=118.0)),
            patch.object(engine, "_check_strategy_exit", return_value=None),
            patch.object(engine, "_check_exit_conditions", return_value="EXIT_SIGNAL") as exit_check,
        ):
            await engine._tick()

        exit_check.assert_called()
        self.assertTrue(engine._exit_position.await_count >= 1)
        _, reason, _, _ = engine._exit_position.await_args.args
        self.assertEqual(reason, "EXIT_SIGNAL")
        self.assertEqual(len(engine.positions), 0)

    async def test_live_tick_does_not_arm_entry_on_last_candle_of_session(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="live-close-candle-entry")
        engine.configure(
            strategy={
                "instrument": "26000",
                "market_open": "09:15",
                "market_close": "15:25",
                "timeframe_minutes": 5,
                "indicators": [],
                "max_trades_per_day": 1,
            },
            entry_conditions=[],
            exit_conditions=[],
            deploy_config={},
        )
        df = _make_ohlcv("2026-03-27 15:15", [100.0, 99.0], freq="5min")

        with (
            patch("engine.live._now_ist", return_value=pd.Timestamp("2026-03-27 15:25:00").to_pydatetime()),
            patch.object(engine, "_fetch_live_data", AsyncMock(return_value=df)),
            patch.object(engine, "_evaluate_entry_conditions_with_debug", return_value=(True, {"gate": "pass"})),
        ):
            await engine._tick()

        self.assertIsNone(engine._pending_order)
        self.assertFalse(engine._entry_signal_pending)
        self.assertEqual(engine._condition_debug.get("gate"), "market_close_boundary")


class LivePendingEntryTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_pending_order_waits_until_next_candle_open_plus_one_second(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="timing-test")
        engine.strategy = {"timeframe_minutes": 5, "indicators": [], "max_trades_per_day": 1}
        signal_ts = pd.Timestamp("2026-03-19 09:15").to_pydatetime()
        engine._pending_order = {
            "signal_candle_time": signal_ts,
            "created_at": pd.Timestamp("2026-03-19 09:20:00").to_pydatetime(),
            "ready_at": next_entry_ready_at(signal_ts, 5),
            "row": pd.Series({"open": 100.0}),
            "attempts": 0,
            "retry_at": None,
        }
        engine._flush_pending_order = AsyncMock()

        with patch("engine.live._now_ist", return_value=pd.Timestamp("2026-03-19 09:20:00").to_pydatetime()):
            await engine._try_flush_pending_order()
        engine._flush_pending_order.assert_not_awaited()

        with patch("engine.live._now_ist", return_value=pd.Timestamp("2026-03-19 09:20:01").to_pydatetime()):
            await engine._try_flush_pending_order()
        engine._flush_pending_order.assert_awaited_once()

    async def test_live_flush_pending_order_retries_once_then_clears(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="timing-retry-test")
        signal_ts = pd.Timestamp("2026-03-19 09:15").to_pydatetime()
        engine._pending_order = {
            "signal_candle_time": signal_ts,
            "created_at": pd.Timestamp("2026-03-19 09:20:00").to_pydatetime(),
            "ready_at": next_entry_ready_at(signal_ts, 5),
            "row": pd.Series({"open": 100.0}),
            "attempts": 0,
            "retry_at": None,
        }
        engine._enter_trade = AsyncMock(return_value=None)
        engine.in_trade = False

        with patch("engine.live._now_ist", return_value=pd.Timestamp("2026-03-19 09:20:02").to_pydatetime()):
            first = await engine._flush_pending_order(pd.Series({"open": 100.0}))

        self.assertFalse(first)
        self.assertIsNotNone(engine._pending_order)
        self.assertEqual(engine._pending_order["attempts"], 1)
        self.assertIsNotNone(engine._pending_order["retry_at"])

        with patch("engine.live._now_ist", return_value=pd.Timestamp("2026-03-19 09:20:06").to_pydatetime()):
            second = await engine._flush_pending_order(pd.Series({"open": 100.0}))

        self.assertFalse(second)
        self.assertIsNone(engine._pending_order)

    async def test_live_try_flush_pending_order_drops_duplicate_processed_candle(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="timing-dedupe-test")
        signal_ts = pd.Timestamp("2026-03-19 09:15").to_pydatetime()
        engine.strategy = {"timeframe_minutes": 5, "indicators": [], "max_trades_per_day": 1}
        engine._last_processed_candle_time = signal_ts
        engine._pending_order = {
            "signal_candle_time": signal_ts,
            "created_at": pd.Timestamp("2026-03-19 09:20:00").to_pydatetime(),
            "ready_at": pd.Timestamp("2026-03-19 09:20:01").to_pydatetime(),
            "row": pd.Series({"open": 100.0}),
            "attempts": 0,
            "retry_at": None,
        }
        engine._flush_pending_order = AsyncMock()

        with patch("engine.live._now_ist", return_value=pd.Timestamp("2026-03-19 09:20:02").to_pydatetime()):
            await engine._try_flush_pending_order()

        engine._flush_pending_order.assert_not_awaited()
        self.assertIsNone(engine._pending_order)

    async def test_live_try_flush_pending_order_clears_previous_session_signal(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="timing-stale-session-test")
        engine.configure(
            strategy={
                "instrument": "26000",
                "market_open": "09:15",
                "market_close": "15:25",
                "timeframe_minutes": 5,
                "indicators": [],
                "max_trades_per_day": 1,
            },
            entry_conditions=[],
            exit_conditions=[],
            deploy_config={},
        )
        signal_ts = pd.Timestamp("2026-03-27 15:20").to_pydatetime()
        engine._pending_order = {
            "signal_candle_time": signal_ts,
            "created_at": pd.Timestamp("2026-03-27 15:25:00").to_pydatetime(),
            "ready_at": next_entry_ready_at(signal_ts, 5),
            "row": pd.Series({"open": 100.0}),
            "attempts": 0,
            "retry_at": None,
        }
        engine._flush_pending_order = AsyncMock()

        with patch("engine.live._now_ist", return_value=pd.Timestamp("2026-03-30 09:15:02").to_pydatetime()):
            await engine._try_flush_pending_order()

        engine._flush_pending_order.assert_not_awaited()
        self.assertIsNone(engine._pending_order)


class WsClosedCandleGuardTests(unittest.TestCase):
    def test_live_ws_frame_ignores_first_forming_5m_candle(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="live-ws-guard")
        raw = _make_ohlcv("2026-03-19 09:10", [100, 101], freq="5min")

        result = engine._prepare_ws_strategy_frame(
            raw,
            indicators=[],
            execution_timeframe=5,
            fetch_timeframe=5,
            now=pd.Timestamp("2026-03-19 09:15:36").to_pydatetime(),
        )

        self.assertEqual(list(result.index.strftime("%H:%M")), ["09:10"])

    def test_live_previous_session_candle_is_not_treated_as_current_session_signal(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="live-session-guard")
        engine.configure(
            strategy={
                "instrument": "26000",
                "market_open": "09:15",
                "market_close": "15:25",
                "timeframe_minutes": 5,
                "indicators": [],
            },
            entry_conditions=[],
            exit_conditions=[],
            deploy_config={},
        )
        engine.current_time = pd.Timestamp("2026-03-30 09:15:02").to_pydatetime()
        signal_ts = pd.Timestamp("2026-03-27 15:20").to_pydatetime()

        self.assertFalse(engine._strategy_candle_closes_in_session(signal_ts))
        self.assertFalse(engine._entry_can_be_scheduled(signal_ts))

    def test_paper_ws_frame_ignores_first_forming_5m_candle(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="paper-ws-guard")
        raw = _make_ohlcv("2026-03-19 09:10", [100, 101], freq="5min")

        result = engine._prepare_ws_strategy_frame(
            raw,
            indicators=[],
            execution_timeframe=5,
            fetch_timeframe=5,
            now=pd.Timestamp("2026-03-19 09:15:36").to_pydatetime(),
        )

        self.assertEqual(list(result.index.strftime("%H:%M")), ["09:10"])

    def test_paper_previous_session_candle_is_not_treated_as_current_session_signal(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="paper-session-guard")
        engine.configure(
            strategy={
                "instrument": "26000",
                "market_open": "09:15",
                "market_close": "15:25",
                "timeframe_minutes": 5,
                "indicators": [],
            },
            entry_conditions=[],
            exit_conditions=[],
        )
        engine.current_time = pd.Timestamp("2026-03-30 09:15:02").to_pydatetime()
        signal_ts = pd.Timestamp("2026-03-27 15:20").to_pydatetime()
        engine._arm_pending_entry(signal_ts, pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}))

        self.assertFalse(engine._strategy_candle_closes_in_session(signal_ts))
        self.assertFalse(engine._entry_can_be_scheduled(signal_ts))
        self.assertFalse(engine._pending_entry_is_ready(pd.Timestamp("2026-03-30 09:15:02").to_pydatetime()))


class FeedDisconnectRegressionTests(unittest.TestCase):
    def test_close_frame_errors_are_treated_as_disconnects(self):
        self.assertTrue(_looks_like_disconnect_error(RuntimeError("no close frame received or sent")))
        self.assertTrue(_looks_like_disconnect_error(RuntimeError("Connection closed unexpectedly")))
        self.assertFalse(_looks_like_disconnect_error(RuntimeError("temporary parse warning")))


class IntradayUiResetRegressionTests(unittest.TestCase):
    def test_paper_reset_intraday_status_clears_stale_ui_state(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="paper-reset")
        engine.current_spot = 22924.7
        engine.current_time = pd.Timestamp("2026-03-24 15:24:32").to_pydatetime()
        engine.current_candle = {"close": 22924.7, "updated_at": "2026-03-24 03:24:32 PM"}
        engine.current_indicators = {"EMA_17_5m": 22800.0}
        engine._arm_pending_entry(
            pd.Timestamp("2026-03-24 15:21:00").to_pydatetime(),
            pd.Series({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}),
        )

        engine._reset_intraday_status()

        self.assertEqual(engine.current_spot, 0.0)
        self.assertIsNone(engine.current_time)
        self.assertEqual(engine.current_candle, {})
        self.assertEqual(engine.current_indicators, {})
        self.assertFalse(engine._entry_signal_pending)
        self.assertEqual(engine._condition_debug["gate"], "waiting_for_first_candle")

    def test_live_reset_intraday_status_clears_stale_ui_state(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="live-reset")
        engine.current_spot = 22924.7
        engine.current_time = pd.Timestamp("2026-03-24 15:24:32").to_pydatetime()
        engine.current_candle = {"close": 22924.7, "updated_at": "2026-03-24 03:24:32 PM"}
        engine.current_indicators = {"EMA_17_5m": 22800.0}
        engine._entry_signal_pending = True
        engine._pending_order = {"created_at": pd.Timestamp("2026-03-24 15:24:32").to_pydatetime()}

        engine._reset_intraday_status()

        self.assertEqual(engine.current_spot, 0.0)
        self.assertIsNone(engine.current_time)
        self.assertEqual(engine.current_candle, {})
        self.assertEqual(engine.current_indicators, {})
        self.assertFalse(engine._entry_signal_pending)
        self.assertIsNone(engine._pending_order)
        self.assertEqual(engine._condition_debug["gate"], "waiting_for_first_candle")

    def test_paper_stale_carry_restore_resets_daily_trade_counter(self):
        saved_date = (pd.Timestamp.now().date() - timedelta(days=1)).isoformat()
        with TemporaryDirectory() as tempdir:
            state_path = f"{tempdir}/paper_state_carry-reset.json"
            with open(state_path, "w", encoding="utf-8") as handle:
                import json

                json.dump(
                    {
                        "session_date": saved_date,
                        "strategy": {
                            "run_name": "Carry Paper",
                            "instrument": "26000",
                            "product_type": "NRML",
                            "timeframe_minutes": 5,
                            "indicators": [],
                        },
                        "entry_conditions": [],
                        "exit_conditions": [],
                        "positions": [{"status": "open", "entry_premium": 100.0}],
                        "in_trade": True,
                        "closed_trades": [],
                        "trades_today": 10,
                        "daily_pnl": -2500.0,
                    },
                    handle,
                )

            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="carry-reset", state_dir=tempdir)

            self.assertEqual(engine.trades_today, 0)
            self.assertEqual(engine.daily_pnl, 0.0)
            self.assertTrue(engine.in_trade)
            self.assertEqual(len(engine.positions), 1)

    def test_live_stale_carry_restore_resets_daily_trade_counter(self):
        saved_date = (pd.Timestamp.now().date() - timedelta(days=1)).isoformat()
        with TemporaryDirectory() as tempdir:
            state_path = f"{tempdir}/live_state_carry-reset.json"
            with open(state_path, "w", encoding="utf-8") as handle:
                import json

                json.dump(
                    {
                        "session_date": saved_date,
                        "strategy": {
                            "run_name": "Carry Live",
                            "instrument": "26000",
                            "product_type": "NRML",
                            "timeframe_minutes": 5,
                            "indicators": [],
                        },
                        "deploy_config": {"product_type": "NRML"},
                        "entry_conditions": [],
                        "exit_conditions": [],
                        "positions": [{"status": "open", "entry_premium": 100.0}],
                        "in_trade": True,
                        "closed_trades": [],
                        "trades_today": 10,
                        "daily_pnl": -2500.0,
                    },
                    handle,
                )

            engine = LiveEngine(dhan=DummyBroker(), run_id="carry-reset", state_dir=tempdir)
            engine._load_state()

            self.assertEqual(engine.trades_today, 0)
            self.assertEqual(engine.daily_pnl, 0.0)
            self.assertTrue(engine.in_trade)
            self.assertEqual(len(engine.positions), 1)


class PaperIntrabarExitRegressionTests(unittest.TestCase):
    def test_paper_intrabar_monitor_skips_signal_exit_until_next_closed_candle(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="paper-intrabar-exit")
        engine.exit_conditions = [{"left": "close", "operator": "is_above", "right": "number", "right_number_value": 1}]
        position = {
            "transaction_type": "BUY",
            "entry_premium": 100.0,
            "peak_premium": 100.0,
            "sl_pct": 0,
            "target_pct": 0,
            "sl_points": 0,
            "target_points": 0,
            "sl_rupees": 0,
            "target_rupees": 0,
            "trail_pct": 0,
            "sqoff_time": "15:20",
        }
        row = pd.Series({"close": 10.0})
        engine.current_time = pd.Timestamp("2026-03-25 13:42:02").to_pydatetime()

        with patch("engine.paper_trading.eval_condition_group", return_value=True):
            self.assertEqual(engine._check_exit_conditions(position, row, 100.0), "EXIT_SIGNAL")
            self.assertIsNone(engine._check_exit_conditions(position, row, 100.0, allow_signal_exit=False))


class LiveIntrabarExitRegressionTests(unittest.TestCase):
    def test_live_intrabar_monitor_skips_signal_exit_until_next_closed_candle(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="live-intrabar-exit")
        engine.exit_conditions = [{"left": "close", "operator": "is_above", "right": "number", "right_number_value": 1}]
        position = {
            "transaction_type": "BUY",
            "entry_premium": 100.0,
            "peak_premium": 100.0,
            "sl_pct": 0,
            "target_pct": 0,
            "sl_points": 0,
            "target_points": 0,
            "sl_rupees": 0,
            "target_rupees": 0,
            "trail_pct": 0,
            "sqoff_time": "15:20",
            "lots": 1,
            "lot_size": 1,
        }
        row = pd.Series({"close": 10.0})
        engine.current_time = pd.Timestamp("2026-03-25 13:42:02").to_pydatetime()

        with patch("engine.live.eval_condition_group", return_value=True):
            self.assertEqual(engine._check_exit_conditions(position, row, 100.0), "EXIT_SIGNAL")
            self.assertIsNone(engine._check_exit_conditions(position, row, 100.0, allow_signal_exit=False))


class PaperPendingEntryTimingTests(unittest.TestCase):
    def test_paper_pending_entry_arms_for_next_candle_open_plus_one_second(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="timing-test")
        engine.strategy = {"timeframe_minutes": 5, "indicators": []}
        signal_ts = pd.Timestamp("2026-03-19 09:15").to_pydatetime()
        latest_row = pd.Series({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5})

        engine._arm_pending_entry(signal_ts, latest_row)

        self.assertFalse(engine._pending_entry_is_ready(pd.Timestamp("2026-03-19 09:20:00").to_pydatetime()))
        self.assertTrue(engine._pending_entry_is_ready(pd.Timestamp("2026-03-19 09:20:01").to_pydatetime()))


class LiveTouchExitTests(unittest.TestCase):
    def test_live_touch_exit_uses_forming_execution_candle(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="touch-live")
        engine.strategy = {"instrument": "26000", "timeframe_minutes": 3, "indicators": []}
        engine.exit_conditions = [
            {"left": "current_high", "operator": "touches", "right": "number", "right_number_value": 104}
        ]
        engine.current_time = pd.Timestamp("2026-03-19 09:22:30").to_pydatetime()
        engine.current_spot = 102.0
        engine._ws_mode = True
        engine._feed = DummyFeed(
            pd.DataFrame(
                {
                    "open": [101.0, 102.0],
                    "high": [105.0, 103.0],
                    "low": [100.0, 101.0],
                    "close": [103.0, 102.0],
                    "volume": [100, 120],
                },
                index=pd.to_datetime(["2026-03-19 09:21:00", "2026-03-19 09:22:00"]),
            )
        )
        row = pd.Series(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 90},
            name=pd.Timestamp("2026-03-19 09:18:00"),
        )
        pos = {"transaction_type": "BUY", "entry_premium": 100.0, "peak_premium": 100.0, "lots": 1, "lot_size": 1}

        reason = engine._check_exit_conditions(pos, row, 100.0)

        self.assertEqual(reason, "TOUCH_EXIT")


class PaperTouchExitTests(unittest.TestCase):
    def test_paper_touch_exit_uses_latest_raw_snapshot_in_rest_mode(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="touch-paper")
        engine.strategy = {"instrument": "26000", "timeframe_minutes": 3, "indicators": []}
        engine.exit_conditions = [
            {"left": "current_high", "operator": "touches", "right": "number", "right_number_value": 104}
        ]
        engine.current_time = pd.Timestamp("2026-03-19 09:22:30").to_pydatetime()
        engine.current_spot = 102.0
        engine._latest_raw_candles = pd.DataFrame(
            {
                "open": [101.0, 102.0],
                "high": [105.0, 103.0],
                "low": [100.0, 101.0],
                "close": [103.0, 102.0],
                "volume": [100, 120],
            },
            index=pd.to_datetime(["2026-03-19 09:21:00", "2026-03-19 09:22:00"]),
        )
        row = pd.Series(
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 90},
            name=pd.Timestamp("2026-03-19 09:18:00"),
        )
        position = {"transaction_type": "BUY", "entry_premium": 100.0, "peak_premium": 100.0, "lots": 1, "lot_size": 1}

        reason = engine._check_exit_conditions(position, row, 100.0)

        self.assertEqual(reason, "TOUCH_EXIT")


class PortfolioStrategyExitTests(unittest.TestCase):
    def test_live_strategy_exit_uses_combined_open_position_pnl(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="portfolio-live")
        engine.strat_sl_val = 90.0
        engine.positions = [
            {
                "status": "open",
                "transaction_type": "BUY",
                "entry_premium": 100.0,
                "current_premium": 50.0,
                "lots": 1,
                "lot_size": 1,
                "quantity": 1,
            },
            {
                "status": "open",
                "transaction_type": "BUY",
                "entry_premium": 100.0,
                "current_premium": 50.0,
                "lots": 1,
                "lot_size": 1,
                "quantity": 1,
            },
        ]

        self.assertEqual(engine._check_strategy_exit(), "STRATEGY_SL")

    def test_live_strategy_thresholds_use_combined_entry_notional(self):
        engine = LiveEngine(dhan=DummyBroker(), run_id="portfolio-live")
        engine._sl_pct = 10.0
        engine._tp_pct = 20.0
        engine._set_strategy_thresholds(
            [
                {"entry_premium": 100.0, "lots": 1, "lot_size": 50, "quantity": 50},
                {"entry_premium": 120.0, "lots": 1, "lot_size": 50, "quantity": 50},
            ]
        )

        self.assertEqual(engine.trade_entry_prem, 11000.0)
        self.assertEqual(engine.strat_sl_val, 1100.0)
        self.assertEqual(engine.strat_tp_val, 2200.0)

    def test_paper_strategy_exit_uses_combined_open_position_pnl(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="portfolio-paper-exit")
        engine.strat_tp_val = 90.0
        engine.positions = [
            {
                "status": "open",
                "transaction_type": "BUY",
                "entry_premium": 100.0,
                "current_premium": 145.0,
                "lots": 1,
                "lot_size": 1,
            },
            {
                "status": "open",
                "transaction_type": "BUY",
                "entry_premium": 100.0,
                "current_premium": 145.0,
                "lots": 1,
                "lot_size": 1,
            },
        ]

        self.assertEqual(engine._check_strategy_exit(), "STRATEGY_TP")

    def test_paper_strategy_close_uses_actual_exit_premium(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="portfolio-paper-close")
        engine._history_file = "/tmp/paper_history_portfolio-paper.json"
        engine.current_time = pd.Timestamp("2026-03-19 09:25").to_pydatetime()
        position = {
            "status": "open",
            "transaction_type": "BUY",
            "entry_premium": 100.0,
            "lots": 1,
            "lot_size": 1,
            "leg_num": 1,
        }
        engine.positions = [position]

        with patch.object(engine, "_save_trade_history"):
            engine._close_position(position, "STRATEGY_TP", 110.0)

        self.assertEqual(engine.closed_trades[-1]["pnl"], 10.0)


class CapitalGatingTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_capital_check_blocks_unaffordable_long_entry(self):
        engine = LiveEngine(dhan=DummyBroker({"availabelBalance": 1000.0}), run_id="capital-live")
        engine.strategy = {"instrument": "26000"}
        engine._enforce_capital = True
        engine._capital_buffer_pct = 0.0
        engine._sell_option_margin_per_lot = 100000.0

        allowed = await engine._can_enter_trade(
            [{"transaction_type": "BUY", "entry_premium": 30.0, "lots": 1, "quantity": 50, "lot_size": 50}]
        )

        self.assertFalse(allowed)
        self.assertEqual(engine.capital_rejections, 1)
        self.assertFalse(engine.last_capital_check["passed"])

    async def test_live_capital_check_uses_margin_for_short_option(self):
        engine = LiveEngine(dhan=DummyBroker({"availabelBalance": 90000.0}), run_id="capital-live")
        engine.strategy = {"instrument": "26000"}
        engine._enforce_capital = True
        engine._capital_buffer_pct = 0.0
        engine._sell_option_margin_per_lot = 100000.0

        allowed = await engine._can_enter_trade(
            [{"transaction_type": "SELL", "entry_premium": 10.0, "lots": 1, "quantity": 50, "lot_size": 50}]
        )

        self.assertFalse(allowed)
        self.assertEqual(engine.last_capital_check["required"], 100000.0)

    async def test_paper_capital_check_blocks_unaffordable_portfolio(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="capital-paper")
        engine.initial_capital = 1000.0
        engine._enforce_capital = True
        engine._capital_buffer_pct = 0.0
        engine._sell_option_margin_per_lot = 100000.0

        allowed = engine._can_enter_trade(
            [{"transaction_type": "BUY", "entry_premium": 30.0, "lots": 1, "lot_size": 50}]
        )

        self.assertFalse(allowed)
        self.assertEqual(engine.capital_rejections, 1)
        self.assertFalse(engine.last_capital_check["passed"])


class LiveOrderVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_entry_uses_verified_broker_fill_price(self):
        broker = DummyBroker(
            option_ltp=100.0,
            place_order_results=[{"orderId": "ENTRY1"}],
            verify_results=[{"status": "FILLED", "filled_qty": 50, "avg_price": 101.25}],
        )
        engine = LiveEngine(dhan=broker, run_id="verify-entry")
        engine.strategy = {
            "run_name": "Verify Entry",
            "instrument": "26000",
            "legs": [{"transaction_type": "BUY", "option_type": "CE", "strike_type": "atm", "lots": 1}],
        }
        engine.deploy_config = {"product_type": "MIS", "entry_order": "MARKET"}
        engine.current_spot = 22000.0
        engine.current_time = pd.Timestamp("2026-03-19 09:20:01").to_pydatetime()
        engine.session_date = engine.current_time.date()

        with (
            patch.object(engine, "_can_enter_trade", AsyncMock(return_value=True)),
            patch("engine.live.ScripMaster.resolve_expiry", return_value="2026-03-26"),
            patch("engine.live.ScripMaster.get_lot_size", return_value=50),
        ):
            await engine._enter_trade(pd.Series({"open": 22000.0}))

        self.assertTrue(engine.in_trade)
        self.assertEqual(len(engine.positions), 1)
        self.assertAlmostEqual(engine.positions[0]["entry_premium"], 101.25)
        self.assertTrue(engine.positions[0]["entry_fill_verified"])
        self.assertEqual(engine.last_order_verification["status"], "FILLED")

    async def test_live_entry_timeout_does_not_open_position(self):
        broker = DummyBroker(
            option_ltp=100.0,
            place_order_results=[{"orderId": "ENTRY1"}],
            verify_results=[{"status": "TIMEOUT", "filled_qty": 0, "avg_price": 0.0, "message": "pending"}],
        )
        engine = LiveEngine(dhan=broker, run_id="verify-entry-timeout")
        engine.strategy = {
            "run_name": "Verify Entry Timeout",
            "instrument": "26000",
            "legs": [{"transaction_type": "BUY", "option_type": "CE", "strike_type": "atm", "lots": 1}],
        }
        engine.deploy_config = {"product_type": "MIS", "entry_order": "MARKET"}
        engine.current_spot = 22000.0
        engine.current_time = pd.Timestamp("2026-03-19 09:20:01").to_pydatetime()
        engine.session_date = engine.current_time.date()

        with (
            patch.object(engine, "_can_enter_trade", AsyncMock(return_value=True)),
            patch("engine.live.ScripMaster.resolve_expiry", return_value="2026-03-26"),
            patch("engine.live.ScripMaster.get_lot_size", return_value=50),
        ):
            await engine._enter_trade(pd.Series({"open": 22000.0}))

        self.assertFalse(engine.in_trade)
        self.assertEqual(len(engine.positions), 0)
        self.assertEqual(engine.order_verification_failures, 1)
        self.assertIn("ENTRY1", broker.cancelled_orders)

    async def test_live_exit_uses_verified_broker_fill_price(self):
        broker = DummyBroker(
            place_order_results=[{"orderId": "EXIT1"}],
            verify_results=[{"status": "FILLED", "filled_qty": 50, "avg_price": 118.5}],
        )
        engine = LiveEngine(dhan=broker, run_id="verify-exit")
        engine.deploy_config = {"product_type": "MIS", "exit_order": "MARKET"}
        engine.current_time = pd.Timestamp("2026-03-19 09:25:00").to_pydatetime()
        position = {
            "leg_num": 1,
            "status": "open",
            "transaction_type": "BUY",
            "underlying": "NIFTY",
            "option_type": "CE",
            "strike": 22000,
            "expiry": "2026-03-26",
            "entry_premium": 100.0,
            "current_premium": 120.0,
            "quantity": 50,
            "lots": 1,
            "lot_size": 50,
            "trading_symbol": "NIFTY 22000CE 2026-03-26",
        }
        engine.positions = [position]
        engine.in_trade = True

        await engine._exit_position(position, "EXIT_SIGNAL", 120.0)

        self.assertFalse(engine.in_trade)
        self.assertEqual(len(engine.positions), 0)
        self.assertAlmostEqual(engine.closed_trades[-1]["exit_premium"], 118.5)
        self.assertEqual(engine.last_order_verification["status"], "FILLED")

    async def test_live_exit_timeout_marks_position_for_retry(self):
        broker = DummyBroker(
            place_order_results=[{"orderId": "EXIT1"}],
            verify_results=[{"status": "TIMEOUT", "filled_qty": 0, "avg_price": 0.0, "message": "pending"}],
        )
        engine = LiveEngine(dhan=broker, run_id="verify-exit-timeout")
        engine.deploy_config = {"product_type": "MIS", "exit_order": "MARKET"}
        engine.current_time = pd.Timestamp("2026-03-19 09:25:00").to_pydatetime()
        position = {
            "leg_num": 1,
            "status": "open",
            "transaction_type": "BUY",
            "underlying": "NIFTY",
            "option_type": "CE",
            "strike": 22000,
            "expiry": "2026-03-26",
            "entry_premium": 100.0,
            "current_premium": 120.0,
            "quantity": 50,
            "lots": 1,
            "lot_size": 50,
            "trading_symbol": "NIFTY 22000CE 2026-03-26",
        }
        engine.positions = [position]
        engine.in_trade = True

        await engine._exit_position(position, "EXIT_SIGNAL", 120.0)

        self.assertTrue(engine.in_trade)
        self.assertEqual(len(engine.positions), 1)
        self.assertEqual(engine.positions[0]["_force_exit_reason"], "EXIT_SIGNAL")
        self.assertIn("EXIT1", broker.cancelled_orders)


class ScalpBrokerReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def test_scalp_trade_exit_grace_is_short_and_blocks_only_immediate_same_second_exit(self):
        trade = ScalpTrade(
            trade_id=1,
            underlying="NIFTY",
            strike=23000,
            option_type="CE",
            expiry="2026-03-24",
            transaction_type="BUY",
            lots=1,
            lot_size=75,
            entry_premium=100.0,
            target_premium=101.0,
            sl_premium=99.0,
            mode="live",
        )
        trade.entry_time = pd.Timestamp("2026-03-24 09:15:00").to_pydatetime()

        with patch("scalp._now_ist", return_value=pd.Timestamp("2026-03-24 09:15:00.500").to_pydatetime()):
            self.assertIsNone(trade.check_exit(101.5))

        with patch("scalp._now_ist", return_value=pd.Timestamp("2026-03-24 09:15:01.100").to_pydatetime()):
            self.assertEqual(trade.check_exit(101.5), "target_hit")

    def test_scalp_trade_ignores_legacy_square_off_time_after_market_close(self):
        trade = ScalpTrade(
            trade_id=1,
            underlying="NIFTY",
            strike=23000,
            option_type="CE",
            expiry="2026-03-24",
            transaction_type="BUY",
            lots=1,
            lot_size=75,
            entry_premium=100.0,
            target_premium=0.0,
            sl_premium=0.0,
            sqoff_time="15:20",
            mode="paper",
        )
        trade.entry_time = pd.Timestamp("2026-03-24 09:15:00").to_pydatetime()

        with patch("scalp._now_ist", return_value=pd.Timestamp("2026-03-24 23:30:00").to_pydatetime()):
            self.assertIsNone(trade.check_exit(100.0))

    def test_engine_scalp_import_uses_canonical_scalp_classes(self):
        from engine.scalp import ScalpEngine as EngineScalpEngine
        from engine.scalp import ScalpTrade as EngineScalpTrade

        self.assertIs(EngineScalpEngine, ScalpEngine)
        self.assertIs(EngineScalpTrade, ScalpTrade)

    async def test_manual_broker_exit_closes_local_trade(self):
        class DummyScalpBroker:
            def get_positions_cached(self, ttl=3.0):
                return []

        engine = ScalpEngine(DummyScalpBroker())
        trade = ScalpTrade(
            trade_id=1,
            underlying="NIFTY",
            strike=23000,
            option_type="CE",
            expiry="2026-03-24",
            transaction_type="BUY",
            lots=1,
            lot_size=75,
            entry_premium=100.0,
            target_premium=120.0,
            sl_premium=90.0,
            order_id="ENTRY1",
            mode="live",
        )
        trade.super_order_id = "SO123"
        trade.current_premium = 108.0
        trade.entry_time = trade.entry_time - pd.Timedelta(seconds=20)
        engine.open_trades[trade.trade_id] = trade
        engine._close_trade = AsyncMock()

        with patch("scalp.ScripMaster.lookup", return_value="555"):
            await engine._sync_broker_positions()

        engine._close_trade.assert_awaited_once()
        self.assertEqual(engine._close_trade.await_args.args[1], "broker_manual_exit")
        self.assertTrue(engine._close_trade.await_args.kwargs["skip_broker_exit"])

    async def test_super_order_cancel_already_traded_is_not_logged_as_error(self):
        class DummyScalpBroker:
            def __init__(self):
                self.legs = []

            def cancel_super_order(self, order_id, leg_name="ENTRY_LEG"):
                self.legs.append(leg_name)
                return {
                    "orderId": order_id,
                    "orderStatus": "TRADED",
                    "errorMessage": "Order Has Traded Please Refresh Order Book",
                }

        engine = ScalpEngine(DummyScalpBroker())
        trade = ScalpTrade(
            trade_id=1,
            underlying="NIFTY",
            strike=23000,
            option_type="CE",
            expiry="2026-03-24",
            transaction_type="BUY",
            lots=1,
            lot_size=75,
            entry_premium=100.0,
            order_id="ENTRY1",
            mode="live",
        )
        trade.super_order_id = "333260320564871"

        await engine._cancel_super_order(trade)

        self.assertEqual(trade.super_order_status, "TRADED")
        self.assertFalse([e for e in engine.event_log if e["type"] == "error"], engine.event_log)
        self.assertTrue(any("already traded" in e["message"] for e in engine.event_log))
        # A traded entry leaves its target and stop WORKING at the broker, so
        # they are released too (2026-09-23) -- the log no longer ends on the
        # entry line, which is why this reads the whole log.
        self.assertEqual(engine.dhan.legs, ["ENTRY_LEG", "TARGET_LEG", "STOP_LOSS_LEG"])

    async def test_nested_position_payload_does_not_break_broker_sync(self):
        class DummyScalpBroker:
            def get_positions_cached(self, ttl=3.0):
                return [[{"securityId": "555", "netQty": 75}]]

        engine = ScalpEngine(DummyScalpBroker())
        trade = ScalpTrade(
            trade_id=1,
            underlying="NIFTY",
            strike=23000,
            option_type="CE",
            expiry="2026-03-24",
            transaction_type="BUY",
            lots=1,
            lot_size=75,
            entry_premium=100.0,
            order_id="ENTRY1",
            mode="live",
        )
        trade.current_premium = 108.0
        trade.entry_time = trade.entry_time - pd.Timedelta(seconds=20)
        engine.open_trades[trade.trade_id] = trade
        engine._close_trade = AsyncMock()

        with patch("scalp.ScripMaster.lookup", return_value="555"):
            await engine._sync_broker_positions()

    async def test_cancel_broker_orders_runs_sl_tp_cancels_together(self):
        class DummyScalpBroker:
            def __init__(self):
                self.cancelled = []

            def cancel_order(self, order_id):
                self.cancelled.append(order_id)

        broker = DummyScalpBroker()
        engine = ScalpEngine(broker)
        trade = ScalpTrade(
            trade_id=1,
            underlying="NIFTY",
            strike=23000,
            option_type="CE",
            expiry="2026-03-24",
            transaction_type="BUY",
            lots=1,
            lot_size=75,
            entry_premium=100.0,
            order_id="ENTRY1",
            mode="live",
        )
        trade.broker_sl_order_id = "SL1"
        trade.broker_tp_order_id = "TP1"

        await engine._cancel_broker_orders(trade)

        self.assertCountEqual(broker.cancelled, ["SL1", "TP1"])
        self.assertEqual(trade.broker_sl_order_id, "")
        self.assertEqual(trade.broker_tp_order_id, "")

    async def test_fetch_all_ltps_reuses_recent_batch_snapshot(self):
        class DummyScalpBroker:
            def __init__(self):
                self.calls = 0

            def get_ltp_multi(self, segments):
                self.calls += 1
                return {"NSE_FNO": {"555": {"last_price": 101.25}}}

        broker = DummyScalpBroker()
        engine = ScalpEngine(broker)
        trade = ScalpTrade(
            trade_id=1,
            underlying="NIFTY",
            strike=23000,
            option_type="CE",
            expiry="2026-03-24",
            transaction_type="BUY",
            lots=1,
            lot_size=75,
            entry_premium=100.0,
            mode="paper",
        )

        with patch("scalp.ScripMaster.lookup", return_value="555"):
            first = await engine._fetch_all_ltps([(trade.trade_id, trade)])
            second = await engine._fetch_all_ltps([(trade.trade_id, trade)])

        self.assertEqual(first[trade.trade_id], 101.25)
        self.assertEqual(second[trade.trade_id], 101.25)
        self.assertEqual(broker.calls, 1)

    async def test_fetch_all_ltps_backs_off_after_rate_limit(self):
        class DummyScalpBroker:
            def __init__(self):
                self.calls = 0

            def get_ltp_multi(self, segments):
                self.calls += 1
                raise Exception(
                    'Dhan API 429: {"data":{"805":"Too many requests. Further requests may result in the user being blocked."},"status":"failed"}'
                )

        broker = DummyScalpBroker()
        engine = ScalpEngine(broker)
        trade = ScalpTrade(
            trade_id=1,
            underlying="NIFTY",
            strike=23000,
            option_type="CE",
            expiry="2026-03-24",
            transaction_type="BUY",
            lots=1,
            lot_size=75,
            entry_premium=100.0,
            mode="paper",
        )

        with patch("scalp.ScripMaster.lookup", return_value="555"):
            first = await engine._fetch_all_ltps([(trade.trade_id, trade)])
            second = await engine._fetch_all_ltps([(trade.trade_id, trade)])

        self.assertEqual(first, {})
        self.assertEqual(second, {})
        self.assertEqual(broker.calls, 1)
        self.assertEqual(engine.event_log[-1]["type"], "warn")
        self.assertIn("rate-limited", engine.event_log[-1]["message"])

    async def test_get_ltp_uses_cached_snapshot_during_backoff(self):
        class DummyScalpBroker:
            def __init__(self):
                self.single_calls = 0

            def get_ltp_multi(self, segments):
                return {"NSE_FNO": {"555": {"last_price": 102.5}}}

            def get_option_ltp(self, *args, **kwargs):
                self.single_calls += 1
                return 0.0

        broker = DummyScalpBroker()
        engine = ScalpEngine(broker)
        trade = ScalpTrade(
            trade_id=1,
            underlying="NIFTY",
            strike=23000,
            option_type="CE",
            expiry="2026-03-24",
            transaction_type="BUY",
            lots=1,
            lot_size=75,
            entry_premium=100.0,
            mode="paper",
        )

        with patch("scalp.ScripMaster.lookup", return_value="555"):
            prices = await engine._fetch_all_ltps([(trade.trade_id, trade)])
            engine._ltp_backoff_until_mono = time.monotonic() + 5
            cached = engine._get_ltp(trade, trade.trade_id)

        self.assertEqual(prices[trade.trade_id], 102.5)
        self.assertEqual(cached, 102.5)
        self.assertEqual(broker.single_calls, 0)


class PaperExecutionRealismTests(unittest.TestCase):
    def test_paper_execution_costs_make_entry_and_exit_worse_for_longs(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="paper-realism")
        engine.configure(
            {
                "run_name": "Paper Realism",
                "instrument": "26000",
                "execution_profile": "manual",
                "spread_bps": 0.0,
                "entry_slippage_bps": 100.0,
                "exit_slippage_bps": 100.0,
            },
            [],
            [],
        )

        self.assertAlmostEqual(engine._apply_execution_costs(100.0, "BUY", "entry"), 101.0)
        self.assertAlmostEqual(engine._apply_execution_costs(110.0, "BUY", "exit"), 108.9)

    def test_paper_close_uses_adjusted_exit_fill_price(self):
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            engine = PaperTradingEngine(dhan=DummyBroker(), run_id="paper-realism-close")
        engine.configure(
            {
                "run_name": "Paper Realism Close",
                "instrument": "26000",
                "execution_profile": "manual",
                "spread_bps": 0.0,
                "entry_slippage_bps": 0.0,
                "exit_slippage_bps": 100.0,
            },
            [],
            [],
        )
        engine.current_time = pd.Timestamp("2026-03-19 09:25").to_pydatetime()
        position = {
            "status": "open",
            "transaction_type": "BUY",
            "entry_premium": 100.0,
            "quantity": 1,
            "lots": 1,
            "lot_size": 1,
            "leg_num": 1,
        }
        engine.positions = [position]

        with patch.object(engine, "_save_trade_history"):
            engine._close_position(position, "TARGET", 110.0)

        self.assertAlmostEqual(engine.closed_trades[-1]["exit_quote_premium"], 110.0)
        self.assertAlmostEqual(engine.closed_trades[-1]["exit_premium"], 108.9)
        self.assertAlmostEqual(engine.closed_trades[-1]["pnl"], 8.9)


if __name__ == "__main__":
    unittest.main()
