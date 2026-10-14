import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-14: "shade the supertrend like the one we see in trading view".
// Green under the price while the trend is up, red over it while it is down,
// the band between the line and each candle's body middle filled faintly, and
// a break at the flip rather than a diagonal across it.

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

// Count painted pixels near each Supertrend colour, line and shading alike.
async function stPixels(page: Page, payload: unknown) {
  return page.evaluate((data) => {
    const host = document.createElement('div');
    host.style.cssText = 'position:fixed;left:0;top:0;width:900px;height:420px;z-index:99999;background:#0b1220';
    document.body.appendChild(host);
    // @ts-ignore
    window.pfBenchDrawChart(host, data);
    return new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(() => {
      const canvas = host.querySelector('canvas') as HTMLCanvasElement;
      const px = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
      let green = 0, red = 0;
      for (let i = 0; i < px.length; i += 4) {
        const [r, g, b, a] = [px[i], px[i + 1], px[i + 2], px[i + 3]];
        if (!a) continue;
        if (g > r + 25 && g > b + 15 && Math.abs(r - 76) < 70 && Math.abs(b - 80) < 70) green++;   // #4caf50 and its tint
        if (r > g + 60 && r > b + 40 && g > 40) red++;                                              // #ff5252 and its tint
      }
      host.remove();
      done({ green, red });
    })));
  }, payload);
}

function sample(withDirection: boolean) {
  const t0 = Math.floor(new Date('2026-10-13T09:15:00+05:30').getTime() / 1000);
  const candles = [], st = [];
  let px = 22400;
  for (let i = 0; i < 60; i++) {
    const up = i < 30, o = px, c = px + (up ? 12 : -12);
    candles.push({ t: t0 + i * 300, o, h: Math.max(o, c) + 4, l: Math.min(o, c) - 4, c });
    const v = up ? Math.min(o, c) - 40 : Math.max(o, c) + 40;
    st.push(withDirection ? { t: t0 + i * 300, v, d: up ? 1 : -1 } : { t: t0 + i * 300, v });
    px = c;
  }
  return { candles, entries: [], exits: [], lines: [], overlays: [], indicators: { supertrend: st, atr_period: 10, multiplier: 1.5 } };
}

test('the Supertrend is green and shaded below an up-trend, red and shaded above a down-trend', async ({ page }) => {
  await login(page);
  await page.waitForFunction(() => typeof (window as any).pfBenchDrawChart === 'function');
  const shaded: any = await stPixels(page, sample(true));
  // A 1.6px line over half the chart is a few hundred pixels; the shading is thousands.
  expect(shaded.green).toBeGreaterThan(2000);
  expect(shaded.red).toBeGreaterThan(2000);
  // An older payload with no direction is read off the candles the same way.
  const inferred: any = await stPixels(page, sample(false));
  expect(inferred.green).toBeGreaterThan(2000);
  expect(inferred.red).toBeGreaterThan(2000);
});

test('the desk chart\'s Supertrend runs are shaded the same way', async ({ page }) => {
  await login(page);
  await page.waitForFunction(() => typeof (window as any).pfBenchDrawChart === 'function');
  const base: any = sample(true);
  const st = base.indicators.supertrend;
  const run = (dir: number) => ({ label: '', color: dir > 0 ? '#4caf50' : '#ff5252', width: 1.6, supertrend: dir,
    points: st.filter((p: any) => p.d === dir).map((p: any) => ({ t: p.t, price: p.v })) });
  const desk = { ...base, indicators: {}, overlays: [run(1), run(-1)] };
  const shaded: any = await stPixels(page, desk);
  expect(shaded.green).toBeGreaterThan(2000);
  expect(shaded.red).toBeGreaterThan(2000);
});
