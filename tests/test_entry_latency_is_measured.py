"""An entry that is late must say so, and say WHERE it was late.

On 2026-09-10 a live PE entry signalled on the bar that closed at 09:25:00 and
Dhan accepted the order at 09:25:16.7. Working out that number meant reading a
journal line by line and subtracting timestamps by hand, because the engine
logged every step and never logged the total. So the argument about whether an
option entry was fast enough was an argument about opinions.

Now the engine reports the one number that decides the fill -- bar close to
order -- and the phases that add up to it:

    ⏱ Entry latency: signal→order 1.2s | strike 0.31s | price 0.02s |
                     capital 0.00s | submit 0.28s | fill 0.44s

Read it as: `strike` is the option chain scan, `price` the per-leg premium
preview, `capital` the broker balance check, `submit` the order POST, `fill` the
wait for the fill to be confirmed. Only the first four sit in front of the order.
"""

import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "engine" / "live.py").read_text(encoding="utf-8")


def _body(name: str) -> str:
    return SRC.split(f"def {name}")[1].split("\n    def ")[0]


class TheEntryReportsItsOwnLatency(unittest.TestCase):
    def test_every_phase_in_front_of_the_order_is_timed(self):
        entry = _body("_enter_trade")
        for phase in ("strike", "price", "capital", "submit"):
            self.assertIn(
                f'self._entry_phase_s["{phase}"]',
                entry,
                f"nothing times the {phase} phase, so nobody can say whether it is the slow one",
            )

    def test_the_phases_are_stamped_in_the_order_they_happen(self):
        entry = _body("_enter_trade")
        marks = [p for p in ("strike", "price", "capital", "submit", "fill") if f'_entry_phase_s["{p}"]' in entry]
        positions = [entry.index(f'_entry_phase_s["{p}"]') for p in marks]
        self.assertEqual(positions, sorted(positions), f"phases stamped out of order: {marks}")

    def test_the_capital_check_is_timed_around_the_broker_call_not_before_it(self):
        """The whole point is that the funds round trip is IN FRONT of the order.
        Stamping `capital` before `_can_enter_trade` would time nothing."""
        entry = _body("_enter_trade")
        check = entry.index("await self._can_enter_trade(")
        stamp = entry.index('self._entry_phase_s["capital"]')
        self.assertGreater(stamp, check)

    def test_the_report_is_actually_emitted_on_a_successful_entry(self):
        flush = _body("_flush_pending_order")
        self.assertIn("_log_entry_latency", flush)

    def test_the_headline_is_bar_close_to_order_not_fire_to_order(self):
        """Measuring from the moment the engine noticed would hide the part that
        was worst: on 2026-09-10 nine of the sixteen seconds were gone before
        the engine even looked at the closed bar."""
        report = _body("_log_entry_latency")
        self.assertIn("signal_candle_time", report)
        self.assertIn("timedelta(minutes=self._get_timeframe())", report)


class TheReportReadsCorrectly(unittest.TestCase):
    """Exercise the reporter itself, not its source."""

    def _engine(self):
        from engine.live import LiveEngine

        engine = LiveEngine.__new__(LiveEngine)
        engine.logs = []
        engine.strategy = {"timeframe_minutes": 5}
        engine.entry_conditions = []

        def log_event(event_type, message, data=None):
            engine.logs.append((event_type, message))

        engine.log_event = log_event
        return engine

    def test_it_names_the_phases_and_the_total(self):
        engine = self._engine()
        engine._entry_phase_s = {"strike": 0.31, "price": 0.02, "capital": 0.0, "submit": 0.28, "fill": 0.44}
        bar_open = datetime.now() - timedelta(minutes=5, seconds=1)
        engine._log_entry_latency({"signal_candle_time": bar_open.replace(microsecond=0)})

        self.assertEqual(len(engine.logs), 1)
        line = engine.logs[0][1]
        for phase in ("strike", "price", "capital", "submit", "fill"):
            self.assertIn(phase, line)
        self.assertIn("signal→order", line)

    def test_it_says_nothing_rather_than_something_wrong(self):
        """No phases and no bar time means no claim about latency."""
        engine = self._engine()
        engine._entry_phase_s = {}
        engine._log_entry_latency({})
        self.assertEqual(engine.logs, [])

    def test_a_missing_bar_time_still_reports_the_phases(self):
        engine = self._engine()
        engine._entry_phase_s = {"strike": 1.5}
        engine._log_entry_latency({"signal_candle_time": None})
        self.assertEqual(len(engine.logs), 1)
        self.assertIn("strike 1.50s", engine.logs[0][1])
        self.assertNotIn("signal→order", engine.logs[0][1])


if __name__ == "__main__":
    unittest.main()
