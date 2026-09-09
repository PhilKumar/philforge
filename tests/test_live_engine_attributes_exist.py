"""Every `self.x` the order path touches must actually exist on the engine.

The bug this exists for: the compounding ladder was written against
`self.strategy_config`, which LiveEngine does not have -- the attribute is
`self.strategy`. Reading a key off it raised AttributeError at the moment an
order was being sized, so EVERY live entry would have failed.

Nothing caught it. The ladder's own tests matched the key names in the source
text, which were all correct; only the object they were read from was wrong.
This walks the syntax tree instead, so a name that does not exist is a failure
whether or not the surrounding text looks right.
"""

import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "engine", "live.py"), encoding="utf-8").read()
TREE = ast.parse(SRC)
CLS = next(n for n in ast.walk(TREE) if isinstance(n, ast.ClassDef) and n.name == "LiveEngine")

# Every attribute the class ever assigns, plus its methods and properties.
KNOWN = set()
for node in ast.walk(CLS):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        KNOWN.add(node.name)
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                KNOWN.add(t.attr)
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        t = node.target
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
            KNOWN.add(t.attr)


def _reads(method_name: str) -> set:
    fn = next(n for n in CLS.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method_name)
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            out.add(node.attr)
    return out


class TheOrderPathOnlyTouchesRealAttributes(unittest.TestCase):
    def test_entering_a_trade(self):
        """_enter_trade sizes and places the order. A bad name here is a
        failed entry with real money on the line."""
        unknown = sorted(_reads("_enter_trade") - KNOWN)
        self.assertEqual(unknown, [], f"LiveEngine has no such attribute(s): {unknown}")

    def test_the_ladder_reads_the_strategy_not_a_config_that_does_not_exist(self):
        self.assertNotIn("self.strategy_config", SRC)
        block = SRC.split("# COMPOUND AS THE BOOK GROWS")[1][:900]
        self.assertIn("self.strategy.get(", block)
        self.assertIn("self.banked_pnl", block)

    def test_loading_state(self):
        unknown = sorted(_reads("_load_state") - KNOWN)
        self.assertEqual(unknown, [], f"LiveEngine has no such attribute(s): {unknown}")

    def test_saving_state(self):
        unknown = sorted(_reads("_save_state") - KNOWN)
        self.assertEqual(unknown, [], f"LiveEngine has no such attribute(s): {unknown}")


if __name__ == "__main__":
    unittest.main()
