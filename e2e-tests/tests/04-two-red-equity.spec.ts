/**
 * 04-two-red-equity.spec.ts
 *
 * The Equity page's second strategy. These are regression tests for the three
 * things that fail SILENTLY in this stack and so cannot be caught by reading
 * the code:
 *   1. A delegated action missing from PF_DELEGATED_ACTIONS — the click is
 *      simply ignored, with no console error.
 *   2. A renderer typo — the table comes out blank, and a green pytest run
 *      says nothing about it.
 *   3. The screen sending the Cascade's 1% pullback band instead of the
 *      ladder's 8% — the rows would look plausible and be the wrong scrips.
 */
import { test, expect, Page } from '@playwright/test';

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';

async function openEquity(page: Page) {
  await page.click('#nav-trading');
  await page.locator('.page-section.active-page [data-pf-trading-page="stock-terminal-page"]').click();
  await expect(page.locator('#stock-terminal-page')).toHaveClass(/active-page/);
}

async function login(page: Page) {
  // Offline layout/controls tests do not consume the live socket. A custom
  // localhost test port is intentionally outside production's WS allowlist.
  if (process.env.E2E_OFFLINE !== '0') await page.routeWebSocket('**/ws', () => {});
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const passwordInput = page.locator('#password-input');
  if (await passwordInput.isVisible()) {
    await passwordInput.fill(PIN);
    await page.click('#unlock-btn');
  } else {
    for (const digit of PIN.split('')) await page.click(`[data-val="${digit}"]`);
  }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
}

test('the Trading page carries the Equity section and its strategies', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
  page.on('pageerror', (err) => errors.push(String(err)));

  await login(page);

  const tab = page.locator('#nav-trading');
  await expect(tab).toContainText('Trading');
  await openEquity(page);
  await expect(page.locator('#nav-terminal')).toContainText('Equity');

  const cascade = page.locator('#equity-strategy-cascade');
  const tworeds = page.locator('#equity-strategy-tworeds');
  const desk = page.locator('#equity-strategy-desk');

  // Cash Cascade is the default section.
  await expect(cascade).toBeVisible();
  await expect(tworeds).toBeHidden();
  await expect(desk).toBeHidden();

  // Switch to the ladder.
  await page.click('[data-equity-strategy="tworeds"]');
  await expect(tworeds).toBeVisible();
  await expect(cascade).toBeHidden();
  await expect(desk).toBeHidden();

  // The manual desk is its own section, not a footer under both strategies.
  // Its panels must not appear while a strategy is showing.
  await expect(page.locator('#stock-terminal-search')).toBeHidden();
  await page.click('[data-equity-strategy="desk"]');
  await expect(desk).toBeVisible();
  await expect(cascade).toBeHidden();
  await expect(tworeds).toBeHidden();
  await expect(page.locator('#stock-terminal-search')).toBeVisible();
  await expect(page.locator('#stock-terminal-orders-body')).toBeVisible();

  await page.click('[data-equity-strategy="tworeds"]');
  await expect(tworeds).toBeVisible();
  await expect(desk).toBeHidden();

  // Its own panels are really there.
  await expect(page.locator('#tworeds-scan-panel')).toBeVisible();
  await expect(page.locator('#tworeds-setup-panel')).toBeVisible();
  await expect(page.locator('#tworeds-campaigns-panel')).toBeVisible();
  await expect(page.locator('#tworeds-scan-run')).toBeVisible();

  // The defaults the backtest chose are the ones selected.
  await expect(page.locator('#tworeds-min-fall')).toHaveValue('8');
  await expect(page.locator('#tworeds-target')).toHaveValue('0.75');
  await expect(page.locator('#tworeds-mother-timeframe')).toHaveValue('1d');

  // Switch back.
  await page.click('[data-equity-strategy="cascade"]');
  await expect(cascade).toBeVisible();
  await expect(tworeds).toBeHidden();

  expect(errors.filter((e) => !/favicon|manifest|Failed to load resource/i.test(e))).toEqual([]);
});

test('the start button is wired through the delegated-action allowlist', async ({ page }) => {
  await login(page);
  await openEquity(page);
  await page.click('[data-equity-strategy="tworeds"]');

  // No scrip typed: the handler must run and complain, which is the proof the
  // action reached it at all. A missing allowlist entry fails SILENTLY.
  await page.click('#tworeds-start');
  await expect(page.locator('#tworeds-form-status')).toContainText('Pick a scrip first', { timeout: 5000 });

  // Now a scrip but no mother.
  await page.fill('#tworeds-symbol', 'RELIANCE');
  await page.click('#tworeds-start');
  await expect(page.locator('#tworeds-form-status')).toContainText('mother candle', { timeout: 5000 });
});

test('the ladder screen renders its own columns and arithmetic', async ({ page }) => {
  const seen: string[] = [];
  await page.route('**/api/terminal/cascade/scan?**', async (route) => {
    const url = route.request().url();
    seen.push(url);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        scanned_at: new Date().toISOString(),
        universe: 223,
        no_history: 0,
        candidates: [
          {
            symbol: 'PAYTM', name: 'One 97', last_price: 800, strength_pct: 12.5,
            pullback_pct: 20, recent_high: 1000, affordable_shares: 250,
            rungs_fundable: 3, score: 1, etf: false,
          },
        ],
        rejected_sample: [],
        rejected_total: 0,
        cached: false,
      }),
    });
  });

  await login(page);
  await openEquity(page);
  await page.click('[data-equity-strategy="tworeds"]');
  await page.fill('#tworeds-scan-capital', '200000');
  await page.click('#tworeds-scan-run');

  const table = page.locator('#tworeds-scan-body table');
  await expect(table).toBeVisible({ timeout: 10_000 });

  // The screen must ask the server for the 8% band, not the Cascade's 1%.
  expect(seen.some((url) => url.includes('min_pullback=8'))).toBeTruthy();

  // Ladder columns, not Cascade columns.
  await expect(table).toContainText('First buy');
  await expect(table).toContainText('Target');
  await expect(table).not.toContainText('Rungs');

  // 20% down on a 200,000 purse commits 40,000, which is 50 shares at 800.
  const row = page.locator('#tworeds-scan-body tr[data-symbol="PAYTM"]');
  await expect(row).toContainText('₹40,000');
  await expect(row).toContainText('50');
  // Target = 800 + 0.75 x (1000 - 800) = 950.
  await expect(row).toContainText('₹950');

  // No "Use" button in ladder mode; clicking the row fills the campaign form.
  await expect(row.locator('.cascade-scan-pick')).toHaveCount(0);
  await row.locator('td').first().click();
  await expect(page.locator('#tworeds-symbol')).toHaveValue('PAYTM');
});

test('the mother finder lists live mothers and picking one fills the form', async ({ page }) => {
  await page.route('**/api/two-red/mothers?**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok', symbol: 'RELIANCE', timeframe: '1d', scanned: 400, last_price: 1200,
        mothers: [
          { timestamp: '2026-07-14T09:15:00', high: 1500, low: 1470, state: 'ready',
            fall_pct: 12.4, now_pct: 20, reclaimed_at: null, bars_since: 20 },
          { timestamp: '2026-06-02T09:15:00', high: 1400, low: 1380, state: 'waiting',
            fall_pct: 3.1, now_pct: 14, reclaimed_at: null, bars_since: 50 },
          { timestamp: '2026-04-01T09:15:00', high: 1300, low: 1280, state: 'spent',
            fall_pct: 2.0, now_pct: 8, reclaimed_at: '2026-04-09T09:15:00', bars_since: 90 },
        ],
      }),
    });
  });

  await login(page);
  await openEquity(page);
  await page.click('[data-equity-strategy="tworeds"]');
  await page.fill('#tworeds-symbol', 'RELIANCE');
  await page.click('#tworeds-find-mothers');

  const list = page.locator('#tworeds-mothers');
  await expect(list).toBeVisible({ timeout: 10_000 });
  await expect(list).toContainText('ready');
  await expect(list).toContainText('waiting');
  await expect(list).toContainText('spent');
  await expect(page.locator('#tworeds-form-status')).toContainText('1 ready to take');

  // A spent mother cannot be chosen — starting on it would void immediately.
  await expect(list.locator('[data-two-red-mother="2026-04-01T09:15:00"]')).toBeDisabled();

  // Picking the ready one fills the timestamp the Start button reads.
  await list.locator('[data-two-red-mother="2026-07-14T09:15:00"]').click();
  await expect(page.locator('#tworeds-mother-timestamp')).toHaveValue('2026-07-14T09:15');
});

test('the ladder chart is the Canvas renderer, not a hand-rolled SVG', async ({ page }) => {
  await page.route('**/api/two-red/chart?**', async (route) => {
    const candles = [];
    let base = 1500;
    for (let i = 0; i < 60; i += 1) {
      base -= 4;
      candles.push({
        t: new Date(Date.UTC(2026, 5, 1 + i, 3, 45)).toISOString().replace('Z', ''),
        o: base, h: base + 6, l: base - 6, c: base + 1, is_mother: i === 0,
      });
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok', symbol: 'RELIANCE', timeframe: '1d', candles,
        mother: { high: 1506, low: 1494 },
        lines: [
          { price: 1506, label: 'MOTHER HIGH', filled: true },
          { price: 1385.52, label: 'FIRST BUY BELOW (8%)', filled: false },
        ],
        entries: [], exits: [], avg_entry_price: null, tp_price: null, tp_label: '',
      }),
    });
  });

  await login(page);
  await openEquity(page);
  await page.click('[data-equity-strategy="tworeds"]');
  await page.fill('#tworeds-symbol', 'RELIANCE');
  await page.click('#tworeds-chart-btn');

  const overlay = page.locator('#tworeds-chart-overlay');
  await expect(overlay).toHaveClass(/is-open/, { timeout: 10_000 });

  // The one renderer draws to a CANVAS — two of them, a main surface and an
  // overlay for the crosshair. An SVG here would mean a second renderer had
  // crept back in, which is the whole thing being prevented.
  const canvas = page.locator('#pf-bench-canvas-main');
  await expect(canvas).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('#tworeds-chart canvas')).toHaveCount(2);
  await expect(page.locator('#tworeds-chart svg')).toHaveCount(0);

  // And it actually painted something, rather than mounting an empty surface.
  const painted = await canvas.evaluate((el: HTMLCanvasElement) => {
    const ctx = el.getContext('2d');
    if (!ctx || !el.width || !el.height) return 0;
    const data = ctx.getImageData(0, 0, el.width, el.height).data;
    const seen = new Set<string>();
    for (let i = 0; i < data.length; i += 4) seen.add(`${data[i]},${data[i + 1]},${data[i + 2]}`);
    return seen.size;
  });
  expect(painted).toBeGreaterThan(3);

  await expect(page.locator('#tworeds-chart-meta')).toContainText('2 levels');

  // Five charts to look at, 15m through 1W.
  await expect(page.locator('#tworeds-chart-tf [data-tworeds-tf]')).toHaveCount(5);
  for (const tf of ['15m', '1h', '4h', '1d', '1w']) {
    await expect(page.locator(`#tworeds-chart-tf [data-tworeds-tf="${tf}"]`)).toBeVisible();
  }
  await page.click('#tworeds-chart-tf [data-tworeds-tf="4h"]');
  await expect(page.locator('#tworeds-chart-tf [data-tworeds-tf="4h"]')).toHaveClass(/is-active/);
  await expect(page.locator('#pf-bench-canvas-main')).toBeVisible();

  // Closing must tear the canvas down, or the renderer's observers leak.
  await page.click('[data-pf-action="hideTwoRedChart"]');
  await expect(overlay).not.toHaveClass(/is-open/);
  await expect(page.locator('#tworeds-chart canvas')).toHaveCount(0);
});

test('the scanner chart is the Canvas renderer and carries all five timeframes', async ({ page }) => {
  const asked: string[] = [];
  await page.route('**/api/terminal/cascade/scan?**', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok', scanned_at: new Date().toISOString(), universe: 223, no_history: 0,
        candidates: [{
          symbol: 'PHOENIXLTD', name: 'Phoenix Mills', last_price: 1908.7, strength_pct: 10.7,
          pullback_pct: 12.6, recent_high: 2149, affordable_shares: 104, rungs_fundable: 3,
          score: 1, etf: false,
        }],
        rejected_sample: [], rejected_total: 0, cached: false,
      }),
    });
  });
  await page.route('**/api/terminal/cascade/scan/chart?**', async (route) => {
    const url = new URL(route.request().url());
    asked.push(url.searchParams.get('timeframe') || '(none)');
    const candles = [];
    let base = 2149;
    for (let i = 0; i < 80; i += 1) {
      base -= 3;
      candles.push({
        t: new Date(Date.UTC(2026, 4, 1 + i, 3, 45)).toISOString().replace('Z', ''),
        o: base, h: base + 8, l: base - 8, c: base + 2,
      });
    }
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok', symbol: 'PHOENIXLTD', name: 'Phoenix Mills', chart_mode: 'native_ohlc',
        timeframe: url.searchParams.get('timeframe') || '1d', candles,
        recent_high: 2149, recent_high_lookback: 20, last_price: 1908.7, pullback_pct: 12.6,
      }),
    });
  });

  await login(page);
  await openEquity(page);
  await page.click('[data-equity-strategy="tworeds"]');
  await page.click('#tworeds-scan-run');
  await expect(page.locator('#tworeds-scan-body table')).toBeVisible({ timeout: 10_000 });

  await page.click('#tworeds-scan-body .cascade-scan-chart-btn');

  // The scan chart draws through the ONE Canvas renderer, not an SVG.
  await expect(page.locator('#pf-bench-canvas-main')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('.cascade-scan-chart svg')).toHaveCount(0);

  // All five timeframes are offered on the scanner chart too.
  const strip = page.locator('.cascade-scan-chart-tf');
  await expect(strip).toBeVisible();
  await expect(strip.locator('[data-scan-tf]')).toHaveCount(5);
  for (const tf of ['15m', '1h', '4h', '1d', '1w']) {
    await expect(strip.locator(`[data-scan-tf="${tf}"]`)).toBeVisible();
  }
  expect(asked).toContain('1d');

  // Switching redraws the SAME row rather than closing it.
  await strip.locator('[data-scan-tf="15m"]').click();
  await expect(page.locator('.cascade-scan-chart-tf [data-scan-tf="15m"]')).toHaveClass(/is-active/, { timeout: 10_000 });
  await expect(page.locator('#pf-bench-canvas-main')).toBeVisible();
  expect(asked).toContain('15m');
});

test('the two-red status endpoint answers and declares itself paper-only', async ({ page }) => {
  await login(page);
  const body = await page.evaluate(async () => {
    const res = await fetch('/api/two-red/status', { credentials: 'same-origin' });
    return { ok: res.ok, data: await res.json() };
  });
  expect(body.ok).toBeTruthy();
  expect(body.data.live.enabled).toBe(false);
  expect(body.data.defaults.min_fall_pct).toBe(8);
  expect(body.data.defaults.target_fraction).toBe(0.75);
  expect(Array.isArray(body.data.campaigns)).toBeTruthy();
});

const SCAN_ONE = {
  status: 'ok', scanned_at: new Date().toISOString(), universe: 223, no_history: 0,
  candidates: [{
    symbol: 'PHOENIXLTD', name: 'Phoenix Mills', last_price: 1908.7, strength_pct: 10.7,
    pullback_pct: 12.6, recent_high: 2149, affordable_shares: 104, rungs_fundable: 3,
    score: 1, etf: false,
  }],
  rejected_sample: [], rejected_total: 0, cached: false,
};

async function openScanChart(page: Page) {
  await page.route('**/api/terminal/cascade/scan?**', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(SCAN_ONE),
  }));
  await login(page);
  // Shrink the chart deadline so the timeout BEHAVIOUR can be asserted without
  // waiting the real 45 seconds for it.
  await page.evaluate(() => { (window as any).pfScanChartTimeoutMs = 400; });
  await openEquity(page);
  await page.click('[data-equity-strategy="cascade"]');
  await page.click('#cascade-scan-run');
  await expect(page.locator('#cascade-scan-body table')).toBeVisible({ timeout: 10_000 });
  await page.click('#cascade-scan-body .cascade-scan-chart-btn');
}

test('a chart that never answers times out and offers a retry', async ({ page }) => {
  // "Loading PHOENIXLTD 1d candles…" sat on screen indefinitely: the fetch had
  // no deadline, so anything slow upstream -- Dhan retrying a 30s call, the
  // account-wide rate budget, a browser connection queued behind the page's
  // pollers -- left the row on its placeholder with nothing to click.
  await page.route('**/api/terminal/cascade/scan/chart?**', () => { /* never answers */ });
  await openScanChart(page);
  const error = page.locator('.cascade-scan-chart-error');
  await expect(error).toBeVisible({ timeout: 15_000 });
  await expect(error).toContainText('did not answer');
  await expect(page.locator('[data-scan-chart-retry]')).toBeVisible();
});

test('a refused chart shows the server reason, not "Chart failed"', async ({ page }) => {
  // error_handlers.py answers 4xx as { success:false, error:{...detail} }. The
  // scanner read `data.detail`, which is never there, so every refusal said
  // "Chart failed" and nothing about why.
  await page.route('**/api/terminal/cascade/scan/chart?**', route => route.fulfill({
    status: 400, contentType: 'application/json',
    body: JSON.stringify({
      success: false,
      error: { code: 400, title: 'Bad Request', message: 'generic',
               detail: 'Connect a Dhan account to load the scanner chart.' },
    }),
  }));
  await openScanChart(page);
  const error = page.locator('.cascade-scan-chart-error');
  await expect(error).toBeVisible({ timeout: 15_000 });
  await expect(error).toContainText('Connect a Dhan account');
  await expect(error).not.toContainText('Chart failed');
});

/* ── 2026-08-12: three reports in one message ─────────────────────────
   "In 2 red-ladder section, the chart is flickering" · "Move the Campaigns
   under THE RULE" · "add a chart button before delete or stop for each
   campaign". The flicker's cause: the 15s poll rewrote both tables with
   innerHTML whether anything changed or not, and behind the chart overlay's
   blurred translucent backdrop every wipe read as a flash of the picture. */

function twoRedStatus() {
  return {
    status: 'ok',
    campaigns: [
      { symbol: 'LUPIN', status: 'VOID', running: false, quantity: 0, rung: 0, rungs: 3,
        mother: { timestamp: '2026-07-16T09:15:00+05:30', timeframe: '1h', high: 2517.3 } },
      { symbol: 'PHOENIXLTD', status: 'WATCHING', running: true, quantity: 0, rung: 0, rungs: 3,
        mother: { timestamp: '2026-08-11T09:15:00+05:30', timeframe: '1d', high: 1939.5 } },
    ],
    closed: [],
  };
}

async function openTwoReds(page: Page, status: object) {
  await page.route('**/api/two-red/status**', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(status),
  }));
  await login(page);
  await openEquity(page);
  await page.click('[data-equity-strategy="tworeds"]');
  await expect(page.locator('#tworeds-campaigns-body table')).toBeVisible({ timeout: 10_000 });
}

test('an unchanged status poll does not rewrite the campaigns table', async ({ page }) => {
  await openTwoReds(page, twoRedStatus());
  await page.evaluate(() => {
    (window as any).__wipes = 0;
    const mo = new MutationObserver(() => { (window as any).__wipes++; });
    mo.observe(document.getElementById('tworeds-campaigns-body')!, { childList: true });
  });
  // Same payload, three more polls' worth of refreshes.
  for (let i = 0; i < 3; i += 1) await page.evaluate(() => (window as any).refreshTwoRedCampaigns());
  await page.waitForTimeout(400);
  expect(await page.evaluate(() => (window as any).__wipes)).toBe(0);
});

test('nothing repaints behind an open chart, and closing it catches up', async ({ page }) => {
  await openTwoReds(page, twoRedStatus());
  await page.route('**/api/two-red/chart?**', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      status: 'ok', symbol: 'PHOENIXLTD', timeframe: '1d',
      candles: [{ t: '2026-08-11T09:15:00+05:30', o: 1910, h: 1939.5, l: 1905.7, c: 1920, is_mother: true }],
      mother: { high: 1939.5, low: 1905.7 }, lines: [], entries: [], exits: [],
    }),
  }));
  await page.fill('#tworeds-symbol', 'PHOENIXLTD');
  await page.click('#tworeds-chart-btn');
  await expect(page.locator('#tworeds-chart-overlay')).toHaveClass(/is-open/);

  // While the overlay is up, a poll must not touch the table at all — the
  // wipe behind the blurred backdrop IS the reported flicker.
  await page.evaluate(() => {
    (window as any).__wipes = 0;
    const mo = new MutationObserver(() => { (window as any).__wipes++; });
    mo.observe(document.getElementById('tworeds-campaigns-body')!, { childList: true, subtree: true });
  });
  await page.evaluate(() => (window as any).refreshTwoRedCampaigns());
  await page.waitForTimeout(400);
  expect(await page.evaluate(() => (window as any).__wipes)).toBe(0);

  // Closing refreshes once, so the table is not stale after a long look.
  let polled = 0;
  await page.route('**/api/two-red/status**', route => {
    polled += 1;
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(twoRedStatus()) });
  });
  await page.click('[data-pf-action="hideTwoRedChart"]');
  await expect.poll(() => polled).toBeGreaterThan(0);
});

test('Campaigns sits under THE RULE, in the same column', async ({ page }) => {
  await openTwoReds(page, twoRedStatus());
  // Same section: the rule strip and the campaigns table share one panel...
  const panel = page.locator('#tworeds-campaigns-panel');
  await expect(panel.locator('#tworeds-rule-flow')).toHaveCount(1);
  await expect(panel.locator('#tworeds-campaigns-body')).toHaveCount(1);
  // ...and the table is BELOW the rule, not beside it.
  const rule = (await page.locator('#tworeds-rule-flow').boundingBox())!;
  const table = (await page.locator('#tworeds-campaigns-body').boundingBox())!;
  expect(table.y).toBeGreaterThan(rule.y + rule.height - 1);
});

test('each campaign row charts ITS OWN mother, ahead of Stop or Delete', async ({ page }) => {
  await openTwoReds(page, twoRedStatus());

  let asked: URLSearchParams | null = null;
  await page.route('**/api/two-red/chart?**', route => {
    asked = new URL(route.request().url()).searchParams;
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      status: 'ok', symbol: 'LUPIN', timeframe: '1h',
      candles: [{ t: '2026-07-16T09:15:00+05:30', o: 2500, h: 2517.3, l: 2494, c: 2510, is_mother: true }],
      mother: { high: 2517.3, low: 2494 }, lines: [], entries: [], exits: [],
    }) });
  });

  // The form deliberately holds a DIFFERENT scrip: the row must win.
  await page.fill('#tworeds-symbol', 'PHOENIXLTD');

  const lupin = page.locator('#tworeds-campaigns-body tr', { hasText: 'LUPIN' });
  // Chart comes before the destructive action on every row.
  const buttons = lupin.locator('button');
  await expect(buttons.first()).toHaveText('Chart');
  await expect(lupin.locator('[data-two-red-delete]')).toHaveCount(1);

  await lupin.locator('[data-two-red-chart]').click();
  await expect(page.locator('#tworeds-chart-overlay')).toHaveClass(/is-open/);
  await expect.poll(() => asked).not.toBeNull();
  expect(asked!.get('symbol')).toBe('LUPIN');
  expect(asked!.get('mother_timestamp')).toBe('2026-07-16T09:15');
  // A 1H mother opens on its own chart, not the default daily.
  expect(asked!.get('timeframe')).toBe('1h');
  await expect(page.locator('#tworeds-chart-tf [data-tworeds-tf="1h"]')).toHaveClass(/is-active/);
});
