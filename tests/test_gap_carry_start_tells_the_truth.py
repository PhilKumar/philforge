"""A start must say which mode it started in.

Phil, 2026-09-10: "After completing the paper yesterday I flipped it to Live
but ... now it shows 'Paper carry started.'"

Both ends lied. The route returned a hardcoded "mode": "paper" whatever was
asked for, and the page printed a hardcoded 'Paper carry started.' whatever
came back. The campaign itself was genuinely live -- it bought a real
NIFTY 23650 PE at 15:10 and sold it at 09:20 for Rs 610.29 -- so the only
thing wrong was every message about it.

A reply that cannot tell real money from a simulation is the one thing a start
must never get wrong.
"""

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
JS = open(os.path.join(ROOT, "static", "philforge-app.js"), encoding="utf-8").read()


def start_route() -> str:
    i = APP.index("_start_gap_carry_campaign(user_id, payload, broker_client=broker_client)")
    return APP[i : i + 700]


class TheServerAnswersWithTheRealMode(unittest.TestCase):
    def test_the_mode_is_derived_not_asserted(self):
        block = start_route()
        self.assertIn('"mode": "live" if getattr(runtime.engine, "executor"', block)

    def test_it_is_read_from_the_thing_that_places_orders(self):
        """The executor is what makes a start real; nothing else can be
        trusted to have survived the journey from the form."""
        block = start_route()
        self.assertIn("runtime.engine", block)
        self.assertNotIn('"mode": "paper",', block, "the hardcoded answer is gone")

    def test_a_live_request_builds_a_live_executor(self):
        """The branch the mode is inferred from must still exist."""
        self.assertIn('if trade_mode == "live"', APP)
        self.assertIn('trade_mode = "live" if trade_mode == "live" else "paper"', APP)


class ThePageRepeatsWhatItWasTold(unittest.TestCase):
    def test_the_message_follows_the_response(self):
        block = JS.split("async function startGapCarryPaper()")[1][:2600]
        self.assertIn("String(data.mode || '').toLowerCase() === 'live'", block)
        self.assertIn("LIVE carry started.", block)

    def test_it_does_not_read_the_form_for_this(self):
        """The form is what was ASKED for; the response is what happened."""
        block = JS.split("_renderGapCarryStatus(data.campaign || null, true);")[1][:900]
        self.assertNotIn("oc-gap-mode", block)

    def test_a_start_before_the_entry_time_says_it_is_waiting(self):
        """Gap Carry buys at 15:10. Started at 09:20 it is correctly idle, and
        the old message described pricing a contract it was not pricing."""
        block = JS.split("async function startGapCarryPaper()")[1][:2600]
        self.assertIn("Waiting for", block)
        self.assertIn("oc-gap-entry-time", block)


if __name__ == "__main__":
    unittest.main()
