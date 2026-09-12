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
    else if (path === '/api/published-runs') await route.fulfill({ json: [] });
    else if (path.startsWith('/api/published-runs/')) await route.fulfill({ json: { status: 'error', message: 'E2E offline published run mock' } });
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
    else if (path === '/api/two-red/status') await route.fulfill({ json: { status: 'ok', campaigns: [], closed: [] } });
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
    else if (path === '/api/gap-carry/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper', live_available: false, auto: {}, timeframes: ['5m', '15m'] } });
    else if (path === '/api/live/index-chart') await route.fulfill({ json: { status: 'ok', timeframe: '5m', is_open: true, instrument: { underlying: 'NIFTY 50' }, candles: [], entries: [], exits: [], lines: [], overlays: {}, live_price: 0 } });
    else if (path === '/api/live/runs') await route.fulfill({ json: { status: 'ok', runs: [], count: 0, booked_total: 0, day_total: 0 } });
    else if (path === '/api/recovery/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper' } });
    else if (path === '/api/supertrend/paper/status') await route.fulfill({ json: { status: 'not_started', mode: 'paper', live_available: false, auto: {}, timeframes: ['1h', '30m'] } });
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
    // The Fib Boundary panel asks for the last saved replay on every load, so
    // that the result of a run that cost several Dhan round trips survives a
    // redraw. No saved run is the normal answer.
    else if (path === '/api/fib-boundary/backtests/latest') await route.fulfill({ json: { status: 'ok', run: null } });
    else if (path === '/api/candle-entry/backtests/latest') await route.fulfill({ json: { status: 'ok', run: null } });
    else if (path === '/api/gap-carry/backtests/latest') await route.fulfill({ json: { status: 'empty' } });
    else if (path === '/api/supertrend/backtests/latest') await route.fulfill({ json: { status: 'ok', run: null } });
    else if (path === '/api/recovery/backtests/latest') await route.fulfill({ json: { status: 'empty' } });
    // THE PAPER LEDGER. Every strategy tab asks for its finished campaigns on
    // each refresh; an empty archive is the normal answer offline.
    else if (path.startsWith('/api/paper-campaigns/')) await route.fulfill({ json: { status: 'ok', campaigns: [], count: 0, net_total: null } });
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
  await page.waitForFunction(() => document.documentElement.getAttribute('data-nav-ready') === '1');
}

test('audit: switching saved strategies clears stale targets and treats labels as text', async ({ page }) => {
  await login(page);
  const malicious = '<img src=x onerror="window.__auditXss=1">';
  await page.evaluate((label) => {
    const strategies = [
      { id: 991, run_name: 'Targeted', combined_target_rupees: 10000, combined_sl_rupees: 500 },
      { id: 992, run_name: label, combined_target_rupees: 0, combined_sl_rupees: 0,
        indicators: [label], folder: 'A "] quoted folder' },
      { id: 993, run_name: 'No optional combined limits' },
    ];
    (window as any).eval('savedStrategiesCache = ' + JSON.stringify(strategies));
    (window as any).loadStrategy(991);
  }, malicious);
  await expect(page.locator('#combined-target-rupees')).toHaveValue('10000');
  await page.evaluate(() => (window as any).loadStrategy(992));
  await expect(page.locator('#combined-target-rupees')).toHaveValue('0');
  await expect(page.locator('#combined-sl-rupees')).toHaveValue('0');
  await expect(page.locator('#active-indicators-list img')).toHaveCount(0);
  await expect(page.locator('#active-indicators-list')).toContainText(malicious);
  await expect(page.locator('#loaded-strategy-info img')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__auditXss)).toBeUndefined();
  await expect(page.locator('#active-indicators-list .pf-indicator-remove')).toHaveAttribute('type', 'button');
  await page.locator('#active-indicators-list .pf-indicator-remove').click();
  await expect(page.locator('#active-indicators-list .pf-indicator-remove')).toHaveCount(0);
  await page.evaluate(() => (window as any).loadStrategy(993));
  await expect(page.locator('#combined-target-rupees')).toHaveValue('');
});

test('audit: light chart overlays resolve readable colors and missing PnL is not zero', async ({ page }) => {
  await login(page);
  const result = await page.evaluate(() => (window as any).eval(`({
    missing: _cepeMoney(null), empty: _cepeMoney(''), zero: _cepeMoney(0),
    light: _pfChartSeriesColor('#4ade80', _PF_CHART_LIGHT, '#000'),
    dark: _pfChartSeriesColor('#4ade80', _PF_CHART_DARK, '#000')
  })`));
  expect(result.missing).toBe('—');
  expect(result.empty).toBe('—');
  expect(result.zero).not.toBe('—');
  expect(result.light).toBe('#15803d');
  expect(result.dark).toBe('#4ade80');
  await openTradingSection(page, 'cascade');
  await page.locator('#oc-tabbtn-cepe').click();
  for (const theme of ['light', 'dark']) {
    await page.evaluate((value) => (window as any).pfApplyTheme(value), theme);
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.locator('#options-cascade-page')).toHaveClass(/active-page/);
      const headerState = await page.locator('#options-cascade-page .trading-workspace-head').evaluate((header) => {
        // Reproduce focus/scroll-into-view scrolling a decorative overflow-hidden header.
        header.scrollTo(76, 21);
        return { left: header.scrollLeft, top: header.scrollTop,
          titleInside: header.querySelector('.trading-workspace-intro')!.getBoundingClientRect().left >= header.getBoundingClientRect().left };
      });
      expect(headerState).toEqual({ left: 0, top: 0, titleInside: true });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
      await page.screenshot({ path: test.info().outputPath(`cepe-${theme}-${width}.png`), fullPage: true, animations: 'disabled' });
    }
  }
});

async function openTradingSection(page: Page, section: 'equity' | 'scalp' | 'cascade') {
  await page.click('#nav-trading');
  const pageId = {
    equity: 'stock-terminal-page',
    scalp: 'scalp-page',
    cascade: 'options-cascade-page',
  }[section];
  await page.locator('.page-section.active-page [data-pf-trading-page="' + pageId + '"]').click();
  await expect(page.locator('#' + pageId)).toHaveClass(/active-page/);
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
    ['#nav-trading', '#options-cascade-page'],
    ['#nav-builder', '#builder-page'],
    ['#nav-charts', '#charts-page'],
    ['#nav-results', '#results-page'],
  ];
  for (const [control, pageSection] of surfaces) {
    await page.click(control);
    await expect(page.locator(control)).toHaveAttribute('aria-current', 'page');
    expect(await seriousAccessibilityViolations(page, pageSection), pageSection).toEqual([]);
  }
  for (const [section, pageSection] of [
    ['equity', '#stock-terminal-page'],
    ['scalp', '#scalp-page'],
    ['cascade', '#options-cascade-page'],
  ] as const) {
    await openTradingSection(page, section);
    expect(await seriousAccessibilityViolations(page, pageSection), pageSection).toEqual([]);
  }
});

test('Insights, Cascade, and Journal subpanels have no serious automated WCAG violations', async ({ page }) => {
  // Multiple complete Axe scans share this test's deadline.
  test.setTimeout(90000);
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

  await openTradingSection(page, 'cascade');
  for (const [control, panel] of [
    ['#oc-tabbtn-cepe', '#oc-tab-cepe'],
    ['#oc-tabbtn-fib', '#oc-tab-fib'],
    ['#oc-tabbtn-candle', '#oc-tab-candle'],
    ['#oc-tabbtn-recovery', '#oc-tab-recovery'],
    ['#oc-tabbtn-gapcarry', '#oc-tab-gapcarry'],
    ['#oc-tabbtn-supertrend', '#oc-tab-supertrend'],
  ]) {
    await page.click(control);
    await expect(page.locator(control)).toHaveAttribute('aria-selected', 'true');
    expect(await seriousAccessibilityViolations(page, panel), panel).toEqual([]);
  }
  await page.locator('#oc-tabbtn-supertrend').focus();
  await page.keyboard.press('Home');
  // Home goes to the FIRST tab. Gap Carry led the strip from 2026-08-25; CE + PE
  // took the front on 2026-09-09, being the desk that trades real money.
  await expect(page.locator('#oc-tabbtn-cepe')).toHaveAttribute('aria-selected', 'true');

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
    ['#nav-trading', '#options-cascade-page'],
    ['#nav-builder', '#builder-page'],
    ['#nav-charts', '#charts-page'],
    ['#nav-results', '#results-page'],
  ];
  for (const [control, pageSection] of surfaces) {
    await page.click(control);
    await expect(page.locator(pageSection)).toHaveClass(/active-page/);
  }

  for (const section of ['equity', 'scalp', 'cascade'] as const) {
    await openTradingSection(page, section);
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

test('Trading defaults to Cascade and remembers its last desk and page views', async ({ page }) => {
  await login(page);

  const primaryLabels = await page.locator('.nav-tabs > .nav-tab .tab-label').allTextContents();
  expect(primaryLabels).toContain('Trading');
  expect(primaryLabels).not.toContain('Equity');
  expect(primaryLabels).not.toContain('Scalp');
  expect(primaryLabels).not.toContain('Cascade');

  await page.click('#nav-trading');
  await expect(page.locator('#options-cascade-page')).toHaveClass(/active-page/);
  await expect(page).toHaveURL(/#trading\/cascade$/);

  await expect(page.locator('#options-cascade-page .trading-section-tab strong')).toHaveText([
    'Options',
    'Scalp',
    'Equity',
  ]);

  const activeCascadeTab = page.locator('#options-cascade-page .trading-section-tab.is-active');
  await expect(activeCascadeTab).toContainText('Options');
  await activeCascadeTab.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);
  await expect(page).toHaveURL(/#trading\/scalp$/);

  await page.click('#nav-dashboard');
  await page.click('#nav-trading');
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);

  await page.locator('#scalp-page .trading-section-tab[data-pf-trading-page="options-cascade-page"]').click();
  await expect(page.locator('#options-cascade-page')).toHaveClass(/active-page/);
  await expect(page).toHaveURL(/#trading\/cascade$/);

  await page.locator('[data-oc-tab="gapcarry"]').click();
  await expect(page.locator('#oc-tab-gapcarry')).toBeVisible();

  await page.reload();
  await expect(page.locator('#options-cascade-page')).toHaveClass(/active-page/);
  await expect(page.locator('#nav-trading')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#options-cascade-page .trading-section-tab.is-active')).toContainText('Options');
  await expect(page.locator('[data-oc-tab="gapcarry"]')).toHaveClass(/is-active/);
  await expect(page.locator('#oc-tab-gapcarry')).toBeVisible();
});

test('Primary pages and stable nested views survive refresh', async ({ page }) => {
  await login(page);

  for (const [control, pageSection] of [
    ['#nav-portfolio', '#portfolio-page'],
    ['#nav-builder', '#builder-page'],
    ['#nav-results', '#results-page'],
  ] as const) {
    await page.click(control);
    await page.reload();
    await expect(page.locator(pageSection)).toHaveClass(/active-page/);
  }

  await page.click('#nav-insights');
  await page.locator('[data-insights-tab="study"]').click();
  await page.reload();
  await expect(page.locator('#insights-page')).toHaveClass(/active-page/);
  await expect(page.locator('[data-insights-tab="study"]')).toHaveClass(/is-active/);
  await expect(page.locator('#insights-study')).toBeVisible();

  await page.click('#nav-assets');
  await page.locator('[data-pf-architecture-view="philforge"]').click();
  await page.reload();
  await expect(page.locator('#assets-page')).toHaveClass(/active-page/);
  await expect(page.locator('[data-pf-architecture-view="philforge"]')).toHaveAttribute('aria-selected', 'true');

  await page.click('#nav-charts');
  await page.locator('#cj-tab-plan').click();
  await page.reload();
  await expect(page.locator('#charts-page')).toHaveClass(/active-page/);
  await expect(page.locator('#cj-tab-plan')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#cj-plan-view')).toBeVisible();

  await openTradingSection(page, 'equity');
  await page.locator('[data-equity-strategy="tworeds"]').click();
  await page.reload();
  await expect(page.locator('#stock-terminal-page')).toHaveClass(/active-page/);
  await expect(page.locator('[data-equity-strategy="tworeds"]')).toHaveClass(/is-active/);
  await expect(page.locator('#equity-strategy-tworeds')).toBeVisible();
});

test('Appearance presets switch and persist after reload', async ({ page }) => {
  await login(page);

  await page.click('#appearance-btn');
  await expect(page.locator('#appearance-modal')).toHaveClass(/open/);

  // The roster comes from the page's own registry, never typed here.
  const tintIds: string[] = await page.evaluate(() =>
    ((window as any).PHILFORGE_APPEARANCE_PRESETS?.tints || [])
      .map((t: any) => t.id)
  );
  expect(tintIds).toHaveLength(7);
  const tintPalettes: Record<string, string> = {};
  const surfacePalettes: Record<string, string> = {};
  for (const tint of tintIds) {
    await page.click(`[data-appearance-tint="${tint}"]`);
    if (tint === 'native') await expect(page.locator('html')).not.toHaveAttribute('data-pf-tint');
    else await expect(page.locator('html')).toHaveAttribute('data-pf-tint', tint);
    tintPalettes[tint] = await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement);
      return [root.getPropertyValue('--accent').trim(), getComputedStyle(document.body).backgroundImage].join('|');
    });
    surfacePalettes[tint] = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--card').trim());
  }
  expect(new Set(Object.values(tintPalettes)).size).toBe(7);
  expect(new Set(Object.values(surfacePalettes)).size).toBe(1);

  const fontStacks: Record<string, string> = {};
  const fontIds: string[] = await page.evaluate(() =>
    ((window as any).PHILFORGE_APPEARANCE_PRESETS?.fonts || []).map((font: any) => font.id)
  );
  expect(fontIds).toHaveLength(6);
  for (const font of fontIds) {
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
  expect(new Set(Object.values(fontStacks)).size).toBe(6);

  await page.reload();
  await expect(page.locator('.nav-tab').first()).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-pf-tint', tintIds[tintIds.length - 1]);
  await expect(page.locator('html')).toHaveAttribute('data-pf-font', fontIds[fontIds.length - 1]);
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
      candleReplay: color('#oc-candle-status'),
      candleBadge: color('#oc-candle-badge'),
      candleWarning: color('#oc-candle-summary .is-warning'),
      fibBoundary: color('#oc-fib-status'),
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
  await page.getByRole('button', { name: 'Close appearance', exact: true }).click();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.nav-bar')).toBeVisible();
  await expect(page.locator('.nav-bar')).toHaveScreenshot('mobile-nav.png', {
    animations: 'disabled',
    maxDiffPixelRatio: 0.04,
  });

  await openTradingSection(page, 'scalp');
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);
  await expect(page.locator('#scalp-form-title')).toBeVisible();
  // This screenshot checks the launchpad layout, not live WebSocket timing.
  // CI can reach the local feed before the capture while a slower run reaches
  // the disconnected heartbeat first; those two labels wrap the command bar
  // differently on a 390px screen. Pin the documented offline state so the
  // visual gate measures one deterministic layout.
  await page.evaluate(() => {
    const setIndicator = (window as typeof window & { _wsSetLiveIndicator?: (connected: boolean, stale: boolean) => void })._wsSetLiveIndicator;
    if (typeof setIndicator === 'function') setIndicator(false, false);
  });
  await expect(page.locator('#ws-status-label')).toHaveText('Disconnected');
  // The NIFTY chart control is intentionally between Start and Stop. Assert it
  // separately, then remove it only for the legacy launchpad baseline: this
  // snapshot's job is the ticket below, not the command-bar feature count.
  await expect(page.getByRole('button', { name: /NIFTY 5m/ })).toBeVisible();
  await page.locator('#scalp-start-btn + [data-pf-action="openCePeIndexChart"][data-cepe-tf="5m"]').evaluate((element) => {
    (element as HTMLElement).style.display = 'none';
  });
  await expect(page.locator('#scalp-page')).toHaveScreenshot('scalp-launchpad.png', {
    animations: 'disabled',
    maxDiffPixelRatio: 0.04,
  });
});

// A blank chart is the failure this catches. The renderer is hand-written
// Canvas: a typo in a draw layer throws inside a paint loop, the surface stays
// empty, and every Python test still passes. So this asserts the semantic paint
// record — real candles, real geometry, real labels — not just that a canvas
// element exists.
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
  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-candle');

  // The four ladders, each named by the charts it climbs through.
  await expect(page.locator('#oc-candle-timeframe option')).toHaveCount(4);
  // Two rungs: the starting chart and the one above it.
  await expect(page.locator('#oc-candle-timeframe option[value="1m"]')).toHaveText(/^1m · 1m → 5m$/);
  await expect(page.locator('#oc-candle-timeframe option[value="15m"]')).toHaveText(/^15m · 15m → 1H$/);
  await expect(page.locator('#oc-candle-timeframe option[value="1h"]')).toHaveText(/^1H · 1H → 1D$/);

  // Switching the chart retunes the mother calendar's minutes.
  await page.selectOption('#oc-candle-timeframe', '1h');
  await expect(page.locator('#oc-candle-mother-timestamp')).toHaveAttribute('data-pf-calendar-minutes', '15');
  await page.selectOption('#oc-candle-timeframe', '15m');
  await expect(page.locator('#oc-candle-mother-timestamp')).toHaveAttribute('data-pf-calendar-minutes', '0,15,30,45');

  // The copy sells the ladder, not the old single 1H buy.
  await expect(page.locator('#oc-candle-page-kicker, #options-cascade-page')).toContainText('TWO-RED LADDER');
});

// Phil, 2026-08-20, looking at a fully-bought paper basket: "Now I don't know
// whether I started paper or not... The trading console is completely shit".
// The monitor showed deployed capital and NOTHING about what the basket was
// worth. It now carries the open P&L, marked and stamped, and each rung's own
// contract priced where it stands.
// Phil, 2026-08-20: "Need a link to the tearsheet P&L same like that we have
// it on Fib boundary". The chip walks to Assets -> Tearsheet -> Candle Entry
// at the trail book's equity curve, in the same window; the href stays real so
// a middle-click still opens the document on its own.
test('The Candle Entry card links to its own tearsheet P&L', async ({ page }) => {
  await login(page);
  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-candle');

  const chip = page.locator('#oc-tab-candle .ocp-confirmed-link');
  await expect(chip).toBeVisible();
  await expect(chip).toHaveAttribute('href', '/assets/tearsheet?doc=candle#curve-trail');

  await chip.click();
  // The Assets tearsheet view, on the Candle Entry document, at the curve.
  await expect(page.locator('#assets-tearsheet-panel')).toBeVisible();
  await expect.poll(async () => page.locator('.pf-tearsheet-doc[data-doc="candle"]').getAttribute('class'))
    .toContain('is-active');
  await expect.poll(async () => page.locator('#assets-tearsheet-frame').getAttribute('src'))
    .toContain('doc=candle');
  await expect.poll(async () => page.locator('#assets-tearsheet-frame').getAttribute('src'))
    .toContain('#curve-trail');

  // Both chips share one walk now, so the Fib Boundary one is proved here too:
  // its own document, at its own section.
  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-fib');
  await page.locator('#oc-tab-fib .ocp-confirmed-link').click();
  await expect.poll(async () => page.locator('.pf-tearsheet-doc[data-doc="fib"]').getAttribute('class'))
    .toContain('is-active');
  await expect.poll(async () => page.locator('#assets-tearsheet-frame').getAttribute('src'))
    .toContain('doc=fib');
  await expect.poll(async () => page.locator('#assets-tearsheet-frame').getAttribute('src'))
    .toContain('#auto-mother');
});

// A replay is filed server-side so a page reload brings it back without paying
// for it again -- but the file outlives the RULE. A run saved on 19 Aug under
// three rungs struck at the mother was still the panel's whole content after
// the ladder became two rungs struck at each buy, and nothing on the page could
// remove it (Phil, 2026-08-20: "I am not able to remove the old one"). So a
// restored replay says it is a stored one, and carries its own delete.
test('A restored Candle Entry replay says it is saved, and can be thrown away', async ({ page }) => {
  await login(page);
  const saved = {
    status: 'ok',
    run: {
      created_at: '2026-08-19T22:31:57+05:30',
      payload: {
        status: 'ok', mode: 'backtest', pricing: 'recorded_history', timeframe: '5m',
        stages: ['5m', '15m', '1h'], lot_size: 65, strike_at: 'mother', expiry_rule: 'monthly',
        target_fraction: 0.25, trailing_target: true, intraday_close: false, mother_mode: 'clock',
        candles_replayed: 1498, horizon_to: '2026-05-26T15:30:00+05:30', still_open: false,
        contract: { strike: 24350, expiry: '2026-05-28' },
        mother: { timestamp: '2026-05-07T12:40:00+05:30', high: 24482.1 },
        campaign: {
          net_pnl: -48181.12, gross_pnl: -48054.5, costs_total: 126.62, deployed_inr: 48405.5,
          average_entry: 23715.6, target_index: 23907.22,
          exit: { timestamp: '2026-05-26T13:05:00+05:30', reason: 'trail', index_price: 23977.54, option_premium: 0.9 },
          fills: [
            { rung: 1, timeframe: '5m', timestamp: '2026-05-11T09:45:00+05:30', index_price: 23881.8, strike: 24350, option_premium: 176, lots: 1, quantity: 65 },
          ],
        },
        charts: {}, premium_failures: [], premium_stale_fills: [],
        note: 'NIFTY CE two-red ladder from a 5m mother.',
      },
    },
  };
  await page.route('**/api/candle-entry/backtests/latest', route => {
    if (route.request().method() === 'DELETE') return route.fulfill({ json: { status: 'ok', removed: true } });
    return route.fulfill({ json: saved });
  });
  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-candle');

  const panel = page.locator('#oc-candle-backtest');
  await expect(panel).toBeVisible();
  // It is labelled a stored result, with the minute it was stored.
  await expect(page.locator('#oc-candle-backtest-badge')).toContainText('SAVED');
  await expect(page.locator('#oc-candle-backtest-stale')).toContainText('Saved replay');
  await expect(page.locator('#oc-candle-backtest-stale')).toContainText('a ladder the page no longer trades');
  await expect(page.locator('#oc-candle-backtest-note')).toContainText('saved run from');

  // It reads BELOW the live monitor, not above it.
  const order = await page.evaluate(() => {
    const live = document.getElementById('oc-candle-monitor');
    const back = document.getElementById('oc-candle-backtest');
    return live.compareDocumentPosition(back) & Node.DOCUMENT_POSITION_FOLLOWING ? 'after' : 'before';
  });
  expect(order).toBe('after');

  // CSV survives; the JSON download is gone.
  await expect(page.locator('#oc-candle-backtest-csv')).toBeVisible();
  await expect(page.locator('#oc-candle-backtest-json')).toHaveCount(0);

  // And it can be removed.
  await page.click('#oc-candle-backtest-delete');
  await page.click('#confirm-ok-btn');
  await expect(panel).toBeHidden();
  await expect(page.locator('#oc-candle-status')).toContainText('Saved replay deleted');
});

// The auto row is drawn from the auto SETTING, which can still carry the
// free_from stamp of an earlier campaign -- so it read "waiting for the next
// new 278-bar high" over a live basket holding two rungs (Phil's screen,
// 2026-08-21 11:23, mid-deploy). A running campaign is not waiting.
test('The auto row does not call a running campaign "waiting"', async ({ page }) => {
  await login(page);
  await page.route('**/api/candle-entry/paper/status', route => route.fulfill({
    json: {
      status: 'ok', mode: 'paper',
      auto: { enabled: true, mode: 'paper', free_from: '2026-08-21T00:03:00+05:30', last_mother: '2026-08-03T15:25:00+05:30', log: [] },
      campaign: {
        running: true, status: 'OPEN', strike_at: 'each_buy',
        contract: { underlying: 'NIFTY', strike: 24400, option_type: 'CE', expiry: '2026-08-25', lot_size: 65 },
        mother: { timestamp: '2026-08-03T15:25:00+05:30', high: 24774.3 },
        latest_closed_candle: { timestamp: '2026-08-21T11:15:00+05:30', close: 24255.35 },
        box: { bars: 278, filled: 278, line: 24109.26, low: 24025.65, high: 24360.1 },
        target_index: 24509.04, deployed_inr: 56953, net_pnl: null, exit: null, events: [],
        rungs: [
          { rung: 1, timeframe: '5m', lots: 1, quantity: 65, state: 'filled',
            fill: { timestamp: '2026-08-11T09:25:00+05:30', index_price: 24504.75, option_premium: 309, strike: 24400, option_type: 'CE', quantity: 65 } },
        ],
      },
    },
  }));
  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-candle');

  const card = page.locator('#oc-candle-auto-card');
  await expect(card).toBeVisible();
  await expect(card).toContainText('mother 3 Aug 15:25 · running');
  await expect(card).not.toContainText('waiting for the next new');
});

test('A held Candle Entry basket shows what it is worth right now', async ({ page }) => {
  await login(page);
  const marked = {
    status: 'ok', mode: 'paper',
    campaign: {
      running: true, status: 'OPEN', strike_at: 'each_buy',
      contract: { underlying: 'NIFTY', strike: 24400, option_type: 'CE', expiry: '2026-08-25', lot_size: 65 },
      mother: { timestamp: '2026-08-03T15:25:00+05:30', high: 24774.3 },
      latest_closed_candle: { timestamp: '2026-08-20T09:45:00+05:30', close: 24203.35 },
      box: { bars: 278, filled: 278, line: 24120.54, low: 24025.65, high: 24405.2 },
      target_index: 24464.89, deployed_inr: 117042.25, net_pnl: null, exit: null, events: [],
      rungs: [
        { rung: 1, timeframe: '5m', lots: 1, quantity: 65, state: 'filled',
          fill: { timestamp: '2026-08-11T09:25:00+05:30', index_price: 24504.75, option_premium: 309, strike: 24400, option_type: 'CE', quantity: 65 } },
        { rung: 2, timeframe: '15m', lots: 2, quantity: 130, state: 'filled',
          fill: { timestamp: '2026-08-12T10:15:00+05:30', index_price: 24378.55, option_premium: 283.6, strike: 24300, option_type: 'CE', quantity: 130 } },
      ],
      mark: {
        at: '2026-08-20T09:51:00+05:30', unpriced: false,
        deployed_inr: 56953.0, gross_pnl: 4875.0, costs_total: 375.0, net_pnl: 4500.0, return_pct: 7.9,
        legs: [
          { strike: 24400, option_type: 'CE', quantity: 65, paid: 309, mark: 329, gross_pnl: 1300 },
          { strike: 24300, option_type: 'CE', quantity: 130, paid: 283.6, mark: 311.1, gross_pnl: 3575 },
        ],
      },
    },
  };
  await page.route('**/api/candle-entry/paper/status', route => route.fulfill({ json: marked }));
  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-candle');

  // It says, in words, that a paper campaign is RUNNING and since when.
  // One line, and only what the heading and the recipe strip do not already
  // say (Phil, 2026-08-21: "Decrease the texts here... simple and crisp").
  await expect(page.locator('#oc-candle-monitor-kicker')).toContainText('Paper · NIFTY 24400 CE · 25 Aug');
  await expect(page.locator('#oc-candle-monitor-kicker')).toContainText('from 11 Aug 09:25');

  // The money tile: net if sold now, its return, and the minute it was marked.
  const tiles = page.locator('#oc-candle-tiles');
  await expect(tiles).toContainText('Open P&L · if sold');
  await expect(tiles).toContainText('+₹4,500.00');
  await expect(tiles).toContainText('7.9%');
  await expect(page.locator('#oc-candle-monitor-updated')).toContainText('marked 09:51');

  // Each rung priced on ITS OWN contract, with the move on its own quantity.
  const rows = page.locator('#oc-candle-rows tr');
  await expect(rows.nth(0)).toContainText('₹329.00');
  await expect(rows.nth(0)).toContainText('+₹1,300.00');
  await expect(rows.nth(1)).toContainText('₹311.10');
  await expect(rows.nth(1)).toContainText('+₹3,575.00');
  await expect(rows).toHaveCount(2);

  // A phone reads it without the page itself scrolling sideways.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(tiles).toContainText('+₹4,500.00');
});

// Phil, 2026-08-24: "I need a change on the button after paper / Live is
// started.... and status and info panel on the right". Every Gap Carry test
// before this one mocked `not_started`, so the suite had never once seen a
// RUNNING carry -- and a campaign that had started left the button reading
// "Start paper carry" over an empty right-hand pane.
test('A started Gap Carry says so on the button and fills the right pane', async ({ page }) => {
  await login(page);
  const waiting = {
    status: 'ok', mode: 'paper', live_available: false, auto: {}, timeframes: ['5m', '15m'],
    campaign: {
      strategy: 'gap_carry', status: 'WAITING', timeframe: '5m', running: true, open: false,
      rule: { rsi_threshold: 70, rsi_for_call: 70, rsi_for_put: 30, strike_offset_steps: 4, lots: 1,
              entry_time: '15:10', exit_time: '09:20', ema_period: 20 },
      signal: { timestamp: '2026-08-24T15:10:00+05:30', close: 24612.4, ema: 24580.1, rsi: 64.2,
                side: null, reason: 'RSI 64.2 is inside the band' },
      position: null, mark: null, last_index_close: 24612.4,
      closed_trades: 0, realised: 0, floored_exits: 0, floored_net: 0,
      notes: ['2026-08-24: RSI 64.2 is inside the band'], history: [],
    },
  };
  await page.route('**/api/gap-carry/paper/status', route => route.fulfill({ json: waiting }));
  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-gapcarry');

  // THE BUTTON. It must not still be inviting a start that already happened.
  const start = page.locator('#oc-gap-start');
  await expect(start).toContainText('Carrying');
  await expect(start).toBeDisabled();
  // And Kill must be reachable while it merely WAITS -- a campaign waiting for
  // 15:10 is still a campaign, and used to have no way to be stopped.
  await expect(page.locator('#oc-gap-stop')).toBeVisible();
  await expect(page.locator('#oc-gap-badge')).toContainText('WAITING');

  // THE RIGHT PANE. It used to stay hidden until something was bought, so a
  // carry started in the morning showed nothing at all until 15:10.
  const monitor = page.locator('#oc-gap-monitor');
  await expect(monitor).toBeVisible();
  await expect(page.locator('#oc-gap-monitor-title')).toContainText('Waiting for 15:10');
  await expect(page.locator('#oc-gap-tiles')).toContainText('RSI 64.2');
  await expect(page.locator('#oc-gap-monitor-rule')).toContainText('ATM+4 ITM');
  await expect(page.locator('#oc-gap-rows')).toContainText('Nothing bought yet');
  await expect(page.locator('#oc-gap-event-count')).toContainText('1 update');
  // The tiles must use the styled class, not the two that never existed.
  await expect(page.locator('#oc-gap-tiles .ocp-tile').first()).toBeVisible();

  // A phone reads it without the page itself scrolling sideways.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(monitor).toBeVisible();
});

// Phil, 2026-08-24: "Is delete button and stop button placed for paper run
// backtest and live and also the chart with indicators?" The chart did not
// exist, and the shared renderer could draw no indicator at all -- a trendline
// has two anchors and `lines` are horizontal, so neither can be a curve. This
// asserts the two new layers actually paint, and that the RSI pane never eats
// the price pane it sits under.
test('The Gap Carry chart draws the EMA and the RSI that are its rule', async ({ page }) => {
  await login(page);
  const running = {
    status: 'ok', mode: 'paper', live_available: false, auto: {}, timeframes: ['5m', '15m'],
    campaign: {
      strategy: 'gap_carry', status: 'HOLDING', timeframe: '5m', running: true, open: true,
      rule: { rsi_threshold: 70, rsi_for_call: 70, rsi_for_put: 30, strike_offset_steps: 4, lots: 1,
              entry_time: '15:10', exit_time: '09:20', ema_period: 20 },
      signal: { timestamp: '2026-08-19T15:10:00+05:30', close: 24700, ema: 24610, rsi: 73.8, side: 'CE', reason: 'RSI 73.8' },
      position: { session: '2026-08-19', side: 'CE', strike: 24500, expiry: '2026-08-28', lots: 1, lot_size: 65,
                  entry: { timestamp: '2026-08-19T15:10:00+05:30', spot: 24700, premium: 268.5, capital: 17452.5 }, exit: null, net: null },
      mark: { at: '2026-08-20T09:19:00+05:30', premium: 301.75, unrealised: 2161.25 },
      last_index_close: 24712, closed_trades: 0, realised: 0, floored_exits: 0, floored_net: 0, notes: [], history: [],
    },
  };
  // 240 bars of a real-shaped tape, with the two series the engine emits.
  const t0 = 1755500000, candles = [], ema = [], rsi = [];
  let px = 24500;
  for (let i = 0; i < 240; i++) {
    px += Math.sin(i / 11) * 9 + (i % 5 - 2);
    candles.push({ t: t0 + i * 300, o: px, h: px + 12, l: px - 11, c: px + 3, is_mother: false });
    ema.push({ t: t0 + i * 300, v: i < 19 ? null : px - 8 });
    rsi.push({ t: t0 + i * 300, v: i < 13 ? null : 50 + 22 * Math.sin(i / 13) });
  }
  await page.route('**/api/gap-carry/paper/status', route => route.fulfill({ json: running }));
  await page.route('**/api/gap-carry/paper/chart**', route => route.fulfill({
    json: {
      status: 'ok', timeframe: '5m', stages: ['5m', '15m'], campaign_status: 'HOLDING',
      chart: {
        timeframe: '5m', candles, mother: { high: null, low: null }, trendlines: [], legs: [],
        lines: [{ price: 24500, label: 'CE 24500 · 2026-08-28', inr_notional: 0, filled: true }],
        entries: [{ t: t0 + 300 * 100, price: 24700 }], exits: [],
        avg_entry_price: null, tp_price: null, tp_label: '',
        indicators: { ema, rsi, ema_period: 20, rsi_period: 14, rsi_upper: 70, rsi_lower: 30 },
      },
    },
  }));
  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-gapcarry');

  // The button only exists while there is a campaign to draw.
  const chartBtn = page.locator('#oc-gap-chart-btn');
  await expect(chartBtn).toBeVisible();
  await chartBtn.click();
  await expect(page.locator('#oc-gap-chart-overlay')).toHaveClass(/is-open/);
  await expect(page.locator('#oc-gap-chart #pf-bench-canvas-host')).toBeVisible();

  // THE PAINT SEAM. Pixels alone cannot tell a missing EMA from a flat one.
  const paint = await page.evaluate(() => {
    const c = window._pfChartCanvas;
    return c && c.paint ? { candles: c.paint.candles, ema: c.paint.ema, rsi: c.paint.rsi,
                            rsiH: Math.round(c.projection.rsiH), plotH: Math.round(c.projection.plotH) } : null;
  });
  expect(paint).not.toBeNull();
  expect(paint.candles).toBe(240);
  expect(paint.ema).toBeGreaterThan(200);
  expect(paint.rsi).toBeGreaterThan(200);
  // The sub-pane is a SHARE of the height, and the price keeps the majority.
  expect(paint.rsiH).toBeGreaterThan(40);
  expect(paint.plotH).toBeGreaterThan(paint.rsiH * 2);
  await expect(page.locator('#oc-gap-chart-meta')).toContainText('EMA20 and RSI14');

  // A payload WITHOUT indicators must reserve no pane at all -- that is the
  // whole reason the other four charts on this page are unaffected.
  const noInd = await page.evaluate(() => {
    const d = JSON.parse(JSON.stringify(window._pfChartCanvas.data));
    delete d.indicators;
    window._pfChartCanvasRefresh(d, null);
    const c = window._pfChartCanvas;
    return { rsiH: Math.round(c.projection.rsiH), ema: c.paint.ema, rsi: c.paint.rsi };
  });
  expect(noInd.rsiH).toBe(0);
  expect(noInd.ema).toBe(0);
  expect(noInd.rsi).toBe(0);
});

// Phil, 2026-08-16, pointing at the Cash Cascade page: "Why don't you put the
// panels in this format?.. The (i) for cascades". They already shared its
// classes; what they did not share was ROOM. A .pf-info-doc flows its sections
// into ~34em columns, so a doc boxed inside the narrow setup card, or capped at
// 960px, can never form them — same stylesheet, different shape on screen.
test('Every cascade ⓘ reads as the Cash Cascade document', async ({ page }) => {
  const jsErrors: string[] = [];
  page.on('pageerror', (err) => jsErrors.push(String(err)));

  await login(page);
  await page.setViewportSize({ width: 1600, height: 1100 });

  // The reference, on the Equity desk. It is the format, so it is measured
  // alongside the others rather than assumed.
  await openTradingSection(page, 'equity');
  await page.click('[data-pf-info="cash-cascade-rules"]');
  const reference = await docShape(page, 'cash-cascade-rules');
  expect(reference.columns, 'the reference doc itself').toBeGreaterThan(1);

  await openTradingSection(page, 'cascade');
  for (const [tab, id] of [
    ['#oc-tabbtn-fib', 'oc-fib-info'],
    ['#oc-tabbtn-candle', 'oc-candle-info'],
    ['#oc-tabbtn-recovery', 'oc-high-info'],
    ['#oc-tabbtn-gapcarry', 'oc-gap-info'],
    ['#oc-tabbtn-supertrend', 'oc-st-info'],
  ]) {
    await page.click(tab);
    await page.click(`[data-pf-info="${id}"]`);
    const shape = await docShape(page, id);
    // More than one column is the whole ask: it is what makes a long doc
    // readable across a wide screen instead of a ribbon down one side.
    expect(shape.columns, `${id} columns`).toBeGreaterThan(1);
    // And it uses the card it is in. A doc half the width of its own card is
    // the capped layout Phil was looking at.
    expect(shape.width / shape.hostWidth, `${id} fills its card`).toBeGreaterThan(0.9);
    // The bilingual header is part of the format.
    await expect(page.locator(`#${id} .pf-doc-lang-btn`)).toHaveCount(2);
  }

  // One column on a phone, and never a sideways scroll.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.click('#oc-tabbtn-fib');
  const phone = await docShape(page, 'oc-fib-info');
  expect(phone.columns, 'a phone gets one column').toBe(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);

  expect(jsErrors).toEqual([]);
});

/* Where a .pf-info-doc's sections actually landed. Columns are counted by
 * distinct left edges rather than read off the CSS: `columns: 34em auto` is a
 * request, and how many the browser grants is the thing being asserted. */
async function docShape(page: Page, id: string) {
  return page.evaluate((docId) => {
    const doc = document.getElementById(docId)!;
    const sections = Array.from(doc.querySelectorAll('.pf-doc-lang.is-active > section'));
    const lefts = new Set(sections.map((s) => Math.round(s.getBoundingClientRect().left)));
    return {
      width: Math.round(doc.getBoundingClientRect().width),
      hostWidth: Math.round((doc.parentElement as HTMLElement).getBoundingClientRect().width),
      columns: lefts.size,
      sections: sections.length,
    };
  }, id);
}

test('Fib Boundary tab renders the swing-ladder controls', async ({ page }) => {
  const jsErrors: string[] = [];
  page.on('pageerror', (err) => jsErrors.push(String(err)));

  await login(page);
  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-fib');
  // Monitors are collapsed by default (Phil, 2026-08-13); unfold to inspect.
  await page.waitForSelector('#oc-fib-rows details');
  await page.evaluate(() => {
    document.querySelectorAll('#oc-fib-rows details').forEach((d) => { (d as HTMLDetailsElement).open = true; });
  });

  await expect(page.locator('#oc-tab-fib')).toBeVisible();

  // The rule switches live in the Advanced fold now, as they do on every other
  // strategy panel (Phil, 2026-08-26: uniformity across all four) -- open it
  // before touching them.
  await page.evaluate(() => { const d = document.getElementById('oc-fib-advanced') as HTMLDetailsElement; if (d) d.open = true; });

  // All five instruments Phil asked for, NIFTY first.
  await expect(page.locator('#oc-fib-symbol option')).toHaveCount(5);
  await expect(page.locator('#oc-fib-symbol')).toHaveValue('NIFTY');
  await expect(page.locator('#oc-fib-side option')).toHaveCount(2);
  await expect(page.locator('#oc-fib-side')).toContainText('Buy CE');
  await expect(page.locator('#oc-fib-side')).toContainText('Buy PE');

  // The old per-rung budget is gone; the ladder cap replaces it.
  await expect(page.locator('#oc-fib-capital-cap')).toHaveValue('75000');
  await expect(page.locator('#oc-fib-rung-inr')).toHaveCount(0);
  await expect(page.locator('#oc-fib-levels-hint')).toContainText('L16');

  // The session rule, and it is what gets SENT. Phil, 2026-08-16: intraday
  // close at 3:15, with the carry-on rule still available beside it.
  await expect(page.locator('#oc-fib-session')).toHaveValue('intraday');
  await expect(page.locator('#oc-fib-levels-hint')).toContainText('OUT 3:15');
  await page.click('#oc-fib-session-toggle [data-value="normal"]');
  await expect(page.locator('#oc-fib-session')).toHaveValue('normal');
  await expect(page.locator('#oc-fib-levels-hint')).toContainText('CARRIES');
  await page.click('#oc-fib-session-toggle [data-value="intraday"]');

  // Every chart a mother may be read on. Entries stay 1m whichever is picked.
  // A button row, not a dropdown -- all four charts visible at once.
  await expect(page.locator('#oc-fib-timeframe .oc-fib-tf')).toHaveCount(4);
  await expect(page.locator('#oc-fib-timeframe')).toHaveAttribute('data-value', '1m');
  await page.click('#oc-fib-timeframe .oc-fib-tf[data-tf="15m"]');
  await expect(page.locator('#oc-fib-timeframe')).toHaveAttribute('data-value', '15m');
  await expect(page.locator('#oc-fib-timeframe .oc-fib-tf[data-tf="15m"]')).toHaveClass(/is-active/);
  await page.click('#oc-fib-timeframe .oc-fib-tf[data-tf="1m"]');

  // Paper is what you get by default, and Mode is now the Scalp page's plain
  // toggle -- Phil asked for that on 2026-08-15, with no arming step and no
  // password + authenticator behind it. The server still refuses real orders
  // until its broker lifecycle is verified; that gate is not this control.
  await expect(page.locator('#oc-fib-mode')).toHaveValue('paper');
  await expect(page.locator('#oc-fib-mode-note')).toContainText('sends nothing');
  await expect(page.locator('#oc-fib-mode-toggle .scalp-toggle-btn[data-value="paper"]')).toHaveClass(/active/);
  await expect(page.locator('#oc-fib-mode-toggle .scalp-toggle-btn[data-value="live"]')).not.toHaveClass(/active/);
  // It really toggles, both ways, and the hidden field follows it.
  await page.click('#oc-fib-mode-toggle .scalp-toggle-btn[data-value="live"]');
  await expect(page.locator('#oc-fib-mode')).toHaveValue('live');
  await expect(page.locator('#oc-fib-mode-toggle .scalp-toggle-btn[data-value="live"]')).toHaveClass(/active/);
  await page.click('#oc-fib-mode-toggle .scalp-toggle-btn[data-value="paper"]');
  await expect(page.locator('#oc-fib-mode')).toHaveValue('paper');
  // The separate "Arm live" control is gone with the gate it existed for.
  await expect(page.locator('[data-fx="arm"]')).toHaveCount(0);

  // THE NOTES. This doc is the only place the rules are written down for Phil,
  // so it has to describe the strategy that is actually running -- it spent a
  // day describing the swing ladder the merge replaced.
  await page.click('[data-pf-info="oc-fib-info"]');
  const doc = page.locator('#oc-fib-info .pf-doc-lang[data-pf-lang="en"]');
  await expect(doc).toBeVisible();
  await expect(doc).toContainText('one strategy now');
  await expect(doc).toContainText('It never moves');            // the mother is not rebased
  await expect(doc).toContainText('cuts back below that low');  // how a fib is drawn
  await expect(doc).toContainText('Fibs STACK');
  await expect(doc).toContainText('A touch COLLECTS');   // the 2026-08-16 rule
  await expect(doc).toContainText('GAPPED');
  await expect(doc).toContainText("up to the mother's high");   // what the target is measured to
  await expect(doc).toContainText('rests on the broker');
  await expect(doc).toContainText('new deepest low');
  await expect(doc).toContainText('No stop loss');
  // And nothing from the geometry it replaced.
  await expect(doc).not.toContainText('first involvement');
  await expect(doc).not.toContainText('authenticator code');
  // Both languages, and the Tamil half is a real translation, not a stub.
  await page.click('#oc-fib-info [data-pf-doc-lang="ta"]');
  const ta = page.locator('#oc-fib-info .pf-doc-lang[data-pf-lang="ta"]');
  await expect(ta).toBeVisible();
  await expect(ta).toContainText('ஒரே strategy');
  await expect(ta).toContainText('broker-இல் தங்கும்');
  expect((await ta.innerText()).length).toBeGreaterThan(1500);
  await page.click('#oc-fib-info [data-pf-doc-lang="en"]');
  await page.click('[data-pf-info="oc-fib-info"]');

  // A symbol whose weeklies NSE withdrew must say so, or the user believes
  // they are getting a weekly contract that does not exist.
  await page.selectOption('#oc-fib-symbol', 'BANKNIFTY');
  await expect(page.locator('#oc-fib-symbol-note')).toContainText('Monthly expiries only');
  await page.selectOption('#oc-fib-symbol', 'SENSEX');
  await expect(page.locator('#oc-fib-symbol-note')).toContainText('Thin book');
  await page.selectOption('#oc-fib-symbol', 'NIFTY');

  // The monitor renders from a not_started payload rather than staying blank.
  await expect(page.locator('#oc-fib-rows [data-fx="badge"]')).toHaveText('IDLE');
  await expect(page.locator('#oc-fib-start')).toBeVisible();
  await expect(page.locator('#oc-fib-rows [data-fx="kill"]')).toBeHidden();

  // Nothing is parked any more: the Backtest replays the SAME ladder Start
  // trades, so both are live and the parked note is gone.
  await expect(page.locator('#oc-fib-rows [data-fx="chart"]')).toBeVisible();
  await expect(page.locator('#oc-fib-backtest-btn')).toBeVisible();
  await expect(page.locator('#oc-fib-parked-note')).toHaveCount(0);

  expect(jsErrors).toEqual([]);
});

test('Fib Boundary remembers Auto mother mode after a page refresh', async ({ page }) => {
  await login(page);
  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-fib');

  const auto = page.locator('#oc-fib-mother-mode-toggle [data-value="auto"]');
  const manual = page.locator('#oc-fib-mother-mode-toggle [data-value="manual"]');
  await auto.click();
  await expect(page.locator('#oc-fib-mother-mode')).toHaveValue('auto');
  await expect(auto).toHaveAttribute('aria-checked', 'true');
  await expect(manual).toHaveAttribute('aria-checked', 'false');
  await expect(page.locator('#oc-fib-mother-manual-row')).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem('philforge_fib_boundary_mother_mode_v1'))).toBe('auto');

  await page.reload();
  await page.waitForFunction(() => document.documentElement.getAttribute('data-nav-ready') === '1');
  await expect(page.locator('#options-cascade-page')).toHaveClass(/active-page/);
  await expect(page.locator('#oc-tab-fib')).toBeVisible();
  await expect(page.locator('#oc-fib-mother-mode')).toHaveValue('auto');
  await expect(auto).toHaveAttribute('aria-checked', 'true');
  await expect(manual).toHaveAttribute('aria-checked', 'false');
  await expect(page.locator('#oc-fib-mother-manual-row')).toBeHidden();
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
  // TWO stacked fibs. Since the merge (2026-08-15) a new structure ADDS its
  // levels and the old ones keep resting, so a rung is (fib, level): F1 and F2
  // both have an L2 and they are different prices holding different money.
  fibs: [
    {
      fib_id: 1, trendline_id: 1, fib0: 24700, fib1: 24600, span: 100,
      touch_timestamp: '2026-08-06T09:16:00+05:30', drawn_timestamp: '2026-08-06T09:23:00+05:30',
      levels: [{ level: 2, price: 24500 }, { level: 3, price: 24400 }, { level: 4, price: 24300 }, { level: 6, price: 24100 }],
    },
    {
      fib_id: 2, trendline_id: 1, fib0: 24680, fib1: 24560, span: 120,
      touch_timestamp: '2026-08-06T09:30:00+05:30', drawn_timestamp: '2026-08-06T09:35:00+05:30',
      levels: [{ level: 2, price: 24440 }, { level: 3, price: 24320 }, { level: 4, price: 24200 }, { level: 6, price: 23960 }],
    },
  ],
  trendlines: [
    { id: 1, a1: { t: '2026-08-06T09:21:00+05:30', p: 24624 }, a2: { t: '2026-08-06T09:19:00+05:30', p: 24662 }, active: true },
  ],
  mother_high: 24624, mother_low: 24600,
  levels: [
    { level: 2, fib_id: 1, key: 'F1L2', index_price: 24500, status: 'FILLED', filled_at: '2026-08-06T09:30:00+05:30' },
    { level: 3, fib_id: 1, key: 'F1L3', index_price: 24400, status: 'FILLED', filled_at: '2026-08-06T09:35:00+05:30' },
    { level: 4, fib_id: 1, key: 'F1L4', index_price: 24300, status: 'PENDING', filled_at: null },
    { level: 6, fib_id: 1, key: 'F1L6', index_price: 24100, status: 'UNFUNDED', filled_at: null },
    { level: 2, fib_id: 2, key: 'F2L2', index_price: 24440, status: 'PENDING', filled_at: null },
    { level: 3, fib_id: 2, key: 'F2L3', index_price: 24320, status: 'PENDING', filled_at: null },
  ],
  fills: [
    { buy_number: 1, level: 2, fib_id: 1, rung_key: 'F1L2', timestamp: '2026-08-06T09:30:00+05:30', index_price: 24500, premium: 200, lots: 1, quantity: 65, strike: 24400, expiry: '2026-08-11', option_type: 'CE', funded_inr: 13000 },
    { buy_number: 2, level: 3, fib_id: 1, rung_key: 'F1L3', timestamp: '2026-08-06T09:35:00+05:30', index_price: 24400, premium: 180, lots: 1, quantity: 65, strike: 24300, expiry: '2026-08-11', option_type: 'CE', funded_inr: 11700 },
  ],
  // The target is a real order sitting on the broker, not something the app
  // notices and then chases with a market sell.
  resting_exits: [
    { order_id: 'PF1', rung_key: 'F1L2', strike: 24400, expiry: '2026-08-11', option_type: 'CE', quantity: 65, price: 236 },
    { order_id: 'PF2', rung_key: 'F1L3', strike: 24300, expiry: '2026-08-11', option_type: 'CE', quantity: 65, price: 213 },
  ],
  rounds: [], rearm_below: null,
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
    // Deep enough that L6 (24,100) sits inside the fitted price range with
    // only the campaign's own bars on the chart (the two bars before the
    // mother are no longer drawn).
    { t: '2026-08-06T09:35:00+05:30', o: 24510, h: 24512, l: 24145, c: 24160, is_mother: false },
  ],
  anchor: fibTouchCampaign.anchor,
  // The route hands the chart the STRUCTURES the ladder drew, not one swing.
  fibs: fibTouchCampaign.fibs,
  trendlines: fibTouchCampaign.trendlines,
  mother_high: fibTouchCampaign.mother_high,
  mother_low: fibTouchCampaign.mother_low,
  levels: [
    { level: 2, fib_id: 1, key: 'F1L2', price: 24500 }, { level: 3, fib_id: 1, key: 'F1L3', price: 24400 },
    { level: 4, fib_id: 1, key: 'F1L4', price: 24300 }, { level: 6, fib_id: 1, key: 'F1L6', price: 24100 },
    { level: 2, fib_id: 2, key: 'F2L2', price: 24440 }, { level: 3, fib_id: 2, key: 'F2L3', price: 24320 },
  ],
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

  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-fib');
  // Monitors are collapsed by default (Phil, 2026-08-13); unfold to inspect.
  await page.waitForSelector('#oc-fib-rows details');
  await page.evaluate(() => {
    document.querySelectorAll('#oc-fib-rows details').forEach((d) => { (d as HTMLDetailsElement).open = true; });
  });

  // The monitor renders the running ladder before the chart is even opened.
  await expect(page.locator('#oc-fib-rows [data-fx="badge"]')).toHaveText('OPEN');
  await expect(page.locator('#oc-fib-rows [data-fx="fills"] tr')).toHaveCount(2);
  // Start is fail-closed for the selected instrument while its ladder runs,
  // and the button names exactly what must happen first.
  await expect(page.locator('#oc-fib-start')).toBeDisabled();
  await expect(page.locator('#oc-fib-start')).toContainText('Running · NIFTY');
  // Which ladder is blocking is a TABLE, not a sentence that read as a riddle.
  await expect(page.locator('#oc-fib-blocked table')).toBeVisible();
  await expect(page.locator('#oc-fib-blocked')).toContainText('NIFTY');
  await expect(page.locator('#oc-fib-blocked')).toContainText('CE · 15M mother');
  await expect(page.locator('#oc-fib-blocked')).toContainText('1 ladder · one per instrument');
  // The anchor block is a table now, labelled fib high/low, and it no longer
  // spells out the involvement rule.
  await expect(page.locator('#oc-fib-rows [data-fx="anchor"] table')).toBeVisible();
  await expect(page.locator('#oc-fib-rows [data-fx="anchor"]')).toContainText('Fib high');
  await expect(page.locator('#oc-fib-rows [data-fx="anchor"]')).toContainText('Fib low');
  await expect(page.locator('#oc-fib-rows [data-fx="anchor"]')).toContainText('24,700');
  await expect(page.locator('#oc-fib-rows [data-fx="anchor"]')).not.toContainText('consecutive candles');
  await expect(page.locator('#oc-fib-rows [data-fx="summary"]')).toContainText('₹24,700');
  // The strip names the mother's chart and the mode it is running in.
  await expect(page.locator('#oc-fib-rows [data-fx="gist"]')).toContainText('15M mother, 1m entries');
  await expect(page.locator('#oc-fib-rows [data-fx="gist"]')).toContainText('PAPER');

  await page.click('#oc-fib-rows [data-fx="chart"]');
  await page.waitForSelector('#pf-bench-canvas-main', { timeout: 10_000 });

  const paint = await page.evaluate(() => {
    const app = window as typeof window & { _pfChartCanvas?: { paint?: Record<string, unknown> } };
    if (!app._pfChartCanvas || !app._pfChartCanvas.paint) throw new Error('The fib-boundary canvas never painted');
    return app._pfChartCanvas.paint;
  });

  // Six candles in, FOUR drawn: the chart keeps the campaign's own life --
  // from the mother candle to its end -- and the two bars before the mother
  // are outside it (Phil, 2026-08-18: "only the trade live times"). A
  // translator that dropped the native-price fallback would render none.
  expect(paint).toMatchObject({ candles: 4 });
  const labels = paint.labelTexts as string[];
  // BOTH structures are on the chart. Fibs stack since the merge, and a chart
  // that draws one of them shows prices that are not the ones holding money.
  // EVERY fib draws its own 0 and 1 -- two structures, so two of each.
  expect(labels.filter((t) => t.startsWith('0 (')).length).toBe(2);
  expect(labels.filter((t) => t.startsWith('1 (')).length).toBe(2);
  // and no "F1"/"F2" prefixes: the colour says which structure it is.
  expect(labels.some((t) => /^F\d/.test(t))).toBe(false);
  // Only the mother edge the ladder works against -- a CE draws its HIGH and
  // never its low, which is the whole of Phil's 2026-08-06 correction.
  expect(labels.some((t) => t.startsWith('MOTHER'))).toBe(true);
  expect(labels.some((t) => t.includes('MOTHER LOW'))).toBe(false);
  // The trendline is drawn even though it gates nothing here.
  expect(paint).toMatchObject({ trendlines: 1 });
  // Each rung carries its own live state and names the FIB it belongs to --
  // two of them are "level 2" at different prices.
  expect(labels.some((t) => t.startsWith('2 filled'))).toBe(true);
  expect(labels.some((t) => t.startsWith('4 ('))).toBe(true);
  expect(labels.some((t) => t.startsWith('6 unfunded'))).toBe(true);
  expect(labels.filter((t) => t.startsWith('2 ')).length).toBeGreaterThan(1);
  // A ladder still holding must not claim it sold at the target -- and when the
  // target is a real order on the broker, the line says so.
  expect(labels.some((t) => t.includes('TARGET · 2 resting'))).toBe(true);

  // NO DASHED OR DOTTED LINES. Phil has asked for this more times than it
  // should have taken; the payload is the one place it can be proven.
  const dashes = await page.evaluate(() => {
    const app = window as typeof window & { _pfChartCanvas?: { data?: { lines?: { dash?: number[] }[]; tp_price?: number | null; avg_entry_price?: number | null } } };
    const d = app._pfChartCanvas?.data || {};
    return {
      dashed: (d.lines || []).filter((l) => Array.isArray(l.dash) && l.dash.length).length,
      tp: d.tp_price, avg: d.avg_entry_price,
    };
  });
  expect(dashes.dashed).toBe(0);
  // and the two the shared renderer would draw dashed are drawn by us instead
  expect(dashes.tp).toBeNull();
  expect(dashes.avg).toBeNull();

  // THE TOOLBAR SITS ON ONE LINE. .pf-chart-strip was built as a row of its
  // own above a chart, so it carries margin-bottom:10px; nested in the
  // actions row, align-items:center centres its MARGIN box and every control
  // it holds rides 5px above the close beside it (Phil, 2026-08-28: "The
  // alignment is missing in all the charts"). Measured, because -5px is
  // invisible to a selector and obvious on a screen.
  const rowOffset = await page.evaluate(() => {
    const overlay = document.getElementById('oc-fib-chart-overlay');
    const inStrip = overlay?.querySelector('#oc-fib-chart-strip button');
    const close = overlay?.querySelector('button[aria-label="Close chart"]');
    if (!inStrip || !close) return null;
    const a = inStrip.getBoundingClientRect(), b = close.getBoundingClientRect();
    return (a.top + a.height / 2) - (b.top + b.height / 2);
  });
  expect(rowOffset, 'the chart strip rendered no controls to measure').not.toBeNull();
  expect(Math.abs(rowOffset as number)).toBeLessThanOrEqual(1);

  // ONE close, and only one (Phil, 2026-08-27: "Why two X buttons?"). The
  // toolbar carries the ✕ in its own markup so it is there even when the chart
  // fails and the strip never renders; the strip is given no onClose, so it
  // draws none of its own. Count it, or the pair comes back unnoticed.
  await expect(page.locator('#oc-fib-chart-overlay button[aria-label="Close chart"]:visible')).toHaveCount(1);
  await page.click('#oc-fib-chart-overlay button[aria-label="Close chart"]:visible');
  await expect(page.locator('#pf-bench-canvas-main')).toHaveCount(0);

  expect(jsErrors).toEqual([]);
});

// The same ladder in the OTHER half of the merged strategy: a rung is then a
// SPACE between two fibs' levels, and both of its edges have to be drawable or
// the space cannot be seen at all.
// A mother that BANKED a round and parked, which is where the screen used to
// lose the trade: `fills` is emptied when a round pays out, so a campaign that
// had bought six times and made ₹60,413 rendered "No buy yet." over three
// zeroed tiles. Found by replaying a real 22-Jul-2026 NIFTY mother.
const fibParkedCampaign = {
  ...fibTouchCampaign,
  status: 'WAITING_NEW_LOW',
  fills: [],
  resting_exits: [],
  // Parking RELEASES the rungs: they are waiting for a fresh touch below the
  // campaign's deepest low, which is exactly why last round's rupees must not
  // still be printed on them.
  levels: fibTouchCampaign.levels.map((row) => ({ ...row, status: 'PENDING', filled_at: null })),
  open_lots: 0, open_quantity: 0, deployed_inr: 0, remaining_inr: 75000,
  average_index_entry: null, average_premium: null, target_index: null,
  rearm_below: 24395,
  gross_pnl: 61000, costs_total: 587, net_pnl: 60413.05,
  rounds: [{
    round: 1,
    gross_pnl: 61000, costs_total: 587, net_pnl: 60413.05,
    exit_timestamp: '2026-07-27T11:31:00+05:30', exit_index: 23971.6, exit_reason: 'target',
    rung_keys: ['F1L2', 'F1L3'], deployed_inr: 13542.75,
    fills: [
      { buy_number: 1, level: 2, fib_id: 1, rung_key: 'F1L2', timestamp: '2026-07-24T09:15:00+05:30', index_price: 23898, premium: 93.45, exit_premium: 199.95, lots: 1, quantity: 65, strike: 23800, expiry: '2026-07-28', option_type: 'CE', funded_inr: 6074.25 },
      { buy_number: 2, level: 3, fib_id: 1, rung_key: 'F1L3', timestamp: '2026-07-24T09:16:00+05:30', index_price: 23834.6, premium: 114.9, exit_premium: 244.25, lots: 1, quantity: 65, strike: 23750, expiry: '2026-07-28', option_type: 'CE', funded_inr: 7468.5 },
    ],
  }],
};

test('A banked round stays on the screen after the mother parks', async ({ page }) => {
  const jsErrors: string[] = [];
  page.on('pageerror', (err) => jsErrors.push(String(err)));

  await login(page);
  await page.route('**/api/fib-boundary/paper/status**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok', mode: 'paper', campaigns: [fibParkedCampaign] }) }));
  await page.route('**/api/fib-boundary/paper/chart**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fibTouchChart) }));

  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-fib');
  await page.waitForSelector('#oc-fib-rows details');
  await page.evaluate(() => {
    document.querySelectorAll('#oc-fib-rows details').forEach((d) => { (d as HTMLDetailsElement).open = true; });
  });

  // Every buy the mother made, still listed, tagged with the round that sold it
  // and showing what each leg went out at.
  await expect(page.locator('#oc-fib-rows [data-fx="fills"] tr')).toHaveCount(2);
  await expect(page.locator('#oc-fib-rows [data-fx="fills"]')).toContainText('F1L2');
  await expect(page.locator('#oc-fib-rows [data-fx="fills"]')).toContainText('₹93.45 → ₹199.95');
  await expect(page.locator('#oc-fib-rows [data-fx="fills"]')).not.toContainText('No buy yet');
  await expect(page.locator('#oc-fib-rows [data-fx="fill-summary"]')).toContainText('1 round banked');
  await expect(page.locator('#oc-fib-rows [data-fx="fill-summary"]')).toContainText('60,413');

  // A flat basket has no target and no average; the tiles say what it made and
  // what it is waiting for instead of printing 0.00 three times.
  const summary = page.locator('#oc-fib-rows [data-fx="summary"]');
  await expect(summary).toContainText('Banked');
  await expect(summary).toContainText('60,413');
  await expect(summary).toContainText('Re-arms below');
  await expect(summary).toContainText('24,395');
  await expect(summary).not.toContainText('Index target');

  // And on the chart: the money that was sold is NOT still sitting on a rung
  // that is waiting to be bought again.
  await page.click('#oc-fib-rows [data-fx="chart"]');
  await page.waitForSelector('#pf-bench-canvas-main', { timeout: 10_000 });
  const labels = await page.evaluate(() => {
    const app = window as typeof window & { _pfChartCanvas?: { paint?: { labelTexts?: string[] } } };
    return app._pfChartCanvas?.paint?.labelTexts || [];
  });
  expect(labels.some((t) => t.startsWith('2 ('))).toBe(true);
  expect(labels.some((t) => t.startsWith('2 (') && t.includes('₹'))).toBe(false);
  // What the round made is on its own sell mark, and the low that wakes the
  // mother is drawn.
  expect(labels.some((t) => t.includes('RE-ARM LOW'))).toBe(true);
  await page.click('#oc-fib-chart-overlay button[aria-label="Close chart"]:visible');

  expect(jsErrors).toEqual([]);
});

const fibConvergenceCampaign = {
  ...fibTouchCampaign,
  buy_mode: 'convergence',
  levels: [
    {
      level: 2, fib_id: 1, key: 'Z1-2:2-4', index_price: 24500, status: 'FILLED',
      filled_at: '2026-08-06T09:30:00+05:30', zone_floor: 24440, zone_label: '2-4', zone_bottom_fib_id: 2,
    },
    {
      level: 4, fib_id: 1, key: 'Z1-2:4-8', index_price: 24300, status: 'PENDING',
      filled_at: null, zone_floor: 24200, zone_label: '4-8', zone_bottom_fib_id: 2,
    },
  ],
  fills: [
    { buy_number: 1, level: 2, fib_id: 1, rung_key: 'Z1-2:2-4', timestamp: '2026-08-06T09:30:00+05:30', index_price: 24500, premium: 200, lots: 1, quantity: 65, strike: 24400, expiry: '2026-08-11', option_type: 'CE', funded_inr: 13000 },
  ],
  resting_exits: [],
};

test('The merge switch reaches the engine, and convergence draws its spaces', async ({ page }) => {
  const jsErrors: string[] = [];
  page.on('pageerror', (err) => jsErrors.push(String(err)));

  await login(page);
  // Start with NOTHING running: a live NIFTY ladder disables the whole form,
  // which is the correct behaviour and the wrong state to test a switch in.
  await page.route('**/api/fib-boundary/paper/status**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok', mode: 'paper', campaigns: [] }) }));
  await page.route('**/api/fib-boundary/paper/chart**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fibTouchChart) }));

  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-fib');

  // Fib Boundary and Fib Space became ONE engine on 2026-08-15. The switch was
  // built into it and wired to nothing -- the page could only ever start one of
  // the two halves.
  // The rule switches live in the Advanced fold now, as they do on every other
  // strategy panel (Phil, 2026-08-26: uniformity across all four) -- open it
  // before touching them.
  await page.evaluate(() => { const d = document.getElementById('oc-fib-advanced') as HTMLDetailsElement; if (d) d.open = true; });
  await expect(page.locator('#oc-fib-buy-mode')).toHaveValue('levels');
  await expect(page.locator('#oc-fib-levels-hint')).toContainText('L2·L3·L4·L6·L8·L12·L16');
  await page.click('#oc-fib-buy-mode-toggle [data-value="convergence"]');
  await expect(page.locator('#oc-fib-buy-mode')).toHaveValue('convergence');
  // A hidden input fires no change event, so the hint proves the handler ran.
  await expect(page.locator('#oc-fib-levels-hint')).toContainText('ZONES · L1·L2·L4·L8');
  await page.click('#oc-fib-buy-mode-toggle [data-value="levels"]');
  await expect(page.locator('#oc-fib-buy-mode')).toHaveValue('levels');

  // And it is what gets SENT -- a switch the server never hears about is a
  // switch that does nothing.
  await page.click('#oc-fib-buy-mode-toggle [data-value="convergence"]');
  let sent: Record<string, unknown> | null = null;
  await page.route('**/api/fib-boundary/paper/start', async (route) => {
    sent = route.request().postDataJSON();
    await route.fulfill({ json: { status: 'started', campaign: fibConvergenceCampaign } });
  });
  // The field is readonly by design -- it is driven by the site's own calendar
  // popup -- so the value is set the way that popup sets it.
  await page.evaluate(() => {
    const input = document.getElementById('oc-fib-mother-timestamp') as HTMLInputElement;
    input.value = '2026-08-06T09:21';
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.click('#oc-fib-start');
  await expect.poll(() => sent && sent.buy_mode).toBe('convergence');

  // The chart: a space is two lines, the price it fills at and how deep it may
  // still be worked. One line alone is not a space.
  await page.route('**/api/fib-boundary/paper/status**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok', mode: 'paper', campaigns: [fibConvergenceCampaign] }) }));
  await page.evaluate(() => (window as typeof window & { refreshFibBoundaryStatus: () => Promise<void> }).refreshFibBoundaryStatus());
  await page.waitForSelector('#oc-fib-rows details');
  await page.evaluate(() => {
    document.querySelectorAll('#oc-fib-rows details').forEach((d) => { (d as HTMLDetailsElement).open = true; });
  });
  await page.click('#oc-fib-rows [data-fx="chart"]');
  await page.waitForSelector('#pf-bench-canvas-main', { timeout: 10_000 });
  const labels = await page.evaluate(() => {
    const app = window as typeof window & { _pfChartCanvas?: { paint?: { labelTexts?: string[] } } };
    if (!app._pfChartCanvas?.paint) throw new Error('The convergence canvas never painted');
    return app._pfChartCanvas.paint.labelTexts || [];
  });
  expect(labels.some((t) => t.includes('2-4 zone filled'))).toBe(true);
  expect(labels.some((t) => t.includes('2-4 floor'))).toBe(true);
  expect(labels.some((t) => t.includes('4-8 zone'))).toBe(true);

  // ONE LABEL PER PRICE. A zone sits on its own fib's level, and the route
  // sends zones too -- three labels were stacking on one line (Phil,
  // 2026-08-17: "Why too much of labels, L4 floor? twice?").
  const prices = await page.evaluate(() => {
    const app = window as typeof window & { _pfChartCanvas?: { data?: { lines?: { price: number }[] } } };
    return (app._pfChartCanvas?.data?.lines || []).map((l) => l.price.toFixed(2));
  });
  expect(prices.length).toBe(new Set(prices).size);
  // and no "L4" spelling survives -- levels are numbers here
  expect(labels.some((t) => /\bL\d/.test(t))).toBe(false);

  await page.click('#oc-fib-chart-overlay button[aria-label="Close chart"]:visible');
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
  // ...but it must not MOVE. Trading now owns Equity, Scalp, and Cascade as
  // sections, so Insights follows the consolidated Trading entry at order 4.
  const order = await page.evaluate(() =>
    getComputedStyle(document.getElementById('nav-insights')!).order);
  expect(order).toBe('4');
  // The panels' actions wear the app's button, not their own skin.
  await expect(page.locator('#insights-study a.btn').first()).toBeVisible();
  await expect(page.locator('#insights-page .app-btn')).toHaveCount(0);

  expect(jsErrors).toEqual([]);
});

test('Recovery tab renders its controls and monitor', async ({ page }) => {
  const jsErrors: string[] = [];
  page.on('pageerror', (err) => jsErrors.push(String(err)));

  await login(page);
  await openTradingSection(page, 'cascade');
  await page.click('#oc-tabbtn-recovery');

  await expect(page.locator('#oc-tab-recovery')).toBeVisible();

  // Every timeframe the engine supports, with the measured setting selected. 5m
  // replaced 1H as the default in 63fbd23: the five-year audit found it the only
  // chart that comes out green, and the option labels say so.
  await expect(page.locator('#oc-high-timeframe option')).toHaveCount(4);
  await expect(page.locator('#oc-high-timeframe')).toHaveValue('5m');
  await expect(page.locator('#oc-high-mode option')).toHaveCount(2);

  // The monitor must render from a not_started payload rather than staying blank
  // -- a JS typo here leaves an empty panel that a green Python run never catches.
  await expect(page.locator('#oc-high-badge')).toHaveText('IDLE');
  await expect(page.locator('#oc-high-rows')).toContainText('Not running');
  await expect(page.locator('#oc-high-start')).toBeVisible();
  await expect(page.locator('#oc-high-stop')).toBeHidden();

  // The paper-only promise is still stated where the trader can read it. The
  // slogan banner came off the tab on 2026-08-20 ("Still more texts"); the
  // hero's gate chip is what carries it now.
  await expect(page.locator('#options-cascade-page')).toContainText('LIVE LOCKED');
  await expect(page.locator('#options-cascade-page')).toContainText('Paper validation required');

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

test('A ResizeObserver notice does not put a crash screen over a working app', async ({ page }) => {
  await login(page);

  // The browser raises this when a resize callback changes layout, so the batch
  // could not finish delivering in one frame. It retries on the next; nothing
  // is broken and there is no stack to act on. But it arrives at window.onerror
  // like any other error, and it used to drop a full-page "Something went
  // wrong" over a working app the moment a chart opened — which is the actual
  // damage, since the app underneath was fine.
  const dispatched = await page.evaluate(() => {
    const before = !!document.getElementById('_af-crash-screen');
    window.dispatchEvent(
      new ErrorEvent('error', { message: 'ResizeObserver loop completed with undelivered notifications' })
    );
    window.dispatchEvent(new ErrorEvent('error', { message: 'ResizeObserver loop limit exceeded' }));
    return before;
  });
  expect(dispatched, 'a crash screen was already up before the test dispatched anything').toBe(false);
  await expect(page.locator('#_af-crash-screen')).toHaveCount(0);

  // And the screen still appears for something that IS a crash — suppressing
  // the notice must not have disarmed the handler.
  await page.evaluate(() => {
    window.dispatchEvent(new ErrorEvent('error', { message: 'TypeError: genuinely broken' }));
  });
  await expect(page.locator('#_af-crash-screen')).toHaveCount(1);
});

test('The chart dialog cannot flicker its own canvas', async ({ page }) => {
  await login(page);

  // The flicker: the chart dialog scrolls, and the canvas host inside it is
  // width:100% with an aspect-ratio, so its HEIGHT follows its WIDTH. A
  // scrollbar appearing narrows the content -> the chart shortens -> the
  // scrollbar goes -> it widens again. That loop repaints every frame, and is
  // both what "the chart flickers" was and what raised the ResizeObserver
  // notice that put a crash screen over the app.
  //
  // Reserving the gutter breaks the feedback at the source: the content width
  // no longer depends on whether the scrollbar happens to be showing.
  const gutter = await page.evaluate(() => {
    const probe = document.createElement('div');
    probe.className = 'pf-cascade-chart-dialog';
    document.body.appendChild(probe);
    const value = getComputedStyle(probe).scrollbarGutter;
    probe.remove();
    return value;
  });
  expect(gutter, 'the chart dialog must reserve its scrollbar gutter').toContain('stable');

  // The second guard is a dead-band inside the resize routine, so a pixel of
  // layout wobble never reaches a redraw. It lives in the drawing file rather
  // than the DOM, so it is pinned where it can actually be exercised.
  const deadBand = await page.evaluate(async () => {
    const source = await fetch('/static/philforge-bench-chart.js').then(r => r.text());
    return /Math\.abs\(cssW - c\.w\) <= 2 && Math\.abs\(cssH - c\.h\) <= 2/.test(source);
  });
  expect(deadBand, 'the resize dead-band is gone — a 1px wobble will repaint again').toBe(true);
});
