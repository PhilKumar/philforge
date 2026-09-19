import { test, expect, Page } from '@playwright/test';

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';

async function login(page: Page) {
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const pw = page.locator('#password-input');
  if (await pw.isVisible()) {
    await pw.fill(PIN);
    await page.click('#unlock-btn');
  } else {
    for (const digit of PIN.split('')) await page.click(`[data-val="${digit}"]`);
  }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
}

test('Portfolio uses the compact Cascade-style capital ledger', async ({ page }) => {
  await login(page);
  await page.click('#nav-portfolio');
  await expect(page.locator('#portfolio-page')).toBeVisible();

  const desktop = await page.evaluate(() => {
    const ledger = document.querySelector<HTMLElement>('#portfolio-page .portfolio-capital-ledger')!;
    const grid = document.querySelector<HTMLElement>('#portfolio-page .portfolio-capital-grid')!;
    const metrics = [...document.querySelectorAll<HTMLElement>('#portfolio-page .portfolio-capital-metric')];
    const bookCard = document.querySelector<HTMLElement>('#portfolio-page .portfolio-book-card')!;
    const firstValue = document.querySelector<HTMLElement>('#portfolio-balance')!;
    return {
      metricCount: metrics.length,
      columns: getComputedStyle(grid).gridTemplateColumns.split(' ').length,
      ledgerHeight: ledger.getBoundingClientRect().height,
      maxMetricHeight: Math.max(...metrics.map(metric => metric.getBoundingClientRect().height)),
      valueFontSize: getComputedStyle(firstValue).fontSize,
      bookCardCount: document.querySelectorAll('#portfolio-page .portfolio-book-card').length,
      paperMetricCount: document.querySelectorAll('#portfolio-page .portfolio-paper-metric').length,
      bookPadding: getComputedStyle(bookCard).paddingTop,
      legacySummaryCards: document.querySelectorAll('#portfolio-page .pf-summary-card').length,
    };
  });

  expect(desktop).toMatchObject({
    metricCount: 4,
    columns: 4,
    valueFontSize: '19px',
    bookCardCount: 2,
    paperMetricCount: 4,
    bookPadding: '14px',
    legacySummaryCards: 0,
  });
  expect(desktop.ledgerHeight).toBeLessThanOrEqual(146);
  expect(desktop.maxMetricHeight).toBeLessThanOrEqual(72);

  await page.setViewportSize({ width: 390, height: 844 });
  const mobile = await page.evaluate(() => {
    const ledger = document.querySelector<HTMLElement>('#portfolio-page .portfolio-capital-ledger')!;
    const grid = document.querySelector<HTMLElement>('#portfolio-page .portfolio-capital-grid')!;
    const bookGrid = document.querySelector<HTMLElement>('#portfolio-dual-grid')!;
    return {
      columns: getComputedStyle(grid).gridTemplateColumns.split(' ').length,
      bookColumns: getComputedStyle(bookGrid).gridTemplateColumns.split(' ').length,
      ledgerFits: ledger.scrollWidth <= ledger.clientWidth,
    };
  });

  expect(mobile).toEqual({ columns: 2, bookColumns: 1, ledgerFits: true });
});

test('Monthly real-trades ledger keeps every financial value on one line', async ({ page }) => {
  await login(page);
  await page.click('#nav-portfolio');
  await page.evaluate(() => {
    (window as any)._portfolioDaily = {
      '2026-09-01': {
        real_pnl: -1605.5, real_net_pnl: -1740.25, real_charges: 94.75,
        real_brokerage: 40, real_trades: 2, real_trade_legs: 2,
      },
    };
    (window as any)._currentMonthlyView = '2026-09';
    (window as any).renderMonthlyDailyGrid();
    (window as any).togglePortfolioMonthlyTrades();
  });
  await expect(page.locator('.portfolio-monthly-trades-table')).toBeVisible();
  const layout = await page.evaluate(() => {
    const table = document.querySelector<HTMLElement>('.portfolio-monthly-trades-table')!;
    const scroll = table.closest<HTMLElement>('.trade-table-scroll')!;
    const cells = [...table.querySelectorAll<HTMLElement>('th, td')];
    return {
      scrolls: table.scrollWidth > scroll.clientWidth,
      unbroken: cells.every(cell => getComputedStyle(cell).whiteSpace === 'nowrap'),
    };
  });
  expect(layout).toEqual({ scrolls: true, unbroken: true });
});
