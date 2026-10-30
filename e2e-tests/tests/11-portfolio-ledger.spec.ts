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

// Phil, 2026-10-30: "redesign my portfolio page ... something new and modern
// and aesthetic ... like this" (two glass banking dashboards). Pill tabs, the
// month as hatched pill bars with the latest day solid in a bubble, a donut of
// where the month went, the balance as wallet cards, trades as a list.
test('Portfolio is a glass dashboard: P&L flow beside the wallet, bars from zero, a donut of the month', async ({ page }) => {
  await login(page);
  await page.click('#nav-portfolio');
  await expect(page.locator('#portfolio-page')).toBeVisible();
  // The page's own load first, so it cannot land on top of this month.
  await page.waitForFunction(() => !!(window as unknown as { _currentMonthlyView?: string })._currentMonthlyView);
  await page.evaluate(() => {
    const w = window as any;
    w._portfolioDaily = {
      '2026-09-01': { real_pnl: 1200, real_net_pnl: 1100, real_charges: 60, real_brokerage: 40, real_trades: 2, real_trade_legs: 2 },
      '2026-09-02': { real_pnl: -400, real_net_pnl: -480, real_charges: 40, real_brokerage: 40, real_trades: 2, real_trade_legs: 2 },
    };
    w._currentMonthlyView = '2026-09';
    w.renderMonthlyDailyGrid();
  });

  const desktop = await page.evaluate(() => {
    const box = (sel: string) => document.querySelector(sel)!.getBoundingClientRect();
    const flow = box('#portfolio-month'), wallet = box('#portfolio-page .pfx-wallet');
    return {
      walletBeside: wallet.left >= flow.right - 1 && Math.abs(wallet.top - flow.top) < 4,
      folded: [...document.querySelectorAll<HTMLDetailsElement>('#portfolio-page details.pfolio-fold')].map(d => d.open),
      // No Kite app: no account tabs, no Zerodha card.
      tabs: !(document.getElementById('portfolio-month-broker') as HTMLElement).hidden,
      zerodhaCard: (document.querySelector('#portfolio-page .pfx-acct.is-zerodha') as HTMLElement).getClientRects().length > 0,
    };
  });
  expect(desktop).toEqual({ walletBeside: true, folded: [false, false], tabs: false, zerodhaCard: false });

  const bars = page.locator('#portfolio-monthly-grid .portfolio-monthly-day');
  await expect(bars).toHaveCount(2);
  await expect(bars.nth(0)).toHaveAttribute('aria-label', '01 Sep');
  await expect(bars.nth(0)).toHaveClass(/is-profit/);
  await expect(bars.nth(1)).toHaveClass(/is-loss/);
  // The up bar stands on the zero line, the down bar hangs from it.
  const geo = await bars.evaluateAll(els => els.map(el => el.querySelector('.pfx-pillbar')!.getBoundingClientRect()).map(r => ({ top: r.top, bottom: r.bottom })));
  expect(Math.abs(geo[0].bottom - geo[1].top)).toBeLessThan(2);
  // The latest day is the solid one, its bubble showing; a pointer moves it.
  const tipOpacity = (i: number) => bars.nth(i).locator('.pfolio-tip').evaluate(el => getComputedStyle(el).opacity);
  await expect(bars.nth(1)).toHaveClass(/is-picked/);
  await expect.poll(() => tipOpacity(1)).toBe('1');
  await expect.poll(() => tipOpacity(0)).toBe('0');
  await bars.nth(0).hover();
  await expect.poll(() => tipOpacity(0)).toBe('1');
  await expect(bars.nth(0).locator('.pfolio-tip')).toContainText('+₹1100.00');

  await expect(page.locator('#portfolio-monthly-summary')).toContainText('₹ 620.00');
  // Where the month went: what green days made, red days lost, costs took.
  const donut = page.locator('#portfolio-month-ring');
  await expect(donut.locator('.pfx-donut-ring strong')).toHaveText('₹620');
  await expect(donut.locator('.pfx-legend li')).toHaveText([/Green days · 1\s*₹1,100/, /Red days · 1\s*₹480/, /Charges \+ brokerage\s*₹180/]);
  await expect(donut.locator('.pfx-arc')).toHaveCount(3);

  await page.setViewportSize({ width: 390, height: 844 });
  const mobile = await page.evaluate(() => ({
    columns: getComputedStyle(document.querySelector('#portfolio-strip')!).gridTemplateColumns.split(' ').length,
    fits: document.documentElement.scrollWidth <= window.innerWidth,
  }));
  expect(mobile).toEqual({ columns: 1, fits: true });
});

test('Monthly real-trades ledger keeps every financial value on one line', async ({ page }) => {
  await login(page);
  await page.click('#nav-portfolio');
  await page.waitForFunction(() => !!(window as unknown as { _currentMonthlyView?: string })._currentMonthlyView);
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
  // The table spans the whole month card now, so it may fit; when it does
  // not, it scrolls rather than breaking a figure over two lines.
  const layout = await page.evaluate(() => {
    const table = document.querySelector<HTMLElement>('.portfolio-monthly-trades-table')!;
    const scroll = table.closest<HTMLElement>('.trade-table-scroll')!;
    const cells = [...table.querySelectorAll<HTMLElement>('th, td')];
    return {
      scrolls: ['auto', 'scroll'].includes(getComputedStyle(scroll).overflowX),
      unbroken: cells.every(cell => getComputedStyle(cell).whiteSpace === 'nowrap'),
    };
  });
  expect(layout).toEqual({ scrolls: true, unbroken: true });
});

test('With a Kite app saved, the wallet holds both cards and the tabs switch the month between accounts', async ({ page }) => {
  await login(page);
  await page.click('#nav-portfolio');
  await expect(page.locator('#portfolio-page')).toBeVisible();
  // No Kite app: no split, no switch, no note.
  await expect(page.locator('#portfolio-strip')).not.toHaveClass(/has-zerodha/);
  await expect(page.locator('#portfolio-zerodha-row')).toBeHidden();
  await expect(page.locator('#portfolio-month-broker')).toBeHidden();
  await expect(page.locator('#portfolio-zerodha-tools')).toBeHidden();
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
      // 30 trades, 11 of them paper runs': the table counts the 19 real ones.
      monthly: { [month]: { real_pnl: 23500, real_net_pnl: 22721, real_charges: 500, real_brokerage: 279, trades: 30, real_trades: 19 } },
    },
  }));
  await page.evaluate(() => (window as unknown as { loadPortfolioData: () => Promise<void> }).loadPortfolioData());
  await expect(page.locator('#portfolio-strip')).toHaveClass(/has-zerodha/);
  // Connected: the strip says it all, no note.
  await expect(page.locator('#portfolio-zerodha-row')).toBeHidden();
  await expect(page.locator('#portfolio-zerodha-balance')).toHaveText('₹1,52,341');
  await expect(page.locator('#portfolio-page .pfx-acct.is-zerodha')).toBeVisible();
  await expect(page.locator('#portfolio-zerodha-pnl')).toHaveText('₹-1250.75');
  await expect(page.locator('#portfolio-zerodha-trades')).toHaveText('3');
  await expect(page.locator('#portfolio-zerodha-open')).toHaveText('1');
  await expect(page.locator('#portfolio-month-zerodha')).toHaveText('₹1469.75'); // 2480.25 - 1010.50
  // The big figures are the two accounts added.
  const totals = await page.evaluate(() => {
    const w = window as unknown as { _portfolioMoney: { dhanBalance: number; dhanToday: number }; pfMoneyGrouped: (n: number) => string };
    return {
      balance: [document.getElementById('portfolio-total-balance')!.textContent, w.pfMoneyGrouped(w._portfolioMoney.dhanBalance + 152340.5)],
      today: [document.getElementById('portfolio-total-today')!.textContent, w.pfMoneyGrouped(w._portfolioMoney.dhanToday - 1250.75)],
    };
  });
  expect(totals.balance[0]).toBe(totals.balance[1]);
  expect(totals.today[0]).toBe(totals.today[1]);
  const chip = (sel: string) => page.locator(sel).first().evaluate(el => getComputedStyle(el).color);
  expect(await chip('#portfolio-strip .pfx-lines .pf-broker-zerodha')).not.toBe(await chip('#portfolio-strip .pfx-lines .pf-broker-dhan'));

  // The month: All, Dhan or Zerodha.
  await expect(page.locator('#portfolio-month-broker')).toBeVisible();
  await expect(page.locator('#portfolio-zerodha-tools')).toBeVisible();
  await expect(page.locator('#portfolio-month-broker [data-broker="all"]')).toHaveClass(/is-on/);
  const days = page.locator('#portfolio-monthly-grid .portfolio-monthly-day');
  await expect(days).toHaveCount(2);
  await page.click('#portfolio-month-broker [data-broker="zerodha"]');
  await expect(page.locator('#portfolio-month-broker [data-broker="zerodha"]')).toHaveClass(/is-on/);
  await expect(days).toHaveCount(2);
  await expect(page.locator('#portfolio-monthly-summary')).toContainText('₹ 1469.75');
  await expect(page.locator('#portfolio-month-ring .pfx-legend')).toContainText('Green days · 1');
  await expect(page.locator('#portfolio-month-ring .pfx-legend')).toContainText('Red days · 1');
  await page.click('#portfolio-month-broker [data-broker="dhan"]');
  await expect(days).toHaveCount(0);

  // The yearly table adds the two accounts together, and says so.
  await expect(page.locator('#portfolio-ytd-brokers')).toBeVisible();
  await expect(page.locator('#portfolio-ytd-brokers')).toHaveText('Dhan+Zerodha');
  const ytd = await page.evaluate((ym) => {
    const w = window as unknown as { _portfolioMonthly: Record<string, { real_net_pnl?: number; real_pnl?: number; real_trades?: number }> };
    const dhan = w._portfolioMonthly[ym];
    const dhanNet = Number(dhan ? (dhan.real_net_pnl ?? dhan.real_pnl ?? 0) : 0);
    const [year, mm] = ym.split('-');
    const row = Array.from(document.querySelectorAll('.portfolio-ytd-table tbody tr')).find(tr => tr.firstElementChild!.textContent!.trim() === year)!;
    const cell = row.children[Number(mm)] as HTMLElement;
    return {
      shown: cell.querySelector('div')!.textContent!.trim(),
      expected: '₹' + (dhanNet + 1469.75).toFixed(0),
      trades: cell.querySelectorAll('div')[1].textContent!.trim(),
      expectedTrades: `${Number((dhan && dhan.real_trades) || 0) + 4}T`,
      title: cell.getAttribute('title') || '',
    };
  }, month);
  expect(ytd.shown).toBe(ytd.expected);
  expect(ytd.shown).toBe('₹24191'); // 22721 at Dhan + 1469.75 at Zerodha
  expect(ytd.trades).toBe(ytd.expectedTrades);
  expect(ytd.trades).toBe('23T');
  expect(ytd.title).toContain('Dhan: ₹22721.00 · Zerodha: ₹1469.75');

  // Saved but not logged in today: the note says what to do.
  await page.route('**/api/zerodha/portfolio', route => route.fulfill({
    json: { status: 'ok', configured: true, logged_in_today: false, connected: false, user_id: 'AB1234', reason: 'login needed' },
  }));
  await page.evaluate(() => (window as unknown as { loadPortfolioData: () => Promise<void> }).loadPortfolioData());
  await expect(page.locator('#portfolio-zerodha-row')).toBeVisible();
  await expect(page.locator('#portfolio-zerodha-id')).toHaveText('AB1234');
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

test("Today's Trades lists Zerodha's fills beside Dhan's, one row per contract and product", async ({ page }) => {
  // Phil, 2026-10-15: "why only one trade or is it from Dhan only?" -- it was
  // Dhan only. The CE book's 22800CE traded MIS at Zerodha the same day the
  // scalps traded it NRML: two trades, so two rows.
  await login(page);
  const day = await page.evaluate(() => {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  });
  await page.route('**/api/broker/trades', route => route.fulfill({ json: { status: 'success', trades: [
    { securityId: '47153', tradingSymbol: 'NIFTY-Oct2026-22800-CE', transactionType: 'BUY', tradedQuantity: 130, tradedPrice: 276.5, createTime: `${day} 13:10:01` },
    { securityId: '47153', tradingSymbol: 'NIFTY-Oct2026-22800-CE', transactionType: 'SELL', tradedQuantity: 130, tradedPrice: 303.85, createTime: `${day} 14:20:00` },
  ] } }));
  const fill = (sym: string, product: string, side: string, qty: number, price: number, at: string) =>
    ({ tradingSymbol: sym, productType: product, transactionType: side, tradedQuantity: qty, tradedPrice: price, exchangeTime: `${day} ${at}`, orderId: at });
  await page.route('**/api/zerodha/portfolio', route => route.fulfill({ json: {
    status: 'ok', configured: true, logged_in_today: true, connected: true, user_id: 'AB1234',
    available: 152340.5, day_pnl: 0, trades_today: 3, open_positions: 0,
    fills: [
      fill('NIFTY26O1922800CE', 'NRML', 'BUY', 195, 255.45, '12:36:58'),
      fill('NIFTY26O1922800CE', 'NRML', 'SELL', 195, 261.65, '12:39:15'),
      fill('NIFTY26O1922800CE', 'MIS', 'BUY', 130, 276.3, '13:10:01'),
      fill('NIFTY26O1922800CE', 'MIS', 'SELL', 130, 301.75, '13:37:07'),
      fill('SENSEX26O1574000PE', 'NRML', 'BUY', 80, 300, '14:00:00'), // still open: no row
    ],
  } }));
  await page.click('#nav-portfolio');
  const rows = page.locator('#portfolio-trades-body .pfx-txn');
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText('ZERODHA · MIS');
  await expect(rows.nth(0)).toContainText('₹276.30');
  await expect(rows.nth(0)).toContainText('₹301.75');
  await expect(rows.nth(0)).toContainText('3308.50');
  await expect(rows.nth(1)).toContainText('DHAN');
  await expect(rows.nth(1)).toContainText('3555.50');
  await expect(rows.nth(2)).toContainText('ZERODHA · NRML');
  await expect(rows.nth(2)).toContainText('1209.00');
});

test('A Zerodha Console tradebook fills the older Zerodha months, marked before charges', async ({ page }) => {
  // Phil, 2026-10-16: "Why old trades are not visible in zerodha like Aug 2021?"
  // Kite keeps today only; Console's tradebook has the rest. The upload goes
  // through the real route and the real ledger.
  await login(page);
  await page.route('**/api/zerodha/portfolio', route => route.fulfill({ json: {
    status: 'ok', configured: true, logged_in_today: true, connected: true, user_id: 'AB1234',
    available: 0, day_pnl: 0, trades_today: 0, open_positions: 0, fills: [],
  } }));
  await page.click('#nav-portfolio');
  await expect(page.locator('#portfolio-zerodha-tools')).toBeVisible();
  const csv = [
    'symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time,expiry_date',
    'NIFTY21AUG16400CE,,2021-08-10,NFO,NFO-OPT,,buy,false,50,100.00,T1,O1,2021-08-10T09:20:00,2021-08-12',
    'NIFTY21AUG16400CE,,2021-08-10,NFO,NFO-OPT,,sell,false,50,115.00,T2,O2,2021-08-10T10:00:00,2021-08-12',
    'NIFTY21AUG16500PE,,2021-08-11,NFO,NFO-OPT,,buy,false,50,80.00,T3,O3,2021-08-11T09:30:00,2021-08-12',
    'NIFTY21AUG16500PE,,2021-08-11,NFO,NFO-OPT,,sell,false,50,70.00,T4,O4,2021-08-11T14:30:00,2021-08-12',
  ].join('\n');
  const done = page.waitForResponse(r => r.url().includes('/api/zerodha/import-tradebook'));
  await page.setInputFiles('#portfolio-zerodha-import', { name: 'tradebook-AB1234-FO.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
  const res = await done;
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect([body.written, body.first, body.last, body.gross]).toEqual([2, '2021-08-10', '2021-08-11', 250]);

  await page.evaluate(() => {
    const w = window as unknown as { _currentMonthlyView: string; setPortfolioMonthBroker: (b: string) => void };
    w._currentMonthlyView = '2021-08';
    w.setPortfolioMonthBroker('zerodha');
  });
  const summary = page.locator('#portfolio-monthly-summary');
  await expect(summary).toContainText('250');
  await expect(summary).toContainText('2 of 2 days imported from the Zerodha Console tradebook, before charges');
  await expect(page.locator('#portfolio-monthly-grid .portfolio-monthly-day')).toHaveCount(2);
});

test('A real MCX trade on an NSE holiday shows in the month; an empty holiday does not', async ({ page }) => {
  // Phil, 2026-10-21: "why yesterday's commodity trade is not displayed yet?"
  // -- a crude trade on Dussehra (20-Oct), an NSE holiday, MCX open.
  await login(page);
  await page.click('#nav-portfolio');
  // The page's own load first, so it cannot land on top of this month.
  await page.waitForFunction(() => !!(window as unknown as { _currentMonthlyView?: string })._currentMonthlyView);
  await page.evaluate(() => {
    (window as any)._portfolioDaily = {
      '2026-10-19': { real_pnl: -10003.49, real_net_pnl: -10817.11, real_charges: 533.62, real_brokerage: 280, real_trades: 14, real_trade_legs: 14 },
      '2026-10-20': { real_pnl: 327, real_net_pnl: 270.82, real_charges: 16.18, real_brokerage: 40, real_trades: 2, real_trade_legs: 2 },
      '2026-10-02': { real_pnl: 0, real_net_pnl: 0, real_trades: 0 },  // Gandhi Jayanti: nothing traded
    };
    (window as any)._currentMonthlyView = '2026-10';
    (window as any).renderMonthlyDailyGrid();
  });
  const tiles = page.locator('#portfolio-monthly-grid .portfolio-monthly-day');
  await expect(tiles).toHaveCount(2);
  await expect(tiles.nth(1)).toHaveAttribute('aria-label', '20 Oct');
  await expect(tiles.nth(1).locator('.pfolio-tip')).toContainText('₹270.82');
});

// Phil, 2026-10-30: "Need dhan and zerodha tags separately ... and also need
// to download excel from that data".
test("Trades for month tags each row Dhan or Zerodha, and downloads as Excel", async ({ page }) => {
  await login(page);
  await page.click('#nav-portfolio');
  await page.waitForFunction(() => !!(window as unknown as { _currentMonthlyView?: string })._currentMonthlyView);
  await page.evaluate(() => {
    const w = window as any;
    w._portfolioDaily = {
      '2026-09-01': { real_pnl: 1200, real_net_pnl: 1100, real_charges: 60, real_brokerage: 40, real_trades: 2, real_trade_legs: 2 },
      '2026-09-02': { real_pnl: -400, real_net_pnl: -480, real_charges: 40, real_brokerage: 40, real_trades: 2, real_trade_legs: 2 },
    };
    w._portfolioZerodhaDaily = {
      '2026-09-01': { real_pnl: 900, real_net_pnl: 810.5, real_charges: 49.5, real_brokerage: 40, real_trades: 2, real_trade_legs: 2 },
    };
    (document.getElementById('portfolio-month-broker') as HTMLElement).hidden = false;
    w._currentMonthlyView = '2026-09';
    w.setPortfolioMonthBroker('all');
    w.togglePortfolioMonthlyTrades();
  });
  // All: 1 Sep traded at both, so two rows, each with its own figures.
  const rows = page.locator('.portfolio-monthly-trades-table tbody tr');
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toHaveAttribute('data-broker', 'dhan');
  await expect(rows.nth(0).locator('.pf-broker-dhan')).toHaveText('Dhan');
  await expect(rows.nth(0)).toContainText('₹ 1100.00');
  await expect(rows.nth(1)).toHaveAttribute('data-broker', 'zerodha');
  await expect(rows.nth(1).locator('.pf-broker-zerodha')).toHaveText('Zerodha');
  await expect(rows.nth(1)).toContainText('₹ 810.50');
  await expect(page.locator('.portfolio-monthly-trades-sub')).toHaveText('2 trading days · 6 trades');

  const [download] = await Promise.all([page.waitForEvent('download'), page.click('#portfolio-month-xlsx')]);
  expect(download.suggestedFilename()).toBe('philforge-trades-2026-09.xlsx');
  const fs = await import('fs');
  const body = fs.readFileSync((await download.path())!);
  expect(body.subarray(0, 2).toString()).toBe('PK');
  const xml = body.toString('utf8');
  for (const want of ['<t>Date</t>', '<t>Account</t>', '<t>2026-09-01</t>', '<t>Dhan</t>', '<t>Zerodha</t>', '<v>810.5</v>', '<t>Total</t>', '<v>1430.5</v>']) {
    expect(xml).toContain(want);
  }

  // One account: only its rows, still tagged.
  await page.evaluate(() => (window as any).setPortfolioMonthBroker('dhan'));
  await expect(rows).toHaveCount(2);
  await expect(page.locator('.portfolio-monthly-trades-table tbody tr[data-broker="zerodha"]')).toHaveCount(0);
});
