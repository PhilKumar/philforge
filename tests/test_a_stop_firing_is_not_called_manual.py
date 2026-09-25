"""A stop the engine placed itself must not be reported as a manual exit.

25-Sep-2026, Phil: "The live trade exited with reason manual exit.. but I've not
exited manually..." He had not. His own resting stop had been hit — entry
₹242.60, trigger ₹194.08, filled ₹193.85 — and the PAPER book of the same
strategy logged STOP_LOSS for the same moment, one second apart.

The engine placed that stop itself at entry and then failed to recognise it:
anything that closed a position without an exit order of its own was labelled
BROKER_MANUAL_EXIT. So the exit the engine is most certain about was reported to
him as somebody else's doing — alarming, false, and written into the trade
history, which is where every later reading of these books comes from.

The broker's trade book carries the order id of the fill, so this is decided,
not guessed.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.live import LiveEngine  # noqa: E402


class _Engine:
    def __init__(self):
        self.event_log = []

    def log_event(self, event_type, message, data=None):
        self.event_log.append(message)

    _safe_float = staticmethod(LiveEngine._safe_float)  # it is a staticmethod on the engine
    _broker_close_reason = LiveEngine._broker_close_reason


def _pos(**kw):
    base = {
        "leg_num": 1,
        "transaction_type": "BUY",
        "entry_premium": 242.60,
        "sl_order_id": "34226092513511",
        "sl_price": 194.08,
    }
    base.update(kw)
    return base


class AStopFiringIsNotCalledManual(unittest.TestCase):
    def setUp(self):
        self.engine = _Engine()

    def test_the_real_case_our_own_stop_order_filled(self):
        reason = self.engine._broker_close_reason(_pos(), 193.85, "34226092513511")
        self.assertEqual(reason, "STOP_LOSS")
        self.assertTrue(any("our stop order" in m.lower() for m in self.engine.event_log), self.engine.event_log)

    def test_a_genuine_manual_close_is_still_called_manual(self):
        """Phil closing it himself fills under a DIFFERENT order id, above the stop."""
        reason = self.engine._broker_close_reason(_pos(), 265.00, "99999999999")
        self.assertEqual(reason, "BROKER_MANUAL_EXIT")

    def test_without_an_order_id_the_price_decides_and_says_so(self):
        reason = self.engine._broker_close_reason(_pos(), 193.85, "")
        self.assertEqual(reason, "STOP_LOSS")
        self.assertTrue(any("no order id" in m for m in self.engine.event_log), self.engine.event_log)

    def test_without_an_order_id_a_fill_above_the_stop_stays_manual(self):
        self.assertEqual(self.engine._broker_close_reason(_pos(), 265.00, ""), "BROKER_MANUAL_EXIT")

    def test_a_short_leg_reads_the_stop_the_other_way_up(self):
        short = _pos(transaction_type="SELL", sl_price=300.0, sl_order_id="")
        self.assertEqual(self.engine._broker_close_reason(short, 305.0, ""), "STOP_LOSS")
        self.assertEqual(self.engine._broker_close_reason(short, 250.0, ""), "BROKER_MANUAL_EXIT")

    def test_a_position_with_no_stop_at_all_is_manual(self):
        bare = _pos(sl_order_id=None, sl_price=None)
        self.assertEqual(self.engine._broker_close_reason(bare, 193.85, ""), "BROKER_MANUAL_EXIT")

    def test_the_stop_id_must_match_exactly_not_merely_be_present(self):
        """A different order closing it while our stop still rests is not a stop."""
        self.assertEqual(self.engine._broker_close_reason(_pos(), 300.00, "11111111111"), "BROKER_MANUAL_EXIT")


if __name__ == "__main__":
    unittest.main()
