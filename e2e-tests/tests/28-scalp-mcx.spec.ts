import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-16: "I need crude oil and natural gas options to be added to
// the scalp section" -- paper first, full and mini contracts. MCX options are
// options on the futures, trading 09:00 to 23:30.

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';

async function login(page: Page) {
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const pw = page.locator('#password-input');
  if (await pw.isVisible()) { await pw.fill(PIN); await page.click('#unlock-btn'); }
  else { for (const d of PIN.split('')) await page.click(`[data-val="${d}"]`); }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
  await page.waitForFunction(() => typeof (window as unknown as { showPage?: unknown }).showPage === 'function');
}

const ist = (s: string) => Math.floor(new Date(`${s}+05:30`).getTime() / 1000);

test('Scalp: crude oil options trade on paper with the contract size, the future and MCX hours', async ({ page }) => {
  test.setTimeout(60_000);
  await page.clock.setFixedTime(new Date('2026-10-16T22:07:30+05:30'));
  await page.setViewportSize({ width: 1400, height: 1000 });
  const asked: Record<string, string>[] = [];
  await page.route('**/api/expiry-list/CRUDEOIL', (route) => route.fulfill({ json: { status: 'ok', expiries: ['2026-11-17', '2026-12-16'] } }));
  await page.route('**/api/scalp/underlying-price**', (route) => {
    asked.push(Object.fromEntries(new URL(route.request().url()).searchParams));
    return route.fulfill({ json: { status: 'ok', underlying: 'CRUDEOIL', price: 9110, future_security_id: '573422' } });
  });
  await page.route('**/api/option-ltp**', (route) => route.fulfill({ json: { status: 'ok', ltp: 524.4 } }));
  const open = ist('2026-10-16T09:00:00');
  const candles = Array.from({ length: 157 }, (_, i) => ({ t: open + i * 300, o: 500 + (i % 9), h: 506 + (i % 9), l: 496 + (i % 9), c: 502 + (i % 9) })); // 09:00 .. 22:00
  await page.route('**/api/scalp/contract-chart**', (route) => {
    const q = new URL(route.request().url()).searchParams;
    return route.fulfill({ json: {
      status: 'ok', timeframe: q.get('timeframe'), candles, entries: [], overlays: [], lines: [], live_price: 524.4,
      session: { open: '09:00', close: '23:30', exchange: 'MCX' },
      instrument: { underlying: 'CRUDEOIL', strike: Number(q.get('strike')), option_type: q.get('option_type'), expiry: q.get('expiry'), security_id: '1' },
    } });
  });
  await login(page);
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);

  await page.selectOption('#scalp-underlying', 'CRUDEOIL');
  await expect(page.locator('#scalp-lot-size-label')).toHaveText('100');
  await expect(page.locator('#scalp-expiry option')).toHaveText([/17 Nov 26/, /16 Dec 26/]);
  // Two strikes in the money off the 19-Nov future (9,110 -> ATM 9,100).
  await expect(page.locator('#scalp-strike')).toHaveValue('9000');
  expect(asked[0]).toEqual({ underlying: 'CRUDEOIL', expiry: '2026-11-17' });
  // Paper only for now.
  await expect(page.locator('#scalp-mode')).toHaveValue('paper');
  await expect(page.locator('#scalp-mode-toggle [data-value="live"]')).toBeDisabled();
  await expect(page.locator('#scalp-live-note')).toContainText('paper only for now');
  // Margin: premium x lots x 100 barrels.
  const lots = Number(await page.locator('#scalp-lots').inputValue());
  await expect(page.locator('#scalp-margin')).toHaveText('₹' + Math.round(524.4 * lots * 100).toLocaleString('en-IN'));

  // The chart keeps MCX hours: at 22:07:30 the 22:05 bar is forming, 02:30 to go,
  // and one day is 09:00-23:30 (174 five-minute bars).
  await page.evaluate(() => {
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '9100';
    const w = window as unknown as { _scalpCurrentLTP: number; _scalpLTPKey: string; _scalpOptionChartTf: string; _syncScalpContractChartLabel: () => void };
    w._scalpCurrentLTP = 524.4;
    w._scalpLTPKey = 'form:CRUDEOIL|9100|2026-11-17|CE';
    w._scalpOptionChartTf = '5m';
    w._syncScalpContractChartLabel();
  });
  await expect(page.locator('#scalp-dock-chart-body canvas').first()).toBeVisible();
  const c = () => page.evaluate(() => { const k = (window as any)._pfChartCanvas; return { last: k.data.candles.at(-1), countdown: k.paint.countdown, fit: k.data.fit_bars }; });
  await expect.poll(async () => (await c()).last.t).toBe(ist('2026-10-16T22:05:00'));
  const now = await c();
  expect(now.last.c).toBe(524.4);
  expect(now.countdown).toBe('02:30');
  expect(now.fit).toBe(174);
});

test('Scalp: back to NIFTY, Live is offered again', async ({ page }) => {
  await page.route('**/api/expiry-list/NATGASMINI', (route) => route.fulfill({ json: { status: 'ok', expiries: ['2026-10-23'] } }));
  await page.route('**/api/scalp/underlying-price**', (route) => route.fulfill({ json: { status: 'ok', price: 303.9 } }));
  await login(page);
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await page.selectOption('#scalp-underlying', 'NATGASMINI');
  await expect(page.locator('#scalp-lot-size-label')).toHaveText('250');
  await expect(page.locator('#scalp-strike')).toHaveValue('295'); // ATM 305, CE two 5-point steps in
  await expect(page.locator('#scalp-mode-toggle [data-value="live"]')).toBeDisabled();
  await page.selectOption('#scalp-underlying', 'NIFTY');
  await expect(page.locator('#scalp-lot-size-label')).toHaveText('65');
  await expect(page.locator('#scalp-mode-toggle [data-value="live"]')).toBeEnabled();
});
