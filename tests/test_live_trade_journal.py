"""The trade journal: every closed trade carries its WHY, and its chart freezes.

Phil, 2026-08-17: "a journal chart on every live trade ... where, when, why
trade taken and exited place, with all CPR and indicators specified on the
chart ... a frozen chart like CryptoForge."

Two halves. The ENGINE half: both engines already computed per-condition
verdicts for the debug panel and threw them away on the next bar; now the
verdicts, the bar's indicator readings and the exit reason are frozen onto
the trade record. The ROUTE half: /api/live/trade-chart picks ONE trade by id
(running engine or history file), stops the candles at the exit -- CryptoForge's
`end_ts` freeze -- and returns the why beside the entry/exit marks.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402
from engine.backtest import decision_why, inspect_condition_group  # noqa: E402
from engine.paper_trading import PaperTradingEngine  # noqa: E402

IST = app_module.IST


def _row(ts="2026-08-17 09:20:00", close=24280.5):
    row = pd.Series(
        {
            "close": close,
            "current_close": close,
            "CPR_TC": 24310.2,
            "CPR_BC": 24290.7,
            "CPR_R1": 24350.0,
            "CPR_S1": 24240.0,
            "EMA_20_5m": 24295.1,
            "CPR_is_wide": False,
        },
        name=pd.Timestamp(ts),
    )
    return row


ENTRY_CONDS = [
    {"left": "current_close", "operator": "is_below", "right": "EMA_20_5m", "logic": "IF"},
    {"left": "current_close", "operator": "is_below", "right": "CPR_BC", "logic": "AND"},
]


class DecisionWhyTests(unittest.TestCase):
    def test_the_why_quotes_each_condition_with_the_values_that_fired_it(self):
        row = _row()
        _ov, details, _miss = inspect_condition_group(row, ENTRY_CONDS, None)
        why = decision_why(row, ENTRY_CONDS, {"conditions": details}, None, "ENTRY_SIGNAL")
        self.assertEqual(why["reason"], "ENTRY_SIGNAL")
        self.assertEqual(why["bar_time"], "2026-08-17T09:20:00")
        self.assertEqual(why["spot"], 24280.5)
        self.assertEqual(len(why["conditions"]), 2)
        first = why["conditions"][0]
        self.assertIn("current_close is_below EMA_20_5m", first["condition"])
        self.assertTrue(first["result"])
        self.assertIn("24,280", str(first["left_value"]))
        self.assertIn("24,295", str(first["right_value"]))

    def test_the_why_carries_the_indicator_readings_at_that_bar(self):
        why = decision_why(_row(), [], None, None, "TARGET")
        self.assertEqual(why["indicators"]["CPR_TC"], 24310.2)
        self.assertEqual(why["indicators"]["CPR_S1"], 24240.0)
        self.assertEqual(why["indicators"]["EMA_20_5m"], 24295.1)
        self.assertIs(why["indicators"]["CPR_is_wide"], False)
        self.assertNotIn("VWAP", why["indicators"], "absent columns are skipped, not invented")

    def test_the_why_never_raises(self):
        why = decision_why(None, ENTRY_CONDS, None, None, "SQUARE_OFF")
        self.assertEqual(why["reason"], "SQUARE_OFF")
        self.assertEqual(why["conditions"], [])
        self.assertIsNone(why["bar_time"])


class PaperEngineRecordsWhyTests(unittest.TestCase):
    def _engine(self):
        engine = PaperTradingEngine.__new__(PaperTradingEngine)
        engine.running = True
        engine.current_time = datetime(2026, 8, 17, 9, 59, 22)
        engine.daily_pnl = 0.0
        engine.exit_conditions = [
            {"left": "current_close", "operator": "crosses_above", "right": "CPR_TC", "logic": "IF"}
        ]
        engine.candle_buffer = pd.DataFrame(
            [_row("2026-08-17 09:55:00").to_dict()], index=[pd.Timestamp("2026-08-17 09:55:00")]
        )
        engine._prev_row = None
        engine.closed_trades = []
        engine.events = []
        engine.log_event = lambda *a, **k: engine.events.append(a)
        engine._save_state = lambda: None
        engine._arm_profit_cooldown = lambda: None
        engine._apply_execution_costs = lambda premium, side, leg: premium
        engine._position_quantity = lambda pos: pos["quantity"]
        engine.in_trade = True
        engine.positions = [
            {
                "id": 1,
                "leg_num": 1,
                "transaction_type": "BUY",
                "entry_premium": 242.29,
                "quantity": 260,
                "strike": 24550,
                "option_type": "PE",
                "entry_why": {"reason": "ENTRY_SIGNAL", "conditions": [{"condition": "x", "result": True}]},
            }
        ]
        return engine

    def test_closing_records_the_exit_why_with_reason_and_bar_indicators(self):
        engine = self._engine()
        engine._close_position(engine.positions[0], "TARGET", 256.19)
        closed = engine.closed_trades[0]
        self.assertEqual(closed["exit_reason"], "TARGET")
        self.assertEqual(closed["exit_why"]["reason"], "TARGET")
        self.assertEqual(closed["exit_why"]["indicators"]["CPR_TC"], 24310.2)
        self.assertEqual(closed["exit_why"]["conditions"], [], "a target needs no exit conditions to explain it")
        self.assertEqual(closed["entry_why"]["reason"], "ENTRY_SIGNAL", "the entry why rides along on the copy")

    def test_a_signal_exit_records_the_exit_conditions_verdicts(self):
        engine = self._engine()
        engine._close_position(engine.positions[0], "EXIT_SIGNAL", 250.0)
        why = engine.closed_trades[0]["exit_why"]
        self.assertEqual(why["reason"], "EXIT_SIGNAL")
        self.assertEqual(len(why["conditions"]), 1)
        self.assertIn("crosses_above CPR_TC", why["conditions"][0]["condition"])


class _DummyRequest:
    def __init__(self, user_id: int = 7):
        self.state = SimpleNamespace(user_id=user_id)


class _FakeBroker:
    """Returns 5m option candles for a whole day so the freeze has something to cut."""

    def __init__(self, day):
        self.day = day

    def get_historical_data(self, **kwargs):
        # Two sessions: CPR is built from the PRIOR day, exactly as the entry
        # chart's analytics expect, so the journal gets the same lines.
        idx = []
        rows = []
        px = 240.0
        for day in (self.day - timedelta(days=1), self.day):
            stamp = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=15)
            while stamp.time() <= datetime.min.time().replace(hour=15, minute=25):
                idx.append(pd.Timestamp(stamp))
                rows.append({"open": px, "high": px + 3, "low": px - 3, "close": px + 1})
                px += 0.5
                stamp += timedelta(minutes=5)
        return pd.DataFrame(rows, index=idx)


class TradeChartRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.user_id = 7
        self.run_id = "journal_run"
        app_module.paper_engines.clear()
        entry = datetime(2026, 8, 17, 9, 20, tzinfo=IST)
        exit_ = datetime(2026, 8, 17, 9, 59, 22, tzinfo=IST)
        engine = SimpleNamespace(
            running=False,
            instrument="26000",
            dhan=_FakeBroker(entry.date()),
            positions=[],
            closed_trades=[
                {
                    "id": 3,
                    "symbol": "NIFTY 24550 PE",
                    "underlying": "NIFTY",
                    "strike": 24550,
                    "option_type": "PE",
                    "expiry": "2026-08-18",
                    "transaction_type": "BUY",
                    "quantity": 260,
                    "lots": 4,
                    "entry_time": entry,
                    "exit_time": exit_,
                    "entry_premium": 242.29,
                    "exit_premium": 256.19,
                    "entry_spot": 24280.5,
                    "pnl": 3614.10,
                    "exit_reason": "ENGINE_STOP",
                    "entry_why": {
                        "reason": "ENTRY_SIGNAL",
                        "conditions": [
                            {
                                "condition": "current_close is_below EMA_20_5m",
                                "left_value": "24,280.50",
                                "right_value": "24,295.10",
                                "result": True,
                            }
                        ],
                        "indicators": {"CPR_TC": 24310.2},
                        "bar_time": "2026-08-17T09:15:00",
                        "spot": 24280.5,
                    },
                    "exit_why": {
                        "reason": "ENGINE_STOP",
                        "conditions": [],
                        "indicators": {"CPR_TC": 24310.2},
                        "bar_time": "2026-08-17T09:55:00",
                        "spot": 24300.0,
                    },
                }
            ],
        )
        app_module.paper_engines[self.user_id] = {self.run_id: engine}
        self.exit_ts = int(exit_.timestamp())

    async def asyncTearDown(self):
        app_module.paper_engines.clear()

    async def test_the_chart_is_frozen_at_the_exit_and_carries_the_why(self):
        with patch.object(app_module.ScripMaster, "lookup", return_value="99999"):
            data = await app_module.live_trade_chart(
                _DummyRequest(self.user_id), run_id=self.run_id, trade_id="3", timeframe="5m"
            )
        self.assertEqual(data["status"], "ok")
        self.assertFalse(data["is_open"])
        self.assertEqual(data["frozen_at"], self.exit_ts)
        # THE FREEZE: the broker returned the whole day; nothing past the exit
        # (plus a six-bar runway) survives.
        last = max(c["t"] for c in data["candles"])
        self.assertLessEqual(last, self.exit_ts + 6 * 300)
        self.assertGreater(len(data["candles"]), 5)
        # marks
        self.assertEqual(data["entries"][0]["price"], 242.29)
        self.assertEqual(data["exits"][0]["price"], 256.19)
        self.assertEqual(data["exits"][0]["pnl"], 3614.10)
        # the why
        self.assertEqual(data["why"]["entry"]["conditions"][0]["result"], True)
        self.assertEqual(data["why"]["exit"]["reason"], "ENGINE_STOP")
        self.assertEqual(data["trade"]["exit_reason"], "ENGINE_STOP")
        # analytics still there -- CPR/EMA from the same helper as the entry chart.
        # The pivots are OVERLAYS since 2026-09-23: each session carries its own
        # levels across its own bars, so they are polylines and not the flat
        # full-width lines they used to be.
        drawn = list(data["lines"]) + list(data.get("overlays") or [])
        self.assertTrue(
            any(
                str(line.get("label", "")).upper().startswith(("CPR", "R1", "S1", "TC", "BC", "PIVOT", "P"))
                or "EMA" in str(line.get("label", "")).upper()
                for line in drawn
            ),
            drawn[:4],
        )

    async def test_an_unknown_trade_id_is_a_404(self):
        with patch.object(app_module.ScripMaster, "lookup", return_value="99999"):
            with self.assertRaises(app_module.HTTPException) as raised:
                await app_module.live_trade_chart(_DummyRequest(self.user_id), run_id="no_such_run", trade_id="1")
        self.assertEqual(raised.exception.status_code, 404)

    async def test_history_file_is_read_when_the_engine_is_gone(self):
        """A stopped run's trades stay reachable -- the journal outlives the engine."""
        app_module.paper_engines.clear()
        rows = list(app_module.paper_engines.get(self.user_id, {}).values())
        self.assertEqual(rows, [])
        with (
            patch.object(
                app_module,
                "_live_run_history_trades",
                return_value=[
                    {
                        "id": 9,
                        "underlying": "NIFTY",
                        "strike": 24550,
                        "option_type": "PE",
                        "expiry": "2026-08-18",
                        "entry_time": "2026-08-17T09:20:00+05:30",
                        "exit_time": "2026-08-17T09:59:22+05:30",
                        "entry_premium": 242.29,
                        "exit_premium": 256.19,
                        "pnl": 3614.10,
                        "exit_reason": "TARGET",
                    }
                ],
            ),
            patch.object(app_module.ScripMaster, "lookup", return_value="99999"),
            patch.object(
                app_module,
                "_request_broker_context",
                AsyncMock(return_value=({"id": 7}, _FakeBroker(datetime(2026, 8, 17).date()), "user")),
            ),
        ):
            data = await app_module.live_trade_chart(_DummyRequest(self.user_id), run_id="gone", trade_id="9")
        self.assertEqual(data["trade"]["id"], 9)
        self.assertEqual(data["trade"]["exit_reason"], "TARGET")


if __name__ == "__main__":
    unittest.main()
