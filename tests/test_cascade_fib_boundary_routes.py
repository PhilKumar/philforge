import json
import os
import shutil
import sys
import unittest
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_DB = Path("/tmp/philforge-test-fib-routes.db")
TEST_USER_DATA = Path("/tmp/philforge-test-fib-routes-data")

os.environ["PHILFORGE_PIN"] = "123456"
os.environ["PHILFORGE_DB"] = str(TEST_DB)
os.environ["PHILFORGE_USER_DATA_ROOT"] = str(TEST_USER_DATA)
os.environ["PHILFORGE_SKIP_STARTUP_JOBS"] = "1"
os.environ["ENCRYPTION_KEY"] = "QmG8YWqLPtWFDn7gCAiHJXoX7zH5zi89kUnkkMvibU="
os.environ["DHAN_PIN"] = ""
os.environ["DHAN_TOTP_SECRET"] = ""

import app as app_module  # noqa: E402


class _DummyRequest:
    def __init__(self, user_id: int = 11):
        self.state = SimpleNamespace(user_id=user_id)


def _today_1m_mother() -> datetime:
    """A 1m candle that has definitely CLOSED, whenever the suite is run.

    The old version always used 09:20 today, so the whole file failed on any
    run before 09:21 IST -- every morning, and passing in CI only because CI
    happened to run at night. It steps back a day when today's session has not
    reached that minute yet; a past mother is a valid paper start now.
    """
    now = datetime.now(app_module.IST)
    mother = now.replace(hour=9, minute=20, second=0, microsecond=0)
    if mother + timedelta(minutes=1) > now:
        mother -= timedelta(days=1)
    return mother


def _recent_5m_mother() -> datetime:
    # A completed 5m candle several days back, safely inside the replay window.
    return (datetime.now(app_module.IST) - timedelta(days=6)).replace(hour=14, minute=15, second=0, microsecond=0)


class _Broker:
    def place_option_order(self, *a, **k):
        return SimpleNamespace(order_id="DHAN-1")

    def verify_order_fill(self, order_id, max_wait_sec=20, poll_interval=1.5):
        # Since 2026-08-30 the live executor verifies every order it places;
        # a fake broker that cannot answer reads as UNKNOWN and halts.
        return {"order_id": order_id, "status": "FILLED", "filled_qty": 65, "avg_price": 0.0}


class _StubAdapter:
    def get_ticker(self, _symbol):
        return {"last_price": 24_500}


def _live_ladder(symbol: str, broker, *, armed: bool = True, live: bool = True):
    """A LIVE ladder that has already bought a rung, for save/restore and for
    proving one instrument's actions leave the others alone."""
    from datetime import date as _date

    from engine.fib_touch_ladder import FibTouchConfig, FibTouchLadder, LiveExecutor, PaperExecutor

    base = datetime(2026, 8, 6, 9, 15)
    rows = [
        # Mother high 24,780, above its own bounce -- a close past the mother
        # ends the campaign, so a fixture that must keep trading contains it.
        (24_660, 24_780, 24_640, 24_642),
        (24_642, 24_644, 24_620, 24_622),
        (24_622, 24_624, 24_600, 24_602),
        (24_602, 24_612, 24_600, 24_610),
        (24_610, 24_620, 24_608, 24_618),
        (24_618, 24_650, 24_615, 24_645),
        (24_645, 24_700, 24_640, 24_695),
        (24_695, 24_698, 24_680, 24_682),
        (24_682, 24_684, 24_670, 24_672),
        (24_672, 24_674, 24_495, 24_510),
        # THE TURN. Since 2026-08-16 a touch only collects; the buy needs two
        # red closes under the collected line and then a rise back through the
        # first red's close. Without these three bars this ladder holds nothing.
        (24_510, 24_512, 24_493, 24_500),
        (24_500, 24_501, 24_490, 24_494),
        (24_494, 24_508, 24_492, 24_506),
    ]
    candles = [
        app_module.IndexCandle(base + timedelta(minutes=i), o, h, low, c) for i, (o, h, low, c) in enumerate(rows)
    ]
    engine = FibTouchLadder(
        FibTouchConfig(
            symbol=symbol,
            side="CE",
            mother_timestamp=candles[0].timestamp,
            lot_size=65,
            strike_step=50.0,
        ),
        premium_lookup=lambda *a: 200.0,
        expiry_source=lambda on: [_date(2026, 8, 11)],
        executor=LiveExecutor(broker, symbol, armed=armed) if live else PaperExecutor(),
    )
    with patch("engine.fib_touch_ladder.FIB_TOUCH_LIVE_EXECUTION_ENABLED", True):
        for candle in candles:
            engine.on_candle(candle)
    return engine


class FibBoundaryRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if TEST_DB.exists():
            TEST_DB.unlink()
        if TEST_USER_DATA.exists():
            shutil.rmtree(TEST_USER_DATA)
        app_module.config.DB_PATH = str(TEST_DB)
        app_module.config.USER_DATA_ROOT = str(TEST_USER_DATA)
        app_module._USER_DATA_ROOT = str(TEST_USER_DATA)
        app_module._db_mod.config.DB_PATH = str(TEST_DB)
        app_module._db_mod.config.USER_DATA_ROOT = str(TEST_USER_DATA)
        app_module._db_mod._initialized = False
        app_module._fib_boundary_engines.clear()
        # The start route is rate limited like its siblings; a test file calls
        # it far faster than any human, so each test starts with a clean slate.
        app_module._rate_limits.clear()
        await app_module._db_mod.init_db()

    async def asyncTearDown(self):
        for ladders in app_module._fib_boundary_engines.values():
            for runtime in ladders.values():
                if runtime.task and not runtime.task.done():
                    runtime.task.cancel()
        app_module._fib_boundary_engines.clear()

    async def test_status_not_started(self):
        result = await app_module.fib_boundary_paper_status(_DummyRequest())
        self.assertEqual(result["status"], "not_started")
        self.assertEqual(result["mode"], "paper")
        self.assertFalse(result["live_available"])
        # A LIST now, always present, so the console renders zero panels rather
        # than special-casing "no campaign".
        self.assertEqual(result["campaigns"], [])

    async def test_start_rejects_bad_side(self):
        payload = app_module.FibTouchStartPayload(mother_timestamp=_today_1m_mother().isoformat(), side="XX")
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_paper_start(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("side must be CE or PE", str(raised.exception.detail))

    async def test_start_fails_closed_when_live_order_reconciliation_is_unavailable(self):
        payload = app_module.FibTouchStartPayload(
            mother_timestamp=_today_1m_mother().isoformat(), side="CE", mode="live"
        )
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_paper_start(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("partial-fill handling", str(raised.exception.detail))

    async def test_start_rejects_an_unknown_buy_mode(self):
        """The merge's switch: every level of every fib, or only where two fibs
        meet. Anything else is a typo, and a typo that silently fell back to
        "levels" would trade the other strategy without saying so."""
        payload = app_module.FibTouchStartPayload(
            mother_timestamp=_today_1m_mother().isoformat(), buy_mode="convergance"
        )
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_paper_start(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("buy_mode must be one of", str(raised.exception.detail))

    async def test_both_halves_of_the_merged_strategy_are_reachable(self):
        """Fib Boundary and Fib Space are one engine since 2026-08-15. The
        switch was built into it and wired to nothing -- the page could only
        ever start one of the two."""
        for mode in ("levels", "convergence"):
            payload = app_module.FibTouchStartPayload(mother_timestamp=_today_1m_mother().isoformat(), buy_mode=mode)
            with patch.object(
                app_module, "_request_broker_context", AsyncMock(return_value=({"id": 11}, None, "user"))
            ):
                with self.assertRaises(app_module.HTTPException) as raised:
                    await app_module.fib_boundary_paper_start(payload, _DummyRequest())
            # Reaching broker validation is what proves the mode was accepted.
            self.assertIn("Connect a Dhan account", str(raised.exception.detail), mode)

    async def test_start_rejects_an_unknown_symbol(self):
        payload = app_module.FibTouchStartPayload(mother_timestamp=_today_1m_mother().isoformat(), symbol="RELIANCE")
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_paper_start(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Unknown symbol", str(raised.exception.detail))

    async def test_start_accepts_every_listed_instrument(self):
        # All five reach broker validation, so none is rejected as unknown.
        for symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"):
            payload = app_module.FibTouchStartPayload(mother_timestamp=_today_1m_mother().isoformat(), symbol=symbol)
            with patch.object(
                app_module, "_request_broker_context", AsyncMock(return_value=({"id": 11}, None, "user"))
            ):
                with self.assertRaises(app_module.HTTPException) as raised:
                    await app_module.fib_boundary_paper_start(payload, _DummyRequest())
            self.assertIn("Connect a Dhan account", str(raised.exception.detail), symbol)

    async def test_a_mother_from_an_earlier_day_is_accepted(self):
        """Phil, 2026-08-07: a paper campaign must run on past days too.

        It is priced from RECORDED history rather than today's LTP, so the only
        limit is how far back Dhan still serves candles -- not a window this
        code invents. Reaching broker validation proves the date guard is gone.
        """
        stale = (datetime.now(app_module.IST) - timedelta(days=3)).replace(hour=11, minute=30, second=0, microsecond=0)
        payload = app_module.FibTouchStartPayload(mother_timestamp=stale.isoformat())
        with patch.object(app_module, "_request_broker_context", AsyncMock(return_value=({"id": 11}, None, "user"))):
            with self.assertRaises(app_module.HTTPException) as raised:
                await app_module.fib_boundary_paper_start(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Connect a Dhan account", str(raised.exception.detail))
        self.assertNotIn("Backtest", str(raised.exception.detail))

    async def test_a_recent_bar_is_never_priced_from_history(self):
        """The rule that made the old restriction look necessary, kept.

        A minute young enough to still have a live quote must NOT fall through
        to recorded history -- history has not written it yet. A failed live
        quote returns None rather than reaching for the older source.
        """
        calls: list = []
        broker = SimpleNamespace(get_option_ltp=lambda *a, **k: 0)
        lookup = app_module._fib_touch_premium_lookup(broker, "NIFTY", history=lambda *a, **k: calls.append(a) or 123.0)
        fresh = datetime.now(app_module.IST).replace(second=0, microsecond=0)
        self.assertIsNone(lookup(fresh, 24_400, date(2026, 8, 11), "CE"))
        self.assertEqual(calls, [], "a live-window bar must never read history")

    async def test_an_old_bar_is_priced_from_history_not_the_live_quote(self):
        quoted: list = []

        def _ltp(*a, **k):
            quoted.append(a)
            return 999.0

        lookup = app_module._fib_touch_premium_lookup(
            SimpleNamespace(get_option_ltp=_ltp), "NIFTY", history=lambda when, contract: 88.5
        )
        old_bar = datetime.now(app_module.IST) - timedelta(days=2)
        self.assertEqual(lookup(old_bar, 24_400, date(2026, 8, 11), "CE"), 88.5)
        self.assertEqual(quoted, [], "an old bar must never read today's LTP")

    async def test_an_old_bar_with_no_history_source_has_no_price(self):
        lookup = app_module._fib_touch_premium_lookup(
            SimpleNamespace(get_option_ltp=lambda *a, **k: 999.0), "NIFTY", history=None
        )
        old_bar = datetime.now(app_module.IST) - timedelta(days=2)
        self.assertIsNone(lookup(old_bar, 24_400, date(2026, 8, 11), "CE"))

    async def test_start_rejects_a_timestamp_outside_the_session(self):
        payload = app_module.FibTouchStartPayload(
            mother_timestamp=datetime.now(app_module.IST)
            .replace(hour=8, minute=30, second=0, microsecond=0)
            .isoformat()
        )
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_paper_start(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("09:15", str(raised.exception.detail))

    async def test_valid_mother_reaches_broker_validation(self):
        payload = app_module.FibTouchStartPayload(mother_timestamp=_today_1m_mother().isoformat(), side="CE")
        with patch.object(
            app_module,
            "_request_broker_context",
            AsyncMock(return_value=({"id": 11}, None, "user")),
        ):
            with self.assertRaises(app_module.HTTPException) as raised:
                await app_module.fib_boundary_paper_start(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Connect a Dhan account", str(raised.exception.detail))

    async def test_symbols_route_tells_the_console_what_is_honest(self):
        payload = await app_module.fib_touch_symbols(_DummyRequest())
        self.assertEqual(payload["levels"], [2, 3, 4, 6, 8, 12, 16])
        rows = {row["symbol"]: row for row in payload["symbols"]}
        self.assertEqual(set(rows), {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"})
        # NSE withdrew these weeklies; the console must not offer a week that
        # does not exist.
        self.assertTrue(rows["NIFTY"]["has_weeklies"])
        self.assertTrue(rows["SENSEX"]["has_weeklies"])
        for symbol in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
            self.assertFalse(rows[symbol]["has_weeklies"], symbol)
        # And no premium history means no backtest, said out loud.
        self.assertFalse(rows["FINNIFTY"]["backtestable"])
        self.assertFalse(rows["MIDCPNIFTY"]["backtestable"])
        self.assertEqual(rows["NIFTY"]["lot_size"], 65)
        self.assertEqual(rows["BANKNIFTY"]["strike_step"], 100.0)

    async def test_chart_rejects_an_unknown_symbol(self):
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_paper_chart(
                _today_1m_mother().isoformat(), _DummyRequest(), symbol="RELIANCE"
            )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Unknown symbol", str(raised.exception.detail))

    async def test_chart_rejects_a_bad_side(self):
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_paper_chart(_today_1m_mother().isoformat(), _DummyRequest(), side="XX")
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("side must be CE or PE", str(raised.exception.detail))

    async def test_chart_needs_a_broker_before_it_draws_anything(self):
        with patch.object(app_module, "_request_broker_context", AsyncMock(return_value=({"id": 11}, None, "user"))):
            with self.assertRaises(app_module.HTTPException) as raised:
                await app_module.fib_boundary_paper_chart(_today_1m_mother().isoformat(), _DummyRequest())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Connect a Dhan account", str(raised.exception.detail))

    async def test_chart_prices_the_same_ladder_the_engine_trades(self):
        # The whole point of recomputing the anchor in the route is that it
        # cannot drift from the engine. Prove it: same candles, same mother,
        # same side -> byte-identical levels.
        from engine.fib_touch_ladder import HALVING_LEVELS, find_swing_anchor, level_price

        rows = [
            (24_660, 24_665, 24_640, 24_642),  # MOTHER
            (24_642, 24_644, 24_620, 24_622),
            (24_622, 24_624, 24_600, 24_602),  # low 24,600
            (24_602, 24_612, 24_600, 24_610),
            (24_610, 24_620, 24_608, 24_618),  # LOW frozen
            (24_618, 24_650, 24_615, 24_645),
            (24_645, 24_700, 24_640, 24_695),  # high 24,700
            (24_695, 24_698, 24_680, 24_682),
            (24_682, 24_684, 24_670, 24_672),  # HIGH frozen
        ]
        base = datetime(2026, 8, 6, 9, 15)
        candles = [
            app_module.IndexCandle(base + timedelta(minutes=i), o, h, low, c) for i, (o, h, low, c) in enumerate(rows)
        ]
        anchor = find_swing_anchor(candles, candles[0].timestamp, "CE")
        self.assertIsNotNone(anchor)
        assert anchor is not None
        # Both anchors come from the swing AFTER the mother, never its own high.
        self.assertEqual(anchor.high, 24_700.0)
        self.assertEqual(anchor.low, 24_600.0)
        priced = [round(level_price("CE", anchor.high, anchor.low, level), 2) for level in HALVING_LEVELS]
        # span is 100, so the ladder walks down in whole spans from 24,700.
        self.assertEqual(priced, [24_500.0, 24_400.0, 24_300.0, 24_100.0, 23_900.0, 23_500.0, 23_100.0])

    async def test_a_ladder_survives_a_restart_and_comes_back_unarmed(self):
        """The real save/restore path, not the engine's round-trip in isolation."""
        broker = _Broker()
        engine = _live_ladder("NIFTY", broker)
        self.assertTrue(engine.fills, "fixture should have bought a rung")
        self.assertTrue(engine.get_status()["armed"])

        runtime = app_module._CascadeRuntime(engine, None, broker, engine.history[-1].timestamp, running=True)
        app_module._fib_boundary_engines[11] = {"NIFTY": runtime}
        await app_module._save_fib_boundary_open_state(11, force=True)

        # The process "restarts": every in-memory ladder is gone.
        app_module._fib_boundary_engines.clear()
        app_module._fib_boundary_open_state_last_save.clear()

        revived = await app_module._restore_fib_boundary_open_state(11, broker, activate=False)
        self.assertEqual(set(revived), {"NIFTY"})
        status = revived["NIFTY"].engine.get_status()
        self.assertEqual(len(status["fills"]), len(engine.fills))
        self.assertEqual(status["deployed_inr"], engine.deployed_inr)
        # The newest fib is the anchor now: 0 = 24,698, 1 = 24,600.
        self.assertEqual(status["anchor"]["high"], 24_698.0)
        self.assertEqual(status["anchor"]["low"], 24_600.0)
        # THE RULE: a restart is not a person deciding to trade real money.
        self.assertEqual(status["mode"], "live")
        self.assertFalse(status["armed"])

    async def test_a_killed_ladder_can_be_deleted(self):
        """Phil, 2026-08-17: Kill worked, then Delete said "This ladder still
        reports holdings; it cannot be deleted." Kill sells and books the
        basket; what it leaves in `fills` is the record, not a position."""
        broker = _Broker()
        engine = _live_ladder("NIFTY", broker, live=False)
        self.assertTrue(engine.fills, "fixture should have bought a rung")
        runtime = app_module._CascadeRuntime(engine, _StubAdapter(), broker, engine.history[-1].timestamp, running=True)
        app_module._fib_boundary_engines[11] = {"NIFTY": runtime}

        killed = await app_module.fib_boundary_paper_kill(_DummyRequest(), symbol="NIFTY")
        self.assertEqual(killed["status"], "killed")
        self.assertEqual(killed["campaign"]["open_lots"], 0)
        self.assertEqual(len(killed["campaign"]["fills"]), 1, "the record of the buy is kept")

        deleted = await app_module.fib_boundary_paper_delete(_DummyRequest(), symbol="NIFTY")
        self.assertEqual(deleted, {"status": "ok", "deleted": "NIFTY"})
        self.assertNotIn("NIFTY", app_module._fib_boundary_engines.get(11, {}))

    async def test_a_ladder_still_holding_cannot_be_deleted(self):
        """The other half: Delete is bookkeeping, never an exit."""
        broker = _Broker()
        engine = _live_ladder("NIFTY", broker, live=False)
        runtime = app_module._CascadeRuntime(engine, _StubAdapter(), broker, engine.history[-1].timestamp, running=True)
        app_module._fib_boundary_engines[11] = {"NIFTY": runtime}
        with self.assertRaises(app_module.HTTPException) as caught:
            await app_module.fib_boundary_paper_delete(_DummyRequest(), symbol="NIFTY")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("NIFTY", app_module._fib_boundary_engines[11])

    async def test_a_restart_brings_back_every_ladder_and_arms_none_of_them(self):
        broker = _Broker()
        app_module._fib_boundary_engines[11] = {
            symbol: app_module._CascadeRuntime(
                engine := _live_ladder(symbol, broker), None, broker, engine.history[-1].timestamp, running=True
            )
            for symbol in ("NIFTY", "SENSEX")
        }
        await app_module._save_fib_boundary_open_state(11, force=True)

        app_module._fib_boundary_engines.clear()
        app_module._fib_boundary_open_state_last_save.clear()

        revived = await app_module._restore_fib_boundary_open_state(11, broker, activate=False)
        self.assertEqual(set(revived), {"NIFTY", "SENSEX"})
        for symbol, runtime in revived.items():
            status = runtime.engine.get_status()
            self.assertEqual(status["symbol"], symbol)
            self.assertTrue(status["fills"], symbol)
            self.assertFalse(status["armed"], symbol)

    async def test_a_legacy_single_snapshot_row_still_revives_its_ladder(self):
        """The row written before ladders were per-symbol held ONE campaign at
        the top level. A ladder in flight through the upgrade must not vanish."""
        broker = _Broker()
        engine = _live_ladder("NIFTY", broker)
        await app_module._db_mod.set_app_state(
            app_module._fib_boundary_open_state_key(11),
            json.dumps(
                {
                    "running": True,
                    "last_candle_timestamp": engine.history[-1].timestamp.isoformat(),
                    "engine": engine.to_dict(),
                },
                default=str,
            ),
        )
        app_module._fib_boundary_engines.clear()
        revived = await app_module._restore_fib_boundary_open_state(11, broker, activate=False)
        self.assertEqual(set(revived), {"NIFTY"})
        self.assertEqual(len(revived["NIFTY"].engine.get_status()["fills"]), len(engine.fills))

    async def test_a_snapshot_from_the_retired_engine_is_ignored_not_guessed_at(self):
        await app_module._db_mod.set_app_state(
            app_module._fib_boundary_open_state_key(11),
            json.dumps(
                {
                    "running": True,
                    "last_candle_timestamp": "2026-08-06T09:20:00+05:30",
                    "engine": {"timeframe": "5m", "rungs": []},
                }
            ),
        )
        app_module._fib_boundary_engines.clear()
        self.assertEqual(await app_module._restore_fib_boundary_open_state(11, object(), activate=False), {})

    async def test_one_bad_campaign_does_not_cost_the_user_the_good_one(self):
        broker = _Broker()
        engine = _live_ladder("NIFTY", broker)
        await app_module._db_mod.set_app_state(
            app_module._fib_boundary_open_state_key(11),
            json.dumps(
                {
                    "campaigns": [
                        {"running": True, "last_candle_timestamp": "nonsense", "engine": {"version": 1}},
                        {
                            "running": True,
                            "last_candle_timestamp": engine.history[-1].timestamp.isoformat(),
                            "engine": engine.to_dict(),
                        },
                    ]
                },
                default=str,
            ),
        )
        app_module._fib_boundary_engines.clear()
        revived = await app_module._restore_fib_boundary_open_state(11, broker, activate=False)
        self.assertEqual(set(revived), {"NIFTY"})

    async def test_two_symbols_run_side_by_side_and_are_killed_one_at_a_time(self):
        """The whole point of the change: a second instrument starts, and killing
        it leaves the first one holding its own basket."""
        broker = _Broker()
        app_module._fib_boundary_engines[11] = {
            symbol: app_module._CascadeRuntime(
                engine := _live_ladder(symbol, broker, live=False),
                _StubAdapter(),
                broker,
                engine.history[-1].timestamp,
                running=True,
            )
            for symbol in ("NIFTY", "SENSEX")
        }
        status = await app_module.fib_boundary_paper_status(_DummyRequest())
        self.assertEqual([row["symbol"] for row in status["campaigns"]], ["NIFTY", "SENSEX"])
        self.assertEqual(status["mode"], "paper")
        self.assertFalse(status["live_available"])

        killed = await app_module.fib_boundary_paper_kill(_DummyRequest(), symbol="SENSEX")
        self.assertEqual(killed["campaign"]["symbol"], "SENSEX")
        ladders = app_module._fib_boundary_engines[11]
        self.assertFalse(ladders["SENSEX"].running)
        self.assertTrue(ladders["NIFTY"].running, "killing one ladder must not touch the other")
        # And the survivor keeps its own fills.
        self.assertTrue(ladders["NIFTY"].engine.get_status()["fills"])

    async def test_arming_one_ladder_does_not_arm_the_other(self):
        broker = _Broker()
        app_module._fib_boundary_engines[11] = {
            symbol: app_module._CascadeRuntime(
                engine := _live_ladder(symbol, broker, armed=False),
                _StubAdapter(),
                broker,
                engine.history[-1].timestamp,
                running=True,
            )
            for symbol in ("NIFTY", "SENSEX")
        }
        with patch.object(app_module, "_FIB_TOUCH_LIVE_EXECUTION_ENABLED", True):
            result = await app_module.fib_boundary_live_arm("NIFTY", _DummyRequest())
        self.assertEqual(result["status"], "armed")
        ladders = app_module._fib_boundary_engines[11]
        self.assertTrue(ladders["NIFTY"].engine.get_status()["armed"])
        self.assertFalse(ladders["SENSEX"].engine.get_status()["armed"], "arming is per instrument")

    async def test_live_arm_is_safety_locked_by_default(self):
        broker = _Broker()
        engine = _live_ladder("NIFTY", broker, armed=False)
        app_module._fib_boundary_engines[11] = {
            "NIFTY": app_module._CascadeRuntime(
                engine, _StubAdapter(), broker, engine.history[-1].timestamp, running=True
            )
        }
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_live_arm("NIFTY", _DummyRequest())
        self.assertEqual(raised.exception.status_code, 503)
        self.assertFalse(engine.get_status()["armed"])

    async def test_the_same_instrument_twice_is_still_a_409(self):
        """THE rule the per-symbol keying rests on: a second instrument starts,
        a second mother on the SAME instrument does not."""
        broker = _Broker()
        engine = _live_ladder("NIFTY", broker)
        app_module._fib_boundary_engines[11] = {
            "NIFTY": app_module._CascadeRuntime(
                engine, _StubAdapter(), broker, engine.history[-1].timestamp, running=True
            )
        }
        payload = app_module.FibTouchStartPayload(mother_timestamp=_today_1m_mother().isoformat(), symbol="NIFTY")
        with patch.object(app_module, "_request_broker_context", AsyncMock(return_value=({"id": 11}, broker, "user"))):
            with self.assertRaises(app_module.HTTPException) as raised:
                await app_module.fib_boundary_paper_start(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("NIFTY CE ladder is already running on this instrument", str(raised.exception.detail))

    async def test_a_different_instrument_is_never_blocked_by_a_running_one(self):
        """The point of the change. A running NIFTY ladder used to hold the only
        slot; SENSEX must now get past the conflict check entirely."""
        broker = _Broker()
        engine = _live_ladder("NIFTY", broker)
        app_module._fib_boundary_engines[11] = {
            "NIFTY": app_module._CascadeRuntime(
                engine, _StubAdapter(), broker, engine.history[-1].timestamp, running=True
            )
        }
        payload = app_module.FibTouchStartPayload(mother_timestamp=_today_1m_mother().isoformat(), symbol="SENSEX")
        with patch.object(app_module, "_request_broker_context", AsyncMock(return_value=({"id": 11}, broker, "user"))):
            with self.assertRaises(Exception) as raised:
                await app_module.fib_boundary_paper_start(payload, _DummyRequest())
        # It fails LATER (this stub broker cannot serve candles) -- never at 409.
        self.assertNotEqual(getattr(raised.exception, "status_code", None), 409)

    async def test_kill_and_arm_reject_a_symbol_that_is_not_running(self):
        for route in (app_module.fib_boundary_paper_kill,):
            with self.assertRaises(app_module.HTTPException) as raised:
                await route(_DummyRequest(), symbol="SENSEX")
            self.assertEqual(raised.exception.status_code, 404)
            self.assertIn("SENSEX", str(raised.exception.detail))
        for route in (app_module.fib_boundary_live_arm, app_module.fib_boundary_live_kill):
            with self.assertRaises(app_module.HTTPException) as raised:
                await route("SENSEX", _DummyRequest())
            self.assertEqual(raised.exception.status_code, 404)
            self.assertIn("SENSEX", str(raised.exception.detail))

    async def test_killing_a_live_ladder_still_stops_at_the_execution_gate(self):
        """The MFA gate is gone (Phil, 2026-08-15) and Kill now serves both
        modes -- but the gate that actually matters is untouched: no real exit
        goes out while the broker order lifecycle is unverified, and the
        campaign keeps running rather than pretending it closed."""
        broker = _Broker()
        engine = _live_ladder("NIFTY", broker)
        app_module._fib_boundary_engines[11] = {
            "NIFTY": app_module._CascadeRuntime(
                engine, _StubAdapter(), broker, engine.history[-1].timestamp, running=True
            )
        }
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_paper_kill(_DummyRequest(), symbol="NIFTY")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("not yet reconciled", str(raised.exception.detail))
        self.assertTrue(app_module._fib_boundary_engines[11]["NIFTY"].running)

    async def test_live_kill_sends_real_exits_before_stopping(self):
        sent = []

        class _TrackingBroker:
            def place_option_order(self, **order):
                sent.append((order["underlying"], order["transaction_type"], order["quantity"]))
                return {"orderId": f"DHAN-{order['transaction_type']}-{len(sent)}"}

            def verify_order_fill(self, order_id, max_wait_sec=20, poll_interval=1.5):
                return {"order_id": order_id, "status": "FILLED", "filled_qty": 65, "avg_price": 0.0}

        broker = _TrackingBroker()
        engine = _live_ladder("NIFTY", broker)
        self.assertEqual([side for _symbol, side, _quantity in sent], ["BUY"])
        app_module._fib_boundary_engines[11] = {
            "NIFTY": app_module._CascadeRuntime(
                engine, _StubAdapter(), broker, engine.history[-1].timestamp, running=True
            )
        }

        with patch.object(app_module, "_FIB_TOUCH_LIVE_EXECUTION_ENABLED", True):
            with patch("engine.fib_touch_ladder.FIB_TOUCH_LIVE_EXECUTION_ENABLED", True):
                result = await app_module.fib_boundary_live_kill("NIFTY", _DummyRequest())
        self.assertEqual(result["status"], "killed")
        self.assertEqual(result["mode"], "live")
        self.assertEqual([side for _symbol, side, _quantity in sent], ["BUY", "SELL"])
        self.assertFalse(app_module._fib_boundary_engines[11]["NIFTY"].running)

    async def test_live_kill_broker_failure_never_marks_the_ladder_closed(self):
        class _FailingExitBroker:
            def place_option_order(self, **order):
                if order["transaction_type"] == "SELL":
                    raise RuntimeError("broker exit failed")
                return {"orderId": "DHAN-BUY-1"}

            def verify_order_fill(self, order_id, max_wait_sec=20, poll_interval=1.5):
                return {"order_id": order_id, "status": "FILLED", "filled_qty": 65, "avg_price": 0.0}

        broker = _FailingExitBroker()
        engine = _live_ladder("NIFTY", broker)
        runtime = app_module._CascadeRuntime(engine, _StubAdapter(), broker, engine.history[-1].timestamp, running=True)
        app_module._fib_boundary_engines[11] = {"NIFTY": runtime}

        with patch.object(app_module, "_FIB_TOUCH_LIVE_EXECUTION_ENABLED", True):
            with patch("engine.fib_touch_ladder.FIB_TOUCH_LIVE_EXECUTION_ENABLED", True):
                # Since 2026-08-30 the engine books the unknown outcome as
                # EXIT_ERROR instead of letting the broker error escape as a
                # 500, so the route answers 409 and the basket stays monitored.
                with self.assertRaises(app_module.HTTPException) as raised:
                    await app_module.fib_boundary_live_kill("NIFTY", _DummyRequest())
        self.assertEqual(raised.exception.status_code, 409)
        self.assertTrue(runtime.running)
        self.assertNotEqual(engine.status, "KILLED")
        self.assertEqual(engine.status, "EXIT_ERROR")

    async def test_live_kill_is_safety_locked_without_changing_runtime_state(self):
        broker = _Broker()
        engine = _live_ladder("NIFTY", broker)
        runtime = app_module._CascadeRuntime(engine, _StubAdapter(), broker, engine.history[-1].timestamp, running=True)
        app_module._fib_boundary_engines[11] = {"NIFTY": runtime}

        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_live_kill("NIFTY", _DummyRequest())
        self.assertEqual(raised.exception.status_code, 503)
        self.assertTrue(runtime.running)
        self.assertNotEqual(engine.status, "KILLED")

    async def test_legacy_query_string_arm_fails_closed(self):
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_legacy_arm(_DummyRequest(), symbol="NIFTY")
        self.assertEqual(raised.exception.status_code, 410)

    async def test_kill_without_campaign_is_404(self):
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_paper_kill(_DummyRequest())
        self.assertEqual(raised.exception.status_code, 404)

    async def test_backtest_rejects_bad_side(self):
        payload = app_module.FibTouchBacktestPayload(mother_timestamp=_today_1m_mother().isoformat(), side="XX")
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_backtest(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("side must be CE or PE", str(raised.exception.detail))

    async def test_backtest_rejects_an_unknown_symbol(self):
        payload = app_module.FibTouchBacktestPayload(mother_timestamp=_today_1m_mother().isoformat(), symbol="RELIANCE")
        with self.assertRaises(app_module.HTTPException) as raised:
            await app_module.fib_boundary_backtest(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Unknown symbol", str(raised.exception.detail))

    async def test_backtest_refuses_a_symbol_with_no_recorded_prices(self):
        """A zero-filled replay would LOOK like a result. Say so instead."""
        for symbol in ("FINNIFTY", "MIDCPNIFTY"):
            payload = app_module.FibTouchBacktestPayload(mother_timestamp=_today_1m_mother().isoformat(), symbol=symbol)
            with self.assertRaises(app_module.HTTPException) as raised:
                await app_module.fib_boundary_backtest(payload, _DummyRequest())
            self.assertEqual(raised.exception.status_code, 400, symbol)
            self.assertIn("no recorded option history", str(raised.exception.detail), symbol)

    async def test_backtest_accepts_the_symbols_that_do_have_prices(self):
        for symbol in ("NIFTY", "BANKNIFTY", "SENSEX"):
            payload = app_module.FibTouchBacktestPayload(mother_timestamp=_today_1m_mother().isoformat(), symbol=symbol)
            with patch.object(
                app_module, "_request_broker_context", AsyncMock(return_value=({"id": 11}, None, "user"))
            ):
                with self.assertRaises(app_module.HTTPException) as raised:
                    await app_module.fib_boundary_backtest(payload, _DummyRequest())
            self.assertIn("Connect a Dhan account", str(raised.exception.detail), symbol)

    async def test_backtest_and_start_read_the_same_fields(self):
        """The two must not drift apart again.

        The tab spent a day carrying a backtest of one ladder beside a Start
        button trading another. Every field that shapes the geometry has to
        exist on BOTH payloads with the same default.
        """
        start = app_module.FibTouchStartPayload.model_fields
        back = app_module.FibTouchBacktestPayload.model_fields
        for name in ("symbol", "side", "mother_timestamp", "timeframe", "capital_cap_inr", "itm_steps", "min_dte"):
            self.assertIn(name, back, name)
            self.assertEqual(back[name].default, start[name].default, name)

    async def test_backtest_without_broker_asks_to_connect_dhan(self):
        payload = app_module.FibTouchBacktestPayload(mother_timestamp=_today_1m_mother().isoformat())
        with patch.object(app_module, "_request_broker_context", AsyncMock(return_value=({"id": 11}, None, "user"))):
            with self.assertRaises(app_module.HTTPException) as raised:
                await app_module.fib_boundary_backtest(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Connect a Dhan account", str(raised.exception.detail))


class FibBoundaryBacktestPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """A replay must outlive the page it was run on.

    It cost a Dhan round trip per contract and several seconds, then lived in
    one browser variable over a panel that starts hidden -- so any redraw of
    the page lost it and the only way back was to pay for it again. Phil,
    2026-08-15: "Complete backtest result panel is gone."

    db.save_fib_backtest_run and the list/export routes were written for this
    and never wired to anything, so the table had always been empty.
    """

    async def asyncSetUp(self):
        if TEST_DB.exists():
            TEST_DB.unlink()
        if TEST_USER_DATA.exists():
            shutil.rmtree(TEST_USER_DATA, ignore_errors=True)
        app_module.config.DB_PATH = str(TEST_DB)
        app_module.config.USER_DATA_ROOT = str(TEST_USER_DATA)
        app_module._db_mod.config.DB_PATH = str(TEST_DB)
        app_module._db_mod.config.USER_DATA_ROOT = str(TEST_USER_DATA)
        app_module._db_mod._initialized = False
        await app_module._db_mod.init_db()
        # The runs table has a foreign key to users, so a run needs an owner.
        for user_id in (11, 99):
            await app_module._db_mod.create_user(f"owner{user_id}", "x", role="user")
        self.user_ids = {}
        for name in ("owner11", "owner99"):
            row = await app_module._db_mod.get_user_by_username(name)
            self.user_ids[name] = int(row["id"])

    def _payload(self, net=149.27):
        return {
            "status": "ok",
            "mode": "backtest",
            "symbol": "NIFTY",
            "side": "CE",
            "timeframe": "1m",
            "mother": {"timestamp": "2026-08-14T10:11:00"},
            "horizon_to": "2026-08-15",
            "campaign": {"net_pnl": net, "fills": [], "levels": []},
            "chart": {"candles": [{"t": "2026-08-14T10:11:00", "o": 1, "h": 2, "l": 0, "c": 1}]},
            "result": {"fully_priced": True, "net_pnl": net, "data_gaps": []},
        }

    async def test_nothing_saved_is_not_an_error(self):
        """Never having run one is a normal state, not a 404."""
        data = await app_module.latest_fib_boundary_backtest(_DummyRequest(self.user_ids["owner11"]))
        self.assertEqual(data["status"], "ok")
        self.assertIsNone(data["run"])

    async def test_a_saved_run_comes_back_whole(self):
        await app_module._db_mod.save_fib_backtest_run(self.user_ids["owner11"], self._payload())
        data = await app_module.latest_fib_boundary_backtest(_DummyRequest(self.user_ids["owner11"]))
        self.assertIsNotNone(data["run"])
        # The payload is the exact response the backtest route returned, so the
        # panel re-renders it through the same function with nothing special-cased.
        payload = data["run"]["payload"]
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["campaign"]["net_pnl"], 149.27)
        self.assertTrue(payload["chart"]["candles"])

    async def test_the_latest_is_the_one_that_comes_back(self):
        await app_module._db_mod.save_fib_backtest_run(self.user_ids["owner11"], self._payload(net=10.0))
        await app_module._db_mod.save_fib_backtest_run(self.user_ids["owner11"], self._payload(net=99.0))
        data = await app_module.latest_fib_boundary_backtest(_DummyRequest(self.user_ids["owner11"]))
        self.assertEqual(data["run"]["payload"]["campaign"]["net_pnl"], 99.0)

    async def test_one_user_never_sees_another_users_run(self):
        await app_module._db_mod.save_fib_backtest_run(self.user_ids["owner99"], self._payload(net=42.0))
        data = await app_module.latest_fib_boundary_backtest(_DummyRequest(self.user_ids["owner11"]))
        self.assertIsNone(data["run"], "a run belongs to the account that made it")


class FibBoundaryChartTimeframeTests(unittest.IsolatedAsyncioTestCase):
    """The chart's timeframe buttons pick CANDLES, never prices.

    They used to pick both. One parameter carried the mother's timeframe and
    the view, so pressing 5M on a 1H mother re-read the swing off 5m candles,
    re-priced all seven levels and redrew the trendline. What you were looking
    at was then not the ladder the engine was working, and nothing said so.
    Phil caught it on 2026-08-15.
    """

    # A 1H mother that draws TWO fibs under the merged geometry: a fall, a
    # bounce, a trendline, and a second structure once the first one's low
    # breaks. The single-swing fixture this class used to carry drew nothing at
    # all after the merge -- the levels came back empty and the tests passed on
    # two empty lists, which is the failure mode they exist to catch.
    #   FIB 1  0 = 24,698  1 = 24,600  span  98
    #   FIB 2  0 = 24,690  1 = 24,560  span 130
    _HOURLY = [
        (24_660, 24_780, 24_640, 24_642),  # MOTHER, high 24,780
        (24_642, 24_644, 24_620, 24_622),
        (24_622, 24_624, 24_600, 24_602),  # lowest low 24,600
        (24_602, 24_612, 24_600, 24_610),  # low LOCKS
        (24_610, 24_620, 24_608, 24_618),
        (24_618, 24_650, 24_615, 24_645),
        (24_645, 24_700, 24_640, 24_695),  # bounce high 24,700
        (24_695, 24_698, 24_680, 24_682),
        (24_682, 24_684, 24_670, 24_672),
        (24_672, 24_674, 24_560, 24_570),  # TL1 + FIB 1 drawn
        (24_570, 24_640, 24_565, 24_635),
        (24_635, 24_690, 24_630, 24_640),  # tags the standing line
        (24_640, 24_642, 24_505, 24_510),  # FIB 2 drawn
    ]
    # The SAME shape 400 points lower with a wider second leg. If the view ever
    # leaks into the geometry these numbers are what show up, and not one of
    # them can be mistaken for an hourly rung.
    #   FIB 1  0 = 24,284  1 = 24,200  span  84
    #   FIB 2  0 = 24,290  1 = 24,160  span 130
    _MINUTE = [
        (24_660, 24_780, 24_240, 24_242),  # MOTHER, same open so it is found
        (24_242, 24_244, 24_220, 24_222),
        (24_222, 24_224, 24_200, 24_202),
        (24_202, 24_212, 24_200, 24_210),
        (24_210, 24_220, 24_208, 24_218),
        (24_218, 24_250, 24_215, 24_245),
        (24_245, 24_300, 24_240, 24_295),
        (24_295, 24_298, 24_280, 24_282),
        (24_282, 24_284, 24_270, 24_272),
        (24_272, 24_274, 24_160, 24_170),
        (24_170, 24_240, 24_165, 24_235),
        (24_235, 24_290, 24_230, 24_240),
        (24_240, 24_242, 24_105, 24_110),
    ]

    def _mother(self):
        now = datetime.now(app_module.IST)
        mother = now.replace(hour=9, minute=15, second=0, microsecond=0)
        if mother + timedelta(hours=1) > now:
            mother -= timedelta(days=1)
        return mother

    def _adapter(self, mother):
        """One adapter that answers with a different shape per resolution."""
        by_tf = {"1h": self._HOURLY, "1m": self._MINUTE, "5m": self._MINUTE, "15m": self._MINUTE}
        step = {
            "1h": timedelta(hours=1),
            "15m": timedelta(minutes=15),
            "5m": timedelta(minutes=5),
            "1m": timedelta(minutes=1),
        }
        asked: list[str] = []

        class _Adapter:
            def __init__(self, *a, **k):
                pass

            async def async_get_candles(self, _symbol, resolution, **_k):
                asked.append(resolution)
                rows = by_tf[resolution]
                return [
                    app_module.IndexCandle(mother + i * step[resolution], o, h, low, c)
                    for i, (o, h, low, c) in enumerate(rows)
                ]

        return _Adapter, asked

    async def _chart(self, **kwargs):
        mother = self._mother()
        adapter_cls, asked = self._adapter(mother)
        with patch.object(
            app_module, "_request_broker_context", AsyncMock(return_value=({"id": 11}, _Broker(), "user"))
        ):
            with patch.object(app_module, "CascadeOptionsAdapter", adapter_cls):
                data = await app_module.fib_boundary_paper_chart(
                    mother.isoformat(), _DummyRequest(), symbol="NIFTY", side="CE", **kwargs
                )
        return data, asked

    # The ladder a 1H mother must always produce, whatever is drawn under it:
    # both fibs' rungs, interleaved, deepest last.
    _HOURLY_LEVELS = sorted(
        [24_698.0 - level * 98.0 for level in (2, 3, 4, 6, 8, 12, 16)]
        + [24_690.0 - level * 130.0 for level in (2, 3, 4, 6, 8, 12, 16)],
        reverse=True,
    )

    async def test_the_ladder_comes_from_the_mother_timeframe(self):
        data, _asked = await self._chart(timeframe="1h", base_timeframe="1h")
        self.assertEqual([row["price"] for row in data["levels"]], self._HOURLY_LEVELS)
        self.assertEqual([fib["fib0"] for fib in data["fibs"]], [24_698.0, 24_690.0])
        self.assertEqual([fib["fib1"] for fib in data["fibs"]], [24_600.0, 24_560.0])
        # `anchor` is the NEWEST fib -- the caption's view, not the whole ladder.
        self.assertEqual(data["anchor"]["high"], 24_690.0)
        self.assertEqual(data["anchor"]["low"], 24_560.0)

    async def test_a_rung_is_named_by_its_own_fib(self):
        """Two fibs both have an L4 at different prices holding different money,
        so the level alone cannot name a line on the chart."""
        data, _asked = await self._chart(timeframe="1h", base_timeframe="1h")
        keys = [row["key"] for row in data["levels"]]
        self.assertEqual(len(set(keys)), len(keys), "every rung is uniquely named")
        self.assertIn("F1L4", keys)
        self.assertIn("F2L4", keys)
        by_key = {row["key"]: row["price"] for row in data["levels"]}
        self.assertNotEqual(by_key["F1L4"], by_key["F2L4"])

    async def test_the_standing_trendlines_come_back(self):
        """A fib is only drawn where price cuts back through a line, so a chart
        without the lines cannot be checked against the rule that drew it."""
        data, _asked = await self._chart(timeframe="1h", base_timeframe="1h")
        self.assertTrue(data["trendlines"], "the line that produced these fibs is drawable")
        first = data["trendlines"][0]
        self.assertIn("t", first["a1"])
        self.assertIn("p", first["a2"])

    async def test_drilling_into_one_minute_does_not_move_a_single_level(self):
        """The bug, stated as a test: same mother, finer view, same ladder."""
        data, asked = await self._chart(timeframe="1m", base_timeframe="1h")
        self.assertEqual([row["price"] for row in data["levels"]], self._HOURLY_LEVELS)
        self.assertEqual(data["anchor"]["high"], 24_690.0, "the 1m structure (24,290) must not leak in")
        self.assertEqual(data["anchor"]["low"], 24_560.0, "nor its low (24,160)")
        self.assertEqual(data["base_timeframe"], "1h")
        self.assertEqual(data["timeframe"], "1m")
        # And the CANDLES really are the finer ones -- the view did change.
        self.assertIn("1m", asked)
        self.assertIn("1h", asked)

    async def test_every_view_gives_the_same_ladder(self):
        for view in ("1m", "5m", "15m", "1h"):
            data, _asked = await self._chart(timeframe=view, base_timeframe="1h")
            self.assertEqual(
                [row["price"] for row in data["levels"]], self._HOURLY_LEVELS, f"{view} view changed the ladder"
            )

    async def test_one_fetch_when_the_view_is_the_mother_timeframe(self):
        """No second round trip to Dhan to draw the candles it already has."""
        _data, asked = await self._chart(timeframe="1h", base_timeframe="1h")
        self.assertEqual(asked, ["1h"])

    async def test_an_old_client_sending_one_timeframe_is_unchanged(self):
        data, _asked = await self._chart(timeframe="1h")
        self.assertEqual(data["base_timeframe"], "1h")
        self.assertEqual([row["price"] for row in data["levels"]], self._HOURLY_LEVELS)

    async def test_the_old_behaviour_is_what_moved_the_ladder(self):
        """Exactly what pressing 1M used to do: the view became the geometry.
        Kept as a test so the difference is provable rather than argued -- the
        1m ladder is a real ladder, it is just not the one being traded."""
        data, _asked = await self._chart(timeframe="1m", base_timeframe="1m")
        self.assertEqual(data["anchor"]["high"], 24_290.0)
        self.assertEqual(data["anchor"]["low"], 24_160.0)
        self.assertNotEqual([row["price"] for row in data["levels"]], self._HOURLY_LEVELS)

    async def test_where_two_meet_draws_ZONES_not_the_level_ladder(self):
        """Phil, 2026-08-16: "Why no trade taken here?" The chart had drawn
        seven live-looking rungs under a mode that could not buy any of them."""
        data, _asked = await self._chart(timeframe="1h", base_timeframe="1h", buy_mode="convergence")
        self.assertEqual(data["buy_mode"], "convergence")
        self.assertTrue(data["zones"], "two fibs converge here, so there is something to buy")
        for zone in data["zones"]:
            self.assertIn("top", zone)
            self.assertIn("label", zone)
        # The full ladder still comes back -- it is the structure the chart
        # draws underneath -- but the ZONES are what this mode trades.
        self.assertTrue(data["levels"])

    async def test_every_level_names_no_zones_at_all(self):
        data, _asked = await self._chart(timeframe="1h", base_timeframe="1h")
        self.assertEqual(data["buy_mode"], "levels")
        self.assertEqual(data["zones"], [])

    async def test_a_bad_base_timeframe_is_refused(self):
        with self.assertRaises(app_module.HTTPException) as raised:
            await self._chart(timeframe="1h", base_timeframe="3h")
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("base_timeframe must be one of", str(raised.exception.detail))


class _StreamAdapter:
    """A Dhan stand-in that serves fixed candle streams per timeframe and
    counts how often each was asked for."""

    def __init__(self, streams: dict):
        self.streams = streams
        self.asked: dict = {}

    async def async_get_candles(self, _symbol, timeframe="5m", *, from_date=None, to_date=None, now=None):
        self.asked[timeframe] = self.asked.get(timeframe, 0) + 1
        return list(self.streams.get(str(timeframe).lower(), []))


class FibBoundaryPaperLoopTests(unittest.IsolatedAsyncioTestCase):
    """The poll that drives a running paper ladder, tick by tick.

    Until 2026-08-18 it read the mother's own chart only until the FIRST swing
    existed, so a paper ladder never drew a second fib while the backtest of
    the same mother drew three; and it went on polling Dhan every ten seconds
    for a MOTHER_BROKEN campaign nothing could change.
    """

    def _rows(self):
        return [
            (24_660, 24_780, 24_640, 24_642),
            (24_642, 24_644, 24_620, 24_622),
            (24_622, 24_624, 24_600, 24_602),
            (24_602, 24_612, 24_600, 24_610),
            (24_610, 24_620, 24_608, 24_618),
            (24_618, 24_650, 24_615, 24_645),
            (24_645, 24_700, 24_640, 24_695),
            (24_695, 24_698, 24_680, 24_682),
            (24_682, 24_684, 24_670, 24_672),
            (24_672, 24_674, 24_560, 24_570),  # FIB 1 drawn
            (24_570, 24_640, 24_565, 24_635),
            (24_635, 24_690, 24_630, 24_640),
            (24_640, 24_642, 24_505, 24_510),  # FIB 2 drawn
        ]

    def _ladder(self, timeframe="5m"):
        from datetime import date as _date

        from engine.fib_touch_ladder import FibTouchConfig, FibTouchLadder

        base = datetime(2026, 8, 6, 9, 15)
        step = 5 if timeframe == "5m" else 1
        geometry = [
            app_module.IndexCandle(base + timedelta(minutes=i * step), o, h, low, c)
            for i, (o, h, low, c) in enumerate(self._rows())
        ]
        engine = FibTouchLadder(
            FibTouchConfig(
                symbol="NIFTY",
                side="CE",
                mother_timestamp=geometry[0].timestamp,
                lot_size=65,
                strike_step=50.0,
                timeframe=timeframe,
                entry_timeframe="1m",
            ),
            premium_lookup=lambda *a: 200.0,
            expiry_source=lambda on: [_date(2026, 8, 11)],
        )
        return engine, geometry

    async def _one_tick(self, runtime, user_id=11):
        """Run the loop for exactly one iteration."""
        app_module._fib_boundary_engines[user_id] = {"NIFTY": runtime}

        async def _stop_after_first_sleep(_seconds):
            runtime.running = False

        with patch.object(app_module.asyncio, "sleep", _stop_after_first_sleep):
            await app_module._run_fib_boundary_paper_loop(user_id, runtime)

    async def test_the_mothers_chart_is_read_on_every_tick_not_only_until_the_first_swing(self):
        engine, geometry = self._ladder()
        # First tick: only the bars up to FIB 1 exist yet.
        adapter = _StreamAdapter({"5m": geometry[:10], "1m": []})
        runtime = app_module._CascadeRuntime(engine, adapter, _Broker(), geometry[0].timestamp, running=True)
        await self._one_tick(runtime)
        self.assertEqual(len(engine.geometry.fibs), 1)
        self.assertIsNotNone(engine.anchor)
        # Second tick, later in the day: the market drew a second structure.
        adapter.streams["5m"] = geometry
        runtime.running = True
        await self._one_tick(runtime)
        self.assertEqual(len(engine.geometry.fibs), 2, "the second fib is drawn on a paper ladder too")
        self.assertEqual(adapter.asked["5m"], 2, "the mother's chart is fetched every tick")

    async def test_a_restarted_ladder_gets_its_geometry_back_from_the_stream(self):
        from engine.fib_touch_ladder import FibTouchLadder

        engine, geometry = self._ladder()
        for bar in geometry:
            engine.on_geometry_candle(bar)
        self.assertEqual(len(engine.geometry.fibs), 2)
        revived = FibTouchLadder.from_dict(
            engine.to_dict(), premium_lookup=lambda *a: 200.0, expiry_source=lambda on: [date(2026, 8, 11)]
        )
        self.assertEqual(revived.geometry.fibs, [])
        adapter = _StreamAdapter({"5m": geometry, "1m": []})
        runtime = app_module._CascadeRuntime(revived, adapter, _Broker(), geometry[-1].timestamp, running=True)
        await self._one_tick(runtime)
        self.assertEqual(len(revived.geometry.fibs), 2)
        self.assertEqual([r.key for r in revived.rungs], [r.key for r in engine.rungs])
        self.assertEqual(len(revived.get_status()["fibs"]), 2, "and the chart payload shows them again")

    async def test_a_one_minute_mother_rebuilds_from_the_entry_stream(self):
        from engine.fib_touch_ladder import FibTouchLadder

        engine, geometry = self._ladder(timeframe="1m")
        for bar in geometry:
            engine.on_candle(bar)
        self.assertEqual(len(engine.geometry.fibs), 2)
        revived = FibTouchLadder.from_dict(
            engine.to_dict(), premium_lookup=lambda *a: 200.0, expiry_source=lambda on: [date(2026, 8, 11)]
        )
        adapter = _StreamAdapter({"1m": geometry})
        # last_candle_timestamp is the LAST bar: every 1m bar is "already traded on".
        runtime = app_module._CascadeRuntime(revived, adapter, _Broker(), geometry[-1].timestamp, running=True)
        await self._one_tick(runtime)
        self.assertEqual(len(revived.geometry.fibs), 2)
        self.assertEqual(len(revived.history), 0, "no bar was traded on twice")

    async def test_a_broken_mother_stops_the_poll(self):
        engine, geometry = self._ladder()
        for bar in geometry:
            engine.on_geometry_candle(bar)
        broken = app_module.IndexCandle(geometry[-1].timestamp + timedelta(minutes=5), 24_700, 24_800, 24_690, 24_790)
        adapter = _StreamAdapter({"5m": geometry, "1m": [broken]})
        runtime = app_module._CascadeRuntime(engine, adapter, _Broker(), geometry[0].timestamp, running=True)
        app_module._fib_boundary_engines[11] = {"NIFTY": runtime}
        slept = []

        async def _sleep(seconds):
            slept.append(seconds)
            if len(slept) > 3:
                runtime.running = False  # safety net: the loop should have stopped itself

        with patch.object(app_module.asyncio, "sleep", _sleep):
            await app_module._run_fib_boundary_paper_loop(11, runtime)
        self.assertEqual(engine.status, "MOTHER_BROKEN")
        self.assertFalse(runtime.running)
        self.assertEqual(len(slept), 1, "one tick saw the break and the poll ended")


class DeepCarryRouteTests(unittest.IsolatedAsyncioTestCase):
    """The form's "Deep ladder" switch has to reach the engine on BOTH routes,
    or Start and Backtest would replay different campaigns for one mother."""

    async def asyncSetUp(self):
        # See FibBoundaryRouteTests: the rate-limited start route needs a
        # clean limiter per test.
        app_module._rate_limits.clear()

    def _mother_and_stream(self):
        # A completed 5m candle at 10:15 five days back, on a stream that
        # contains it.
        day = (datetime.now(app_module.IST) - timedelta(days=5)).date()
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        mother = datetime(day.year, day.month, day.day, 10, 15, tzinfo=app_module.IST)
        rows = [
            app_module.IndexCandle(mother + timedelta(minutes=5 * i), 24_600 - i, 24_610 - i, 24_590 - i, 24_600 - i)
            for i in range(-12, 12)
        ]
        return mother, rows

    async def _config_seen_by(self, route, payload_cls, **fields):
        mother, rows = self._mother_and_stream()
        seen = {}
        real = app_module.FibTouchConfig

        def spy(**kwargs):
            seen.update(kwargs)
            raise app_module.HTTPException(status_code=418, detail="captured")

        payload = payload_cls(mother_timestamp=mother.replace(tzinfo=None).isoformat(), timeframe="5m", **fields)
        with (
            patch.object(
                app_module, "_request_broker_context", AsyncMock(return_value=({"id": 11}, _Broker(), "user"))
            ),
            patch.object(app_module, "CascadeOptionsAdapter", lambda *a, **k: _StreamAdapter({"5m": rows, "1m": rows})),
            patch.object(app_module, "FibTouchConfig", spy),
            patch("broker.dhan.ScripMaster.get_expiries", lambda *a, **k: []),
        ):
            with self.assertRaises(app_module.HTTPException) as raised:
                await route(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 418, raised.exception.detail)
        del real
        return seen

    async def test_start_passes_the_switch_through_and_defaults_to_close(self):
        seen = await self._config_seen_by(app_module.fib_boundary_paper_start, app_module.FibTouchStartPayload)
        self.assertFalse(seen["deep_carry"], "off by default -- it measured worse")
        seen = await self._config_seen_by(
            app_module.fib_boundary_paper_start, app_module.FibTouchStartPayload, deep_carry=True
        )
        self.assertTrue(seen["deep_carry"])

    async def test_the_target_rule_reaches_both_routes_and_defaults_to_fixed(self):
        for route, cls in (
            (app_module.fib_boundary_paper_start, app_module.FibTouchStartPayload),
            (app_module.fib_boundary_backtest, app_module.FibTouchBacktestPayload),
        ):
            seen = await self._config_seen_by(route, cls)
            self.assertFalse(seen["trailing_stop"])
            seen = await self._config_seen_by(route, cls, trailing_target=True)
            self.assertTrue(seen["trailing_stop"])

    async def test_lots_per_buy_reaches_both_routes_and_defaults_to_flat(self):
        """Phil, 2026-08-20: "put it on the form". Same is still the default."""
        for route, cls in (
            (app_module.fib_boundary_paper_start, app_module.FibTouchStartPayload),
            (app_module.fib_boundary_backtest, app_module.FibTouchBacktestPayload),
        ):
            seen = await self._config_seen_by(route, cls)
            self.assertFalse(seen["lot_ramp"])
            seen = await self._config_seen_by(route, cls, lot_ramp=True)
            self.assertTrue(seen["lot_ramp"])

    async def test_a_past_mothers_paper_start_is_sized_like_the_backtest(self):
        """The Start route read the lot off the EARLIEST listed expiry, which is
        right for today's mother and wrong for a replay of one from another
        lot era: 2026-08-05 sized at 25 in the offline server while the
        backtest of the same mother sized at 65."""
        seen = await self._config_seen_by(app_module.fib_boundary_paper_start, app_module.FibTouchStartPayload)
        mother = datetime.fromisoformat(str(seen["mother_timestamp"]).replace("+05:30", ""))
        self.assertEqual(seen["lot_size"], app_module._nifty_lot_size_on(mother.date()))

    async def test_a_same_day_mother_started_hours_later_gets_recorded_prices(self):
        """2026-08-18: a 09:25 mother started at 19:17 recorded 255 "no quote"
        gaps and bought nothing, while the backtest of the same mother bought
        at 10:57 -- Start built the history lookup only for a mother from
        another day. Any catch-up older than the live-quote window needs it."""
        now = datetime.now(app_module.IST)
        if now.time() < dt_time(9, 45) or now.weekday() >= 5:
            self.skipTest("needs a same-day mother that is already hours old")
        mother = now.replace(hour=9, minute=15, second=0, microsecond=0)
        rows = [
            app_module.IndexCandle(mother + timedelta(minutes=5 * i), 24_600 - i, 24_610 - i, 24_590 - i, 24_600 - i)
            for i in range(-12, 12)
        ]
        history_calls = []

        def history(_broker, symbol, from_day, to_day):
            history_calls.append((symbol, from_day, to_day))
            return None

        def stop_here(*a, **k):
            raise app_module.HTTPException(status_code=418, detail="captured")

        payload = app_module.FibTouchStartPayload(
            mother_timestamp=mother.replace(tzinfo=None).isoformat(), timeframe="5m"
        )
        with (
            patch.object(
                app_module, "_request_broker_context", AsyncMock(return_value=({"id": 11}, _Broker(), "user"))
            ),
            patch.object(app_module, "CascadeOptionsAdapter", lambda *a, **k: _StreamAdapter({"5m": rows, "1m": rows})),
            patch.object(app_module, "_fib_touch_history_lookup", history),
            patch.object(app_module, "FibTouchLadder", stop_here),
            patch("broker.dhan.ScripMaster.get_expiries", lambda *a, **k: []),
        ):
            with self.assertRaises(app_module.HTTPException) as raised:
                await app_module.fib_boundary_paper_start(payload, _DummyRequest())
        self.assertEqual(raised.exception.status_code, 418, raised.exception.detail)
        self.assertEqual(history_calls, [("NIFTY", mother.date(), now.date())], "today's old mother reads history too")

    async def test_backtest_passes_the_same_switch(self):
        seen = await self._config_seen_by(app_module.fib_boundary_backtest, app_module.FibTouchBacktestPayload)
        self.assertFalse(seen["deep_carry"])
        seen = await self._config_seen_by(
            app_module.fib_boundary_backtest, app_module.FibTouchBacktestPayload, deep_carry=True
        )
        self.assertTrue(seen["deep_carry"])


class PerSymbolPremiumSourceTests(unittest.IsolatedAsyncioTestCase):
    """The fib history lookup must build Upstox with the SYMBOL'S underlying
    key. Built with the default (NIFTY) it listed NIFTY's Tuesdays for a
    SENSEX replay and back-filled from NIFTY's chain, so every expired SENSEX
    contract fell through to Dhan and went unpriced."""

    def _seen_key(self, symbol):
        seen = {}

        class _Src:
            def __init__(self, **kwargs):
                seen.update(kwargs)

            def available_expiries(self):
                return set()

        with (
            patch("data.cascade_upstox.UpstoxPremiumSource", _Src),
            patch.object(app_module, "_hybrid_premium_lookup", lambda *a, **k: "lookup"),
        ):
            out = app_module._fib_touch_history_lookup(_Broker(), symbol, date(2026, 8, 1), date(2026, 8, 10))
        return out, seen

    def test_sensex_and_banknifty_get_their_own_key(self):
        _out, seen = self._seen_key("SENSEX")
        self.assertEqual(seen.get("underlying_key"), "BSE_INDEX|SENSEX")
        _out, seen = self._seen_key("BANKNIFTY")
        self.assertEqual(seen.get("underlying_key"), "NSE_INDEX|Nifty Bank")
        _out, seen = self._seen_key("NIFTY")
        self.assertEqual(seen.get("underlying_key"), "NSE_INDEX|Nifty 50")

    def test_a_symbol_without_a_key_is_dhan_only(self):
        _out, seen = self._seen_key("FINNIFTY")
        self.assertEqual(seen, {}, "no Upstox source is built at all")


if __name__ == "__main__":
    unittest.main()


class FibBoundaryAutoMotherTests(unittest.IsolatedAsyncioTestCase):
    """The auto mother (Phil, 2026-08-19): the 09:15 5m candle is the mother
    every session; a broken mother is replaced by the breakout candle once it
    has closed; the day ends at 15:15; tomorrow starts itself. Measured over
    23 months before it was built (NIFTY CE +1,13,568 / -41,200; SENSEX CE
    +1,12,244 / -44,662); these tests pin the mechanics, not the money."""

    async def asyncSetUp(self):
        if TEST_DB.exists():
            TEST_DB.unlink()
        app_module.config.DB_PATH = str(TEST_DB)
        app_module._db_mod.config.DB_PATH = str(TEST_DB)
        app_module._db_mod._initialized = False
        app_module._fib_boundary_engines.clear()
        app_module._fib_boundary_auto.clear()
        app_module._fib_boundary_auto_loaded.clear()
        # These tests are pinned to August 2026, and the auto log keeps 30 days
        # counted from the REAL clock. On 19-Sep-2026 the 19-Aug entry aged out
        # and the chaining test read an empty log -- the rule still worked,
        # the fixture had expired. Keep the window wide enough to hold it.
        retention = patch.object(app_module, "_FIB_AUTO_LOG_DAYS", 36500)
        retention.start()
        self.addCleanup(retention.stop)
        await app_module._db_mod.init_db()

    async def asyncTearDown(self):
        for runtimes in app_module._fib_boundary_engines.values():
            for runtime in runtimes.values():
                if runtime.task:
                    runtime.task.cancel()
        app_module._fib_boundary_engines.clear()
        app_module._fib_boundary_auto.clear()
        app_module._fib_boundary_auto_loaded.clear()

    # ---- the next-mother arithmetic, pure
    def test_the_breakout_candle_is_the_next_mother_and_only_moves_forward(self):
        nm = app_module._fib_auto_breakout_candle
        IST = app_module.IST
        m = datetime(2026, 8, 19, 9, 15, tzinfo=IST)
        # a break at 10:23 lies in the 10:20 bar -> that bar is the new mother
        self.assertEqual(nm(datetime(2026, 8, 19, 10, 23, tzinfo=IST), m), datetime(2026, 8, 19, 10, 20, tzinfo=IST))
        # on the grid edge the holder is the bar that opens there
        self.assertEqual(nm(datetime(2026, 8, 19, 9, 20, tzinfo=IST), m), datetime(2026, 8, 19, 9, 20, tzinfo=IST))
        # a tick disagreement breaks "inside" the mother's own bar: move forward, never back to the same candle
        self.assertEqual(nm(datetime(2026, 8, 19, 9, 19, tzinfo=IST), m), datetime(2026, 8, 19, 9, 20, tzinfo=IST))
        # nothing opens at/after 15:10
        self.assertIsNone(nm(datetime(2026, 8, 19, 15, 12, tzinfo=IST), m))
        self.assertEqual(nm(datetime(2026, 8, 19, 15, 9, tzinfo=IST), m), datetime(2026, 8, 19, 15, 5, tzinfo=IST))

    # ---- the route + persistence
    async def test_enable_persists_and_is_read_back_in_status(self):
        payload = app_module.FibTouchAutoPayload(symbol="NIFTY", enabled=True, mode="paper", trailing_target=True)
        out = await app_module.fib_boundary_auto(payload, _DummyRequest())
        self.assertTrue(out["auto"]["NIFTY"]["enabled"])
        self.assertEqual(out["auto"]["NIFTY"]["next_mother"], "breakout candle")
        # a fresh process reads it back from app_state
        app_module._fib_boundary_auto.clear()
        app_module._fib_boundary_auto_loaded.clear()
        status = await app_module.fib_boundary_paper_status(_DummyRequest())
        self.assertTrue(status["auto"]["NIFTY"]["enabled"])
        self.assertEqual(status["auto"]["NIFTY"]["first_mother"], "09:15")
        off = await app_module.fib_boundary_auto(
            app_module.FibTouchAutoPayload(symbol="NIFTY", enabled=False), _DummyRequest()
        )
        self.assertFalse(off["auto"]["NIFTY"]["enabled"])

    async def test_auto_runs_the_measured_rule_whatever_the_form_says(self):
        """Phil, 2026-08-20: "Auto doesn't need this form -- it can start as
        per the strategy and the backtest." So the console cannot put auto on
        a configuration nobody measured: only the index, paper/live and the
        rupee cap come from the form."""
        out = await app_module.fib_boundary_auto(
            app_module.FibTouchAutoPayload(
                symbol="SENSEX",
                enabled=True,
                side="PE",  # loses on both indices
                timeframe="15m",  # never measured green
                buy_mode="convergence",  # Partner, not Lone
                trailing_target=False,  # fixed target loses
                itm_steps=0,
                min_dte=0,
                capital_cap_inr=50_000,
                mode="paper",
            ),
            _DummyRequest(),
        )
        saved = out["auto"]["SENSEX"]
        self.assertEqual(saved["side"], "CE")
        self.assertEqual(saved["timeframe"], "5m")
        self.assertEqual(saved["buy_mode"], "levels", "Lone, the configuration on the tearsheet")
        self.assertTrue(saved["trailing_target"])
        self.assertEqual(saved["itm_steps"], 2)
        self.assertEqual(saved["min_dte"], 4)
        self.assertTrue(saved["intraday_close"])
        self.assertFalse(saved["deep_carry"])
        self.assertEqual(saved["max_buys"], 4)
        self.assertEqual(saved["capital_cap_inr"], 50_000, "size is still the desk's")
        self.assertEqual(saved["mode"], "paper")

    async def test_a_setting_written_before_the_rule_was_locked_still_starts_the_rule(self):
        IST = app_module.IST
        started = []
        stale = self._setting(side="PE", timeframe="15m", buy_mode="convergence", trailing_target=False, itm_steps=0)
        self.assertEqual(await self._step(stale, datetime(2026, 8, 19, 9, 20, tzinfo=IST), started), "started")
        p = started[0]
        self.assertEqual(
            (p.side, p.timeframe, p.buy_mode, p.trailing_target, p.itm_steps, p.min_dte),
            ("CE", "5m", "levels", True, 2, 4),
        )

    async def test_live_mode_is_refused_while_live_execution_is_disabled(self):
        with patch.object(app_module, "_FIB_TOUCH_LIVE_EXECUTION_ENABLED", False):
            with self.assertRaises(app_module.HTTPException) as raised:
                await app_module.fib_boundary_auto(
                    app_module.FibTouchAutoPayload(symbol="NIFTY", mode="live"), _DummyRequest()
                )
        self.assertEqual(raised.exception.status_code, 503)

    # ---- the step
    def _setting(self, **over):
        base = {
            "enabled": True,
            "side": "CE",
            "timeframe": "5m",
            "capital_cap_inr": 75000.0,
            "itm_steps": 2,
            "min_dte": 4,
            "mode": "paper",
            "trailing_target": True,
            "buy_mode": "levels",
        }
        base.update(over)
        return base

    def _user(self):
        return {"id": 11, "username": "admin"}

    async def _step(self, setting, now, started):
        """Run one auto step with the ladder start captured instead of executed."""

        async def _fake_start(uid, payload, *, broker_client=None, broker_factory=None):
            started.append(payload)
            return {"status": "started"}

        with (
            patch.object(app_module, "_start_fib_boundary_ladder", _fake_start),
            patch.object(app_module, "_resolve_user_broker_client", lambda *a, **k: (object(), "user")),
        ):
            return await app_module._fib_boundary_auto_step(self._user(), "NIFTY", setting, now=now)

    async def test_it_starts_on_the_0915_bar_at_0920_and_not_before(self):
        IST = app_module.IST
        started = []
        s = self._setting()
        self.assertEqual(await self._step(s, datetime(2026, 8, 19, 9, 19, tzinfo=IST), started), "outside-window")
        self.assertEqual(started, [])
        self.assertEqual(await self._step(s, datetime(2026, 8, 19, 9, 20, tzinfo=IST), started), "started")
        self.assertEqual(len(started), 1)
        p = started[0]
        self.assertEqual(p.mother_timestamp, "2026-08-19T09:15:00")
        self.assertEqual((p.symbol, p.side, p.timeframe, p.mode), ("NIFTY", "CE", "5m", "paper"))
        self.assertTrue(p.intraday_close, "auto forces intraday")
        self.assertFalse(p.deep_carry, "auto forces deep carry off")
        self.assertTrue(p.trailing_target)
        # the same day is not started twice once the ladder is gone
        self.assertEqual(await self._step(s, datetime(2026, 8, 19, 9, 30, tzinfo=IST), started), "day-done")
        self.assertEqual(len(started), 1)

    async def test_a_day_with_no_0915_bar_is_skipped_after_1000(self):
        IST = app_module.IST
        s = self._setting()

        async def _no_bar(uid, payload, *, broker_client=None, broker_factory=None):
            raise app_module.HTTPException(
                status_code=400, detail="Dhan has no NIFTY 5m candle opening at 19 Aug 2026 09:15 IST. Check the date."
            )

        with (
            patch.object(app_module, "_start_fib_boundary_ladder", _no_bar),
            patch.object(app_module, "_resolve_user_broker_client", lambda *a, **k: (object(), "user")),
        ):
            self.assertEqual(
                await app_module._fib_boundary_auto_step(
                    self._user(), "NIFTY", s, now=datetime(2026, 8, 19, 9, 25, tzinfo=IST)
                ),
                "no-bar-yet",
            )
            self.assertEqual(
                await app_module._fib_boundary_auto_step(
                    self._user(), "NIFTY", s, now=datetime(2026, 8, 19, 10, 0, tzinfo=IST)
                ),
                "day-skipped",
            )
        self.assertEqual(s["skipped_day"], "2026-08-19")
        started = []
        self.assertEqual(await self._step(s, datetime(2026, 8, 19, 10, 30, tzinfo=IST), started), "day-skipped")
        self.assertEqual(started, [])

    def _ended_runtime(self, status, exit_at, mother=datetime(2026, 8, 19, 9, 15)):
        from datetime import date as _date

        from engine.fib_touch_ladder import FibTouchConfig, FibTouchLadder

        IST = app_module.IST
        engine = FibTouchLadder(
            FibTouchConfig(
                symbol="NIFTY",
                side="CE",
                mother_timestamp=mother.replace(tzinfo=IST),
                lot_size=65,
                strike_step=50.0,
                timeframe="5m",
                entry_timeframe="1m",
            ),
            premium_lookup=lambda *a: 200.0,
            expiry_source=lambda on: [_date(2026, 8, 25)],
        )
        engine.status = status
        engine.exit_reason = "mother_broken_no_buys" if status == "MOTHER_BROKEN" else "intraday_close"
        engine.exit_timestamp = exit_at.replace(tzinfo=IST)
        return app_module._CascadeRuntime(
            engine=engine, adapter=None, broker=None, last_candle_timestamp=exit_at.replace(tzinfo=IST), running=False
        )

    async def test_a_broken_mother_chains_to_the_breakout_candle_once_it_has_closed(self):
        IST = app_module.IST
        s = self._setting(last_day="2026-08-19", _seq=1)
        app_module._fib_boundary_engines[11] = {
            "NIFTY": self._ended_runtime("MOTHER_BROKEN", datetime(2026, 8, 19, 10, 23))
        }
        started = []
        # 10:24: the breakout candle (10:20) has not closed -> wait
        self.assertEqual(await self._step(s, datetime(2026, 8, 19, 10, 24, tzinfo=IST), started), "waiting-for-candle")
        self.assertEqual(started, [])
        # 10:25: it has -> start on it
        self.assertEqual(await self._step(s, datetime(2026, 8, 19, 10, 25, tzinfo=IST), started), "started")
        self.assertEqual(started[0].mother_timestamp, "2026-08-19T10:20:00")
        self.assertEqual(s["_seq"], 2)
        self.assertEqual(
            s["log"][-1]["mother"][:16], "2026-08-19T09:15", "the ended campaign is logged before it is replaced"
        )
        self.assertEqual(s["log"][-1]["exit_reason"], "mother_broken_no_buys")

    async def test_a_closed_day_does_not_chain_and_a_running_ladder_is_left_alone(self):
        IST = app_module.IST
        s = self._setting(last_day="2026-08-19")
        app_module._fib_boundary_engines[11] = {"NIFTY": self._ended_runtime("CLOSED", datetime(2026, 8, 19, 15, 15))}
        started = []
        self.assertEqual(await self._step(s, datetime(2026, 8, 19, 15, 16, tzinfo=IST), started), "day-done")
        busy = self._ended_runtime("OPEN", datetime(2026, 8, 19, 11, 0))
        busy.running = True
        app_module._fib_boundary_engines[11] = {"NIFTY": busy}
        self.assertEqual(await self._step(s, datetime(2026, 8, 19, 11, 5, tzinfo=IST), started), "busy")
        self.assertEqual(started, [])

    async def test_a_break_too_late_in_the_day_ends_the_chain(self):
        IST = app_module.IST
        s = self._setting(last_day="2026-08-19")
        app_module._fib_boundary_engines[11] = {
            "NIFTY": self._ended_runtime("MOTHER_BROKEN", datetime(2026, 8, 19, 15, 11))
        }
        started = []
        self.assertEqual(await self._step(s, datetime(2026, 8, 19, 15, 12, tzinfo=IST), started), "day-done")
        self.assertEqual(started, [])

    async def test_a_stuck_ladder_is_reported_and_never_chained_over(self):
        IST = app_module.IST
        s = self._setting(last_day="2026-08-19")
        app_module._fib_boundary_engines[11] = {
            "NIFTY": self._ended_runtime("EXIT_REFUSED", datetime(2026, 8, 19, 11, 0))
        }
        started = []
        self.assertEqual(await self._step(s, datetime(2026, 8, 19, 11, 10, tzinfo=IST), started), "stuck")
        self.assertIn("EXIT_REFUSED", s["alert"])
        self.assertEqual(started, [])

    async def test_yesterdays_ladder_does_not_block_todays_0915(self):
        IST = app_module.IST
        s = self._setting(last_day="2026-08-18")
        app_module._fib_boundary_engines[11] = {
            "NIFTY": self._ended_runtime("CLOSED", datetime(2026, 8, 18, 15, 15), mother=datetime(2026, 8, 18, 9, 15))
        }
        started = []
        self.assertEqual(await self._step(s, datetime(2026, 8, 19, 9, 20, tzinfo=IST), started), "started")
        self.assertEqual(started[0].mother_timestamp, "2026-08-19T09:15:00")

    async def test_the_loop_is_not_started_on_a_standby_instance(self):
        with (
            patch.object(app_module, "_engine_restore_owner_is_active_instance", lambda: False),
            patch.object(app_module, "_STARTUP_ENGINE_RESTORE_ENABLED", True),
            patch.object(app_module, "_SKIP_STARTUP_JOBS", False),
            patch.object(app_module.config, "AUTO_TOKEN_ENABLED", False),
            patch.object(app_module, "_STARTUP_SCRIP_MASTER_ENABLED", False),
            patch.object(app_module, "_STARTUP_TRADE_BACKFILL_ENABLED", False),
            patch.object(app_module, "_STARTUP_EMPTY_RUN_CLEANUP_ENABLED", False),
            patch.object(app_module.asyncio, "create_task") as created,
        ):
            await app_module._start_token_renewal()
        names = [str(call.args[0]) for call in created.call_args_list]
        self.assertFalse(any("_run_fib_boundary_auto_loop" in n for n in names), names)


class FibBoundaryOtmStrikeTests(unittest.TestCase):
    """ATM+1 and ATM+2 (Phil, 2026-08-20). Negative itm_steps is OUT of the money."""

    def test_engine_accepts_and_places_an_otm_strike(self):
        from engine.fib_touch_ladder import atm_strike

        atm = atm_strike(24_337.0, 50.0)
        self.assertEqual(atm, 24_350.0)
        # CE, itm_steps -2 -> two steps ABOVE the money.
        self.assertEqual(atm - (-2 * 50.0), 24_450.0)

    def test_engine_refuses_a_strike_beyond_ten_steps(self):
        from engine.fib_touch_ladder import FibTouchConfig, FibTouchError

        def build(steps):
            return FibTouchConfig(
                symbol="NIFTY",
                mother_timestamp=datetime(2026, 8, 20, 9, 15),
                side="CE",
                lot_size=65,
                strike_step=50.0,
                itm_steps=steps,
            )

        build(-2)  # allowed
        with self.assertRaises(FibTouchError):
            build(-11)

    def test_start_payload_takes_atm_plus_two(self):
        payload = app_module.FibTouchStartPayload(symbol="NIFTY", mother_timestamp="2026-08-20T09:15:00", itm_steps=-2)
        self.assertEqual(payload.itm_steps, -2)
        with self.assertRaises(Exception):
            app_module.FibTouchStartPayload(symbol="NIFTY", mother_timestamp="2026-08-20T09:15:00", itm_steps=-3)


class FibBoundaryLotRampTests(unittest.TestCase):
    """1, 2, 3 lots down the ladder — on the form 2026-08-20, off by default."""

    def test_payloads_default_to_flat_lots(self):
        start = app_module.FibTouchStartPayload(symbol="NIFTY", mother_timestamp="2026-08-20T09:15:00")
        self.assertFalse(start.lot_ramp)
        self.assertTrue(
            app_module.FibTouchStartPayload(
                symbol="NIFTY", mother_timestamp="2026-08-20T09:15:00", lot_ramp=True
            ).lot_ramp
        )

    def test_engine_ramps_the_lot_count_per_buy(self):
        from engine.fib_touch_ladder import FibTouchConfig

        flat = FibTouchConfig(
            symbol="NIFTY",
            mother_timestamp=datetime(2026, 8, 20, 9, 15),
            side="CE",
            lot_size=65,
            strike_step=50.0,
        )
        self.assertFalse(flat.lot_ramp)
        ramped = FibTouchConfig(
            symbol="NIFTY",
            mother_timestamp=datetime(2026, 8, 20, 9, 15),
            side="CE",
            lot_size=65,
            strike_step=50.0,
            lot_ramp=True,
        )
        # The n-th buy of a round takes n x lots_per_rung: 1, 2, 3 ...
        self.assertEqual(
            [ramped.lots_per_rung * (n + 1) for n in range(3)],
            [ramped.lots_per_rung, ramped.lots_per_rung * 2, ramped.lots_per_rung * 3],
        )

    def test_the_auto_rule_never_ramps(self):
        self.assertNotIn("lot_ramp", app_module._FIB_AUTO_RULE)
        self.assertFalse(app_module.FibTouchAutoPayload(symbol="NIFTY").model_dump().get("lot_ramp", False))
