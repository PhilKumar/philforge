"""The chart's first frame never starts past its last candle.

Phil, 2026-11-02, on the Scalp chart after the close: "Cannot read properties
of undefined (reading 'l')". Once the day has closed the chart lays tomorrow's
CPR in a block of empty bars past the last candle (77 of them on 5m), and it
reopens at the span Phil last zoomed to. Counted back from the RIGHT EDGE, a
span narrower than that block began beyond the data, and the price fit read
the low of a bar that does not exist. The span is now counted back from the
last real candle, with tomorrow's block riding on after it.

The renderer's own file is run under Node, not reimplemented.
"""

import json
import os
import shutil
import subprocess
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BENCH_JS = os.path.join(_HERE, "static", "philforge-bench-chart.js")
_NODE = shutil.which("node")

_HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const ctx = { console, Math, Number, isFinite,
  document: { addEventListener() {}, querySelector() { return null; }, getElementById() { return null; } } };
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
const data = JSON.parse(process.argv[2]);
const t0 = 1793600000;
data.candles = Array.from({ length: data.n }, (_, i) => ({ t: t0 + i * 300, o: 100, h: 101, l: 99, c: 100 }));
console.log(JSON.stringify(ctx._pfChartCanvasFit({ data }, true)));
"""


@unittest.skipIf(_NODE is None, "node is not installed")
class TheFirstFrameHoldsCandles(unittest.TestCase):
    def fit(self, **data):
        proc = subprocess.run(
            [_NODE, "-e", _HARNESS, "--", _BENCH_JS, json.dumps(data)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            self.fail(f"node failed: {proc.stderr.strip()}")
        return json.loads(proc.stdout)

    def test_a_span_narrower_than_tomorrows_block_still_frames_candles(self):
        fit = self.fit(n=150, future_bars=77, initial_bars=24)
        self.assertEqual(fit["tMin"], 149.5 - 24)  # the last 24 candles...
        self.assertEqual(fit["tMax"], 149.5 + 77)  # ...then tomorrow's block
        self.assertEqual((fit["pMin"] < 99, fit["pMax"] > 101), (True, True))

    def test_without_tomorrows_block_the_span_is_unchanged(self):
        fit = self.fit(n=150, initial_bars=24)
        self.assertEqual((fit["tMin"], fit["tMax"]), (125.5, 149.5))

    def test_a_span_wider_than_the_data_shows_all_of_it(self):
        fit = self.fit(n=10, future_bars=77, initial_bars=500)
        self.assertEqual(fit["tMin"], -0.5)


if __name__ == "__main__":
    unittest.main()
