import { test, expect, Page } from '@playwright/test';
import fs from 'fs';
import path from 'path';

// The Option Builder on a synthetic NIFTY chain (fixtures/option-builder-chain.json:
// Black-Scholes prices with a skew, spot 25,012.4, lot 75). Every
// /api/option-builder call is answered here, so the page is tested end to end
// without a broker -- and no order can leave the test.

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';
const CHAIN = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'option-builder-chain.json'), 'utf8'));

async function login(page: Page) {
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const pw = page.locator('#password-input');
  if (await pw.isVisible()) { await pw.fill(PIN); await page.click('#unlock-btn'); }
  else { for (const d of PIN.split('')) await page.click(`[data-val="${d}"]`); }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
}

async function mockBuilder(page: Page, calls: { method: string; url: string; body: any }[]) {
  await page.route('**/api/option-builder/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const body = req.postData() ? JSON.parse(req.postData() as string) : null;
    calls.push({ method: req.method(), url: url.pathname, body });
    const json = (data: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
    if (url.pathname.endsWith('/expiries')) return json({ status: 'ok', expiries: Object.keys(CHAIN) });
    if (url.pathname.endsWith('/chain')) return json(CHAIN[url.searchParams.get('expiry') || '2026-10-13']);
    if (url.pathname.endsWith('/margin')) return json({ status: 'ok', lot_size: 75, total: 41250, hedge_benefit: 98000, available: 400000 });
    if (url.pathname.endsWith('/paper') && req.method() === 'GET') return json({ status: 'ok', baskets: [] });
    if (url.pathname.endsWith('/positions')) return json({ status: 'ok', legs: [], notes: [] });
    return json({ status: 'ok' });
  });
}

async function openBuilder(page: Page) {
  await page.evaluate(() => { try { localStorage.removeItem('philforge_option_builder_v1'); } catch (_) {} });
  await page.locator('[data-pf-trading-page="option-builder-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#option-builder-page')).toHaveClass(/active-page/);
  await expect(page.locator('#ob-spot')).toHaveText('25,012.4');
}

test('Option Builder: an iron condor shows the right numbers and a drawn payoff', async ({ page }) => {
  const calls: { method: string; url: string; body: any }[] = [];
  await login(page);
  await mockBuilder(page, calls);
  await openBuilder(page);

  // Trading has four desks, in Phil's order.
  const tabs = page.locator('#option-builder-page .trading-section-tab strong');
  await expect(tabs).toHaveText(['Options', 'Scalp', 'Option Builder', 'Equity']);
  expect(new URL(page.url()).hash).toBe('#trading/builder');

  await page.click('[data-ob-view="neutral"]');
  await page.click('[data-ob-template="iron-condor"]');
  await expect(page.locator('#ob-legs-table tbody tr')).toHaveCount(4);
  await expect(page.locator('#ob-strategy-name')).toHaveText('Iron Condor');

  // Wings 100 points wide on NIFTY: profit + loss = 100 x 75.
  const stats = await page.locator('.ob-stat b').allTextContents();
  const rupees = (s: string) => Number(s.replace(/[^0-9.-]/g, ''));
  expect(rupees(stats[0]) + Math.abs(rupees(stats[1]))).toBeCloseTo(7500, 0);
  await expect(page.locator('.ob-stat').nth(2)).toContainText('·'); // two breakevens
  await expect(page.locator('.ob-stat').nth(6)).toContainText('₹41,250');

  // The payoff spans the whole desk, under the ready-made panel too.
  const widths = await page.evaluate(() => [
    document.querySelector('.ob-analysis')!.getBoundingClientRect().width,
    document.querySelector('.ob-grid')!.getBoundingClientRect().width,
  ]);
  expect(widths[0]).toBeCloseTo(widths[1], 0);

  // Dhan's margin was asked for the whole basket at once, hedges included.
  const margin = calls.find((c) => c.url.endsWith('/margin'));
  expect(margin?.body.legs).toHaveLength(4);

  // The canvas is painted: count non-transparent pixels.
  const painted = await page.locator('#ob-chart').evaluate((c: HTMLCanvasElement) => {
    const d = c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data;
    let n = 0; for (let i = 3; i < d.length; i += 16) if (d[i] > 0) n++;
    return n;
  });
  expect(painted).toBeGreaterThan(2000);
  await expect(page.locator('#ob-chart-empty')).toBeHidden();

  // Hover reads the P&L at that price.
  await expect(page.locator('#ob-tip')).toBeHidden();
  // Bottom of the chart in view: the frozen header and banner cover the top
  // of a short window, and the pointer must land on the canvas itself.
  await page.locator('#ob-chart').evaluate((el) => el.scrollIntoView({ block: 'end' }));
  const box = (await page.locator('#ob-chart').boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.8);
  await expect(page.locator('#ob-tip')).toBeVisible();
  await expect(page.locator('#ob-tip')).toContainText('On expiry');
});

test('Option Builder: chain buttons add legs, sliders move the target curve, nothing is sent live without a confirm', async ({ page }) => {
  const calls: { method: string; url: string; body: any }[] = [];
  await login(page);
  await mockBuilder(page, calls);
  await openBuilder(page);

  await page.click('[data-ob-left="chain"]');
  await expect(page.locator('.ob-chain-row.is-atm')).toContainText('25,000');
  await page.click('[data-ob-add="BUY:CE:25000"]');
  await page.click('[data-ob-add="SELL:CE:25200"]');
  await expect(page.locator('#ob-strategy-name')).toHaveText('Bull Call Spread');
  await expect(page.locator('.ob-stat').nth(2)).toContainText('25,089.7');

  // The target-date slider changes the label and the P&L table.
  await page.click('[data-ob-ana="table"]');
  const before = await page.locator('#ob-pnl-table tr.is-spot td').nth(2).textContent();
  await page.locator('#ob-target').evaluate((el: HTMLInputElement) => { el.value = '900'; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await expect(page.locator('#ob-target-label')).not.toHaveText('Today');
  await expect(page.locator('#ob-pnl-table tr.is-spot td').nth(2)).not.toHaveText(before || '');

  // Greeks are summed per strategy.
  await page.click('[data-ob-ana="greeks"]');
  await expect(page.locator('.ob-greek-cards > div')).toHaveCount(4);

  // Live asks first; cancelling sends nothing.
  await page.click('[data-ob-act="live"]');
  await expect(page.locator('#confirm-title')).toHaveText('Place 2 live orders?');
  await expect(page.locator('#confirm-message')).toContainText('Buys go first');
  await page.locator('#confirm-modal').getByRole('button', { name: /cancel/i }).click();
  expect(calls.some((c) => c.url.endsWith('/execute'))).toBe(false);

  // Paper sends the legs at their entry prices.
  await page.click('[data-ob-act="paper"]');
  await expect.poll(() => calls.find((c) => c.url.endsWith('/paper') && c.method === 'POST')?.body.legs.length).toBe(2);
});
