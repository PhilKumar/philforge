"""A provisional day the broker contradicts must not live forever.

Phil, 2026-09-10: "Why 2 enteries for the same trade?"

The same 130-lot NIFTY 23950 PE appeared twice -- buy 250.53, sell 229.45 on
both -- under two dates:

    2026-09-08  historical_fifo  2 fills, 1 segment   net -2,859.18
    2026-09-09  live_day_fifo    3 fills, 2 segments  net -2,739.75

A live_day_fifo row is written from the day's own fills and can land on the
wrong date. The incremental backfill only ever WRITES days that have trades,
so nothing could ever clear the duplicate and every total counted it twice.

The sweep removes such a row only when all of these hold: the day is settled,
the row is provisional, the broker returned no trades for it, and the pull
actually covered that date.
"""

import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
DB = open(os.path.join(ROOT, "db.py"), encoding="utf-8").read()


def sweep() -> str:
    return APP.split("DROP A PROVISIONAL DAY THE BROKER DOES NOT HAVE")[1].split("return new_dates")[0]


def would_drop(date_str, *, source, today, from_date, in_broker_pull):
    """The sweep's four conditions, restated so the test is independent."""
    if date_str >= today or date_str < from_date:
        return False
    if in_broker_pull:
        return False
    return source == "live_day_fifo"


class TheConditions(unittest.TestCase):
    CASE = dict(today="2026-09-10", from_date="2026-09-01", in_broker_pull=False)

    def test_it_drops_the_duplicate(self):
        self.assertTrue(would_drop("2026-09-09", source="live_day_fifo", **self.CASE))

    def test_it_never_drops_a_settled_record(self):
        self.assertFalse(would_drop("2026-09-09", source="historical_fifo", **self.CASE))

    def test_it_never_drops_today(self):
        """Today's row is provisional BY DESIGN -- the day is not over."""
        self.assertFalse(would_drop("2026-09-10", source="live_day_fifo", **self.CASE))

    def test_it_never_drops_a_day_the_pull_did_not_cover(self):
        """Outside the window the broker was not asked, so silence means
        nothing."""
        self.assertFalse(would_drop("2026-08-20", source="live_day_fifo", **self.CASE))

    def test_it_keeps_a_day_the_broker_confirms(self):
        self.assertFalse(
            would_drop(
                "2026-09-09", source="live_day_fifo", today="2026-09-10", from_date="2026-09-01", in_broker_pull=True
            )
        )


class TheImplementationMatches(unittest.TestCase):
    def test_all_four_guards_are_present(self):
        block = sweep()
        self.assertIn("date_str >= today_str or date_str < from_date", block)
        self.assertIn("if date_str in daily_entries:", block)
        self.assertIn('!= "live_day_fifo"', block)

    def test_it_uses_a_targeted_delete(self):
        block = sweep()
        self.assertIn("delete_trade_history_entry_sync", block)
        self.assertNotIn("clear_trade_history", block, "never the wholesale wipe")

    def test_the_delete_is_scoped_to_one_user_and_one_day(self):
        block = DB.split("def delete_trade_history_entry_sync")[1].split("\ndef ")[0]
        self.assertIn("WHERE user_id = ? AND trade_date = ?", block)

    def test_the_in_memory_history_is_kept_in_step(self):
        """Leaving the dict behind would hand the caller a row it just deleted."""
        self.assertIn("history.pop(date_str, None)", sweep())

    def test_it_says_what_it_removed(self):
        self.assertIn("Dropped", sweep())

    def test_every_name_in_the_backfill_resolves(self):
        """The sweep first referenced refresh_from_date, which is assigned in a
        different function entirely."""
        tree = ast.parse(APP)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_backfill_trade_history")
        bound = {a.arg for a in fn.args.args}
        for n in ast.walk(fn):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                bound.add(n.id)
            if isinstance(n, ast.ExceptHandler) and n.name:
                bound.add(n.name)
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    bound.add(a.asname or a.name.split(".")[0])
        module = set()
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                module.add(n.name)
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        module.add(t.id)
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    module.add(a.asname or a.name.split(".")[0])
        used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        import builtins

        unresolved = sorted(used - bound - module - set(dir(builtins)))
        self.assertEqual(unresolved, [], f"unresolved: {unresolved}")


if __name__ == "__main__":
    unittest.main()
