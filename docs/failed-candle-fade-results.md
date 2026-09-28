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
