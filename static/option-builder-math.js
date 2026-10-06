/* Option Builder -- the numbers behind the payoff chart.
 *
 * Pure functions only: no DOM, no fetch. The page calls these on every slider
 * move, so they must be quick, and tests/test_option_builder_math.py runs them
 * under node, so they must not touch the browser.
 *
 * Conventions
 *   leg      {side:'BUY'|'SELL', type:'CE'|'PE', strike, expiry:'YYYY-MM-DD',
 *             lots, entry, iv (percent), ltp, enabled}
 *   qty      lots * lotSize, signed: + for BUY, - for SELL
 *   time     milliseconds since epoch; an expiry ends at 15:30 IST
 *   P&L      rupees for the whole strategy
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PFOptionMath = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const YEAR_MS = 365 * 24 * 3600 * 1000;
  const RISK_FREE = 0.065; // close to the 91-day T-bill; Dhan's greeks assume a similar rate
  const IST_OFFSET_MIN = 330;

  // Abramowitz-Stegun 7.1.26: |error| < 1.5e-7, plenty for a chart.
  function normCdf(x) {
    const t = 1 / (1 + 0.3275911 * Math.abs(x) / Math.SQRT2);
    const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x / 2);
    return x >= 0 ? 0.5 * (1 + y) : 0.5 * (1 - y);
  }
  function normPdf(x) { return Math.exp(-x * x / 2) / Math.sqrt(2 * Math.PI); }

  function expiryCloseMs(expiry) {
    const [y, m, d] = String(expiry).split('-').map(Number);
    return Date.UTC(y, m - 1, d, 15, 30) - IST_OFFSET_MIN * 60000;
  }
  function yearsBetween(fromMs, toMs) { return Math.max(0, (toMs - fromMs) / YEAR_MS); }

  function intrinsic(type, S, K) { return type === 'CE' ? Math.max(0, S - K) : Math.max(0, K - S); }

  function bsPrice(type, S, K, t, sigma, r = RISK_FREE) {
    if (t <= 0 || sigma <= 0 || S <= 0) return intrinsic(type, S, K);
    const st = sigma * Math.sqrt(t);
    const d1 = (Math.log(S / K) + (r + sigma * sigma / 2) * t) / st;
    const d2 = d1 - st;
    const df = Math.exp(-r * t);
    return type === 'CE'
      ? S * normCdf(d1) - K * df * normCdf(d2)
      : K * df * normCdf(-d2) - S * normCdf(-d1);
  }

  // Per-unit greeks; theta per calendar day, vega per 1 vol point -- the
  // units Dhan's chain uses, so the two can sit in one table.
  function bsGreeks(type, S, K, t, sigma, r = RISK_FREE) {
    if (t <= 0 || sigma <= 0 || S <= 0) {
      const itm = type === 'CE' ? S > K : S < K;
      return { delta: itm ? (type === 'CE' ? 1 : -1) : 0, gamma: 0, theta: 0, vega: 0 };
    }
    const st = sigma * Math.sqrt(t);
    const d1 = (Math.log(S / K) + (r + sigma * sigma / 2) * t) / st;
    const d2 = d1 - st;
    const df = Math.exp(-r * t);
    const gamma = normPdf(d1) / (S * st);
    const vega = S * normPdf(d1) * Math.sqrt(t) / 100;
    const common = -S * normPdf(d1) * sigma / (2 * Math.sqrt(t));
    const theta = type === 'CE'
      ? (common - r * K * df * normCdf(d2)) / 365
      : (common + r * K * df * normCdf(-d2)) / 365;
    return { delta: type === 'CE' ? normCdf(d1) : normCdf(d1) - 1, gamma, theta, vega };
  }

  function signedQty(leg, lotSize) { return (leg.side === 'BUY' ? 1 : -1) * leg.lots * lotSize; }
  function activeLegs(legs) { return (legs || []).filter((leg) => leg && leg.enabled !== false && leg.lots > 0); }

  /* What one leg is worth at price S and moment `atMs`.
   * Black-Scholes on the leg's IV (+ ivShift points). The model never quite
   * reproduces the market price, so the gap seen NOW (ltp - model) is carried
   * forward and faded with the time left: at today's price the curve passes
   * through today's real P&L, and at expiry it is pure intrinsic. */
  function legValue(leg, S, atMs, ctx) {
    const closeMs = expiryCloseMs(leg.expiry);
    const t = yearsBetween(atMs, closeMs);
    if (t <= 0) return intrinsic(leg.type, S, leg.strike);
    const sigma = Math.max(0.01, ((leg.iv || ctx.fallbackIv || 15) + (ctx.ivShift || 0)) / 100);
    let value = bsPrice(leg.type, S, leg.strike, t, sigma);
    const tNow = yearsBetween(ctx.nowMs, closeMs);
    if (leg.ltp > 0 && tNow > 0 && ctx.spot > 0) {
      const gap = leg.ltp - bsPrice(leg.type, ctx.spot, leg.strike, tNow, Math.max(0.01, (leg.iv || ctx.fallbackIv || 15) / 100));
      value += gap * Math.min(1, t / tNow);
    }
    return Math.max(0, value);
  }

  function pnlAt(legs, S, atMs, ctx) {
    let total = 0;
    for (const leg of activeLegs(legs)) total += signedQty(leg, ctx.lotSize) * (legValue(leg, S, atMs, ctx) - leg.entry);
    return total;
  }

  function firstExpiryMs(legs) {
    const live = activeLegs(legs);
    if (!live.length) return 0;
    return Math.min(...live.map((leg) => expiryCloseMs(leg.expiry)));
  }

  function sameExpiry(legs) { return new Set(activeLegs(legs).map((leg) => leg.expiry)).size <= 1; }

  /* The price axis: wide enough for a 3-sigma move to the first expiry and for
   * every strike in the strategy, never narrower than +/-4%. */
  function priceRange(legs, ctx, zoom = 1) {
    const spot = ctx.spot;
    const sigma = Math.max(0.05, (ctx.atmIv || ctx.fallbackIv || 15) / 100);
    const t = Math.max(1 / 365, yearsBetween(ctx.nowMs, firstExpiryMs(legs) || ctx.nowMs + 7 * 864e5));
    let half = Math.max(0.04, 3 * sigma * Math.sqrt(t)) * spot;
    for (const leg of activeLegs(legs)) half = Math.max(half, Math.abs(leg.strike - spot) * 1.25);
    half /= zoom;
    return [Math.max(1, spot - half), spot + half];
  }

  function series(legs, ctx, opts = {}) {
    const [lo, hi] = opts.range || priceRange(legs, ctx, opts.zoom || 1);
    const n = opts.points || 241;
    const expiryMs = firstExpiryMs(legs);
    const targetMs = Math.min(opts.targetMs || ctx.nowMs, expiryMs || ctx.nowMs);
    const xs = [], atExpiry = [], atTarget = [];
    for (let i = 0; i < n; i++) {
      const S = lo + (hi - lo) * i / (n - 1);
      xs.push(S);
      atExpiry.push(pnlAt(legs, S, expiryMs, ctx));
      atTarget.push(pnlAt(legs, S, targetMs, ctx));
    }
    return { xs, atExpiry, atTarget, lo, hi, expiryMs, targetMs };
  }

  /* Max profit / loss and breakevens at the first expiry.
   * One expiry: the payoff is straight lines between strikes, so evaluating at
   * 0, every strike, and the slope past the top strike is EXACT -- including
   * whether a side is unlimited. Several expiries: read from a fine grid and
   * marked approximate. */
  function expiryStats(legs, ctx) {
    const live = activeLegs(legs);
    if (!live.length) return null;
    const expiryMs = firstExpiryMs(live);
    const f = (S) => pnlAt(live, S, expiryMs, ctx);
    const exact = sameExpiry(live);
    let points;
    if (exact) {
      const strikes = [...new Set(live.map((leg) => leg.strike))].sort((a, b) => a - b);
      points = [0.01, ...strikes];
    } else {
      const [lo, hi] = priceRange(live, ctx, 0.35);
      points = [];
      for (let i = 0; i <= 1200; i++) points.push(Math.max(0.01, lo + (hi - lo) * i / 1200));
    }
    const values = points.map(f);
    const top = points[points.length - 1];
    const slopeUp = exact
      ? live.reduce((acc, leg) => acc + (leg.type === 'CE' ? signedQty(leg, ctx.lotSize) : 0), 0)
      : (f(top * 1.5) - f(top)) / (top * 0.5);
    let maxProfit = Math.max(...values);
    let maxLoss = Math.min(...values);
    const unlimitedProfit = slopeUp > 1e-9;
    const unlimitedLoss = slopeUp < -1e-9;
    if (unlimitedProfit) maxProfit = Infinity;
    if (unlimitedLoss) maxLoss = -Infinity;

    const breakevens = [];
    const xs = exact ? [...points, top * 2] : points;
    const ys = exact ? [...values, f(top * 2)] : values;
    for (let i = 1; i < xs.length; i++) {
      const a = ys[i - 1], b = ys[i];
      let x = null;
      if (Math.abs(b) < 1e-9) x = xs[i];
      else if (Math.abs(a) >= 1e-9 && (a < 0) !== (b < 0)) x = xs[i - 1] + (xs[i] - xs[i - 1]) * (a / (a - b));
      if (x !== null && x > 1 && !breakevens.some((v) => Math.abs(v - x) < 1e-6)) breakevens.push(x);
    }
    return { maxProfit, maxLoss, breakevens, exact, expiryMs };
  }

  /* Probability the strategy is in profit at the first expiry, under a
   * lognormal walk with the ATM IV. Integrates over a fine price grid, so it
   * serves several expiries as well as one. */
  function probabilityOfProfit(legs, ctx) {
    const live = activeLegs(legs);
    if (!live.length || !(ctx.spot > 0)) return null;
    const expiryMs = firstExpiryMs(live);
    const t = yearsBetween(ctx.nowMs, expiryMs);
    const sigma = Math.max(0.01, (ctx.atmIv || ctx.fallbackIv || 15) / 100);
    if (t <= 0) return pnlAt(live, ctx.spot, expiryMs, ctx) > 0 ? 1 : 0;
    const sd = sigma * Math.sqrt(t);
    const mu = Math.log(ctx.spot) - sd * sd / 2;
    const cdf = (x) => normCdf((Math.log(x) - mu) / sd);
    const lo = ctx.spot * Math.exp(-6 * sd), hi = ctx.spot * Math.exp(6 * sd);
    const n = 2000;
    let p = 0, prevX = lo, prevC = cdf(lo);
    for (let i = 1; i <= n; i++) {
      const x = lo * Math.pow(hi / lo, i / n);
      const c = cdf(x);
      if (pnlAt(live, (prevX + x) / 2, expiryMs, ctx) > 0) p += c - prevC;
      prevX = x; prevC = c;
    }
    if (pnlAt(live, hi * 1.5, expiryMs, ctx) > 0) p += 1 - cdf(hi);
    if (pnlAt(live, lo / 1.5, expiryMs, ctx) > 0) p += cdf(lo);
    return Math.max(0, Math.min(1, p));
  }

  function netPremium(legs, lotSize) {
    // + is a credit received, - a debit paid
    return activeLegs(legs).reduce((acc, leg) => acc - signedQty(leg, lotSize) * leg.entry, 0);
  }

  function currentPnl(legs, lotSize) {
    return activeLegs(legs).reduce((acc, leg) => acc + (leg.ltp > 0 ? signedQty(leg, lotSize) * (leg.ltp - leg.entry) : 0), 0);
  }

  // Strategy greeks: from Dhan's per-leg greeks when given, else Black-Scholes.
  function positionGreeks(legs, ctx, S = ctx.spot, atMs = ctx.nowMs) {
    const total = { delta: 0, gamma: 0, theta: 0, vega: 0 };
    const rows = activeLegs(legs).map((leg) => {
      const q = signedQty(leg, ctx.lotSize);
      const useBroker = leg.greeks && S === ctx.spot && atMs === ctx.nowMs && !ctx.ivShift;
      const t = yearsBetween(atMs, expiryCloseMs(leg.expiry));
      const sigma = Math.max(0.01, ((leg.iv || ctx.fallbackIv || 15) + (ctx.ivShift || 0)) / 100);
      const g = useBroker ? leg.greeks : bsGreeks(leg.type, S, leg.strike, t, sigma);
      const row = { leg, qty: q, delta: g.delta * q, gamma: g.gamma * q, theta: g.theta * q, vega: g.vega * q };
      for (const k of Object.keys(total)) total[k] += row[k];
      return row;
    });
    return { rows, total };
  }

  function sigmaBands(ctx, atMs) {
    const t = yearsBetween(ctx.nowMs, atMs);
    const sd = Math.max(0.01, (ctx.atmIv || ctx.fallbackIv || 15) / 100) * Math.sqrt(t);
    return { one: [ctx.spot * Math.exp(-sd), ctx.spot * Math.exp(sd)], two: [ctx.spot * Math.exp(-2 * sd), ctx.spot * Math.exp(2 * sd)] };
  }

  /* Ready-made strategies. Strikes are in WIDTHS from ATM (a width is two
   * strike steps: 100 points on NIFTY), so one recipe fits every index. */
  const TEMPLATES = [
    { id: 'long-call', name: 'Buy Call', view: 'bullish', legs: [['BUY', 'CE', 0]] },
    { id: 'short-put', name: 'Sell Put', view: 'bullish', legs: [['SELL', 'PE', 0]] },
    { id: 'bull-call-spread', name: 'Bull Call Spread', view: 'bullish', legs: [['BUY', 'CE', 0], ['SELL', 'CE', 1]] },
    { id: 'bull-put-spread', name: 'Bull Put Spread', view: 'bullish', legs: [['SELL', 'PE', 0], ['BUY', 'PE', -1]] },
    { id: 'call-ratio-back', name: 'Call Ratio Back Spread', view: 'bullish', legs: [['SELL', 'CE', 0], ['BUY', 'CE', 1, 2]] },
    { id: 'long-synthetic', name: 'Long Synthetic', view: 'bullish', legs: [['BUY', 'CE', 0], ['SELL', 'PE', 0]] },
    { id: 'long-put', name: 'Buy Put', view: 'bearish', legs: [['BUY', 'PE', 0]] },
    { id: 'short-call', name: 'Sell Call', view: 'bearish', legs: [['SELL', 'CE', 0]] },
    { id: 'bear-put-spread', name: 'Bear Put Spread', view: 'bearish', legs: [['BUY', 'PE', 0], ['SELL', 'PE', -1]] },
    { id: 'bear-call-spread', name: 'Bear Call Spread', view: 'bearish', legs: [['SELL', 'CE', 0], ['BUY', 'CE', 1]] },
    { id: 'put-ratio-back', name: 'Put Ratio Back Spread', view: 'bearish', legs: [['SELL', 'PE', 0], ['BUY', 'PE', -1, 2]] },
    { id: 'short-synthetic', name: 'Short Synthetic', view: 'bearish', legs: [['SELL', 'CE', 0], ['BUY', 'PE', 0]] },
    { id: 'short-straddle', name: 'Short Straddle', view: 'neutral', legs: [['SELL', 'CE', 0], ['SELL', 'PE', 0]] },
    { id: 'short-strangle', name: 'Short Strangle', view: 'neutral', legs: [['SELL', 'CE', 1], ['SELL', 'PE', -1]] },
    { id: 'iron-condor', name: 'Iron Condor', view: 'neutral', legs: [['SELL', 'CE', 1], ['BUY', 'CE', 2], ['SELL', 'PE', -1], ['BUY', 'PE', -2]] },
    { id: 'iron-butterfly', name: 'Iron Butterfly', view: 'neutral', legs: [['SELL', 'CE', 0], ['SELL', 'PE', 0], ['BUY', 'CE', 1], ['BUY', 'PE', -1]] },
    { id: 'call-butterfly', name: 'Call Butterfly', view: 'neutral', legs: [['BUY', 'CE', -1], ['SELL', 'CE', 0, 2], ['BUY', 'CE', 1]] },
    { id: 'call-calendar', name: 'Call Calendar', view: 'neutral', legs: [['SELL', 'CE', 0], ['BUY', 'CE', 0, 1, 1]] },
    { id: 'long-straddle', name: 'Long Straddle', view: 'volatile', legs: [['BUY', 'CE', 0], ['BUY', 'PE', 0]] },
    { id: 'long-strangle', name: 'Long Strangle', view: 'volatile', legs: [['BUY', 'CE', 1], ['BUY', 'PE', -1]] },
    { id: 'long-iron-condor', name: 'Long Iron Condor', view: 'volatile', legs: [['BUY', 'CE', 1], ['SELL', 'CE', 2], ['BUY', 'PE', -1], ['SELL', 'PE', -2]] },
    { id: 'reverse-iron-fly', name: 'Reverse Iron Butterfly', view: 'volatile', legs: [['BUY', 'CE', 0], ['BUY', 'PE', 0], ['SELL', 'CE', 1], ['SELL', 'PE', -1]] },
  ];

  /* A template's legs on a real chain: [side, type, widths, lots=1, expiryIndex=0]. */
  function templateLegs(template, { atm, step, expiries, lots = 1 }) {
    const width = 2 * step;
    return template.legs.map(([side, type, widths, mult = 1, expiryIndex = 0]) => ({
      side,
      type,
      strike: atm + widths * width,
      expiry: expiries[Math.min(expiryIndex, expiries.length - 1)],
      lots: lots * mult,
    }));
  }

  return {
    RISK_FREE, normCdf, bsPrice, bsGreeks, expiryCloseMs, yearsBetween, intrinsic,
    legValue, pnlAt, firstExpiryMs, sameExpiry, priceRange, series, expiryStats,
    probabilityOfProfit, netPremium, currentPnl, positionGreeks, sigmaBands,
    signedQty, activeLegs, TEMPLATES, templateLegs,
  };
});
