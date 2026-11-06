"""The CE/PE books' open legs are priced from the feed with every 250 ms push.

Phil, 2026-11-06: "during live CE PE trades, the live open positions is not
showing the live prices and it is delayed.. make it to show like the scalp open
positions". The legs reached the page only in the 5 s engine status.
"""

import base64
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("PHILFORGE_PIN", "123456")
os.environ.setdefault("PHILFORGE_DB", "/tmp/philforge-engine-legs.db")
os.environ.setdefault("PHILFORGE_USER_DATA_ROOT", "/tmp/philforge-engine-legs-data")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")
os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())

import app  # noqa: E402


class _Book:
    def __init__(self, positions, running=True, in_trade=True, feed=None):
        self.positions, self.running, self.in_trade, self.feed = positions, running, in_trade, feed or {}

    def _get_premium_from_feed(self, pos):
        return self.feed.get(pos.get("leg_num"), 0.0)

    @staticmethod
    def _position_quantity(pos):
        return int(pos.get("quantity") or 0)


def _leg(n, **over):
    leg = {
        "leg_num": n,
        "trading_symbol": f"NIFTY 24150CE #{n}",
        "transaction_type": "BUY",
        "entry_premium": 258.55,
        "current_premium": 260.0,
        "quantity": 130,
        "status": "open",
    }
    leg.update(over)
    return leg


class EngineLegsPush(unittest.TestCase):
    def test_open_legs_are_priced_from_the_feed(self):
        live = {"CE_SL15": _Book([_leg(1), _leg(2, status="closed")], feed={1: 306.25})}
        paper = {"PE_NoTarget": _Book([_leg(1, transaction_type="SELL", entry_premium=100.0)], feed={1: 90.0})}
        idle = {"Other": _Book([_leg(1)], in_trade=False)}
        with (
            patch.object(app, "live_engines", {1: {**live, **idle}}),
            patch.object(app, "paper_engines", {1: paper}),
        ):
            got = app._engine_legs_snapshot(1)
        self.assertEqual(
            got,
            [
                {
                    "run_id": "CE_SL15",
                    "mode": "live",
                    "legs": [
                        {
                            "leg": 1,
                            "symbol": "NIFTY 24150CE #1",
                            "current_premium": 306.25,
                            "unrealized_pnl": round((306.25 - 258.55) * 130, 2),
                        }
                    ],
                },
                {
                    "run_id": "PE_NoTarget",
                    "mode": "paper",
                    "legs": [
                        {"leg": 1, "symbol": "NIFTY 24150CE #1", "current_premium": 90.0, "unrealized_pnl": 1300.0}
                    ],
                },
            ],
        )

    def test_no_feed_tick_falls_back_to_the_engines_last_price(self):
        with (
            patch.object(app, "live_engines", {1: {"CE": _Book([_leg(1)])}}),
            patch.object(app, "paper_engines", {}),
        ):
            got = app._engine_legs_snapshot(1)
        self.assertEqual(got[0]["legs"][0]["current_premium"], 260.0)

    def test_a_leg_with_no_price_yet_is_still_listed(self):
        # The page reads a leg's absence as closed.
        with (
            patch.object(app, "live_engines", {1: {"CE": _Book([_leg(1, current_premium=0)])}}),
            patch.object(app, "paper_engines", {}),
        ):
            got = app._engine_legs_snapshot(1)
        self.assertEqual(got[0]["legs"][0]["current_premium"], 0.0)
        self.assertIsNone(got[0]["legs"][0]["unrealized_pnl"])

    def test_nothing_open_sends_nothing(self):
        with patch.object(app, "live_engines", {}), patch.object(app, "paper_engines", {}):
            self.assertEqual(app._engine_legs_snapshot(1), [])


if __name__ == "__main__":
    unittest.main()
