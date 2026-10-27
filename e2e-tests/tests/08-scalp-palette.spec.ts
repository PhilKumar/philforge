import { test, expect, Page } from '@playwright/test';

const USERNAME = process.env.E2E_USERNAME || 'admin';
const PIN = process.env.E2E_PIN || '123456';

async function login(page: Page) {
  await page.goto('/app');
  await page.fill('#username-input', USERNAME);
  const pw = page.locator('#password-input');
  if (await pw.isVisible()) { await pw.fill(PIN); await page.click('#unlock-btn'); }
  else { for (const d of PIN.split('')) await page.click(`[data-val="${d}"]`); }
  await page.waitForSelector('.nav-tab', { timeout: 15_000 });
  // The nav is static markup, so it appears while the app script is still
  // parsing; the light-skin test calls showPage and must wait for it.
  await page.waitForFunction(() => typeof (window as unknown as { showPage?: unknown }).showPage === 'function');
}

test('Scalp follows the selected skin and reserves green for trading meaning', async ({ page }) => {
  await login(page);
  await page.waitForFunction(() => typeof (window as any)._renderScalpStatus === 'function');
  await page.evaluate(() => {
    // Magenta makes any stale fixed blue/purple workspace override obvious.
    (window as any).pfApplyAppearance({ tint: 'magenta' }, { persist: false });
    (window as any)._renderScalpStatus({
      running: false,
      session_pnl: 0,
      open_trades: [],
      closed_trades: [],
      file_trades: [],
      event_log: [
        { time: '09:20', type: 'entry', message: 'Order accepted' },
        { time: '09:35', type: 'exit', message: 'Target reached' },
        { time: '09:40', type: 'info', message: 'Feed refreshed' },
      ],
    });
  });

  const palette = await page.evaluate(() => {
    const color = (selector: string) => getComputedStyle(document.querySelector(selector)!).color;
    const eventColors = Array.from(document.querySelectorAll('#scalp-event-log > div span:last-child'))
      .map(el => getComputedStyle(el).color);
    return {
      title: color('#scalp-form-title'),
      quote: color('#scalp-live-ltp'),
      call: color('#scalp-option-toggle [data-tone="bull"]'),
      start: color('#scalp-start-btn'),
      buy: color('#scalp-page [onclick="submitScalpEntry(\'BUY\')"]'),
      eventColors,
    };
  });

  expect(palette.title).toBe('rgb(192, 132, 252)');
  expect(palette.quote).toBe('rgb(232, 121, 249)');
  expect(palette.call).toBe('rgb(232, 121, 249)');
  expect(palette.start).toBe('rgb(232, 121, 249)');
  expect(palette.eventColors).toEqual([
    'rgb(232, 121, 249)',
    'rgb(251, 191, 36)',
    'rgb(192, 132, 252)',
  ]);
  // BUY remains green because direction, targets and positive P&L are exactly
  // where the Cascade palette uses semantic green.
  expect(palette.buy).toBe('rgb(52, 211, 153)');

  const lightPalette = await page.evaluate(() => {
    document.documentElement.setAttribute('data-theme', 'light');
    const color = (selector: string) => getComputedStyle(document.querySelector(selector)!).color;
    return {
      title: color('#scalp-form-title'),
      quote: color('#scalp-live-ltp'),
      call: color('#scalp-option-toggle [data-tone="bull"]'),
      eventColors: Array.from(document.querySelectorAll('#scalp-event-log > div span:last-child'))
        .map(el => getComputedStyle(el).color),
    };
  });
  expect(lightPalette).toEqual({
    title: 'rgb(126, 34, 206)',
    quote: 'rgb(162, 28, 175)',
    call: 'rgb(162, 28, 175)',
    eventColors: [
      'rgb(162, 28, 175)',
      'rgb(146, 64, 14)',
      'rgb(126, 34, 206)',
    ],
  });
});

test('Scalp panels size to content and Advanced preserves every execution field', async ({ page }) => {
  await login(page);
  await page.evaluate(() => {
    (window as any).showPage('scalp-page', document.getElementById('nav-scalp'));
  });
  await expect(page.locator('#scalp-page')).toHaveClass(/active-page/);

  const advanced = page.locator('#scalp-page .scalp-advanced');
  await expect(advanced).not.toHaveAttribute('open', '');
  await expect(advanced.locator('summary')).toContainText('Advanced');
  await expect(advanced.locator('summary')).toContainText('Exit rules + stop-limit entry');

  // THE DESK IS BUILT LIKE THE FOUR STRATEGY CONSOLES NOW (2026-08-26): one
  // card, a head, the form beside the live monitor in `.ocp-panes`, then the
  // log and the archive as their own sections. The old `.scalp-desk-grid`
  // paired the form with the event log; the log is a flow-down section below.
  // POSITIONS FIRST, then the form beside the log. The table holds the fields
  // that get edited in a hurry, so it leads the desk at full width rather than
  // sitting a scroll below the launchpad (Phil, 2026-08-27).
  const geometry = await page.evaluate(() => {
    const panes = document.querySelector('#scalp-page .ocp-panes')!;
    const positionsEl = document.querySelector('#scalp-page .scalp-positions-card')!;
    const entryEl = document.querySelector('#scalp-page .scalp-entry-card')!;
    const log = document.querySelector('#scalp-page .scalp-event-card')!;
    const entry = entryEl.getBoundingClientRect();
    const logBox = log.getBoundingClientRect();
    const advancedBox = document.querySelector('#scalp-page .scalp-advanced')!.getBoundingClientRect();
    // Since 2026-10-27 the desk card itself is the grid (the panes' wrapper
    // steps aside), so the chart can run from the top beside the positions.
    const desk = panes.parentElement!;
    const pos = positionsEl.getBoundingClientRect();
    return {
      columns: getComputedStyle(desk).gridTemplateColumns,
      alignItems: getComputedStyle(entryEl).alignSelf,
      positionsOverTheForm: Math.abs(pos.left - entry.left) < 2 && pos.bottom <= entry.top,
      positionsLeadTheDesk: !!(positionsEl.compareDocumentPosition(panes) & Node.DOCUMENT_POSITION_FOLLOWING)
        && !positionsEl.closest('.ocp-panes'),
      // Since 2026-10-27 the log stands under the form, in the right column,
      // and the chart runs down the whole left column.
      logUnderTheForm: Math.abs(logBox.left - entry.left) < 2 && logBox.top >= entry.bottom,
      logIsItsOwnSection: log.tagName === 'DETAILS',
      logInLiveColumn: !!log.closest('.ocp-pane-live'),
      advancedHeight: advancedBox.height,
    };
  });
  expect(geometry.columns.split(' ')).toHaveLength(2);
  expect(geometry.alignItems).toBe('start');
  expect(geometry.positionsLeadTheDesk).toBe(true);
  expect(geometry.positionsOverTheForm).toBe(true);
  expect(geometry.logUnderTheForm).toBe(true);
  expect(geometry.logIsItsOwnSection).toBe(true);
  expect(geometry.logInLiveColumn).toBe(true);
  expect(geometry.advancedHeight).toBeLessThan(50);

  await advanced.locator('summary').click();
  await expect(advanced).toHaveAttribute('open', '');
  // Exit rules show one way at a time (2026-10-19); each pair is a click away.
  for (const [mode, ids] of [
    ['points', ['scalp-sl-pts', 'scalp-target-pts']],
    ['premium', ['scalp-sl-prem', 'scalp-target-prem']],
    ['rupees', ['scalp-sl-rs', 'scalp-target-rs']],
  ] as const) {
    await page.click(`#scalp-exit-mode-toggle [data-value="${mode}"]`);
    for (const id of ids) await expect(page.locator(`#${id}`)).toBeVisible();
  }
  await page.click('#scalp-exit-mode-toggle [data-value="points"]');
  for (const id of ['scalp-limit-price', 'scalp-limit-max']) await expect(page.locator(`#${id}`)).toBeVisible();
  await expect(page.locator('[onclick="submitScalpEntry(\'BUY\')"]')).toHaveCount(1);
  await expect(page.locator('[onclick="submitScalpEntry(\'SELL\')"]')).toHaveCount(1);
});

/**
 * The desk's panels used to paint themselves: `#scalp-page .scalp-*-card` set
 * a dark gradient, and an id beats `[data-theme="light"] .ocp-monitor`, so the
 * light skin arrived with dark boxes on it (Phil, 2026-08-27: "Scalp light
 * mode not completed properly"). They carry `.ocp-monitor` now and are painted
 * once, for both themes -- this measures that they actually invert.
 */
test('scalp panels follow the light skin', async ({ page }) => {
  await login(page);
  await page.evaluate(() => (window as any).showPage('scalp-page', document.getElementById('nav-scalp')));

  const read = () => page.evaluate(() => {
    const rgb = (s: string) => (s.match(/\d+/g) || []).slice(0, 3).map(Number);
    const lum = (c: number[]) => (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255;
    const out: Record<string, number> = {};
    for (const cls of ['scalp-entry-card', 'scalp-positions-card', 'scalp-event-card', 'scalp-history-card']) {
      const el = document.querySelector(`#scalp-page .${cls}`) as HTMLElement;
      const bg = getComputedStyle(el).backgroundImage + ' ' + getComputedStyle(el).backgroundColor;
      const first = bg.match(/rgba?\([^)]+\)/);
      out[cls] = first ? lum(rgb(first[0])) : -1;
    }
    return out;
  });

  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
  const dark = await read();
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
  const light = await read();
  for (const cls of Object.keys(light)) {
    expect(light[cls], `${cls} must lighten in the light skin`).toBeGreaterThan(dark[cls] + 0.2);
  }
});
