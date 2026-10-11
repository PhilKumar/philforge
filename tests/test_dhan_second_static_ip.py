"""Dhan keeps two static IPs and takes orders from either.

Phil's account, 2026-10-11: the server's address sat in the second slot (the
first held an IPv6 saved by mistake, and Dhan refuses the same address in
both). Dhan answered SECONDARY_MATCH with orders allowed -- and Account
Settings showed a red "IP MISMATCH" beside "Order status: Allowed".
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402

SERVER = "13.205.6.8"
WRONG_V6 = "2409:40f4:1121:bede:85c4:258c:bd3f:5d1e"


class _Client:
    client_id = "1100000001"
    access_token = "token"  # nosec B105 - a placeholder, never sent


def _status(payload):
    with (
        mock.patch.object(app, "_get_server_public_ip", return_value=(SERVER, "")),
        mock.patch.object(app, "_get_cached_dhan_saved_ip_payload", return_value=(payload, "")),
    ):
        return app._build_execution_ip_status({"id": 1}, _Client(), "user")


class TheSecondSlotCounts(unittest.TestCase):
    def test_a_secondary_match_is_a_match(self):
        status = _status(
            {
                "primaryIP": WRONG_V6,
                "secondaryIP": SERVER,
                "detectedIP": SERVER,
                "ipMatchStatus": "SECONDARY_MATCH",
                "ordersAllowed": True,
            }
        )
        self.assertTrue(status["match"])
        self.assertEqual(status["dhan_saved_ip"], SERVER)
        self.assertEqual(status["dhan_other_ip"], WRONG_V6)
        self.assertEqual(status["dhan_ip_slot"], "secondary")
        self.assertEqual(status["warning"], "")

    def test_a_secondary_match_without_the_address_still_shows_the_matched_one(self):
        status = _status(
            {"primaryIP": WRONG_V6, "detectedIP": SERVER, "ipMatchStatus": "SECONDARY_MATCH", "ordersAllowed": True}
        )
        self.assertTrue(status["match"])
        self.assertEqual(status["dhan_saved_ip"], SERVER)

    def test_the_first_slot_reads_as_before(self):
        status = _status({"primaryIP": SERVER, "detectedIP": SERVER, "ipMatchStatus": "MATCH", "ordersAllowed": True})
        self.assertTrue(status["match"])
        self.assertEqual((status["dhan_saved_ip"], status["dhan_ip_slot"]), (SERVER, "primary"))

    def test_a_real_mismatch_is_still_one(self):
        status = _status(
            {
                "primaryIP": WRONG_V6,
                "secondaryIP": "203.0.113.9",
                "detectedIP": SERVER,
                "ipMatchStatus": "MISMATCH",
                "ordersAllowed": False,
            }
        )
        self.assertFalse(status["match"])
        self.assertEqual(status["dhan_saved_ip"], WRONG_V6)

    def test_blocked_orders_are_never_a_match(self):
        status = _status(
            {"primaryIP": WRONG_V6, "secondaryIP": SERVER, "ipMatchStatus": "SECONDARY_MATCH", "ordersAllowed": False}
        )
        self.assertFalse(status["match"])


if __name__ == "__main__":
    unittest.main()
