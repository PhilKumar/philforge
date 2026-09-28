# His ladder on Bitcoin — it does win, almost every time

Phil, 28-Sep-2026: *"you might be wrong somewhere… this is how he does things"* — and the
first backtests were: they priced single rules with a stop and a 30-minute exit, which is
not how the method is traded. This one uses Phil's own answers: **measured from the
mother candle, sized by the fund formula, first exit at 0.25**, on every timeframe with
its own fund. Spot, no leverage, Binance fees 0.1% per fill. Tool: `tools/mother_ladder.py`.

## The answer, simply

| Timeframe | Win rate | Profit a year (on capital) | Longest a book was stuck | Deepest dip while holding |
|---|---|---|---|---|
| 5m | 21% | +0.3% | 41 days | −6% |
| 15m | 37% | +1.0% | 15 days | −5% |
| 1h | 77% | +0.5% | 5 days | −3% |
| 4h | **100%** | +0.2% | 4 days | −2% |
| **1d** (9 years) | **100%** (147 of 147) | **+5.1%** | **174 days** | **−36%** |
| 1w (9 years) | **100%** (17 of 17) | +2.4% | **2.2 years** | **−66%** |

**Phil is right that it wins.** From the 1-hour chart up, almost every book closes in
profit, and on the daily and weekly every single one did, across nine years that include
the 2018 and 2022 crashes. Phil's own 16-for-16 live record is what this method produces.

**What the 100% costs** is time, not losses:

- The worst daily book: bought Oct-2018, stuck **174 days**, down **34%** before it came back.
- The worst weekly book: bought Nov-2021 near $69,000, stuck **2.2 years**, down **66%**, closed Feb-2024 for a profit of $12.86 on $200.
- On 5m and 15m the targets are smaller than one exchange fee, so most "wins" are eaten by fees — the method works where the moves are bigger than the costs.

**The return is modest:** about 3–5% a year on the capital on the daily, because most of
the time most of the money waits (at most 50% is ever in use, T6). The result holds for
a mother of 10, 20 or 50 candles (3.2–5.1% a year, 100% wins).

**Why the first tests said "no":** they tested pieces with a stop. His method has no stop
and buys more as price falls, so it almost never closes at a loss — it waits.

**Also fixed:** `tools/ladder_backtest.py` chose mother candles with a centred window that
read future candles; it now uses only candles already seen.

Data: `BTCUSDT_5m.pkl` (12-Jul to 19-Sep-2026, resampled to 15m/1h/4h — ten weeks only) and
`BTCUSDT_1d.json` (Aug-2017 to Sep-2026, and weekly from it), both from Drive.

---

## Whole book sold at 0.25

Capital $200. Mother = new 20-candle high, known at its close. Fees 0.1% per fill. Exit: whole book at 0.25.

| TF | data | books | closed | still open | win rate | profit on capital | per year | avg profit on money used | median hold | longest hold | worst dip vs average | most capital in use |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5m | 2026-07-12–2026-09-19 | 84 | 84 | 0 | 21% | +0.06% | +0.3% | -0.02% | 0 h | 978 h | -6.4% | 5.9% |
| 15m | 2026-07-12–2026-09-19 | 95 | 95 | 0 | 37% | +0.19% | +1.0% | +0.03% | 0 h | 352 h | -5.2% | 8.5% |
| 1h | 2026-07-12–2026-09-19 | 43 | 43 | 0 | 77% | +0.09% | +0.5% | +0.10% | 1 h | 119 h | -2.6% | 3.3% |
| 4h | 2026-07-12–2026-09-19 | 10 | 10 | 0 | 100% | +0.03% | +0.2% | +0.18% | 4 h | 96 h | -2.4% | 3.2% |
| 1d | 2017-08-17–2026-09-16 | 147 | 147 | 0 | 100% | +46.23% | +5.1% | +1.86% | 24 h | 4176 h | -35.9% | 50.0% |
| 1w | 2017-08-20–2026-09-20 | 17 | 17 | 0 | 100% | +22.11% | +2.4% | +4.83% | 168 h | 19656 h | -65.6% | 48.9% |


## Half at 0.25, the rest at 0.5

Capital $200. Mother = new 20-candle high, known at its close. Fees 0.1% per fill. Exit: half at 0.25, rest at 0.5.

| TF | data | books | closed | still open | win rate | profit on capital | per year | avg profit on money used | median hold | longest hold | worst dip vs average | most capital in use |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5m | 2026-07-12–2026-09-19 | 63 | 62 | 1 | 47% | +0.13% | +0.7% | +0.05% | 0 h | 978 h | -6.4% | 5.9% |
| 15m | 2026-07-12–2026-09-19 | 26 | 25 | 1 | 88% | +0.24% | +1.3% | +0.22% | 1 h | 978 h | -5.8% | 8.5% |
| 1h | 2026-07-12–2026-09-19 | 31 | 31 | 0 | 90% | +0.32% | +1.7% | +0.30% | 2 h | 351 h | -4.4% | 7.1% |
| 4h | 2026-07-12–2026-09-19 | 8 | 8 | 0 | 100% | +0.31% | +1.6% | +0.73% | 6 h | 348 h | -4.6% | 8.5% |
| 1d | 2017-08-17–2026-09-16 | 61 | 60 | 1 | 100% | +64.90% | +7.1% | +4.32% | 48 h | 25272 h | -71.4% | 50.0% |
| 1w | 2017-08-20–2026-09-20 | 17 | 16 | 1 | 100% | +36.05% | +4.0% | +8.05% | 168 h | 19992 h | -65.6% | 48.9% |

