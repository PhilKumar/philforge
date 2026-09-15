"""A LIVE Gap Carry stays live through a restart, and Auto trades the mode chosen.

Phil, 2026-09-15: "for Gap Carry the auto live is not persistent, it is not
surviving the restart and revert back to paper".

Four faults, one symptom:
  * the Auto step started every campaign with mode="paper", hardcoded;
  * a saved campaign did not record its mode, and the restore built it with no
    executor -- shown as paper, and unable to sell a real leg at 09:20;
  * changing Paper/Live while Auto was on never reached the server;
  * the status reply said "paper" as a constant, so the page drew Paper.
"""

import asyncio
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PHILFORGE_PIN", "test-pin-not-real")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_SCRIP_MASTER", "0")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app as app_module  # noqa: E402
from engine import gap_carry_paper  # noqa: E402

IST = app_module.IST
JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")


def _engine(executor=None):
    return gap_carry_paper.GapCarryPaper(config=gap_carry_paper.GapCarryConfig(), executor=executor)


class TheSavedCampaignSaysWhatItIs(unittest.TestCase):
    def test_a_live_campaign_is_saved_as_live(self):
        self.assertEqual(_engine(executor=object()).to_dict()["mode"], "live")

    def test_a_paper_campaign_is_saved_as_paper(self):
        self.assertEqual(_engine().to_dict()["mode"], "paper")

    def test_a_recorded_mode_is_read_back(self):
        self.assertEqual(app_module._gap_carry_saved_mode({"mode": "live"}), "live")
        self.assertEqual(app_module._gap_carry_saved_mode({"mode": "paper"}), "paper")

    def test_an_old_state_holding_a_dhan_order_is_live(self):
        """Written before the mode was recorded: only the live path has an order id."""
        self.assertEqual(app_module._gap_carry_saved_mode({"position": {"order_id": "3122609"}}), "live")

    def test_an_old_state_with_nothing_real_is_paper(self):
        self.assertEqual(app_module._gap_carry_saved_mode({"position": None}), "paper")


class TheRestoreBringsItBackLive(unittest.TestCase):
    def setUp(self):
        app_module._gap_carry_engines.clear()

    def tearDown(self):
        app_module._gap_carry_engines.clear()

    def _restore(self, saved: dict, *, live_open: bool):
        stored = json.dumps({"engine": saved, "running": True, "last_candle_timestamp": "2026-09-15T09:15:00+05:30"})

        async def _get(_key):
            return stored

        async def _history(_broker):
            return None

        async def _reconcile(*_a, **_k):
            return None

        with (
            patch.object(app_module._db_mod, "get_app_state", _get),
            patch.object(app_module, "_gap_carry_history_lookup", _history),
            patch.object(app_module, "_reconcile_shared_live_orders", _reconcile),
            patch.object(app_module, "_gap_carry_live_open", lambda: live_open),
            patch.object(app_module, "_gap_carry_executor", lambda broker, mode: ("LIVE-EXECUTOR", mode)),
            patch.object(app_module, "CascadeOptionsAdapter", lambda *a, **k: None),
        ):
            return asyncio.run(app_module._restore_gap_carry_open_state(1, object(), activate=False))

    def test_a_live_campaign_gets_its_order_path_back(self):
        runtime = self._restore(_engine(executor=object()).to_dict(), live_open=True)
        self.assertEqual(runtime.engine.executor, ("LIVE-EXECUTOR", "live"))
        self.assertTrue(runtime.running)

    def test_a_paper_campaign_stays_paper(self):
        runtime = self._restore(_engine().to_dict(), live_open=True)
        self.assertIsNone(runtime.engine.executor)

    def test_live_the_server_no_longer_allows_is_held_not_run_on_paper(self):
        runtime = self._restore(_engine(executor=object()).to_dict(), live_open=False)
        self.assertIsNone(runtime.engine.executor)
        self.assertFalse(runtime.running, "a paper exit would book a sale the account never made")
        self.assertIn("LIVE", runtime.engine.frozen_reason)


class AutoTradesTheModeItWasGiven(unittest.TestCase):
    AT_1511 = datetime(2026, 9, 15, 15, 11, tzinfo=IST)

    def setUp(self):
        app_module._gap_carry_engines.clear()
        self.started = []
        self.saved_open = 0

    def tearDown(self):
        app_module._gap_carry_engines.clear()

    def _step(self, setting, *, live_open=True, runtime=None):
        async def _start(uid, payload, *, broker_client):
            self.started.append(payload.mode)

        async def _restore(uid, broker, activate=True):
            return runtime

        async def _save_auto(uid):
            return None

        async def _save_open(uid, force=False):
            self.saved_open += 1

        with (
            patch.object(app_module, "_resolve_user_broker_client", lambda user: (object(), "user")),
            patch.object(app_module, "_restore_gap_carry_open_state", _restore),
            patch.object(app_module, "_start_gap_carry_campaign", _start),
            patch.object(app_module, "_save_gap_carry_auto", _save_auto),
            patch.object(app_module, "_save_gap_carry_open_state", _save_open),
            patch.object(app_module, "_gap_carry_live_open", lambda: live_open),
        ):
            return asyncio.run(app_module._gap_carry_auto_step({"id": 1}, setting, now=self.AT_1511))

    def test_auto_set_to_live_starts_a_live_campaign(self):
        self.assertEqual(self._step({"enabled": True, "mode": "live"}), "entered")
        self.assertEqual(self.started, ["live"])

    def test_auto_set_to_paper_starts_a_paper_campaign(self):
        self._step({"enabled": True, "mode": "paper"})
        self.assertEqual(self.started, ["paper"])

    def test_live_refused_by_the_server_buys_nothing_and_says_so(self):
        setting = {"enabled": True, "mode": "live"}
        self.assertEqual(self._step(setting, live_open=False), "start-failed")
        self.assertEqual(self.started, [], "never swapped for a paper trade")
        self.assertIn("LIVE", setting["last_error"])

    def test_an_idle_paper_campaign_does_not_take_the_live_nights_entry(self):
        idle = SimpleNamespace(
            engine=SimpleNamespace(executor=None, has_open_position=False, status="WAITING"),
            running=True,
            task=None,
        )
        app_module._gap_carry_engines[1] = idle
        self._step({"enabled": True, "mode": "live"}, runtime=idle)
        self.assertNotIn(1, app_module._gap_carry_engines)
        self.assertFalse(idle.running)
        self.assertEqual(self.saved_open, 1, "the retirement is saved, or a restart restores it")
        self.assertEqual(self.started, ["live"])

    def test_a_paper_campaign_holding_a_leg_is_never_retired(self):
        holding = SimpleNamespace(
            engine=SimpleNamespace(
                executor=None,
                has_open_position=True,
                status="OPEN",
                position=SimpleNamespace(session=self.AT_1511.date()),
                config=SimpleNamespace(exit_time=datetime(2026, 9, 15, 9, 20).time()),
            ),
            running=True,
            task=None,
        )
        app_module._gap_carry_engines[1] = holding
        self._step({"enabled": True, "mode": "live"}, runtime=holding)
        self.assertIs(app_module._gap_carry_engines.get(1), holding)
        self.assertTrue(holding.running)


class ThePageShowsWhatTheServerHas(unittest.TestCase):
    def test_the_status_mode_is_not_a_constant(self):
        import inspect

        src = inspect.getsource(app_module.gap_carry_paper_status)
        self.assertNotIn('"mode": "paper",', src)
        self.assertIn("_gap_carry_live_open()", src)

    def test_changing_mode_with_auto_on_is_saved(self):
        fn = JS.split("async function setGapCarryMode(")[1].split("\nfunction ")[0]
        self.assertIn("/api/gap-carry/auto", fn)
        self.assertIn("_lastGapCarryAuto.enabled", fn)
        self.assertIn("customConfirm", fn, "real money is confirmed before it is switched on")

    def test_the_auto_card_draws_the_saved_mode(self):
        fn = JS.split("function _renderGapCarryAuto(")[1].split("\nfunction ")[0]
        self.assertIn("'oc-gap-mode'", fn)
        self.assertIn("LIVE", fn)


if __name__ == "__main__":
    unittest.main()
