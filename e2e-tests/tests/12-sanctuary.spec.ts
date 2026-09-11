import { test, expect } from '@playwright/test';

// Opt in only on a disposable local database. This suite creates personal records.
test.skip(process.env.E2E_SANCTUARY_DISPOSABLE !== '1', 'Requires disposable Sanctuary database');

test('Sanctuary lock, journal, finance charts and responsive themes', async ({ page, baseURL }) => {
  test.setTimeout(90000);
  expect(['localhost', '127.0.0.1']).toContain(new URL(baseURL!).hostname);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== new URL(baseURL!).origin || url.pathname.endsWith('.mp4')) {
      await route.abort();
    } else {
      await route.continue();
    }
  });
  await page.route('**/api/sanctuary/daily', route => route.fulfill({ json: {
    verse: { text: 'Isolated test verse', reference: 'Test', fallback: true },
    quote: { text: 'Isolated test quote', author: 'Test' },
  } }));
  const login = await page.request.post('/api/auth/login', {
    data: { username: 'admin', password: process.env.E2E_PIN || '123456' },
  });
  expect(login.ok()).toBeTruthy();
  const status = await (await page.request.get('/api/sanctuary/status')).json();
  // Public dummy fixture for the opt-in disposable localhost database, not a real credential.
  const password = 'Disposable-sanctuary-test-123'; // gitleaks:allow
  const grant = await page.request.post(`/api/sanctuary/${status.setup ? 'unlock' : 'setup'}`, {
    data: { password },
  });
  expect(grant.ok()).toBeTruthy();
  await page.goto('/sanctuary');
  await expect(page.locator('#home')).toBeVisible();
  const title = `Audit journal ${Date.now()}`;
  await page.locator('#c-title').fill(title);
  await page.locator('#c-body').fill('Disposable browser audit record.');
  const saved = page.waitForResponse(r => r.url().endsWith('/api/sanctuary/journal') && r.request().method() === 'POST');
  await page.locator('#c-save').click();
  expect((await saved).ok()).toBeTruthy();
  await expect(page.locator('#c-title')).toHaveValue('');
  await expect(page.getByText(title, { exact: true }).first()).toBeVisible();
  await page.locator('#tab-finance').click();
  await expect(page.locator('#view-finance')).toBeVisible();
  await page.locator('#quick-cats .qc').first().click();
  await page.locator('#q-amt').fill('123');
  await page.locator('#q-note').fill(title);
  const spent = page.waitForResponse(r => r.url().endsWith('/api/sanctuary/finance/entry') && r.request().method() === 'POST');
  await page.locator('#q-add').click();
  expect((await spent).ok()).toBeTruthy();
  await expect(page.locator('#q-amt')).toHaveValue('');
  await expect(page.locator('#trend')).toBeVisible();
  await expect(page.locator('#trend').locator('path, rect, line').first()).toBeAttached();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const theme of ['light', 'dark']) {
      if (await page.locator('html').getAttribute('data-theme') !== theme) await page.locator('#theme-btn').click();
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
    }
  }
  await page.locator('#lock-btn').click();
  await expect(page.locator('#gate')).toBeVisible();
  const locked = await page.request.get('/api/sanctuary/finance');
  expect(locked.status()).toBe(423);
  await page.locator('#unlock-pass').fill(password);
  await page.locator('#unlock-btn').click();
  await expect(page.locator('#home')).toBeVisible();
  expect(errors).toEqual([]);
});
