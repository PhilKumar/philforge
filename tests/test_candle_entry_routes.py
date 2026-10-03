"""The Candle Entry routes' rules that do not need a broker: the contract, the
pricing-by-age lookup, and that Backtest and Start ask the same questions."""

import base64
import os
import unittest
from datetime import date, datetime, timedelta

os.environ.setdefault("PHILFORGE_PIN", "123456")
os.environ.setdefault("PHILFORGE_DB", "/tmp/philforge-candle-entry-routes.db")
os.environ.setdefault("PHILFORGE_USER_DATA_ROOT", "/tmp/philforge-candle-entry-routes-data")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())

import app as app_module  # noqa: E402

IST = app_module.IST


class ContractRuleTests(unittest.TestCase):
    """One CE for the whole ladder: the monthly, ATM-2 of the mother close, lot by date."""

    def _mother(self, when: datetime, close: float):
        return app_module.IndexCandle(when.replace(tzinfo=IST), close, close + 20, close - 20, close)

    def test_the_monthly_inside_fifteen_to_forty_five_days_is_taken(self):
        mother = self._mother(datetime(2026, 7, 22, 9, 15), 24110.0)
        expiries = [
            date(2026, 7, 28),
            date(2026, 8, 4),
            date(2026, 8, 11),
            date(2026, 8, 18),
            date(2026, 8, 25),
            date(2026, 9, 29),
        ]
        contract = app_module._candle_entry_contract(mother, expiries, -2)
        self.assertEqual(contract.expiry, date(2026, 8, 25))  # 34 DTE, the August monthly
        self.assertEqual(contract.strike, 24000)  # ATM 24100 - 100
        self.assertEqual(contract.option_type, "CE")
        self.assertEqual(contract.lot_size, 65)
        self.assertEqual(contract.security_id, "")

    def test_the_lot_size_follows_the_mothers_date_not_todays(self):
        mother = self._mother(datetime(2025, 3, 4, 9, 15), 22000.0)
        expiries = [date(2025, 3, 27), date(2025, 4, 24)]
        contract = app_module._candle_entry_contract(mother, expiries, -2)
        self.assertEqual(contract.lot_size, 75)  # the 2025 lot
        self.assertEqual(contract.expiry, date(2025, 3, 27))

    def test_the_offset_is_measured_from_the_mothers_atm(self):
        mother = self._mother(datetime(2026, 7, 22, 9, 15), 24124.0)  # rounds to 24100
        contract = app_module._candle_entry_contract(mother, [date(2026, 8, 25)], 0)
        self.assertEqual(contract.strike, 24100)
        contract = app_module._candle_entry_contract(mother, [date(2026, 8, 25)], -4)
        self.assertEqual(contract.strike, 23900)

    def test_the_weekly_rule_takes_the_first_expiry_at_least_four_days_out(self):
        mother = self._mother(datetime(2026, 7, 22, 9, 15), 24110.0)  # a Wednesday
        expiries = [date(2026, 7, 28), date(2026, 8, 4), date(2026, 8, 25)]
        contract = app_module._candle_entry_contract(mother, expiries, -2, "weekly4")
        self.assertEqual(contract.expiry, date(2026, 7, 28))  # 6 days: the first one >= 4
        mother = self._mother(datetime(2026, 7, 25, 9, 15), 24110.0)  # 3 days before the 28th
        contract = app_module._candle_entry_contract(mother, expiries, -2, "weekly4")
        self.assertEqual(contract.expiry, date(2026, 8, 4))
        with self.assertRaises(app_module.HTTPException):
            app_module._candle_entry_contract(mother, expiries, -2, "fortnightly")

    def test_no_expiry_at_all_is_refused_plainly(self):
        mother = self._mother(datetime(2026, 7, 22, 9, 15), 24110.0)
        with self.assertRaises(app_module.HTTPException) as caught:
            app_module._candle_entry_contract(mother, [], -2)
        self.assertEqual(caught.exception.status_code, 503)


class _Broker:
    def __init__(self, ltp=None):
        self.ltp = ltp
        self.asked = []

    def get_option_ltp(self, underlying, strike, expiry, side):
        self.asked.append((underlying, strike, expiry, side))
        return self.ltp


class PricingByAgeTests(unittest.TestCase):
    """A fresh minute gets the live quote; an old one only recorded history."""

    CONTRACT = app_module.FixedCampaignOption("NIFTY", 24000, date(2026, 8, 25), "CE", 65, "")

    def test_a_fresh_minute_is_the_live_quote(self):
        broker = _Broker(ltp=321.5)
        lookup = app_module._candle_entry_premium_lookup(broker, history=lambda _w, _c: 999.0)
        self.assertEqual(lookup(datetime.now(IST) - timedelta(seconds=30), self.CONTRACT), 321.5)
        self.assertEqual(broker.asked[0][:2], ("NIFTY", 24000))

    def test_an_old_minute_never_gets_todays_quote(self):
        broker = _Broker(ltp=321.5)
        history_calls = []

        def history(when, contract):
            history_calls.append((when, contract.strike))
            return 210.25

        lookup = app_module._candle_entry_premium_lookup(broker, history=history)
        old = datetime.now(IST) - timedelta(hours=3)
        self.assertEqual(lookup(old, self.CONTRACT), 210.25)
        self.assertEqual(broker.asked, [])
        self.assertEqual(history_calls[0][1], 24000)

    def test_an_old_minute_with_no_history_is_a_gap_not_a_guess(self):
        broker = _Broker(ltp=321.5)
        lookup = app_module._candle_entry_premium_lookup(broker, history=None)
        self.assertIsNone(lookup(datetime.now(IST) - timedelta(hours=3), self.CONTRACT))
        self.assertEqual(broker.asked, [])

    def test_a_failed_live_quote_does_not_fall_through_to_history(self):
        broker = _Broker(ltp=None)
        lookup = app_module._candle_entry_premium_lookup(broker, history=lambda _w, _c: 999.0)
        self.assertIsNone(lookup(datetime.now(IST), self.CONTRACT))


class PayloadIdentityTests(unittest.TestCase):
    """Backtest and Start trade ONE ladder, so they must ask the same questions."""

    def test_backtest_and_start_share_every_field(self):
        start = set(app_module.CandleEntryPaperStartPayload.model_fields)
        backtest = set(app_module.CandleEntryBacktestPayload.model_fields)
        # `mode` (paper/live) is the one thing only a Start can mean; a
        # backtest is neither. Everything that shapes the LADDER is shared.
        self.assertEqual(start - {"mode"}, backtest)
        self.assertEqual(
            backtest,
            {
                "mother_mode",
                "mother_timestamp",
                "watch_from",
                "timeframe",
                "box_bars",
                "box_position",
                "ce_offset_steps",
                "strike_at",
                "intraday_close",
                "expiry_rule",
                "target_fraction",
                "trailing_target",
            },
        )

    def test_the_box_defaults_are_the_measured_winner(self):
        # 278 bars, the bottom quarter -- the configuration the tearsheet is
        # built from. Changing these silently would change what the page runs.
        payload = app_module.CandleEntryBacktestPayload(mother_timestamp="2026-08-10T12:30")
        self.assertEqual(payload.box_bars, 278)
        self.assertEqual(payload.box_position, 0.25)
        self.assertEqual(payload.mother_mode, "manual")
        # Every rung takes ATM-2 of its own fill (Phil, 2026-08-19) -- the
        # mother's own ATM-2 sat above the target on half the campaigns.
        self.assertEqual(payload.strike_at, "each_buy")
        self.assertEqual(app_module._candle_entry_strike_at("first_buy"), "first_buy")
        with self.assertRaises(app_module.HTTPException):
            app_module._candle_entry_strike_at("second_buy")

    def test_live_is_refused_behind_the_same_gate_as_fib_boundary(self):
        with self.assertRaises(app_module.HTTPException) as caught:
            app_module._candle_entry_trade_mode("live")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(app_module._candle_entry_trade_mode("paper"), "paper")
        with self.assertRaises(app_module.HTTPException):
            app_module._candle_entry_trade_mode("demo")


class RegularSessionTests(unittest.TestCase):
    """Diwali's muhurat hour is not a session the ladder reads."""

    def _bar(self, when: datetime):
        return app_module.IndexCandle(when.replace(tzinfo=IST), 100.0, 101.0, 99.0, 100.5)

    def test_a_session_that_opens_in_the_afternoon_is_dropped_whole(self):
        normal = [self._bar(datetime(2025, 10, 20, 9, 15)), self._bar(datetime(2025, 10, 20, 9, 20))]
        muhurat = [self._bar(datetime(2025, 10, 21, 13, 45)), self._bar(datetime(2025, 10, 21, 13, 50))]
        after = [self._bar(datetime(2025, 10, 22, 9, 15))]
        kept = app_module._candle_entry_regular_sessions(normal + muhurat + after)
        self.assertEqual([row.timestamp.date() for row in kept], [date(2025, 10, 20)] * 2 + [date(2025, 10, 22)])

    def test_a_late_first_bar_from_a_partial_fetch_is_also_dropped(self):
        # The rule is "opens at 09:15": a fetch that starts mid-session on its
        # first day is treated the same way, which is the safe direction.
        rows = [self._bar(datetime(2025, 10, 20, 11, 0)), self._bar(datetime(2025, 10, 21, 9, 15))]
        kept = app_module._candle_entry_regular_sessions(rows)
        self.assertEqual([row.timestamp.date() for row in kept], [date(2025, 10, 21)])


class BoxMotherTests(unittest.IsolatedAsyncioTestCase):
    """The mother is FOUND: the latest bar making the N-bar high at or before the look-back moment."""

    class _Adapter:
        def __init__(self, rows):
            self.rows = rows

        async def async_get_candles(self, _symbol, _timeframe, from_date, to_date):
            return [row for row in self.rows if from_date <= row.timestamp.date() <= to_date]

    def _day(self, day: date, highs):
        out = []
        for i, high in enumerate(highs):
            when = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=15, tzinfo=IST) + timedelta(
                minutes=5 * i
            )
            out.append(app_module.IndexCandle(when, high - 5, high, high - 10, high - 2))
        return out

    async def test_it_finds_the_latest_box_high_and_hands_back_its_window(self):
        # Three sessions of 75 bars; the box is 150 bars. The last bar to make
        # a 150-bar high is at 11:00 on day three (bar 21), after which highs fall.
        d1 = self._day(date(2026, 8, 3), [100 + i * 0.1 for i in range(75)])
        d2 = self._day(date(2026, 8, 4), [108 + i * 0.1 for i in range(75)])
        d3 = self._day(date(2026, 8, 5), [116 + i * 0.1 for i in range(22)] + [110 - i * 0.1 for i in range(53)])
        adapter = self._Adapter(d1 + d2 + d3)
        mother, window = await app_module._candle_entry_find_box_mother(
            adapter, "5m", 150, datetime(2026, 8, 5, 15, 30, tzinfo=IST)
        )
        self.assertEqual(mother.timestamp, d3[21].timestamp)
        self.assertEqual(len(window), 150)
        self.assertEqual(window[-1].timestamp, mother.timestamp)
        self.assertEqual(max(row.high for row in window), mother.high)

    async def test_the_look_back_moment_is_respected(self):
        d1 = self._day(date(2026, 8, 3), [100 + i * 0.1 for i in range(75)])
        d2 = self._day(date(2026, 8, 4), [108 + i * 0.1 for i in range(75)])
        d3 = self._day(date(2026, 8, 5), [116 + i * 0.1 for i in range(75)])
        adapter = self._Adapter(d1 + d2 + d3)
        # Looking back from day two's close must not see day three's highs.
        mother, _ = await app_module._candle_entry_find_box_mother(
            adapter, "5m", 100, datetime(2026, 8, 4, 15, 30, tzinfo=IST)
        )
        self.assertEqual(mother.timestamp.date(), date(2026, 8, 4))

    async def test_not_before_takes_the_first_box_high_after_it_not_the_latest(self):
        # Day three makes a new high at bar 21 and another, higher, at bar 40.
        # Looking back from the close, the LATEST is bar 40; the next campaign
        # after an exit at 10:00 takes the FIRST one after that -- bar 21.
        d1 = self._day(date(2026, 8, 3), [100 + i * 0.1 for i in range(75)])
        d2 = self._day(date(2026, 8, 4), [108 + i * 0.1 for i in range(75)])
        highs = (
            [116 + i * 0.1 for i in range(22)] + [110 - i * 0.1 for i in range(18)] + [125 + i * 0.1 for i in range(35)]
        )
        d3 = self._day(date(2026, 8, 5), highs)
        adapter = self._Adapter(d1 + d2 + d3)
        latest, _ = await app_module._candle_entry_find_box_mother(
            adapter, "5m", 150, datetime(2026, 8, 5, 15, 30, tzinfo=IST)
        )
        self.assertEqual(latest.timestamp, d3[-1].timestamp)
        first, _ = await app_module._candle_entry_find_box_mother(
            adapter,
            "5m",
            150,
            datetime(2026, 8, 5, 15, 30, tzinfo=IST),
            not_before=datetime(2026, 8, 5, 10, 0, tzinfo=IST),
        )
        self.assertEqual(first.timestamp, d3[9].timestamp)  # 10:00 is bar 9; it makes a new high
        later, _ = await app_module._candle_entry_find_box_mother(
            adapter,
            "5m",
            150,
            datetime(2026, 8, 5, 15, 30, tzinfo=IST),
            not_before=datetime(2026, 8, 5, 11, 30, tzinfo=IST),
        )
        self.assertEqual(later.timestamp, d3[40].timestamp)  # the first bar at/after 11:30 that makes one

    async def test_too_little_history_is_a_plain_404(self):
        adapter = self._Adapter(self._day(date(2026, 8, 5), [100 + i for i in range(30)]))
        with self.assertRaises(app_module.HTTPException) as caught:
            await app_module._candle_entry_find_box_mother(adapter, "5m", 278, datetime(2026, 8, 5, 15, 30, tzinfo=IST))
        self.assertEqual(caught.exception.status_code, 404)

    def test_intraday_defaults_off_the_way_the_ladder_was_measured(self):
        self.assertFalse(app_module.CandleEntryPaperStartPayload(mother_timestamp="2026-08-10T12:30").intraday_close)
        self.assertFalse(app_module.CandleEntryBacktestPayload(mother_timestamp="2026-08-10T12:30").intraday_close)


class MotherValidationTests(unittest.TestCase):
    def test_a_mother_must_sit_on_its_charts_grid_and_be_closed(self):
        now = datetime(2026, 8, 18, 12, 0, tzinfo=IST)
        ok = datetime(2026, 8, 18, 10, 15, tzinfo=IST)
        app_module._candle_entry_validate_mother(ok, "1h", now)  # 10:15 is on the 1H grid
        with self.assertRaises(app_module.HTTPException):
            app_module._candle_entry_validate_mother(datetime(2026, 8, 18, 10, 30, tzinfo=IST), "1h", now)
        with self.assertRaises(app_module.HTTPException):
            app_module._candle_entry_validate_mother(
                datetime(2026, 8, 18, 11, 15, tzinfo=IST), "1h", now
            )  # closes 12:15
        app_module._candle_entry_validate_mother(datetime(2026, 8, 18, 11, 40, tzinfo=IST), "5m", now)
        with self.assertRaises(app_module.HTTPException):
            app_module._candle_entry_validate_mother(datetime(2026, 8, 18, 11, 42, tzinfo=IST), "5m", now)


if __name__ == "__main__":
    unittest.main()


class _NoOrders:
    """Stands in for the adapter and the broker: paper only, sends nothing."""

    paper_only = True

    def place_order(self, *_a, **_k):
        return None


class _Runtime:
    """The engine carries its OWN status string, which is what the auto step
    reads -- `running` is only the poll loop's flag."""

    def __init__(self, status: dict, running: bool, engine_status: str | None = None):
        self.running = running
        state = engine_status or str(status.get("status") or "CLOSED")
        self.engine = type("E", (), {"get_status": lambda _self, _s=status: _s, "status": state})()


class RestoreOpenBasketTests(unittest.IsolatedAsyncioTestCase):
    """A restart must not leave an open basket unmonitored.

    The saved row carries whatever `running` the poll loop held when it was
    written, and a deploy that stops the instance can persist False over a
    campaign that is still holding rungs. Phil, 2026-08-21, mid-deploy with
    two rungs open: "Make sure no runs are lost after refresh".
    """

    USER = 91

    def _open_engine(self):
        from datetime import date as _date

        mother = app_module.IndexCandle(datetime(2026, 8, 3, 15, 25, tzinfo=IST), 24700, 24774.3, 24680, 24760)
        contract = app_module.FixedCampaignOption("NIFTY", 24400, _date(2026, 8, 25), "CE", 65, "")
        engine = app_module.LadderCandleEntryPaper(
            mother, "5m", contract, _NoOrders(), lambda _t, _c: 309.0, require_below_mother=True, atm_fallback=True
        )
        engine.ingest(
            {
                "5m": [
                    app_module.IndexCandle(datetime(2026, 8, 11, 9, 5, tzinfo=IST), 24540, 24545, 24520, 24530),
                    app_module.IndexCandle(datetime(2026, 8, 11, 9, 10, tzinfo=IST), 24530, 24532, 24500, 24505),
                    app_module.IndexCandle(datetime(2026, 8, 11, 9, 15, tzinfo=IST), 24505, 24506, 24480, 24485),
                    app_module.IndexCandle(datetime(2026, 8, 11, 9, 25, tzinfo=IST), 24485, 24520, 24484, 24515),
                ]
            }
        )
        return engine

    def _store(self, engine, running):
        """The persisted row, served without touching sqlite."""
        row = app_module.json.dumps(
            {
                "running": running,
                "last_candle_timestamp": datetime(2026, 8, 11, 9, 25, tzinfo=IST).isoformat(),
                "engine": engine.to_dict(),
            },
            default=str,
        )

        async def fake_get(_key, _row=row):
            return _row

        app_module._db_mod.get_app_state = fake_get

    async def asyncSetUp(self):
        app_module._candle_entry_engines.pop(self.USER, None)
        self._orig_get = app_module._db_mod.get_app_state
        self.engine = self._open_engine()
        self.assertTrue(self.engine.ladder.fills, "the fixture must actually hold a rung")
        self.assertNotIn(self.engine.status, app_module._CANDLE_ENTRY_TERMINAL)
        self._store(self.engine, False)  # what a stopped instance wrote

    async def asyncTearDown(self):
        app_module._db_mod.get_app_state = self._orig_get
        app_module._candle_entry_engines.pop(self.USER, None)

    async def test_an_open_basket_comes_back_monitored_even_if_the_row_says_stopped(self):
        runtime = await app_module._restore_candle_entry_open_state(self.USER, _NoOrders(), activate=False)
        self.assertIsNotNone(runtime)
        self.assertTrue(runtime.running, "an unfinished basket must come back running")
        self.assertTrue(runtime.engine.ladder.fills)

    async def test_a_finished_campaign_is_not_revived(self):
        self.engine.kill_and_close(
            app_module.IndexCandle(datetime(2026, 8, 11, 10, 0, tzinfo=IST), 24500, 24500, 24500, 24500)
        )
        self.assertIn(self.engine.status, app_module._CANDLE_ENTRY_TERMINAL)
        self._store(self.engine, True)  # a stale flag over a finished campaign
        app_module._candle_entry_engines.pop(self.USER, None)
        runtime = await app_module._restore_candle_entry_open_state(self.USER, _NoOrders(), activate=False)
        self.assertIsNotNone(runtime)
        self.assertFalse(runtime.running)


class AutoMotherTests(unittest.IsolatedAsyncioTestCase):
    """Phil, 2026-08-20: match the backtest, new box high only, market hours only."""

    USER = {"id": 77}

    def setUp(self):
        app_module._candle_entry_auto[77] = {"enabled": True, "mode": "paper"}
        app_module._candle_entry_auto_loaded.add(77)
        app_module._candle_entry_engines.pop(77, None)
        self._saved = []
        self._orig_save = app_module._save_candle_entry_auto

        async def fake_save(uid):
            self._saved.append(uid)

        app_module._save_candle_entry_auto = fake_save
        self._orig_broker = app_module._resolve_user_broker_client
        app_module._resolve_user_broker_client = lambda _u, allow_admin_fallback=True: (object(), "test")

    def tearDown(self):
        app_module._save_candle_entry_auto = self._orig_save
        app_module._resolve_user_broker_client = self._orig_broker
        app_module._candle_entry_engines.pop(77, None)
        app_module._candle_entry_auto.pop(77, None)

    def _bar(self, when: datetime, high=24600.0):
        return app_module.IndexCandle(when.replace(tzinfo=IST), high - 10, high, high - 20, high - 5)

    @property
    def setting(self):
        return app_module._candle_entry_auto[77]

    async def test_it_does_nothing_outside_market_hours(self):
        for when in (datetime(2026, 8, 22, 10, 0), datetime(2026, 8, 20, 9, 10)):  # a Saturday; before 09:20
            out = await app_module._candle_entry_auto_step(self.USER, self.setting, now=when.replace(tzinfo=IST))
            self.assertEqual(out, "outside-window")
        out = await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 20, 15, 12, tzinfo=IST)
        )
        self.assertEqual(out, "too-late")

    async def test_a_running_campaign_is_left_alone(self):
        app_module._candle_entry_engines[77] = _Runtime(
            {"mother": {"timestamp": "2026-08-18T10:00:00+05:30"}}, running=True
        )
        out = await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 20, 11, 0, tzinfo=IST)
        )
        self.assertEqual(out, "busy")

    async def test_it_starts_the_measured_rule_on_the_first_new_box_high(self):
        started = []
        mother = self._bar(datetime(2026, 8, 20, 10, 30))

        async def finder():
            return mother, []

        async def starter(payload):
            started.append(payload)

        out = await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 20, 10, 40, tzinfo=IST), find_mother=finder, start=starter
        )
        self.assertEqual(out, "started")
        p = started[0]
        self.assertEqual(p.mother_mode, "box")
        self.assertEqual(p.timeframe, "5m")
        self.assertEqual((p.box_bars, p.box_position, p.ce_offset_steps, p.strike_at), (278, 0.25, -2, "each_buy"))
        self.assertEqual(
            (p.expiry_rule, p.target_fraction, p.trailing_target, p.intraday_close), ("monthly", 0.25, True, False)
        )
        self.assertEqual(p.mode, "paper")
        self.assertEqual(self.setting["last_mother"], mother.timestamp.isoformat())
        self.assertTrue(self._saved)

    async def test_a_mother_whose_bar_has_not_closed_waits(self):
        mother = self._bar(datetime(2026, 8, 20, 10, 30))

        async def finder():
            return mother, []

        out = await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 20, 10, 33, tzinfo=IST), find_mother=finder, start=None
        )
        self.assertEqual(out, "waiting-for-candle")

    async def test_the_same_mother_is_never_traded_twice(self):
        mother = self._bar(datetime(2026, 8, 20, 10, 30))
        self.setting["last_mother"] = mother.timestamp.isoformat()

        async def finder():
            return mother, []

        out = await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 20, 11, 0, tzinfo=IST), find_mother=finder, start=None
        )
        self.assertEqual(out, "waiting-for-new-high")

    async def test_an_ended_campaign_is_logged_once_and_frees_the_chain_from_its_exit(self):
        status = {
            "mother": {"timestamp": "2026-08-18T10:00:00+05:30"},
            "contract": {"strike": 24300, "option_type": "CE"},
            "status": "CLOSED",
            "fills": [{"rung": 1}],
            "exit": {"timestamp": "2026-08-20T10:05:00+05:30", "reason": "trail"},
            "net_pnl": 1234.5,
        }
        app_module._candle_entry_engines[77] = _Runtime(status, running=False)
        self.setting["last_mother"] = "2026-08-18T10:00:00+05:30"
        asked = []

        async def finder():
            asked.append(1)
            raise app_module.HTTPException(status_code=404, detail="none yet")

        out = await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 20, 10, 20, tzinfo=IST), find_mother=finder, start=None
        )
        self.assertEqual(out, "waiting-for-new-high")
        self.assertEqual(self.setting["free_from"], "2026-08-20T10:05:00+05:30")
        self.assertEqual(len(self.setting["log"]), 1)
        self.assertEqual(self.setting["log"][0]["net"], 1234.5)
        # A second tick does not log it again.
        await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 20, 10, 21, tzinfo=IST), find_mother=finder, start=None
        )
        self.assertEqual(len(self.setting["log"]), 1)


class TheCampaignLogKeepsItsOwnClock(unittest.TestCase):
    """The 45-day log trim ran on the WALL clock: the route test above began
    failing on 3-Oct-2026 by date alone, and a campaign whose mother was more
    than 45 days old was trimmed the moment it was logged."""

    def _runtime(self, mother_ts):
        return _Runtime(
            {
                "mother": {"timestamp": mother_ts},
                "contract": {"strike": 24300, "option_type": "CE"},
                "status": "CLOSED",
                "fills": [{"rung": 1}],
                "exit": {"timestamp": "2026-08-20T10:05:00+05:30", "reason": "trail"},
                "net_pnl": 10.0,
            },
            running=False,
        )

    def test_the_trim_is_measured_from_the_tick(self):
        setting = {"log": [{"mother": "2026-05-01T09:15:00+05:30"}]}
        app_module._candle_auto_log_campaign(
            setting, self._runtime("2026-08-18T10:00:00+05:30"), datetime(2026, 8, 20, 10, 20, tzinfo=IST)
        )
        self.assertEqual([r["mother"] for r in setting["log"]], ["2026-08-18T10:00:00+05:30"])

    def test_an_old_mother_is_still_logged_when_its_campaign_ends(self):
        setting = {}
        app_module._candle_auto_log_campaign(
            setting, self._runtime("2026-06-01T10:00:00+05:30"), datetime(2026, 8, 20, 10, 20, tzinfo=IST)
        )
        self.assertEqual(len(setting["log"]), 1)


class AutoOneMotherOneTradeTests(unittest.IsolatedAsyncioTestCase):
    """ONE MOTHER, ONE TRADE, however the campaign ended.

    A campaign that did not end in profit used to put the same mother back
    and start again at once. Stress-tested 2026-08-21, that rule reads the
    SIGN of a campaign's P&L: the 02-Jan-2025 campaign nets +Rs 279.55, and
    Rs 1.86/unit of slippage flips it, fires the retry, and the retry expires
    worthless for -Rs 1,05,850. Over 40 perturbed replays the retry rule's
    median was Rs 92k with 31 holding one to expiry; never retrying was
    Rs 2.06L, none of them, and a 3% spread. Phil: "Change it to never retry
    (new high only)"."""

    USER = {"id": 78}

    def setUp(self):
        app_module._candle_entry_auto[78] = {"enabled": True, "mode": "paper"}
        app_module._candle_entry_auto_loaded.add(78)
        self._orig_save = app_module._save_candle_entry_auto

        async def fake_save(uid):
            pass

        app_module._save_candle_entry_auto = fake_save
        self._orig_broker = app_module._resolve_user_broker_client
        app_module._resolve_user_broker_client = lambda _u, allow_admin_fallback=True: (object(), "test")

    def tearDown(self):
        app_module._save_candle_entry_auto = self._orig_save
        app_module._resolve_user_broker_client = self._orig_broker
        app_module._candle_entry_engines.pop(78, None)
        app_module._candle_entry_auto.pop(78, None)

    @property
    def setting(self):
        return app_module._candle_entry_auto[78]

    def _ended(self, net, mother="2026-08-18T10:00:00+05:30", exit_at="2026-08-20T10:05:00+05:30"):
        status = {
            "mother": {"timestamp": mother},
            "contract": {"strike": 24300, "option_type": "CE"},
            "status": "CLOSED",
            "fills": [{"rung": 1}],
            "exit": {"timestamp": exit_at, "reason": "trail"},
            "net_pnl": net,
        }
        app_module._candle_entry_engines[78] = _Runtime(status, running=False)
        self.setting["last_mother"] = mother

    async def test_a_losing_campaign_does_not_retry_its_mother(self):
        self._ended(net=-1200.0)
        mother = app_module.IndexCandle(datetime(2026, 8, 18, 10, 0, tzinfo=IST), 24590, 24600, 24580, 24595)
        started = []

        async def finder():
            return mother, []  # the mother just traded comes back: refused

        async def starter(payload):
            started.append(payload)

        out = await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 20, 10, 20, tzinfo=IST), find_mother=finder, start=starter
        )
        self.assertEqual(out, "waiting-for-new-high")
        self.assertEqual(started, [])
        self.assertNotIn("retry_same", self.setting)

    async def test_a_no_buy_campaign_does_not_retry_either(self):
        self._ended(net=None)
        mother = app_module.IndexCandle(datetime(2026, 8, 18, 10, 0, tzinfo=IST), 24590, 24600, 24580, 24595)
        started = []

        async def finder():
            return mother, []

        async def starter(payload):
            started.append(payload)

        out = await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 20, 10, 20, tzinfo=IST), find_mother=finder, start=starter
        )
        self.assertEqual(out, "waiting-for-new-high")
        self.assertEqual(started, [])

    async def test_a_new_high_after_a_loss_starts_and_is_watched_from_itself(self):
        self._ended(net=-1200.0)
        fresh = app_module.IndexCandle(datetime(2026, 8, 20, 10, 30, tzinfo=IST), 24610, 24640, 24600, 24630)
        started = []

        async def finder():
            return fresh, []

        async def starter(payload):
            started.append(payload)

        out = await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 20, 11, 0, tzinfo=IST), find_mother=finder, start=starter
        )
        self.assertEqual(out, "started")
        self.assertEqual(started[0].mother_timestamp, "2026-08-20T10:30:00")
        self.assertEqual(started[0].watch_from, "")  # a new bar has no history to skip
        self.assertIsNone(self.setting["last_watch_from"])

    async def test_an_open_basket_is_never_freed_just_because_the_loop_stopped(self):
        """A restart clears the poll flag; it does not end a campaign.

        Phil's screen, 2026-08-21: the card read "waiting for the next new
        278-bar high" while two rungs were still held and marked minutes
        earlier. Freeing there would let a second campaign start on top of a
        live basket."""
        self._ended(net=None)
        runtime = app_module._candle_entry_engines[78]
        runtime.engine.__class__.status = "OPEN"  # the basket is still held
        started = []

        async def finder():
            raise AssertionError("a mother must not even be looked for")

        async def starter(payload):
            started.append(payload)

        out = await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 21, 11, 23, tzinfo=IST), find_mother=finder, start=starter
        )
        self.assertEqual(out, "busy")
        self.assertEqual(started, [])
        self.assertNotIn("free_from", self.setting)
        self.assertNotIn("log", self.setting)

    async def test_a_stored_retry_flag_from_the_old_rule_is_ignored(self):
        self._ended(net=-1200.0)
        self.setting["retry_same"] = True
        mother = app_module.IndexCandle(datetime(2026, 8, 18, 10, 0, tzinfo=IST), 24590, 24600, 24580, 24595)

        async def finder():
            return mother, []

        out = await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 20, 10, 20, tzinfo=IST), find_mother=finder, start=None
        )
        self.assertEqual(out, "waiting-for-new-high")
        self.assertNotIn("retry_same", self.setting)

    async def test_a_profitable_campaign_waits_for_a_new_high(self):
        self._ended(net=4500.0)
        mother = app_module.IndexCandle(datetime(2026, 8, 18, 10, 0, tzinfo=IST), 24590, 24600, 24580, 24595)

        async def finder():
            return mother, []  # the same mother comes back: refused

        out = await app_module._candle_entry_auto_step(
            self.USER, self.setting, now=datetime(2026, 8, 20, 10, 20, tzinfo=IST), find_mother=finder, start=None
        )
        self.assertEqual(out, "waiting-for-new-high")
        self.assertFalse(self.setting.get("retry_same"))


class ResolveBoxTests(unittest.IsolatedAsyncioTestCase):
    """watch_from: the look-back moment IS watch_from, and the watch starts there."""

    class _Adapter:
        def __init__(self, rows):
            self.rows = rows

        async def async_get_candles(self, _symbol, _timeframe, from_date, to_date):
            return [row for row in self.rows if from_date <= row.timestamp.date() <= to_date]

    def _day(self, day, highs):
        out = []
        for i, high in enumerate(highs):
            when = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=15, tzinfo=IST) + timedelta(
                minutes=5 * i
            )
            out.append(app_module.IndexCandle(when, high - 5, high, high - 10, high - 2))
        return out

    async def test_watch_from_keeps_the_old_high_as_mother_and_starts_the_watch_later(self):
        d1 = self._day(date(2026, 8, 3), [100 + i * 0.1 for i in range(75)])
        d2 = self._day(date(2026, 8, 4), [108 + i * 0.1 for i in range(75)])
        d3 = self._day(date(2026, 8, 5), [116 + i * 0.1 for i in range(22)] + [110 - i * 0.1 for i in range(53)])
        adapter = self._Adapter(d1 + d2 + d3)
        payload = app_module.CandleEntryBacktestPayload(
            mother_mode="box", mother_timestamp="", watch_from="2026-08-05T14:00", box_bars=150
        )
        mother, window, watch_from = await app_module._candle_entry_resolve_box(
            adapter, payload, "5m", datetime(2026, 8, 6, 9, 0, tzinfo=IST)
        )
        self.assertEqual(mother.timestamp, d3[21].timestamp)  # the box high, 11:00
        self.assertEqual(watch_from, datetime(2026, 8, 5, 14, 0, tzinfo=IST))
        self.assertEqual(
            window[-1].timestamp, datetime(2026, 8, 5, 14, 0, tzinfo=IST)
        )  # the box ends where the watch starts
        # watch_from at or before the mother is an ordinary start
        payload = app_module.CandleEntryBacktestPayload(
            mother_mode="box", mother_timestamp="", watch_from="2026-08-05T11:00", box_bars=150
        )
        _m, _w, wf = await app_module._candle_entry_resolve_box(
            adapter, payload, "5m", datetime(2026, 8, 6, 9, 0, tzinfo=IST)
        )
        self.assertIsNone(wf)
