"""A Telegram alert is stamped in IST, whatever clock the machine keeps.

Phil, 2026-09-10: "My philforge telegram alerts are not in IST".

datetime.now() with no timezone reads the machine's clock, and production runs
on UTC. So every alert was stamped five and a half hours behind, and one sent
after 18:30 IST also carried the wrong DATE -- an order failure at 19:00 on
Tuesday arrived labelled Tuesday 13:30, which is during the session it was not
sent in.

The person reading it is in India, beside a market that runs on IST.
"""

import datetime
import os
import unittest
from zoneinfo import ZoneInfo

import alerter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "alerter.py"), encoding="utf-8").read()


class TheStampIsIST(unittest.TestCase):
    def test_the_module_knows_ist(self):
        self.assertEqual(alerter.IST, ZoneInfo("Asia/Kolkata"))

    def test_the_stamp_is_not_taken_from_the_machine_clock(self):
        """datetime.now() with no argument is the bug, in any form."""
        body = SRC.split("def send_alert")[1] if "def send_alert" in SRC else SRC
        code = "\n".join(line for line in body.split("\n") if not line.strip().startswith("#"))
        self.assertNotIn("datetime.now()", code)
        self.assertIn("datetime.now(IST)", code)

    def test_the_reader_is_told_which_zone(self):
        """A bare time invites the reader to assume their own."""
        self.assertIn("%H:%M:%S IST", SRC)

    def test_a_utc_machine_would_stamp_ist(self):
        utc = datetime.datetime(2026, 9, 9, 20, 51, tzinfo=datetime.timezone.utc)
        stamped = utc.astimezone(alerter.IST).strftime("%Y-%m-%d %H:%M:%S IST")
        # 20:51 UTC is 02:21 on the FOLLOWING day in India
        self.assertEqual(stamped, "2026-09-10 02:21:00 IST")

    def test_both_channels_carry_the_same_stamp(self):
        """Telegram and Discord are built from one `ts`; two would drift."""
        block = SRC.split("ts = datetime.now(IST)")[1][:400]
        self.assertIn("{ts}", block)
        self.assertEqual(block.count("{ts}"), 2, "one for Telegram, one for Discord")


if __name__ == "__main__":
    unittest.main()
