import { test, expect, Page } from '@playwright/test';

// Phil, 2026-10-06: the nav bar and a page banner took half of a laptop
// window. On a desktop window every banner is one compact row, and once the
// page scrolls the pinned banner folds to a strip -- without moving the page.

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';
const PAGES = [
  'dashboard-page', 'portfolio-page', 'options-cascade-page', 'scalp-page', 'option-builder-page',
  'stock-terminal-page', 'insights-page', 'live-page', 'builder-page', 'charts-page', 'results-page', 'assets-page',
];

async function login(page: Page) {
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const pw = page.locator('#password-input');
  if (await pw.isVisible()) { await pw.fill(PIN); await page.click('#unlock-btn'); }
  else { for (const d of PIN.split('')) await page.click(`[data-val="${d}"]`); }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
  // The app then restores the page that was open last; a click before that
  // lands would be undone by it.
  await page.waitForFunction(() => !!(history.state && (history.state as { page?: string }).page));
}

async function open(page: Page, id: string) {
  await page.waitForFunction(() => typeof (window as unknown as { showPage?: unknown }).showPage === 'function');
  await page.evaluate((pageId) => {
    window.scrollTo(0, 0);
    (window as unknown as { showPage: (p: string) => void }).showPage(pageId);
  }, id);
  await expect(page.locator(`#${id}`)).toHaveClass(/active-page/);
  await expect(page.locator(`#${id} > .pf-art-hero`)).toBeVisible();
}

function banner(page: Page) {
  return page.evaluate(() => {
    const hero = document.querySelector('.page-section.active-page > .pf-art-hero') as HTMLElement;
    let next = hero.nextElementSibling;
    while (next && !next.getClientRects().length) next = next.nextElementSibling;
    return {
      height: hero.getBoundingClientRect().height,
      folded: hero.classList.contains('pf-hero-folded'),
      nextTop: next ? next.getBoundingClientRect().top : NaN,
    };
  });
}

test('Every banner is one compact row on a laptop window, nothing cut', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 820 });
  await login(page);
  for (const id of PAGES) {
    await open(page, id);
    const { height } = await banner(page);
    expect(height, id).toBeLessThanOrEqual(150);
  }
  // The four desk tabs keep their whole subtitle.
  await open(page, 'option-builder-page');
  const cut = await page.locator('#option-builder-page .trading-section-tab small').evaluateAll(
    (els) => els.filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent),
  );
  expect(cut).toEqual([]);
});

test('Scrolled, the pinned banner folds to a strip without moving the page', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 820 });
  await login(page);
  await page.evaluate(() => document.fonts.ready);
  for (const id of ['stock-terminal-page', 'portfolio-page', 'assets-page']) {
    await open(page, id);
    const top = await banner(page);
    expect(top.folded, id).toBe(false);

    await expect.poll(async () => {
      await page.evaluate(() => window.scrollTo(0, 300));
      return (await banner(page)).folded;
    }, { message: id }).toBe(true);
    expect((await banner(page)).height, id).toBeLessThanOrEqual(72);
    // The page under it does not move: at this same instant and scroll, the
    // content sits where it would with the banner unfolded.
    const moved = await page.evaluate(() => {
      const hero = document.querySelector('.page-section.active-page > .pf-art-hero') as HTMLElement;
      let next = hero.nextElementSibling;
      while (next && !next.getClientRects().length) next = next.nextElementSibling;
      const folded = next!.getBoundingClientRect().top;
      const margin = hero.style.getPropertyValue('margin-bottom');
      hero.classList.remove('pf-hero-folded'); hero.style.removeProperty('margin-bottom');
      const unfolded = next!.getBoundingClientRect().top;
      hero.classList.add('pf-hero-folded'); hero.style.setProperty('margin-bottom', margin, 'important');
      return Math.abs(folded - unfolded);
    });
    expect(moved, id).toBeLessThanOrEqual(1);
    // The title and the controls stay; the description goes.
    await expect(page.locator(`#${id} > .pf-art-hero :is(h1,h2)`)).toBeVisible();
    await expect(page.locator(`#${id} > .pf-art-hero p`).first()).toBeHidden();

    await page.evaluate(() => window.scrollTo(0, 0));
    await expect.poll(async () => (await banner(page)).folded, { message: id }).toBe(false);
  }
  // The Trading strip keeps all four desks.
  await open(page, 'stock-terminal-page');
  await expect.poll(async () => {
    await page.evaluate(() => window.scrollTo(0, 300));
    return page.locator('#stock-terminal-page > .pf-hero-folded .trading-section-tab').count();
  }).toBe(4);
  await expect(page.locator('#stock-terminal-page > .pf-hero-folded .trading-section-tab').nth(2)).toBeVisible();
});

test('At the exact edge the banner settles: it never folds and unfolds on every frame', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 820 });
  await login(page);
  for (const id of ['stock-terminal-page', 'portfolio-page']) {
    await open(page, id);
    const flips = await page.evaluate(async () => {
      const hero = document.querySelector('.page-section.active-page > .pf-art-hero') as HTMLElement;
      const frames = (n: number) => new Promise<void>((done) => {
        const step = () => (n-- > 0 ? requestAnimationFrame(step) : done());
        step();
      });
      const foldedAt = async (y: number) => { window.scrollTo(0, y); await frames(2); return hero.classList.contains('pf-hero-folded'); };
      let edge = 0;
      for (let y = 8; y < 400 && !edge; y += 8) if (await foldedAt(y)) edge = y;
      while (edge > 1 && await foldedAt(edge - 1)) edge -= 1;
      let changes = 0;
      new MutationObserver((list) => { changes += list.length; }).observe(hero, { attributes: true, attributeFilter: ['class'] });
      const counts: number[] = [];
      for (let y = edge - 3; y <= edge + 3; y++) {
        window.scrollTo(0, y);
        await frames(3);
        changes = 0;
        await frames(12);
        counts.push(changes);
      }
      return { edge, counts };
    });
    expect(flips.edge, id).toBeGreaterThan(0);
    expect(flips.counts, id).toEqual([0, 0, 0, 0, 0, 0, 0]);
  }
});

test('On a phone the banner keeps its stacked layout and never folds', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await open(page, 'portfolio-page');
  await page.evaluate(() => window.scrollTo(0, 400));
  await page.waitForTimeout(200);
  expect((await banner(page)).folded).toBe(false);
  await expect(page.locator('#portfolio-page > .pf-art-hero')).toHaveCSS('flex-direction', 'column');
});
