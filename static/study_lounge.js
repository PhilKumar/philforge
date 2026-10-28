const state = {
  items: [],
  categories: [],
  selectedId: null,
  kindFilter: 'all',
  categoryFilter: 'all',
  search: '',
};

// One glyph per kind, drawn in the kind's colour: the library is read by its
// icons first and its titles second (Phil, 2026-10-28: "everything is in text
// and not easy to read and find").
const KIND_ICONS = {
  video: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13l11-6.5z"/></svg>',
  deck: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M12 16v4M8 20h8M7 12l3-3 2 2 5-4"/></svg>',
  audio: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/></svg>',
  image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/></svg>',
};
const KIND_LABELS = { video: 'Video', deck: 'Deck', audio: 'Audio', image: 'Board', file: 'File' };

function kindOf(item) {
  const kind = String(item?.kind || '').toLowerCase();
  return KIND_ICONS[kind] ? kind : 'file';
}

let destroyPreviewAudioMotion = () => {};

function cleanupPreviewAudioMotion() {
  destroyPreviewAudioMotion();
  destroyPreviewAudioMotion = () => {};
}

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function setThemeToggleIcon() {
  const btn = $('theme-toggle');
  if (!btn) return;
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  btn.innerHTML = isLight
    ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>'
    : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2"></path><path d="M12 20v2"></path><path d="m4.93 4.93 1.41 1.41"></path><path d="m17.66 17.66 1.41 1.41"></path><path d="M2 12h2"></path><path d="M20 12h2"></path><path d="m6.34 17.66-1.41 1.41"></path><path d="m19.07 4.93-1.41 1.41"></path></svg>';
}

function toggleTheme() {
  if (typeof window.pfToggleTheme === 'function') window.pfToggleTheme();
  else {
    const root = document.documentElement;
    const next = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    root.setAttribute('data-theme', next);
  }
  setThemeToggleIcon();
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function applyFeatured(item) {
  setText('featured-title', item?.title || 'No study assets yet');
  setText('featured-kind', item?.kind_label || 'Study Asset');
  setText('featured-description', item?.description || 'Drop videos, decks, and audio briefs into the study folders to populate this page.');
  setText('featured-category', item?.category || 'General');
  setText('featured-date', item?.modified_label || '--');
  setText('featured-size', item?.size_label || '--');
  const openBtn = $('open-item-btn');
  const downloadBtn = $('download-item-btn');
  if (openBtn) {
    openBtn.href = item?.url || '#';
    openBtn.toggleAttribute('aria-disabled', !item);
  }
  if (downloadBtn) {
    downloadBtn.href = item?.download_url || item?.url || '#';
    downloadBtn.toggleAttribute('aria-disabled', !item);
  }
}

function bindAudioOrbMotion(shell) {
  const audio = shell.querySelector('audio');
  const orb = shell.querySelector('.audio-orb');
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  if (!audio || !orb || !AudioContextCtor) return;

  let audioContext = null;
  let analyser = null;
  let source = null;
  let samples = null;
  let rafId = 0;
  let destroyed = false;
  let smoothedLevel = 0;

  const stopLoop = () => {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
  };

  const resetOrb = () => {
    smoothedLevel = 0;
    orb.style.setProperty('--orb-scale', '1');
    orb.style.setProperty('--orb-glow-scale', '1');
    orb.classList.remove('is-reactive');
  };

  const applyLevel = (level) => {
    smoothedLevel += (level - smoothedLevel) * 0.24;
    orb.style.setProperty('--orb-scale', (1 + smoothedLevel * 0.28).toFixed(3));
    orb.style.setProperty('--orb-glow-scale', (1 + smoothedLevel * 0.58).toFixed(3));
    orb.classList.toggle('is-reactive', smoothedLevel > 0.05 && !audio.paused && !audio.ended);
  };

  const ensureContext = async () => {
    if (audioContext) return audioContext;
    audioContext = new AudioContextCtor();
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.82;
    samples = new Uint8Array(analyser.fftSize);
    source = audioContext.createMediaElementSource(audio);
    source.connect(analyser);
    analyser.connect(audioContext.destination);
    return audioContext;
  };

  const tick = () => {
    if (destroyed) return;
    if (audio.paused || audio.ended) {
      stopLoop();
      resetOrb();
      return;
    }

    analyser.getByteTimeDomainData(samples);
    let sumSquares = 0;
    for (let i = 0; i < samples.length; i += 1) {
      const centered = (samples[i] - 128) / 128;
      sumSquares += centered * centered;
    }
    const rms = Math.sqrt(sumSquares / samples.length);
    const level = Math.min(1, Math.max(0, (rms - 0.015) * 8));
    applyLevel(level);
    rafId = requestAnimationFrame(tick);
  };

  const start = async () => {
    try {
      const context = await ensureContext();
      if (context.state === 'suspended') await context.resume();
      if (!rafId) tick();
    } catch (err) {
      console.warn('Audio orb visualizer unavailable', err);
    }
  };

  const stop = () => {
    stopLoop();
    resetOrb();
  };

  audio.addEventListener('play', start);
  audio.addEventListener('playing', start);
  audio.addEventListener('pause', stop);
  audio.addEventListener('ended', stop);
  audio.addEventListener('emptied', stop);

  if (!audio.paused) start();
  else resetOrb();

  destroyPreviewAudioMotion = () => {
    destroyed = true;
    stopLoop();
    audio.removeEventListener('play', start);
    audio.removeEventListener('playing', start);
    audio.removeEventListener('pause', stop);
    audio.removeEventListener('ended', stop);
    audio.removeEventListener('emptied', stop);
    resetOrb();
    try {
      source?.disconnect();
    } catch (err) {}
    try {
      analyser?.disconnect();
    } catch (err) {}
    if (audioContext && audioContext.state !== 'closed') {
      audioContext.close().catch(() => {});
    }
  };
}

function renderPreview(item) {
  setText('preview-title', item?.title || 'Select a deck, video, or audio brief');
  const kindPill = $('preview-kind');
  if (kindPill) {
    kindPill.textContent = item?.kind_label || 'Preview';
    kindPill.className = `type-pill ${item ? kindOf(item) : ''}`.trim();
  }
  const shell = $('preview-shell');
  cleanupPreviewAudioMotion();
  if (!item) {
    shell.innerHTML = '<div class="preview-empty">Your selected deck, video, or audio brief will open here.</div>';
    return;
  }
  if (item.kind === 'video') {
    shell.innerHTML = `<video controls playsinline preload="metadata" src="${escapeHtml(item.preview_url)}"></video>`;
    return;
  }
  if (item.kind === 'deck') {
    const pdfUrl = `${item.preview_url}#toolbar=0&navpanes=0&scrollbar=0&view=FitH`;
    shell.innerHTML = `<iframe src="${escapeHtml(pdfUrl)}" title="${escapeHtml(item.title)}"></iframe>`;
    return;
  }
  if (item.kind === 'image') {
    shell.innerHTML = `<img src="${escapeHtml(item.preview_url)}" alt="${escapeHtml(item.title)}">`;
    return;
  }
  if (item.kind === 'audio') {
    shell.innerHTML = `
      <div class="audio-stage">
        <div class="audio-orb"></div>
        <div class="audio-copy">
          <span class="eyebrow">Audio Brief</span>
          <h3>${escapeHtml(item.title)}</h3>
          <p>${escapeHtml(item.description)}</p>
        </div>
        <audio controls preload="metadata" src="${escapeHtml(item.preview_url)}"></audio>
      </div>
    `;
    bindAudioOrbMotion(shell);
    return;
  }
  shell.innerHTML = `<div class="preview-empty">This asset can be downloaded from the library card.</div>`;
}

function matchesSearch(item) {
  const needle = state.search.trim().toLowerCase();
  if (!needle) return true;
  return [item.title, item.category, item.description, item.filename, item.kind_label]
    .some((field) => String(field || '').toLowerCase().includes(needle));
}

function filteredItems() {
  return state.items.filter((item) => {
    if (state.kindFilter !== 'all' && kindOf(item) !== state.kindFilter) return false;
    if (state.categoryFilter !== 'all' && item.category !== state.categoryFilter) return false;
    return matchesSearch(item);
  });
}

// The tracks in the library's own order (Class 0..7, then the themes); a
// payload without a category list still gets its tracks from the items.
function trackNames() {
  const named = state.categories.map((category) => category.name).filter(Boolean);
  const seen = new Set(named);
  state.items.forEach((item) => {
    if (item.category && !seen.has(item.category)) {
      seen.add(item.category);
      named.push(item.category);
    }
  });
  return named;
}

function renderTypeFilters() {
  const container = $('type-filters');
  if (!container) return;
  const sourceItems = state.items.filter((item) =>
    (state.categoryFilter === 'all' || item.category === state.categoryFilter) && matchesSearch(item));
  const counts = sourceItems.reduce((map, item) => {
    map[kindOf(item)] = (map[kindOf(item)] || 0) + 1;
    return map;
  }, { all: sourceItems.length });
  const kinds = [['all', 'All'], ['video', 'Videos'], ['deck', 'Decks'], ['audio', 'Audio'], ['image', 'Boards']]
    .filter(([kind]) => kind === 'all' || counts[kind] || state.kindFilter === kind);
  container.innerHTML = kinds.map(([kind, label]) => `
    <button class="filter-chip kind-${kind} ${state.kindFilter === kind ? 'active' : ''}" type="button" data-kind="${kind}" aria-pressed="${state.kindFilter === kind}">
      ${kind === 'all' ? '' : `<span class="chip-icon">${KIND_ICONS[kind]}</span>`}${escapeHtml(label)} <span class="chip-count">${counts[kind] || 0}</span>
    </button>
  `).join('');
  container.querySelectorAll('[data-kind]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.kindFilter = btn.dataset.kind || 'all';
      renderLibrary();
    });
  });
}

function renderCategoryFilters() {
  const container = $('category-filters');
  if (!container) return;
  const sourceItems = state.items.filter((item) =>
    (state.kindFilter === 'all' || kindOf(item) === state.kindFilter) && matchesSearch(item));
  const counts = sourceItems.reduce((map, item) => map.set(item.category, (map.get(item.category) || 0) + 1), new Map());
  const options = [{ name: 'all', label: 'All tracks', count: sourceItems.length }]
    .concat(trackNames().filter((name) => counts.has(name)).map((name) => ({ name, label: name, count: counts.get(name) })));
  if (!options.some((option) => option.name === state.categoryFilter)) state.categoryFilter = 'all';

  container.innerHTML = options.map((option) => `
    <button class="track-chip ${option.name === state.categoryFilter ? 'active' : ''}" type="button" data-track="${escapeHtml(option.name)}" aria-pressed="${option.name === state.categoryFilter}">
      ${escapeHtml(option.label)} <span class="chip-count">${option.count}</span>
    </button>
  `).join('');
  container.querySelectorAll('[data-track]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.categoryFilter = btn.dataset.track || 'all';
      renderLibrary();
    });
  });
}

function renderTile(item) {
  const kind = kindOf(item);
  const facts = [KIND_LABELS[kind], item.size_label].filter(Boolean).join(' · ');
  return `
    <button type="button" class="study-tile kind-${kind} ${item.id === state.selectedId ? 'active' : ''}" data-id="${escapeHtml(item.id)}"
      title="${escapeHtml(item.description || item.title)}" aria-pressed="${item.id === state.selectedId}">
      <span class="study-tile-icon">${KIND_ICONS[kind]}</span>
      <span class="study-tile-body">
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(facts)}</small>
      </span>
    </button>
  `;
}

function renderLibrary() {
  renderCategoryFilters();
  renderTypeFilters();
  const items = filteredItems();
  const selected = items.find((item) => item.id === state.selectedId) || items[0] || null;
  state.selectedId = selected?.id || null;
  applyFeatured(selected || state.items[0] || null);
  renderPreview(selected);

  const grid = $('library-grid');
  if (!grid) return;
  if (!items.length) {
    grid.innerHTML = `<div class="library-empty">${state.search ? `Nothing matches "${escapeHtml(state.search)}".` : 'No study assets match the current filter.'}</div>`;
    return;
  }

  // Grouped by track, in the library's order, each group headed by its name.
  const groups = new Map(trackNames().map((name) => [name, []]));
  items.forEach((item) => {
    if (!groups.has(item.category)) groups.set(item.category, []);
    groups.get(item.category).push(item);
  });
  grid.innerHTML = Array.from(groups.entries())
    .filter(([, members]) => members.length)
    .map(([name, members]) => `
      <section class="study-group" style="--n:${Math.min(members.length, 3)}">
        <header class="study-group-head"><h3>${escapeHtml(name || 'General')}</h3><span>${members.length}</span></header>
        <div class="study-group-grid">${members.map(renderTile).join('')}</div>
      </section>
    `).join('');

  grid.querySelectorAll('.study-tile').forEach((card) => {
    card.addEventListener('click', () => {
      state.selectedId = card.dataset.id;
      renderLibrary();
      if (window.innerWidth <= 1100) {
        $('preview-shell')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });
}

function bindSearch() {
  const input = $('study-search');
  if (!input || input.dataset.bound) return;
  input.dataset.bound = '1';
  input.addEventListener('input', () => {
    state.search = input.value || '';
    renderLibrary();
  });
}

async function loadLibrary() {
  try {
    const res = await fetch(`/api/study-library?_=${Date.now()}`, { cache: 'no-store' });
    const data = await res.json();
    state.items = Array.isArray(data.items) ? data.items : [];
    state.categories = Array.isArray(data.categories) ? data.categories : [];
    state.selectedId = data.featured?.id || state.items[0]?.id || null;
    setText('library-status', state.items.length ? 'Library ready' : 'No study assets yet');
    setText('hero-total', `${data.stats?.total_items || state.items.length} assets`);
    setText('hero-categories', `${data.stats?.categories || trackNames().length} tracks`);
    const search = $('study-search');
    if (search) search.placeholder = `Search ${state.items.length} lessons`;
    renderLibrary();
  } catch (err) {
    console.error('Study library load failed', err);
    setText('library-status', 'Library unavailable');
    if ($('library-grid')) $('library-grid').innerHTML = '<div class="library-empty">Failed to load the study library.</div>';
    applyFeatured(null);
    renderPreview(null);
  }
}

// Same two homes as the heatmap. The theme toggle is only wired on the
// standalone page -- inside Insights the app shell owns it, and binding a
// second handler would flip the theme twice per click.
function startStudyLounge() {
  bindSearch();
  loadLibrary();
}
window.pfStartStudyLounge = startStudyLounge;
document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('insights-page')) return;
  $('theme-toggle')?.addEventListener('click', toggleTheme);
  setThemeToggleIcon();
  startStudyLounge();
});
