// Run by tests/test_option_builder_math.py under node. Prints one JSON line.
const M = require('../static/option-builder-math.js');

const results = [];
function check(name, ok, detail) { results.push({ name, ok: !!ok, detail: ok ? '' : String(detail) }); }
function near(a, b, tol) { return Math.abs(a - b) <= tol; }

const nowMs = Date.UTC(2026, 9, 6, 4, 0); // 09:30 IST, 6 Oct 2026
const expiry = '2026-10-13';
const ctx = { spot: 25000, nowMs, lotSize: 75, atmIv: 12, fallbackIv: 12 };
const leg = (side, type, strike, entry, extra = {}) => ({ side, type, strike, expiry, lots: 1, entry, iv: 12, ltp: 0, ...extra });

// Black-Scholes against put-call parity: C - P = S - K e^{-rt}
{
  const t = 0.1, s = 0.2, S = 25000, K = 25200;
  const c = M.bsPrice('CE', S, K, t, s), p = M.bsPrice('PE', S, K, t, s);
  check('put-call parity', near(c - p, S - K * Math.exp(-M.RISK_FREE * t), 1e-6), c - p);
  check('a textbook call', near(M.bsPrice('CE', 100, 100, 1, 0.2, 0.05), 10.4506, 1e-3), M.bsPrice('CE', 100, 100, 1, 0.2, 0.05));
  const g = M.bsGreeks('CE', 100, 100, 1, 0.2, 0.05);
  check('textbook call delta', near(g.delta, 0.6368, 1e-3), g.delta);
}

// An expiry is 15:30 IST, i.e. 10:00 UTC
check('expiry close is 15:30 IST', M.expiryCloseMs('2026-10-13') === Date.UTC(2026, 9, 13, 10, 0), M.expiryCloseMs('2026-10-13'));

// Bull call spread: debit 60, width 200 -> max loss 60*75, max profit 140*75, breakeven 25060
{
  const legs = [leg('BUY', 'CE', 25000, 150), leg('SELL', 'CE', 25200, 90)];
  const st = M.expiryStats(legs, ctx);
  check('bull call max profit', near(st.maxProfit, 140 * 75, 1e-6), st.maxProfit);
  check('bull call max loss', near(st.maxLoss, -60 * 75, 1e-6), st.maxLoss);
  check('bull call one breakeven', st.breakevens.length === 1 && near(st.breakevens[0], 25060, 1e-6), JSON.stringify(st.breakevens));
  check('bull call exact', st.exact === true, st.exact);
  check('bull call is a debit', near(M.netPremium(legs, 75), -60 * 75, 1e-9), M.netPremium(legs, 75));
}

// Short straddle: unlimited loss upside, two breakevens at K +/- credit
{
  const legs = [leg('SELL', 'CE', 25000, 200), leg('SELL', 'PE', 25000, 180)];
  const st = M.expiryStats(legs, ctx);
  check('straddle loss unlimited', st.maxLoss === -Infinity, st.maxLoss);
  check('straddle max profit = credit', near(st.maxProfit, 380 * 75, 1e-6), st.maxProfit);
  const be = st.breakevens.slice().sort((a, b) => a - b);
  check('straddle breakevens', be.length === 2 && near(be[0], 24620, 1e-6) && near(be[1], 25380, 1e-6), JSON.stringify(be));
}

// Long call: unlimited profit, loss = premium
{
  const st = M.expiryStats([leg('BUY', 'CE', 25000, 120)], ctx);
  check('long call profit unlimited', st.maxProfit === Infinity, st.maxProfit);
  check('long call max loss', near(st.maxLoss, -120 * 75, 1e-6), st.maxLoss);
}

// Long put: profit is bounded by S -> 0, not unlimited
{
  const st = M.expiryStats([leg('BUY', 'PE', 25000, 100)], ctx);
  check('long put profit is finite', Number.isFinite(st.maxProfit) && near(st.maxProfit, (25000 - 0.01 - 100) * 75, 1), st.maxProfit);
}

// Iron condor: both sides bounded
{
  const legs = [leg('SELL', 'CE', 25200, 60), leg('BUY', 'CE', 25400, 20), leg('SELL', 'PE', 24800, 55), leg('BUY', 'PE', 24600, 18)];
  const st = M.expiryStats(legs, ctx);
  check('condor max profit', near(st.maxProfit, 77 * 75, 1e-6), st.maxProfit);
  check('condor max loss', near(st.maxLoss, -(200 - 77) * 75, 1e-6), st.maxLoss);
  check('condor two breakevens', st.breakevens.length === 2, JSON.stringify(st.breakevens));
  const pop = M.probabilityOfProfit(legs, ctx);
  check('condor POP is a probability', pop > 0.2 && pop < 0.95, pop);
}

// POP of a long ATM call is a bit under 50% (premium must be recovered)
{
  const pop = M.probabilityOfProfit([leg('BUY', 'CE', 25000, 150)], ctx);
  check('long ATM call POP < 0.5', pop > 0.2 && pop < 0.5, pop);
}

// The target curve passes through today's real P&L at today's price
{
  const l = leg('BUY', 'CE', 25000, 150, { ltp: 172 });
  const pnl = M.pnlAt([l], 25000, nowMs, ctx);
  check('today curve hits today\'s P&L', near(pnl, (172 - 150) * 75, 1e-6), pnl);
  const atExpiry = M.pnlAt([l], 25300, M.expiryCloseMs(expiry), ctx);
  check('expiry curve is intrinsic', near(atExpiry, (300 - 150) * 75, 1e-6), atExpiry);
}

// A disabled leg is ignored
{
  const legs = [leg('BUY', 'CE', 25000, 150), { ...leg('SELL', 'CE', 25200, 90), enabled: false }];
  check('disabled leg ignored', M.expiryStats(legs, ctx).maxProfit === Infinity, M.expiryStats(legs, ctx).maxProfit);
}

// Templates land on real strikes
{
  const t = M.TEMPLATES.find((x) => x.id === 'iron-condor');
  const legs = M.templateLegs(t, { atm: 25000, step: 50, expiries: ['2026-10-13', '2026-10-20'] });
  check('condor template strikes', JSON.stringify(legs.map((l) => l.strike)) === JSON.stringify([25100, 25200, 24900, 24800]), JSON.stringify(legs.map((l) => l.strike)));
  const cal = M.templateLegs(M.TEMPLATES.find((x) => x.id === 'call-calendar'), { atm: 25000, step: 50, expiries: ['2026-10-13', '2026-10-20'] });
  check('calendar uses the next expiry', cal[1].expiry === '2026-10-20', cal[1].expiry);
}

// Multi-expiry strategy still gives stats (approximate)
{
  const legs = [leg('SELL', 'CE', 25000, 150), { ...leg('BUY', 'CE', 25000, 230), expiry: '2026-10-20', ltp: 230 }];
  const st = M.expiryStats(legs, ctx);
  check('calendar stats approximate', st.exact === false && Number.isFinite(st.maxProfit), JSON.stringify(st));
}

console.log(JSON.stringify(results));
