"""The desk chart must draw the frame its own header advertises.

Phil, 2026-09-09: "The chart doesn't show the CPR markings and the supertrend".
The header reads "CPR, R1-R4, S1-S4, 20-EMA", but /api/live/index-chart took
only analytics["overlays"] and threw analytics["lines"] away, so the chart
carried the 20-EMA and nothing else.

Both live books also read a Supertrend (PE_NoTarget on Supertrend_10_2_3m,
CE_SL15_NoMonTue on Supertrend_10_2), so the index chart draws that too.
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


def _helpers():
    start = APP_PY.index("def _supertrend_overlay(")
    end = APP_PY.index("def _chart_session_analytics(")
    ns: dict = {}
    exec(APP_PY[start:end], ns)  # noqa: S102 - reading our own source under test
    return ns


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
        """The regression: analytics["lines"] holds CPR/R1-R4/S1-S4."""
        body = _route_body()
        self.assertIn('analytics["lines"]', body)
        self.assertIn('"LAST"', body, "LAST is drawn on top of the pivots, not instead of them")

    def test_the_header_and_the_payload_agree(self):
        """A header promising CPR must not sit above an EMA-only chart."""
        fn = APP_JS.split("async function openCePeIndexChart(")[1][:3000]
        self.assertIn("CPR, R1-R4, S1-S4, 20-EMA", fn)
        self.assertIn("Supertrend 10,2.7 (CE)", fn)
        self.assertIn("Supertrend 10,2 (PE)", fn)
        self.assertIn('analytics["lines"]', _route_body())
        self.assertIn("_supertrend_overlay_lines(candles, book)", _route_body())

    def test_the_pivot_builder_really_emits_them(self):
        """_chart_session_analytics is the source; prove it labels the pivots."""
        block = APP_PY.split("def _chart_session_analytics(")[1].split("\ndef ")[0]
        for label in ("CPR TC", "CPR P", "CPR BC", "R1", "R4", "S1", "S4"):
            self.assertIn(f'"{label}"', block, label)


class TheChartDrawsTheSupertrend(unittest.TestCase):
    def test_it_reuses_the_one_implementation(self):
        """A second supertrend in app.py would be a second set of numbers."""
        block = APP_PY.split("def _supertrend_overlay(")[1].split("\ndef _supertrend_overlay_lines")[0]
        self.assertIn("from engine.indicators import supertrend", block)
        self.assertNotIn("Wilder", block, "the maths belongs in engine/indicators.py")

    def test_it_produces_coloured_segments(self):
        ns = _helpers()
        overlays = ns["_supertrend_overlay_lines"](_fake_candles())
        self.assertTrue(overlays, "supertrend produced no overlay")
        self.assertEqual(sorted({o["color"] for o in overlays}), ["#4ade80", "#f87171"])
        for o in overlays:
            self.assertGreaterEqual(len(o["points"]), 2)

    def test_the_legend_names_each_line_once(self):
        """One entry per Supertrend drawn, not one per flip."""
        ns = _helpers()
        for book, expected in (("PE", ["PE ST 10,2"]), ("CE", ["CE ST 10,2.7"])):
            labelled = [o["label"] for o in ns["_supertrend_overlay_lines"](_fake_candles(), book) if o["label"]]
            self.assertEqual(labelled, expected, book)

    def test_each_book_gets_the_supertrend_it_actually_trades(self):
        """PE_NoTarget exits on Supertrend_10_2_3m and CE_SL15_NoMonTue on
        Supertrend_10_2.7_3m. One line for both is wrong for one of them."""
        ns = _helpers()
        pe = ns["_supertrend_overlay_lines"](_fake_candles(), "PE")
        ce = ns["_supertrend_overlay_lines"](_fake_candles(), "CE")
        self.assertNotEqual(pe[0]["points"], ce[0]["points"])

    def test_showing_both_tells_them_apart(self):
        ns = _helpers()
        both = ns["_supertrend_overlay_lines"](_fake_candles(), "all")
        labels = sorted(o["label"] for o in both if o["label"])
        self.assertEqual(labels, ["CE ST 10,2.7", "PE ST 10,2"])
        self.assertTrue(any(o.get("dash") for o in both), "the two lines must be distinguishable")

    def test_a_short_series_is_declined_rather_than_faked(self):
        ns = _helpers()
        self.assertEqual(ns["_supertrend_overlay_lines"](_fake_candles(10)), [])

    def test_the_route_asks_for_it(self):
        self.assertIn("_supertrend_overlay_lines(candles, book)", _route_body())


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
        for name in ("_supertrend_overlay", "_supertrend_overlay_lines", "_chart_session_analytics"):
            self.assertIn(name, module_names, f"{name} is not defined at module level")


if __name__ == "__main__":
    unittest.main()
