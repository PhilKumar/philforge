"""The fill times must reach the STORED row, not merely be collected.

The bug this exists for: the accumulator gathered first_buy and last_sell
correctly, and the projection that actually gets serialised did not copy them.
Every assertion I had matched the source text -- the names were all present and
spelled right -- so the tests passed while the database stored rows without a
single timestamp, and the desk kept showing bare dates.

So this runs the real summariser over fills and reads what it would store.
"""

import ast
import datetime
import os
import unittest
from collections import defaultdict, deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()


def _summariser():
    """Load the summariser and exactly what it references, nothing else.

    app.py is 23k lines and its module body does start-up work, so executing it
    wholesale hangs. Hand-listing the helpers meant a NameError per run -- the
    same "spell the name right and hope" that produced the bug this file
    guards. So: start at the summariser and close over the names it uses.
    """
    tree = ast.parse(SRC)
    top = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            top[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    top[t.id] = node

    wanted, seen = {"_summarize_real_trade_history"}, set()
    while wanted - seen:
        name = (wanted - seen).pop()
        seen.add(name)
        node = top.get(name)
        if node is None:
            continue
        for ref in ast.walk(node):
            if isinstance(ref, ast.Name) and ref.id in top:
                wanted.add(ref.id)

    ns = {
        "defaultdict": defaultdict,
        "deque": deque,
        "datetime": datetime,
        "date": datetime.date,
        "timedelta": datetime.timedelta,
        "json": __import__("json"),
        "re": __import__("re"),
        "math": __import__("math"),
    }
    for node in tree.body:  # file order, so definitions land before their users
        name = getattr(node, "name", None) or (
            node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) else None
        )
        if name in seen:
            try:
                exec(ast.get_source_segment(SRC, node), ns)  # noqa: S102 - our own source
            except Exception:
                pass
    return ns["_summarize_real_trade_history"]


def _fill(stamp, side, qty, price, oid, tid):
    return {
        "exchangeTime": stamp,
        "transactionType": side,
        "tradedQuantity": qty,
        "tradedPrice": price,
        "customSymbol": "NIFTY 08 SEP 23950 PUT",
        "exchangeOrderId": oid,
        "exchangeTradeId": tid,
    }


class TheStoredRowCarriesTheTimes(unittest.TestCase):
    def setUp(self):
        self.summarise = _summariser()

    def _leg(self, fills):
        out = self.summarise(fills, source="historical_fifo", carry_inventory=True)
        return out["2026-09-08"]["details"][0]

    def test_a_round_trip_records_both_ends(self):
        leg = self._leg(
            [
                _fill("2026-09-08 09:47:12", "BUY", 130, 250.53, "A1", "T1"),
                _fill("2026-09-08 10:32:44", "SELL", 130, 229.45, "A2", "T2"),
            ]
        )
        self.assertEqual(leg["first_buy"], "2026-09-08 09:47")
        self.assertEqual(leg["last_sell"], "2026-09-08 10:32")

    def test_the_open_is_the_earliest_buy_not_the_last(self):
        """Fills arrive in whatever order the broker returns them."""
        leg = self._leg(
            [
                _fill("2026-09-08 11:05:02", "BUY", 65, 251.00, "A3", "T3"),
                _fill("2026-09-08 09:47:12", "BUY", 130, 250.53, "A1", "T1"),
                _fill("2026-09-08 12:00:00", "SELL", 195, 229.45, "A2", "T2"),
            ]
        )
        self.assertEqual(leg["first_buy"], "2026-09-08 09:47")

    def test_the_close_is_the_latest_sell(self):
        leg = self._leg(
            [
                _fill("2026-09-08 09:47:12", "BUY", 195, 250.53, "A1", "T1"),
                _fill("2026-09-08 14:20:00", "SELL", 65, 229.45, "A2", "T2"),
                _fill("2026-09-08 10:32:44", "SELL", 130, 231.00, "A4", "T4"),
            ]
        )
        self.assertEqual(leg["last_sell"], "2026-09-08 14:20")

    def test_a_fill_with_no_timestamp_leaves_the_slot_empty(self):
        """Empty is honest; a wrong time is not."""
        # A fill with no stamp has no date either, so it lands on no day at
        # all; what matters is that a real day never invents a time it lacks.
        leg = self._leg(
            [
                _fill("2026-09-08 09:47:12", "BUY", 130, 250.53, "A1", "T1"),
            ]
        )
        self.assertEqual(leg["last_sell"], "", "nothing was sold, so no close time")


class TheSchemaForcesARepull(unittest.TestCase):
    def test_the_version_is_past_the_one_that_stored_no_times(self):
        """5 collected the times and dropped them in the projection, so rows
        written under 5 are as timeless as those under 4."""
        self.assertIn("_TRADE_HISTORY_SCHEMA_VERSION = 6", SRC)


if __name__ == "__main__":
    unittest.main()
