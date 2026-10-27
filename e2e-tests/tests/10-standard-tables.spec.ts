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
}

test('every site table uses the Campaigns ledger contract', async ({ page }) => {
  await login(page);

  const pageIds = [
    'dashboard-page',
    'builder-page',
    'results-page',
    'live-page',
    'portfolio-page',
    'stock-terminal-page',
    'options-cascade-page',
    'insights-page',
    'scalp-page',
    'charts-page',
  ];

  const tableClasses = [
    'admin-table',
    'ocp-table',
    'cascade-scan-table',
    'cj-plan-table',
    'dash-table',
    'portfolio-monthly-trades-table',
    'portfolio-ytd-table',
    'scalp-active-table',
    'tb-table',
    'terminal-cascade-ladder-table',
    'trade-table',
  ];

  await page.evaluate(({ ids, classes }) => {
    for (const id of ids) {
      const host = document.getElementById(id)!;
      const probe = document.createElement('div');
      probe.innerHTML = `<table data-table-probe="${id}"><thead><tr><th>Price</th></tr></thead><tbody><tr><td>1,234.50</td></tr><tr><td>1,235.50</td></tr></tbody></table>`;
      host.appendChild(probe);
    }

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = '<table data-table-probe="overlay"><thead><tr><th>Price</th></tr></thead><tbody><tr><td>1,234.50</td></tr><tr><td>1,235.50</td></tr></tbody></table>';
    document.body.appendChild(overlay);

    const classHost = document.getElementById('dashboard-page')!;
    for (const tableClass of classes) {
      const probe = document.createElement('div');
      probe.innerHTML = `<table class="${tableClass}" data-table-probe="class-${tableClass}"><thead><tr><th>Price</th></tr></thead><tbody><tr><td>1,234.50</td></tr><tr><td>1,235.50</td></tr></tbody></table>`;
      classHost.appendChild(probe);
    }
  }, { ids: pageIds, classes: tableClasses });

  const styles = await page.evaluate(({ ids, classes }) => {
    const read = (name: string) => {
      const table = document.querySelector(`[data-table-probe="${name}"]`)!;
      const th = getComputedStyle(table.querySelector('th')!);
      const td = getComputedStyle(table.querySelector('td')!);
      const evenTd = getComputedStyle(table.querySelector('tbody tr:nth-child(2) td') || table.querySelector('td')!);
      return {
        tableWidth: getComputedStyle(table).width,
        tableFontSize: getComputedStyle(table).fontSize,
        tableFontFamily: getComputedStyle(table).fontFamily,
        headerPadding: `${th.paddingTop} ${th.paddingRight} ${th.paddingBottom} ${th.paddingLeft}`,
        cellPadding: `${td.paddingTop} ${td.paddingRight} ${td.paddingBottom} ${td.paddingLeft}`,
        headerPosition: th.position,
        headerTop: th.top,
        headerFontSize: th.fontSize,
        headerFontWeight: th.fontWeight,
        headerTransform: th.textTransform,
        headerLetterSpacing: th.letterSpacing,
        headerOpacity: th.opacity,
        headerBackground: th.backgroundImage,
        cellFontSize: td.fontSize,
        cellFontFamily: td.fontFamily,
        cellLineHeight: td.lineHeight,
        cellBorder: td.borderBottomWidth,
        evenBackground: evenTd.backgroundColor,
        numericVariant: td.fontVariantNumeric,
      };
    };
    return Object.fromEntries([
      ...ids,
      'overlay',
      ...classes.map(tableClass => `class-${tableClass}`),
    ].map(id => [id, read(id)]));
  }, { ids: pageIds, classes: tableClasses });

  const activeMonoFamily = await page.evaluate(() =>
    getComputedStyle(document.documentElement)
      .getPropertyValue('--font-mono')
      .split(',')[0]
      .replace(/["']/g, '')
      .trim()
  );

  for (const name of [...pageIds, 'overlay']) {
    expect(styles[name].tableWidth).not.toBe('0px');
    expect(styles[name]).toMatchObject({
      tableFontSize: '13px',
      headerPadding: '10px 13px 10px 13px',
      cellPadding: '10px 13px 10px 13px',
      headerPosition: 'sticky',
      headerTop: '0px',
      headerFontSize: '10px',
      headerFontWeight: '800',
      headerTransform: 'uppercase',
      headerLetterSpacing: '0.8px',
      headerOpacity: '1',
      cellFontSize: '13px',
      cellLineHeight: '18.85px',
      cellBorder: '1px',
      evenBackground: 'rgba(255, 255, 255, 0.02)',
      numericVariant: 'tabular-nums',
    });
    expect(styles[name].tableFontFamily).toContain(activeMonoFamily);
    expect(styles[name].cellFontFamily).toContain(activeMonoFamily);
    expect(styles[name].headerBackground).toContain('rgba(79, 142, 247, 0.16)');
  }

  const baseline = { ...styles['dashboard-page'], tableWidth: undefined };
  for (const tableClass of tableClasses) {
    const actual = { ...styles[`class-${tableClass}`], tableWidth: undefined };
    expect(actual, `${tableClass} drifted from the Campaigns table contract`).toEqual(baseline);
  }

  const drift = await page.evaluate(() => [...document.querySelectorAll('table:not([data-table-probe])')]
    .map((table, index) => {
      const th = table.querySelector('th');
      const td = table.querySelector('td');
      if (!th || !td) return null;
      // Active Positions docked beside the Scalp chart lays each position on
      // two lines in a ~440px column (Phil, 2026-10-27); there the cells keep
      // 6px sides so Entry, LTP and P&L fit. The wide table keeps the contract.
      if (table.classList.contains('scalp-active-table') && getComputedStyle(table).display === 'block') return null;
      const head = getComputedStyle(th);
      const cell = getComputedStyle(td);
      const matches = getComputedStyle(table).fontSize === '13px'
        && `${head.paddingTop} ${head.paddingRight} ${head.paddingBottom} ${head.paddingLeft}` === '10px 13px 10px 13px'
        && `${cell.paddingTop} ${cell.paddingRight} ${cell.paddingBottom} ${cell.paddingLeft}` === '10px 13px 10px 13px'
        && head.position === 'sticky'
        && head.fontSize === '10px'
        && head.fontWeight === '800'
        && head.textTransform === 'uppercase'
        && cell.fontSize === '13px';
      return matches ? null : {
        table: `${index}:${table.id || table.className || 'plain'}`,
        tableFont: getComputedStyle(table).fontSize,
        headerPadding: `${head.paddingTop} ${head.paddingRight} ${head.paddingBottom} ${head.paddingLeft}`,
        cellPadding: `${cell.paddingTop} ${cell.paddingRight} ${cell.paddingBottom} ${cell.paddingLeft}`,
        headerPosition: head.position,
        headerFont: `${head.fontSize}/${head.fontWeight}/${head.textTransform}`,
        cellFont: cell.fontSize,
      };
    })
    .filter(Boolean));

  expect(drift).toEqual([]);

  const light = await page.evaluate(() => {
    document.documentElement.setAttribute('data-theme', 'light');
    const th = getComputedStyle(document.querySelector('[data-table-probe="scalp-page"] th')!);
    const td = getComputedStyle(document.querySelector('[data-table-probe="scalp-page"] td')!);
    return { headerColor: th.color, headerBackground: th.backgroundImage, cellBorder: td.borderBottomColor };
  });

  expect(light.headerColor).toBe('rgb(71, 85, 105)');
  expect(light.headerBackground).toContain('rgb(248, 250, 252)');
  expect(light.headerBackground).toContain('rgb(233, 239, 247)');
  expect(light.cellBorder).toBe('rgba(15, 23, 42, 0.08)');
});
