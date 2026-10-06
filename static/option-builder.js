/* Option Builder -- a multi-leg option strategy desk on the live Dhan chain.
 *
 * Phil, 2026-10-06: "a page like sensibull, options trader page in dhan --
 * don't do an exact replica but according to our philforge style ... every data
 * ... pictorial representation very fluid and clear ... data synced".
 *
 * Data:  /api/option-builder/* (chain, expiries, margin, positions, paper,
 *        execute). Dhan allows one chain request per 3s for the account, so
 *        the server caches; this page asks every 3s in session, every 60s out.
 * Math:  static/option-builder-math.js (PFOptionMath), tested under node.
 * Chart: a canvas drawn here; every change TWEENS from the last frame, so
 *        dragging a slider or swapping a leg moves the curve, never jumps it.
 */
(function () {
  'use strict';
  const M = window.PFOptionMath;
  if (!M) return;

  const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX'];
  const VIEWS = [['bullish', 'Bullish'], ['bearish', 'Bearish'], ['neutral', 'Neutral'], ['volatile', 'Volatile']];
  const STATE_KEY = 'philforge_option_builder_v1';
  const CHAIN_WINDOW = 14; // strikes either side of ATM shown in the chain
  const POLL_SESSION_MS = 3000;
  const POLL_CLOSED_MS = 60000;
  const OTHER_EXPIRY_MS = 9000;

  const S = {
    underlying: 'NIFTY',
    expiries: [],
    expiry: '',
    chains: {},          // expiry -> {data, at}
    legs: [],
    view: 'bullish',
    leftTab: 'readymade',
    anaTab: 'payoff',
    targetAt: 0,         // the target date's clock time (ms); 0 = now
    ivShift: 0,
    zoom: 1,
    showOI: true,
    showSigma: true,
    product: 'INTRADAY',
    broker: 'dhan',
    margin: null,
    marginNote: '',
    paper: [],
    portfolios: [],
    saved: [],
    pfView: 'portfolios', // or 'saved'
    openPortfolio: null,
    portfolioId: null,   // where 'Add to portfolio' sends the strategy
    naming: null,        // {kind: 'new' | 'rename' | 'save', id?, value}
    positions: null,
    positionNotes: [],
    error: '',
    sessionOpen: false,
    active: false,
    timer: null,
    nextId: 1,
    built: false,
  };

  // ── small helpers ────────────────────────────────────────────────────────
  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d = 0) => { const n = Number(v); return Number.isFinite(n) ? n : d; };
  function inr(v, digits = 0) {
    if (v === Infinity) return 'Unlimited';
    if (v === -Infinity) return 'Unlimited';
    if (!Number.isFinite(v)) return '—';
    const sign = v < 0 ? '-' : '';
    return `${sign}₹${Math.abs(v).toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  }
  function signedInr(v, digits = 0) {
    if (!Number.isFinite(v)) return inr(v, digits);
    return (v > 0 ? '+' : '') + inr(v, digits);
  }
  function compactInr(v) {
    const a = Math.abs(v), sign = v < 0 ? '-' : '';
    if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(a >= 1e8 ? 0 : 1)}Cr`;
    if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(a >= 1e6 ? 0 : 1)}L`;
    if (a >= 1e3) return `${sign}₹${(a / 1e3).toFixed(a >= 1e4 ? 0 : 1)}k`;
    return `${sign}₹${Math.round(a)}`;
  }
  const price = (v) => Number.isFinite(v) ? v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : '—';
  const strikeTxt = (v) => Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  const tone = (v) => (v > 0 ? 'is-pos' : v < 0 ? 'is-neg' : '');
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  function dte(expiry) {
    const ms = M.expiryCloseMs(expiry) - Date.now();
    if (ms <= 0) return 'expired';
    const d = Math.floor(ms / 864e5), h = Math.floor((ms % 864e5) / 36e5);
    return d > 0 ? `${d}d ${h}h` : `${h}h ${Math.floor((ms % 36e5) / 6e4)}m`;
  }
  function shortDate(expiry) {
    const d = new Date(`${expiry}T00:00:00`);
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
  }
  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts });
    let body = {};
    try { body = await res.json(); } catch (_) { body = {}; }
    if (!res.ok || body.status === 'error') {
      const msg = body.message || body.detail || `Request failed (${res.status})`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return body;
  }
  function notify(msg, kind) { if (typeof window.toast === 'function') window.toast(msg, kind || ''); }

  // ── persistence (UI choices and the legs being built; never orders) ─────
  function save() {
    try {
      localStorage.setItem(STATE_KEY, JSON.stringify({
        underlying: S.underlying, expiry: S.expiry, view: S.view, leftTab: S.leftTab, anaTab: S.anaTab,
        product: S.product, broker: S.broker, showOI: S.showOI, showSigma: S.showSigma,
        legs: S.legs.map(({ side, type, strike, expiry, lots, entry, enabled, source }) => ({ side, type, strike, expiry, lots, entry, enabled, source })),
      }));
    } catch (_) { /* private mode: the page still works */ }
  }
  function restore() {
    try {
      const raw = JSON.parse(localStorage.getItem(STATE_KEY) || 'null');
      if (!raw || typeof raw !== 'object') return;
      if (UNDERLYINGS.includes(raw.underlying)) S.underlying = raw.underlying;
      S.expiry = String(raw.expiry || '');
      for (const k of ['view', 'leftTab', 'anaTab', 'product', 'broker']) if (raw[k]) S[k] = raw[k];
      S.showOI = raw.showOI !== false; S.showSigma = raw.showSigma !== false;
      const today = new Date().toISOString().slice(0, 10);
      S.legs = (Array.isArray(raw.legs) ? raw.legs : [])
        .filter((l) => l && l.expiry >= today && ['BUY', 'SELL'].includes(l.side) && ['CE', 'PE'].includes(l.type))
        .map((l) => newLeg(l));
    } catch (_) { /* ignore a corrupt entry */ }
  }

  // ── chain + legs ─────────────────────────────────────────────────────────
  function chainFor(expiry) { return (S.chains[expiry] || {}).data || null; }
  function primary() { return chainFor(S.expiry); }
  function lotSize() { return num((primary() || {}).lot_size) || num((Object.values(S.chains)[0] || {}).data?.lot_size); }
  function quote(expiry, strike, type) {
    const chain = chainFor(expiry);
    if (!chain) return null;
    const row = chain.rows.find((r) => Math.abs(r.strike - strike) < 1e-6);
    return row ? row[type.toLowerCase()] : null;
  }
  function newLeg(o) {
    return {
      id: S.nextId++, side: o.side, type: o.type, strike: num(o.strike), expiry: o.expiry,
      lots: Math.max(1, Math.round(num(o.lots, 1))), entry: num(o.entry), enabled: o.enabled !== false,
      source: o.source || 'builder', ltp: 0, iv: 0, bid: 0, ask: 0, greeks: null, securityId: '',
    };
  }
  // Fill every leg's live numbers from its chain; a new leg's entry is its LTP.
  function syncLegs() {
    for (const leg of S.legs) {
      const q = quote(leg.expiry, leg.strike, leg.type);
      if (!q) continue;
      leg.ltp = q.ltp; leg.iv = q.iv; leg.bid = q.bid; leg.ask = q.ask; leg.securityId = q.security_id;
      leg.greeks = { delta: q.delta, gamma: q.gamma, theta: q.theta, vega: q.vega };
      if (!(leg.entry > 0) && q.ltp > 0) leg.entry = q.ltp;
    }
  }
  function ctx() {
    const chain = primary() || {};
    return { spot: num(chain.spot), nowMs: Date.now(), lotSize: lotSize(), atmIv: num(chain.atm_iv) || 15, fallbackIv: num(chain.atm_iv) || 15, ivShift: S.ivShift };
  }
  function strikesFor(expiry) {
    const chain = chainFor(expiry) || primary();
    return chain ? chain.rows.map((r) => r.strike) : [];
  }
  function addLeg(side, type, strike, expiry = S.expiry, lots = 1) {
    S.legs.push(newLeg({ side, type, strike, expiry, lots }));
    changed({ margin: true });
  }

  // ── fetching ─────────────────────────────────────────────────────────────
  async function loadExpiries() {
    const body = await api(`/api/option-builder/expiries?underlying=${encodeURIComponent(S.underlying)}`);
    S.expiries = body.expiries || [];
    if (!S.expiries.includes(S.expiry)) S.expiry = S.expiries[0] || '';
  }
  async function loadChain(expiry) {
    const body = await api(`/api/option-builder/chain?underlying=${encodeURIComponent(S.underlying)}&expiry=${encodeURIComponent(expiry)}`);
    S.chains[expiry] = { data: body, at: Date.now() };
    S.sessionOpen = !!body.session_open;
    return body;
  }
  async function refresh(force) {
    try {
      if (!S.expiries.length || force === 'expiries') await loadExpiries();
      if (!S.expiry) throw new Error('Dhan lists no open expiries for this index.');
      await loadChain(S.expiry);
      const others = [...new Set(S.legs.map((l) => l.expiry))].filter((e) => e !== S.expiry);
      for (const e of others) {
        const hit = S.chains[e];
        if (!hit || Date.now() - hit.at > (S.sessionOpen ? OTHER_EXPIRY_MS : POLL_CLOSED_MS)) await loadChain(e);
      }
      S.error = '';
      syncLegs();
    } catch (err) {
      S.error = err.message || String(err);
    }
    render();
  }
  function schedule() {
    clearTimeout(S.timer);
    if (!S.active) return;
    const wait = document.hidden ? 4000 : (S.sessionOpen ? POLL_SESSION_MS : POLL_CLOSED_MS);
    S.timer = setTimeout(async () => {
      if (S.active && !document.hidden) await refresh();
      schedule();
    }, wait);
  }

  let marginTimer = null;
  function queueMargin() {
    clearTimeout(marginTimer);
    marginTimer = setTimeout(fetchMargin, 650);
  }
  async function fetchMargin() {
    const legs = M.activeLegs(S.legs).filter((l) => l.source !== 'position');
    if (!legs.length) { S.margin = null; S.marginNote = ''; renderStats(); return; }
    S.marginNote = 'Asking Dhan…'; renderStats();
    try {
      S.margin = await api('/api/option-builder/margin', { method: 'POST', body: JSON.stringify({ legs: payloadLegs(legs), product: S.product }) });
      S.marginNote = '';
    } catch (err) {
      S.margin = null; S.marginNote = err.message;
    }
    renderStats();
  }
  function payloadLegs(legs) {
    return legs.map((l) => ({ underlying: S.underlying, strike: l.strike, expiry: l.expiry, option_type: l.type, side: l.side, lots: l.lots, security_id: l.securityId, price: l.ltp || l.entry }));
  }

  // ── rendering: the frame once, then each region on its own ──────────────
  function buildShell(root) {
    root.innerHTML = `
      <div class="ob-topbar card">
        <div class="ob-und" role="tablist" aria-label="Underlying">${UNDERLYINGS.map((u) => `<button type="button" class="ob-chip" data-ob-und="${u}">${u}</button>`).join('')}</div>
        <div class="ob-spot"><span class="ob-spot-label" id="ob-spot-label">NIFTY</span><strong id="ob-spot">—</strong><span class="ob-live" id="ob-live"><i></i><span>connecting</span></span></div>
        <div class="ob-facts" id="ob-facts"></div>
      </div>
      <div class="ob-expiries" id="ob-expiries" role="tablist" aria-label="Expiry"></div>
      <div class="ob-error" id="ob-error" hidden></div>
      <div class="ob-grid">
        <aside class="ob-left card">
          <div class="ob-tabs" role="tablist">
            <button type="button" data-ob-left="readymade">Ready-made</button>
            <button type="button" data-ob-left="chain">Option chain</button>
            <button type="button" data-ob-left="positions">Positions</button>
            <button type="button" data-ob-left="paper">Portfolios</button>
          </div>
          <div class="ob-left-body" id="ob-left-body"></div>
        </aside>
        <section class="ob-main">
          <div class="ob-legs card">
            <div class="ob-legs-head">
              <div><div class="ob-kicker">Strategy</div><h3 id="ob-strategy-name">Your strategy</h3></div>
              <div class="ob-legs-tools">
                <button type="button" class="btn btn-sm" data-ob-act="save-start" title="Save these legs to load again later">☆ Save</button>
                <button type="button" class="btn btn-sm" data-ob-act="reprice" title="Set every entry price to the live LTP">↻ Entries to LTP</button>
                <button type="button" class="btn btn-sm" data-ob-act="clear">Clear</button>
              </div>
            </div>
            <div id="ob-legs-table"></div>
          </div>
          <div class="ob-actions card">
            <div class="ob-action-opts">
              <label>Product <select id="ob-product"><option value="INTRADAY">Intraday</option><option value="MARGIN">Normal (carry)</option></select></label>
              <label>Broker <select id="ob-broker"><option value="dhan">Dhan</option><option value="zerodha">Zerodha</option></select></label>
              <span class="ob-action-note" id="ob-action-note"></span>
            </div>
            <div class="ob-action-btns">
              <label class="ob-pf-pick">Portfolio <select id="ob-portfolio" aria-label="Portfolio"></select></label>
              <button type="button" class="btn" data-ob-act="paper">Add to portfolio</button>
              <button type="button" class="btn ob-live-btn" data-ob-act="live">Place live ▸</button>
            </div>
          </div>
          <div class="ob-stats" id="ob-stats"></div>
        </section>
        <div class="ob-analysis card">
          <div class="ob-tabs ob-tabs-inline" role="tablist">
            <button type="button" data-ob-ana="payoff">Payoff</button>
            <button type="button" data-ob-ana="table">P&amp;L table</button>
            <button type="button" data-ob-ana="greeks">Greeks</button>
          </div>
          <div class="ob-ana-body">
            <div class="ob-ana-pane" data-pane="payoff">
              <div class="ob-chart-wrap"><canvas id="ob-chart" aria-label="Payoff chart"></canvas><div class="ob-tip" id="ob-tip" hidden></div><div class="ob-chart-empty" id="ob-chart-empty">Pick a ready-made strategy, or tap <b>B</b> / <b>S</b> on the option chain.</div></div>
              <div class="ob-legend">
                <span><i class="ob-sw ob-sw-exp"></i>On expiry</span>
                <span><i class="ob-sw ob-sw-tgt"></i>On target date</span>
                <span><i class="ob-sw ob-sw-spot"></i>Spot</span>
                <label class="ob-toggle"><input type="checkbox" data-ob-flag="showSigma"> ±σ range</label>
                <label class="ob-toggle"><input type="checkbox" data-ob-flag="showOI"> Open interest</label>
                <span class="ob-zoom"><button type="button" data-ob-act="zoom-out" aria-label="Zoom out">−</button><button type="button" data-ob-act="zoom-in" aria-label="Zoom in">+</button></span>
              </div>
              <div class="ob-sliders">
                <div class="ob-slider"><span>Target date <b id="ob-target-label">Today</b></span><div class="ob-step-row"><button type="button" class="ob-step" data-ob-step="target:-1" title="One hour earlier" aria-label="Target one hour earlier">−</button><input type="range" id="ob-target" min="0" max="1000" value="0" aria-label="Target date"><button type="button" class="ob-step" data-ob-step="target:1" title="One hour later" aria-label="Target one hour later">+</button></div></div>
                <div class="ob-slider"><span>IV change <b id="ob-iv-label">+0.0</b></span><div class="ob-step-row"><button type="button" class="ob-step" data-ob-step="iv:-1" title="IV 0.1 point lower" aria-label="IV 0.1 point lower">−</button><input type="range" id="ob-iv" min="-15" max="15" step="0.1" value="0" aria-label="IV change"><button type="button" class="ob-step" data-ob-step="iv:1" title="IV 0.1 point higher" aria-label="IV 0.1 point higher">+</button></div></div>
                <button type="button" class="btn btn-sm" data-ob-act="reset-sliders">Reset</button>
              </div>
            </div>
            <div class="ob-ana-pane" data-pane="table" id="ob-pnl-table"></div>
            <div class="ob-ana-pane" data-pane="greeks" id="ob-greeks"></div>
          </div>
        </div>
      </div>`;
    chart.attach($('#ob-chart', root), $('#ob-tip', root));
  }

  function render() {
    const root = document.getElementById('ob-root');
    if (!root) return;
    if (!S.built) { buildShell(root); S.built = true; bind(root); }
    renderTop(); renderLeft(); renderLegs(); renderStats(); renderAnalysis();
  }

  function renderTop() {
    const chain = primary();
    document.querySelectorAll('[data-ob-und]').forEach((b) => b.classList.toggle('is-active', b.dataset.obUnd === S.underlying));
    $('#ob-spot-label').textContent = chain?.label || S.underlying;
    $('#ob-spot').textContent = chain ? price(chain.spot) : '—';
    const live = $('#ob-live');
    const age = chain ? Math.round((Date.now() - S.chains[S.expiry].at) / 1000) : null;
    live.className = 'ob-live ' + (S.error ? 'is-err' : chain?.stale ? 'is-stale' : S.sessionOpen ? 'is-on' : 'is-off');
    live.querySelector('span').textContent = S.error ? 'no data' : !chain ? 'connecting' : chain.stale ? 'delayed' : S.sessionOpen ? `live · ${age}s` : `market closed · ${String(chain.fetched_at || '').slice(11, 16)}`;
    $('#ob-facts').innerHTML = chain ? [
      ['ATM IV', `${num(chain.atm_iv).toFixed(1)}%`], ['PCR', num(chain.pcr).toFixed(2)], ['Max pain', strikeTxt(chain.max_pain)],
      ['Lot', chain.lot_size || '—'], ['Expiry in', dte(S.expiry)],
    ].map(([k, v]) => `<div><span>${k}</span><b>${esc(v)}</b></div>`).join('') : '';
    $('#ob-expiries').innerHTML = S.expiries.slice(0, 10).map((e) => `<button type="button" class="ob-exp ${e === S.expiry ? 'is-active' : ''}" data-ob-exp="${e}"><b>${shortDate(e)}</b><small>${dte(e)}</small></button>`).join('');
    const err = $('#ob-error');
    err.hidden = !S.error; err.textContent = S.error;
  }

  // ── left panel ───────────────────────────────────────────────────────────
  function renderLeft() {
    document.querySelectorAll('[data-ob-left]').forEach((b) => b.classList.toggle('is-active', b.dataset.obLeft === S.leftTab));
    const body = $('#ob-left-body');
    if (S.leftTab === 'readymade') body.innerHTML = readymadeHtml();
    else if (S.leftTab === 'chain') { body.innerHTML = chainHtml(); centreChain(body); }
    else if (S.leftTab === 'positions') body.innerHTML = positionsHtml();
    else body.innerHTML = paperHtml();
  }

  function templateLegsNow(t) {
    const chain = primary();
    if (!chain || !chain.atm || !chain.strike_step) return null;
    return M.templateLegs(t, { atm: chain.atm, step: chain.strike_step, expiries: S.expiries.length ? S.expiries.slice(S.expiries.indexOf(S.expiry)) : [S.expiry] });
  }
  // A thumbnail of the template's expiry payoff on today's prices.
  function miniPayoff(t) {
    const chain = primary();
    const legs = templateLegsNow(t);
    const w = 120, h = 46;
    if (!legs || !chain) return `<svg viewBox="0 0 ${w} ${h}" class="ob-mini"></svg>`;
    const priced = legs.map((l) => ({ ...l, entry: (quote(l.expiry, l.strike, l.type) || {}).ltp || 0, iv: (quote(l.expiry, l.strike, l.type) || {}).iv || chain.atm_iv, ltp: (quote(l.expiry, l.strike, l.type) || {}).ltp || 0 }));
    const c = { ...ctx(), lotSize: 1 };
    const sr = M.series(priced, c, { points: 48, zoom: 1.3 });
    const ys = sr.atExpiry;
    const lo = Math.min(0, ...ys), hi = Math.max(0, ...ys), span = hi - lo || 1;
    const px = (i) => (i / (ys.length - 1)) * w, py = (v) => h - 4 - ((v - lo) / span) * (h - 8);
    const d = ys.map((v, i) => `${i ? 'L' : 'M'}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join('');
    const z = py(0).toFixed(1);
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="ob-mini" aria-hidden="true">
      <defs><clipPath id="obc-up-${t.id}"><rect x="0" y="0" width="${w}" height="${z}"/></clipPath><clipPath id="obc-dn-${t.id}"><rect x="0" y="${z}" width="${w}" height="${h}"/></clipPath></defs>
      <line x1="0" x2="${w}" y1="${z}" y2="${z}" class="ob-mini-zero"/>
      <path d="${d}L${w},${z}L0,${z}Z" class="ob-mini-up" clip-path="url(#obc-up-${t.id})"/>
      <path d="${d}L${w},${z}L0,${z}Z" class="ob-mini-dn" clip-path="url(#obc-dn-${t.id})"/>
      <path d="${d}" class="ob-mini-line"/></svg>`;
  }
  function readymadeHtml() {
    const list = M.TEMPLATES.filter((t) => t.view === S.view);
    return `<div class="ob-views">${VIEWS.map(([k, label]) => `<button type="button" class="ob-chip ob-view-${k} ${k === S.view ? 'is-active' : ''}" data-ob-view="${k}">${label}</button>`).join('')}</div>
      <div class="ob-templates">${list.map((t) => `<button type="button" class="ob-template" data-ob-template="${t.id}">${miniPayoff(t)}<span>${esc(t.name)}</span></button>`).join('')}</div>
      <p class="ob-hint">Strikes are placed around today's ATM; every leg can be changed after.</p>`;
  }

  // A grid, not a <table>: the site's table contract (13px, roomy padding) is
  // right for ledgers, but a 7-column ladder in a side panel needs to be dense.
  function chainHtml() {
    const chain = primary();
    if (!chain) return `<div class="ob-empty">${S.error ? esc(S.error) : 'Loading the chain…'}</div>`;
    const idx = chain.rows.findIndex((r) => r.strike === chain.atm);
    const rows = chain.rows.slice(Math.max(0, idx - CHAIN_WINDOW), idx + CHAIN_WINDOW + 1);
    const maxOi = Math.max(1, ...rows.flatMap((r) => [r.ce?.oi || 0, r.pe?.oi || 0]));
    const held = new Map(S.legs.filter((l) => l.expiry === S.expiry).map((l) => [`${l.strike}${l.type}`, l.side]));
    const oi = (side, type) => side
      ? `<div class="ob-c-oi ob-oi-${type.toLowerCase()}"><i style="width:${(100 * side.oi / maxOi).toFixed(1)}%"></i><span>${(side.oi / 1e5).toFixed(1)}L</span><small class="${tone(side.oi_change)}">${side.oi_change > 0 ? '+' : ''}${(side.oi_change / 1e5).toFixed(1)}</small></div>`
      : '<div class="ob-c-oi"></div>';
    const ltp = (side) => `<div class="ob-c-ltp">${side ? price(side.ltp) : '—'}<small>${side && side.iv ? side.iv.toFixed(1) : ''}</small></div>`;
    const act = (side, r, type) => {
      if (!side) return '<div class="ob-c-act"></div>';
      const tag = held.get(`${r.strike}${type}`);
      return `<div class="ob-c-act"><button type="button" class="ob-b ${tag === 'BUY' ? 'is-on' : ''}" data-ob-add="BUY:${type}:${r.strike}" aria-label="Buy ${strikeTxt(r.strike)} ${type}">B</button><button type="button" class="ob-s ${tag === 'SELL' ? 'is-on' : ''}" data-ob-add="SELL:${type}:${r.strike}" aria-label="Sell ${strikeTxt(r.strike)} ${type}">S</button></div>`;
    };
    // CE reads outward-in (OI, price, B/S | strike); PE mirrors it.
    return `<div class="ob-chain-scroll"><div class="ob-chain" role="grid" aria-label="Option chain">
      <div class="ob-chain-head" role="row"><span>OI</span><span>CE ₹<small>IV</small></span><span></span><span>Strike</span><span></span><span>PE ₹<small>IV</small></span><span>OI</span></div>
      ${rows.map((r) => `<div class="ob-chain-row ${r.strike === chain.atm ? 'is-atm' : ''} ${r.strike < chain.spot ? 'ce-itm' : 'pe-itm'}" role="row" data-strike="${r.strike}">
          ${oi(r.ce, 'CE')}${ltp(r.ce)}${act(r.ce, r, 'CE')}
          <div class="ob-c-k">${strikeTxt(r.strike)}${r.strike === chain.atm ? `<small>spot ${price(chain.spot)}</small>` : ''}</div>
          ${act(r.pe, r, 'PE')}${ltp(r.pe)}${oi(r.pe, 'PE')}
        </div>`).join('')}
      </div></div>
      <p class="ob-hint">PCR ${num(chain.pcr).toFixed(2)} · max pain ${strikeTxt(chain.max_pain)} · shaded = in the money · OI in lakh, change below</p>`;
  }
  let chainCentredFor = '';
  function centreChain(body) {
    const key = `${S.underlying}:${S.expiry}`;
    if (chainCentredFor === key) return;
    const atm = body.querySelector('.ob-chain-row.is-atm');
    const inner = body.querySelector('.ob-chain-scroll');
    // The chain scrolls inside the sticky panel on wide screens, in its own
    // box on narrow ones: centre ATM in whichever one actually scrolls.
    const scroller = inner && inner.scrollHeight > inner.clientHeight + 1 ? inner : body;
    if (!atm || scroller.scrollHeight <= scroller.clientHeight + 1) return;
    const delta = atm.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    scroller.scrollTop += delta - scroller.clientHeight / 2 + atm.offsetHeight / 2;
    chainCentredFor = key;
  }

  function positionsHtml() {
    if (S.positions === null) return `<div class="ob-empty"><p>Bring your open option positions from Dhan (and Zerodha, if logged in today) into the builder to see their combined payoff.</p><button type="button" class="btn" data-ob-act="load-positions">Load my positions</button></div>`;
    const mine = S.positions.filter((p) => p.underlying === S.underlying);
    const notes = S.positionNotes.map((n) => `<p class="ob-hint is-warn">${esc(n)}</p>`).join('');
    if (!mine.length) return `<div class="ob-empty"><p>No open ${S.underlying} option positions.</p><button type="button" class="btn btn-sm" data-ob-act="load-positions">Refresh</button></div>${notes}`;
    return `<div class="ob-pos-list">${mine.map((p) => `<div class="ob-pos"><span class="ob-side ${p.side === 'BUY' ? 'is-buy' : 'is-sell'}">${p.side === 'BUY' ? 'B' : 'S'}</span><b>${strikeTxt(p.strike)} ${p.option_type}</b><small>${shortDate(p.expiry)} · ${p.quantity} qty · ${p.broker}</small><span>@ ${price(p.price)}</span></div>`).join('')}</div>
      <div class="ob-row-btns"><button type="button" class="btn" data-ob-act="analyse-positions">Analyse these</button><button type="button" class="btn btn-sm" data-ob-act="load-positions">Refresh</button></div>
      <p class="ob-hint">Analysed positions are marked <b>HELD</b> and are never re-sent as orders.</p>${notes}`;
  }

  function basketPnl(b) {
    if (b.status !== 'open') return b.realised;
    let total = 0, priced = true;
    for (const leg of b.legs) {
      const q = quote(leg.expiry, leg.strike, leg.option_type);
      if (!q || !(q.ltp > 0)) { priced = false; continue; }
      total += (leg.side === 'BUY' ? 1 : -1) * (q.ltp - leg.entry) * leg.lots * b.lot_size;
    }
    return priced ? total : NaN;
  }
  function strategyRow(b) {
    const pnl = basketPnl(b);
    const legs = b.legs.map((l) => `${l.side === 'BUY' ? 'B' : 'S'} ${l.lots}× ${strikeTxt(l.strike)}${l.option_type}`).join(' · ');
    return `<div class="ob-paper-row ${b.status}">
      <div><b>${esc(b.name)}</b><small>${esc(b.underlying)} · ${esc(legs)}</small><small>${esc(String(b.opened_at).slice(0, 16).replace('T', ' '))}${b.closed_at ? ' → ' + esc(String(b.closed_at).slice(11, 16)) + ' · closed' : ''}</small></div>
      <div class="ob-paper-pnl ${tone(pnl)}">${Number.isFinite(pnl) ? signedInr(pnl) : (b.underlying === S.underlying ? '—' : esc(b.underlying))}</div>
      <div class="ob-paper-btns">${b.status === 'open'
        ? `<button type="button" class="btn btn-sm" data-ob-paper-load="${b.id}">Open</button><button type="button" class="btn btn-sm btn-danger" data-ob-paper-close="${b.id}">Close</button>`
        : `<button type="button" class="btn btn-sm" data-ob-paper-load="${b.id}">Open</button><button type="button" class="btn btn-sm" data-ob-paper-del="${b.id}" aria-label="Remove">×</button>`}</div>
    </div>`;
  }
  function namingRow(kind, placeholder) {
    const n = S.naming;
    if (!n || n.kind !== kind) return '';
    return `<form class="ob-naming" data-ob-naming="${kind}"><input type="text" maxlength="40" value="${esc(n.value || '')}" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}" autofocus><button type="submit" class="btn btn-sm">Save</button><button type="button" class="btn btn-sm" data-ob-act="naming-cancel">Cancel</button></form>`;
  }
  // Portfolios: Sensibull-style draft portfolios (folders of paper strategies,
  // tracked from the prices they were added at) and saved strategies (legs
  // kept for later, no tracking).
  function paperHtml() {
    const seg = `<div class="ob-seg"><button type="button" class="${S.pfView === 'portfolios' ? 'is-active' : ''}" data-ob-pfview="portfolios">Draft portfolios</button><button type="button" class="${S.pfView === 'saved' ? 'is-active' : ''}" data-ob-pfview="saved">Saved strategies</button></div>`;
    if (S.pfView === 'saved') {
      const list = S.saved.slice().reverse();
      return seg + `<div class="ob-pf-actions"><button type="button" class="btn btn-sm" data-ob-act="save-start">☆ Save current strategy</button></div>${namingRow('save', 'Strategy name')}
        ${list.length ? `<div class="ob-paper">${list.map((r) => `<div class="ob-paper-row"><div><b>${esc(r.name)}</b><small>${esc(r.underlying)} · ${esc(r.legs.map((l) => `${l.side === 'BUY' ? 'B' : 'S'} ${l.lots}× ${strikeTxt(l.strike)}${l.option_type}`).join(' · '))}</small><small>saved ${esc(String(r.saved_at).slice(0, 16).replace('T', ' '))}</small></div><div></div><div class="ob-paper-btns"><button type="button" class="btn btn-sm" data-ob-saved-load="${r.id}">Load</button><button type="button" class="btn btn-sm" data-ob-saved-del="${r.id}" aria-label="Remove">×</button></div></div>`).join('')}</div>`
          : '<div class="ob-empty"><p>Nothing saved yet. Build a strategy and save it here to load it again any day.</p></div>'}`;
    }
    const rows = S.portfolios.map((p) => {
      const mine = S.paper.filter((b) => b.portfolio_id === p.id);
      const open = mine.filter((b) => b.status === 'open').length;
      const values = mine.map(basketPnl).filter(Number.isFinite);
      const total = values.reduce((a, v) => a + v, 0);
      const expanded = S.openPortfolio === p.id;
      return `<div class="ob-pf ${expanded ? 'is-open' : ''}">
        <div class="ob-pf-head" data-ob-pf-toggle="${p.id}" role="button" tabindex="0" aria-expanded="${expanded}">
          <div><b>${esc(p.name)}</b><small>${mine.length} strateg${mine.length === 1 ? 'y' : 'ies'}${open ? ` · ${open} open` : ''}</small></div>
          <span class="ob-pf-pnl ${tone(total)}">${mine.length ? signedInr(total) : '0'}</span>
          <span class="ob-pf-tools"><button type="button" data-ob-pf-rename="${p.id}" aria-label="Rename ${esc(p.name)}" title="Rename">✎</button><button type="button" data-ob-pf-delete="${p.id}" aria-label="Delete ${esc(p.name)}" title="Delete">🗑</button></span>
        </div>
        ${S.naming && S.naming.kind === 'rename' && S.naming.id === p.id ? namingRow('rename', 'Portfolio name') : ''}
        ${expanded ? `<div class="ob-pf-body">${mine.length ? `<div class="ob-paper">${mine.slice().reverse().map(strategyRow).join('')}</div>` : '<p class="ob-hint">Empty. Build a strategy, pick this portfolio below the legs and press <b>Add to portfolio</b>.</p>'}</div>` : ''}
      </div>`;
    }).join('');
    return seg + `<div class="ob-pf-actions"><button type="button" class="btn btn-sm" data-ob-act="new-portfolio">＋ Create new portfolio</button></div>${namingRow('new', 'New portfolio name')}<div class="ob-pf-list">${rows}</div>
      <p class="ob-hint">Each strategy is tracked from the prices it was added at; the total is live P&amp;L for open ones plus booked P&amp;L for closed.</p>`;
  }

  // ── legs ─────────────────────────────────────────────────────────────────
  function renderLegs() {
    const box = $('#ob-legs-table');
    const lot = lotSize();
    if (!S.legs.length) { box.innerHTML = '<div class="ob-empty ob-empty-legs">No legs yet.</div>'; $('#ob-strategy-name').textContent = 'Your strategy'; return; }
    $('#ob-strategy-name').textContent = guessName();
    const expiryOpts = (sel) => S.expiries.slice(0, 10).map((e) => `<option value="${e}" ${e === sel ? 'selected' : ''}>${shortDate(e)}</option>`).join('');
    box.innerHTML = `<div class="ob-legs-scroll"><table class="ob-legs-grid" data-pf-disable-sort>
      <thead><tr><th></th><th>B/S</th><th>Expiry</th><th>Strike</th><th>Type</th><th>Lots</th><th>Entry ₹</th><th>LTP</th><th>P&amp;L</th><th></th></tr></thead>
      <tbody>${S.legs.map((l) => {
        const pnl = l.ltp > 0 ? (l.side === 'BUY' ? 1 : -1) * (l.ltp - l.entry) * l.lots * lot : NaN;
        const strikes = strikesFor(l.expiry);
        const near = strikes.length ? strikes.filter((k) => Math.abs(k - l.strike) <= (primary()?.strike_step || 50) * 30) : [l.strike];
        if (!near.includes(l.strike)) near.push(l.strike);
        near.sort((a, b) => a - b);
        return `<tr class="${l.enabled ? '' : 'is-off'} ${l.source === 'position' ? 'is-held' : ''}" data-leg="${l.id}">
          <td><input type="checkbox" ${l.enabled ? 'checked' : ''} data-leg-f="enabled" aria-label="Include leg"></td>
          <td><button type="button" class="ob-side ${l.side === 'BUY' ? 'is-buy' : 'is-sell'}" data-leg-act="side">${l.side === 'BUY' ? 'B' : 'S'}</button></td>
          <td><select data-leg-f="expiry">${expiryOpts(l.expiry)}</select></td>
          <td class="ob-strike-cell"><button type="button" data-leg-act="down" aria-label="Lower strike">−</button><select data-leg-f="strike">${near.map((k) => `<option value="${k}" ${k === l.strike ? 'selected' : ''}>${strikeTxt(k)}</option>`).join('')}</select><button type="button" data-leg-act="up" aria-label="Higher strike">+</button></td>
          <td><button type="button" class="ob-type ${l.type === 'CE' ? 'is-ce' : 'is-pe'}" data-leg-act="type">${l.type}</button></td>
          <td class="ob-lots-cell"><button type="button" data-leg-act="lots-">−</button><input type="number" min="1" max="50" value="${l.lots}" data-leg-f="lots" aria-label="Lots"><button type="button" data-leg-act="lots+">+</button></td>
          <td><input type="number" step="0.05" min="0" value="${l.entry ? l.entry.toFixed(2) : ''}" data-leg-f="entry" class="ob-entry" aria-label="Entry price"></td>
          <td class="ob-num">${l.ltp ? price(l.ltp) : '—'}${l.source === 'position' ? '<small class="ob-held">HELD</small>' : ''}</td>
          <td class="ob-num ${tone(pnl)}">${Number.isFinite(pnl) ? signedInr(pnl) : '—'}</td>
          <td><button type="button" class="ob-del" data-leg-act="del" aria-label="Remove leg">×</button></td>
        </tr>`;
      }).join('')}</tbody></table></div>
      <div class="ob-legs-foot">
        <span>Lot size <b>${lot || '—'}</b></span>
        <span>${M.netPremium(S.legs, lot) >= 0 ? 'Net credit' : 'Net debit'} <b class="${tone(M.netPremium(S.legs, lot))}">${inr(Math.abs(M.netPremium(S.legs, lot)))}</b></span>
        <span>Live P&amp;L <b class="${tone(M.currentPnl(S.legs, lot))}">${signedInr(M.currentPnl(S.legs, lot))}</b></span>
      </div>`;
  }
  function guessName(legs = S.legs) {
    const live = M.activeLegs(legs);
    const sig = (legs) => legs.map((l) => `${l.side}${l.type}`).sort().join('|');
    for (const t of M.TEMPLATES) {
      if (t.legs.length === live.length && sig(t.legs.map(([side, type]) => ({ side, type }))) === sig(live)) return t.name;
    }
    return live.length ? `Custom · ${live.length} leg${live.length > 1 ? 's' : ''}` : 'Your strategy';
  }

  // ── stats ────────────────────────────────────────────────────────────────
  function renderStats() {
    const box = $('#ob-stats');
    if (!box) return;
    const c = ctx();
    const live = M.activeLegs(S.legs);
    const st = live.length && c.spot && c.lotSize ? M.expiryStats(S.legs, c) : null;
    const pop = st ? M.probabilityOfProfit(S.legs, c) : null;
    const net = c.lotSize ? M.netPremium(S.legs, c.lotSize) : 0;
    const rr = st && Number.isFinite(st.maxProfit) && Number.isFinite(st.maxLoss) && st.maxLoss < 0 ? `${(st.maxProfit / -st.maxLoss).toFixed(2)} : 1` : st ? '—' : '—';
    const m = S.margin;
    const cards = [
      ['Max profit', st ? inr(st.maxProfit) : '—', st ? 'is-pos' : ''],
      ['Max loss', st ? inr(st.maxLoss) : '—', st ? 'is-neg' : ''],
      ['Breakeven', st ? (st.breakevens.length ? st.breakevens.map((b) => strikeTxt(Math.round(b * 100) / 100)).join(' · ') : 'none') : '—', ''],
      ['Chance of profit', pop === null ? '—' : `${(pop * 100).toFixed(1)}%`, pop === null ? '' : pop >= 0.5 ? 'is-pos' : 'is-warn'],
      ['Reward : risk', rr, ''],
      [net >= 0 ? 'Net credit' : 'Net debit', live.length ? inr(Math.abs(net)) : '—', ''],
      ['Margin (Dhan)', m ? inr(m.total) : S.marginNote ? '…' : '—', '', m && m.hedge_benefit ? `hedge benefit ${inr(m.hedge_benefit)}` : S.marginNote],
      ['Funds available', m && m.available ? inr(m.available) : '—', m && m.available && m.total > m.available ? 'is-neg' : ''],
    ];
    box.innerHTML = cards.map(([k, v, cls, sub]) => `<div class="ob-stat"><span>${esc(k)}</span><b class="${cls}">${esc(v)}</b>${sub ? `<small>${esc(sub)}</small>` : ''}</div>`).join('')
      + (st && !st.exact ? '<p class="ob-hint ob-stat-note">Several expiries: profit, loss and breakevens are read at the first expiry, with later legs valued on their IV.</p>' : '');
  }

  // ── analysis: payoff, table, greeks ──────────────────────────────────────
  // The target is a clock time, so it stays put while the clock runs; it is
  // read between now and the first expiry.
  function targetMs() {
    const now = Date.now();
    const first = M.firstExpiryMs(S.legs);
    if (!first || !S.targetAt) return now;
    return Math.max(now, Math.min(first, S.targetAt));
  }
  // The steppers: the target moves to the next or previous whole hour (IST),
  // never past the first expiry and never before now; IV moves 0.1 point.
  const HOUR_MS = 3600e3;
  const IST_MS = 5.5 * HOUR_MS;
  function stepTarget(dir) {
    const now = Date.now();
    const first = M.firstExpiryMs(S.legs);
    if (!first || first <= now) return;
    const at = targetMs() + IST_MS;
    const next = (dir > 0 ? Math.floor(at / HOUR_MS) + 1 : Math.ceil(at / HOUR_MS) - 1) * HOUR_MS - IST_MS;
    S.targetAt = next <= now ? 0 : Math.min(first, next);
  }
  function stepIv(dir) {
    S.ivShift = Math.max(-15, Math.min(15, Math.round(S.ivShift * 10 + dir) / 10));
  }
  function setSliders() {
    const now = Date.now();
    const first = M.firstExpiryMs(S.legs);
    $('#ob-target').value = first > now && S.targetAt ? Math.round(((targetMs() - now) / (first - now)) * 1000) : 0;
    $('#ob-iv').value = S.ivShift;
  }
  function renderAnalysis() {
    document.querySelectorAll('[data-ob-ana]').forEach((b) => b.classList.toggle('is-active', b.dataset.obAna === S.anaTab));
    document.querySelectorAll('.ob-ana-pane').forEach((p) => { p.hidden = p.dataset.pane !== S.anaTab; });
    document.querySelectorAll('[data-ob-flag]').forEach((el) => { el.checked = !!S[el.dataset.obFlag]; });
    const tgt = targetMs();
    const firstExp = M.firstExpiryMs(S.legs);
    $('#ob-target-label').textContent = tgt <= Date.now() ? 'Today' : new Date(tgt).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }) + (firstExp && tgt >= firstExp ? ' · expiry' : '');
    if (document.activeElement !== $('#ob-target')) setSliders();
    $('#ob-iv-label').textContent = `${S.ivShift >= 0 ? '+' : ''}${S.ivShift.toFixed(1)} pts`;
    $('#ob-product').value = S.product; $('#ob-broker').value = S.broker;
    const held = S.legs.filter((l) => l.enabled && l.source === 'position').length;
    $('#ob-action-note').textContent = held ? `${held} held leg${held > 1 ? 's are' : ' is'} analysed only, never re-sent.` : '';
    if (S.anaTab === 'payoff') drawChart();
    else if (S.anaTab === 'table') renderPnlTable();
    else renderGreeks();
  }

  function drawChart() {
    const c = ctx();
    const live = M.activeLegs(S.legs);
    $('#ob-chart-empty').hidden = !!(live.length && c.spot && c.lotSize);
    if (!live.length || !c.spot || !c.lotSize) { chart.set(null); return; }
    const sr = M.series(S.legs, c, { zoom: S.zoom, targetMs: targetMs() });
    const st = M.expiryStats(S.legs, c);
    const chain = primary();
    const oi = S.showOI && chain ? chain.rows.filter((r) => r.strike >= sr.lo && r.strike <= sr.hi).map((r) => ({ k: r.strike, ce: r.ce?.oi || 0, pe: r.pe?.oi || 0 })) : [];
    chart.set({
      xs: sr.xs, exp: sr.atExpiry, tgt: sr.atTarget, lo: sr.lo, hi: sr.hi, spot: c.spot,
      bands: S.showSigma ? M.sigmaBands(c, sr.expiryMs) : null,
      breakevens: st ? st.breakevens : [],
      marks: live.map((l) => ({ k: l.strike, side: l.side, type: l.type })),
      oi, showTarget: true, pnlAt: (x) => [M.pnlAt(S.legs, x, sr.expiryMs, c), M.pnlAt(S.legs, x, sr.targetMs, c)],
    });
  }

  function renderPnlTable() {
    const box = $('#ob-pnl-table');
    const c = ctx();
    if (!M.activeLegs(S.legs).length || !c.spot) { box.innerHTML = '<div class="ob-empty">Add legs to see the P&L at each price.</div>'; return; }
    const step = primary()?.strike_step || 50;
    const base = Math.round(c.spot / step) * step;
    const exp = M.firstExpiryMs(S.legs), tgt = targetMs();
    const rows = [];
    for (let i = -12; i <= 12; i++) {
      const p = base + i * step;
      rows.push({ p, ch: (p / c.spot - 1) * 100, t: M.pnlAt(S.legs, p, tgt, c), e: M.pnlAt(S.legs, p, exp, c) });
    }
    const max = Math.max(1, ...rows.map((r) => Math.abs(r.e)), ...rows.map((r) => Math.abs(r.t)));
    const bar = (v) => `<i class="${v >= 0 ? 'is-pos' : 'is-neg'}" style="width:${(50 * Math.abs(v) / max).toFixed(1)}%;${v >= 0 ? 'left:50%' : `right:50%`}"></i>`;
    box.innerHTML = `<div class="ob-table-scroll"><table class="ob-pnl" data-pf-disable-sort>
      <thead><tr><th>${esc(S.underlying)} at</th><th>Change</th><th>On target date</th><th>On expiry</th></tr></thead>
      <tbody>${rows.map((r) => `<tr class="${Math.abs(r.p - base) < 1e-6 ? 'is-spot' : ''}"><td>${strikeTxt(r.p)}</td><td class="${tone(r.ch)}">${r.ch >= 0 ? '+' : ''}${r.ch.toFixed(2)}%</td>
        <td class="ob-bar-cell">${bar(r.t)}<span class="${tone(r.t)}">${signedInr(r.t)}</span></td><td class="ob-bar-cell">${bar(r.e)}<span class="${tone(r.e)}">${signedInr(r.e)}</span></td></tr>`).join('')}</tbody></table></div>`;
  }

  function renderGreeks() {
    const box = $('#ob-greeks');
    const c = ctx();
    if (!M.activeLegs(S.legs).length || !c.lotSize) { box.innerHTML = '<div class="ob-empty">Add legs to see the strategy greeks.</div>'; return; }
    const g = M.positionGreeks(S.legs, c);
    const f = (v, d = 2) => (Number.isFinite(v) ? v.toLocaleString('en-IN', { maximumFractionDigits: d, minimumFractionDigits: d }) : '—');
    box.innerHTML = `<div class="ob-greek-cards">
        <div><span>Delta</span><b class="${tone(g.total.delta)}">${f(g.total.delta)}</b><small>₹ per 1-pt move ${signedInr(g.total.delta)}</small></div>
        <div><span>Gamma</span><b>${f(g.total.gamma, 4)}</b><small>delta change per point</small></div>
        <div><span>Theta</span><b class="${tone(g.total.theta)}">${signedInr(g.total.theta)}</b><small>per day, from time alone</small></div>
        <div><span>Vega</span><b class="${tone(g.total.vega)}">${signedInr(g.total.vega)}</b><small>per 1 point of IV</small></div>
      </div>
      <div class="ob-table-scroll"><table class="ob-pnl ob-greek-table" data-pf-disable-sort><thead><tr><th>Leg</th><th>Qty</th><th>IV</th><th>Delta</th><th>Gamma</th><th>Theta ₹</th><th>Vega ₹</th></tr></thead>
      <tbody>${g.rows.map((r) => `<tr><td>${r.leg.side === 'BUY' ? 'B' : 'S'} ${strikeTxt(r.leg.strike)}${r.leg.type} <small>${shortDate(r.leg.expiry)}</small></td><td>${r.qty}</td><td>${r.leg.iv ? r.leg.iv.toFixed(1) + '%' : '—'}</td><td>${f(r.delta)}</td><td>${f(r.gamma, 4)}</td><td>${f(r.theta, 0)}</td><td>${f(r.vega, 0)}</td></tr>`).join('')}</tbody></table></div>
      <p class="ob-hint">Today's greeks are Dhan's; after a slider move they are Black-Scholes on each leg's IV.</p>`;
  }

  // ── the chart ────────────────────────────────────────────────────────────
  const chart = (() => {
    let canvas, tip, ctx2d, data = null, shown = null, from = null, t0 = 0, raf = 0, hoverX = null, dpr = 1, W = 0, H = 0;
    const DUR = 240;
    const pad = { l: 64, r: 16, t: 18, b: 34 };
    const ease = (t) => 1 - Math.pow(1 - t, 3);

    function attach(c, t) {
      canvas = c; tip = t; ctx2d = canvas.getContext('2d');
      new ResizeObserver(() => { size(); paint(); }).observe(canvas.parentElement);
      canvas.addEventListener('pointermove', (e) => { const r = canvas.getBoundingClientRect(); hoverX = e.clientX - r.left; paint(); });
      canvas.addEventListener('pointerleave', () => { hoverX = null; paint(); });
      size();
    }
    function size() {
      if (!canvas) return;
      const r = canvas.parentElement.getBoundingClientRect();
      dpr = window.devicePixelRatio || 1;
      W = Math.max(280, r.width); H = Math.round(Math.max(260, Math.min(560, r.width * (r.width > 900 ? 0.38 : 0.55))));
      canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    }
    function set(next) {
      data = next;
      if (!next) { shown = null; paint(); return; }
      const same = shown && shown.xs.length === next.xs.length;
      from = same ? shown : null;
      t0 = performance.now();
      cancelAnimationFrame(raf);
      const step = (now) => {
        const k = from ? ease(Math.min(1, (now - t0) / DUR)) : 1;
        shown = from ? blend(from, data, k) : data;
        paint();
        if (k < 1) raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
    }
    function blend(a, b, k) {
      const mix = (x, y) => x + (y - x) * k;
      return { ...b, xs: b.xs.map((v, i) => mix(a.xs[i], v)), exp: b.exp.map((v, i) => mix(a.exp[i], v)), tgt: b.tgt.map((v, i) => mix(a.tgt[i], v)), lo: mix(a.lo, b.lo), hi: mix(a.hi, b.hi), yLo: mix(a.yLo ?? yRange(a)[0], yRange(b)[0]), yHi: mix(a.yHi ?? yRange(a)[1], yRange(b)[1]) };
    }
    function yRange(d) {
      const all = d.exp.concat(d.tgt).filter(Number.isFinite);
      let lo = Math.min(0, ...all), hi = Math.max(0, ...all);
      const span = (hi - lo) || 1000;
      return [lo - span * 0.1, hi + span * 0.1];
    }
    function niceStep(span, n) {
      const raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
      return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
    }
    function paint() {
      if (!ctx2d) return;
      const g = ctx2d;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, W, H);
      const d = shown;
      if (!d) { tip.hidden = true; return; }
      const C = {
        text: cssVar('--muted', '#8491a3'), grid: 'rgba(148,163,184,0.10)', zero: 'rgba(148,163,184,0.45)',
        pos: cssVar('--green', '#34d399'), neg: cssVar('--red', '#f87171'), tgt: cssVar('--purple', '#a78bfa'),
        spot: cssVar('--warn', '#fbbf24'), ce: cssVar('--cepe-ce', '#38bdf8'), pe: cssVar('--cepe-pe', '#f0a13a'),
      };
      const [yLo, yHi] = d.yLo !== undefined ? [d.yLo, d.yHi] : yRange(d);
      const X = (v) => pad.l + (v - d.lo) / (d.hi - d.lo) * (W - pad.l - pad.r);
      const Y = (v) => pad.t + (yHi - v) / (yHi - yLo) * (H - pad.t - pad.b);
      const bottom = H - pad.b, zeroY = Y(0);
      g.font = "10.5px 'JetBrains Mono', monospace";

      // sigma bands
      if (d.bands) {
        for (const [band, alpha, label] of [[d.bands.two, 0.045, '2σ'], [d.bands.one, 0.07, '1σ']]) {
          const a = Math.max(pad.l, X(band[0])), b = Math.min(W - pad.r, X(band[1]));
          if (b > a) { g.fillStyle = `rgba(148,163,184,${alpha})`; g.fillRect(a, pad.t, b - a, bottom - pad.t); g.fillStyle = C.text; g.fillText(`−${label}`, a + 3, pad.t + 10); g.fillText(`+${label}`, b - 22, pad.t + 10); }
        }
      }
      // grid + y labels
      const ys = niceStep(yHi - yLo, 5);
      g.strokeStyle = C.grid; g.lineWidth = 1; g.fillStyle = C.text; g.textAlign = 'right';
      for (let v = Math.ceil(yLo / ys) * ys; v <= yHi; v += ys) {
        const y = Math.round(Y(v)) + 0.5;
        g.beginPath(); g.moveTo(pad.l, y); g.lineTo(W - pad.r, y); g.stroke();
        g.fillText(compactInr(v), pad.l - 8, y + 3);
      }
      // x labels
      const xsStep = niceStep(d.hi - d.lo, Math.max(3, Math.floor(W / 110)));
      g.textAlign = 'center';
      for (let v = Math.ceil(d.lo / xsStep) * xsStep; v <= d.hi; v += xsStep) g.fillText(strikeTxt(v), X(v), H - 12);
      // open interest
      if (d.oi && d.oi.length) {
        const maxOi = Math.max(1, ...d.oi.flatMap((o) => [o.ce, o.pe]));
        const hMax = (bottom - pad.t) * 0.26, bw = Math.max(2, Math.min(7, (W - pad.l - pad.r) / d.oi.length / 3));
        for (const o of d.oi) {
          const x = X(o.k);
          g.fillStyle = C.ce; g.globalAlpha = 0.32; g.fillRect(x - bw - 0.5, bottom - hMax * o.ce / maxOi, bw, hMax * o.ce / maxOi);
          g.fillStyle = C.pe; g.fillRect(x + 0.5, bottom - hMax * o.pe / maxOi, bw, hMax * o.pe / maxOi);
          g.globalAlpha = 1;
        }
      }
      // expiry payoff: filled green above zero, red below
      const path = () => { g.beginPath(); d.exp.forEach((v, i) => (i ? g.lineTo(X(d.xs[i]), Y(v)) : g.moveTo(X(d.xs[i]), Y(v)))); };
      for (const [clipTop, clipH, colour] of [[pad.t, zeroY - pad.t, C.pos], [zeroY, bottom - zeroY, C.neg]]) {
        if (clipH <= 0) continue;
        g.save(); g.beginPath(); g.rect(pad.l, clipTop, W - pad.l - pad.r, clipH); g.clip();
        path(); g.lineTo(X(d.xs[d.xs.length - 1]), zeroY); g.lineTo(X(d.xs[0]), zeroY); g.closePath();
        const grad = g.createLinearGradient(0, clipTop, 0, clipTop + clipH);
        const [a, b] = colour === C.pos ? [0.30, 0.04] : [0.04, 0.30];
        grad.addColorStop(0, withAlpha(colour, a)); grad.addColorStop(1, withAlpha(colour, b));
        g.fillStyle = grad; g.fill();
        path(); g.strokeStyle = colour; g.lineWidth = 2.2; g.lineJoin = 'round'; g.stroke();
        g.restore();
      }
      // zero line
      g.strokeStyle = C.zero; g.lineWidth = 1; g.beginPath(); g.moveTo(pad.l, Math.round(zeroY) + 0.5); g.lineTo(W - pad.r, Math.round(zeroY) + 0.5); g.stroke();
      // target-date curve
      if (d.showTarget) {
        g.setLineDash([6, 4]); g.strokeStyle = C.tgt; g.lineWidth = 1.8; g.beginPath();
        d.tgt.forEach((v, i) => (i ? g.lineTo(X(d.xs[i]), Y(v)) : g.moveTo(X(d.xs[i]), Y(v)))); g.stroke(); g.setLineDash([]);
      }
      // leg strikes
      for (const m of d.marks || []) {
        const x = X(m.k); if (x < pad.l || x > W - pad.r) continue;
        g.fillStyle = m.side === 'BUY' ? C.pos : C.neg;
        g.beginPath(); g.moveTo(x, bottom); g.lineTo(x - 5, bottom + 8); g.lineTo(x + 5, bottom + 8); g.closePath(); g.fill();
      }
      // breakevens
      g.textAlign = 'center';
      for (const b of d.breakevens || []) {
        const x = X(b); if (x < pad.l || x > W - pad.r) continue;
        g.fillStyle = C.text; g.beginPath(); g.arc(x, zeroY, 3.5, 0, Math.PI * 2); g.fill();
        g.fillText(strikeTxt(Math.round(b)), x, zeroY - 8);
      }
      // spot
      const sx = X(d.spot);
      g.strokeStyle = C.spot; g.lineWidth = 1.2; g.setLineDash([2, 3]); g.beginPath(); g.moveTo(sx, pad.t); g.lineTo(sx, bottom); g.stroke(); g.setLineDash([]);
      pill(g, `Spot ${strikeTxt(Math.round(d.spot * 100) / 100)}`, sx, pad.t + 2, C.spot);
      // hover
      if (hoverX !== null && hoverX >= pad.l && hoverX <= W - pad.r && data) {
        const price = d.lo + (hoverX - pad.l) / (W - pad.l - pad.r) * (d.hi - d.lo);
        const [pe, pt] = data.pnlAt(price);
        g.strokeStyle = 'rgba(148,163,184,0.55)'; g.lineWidth = 1; g.beginPath(); g.moveTo(hoverX, pad.t); g.lineTo(hoverX, bottom); g.stroke();
        for (const [v, col] of [[pe, pe >= 0 ? C.pos : C.neg], [pt, C.tgt]]) { g.fillStyle = col; g.beginPath(); g.arc(hoverX, Y(v), 4, 0, Math.PI * 2); g.fill(); }
        const ch = (price / d.spot - 1) * 100;
        tip.hidden = false;
        tip.innerHTML = `<b>${strikeTxt(Math.round(price * 100) / 100)}</b> <small>${ch >= 0 ? '+' : ''}${ch.toFixed(2)}% from spot</small>
          <div><span class="ob-sw ob-sw-exp"></span>On expiry <b class="${tone(pe)}">${signedInr(pe)}</b></div>
          <div><span class="ob-sw ob-sw-tgt"></span>On target <b class="${tone(pt)}">${signedInr(pt)}</b></div>`;
        const tw = tip.offsetWidth;
        tip.style.left = `${Math.min(W - tw - 6, Math.max(6, hoverX + 14))}px`;
        tip.style.top = `${pad.t + 8}px`;
      } else tip.hidden = true;
    }
    function pill(g, text, x, y, colour) {
      g.font = "600 10.5px 'JetBrains Mono', monospace";
      const w = g.measureText(text).width + 12;
      const left = Math.min(W - pad.r - w, Math.max(pad.l, x - w / 2));
      g.fillStyle = withAlpha(colour, 0.16); g.strokeStyle = withAlpha(colour, 0.6);
      g.beginPath(); g.roundRect ? g.roundRect(left, y, w, 17, 8) : g.rect(left, y, w, 17); g.fill(); g.stroke();
      g.fillStyle = colour; g.textAlign = 'left'; g.fillText(text, left + 6, y + 12);
      g.font = "10.5px 'JetBrains Mono', monospace";
    }
    function withAlpha(colour, a) {
      if (colour.startsWith('#')) {
        const h = colour.length === 4 ? colour.slice(1).split('').map((c) => c + c).join('') : colour.slice(1, 7);
        return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a})`;
      }
      const m = colour.match(/rgba?\(([^)]+)\)/);
      if (m) { const [r, gg, b] = m[1].split(',').map((x) => x.trim()); return `rgba(${r},${gg},${b},${a})`; }
      return colour;
    }
    return { attach, set, repaint: () => { size(); paint(); } };
  })();

  // ── events ───────────────────────────────────────────────────────────────
  function changed(opts = {}) {
    syncLegs();
    save();
    renderLegs(); renderStats(); renderAnalysis();
    if (S.leftTab === 'chain') renderLeft();
    if (opts.margin) queueMargin();
  }
  function legOf(el) { const tr = el.closest('[data-leg]'); return tr ? S.legs.find((l) => l.id === Number(tr.dataset.leg)) : null; }
  function stepStrike(leg, dir) {
    const strikes = strikesFor(leg.expiry);
    const i = strikes.indexOf(leg.strike);
    const next = strikes[Math.max(0, Math.min(strikes.length - 1, (i < 0 ? strikes.findIndex((k) => k > leg.strike) : i) + dir))];
    if (next) { leg.strike = next; leg.entry = 0; }
  }

  function focusNaming() {
    requestAnimationFrame(() => { const input = document.querySelector('.ob-naming input'); if (input) { input.focus(); input.select(); } });
  }

  function bind(root) {
    root.addEventListener('submit', (e) => {
      const form = e.target.closest('[data-ob-naming]');
      if (!form) return;
      e.preventDefault();
      submitNaming(form.dataset.obNaming, form.querySelector('input').value);
    });
    root.addEventListener('click', async (e) => {
      const t = e.target.closest('button, [data-ob-pf-toggle]');
      if (!t) return;
      const d = t.dataset;
      if (d.obUnd && d.obUnd !== S.underlying) {
        S.underlying = d.obUnd; S.expiries = []; S.expiry = ''; S.chains = {}; S.margin = null; chainCentredFor = '';
        S.legs = []; // a strategy belongs to one index
        save(); render(); await refresh(); return;
      }
      if (d.obExp) { S.expiry = d.obExp; chainCentredFor = ''; save(); await refresh(); return; }
      if (d.obLeft) { S.leftTab = d.obLeft; save(); renderLeft(); if (d.obLeft === 'paper') loadPaper(); return; }
      if (d.obView) { S.view = d.obView; save(); renderLeft(); return; }
      if (d.obAna) { S.anaTab = d.obAna; save(); renderAnalysis(); return; }
      if (d.obStep) { if (e.detail === 0) stepBy(d.obStep); return; } // a mouse press stepped on pointerdown
      if (d.obTemplate) {
        const tpl = M.TEMPLATES.find((x) => x.id === d.obTemplate);
        const legs = tpl && templateLegsNow(tpl);
        if (!legs) { notify('The chain has not loaded yet.', 'warning'); return; }
        for (const e2 of new Set(legs.map((l) => l.expiry))) if (!chainFor(e2)) { try { await loadChain(e2); } catch (_) { /* priced on the next poll */ } }
        S.legs = legs.map((l) => newLeg(l));
        S.targetAt = 0; S.ivShift = 0; setSliders();
        changed({ margin: true }); return;
      }
      if (d.obAdd) { const [side, type, k] = d.obAdd.split(':'); addLeg(side, type, Number(k)); return; }
      if (d.legAct) {
        const leg = legOf(t); if (!leg) return;
        if (d.legAct === 'del') S.legs = S.legs.filter((l) => l !== leg);
        else if (d.legAct === 'side') { leg.side = leg.side === 'BUY' ? 'SELL' : 'BUY'; }
        else if (d.legAct === 'type') { leg.type = leg.type === 'CE' ? 'PE' : 'CE'; leg.entry = 0; }
        else if (d.legAct === 'up' || d.legAct === 'down') stepStrike(leg, d.legAct === 'up' ? 1 : -1);
        else if (d.legAct === 'lots+') leg.lots = Math.min(50, leg.lots + 1);
        else if (d.legAct === 'lots-') leg.lots = Math.max(1, leg.lots - 1);
        changed({ margin: true }); return;
      }
      if (d.obPfview) { S.pfView = d.obPfview; S.naming = null; renderLeft(); return; }
      if (d.obPfToggle && !e.target.closest('.ob-pf-tools')) { const id = Number(d.obPfToggle); S.openPortfolio = S.openPortfolio === id ? null : id; renderLeft(); return; }
      if (d.obPfRename) { const p = S.portfolios.find((x) => x.id === Number(d.obPfRename)); S.naming = { kind: 'rename', id: p.id, value: p.name }; renderLeft(); focusNaming(); return; }
      if (d.obPfDelete) { deletePortfolio(Number(d.obPfDelete)); return; }
      if (d.obSavedLoad) { loadSaved(Number(d.obSavedLoad)); return; }
      if (d.obSavedDel) { try { await api(`/api/option-builder/saved/${d.obSavedDel}`, { method: 'DELETE' }); } catch (err) { notify(err.message, 'error'); } await loadPaper(); return; }
      if (d.obPaperLoad) { openPaper(Number(d.obPaperLoad)); return; }
      if (d.obPaperClose) { closePaper(Number(d.obPaperClose)); return; }
      if (d.obPaperDel) { deletePaper(Number(d.obPaperDel)); return; }
      switch (d.obAct) {
        case 'clear': S.legs = []; S.margin = null; changed(); break;
        case 'new-portfolio': S.naming = { kind: 'new', value: '' }; S.leftTab = 'paper'; S.pfView = 'portfolios'; renderLeft(); focusNaming(); break;
        case 'save-start':
          if (!sendable().length) { notify('Add legs first.', 'warning'); break; }
          S.naming = { kind: 'save', value: guessName(sendable()) }; S.leftTab = 'paper'; S.pfView = 'saved'; save(); renderLeft(); loadPaper(); focusNaming(); break;
        case 'naming-cancel': S.naming = null; renderLeft(); break;
        case 'reprice': for (const l of S.legs) if (l.ltp > 0 && l.source !== 'position') l.entry = l.ltp; changed(); break;
        case 'zoom-in': S.zoom = Math.min(4, S.zoom * 1.35); renderAnalysis(); break;
        case 'zoom-out': S.zoom = Math.max(0.4, S.zoom / 1.35); renderAnalysis(); break;
        case 'reset-sliders': S.targetAt = 0; S.ivShift = 0; S.zoom = 1; setSliders(); renderAnalysis(); renderStats(); break;
        case 'load-positions': await loadPositions(); break;
        case 'analyse-positions': analysePositions(); break;
        case 'paper': await paperTrade(); break;
        case 'live': await placeLive(); break;
        default: break;
      }
    });
    root.addEventListener('change', (e) => {
      const el = e.target;
      if (el.dataset.obFlag) { S[el.dataset.obFlag] = el.checked; save(); renderAnalysis(); return; }
      if (el.id === 'ob-product') { S.product = el.value; save(); queueMargin(); return; }
      if (el.id === 'ob-broker') { S.broker = el.value; save(); return; }
      if (el.id === 'ob-portfolio') { S.portfolioId = Number(el.value) || null; return; }
      const f = el.dataset.legF; if (!f) return;
      const leg = legOf(el); if (!leg) return;
      if (f === 'enabled') leg.enabled = el.checked;
      else if (f === 'expiry') { leg.expiry = el.value; leg.entry = 0; if (!chainFor(el.value)) loadChain(el.value).then(() => changed({ margin: true })).catch(() => {}); }
      else if (f === 'strike') { leg.strike = Number(el.value); leg.entry = 0; }
      else if (f === 'lots') leg.lots = Math.max(1, Math.min(50, Math.round(num(el.value, 1))));
      else if (f === 'entry') leg.entry = Math.max(0, num(el.value));
      changed({ margin: f !== 'entry' });
    });
    // sliders redraw on every frame they move, without touching the network
    let pending = 0;
    root.addEventListener('input', (e) => {
      if (e.target.id !== 'ob-target' && e.target.id !== 'ob-iv') return;
      if (e.target.id === 'ob-target') {
        const v = Number(e.target.value) / 1000, now = Date.now(), first = M.firstExpiryMs(S.legs);
        S.targetAt = v > 0 && first > now ? (v >= 1 ? first : now + (first - now) * v) : 0;
      } else S.ivShift = Math.round(Number(e.target.value) * 10) / 10;
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(() => renderAnalysis());
    });
    // The +/- steppers: one step per press, and stepping on while held.
    let hold = 0;
    const stopHold = () => { clearTimeout(hold); hold = 0; };
    function stepBy(spec) {
      const [what, dir] = spec.split(':');
      if (what === 'target') stepTarget(Number(dir)); else stepIv(Number(dir));
      setSliders();
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(() => renderAnalysis());
    }
    root.addEventListener('pointerdown', (e) => {
      const b = e.target.closest('[data-ob-step]');
      if (!b || e.button !== 0) return;
      e.preventDefault(); // no focus ring or text selection from a held press
      stopHold();
      stepBy(b.dataset.obStep);
      const again = (delay) => { hold = setTimeout(() => { stepBy(b.dataset.obStep); again(70); }, delay); };
      again(420);
    });
    root.addEventListener('pointerout', (e) => { if (e.target.closest('[data-ob-step]')) stopHold(); });
    window.addEventListener('pointerup', stopHold);
    window.addEventListener('pointercancel', stopHold);
  }

  // ── positions, paper, live ───────────────────────────────────────────────
  async function loadPositions() {
    try {
      const body = await api('/api/option-builder/positions');
      S.positions = body.legs || []; S.positionNotes = body.notes || [];
    } catch (err) { S.positions = []; S.positionNotes = [err.message]; }
    renderLeft();
  }
  function analysePositions() {
    const lot = lotSize();
    const mine = (S.positions || []).filter((p) => p.underlying === S.underlying);
    if (!mine.length || !lot) return;
    const missing = [...new Set(mine.map((p) => p.expiry))].filter((e) => !chainFor(e));
    Promise.all(missing.map((e) => loadChain(e).catch(() => null))).then(() => {
      S.legs = S.legs.filter((l) => l.source !== 'position').concat(mine.map((p) => newLeg({
        side: p.side, type: p.option_type, strike: p.strike, expiry: p.expiry,
        lots: Math.max(1, Math.round(p.quantity / lot)), entry: p.price, source: 'position',
      })));
      changed({ margin: true });
      notify(`${mine.length} position leg${mine.length > 1 ? 's' : ''} loaded for analysis.`, 'success');
    });
  }
  async function loadPaper() {
    try {
      const body = await api('/api/option-builder/paper');
      S.paper = body.baskets || []; S.portfolios = body.portfolios || []; S.saved = body.saved || [];
    } catch (_) { S.paper = []; }
    if (!S.portfolios.some((p) => p.id === S.portfolioId)) S.portfolioId = S.portfolios[0]?.id ?? null;
    renderPortfolioPicker();
    if (S.leftTab === 'paper') renderLeft();
  }
  function renderPortfolioPicker() {
    const sel = $('#ob-portfolio');
    if (!sel) return;
    sel.innerHTML = S.portfolios.map((p) => `<option value="${p.id}" ${p.id === S.portfolioId ? 'selected' : ''}>${esc(p.name)}</option>`).join('') || '<option value="">Paper</option>';
  }
  async function submitNaming(kind, value) {
    const name = String(value || '').trim();
    if (!name) { notify('Type a name first.', 'warning'); return; }
    try {
      if (kind === 'new') {
        const body = await api('/api/option-builder/portfolios', { method: 'POST', body: JSON.stringify({ name }) });
        S.openPortfolio = body.portfolio.id; S.portfolioId = body.portfolio.id;
      } else if (kind === 'rename') {
        await api(`/api/option-builder/portfolios/${S.naming.id}`, { method: 'PUT', body: JSON.stringify({ name }) });
      } else if (kind === 'save') {
        const legs = sendable();
        if (!legs.length) { notify('Add legs first.', 'warning'); return; }
        await api('/api/option-builder/saved', { method: 'POST', body: JSON.stringify({ legs: payloadLegs(legs), name }) });
        notify(`Saved “${name}”.`, 'success');
      }
      S.naming = null;
      await loadPaper();
    } catch (err) { notify(err.message, 'error'); }
  }
  async function deletePortfolio(id) {
    const p = S.portfolios.find((x) => x.id === id);
    if (!p) return;
    const ok = typeof window.customConfirm === 'function'
      ? await window.customConfirm(`Delete <b>${esc(p.name)}</b> and its closed strategies? A portfolio with open strategies cannot be deleted.`, { title: 'Delete portfolio?', okText: 'Delete', danger: true })
      : window.confirm('Delete this portfolio?');
    if (!ok) return;
    try { await api(`/api/option-builder/portfolios/${id}`, { method: 'DELETE' }); } catch (err) { notify(err.message, 'error'); }
    await loadPaper();
  }
  function loadSaved(id) {
    const r = S.saved.find((x) => x.id === id);
    if (!r) return;
    if (r.underlying !== S.underlying) { notify(`Switch to ${r.underlying} first.`, 'warning'); return; }
    const today = new Date().toISOString().slice(0, 10);
    const stale = r.legs.filter((l) => l.expiry < today).length;
    S.legs = r.legs.map((l) => newLeg({ side: l.side, type: l.option_type, strike: l.strike, expiry: l.expiry >= today ? l.expiry : S.expiry, lots: l.lots }));
    changed({ margin: true });
    if (stale) notify(`${stale} leg${stale > 1 ? 's' : ''} had expired; moved to ${shortDate(S.expiry)}.`, 'warning');
  }
  function sendable() {
    return M.activeLegs(S.legs).filter((l) => l.source !== 'position');
  }
  async function paperTrade() {
    const legs = sendable();
    if (!legs.length) { notify('Add legs first.', 'warning'); return; }
    try {
      const portfolio = S.portfolios.find((p) => p.id === S.portfolioId);
      await api('/api/option-builder/paper', { method: 'POST', body: JSON.stringify({ legs: payloadLegs(legs).map((l, i) => ({ ...l, price: legs[i].entry || legs[i].ltp })), name: guessName(legs), portfolio_id: S.portfolioId }) });
      notify(`Added to ${portfolio ? portfolio.name : 'your portfolio'} — tracked at live prices.`, 'success');
      S.leftTab = 'paper'; S.pfView = 'portfolios'; S.openPortfolio = S.portfolioId; save(); await loadPaper();
    } catch (err) { notify(err.message, 'error'); }
  }
  function openPaper(id) {
    const b = S.paper.find((x) => x.id === id);
    if (!b) return;
    if (b.underlying !== S.underlying) { notify(`Switch to ${b.underlying} first.`, 'warning'); return; }
    S.legs = b.legs.map((l) => newLeg({ side: l.side, type: l.option_type, strike: l.strike, expiry: l.expiry, lots: l.lots, entry: l.entry }));
    changed({ margin: true });
  }
  async function closePaper(id) {
    try { await api(`/api/option-builder/paper/${id}/close`, { method: 'POST' }); notify('Paper strategy closed at live prices.', 'success'); }
    catch (err) { notify(err.message, 'error'); }
    await loadPaper();
  }
  async function deletePaper(id) {
    try { await api(`/api/option-builder/paper/${id}`, { method: 'DELETE' }); } catch (err) { notify(err.message, 'error'); }
    await loadPaper();
  }
  async function placeLive() {
    const legs = sendable();
    const lot = lotSize();
    if (!legs.length || !lot) { notify('Add legs first.', 'warning'); return; }
    const ordered = legs.filter((l) => l.side === 'BUY').concat(legs.filter((l) => l.side === 'SELL'));
    const m = S.margin;
    const rows = ordered.map((l) => `<div style="display:flex;gap:10px;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);white-space:nowrap"><b style="color:${l.side === 'BUY' ? 'var(--green)' : 'var(--red)'}">${l.side}</b><span>${l.lots} lot${l.lots > 1 ? 's' : ''} · ${esc(S.underlying)} ${strikeTxt(l.strike)} ${l.type} · ${shortDate(l.expiry)}</span><span>≈ ₹${price(l.side === 'BUY' ? (l.ask || l.ltp) : (l.bid || l.ltp))}</span></div>`).join('');
    const msg = `<div style="font-size:12.5px;line-height:1.5">
      <p style="margin:0 0 8px">Real orders on <b>${S.broker === 'zerodha' ? 'Zerodha' : 'Dhan'}</b> · ${S.product === 'MARGIN' ? 'Normal (carry)' : 'Intraday'}. Buys go first, so every short leg is hedged when it lands; if a buy fails, the sells are held back.</p>
      <div style="font-family:'JetBrains Mono',monospace;font-size:11.5px;text-align:left">${rows}</div>
      <p style="margin:8px 0 0;color:var(--muted)">Each is a limit order 2% through the touch so it fills now.${m ? ` Dhan margin ≈ ${inr(m.total)}.` : ''}</p></div>`;
    const ok = typeof window.customConfirm === 'function'
      ? await window.customConfirm(msg, { title: `Place ${ordered.length} live order${ordered.length > 1 ? 's' : ''}?`, okText: 'Place orders', danger: true })
      : window.confirm('Place these live orders?');
    if (!ok) return;
    try {
      const body = await api('/api/option-builder/execute', { method: 'POST', body: JSON.stringify({ legs: payloadLegs(legs), product: S.product, broker: S.broker, name: guessName(legs) }) });
      const failed = body.results.filter((r) => r.status !== 'sent');
      if (!failed.length) notify(`All ${body.sent} orders sent.`, 'success');
      else notify(`${body.sent} sent, ${failed.length} not: ${failed[0].message || failed[0].status}`, 'error');
    } catch (err) { notify(err.message, 'error'); }
  }

  // ── lifecycle ────────────────────────────────────────────────────────────
  let restored = false;
  async function init() {
    if (!restored) { restore(); restored = true; }
    S.active = true;
    render();
    await refresh(S.expiries.length ? undefined : 'expiries');
    if (S.legs.length) queueMargin();
    loadPaper(); // the portfolio picker needs the list on every desk
    schedule();
  }
  function pause() { S.active = false; clearTimeout(S.timer); }
  document.addEventListener('visibilitychange', () => { if (S.active && !document.hidden) { refresh(); schedule(); } });

  window.initOptionBuilderPage = init;
  window.PFOptionBuilder = { pause, state: S };
})();
