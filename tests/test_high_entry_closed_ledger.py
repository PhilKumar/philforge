"""A running campaign is not a closed one.

High Entry's "Closed paper campaigns" table asked for `status !== 'RUNNING'`.
The recovery engine has never produced that word: its statuses are WATCHING,
ARMED, IN_TRADE, AWAIT_LOW, AWAIT_BREAK, ZONES and then RECOVERED, ABANDONED
or ENDED (engine/candle_recovery.py).  So the filter matched EVERY campaign --
a mother still hunting its entry was filed under the closed ledger, and its
running P&L was folded into the table's net.

Phil, 2026-09-08, reading the card and the table side by side: "I am confused
by the 2 screens now".  Two other things in that pair were saying something
untrue as well, and are pinned here too:

  * a RECOVERED campaign still printed "open trade must net Rs 500 to finish
    green" -- `required_recovery` is `max(0, -booked) + min_profit` and never
    goes away, so a finished, green campaign claimed an open trade it did not
    have;
  * the ledger printed the legs that DID price as the net, with no word about
    the ones that did not.
"""

import unittest
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent / "static" / "philforge-app.js").read_text(encoding="utf-8")
ENGINE = (Path(__file__).resolve().parent.parent / "engine" / "candle_recovery.py").read_text(encoding="utf-8")


def _block(needle: str, size: int = 2600) -> str:
    return SCRIPT[SCRIPT.index(needle) :][:size]


class HighEntryClosedLedgerTests(unittest.TestCase):
    def test_the_engine_still_has_no_status_called_running(self):
        # If it ever gains one, the old filter would start being defensible and
        # this test should be revisited rather than deleted.
        self.assertNotIn('"RUNNING"', ENGINE)
        self.assertNotIn("'RUNNING'", ENGINE)

    def test_the_closed_table_filters_on_the_terminal_statuses(self):
        self.assertIn("_RECOVERY_TERMINAL", SCRIPT)
        for status in ("RECOVERED", "ABANDONED", "ENDED"):
            self.assertIn(f"'{status}'", _block("const _RECOVERY_TERMINAL", 400))
        body = _block("const closedRows = document.getElementById('oc-high-closed-rows')")
        self.assertIn(".filter(_recoveryIsOver)", body)
        self.assertNotIn("!== 'RUNNING'", body)

    def test_a_finished_campaign_does_not_claim_an_open_trade(self):
        card = _block("function _recoveryCampaign(c) {")
        line = next(row for row in card.splitlines() if "to finish green" in row)
        self.assertIn("c.open_trades", line)

    def test_the_campaign_tile_counts_campaigns_not_trades(self):
        body = _block("function renderRecovery(data) {", 4000)
        self.assertIn("const finished = campaigns.filter(_recoveryIsOver).length;", body)
        self.assertIn("${finished} finished", body)

    def test_unpriced_legs_are_said_out_loud_in_the_ledger(self):
        body = _block("const closedRows = document.getElementById('oc-high-closed-rows')", 3600)
        self.assertIn("unpricedEnded", body)
        self.assertIn("unpriced", body)
