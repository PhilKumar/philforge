import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-14: a Zerodha scalp's exit was refused and he sold by hand in
// Kite, so the row kept the site's prices (+728) instead of Kite's (+880). The
// Scalp row's match button is retired (2026-10-20: the engine books the fills);
// the live books' match on the Portfolio page stays.

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

test('Scalp history: no Kite match button -- the engine books Kite\'s fills itself', async ({ page }) => {
  // Phil, 2026-10-20: "Why we are having this kite button when scalp does
  // exactly what kite has". Since 14-Oct the engine books Zerodha's fills;
  // a hand close in Kite now reads them too, so the row needs no button.
  await login(page);
  const today = await page.evaluate(() => {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  });
  const row = (id: number, extra: Record<string, unknown>) => ({
    trade_id: id, underlying: 'SENSEX', strike: 73000, option_type: 'PE', transaction_type: 'BUY',
    lots: 4, lot_size: 20, quantity: 80, entry_premium: 510.6, exit_premium: 521.6, pnl: 880,
    entry_time: today + ' 13:06:32', exit_time: today + ' 13:06:40', exit_reason: 'broker_manual_exit', ...extra,
  });
  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);
  await page.evaluate((rows) => {
    const w = window as unknown as { _scalpAllClosed: unknown[]; _renderScalpHistoryPage: () => void; refreshScalpStatus: () => void };
    w.refreshScalpStatus = () => {};
    w._scalpAllClosed = rows;
    w._renderScalpHistoryPage();
  }, [
    row(916, { mode: 'live', broker: 'zerodha', broker_match: { matched_at: today + ' 13:10:00', was: {} } }),
    row(915, { mode: 'live', broker: 'zerodha' }),
    row(914, { mode: 'live', broker: 'dhan' }),
  ]);
  await expect(page.locator('#scalp-history-body tr')).toHaveCount(3);
  await expect(page.locator('#scalp-history-body .scalp-match-btn')).toHaveCount(0);
  await expect(page.locator('#scalp-history-body')).not.toContainText('Kite');
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
