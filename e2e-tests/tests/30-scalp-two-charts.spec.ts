import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-29: "build two charts on one screen ... When I click on the
// left screen, it has to be CE controls enabled and if I click on the right,
// PE controls has to be enabled ... The eye and move buttons ... has to be
// also for the B and S button". The Scalp desk's layout menu offers one
// chart, CE | PE side by side, or CE over PE; the pane of the form's side is
// the one its B/S, lines and tools speak to, and a click on the other turns
// the form to that side.

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

// CE climbs from 140, PE from 300, so each pane's candles are its own.
function bars(base: number) {
  const open = ist('2026-10-14T09:15:00');
  const out = [];
  for (let i = 0; i < 60; i++) out.push({ t: open + i * 60, o: base + (i % 7), h: base + 4 + (i % 7), l: base - 2 + (i % 7), c: base + 1 + (i % 7) });
  return out;
}

async function twoCharts(page: Page, layout: 'cols' | 'rows') {
  // Each request answers for the contract it asks for.
  await page.route('**/api/scalp/contract-chart**', (route) => {
    const q = new URL(route.request().url()).searchParams;
    const side = q.get('option_type') || 'CE';
    const base = side === 'PE' ? 300 : 140;
    route.fulfill({ json: {
      status: 'ok', timeframe: q.get('timeframe') || '1m', candles: bars(base), entries: [], overlays: [], live_price: base,
      lines: [{ label: 'LIVE', price: base, filled: true, color: '#e2e8f0', dash: [2, 3] }],
      instrument: { underlying: 'NIFTY', strike: Number(q.get('strike')), option_type: side, expiry: q.get('expiry'), security_id: '1' },
    } });
  });
  await page.route('**/api/option-ltp**', (route) => route.fulfill({ json: { status: 'ok', ltp: 150 } }));
  await page.route('**/api/expiry-list/**', (route) => route.fulfill({ json: { status: 'ok', expiries: ['2026-10-19'] } }));
  await page.addInitScript((l) => { try { localStorage.setItem('pf-scalp-chart-layout', l); localStorage.setItem('pf-scalp-lines', '{}'); } catch (_) {} }, layout);
  await login(page);
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);
  // The saved form is restored first, so it cannot land on top of this one.
  await page.waitForFunction('_scalpFormInitialized === true');
  await page.evaluate(() => {
    const w = window as unknown as { _scalpOptionChartTf: string; _syncScalpContractChartLabel: () => void };
    const sel = document.getElementById('scalp-expiry') as HTMLSelectElement;
    sel.innerHTML = '<option value="2026-10-19">19 Oct</option>';
    sel.value = '2026-10-19';
    (document.getElementById('scalp-underlying') as HTMLSelectElement).value = 'NIFTY';
    (document.getElementById('scalp-option-type') as HTMLInputElement).value = 'CE';
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '22700';
    w._scalpOptionChartTf = '1m';
    w._syncScalpContractChartLabel();
  });
  await expect(page.locator('#scalp-split .scalp-pane canvas')).toHaveCount(4);
}

// Which side each live chart shows, and which one the page is pointed at.
const charts = (page: Page) => page.evaluate(() => {
  type C = { host: HTMLElement; data: { instrument: { option_type: string } } };
  const w = window as unknown as { _pfChartCanvases: C[]; _pfChartCanvas: C };
  const side = (c: C | null) => (c && (c.host.closest('.scalp-pane') as HTMLElement | null)?.dataset.opt) || '';
  return {
    panes: w._pfChartCanvases.map(c => `${side(c)}:${c.data.instrument.option_type}`).sort(),
    focused: side(w._pfChartCanvas),
  };
});

test('the layout menu puts CE beside PE, each pane charting its own side', async ({ page }) => {
  await twoCharts(page, 'cols');
  await expect(page.locator('#scalp-split')).toHaveAttribute('data-layout', 'cols');
  const panes = page.locator('#scalp-split .scalp-pane');
  await expect(panes.nth(0)).toHaveAttribute('data-opt', 'CE');
  await expect(panes.nth(1)).toHaveAttribute('data-opt', 'PE');
  await expect(panes.nth(0)).toHaveClass(/is-active/);
  await expect.poll(() => charts(page)).toEqual({ panes: ['CE:CE', 'PE:PE'], focused: 'CE' });
  const rects = () => page.evaluate(() => [...document.querySelectorAll('#scalp-split .scalp-pane')].map((el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height };
  }));
  const [a, b] = await rects();
  expect(b.x).toBeGreaterThan(a.x + a.width - 1);
  expect(Math.abs(b.y - a.y)).toBeLessThan(1);
  // The B/S trade the form's CE.
  await expect(page.locator('#scalp-ticket-buy')).toBeEnabled();
  await expect(page.locator('#scalp-dock-chart-title')).toContainText('22700CE');

  // The menu: 1, then 2 side by side or stacked; the stacked one stacks.
  await page.click('#scalp-layout-btn');
  await expect(page.locator('#scalp-layout-menu')).toBeVisible();
  await expect(page.locator('#scalp-layout-menu [data-layout="cols"]')).toHaveClass(/is-on/);
  await page.click('#scalp-layout-menu [data-layout="rows"]');
  await expect(page.locator('#scalp-layout-menu')).toBeHidden();
  await expect(page.locator('#scalp-split')).toHaveAttribute('data-layout', 'rows');
  await expect.poll(() => charts(page)).toEqual({ panes: ['CE:CE', 'PE:PE'], focused: 'CE' });
  const [top, bottom] = await rects();
  expect(bottom.y).toBeGreaterThan(top.y + top.height - 1);
  expect(Math.abs(bottom.x - top.x)).toBeLessThan(1);

  // And back to one chart.
  await page.click('#scalp-layout-btn');
  await page.click('#scalp-layout-menu [data-layout="one"]');
  await expect(page.locator('#scalp-split')).toHaveCount(0);
  await expect(page.locator('#scalp-dock-chart-canvas canvas')).toHaveCount(2);
  expect(await page.evaluate(() => (window as unknown as { _pfChartCanvases: unknown[] })._pfChartCanvases.length)).toBe(1);
});

test('a click on the PE pane turns the form to PE, and that chart takes the ticket without a redraw', async ({ page }) => {
  await twoCharts(page, 'cols');
  await expect.poll(() => charts(page)).toEqual({ panes: ['CE:CE', 'PE:PE'], focused: 'CE' });
  // The PE chart already on screen is the one that becomes the page's chart.
  await page.evaluate(() => {
    type C = { host: HTMLElement };
    const w = window as unknown as { _pfChartCanvases: C[]; __pe: C };
    w.__pe = w._pfChartCanvases.find(c => (c.host.closest('.scalp-pane') as HTMLElement).dataset.opt === 'PE')!;
  });
  // At the top of the page, where the pane is in view and nothing folds.
  await page.evaluate(() => window.scrollTo(0, 0));
  const pe = page.locator('#scalp-split .scalp-pane[data-opt="PE"]');
  const box = (await pe.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + 100);
  await expect(page.locator('#scalp-option-type')).toHaveValue('PE');
  await expect(pe).toHaveClass(/is-active/);
  await expect(page.locator('#scalp-split .scalp-pane[data-opt="CE"]')).not.toHaveClass(/is-active/);
  expect(await page.evaluate(() => {
    const w = window as unknown as { _pfChartCanvas: unknown; __pe: unknown };
    return w._pfChartCanvas === w.__pe;
  })).toBe(true);
  await expect(page.locator('#scalp-dock-chart-title')).toContainText('22700PE');
  await expect(page.locator('#scalp-ticket-buy')).toBeEnabled();
  // The ticket now sits over the PE pane.
  const ticket = (await page.locator('#scalp-ticket').boundingBox())!;
  expect(ticket.x).toBeGreaterThan(box.x);
  // CE stays drawn beside it, still CE.
  await expect.poll(() => charts(page)).toEqual({ panes: ['CE:CE', 'PE:PE'], focused: 'PE' });

  // The form's own CE button brings the outline back to the left.
  await page.evaluate(() => (window as unknown as { setScalpOptionType: (v: string) => void }).setScalpOptionType('CE'));
  await expect(page.locator('#scalp-split .scalp-pane[data-opt="CE"]')).toHaveClass(/is-active/);
  await expect.poll(() => charts(page)).toEqual({ panes: ['CE:CE', 'PE:PE'], focused: 'CE' });
});

// Phil, 2026-10-30: "The charts are not staying on the strike price I
// select... It defaults to a different strike".
test('each side keeps the strike picked for it, and the other pane does not follow the index', async ({ page }) => {
  await twoCharts(page, 'cols');
  type W = { setScalpOptionType: (v: string) => void; _scalpAltContract: () => { strike: number; option_type: string } };
  const pick = (strike: string) => page.evaluate((s) => {
    const inp = document.getElementById('scalp-strike') as HTMLInputElement;
    inp.value = s;
    inp.dispatchEvent(new Event('input', { bubbles: true }));
  }, strike);
  const side = (v: string) => page.evaluate((s) => (window as unknown as W).setScalpOptionType(s), v);
  const other = () => page.evaluate(() => (window as unknown as W)._scalpAltContract());
  const strike = page.locator('#scalp-strike');

  await page.evaluate('_scalpSpotCache.NIFTY = 22810; _scalpSideStrikes = {};');
  await pick('22650');
  // The PE pane starts at its in-the-money strike...
  expect(await other()).toMatchObject({ option_type: 'PE', strike: 22900 });
  // ...and stays there as the index moves.
  await page.evaluate('_scalpSpotCache.NIFTY = 23110');
  expect(await other()).toMatchObject({ option_type: 'PE', strike: 22900 });

  // PE opens on the strike its pane showed; a strike picked there is PE's.
  await side('PE');
  await expect(strike).toHaveValue('22900');
  await pick('23000');
  // Back to CE: the strike picked for CE, not a new in-the-money one.
  await side('CE');
  await expect(strike).toHaveValue('22650');
  expect(await other()).toMatchObject({ option_type: 'PE', strike: 23000 });
  await side('PE');
  await expect(strike).toHaveValue('23000');
  // And across a reload.
  await page.reload();
  await page.waitForFunction(() => typeof (window as unknown as { _scalpKeptStrike?: unknown })._scalpKeptStrike === 'function');
  expect(await page.evaluate(() => (window as unknown as { _scalpKeptStrike: (u: string, e: string, o: string) => number })
    ._scalpKeptStrike('NIFTY', '2026-10-19', 'CE'))).toBe(22650);
});

test('the eye hides B/S with the trade lines, and the move button swaps their sides', async ({ page }) => {
  await twoCharts(page, 'cols');
  const ticket = page.locator('#scalp-ticket');
  const pills = page.locator('#scalp-chart-pills');
  await expect(ticket).toBeVisible();
  await page.click('#scalp-lines-eye');
  await expect(ticket).toBeHidden();
  await expect(pills).toHaveClass(/is-hidden/);
  await page.click('#scalp-lines-eye');
  await expect(ticket).toBeVisible();

  const before = (await ticket.boundingBox())!;
  await page.click('#scalp-lines-side');
  await expect(ticket).toHaveClass(/is-left/);
  await expect(pills).toHaveClass(/is-right/);
  expect((await ticket.boundingBox())!.x).toBeLessThanOrEqual(before.x);
  await page.click('#scalp-lines-side');
  await expect(ticket).not.toHaveClass(/is-left/);
  await expect(pills).not.toHaveClass(/is-right/);
});

test('full screen keeps the Scalp controls in a column beside the chart; ▸ leaves the chart alone', async ({ page }) => {
  // Phil, 2026-10-29: "Make the scalp controls on the right to be visible on
  // the full screen as well".
  await page.setViewportSize({ width: 1600, height: 900 });
  await twoCharts(page, 'cols');
  await page.evaluate(() => (window as unknown as { toggleScalpChartFull: () => void }).toggleScalpChartFull());
  await expect(page.locator('body')).toHaveClass(/scalp-chart-full/);
  const box = (sel: string) => page.locator(sel).first().evaluate((el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
  });
  await expect.poll(async () => (await box('#scalp-dock-chart')).height).toBeGreaterThan(820);
  const chart = await box('#scalp-dock-chart');
  const form = await box('#scalp-page .ocp-pane-form');
  const positions = await box('#scalp-page .scalp-positions-card');
  // The chart from the top to the bottom of the screen, the controls to its right.
  expect(chart.y).toBeLessThan(20);
  expect(chart.bottom).toBeGreaterThan(880);
  expect(form.x).toBeGreaterThan(chart.right);
  expect(positions.x).toBeGreaterThan(chart.right);
  expect(positions.height).toBeGreaterThan(60);
  expect(form.y).toBeGreaterThan(positions.bottom - 1);
  await expect(page.locator('#scalp-page .ocp-pane-form button', { hasText: 'BUY' }).first()).toBeVisible();

  // ▸ hides the column: the chart takes the whole screen.
  await page.click('#scalp-rail-toggle');
  await expect.poll(async () => (await box('#scalp-dock-chart')).width).toBeGreaterThan(1500);
  await page.click('#scalp-rail-toggle');
  await expect.poll(async () => (await box('#scalp-page .ocp-pane-form')).x).toBeGreaterThan(1000);
});
