# Verdict — the failed-candle fade does not make money

The two playlist ideas that survived testing (`docs/playlist-claims-results.md`) —
the opening hour is violent, and a failed candle falls back — were turned into one
trade with its rules fixed before any result was seen: in 09:15–10:15, fade a 5m
candle that closes beyond the last 12 candles' high/low with under half of it beyond;
stop at its extreme, target the same distance (1:1), out after 30 minutes.

| Version | Win rate | Per trade (after 1.5 pts cost) | Two years |
|---|---|---|---|
| As fixed (stop at the candle's extreme) | 15% | −3.7 pts | −1,444 pts |
| Stop one average candle wider | 49% | −1.9 pts | −661 pts |
| Same, with **zero** cost | 49% | −0.4 pts | −148 pts |

**Why the as-fixed rule fails:** a failed candle closes near its own high, so the
stop sat a median 3 points away and ordinary noise hit it on 328 of 390 trades.

**Why the wider version fails too:** it wins about half the time at 1:1 — a coin
toss — and loses a little even with no costs. The failed candle does fall back
more often than a full break, but not far enough to pay for a trade.

**Decision:** not a strategy. Nothing here should go near the live book. The one
second-half gain (+80 pts on 155 trades, PF 1.04) is the size of chance.

Reproduce: `python3 tools/failed_candle_fade.py --data DIR`.

---

# Failed-candle fade — NIFTY 5m, 2024-07-15 to 2026-08-03

Cost 1.5 points per round trip. Split at 2025-07-22. Points are NIFTY index points.

| variant | period | trades | win rate | avg / trade | total pts | profit factor | worst drawdown |
|---|---|---|---|---|---|---|---|
| **The rule**: failed candle, first hour | all | 390 | 14.6% | -3.7 | -1444 | 0.22 | 1452 |
| **The rule**: failed candle, first hour | 1st half | 210 | 15.2% | -4.1 | -852 | 0.21 | 864 |
| **The rule**: failed candle, first hour | 2nd half | 180 | 13.9% | -3.3 | -592 | 0.23 | 589 |
| The rule, stop one average candle wider | all | 342 | 48.5% | -1.9 | -661 | 0.87 | 1033 |
| The rule, stop one average candle wider | 1st half | 187 | 47.6% | -4.0 | -742 | 0.76 | 904 |
| The rule, stop one average candle wider | 2nd half | 155 | 49.7% | +0.5 | +80 | 1.04 | 400 |
| Same signal, all day | all | 2212 | 18.1% | -3.0 | -6575 | 0.22 | 6578 |
| Same signal, all day | 1st half | 1094 | 19.7% | -3.0 | -3271 | 0.24 | 3270 |
| Same signal, all day | 2nd half | 1118 | 16.5% | -3.0 | -3304 | 0.20 | 3306 |
| Control: fade FULL breaks, first hour | all | 597 | 29.0% | -2.5 | -1519 | 0.74 | 1700 |
| Control: fade FULL breaks, first hour | 1st half | 307 | 31.3% | -0.0 | -5 | 1.00 | 309 |
| Control: fade FULL breaks, first hour | 2nd half | 290 | 26.6% | -5.2 | -1514 | 0.54 | 1659 |

Cost sensitivity — the rule over the whole period:

| cost / trade | trades | avg / trade | total pts | profit factor |
|---|---|---|---|---|
| 0.0 | 390 | -2.2 | -859 | 0.37 |
| 1.0 | 390 | -3.2 | -1249 | 0.26 |
| 1.5 | 390 | -3.7 | -1444 | 0.22 |
| 3.0 | 390 | -5.2 | -2029 | 0.14 |

How the rule's trades ended: stop 328, target 62
Median risk (entry to stop): 3.1 points.

---

# The same, on Bitcoin (BTCUSDT 5m, 12-Jul to 19-Sep-2026)

Phil, 28-Sep-2026: *"but it does very good on crypto"*. Tested on the only BTC candles
available here — `BTCUSDT_5m.pkl` from Drive, 14,692 bars, ten weeks — so a first look,
not a verdict. Same rule; the NIFTY opening hour is replaced by his BTC high-volume
window, 18:00–22:00 IST (T215), and all day. Result in % of price per trade.

| Window | Stop | Cost / trade | Trades | Win rate | Total |
|---|---|---|---|---|---|
| 18:00–22:00 IST | at the candle | 0 | 150 | 21% | −1.4% |
| 18:00–22:00 IST | one candle wider | 0 | 136 | 49% | **+1.0%** |
| 18:00–22:00 IST | one candle wider | 0.04% | 136 | 43% | −4.4% |
| all day | one candle wider | 0 | 815 | 51% | **+2.4%** |
| all day | one candle wider | 0.04% | 815 | 43% | −30.3% |
| all day | one candle wider | 0.10% | 815 | 22% | −79.2% |

Both halves of the ten weeks lose at 0.04% (−15.7% and −14.6%).

**On BTC the failed candle is a stronger signal than on NIFTY** — at the same close
distance it comes back 11–17 points more often than a full break, against 2–15 on NIFTY.
But the moves are small: the average winning fade is a few hundredths of a percent, and
one exchange fee (0.04% round trip) is larger than the whole edge. Without costs it is a
coin toss that very slightly wins; with any realistic cost it loses.

His other claims on the same BTC bars: the 3-candle hold did not help (held breaks went
on 45%, not-held 49%; at the 50% line 50% vs 47%), and under value did not beat over value
(1:1 — 50% vs 46%; 2:1 — 32% vs 33%).

**Decision:** the same as NIFTY — not a strategy. More BTC history (the 15 MB
`BTCUSDT_1h.json` in Drive is too large for the Drive tool here) would firm this up, but a
signal smaller than one fee does not become profitable with more data.
