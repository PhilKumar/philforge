"""The High Entry strip must read the RUN, and the run lives under `book`.

Phil, 2026-09-23: "I am not seeing any change on the UI?"  The strip had been
rewritten to describe the running campaigns instead of the form, and it read
`status.sides_running` / `status.timeframe` / `status.config` -- but
`_recovery_status_payload` nests the host snapshot under `book`.  Every read
found `undefined`, every one fell back to the form value, and the strip went on
describing the form while claiming to describe the book.  Nothing failed: JS
reads a missing key as undefined, and `??` made the fallback look deliberate.

So this pins BOTH ends of that contract together.  Move the nesting or move the
read and this test says so.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")


def _recipe_body() -> str:
    start = JS.index("function _recoveryRecipe(")
    return JS[start : JS.index("\nfunction ", start + 1)]


class TheSnapshotIsNestedUnderBook(unittest.TestCase):
    def test_the_status_payload_nests_it(self):
        payload = APP[APP.index("def _recovery_status_payload(") :]
        payload = payload[: payload.index("\n\n\n")]
        self.assertIn('"book": runtime.host.snapshot()', payload)

    def test_the_snapshot_is_the_only_place_those_fields_live(self):
        """If they were top level too, the strip's old read would have worked."""
        payload = APP[APP.index("def _recovery_status_payload(") :]
        payload = payload[: payload.index("\n\n\n")]
        for field in ('"sides_running"', '"timeframe"', '"config"'):
            self.assertNotIn(field, payload, f"{field} is not top level; the strip must read book")


class TheStripReadsTheRunNotTheForm(unittest.TestCase):
    def test_it_takes_the_book_off_the_status(self):
        self.assertRegex(_recipe_body(), r"const book\s*=\s*status\.book\s*\|\|")

    def test_every_run_field_is_read_from_the_book(self):
        body = _recipe_body()
        for field in ("sides_running", "timeframe", "config"):
            self.assertIn(f"book.{field}", body, f"{field} must come off book, not status")
            # the bug exactly: the same name read straight off the status
            self.assertNotRegex(body, rf"status\.{field}\b")

    def test_both_sides_can_be_named_at_once(self):
        """The whole point: a run carrying CE and PE says so."""
        self.assertIn("sides.join(' + ')", _recipe_body())

    def test_the_strip_still_falls_back_to_the_form_when_idle(self):
        body = _recipe_body()
        self.assertIn("if (!running) { host.innerHTML = form; return; }", body)


class TheHostPublishesWhatTheStripNeeds(unittest.TestCase):
    def test_the_snapshot_carries_the_sides_actually_running(self):
        host = (ROOT / "engine" / "candle_recovery_host.py").read_text(encoding="utf-8")
        snapshot = host[host.index("    def snapshot(self)") :]
        self.assertIn('"sides_running": sorted({c.side for c in self.campaigns.values()})', snapshot)


if __name__ == "__main__":
    unittest.main()
