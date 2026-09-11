/* THE chart — the CryptoForge Canvas renderer, ported. Written for the Test
 * Bench (retired 2026-08-29); every strategy's chart draws through it now.
 *
 * This is the chart Phil accepted on CryptoForge on 2026-07-30, moved across
 * rather than rewritten, because a second implementation of the same drawing
 * is a second set of bugs.  What changed in the move:
 *
 *   * names are prefixed _pfChart instead of _cfChart, so nothing collides
 *     with philforge-app.js;
 *   * money is rupees, not dollars;
 *   * timestamps read as IST, which is the only clock this app uses;
 *   * the CryptoForge cascade modal's restaging helper is gone — the Test
 *     Bench owns its own page chrome and never moves the canvas host.
 *
 * One deliberate departure from the CryptoForge original (2026-07-30): the
 * x-axis is BAR INDEX, not wall time.  Crypto trades around the clock so the
 * two were the same thing; NSE does not, and on wall time every night and
 * weekend became a void.  Timestamps still flow through the whole pipeline —
 * the projection converts at the edge — and session gaps are drawn as
 * translucent synthetic candles instead of empty space.
 *
 * Payload shape (built by engine/ladder_reporting.py):
 *   { timeframe, candles:[{t,o,h,l,c,is_mother}], mother:{high,low},
 *     trendlines:[{id,a1:{t,p},a2:{t,p},active}],
 *     legs:[{leg_id,touch_timestamp,touch_high,low,levels:{...},
 *            orders:[{level,inr_notional}]}],
 *     entries:[{t,price}], exits:[{t,price,pnl}],
 *     avg_entry_price, tp_price, frozen }
 * Every `t` is epoch SECONDS, never an ISO string — the projection does
 * arithmetic on them.
 */

function _pfChartIst(ts) {
  if (!ts) return '--';
  var d = new Date((Number(ts) + 19800) * 1000);
  if (isNaN(d.getTime())) return '--';
  return d.toISOString().slice(5, 16).replace('T', ' ');
}

// Indian grouping, so 1275000 reads 12,75,000 the way every other rupee figure
// in this app does.
function _pfChartInr(value) {
  var n = Number(value);
  if (!isFinite(n)) return '₹0';
  return '₹' + Math.round(n).toLocaleString('en-IN');
}

function _pfBenchChartHostHtml() {
  // The zoom cluster is part of the HOST, not any one caller's toolbar, so
  // every chart on the site carries the same − % + controls without each page
  // remembering to add them (Phil's one-strip standard, 2026-08-13).
  return '<div class="pf-bench-canvas-host" id="pf-bench-canvas-host">'
    + '<canvas id="pf-bench-canvas-main"></canvas>'
    + '<canvas id="pf-bench-canvas-overlay"></canvas>'
    + '<div class="pf-bench-zoom" data-bench-zoom>'
    + '<button type="button" data-bench-zoom-out aria-label="Zoom out">&minus;</button>'
    // The readout IS the reset. Reset used to be a double-click on the plot,
    // announced in a title tooltip -- a phone has neither hover nor a reliable
    // double-click, so on mobile the function existed and could not be found
    // (Phil, 2026-08-17: "Chart zoom functions must be clear for charts in
    // mobile"). A button carrying the percentage is the obvious target, and it
    // still shows what it always showed.
    + '<button type="button" class="pf-bench-zoom-reset" data-bench-zoom-level data-bench-zoom-reset'
    + ' aria-label="Reset zoom to fit" title="Reset zoom to fit">100%</button>'
    + '<button type="button" data-bench-zoom-in aria-label="Zoom in">+</button>'
    + '</div>'
    + '</div>';
}

var _PF_CHART_MAX_STRUCTURES = 3;

// buyMark / sellMark are deliberately OFF the candle palette. Green buy arrows
// vanished against green candles and red sell arrows against red ones — the two
// marks you most need to find were camouflaged by the bars they sat on. Amber
// and white appear nowhere else on the chart.
var _PF_CHART_DARK = {
  grid: 'rgba(148,163,184,0.12)', axis: 'rgba(148,163,184,0.55)',
  up: '#3fae56', down: '#d9534f', mother: '#a855f7', tp: '#10b981',
  avg: '#e2e8f0', fill: '#22c55e', fillRing: '#0b1220',
  buyMark: '#ffffff', sellMark: '#fbbf24', markRing: '#0b1220',
  fibs: ['#3b82f6', '#22c55e', '#ef4444'],
  ema: '#fb923c', rsi: '#c084fc', rsiGuide: 'rgba(148,163,184,0.45)', rsiBand: 'rgba(192,132,252,0.10)'
};
var _PF_CHART_LIGHT = {
  grid: 'rgba(15,23,42,0.10)', axis: 'rgba(51,65,85,0.75)',
  up: '#0f766e', down: '#be123c', mother: '#7c3aed', tp: '#047857',
  avg: '#334155', fill: '#15803d', fillRing: '#ffffff',
  buyMark: '#1e293b', sellMark: '#b45309', markRing: '#ffffff',
  fibs: ['#1d4ed8', '#15803d', '#be123c'],
  ema: '#c2410c', rsi: '#7e22ce', rsiGuide: 'rgba(51,65,85,0.45)', rsiBand: 'rgba(126,34,206,0.08)'
};

function _pfChartPalette() {
  var theme = document.documentElement.getAttribute('data-theme');
  if (!theme || theme === 'auto') {
    theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  return theme === 'light' ? _PF_CHART_LIGHT : _PF_CHART_DARK;
}

// API overlays carry literal colours. Resolve their light-theme equivalents
// at paint time so switching theme also updates CPR, Supertrend and quotes.
function _pfChartSeriesColor(color, PAL, fallback) {
  if (!color) return fallback;
  if (PAL !== _PF_CHART_LIGHT) return color;
  var light = {
    '#4ade80': '#15803d', '#6ee7b7': '#047857', '#34d399': '#047857',
    '#f87171': '#b91c1c', '#fca5a5': '#b91c1c',
    '#f59e0b': '#92400e', '#fbbf24': '#92400e',
    '#38bdf8': '#0369a1', '#60a5fa': '#1d4ed8', '#93c5fd': '#1d4ed8',
    '#e2e8f0': '#334155', '#ffffff': '#334155', '#fff': '#334155'
  };
  return light[String(color).toLowerCase()] || color;
}

// THE ONE PALETTE, exported so a second chart cannot invent its own. The
// Terminal campaign chart kept a private copy and it drifted: two of its three
// dark fib colours were written `var(--green)` and `var(--red)`, and Canvas does
// not resolve CSS variables -- assigning one to strokeStyle is silently ignored,
// so the context keeps whatever colour it had. Every leg after the first, every
// trendline after the first and every fill drew in leg 1's blue.
window.pfChartPalette = _pfChartPalette;

// THE SAME CAP, EXPORTED FOR THE SAME REASON. Only the newest three legs and
// three trendlines are drawn; past that the chart is a cat's cradle and the
// structure that matters is the one nearest the price. The Terminal campaign
// chart had its own drawing loop and no cap at all, so it drew seven fibs and
// six trendlines while every other chart on the site drew three.
window.pfChartMaxStructures = _PF_CHART_MAX_STRUCTURES;

/* The newest `pfChartMaxStructures` of a structure list, never dropping the
 * ACTIVE one — Classic's exception, kept: the live trendline stays on the chart
 * even when three retired ones came after it. `isActive` is a predicate because
 * the two payload shapes spell it differently. */
window.pfChartLatestStructures = function (list, isActive) {
  var all = Array.isArray(list) ? list : [];
  var kept = all.slice(-_PF_CHART_MAX_STRUCTURES);
  if (typeof isActive !== 'function') return kept;
  var active = all.filter(isActive)[0];
  if (!active || kept.indexOf(active) !== -1) return kept;
  // THE EXCEPTION NEVER WORKED. It used to prepend the active line and then
  // slice(-MAX) again, which drops exactly what was just prepended -- so the
  // live trendline vanished the moment three retired ones came after it. Make
  // room instead: the active line plus the newest MAX-1 others.
  return [active].concat(kept.slice(-(_PF_CHART_MAX_STRUCTURES - 1)));
};


// Live canvas state, or null when no canvas chart is mounted. Holding the
// ResizeObserver here is what makes teardown possible — without it, every
// refresh would leave another observer watching a detached element.
var _pfChartCanvas = null;

/* Retire every OTHER canvas host in the document.
 *
 * The renderer addresses its surfaces by fixed ids, so only one chart can be
 * live at a time -- that has always been true. What made it bite is that the
 * Equity page grew charts that stay in the DOM after you navigate away (an
 * expanded scanner row, the ladder overlay). With two hosts present,
 * getElementById returns whichever comes FIRST in document order, so opening
 * the scalp option chart painted into the scanner's canvas and left the scalp
 * dialog blank -- no error, nothing in the console, just an empty box.
 *
 * Retiring the strays here keeps the ids unique by construction, which is the
 * only way this stays fixed as more charts are added.
 */
function _pfChartCanvasRetireOthers(keep) {
  var hosts = document.querySelectorAll('#pf-bench-canvas-host');
  for (var i = 0; i < hosts.length; i += 1) {
    var node = hosts[i];
    if (node === keep || !node.parentNode) continue;
    var note = document.createElement('p');
    note.className = 'pf-bench-empty';
    note.textContent = 'Chart closed — only one chart can be open at a time.';
    node.parentNode.replaceChild(note, node);
  }
}

function _pfChartCanvasMount(d, scope) {
  _pfChartCanvasTeardown();
  // Scoped to the container the caller just wrote the host into. A global
  // lookup finds the first host in the document, which is not necessarily the
  // one being drawn -- see _pfChartCanvasRetireOthers.
  var host = (scope && scope.querySelector('#pf-bench-canvas-host'))
    || document.getElementById('pf-bench-canvas-host');
  if (!host) return;
  _pfChartCanvasRetireOthers(host);
  var main = host.querySelector('#pf-bench-canvas-main');
  var overlay = host.querySelector('#pf-bench-canvas-overlay');
  if (!main || !overlay) return;
  _pfChartCanvas = {
    host: host, main: main, overlay: overlay,
    ctx: main.getContext('2d'), octx: overlay.getContext('2d'),
    data: d, w: 0, h: 0, dpr: 0, ro: null, themeObserver: null,
    // Phase 2 owns this model. Phase 3 changes it through pan/zoom/axis-drag;
    // Phase 4 preserves it across a live refresh.
    viewport: null, projection: null, paint: null, paintKey: '', handlers: [], drag: null,
    // Pending requestAnimationFrame id for a coalesced resize; see the
    // ResizeObserver below for why the work cannot run inline.
    resizeFrame: 0
  };
  var c = _pfChartCanvas;
  _pfChartCanvasResize();
  _pfChartCanvasBindInteraction(_pfChartCanvas);
  // The host's own zoom cluster. Bound per-mount; teardown removes the host.
  var zoomBox = host.querySelector('[data-bench-zoom]');
  if (zoomBox) {
    var paintLevel = function () {
      var level = zoomBox.querySelector('[data-bench-zoom-level]');
      if (level && typeof pfChartZoomPercent === 'function') level.textContent = pfChartZoomPercent() + '%';
    };
    zoomBox.addEventListener('click', function (event) {
      if (event.target.closest('[data-bench-zoom-in]')) _pfChartCanvasZoom(0.8);
      else if (event.target.closest('[data-bench-zoom-out]')) _pfChartCanvasZoom(1.25);
      else if (event.target.closest('[data-bench-zoom-reset]')) _pfChartCanvasZoom(0, true);
      else return;
      paintLevel();
    });
    // Wheel-zoom and double-click reset land on the canvas; keep the readout
    // honest without a caller having to wire anything.
    host.addEventListener('wheel', function () { requestAnimationFrame(paintLevel); }, { passive: true });
    host.addEventListener('dblclick', function () { requestAnimationFrame(paintLevel); });
  }
  // Canvas pixels do not inherit CSS colours. Watch the single theme attribute
  // and repaint from the same payload/viewport when it changes; SVG gets this
  // for free through a fresh DOM render, Canvas must do it explicitly.
  if (window.MutationObserver) {
    _pfChartCanvas.themeObserver = new MutationObserver(function () {
      if (_pfChartCanvas && _pfChartCanvas === c) _pfChartCanvasDraw();
    });
    _pfChartCanvas.themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  }
  // The host is sized entirely by CSS, and the things that change it — going
  // fullscreen, rotating a phone, dragging the window edge — do not all fire a
  // resize event on window. Observe the element itself.
  if (window.ResizeObserver) {
    // Deferred to the next frame, deliberately. Resizing writes canvas
    // width/height and inline styles, which mutates layout — do that INSIDE the
    // observer callback and the browser cannot finish delivering the batch it
    // is in the middle of, so it aborts with "ResizeObserver loop completed
    // with undelivered notifications". That is fired at window.onerror, which
    // on this site puts a full "Something went wrong" screen over the app the
    // moment a chart opens. Coalescing to one frame keeps the layout write out
    // of the delivery cycle, and collapses a burst of observations into a
    // single redraw.
    _pfChartCanvas.ro = new ResizeObserver(function () {
      if (c.resizeFrame) return;
      c.resizeFrame = requestAnimationFrame(function () {
        c.resizeFrame = 0;
        // Torn down, or replaced by another chart, between frames.
        if (_pfChartCanvas === c) _pfChartCanvasResize();
      });
    });
    _pfChartCanvas.ro.observe(host);
  } else {
    window.addEventListener('resize', _pfChartCanvasResize);
  }
}

function _pfChartCanvasTeardown() {
  var c = _pfChartCanvas;
  _pfChartCanvas = null;
  if (!c) return;
  (c.handlers || []).forEach(function (row) { c.host.removeEventListener(row[0], row[1], row[2]); });
  // A frame already queued would otherwise resize a chart that is gone.
  if (c.resizeFrame) { cancelAnimationFrame(c.resizeFrame); c.resizeFrame = 0; }
  if (c.ro) { try { c.ro.disconnect(); } catch (err) {} }
  else window.removeEventListener('resize', _pfChartCanvasResize);
  if (c.themeObserver) { try { c.themeObserver.disconnect(); } catch (err) {} }
}

// The actual "high definition" fix. The backing store is sized in DEVICE
// pixels and the context scaled by the ratio, so a 1px line is one real pixel
// on a retina screen. The SVG cannot do this: it is laid out once at a fixed
// 1440×660 and then stretched to whatever width the panel happens to be.
function _pfChartCanvasResize() {
  var c = _pfChartCanvas;
  if (!c || !c.host.isConnected) return;
  var box = c.host.getBoundingClientRect();
  var cssW = Math.max(Math.round(box.width), 1);
  var cssH = Math.max(Math.round(box.height), 1);
  var dpr = Math.max(window.devicePixelRatio || 1, 1);
  if (cssW === c.w && cssH === c.h && dpr === c.dpr) return;
  // Ignore a wobble of a pixel or two once the chart is up. Sub-pixel layout
  // rounding and a scrollbar arriving and leaving can nudge the box back and
  // forth forever; redrawing on each nudge is a visible flicker and buys
  // nothing, since a 1px change is not perceptible in the drawing. The first
  // paint (c.w === 0) and any DPR change always go through.
  if (c.w && dpr === c.dpr && Math.abs(cssW - c.w) <= 2 && Math.abs(cssH - c.h) <= 2) return;
  c.w = cssW; c.h = cssH; c.dpr = dpr;
  [c.main, c.overlay].forEach(function (cv) {
    cv.width = Math.round(cssW * dpr);
    cv.height = Math.round(cssH * dpr);
    cv.style.width = cssW + 'px';
    cv.style.height = cssH + 'px';
  });
  // Setting width/height resets the context, so the DPR transform has to be
  // reapplied every time — after which everything downstream draws in plain
  // CSS pixels and never has to think about the ratio again.
  c.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.octx.setTransform(dpr, 0, 0, dpr, 0, 0);
  _pfChartCanvasDraw();
}

function _pfChartCanvasStructures(d) {
  // Same helper the Terminal campaign chart uses, so the cap cannot be three
  // in one chart and seven in another.
  return {
    legs: window.pfChartLatestStructures(d.legs),
    trendlines: window.pfChartLatestStructures(d.trendlines, function (tl) { return tl && tl.active; }),
  };
}

function _pfChartCanvasTimeframeSeconds(d) {
  var tf = String(d.timeframe || d.campaign_timeframe || '5m').toLowerCase();
  var match = tf.match(/^(\d+)(m|h|d|w)$/);
  if (!match) return 300;
  var units = { m: 60, h: 3600, d: 86400, w: 604800 };
  return Math.max(Number(match[1]) * units[match[2]], 1);
}

// ── Bar-index axis ───────────────────────────────────────────
// The x-axis walks BARS, not the clock.  NSE trades 09:15–15:30 and sleeps
// nights and weekends; projected on wall time, five sessions of candles bunch
// into thin stripes separated by voids (Phil: "connect the candles without any
// gaps").  So x is the candle's position in the array, times are interpolated
// only for labels, and anything between two sessions simply does not exist —
// the same convention TradingView draws with.
function _pfChartCanvasAxisBuild(d) {
  var candles = d.candles || [];
  var times = [];
  for (var i = 0; i < candles.length; i++) times.push(Number(candles[i].t));
  var diffs = [];
  for (var j = 1; j < times.length; j++) {
    var step = times[j] - times[j - 1];
    if (step > 0) diffs.push(step);
  }
  diffs.sort(function (a, b) { return a - b; });
  // Median, not mean: one weekend in the diffs would drag a mean to hours.
  var barSec = diffs.length ? diffs[Math.floor(diffs.length / 2)] : _pfChartCanvasTimeframeSeconds(d);
  return { source: candles, times: times, barSec: Math.max(barSec, 1) };
}

function _pfChartCanvasAxisOf(c) {
  var candles = (c.data || {}).candles || [];
  if (!c.axis || c.axis.source !== candles) c.axis = _pfChartCanvasAxisBuild(c.data || {});
  return c.axis;
}

// Epoch seconds -> fractional bar index. Piecewise-linear inside the data,
// extrapolated at one bar per barSec beyond either edge.
function _pfChartCanvasIdxOf(axis, t) {
  var times = axis.times;
  if (!times.length) return 0;
  t = Number(t);
  if (t <= times[0]) return (t - times[0]) / axis.barSec;
  var last = times.length - 1;
  if (t >= times[last]) return last + (t - times[last]) / axis.barSec;
  var lo = 0, hi = last;
  while (hi - lo > 1) {
    var mid = (lo + hi) >> 1;
    if (times[mid] <= t) lo = mid; else hi = mid;
  }
  return lo + (t - times[lo]) / Math.max(times[hi] - times[lo], 1);
}

// Fractional bar index -> epoch seconds, for axis and crosshair labels only.
function _pfChartCanvasTimeAt(axis, idx) {
  var times = axis.times;
  if (!times.length) return 0;
  idx = Number(idx);
  if (idx <= 0) return times[0] + idx * axis.barSec;
  var last = times.length - 1;
  if (idx >= last) return times[last] + (idx - last) * axis.barSec;
  var lo = Math.floor(idx);
  return times[lo] + (idx - lo) * (times[lo + 1] - times[lo]);
}

// How many bars of context frame the trade on either side.
// How far past the candles a level may drag the y-axis, in multiples of the
// visible candle range. ONE: the candles are the subject of the chart, and a
// rung a full range away is already the furthest thing worth seeing beside
// them. Deeper ones stay drawn and return on zoom-out.
var _PF_CHART_LEVEL_FIT_SPANS = 1;
var _PF_CHART_TRADE_PAD_BARS = 25;

// Initial fit: the TRADE, not the whole fetch.  With entries the window is 25
// bars before the first buy (the mother always kept in view) to 25 past the
// exit; without any it falls back to every candle.  The price range then fits
// what is actually visible plus the drawn lines — fitting the whole fetch
// squashed the trade into a sliver whenever later days fell far below it.
// tMin/tMax are BAR INDICES; only the names are inherited.
function _pfChartCanvasFit(c) {
  var d = c.data || {};
  var candles = d.candles || [];
  if (!candles.length) return null;
  var axis = _pfChartCanvasAxisOf(c);
  var last = candles.length - 1;
  var iMin = -0.5, iMax = last + 0.5;
  var entries = d.entries || [], exits = d.exits || [];
  if (entries.length) {
    var entryIdx = Infinity;
    entries.forEach(function (fill) {
      if (fill && fill.t != null) entryIdx = Math.min(entryIdx, _pfChartCanvasIdxOf(axis, fill.t));
    });
    var exitIdx = last;
    if (exits.length) {
      exitIdx = 0;
      exits.forEach(function (exit) {
        if (exit && exit.t != null) exitIdx = Math.max(exitIdx, _pfChartCanvasIdxOf(axis, exit.t));
      });
    }
    var motherIdx = 0;
    for (var m = 0; m < candles.length; m++) {
      if (candles[m].is_mother) { motherIdx = m; break; }
    }
    if (isFinite(entryIdx)) {
      iMin = Math.max(-0.5, Math.min(motherIdx - 2, entryIdx - _PF_CHART_TRADE_PAD_BARS) - 0.5);
      iMax = Math.min(last + 0.5, exitIdx + _PF_CHART_TRADE_PAD_BARS + 0.5);
    }
  }
  var from = Math.max(Math.ceil(iMin), 0), to = Math.min(Math.floor(iMax), last);
  var lo = Number(candles[from].l), hi = Number(candles[from].h);
  for (var i = from; i <= to; i++) {
    lo = Math.min(lo, Number(candles[i].l));
    hi = Math.max(hi, Number(candles[i].h));
  }
  if (d.mother && d.mother.high) hi = Math.max(hi, Number(d.mother.high));
  if (d.mother && d.mother.low) lo = Math.min(lo, Number(d.mother.low));
  var structures = _pfChartCanvasStructures(d);
  structures.legs.forEach(function (leg) {
    if (leg.touch_high) hi = Math.max(hi, Number(leg.touch_high));
    if (leg.low) lo = Math.min(lo, Number(leg.low));
  });
  if (d.tp_price) {
    hi = Math.max(hi, Number(d.tp_price));
    lo = Math.min(lo, Number(d.tp_price));
  }
  // Levels are allowed to PULL the fit only as far as the price action itself.
  // A fib ladder extrapolates: L16 sits sixteen swing-spans below the swing
  // high, which on BANKNIFTY put it 3,600 points under anything that ever
  // traded and squeezed every candle into the top tenth of the pane. So a level
  // joins the fit only if it is already near the market; the deep ones are
  // still DRAWN, and come back into view on zoom-out because hline() skips
  // whatever is off-axis. Same rule CryptoForge's cascade chart has always
  // used -- fit the prices the market printed, never the extrapolation.
  var candleSpan = (hi - lo) || Math.max(Math.abs(hi) * 0.02, 1);
  var nearLo = lo - candleSpan * _PF_CHART_LEVEL_FIT_SPANS;
  var nearHi = hi + candleSpan * _PF_CHART_LEVEL_FIT_SPANS;
  (d.lines || []).forEach(function (line) {
    if (!line || line.price == null) return;
    var price = Number(line.price);
    if (!isFinite(price) || price < nearLo || price > nearHi) return;
    hi = Math.max(hi, price);
    lo = Math.min(lo, price);
  });
  var priceSpan = (hi - lo) || Math.max(Math.abs(hi) * 0.02, 1);
  var padP = priceSpan * 0.06;
  return { tMin: iMin, tMax: iMax, pMin: lo - padP, pMax: hi + padP };
}

// Only fields that can change Canvas pixels belong in this key. A refresh that
// merely changes table/status data can keep the already-painted surface; a new
// candle, level or marker asks the normal draw pipeline to repaint coherently.
function _pfChartCanvasPaintKey(d) {
  d = d || {};
  return JSON.stringify({
    candles: d.candles || [], mother: d.mother || null,
    legs: d.legs || [], trendlines: d.trendlines || [], lines: d.lines || [],
    fills: d.fills || [], entries: d.entries || [], exits: d.exits || [],
    avg_entry_price: d.avg_entry_price, tp_price: d.tp_price,
    tp_label: d.tp_label || '', timeframe: d.timeframe,
    indicators: d.indicators || null
  });
}

function _pfChartCanvasSameViewport(a, b) {
  return !!a && !!b && a.tMin === b.tMin && a.tMax === b.tMax && a.pMin === b.pMin && a.pMax === b.pMax;
}

// Captured before the chart API refresh begins. At the right edge means the
// view is following the latest bar; a panned-back research view is never
// allowed to jump just because a poll returned a newer payload.
function _pfChartCanvasRefreshState() {
  var c = _pfChartCanvas, fit = c && _pfChartCanvasFit(c);
  if (!c || !c.viewport || !fit) return null;
  var tolerance = Math.max(0.5, (c.viewport.tMax - c.viewport.tMin) * 0.001);
  return {
    viewport: { tMin: c.viewport.tMin, tMax: c.viewport.tMax, pMin: c.viewport.pMin, pMax: c.viewport.pMax },
    followRight: Math.abs(c.viewport.tMax - fit.tMax) <= tolerance
  };
}

// Update the existing two Canvas surfaces instead of destroying/recreating
// them. This retains pointer bindings and DPR backing stores, and it gives the
// renderer a chance to skip paint work when the chart-specific payload did not
// actually change.
function _pfChartCanvasRefresh(d, saved) {
  var c = _pfChartCanvas;
  if (!c || !c.host || !c.host.isConnected) return false;
  var oldKey = c.paintKey, oldViewport = c.viewport;
  c.data = d;
  var fit = _pfChartCanvasFit(c);
  if (!fit) return false;
  var next = saved && saved.viewport ? {
    tMin: saved.viewport.tMin, tMax: saved.viewport.tMax,
    pMin: saved.viewport.pMin, pMax: saved.viewport.pMax
  } : fit;
  if (saved && saved.followRight) {
    var span = saved.viewport.tMax - saved.viewport.tMin;
    next.tMax = fit.tMax;
    next.tMin = fit.tMax - span;
  }
  c.viewport = next;
  var nextKey = _pfChartCanvasPaintKey(d);
  // A changed viewport moves every projected pixel, so it must repaint as a
  // whole. With an unchanged viewport, this intentionally skips paint if none
  // of the independent draw layers received new data.
  if (!_pfChartCanvasSameViewport(oldViewport, next) || oldKey !== nextKey) _pfChartCanvasDraw();
  return true;
}

// xOf/yOf and their inverses are kept together and exposed on canvas state.
// Phase 3 uses the inverse pair for cursor-anchored zoom and the crosshair.
// The horizontal domain is bar indices; xOf accepts epoch seconds and converts
// through the axis, so every draw layer keeps passing timestamps unchanged.
function _pfChartCanvasProjection(c) {
  var v = c.viewport;
  if (!v) return null;
  var axis = _pfChartCanvasAxisOf(c);
  // Classic's 150/62/26/34 gutters live in a 1440×660 viewBox. Scale that
  // exact geometry to the real Canvas surface (rather than retaining 150 CSS
  // pixels at every width), so both engines put the same payload in the same
  // horizontal lane. The floors keep labels readable on a narrow screen.
  var padL = Math.max(86, 150 * c.w / 1440);
  var padR = Math.max(42, 62 * c.w / 1440);
  var padT = Math.max(18, 26 * c.h / 660);
  var padB = Math.max(26, 34 * c.h / 660);
  var plotW = Math.max(c.w - padL - padR, 1);
  var fullH = Math.max(c.h - padT - padB, 1);
  // A SUB-PANE IS OPT-IN. Reserving the band unconditionally would move every
  // pixel of the four charts that already exist; keyed off the payload, a chart
  // that sends no RSI is byte-identical to before this pane existed.
  var rsi = (c.data && c.data.indicators && c.data.indicators.rsi) || null;
  // A FLOOR WOULD EAT THE PRICE. `Math.max(46, ...)` took 46px of a 130px plot
  // in testing and left the candles 71px tall. The band is a SHARE of the
  // height, capped, and simply not drawn when the surface is too short for a
  // readable one -- the price pane always keeps at least ~three quarters.
  var rsiH = rsi && rsi.length && fullH >= 190 ? Math.min(120, fullH * 0.22) : 0;
  var rsiGap = rsiH ? Math.max(10, padT * 0.5) : 0;
  var plotH = Math.max(fullH - rsiH - rsiGap, 40);
  var tSpan = Math.max(v.tMax - v.tMin, 1);
  var pSpan = Math.max(v.pMax - v.pMin, Number.EPSILON);
  var p = {
    padL: padL, padR: padR, padT: padT, padB: padB, plotW: plotW, plotH: plotH,
    fontScale: Math.max(0.75, Math.min(1, c.w / 1440, c.h / 660)),
    xOf: function (t) { return padL + ((_pfChartCanvasIdxOf(axis, t) - v.tMin) / tSpan) * plotW; },
    yOf: function (price) { return padT + ((v.pMax - Number(price)) / pSpan) * plotH; },
    tAt: function (x) { return v.tMin + ((Number(x) - padL) / plotW) * tSpan; },
    timeAt: function (idx) { return _pfChartCanvasTimeAt(axis, idx); },
    pAt: function (y) { return v.pMax - ((Number(y) - padT) / plotH) * pSpan; },
    inPrice: function (price) { return Number(price) >= v.pMin && Number(price) <= v.pMax; },
    rsiH: rsiH, rsiGap: rsiGap,
    rsiTop: padT + plotH + rsiGap,
    // RSI is bounded 0-100 by construction, so this axis is fixed rather than
    // fitted -- a fitted one would silently rescale the 70/30 lines that are
    // the whole point of looking at it.
    rsiYOf: function (value) {
      return padT + plotH + rsiGap + (1 - Math.max(0, Math.min(100, Number(value))) / 100) * rsiH;
    }
  };
  c.projection = p;
  return p;
}

function _pfChartCanvasText(ctx, text, x, y, color, size, align, weight, scale) {
  ctx.fillStyle = color;
  ctx.font = (weight || '400') + ' ' + ((size || 10) * (scale || 1)) + 'px monospace';
  ctx.textAlign = align || 'left';
  ctx.textBaseline = 'middle';
  ctx.fillText(String(text), x, y);
}

function _pfChartCanvasClip(ctx, p, draw) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(p.padL, p.padT, p.plotW, p.plotH);
  ctx.clip();
  draw();
  ctx.restore();
}

function _pfChartCanvasGridAxes(c, p, PAL) {
  var ctx = c.ctx, d = c.data || {}, candles = d.candles || [];
  var labels = 0;
  ctx.lineWidth = 1;
  for (var g = 0; g <= 4; g++) {
    var price = c.viewport.pMin + (c.viewport.pMax - c.viewport.pMin) * (g / 4);
    var y = p.yOf(price);
    ctx.strokeStyle = PAL.grid;
    ctx.beginPath(); ctx.moveTo(p.padL, y); ctx.lineTo(p.padL + p.plotW, y); ctx.stroke();
    _pfChartCanvasText(ctx, Number(price).toLocaleString('en-US', { maximumFractionDigits: 2 }),
      p.padL + p.plotW + 6, y, PAL.axis, 9.5, 'left', null, p.fontScale);
    labels++;
  }
  // Ticks come from the candles actually inside the viewport — the window is
  // usually a clipped slice of the fetch, and a tick from outside it would be
  // painted into the gutters.
  var from = Math.max(Math.ceil(c.viewport.tMin), 0);
  var to = Math.min(Math.floor(c.viewport.tMax), candles.length - 1);
  if (to >= from) {
    var ticks = Math.min(6, to - from + 1);
    for (var i = 0; i < ticks; i++) {
      var ci = Math.round(from + (to - from) * (i / Math.max(ticks - 1, 1)));
      _pfChartCanvasText(ctx, _pfChartIst(candles[ci].t), p.xOf(candles[ci].t), c.h - 8,
        PAL.axis, 9.5, 'center', null, p.fontScale);
      labels++;
    }
  }
  return labels;
}

function _pfChartCanvasCandleWidth(d, p) {
  var candles = d.candles || [];
  if (candles.length < 2) return p.plotW;
  return Math.abs(p.xOf(candles[1].t) - p.xOf(candles[0].t));
}

function _pfChartCanvasMotherColumn(c, p, PAL) {
  var ctx = c.ctx, d = c.data || {}, bodyW = Math.max(Math.min(_pfChartCanvasCandleWidth(d, p) * 0.65, 9), 1);
  _pfChartCanvasClip(ctx, p, function () {
    (d.candles || []).forEach(function (bar) {
      if (!bar.is_mother) return;
      var x = p.xOf(bar.t);
      ctx.fillStyle = PAL.mother;
      ctx.globalAlpha = 0.09;
      ctx.fillRect(x - Math.max(bodyW, 6) / 2 - 3, p.padT + 1, Math.max(bodyW, 6) + 6, p.plotH - 2);
      ctx.globalAlpha = 1;
    });
  });
}

// Session-open gaps, drawn the way Phil's Pine reference draws them: a
// translucent synthetic candle spanning the previous close to the new open,
// sitting on the bar where the gap happened.  With the bar axis there is no
// blank space to look at, so this block is the only trace a gap leaves.
function _pfChartCanvasGapCandles(c, p, PAL) {
  var ctx = c.ctx, d = c.data || {}, candles = d.candles || [];
  var bodyW = Math.max(Math.min(_pfChartCanvasCandleWidth(d, p) * 0.65, 9), 1);
  var count = 0;
  _pfChartCanvasClip(ctx, p, function () {
    for (var i = 1; i < candles.length; i++) {
      var prev = candles[i - 1], bar = candles[i];
      // A session break is any pause much longer than one bar; intraday
      // consecutive NSE bars have no close->open gap to draw.
      if (Number(bar.t) - Number(prev.t) < 4 * 3600) continue;
      var prevClose = Number(prev.c), open = Number(bar.o);
      if (!isFinite(prevClose) || !isFinite(open) || prevClose === open) continue;
      var x = p.xOf(bar.t), up = open > prevClose;
      var top = p.yOf(Math.max(prevClose, open)), bottom = p.yOf(Math.min(prevClose, open));
      ctx.fillStyle = up ? PAL.up : PAL.down;
      ctx.globalAlpha = 0.22;
      ctx.fillRect(x - (bodyW + 4) / 2, top, bodyW + 4, Math.max(bottom - top, 1));
      ctx.globalAlpha = 0.6;
      ctx.strokeStyle = up ? PAL.up : PAL.down;
      ctx.lineWidth = 0.8;
      ctx.strokeRect(x - (bodyW + 4) / 2, top, bodyW + 4, Math.max(bottom - top, 1));
      ctx.globalAlpha = 1;
      count++;
    }
  });
  return count;
}

function _pfChartCanvasCandles(c, p, PAL) {
  var ctx = c.ctx, d = c.data || {}, bodyW = Math.max(Math.min(_pfChartCanvasCandleWidth(d, p) * 0.65, 9), 1);
  var count = 0;
  _pfChartCanvasClip(ctx, p, function () {
    (d.candles || []).forEach(function (bar) {
      var x = p.xOf(bar.t), up = Number(bar.c) >= Number(bar.o), color = up ? PAL.up : PAL.down;
      ctx.strokeStyle = color; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, p.yOf(bar.h)); ctx.lineTo(x, p.yOf(bar.l)); ctx.stroke();
      var top = p.yOf(Math.max(Number(bar.o), Number(bar.c)));
      var bottom = p.yOf(Math.min(Number(bar.o), Number(bar.c)));
      ctx.fillStyle = color;
      ctx.fillRect(x - bodyW / 2, top, bodyW, Math.max(bottom - top, 1));
      if (bar.is_mother) {
        ctx.strokeStyle = PAL.mother; ctx.lineWidth = 1.4;
        ctx.strokeRect(x - bodyW / 2 - 1, p.yOf(bar.h) - 1, bodyW + 2,
          Math.max(p.yOf(bar.l) - p.yOf(bar.h) + 2, 4));
        _pfChartCanvasText(ctx, 'MC', x, Math.max(p.yOf(bar.h) - 8, p.padT + 10), PAL.mother, 9.5, 'center', '700', p.fontScale);
      }
      count++;
    });
  });
  return count;
}

function _pfChartCanvasTrendlines(c, p, PAL, labels) {
  var d = c.data || {}, ctx = c.ctx, drawn = 0;
  _pfChartCanvasClip(ctx, p, function () {
    _pfChartCanvasStructures(d).trendlines.forEach(function (tl) {
      var a1 = tl.a1, a2 = tl.a2;
      if (!a1 || !a2 || Number(a2.t) === Number(a1.t)) return;
      // The line is straight through its two anchors in BAR space (the same
      // convention TradingView extends drawings with), so it is extended in
      // pixels rather than re-derived from a wall-clock slope.
      var x1 = p.xOf(a1.t), y1 = p.yOf(a1.p);
      var x2 = p.xOf(a2.t), y2 = p.yOf(a2.p);
      if (x2 === x1) return;
      var slope = (y2 - y1) / (x2 - x1);
      // FROM the first anchor, not through it. Every trendline on this site is
      // carried down from a mother candle's high; drawing it back past the
      // mother put a line over bars that existed before the structure did
      // (Phil, 2026-08-18: "just start from that mother candle high"). It still
      // extends to the right edge -- a standing line is live until broken.
      var xLeft = Math.max(p.padL, Math.min(x1, x2)), xRight = p.padL + p.plotW;
      var yLeft = y1 + slope * (xLeft - x1), yRight = y1 + slope * (xRight - x1);
      var color = PAL.fibs[(Math.max(1, Number(tl.id) || 1) - 1) % PAL.fibs.length];
      var noFib = tl.bears_fib === false;
      ctx.strokeStyle = color; ctx.lineWidth = tl.active ? 1.3 : 0.9;
      ctx.globalAlpha = noFib ? 0.35 : (tl.active ? 0.95 : 0.5);
      ctx.setLineDash(noFib ? [6, 4] : []);
      ctx.beginPath(); ctx.moveTo(xLeft, yLeft); ctx.lineTo(xRight, yRight); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
      if (yRight >= p.padT && yRight <= p.padT + p.plotH) labels.push({ kind: 'right', x: xRight - 4, y: yRight - 5,
        text: 'TL' + tl.id + (noFib ? ' (no fib)' : (tl.active ? ' ★' : '')), color: color });
      drawn++;
    });
  });
  return drawn;
}

function _pfChartCanvasHline(c, p, labels, price, color, text, dash, width, opacity) {
  if (!p.inPrice(price)) return false;
  var ctx = c.ctx, y = p.yOf(price);
  _pfChartCanvasClip(ctx, p, function () {
    ctx.strokeStyle = color; ctx.lineWidth = width || 0.9; ctx.globalAlpha = opacity || 1;
    ctx.setLineDash(dash || []);
    ctx.beginPath(); ctx.moveTo(p.padL, y); ctx.lineTo(p.padL + p.plotW, y); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha = 1;
  });
  if (text) labels.push({ kind: 'gutter', y: y, text: text, color: color });
  return true;
}

// Plain labelled price lines, for strategies that have no fib ladder to draw.
// The two-red ladder's rungs are buy-stops read off candle closes, not levels
// measured from a mother — same picture, different arithmetic behind it.
function _pfChartCanvasLines(c, p, PAL, labels) {
  var d = c.data || {}, count = 0;
  (d.lines || []).forEach(function (line, index) {
    if (line == null || line.price == null) return;
    // A line may name its own colour/dash (CPR, R1-R4, S1-S5, the dotted LIVE
    // line). Literal hex only -- Canvas silently ignores var(--x). Lines that
    // name nothing keep the old palette-by-index behaviour.
    var color = _pfChartSeriesColor(line.color, PAL, PAL.fibs[index % PAL.fibs.length]);
    var spent = Number(line.inr_notional) || 0;
    // A line with NO label is drawn silent: the line only, no price in the
    // gutter. Under "where two meet" the fib levels are ghosted structure
    // behind the zones, and printing "(24,093.15)" for every one of them put
    // 35 numbers down the left edge over two zones that mattered.
    var text = line.label === '' && spent <= 0
      ? ''
      : String(line.label || '') + ' (' + Number(line.price).toLocaleString('en-US', { maximumFractionDigits: 2 }) + ')'
        + (spent > 0 ? '  ' + _pfChartInr(spent) : '');
    var dash = Array.isArray(line.dash) ? line.dash : (line.filled ? [] : [4, 3]);
    var width = Number(line.width) || (line.filled ? 1.1 : 0.8);
    var opacity = Number(line.opacity) || (line.filled ? 0.9 : 0.45);
    count += _pfChartCanvasHline(c, p, labels, Number(line.price), color, text, dash, width, opacity) ? 1 : 0;
  });
  return count;
}

// Indicator polylines -- the 20-EMA and anything else that is a running series
// rather than a level. overlays: [{label, color, dash, width, points:[{t,price}]}]
function _pfChartCanvasOverlays(c, p, PAL, labels) {
  var d = c.data || {}, ctx = c.ctx, count = 0;
  (d.overlays || []).forEach(function (ov) {
    var pts = (ov && ov.points || []).filter(function (pt) { return pt && pt.t != null && pt.price != null; });
    if (pts.length < 2) return;
    var color = _pfChartSeriesColor(ov.color, PAL, PAL.avg);
    _pfChartCanvasClip(ctx, p, function () {
      ctx.strokeStyle = color;
      ctx.lineWidth = Number(ov.width) || 1.3;
      ctx.setLineDash(Array.isArray(ov.dash) ? ov.dash : []);
      ctx.globalAlpha = Number(ov.opacity) || 0.9;
      ctx.beginPath();
      pts.forEach(function (pt, i) {
        var x = p.xOf(pt.t), y = p.yOf(pt.price);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
    });
    var last = pts[pts.length - 1];
    if (ov.label && p.inPrice(last.price)) labels.push({ kind: 'gutter', y: p.yOf(last.price), text: String(ov.label), color: color });
    count += 1;
  });
  return count;
}

function _pfChartCanvasFibs(c, p, PAL, labels) {
  var d = c.data || {}, count = 0;
  function fmt(v) { return Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 }); }
  if (d.mother && d.mother.high) count += _pfChartCanvasHline(c, p, labels, Number(d.mother.high), PAL.mother,
    'MOTHER (' + fmt(d.mother.high) + ')', [5, 3], 1.1) ? 1 : 0;
  // The low half of the mother band. Every caller already sends it and the
  // renderer used to drop it on the floor, which on a fib chart hides the
  // measuring stick the whole ladder is stepped off.
  if (d.mother && d.mother.low) count += _pfChartCanvasHline(c, p, labels, Number(d.mother.low), PAL.mother,
    'MOTHER LOW (' + fmt(d.mother.low) + ')', [5, 3], 1.1, 0.7) ? 1 : 0;
  count += _pfChartCanvasLines(c, p, PAL, labels);
  _pfChartCanvasStructures(d).legs.forEach(function (leg) {
    var color = PAL.fibs[(Math.max(1, Number(leg.leg_id) || 1) - 1) % PAL.fibs.length];
    count += _pfChartCanvasHline(c, p, labels, Number(leg.touch_high), color,
      '0 (' + fmt(leg.touch_high) + ')', [], 0.8, 0.4) ? 1 : 0;
    count += _pfChartCanvasHline(c, p, labels, Number(leg.low), color,
      '1 (' + fmt(leg.low) + ')', [], 0.8, 0.4) ? 1 : 0;
    [2, 4, 8].forEach(function (level) {
      var price = leg.levels ? leg.levels[String(level)] : null;
      if (price == null) return;
      var order = (leg.orders || []).find(function (o) { return o.level === level; }) || {};
      var spent = Number(order.inr_notional) || 0;
      count += _pfChartCanvasHline(c, p, labels, Number(price), color,
        level + ' (' + fmt(price) + ')' + (spent > 0 ? '  ' + _pfChartInr(spent) : ''), [], 1.1, 0.9) ? 1 : 0;
    });
    if (leg.touch_timestamp && p.inPrice(leg.touch_high)) {
      var ctx = c.ctx;
      _pfChartCanvasClip(ctx, p, function () {
        ctx.strokeStyle = color; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(p.xOf(leg.touch_timestamp), p.yOf(leg.touch_high), 3.5, 0, Math.PI * 2); ctx.stroke();
      });
    }
  });
  // Upstream labelled this line "SOLD AT" whenever the campaign had ended, which
  // is only true when the target was the thing that ended it. A mother held to
  // expiry never traded here, so the caller names the line and this draws it.
  var hit = String(d.tp_label || '') === 'TARGET HIT';
  if (d.tp_price) count += _pfChartCanvasHline(c, p, labels, Number(d.tp_price), hit ? PAL.sellMark : PAL.tp,
    (d.tp_label || 'TARGET') + ' (' + fmt(d.tp_price) + ')', [6, 3], 1.2) ? 1 : 0;
  if (d.avg_entry_price) count += _pfChartCanvasHline(c, p, labels, Number(d.avg_entry_price), PAL.avg,
    'AVG ENTRY (' + fmt(d.avg_entry_price) + ')', [4, 4], 1.1) ? 1 : 0;
  return count;
}

function _pfChartCanvasMarkers(c, p, PAL, labels) {
  var d = c.data || {}, ctx = c.ctx, count = 0;
  var entries = (d.entries && d.entries.length) ? d.entries : (d.fills || []).map(function (fill) {
    return { t: fill && fill.timestamp, price: fill && fill.price };
  });
  _pfChartCanvasClip(ctx, p, function () {
    entries.forEach(function (fill) {
      if (!fill || !fill.price || !p.inPrice(fill.price)) return;
      var x = p.xOf(fill.t), y = p.yOf(fill.price) + 10;
      ctx.fillStyle = PAL.buyMark; ctx.strokeStyle = PAL.markRing; ctx.lineWidth = 0.9;
      ctx.beginPath(); ctx.moveTo(x, y - 9); ctx.lineTo(x - 5, y); ctx.lineTo(x - 2, y); ctx.lineTo(x - 2, y + 6);
      ctx.lineTo(x + 2, y + 6); ctx.lineTo(x + 2, y); ctx.lineTo(x + 5, y); ctx.closePath(); ctx.fill(); ctx.stroke();
      count++;
    });
    (d.exits || []).forEach(function (exit) {
      if (!exit || !exit.price || !p.inPrice(exit.price)) return;
      var x = p.xOf(exit.t), y = p.yOf(exit.price) - 10;
      ctx.fillStyle = PAL.sellMark; ctx.strokeStyle = PAL.markRing; ctx.lineWidth = 0.9;
      ctx.beginPath(); ctx.moveTo(x, y + 9); ctx.lineTo(x - 5, y); ctx.lineTo(x - 2, y); ctx.lineTo(x - 2, y - 6);
      ctx.lineTo(x + 2, y - 6); ctx.lineTo(x + 2, y); ctx.lineTo(x + 5, y); ctx.closePath(); ctx.fill(); ctx.stroke();
      // A null P&L is UNKNOWN, not zero. `Number(x) || 0` printed "+Rs 0" on
      // every unpriced exit; a real 0 still shows, an absent one says nothing.
      var known = exit.pnl !== null && exit.pnl !== undefined && isFinite(Number(exit.pnl));
      var pnl = Number(exit.pnl);
      labels.push({ kind: 'marker', x: x, y: y - 9, text: 'SELL ' + Number(exit.price).toLocaleString('en-US', { maximumFractionDigits: 2 })
        + (known ? '  ' + (pnl >= 0 ? '+' : '−') + _pfChartInr(Math.abs(pnl)) : '  unpriced'), color: PAL.sellMark });
      count++;
    });

    // THE RULE THAT ENDED THE TRADE, marked in TIME.
    // Phil, 2026-09-03: "I need the super trend indicator that cuts today's
    // trade to an exit.. I want that in the chart as well". CE_SL15 exits on
    // Supertrend(10, 2.7) over 3m NIFTY -- it turned down at 10:36 and the
    // engine acted at the next 5m boundary, 10:45.
    //
    // A VERTICAL LINE, NOT A PRICE LINE. This chart's candles are the OPTION's
    // premium and the supertrend is an INDEX level: on an axis running 250-300
    // a value of 23,996 is off-screen at best, and read as a premium at worst.
    // The two series share a time axis and nothing else, so time is what is
    // drawn -- which is also the only part that matters: WHEN it turned.
    var st = d.supertrend;
    ((st && Array.isArray(st.flips)) ? st.flips : []).forEach(function (flip) {
      if (!flip || flip.t == null) return;
      var x = p.xOf(flip.t);
      if (!isFinite(x) || x < p.padL || x > p.padL + p.plotW) return;
      var down = String(flip.dir) === 'down';
      ctx.save();
      ctx.strokeStyle = down ? PAL.sellMark : PAL.buyMark;
      ctx.globalAlpha = 0.55;
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(x, p.padT);
      ctx.lineTo(x, p.padT + p.plotH);
      ctx.stroke();
      ctx.restore();
      labels.push({
        kind: 'marker',
        x: x,
        y: p.padT + (down ? 12 : 26),
        text: 'ST ' + (down ? 'DOWN' : 'UP') + ' ' + _pfChartIst(flip.t).slice(6),
        color: down ? PAL.sellMark : PAL.buyMark,
      });
    });
  });
  return count;
}

function _pfChartCanvasLabels(c, p, labels) {
  var ctx = c.ctx, slots = [], count = 0;
  labels.forEach(function (label) {
    if (label.kind === 'gutter') {
      var y = label.y;
      // Do not "clean this up" into an exact 10px shift. The 0.5px overshoot
      // is the Classic ETH-freeze fix: exact 10 can remain 9.9999999998 apart
      // in floating point and loop forever in the next collision check.
      for (var pass = 0, moved = true; moved && pass <= slots.length; pass++) {
        moved = false;
        for (var k = 0; k < slots.length; k++) {
          if (Math.abs(slots[k] - y) < 10) { y = slots[k] + 10.5; moved = true; break; }
        }
      }
      slots.push(y);
      // Gutter labels are right-aligned against the price lane, so a long one
      // ("TARGET (not reached) (24,412.7)") runs off the left edge and loses its
      // first word. When it cannot fit the gutter, start it just inside the plot
      // instead: overlapping a candle is recoverable, a cut-off label is not.
      ctx.font = '400 ' + (10 * (p.fontScale || 1)) + 'px monospace';
      var fits = ctx.measureText(String(label.text)).width <= p.padL - 8;
      _pfChartCanvasText(ctx, label.text, fits ? p.padL - 6 : p.padL + 6, y + 3,
        label.color, 10, fits ? 'right' : 'left', null, p.fontScale);
    } else if (label.kind === 'right') {
      _pfChartCanvasText(ctx, label.text, label.x, label.y, label.color, 9.5, 'right', null, p.fontScale);
    } else {
      _pfChartCanvasText(ctx, label.text, label.x, label.y, label.color, 9.5, 'center', null, p.fontScale);
    }
    count++;
  });
  return count;
}

/* ── Indicator layers ────────────────────────────────────────────────────
 *
 * Added 2026-08-24 for Gap Carry, whose rule IS an EMA and an RSI: a chart of
 * that rule without them shows the night's candle and no reason for it.
 *
 * Both layers are driven by `payload.indicators`:
 *   { ema: [{t, v}], ema_period: 20,
 *     rsi: [{t, v}], rsi_period: 14, rsi_upper: 70, rsi_lower: 30 }
 * `t` is epoch SECONDS like every other timestamp here. A payload without
 * `indicators` draws nothing and reserves no space, which is why the four
 * charts that predate this are unaffected.
 */

// An EMA is a CURVE, so it cannot be expressed as a line/trendline: `lines`
// are horizontal and a trendline has exactly two anchors. Hence a real path.
function _pfChartCanvasIndicators(c, p, PAL, labels) {
  var ind = (c.data || {}).indicators;
  // EMA or SUPERTREND -- both are curves and draw identically. Supertrend has
  // sent its series as `indicators.supertrend` since the console was built,
  // and this only ever looked at `ema`, so the one strategy NAMED after an
  // indicator was the one chart that never showed it (Phil, 2026-09-01).
  var series = (ind && (ind.ema || ind.supertrend)) || null;
  if (!series || !series.length) return 0;
  var ctx = c.ctx, drawn = 0, last = null;
  _pfChartCanvasClip(ctx, p, function () {
    ctx.strokeStyle = PAL.ema;
    ctx.lineWidth = 1.6;
    ctx.setLineDash([]);          // Phil's standard: no dashed indicator lines.
    ctx.lineJoin = 'round';
    ctx.beginPath();
    var started = false;
    for (var i = 0; i < series.length; i++) {
      var point = series[i];
      if (!point || point.v === null || point.v === undefined || !isFinite(Number(point.v))) continue;
      var x = p.xOf(point.t), y = p.yOf(point.v);
      if (!isFinite(x) || !isFinite(y)) continue;
      if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true; }
      drawn++;
      last = { x: x, y: y, v: point.v };
    }
    if (started) ctx.stroke();
  });
  if (last) {
    labels.push({
      y: last.y, text: 'EMA' + (ind.ema_period || 20) + ' ' + Math.round(Number(last.v)),
      color: PAL.ema, align: 'right'
    });
  }
  return drawn;
}

// Its own pane under the price, on a FIXED 0-100 axis with the rule's two
// thresholds drawn: the whole reason to look at it is where 70 and 30 sit.
function _pfChartCanvasRsiPane(c, p, PAL) {
  var ind = (c.data || {}).indicators;
  var series = ind && ind.rsi;
  if (!series || !series.length || !p.rsiH) return 0;
  var ctx = c.ctx, scale = p.fontScale;
  var upper = Number(ind.rsi_upper);
  var lower = Number(ind.rsi_lower);
  if (!isFinite(upper)) upper = 70;
  if (!isFinite(lower)) lower = 30;

  // The band between the two thresholds is the "nothing happens" zone.
  ctx.fillStyle = PAL.rsiBand;
  ctx.fillRect(p.padL, p.rsiYOf(upper), p.plotW, Math.max(1, p.rsiYOf(lower) - p.rsiYOf(upper)));

  ctx.strokeStyle = PAL.rsiGuide;
  ctx.lineWidth = 1;
  ctx.setLineDash([]);
  [upper, 50, lower].forEach(function (level) {
    var y = p.rsiYOf(level);
    ctx.beginPath();
    ctx.moveTo(p.padL, y);
    ctx.lineTo(p.padL + p.plotW, y);
    ctx.stroke();
    _pfChartCanvasText(ctx, String(Math.round(level)), p.padL - 6, y, PAL.axis, 9, 'right', '400', scale);
  });

  ctx.save();
  ctx.beginPath();
  ctx.rect(p.padL, p.rsiTop, p.plotW, p.rsiH);
  ctx.clip();
  ctx.strokeStyle = PAL.rsi;
  ctx.lineWidth = 1.6;
  ctx.lineJoin = 'round';
  ctx.beginPath();
  var started = false, drawn = 0;
  for (var i = 0; i < series.length; i++) {
    var point = series[i];
    if (!point || point.v === null || point.v === undefined || !isFinite(Number(point.v))) continue;
    var x = p.xOf(point.t), y = p.rsiYOf(point.v);
    if (!isFinite(x) || !isFinite(y)) continue;
    if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true; }
    drawn++;
  }
  if (started) ctx.stroke();
  ctx.restore();

  _pfChartCanvasText(ctx, 'RSI' + (ind.rsi_period || 14), p.padL + 6, p.rsiTop + 8 * scale, PAL.rsi, 9, 'left', '700', scale);
  return drawn;
}

function _pfChartCanvasDraw() {
  var c = _pfChartCanvas;
  if (!c || !c.ctx) return;
  var d = c.data || {}, candles = d.candles || [];
  var ctx = c.ctx, PAL = _pfChartPalette();
  ctx.clearRect(0, 0, c.w, c.h);
  if (!candles.length) return;
  if (!c.viewport) c.viewport = _pfChartCanvasFit(c);
  var p = _pfChartCanvasProjection(c);
  if (!p) return;
  var labels = [];
  // Keep these independently callable layers in this exact order. Later live
  // refreshes can redraw only changed layers without changing the model.
  var axisLabelCount = _pfChartCanvasGridAxes(c, p, PAL);
  _pfChartCanvasMotherColumn(c, p, PAL);
  var gapCount = _pfChartCanvasGapCandles(c, p, PAL);
  var candleCount = _pfChartCanvasCandles(c, p, PAL);
  var trendlineCount = _pfChartCanvasTrendlines(c, p, PAL, labels);
  var fibCount = _pfChartCanvasFibs(c, p, PAL, labels);
  var overlayCount = _pfChartCanvasOverlays(c, p, PAL, labels);
  var emaCount = _pfChartCanvasIndicators(c, p, PAL, labels);
  var rsiCount = _pfChartCanvasRsiPane(c, p, PAL);
  var markerCount = _pfChartCanvasMarkers(c, p, PAL, labels);
  var labelCount = _pfChartCanvasLabels(c, p, labels);
  // E2E reads this small semantic paint record in addition to real pixels. It
  // is a test seam, not payload state, and catches a canvas that draws a frame
  // but silently loses candles/labels/geometry.
  c.paint = {
    candles: candleCount, trendlines: trendlineCount, fibs: fibCount, markers: markerCount,
    overlays: overlayCount, ema: emaCount, rsi: rsiCount,
    gaps: gapCount, labels: axisLabelCount + labelCount,
    labelTexts: labels.map(function (label) { return label.text; }),
    theme: document.documentElement.getAttribute('data-theme') || 'auto'
  };
  c.paintKey = _pfChartCanvasPaintKey(d);
}

function _pfChartCanvasPoint(c, event) {
  var p = c && c.projection;
  if (!p) return null;
  var box = c.host.getBoundingClientRect();
  if (!box.width || !box.height) return null;
  var x = event.clientX - box.left, y = event.clientY - box.top;
  return {
    x: x, y: y,
    plot: x >= p.padL && x <= p.padL + p.plotW && y >= p.padT && y <= p.padT + p.plotH,
    priceAxis: x > p.padL + p.plotW && y >= p.padT && y <= p.padT + p.plotH,
    timeAxis: y > p.padT + p.plotH && x >= p.padL && x <= p.padL + p.plotW
  };
}

function _pfChartCanvasSetViewport(c, next) {
  if (!c || !next) return;
  var v = c.viewport || {};
  var tSpan = Number(next.tMax) - Number(next.tMin);
  var pSpan = Number(next.pMax) - Number(next.pMin);
  if (!isFinite(tSpan) || !isFinite(pSpan) || tSpan <= 0 || pSpan <= 0) return;
  c.viewport = { tMin: Number(next.tMin), tMax: Number(next.tMax), pMin: Number(next.pMin), pMax: Number(next.pMax) };
  // Every readout on the page shows the SAME number: the Test Bench toolbar's
  // (by id), the host cluster's, and any chart-strip's. Updating only the id
  // left the dialog's own readout frozen at 100% while the chart zoomed.
  var fit = _pfChartCanvasFit(c);
  if (fit) {
    var pct = Math.round((fit.tMax - fit.tMin) / tSpan * 100) + '%';
    var legacy = document.getElementById('pf-bench-zoom-level');
    if (legacy) legacy.textContent = pct;
    document.querySelectorAll('[data-pf-zoom-readout], [data-bench-zoom-level]')
      .forEach(function (node) { node.textContent = pct; });
  }
  _pfChartCanvasDraw();
}

function _pfChartCanvasZoom(factor, reset, anchor) {
  var c = _pfChartCanvas;
  if (!c || !c.viewport) return;
  if (reset || factor === 0) {
    _pfChartCanvasSetViewport(c, _pfChartCanvasFit(c));
    return;
  }
  var step = Number(factor);
  if (!isFinite(step) || step <= 0) return;
  var p = c.projection || _pfChartCanvasProjection(c);
  if (!p) return;
  var oldSpan = c.viewport.tMax - c.viewport.tMin;
  var newSpan = Math.max(0.5, Math.min(oldSpan * 60, oldSpan * step));
  var x = anchor && isFinite(anchor.x) ? anchor.x : p.padL + p.plotW / 2;
  var at = p.tAt(x), ratio = (at - c.viewport.tMin) / oldSpan;
  _pfChartCanvasSetViewport(c, {
    tMin: at - ratio * newSpan,
    tMax: at + (1 - ratio) * newSpan,
    pMin: c.viewport.pMin, pMax: c.viewport.pMax
  });
}

function _pfChartCanvasDrawCrosshair(c, point) {
  var ctx = c.octx, p = c.projection;
  if (!ctx || !p) return;
  ctx.clearRect(0, 0, c.w, c.h);
  if (!point || !point.plot) return;
  var PAL = _pfChartPalette(), price = p.pAt(point.y), when = p.tAt(point.x);
  ctx.save();
  ctx.strokeStyle = PAL.axis; ctx.lineWidth = 0.7; ctx.globalAlpha = 0.8; ctx.setLineDash([4, 3]);
  ctx.beginPath(); ctx.moveTo(p.padL, point.y); ctx.lineTo(p.padL + p.plotW, point.y); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(point.x, p.padT); ctx.lineTo(point.x, p.padT + p.plotH); ctx.stroke();
  ctx.setLineDash([]); ctx.globalAlpha = 1;
  var priceText = Number(price).toLocaleString('en-US', { maximumFractionDigits: 2 });
  ctx.font = '9.5px monospace';
  var priceW = Math.max(ctx.measureText(priceText).width + 10, 42), timeText = _pfChartIst(Math.round(p.timeAt(when))) + ' IST';
  var timeW = Math.max(ctx.measureText(timeText).width + 10, 84);
  ctx.fillStyle = PAL.axis;
  ctx.fillRect(p.padL + p.plotW + 2, point.y - 8, priceW, 16);
  ctx.fillRect(point.x - timeW / 2, c.h - 20, timeW, 15);
  _pfChartCanvasText(ctx, priceText, p.padL + p.plotW + 6, point.y + 0.5, PAL.fillRing, 9.5, 'left');
  _pfChartCanvasText(ctx, timeText, point.x, c.h - 12, PAL.fillRing, 9, 'center');
  ctx.restore();
}

function _pfChartCanvasClearCrosshair(c) {
  if (c && c.octx) c.octx.clearRect(0, 0, c.w, c.h);
}

// Phase 3 interaction model. Plot drag pans both axes; the axis gutters are
// deliberately separate so price stretching can never move time and vice versa.
function _pfChartCanvasBindInteraction(c) {
  if (!c || !c.host) return;
  function bind(name, fn, opts) {
    c.host.addEventListener(name, fn, opts);
    c.handlers.push([name, fn, opts]);
  }
  // Two-finger pinch. Pointer events deliver each finger separately, so
  // without this map a second touch simply restarted the one-pointer drag and
  // the chart jittered instead of zooming — Phil's phones could never zoom.
  c.touches = new Map();
  c.pinch = null;
  function pinchGeom() {
    var pts = Array.from(c.touches.values());
    return {
      mx: (pts[0].x + pts[1].x) / 2, my: (pts[0].y + pts[1].y) / 2,
      dx: Math.abs(pts[0].x - pts[1].x), dy: Math.abs(pts[0].y - pts[1].y),
      d: Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y)
    };
  }
  function beginPinch() {
    var v = c.viewport, p = c.projection;
    if (!v || !p || c.touches.size < 2) return;
    var g = pinchGeom();
    c.drag = null;
    c.host.style.cursor = '';
    c.pinch = {
      start: g,
      viewport: { tMin: v.tMin, tMax: v.tMax, pMin: v.pMin, pMax: v.pMax },
      // A clearly vertical pinch stretches price; anything else zooms time.
      vertical: g.dy >= 40 && g.dy > 1.5 * g.dx,
      anchorTime: p.tAt(g.mx), anchorPrice: p.pAt(g.my)
    };
    _pfChartCanvasClearCrosshair(c);
  }
  function movePinch() {
    var pin = c.pinch, p = c.projection;
    if (!pin || !p || c.touches.size < 2) return;
    var g = pinchGeom(), s = pin.start, v0 = pin.viewport;
    var tSpan = v0.tMax - v0.tMin, pSpan = v0.pMax - v0.pMin;
    if (pin.vertical) { if (g.dy >= 12) pSpan = pSpan * s.dy / g.dy; }
    else if (g.d >= 12) tSpan = Math.max(0.5, tSpan * s.d / g.d);
    // The data point under the fingers' first midpoint stays under the moving
    // midpoint, so one gesture both zooms and pans.
    var tMin = pin.anchorTime - ((g.mx - p.padL) / p.plotW) * tSpan;
    var pMax = pin.anchorPrice + ((g.my - p.padT) / p.plotH) * pSpan;
    _pfChartCanvasSetViewport(c, { tMin: tMin, tMax: tMin + tSpan, pMin: pMax - pSpan, pMax: pMax });
  }
  function endDrag(event) {
    if (event) { try { c.host.releasePointerCapture(event.pointerId); } catch (err) {} }
    if (event && event.pointerType === 'touch') {
      c.touches.delete(event.pointerId);
      _pfChartCanvasClearCrosshair(c);
      if (c.pinch) {
        if (c.touches.size >= 2) { beginPinch(); return; }
        c.pinch = null;
        if (c.touches.size === 1 && c.viewport) {
          // The finger that stayed down keeps panning without a re-touch.
          var rest = c.touches.values().next().value, v = c.viewport;
          c.drag = { kind: 'pan', x: rest.x, y: rest.y, viewport: { tMin: v.tMin, tMax: v.tMax, pMin: v.pMin, pMax: v.pMax } };
          return;
        }
      }
      // A finger is still down (its pan is live); only a fully lifted
      // gesture may fall through and clear the drag.
      if (c.touches.size) return;
    }
    if (!c.drag) return;
    c.drag = null;
    c.host.style.cursor = '';
  }
  bind('pointerdown', function (event) {
    var point = _pfChartCanvasPoint(c, event), v = c.viewport;
    if (event.pointerType === 'touch' && point) {
      c.touches.set(event.pointerId, { x: point.x, y: point.y });
      try { c.host.setPointerCapture(event.pointerId); } catch (err) {}
      if (c.touches.size === 2) { beginPinch(); return; }
      if (c.touches.size > 2) return;
    }
    if (!point || !v || (!point.plot && !point.priceAxis && !point.timeAxis)) return;
    c.drag = {
      kind: point.plot ? 'pan' : (point.priceAxis ? 'price' : 'time'),
      x: point.x, y: point.y,
      viewport: { tMin: v.tMin, tMax: v.tMax, pMin: v.pMin, pMax: v.pMax },
      anchorTime: c.projection.tAt(point.x), anchorPrice: c.projection.pAt(point.y)
    };
    c.host.style.cursor = point.plot ? 'grabbing' : 'ns-resize';
    if (point.timeAxis) c.host.style.cursor = 'ew-resize';
    try { c.host.setPointerCapture(event.pointerId); } catch (err) {}
    _pfChartCanvasClearCrosshair(c);
  });
  bind('pointermove', function (event) {
    var point = _pfChartCanvasPoint(c, event);
    if (!point) return;
    if (event.pointerType === 'touch' && c.touches.has(event.pointerId)) {
      c.touches.set(event.pointerId, { x: point.x, y: point.y });
      if (c.pinch) { movePinch(); return; }
    }
    if (!c.drag) {
      c.host.style.cursor = point.plot ? 'crosshair' : (point.priceAxis ? 'ns-resize' : (point.timeAxis ? 'ew-resize' : ''));
      _pfChartCanvasDrawCrosshair(c, point);
      return;
    }
    var drag = c.drag, start = drag.viewport, p = c.projection;
    var tSpan = start.tMax - start.tMin, pSpan = start.pMax - start.pMin;
    if (drag.kind === 'pan') {
      var dt = -(point.x - drag.x) / p.plotW * tSpan;
      var dp = (point.y - drag.y) / p.plotH * pSpan;
      _pfChartCanvasSetViewport(c, { tMin: start.tMin + dt, tMax: start.tMax + dt, pMin: start.pMin + dp, pMax: start.pMax + dp });
    } else if (drag.kind === 'price') {
      // Pulling the price axis upward zooms in (a tighter price range); pulling
      // it downward zooms out. This is intentionally opposite to screen Y,
      // which increases downward.
      var priceSpan = pSpan * Math.exp((point.y - drag.y) / 160);
      var priceRatio = (drag.anchorPrice - start.pMin) / pSpan;
      _pfChartCanvasSetViewport(c, {
        tMin: start.tMin, tMax: start.tMax,
        pMin: drag.anchorPrice - priceRatio * priceSpan,
        pMax: drag.anchorPrice + (1 - priceRatio) * priceSpan
      });
    } else {
      var timeSpan = Math.max(0.5, tSpan * Math.exp((drag.x - point.x) / 160));
      var timeRatio = (drag.anchorTime - start.tMin) / tSpan;
      _pfChartCanvasSetViewport(c, {
        tMin: drag.anchorTime - timeRatio * timeSpan,
        tMax: drag.anchorTime + (1 - timeRatio) * timeSpan,
        pMin: start.pMin, pMax: start.pMax
      });
    }
  });
  bind('pointerup', endDrag);
  bind('pointercancel', endDrag);
  bind('pointerleave', function () { if (!c.drag) _pfChartCanvasClearCrosshair(c); });
  bind('wheel', function (event) {
    var point = _pfChartCanvasPoint(c, event);
    if (!point || !point.plot) return;
    event.preventDefault();
    // Time only: vertical scale is reserved for deliberate price-axis drags.
    _pfChartCanvasZoom(event.deltaY < 0 ? 1 / 1.08 : 1.08, false, point);
  }, { passive: false });
  bind('dblclick', function (event) {
    var point = _pfChartCanvasPoint(c, event), fit = _pfChartCanvasFit(c);
    if (!point || !fit || !c.viewport) return;
    if (point.priceAxis) _pfChartCanvasSetViewport(c, { tMin: c.viewport.tMin, tMax: c.viewport.tMax, pMin: fit.pMin, pMax: fit.pMax });
    else if (point.timeAxis) _pfChartCanvasSetViewport(c, { tMin: fit.tMin, tMax: fit.tMax, pMin: c.viewport.pMin, pMax: c.viewport.pMax });
    else if (point.plot) _pfChartCanvasSetViewport(c, fit);
  });
}


// ── The Test Bench's one entry point ─────────────────────────
// Render into whichever container the page hands us. Mounting is idempotent:
// a second run with a fresh result repaints the same surfaces rather than
// leaving a detached canvas and its observers behind.
function pfBenchDrawChart(container, payload) {
  if (!container) return false;
  if (!payload || !(payload.candles || []).length) {
    _pfChartCanvasTeardown();
    container.innerHTML = '<p class="pf-bench-empty">No candles to draw.</p>';
    return false;
  }
  container.innerHTML = _pfBenchChartHostHtml();
  // Pass the container so the mount addresses THIS chart's surfaces, not
  // whichever host happens to come first in the document.
  _pfChartCanvasMount(payload, container);
  return true;
}

// ── The one chart header strip ───────────────────────────────────────
// Phil, 2026-08-13: "Change or modify all the charts in the site to this
// specifications for the header strip" — the Campaign Chart's row of
// timeframes, − zoom% +, refresh and close, on every canvas. This builds
// that strip once so a caller cannot drift from the standard: it owns only
// the callbacks (what a timeframe change or refresh MEANS for its data).
//
// pfChartStrip(host, {
//   timeframes: ['5m','15m','1h'],   // omit for a chart with one timeframe
//   active: '5m',
//   onTimeframe: function (tf) {},
//   onRefresh: function () {},       // omit to hide the refresh button
//   onClose: function () {},         // omit to hide the close button
// })
// Zoom buttons drive the mounted canvas directly; the % readout is the
// fitted span over the visible span, so 100% is "the framed view".
function pfChartZoomPercent() {
  var c = _pfChartCanvas;
  if (!c || !c.viewport) return 100;
  var fit = _pfChartCanvasFit(c);
  if (!fit) return 100;
  var span = c.viewport.tMax - c.viewport.tMin;
  if (!(span > 0)) return 100;
  return Math.max(1, Math.round(((fit.tMax - fit.tMin) / span) * 100));
}

function pfChartStrip(host, opts) {
  if (!host) return null;
  opts = opts || {};
  var strip = document.createElement('div');
  strip.className = 'pf-chart-strip';
  var html = '';
  if (Array.isArray(opts.timeframes) && opts.timeframes.length) {
    html += '<div class="terminal-cascade-tf-toggle pf-chart-strip-tfs" role="radiogroup" aria-label="Chart timeframe">'
      + opts.timeframes.map(function (tf) {
        var active = String(tf) === String(opts.active || opts.timeframes[0]);
        return '<button type="button" class="terminal-cascade-tf-option' + (active ? ' is-active' : '') + '" data-strip-tf="'
          + tf + '" role="radio" aria-checked="' + (active ? 'true' : 'false') + '">' + String(tf).toUpperCase() + '</button>';
      }).join('')
      + '</div>';
  }
  // ONE chart toolbar for the whole site, copied from the Campaign Chart
  // (Equity -> Cash Cascade) because that is the design Phil adjudicated:
  // timeframes, then zoom, then the round controls, in that order. Zoom used
  // to float over the canvas here and sit in the toolbar there, which is
  // exactly the "deviates everywhere a chart exists" complaint. It lives in
  // the toolbar now; the host's floating cluster is hidden by CSS wherever
  // this strip supplies one.
  if (opts.zoom !== false) {
    html += '<div class="terminal-cascade-tf-toggle pf-chart-strip-zoom" role="group" aria-label="Chart zoom">'
      + '<button type="button" class="terminal-cascade-tf-option" data-strip-zoom-out title="Zoom out" aria-label="Zoom out">&minus;</button>'
      + '<button type="button" class="terminal-cascade-tf-option terminal-cascade-zoom-readout" data-pf-zoom-readout data-strip-zoom-reset title="Reset zoom">100%</button>'
      + '<button type="button" class="terminal-cascade-tf-option" data-strip-zoom-in title="Zoom in" aria-label="Zoom in">+</button>'
      + '</div>';
  }
  if (typeof opts.onRefresh === 'function') {
    html += '<button type="button" class="terminal-cascade-chart-control" data-strip-refresh aria-label="Refresh chart" title="Refresh chart">&#x27F3;</button>';
  }
  if (typeof opts.onClose === 'function') {
    html += '<button type="button" class="terminal-cascade-chart-control is-danger pf-chart-strip-close" data-strip-close aria-label="Close chart" title="Close chart">&#x2715;</button>';
  }
  strip.innerHTML = html;

  strip.addEventListener('click', function (event) {
    var tfBtn = event.target.closest('[data-strip-tf]');
    if (tfBtn) {
      strip.querySelectorAll('[data-strip-tf]').forEach(function (b) {
        var on = b === tfBtn;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-checked', on ? 'true' : 'false');
      });
      if (typeof opts.onTimeframe === 'function') opts.onTimeframe(tfBtn.getAttribute('data-strip-tf'));
      return;
    }
    // Zoom is NOT handled here — one delegated listener below drives it for
    // every toolbar, generated or hand-written, so they cannot drift apart.
    if (event.target.closest('[data-strip-refresh]')) { opts.onRefresh(); return; }
    if (event.target.closest('[data-strip-close]')) { opts.onClose(); }
  });
  host.insertBefore(strip, host.firstChild);
  return strip;
}

// The zoom trio behaves the same wherever it appears: the Campaign Chart's
// hand-written toolbar, a pfChartStrip, or the host's floating cluster. One
// delegated listener is what makes that true -- per-toolbar handlers are how
// the controls drifted apart in the first place. Factors match the host
// cluster's exactly (1.25 out, 0.8 in) so the same click zooms the same amount
// on every chart.
document.addEventListener('click', function (event) {
  var target = event.target;
  if (!target || typeof target.closest !== 'function') return;
  if (target.closest('[data-strip-zoom-out]')) { _pfChartCanvasZoom(1.25); return; }
  if (target.closest('[data-strip-zoom-in]')) { _pfChartCanvasZoom(0.8); return; }
  if (target.closest('[data-strip-zoom-reset]')) _pfChartCanvasZoom(0, true);
});
