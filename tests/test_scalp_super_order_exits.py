"""A Super Order's exits are checked against the live premium, not guessed.

Phil, 2026-09-23: "Super order placement failed 400 ... DH-906 Profit Price
Should be greater than Order price. Why?" Dhan refuses the WHOLE order --
entry included -- when the target is not on the profitable side of the price,
and answers with a code instead of the numbers. The Scalp form's flat Rs 300
target sat UNDER an option trading above it, and nothing checked before the
order was sent.
"""

import unittest
from pathlib import Path

from scalp import (
    SCALP_DEFAULT_SL_PCT,
    SCALP_DEFAULT_SL_PREMIUM,
    SCALP_DEFAULT_TARGET_PCT,
    SCALP_DEFAULT_TARGET_PREMIUM,
    resolve_scalp_exit_prices,
)

ROOT = Path(__file__).resolve().parent.parent


class TheOrderPhilLost(unittest.TestCase):
    def test_a_buy_target_under_the_price_is_refused_with_the_numbers(self):
        target, _stop, problem = resolve_scalp_exit_prices("BUY", 320.0, 300.0, 100.0)
        self.assertIsNotNone(problem)
        self.assertIn("320", problem)
        self.assertIn("300", problem)
        self.assertIn("above", problem)
        self.assertEqual(target, 300.0)

    def test_the_same_order_passes_once_the_target_clears_the_price(self):
        target, stop, problem = resolve_scalp_exit_prices("BUY", 320.0, 360.0, 280.0)
        self.assertIsNone(problem)
        self.assertEqual((target, stop), (360.0, 280.0))


class TheDirectionIsWhatMatters(unittest.TestCase):
    def test_a_buy_wants_the_target_above_and_the_stop_below(self):
        self.assertIsNone(resolve_scalp_exit_prices("BUY", 100.0, 130.0, 80.0)[2])
        self.assertIn("stop", resolve_scalp_exit_prices("BUY", 100.0, 130.0, 120.0)[2])

    def test_a_sell_wants_the_opposite(self):
        self.assertIsNone(resolve_scalp_exit_prices("SELL", 100.0, 70.0, 130.0)[2])
        self.assertIn("target", resolve_scalp_exit_prices("SELL", 100.0, 130.0, 150.0)[2])
        self.assertIn("stop", resolve_scalp_exit_prices("SELL", 100.0, 70.0, 80.0)[2])


class BlanksFollowThePremium(unittest.TestCase):
    def test_a_blank_buy_pair_is_priced_off_the_option(self):
        target, stop, problem = resolve_scalp_exit_prices("BUY", 80.0, 0, 0)
        self.assertIsNone(problem)
        self.assertAlmostEqual(target, 80.0 * (1 + SCALP_DEFAULT_TARGET_PCT / 100), places=2)
        self.assertAlmostEqual(stop, 80.0 * (1 - SCALP_DEFAULT_SL_PCT / 100), places=2)

    def test_a_blank_sell_pair_inverts(self):
        target, stop, _ = resolve_scalp_exit_prices("SELL", 80.0, 0, 0)
        self.assertLess(target, 80.0)
        self.assertGreater(stop, 80.0)

    def test_the_flat_300_default_no_longer_rides_over_an_80_rupee_option(self):
        target, _stop, problem = resolve_scalp_exit_prices("BUY", 80.0, 0, 0)
        self.assertIsNone(problem)
        self.assertNotEqual(target, SCALP_DEFAULT_TARGET_PREMIUM)

    def test_a_stop_can_never_be_zero_or_negative(self):
        _target, stop, _ = resolve_scalp_exit_prices("BUY", 0.06, 0, 0)
        self.assertGreaterEqual(stop, 0.05)


class WithNoQuoteDhanRemainsTheJudge(unittest.TestCase):
    def test_nothing_is_refused_on_a_price_we_could_not_read(self):
        target, stop, problem = resolve_scalp_exit_prices("BUY", 0.0, 0, 0)
        self.assertIsNone(problem)
        self.assertEqual((target, stop), (SCALP_DEFAULT_TARGET_PREMIUM, SCALP_DEFAULT_SL_PREMIUM))

    def test_typed_values_still_pass_through_unpriced(self):
        self.assertEqual(resolve_scalp_exit_prices("BUY", 0.0, 12.0, 5.0)[:2], (12.0, 5.0))


class ARefusedOrderLeavesATrace(unittest.TestCase):
    """2026-09-23: Phil saw a DH-906 rejection on screen and the server journal
    had no record of it at all -- the handler returned the broker's words to the
    browser and logged nothing, so the order could not be investigated."""

    SRC = (ROOT / "scalp.py").read_text(encoding="utf-8")

    def test_a_broker_exception_is_logged_with_what_was_sent(self):
        block = self.SRC.split("result = self.dhan.place_super_order(")[1].split("# Use a live premium snapshot")[0]
        failure = block.split("except Exception as e:")[1]
        self.assertIn("self._log(", failure)
        for field in ("quoted=", "target=", "SL=", "qty="):
            self.assertIn(field, failure)

    def test_a_rejected_status_is_logged_too(self):
        block = self.SRC.split("result = self.dhan.place_super_order(")[1].split("except Exception as e:")[0]
        self.assertIn("Super Order rejected:", block)
        self.assertIn("quoted=", block)


class ItIsWiredWhereTheOrderIsPlaced(unittest.TestCase):
    SRC = (ROOT / "scalp.py").read_text(encoding="utf-8")

    def test_the_check_runs_before_place_super_order(self):
        head = self.SRC.split("result = self.dhan.place_super_order(")[0]
        self.assertIn("resolve_scalp_exit_prices(", head.split("# ── Immediate (market) entry ──")[1])

    def test_a_pending_stop_limit_entry_is_not_judged_on_todays_price(self):
        """It fills at its TRIGGER, which is deliberately away from the price."""
        pending = self.SRC.split("# ── Stop-limit entry")[1].split("# ── Immediate (market) entry ──")[0]
        self.assertNotIn("resolve_scalp_exit_prices(", pending)

    def test_the_route_no_longer_forces_the_flat_defaults(self):
        app_py = (ROOT / "app.py").read_text(encoding="utf-8")
        block = app_py.split("def _validate_scalp_entry_request(")[1].split("\ndef ")[0]
        self.assertNotIn("req.target_premium = SCALP_DEFAULT_TARGET_PREMIUM", block)


if __name__ == "__main__":
    unittest.main()
