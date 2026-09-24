"""A read-only account can open a closed campaign's chart.

Phil, 24-Sep-2026: "Closed campaign charts are not opening up". The server was
answering 503 on every one:

    HTTP 503 on GET /api/paper-campaigns/candle_recovery/520/chart

The chart needs candles, candles need a broker, and `_resolve_user_broker_client`
had a clause named "admin fallback" that only ever fired when the requesting
user was THEMSELVES an admin. A viewer's role is "viewer", so it fell through to
(None, "missing") and the route refused for lack of a broker.

A viewer now borrows the same client the shared reads already come from. The
thing that keeps that safe is the METHOD gate: no route that places an order is
reachable by a read-only account, and the balance stays refused outright. These
tests pin both halves — the borrow works, and it opens no door.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auth  # noqa: E402


def _resolve(role, configured=True):
    import app

    with patch.object(app.dhan, "_is_configured", return_value=configured):
        return app._resolve_user_broker_client({"id": 7, "role": role})


class AViewerGetsABrokerForReads(unittest.TestCase):
    def test_a_viewer_is_handed_the_owners_client(self):
        client, source = _resolve("viewer")
        self.assertIsNotNone(client, "a viewer with no broker gets none, so charts 503")
        self.assertEqual(source, "global-viewer")

    def test_an_admin_is_unchanged(self):
        client, source = _resolve("admin")
        self.assertIsNotNone(client)
        self.assertEqual(source, "global")

    def test_the_source_says_which_it_was(self):
        """A borrowed client must be visible as borrowed in any log or audit."""
        self.assertNotEqual(_resolve("viewer")[1], _resolve("admin")[1])

    def test_an_ordinary_user_still_gets_nothing(self):
        """Only admin and viewer borrow. A plain user brings their own or none."""
        client, source = _resolve("user")
        self.assertIsNone(client)
        self.assertEqual(source, "missing")

    def test_no_broker_configured_means_no_client_for_anyone(self):
        for role in ("admin", "viewer", "user"):
            with self.subTest(role=role):
                self.assertIsNone(_resolve(role, configured=False)[0])


class BorrowingItOpensNoDoor(unittest.TestCase):
    """The guarantees this change leans on. If any of these break, so does it."""

    def test_a_viewer_cannot_place_or_change_anything(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in ("/api/orders", "/api/live/start", "/api/scalp/entry", "/api/cascade/start"):
                with self.subTest(method=method, path=path):
                    self.assertFalse(auth.viewer_may_call(method, path))

    def test_the_balance_is_still_refused_outright(self):
        self.assertFalse(auth.viewer_may_read("/api/funds"))
        self.assertFalse(auth.viewer_may_read("/api/portfolio/summary"))

    def test_the_chart_route_is_a_read(self):
        path = "/api/paper-campaigns/candle_recovery/520/chart"
        self.assertTrue(auth.viewer_may_call("GET", path))
        self.assertTrue(auth.viewer_reads_owner_data("GET", path))


if __name__ == "__main__":
    unittest.main()
