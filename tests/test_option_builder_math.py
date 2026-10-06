"""The Option Builder's payoff math, run under node.

The math lives in static/option-builder-math.js because the chart recomputes it
on every slider move; these checks hold it to textbook values -- put-call
parity, the max profit / loss / breakevens of the standard spreads, and the
target curve passing through today's real P&L.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not installed")
class ThePayoffMath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run(
            [NODE, str(ROOT / "tests" / "option_builder_math.test.js")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        cls.results = json.loads(out.stdout.strip().splitlines()[-1])

    def test_every_check_passes(self):
        failed = [f"{r['name']}: {r['detail']}" for r in self.results if not r["ok"]]
        self.assertEqual(failed, [])

    def test_the_checks_ran(self):
        self.assertGreaterEqual(len(self.results), 25)


if __name__ == "__main__":
    unittest.main()
