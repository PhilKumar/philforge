"""Option Builder paper strategies: an expired one closes, an open one grows.

Phil, 2026-10-15: "I cannot close it and it shows error.... Also I cannot add
some strategies or strikes to the existing portfolio strategies".

1. Strategy #1, a 9-Oct short straddle on the 13-Oct expiry, answered every
   Close with 502: an expired contract has no chain, so there was no price to
   close at. It settles as NSE settles it -- at the index's close on expiry
   day (NIFTY 22,721.95): SELL 22750 CE @ 98.60 and SELL 22550 PE @ 123.60
   both expired worthless, +Rs 14,443 on one lot of 65.
2. Add to portfolio always made a NEW strategy, so adding a strike to an
   opened one duplicated it (#3 is #2 again). New strikes now join the open
   strategy; more lots on a held leg average its entry.
"""

import unittest
from datetime import datetime
from pathlib import Path

import option_builder as ob

NOW = datetime(2026, 10, 15, 14, 30)


def _leg(side, strike, kind, expiry, entry, lots=1):
    return {
        "underlying": "NIFTY",
        "side": side,
        "strike": float(strike),
        "option_type": kind,
        "expiry": expiry,
        "lots": lots,
        "entry": entry,
    }


STRADDLE_13_OCT = {
    "id": 1,
    "name": "Short Straddle",
    "underlying": "NIFTY",
    "lot_size": 65,
    "status": "open",
    "legs": [_leg("SELL", 22750, "CE", "2026-10-13", 98.6), _leg("SELL", 22550, "PE", "2026-10-13", 123.6)],
}


class AnExpiredStrategySettles(unittest.TestCase):
    def test_the_expiry_value_is_the_intrinsic_at_the_close(self):
        self.assertEqual(ob.expiry_value(22750, "CE", 22721.95), 0.0)
        self.assertEqual(ob.expiry_value(22550, "PE", 22721.95), 0.0)
        self.assertEqual(ob.expiry_value(22700, "CE", 22721.95), 21.95)
        self.assertEqual(ob.expiry_value(22800, "PE", 22721.95), 78.05)

    def test_the_13_oct_straddle_closes_at_its_settlement(self):
        closed = ob.close_paper_basket(STRADDLE_13_OCT, {}, NOW, {"2026-10-13": 22721.95})
        self.assertEqual(closed["status"], "closed")
        self.assertEqual([leg["exit"] for leg in closed["legs"]], [0.0, 0.0])
        self.assertTrue(all(leg["settled_at_expiry"] for leg in closed["legs"]))
        self.assertEqual(closed["realised"], 14443.0)  # (98.60 + 123.60) x 65

    def test_a_live_leg_with_no_price_is_still_refused(self):
        live = {**STRADDLE_13_OCT, "legs": [_leg("SELL", 22900, "CE", "2026-10-19", 114.65)]}
        with self.assertRaises(ob.OptionBuilderError):
            ob.close_paper_basket(live, {}, NOW, {"2026-10-13": 22721.95})

    def test_the_close_route_settles_an_expiry_before_today(self):
        src = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
        route = src.split('@app.post("/api/option-builder/paper/{basket_id}/close")')[1].split("\n@app.")[0]
        self.assertIn("_ob_index_close_on", route)
        self.assertIn("< today", route)


class NewStrikesJoinTheOpenStrategy(unittest.TestCase):
    def setUp(self):
        self.straddle = {
            "id": 2,
            "name": "Short Straddle",
            "underlying": "NIFTY",
            "lot_size": 65,
            "status": "open",
            "legs": [_leg("SELL", 22900, "CE", "2026-10-19", 114.65), _leg("SELL", 22700, "PE", "2026-10-19", 99.0)],
        }

    def _new(self, side, strike, kind, price, lots=1, underlying="NIFTY"):
        return {**_leg(side, strike, kind, "2026-10-19", 0, lots), "underlying": underlying, "price": price}

    def test_a_hedge_is_added_and_the_old_legs_keep_their_entries(self):
        grown = ob.add_legs_to_basket(self.straddle, [self._new("BUY", 23200, "CE", 31.5)], NOW)
        self.assertEqual(len(grown["legs"]), 3)
        self.assertEqual([leg["entry"] for leg in grown["legs"]], [114.65, 99.0, 31.5])
        self.assertEqual(grown["id"], 2)

    def test_more_lots_on_a_held_leg_average_its_entry(self):
        grown = ob.add_legs_to_basket(self.straddle, [self._new("SELL", 22700, "PE", 40.25)], NOW)
        pe = grown["legs"][1]
        self.assertEqual((pe["lots"], pe["entry"]), (2, 69.62))

    def test_a_closed_strategy_or_another_index_is_refused(self):
        with self.assertRaises(ob.OptionBuilderError):
            ob.add_legs_to_basket({**self.straddle, "status": "closed"}, [self._new("BUY", 23200, "CE", 31.5)], NOW)
        with self.assertRaises(ob.OptionBuilderError):
            ob.add_legs_to_basket(self.straddle, [self._new("BUY", 60000, "CE", 31.5, underlying="BANKNIFTY")], NOW)

    def test_a_strike_with_no_price_is_refused(self):
        with self.assertRaises(ob.OptionBuilderError):
            ob.add_legs_to_basket(self.straddle, [self._new("BUY", 23200, "CE", 0)], NOW)

    def test_the_original_strategy_is_not_changed_in_place(self):
        ob.add_legs_to_basket(self.straddle, [self._new("SELL", 22700, "PE", 40.25)], NOW)
        self.assertEqual(self.straddle["legs"][1]["lots"], 1)


if __name__ == "__main__":
    unittest.main()
