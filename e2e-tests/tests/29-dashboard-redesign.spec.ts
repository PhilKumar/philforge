import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-29: the Dashboard redesigned. One headline row, Live now and
// Desks beside Market flow, and the three ledgers as tabs of one panel.

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';

async function login(page: Page) {
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const pw = page.locator('#password-input');
  if (await pw.isVisible()) { await pw.fill(PIN); await page.click('#unlock-btn'); }
  else { for (const d of PIN.split('')) await page.click(`[data-val="${d}"]`); }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
}

const daily = Array.from({ length: 5 }, (_, i) => ({
  date: `2026-10-2${i}`, display_date: `2${i} Oct`, fii_net: i % 2 ? 1200 : -800, dii_net: 600 + i * 100,
}));

test('Dashboard: running engines light Live now, flows draw as bars, ledgers are tabs', async ({ page }) => {
  await page.route('**/api/dashboard/summary', route => route.fulfill({ json: {
    paper_flow: { pnl: 250, trades: 2 }, real_flow: { pnl: 0, trades: 0 },
    paper_strategy_flow: { active: true, name: 'Paper Test', pnl: 250, trades: 2 },
    live_strategy_flow: { active: false }, scalp_flow: { active: false },
    fii_dii: { status: 'ok', as_of: '24 Oct', latest: { fii_net: -800, dii_net: 1000 }, rolling_30d: { fii_net: 1600, dii_net: 4000 }, rolling_daily: daily },
    recent_transactions: [],
    running_engines: [{ running: true, run_id: 'r1', strategy_name: 'Paper Test', mode: 'paper', total_pnl: 250, trades_today: 2, instrument: '26000' }],
  } }));
  await login(page);
  await page.click('#nav-dashboard');

  await expect(page.locator('#pfd-live')).toHaveClass(/is-live/);
  await expect(page.locator('#pfd-live-empty')).toBeHidden();
  await expect(page.locator('#pfd-running')).toHaveText('1 engine');
  await expect(page.locator('#arsenal-running-grid > .card')).toHaveCount(1);
  await expect(page.locator('#pfd-flow-bars .pfd-flow-day')).toHaveCount(5);
  // The latest day stands lit, its figures in a bubble (Phil, 2026-10-30).
  await expect(page.locator('#pfd-flow-bars .pfd-flow-day').last()).toHaveClass(/is-picked/);
  await expect(page.locator('#pfd-flow-bars .pfd-flow-day.is-picked .pfd-flow-tip')).toContainText('24 Oct');
  await expect(page.locator('#pfd-session')).not.toHaveText('—');

  // Tabs: one pane at a time, and the search box only with Transactions.
  await expect(page.locator('[data-pfd-pane="txns"]')).toBeVisible();
  await page.click('[data-pfd-tab="runs"]');
  await expect(page.locator('[data-pfd-pane="runs"]')).toBeVisible();
  // The tabs are the site's own toggle: the chosen one wears its active state.
  await expect(page.locator('[data-pfd-tab="runs"]')).toHaveClass(/scalp-toggle-btn.*\bactive\b|\bactive\b.*scalp-toggle-btn/);
  await expect(page.locator('[data-pfd-tab="txns"]')).not.toHaveClass(/\bactive\b/);
  await expect(page.locator('[data-pfd-pane="txns"]')).toBeHidden();
  await expect(page.locator('#transactions-search-input')).toBeHidden();
  // Opening a saved-strategy folder from elsewhere brings its tab forward.
  await page.evaluate(() => (window as unknown as { toggleDashboardSavedStrategies: (o: boolean) => void }).toggleDashboardSavedStrategies(true));
  await expect(page.locator('[data-pfd-pane="saved"]')).toBeVisible();
  await expect(page.locator('[data-pfd-tab="saved"]')).toHaveAttribute('aria-selected', 'true');
  await page.click('[data-pfd-tab="txns"]');
  await expect(page.locator('#transactions-search-input')).toBeVisible();
});

test('Dashboard: nothing running shows the empty state, and no page-wide sideways scroll on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/api/dashboard/summary', route => route.fulfill({ json: {
    paper_flow: {}, real_flow: {}, paper_strategy_flow: {}, live_strategy_flow: {}, scalp_flow: {}, recent_transactions: [], running_engines: [],
  } }));
  await page.route('**/api/engines/all', route => route.fulfill({ json: { engines: [] } }));
  await login(page);
  await page.click('#nav-dashboard');
  await expect(page.locator('#pfd-live-empty')).toBeVisible();
  await expect(page.locator('#pfd-running')).toHaveText('None');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
