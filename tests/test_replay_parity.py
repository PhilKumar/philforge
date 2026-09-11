import math
import unittest
from unittest.mock import patch

import pandas as pd

from engine.backtest import debug_condition_group, run_backtest
from engine.live import LiveEngine
from engine.paper_trading import PaperTradingEngine
from engine.replay import (
    decision_summary,
    decision_times,
    infer_signal_candle_times_from_trades,
    replay_condition_debug,
)


def _make_minute_session(date_str: str, base_price: float, periods: int = 36) -> pd.DataFrame:
    index = pd.date_range(f"{date_str} 09:15", periods=periods, freq="1min")
    closes = []
    for idx in range(periods):
        drift = -0.18 * idx
        wave = math.sin(idx / 3.0) * 1.2
        closes.append(base_price + drift + wave)
    opens = [closes[0]] + closes[:-1]
    highs = [max(op, cl) + 0.35 for op, cl in zip(opens, closes)]
    lows = [min(op, cl) - 0.35 for op, cl in zip(opens, closes)]
    volumes = [100 + idx * 5 for idx in range(periods)]
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )


def _make_multi_day_session() -> pd.DataFrame:
    day_one = _make_minute_session("2026-03-26", 23020.0)
    day_two = _make_minute_session("2026-03-27", 22980.0)
    day_three = _make_minute_session("2026-03-30", 22940.0)
    return pd.concat([day_one, day_two, day_three]).sort_index()


class _DummyBroker:
    async def async_get_funds(self):
        return {"availabelBalance": 100000.0}


class ReplayParityTests(unittest.TestCase):
    def test_replay_filters_preopen_and_postclose_rows(self):
        index = pd.to_datetime(
            [
                "2026-03-27 09:10",
                "2026-03-27 09:15",
                "2026-03-27 09:20",
                "2026-03-27 15:25",
                "2026-03-27 15:30",
            ]
        )
        raw = pd.DataFrame(
            {
                "open": [100, 101, 102, 103, 104],
                "high": [101, 102, 103, 104, 105],
                "low": [99, 100, 101, 102, 103],
                "close": [100.5, 101.5, 102.5, 103.5, 104.5],
                "volume": [10, 20, 30, 40, 50],
            },
            index=index,
        )
        conditions = [{"left": "current_close", "operator": "is_above", "right": "number", "right_number_value": 100}]

        replay = replay_condition_debug(
            raw,
            conditions,
            indicators=[],
            default_timeframe_minutes=5,
            source_timeframe_minutes=5,
            market_open="09:15",
            market_close="15:25",
        )
        times = decision_times(replay["decisions"])

        self.assertEqual(
            times,
            [
                pd.Timestamp("2026-03-27 09:15"),
                pd.Timestamp("2026-03-27 09:20"),
                pd.Timestamp("2026-03-27 15:25"),
            ],
        )

    def test_replay_marks_missing_condition_data_and_summary_counts_it(self):
        raw = _make_minute_session("2026-03-27", 23020.0, periods=12)
        conditions = [
            {"left": "current_close", "operator": "is_below", "right": "CPR_BC", "logic": "IF"},
            {"left": "Time_Of_Day", "operator": "is_below", "right": "time", "right_time": "09:40", "logic": "AND"},
        ]

        replay = replay_condition_debug(
            raw,
            conditions,
            indicators=[],
            default_timeframe_minutes=1,
            source_timeframe_minutes=1,
        )
        summary = decision_summary(replay["decisions"])

        self.assertGreater(summary["missing_data"], 0)
        self.assertTrue(all(item["gate"].startswith("missing_condition_data") for item in replay["decisions"]))
        self.assertTrue(all("CPR_BC" in item["missing_fields"] for item in replay["decisions"]))
        self.assertTrue(all(not item["overall"] for item in replay["decisions"]))

    def test_replay_timeline_matches_live_and_paper_condition_decisions(self):
        raw = _make_multi_day_session()
        indicators = ["EMA_2_5m", "RSI_2_15m", "CPR_0.2_0.5", "Previous_Day", "ORB_15min"]
        conditions = [
            {"left": "current_close", "operator": "is_below", "right": "EMA_2_5m", "logic": "IF"},
            {"left": "current_close", "operator": "is_below", "right": "CPR_BC", "logic": "AND"},
            {
                "left": "Day_Of_Week",
                "operator": "contains",
                "right": "days",
                "right_days": ["Thursday", "Friday"],
                "logic": "AND",
            },
            {"left": "Time_Of_Day", "operator": "is_below", "right": "time", "right_time": "09:50", "logic": "AND"},
        ]

        replay = replay_condition_debug(
            raw,
            conditions,
            indicators,
            default_timeframe_minutes=5,
            source_timeframe_minutes=1,
        )

        live_engine = LiveEngine(dhan=_DummyBroker(), run_id="replay-live")
        live_engine.configure(
            strategy={"instrument": "26000", "timeframe_minutes": 5, "indicators": indicators},
            entry_conditions=conditions,
            exit_conditions=[],
            deploy_config={},
        )
        with patch.object(PaperTradingEngine, "_load_state", autospec=True, return_value=None):
            paper_engine = PaperTradingEngine(dhan=_DummyBroker(), run_id="replay-paper")
        paper_engine.configure(
            strategy={"instrument": "26000", "timeframe_minutes": 5, "indicators": indicators},
            entry_conditions=conditions,
            exit_conditions=[],
        )
        now = raw.index[-1].to_pydatetime() + pd.Timedelta(minutes=1, seconds=1)
        live_frame = live_engine._prepare_ws_strategy_frame(raw, live_engine.strategy["indicators"], 5, 1, now)
        paper_frame = paper_engine._prepare_ws_strategy_frame(raw, paper_engine.strategy["indicators"], 5, 1, now)

        live_decisions = []
        paper_decisions = []
        for idx in range(len(live_frame)):
            row = live_frame.iloc[idx]
            prev_row = live_frame.iloc[idx - 1] if idx > 0 else None
            overall, _details = debug_condition_group(row, conditions, prev_row)
            live_decisions.append({"time": row.name, "overall": overall})
        for idx in range(len(paper_frame)):
            row = paper_frame.iloc[idx]
            prev_row = paper_frame.iloc[idx - 1] if idx > 0 else None
            overall, _details = debug_condition_group(row, conditions, prev_row)
            paper_decisions.append({"time": row.name, "overall": overall})

        self.assertEqual(decision_times(replay["decisions"]), decision_times(live_decisions))
        self.assertEqual(decision_times(replay["decisions"]), decision_times(paper_decisions))
        self.assertEqual(
            decision_times(replay["decisions"], overall=True),
            decision_times(live_decisions, overall=True),
        )
        self.assertEqual(
            decision_times(replay["decisions"], overall=True),
            decision_times(paper_decisions, overall=True),
        )

    def test_replay_soak_has_no_duplicate_candle_decisions(self):
        raw = _make_multi_day_session()
        indicators = ["EMA_3_5m", "MACD_2_3_2_5m", "CPR_0.2_0.5", "Previous_Day", "ORB_15min"]
        conditions = [
            {"left": "current_close", "operator": "is_below", "right": "EMA_3_5m", "logic": "IF"},
            {"left": "Time_Of_Day", "operator": "is_below", "right": "time", "right_time": "10:00", "logic": "AND"},
        ]

        replay = replay_condition_debug(
            raw,
            conditions,
            indicators,
            default_timeframe_minutes=5,
            source_timeframe_minutes=1,
        )
        times = decision_times(replay["decisions"])

        self.assertGreater(len(times), 10)
        self.assertEqual(len(times), len(set(times)))
        self.assertTrue(all(ts.time() >= pd.Timestamp("09:15").time() for ts in times))
        self.assertTrue(all(ts.time() <= pd.Timestamp("15:25").time() for ts in times))

    def test_replay_signal_times_align_with_simple_backtest_entries(self):
        raw = _make_multi_day_session()
        indicators = ["EMA_2_5m"]
        entry_conditions = [{"left": "current_close", "operator": "crosses_above", "right": "EMA_2_5m", "logic": "IF"}]
        exit_conditions = [{"left": "current_close", "operator": "crosses_below", "right": "EMA_2_5m", "logic": "IF"}]

        replay = replay_condition_debug(
            raw,
            entry_conditions,
            indicators,
            default_timeframe_minutes=5,
            source_timeframe_minutes=1,
        )
        backtest = run_backtest(
            raw,
            entry_conditions=entry_conditions,
            exit_conditions=exit_conditions,
            strategy_config={
                "instrument": "RELIANCE",
                "timeframe_minutes": 5,
                "fetch_timeframe_minutes": 1,
                "indicators": indicators,
                "lot_size": 1,
                "lots": 1,
                "max_trades_per_day": 10,
                "skip_stub_sessions": False,
                "market_open": "09:15",
                "market_close": "15:25",
                "combined_sqoff_time": "15:25",
            },
        )

        signal_times = decision_times(replay["decisions"], overall=True)
        inferred = infer_signal_candle_times_from_trades(backtest.get("trades", []), 5)

        self.assertTrue(signal_times)
        self.assertTrue(inferred)
        self.assertEqual(inferred, signal_times[: len(inferred)])


if __name__ == "__main__":
    unittest.main()
