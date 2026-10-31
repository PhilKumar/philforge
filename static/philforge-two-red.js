/* The two-red ladder console, on the Equity page.
 *
 * Paper only. Nothing in this file can place an order -- there is no live
 * executor written for this strategy at all, so the buttons here start, stop,
 * close and delete PAPER campaigns and nothing else.
 *
 * Kept out of philforge-app.js on purpose: that file is already very large,
 * and this console talks to its own six endpoints and shares no state with the
 * Cash Cascade beyond the scrip list.
 */
(function () {
  'use strict';

  var POLL_MS = 15000;
  var timer = null;
  var lastCampaigns = [];
  var lastClosed = [];
  var VIEW_KEY = 'philforge_equity_strategy_v1';
  var VIEW_NAMES = ['cascade', 'tworeds', 'desk'];

  function $(id) { return document.getElementById(id); }

  function esc(text) {
    return String(text == null ? '' : text).replace(/[&<>"]/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch];
    });
  }

  function rupees(value, digits) {
    if (value === null || value === undefined || value === '') return '—';
    return '₹' + Number(value).toLocaleString('en-IN', {
      minimumFractionDigits: digits === undefined ? 0 : digits,
      maximumFractionDigits: digits === undefined ? 0 : digits
    });
  }

  function setStatus(text, tone) {
    var el = $('tworeds-form-status');
    if (!el) return;
    el.textContent = text || '';
    el.style.color = tone === 'error' ? 'var(--danger)'
      : tone === 'success' ? 'var(--success)' : 'var(--muted)';
  }

  /* ── the section switcher ───────────────────────────────────────
   * Three groups of panels on one page: the two strategies, and the manual
   * desk (scrip list, order ticket, broker book) which is not a strategy at
   * all. Switching hides and shows rather than re-rendering, so an open chart
   * or a half-typed mother survives a trip to another section and back.
   *
   * The desk is exported on window because philforge-app.js has to ask whether
   * it is on screen before spending a quote poll on it.
   */
  function showStrategy(which) {
    which = VIEW_NAMES.indexOf(which) >= 0 ? which : 'cascade';
    var groups = {
      cascade: $('equity-strategy-cascade'),
      tworeds: $('equity-strategy-tworeds'),
      desk: $('equity-strategy-desk')
    };
    if (!groups.cascade || !groups.tworeds || !groups.desk) return;
    Object.keys(groups).forEach(function (key) {
      groups[key].style.display = key === which ? '' : 'none';
    });
    var buttons = document.querySelectorAll('#equity-strategy-switch [data-equity-strategy]');
    Array.prototype.forEach.call(buttons, function (button) {
      var active = button.getAttribute('data-equity-strategy') === which;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    try { localStorage.setItem(VIEW_KEY, which); } catch (_error) {}
    // Polling only while its own half is on screen: this console is useless in
    // the background and the Dhan rate budget is account-wide.
    if (which === 'tworeds') {
      refresh();
      startPolling();
    } else {
      stopPolling();
    }
  }

  function startPolling() {
    if (timer) return;
    timer = setInterval(function () {
      var panel = $('equity-strategy-tworeds');
      if (!panel || panel.style.display === 'none') return;
      if (document.hidden) return;
      refresh();
    }, POLL_MS);
  }

  function stopPolling() {
    if (!timer) return;
    clearInterval(timer);
    timer = null;
  }

  /* ── campaigns ──────────────────────────────────────────────── */

  function stateChip(campaign) {
    var map = {
      WATCHING: ['watching', 'var(--muted)'],
      ARMED: ['armed', '#fbbf24'],
      HOLDING: ['holding', '#38bdf8'],
      CLOSED: ['closed', 'var(--pf-gain)'],
      VOID: ['void', 'var(--muted)'],
      KILLED: ['killed', 'var(--danger)']
    };
    var row = map[campaign.status] || [String(campaign.status || '').toLowerCase(), 'var(--muted)'];
    return '<span class="terminal-cascade-pill" style="color:' + row[1] + ';">' + esc(row[0]) + '</span>';
  }

  function moneyCell(campaign) {
    // A campaign that has not sold anything has NO result. A confident ₹0.00
    // there reads as "traded and came out level", which is a different and
    // wrong statement. Same rule the fib-space book follows.
    if (campaign.realised) {
      var net = Number(campaign.realised.net_pnl);
      return '<strong style="color:' + (net >= 0 ? 'var(--pf-gain)' : 'var(--danger)') + ';">' +
        rupees(net, 2) + '</strong><span class="cascade-scan-name">realised, after costs</span>';
    }
    if (campaign.open_money) {
      var open = Number(campaign.open_money.net_pnl);
      return '<span style="color:' + (open >= 0 ? 'var(--pf-gain)' : 'var(--danger)') + ';">' +
        rupees(open, 2) + '</span><span class="cascade-scan-name">if sold now</span>';
    }
    return '—';
  }

  function campaignRow(campaign) {
    var mother = campaign.mother || {};
    var when = mother.timestamp ? String(mother.timestamp).slice(0, 16).replace('T', ' ') : '—';
    // Every campaign can show its own picture — symbol AND mother travel on
    // the button, so this draws the campaign's chart, not whatever the setup
    // form happens to hold.
    var actions = '<button type="button" class="btn btn-sm btn-outline" data-two-red-chart="' +
      esc(campaign.symbol) + '" data-two-red-chart-mother="' + esc(mother.timestamp || '') +
      '" data-two-red-chart-tf="' + esc(mother.timeframe || '1d') + '">Chart</button>';
    if (campaign.running) {
      actions += '<button type="button" class="btn btn-sm btn-outline" data-two-red-stop="' +
        esc(campaign.symbol) + '">Stop</button>';
      if (campaign.quantity > 0) {
        actions += '<button type="button" class="btn btn-sm btn-danger" data-two-red-kill="' +
          esc(campaign.symbol) + '">Close now</button>';
      }
    } else {
      actions += '<button type="button" class="btn btn-sm btn-danger" data-two-red-delete="' +
        esc(campaign.symbol) + '">Delete</button>';
    }

    var target = campaign.target
      ? rupees(campaign.target, 2) +
        (campaign.target_gap_pct !== null && campaign.target_gap_pct !== undefined
          ? '<span class="cascade-scan-name">' + Number(campaign.target_gap_pct).toFixed(1) + '% away</span>'
          : '')
      : '—';

    return '<tr>' +
      '<td><strong>' + esc(campaign.symbol) + '</strong><span class="cascade-scan-name">' +
      esc(mother.timeframe || '') + ' mother · ' + esc(when) + '</span></td>' +
      '<td>' + stateChip(campaign) + '</td>' +
      '<td class="num">' + rupees(mother.high, 2) + '</td>' +
      '<td class="num">' + campaign.rung + ' of ' + campaign.rungs + '</td>' +
      '<td class="num">' + (campaign.quantity || 0) + '</td>' +
      '<td class="num">' + (campaign.average_entry ? rupees(campaign.average_entry, 2) : '—') + '</td>' +
      '<td class="num">' + target + '</td>' +
      '<td class="num">' + moneyCell(campaign) + '</td>' +
      '<td class="cascade-scan-row-actions">' + actions + '</td>' +
      '</tr>';
  }

  /* Phil: "the chart is flickering". The poll rewrote both tables with
   * innerHTML every 15 seconds whether anything changed or not, and behind the
   * chart overlay's translucent, blurred backdrop each wipe-and-rebuild reads
   * as a flicker of the whole picture. Two rules kill it: a panel is only
   * touched when its rendered markup actually CHANGED, and nothing repaints at
   * all while the chart overlay is open (same freeze the Cascade journal uses
   * — the table under a dialog has no viewer). */
  var lastHtml = { campaigns: null, closed: null };

  function setHtml(which, body, html) {
    if (lastHtml[which] === html) return;
    lastHtml[which] = html;
    body.innerHTML = html;
  }

  function chartOverlayOpen() {
    var overlay = $('tworeds-chart-overlay');
    return !!overlay && overlay.classList.contains('is-open');
  }

  function renderCampaigns(campaigns) {
    var body = $('tworeds-campaigns-body');
    if (!body) return;
    if (!campaigns.length) {
      setHtml('campaigns', body, '<div class="terminal-cascade-empty">Nothing running. Name a mother candle above to start one.</div>');
      return;
    }
    setHtml('campaigns', body,
      '<div class="cascade-scan-table-wrap"><table class="cascade-scan-table">' +
      '<thead><tr>' +
      '<th>Scrip</th><th>State</th><th class="num">Mother high</th>' +
      '<th class="num" title="How many rungs of the ladder have filled">Rungs</th>' +
      '<th class="num">Shares</th><th class="num">Avg entry</th>' +
      '<th class="num">Target</th><th class="num">P&amp;L</th><th></th>' +
      '</tr></thead><tbody>' + campaigns.map(campaignRow).join('') + '</tbody></table></div>');
  }

  function renderClosed(rows) {
    var body = $('tworeds-closed-body');
    if (!body) return;
    if (!rows.length) {
      setHtml('closed', body, '<div class="terminal-cascade-empty">No closed campaigns yet.</div>');
      return;
    }
    setHtml('closed', body,
      '<div class="cascade-scan-table-wrap"><table class="cascade-scan-table">' +
      '<thead><tr><th>Scrip</th><th>Ended</th><th class="num">Shares</th>' +
      '<th class="num">Invested</th><th class="num">Net</th></tr></thead><tbody>' +
      rows.map(function (row) {
        var net = row.realised ? Number(row.realised.net_pnl) : null;
        return '<tr>' +
          '<td><strong>' + esc(row.symbol) + '</strong></td>' +
          '<td>' + esc((row.exit && row.exit.reason) || row.ended_reason || '—') + '</td>' +
          '<td class="num">' + (row.quantity || 0) + '</td>' +
          '<td class="num">' + rupees(row.invested, 0) + '</td>' +
          '<td class="num">' + (net === null ? '—' :
            '<strong style="color:' + (net >= 0 ? 'var(--pf-gain)' : 'var(--danger)') + ';">' + rupees(net, 2) + '</strong>') +
          '</td></tr>';
      }).join('') + '</tbody></table></div>');
  }

  async function refresh() {
    // Frozen under the chart: repainting a table nobody can see, through a
    // blurred backdrop, is exactly the flicker being fixed. The overlay's
    // close handler refreshes once, so nothing is stale after it.
    if (chartOverlayOpen()) return;
    try {
      var res = await fetch('/api/two-red/status', { credentials: 'same-origin', cache: 'no-store' });
      if (!res.ok) return;
      var data = await res.json();
      lastCampaigns = data.campaigns || [];
      lastClosed = data.closed || [];
      renderCampaigns(lastCampaigns);
      renderClosed(lastClosed);
      var meta = $('tworeds-campaigns-meta');
      if (meta) {
        var holding = lastCampaigns.filter(function (row) { return row.quantity > 0; }).length;
        meta.textContent = lastCampaigns.length + ' campaign' + (lastCampaigns.length === 1 ? '' : 's')
          + ' · ' + holding + ' holding shares';
      }
    } catch (err) {
      // A failed poll is not worth a banner: the next one is 15 seconds away
      // and the table still shows the last good state.
    }
  }

  /* ── actions ────────────────────────────────────────────────── */

  window.startTwoRedCampaign = async function startTwoRedCampaign() {
    var symbol = ($('tworeds-symbol') || {}).value || '';
    var mother = ($('tworeds-mother-timestamp') || {}).value || '';
    if (!symbol.trim()) { setStatus('Pick a scrip first.', 'error'); return; }
    if (!mother) { setStatus('Name the mother candle.', 'error'); return; }
    // Asked first, as Cash Cascade's Start is: one tap must not start a
    // campaign by accident (the Equity audit, 2026-10-21).
    if (typeof window.customConfirm === 'function') {
      var purse = parseFloat(($('tworeds-capital') || {}).value || '200000');
      var ok = await window.customConfirm(
        'Start the Two-red ladder (paper) for <strong>' + esc(symbol.trim().toUpperCase()) + '</strong>?' +
        '<br><span style="font-size:12px;color:var(--muted);">Mother: ' + esc(mother) + ' · Purse: ' + esc(rupees(purse, 0)) +
        ' · No broker order is sent.</span>',
        { title: 'Start Two-red ladder', okText: 'Start Paper' }
      );
      if (!ok) return;
    }
    setStatus('Starting…');
    var button = $('tworeds-start');
    if (button) button.disabled = true;
    try {
      var res = await fetch('/api/two-red/start', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: symbol.trim().toUpperCase(),
          mother_timestamp: mother,
          mother_timeframe: ($('tworeds-mother-timeframe') || {}).value || '1d',
          capital_inr: parseFloat(($('tworeds-capital') || {}).value || '200000'),
          min_fall_pct: parseFloat(($('tworeds-min-fall') || {}).value || '8'),
          target_fraction: parseFloat(($('tworeds-target') || {}).value || '0.75')
        })
      });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || 'Could not start the campaign.');
      var campaign = data.campaign || {};
      // A replayed campaign can arrive already finished, and saying "started"
      // for something that is over reads as a fault when the table shows it
      // closed a second later.
      if (campaign.status === 'VOID') {
        // Saying only "spent" leaves nowhere to go, and with an 8% gate most
        // hand-picked candles ARE spent. Point at the finder instead.
        setStatus('That mother is spent — price closed back above its high, so the fall it was waiting for is over. '
          + 'Press Find mothers to see which ones are still live.', 'error');
        if (typeof window.findTwoRedMothers === 'function') window.findTwoRedMothers();
      } else if (campaign.status === 'CLOSED') {
        setStatus('Replayed to now and it has already finished. See the row below.', 'success');
      } else {
        setStatus('Running. ' + (campaign.quantity ? campaign.quantity + ' shares already bought on replay.' : 'Watching for two reds.'), 'success');
      }
      await refresh();
    } catch (err) {
      setStatus(String((err && err.message) || err), 'error');
    } finally {
      if (button) button.disabled = false;
    }
  };

  window.refreshTwoRedCampaigns = function refreshTwoRedCampaigns() { refresh(); };

  /* ── the mother finder ──────────────────────────────────────────
   * The piece that makes this page usable. An 8% gate means most candles are
   * not live setups: over three years a symbol takes 2-8 trades. Naming one by
   * hand and being told "spent" afterwards leaves nowhere to go, so this runs
   * the backtest's own detector and says which mothers are still worth taking.
   */
  function motherRow(row) {
    var tone = row.state === 'ready' ? 'var(--pf-gain)' : row.state === 'waiting' ? '#fbbf24' : 'var(--muted)';
    var when = String(row.timestamp).slice(0, 10);
    var why = row.state === 'ready'
      ? 'fell ' + row.fall_pct.toFixed(1) + '% — deep enough to buy'
      : row.state === 'waiting'
        ? 'only ' + row.fall_pct.toFixed(1) + '% down so far'
        : 'reclaimed ' + (row.reclaimed_at ? String(row.reclaimed_at).slice(0, 10) : '');
    return '<button type="button" class="cascade-scan-rung' +
      (row.state === 'spent' ? ' is-short' : '') +
      '" data-two-red-mother="' + esc(row.timestamp) + '"' +
      (row.state === 'spent' ? ' disabled' : '') +
      ' style="text-align:left;cursor:' + (row.state === 'spent' ? 'not-allowed' : 'pointer') + ';">' +
      '<span style="color:' + tone + ';">' + esc(row.state) + ' · ' + esc(when) + '</span>' +
      '<strong>' + rupees(row.high, 2) + '</strong>' +
      '<em>' + esc(why) + '</em>' +
      '</button>';
  }

  window.findTwoRedMothers = async function findTwoRedMothers() {
    var symbol = (($('tworeds-symbol') || {}).value || '').trim().toUpperCase();
    if (!symbol) { setStatus('Pick a scrip first — then it can look for mothers.', 'error'); return; }
    var box = $('tworeds-mothers');
    var button = $('tworeds-find-mothers');
    if (button) button.disabled = true;
    setStatus('Looking for run-mothers on ' + symbol + '…');
    try {
      var res = await fetch('/api/two-red/mothers?symbol=' + encodeURIComponent(symbol) +
        '&timeframe=' + encodeURIComponent(($('tworeds-mother-timeframe') || {}).value || '1d'),
        { credentials: 'same-origin', cache: 'no-store' });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || 'Could not look for mothers.');
      var rows = data.mothers || [];
      if (box) {
        box.style.display = '';
        if (!rows.length) {
          box.innerHTML = '<div class="cascade-scan-rung-note">No run-mothers found on ' +
            esc(symbol) + '. That means no run of five higher highs in the window — not a fault.</div>';
        } else {
          box.innerHTML = '<div class="cascade-scan-rungs-strip">' + rows.map(motherRow).join('') +
            '<div class="cascade-scan-rung-note"><strong>ready</strong> has already fallen far enough to buy · ' +
            '<strong>waiting</strong> is still under its high but not deep enough yet · ' +
            '<strong>spent</strong> closed back above its high, so a campaign on it would void at once.</div></div>';
        }
      }
      var ready = rows.filter(function (row) { return row.state === 'ready'; }).length;
      setStatus(rows.length + ' mother' + (rows.length === 1 ? '' : 's') + ' found · ' +
        (ready ? ready + ' ready to take. Click one.' : 'none ready — the ones waiting can still be started.'),
        ready ? 'success' : '');
    } catch (err) {
      setStatus(String((err && err.message) || err), 'error');
    } finally {
      if (button) button.disabled = false;
    }
  };

  /* ── the chart ──────────────────────────────────────────────────
   * pfBenchDrawChart is the ONE renderer PhilForge draws through. This owns
   * only the payload translator: `t` as epoch SECONDS, and the levels that
   * make the picture mean something.
   */
  function chartPayload(data) {
    var epoch = function (value) {
      var parsed = Date.parse(value);
      return Number.isFinite(parsed) ? Math.round(parsed / 1000) : null;
    };
    var candles = (data.candles || []).map(function (row) {
      return {
        t: epoch(row.t), o: Number(row.o), h: Number(row.h),
        l: Number(row.l), c: Number(row.c), is_mother: !!row.is_mother
      };
    }).filter(function (row) {
      return row.t !== null && [row.o, row.h, row.l, row.c].every(Number.isFinite);
    });
    return {
      timeframe: data.timeframe || '1d',
      candles: candles,
      mother: data.mother || null,
      lines: data.lines || [],
      entries: (data.entries || []).map(function (row) { return { t: epoch(row.t), price: Number(row.price) }; }),
      exits: (data.exits || []).map(function (row) {
        return { t: epoch(row.t), price: Number(row.price), pnl: row.pnl };
      }),
      avg_entry_price: data.avg_entry_price || null,
      tp_price: data.tp_price || null,
      tp_label: data.tp_label || ''
    };
  }

  var chartTf = '1d';
  var drawnTf = '';

  // Which symbol+mother the OPEN overlay is showing. The TF toggle redraws
  // through this, so switching timeframes on a campaign's chart keeps showing
  // that campaign instead of snapping back to the setup form's scrip — the
  // exact drift the Cascade's Preview button had.
  var chartContext = null;

  window.loadTwoRedChart = async function loadTwoRedChart(symbolArg, motherArg) {
    var fromRow = typeof symbolArg === 'string' && symbolArg;
    var symbol = (fromRow ? symbolArg : (($('tworeds-symbol') || {}).value || '')).trim().toUpperCase();
    if (!symbol) { setStatus('Pick a scrip first.', 'error'); return; }
    var overlay = $('tworeds-chart-overlay');
    var box = $('tworeds-chart');
    var title = $('tworeds-chart-title');
    if (typeof window.pfSetCascadeChartOverlayOpen === 'function') window.pfSetCascadeChartOverlayOpen(overlay, true);
    else if (overlay) { overlay.classList.add('is-open'); overlay.setAttribute('aria-hidden', 'false'); }
    if (title) title.textContent = symbol + ' · ' + chartTf.toUpperCase();
    // No "Loading…" flash over a chart already showing: hold the old frame
    // until the new candles arrive, then repaint once (Phil's flicker report,
    // 2026-08-13). The empty box still announces itself on first open.
    if (box && !box.querySelector('#pf-bench-canvas-host')) {
      box.innerHTML = '<div class="pf-cascade-chart-empty">Loading ' + esc(symbol) + ' candles…</div>';
    }
    try {
      var query = 'symbol=' + encodeURIComponent(symbol) + '&timeframe=' + encodeURIComponent(chartTf);
      var mother = fromRow ? (motherArg || '') : (($('tworeds-mother-timestamp') || {}).value || '');
      chartContext = { symbol: symbol, mother: mother };
      if (mother) query += '&mother_timestamp=' + encodeURIComponent(String(mother).slice(0, 16));
      var res = await fetch('/api/two-red/chart?' + query, { credentials: 'same-origin', cache: 'no-store' });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || 'Chart failed.');
      if (typeof window.pfBenchDrawChart !== 'function') throw new Error('Chart renderer not loaded.');
      // An already-mounted canvas is UPDATED in place, never torn down and
      // remounted: replacing the canvas element is the flicker (the compositor
      // can show the fresh blank element before its first paint commits).
      // A new timeframe refits the viewport; same-timeframe keeps it.
      var payload = chartPayload(data);
      var mountedHost = box ? box.querySelector('#pf-bench-canvas-host') : null;
      var canvasIsThisChart = mountedHost && typeof window._pfChartCanvas !== 'undefined'
        && window._pfChartCanvas && window._pfChartCanvas.host === mountedHost;
      var drawnKey = symbol + '|' + mother + '|' + String(data.timeframe || chartTf);
      var view = canvasIsThisChart && drawnKey === drawnTf && typeof window._pfChartCanvasRefreshState === 'function'
        ? window._pfChartCanvasRefreshState() : null;
      var refreshed = canvasIsThisChart && typeof window._pfChartCanvasRefresh === 'function'
        && window._pfChartCanvasRefresh(payload, view);
      if (!refreshed) {
        // A FIRST mount measures the host to size itself, so it must not draw
        // in the frame the overlay opened — that yields a 2px canvas. An
        // in-place refresh above is already sized and skips the wait.
        if (typeof window.pfWaitForCascadeChartLayout === 'function') await window.pfWaitForCascadeChartLayout();
        window.pfBenchDrawChart(box, payload);
      }
      drawnTf = drawnKey;
      var meta = $('tworeds-chart-meta');
      if (meta) {
        meta.textContent = (data.candles || []).length + ' closed ' + String(data.timeframe).toUpperCase() +
          ' candles · ' + (data.lines || []).length + ' levels · drag to pan, wheel to zoom, double-click to reset';
      }
    } catch (err) {
      if (typeof window._pfChartCanvasTeardown === 'function') window._pfChartCanvasTeardown();
      if (box) box.innerHTML = '<div class="pf-cascade-chart-empty">' + esc(String((err && err.message) || err)) + '</div>';
    }
  };

  window.hideTwoRedChart = function hideTwoRedChart() {
    var overlay = $('tworeds-chart-overlay');
    if (typeof window.pfSetCascadeChartOverlayOpen === 'function') window.pfSetCascadeChartOverlayOpen(overlay, false);
    else if (overlay) { overlay.classList.remove('is-open'); overlay.setAttribute('aria-hidden', 'true'); }
    // Not tearing down leaks the resize/mutation observers the renderer mounts.
    if (typeof window._pfChartCanvasTeardown === 'function') window._pfChartCanvasTeardown();
    var box = $('tworeds-chart');
    if (box) box.innerHTML = '';
    chartContext = null;
    drawnTf = '';
    // The poll was held while the overlay was up; catch the tables up now.
    refresh();
  };

  async function post(url, symbol) {
    var res = await fetch(url + '?symbol=' + encodeURIComponent(symbol), {
      method: 'POST', credentials: 'same-origin'
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.detail || 'Request failed.');
    return data;
  }

  async function handle(symbol, kind) {
    try {
      if (kind === 'stop') {
        var ok = await window.customConfirm(
          'Stop watching ' + esc(symbol) + '? Anything already bought stays held — this does not sell.',
          { title: 'Stop campaign', okText: 'Stop' }
        );
        if (!ok) return;
        await post('/api/two-red/stop', symbol);
        setStatus(symbol + ' stopped. Any shares are still held.', 'success');
      } else if (kind === 'kill') {
        var okKill = await window.customConfirm(
          'Close ' + esc(symbol) + ' now at the last traded price? No Dhan order is sent — this is paper.',
          { title: 'Close basket', okText: 'Close now', danger: true }
        );
        if (!okKill) return;
        await post('/api/two-red/kill', symbol);
        setStatus(symbol + ' closed at the last price.', 'success');
      } else if (kind === 'delete') {
        var okDelete = await window.customConfirm(
          'Delete ' + esc(symbol) + ' from this page? If it traded, its record moves to Closed Campaigns.',
          { title: 'Delete campaign', okText: 'Delete', danger: true }
        );
        if (!okDelete) return;
        var res = await fetch('/api/two-red?symbol=' + encodeURIComponent(symbol), {
          method: 'DELETE', credentials: 'same-origin'
        });
        var data = await res.json().catch(function () { return {}; });
        if (!res.ok) throw new Error(data.detail || 'Delete failed.');
        setStatus(symbol + ' deleted.', 'success');
      }
      await refresh();
    } catch (err) {
      setStatus(String((err && err.message) || err), 'error');
    }
  }

  /* ── wiring ─────────────────────────────────────────────────── */

  // philforge-app.js polls the LTP and the order book on timers. Those cost
  // Dhan calls against an ACCOUNT-WIDE rate budget, and both are useless while
  // the desk is hidden behind another section, so it asks this first.
  window.pfEquityDeskVisible = function pfEquityDeskVisible() {
    var desk = document.getElementById('equity-strategy-desk');
    return !!desk && desk.style.display !== 'none';
  };

  function init() {
    var page = $('stock-terminal-page');
    if (!page) return;

    var switcher = $('equity-strategy-switch');
    if (switcher) {
      switcher.addEventListener('click', function (event) {
        var button = event.target.closest('[data-equity-strategy]');
        if (!button) return;
        showStrategy(button.getAttribute('data-equity-strategy'));
      });
    }

    var remembered = 'cascade';
    try {
      var saved = localStorage.getItem(VIEW_KEY);
      if (VIEW_NAMES.indexOf(saved) >= 0) remembered = saved;
    } catch (_error) {}
    showStrategy(remembered);

    var campaigns = $('tworeds-campaigns-body');
    if (campaigns) {
      campaigns.addEventListener('click', function (event) {
        var chart = event.target.closest('[data-two-red-chart]');
        if (chart) {
          // The mother's own chart first: a 1H mother opens on 1H, a daily on 1D.
          var tf = chart.getAttribute('data-two-red-chart-tf') || '1d';
          chartTf = tf === '1h' ? '1h' : '1d';
          var toggle = $('tworeds-chart-tf');
          if (toggle) {
            toggle.querySelectorAll('[data-tworeds-tf]').forEach(function (node) {
              var active = node.getAttribute('data-tworeds-tf') === chartTf;
              node.classList.toggle('is-active', active);
              node.setAttribute('aria-checked', active ? 'true' : 'false');
            });
          }
          return void window.loadTwoRedChart(
            chart.getAttribute('data-two-red-chart'),
            chart.getAttribute('data-two-red-chart-mother') || ''
          );
        }
        var stop = event.target.closest('[data-two-red-stop]');
        if (stop) return void handle(stop.getAttribute('data-two-red-stop'), 'stop');
        var kill = event.target.closest('[data-two-red-kill]');
        if (kill) return void handle(kill.getAttribute('data-two-red-kill'), 'kill');
        var drop = event.target.closest('[data-two-red-delete]');
        if (drop) return void handle(drop.getAttribute('data-two-red-delete'), 'delete');
      });
    }

    // The screen's rows have no "Use" button, so picking a scrip for a campaign
    // is a click on the row itself. Filling the form is the whole handoff
    // between the two halves of this strategy.
    var screen = $('tworeds-scan-body');
    if (screen) {
      screen.addEventListener('click', function (event) {
        if (event.target.closest('button')) return;
        var row = event.target.closest('tr[data-symbol]');
        if (!row) return;
        var symbol = row.getAttribute('data-symbol');
        var field = $('tworeds-symbol');
        if (field) field.value = symbol;
        var banner = $('tworeds-selected');
        if (banner) {
          banner.textContent = 'Campaign setup is for ' + symbol;
          banner.classList.add('is-set');
        }
        var capital = $('tworeds-scan-capital');
        var purse = $('tworeds-capital');
        if (capital && purse && capital.value) purse.value = capital.value;
        setStatus(symbol + ' picked. Name the mother candle to start.', '');
      });
    }

    // Picking a found mother fills the timestamp field, which is the whole
    // point of the finder: no typing a datetime and hoping.
    var mothers = $('tworeds-mothers');
    if (mothers) {
      mothers.addEventListener('click', function (event) {
        var button = event.target.closest('[data-two-red-mother]');
        if (!button || button.disabled) return;
        var stamp = button.getAttribute('data-two-red-mother');
        var field = $('tworeds-mother-timestamp');
        if (field) {
          // datetime-local wants YYYY-MM-DDTHH:MM and nothing after it.
          field.value = String(stamp).slice(0, 16);
          field.dispatchEvent(new Event('input', { bubbles: true }));
        }
        mothers.querySelectorAll('[data-two-red-mother]').forEach(function (node) {
          node.classList.toggle('is-picked', node === button);
        });
        setStatus('Mother of ' + String(stamp).slice(0, 10) + ' chosen. Start Paper to run it.', 'success');
      });
    }

    var tfToggle = $('tworeds-chart-tf');
    if (tfToggle) {
      tfToggle.addEventListener('click', function (event) {
        var button = event.target.closest('[data-tworeds-tf]');
        if (!button) return;
        chartTf = button.getAttribute('data-tworeds-tf');
        tfToggle.querySelectorAll('[data-tworeds-tf]').forEach(function (node) {
          var active = node === button;
          node.classList.toggle('is-active', active);
          node.setAttribute('aria-checked', active ? 'true' : 'false');
        });
        // Redraw what is on screen, not what the form holds.
        if (chartContext) window.loadTwoRedChart(chartContext.symbol, chartContext.mother);
        else window.loadTwoRedChart();
      });
    }

    var overlay = $('tworeds-chart-overlay');
    if (overlay) {
      overlay.addEventListener('click', function (event) {
        if (event.target === overlay) window.hideTwoRedChart();
      });
    }

    // The readable IST echo, same as the Cascade's mother field: the native
    // picker renders in browser locale, which reads as mm/dd to half the world.
    var motherField = $('tworeds-mother-timestamp');
    var readable = $('tworeds-mother-readable');
    if (motherField && readable) {
      var sync = function () {
        var match = String(motherField.value || '').match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
        readable.textContent = match
          ? match[3] + '-' + match[2] + '-' + match[1] + '  ' + match[4] + ':' + match[5] + ' IST'
          : '--';
      };
      sync();
      motherField.addEventListener('input', sync);
      motherField.addEventListener('change', sync);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
