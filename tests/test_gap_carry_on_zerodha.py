"""Gap Carry can send its live orders to Zerodha.

Phil, 2026-10-11: "Need that all for zerodha as well" -- of the four he named,
Gap Carry is the only one that trades live, so it is the one wired. Two things
are different at Zerodha and both are decisions, not accidents:

  * NO STOP RESTS THERE. Dhan's Super Order carries the 30% net inside the
    entry and retires it with the position. Kite has no such order; a GTT is
    not tied to the holding and one left behind would sell an option no longer
    held. Gap Carry only buys, so the premium is the most a night can lose.
  * THE DAY'S LOGIN IS NEEDED. Kite's session lapses at 06:00. An order that
    could not be sent for want of one is "nothing sent" -- retried after the
    login -- never an unknown outcome, which would freeze a carry on the very
    morning it has to be sold.

The Dhan path must not move: a broker that says nothing about itself is Dhan.
"""

import asyncio
import json
import os
import sys
import unittest
from datetime import date, datetime
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
from broker import zerodha  # noqa: E402
from engine import gap_carry_paper  # noqa: E402
from engine.options_live_executor import ExecutionRefused, OptionsLiveExecutor  # noqa: E402

IST = app_module.IST
WHEN = datetime(2026, 10, 12, 15, 10)
EXPIRY = date(2026, 10, 13)
TODAY_TOKEN = datetime.now(IST).isoformat()
STALE_TOKEN = "2026-09-18T08:00:00+05:30"


class _Broker:
    """Records what it is asked. `kind` decides whether it can bracket."""

    def __init__(self, kind="dhan", *, no_session=False):
        self.calls = []
        self.no_session = no_session
        if kind == "zerodha":
            self.broker_name = "Zerodha"
            self.supports_super_order = False

    def _order(self, name, kw):
        if self.no_session:
            raise zerodha.ZerodhaSessionError("Zerodha session has expired.")
        self.calls.append((name, kw))
        return {"orderId": f"{name}-1", "orderStatus": "TRANSIT"}

    def place_super_order(self, **kw):
        return self._order("super", kw)

    def place_option_order(self, **kw):
        return self._order("plain", kw)

    def get_super_orders(self):
        return [{"orderId": "super-1", "orderStatus": "TRADED", "filledQty": 65, "averageTradedPrice": 201.0}]

    def verify_order_fill(self, order_id, max_wait_sec=20):
        return {"status": "FILLED", "filled_qty": 65, "avg_price": 200.5}


def _executor(broker):
    executor = OptionsLiveExecutor(broker, "NIFTY", armed=True, product_type="MARGIN", tag="PF_GAP_CARRY")
    executor.verify_wait_sec = 1
    return executor


def _buy(executor, stop=60.0):
    return executor.buy(
        when=WHEN, strike=22700, expiry=EXPIRY, option_type="PE", quantity=65, premium=200.0, stop_price=stop
    )


class AtZerodhaTheBuyCarriesNoStop(unittest.TestCase):
    def test_a_plain_buy_goes_out_and_the_receipt_says_no_stop_rests(self):
        broker = _Broker("zerodha")
        receipt = _buy(_executor(broker))
        self.assertEqual([name for name, _ in broker.calls], ["plain"])
        self.assertEqual(broker.calls[0][1]["transaction_type"], "BUY")
        self.assertEqual(broker.calls[0][1]["product_type"], "MARGIN")
        self.assertNotIn("bracket_order_id", receipt)
        self.assertEqual(receipt["traded_premium"], 200.5)
        self.assertIn("no stop rests at Zerodha", receipt["note"])

    def test_dhan_still_gets_the_super_order_with_its_stop(self):
        broker = _Broker("dhan")
        receipt = _buy(_executor(broker))
        self.assertEqual([name for name, _ in broker.calls], ["super"])
        self.assertEqual(broker.calls[0][1]["stop_loss_price"], 60.0)
        self.assertEqual(receipt["bracket_order_id"], "super-1")
        self.assertNotIn("note", receipt)

    def test_the_real_client_says_it_cannot_bracket(self):
        client = zerodha.ZerodhaClient("key", "tok")
        self.assertFalse(client.supports_super_order)
        self.assertFalse(_executor(client).brackets)
        self.assertEqual(_executor(client).broker_label, "Zerodha")
        self.assertEqual(_executor(object()).broker_label, "Dhan")
        self.assertTrue(_executor(object()).brackets)


class WithNoSessionNothingWasSent(unittest.TestCase):
    def test_the_client_raises_the_session_error_before_any_request(self):
        with self.assertRaises(zerodha.ZerodhaSessionError) as caught:
            zerodha.ZerodhaClient("key", "")._get_data("/orders")
        self.assertTrue(caught.exception.nothing_sent)
        self.assertIsInstance(caught.exception, ConnectionError)  # older handlers still catch it

    def test_a_buy_is_refused_not_unknown(self):
        with self.assertRaises(ExecutionRefused) as caught:
            _buy(_executor(_Broker("zerodha", no_session=True)))
        self.assertIn("Zerodha took no order", str(caught.exception))

    def test_a_sell_answers_not_sent(self):
        receipt = _executor(_Broker("zerodha", no_session=True)).sell(
            when=WHEN, strike=22700, expiry=EXPIRY, option_type="PE", quantity=65
        )
        self.assertEqual(receipt["status"], "NOT_SENT")

    def test_a_flat_no_from_kite_is_a_refusal_too(self):
        """Insufficient funds answers 400: no order exists, so nothing to freeze over."""
        from broker.dhan import DhanOrderError
        from engine.options_live_executor import OrderRejected

        class Refuses(_Broker):
            def place_option_order(self, **kw):
                error = DhanOrderError("Order placement", 400, "Insufficient funds", {})
                error.rejected = True
                raise error

        with self.assertRaises(OrderRejected) as caught:
            _buy(_executor(Refuses("zerodha")))
        self.assertIn("Zerodha refused the buy", str(caught.exception))
        receipt = _executor(Refuses("zerodha")).sell(
            when=WHEN, strike=22700, expiry=EXPIRY, option_type="PE", quantity=65
        )
        self.assertEqual(receipt["status"], "REJECTED")

    def test_the_real_client_marks_a_flat_no_and_nothing_else(self):
        from broker.dhan import AmbiguousOrderSubmission, DhanOrderError

        class Http:
            def __init__(self, status, body):
                self.status, self.body = status, body

            def request(self, *_a, **_k):
                return SimpleNamespace(status_code=self.status, json=lambda: self.body, text=str(self.body))

        info = {"tradingsymbol": "NIFTY26OCT22700PE", "exchange": "NFO", "tick_size": 0.05}

        def place(status, body):
            client = zerodha.ZerodhaClient("key", "tok", session=Http(status, body))
            return client._place(info, "BUY", 65, "MARKET", "MARGIN", 0, 0, "PF_GAP_CARRY_BUY")

        with self.assertRaises(DhanOrderError) as caught:
            place(400, {"status": "error", "error_type": "MarginException", "message": "Insufficient funds"})
        self.assertTrue(getattr(caught.exception, "rejected", False))
        with self.assertRaises(AmbiguousOrderSubmission) as unknown:
            place(503, {"status": "error", "message": "gateway"})
        self.assertFalse(getattr(unknown.exception, "rejected", False))

    def test_any_other_failure_is_still_unknown(self):
        class Dies(_Broker):
            def place_option_order(self, **kw):
                raise TimeoutError("read timed out")

        with self.assertRaises(TimeoutError):
            _buy(_executor(Dies("zerodha")))


def _position(order_id="Z-1"):
    return gap_carry_paper.GapCarryPosition(
        session=date(2026, 10, 12),
        side="PE",
        strike=22700,
        expiry=EXPIRY,
        lot_size=65,
        lots=1,
        signal=None,
        entry_timestamp=WHEN.replace(tzinfo=IST),
        entry_spot=22750.0,
        entry_premium=200.0,
        order_id=order_id,
        bracket_order_id=None,
    )


class TheEngineKnowsItsBroker(unittest.TestCase):
    def test_the_saved_campaign_says_through_whom(self):
        on_zerodha = gap_carry_paper.GapCarryPaper(
            config=gap_carry_paper.GapCarryConfig(), executor=_executor(_Broker("zerodha"))
        )
        on_dhan = gap_carry_paper.GapCarryPaper(
            config=gap_carry_paper.GapCarryConfig(), executor=_executor(_Broker("dhan"))
        )
        paper = gap_carry_paper.GapCarryPaper(config=gap_carry_paper.GapCarryConfig())
        self.assertEqual((on_zerodha.to_dict()["broker"], on_zerodha.get_status()["broker"]), ("zerodha", "zerodha"))
        self.assertEqual(on_dhan.to_dict()["broker"], "dhan")
        self.assertEqual(paper.to_dict()["broker"], "")

    def test_an_unsent_exit_keeps_the_leg_and_does_not_freeze(self):
        engine = gap_carry_paper.GapCarryPaper(
            config=gap_carry_paper.GapCarryConfig(), executor=_executor(_Broker("zerodha", no_session=True))
        )
        pos = _position()
        for _ in range(3):  # every two seconds of the exit window
            self.assertIsNone(engine._sell_for_real(pos, datetime(2026, 10, 13, 9, 20)))
        self.assertFalse(engine.frozen_reason)
        self.assertEqual(sum("exit NOT sent" in note for note in engine.notes), 1)

    def test_after_the_login_the_same_leg_sells(self):
        broker = _Broker("zerodha", no_session=True)
        engine = gap_carry_paper.GapCarryPaper(config=gap_carry_paper.GapCarryConfig(), executor=_executor(broker))
        pos = _position()
        self.assertIsNone(engine._sell_for_real(pos, datetime(2026, 10, 13, 9, 20)))
        broker.no_session = False
        self.assertEqual(engine._sell_for_real(pos, datetime(2026, 10, 13, 9, 21)), 200.5)
        self.assertEqual(broker.calls[-1][1]["transaction_type"], "SELL")


def _zerodha_user(token_at=TODAY_TOKEN, uid=77):
    return {
        "id": uid,
        "zerodha_api_key": "key123",
        "zerodha_api_secret": "secret",
        "zerodha_access_token": "tok",
        "zerodha_user_id": "AB1234",
        "zerodha_token_at": token_at,
    }


class TheAppRoutesItToTheRightAccount(unittest.TestCase):
    UID = 77

    def setUp(self):
        app_module._gap_carry_engines.clear()
        app_module._gap_carry_reconcile_pending.clear()
        app_module._gap_carry_zerodha_login_missing.clear()
        self.addCleanup(app_module._gap_carry_engines.clear)
        self.addCleanup(app_module._gap_carry_reconcile_pending.clear)
        self.addCleanup(app_module._gap_carry_zerodha_login_missing.clear)

    def test_only_the_exact_word_moves_a_carry(self):
        name = app_module._gap_carry_broker_name
        self.assertEqual(
            [name(v) for v in ("zerodha", " Zerodha ", "dhan", "", None, "kite")],
            ["zerodha", "zerodha", "dhan", "dhan", "dhan", "dhan"],
        )

    def test_the_executor_takes_the_order_broker_and_dhan_by_default(self):
        dhan, kite = object(), zerodha.ZerodhaClient("k", "t")
        self.assertIs(app_module._gap_carry_executor(dhan, "live").broker, dhan)
        self.assertIs(app_module._gap_carry_executor(dhan, "live", kite).broker, kite)
        self.assertIsNone(app_module._gap_carry_executor(dhan, "paper", kite))

    def _saved(self, broker_client):
        engine = gap_carry_paper.GapCarryPaper(
            config=gap_carry_paper.GapCarryConfig(), executor=_executor(broker_client)
        )
        engine.position = _position()
        engine._status = gap_carry_paper.HOLDING
        return engine.to_dict()

    def _restore(self, saved, user, reconciled):
        stored = json.dumps({"engine": saved, "running": True, "last_candle_timestamp": "2026-10-12T15:10:00+05:30"})

        async def _get(_key):
            return stored

        async def _user(_uid):
            return user

        async def _history(_broker):
            return None

        async def _reconcile(_label, broker, **_k):
            reconciled.append(broker)
            return None

        with (
            patch.object(app_module._db_mod, "get_app_state", _get),
            patch.object(app_module._db_mod, "get_user_by_id", _user),
            patch.object(app_module, "_gap_carry_history_lookup", _history),
            patch.object(app_module, "_reconcile_shared_live_orders", _reconcile),
            patch.object(app_module, "_gap_carry_live_open", lambda: True),
            patch.object(app_module, "CascadeOptionsAdapter", lambda *a, **k: None),
        ):
            return asyncio.run(app_module._restore_gap_carry_open_state(self.UID, object(), activate=False))

    def test_a_zerodha_carry_comes_back_on_zerodha_and_is_checked_there(self):
        reconciled = []
        runtime = self._restore(self._saved(_Broker("zerodha")), _zerodha_user(), reconciled)
        client = runtime.engine.executor.broker
        self.assertIsInstance(client, zerodha.ZerodhaClient)
        self.assertEqual(reconciled, [client])  # asked of the account that holds the leg
        self.assertTrue(runtime.running)
        self.assertEqual(runtime.engine.to_dict()["broker"], "zerodha")

    def test_before_the_days_login_it_comes_back_unfrozen_and_owes_its_check(self):
        reconciled = []
        runtime = self._restore(self._saved(_Broker("zerodha")), _zerodha_user(STALE_TOKEN), reconciled)
        self.assertEqual(reconciled, [])  # the stale session cannot answer; not asked, not frozen
        self.assertFalse(runtime.engine.frozen_reason)
        self.assertTrue(runtime.running)
        self.assertIn(self.UID, app_module._gap_carry_reconcile_pending)

    def test_the_login_hands_over_the_token_and_asks_the_owed_question(self):
        reconciled = []
        runtime = self._restore(self._saved(_Broker("zerodha")), _zerodha_user(STALE_TOKEN), reconciled)
        handed = app_module._hand_zerodha_token_to_running_books(self.UID, "fresh", TODAY_TOKEN)
        self.assertEqual(handed, 1)
        self.assertEqual(runtime.engine.executor.broker.access_token, "fresh")

        async def _reconcile(_label, broker, **_k):
            reconciled.append(broker)
            return "Dhan holds less than this campaign expects: 65 short on 22700PE"

        async def _save(*_a, **_k):
            return None

        with (
            patch.object(app_module, "_reconcile_shared_live_orders", _reconcile),
            patch.object(app_module, "_save_gap_carry_open_state", _save),
        ):
            asyncio.run(app_module._gap_carry_after_zerodha_login(self.UID))
        self.assertEqual(reconciled, [runtime.engine.executor.broker])
        self.assertIn("Zerodha holds less", runtime.engine.frozen_reason)
        self.assertFalse(runtime.running)
        self.assertNotIn(self.UID, app_module._gap_carry_reconcile_pending)

    def test_a_dhan_carry_is_untouched_by_any_of_it(self):
        reconciled = []
        dhan = object()
        saved = self._saved(_Broker("dhan"))
        self.assertEqual(saved["broker"], "dhan")
        runtime = self._restore(saved, _zerodha_user(), reconciled)
        self.assertNotIsInstance(runtime.engine.executor.broker, zerodha.ZerodhaClient)
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(app_module._hand_zerodha_token_to_running_books(self.UID, "fresh", TODAY_TOKEN), 0)
        self.assertFalse(app_module._user_zerodha_settings_lock(self.UID)[0])
        del dhan

    def test_a_zerodha_carry_whose_app_was_removed_is_held_not_moved_to_dhan(self):
        reconciled = []
        runtime = self._restore(self._saved(_Broker("zerodha")), {"id": self.UID}, reconciled)
        self.assertIsNone(runtime.engine.executor)
        self.assertIn("Zerodha app is no longer saved", runtime.engine.frozen_reason)
        self.assertFalse(runtime.running)

    def test_a_running_zerodha_carry_locks_the_kite_app_and_rings_before_the_open(self):
        self._restore(self._saved(_Broker("zerodha")), _zerodha_user(STALE_TOKEN), [])
        locked, reason = app_module._user_zerodha_settings_lock(self.UID)
        self.assertTrue(locked)
        self.assertIn("Gap Carry", reason)
        self.assertIn("Gap Carry", app_module._zerodha_books_waiting_for_login(datetime.now(IST)))

    def test_starting_on_zerodha_needs_todays_login(self):
        async def _user(_uid):
            return _zerodha_user(STALE_TOKEN)

        async def _no_restore(*_a, **_k):
            return None

        async def _candles(*_a, **_k):
            return []

        payload = SimpleNamespace(**app_module._GAP_CARRY_AUTO_RULE, mode="live", broker="zerodha")
        with (
            patch.object(app_module._db_mod, "get_user_by_id", _user),
            patch.object(app_module, "_restore_gap_carry_open_state", _no_restore),
            patch.object(app_module, "_gap_carry_load_candles", _candles),
            patch.object(app_module, "CascadeOptionsAdapter", lambda *a, **k: None),
        ):
            with self.assertRaises(app_module.HTTPException) as caught:
                asyncio.run(app_module._start_gap_carry_campaign(self.UID, payload, broker_client=object()))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("Log in to Zerodha", caught.exception.detail)
        self.assertNotIn(self.UID, app_module._gap_carry_engines)


class AutoOnZerodha(unittest.TestCase):
    UID = 78

    def setUp(self):
        app_module._gap_carry_engines.clear()
        app_module._gap_carry_zerodha_login_missing.clear()
        self.addCleanup(app_module._gap_carry_engines.clear)
        self.addCleanup(app_module._gap_carry_zerodha_login_missing.clear)

    def _step(self, setting, full_user, started):
        async def _user(_uid):
            return full_user

        async def _restore(*_a, **_k):
            return None

        async def _save(_uid):
            return None

        async def _start(uid, payload, *, broker_client):
            started.append(payload)

        alerts = []
        now = datetime(2026, 10, 12, 15, 11, tzinfo=IST)
        with (
            patch.object(app_module._db_mod, "get_user_by_id", _user),
            patch.object(app_module, "_resolve_user_broker_client", lambda user: (object(), "user")),
            patch.object(app_module, "_restore_gap_carry_open_state", _restore),
            patch.object(app_module, "_save_gap_carry_auto", _save),
            patch.object(app_module, "_start_gap_carry_campaign", _start),
            patch.object(app_module, "_gap_carry_live_open", lambda: True),
            patch.object(app_module.alerter, "alert", lambda *a, **k: alerts.append(a)),
        ):
            # The loop walks list_users(): a row with NO Kite fields in it.
            state = asyncio.run(app_module._gap_carry_auto_step({"id": self.UID}, setting, now=now))
        return state, alerts

    def test_it_starts_on_zerodha_reading_the_full_user_row(self):
        started = []
        setting = {"enabled": True, "mode": "live", "broker": "zerodha"}
        state, _alerts = self._step(setting, _zerodha_user(uid=self.UID), started)
        self.assertEqual(state, "entered")
        self.assertEqual((started[0].mode, started[0].broker), ("live", "zerodha"))

    def test_without_the_login_it_waits_says_so_once_and_keeps_the_day(self):
        started = []
        setting = {"enabled": True, "mode": "live", "broker": "zerodha"}
        state, alerts = self._step(setting, _zerodha_user(STALE_TOKEN, uid=self.UID), started)
        self.assertEqual(state, "waiting-for-zerodha-login")
        self.assertEqual(started, [])
        self.assertNotIn("entry_day", setting)  # tried again on the next tick
        self.assertIn("Log in to Zerodha", setting["last_error"])
        self.assertEqual(len(alerts), 1)
        _state, again = self._step(setting, _zerodha_user(STALE_TOKEN, uid=self.UID), started)
        self.assertEqual(again, [])  # the phone rings once a day, not every tick
        self.assertIn(self.UID, app_module._gap_carry_zerodha_login_missing)

    def test_an_auto_setting_from_before_today_is_dhan(self):
        started = []
        state, _alerts = self._step({"enabled": True, "mode": "live"}, {"id": self.UID}, started)
        self.assertEqual(state, "entered")
        self.assertEqual(started[0].broker, "dhan")


class ThePageOffersTheChoice(unittest.TestCase):
    HTML = (ROOT / "strategy.html").read_text(encoding="utf-8")
    JS = (ROOT / "static" / "philforge-app.js").read_text(encoding="utf-8")

    def test_the_switch_is_hidden_until_a_kite_app_is_saved(self):
        self.assertIn('id="oc-gap-broker-row" class="ocp-broker-row" hidden', self.HTML)
        self.assertIn('data-value="zerodha" data-tone="zerodha" data-pf-action="setGapCarryBroker"', self.HTML)

    def test_the_chosen_broker_wears_its_own_colour_on_both_switches(self):
        """Phil, 2026-10-12: "there is no highlighter while changing broker"."""
        css = (ROOT / "static" / "philforge-app.css").read_text(encoding="utf-8")
        for tone in ("dhan", "zerodha"):
            self.assertIn(f'.scalp-toggle-btn.active[data-tone="{tone}"]', css)
            # Scalp + Gap Carry + the Equity desk (2026-10-21).
            self.assertEqual(self.HTML.count(f'data-value="{tone}" data-tone="{tone}"'), 3, tone)

    def test_start_and_both_auto_calls_send_the_broker(self):
        self.assertEqual(self.JS.count("broker: _gapCarryBroker()"), 3)
        self.assertIn("body: JSON.stringify({ enabled: true, mode: 'live', broker: wanted })", self.JS)

    def test_the_confirm_names_whose_money(self):
        self.assertNotIn("with your Dhan money", self.JS)
        self.assertEqual(self.JS.count("money every session: 15:10 in, 09:20 out."), 3)


if __name__ == "__main__":
    unittest.main()
