const MARKET_MOVERS_REFRESH_MS = 30000;
const MARKET_MOVERS_FETCH_TIMEOUT_MS = 12000;
const MARKET_MOVERS_CACHE_KEY = 'philforge_market_movers_snapshot_v1';
let moversNextRefreshAt = 0;
let moversRefreshTimer = null;
let latestPayload = null;
let marketMoversLoading = false;

function moversSunIcon() {
  return `
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4"></circle>
      <path d="M12 2v2"></path>
      <path d="M12 20v2"></path>
      <path d="m4.93 4.93 1.41 1.41"></path>
      <path d="m17.66 17.66 1.41 1.41"></path>
      <path d="M2 12h2"></path>
      <path d="M20 12h2"></path>
      <path d="m6.34 17.66-1.41 1.41"></path>
      <path d="m19.07 4.93-1.41 1.41"></path>
    </svg>
  `;
}

function moversMoonIcon() {
  return `
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"></path>
    </svg>
  `;
}

function applyMarketTheme(theme) {
  if (typeof window.pfApplyTheme === 'function') window.pfApplyTheme(theme);
  else document.documentElement.setAttribute('data-theme', theme);
  const toggle = document.getElementById('theme-toggle');
  if (toggle) toggle.innerHTML = theme === 'light' ? moversMoonIcon() : moversSunIcon();
}

function toggleMarketTheme() {
  const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  applyMarketTheme(next);
}

function formatCurrency(value) {
  const amount = Number(value || 0);
  return `₹${amount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatSignedCurrency(value) {
  const amount = Number(value || 0);
  const sign = amount > 0 ? '+' : amount < 0 ? '-' : '';
  return `${sign}${formatCurrency(Math.abs(amount))}`;
}

function formatSignedPercent(value) {
  const amount = Number(value || 0);
  const sign = amount > 0 ? '+' : amount < 0 ? '' : '';
  return `${sign}${amount.toFixed(2)}%`;
}

function toneClass(value) {
  const amount = Number(value || 0);
  if (amount > 0) return 'positive';
  if (amount < 0) return 'negative';
  return 'neutral';
}

function formatVolume(value) {
  const amount = Math.max(0, Number(value || 0));
  if (amount >= 1e7) return `${(amount / 1e7).toFixed(amount >= 5e7 ? 0 : 1)}Cr`;
  if (amount >= 1e5) return `${(amount / 1e5).toFixed(amount >= 5e5 ? 0 : 1)}L`;
  if (amount >= 1e3) return `${(amount / 1e3).toFixed(amount >= 5e3 ? 0 : 1)}K`;
  return amount.toLocaleString('en-IN');
}

function formatAsOf(value) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'medium',
    timeZone: 'Asia/Kolkata',
  }).format(date);
}

function sourceLabel(payload) {
  if (!payload) return 'Waiting for feed';
  if (payload.stale) return 'Cached Snapshot';
  if (payload.source === 'dhan_quote') return 'Dhan Live + Daily Close';
  if (payload.source === 'yfinance_fallback') return 'Fallback Feed';
  return 'Market Snapshot';
}

function marketStatusLabel(payload) {
  if (!payload) return 'Loading live breadth...';
  if (payload.status !== 'ok') return 'Feed unavailable';
  if (payload.stale) return 'Serving cached snapshot';
  return 'Live Nifty 50 breadth';
}

function computeMedian(items) {
  const values = items
    .filter((item) => !item.unavailable)
    .map((item) => Number(item.change_pct || 0))
    .sort((a, b) => a - b);
  if (!values.length) return 0;
  const middle = Math.floor(values.length / 2);
  return values.length % 2 ? values[middle] : (values[middle - 1] + values[middle]) / 2;
}

function computeIndustryMoves(items) {
  const grouped = new Map();
  items.filter((item) => !item.unavailable).forEach((item) => {
    const industry = item.industry || 'Other';
    const bucket = grouped.get(industry) || { industry, total: 0, count: 0, volume: 0 };
    bucket.total += Number(item.change_pct || 0);
    bucket.count += 1;
    bucket.volume += Number(item.volume || 0);
    grouped.set(industry, bucket);
  });
  return Array.from(grouped.values())
    .map((bucket) => ({
      ...bucket,
      change_pct: bucket.count ? bucket.total / bucket.count : 0,
    }))
    .sort((a, b) => b.change_pct - a.change_pct);
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

// The map's colour: four steps each way by the size of the move, grey inside
// ±0.25% (Phil, 2026-10-28: "everything is in text and not easy to read").
function heatClass(item) {
  if (item.unavailable) return 'hm-na';
  const pct = Number(item.change_pct || 0);
  const size = Math.abs(pct);
  const level = size < 0.25 ? 0 : size < 1 ? 1 : size < 2 ? 2 : 3;
  if (!level) return 'hm-0';
  return `${pct > 0 ? 'hm-p' : 'hm-n'}${level}`;
}

// Index weight sets the tile: the heaviest names take a 2x2 block, the next
// band a double-wide one, the rest a single cell.
function tileSpan(weight) {
  const value = Number(weight || 1);
  if (value >= 2.3) return 'hm-xl';
  if (value >= 1.6) return 'hm-lg';
  return '';
}

function tileTitle(item) {
  const lines = [`${item.symbol} · ${item.name || ''}`, item.industry || ''];
  if (item.unavailable) lines.push('No quote yet');
  else lines.push(`${formatCurrency(item.price || 0)}  ${formatSignedCurrency(item.change || 0)} (${formatSignedPercent(item.change_pct)})`, `Volume ${formatVolume(item.volume || 0)}`);
  return lines.filter(Boolean).join('\n');
}

function escapeAttr(value) {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function renderHero(cardId, item, priceId, absId, volumeId) {
  const card = document.getElementById(cardId);
  if (!card) return;
  const symbolEl = card.querySelector('.hero-symbol');
  const companyEl = card.querySelector('.hero-company');
  const changeEl = card.querySelector('.hero-change');
  if (symbolEl) symbolEl.textContent = item?.symbol || '--';
  if (companyEl) companyEl.textContent = item ? (item.name || item.industry || '') : 'Awaiting quote';
  if (changeEl) {
    changeEl.textContent = item ? formatSignedPercent(item.change_pct) : '0.00%';
    changeEl.className = `hero-change ${toneClass(item?.change_pct)}`;
  }
  card.title = item ? tileTitle(item) : '';
  setText(priceId, item ? formatCurrency(item.price || 0) : '--');
  setText(absId, item ? formatSignedCurrency(item.change || 0) : '--');
  setText(volumeId, item ? formatVolume(item.volume || 0) : '--');
}

// Each mover as a bar against the biggest move in its list.
function renderRailList(containerId, items, emptyMessage) {
  const host = document.getElementById(containerId);
  if (!host) return;
  if (!items.length) {
    host.innerHTML = `<div class="heatmap-empty">${emptyMessage}</div>`;
    return;
  }
  const top = Math.max(...items.map((item) => Math.abs(Number(item.change_pct || 0))), 0.01);
  host.innerHTML = items
    .map((item) => {
      const pct = Number(item.change_pct || 0);
      return `
        <div class="rail-row" title="${escapeAttr(tileTitle(item))}">
          <span class="rail-symbol">${escapeAttr(item.symbol)}</span>
          <span class="rail-bar"><i class="${toneClass(pct)}" style="width:${((Math.abs(pct) / top) * 100).toFixed(1)}%"></i></span>
          <span class="rail-move ${toneClass(pct)}">${formatSignedPercent(pct)}</span>
        </div>
      `;
    })
    .join('');
}

// Sectors as bars either side of zero, best at the top.
function renderIndustryList(items) {
  const host = document.getElementById('industry-list');
  if (!host) return;
  if (!items.length) {
    host.innerHTML = `<div class="heatmap-empty">Sectors appear once quotes arrive.</div>`;
    return;
  }
  const top = Math.max(...items.map((item) => Math.abs(item.change_pct)), 0.01);
  host.innerHTML = items
    .map((item) => {
      const pct = Number(item.change_pct || 0);
      const width = ((Math.abs(pct) / top) * 50).toFixed(1);
      return `
        <div class="industry-row" title="${escapeAttr(`${item.industry} · ${item.count} stocks · ${formatVolume(item.volume)} volume`)}">
          <span class="industry-name">${escapeAttr(item.industry)}</span>
          <span class="industry-bar"><i class="${toneClass(pct)}" style="width:${width}%;${pct >= 0 ? 'left:50%' : `right:50%`}"></i></span>
          <span class="industry-move ${toneClass(pct)}">${formatSignedPercent(pct)}</span>
        </div>
      `;
    })
    .join('');
}

function renderHeatmapSkeleton() {
  const host = document.getElementById('heatmap-grid');
  if (!host) return;
  host.innerHTML = `<div class="hm-map">${Array.from({ length: 6 }, () => `
    <section class="hm-sector heatmap-skeleton" style="--w:6" aria-hidden="true"></section>`).join('')}</div>`;
}

function renderRailSkeleton(containerId, count = 6) {
  const host = document.getElementById(containerId);
  if (!host) return;
  host.innerHTML = Array.from({ length: count }, () => `
    <div class="rail-row skeleton-row" aria-hidden="true"></div>
  `).join('');
}

function renderIndustrySkeleton(count = 4) {
  const host = document.getElementById('industry-list');
  if (!host) return;
  host.innerHTML = Array.from({ length: count }, () => `
    <div class="industry-row skeleton-row" aria-hidden="true"></div>
  `).join('');
}

function renderLoadingSkeleton() {
  renderHeatmapSkeleton();
  renderRailSkeleton('leaders-list');
  renderRailSkeleton('laggards-list');
  renderIndustrySkeleton();
}

// Sectors heaviest first, each a block as wide as its weight; inside, the
// stocks heaviest first. A tile shows its symbol and move; the rest is its
// tooltip.
function renderHeatmap(items) {
  const host = document.getElementById('heatmap-grid');
  if (!host) return;
  if (!items.length) {
    host.innerHTML = `<div class="heatmap-empty">The Nifty 50 map is waiting for its first snapshot.</div>`;
    return;
  }
  const sectors = new Map();
  items.forEach((item) => {
    const name = item.industry || 'Other';
    const sector = sectors.get(name) || { name, weight: 0, total: 0, live: 0, items: [] };
    sector.weight += Number(item.weight || 1);
    if (!item.unavailable) {
      sector.total += Number(item.change_pct || 0);
      sector.live += 1;
    }
    sector.items.push(item);
    sectors.set(name, sector);
  });
  const byWeight = (a, b) => (Number(b.weight || 0) - Number(a.weight || 0)) || String(a.symbol || '').localeCompare(String(b.symbol || ''));
  host.innerHTML = `<div class="hm-map">${Array.from(sectors.values())
    .sort((a, b) => b.weight - a.weight || a.name.localeCompare(b.name))
    .map((sector) => {
      const avg = sector.live ? sector.total / sector.live : null;
      return `
        <section class="hm-sector" style="--w:${sector.weight.toFixed(2)}">
          <header class="hm-sector-head" title="${escapeAttr(`${sector.name} · ${sector.items.length} stocks`)}">
            <span>${escapeAttr(sector.name)}</span>
            ${avg === null ? '' : `<strong class="${toneClass(avg)}">${formatSignedPercent(avg)}</strong>`}
          </header>
          <div class="hm-tiles">${sector.items.sort(byWeight).map((item) => `
            <div class="hm-tile ${heatClass(item)} ${tileSpan(item.weight)}" title="${escapeAttr(tileTitle(item))}">
              <span class="hm-sym">${escapeAttr(item.symbol)}</span>
              <span class="hm-pct">${item.unavailable ? '—' : formatSignedPercent(item.change_pct)}</span>
            </div>`).join('')}
          </div>
        </section>
      `;
    })
    .join('')}</div>`;
}

function saveSnapshot(payload) {
  if (!payload || !Array.isArray(payload.items) || !payload.items.length) return;
  try {
    localStorage.setItem(MARKET_MOVERS_CACHE_KEY, JSON.stringify(payload));
  } catch (e) {}
}

function loadCachedSnapshot() {
  try {
    const raw = localStorage.getItem(MARKET_MOVERS_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && Array.isArray(parsed.items) && parsed.items.length ? parsed : null;
  } catch (e) {
    return null;
  }
}

function updateBreadth(payload, industryMoves) {
  const items = Array.isArray(payload.items) ? payload.items : [];
  const breadth = payload.breadth || {};
  const advancers = Number(breadth.advancers || 0);
  const decliners = Number(breadth.decliners || 0);
  const flat = Number(breadth.flat || 0);
  const total = Math.max(1, advancers + decliners + flat);
  const median = computeMedian(items);

  setText('breadth-advancers', advancers);
  setText('breadth-decliners', decliners);
  const medianEl = document.getElementById('breadth-median');
  if (medianEl) {
    medianEl.textContent = formatSignedPercent(median);
    medianEl.className = `pulse-value ${toneClass(median)}`;
  }
  const widths = { 'breadth-bar-advancers': advancers, 'breadth-bar-flat': flat, 'breadth-bar-decliners': decliners };
  Object.entries(widths).forEach(([id, count]) => {
    const el = document.getElementById(id);
    if (el) el.style.width = `${(count / total) * 100}%`;
  });

  const strongest = industryMoves[0];
  const weakest = industryMoves[industryMoves.length - 1];
  setText('market-strongest-industry', strongest ? `${strongest.industry} ${formatSignedPercent(strongest.change_pct)}` : '--');
  setText('market-weakest-industry', weakest ? `${weakest.industry} ${formatSignedPercent(weakest.change_pct)}` : '--');
}

// The feed's own words show only when something is wrong; a good snapshot
// needs no sentence.
function setPageMessage(text, show) {
  const el = document.getElementById('page-message');
  if (!el) return;
  el.textContent = text || '';
  el.hidden = !show;
}

function renderSnapshot(payload) {
  latestPayload = payload;
  saveSnapshot(payload);
  const items = Array.isArray(payload.items) ? payload.items : [];
  const leaders = Array.isArray(payload.leaders) ? payload.leaders : [];
  const laggards = Array.isArray(payload.laggards) ? payload.laggards : [];
  const industryMoves = computeIndustryMoves(items);

  setText('market-source', sourceLabel(payload));
  setText('market-source-detail', sourceLabel(payload));
  setText('market-status', marketStatusLabel(payload));
  setText('market-as-of', formatAsOf(payload.as_of));
  const troubled = payload.status !== 'ok' || !!payload.stale;
  setPageMessage(payload.message || 'Nifty 50 live breadth is synced to a standalone feed.', troubled);

  updateBreadth(payload, industryMoves);
  renderHero('top-gainer-card', leaders[0], 'top-gainer-price', 'top-gainer-abs', 'top-gainer-volume');
  renderHero('top-laggard-card', laggards[0], 'top-laggard-price', 'top-laggard-abs', 'top-laggard-volume');
  renderHeatmap(items);
  renderRailList('leaders-list', leaders, 'Gainers appear once data is available.');
  renderRailList('laggards-list', laggards, 'Losers appear once data is available.');
  renderIndustryList(industryMoves);
}

function updateCountdown() {
  const age = document.getElementById('market-age');
  if (!age) return;
  age.textContent = latestPayload?.stale ? 'Cached snapshot' : 'Auto-refresh active';
}

async function loadMarketMovers() {
  if (marketMoversLoading) return;
  marketMoversLoading = true;
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), MARKET_MOVERS_FETCH_TIMEOUT_MS);
  try {
    const response = await fetch('/api/market-movers/nifty50', {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (response.status === 401) {
      window.location.href = '/app';
      return;
    }
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    const payload = await response.json();
    renderSnapshot(payload);
  } catch (error) {
    setText('market-status', 'Feed unavailable');
    setText('market-source', 'Feed unavailable');
    setText('market-source-detail', 'Feed unavailable');
    setPageMessage(latestPayload
      ? 'Unable to refresh right now. Showing the latest available snapshot.'
      : 'Unable to fetch market movers right now.', true);
    if (!latestPayload) renderLoadingSkeleton();
  } finally {
    window.clearTimeout(timeoutId);
    marketMoversLoading = false;
    moversNextRefreshAt = Date.now() + MARKET_MOVERS_REFRESH_MS;
    updateCountdown();
  }
}

function startMarketMovers() {
  // Insights calls this every time its Heatmap tab is shown. Once running, a
  // re-show only refreshes: a second start stacked a second 30s poll.
  if (moversRefreshTimer) {
    loadMarketMovers();
    return;
  }
  applyMarketTheme(document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
  document.getElementById('theme-toggle')?.addEventListener('click', toggleMarketTheme);
  const cached = loadCachedSnapshot();
  if (cached) {
    cached.stale = true;
    renderSnapshot(cached);
    setText('market-status', 'Refreshing snapshot...');
    setText('market-source', 'Cached snapshot');
    setText('market-source-detail', 'Cached snapshot');
  } else {
    renderLoadingSkeleton();
  }
  loadMarketMovers();
  moversRefreshTimer = window.setInterval(() => {
    if (document.visibilityState !== 'visible') return;
    loadMarketMovers();
  }, MARKET_MOVERS_REFRESH_MS);
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  // Inside the Insights tab this must not poll a panel nobody is looking at.
  if (document.getElementById('insights-heatmap')?.style.display === 'none') return;
  loadMarketMovers();
});

// Two homes now: its own page, and the Heatmap tab inside Insights. On the
// standalone page it starts itself; in the tab it waits to be asked, so the
// live-price poll does not run behind a panel that is not on screen.
window.pfStartMarketMovers = startMarketMovers;
document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('insights-page')) return;
  startMarketMovers();
});
