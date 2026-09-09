"""The compounding ladder: one more lot per rung of banked profit.

Phil, 2026-09-09: "I want this to go live" -- CE at +25%, PE at +75%.

This rule lives in the engines rather than in an analysis script on purpose.
Sizing used to be estimated by dividing a finished book's P&L by its lot count,
which is wrong whenever fees do not scale with lots -- brokerage is a flat
Rs 80 per order, so they never do, and a 578-trade PE book divided that way
implied lot counts of 1.01, 1.03 and 2.97 instead of 1 and 3.
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKTEST = open(os.path.join(ROOT, "engine", "backtest.py"), encoding="utf-8").read()
LIVE = open(os.path.join(ROOT, "engine", "live.py"), encoding="utf-8").read()


def ladder(base_lots: int, banked: float, capital: float, step_pct: float, max_lots: int = 20) -> int:
    """The rule, written once here so the test states it independently."""
    if step_pct <= 0 or capital <= 0:
        return base_lots
    rung = capital * step_pct / 100.0
    extra = int(max(0.0, banked) // rung)
    return max(1, min(base_lots + extra, max_lots))


class TheRuleItself(unittest.TestCase):
    def test_nothing_banked_means_the_base_size(self):
        self.assertEqual(ladder(2, 0, 100_000, 25), 2)

    def test_a_loss_never_sizes_below_the_base(self):
        """A book in drawdown keeps trading its configured size, not less."""
        self.assertEqual(ladder(2, -80_000, 100_000, 25), 2)

    def test_one_rung_adds_exactly_one_lot(self):
        self.assertEqual(ladder(2, 25_000, 100_000, 25), 3)
        self.assertEqual(ladder(2, 24_999, 100_000, 25), 2)

    def test_it_ratchets_back_down_when_money_is_given_back(self):
        self.assertEqual(ladder(2, 75_000, 100_000, 25), 5)
        self.assertEqual(ladder(2, 30_000, 100_000, 25), 3)

    def test_the_gentler_step_climbs_slower(self):
        """CE runs +25%, PE runs +75% -- the same banked profit, fewer rungs."""
        self.assertEqual(ladder(2, 150_000, 100_000, 25), 8)
        self.assertEqual(ladder(2, 150_000, 100_000, 75), 4)

    def test_the_cap_holds(self):
        self.assertEqual(ladder(2, 10_000_000, 100_000, 25), 20)

    def test_off_by_default(self):
        self.assertEqual(ladder(2, 500_000, 100_000, 0), 2)


class BothEnginesImplementIt(unittest.TestCase):
    """A rule that sizes real orders must not differ between the two engines."""

    def test_the_backtest_reads_the_three_keys(self):
        block = BACKTEST.split("# COMPOUND AS THE BOOK GROWS")[1][:1400]
        for key in ("compound_step_pct", "compound_base_capital", "compound_max_lots"):
            self.assertIn(key, block, key)

    def test_the_live_engine_reads_the_same_three_keys(self):
        block = LIVE.split("# COMPOUND AS THE BOOK GROWS")[1][:1600]
        for key in ("compound_step_pct", "compound_base_capital", "compound_max_lots"):
            self.assertIn(key, block, key)

    def test_both_size_on_banked_money_only(self):
        """An open position, however green, must never size the next entry."""
        bt = BACKTEST.split("# COMPOUND AS THE BOOK GROWS")[1][:1400]
        self.assertIn("total_pnl", bt, "the backtest's realised total")
        lv = LIVE.split("# COMPOUND AS THE BOOK GROWS")[1][:1600]
        self.assertIn("self.closed_trades", lv, "live must sum CLOSED trades")
        self.assertNotIn("unrealized", lv)

    def test_both_use_the_same_arithmetic(self):
        """floor(banked / rung), clamped -- identical in both files."""
        for name, src in (("backtest", BACKTEST), ("live", LIVE)):
            block = src.split("# COMPOUND AS THE BOOK GROWS")[1][:1600]
            self.assertRegex(block, r"rung = ladder_capital \* step_pct / 100\.0", name)
            self.assertRegex(block, r"extra = int\(max\(0\.0, \w+\) // rung\)", name)
            self.assertTrue(re.search(r"min\(\w*lots \+ extra, max_lots\)", block), f"{name} clamp")

    def test_the_live_engine_says_why_it_changed_size(self):
        """Phil could not tell from the logs why a size changed before."""
        block = LIVE.split("# COMPOUND AS THE BOOK GROWS")[1][:1600]
        self.assertIn("self.log_event", block)
        self.assertIn("banked", block)

    def test_it_stacks_on_the_expiry_rule_rather_than_replacing_it(self):
        """expiry_day_lots sets the day's base; the ladder adds to that."""
        bt = BACKTEST.split("expiry_day_lots = int(")[1][:1800]
        self.assertLess(
            bt.index("leg_lots = expiry_day_lots"),
            bt.index("# COMPOUND AS THE BOOK GROWS"),
            "the ladder must be applied after the expiry base is chosen",
        )


if __name__ == "__main__":
    unittest.main()
