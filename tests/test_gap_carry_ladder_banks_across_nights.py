"""Gap Carry's ladder counts every night the book has banked, not one campaign's.

2026-09-15: Auto runs +1 lot per +25% of Rs 1,00,000 banked, capped at 7 lots.
Live it never climbed. The engine sized from its own campaign's closed nights,
Auto starts a fresh campaign every night, and a restart dropped the ladder
settings -- so every night read "nothing banked" and bought one lot.

Over 2021-2026 on the corrected entry that is the difference between Rs 2,00,264
(one lot for ever) and Rs 10,78,451 (the ladder as configured).
"""

import asyncio
import os
import unittest
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")

import app  # noqa: E402
from engine.gap_carry import GapCarryConfig, GapCarryPosition  # noqa: E402
from engine.gap_carry_paper import HOLDING, GapCarryPaper  # noqa: E402

LADDER = dict(compound_step_pct=25.0, compound_base_capital=100000.0, compound_max_lots=7)


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float


def _falling_day(day: date) -> list:
    base = datetime.combine(day, time(9, 15))
    return [Candle(base + timedelta(minutes=5 * i), c, c, c, c) for i, c in enumerate(24500.0 - i for i in range(80))]


def _engine(**kw) -> GapCarryPaper:
    return GapCarryPaper(
        config=GapCarryConfig(**LADDER),
        option_premium_lookup=lambda *_a: 250.0,
        expiry_lookup=lambda _s: date(2026, 3, 17),
        lot_size_lookup=lambda _e: 65,
        **kw,
    )


def _closed(session: date, net: float) -> GapCarryPosition:
    pos = GapCarryPosition(
        session=session,
        side="PE",
        strike=24000,
        expiry=date(2026, 3, 17),
        lot_size=65,
        lots=1,
        signal=None,
        entry_timestamp=datetime.combine(session, time(15, 10)),
        entry_spot=24000.0,
        entry_premium=250.0,
    )
    pos.exit_timestamp = datetime.combine(session + timedelta(days=1), time(9, 20))
    pos.exit_premium = 250.0
    pos.charges = 0.0
    return pos, net


class AFreshCampaignSeesTheBooksBank(unittest.TestCase):
    def test_nothing_banked_is_one_lot(self):
        eng = _engine()
        eng.ingest({"5m": _falling_day(date(2026, 3, 10))})
        self.assertEqual(eng.status, HOLDING)
        self.assertEqual(eng.position.lots, 1)

    def test_fifty_thousand_banked_by_earlier_nights_is_three_lots(self):
        """Two rungs of Rs 25,000 on top of the base lot."""
        eng = _engine(banked_before=50000.0)
        eng.ingest({"5m": _falling_day(date(2026, 3, 10))})
        self.assertEqual(eng.position.lots, 3)
        self.assertEqual(eng.position.quantity, 195)

    def test_the_cap_holds(self):
        eng = _engine(banked_before=10_00_000.0)
        eng.ingest({"5m": _falling_day(date(2026, 3, 10))})
        self.assertEqual(eng.position.lots, 7)

    def test_the_status_says_what_the_next_carry_buys(self):
        ladder = _engine(banked_before=30000.0).get_status()["ladder"]
        self.assertEqual(ladder, {"banked": 30000.0, "next_lots": 2})


class ARestartKeepsTheLadder(unittest.TestCase):
    def test_the_settings_survive_to_dict_and_back(self):
        restored = GapCarryPaper.from_dict(_engine().to_dict())
        self.assertEqual(restored.config.compound_step_pct, 25.0)
        self.assertEqual(restored.config.compound_base_capital, 100000.0)
        self.assertEqual(restored.config.compound_max_lots, 7)


class _Store:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value


class TheBank(unittest.TestCase):
    def setUp(self):
        self.store = _Store()
        self.patch = patch.multiple(app._db_mod, get_app_state=self.store.get, set_app_state=self.store.set)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def _book(self, engine):
        asyncio.run(app._bank_gap_carry_nights(1, engine))

    def _history(self, engine, *nights):
        for session, gross in nights:
            pos, _ = _closed(session, gross)
            pos.exit_premium = 250.0 + gross / 65.0
            engine.history.append(pos)

    def test_a_closed_night_is_banked_once_however_often_it_is_saved(self):
        eng = _engine()
        self._history(eng, (date(2026, 3, 10), 6500.0))
        self._book(eng)
        self._book(eng)
        bank = asyncio.run(app._gap_carry_bank(1, "paper"))
        self.assertEqual(len(bank["nights"]), 1)

    def test_live_and_paper_never_share_a_bank(self):
        """Paper profit must not size a real order."""
        paper = _engine()
        self._history(paper, (date(2026, 3, 10), 65000.0))
        self._book(paper)
        live_bank = asyncio.run(app._gap_carry_bank(1, "live"))
        self.assertEqual(live_bank["nights"], {})

    def test_the_next_nights_campaign_climbs(self):
        """The case that was broken: a new campaign after a banked night."""
        first = _engine(executor=object())
        self._history(first, (date(2026, 3, 9), 26000.0))
        self._book(first)
        bank = asyncio.run(app._gap_carry_bank(1, "live"))
        second = _engine(executor=None)
        second.banked_before = app._gap_carry_banked_before(bank, second)
        self.assertGreaterEqual(second.banked_before, 25000.0)
        second.ingest({"5m": _falling_day(date(2026, 3, 10))})
        self.assertEqual(second.position.lots, 2)

    def test_a_campaign_is_not_credited_twice_for_its_own_nights(self):
        eng = _engine()
        self._history(eng, (date(2026, 3, 9), 26000.0))
        self._book(eng)
        bank = asyncio.run(app._gap_carry_bank(1, "paper"))
        self.assertEqual(app._gap_carry_banked_before(bank, eng), 0.0)
        self.assertAlmostEqual(eng.banked, float(eng.history[0].net), places=2)

    def test_the_bank_is_keyed_by_mode(self):
        self.assertTrue(app._gap_carry_bank_key(1, "live").endswith(":live"))
        self.assertTrue(app._gap_carry_bank_key(1, "anything").endswith(":paper"))


class EveryPathHandsTheBankIn(unittest.TestCase):
    def test_start_restore_and_save_all_use_it(self):
        import inspect

        self.assertIn("_gap_carry_banked_before(", inspect.getsource(app._start_gap_carry_campaign))
        self.assertIn("_gap_carry_banked_before(", inspect.getsource(app._restore_gap_carry_open_state))
        self.assertIn("_bank_gap_carry_nights(", inspect.getsource(app._save_gap_carry_open_state))

    def test_the_start_sizes_before_the_first_ingest(self):
        import inspect

        src = inspect.getsource(app._start_gap_carry_campaign)
        self.assertLess(src.index("engine.banked_before ="), src.index("engine.ingest"))


if __name__ == "__main__":
    unittest.main()
