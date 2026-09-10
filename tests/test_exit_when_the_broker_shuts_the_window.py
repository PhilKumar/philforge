"""What the engine does when Dhan refuses the exit because the day is over.

2026-09-10, the PE book's first ever live timed square-off:

    15:25:05  🚫 SL order cancelled
    15:25:05  REJECTED  RMS: Intraday orders cannot be placed at this time.
    15:25:06  🚫 SL order cancelled          (attempt 2)
    15:25:08  REJECTED  RMS: Intraday orders cannot be placed at this time.
    15:25:08  🚫 SL order cancelled          (attempt 3)
    15:25:08  REJECTED  RMS: Intraday orders cannot be placed at this time.
    15:25:08  CRITICAL: Manual intervention required.   → engine stopped

Three things were wrong and all three are tested here.

1. It retried. The window does not reopen, so no retry could ever have worked.
2. Each attempt cancelled the stop first. From 15:25:05 the account held 130
   lots of a bought PE with nothing under it, and no way to sell. Dhan squared
   off at 15:26:36; had it not, that was a naked overnight position.
3. It then stopped the engine and set `manual_intervention_required`, which
   ALSO blocks the restore on the next start. So the book disappeared from the
   Live page while the position it believed it held had already been sold.
"""

import asyncio
import unittest
from datetime import datetime, time
from pathlib import Path

from engine.live import BROKER_INTRADAY_CUTOFF, LiveEngine, _intraday_window_shut

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "engine" / "live.py").read_text(encoding="utf-8")

REJECTION = "REJECTED RMS:322260910240811:Intraday orders cannot be placed at this time."


class TheRefusalIsRecognised(unittest.TestCase):
    def test_dhans_wording_is_matched(self):
        self.assertTrue(_intraday_window_shut(REJECTION))
        self.assertTrue(_intraday_window_shut(Exception(REJECTION)))

    def test_other_rejections_are_not_swallowed_by_it(self):
        """An insufficient-funds refusal IS worth retrying — two seconds later
        on 2026-09-08 the identical order needed no margin at all."""
        self.assertFalse(_intraday_window_shut("REJECTED RMS: You have insufficient funds."))
        self.assertFalse(_intraday_window_shut(""))
        self.assertFalse(_intraday_window_shut(None))

    def test_the_cutoff_is_before_the_time_that_was_refused(self):
        self.assertLess(BROKER_INTRADAY_CUTOFF, time(15, 25))


class _Engine(LiveEngine):
    """A live engine with the broker and the disk taken out."""

    def __init__(self):
        self.logs = []
        self.positions = []
        self.closed_trades = []
        self.manual_intervention_required = False
        self.running = True
        self.sl_orders_placed = 0
        self.reconciled = 0
        self.saved = 0
        self.sl_placement_fails = False

    def log_event(self, event_type, message, data=None):
        self.logs.append((event_type, message))

    def _save_state(self):
        self.saved += 1

    async def _place_sl_order(self, pos):
        if self.sl_placement_fails:
            raise RuntimeError("Dhan said no")
        self.sl_orders_placed += 1
        pos["sl_order_id"] = "restored-1"

    async def _reconcile_broker_positions(self, callback=None):
        self.reconciled += 1
        return True

    def messages(self):
        return " | ".join(m for _lvl, m in self.logs)


def _position():
    return {
        "leg_num": 1,
        "trading_symbol": "NIFTY 23700PE 2026-09-15",
        "status": "open",
        "quantity": 130,
        "sl_pct": 20.0,
        "entry_premium": 265.0,
        "sl_order_id": "23226091013711",
    }


class TheStopGoesBackOn(unittest.TestCase):
    def test_a_cancelled_stop_is_restored_when_the_exit_fails(self):
        engine = _Engine()
        pos = _position()
        asyncio.run(engine._restore_stop_after_failed_exit(pos, "CANCELLED"))
        self.assertEqual(engine.sl_orders_placed, 1)
        self.assertIn("Stop restored", engine.messages())

    def test_an_unconfirmed_cancel_is_treated_as_cancelled(self):
        """`UNCONFIRMED` means we asked and never got told. Assuming the stop
        survived would leave the position naked if it did not."""
        engine = _Engine()
        asyncio.run(engine._restore_stop_after_failed_exit(_position(), "UNCONFIRMED"))
        self.assertEqual(engine.sl_orders_placed, 1)

    def test_a_stop_that_already_traded_is_not_replaced(self):
        """It filled. Placing another would open a fresh short."""
        engine = _Engine()
        asyncio.run(engine._restore_stop_after_failed_exit(_position(), "TRADED"))
        self.assertEqual(engine.sl_orders_placed, 0)

    def test_nothing_is_placed_when_there_was_no_stop(self):
        engine = _Engine()
        asyncio.run(engine._restore_stop_after_failed_exit(_position(), "NONE"))
        self.assertEqual(engine.sl_orders_placed, 0)

    def test_a_closed_position_is_not_re_protected(self):
        engine = _Engine()
        pos = _position()
        pos["status"] = "closed"
        asyncio.run(engine._restore_stop_after_failed_exit(pos, "CANCELLED"))
        self.assertEqual(engine.sl_orders_placed, 0)

    def test_a_leg_with_no_stop_rule_gets_none(self):
        engine = _Engine()
        pos = _position()
        pos["sl_pct"] = 0
        asyncio.run(engine._restore_stop_after_failed_exit(pos, "CANCELLED"))
        self.assertEqual(engine.sl_orders_placed, 0)

    def test_failing_to_restore_says_so_in_plain_words(self):
        """The one outcome that must never be silent."""
        engine = _Engine()
        engine.sl_placement_fails = True
        asyncio.run(engine._restore_stop_after_failed_exit(_position(), "CANCELLED"))
        self.assertIn("OPEN WITH NO STOP", engine.messages())


class TheEngineHandsOverInsteadOfFighting(unittest.TestCase):
    def _handover(self):
        engine = _Engine()
        pos = _position()
        engine.positions.append(pos)
        asyncio.run(engine._hand_over_to_broker_squareoff(pos, "CANCELLED"))
        return engine, pos

    def test_the_position_is_marked_so_nothing_re_sends_the_exit(self):
        engine, pos = self._handover()
        self.assertTrue(pos["_awaiting_broker_squareoff"])
        self.assertGreater(pos["_exit_retry_after"], datetime.now())

    def test_the_stop_is_put_back(self):
        engine, _pos = self._handover()
        self.assertEqual(engine.sl_orders_placed, 1)

    def test_the_engine_keeps_running(self):
        """It has to, or nothing is left to notice the broker's own square-off."""
        engine, _pos = self._handover()
        self.assertTrue(engine.running)

    def test_it_does_not_block_tomorrows_restore(self):
        """`manual_intervention_required` also stops the engine being restored
        on the next start — which is why the book vanished from the page."""
        engine, _pos = self._handover()
        self.assertFalse(engine.manual_intervention_required)

    def test_it_says_the_square_off_time_is_the_problem(self):
        engine, _pos = self._handover()
        self.assertIn("intraday window is shut", engine.messages())
        self.assertIn("moved earlier", engine.messages())


class TheMarketCloseLoopLetsGo(unittest.TestCase):
    def test_a_handed_over_position_is_reconciled_not_re_sold(self):
        source = SRC.split("async def _force_market_close_if_needed")[1].split("\n    def ")[0]
        self.assertIn("_awaiting_broker_squareoff", source)
        self.assertIn("_reconcile_broker_positions", source)

    def test_the_exit_path_checks_for_the_shut_window_before_counting_attempts(self):
        """Counting it as attempt 1 of 3 is what produced three cancelled stops."""
        source = SRC.split("async def _exit_position")[1].split("\n    def ")[0]
        shut = source.index("_intraday_window_shut")
        attempts = source.index('pos["_exit_attempts"] = pos.get("_exit_attempts", 0) + 1')
        self.assertLess(shut, attempts)


class TheConfigIsCheckedBeforeTheDayStarts(unittest.TestCase):
    def test_a_square_off_past_the_cutoff_is_called_out(self):
        source = SRC.split("def _apply_session_times")[1].split("\n    def ")[0]
        self.assertIn("BROKER_INTRADAY_CUTOFF", source)
        self.assertIn("REJECTED", source)


if __name__ == "__main__":
    unittest.main()
