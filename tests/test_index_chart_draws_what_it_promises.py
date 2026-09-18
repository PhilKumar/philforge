"""The desk chart must draw the frame its own header advertises.

Phil, 2026-09-09: "The chart doesn't show the CPR markings and the supertrend".
The header reads "CPR, R1-R4, S1-S5, 20-EMA", but /api/live/index-chart took
only analytics["overlays"] and threw analytics["lines"] away, so the chart
carried the 20-EMA and nothing else.

Both live books also read a Supertrend on 3-minute bars (PE_NoTarget 10,2,
CE_SL15_NoMonTue 10,2.7). Phil, 2026-09-18: "put only one supertrend line...
this is completely confusing" -- the chart draws ONE, computed on the 3m bars
the rule reads, not on the chart's own candles.
"""

import ast
import os
import random
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PY = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
APP_JS = open(os.path.join(ROOT, "static", "philforge-app.js"), encoding="utf-8").read()


def _route_body() -> str:
    return APP_PY.split('@app.get("/api/live/index-chart")')[1].split("@app.get(")[0]


def _fake_candles(n: int = 400) -> list[dict]:
    random.seed(7)
    price = 23800.0
    t0 = int(time.time()) - 300 * n
    out = []
    for i in range(n):
        price += random.gauss(-0.6 if i > n // 2 else 0.4, 6)
        out.append(
            {
                "t": t0 + i * 300,
                "o": round(price, 2),
                "h": round(price + abs(random.gauss(0, 4)), 2),
                "l": round(price - abs(random.gauss(0, 4)), 2),
                "c": round(price, 2),
            }
        )
    return out


class TheChartKeepsItsPivots(unittest.TestCase):
    def test_the_route_does_not_discard_the_pivot_lines(self):
        """The regression: analytics["lines"] holds CPR/R1-R4/S1-S5."""
        body = _route_body()
        self.assertIn('analytics["lines"]', body)
        self.assertIn('"LAST"', body, "LAST is drawn on top of the pivots, not instead of them")

    def test_the_header_names_the_one_line_drawn(self):
        fn = APP_JS.split("async function openCePeIndexChart(")[1][:3000]
        self.assertIn("CPR, R1-R4, S1-S5, 20-EMA", fn)
        self.assertIn("data.supertrend_book", fn)
        self.assertNotIn("dashed", fn)

    def test_the_pivot_builder_really_emits_them(self):
        block = APP_PY.split("def _chart_session_analytics(")[1].split("\ndef ")[0]
        for label in ("CPR TC", "CPR P", "CPR BC", "R1", "R4", "S1", "S4"):
            self.assertIn(f'"{label}"', block, label)


def _minute_frame(days: int = 2):
    import pandas as pd

    random.seed(11)
    stamps, rows, price = [], [], 23300.0
    for d in range(days):
        start = pd.Timestamp("2026-09-17 09:15") + pd.Timedelta(days=d)
        for m in range(375):
            price += random.gauss(-0.4 if (d == days - 1 and m > 200) else 0.3, 3)
            stamps.append(start + pd.Timedelta(minutes=m))
            rows.append((price, price + abs(random.gauss(0, 2)), price - abs(random.gauss(0, 2)), price))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=pd.DatetimeIndex(stamps))


def _five_minute_candles(frame):
    from zoneinfo import ZoneInfo

    bars = (
        frame.resample("5min", label="left", closed="left", origin="start_day", offset="15min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    ist = ZoneInfo("Asia/Kolkata")
    return [
        {"t": int(i.tz_localize(ist).timestamp()), "o": r.open, "h": r.high, "l": r.low, "c": r.close}
        for i, r in bars.iterrows()
    ]


class OneSupertrendOnTheRulesBars(unittest.TestCase):
    def test_the_route_draws_exactly_one(self):
        body = _route_body()
        self.assertIn("_supertrend_book_for_chart(", body)
        self.assertIn("_rule_supertrend_on_chart_bars(", body)
        self.assertNotIn("_supertrend_overlay_lines", APP_PY)
        self.assertIn('candle_type="1"', body, "the 3m line is built from 1m, as the engine builds it")

    def test_the_line_is_the_3m_rule_not_the_chart_candles(self):
        import app

        frame = _minute_frame()
        candles = _five_minute_candles(frame)
        segments = app._rule_supertrend_on_chart_bars(frame, candles, 5, multiplier=2.7)
        self.assertTrue(segments)
        drawn = {p["t"]: p["price"] for seg in segments for p in seg["points"]}

        bars = app.resample_ohlcv(frame, 3, source_timeframe_minutes=1)
        rule = app.supertrend(bars, period=10, multiplier=2.7)["supertrend"]
        # 5m bar 10:00-10:05 closes at 10:05: the last 3m bar closed by then is 10:00-10:03.
        from zoneinfo import ZoneInfo

        import pandas as pd

        ist = ZoneInfo("Asia/Kolkata")
        t = int(pd.Timestamp("2026-09-18 10:00").tz_localize(ist).timestamp())
        self.assertAlmostEqual(drawn[t], round(float(rule[pd.Timestamp("2026-09-18 10:00")]), 2))

    def test_a_flip_starts_a_new_segment_and_colours_it(self):
        import app

        frame = _minute_frame()
        segments = app._rule_supertrend_on_chart_bars(frame, _five_minute_candles(frame), 5, multiplier=2.0)
        self.assertGreaterEqual(len(segments), 2)
        self.assertEqual({s["dir"] for s in segments}, {1, -1})

    def test_no_minutes_no_line(self):
        import app

        self.assertEqual(app._rule_supertrend_on_chart_bars(None, [{"t": 1}], 5, multiplier=2.7), [])


class WhichBooksLine(unittest.TestCase):
    USER = 99904

    def _book(self, side, positions=(), trades=()):
        import app
        from engine.live import LiveEngine

        engine = LiveEngine(object(), run_id=f"B_{side}", state_dir="/tmp")
        engine.strategy = {"legs": [{"option_type": side}]}
        engine.positions = list(positions)
        engine.closed_trades = list(trades)
        app._registry_bucket(app.live_engines, self.USER)[f"B_{side}"] = engine

    def tearDown(self):
        import app

        app._registry_bucket(app.live_engines, self.USER).clear()

    def test_a_filtered_desk_draws_its_own_book(self):
        import app

        self.assertEqual(app._supertrend_book_for_chart(self.USER, "PE"), "PE")
        self.assertEqual(app._supertrend_book_for_chart(self.USER, "ce"), "CE")

    def test_the_book_holding_a_position_wins(self):
        import app

        self._book("CE")
        self._book("PE", positions=[{"status": "open"}])
        self.assertEqual(app._supertrend_book_for_chart(self.USER, "all"), "PE")

    def test_otherwise_the_book_that_traded_last_today(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        import app

        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        self._book("CE", trades=[{"entry_time": f"{today} 09:20:00"}])
        self._book("PE", trades=[{"entry_time": f"{today} 14:30:00"}])
        self.assertEqual(app._supertrend_book_for_chart(self.USER, "all"), "PE")

    def test_a_quiet_day_draws_the_ce_line(self):
        import app

        self._book("PE", trades=[{"entry_time": "2020-01-01 09:20:00"}])
        self.assertEqual(app._supertrend_book_for_chart(self.USER, "all"), "CE")


class TheRouteStillResolves(unittest.TestCase):
    def test_every_name_the_route_uses_exists(self):
        """The last chart bug was a helper copied from another module."""
        tree = ast.parse(APP_PY)
        module_names = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                module_names.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        module_names.add(t.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    module_names.add(a.asname or a.name.split(".")[0])
        for name in ("_rule_supertrend_on_chart_bars", "_supertrend_book_for_chart", "_chart_session_analytics"):
            self.assertIn(name, module_names, f"{name} is not defined at module level")


if __name__ == "__main__":
    unittest.main()
