"""A route may not call a helper that lives in another module.

`/api/live/index-chart` called `_get_instrument_map()`, which belongs to
engine/live.py. app.py has `INSTRUMENT_MAP` instead. Nothing caught it: the unit
tests matched strings in the source, CI never imports the route, and Playwright
never opens that chart. It reached production and answered 500 on the first
click (Phil, 2026-09-09: "Chart not working").

So this walks the routes that were added with copied code and checks every bare
name they use actually resolves in app's namespace. It is a cheap stand-in for
calling them, and it fails on exactly the mistake that got through.
"""

import ast
import builtins
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
TREE = ast.parse(SOURCE)

# Names bound at module level: assignments, defs, classes and imports.
MODULE_NAMES = {t.id for st in TREE.body if isinstance(st, ast.Assign) for t in st.targets if isinstance(t, ast.Name)}
MODULE_NAMES |= {st.target.id for st in TREE.body if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)}
MODULE_NAMES |= {n.name for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
MODULE_NAMES |= {
    a.asname or a.name.split(".")[0]
    for st in TREE.body
    if isinstance(st, (ast.Import, ast.ImportFrom))
    for a in st.names
}
for _st in TREE.body:  # names bound inside try/except import blocks
    if isinstance(_st, ast.Try):
        for _inner in ast.walk(_st):
            if isinstance(_inner, (ast.Import, ast.ImportFrom)):
                MODULE_NAMES |= {a.asname or a.name.split(".")[0] for a in _inner.names}
            elif isinstance(_inner, ast.Assign):
                MODULE_NAMES |= {t.id for t in _inner.targets if isinstance(t, ast.Name)}

ROUTES = ("live_index_chart", "live_runs")


def _bound_locally(fn: ast.AST) -> set[str]:
    out = {a.arg for a in getattr(fn.args, "args", [])} | {a.arg for a in getattr(fn.args, "kwonlyargs", [])}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                out |= {e.id for e in ast.walk(t) if isinstance(e, ast.Name)}
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            out |= {e.id for e in ast.walk(node.target) if isinstance(e, ast.Name)}
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            out |= {e.id for e in ast.walk(node.optional_vars) if isinstance(e, ast.Name)}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            out |= {a.arg for a in node.args.args}
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(node.name)
    return out


class EveryNameARouteUsesExists(unittest.TestCase):
    def test_the_routes_added_this_week_resolve(self):
        for name in ROUTES:
            with self.subTest(route=name):
                fn = next(
                    (
                        n
                        for n in ast.walk(TREE)
                        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
                    ),
                    None,
                )
                self.assertIsNotNone(fn, f"{name} is gone — update ROUTES")
                local = _bound_locally(fn)
                used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
                missing = sorted(
                    n for n in used if n not in local and n not in MODULE_NAMES and not hasattr(builtins, n)
                )
                self.assertEqual(missing, [], f"{name} uses names that do not exist in app.py: {missing}")

    def test_the_helper_that_broke_it_is_not_back(self):
        """_get_instrument_map lives in engine/live.py; app.py has INSTRUMENT_MAP."""
        self.assertNotIn("_get_instrument_map", SOURCE)
        self.assertIn("INSTRUMENT_MAP = {", SOURCE)


if __name__ == "__main__":
    unittest.main()
