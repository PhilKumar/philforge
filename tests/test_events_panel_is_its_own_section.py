"""The event log is a section of its own, on all four strategies.

It used to live INSIDE the campaign monitor -- so it appeared and vanished
with the monitor, and on Fib Boundary it ran on under the closed round as one
unbroken wall (Phil, 2026-08-26: "put the NIFTY campaign events in a separate
section like the Closed paper campaigns in a flowdown panel. Do the same for
all 4 strategies"). High Entry had no log on the page at all, though its
engine had been recording one the whole time.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKUP = (ROOT / "strategy.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "philforge-app.css").read_text(encoding="utf-8")
HOST = (ROOT / "engine" / "candle_recovery_host.py").read_text(encoding="utf-8")

TAG = re.compile(r"<(/?)(?:div|section|details)\b")


def _ancestors(needle: str) -> list:
    """Every enclosing element of the line carrying `needle`, innermost first."""
    lines = MARKUP.split("\n")
    index = next(i for i, line in enumerate(lines) if needle in line)
    depth, out = 0, []
    for i in range(index - 1, -1, -1):
        for closing in TAG.findall(lines[i]):
            depth += 1 if closing else -1
        if depth < 0:
            found = re.search(r'id="([^"]+)"', lines[i]) or re.search(r'class="([^"]*)"', lines[i])
            out.append(found.group(1) if found else "?")
            depth = 0
    return out


class EventsAreTheirOwnSectionTests(unittest.TestCase):
    # The marker that identifies each strategy's events section.
    SECTIONS = {
        "candle_entry": 'id="oc-candle-event-count"',
        "gap_carry": 'id="oc-gap-event-count"',
        "high_entry": 'id="oc-high-events-panel"',
        "fib_boundary": 'id="oc-fib-events-tpl"',
    }

    def test_every_strategy_has_one(self):
        for strategy, marker in self.SECTIONS.items():
            with self.subTest(strategy=strategy):
                self.assertIn(marker, MARKUP)

    def test_none_of_them_sits_inside_the_panes(self):
        # Inside the live pane it is the monitor's business and disappears with
        # it; outside, it is a section of the strategy.
        for strategy, marker in self.SECTIONS.items():
            if strategy == "fib_boundary":
                continue  # a <template>, cloned into its host; checked below
            with self.subTest(strategy=strategy):
                ancestors = _ancestors(marker)
                self.assertNotIn("ocp-panes", ancestors)
                self.assertNotIn("ocp-pane ocp-pane-live", ancestors)

    def test_fib_clones_one_log_per_ladder(self):
        # Two instruments keep two logs, so this one is cloned, not shared.
        self.assertIn('id="oc-fib-events-host"', MARKUP)
        self.assertNotIn("ocp-panes", _ancestors('id="oc-fib-events-host"'))
        self.assertIn("eventsTpl.content.firstElementChild.cloneNode(true)", SCRIPT)
        self.assertIn("events.dataset.fxSymbol = key", SCRIPT)

    def test_each_one_flows_down_from_its_own_head(self):
        self.assertGreaterEqual(MARKUP.count("ocp-monitor-fold"), 3)
        self.assertIn(".ocp-monitor-fold > summary", CSS)
        self.assertIn(".ocp-monitor-fold[open] > summary::after", CSS)


class HighEntryEventsTests(unittest.TestCase):
    def test_the_engine_finally_ships_the_log_it_was_keeping(self):
        # The list is now sorted onto one clock and each row tagged with its
        # campaign, so the literal changed; what matters is that it is shipped.
        self.assertIn('"events": sorted(', HOST)
        self.assertIn('getattr(campaign.engine, "events", [])', HOST)
        self.assertIn('getattr(campaign.engine, "events", [])', HOST)

    def test_the_page_renders_them(self):
        self.assertIn("oc-high-events", SCRIPT)
        self.assertIn("Array.isArray(book.events)", SCRIPT)


if __name__ == "__main__":
    unittest.main()
