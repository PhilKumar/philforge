"""A finished High Entry campaign can be drawn (Phil, 2026-09-23).

"there are no completed charts on the closed campaigns" -- and there never had
been one.  `/api/paper-campaigns/{strategy}/{id}/chart` refuses anything whose
archived payload carries no `engine`, and High Entry archives its mother and
its trades instead of an engine.  So every closed campaign answered 409 with a
message about recorded prices, which was not what had happened.

Nothing needs reviving: the stored mother and trades are exactly what
`_recovery_chart` reads, and it is the renderer the LIVE chart already uses.
This test CALLS the route rather than reading it, because today two defects
shipped past tests that only asserted on source text.
"""

import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module
from engine.cascade_options import IST

MOTHER = dt.datetime(2026, 9, 22, 9, 15)


def _candle(minute_offset, o, h, low, c):
    stamp = (MOTHER + dt.timedelta(minutes=minute_offset)).replace(tzinfo=IST)
    return SimpleNamespace(timestamp=stamp, open=o, high=h, low=low, close=c)


ARCHIVED = {
    "campaign_key": MOTHER.isoformat(),
    "strategy": "candle_recovery",
    "symbol": "NIFTY",
    "status": "ABANDONED",
    "closed_at": "2026-09-22T15:10:00",
    "net_pnl": -2882.0,
    "payload": {
        "mother": {"timestamp": MOTHER.isoformat(), "high": 23489.0, "low": 23400.0},
        "side": "CE",
        "timeframe": "5m",
        "trades": [
            {
                "trade_no": 1,
                "trigger": 23365.05,
                "entry_time": "2026-09-22T12:05:00",
                "entry_index": 23365.05,
                "sl_level": 23355.25,
                "exit_time": "2026-09-22T12:25:00",
                "exit_index": 23350.0,
                "entry_premium": 434.0,
                "exit_premium": 430.0,
                "quantity": 65,
                "net_pnl": -260.0,
            }
        ],
    },
}


class AClosedHighEntryCampaignDraws(unittest.IsolatedAsyncioTestCase):
    async def _call(self, row, timeframe=""):
        candles = [_candle(i * 5, 23400 + i, 23410 + i, 23390 + i, 23405 + i) for i in range(80)]

        class _Adapter:
            def __init__(self, *a, **kw):
                pass

            async def async_get_candles(self, symbol, tf, *, from_date=None, to_date=None, now=None):
                return candles

        async def _broker(_request):
            return (None, object(), "test")

        with (
            patch.object(app_module, "_request_user_id", lambda request: 1),
            patch.object(app_module, "_request_broker_context", _broker),
            patch.object(app_module, "CascadeOptionsAdapter", _Adapter),
            patch.object(app_module._db_mod, "get_paper_campaign", self._row_returning(row)),
        ):
            return await app_module.paper_campaign_chart("candle_recovery", 7, SimpleNamespace(), timeframe=timeframe)

    @staticmethod
    def _row_returning(row):
        async def _get(user_id, campaign_id):
            return row

        return _get

    async def test_the_route_returns_a_drawable_chart(self):
        out = await self._call(ARCHIVED)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["frozen"])
        self.assertEqual(out["timeframe"], "5m")
        self.assertEqual(out["side"], "CE")
        self.assertEqual(out["net_pnl"], -2882.0)
        # the thing the page actually draws
        self.assertTrue(out["chart"].get("candles"))

    async def test_it_no_longer_refuses_for_a_missing_engine(self):
        """The 409 that made every closed campaign chartless."""
        self.assertNotIn("engine", ARCHIVED["payload"])
        out = await self._call(ARCHIVED)
        self.assertEqual(out["status"], "ok")

    async def test_a_put_campaign_keeps_its_side(self):
        row = {**ARCHIVED, "payload": {**ARCHIVED["payload"], "side": "PE"}}
        out = await self._call(row)
        self.assertEqual(out["side"], "PE")

    async def test_an_archive_with_no_mother_says_so_plainly(self):
        row = {**ARCHIVED, "payload": {**ARCHIVED["payload"], "mother": {}}}
        with self.assertRaises(app_module.HTTPException) as caught:
            await self._call(row)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("mother candle", caught.exception.detail)

    async def test_an_unknown_timeframe_is_refused(self):
        with self.assertRaises(app_module.HTTPException) as caught:
            await self._call(ARCHIVED, timeframe="3m")
        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
