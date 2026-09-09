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

    def test_only_terminal_campaigns_reach_the_closed_table(self):
        """The table moved to the archive on 2026-09-09, so this rule moved with
        it: the SERVER now decides what is finished, when it writes the row.
        The browser-side set survives because the campaign cards still use it."""
        self.assertIn("_RECOVERY_TERMINAL", SCRIPT)
        for status in ("RECOVERED", "ABANDONED", "ENDED"):
            self.assertIn(f"'{status}'", _block("const _RECOVERY_TERMINAL", 400))
        self.assertNotIn(
            "String(c.status || '').toUpperCase() !== 'RUNNING'",
            SCRIPT,
            "the filter on a status this engine never produces is back",
        )
        app_py = Path(__file__).resolve().parent.parent.joinpath("app.py").read_text(encoding="utf-8")
        guard = app_py.split("def _recovery_campaign_row(")[1][:900]
        self.assertIn("_RECOVERY_TERMINAL_STATES", guard)
        for status in ("RECOVERED", "ABANDONED", "ENDED"):
            self.assertIn(f'"{status}"', app_py.split("_RECOVERY_TERMINAL_STATES = ")[1][:120])

    def test_a_finished_campaign_does_not_claim_an_open_trade(self):
        card = _block("function _recoveryCampaign(c) {")
        line = next(row for row in card.splitlines() if "to finish green" in row)
        self.assertIn("c.open_trades", line)

    def test_the_campaign_tile_counts_campaigns_not_trades(self):
        body = _block("function renderRecovery(data) {", 4000)
        self.assertIn("const finished = campaigns.filter(_recoveryIsOver).length;", body)
        self.assertIn("${finished} finished", body)

    def test_an_unpriced_leg_still_refuses_to_become_a_total(self):
        """Also moved to the server: a campaign with a leg that never priced is
        archived with net_pnl NULL, and the shared ledger renderer prints
        "unpriced" for a null net. Summing the legs that DID price is how five
        stops once read as +Rs 0."""
        app_py = Path(__file__).resolve().parent.parent.joinpath("app.py").read_text(encoding="utf-8")
        builder = app_py.split("def _recovery_campaign_row(")[1].split("\nasync def")[0]
        self.assertIn("unpriced", builder)
        self.assertIn("net = None if unpriced else", builder)
        self.assertIn("_paperLedgerMoney", SCRIPT)
