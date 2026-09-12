/**
 * 05-results-deploy.spec.ts
 *
 * Deploying a saved run from the Results page. Both failures here were
 * invisible to Python tests and to reading the deploy code, because the deploy
 * payload is assembled in THREE places on the way to the broker:
 *   1. the modal's inline validation preview,
 *   2. a monkey-patched deployStrategy wrapper that validates again, and
 *   3. the deploy call itself.
 * Each one built its own payload; the wrapper called buildPayload() alone, so
 * it judged the EMPTY builder form and refused a run that plainly had an
 * instrument and entry conditions ("No instrument selected").
 *
 * The second test covers the click that looked like it did nothing: opening a
 * run from the list at the bottom of an already-open Results page left the
 * viewport at the bottom.
 */
import { test, expect, Page } from '@playwright/test';

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';

const RUN_CONFIG = {
  run_name: 'E2E_Deploy_Run',
  folder: 'E2E',
  instrument: '26000',
  segment: 'indices',
  from_date: '2026-01-01',
  to_date: '2026-01-31',
  lots: 4,
  target_profit_rupees: 10000,
  tp_type: 'rupees',
  sl_type: 'rupees',
  market_open: '09:15',
  market_close: '15:25',
  max_trades_per_day: 1,
  indicators: ['Current_Candle_5m', 'EMA_20_5m', 'CPR_0.2_0.5'],
  // Shapes copied from a real stored run — the builder writes the right-hand
  // side as a bare field name, not a {right:'indicator'} wrapper.
  entry_conditions: [
    { logic: 'IF', left: 'current_close', operator: 'is_below', right: 'EMA_20_5m' },
  ],
  exit_conditions: [
    { logic: 'IF', left: 'current_close', operator: 'crosses_below', right: 'CPR_S1' },
  ],
  legs: [{ transaction_type: 'BUY', option_type: 'PE', expiry: 'current_week', strike_type: 'premium_near', strike_value: 250, lots: 4, sl_pct: 20, sqoff_time: '15:25' }],
  stoploss_rupees: 15000,
  stats: { total_pnl: 12345, total_trades: 2, win_rate: 50, winning_trades: 1, losing_trades: 1, initial_capital: 500000 },
};

async function login(page: Page) {
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
}

/** Serve one deployable run to every runs endpoint the page uses. */
async function stubRun(page: Page, id = 4242) {
  const run = {
    id,
    ...RUN_CONFIG,
    mode: 'backtest',
    strategy_name: RUN_CONFIG.run_name,
    total_pnl: 12345,
    trade_count: 2,
    created_at: '2026-02-01 10:00:00',
    trades: [
      { id: 1, entry_time: '2026-01-05 09:20', exit_time: '2026-01-05 09:40', entry_price: 250, exit_price: 300, pnl: 12000, exit_reason: 'StrategyTP', cumulative: 12000, symbol: 'NIFTY 24000 PE', qty: 260, txn_type: 'BUY', fees: 80 },
      { id: 2, entry_time: '2026-01-08 09:25', exit_time: '2026-01-08 10:05', entry_price: 250, exit_price: 249, pnl: 345, exit_reason: 'Signal', cumulative: 12345, symbol: 'NIFTY 24100 PE', qty: 260, txn_type: 'BUY', fees: 80 },
    ],
    equity: [
      { time: '2026-01-05 09:20', equity: 0 },
      { time: '2026-01-05 09:40', equity: 12000 },
      { time: '2026-01-08 10:05', equity: 12345 },
    ],
    monthly: [{ month: '2026-01', pnl: 12345 }],
    yearly: [{ year: '2026', hits: 1, miss: 1, profit: 12000, loss: 345 }],
    day_of_week: [{ day: 'Monday', hits: 1, miss: 0, profit: 12000, loss: 0 }],
  };
  await page.route('**/api/runs', (route) => route.fulfill({ json: [run] }));
  await page.route(`**/api/runs/${id}`, (route) => route.fulfill({ json: run }));
  return run;
}

test('a run deploys from the Results page without claiming its config is empty', async ({ page }) => {
  await stubRun(page);
  await login(page);

  // The payload that reaches the server is the assertion — a passing modal
  // means nothing if the deploy itself posts an empty strategy.
  const deployPayloads: any[] = [];
  await page.route('**/api/paper/start', async (route) => {
    deployPayloads.push(route.request().postDataJSON());
    await route.fulfill({ json: { status: 'started', run_id: 'E2E_Deploy_Run' } });
  });

  await page.click('#nav-results');
  // The runs table repaints as the page loads; wait for the row to settle
  // rather than racing it.
  const row = page.locator('#runs-list-results td[title="E2E_Deploy_Run"]');
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.click();
  await expect(page.locator('#results-run-title')).toHaveText('E2E_Deploy_Run');

  await page.click('#results-content .deploy-cta-btn');
  await expect(page.locator('#deploy-modal')).toHaveClass(/open/);

  // The modal's own preview must not report the empty builder form.
  const validation = page.locator('#deploy-validation-box');
  await expect(validation).not.toContainText('No instrument selected');
  await expect(validation).not.toContainText('No entry conditions defined');

  await page.click('#deploy-paper-btn');
  await page.click('#deploy-modal .deploy-cta-btn');

  // The dialog arrives after a validate round-trip, so wait for it rather than
  // sampling. This fixture always raises the "no strategy-level SL" warning;
  // the bug's signature is the title reading "Strategy Validation Failed"
  // instead, which is what an empty-form payload produced.
  const confirmTitle = page.locator('#confirm-title');
  await expect(confirmTitle).toBeVisible({ timeout: 15_000 });
  await expect(confirmTitle).toContainText('Strategy Warnings');
  await page.click('#confirm-ok-btn');

  await expect.poll(() => deployPayloads.length, { timeout: 10_000 }).toBe(1);
  const sent = deployPayloads[0];
  expect(sent.instrument).toBe('26000');
  expect(sent.entry_conditions.length).toBeGreaterThan(0);
  expect(sent.legs.length).toBeGreaterThan(0);
  expect(sent.target_profit_rupees).toBe(10000);
});

test('opening a run from the list scrolls its results into view', async ({ page }) => {
  await stubRun(page);
  await login(page);

  await page.click('#nav-results');
  const row = page.locator('#runs-list-results td[title="E2E_Deploy_Run"]');
  await expect(row).toBeVisible({ timeout: 15_000 });

  // The runs list sits below the fold; scroll to it the way a reader would.
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);

  await row.click();
  await expect(page.locator('#results-run-title')).toHaveText('E2E_Deploy_Run');

  // Same page, so the old code skipped the scroll entirely and the click
  // looked like it had done nothing.
  await expect.poll(() => page.evaluate(() => window.scrollY), { timeout: 5_000 }).toBe(0);
});

test('published historical books open as immutable Results runs', async ({ page }) => {
  await stubRun(page);
  await login(page);

  await page.click('#nav-results');
  const row = page.locator('#runs-list-results td[title="CE_SL15_NoMonTue"]');
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.click();

  await expect(page.locator('#results-run-title')).toHaveText('CE_SL15_NoMonTue');
  await expect(page.locator('#res-header-pnl')).toHaveText('₹14,41,436');
  await expect(page.locator('#res-trade-count-badge')).toHaveText('353 Trades');
  await expect(page.locator('#equity-chart')).toBeVisible();
  await expect(page.locator('#results-action-row')).toContainText('Open Tearsheet');
  await expect(page.locator('#results-action-row .deploy-cta-btn')).toHaveCount(0);
  await expect(page.locator('#results-analytics-card')).toBeVisible();
  await expect(page.locator('#results-heatmap-card')).toBeVisible();
  await expect(page.locator('#results-pnl-heatmap-card')).toBeVisible();
  await expect(page.locator('#results-trade-log-card')).toBeVisible();
  await expect(page.locator('#monthly-pnl-grid')).toContainText('2021');
  await expect(page.locator('#trade-count-display')).toHaveText('353 Transactions');
  await expect(page.locator('#trade-log-body')).toContainText('Published curve');
});

test('Results analytics use the restrained Cascade contrast', async ({ page }) => {
  await stubRun(page);
  await login(page);

  await page.click('#nav-results');
  const row = page.locator('#runs-list-results td[title="E2E_Deploy_Run"]');
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.click();
  await expect(page.locator('#results-run-title')).toHaveText('E2E_Deploy_Run');

  const contrast = await page.evaluate(() => {
    const value = getComputedStyle(document.querySelector('#res-win-rate')!);
    const statEl = document.querySelector('#results-page .results-metrics-grid .results-stat-metric')!;
    const stat = getComputedStyle(statEl);
    const panel = getComputedStyle(document.querySelector('#results-page .analytics-inner')!);
    const bars = Array.from(document.querySelectorAll('#year-analysis .analysis-bar-wrap > div'))
      .map(el => {
        const style = getComputedStyle(el);
        return { background: style.backgroundColor, color: style.color };
      });
    const monthlyCell = Array.from(document.querySelectorAll('#monthly-pnl-grid tbody td'))
      .find(el => el.textContent?.includes('12,345'))!;
    return {
      valueShadow: value.textShadow,
      metricCount: document.querySelectorAll('#results-page .results-stat-metric').length,
      primaryColumns: getComputedStyle(document.querySelector('#results-page .results-metrics-grid')!).gridTemplateColumns.split(' ').length,
      secondaryColumns: getComputedStyle(document.querySelector('#results-page .results-pair-grid')!).gridTemplateColumns.split(' ').length,
      statHeight: statEl.getBoundingClientRect().height,
      valueSize: value.fontSize,
      statBackground: stat.backgroundColor,
      statShadow: stat.boxShadow,
      panelBackground: panel.backgroundColor,
      panelShadow: panel.boxShadow,
      bars,
      monthlyBackground: getComputedStyle(monthlyCell).backgroundColor,
    };
  });

  expect(contrast.valueShadow).toBe('none');
  expect(contrast.metricCount).toBe(11);
  expect(contrast.primaryColumns).toBe(5);
  expect(contrast.secondaryColumns).toBe(6);
  expect(contrast.statHeight).toBeLessThanOrEqual(72);
  expect(contrast.valueSize).toBe('19px');
  expect(contrast.statBackground).toBe('rgba(9, 15, 28, 0.54)');
  expect(contrast.statShadow).not.toContain('18px');
  expect(contrast.panelBackground).toBe('rgba(7, 16, 29, 0.46)');
  expect(contrast.panelShadow).toBe('none');
  expect(contrast.bars).toEqual([
    { background: 'rgba(52, 211, 153, 0.18)', color: 'rgb(110, 231, 183)' },
    { background: 'rgba(248, 113, 113, 0.16)', color: 'rgb(252, 165, 165)' },
  ]);
  expect(contrast.monthlyBackground).toMatch(/^rgba\(52, 211, 153, /);
  expect(Number(contrast.monthlyBackground.match(/, ([\d.]+)\)$/)?.[1])).toBeLessThanOrEqual(0.10);
});
