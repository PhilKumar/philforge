"""engine/candle_recovery_host.py -- one poll cycle of the recovery paper run.

engine/candle_recovery.py decides; this module feeds it.  A cycle fetches the
closed index candles and REPLAYS every campaign from its own mother, then
reports what changed.  A plain object with an async ``poll``, so app.py owns the
scheduling and tests can drive it a tick at a time.

REPLAY, NEVER STEP.  Both engines are pure over (mother, bars), so a poll
rebuilds each campaign from scratch rather than keeping a parallel state
machine.  A restart, a redeploy or a missed tick therefore cannot leave the book
disagreeing with the chart -- the bars are the only state that matters.  It is
the same discipline engine/fib_space_live.py uses, for the same reason.

THE SESSION GATE.  Dhan's rate budget is account-wide and a loop polling through
the night once produced a 4 AM 429 storm that starved the live engine, so a
cycle outside NSE cash hours does no I/O at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from engine.candle_recovery import (
    TIMEFRAME_MINUTES,
    FibZoneEntry,
    RecoveryBar,
    RecoveryConfig,
    TwoRedRecovery,
    mirror_bar,
    mirror_bars,
    unmirror_price,
)
from engine.cascade_options import is_nse_cash_session

MODES = ("ladder", "fib-zone")
SIDES = ("CE", "PE")

# A campaign is replayed from its own mother, so the fetch must reach back far
# enough to cover the oldest one still running -- not just far enough to see
# today.  Too short a window silently truncates an old campaign's replay.
MIN_LOOKBACK_DAYS = 20
LOOKBACK_WARMUP_DAYS = 5
MAX_FETCH_DAYS = 60

# How far back a mother may be named.  Beyond this the ladder has usually
# already run, and its fills cannot be quoted now.
MAX_MOTHER_AGE_DAYS = 30


def bars_from_candles(candles) -> list[RecoveryBar]:
    """Adapter candles to engine bars, flattened to naive IST.

    Adapter candles are TZ-AWARE and the engine's bars are naive; `aware ==
    naive` silently returns False, so every timestamp is flattened once here
    rather than compared across the two.
    """
    out: list[RecoveryBar] = []
    for candle in sorted(candles, key=lambda row: row.timestamp):
        stamp = candle.timestamp
        stamp = stamp.replace(tzinfo=None) if stamp.tzinfo else stamp
        out.append(RecoveryBar(stamp, float(candle.open), float(candle.high), float(candle.low), float(candle.close)))
    return out


@dataclass
class RecoveryCampaign:
    """One named mother and everything replayed from it.

    SIDE BELONGS TO THE CAMPAIGN, not to the run (Phil, 2026-09-23: "I need
    both CE and PE side by side").  A call campaign reads the bars as they
    come; a put campaign reads them mirrored.  Holding the side here is what
    lets one host carry both at once -- and what lets the same mother candle
    be traded from both ends, which a shared host-level side made impossible.
    """

    campaign_id: str
    mother: RecoveryBar
    mode: str
    started_at: datetime
    engine: Any = None  # the most recent replay
    side: str = "CE"

    @property
    def mirrored(self) -> bool:
        return str(self.side).upper() == "PE"

    @property
    def status(self) -> str:
        return getattr(self.engine, "status", "PENDING")

    @property
    def booked_net(self) -> Optional[float]:
        return getattr(self.engine, "booked_net", None)


@dataclass
class PollReport:
    at: datetime
    skipped: Optional[str] = None
    error: Optional[str] = None
    bars: int = 0
    fills: list = field(default_factory=list)
    exits: list = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.fills or self.exits)

    def to_dict(self) -> dict:
        return {
            "at": self.at.isoformat(),
            "skipped": self.skipped,
            "error": self.error,
            "bars": self.bars,
            "fills": list(self.fills),
            "exits": list(self.exits),
        }


class CandleRecoveryHost:
    """The recovery strategy over one symbol, one timeframe, one mode."""

    def __init__(
        self,
        symbol: str,
        adapter,
        *,
        premium_lookup,
        select_contract: Callable[[datetime, float], object],
        config: RecoveryConfig,
        mode: str = "ladder",
        side: str = "CE",
        lot_size: int = 75,
        dhan_symbol: Optional[str] = None,
        min_lookback_days: int = MIN_LOOKBACK_DAYS,
        order_book=None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if str(side).upper() not in SIDES:
            raise ValueError(f"side must be one of {SIDES}")
        self.symbol = symbol
        self.adapter = adapter
        self.dhan_symbol = dhan_symbol or symbol.upper()
        self.premium_lookup = premium_lookup
        self.select_contract = select_contract
        self.config = config
        self.mode = mode
        # A PE campaign runs the SAME engine on MIRRORED bars: swing-low mother,
        # two greens, buy at the second green's low, stop above the entry candle's
        # high. One implementation of the rules, flipped through zero.
        self.side = str(side).upper()
        self.lot_size = int(lot_size)
        self.min_lookback_days = min_lookback_days
        self.campaigns: dict[str, RecoveryCampaign] = {}
        # PRICES ARE REMEMBERED, NOT RE-ASKED.  The broker's lookup only serves a
        # CURRENT quote -- ask it for a fill from an hour ago and it correctly
        # refuses.  But a poll replays the whole campaign, so without this every
        # past fill would come back unpriced on the next tick and the ledger the
        # recovery target is measured against would evaporate.  A premium seen
        # live is kept, keyed by the minute it belonged to.
        self._premium_cache: dict[tuple, float] = {}
        self.last_poll: Optional[datetime] = None
        self.last_report: Optional[PollReport] = None
        # REAL ORDERS, IF THIS RUN HAS ANY. The replay decides; the book
        # remembers what was actually sent for each decision, so a second
        # replay of the same trade cannot buy it twice. None for paper runs,
        # which is every run until live is proven.
        self.order_book = order_book

    # ── the engine's two callbacks ──────────────────────────────────────────

    @property
    def mirrored(self) -> bool:
        """The side a NEW campaign takes by default. Each campaign knows its own."""
        return self.side == "PE"

    def _contract_for(self, when: datetime, index_price: float, side: str):
        # On a PE campaign the engine hands back a mirrored (negative) index
        # price; the chain must be asked for the real spot.
        spot = -float(index_price) if str(side).upper() == "PE" else float(index_price)
        try:
            picked = self.select_contract(when, spot, side)
        except Exception:
            return None
        return int(picked.strike), picked.expiry

    def _premium_for(self, when: datetime, strike: int, expiry, side: str) -> Optional[float]:
        # SIDE IS PART OF THE KEY. A call and a put on the same strike and
        # expiry are two different contracts at two different prices; without
        # the side in the key a CE campaign and a PE campaign running together
        # would read each other's premiums and both books would be wrong.
        key = (when.replace(second=0, microsecond=0), int(strike), str(expiry), str(side).upper())
        cached = self._premium_cache.get(key)
        if cached is not None:
            return cached
        try:
            value = self.premium_lookup(when, strike, expiry, side)
        except Exception:
            return None
        if value is not None:
            self._premium_cache[key] = float(value)
        return value

    # ── fetching ────────────────────────────────────────────────────────────

    def lookback_days(self, *, now: datetime) -> int:
        oldest = min((c.mother.timestamp for c in self.campaigns.values()), default=None)
        if oldest is None:
            return self.min_lookback_days
        return max(self.min_lookback_days, (now.date() - oldest.date()).days + LOOKBACK_WARMUP_DAYS)

    async def _fetch(self, *, now: datetime, days: int | None = None) -> list:
        """Closed candles over the needed window, in slices the broker accepts.

        `days` is normally derived from the oldest live campaign. A backtest
        names its own, because the mother it wants is older than any campaign
        this host is carrying -- with the live figure the mother's bar falls
        off the front of the window and reads as "no candle opens at that time".
        """
        days = int(self.lookback_days(now=now) if days is None else days)
        by_stamp: dict = {}
        end = now.date()
        remaining = days
        while remaining > 0:
            span = min(remaining, MAX_FETCH_DAYS)
            start = end - timedelta(days=span)
            for candle in await self.adapter.async_get_candles(
                self.dhan_symbol, self.config.timeframe, from_date=start, to_date=end, now=now
            ):
                by_stamp[candle.timestamp] = candle
            remaining -= span
            end = start
        return [by_stamp[k] for k in sorted(by_stamp)]

    async def bars(self, *, now: datetime, days: int | None = None) -> list[RecoveryBar]:
        return bars_from_candles(await self._fetch(now=now, days=days))

    # ── replay ──────────────────────────────────────────────────────────────

    def _replay(self, campaign: RecoveryCampaign, bars: list[RecoveryBar]):
        """Rebuild this campaign from its mother. Pure; returns the engine."""
        engine_cls = FibZoneEntry if campaign.mode == "fib-zone" else TwoRedRecovery
        # The engine's callbacks take (when, price) and (when, strike, expiry);
        # this campaign's side is bound in here rather than widening the
        # engine's contract, so the rules module stays side-blind.
        side = campaign.side
        engine = engine_cls(
            campaign.mother,
            self.config,
            contract_for=lambda when, price: self._contract_for(when, price, side),
            premium_lookup=lambda when, strike, expiry: self._premium_for(when, strike, expiry, side),
            lot_size=self.lot_size,
        )
        window = [b for b in bars if b.timestamp > campaign.mother.timestamp]
        engine.run(mirror_bars(window) if campaign.mirrored else window)
        return engine

    async def start_named_mother(
        self, when: datetime, *, now: datetime, max_age_days: int | None = None, side: str | None = None
    ) -> RecoveryCampaign:
        """Open a campaign on the bar the trader named.

        The mother's high and low come from the market bar, never from anything
        typed -- a timestamp with no bar behind it is an error, not a shape to
        invent.  Replayed at once, so it can be checked against a chart the
        same evening rather than waiting for a poll.

        `max_age_days` is 30 for a live run: adopting a months-old mother into
        a running paper book is nearly always a mistake. A BACKTEST passes its
        own, larger figure, because reaching back is the whole point of one --
        and the candle fetch widens with it, or the mother's own bar falls off
        the front of the window and reads as "no candle opens at that time".

        `side` defaults to the run's, but naming it makes the campaign a call
        or a put on its own. The SAME candle can therefore be a call mother and
        a put mother at once -- its high starts one book and its low the other.
        """
        side = str(side or self.side).upper()
        if side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}")
        step = TIMEFRAME_MINUTES[self.config.timeframe]
        if when.minute % step or when.second or when.microsecond:
            raise ValueError(f"mother must be a {self.config.timeframe} candle open in IST")
        cap = int(MAX_MOTHER_AGE_DAYS if max_age_days is None else max_age_days)
        age = (now.date() - when.date()).days
        if age > cap:
            raise ValueError(f"choose a mother from the last {cap} days")
        bars = await self.bars(now=now, days=max(self.min_lookback_days, age + LOOKBACK_WARMUP_DAYS))
        bar = next((b for b in bars if b.timestamp == when), None)
        if bar is None:
            raise LookupError(
                f"no closed {self.config.timeframe} {self.dhan_symbol} candle opens at {when:%d %b %Y %H:%M} IST"
            )
        # SIDE IS PART OF THE ID. Without it the call campaign on a mother
        # blocked the put campaign on the same candle, which read as "already
        # running" for a book that did not exist (Phil, 2026-09-23).
        campaign_id = f"{self.symbol}:{self.config.timeframe}:{side}:{when:%Y%m%dT%H%M}"
        if campaign_id in self.campaigns:
            raise ValueError(f"a {side} campaign is already running on the {when:%d %b %Y %H:%M} mother")
        campaign = RecoveryCampaign(
            campaign_id,
            mirror_bar(bar) if side == "PE" else bar,
            self.mode,
            now,
            side=side,
        )
        campaign.engine = self._replay(campaign, bars)
        self.campaigns[campaign_id] = campaign
        return campaign

    def drop(self, campaign_id: str) -> bool:
        return self.campaigns.pop(campaign_id, None) is not None

    async def poll(self, *, now: datetime) -> PollReport:
        report = PollReport(at=now)
        if not is_nse_cash_session(now):
            report.skipped = "outside NSE cash session"
            self.last_report = report
            return report
        if not self.campaigns:
            report.skipped = "no campaigns"
            self.last_report = report
            return report
        try:
            bars = await self.bars(now=now)
        except Exception as exc:
            report.error = f"candle fetch failed: {exc}"
            self.last_report = report
            return report
        report.bars = len(bars)
        if not bars:
            report.skipped = "no closed bars yet"
            self.last_report = report
            return report

        for campaign in list(self.campaigns.values()):
            before = len(getattr(campaign.engine, "trades", []) or [])
            closed_before = sum(1 for t in (getattr(campaign.engine, "trades", []) or []) if t.exit_time is not None)
            campaign.engine = self._replay(campaign, bars)
            trades = campaign.engine.trades
            if len(trades) > before:
                for t in trades[before:]:
                    if t.entry_time is not None:
                        report.fills.append(
                            {
                                "campaign": campaign.campaign_id,
                                "trade": t.trade_no,
                                "index": t.entry_index,
                                "strike": t.strike,
                                "lots": t.lots,
                                "premium": t.entry_premium,
                            }
                        )
            closed_now = sum(1 for t in trades if t.exit_time is not None)
            if closed_now > closed_before:
                for t in [t for t in trades if t.exit_time is not None][closed_before:]:
                    report.exits.append(
                        {
                            "campaign": campaign.campaign_id,
                            "trade": t.trade_no,
                            "reason": t.exit_reason,
                            "net": t.net_pnl,
                        }
                    )

        # The replay has finished deciding. Only now does anything reach a
        # broker, and only through the book that knows what was already sent.
        if self.order_book is not None:
            for campaign in list(self.campaigns.values()):
                try:
                    outcome = self.order_book.sync(
                        campaign.campaign_id,
                        list(getattr(campaign.engine, "trades", []) or []),
                        symbol=self.dhan_symbol,
                        side=campaign.side,
                        when=now,
                    )
                except Exception as exc:
                    report.error = f"order sync failed: {exc}"
                    continue
                if outcome.get("frozen"):
                    report.error = (
                        "orders exist that this replay no longer accounts for; "
                        "reconcile the Dhan book by hand before this campaign trades again"
                    )

        self.last_poll = now
        self.last_report = report
        return report

    # ── what the panel reads ────────────────────────────────────────────────

    def _px(self, value, mirrored: bool):
        """An index-space number from the engine, in real prices."""
        return unmirror_price(value) if mirrored else value

    def _trade_row(self, t, side: str, mirrored: bool) -> dict:
        _px = lambda v: self._px(v, mirrored)  # noqa: E731
        return {
            "trade_no": t.trade_no,
            "armed_at": t.armed_at.isoformat() if t.armed_at else None,
            "trigger": _px(t.trigger),
            "entry_time": t.entry_time.isoformat() if t.entry_time else None,
            "entry_index": _px(t.entry_index),
            "sl_level": _px(t.sl_level),
            "strike": t.strike,
            # Which contract this leg actually bought. The table read
            # "2x24050" with no way to tell a call from a put.
            "side": side,
            "expiry": t.expiry.isoformat() if t.expiry else None,
            "lots": t.lots,
            "quantity": t.quantity,
            "entry_premium": t.entry_premium,
            "exit_time": t.exit_time.isoformat() if t.exit_time else None,
            "exit_index": _px(t.exit_index),
            "exit_premium": t.exit_premium,
            "exit_reason": t.exit_reason,
            "net_pnl": t.net_pnl,
            "costs": t.costs,
            "open": t.open,
        }

    # INDEX PRICES IN AN EVENT ARE ENGINE-SPACE, and a put campaign runs on
    # MIRRORED bars -- so a PE stop was logged at -23,352.05 and the page would
    # print it. Harmless while a run was all one side; now that a call book and
    # a put book share a host it is on screen. The event also carries which
    # campaign it came from, or a desk running both sides reads as one stream
    # with no way to tell whose trade stopped.
    _EVENT_PRICE_FIELDS = ("trigger", "entry_index", "sl", "close", "low", "buyer_high", "swing_low")

    def _event_row(self, campaign: RecoveryCampaign, event: dict) -> dict:
        row = dict(event)
        row["campaign_id"] = campaign.campaign_id
        row["side"] = campaign.side
        if campaign.mirrored:
            for field in self._EVENT_PRICE_FIELDS:
                if row.get(field) is not None:
                    row[field] = unmirror_price(row[field])
            for zone in row.get("zones") or []:
                # upper and lower swap as well as negate, like a mirrored bar
                if isinstance(zone, dict) and zone.get("upper") is not None:
                    zone["upper"], zone["lower"] = unmirror_price(zone["lower"]), unmirror_price(zone["upper"])
        return row

    def campaign_row(self, campaign: RecoveryCampaign) -> dict:
        engine = campaign.engine
        trades = list(getattr(engine, "trades", []) or [])
        open_trades = [t for t in trades if t.open]
        mirrored = campaign.mirrored
        _px = lambda v: self._px(v, mirrored)  # noqa: E731
        return {
            "campaign_id": campaign.campaign_id,
            "mode": campaign.mode,
            "timeframe": self.config.timeframe,
            "side": campaign.side,
            # A card has to be able to say which rule it is. The panel's Side
            # and depth controls describe the run you would START, not the one
            # you are looking at, so a CE book read under a PE recipe looks
            # like the same mother behaving differently (Phil, 2026-08-29).
            "itm_steps": self.config.itm_steps,
            "lot_size": self.lot_size,
            # Closed legs the archive could not price. A leg with no premium is
            # not a flat leg -- it is an unknown one, and a ledger that counts
            # it as zero reads five stops as break-even.
            "unpriced_legs": sum(1 for t in trades if t.entry_time is not None and not t.open and t.net_pnl is None),
            "mother": {
                "timestamp": campaign.mother.timestamp.isoformat(),
                # a mirrored mother's high is the real low, and vice versa
                "high": _px(campaign.mother.low) if mirrored else campaign.mother.high,
                "low": _px(campaign.mother.high) if mirrored else campaign.mother.low,
            },
            "status": campaign.status,
            "end_reason": getattr(engine, "end_reason", None),
            "booked_net": campaign.booked_net,
            # What the OPEN trade must net for the campaign to end green. The
            # target is a rupee threshold on the ledger, never a price level.
            "required_recovery": getattr(engine, "required_recovery", None),
            "open_trades": len(open_trades),
            "trades": [self._trade_row(t, campaign.side, mirrored) for t in trades],
            "zones": [
                {"level": z.level, "upper": _px(z.upper), "lower": _px(z.lower), "lots": z.lots}
                for z in (getattr(engine, "zones", None) or [])
            ],
            "swing_low": _px(getattr(engine, "swing_low", None)),
            "buyer_high": _px(getattr(engine, "buyer_high", None)),
        }

    def snapshot(self) -> dict:
        rows = [self.campaign_row(c) for c in self.campaigns.values()]
        priced = [r["booked_net"] for r in rows if r["booked_net"] is not None]
        return {
            "symbol": self.symbol,
            "dhan_symbol": self.dhan_symbol,
            "mode": self.mode,
            # The side a NEW mother takes unless it names its own. The panel
            # must describe the CAMPAIGNS, not this -- reading the run's side
            # as the book's is what made an ATM-2 CE book display under an
            # ATM-4 recipe (Phil, 2026-09-23).
            "side": self.side,
            "sides_running": sorted({c.side for c in self.campaigns.values()}),
            "timeframe": self.config.timeframe,
            "lot_size": self.lot_size,
            "config": {
                "lots_schedule": list(self.config.lots_schedule),
                "min_profit_inr": self.config.min_profit_inr,
                "sl_source": self.config.sl_source,
                "horizon_sessions": self.config.horizon_sessions,
                "itm_steps": self.config.itm_steps,
                "min_dte": self.config.min_dte,
            },
            "campaigns": rows,
            # THE EVENT LOG THE ENGINE ALREADY KEEPS. It recorded every arm,
            # re-arm and missing contract and then never shipped them, so High
            # Entry was the one strategy whose page could not say what its run
            # had been doing (Phil, 2026-08-26). Newest last, capped: a page
            # needs the recent past, not the whole session.
            "events": sorted(
                (
                    self._event_row(campaign, event)
                    for campaign in self.campaigns.values()
                    for event in (getattr(campaign.engine, "events", []) or [])
                ),
                key=lambda row: str(row.get("timestamp") or ""),
            )[-120:],
            "booked_net": round(sum(priced), 2) if priced else 0.0,
            "last_poll": self.last_poll.isoformat() if self.last_poll else None,
            "last_report": self.last_report.to_dict() if self.last_report else None,
        }


__all__ = ["CandleRecoveryHost", "RecoveryCampaign", "PollReport", "MODES", "SIDES", "bars_from_candles"]
