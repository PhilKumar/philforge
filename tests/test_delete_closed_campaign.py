"""Any closed campaign can be removed from the ledger -- but only his own.

Phil asked first for the unpriced rows (2026-09-23), then widened it: "I need
for all runs on the closed campaigns... Because I use different timings to test
and it gives more results."  Testing 5m against 15m against 1h fills the table
with runs he has no use for.

So the guard that is left is OWNERSHIP, and that is what these test hardest: a
row belonging to another user, or to another strategy, must be untouchable
through this door however the id is aimed at it.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module

UNPRICED = {"id": 7, "strategy": "candle_recovery", "net_pnl": None, "buys": 3}
SETTLED = {"id": 8, "strategy": "candle_recovery", "net_pnl": -2882.0, "buys": 3}
NO_TRADE = {"id": 9, "strategy": "candle_recovery", "net_pnl": None, "buys": 0}


class TheDeleteDoorIsScopedToHim(unittest.IsolatedAsyncioTestCase):
    async def _call(self, row, *, strategy="candle_recovery", deleted=True):
        self.asked = []

        async def _get(user_id, campaign_id):
            return row

        async def _delete(user_id, campaign_id):
            self.asked.append((user_id, campaign_id))
            return deleted

        with (
            patch.object(app_module, "_request_user_id", lambda request: 1),
            patch.object(app_module._db_mod, "get_paper_campaign", _get),
            patch.object(app_module._db_mod, "delete_paper_campaign", _delete),
        ):
            return await app_module.delete_paper_campaign(strategy, int(row["id"]), SimpleNamespace())

    async def test_an_unpriced_row_is_removed(self):
        out = await self._call(UNPRICED)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["deleted"], 7)
        self.assertEqual(self.asked, [(1, 7)])

    async def test_a_settled_campaign_goes_too_and_its_net_is_reported_back(self):
        """So the page can tell him what he just removed."""
        out = await self._call(SETTLED)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["net_pnl"], -2882.0)

    async def test_a_campaign_that_never_bought_goes_as_well(self):
        out = await self._call(NO_TRADE)
        self.assertEqual(out["status"], "ok")

    async def test_an_unknown_strategy_is_a_404(self):
        with self.assertRaises(app_module.HTTPException) as caught:
            await self._call(UNPRICED, strategy="not_a_strategy")
        self.assertEqual(caught.exception.status_code, 404)

    async def test_a_row_belonging_to_another_strategy_is_a_404(self):
        with self.assertRaises(app_module.HTTPException) as caught:
            await self._call(UNPRICED, strategy="gap_carry")
        self.assertEqual(caught.exception.status_code, 404)

    async def test_a_delete_that_removed_nothing_is_not_reported_as_success(self):
        with self.assertRaises(app_module.HTTPException) as caught:
            await self._call(UNPRICED, deleted=False)
        self.assertEqual(caught.exception.status_code, 409)


class TheSqlIsScopedToTheUser(unittest.TestCase):
    """Ownership is the guard that is left, so it must be in the SQL."""

    def test_the_statement_is_scoped_to_the_user(self):
        from pathlib import Path

        db = Path(app_module.__file__).parent.joinpath("db.py").read_text(encoding="utf-8")
        body = db[db.index("async def delete_paper_campaign(") :]
        body = body[: body.index("\nasync def ")]
        self.assertIn("user_id = ?", body)
        self.assertIn("AND id = ?", body)


class TheButtonIsReachable(unittest.TestCase):
    """A data-pf-action missing from the registry is a click that does nothing."""

    def test_the_action_is_registered_and_exported(self):
        from pathlib import Path

        js = Path(app_module.__file__).parent.joinpath("static", "philforge-app.js").read_text(encoding="utf-8")
        self.assertIn("'deleteClosedCampaign',", js)
        self.assertIn("window.deleteClosedCampaign = deleteClosedCampaign;", js)
        self.assertIn('data-pf-action="deleteClosedCampaign"', js)

    def test_a_booked_row_names_its_money_before_it_goes(self):
        from pathlib import Path

        js = Path(app_module.__file__).parent.joinpath("static", "philforge-app.js").read_text(encoding="utf-8")
        self.assertIn("booked net of <b>${escapeHtml(_candleEntrySigned(Number(raw)))}</b>", js)


if __name__ == "__main__":
    unittest.main()


class AgainstARealDatabase(unittest.IsolatedAsyncioTestCase):
    """The guard that matters is the one in the SQL, so exercise the SQL."""

    async def asyncSetUp(self):
        import os
        import tempfile

        import config
        import db as db_mod

        self.db_mod = db_mod
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = config.DB_PATH
        config.DB_PATH = os.path.join(self.temp.name, "ledger-test.db")
        db_mod._initialized = False
        await db_mod.init_db()
        self.user = await db_mod.create_user("ledger-user", "x" * 60)

    async def asyncTearDown(self):
        import config

        config.DB_PATH = self.old_path
        self.db_mod._initialized = False
        self.temp.cleanup()

    async def _save(self, key, net, buys):
        await self.db_mod.save_paper_campaign(
            self.user,
            "candle_recovery",
            {
                "campaign_key": key,
                "symbol": "NIFTY",
                "contract": "23250 CE",
                "opened_at": "2026-09-22T09:15:00",
                "closed_at": "2026-09-22T15:10:00",
                "status": "ENDED",
                "exit_reason": "end_of_data",
                "buys": buys,
                "deployed_inr": 1000.0,
                "gross_pnl": None,
                "costs_total": None,
                "net_pnl": net,
                "source": "live",
                "payload": {},
            },
        )
        rows = await self.db_mod.list_paper_campaigns(self.user, "candle_recovery", only_traded=False)
        return next(r["id"] for r in rows if r["campaign_key"] == key)

    async def test_an_unpriced_row_really_goes(self):
        rid = await self._save("unpriced", None, 3)
        self.assertTrue(await self.db_mod.delete_paper_campaign(self.user, rid))
        self.assertIsNone(await self.db_mod.get_paper_campaign(self.user, rid))

    async def test_a_settled_row_goes_too(self):
        """The whole point of widening it -- test runs book real numbers."""
        rid = await self._save("settled", -2882.0, 3)
        self.assertTrue(await self.db_mod.delete_paper_campaign(self.user, rid))
        self.assertIsNone(await self.db_mod.get_paper_campaign(self.user, rid))

    async def test_a_no_buy_row_goes(self):
        rid = await self._save("nobuy", None, 0)
        self.assertTrue(await self.db_mod.delete_paper_campaign(self.user, rid))
        self.assertIsNone(await self.db_mod.get_paper_campaign(self.user, rid))

    async def test_another_users_row_is_untouchable(self):
        """The one guard that is left, and the one that matters."""
        rid = await self._save("unpriced", None, 3)
        other = await self.db_mod.create_user("someone-else", "y" * 60)
        self.assertFalse(await self.db_mod.delete_paper_campaign(other, rid))
        self.assertIsNotNone(await self.db_mod.get_paper_campaign(self.user, rid))

    async def test_only_the_named_row_goes(self):
        keep = await self._save("keep", 500.0, 2)
        drop = await self._save("drop", 100.0, 2)
        self.assertTrue(await self.db_mod.delete_paper_campaign(self.user, drop))
        self.assertIsNotNone(await self.db_mod.get_paper_campaign(self.user, keep))
