"""The book cards and the desk tile must report the same money.

Phil, 2026-09-23: "is booked all time correct?"  It was not.  His desk showed
CE -Rs 1,020.12 and PE +Rs 8,456.08 on the two book cards -- Rs 7,435.96 -- and
Rs 3,816 in the "Booked - all time" tile above them.

The tile was right.  `booked_pnl` is summed from the engine's own in-memory
closed list, which EVERY DEPLOY EMPTIES, and four deploys that day left the
cards three real trades short: a PE 23950 on 08-Sep, a PE 24100 on 07-Sep and a
CE 23750 on 03-Sep, together -Rs 3,620.31.  The tile had already been moved
onto the ledger on 22-Sep for this exact reason; the cards were left behind.

These are his real figures, so the arithmetic is pinned to something that
actually happened rather than to numbers chosen to pass.
"""

import unittest
from pathlib import Path

JS = (Path(__file__).resolve().parent.parent / "static" / "philforge-app.js").read_text(encoding="utf-8")

CARDS = {"CE_SL15_NoMonTue": -1020.12, "PE_NoTarget": 8456.08}
HISTORY = [
    {"book": "PE_NoTarget", "side": "PE", "pnl": -2859.18},
    {"book": "PE_NoTarget", "side": "PE", "pnl": 979.12},
    {"book": "CE_SL15_NoMonTue", "side": "CE", "pnl": -1740.25},
]
TILE = 3815.65


class HisNumbersReconcile(unittest.TestCase):
    def test_the_cards_alone_do_not_match_the_tile(self):
        """The bug, stated as arithmetic."""
        self.assertAlmostEqual(sum(CARDS.values()), 7435.96, places=2)
        self.assertNotAlmostEqual(sum(CARDS.values()), TILE, places=2)

    def test_the_gap_is_exactly_the_saved_run_history(self):
        self.assertAlmostEqual(sum(h["pnl"] for h in HISTORY), -3620.31, places=2)
        self.assertAlmostEqual(sum(CARDS.values()) + sum(h["pnl"] for h in HISTORY), TILE, places=2)

    def test_adding_each_books_own_history_makes_the_cards_agree(self):
        """What _cepeBookAllTime does, done here in Python."""
        fixed = {
            name: round(net + sum(h["pnl"] for h in HISTORY if h["book"] == name), 2) for name, net in CARDS.items()
        }
        self.assertAlmostEqual(fixed["CE_SL15_NoMonTue"], -2760.37, places=2)
        self.assertAlmostEqual(fixed["PE_NoTarget"], 6576.02, places=2)
        self.assertAlmostEqual(sum(fixed.values()), TILE, places=2)


class TheCardReadsItsOwnHistory(unittest.TestCase):
    def _fn(self) -> str:
        start = JS.index("function _cepeBookAllTime(")
        return JS[start : JS.index("\nfunction ", start + 1)]

    def test_the_helper_exists_and_the_card_uses_it(self):
        self.assertIn("function _cepeBookAllTime(run, earlier)", JS)
        self.assertIn("const allTime = _cepeBookAllTime(run, earlier);", JS)
        self.assertIn("_cepeMoney(allTime.booked)", JS)

    def test_it_matches_on_BOTH_the_book_name_and_the_side(self):
        """Two books can share a name across sides; one must not eat the other's."""
        body = self._fn()
        self.assertRegex(body, r"String\(h\.book \|\| ''\) === String\(run\.name")
        self.assertRegex(body, r"String\(h\.side \|\| ''\)\.toUpperCase\(\)")

    def test_a_book_with_no_history_is_left_exactly_as_it_was(self):
        """Including the null that means 'nothing closed yet', not zero."""
        self.assertIn("if (!mine.length) return { booked: run.booked_pnl, closed: run.closed_count ?? 0 };", self._fn())

    def test_the_closed_count_grows_with_the_money(self):
        """A net that counts 4 trades must not sit beside '1 closed'."""
        self.assertIn("closed: Number(run.closed_count || 0) + mine.length", self._fn())

    def test_history_is_handed_in_on_the_live_page_only(self):
        """A paper book never reached the broker -- same rule the ledger uses."""
        self.assertIn("const earlier = _cepeMode === 'live' ? ((data && data.history) || []) : [];", JS)
        self.assertIn("runs.map(r => _cepeBookCard(r, earlier))", JS)

    def test_the_old_stale_read_is_gone_from_the_card(self):
        card = JS[JS.index("function _cepeBookCard(") :]
        card = card[: card.index("\nfunction ")]
        self.assertNotIn("_cepeMoney(run.booked_pnl)", card)
        self.assertNotIn("${run.closed_count ?? 0} closed", card)


if __name__ == "__main__":
    unittest.main()
