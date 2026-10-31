"""My exits against the rule's, for the Assets document (Phil, 2026-10-31)."""

from datetime import datetime, timedelta

import pandas as pd

import live_exit_review as ler

DAY = "2026-10-30"


def _index(close_at=lambda ts: 23000.0, days=2):
    """NIFTY 1m: a quiet day before the trade day, then the trade day."""
    rows = []
    for back in range(days - 1, -1, -1):
        start = datetime.fromisoformat(f"{DAY} 09:15") - timedelta(days=back)
        for i in range(375):
            ts = start + timedelta(minutes=i)
            px = close_at(ts) if back == 0 else 23000.0
            rows.append({"ts": ts, "open": px, "high": px + 1, "low": px - 1, "close": px, "volume": 1})
    return pd.DataFrame(rows).set_index("ts")


def _bars(prices: dict, default=272.0):
    """The contract's 5m bars for the trade day; `prices` maps HH:MM to (open, high, low, close)."""
    out = []
    start = datetime.fromisoformat(f"{DAY} 09:15")
    for i in range(75):
        ts = start + timedelta(minutes=5 * i)
        o, h, lo, c = prices.get(ts.strftime("%H:%M"), (default, default + 2, default - 2, default))
        epoch = int((ts - timedelta(hours=5, minutes=30)).timestamp() - datetime(1970, 1, 1).timestamp())
        out.append({"t": epoch, "o": o, "h": h, "l": lo, "c": c})
    return ler.option_bars(out)


TRADE = {
    "book_name": "CE_SL15_NoMonTue",
    "book_side": "CE",
    "id": 5,
    "strike": 22750,
    "option_type": "CE",
    "expiry": "2026-11-03",
    "entry_time": f"{DAY} 09:30:00.885250",
    "exit_time": f"{DAY} 09:50:35",
    "entry_premium": 272.15,
    "exit_premium": 296.15,
    "quantity": 130,
    "lots": 2.0,
    "sl_pct": 15,
    "sqoff_time": "15:10",
    "pnl": 3001.39,
    "exit_reason": "BROKER_MANUAL_EXIT",
}
BELOW_22900 = [{"left": "current_close", "operator": "is_below", "right": "number", "right_number_value": 22900}]
RULES = {"exit_conditions": BELOW_22900, "entry_conditions": [], "indicators": [], "signal_cutoff_time": "15:15"}


def test_the_stop_is_judged_on_the_contract_from_the_real_entry():
    bars = _bars({"10:15": (241.9, 241.9, 217.45, 233.2)})
    rule = ler.rule_exit(TRADE, RULES, _index(), bars)
    # 15% under 272.15 is 231.33, touched inside the 10:15 candle.
    assert rule == {
        "time": "10:15",
        "price": 231.33,
        "reason": "STOP_LOSS",
        "why": "15% stop at 231.33",
        "source": "replay",
    }


def test_a_candle_that_opens_through_the_stop_fills_at_its_open():
    bars = _bars({"10:15": (220.0, 225.0, 210.0, 215.0)})
    assert ler.rule_exit(TRADE, RULES, _index(), bars)["price"] == 220.0


def test_the_exit_signal_acts_at_the_5m_close_at_the_contracts_price_then():
    falls = lambda ts: 22850.0 if ts.strftime("%H:%M") >= "11:00" else 23000.0  # noqa: E731
    bars = _bars({"11:00": (290.0, 300.0, 285.0, 298.5)})
    rule = ler.rule_exit(TRADE, RULES, _index(falls), bars)
    # The 11:00 candle closes below 22,900, so the book lets go at 11:05.
    assert (rule["time"], rule["price"], rule["reason"]) == ("11:05", 298.5, "EXIT_SIGNAL")
    assert rule["why"] == "NIFTY close is below number"


def test_with_nothing_firing_the_square_off_takes_it():
    bars = _bars({"15:05": (318.0, 322.0, 317.0, 320.95)})
    rule = ler.rule_exit(TRADE, RULES, _index(), bars)
    assert rule == {"time": "15:10", "price": 320.95, "reason": "SQUARE_OFF", "why": "square-off", "source": "replay"}


def test_a_trade_the_book_closed_itself_is_the_same_in_both_columns():
    row = ler.review_row({**TRADE, "exit_reason": "STOP_LOSS", "pnl": -5089.6}, None)
    assert row["manual"] is False and row["rule"] == row["mine"] == -5089.6


def test_a_hand_exit_is_priced_at_the_rules_exit_less_charges():
    rule = {"time": "10:15", "price": 231.33, "reason": "STOP_LOSS", "why": "15% stop at 231.33"}
    row = ler.review_row(TRADE, rule)
    gross = (231.33 - 272.15) * 130
    assert row["manual"] and row["my_how"] == "closed in Dhan"
    assert gross - 150 < row["rule"] < gross  # charges are a round trip's, about a hundred rupees


def test_months_total_mine_and_rule_and_keep_real_lots():
    expiry_day = {**TRADE, "entry_time": "2026-10-19 09:30:00", "lots": 3.0, "quantity": 195}
    rows = [
        ler.review_row(TRADE, {"time": "10:15", "price": 231.33, "reason": "STOP_LOSS"}),
        ler.review_row({**expiry_day, "exit_reason": "STOP_LOSS", "pnl": -10227.86}, None),
        ler.review_row(
            {**TRADE, "entry_time": "2026-09-30 13:10:00", "exit_reason": "STOP_LOSS", "pnl": -5089.6}, None
        ),
    ]
    months = ler.by_month(rows)
    assert [m["month"] for m in months] == ["2026-10", "2026-09"]
    october = months[0]
    assert october["count"] == 2 and october["manual"] == 1 and october["i_won"] == 1
    assert october["mine"] == round(3001.39 - 10227.86, 2)
    assert [t["lots"] for t in october["trades"]] == [3.0, 2.0]


def test_a_hand_exit_not_yet_worked_out_is_shown_as_pending_not_zero():
    row = ler.review_row(TRADE, None)
    assert row["rule"] is None
    month = ler.by_month([row])[0]
    assert month["pending"] == 1 and month["rule"] == 0


def test_the_document_reads_in_rupees_the_indian_way():
    assert ler._inr(1234567.4) == "+₹12,34,567"
    assert ler._inr(-6905.17) == "−₹6,905"
    html = ler.render(ler.by_month([ler.review_row(TRADE, None)]), generated="31 Oct 2026, 18:00 IST")
    assert "October 2026" in html and "My exits vs the rule" in html
    assert "not worked out yet" in html and "<script" not in html


def test_the_review_reads_dhan_books_only_and_keeps_each_answer(monkeypatch):
    import asyncio
    import json

    import app as app_module

    trades = [
        {
            **TRADE,
            "entry_time": "2026-10-29 13:35:00",
            "exit_time": "2026-10-29 13:56:04",
            "exit_reason": "MANUAL_EXIT",
        },
        {**TRADE, "book_name": "CE_SL15_NoMonTue_Zerodha", "broker": "zerodha", "entry_time": "2026-10-29 13:35:01"},
        {**TRADE, "entry_time": "2026-08-20 09:30:00", "exit_reason": "EXIT_SIGNAL"},
    ]
    store, asked = {}, []

    async def book_trades(_uid):
        return trades

    async def get_state(key):
        return store.get(key)

    async def set_state(key, value):
        store[key] = value

    async def work_out(user_id, trade, entry_ts, exit_ts, client, index_for):
        asked.append(trade["entry_time"])
        return {"time": "15:10", "price": 320.95, "reason": "SQUARE_OFF", "why": "square-off"}

    monkeypatch.setattr(app_module, "_live_book_trades", book_trades)
    monkeypatch.setattr(app_module._db_mod, "get_app_state", get_state)
    monkeypatch.setattr(app_module._db_mod, "set_app_state", set_state)
    monkeypatch.setattr(app_module, "_work_out_rule_exit", work_out)
    months = asyncio.run(app_module._live_exit_review({"id": 1}, "dhan"))
    assert [m["month"] for m in months] == ["2026-10"]
    (only,) = months[0]["trades"]
    assert only["book"] == "CE_SL15_NoMonTue" and only["rule_time"] == "15:10"
    assert len(json.loads(store["live_exit_review:1"])["rules"]) == 1
    # Opened again: answered from what was kept, nothing walked twice.
    asyncio.run(app_module._live_exit_review({"id": 1}, "dhan"))
    assert asked == ["2026-10-29 13:35:00"]


def test_candles_that_do_not_hold_the_real_entry_are_not_this_contracts():
    # An archive chart of the wrong strike: the 09:30 entry at 272.15 is nowhere near it.
    assert ler.rule_exit(TRADE, RULES, _index(), _bars({}, default=150.0)) is None


def test_a_dhan_close_at_the_books_own_stop_is_the_stop_not_a_hand_exit():
    # 25-Sep-2026: entry 242.60, 20% stop 194.08, filled 193.85, booked as BROKER_MANUAL_EXIT.
    stop = {**TRADE, "entry_premium": 242.6, "sl_pct": 20, "exit_premium": 193.85, "pnl": -6436.95}
    assert ler.was_its_own_stop(stop)
    row = ler.review_row(stop, None)
    assert not row["manual"] and row["rule"] == row["mine"] == -6436.95
    assert not ler.was_its_own_stop(TRADE)  # 296.15 is nowhere near its 231.33 stop


def test_the_paper_twin_of_the_same_contract_gives_the_rules_exit():
    twin = {
        **TRADE,
        "entry_time": f"{DAY} 09:30:01",
        "entry_premium": 271.82,
        "exit_time": f"{DAY} 10:15:34",
        "exit_premium": 230.42,
        "exit_reason": "STOP_LOSS",
    }
    rule = ler.twin_exit(TRADE, [twin])
    # Its stop sat under ITS entry; this trade's sits under the real one.
    assert (rule["time"], rule["price"], rule["reason"], rule["source"]) == ("10:15", 231.33, "STOP_LOSS", "paper twin")
    assert ler.twin_exit(TRADE, [{**twin, "strike": 22800}]) is None
    assert ler.twin_exit(TRADE, [{**twin, "book_name": "My_First_Run_CE"}]) is None
    late = {**twin, "exit_time": f"{DAY} 15:25:00", "exit_reason": "SQUARE_OFF", "exit_premium": 300.0}
    assert ler.twin_exit(TRADE, [late])["time"] == "15:10"
