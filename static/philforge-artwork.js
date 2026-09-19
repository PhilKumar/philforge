/* Decorative presentation only; no order, data, or engine hooks. */
(() => {
  'use strict';
  function init() {
    const root = document.documentElement;
    if (!document.getElementById('main-content') || root.classList.contains('pf-glass')) return;
    root.classList.add('pf-glass');
    const pages = {
      'dashboard-page': 'dashboard', 'portfolio-page': 'portfolio',
      'options-cascade-page': 'trading', 'stock-terminal-page': 'equity',
      'scalp-page': 'scalp', 'insights-page': 'insights', 'live-page': 'live',
      'builder-page': 'builder', 'charts-page': 'journal',
      'results-page': 'results', 'assets-page': 'assets'
    };
    const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
      for (const entry of entries) entry.target.classList.toggle('pf-art-visible', entry.isIntersecting);
    }) : null;
    for (const [pageId, name] of Object.entries(pages)) {
      const page = document.getElementById(pageId);
      const hero = page?.querySelector(':scope > .pf-workspace-hero, :scope > .trading-workspace-head, :scope > .oc-hero');
      if (!hero) continue;
      hero.classList.add('pf-art-hero');
      const figure = document.createElement('figure');
      figure.className = 'pf-tab-art';
      figure.setAttribute('aria-hidden', 'true');
      figure.dataset.artwork = name;
      const img = document.createElement('img');
      img.src = `/static/artwork/${name}.png?v=20260919-artwork-3`;
      img.alt = ''; img.width = 1280; img.height = 1280;
      img.decoding = 'async'; img.loading = 'lazy';
      const orbit = document.createElement('span'); orbit.className = 'pf-art-orbit';
      const ball = document.createElement('span'); ball.className = 'pf-art-ball';
      figure.append(orbit, img, ball); hero.append(figure);
      if (observer) observer.observe(figure); else figure.classList.add('pf-art-visible');
    }
    const atmosphere = document.createElement('div');
    atmosphere.className = 'pf-glass-atmosphere'; atmosphere.setAttribute('aria-hidden', 'true');
    document.body.prepend(atmosphere);
    const motion = document.createElement('button'); motion.type = 'button'; motion.className = 'pf-motion-button';
    let paused = false;
    try { paused = localStorage.getItem('pf-art-motion') === 'off'; } catch (_) { /* Storage is optional. */ }
    const syncMotion = () => {
      root.dataset.artMotion = paused ? 'off' : 'on';
      motion.textContent = paused ? 'Motion off' : 'Motion on';
      motion.title = paused ? 'Resume artwork animation' : 'Pause artwork animation';
      motion.setAttribute('aria-label', motion.title);
      motion.setAttribute('aria-pressed', String(paused));
    };
    syncMotion();
    motion.addEventListener('click', () => {
      paused = !paused; syncMotion();
      try { localStorage.setItem('pf-art-motion', paused ? 'off' : 'on'); } catch (_) { /* Storage is optional. */ }
    });
    (document.querySelector('.topbar-actions') || document.querySelector('.topbar-right')).append(motion);
    // Unique outline glyphs in the existing icon slots; labels and handlers stay intact.
    const paths = {
      dashboard: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="5" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="12" width="7" height="9" rx="2"/>',
      portfolio: '<rect x="3" y="7" width="18" height="14" rx="3"/><path d="M8 7V4h8v3M3 12a20 20 0 0 0 18 0M10 14h4"/>',
      trading: '<path d="M7 2v4m0 10v6M17 2v7m0 9v4"/><rect x="4" y="6" width="6" height="10" rx="2"/><rect x="14" y="9" width="6" height="9" rx="2"/>',
      insights: '<circle cx="10" cy="10" r="7"/><path d="m15 15 6 6M6 12l3-4 3 2 2-3"/>',
      builder: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/><path d="M15 6h3a2 2 0 0 1 2 2v3M9 18H6a2 2 0 0 1-2-2v-3"/>',
      charts: '<path d="M12 5C8 2 4 3 2 4v15c4-2 7-1 10 1 3-2 6-3 10-1V4c-2-1-6-2-10 1ZM12 5v15M5 8l4 1m6 0 4-1"/>',
      results: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 17v-4m5 4V9m5 8V6"/>',
      assets: '<path d="m12 2 9 5v10l-9 5-9-5V7Zm0 10 9-5M3 7l9 5v10"/>'
    };
    for (const [name, markup] of Object.entries(paths)) {
      const slot = document.querySelector(`#nav-${name} .tab-icon`);
      if (!slot) continue;
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      for (const [key,value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.7','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'})) svg.setAttribute(key,value);
      svg.innerHTML = markup; slot.replaceChildren(svg);
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true}); else init();
})();
