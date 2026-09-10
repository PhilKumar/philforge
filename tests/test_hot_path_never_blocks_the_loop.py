"""One event loop runs everything, so anything that blocks it delays the order.

WHAT THIS IS ABOUT. On 2026-09-10 a live PE entry signalled on the bar that
closed at 09:25:00 and the order was not accepted by Dhan until 09:25:16.7.
The order POST itself was async and unthrottled -- it was submitted at
09:25:14.478 and its response was not read until 09:25:16.721, because in
between the process was busy elsewhere:

    14.637  marketfeed/ohlc -> 429   (the topbar ticker, retrying, sleeping)
    16.157  GET nseindia.com -> 403  (the ticker's VIX lookup, blocking)
    16.476  GET nseindia.com/api/allIndices
    16.721  POST /v2/orders -> 200   (our order, finally read)

`/api/ticker` is an `async def`, and it called Dhan and NSE through the
SYNCHRONOUS client -- inside a pacing lock that `time.sleep()`s, with 429
retries that sleep again. A browser polling the topbar could therefore hold the
only event loop shut while a real order was in flight. The paper engines did the
same thing at every candle close: a 31-strike scan through the sync client, at
the same instant the live engine needed the loop to place its order.

So the rule this test enforces: in a coroutine, broker and market-data calls go
through the async client or through `asyncio.to_thread`. Never straight.

If this fails, do not delete the entry -- offload the call. `await
asyncio.to_thread(client.get_funds)` is the whole fix in most cases; where an
`async_*` twin exists, use it.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Methods on a broker client that make a blocking HTTP call.
BLOCKING_METHODS = {
    "get_ltp",
    "get_ltp_multi",
    "get_ltp_cached",
    "get_ltp_prices",
    "get_ohlc_multi",
    "get_quote_multi",
    "get_option_ltp",
    "get_historical_data",
    "get_funds",
    "get_funds_cached",
    "get_positions",
    "get_positions_cached",
    "get_order_book",
    "get_order_status",
    "place_order",
    "place_option_order",
    "place_sl_order",
    "cancel_order",
    "verify_order_fill",
}

# Module-level helpers that reach the network synchronously.
BLOCKING_FUNCTIONS = {"_fetch_nse_vix", "_get_prev_close"}

# Calling one of these means the work was handed to a thread, which is the fix.
OFFLOADERS = {"to_thread", "run_in_executor", "run_sync"}

FILES = [
    "app.py",
    "engine/live.py",
    "engine/paper_trading.py",
    "engine/gap_carry.py",
    "engine/gap_carry_paper.py",
    "engine/supertrend_entry.py",
    "engine/supertrend_paper.py",
]


def _receiver(attribute: ast.Attribute) -> str:
    """Dotted name the method is called ON, e.g. `self.dhan` in self.dhan.get_ltp()."""
    parts: list[str] = []
    cur = attribute.value
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


class _Blocking(ast.NodeVisitor):
    def __init__(self) -> None:
        # None marks a plain `def`: a sync helper runs in whatever thread called
        # it, so a blocking call there is not this test's business.
        self.enclosing: list[str | None] = []
        self.found: list[str] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.enclosing.append(node.name)
        self.generic_visit(node)
        self.enclosing.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.enclosing.append(None)
        self.generic_visit(node)
        self.enclosing.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.enclosing.append(None)
        self.generic_visit(node)
        self.enclosing.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if name in OFFLOADERS:
            return  # handed to a thread — and so is everything inside it
        inside = self.enclosing[-1] if self.enclosing else None
        if inside:
            if isinstance(node.func, ast.Attribute):
                receiver = _receiver(node.func)
                on_a_broker = any(word in receiver.lower() for word in ("dhan", "client", "broker"))
                if name in BLOCKING_METHODS and on_a_broker:
                    self.found.append(f"line {node.lineno}: {inside}() calls {receiver}.{name}()")
            elif name in BLOCKING_FUNCTIONS:
                self.found.append(f"line {node.lineno}: {inside}() calls {name}()")
        self.generic_visit(node)


@pytest.mark.parametrize("relative_path", FILES)
def test_no_blocking_broker_call_inside_a_coroutine(relative_path):
    path = ROOT / relative_path
    if not path.exists():
        pytest.skip(f"{relative_path} is not in this checkout")
    visitor = _Blocking()
    visitor.visit(ast.parse(path.read_text()))
    assert not visitor.found, (
        f"{relative_path} blocks the event loop while an order may be in flight:\n  "
        + "\n  ".join(visitor.found)
        + "\n\nUse the async_* twin, or wrap the call in `await asyncio.to_thread(...)`."
    )


def test_the_detector_still_detects():
    """A test that can no longer fail is worse than no test."""
    source = """
import asyncio
async def endpoint(client):
    return client.get_funds()
"""
    visitor = _Blocking()
    visitor.visit(ast.parse(source))
    assert len(visitor.found) == 1, visitor.found

    fixed = """
import asyncio
async def endpoint(client):
    return await asyncio.to_thread(client.get_funds)
"""
    visitor = _Blocking()
    visitor.visit(ast.parse(fixed))
    assert visitor.found == []

    sync_helper_is_fine = """
def helper(client):
    return client.get_funds()
"""
    visitor = _Blocking()
    visitor.visit(ast.parse(sync_helper_is_fine))
    assert visitor.found == []
