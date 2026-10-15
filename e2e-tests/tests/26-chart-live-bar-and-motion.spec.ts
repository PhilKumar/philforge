import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-15: "why the chart painting is not smooth like Trading view?"
// then "yes do both and also add the countdown to bar close". The Scalp chart
// grows its forming candle from the live premium, carries a price tag with the
// time left in the bar, and zooms and pans with motion instead of jumps.

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

function oneMinuteBars(day: string, until: string) {
  const open = ist(`${day}T09:15:00`), end = ist(`${day}T${until}`);
  const bars = [];
  for (let t = open, i = 0; t <= end; t += 60, i++) bars.push({ t, o: 140 + (i % 7), h: 144 + (i % 7), l: 138 + (i % 7), c: 141 + (i % 7) });
  return bars;
}

async function scalpChart(page: Page, candles: unknown[], ltp: number, ownRoutes = true) {
  // Playwright tries the LAST route registered first, so a test with routes
  // of its own passes ownRoutes=false rather than have these shadow them.
  if (ownRoutes) await page.route('**/api/scalp/contract-chart**', (route) => route.fulfill({ json: {
    status: 'ok', timeframe: '1m', candles, entries: [], overlays: [], live_price: ltp,
    lines: [{ label: 'LIVE', price: ltp, filled: true, color: '#e2e8f0', dash: [2, 3] }],
    instrument: { underlying: 'NIFTY', strike: 22700, option_type: 'PE', expiry: '2026-10-19', security_id: '1' },
  } }));
  if (ownRoutes) await page.route('**/api/option-ltp**', (route) => route.fulfill({ json: { status: 'ok', ltp } }));
  await login(page);
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);
  await page.evaluate((price) => {
    const w = window as unknown as { _scalpCurrentLTP: number; _scalpOptionChartTf: string; _syncScalpContractChartLabel: () => void };
    const sel = document.getElementById('scalp-expiry') as HTMLSelectElement;
    sel.innerHTML = '<option value="2026-10-19">19 Oct</option>';
    sel.value = '2026-10-19';
    (document.getElementById('scalp-underlying') as HTMLSelectElement).value = 'NIFTY';
    (document.getElementById('scalp-option-type') as HTMLInputElement).value = 'PE';
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '22700';
    w._scalpCurrentLTP = price;
    (window as unknown as { _scalpLTPKey: string })._scalpLTPKey = 'form:NIFTY|22700|2026-10-19|PE';
    w._scalpOptionChartTf = '1m';
    w._syncScalpContractChartLabel();
  }, ltp);
  await expect(page.locator('#scalp-dock-chart-body canvas').first()).toBeVisible();
}

type Canvas = { data: { candles: { t: number; o: number; h: number; l: number; c: number }[] }; paint: { countdown: string; lastPrice: number }; viewport: { tMin: number; tMax: number } };
const canvas = (page: Page) => page.evaluate(() => {
  const c = (window as unknown as { _pfChartCanvas: Canvas })._pfChartCanvas;
  return { candles: c.data.candles, paint: c.paint, viewport: { ...c.viewport } };
});

test('the bar clock: 09:15 grid, a 1h bar closes at 15:30, no clock on a weekend', async ({ page }) => {
  await login(page);
  const out = await page.evaluate(([a, b, sat, shut]) => {
    const w = window as unknown as { pfChartBarWindow: (n: number, tf: number) => { start: number; close: number } | null };
    return [w.pfChartBarWindow(a, 60), w.pfChartBarWindow(b, 3600), w.pfChartBarWindow(sat, 60), w.pfChartBarWindow(shut, 60)];
  }, [ist('2026-10-15T10:07:30'), ist('2026-10-15T15:20:00'), ist('2026-10-17T11:00:00'), ist('2026-10-15T15:30:00')]);
  expect(out[0]).toMatchObject({ start: ist('2026-10-15T10:07:00'), close: ist('2026-10-15T10:08:00') });
  expect(out[1]).toMatchObject({ start: ist('2026-10-15T15:15:00'), close: ist('2026-10-15T15:30:00') });
  expect(out[2]).toBeNull();
  expect(out[3]).toBeNull();
});

test('the forming candle grows from the live premium and the tag counts to the close', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-10-15T10:07:30+05:30'));
  const candles = [...oneMinuteBars('2026-10-14', '15:29:00'), ...oneMinuteBars('2026-10-15', '10:06:00')];
  await scalpChart(page, candles, 150);

  // Dhan's last bar is 10:06; the page opens 10:07 itself at the live price.
  await expect.poll(async () => (await canvas(page)).candles.at(-1)!.t).toBe(ist('2026-10-15T10:07:00'));
  let now = await canvas(page);
  expect(now.candles.at(-1)).toMatchObject({ o: 150, h: 150, l: 150, c: 150 });
  expect(now.paint.lastPrice).toBe(150);
  expect(now.paint.countdown).toBe('00:30');

  // The price moves: the same bar's high and close follow, no new bar. (The
  // page polls the premium, so the feed itself moves, not just the variable.)
  await page.unroute('**/api/option-ltp**');
  await page.route('**/api/option-ltp**', (route) => route.fulfill({ json: { status: 'ok', ltp: 153.5 } }));
  await page.evaluate(() => { (window as unknown as { _scalpCurrentLTP: number })._scalpCurrentLTP = 153.5; });
  await expect.poll(async () => (await canvas(page)).candles.at(-1)!.c).toBe(153.5);
  now = await canvas(page);
  expect(now.candles.at(-1)).toMatchObject({ t: ist('2026-10-15T10:07:00'), o: 150, h: 153.5, l: 150 });
  expect(now.candles.length).toBe(candles.length + 1);
});

test('a day with no bar of its own grows nothing and shows no clock', async ({ page }) => {
  // A weekday whose chart ends yesterday: a holiday, or Dhan not caught up.
  await page.clock.setFixedTime(new Date('2026-10-15T10:07:30+05:30'));
  const candles = oneMinuteBars('2026-10-14', '15:29:00');
  await scalpChart(page, candles, 150);
  await page.waitForTimeout(1500);
  const now = await canvas(page);
  expect(now.candles.length).toBe(candles.length);
  expect(now.paint.countdown).toBe('');
});

test('the wheel glides, a sideways swipe pans, and a flicked pan coasts', async ({ page }) => {
  const candles = [...oneMinuteBars('2026-10-13', '15:29:00'), ...oneMinuteBars('2026-10-14', '15:29:00')];
  await page.setViewportSize({ width: 1400, height: 1000 });
  await scalpChart(page, candles, 150);
  await page.locator('#scalp-dock-chart').scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const box = (await page.locator('#scalp-dock-chart-body canvas').first().boundingBox())!;
  const mid = { x: box.x + box.width * 0.6, y: box.y + box.height * 0.5 };
  await page.mouse.move(mid.x, mid.y);
  await page.waitForTimeout(300);

  const start = (await canvas(page)).viewport;
  const span0 = start.tMax - start.tMin;
  await page.mouse.wheel(0, 100);
  // It eases to ~16% wider over a few frames rather than jumping there.
  await expect.poll(async () => { const v = (await canvas(page)).viewport; return +((v.tMax - v.tMin) / span0).toFixed(2); })
    .toBeCloseTo(Math.exp(0.15), 1);

  const beforeSwipe = (await canvas(page)).viewport;
  await page.mouse.wheel(200, 0);
  await expect.poll(async () => (await canvas(page)).viewport.tMin).toBeGreaterThan(beforeSwipe.tMin + 1);

  await page.waitForTimeout(400);
  // A real mouse reports every ~8-16ms; Playwright's mouse.move is a round
  // trip each (75-190ms apart under test), which no flick looks like. So the
  // flick is played inside the page at a mouse's pace.
  const released = await page.evaluate(async ([x, y]) => {
    const c = (window as unknown as { _pfChartCanvas: { host: HTMLElement; viewport: { tMin: number } } })._pfChartCanvas;
    const fire = (type: string, cx: number) => c.host.dispatchEvent(new PointerEvent(type, {
      bubbles: true, cancelable: true, pointerId: 7, pointerType: 'mouse', clientX: cx, clientY: y, buttons: type === 'pointerup' ? 0 : 1,
    }));
    fire('pointerdown', x);
    for (let i = 1; i <= 8; i++) {
      await new Promise((r) => setTimeout(r, 16));
      fire('pointermove', x - i * 30);
    }
    fire('pointerup', x - 240);
    return c.viewport.tMin;
  }, [mid.x, mid.y]);
  await page.waitForTimeout(250);
  // Dragged left moves forward in time, and keeps going after the release.
  expect((await canvas(page)).viewport.tMin).toBeGreaterThan(released + 0.5);
});

test('a new strike never takes the last strike\'s price into its candle', async ({ page }) => {
  // Phil, 2026-10-15: "Why it is putting a big wick below or above when I
  // change strikes". The old strike's premium was still the page's price for
  // a moment and the new strike's live bar took it as its own low or high.
  await page.clock.setFixedTime(new Date('2026-10-15T10:07:30+05:30'));
  const low = [...oneMinuteBars('2026-10-14', '15:29:00'), ...oneMinuteBars('2026-10-15', '10:06:00')];
  const high = low.map((b) => ({ ...b, o: b.o + 160, h: b.h + 160, l: b.l + 160, c: b.c + 160 }));
  await page.route('**/api/scalp/contract-chart**', (route) => {
    const strike = new URL(route.request().url()).searchParams.get('strike');
    return route.fulfill({ json: {
      status: 'ok', timeframe: '1m', candles: strike === '22750' ? high : low, entries: [], overlays: [], lines: [], live_price: 0,
      instrument: { underlying: 'NIFTY', strike: Number(strike), option_type: 'PE', expiry: '2026-10-19', security_id: '1' },
    } });
  });
  // The new strike's own price is slow to come back, as Dhan can be.
  await page.route('**/api/option-ltp**', async (route) => {
    const strike = new URL(route.request().url()).searchParams.get('strike');
    if (strike === '22750') await new Promise((r) => setTimeout(r, 4000));
    await route.fulfill({ json: { status: 'ok', ltp: strike === '22750' ? 305 : 150 } });
  });
  await scalpChart(page, low, 150, false);
  await expect.poll(async () => (await canvas(page)).candles.at(-1)!.c).toBe(150);

  await page.evaluate(() => {
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '22750';
    (window as unknown as { _syncScalpContractChartLabel: () => void })._syncScalpContractChartLabel();
  });
  await expect(page.locator('#scalp-dock-chart-title')).toHaveText('NIFTY 22750PE');
  await page.waitForTimeout(2500);
  const lows = (await canvas(page)).candles.map((b) => b.l);
  expect(Math.min(...lows)).toBeGreaterThan(290);
});

test('Supertrend (10, 2) is drawn on the option chart and its switch turns it off and on', async ({ page }) => {
  // Phil, 2026-10-15: "Add supertrend 2 as an option on the options charts".
  // A day that rises then falls, so the line turns green and then red.
  const candles = oneMinuteBars('2026-10-14', '15:29:00').map((b, i) => {
    const mid = 150 + (i < 180 ? i * 0.6 : 108 - (i - 180) * 0.6);
    return { ...b, o: mid - 0.3, h: mid + 1, l: mid - 1, c: mid + (i < 180 ? 0.3 : -0.3) };
  });
  await scalpChart(page, candles, 150);
  const st = () => page.evaluate(() => {
    const c = (window as unknown as { _pfChartCanvas: { data: { overlays: { _scalpSt?: boolean; supertrend?: number; label?: string }[] }; paint: { overlays: number } } })._pfChartCanvas;
    const runs = (c.data.overlays || []).filter((o) => o._scalpSt);
    return { runs: runs.length, dirs: [...new Set(runs.map((o) => o.supertrend))].sort(), label: runs.filter((o) => o.label).map((o) => o.label), painted: c.paint.overlays };
  });
  const on = await st();
  expect(on.runs).toBeGreaterThan(1);
  expect(on.dirs).toEqual([-1, 1]);
  expect(on.label).toEqual(['ST 10,2']);

  const toggle = page.locator('#scalp-dock-chart-actions .scalp-st-toggle');
  await expect(toggle).toHaveText('ST 10,2');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(async () => (await st()).runs).toBe(0);
  // It repainted without the line, and the choice is remembered.
  await expect.poll(async () => (await st()).painted).toBeLessThan(on.painted);
  expect(await page.evaluate(() => localStorage.getItem('pf_scalp_chart_supertrend'))).toBe('0');
  await toggle.click();
  await expect.poll(async () => (await st()).runs).toBe(on.runs);
});
