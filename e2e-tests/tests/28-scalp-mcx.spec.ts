import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-16: "I need crude oil and natural gas options to be added to
// the scalp section" -- full and mini contracts, then "build live MCX for
// both" (Dhan and Zerodha). MCX options are options on the futures, trading
// 09:00 to 23:30.

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

// The underlying is chosen from the picker's tabs (desktop), as Phil does.
async function pick(page: Page, value: string) {
  await page.click('#scalp-uly .scalp-uly-btn');
  const item = page.locator(`#scalp-uly .scalp-uly-item[data-value="${value}"]`);
  if (!(await item.isVisible())) {
    const group = await page.evaluate((v) => (document.querySelector(`#scalp-underlying option[value="${v}"]`)!.parentElement as HTMLOptGroupElement).label, value);
    await page.click(`#scalp-uly .scalp-uly-tab[data-group="${group}"]`);
  }
  await item.click();
  await expect(page.locator('#scalp-uly .scalp-uly-panel')).toBeHidden();
}

const ist = (s: string) => Math.floor(new Date(`${s}+05:30`).getTime() / 1000);

test('Scalp: crude oil options take the contract size, the future and MCX hours', async ({ page }) => {
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

  await pick(page, 'CRUDEOIL');
  await expect(page.locator('#scalp-lot-size-label')).toHaveText('100');
  await expect(page.locator('#scalp-expiry option')).toHaveText([/17 Nov 26/, /16 Dec 26/]);
  // Two strikes in the money off the 19-Nov future (9,110 -> ATM 9,100).
  await expect(page.locator('#scalp-strike')).toHaveValue('9000');
  expect(asked[0]).toEqual({ underlying: 'CRUDEOIL', expiry: '2026-11-17' });
  // Live is offered, and the note gives MCX's hours.
  await expect(page.locator('#scalp-mode-toggle [data-value="live"]')).toBeEnabled();
  await expect(page.locator('#scalp-live-note')).toContainText('CRUDEOIL trades on MCX from 09:00 to 23:30');
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
  const c = () => page.evaluate(() => { const k = (window as any)._pfChartCanvas; return { last: k.data.candles.at(-1), countdown: k.paint.countdown, fit: k.data.fit_sessions }; });
  await expect.poll(async () => (await c()).last.t).toBe(ist('2026-10-16T22:05:00'));
  const now = await c();
  expect(now.last.c).toBe(524.4);
  expect(now.countdown).toBe('02:30');
  // Three trading days on screen, whatever the session's length (MCX runs to 23:30).
  expect(now.fit).toBe(3);
});

test('Scalp: natural gas mini can go live on either broker', async ({ page }) => {
  await page.route('**/api/expiry-list/NATGASMINI', (route) => route.fulfill({ json: { status: 'ok', expiries: ['2026-10-23'] } }));
  await page.route('**/api/scalp/underlying-price**', (route) => route.fulfill({ json: { status: 'ok', price: 303.9 } }));
  await login(page);
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await pick(page, 'NATGASMINI');
  await expect(page.locator('#scalp-lot-size-label')).toHaveText('250');
  await expect(page.locator('#scalp-strike')).toHaveValue('295'); // ATM 305, CE two 5-point steps in
  await expect(page.locator('#scalp-mode-toggle [data-value="live"]')).toBeEnabled();
  await page.evaluate(() => (window as unknown as { setScalpBroker: (v: string) => void }).setScalpBroker('zerodha'));
  await expect(page.locator('#scalp-live-note')).toContainText('Live on Zerodha');
  await expect(page.locator('#scalp-live-note')).toContainText('NATGASMINI trades on MCX');
  await pick(page, 'NIFTY');
  await expect(page.locator('#scalp-lot-size-label')).toHaveText('65');
  await expect(page.locator('#scalp-live-note')).not.toContainText('MCX');
});

test('Scalp: the underlying picker has a tab per group, lot sizes, and folds away', async ({ page }) => {
  // Phil, 2026-10-16: the plain list "is not neat ... if I need to add more
  // options data, like stock options".
  await page.setViewportSize({ width: 1400, height: 1000 });
  await page.route('**/api/scalp/underlyings', (route) => route.fulfill({ json: { status: 'ok', stocks: [] } }));
  await page.route('**/api/expiry-list/CRUDEOILM', (route) => route.fulfill({ json: { status: 'ok', expiries: ['2026-11-17'] } }));
  await page.route('**/api/scalp/underlying-price**', (route) => route.fulfill({ json: { status: 'ok', price: 9110 } }));
  await login(page);
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#scalp-underlying')).toBeHidden();
  const btn = page.locator('#scalp-uly .scalp-uly-btn');
  await expect(btn.locator('.scalp-uly-tag')).toHaveText('Index');
  await btn.click();
  await expect(page.locator('#scalp-uly .scalp-uly-tab')).toHaveText(['Index4', 'Commodity4']);
  await expect(page.locator('#scalp-uly .scalp-uly-item[data-value="NIFTY"] small')).toHaveText('Lot 65');
  // A click elsewhere folds it.
  await page.locator('#scalp-form-title').click();
  await expect(page.locator('#scalp-uly .scalp-uly-panel')).toBeHidden();
  await btn.click();
  await page.click('#scalp-uly .scalp-uly-tab[data-group="Commodity"]');
  await expect(page.locator('#scalp-uly .scalp-uly-item small')).toHaveText(['100 bbl / lot', '10 bbl / lot', '1,250 mmBtu / lot', '250 mmBtu / lot']);
  await page.click('#scalp-uly .scalp-uly-item[data-value="CRUDEOILM"]');
  await expect(page.locator('#scalp-underlying')).toHaveValue('CRUDEOILM');
  await expect(btn.locator('.scalp-uly-name')).toHaveText('CRUDEOIL MINI');
  await expect(btn.locator('.scalp-uly-tag')).toHaveText('Commodity');
  await expect(page.locator('#scalp-lot-size-label')).toHaveText('10');
  // Opened again, it is on the chosen symbol's tab with it marked.
  await btn.click();
  await expect(page.locator('#scalp-uly .scalp-uly-tab.is-active')).toHaveAttribute('data-group', 'Commodity');
  await expect(page.locator('#scalp-uly .scalp-uly-item.is-selected')).toHaveAttribute('data-value', 'CRUDEOILM');
  // A phone keeps its own list.
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('#scalp-uly')).toBeHidden();
  await expect(page.locator('#scalp-underlying')).toBeVisible();
});

test('Scalp: stock options are searched in their own tab and take each stock\'s lot and strike gap', async ({ page }) => {
  // Phil, 2026-10-16: "add all F&O stocks both live and paper".
  await page.setViewportSize({ width: 1400, height: 1000 });
  await page.route('**/api/scalp/underlyings', (route) => route.fulfill({ json: { status: 'ok', stocks: [
    { symbol: 'BAJAJ-AUTO', lot: 75 }, { symbol: 'CROMPTON', lot: 1800 }, { symbol: 'RELIANCE', lot: 500 },
    ...Array.from({ length: 12 }, (_, i) => ({ symbol: `STOCK${i}`, lot: 100 })),
  ] } }));
  await page.route('**/api/expiry-list/CROMPTON', (route) => route.fulfill({ json: { status: 'ok', expiries: ['2026-10-27', '2026-11-23'] } }));
  await page.route('**/api/scalp/contract-spec**', (route) => route.fulfill({ json: { status: 'ok', lot: 1800, step: 2.5 } }));
  const asked: string[] = [];
  await page.route('**/api/scalp/underlying-price**', (route) => {
    asked.push(new URL(route.request().url()).searchParams.get('underlying')!);
    return route.fulfill({ json: { status: 'ok', underlying: 'CROMPTON', price: 147.4 } });
  });
  await page.route('**/api/option-ltp**', (route) => route.fulfill({ json: { status: 'ok', ltp: 6.35 } }));
  await login(page);
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  const btn = page.locator('#scalp-uly .scalp-uly-btn');
  await btn.click();
  await expect(page.locator('#scalp-uly .scalp-uly-tab')).toHaveText(['Index4', 'Commodity4', 'Stocks15']);
  await page.click('#scalp-uly .scalp-uly-tab[data-group="Stocks"]');
  // A long group is searched.
  const search = page.locator('#scalp-uly .scalp-uly-search');
  await expect(search).toBeVisible();
  await search.fill('crom');
  await expect(page.locator('#scalp-uly .scalp-uly-item')).toHaveCount(1);
  await expect(page.locator('#scalp-uly .scalp-uly-item small')).toHaveText('Lot 1800');
  await page.click('#scalp-uly .scalp-uly-item[data-value="CROMPTON"]');
  await expect(btn.locator('.scalp-uly-tag')).toHaveText('Stocks');
  // Its CMP sits beside the name (Phil, 2026-10-16: "I want to know the CMP").
  await expect(btn.locator('.scalp-uly-cmp')).toHaveText('₹147.40');
  await expect(page.locator('#scalp-lot-size-label')).toHaveText('1800');
  // Two 2.5-point strikes in the money off the share (147.4 -> ATM 147.5), and
  // the stepper keeps the half.
  await expect(page.locator('#scalp-strike')).toHaveValue('142.5');
  expect(asked).toContain('CROMPTON');
  await page.evaluate(() => (window as unknown as { stepScalpStrike: (d: number) => void }).stepScalpStrike(1));
  await expect(page.locator('#scalp-strike')).toHaveValue('145');
  // A reload comes back to the stock, before and after its list arrives.
  await page.reload();
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#scalp-uly .scalp-uly-name')).toHaveText('CROMPTON');
  await expect(page.locator('#scalp-underlying')).toHaveValue('CROMPTON');
});

test('Scalp: a stock picked after crude takes a strike it lists, never crude\'s strike or premium', async ({ page }) => {
  // Phil, 2026-10-20: APLAPOLLO kept crude's 9350 (its strikes run 2700-3000)
  // and crude's ₹575.55 premium on a holiday with no live share price, so the
  // chart found no contract and the margin read ₹8 lakh.
  await page.setViewportSize({ width: 1400, height: 1000 });
  await page.route('**/api/scalp/underlyings', (route) => route.fulfill({ json: { status: 'ok', stocks: [{ symbol: 'APLAPOLLO', lot: 350 }] } }));
  await page.route('**/api/expiry-list/CRUDEOIL', (route) => route.fulfill({ json: { status: 'ok', expiries: ['2026-11-17'] } }));
  await page.route('**/api/expiry-list/APLAPOLLO', (route) => route.fulfill({ json: { status: 'ok', expiries: ['2026-10-27'] } }));
  const listed = [2700, 2760, 2820, 2880, 2940, 3000];
  await page.route('**/api/scalp/contract-spec**', (route) => route.fulfill({ json: { status: 'ok', lot: 350, step: 60, strikes: listed } }));
  await page.route('**/api/scalp/underlying-price**', (route) => {
    const u = new URL(route.request().url()).searchParams.get('underlying');
    return u === 'APLAPOLLO'
      ? route.fulfill({ status: 503, json: { detail: 'No APLAPOLLO share price yet.' } })
      : route.fulfill({ json: { status: 'ok', underlying: u, price: 9350 } });
  });
  await page.route('**/api/option-ltp**', (route) => {
    const q = new URL(route.request().url()).searchParams;
    return route.fulfill({ json: { status: 'ok', ltp: q.get('underlying') === 'CRUDEOIL' ? 575.55 : 0 } });
  });
  await login(page);
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await pick(page, 'CRUDEOIL');
  await expect(page.locator('#scalp-live-ltp')).toHaveText('₹575.55');
  const crudeStrike = await page.locator('#scalp-strike').inputValue();
  expect(Number(crudeStrike)).toBeGreaterThan(8000);
  await page.locator('#scalp-uly .scalp-uly-btn').click();
  await page.click('#scalp-uly .scalp-uly-tab[data-group="Stocks"]');
  await page.click('#scalp-uly .scalp-uly-item[data-value="APLAPOLLO"]');
  await expect.poll(async () => Number(await page.locator('#scalp-strike').inputValue())).not.toBe(Number(crudeStrike));
  expect(listed).toContain(Number(await page.locator('#scalp-strike').inputValue()));
  await expect(page.locator('#scalp-live-ltp')).toHaveText('N/A');
});

test('Scalp: the spot chart button follows the underlying and opens its chart', async ({ page }) => {
  // Phil, 2026-10-21: "I need the respective spot chart on this place instead
  // of only nifty".
  await page.setViewportSize({ width: 1400, height: 1000 });
  await page.route('**/api/scalp/underlyings', (route) => route.fulfill({ json: { status: 'ok', stocks: [] } }));
  await page.route('**/api/expiry-list/BANKNIFTY', (route) => route.fulfill({ json: { status: 'ok', expiries: ['2026-10-27'] } }));
  const asked: string[] = [];
  const t0 = Math.floor(new Date('2026-10-20T09:15:00+05:30').getTime() / 1000);
  await page.route('**/api/scalp/underlying-chart**', (route) => {
    asked.push(new URL(route.request().url()).searchParams.get('underlying')!);
    return route.fulfill({ json: {
      status: 'ok', timeframe: '5m', candles: Array.from({ length: 60 }, (_, i) => ({ t: t0 + i * 300, o: 52000, h: 52010, l: 51990, c: 52000 + i })),
      entries: [], lines: [], overlays: [], live_price: 52059, fit_sessions: 3,
      instrument: { underlying: 'BANKNIFTY', label: 'BANK NIFTY', kind: 'index' },
    } });
  });
  await login(page);
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await pick(page, 'BANKNIFTY');
  const btn = page.locator('#scalp-uly-chart-btn');
  const label = await page.evaluate(() => { const s = document.getElementById('scalp-underlying') as HTMLSelectElement; return s.options[s.selectedIndex].textContent!.trim(); });
  await expect(btn).toHaveText(`↗ ${label} 5m`);
  await btn.click();
  await expect.poll(() => asked).toContain('BANKNIFTY');
  await expect(page.locator('#live-entry-chart-title')).toHaveText('BANK NIFTY');
  await expect(page.locator('#live-entry-chart-body canvas').first()).toBeVisible();
});
