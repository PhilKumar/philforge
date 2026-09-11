"""A book started from the page must still send its Telegram alerts.

2026-09-11: Phil re-deployed both live books at 09:12–09:13. From 09:15 every
event either book emitted raised `KeyError: 'seen'` — 4,386 of them by 09:59 —
and the 09:20 PE entry sent no alert. Both start routes seeded the alert cache
with the shape it had before it tracked announced trades, and
`_check_trade_alerts` reads `state["seen"]`. `_emit` swallows a raising
callback, so the trade went through and the silence went unnoticed.
"""

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

import app

SRC = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")


class NoStartSeedsTheOldShape(unittest.TestCase):
    def test_the_old_seed_is_gone_from_both_start_routes(self):
        code = "\n".join(ln for ln in SRC.splitlines() if not ln.strip().startswith(("#", '"', "BEFORE")))
        self.assertNotIn('= {"in_trade": False, "closed_count": 0}', code)

    def test_both_routes_use_the_reset(self):
        self.assertEqual(SRC.count("await _reset_alert_state_for_start(user_id, run_id, engine.closed_trades)"), 2)


class _Store:
    def __init__(self, initial=None):
        self.data = dict(initial or {})

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value


def _run(coro):
    return asyncio.run(coro)


class TheResetKeepsWhatWasAnnounced(unittest.TestCase):
    def setUp(self):
        app._alert_state.clear()

    def _with_store(self, store):
        return patch.multiple(app._db_mod, get_app_state=store.get, set_app_state=store.set)

    def test_after_a_start_an_event_does_not_raise(self):
        store = _Store()
        with self._with_store(store), patch.object(app.alerter, "alert", lambda *a, **k: None):
            _run(app._reset_alert_state_for_start(1, "PE_NoTarget", []))
            _run(app._check_trade_alerts("PE_NoTarget", "LIVE", {"type": "tick", "in_trade": True}, 1))

    def test_the_first_entry_after_a_start_is_announced(self):
        sent = []
        store = _Store()
        with self._with_store(store), patch.object(app.alerter, "alert", lambda t, b, **k: sent.append(t)):
            _run(app._reset_alert_state_for_start(1, "PE_NoTarget", []))
            _run(app._check_trade_alerts("PE_NoTarget", "LIVE", {"type": "tick", "in_trade": True, "positions": []}, 1))
        self.assertEqual(sent, ["Trade Entry"])

    def test_reloaded_history_is_not_announced_again(self):
        old = {
            "trading_symbol": "NIFTY 23700PE",
            "entry_time": "2026-09-10 09:25:09",
            "exit_time": "2026-09-10 15:26:36",
            "pnl": 1763.21,
        }
        sent = []
        store = _Store()
        with self._with_store(store), patch.object(app.alerter, "alert", lambda t, b, **k: sent.append(t)):
            _run(app._reset_alert_state_for_start(1, "PE_NoTarget", [old]))
            _run(
                app._check_trade_alerts(
                    "PE_NoTarget", "LIVE", {"type": "tick", "in_trade": False, "closed_trades": [old]}, 1
                )
            )
        self.assertEqual(sent, [], "a trade from yesterday is not today's exit")

    def test_what_was_announced_before_the_start_stays_announced(self):
        old = {"trading_symbol": "NIFTY 23950PE", "entry_time": "2026-09-08 09:20:00", "pnl": -2739.75}
        fingerprint = app._closed_trade_fingerprint(old)
        import json

        store = _Store(
            {
                "trade_alerts:" + app._alert_state_key(1, "PE_NoTarget"): json.dumps(
                    {"in_trade": True, "seen": [fingerprint]}
                )
            }
        )
        with self._with_store(store):
            _run(app._reset_alert_state_for_start(1, "PE_NoTarget", []))
        state = app._alert_state[app._alert_state_key(1, "PE_NoTarget")]
        self.assertIn(fingerprint, state["seen"])
        self.assertFalse(state["in_trade"], "a fresh start is flat")


if __name__ == "__main__":
    unittest.main()
