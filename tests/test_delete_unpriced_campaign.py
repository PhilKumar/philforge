"""An unpriced closed campaign can be removed; a settled one cannot.

Phil, 2026-09-23: "Give me a del button to remove the unpriced entries on the
closed campaigns."  He chose the narrow version deliberately -- whole rows that
never priced, never individual legs, because dropping an unpriced leg and
summing the rest reports a SMALLER loss than the campaign had.

So the thing worth testing is the refusal, not the delete.  A row with a real
net is booked money and must survive every path into this door.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module

UNPRICED = {"id": 7, "strategy": "candle_recovery", "net_pnl": None, "buys": 3}
SETTLED = {"id": 8, "strategy": "candle_recovery", "net_pnl": -2882.0, "buys": 3}
NO_TRADE = {"id": 9, "strategy": "candle_recovery", "net_pnl": None, "buys": 0}


class TheDeleteDoorIsNarrow(unittest.IsolatedAsyncioTestCase):
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
            patch.object(app_module._db_mod, "delete_unpriced_paper_campaign", _delete),
        ):
            return await app_module.delete_paper_campaign(strategy, int(row["id"]), SimpleNamespace())

    async def test_an_unpriced_row_is_removed(self):
        out = await self._call(UNPRICED)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["deleted"], 7)
        self.assertEqual(self.asked, [(1, 7)])

    async def test_a_settled_campaign_is_refused_and_never_reaches_the_db(self):
        with self.assertRaises(app_module.HTTPException) as caught:
            await self._call(SETTLED)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("booked money", caught.exception.detail)
        self.assertEqual(self.asked, [])

    async def test_a_campaign_that_never_bought_is_refused(self):
        """Its P&L is nil, not missing -- the ledger says 'no trade'."""
        with self.assertRaises(app_module.HTTPException) as caught:
            await self._call(NO_TRADE)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.asked, [])

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


class TheSqlGuardsItToo(unittest.TestCase):
    """The route's check is not the only one; a careless caller must fail too."""

    def test_the_statement_refuses_a_priced_row(self):
        from pathlib import Path

        db = Path(app_module.__file__).parent.joinpath("db.py").read_text(encoding="utf-8")
        body = db[db.index("async def delete_unpriced_paper_campaign(") :]
        body = body[: body.index("\nasync def ")]
        self.assertIn("net_pnl IS NULL", body)
        self.assertIn("buys > 0", body)
        self.assertIn("user_id = ?", body)


class TheButtonIsReachable(unittest.TestCase):
    """A data-pf-action missing from the registry is a click that does nothing."""

    def test_the_action_is_registered_and_exported(self):
        from pathlib import Path

        js = Path(app_module.__file__).parent.joinpath("static", "philforge-app.js").read_text(encoding="utf-8")
        self.assertIn("'deleteUnpricedCampaign',", js)
        self.assertIn("window.deleteUnpricedCampaign = deleteUnpricedCampaign;", js)
        self.assertIn('data-pf-action="deleteUnpricedCampaign"', js)

    def test_the_button_is_only_drawn_on_an_unpriced_row(self):
        from pathlib import Path

        js = Path(app_module.__file__).parent.joinpath("static", "philforge-app.js").read_text(encoding="utf-8")
        self.assertIn("const delCell = (net == null && Number(row.buys || 0) > 0)", js)


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
        self.assertTrue(await self.db_mod.delete_unpriced_paper_campaign(self.user, rid))
        self.assertIsNone(await self.db_mod.get_paper_campaign(self.user, rid))

    async def test_a_settled_row_survives_the_same_call(self):
        rid = await self._save("settled", -2882.0, 3)
        self.assertFalse(await self.db_mod.delete_unpriced_paper_campaign(self.user, rid))
        self.assertIsNotNone(await self.db_mod.get_paper_campaign(self.user, rid))

    async def test_a_no_buy_row_survives(self):
        rid = await self._save("nobuy", None, 0)
        self.assertFalse(await self.db_mod.delete_unpriced_paper_campaign(self.user, rid))
        self.assertIsNotNone(await self.db_mod.get_paper_campaign(self.user, rid))

    async def test_another_users_row_is_untouchable(self):
        rid = await self._save("unpriced", None, 3)
        other = await self.db_mod.create_user("someone-else", "y" * 60)
        self.assertFalse(await self.db_mod.delete_unpriced_paper_campaign(other, rid))
        self.assertIsNotNone(await self.db_mod.get_paper_campaign(self.user, rid))
