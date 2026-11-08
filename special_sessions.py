"""Exchange sessions held on a day the calendar calls closed.

Phil, 2026-11-08: "today muhurat trading is there.. check this". Diwali's
Muhurat session is an hour on a Sunday, and every "is the market open?" check
here said no to any Saturday or Sunday: the Scalp monitor would have left a
Muhurat trade's stop, target and Supertrend exit unwatched. Each check now asks
this table first.

NSE/BSE, 08-Nov-2026: pre-open 14:05, trading 14:20, F&O to 15:40 (cash 15:30).
Add a row when the exchanges announce the next one.

No imports from the app: the engine, the feed and the routes all read it.
"""

from __future__ import annotations

from datetime import date, datetime

# day -> (pre-open, trading open, F&O close, cash close), each (hour, minute), IST.
NSE_SPECIAL_SESSIONS: dict[str, tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]]] = {
    "2026-11-08": ((14, 5), (14, 20), (15, 40), (15, 30)),  # Diwali Muhurat
}


def special_session(day: date | datetime | str | None):
    """(pre-open, open, F&O close, cash close) for a special session that day, or None."""
    if day is None:
        return None
    if isinstance(day, datetime):
        day = day.date()
    return NSE_SPECIAL_SESSIONS.get(str(day)[:10])


def is_special_session_day(day) -> bool:
    return special_session(day) is not None


def in_special_session(moment: datetime, *, pre_open: bool = True, cash: bool = False, slack_min: int = 0) -> bool:
    """True while `moment` (IST) falls inside that day's special session."""
    row = special_session(moment)
    if row is None:
        return False
    start = row[0] if pre_open else row[1]
    end = row[3] if cash else row[2]
    minutes = moment.hour * 60 + moment.minute
    return start[0] * 60 + start[1] - slack_min <= minutes <= end[0] * 60 + end[1] + slack_min
