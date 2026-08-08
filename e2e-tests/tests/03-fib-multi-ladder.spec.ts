/**
 * 03-fib-multi-ladder.spec.ts
 *
 * One ladder PER SYMBOL per user. The monitor used to be a single panel writing
 * to 42 fixed element ids, so "two campaigns" was not something it could show at
 * all. It is a cloned template now, one root per instrument, and these specs
 * hold that shape:
 *   - two symbols render two independent monitors, with their own anchors,
 *     fills and events;
 *   - the Kill and Arm buttons carry the symbol of the panel they sit in, so
 *     killing one ladder cannot close another;
 *   - a different instrument never blocks Start; only the selected one does.
 */

import { test, expect, Page } from '@playwright/test';

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';
const BASE_ORIGIN = new URL(process.env.E2E_BASE_URL || process.env.BASE_URL || 'http://localhost:8000').origin;

/** One campaign as /api/fib-boundary/paper/status reports it. */
function campaign(symbol: string, over: Record<string, unknown> = {}) {
  const base = symbol === 'SENSEX' ? 80_000 : 24_700;
  return {
    symbol,
    side: 'CE',
    timeframe: symbol === 'SENSEX' ? '5m' : '1m',
    entry_timeframe: '1m',
    mode: 'paper',
    is_live: false,
    armed: false,
    running: true,
    status: 'OPEN',
    mother_timestamp: '2026-08-06T09:15:00+05:30',
    anchor: {
      high: base,
      low: base - 100,
      span: 100,
      high_timestamp: '2026-08-06T09:21:00+05:30',
      low_timestamp: '2026-08-06T09:17:00+05:30',
      confirmed_at: '2026-08-06T09:23:00+05:30',
      involvement_candles: 2,
    },
    levels: [
      { key: 'L2', level: 2, index_price: base - 200, status: 'FILLED', filled_at: '2026-08-06T09:25:00+05:30' },
      { key: 'L3', level: 3, index_price: base - 300, status: 'PENDING', filled_at: null },
    ],
    fills: [{
      buy_number: 1, level: 2, timestamp: '2026-08-06T09:25:00+05:30',
      index_price: base - 200, strike: base - 250, option_type: 'CE',
      expiry: '2026-08-11', premium: 200, lots: 1, quantity: 65, funded_inr: 13_000,
    }],
    lot_size: 65, strike_step: 50, itm_steps: 2, min_dte: 4,
    capital_cap_inr: 75_000, deployed_inr: 13_000, remaining_inr: 62_000,
    open_lots: 1, open_quantity: 65,
    average_index_entry: base - 200, average_premium: 200,
    target_index: base - 175, target_fraction: 0.25,
    mother_high: base + 80, mother_low: base - 60,
    exit_timestamp: null, exit_reason: null, exit_index: null, exit_premiums: [],
    gross_pnl: 0, costs_total: 0, net_pnl: 0,
    events: [{ timestamp: '2026-08-06T09:25:00+05:30', event: `${symbol}_RUNG_FILLED`, level: 2 }],
    data_gaps: [],
    ...over,
  };
}

/** Offline shell + a fib status endpoint this test controls. */
async function login(page: Page, campaigns: () => unknown[]) {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (!['http:', 'https:'].includes(url.protocol) || url.origin === BASE_ORIGIN) { await route.fallback(); return; }
    await route.fulfill({ status: 204, body: '' });
  });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/health' || path.startsWith('/api/auth/')) { await route.continue(); return; }
    if (path === '/api/fib-boundary/paper/status') {
      const rows = campaigns();
      await route.fulfill({ json: { status: rows.length ? 'ok' : 'not_started', mode: 'paper', live_available: false, campaigns: rows } });
      return;
    }
    if (path === '/api/fib-boundary/symbols') { await route.continue(); return; }
    if (path === '/api/ticker') { await route.fulfill({ json: { status: 'ok', nifty: { price: 24700 } } }); return; }
    if (path === '/api/engine-control/status') { await route.fulfill({ json: { status: 'ok', any_running: false, users: [] } }); return; }
    // Everything else this page touches is irrelevant here; an empty envelope
    // keeps the console quiet without pretending to be a real answer.
    await route.fulfill({ json: { status: 'not_started', mode: 'paper', campaigns: [], rows: [], data: [], entries: [] } });
  });

  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const password = page.locator('#password-input');
  if (await password.isVisible()) { await password.fill(PIN); await page.click('#unlock-btn'); }
  else for (const digit of PIN.split('')) await page.click(`[data-val="${digit}"]`);
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
}

/**
 * Navigate the way a user does — the nav button, not showPage(). The page's
 * `data-pf-after-nav="initOptionsCascadePage"` is what binds the form listeners
 * and starts the poll; calling showPage directly skips all of it.
 */
async function openFibTab(page: Page) {
  await page.click('#nav-cascade');
  await page.click('#oc-tabbtn-fib');
  await page.waitForFunction(() => document.querySelectorAll('#fibx-monitors > *').length > 0, null, { timeout: 10_000 });
}

test.describe('Fib Boundary · one ladder per instrument', () => {
  test('two symbols get two monitors, each with its own anchor and fills', async ({ page }) => {
    await login(page, () => [campaign('NIFTY'), campaign('SENSEX')]);
    await openFibTab(page);

    const monitors = page.locator('#fibx-monitors > [data-fx-symbol]');
    await expect(monitors).toHaveCount(2);
    await expect(monitors.nth(0)).toHaveAttribute('data-fx-symbol', 'NIFTY');
    await expect(monitors.nth(1)).toHaveAttribute('data-fx-symbol', 'SENSEX');

    // Independent geometry, not one panel's numbers repeated.
    await expect(monitors.nth(0).locator('[data-fx="anchor"]')).toContainText('24,700');
    await expect(monitors.nth(1).locator('[data-fx="anchor"]')).toContainText('80,000');
    await expect(monitors.nth(0).locator('[data-fx="title"]')).toHaveText('NIFTY CE monitor');
    await expect(monitors.nth(1).locator('[data-fx="title"]')).toHaveText('SENSEX CE monitor');
    // Each panel's mother chart is its own, so the two gists differ.
    await expect(monitors.nth(0).locator('[data-fx="gist"]')).toContainText('1M mother');
    await expect(monitors.nth(1).locator('[data-fx="gist"]')).toContainText('5M mother');

    // And one results + events pair per ladder, with that ladder's events.
    const pairs = page.locator('#fibx-lower > [data-fx-symbol]');
    await expect(pairs).toHaveCount(2);
    await expect(pairs.nth(0).locator('[data-fx="events"]')).toContainText('NIFTY RUNG FILLED');
    await expect(pairs.nth(1).locator('[data-fx="events"]')).toContainText('SENSEX RUNG FILLED');
  });

  test('killing one ladder sends that symbol and leaves the other running', async ({ page }) => {
    const live = new Set(['NIFTY', 'SENSEX']);
    let killed = '';
    await login(page, () => [...live].map(s => campaign(s)));
    await page.route('**/api/fib-boundary/paper/kill*', async route => {
      killed = new URL(route.request().url()).searchParams.get('symbol') || '';
      live.delete(killed);
      await route.fulfill({ json: { status: 'killed', mode: 'paper', campaign: campaign(killed, { running: false, status: 'KILLED' }) } });
    });
    await openFibTab(page);

    await page.locator('[data-fx-symbol="SENSEX"] [data-fx="kill"]').first().click();
    await page.locator('#confirm-ok-btn').first().click();

    // The route was told WHICH ladder -- without the symbol the server would
    // have killed whatever it found first.
    await expect.poll(() => killed).toBe('SENSEX');
    const monitors = page.locator('#fibx-monitors > [data-fx-symbol]');
    await expect(monitors).toHaveCount(1);
    await expect(monitors.first()).toHaveAttribute('data-fx-symbol', 'NIFTY');
    await expect(page.locator('#fibx-lower > [data-fx-symbol]')).toHaveCount(1);
  });

  test('a safety-locked live ladder cannot be armed from the UI', async ({ page }) => {
    await login(page, () => ['NIFTY', 'SENSEX'].map(s => campaign(s, { mode: 'live', is_live: true, armed: false })));
    await openFibTab(page);

    await expect(page.locator('#fibx-monitors [data-fx="arm"]:visible')).toHaveCount(0);
    await expect(page.locator('#options-cascade-live-gate')).toContainText('LIVE SAFETY LOCKED');
  });

  test('a safety-locked live ladder directs exits to Dhan without changing PhilForge state', async ({ page }) => {
    await login(page, () => ['NIFTY', 'SENSEX'].map(s => campaign(s, { mode: 'live', is_live: true, armed: false })));
    await openFibTab(page);

    await page.locator('[data-fx-symbol="SENSEX"] [data-fx="kill"]').first().click();
    await expect(page.locator('#fibx-form-status')).toContainText('manage any real position in Dhan');
    await expect(page.locator('#fibx-monitors > [data-fx-symbol]')).toHaveCount(2);
  });

  test('a running ladder blocks Start only on its own instrument', async ({ page }) => {
    await login(page, () => [campaign('NIFTY')]);
    await openFibTab(page);

    // NIFTY is the default selection and it IS running.
    await expect(page.locator('#fibx-start')).toHaveText(/Kill the NIFTY ladder first/);
    await page.selectOption('#fibx-symbol', 'SENSEX');
    // A different instrument never blocked anything technically; now it does
    // not say it does either.
    await expect(page.locator('#fibx-start')).toHaveText(/Start fib-boundary paper/);
    await expect(page.locator('#fibx-blocked')).toContainText('Free — Start runs it alongside the others.');
    await expect(page.locator('#fibx-start')).toBeEnabled();
  });

  test('with nothing running the panel is a single IDLE monitor', async ({ page }) => {
    await login(page, () => []);
    await openFibTab(page);
    const monitors = page.locator('#fibx-monitors > [data-fx-symbol]');
    await expect(monitors).toHaveCount(1);
    await expect(monitors.first()).toHaveAttribute('data-fx-symbol', '');
    await expect(monitors.first().locator('[data-fx="badge"]')).toHaveText('IDLE');
    await expect(monitors.first().locator('[data-fx="kill"]')).toBeHidden();
    await expect(page.locator('#fibx-blocked')).toBeEmpty();
  });
});
