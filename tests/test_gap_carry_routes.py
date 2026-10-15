"""The Gap Carry routes' rules that do not need a broker.

Everything here is a refusal or a shape: which charts the page may ask for,
which clock times can actually be reached, that the live gate holds, that Start
and Backtest ask the same questions, and that a strike offset can never be
pointed at the out-of-the-money wing.
"""

import base64
import os
import unittest
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

os.environ.setdefault("PHILFORGE_PIN", "123456")
os.environ.setdefault("PHILFORGE_DB", "/tmp/philforge-gap-carry-routes.db")
os.environ.setdefault("PHILFORGE_USER_DATA_ROOT", "/tmp/philforge-gap-carry-routes-data")
os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())

from fastapi import HTTPException  # noqa: E402

import app as app_module  # noqa: E402

IST = app_module.IST


def _payload(**kw):
    base = dict(
        timeframe="5m",
        rsi_threshold=70.0,
        strike_offset_steps=4,
        lots=1,
        entry_time="15:10",
        exit_time="09:20",
        expiry_rule="weekly",
        mode="paper",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class ChartTests(unittest.TestCase):
    """The page may only offer charts the adapter can actually fetch."""

    def test_the_two_fetchable_charts_are_accepted(self):
        for tf in ("5m", "15m"):
            self.assertEqual(app_module._gap_carry_timeframe(tf), tf)

    def test_a_chart_the_adapter_cannot_source_is_refused(self):
        """The engine was measured on 10m too, but CascadeOptionsAdapter cannot
        fetch it. Offering it would fail at the first candle request."""
        for tf in ("10m", "30m", "1m", "1h"):
            with self.assertRaises(HTTPException) as ctx:
                app_module._gap_carry_timeframe(tf)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("5m", str(ctx.exception.detail))

    def test_the_offered_list_is_a_subset_of_what_the_engine_allows(self):
        import engine.gap_carry as gap_carry

        self.assertTrue(set(app_module._GAP_CARRY_TIMEFRAMES) <= set(gap_carry.TIMEFRAMES))


class ClockTests(unittest.TestCase):
    """A time the background loop never wakes for is not a setting, it is a trap."""

    def test_the_measured_clocks_are_accepted(self):
        self.assertEqual(app_module._gap_carry_clock("15:10", label="Entry time"), time(15, 10))
        self.assertEqual(app_module._gap_carry_clock("09:20", label="Exit time"), time(9, 20))

    def test_a_time_after_the_close_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            app_module._gap_carry_clock("15:40", label="Entry time")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("session", str(ctx.exception.detail))

    def test_a_time_before_the_open_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            app_module._gap_carry_clock("09:05", label="Exit time")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_junk_is_refused_with_the_field_named(self):
        with self.assertRaises(HTTPException) as ctx:
            app_module._gap_carry_clock("half past three", label="Entry time")
        self.assertIn("Entry time", str(ctx.exception.detail))


class ExpiryRuleTests(unittest.TestCase):
    def test_weekly_and_monthly_are_the_choices(self):
        self.assertEqual(app_module._gap_carry_expiry_rule("weekly"), "weekly")
        self.assertEqual(app_module._gap_carry_expiry_rule("monthly"), "monthly")

    def test_anything_else_is_refused(self):
        with self.assertRaises(HTTPException) as ctx:
            app_module._gap_carry_expiry_rule("biweekly")
        self.assertEqual(ctx.exception.status_code, 400)


class LiveGateTests(unittest.TestCase):
    """Live is refused at the same gate as its two siblings on this page."""

    def test_paper_is_allowed(self):
        self.assertEqual(app_module._gap_carry_trade_mode("paper"), "paper")

    def test_live_is_refused_with_the_reason_when_its_tag_is_not_open(self):
        """Gap Carry has a live path since 2026-08-30, so the reason changed:
        it is built and closed, not missing. The refusal rides on the SHARED
        executor's own per-strategy switch and never on the fib ladder's flag.

        PF_GAP_CARRY was let out on 2026-09-01, so the closed case is pinned
        by emptying the open set rather than by the gate simply being shut.
        The property under test is unchanged: however open the fib ladder is,
        it is not what decides this.
        """
        from unittest.mock import patch

        import engine.options_live_executor as executor_module

        original = app_module._FIB_TOUCH_LIVE_EXECUTION_ENABLED
        app_module._FIB_TOUCH_LIVE_EXECUTION_ENABLED = True
        try:
            with patch.object(executor_module, "OPTIONS_LIVE_OPEN_TAGS", frozenset()):
                with self.assertRaises(HTTPException) as ctx:
                    app_module._gap_carry_trade_mode("live")
            self.assertEqual(ctx.exception.status_code, 503)
            self.assertIn("built but disabled", str(ctx.exception.detail))
        finally:
            app_module._FIB_TOUCH_LIVE_EXECUTION_ENABLED = original

    def test_live_is_accepted_because_gap_carry_is_the_open_strategy(self):
        """2026-09-01: PF_GAP_CARRY is the one tag in the open set, and this
        is the assertion that says so out loud. It is real money -- if this
        ever fails, the door closed, and nobody should discover that from a
        campaign that quietly traded paper."""
        self.assertEqual(app_module._gap_carry_trade_mode("live"), "live")

    def test_the_gate_opens_only_when_the_shared_executor_is_enabled(self):
        original = app_module._OPTIONS_LIVE_EXECUTION_ENABLED
        app_module._OPTIONS_LIVE_EXECUTION_ENABLED = True
        try:
            self.assertEqual(app_module._gap_carry_trade_mode("live"), "live")
        finally:
            app_module._OPTIONS_LIVE_EXECUTION_ENABLED = original

    def test_a_nonsense_mode_is_a_400_not_a_503(self):
        with self.assertRaises(HTTPException) as ctx:
            app_module._gap_carry_trade_mode("wishful")
        self.assertEqual(ctx.exception.status_code, 400)


class ConfigTests(unittest.TestCase):
    def test_a_valid_payload_builds_the_engines_own_config(self):
        config = app_module._gap_carry_config(_payload(lots=3, rsi_threshold=72.0))
        self.assertEqual(config.timeframe, "5m")
        self.assertEqual(config.lots, 3)
        self.assertEqual(config.rsi_threshold, 72.0)
        self.assertEqual(config.rsi_ceiling_for_put, 28.0)
        self.assertEqual(config.entry_time, time(15, 10))

    def test_the_engines_refusal_reaches_the_user_as_a_400(self):
        with self.assertRaises(HTTPException) as ctx:
            app_module._gap_carry_config(_payload(rsi_threshold=99.0))
        self.assertEqual(ctx.exception.status_code, 400)


class StrikeTests(unittest.TestCase):
    """The offset is applied AGAINST the trade's direction, always."""

    def test_a_call_is_bought_below_spot_and_a_put_above(self):
        import engine.gap_carry as gap_carry

        config = app_module._gap_carry_config(_payload(strike_offset_steps=4))
        self.assertEqual(gap_carry.strike_for(24580, "CE", config), 24400)
        self.assertEqual(gap_carry.strike_for(24580, "PE", config), 24800)

    def test_the_payload_cannot_express_an_out_of_the_money_offset(self):
        field = app_module.GapCarryPaperStartPayload.model_fields["strike_offset_steps"]
        lows = [m for m in field.metadata if getattr(m, "ge", None) is not None]
        self.assertTrue(lows and lows[0].ge == 0, "a negative offset would buy the cheap OTM wing")


class PayloadShapeTests(unittest.TestCase):
    def test_backtest_asks_the_same_questions_as_start(self):
        """If these drift, the replay stops describing what Start would trade."""
        start = set(app_module.GapCarryPaperStartPayload.model_fields)
        back = set(app_module.GapCarryBacktestPayload.model_fields)
        # mode and broker say WHERE a carry's orders go, not what it trades.
        self.assertEqual(start - {"mode", "broker"}, back - {"lookback_days"})

    def test_defaults_are_the_measured_configuration(self):
        p = app_module.GapCarryPaperStartPayload()
        self.assertEqual(p.timeframe, "5m")
        self.assertEqual(p.rsi_threshold, 70.0)
        self.assertEqual(p.strike_offset_steps, 4)
        self.assertEqual(p.lots, 1)
        self.assertEqual(p.entry_time, "15:10")
        self.assertEqual(p.exit_time, "09:20")

    def test_lots_are_bounded_on_the_wire(self):
        import pydantic

        with self.assertRaises(pydantic.ValidationError):
            app_module.GapCarryPaperStartPayload(lots=0)
        with self.assertRaises(pydantic.ValidationError):
            app_module.GapCarryPaperStartPayload(lots=99)


class AutoRuleTests(unittest.TestCase):
    def test_automation_is_pinned_to_the_measured_configuration(self):
        """The console must not be able to leave an unattended loop on a setting
        nobody has replayed."""
        rule = app_module._GAP_CARRY_AUTO_RULE
        self.assertEqual(rule["timeframe"], "5m")
        self.assertEqual(rule["rsi_threshold"], 70.0)
        self.assertEqual(rule["strike_offset_steps"], 4)
        self.assertEqual(rule["entry_time"], "15:10")
        self.assertEqual(rule["exit_time"], "09:20")

    def test_the_auto_rule_is_itself_a_legal_payload(self):
        config = app_module._gap_carry_config(SimpleNamespace(**rule_ns()))
        self.assertEqual(config.timeframe, "5m")

    def test_underscore_keys_never_reach_the_page(self):
        public = app_module._gap_carry_auto_public({"enabled": True, "_seq": 4, "state": "holding"})
        self.assertEqual(public, {"enabled": True, "state": "holding"})


def rule_ns() -> dict:
    return dict(app_module._GAP_CARRY_AUTO_RULE)


class RegistrationTests(unittest.TestCase):
    """Miss one of these and a campaign is dropped by a kill, a save or a restart."""

    def test_every_route_is_registered(self):
        paths = {r.path for r in app_module.app.routes if "gap-carry" in getattr(r, "path", "")}
        self.assertEqual(
            paths,
            {
                "/api/gap-carry/paper/status",
                "/api/gap-carry/paper/start",
                "/api/gap-carry/paper/kill",
                "/api/gap-carry/backtest",
                "/api/gap-carry/backtests/latest",
                "/api/gap-carry/auto",
                # The chart and the two exports. A chart that is not registered
                # leaves a button on the page that 404s.
                "/api/gap-carry/paper/chart",
                "/api/gap-carry/backtests/latest/chart",
                # The 15:10 signal card, live from 15:00 (2026-10-15).
                "/api/gap-carry/signal-monitor",
                "/api/gap-carry/backtests/latest/export.csv",
                "/api/gap-carry/backtests/latest/export.json",
            },
        )

    def test_the_registry_joins_the_runtime_owners(self):
        app_module._gap_carry_engines[4242] = object()
        try:
            self.assertIn(4242, app_module._runtime_owner_ids())
        finally:
            app_module._gap_carry_engines.pop(4242, None)

    def test_the_control_summary_reports_the_strategy(self):
        summary = app_module._runtime_control_summary(999999)
        self.assertIn("gap_carry_running", summary)

    def test_a_viewer_may_read_but_the_writes_are_not_allowlisted(self):
        import auth

        self.assertIn("/api/gap-carry/", auth.VIEWER_SHARED_READ_PREFIXES)
        self.assertFalse(any("gap-carry" in str(p) for p in auth.VIEWER_WRITE_ALLOWLIST))


class IndicatorTests(unittest.TestCase):
    """The chart's EMA and RSI come from the RULE's own two functions.

    A chart recomputing its own EMA -- in the route, or in JavaScript -- agrees
    with the rule right up until one of them is changed, and then it is a
    picture of a rule nobody trades.
    """

    def _candles(self, n=60):
        import engine.gap_carry as gap_carry

        Candle = type(
            "C",
            (),
            {"__init__": lambda s, t, c: (setattr(s, "timestamp", t), setattr(s, "close", c), None)[2]},
        )
        base = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
        del gap_carry
        return [Candle(base + timedelta(minutes=5 * i), 24000.0 + i * 3) for i in range(n)]

    def test_the_series_agree_with_the_rules_own_maths(self):
        import engine.gap_carry as gap_carry

        rows = self._candles()
        config = app_module._gap_carry_config(_payload())
        out = gap_carry.indicator_series(rows, config)
        closes = [r.close for r in rows]
        self.assertAlmostEqual(out["ema"][-1]["v"], round(gap_carry._ema(closes, 20)[-1], 2), places=2)
        self.assertAlmostEqual(out["rsi"][-1]["v"], round(gap_carry._wilder_rsi(closes, 14)[-1], 2), places=2)

    def test_the_warm_up_is_null_not_a_number_the_rule_never_read(self):
        import engine.gap_carry as gap_carry

        out = gap_carry.indicator_series(self._candles(), app_module._gap_carry_config(_payload()))
        self.assertIsNone(out["ema"][0]["v"], "the EMA seed is one close, not an average of twenty")
        self.assertIsNone(out["ema"][18]["v"])
        self.assertIsNotNone(out["ema"][19]["v"])
        self.assertIsNone(out["rsi"][0]["v"])

    def test_the_thresholds_travel_with_the_series(self):
        """The renderer draws the two guide lines from these, so a changed RSI
        setting has to move them."""
        import engine.gap_carry as gap_carry

        out = gap_carry.indicator_series(self._candles(), app_module._gap_carry_config(_payload(rsi_threshold=72.0)))
        self.assertEqual(out["rsi_upper"], 72.0)
        self.assertEqual(out["rsi_lower"], 28.0)

    def test_no_candles_is_empty_not_an_error(self):
        import engine.gap_carry as gap_carry

        out = gap_carry.indicator_series([], app_module._gap_carry_config(_payload()))
        self.assertEqual(out["ema"], [])
        self.assertEqual(out["rsi"], [])
        self.assertEqual(out["rsi_upper"], 70.0)

    def test_every_stamp_is_epoch_seconds(self):
        """The renderer does arithmetic on `t`; an ISO string silently breaks it."""
        import engine.gap_carry as gap_carry

        out = gap_carry.indicator_series(self._candles(), app_module._gap_carry_config(_payload()))
        for row in out["ema"] + out["rsi"]:
            self.assertIsInstance(row["t"], int)


class ExportTests(unittest.TestCase):
    def test_a_floored_exit_is_flagged_in_the_spreadsheet(self):
        """A floor is not a price, and a CSV that does not say so reads as one."""
        rows = app_module._gap_carry_export_rows(
            {
                "positions": [
                    {
                        "session": "2026-08-03",
                        "side": "CE",
                        "strike": 24400,
                        "net": 5210.0,
                        "entry": {"premium": 240.0},
                        "exit": {"premium": 330.0, "priced": False},
                        "signal": {"rsi": 73.8},
                    }
                ]
            }
        )
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0]["exit_priced"], False)
        self.assertEqual(rows[0]["rsi"], 73.8)

    def test_an_empty_replay_exports_nothing_rather_than_a_header(self):
        self.assertEqual(app_module._gap_carry_export_rows({}), [])

    def test_a_bad_stamp_is_zero_not_a_crash(self):
        self.assertEqual(app_module._gap_carry_epoch("not a time"), 0)
        self.assertGreater(app_module._gap_carry_epoch("2026-08-03T15:10:00+05:30"), 0)


class ExpiryLookupTests(unittest.TestCase):
    """A contract settling tonight cannot carry an overnight position."""

    class _Broker:
        pass

    def _lookup(self, rows, rule="weekly"):
        original = app_module._fib_touch_expiry_source
        app_module._fib_touch_expiry_source = lambda _b, _s: lambda _on: rows
        try:
            return app_module._gap_carry_expiry_lookup(self._Broker(), rule)
        finally:
            app_module._fib_touch_expiry_source = original

    def test_tonights_expiry_is_skipped_for_the_next_one(self):
        session = date(2026, 3, 10)
        lookup = self._lookup([session, session + timedelta(days=7)])
        self.assertEqual(lookup(session), session + timedelta(days=7))

    def test_nothing_far_enough_out_is_none(self):
        session = date(2026, 3, 10)
        self.assertIsNone(self._lookup([session])(session))


class AdapterBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """The one boundary the rest of this file does not cross.

    Every other test here is a refusal or a shape, so all 28 of them passed
    while `_gap_carry_load_candles` called `async_get_candles` with its
    keyword-only dates passed positionally -- a TypeError that the loader's own
    `except Exception` then dressed up as a 503, so Start simply never started.
    This test drives the REAL CascadeOptionsAdapter, which is the only thing
    that can catch that signature drifting again.
    """

    class _StubDhan:
        def get_historical_data(self, security_id, exchange_segment, instrument_type, *args, **kwargs):
            import pandas as pd

            start = datetime(2026, 8, 20, 9, 15)
            index = pd.DatetimeIndex([start + timedelta(minutes=5 * i) for i in range(40)])
            base = [24000.0 + i for i in range(40)]
            return pd.DataFrame(
                {
                    "open": base,
                    "high": [b + 5 for b in base],
                    "low": [b - 5 for b in base],
                    "close": base,
                },
                index=index,
            )

    async def test_the_loader_actually_reaches_the_adapter(self):
        from engine.cascade_options import CascadeOptionsAdapter

        adapter = CascadeOptionsAdapter(self._StubDhan(), paper_only=True)
        rows = await app_module._gap_carry_load_candles(adapter, "5m", days=6)
        self.assertTrue(rows, "the loader returned nothing; Start would have had no candle to read")

    async def test_a_real_failure_still_becomes_a_503(self):
        class _Broken:
            def get_historical_data(self, *a, **k):
                raise RuntimeError("Dhan said no")

        from engine.cascade_options import CascadeOptionsAdapter

        adapter = CascadeOptionsAdapter(_Broken(), paper_only=True)
        with self.assertRaises(HTTPException) as ctx:
            await app_module._gap_carry_load_candles(adapter, "5m", days=6)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("Dhan said no", str(ctx.exception.detail))

    def test_start_and_the_replay_share_one_loader(self):
        """The replay used to fetch its own candles with the same wrong call."""
        import inspect

        src = inspect.getsource(app_module.gap_carry_backtest)
        self.assertIn("_gap_carry_load_candles", src)
        self.assertNotIn("async_get_candles", src)


if __name__ == "__main__":
    unittest.main()


class AutoStepExecutesTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_auto_step_actually_runs_to_a_verdict(self):
        """The whole loop, not a unit of it — because the loop was the bug.

        `_resolve_user_broker_client` is a plain function returning a tuple, and
        an `await` in front of it made EVERY tick raise "object tuple can't be
        used in 'await' expression" — silently, each 15 seconds, from at least
        25 Aug. The 25 Aug entry only happened because a manually started
        campaign's poll loop was still alive; 26 Aug, when the auto step was the
        only entry path, lost its trade. Under the old code this test dies with
        that exact TypeError before any verdict is returned.
        """
        import app as app_module

        orig = app_module._resolve_user_broker_client
        app_module._resolve_user_broker_client = lambda user, **kw: (None, "none")
        try:
            verdict = await app_module._gap_carry_auto_step({"id": 9944}, {})
        finally:
            app_module._resolve_user_broker_client = orig
        self.assertEqual(verdict, "no-broker")
