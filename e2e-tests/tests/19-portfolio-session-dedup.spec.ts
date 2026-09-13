import { test, expect, Page } from '@playwright/test';

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';

async function login(page: Page) {
  const response = await page.request.post('/api/auth/login', {
    data: { username: USERNAME, password: PIN },
  });
  expect(response.status()).toBe(200);
}

test('Portfolio shows one reconciled trade, retaining its engine explanation', async ({ page }) => {
  await login(page);
  await page.goto('/app#portfolio-page');

  const merged = await page.evaluate(() => {
    const engineTrade = {
      id: 7,
      symbol: 'NIFTY 23700PE 2026-09-15',
      option_type: 'PE',
      transaction_type: 'BUY',
      lots: 2,
      entry_time: '2026-09-10 09:25:09',
      exit_time: '2026-09-10 15:26:36',
      entry_premium: 265,
      exit_premium: 279.45,
      pnl: 1763.21,
      exit_reason: 'BROKER_MANUAL_EXIT',
    };
    const settledAccountCopy = {
      trading_symbol: 'NIFTY 10 SEP 23700 PUT',
      option_type: 'PE',
      transaction_type: 'BUY',
      quantity: 130,
      entry_time: '2026-09-10T09:25:00',
      exit_time: '2026-09-10T15:26:00',
      entry_premium: 265,
      exit_premium: 279.45,
      pnl: 1798.44,
      charges: 80.06,
      settled_by_broker: true,
      from_account: true,
      exit_reason: 'from the account',
    };
    return window._portfolioMergeReconciliationCopies([engineTrade, settledAccountCopy]);
  });

  expect(merged).toHaveLength(1);
  expect(merged[0]).toMatchObject({
    id: 7,
    lots: 2,
    pnl: 1798.44,
    charges: 80.06,
    exit_reason: 'BROKER_MANUAL_EXIT',
  });
});
