"""A mother that never bought is not a line in the money ledger.

Fib Boundary archives EVERY mother it accepts, including the ones that break
before a single fill. On 4 September that was seven `mother_broken_no_buys`
rows in one morning, each carrying Rs 0.00 in the contract, buys, deployed and
net columns -- so the closed-campaign table read 16 campaigns when seven had
traded, and the row limit was being spent on nothing at all.

Phil, 2026-09-08: "in Fib-boundary I do not want all the no buys data... It is
not useful at all."

They are still archived; they are simply not shown as results.
"""

import asyncio
import base64
import os
import sys
import tempfile
import unittest
import uuid

os.environ.setdefault("PHILFORGE_PIN", "123456")
os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())
_TMP = tempfile.mkdtemp(prefix="philforge-nobuy-")
os.environ["PHILFORGE_DB"] = os.path.join(_TMP, "philforge.db")
os.environ.setdefault("PHILFORGE_USER_DATA_ROOT", os.path.join(_TMP, "data"))
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as db_module  # noqa: E402


def _campaign(key: str, buys: int, net, reason: str) -> dict:
    return {
        "campaign_key": key,
        "symbol": "NIFTY",
        "contract": "23700.0 CE" if buys else "",
        "opened_at": f"2026-09-04T{key}:00+05:30",
        "closed_at": f"2026-09-04T{key}:00+05:30",
        "status": "CLOSED" if buys else "MOTHER_BROKEN",
        "exit_reason": reason,
        "buys": buys,
        "deployed_inr": 66309.75 if buys else 0.0,
        "net_pnl": net,
        "source": "live",
        "payload": {"engine": {"whatever": True}},
    }


class NoBuyCampaignsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db_module.init_db()
        self.user = await db_module.create_user(f"nobuy-{uuid.uuid4().hex[:8]}", "x" * 60)
        await db_module.save_paper_campaign(
            self.user, "fib_boundary", _campaign("10:18", 8, -2627.92, "intraday_close")
        )
        for stamp in ("11:20", "11:10", "10:30", "10:25"):
            await db_module.save_paper_campaign(
                self.user, "fib_boundary", _campaign(stamp, 0, 0.0, "mother_broken_no_buys")
            )
        # The other shape of nothing: a mother that lived to the close and
        # still never filled. Its net is NULL, not zero.
        await db_module.save_paper_campaign(self.user, "fib_boundary", _campaign("14:35", 0, None, "intraday_close"))

    async def test_only_the_campaigns_that_bought_are_listed(self):
        rows = await db_module.list_paper_campaigns(self.user, "fib_boundary", limit=100)
        self.assertEqual([r["buys"] for r in rows], [8])
        self.assertNotIn("mother_broken_no_buys", [r["exit_reason"] for r in rows])

    async def test_they_are_archived_all_the_same(self):
        rows = await db_module.list_paper_campaigns(self.user, "fib_boundary", limit=100, only_traded=False)
        self.assertEqual(len(rows), 6)

    async def test_the_limit_is_spent_on_campaigns_that_traded(self):
        # Six of these are noise; asking for two must not return two noise rows
        # and call the archive exhausted.
        rows = await db_module.list_paper_campaigns(self.user, "fib_boundary", limit=2)
        self.assertTrue(all(r["buys"] > 0 for r in rows))


if __name__ == "__main__":
    asyncio.run(unittest.main())
