"""Every strategy's closed-campaign table pages ten at a time.

Phil, 2026-09-23: "Apply pagination to all the tables on all the strategies'
closed campaigns."  The CE/PE desk has had a pager since 2026-09-11; the five
strategy ledgers grew without one, and a morning spent testing different
timeframes buries the campaigns that matter.

One renderer draws all five, so the pager belongs there -- and the page is kept
PER STRATEGY and held across a refresh, or a poll would drop you back to page 1
while you were reading page 3.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")

STRATEGIES = ("candle_entry", "fib_boundary", "supertrend", "gap_carry", "candle_recovery")


def _fn(name: str) -> str:
    start = JS.index(f"function {name}(")
    return JS[start : JS.index("\nfunction ", start + 1)]


class AllFiveLedgersShareOnePagedRenderer(unittest.TestCase):
    def test_every_strategy_goes_through_it(self):
        table = JS[JS.index("const _PAPER_LEDGER_UI = {") :]
        table = table[: table.index("};")]
        for strategy in STRATEGIES:
            self.assertIn(strategy, table, f"{strategy} has no ledger table to page")

    def test_the_page_size_is_ten(self):
        self.assertIn("const _PAPER_LEDGER_PAGE_SIZE = 10;", JS)

    def test_only_one_page_of_rows_is_drawn(self):
        body = _fn("_renderPaperLedger")
        self.assertIn("const shown = rows.slice(from, from + _PAPER_LEDGER_PAGE_SIZE);", body)
        self.assertIn("body.innerHTML = shown.map(row => {", body)

    def test_the_count_still_describes_the_WHOLE_archive(self):
        """A total that changed as you paged would be worse than none."""
        body = _fn("_renderPaperLedger")
        self.assertIn("count.textContent = `· ${rows.length}`", body)

    def test_the_page_is_kept_per_strategy(self):
        self.assertIn("const _paperLedgerPage = {};", JS)
        self.assertIn("_paperLedgerPage[strategy]", _fn("_renderPaperLedger"))

    def test_a_refresh_does_not_throw_you_back_to_page_one(self):
        """The fetch fills a cache; only an out-of-range page is corrected."""
        refresh = _fn("_refreshPaperLedger")
        self.assertNotIn("_paperLedgerPage[strategy] = 1", refresh)
        body = _fn("_renderPaperLedger")
        self.assertIn("> pages) _paperLedgerPage[strategy] = pages;", body)

    def test_an_empty_archive_clears_a_pager_left_from_a_fuller_one(self):
        body = _fn("_renderPaperLedger")
        self.assertIn("const stale = document.getElementById(`${ids.body}-pager`);", body)

    def test_the_pager_is_hidden_when_everything_fits(self):
        self.assertIn(
            "if (total <= _PAPER_LEDGER_PAGE_SIZE) { host.innerHTML = ''; return; }", _fn("_paperLedgerPager")
        )

    def test_the_pager_needs_no_new_markup_on_any_page(self):
        """Five strategy pages would otherwise each need a hand-placed div."""
        body = _fn("_paperLedgerPager")
        self.assertIn("document.createElement('div')", body)
        self.assertIn("table.parentNode.insertBefore(host, table.nextSibling)", body)


class TheButtonsActuallyFire(unittest.TestCase):
    """A data-pf-action missing from the registry is a click that does nothing."""

    def test_the_action_is_registered_and_exported(self):
        self.assertIn("'setPaperLedgerPage',", JS)
        self.assertIn("window.setPaperLedgerPage = setPaperLedgerPage;", JS)
        self.assertIn('data-pf-action="setPaperLedgerPage"', JS)

    def test_the_buttons_name_their_strategy(self):
        """One handler serves five tables; without it, it cannot tell which."""
        body = _fn("_paperLedgerPager")
        self.assertIn('data-strategy="${escapeHtml(strategy)}"', body)

    def test_prev_and_next_both_exist_and_disable_at_the_ends(self):
        body = _fn("_paperLedgerPager")
        self.assertIn("data-ledger-page=\"prev\" ${page <= 1 ? 'disabled' : ''}", body)
        self.assertIn("data-ledger-page=\"next\" ${page >= pages ? 'disabled' : ''}", body)


if __name__ == "__main__":
    unittest.main()
