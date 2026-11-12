"""The Scalp guard (2026-11-10): the limits a live Scalp day is held to.

Each test is one of the faults in the 09-Nov-2026 review: too many trades,
no stop for the day, size raised after losing, stops 100 points away, and a
limit that could be lifted while losing.
"""

import unittest
from datetime import date, datetime

import scalp_guard as g

DAY = date(2026, 11, 10)
NOW = datetime(2026, 11, 10, 11, 0, 0)


def _closed(tid, pnl, exit_time="2026-11-10 10:00:00", **extra):
    return {
        "trade_id": tid,
        "mode": "live",
        "entry_time": "2026-11-10 09:59:00",
        "exit_time": exit_time,
        "pnl": pnl,
        **extra,
    }


class DayStats(unittest.TestCase):
    def test_counts_todays_live_trades_once(self):
        closed = [_closed(1, 500), _closed(1, 500), _closed(2, -700), {**_closed(3, 100), "mode": "paper"}]
        closed.append({**_closed(4, 50), "entry_time": "2026-11-09 10:00:00"})
        stats = g.day_stats(closed, [{"trade_id": 5, "mode": "live", "pnl": -300, "status": "open"}], DAY)
        self.assertEqual(stats["trades"], 3)
        self.assertEqual(stats["booked"], -200.0)
        self.assertEqual(stats["pnl"], -500.0)

    def test_a_both_brokers_pair_is_one_trade(self):
        closed = [_closed(1, 100, pair_id="P1"), _closed(2, 100, pair_id="P1")]
        self.assertEqual(g.day_stats(closed, [], DAY)["trades"], 1)

    def test_a_cancelled_order_is_not_a_trade(self):
        self.assertEqual(g.day_stats([_closed(1, 0, exit_reason="cancelled")], [], DAY)["trades"], 0)


class Refusal(unittest.TestCase):
    limits = g.effective(None, DAY)

    def test_the_defaults_are_on(self):
        self.assertTrue(self.limits["enabled"])
        self.assertEqual((self.limits["max_trades"], self.limits["max_lots"]), (10, 2))

    def test_an_ordinary_entry_goes(self):
        self.assertEqual(g.refusal(self.limits, {"trades": 2, "pnl": -1000, "last_loss_at": ""}, 2, NOW), "")

    def test_the_day_stops_at_the_loss_limit(self):
        self.assertIn("No more live trades today", g.refusal(self.limits, {"trades": 2, "pnl": -5000}, 1, NOW))

    def test_the_day_stops_at_the_trade_limit(self):
        self.assertIn("your limit is 10", g.refusal(self.limits, {"trades": 10, "pnl": 900}, 1, NOW))

    def test_size_is_capped(self):
        self.assertIn("over your limit of 2", g.refusal(self.limits, {"trades": 0, "pnl": 0}, 4, NOW))

    def test_a_loss_is_followed_by_a_wait(self):
        stats = {"trades": 1, "pnl": -500, "last_loss_at": "2026-11-10 10:59:00"}
        self.assertIn("wait 61s", g.refusal(self.limits, stats, 1, NOW))
        stats["last_loss_at"] = "2026-11-10 10:57:59"
        self.assertEqual(g.refusal(self.limits, stats, 1, NOW), "")

    def test_switched_off_it_refuses_nothing(self):
        off = {**self.limits, "enabled": False}
        self.assertEqual(g.refusal(off, {"trades": 99, "pnl": -99999}, 50, NOW), "")


class Stop(unittest.TestCase):
    limits = g.effective(None, DAY)

    def _clamp(self, **kw):
        base = dict(sl_points=0, sl_premium=0, sl_pct=0, sl_rupees=0, quantity=130, price=120.0, buying=True)
        return g.clamp_stop(self.limits, **{**base, **kw})

    def test_a_stop_inside_the_limit_is_left_alone(self):
        self.assertEqual(self._clamp(sl_points=15)["sl_points"], 15)
        self.assertEqual(self._clamp(sl_points=15)["note"], "")
        self.assertEqual(self._clamp(sl_premium=105.0)["sl_premium"], 105.0)
        self.assertEqual(self._clamp(sl_rupees=2000)["sl_rupees"], 2000)

    def test_his_100_point_stop_becomes_20(self):
        out = self._clamp(sl_points=100)
        self.assertEqual((out["sl_points"], out["sl_premium"]), (20.0, 0.0))
        self.assertIn("it was 100 points", out["note"])

    def test_a_blank_stop_becomes_20(self):
        out = self._clamp()
        self.assertEqual(out["sl_points"], 20.0)
        self.assertIn("not set", out["note"])

    def test_a_far_premium_or_rupee_stop_becomes_20(self):
        self.assertEqual(self._clamp(sl_premium=20.0)["sl_points"], 20.0)
        self.assertEqual(self._clamp(sl_rupees=13000)["sl_points"], 20.0)
        # No price to measure a premium stop against: it is not trusted.
        self.assertEqual(self._clamp(sl_premium=105.0, price=0)["sl_points"], 20.0)

    def test_a_live_stop_may_come_closer_but_not_go_further(self):
        move = lambda new, old=100.0: g.may_move_stop(self.limits, entry=120.0, new_stop=new, old_stop=old, buying=True)  # noqa: E731
        self.assertEqual(move(110.0), "")
        self.assertIn("your limit is 20", move(90.0))
        self.assertIn("keeps its stop", move(0))
        # An older trade with a wide stop may still be brought in.
        self.assertEqual(move(60.0, old=20.0), "")
        self.assertIn("your limit", move(10.0, old=20.0))


class EveryChangeAppliesAtOnce(unittest.TestCase):
    """Phil, 2026-11-12: "I can't even change the guard". His limits, his call."""

    def test_looser_and_tighter_both_apply_now(self):
        state = g.apply_change({"active": g.DEFAULTS}, {"max_lots": 1, "max_trades": 50, "max_daily_loss": 0}, DAY)
        now = g.effective(state, DAY)
        self.assertEqual((now["max_lots"], now["max_trades"], now["max_daily_loss"]), (1, 50, 0))
        self.assertIsNone(state["pending"])

    def test_switching_it_off_is_now(self):
        self.assertFalse(g.effective(g.apply_change({"active": g.DEFAULTS}, {"enabled": False}, DAY), DAY)["enabled"])

    def test_a_change_left_waiting_by_the_old_rule_counts_now(self):
        old = {"active": dict(g.DEFAULTS), "pending": {"enabled": False}, "pending_from": "2026-11-13"}
        self.assertFalse(g.effective(old, DAY)["enabled"])


class TheEntryRouteObeysIt(unittest.TestCase):
    """The live entry route refuses over-limit entries before any order, and
    sends a stop inside the limit. Paper is untouched."""

    def _enter(self, *, mode="live", lots=2, sl_points=100.0, closed=(), stored=None):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        import app

        eng = MagicMock()
        eng.open_trades = {}
        eng.closed_trades = list(closed)
        eng.enter_trade = AsyncMock(return_value={"status": "ok", "trade_id": 1})
        req = app.ScalpEntryReq(
            underlying="NIFTY",
            strike=25000,
            option_type="CE",
            expiry="2026-11-17",
            lots=lots,
            lot_size=65,
            mode=mode,
            transaction_type="BUY",
            sl_points=sl_points,
        )
        state = None if stored is None else __import__("json").dumps(stored)
        with (
            patch.object(app, "_request_user_id", return_value=1),
            patch.object(app, "_request_broker_context", AsyncMock(return_value=({"id": 1}, MagicMock(), "user"))),
            patch.object(app, "_get_scalp_engine", return_value=eng),
            patch.object(app, "_attach_scalp_zerodha", return_value=(False, "missing")),
            patch.object(app, "_save_scalp_open_state", AsyncMock()),
            patch.object(app, "_notify_scalp_ws"),
            patch.object(app, "_alert_scalp_entry"),
            patch.object(app, "_market_feed", None),
            patch.object(app._db_mod, "get_app_state", AsyncMock(return_value=state)),
            patch.object(app._db_mod, "list_scalp_trades_since", AsyncMock(return_value=[])),
            patch.dict(app._last_scalp_entry_ts, {}, clear=True),
        ):
            result = asyncio.run(app.scalp_entry(req, MagicMock()))
        return result, eng

    def test_four_lots_live_is_refused_and_no_order_is_built(self):
        result, eng = self._enter(lots=4)
        self.assertEqual(result["status"], "error")
        self.assertIn("over your limit of 2", result["message"])
        eng.enter_trade.assert_not_called()

    def test_a_100_point_stop_is_sent_as_20(self):
        result, eng = self._enter(lots=2, sl_points=100.0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(eng.enter_trade.call_args.kwargs["sl_points"], 20.0)
        self.assertIn("stop set 20 points", result["guard_note"])

    def test_after_ten_trades_the_day_is_over(self):
        today = datetime.now(__import__("app").IST).date().isoformat()
        closed = [
            {
                "trade_id": i,
                "mode": "live",
                "entry_time": f"{today} 09:30:00",
                "exit_time": f"{today} 09:31:00",
                "pnl": 100,
            }
            for i in range(10)
        ]
        result, eng = self._enter(closed=closed)
        self.assertIn("your limit is 10", result["message"])
        eng.enter_trade.assert_not_called()

    def test_paper_is_free(self):
        result, eng = self._enter(mode="paper", lots=9, sl_points=100.0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(eng.enter_trade.call_args.kwargs["sl_points"], 100.0)

    def test_switched_off_it_stays_out_of_the_way(self):
        result, eng = self._enter(lots=9, sl_points=100.0, stored={"active": {**g.DEFAULTS, "enabled": False}})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(eng.enter_trade.call_args.kwargs["sl_points"], 100.0)


class SensexHasItsOwnStop(unittest.TestCase):
    """Phil, 2026-11-12: "This is ridiculous for SENSEX.. 20 points is very small"."""

    def test_sensex_left_at_zero_is_three_times_nifty(self):
        limits = {**g.DEFAULTS, "max_stop_points": 26.0}
        self.assertEqual(g.stop_cap(limits, "NIFTY"), 26.0)
        self.assertEqual(g.stop_cap(limits, "SENSEX"), 78.0)

    def test_its_own_number_wins(self):
        limits = {**g.DEFAULTS, "max_stop_points": 26.0, "max_stop_points_sensex": 90.0}
        self.assertEqual(g.stop_cap(limits, "SENSEX"), 90.0)

    def test_a_60_point_sensex_stop_is_left_alone(self):
        limits = {**g.DEFAULTS, "max_stop_points": 20.0}
        base = dict(sl_premium=0, sl_pct=0, sl_rupees=0, quantity=40, price=300.0, buying=True)
        self.assertEqual(g.clamp_stop(limits, underlying="SENSEX", sl_points=60, **base)["note"], "")
        self.assertEqual(g.clamp_stop(limits, underlying="NIFTY", sl_points=60, **base)["sl_points"], 20.0)
        self.assertEqual(
            g.may_move_stop(limits, underlying="SENSEX", entry=300.0, new_stop=245.0, old_stop=250.0, buying=True), ""
        )

    def test_a_sensex_number_applies_now(self):
        state = g.apply_change({"active": g.DEFAULTS}, {"max_stop_points_sensex": 80}, DAY)
        self.assertEqual(g.stop_cap(g.effective(state, DAY), "SENSEX"), 80.0)


if __name__ == "__main__":
    unittest.main()
