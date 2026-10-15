"""Gap Carry's 15:10 signal, watched live from 15:00.

Phil, 2026-10-15: "a small status monitor ... after 15:00 for Gap carry to see
the EMA and RSI status". The card reads the latest CLOSED bar with the rule's
own EMA and Wilder RSI, so at 15:10 it shows the decision itself. That day the
15:05 bar closed 22,948.35 below an EMA20 of 22,990.28 with RSI 30.9: a PE
needed 30 or under and missed by 0.9.
"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

import app
from engine.gap_carry import GapCarryConfig, _ema, _wilder_rsi


def _bars(closes, last_start):
    start = last_start - timedelta(minutes=5 * (len(closes) - 1))
    return [SimpleNamespace(timestamp=start + timedelta(minutes=5 * i), close=c) for i, c in enumerate(closes)]


class TheSignalCard(unittest.TestCase):
    def test_it_reads_the_last_closed_bar_with_the_rules_own_numbers(self):
        closes = [23100 - i * 3 for i in range(60)]
        rows = _bars(closes, datetime(2026, 10, 15, 15, 5))
        body = app._gap_carry_signal_payload(rows, GapCarryConfig(), datetime(2026, 10, 15, 15, 7))
        # The 15:05 bar has not closed at 15:07: the card reads 15:00.
        self.assertEqual((body["bar_start"], body["bar_end"]), ("15:00", "15:05"))
        used = [float(c) for c in closes[:-1]]
        self.assertEqual(body["ema"], round(_ema(used, 20)[-1], 2))
        self.assertEqual(body["rsi"], round(_wilder_rsi(used, 14)[-1], 1))
        self.assertFalse(body["above_ema"])
        self.assertEqual(body["side"], "PE")  # a steady fall: RSI at the floor
        self.assertNotIn("decided", body)

    def test_from_the_entry_time_it_carries_the_decision(self):
        closes = [23000 + (i % 2) * 4 for i in range(60)]
        rows = _bars(closes, datetime(2026, 10, 15, 15, 5))
        body = app._gap_carry_signal_payload(rows, GapCarryConfig(), datetime(2026, 10, 15, 15, 11))
        self.assertEqual(body["bar_start"], "15:05")
        self.assertIsNone(body["side"])
        self.assertIn("decided", body)
        self.assertIsNone(body["decided"]["side"])

    def test_too_little_history_says_so(self):
        rows = _bars([23000.0] * 10, datetime(2026, 10, 15, 15, 0))
        body = app._gap_carry_signal_payload(rows, GapCarryConfig(), datetime(2026, 10, 15, 15, 7))
        self.assertEqual(body["status"], "empty")


if __name__ == "__main__":
    unittest.main()
