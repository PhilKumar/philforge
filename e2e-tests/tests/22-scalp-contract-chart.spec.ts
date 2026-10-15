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
  // It wears the site's own chart icon, not an arrow.
  await expect(btn.locator('#scalp-contract-chart-ico svg rect')).toHaveCount(3);
  await expect(btn).not.toContainText('↗');

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

  // Phil, 2026-10-15: on a two-column window the chart sits beside the form
  // and follows it; the button just brings it into view.
  await btn.click();
  const dock = page.locator('#scalp-dock-chart');
  await expect(dock).toBeVisible();
  await expect(page.locator('#scalp-option-chart-overlay.is-open')).toHaveCount(0);
  await expect(page.locator('#scalp-dock-chart-title')).toHaveText('NIFTY 22700PE');
  await expect(page.locator('#scalp-dock-chart-body canvas').first()).toBeVisible();
  const sideBySide = await page.evaluate(() => {
    const form = document.querySelector('#scalp-page .scalp-entry-card')!.getBoundingClientRect();
    const d = document.getElementById('scalp-dock-chart')!.getBoundingClientRect();
    return d.left >= form.right && d.width > 300 && d.height > 400;
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
  await expect(page.locator('#scalp-auto-exits')).toBeChecked();
  await expect(page.locator('#scalp-sl-prem')).toHaveValue('380.85');
  await expect(page.locator('#scalp-target-prem')).toHaveValue('580.85');
  expect(await page.locator('#scalp-sl-prem').evaluate((el: HTMLInputElement) => el.readOnly)).toBe(true);

  // The price moves: so do they.
  await page.evaluate(() => {
    (window as unknown as { _scalpCurrentLTP: number })._scalpCurrentLTP = 500;
    (window as unknown as { updateScalpMargin: () => void }).updateScalpMargin();
  });
  await expect(page.locator('#scalp-sl-prem')).toHaveValue('400.00');
  await expect(page.locator('#scalp-target-prem')).toHaveValue('600.00');

  // Sent as distances, for the server to measure from the price it trades at.
  await page.evaluate(() => (window as unknown as { submitScalpEntry: (d: string) => Promise<void> }).submitScalpEntry('BUY'));
  await expect.poll(() => sent).not.toBeNull();
  expect([sent.sl_points, sent.target_points]).toEqual([100, 100]);

  // Off: the prices are typed by hand again and no points are sent.
  sent = null;
  // The switch lives under Advanced.
  await page.locator('#scalp-page details.scalp-advanced > summary').click();
  await page.locator('#scalp-auto-exits').uncheck();
  expect(await page.locator('#scalp-sl-prem').evaluate((el: HTMLInputElement) => el.readOnly)).toBe(false);
  await page.evaluate(() => (window as unknown as { submitScalpEntry: (d: string) => Promise<void> }).submitScalpEntry('BUY'));
  await expect.poll(() => sent).not.toBeNull();
  expect(sent.sl_points).toBeUndefined();
});
