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
  const btn = page.locator('#scalp-contract-chart-btn');
  await expect(btn).toBeVisible();

  // It sits on the broker's row, to the right of the switch, inside the panel.
  const placed = await page.evaluate(() => {
    const row = document.querySelector('.scalp-broker-row')!.getBoundingClientRect();
    const sw = document.getElementById('scalp-broker-toggle')!.getBoundingClientRect();
    const b = document.getElementById('scalp-contract-chart-btn')!.getBoundingClientRect();
    return b.left >= sw.right && b.right <= row.right + 1 && b.top >= row.top - 1 && b.bottom <= row.bottom + 1;
  });
  expect(placed).toBe(true);

  // Point the form at a contract, as picking a strike does. The expiry is
  // whichever one the page has loaded for itself.
  await page.waitForFunction(() => !!(document.getElementById('scalp-expiry') as HTMLSelectElement).value);
  const expiry = await page.evaluate(() => {
    const w = window as unknown as { _scalpCurrentLTP: number; _syncScalpContractChartLabel: () => void };
    (document.getElementById('scalp-underlying') as HTMLSelectElement).value = 'NIFTY';
    (document.getElementById('scalp-option-type') as HTMLInputElement).value = 'PE';
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '22700';
    w._scalpCurrentLTP = 131.5;
    w._syncScalpContractChartLabel();
    return (document.getElementById('scalp-expiry') as HTMLSelectElement).value;
  });
  await expect(page.locator('#scalp-contract-chart-label')).toHaveText('22700 PE chart');
  await expect(btn).toBeEnabled();

  await btn.click();
  await expect(page.locator('#scalp-option-chart-overlay')).toHaveClass(/is-open/);
  await expect(page.locator('#scalp-option-chart-title')).toHaveText('NIFTY 22700PE');
  await expect(page.locator('#scalp-option-chart-body canvas').first()).toBeVisible();
  const first = asked[0].searchParams;
  expect([first.get('underlying'), first.get('strike'), first.get('expiry'), first.get('option_type'), first.get('timeframe')])
    .toEqual(['NIFTY', '22700', expiry, 'PE', '5m']);
  expect(first.get('live_price')).toBe('131.5');

  // Another timeframe is asked for the same contract.
  await page.locator('#scalp-option-chart-actions').getByRole('radio', { name: /15m/i }).click();
  await expect.poll(() => asked.some((u) => u.searchParams.get('timeframe') === '15m' && u.searchParams.get('strike') === '22700')).toBe(true);

  // Closed, it stops asking.
  await page.keyboard.press('Escape');
  await expect(page.locator('#scalp-option-chart-overlay')).not.toHaveClass(/is-open/);
  const settled = asked.length;
  await page.waitForTimeout(2600);
  expect(asked.length).toBe(settled);

  // With no strike there is nothing to chart, and the button says so.
  await page.evaluate(() => {
    (document.getElementById('scalp-strike') as HTMLInputElement).value = '';
    (window as unknown as { _syncScalpContractChartLabel: () => void })._syncScalpContractChartLabel();
  });
  await expect(btn).toBeDisabled();
  await expect(page.locator('#scalp-contract-chart-label')).toHaveText('Option chart');
});
