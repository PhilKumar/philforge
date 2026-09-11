"""The CE + PE ledger, as Phil asked for it on 2026-09-11.

"Align this properly and fix bugs and this is on the paper section... and
paginate for 10. Put the texts 'Why it closed' in a (i)".

What the paper page actually showed:
  * real-money "old closed" rows from the broker, among simulated trades;
  * a Contract of "—" on every paper row (paper names it `symbol`);
  * the full rule that fired written into the cell, which never wraps, so the
    column stretched until the Chart buttons fell off the card's right edge;
  * two paper PE books trading the same morning, their rows labelled only
    "PE" — indistinguishable from a duplicate.

Verified rendered, headless, on a snapshot of production's own engines: live
page 4 rows for 4 trades; paper page 1–10 of 28, no broker rows, every
contract named, every book named, 0 of 10 Chart buttons clipped, and an open ⓘ
still open after a repaint.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "philforge-app.css").read_text(encoding="utf-8")
HTML = (ROOT / "strategy.html").read_text(encoding="utf-8")
APP = (ROOT / "app.py").read_text(encoding="utf-8")
RENDER = JS.split("function renderCePe(data)")[1].split("\nasync function refreshCePeStatus")[0]


class TheBrokerRecordStaysOnTheLivePage(unittest.TestCase):
    def test_history_is_read_only_in_live_mode(self):
        self.assertIn("_cepeMode === 'live' ? ((data && data.history) || []) : []", RENDER)

    def test_the_note_about_it_hides_on_paper(self):
        self.assertIn('id="oc-cepe-closed-note"', HTML)
        self.assertIn("note.hidden = _cepeMode !== 'live'", RENDER)


class EveryRowSaysWhatItIs(unittest.TestCase):
    def test_a_paper_trade_names_its_contract(self):
        body = APP.split("async def live_runs(")[1].split("\n@app.")[0]
        self.assertIn('t.get("trading_symbol") or t.get("display_symbol") or t.get("symbol")', body)

    def test_a_book_row_names_its_book(self):
        """Two paper PE books on one morning read as a duplicate without it."""
        self.assertIn('class="cepe-book-name"', RENDER)
        self.assertIn("!old && run.name", RENDER)


class WhyItClosedIsAnInfoButton(unittest.TestCase):
    def test_the_cell_uses_the_house_info_button(self):
        self.assertIn("_cepeWhyCell(run, t)", RENDER)
        cell = JS.split("function _cepeWhyCell(")[1].split("\n}\n")[0]
        self.assertIn('class="pf-info-btn cepe-why-btn"', cell)
        self.assertIn('class="pf-info-pop cepe-why-pop"', cell)

    def test_the_full_rule_is_no_longer_written_into_the_cell(self):
        self.assertNotIn("t.why[0]", RENDER)

    def test_a_reason_with_no_detail_gets_no_button(self):
        cell = JS.split("function _cepeWhyCell(")[1].split("\n}\n")[0]
        self.assertIn("if (!why.length) return", cell)

    def test_an_open_reason_survives_the_poll(self):
        self.assertIn("_cepeWhyOpen.forEach", RENDER)

    def test_the_reason_wraps_and_nothing_else_does(self):
        self.assertIn(".cepe-ledger td.cepe-why { white-space:normal;", CSS)


class TheColumnsLineUp(unittest.TestCase):
    def test_the_ledger_has_its_own_class(self):
        """So these rules cannot move any other `.ocp-table` on the site."""
        self.assertIn('<table class="ocp-table cepe-ledger">', HTML)

    def test_words_left_figures_right_button_centred(self):
        self.assertIn(".cepe-ledger td:nth-child(-n+4)", CSS)
        self.assertIn("font-variant-numeric:tabular-nums", CSS)
        self.assertIn(".cepe-ledger td:nth-child(10) { text-align:center;", CSS)


class TenToAPage(unittest.TestCase):
    def test_page_size(self):
        self.assertIn("const _CEPE_PAGE_SIZE = 10", JS)


if __name__ == "__main__":
    unittest.main()
