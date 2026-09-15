"""One broker-facing executor for the option strategies that had none.

Fib Boundary grew its own executor first, and everything learned there is
here: an order is not a fill, a rejection and an unknown outcome are different
answers, a stop belongs INSIDE the entry so the exchange retires it, and the
last inch to the exchange stays closed until the whole lifecycle is proven.

The rest of the option strategies -- High Entry, Gap Carry, Candle Entry,
Supertrend -- each accepted `mode: "live"` and quietly traded paper before
2026-08-30. They share this one executor instead of growing four more, so a
fix to the money path is a fix everywhere rather than three near-misses.

Fib Boundary is deliberately NOT migrated onto this. It is the strategy
closest to real money and its own executor is tested against its own ladder;
moving it for tidiness would risk the one path that is nearly ready. When this
module has been proven live, that consolidation is the natural next step.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any, Optional

# THE MASTER SWITCH, and it opens ALL FOUR strategies at once -- which is why
# it stays False and is no longer what anything is opened with. Every real
# order still goes through `_availability_guard`, but that asks
# `live_execution_open(tag)` below, not this directly. Like Fib Boundary's own
# flag it is a module constant and not a config value or an environment
# variable, so nothing can drift it open at runtime.
OPTIONS_LIVE_EXECUTION_ENABLED = False

# THE SWITCH ACTUALLY USED: one strategy at a time, keyed on the tag each
# engine already passes -- PF_GAP_CARRY, PF_HIGH_ENTRY, PF_CANDLE_ENTRY,
# PF_SUPERTREND. A strategy can be let out on its own evidence without lending
# its permission to the three beside it, and an order whose tag is not named
# here is refused exactly as if nothing were open.
#
# PF_GAP_CARRY opened 2026-09-01: it settles a position every session at
# 09:20, so it is the one strategy that produces a real fill and a real exit
# daily -- which is the evidence the other three still owe.
OPTIONS_LIVE_OPEN_TAGS: frozenset[str] = frozenset({"PF_GAP_CARRY"})


def live_execution_open(tag: str) -> bool:
    """Is real money allowed for the engine carrying this tag?

    Asked through a function and never captured into a local, so a caller
    holding an import-time copy of the master cannot answer stale.
    """
    if OPTIONS_LIVE_EXECUTION_ENABLED:
        return True
    return str(tag or "").strip().upper() in OPTIONS_LIVE_OPEN_TAGS


# A PLACEHOLDER TARGET, because Dhan will not take a Super Order without one.
# The first version of this sent targetPrice 0 -- at entry there is no honest
# target to name, and this engine does not invent prices. Scalp, the one part
# of this system with real live experience, already knew better: its live path
# forces both exit prices in with defaults and says why (app.py, "A Dhan Super
# Order needs both exit prices").
#
# So the target goes in at this multiple of the entry premium: far enough out
# that it is not an exit anyone is expecting to take, and replaced by the real
# one through `amend_bracket_target` the moment a target can be measured. It
# is a placeholder, not a decision -- and if it ever DID fill, it filled at ten
# times what the leg cost.
BRACKET_PLACEHOLDER_TARGET_MULTIPLE = 10.0


class ExecutionRefused(RuntimeError):
    """An order was decided but the executor would not send it."""


def _placement_error(result: Any) -> str:
    """What Dhan said wrong in its acknowledgement, or "" if it said nothing.

    A 200 from the order endpoint is not acceptance. The body can carry
    REJECTED, FAILED or no order id at all, and reading only the HTTP status
    turns a clean refusal into an unknown outcome.
    """
    if not isinstance(result, dict):
        return "" if result else "no response from the broker"
    status = str(result.get("orderStatus", result.get("status", ""))).upper()
    if status in ("REJECTED", "FAILED", "CANCELLED"):
        return str(result.get("remarks") or result.get("message") or result.get("rejectedReason") or status)
    if not result.get("orderId"):
        return str(result.get("message") or result)
    return ""


class OrderRejected(ExecutionRefused):
    """Dhan examined the order and said no. Nothing is working at the broker,
    so unlike an unknown outcome this is safe to retry on a later trigger."""


class OptionsPaperExecutor:
    """Records what would have been sent and sends nothing anywhere."""

    mode = "paper"
    is_live = False

    def __init__(self, tag: str = "PF") -> None:
        self.tag = str(tag)

    def buy(self, *, when, strike, expiry, option_type, quantity, premium, stop_price=None) -> dict:
        receipt = {
            "order_id": f"paper-{self.tag}-{when.strftime('%H%M%S')}-{int(strike)}{option_type}",
            "mode": "paper",
        }
        if stop_price:
            receipt["bracket_order_id"] = receipt["order_id"]
        return receipt

    def sell(self, *, when, strike, expiry, option_type, quantity) -> dict:
        return {
            "order_id": f"paper-{self.tag}-exit-{when.strftime('%H%M%S')}-{int(strike)}{option_type}",
            "mode": "paper",
            "status": "FILLED",
        }

    def amend_bracket_target(self, *, order_id, price) -> dict:
        return {"order_id": str(order_id), "amended": True, "mode": "paper"}

    def cancel_bracket(self, *, order_id) -> dict:
        return {"order_id": str(order_id), "cancelled": True, "mode": "paper"}


class OptionsLiveExecutor:
    """The real-money path, armed deliberately and closed by default."""

    mode = "live"
    is_live = True

    def __init__(
        self,
        broker: Any,
        symbol: str,
        *,
        armed: bool = False,
        product_type: str = "MARGIN",
        tag: str = "PF",
    ) -> None:
        self.broker = broker
        self.symbol = symbol
        self.armed = bool(armed)
        # One product for every leg. A SELL under a different product than its
        # BUY does not net at the broker; it opens a short. INTRADAY is squared
        # off by Dhan at ~15:20, so anything meant to be held asks for MARGIN.
        self.product_type = str(product_type).upper()
        self.tag = str(tag)[:20]
        # How long to wait for an order to resolve before calling it unknown.
        # An attribute rather than a constant so a test can ask the same
        # question in a second instead of twenty.
        self.verify_wait_sec = 20

    # ── guards ──────────────────────────────────────────────────────────────

    def _availability_guard(self) -> None:
        if not live_execution_open(self.tag):
            raise ExecutionRefused(
                "Live execution for this strategy is built but disabled until its fills, partial fills "
                "and restart reconciliation are proven against Dhan. Use Paper or Backtest."
            )

    def _guard(self) -> None:
        self._availability_guard()
        if not self.armed:
            raise ExecutionRefused(
                "Live execution is built but not armed. Watch a paper run first, then arm live "
                "deliberately -- no config value or environment variable opens it."
            )

    def _verify_super(self, order_id, *, max_wait_sec: int = 20) -> dict:
        """Ask the SUPER ORDER book what became of a bracketed entry.

        A super order does not live in the ordinary order book, so the plain
        fill check cannot see it -- it would answer UNKNOWN for an entry that
        filled perfectly, and an unknown entry freezes the strategy. Scalp has
        always read `get_super_orders` for these; this is the same reading.
        """
        fetch = getattr(self.broker, "get_super_orders", None)
        if not order_id or fetch is None:
            return {"status": "UNKNOWN", "message": "no order id or no super order book on this broker"}
        deadline = time.monotonic() + max(1, int(max_wait_sec))
        last: dict = {}
        while True:
            try:
                book = fetch() or []
            except Exception as exc:
                return {"status": "UNKNOWN", "message": str(exc)}
            for row in book:
                if str(row.get("orderId") or "") != str(order_id):
                    continue
                last = row
                status = str(row.get("orderStatus") or "").upper()
                try:
                    filled = int(float(row.get("filledQty") or 0))
                except (TypeError, ValueError):
                    filled = 0
                try:
                    avg = float(row.get("averageTradedPrice") or 0.0)
                except (TypeError, ValueError):
                    avg = 0.0
                if status in ("TRADED", "FILLED", "COMPLETE", "PART_TRADED") and filled > 0:
                    if avg <= 0:
                        # PROVEN ON A REAL ORDER, 2026-09-01: the super order
                        # book reports the entry TRADED with filledQty 65 and
                        # "TRADE CONFIRMED" -- and averageTradedPrice 0.0.
                        # Dhan does not fill that field in here. The ORDINARY
                        # order book resolves the same id and gives the real
                        # price (37.05 on that order), so the two books are
                        # asked for what each actually knows: this one for
                        # whether the bracket traded, that one for what it
                        # cost. Without this the engine books the QUOTE
                        # instead of the fill, which is the estimate that
                        # verifying fills exists to remove.
                        plain = self._verify(order_id, max_wait_sec=5)
                        try:
                            avg = float(plain.get("avg_price") or 0.0)
                        except (TypeError, ValueError):
                            avg = 0.0
                    return {"status": "FILLED", "filled_qty": filled, "avg_price": avg}
                if status in ("REJECTED", "CANCELLED", "FAILED"):
                    return {
                        "status": "REJECTED",
                        "filled_qty": filled,
                        "message": str(row.get("remarks") or row.get("omsErrorDescription") or status),
                    }
                break
            if time.monotonic() >= deadline:
                return {
                    "status": "TIMEOUT",
                    "filled_qty": 0,
                    "message": f"super order still {last.get('orderStatus') or 'unseen'} after {max_wait_sec}s",
                }
            time.sleep(1.0)

    def _verify(self, order_id, *, max_wait_sec: int = 20) -> dict:
        """Ask Dhan what became of the order. UNKNOWN is an answer too."""
        verify = getattr(self.broker, "verify_order_fill", None)
        if not order_id or verify is None:
            return {"status": "UNKNOWN", "message": "no order id or no verifier on this broker"}
        try:
            return verify(str(order_id), max_wait_sec=max_wait_sec)
        except Exception as exc:  # a status fetch that dies is an unknown, not a fill
            return {"status": "UNKNOWN", "message": str(exc)}

    @staticmethod
    def _expiry_str(expiry) -> str:
        return expiry.isoformat() if isinstance(expiry, (date, datetime)) else str(expiry)

    # ── orders ──────────────────────────────────────────────────────────────

    def buy(self, *, when, strike, expiry, option_type, quantity, premium, stop_price=None) -> dict:
        """Buy one leg, and carry its stop into the same order when given one.

        With `stop_price` this is a Dhan Super Order: entry, target and stop as
        one, with the exchange cancelling whichever leg the other does not
        take. That is the only way a stop cannot outlive the leg it protects.

        The target leg goes in at a PLACEHOLDER, not a decision: Dhan
        refuses a Super Order without one, and these strategies name their
        real exit from index geometry as the campaign develops.
        `amend_bracket_target` replaces it the moment that exit is measurable.
        See BRACKET_PLACEHOLDER_TARGET_MULTIPLE.
        """
        self._guard()
        bracketed = False
        if stop_price:
            order = self.broker.place_super_order(
                underlying=self.symbol,
                strike_price=int(strike),
                option_type=str(option_type),
                expiry=self._expiry_str(expiry),
                transaction_type="BUY",
                quantity=int(quantity),
                target_price=round(float(premium) * BRACKET_PLACEHOLDER_TARGET_MULTIPLE, 2),
                stop_loss_price=float(stop_price),
                order_type="MARKET",
                product_type=self.product_type,
                tag=f"{self.tag}_SO",
            )
            bracketed = True
        else:
            order = self.broker.place_option_order(
                underlying=self.symbol,
                strike_price=float(strike),
                option_type=str(option_type),
                expiry=self._expiry_str(expiry),
                transaction_type="BUY",
                quantity=int(quantity),
                product_type=self.product_type,
                tag=f"{self.tag}_BUY",
            )
        refused = _placement_error(order)
        if refused:
            # Dhan answered 200 and still said no. Scalp learned this one the
            # expensive way: without reading the response body, a rejection
            # walks on to the fill check, comes back UNKNOWN and freezes the
            # strategy -- when in fact nothing is working and the next trigger
            # could simply try again.
            raise OrderRejected(f"Dhan refused the buy: {refused}")
        order_id = order.get("orderId") if isinstance(order, dict) else getattr(order, "order_id", None)
        # The acknowledgement is not the fill. What the book carries has to be
        # what Dhan actually traded, or every rupee downstream is an estimate.
        # A BRACKETED entry is asked about in the SUPER ORDER book: it is not
        # in the ordinary one, and asking the wrong book answers UNKNOWN for an
        # entry that filled perfectly -- which freezes the strategy on its
        # first live trade.
        outcome = (
            self._verify_super(order_id, max_wait_sec=self.verify_wait_sec)
            if bracketed
            else self._verify(order_id, max_wait_sec=self.verify_wait_sec)
        )
        status = str(outcome.get("status") or "UNKNOWN").upper()
        if status == "FILLED":
            avg = float(outcome.get("avg_price") or 0.0)
            filled = int(outcome.get("filled_qty") or 0)
            receipt = {
                "order_id": str(order_id),
                "mode": "live",
                "traded_premium": avg if avg > 0 else None,
                "traded_quantity": filled if 0 < filled <= int(quantity) else int(quantity),
            }
            if bracketed:
                receipt["bracket_order_id"] = str(order_id)
            return receipt
        if status in ("REJECTED", "CANCELLED") and int(outcome.get("filled_qty") or 0) == 0:
            raise OrderRejected(f"Dhan {status.lower()} the buy: {outcome.get('message') or 'no reason given'}")
        raise RuntimeError(
            f"buy outcome unresolved (status={status}, filled={outcome.get('filled_qty') or 0}): "
            f"{outcome.get('message') or 'no detail'}"
        )

    def sell(self, *, when, strike, expiry, option_type, quantity) -> dict:
        """Close one leg at market, and say plainly what became of it."""
        self._availability_guard()
        order = self.broker.place_option_order(
            underlying=self.symbol,
            strike_price=float(strike),
            option_type=str(option_type),
            expiry=self._expiry_str(expiry),
            transaction_type="SELL",
            quantity=int(quantity),
            product_type=self.product_type,
            tag=f"{self.tag}_EXIT",
        )
        refused = _placement_error(order)
        if refused:
            return {"order_id": "", "mode": "live", "status": "REJECTED", "avg_price": None, "message": refused}
        order_id = order.get("orderId") if isinstance(order, dict) else getattr(order, "order_id", None)
        outcome = self._verify(order_id)
        status = str(outcome.get("status") or "UNKNOWN").upper()
        if status == "FILLED":
            avg = float(outcome.get("avg_price") or 0.0)
            return {
                "order_id": str(order_id),
                "mode": "live",
                "status": "FILLED",
                "avg_price": avg if avg > 0 else None,
            }
        if status in ("REJECTED", "CANCELLED") and int(outcome.get("filled_qty") or 0) == 0:
            return {"order_id": str(order_id), "mode": "live", "status": "REJECTED", "avg_price": None}
        # Money may be in motion. The caller must freeze rather than guess.
        return {
            "order_id": str(order_id),
            "mode": "live",
            "status": "UNKNOWN",
            "avg_price": None,
            "message": str(outcome.get("message") or ""),
        }

    def amend_bracket_target(self, *, order_id, price) -> dict:
        """Name a bracket's target now that it can honestly be measured."""
        self._availability_guard()
        if float(price) <= 0:
            raise ExecutionRefused("a bracket target needs a positive price")
        result = self.broker.modify_super_order(str(order_id), "TARGET_LEG", target_price=float(price))
        return {"order_id": str(order_id), "amended": True, "raw": result, "mode": "live"}

    def _bracket_leg_price(self, order_id, leg_name: str) -> Optional[float]:
        """What a bracket leg traded at, asking whichever book knows."""
        fetch = getattr(self.broker, "get_super_orders", None)
        if fetch is not None:
            try:
                for row in fetch() or []:
                    if str(row.get("orderId") or "") != str(order_id):
                        continue
                    for leg in row.get("legDetails") or []:
                        if str(leg.get("legName") or "").upper() != leg_name:
                            continue
                        for key in ("averageTradedPrice", "price"):
                            try:
                                value = float(leg.get(key) or 0.0)
                            except (TypeError, ValueError):
                                value = 0.0
                            if value > 0:
                                return value
            except Exception:
                pass
        plain = self._verify(order_id, max_wait_sec=3)
        try:
            value = float(plain.get("avg_price") or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        return value if value > 0 else None

    def cancel_bracket(self, *, order_id) -> dict:
        """Release a bracket's exit legs so the position can be sold.

        THE EXIT LEGS, NOT THE ENTRY. Cancelling ENTRY_LEG after the entry has
        traded comes back `DH-906 Order Has Traded` -- and the first version of
        this read that as "a leg already sold the position", told the engine to
        book the leg as settled, and left the target and stop STILL WORKING at
        Dhan against a position the engine now believed was closed. Proven on a
        real order on 2026-09-01: both legs sat PENDING over a flat position
        until they were cancelled by hand. That is a naked short waiting for a
        bad tick, and a ledger that disagrees with the account.

        A leg that reports TRADED really did sell the position, and that IS
        reported, so the caller books it instead of selling the same lot twice.
        """
        self._availability_guard()
        cancelled, traded_at = [], None
        for leg_name in ("TARGET_LEG", "STOP_LOSS_LEG"):
            try:
                result = self.broker.cancel_super_order(str(order_id), leg_name)
            except Exception as exc:
                # One leg failing must not leave the other one working.
                cancelled.append({"leg": leg_name, "error": str(exc)})
                continue
            raw = str((result or {}).get("orderStatus") or "").upper()
            message = str((result or {}).get("errorMessage") or "")
            if raw in ("TRADED", "FILLED", "COMPLETE") or "Has Traded" in message:
                traded_at = traded_at or self._bracket_leg_price(order_id, leg_name)
                cancelled.append({"leg": leg_name, "traded": True})
                continue
            cancelled.append({"leg": leg_name, "cancelled": True})
        if any(row.get("error") for row in cancelled):
            raise ExecutionRefused(f"a bracket leg would not cancel: {cancelled}")
        if traded_at is not None or any(row.get("traded") for row in cancelled):
            return {
                "order_id": str(order_id),
                "cancelled": False,
                "traded": True,
                "avg_price": traded_at,
                "legs": cancelled,
                "mode": "live",
            }
        return {"order_id": str(order_id), "cancelled": True, "legs": cancelled, "mode": "live"}


def build_executor(
    broker: Any,
    symbol: str,
    *,
    mode: str,
    armed: bool = True,
    product_type: str = "MARGIN",
    tag: str = "PF",
) -> Any:
    """Paper or live, chosen in one place so no route can get it wrong."""
    if str(mode).lower() == "live":
        return OptionsLiveExecutor(broker, symbol, armed=armed, product_type=product_type, tag=tag)
    return OptionsPaperExecutor(tag=tag)


def reconcile_live_orders(broker: Any, *, symbol: str, legs: list[dict]) -> dict:
    """Ask Dhan what happened to these legs while the process was down.

    BLOCKING -- call it through `asyncio.to_thread`.

    `legs` are what the engine BELIEVES it holds, each
    ``{order_id, bracket_order_id, strike, expiry, option_type, quantity}``.
    What comes back says which of them the broker has already sold, and
    whether the account agrees about the rest.

    Conservative on purpose, and asymmetric on purpose:

    * a bracket that TRADED means one of its legs sold the position, so the
      engine must book that leg rather than sell it again;
    * the account holding LESS than the engine expects is the dangerous
      direction -- something closed that the engine still thinks it owns --
      so it is reported as `short_by` and the caller freezes;
    * holding MORE is only noted. The Dhan account is shared across every
      strategy here, and a leg belonging to a different one is not this
      engine's business to close.
    """
    notes: list[str] = []
    settled: dict[str, float] = {}
    for leg in legs:
        bracket = str(leg.get("bracket_order_id") or "")
        if not bracket or bracket.startswith("paper-"):
            continue
        if bracket == str(leg.get("order_id") or ""):
            # A SUPER ORDER: Dhan gives the entry and its target/stop legs ONE
            # id, and that id's status is the ENTRY's. "TRADED" there means the
            # buy filled, not that anything sold -- on 2026-09-15 a restart read
            # the live Gap Carry 23350PE's own entry as its exit, froze the
            # campaign as "sold while the app was down", and would have skipped
            # the 09:20 sale of a position still in the account. Whether it sold
            # is answered by the position book below, which is the check that
            # can actually see it.
            notes.append(f"super order {bracket}: entry id, left to the position book")
            continue
        try:
            status = broker.get_order_status(bracket) or {}
        except Exception as exc:  # a status fetch that dies is not an answer
            notes.append(f"bracket {bracket} could not be checked at Dhan: {exc}")
            continue
        raw = str(status.get("orderStatus") or status.get("status") or "UNKNOWN").upper()
        if raw in ("TRADED", "FILLED", "COMPLETE", "CLOSED"):
            price = 0.0
            for key in ("averagePrice", "averageTradedPrice", "price"):
                try:
                    price = float(status.get(key) or 0.0)
                except (TypeError, ValueError):
                    price = 0.0
                if price > 0:
                    break
            settled[str(leg.get("order_id") or bracket)] = price
            notes.append(f"bracket {bracket} TRADED while the app was down; its leg is booked")
        elif raw == "UNKNOWN":
            notes.append(f"bracket {bracket} status UNKNOWN at Dhan; left standing")

    expected: dict[tuple, int] = {}
    for leg in legs:
        if str(leg.get("order_id") or "") in settled:
            continue
        if not leg.get("order_id") or str(leg["order_id"]).startswith("paper-"):
            continue
        key = (float(leg["strike"]), str(leg["expiry"]), str(leg["option_type"]))
        expected[key] = expected.get(key, 0) + int(leg.get("quantity") or 0)
    short_by: dict[str, int] = {}
    if expected:
        try:
            from broker.dhan import ScripMaster

            held: dict[str, int] = {}
            for pos in broker.get_positions() or []:
                sec = str(pos.get("securityId") or "")
                try:
                    qty = int(float(pos.get("netQty") or 0))
                except (TypeError, ValueError):
                    qty = 0
                if sec:
                    held[sec] = held.get(sec, 0) + qty
            for (strike, expiry, option_type), qty in expected.items():
                security_id = str(ScripMaster.lookup(symbol, strike, expiry, option_type))
                have = held.get(security_id, 0)
                label = f"{int(strike)}{option_type} {expiry}"
                if have < qty:
                    short_by[label] = qty - have
                    notes.append(f"Dhan holds {have} of the {qty} this engine expects on {label}")
                elif have > qty:
                    notes.append(f"Dhan holds {have} on {label}, more than this engine's {qty} -- not touched")
        except Exception as exc:
            # No position check is not the same as a clean one. Say so, and
            # let the caller decide; it must not read as "everything agrees".
            notes.append(f"could not compare the Dhan position book: {exc}")
            short_by["__unchecked__"] = 0
    return {"notes": notes, "settled": settled, "short_by": short_by}


__all__ = [
    "OPTIONS_LIVE_EXECUTION_ENABLED",
    "ExecutionRefused",
    "OptionsLiveExecutor",
    "OptionsPaperExecutor",
    "OrderRejected",
    "build_executor",
    "reconcile_live_orders",
]
