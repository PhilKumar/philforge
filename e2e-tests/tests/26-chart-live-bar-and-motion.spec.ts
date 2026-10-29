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
  // The form's own expiry list, so the page's start-up cannot replace the
  // 19-Oct expiry these charts are drawn for once that date has passed.
  await page.route('**/api/expiry-list/**', (route) => route.fulfill({ json: { status: 'ok', expiries: ['2026-10-19'] } }));
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

// The studies and drawing tools sit in the Studio drawer beside the heading
// (2026-10-28); these tests use them, so the drawer starts open. The drawer's
// own test below starts it closed.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.setItem('pf-scalp-studio-open', '1'); } catch (_) {} });
});

test('the Studio drawer rolls the chart tools out beside the heading, and back in', async ({ page }) => {
  // Phil, 2026-10-28: "Move these buttons into a arrow with a catchy text and
  // move it beside the chart heading ... rolling out from left to right".
  await page.addInitScript(() => { try { localStorage.setItem('pf-scalp-studio-open', '0'); } catch (_) {} });
  await scalpChart(page, oneMinuteBars('2026-10-14', '10:00:00'), 150);
  const tab = page.locator('#scalp-dock-chart .scalp-studio-tab');
  const tray = page.locator('#scalp-dock-chart .scalp-studio-tray');
  await expect(tab).toBeVisible();
  await expect(tab).toHaveAttribute('aria-expanded', 'false');
  // Beside the heading: the tab starts where the title ends.
  const beside = await page.evaluate(() => {
    const t = document.querySelector('#scalp-dock-chart .terminal-cascade-chart-title')!.getBoundingClientRect();
    const b = document.querySelector('#scalp-dock-chart .scalp-studio-tab')!.getBoundingClientRect();
    return b.left > t.right && b.left - t.right < 40 && b.top < t.bottom;
  });
  expect(beside).toBe(true);
  const st = page.locator('#scalp-dock-chart .scalp-st-toggle');
  await expect(st).toBeHidden();
  await tab.click();
  await expect(tab).toHaveAttribute('aria-expanded', 'true');
  await expect(st).toBeVisible();
  // It rolls out beside the tab, floating where nothing moves; or, with no
  // room there, as its own row between the heading and the chart. Inside the
  // card either way, and never over the timeframes, zoom or the B/S ticket.
  // Polled: the B/S ticket follows the chart on its next redraw, a frame
  // after a tray row has pushed the chart down.
  const placement = () => page.evaluate(() => {
    const r = (sel: string) => document.querySelector(sel)!.getBoundingClientRect();
    const t = r('#scalp-dock-chart .scalp-studio-tab'), y = r('#scalp-dock-chart .scalp-studio-tray');
    const card = r('#scalp-dock-chart'), a = r('#scalp-dock-chart-actions'), body = r('#scalp-dock-chart-body');
    const overlaps = (p: DOMRect, q: DOMRect) => p.left < q.right && q.left < p.right && p.top < q.bottom && q.top < p.bottom;
    const ticket = document.getElementById('scalp-ticket');
    const beside = y.left >= t.right && y.top < t.bottom;
    const row = y.top >= t.bottom - 1 && y.bottom <= body.top + 1;
    return { ok: (beside || row) && y.right <= card.right + 1 && !overlaps(y, a)
      && !(ticket && ticket.getClientRects().length && overlaps(y, ticket.getBoundingClientRect())), beside, row,
      rects: [t, y, card, a, body, ticket?.getBoundingClientRect()].map((q) => q && [q.left, q.top, q.right, q.bottom].map(Math.round).join(',')).join(' | ') };
  });
  await expect.poll(async () => { const p = await placement(); return p.ok ? 'ok' : p.rects; }).toBe('ok');
  await tab.click();
  await expect(tab).toHaveAttribute('aria-expanded', 'false');
  await expect(st).toBeHidden();
  await expect(tray).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem('pf-scalp-studio-open'))).toBe('0');
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

test('at the open the first bar is drawn from the price as soon as it trades, before Dhan has one', async ({ page }) => {
  // Phil, 2026-10-29: "The first few seconds when the market starts, I am not
  // seeing any candles". Dhan's history still ends yesterday at 09:15:20.
  await page.clock.setFixedTime(new Date('2026-10-15T09:15:20+05:30'));
  const candles = oneMinuteBars('2026-10-14', '15:29:00');
  await scalpChart(page, candles, 150);
  await page.waitForTimeout(1500);
  // A price that has not moved yet could be a holiday's: nothing.
  expect((await canvas(page)).candles.length).toBe(candles.length);
  // It trades: the 09:15 bar opens at once, and follows.
  await page.unroute('**/api/option-ltp**');
  await page.route('**/api/option-ltp**', (route) => route.fulfill({ json: { status: 'ok', ltp: 152 } }));
  await page.evaluate(() => { (window as unknown as { _scalpCurrentLTP: number })._scalpCurrentLTP = 152; });
  await expect.poll(async () => (await canvas(page)).candles.length).toBe(candles.length + 1);
  expect((await canvas(page)).candles.at(-1)).toMatchObject({ t: ist('2026-10-15T09:15:00'), o: 152, h: 152, l: 152, c: 152 });
  await page.unroute('**/api/option-ltp**');
  await page.route('**/api/option-ltp**', (route) => route.fulfill({ json: { status: 'ok', ltp: 149.5 } }));
  await page.evaluate(() => { (window as unknown as { _scalpCurrentLTP: number })._scalpCurrentLTP = 149.5; });
  await expect.poll(async () => (await canvas(page)).candles.at(-1)!.c).toBe(149.5);
  expect((await canvas(page)).candles.at(-1)).toMatchObject({ o: 152, h: 152, l: 149.5 });
  expect((await canvas(page)).candles.length).toBe(candles.length + 1);
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
    const c = (window as unknown as { _pfChartCanvas: { data: { overlays: { supertrend?: number; label?: string }[] }; paint: { overlays: number } } })._pfChartCanvas;
    const runs = (c.data.overlays || []).filter((o) => o.supertrend);
    return { runs: runs.length, dirs: [...new Set(runs.map((o) => o.supertrend))].sort(), label: runs.filter((o) => o.label).map((o) => o.label), painted: c.paint.overlays };
  });
  const on = await st();
  expect(on.runs).toBeGreaterThan(1);
  expect(on.dirs).toEqual([-1, 1]);
  // The label carries the line's latest value, as CPR's do (2026-10-28).
  expect(on.label).toHaveLength(1);
  expect(on.label[0]).toMatch(/^ST 10,2 \([\d,]+\.\d{2}\)$/);

  const toggle = page.locator('#scalp-dock-chart .scalp-studio-tray .scalp-st-toggle');
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

test('Fibs and trendlines draw, edit and delete the way TradingView does', async ({ page }) => {
  // Phil, 2026-10-16: three Fib sets drawn by hand, then "I want same like the
  // trading view": the first click starts the line and it follows the pointer,
  // a drawn line can be taken by its ends or moved whole, arming a tool leaves
  // the view alone, no instructions in the toolbar, and the Fib menu folds
  // away on a click anywhere else.
  // 630 tall: under the Scalp freeze (>= 640 since 2026-10-22) the pinned
  // stack covers the chart's top, and this test is about drawing, not the freeze.
  await page.setViewportSize({ width: 1400, height: 630 });
  // The RSI pane takes the bottom of the plot; this test draws there, so RSI is off.
  await page.addInitScript(() => { try { localStorage.setItem('pf_scalp_chart_rsi', '0'); } catch (_) {} });
  const candles = [...oneMinuteBars('2026-10-13', '15:29:00'), ...oneMinuteBars('2026-10-14', '15:29:00')];
  await scalpChart(page, candles, 150);
  await page.evaluate(() => { try { localStorage.removeItem('pf_scalp_chart_drawings_v1'); } catch (_) {} });
  const chart = page.locator('#scalp-dock-chart-body canvas').first();
  const xy = async (fx: number, fy: number) => {
    await page.locator('#scalp-dock-chart-body').scrollIntoViewIfNeeded();
    const box = (await chart.boundingBox())!;
    return [box.x + box.width * fx, box.y + box.height * fy] as const;
  };
  const click = async (fx: number, fy: number) => { const [x, y] = await xy(fx, fy); await page.mouse.click(x, y); };
  const tools = page.locator('#scalp-dock-chart .scalp-studio-tray .scalp-chart-tools');
  const stored = () => page.evaluate(() => Object.values(JSON.parse(localStorage.getItem('pf_scalp_chart_drawings_v1') || '{}'))[0] as any || { fibs: [], lines: [] });
  const view = () => page.evaluate(() => JSON.stringify((window as any)._pfChartCanvas.viewport));
  // A drawing's end, in page pixels.
  const endAt = (kind: string, end: 'a' | 'b') => page.evaluate(([k, e]) => {
    const c = (window as any)._pfChartCanvas, dr = c.data.drawings.find((d: any) => d.kind === k);
    const box = c.host.getBoundingClientRect();
    return [box.left + c.projection.xOf(dr[e].t), box.top + c.projection.yOf(dr[e].price)];
  }, [kind, end]);

  // The contract's name keeps its room beside the tools (it was squeezed to
  // 0px at this width once the chart moved left).
  expect((await page.locator('#scalp-dock-chart-title').boundingBox())!.width).toBeGreaterThan(100);
  // The menu folds on a click elsewhere.
  await tools.locator('.scalp-fib-btn').click();
  await expect(tools.locator('.scalp-fib-item')).toHaveText(['0 · 1 · 2 · 4 · 8', '0 · 0.5 · 1', '0 · 0.25 · 0.5 · 0.75 · 1']);
  await page.locator('#scalp-dock-chart-title').click();
  await expect(tools.locator('.scalp-fib-menu')).toBeHidden();

  // Arming changes nothing on the chart, and says nothing.
  const before = await view();
  await tools.locator('.scalp-fib-btn').click();
  await tools.locator('[data-fib-preset="half"]').click();
  await expect(tools.locator('.scalp-fib-btn')).toHaveClass(/is-armed/);
  await expect(tools.locator('.scalp-draw-hint')).toHaveCount(0);
  expect(await view()).toBe(before);

  // First click: the Fib is already drawing, and follows the pointer.
  await click(0.4, 0.7);
  const [mx, my] = await xy(0.7, 0.3);
  await page.mouse.move(mx, my, { steps: 4 });
  const pending = await page.evaluate(() => (window as any)._pfChartCanvas.drawPending);
  expect(pending.b.price).toBeGreaterThan(pending.a.price);
  await page.mouse.click(mx, my);
  await expect(tools.locator('.scalp-fib-btn')).not.toHaveClass(/is-armed/);
  expect(await view()).toBe(before);
  const fib = (await stored()).fibs[0];
  expect(fib.preset).toBe('half');
  expect(fib.b.price).toBeGreaterThan(fib.a.price);
  // The magnet: each point sits on its candle's open, high, low or close.
  const onCandle = await page.evaluate(([p1, p2]) => {
    const bars = (window as any)._pfChartCanvas.data.candles;
    const ohlc = (pt: any) => { const bar = bars.find((x: any) => x.t === pt.t); return bar ? [bar.o, bar.h, bar.l, bar.c].includes(pt.price) : false; };
    return [ohlc(p1), ohlc(p2)];
  }, [fib.a, fib.b]);
  expect(onCandle).toEqual([true, true]);
  expect(await page.evaluate(() => (window as any)._pfChartCanvas.paint.drawings)).toBe(1);

  // Press, drag, let go draws one too.
  await tools.locator('.scalp-fib-btn').click();
  await tools.locator('[data-fib-preset="x2"]').click();
  const [dx0, dy0] = await xy(0.45, 0.8), [dx1, dy1] = await xy(0.6, 0.75);
  await page.mouse.move(dx0, dy0);
  await page.mouse.down();
  await page.mouse.move(dx1, dy1, { steps: 5 });
  await page.mouse.up();
  await expect.poll(async () => (await stored()).fibs.length).toBe(2);

  // A trendline by two clicks: a segment between them.
  await tools.locator('.scalp-line-btn').click();
  await click(0.3, 0.8);
  await click(0.6, 0.6);
  await expect.poll(async () => (await stored()).lines.length).toBe(1);
  const line = (await stored()).lines[0];
  expect(line.b.t).toBeGreaterThan(line.a.t);
  // It runs on past its second point to the right edge ("Extend right"), and
  // that part is the line too: a click on it selects it.
  const beyond = await page.evaluate(() => {
    const c = (window as any)._pfChartCanvas, dr = c.data.drawings.find((d: any) => d.kind === 'line'), p = c.projection;
    const box = c.host.getBoundingClientRect();
    const xa = p.xOf(dr.a.t), ya = p.yOf(dr.a.price), xb = p.xOf(dr.b.t), yb = p.yOf(dr.b.price);
    const x = xb + (p.padL + p.plotW - xb) / 2;
    return { extend: dr.extend, x: box.left + x, y: box.top + ya + (yb - ya) * (x - xa) / (xb - xa) };
  });
  expect(beyond.extend).toBe('right');
  await page.mouse.click(beyond.x, beyond.y);
  expect(await page.evaluate(() => { const c = (window as any)._pfChartCanvas; return c.data.drawings.find((d: any) => d.id === c.drawSel)?.kind; })).toBe('line');

  // Its end is taken and moved...
  const [bx, by] = await endAt('line', 'b');
  await page.mouse.move(bx, by);
  await page.mouse.down();
  await page.mouse.move(bx + 60, by - 50, { steps: 5 });
  await page.mouse.up();
  await expect.poll(async () => (await stored()).lines[0].b.t).toBeGreaterThan(line.b.t);
  const moved = (await stored()).lines[0];
  expect(moved.a).toEqual(line.a);
  expect(moved.b.t).toBeGreaterThan(line.b.t);
  expect(moved.b.price).toBeGreaterThan(line.b.price);
  // ...and the whole line is moved by its middle, without panning the chart.
  const [ax, ay] = await endAt('line', 'a'), [ex, ey] = await endAt('line', 'b');
  const v0 = await view();
  await page.mouse.move((ax + ex) / 2, (ay + ey) / 2);
  await page.mouse.down();
  await page.mouse.move((ax + ex) / 2 + 20, (ay + ey) / 2 + 15, { steps: 3 });
  // Held across one of the chart's own refreshes: one landing mid-drag put the
  // stored, unmoved line back and the release saved it (CI, 2026-10-21).
  await page.waitForTimeout(4500);
  await page.mouse.move((ax + ex) / 2 + 40, (ay + ey) / 2 + 30, { steps: 3 });
  await page.mouse.up();
  await expect.poll(async () => (await stored()).lines[0].a.t).toBeGreaterThan(moved.a.t);
  const shifted = (await stored()).lines[0];
  expect(shifted.a.t).toBeGreaterThan(moved.a.t);
  expect(shifted.a.price).toBeLessThan(moved.a.price);
  expect(shifted.b.t - shifted.a.t).toBe(moved.b.t - moved.a.t);
  expect(await view()).toBe(v0);

  // Selected (just moved), Delete removes it.
  await page.keyboard.press('Delete');
  await expect.poll(async () => (await stored()).lines.length).toBe(0);

  // A new timeframe keeps the Fibs: they belong to the contract.
  await page.locator('#scalp-dock-chart-actions').getByRole('radio', { name: /^5m$/i }).click();
  await expect.poll(() => page.evaluate(() => (window as any)._pfChartCanvas.data.drawings.length)).toBe(2);

  // Clear takes this contract's drawings away.
  await tools.locator('.scalp-clear-btn').click();
  await expect.poll(() => page.evaluate(() => (window as any)._pfChartCanvas.data.drawings.length)).toBe(0);
});

test('the docked chart opens big in a pop-up and comes back when it closes', async ({ page }) => {
  // Phil, 2026-10-16: "give a zoom button to view in a pop-up window for a nice big view".
  await page.setViewportSize({ width: 1400, height: 1000 });
  await scalpChart(page, oneMinuteBars('2026-10-14', '15:29:00'), 150);
  await page.locator('#scalp-dock-chart .scalp-studio-tray .scalp-popout-btn').click();
  const dialog = page.locator('#scalp-option-chart-overlay.is-open .scalp-option-chart-dialog.is-big');
  await expect(dialog).toBeVisible();
  await expect(page.locator('#scalp-option-chart-body canvas').first()).toBeVisible();
  const size = await page.locator('#scalp-option-chart-body canvas').first().boundingBox();
  expect(size!.width).toBeGreaterThan(1200);
  expect(size!.height).toBeGreaterThan(700);
  await page.locator('#scalp-option-chart-actions [data-strip-close]').click();
  await expect(page.locator('#scalp-option-chart-overlay.is-open')).toHaveCount(0);
  await expect(page.locator('#scalp-dock-chart-body canvas').first()).toBeVisible();
});

test('an open trade on the form\'s contract does not hold the chart when CE turns to PE', async ({ page }) => {
  // Phil, 2026-10-16: "The options chart is also not changing while toggling
  // from PE to CE or vice versa".
  await page.setViewportSize({ width: 1400, height: 1000 });
  const candles = oneMinuteBars('2026-10-14', '15:29:00');
  await page.route('**/api/scalp/trades/7/chart**', (route) => route.fulfill({ json: {
    status: 'ok', timeframe: '1m', candles, entries: [], overlays: [], lines: [], live_price: 150,
    instrument: { underlying: 'NIFTY', strike: 22700, option_type: 'PE', expiry: '2026-10-19', security_id: '1' },
  } }));
  await page.route('**/api/scalp/contract-chart**', (route) => {
    const q = new URL(route.request().url()).searchParams;
    return route.fulfill({ json: {
      status: 'ok', timeframe: '1m', candles, entries: [], overlays: [], lines: [], live_price: 150,
      instrument: { underlying: 'NIFTY', strike: Number(q.get('strike')), option_type: q.get('option_type'), expiry: q.get('expiry'), security_id: '1' },
    } });
  });
  const trade = { trade_id: 7, status: 'open', underlying: 'NIFTY', strike: 22700, expiry: '2026-10-19', option_type: 'PE' };
  // The page's own status poll reports the trade open too, as the server would.
  await page.route('**/api/scalp/status', async (route) => {
    const body = await (await route.fetch()).json();
    await route.fulfill({ json: { ...body, open_trades: [trade] } });
  });
  await scalpChart(page, candles, 150, false);
  await page.evaluate((t) => (window as any)._scalpDockFollow([t]), trade);
  await expect(page.locator('#scalp-dock-chart-title')).toHaveText('NIFTY 22700PE · your trade');
  await page.evaluate((t) => {
    (document.getElementById('scalp-option-type') as HTMLInputElement).value = 'CE';
    (window as any)._scalpDockFollow([t]);
  }, trade);
  await expect(page.locator('#scalp-dock-chart-title')).toHaveText('NIFTY 22700CE');
  // The 3s status poll may be mid-fetch through the route above as the page closes.
  await page.unrouteAll({ behavior: 'ignoreErrors' });
});

test('RSI (14) sits under the option chart as TradingView draws it, and its switch hides it', async ({ page }) => {
  // Phil, 2026-10-19: "Add RSI to my options chart... same like the trading view".
  await page.setViewportSize({ width: 1400, height: 1000 });
  await page.evaluate(() => { try { localStorage.removeItem('pf_scalp_chart_rsi'); } catch (_) {} });
  const candles = oneMinuteBars('2026-10-14', '15:29:00').map((b, i) => {
    const mid = 150 + 12 * Math.sin(i / 17) + (i % 5);
    return { ...b, o: mid - 0.5, h: mid + 1.5, l: mid - 1.5, c: mid };
  });
  await scalpChart(page, candles, 150);
  const rsi = () => page.evaluate(() => {
    const c = (window as any)._pfChartCanvas, ind = c.data.indicators || {};
    return { n: (ind.rsi || []).length, last: (ind.rsi || []).at(-1)?.v, period: ind.rsi_period, painted: c.paint.rsi, paneH: Math.round(c.projection.rsiH), bars: c.data.candles.length };
  });
  const on = await rsi();
  expect(on.period).toBe(14);
  // Bands at 60 / 40 (Phil, 2026-10-21).
  expect(await page.evaluate(() => { const i = (window as any)._pfChartCanvas.data.indicators; return [i.rsi_upper, i.rsi_lower]; })).toEqual([60, 40]);
  expect(on.n).toBe(on.bars - 14);
  expect(on.painted).toBeGreaterThan(0);
  expect(on.paneH).toBeGreaterThan(40);
  // Wilder's RSI on the chart's own closes, worked out here independently.
  const expected = await page.evaluate(() => {
    const k = (window as any)._pfChartCanvas.data.candles.map((b: any) => b.c), n = 14;
    let g = 0, l = 0;
    for (let i = 1; i <= n; i++) { const d = k[i] - k[i - 1]; g += Math.max(d, 0); l += Math.max(-d, 0); }
    g /= n; l /= n;
    for (let i = n + 1; i < k.length; i++) { const d = k[i] - k[i - 1]; g = (g * (n - 1) + Math.max(d, 0)) / n; l = (l * (n - 1) + Math.max(-d, 0)) / n; }
    return Math.round((l === 0 ? 100 : 100 - 100 / (1 + g / l)) * 100) / 100;
  });
  expect(on.last).toBeCloseTo(expected, 2);
  const toggle = page.locator('#scalp-dock-chart .scalp-studio-tray .scalp-rsi-toggle');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await toggle.click();
  await expect.poll(async () => (await rsi()).paneH).toBe(0);
  expect(await page.evaluate(() => localStorage.getItem('pf_scalp_chart_rsi'))).toBe('0');
  await toggle.click();
  await expect.poll(async () => (await rsi()).painted).toBeGreaterThan(0);
});

test('a new strike or CE<->PE keeps the stretch of time on screen; only the price axis refits', async ({ page }) => {
  // Phil, 2026-10-19: "the chart resets again and again if I move to other
  // strike price or CE to PE ... make it stay what I have viewed before".
  await page.setViewportSize({ width: 1400, height: 1000 });
  const day = oneMinuteBars('2026-10-14', '15:29:00');
  await page.route('**/api/scalp/contract-chart**', (route) => {
    const q = new URL(route.request().url()).searchParams;
    const base = q.get('option_type') === 'CE' ? 60 : 300;
    const candles = day.map((b, i) => ({ t: b.t, o: base + i * 0.05, h: base + i * 0.05 + 2, l: base + i * 0.05 - 2, c: base + i * 0.05 + 1 }));
    return route.fulfill({ json: {
      status: 'ok', timeframe: '1m', candles, entries: [], overlays: [], lines: [], live_price: 0,
      instrument: { underlying: 'NIFTY', strike: Number(q.get('strike')), option_type: q.get('option_type'), expiry: q.get('expiry'), security_id: '1' },
    } });
  });
  await scalpChart(page, day, 150, false);
  const win = () => page.evaluate(() => {
    const c = (window as any)._pfChartCanvas, w = (window as any).pfChartCurrentWindow();
    return { from: Math.round(w.from), to: Math.round(w.to), follow: w.followRight, pMin: c.viewport.pMin, pMax: c.viewport.pMax, type: c.data.instrument.option_type };
  });
  // Pan back to 11:00-12:00, off the live edge.
  const t11 = Math.floor(new Date('2026-10-14T11:00:00+05:30').getTime() / 1000);
  await page.evaluate((from) => {
    const c = (window as any)._pfChartCanvas;
    (window as any).pfChartApplyWindow({ from, to: from + 3600, followRight: false });
    c.viewport.pMin = c.viewport.pMin; // keep
  }, t11);
  const before = await win();
  expect([before.from, before.to, before.follow, before.type]).toEqual([t11, t11 + 3600, false, 'PE']);
  // Switch to CE: same times; prices refit to the CE's ~60s.
  await page.evaluate(() => {
    (document.getElementById('scalp-option-type') as HTMLInputElement).value = 'CE';
    (window as any)._syncScalpContractChartLabel();
  });
  await expect.poll(async () => (await win()).type).toBe('CE');
  const after = await win();
  expect([after.from, after.to, after.follow]).toEqual([t11, t11 + 3600, false]);
  expect(after.pMax).toBeLessThan(80);
  expect(after.pMin).toBeGreaterThan(50);
  // A new strike keeps it too.
  await page.evaluate(() => {
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '22800';
    (window as any)._syncScalpContractChartLabel();
  });
  await expect.poll(() => page.evaluate(() => (window as any)._pfChartCanvas.data.instrument.strike)).toBe(22800);
  const strike = await win();
  expect([strike.from, strike.to]).toEqual([t11, t11 + 3600]);
});

test('the CPR switch hides and shows the pivot set as one', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 1000 });
  const candles = oneMinuteBars('2026-10-14', '15:29:00');
  const day = candles[0].t, end = candles[candles.length - 1].t;
  const level = (label: string, price: number) => ({ label, color: '#2962ff', dash: [], width: 1, group: 'cpr', points: [{ t: day, price }, { t: end, price }] });
  await page.route('**/api/scalp/contract-chart**', (route) => route.fulfill({ json: {
    status: 'ok', timeframe: '1m', candles, entries: [], lines: [], live_price: 0,
    overlays: [level('CPR TC (146)', 146), level('CPR BC (144)', 144), level('R1 (150)', 150), { label: 'EMA 20', color: '#2962ff', points: candles.map((c) => ({ t: c.t, price: c.c })) }],
    instrument: { underlying: 'NIFTY', strike: 22700, option_type: 'PE', expiry: '2026-10-19', security_id: '1' },
  } }));
  await scalpChart(page, candles, 150, false);
  const count = () => page.evaluate(() => (window as any)._pfChartCanvas.data.overlays.filter((o: any) => o.group === 'cpr').length);
  const cpr = page.locator('#scalp-dock-chart .scalp-studio-tray .scalp-cpr-toggle');
  await expect(cpr).toHaveAttribute('aria-pressed', 'true');
  expect(await count()).toBe(3);
  await cpr.click();
  await expect.poll(count).toBe(0);
  // The EMA is not part of the set and stays.
  expect(await page.evaluate(() => (window as any)._pfChartCanvas.data.overlays.some((o: any) => /^EMA 20 \([\d,]+\.\d{2}\)$/.test(String(o.label))))).toBe(true);
  expect(await page.evaluate(() => localStorage.getItem('pf_scalp_chart_cpr'))).toBe('0');
  await cpr.click();
  await expect.poll(count).toBe(3);
});

test('the EMA switch hides and shows the 20-EMA alone, like CPR', async ({ page }) => {
  // Phil, 2026-10-21: "Add EMA also as a button like CPR so that I can turn on
  // and off on the options chart".
  await page.setViewportSize({ width: 1400, height: 1000 });
  await page.addInitScript(() => { try { localStorage.removeItem('pf_scalp_chart_ema'); } catch (_) {} });
  const candles = oneMinuteBars('2026-10-14', '15:29:00');
  const day = candles[0].t, end = candles[candles.length - 1].t;
  await page.route('**/api/scalp/contract-chart**', (route) => route.fulfill({ json: {
    status: 'ok', timeframe: '1m', candles, entries: [], lines: [], live_price: 0,
    overlays: [
      { label: 'CPR P (145)', color: '#d946ef', dash: [], width: 1, group: 'cpr', points: [{ t: day, price: 145 }, { t: end, price: 145 }] },
      { label: 'EMA 20', color: '#2962ff', group: 'ema', points: candles.map((c) => ({ t: c.t, price: c.c })) },
    ],
    instrument: { underlying: 'NIFTY', strike: 22700, option_type: 'PE', expiry: '2026-10-19', security_id: '1' },
  } }));
  await scalpChart(page, candles, 150, false);
  const has = (label: string) => page.evaluate((l) => (window as any)._pfChartCanvas.data.overlays.some((o: any) => String(o.label).startsWith(l)), label);
  const ema = page.locator('#scalp-dock-chart .scalp-studio-tray .scalp-ema-toggle');
  await expect(ema).toHaveText('EMA');
  await expect(ema).toHaveAttribute('aria-pressed', 'true');
  expect(await has('EMA 20')).toBe(true);
  await ema.click();
  await expect.poll(() => has('EMA 20')).toBe(false);
  await expect(ema).toHaveAttribute('aria-pressed', 'false');
  expect(await has('CPR P')).toBe(true);  // the pivots stay
  expect(await page.evaluate(() => localStorage.getItem('pf_scalp_chart_ema'))).toBe('0');
  await ema.click();
  await expect.poll(() => has('EMA 20')).toBe(true);
});

test('a trendline reaches right while it is being drawn, not only after the second click', async ({ page }) => {
  // Phil, 2026-10-20: "while starting from a point it is not extending once the
  // second point is done it extends its ray".
  await page.setViewportSize({ width: 1400, height: 1400 });
  await page.addInitScript(() => { try { localStorage.setItem('pf_scalp_chart_rsi', '0'); localStorage.removeItem('pf_scalp_chart_drawings_v1'); } catch (_) {} });
  await scalpChart(page, oneMinuteBars('2026-10-14', '15:29:00'), 150);
  await page.evaluate(() => window.scrollTo(0, 0));
  const box = (await page.locator('#scalp-dock-chart-body canvas').first().boundingBox())!;
  await page.locator('#scalp-dock-chart .scalp-studio-tray .scalp-line-btn').click();
  await page.mouse.click(box.x + box.width * 0.3, box.y + box.height * 0.6);
  await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.4, { steps: 4 });
  const ray = await page.evaluate(() => {
    const w = window as any, c = w._pfChartCanvas, pend = c.drawPending;
    const end = w._pfChartLineEnd(c.projection, pend, w._pfChartDrawGeom(c.projection, pend));
    return { extend: pend.extend, endX: end && Math.round(end.x), right: Math.round(c.projection.padL + c.projection.plotW) };
  });
  expect(ray.extend).toBe('right');
  expect(ray.endX).toBe(ray.right);
  await page.keyboard.press('Escape');
});

test('the RSI axis stretches like the price axis: hover shows the up-down cursor, drag scales, double-click resets', async ({ page }) => {
  // Phil, 2026-10-20: "the chart Y axis when we hover we get a up and down
  // arrow right? the same way RSI Y axis has to have it".
  await page.setViewportSize({ width: 1400, height: 1400 });
  await page.addInitScript(() => { try { localStorage.removeItem('pf_scalp_chart_rsi'); } catch (_) {} });
  const candles = oneMinuteBars('2026-10-14', '15:29:00').map((b, i) => ({ ...b, c: 150 + 12 * Math.sin(i / 17) }));
  await scalpChart(page, candles, 150);
  await page.evaluate(() => window.scrollTo(0, 0));
  const pane = () => page.evaluate(() => (window as any)._pfChartCanvas.paint.rsiPane);
  const host = (await page.locator('#scalp-dock-chart-body .pf-bench-canvas-host').boundingBox())!;
  const p0 = await pane();
  expect([p0.lo, p0.hi]).toEqual([0, 100]);
  const ax = host.x + host.width - 20, ay = host.y + p0.top + p0.h / 2;
  await page.mouse.move(ax, ay);
  expect(await page.evaluate(() => (window as any)._pfChartCanvas.host.style.cursor)).toBe('ns-resize');
  // Up tightens the range...
  await page.mouse.down();
  await page.mouse.move(ax, ay - 60, { steps: 4 });
  await page.mouse.up();
  const tight = await pane();
  expect(tight.hi - tight.lo).toBeLessThan(80);
  // ...down widens it...
  await page.mouse.move(ax, ay);
  await page.mouse.down();
  await page.mouse.move(ax, ay + 160, { steps: 6 });
  await page.mouse.up();
  const wide = await pane();
  expect(wide.hi - wide.lo).toBeGreaterThan(tight.hi - tight.lo);
  // ...and the price scale was never touched.
  const price = await page.evaluate(() => { const v = (window as any)._pfChartCanvas.viewport; return [v.pMin, v.pMax]; });
  // A double-click on the RSI axis puts it back to 0-100.
  await page.mouse.dblclick(ax, ay);
  await expect.poll(async () => { const q = await pane(); return [q.lo, q.hi]; }).toEqual([0, 100]);
  expect(await page.evaluate(() => { const v = (window as any)._pfChartCanvas.viewport; return [v.pMin, v.pMax]; })).toEqual(price);
  // The pane itself keeps no move or collapse buttons.
  await expect(page.locator('#scalp-dock-chart-body [data-pane-tools]')).toHaveCount(0);
});

test('the crosshair runs down through the RSI pane, and reads the RSI there', async ({ page }) => {
  // Phil, 2026-10-20: "Make the cross-hair flow till RSI".
  await page.setViewportSize({ width: 1400, height: 1400 });
  await page.addInitScript(() => { try { localStorage.removeItem('pf_scalp_chart_rsi'); } catch (_) {} });
  const candles = oneMinuteBars('2026-10-14', '15:29:00').map((b, i) => ({ ...b, c: 150 + 12 * Math.sin(i / 17) }));
  await scalpChart(page, candles, 150);
  await page.evaluate(() => window.scrollTo(0, 0));
  const hair = () => page.evaluate(() => (window as any)._pfChartCanvas.crosshair);
  // Measured and pointed in one go, again if the page shifted in between.
  const pointAt = async (pane: 'price' | 'rsi') => {
    await expect.poll(async () => {
      const at = await page.evaluate((which) => {
        const c = (window as any)._pfChartCanvas, box = c.host.getBoundingClientRect(), p = c.projection;
        return { x: box.left + p.padL + p.plotW / 2, y: box.top + (which === 'rsi' ? p.rsiTop + p.rsiH * 0.25 : p.padT + p.plotH * 0.4) };
      }, pane);
      await page.mouse.move(at.x, at.y);
      return (await hair())?.pane;
    }).toBe(pane);
    return hair();
  };
  const onPrice = await pointAt('price');
  const pane = await page.evaluate(() => (window as any)._pfChartCanvas.paint.rsiPane);
  expect(Math.round(onPrice.bottom)).toBe(Math.round(pane.top + pane.h));
  const onRsi = await pointAt('rsi');
  // The label is the RSI where the line crosses: a quarter down 0-100 is ~75.
  const at = await page.evaluate((y) => (window as any)._pfChartCanvas.projection.rsiAt(y), onRsi.y);
  expect(Math.abs(Number(onRsi.label) - at)).toBeLessThan(0.01);
  expect(Math.abs(at - 75)).toBeLessThan(3);
  expect(await page.evaluate(() => (window as any)._pfChartCanvas.host.style.cursor)).toBe('crosshair');
});

test('Undo and Redo step through drawing changes, Clear included', async ({ page }) => {
  // Phil, 2026-10-20: "If I draw one or more TLs or Fibs and if I click clear,
  // all is getting cleared ... Give me a undo button or previous button".
  // 630 tall: below the Scalp freeze (>= 640), so the pinned stack does not
  // cover the chart this test draws on.
  await page.setViewportSize({ width: 1400, height: 630 });
  await page.addInitScript(() => { try { localStorage.setItem('pf_scalp_chart_rsi', '0'); localStorage.removeItem('pf_scalp_chart_drawings_v1'); } catch (_) {} });
  await scalpChart(page, oneMinuteBars('2026-10-14', '15:29:00'), 150);
  const tools = page.locator('#scalp-dock-chart .scalp-studio-tray .scalp-chart-tools');
  const undo = tools.locator('.scalp-undo-btn'), redo = tools.locator('.scalp-redo-btn');
  const count = () => page.evaluate(() => (window as any)._pfChartCanvas.data.drawings.length);
  const click = async (fx: number, fy: number) => {
    await page.locator('#scalp-dock-chart-body').scrollIntoViewIfNeeded();
    const box = (await page.locator('#scalp-dock-chart-body canvas').first().boundingBox())!;
    await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy);
  };
  await expect(undo).toBeDisabled();
  await expect(redo).toBeDisabled();
  // A trendline, then a Fib.
  await tools.locator('.scalp-line-btn').click();
  await click(0.3, 0.7); await click(0.5, 0.5);
  await expect.poll(count).toBe(1);
  await tools.locator('.scalp-fib-btn').click();
  await tools.locator('[data-fib-preset="half"]').click();
  await click(0.6, 0.7); await click(0.7, 0.4);
  await expect.poll(count).toBe(2);
  await expect(undo).toBeEnabled();
  // Clear takes both; Undo brings both back.
  await tools.locator('.scalp-clear-btn').click();
  await expect.poll(count).toBe(0);
  await undo.click();
  await expect.poll(count).toBe(2);
  // Undo again: the Fib goes, the trendline stays. Redo puts the Fib back.
  await undo.click();
  await expect.poll(count).toBe(1);
  expect(await page.evaluate(() => (window as any)._pfChartCanvas.data.drawings[0].kind)).toBe('line');
  await expect(redo).toBeEnabled();
  await redo.click();
  await expect.poll(count).toBe(2);
  // The keys do the same.
  await page.locator('#scalp-dock-chart-title').click();
  await page.keyboard.press('ControlOrMeta+z');
  await expect.poll(count).toBe(1);
  await page.keyboard.press('ControlOrMeta+Shift+z');
  await expect.poll(count).toBe(2);
  // What is on screen is what is kept.
  const kept = await page.evaluate(() => Object.values(JSON.parse(localStorage.getItem('pf_scalp_chart_drawings_v1') || '{}'))[0] as any);
  expect(kept.lines.length + kept.fibs.length).toBe(2);

  // A drag is ONE step: one Undo puts the line back exactly where it was
  // (Phil, 2026-10-21: "if I mistakenly moved the TL or fib, it not reverting
  // the original"). It used to save -- and count -- every pixel of the drag.
  const lineNow = () => page.evaluate(() => JSON.stringify((window as any)._pfChartCanvas.data.drawings.find((d: any) => d.kind === 'line')));
  const before = await lineNow();
  await page.evaluate(() => (window as any)._pfChartCanvas.host.scrollIntoView({ block: 'center' }));
  const ends = await page.evaluate(() => {
    const c = (window as any)._pfChartCanvas, dr = c.data.drawings.find((d: any) => d.kind === 'line'), box = c.host.getBoundingClientRect();
    const pt = (e: any) => [box.left + c.projection.xOf(e.t), box.top + c.projection.yOf(e.price)];
    return { a: pt(dr.a), b: pt(dr.b) };
  });
  // Selected first, as a line's ends are taken only once it is.
  await page.mouse.click((ends.a[0] + ends.b[0]) / 2, (ends.a[1] + ends.b[1]) / 2);
  const end = ends.b;
  await page.mouse.move(end[0], end[1]);
  await page.mouse.down();
  await page.mouse.move(end[0] + 70, end[1] - 60, { steps: 20 });
  await page.mouse.up();
  await expect.poll(lineNow).not.toBe(before);
  const after = await lineNow();
  await undo.click();
  await expect.poll(lineNow).toBe(before);
  await redo.click();
  await expect.poll(lineNow).toBe(after);
});

test('the broker pair spans its row with no BROKER label', async ({ page }) => {
  // Phil, 2026-10-20: "Remove the text broker and justify the both broker on the space correctly".
  await page.setViewportSize({ width: 1400, height: 1000 });
  await scalpChart(page, oneMinuteBars('2026-10-14', '15:29:00'), 150);
  const row = page.locator('#scalp-page .scalp-broker-row');
  await expect(row).not.toContainText('BROKER');
  await expect(page.locator('#scalp-broker-toggle')).toHaveAttribute('aria-label', 'Broker');
  const fit = await page.evaluate(() => {
    const r = document.querySelector('#scalp-page .scalp-broker-row')!.getBoundingClientRect();
    const t = document.getElementById('scalp-broker-toggle')!.getBoundingClientRect();
    const [d, z] = Array.from(document.querySelectorAll('#scalp-broker-toggle .scalp-toggle-btn')).map((b) => b.getBoundingClientRect().width);
    // The "Both" tick (2026-10-26) shares the row, after the pair.
    const both = document.querySelector('#scalp-page .scalp-both-brokers')!.getBoundingClientRect();
    return { gap: Math.round(r.width - t.width - both.width - 8), even: Math.abs(d - z) < 1,
      beside: both.left >= t.right && both.right <= r.right + 1 && both.top >= r.top - 1 && both.bottom <= r.bottom + 1 };
  });
  expect(fit.gap).toBeLessThanOrEqual(1);
  expect(fit.even).toBe(true);
  expect(fit.beside).toBe(true);
});

test('the Scalp desk freezes down to Active Positions; the rest scrolls beneath', async ({ page }) => {
  // Phil, 2026-10-20: "Let the page freeze starts from the Active positions".
  await page.setViewportSize({ width: 1400, height: 1000 });
  await scalpChart(page, oneMinuteBars('2026-10-14', '15:29:00'), 150);
  const at = (y: number) => page.evaluate(async (v) => {
    window.scrollTo(0, v);
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const card = document.querySelector('#scalp-page > .card')!;
    const head = card.querySelector(':scope > .ocp-head')!.getBoundingClientRect();
    const pos = card.querySelector(':scope > .scalp-positions-card')!.getBoundingClientRect();
    const chart = document.getElementById('scalp-dock-chart-body')!.getBoundingClientRect();
    const hero = document.querySelector('#scalp-page > .trading-workspace-head')!.getBoundingClientRect();
    return { head: Math.round(head.top), headBottom: Math.round(head.bottom), pos: Math.round(pos.top), chart: Math.round(chart.top), hero: Math.round(hero.bottom), stuck: card.classList.contains('pf-scalp-stuck') };
  }, y);
  const rest = await at(0);
  expect(rest.stuck).toBe(false);
  await at(500);
  await expect.poll(async () => (await at(500)).stuck).toBe(true);
  // Right under the Trading banner (not at the window's top, under the nav),
  // Active Positions flush under the engine line -- at two scroll depths.
  const settled = (y: number) => expect.poll(async () => {
    const q = await at(y);
    return q.stuck && Math.abs(q.head - q.hero) <= 1 && Math.abs(q.pos - q.headBottom) <= 1;
  }).toBe(true);
  await settled(500);
  const a = await at(500);
  await settled(900);
  const b = await at(900);
  expect(Math.abs(b.head - a.head)).toBeLessThanOrEqual(1);
  expect(b.chart).toBeLessThan(a.chart);  // the chart scrolled on under them
  // A short window keeps the old flow: nothing pinned below the banner. Short
  // means under 640 since 2026-10-22 (it was 820, and a Windows laptop at 125%
  // scaling, ~730 tall, never froze).
  await page.setViewportSize({ width: 1400, height: 620 });
  await page.evaluate(() => window.dispatchEvent(new Event('resize')));
  expect(await page.evaluate(() => getComputedStyle(document.querySelector('#scalp-page > .card > .scalp-positions-card')!).position)).toBe('static');
});
