# PhilForge website audit — 11 September 2026

## Status and safety boundary

Local, reviewable remediation on `codex/philforge-audit-recovered-20260911`, based on `ec82853`.
The original temporary worktree disappeared; recorded patches were recovered into
`.worktrees/audit-20260911` and verification was rerun. Main and its existing untracked
files were not modified. No production deployment, service restart, broker order,
live configuration change, or credential rotation was performed.

This is a source review plus automated regression and visual verification, not a
certification that every line is safe or that future bugs or trading losses are impossible.
Live broker execution, production settings and complete raw-fill provenance are not
certified by a localhost test suite.

## Implemented findings

| Finding | Surgical correction | Evidence |
| --- | --- | --- |
| Loading a zero or omitted combined target could retain the previous strategy's target | Always restore saved combined limits and related settings, including explicit zero; missing values use the form's defaults | Browser regression switches targeted → zero-target → omitted-target strategies |
| Saved/imported indicator labels, run names and folder names could enter HTML or inline event handlers | Escape text/attributes; construct removable indicator badges with DOM text and event listeners; compare folder values without selector interpolation | Browser regression uses an HTML/event-handler payload and a quoted folder name |
| Origin validation treated explicit malformed/null origins like absent headers and trusted forwarded host for its allowlist | Fail closed for explicit invalid HTTP/WebSocket origins and HTTP Referer; derive HTTP host allowance from Host, not X-Forwarded-Host | Offline tests cover malformed, null, foreign, downgraded, same-origin and headerless requests |
| Three undefined runtime names could break fallback/export/error paths | Import `csv`, use existing `IST`, define Cascade module logger | F821 check, also added to CI |
| Shared CPR charts stopped at S4 although the indicator engine already supports S5 | Add S5 with the existing floor-pivot formula and align chart descriptions; order CPR bottom/top correctly | Previous-session numerical test and no-lookahead/no-previous-session tests |
| Decision evidence omitted S5 and the actual `CPR_Pivot` field name | Include both in the recorded indicator evidence keys | Offline regression; no decision or order-execution formula changed |
| Literal bright chart colors bypassed the light palette | Resolve known overlay/line colors at paint time; retain dark colors | Browser regression, theme screenshots and existing chart tests |
| CE/PE labels, positive money and paper status used dark-only colors | Shared theme-aware CE/PE tokens and semantic success color; improve light eyebrow labels | Light/dark desktop/mobile checks |
| Tiny indicator-remove and CE/PE explanation controls | Use semantic remove buttons and 24px controls | Browser keyboard/button regression and visual inspection |
| Trading header could pan internally and crop its title after navigation | Use non-scrollable clipping for the decorative header, preserving its layout | Regression attempts the observed 76px horizontal / 21px vertical scroll at both widths and themes |
| Missing CE/PE money rendered like genuine zero | Null/empty values show an em dash, while numeric zero remains zero | Browser regression |
| Landing headline and equity curve lagged the current tearsheet dataset | Fix missing `trading_days` lookup and regenerate landing from the same JSON | Generated-output equality, headline/trade count/curve tests |
| Sizing ROI was total-period return but prose and landing labelled it annual | Label total return explicitly, distinguish starting-size comparisons from fixed lots, and remove stale live-funding assumptions | Verify every sizing ROI equals rounded net / modelled funding; reject annual-return wording |
| Historical four-lot comparison was described as deployed/live and legacy fees were labelled with fixed rates | Clearly distinguish historical comparison and dated configuration snapshot; label recorded fees without asserting unverified fixed rates | Served tearsheet regenerated from its source |
| Headline window ended before its last recorded trade | Set requested archive-window end to 2026-08-31, matching the existing scenario basis; actual last trade remains 2026-08-18 | Date consistency regression |
| Legacy splice rebuild could overwrite a newer report schema with an incompatible dataset | Refuse that overwrite before writing; legacy-check failures now return a nonzero exit code | Regression verifies refusal leaves the file unchanged |
| Missing archive data was described as proving a performance floor | Replace that assurance with explicit coverage/bias limitations in English and Tamil | Documentation regression |
| Gap Carry's compact selector omitted the early loss-cut condition | Short descriptor includes 09:15 cut and 09:20 exit, retaining the two-row tile contract | Existing descriptor/layout tests |

## S4/S5: precisely what changed

The engine already computes both support levels. For the shared chart, using the
previous session's high `H`, low `L`, close `C`, and `P=(H+L+C)/3`:

- `S3 = L - 2*(H-P)`
- `S4 = S3 - (H-L)`
- `S5 = S4 - (H-L)`

S4 remains unchanged. S5 is added to the shared chart and decision evidence.
The current session's prices do not alter these levels. Without a previous session,
no CPR support levels are fabricated. This does not itself certify that an upstream
previous-session candle archive is complete.

CE_SL15_NoMonTue and PE_NoTarget retain their entry, exit, stop, target, weekday,
expiry and position-sizing rules. No new S4/S5 trading condition was inferred.
Changing those rules would need the precise intended condition and a matching replay.

## Backtest truthfulness and remaining provenance gap

The current archived headline is 931 trades, net ₹19,11,711.18 and maximum drawdown
₹3,62,229.06. The landing is synchronized to these recorded values; synchronization
is not an independent verification of the fills.

Internal checks reconcile the combined headline to the final cumulative series,
trade count, monthly totals, and yearly CE/PE totals. Whole-rupee presentation of
daily and charge components introduces small rounding differences; it is not a
replacement for paise-precision raw trade reconciliation.

The dataset mixes separately labelled scenarios: the Dhan four-lot/five-expiry
ladder headline, sizing comparisons, legacy Upstox comparisons, and a live-config
snapshot recorded on **9 September 2026 at 18:30 IST**. That snapshot is not current
live status. Historical `generated` metadata was not advanced to imply a fresh replay.

The legacy `rebuild_data.py` does not produce the newer configuration, compounding
and ladder sections. Its overwrite guard intentionally means the legacy weekly
refresh cannot publish over this newer dataset. A matching, reproducible replay
pipeline and exact configuration/source inputs are required to restore that refresh
safely. Combining stale supplementary sections with newly rebuilt headline values
would hide this issue, not solve it.

Other strategy reports are exercised by existing document and chart tests, but a
complete independent trade-by-trade reprice of every strategy is outstanding. Some
report builders rely on local/untracked archives or temporary research outputs.
No historical prices or missing P&L were synthesized to fill those gaps.

## Verification

Final results are recorded below. Tests use an isolated database,
dummy broker credentials, disabled startup jobs and offline browser API mocks.

- Python test suite: **2,731 passed**, plus 205 subtests; six warnings.
- Browser suite: **110/110 passed**. After the final header-clipping CSS change,
  all five focused navigation, saved-strategy and light/dark/mobile regressions passed again.
- JavaScript syntax, Ruff lint, runtime-name checks, tracked Python formatting
  with CI's Ruff 0.4.4, and `git diff --check` pass.
- Python requirement resolution audit: 61 packages, no known vulnerabilities reported.
- npm audit: no vulnerabilities reported in the test dependency tree.
- Bandit: no medium/high findings at medium/high confidence under the repository's configured exclusions and skips; this is not an exhaustive security proof.
- Gitleaks: no secrets detected in a redacted directory scan of the isolated checkout; production secrets and all historical commits were not audited.
- CE/PE screenshots inspected at desktop 1280px and mobile 390px, light and dark.

Local evidence is preserved outside the tracked patch in
`.worktrees/audit-evidence-20260911` in the main checkout: final test logs,
redacted scanner reports and the post-fix screenshots. Do not treat sample ticker
prices, expiry dates or empty books in those offline screenshots as production data.

Existing test fixtures with only a few candles explicitly opt out of the production
stub-session filter so they continue testing fills, limits and cooldowns. Production
`skip_stub_sessions` behavior was not changed. Obsolete source-string assertions were
updated to follow the persistent server-ordered campaign ledger and dated report labels.
The offline Equity UI test now stubs its WebSocket instead of racing a 403 response
from an intentionally non-allowlisted localhost port. Production's socket allowlist
was not expanded to make a UI test pass.

## Not a production security sign-off

- Nginx still permits inline scripts in CSP. Removing that requires a separate
  nonce/hash or script-extraction migration with UI coverage, not an untested header change.
- Dependency scans cover repository requirements resolved during this audit, not an
  inventory of the deployed host, OS packages or runtime secrets.
- Production encryption-key availability, reverse-proxy trust boundaries, access
  controls across real accounts, backup restoration and live-order failure handling
  require separate environment-specific verification.
- A green mocked browser suite does not prove market-data completeness, fills,
  profitability, or live trading safety. Review and an authorized release gate remain
  necessary before these changes reach production.
