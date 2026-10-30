import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-30: "remove the pop-up while exiting a trade manually in Live.
// It creates delays not helping to exit at the right price immediately". One
// press sends the exit; a second press while it is in flight sends nothing.

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

test('Exit on a Live position goes at once, with no dialog, and only once', async ({ page }) => {
  await page.route('**/api/engines/all', route => route.fulfill({ json: { engines: [{
    run_id: 'live-1', strategy_name: 'CE_SL15_NoMonTue', mode: 'auto', running: true, in_trade: true, instrument: '26000',
    total_pnl: 0, positions: [{ symbol: 'NIFTY 22800 CE', transaction_type: 'BUY', entry_premium: 276.5, current_premium: 301.75, unrealized_pnl: 3282.5 }],
    closed_trades: [], event_log: [], strategy: { entry_conditions: [], exit_conditions: [], legs: [] },
  }] } }));
  const sent: string[] = [];
  let release: () => void = () => {};
  const answered = new Promise<void>(r => { release = r; });
  await page.route('**/api/live/exit-position', async (route) => {
    sent.push(route.request().postData() || '');
    await answered; // the broker is still working on it
    await route.fulfill({ json: { status: 'ok', message: 'Position exited' } });
  });
  await login(page);
  await page.evaluate(() => {
    const w = window as unknown as { showPage: (p: string, el?: Element | null) => void; startLiveMonitor?: () => void };
    w.showPage('live-page', document.getElementById('nav-live'));
    w.startLiveMonitor?.();
  });
  const exit = page.locator('#live-panels-container button', { hasText: 'Exit' }).first();
  await expect(exit).toBeVisible();

  await exit.click();
  await expect.poll(() => sent.length).toBe(1);
  expect(JSON.parse(sent[0])).toEqual({ run_id: 'live-1', position_index: 0 });
  await expect(page.locator('#confirm-modal')).not.toHaveClass(/open|active|show/);
  await expect(page.locator('#confirm-modal')).toBeHidden();
  // A second press while the first is in flight: nothing more is sent.
  await page.evaluate(() => (window as unknown as { _forceExitPosition: (a: string, r: string, i: number) => Promise<void> })
    ._forceExitPosition('/api/live/exit-position', 'live-1', 0));
  await page.waitForTimeout(300);
  expect(sent.length).toBe(1);
  release();
});
