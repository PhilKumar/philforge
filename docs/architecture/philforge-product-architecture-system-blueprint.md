# PhilForge Product Architecture and System Blueprint

**Document status:** Complete production architecture reference
**Architecture snapshot date:** 24 August 2026
**Original blueprint date:** 14 August 2026
**Source baseline:** PhilForge `29c94c5`
**Revision scope:** Production changes since the original blueprint
**Audience:** Product owners, developers, operators, security reviewers, support staff, and future architects

This is the independent architecture document for PhilForge. It describes only the Indian equities and options platform. Shared hosting comparisons belong in the separate [estate overview](./cryptoforge-philforge-product-architecture-system-blueprint.md).

---

# Chapter 1: The Eagle's View

## 1.1 Product mission

PhilForge is an Indian-market research, backtesting, paper-trading, selected live-trading, and portfolio platform. It supports NSE indices, cash equities, ETFs, index options, and stock options. Its strategy families include the visual Strategy Builder, Scalp, options and cash Cascade, Fib Boundary, Candle Entry, Gap Carry, Fib Space, Recovery, Two Red equity, backtests, journals, and portfolio analytics.

The platform is multi-user. Each user has an account, strategies, run history, journals, selected broker credentials, and user-owned runtime state. Admin users can manage accounts and inspect engine summaries, but normal database and WebSocket paths remain scoped to the owning user.

PhilForge is built around Indian market time. It must understand 09:15-15:30 IST sessions, market holidays, weekly expiry selection, lot sizes, strike steps, market-close handling, and expiry-day exits.

## 1.2 Product profile

| Concern | PhilForge design |
|---|---|
| Asset classes | NSE indices, cash equities, ETFs, index options, and stock options |
| Market schedule | Indian Standard Time with NSE sessions, holidays, and expiry rules |
| Main users | Admin, account user, strategy developer, backtester, paper trader, live trader, scalp trader, and portfolio reviewer |
| Main broker | Dhan for live orders, positions, balances, market data, and historical index/equity candles |
| Historical option source | Upstox exact contract history for selected expired-option replay gaps |
| Other market sources | NSE public endpoints and yfinance for selected market information |
| Public origin | `philforge.in` and `www.philforge.in` |
| Application ports | Blue-green pair `8000` and `8001` |
| Runtime model | One active FastAPI/Uvicorn worker with user-scoped asynchronous engines |
| Primary database | Multi-tenant SQLite in WAL mode |
| Market transport | Dhan MarketFeed WebSocket plus Dhan REST candles and quotes |
| Browser transport | Authenticated REST plus user-scoped `/ws` WebSocket |
| Front end | Server-hosted HTML, CSS, plain JavaScript, Canvas/SVG charts, service worker, and PWA manifest |
| Hosting | AWS Linux virtual machine behind Nginx and systemd |

The timing values below are design cadences, not execution guarantees:

| Workload | Typical design cadence |
|---|---|
| Dhan tick delivery | Event-driven while MarketFeed is healthy |
| Standard live or paper strategy | Normally polls every 10 seconds and respects candle-close rules |
| Cascade paper monitors | Commonly poll every 12 seconds during the market session |
| Selected Candle Entry and Two Red monitors | Commonly poll every 20 seconds |
| Recovery monitor | 30 seconds |
| Fib Space monitor | 60 seconds |
| Off-session Cash Cascade | Sleeps for 5 minutes instead of polling continuously |

The real order time depends on Dhan, the exchange, network delay, rate limits, order type, and market liquidity.

## 1.3 System stack

| Layer | Technology | Responsibility |
|---|---|---|
| Public edge | Nginx with TLS and security headers | Route HTTPS and WebSocket traffic to the active PhilForge port |
| Application | Python 3.11, FastAPI, Starlette, Pydantic, Uvicorn | Authenticate users, validate inputs, expose APIs, and own engine lifecycles |
| Concurrency | Python `asyncio`, worker threads for blocking calls | Run user engines and broker requests without blocking the event loop |
| Front end | HTML, CSS, plain JavaScript | Dashboards, Strategy Builder, portfolio, journals, terminals, monitors, and admin tools |
| Charting | Canvas and SVG | Render native OHLC, Cascade structure, trade marks, replay charts, and equity curves |
| Analytics | Pandas and NumPy | Normalize candles, calculate indicators, backtest, and aggregate portfolio metrics |
| Database | SQLite with `aiosqlite` and WAL | Store users, sessions, strategies, runs, journals, financial plans, trades, and result catalogs |
| Recovery | SQLite application state plus per-user files | Restore live, paper, scalp, Cascade, and auxiliary engines after restart |
| Caches | Dhan candle cache, scrip cache, Upstox option archive | Reduce broker load and preserve exact historical contracts |
| Broker and data | Dhan REST/WebSocket, Upstox history, NSE endpoints, yfinance | Live trading, ticks, candles, expired-option premiums, and market context |
| Identity security | bcrypt, TOTP, Fernet encryption, one-use action tokens | Protect passwords, MFA, broker credentials, and sensitive actions |
| Alerts | Telegram helper | Report token renewal, trading, and operational failures |
| Observability | Health/feed endpoints, systemd journal, optional Prometheus | Show process, broker, feed, engine, and backup health |
| Quality gates | Ruff, Bandit, Gitleaks, dependency audit, unit tests, Playwright | Stop unsafe or visually broken revisions before deployment |
| Operations | GitHub Actions, SSH, systemd, Nginx, backup timer | Deploy, isolate, health-check, back up, and recover the service |

PhilForge does not use a separate message broker. A “queue” is an in-process asynchronous task or job registry. This is why one active worker owns the engines and why blue-green deployment has an explicit ownership handover.

## 1.4 Central wiring map

### 1.4.1 User command path

**Browser action → HTTPS → Nginx → active Uvicorn worker → session and permission checks → optional MFA action approval → payload validation → user-owned engine/service → Dhan adapter → broker/exchange → normalized result → SQLite/recovery state → REST and user-scoped WebSocket → browser**

1. The user selects an instrument, strategy, mode, date range, or portfolio action.
2. Browser JavaScript sends a same-origin request with the session cookie.
3. Nginx forwards it to port 8000 or 8001, whichever is active.
4. FastAPI resolves the user and role before reading or changing private state.
5. A sensitive action may require a short-lived, one-use action token backed by a fresh MFA code.
6. Pydantic and route-specific checks normalize the request.
7. Broker credentials are resolved for this user. An admin-only global fallback can be used where explicitly allowed.
8. The engine applies strategy, session, contract, quantity, stop, target, and safety rules.
9. The Dhan client translates the order into a security ID, segment, instrument type, side, order type, product type, quantity, and price.
10. The result is checked, stored, and returned. Later events are pushed only to the owning user's sockets.

### 1.4.2 Market-data path

| Input | Processing | Output |
|---|---|---|
| Dhan MarketFeed tick | Resolve security ID; update LTP cache; call tick subscribers; aggregate candles where required | Fast price state for scalp and live engines |
| Dhan historical candle | Chunk supported date ranges; throttle requests; normalize time and OHLCV; cache complete spans | Charts, signals, scanners, and backtests |
| Dhan scrip master | Download and cache broker instrument catalog; normalize symbols | Equity and option security IDs, expiry, strike, lot size, and segment |
| Upstox exact option candle | Resolve contract, read archive, fetch only missing gaps when allowed | Historical fixed-strike premium and net P&L |
| NSE/yfinance context | Validate and normalize the selected public response | Market movers, FII/DII, and supporting market panels |

### 1.4.3 Historical option integrity

PhilForge separates signal data from option-price data:

**Dhan NIFTY candles → strategy signal and expiry selection → exact strike/expiry identity → Upstox option archive → gap check → optional narrow backfill → fully priced result or explicit incomplete result**

The platform must not replace a missing premium with zero or infer it from the index. If one required contract candle is absent, the result says what is missing and withholds any P&L that cannot be proved.

## 1.5 Runtime engine families

| Engine family | Purpose | Execution status |
|---|---|---|
| Backtest | Replay Strategy Builder contracts and specialist systems over historical candles | Simulation only |
| Standard Paper | Follow current data with simulated fills and stored history | Paper |
| Standard Live | Evaluate a saved strategy and place Dhan orders | Live-capable with broker and safety checks |
| Scalp | Fast option entry, target, stop, and exit monitor | Paper and selected live paths |
| Options Cascade | Mother-candle and fib campaign using a fixed option contract | Paper first; live gates remain explicit |
| Cash Cascade | Trade equity/ETF ladders from cash-market geometry | Paper workflow in the current terminal path |
| Candle Entry | Operate the two-red box ladder from a manual or automatic mother | Backtest and paper; automatic market-session host |
| Gap Carry | Enter the measured late-session option campaign and exit in the next session | Backtest, paper and live (real Dhan orders); manual or automatic host |
| Fib Boundary | Build and monitor multi-symbol fib touch ladders | Paper by default; live exit safety is separately gated |
| Fib Space | Monitor structured fib-space campaigns | Paper |
| Two Red Equity | Find mothers and operate equity campaign ladders | Paper |
| Test Bench | Store repeatable mother-candle strategy experiments | Historical simulation |

Each engine is registered under the user ID and an engine or instrument identity. This prevents one user's stop command, status request, or browser event from controlling another user's engine.

## 1.6 Multi-tenant persistence

| State group | SQLite ownership and purpose |
|---|---|
| Users | Username, password hash, role, active state, encrypted Dhan fields, and MFA state |
| Sessions | Login token, user owner, creation time, and expiry |
| Action tokens | Hashed one-use approval bound to user, session, action class, method, and path |
| Strategies | User-owned configuration, folder, version, and version history |
| Runs | User-owned mode, strategy, trades, metrics, P&L, and created time |
| Trade history | Per-user and per-date broker or paper activity |
| Journals | Per-user dated trading notes and structured review data |
| Financial plans | One current user-owned planning document |
| Scalp trades | User-owned scalp ledger records |
| Test Bench/Fib backtests | Repeatable experiment identity, result summary, and payload |
| Application state | Engine snapshots and control-plane state that must survive restart |

SQLite uses WAL mode for concurrent reads and a busy timeout for short write contention. Sensitive broker and MFA fields are encrypted before storage. The server state root and per-user folders are writable only to the service account.

## 1.7 Authentication and action authorization

### 1.7.1 Normal sign-in

1. The user submits username and password.
2. bcrypt verifies the password hash.
3. If MFA is enabled, a current TOTP code is also required.
4. The database atomically prevents the same TOTP time-step from being reused.
5. A server-side session is created and returned in a protected cookie.
6. Every private route resolves the session and checks that the user is still active.

### 1.7.2 Sensitive action step-up

For protected changes, the browser first asks for a fresh MFA code. The server issues a short-lived token bound to:

- The user ID
- The current login session
- The action class
- The HTTP method
- The exact API path
- A single use and expiry time

The protected request sends that token once. SQLite consumes it atomically, so replaying the same approval fails.

## 1.8 Deployment and recovery

| Phase | Behavior |
|---|---|
| Pre-cutover backup | Create and verify a backup of SQLite and runtime state |
| Migration | Apply supported JSON-to-SQLite or runtime-storage migrations |
| Standby start | Start new code on the unused port with engine restore disabled |
| Standby health | Verify imports, database, and `/api/health` before ownership moves |
| State save | Ask the old active worker to persist engine state |
| Ownership handover | Stop the old worker, mark the new port active, restore engines, start automatic strategy mothers, and wake the Journal chart job on the new worker |
| Traffic cutover | Validate Nginx and move public traffic only after engine ownership is live |
| Boot ownership | Enable the active systemd instance and disable the old port |
| Post-cutover | Re-run migration checks, health check, and verified backup job |

The PhilForge systemd service binds to loopback, starts memory pressure at 300 MB, enforces a 380 MB hard limit, and caps swap at 64 MB. These limits keep one heavy backtest from taking down Nginx and CryptoForge on the shared host.

## 1.9 Backup and failure containment

1. A systemd timer runs the PhilForge backup service daily.
2. Backup creation is followed by archive verification.
3. The rollout workflow also creates a manual pre-cutover backup.
4. Active and standby ports are never allowed to restore engines at the same time.
5. Dhan token generation uses a file lock so two workers do not request competing tokens.
6. MarketFeed reconnect logic preserves subscriptions and rebuilds the feed after interruption.
7. Off-session loops reduce unnecessary broker calls and rate-limit pressure.
8. Engine recovery skips stale or unsafe live state that requires manual broker reconciliation.
9. systemd restarts the failed PhilForge service without deliberately restarting CryptoForge.
10. The deploy gate refuses a normal cutover during the NSE session while a current strategy engine is running.
11. Expired local backup archives are pruned before the free-space reserve is enforced, so stale backups cannot prevent their own replacement.

## 1.10 Architecture invariants

1. Every user-owned database query must include the correct user ID.
2. Every user-visible WebSocket event must go only to the owning user.
3. Per-user broker credentials must stay encrypted at rest and server-side.
4. Sensitive actions require exact, fresh, one-use authorization when their route class is protected.
5. Native exchange OHLC must remain the chart truth; visual transforms must not change engine prices.
6. A fixed option campaign keeps its resolved contract unless a documented strategy rule says otherwise.
7. Missing historical option data must be disclosed, not guessed.
8. Paper and live order paths must remain visibly and technically separate.
9. Only the active blue-green worker may restore and own engines.
10. Horizontal scaling is unsafe until distributed ownership, fencing, shared persistence, and idempotent broker commands are designed.

## 1.11 Evidence map

| Subject | Source area |
|---|---|
| Application and routes | `app.py` |
| Authentication and action approval | `auth.py`, auth middleware in `app.py` |
| Multi-tenant database | `db.py` |
| Dhan broker | `broker/dhan.py` |
| Dhan live feed | `engine/market_feed.py` |
| Upstox history | `data/cascade_upstox.py`, `upstox_token_manager.py` |
| Standard engines | `engine/live.py`, `engine/paper_trading.py`, `scalp.py` |
| Specialist engines | `engine/cascade_*`, `engine/fib_*`, `engine/candle_*`, `engine/two_red_equity.py` |
| Front end | `strategy.html`, standalone HTML pages, and `static/` |
| Deployment and backup | `deploy/`, `scripts/backup_philforge.py`, `.github/workflows/` |

## 1.12 Production changes since the original blueprint

The 14 August document was the first complete architecture snapshot. This revision folds in the production work completed after that document was created; the table is an architecture change register, not a substitute for the detailed chapters that follow.

| Area | Production change now reflected | Primary evidence |
|---|---|---|
| Trading workspace | Cascade is the first/default desk; Scalp, Cascade, and Equity retain the last selected page and subview; Scalp uses compact panels with exit rules under Advanced | `static/philforge-app.js`, `static/philforge-app.css`, `strategy.html` |
| Workspace appearance | PhilForge skins now share CryptoForge's palette discipline across panels, text, headings, navigation, semantic values, light mode, and PWA layouts without recolouring the logo | `static/philforge-theme.js`, `static/philforge-app.css` |
| Fib Boundary | Manual and 09:15 automatic mothers share the measured ladder, persisted settings, charts, saved replay, and a current repository-generated tearsheet | `engine/fib_touch_ladder.py`, Fib Boundary routes in `app.py`, `docs/assets/fib-boundary-tearsheet.html` |
| Candle Entry | Manual and automatic box mothers use the same backtest/paper engine, current-session monitor, restart-safe state, campaign chart, saved replay, and tearsheet evidence | `engine/candle_ladder.py`, `engine/candle_entry_offline.py`, Candle Entry routes in `app.py`, `docs/assets/candle-entry-tearsheet.html` |
| Gap Carry | The late-session-to-next-session campaign now has manual/auto controls, paper lifecycle, backtest, campaign chart, exports, and a full tearsheet | `engine/gap_carry.py`, Gap Carry routes in `app.py`, `docs/assets/gap-carry-tearsheet.html` |
| Chart interaction | Specialist charts use native OHLC, consistent full-chart dialogs, stable polling refresh, and phone-safe pan, pinch, zoom, and reset controls | chart renderers in `static/philforge-app.js`, chart UI tests |
| Evidence library | Assets now opens the five-year, Fib Boundary, Candle Entry, and Gap Carry tearsheets beside both repository-backed architecture blueprints | `static/philforge-architecture-page.js`, `docs/assets/`, `tools/tearsheet/` |
| Journal automation | After market hours the system creates missing three-session NIFTY and SENSEX 5-minute charts with CPR, EMA20, and gap completion; both indices are stored in the existing date folder and generated duplicate folders are consolidated safely | `journal_charts.py`, Journal chart scheduler and routes in `app.py` |
| Deployment continuity | Blue-green cutover is session guarded; the active handover restarts automatic strategy mothers and wakes Journal consolidation immediately; backups prune expired archives before disk checks | `deploy/cd-deploy.sh`, `/api/restore-engines`, `scripts/backup_philforge.py` |
| View continuity | Refresh restores the last page, Trading desk, strategy subview, Assets view, tearsheet, Journal panel, Live engine, and Fib Boundary mother mode | `PF_VIEW_STATE` in `static/philforge-app.js` |

---

**PhilForge Chapter 1 is complete.**

---

# Chapter 2: Full Site Map and Routing Graph

## 2.1 Route hierarchy

PhilForge has public story pages, a private single-page terminal, private standalone tools, private architecture pages, JSON APIs, and a user-scoped WebSocket.

- Public story → `/` → shared Forge landing; `/equities` → PhilForge equities story.
- Private terminal → `/app` → Dashboard, Portfolio, Insights, Live, Equity, Scalp, Cascade, Builder, Journal, Results, and Architecture.
- Insights → Heatmap and Study Lounge tabs.
- Equity → Cash Cascade scanner/campaign, Two Red equity, manual order, GTT, and Forever-order views.
- Cascade → Fib Boundary, Candle Entry, Gap Carry, Fib Space, Recovery, and Test Bench.
- Journal → chart library, dated journal, and financial plan.
- Standalone private tools → `/charts-viewer`, `/market-movers`, `/study-lounge`, and `/architecture`.
- Architecture → `/architecture/cryptoforge` and `/architecture/philforge`; trusted content comes from `/architecture/content/{platform}`.
- Control plane → identity, MFA, users, broker setup, engine ownership, backups, restore, health, and feed status.
- Trading plane → standard backtest/paper/live, scalp, terminal orders, specialist strategy engines, and user-scoped `/ws` events.

## 2.2 Page-route catalog

| Route or workspace | Access | Primary purpose |
|---|---|---|
| `/` | Public | Shared Forge introduction and product entry points |
| `/equities` and `/equities/` | Public | PhilForge equities product story |
| `/app` → Dashboard | Authenticated | Account, market, engine, run, and transaction overview |
| `/app` → Portfolio | Authenticated | Balance, P&L, paper/live session history, monthly ledger, and trades |
| `/app` → Insights | Authenticated | Nifty heatmap, movers, market context, and embedded Study Lounge |
| `/app` → Live | Authenticated | Standard live and paper engine monitors |
| `/app` → Equity | Authenticated | Cash-market terminal, Cascade, Two Red, manual order, GTT, and Forever orders |
| `/app` → Scalp | Authenticated | Option scalp setup, active trades, charts, exits, and history |
| `/app` → Cascade | Authenticated | Fib Boundary, Candle Entry, Gap Carry, Fib Space, Recovery, and Test Bench workspaces |
| `/app` → Builder | Authenticated | Strategy construction, validation, backtest, save, and deployment |
| `/app` → Journal | Authenticated | Chart library, daily journal, and financial plan |
| `/app` → Results | Authenticated | Saved run archive, analytics, comparison, export, and cleanup |
| `/charts-viewer` | Authenticated | Standalone filesystem-backed chart library |
| `/market-movers` | Authenticated | Standalone Nifty 50 market-movers board |
| `/study-lounge` | Authenticated | Standalone study asset library and player |
| `/architecture` | Authenticated | Cross-platform Architecture Atlas |
| `/architecture/{platform}` | Authenticated | Visual CryptoForge or PhilForge blueprint reader |
| Account/Admin overlays | Authenticated; role-scoped | Profile, password, MFA, broker, users, action approval, and appearance |

## 2.3 Content, identity, and control routes

| Route family | Methods and paths | Responsibility |
|---|---|---|
| Static identity | `GET /logo.jpg`; `GET /logo.png`; `GET /favicon.ico`; `GET /apple-touch-icon.png`; `GET /manifest.webmanifest`; `GET /site.webmanifest`; `GET /sw.js` | Serve the product identity and PWA shell |
| Search policy | `GET /robots.txt`; `GET /sitemap.xml` | Allow public story discovery while excluding private terminal routes |
| Study and charts | `GET /study-assets/{asset_path:path}`; `GET /api/study-library`; `GET /api/charts/tree`; `GET /api/charts/daily-status`; `GET /api/charts/images/{year}/{month}/{day}`; `GET /charts-static/{year}/{month}/{day}/{filename}`; `POST /api/upload-chart`; `DELETE /api/charts/delete/{year}/{month}/{day}/{filename}`; `PATCH /api/charts/rename/{year}/{month}/{day}/{filename}`; `PATCH /api/charts/rename-folder`; `POST /api/charts/create-folder`; `POST /api/charts/reorder` | Read and manage the user chart/study library and report the after-market chart job |
| Journal and plan | `GET /api/journal/list`; `GET /api/journal/{date_str}`; `PUT /api/journal/{date_str}`; `DELETE /api/journal/{date_str}`; `GET /api/financial-plan`; `PUT /api/financial-plan` | Store dated reflection and one user-owned financial plan |
| Architecture | `GET /architecture`; `GET /architecture/docs/{platform}`; `GET /architecture/content/{platform}`; `GET /architecture/{platform}` | Render private, repository-backed visual architecture pages |
| Authentication | `POST /api/auth/login`; `GET /api/auth/status`; `POST /api/auth/logout`; `POST /api/auth/mfa/enroll/start`; `POST /api/auth/mfa/enroll/verify`; `DELETE /api/auth/mfa`; `POST /api/auth/action-token` | Login, MFA lifecycle, logout, and one-use sensitive-action approval |
| User account | `PUT /api/user/password`; `GET /api/user/profile`; `GET /api/user/execution-ip-status`; `PUT /api/user/broker`; `DELETE /api/user/broker` | Maintain the current user's identity and encrypted broker configuration |
| Admin users | `GET /api/admin/users`; `POST /api/admin/users`; `PUT /api/admin/users/{user_id}/toggle`; `PUT /api/admin/users/{user_id}/password`; `POST /api/admin/users/{user_id}/copy-examples` | Create, suspend, reset, and seed accounts without crossing normal ownership rules |
| Runtime control | `GET /api/engine-control/status`; `GET /api/admin/engines`; `POST /api/emergency-stop`; `POST /api/save-state`; `POST /api/restore-engines`; `GET /api/health`; `GET /api/feed/status` | Inspect ownership, stop engines, persist state, restore safely, and report health |
| Broker token | `GET /api/token-status`; `POST /api/refresh-token`; `POST /api/broker/check`; `POST /api/broker/connect` | Maintain and validate Dhan authentication |

## 2.4 Market, portfolio, and standard-engine routes

| Route family | Methods and paths | Responsibility |
|---|---|---|
| Market context | `GET /api/market-movers/nifty50`; `GET /api/ticker`; `GET /api/expiry-dates`; `GET /api/expiry-list/{symbol}`; `GET /api/option-ltp` | Supply normalized market, expiry, and option-price context |
| Dashboard | `GET /api/dashboard/summary`; `POST /api/dashboard/recent-transactions/bulk-delete` | Build the overview and selectively clean its local transaction list |
| Portfolio | `GET /api/portfolio/summary`; `GET /api/broker/trades`; `GET /api/portfolio/backfill`; `GET /api/backfill/status`; `GET /api/portfolio/history` | Reconcile broker trades and build durable history |
| Strategy contract | `POST /api/validate-strategy`; `GET /api/strategies`; `POST /api/strategies`; `POST /api/strategies/folders`; `GET /api/strategies/{sid}/versions`; `PUT /api/strategies/{sid}`; `DELETE /api/strategies/{sid}` | Validate, organize, version, and maintain user strategies |
| Backtest jobs | `POST /api/backtest/jobs`; `GET /api/backtest/jobs`; `GET /api/backtest/jobs/{job_id}`; `POST /api/backtest` | Run expensive simulation outside the request loop and expose its status/result |
| Standard live | `POST /api/live/start`; `POST /api/live/stop`; `GET /api/live/status`; `GET /api/live/debug`; `GET /api/live/trades/csv`; `POST /api/live/exit-position` | Start, monitor, stop, inspect, export, and explicitly exit a Dhan-backed live engine |
| Standard paper | `POST /api/paper/start`; `POST /api/paper/stop`; `GET /api/paper/status`; `POST /api/paper/exit-position`; `GET /api/paper/trades/csv` | Run the matching simulated engine and its ledger |
| Engine inventory | `GET /api/engines/all`; `POST /api/engines/dismiss` | Aggregate user-owned engines and dismiss completed display records |
| Run archive | `GET /api/runs`; `POST /api/runs/bulk-delete`; `POST /api/runs/cleanup-empty`; `GET /api/runs/{rid}`; `PUT /api/runs/{rid}`; `DELETE /api/runs/{rid}`; `GET /api/runs/{rid}/csv` | Store, annotate, export, and clean user-owned results |
| Direct broker | `POST /api/orders/place`; `GET /api/orders`; `GET /api/orders/{order_id}/status`; `DELETE /api/orders/{order_id}`; `GET /api/positions`; `GET /api/funds` | Place and inspect explicit account orders, positions, and funds |
| Browser stream | `WS /ws` | Send only the owning user's engine and trade events to that user's browser sockets |

## 2.5 Specialist engine routes

| Engine family | Methods and paths | Responsibility |
|---|---|---|
| Cascade backtest and replay | `POST /api/replay/export-ohlcv`; `POST /api/cascade/backtest` | Export normalized candles and replay Cascade with option-data integrity |
| Options Cascade paper | `GET /api/cascade/paper/status`; `GET /api/cascade/paper/chart`; `POST /api/cascade/paper/start`; `POST /api/cascade/paper/stop`; `POST /api/cascade/paper/kill`; `DELETE /api/cascade/paper`; `GET /api/cascade/live-gate` | Own the fixed-contract paper campaign and expose the explicit live-readiness gate |
| Candle Entry | `GET /api/candle-entry/paper/status`; `POST /api/candle-entry/paper/start`; `POST /api/candle-entry/paper/kill`; `GET /api/candle-entry/paper/chart`; `POST /api/candle-entry/backtest`; `GET /api/candle-entry/backtests/latest`; `POST /api/candle-entry/auto` | Backtest, run, chart, close, and automate the two-red box ladder |
| Gap Carry | `GET /api/gap-carry/paper/status`; `POST /api/gap-carry/paper/start`; `POST /api/gap-carry/paper/kill`; `GET /api/gap-carry/paper/chart`; `POST /api/gap-carry/backtest`; `GET /api/gap-carry/backtests/latest`; `POST /api/gap-carry/auto` | Backtest, run, chart, close, and automate the late-session carry campaign |
| Fib Boundary | `GET /api/fib-boundary/symbols`; `GET /api/fib-boundary/paper/status`; `POST /api/fib-boundary/paper/start`; `POST /api/fib-boundary/paper/arm`; `POST /api/fib-boundary/paper/kill`; `GET /api/fib-boundary/paper/chart`; `POST /api/fib-boundary/live/{symbol}/arm`; `POST /api/fib-boundary/live/{symbol}/kill`; `POST /api/fib-boundary/backtest`; `GET /api/fib-boundary/backtests`; `GET /api/fib-boundary/backtests/{run_id}/export.json`; `GET /api/fib-boundary/backtests/{run_id}/export.csv` | Build, test, monitor, arm, and close multi-symbol fib touch ladders |
| Option archive | `GET /api/options/archive`; `GET /api/options/archive/export.csv` | Expose exact contract coverage and export its audit catalog |
| Test Bench | `POST /api/test-bench/run`; `GET /api/test-bench/results`; `GET /api/test-bench/results/{run_id}`; `DELETE /api/test-bench/results/{run_id}` | Save repeatable historical mother-candle experiments |
| Fib Space | `POST /api/fib-space/paper/start`; `POST /api/fib-space/paper/mother`; `GET /api/fib-space/paper/campaign`; `POST /api/fib-space/paper/campaign/delete`; `GET /api/fib-space/paper/chart`; `POST /api/fib-space/paper/stop`; `GET /api/fib-space/paper/status` | Build geometry from a selected mother and operate its paper campaign |
| Recovery | `POST /api/recovery/paper/start`; `POST /api/recovery/paper/mother`; `POST /api/recovery/paper/drop`; `POST /api/recovery/paper/stop`; `GET /api/recovery/paper/status` | Track two-red recovery structure and its carried ledger debt |
| Cash Cascade terminal | `GET /api/terminal/cascade/status`; `GET /api/terminal/cascade/chart`; `POST /api/terminal/cascade/start`; `POST /api/terminal/cascade/stop`; `POST /api/terminal/cascade/kill`; `DELETE /api/terminal/cascade`; `GET /api/terminal/cascade/closed`; `DELETE /api/terminal/cascade/closed/{archive_id}`; `GET /api/terminal/cascade/scan`; `GET /api/terminal/cascade/scan/chart` | Scan and operate cash equity/ETF Cascade campaigns |
| Equity universe and quote | `GET /api/terminal/nifty200`; `GET /api/terminal/nifty100`; `GET /api/terminal/nifty50`; `GET /api/terminal/quote` | Resolve supported cash instruments and current quote context |
| Two Red equity | `GET /api/two-red/status`; `POST /api/two-red/start`; `POST /api/two-red/stop`; `POST /api/two-red/kill`; `DELETE /api/two-red`; `GET /api/two-red/mothers`; `GET /api/two-red/chart` | Find mother structures and run paper equity campaigns |
| Terminal execution | `POST /api/terminal/order`; `POST /api/terminal/gtt`; `GET /api/terminal/forever`; `DELETE /api/terminal/forever/{order_id}` | Place explicit cash orders, create GTT orders, and manage Forever orders |
| Scalp | `GET /api/scalp/trades`; `POST /api/scalp/trades/bulk-delete`; `DELETE /api/scalp/trades/{tid}`; `GET /api/scalp/trades/{trade_id}/chart`; `GET /api/scalp/status`; `POST /api/scalp/start`; `POST /api/scalp/stop`; `POST /api/scalp/entry`; `POST /api/scalp/exit/{trade_id}`; `POST /api/scalp/kill-all`; `PUT /api/scalp/trades/{trade_id}/targets` | Operate and audit the user-owned option scalp engine |

## 2.6 Universal layout and context shifting

The terminal header shows identity, broker state, emergency control, account/settings access, and product navigation. Page buttons change the active workspace without reloading `/app`. Each workspace initializes only the data and timers it needs. The active user ID is resolved on the server for every private database read, engine action, and WebSocket send.

1. The browser changes visual context.
2. The server does not change ownership context; it always derives the user from the session.
3. Page polling supplies recoverable snapshots.
4. `/ws` supplies faster user-scoped events.
5. Engine registries use user plus engine identity, so identical instruments can run for different users without sharing control.
6. Admin views may aggregate metadata, but normal user routes cannot read another user's journal, broker secret, run, or engine.

---

# Chapter 4: Page-by-Page Deep Dive — PhilForge

## 4.1 Public Forge landing — `/`

### Intent and wire

Introduce the shared Forge estate and direct visitors to the correct product without loading private data.

### Layout components

Brand story, CryptoForge and PhilForge entry cards, public-origin links, and responsive marketing sections.

### Wiring and data patterns

Static HTML and versioned assets only. Private account, broker, and trading APIs are not called.

### Interactive workflow

1. Read the product split.
2. Choose equities or crypto.
3. Continue to the relevant sign-in/terminal origin.

## 4.2 Equities landing — `/equities`

### Intent and wire

Explain PhilForge's Indian-market purpose before a user enters the terminal.

### Layout components

Product story, session-aware feature blocks, safety language, and terminal entry link.

### Wiring and data patterns

Static, cache-versioned landing assets; no broker credential or user-state call.

### Interactive workflow

1. Review supported workflows.
2. Follow the private terminal entry.
3. Authenticate before any user data appears.

## 4.3 Unlock and account access

### Intent and wire

Establish a user identity, role, and optional MFA proof before private state is read.

### Layout components

Username, password, optional TOTP input, safe error state, brand, and appearance controls.

### Wiring and data patterns

Login creates a database-backed session. MFA replay protection is atomic. Account overlays later use profile, password, broker, MFA, and action-token routes.

### Interactive workflow

1. Enter credentials and current MFA code when enabled.
2. Continue to `/app` after the session is created.
3. Use Account to update password, broker, or MFA.
4. Supply a fresh MFA code for protected actions; use the returned action token once.

## 4.4 Dashboard workspace

### Intent and wire

Give a compact morning and incident view of capital, P&L, market context, engines, runs, and transactions.

### Layout components

Balance and performance cards, market indicators, FII/DII context, active engines, recent runs, recent transactions, filters, and quick actions.

### Wiring and data patterns

`/api/dashboard/summary`, engine inventory, market context, broker status, and run history are combined server-side under the current user.

### Interactive workflow

1. Confirm session, token, feed, and balance posture.
2. Review current engines and transaction anomalies.
3. Open the relevant specialist page.
4. Bulk-delete only selected local recent-transaction rows when cleanup is intended.

## 4.5 Portfolio workspace

### Intent and wire

Provide a clean capital ledger for real and paper activity without blending the two.

### Layout components

Available balance, today's P&L, trades, win rate, Paper Trade P&L, monthly real-trade calendar, Today's Trades, and year-to-date analytics.

### Wiring and data patterns

Portfolio summary, broker trades, backfill status, portfolio history, and engine state form the page. Broker records remain the source for real trades; application runs supply paper rows.

### Interactive workflow

1. Refresh broker and local history.
2. Check data-source and freshness labels.
3. Navigate the monthly ledger.
4. Compare paper sessions with real trades and account balance.
5. Trigger backfill only when the history panel identifies a supported gap.

## 4.6 Insights workspace

### Intent and wire

Separate market observation and deliberate study from order entry.

### Layout components

Heatmap tab with Nifty winners, losers, weighted canvas, sector/context rail; Study tab with featured asset, tracks, preview player, and study guidance.

### Wiring and data patterns

Market Movers uses `/api/market-movers/nifty50`; Study Lounge uses `/api/study-library` and protected `/study-assets/*` media.

### Interactive workflow

1. Refresh the market snapshot.
2. Inspect breadth and weighted contribution.
3. Switch to Study Lounge.
4. Filter or select a deck, audio, or video and open its preview.

## 4.7 Live workspace

### Intent and wire

Monitor standard strategy engines separately from Scalp and specialist Cascade services.

### Layout components

Paper and live tabs, run identity, instrument, strategy, position, P&L, events, explicit stop, and exit controls.

### Wiring and data patterns

Standard live/paper routes and user-scoped `/ws` events drive the panels. Debug and CSV routes provide deeper evidence.

### Interactive workflow

1. Confirm the correct mode and run owner.
2. Monitor signal, position, stop, target, and market-close state.
3. Exit a named position or stop the named engine.
4. Export and reconcile the final ledger.

## 4.8 Equity terminal

### Intent and wire

Provide professional cash-market research, paper campaign control, and explicit order tools in one workspace.

### Layout components

Universe/quote controls, Cash Cascade scanner, open positions, campaign start form, chart, events, round ledger, closed campaigns, Two Red scanner/campaigns, manual order ticket, GTT, and Forever orders.

### Wiring and data patterns

Terminal universe, quote, Cascade, Two Red, order, GTT, and Forever routes are distinct. Dhan security IDs and exchange segments are resolved server-side.

### Interactive workflow

1. Select a Nifty universe and scan candidate instruments.
2. Open a candidate chart and inspect native OHLC and mother geometry.
3. Start a paper Cash Cascade or Two Red campaign.
4. Monitor positions, rounds, events, and closed records.
5. For an explicit broker order, verify instrument, side, product, quantity, price, and action approval before submission.
6. Review or cancel only the intended Forever order.

## 4.9 Scalp workspace

### Intent and wire

Manage fast index-option entries with visible contract, target, stop, and exit evidence.

### Layout components

Engine setup, option selector, active-trade table, per-trade chart, target editor, exit, kill-all, history, filters, and delete tools.

### Wiring and data patterns

Dhan tick/feed state drives the monitor. Expiry and LTP routes resolve contract context. Scalp routes are user-scoped and persist closed trades.

### Interactive workflow

1. Start the user's scalp monitor.
2. Resolve expiry, strike, option type, lot size, and premium.
3. Submit an entry and wait for broker or paper acknowledgement.
4. Edit targets or exit the named trade.
5. Use kill-all only for the intended user engine and verify broker state afterwards.

## 4.10 Options Cascade — Fib Boundary

### Intent and wire

Build a swing-anchored fib touch ladder, buy one lot at qualified levels, and close the basket under target, stop, time, structure, or expiry rules.

### Layout components

Symbol list, mother/anchor inputs, paper start, campaign monitors, rung table, chart, arm/kill controls, backtest form, saved results, and exports.

### Wiring and data patterns

Fib Boundary routes call `engine/fib_touch_ladder.py` through a user-owned host. Dhan supplies current candles/contracts; the option archive supplies exact historical premiums.

### Interactive workflow

1. Select a supported symbol and anchor period.
2. Review swing, side, levels, expiry, and capital.
3. Start paper mode.
4. Arm only a fully reviewed symbol.
5. Monitor fills and basket exits.
6. Backtest and export the exact result before considering any live arm.

## 4.11 Options Cascade — Candle Entry

### Intent and wire

Climb a defined timeframe ladder after two-red structure and buy recovery under the engine's paper rules.

### Layout components

Start form, at-a-glance monitor, rung table, NIFTY candle table, event table, and kill control.

### Wiring and data patterns

Candle Entry routes own a user-scoped paper host. Historical replay can warm the structure, but only post-start paper fills count toward the paper ledger.

### Interactive workflow

1. Choose the mother and campaign settings.
2. Start paper monitoring.
3. Review each rung's timeframe, lots, stop, and fill state.
4. Kill and close through the explicit route when required.

## 4.12 Options Cascade — Gap Carry

### Intent and wire

Run the measured late-session option campaign from its afternoon signal into a controlled next-session exit without turning it into an unrelated intraday rule.

### Layout components

Manual/automatic mode, underlying and contract controls, entry and exit windows, strategy variants, paper start/kill, live campaign monitor, campaign chart, backtest, saved result, export, and in-place tearsheet link.

### Wiring and data patterns

Gap Carry routes call `engine/gap_carry.py` for historical, paper and live lifecycles. The signal is the candle that has CLOSED by the entry time (15:05–15:10 on 5m) and the contract is priced and bought at 15:10, identically in the replay and the live loop; the loop polls every two seconds around the entry clock. Signal candles and exact option premiums stay distinct, the campaign persists restart-safe state including whether it is live, and the chart renders the campaign window and trade evidence from the same result contract.

### Interactive workflow

1. Select the underlying, measured variant, contract rule, and paper mode.
2. Start manually or enable the automatic session host.
3. Confirm the afternoon entry and next-session exit windows before activation.
4. Monitor the running campaign, option marks, chart, and event evidence.
5. Kill only through the named campaign control and retain its result for review.
6. Compare the stored backtest and full tearsheet before changing a production setting.

## 4.13 Options Cascade — Fib Space

### Intent and wire

Use a chosen mother candle to draw trendlines and fibs, then buy where valid fib structures converge.

### Layout components

Host start, mother selection, campaign summary, chart, fills/rounds, stop, and delete controls.

### Wiring and data patterns

`engine/fib_space_geometry.py` builds native-price geometry. `engine/fib_space_cascade.py` simulates the campaign. Host routes persist one user-owned paper campaign.

### Interactive workflow

1. Start the host and choose a valid mother.
2. Inspect trendline and fib construction.
3. Start/observe the paper campaign.
4. Stop monitoring or delete only after the campaign is no longer needed.

## 4.14 Options Cascade — Recovery

### Intent and wire

Track a two-red break, buy a defined recovery, and carry prior loss as visible recovery debt rather than hiding it.

### Layout components

Host start, mother/drop inputs, status, open exposure, debt/target view, events, and stop.

### Wiring and data patterns

Recovery routes call the user-scoped recovery host. The engine's ledger records each drop, entry, exit, and carried amount.

### Interactive workflow

1. Start the paper host.
2. Select the mother and record a qualified drop.
3. Observe the recovery entry and stop logic.
4. Stop the host and retain its ledger for review.

## 4.15 Options Cascade — Test Bench

### Intent and wire

Replay one historical mother-candle experiment without starting a live or paper monitor.

### Layout components

Experiment form, result metrics, event/trade tables, saved-run table, paging, detail, and delete.

### Wiring and data patterns

Test Bench routes store repeatable inputs and outputs. Historical sources are read-only and any missing premium is disclosed.

### Interactive workflow

1. Select historical identity and parameters.
2. Run the experiment.
3. Inspect source coverage, trades, costs, and warnings.
4. Save or delete the named result.

## 4.16 Strategy Builder

### Intent and wire

Create a validated Indian-options strategy with signal conditions and one or more execution legs.

### Layout components

Folder/name, segment, instrument, dates, market window, indicators, IF/AND/OR conditions, CE/PE legs, strike and expiry selection, lots, per-leg and combined risk, slippage, fees, save, backtest, and deploy.

### Wiring and data patterns

Strategy validation uses `engine/strategy_contract.py`. CRUD stores versions. Backtest jobs call `engine/backtest.py`; paper/live routes consume the saved contract.

### Interactive workflow

1. Define signal instrument and market window.
2. Add indicators and Boolean conditions.
3. Define each option leg and risk rule.
4. Set realistic spread, entry slippage, exit slippage, and fee assumptions.
5. Validate, save, and backtest.
6. Deploy paper first and promote only after evidence review.

## 4.17 Journal workspace

### Intent and wire

Combine visual chart evidence, daily reflection, and financial planning in one review workflow.

### Layout components

Chart folder tree, upload/rename/delete/reorder tools, image viewer, automated NIFTY/SENSEX evidence, daily-job status, dated journal editor, journal list, financial-plan tab, and structured planning table.

### Wiring and data patterns

Chart routes use the protected chart directory. The after-market job renders missing three-session NIFTY and SENSEX 5-minute charts with CPR, EMA20, and gap-completion evidence. It reuses the owner's existing date folder, adds the second index beside the first, and consolidates only exact generated files from legacy duplicate folders without overwriting manual charts. Journal and plan routes use user-owned database records. Uploaded filenames and paths are validated before filesystem access.

### Interactive workflow

1. Choose an existing trading date or create a folder for genuinely new manual evidence.
2. Review the NIFTY and SENSEX charts stored together for that date.
3. Upload and organize any additional evidence without duplicating the date folder.
4. Open the trading date and write the review.
5. Save or deliberately delete that date.
6. Update the financial plan separately from the daily journal.

## 4.18 Results workspace

### Intent and wire

Turn stored simulations and sessions into reviewable evidence rather than isolated headline P&L.

### Layout components

Run filters, analytics cards, equity chart, transactions analytics, monthly P&L, heatmap, trade ledger, comparison, export, edit, and cleanup tools.

### Wiring and data patterns

Run routes return user-owned records and assumptions. Charts and tables are rendered from that response. Bulk cleanup never reaches broker trade history.

### Interactive workflow

1. Filter comparable run modes.
2. Open a run and read assumptions, warnings, and data source first.
3. Review drawdown, costs, trades, monthly behavior, and heatmaps.
4. Export, annotate, compare, or delete selected local results.

## 4.19 Standalone chart, market, study, and architecture pages

### Intent and wire

Offer focused full-page experiences when the terminal layout would be distracting.

### Layout components

`/charts-viewer` focuses on chart folders and images; `/market-movers` on Nifty breadth; `/study-lounge` on learning assets; `/architecture` on the system map and both blueprints.

### Wiring and data patterns

All four pages require a valid session and call the same protected APIs used by their embedded terminal equivalents. The architecture reader fetches trusted repository Markdown and renders it as a non-download page.

### Interactive workflow

1. Open the focused route from the terminal.
2. Use its scoped filters, navigation, or preview controls.
3. Return to `/app` without changing active engine ownership.

## 4.20 Admin and account portals

### Intent and wire

Keep identity, broker, MFA, action authorization, and user management separate from trade forms.

### Layout components

Profile, password, execution-IP status, Dhan credentials, MFA enrollment/removal, action-auth prompt, appearance, user table, create user, toggle, reset password, and copy examples.

### Wiring and data patterns

Normal account routes are current-user scoped. Admin routes require role checks. Broker and MFA fields are encrypted at rest. Protected mutations consume exact one-use action tokens.

### Interactive workflow

1. Open Account or Admin from the header.
2. Select one scoped change.
3. Provide fresh MFA approval when required.
4. Submit once and verify the returned account or user state.

---

# Chapter 5: User Experience Fork — Console and Professional Terminal

## 5.1 Buyer's experience — Console

1. Open `philforge.in` and enter the private terminal.
2. Authenticate with username/password and MFA when enabled.
3. Check Dashboard token, feed, funds, and active-engine state.
4. Open Portfolio and confirm available balance and existing exposure.
5. Use Builder or a paper specialist workspace to define the trade.
6. Resolve the exact exchange instrument, expiry, strike, lot size, and premium.
7. Run a backtest or paper session first.
8. If an explicit live order is authorized, review side, quantity, product, order type, price, and action approval.
9. Submit once, then verify broker order status and Portfolio.
10. Review the result in Journal and Results.

## 5.2 Professional's experience — Terminal

| Professional need | Current implementation | Operating guidance |
|---|---|---|
| Multi-monitor use | `/app`, `/market-movers`, `/charts-viewer`, `/study-lounge`, and `/architecture` can occupy separate windows | Keep Dashboard/Portfolio visible beside the active Equity, Scalp, Cascade, or Live workspace |
| Advanced charting | Canvas/SVG native OHLC, fibs, trendlines, fills, orders, targets, scan windows, and replay views | Native exchange OHLC is the price truth; overlays must not rewrite candles |
| Hotkeys | Tab controls support standard keyboard navigation; no global order-entry/cancel hotkey contract is defined | Use explicit order controls and MFA approval; do not rely on undocumented keys |
| Routing override | Dhan is the live execution broker; user broker records and execution IP are protected settings | Do not redirect a running engine by editing credentials; stop and reconcile first |
| Ledger audit | Portfolio, broker trades, Results, specialist rounds/events, and Journal provide different evidence layers | Reconcile by user, engine/run, security ID, broker order ID, fill, charges, and timestamp |
| Emergency control | Global emergency stop plus engine-specific stop, kill, exit, and reconcile controls | Use the narrowest action that protects the account and preserves evidence |

## 5.3 Professional session layout

1. Display one: Dashboard, broker/feed health, funds, and active engines.
2. Display two: active execution workspace and chart.
3. Display three: Portfolio, orders, positions, and session ledger.
4. Optional display: Market Movers, Journal evidence, or Architecture/runbook.
5. Treat duplicate browser windows as duplicate command surfaces; submit a state-changing action only once.

---

# Chapter 6: Integrations, Strategies, and Backtesting Engine

## 6.1 Broker and data integration layer

| Integration | Role | Credential and safety boundary |
|---|---|---|
| Dhan REST | Orders, status, positions, funds, trades, quotes, historical candles, and scrip master | Per-user credentials are encrypted; protected live actions may require static IP and MFA action approval |
| Dhan MarketFeed | Event-driven live LTP and candle support | Singleton connection with subscription registry, reconnect, freshness, and user-engine consumers |
| Upstox history | Exact expired option instruments and candles for replay gaps | Used as historical data, not the PhilForge live execution broker |
| NSE public sources | Market context such as official combined daily information | Parsed defensively, cached, and never treated as order acknowledgement |
| yfinance | Selected supporting market context | Fallback/context only; not execution truth |
| Local option archive | Contract metadata and candle coverage | Missing spans remain visible; zero is never substituted for a missing premium |

## 6.2 Algorithmic strategy catalog

| Strategy family | Plain-English behavior | Execution boundary |
|---|---|---|
| Strategy Builder momentum | Uses conditions such as price above or crossing EMA, Supertrend, CPR, prior-day, or ORB levels; option legs define the actual trade | Backtest, paper, and selected live paths |
| Strategy Builder mean reversion | Uses validated conditions to enter when price is expected to return toward a moving average, band, pivot, or numeric level | Contract pattern, not a separate daemon |
| Standard multi-leg options | Resolves expiry and strike per leg, applies leg/combined stop, target, trailing, and square-off rules | Backtest, paper, and live with Dhan checks |
| Option Scalp | Opens a selected CE/PE contract and watches premium target/stop/manual exit | User-scoped paper/live engine |
| Options Cascade | Anchors index structure and keeps the chosen option contract fixed for the campaign | Paper-first; live gate explicit |
| Cash Cascade | Applies mother/fib ladder concepts to NSE equity or ETF shares | Terminal paper campaign in the current baseline |
| Fib Boundary | Finds a swing boundary and buys one-lot touches through configured deeper levels; closes the basket on explicit risk/time rules | Backtest, paper, and narrowly gated live controls |
| Candle Entry | Uses the selected two-red box mother, climbs its measured rung ladder, and can start from a manual or automatic market-session mother | Backtest and paper |
| Gap Carry | Opens the measured late-session option campaign and closes it in the configured next-session window, with explicit strategy variants | Backtest and paper |
| Fib Space | Draws mother-origin trendlines and fibs and acts where qualified structures overlap | Paper and historical simulation |
| Recovery | Carries visible recovery debt after a stopped trade and uses a defined later setup to recover it | Paper |
| Two Red Equity | Finds cash-market mother/two-red patterns and runs a share-based campaign | Paper |
| Test Bench | Replays one historical mother and stores a repeatable experiment | Simulation only |
| Grid trading | No generic fixed-price grid engine is confirmed | Cascade ladders are geometry-driven and must not be relabeled as a generic grid |
| Arbitrage | No cross-broker or cross-exchange arbitrage executor is confirmed | Multiple data sources do not create an arbitrage product |

## 6.3 Historical backtest journey

1. Validate the user-owned strategy contract and date range.
2. Resolve the signal instrument, timeframe, session, holiday, and market window.
3. Load cached Dhan/NSE candles and fetch only supported missing spans.
4. Normalize timestamps to IST and preserve native OHLCV.
5. Calculate selected indicators without future-data leakage.
6. Evaluate entry/exit conditions in chronological order.
7. Resolve every option leg's expiry, strike rule, quantity, and contract identity at the correct event time.
8. Read exact option premiums from the archive or Upstox source.
9. If required premium data is missing, mark the run incomplete and withhold unproved P&L.
10. Apply spread, separate entry/exit slippage, brokerage, STT, exchange charges, SEBI charges, stamp duty, and GST assumptions.
11. Apply stops, targets, trailing rules, square-off, close-candle policy, and next-candle execution policy.
12. Store trades, costs, equity, metrics, warnings, source coverage, and run identity.

## 6.4 Metrics produced

| Metric | Meaning |
|---|---|
| Total trades, wins, losses, and win rate | Sample size and outcome frequency |
| Average profit and average loss | Typical size of each outcome |
| Win/loss ratio and profit factor | Balance of winning and losing value |
| Expectancy | Expected value per trade in the tested sample |
| Net P&L after fees | Result after the configured cost model |
| Maximum drawdown, value, and days | Worst peak-to-trough loss and recovery duration |
| Sharpe ratio | Return consistency using a 252-session market convention |
| Calmar ratio | Annual return compared with maximum drawdown |
| Winning and losing streaks | Consecutive outcome risk |
| Maximum profit and loss | Largest single-trade outcomes |
| Monthly, yearly, and day-of-week breakdown | Time concentration and seasonality evidence |
| Equity curve, heatmap, and complete ledger | Traceable evidence behind the summary |

## 6.5 Historical integrity rules

1. Native index/equity candles generate the signal.
2. Exact option candles price the option leg.
3. Strike, expiry, and lot size are time-sensitive contract facts.
4. Missing premium never becomes zero.
5. Current scrip-master data must not silently rewrite an expired contract.
6. Every result states its source, gaps, and execution assumptions.

---

# Chapter 7: Financials, Operational Costs, and Risk Engine

## 7.1 Cost model

These are planning references as of the snapshot date, not invoices. Brokerage and statutory charges must be reconciled to the user's current Dhan contract note.

| Cost item | Current architecture basis | Planning treatment |
|---|---|---|
| Shared Linux host | AWS Lightsail in Mumbai, shared with CryptoForge but isolated by origin, ports, service, and state | Public-IPv4 Linux bundles list 1 GB at $7/month, 2 GB at $12/month, and 4 GB at $24/month. Use the actual provisioned bundle. [AWS Lightsail pricing](https://aws.amazon.com/lightsail/pricing/) |
| Snapshots | Lightsail instance/disk snapshots plus application backup archives | AWS lists $0.05 per GB-month for snapshots; local archive retention also consumes instance disk |
| Dhan trading APIs | Live account and order interface | Dhan describes trading APIs as free; normal brokerage, taxes, and account charges still apply |
| Dhan Data API | Live/historical data service used by the platform | Dhan states ₹499 plus tax per 30-day subscription. [Dhan Data API subscription](https://dhan.co/support/platforms/dhanhq-api/how-does-the-dhanhq-data-api-subscription-work/) |
| Upstox data APIs | Exact historical option coverage and selected gap backfill | Upstox states trading and data APIs are free; brokerage applies if trading is used, but PhilForge uses this integration for history. [Upstox API pricing](https://upstox.com/trading-api/) |
| Dhan order cost | Brokerage plus STT, exchange transaction, SEBI, stamp, GST, and slippage | The specialist model defaults to ₹20 brokerage per order and configurable statutory rates; current contract notes override the model |
| Backtest compute | CPU, RAM, cache disk, and provider rate limits | Cost grows with symbols × date span × intervals × option contracts × strategy variants |
| Media/chart storage | Study assets, uploaded charts, option archive, market cache, and run exports | Monitor disk growth; archive or move immutable media before the shared disk becomes a runtime risk |
| Domain/TLS/alerts | Domain registration, DNS, certificates, and Telegram transport | Separate external costs; open-source Nginx and certificate tooling have no application licence fee |

## 7.2 Scaling-cost stages

| Stage | Trigger | Required change |
|---|---|---|
| Current single active worker | Small user set and controlled concurrent jobs | Lightsail bundle, snapshots, Dhan data subscription, and broker costs |
| Larger host | Blue-green overlap or backtests approach memory limits | Increase bundle size; keep one engine owner |
| Dedicated backtest worker | Jobs delay feed, orders, or status responses | Durable job queue, separate compute, and result store |
| Separate data/archive storage | Option history and media pressure local disk | Object/block storage with integrity, retention, and cache policy |
| Multi-node application | User or connection count exceeds one worker | Shared database, distributed fencing, idempotency, session store, and centralized observability |

## 7.3 Risk matrix

| Risk | Signal | Automated mitigation | Operator action |
|---|---|---|---|
| Dhan token expiry | Authentication error or token-status warning | Block new live entry; serialize refresh with token lock | Refresh once and verify account endpoints |
| Invalid execution IP | Broker rejection such as static-IP mismatch | Refuse retry storm and show execution-IP status | Correct broker registration before resubmission |
| MarketFeed disconnect | Feed stopped, reconnect count, or stale LTP | Reconnect with backoff; block stale-price entry | Check broker/feed health and subscriptions |
| Missing historical option candle | Cache gap or unavailable expired contract | Mark incomplete and withhold unproved P&L | Backfill the exact gap or accept an incomplete result |
| Wrong expiry/strike/lot | Contract rule or scrip-master mismatch | Validate identity and quantity before order | Compare current exchange circular/scrip master |
| Duplicate engine ownership | Old and new worker overlap | Restore disabled on standby; ownership handover is explicit | Stop stale owner and reconcile broker |
| User-data leakage | Query/event lacks correct owner | Reject request; user-scoped database and socket routing | Audit route and affected access logs |
| Replayed sensitive approval | Reused/expired action token or TOTP step | Atomic one-use consumption rejects replay | Request a fresh MFA approval |
| Partial/uncertain fill | Order accepted without complete trade evidence | Keep pending state and query order/trade APIs | Reconcile order ID, fills, quantity, and charges |
| Slippage/liquidity shock | Premium/spread leaves configured limits | Block new entries or apply explicit stop/exit rule | Reduce size or stand down; do not chase inferred price |
| Expiry-day risk | Contract reaches cutoff or expiry window | Time/expiry exit and square-off checks | Verify broker position is flat |
| Platform downtime | Health failure or systemd restart | Restart process; restore only verified user state | Check broker positions before enabling engines |
| Failed deployment | Standby health, backup, migration, or restore gate fails | Abort or roll back without duplicate ownership | Keep last healthy active port and investigate |
| SQLite corruption | Integrity or backup verification fails | Reject bad backup and preserve last verified archive | Recover into an isolated copy and verify tenants |
| Shared-host resource exhaustion | Memory pressure, OOM, disk full, or long request | systemd memory limits and background backtest jobs contain impact | Scale host or split workloads before raising limits |
| Market-wide gap/circuit event | Large opening gap, circuit, rejection, or missing liquidity | Respect session/circuit rules; block assumptions and new unsafe orders | Reconcile with exchange/broker and protect capital |

## 7.4 Risk-engine decision order

**Session owner → role and action approval → engine ownership → broker token and IP → feed freshness → market session → security ID and contract rules → quantity and funds → existing orders/positions → submit once → verify broker → persist user state → notify only that user**

## 7.5 Operational controls

1. Run verified backups daily and before cutover.
2. Keep standby engine restore disabled until ownership handover.
3. Refuse live action when data, token, contract, or quantity cannot be proved.
4. Keep paper and live labels, routes, ledgers, and controls distinct.
5. Compare modeled charges with actual broker contract notes.
6. Review user/engine/feed health before and after every release.

## 7.6 Blueprint completion and evidence

| Chapter | Coverage | Primary evidence |
|---|---|---|
| 1 | Platform architecture and ownership | `app.py`, `auth.py`, `db.py`, `broker/`, `engine/`, `deploy/` |
| 2 | Every screen and service-route family | FastAPI decorators, terminal and standalone templates |
| 4 | Every user-facing page and portal | `strategy.html`, standalone pages, and `static/philforge-app.js` |
| 5 | Console and professional workflows | UI controls, authorization, broker and journal paths |
| 6 | Integrations, specialist strategies, and backtesting | Dhan/Upstox adapters, strategy contract, backtest and specialist engines |
| 7 | Costs, scaling, and automated risk controls | provider references, fee models, deployment/backup gates, and safety tests |

**The PhilForge blueprint is complete across Chapters 1, 2, 4, 5, 6, and 7 for the 24 August 2026 production baseline, including the changes made after the original 14 August document. Chapter 3 belongs to the separate CryptoForge blueprint. Runtime configuration, broker records, exchange rules, and later code changes remain authoritative.**
