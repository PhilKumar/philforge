"""The cutover must never leave traffic pointed at a stopped worker.

2026-09-08, mid-session. The deploy started the standby on 8001, stopped 8000,
then called POST /api/restore-engines with a 30-second budget. The restore
finished at 05:13:35; the call gave up at 05:13:28. So the script died with:

    ERROR: Old worker stopped, but the new worker could not restore engines.

Both halves of that sentence were wrong. The engines HAD restored -- the live
engine, three paper engines and High Entry were all running on 8001, taking
candles at 0.1s latency. And dying there skipped the step that repoints nginx,
so philforge.in served 502 to every request while a perfectly healthy worker
sat behind the port nobody was pointing at.

By the time this step runs the old worker is already stopped: the cutover has
no way back. The traffic must follow the process that exists, and a failure is
reported over a site that is up.
"""

import re
import unittest
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent / "deploy" / "cd-deploy.sh").read_text(encoding="utf-8")


def _at(needle: str) -> int:
    where = SCRIPT.find(needle)
    assert where >= 0, f"{needle!r} is no longer in cd-deploy.sh"
    return where


class TheCutoverFinishesWhatItStarts(unittest.TestCase):
    def test_nginx_is_switched_before_any_restore_failure_can_abort(self):
        stop_old = _at("Stopping old instance on port")
        switch = _at("Switching nginx to port")
        give_up = _at("Engines could not be confirmed on port")
        self.assertLess(stop_old, switch, "the old worker is stopped before the switch — that is the danger window")
        self.assertLess(switch, give_up, "traffic must be moved BEFORE the deploy is allowed to fail")

    def test_the_old_die_that_stranded_traffic_is_gone(self):
        self.assertNotIn("could not restore engines. Check broker positions", SCRIPT)

    def test_the_restore_is_given_a_realistic_budget_and_a_second_ask(self):
        budgets = [int(v) for v in re.findall(r"--max-time (\d+) -X POST[^\n]*restore-engines", SCRIPT)]
        self.assertTrue(budgets, "the restore call is no longer recognisable")
        self.assertGreaterEqual(min(budgets), 90, "30s was shorter than the restore itself")
        self.assertIn("for attempt in 1 2", SCRIPT, "one timed-out call must not be the whole verdict")

    def test_a_failed_restore_is_still_reported(self):
        # Up is not the same as correct: the deploy must still go red.
        tail = SCRIPT[_at("Engines could not be confirmed on port") :]
        self.assertIn("check the broker positions", tail)
        self.assertIn('die "Engines could not be confirmed', SCRIPT)


if __name__ == "__main__":
    unittest.main()
