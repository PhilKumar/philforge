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

// The fixture's expiries are fixed dates, so the page's clock is held a week
// before the first: the target date and its steppers read time to expiry, and
// on a real clock this spec would start failing once 13-Oct-2026 had passed.
const FIXTURE_NOW = new Date('2026-10-06T11:05:00+05:30');

async function login(page: Page) {
  await page.clock.setFixedTime(FIXTURE_NOW);
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const pw = page.locator('#password-input');
  if (await pw.isVisible()) { await pw.fill(PIN); await page.click('#unlock-btn'); }
  else { for (const d of PIN.split('')) await page.click(`[data-val="${d}"]`); }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
  // The app then restores the page that was open last; a click before that
  // lands would be undone by it.
  await page.waitForFunction(() => !!(history.state && (history.state as { page?: string }).page));
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
  test.setTimeout(60_000); // the chain, both sliders, four steppers and both order paths
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

  // The steppers: the target moves a whole IST hour, IV 0.1 point; Reset is today.
  await page.click('[data-ob-ana="payoff"]');
  await page.click('[data-ob-act="reset-sliders"]');
  await expect(page.locator('#ob-target-label')).toHaveText('Today');
  // The clock is held at 11:05 IST: + is 12:00, + is 13:00, - is 12:00 again.
  await page.click('[data-ob-step="target:1"]');
  await expect(page.locator('#ob-target-label')).toHaveText('06 Oct, 12:00');
  await page.click('[data-ob-step="target:1"]');
  await expect(page.locator('#ob-target-label')).toHaveText('06 Oct, 13:00');
  await page.click('[data-ob-step="target:-1"]');
  await expect(page.locator('#ob-target-label')).toHaveText('06 Oct, 12:00');
  await page.click('[data-ob-step="iv:1"]');
  await page.click('[data-ob-step="iv:1"]');
  await expect(page.locator('#ob-iv-label')).toHaveText('+0.2 pts');
  await page.click('[data-ob-step="iv:-1"]');
  await page.click('[data-ob-step="iv:-1"]');
  await page.click('[data-ob-step="iv:-1"]');
  await expect(page.locator('#ob-iv-label')).toHaveText('-0.1 pts');
  await expect(page.locator('#ob-iv')).toHaveValue('-0.1');
  await page.click('[data-ob-act="reset-sliders"]');
  await expect(page.locator('#ob-target-label')).toHaveText('Today');
  await expect(page.locator('#ob-iv-label')).toHaveText('+0.0 pts');

  // The four left tabs stay inside their panel.
  const tabsFit = await page.evaluate(() => {
    const tabs = document.querySelector('.ob-left > .ob-tabs') as HTMLElement;
    return tabs.scrollWidth <= tabs.clientWidth
      && tabs.lastElementChild!.getBoundingClientRect().right <= tabs.closest('.ob-left')!.getBoundingClientRect().right;
  });
  expect(tabsFit).toBe(true);

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

test('Option Builder: from a weekend the target steps straight to the next open, and a name being typed survives a refresh', async ({ page }) => {
  const calls: { method: string; url: string; body: any }[] = [];
  await login(page);
  await mockBuilder(page, calls);
  await openBuilder(page);
  await page.click('[data-ob-view="neutral"]');
  await page.click('[data-ob-template="short-straddle"]');
  await expect(page.locator('#ob-legs-table tbody tr')).toHaveCount(2);

  // Sunday 11-Oct-2026, 15:00 IST. An hour later is still Sunday: the first
  // step is Monday's open, where the weekend's decay shows.
  await page.clock.setFixedTime(new Date('2026-10-11T15:00:00+05:30'));
  await page.click('[data-ob-act="reset-sliders"]');
  await expect(page.locator('#ob-target-label')).toHaveText('Today');
  await page.click('[data-ob-step="target:1"]');
  await expect(page.locator('#ob-target-label')).toHaveText('12 Oct, 09:15');
  await page.click('[data-ob-step="target:1"]');
  await expect(page.locator('#ob-target-label')).toHaveText('12 Oct, 10:00');
  await page.click('[data-ob-step="target:-1"]');
  await page.click('[data-ob-step="target:-1"]');
  await expect(page.locator('#ob-target-label')).toHaveText('Today'); // Friday's close is behind us

  // A portfolio name being typed is not put back by the price refresh.
  await page.click('[data-ob-left="paper"]');
  await page.click('[data-ob-act="new-portfolio"]');
  const name = page.locator('#ob-left-body .ob-naming input');
  await name.fill('Swing ideas');
  const chains = () => calls.filter((c) => c.url.endsWith('/chain')).length;
  const before = chains();
  await page.locator('[data-ob-exp]').first().evaluate((el) => (el as HTMLElement).click()); // a refresh, as the poll does
  await expect.poll(chains).toBeGreaterThan(before);
  await expect(name).toHaveValue('Swing ideas');
  await name.press('End');
  await name.pressSequentially(' 2');
  await expect(name).toHaveValue('Swing ideas 2');
});

test('Option Builder: Exit closes a held position on one confirm, with no code asked, and is not offered twice', async ({ page }) => {
  const calls: { method: string; url: string; body: any }[] = [];
  await login(page);
  await mockBuilder(page, calls);
  const leg = (broker: string, strike: number, side: string, product: string) => ({
    key: `${broker}|NIFTY|${strike}|2026-10-13|PE|${product}`,
    underlying: 'NIFTY', strike, expiry: '2026-10-13', option_type: 'PE', side, quantity: 75, price: 100, broker, product,
  });
  const held = [leg('zerodha', 24900, 'BUY', 'MIS'), leg('dhan', 25000, 'SELL', 'INTRADAY')];
  await page.route('**/api/option-builder/positions**', (route) => route.fulfill({ json: { status: 'ok', legs: held, notes: [] } }));
  await page.route('**/api/option-builder/exit', async (route) => {
    const body = JSON.parse(route.request().postData() as string);
    calls.push({ method: 'POST', url: '/api/option-builder/exit', body });
    await route.fulfill({ json: { status: 'ok', sent: body.keys.length, results: body.keys.map((key: string) => ({ key, status: 'sent' })) } });
  });
  await openBuilder(page);
  await page.click('[data-ob-left="positions"]');
  await page.click('[data-ob-act="load-positions"]');
  await expect(page.locator('.ob-pos')).toHaveCount(2);
  const exits = () => calls.filter((c) => c.url.endsWith('/exit'));

  // The button sits inside its row.
  const fits = await page.locator('.ob-pos').first().evaluate((row) => {
    const btn = row.querySelector('.ob-pos-exit')!.getBoundingClientRect();
    const box = row.getBoundingClientRect();
    return btn.right <= box.right && btn.left >= box.left && row.scrollWidth <= row.clientWidth;
  });
  expect(fits).toBe(true);

  // Cancelling sends nothing.
  await page.click(`[data-ob-exit="${held[1].key}"]`);
  await expect(page.locator('#confirm-title')).toHaveText('Exit 1 position?');
  await expect(page.locator('#confirm-message')).toContainText('BUY'); // the short is bought back
  await page.locator('#confirm-modal').getByRole('button', { name: /cancel/i }).click();
  expect(exits()).toHaveLength(0);

  // One confirm, and the request only names the position: no side, no size.
  await page.click(`[data-ob-exit="${held[1].key}"]`);
  await page.locator('#confirm-modal').getByRole('button', { name: 'Exit now' }).click();
  await expect.poll(() => exits().length).toBe(1);
  expect(exits()[0].body).toEqual({ keys: [held[1].key] });
  await expect(page.locator('#action-auth-modal')).toBeHidden();
  const sentRow = page.locator('.ob-pos').nth(1).locator('.ob-pos-exit');
  await expect(sentRow).toHaveText('Exit sent');
  await expect(sentRow).toBeDisabled();

  // Exit all leaves out the one just sent.
  await page.click('[data-ob-act="exit-all"]');
  await expect(page.locator('#confirm-title')).toHaveText('Exit 1 position?');
  await page.locator('#confirm-modal').getByRole('button', { name: 'Exit now' }).click();
  await expect.poll(() => exits().length).toBe(2);
  expect(exits()[1].body).toEqual({ keys: [held[0].key] });
  expect(calls.some((c) => c.url.endsWith('/execute'))).toBe(false);
});

test('Option Builder: an opened strategy takes a new strike instead of being copied', async ({ page }) => {
  // Phil, 2026-10-15: "I cannot add some strategies or strikes to the existing
  // portfolio strategies" -- Add to portfolio made a duplicate straddle.
  test.setTimeout(60_000);
  const calls: { method: string; url: string; body: any }[] = [];
  await login(page);
  const straddle = {
    id: 2, name: 'Short Straddle', underlying: 'NIFTY', lot_size: 75, status: 'open', portfolio_id: 1,
    opened_at: '2026-10-06T10:00:00+05:30',
    legs: [
      { underlying: 'NIFTY', side: 'SELL', strike: 25000, option_type: 'CE', expiry: '2026-10-13', lots: 1, entry: 110.5 },
      { underlying: 'NIFTY', side: 'SELL', strike: 25000, option_type: 'PE', expiry: '2026-10-13', lots: 1, entry: 98.25 },
    ],
  };
  await mockBuilder(page, calls);
  // After mockBuilder: Playwright tries the last route registered first.
  await page.route('**/api/option-builder/paper', (route) => route.request().method() === 'GET'
    ? route.fulfill({ json: { status: 'ok', baskets: [straddle], portfolios: [{ id: 1, name: 'Short2' }], saved: [] } })
    : route.fallback());
  await openBuilder(page);

  await page.click('[data-ob-left="paper"]');
  await page.locator('[data-ob-pf-toggle="1"]').first().click();
  await page.click('[data-ob-paper-load="2"]');
  const add = page.locator('[data-ob-act="paper"]');
  await expect(add).toHaveText('Add to Short Straddle');
  await expect(page.locator('[data-ob-act="paper-new"]')).toBeVisible();

  // Nothing new yet: nothing is sent, and the straddle is not copied.
  await add.click();
  expect(calls.some((c) => c.method === 'POST' && c.url.includes('/paper'))).toBe(false);

  // A hedge from the chain joins the straddle, alone, at its live price.
  await page.click('[data-ob-left="chain"]');
  await page.click('[data-ob-add="BUY:CE:25300"]');
  await add.click();
  await expect.poll(() => calls.find((c) => c.method === 'POST' && c.url.endsWith('/paper/2/legs'))?.body.legs).toHaveLength(1);
  const sent = calls.find((c) => c.url.endsWith('/paper/2/legs'))!.body.legs[0];
  expect([sent.side, sent.strike, sent.option_type, sent.lots]).toEqual(['BUY', 25300, 'CE', 1]);
  expect(sent.price).toBeGreaterThan(0);
  expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/option-builder/paper'))).toBe(false);

  // Clear goes back to making a new strategy.
  await page.click('[data-ob-act="clear"]');
  await expect(add).toHaveText('Add to portfolio');
  await expect(page.locator('[data-ob-act="paper-new"]')).toBeHidden();
});

test('Option Builder: B in the chain adds, again takes it back, and − lots + sizes it', async ({ page }) => {
  // Phil, 2026-10-15: "when I click B it has to add the buy and if I again
  // click it has to undo ... a number, + on the right side and - on the left".
  const calls: { method: string; url: string; body: any }[] = [];
  await login(page);
  await mockBuilder(page, calls);
  await openBuilder(page);
  await page.click('[data-ob-left="chain"]');
  const legs = () => page.evaluate(() => (window as any).PFOptionBuilder.state.legs.map((l: any) => `${l.side}:${l.type}:${l.strike}:${l.lots}`));

  await page.click('[data-ob-add="BUY:CE:25100"]');
  expect(await legs()).toEqual(['BUY:CE:25100:1']);
  const held = page.locator('.ob-chain-row[data-strike="25100"] .ob-c-act.is-held').first();
  await expect(held.locator('[data-ob-undo="BUY:CE:25100"]')).toHaveText('B1');
  await expect(held.locator('button')).toHaveText(['−', 'B1', '+']);

  await held.locator('[data-ob-lots="BUY:CE:25100:1"]').click();
  await page.locator('.ob-chain-row[data-strike="25100"] [data-ob-lots="BUY:CE:25100:1"]').click();
  expect(await legs()).toEqual(['BUY:CE:25100:3']);
  await page.locator('.ob-chain-row[data-strike="25100"] [data-ob-lots="BUY:CE:25100:-1"]').click();
  expect(await legs()).toEqual(['BUY:CE:25100:2']);

  // The same button again takes the leg back; B and S return.
  await page.locator('[data-ob-undo="BUY:CE:25100"]').click();
  expect(await legs()).toEqual([]);
  await expect(page.locator('[data-ob-add="BUY:CE:25100"]')).toBeVisible();

  // S works the same, and − at one lot also removes it.
  await page.click('[data-ob-add="SELL:PE:24900"]');
  await expect(page.locator('[data-ob-undo="SELL:PE:24900"]')).toHaveText('S1');
  await page.locator('[data-ob-lots="SELL:PE:24900:-1"]').click();
  expect(await legs()).toEqual([]);
});

test('Option Builder: crude and gas from the Commodity menu, any F&O stock from a searched Stocks menu', async ({ page }) => {
  // Phil, 2026-10-16: "work on the option builder as well with these commodity and stocks addition".
  const calls: { method: string; url: string; body: any }[] = [];
  await login(page);
  await mockBuilder(page, calls);
  await page.route('**/api/scalp/underlyings', (route) => route.fulfill({ json: { status: 'ok', stocks: [
    { symbol: 'BAJAJ-AUTO', lot: 75 }, { symbol: 'RELIANCE', lot: 500 }, { symbol: 'SBIN', lot: 750 },
  ] } }));
  await openBuilder(page);
  const asked = () => calls.filter((c) => c.url.endsWith('/expiries')).length;

  // The Commodity menu opens, and folds on a click elsewhere.
  await page.click('[data-ob-menu="mcx"]');
  await expect(page.locator('[data-ob-panel="mcx"] .ob-menu-item b')).toHaveText(['CRUDEOIL', 'CRUDEOILM', 'NATURALGAS', 'NATGASMINI']);
  await page.locator('#ob-spot').click();
  await expect(page.locator('[data-ob-panel="mcx"]')).toBeHidden();

  const before = asked();
  await page.click('[data-ob-menu="mcx"]');
  const [req] = await Promise.all([
    page.waitForRequest((r) => r.url().includes('/api/option-builder/expiries') && r.url().includes('underlying=CRUDEOIL')),
    page.click('[data-ob-panel="mcx"] [data-ob-und="CRUDEOIL"]'),
  ]);
  expect(req).toBeTruthy();
  expect(asked()).toBeGreaterThan(before);
  await expect(page.locator('[data-ob-panel="mcx"]')).toBeHidden();
  await expect(page.locator('[data-ob-menu="mcx"]')).toHaveText('CRUDEOIL ▾');
  await expect(page.locator('[data-ob-menu="mcx"]')).toHaveClass(/is-active/);
  await expect(page.locator('[data-ob-und="NIFTY"]')).not.toHaveClass(/is-active/);

  // Stocks: searched, then picked.
  await page.click('[data-ob-menu="stocks"]');
  await expect(page.locator('#ob-stock-list .ob-menu-item')).toHaveCount(3);
  await page.locator('.ob-menu-search').fill('rel');
  await expect(page.locator('#ob-stock-list .ob-menu-item')).toHaveCount(1);
  await expect(page.locator('#ob-stock-list .ob-menu-item small')).toHaveText('Lot 500');
  await Promise.all([
    page.waitForRequest((r) => r.url().includes('/api/option-builder/chain') && r.url().includes('underlying=RELIANCE')),
    page.click('#ob-stock-list [data-ob-und="RELIANCE"]'),
  ]);
  await expect(page.locator('[data-ob-menu="stocks"]')).toHaveText('RELIANCE ▾');
  await expect(page.locator('[data-ob-menu="mcx"]')).toHaveText('Commodity ▾');
  // Back to an index in one click.
  await page.click('[data-ob-und="NIFTY"]');
  await expect(page.locator('[data-ob-menu="stocks"]')).toHaveText('Stocks ▾');
});
