# Where these figures come from

Copied verbatim from CryptoForge commit 476a5c8 (claude/dhan-options-backfill),
produced by `tools/supertrend_options_backtest.py` there against the Dhan
expired-options archive (4 stores, bleed-filtered) with real NIFTY 1m index
candles from THIS repo's tools/.nifty_cache for signals.

- st_final_CE.json          the shipped book: 1H, ST(10, 1.5), CE, ATM,
                            next-week expiry, roll at 6 strikes,
                            trail armed +100 pts / 80-pt give-back
- st_roll_CE_tf60.json      the same without the trail (baseline), all mults
- st_roll_PE_tf60.json      the put mirror, all mults — loses on priced exits
- st_tgt_CE_tf60_m1.5_t125.json  fixed 125-pt target, for the comparison table

Validated eight ways before publication (mechanical bias audit; every premium
re-read from raw parquet 685/685; floored exits proven absent and floored
below true value; an independently written replayer reproducing all 685
trades to the rupee; Upstox cross-pricing corr 0.959 with Dhan the cheaper;
walk-forward config choice; bootstrap; 3x cost stress; calendar tape test;
premium-level sanity). Zero failures.

## Extended 02-Oct-2026 to 30-Sep-2026

The Dhan archive (all four stores) was extended from 21-Aug to 30-Sep-2026 with
the same rolling-option pull, and tools/.nifty_cache gained NIFTY 1m/5m for
21-30 Sep (Dhan index candles; they match the Upstox cache on 15-18 Sep to
within 0.55 of a point). The four JSONs were re-run in PhilForge with
tools/supertrend_options_backtest.py:

    --timeframes 60 [--multipliers 1.5] --side CE|PE --hold flip --expiry 2
    --roll-strikes 6 --pricing dhan --to 2026-09-30
    st_final_CE: + --trail-points 80 --trail-after-points 100
    st_tgt_...:  + --target-points 125

The old window reproduces the published book exactly (685 trades,
Rs 2,60,966.89); every trade to 21-Aug is identical in the extended files.
The eight checks above covered those 685; the 11 added since were not re-audited.
