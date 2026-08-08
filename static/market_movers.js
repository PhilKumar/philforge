const MARKET_MOVERS_REFRESH_MS = 30000;
const MARKET_MOVERS_FETCH_TIMEOUT_MS = 12000;
const MARKET_MOVERS_CACHE_KEY = 'philforge_market_movers_snapshot_v1';
let moversNextRefreshAt = 0;
let moversRefreshTimer = null;
let latestPayload = null;
let marketMoversLoading = false;

const INDUSTRY_PALETTE = {
  'Financial Services': { rgb: '79, 142, 247' },
  'Information Technology': { rgb: '167, 139, 250' },
  Healthcare: { rgb: '52, 211, 153' },
  'Automobile and Auto Components': { rgb: '251, 191, 36' },
  'Oil Gas & Consumable Fuels': { rgb: '251, 146, 60' },
  'Fast Moving Consumer Goods': { rgb: '34, 197, 94' },
  'Metals & Mining': { rgb: '244, 114, 182' },
  'Capital Goods': { rgb: '6, 182, 212' },
  Power: { rgb: '14, 165, 233' },
  'Consumer Durables': { rgb: '99, 102, 241' },
  'Consumer Services': { rgb: '236, 72, 153' },
  Construction: { rgb: '248, 113, 113' },
  'Construction Materials': { rgb: '249, 115, 22' },
  Telecommunication: { rgb: '168, 85, 247' },
  Services: { rgb: '56, 189, 248' },
  Other: { rgb: '148, 163, 184' },
};

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

function industryAccent(industry) {
  return INDUSTRY_PALETTE[industry] || INDUSTRY_PALETTE.Other;
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

function tileSize(weight) {
  const value = Number(weight || 1);
  if (value >= 2.3) return 'tile-xl';
  return 'tile-lg';
}

function tileTone(item) {
  const accent = industryAccent(item.industry).rgb;
  if (item.unavailable) {
    return {
      moveRgb: '107, 114, 128',
      accentRgb: accent,
      alpha: '0.10',
      accentAlpha: '0.12',
      state: 'is-unavailable',
    };
  }
  const pct = Number(item.change_pct || 0);
  const intensity = Math.min(Math.abs(pct) / 4.5, 1);
  if (pct > 0) {
    return {
      moveRgb: '49, 212, 191',
      accentRgb: accent,
      alpha: (0.14 + intensity * 0.20).toFixed(3),
      accentAlpha: '0.18',
      state: 'is-positive',
    };
  }
  if (pct < 0) {
    return {
      moveRgb: '255, 123, 130',
      accentRgb: accent,
      alpha: (0.14 + intensity * 0.20).toFixed(3),
      accentAlpha: '0.18',
      state: 'is-negative',
    };
  }
  return {
    moveRgb: accent,
    accentRgb: accent,
    alpha: '0.11',
    accentAlpha: '0.20',
    state: 'is-flat',
  };
}

function renderHero(cardId, item, priceId, absId, volumeId) {
  const card = document.getElementById(cardId);
  if (!card) return;
  const symbolEl = card.querySelector('.hero-symbol');
  const companyEl = card.querySelector('.hero-company');
  const changeEl = card.querySelector('.hero-change');
  const priceEl = document.getElementById(priceId);
  const absEl = document.getElementById(absId);
  const volumeEl = document.getElementById(volumeId);

  if (!item) {
    symbolEl.textContent = '--';
    companyEl.textContent = 'Awaiting quote';
    changeEl.textContent = '0.00%';
    priceEl.textContent = '--';
    absEl.textContent = '--';
    volumeEl.textContent = '--';
    return;
  }

  symbolEl.textContent = item.symbol || '--';
  companyEl.textContent = item.name || item.industry || 'Awaiting quote';
  changeEl.textContent = formatSignedPercent(item.change_pct);
  changeEl.className = `hero-change ${toneClass(item.change_pct)}`;
  priceEl.textContent = formatCurrency(item.price || 0);
  absEl.textContent = formatSignedCurrency(item.change || 0);
  volumeEl.textContent = formatVolume(item.volume || 0);
}

function renderRailList(containerId, items, emptyMessage) {
  const host = document.getElementById(containerId);
  if (!host) return;
  if (!items.length) {
    host.innerHTML = `<div class="heatmap-empty">${emptyMessage}</div>`;
    return;
  }
  host.innerHTML = items
    .map(
      (item, index) => {
        const accent = industryAccent(item.industry);
        return `
        <div class="rail-row" style="--rail-accent-rgb:${accent.rgb};">
          <span class="rail-rank">${index + 1}</span>
          <div class="rail-main">
            <span class="rail-symbol">${item.symbol}</span>
            <span class="rail-company">${item.name}</span>
          </div>
          <span class="rail-move ${toneClass(item.change_pct)}">${formatSignedPercent(item.change_pct)}</span>
        </div>
      `;
      }
    )
    .join('');
}

function renderIndustryList(items) {
  const host = document.getElementById('industry-list');
  if (!host) return;
  if (!items.length) {
    host.innerHTML = `<div class="heatmap-empty">Industry drift will appear once quotes arrive.</div>`;
    return;
  }
  host.innerHTML = items
    .slice(0, 6)
    .map((item) => {
      const accent = industryAccent(item.industry);
      return `
        <div class="industry-row" style="--rail-accent-rgb:${accent.rgb};">
          <div class="industry-main">
            <span class="industry-name">${item.industry}</span>
            <span class="industry-meta">${item.count} stocks • ${formatVolume(item.volume)} volume</span>
          </div>
          <span class="industry-move ${toneClass(item.change_pct)}">${formatSignedPercent(item.change_pct)}</span>
        </div>
      `;
    })
    .join('');
}

function renderHeatmapSkeleton() {
  const host = document.getElementById('heatmap-grid');
  if (!host) return;
  host.innerHTML = Array.from({ length: 12 }, (_, idx) => {
    const cls = idx < 2 ? 'tile-xl' : idx < 8 ? 'tile-lg' : 'tile-sm';
    return `<article class="heatmap-tile ${cls} heatmap-skeleton" aria-hidden="true"></article>`;
  }).join('');
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

function renderHeatmap(items) {
  const host = document.getElementById('heatmap-grid');
  if (!host) return;
  if (!items.length) {
    host.innerHTML = `<div class="heatmap-empty">The Nifty 50 mosaic is waiting for its first snapshot.</div>`;
    return;
  }

  const ranked = [...items].sort((a, b) => {
    const weightDelta = Number(b.weight || 0) - Number(a.weight || 0);
    if (Math.abs(weightDelta) > 0.0001) return weightDelta;
    return String(a.symbol || '').localeCompare(String(b.symbol || ''));
  });

  host.innerHTML = ranked
    .map((item) => {
      const tone = tileTone(item);
      return `
        <article class="heatmap-tile ${tileSize(item.weight)} ${tone.state}" style="--tile-move-rgb:${tone.moveRgb}; --tile-accent-rgb:${tone.accentRgb}; --tile-alpha:${tone.alpha}; --tile-accent-alpha:${tone.accentAlpha};">
          <div class="tile-head">
            <div>
              <div class="tile-symbol">${item.symbol}</div>
              <div class="tile-industry">${item.industry || 'Industry'}</div>
            </div>
            <span class="tile-change ${toneClass(item.change_pct)}">${item.unavailable ? 'No feed' : formatSignedPercent(item.change_pct)}</span>
          </div>
          <div class="tile-body">
            <div class="tile-price">${item.unavailable ? 'Awaiting quote' : formatCurrency(item.price || 0)}</div>
            <div class="tile-foot">
              <span class="tile-company">${item.name || ''}</span>
              <span class="tile-volume">${formatVolume(item.volume || 0)}</span>
            </div>
          </div>
        </article>
      `;
    })
    .join('');
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

  document.getElementById('breadth-advancers').textContent = advancers;
  document.getElementById('breadth-decliners').textContent = decliners;
  const medianEl = document.getElementById('breadth-median');
  medianEl.textContent = formatSignedPercent(median);
  medianEl.className = `pulse-value ${toneClass(median)}`;
  document.getElementById('breadth-bar-advancers').style.width = `${(advancers / total) * 100}%`;
  document.getElementById('breadth-bar-flat').style.width = `${(flat / total) * 100}%`;
  document.getElementById('breadth-bar-decliners').style.width = `${(decliners / total) * 100}%`;

  const strongest = industryMoves[0];
  const weakest = industryMoves[industryMoves.length - 1];
  document.getElementById('market-strongest-industry').textContent = strongest ? `${strongest.industry} ${formatSignedPercent(strongest.change_pct)}` : '--';
  document.getElementById('market-weakest-industry').textContent = weakest ? `${weakest.industry} ${formatSignedPercent(weakest.change_pct)}` : '--';
}

function renderSnapshot(payload) {
  latestPayload = payload;
  saveSnapshot(payload);
  const items = Array.isArray(payload.items) ? payload.items : [];
  const leaders = Array.isArray(payload.leaders) ? payload.leaders : [];
  const laggards = Array.isArray(payload.laggards) ? payload.laggards : [];
  const industryMoves = computeIndustryMoves(items);

  document.getElementById('market-source').textContent = sourceLabel(payload);
  const sourceDetail = document.getElementById('market-source-detail');
  if (sourceDetail) sourceDetail.textContent = sourceLabel(payload);
  document.getElementById('market-status').textContent = marketStatusLabel(payload);
  document.getElementById('market-as-of').textContent = formatAsOf(payload.as_of);
  document.getElementById('page-message').textContent = payload.message || 'Nifty 50 live breadth is synced to a standalone feed.';

  updateBreadth(payload, industryMoves);
  renderHero('top-gainer-card', leaders[0], 'top-gainer-price', 'top-gainer-abs', 'top-gainer-volume');
  renderHero('top-laggard-card', laggards[0], 'top-laggard-price', 'top-laggard-abs', 'top-laggard-volume');
  renderHeatmap(items);
  renderRailList('leaders-list', leaders, 'Positive movers will appear once data is available.');
  renderRailList('laggards-list', laggards, 'Negative movers will appear once data is available.');
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
    document.getElementById('market-status').textContent = 'Feed unavailable';
    document.getElementById('market-source').textContent = 'Feed unavailable';
    const sourceDetail = document.getElementById('market-source-detail');
    if (sourceDetail) sourceDetail.textContent = 'Feed unavailable';
    document.getElementById('page-message').textContent = latestPayload
      ? 'Unable to refresh right now. Showing the latest available snapshot.'
      : 'Unable to fetch market movers right now.';
    if (!latestPayload) renderLoadingSkeleton();
  } finally {
    window.clearTimeout(timeoutId);
    marketMoversLoading = false;
    moversNextRefreshAt = Date.now() + MARKET_MOVERS_REFRESH_MS;
    updateCountdown();
  }
}

function startMarketMovers() {
  applyMarketTheme(document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
  document.getElementById('theme-toggle')?.addEventListener('click', toggleMarketTheme);
  const cached = loadCachedSnapshot();
  if (cached) {
    cached.stale = true;
    renderSnapshot(cached);
    document.getElementById('market-status').textContent = 'Refreshing snapshot...';
    document.getElementById('market-source').textContent = 'Cached snapshot';
    const sourceDetail = document.getElementById('market-source-detail');
    if (sourceDetail) sourceDetail.textContent = 'Cached snapshot';
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
