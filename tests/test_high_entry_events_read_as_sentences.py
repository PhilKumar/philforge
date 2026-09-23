"""The High Entry event log says what happened, in words.

Phil, 2026-09-23: "put sensible events on the high entry campaign events."
It printed the engine's own key with underscores swapped -- "eod square off",
"no contract" -- and dropped the payload beside it, so the panel could say that
something happened and never what.  Every number was already being recorded.

The second half is correctness rather than wording: index prices in an event
are ENGINE-space, and a put campaign runs on MIRRORED bars, so a PE stop was
logged at -23,352.05.  Harmless while a run was all one side; now that a call
book and a put book share a host it would be on screen.
"""

import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")
HOST = (ROOT / "engine" / "candle_recovery_host.py").read_text(encoding="utf-8")


class EveryEventTheEngineEmitsHasASentence(unittest.TestCase):
    """A missing case falls through to the old underscore-swapped key."""

    def test_no_engine_event_is_left_without_wording(self):
        engine = (ROOT / "engine" / "candle_recovery.py").read_text(encoding="utf-8")
        emitted = set()
        for chunk in engine.split("self._log(")[1:]:
            head = chunk[: chunk.index(")") if ")" in chunk else len(chunk)]
            for piece in head.split(","):
                piece = piece.strip()
                if piece.startswith('"') and piece.endswith('"'):
                    emitted.add(piece.strip('"'))
                    break
        self.assertTrue(emitted, "no events found in the engine at all")
        body = JS[JS.index("function _recoveryEventSentence(") :]
        body = body[: body.index("\n// The recipe is only honest")]
        missing = sorted(e for e in emitted if f"case '{e}':" not in body)
        self.assertEqual(missing, [], f"these events would print as a raw key: {missing}")

    def test_the_sentences_use_the_payload_not_just_the_name(self):
        body = JS[JS.index("function _recoveryEventSentence(") :]
        for field in ("ev.trigger", "ev.sl", "ev.close", "ev.premium", "ev.booked", "ev.net"):
            self.assertIn(field, body, f"{field} is recorded but never shown")

    def test_a_campaign_over_reason_is_explained(self):
        body = JS[JS.index("function _recoveryEventSentence(") :]
        for reason in ("recovered", "horizon", "end_of_data"):
            self.assertIn(f"{reason}:", body, f"'{reason}' would read as a bare word")

    def test_the_log_shows_which_side_the_event_belongs_to(self):
        """Two books in one stream, otherwise indistinguishable."""
        self.assertIn("const side = String(ev.side || '').toUpperCase();", JS)


class APutEventReadsInRealPrices(unittest.IsolatedAsyncioTestCase):
    async def test_a_mirrored_campaign_is_unmirrored_on_the_way_out(self):
        from engine.candle_recovery import RecoveryBar
        from engine.candle_recovery_host import CandleRecoveryHost, RecoveryCampaign

        host = CandleRecoveryHost.__new__(CandleRecoveryHost)
        mother = RecoveryBar(datetime(2026, 9, 22, 9, 15), -1.0, -1.0, -1.0, -1.0)
        pe = RecoveryCampaign("nifty:5m:PE:x", mother, "ladder", datetime(2026, 9, 22), side="PE")
        row = host._event_row(
            pe,
            {
                "timestamp": "2026-09-22T12:05:00",
                "event": "stopped",
                "close": -23352.05,
                "sl": -23355.25,
                "net": -260.0,
            },
        )
        self.assertAlmostEqual(row["close"], 23352.05)
        self.assertAlmostEqual(row["sl"], 23355.25)
        self.assertEqual(row["net"], -260.0, "rupees are not mirrored, only index prices")
        self.assertEqual(row["side"], "PE")
        self.assertEqual(row["campaign_id"], "nifty:5m:PE:x")

    async def test_a_call_event_is_left_exactly_as_it_was(self):
        from engine.candle_recovery import RecoveryBar
        from engine.candle_recovery_host import CandleRecoveryHost, RecoveryCampaign

        host = CandleRecoveryHost.__new__(CandleRecoveryHost)
        mother = RecoveryBar(datetime(2026, 9, 22, 9, 15), 1.0, 1.0, 1.0, 1.0)
        ce = RecoveryCampaign("nifty:5m:CE:x", mother, "ladder", datetime(2026, 9, 22), side="CE")
        row = host._event_row(ce, {"event": "stopped", "close": 23352.05, "sl": 23355.25})
        self.assertAlmostEqual(row["close"], 23352.05)
        self.assertEqual(row["side"], "CE")

    async def test_a_mirrored_zone_swaps_upper_and_lower(self):
        from engine.candle_recovery import RecoveryBar
        from engine.candle_recovery_host import CandleRecoveryHost, RecoveryCampaign

        host = CandleRecoveryHost.__new__(CandleRecoveryHost)
        mother = RecoveryBar(datetime(2026, 9, 22, 9, 15), -1.0, -1.0, -1.0, -1.0)
        pe = RecoveryCampaign("nifty:5m:PE:x", mother, "fib-zone", datetime(2026, 9, 22), side="PE")
        row = host._event_row(pe, {"event": "zones_drawn", "zones": [{"level": 2, "upper": -100.0, "lower": -120.0}]})
        zone = row["zones"][0]
        self.assertAlmostEqual(zone["upper"], 120.0)
        self.assertAlmostEqual(zone["lower"], 100.0)


class TheEventsAreOnOneClock(unittest.TestCase):
    def test_two_campaigns_interleave_by_time(self):
        """Concatenating per campaign put a whole book after the other."""
        snapshot = HOST[HOST.index("    def snapshot(self)") :]
        self.assertIn('key=lambda row: str(row.get("timestamp") or "")', snapshot)


if __name__ == "__main__":
    unittest.main()
