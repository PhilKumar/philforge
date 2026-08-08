/**
 * 01-smoke.spec.ts
 * Smoke tests for PhilForge:
 *   1. Login via password-first auth shell
 *   2. Health endpoint returns OK
 *   3. Auth status reflects authenticated session
 */

import { test, expect, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';
const OFFLINE_E2E = process.env.E2E_OFFLINE !== '0';
const BASE_ORIGIN = new URL(process.env.E2E_BASE_URL || process.env.BASE_URL || 'http://localhost:8000').origin;

const tickerMock = {
  status: 'ok',
  nifty: { price: 22000 },
  banknifty: { price: 47000 },
  midcpnifty: { price: 12000 },
  sensex: { price: 73000 },
};

const paperStatusMock = {
  running: false,
  in_trade: false,
  total_pnl: 0,
  trades_today: 0,
  positions: [],
  closed_trades: [],
  event_log: [],
};

const liveStatusMock = {
  running: false,
  in_trade: false,
  total_pnl: 0,
  trades_today: 0,
  positions: [],
  closed_trades: [],
  event_log: [],
};

const scalpStatusMock = {
  running: false,
  open_trades: [],
  closed_trades: [],
  events: [],
  session_pnl: 0,
};

const dashboardSummaryMock = {
  paper_flow: { pnl: 0, trades: 0 },
  real_flow: { pnl: 0, trades: 0, source_label: 'E2E mock' },
  paper_strategy_flow: {},
  live_strategy_flow: {},
  scalp_flow: {},
  active_count: 0,
  active_detail: 'No strategies running',
  strategy_count: 0,
  backtest_count: 0,
  best_run: null,
  worst_run: null,
  recent_transactions: [],
  running_engines: [],
  fii_dii: { status: 'unavailable' },
};

async function installOfflineE2E(page: Page) {
  if (!OFFLINE_E2E) return;

  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (!['http:', 'https:'].includes(url.protocol) || url.origin === BASE_ORIGIN) {
      await route.fallback();
      return;
    }
    if (url.hostname === 'fonts.googleapis.com') {
      await route.fulfill({ contentType: 'text/css', body: '' });
      return;
    }
    if (url.hostname === 'fonts.gstatic.com') {
      await route.fulfill({ status: 204, body: '' });
      return;
    }
    throw new Error(`Offline E2E blocked external request: ${url.href}`);
  });

  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === '/api/health' || path.startsWith('/api/auth/')) {
      await route.continue();
      return;
    }
    if (path.includes('/broker') || path.includes('/dhan')) {
      await route.fulfill({ json: { status: 'error', message: 'E2E offline broker mock', available_balance: 0, funds: {} } });
      return;
    }
    if (path === '/api/ticker') await route.fulfill({ json: tickerMock });
    else if (path === '/api/dashboard/summary') await route.fulfill({ json: dashboardSummaryMock });
    else if (path === '/api/backfill/status') await route.fulfill({ json: { status: 'idle', running: false } });
    else if (path === '/api/strategies') await route.fulfill({ json: [] });
    else if (path === '/api/strategies/folders') await route.fulfill({ json: [] });
    else if (path === '/api/runs') await route.fulfill({ json: [] });
    else if (path.startsWith('/api/runs/')) await route.fulfill({ json: { status: 'error', message: 'E2E offline run mock' } });
    else if (path === '/api/engines/all') await route.fulfill({ json: { engines: [] } });
    else if (path === '/api/expiry-dates') await route.fulfill({ json: { status: 'ok', nifty: '2026-05-07', banknifty: '2026-05-28', sensex: '2026-05-01' } });
    else if (path.startsWith('/api/expiry-list/')) await route.fulfill({ json: { status: 'ok', expiries: ['2026-05-07', '2026-05-14'] } });
    else if (path === '/api/option-ltp') await route.fulfill({ json: { status: 'ok', ltp: 110.5 } });
    else if (path === '/api/paper/status') await route.fulfill({ json: paperStatusMock });
    else if (path === '/api/live/status') await route.fulfill({ json: liveStatusMock });
    else if (path === '/api/scalp/status') await route.fulfill({ json: scalpStatusMock });
    else if (path === '/api/engine-control/status') await route.fulfill({ json: { status: 'ok', any_running: false, users: [] } });
    else if (path === '/api/terminal/nifty200') await route.fulfill({ json: { status: 'ok', symbols: [] } });
    else if (path === '/api/terminal/cascade/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper' } });
    else if (path === '/api/terminal/cascade/closed') await route.fulfill({ json: { status: 'ok', campaigns: [] } });
    else if (path === '/api/terminal/forever') await route.fulfill({ json: { status: 'success', data: [] } });
    else if (path === '/api/charts/tree') await route.fulfill({ json: { years: {} } });
    else if (path === '/api/financial-plan') await route.fulfill({ json: { status: 'ok', plan: {} } });
    else if (path === '/api/journal/list') await route.fulfill({ json: { status: 'ok', entries: [] } });
    else if (path === '/api/terminal/cascade/scan') {
      await route.fulfill({ json: { status: 'empty', cached: false, scan_date: '2026-07-29' } });
    }
    else if (path === '/api/cascade/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper', live_gate: { enabled: false } } });
    // A LIST now — one ladder per instrument, so the console reads `campaigns`.
    else if (path === '/api/fib-boundary/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper', campaigns: [] } });
    else if (path === '/api/candle-entry/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper' } });
    else if (path === '/api/fib-space/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper' } });
    else if (path === '/api/recovery/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper' } });
    else if (path === '/api/test-bench/results') await route.fulfill({ json: { status: 'ok', total: 0, page: 1, per_page: 10, pages: 1, rows: [] } });
    else if (path === '/api/orders' || path === '/api/positions') await route.fulfill({ json: { status: 'success', data: [] } });
    else if (path === '/api/portfolio/history') await route.fulfill({ json: { status: 'success', monthly: {}, yearly: {} } });
    // Insights panels. Both used to be their own pages; inside the tab they
    // call the same two endpoints, and the strict table has to know them or
    // opening the tab throws.
    else if (path.startsWith('/api/market-movers/')) await route.fulfill({ json: {
      status: 'success', as_of: '2026-08-06T13:20:00+05:30', index: 'NIFTY 50',
      gainers: [{ symbol: 'INFY', last_price: 1580.4, change_pct: 2.41, change: 37.2 }],
      losers: [{ symbol: 'TCS', last_price: 3890.1, change_pct: -1.82, change: -72.1 }],
    } });
    else if (path.startsWith('/api/study-library')) await route.fulfill({ json: {
      status: 'success', items: [{ id: 'a1', title: 'Risk of ruin', category: 'Psychology',
        kind: 'PDF', description: 'A short read on position sizing.', url: '#',
        updated_at: '2026-08-01', size: '1.2 MB' }],
    } });
    else throw new Error(`Offline E2E has no mock for ${request.method()} ${path}`);
  });
}

// ── Auth helper ─────────────────────────────────────────────
// Current login defaults to username + password, but we keep a fallback
// for explicit PIN mode in case a branch toggles that UI back on.
async function login(page: Page) {
  await installOfflineE2E(page);
  await page.goto('/app');

  await page.fill('#username-input', USERNAME);

  const passwordInput = page.locator('#password-input');
  if (await passwordInput.isVisible()) {
    await passwordInput.fill(PIN);
    await page.click('#unlock-btn');
  } else {
    for (const digit of PIN.split('')) {
      await page.click(`[data-val="${digit}"]`);
    }
  }

  // Wait for the authenticated shell (nav bar rendered by strategy.html)
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
}

async function seriousAccessibilityViolations(page: Page, include?: string) {
  let builder = new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']);
  if (include) builder = builder.include(include);
  const results = await builder.analyze();
  return results.violations
    .filter((violation) => ['serious', 'critical'].includes(violation.impact || ''))
    .map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      targets: violation.nodes.map((node) => node.target),
    }));
}

test('Login is semantic, keyboard-visible, and has no serious WCAG A/AA violations', async ({ page }) => {
  await installOfflineE2E(page);
  await page.goto('/app');
  await expect(page.getByRole('main')).toBeVisible();
  await expect(page.getByRole('heading', { level: 1, name: 'PhilForge' })).toBeVisible();
  await expect(page.getByLabel('Username')).toBeVisible();
  await expect(page.getByLabel('Password', { exact: true })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  const smallControls = await page.locator('button:visible, a:visible, input:visible').evaluateAll((controls) =>
    controls
      .map((control) => {
        const rect = control.getBoundingClientRect();
        return { id: control.id, width: rect.width, height: rect.height };
      })
      .filter((control) => control.width < 44 || control.height < 44)
  );
  expect(smallControls).toEqual([]);
  expect(await seriousAccessibilityViolations(page)).toEqual([]);
});

test('Authenticated primary surfaces have landmarks and no serious automated WCAG violations', async ({ page }) => {
  await login(page);
  await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible();
  await expect(page.getByRole('main')).toBeVisible();

  const surfaces = [
    ['#nav-dashboard', '#dashboard-page'],
    ['#nav-portfolio', '#portfolio-page'],
    ['#nav-insights', '#insights-page'],
    ['#nav-live', '#live-page'],
    ['#nav-terminal', '#stock-terminal-page'],
    ['#nav-scalp', '#scalp-page'],
    ['#nav-cascade', '#options-cascade-page'],
    ['#nav-builder', '#builder-page'],
    ['#nav-charts', '#charts-page'],
    ['#nav-results', '#results-page'],
  ];
  for (const [control, pageSection] of surfaces) {
    await page.click(control);
    await expect(page.locator(control)).toHaveAttribute('aria-current', 'page');
    expect(await seriousAccessibilityViolations(page, pageSection), pageSection).toEqual([]);
  }
});

test('Insights, Cascade, and Journal subpanels have no serious automated WCAG violations', async ({ page }) => {
  await login(page);

  await page.click('#nav-insights');
  for (const [control, panel] of [
    ['#insights-tabbtn-heatmap', '#insights-heatmap'],
    ['#insights-tabbtn-study', '#insights-study'],
  ]) {
    await page.click(control);
    await expect(page.locator(control)).toHaveAttribute('aria-selected', 'true');
    expect(await seriousAccessibilityViolations(page, panel), panel).toEqual([]);
  }
  await page.locator('#insights-tabbtn-study').focus();
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#insights-tabbtn-heatmap')).toHaveAttribute('aria-selected', 'true');

  await page.click('#nav-cascade');
  for (const [control, panel] of [
    ['#oc-tabbtn-fib', '#oc-tab-fib'],
    ['#oc-tabbtn-candle', '#oc-tab-candle'],
    ['#oc-tabbtn-space', '#oc-tab-space'],
    ['#oc-tabbtn-recovery', '#oc-tab-recovery'],
    ['#oc-tabbtn-bench', '#oc-tab-bench'],
  ]) {
    await page.click(control);
    await expect(page.locator(control)).toHaveAttribute('aria-selected', 'true');
    expect(await seriousAccessibilityViolations(page, panel), panel).toEqual([]);
  }
  await page.locator('#oc-tabbtn-bench').focus();
  await page.keyboard.press('Home');
  await expect(page.locator('#oc-tabbtn-fib')).toHaveAttribute('aria-selected', 'true');

  await page.click('#nav-charts');
  for (const [control, panel] of [
    ['#cj-tab-journal', '#cj-journal-view'],
    ['#cj-tab-plan', '#cj-plan-view'],
  ]) {
    await page.click(control);
    await expect(page.locator(control)).toHaveAttribute('aria-selected', 'true');
    expect(await seriousAccessibilityViolations(page, panel), panel).toEqual([]);
  }
  await page.locator('#cj-tab-plan').focus();
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#cj-tab-journal')).toHaveAttribute('aria-selected', 'true');
});

// ── Health check ─────────────────────────────────────────────
test('Health endpoint returns OK', async ({ request }) => {
  const resp = await request.get('/api/health');
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body).toMatchObject({ status: 'ok' });
});

// ── Login ────────────────────────────────────────────────────
test('PIN-pad login succeeds and loads main app', async ({ page }) => {
  await login(page);
  // Nav tabs should be visible after successful authentication
  await expect(page.locator('.nav-tab').first()).toBeVisible();
});

// ── Auth status ──────────────────────────────────────────────
test('Auth status returns authenticated after login', async ({ page }) => {
  await login(page);
  const resp = await page.request.get('/api/auth/status');
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.authenticated).toBe(true);
});

test('Every primary navigation surface has a working owner and active page', async ({ page }) => {
  await login(page);
  const surfaces = [
    ['#nav-dashboard', '#dashboard-page'],
    ['#nav-portfolio', '#portfolio-page'],
    ['#nav-live', '#live-page'],
    ['#nav-terminal', '#stock-terminal-page'],
    ['#nav-scalp', '#scalp-page'],
    ['#nav-cascade', '#options-cascade-page'],
    ['#nav-builder', '#builder-page'],
    ['#nav-charts', '#charts-page'],
    ['#nav-results', '#results-page'],
  ];
  for (const [control, pageSection] of surfaces) {
    await page.click(control);
    await expect(page.locator(pageSection)).toHaveClass(/active-page/);
  }

  // Insights is a page with tabs now, not a dropdown of links.
  await page.click('#nav-insights');
  await expect(page.locator('#insights-page')).toHaveClass(/active-page/);
  await expect(page.locator('#nav-insights-wrap')).toHaveCount(0);
  // The standalone pages are deliberately left serving, so old bookmarks live.
  for (const target of ['/market-movers', '/study-lounge']) {
    const response = await page.request.get(target);
    expect(response.status()).toBe(200);
  }

  // The retired chart-type choices had no calculation or backend owner.
  await expect(page.locator('#cpr-modal .chart-type-btn')).toHaveCount(0);
});

test('Appearance presets switch and persist after reload', async ({ page }) => {
  await login(page);

  await page.click('#appearance-btn');
  await expect(page.locator('#appearance-modal')).toHaveClass(/open/);

  await page.click('[data-appearance-tint="native"]');
  await expect(page.locator('html')).not.toHaveAttribute('data-pf-tint');

  // The roster comes from the page's own registry, never typed here: a
  // hand-listed id survives a rename in the product and fails for the wrong
  // reason — which is exactly how this spec broke when the five pastels
  // became five contrasting rooms.
  const tintIds: string[] = await page.evaluate(() =>
    ((window as any).PHILFORGE_APPEARANCE_PRESETS?.tints || [])
      .map((t: any) => t.id)
      .filter((id: string) => id !== 'native')
  );
  expect(tintIds).toHaveLength(5);
  const tintPalettes: Record<string, string> = {};
  for (const tint of tintIds) {
    await page.click(`[data-appearance-tint="${tint}"]`);
    await expect(page.locator('html')).toHaveAttribute('data-pf-tint', tint);
    tintPalettes[tint] = await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement);
      return [
        root.getPropertyValue('--bg').trim(),
        root.getPropertyValue('--card').trim(),
        root.getPropertyValue('--accent').trim(),
        getComputedStyle(document.body).backgroundImage,
      ].join('|');
    });
  }
  expect(new Set(Object.values(tintPalettes)).size).toBe(5);

  const fontStacks: Record<string, string> = {};
  for (const font of ['forge', 'atelier', 'exchange', 'blueprint', 'scribe']) {
    await page.click(`[data-appearance-font="${font}"]`);
    await expect(page.locator('html')).toHaveAttribute('data-pf-font', font);
    fontStacks[font] = await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement);
      return [
        root.getPropertyValue('--font-body').trim(),
        root.getPropertyValue('--font-display').trim(),
        root.getPropertyValue('--font-mono').trim(),
      ].join('|');
    });
  }
  expect(new Set(Object.values(fontStacks)).size).toBe(5);

  await page.reload();
  await expect(page.locator('.nav-tab').first()).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-pf-tint', tintIds[tintIds.length - 1]);
  await expect(page.locator('html')).toHaveAttribute('data-pf-font', 'scribe');
});

test('Cascade generated statuses remain legible in light mode', async ({ page }) => {
  await login(page);

  const statuses = await page.evaluate(() => {
    document.documentElement.setAttribute('data-theme', 'light');

    const app = window as typeof window & {
      _setCandleEntryFormStatus?: (message: string, tone: string) => void;
      _renderCandleEntryStatus?: (payload: unknown) => void;
      _fibSetFormStatus?: (message: string, tone: string) => void;
    };
    if (!app._setCandleEntryFormStatus || !app._renderCandleEntryStatus || !app._fibSetFormStatus) {
      throw new Error('Cascade status renderers are unavailable');
    }

    app._setCandleEntryFormStatus('Historical 1H replay completed. Fixed-strike P&L is withheld.', 'success');
    app._renderCandleEntryStatus({
      campaign: {
        running: false,
        replay_complete: true,
        status: 'completed',
        contract: { underlying: 'NIFTY', strike: 24000, option_type: 'CE', expiry: '2026-08-06', lot_size: 65 },
        entry_stop: 23900,
        target_index: 24100,
        qualifying_reds: [],
        pricing_warning: 'Historical replay verifies index geometry only. Fixed-strike option P&L is withheld.',
      },
    });
    app._fibSetFormStatus('Replaying the index geometry...', 'busy');

    const color = (selector: string) => {
      const element = document.querySelector(selector);
      if (!element) throw new Error(`Missing ${selector}`);
      return getComputedStyle(element).color;
    };
    return {
      candleReplay: color('#candle-entry-form-status'),
      candleBadge: color('#candle-entry-badge'),
      candleWarning: color('#candle-entry-summary .is-warning'),
      fibBoundary: color('#fibx-form-status'),
    };
  });

  expect(statuses).toEqual({
    candleReplay: 'rgb(4, 120, 87)',
    candleBadge: 'rgb(146, 64, 14)',
    candleWarning: 'rgb(146, 64, 14)',
    fibBoundary: 'rgb(146, 64, 14)',
  });
});

test('Appearance, mobile nav, and scalp launchpad match screenshots', async ({ page }) => {
  await login(page);

  await page.click('#appearance-btn');
  await expect(page.locator('#appearance-modal')).toHaveClass(/open/);
  await expect(page.locator('#appearance-modal .appearance-modal')).toHaveScreenshot('appearance-modal.png', {
    animations: 'disabled',
    maxDiffPixelRatio: 0.04,
  });
  await page.click('[data-pf-action="closeAppearanceModal"]');

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.nav-bar')).toBeVisible();
  await expect(page.locator('.nav-bar')).toHaveScreenshot('mobile-nav.png', {
    animations: 'disabled',
    maxDiffPixelRatio: 0.04,
  });

  await page.click('#nav-scalp');
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);
  await expect(page.locator('#scalp-form-title')).toBeVisible();
  await expect(page.locator('#scalp-page')).toHaveScreenshot('scalp-launchpad.png', {
    animations: 'disabled',
    maxDiffPixelRatio: 0.04,
  });
});

// ── Test Bench ───────────────────────────────────────────────
// A blank chart is the failure this catches. The renderer is hand-written
// Canvas: a typo in a draw layer throws inside a paint loop, the surface stays
// empty, and every Python test still passes. So this asserts the semantic paint
// record — real candles, real geometry, real labels — not just that a canvas
// element exists.
const testBenchRunMock = {
  status: 'ok',
  strategy: 'fib',
  summary: {
    instrument: 'NIFTY',
    timeframe: '15m',
    outcome: 'Target hit',
    exit_reason: 'target',
    still_open: false,
    target_index: 24625,
    average_spot: 24425,
    mother_timestamp: '2026-07-21T09:15:00',
    entry_timestamp: '2026-07-21T12:15:00',
    exit_timestamp: '2026-07-21T15:15:00',
    entry_count: 2,
    unpriced_entries: 0,
    spend_inr: 31200,
    net_pnl: 18400,
    costs_total: 620,
    strike: 24450,
    option_type: 'CE',
    expiry: '2026-08-04',
    lot_size: 65,
    underlying: 'NIFTY',
  },
  entries: [
    { timestamp: '2026-07-21T12:15:00', spot: 24450, option_price: 180, lots: 1, quantity: 65, level: 4, leg_id: 1, spend_inr: 11700, strike: 24450, option_type: 'CE' },
    { timestamp: '2026-07-21T13:15:00', spot: 24400, option_price: 150, lots: 2, quantity: 130, level: 8, leg_id: 1, spend_inr: 19500, strike: 24400, option_type: 'CE' },
  ],
  chart: {
    timeframe: '15m',
    candles: [
      { t: 1784017500, o: 24600, h: 24650, l: 24560, c: 24580, is_mother: true },
      { t: 1784018400, o: 24580, h: 24590, l: 24440, c: 24450, is_mother: false },
      { t: 1784019300, o: 24450, h: 24460, l: 24390, c: 24400, is_mother: false },
      { t: 1784020200, o: 24400, h: 24640, l: 24395, c: 24630, is_mother: false },
    ],
    mother: { high: 24650, low: 24560 },
    trendlines: [{ id: 1, a1: { t: 1784017500, p: 24650 }, a2: { t: 1784018400, p: 24590 }, active: true }],
    legs: [{
      leg_id: 1,
      touch_timestamp: 1784018400,
      touch_high: 24590,
      low: 24440,
      levels: { '0': 24590, '1': 24440, '2': 24500, '4': 24450, '8': 24400 },
      orders: [{ level: 4, inr_notional: 11700 }, { level: 8, inr_notional: 19500 }],
    }],
    entries: [{ t: 1784018400, price: 24450 }, { t: 1784019300, price: 24400 }],
    exits: [{ t: 1784020200, price: 24630, pnl: 18400 }],
    avg_entry_price: 24425,
    tp_price: 24625,
    tp_label: 'TARGET HIT',
  },
};

test('Test Bench draws one mother candle and every level it bought', async ({ page }) => {
  await login(page);
  await page.route('**/api/test-bench/run', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(testBenchRunMock) }));

  await page.click('#nav-cascade');
  await page.click('#oc-tabbtn-bench');
  // The app upgrades every datetime-local input into its own read-only calendar
  // widget, so the value is set the way that widget sets it.
  await page.evaluate(() => {
    const input = document.getElementById('tb-mother') as HTMLInputElement;
    input.value = '2026-07-21T09:15';
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.click('#tb-run');

  // The result arrives as ONE strip; the chart sits behind its button, the
  // way every other panel does it.
  await expect(page.locator('#tb-outcome-badge')).toHaveText('TARGET HIT');
  await expect(page.locator('#tb-verdict')).toContainText('Target hit');
  await expect(page.locator('#tb-verdict')).toContainText('₹31,200');
  await expect(page.locator('#tb-entries tbody tr')).toHaveCount(2);

  await page.click('#tb-chart-btn');
  await page.waitForSelector('#pf-bench-canvas-main', { timeout: 10_000 });

  const paint = await page.evaluate(() => {
    const app = window as typeof window & { _pfChartCanvas?: { paint?: Record<string, unknown> } };
    if (!app._pfChartCanvas || !app._pfChartCanvas.paint) throw new Error('The Test Bench canvas never painted');
    return app._pfChartCanvas.paint;
  });

  expect(paint).toMatchObject({ candles: 4, trendlines: 1, markers: 3 });
  const labels = paint.labelTexts as string[];
  // The two lines that decide whether the trade worked, and what it cost.
  expect(labels.some((text) => text.startsWith('TARGET HIT'))).toBe(true);
  expect(labels.some((text) => text.includes('₹11,700'))).toBe(true);
  expect(labels.some((text) => text.includes('₹19,500'))).toBe(true);

  // The chart button folds it away again.
  await page.click('#tb-chart-btn');
  await expect(page.locator('#pf-bench-canvas-main')).toHaveCount(0);
});

test('Test Bench calendar offers only the minutes its timeframe can open on', async ({ page }) => {
  await login(page);
  await page.click('#nav-cascade');
  await page.click('#oc-tabbtn-bench');

  // A 5-minute picker cannot express a 1m mother at all, and an every-minute
  // list on 1H is 59 choices that all fail with "no candle at that time".
  const minutesFor = async (timeframe: string) => {
    await page.selectOption('#tb-timeframe', timeframe);
    await page.click('#tb-mother');
    await page.waitForSelector('.pf-cascade-calendar:not([hidden])');
    const values = await page.$$eval('[data-pf-calendar-minute] option', (opts) =>
      opts.map((o) => (o as HTMLOptionElement).value));
    await page.click('[data-pf-calendar-cancel]');
    return values;
  };

  expect(await minutesFor('1m')).toHaveLength(60);
  expect(await minutesFor('15m')).toEqual(['0', '15', '30', '45']);
  // NSE opens at 09:15, so every 1H bar opens at :15 and no other minute.
  expect(await minutesFor('1h')).toEqual(['15']);

  // And a 1m timestamp survives the round trip through the picker.
  await page.selectOption('#tb-timeframe', '1m');
  await page.click('#tb-mother');
  await page.waitForSelector('.pf-cascade-calendar:not([hidden])');
  await page.selectOption('[data-pf-calendar-hour]', '10');
  await page.selectOption('[data-pf-calendar-minute]', '37');
  await page.click('[data-pf-calendar-apply]');
  expect(await page.inputValue('#tb-mother')).toMatch(/T10:37$/);
});

test('Test Bench switches cleanly between the two strategies', async ({ page }) => {
  await login(page);
  await page.click('#nav-cascade');
  await page.click('#oc-tabbtn-bench');

  // Fib names the levels it buys; Two Red names the charts it climbs through.
  await page.selectOption('#tb-strategy', 'fib');
  await expect(page.locator('#tb-timeframe option[value="1m"]')).toHaveText(/L4/);
  await expect(page.locator('#tb-rung-field')).toBeVisible();

  await page.selectOption('#tb-strategy', 'two_red');
  await expect(page.locator('#tb-timeframe option[value="1m"]')).toHaveText(/1m → 5m → 15m → 1H/);
  // A 1H start has nothing above it, so it is a single trade and says so.
  await expect(page.locator('#tb-timeframe option[value="1h"]')).toHaveText(/^1H · 1H$/);
  // The rupee-per-level box is a fib control; the ladder sizes itself in lots.
  await expect(page.locator('#tb-rung-field')).toBeHidden();
  await expect(page.locator('#tb-explainer')).toContainText('two red candles');
});

test('Desktop nav is one row that scrolls, never two', async ({ page }) => {
  // The nav positions tabs with per-id CSS order rules and used to wrap to a
  // second row when they stopped fitting — which is how the Test Bench tab
  // once landed on top of the brand panel. One row is now the invariant at
  // every width; overflow scrolls sideways instead.
  await page.setViewportSize({ width: 1600, height: 900 });
  await login(page);

  const rowsAt = async (width: number) => {
    await page.setViewportSize({ width, height: 900 });
    await page.waitForTimeout(150);
    return page.evaluate(() => {
      const tabs = Array.from(document.querySelectorAll('.nav-tabs > *')) as HTMLElement[];
      const visible = tabs.filter((el) => el.offsetParent !== null);
      return new Set(visible.map((el) => Math.round(el.getBoundingClientRect().top))).size;
    });
  };

  expect(await rowsAt(1600)).toBe(1);
  expect(await rowsAt(1280)).toBe(1);
  expect(await rowsAt(1024)).toBe(1);

  // And the row is genuinely scrollable rather than clipping tabs away.
  await page.setViewportSize({ width: 900, height: 900 });
  await page.waitForTimeout(150);
  const scrollable = await page.evaluate(() => {
    const bar = document.querySelector('.nav-bar') as HTMLElement;
    return bar.scrollWidth > bar.clientWidth + 1;
  });
  expect(scrollable).toBe(true);
});

test('Candle Entry tab offers the full ladder of starting charts', async ({ page }) => {
  await login(page);
  await page.click('#nav-cascade');
  await page.click('#oc-tabbtn-candle');

  // The four ladders, each named by the charts it climbs through.
  await expect(page.locator('#candle-entry-timeframe option')).toHaveCount(4);
  await expect(page.locator('#candle-entry-timeframe option[value="1m"]')).toHaveText(/1m → 5m → 15m → 1H/);
  await expect(page.locator('#candle-entry-timeframe option[value="1h"]')).toHaveText(/^1H · 1H$/);

  // Switching the chart retunes the mother calendar's minutes.
  await page.selectOption('#candle-entry-timeframe', '1h');
  await expect(page.locator('#candle-entry-mother-timestamp')).toHaveAttribute('data-pf-calendar-minutes', '15');
  await page.selectOption('#candle-entry-timeframe', '15m');
  await expect(page.locator('#candle-entry-mother-timestamp')).toHaveAttribute('data-pf-calendar-minutes', '0,15,30,45');

  // The copy sells the ladder, not the old single 1H buy.
  await expect(page.locator('#candle-entry-page-kicker, #options-cascade-page')).toContainText('TWO-RED LADDER');
});

test('Fib Boundary tab renders the swing-ladder controls', async ({ page }) => {
  const jsErrors: string[] = [];
  page.on('pageerror', (err) => jsErrors.push(String(err)));

  await login(page);
  await page.click('#nav-cascade');
  await page.click('#oc-tabbtn-fib');

  await expect(page.locator('#oc-tab-fib')).toBeVisible();

  // All five instruments Phil asked for, NIFTY first.
  await expect(page.locator('#fibx-symbol option')).toHaveCount(5);
  await expect(page.locator('#fibx-symbol')).toHaveValue('NIFTY');
  await expect(page.locator('#fibx-side option')).toHaveCount(2);
  await expect(page.locator('#fibx-side')).toContainText('Buy CE');
  await expect(page.locator('#fibx-side')).toContainText('Buy PE');

  // The old per-rung budget is gone; the ladder cap replaces it.
  await expect(page.locator('#fibx-capital-cap')).toHaveValue('75000');
  await expect(page.locator('#fibx-rung-inr')).toHaveCount(0);
  await expect(page.locator('#fibx-levels-hint')).toContainText('L16');

  // Every chart a mother may be read on. Entries stay 1m whichever is picked.
  // A button row, not a dropdown -- all four charts visible at once.
  await expect(page.locator('#fibx-timeframe .fibx-tf')).toHaveCount(4);
  await expect(page.locator('#fibx-timeframe')).toHaveAttribute('data-value', '1m');
  await page.click('#fibx-timeframe .fibx-tf[data-tf="15m"]');
  await expect(page.locator('#fibx-timeframe')).toHaveAttribute('data-value', '15m');
  await expect(page.locator('#fibx-timeframe .fibx-tf[data-tf="15m"]')).toHaveClass(/is-active/);
  await page.click('#fibx-timeframe .fibx-tf[data-tf="1m"]');

  // Paper is what you get by default. Live stays visibly safety-locked until
  // broker fill, exit, and restart reconciliation have acceptance coverage.
  await expect(page.locator('#fibx-mode')).toHaveValue('paper');
  await expect(page.locator('#fibx-mode-note')).toContainText('sends nothing');
  await expect(page.locator('#fibx-mode option[value="live"]')).toBeDisabled();
  await expect(page.locator('#fibx-mode')).toHaveAttribute('title', /Live remains unavailable/);

  // A symbol whose weeklies NSE withdrew must say so, or the user believes
  // they are getting a weekly contract that does not exist.
  await page.selectOption('#fibx-symbol', 'BANKNIFTY');
  await expect(page.locator('#fibx-symbol-note')).toContainText('Monthly expiries only');
  await page.selectOption('#fibx-symbol', 'SENSEX');
  await expect(page.locator('#fibx-symbol-note')).toContainText('Thin book');
  await page.selectOption('#fibx-symbol', 'NIFTY');

  // The monitor renders from a not_started payload rather than staying blank.
  await expect(page.locator('#fibx-monitors [data-fx="badge"]')).toHaveText('IDLE');
  await expect(page.locator('#fibx-start')).toBeVisible();
  await expect(page.locator('#fibx-monitors [data-fx="kill"]')).toBeHidden();

  // Nothing is parked any more: the Backtest replays the SAME ladder Start
  // trades, so both are live and the parked note is gone.
  await expect(page.locator('#fibx-monitors [data-fx="chart"]')).toBeVisible();
  await expect(page.locator('#fibx-backtest-btn')).toBeVisible();
  await expect(page.locator('#fibx-parked-note')).toHaveCount(0);

  expect(jsErrors).toEqual([]);
});

// A running ladder, shaped exactly like FibTouchLadder.get_status().
const fibTouchCampaign = {
  symbol: 'NIFTY', side: 'CE', timeframe: '15m', entry_timeframe: '1m',
  mode: 'paper', is_live: false, armed: false, status: 'OPEN', running: true,
  mother_timestamp: '2026-08-06T09:21:00+05:30',
  anchor: {
    high: 24700, low: 24600, span: 100,
    high_timestamp: '2026-08-06T09:16:00+05:30',
    low_timestamp: '2026-08-06T09:21:00+05:30',
    confirmed_at: '2026-08-06T09:23:00+05:30',
    involvement_candles: 2,
  },
  levels: [
    { level: 2, key: 'L2', index_price: 24500, status: 'FILLED', filled_at: '2026-08-06T09:30:00+05:30' },
    { level: 3, key: 'L3', index_price: 24400, status: 'FILLED', filled_at: '2026-08-06T09:35:00+05:30' },
    { level: 4, key: 'L4', index_price: 24300, status: 'PENDING', filled_at: null },
    { level: 6, key: 'L6', index_price: 24100, status: 'UNFUNDED', filled_at: null },
  ],
  fills: [
    { buy_number: 1, level: 2, rung_key: 'L2', timestamp: '2026-08-06T09:30:00+05:30', index_price: 24500, premium: 200, lots: 1, quantity: 65, strike: 24400, expiry: '2026-08-11', option_type: 'CE', funded_inr: 13000 },
    { buy_number: 2, level: 3, rung_key: 'L3', timestamp: '2026-08-06T09:35:00+05:30', index_price: 24400, premium: 180, lots: 1, quantity: 65, strike: 24300, expiry: '2026-08-11', option_type: 'CE', funded_inr: 11700 },
  ],
  lot_size: 65, strike_step: 50, itm_steps: 2, min_dte: 4,
  capital_cap_inr: 75000, deployed_inr: 24700, remaining_inr: 50300,
  open_lots: 2, open_quantity: 130,
  average_index_entry: 24450, average_premium: 190, target_index: 24512.5, target_fraction: 0.25,
  exit_timestamp: null, exit_reason: null, exit_index: null, exit_premiums: [],
  gross_pnl: null, costs_total: null, net_pnl: null,
  events: [], data_gaps: [],
};

const fibTouchChart = {
  status: 'ok', symbol: 'NIFTY', timeframe: '15m', side: 'CE', chart_mode: 'visual_gap_adjusted',
  candles: [
    { t: '2026-08-06T09:16:00+05:30', o: 24675, h: 24700, l: 24670, c: 24695, is_mother: false },
    { t: '2026-08-06T09:19:00+05:30', o: 24662, h: 24665, l: 24640, c: 24642, is_mother: false },
    { t: '2026-08-06T09:21:00+05:30', o: 24622, h: 24624, l: 24600, c: 24602, is_mother: true },
    { t: '2026-08-06T09:23:00+05:30', o: 24610, h: 24620, l: 24608, c: 24618, is_mother: false },
    { t: '2026-08-06T09:30:00+05:30', o: 24560, h: 24565, l: 24495, c: 24510, is_mother: false },
    { t: '2026-08-06T09:35:00+05:30', o: 24510, h: 24512, l: 24395, c: 24410, is_mother: false },
  ],
  anchor: fibTouchCampaign.anchor,
  levels: [
    { level: 2, price: 24500 }, { level: 3, price: 24400 },
    { level: 4, price: 24300 }, { level: 6, price: 24100 },
  ],
  trendline: {
    start_timestamp: '2026-08-06T09:21:00+05:30', start_price: 24624,
    anchor_timestamp: '2026-08-06T09:19:00+05:30', anchor_price: 24662,
  },
  note: 'Gap adjustment is visual only; the ladder\'s geometry uses native Dhan OHLC.',
};

test('Fib Boundary chart paints the swing, every level and each buy', async ({ page }) => {
  const jsErrors: string[] = [];
  page.on('pageerror', (err) => jsErrors.push(String(err)));

  await login(page);
  // Registered after login, so these win over the table in the fixture.
  await page.route('**/api/fib-boundary/paper/status**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok', mode: 'paper', campaigns: [fibTouchCampaign] }) }));
  await page.route('**/api/fib-boundary/paper/chart**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fibTouchChart) }));

  await page.click('#nav-cascade');
  await page.click('#oc-tabbtn-fib');

  // The monitor renders the running ladder before the chart is even opened.
  await expect(page.locator('#fibx-monitors [data-fx="badge"]')).toHaveText('OPEN');
  await expect(page.locator('#fibx-monitors [data-fx="fills"] tr')).toHaveCount(2);
  // Start is fail-closed for the selected instrument while its ladder runs,
  // and the button names exactly what must happen first.
  await expect(page.locator('#fibx-start')).toBeDisabled();
  await expect(page.locator('#fibx-start')).toContainText('Kill the NIFTY ladder first');
  // Which ladder is blocking is a TABLE, not a sentence that read as a riddle.
  await expect(page.locator('#fibx-blocked table')).toBeVisible();
  await expect(page.locator('#fibx-blocked')).toContainText('NIFTY');
  await expect(page.locator('#fibx-blocked')).toContainText('CE · 15M mother');
  await expect(page.locator('#fibx-blocked')).toContainText('1 ladder · one per instrument');
  // The anchor block is a table now, labelled fib high/low, and it no longer
  // spells out the involvement rule.
  await expect(page.locator('#fibx-monitors [data-fx="anchor"] table')).toBeVisible();
  await expect(page.locator('#fibx-monitors [data-fx="anchor"]')).toContainText('Fib high');
  await expect(page.locator('#fibx-monitors [data-fx="anchor"]')).toContainText('Fib low');
  await expect(page.locator('#fibx-monitors [data-fx="anchor"]')).toContainText('24,700');
  await expect(page.locator('#fibx-monitors [data-fx="anchor"]')).not.toContainText('consecutive candles');
  await expect(page.locator('#fibx-monitors [data-fx="summary"]')).toContainText('₹24,700');
  // The strip names the mother's chart and the mode it is running in.
  await expect(page.locator('#fibx-monitors [data-fx="gist"]')).toContainText('15M mother, 1m entries');
  await expect(page.locator('#fibx-monitors [data-fx="gist"]')).toContainText('PAPER');

  await page.click('#fibx-monitors [data-fx="chart"]');
  await page.waitForSelector('#pf-bench-canvas-main', { timeout: 10_000 });

  const paint = await page.evaluate(() => {
    const app = window as typeof window & { _pfChartCanvas?: { paint?: Record<string, unknown> } };
    if (!app._pfChartCanvas || !app._pfChartCanvas.paint) throw new Error('The fib-boundary canvas never painted');
    return app._pfChartCanvas.paint;
  });

  // Six candles in, six drawn -- a translator that drops the native-price
  // fallback silently renders none of them.
  expect(paint).toMatchObject({ candles: 6 });
  const labels = paint.labelTexts as string[];
  // The swing is the ladder's frame of reference and must be on the chart.
  expect(labels.some((t) => t.includes('SWING HIGH'))).toBe(true);
  expect(labels.some((t) => t.includes('SWING LOW'))).toBe(true);
  // Only the mother edge the ladder works against -- a CE draws its HIGH and
  // never its low, which is the whole of Phil's 2026-08-06 correction.
  expect(labels.some((t) => t.includes('MOTHER HIGH'))).toBe(true);
  expect(labels.some((t) => t.includes('MOTHER LOW'))).toBe(false);
  // The trendline is drawn even though it gates nothing.
  expect(paint).toMatchObject({ trendlines: 1 });
  // Each level carries its own live state, including the one the cap stopped.
  expect(labels.some((t) => t.startsWith('L2 FILLED'))).toBe(true);
  expect(labels.some((t) => t.startsWith('L4 PENDING'))).toBe(true);
  expect(labels.some((t) => t.startsWith('L6 UNFUNDED'))).toBe(true);
  // A ladder still holding must not claim it sold at the target.
  expect(labels.some((t) => t.includes('TARGET (open'))).toBe(true);

  await page.click('[data-pf-action="hideFibBoundaryChart"]');
  await expect(page.locator('#pf-bench-canvas-main')).toHaveCount(0);

  expect(jsErrors).toEqual([]);
});

test('Insights carries Heatmap and Study Lounge as tabs, and repaints nothing', async ({ page }) => {
  const jsErrors: string[] = [];
  page.on('pageerror', (err) => jsErrors.push(String(err)));

  await login(page);
  // The app's own palette, read BEFORE Insights loads its two panel sheets.
  const before = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--bg').trim());

  await page.click('#nav-insights');
  await expect(page.locator('#insights-page')).toBeVisible();

  // Two tabs, Heatmap first and active.
  await expect(page.locator('#insights-page .oc-tab')).toHaveCount(2);
  await expect(page.locator('#insights-tabbtn-heatmap')).toHaveClass(/is-active/);
  await expect(page.locator('#insights-heatmap')).toBeVisible();
  await expect(page.locator('#insights-study')).toBeHidden();

  // Both stylesheets redefine :root with the app's OWN variable names, so the
  // scoping in tools/scope_insights_css.py is the only thing stopping them
  // repainting every page. A regression here is silent and site-wide.
  const after = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--bg').trim());
  expect(after).toBe(before);

  await page.click('#insights-tabbtn-study');
  await expect(page.locator('#insights-study')).toBeVisible();
  await expect(page.locator('#insights-heatmap')).toBeHidden();
  // The panel's own script ran: the library rendered from the mocked payload.
  await expect(page.locator('#insights-study')).toContainText('Risk of ruin');

  // The dropdown is gone; Insights is a page like Cascade is.
  await expect(page.locator('#nav-insights-menu')).toHaveCount(0);
  // ...but it must not MOVE. The CSS orders the nav by id, and the rule used
  // to key the wrapper this commit deleted, which sent Insights to order 0
  // and put it first in the row.
  const order = await page.evaluate(() =>
    getComputedStyle(document.getElementById('nav-insights')!).order);
  expect(order).toBe('6');
  // The panels' actions wear the app's button, not their own skin.
  await expect(page.locator('#insights-study a.btn').first()).toBeVisible();
  await expect(page.locator('#insights-page .app-btn')).toHaveCount(0);

  expect(jsErrors).toEqual([]);
});

test('Recovery tab renders its controls and monitor', async ({ page }) => {
  const jsErrors: string[] = [];
  page.on('pageerror', (err) => jsErrors.push(String(err)));

  await login(page);
  await page.click('#nav-cascade');
  await page.click('#oc-tabbtn-recovery');

  await expect(page.locator('#oc-tab-recovery')).toBeVisible();

  // Every timeframe the engine supports, with the measured one first.
  await expect(page.locator('#recovery-timeframe option')).toHaveCount(4);
  await expect(page.locator('#recovery-timeframe')).toHaveValue('15m');
  await expect(page.locator('#recovery-mode option')).toHaveCount(2);

  // The monitor must render from a not_started payload rather than staying blank
  // -- a JS typo here leaves an empty panel that a green Python run never catches.
  await expect(page.locator('#recovery-badge')).toHaveText('IDLE');
  await expect(page.locator('#recovery-campaigns')).toContainText('Nothing running');
  await expect(page.locator('#recovery-start')).toBeVisible();
  await expect(page.locator('#recovery-stop')).toBeHidden();

  // The rules are stated where the trader can read them.
  await expect(page.locator('#options-cascade-page')).toContainText('NO REAL ORDER IS EVER SENT');

  expect(jsErrors, `page errors: ${jsErrors.join(' | ')}`).toHaveLength(0);
});

test('The action-authorization prompt sits above every other modal', async ({ page }) => {
  await login(page);

  // Every modal shares .modal-overlay at z-index 2000, so a tie is broken by
  // DOM order -- and #admin-modal is declared AFTER #action-auth-modal. That
  // put the password/authenticator prompt BEHIND the admin window, where it
  // could not be reached, and disabling a user looked broken.
  const layers = await page.evaluate(() => {
    const z = (id: string) => {
      const el = document.getElementById(id);
      if (!el) return null;
      return parseInt(getComputedStyle(el).zIndex || '0', 10);
    };
    return {
      actionAuth: z('action-auth-modal'),
      admin: z('admin-modal'),
      account: z('account-modal'),
      confirm: z('confirm-modal'),
    };
  });

  expect(layers.actionAuth).not.toBeNull();
  for (const [name, value] of Object.entries(layers)) {
    if (name === 'actionAuth' || value === null) continue;
    expect(layers.actionAuth!, `action-auth must outrank #${name}`).toBeGreaterThan(value);
  }
});
