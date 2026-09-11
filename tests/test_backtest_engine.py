import contextlib
import io
import unittest

import pandas as pd

from engine.backtest import _release_closed_dynamic_histories, eval_condition, run_backtest


def _make_ohlcv(
    start: str, closes: list[float], *, freq: str = "1min", opens: list[float] | None = None
) -> pd.DataFrame:
    index = pd.date_range(start, periods=len(closes), freq=freq)
    open_values = opens or closes
    return pd.DataFrame(
        {
            "open": open_values,
            "high": [max(op, cl) + 0.5 for op, cl in zip(open_values, closes)],
            "low": [min(op, cl) - 0.5 for op, cl in zip(open_values, closes)],
            "close": closes,
            "volume": [100] * len(closes),
        },
        index=index,
    )


def _always_true_conditions():
    return [{"left": "current_close", "operator": "is_above", "right": "number", "right_number_value": 0}]


def _run_backtest(*args, **kwargs):
    # These fixtures deliberately contain only a few minutes. Session
    # completeness is tested separately; keep exercising the execution rules.
    kwargs["strategy_config"] = {"skip_stub_sessions": False, **kwargs.get("strategy_config", {})}
    with contextlib.redirect_stdout(io.StringIO()):
        return run_backtest(*args, **kwargs)


class BacktestRegressionTests(unittest.TestCase):
    def test_closed_upstox_history_is_released_but_shared_history_is_preserved(self):
        dynamic_key = "upstox|contract|2026-03-19|25000|CE"
        shared_key = "rolling|NIFTY|CE"
        histories = {dynamic_key: object(), shared_key: object()}
        closed = [
            {"option_history_key": dynamic_key},
            {"option_history_key": shared_key},
        ]

        _release_closed_dynamic_histories(histories, closed, [])

        self.assertNotIn(dynamic_key, histories)
        self.assertIn(shared_key, histories)

    def test_history_stays_alive_until_the_last_open_leg_closes(self):
        dynamic_key = "upstox|contract|2026-03-19|25000|CE"
        histories = {dynamic_key: object()}

        _release_closed_dynamic_histories(
            histories,
            [{"option_history_key": dynamic_key}],
            [{"option_history_key": dynamic_key}],
        )

        self.assertIn(dynamic_key, histories)

    def test_touches_distinguishes_candle_range_from_close_value(self):
        row = pd.Series(
            {"open": 101.0, "high": 105.0, "low": 100.0, "close": 102.0},
            name=pd.Timestamp("2026-03-18 09:20"),
        )

        self.assertTrue(
            eval_condition(
                row,
                {"left": "current_high", "operator": "touches", "right": "number", "right_number_value": 104},
            )
        )
        self.assertFalse(
            eval_condition(
                row,
                {"left": "current_close", "operator": "touches", "right": "number", "right_number_value": 104},
            )
        )

    def test_touches_detects_series_intersection(self):
        prev_row = pd.Series({"EMA_5_5m": 99.0, "VWAP_5m": 101.0}, name=pd.Timestamp("2026-03-18 09:15"))
        row = pd.Series({"EMA_5_5m": 101.0, "VWAP_5m": 99.0}, name=pd.Timestamp("2026-03-18 09:20"))

        self.assertTrue(eval_condition(row, {"left": "EMA_5_5m", "operator": "touches", "right": "VWAP_5m"}, prev_row))

    def test_signal_exit_closes_on_current_signal_candle(self):
        df = _make_ohlcv("2026-03-18 09:20", [100, 101, 102, 103])
        result = _run_backtest(
            df,
            entry_conditions=_always_true_conditions(),
            exit_conditions=_always_true_conditions(),
            strategy_config={"instrument": "RELIANCE", "timeframe_minutes": 1, "lot_size": 1},
        )

        trade = result["trades"][0]
        self.assertEqual(trade["entry_time"], "2026-03-18 09:21")
        self.assertEqual(trade["exit_time"], "2026-03-18 09:22")
        self.assertEqual(trade["exit_reason"], "Signal")

    def test_square_off_uses_raw_timestamp_for_derived_timeframe(self):
        df = _make_ohlcv("2026-03-18 15:15", [100, 101, 102, 103, 104, 105, 106])
        result = _run_backtest(
            df,
            entry_conditions=_always_true_conditions(),
            exit_conditions=[],
            strategy_config={
                "instrument": "RELIANCE",
                "timeframe_minutes": 3,
                "fetch_timeframe_minutes": 1,
                "lot_size": 1,
                "combined_sqoff_time": "15:20",
            },
        )

        trade = result["trades"][0]
        self.assertEqual(trade["entry_time"], "2026-03-18 15:18")
        self.assertEqual(trade["exit_time"], "2026-03-18 15:20")
        self.assertEqual(trade["exit_reason"], "SquareOff")

    def test_max_daily_loss_blocks_later_entries(self):
        df = _make_ohlcv("2026-03-18 09:20", [100, 100, 95, 96, 97, 98])
        result = _run_backtest(
            df,
            entry_conditions=_always_true_conditions(),
            exit_conditions=_always_true_conditions(),
            strategy_config={
                "instrument": "RELIANCE",
                "timeframe_minutes": 1,
                "lot_size": 1,
                "max_daily_loss": 4,
                "fee_pct": 0.01,
            },
        )

        self.assertEqual(result["stats"]["total_trades"], 1)
        self.assertTrue(result["stats"]["max_daily_loss_hit"])

    def test_backtest_stats_include_risk_per_trade_pct(self):
        df = _make_ohlcv("2026-03-18 09:20", [100, 101, 99, 102, 98, 103, 97, 104])
        result = _run_backtest(
            df,
            entry_conditions=_always_true_conditions(),
            exit_conditions=_always_true_conditions(),
            strategy_config={
                "instrument": "RELIANCE",
                "timeframe_minutes": 1,
                "lot_size": 1,
                "initial_capital": 50000,
            },
        )

        stats = result["stats"]
        self.assertIn("risk_per_trade_pct", stats)
        self.assertEqual(
            stats["risk_per_trade_pct"],
            round((stats["risk_per_trade"] / stats["initial_capital"]) * 100, 2),
        )

    def test_entry_delay_candles_delays_fill(self):
        df = _make_ohlcv("2026-03-18 09:20", [100, 101, 102, 103, 104])
        result = _run_backtest(
            df,
            entry_conditions=_always_true_conditions(),
            exit_conditions=[],
            strategy_config={
                "instrument": "RELIANCE",
                "timeframe_minutes": 1,
                "lot_size": 1,
                "combined_sqoff_time": "09:24",
                "entry_delay_candles": 1,
            },
        )

        trade = result["trades"][0]
        self.assertEqual(trade["entry_time"], "2026-03-18 09:22")

    def test_signal_exit_delay_candles_delays_signal_fill(self):
        df = _make_ohlcv("2026-03-18 09:20", [100, 101, 102, 103, 104])
        result = _run_backtest(
            df,
            entry_conditions=_always_true_conditions(),
            exit_conditions=_always_true_conditions(),
            strategy_config={
                "instrument": "RELIANCE",
                "timeframe_minutes": 1,
                "lot_size": 1,
                "signal_exit_delay_candles": 1,
            },
        )

        trade = result["trades"][0]
        self.assertEqual(trade["entry_time"], "2026-03-18 09:21")
        self.assertEqual(trade["exit_time"], "2026-03-18 09:23")
        self.assertEqual(trade["exit_reason"], "Signal")

    def test_spread_and_slippage_adjust_fill_prices(self):
        df = _make_ohlcv("2026-03-18 09:20", [100, 101, 102])
        result = _run_backtest(
            df,
            entry_conditions=_always_true_conditions(),
            exit_conditions=[],
            strategy_config={
                "instrument": "RELIANCE",
                "timeframe_minutes": 1,
                "lot_size": 1,
                "combined_sqoff_time": "09:22",
                "spread_bps": 100,
                "entry_slippage_bps": 50,
                "exit_slippage_bps": 50,
            },
        )

        trade = result["trades"][0]
        self.assertAlmostEqual(trade["entry_price"], 102.01, places=2)
        self.assertAlmostEqual(trade["exit_price"], 100.98, places=2)

    def test_capital_enforcement_rejects_short_option_margin(self):
        df = _make_ohlcv("2026-03-18 09:20", [100, 101, 102, 103])
        result = _run_backtest(
            df,
            entry_conditions=_always_true_conditions(),
            exit_conditions=[],
            strategy_config={
                "instrument": "26000",
                "timeframe_minutes": 1,
                "initial_capital": 50000,
                "enforce_capital": True,
                "lot_size": 1,
                "legs": [
                    {
                        "option_type": "CE",
                        "transaction_type": "SELL",
                        "strike_type": "atm",
                        "lots": 1,
                    }
                ],
            },
        )

        self.assertEqual(result["status"], "no_trades")
        self.assertGreaterEqual(result["stats"]["capital_rejections"], 1)


if __name__ == "__main__":
    unittest.main()
