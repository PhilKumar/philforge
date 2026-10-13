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
  // The app then restores the page that was open last; a click on Portfolio
  // before that lands is undone by it (the page went back to the Dashboard).
  await page.waitForFunction(() => !!(history.state && (history.state as { page?: string }).page));
}

test('Portfolio uses the compact Cascade-style capital ledger', async ({ page }) => {
  await login(page);
  await page.click('#nav-portfolio');
  await expect(page.locator('#portfolio-page')).toBeVisible();

  const desktop = await page.evaluate(() => {
    const ledger = document.querySelector<HTMLElement>('#portfolio-page .portfolio-capital-ledger')!;
    const grid = document.querySelector<HTMLElement>('#portfolio-page .portfolio-capital-grid')!;
    // The Zerodha row is in the page but hidden until a Kite app is saved.
    const metrics = [...document.querySelectorAll<HTMLElement>('#portfolio-page .portfolio-capital-metric')]
      .filter(metric => metric.getClientRects().length > 0);
    const bookCard = document.querySelector<HTMLElement>('#portfolio-page .portfolio-book-card')!;
    const firstValue = document.querySelector<HTMLElement>('#portfolio-balance')!;
    return {
      metricCount: metrics.length,
      columns: getComputedStyle(grid).gridTemplateColumns.split(' ').length,
      ledgerHeight: ledger.getBoundingClientRect().height,
      maxMetricHeight: Math.max(...metrics.map(metric => metric.getBoundingClientRect().height)),
      valueFontSize: getComputedStyle(firstValue).fontSize,
      // Zerodha's month panel is in the page too, hidden until a Kite app is saved.
      bookCardCount: [...document.querySelectorAll('#portfolio-page .portfolio-book-card')].filter(card => card.getClientRects().length > 0).length,
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

test('Zerodha gets its own row on Portfolio once a Kite app is saved', async ({ page }) => {
  await login(page);
  await page.click('#nav-portfolio');
  await expect(page.locator('#portfolio-page')).toBeVisible();
  // No Kite app: one untagged row, as before.
  await expect(page.locator('#portfolio-zerodha-row')).toBeHidden();
  await expect(page.locator('#portfolio-dhan-tag')).toBeHidden();
  await expect(page.locator('#portfolio-zerodha-book')).toBeHidden();
  await expect(page.locator('#portfolio-monthly-dhan-chip')).toBeHidden();
  await expect(page.locator('#portfolio-ytd-brokers')).toBeHidden();

  // The days PhilForge has recorded at Zerodha, in the month on screen.
  await page.waitForFunction(() => !!(window as unknown as { _currentMonthlyView?: string })._currentMonthlyView);
  const month = await page.evaluate(() => (window as unknown as { _currentMonthlyView: string })._currentMonthlyView);
  const tradingDays = await page.evaluate((ym) => {
    const w = window as unknown as { isClosedMarketDay: (d: string) => boolean };
    return Array.from({ length: 28 }, (_, i) => `${ym}-${String(i + 1).padStart(2, '0')}`).filter(d => !w.isClosedMarketDay(d)).slice(0, 2);
  }, month);
  await page.route('**/api/zerodha/history', route => route.fulfill({
    json: {
      status: 'ok', configured: true, user_id: 'AB1234',
      daily: {
        [tradingDays[0]]: { broker: 'zerodha', real_pnl: 2600, real_net_pnl: 2480.25, real_charges: 79.75, real_brokerage: 40, real_total_costs: 119.75, real_trades: 2, real_order_count: 2, real_trade_legs: 2, charges_known: true },
        [tradingDays[1]]: { broker: 'zerodha', real_pnl: -900, real_net_pnl: -1010.5, real_charges: 70.5, real_brokerage: 40, real_total_costs: 110.5, real_trades: 2, real_order_count: 2, real_trade_legs: 2, charges_known: true },
      },
    },
  }));

  await page.route('**/api/zerodha/portfolio', route => route.fulfill({
    json: {
      status: 'ok', configured: true, logged_in_today: true, connected: true, user_id: 'AB1234',
      available: 152340.5, day_pnl: -1250.75, trades_today: 3, open_positions: 1,
    },
  }));
  // Dhan's month, so the yearly table has two accounts to add together.
  await page.route('**/api/portfolio/history', route => route.fulfill({
    json: {
      status: 'success', daily: {}, yearly: {},
      monthly: { [month]: { real_pnl: 23500, real_net_pnl: 22721, real_charges: 500, real_brokerage: 279, trades: 19 } },
    },
  }));
  await page.evaluate(() => (window as unknown as { loadPortfolioData: () => Promise<void> }).loadPortfolioData());
  await expect(page.locator('#portfolio-zerodha-row')).toBeVisible();
  await expect(page.locator('#portfolio-dhan-tag')).toBeVisible();
  await expect(page.locator('#portfolio-zerodha-id')).toHaveText('AB1234');
  await expect(page.locator('#portfolio-capital-count')).toHaveText('2 accounts · 8 live metrics');
  await expect(page.locator('#portfolio-dhan-tag .pf-broker-dhan')).toHaveText('Dhan');
  await expect(page.locator('#portfolio-zerodha-row .pf-broker-zerodha')).toHaveText('Zerodha');
  await expect(page.locator('#portfolio-zerodha-balance')).toHaveText('₹152340.50');
  await expect(page.locator('#portfolio-zerodha-pnl')).toHaveText('₹-1250.75');
  await expect(page.locator('#portfolio-zerodha-trades')).toHaveText('3');
  await expect(page.locator('#portfolio-zerodha-open')).toHaveText('1');

  // Each broker's month sits beside the other, tagged; Paper drops beneath.
  await expect(page.locator('#portfolio-zerodha-book')).toBeVisible();
  await expect(page.locator('#portfolio-monthly-dhan-chip')).toHaveText('Dhan');
  await expect(page.locator('#portfolio-zerodha-book .pf-broker-zerodha')).toHaveText('Zerodha');
  await expect(page.locator('#portfolio-zerodha-monthly-grid .portfolio-monthly-day')).toHaveCount(2);
  await expect(page.locator('#portfolio-zerodha-monthly-summary')).toContainText('₹ 1469.75'); // 2480.25 - 1010.50
  await expect(page.locator('#portfolio-zerodha-monthly-summary')).toContainText('1/2');
  const layout = await page.evaluate(() => {
    const top = (sel: string) => document.querySelector(sel)!.getBoundingClientRect();
    const dhan = top('#portfolio-dual-grid > .portfolio-monthly-book:not(.portfolio-zerodha-book)');
    const zerodha = top('#portfolio-zerodha-book');
    const paper = top('#portfolio-dual-grid > .portfolio-paper-book');
    const chip = (sel: string) => getComputedStyle(document.querySelector(sel)!).color;
    return {
      sideBySide: Math.abs(dhan.top - zerodha.top) < 2 && dhan.right <= zerodha.left,
      paperBelow: paper.top >= Math.max(dhan.bottom, zerodha.bottom) - 1,
      paperFullWidth: paper.width > dhan.width * 1.8,
      differentColours: chip('#portfolio-zerodha-book .pf-broker-zerodha') !== chip('#portfolio-monthly-dhan-chip'),
    };
  });
  expect(layout).toEqual({ sideBySide: true, paperBelow: true, paperFullWidth: true, differentColours: true });

  // The yearly table adds the two accounts together, and says so.
  await expect(page.locator('#portfolio-ytd-brokers')).toBeVisible();
  await expect(page.locator('#portfolio-ytd-brokers')).toHaveText('Dhan+Zerodha');
  const ytd = await page.evaluate((ym) => {
    const w = window as unknown as { _portfolioMonthly: Record<string, { real_net_pnl?: number; real_pnl?: number; trades?: number }> };
    const dhan = w._portfolioMonthly[ym];
    const dhanNet = Number(dhan ? (dhan.real_net_pnl ?? dhan.real_pnl ?? 0) : 0);
    const [year, mm] = ym.split('-');
    const row = Array.from(document.querySelectorAll('.portfolio-ytd-table tbody tr')).find(tr => tr.firstElementChild!.textContent!.trim() === year)!;
    const cell = row.children[Number(mm)] as HTMLElement;
    return {
      shown: cell.querySelector('div')!.textContent!.trim(),
      expected: '₹' + (dhanNet + 1469.75).toFixed(0),
      trades: cell.querySelectorAll('div')[1].textContent!.trim(),
      expectedTrades: `${Number((dhan && dhan.trades) || 0) + 4}T`,
      title: cell.getAttribute('title') || '',
    };
  }, month);
  expect(ytd.shown).toBe(ytd.expected);
  expect(ytd.shown).toBe('₹24191'); // 22721 at Dhan + 1469.75 at Zerodha
  expect(ytd.trades).toBe(ytd.expectedTrades);
  expect(ytd.trades).toBe('23T');
  expect(ytd.title).toContain('Dhan: ₹22721.00 · Zerodha: ₹1469.75');

  // Saved but not logged in today: the row stays and says what to do.
  await page.route('**/api/zerodha/portfolio', route => route.fulfill({
    json: { status: 'ok', configured: true, logged_in_today: false, connected: false, user_id: 'AB1234', reason: 'login needed' },
  }));
  await page.evaluate(() => (window as unknown as { loadPortfolioData: () => Promise<void> }).loadPortfolioData());
  await expect(page.locator('#portfolio-zerodha-note')).toContainText('Log in to Zerodha');
  await expect(page.locator('#portfolio-zerodha-balance')).toHaveText('—');
});

test('Broker Status gives each broker its own column and its own button', async ({ page }) => {
  await login(page);
  await page.click('#nav-dashboard');
  await expect(page.locator('#dash-broker-summary-card')).toBeVisible();
  // The page's own check on load asks both brokers; let it finish first.
  await expect(page.locator('#broker-text')).not.toHaveText('Checking...');
  await page.waitForLoadState('networkidle');
  await expect(page.locator('#broker-zerodha-row')).toBeHidden(); // no Kite app: Dhan alone, as before

  const asked: string[] = [];
  page.on('request', (r) => { const p = new URL(r.url()).pathname; if (p === '/api/zerodha/status' || p === '/api/broker/check') asked.push(p); });
  await page.route('**/api/zerodha/status', route => route.fulfill({
    json: { status: 'ok', configured: true, logged_in_today: true, connected: true, name: 'Test', available: 1000 },
  }));
  await page.evaluate(() => (window as unknown as { refreshZerodhaBrokerLine: () => Promise<unknown> }).refreshZerodhaBrokerLine());
  await expect(page.locator('#broker-zerodha-text')).toHaveText('Zerodha Connected');
  await expect(page.locator('#broker-zerodha-btn')).toHaveText('Recheck');
  // Side by side: the two names on one line, the two buttons on the next.
  const layout = await page.evaluate(() => {
    const box = (id: string) => document.getElementById(id)!.getBoundingClientRect();
    const [dhan, zerodha, dhanBtn, zerodhaBtn] = ['broker-text', 'broker-zerodha-text', 'broker-toggle-btn', 'broker-zerodha-btn'].map(box);
    return {
      namesOnOneLine: Math.abs(dhan.top - zerodha.top) < 2 && dhan.right < zerodha.left,
      buttonsOnOneLine: Math.abs(dhanBtn.top - zerodhaBtn.top) < 2 && dhanBtn.right <= zerodhaBtn.left,
      buttonsUnderNames: dhanBtn.top > dhan.bottom,
    };
  });
  expect(layout).toEqual({ namesOnOneLine: true, buttonsOnOneLine: true, buttonsUnderNames: true });

  // Each button checks its own broker and no other.
  asked.length = 0;
  await page.click('#broker-zerodha-btn');
  await expect(page.locator('#broker-zerodha-btn')).toHaveText('Recheck');
  expect(asked).toEqual(['/api/zerodha/status']);
  asked.length = 0;
  await page.click('#broker-toggle-btn');
  await expect(page.locator('#broker-toggle-btn')).not.toHaveText('Checking...');
  expect(asked).toEqual(['/api/broker/check']);

  // The morning after: saved, but today's login has not happened -- the button is Log in.
  await page.route('**/api/zerodha/status', route => route.fulfill({
    json: { status: 'ok', configured: true, logged_in_today: false, connected: false, reason: 'Log in to Zerodha' },
  }));
  await page.click('#broker-zerodha-btn');
  await expect(page.locator('#broker-zerodha-text')).toHaveText('Zerodha · login needed');
  await expect(page.locator('#broker-zerodha-btn')).toHaveText('Log in');
  await expect(page.locator('#broker-zerodha-btn')).toHaveAttribute('data-action', 'login');
});
