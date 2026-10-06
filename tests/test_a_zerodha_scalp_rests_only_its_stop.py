"""A Zerodha scalp is an entry plus a stop resting at Zerodha; the target is ours.

Zerodha has no Super Order (it withdrew bracket orders in 2020), so a scalp
sent there cannot carry its exits inside the entry the way Dhan's does. The
stop rests at Zerodha as the net for a server that dies; the target is watched
by the engine, because a second exit order resting for the same position can
fill as a fresh SHORT. Dhan still prices every scalp and still trades the Dhan
ones exactly as before.
"""

import asyncio
import unittest

from scalp import ScalpEngine, ScalpTrade


class Dhan:
    """Prices, positions, and the Super Orders of Dhan scalps."""

    def __init__(self, ltp=200.0, positions=None):
        self.ltp = ltp
        self.positions = positions or []
        self.super_orders = []
        self.orders = []

    def get_option_ltp(self, *a, **k):
        return self.ltp

    def get_positions_cached(self, ttl):
        return self.positions

    def place_super_order(self, **kw):
        self.super_orders.append(kw)
        return {"orderId": "SO1", "orderStatus": "TRANSIT"}

    def place_option_order(self, **kw):
        self.orders.append(kw)
        return {"orderId": f"D{len(self.orders)}", "orderStatus": "TRANSIT"}

    def get_super_orders(self):
        return []


class Zerodha:
    def __init__(self, stop_status="PENDING", stop_avg=0.0, positions=None):
        self.orders = []
        self.cancelled = []
        self.modified = []
        self.stop_status = stop_status
        self.stop_avg = stop_avg
        self.positions = positions or []

    def place_option_order(self, **kw):
        self.orders.append(kw)
        return {"orderId": f"Z{len(self.orders)}", "orderStatus": "TRANSIT", "broker": "zerodha"}

    def verify_order_fill(self, order_id, max_wait_sec=15, poll_interval=1.0):
        return {"order_id": order_id, "status": "FILLED", "avg_price": 201.5}

    def get_order_status(self, order_id):
        return {"orderId": order_id, "orderStatus": self.stop_status, "averageTradedPrice": self.stop_avg}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"orderId": order_id, "orderStatus": ""}

    def modify_order(self, order_id, **kw):
        self.modified.append((order_id, kw))
        return {"orderId": order_id, "orderStatus": "TRANSIT"}

    def get_positions(self):
        return self.positions


def _entry(**over):
    args = dict(
        underlying="NIFTY",
        strike=23600,
        option_type="CE",
        expiry="2099-01-01",
        transaction_type="BUY",
        lots=1,
        lot_size=65,
        target_premium=260.0,
        sl_premium=150.0,
        mode="live",
        broker="zerodha",
    )
    args.update(over)
    return args


def _open_zerodha_trade(**over):
    trade = ScalpTrade(
        trade_id=1,
        underlying="NIFTY",
        strike=23600,
        option_type="CE",
        expiry="2099-01-01",
        transaction_type="BUY",
        lots=1,
        lot_size=65,
        entry_premium=200.0,
        target_premium=260.0,
        sl_premium=150.0,
        order_id="Z1",
        mode="live",
        broker="zerodha",
    )
    trade.broker_order_model = "zerodha"
    trade.broker_sl_order_id = "Z2"
    for key, value in over.items():
        setattr(trade, key, value)
    return trade


class AnEngine(unittest.IsolatedAsyncioTestCase):
    def make(self, dhan=None, zerodha=None):
        engine = ScalpEngine(dhan or Dhan(), zerodha_client=zerodha)
        engine._log = lambda level, msg: self.logged.append((level, msg))
        self.logged = []
        self.addCleanup(engine.stop)
        return engine

    async def settle(self, engine):
        tasks = [t for t in engine._broker_sync_tasks.values() if not t.done()]
        if tasks:
            await asyncio.gather(*tasks)


class TheEntryGoesToZerodha(AnEngine):
    async def test_the_entry_and_its_stop_go_to_zerodha_and_nothing_to_dhan(self):
        dhan, zerodha = Dhan(), Zerodha()
        engine = self.make(dhan, zerodha)
        result = await engine.enter_trade(**_entry())
        self.assertEqual(result["status"], "ok", result)
        await self.settle(engine)

        self.assertEqual(dhan.super_orders, [])
        self.assertEqual(dhan.orders, [])
        kinds = [o["order_type"] for o in zerodha.orders]
        self.assertEqual(kinds, ["MARKET", "SL"])
        self.assertEqual(zerodha.orders[1]["transaction_type"], "SELL")
        self.assertEqual(zerodha.orders[1]["trigger_price"], 150.0)

        trade = engine.open_trades[result["trade_id"]]
        self.assertEqual(trade.broker, "zerodha")
        self.assertEqual(trade.broker_sl_order_id, "Z2")
        self.assertEqual(trade.broker_tp_order_id, "")
        self.assertEqual(trade.super_order_id, "")
        self.assertEqual(trade.entry_premium, 201.5)  # the fill, not the quote
        self.assertEqual(trade.to_dict()["broker"], "zerodha")

    async def test_no_target_order_rests_at_zerodha(self):
        zerodha = Zerodha()
        engine = self.make(Dhan(), zerodha)
        await engine.enter_trade(**_entry())
        await self.settle(engine)
        self.assertNotIn("LIMIT", [o["order_type"] for o in zerodha.orders])

    async def test_without_a_zerodha_login_nothing_is_sent(self):
        dhan = Dhan()
        engine = self.make(dhan, None)
        result = await engine.enter_trade(**_entry())
        self.assertEqual(result["status"], "error")
        self.assertIn("Zerodha", result["message"])
        self.assertEqual(dhan.super_orders, [])
        self.assertEqual(engine.open_trades, {})

    async def test_a_dhan_scalp_is_still_one_super_order(self):
        dhan, zerodha = Dhan(), Zerodha()
        engine = self.make(dhan, zerodha)
        result = await engine.enter_trade(**_entry(broker="dhan"))
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(len(dhan.super_orders), 1)
        self.assertEqual(zerodha.orders, [])
        self.assertEqual(engine.open_trades[result["trade_id"]].broker, "dhan")

    async def test_a_target_on_the_wrong_side_names_zerodha(self):
        engine = self.make(Dhan(ltp=200.0), Zerodha())
        result = await engine.enter_trade(**_entry(target_premium=190.0))
        self.assertEqual(result["status"], "error")
        self.assertIn("Zerodha would refuse", result["message"])

    async def test_a_triggered_pending_entry_also_goes_to_zerodha(self):
        dhan, zerodha = Dhan(), Zerodha()
        engine = self.make(dhan, zerodha)
        result = await engine.enter_trade(**_entry(entry_limit_price=195.0, entry_limit_max=205.0))
        trade = engine.open_trades[result["trade_id"]]
        trade.current_premium = 200.0
        await engine._activate_pending_trade(trade)
        await self.settle(engine)
        self.assertEqual(dhan.super_orders, [])
        self.assertEqual([o["order_type"] for o in zerodha.orders], ["MARKET", "SL"])
        self.assertEqual(trade.status, "open")


class TheExitAsksZerodhaFirst(AnEngine):
    async def test_an_exit_cancels_the_stop_then_sells_on_zerodha(self):
        dhan, zerodha = Dhan(), Zerodha()
        engine = self.make(dhan, zerodha)
        trade = _open_zerodha_trade()
        engine.open_trades[1] = trade
        await engine.exit_trade(1)
        self.assertEqual(zerodha.cancelled, ["Z2"])
        self.assertEqual(len(zerodha.orders), 1)
        self.assertEqual(zerodha.orders[0]["transaction_type"], "SELL")
        self.assertEqual(dhan.orders, [])
        self.assertNotIn(1, engine.open_trades)

    async def test_a_stop_that_already_filled_means_no_second_sell(self):
        """Selling again after Zerodha's stop sold would open a short."""
        zerodha = Zerodha(stop_status="TRADED", stop_avg=149.5)
        engine = self.make(Dhan(), zerodha)
        engine.open_trades[1] = _open_zerodha_trade()
        await engine.exit_trade(1)
        self.assertEqual(zerodha.orders, [])
        closed = engine.closed_trades[-1]
        self.assertEqual(closed["exit_reason"], "sl_hit")
        self.assertEqual(closed["exit_premium"], 149.5)

    async def test_moving_the_stop_moves_the_order_at_zerodha(self):
        zerodha = Zerodha()
        engine = self.make(Dhan(), zerodha)
        engine.open_trades[1] = _open_zerodha_trade()
        result = await engine.update_trade_targets(1, sl_premium=170.0)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(zerodha.modified[0][0], "Z2")
        self.assertEqual(zerodha.modified[0][1]["trigger_price"], 170.0)

    async def test_moving_the_target_sends_nothing(self):
        zerodha = Zerodha()
        engine = self.make(Dhan(), zerodha)
        engine.open_trades[1] = _open_zerodha_trade()
        result = await engine.update_trade_targets(1, target_premium=280.0)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(zerodha.orders, [])
        self.assertEqual(zerodha.modified, [])
        self.assertEqual(engine.open_trades[1].target_premium, 280.0)

    async def test_the_engine_still_exits_at_the_target(self):
        trade = _open_zerodha_trade()
        trade.entry_time = trade.entry_time.replace(year=2000)
        self.assertEqual(trade.check_exit(261.0), "target_hit")


class TheStopIsWatched(AnEngine):
    async def test_a_filled_stop_closes_the_trade_as_a_stop(self):
        zerodha = Zerodha(stop_status="TRADED", stop_avg=148.0)
        engine = self.make(Dhan(), zerodha)
        engine.open_trades[1] = _open_zerodha_trade()
        await engine._sync_zerodha_stops()
        self.assertNotIn(1, engine.open_trades)
        self.assertEqual(engine.closed_trades[-1]["exit_reason"], "sl_hit")
        self.assertEqual(zerodha.orders, [])

    async def test_a_resting_stop_leaves_the_trade_open(self):
        engine = self.make(Dhan(), Zerodha(stop_status="PENDING"))
        engine.open_trades[1] = _open_zerodha_trade()
        await engine._sync_zerodha_stops()
        self.assertIn(1, engine.open_trades)

    async def test_a_stop_that_closes_mid_placement_is_withdrawn(self):
        zerodha = Zerodha()
        engine = self.make(Dhan(), zerodha)
        trade = _open_zerodha_trade(broker_sl_order_id="")
        trade.status = "closed"  # closed while the stop was on its way
        await engine._place_broker_sl_tp(trade)
        self.assertEqual(zerodha.cancelled, ["Z1"])
        self.assertEqual(trade.broker_sl_order_id, "")


class EachTradeIsCheckedAtItsOwnBroker(AnEngine):
    async def test_an_empty_dhan_book_does_not_close_a_zerodha_scalp(self):
        held = [{"securityId": "", "netQty": 0}]
        engine = self.make(Dhan(positions=[]), Zerodha(positions=held))
        trade = _open_zerodha_trade()
        trade.entry_time = trade.entry_time.replace(year=2000)
        engine.open_trades[1] = trade
        from unittest.mock import patch

        with patch("scalp.ScripMaster.lookup", return_value="40001"):
            engine.zerodha.positions = [{"securityId": "40001", "netQty": 65}]
            await engine._sync_broker_positions()
        self.assertIn(1, engine.open_trades)

    async def test_a_position_zerodha_cannot_name_is_not_read_as_gone(self):
        engine = self.make(Dhan(), Zerodha(positions=[{"securityId": "", "netQty": 65}]))
        trade = _open_zerodha_trade()
        trade.entry_time = trade.entry_time.replace(year=2000)
        engine.open_trades[1] = trade
        from unittest.mock import patch

        with patch("scalp.ScripMaster.lookup", return_value="40001"):
            await engine._sync_broker_positions()
        self.assertIn(1, engine.open_trades)


class ARestoredTradeKeepsItsBroker(unittest.TestCase):
    def test_the_saved_row_comes_back_on_zerodha(self):
        import app

        row = _open_zerodha_trade().to_dict()
        trade = app._restore_scalp_trade_from_payload(row)
        self.assertEqual(trade.broker, "zerodha")
        self.assertEqual(trade.broker_sl_order_id, "Z2")

    def test_an_old_row_without_a_broker_stays_on_dhan(self):
        import app

        row = _open_zerodha_trade().to_dict()
        row.pop("broker")
        self.assertEqual(app._restore_scalp_trade_from_payload(row).broker, "dhan")


if __name__ == "__main__":
    unittest.main()
