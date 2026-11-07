import { test, expect, Page } from '@playwright/test';

// Phil, 2026-11-07: "put this as a separate page for practice in scalp".
// Practice is its own desk tab (#trading/practice): with no day running it
// shows the day picker and none of the desk; the live Scalp tab never shows
// the practice line. The replay itself needs the day archive, which CI does
// not have, so the server's answers are mocked here.

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';

async function login(page: Page) {
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const pw = page.locator('#password-input');
  if (await pw.isVisible()) { await pw.fill(PIN); await page.click('#unlock-btn'); }
  else { for (const d of PIN.split('')) await page.click(`[data-val="${d}"]`); }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
  await page.waitForFunction(() => typeof (window as unknown as { showPage?: unknown }).showPage === 'function');
}

test('Practice is its own tab: the day picker, none of the live desk, and Scalp stays live', async ({ page }) => {
  await page.route('**/api/practice/state', (route) => route.fulfill({ json: { status: 'none' } }));
  await page.route('**/api/practice/days', (route) => route.fulfill({
    json: { status: 'ok', days: { NIFTY: ['2026-01-30', '2026-02-01'], SENSEX: ['2026-02-01'] } },
  }));
  await login(page);

  await page.locator('[data-pf-trading-page="scalp-page"]').first().evaluate((el) => (el as HTMLElement).click());
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);
  await expect(page).toHaveURL(/#trading\/scalp$/);
  await expect(page.locator('#scalp-practice')).toBeHidden();

  await page.locator('#scalp-page [data-pf-trading-page="scalp-practice"]').click();
  await expect(page).toHaveURL(/#trading\/practice$/);
  await expect(page.locator('#scalp-page [data-pf-trading-page="scalp-practice"]')).toHaveClass(/is-active/);
  await expect(page.locator('#scalp-page .ocp-title-row h3')).toHaveText('Scalp Practice');
  await expect(page.locator('#scalp-practice-setup')).toBeVisible();
  await expect(page.locator('#scalp-practice-msg')).toContainText('2 real NIFTY days');
  // No day running: no positions, no launchpad, no Start Engine.
  await expect(page.locator('#scalp-page .scalp-positions-card')).toBeHidden();
  await expect(page.locator('#scalp-start-btn')).toBeHidden();

  // A reload stays on Practice.
  await page.reload();
  await page.waitForFunction(() => typeof (window as unknown as { showPage?: unknown }).showPage === 'function');
  await expect(page.locator('#scalp-practice-setup')).toBeVisible();

  // Back to Scalp: live again.
  await page.locator('#scalp-page [data-pf-trading-page="scalp-page"]').click();
  await expect(page).toHaveURL(/#trading\/scalp$/);
  await expect(page.locator('#scalp-page .ocp-title-row h3')).toHaveText('Scalp Engine');
  await expect(page.locator('#scalp-practice')).toBeHidden();
  await expect(page.locator('#scalp-page .scalp-positions-card')).toBeVisible();
});
