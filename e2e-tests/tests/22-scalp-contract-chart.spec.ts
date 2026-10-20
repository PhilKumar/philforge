import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-13: "put the corresponding options chart near the broker so
// that I can find the particular chart while taking trade". The Scalp chart
// used to need an open trade to name its contract; this button charts the
// option the entry form is pointing at, before any order.

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';

async function login(page: Page) {
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const pw = page.locator('#password-input');
  if (await pw.isVisible()) { await pw.fill(PIN); await page.click('#unlock-btn'); }
  else { for (const d of PIN.split('')) await page.click(`[data-val="${d}"]`); }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
  await page.waitForFunction(() => !!(history.state && (history.state as { page?: string }).page));
}

test('Scalp: the option chart button beside the broker charts the contract in the form', async ({ page }) => {
  test.setTimeout(60_000); // the Scalp page waits on its expiry list before the form is usable
  await login(page);
  const asked: URL[] = [];
  const t0 = Math.floor(new Date('2026-10-13T09:15:00+05:30').getTime() / 1000);
  const candles = Array.from({ length: 30 }, (_, i) => {
    const o = 120 + Math.sin(i / 3) * 15;
    return { t: t0 + i * 300, o, h: o + 4, l: o - 4, c: o + 2 };
  });
  await page.route('**/api/scalp/contract-chart**', async (route) => {
    const url = new URL(route.request().url());
    asked.push(url);
    const live = Number(url.searchParams.get('live_price') || 0);
    await route.fulfill({
      json: {
        status: 'ok', timeframe: url.searchParams.get('timeframe'), candles, entries: [], overlays: [],
        lines: live > 0 ? [{ label: 'LIVE', price: live, filled: true, color: '#e2e8f0', dash: [2, 3] }] : [],
        live_price: live,
        instrument: {
          underlying: url.searchParams.get('underlying'), strike: Number(url.searchParams.get('strike')),
          option_type: url.searchParams.get('option_type'), expiry: url.searchParams.get('expiry'), security_id: '1',
        },
      },
    });
  });
  await page.route('**/api/option-ltp**', (route) => route.fulfill({ json: { status: 'ok', ltp: 131.5 } }));

  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);
  // Phil, 2026-10-15: "no need of this button" -- with the chart docked
  // beside the form it has nothing to do, so a two-column window hides it.
  const btn = page.locator('#scalp-contract-chart-btn');
  await expect(btn).toBeHidden();

  // Point the form at a contract, as picking a strike does. The expiry is
  // whichever one the page has loaded for itself.
  await page.waitForFunction(() => !!(document.getElementById('scalp-expiry') as HTMLSelectElement).value);
  const expiry = await page.evaluate(() => {
    const w = window as unknown as { _scalpCurrentLTP: number; _syncScalpContractChartLabel: () => void };
    (document.getElementById('scalp-underlying') as HTMLSelectElement).value = 'NIFTY';
    (document.getElementById('scalp-option-type') as HTMLInputElement).value = 'PE';
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '22700';
    w._scalpCurrentLTP = 131.5;
    (window as unknown as { _scalpLTPKey: string })._scalpLTPKey = `form:NIFTY|22700|${(document.getElementById('scalp-expiry') as HTMLSelectElement).value}|PE`;
    w._syncScalpContractChartLabel();
    return (document.getElementById('scalp-expiry') as HTMLSelectElement).value;
  });
  await expect(page.locator('#scalp-contract-chart-label')).toHaveText('22700 PE chart');

  // Phil, 2026-10-15: on a two-column window the chart sits beside the form
  // and follows it, with no click.
  const dock = page.locator('#scalp-dock-chart');
  await expect(dock).toBeVisible();
  await expect(page.locator('#scalp-option-chart-overlay.is-open')).toHaveCount(0);
  await expect(page.locator('#scalp-dock-chart-title')).toHaveText('NIFTY 22700PE');
  await expect(page.locator('#scalp-dock-chart-body canvas').first()).toBeVisible();
  // The heading is the expiry and the timeframe, nothing more.
  await expect(page.locator('#scalp-dock-chart-meta')).toHaveText(/^Exp \d{2} \w{3} · 5M$/);
  const sideBySide = await page.evaluate(() => {
    const form = document.querySelector('#scalp-page .scalp-entry-card')!.getBoundingClientRect();
    const d = document.getElementById('scalp-dock-chart')!.getBoundingClientRect();
    // Chart on the left, the form on the right (Phil, 2026-10-20).
    return d.right <= form.left && d.width > 300 && d.height > 400;
  });
  expect(sideBySide).toBe(true);
  const first = asked.find((u) => u.searchParams.get('strike') === '22700')!.searchParams;
  expect([first.get('underlying'), first.get('strike'), first.get('expiry'), first.get('option_type'), first.get('timeframe')])
    .toEqual(['NIFTY', '22700', expiry, 'PE', '5m']);
  expect(first.get('live_price')).toBe('131.5');

  // 1m and 3m sit before the three it always had, and each is asked for the
  // same contract.
  const strip = page.locator('#scalp-dock-chart-actions');
  await expect(strip.getByRole('radio')).toHaveText(['1M', '3M', '5M', '15M', '1H']);
  for (const tf of ['1m', '3m', '15m']) {
    await strip.getByRole('radio', { name: new RegExp(`^${tf}$`, 'i') }).click();
    await expect.poll(() => asked.some((u) => u.searchParams.get('timeframe') === tf && u.searchParams.get('strike') === '22700'), { message: tf }).toBe(true);
    await expect(page.locator('#scalp-dock-chart-body canvas').first()).toBeVisible();
  }

  // A new strike in the form is followed without a click.
  await page.evaluate(() => {
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '22750';
    (window as unknown as { _syncScalpContractChartLabel: () => void })._syncScalpContractChartLabel();
  });
  await expect(page.locator('#scalp-dock-chart-title')).toHaveText('NIFTY 22750PE');

  // Once a trade is open on that contract, the dock shows the trade (it
  // carries the entry, target and stop); when it closes, back to the form.
  await page.route('**/api/scalp/trades/7/chart**', (route) => route.fulfill({
    json: {
      status: 'ok', timeframe: '5m', candles, entries: [], overlays: [], lines: [], live_price: 0,
      instrument: { underlying: 'NIFTY', strike: 22750, option_type: 'PE', expiry, security_id: '1' },
    },
  }));
  const trade = { trade_id: 7, status: 'open', underlying: 'NIFTY', strike: 22750, option_type: 'PE', expiry };
  await page.evaluate((t) => (window as unknown as { _scalpDockFollow: (o: unknown[]) => void })._scalpDockFollow([t]), trade);
  await expect(page.locator('#scalp-dock-chart-title')).toHaveText('NIFTY 22750PE · your trade');
  await page.evaluate(() => (window as unknown as { _scalpDockFollow: (o: unknown[]) => void })._scalpDockFollow([]));
  await expect(page.locator('#scalp-dock-chart-title')).toHaveText('NIFTY 22750PE');

  // Narrower than two columns, the dock steps aside and the button opens the pop-up.
  await page.setViewportSize({ width: 1000, height: 800 });
  await expect(dock).toBeHidden();
  await expect(btn).toBeVisible();
  // It wears the site's own chart icon, not an arrow, on the broker's row.
  await expect(btn.locator('#scalp-contract-chart-ico svg rect')).toHaveCount(3);
  await expect(btn).not.toContainText('↗');
  const placed = await page.evaluate(() => {
    const row = document.querySelector('.scalp-broker-row')!.getBoundingClientRect();
    const sw = document.getElementById('scalp-broker-toggle')!.getBoundingClientRect();
    const b = document.getElementById('scalp-contract-chart-btn')!.getBoundingClientRect();
    return b.left >= sw.right && b.right <= row.right + 1 && b.top >= row.top - 1 && b.bottom <= row.bottom + 1;
  });
  expect(placed).toBe(true);
  await btn.click();
  await expect(page.locator('#scalp-option-chart-overlay')).toHaveClass(/is-open/);
  await expect(page.locator('#scalp-option-chart-title')).toHaveText('NIFTY 22750PE');
  await expect(page.locator('#scalp-option-chart-body canvas').first()).toBeVisible();

  // Closed, it stops asking.
  await page.keyboard.press('Escape');
  await expect(page.locator('#scalp-option-chart-overlay')).not.toHaveClass(/is-open/);
  const settled = asked.length;
  await page.waitForTimeout(2600);
  expect(asked.length).toBe(settled);
  await page.setViewportSize({ width: 1280, height: 720 });

  // With no strike there is nothing to chart, and the button says so.
  await page.evaluate(() => {
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '';
    (window as unknown as { _syncScalpContractChartLabel: () => void })._syncScalpContractChartLabel();
  });
  await expect(btn).toBeDisabled();
  await expect(page.locator('#scalp-contract-chart-label')).toHaveText('Option chart');
});

test('Scalp: SL and Target follow the premium by the points set, and go to the server as points', async ({ page }) => {
  test.setTimeout(60_000);
  await login(page);
  let sent: any = null;
  await page.route('**/api/scalp/entry', async (route) => {
    sent = JSON.parse(route.request().postData() as string);
    await route.fulfill({ json: { status: 'error', message: 'stopped by the test' } });
  });
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);
  await page.waitForFunction(() => !!(document.getElementById('scalp-expiry') as HTMLSelectElement).value);
  // SENSEX 73000PE at Rs 480.85, the price that refused the order on 14-Oct.
  await page.evaluate(() => {
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '73000';
    (window as unknown as { _scalpCurrentLTP: number })._scalpCurrentLTP = 480.85;
    (window as unknown as { _syncScalpAutoExits: () => void })._syncScalpAutoExits();
  });
  // Points is the way chosen; the exits it gives are said in words.
  await expect(page.locator('#scalp-exit-mode')).toHaveValue('points');
  await expect(page.locator('#scalp-sl-prem')).toHaveValue('380.85');
  await expect(page.locator('#scalp-target-prem')).toHaveValue('580.85');
  await expect(page.locator('#scalp-exit-preview')).toContainText('SL ₹380.85');
  await expect(page.locator('#scalp-exit-preview')).toContainText('Target ₹580.85');

  // The price moves: so do they.
  await page.evaluate(() => {
    (window as unknown as { _scalpCurrentLTP: number })._scalpCurrentLTP = 500;
    (window as unknown as { updateScalpMargin: () => void }).updateScalpMargin();
  });
  await expect(page.locator('#scalp-sl-prem')).toHaveValue('400.00');
  await expect(page.locator('#scalp-target-prem')).toHaveValue('600.00');

  // Sent as distances, for the server to measure from the price it trades at,
  // and nothing else: no premiums, no rupees.
  await page.evaluate(() => (window as unknown as { submitScalpEntry: (d: string) => Promise<void> }).submitScalpEntry('BUY'));
  await expect.poll(() => sent).not.toBeNull();
  expect([sent.sl_points, sent.target_points]).toEqual([100, 100]);
  expect([sent.sl_premium, sent.target_premium, sent.sl_rupees, sent.target_rupees]).toEqual([0, 0, 0, 0]);

  // Phil, 2026-10-19: a 0 SL is no SL -- not a stop at Rs 0.05.
  await page.locator('#scalp-page details.scalp-advanced > summary').click();
  await page.fill('#scalp-sl-pts', '0');
  await page.fill('#scalp-target-pts', '10');
  await expect(page.locator('#scalp-exit-preview')).toContainText('SL none');
  await expect(page.locator('#scalp-sl-prem')).toHaveValue('0');

  // Premium: only that pair shows, typed prices go, no points.
  sent = null;
  await page.click('#scalp-exit-mode-toggle [data-value="premium"]');
  await expect(page.locator('#scalp-sl-pts')).toBeHidden();
  await expect(page.locator('#scalp-sl-prem')).toBeVisible();
  await page.fill('#scalp-sl-prem', '450');
  await page.fill('#scalp-target-prem', '560');
  await page.evaluate(() => (window as unknown as { submitScalpEntry: (d: string) => Promise<void> }).submitScalpEntry('BUY'));
  await expect.poll(() => sent).not.toBeNull();
  expect(sent.sl_points).toBeUndefined();
  expect([sent.sl_premium, sent.target_premium]).toEqual([450, 560]);

  // Rupees of P&L become points per unit of the position.
  sent = null;
  await page.click('#scalp-exit-mode-toggle [data-value="rupees"]');
  const qty = await page.evaluate(() => (parseInt((document.getElementById('scalp-lots') as HTMLInputElement).value) || 0) * (parseInt((document.getElementById('scalp-lot-size') as HTMLInputElement).value) || 0));
  await page.fill('#scalp-sl-rs', String(qty * 20));
  await page.fill('#scalp-target-rs', String(qty * 40));
  await page.evaluate(() => (window as unknown as { submitScalpEntry: (d: string) => Promise<void> }).submitScalpEntry('BUY'));
  await expect.poll(() => sent).not.toBeNull();
  expect([sent.sl_points, sent.target_points, sent.sl_rupees, sent.sl_premium]).toEqual([20, 40, 0, 0]);
});

test('Scalp: the docked chart leaves the NIFTY 5m chart alone while it is open', async ({ page }) => {
  // Phil, 2026-10-15: "In scalp page my nifty main chart is not opening".
  // The dock redrew itself every few seconds and took back the one canvas.
  test.setTimeout(60_000);
  await login(page);
  const t0 = Math.floor(new Date('2026-10-14T09:15:00+05:30').getTime() / 1000);
  const candles = Array.from({ length: 30 }, (_, i) => ({ t: t0 + i * 300, o: 100 + i, h: 104 + i, l: 98 + i, c: 102 + i }));
  await page.route('**/api/scalp/contract-chart**', (route) => route.fulfill({ json: {
    status: 'ok', timeframe: '5m', candles, entries: [], overlays: [], lines: [], live_price: 0,
    instrument: { underlying: 'NIFTY', strike: 22700, option_type: 'PE', expiry: '2026-10-19', security_id: '1' },
  } }));
  await page.route('**/api/live/index-chart**', (route) => route.fulfill({ json: {
    status: 'ok', timeframe: '5m', candles: candles.map((c) => ({ ...c, o: c.o * 200, h: c.h * 200, l: c.l * 200, c: c.c * 200 })),
    entries: [], overlays: [], lines: [], instrument: { underlying: 'NIFTY' },
  } }));
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);
  await page.evaluate(() => {
    const sel = document.getElementById('scalp-expiry') as HTMLSelectElement;
    if (!sel.value) { sel.innerHTML = '<option value="2026-10-19">19 Oct</option>'; sel.value = '2026-10-19'; }
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '22700';
    (window as unknown as { _syncScalpContractChartLabel: () => void })._syncScalpContractChartLabel();
  });
  await expect(page.locator('#scalp-dock-chart-body canvas').first()).toBeVisible();

  await page.locator('#scalp-start-btn + [data-pf-action="openCePeIndexChart"][data-cepe-tf="5m"]').click();
  await expect(page.locator('#live-entry-chart-overlay')).toHaveClass(/is-open/);
  await expect(page.locator('#live-entry-chart-body canvas').first()).toBeVisible();
  // Past the dock's 2s poll and the 5s premium refresh: still the NIFTY chart.
  await page.evaluate(() => {
    const w = window as unknown as { _syncScalpContractChartLabel: () => void; _scalpDockFollow: (o: unknown[]) => void };
    w._syncScalpContractChartLabel();
    w._scalpDockFollow([]);
  });
  await page.waitForTimeout(6500);
  await expect(page.locator('#live-entry-chart-body canvas').first()).toBeVisible();
  await expect(page.locator('#live-entry-chart-body')).not.toContainText('only one chart');

  // Closed, the dock draws itself again.
  await page.keyboard.press('Escape');
  await expect(page.locator('#live-entry-chart-overlay')).not.toHaveClass(/is-open/);
  await page.evaluate(() => (window as unknown as { _syncScalpContractChartLabel: () => void })._syncScalpContractChartLabel());
  await expect(page.locator('#scalp-dock-chart-body canvas').first()).toBeVisible();
});

test('Scalp: the option chart opens on one day of candles and keeps a chosen zoom', async ({ page }) => {
  // Phil, 2026-10-15: "Need options chart view to one day amount of candles
  // ... I want only one day quantity and stays till I refresh".
  test.setTimeout(60_000);
  await login(page);
  const days = ['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15'];
  const bars = (tfMin: number) => days.flatMap((day) => {
    const open = Math.floor(new Date(`${day}T09:15:00+05:30`).getTime() / 1000);
    return Array.from({ length: Math.ceil(375 / tfMin) }, (_, i) => ({ t: open + i * tfMin * 60, o: 100 + i, h: 104 + i, l: 98 + i, c: 102 + i }));
  });
  await page.route('**/api/scalp/contract-chart**', (route) => {
    const tf = new URL(route.request().url()).searchParams.get('timeframe') || '5m';
    const tfMin = { '1m': 1, '3m': 3, '5m': 5, '15m': 15, '1h': 60 }[tf] || 5;
    return route.fulfill({ json: {
      status: 'ok', timeframe: tf, candles: bars(tfMin), entries: [], overlays: [], lines: [], live_price: 0,
      instrument: { underlying: 'NIFTY', strike: 22700, option_type: 'PE', expiry: '2026-10-19', security_id: '1' },
    } });
  });
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);
  await page.evaluate(() => {
    const sel = document.getElementById('scalp-expiry') as HTMLSelectElement;
    if (!sel.value) { sel.innerHTML = '<option value="2026-10-19">19 Oct</option>'; sel.value = '2026-10-19'; }
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '22700';
    const w = window as unknown as { _scalpOptionChartTf: string; _syncScalpContractChartLabel: () => void };
    w._scalpOptionChartTf = '5m';
    w._syncScalpContractChartLabel();
  });
  await expect(page.locator('#scalp-dock-chart-body canvas').first()).toBeVisible();
  type V = { tMin: number; tMax: number; pMin: number; pMax: number };
  const view = () => page.evaluate(() => ({ ...(window as unknown as { _pfChartCanvas: { viewport: V } })._pfChartCanvas.viewport }));
  const span = async () => { const v = await view(); return Math.round(v.tMax - v.tMin); };

  // One day on 5m is 75 bars, ending at the newest of the 300.
  const first = await view();
  expect(first.tMin).toBeCloseTo(224.5, 0);
  expect(first.tMax).toBeCloseTo(299.5, 0);

  // Zoom in to 30 bars (150 minutes): the refreshes and the refresh button keep it.
  await page.evaluate(() => {
    const w = window as unknown as { _pfChartCanvas: { viewport: V }; _pfChartCanvasSetViewport: (c: unknown, v: V) => void };
    const v = w._pfChartCanvas.viewport;
    w._pfChartCanvasSetViewport(w._pfChartCanvas, { tMin: v.tMax - 30, tMax: v.tMax, pMin: v.pMin, pMax: v.pMax });
  });
  await page.waitForTimeout(2600); // past a live refresh
  expect(await span()).toBe(30);
  const strip = page.locator('#scalp-dock-chart-actions');
  await strip.locator('button[title*="efresh" i], button[aria-label*="efresh" i]').first().click();
  await page.waitForTimeout(800);
  expect(await span()).toBe(30);

  // A new timeframe keeps the same stretch of time: 150 minutes is 10 bars of 15m.
  await strip.getByRole('radio', { name: /^15m$/i }).click();
  await expect.poll(span).toBe(10);

  // The 100% button goes back to one day: 25 bars of 15m.
  await page.locator('#scalp-dock-chart-body [data-bench-zoom-reset]').click();
  await expect.poll(span).toBe(25);
  await strip.getByRole('radio', { name: /^5m$/i }).click();
});
