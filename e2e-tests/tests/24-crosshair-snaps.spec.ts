import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-14: "the cross hair has to jump/dragged correctly on the minute
// and not all the places". The vertical line steps from candle to candle, as on
// TradingView, and its time chip reads that candle's own time.

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';

async function login(page: Page) {
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const pw = page.locator('#password-input');
  if (await pw.isVisible()) { await pw.fill(PIN); await page.click('#unlock-btn'); }
  else { for (const d of PIN.split('')) await page.click(`[data-val="${d}"]`); }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
}

test('the crosshair\'s vertical line sits on a candle, never between two', async ({ page }) => {
  await login(page);
  await page.waitForFunction(() => typeof (window as any).pfBenchDrawChart === 'function');
  await page.evaluate(() => {
    const t0 = Math.floor(new Date('2026-10-13T09:15:00+05:30').getTime() / 1000);
    const candles = Array.from({ length: 40 }, (_, i) => ({ t: t0 + i * 300, o: 100 + i, h: 104 + i, l: 97 + i, c: 102 + i }));
    const host = document.createElement('div');
    host.id = 'xhair-host';
    host.style.cssText = 'position:fixed;left:0;top:0;width:900px;height:420px;z-index:99999;background:#0b1220';
    document.body.appendChild(host);
    (window as any).pfBenchDrawChart(host, { candles, entries: [], exits: [], lines: [], overlays: [] });
  });
  // Where candles 10 and 11 stand, in page pixels.
  const geo = await page.evaluate(() => {
    const c = (window as any)._pfChartCanvas, p = c.projection, rect = c.host.querySelector('canvas').getBoundingClientRect();
    const t0 = c.data.candles[10].t, t1 = c.data.candles[11].t;
    return { left: rect.left, top: rect.top, x10: p.xOf(t0), x11: p.xOf(t1), midY: p.padT + p.plotH * 0.5, probeY: p.padT + 6 };
  });
  async function lineAt(fraction: number) {
    const x = geo.x10 + (geo.x11 - geo.x10) * fraction;
    await page.mouse.move(geo.left + x, geo.top + geo.midY);
    return page.evaluate((probeY) => {
      const overlay = document.querySelector('#xhair-host #pf-bench-canvas-overlay') as HTMLCanvasElement;
      const scale = overlay.width / overlay.getBoundingClientRect().width;
      const row = overlay.getContext('2d')!.getImageData(0, Math.round(probeY * scale), overlay.width, 1).data;
      const hits: number[] = [];
      for (let i = 0; i < row.length; i += 4) if (row[i + 3] > 40) hits.push(i / 4 / scale);
      return hits.length ? hits.reduce((a, b) => a + b, 0) / hits.length : null;
    }, geo.probeY);
  }
  // A third of the way across sits on candle 10; two thirds, on candle 11.
  expect(await lineAt(0.3)).toBeCloseTo(geo.x10, 0);
  expect(await lineAt(0.45)).toBeCloseTo(geo.x10, 0);
  expect(await lineAt(0.7)).toBeCloseTo(geo.x11, 0);
});
