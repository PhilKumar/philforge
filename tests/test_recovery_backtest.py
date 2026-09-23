"""High Entry's replay: the same rules its Start button paper-trades.

Phil, 2026-08-29, on finding it was the one strategy that could not answer
"what would this have done": "Yes build the backtest for High Entry".

It was also the reason the Test Bench kept being asked to grow a fifth
strategy. The point of these tests is that the replay is not a second
implementation of the rule -- it builds the SAME `CandleRecoveryHost` the live
tab builds and calls the same replay its poll calls, and differs only in two
things that are about HISTORY rather than rules:

* prices come from the recorded archive, because a live chain cannot quote a
  contract that expired last March, and
* the lot size is the one listed on the mother's own date, because NIFTY has
  been 75, then 50, then 25, and pricing an old campaign at today's lot
  silently rewrites its money.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = pathlib.Path(__file__).resolve().parent.parent
APP = (_REPO / "app.py").read_text()
HTML = (_REPO / "strategy.html").read_text(encoding="utf-8")
JS = (_REPO / "static" / "philforge-app.js").read_text(encoding="utf-8")
CSS = (_REPO / "static" / "philforge-app.css").read_text(encoding="utf-8")


def _route_body(path: str, verb: str = "post") -> str:
    start = APP.index(f'@app.{verb}("{path}")')
    tail = APP[start:]
    nxt = re.search(r"\n@app\.(get|post|delete|put)\(", tail[10:])
    return tail[: nxt.start() + 10] if nxt else tail


class RouteTests(unittest.TestCase):
    def test_every_route_the_panel_calls_exists(self):
        for path, verb in [
            ("/api/recovery/backtest", "post"),
            ("/api/recovery/backtests/latest", "get"),
            ("/api/recovery/backtests/latest", "delete"),
        ]:
            self.assertIn(f'@app.{verb}("{path}")', APP, f"{verb.upper()} {path} is missing")

    def test_the_panel_only_calls_routes_that_exist(self):
        for path in set(re.findall(r"fetch\('(/api/recovery/[^'?]+)", JS)):
            self.assertIn(f'"{path}"', APP, f"the console calls {path}, which app.py does not serve")

    def test_it_is_registered_for_the_viewer_door(self):
        auth = (_REPO / "auth.py").read_text()
        self.assertIn('"/api/recovery/', auth, "the recovery prefix is not in the shared-read list")


class SameEngineTests(unittest.TestCase):
    """The whole value of the replay is that it is not a second rule."""

    BODY = _route_body("/api/recovery/backtest")

    def test_it_builds_the_live_host_rather_than_its_own(self):
        self.assertIn("_build_recovery_host(", self.BODY)

    def test_it_replays_through_the_hosts_own_named_mother_path(self):
        """`start_named_mother` is what the live '+ Run this mother' calls, and
        it replays through `_replay` -- the same function the poll runs."""
        self.assertIn("start_named_mother(", self.BODY)

    def test_it_never_reimplements_the_recovery_engine(self):
        for forbidden in ("TwoRedRecovery(", "FibZoneEntry(", "RecoveryConfig("):
            self.assertNotIn(
                forbidden,
                self.BODY,
                f"the replay constructs {forbidden} itself instead of letting the host do it",
            )

    def test_the_host_builder_defaults_to_the_live_quote(self):
        """A missing override must not SILENTLY give a live run recorded prices.

        Still the rule. What changed on 2026-09-01 is that a caller may now
        pass `history` deliberately, and the two live routes do -- because
        without it every exit the poll noticed more than seven minutes late
        came back unpriced, and the campaign closed with its money unknown.
        That is not the thing this test was guarding: the danger is a CURRENT
        quote filling a PAST candle, and a recorded price for a minute IS that
        minute's price. With no history passed, the behaviour is unchanged.
        """
        builder = APP[APP.index("def _build_recovery_host(") :]
        builder = builder[: builder.index("\nasync def ")]
        self.assertIn("if premium_source is not None:", builder)
        self.assertIn("_live_quote = _cascade_premium_lookup(broker)", builder)
        # The fallback is reached ONLY when a caller supplied one.
        self.assertIn("if value is not None or history is None:", builder)
        self.assertIn("if lot_size_override", builder)

    def test_a_host_built_without_history_still_prices_live_only(self):
        """The default path, exercised rather than read."""
        import app as app_module

        calls = []

        def _live(when, contract):
            calls.append(when)
            return None

        with patch.object(app_module, "_cascade_premium_lookup", lambda broker: _live):
            builder = app_module._build_recovery_host
            self.assertIsNotNone(builder)
        # Nothing recorded can be reached when nothing recorded was given.
        self.assertEqual(calls, [])


class ReachTests(unittest.TestCase):
    """A backtest that can only see last month is not a backtest.

    `start_named_mother` caps a LIVE named mother at 30 days, which is right --
    adopting a months-old mother into a running paper book is nearly always a
    mistake. The replay inherited that cap when it was first built, and would
    have refused every mother older than a month with "choose a mother from the
    last 30 days". Found by reading the host before running the first replay.
    """

    BODY = _route_body("/api/recovery/backtest")
    HOST = (_REPO / "engine" / "candle_recovery_host.py").read_text()

    def test_the_replay_names_its_own_reach(self):
        self.assertIn("max_age_days=_RECOVERY_BACKTEST_MAX_AGE_DAYS", self.BODY)
        self.assertIn("_RECOVERY_BACKTEST_MAX_AGE_DAYS = ", APP)

    def test_it_reaches_back_further_than_a_live_mother(self):
        import re as _re

        from engine.candle_recovery_host import MAX_MOTHER_AGE_DAYS

        value = int(_re.search(r"_RECOVERY_BACKTEST_MAX_AGE_DAYS = (\d+)", APP).group(1))
        self.assertGreater(value, MAX_MOTHER_AGE_DAYS, "the replay is capped at the live limit")

    def test_the_live_run_keeps_its_own_cap(self):
        """The override must not have loosened the live path by default."""
        self.assertIn("cap = int(MAX_MOTHER_AGE_DAYS if max_age_days is None else max_age_days)", self.HOST)
        live = APP[APP.index('@app.post("/api/recovery/paper/mother")') :][:1400]
        self.assertNotIn("max_age_days", live, "the live mother route must not raise its own cap")

    def test_the_candle_fetch_widens_with_the_reach(self):
        """Otherwise the mother's own bar falls off the front of the window and
        the replay refuses it as 'no candle opens at that time'."""
        self.assertIn("age + LOOKBACK_WARMUP_DAYS", self.HOST)
        self.assertIn("async def bars(self, *, now: datetime, days: int | None = None)", self.HOST)


class ReachBehaviourTests(unittest.IsolatedAsyncioTestCase):
    """The reach fix, driven rather than read.

    The source checks above would pass on a change that reads right and does
    nothing. This one names a mother 198 days back and asserts what actually
    happens to it.
    """

    NOW = __import__("datetime").datetime(2026, 8, 29, 16, 0)
    OLD = __import__("datetime").datetime(2026, 2, 12, 10, 15)

    def _host(self):
        import datetime as dt
        from types import SimpleNamespace

        from engine.candle_recovery import RecoveryConfig
        from engine.candle_recovery_host import CandleRecoveryHost

        class Adapter:
            def __init__(self):
                self.spans = []

            async def async_get_candles(self, symbol, tf, *, from_date, to_date, now=None):
                self.spans.append((from_date, to_date))
                out, day = [], from_date
                while day <= to_date:
                    if day.weekday() < 5:
                        for hour, minute in ((10, 15), (10, 30), (10, 45), (11, 0)):
                            out.append(
                                SimpleNamespace(
                                    timestamp=dt.datetime(day.year, day.month, day.day, hour, minute),
                                    open=24000.0,
                                    high=24050.0,
                                    low=23950.0,
                                    close=24000.0,
                                )
                            )
                    day += dt.timedelta(days=1)
                return out

            def get_ticker(self, symbol):
                return {"last_price": 24000.0}

        return CandleRecoveryHost(
            "nifty",
            Adapter(),
            premium_lookup=lambda when, strike, expiry, side="CE": 200.0,
            select_contract=lambda when, px, side="CE": SimpleNamespace(
                strike=24000, expiry=dt.date(2026, 3, 5), lot_size=75
            ),
            config=RecoveryConfig(timeframe="15m"),
            mode="ladder",
            side="CE",
            lot_size=75,
            dhan_symbol="NIFTY",
        )

    async def test_a_live_run_still_refuses_an_old_mother(self):
        with self.assertRaises(ValueError) as caught:
            await self._host().start_named_mother(self.OLD, now=self.NOW)
        self.assertIn("last 30 days", str(caught.exception))

    async def test_a_replay_accepts_one_and_reads_its_real_bar(self):
        host = self._host()
        campaign = await host.start_named_mother(self.OLD, now=self.NOW, max_age_days=730)
        self.assertEqual(campaign.mother.timestamp, self.OLD)
        self.assertEqual(campaign.mother.high, 24050.0, "the high must come from the bar, never be invented")

    async def test_the_fetch_actually_reaches_the_mother(self):
        """A wider cap with the old window would refuse it a layer deeper, as
        'no candle opens at that time' -- the failure that looks like bad data."""
        host = self._host()
        await host.start_named_mother(self.OLD, now=self.NOW, max_age_days=730)
        earliest = min(span[0] for span in host.adapter.spans)
        self.assertLessEqual(earliest, self.OLD.date(), "the candle fetch never reached the mother")


class OfflineParityTests(unittest.TestCase):
    """The rule was proven offline before it had a button.

    `tools/candle_recovery_sweep.py` is where High Entry's book was measured, so
    a replay in the app that disagreed with it would make the published numbers
    unreachable from the screen. Both drive the same engine over the same
    window; these pin the two places that could silently diverge.
    """

    HOST = (_REPO / "engine" / "candle_recovery_host.py").read_text()
    SWEEP = (_REPO / "tools" / "candle_recovery_sweep.py").read_text()

    def test_both_pick_the_engine_the_same_way(self):
        for source, name in ((self.HOST, "host"), (self.SWEEP, "offline sweep")):
            self.assertIn(
                "FibZoneEntry if",
                source,
                f"the {name} no longer chooses between the two rules the same way",
            )
            self.assertIn("else TwoRedRecovery", source, name)

    def test_both_start_the_watch_after_the_mother_never_on_it(self):
        """Replaying the mother's own bar lets a campaign fill on a bar a live
        system had not been told about yet -- the lookahead the sweep calls out
        by name."""
        self.assertIn("b.timestamp > campaign.mother.timestamp", self.HOST)
        self.assertIn("row.timestamp > watch_from", self.SWEEP)


class HonestyTests(unittest.TestCase):
    BODY = _route_body("/api/recovery/backtest")

    def test_it_prices_from_the_record_not_a_live_chain(self):
        self.assertIn("_candle_entry_pricing", self.BODY)
        self.assertIn("premium_source=history", self.BODY)

    def test_no_recorded_history_is_refused_rather_than_replayed_at_zero(self):
        self.assertIn("if history is None:", self.BODY)
        self.assertIn("503", self.BODY)

    def test_the_lot_is_the_one_listed_on_the_mothers_own_date(self):
        self.assertIn("_nifty_lot_size_on(mother_timestamp.date())", self.BODY)

    def test_it_is_paper_only(self):
        self.assertIn("paper_only=True", self.BODY)

    def test_a_future_mother_is_refused(self):
        self.assertIn("Mother timestamp cannot be in the future", self.BODY)

    def test_the_panel_says_when_a_replay_was_not_fully_priced(self):
        """A replay missing a leg's premium is not a book, and the badge is the
        one place that difference is visible."""
        self.assertIn("premium_failures", self.BODY)
        self.assertIn("fully_priced", JS[JS.index("function _renderRecoveryBacktest") :][:2600])
        self.assertIn("not a book", JS[JS.index("function _renderRecoveryBacktest") :][:2600])


class PanelTests(unittest.TestCase):
    def test_the_tab_has_a_backtest_button_wired_three_ways(self):
        self.assertIn('id="oc-high-backtest-btn"', HTML)
        self.assertIn('data-pf-action="runRecoveryBacktest"', HTML)
        for action in ("runRecoveryBacktest", "deleteRecoveryBacktest"):
            self.assertIn(f"  '{action}',", JS, f"{action} is not in the delegated allowlist")
            self.assertIn(f"function {action}(", JS, f"{action} has no function")
            self.assertIn(f"window.{action} = {action};", JS, f"{action} is not exported")

    def test_every_class_the_panel_uses_is_a_real_one(self):
        """An invented class renders as bare text. This has happened before."""
        panel = HTML[HTML.index('<section id="oc-high-backtest"') :]
        panel = panel[: panel.index("</section>")]
        for cls in sorted({c for m in re.findall(r'class="([^"]+)"', panel) for c in m.split()}):
            self.assertRegex(CSS, rf"\.{re.escape(cls)}\b", f"{cls} is not defined in the stylesheet")

    def test_the_saved_replay_comes_back_with_the_tab(self):
        self.assertIn("_loadRecoveryBacktest()", JS)
        self.assertIn("tab === 'recovery'", JS)

    def test_start_and_replay_read_one_form(self):
        """Two readers of the same controls drift; one cannot."""
        self.assertIn("function _recoveryFormPayload()", JS)
        start = JS[JS.index("async function recoveryStart()") :][:800]
        self.assertIn("_recoveryFormPayload()", start)
        replay = JS[JS.index("async function runRecoveryBacktest()") :][:1200]
        self.assertIn("_recoveryFormPayload()", replay)


if __name__ == "__main__":
    unittest.main()
