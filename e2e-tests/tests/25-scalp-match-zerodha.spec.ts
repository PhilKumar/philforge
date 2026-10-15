import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-14: a Zerodha scalp's exit was refused and he sold by hand in
// Kite, so the row kept the site's prices (+728) instead of Kite's (+880).
// "Match from Zerodha" re-books the row from Kite's own fills, on the day only.

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

test('Scalp history: a live Zerodha row from today can be matched with Kite', async ({ page }) => {
  await login(page);
  const today = await page.evaluate(() => {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  });
  const row = (id: number, extra: Record<string, unknown>) => ({
    trade_id: id, underlying: 'SENSEX', strike: 73000, option_type: 'PE', transaction_type: 'BUY',
    lots: 4, lot_size: 20, quantity: 80, entry_premium: 505.55, exit_premium: 514.65, pnl: 728,
    entry_time: today + ' 13:06:32', exit_time: today + ' 13:06:40', exit_reason: 'manual', ...extra,
  });
  let asked = '';
  await page.route('**/api/scalp/trades/*/match-broker', async (route) => {
    asked = new URL(route.request().url()).pathname;
    await route.fulfill({ json: { status: 'ok', trade: row(916, { mode: 'live', broker: 'zerodha', entry_premium: 510.6, exit_premium: 521.6, pnl: 880 }) } });
  });

  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);
  await page.evaluate((rows) => {
    const w = window as unknown as { _scalpAllClosed: unknown[]; _renderScalpHistoryPage: () => void; refreshScalpStatus: () => void };
    w.refreshScalpStatus = () => {};
    w._scalpAllClosed = rows;
    w._renderScalpHistoryPage();
  }, [
    row(916, { mode: 'live', broker: 'zerodha' }),
    row(915, { mode: 'live', broker: 'dhan' }),
    row(914, { mode: 'paper' }),
    row(913, { mode: 'live', broker: 'zerodha', entry_time: '2026-01-02 10:00:00' }),
  ]);

  // Only today's live Zerodha row offers it: Dhan, paper and old rows do not.
  const buttons = page.locator('#scalp-history-body .scalp-match-btn');
  await expect(buttons).toHaveCount(1);
  await expect(buttons.first()).toHaveText('⇄ Kite');

  await buttons.first().click();
  await page.click('#confirm-ok-btn');
  await expect.poll(() => asked).toBe('/api/scalp/trades/916/match-broker');
  await expect(page.locator('body')).toContainText('+₹880.00');
});

test('Live book: today\'s Zerodha trade can be matched with Kite, a Dhan one cannot', async ({ page }) => {
  // Phil, 2026-10-15: "I closed the Zerodha CE in Kite, correct the record".
  await login(page);
  const today = await page.evaluate(() => {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  });
  let sent: unknown = null;
  await page.route('**/api/live/trades/match-broker', async (route) => {
    sent = JSON.parse(route.request().postData() as string);
    await route.fulfill({ json: { status: 'ok', trade: { entry_premium: 276.3, exit_premium: 301.75, pnl: 3195.15 } } });
  });
  await page.evaluate((day) => {
    const w = window as unknown as { _portfolioCompletedTradeRowHtml: (t: unknown, i: number, a: unknown[], r: string) => string };
    const row = (extra: Record<string, unknown>) => ({ symbol: 'NIFTY 22800CE', entry_time: `${day} 13:10:00`, transaction_type: 'BUY', pnl: -2017.85, ...extra });
    const host = document.createElement('table');
    host.id = 'kite-match-probe';
    host.innerHTML = '<tbody>'
      + w._portfolioCompletedTradeRowHtml(row({ id: 1, broker: 'zerodha' }), 0, [], 'CE_SL15_NoMonTue_Zerodha')
      + w._portfolioCompletedTradeRowHtml(row({ id: 2, broker: 'dhan' }), 1, [], 'CE_SL15_NoMonTue')
      + w._portfolioCompletedTradeRowHtml(row({ id: 3, broker: 'zerodha', entry_time: '2026-01-02 10:00:00' }), 2, [], 'CE_SL15_NoMonTue_Zerodha')
      + '</tbody>';
    document.body.appendChild(host);
  }, today);
  const buttons = page.locator('#kite-match-probe .live-kite-match-btn');
  await expect(buttons).toHaveCount(1);
  await buttons.first().click();
  await page.click('#confirm-ok-btn');
  await expect.poll(() => sent).toEqual({ run_id: 'CE_SL15_NoMonTue_Zerodha', trade_id: '1' });
  await expect(page.locator('body')).toContainText('+₹3195.15');
});
