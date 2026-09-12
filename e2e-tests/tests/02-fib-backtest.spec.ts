/**
 * 02-fib-backtest.spec.ts
 * The Fib Boundary Backtest panel shows rupee P&L, and never lies about why
 * it couldn't.
 *
 * REWRITTEN 2026-08-07: the button now replays the SWING TOUCH LADDER -- the
 * same engine Start trades -- so the response shape changed completely and
 * these specs were rewritten against it rather than restored.
 *
 * Born from the 2026-08-01 session: every backtest of a recent mother died as
 * "1 premium gap … net P&L withheld" while Dhan had the bar all along (the
 * hybrid lookup keyed naive Dhan minutes against aware engine timestamps).
 * The server side of that fix is pinned by tests/test_fib_backtest_route_e2e.py;
 * this spec pins the panel: numbers render, a stale-priced leg is disclosed
 * quietly, and a broken data source is called a failure, not a market gap.
 * The backtest response here is the VERBATIM JSON that route test returns.
 */

import { test, expect, Page } from '@playwright/test';

async function openCascade(page: Page) {
  await page.locator('[data-pf-trading-page="options-cascade-page"]').first()
    .evaluate((button: HTMLElement) => button.click());
}


const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';
const BASE_ORIGIN = new URL(process.env.E2E_BASE_URL || process.env.BASE_URL || 'http://localhost:8000').origin;

// The real serializer output for the two-fill walk the route e2e replays
// (chart omitted — the panel only draws it on demand).
const backtestSuccess = {
  status: 'ok', mode: 'backtest', pricing: 'upstox_real_premiums', side: 'CE', timeframe: '15m',
  mother: { timestamp: '2026-07-17T14:15:00+05:30', high: 24367.3, low: 24280.55 },
  candles_replayed: 7, horizon_to: '2026-08-01', lot_size: 65,
  result: {
    status: 'closed',
    contract: { strike: 23850.0, expiry: '2026-08-25', option_type: 'CE', lot_size: 65 },
    entries: [
      { timestamp: '2026-07-20T09:45:00+05:30', spot: 23960.0, option_price: 500.0, lots: 1, quantity: 65, level: 4, leg_id: null, spend_inr: 32500.0, strike: 23850.0, option_type: 'CE', expiry: '2026-08-25' },
      { timestamp: '2026-07-20T10:30:00+05:30', spot: 23610.0, option_price: 300.0, lots: 2, quantity: 130, level: 8, leg_id: null, spend_inr: 39000.0, strike: 23500.0, option_type: 'CE', expiry: '2026-08-25' },
    ],
    exit_timestamp: '2026-07-20T10:45:00+05:30', exit_reason: 'target',
    target_index: 23886.825, average_spot: 23726.666666666668, index_move: 160.15833333333285,
    gross_pnl: 29900.0, costs_total: 268.26, net_pnl: 29631.74, fully_priced: true,
    data_gaps: [], premium_failures: [],
    premium_stale_fills: [
      '23500CE at 10:30 priced from the last trade 3 min earlier (10:27 bar, ₹300.00)',
      '23850CE at 10:45 priced from its next trade 2 min into the candle (10:47 bar, ₹520.00)',
      '23500CE at 10:45 priced from its next trade 2 min into the candle (10:47 bar, ₹520.00)',
    ],
  },
  note: 'Typed-mother fib ladder — L4 and L8 measured straight off 24,367.30 / 24,280.55.',
};

const backtestSourceFailure = {
  ...backtestSuccess,
  result: {
    ...backtestSuccess.result,
    status: 'data_gap', contract: null, entries: [],
    exit_timestamp: null, exit_reason: null, target_index: null, average_spot: null, index_move: null,
    gross_pnl: null, costs_total: null, net_pnl: null, fully_priced: false,
    data_gaps: ['missing option candle at 2026-07-22T10:00:00+05:30 for NIFTY 23900.0CE'],
    premium_failures: ['Dhan option candles unavailable for NIFTY 23900CE 2026-08-25: DH-901 token expired'],
    premium_stale_fills: [],
  },
};

// A signal-only replay that hit its target, VERBATIM from
// FibBoundaryPaper.get_status() with a historical premium lookup wired in —
// the campaign monitor must show the settled round, not "withheld".
const replayClosedStatus = {
  status: 'ok', mode: 'paper',
  campaign: {
    mode: 'paper', strategy: 'fib_boundary', running: false, status: 'CLOSED', side: 'CE', timeframe: '5m',
    pricing_mode: 'replay_history',
    pricing_warning: 'Historical replay — premiums priced from real Upstox/Dhan bars. No live order was sent.',
    replay_complete: true,
    mother: { timestamp: '2026-07-29T09:10:00', high: 24180, low: 24050 },
    contract: { underlying: 'NIFTY', strike: 24000, expiry: '2026-08-11', option_type: 'CE', lot_size: 65, security_id: '111' },
    rung_inr: 75000.0, target_index: null, average_index_entry: null, open_quantity: 0, entry_stop: null,
    boundaries: [
      { key: 'L4', level: 4, index_price: 23660, status: 'CLOSED' },
      { key: 'L8', level: 8, index_price: 23140, status: 'PENDING' },
    ],
    open_fills: [], signal_fills: [],
    rounds: [{
      round_id: 1, opened_at: '2026-07-29T09:25:00', closed_at: '2026-07-29T09:45:00',
      fills: [{ timestamp: '2026-07-29T09:25:00', index_price: 23640.0, option_premium: 150.0, lots: 7, quantity: 455, rung_keys: ['L4'], order_id: 'REPLAY' }],
      target_index: 23775.0, exit_index_price: 23775.0, exit_option_premium: 210.0, exit_quantity: 455,
      gross_pnl: 27300.0,
      costs: { buy_turnover: 68250.0, sell_turnover: 95550.0, brokerage: 40.0, stt: 59.72, exchange_transaction: 86.81, sebi: 0.16, stamp: 2.05, gst: 22.86, total: 211.6 },
      net_pnl: 27088.4, exit_reason: 'target',
    }],
    events: [
      { timestamp: '2026-07-29T09:25:00', event: 'signal_entry', level: 4, index_price: 23640, option_premium: 150.0 },
      { timestamp: '2026-07-29T09:45:00', event: 'round_closed', reason: 'target', net_pnl: 27088.4, pricing: 'replay_history' },
      { timestamp: '2026-07-29T09:45:00', event: 'historical_replay_complete', status: 'CLOSED' },
    ],
  },
};

async function installMocks(page: Page, backtestBody: object, paperStatus?: object) {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (!['http:', 'https:'].includes(url.protocol) || url.origin === BASE_ORIGIN) { await route.fallback(); return; }
    if (url.hostname === 'fonts.googleapis.com') { await route.fulfill({ contentType: 'text/css', body: '' }); return; }
    if (url.hostname === 'fonts.gstatic.com') { await route.fulfill({ status: 204, body: '' }); return; }
    throw new Error(`Offline E2E blocked external request: ${url.href}`);
  });

  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/health' || path.startsWith('/api/auth/')) { await route.continue(); return; }
    if (path === '/api/fib-boundary/backtest') { await route.fulfill({ json: backtestBody }); return; }
    if (path.includes('/broker') || path.includes('/dhan')) {
      await route.fulfill({ json: { status: 'error', message: 'E2E offline broker mock', available_balance: 0, funds: {} } });
      return;
    }
    if (path === '/api/ticker') await route.fulfill({ json: { status: 'ok', nifty: { price: 22000 }, banknifty: { price: 47000 }, midcpnifty: { price: 12000 }, sensex: { price: 73000 } } });
    else if (path === '/api/dashboard/summary') await route.fulfill({ json: { paper_flow: { pnl: 0, trades: 0 }, real_flow: { pnl: 0, trades: 0, source_label: 'E2E mock' }, paper_strategy_flow: {}, live_strategy_flow: {}, scalp_flow: {}, active_count: 0, active_detail: 'No strategies running', strategy_count: 0, backtest_count: 0, best_run: null, worst_run: null, recent_transactions: [], running_engines: [], fii_dii: { status: 'unavailable' } } });
    else if (path === '/api/backfill/status') await route.fulfill({ json: { status: 'idle', running: false } });
    else if (path === '/api/strategies') await route.fulfill({ json: [] });
    else if (path === '/api/strategies/folders') await route.fulfill({ json: [] });
    else if (path === '/api/runs') await route.fulfill({ json: [] });
    else if (path === '/api/published-runs') await route.fulfill({ json: [] });
    else if (path.startsWith('/api/published-runs/')) await route.fulfill({ json: { status: 'error', message: 'E2E offline published run mock' } });
    else if (path === '/api/engines/all') await route.fulfill({ json: { engines: [] } });
    else if (path === '/api/expiry-dates') await route.fulfill({ json: { status: 'ok', nifty: '2026-05-07', banknifty: '2026-05-28', sensex: '2026-05-01' } });
    else if (path.startsWith('/api/expiry-list/')) await route.fulfill({ json: { status: 'ok', expiries: ['2026-05-07', '2026-05-14'] } });
    else if (path === '/api/option-ltp') await route.fulfill({ json: { status: 'ok', ltp: 110.5 } });
    else if (path === '/api/paper/status') await route.fulfill({ json: { running: false, in_trade: false, total_pnl: 0, trades_today: 0, positions: [], closed_trades: [], event_log: [] } });
    else if (path === '/api/live/status') await route.fulfill({ json: { running: false, in_trade: false, total_pnl: 0, trades_today: 0, positions: [], closed_trades: [], event_log: [] } });
    else if (path === '/api/scalp/status') await route.fulfill({ json: { running: false, open_trades: [], closed_trades: [], events: [], session_pnl: 0 } });
    else if (path === '/api/engine-control/status') await route.fulfill({ json: { status: 'ok', any_running: false, users: [] } });
    else if (path === '/api/terminal/cascade/scan') await route.fulfill({ json: { status: 'empty', cached: false, scan_date: '2026-07-29' } });
    else if (path === '/api/cascade/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper', live_gate: { enabled: false } } });
    else if (path === '/api/fib-boundary/paper/status') await route.fulfill({ json: paperStatus ?? { status: 'not_started', mode: 'paper' } });
    else if (path === '/api/candle-entry/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper' } });
    else if (path === '/api/gap-carry/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper', live_available: false, auto: {}, timeframes: ['5m', '15m'] } });
    else if (path === '/api/live/index-chart') await route.fulfill({ json: { status: 'ok', timeframe: '5m', is_open: true, instrument: { underlying: 'NIFTY 50' }, candles: [], entries: [], exits: [], lines: [], overlays: {}, live_price: 0 } });
    else if (path === '/api/live/runs') await route.fulfill({ json: { status: 'ok', runs: [], count: 0, booked_total: 0, day_total: 0 } });
    else if (path === '/api/recovery/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper' } });
    else if (path === '/api/supertrend/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper', live_available: false, auto: {}, timeframes: ['1h', '30m'] } });
    else if (path === '/api/terminal/nifty200') await route.fulfill({ json: { status: 'ok', symbols: [] } });
    else if (path === '/api/terminal/cascade/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper' } });
    else if (path === '/api/terminal/cascade/closed') await route.fulfill({ json: { status: 'ok', campaigns: [] } });
    else if (path === '/api/terminal/forever') await route.fulfill({ json: { status: 'success', data: [] } });
    else if (path === '/api/orders' || path === '/api/positions') await route.fulfill({ json: { status: 'success', data: [] } });
    else if (path === '/api/portfolio/history') await route.fulfill({ json: { status: 'success', monthly: {}, yearly: {} } });
    // Asked on every load so a replay that cost several Dhan round trips
    // survives a redraw of the panel. No saved run is the normal answer.
    else if (path === '/api/fib-boundary/backtests/latest') await route.fulfill({ json: { status: 'ok', run: null } });
    else if (path === '/api/candle-entry/backtests/latest') await route.fulfill({ json: { status: 'ok', run: null } });
    else if (path === '/api/gap-carry/backtests/latest') await route.fulfill({ json: { status: 'empty' } });
    else if (path === '/api/supertrend/backtests/latest') await route.fulfill({ json: { status: 'ok', run: null } });
    else if (path === '/api/recovery/backtests/latest') await route.fulfill({ json: { status: 'empty' } });
    // THE PAPER LEDGER. Every strategy tab asks for its finished campaigns on
    // each refresh; an empty archive is the normal answer offline.
    else if (path.startsWith('/api/paper-campaigns/')) await route.fulfill({ json: { status: 'ok', campaigns: [], count: 0, net_total: null } });
    else throw new Error(`Offline E2E has no mock for ${route.request().method()} ${path}`);
  });
}

async function openCascadePage(page: Page, backtestBody: object, paperStatus?: object) {
  await installMocks(page, backtestBody, paperStatus);
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const passwordInput = page.locator('#password-input');
  if (await passwordInput.isVisible()) {
    await passwordInput.fill(PIN);
    await page.click('#unlock-btn');
  } else {
    for (const digit of PIN.split('')) await page.click(`[data-val="${digit}"]`);
  }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
  await openCascade(page);
}

async function openFibPanel(page: Page, backtestBody: object) {
  await openCascadePage(page, backtestBody);
  // Gap Carry leads the strip now (Phil, 2026-08-25), so Fib Boundary is no
  // longer the panel the page opens on. This used to lean on it being the
  // default and clicked straight into a hidden panel.
  await page.click('#oc-tabbtn-fib');
  await expect(page.locator('#oc-tab-fib')).toBeVisible();
  // The pf-calendar overlay marks the input readonly; the backtest reads its
  // .value, so set it the way the picker would.
  await page.evaluate(() => {
    const input = document.getElementById('oc-fib-mother-timestamp') as HTMLInputElement;
    input.value = '2026-07-17T14:15';
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.click('#oc-fib-backtest-btn');
  await expect(page.locator('#oc-fib-backtest')).toBeVisible();
  if (process.env.E2E_SHOT) await page.locator('#oc-fib-backtest').screenshot({ path: process.env.E2E_SHOT });
}

// A closed round on the swing ladder, shaped exactly like the route returns.
const ladderBacktest = {
  status: 'ok', mode: 'backtest', engine: 'fib_touch_ladder', pricing: 'recorded_history',
  symbol: 'NIFTY', side: 'CE', timeframe: '15m', lot_size: 65,
  mother: { timestamp: '2026-08-06T09:21:00+05:30' },
  candles_replayed: 812, horizon_to: '2026-08-16',
  note: 'NIFTY CE swing touch ladder on a 15m mother, 1m entries.',
  campaign: {
    symbol: 'NIFTY', side: 'CE', timeframe: '15m', status: 'CLOSED',
    anchor: { high: 24700, low: 24600, span: 100, high_timestamp: '2026-08-06T09:16:00+05:30', low_timestamp: '2026-08-06T09:21:00+05:30', confirmed_at: '2026-08-06T09:23:00+05:30', involvement_candles: 2 },
    levels: [
      { level: 2, key: 'L2', index_price: 24500, status: 'FILLED', filled_at: '2026-08-06T09:30:00+05:30' },
      { level: 3, key: 'L3', index_price: 24400, status: 'PENDING', filled_at: null },
    ],
    fills: [
      { buy_number: 1, level: 2, timestamp: '2026-08-06T09:30:00+05:30', index_price: 24500, premium: 200, lots: 1, quantity: 65, strike: 24400, expiry: '2026-08-11', option_type: 'CE', funded_inr: 13000, order_id: 'paper-1' },
    ],
    open_lots: 1, open_quantity: 65, deployed_inr: 13000, remaining_inr: 62000,
    average_index_entry: 24500, target_index: 24550, exit_reason: 'target',
    exit_premiums: [260], gross_pnl: 3900, costs_total: 128.4, net_pnl: 3771.6,
    data_gaps: [], events: [],
  },
  chart: { status: 'ok', symbol: 'NIFTY', timeframe: '15m', side: 'CE', candles: [], anchor: null, levels: [], trendline: null },
};

test('Backtest replays the ladder Start trades and shows its rupee P&L', async ({ page }) => {
  const jsErrors: string[] = [];
  page.on('pageerror', (e) => jsErrors.push(String(e)));
  await openFibPanel(page, ladderBacktest);

  await expect(page.locator('#oc-fib-backtest-badge')).toHaveText('REAL PREMIUMS');
  await expect(page.locator('#oc-fib-backtest-summary')).toContainText('TARGET');
  await expect(page.locator('#oc-fib-backtest-summary')).toContainText('₹3,771.60');
  // The header names the same geometry the Start button runs.
  await expect(page.locator('#oc-fib-backtest-contract')).toContainText('15M mother, 1m entries');
  // One priced leg, entry AND exit premium on it.
  await expect(page.locator('#oc-fib-backtest-legs tr')).toHaveCount(1);
  await expect(page.locator('#oc-fib-backtest-legs')).toContainText('₹200.00');
  await expect(page.locator('#oc-fib-backtest-legs')).toContainText('₹260.00');
  expect(jsErrors).toEqual([]);
});

test('A banked round still lists its priced legs', async ({ page }) => {
  // Phil, 2026-08-16: a replay whose OUTCOME said TARGET and whose NET P&L said
  // +Rs 4,941 showed "No level was touched in this window" under it. A round
  // that pays out empties `fills` and parks the mother, and the table only ever
  // read `fills`.
  const jsErrors: string[] = [];
  page.on('pageerror', (e) => jsErrors.push(String(e)));
  const parked = {
    ...ladderBacktest,
    campaign: {
      ...ladderBacktest.campaign,
      status: 'WAITING_NEW_LOW',
      fills: [],
      open_lots: 0,
      rounds: [{
        round: 1, gross_pnl: 3900, costs_total: 128.4, net_pnl: 3771.6,
        exit_timestamp: '2026-07-20T10:45:00+05:30', exit_index: 23960, exit_reason: 'target',
        fills: (ladderBacktest.campaign.fills || []).map((f: Record<string, unknown>) => ({ ...f, exit_premium: 260 })),
      }],
    },
  };
  await openFibPanel(page, parked);

  await expect(page.locator('#oc-fib-backtest-legs')).not.toContainText('No level was touched');
  await expect(page.locator('#oc-fib-backtest-legs tr')).toHaveCount(1);
  await expect(page.locator('#oc-fib-backtest-legs')).toContainText('₹200.00');   // paid
  await expect(page.locator('#oc-fib-backtest-legs')).toContainText('₹260.00');   // sold
  await expect(page.locator('#oc-fib-backtest-legs')).toContainText('R1');        // which round
  // And the tile stops saying "0 buys" over a round that traded.
  await expect(page.locator('#oc-fib-backtest-summary')).toContainText('1 buys');
  expect(jsErrors).toEqual([]);
});

test('A replay with no recorded prices says so instead of showing zeros', async ({ page }) => {
  const unpriced = {
    ...ladderBacktest,
    pricing: 'unpriced',
    note: 'No recorded option history was reachable, so this replay is geometry only — no prices, no P&L.',
    campaign: { ...ladderBacktest.campaign, fills: [], gross_pnl: null, costs_total: null, net_pnl: null, exit_reason: null, status: 'ARMED' },
  };
  await openFibPanel(page, unpriced);

  await expect(page.locator('#oc-fib-backtest-badge')).toHaveText('GEOMETRY ONLY');
  // A dash, never a zero: an absent P&L must not read as a break-even trade.
  // Asserting the dash alone is too weak -- Gross and Costs show one too, so
  // it passes even when Net renders ₹0.00. Assert the LIE is absent.
  await expect(page.locator('#oc-fib-backtest-summary')).not.toContainText('₹0.00');
  await expect(page.locator('#oc-fib-backtest-summary')).toContainText('—');
  await expect(page.locator('#oc-fib-backtest-gist')).toContainText('geometry only');
  await expect(page.locator('#oc-fib-backtest-legs')).toContainText('No level was touched');
});

test('Pricing gaps are disclosed on the badge and listed, not buried', async ({ page }) => {
  const gappy = {
    ...ladderBacktest,
    campaign: { ...ladderBacktest.campaign, data_gaps: ['L3: no NIFTY 24300CE 2026-08-11 quote (x4, last 11:20)'] },
  };
  await openFibPanel(page, gappy);

  await expect(page.locator('#oc-fib-backtest-badge')).toHaveText('PRICED · 1 GAP');
  await expect(page.locator('#oc-fib-backtest-gaps')).toContainText('no NIFTY 24300CE');
});
