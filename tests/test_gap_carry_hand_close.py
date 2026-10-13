"""A carry sold at the broker by hand is booked, and is never sold again.

Phil, 13-Oct-2026: he sold the live 22500CE himself at 15:30:22 in Dhan's own
app. The site went on saying "Holding overnight - sells at 09:20", because the
engine compared itself with the broker's position book only after a restart.
Worse than a stale page: at 09:20 it would have cancelled the bracket and sent
a market SELL for 65 calls the account no longer held -- a naked short -- and
until then the bracket's stop and target were resting over a flat account.

So the book is asked while the carry is held and again before every exit:

  * gone, with the sale on the book  -> booked at the broker's price, nothing sent
  * gone, no sale to be seen         -> believed on the second reading, flagged
  * partly gone                      -> frozen for a human, nothing sent
  * book unreadable                  -> nothing sent until it can be read
"""

import asyncio
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
from engine import gap_carry_paper  # noqa: E402
from engine.options_live_executor import OptionsLiveExecutor  # noqa: E402

IST = app_module.IST
BOUGHT = datetime(2026, 10, 13, 15, 10, tzinfo=IST)
EVENING = datetime(2026, 10, 13, 15, 31, tzinfo=IST)
MORNING = datetime(2026, 10, 14, 9, 20, 1, tzinfo=IST)
EXPIRY = date(2026, 10, 19)


class _Executor:
    """A live executor whose position book the test writes."""

    broker_label = "Dhan"

    def __init__(self, book):
        self.book = book
        self.sold = []
        self.cancelled = []
        self.asked = 0

    def position_at_broker(self, **_kw):
        self.asked += 1
        book = self.book
        if isinstance(book, list):
            book = book.pop(0) if len(book) > 1 else book[0]
        if isinstance(book, Exception):
            raise book
        return book

    def cancel_bracket(self, *, order_id):
        self.cancelled.append(order_id)
        return {"order_id": order_id, "cancelled": True}

    def sell(self, **kw):
        self.sold.append(kw)
        return {"order_id": "X-1", "mode": "live", "status": "FILLED", "avg_price": 370.0}


def _held(executor, bracket="SO-1"):
    engine = gap_carry_paper.GapCarryPaper(config=gap_carry_paper.GapCarryConfig(), executor=executor)
    engine.position = gap_carry_paper.GapCarryPosition(
        session=date(2026, 10, 13),
        side="CE",
        strike=22500,
        expiry=EXPIRY,
        lot_size=65,
        lots=1,
        signal=None,
        entry_timestamp=BOUGHT,
        entry_spot=22690.0,
        entry_premium=331.0,
        order_id="SO-1",
        bracket_order_id=bracket,
    )
    engine._status = gap_carry_paper.HOLDING
    return engine


HELD = {"held": 65, "sell_qty": 0, "sell_avg": None}
SOLD_BY_HAND = {"held": 0, "sell_qty": 65, "sell_avg": 369.7}
GONE = {"held": 0, "sell_qty": 0, "sell_avg": None}


class SoldByHandIsBooked(unittest.TestCase):
    def test_the_sale_on_the_book_is_booked_at_its_price_and_nothing_is_sent(self):
        executor = _Executor(SOLD_BY_HAND)
        engine = _held(executor)
        self.assertEqual(engine.sync_with_broker_book(EVENING), "closed")
        self.assertEqual(executor.sold, [])
        self.assertFalse(engine.has_open_position)
        night = engine.history[-1]
        self.assertEqual((night.exit_reason, night.exit_premium, night.exit_priced), ("CLOSED_AT_BROKER", 369.7, True))
        self.assertEqual(engine.status, gap_carry_paper.CLOSED)

    def test_its_stop_and_target_come_off(self):
        executor = _Executor(SOLD_BY_HAND)
        engine = _held(executor)
        engine.sync_with_broker_book(EVENING)
        self.assertEqual(executor.cancelled, ["SO-1"])
        self.assertIsNone(engine.history[-1].bracket_order_id)

    def test_a_bracket_that_will_not_cancel_is_said_and_the_leg_is_still_booked(self):
        class Stuck(_Executor):
            def cancel_bracket(self, *, order_id):
                raise RuntimeError("DH-905")

        engine = _held(Stuck(SOLD_BY_HAND))
        self.assertEqual(engine.sync_with_broker_book(EVENING), "closed")
        self.assertTrue(any("would NOT cancel" in note for note in engine.notes))
        self.assertFalse(engine.has_open_position)


class TheMorningExitNeverSellsWhatIsGone(unittest.TestCase):
    def test_the_0920_exit_books_the_hand_sale_instead_of_selling(self):
        executor = _Executor(SOLD_BY_HAND)
        engine = _held(executor)
        engine.mark(MORNING, premium=382.0)
        self.assertEqual(executor.sold, [])
        self.assertEqual(engine.history[-1].exit_reason, "CLOSED_AT_BROKER")
        self.assertEqual(engine.history[-1].exit_premium, 369.7)

    def test_a_kill_does_not_sell_it_either(self):
        executor = _Executor(SOLD_BY_HAND)
        engine = _held(executor)
        engine.kill_and_close(382.0)
        self.assertEqual(executor.sold, [])
        self.assertFalse(engine.has_open_position)

    def test_a_flat_book_with_no_sale_on_it_is_believed_the_second_time(self):
        executor = _Executor(GONE)
        engine = _held(executor)
        engine.mark(MORNING, premium=382.0)
        self.assertTrue(engine.has_open_position)  # one reading is not enough
        engine.mark(MORNING, premium=380.0)
        self.assertEqual(executor.sold, [])
        night = engine.history[-1]
        self.assertEqual((night.exit_reason, night.exit_premium, night.exit_priced), ("CLOSED_AT_BROKER", 380.0, False))

    def test_one_flat_reading_between_two_held_ones_changes_nothing(self):
        executor = _Executor([GONE, HELD, GONE, HELD])
        engine = _held(executor)
        for _ in range(3):
            engine.sync_with_broker_book(EVENING)
        self.assertTrue(engine.has_open_position)

    def test_a_partly_sold_leg_freezes_and_nothing_is_sent(self):
        executor = _Executor({"held": 30, "sell_qty": 35, "sell_avg": 360.0})
        engine = _held(executor)
        engine.mark(MORNING, premium=382.0)
        engine.mark(MORNING, premium=382.0)
        self.assertEqual(executor.sold, [])
        self.assertTrue(engine.has_open_position)
        self.assertIn("holds 30 of the 65", engine.frozen_reason)
        self.assertEqual(sum("holds 30 of the 65" in note for note in engine.notes), 1)

    def test_an_unreadable_book_sends_nothing_and_then_the_exit_goes(self):
        executor = _Executor([None, RuntimeError("502"), HELD])
        engine = _held(executor)
        engine.mark(MORNING, premium=382.0)
        engine.mark(MORNING, premium=382.0)
        self.assertEqual(executor.sold, [])
        self.assertTrue(engine.has_open_position)
        self.assertFalse(engine.frozen_reason)
        engine.mark(MORNING, premium=382.0)
        self.assertEqual(len(executor.sold), 1)
        self.assertEqual(engine.history[-1].exit_reason, "MORNING_EXIT")

    def test_a_held_leg_sells_exactly_as_before(self):
        executor = _Executor(HELD)
        engine = _held(executor)
        engine.mark(MORNING, premium=382.0)
        self.assertEqual(len(executor.sold), 1)
        self.assertEqual(executor.cancelled, ["SO-1"])
        self.assertEqual((engine.history[-1].exit_reason, engine.history[-1].exit_premium), ("MORNING_EXIT", 370.0))

    def test_paper_asks_nobody(self):
        engine = _held(None)
        engine.position.order_id = None
        self.assertEqual(engine.sync_with_broker_book(EVENING), "held")
        engine.mark(MORNING, premium=382.0)
        self.assertEqual(engine.history[-1].exit_reason, "MORNING_EXIT")


class TheBookIsReadInThisProductOnly(unittest.TestCase):
    def read(self, rows, product="MARGIN"):
        broker = SimpleNamespace(get_positions=lambda: rows() if callable(rows) else rows)
        executor = OptionsLiveExecutor(broker, "NIFTY", armed=True, product_type=product, tag="PF_GAP_CARRY")
        with patch("broker.dhan.ScripMaster.lookup", return_value="42529"):
            return executor.position_at_broker(strike=22500, expiry=EXPIRY, option_type="CE")

    def test_the_closed_row_dhan_sent_on_13_october(self):
        rows = [
            {"securityId": "42529", "productType": "MARGIN", "netQty": 0, "buyQty": 65, "sellQty": 65, "sellAvg": 369.7}
        ]
        self.assertEqual(self.read(rows), {"held": 0, "sell_qty": 65, "sell_avg": 369.7})

    def test_an_intraday_book_on_the_same_strike_is_not_this_carry(self):
        rows = [
            {"securityId": "42529", "productType": "INTRADAY", "netQty": 130, "sellQty": 0, "sellAvg": 0},
            {"securityId": "99999", "productType": "MARGIN", "netQty": 65, "sellQty": 0, "sellAvg": 0},
        ]
        self.assertEqual(self.read(rows)["held"], 0)

    def test_kites_names_for_the_same_products(self):
        rows = [{"securityId": "42529", "productType": "NRML", "netQty": 65, "sellQty": 0, "sellAvg": 0}]
        self.assertEqual(self.read(rows)["held"], 65)
        self.assertEqual(self.read(rows, product="INTRADAY")["held"], 0)

    def test_a_book_that_cannot_be_read_is_not_an_empty_one(self):
        def dies():
            raise RuntimeError("Positions fetch failed")

        self.assertIsNone(self.read(dies))
        self.assertIsNone(self.read({"errorCode": "DH-901"}))
        self.assertEqual(self.read([]), {"held": 0, "sell_qty": 0, "sell_avg": None})


class TheServerLooksWhileItHolds(unittest.TestCase):
    def runtime(self, executor):
        return SimpleNamespace(engine=_held(executor))

    def test_once_a_minute_through_the_day_and_not_at_night(self):
        runtime = self.runtime(_Executor(HELD))
        app_module._gap_carry_book_checked_at.pop(7, None)
        self.assertTrue(app_module._gap_carry_book_check_due(7, runtime, EVENING))
        self.assertFalse(app_module._gap_carry_book_check_due(7, runtime, EVENING.replace(hour=19)))
        self.assertFalse(app_module._gap_carry_book_check_due(7, runtime, datetime(2026, 10, 17, 11, 0, tzinfo=IST)))
        with patch.object(app_module.alerter, "alert"):
            asyncio.run(app_module._gap_carry_sync_broker_book(7, runtime.engine, EVENING))
        self.assertFalse(app_module._gap_carry_book_check_due(7, runtime, EVENING))

    def test_a_paper_carry_is_never_checked(self):
        runtime = self.runtime(None)
        app_module._gap_carry_book_checked_at.pop(8, None)
        self.assertFalse(app_module._gap_carry_book_check_due(8, runtime, EVENING))

    def test_a_hand_close_is_announced_once_with_its_price(self):
        runtime = self.runtime(_Executor(SOLD_BY_HAND))
        with patch.object(app_module.alerter, "alert") as alert:
            outcome = asyncio.run(app_module._gap_carry_sync_broker_book(9, runtime.engine, EVENING))
            again = asyncio.run(app_module._gap_carry_sync_broker_book(9, runtime.engine, EVENING))
        self.assertEqual((outcome, again), ("closed", "held"))
        alert.assert_called_once()
        self.assertIn("369.70", alert.call_args.args[1])
        self.assertIn("no order was sent", alert.call_args.args[1])
        self.assertEqual(alert.call_args.kwargs.get("level"), "info")


class ARestartBooksItInsteadOfFreezing(unittest.TestCase):
    """Tonight's case: the row Dhan still shows is CLOSED, 65 sold at 369.70."""

    UID = 4242

    def tearDown(self):
        app_module._gap_carry_engines.pop(self.UID, None)

    def test_the_carry_comes_back_closed_at_the_brokers_price_and_is_saved(self):
        import json

        class Dhan:
            cancelled = []

            def get_positions(self):
                return [{"securityId": "42529", "productType": "MARGIN", "netQty": 0, "sellQty": 65, "sellAvg": 369.7}]

            def cancel_super_order(self, order_id, leg):
                self.cancelled.append((order_id, leg))
                return {"orderId": order_id, "orderStatus": "CANCELLED"}

        dhan = Dhan()
        saved = _held(OptionsLiveExecutor(dhan, "NIFTY", armed=True, product_type="MARGIN", tag="PF_GAP_CARRY"))
        stored = json.dumps(
            {"engine": saved.to_dict(), "running": True, "last_candle_timestamp": "2026-10-13T15:10:00+05:30"}
        )
        saves, asked = [], []

        async def _get(_key):
            return stored

        async def _none(*_a, **_k):
            return None

        async def _save(uid, **kw):
            saves.append((uid, kw))

        async def _reconcile(_label, _broker, *, symbol, legs):
            asked.append(legs)
            return None

        with (
            patch.object(app_module._db_mod, "get_app_state", _get),
            patch.object(app_module, "_gap_carry_history_lookup", _none),
            patch.object(app_module, "_gap_carry_bank", lambda *_a: _bank()),
            patch.object(app_module, "_reconcile_shared_live_orders", _reconcile),
            patch.object(app_module, "_save_gap_carry_open_state", _save),
            patch.object(app_module, "_gap_carry_live_open", lambda: True),
            patch.object(app_module, "CascadeOptionsAdapter", lambda *a, **k: None),
            patch("broker.dhan.ScripMaster.lookup", return_value="42529"),
            patch.object(app_module.alerter, "alert") as alert,
        ):
            runtime = asyncio.run(app_module._restore_gap_carry_open_state(self.UID, dhan, activate=False))
        engine = runtime.engine
        self.assertFalse(engine.has_open_position)
        self.assertFalse(runtime.running)
        self.assertFalse(engine.frozen_reason)
        night = engine.history[-1]
        self.assertEqual((night.exit_reason, night.exit_premium, night.exit_priced), ("CLOSED_AT_BROKER", 369.7, True))
        self.assertEqual(asked, [[]])  # nothing left to reconcile
        self.assertEqual(saves, [(self.UID, {"force": True})])
        alert.assert_called_once()
        self.assertIn("369.70", alert.call_args.args[1])


async def _bank():
    return {"nights": {}}


if __name__ == "__main__":
    unittest.main()
