import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-15: "I need a small status monitor to be displayed after 15:00
// for Gap carry to see the EMA and RSI status". The card shows on weekdays
// from 15:00 to 15:40 IST, with the rule's own two numbers.

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';

async function openGapCarry(page: Page, at: string) {
  await page.clock.setFixedTime(new Date(at));
  await page.route('**/api/gap-carry/paper/status', (route) => route.fulfill({
    json: { status: 'not_started', mode: 'paper', live_available: true, auto: {}, timeframes: ['5m', '15m'] },
  }));
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const pw = page.locator('#password-input');
  if (await pw.isVisible()) { await pw.fill(PIN); await page.click('#unlock-btn'); }
  else { for (const d of PIN.split('')) await page.click(`[data-val="${d}"]`); }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
  await page.waitForFunction(() => !!(history.state && (history.state as { page?: string }).page));
  await page.click('#nav-trading');
  await page.locator('.page-section.active-page [data-pf-trading-page="options-cascade-page"]').click();
  await expect(page.locator('#options-cascade-page')).toHaveClass(/active-page/);
  await page.click('#oc-tabbtn-gapcarry');
}

test('from 15:00 the card shows close vs EMA20 and RSI against the rule', async ({ page }) => {
  let asked = 0;
  await page.route('**/api/gap-carry/signal-monitor', (route) => {
    asked += 1;
    return route.fulfill({ json: {
      status: 'ok', timeframe: '5m', bar_start: '15:00', bar_end: '15:05', close: 22948.35, ema: 22990.28, ema_period: 20,
      rsi: 30.9, rsi_period: 14, rsi_call: 70, rsi_put: 30, above_ema: false, side: null, entry_time: '15:10',
    } });
  });
  await openGapCarry(page, '2026-10-15T15:07:00+05:30');
  const card = page.locator('#oc-gap-signal');
  await expect(card).toBeVisible();
  await expect(card).toContainText('No trade yet');
  await expect(card).toContainText('bar 15:00–15:05 · 5M');
  await expect(card).toContainText('below 22,990.28');
  await expect(card).toContainText('30.9');
  await expect(card).toContainText('PE needs ≤ 30 · 0.9 to go');
  expect(asked).toBeGreaterThan(0);
});

test('after 15:10 it says what was decided', async ({ page }) => {
  await page.route('**/api/gap-carry/signal-monitor', (route) => route.fulfill({ json: {
    status: 'ok', timeframe: '5m', bar_start: '15:05', bar_end: '15:10', close: 23110.5, ema: 23050.1, ema_period: 20,
    rsi: 72.4, rsi_period: 14, rsi_call: 70, rsi_put: 30, above_ema: true, side: 'CE', entry_time: '15:10',
    decided: { side: 'CE', reason: 'close above EMA20, RSI 72.4 >= 70' },
  } }));
  await openGapCarry(page, '2026-10-15T15:12:00+05:30');
  const card = page.locator('#oc-gap-signal');
  await expect(card).toContainText('CE taken at 15:10');
  await expect(card).toContainText('CE needs ≥ 70 ✓');
});

test('before 15:00 there is no card and nothing is asked', async ({ page }) => {
  let asked = 0;
  await page.route('**/api/gap-carry/signal-monitor', (route) => { asked += 1; return route.fulfill({ json: { status: 'empty' } }); });
  await openGapCarry(page, '2026-10-15T14:30:00+05:30');
  await page.waitForTimeout(3500); // past a status poll
  await expect(page.locator('#oc-gap-signal')).toBeHidden();
  expect(asked).toBe(0);
});
