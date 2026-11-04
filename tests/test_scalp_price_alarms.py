"""Price alarms set on the Scalp chart ring on the server, page open or not.

Phil, 2026-11-04: "I need alarm to set on the chart when it crosses or touches
a price line I set.. like right clicking on the chart", then "yes do server
side with telegram": a hidden tab stops pricing, so the page alone could not
ring while he was on another screen.
"""

import asyncio
import base64
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("PHILFORGE_PIN", "123456")
os.environ.setdefault("PHILFORGE_DB", "/tmp/philforge-scalp-alarms.db")
os.environ.setdefault("PHILFORGE_USER_DATA_ROOT", "/tmp/philforge-scalp-alarms-data")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")
os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())

import app  # noqa: E402


class _Request:
    def __init__(self, user_id=1):
        self.state = SimpleNamespace(user_id=user_id, current_user={"id": user_id, "role": "admin"})


class _Feed:
    def __init__(self, price):
        self.price = price

    def live_option_ltp(self, *a, **k):
        return self.price


def _req(price, now):
    return app.ScalpAlarmReq(
        underlying="NIFTY", strike=23750, expiry="2099-11-09", option_type="CE", price=price, now=now
    )


class PriceAlarms(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = {}
        self.sent = []
        self.patches = [
            patch.object(app, "_scalp_alarms", None),
            patch.object(app._db_mod, "get_app_state", AsyncMock(return_value=None)),
            patch.object(app._db_mod, "set_app_state", AsyncMock(side_effect=lambda k, v: self.saved.update({k: v}))),
            patch.object(app.alerter, "alert", lambda title, body, **k: self.sent.append((title, body))),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()

    async def test_an_alarm_above_the_price_waits_for_it_to_rise(self):
        added = (await app.scalp_alarm_add(_req(270.0, 265.85), _Request()))["alarm"]
        self.assertEqual((added["dir"], added["price"]), ("up", 270.0))
        self.assertFalse(app._scalp_alarm_hit(added, 269.95))
        self.assertTrue(app._scalp_alarm_hit(added, 270.0))  # a touch is enough
        below = (await app.scalp_alarm_add(_req(260.03, 265.85), _Request()))["alarm"]
        self.assertEqual((below["dir"], below["price"]), ("down", 260.05))  # on the 0.05 tick
        self.assertIn(app._SCALP_ALARMS_KEY, self.saved)

    async def test_the_watch_rings_it_once_on_telegram(self):
        await app.scalp_alarm_add(_req(270.0, 265.85), _Request())
        with (
            patch.object(app, "_market_feed", _Feed(270.4)),
            patch.object(app.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await app._run_scalp_alarm_loop()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("NIFTY 23750CE", self.sent[0][0])
        self.assertIn("rose to ₹270.00", self.sent[0][1])
        listed = await app.scalp_alarms_list(_Request())
        self.assertEqual(listed["alarms"], [])
        self.assertEqual([f["hit"] for f in listed["fired"]], [270.4])
        # The page reporting the same alarm later sends nothing more.
        again = await app.scalp_alarm_fire(listed["fired"][0]["id"], app.ScalpAlarmFireReq(price=270.5), _Request())
        self.assertIsNone(again["fired"])
        self.assertEqual(len(self.sent), 1)

    async def test_no_price_no_ring(self):
        await app.scalp_alarm_add(_req(270.0, 265.85), _Request())
        with (
            patch.object(app, "_market_feed", _Feed(0)),
            patch.object(app, "_scalp_alarm_quoted", {}),
            patch.object(app, "_scalp_engines", {}),
            patch.object(app, "datetime", wraps=app.datetime) as dt,
            patch.object(app.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            dt.now.return_value = app.datetime(2026, 11, 7, 11, 0, tzinfo=app.IST)  # a Saturday
            with self.assertRaises(asyncio.CancelledError):
                await app._run_scalp_alarm_loop()
        self.assertEqual(self.sent, [])
        self.assertEqual(len((await app.scalp_alarms_list(_Request()))["alarms"]), 1)

    async def test_alarms_are_each_users_own(self):
        await app.scalp_alarm_add(_req(270.0, 265.85), _Request(1))
        self.assertEqual((await app.scalp_alarms_list(_Request(2)))["alarms"], [])
        alarm_id = (await app.scalp_alarms_list(_Request(1)))["alarms"][0]["id"]
        await app.scalp_alarm_remove(alarm_id, _Request(2))
        self.assertEqual(len((await app.scalp_alarms_list(_Request(1)))["alarms"]), 1)
        await app.scalp_alarm_remove(alarm_id, _Request(1))
        self.assertEqual((await app.scalp_alarms_list(_Request(1)))["alarms"], [])

    async def test_removing_a_rung_alarm_takes_it_off_the_rung_list_too(self):
        # Phil, 2026-11-04: "Every now and then I am hearing this alarm sound
        # even after I remove them". Every open page rings a rung alarm when it
        # first sees it; removed (or dismissed), no page may see it again.
        await app.scalp_alarm_add(_req(270.0, 265.85), _Request())
        alarm_id = (await app.scalp_alarms_list(_Request()))["alarms"][0]["id"]
        await app._scalp_fire_alarm("1", alarm_id, 270.4)
        self.assertEqual([f["id"] for f in (await app.scalp_alarms_list(_Request()))["fired"]], [alarm_id])
        self.assertEqual((await app.scalp_alarm_remove(alarm_id, _Request()))["removed"], 1)
        self.assertEqual((await app.scalp_alarms_list(_Request()))["fired"], [])

    async def test_an_expired_contracts_alarm_is_dropped(self):
        old = app.ScalpAlarmReq(
            underlying="NIFTY", strike=23750, expiry="2020-01-02", option_type="CE", price=270, now=265
        )
        await app.scalp_alarm_add(old, _Request())
        await app._scalp_alarms_drop_expired()
        self.assertEqual((await app.scalp_alarms_list(_Request()))["alarms"], [])


if __name__ == "__main__":
    unittest.main()
