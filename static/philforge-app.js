// Protected mutations are challenged by the server. Intercept only that
// explicit response, obtain a path-bound single-use token, and retry once.
const _philForgeNativeFetch = window.fetch.bind(window);
let _actionAuthorizationPrompt = null;

function _actionLabel(actionClass) {
  return ({
    live_trading: 'change live trading state',
    broker_order: 'send or cancel a broker order',
    live_scalp: 'change a live scalp position',
    broker_credentials: 'change broker credentials',
    account_security: 'change account security',
    admin_account: 'change another account',
  })[actionClass] || 'complete this protected action';
}

function _promptForActionAuthorization(challenge) {
  if (_actionAuthorizationPrompt) return _actionAuthorizationPrompt;
  _actionAuthorizationPrompt = new Promise((resolve) => {
    const modal = document.getElementById('action-auth-modal');
    const form = document.getElementById('action-auth-form');
    const password = document.getElementById('action-auth-password');
    const totp = document.getElementById('action-auth-totp');
    const status = document.getElementById('action-auth-status');
    const submit = document.getElementById('action-auth-submit');
    const cancelButtons = [document.getElementById('action-auth-close'), document.getElementById('action-auth-cancel')];
    const previousFocus = document.activeElement;
    document.getElementById('action-auth-description').textContent = `Confirm your identity once to ${_actionLabel(challenge.action_class)}.`;
    password.value = '';
    totp.value = '';
    status.textContent = '';
    modal.classList.add('open');
    password.focus();

    let finished = false;
    const finish = (value) => {
      if (finished) return;
      finished = true;
      modal.classList.remove('open');
      form.removeEventListener('submit', onSubmit);
      cancelButtons.forEach(btn => btn.removeEventListener('click', onCancel));
      document.removeEventListener('keydown', onKeyDown);
      _actionAuthorizationPrompt = null;
      if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus();
      resolve(value);
    };
    const onCancel = () => finish(null);
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        finish(null);
      }
    };
    const onSubmit = async (event) => {
      event.preventDefault();
      const code = totp.value.replace(/\D/g, '').slice(0, 6);
      if (!password.value || !/^\d{6}$/.test(code)) {
        status.textContent = 'Enter your password and a 6-digit authenticator code.';
        return;
      }
      submit.disabled = true;
      status.textContent = 'Verifying…';
      try {
        const response = await _philForgeNativeFetch('/api/auth/action-token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            password: password.value,
            totp: code,
            action_class: challenge.action_class,
            target_method: challenge.target_method,
            target_path: challenge.target_path,
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.action_token) {
          status.textContent = data.detail || 'Verification failed. Use a fresh authenticator code.';
          totp.value = '';
          totp.focus();
          return;
        }
        finish(data.action_token);
      } catch (error) {
        status.textContent = 'Could not reach the server. Try again.';
      } finally {
        submit.disabled = false;
      }
    };
    totp.oninput = () => { totp.value = totp.value.replace(/\D/g, '').slice(0, 6); };
    form.addEventListener('submit', onSubmit);
    cancelButtons.forEach(btn => btn.addEventListener('click', onCancel));
    document.addEventListener('keydown', onKeyDown);
  });
  return _actionAuthorizationPrompt;
}

window.fetch = async function philForgeProtectedFetch(input, init) {
  const request = new Request(input, init);
  const response = await _philForgeNativeFetch(request.clone());
  if (response.status !== 428 || request.method === 'GET') return response;
  const challenge = await response.clone().json().catch(() => ({}));
  if (challenge.code === 'mfa_enrollment_required') {
    toast(challenge.detail || 'Set up an authenticator in Account Settings first.', 'warn');
    openAccountModal();
    return response;
  }
  if (challenge.code !== 'action_authorization_required') return response;
  const actionToken = await _promptForActionAuthorization(challenge);
  if (!actionToken) return response;
  const headers = new Headers(request.headers);
  headers.set('X-PhilForge-Action-Token', actionToken);
  return _philForgeNativeFetch(new Request(request, { headers }));
};

// ══════════════════════════════════════════════════════════════
//  SVG ICON SYSTEM — Modern trading platform icons
// ══════════════════════════════════════════════════════════════
const ICO = {
  // s = size in px (default 16)
  _s: (path, s=16, extra='') => `<span class="ico"><svg width="${s}" height="${s}" viewBox="0 0 24 24"${extra}>${path}</svg></span>`,
  grid:     (s) => ICO._s('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>', s),
  briefcase:(s) => ICO._s('<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/><line x1="12" y1="12" x2="12" y2="12.01"/>', s),
  wrench:   (s) => ICO._s('<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94L6.73 20.2a2 2 0 0 1-2.83-2.83l6.73-6.73a6 6 0 0 1 7.94-7.94L14.7 6.3z"/>', s),
  chart:    (s) => ICO._s('<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>', s),
  pulse:    (s) => ICO._s('<path d="M3 12h4l3-9 4 18 3-9h4"/>', s),
  bolt:     (s) => ICO._s('<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" fill="currentColor" stroke="none"/>', s),
  memo:     (s) => ICO._s('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>', s),
  bot:      (s) => ICO._s('<rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><line x1="12" y1="7" x2="12" y2="11"/><circle cx="8" cy="16" r="1" fill="currentColor" stroke="none"/><circle cx="16" cy="16" r="1" fill="currentColor" stroke="none"/>', s),
  flame:    (s) => ICO._s('<path d="M12 2c.5 4-3 6-3 10a5 5 0 1 0 10 0c0-5-5-6-5-10a5 5 0 0 0-2 0z" fill="currentColor" stroke="none" opacity="0.85"/>', s),
  trophy:   (s) => ICO._s('<path d="M6 9H3V5a1 1 0 0 1 1-1h2"/><path d="M18 9h3V5a1 1 0 0 0-1-1h-2"/><path d="M6 4h12v6a6 6 0 0 1-12 0V4z"/><path d="M9 20h6"/><path d="M12 16v4"/>', s),
  loss:     (s) => ICO._s('<polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/>', s),
  puzzle:   (s) => ICO._s('<path d="M19.439 7.85c-.049.322.059.648.289.878l1.568 1.568c.47.47.706 1.087.706 1.704s-.235 1.233-.706 1.704l-1.611 1.611a.98.98 0 0 1-.837.276c-.47-.07-.802-.48-.968-.925a2.5 2.5 0 1 0-3.214 3.214c.446.166.855.497.925.968a.979.979 0 0 1-.276.837l-1.61 1.611a2.404 2.404 0 0 1-1.705.706 2.404 2.404 0 0 1-1.704-.706l-1.568-1.568a1.026 1.026 0 0 0-.877-.29c-.493.074-.84.504-1.02.968a2.5 2.5 0 1 1-3.237-3.237c.464-.18.894-.527.967-1.02a1.026 1.026 0 0 0-.289-.877l-1.568-1.568A2.404 2.404 0 0 1 1.998 12c0-.617.236-1.234.706-1.704L4.23 8.77c.24-.24.581-.353.917-.303.515.077.877.528 1.073 1.01a2.5 2.5 0 1 0 3.259-3.259c-.482-.196-.933-.558-1.01-1.073-.05-.336.062-.676.303-.917l1.525-1.525A2.404 2.404 0 0 1 12 2c.617 0 1.234.236 1.704.706l1.568 1.568c.23.23.556.338.877.29.493-.074.84-.504 1.02-.968a2.5 2.5 0 1 1 3.237 3.237c-.464.18-.894.527-.967 1.02z"/>', s),
  runs:     (s) => ICO._s('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 3v18"/>', s),
  gear:     (s) => ICO._s('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1.08-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1.08 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1.08 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1.08z"/>', s),
  clip:     (s) => ICO._s('<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/>', s),
  indicators:(s) => ICO._s('<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>', s),
  rocket:   (s) => ICO._s('<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>', s),
  save:     (s) => ICO._s('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>', s),
  leg:      (s) => ICO._s('<path d="M8 3v6.5a2 2 0 0 1-2 2H3"/><path d="M16 3v6.5a2 2 0 0 0 2 2h3"/><line x1="8" y1="9.5" x2="8" y2="21"/><line x1="16" y1="9.5" x2="16" y2="21"/><line x1="6" y1="21" x2="18" y2="21"/>', s),
  antenna:  (s) => ICO._s('<path d="M2 12L12 2l10 10"/><path d="M12 2v20"/><circle cx="12" cy="12" r="4"/>', s),
  moon:     (s) => ICO._s('<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>', s),
  sun:      (s) => ICO._s('<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>', s),
  logout:   (s) => ICO._s('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>', s),
  stop:     (s) => ICO._s('<circle cx="12" cy="12" r="10" fill="currentColor" stroke="none" opacity="0.15"/><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>', s),
  download: (s) => ICO._s('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>', s),
  eye:      (s) => ICO._s('<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>', s),
  folder:   (s) => ICO._s('<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>', s),
  search:   (s) => ICO._s('<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>', s),
  edit:     (s) => ICO._s('<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>', s),
  shuffle:  (s) => ICO._s('<polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/>', s),
  refresh:  (s) => ICO._s('<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>', s),
  calendar: (s) => ICO._s('<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>', s),
  heatmap:  (s) => ICO._s('<rect x="3" y="3" width="7" height="7" rx="1" fill="currentColor" opacity="0.8" stroke="none"/><rect x="14" y="3" width="7" height="7" rx="1" fill="currentColor" opacity="0.3" stroke="none"/><rect x="3" y="14" width="7" height="7" rx="1" fill="currentColor" opacity="0.5" stroke="none"/><rect x="14" y="14" width="7" height="7" rx="1" fill="currentColor" opacity="0.15" stroke="none"/>', s),
  play:     (s) => ICO._s('<polygon points="5 3 19 12 5 21 5 3" fill="currentColor" stroke="none"/>', s),
  pause:    (s) => ICO._s('<rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none"/>', s),
  sqstop:   (s) => ICO._s('<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>', s),
  warn:     (s) => ICO._s('<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>', s),
  alert:    (s) => ICO._s('<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>', s),
  hour:     (s) => ICO._s('<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>', s),
  doc:      (s) => ICO._s('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>', s),
  money:    (s) => ICO._s('<line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>', s),
  ban:      (s) => ICO._s('<circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>', s),
  siren:    (s) => ICO._s('<path d="M12 2v4"/><path d="M5 5l2.83 2.83"/><path d="M19 5l-2.83 2.83"/><path d="M4 12H2"/><path d="M22 12h-2"/><rect x="4" y="14" width="16" height="6" rx="2"/><circle cx="12" cy="12" r="4"/>', s),
  trend:    (s) => ICO._s('<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>', s),
  cross:    (s) => ICO._s('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>', s),
  check:    (s) => ICO._s('<polyline points="20 6 9 17 4 12"/>', s),
  trash:    (s) => ICO._s('<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>', s),
  shield:   (s) => ICO._s('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>', s),
  crosshair:(s) => ICO._s('<circle cx="12" cy="12" r="10"/><line x1="22" y1="12" x2="18" y2="12"/><line x1="6" y1="12" x2="2" y2="12"/><line x1="12" y1="6" x2="12" y2="2"/><line x1="12" y1="22" x2="12" y2="18"/>', s),
  zap:      (s) => ICO._s('<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" fill="currentColor" stroke="none"/>', s),
  brain:    (s) => ICO._s('<path d="M9.5 2A5.5 5.5 0 0 0 5 7.5c0 .68.09 1.34.25 1.96A4.5 4.5 0 0 0 2 13.5 4.5 4.5 0 0 0 6.5 18H8v4h3V7.5A5.5 5.5 0 0 0 9.5 2z"/><path d="M14.5 2A5.5 5.5 0 0 1 19 7.5c0 .68-.09 1.34-.25 1.96A4.5 4.5 0 0 1 22 13.5 4.5 4.5 0 0 1 17.5 18H16v4h-3V7.5A5.5 5.5 0 0 1 14.5 2z"/>', s),
  sword:    (s) => ICO._s('<path d="M14.5 17.5L3 6V3h3l11.5 11.5"/><path d="M13 19l6-6"/><path d="M16 16l4 4"/><path d="M19 21l2-2"/>', s),
  target:   (s) => ICO._s('<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>', s),
  compass:  (s) => ICO._s('<circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" fill="currentColor" stroke="none"/>', s),
  candle:   (s) => ICO._s('<line x1="6" y1="2" x2="6" y2="6"/><rect x="3" y="6" width="6" height="8" rx="1" fill="currentColor" opacity="0.6"/><line x1="6" y1="14" x2="6" y2="18"/><line x1="14" y1="4" x2="14" y2="9"/><rect x="11" y="9" width="6" height="7" rx="1" fill="currentColor" opacity="0.9"/><line x1="14" y1="16" x2="14" y2="21"/><line x1="20" y1="6" x2="20" y2="10"/><rect x="18" y="10" width="4" height="5" rx="1" fill="currentColor" opacity="0.4"/><line x1="20" y1="15" x2="20" y2="19"/>', s),
};

// Inject nav tab icons
(function initNavIcons() {
  const map = {dashboard: ICO.grid(16), portfolio: ICO.briefcase(16), builder: ICO.wrench(16), results: ICO.chart(16), insights: ICO.compass(16), terminal: ICO.money(16), charts: ICO.memo(16)};
  Object.keys(map).forEach(k => { const el = document.getElementById('ico-' + k); if (el) el.innerHTML = map[k]; });
  const subHeatmap = document.getElementById('ico-sub-heatmap');
  if (subHeatmap) subHeatmap.innerHTML = ICO.heatmap(14);
  const subStudy = document.getElementById('ico-sub-study');
  if (subStudy) subStudy.innerHTML = ICO.brain(14);
  const terminalHead = document.getElementById('stock-terminal-head-ico');
  if (terminalHead) terminalHead.innerHTML = ICO.money(14);
})();

// ── Site-styled date/time picker ──────────────────────────────────
// Native picker popups cannot inherit the PhilForge visual system. Keep the
// ISO values expected by each form, while giving every calendar field a
// consistent picker and a useful blank-state prompt.
(() => {
  const TARGET = 'input[type="date"], input[type="datetime-local"], .pf-cascade-datetime';
  const PLACEHOLDERS = {
    'bt-from-date': 'Select backtest start date',
    'bt-to-date': 'Select backtest end date',
    'terminal-cascade-mother-timestamp': 'YYYY-MM-DDTHH:MM · IST',
    'candle-entry-mother-timestamp': 'YYYY-MM-DDTHH:MM · IST',
    'cascade-options-mother-timestamp': 'YYYY-MM-DDTHH:MM · IST',
    'cascade-mother-timestamp': 'YYYY-MM-DDTHH:MM · IST',
  };
  let popover;
  let activeInput;
  let visibleMonth;
  let selectedDate;

  const pad = value => String(value).padStart(2, '0');
  const parseValue = value => {
    const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/);
    if (match) return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4] || 0), Number(match[5] || 0));
    return new Date();
  };
  const iso = value => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}`;
  const dateOnly = value => `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const close = () => { if (popover) popover.hidden = true; activeInput = null; };

  function createPopover() {
    popover = document.createElement('div');
    popover.className = 'pf-cascade-calendar';
    popover.hidden = true;
    popover.setAttribute('role', 'dialog');
    popover.setAttribute('aria-label', 'Choose candle date and time');
    document.body.append(popover);
  }

  function render() {
    if (!popover || !activeInput || !visibleMonth || !selectedDate) return;
    const year = visibleMonth.getFullYear();
    const month = visibleMonth.getMonth();
    const first = new Date(year, month, 1);
    const gridStart = new Date(year, month, 1 - first.getDay());
    const now = new Date();
    const step = Math.max(Number(activeInput.dataset.pfCalendarStep || activeInput.getAttribute('step') || 300) / 60, 1);
    const dateOnlyPicker = activeInput.dataset.pfCalendarKind === 'date';
    let minutes = [];
    // A field can name the exact minutes a candle of its timeframe can open on
    // (NSE 1H bars only ever open at :15), which beats a fixed step: a 5-minute
    // step cannot express a 1m mother at all, and an every-minute list is 60
    // wrong answers when only four are real.
    const named = String(activeInput.dataset.pfCalendarMinutes || '').trim();
    if (named) {
      minutes = named.split(',').map(Number).filter(value => Number.isInteger(value) && value >= 0 && value < 60);
    }
    if (!minutes.length) {
      const minuteStep = step >= 60 ? 5 : step;
      for (let minute = 0; minute < 60; minute += minuteStep) minutes.push(minute);
    }
    // Never hide the minute already in the box — a value set before the
    // timeframe changed must stay selectable rather than silently snap.
    if (!minutes.includes(selectedDate.getMinutes())) minutes = [...minutes, selectedDate.getMinutes()].sort((a, b) => a - b);
    const monthName = new Intl.DateTimeFormat('en-IN', { month: 'long', year: 'numeric' }).format(visibleMonth);
    const weekdays = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(day => `<span>${day}</span>`).join('');
    // A mother candle is a bar that has already printed, so a field marked
    // no-future must not offer one that has not. Nothing stopped it before, and
    // picking a date weeks ahead came back from the server as a bare "(400)".
    const noFuture = activeInput.dataset.pfCalendarNoFuture === '1';
    let days = '';
    for (let index = 0; index < 42; index += 1) {
      const day = new Date(gridStart);
      day.setDate(gridStart.getDate() + index);
      const classes = ['pf-cascade-calendar-day'];
      const ahead = noFuture && !sameDay(day, now) && day > now;
      if (day.getMonth() !== month) classes.push('is-outside');
      if (sameDay(day, now)) classes.push('is-today');
      if (sameDay(day, selectedDate)) classes.push('is-selected');
      days += `<button type="button" class="${classes.join(' ')}"${ahead ? ' disabled aria-disabled="true" title="A mother candle cannot be in the future"' : ''} data-pf-calendar-day="${day.getFullYear()}-${day.getMonth()}-${day.getDate()}">${day.getDate()}</button>`;
    }
    const hours = Array.from({ length: 24 }, (_, hour) => `<option value="${hour}" ${hour === selectedDate.getHours() ? 'selected' : ''}>${pad(hour)}</option>`).join('');
    const minuteOptions = minutes.map(minute => `<option value="${minute}" ${minute === selectedDate.getMinutes() ? 'selected' : ''}>${pad(minute)}</option>`).join('');
    const timeControls = dateOnlyPicker ? '' : `<div class="pf-cascade-calendar-time"><select aria-label="Hour" data-pf-calendar-hour>${hours}</select><select aria-label="Minute" data-pf-calendar-minute>${minuteOptions}</select></div>`;
    popover.innerHTML = `<div class="pf-cascade-calendar-head"><button class="pf-cascade-calendar-nav" type="button" data-pf-calendar-nav="-1" aria-label="Previous month">‹</button><span>${monthName}</span><button class="pf-cascade-calendar-nav" type="button" data-pf-calendar-nav="1" aria-label="Next month">›</button></div><div class="pf-cascade-calendar-weekdays">${weekdays}</div><div class="pf-cascade-calendar-days">${days}</div>${timeControls}<div class="pf-cascade-calendar-actions"><button class="btn btn-sm" type="button" data-pf-calendar-cancel>Cancel</button><button class="btn btn-sm" type="button" data-pf-calendar-apply>Apply</button></div>`;
  }

  function open(input) {
    if (!popover) createPopover();
    activeInput = input;
    selectedDate = parseValue(input.value);
    visibleMonth = new Date(selectedDate.getFullYear(), selectedDate.getMonth(), 1);
    render();
    popover.hidden = false;
    const rect = input.getBoundingClientRect();
    const width = Math.min(332, window.innerWidth - 24);
    popover.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`;
    popover.style.top = `${Math.min(rect.bottom + 8, window.innerHeight - 430)}px`;
  }

  function setup(input) {
    if (input.dataset.pfCalendarReady) return;
    input.dataset.pfCalendarReady = '1';
    input.dataset.pfCalendarKind = input.type;
    input.dataset.pfCalendarStep = input.getAttribute('step') || '300';
    const initialValue = input.value;
    input.type = 'text';
    input.value = initialValue;
    input.readOnly = true;
    input.classList.add('pf-cascade-datetime');
    if (!input.placeholder) input.placeholder = PLACEHOLDERS[input.id] || 'Select date';
    input.setAttribute('aria-haspopup', 'dialog');
    input.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); open(input); });
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(input); }
    });
  }

  document.addEventListener('click', event => {
    if (popover && !popover.contains(event.target)) { close(); return; }
    const action = event.target.closest('[data-pf-calendar-nav], [data-pf-calendar-day], [data-pf-calendar-apply], [data-pf-calendar-cancel]');
    if (!action) return;
    if (!activeInput) return;
    if (action.dataset.pfCalendarNav) {
      visibleMonth.setMonth(visibleMonth.getMonth() + Number(action.dataset.pfCalendarNav));
      render();
    } else if (action.dataset.pfCalendarDay) {
      const [year, month, day] = action.dataset.pfCalendarDay.split('-').map(Number);
      selectedDate.setFullYear(year, month, day);
      visibleMonth = new Date(year, month, 1);
      render();
    } else if (action.hasAttribute('data-pf-calendar-apply')) {
      if (activeInput.dataset.pfCalendarKind !== 'date') {
        const hour = Number(popover.querySelector('[data-pf-calendar-hour]').value);
        const minute = Number(popover.querySelector('[data-pf-calendar-minute]').value);
        selectedDate.setHours(hour, minute, 0, 0);
      }
      // Disabling the future day buttons is not enough on its own: the field can
      // arrive pre-filled with a future value, and Apply would write it straight
      // back out without the grid ever being touched.
      if (activeInput.dataset.pfCalendarNoFuture === '1' && selectedDate > new Date()) {
        selectedDate = new Date();
        selectedDate.setSeconds(0, 0);
      }
      activeInput.value = activeInput.dataset.pfCalendarKind === 'date' ? dateOnly(selectedDate) : iso(selectedDate);
      activeInput.dispatchEvent(new Event('input', { bubbles: true }));
      activeInput.dispatchEvent(new Event('change', { bubbles: true }));
      close();
    } else if (action.hasAttribute('data-pf-calendar-cancel')) {
      close();
    }
  });

  document.addEventListener('change', event => {
    if (!popover || !activeInput || !event.target.matches('[data-pf-calendar-hour], [data-pf-calendar-minute]')) return;
    selectedDate.setHours(Number(popover.querySelector('[data-pf-calendar-hour]').value), Number(popover.querySelector('[data-pf-calendar-minute]').value), 0, 0);
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  document.addEventListener('DOMContentLoaded', () => document.querySelectorAll(TARGET).forEach(setup));
})();

(function initShellIcons() {
  const iconMap = {
    'admin-btn': ICO.shield(16),
    'account-modal-ico': ICO.gear(18),
    'account-summary-ico': ICO.clip(16),
    'account-broker-ico': ICO.money(16),
    'account-mfa-ico': ICO.shield(16),
    'account-password-ico': ICO.shield(16),
    'admin-modal-ico': ICO.shield(18),
    'admin-create-ico': ICO.brain(16),
  };
  Object.entries(iconMap).forEach(([id, iconHtml]) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = iconHtml;
  });
  const installBtn = document.getElementById('install-app-btn');
  if (installBtn) {
    const iconSlot = installBtn.querySelector('.ico');
    if (iconSlot) iconSlot.innerHTML = ICO.download(16);
  }
})();

// ══════════════════════════════════════════════════════════════
//  CUSTOM CONFIRM DIALOG (replaces native browser confirm)
// ══════════════════════════════════════════════════════════════
function customConfirm(message, options = {}) {
  return new Promise(resolve => {
    const modal = document.getElementById('confirm-modal');
    const titleEl = document.getElementById('confirm-title');
    const msgEl = document.getElementById('confirm-message');
    const iconEl = document.getElementById('confirm-icon');
    const okBtn = document.getElementById('confirm-ok-btn');
    const cancelBtn = document.getElementById('confirm-cancel-btn');
    const inputEl = document.getElementById('confirm-input');

    titleEl.innerHTML = options.title || 'Confirm';      // internal ICO SVG only
    msgEl.innerHTML = message;                             // internal HTML only (no user input)
    iconEl.innerHTML = options.icon || ICO.warn(28);     // internal SVG generator only
    okBtn.textContent = options.okText || 'Confirm';
    cancelBtn.textContent = options.cancelText || 'Cancel';

    // Prompt mode: show input with prefill
    if (options.prompt) {
      inputEl.style.display = 'block';
      inputEl.type = options.promptType || 'text';
      inputEl.placeholder = options.promptPlaceholder || '';
      inputEl.value = options.promptValue || '';
      setTimeout(() => { inputEl.focus(); inputEl.select(); }, 60);
    } else {
      inputEl.style.display = 'none';
      inputEl.type = 'text';
      inputEl.placeholder = '';
      inputEl.value = '';
    }

    // Style the OK button based on type
    okBtn.className = options.danger ? 'btn-confirm-danger' : 'btn-confirm-ok';

    modal.classList.add('open');

    function cleanup() {
      modal.classList.remove('open');
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      modal.removeEventListener('click', onBackdrop);
      inputEl.removeEventListener('keydown', onEnter);
    }
    function onOk() { cleanup(); resolve(options.prompt ? inputEl.value.trim() : true); }
    function onCancel() { cleanup(); resolve(options.prompt ? null : false); }
    function onBackdrop(e) { if (e.target === modal) { cleanup(); resolve(options.prompt ? null : false); } }
    function onEnter(e) { if (e.key === 'Enter') { e.preventDefault(); onOk(); } }

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    modal.addEventListener('click', onBackdrop);
    if (options.prompt) inputEl.addEventListener('keydown', onEnter);

    if (!options.prompt) cancelBtn.focus();
  });
}

let _authUser = null;
let _userProfile = null;
let _adminUsers = [];
let _adminEngineRows = [];

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
}

function escapeJsSingleQuoted(value) {
  return String(value ?? '')
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/\r/g, '\\r')
    .replace(/\n/g, '\\n')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029');
}

// A value dropped into a JS single-quoted string that itself lives inside an
// HTML attribute (onclick="fn('HERE')") needs BOTH layers: the browser decodes
// HTML entities in the attribute BEFORE the JS runs, so escaping only the JS
// (or only the HTML) leaves a way out of the string. JS-escape first, then
// HTML-escape the result so no decoded quote can survive.
function escapeJsAttr(value) {
  return escapeHtml(escapeJsSingleQuoted(value));
}

function formatDateTimeLabel(value) {
  if (!value) return '—';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return String(value);
  return dt.toLocaleString('en-IN', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function applyStatusChip(el, text, tone) {
  if (!el) return;
  el.className = `status-chip ${tone || 'warn'}`;
  el.textContent = text;
}

function createStatusChip(text, tone) {
  const chip = document.createElement('span');
  chip.className = `status-chip ${tone || 'warn'}`;
  chip.textContent = text;
  return chip;
}

const PASSWORD_POLICY_HINT = 'Use at least 8 characters.';

function validatePasswordRule(password, label = 'Password') {
  const value = String(password || '');
  if (value.length >= 8) return '';
  return `${label} must be at least 8 characters.`;
}

async function logoutUser() {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (e) {}
  await purgeAuthenticatedShellCaches();
  location.reload();
}

async function purgeAuthenticatedShellCaches() {
  if (!('caches' in window)) return;
  try {
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key.startsWith('philforge-shell-')).map(key => caches.delete(key)));
  } catch (e) {}
}

async function handleUnauthorizedResponse(res) {
  if (!res || res.status !== 401) return false;
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (e) {}
  await purgeAuthenticatedShellCaches();
  location.reload();
  return true;
}

async function loadAuthContext() {
  try {
    const res = await fetch('/api/auth/status');
    const data = await res.json();
    if (!data.authenticated) {
      location.reload();
      return;
    }
    _authUser = data;
    document.getElementById('topbar-username').textContent = data.username || 'Account';
    document.getElementById('topbar-user-role').textContent = String(data.role || 'user').toUpperCase();
    const adminBtn = document.getElementById('admin-btn');
    if (adminBtn) adminBtn.style.display = data.role === 'admin' ? '' : 'none';
  } catch (e) {
    console.warn('Auth context failed:', e);
  }
}

function openAccountModal() {
  document.getElementById('account-modal').classList.add('open');
  document.getElementById('account-current-password').value = '';
  document.getElementById('account-new-password').value = '';
  document.getElementById('account-confirm-password').value = '';
  document.getElementById('account-mfa-password').value = '';
  document.getElementById('account-mfa-code').value = '';
  document.getElementById('account-mfa-enrollment').hidden = true;
  document.getElementById('account-mfa-verify-btn').hidden = true;
  document.getElementById('account-mfa-secret').textContent = '';
  document.getElementById('account-mfa-qr').removeAttribute('src');
  document.getElementById('account-mfa-uri').removeAttribute('href');
  loadUserProfile(true);
}

function closeAccountModal() {
  document.getElementById('account-modal').classList.remove('open');
  document.getElementById('account-mfa-password').value = '';
  document.getElementById('account-mfa-code').value = '';
  document.getElementById('account-mfa-secret').textContent = '';
  document.getElementById('account-mfa-qr').removeAttribute('src');
  document.getElementById('account-mfa-uri').removeAttribute('href');
  document.getElementById('account-mfa-enrollment').hidden = true;
  document.getElementById('account-mfa-verify-btn').hidden = true;
}

async function loadExecutionIpStatus(silent = true) {
  const refreshBtn = document.getElementById('account-ip-refresh-btn');
  const sourceEl = document.getElementById('account-ip-source');
  const clientEl = document.getElementById('account-ip-client');
  const serverEl = document.getElementById('account-ip-server');
  const dhanEl = document.getElementById('account-ip-dhan');
  const detectedEl = document.getElementById('account-ip-detected');
  const ordersEl = document.getElementById('account-ip-orders');
  const chipEl = document.getElementById('account-ip-status-chip');
  const statusEl = document.getElementById('account-ip-status-line');
  const hintEl = document.getElementById('account-ip-hint');
  if (!sourceEl || !clientEl || !serverEl || !dhanEl || !detectedEl || !ordersEl || !chipEl || !statusEl || !hintEl) return;

  if (refreshBtn) refreshBtn.disabled = true;
  applyStatusChip(chipEl, 'Checking', 'warn');
  statusEl.textContent = 'Comparing the server public IP with Dhan’s actual detected order IP…';
  if (!serverEl.textContent || serverEl.textContent === '—') serverEl.textContent = 'Checking…';
  if (!dhanEl.textContent || dhanEl.textContent === '—') dhanEl.textContent = 'Checking…';
  if (!detectedEl.textContent || detectedEl.textContent === '—') detectedEl.textContent = 'Checking…';
  if (!ordersEl.textContent || ordersEl.textContent === '—') ordersEl.textContent = 'Checking…';

  try {
    const res = await fetch('/api/user/execution-ip-status');
    if (await handleUnauthorizedResponse(res)) return false;
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.detail || data.message || 'IP check failed');

    sourceEl.textContent = data.source_label || '—';
    clientEl.textContent = data.client_id_masked || '—';
    serverEl.textContent = data.server_public_ip || '—';
    dhanEl.textContent = data.dhan_saved_ip || '—';
    detectedEl.textContent = data.dhan_detected_ip || '—';
    ordersEl.textContent = data.orders_allowed === true ? 'Allowed' : data.orders_allowed === false ? 'Blocked' : 'Unknown';

    let chipTone = 'warn';
    let chipText = 'Needs Review';
    let statusText = data.error || data.warning || 'Execution IP status loaded.';
    if (data.match) {
      chipTone = 'success';
      chipText = 'Orders Allowed';
      statusText = `Dhan currently detects ${data.dhan_detected_ip || data.server_public_ip} and orders are allowed for this broker account.`;
    } else if (data.orders_allowed === false && data.dhan_detected_ip && data.dhan_saved_ip) {
      chipTone = 'danger';
      chipText = 'Detected IP Mismatch';
      statusText = `Dhan detects ${data.dhan_detected_ip}, but the saved static IP is ${data.dhan_saved_ip}.`;
    } else if (data.server_public_ip && data.dhan_saved_ip) {
      chipTone = 'danger';
      chipText = 'IP Mismatch';
      statusText = `Server IP ${data.server_public_ip} does not match Dhan saved IP ${data.dhan_saved_ip}.`;
    } else if (data.error) {
      chipTone = data.check_ready ? 'danger' : 'warn';
      chipText = data.check_ready ? 'Check Failed' : 'Not Ready';
    }
    applyStatusChip(chipEl, chipText, chipTone);
    statusEl.textContent = statusText;

    const checkedAt = formatDateTimeLabel(data.checked_at);
    if (data.match) {
      hintEl.innerHTML = `Dhan static-IP check passed for this account. Checked ${escapeHtml(checkedAt)}.`;
    } else if (data.orders_allowed === false && data.dhan_detected_ip && data.dhan_saved_ip) {
      hintEl.innerHTML = `Dhan is currently seeing <strong>${escapeHtml(data.dhan_detected_ip)}</strong> for outbound orders, but this account has <strong>${escapeHtml(data.dhan_saved_ip)}</strong> saved. This usually means the server is reaching Dhan over IPv6 while the whitelisted static IP is IPv4. Checked ${escapeHtml(checkedAt)}.`;
    } else if (data.server_public_ip && data.dhan_saved_ip) {
      hintEl.innerHTML = `Update Dhan so the saved IP exactly matches the server public IP above, otherwise order APIs can fail with <strong>DH-905 Invalid IP</strong>. Checked ${escapeHtml(checkedAt)}.`;
    } else if (data.error) {
      hintEl.innerHTML = `${escapeHtml(data.error)}${data.warning ? ' • ' + escapeHtml(data.warning) : ''}${checkedAt !== '—' ? ' • Checked ' + escapeHtml(checkedAt) : ''}`;
    } else {
      hintEl.innerHTML = `${escapeHtml(data.warning || 'Execution IP status loaded.')}${checkedAt !== '—' ? ' • Checked ' + escapeHtml(checkedAt) : ''}`;
    }
    return !!data.match;
  } catch (e) {
    applyStatusChip(chipEl, 'Check Failed', 'danger');
    statusEl.textContent = e.message || 'Failed to load execution IP status';
    hintEl.textContent = 'Open Account Settings on the production server and refresh the IP check after updating Dhan.';
    if (!silent) toast(e.message || 'Failed to load execution IP status', 'danger');
    return false;
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

async function loadUserProfile(silent = true) {
  try {
    const res = await fetch('/api/user/profile');
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.detail || data.message || 'Failed to load profile');
    _userProfile = data;
    const user = data.user || {};
    const broker = data.broker || {};

    document.getElementById('account-profile-username').textContent = user.username || '—';
    document.getElementById('account-profile-role').textContent = String(user.role || 'user').toUpperCase();
    document.getElementById('account-profile-created').textContent = formatDateTimeLabel(user.created_at);
    document.getElementById('account-profile-login').textContent = formatDateTimeLabel(user.last_login);
    const mfaEnabled = !!user.mfa_enabled;
    applyStatusChip(document.getElementById('account-mfa-status-chip'), mfaEnabled ? 'Enabled' : 'Not Enabled', mfaEnabled ? 'success' : 'warn');
    document.getElementById('account-mfa-status-line').textContent = mfaEnabled
      ? `Authenticator protection enabled${user.mfa_enrolled_at ? ` since ${formatDateTimeLabel(user.mfa_enrolled_at)}` : ''}.`
      : 'Set up an authenticator before broker credentials or real-money actions can be changed.';
    const mfaStartBtn = document.getElementById('account-mfa-start-btn');
    mfaStartBtn.textContent = mfaEnabled ? 'Replace Authenticator' : 'Set Up Authenticator';
    document.getElementById('account-mfa-disable-btn').hidden = !mfaEnabled;
    document.getElementById('account-broker-client-id').value = broker.client_id || '';
    document.getElementById('account-broker-token').value = '';
    document.getElementById('account-broker-pin').value = '';
    document.getElementById('account-broker-totp').value = '';
    document.getElementById('account-ip-source').textContent = broker.source === 'user' ? 'Stored per-user broker account' : broker.source === 'global' ? 'Admin server fallback (.env)' : broker.source === 'partial' ? 'Partial broker credentials' : 'No active broker source';
    document.getElementById('account-ip-client').textContent = broker.client_id_masked || '—';
    document.getElementById('account-ip-server').textContent = 'Checking…';
    document.getElementById('account-ip-dhan').textContent = 'Checking…';
    document.getElementById('account-ip-detected').textContent = 'Checking…';
    document.getElementById('account-ip-orders').textContent = 'Checking…';
    applyStatusChip(document.getElementById('account-ip-status-chip'), 'Checking', 'warn');
    document.getElementById('account-ip-status-line').textContent = 'Comparing the server public IP with Dhan’s actual detected order IP…';
    document.getElementById('account-ip-hint').innerHTML = 'If Dhan’s detected IP does not match the saved static IP, Dhan order APIs can fail with <strong>DH-905 Invalid IP</strong>.';

    let chipTone = 'warn';
    let chipText = 'Broker Missing';
    if (!broker.encryption_ready) {
      chipTone = 'danger';
      chipText = 'Encryption Missing';
    } else if (broker.partial) {
      chipTone = 'danger';
      chipText = 'Broker Partial';
    } else if (broker.configured) {
      chipTone = 'success';
      chipText = broker.source === 'global' ? 'Admin Global Broker' : (broker.auto_refresh_ready ? 'Broker + Auto-Refresh' : 'Broker Stored');
    } else if (broker.source === 'global') {
      chipTone = 'success';
      chipText = 'Admin Global Broker';
    }
    applyStatusChip(document.getElementById('account-broker-status-chip'), chipText, chipTone);

    const statusLine = document.getElementById('account-broker-status-line');
    const sourceLabel = broker.source === 'user' ? 'stored per-user credentials' : broker.source === 'global' ? 'admin .env fallback' : 'no active broker source';
    const brokerBits = [`Source: <strong>${escapeHtml(sourceLabel)}</strong>`];
    if (broker.access_token_saved) brokerBits.push('token saved');
    if (broker.pin_saved) brokerBits.push('PIN saved');
    if (broker.totp_saved) brokerBits.push('TOTP saved');
    if (broker.auto_refresh_ready) brokerBits.push('<strong>auto-refresh ready</strong>');
    statusLine.innerHTML = brokerBits.join(' • ');

    const hint = document.getElementById('account-broker-hint');
    if (!broker.encryption_ready) {
      hint.textContent = 'This server does not have ENCRYPTION_KEY configured yet, so stored broker credentials are intentionally blocked until encrypted-at-rest storage is available.';
    } else if (broker.manage_locked) {
      hint.textContent = broker.manage_lock_reason || 'Broker settings are locked while live broker workflows are active.';
    } else if (broker.auto_refresh_ready) {
      hint.textContent = 'This account has stored Dhan PIN + TOTP Secret, so expired market-data tokens can auto-refresh. Leave Access Token, PIN, and TOTP blank to keep the current saved values.';
    } else if (_authUser && _authUser.role === 'admin' && broker.source === 'global' && !broker.configured) {
      hint.textContent = 'Admin fallback is currently coming from the server .env broker configuration. Saving credentials here will switch this account to stored per-user broker settings.';
    } else {
      hint.textContent = 'Save your own Dhan Client ID and Access Token here. Add Dhan PIN + TOTP Secret too if you want this user to auto-refresh expired tokens. Leave any secret field blank to keep the currently saved value.';
    }

    const locked = !!broker.manage_locked || !broker.encryption_ready;
    document.getElementById('account-broker-save-btn').disabled = locked;
    document.getElementById('account-broker-clear-btn').disabled = locked || (!broker.client_id && !broker.access_token_saved && !broker.pin_saved && !broker.totp_saved);
    document.getElementById('account-ip-refresh-btn').disabled = false;
    loadExecutionIpStatus(true);
    if (!silent) toast('Account settings loaded', 'success', 2200);
  } catch (e) {
    if (!silent) toast(e.message || 'Failed to load account settings', 'danger');
  }
}

async function startMfaEnrollment() {
  const password = document.getElementById('account-mfa-password').value;
  const code = document.getElementById('account-mfa-code').value.trim();
  if (!password) {
    toast('Enter your current password', 'warn');
    return;
  }
  if (_userProfile?.user?.mfa_enabled && !/^\d{6}$/.test(code)) {
    toast('Enter a fresh code from your current authenticator before replacing it', 'warn');
    return;
  }
  try {
    const res = await fetch('/api/auth/mfa/enroll/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, ...(code ? { totp: code } : {}) }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== 'pending') throw new Error(data.detail || 'Could not start authenticator setup');
    document.getElementById('account-mfa-secret').textContent = data.secret;
    const qr = document.getElementById('account-mfa-qr');
    if (data.qr_data_uri) {
      qr.src = data.qr_data_uri;
      qr.hidden = false;
    } else {
      qr.removeAttribute('src');
      qr.hidden = true;
    }
    const uri = document.getElementById('account-mfa-uri');
    uri.href = data.otpauth_uri;
    document.getElementById('account-mfa-enrollment').hidden = false;
    document.getElementById('account-mfa-verify-btn').hidden = false;
    document.getElementById('account-mfa-code').value = '';
    document.getElementById('account-mfa-code').focus();
    toast('Authenticator setup started. Add the key, then verify a fresh code.', 'success', 4200);
  } catch (e) {
    toast(e.message || 'Could not start authenticator setup', 'danger');
  }
}

async function verifyMfaEnrollment() {
  const password = document.getElementById('account-mfa-password').value;
  const code = document.getElementById('account-mfa-code').value.trim();
  if (!password || !/^\d{6}$/.test(code)) {
    toast('Enter your current password and the new 6-digit code', 'warn');
    return;
  }
  try {
    const res = await fetch('/api/auth/mfa/enroll/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, totp: code }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.detail || 'Authenticator verification failed');
    document.getElementById('account-mfa-enrollment').hidden = true;
    document.getElementById('account-mfa-verify-btn').hidden = true;
    document.getElementById('account-mfa-password').value = '';
    document.getElementById('account-mfa-code').value = '';
    toast('Authenticator enabled. Protected actions now require step-up verification.', 'success', 4200);
    await loadUserProfile(true);
  } catch (e) {
    toast(e.message || 'Authenticator verification failed', 'danger');
  }
}

async function disableMfa() {
  const password = document.getElementById('account-mfa-password').value;
  const code = document.getElementById('account-mfa-code').value.trim();
  if (!password || !/^\d{6}$/.test(code)) {
    toast('Enter your current password and a fresh authenticator code', 'warn');
    return;
  }
  const ok = await customConfirm(
    'Disable authenticator protection?<br><span style="font-size:11px;">You will be signed out and broker/order actions will be blocked until it is set up again.</span>',
    { title: 'Disable Authenticator', icon: ICO.shield(28), okText: 'Disable & Sign Out', danger: true }
  );
  if (!ok) return;
  try {
    const res = await fetch('/api/auth/mfa', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, totp: code }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.detail || 'Could not disable authenticator');
    await purgeAuthenticatedShellCaches();
    location.reload();
  } catch (e) {
    toast(e.message || 'Could not disable authenticator', 'danger');
  }
}

async function saveBrokerSettings() {
  const clientId = document.getElementById('account-broker-client-id').value.trim();
  const accessToken = document.getElementById('account-broker-token').value.trim();
  const pin = document.getElementById('account-broker-pin').value.trim();
  const totpSecret = document.getElementById('account-broker-totp').value.trim();
  const existingBroker = _userProfile?.broker || {};
  const existingClientId = String(existingBroker.client_id || '').trim();
  const effectiveClientId = clientId || existingClientId;
  const effectiveTokenPresent = !!accessToken || !!existingBroker.access_token_saved;
  const effectivePinPresent = !!pin || !!existingBroker.pin_saved;
  const effectiveTotpPresent = !!totpSecret || !!existingBroker.totp_saved;

  if (!effectiveClientId) {
    toast('Enter a Dhan Client ID', 'warn');
    return;
  }
  if (!effectiveTokenPresent) {
    toast('Enter an Access Token, or keep an already saved token.', 'warn');
    return;
  }
  if (clientId && existingClientId && clientId !== existingClientId && !accessToken) {
    toast('Enter a new Access Token when changing the Dhan Client ID.', 'warn');
    return;
  }
  if ((pin || totpSecret) && !(effectivePinPresent && effectiveTotpPresent)) {
    toast('Save both Dhan PIN and TOTP Secret together to enable auto-refresh.', 'warn');
    return;
  }

  const body = {};
  if (clientId) body.client_id = clientId;
  if (accessToken) body.access_token = accessToken;
  if (pin) body.pin = pin;
  if (totpSecret) body.totp_secret = totpSecret;
  if (!Object.keys(body).length) {
    toast('Enter at least one broker field to update.', 'warn');
    return;
  }
  try {
    const res = await fetch('/api/user/broker', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.detail || data.message || 'Failed to save broker credentials');
    toast(data.message || 'Broker credentials saved', 'success');
    await loadUserProfile(true);
    await checkBrokerStatus(true);
  } catch (e) {
    toast(e.message || 'Failed to save broker credentials', 'danger');
  }
}

async function clearBrokerSettings() {
  const ok = await customConfirm(
    'Clear the stored broker credentials for this account?<br><span style="font-size:11px;">Live broker workflows must be stopped first.</span>',
    { title: 'Clear Broker Credentials', icon: ICO.trash(28), okText: 'Clear', danger: true }
  );
  if (!ok) return;
  try {
    const res = await fetch('/api/user/broker', { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.detail || data.message || 'Failed to clear broker credentials');
    toast(data.message || 'Stored broker credentials cleared', 'warn');
    await loadUserProfile(true);
    await checkBrokerStatus(true);
  } catch (e) {
    toast(e.message || 'Failed to clear broker credentials', 'danger');
  }
}

async function checkBrokerFromSettings() {
  const ok = await checkBrokerStatus(false);
  await loadUserProfile(true);
  if (!ok && _userProfile?.broker?.partial) {
    toast('Save both Client ID and Access Token before testing the broker connection.', 'warn');
  }
}

async function changeOwnPasswordFromSettings() {
  const currentPassword = document.getElementById('account-current-password').value;
  const newPassword = document.getElementById('account-new-password').value;
  const confirmPassword = document.getElementById('account-confirm-password').value;
  if (!currentPassword || !newPassword) {
    toast('Enter both current and new password', 'warn');
    return;
  }
  if (newPassword !== confirmPassword) {
    toast('New password and confirmation do not match', 'warn');
    return;
  }
  const passwordError = validatePasswordRule(newPassword, 'New password');
  if (passwordError) {
    toast(passwordError, 'warn');
    return;
  }
  try {
    const res = await fetch('/api/user/password', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.detail || data.message || 'Password change failed');
    toast(data.message || 'Password changed', 'success');
    setTimeout(() => location.reload(), 900);
  } catch (e) {
    toast(e.message || 'Password change failed', 'danger');
  }
}

function closeAdminModal() {
  document.getElementById('admin-modal').classList.remove('open');
}

async function openAdminModal() {
  if (!_authUser || _authUser.role !== 'admin') {
    toast('Admin access only', 'danger');
    return;
  }
  document.getElementById('admin-modal').classList.add('open');
  await loadAdminConsole(true);
}

async function loadAdminConsole(silent = true) {
  try {
    const [usersRes, enginesRes] = await Promise.all([
      fetch('/api/admin/users'),
      fetch('/api/admin/engines'),
    ]);
    const usersData = await usersRes.json();
    const enginesData = await enginesRes.json();
    if (!usersRes.ok) throw new Error(usersData.detail || usersData.message || 'Failed to load users');
    if (!enginesRes.ok) throw new Error(enginesData.detail || enginesData.message || 'Failed to load engine status');
    _adminUsers = usersData.users || [];
    _adminEngineRows = enginesData.users || [];
    renderAdminSummary(_adminUsers, _adminEngineRows);
    renderAdminUsers(_adminUsers, _adminEngineRows);
    if (!silent) toast('Admin console refreshed', 'success', 2200);
  } catch (e) {
    const container = document.getElementById('admin-users-list');
    if (container) container.textContent = e.message || 'Failed to load admin console';
    if (!silent) toast(e.message || 'Failed to load admin console', 'danger');
  }
}

function renderAdminSummary(users, engineRows) {
  const totalUsers = users.length;
  const activeUsers = users.filter(u => !!u.is_active).length;
  const liveRunning = engineRows.reduce((sum, row) => sum + Number(row.live_running || 0), 0);
  const scalpOpen = engineRows.reduce((sum, row) => sum + Number(row.scalp_open_trades || 0), 0);
  document.getElementById('admin-summary-users').textContent = totalUsers;
  document.getElementById('admin-summary-active').textContent = activeUsers;
  document.getElementById('admin-summary-live').textContent = liveRunning;
  document.getElementById('admin-summary-scalp').textContent = scalpOpen;
}

function renderAdminUsers(users, engineRows) {
  const container = document.getElementById('admin-users-list');
  if (!container) return;
  if (!users.length) {
    container.className = 'admin-empty-state';
    container.textContent = 'No users found.';
    return;
  }

  const engineMap = new Map(engineRows.map(row => [Number(row.user_id), row]));
  container.className = '';
  container.innerHTML = '';

  const table = document.createElement('table');
  table.className = 'admin-table';
  const thead = document.createElement('thead');
  thead.innerHTML = '<tr><th>User</th><th>Broker</th><th>Engines</th><th>Actions</th></tr>';
  table.appendChild(thead);
  const tbody = document.createElement('tbody');

  users.forEach(user => {
    const row = document.createElement('tr');
    const engine = engineMap.get(Number(user.id)) || {};

    const userCell = document.createElement('td');
    userCell.className = 'admin-user-cell';
    const nameLine = document.createElement('div');
    nameLine.style.display = 'flex';
    nameLine.style.alignItems = 'center';
    nameLine.style.gap = '8px';
    const nameStrong = document.createElement('strong');
    nameStrong.textContent = user.username;
    nameLine.appendChild(nameStrong);
    nameLine.appendChild(createStatusChip(user.role === 'admin' ? 'Admin' : 'User', user.role === 'admin' ? 'success' : 'warn'));
    nameLine.appendChild(createStatusChip(user.is_active ? 'Active' : 'Disabled', user.is_active ? 'success' : 'danger'));
    userCell.appendChild(nameLine);

    const meta1 = document.createElement('div');
    meta1.className = 'admin-user-sub';
    meta1.textContent = `Created: ${formatDateTimeLabel(user.created_at)}`;
    userCell.appendChild(meta1);
    const meta2 = document.createElement('div');
    meta2.className = 'admin-user-sub';
    meta2.textContent = `Last login: ${formatDateTimeLabel(user.last_login)}`;
    userCell.appendChild(meta2);
    row.appendChild(userCell);

    const brokerCell = document.createElement('td');
    brokerCell.className = 'admin-user-cell';
    const brokerChip = user.broker_configured
      ? createStatusChip('Configured', 'success')
      : user.broker_partial
        ? createStatusChip('Partial', 'danger')
        : createStatusChip('Missing', 'warn');
    brokerCell.appendChild(brokerChip);
    const brokerMeta = document.createElement('div');
    brokerMeta.className = 'admin-user-sub';
    brokerMeta.textContent = user.broker_configured
      ? 'User-managed broker credentials saved'
      : user.broker_partial
        ? 'Client ID / token pair incomplete'
        : 'No stored broker credentials';
    brokerCell.appendChild(brokerMeta);
    row.appendChild(brokerCell);

    const engineCell = document.createElement('td');
    engineCell.className = 'admin-user-cell';
    const engineSummary = document.createElement('div');
    engineSummary.style.display = 'flex';
    engineSummary.style.flexWrap = 'wrap';
    engineSummary.style.gap = '8px';
    engineSummary.appendChild(createStatusChip(`Paper ${engine.paper_running || 0}`, Number(engine.paper_running || 0) > 0 ? 'success' : 'warn'));
    engineSummary.appendChild(createStatusChip(`Live ${engine.live_running || 0}`, Number(engine.live_running || 0) > 0 ? 'success' : 'warn'));
    engineSummary.appendChild(createStatusChip(`Scalp ${engine.scalp_open_trades || 0}`, Number(engine.scalp_open_trades || 0) > 0 ? 'success' : 'warn'));
    const cascadeCount = Number(!!engine.cascade_running) + Number(!!engine.candle_entry_running)
      + Number(!!engine.fib_boundary_running) + Number(engine.terminal_cascade_running || 0);
    engineSummary.appendChild(createStatusChip(`Cascade ${cascadeCount}`, cascadeCount > 0 ? 'success' : 'warn'));
    engineCell.appendChild(engineSummary);
    const liveRuns = Array.isArray(engine.live_runs) ? engine.live_runs : [];
    const paperRuns = Array.isArray(engine.paper_runs) ? engine.paper_runs : [];
    const engineMeta = document.createElement('div');
    engineMeta.className = 'admin-user-sub';
    const runNames = [...paperRuns, ...liveRuns].map(run => run.strategy_name || run.run_id).filter(Boolean);
    engineMeta.textContent = runNames.length ? `Active runs: ${runNames.join(', ')}` : (cascadeCount ? 'Cascade campaign active' : 'No active engines');
    engineCell.appendChild(engineMeta);
    row.appendChild(engineCell);

    const actionsCell = document.createElement('td');
    const actions = document.createElement('div');
    actions.className = 'admin-action-row';
    if (_authUser && Number(user.id) === Number(_authUser.user_id)) {
      actions.appendChild(createStatusChip('Current Account', 'warn'));
    } else {
      const toggleBtn = document.createElement('button');
      toggleBtn.className = 'admin-action-btn';
      toggleBtn.textContent = user.is_active ? 'Disable' : 'Enable';
      toggleBtn.addEventListener('click', () => toggleAdminUser(Number(user.id)));
      actions.appendChild(toggleBtn);

      const resetBtn = document.createElement('button');
      resetBtn.className = 'admin-action-btn';
      resetBtn.textContent = 'Reset Password';
      resetBtn.addEventListener('click', () => resetAdminUserPassword(Number(user.id), user.username));
      actions.appendChild(resetBtn);

      if (user.role !== 'admin') {
        const copyBtn = document.createElement('button');
        copyBtn.className = 'admin-action-btn';
        copyBtn.textContent = 'Copy Examples';
        copyBtn.addEventListener('click', () => copyAdminExamplesToUser(Number(user.id), user.username));
        actions.appendChild(copyBtn);
      }
    }
    actionsCell.appendChild(actions);
    row.appendChild(actionsCell);

    tbody.appendChild(row);
  });

  table.appendChild(tbody);
  container.appendChild(table);
}

async function createAdminUser() {
  const username = document.getElementById('admin-create-username').value.trim();
  const password = document.getElementById('admin-create-password').value;
  const role = document.getElementById('admin-create-role').value;
  if (!username || !password) {
    toast('Enter username and password', 'warn');
    return;
  }
  const passwordError = validatePasswordRule(password);
  if (passwordError) {
    toast(passwordError, 'warn');
    return;
  }
  try {
    const res = await fetch('/api/admin/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, role }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.detail || data.message || 'Failed to create user');
    const copied = data.copied || {};
    const bits = [];
    if (Number(copied.strategies || 0) > 0) bits.push(`${copied.strategies} strategies`);
    if (Number(copied.backtests || 0) > 0) bits.push(`${copied.backtests} backtests`);
    if (Number(copied.charts || 0) > 0) bits.push('latest chart date');
    const seedSuffix = bits.length ? ` with ${bits.join(', ')}` : '';
    toast(`User "${username}" created${seedSuffix}`, 'success');
    document.getElementById('admin-create-username').value = '';
    document.getElementById('admin-create-password').value = '';
    document.getElementById('admin-create-role').value = 'user';
    await loadAdminConsole(true);
  } catch (e) {
    toast(e.message || 'Failed to create user', 'danger');
  }
}

async function toggleAdminUser(userId) {
  try {
    const res = await fetch(`/api/admin/users/${userId}/toggle`, { method: 'PUT' });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.detail || data.message || 'Failed to update user state');
    toast(data.is_active ? 'User enabled' : 'User disabled', data.is_active ? 'success' : 'warn');
    await loadAdminConsole(true);
  } catch (e) {
    toast(e.message || 'Failed to update user state', 'danger');
  }
}

async function resetAdminUserPassword(userId, username) {
  const password = await customConfirm(
    `Enter a new password for <strong>${escapeHtml(username)}</strong>:<br><span style="font-size:11px;color:var(--muted);">${escapeHtml(PASSWORD_POLICY_HINT)}</span>`,
    {
      title: 'Reset Password',
      icon: ICO.shield(24),
      okText: 'Reset',
      prompt: true,
      promptType: 'password',
      promptPlaceholder: 'At least 8 characters',
      promptValue: ''
    }
  );
  if (password == null) return;
  if (!password) {
    toast('Password cannot be empty', 'warn');
    return;
  }
  const passwordError = validatePasswordRule(password);
  if (passwordError) {
    toast(passwordError, 'warn');
    return;
  }
  try {
    const res = await fetch(`/api/admin/users/${userId}/password`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.detail || data.message || 'Failed to reset password');
    toast(data.message || `Password reset for ${username}`, 'success');
  } catch (e) {
    toast(e.message || 'Failed to reset password', 'danger');
  }
}

async function copyAdminExamplesToUser(userId, username) {
  const ok = await customConfirm(
    `Copy admin examples to <strong>${escapeHtml(username)}</strong>?<br><span style="font-size:11px;color:var(--muted);">This copies only admin items from the <strong>Default</strong> folder: strategies, the latest chart date, and the latest 2 admin backtests. Existing seeded examples for this user are refreshed instead of duplicated, and old seeded journal entries are removed.</span>`,
    {
      title: 'Copy Examples',
      icon: ICO.brain(24),
      okText: 'Copy',
      danger: false,
    }
  );
  if (!ok) return;

  try {
    const res = await fetch(`/api/admin/users/${userId}/copy-examples`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.detail || data.message || 'Failed to copy admin examples');

    const copied = data.copied || {};
    const bits = [];
    if (Number(copied.strategies || 0) > 0) bits.push(`${copied.strategies} strategies`);
    if (Number(copied.backtests || 0) > 0) bits.push(`${copied.backtests} backtests`);
    if (Number(copied.charts || 0) > 0) bits.push('latest chart date');
    if (!bits.length) {
      toast(`No admin examples available for ${username}`, 'warn');
      return;
    }

    toast(`Copied ${bits.join(', ')} to ${username}`, 'success', 3600);
    await loadAdminConsole(true);
  } catch (e) {
    toast(e.message || 'Failed to copy admin examples', 'danger');
  }
}

// ══════════════════════════════════════════════════════════════
//  NAVIGATION & INIT
// ══════════════════════════════════════════════════════════════
const NAV_BUTTON_MAP = {
  'dashboard-page': 'nav-dashboard',
  'portfolio-page': 'nav-portfolio',
  'builder-page': 'nav-builder',
  'results-page': 'nav-results',
  'live-page': 'nav-live',
  'stock-terminal-page': 'nav-terminal',
  'scalp-page': 'nav-scalp',
  'options-cascade-page': 'nav-cascade',
  'charts-page': 'nav-charts',
  'insights-page': 'nav-insights',
};

let _mobileNavSyncTimer = null;
let _insightsMenuHome = null;
let _cascadeMenuHome = null;

function closeInsightsMenu() {
  const wrap = document.getElementById('nav-insights-wrap');
  wrap?.classList.remove('menu-open');
  document.body.classList.remove('insights-menu-open');
  const menu = document.getElementById('nav-insights-menu');
  if (!menu) return;
  menu.classList.remove('mobile-floating');
  menu.classList.remove('menu-open');
  menu.style.removeProperty('left');
  menu.style.removeProperty('top');
  menu.style.removeProperty('right');
  menu.style.removeProperty('width');
  if (wrap && menu.parentElement !== wrap) {
    wrap.appendChild(menu);
  }
}

function positionInsightsMenu() {
  const wrap = document.getElementById('nav-insights-wrap');
  const trigger = document.getElementById('nav-insights');
  const menu = document.getElementById('nav-insights-menu');
  // Every width, not just mobile: the nav row is a horizontal scroller, and
  // an absolutely-positioned menu inside it would be clipped by the scroll.
  if (!wrap || !trigger || !menu || !wrap.classList.contains('menu-open')) {
    return;
  }
  const rect = trigger.getBoundingClientRect();
  const margin = 12;
  const width = Math.min(250, window.innerWidth - (margin * 2));
  const left = Math.min(Math.max(margin, rect.left), window.innerWidth - width - margin);
  if (!_insightsMenuHome && wrap) {
    _insightsMenuHome = wrap;
  }
  if (menu.parentElement !== document.body) {
    document.body.appendChild(menu);
  }
  menu.classList.add('mobile-floating');
  menu.classList.add('menu-open');
  menu.style.left = `${left}px`;
  menu.style.top = `${rect.bottom + 8}px`;
  menu.style.right = 'auto';
  menu.style.width = `${width}px`;
}

function toggleInsightsMenu(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  const wrap = document.getElementById('nav-insights-wrap');
  if (!wrap) return;
  const shouldOpen = !wrap.classList.contains('menu-open');
  closeInsightsMenu();
  if (shouldOpen) {
    wrap.classList.add('menu-open');
    document.body.classList.add('insights-menu-open');
    requestAnimationFrame(positionInsightsMenu);
  }
}

function closeCascadeMenu() {
  const wrap = document.getElementById('nav-cascade-wrap');
  wrap?.classList.remove('menu-open');
  document.body.classList.remove('cascade-menu-open');
  const menu = document.getElementById('nav-cascade-menu');
  if (!menu) return;
  menu.classList.remove('mobile-floating', 'menu-open');
  menu.style.removeProperty('left');
  menu.style.removeProperty('top');
  menu.style.removeProperty('right');
  menu.style.removeProperty('width');
  if (wrap && menu.parentElement !== wrap) wrap.appendChild(menu);
}

function positionCascadeMenu() {
  const wrap = document.getElementById('nav-cascade-wrap');
  const trigger = document.getElementById('nav-cascade');
  const menu = document.getElementById('nav-cascade-menu');
  if (!wrap || !trigger || !menu || window.innerWidth > 767 || !wrap.classList.contains('menu-open')) return;
  const rect = trigger.getBoundingClientRect();
  const margin = 12;
  const width = Math.min(270, window.innerWidth - (margin * 2));
  const left = Math.min(Math.max(margin, rect.left), window.innerWidth - width - margin);
  _cascadeMenuHome = wrap;
  if (menu.parentElement !== document.body) document.body.appendChild(menu);
  menu.classList.add('mobile-floating', 'menu-open');
  menu.style.left = `${left}px`;
  menu.style.top = `${rect.bottom + 8}px`;
  menu.style.right = 'auto';
  menu.style.width = `${width}px`;
}

function toggleCascadeMenu(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  const wrap = document.getElementById('nav-cascade-wrap');
  if (!wrap) return;
  const shouldOpen = !wrap.classList.contains('menu-open');
  closeInsightsMenu();
  closeCascadeMenu();
  if (shouldOpen) {
    wrap.classList.add('menu-open');
    document.body.classList.add('cascade-menu-open');
    if (window.innerWidth <= 767) requestAnimationFrame(positionCascadeMenu);
  }
}

window.toggleCascadeMenu = toggleCascadeMenu;

function _syncMobileActiveNavTab(targetBtn = null, behavior = 'smooth') {
  if (window.innerWidth > 767) return;
  const navBar = document.querySelector('.nav-bar');
  const btn = targetBtn || document.querySelector('.nav-tab.active');
  if (!navBar || !btn) return;
  const navRect = navBar.getBoundingClientRect();
  const btnRect = btn.getBoundingClientRect();
  const padding = 12;
  const leftEdge = navRect.left + padding;
  const rightEdge = navRect.right - padding;
  let nextLeft = navBar.scrollLeft;
  if (btnRect.left < leftEdge) {
    nextLeft -= (leftEdge - btnRect.left);
  } else if (btnRect.right > rightEdge) {
    nextLeft += (btnRect.right - rightEdge);
  } else {
    return;
  }
  navBar.scrollTo({
    left: Math.max(0, nextLeft),
    behavior,
  });
}

function _scrollViewportToTop() {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
      document.documentElement.scrollTop = 0;
      document.body.scrollTop = 0;
    });
  });
}

const _pageScrollPositions = new Map();

function _restorePageScroll(page) {
  const position = _pageScrollPositions.get(page);
  if (!position) return;
  requestAnimationFrame(() => {
    window.scrollTo({ top: position.top, left: position.left, behavior: 'auto' });
  });
}

function _restoreInitialMobileNavPosition(page = 'dashboard-page') {
  if (window.innerWidth > 767) return;
  const navBar = document.querySelector('.nav-bar');
  if (!navBar) return;
  requestAnimationFrame(() => {
    if (page === 'dashboard-page') {
      navBar.scrollTo({ left: 0, behavior: 'auto' });
      return;
    }
    _syncMobileActiveNavTab(document.getElementById(NAV_BUTTON_MAP[page] || ''), 'auto');
  });
}

function buildNavState(page, extra = {}) {
  const state = { page };
  if (extra && typeof extra === 'object') Object.assign(state, extra);
  return state;
}

function navHashForState(state) {
  const page = state?.page || 'dashboard-page';
  if (page === 'results-page' && Number.isFinite(Number(state?.runId)) && Number(state.runId) > 0) {
    return `#results-page/${Number(state.runId)}`;
  }
  return `#${page}`;
}

function navStateFromLocation() {
  const raw = String(location.hash || '').replace(/^#/, '').trim();
  if (!raw) return null;
  const [page, runIdRaw] = raw.split('/');
  if (!page || !document.getElementById(page)) return null;
  const state = { page };
  const runId = Number(runIdRaw);
  if (page === 'results-page' && Number.isFinite(runId) && runId > 0) state.runId = runId;
  return state;
}

const PF_DELEGATED_ACTIONS = new Set([
  'closeAppearanceModal',
  'emergencyStop',
  'logoutUser',
  'openAccountModal',
  'openAdminModal',
  'openAppearanceModal',
  'resetAppearance',
  'toggleTheme',
  'toggleCascadeMenu',
  'startCascadeOptionsPaper',
  'stopCascadeOptionsPaper',
  'loadCascadeOptionsChart',
  'hideCascadeOptionsChart',
  'startCandleEntryPaper',
  'killCandleEntryPaper',
  'killCascadeOptionsPaper',
  'showOptionsCascadeTab',
  'showInsightsTab',
  'pickFibTimeframe',
  'recoveryStart',
  'recoveryStop',
  'recoveryAddMother',
  'recoveryDrop',
  'startFibBoundaryPaper',
  'killFibBoundaryPaper',
  'armFibBoundaryLive',
  'loadFibBoundaryChart',
  'hideFibBoundaryChart',
  'runFibBoundaryBacktest',
  'toggleFibBoundaryBacktestChart',
  'startFibSpacePaper',
  'stopFibSpacePaper',
  'addFibSpaceMother',
  'openFibSpaceCampaign',
  'loadFibSpaceChart',
  'hideFibSpaceChart',
  'runTestBench',
  'toggleTestBenchChart',
  'openSavedTestBenchRun',
  'deleteSavedTestBenchRun',
  'pageSavedTestBenchRuns',
]);

document.addEventListener('click', (event) => {
  const tintBtn = event.target.closest('[data-appearance-tint]');
  if (tintBtn) {
    event.preventDefault();
    setAppearanceTint(tintBtn.getAttribute('data-appearance-tint'));
    return;
  }

  const fontBtn = event.target.closest('[data-appearance-font]');
  if (fontBtn) {
    event.preventDefault();
    setAppearanceFont(fontBtn.getAttribute('data-appearance-font'));
    return;
  }

  const dismissTarget = event.target.closest('[data-pf-dismiss-action]');
  if (dismissTarget && event.target === dismissTarget) {
    const fn = window[dismissTarget.getAttribute('data-pf-dismiss-action')];
    if (typeof fn === 'function') fn();
    return;
  }

  const actionEl = event.target.closest('[data-pf-action]');
  if (actionEl) {
    const action = actionEl.getAttribute('data-pf-action');
    if (PF_DELEGATED_ACTIONS.has(action) && typeof window[action] === 'function') {
      event.preventDefault();
      window[action](event, actionEl);
      return;
    }
  }

  const navEl = event.target.closest('[data-pf-nav-page]');
  if (navEl) {
    event.preventDefault();
    const page = navEl.getAttribute('data-pf-nav-page');
    const btnId = navEl.getAttribute('data-pf-nav-tab');
    showPage(page, btnId ? document.getElementById(btnId) : navEl);
    const after = navEl.getAttribute('data-pf-after-nav');
    if (after && typeof window[after] === 'function') window[after]();
  }
});

async function applyNavState(state) {
  const page = (state && state.page && document.getElementById(state.page)) ? state.page : 'dashboard-page';
  const btn = document.getElementById(NAV_BUTTON_MAP[page] || '');
  showPage(page, btn, { pushHistory: false, historyState: state });
  if (page === 'live-page') startLiveMonitor();
  if (page === 'stock-terminal-page') initStockTerminalPage();
  if (page === 'insights-page') initInsightsPage();
  if (page === 'scalp-page') initScalpPage();
  if (page === 'options-cascade-page') initOptionsCascadePage();
  if (page === 'charts-page') initChartsPage();
  if (page === 'results-page' && Number.isFinite(Number(state?.runId)) && Number(state.runId) > 0 && currentViewingRunId !== Number(state.runId)) {
    await viewRun(Number(state.runId), { pushHistory: false });
  }
}

let _portfolioRefreshTimer = null;
let _portfolioLoadedOnce = false;
let _runsLoadedOnce = false;
let _strategiesLoadedOnce = false;

function stopPortfolioRefresh() {
  if (_portfolioRefreshTimer) {
    clearInterval(_portfolioRefreshTimer);
    _portfolioRefreshTimer = null;
  }
}

async function ensurePortfolioLoaded(force = false) {
  if (force || !_portfolioLoadedOnce) {
    await loadPortfolioData();
    _portfolioLoadedOnce = true;
  }
  if (!_portfolioRefreshTimer) {
    _portfolioRefreshTimer = setInterval(loadPortfolioData, 60000);
  }
}

async function ensureRunsLoaded(force = false) {
  if (force || !_runsLoadedOnce) {
    await fetchRuns();
    _runsLoadedOnce = true;
  }
}

async function ensureStrategiesLoaded(force = false) {
  if (force || !_strategiesLoadedOnce) {
    await fetchStrategies();
    _strategiesLoadedOnce = true;
  }
}

function showPage(id, btn, options = {}) {
  const previousPageId = document.querySelector('.page-section.active-page')?.id || '';
  if (previousPageId && previousPageId !== id) {
    _pageScrollPositions.set(previousPageId, { top: window.scrollY, left: window.scrollX });
  }
  const scalpWasActive = !!document.getElementById('scalp-page')?.classList.contains('active-page');
  if (scalpWasActive && id !== 'scalp-page') _persistScalpFormState();
  document.querySelectorAll('.page-section').forEach(p => {
    p.classList.remove('active-page');
    p.setAttribute('aria-hidden', 'true');
  });
  document.querySelectorAll('.nav-tab').forEach(b => {
    b.classList.remove('active');
    b.removeAttribute('aria-current');
  });
  const activePage = document.getElementById(id);
  activePage.classList.add('active-page');
  activePage.removeAttribute('aria-hidden');
  const activeBtn = btn || document.getElementById(NAV_BUTTON_MAP[id] || '');
  if (activeBtn) {
    activeBtn.classList.add('active');
    activeBtn.setAttribute('aria-current', 'page');
  }
  const mainContent = document.getElementById('main-content');
  if (mainContent) {
    const pageLabel = activeBtn?.querySelector('.tab-label')?.textContent?.trim() || 'PhilForge';
    mainContent.setAttribute('aria-label', pageLabel + ' workspace');
  }
  closeCascadeMenu();
  // Stop live monitor polling when leaving the live page
  if (id !== 'live-page') stopLiveMonitor();
  // Stop scalp polling when leaving scalp page
  if (id !== 'scalp-page' && _scalpPollTimer) { clearInterval(_scalpPollTimer); _scalpPollTimer = null; }
  if (id !== 'scalp-page' && _scalpLTPTimer) { clearInterval(_scalpLTPTimer); _scalpLTPTimer = null; }
  if (id !== 'options-cascade-page' && _fibBoundaryPollTimer) { clearInterval(_fibBoundaryPollTimer); _fibBoundaryPollTimer = null; }
  if (id !== 'portfolio-page') stopPortfolioRefresh();
  // Start/stop builder preview polling
  if (id === 'builder-page') {
    startBuilderPreview();
    ensureStrategiesLoaded();
  } else {
    stopBuilderPreview();
  }
  if (id === 'results-page') ensureRunsLoaded();
  if (id === 'portfolio-page') {
    ensureRunsLoaded();
    ensurePortfolioLoaded();
  }
  // Reload dashboard data when switching to dashboard
  if (id === 'dashboard-page') {
    loadDashboardSummary();
    ensureStrategiesLoaded();
    ensureRunsLoaded();
  }
  // Persist active tab across page refresh
  try { _setLocalState('philforge_active_tab', id); } catch(e) {}
  if (options.pushHistory !== false) {
    const navState = buildNavState(id, options.historyState || {});
    const nextHash = navHashForState(navState);
    const currentState = history.state || {};
    if (currentState.page !== navState.page || currentState.runId !== navState.runId || location.hash !== nextHash) {
      history.pushState(navState, '', nextHash);
    }
  }
  if (previousPageId && previousPageId !== id) {
    if (options.scrollToTop === true) _scrollViewportToTop();
    else _restorePageScroll(id);
  }
}

window.addEventListener('resize', () => {
  closeInsightsMenu();
  closeCascadeMenu();
  clearTimeout(_mobileNavSyncTimer);
  _mobileNavSyncTimer = setTimeout(() => _syncMobileActiveNavTab(), 80);
});

window.addEventListener('orientationchange', () => {
  closeInsightsMenu();
  closeCascadeMenu();
  clearTimeout(_mobileNavSyncTimer);
  _mobileNavSyncTimer = setTimeout(() => _syncMobileActiveNavTab(), 140);
});
window.addEventListener('scroll', () => {
  positionInsightsMenu();
  positionCascadeMenu();
}, { passive: true });
document.addEventListener('click', (event) => {
  if (!event.target.closest('#nav-insights-wrap') && !event.target.closest('#nav-insights-menu')) closeInsightsMenu();
  if (!event.target.closest('#nav-cascade-wrap') && !event.target.closest('#nav-cascade-menu')) closeCascadeMenu();
});
function generateRandomID() { return Math.floor(100000 + Math.random() * 900000); }

function _getLocalState(primaryKey, legacyKey = '') {
  try {
    const current = localStorage.getItem(primaryKey);
    if (current !== null) return current;
    if (!legacyKey) return null;
    const legacy = localStorage.getItem(legacyKey);
    if (legacy !== null) {
      localStorage.setItem(primaryKey, legacy);
      localStorage.removeItem(legacyKey);
    }
    return legacy;
  } catch(e) {
    return null;
  }
}

function _setLocalState(primaryKey, value, legacyKey = '') {
  try {
    localStorage.setItem(primaryKey, value);
    if (legacyKey) localStorage.removeItem(legacyKey);
  } catch(e) {}
}

// ══════════════════════════════════════════════════════════════
//  MANUAL NIFTY 1H CASCADE BACKTEST (read-only)
// ══════════════════════════════════════════════════════════════
function _cascadeEl(id) { return document.getElementById(id); }

function _cascadeNumber(value, decimals = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString('en-IN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : '—';
}

function _cascadeSetTone(el, tone = 'muted') {
  if (!el) return;
  el.classList.remove('is-positive', 'is-warning', 'is-negative', 'is-info');
  if (tone === 'success') el.classList.add('is-positive');
  if (tone === 'busy' || tone === 'warning') el.classList.add('is-warning');
  if (tone === 'error') el.classList.add('is-negative');
  if (tone === 'info') el.classList.add('is-info');
}


// ══════════════════════════════════════════════════════════════
//  NIFTY OPTIONS CASCADE — CURRENT-SESSION PAPER CAMPAIGN
// ══════════════════════════════════════════════════════════════
let _cascadeOptionsPollTimer = null;
let _lastCascadeOptionsStatus = null;

function _cascadeOptionsEl(id) { return document.getElementById(id); }
function _cascadeOptionsMoney(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';
}
function _cascadeOptionsSetFormStatus(message, tone = 'muted') {
  const el = _cascadeOptionsEl('cascade-options-form-status');
  if (!el) return;
  el.textContent = message;
  _cascadeSetTone(el, tone);
  el.style.color = ({ muted: 'var(--muted)', error: 'var(--danger)', success: '#6ee7b7', busy: '#fde68a' }[tone] || 'var(--muted)');
}
function _cascadeOptionsTimestamp(value) {
  return value ? String(value).replace('T', ' ').replace(/(?:\.\d+)?(?:\+05:30|Z)$/, ' IST') : '—';
}
function _cascadeOptionsToneClass(accent) {
  const tone = String(accent || '').toLowerCase();
  if (tone.includes('6ee7b7') || tone.includes('34d399')) return 'is-positive';
  if (tone.includes('fde68a') || tone.includes('fbbf24')) return 'is-warning';
  if (tone.includes('fca5a5') || tone.includes('f87171')) return 'is-negative';
  return '';
}

function _cascadeOptionsMetric(label, value, accent = 'var(--text)') {
  const toneClass = _cascadeOptionsToneClass(accent);
  return `<div class="cascade-options-metric ${toneClass}" style="padding:10px;border:1px solid var(--border);border-radius:7px;min-width:0;"><div style="font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.55px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(label)}</div><div class="cascade-options-metric-value" style="margin-top:4px;font:800 12px 'JetBrains Mono',monospace;color:${accent};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(value)}</div></div>`;
}

function _renderCascadeOptionsStatus(payload) {
  const gate = payload?.live_gate || {};
  const gateEl = _cascadeOptionsEl('cascade-options-live-gate');
  if (gateEl) {
    gateEl.innerHTML = `<strong>${gate.enabled ? 'LIVE GATE PRESENT' : 'LIVE LOCKED'}</strong><br><span style="color:var(--muted);font-size:10px;">${escapeHtml(gate.reason || 'Paper validation required')}</span>`;
  }
  const campaign = payload?.campaign;
  const badge = _cascadeOptionsEl('cascade-options-badge');
  const contract = _cascadeOptionsEl('cascade-options-contract');
  const summary = _cascadeOptionsEl('cascade-options-summary');
  const empty = _cascadeOptionsEl('cascade-options-empty');
  const active = _cascadeOptionsEl('cascade-options-active');
  const gist = _cascadeOptionsEl('cascade-options-gist');
  const campaignWindow = _cascadeOptionsEl('cascade-options-window');
  const startBtn = _cascadeOptionsEl('cascade-options-start');
  const stopBtn = _cascadeOptionsEl('cascade-options-stop');
  const killBtn = _cascadeOptionsEl('cascade-options-kill');
  if (!campaign) {
    if (badge) { badge.textContent = 'IDLE'; _cascadeSetTone(badge); badge.style.color = 'var(--muted)'; badge.style.borderColor = 'var(--border)'; }
    if (contract) contract.textContent = 'No active campaign';
    if (summary) summary.innerHTML = '';
    if (empty) empty.style.display = '';
    if (active) active.style.display = 'none';
    if (gist) gist.textContent = 'Choose a completed 5m mother candle to begin.';
    if (campaignWindow) campaignWindow.classList.remove('is-active');
    if (startBtn) startBtn.disabled = false;
    if (stopBtn) stopBtn.style.display = 'none';
    if (killBtn) killBtn.style.display = 'none';
    _renderCascadeOptionsRounds([]);
    _renderCascadeOptionsEvents([]);
    return;
  }
  const isRunning = !!campaign.running;
  const state = String(campaign.status || 'waiting').replaceAll('_', ' ').toUpperCase();
  const tone = isRunning ? '#6ee7b7' : 'var(--warn)';
  if (badge) { badge.textContent = state; _cascadeSetTone(badge, isRunning ? 'success' : 'warning'); badge.style.color = tone; badge.style.borderColor = tone; }
  const c = campaign.contract || {};
  if (contract) contract.textContent = `${c.underlying || 'NIFTY'} ${Number(c.strike || 0).toLocaleString('en-IN')} ${c.option_type || 'CE'} · ${c.expiry || '—'} · ${c.lot_size || '—'} units/lot`;
  if (gist) gist.textContent = `${campaign.rounds?.length || 0} round${campaign.rounds?.length === 1 ? '' : 's'} · ${_cascadeOptionsMoney(campaign.pending_inr || 0)} pending`;
  if (campaignWindow) campaignWindow.classList.toggle('is-active', isRunning);
  if (summary) {
    summary.innerHTML = [
      _cascadeOptionsMetric('Index target', _cascadeNumber(campaign.target_index)),
      _cascadeOptionsMetric('Avg index', _cascadeNumber(campaign.average_index_entry)),
      _cascadeOptionsMetric('Open quantity', String(campaign.open_quantity || 0), '#6ee7b7'),
      _cascadeOptionsMetric('Pending cash', _cascadeOptionsMoney(campaign.pending_inr || 0), '#fde68a'),
    ].join('');
  }
  if (empty) empty.style.display = 'none';
  if (active) active.style.display = '';
  if (startBtn) startBtn.disabled = isRunning;
  if (stopBtn) stopBtn.style.display = isRunning ? '' : 'none';
  if (killBtn) killBtn.style.display = isRunning ? '' : 'none';
  const motherTimestamp = campaign?.mother?.timestamp;
  const motherInput = _cascadeOptionsEl('cascade-options-mother-timestamp');
  if (motherInput && !motherInput.value && motherTimestamp) motherInput.value = String(motherTimestamp).slice(0, 16);
  const fills = Array.isArray(campaign.open_fills) ? campaign.open_fills : [];
  const fillsEl = _cascadeOptionsEl('cascade-options-fills');
  if (fillsEl) fillsEl.innerHTML = fills.length ? fills.map(fill => `<div style="padding:8px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;gap:8px;"><span>${escapeHtml(_cascadeOptionsTimestamp(fill.timestamp))}</span><span>${escapeHtml(String(fill.lots))} lot · ${escapeHtml(String(fill.quantity))} qty</span><strong class="is-positive" style="color:#6ee7b7;">CE ₹${escapeHtml(_cascadeNumber(fill.option_premium))}</strong></div>`).join('') : '<div style="color:var(--muted);padding:8px 0;">No open paper CE basket.</div>';
  const rungs = Array.isArray(campaign.rungs) ? campaign.rungs : [];
  const rungsEl = _cascadeOptionsEl('cascade-options-rungs');
  if (rungsEl) rungsEl.innerHTML = rungs.length ? rungs.map(rung => {
    const stateColor = ({ PENDING: 'var(--muted)', COLLECTED: '#fde68a', FILLED: '#6ee7b7', CLOSED: '#a78bfa' }[rung.status] || 'var(--muted)');
    return `<div style="padding:8px;border:1px solid var(--border);border-left:3px solid ${stateColor};border-radius:6px;"><div style="display:flex;justify-content:space-between;gap:5px;font:10px 'JetBrains Mono',monospace;"><strong>F${escapeHtml(rung.leg_id)} · L${escapeHtml(rung.level)}</strong><span style="color:${stateColor};">${escapeHtml(rung.status)}</span></div><div style="margin-top:4px;font:800 11px 'JetBrains Mono',monospace;">${escapeHtml(_cascadeNumber(rung.index_price))}</div></div>`;
  }).join('') : '<div style="grid-column:1/-1;color:var(--muted);font-size:11px;">Geometry has not drawn a fib ladder yet.</div>';
  _renderCascadeOptionsRounds(campaign.rounds || []);
  _renderCascadeOptionsEvents(campaign.events || []);
}

function _renderCascadeOptionsRounds(rounds) {
  const body = _cascadeOptionsEl('cascade-options-rounds');
  const count = _cascadeOptionsEl('cascade-options-round-count');
  if (count) count.textContent = `${rounds.length} round${rounds.length === 1 ? '' : 's'}`;
  if (!body) return;
  body.innerHTML = rounds.length ? rounds.slice().reverse().map(row => {
    const pnl = Number(row.net_pnl || 0);
    const color = pnl > 0 ? '#6ee7b7' : pnl < 0 ? '#fca5a5' : 'var(--muted)';
    return `<tr style="border-bottom:1px solid var(--border);"><td style="padding:9px 8px;font-family:'JetBrains Mono',monospace;">#${escapeHtml(row.round_id)}</td><td style="padding:9px 8px;text-align:right;font-family:'JetBrains Mono',monospace;">${escapeHtml(row.exit_quantity)}</td><td style="padding:9px 8px;text-align:right;font-family:'JetBrains Mono',monospace;">₹${escapeHtml(_cascadeNumber(row.exit_option_premium))}</td><td style="padding:9px 8px;text-align:right;font-family:'JetBrains Mono',monospace;">${escapeHtml(_cascadeOptionsMoney(row.gross_pnl))}</td><td style="padding:9px 8px;text-align:right;font-family:'JetBrains Mono',monospace;">${escapeHtml(_cascadeOptionsMoney(row.costs?.total))}</td><td style="padding:9px 8px;text-align:right;font:800 11px 'JetBrains Mono',monospace;color:${color};">${escapeHtml(_cascadeOptionsMoney(row.net_pnl))}</td><td style="padding:9px 8px;color:var(--muted);">${escapeHtml(String(row.exit_reason || '').replaceAll('_', ' '))}</td></tr>`;
  }).join('') : '<tr><td colspan="7" style="padding:18px;text-align:center;color:var(--muted);">No completed paper round</td></tr>';
}

function _renderCascadeOptionsEvents(events) {
  const el = _cascadeOptionsEl('cascade-options-events');
  if (!el) return;
  const scrollTop = el.scrollTop;
  el.innerHTML = events.length ? events.slice(-24).reverse().map(event => `<div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,.04);"><span style="color:#64748b;">${escapeHtml(_cascadeOptionsTimestamp(event.timestamp))}</span> <strong style="color:var(--text);">${escapeHtml(String(event.event || '').replaceAll('_', ' '))}</strong></div>`).join('') : 'No events yet.';
  el.scrollTop = scrollTop;
}

async function refreshCascadeOptionsStatus() {
  try {
    const response = await fetch('/api/cascade/paper/status', { credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data?.detail || 'Unable to load Cascade campaign');
    _lastCascadeOptionsStatus = data;
    _renderCascadeOptionsStatus(data);
  } catch (error) {
    _cascadeOptionsSetFormStatus(error.message || 'Unable to load campaign status.', 'error');
  }
}

async function initCascadeOptionsPage() {
  await refreshCascadeOptionsStatus();
  await refreshCandleEntryStatus();
  if (!_cascadeOptionsPollTimer) {
    _cascadeOptionsPollTimer = setInterval(() => {
      if (_isPageVisible() && _isPageActive('cascade-options-page')) { refreshCascadeOptionsStatus(); refreshCandleEntryStatus(); }
    }, _ws && _ws.readyState === 1 ? 10000 : 3000);
  }
}

function _renderCandleEntryStatus(payload) {
  const campaign = payload?.campaign;
  const badge = _cascadeOptionsEl('candle-entry-badge');
  const summary = _cascadeOptionsEl('candle-entry-summary');
  const start = _cascadeOptionsEl('candle-entry-start');
  const kill = _cascadeOptionsEl('candle-entry-kill');
  const monitor = _cascadeOptionsEl('candle-entry-monitor');
  if (!campaign) {
    if (badge) { badge.textContent = 'IDLE'; _cascadeSetTone(badge); badge.style.color = 'var(--muted)'; }
    if (summary) summary.textContent = 'No active Candle Entry campaign.';
    if (start) start.disabled = false;
    if (kill) kill.style.display = 'none';
    if (monitor) monitor.hidden = true;
    return;
  }
  const running = !!campaign.running;
  const state = String(campaign.status || 'waiting').replaceAll('_', ' ').toUpperCase();
  if (badge) { badge.textContent = campaign.replay_complete ? `REPLAY · ${state}` : state; _cascadeSetTone(badge, running ? 'info' : 'warning'); badge.style.color = running ? '#93c5fd' : 'var(--warn)'; }
  if (start) start.disabled = running;
  if (kill) kill.style.display = running ? '' : 'none';
  const c = campaign.contract || {};
  if (summary) {
    const signal = campaign.signal_entry || {};
    const signalLine = signal.index_price == null ? '' : `<br>Index entry signal: ${escapeHtml(_cascadeNumber(signal.index_price))}${signal.exit_timestamp ? ' · Exit reached' : ''}`;
    const pricingLine = campaign.pricing_warning ? `<br><span class="is-warning" style="color:var(--warn);">${escapeHtml(campaign.pricing_warning)}</span>` : '';
    const rungs = Array.isArray(campaign.rungs) ? campaign.rungs : [];
    const filled = rungs.filter(r => r.state === 'filled').length;
    const ladderLine = rungs.length
      ? `<br>Ladder: ${escapeHtml(rungs.map(r => _TB_TF_LABEL[r.timeframe] || r.timeframe).join(' → '))} · ${escapeHtml(String(filled))} of ${escapeHtml(String(rungs.length))} rung${rungs.length === 1 ? '' : 's'} bought`
      : '';
    summary.innerHTML = `${escapeHtml(c.underlying || 'NIFTY')} ${escapeHtml(String(c.strike || '—'))} ${escapeHtml(c.option_type || 'CE')} · ${escapeHtml(c.expiry || '—')} · lot size ${escapeHtml(String(c.lot_size || '—'))}${ladderLine}<br>Entry stop: ${escapeHtml(_cascadeNumber(campaign.entry_stop))} · Target: ${escapeHtml(_cascadeNumber(campaign.target_index))} · Qualifying red closes: ${escapeHtml(String((campaign.qualifying_reds || []).length))}${signalLine}${pricingLine}`;
  }
  _renderCandleEntryMonitor(campaign);
}

function _candleEntryOverviewRow(label, value, detail = '', tone = '') {
  return `<div class="candle-entry-overview-row"><div class="candle-entry-overview-label">${escapeHtml(label)}</div><div class="candle-entry-overview-value${tone ? ` ${tone}` : ''}">${escapeHtml(value)}</div>${detail ? `<div class="candle-entry-overview-detail">${escapeHtml(detail)}</div>` : ''}</div>`;
}

function _candleEntryReadableState(status) {
  return ({
    WAITING_TWO_RED: 'Waiting for two red candles',
    ARMED: 'Entry stop armed',
    OPEN: 'Ladder fully bought — watching the target',
    OPEN_CLIMBING: 'Basket open — climbing to the next chart',
    OPEN_SIGNAL_ONLY: 'Historical entry signal open',
    AWAITING_OPTION_QUOTE: 'Waiting for an option quote',
    CLOSED: 'Campaign closed',
    EXPIRED: 'Campaign ended at expiry',
    KILLED: 'Campaign stopped',
  })[status] || String(status || 'WAITING').replaceAll('_', ' ').toLowerCase();
}

function _candleEntryEventDescription(event) {
  const name = String(event?.event || 'status').replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase());
  const bits = [];
  if (event?.rung != null) bits.push(`rung ${event.rung}`);
  if (event?.timeframe) bits.push(_TB_TF_LABEL[event.timeframe] || String(event.timeframe));
  if (event?.trigger != null) bits.push(`stop ${_cascadeNumber(event.trigger)}`);
  if (event?.stop != null) bits.push(`stop ${_cascadeNumber(event.stop)}`);
  if (event?.premium != null) bits.push(`premium ₹${_cascadeNumber(event.premium)}`);
  if (event?.lots != null) bits.push(`${event.lots} lot${event.lots === 1 ? '' : 's'}`);
  if (event?.index_price != null) bits.push(`index ${_cascadeNumber(event.index_price)}`);
  if (event?.target_index != null) bits.push(`target ${_cascadeNumber(event.target_index)}`);
  if (event?.option_premium != null) bits.push(`premium ₹${_cascadeNumber(event.option_premium)}`);
  if (event?.quantity != null) bits.push(`qty ${event.quantity}`);
  if (event?.net_pnl != null) bits.push(`net ₹${_cascadeNumber(event.net_pnl)}`);
  if (event?.reason) bits.push(String(event.reason).replaceAll('_', ' '));
  if (event?.action) bits.push(`${event.action} quote unavailable`);
  return { name, detail: bits.join(' · ') };
}

function _renderCandleEntryMonitor(campaign) {
  const monitor = _cascadeOptionsEl('candle-entry-monitor');
  const overview = _cascadeOptionsEl('candle-entry-monitor-overview');
  const candlesEl = _cascadeOptionsEl('candle-entry-monitor-candles');
  const rungsEl = _cascadeOptionsEl('candle-entry-monitor-rungs');
  const updated = _cascadeOptionsEl('candle-entry-monitor-updated');
  const eventsEl = _cascadeOptionsEl('candle-entry-events');
  const eventCount = _cascadeOptionsEl('candle-entry-event-count');
  if (!monitor || !overview || !candlesEl || !eventsEl) return;
  monitor.hidden = false;
  const mother = campaign.mother || {};
  const latest = campaign.latest_closed_candle || mother;
  const qualifying = Array.isArray(campaign.qualifying_reds) ? campaign.qualifying_reds : [];
  const openFill = campaign.open_fill;
  const rungs = Array.isArray(campaign.rungs) ? campaign.rungs : [];
  const activeRung = rungs.find(r => r.state === 'armed' || r.state === 'watching') || null;
  const filledRungs = rungs.filter(r => r.state === 'filled');
  const tfLabel = tf => _TB_TF_LABEL[tf] || String(tf || '—');
  const watchLabel = activeRung ? tfLabel(activeRung.timeframe) : tfLabel(campaign.timeframe);
  const rounds = Array.isArray(campaign.rounds) ? campaign.rounds : [];
  const lastRound = rounds.length ? rounds[rounds.length - 1] : null;
  const status = String(campaign.status || 'WAITING_TWO_RED');
  const stateTone = campaign.running ? 'is-info' : (status === 'CLOSED' ? 'is-positive' : 'is-warning');
  const recoveryValue = `${qualifying.length} of 2 qualifying red candle${qualifying.length === 1 ? '' : 's'}`;
  const recoveryDetail = qualifying.length ? `Latest qualifying candle: ${_cascadeOptionsTimestamp(qualifying[qualifying.length - 1])}` : `A red ${watchLabel} candle must close below the previous candle.`;
  const stopValue = campaign.entry_stop == null ? 'Not armed yet' : _cascadeNumber(campaign.entry_stop);
  const stopDetail = campaign.entry_stop_armed_at ? `Armed after the ${_cascadeOptionsTimestamp(campaign.entry_stop_armed_at)} candle — the FIRST red's close.` : 'It will be set after the second qualifying red candle.';
  let positionValue = 'No open paper position';
  let positionDetail = 'P&L will appear once a quote-backed paper trade has closed.';
  let nextStep = qualifying.length < 2 ? `Waiting for another qualifying red candle on the ${watchLabel} chart` : `Watching for a ${watchLabel} recovery above the entry stop`;
  if (openFill) {
    positionValue = `Open · ${filledRungs.length} rung${filledRungs.length === 1 ? '' : 's'} · ${openFill.quantity || '—'} quantity`;
    positionDetail = `Last buy at ${_cascadeNumber(openFill.index_price)}${openFill.option_premium == null ? '' : ` · option premium ₹${_cascadeNumber(openFill.option_premium)}`} · ${openFill.lots || 1} lot${(openFill.lots || 1) === 1 ? '' : 's'}`;
    nextStep = activeRung
      ? `Watching the target on every chart, and the ${tfLabel(activeRung.timeframe)} chart for rung ${activeRung.rung} after a new low`
      : 'Ladder fully bought — watching the index target to close the whole basket';
  } else if (campaign.signal_entry?.index_price != null) {
    positionValue = campaign.signal_entry.exit_timestamp ? 'Historical signal closed' : 'Historical signal open';
    positionDetail = `Index signal at ${_cascadeNumber(campaign.signal_entry.index_price)} · option premium and P&L are unavailable for replay.`;
    nextStep = campaign.signal_entry.exit_timestamp ? 'Historical replay complete' : 'Watching the historical index target';
  } else if (lastRound) {
    positionValue = `Closed · net P&L ₹${_cascadeNumber(lastRound.net_pnl)}`;
    positionDetail = `${String(lastRound.exit_reason || 'exit').replaceAll('_', ' ')} at ${_cascadeOptionsTimestamp(lastRound.closed_at)}`;
    nextStep = 'Campaign has completed';
  }
  overview.innerHTML = [
    _candleEntryOverviewRow('Current status', _candleEntryReadableState(status), campaign.pricing_mode === 'signal_only_dhan' ? 'Historical replay. This checks NIFTY price movement only; no option P&L is created.' : 'Paper campaign only. No live order will be sent.', stateTone),
    _candleEntryOverviewRow('Recovery progress', recoveryValue, recoveryDetail),
    _candleEntryOverviewRow('Entry stop', stopValue, stopDetail, campaign.entry_stop == null ? '' : 'is-warning'),
    _candleEntryOverviewRow('Index target', campaign.target_index == null ? 'Set after entry' : _cascadeNumber(campaign.target_index), campaign.target_index == null ? 'The target is calculated once the entry is triggered.' : 'The paper position closes when this target is reached.'),
    _candleEntryOverviewRow('Paper position', positionValue, positionDetail, openFill ? 'is-positive' : ''),
    _candleEntryOverviewRow('What happens next', nextStep),
  ].join('');
  if (rungsEl) {
    const rungStateLabel = { filled: 'BOUGHT', armed: 'STOP ARMED', watching: 'WATCHING', waiting: 'WAITING' };
    rungsEl.innerHTML = rungs.length ? rungs.map(rung => {
      const fill = rung.fill;
      const stateTxt = rungStateLabel[rung.state] || String(rung.state || '').toUpperCase();
      const stopTxt = fill ? _cascadeNumber(fill.index_price) : (rung.entry_stop == null ? '—' : _cascadeNumber(rung.entry_stop));
      const filledTxt = fill ? _cascadeOptionsTimestamp(fill.timestamp) : '—';
      const premiumTxt = fill && fill.option_premium != null ? `₹${_cascadeNumber(fill.option_premium)}` : '—';
      const tone = rung.state === 'filled' ? 'candle-entry-strong' : (rung.state === 'armed' ? '' : 'candle-entry-muted');
      return `<tr><td class="${tone}">${rung.rung}</td><td class="${tone}">${escapeHtml(tfLabel(rung.timeframe))}</td><td class="${tone}">${rung.lots} (${rung.quantity})</td><td class="${tone}">${escapeHtml(stateTxt)}</td><td class="${tone}">${escapeHtml(stopTxt)}</td><td class="${tone}">${escapeHtml(filledTxt)}</td><td class="${tone}">${escapeHtml(premiumTxt)}</td></tr>`;
    }).join('') : '<tr><td colspan="7" class="candle-entry-empty">The ladder appears when a campaign starts.</td></tr>';
  }
  const candleRow = (name, candle) => `<tr><td class="candle-entry-strong">${escapeHtml(name)}</td><td class="candle-entry-muted">${escapeHtml(_cascadeOptionsTimestamp(candle.timestamp))}${candle.timeframe ? ` · ${escapeHtml(tfLabel(candle.timeframe))}` : ''}</td><td>${escapeHtml(_cascadeNumber(candle.open))}</td><td>${escapeHtml(_cascadeNumber(candle.high))}</td><td>${escapeHtml(_cascadeNumber(candle.low))}</td><td>${escapeHtml(_cascadeNumber(candle.close))}</td></tr>`;
  candlesEl.innerHTML = candleRow('Mother candle', mother) + candleRow('Latest closed candle', latest);
  if (updated) updated.textContent = `Last screen refresh: ${new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })} IST`;
  const events = Array.isArray(campaign.events) ? campaign.events : [];
  if (eventCount) eventCount.textContent = `${events.length} update${events.length === 1 ? '' : 's'}`;
  eventsEl.innerHTML = events.length ? events.slice(-18).reverse().map(event => {
    const text = _candleEntryEventDescription(event);
    return `<tr><td>${escapeHtml(_cascadeOptionsTimestamp(event.timestamp))}</td><td>${escapeHtml(text.name)}</td><td class="candle-entry-muted">${escapeHtml(text.detail || 'Campaign status updated.')}</td></tr>`;
  }).join('') : '<tr><td colspan="3" class="candle-entry-empty">Updates will appear here when a candle qualifies, the entry stop arms, a paper trade opens or closes, or an option quote is unavailable.</td></tr>';
}

async function refreshCandleEntryStatus() {
  try {
    const response = await fetch('/api/candle-entry/paper/status', { credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data?.detail || 'Unable to load Candle Entry campaign');
    _renderCandleEntryStatus(data);
  } catch (error) {
    const summary = _cascadeOptionsEl('candle-entry-summary');
    if (summary) summary.textContent = error.message || 'Unable to load Candle Entry campaign.';
  }
}

function _setCandleEntryFormStatus(message, tone = 'muted') {
  const el = _cascadeOptionsEl('candle-entry-form-status');
  if (!el) return;
  el.textContent = message || '';
  _cascadeSetTone(el, tone);
  el.style.color = ({ muted: 'var(--muted)', error: 'var(--danger)', success: '#6ee7b7', busy: '#fde68a' }[tone] || 'var(--muted)');
}

function _ceRenderTimeframes() {
  const select = _cascadeOptionsEl('candle-entry-timeframe');
  if (!select) return;
  const chosen = select.value || '5m';
  select.innerHTML = _TB_LADDER.map((tf) => {
    const climb = _tbLadderFrom(tf).map((step) => _TB_TF_LABEL[step]).join(' → ');
    return `<option value="${tf}"${tf === chosen ? ' selected' : ''}>${_TB_TF_LABEL[tf]} · ${climb}</option>`;
  }).join('');
  select.value = chosen;
  const mother = _cascadeOptionsEl('candle-entry-mother-timestamp');
  if (mother) mother.dataset.pfCalendarMinutes = _MOTHER_MINUTES_BY_TF[select.value] || '';
}

async function startCandleEntryPaper() {
  const timeframe = _cascadeOptionsEl('candle-entry-timeframe')?.value || '1h';
  const timestamp = _cascadeOptionsEl('candle-entry-mother-timestamp')?.value;
  if (!timestamp) { _setCandleEntryFormStatus('Choose a completed mother timestamp.', 'error'); return; }
  _setCandleEntryFormStatus(`Checking the ${_TB_TF_LABEL[timeframe] || timeframe} mother candle…`, 'busy');
  let response;
  try {
    response = await fetch('/api/candle-entry/paper/start', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mother_timestamp: timestamp, timeframe }) });
  } catch (error) {
    _setCandleEntryFormStatus(error?.message || 'Unable to reach the paper campaign service.', 'error');
    return;
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !['started', 'replayed'].includes(data.status)) { _setCandleEntryFormStatus(data?.detail || 'Candle Entry campaign did not start.', 'error'); return; }
  _setCandleEntryFormStatus(data.status === 'replayed' ? 'Historical replay completed. Fixed-strike P&L is withheld.' : 'Ladder paper campaign started. No live order will be sent.', 'success');
  _renderCandleEntryStatus({ campaign: data.campaign });
}

async function killCandleEntryPaper() {
  const confirmed = await customConfirm('Kill this <strong>paper-only</strong> Candle Entry ladder and close any open paper basket at the current quote? No Dhan order is sent.', { title: 'Kill Candle Entry', icon: ICO.warn(28), okText: 'Kill & close', danger: true });
  if (!confirmed) return;
  const response = await fetch('/api/candle-entry/paper/kill', { method: 'POST', credentials: 'same-origin' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.status !== 'killed') { _setCandleEntryFormStatus(data?.detail || 'Candle Entry kill could not be confirmed.', 'error'); return; }
  _setCandleEntryFormStatus('Candle Entry campaign killed.', 'success');
  _renderCandleEntryStatus({ campaign: data.campaign });
}

function _cascadeOptionsChartSvg(payload) {
  const candles = Array.isArray(payload?.candles) ? payload.candles : [];
  if (!candles.length) return '';
  const W = 1180, H = 470, padL = 82, padR = 66, padT = 18, padB = 35;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const n = candles.length, cw = plotW / n;
  let lo = Math.min(...candles.map(c => Number(c.l)));
  let hi = Math.max(...candles.map(c => Number(c.h)));
  const mother = payload.mother || {};
  if (Number.isFinite(Number(mother.h))) hi = Math.max(hi, Number(mother.h));
  if (Number.isFinite(Number(mother.l))) lo = Math.min(lo, Number(mother.l));
  const padding = Math.max((hi - lo) * .06, 1);
  hi += padding; lo -= padding;
  const Y = p => padT + ((hi - p) / (hi - lo || 1)) * plotH;
  const X = i => padL + i * cw + cw / 2;
  const number = value => Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  const stamp = value => new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value));
  const parts = [];
  for (let i = 0; i <= 4; i += 1) {
    const price = lo + (hi - lo) * i / 4, y = Y(price);
    parts.push(`<line x1="${padL}" y1="${y.toFixed(1)}" x2="${padL + plotW}" y2="${y.toFixed(1)}" stroke="rgba(148,163,184,.16)"/>`);
    parts.push(`<text x="${padL + plotW + 7}" y="${(y + 3).toFixed(1)}" fill="#94a3b8" font-size="9.5" font-family="monospace">${number(price)}</text>`);
  }
  const tickCount = Math.min(6, n);
  for (let i = 0; i < tickCount; i += 1) {
    const at = Math.round((n - 1) * i / Math.max(tickCount - 1, 1));
    parts.push(`<text x="${X(at).toFixed(1)}" y="${H - 10}" fill="#94a3b8" font-size="9" font-family="monospace" text-anchor="middle">${escapeHtml(stamp(candles[at].t))}</text>`);
  }
  const bodyW = Math.max(Math.min(cw * .66, 9), 1);
  // Overnight gap as a synthetic candle spanning prev close -> new open. Drawn
  // before the real candles so it sits behind them. See the Terminal canvas for
  // the full reasoning; NSE only gaps across a session break.
  const GAP_BREAK_MS = 4 * 3600 * 1000;
  for (let i = 1; i < n; i += 1) {
    const prevC = Number(candles[i - 1].c), openP = Number(candles[i].o);
    if (Date.parse(candles[i].t) - Date.parse(candles[i - 1].t) < GAP_BREAK_MS) continue;
    if (!Number.isFinite(prevC) || !Number.isFinite(openP) || prevC === openP) continue;
    const gTop = Y(Math.max(prevC, openP)), gBot = Y(Math.min(prevC, openP));
    const gw = bodyW + 4, gx = X(i) - gw / 2;
    const gColor = openP > prevC ? 'var(--green)' : 'var(--red)';
    parts.push(`<rect x="${gx.toFixed(1)}" y="${gTop.toFixed(1)}" width="${gw.toFixed(1)}" height="${Math.max(gBot - gTop, 1).toFixed(1)}" fill="${gColor}" opacity=".22" stroke="${gColor}" stroke-opacity=".6" stroke-width="0.8"/>`);
  }
  candles.forEach((candle, i) => {
    const isGap = candle.gap_direction === 'up' || candle.gap_direction === 'down';
    const up = Number(candle.c) >= Number(candle.o);
    const color = candle.gap_direction === 'up' ? 'var(--green)' : candle.gap_direction === 'down' ? 'var(--red)' : (up ? '#39c6b1' : '#ec5f91');
    const x = X(i), top = Y(Math.max(Number(candle.o), Number(candle.c))), bottom = Y(Math.min(Number(candle.o), Number(candle.c)));
    parts.push(`<line x1="${x.toFixed(1)}" y1="${Y(Number(candle.h)).toFixed(1)}" x2="${x.toFixed(1)}" y2="${Y(Number(candle.l)).toFixed(1)}" stroke="${color}" stroke-width="1"/>`);
    parts.push(`<rect x="${(x - bodyW / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bodyW.toFixed(1)}" height="${Math.max(bottom - top, 1).toFixed(1)}" fill="${color}" ${isGap ? 'opacity=".95"' : ''}/>`);
    if (candle.is_mother) {
      parts.push(`<rect x="${(x - Math.max(bodyW, 6) / 2 - 3).toFixed(1)}" y="${padT}" width="${(Math.max(bodyW, 6) + 6).toFixed(1)}" height="${plotH.toFixed(1)}" fill="#a78bfa" opacity=".11"/>`);
      parts.push(`<rect x="${(x - bodyW / 2 - 1).toFixed(1)}" y="${(Y(Number(candle.h)) - 1).toFixed(1)}" width="${(bodyW + 2).toFixed(1)}" height="${Math.max(Y(Number(candle.l)) - Y(Number(candle.h)) + 2, 4).toFixed(1)}" fill="none" stroke="#a78bfa" stroke-width="1.5"/>`);
      parts.push(`<text x="${x.toFixed(1)}" y="${Math.max(Y(Number(candle.h)) - 8, 12).toFixed(1)}" fill="#c4b5fd" font-size="9.5" font-family="monospace" font-weight="700" text-anchor="middle">MC</text>`);
    }
  });
  if (Number.isFinite(Number(mother.h))) {
    const y = Y(Number(mother.h));
    parts.push(`<line x1="${padL}" y1="${y.toFixed(1)}" x2="${padL + plotW}" y2="${y.toFixed(1)}" stroke="#a78bfa" stroke-width="1.1" stroke-dasharray="5 3"/>`);
    parts.push(`<text x="${padL - 6}" y="${(y + 3).toFixed(1)}" fill="#c4b5fd" font-size="9.5" font-family="monospace" text-anchor="end">MOTHER ${number(mother.h)}</text>`);
  }
  return `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" aria-label="NIFTY 5 minute mother candle chart">${parts.join('')}</svg>`;
}

async function loadCascadeOptionsChart() {
  const timestamp = _cascadeOptionsEl('cascade-options-mother-timestamp')?.value || _lastCascadeOptionsStatus?.campaign?.mother?.timestamp;
  const chart = _cascadeOptionsEl('cascade-options-chart');
  const meta = _cascadeOptionsEl('cascade-options-chart-meta');
  const overlay = _cascadeOptionsEl('cascade-options-chart-overlay');
  if (!timestamp) { _cascadeOptionsSetFormStatus('Choose a mother timestamp first.', 'error'); return; }
  if (overlay) { overlay.classList.add('is-open'); overlay.setAttribute('aria-hidden', 'false'); }
  if (chart) chart.innerHTML = '<div class="pf-cascade-chart-empty">Loading actual closed NIFTY 5m candles…</div>';
  try {
    const response = await fetch(`/api/cascade/paper/chart?mother_timestamp=${encodeURIComponent(timestamp)}`, { credentials: 'same-origin', cache: 'no-store' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== 'ok') throw new Error(_apiErrorMessage(data, `Chart failed (${response.status})`));
    if (chart) chart.innerHTML = _cascadeOptionsChartSvg(data);
    const mother = data.mother || {};
    const fieldMap = { open: mother.native_open, high: mother.native_high, low: mother.native_low, close: mother.native_close };
    Object.entries(fieldMap).forEach(([key, value]) => {
      const field = _cascadeOptionsEl(`cascade-options-mother-${key}`);
      if (field && !field.value && Number.isFinite(Number(value))) field.value = Number(value).toFixed(2);
    });
    if (meta) meta.textContent = `${data.candles.length} closed candles · visual gap adjustment ON · paper geometry remains native OHLC`;
    _cascadeOptionsSetFormStatus('Candle loaded from Dhan. You may leave OHLC blank next time; it is auto-loaded at start.', 'success');
  } catch (error) {
    if (chart) chart.innerHTML = `<div class="pf-cascade-chart-empty">${escapeHtml(error.message || 'Unable to load chart.')}</div>`;
    if (meta) meta.textContent = 'Chart unavailable';
    _cascadeOptionsSetFormStatus(error.message || 'Unable to load chart.', 'error');
  }
}

function hideCascadeOptionsChart() {
  const overlay = _cascadeOptionsEl('cascade-options-chart-overlay');
  if (overlay) { overlay.classList.remove('is-open'); overlay.setAttribute('aria-hidden', 'true'); }
}

async function startCascadeOptionsPaper() {
  const value = key => _cascadeOptionsEl(`cascade-options-mother-${key}`)?.value;
  const payload = {
    mother_timestamp: value('timestamp'),
    mother_open: value('open') ? Number(value('open')) : null,
    mother_high: value('high') ? Number(value('high')) : null,
    mother_low: value('low') ? Number(value('low')) : null,
    mother_close: value('close') ? Number(value('close')) : null,
    rung_inr: Number(_cascadeOptionsEl('cascade-options-rung-inr')?.value),
    ce_offset_steps: Number(_cascadeOptionsEl('cascade-options-offset')?.value),
  };
  const ohlc = [payload.mother_open, payload.mother_high, payload.mother_low, payload.mother_close];
  if (!payload.mother_timestamp || !Number.isFinite(payload.rung_inr) || (ohlc.some(v => v !== null) && ohlc.some(v => !Number.isFinite(v)))) {
    _cascadeOptionsSetFormStatus('Choose a timestamp and valid INR rung size. OHLC may be all blank or all entered.', 'error');
    return;
  }
  const selectedDate = String(payload.mother_timestamp).slice(0, 10);
  const istDate = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
  const selectedDay = Date.parse(`${selectedDate}T00:00:00Z`);
  const todayDay = Date.parse(`${istDate}T00:00:00Z`);
  const ageDays = Math.round((todayDay - selectedDay) / 86400000);
  if (!Number.isFinite(ageDays) || ageDays < 0) {
    _cascadeOptionsSetFormStatus('Mother candle cannot be in the future (IST).', 'error');
    return;
  }
  if (ageDays > 14) {
    _cascadeOptionsSetFormStatus('Mother candle is older than the 14-day paper replay window. Use Signal Replay for older history.', 'error');
    return;
  }
  const button = _cascadeOptionsEl('cascade-options-start');
  if (button) { button.disabled = true; button.textContent = 'Selecting fixed CE and starting paper monitor…'; }
  _cascadeOptionsSetFormStatus('Selecting next-weekly CE from the ScripMaster. No order will be sent.', 'busy');
  try {
    const response = await fetch('/api/cascade/paper/start', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await response.json().catch(() => ({}));
    const apiError = data?.error || {};
    const errorMessage = apiError.detail || apiError.message || data?.detail || data?.message;
    if (!response.ok || data.status !== 'started') throw new Error(errorMessage || `Campaign did not start (${response.status})`);
    _cascadeOptionsSetFormStatus('Paper campaign started. Only closed NIFTY 5m candles are processed.', 'success');
    _renderCascadeOptionsStatus({ status: 'ok', mode: 'paper', live_gate: _lastCascadeOptionsStatus?.live_gate, campaign: data.campaign });
  } catch (error) {
    _cascadeOptionsSetFormStatus(error.message || 'Campaign start failed.', 'error');
  } finally {
    if (button) { button.disabled = false; button.textContent = '▶ Start paper campaign'; }
  }
}

async function stopCascadeOptionsPaper() {
  try {
    const response = await fetch('/api/cascade/paper/stop', { method: 'POST', credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data?.detail || 'Unable to stop paper campaign');
    _cascadeOptionsSetFormStatus('Paper monitoring stopped; its state remains saved.', 'success');
    await refreshCascadeOptionsStatus();
  } catch (error) {
    _cascadeOptionsSetFormStatus(error.message || 'Unable to stop paper campaign.', 'error');
  }
}

async function killCascadeOptionsPaper() {
  const campaign = _lastCascadeOptionsStatus?.campaign;
  const openQuantity = Number(campaign?.open_quantity || 0);
  const pending = Number(campaign?.pending_inr || 0);
  const confirmed = await customConfirm(
    `Kill this <strong>paper-only</strong> Cascade campaign?<br><span style="font-size:11px;color:var(--muted);">This cancels ${pending > 0 ? 'all collected and pending rungs' : 'all pending rungs'} and ${openQuantity > 0 ? `records an immediate paper exit for ${openQuantity} open units at the current option quote` : 'stops the campaign'}. No Dhan order is sent.</span>`,
    { title: 'Kill & close paper campaign', icon: ICO.warn(28), okText: 'Kill & close', danger: true }
  );
  if (!confirmed) return;
  const button = _cascadeOptionsEl('cascade-options-kill');
  if (button) { button.disabled = true; button.textContent = 'Closing…'; }
  _cascadeOptionsSetFormStatus('Cancelling paper rungs and requesting a current option quote for the paper exit…', 'busy');
  try {
    const response = await fetch('/api/cascade/paper/kill', { method: 'POST', credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== 'killed') throw new Error(_apiErrorMessage(data, `Kill failed (${response.status})`));
    _cascadeOptionsSetFormStatus(`Campaign killed. ${Number(data.cancelled_rungs || []).length} paper rung(s) cancelled; any open basket is recorded as a manual paper exit.`, 'success');
    _renderCascadeOptionsStatus({ status: 'ok', mode: 'paper', live_gate: _lastCascadeOptionsStatus?.live_gate, campaign: data.campaign });
  } catch (error) {
    _cascadeOptionsSetFormStatus(error.message || 'Kill & close could not be confirmed.', 'error');
    await refreshCascadeOptionsStatus();
  } finally {
    if (button) { button.disabled = false; button.textContent = '■ Kill & close'; }
  }
}

window.initCascadeOptionsPage = initCascadeOptionsPage;
window.startCascadeOptionsPaper = startCascadeOptionsPaper;
window.stopCascadeOptionsPaper = stopCascadeOptionsPaper;
window.loadCascadeOptionsChart = loadCascadeOptionsChart;
window.hideCascadeOptionsChart = hideCascadeOptionsChart;
window.startCandleEntryPaper = startCandleEntryPaper;
window.killCandleEntryPaper = killCandleEntryPaper;
window.killCascadeOptionsPaper = killCascadeOptionsPaper;

// ══════════════════════════════════════════════════════════════
//  OPTIONS CASCADE — two-tab paper (Fib-boundary + Candle-entry)
// ══════════════════════════════════════════════════════════════
let _fibBoundaryPollTimer = null;
// Every running ladder, keyed by symbol -- the chart and the monitor buttons
// each need THEIR campaign, not "the" campaign.
let _lastFibBoundaryStatus = {};
let _fibBoundaryLiveAvailable = false;

// Everything options lives on one page now; each tab owns its own engine.
// The old Signal Ladder replay tab was retired 2026-07-30, and the Test Bench
// moved in beside the two paper strategies the same day — both on Phil's call,
// so the top nav stays one row.
const _OC_TABS = ['fib', 'candle', 'space', 'recovery', 'bench'];

const _INSIGHTS_TABS = ['heatmap', 'study'];

function showInsightsTab(event, el) {
  const tab = (el || event?.currentTarget)?.getAttribute('data-insights-tab') || 'heatmap';
  document.querySelectorAll('#insights-page .oc-tab').forEach(b => {
    const selected = b.getAttribute('data-insights-tab') === tab;
    b.classList.toggle('is-active', selected);
    b.setAttribute('aria-selected', String(selected));
    b.tabIndex = selected ? 0 : -1;
  });
  _INSIGHTS_TABS.forEach(name => {
    const panel = document.getElementById(`insights-${name}`);
    if (panel) {
      const selected = name === tab;
      panel.style.display = selected ? '' : 'none';
      panel.setAttribute('aria-hidden', String(!selected));
    }
  });
  _insightsStartTab(tab);
}

// Each panel's own script exposes a starter and no longer self-starts inside
// the app shell, so a tab only begins polling once it is actually on screen.
// Both are idempotent, so re-showing a tab is free.
const _insightsStarted = {};
function _insightsStartTab(tab) {
  if (tab === 'heatmap' && typeof window.pfStartMarketMovers === 'function') {
    window.pfStartMarketMovers();
    _insightsStarted.heatmap = true;
  }
  if (tab === 'study' && !_insightsStarted.study && typeof window.pfStartStudyLounge === 'function') {
    window.pfStartStudyLounge();
    _insightsStarted.study = true;
  }
}

function initInsightsPage() {
  const active = document.querySelector('#insights-page .oc-tab.is-active');
  _insightsStartTab(active?.getAttribute('data-insights-tab') || 'heatmap');
}

function showOptionsCascadeTab(event, el) {
  const tab = (el || event?.currentTarget)?.getAttribute('data-oc-tab') || 'fib';
  document.querySelectorAll('#options-cascade-page .oc-tab').forEach(b => {
    const selected = b.getAttribute('data-oc-tab') === tab;
    b.classList.toggle('is-active', selected);
    b.setAttribute('aria-selected', String(selected));
    b.tabIndex = selected ? 0 : -1;
  });
  _OC_TABS.forEach(name => {
    const panel = document.getElementById(`oc-tab-${name}`);
    if (panel) {
      const selected = name === tab;
      panel.style.display = selected ? '' : 'none';
      panel.setAttribute('aria-hidden', String(!selected));
    }
  });
  if (tab === 'candle') refreshCandleEntryStatus();
  else if (tab === 'bench') initTestBenchPage();
  else if (tab === 'space') refreshFibSpaceStatus();
  else if (tab === 'recovery') refreshRecoveryStatus();
  else refreshFibBoundaryStatus();
}

// ── Fib space (auto geometry) ─────────────────────────────────────
// The only Cascade that finds its own mothers. The panel is a monitor, not a
// control surface: everything except which underlying to watch was decided by
// the backtest, so there is nothing here to tune.

function _fsxSetFormStatus(message, tone = 'muted') {
  const el = document.getElementById('fsx-form-status');
  if (!el) return;
  el.textContent = message || '';
  _cascadeSetTone(el, tone);
  el.style.color = ({ muted: 'var(--muted)', error: 'var(--danger)', success: '#6ee7b7', busy: '#fde68a' }[tone] || 'var(--muted)');
}

function _renderFibSpaceStatus(payload) {
  const running = payload?.status === 'ok' && payload?.running;
  const book = payload?.book || {};
  const badge = document.getElementById('fsx-badge');
  const contract = document.getElementById('fsx-contract');
  const gist = document.getElementById('fsx-gist');
  const summary = document.getElementById('fsx-summary');
  const empty = document.getElementById('fsx-empty');
  const active = document.getElementById('fsx-active');
  const errorEl = document.getElementById('fsx-error');
  const startBtn = document.getElementById('fsx-start');
  const stopBtn = document.getElementById('fsx-stop');
  const windowEl = document.getElementById('fsx-window');
  const motherBox = document.getElementById('fsx-mother-box');
  const scanBadge = document.getElementById('fsx-scan-badge');

  if (startBtn) startBtn.style.display = running ? 'none' : '';
  if (stopBtn) stopBtn.style.display = running ? '' : 'none';
  if (windowEl) windowEl.classList.toggle('is-active', !!running);
  // Naming a mother needs an open book to put it in, so the field only appears
  // once the run is going.
  if (motherBox) motherBox.style.display = running ? '' : 'none';
  if (scanBadge) scanBadge.textContent = running && book.auto_scan ? 'auto-scan also ON' : '';

  if (!running) {
    if (badge) { badge.textContent = payload?.status === 'ok' ? 'STOPPED' : 'IDLE'; _cascadeSetTone(badge); badge.style.color = 'var(--muted)'; badge.style.borderColor = 'var(--border)'; }
    if (contract) contract.textContent = 'No paper run';
    if (gist) gist.textContent = 'Start a run to watch it find mothers and ladder into them.';
    if (summary) summary.innerHTML = '';
    if (empty) empty.style.display = '';
    if (active) active.style.display = 'none';
    if (errorEl) errorEl.style.display = 'none';
    return;
  }

  const symbol = String(payload.symbol || '').toUpperCase();
  if (badge) { badge.textContent = 'RUNNING'; _cascadeSetTone(badge, 'success'); badge.style.color = '#6ee7b7'; badge.style.borderColor = '#6ee7b7'; }
  if (contract) contract.textContent = `${symbol} · ${book.geometry_timeframe || '15m'} geometry, ${book.entry_timeframe || '5m'} entries · ${payload.lot_size || '—'} units/lot`;
  // The last poll is the honest liveness signal: outside the session the loop
  // deliberately does nothing, so "no new fills" is not evidence either way.
  const lastPoll = book.last_poll ? new Date(book.last_poll).toLocaleTimeString('en-IN', { hour12: false }) : 'not yet';
  if (gist) gist.textContent = `${book.campaigns || 0} campaign${book.campaigns === 1 ? '' : 's'} · last poll ${lastPoll} IST`;

  if (summary) {
    summary.innerHTML = [
      _cascadeOptionsMetric('Campaigns', String(book.campaigns ?? 0)),
      _cascadeOptionsMetric('Trading now', String(book.trading ?? 0), (book.trading || 0) > 0 ? '#6ee7b7' : 'var(--text)'),
      _cascadeOptionsMetric('Open qty', String(book.open_quantity ?? 0)),
      _cascadeOptionsMetric('Realised ₹', _cascadeOptionsMoney(book.realised || 0), (book.realised || 0) >= 0 ? '#6ee7b7' : 'var(--danger)'),
    ].join('');
  }

  if (errorEl) {
    if (payload.last_error) { errorEl.style.display = ''; errorEl.textContent = `Last poll error — ${payload.last_error}`; }
    else errorEl.style.display = 'none';
  }

  const rows = Array.isArray(book.campaign_rows) ? book.campaign_rows : [];
  if (empty) empty.style.display = rows.length ? 'none' : '';
  if (active) active.style.display = rows.length ? '' : 'none';

  const body = document.getElementById('fsx-campaigns');
  if (body) {
    body.innerHTML = rows.map(row => {
      const halted = row.status === 'halted';
      const stateColour = halted ? 'var(--danger)' : row.status === 'trading' ? '#6ee7b7' : 'var(--muted)';
      // Three different states that must not look alike:
      //   unpriced        the driver refused to guess a fill it missed
      //   nothing banked  position still open -- realised P&L is 0 but that is
      //                   NOT a flat result, and showing Rs 0.00 reads as one
      //   a real number   at least one round has closed
      let net = 'unpriced';
      let netColour = 'var(--warn)';
      let netTitle = 'A leg could not be quoted when it happened, so this campaign reports no P&L rather than a guessed one.';
      if (!row.unpriced && !row.closed_rounds) {
        net = '—';
        netColour = 'var(--muted)';
        netTitle = 'Nothing banked yet — the position is still open.';
      } else if (!row.unpriced) {
        net = _cascadeOptionsMoney(row.net || 0);
        netColour = (row.net || 0) >= 0 ? '#6ee7b7' : 'var(--danger)';
        netTitle = `${row.closed_rounds} round${row.closed_rounds === 1 ? '' : 's'} banked`;
      }
      const mother = new Date(row.mother).toLocaleString('en-IN', { hour12: false, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
      // A mother you chose and one the scanner found are different claims about
      // why this trade exists, so the table says which.
      const mark = row.source === 'manual'
        ? '<span title="You named this mother" style="color:#38bdf8;">◈</span> '
        : '<span title="Found by the pivot scanner" style="color:var(--muted);">◇</span> ';
      return `<tr style="text-align:right;border-top:1px solid var(--border);cursor:pointer;" data-pf-action="openFibSpaceCampaign" data-fsx-campaign="${escapeHtml(row.id)}" title="Open premiums, capital and chart">
        <td style="text-align:left;padding:6px 8px;">${mark}${escapeHtml(mother)}</td>
        <td style="padding:6px 8px;">${_cascadeNumber(row.mother_high)}</td>
        <td style="text-align:left;padding:6px 8px;color:${stateColour};" title="${escapeHtml(row.halt_reason || '')}">${escapeHtml(String(row.status || '').toUpperCase())}</td>
        <td style="padding:6px 8px;">${row.fills ?? 0}</td>
        <td style="padding:6px 8px;">${row.open_quantity ?? 0}</td>
        <td style="padding:6px 8px;color:${netColour};" title="${escapeHtml(netTitle)}">${escapeHtml(net)}</td>
        <td style="padding:6px 8px;color:#38bdf8;" title="Open premiums, capital and chart">↗</td>
      </tr>`;
    }).join('');
  }

  const note = document.getElementById('fsx-unpriced-note');
  if (note) {
    const unpriced = Number(book.unpriced_legs || 0);
    if (unpriced) {
      note.style.display = '';
      note.textContent = `${unpriced} leg${unpriced === 1 ? '' : 's'} could not be quoted when the decision was first seen — usually the run was restarting. Those campaigns report no P&L rather than a guessed one.`;
    } else note.style.display = 'none';
  }
}

async function refreshFibSpaceStatus() {
  let response;
  try {
    response = await fetch('/api/fib-space/paper/status', { credentials: 'same-origin' });
  } catch (error) {
    return;
  }
  const data = await response.json().catch(() => ({}));
  if (response.ok) _renderFibSpaceStatus(data);
  // Keep an open trade sheet current without it closing under the cursor.
  if (_fsxOpenCampaignId) await _fsxLoadCampaignDetail();
}

// The campaign currently opened in the detail drawer, so the 3s poll can
// refresh its numbers without the drawer closing under the cursor.
let _fsxOpenCampaignId = null;

function _fsxMoney(value) {
  return value === null || value === undefined ? '—' : _cascadeOptionsMoney(value);
}

async function openFibSpaceCampaign(event, el) {
  const id = (el || event?.currentTarget)?.getAttribute('data-fsx-campaign');
  if (!id) return;
  // The row is a toggle. There is deliberately no "Close" button on the trade
  // sheet: in a trading app that word means square off the position, and a
  // control that reads as "close this trade" but only collapses a panel is a
  // hazard the moment somebody is in a hurry.
  if (_fsxOpenCampaignId === id) { closeFibSpaceDetail(); return; }
  _fsxOpenCampaignId = id;
  await _fsxLoadCampaignDetail();
}

function closeFibSpaceDetail() {
  _fsxOpenCampaignId = null;
  const box = document.getElementById('fsx-detail');
  if (box) box.style.display = 'none';
  hideFibSpaceChart();
}

async function _fsxLoadCampaignDetail() {
  if (!_fsxOpenCampaignId) return;
  let response;
  try {
    response = await fetch(`/api/fib-space/paper/campaign?campaign_id=${encodeURIComponent(_fsxOpenCampaignId)}`, { credentials: 'same-origin' });
  } catch (error) { return; }
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.status !== 'ok') { closeFibSpaceDetail(); return; }
  _renderFibSpaceDetail(data.campaign);
}

function _renderFibSpaceDetail(c) {
  const box = document.getElementById('fsx-detail');
  if (!box || !c) return;
  box.style.display = '';

  const title = document.getElementById('fsx-detail-title');
  const contract = document.getElementById('fsx-detail-contract');
  const mother = new Date(c.mother.at).toLocaleString('en-IN', { hour12: false, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  if (title) title.textContent = `${c.source === 'manual' ? '◈' : '◇'} Mother ${mother} · high ${_cascadeNumber(c.mother.high)}`;
  if (contract) {
    contract.textContent = c.contract
      ? `${c.contract.underlying || c.symbol.toUpperCase()} ${Number(c.contract.strike).toLocaleString('en-IN')} ${c.contract.option_type} · exp ${c.contract.expiry} · ${c.lot_size} units/lot`
      : 'No contract bought yet';
  }

  const money = document.getElementById('fsx-detail-money');
  if (money) {
    // Capital spent is premium paid, which for a bought option IS the money at
    // risk — there is no margin beyond it. Mark-to-market is shown apart from
    // realised and never folded into it.
    money.innerHTML = [
      _cascadeOptionsMetric('Capital spent', _fsxMoney(c.capital_spent)),
      _cascadeOptionsMetric('Still at risk', _fsxMoney(c.capital_open), (c.capital_open || 0) > 0 ? 'var(--warn)' : 'var(--text)'),
      _cascadeOptionsMetric('Realised ₹', _fsxMoney(c.realised), (c.realised || 0) >= 0 ? '#6ee7b7' : 'var(--danger)'),
      _cascadeOptionsMetric('Open now ₹', _fsxMoney(c.unrealised), (c.unrealised || 0) >= 0 ? '#6ee7b7' : 'var(--danger)'),
    ].join('');
  }

  const rounds = document.getElementById('fsx-detail-rounds');
  if (!rounds) return;
  if (!c.rounds.length) {
    rounds.innerHTML = '<div class="cascade-options-empty">Nothing bought yet — the geometry is drawn and it is waiting for price to reach a zone.</div>';
    return;
  }
  rounds.innerHTML = c.rounds.map(r => {
    const head = `Round ${r.round} · ${r.lots} lot${r.lots === 1 ? '' : 's'} / ${r.quantity} qty · avg premium ${r.average_premium ?? '—'} · spent ${_fsxMoney(r.capital_spent)}`;
    const tail = r.exit
      ? `<span style="color:${(r.realised || 0) >= 0 ? '#6ee7b7' : 'var(--danger)'};">${escapeHtml(String(r.exit.reason || 'exit').toUpperCase())} @ ${_cascadeNumber(r.exit.index_price)} · premium ${r.exit.premium ?? '—'} · ${_fsxMoney(r.realised)}</span>`
      : `<span style="color:var(--warn);">OPEN${r.mark_value !== undefined ? ` · worth ${_fsxMoney(r.mark_value)} now (${_fsxMoney(r.unrealised)})` : ''}</span>`;
    const legs = r.fills.map(f => `<tr style="text-align:right;border-top:1px solid var(--border);">
        <td style="text-align:left;padding:5px 8px;">${escapeHtml(new Date(f.at).toLocaleString('en-IN', { hour12: false, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }))}</td>
        <td style="padding:5px 8px;">${_cascadeNumber(f.index_price)}</td>
        <td style="padding:5px 8px;">${f.strike === null ? '—' : Number(f.strike).toLocaleString('en-IN')}</td>
        <td style="padding:5px 8px;">${f.lots}</td>
        <td style="padding:5px 8px;">${f.quantity}</td>
        <td style="padding:5px 8px;">${f.premium ?? '<span style="color:var(--warn);">unpriced</span>'}</td>
        <td style="padding:5px 8px;">${_fsxMoney(f.outlay)}</td>
        <td style="text-align:left;padding:5px 8px;color:var(--muted);">${escapeHtml(f.space || '')}</td>
      </tr>`).join('');
    return `<div style="margin-bottom:14px;">
      <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font:10.5px 'JetBrains Mono',monospace;color:var(--muted);margin-bottom:5px;"><span>${escapeHtml(head)}</span>${tail}</div>
      <div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font:10.5px 'JetBrains Mono',monospace;">
        <thead><tr style="text-align:right;color:var(--muted);"><th style="text-align:left;padding:5px 8px;">Bought</th><th style="padding:5px 8px;">Index</th><th style="padding:5px 8px;">Strike</th><th style="padding:5px 8px;">Lots</th><th style="padding:5px 8px;">Qty</th><th style="padding:5px 8px;">Premium</th><th style="padding:5px 8px;">Capital ₹</th><th style="text-align:left;padding:5px 8px;">Zone</th></tr></thead>
        <tbody>${legs}</tbody></table></div></div>`;
  }).join('');
}

async function loadFibSpaceChart() {
  if (!_fsxOpenCampaignId) return;
  const chart = document.getElementById('fsx-chart');
  const meta = document.getElementById('fsx-chart-meta');
  const overlay = document.getElementById('fsx-chart-overlay');
  if (overlay) { overlay.classList.add('is-open'); overlay.setAttribute('aria-hidden', 'false'); }
  if (chart) chart.innerHTML = '<div class="pf-cascade-chart-empty">Loading the campaign\'s own replay…</div>';
  try {
    const response = await fetch(`/api/fib-space/paper/chart?campaign_id=${encodeURIComponent(_fsxOpenCampaignId)}`, { credentials: 'same-origin', cache: 'no-store' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== 'ok') throw new Error(_apiErrorMessage(data, `Chart failed (${response.status})`));
    // Only one canvas may be mounted — the renderer finds its surfaces by fixed
    // id, so any other chart has to be folded away before this one mounts.
    _fibBoundaryCollapseBacktestChart();
    // The renderer sizes itself from the host's measured width, so it must not
    // draw in the same frame the overlay opened — that yields a 2px canvas.
    // The fetch above usually buys enough time; this makes it certain.
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    if (chart && typeof pfBenchDrawChart === 'function') pfBenchDrawChart(chart, data);
    if (meta) meta.textContent = `${data.candles.length} closed ${String(data.timeframe).toUpperCase()} candles · ${data.legs.length} fibs · ${data.trendlines.length} trendlines · ${data.entries.length} buys · drag to pan, wheel to zoom, double-click to reset`;
  } catch (error) {
    if (typeof _pfChartCanvasTeardown === 'function') _pfChartCanvasTeardown();
    if (chart) chart.innerHTML = `<div class="pf-cascade-chart-empty">${escapeHtml(error.message || 'Unable to load chart.')}</div>`;
    if (meta) meta.textContent = 'Chart unavailable';
  }
}

function hideFibSpaceChart() {
  const overlay = document.getElementById('fsx-chart-overlay');
  if (overlay) { overlay.classList.remove('is-open'); overlay.setAttribute('aria-hidden', 'true'); }
  // Without the teardown the renderer's observers leak on every open.
  if (typeof _pfChartCanvasTeardown === 'function') _pfChartCanvasTeardown();
}

function _fsxSetMotherStatus(message, tone = 'muted') {
  const el = document.getElementById('fsx-mother-status');
  if (!el) return;
  el.textContent = message || '';
  _cascadeSetTone(el, tone);
  el.style.color = ({ muted: 'var(--muted)', error: 'var(--danger)', success: '#6ee7b7', busy: '#fde68a' }[tone] || 'var(--muted)');
}

async function addFibSpaceMother() {
  const input = document.getElementById('fsx-mother-timestamp');
  const timestamp = input?.value;
  if (!timestamp) { _fsxSetMotherStatus('Pick a completed 15m candle open.', 'error'); return; }
  _fsxSetMotherStatus('Reading that candle from Dhan…', 'busy');
  let response;
  try {
    response = await fetch('/api/fib-space/paper/mother', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mother_timestamp: timestamp }) });
  } catch (error) {
    _fsxSetMotherStatus(error?.message || 'Unable to reach the paper run service.', 'error');
    return;
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.status !== 'accepted') {
    _fsxSetMotherStatus(_apiErrorMessage(data, 'That mother was not accepted.'), 'error');
    return;
  }
  // Echo the market's own high/low back, so it is obvious nothing was typed.
  _fsxSetMotherStatus(`Running — high ${_cascadeNumber(data.mother_high)}, low ${_cascadeNumber(data.mother_low)} from Dhan.`, 'success');
  await refreshFibSpaceStatus();
  // Open it straight away. Naming a mother is asking "does the engine see what
  // I see", and the answer is the chart -- it should not be behind knowing to
  // click a table row.
  _fsxOpenCampaignId = data.campaign_id;
  await _fsxLoadCampaignDetail();
  await loadFibSpaceChart();
}

async function startFibSpacePaper() {
  const symbol = document.getElementById('fsx-symbol')?.value || 'banknifty';
  _fsxSetFormStatus(`Starting the ${symbol.toUpperCase()} paper run…`, 'busy');
  let response;
  try {
    response = await fetch('/api/fib-space/paper/start', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol }) });
  } catch (error) {
    _fsxSetFormStatus(error?.message || 'Unable to reach the paper run service.', 'error');
    return;
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.status !== 'started') {
    _fsxSetFormStatus(_apiErrorMessage(data, 'The paper run did not start.'), 'error');
    return;
  }
  _fsxSetFormStatus(`Running on ${String(data.symbol).toUpperCase()} at ${data.lot_size} units/lot. It polls only inside the NSE session.`, 'success');
  await refreshFibSpaceStatus();
}

async function stopFibSpacePaper() {
  const confirmed = await customConfirm('Stop the fib-space paper run? Open paper campaigns stop being tracked, and restarting begins a fresh record.', { title: 'Stop paper run', icon: ICO.warn(28), okText: 'Stop', danger: true });
  if (!confirmed) return;
  const response = await fetch('/api/fib-space/paper/stop', { method: 'POST', credentials: 'same-origin' });
  const data = await response.json().catch(() => ({}));
  _fsxSetFormStatus(data.status === 'stopped' ? 'Paper run stopped.' : 'Nothing was running.', 'muted');
  await refreshFibSpaceStatus();
}

const _FIB_TIMEFRAME_MINUTES = { '1m': 1, '5m': 5, '15m': 15, '1h': 60 };
// Matches the server's _FIB_BOUNDARY_HISTORY_DAYS.
const _FIB_HISTORY_DAYS = 15;

// Mirrors engine/cascade_fib_geometry.boundaries_for_timeframe. The panel used
// to advertise L4+L8 on 1m and L2+L4+L8 on 15m/1H; the engine has traded
// neither of those since the levels were locked, so the form was describing a
// strategy that does not exist.
function _fibLevelsForTimeframe(timeframe) {
  return String(timeframe) === '1m' ? [8] : [4, 8];
}

function _syncFibLevelsHint() {
  // The ladder no longer varies: 1m entries on the halving levels, every
  // instrument, every side. What DOES vary by instrument is which expiry rule
  // the user actually gets and whether a backtest can price it at all -- so
  // that is what this line says now.
  const symbol = document.getElementById('fibx-symbol')?.value || 'NIFTY';
  const note = document.getElementById('fibx-symbol-note');
  if (note) {
    const terms = _FIBX_SYMBOL_NOTES[symbol];
    note.textContent = terms || '';
    note.style.display = terms ? '' : 'none';
  }

  // Do not advertise a real-money path while the server's fill/reconciliation
  // safety gate is closed.
  const mode = document.getElementById('fibx-mode')?.value || 'paper';
  const modeNote = document.getElementById('fibx-mode-note');
  if (modeNote) {
    modeNote.textContent = mode === 'live'
      ? 'Unavailable — broker fill verification and restart reconciliation are still pending.'
      : 'Records fills, sends nothing. Same rules as live.';
    modeNote.style.color = mode === 'live' ? 'var(--warn)' : 'var(--muted)';
  }

  const mother = document.getElementById('fibx-mother-timestamp');
  if (!mother) return;
  // The picker must offer only the minutes this chart can open on: 5m gets
  // multiples of 5, 15m multiples of 15, 1H only :15, and 1m every minute.
  const tf = _fibTimeframe();
  mother.dataset.pfCalendarMinutes = _MOTHER_MINUTES_BY_TF[tf] || '';
  _fibSnapMotherToTimeframe(mother, tf);
}

// Read off Dhan's own scrip master (2026-08-05) and the premium sources that
// actually exist. NSE withdrew the BANKNIFTY/FINNIFTY/MIDCPNIFTY weeklies, so
// "current week" cannot mean anything on those three.
function pickFibTimeframe(event, el) {
  const btn = el || event?.currentTarget;
  const row = document.getElementById('fibx-timeframe');
  if (!btn || !row) return;
  row.dataset.value = btn.getAttribute('data-tf') || '1m';
  row.querySelectorAll('.fibx-tf').forEach(b => b.classList.toggle('is-active', b === btn));
  _syncFibLevelsHint();
}

// The picker is a button row, so `.value` no longer exists on it.
function _fibTimeframe() {
  return document.getElementById('fibx-timeframe')?.dataset.value || '1m';
}

const _FIBX_SYMBOL_NOTES = {
  NIFTY: '',
  BANKNIFTY: 'Monthly expiries only.',
  FINNIFTY: 'Monthly only · no premium history, so no backtest.',
  MIDCPNIFTY: 'Monthly only · no premium history, so no backtest.',
  SENSEX: 'Thin book — 1m touches fill late.',
};

// Changing the timeframe leaves whatever was already typed behind, and the
// picker deliberately keeps offering a stale minute rather than silently
// snapping it. That is right for the dropdown and wrong for the field: 14:17
// left over from 1m is not a 15m candle. Round DOWN to the bar that contains
// it, which is the one a trader means by that instant.
function _fibSnapMotherToTimeframe(input, timeframe) {
  const match = String(input.value || '').match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/);
  if (!match) return;
  const [, day, rawHour, rawMinute] = match;
  let hour = Number(rawHour);
  let minute = Number(rawMinute);
  if (timeframe === '1h') {
    // NSE 1H bars open at :15 only, so 10:05 belongs to the 09:15 bar.
    if (minute < 15) hour -= 1;
    minute = 15;
    if (hour < 9) { hour = 9; minute = 15; }
  } else {
    const step = _FIB_TIMEFRAME_MINUTES[timeframe] || 5;
    minute -= minute % step;
  }
  // Rounding down can land before the open — 09:14 on 5m becomes 09:10, which is
  // not a candle either. The session starts at 09:15 on every timeframe.
  if (hour * 60 + minute < 555) { hour = 9; minute = 15; }
  const snapped = `${day}T${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
  if (snapped === input.value) return;
  input.value = snapped;
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

// The browser may be in any zone; every rule below is an IST rule, so read the
// clock through Asia/Kolkata rather than trusting the local one.
function _istNowParts() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date()).reduce((acc, part) => { acc[part.type] = part.value; return acc; }, {});
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    minuteOfDay: (Number(parts.hour) % 24) * 60 + Number(parts.minute),
  };
}

// The same rules /api/fib-boundary/paper/start enforces, checked before the
// round trip. The server's message is correct but the user never saw it: a
// mother 17 days in the future came back as a bare "Campaign did not start
// (400)". Returns a sentence to show, or null when the mother is startable.
function _fibMotherProblem(raw, timeframe) {
  const match = String(raw || '').match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/);
  if (!match) return 'Mother timestamp must be an IST datetime (YYYY-MM-DDTHH:MM).';
  const [, day, hh, mm] = match;
  const minuteOfDay = Number(hh) * 60 + Number(mm);
  const now = _istNowParts();
  const ageDays = Math.round((Date.parse(`${now.date}T00:00:00Z`) - Date.parse(`${day}T00:00:00Z`)) / 86400000);
  if (!Number.isFinite(ageDays)) return 'Mother timestamp must be an IST datetime (YYYY-MM-DDTHH:MM).';
  if (ageDays < 0) return `Mother candle cannot be in the future — it is ${now.date} in IST.`;
  if (ageDays > _FIB_HISTORY_DAYS) return `Mother candle is older than the ${_FIB_HISTORY_DAYS}-day paper window. Use Backtest for older history.`;
  const weekday = new Date(`${day}T12:00:00Z`).getUTCDay();
  if (weekday === 0 || weekday === 6) return 'NSE does not trade at weekends — pick a session day.';
  // 09:15 = 555, 15:30 = 930.
  if (minuteOfDay < 555 || minuteOfDay > 930) return 'Mother candle must open inside the NSE 09:15–15:30 session (IST).';
  const step = _FIB_TIMEFRAME_MINUTES[String(timeframe)] || 5;
  if (step === 60) {
    if (Number(mm) !== 15) return 'A 1H mother opens at 09:15, 10:15 … 15:15 IST.';
  } else if (Number(mm) % step) {
    return `A ${timeframe} mother opens on a ${step}-minute boundary — its minute must be a multiple of ${step}.`;
  }
  // NSE's last 1H bar is the 15-minute 15:15 block, not a full hour.
  const effective = step === 60 && Number(hh) === 15 ? 15 : step;
  if (ageDays === 0 && minuteOfDay + effective > now.minuteOfDay) return `That ${timeframe} candle has not closed yet.`;
  return null;
}

// FastAPI answers with {detail}, but a request-validation failure makes detail a
// LIST and a proxy can answer with no JSON at all. Every one of those used to
// collapse into a bare status code, which tells the user nothing.
function _apiErrorMessage(data, fallback) {
  const detail = data?.detail ?? data?.error?.detail ?? data?.error?.message ?? data?.message;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const joined = detail.map(row => row?.msg || row?.detail || '').filter(Boolean).join('; ');
    if (joined) return joined;
  }
  return fallback;
}

// The mother's date decides how Start behaves — a today mother runs live paper
// with real current premiums and real P&L; a past mother runs signal-only (P&L
// withheld, because Dhan only quotes 'now'), and the Backtest button is the way
// to get real historical P&L for it. Say so before they hit a button.
function _syncFibModeHint() {
  const el = document.getElementById('fibx-mode-hint');
  if (!el) return;
  const raw = document.getElementById('fibx-mother-timestamp')?.value;
  el.classList.remove('is-live', 'is-replay');
  if (!raw) { el.textContent = ''; return; }
  const picked = new Date(raw);
  if (isNaN(picked)) { el.textContent = ''; return; }
  const today = new Date();
  const isToday = picked.getFullYear() === today.getFullYear() && picked.getMonth() === today.getMonth() && picked.getDate() === today.getDate();
  if (isToday) {
    el.classList.add('is-live');
    el.textContent = '▶ Live paper · real current premiums';
  } else {
    el.classList.add('is-replay');
    el.textContent = '↻ Past mother — today\'s session only; use Backtest for history.';
  }
}

async function initOptionsCascadePage() {
  ['fibx-symbol', 'fibx-mode'].forEach(id => {
    const sel = document.getElementById(id);
    if (sel && !sel._fibHintBound) {
      sel.addEventListener('change', () => {
        _syncFibLevelsHint();
        _renderFibBoundaryRunningTable(Object.values(_lastFibBoundaryStatus || {}));
      });
      sel._fibHintBound = true;
    }
  });
  const tsSel = document.getElementById('fibx-mother-timestamp');
  if (tsSel && !tsSel._fibModeBound) { tsSel.addEventListener('change', _syncFibModeHint); tsSel.addEventListener('input', _syncFibModeHint); tsSel._fibModeBound = true; }
  _syncFibLevelsHint();
  _syncFibModeHint();
  _ceRenderTimeframes();
  await refreshFibBoundaryStatus();
  await refreshCandleEntryStatus();
  await refreshFibSpaceStatus();
  if (!_fibBoundaryPollTimer) {
    _fibBoundaryPollTimer = setInterval(() => {
      if (_isPageVisible() && _isPageActive('options-cascade-page')) { refreshFibBoundaryStatus(); refreshCandleEntryStatus(); refreshFibSpaceStatus(); }
    }, _ws && _ws.readyState === 1 ? 10000 : 3000);
  }
}

function _fibSetFormStatus(message, tone = 'muted') {
  const el = document.getElementById('fibx-form-status');
  if (!el) return;
  el.textContent = message || '';
  _cascadeSetTone(el, tone);
  el.style.color = ({ muted: 'var(--muted)', error: 'var(--danger)', success: '#6ee7b7', busy: '#fde68a' }[tone] || 'var(--muted)');
}

function _fibxLevelTone(status) {
  return ({ PENDING: 'var(--muted)', FILLED: '#6ee7b7', UNFUNDED: '#fbbf24', EXPIRING: '#fbbf24' }[status] || 'var(--muted)');
}

// ── One panel per ladder ──────────────────────────────────────────────
// A user can run one ladder per instrument, so the monitor is a TEMPLATE cloned
// per campaign and addressed with `data-fx=""` inside its own root. Panels are
// RECONCILED, never rebuilt: this repaints every 3s when the socket is down, and
// a rebuild would drop the <details> open state and the events scroll position
// on every tick.
function _fibxPanelRoots(symbols) {
  const monitors = document.getElementById('fibx-monitors');
  const lower = document.getElementById('fibx-lower');
  const monitorTpl = document.getElementById('fibx-monitor-tpl');
  const lowerTpl = document.getElementById('fibx-lower-tpl');
  if (!monitors || !lower || !monitorTpl || !lowerTpl) return new Map();
  const roots = new Map();
  const wanted = symbols.length ? symbols : [''];
  wanted.forEach(symbol => {
    const key = String(symbol);
    let monitor = monitors.querySelector(`[data-fx-symbol="${CSS.escape(key)}"]`);
    if (!monitor) {
      monitor = monitorTpl.content.firstElementChild.cloneNode(true);
      monitor.dataset.fxSymbol = key;
      monitors.appendChild(monitor);
    }
    let pair = lower.querySelector(`[data-fx-symbol="${CSS.escape(key)}"]`);
    if (!pair) {
      pair = lowerTpl.content.firstElementChild.cloneNode(true);
      pair.dataset.fxSymbol = key;
      lower.appendChild(pair);
    }
    roots.set(key, { monitor, pair });
  });
  // A killed-and-cleared instrument leaves; the survivors keep their DOM.
  [monitors, lower].forEach(host => {
    Array.from(host.children).forEach(node => {
      if (!roots.has(String(node.dataset.fxSymbol ?? ''))) node.remove();
    });
  });
  // Only touch the order when it is actually wrong -- re-appending a node the
  // user is scrolling costs them their place.
  [[monitors, 'monitor'], [lower, 'pair']].forEach(([host, which]) => {
    const desired = wanted.map(symbol => roots.get(String(symbol))[which]);
    if (desired.some((node, i) => host.children[i] !== node)) desired.forEach(node => host.appendChild(node));
  });
  return roots;
}

function _renderFibBoundaryStatus(payload) {
  _fibBoundaryLiveAvailable = payload?.live_available === true;
  const campaigns = Array.isArray(payload?.campaigns)
    ? payload.campaigns
    : (payload?.campaign ? [payload.campaign] : []);
  _lastFibBoundaryStatus = {};
  campaigns.forEach(row => { _lastFibBoundaryStatus[String(row.symbol || 'NIFTY')] = row; });

  const roots = _fibxPanelRoots(campaigns.map(row => String(row.symbol || 'NIFTY')));
  if (campaigns.length) campaigns.forEach(row => {
    const root = roots.get(String(row.symbol || 'NIFTY'));
    if (root) _renderFibBoundaryCampaign(root, row);
  });
  else { const root = roots.get(''); if (root) _renderFibBoundaryCampaign(root, null); }

  // The page's live gate is shared by every tab, so with N ladders it reports
  // the LOUDEST state on the board -- armed beats live, live beats paper.
  const liveGate = document.getElementById('options-cascade-live-gate');
  if (liveGate) {
    const running = campaigns.filter(row => row.running);
    const live = running.filter(row => row.is_live);
    const armed = live.filter(row => row.armed);
    const suffix = running.length > 1 ? ` · ${running.length} ladders` : '';
    let label = 'LIVE LOCKED', detail = 'Paper validation required', state = '';
    if (live.length && !_fibBoundaryLiveAvailable) { label = 'LIVE SAFETY LOCKED'; detail = `Automatic orders and exits disabled; reconcile in Dhan${suffix}`; state = 'is-replay'; }
    else if (armed.length) { label = 'LIVE ARMED'; detail = `Real orders reach Dhan${suffix}`; state = 'is-replay'; }
    else if (live.length) { label = 'LIVE · NOT ARMED'; detail = `Decisions run; orders are refused${suffix}`; state = 'is-replay'; }
    else if (running.length) { label = 'PAPER LIVE'; detail = `Quote-backed paper monitor active${suffix}`; state = 'is-paper-live'; }
    else if (campaigns.length) { label = 'PAPER STOPPED'; detail = 'No live order is ever sent'; state = 'is-paused'; }
    liveGate.classList.remove('is-paper-live', 'is-replay', 'is-paused');
    if (state) liveGate.classList.add(state);
    liveGate.innerHTML = `<strong>${escapeHtml(label)}</strong><span>${escapeHtml(detail)}</span>`;
  }
  _renderFibBoundaryRunningTable(campaigns);
}

// What is running right now, and whether it stands in the way of THIS form. A
// different instrument never does any more -- only the selected one can 409.
function _renderFibBoundaryRunningTable(campaigns) {
  const blocked = document.getElementById('fibx-blocked');
  const startBtn = document.getElementById('fibx-start');
  const picked = document.getElementById('fibx-symbol')?.value || 'NIFTY';
  const selectedMode = document.getElementById('fibx-mode')?.value || 'paper';
  const running = campaigns.filter(row => row.running);
  const clash = running.some(row => String(row.symbol) === String(picked));
  if (startBtn) {
    startBtn.disabled = clash || (selectedMode === 'live' && !_fibBoundaryLiveAvailable);
    startBtn.textContent = clash
      ? `▶ Kill the ${picked} ladder first`
      : selectedMode === 'live'
        ? (_fibBoundaryLiveAvailable ? '▶ Start LIVE monitor · unarmed' : '🔒 Live safety verification pending')
        : '▶ Start fib-boundary paper';
  }
  if (!blocked) return;
  blocked.innerHTML = running.length
    ? `<table class="fibx-blocked">`
      + `<tr><th>Running now</th><td>${running.length} ladder${running.length === 1 ? '' : 's'} · one per instrument</td></tr>`
      + running.map(row => {
        const isClash = String(row.symbol) === String(picked);
        const state = String(row.status || '').replaceAll('_', ' ').toUpperCase();
        return `<tr><th>${escapeHtml(String(row.symbol))}</th><td>`
          + `<strong style="color:${isClash ? 'var(--warn)' : 'var(--text)'};">${escapeHtml(String(row.side))} · ${escapeHtml(String(row.timeframe || '1m').toUpperCase())} mother</strong>`
          + ` · ${escapeHtml(state)}`
          + (row.is_live ? (row.armed ? ' · <strong>ARMED</strong>' : ' · not armed') : '')
          + (isClash ? '<br><span style="color:var(--warn);">Kill this one to start a new mother on it.</span>' : '')
          + `</td></tr>`;
      }).join('')
      + (clash ? '' : `<tr><th>${escapeHtml(picked)}</th><td>Free — Start runs it alongside the others.</td></tr>`)
      + `</table>`
    : '';
}

function _renderFibBoundaryCampaign(root, campaign) {
  const { monitor, pair } = root;
  const fx = key => monitor.querySelector(`[data-fx="${key}"]`);
  const px = key => pair.querySelector(`[data-fx="${key}"]`);
  const badge = fx('badge');
  const contract = fx('contract');
  const summary = fx('summary');
  const empty = fx('empty');
  const active = fx('active');
  const gist = fx('gist');
  const title = fx('title');
  const startBtn = document.getElementById('fibx-start');
  const killBtn = fx('kill');
  const armBtn = fx('arm');
  const eventsTf = px('events-tf');
  if (!campaign) {
    if (badge) {
      badge.textContent = 'IDLE';
      badge.classList.remove('is-positive', 'is-warning');
      badge.style.color = 'var(--muted)';
      badge.style.borderColor = 'var(--border)';
    }
    if (title) title.textContent = 'Campaign monitor';
    if (contract) contract.textContent = 'No active campaign';
    if (summary) summary.innerHTML = '';
    if (empty) empty.style.display = '';
    if (active) active.style.display = 'none';
    if (gist) gist.textContent = 'Pick an instrument, a side and a mother candle — the swing is found for you.';
    monitor.classList.remove('is-active');
    if (startBtn) startBtn.disabled = false;
    if (killBtn) killBtn.style.display = 'none';
    if (armBtn) armBtn.style.display = 'none';
    _renderFibBoundaryRounds(pair, null);
    _renderFibBoundaryEvents(pair, []);
    return;
  }
  const isRunning = !!campaign.running;
  const state = String(campaign.status || 'waiting').replaceAll('_', ' ').toUpperCase();
  const tone = isRunning ? '#6ee7b7' : 'var(--warn)';
  if (badge) {
    badge.textContent = state;
    badge.classList.toggle('is-positive', isRunning);
    badge.classList.toggle('is-warning', !isRunning);
    badge.style.color = tone;
    badge.style.borderColor = tone;
  }
  const fills = Array.isArray(campaign.fills) ? campaign.fills : [];
  const levels = Array.isArray(campaign.levels) ? campaign.levels : [];
  const anchor = campaign.anchor || null;
  const symbol = String(campaign.symbol || 'NIFTY');
  const side = String(campaign.side || 'CE');
  // The basket holds a different strike per rung, so the header names the
  // spread rather than pretending there is one contract.
  const strikes = [...new Set(fills.map(f => Number(f.strike)))].sort((a, b) => a - b);
  if (contract) {
    contract.textContent = strikes.length
      ? `${symbol} ${strikes.map(v => v.toLocaleString('en-IN')).join(' / ')} ${side} · exp ${fills[0].expiry} · ${campaign.lot_size || '—'} units/lot`
      : `${symbol} ${side} · ATM−${campaign.itm_steps ?? 2} · ≥${campaign.min_dte ?? 4} DTE · ${campaign.lot_size || '—'} units/lot`;
  }
  const tf = String(campaign.timeframe || '1m').toUpperCase();
  const mode = String(campaign.mode || 'paper').toUpperCase();
  const isLiveCampaign = !!campaign.is_live || mode === 'LIVE';
  // Every panel names its own instrument, because four of them look alike.
  if (title) title.textContent = `${symbol} ${side} monitor`;
  const roundsTitle = px('rounds-title');
  if (roundsTitle) roundsTitle.textContent = `${symbol} closed ${mode.toLowerCase()} round · net P&L`;
  const eventsTitle = px('events-title');
  if (eventsTitle) eventsTitle.textContent = `${symbol} campaign events`;
  if (eventsTf) eventsTf.textContent = `${tf} MOTHER · 1M ENTRIES`;
  const funded = levels.filter(l => l.status === 'FILLED').length;
  if (gist) {
    gist.textContent = anchor
      ? `${mode} · ${side} · ${tf} mother, 1m entries · ${funded}/${levels.length} levels bought · swing ${anchor.low}–${anchor.high} (${anchor.span} pts)`
      : `${mode} · ${side} · ${tf} mother · waiting for the first involvement to freeze the swing`;
  }
  monitor.classList.toggle('is-active', isRunning);
  const deployed = Number(campaign.deployed_inr || 0);
  const cap = Number(campaign.capital_cap_inr || 0);
  const pct = cap > 0 ? Math.min(100, Math.round((deployed / cap) * 100)) : 0;
  if (summary) {
    summary.innerHTML = [
      _cascadeOptionsMetric('Index target', _cascadeNumber(campaign.target_index)),
      _cascadeOptionsMetric('Avg entry', _cascadeNumber(campaign.average_index_entry)),
      _cascadeOptionsMetric('Avg premium', campaign.average_premium == null ? '—' : `₹${_cascadeNumber(campaign.average_premium)}`),
      _cascadeOptionsMetric('Lots held', `${campaign.open_lots || 0} · ${campaign.open_quantity || 0} qty`, '#6ee7b7'),
      // Compact on purpose: the long form ran past the tile and ellipsised the
      // percentage, which is the part that says how much ladder is left.
      _cascadeOptionsMetric('Funded', `${_cascadeOptionsMoney(deployed)} · ${pct}% of cap`, '#fde68a'),
      _cascadeOptionsMetric('Left to spend', _cascadeOptionsMoney(campaign.remaining_inr || 0), '#fde68a'),
    ].join('');
  }
  if (empty) empty.style.display = 'none';
  if (active) active.style.display = '';
  if (killBtn) {
    killBtn.style.display = isRunning ? '' : 'none';
    killBtn.textContent = isLiveCampaign && !_fibBoundaryLiveAvailable ? '⚠ Manage in Dhan' : '■ Kill';
  }
  // Arming is per instrument: this button opens THIS ladder and no other.
  if (armBtn) armBtn.style.display = (isRunning && isLiveCampaign && !campaign.armed && _fibBoundaryLiveAvailable) ? '' : 'none';

  const anchorEl = fx('anchor');
  if (anchorEl) {
    anchorEl.innerHTML = anchor
      ? `<table class="fibx-anchor-table">`
        + `<tr><th>Fib high</th><td><strong style="color:#fca5a5;">${escapeHtml(_cascadeNumber(anchor.high))}</strong></td><td class="fibx-when">${escapeHtml(_cascadeOptionsTimestamp(anchor.high_timestamp))}</td></tr>`
        + `<tr><th>Fib low</th><td><strong style="color:#6ee7b7;">${escapeHtml(_cascadeNumber(anchor.low))}</strong></td><td class="fibx-when">${escapeHtml(_cascadeOptionsTimestamp(anchor.low_timestamp))}</td></tr>`
        + `<tr><th>Span</th><td>${escapeHtml(_cascadeNumber(anchor.span))} pts</td><td class="fibx-when">every level is one span apart</td></tr>`
        + `<tr><th>Confirmed</th><td colspan="2" class="fibx-when">${escapeHtml(_cascadeOptionsTimestamp(anchor.confirmed_at))}</td></tr>`
        + `</table>`
      : '<div style="color:var(--muted);">No fib yet — waiting for the swing to settle.</div>';
  }

  const fillsEl = fx('fills');
  if (fillsEl) {
    fillsEl.innerHTML = fills.length ? fills.map(fill => `<tr style="border-bottom:1px solid var(--border);">`
      + `<td style="padding:8px;"><strong style="color:#38bdf8;">#${escapeHtml(String(fill.buy_number))}</strong></td>`
      + `<td style="padding:8px;">L${escapeHtml(String(fill.level))}</td>`
      + `<td style="padding:8px;">${escapeHtml(_cascadeOptionsTimestamp(fill.timestamp))}</td>`
      + `<td style="padding:8px;text-align:right;">${escapeHtml(_cascadeNumber(fill.index_price))}</td>`
      + `<td style="padding:8px;text-align:right;">${escapeHtml(Number(fill.strike).toLocaleString('en-IN'))} ${escapeHtml(String(fill.option_type))}</td>`
      + `<td style="padding:8px;">${escapeHtml(String(fill.expiry))}</td>`
      + `<td style="padding:8px;text-align:right;">₹${escapeHtml(_cascadeNumber(fill.premium))}</td>`
      + `<td style="padding:8px;text-align:right;">${escapeHtml(String(fill.lots))}</td>`
      + `<td style="padding:8px;text-align:right;">${escapeHtml(String(fill.quantity))}</td>`
      + `<td style="padding:8px;text-align:right;font-weight:800;color:#fde68a;">${escapeHtml(_cascadeOptionsMoney(fill.funded_inr))}</td>`
      + `</tr>`).join('') : '<tr><td colspan="10" style="padding:16px;text-align:center;color:var(--muted);">No buy yet.</td></tr>';
  }
  const fillSummary = fx('fill-summary');
  if (fillSummary) fillSummary.textContent = fills.length ? `${fills.length} buy${fills.length === 1 ? '' : 's'} · ${campaign.open_lots || 0} lots · ${_cascadeOptionsMoney(deployed)}` : '';

  const boundEl = fx('boundaries');
  if (boundEl) {
    boundEl.innerHTML = levels.length ? levels.map(level => {
      const stateColor = _fibxLevelTone(level.status);
      const toneClass = _cascadeOptionsToneClass(stateColor);
      const note = level.status === 'UNFUNDED' ? 'cap spent'
        : level.status === 'EXPIRING' ? 'contract too near expiry'
        : (level.filled_at ? _cascadeOptionsTimestamp(level.filled_at) : '');
      return `<div class="fibx-boundary ${toneClass}" style="padding:8px;border:1px solid var(--border);border-left:3px solid ${stateColor};border-radius:6px;">`
        + `<div style="display:flex;justify-content:space-between;gap:5px;font:10px 'JetBrains Mono',monospace;"><strong>${escapeHtml(String(level.key))}</strong><span class="fibx-boundary-state" style="color:${stateColor};">${escapeHtml(level.status)}</span></div>`
        + `<div style="margin-top:4px;font:800 11px 'JetBrains Mono',monospace;">${escapeHtml(_cascadeNumber(level.index_price))}</div>`
        + (note ? `<div style="margin-top:2px;font:9px 'JetBrains Mono',monospace;color:var(--muted);">${escapeHtml(note)}</div>` : '')
        + `</div>`;
    }).join('') : '<div style="grid-column:1/-1;color:var(--muted);font-size:11px;">No ladder yet — the swing has not been frozen.</div>';
  }

  const gapsEl = fx('gaps');
  if (gapsEl) {
    const gaps = Array.isArray(campaign.data_gaps) ? campaign.data_gaps : [];
    gapsEl.innerHTML = gaps.length
      ? `<div style="padding:8px 10px;border:1px solid rgba(251,191,36,.3);border-radius:7px;color:var(--warn);font:10.5px/1.5 'JetBrains Mono',monospace;"><strong>${gaps.length} pricing gap${gaps.length === 1 ? '' : 's'}</strong><br>${gaps.slice(-4).map(escapeHtml).join('<br>')}</div>`
      : '';
  }
  _renderFibBoundaryRounds(pair, campaign);
  _renderFibBoundaryEvents(pair, campaign.events || []);
}

function _renderFibBoundaryRounds(pair, campaign) {
  const body = pair.querySelector('[data-fx="rounds"]');
  const count = pair.querySelector('[data-fx="round-count"]');
  // The ladder is one-and-done: one target closes the whole basket and ends
  // the campaign, so there is at most one round to show.
  const closed = campaign && ['CLOSED', 'EXPIRED', 'KILLED'].includes(String(campaign.status || ''));
  if (count) count.textContent = `${closed ? 1 : 0} round${closed ? '' : 's'}`;
  if (!body) return;
  if (!closed) {
    const mode = campaign && (campaign.is_live || String(campaign.mode || '').toLowerCase() === 'live') ? 'live' : 'paper';
    body.innerHTML = `<tr><td colspan="8" style="padding:18px;text-align:center;color:var(--muted);">No completed ${mode} round</td></tr>`;
    return;
  }
  const pnl = Number(campaign.net_pnl || 0);
  const color = pnl > 0 ? '#6ee7b7' : pnl < 0 ? '#fca5a5' : 'var(--muted)';
  const cell = 'padding:9px 8px;text-align:right;font-family:\'JetBrains Mono\',monospace;';
  body.innerHTML = `<tr style="border-bottom:1px solid var(--border);">`
    + `<td style="padding:9px 8px;font-family:'JetBrains Mono',monospace;">#1</td>`
    + `<td style="${cell}">${escapeHtml(String(campaign.open_quantity || 0))}</td>`
    + `<td style="${cell}">${escapeHtml(_cascadeNumber(campaign.average_index_entry))}</td>`
    + `<td style="${cell}">${escapeHtml(_cascadeNumber(campaign.exit_index))}</td>`
    + `<td style="${cell}">${escapeHtml(_cascadeOptionsMoney(campaign.gross_pnl))}</td>`
    + `<td style="${cell}">${escapeHtml(_cascadeOptionsMoney(campaign.costs_total))}</td>`
    + `<td class="${_cascadeOptionsToneClass(color)}" style="padding:9px 8px;text-align:right;font:800 11px 'JetBrains Mono',monospace;color:${color};">${escapeHtml(_cascadeOptionsMoney(campaign.net_pnl))}</td>`
    + `<td style="padding:9px 8px;color:var(--muted);">${escapeHtml(String(campaign.exit_reason || '').replaceAll('_', ' '))}</td>`
    + `</tr>`;
}
function _renderFibBoundaryEvents(pair, events) {
  const el = pair.querySelector('[data-fx="events"]');
  if (!el) return;
  const scrollTop = el.scrollTop;
  el.innerHTML = events.length ? events.slice(-24).reverse().map(event => `<div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,.04);"><span style="color:#64748b;">${escapeHtml(_cascadeOptionsTimestamp(event.timestamp))}</span> <strong style="color:var(--text);">${escapeHtml(String(event.event || '').replaceAll('_', ' '))}</strong>${event.level != null ? ` <span style="color:#38bdf8;">L${escapeHtml(String(event.level))}</span>` : ''}</div>`).join('') : 'No events yet.';
  el.scrollTop = scrollTop;
}

async function refreshFibBoundaryStatus() {
  try {
    const response = await fetch('/api/fib-boundary/paper/status', { credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data?.detail || 'Unable to load fib-boundary campaigns');
    _renderFibBoundaryStatus(data);
  } catch (error) {
    const summary = document.querySelector('#fibx-monitors [data-fx="summary"]');
    if (summary && !summary.innerHTML) summary.textContent = error.message || 'Unable to load fib-boundary campaigns.';
  }
}

async function startFibBoundaryPaper() {
  const el = id => document.getElementById(id);
  // Nothing about the geometry is typed: the mother names where to look and the
  // server finds the swing around it.
  const payload = {
    symbol: el('fibx-symbol')?.value || 'NIFTY',
    mother_timestamp: el('fibx-mother-timestamp')?.value,
    side: el('fibx-side')?.value || 'CE',
    timeframe: _fibTimeframe(),
    mode: el('fibx-mode')?.value || 'paper',
    capital_cap_inr: Number(el('fibx-capital-cap')?.value),
    itm_steps: Number(el('fibx-itm')?.value),
  };
  if (!payload.mother_timestamp) { _fibSetFormStatus('Choose a completed mother timestamp.', 'error'); return; }
  if (!Number.isFinite(payload.capital_cap_inr) || payload.capital_cap_inr <= 0) { _fibSetFormStatus('Enter a valid ₹ ladder cap.', 'error'); return; }
  const button = el('fibx-start');
  if (button) {
    button.disabled = true;
    button.textContent = payload.mode === 'live'
      ? 'Finding the swing and starting an UNARMED live monitor…'
      : 'Finding the swing and starting the paper monitor…';
  }
  _fibSetFormStatus(`Reading ${payload.symbol} ${payload.timeframe}…`, 'busy');
  try {
    const response = await fetch('/api/fib-boundary/paper/start', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== 'started') throw new Error(_apiErrorMessage(data, `Ladder did not start (${response.status})`));
    const anchored = data.campaign?.anchor;
    const modeLabel = payload.mode === 'live' ? 'LIVE monitor started UNARMED' : 'paper monitor started';
    _fibSetFormStatus(anchored
      ? `${payload.symbol} ${modeLabel} · swing ${anchored.low}–${anchored.high} (${anchored.span} pts)`
      : `${payload.symbol} ${modeLabel} · waiting for the swing to freeze`, 'success');
    // No optimistic single-campaign paint here: rendering one campaign would
    // tear down every OTHER ladder's panel. The refresh below repaints the board.
  } catch (error) {
    _fibSetFormStatus(error.message || 'Ladder start failed.', 'error');
  } finally {
    if (button) button.disabled = false;
    refreshFibBoundaryStatus();
  }
}

// Which ladder a monitor button means: the panel it sits in owns the symbol.
function _fibxSymbolFor(el) {
  return String(el?.closest('[data-fx-symbol]')?.dataset.fxSymbol || '');
}

async function killFibBoundaryPaper(_event, button) {
  const symbol = _fibxSymbolFor(button);
  if (!symbol) { _fibSetFormStatus('No ladder to kill.', 'error'); return; }
  const campaign = _lastFibBoundaryStatus?.[symbol] || {};
  const isLive = !!campaign.is_live || String(campaign.mode || '').toLowerCase() === 'live';
  if (isLive && !_fibBoundaryLiveAvailable) {
    _fibSetFormStatus(
      `${symbol} live automation is safety-locked. PhilForge changed nothing — manage any real position in Dhan, then reconcile before stopping the campaign.`,
      'error',
    );
    return;
  }
  // Name the instrument. With four monitors on screen, "this campaign" is not
  // enough to know which basket is about to be closed.
  const message = isLive
    ? `Exit every recorded <strong>${escapeHtml(symbol)} LIVE</strong> option leg on Dhan, then kill this ladder? `
      + 'This sends real SELL orders. Other ladders keep running. You will be asked for your password and authenticator code.'
    : `Kill the <strong>${escapeHtml(symbol)}</strong> <strong>paper-only</strong> fib-boundary campaign and close its paper basket at the current quote? No Dhan order is sent. Other ladders keep running.`;
  const confirmed = await customConfirm(message, { title: `${isLive ? 'Exit & kill LIVE' : 'Kill'} ${symbol} fib-boundary`, icon: ICO.warn(28), okText: isLive ? 'Exit real legs & kill' : 'Kill & close', danger: true });
  if (!confirmed) return;
  if (button) { button.disabled = true; button.textContent = 'Closing…'; }
  try {
    const endpoint = isLive
      ? `/api/fib-boundary/live/${encodeURIComponent(symbol)}/kill`
      : `/api/fib-boundary/paper/kill?symbol=${encodeURIComponent(symbol)}`;
    const response = await fetch(endpoint, { method: 'POST', credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== 'killed') throw new Error(_apiErrorMessage(data, `Kill failed (${response.status})`));
    _fibSetFormStatus(`${symbol} fib-boundary campaign killed.`, 'success');
  } catch (error) {
    _fibSetFormStatus(error.message || 'Kill & close could not be confirmed.', 'error');
  } finally {
    if (button) { button.disabled = false; button.textContent = '■ Kill'; }
    await refreshFibBoundaryStatus();
  }
}

// The fib-boundary overlay draws with the SAME Canvas renderer the Test Bench
// and the backtest journal already use -- the CryptoForge chart, ported once in
// philforge-bench-chart.js. It used to carry its own SVG, and that is precisely
// how it drifted: a wall-clock x-axis that stretched a single stray bar across
// the whole afternoon, no session-gap blocks, no pan/zoom/crosshair, and a fixed
// 1180x520 viewBox stretched to whatever width the dialog happened to be. A
// second implementation of the same drawing is a second set of bugs.
//
// This function is now only a translator: it reshapes the chart route's payload
// (plus the live campaign on `_lastFibBoundaryStatus`) into the renderer's
// vocabulary. Every pixel decision lives in philforge-bench-chart.js.
function _fibBoundaryCanvasPayload(payload, symbol, campaignOverride) {
  // The campaign whose panel was clicked -- with several ladders running, the
  // buys drawn on a chart have to be that instrument's own. A BACKTEST has no
  // running campaign at all, so it passes its own result straight in; either
  // way one translator draws both charts.
  const campaign = campaignOverride || (symbol && _lastFibBoundaryStatus?.[symbol]) || {};
  // The renderer does arithmetic on `t`, so every timestamp crosses as epoch
  // SECONDS -- never the ISO string the route speaks.
  const epoch = value => { const parsed = Date.parse(value); return Number.isFinite(parsed) ? Math.round(parsed / 1000) : null; };
  const price = value => { const n = Number(value); return Number.isFinite(n) && n > 0 ? n : null; };

  // Native OHLC, not the route's gap-adjusted display prices. The Canvas draws
  // an overnight gap as its own translucent block, so pre-joining the bars would
  // both hide the gap and paint candles the paper engine never saw.
  const candles = (Array.isArray(payload?.candles) ? payload.candles : [])
    .map(row => ({
      t: epoch(row.t),
      o: Number(row.native_open ?? row.o),
      h: Number(row.native_high ?? row.h),
      l: Number(row.native_low ?? row.l),
      c: Number(row.native_close ?? row.c),
      is_mother: !!row.is_mother,
    }))
    .filter(row => row.t !== null && [row.o, row.h, row.l, row.c].every(Number.isFinite));

  // Level status comes from the live campaign when one is running; the route
  // itself only prices the geometry, so a chart opened before Start shows the
  // ladder with every rung still pending.
  const statusByLevel = {};
  (campaign.levels || []).forEach(row => { statusByLevel[Number(row.level)] = String(row.status || 'PENDING'); });

  const anchor = payload?.anchor || null;
  const lines = [];
  // The swing itself is the ladder's frame of reference, so it is drawn -- the
  // levels below are meaningless without it on screen.
  const motherBar = candles.find(row => row.is_mother);
  if (motherBar) {
    const side = String(payload?.side || campaign.side || 'CE').toUpperCase();
    lines.push({
      price: side === 'CE' ? motherBar.h : motherBar.l,
      label: side === 'CE' ? 'MOTHER HIGH' : 'MOTHER LOW',
      // `filled` is the renderer's solid-vs-dashed flag, not a state here:
      // every level on this chart is drawn as a line.
      filled: true,
      inr_notional: 0,
    });
  }
  if (anchor) {
    lines.push({ price: Number(anchor.high), label: 'SWING HIGH', filled: true, inr_notional: 0 });
    lines.push({ price: Number(anchor.low), label: 'SWING LOW', filled: true, inr_notional: 0 });
  }
  (Array.isArray(payload?.levels) ? payload.levels : []).forEach(row => {
    const level = Number(row.level);
    const status = statusByLevel[level] || 'PENDING';
    lines.push({
      price: Number(row.price),
      label: `L${level} ${status}`,
      filled: true,
      // What this rung actually cost, so a hovered line says how much is on it.
      inr_notional: (campaign.fills || [])
        .filter(f => Number(f.level) === level)
        .reduce((sum, f) => sum + (Number(f.funded_inr) || 0), 0),
    });
  });
  const drawable = lines.filter(line => Number.isFinite(line.price) && line.price > 0);

  // One white buy mark per fill, in the order they were bought.
  const entries = (campaign.fills || [])
    .map(fill => ({ t: epoch(fill.timestamp), price: price(fill.index_price) }))
    .filter(row => row.t !== null && row.price !== null);

  // The ladder is one-and-done, so there is at most one exit.
  const exits = [];
  if (campaign.exit_timestamp && campaign.exit_index != null) {
    const at = epoch(campaign.exit_timestamp), p = price(campaign.exit_index);
    if (at !== null && p !== null) exits.push({ t: at, price: p, pnl: Number(campaign.net_pnl) || 0 });
  }

  return {
    timeframe: payload?.timeframe || '1m',
    candles,
    // No mother BAND. Only the edge the ladder is measured against gets a line:
    // the mother's high on a CE, its low on a PE. The other edge is noise on
    // this chart and Phil asked for it gone. The mother candle itself is still
    // marked -- `is_mother` paints it -- so nothing is lost by dropping the band.
    mother: { high: null, low: null },
    // The trendline is a reference the eye needs; it gates nothing, so it is
    // drawn `active` purely to get the solid stroke rather than the ghost one.
    trendlines: payload?.trendline
      ? [{
          id: 'tl1',
          a1: { t: epoch(payload.trendline.start_timestamp), p: Number(payload.trendline.start_price) },
          a2: { t: epoch(payload.trendline.anchor_timestamp), p: Number(payload.trendline.anchor_price) },
          active: true,
        }].filter(tl => tl.a1.t !== null && tl.a2.t !== null)
      : [],
    legs: [],
    lines: drawable,
    entries,
    exits,
    avg_entry_price: price(campaign.average_index_entry),
    tp_price: price(campaign.target_index),
    // A ladder still holding must not have its target drawn as if it sold there.
    tp_label: exits.length ? 'TARGET HIT' : entries.length ? 'TARGET (open — watching)' : 'TARGET (no buy yet)',
  };
}

async function armFibBoundaryLive(_event, button) {
  const symbol = _fibxSymbolFor(button);
  if (!symbol) { _fibSetFormStatus('No ladder to arm.', 'error'); return; }
  // Real money. The confirm is this site's own dialog, and the route behind it
  // demands a password and an authenticator code on top. It names the ONE
  // instrument being opened -- arming is per ladder, never the whole board.
  const ok = await customConfirm(
    `From the next decision on, the <strong>${escapeHtml(symbol)}</strong> ladder places <strong>REAL F&amp;O orders</strong> on Dhan. `
    + 'Rungs it already refused stay refused, and no other ladder is armed by this. '
    + 'You will be asked for your password and authenticator code.',
    { title: `Arm ${symbol} live execution?`, okText: 'Arm live', cancelText: 'Stay unarmed' },
  );
  if (!ok) return;
  try {
    const response = await fetch(`/api/fib-boundary/live/${encodeURIComponent(symbol)}/arm`, { method: 'POST', credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(_apiErrorMessage(data, `Could not arm (${response.status})`));
    _fibSetFormStatus(`${symbol} LIVE ARMED — its orders from here on reach the exchange.`, 'success');
  } catch (error) {
    _fibSetFormStatus(error.message || 'Could not arm live execution.', 'error');
  }
  await refreshFibBoundaryStatus();
}

async function loadFibBoundaryChart(_event, button) {
  const el = id => document.getElementById(id);
  // The clicked panel's own campaign owns the question; the idle panel has no
  // symbol, so the form answers instead and the chart can be read before Start.
  const campaign = _lastFibBoundaryStatus?.[_fibxSymbolFor(button)];
  const timestamp = campaign?.mother_timestamp || el('fibx-mother-timestamp')?.value;
  const symbol = campaign?.symbol || el('fibx-symbol')?.value || 'NIFTY';
  const side = campaign?.side || el('fibx-side')?.value || 'CE';
  const timeframe = campaign?.timeframe || _fibTimeframe();
  const chart = el('fibx-chart');
  const meta = el('fibx-chart-meta');
  const overlay = el('fibx-chart-overlay');
  const title = el('fibx-chart-title');
  if (!timestamp) { _fibSetFormStatus('Pick a mother timestamp first.', 'error'); return; }
  if (overlay) { overlay.classList.add('is-open'); overlay.setAttribute('aria-hidden', 'false'); }
  if (title) title.textContent = `${symbol} ${side} swing ladder · ${String(timeframe).toUpperCase()} mother`;
  if (chart) chart.innerHTML = `<div class="pf-cascade-chart-empty">Loading closed ${escapeHtml(symbol)} ${escapeHtml(timeframe)} candles…</div>`;
  try {
    const query = new URLSearchParams({ mother_timestamp: timestamp, symbol, side, timeframe });
    const response = await fetch(`/api/fib-boundary/paper/chart?${query.toString()}`, { credentials: 'same-origin', cache: 'no-store' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== 'ok') throw new Error(_apiErrorMessage(data, `Chart failed (${response.status})`));
    // Only one canvas chart can be mounted at a time -- the renderer finds its
    // surfaces by a fixed id. Fold the backtest journal chart away first so the
    // overlay does not mount behind a duplicate host.
    _fibBoundaryCollapseBacktestChart();
    if (chart && typeof pfBenchDrawChart === 'function') pfBenchDrawChart(chart, _fibBoundaryCanvasPayload(data, campaign ? symbol : ''));
    if (meta) {
      meta.textContent = data.anchor
        ? `${data.candles.length} closed ${String(data.timeframe).toUpperCase()} candles · swing ${data.anchor.low}–${data.anchor.high} (${data.anchor.span} pts) · ${(data.levels || []).length} levels · drag to pan, wheel to zoom, double-click to reset`
        : `${data.candles.length} closed ${String(data.timeframe).toUpperCase()} candles · ${data.note}`;
    }
  } catch (error) {
    if (typeof _pfChartCanvasTeardown === 'function') _pfChartCanvasTeardown();
    if (chart) chart.innerHTML = `<div class="pf-cascade-chart-empty">${escapeHtml(error.message || 'Unable to load chart.')}</div>`;
    if (meta) meta.textContent = 'Chart unavailable';
  }
}

// Shared by the overlay and the journal toggle: both draw with the one Canvas
// renderer, so opening either has to put the other away.
function _fibBoundaryCollapseBacktestChart() {
  const box = document.getElementById('fibx-backtest-chart');
  const btn = document.getElementById('fibx-backtest-chart-btn');
  if (box) { box.style.display = 'none'; box.innerHTML = ''; }
  if (btn) btn.textContent = '↗ Journal chart';
}

function hideFibBoundaryChart() {
  const overlay = document.getElementById('fibx-chart-overlay');
  if (overlay) { overlay.classList.remove('is-open'); overlay.setAttribute('aria-hidden', 'true'); }
  // A hidden host still holds pointer bindings, a ResizeObserver and a theme
  // MutationObserver. Closing the dialog has to release them.
  if (typeof _pfChartCanvasTeardown === 'function') _pfChartCanvasTeardown();
  const chart = document.getElementById('fibx-chart');
  if (chart) chart.innerHTML = '';
}

async function runFibBoundaryBacktest() {
  const el = id => document.getElementById(id);
  // Field for field the same as Start. The two engines are one now, so the
  // two payloads have to be too -- a route test pins that.
  const payload = {
    symbol: el('fibx-symbol')?.value || 'NIFTY',
    mother_timestamp: el('fibx-mother-timestamp')?.value,
    side: el('fibx-side')?.value || 'CE',
    timeframe: _fibTimeframe(),
    capital_cap_inr: Number(el('fibx-capital-cap')?.value),
    itm_steps: Number(el('fibx-itm')?.value),
  };
  if (!payload.mother_timestamp) { _fibSetFormStatus('Pick a mother timestamp to backtest.', 'error'); return; }
  const button = el('fibx-backtest-btn');
  if (button) { button.disabled = true; button.textContent = 'Replaying…'; }
  _fibSetFormStatus(`Replaying ${payload.symbol} ${payload.side} on recorded prices…`, 'busy');
  try {
    const response = await fetch('/api/fib-boundary/backtest', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== 'ok') throw new Error(_apiErrorMessage(data, `Backtest failed (${response.status})`));
    _renderFibBoundaryBacktest(data);
    const net = data.campaign?.net_pnl;
    _fibSetFormStatus(
      net == null ? 'Replayed — no completed round to price.' : `Replayed · net ${_cascadeOptionsMoney(net)}`,
      net == null ? 'muted' : (net >= 0 ? 'success' : 'error'),
    );
  } catch (error) {
    _fibSetFormStatus(error.message || 'Backtest failed.', 'error');
  } finally {
    if (button) { button.disabled = false; button.textContent = '◱ Backtest · real P&L'; }
  }
}

function _renderFibBoundaryBacktest(data) {
  const panel = document.getElementById('fibx-backtest');
  if (panel) panel.style.display = '';
  const c = data.campaign || {};
  // The chart payload is the SAME shape the live chart route returns, so the
  // one translator draws both.
  _lastFibBacktest = { meta: data, chart: data.chart };
  const hasChart = !!(data.chart && Array.isArray(data.chart.candles) && data.chart.candles.length);
  _fibBoundaryCollapseBacktestChart();
  const chartBtn = document.getElementById('fibx-backtest-chart-btn');
  if (chartBtn) chartBtn.style.display = hasChart ? '' : 'none';

  const badge = document.getElementById('fibx-backtest-badge');
  if (badge) {
    const priced = data.pricing === 'recorded_history';
    const gaps = (c.data_gaps || []).length;
    badge.textContent = !priced ? 'GEOMETRY ONLY' : gaps ? `PRICED · ${gaps} GAP${gaps === 1 ? '' : 'S'}` : 'REAL PREMIUMS';
    badge.style.color = !priced ? 'var(--warn)' : gaps ? '#fde68a' : '#6ee7b7';
    badge.style.borderColor = badge.style.color;
  }
  const contract = document.getElementById('fibx-backtest-contract');
  if (contract) {
    contract.textContent = `${data.symbol} ${data.side} · ${String(data.timeframe).toUpperCase()} mother, 1m entries · ${data.lot_size} units/lot`;
  }
  const gist = document.getElementById('fibx-backtest-gist');
  if (gist) gist.textContent = data.note || '';

  const summary = document.getElementById('fibx-backtest-summary');
  if (summary) {
    const outcome = String(c.exit_reason || c.status || '—').replaceAll('_', ' ');
    summary.innerHTML = [
      _cascadeOptionsMetric('Outcome', outcome.toUpperCase(), c.net_pnl > 0 ? '#6ee7b7' : c.net_pnl < 0 ? '#fca5a5' : 'var(--muted)'),
      _cascadeOptionsMetric('Net P&L', c.net_pnl == null ? '—' : _cascadeOptionsMoney(c.net_pnl), c.net_pnl >= 0 ? '#6ee7b7' : '#fca5a5'),
      _cascadeOptionsMetric('Gross', c.gross_pnl == null ? '—' : _cascadeOptionsMoney(c.gross_pnl)),
      _cascadeOptionsMetric('Costs', c.costs_total == null ? '—' : _cascadeOptionsMoney(c.costs_total), '#fde68a'),
      _cascadeOptionsMetric('Funded', _cascadeOptionsMoney(c.deployed_inr || 0), '#fde68a'),
      _cascadeOptionsMetric('Lots', `${c.open_lots || 0} · ${(c.fills || []).length} buys`),
    ].join('');
  }

  const note = document.getElementById('fibx-backtest-note');
  if (note) {
    const a = c.anchor;
    note.textContent = a
      ? `Fib ${a.low}–${a.high} (${a.span} pts), confirmed ${_cascadeOptionsTimestamp(a.confirmed_at)} · ${data.candles_replayed} 1m bars to ${data.horizon_to}`
      : `No fib formed in ${data.candles_replayed} bars to ${data.horizon_to} — the swing never settled.`;
  }

  const gapsEl = document.getElementById('fibx-backtest-gaps');
  if (gapsEl) {
    const gaps = c.data_gaps || [];
    gapsEl.innerHTML = gaps.length
      ? `<div style="padding:8px 10px;border:1px solid rgba(251,191,36,.3);border-radius:7px;color:var(--warn);font:10.5px/1.5 'JetBrains Mono',monospace;"><strong>${gaps.length} pricing gap${gaps.length === 1 ? '' : 's'}</strong><br>${gaps.slice(-5).map(escapeHtml).join('<br>')}</div>`
      : '';
  }

  const legs = document.getElementById('fibx-backtest-legs');
  if (legs) {
    const fills = c.fills || [];
    const exits = c.exit_premiums || [];
    legs.innerHTML = fills.length ? fills.map((f, i) => {
      const out = exits[i];
      const pnl = out == null ? null : (Number(out) - Number(f.premium)) * Number(f.quantity);
      const color = pnl == null ? 'var(--muted)' : pnl >= 0 ? '#6ee7b7' : '#fca5a5';
      return `<tr style="border-bottom:1px solid var(--border);">`
        + `<td style="padding:7px 8px;"><strong style="color:#38bdf8;">#${escapeHtml(String(f.buy_number))}</strong> L${escapeHtml(String(f.level))}</td>`
        + `<td style="padding:7px 8px;">${escapeHtml(_cascadeOptionsTimestamp(f.timestamp))}</td>`
        + `<td style="padding:7px 8px;text-align:right;">${escapeHtml(_cascadeNumber(f.index_price))}</td>`
        + `<td style="padding:7px 8px;text-align:right;">${escapeHtml(Number(f.strike).toLocaleString('en-IN'))}</td>`
        + `<td style="padding:7px 8px;text-align:right;">₹${escapeHtml(_cascadeNumber(f.premium))}${out == null ? '' : ' → ₹' + escapeHtml(_cascadeNumber(out))}</td>`
        + `<td style="padding:7px 8px;text-align:right;">${escapeHtml(String(f.lots))}</td>`
        + `<td style="padding:7px 8px;text-align:right;">${escapeHtml(String(f.quantity))}</td>`
        + `<td style="padding:7px 8px;text-align:right;color:${color};font-weight:800;">${pnl == null ? '—' : escapeHtml(_cascadeOptionsMoney(pnl))}</td>`
        + `</tr>`;
    }).join('') : '<tr><td colspan="8" style="padding:16px;text-align:center;color:var(--muted);">No level was touched in this window.</td></tr>';
  }
}

function toggleFibBoundaryBacktestChart() {
  const box = document.getElementById('fibx-backtest-chart');
  const btn = document.getElementById('fibx-backtest-chart-btn');
  if (!box) return;
  if (box.style.display !== 'none' && box.innerHTML) {
    box.style.display = 'none';
    box.innerHTML = '';
    if (typeof _pfChartCanvasTeardown === 'function') _pfChartCanvasTeardown();
    if (btn) btn.textContent = '↗ Journal chart';
    return;
  }
  // The same Canvas renderer the Test Bench draws with — bar-index axis, gap
  // blocks, trade markers — instead of the old wall-clock SVG.  Visible
  // first, then drawn: a display:none host measures zero.
  const chart = _lastFibBacktest && _lastFibBacktest.chart;
  if (!chart) return;
  box.style.display = '';
  // Through the SAME translator the live chart uses, with the backtest's own
  // campaign so its fills and level states are the ones drawn.
  if (typeof pfBenchDrawChart === 'function') {
    pfBenchDrawChart(box, _fibBoundaryCanvasPayload(chart, null, _lastFibBacktest.meta?.campaign));
  }
  if (btn) btn.textContent = '× Hide chart';
}


window.initOptionsCascadePage = initOptionsCascadePage;
window.toggleFibBoundaryBacktestChart = toggleFibBoundaryBacktestChart;
window.showOptionsCascadeTab = showOptionsCascadeTab;
window.startFibSpacePaper = startFibSpacePaper;
window.stopFibSpacePaper = stopFibSpacePaper;
window.addFibSpaceMother = addFibSpaceMother;
window.openFibSpaceCampaign = openFibSpaceCampaign;
window.closeFibSpaceDetail = closeFibSpaceDetail;
window.loadFibSpaceChart = loadFibSpaceChart;
window.hideFibSpaceChart = hideFibSpaceChart;
window.refreshFibSpaceStatus = refreshFibSpaceStatus;
window.startFibBoundaryPaper = startFibBoundaryPaper;
window.killFibBoundaryPaper = killFibBoundaryPaper;
window.loadFibBoundaryChart = loadFibBoundaryChart;
window.hideFibBoundaryChart = hideFibBoundaryChart;
window.runFibBoundaryBacktest = runFibBoundaryBacktest;

// Lot size lookup for display (matches backend get_lot_size)
const INSTRUMENT_LOT_MAP = {
  '26000': 65,  // NIFTY 50 (65 from Jan 2026)
  '26009': 30,  // BANK NIFTY (30 from Jan 2026)
  '26017': 65,  // FINNIFTY (65 from Jan 2026)
  '1': 20,      // SENSEX (20 from Jan 2026)
  '26037': 75,  // NIFTY MIDCAP
};
function _getLotSizeForInstrument(instCode) {
  return INSTRUMENT_LOT_MAP[String(instCode)] || 65;
}

const EXECUTION_PROFILE_DEFAULTS = {
  '26000': { label: 'NIFTY 50', spread_bps: 18, entry_slippage_bps: 10, exit_slippage_bps: 14, capital_buffer_pct: 5, sell_option_margin_per_lot: 100000, enforce_capital: true },
  '26009': { label: 'BANK NIFTY', spread_bps: 28, entry_slippage_bps: 14, exit_slippage_bps: 20, capital_buffer_pct: 6, sell_option_margin_per_lot: 150000, enforce_capital: true },
  '26017': { label: 'NIFTY FIN SVC', spread_bps: 22, entry_slippage_bps: 12, exit_slippage_bps: 16, capital_buffer_pct: 5, sell_option_margin_per_lot: 85000, enforce_capital: true },
  '26037': { label: 'NIFTY MIDCAP 50', spread_bps: 40, entry_slippage_bps: 22, exit_slippage_bps: 30, capital_buffer_pct: 7, sell_option_margin_per_lot: 80000, enforce_capital: true },
  '1': { label: 'SENSEX', spread_bps: 34, entry_slippage_bps: 18, exit_slippage_bps: 24, capital_buffer_pct: 6, sell_option_margin_per_lot: 75000, enforce_capital: true },
  default: { label: 'Cash Equity', spread_bps: 12, entry_slippage_bps: 6, exit_slippage_bps: 8, capital_buffer_pct: 4, sell_option_margin_per_lot: 0, enforce_capital: true },
};

function getExecutionProfileDefaults(instCode) {
  return EXECUTION_PROFILE_DEFAULTS[String(instCode)] || EXECUTION_PROFILE_DEFAULTS.default;
}

function applyExecutionProfile(forceApply = false) {
  const profileSel = document.getElementById('execution-profile');
  const instSel = document.getElementById('instrument-select');
  const hint = document.getElementById('execution-profile-hint');
  if (!profileSel || !instSel || !hint) return;

  const profile = profileSel.value || 'auto';
  const defaults = getExecutionProfileDefaults(instSel.value);

  if (profile === 'auto') {
    document.getElementById('spread-bps').value = defaults.spread_bps;
    document.getElementById('entry-slippage-bps').value = defaults.entry_slippage_bps;
    document.getElementById('exit-slippage-bps').value = defaults.exit_slippage_bps;
    document.getElementById('capital-buffer-pct').value = defaults.capital_buffer_pct;
    document.getElementById('sell-option-margin-per-lot').value = defaults.sell_option_margin_per_lot || 0;
    document.getElementById('enforce-capital').checked = !!defaults.enforce_capital;
    if (forceApply) {
      document.getElementById('entry-delay-candles').value = 0;
      document.getElementById('signal-exit-delay-candles').value = 0;
    }
    hint.textContent = `Auto profile: ${defaults.label} | spread ${defaults.spread_bps}bps, entry slip ${defaults.entry_slippage_bps}bps, exit slip ${defaults.exit_slippage_bps}bps, buffer ${defaults.capital_buffer_pct}%`;
  } else {
    hint.textContent = 'Custom profile active. Manual execution realism values are preserved for this strategy.';
  }
}

function restoreExecutionSettings(source) {
  const data = source || {};
  const profile = data.execution_profile || 'auto';
  document.getElementById('execution-profile').value = profile;
  applyExecutionProfile(profile === 'auto');
  if (data.spread_bps !== undefined) document.getElementById('spread-bps').value = data.spread_bps;
  if (data.entry_slippage_bps !== undefined) document.getElementById('entry-slippage-bps').value = data.entry_slippage_bps;
  if (data.exit_slippage_bps !== undefined) document.getElementById('exit-slippage-bps').value = data.exit_slippage_bps;
  if (data.entry_delay_candles !== undefined) document.getElementById('entry-delay-candles').value = data.entry_delay_candles;
  if (data.signal_exit_delay_candles !== undefined) document.getElementById('signal-exit-delay-candles').value = data.signal_exit_delay_candles;
  if (data.capital_buffer_pct !== undefined) document.getElementById('capital-buffer-pct').value = data.capital_buffer_pct;
  if (data.sell_option_margin_per_lot !== undefined) document.getElementById('sell-option-margin-per-lot').value = data.sell_option_margin_per_lot;
  if (data.enforce_capital !== undefined) document.getElementById('enforce-capital').checked = !!data.enforce_capital;
  applyExecutionProfile(false);
}

// ── Backfill status polling ───────────────────────────────────────
(function pollBackfill() {
  const banner = document.getElementById('backfill-banner');
  const msg    = document.getElementById('backfill-msg');
  let   timer  = null;

  function check() {
    fetch('/api/backfill/status')
      .then(r => r.json())
      .then(data => {
        if (data.status === 'running') {
          banner.classList.add('visible');
          msg.textContent = data.message || 'Backfilling historical data...';
          timer = setTimeout(check, 2500);
        } else if (data.status === 'done') {
          msg.textContent = data.message || 'Trade history up to date.';
          setTimeout(() => banner.classList.remove('visible'), 2000);
        } else if (data.status === 'error') {
          msg.textContent = 'Backfill error: ' + (data.message || 'unknown');
          setTimeout(() => banner.classList.remove('visible'), 5000);
        }
        // status === 'idle' → backfill hasn't started yet (cold start race); check again
        else { timer = setTimeout(check, 1500); }
      })
      .catch(() => { /* server not ready yet — retry */ timer = setTimeout(check, 3000); });
  }
  check();
})();

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[role="tablist"]').forEach(tablist => {
    tablist.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      const tabs = Array.from(tablist.querySelectorAll('[role="tab"]')).filter(tab => !tab.disabled);
      const current = tabs.indexOf(document.activeElement);
      if (current < 0 || !tabs.length) return;
      event.preventDefault();
      let next = current;
      if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (current + 1) % tabs.length;
      else next = (current - 1 + tabs.length) % tabs.length;
      tabs[next].focus();
      tabs[next].click();
    });
  });
  try {
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
  } catch(e) { console.warn('scroll restoration setup failed:', e); }
  try { loadAuthContext(); } catch(e) { console.warn('loadAuthContext failed:', e); }
  loadTickerFromCache();
  try { updateTicker(); } catch(e) { console.warn('updateTicker failed:', e); }
  setInterval(() => {
    if (!_isPageVisible()) return;
    try { updateTicker(); } catch(e) { console.warn('ticker interval failed:', e); }
  }, 30000);
  try {
    const runNameInput = document.getElementById('run-name-input');
    if (runNameInput) runNameInput.value = 'Strategy_' + generateRandomID();
  } catch(e) { console.warn('run name init failed:', e); }
  try { applyExecutionProfile(true); } catch(e) { console.warn('applyExecutionProfile failed:', e); }
  try { renderIndicatorFields(); } catch(e) { console.warn('renderIndicatorFields failed:', e); }
  try { loadDashboardSummary(); } catch(e) { console.warn('loadDashboardSummary failed:', e); }
  // Don't add default conditions - user adds them via indicators or manually

  // Restore active tab from URL or previous session and seed browser history.
  try {
    const savedTab = _getLocalState('philforge_active_tab');
    const initialState =
      history.state ||
      navStateFromLocation() ||
      (savedTab && document.getElementById(savedTab) ? buildNavState(savedTab) : buildNavState('dashboard-page'));
    history.replaceState(initialState, '', navHashForState(initialState));
    applyNavState(initialState);
    _scrollViewportToTop();
    _restoreInitialMobileNavPosition(initialState.page || 'dashboard-page');
  } catch(e) {}
  document.documentElement.setAttribute('data-nav-ready', '1');
  // Start clock
  try { updateClock(); } catch(e) { console.warn('updateClock failed:', e); }
  setInterval(() => {
    if (!_isPageVisible()) return;
    updateClock();
  }, 1000);
  // Load expiry dates
  try { loadExpiryDates(); } catch(e) { console.warn('loadExpiryDates failed:', e); }
});

window.addEventListener('popstate', (event) => {
  const state = event.state || navStateFromLocation() || buildNavState('dashboard-page');
  applyNavState(state);
});



// ══════════════════════════════════════════════════════════════
//  STRATEGY TEMPLATES
// ══════════════════════════════════════════════════════════════
const STRATEGY_TEMPLATES = {
  supertrend_cpr: {
    run_name: 'Supertrend_CPR_Narrow',
    instrument: '26000',
    segment: 'IDX_I',
    market_open: '09:15', market_close: '15:25',
    stoploss_pct: 10, target_profit_pct: 20,
    max_trades_per_day: 2, max_daily_loss: 5000,
    indicators: ['Supertrend_10_2.7_5m', 'CPR_5m'],
    entry_conditions: [
      {left: 'supertrend_dir', operator: '==', right: 'number', right_number_value: 1, connector: 'AND'},
      {left: 'cpr_is_narrow', operator: 'is_true', right: 'true', connector: 'AND'}
    ],
    exit_conditions: [
      {left: 'supertrend_dir', operator: '==', right: 'number', right_number_value: -1, connector: 'AND'}
    ],
    legs: [{option_type: 'CE', transaction_type: 'BUY', strike_type: 'atm', strike_value: 0, lots: 1, sl_pct: 20, target_pct: 40, trail_pct: 15, sqoff_time: '15:20'}]
  },
  orb_breakout: {
    run_name: 'ORB_Breakout_15m',
    instrument: '26000',
    segment: 'IDX_I',
    market_open: '09:15', market_close: '15:25',
    stoploss_pct: 10, target_profit_pct: 20,
    max_trades_per_day: 1, max_daily_loss: 3000,
    indicators: ['ORB_15m'],
    entry_conditions: [
      {left: 'ORB_is_breakout_up', operator: 'is_true', right: 'true', connector: 'AND'},
      {left: 'Time_Of_Day', operator: '>=', right_time: '09:30', connector: 'AND'}
    ],
    exit_conditions: [
      {left: 'current_close', operator: 'crosses_below', right: 'ORB_High', connector: 'AND'}
    ],
    legs: [{option_type: 'CE', transaction_type: 'BUY', strike_type: 'atm', strike_value: 0, lots: 1, sl_pct: 20, target_pct: 30, trail_pct: 0, sqoff_time: '15:20'}]
  },
  ema_crossover: {
    run_name: 'EMA_9_21_Crossover',
    instrument: '26000',
    segment: 'IDX_I',
    market_open: '09:15', market_close: '15:25',
    stoploss_pct: 10, target_profit_pct: 20,
    max_trades_per_day: 2, max_daily_loss: 5000,
    indicators: ['EMA_9_5m', 'EMA_21_5m'],
    entry_conditions: [
      {left: 'EMA_9_5m', operator: 'crosses_above', right: 'EMA_21_5m', connector: 'AND'}
    ],
    exit_conditions: [
      {left: 'EMA_9_5m', operator: 'crosses_below', right: 'EMA_21_5m', connector: 'AND'}
    ],
    legs: [{option_type: 'CE', transaction_type: 'BUY', strike_type: 'atm', strike_value: 0, lots: 1, sl_pct: 15, target_pct: 30, trail_pct: 10, sqoff_time: '15:20'}]
  },
  rsi_reversal: {
    run_name: 'RSI_Reversal_BN',
    instrument: '26009',
    segment: 'IDX_I',
    market_open: '09:15', market_close: '15:25',
    stoploss_pct: 10, target_profit_pct: 20,
    max_trades_per_day: 1, max_daily_loss: 4000,
    indicators: ['RSI_14_15m', 'EMA_20_15m'],
    entry_conditions: [
      {left: 'RSI_14_15m', operator: 'crosses_above', right: 'number', right_number_value: 30, connector: 'AND'},
      {left: 'current_close', operator: 'is_above', right: 'EMA_20_15m', connector: 'AND'}
    ],
    exit_conditions: [
      {left: 'RSI_14_15m', operator: 'is_above', right: 'number', right_number_value: 70, connector: 'AND'}
    ],
    legs: [{option_type: 'CE', transaction_type: 'BUY', strike_type: 'atm', strike_value: 0, lots: 1, sl_pct: 25, target_pct: 50, trail_pct: 20, sqoff_time: '15:20'}]
  }
};

function loadTemplate(key) {
  const t = STRATEGY_TEMPLATES[key];
  if (!t) return;
  currentLoadedStrategyId = null;
  // Switch to builder tab
  showPage('builder-page', document.getElementById('nav-builder'));
  // Fill in fields
  document.getElementById('run-name-input').value = t.run_name;
  document.getElementById('segment-select').value = t.segment || 'IDX_I';
  document.getElementById('segment-select').dispatchEvent(new Event('change'));
  setTimeout(() => {
    document.getElementById('instrument-select').value = t.instrument;
    applyExecutionProfile(true);
    document.getElementById('entry-time-start').value = t.market_open;
    document.getElementById('sq-time').value = t.market_close;
    document.getElementById('txn-sl').value = t.stoploss_pct;
    document.getElementById('target-profit').value = t.target_profit_pct;
    document.getElementById('max-trades-per-day').value = t.max_trades_per_day;
    document.getElementById('max-daily-loss').value = t.max_daily_loss || 0;
    // Set indicators
    myIndicators = [...t.indicators];
    renderIndicatorFields();
    // Set conditions
    const entryBox = document.getElementById('entry-conditions-container');
    const exitBox = document.getElementById('exit-conditions-container');
    if (entryBox) entryBox.innerHTML = '';
    if (exitBox) exitBox.innerHTML = '';
    conditionCounters = { entry: 0, exit: 0 };
    populateConditionRows('entry', t.entry_conditions);
    populateConditionRows('exit', t.exit_conditions);
    // Set legs
    legs = []; legCounter = 0;
    const legsBox = document.getElementById('legs-container');
    legsBox.innerHTML = '';
    document.getElementById('legs-empty').style.display = 'block';
    document.getElementById('combined-pnl-bar').style.display = 'none';
    t.legs.forEach((leg, i) => {
      addLeg(leg.transaction_type || 'BUY', leg.option_type || 'CE');
      const id = legCounter - 1;
      const setVal = (elId, val) => { const el = document.getElementById(elId); if(el && val !== undefined) el.value = val; };
      setVal(`leg-${id}-expiry`, leg.expiry);
      setVal(`leg-${id}-strike-type`, leg.strike_type);
      setVal(`leg-${id}-strike-value`, leg.strike_value);
      setVal(`leg-${id}-lots`, leg.lots);
      setVal(`leg-${id}-sl-pct`, leg.sl_pct);
      setVal(`leg-${id}-target-pct`, leg.target_pct);
      setVal(`leg-${id}-trail-pct`, leg.trail_pct);
      setVal(`leg-${id}-sqoff-time`, leg.sqoff_time);
    });
    toast(`Template "${t.run_name}" loaded! Customize and run backtest.`, 'success');
  }, 200);
}

// ══════════════════════════════════════════════════════════════
//  FETCH & DISPLAY ALL RUNS (Backtest / Paper / Live)
// ══════════════════════════════════════════════════════════════
let _allRunsCache = [];
let _dashboardTransactionsCache = [];
let _dashboardTxnByKey = new Map();
let _currentRunFilter = 'all';
let _runsPageDash = 1;
let _runsPageResults = 1;
const _RUNS_PER_PAGE = 10;
const _DASHBOARD_TXN_PER_PAGE = 10;
let _selectedRunIds = new Set();
let _selectedDashboardTxnKeys = new Set();
let _selectedScalpHistIds = new Set();
let _selectedScalpRunIds = new Set();
let _scalpTradesCache = [];
let _scalpPage = 1;
let _portfolioPaperPage = 1;
let _portfolioRunExpandedKey = '';
let _portfolioRunSessionPages = Object.create(null);
let _portfolioEngineSnapshotCache = [];
let _portfolioRunTradeCache = Object.create(null);
let _portfolioRunTradeLoading = Object.create(null);
let _portfolioRunTradeErrors = Object.create(null);
let _portfolioRunTradePages = Object.create(null);
let _dashboardTxnPage = 1;
let _dashboardTxnSearchQuery = '';
let _dashboardTxnSortCol = 'exitTime';
let _dashboardTxnSortAsc = false;

function _getModeBadge(mode, compact = false) {
  const m = (mode || 'backtest').toLowerCase();
  const compactStyle = compact ? ' style="font-size:9px;padding:1px 5px;border-radius:3px;"' : '';
  if (m === 'paper') return `<span class="mode-badge paper"${compactStyle}>PAPER</span>`;
  if (m === 'live' || m === 'auto' || m === 'real') return `<span class="mode-badge live"${compactStyle}>LIVE</span>`;
  if (m === 'scalp') return `<span class="mode-badge scalp"${compactStyle}>SCALP</span>`;
  return `<span class="mode-badge backtest"${compactStyle}>BACKTEST</span>`;
}

function _normalizeMode(mode) {
  const m = (mode || 'backtest').toLowerCase();
  if (m === 'live' || m === 'auto' || m === 'real') return 'live';
  if (m === 'paper') return 'paper';
  if (m === 'scalp') return 'scalp';
  return 'backtest';
}

// ── Sortable table state ──
let _runsSortCol = null;   // column key
let _runsSortAsc = true;
let _runsSearchQuery = '';

function _sortArrow(col) {
  if (_runsSortCol !== col) return '<span style="opacity:0.3;font-size:10px;margin-left:3px;">⇅</span>';
  return _runsSortAsc ? '<span style="color:var(--accent);font-size:10px;margin-left:3px;">▲</span>' : '<span style="color:var(--accent);font-size:10px;margin-left:3px;">▼</span>';
}
function _toggleRunsSort(col) {
  if (_runsSortCol === col) _runsSortAsc = !_runsSortAsc;
  else { _runsSortCol = col; _runsSortAsc = true; }
  _renderFilteredRuns();
}
function _filterRunsBySearch() {
  _runsSearchQuery = (document.getElementById('runs-search-input')?.value || '').toLowerCase().trim();
  _runsPageResults = 1;
  _renderFilteredRuns();
}
function _applySortAndSearch(arr) {
  let out = arr;
  if (_runsSearchQuery) {
    out = out.filter(r => {
      const name = (r.run_name || '').toLowerCase();
      const inst = (getInstrumentName(r.instrument) || '').toLowerCase();
      const mode = (r.mode || '').toLowerCase();
      return name.includes(_runsSearchQuery) || inst.includes(_runsSearchQuery) || mode.includes(_runsSearchQuery);
    });
  }
  if (_runsSortCol) {
    const key = _runsSortCol;
    out = [...out].sort((a, b) => {
      let va, vb;
      if (key === 'run_name') { va = (a.run_name||'').toLowerCase(); vb = (b.run_name||'').toLowerCase(); }
      else if (key === 'instrument') { va = getInstrumentName(a.instrument)||''; vb = getInstrumentName(b.instrument)||''; }
      else if (key === 'trades') { va = a.trade_count||0; vb = b.trade_count||0; }
      else if (key === 'pnl') { va = a.total_pnl||0; vb = b.total_pnl||0; }
      else if (key === 'period') { va = a.from_date||''; vb = b.from_date||''; }
      else if (key === 'mode') { va = _normalizeMode(a.mode); vb = _normalizeMode(b.mode); }
      else { va = a[key]||''; vb = b[key]||''; }
      if (typeof va === 'number' && typeof vb === 'number') return _runsSortAsc ? va - vb : vb - va;
      return _runsSortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
    });
  }
  return out;
}

async function renameRun(id) {
  const inp = document.getElementById('rn-inp-' + id);
  if (!inp) return;
  const newName = inp.value.trim();
  if (!newName) { toast('Name cannot be empty', 'warn'); return; }
  try {
    const res = await fetch('/api/runs/' + id, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({run_name: newName}) });
    if (!res.ok) throw new Error('Failed');
    toast('Run renamed', 'success');
    // Update cache
    const r = _allRunsCache.find(x => x.id === id);
    if (r) r.run_name = newName;
    _renderFilteredRuns();
  } catch(e) { toast('Rename failed: ' + e.message, 'danger'); }
}

async function moveRunToFolder(id) {
  // Fetch saved strategy folders
  let folders = ['Scalping', 'Intraday', 'Swing', 'Positional', 'Experimental', 'Hedging'];
  try {
    const res = await fetch('/api/strategies');
    const strats = await res.json();
    const extraFolders = [...new Set(strats.map(s => s.folder).filter(Boolean))];
    folders = [...new Set([...folders, ...extraFolders])];
  } catch(e) {}
  const run = _allRunsCache.find(x => x.id === id);
  const currentFolder = run?.folder || '';
  let optionsHtml = folders.map(f => `<option value="${f}"${f === currentFolder ? ' selected' : ''}>${f}</option>`).join('');
  optionsHtml = '<option value="">— No Folder —</option>' + optionsHtml + '<option value="__custom__">+ Custom Folder</option>';
  const html = `<div style="font-size:13px;">
    <label style="font-size:12px;color:var(--muted);margin-bottom:6px;display:block;">Move "<strong>${run?.run_name||'Unnamed'}</strong>" to folder:</label>
    <select id="move-folder-sel" onchange="if(this.value==='__custom__'){document.getElementById('move-folder-custom').style.display='block'}else{document.getElementById('move-folder-custom').style.display='none'}" style="width:100%;padding:8px;font-size:13px;margin-bottom:8px;">${optionsHtml}</select>
    <input type="text" id="move-folder-custom" placeholder="Custom folder name" style="display:none;width:100%;padding:8px;font-size:13px;">
  </div>`;
  const ok = await customConfirm(html, { title: 'Move to Folder', okText: 'Move', danger: false });
  if (!ok) return;
  const sel = document.getElementById('move-folder-sel');
  let folder = sel?.value || '';
  if (folder === '__custom__') folder = document.getElementById('move-folder-custom')?.value?.trim() || '';
  try {
    const res = await fetch('/api/runs/' + id, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({folder}) });
    if (!res.ok) throw new Error('Failed');
    toast('Moved to ' + (folder || 'No Folder'), 'success');
    if (run) run.folder = folder;
    _renderFilteredRuns();
  } catch(e) { toast('Move failed: ' + e.message, 'danger'); }
}

function _truncName(name, max) {
  if (!name) return 'Unnamed';
  return name.length > max ? name.substring(0, max) + '...' : name;
}

function _buildRunCards(runs, opts = {}) {
  if (!runs.length) return '<div class="mobile-data-card mobile-data-card-empty">No runs yet.</div>';
  const showCheck = opts.checkboxes !== false;
  return runs.map(r => {
    const pnlColor = (r.total_pnl || 0) >= 0 ? 'var(--success)' : 'var(--danger)';
    const instName = escapeHtml(getInstrumentName(r.instrument) || '-');
    const folderText = r.folder ? escapeHtml(r.folder) : '—';
    const chk = _selectedRunIds.has(r.id) ? ' checked' : '';
    const folderBadge = r.folder ? `<span style="display:inline-block;padding:2px 8px;border-radius:999px;font-size:10px;background:rgba(99,102,241,0.12);color:rgb(165,148,249);border:1px solid rgba(99,102,241,0.25);">${escapeHtml(r.folder)}</span>` : '<span style="font-size:11px;color:var(--muted);">No folder</span>';
    const selectHtml = showCheck ? `<label style="display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--muted);"><input type="checkbox" class="tbl-chk run-chk" data-id="${r.id}" onchange="toggleRunCheck(this)"${chk}> Select</label>` : '';
    return `<article class="mobile-data-card">
      <div class="mobile-data-card-head">
        <div>
          <div class="mobile-data-card-title">${escapeHtml(r.run_name || 'Unnamed')}</div>
          <div class="mobile-data-card-sub">${_getModeBadge(r.mode)} <span style="margin-left:6px;">${instName}</span></div>
        </div>
        <div class="mobile-data-card-value" style="color:${pnlColor};">${fmt(r.total_pnl || 0)}</div>
      </div>
      <div class="mobile-data-card-grid">
        <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Period</span><span class="mobile-data-card-text">${escapeHtml(r.from_date || '—')} → ${escapeHtml(r.to_date || '—')}</span></div>
        <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Trades</span><span class="mobile-data-card-text">${escapeHtml(String(r.trade_count || 0))}</span></div>
        <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Folder</span><span class="mobile-data-card-text">${folderText}</span></div>
        <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Mode</span><span class="mobile-data-card-text">${escapeHtml(String(r.mode || '—').toUpperCase())}</span></div>
      </div>
      <div class="mobile-card-actions">
        ${selectHtml}
        ${folderBadge}
      </div>
      <div class="mobile-card-actions">
        <button class="btn btn-sm" onclick="viewRunModal(${r.id})" style="font-size:11px;padding:6px 12px;">View</button>
        <button class="btn btn-secondary btn-sm" onclick="copyEditRun(${r.id})" style="font-size:11px;padding:6px 12px;">Copy&amp;Edit</button>
        <button class="btn btn-sm" onclick="event.stopPropagation();_showRenameInline(${r.id})" style="font-size:11px;padding:6px 10px; --btn-bg: rgba(99,102,241,0.15); --btn-color: #a594f9; --btn-border: rgba(99,102,241,0.3);" title="Rename">Rename</button>
        <button class="btn btn-sm" onclick="event.stopPropagation();moveRunToFolder(${r.id})" style="font-size:11px;padding:6px 10px; --btn-bg: rgba(245,158,11,0.15); --btn-color: var(--warn); --btn-border: rgba(245,158,11,0.3);" title="Move to Folder">Folder</button>
        <button class="btn btn-danger btn-sm" onclick="deleteRun(${r.id})" style="font-size:11px;padding:6px 12px;">Del</button>
      </div>
    </article>`;
  }).join('');
}

function _buildRunsTable(runs, opts = {}) {
  if (!runs.length) return '';
  const showCheck = opts.checkboxes !== false;
  const thStyle = 'padding:10px;cursor:pointer;user-select:none;white-space:nowrap;';
  let html = `<table style="width: 100%; text-align: left; border-collapse: collapse; font-size: 13px;">
    <thead><tr style="border-bottom: 2px solid var(--border); color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: 0.3px; font-weight: 600;">
      ${showCheck ? '<th style="padding: 10px; width: 36px;"><input type="checkbox" class="tbl-chk" aria-label="Select all runs" onchange="toggleAllRuns(this)"></th>' : ''}
      <th style="${thStyle}" onclick="_toggleRunsSort('mode')">Mode ${_sortArrow('mode')}</th>
      <th style="${thStyle}" onclick="_toggleRunsSort('run_name')">Run Name ${_sortArrow('run_name')}</th>
      <th style="${thStyle}" onclick="_toggleRunsSort('instrument')">Instrument ${_sortArrow('instrument')}</th>
      <th style="${thStyle}" onclick="_toggleRunsSort('period')">Period ${_sortArrow('period')}</th>
      <th style="${thStyle}" onclick="_toggleRunsSort('trades')">Trades ${_sortArrow('trades')}</th>
      <th style="${thStyle}" onclick="_toggleRunsSort('pnl')">P&L ${_sortArrow('pnl')}</th>
      <th style="${thStyle}">Folder</th>
      <th style="padding: 10px; width: 240px; min-width: 240px; text-align: center;">Actions</th>
    </tr></thead><tbody>`;

  runs.forEach(r => {
    const pnlColor = (r.total_pnl || 0) >= 0 ? 'var(--success)' : 'var(--danger)';
    const instName = escapeHtml(getInstrumentName(r.instrument) || '-');
    const chk = _selectedRunIds.has(r.id) ? ' checked' : '';
    const safeRunName = escapeHtml(r.run_name || 'Unnamed');
    const safeRunTitle = escapeAttr(r.run_name || 'Unnamed');
    const folderBadge = r.folder ? `<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;background:rgba(99,102,241,0.12);color:rgb(165,148,249);border:1px solid rgba(99,102,241,0.25);">${escapeHtml(r.folder)}</span>` : '<span style="color:var(--muted);font-size:11px;">—</span>';
    html += `<tr style="border-bottom: 1px solid var(--border);" data-run-mode="${_normalizeMode(r.mode)}" onmouseover="this.style.background='rgba(var(--pf-tint-primary-rgb, 0,200,150),0.03)'" onmouseout="this.style.background='transparent'">
      ${showCheck ? '<td style="padding: 10px;"><input type="checkbox" class="tbl-chk run-chk" data-id="' + r.id + '" aria-label="Select run ' + escapeAttr(r.run_name || r.id) + '" onchange="toggleRunCheck(this)"' + chk + '></td>' : ''}
      <td style="padding: 10px;">${_getModeBadge(r.mode)}</td>
      <td style="padding: 10px; font-weight: 600; color: var(--accent); cursor: pointer; max-width: 180px;" onclick="viewRun(${r.id})" title="${safeRunTitle}">${escapeHtml(_truncName(r.run_name, 18))}</td>
      <td style="padding: 10px;">${instName}</td>
      <td style="padding: 10px; font-size: 12px;">${escapeHtml(r.from_date || '')} → ${escapeHtml(r.to_date || '')}</td>
      <td style="padding: 10px; font-weight: 600;">${r.trade_count || 0}</td>
      <td style="padding: 10px; font-weight: 700; color: ${pnlColor}; font-family: 'JetBrains Mono', monospace;">${fmt(r.total_pnl || 0)}</td>
      <td style="padding: 10px;">${folderBadge}</td>
      <td style="padding: 10px; width: 240px; min-width: 240px; white-space: nowrap; text-align: center;">
        <div style="display: inline-flex; gap: 4px; align-items: center; justify-content: center;">
          <button class="btn btn-sm" onclick="viewRunModal(${r.id})" style="font-size: 11px; padding: 5px 10px;">View</button>
          <button class="btn btn-secondary btn-sm" onclick="copyEditRun(${r.id})" style="font-size: 11px; padding: 5px 10px;">Copy&amp;Edit</button>
          <button class="btn btn-sm" onclick="event.stopPropagation();_showRenameInline(${r.id})" style="font-size: 11px; padding: 5px 8px; --btn-bg: rgba(99,102,241,0.15); --btn-color: #a594f9; --btn-border: rgba(99,102,241,0.3);" title="Rename">✏️</button>
          <button class="btn btn-sm" onclick="event.stopPropagation();moveRunToFolder(${r.id})" style="font-size: 11px; padding: 5px 8px; --btn-bg: rgba(245,158,11,0.15); --btn-color: var(--warn); --btn-border: rgba(245,158,11,0.3);" title="Move to Folder">📁</button>
          <button class="btn btn-danger btn-sm" onclick="deleteRun(${r.id})" style="font-size: 11px; padding: 5px 10px;">Del</button>
        </div>
      </td>
    </tr>`;
  });
  html += '</tbody></table>';
  return `<div class="trade-table-scroll">${html}</div><div class="mobile-data-cards">${_buildRunCards(runs, opts)}</div>`;
}

function _showRenameInline(id) {
  const run = _allRunsCache.find(x => x.id === id);
  const currentName = run?.run_name || 'Unnamed';
  const html = `<div style="font-size:13px;">
    <label style="font-size:12px;color:var(--muted);margin-bottom:6px;display:block;">New name:</label>
    <input type="text" id="rn-inp-${id}" value="${escapeAttr(currentName)}" style="width:100%;padding:8px;font-size:13px;" onkeydown="if(event.key==='Enter'){renameRun(${id})}">
  </div>`;
  customConfirm(html, { title: 'Rename Run', okText: 'Save', danger: false }).then(ok => {
    if (ok) renameRun(id);
  });
}

// ── Pagination helper ──
function _buildPagination(page, total, perPage, onPageFn) {
  const totalPages = Math.ceil(total / perPage) || 1;
  if (totalPages <= 1) return '';
  const start = (page - 1) * perPage;
  let info = 'Showing ' + (total ? start + 1 : 0) + '–' + Math.min(start + perPage, total) + ' of ' + total;
  let btns = '';
  btns += '<button class="page-btn" onclick="' + onPageFn + '(1)"' + (page <= 1 ? ' disabled' : '') + '>«</button>';
  btns += '<button class="page-btn" onclick="' + onPageFn + '(' + (page - 1) + ')"' + (page <= 1 ? ' disabled' : '') + '>‹</button>';
  const sp = Math.max(1, page - 2), ep = Math.min(totalPages, page + 2);
  for (let p = sp; p <= ep; p++) {
    btns += '<button class="page-btn' + (p === page ? ' active' : '') + '" onclick="' + onPageFn + '(' + p + ')">' + p + '</button>';
  }
  btns += '<button class="page-btn" onclick="' + onPageFn + '(' + (page + 1) + ')"' + (page >= totalPages ? ' disabled' : '') + '>›</button>';
  btns += '<button class="page-btn" onclick="' + onPageFn + '(' + totalPages + ')"' + (page >= totalPages ? ' disabled' : '') + '>»</button>';
  return '<div class="pagination-info">' + info + '</div><div class="pagination-controls">' + btns + '</div>';
}

// ── Bulk select helpers ──
function toggleRunCheck(el) {
  const id = parseInt(el.dataset.id);
  if (el.checked) _selectedRunIds.add(id); else _selectedRunIds.delete(id);
  _updateBulkBar();
}
function toggleAllRuns(el) {
  const boxes = document.querySelectorAll('.run-chk');
  boxes.forEach(b => { b.checked = el.checked; const id = parseInt(b.dataset.id); if (el.checked) _selectedRunIds.add(id); else _selectedRunIds.delete(id); });
  _updateBulkBar();
}
function _updateBulkBar() {
  ['runs-bulk-bar', 'runs-bulk-bar-dash'].forEach(barId => {
    const bar = document.getElementById(barId);
    if (!bar) return;
    const n = _selectedRunIds.size;
    if (!n) { bar.style.display = 'none'; return; }
    bar.style.display = 'flex';
    bar.innerHTML = '<span class="bulk-count">' + n + ' selected</span>'
      + '<button class="btn btn-danger btn-sm" onclick="bulkDeleteRuns()" style="font-size:11px;padding:5px 12px;">' + ICO.trash(13) + ' Delete</button>'
      + '<button class="btn btn-sm" onclick="bulkMoveToFolder()" style="font-size:11px;padding:5px 12px;--btn-bg:rgba(245,158,11,0.15);--btn-color:var(--warn);--btn-border:rgba(245,158,11,0.3);">📁 Move to Folder</button>'
      + '<button class="btn btn-sm" onclick="bulkRename()" style="font-size:11px;padding:5px 12px;--btn-bg:rgba(99,102,241,0.15);--btn-color:#a594f9;--btn-border:rgba(99,102,241,0.3);">✏️ Rename</button>'
      + '<button class="page-btn" onclick="_selectedRunIds.clear();_renderFilteredRuns();_renderDashRuns();" style="font-size:11px;">Clear</button>';
  });
}

async function bulkMoveToFolder() {
  const ids = Array.from(_selectedRunIds);
  if (!ids.length) return;
  let folders = ['Scalping', 'Intraday', 'Swing', 'Positional', 'Experimental', 'Hedging'];
  try { const res = await fetch('/api/strategies'); const strats = await res.json(); const extra = [...new Set(strats.map(s => s.folder).filter(Boolean))]; folders = [...new Set([...folders, ...extra])]; } catch(e) {}
  let optionsHtml = '<option value="">— No Folder —</option>' + folders.map(f => `<option value="${escapeAttr(f)}">${escapeHtml(f)}</option>`).join('') + '<option value="__custom__">+ Custom Folder</option>';
  const html = `<div style="font-size:13px;">
    <label style="font-size:12px;color:var(--muted);margin-bottom:6px;display:block;">Move <strong>${ids.length}</strong> run(s) to folder:</label>
    <select id="bulk-move-sel" onchange="if(this.value==='__custom__'){document.getElementById('bulk-move-custom').style.display='block'}else{document.getElementById('bulk-move-custom').style.display='none'}" style="width:100%;padding:8px;font-size:13px;margin-bottom:8px;">${optionsHtml}</select>
    <input type="text" id="bulk-move-custom" placeholder="Custom folder name" style="display:none;width:100%;padding:8px;font-size:13px;">
  </div>`;
  const ok = await customConfirm(html, { title: 'Bulk Move to Folder', okText: 'Move All', danger: false });
  if (!ok) return;
  let folder = document.getElementById('bulk-move-sel')?.value || '';
  if (folder === '__custom__') folder = document.getElementById('bulk-move-custom')?.value?.trim() || '';
  let moved = 0;
  for (const id of ids) {
    try { await fetch('/api/runs/' + id, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({folder}) }); const r = _allRunsCache.find(x => x.id === id); if (r) r.folder = folder; moved++; } catch(e) {}
  }
  toast(moved + ' run(s) moved to ' + (folder || 'No Folder'), 'success');
  _selectedRunIds.clear();
  _renderFilteredRuns(); _renderDashRuns();
}

async function bulkRename() {
  const ids = Array.from(_selectedRunIds);
  if (!ids.length) return;
  if (ids.length === 1) { _showRenameInline(ids[0]); return; }
  const html = `<div style="font-size:13px;">
    <label style="font-size:12px;color:var(--muted);margin-bottom:6px;display:block;">Add prefix/suffix to <strong>${ids.length}</strong> run name(s):</label>
    <div style="display:flex;gap:8px;margin-bottom:8px;">
      <input type="text" id="bulk-rename-prefix" placeholder="Prefix (optional)" style="flex:1;padding:8px;font-size:13px;">
      <input type="text" id="bulk-rename-suffix" placeholder="Suffix (optional)" style="flex:1;padding:8px;font-size:13px;">
    </div>
    <span style="font-size:11px;color:var(--muted);">Example: prefix "V2_" + suffix "_final" → "V2_StrategyName_final"</span>
  </div>`;
  const ok = await customConfirm(html, { title: 'Bulk Rename', okText: 'Apply', danger: false });
  if (!ok) return;
  const prefix = document.getElementById('bulk-rename-prefix')?.value || '';
  const suffix = document.getElementById('bulk-rename-suffix')?.value || '';
  if (!prefix && !suffix) { toast('Enter a prefix or suffix', 'warn'); return; }
  let renamed = 0;
  for (const id of ids) {
    const r = _allRunsCache.find(x => x.id === id);
    if (!r) continue;
    const newName = prefix + (r.run_name || 'Unnamed') + suffix;
    try { await fetch('/api/runs/' + id, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify({run_name: newName}) }); r.run_name = newName; renamed++; } catch(e) {}
  }
  toast(renamed + ' run(s) renamed', 'success');
  _selectedRunIds.clear();
  _renderFilteredRuns(); _renderDashRuns();
}
async function bulkDeleteRuns() {
  const ids = Array.from(_selectedRunIds);
  if (!ids.length) return;
  const ok = await customConfirm('Delete <strong>' + ids.length + '</strong> selected run' + (ids.length > 1 ? 's' : '') + '?<br><span style="font-size:11px;">This cannot be undone.</span>', { title: 'Bulk Delete', icon: ICO.trash(28), okText: 'Delete All', danger: true });
  if (!ok) return;
  try {
    const r = await fetch('/api/runs/bulk-delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ ids }) });
    if (!r.ok) throw new Error('Failed');
    _selectedRunIds.clear();
    toast(ids.length + ' run' + (ids.length > 1 ? 's' : '') + ' deleted', 'success');
    fetchRuns();
  } catch(e) { toast('Bulk delete failed: ' + e.message, 'danger'); }
}

function filterRuns(mode, btn) {
  _currentRunFilter = mode;
  _runsPageResults = 1;
  _selectedRunIds.clear();
  _selectedScalpRunIds.clear();
  const _setV = (el, bg, clr, bdr) => { el.style.setProperty('--btn-bg', bg); el.style.setProperty('--btn-color', clr); el.style.setProperty('--btn-border', bdr); };
  document.querySelectorAll('.runs-filter-btn').forEach(b => {
    b.classList.remove('active');
    const f = b.getAttribute('data-filter');
    if (f === 'all') _setV(b, 'linear-gradient(180deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.03) 100%)', 'var(--muted)', 'rgba(255,255,255,0.1)');
    else if (f === 'backtest') _setV(b, 'linear-gradient(180deg, rgba(59,130,246,0.25) 0%, rgba(40,90,180,0.4) 100%)', 'rgb(96,165,250)', 'rgba(59,130,246,0.5)');
    else if (f === 'paper') _setV(b, 'linear-gradient(180deg, rgba(245,158,11,0.25) 0%, rgba(180,120,8,0.4) 100%)', 'rgb(251,191,36)', 'rgba(245,158,11,0.5)');
    else if (f === 'live') _setV(b, 'linear-gradient(180deg, rgba(139,92,246,0.25) 0%, rgba(100,60,200,0.4) 100%)', 'rgb(167,139,250)', 'rgba(139,92,246,0.5)');
    else if (f === 'scalp') _setV(b, 'linear-gradient(180deg, rgba(var(--pf-tint-primary-rgb, 6,182,212),0.25) 0%, rgba(4,130,155,0.4) 100%)', 'var(--accent)', 'rgba(var(--pf-tint-primary-rgb, 6,182,212),0.5)');
  });
  if (btn) {
    btn.classList.add('active');
    if (mode === 'all') _setV(btn, 'linear-gradient(180deg, rgba(var(--pf-tint-primary-rgb, 0,200,150),0.3) 0%, rgba(0,150,110,0.5) 100%)', 'rgb(52,211,153)', 'rgba(var(--pf-tint-primary-rgb, 0,200,150),0.6)');
    else if (mode === 'backtest') _setV(btn, 'linear-gradient(180deg, rgba(59,130,246,0.35) 0%, rgba(40,90,180,0.55) 100%)', 'rgb(96,165,250)', 'rgba(59,130,246,0.7)');
    else if (mode === 'paper') _setV(btn, 'linear-gradient(180deg, rgba(245,158,11,0.35) 0%, rgba(180,120,8,0.55) 100%)', 'rgb(251,191,36)', 'rgba(245,158,11,0.7)');
    else if (mode === 'live') _setV(btn, 'linear-gradient(180deg, rgba(139,92,246,0.35) 0%, rgba(100,60,200,0.55) 100%)', 'rgb(167,139,250)', 'rgba(139,92,246,0.7)');
    else if (mode === 'scalp') _setV(btn, 'linear-gradient(180deg, rgba(var(--pf-tint-primary-rgb, 6,182,212),0.35) 0%, rgba(4,130,155,0.55) 100%)', 'var(--accent)', 'rgba(var(--pf-tint-primary-rgb, 6,182,212),0.7)');
  }
  _renderFilteredRuns();
}

function _renderFilteredRuns() {
  const containerResults = document.getElementById('runs-list-results');
  const emptyResults = document.getElementById('runs-empty-results');
  const pagEl = document.getElementById('runs-pagination-results');

  if (_currentRunFilter === 'scalp') {
    _renderScalpRuns(containerResults, emptyResults, pagEl);
    return;
  }

  let filtered = _allRunsCache;
  if (_currentRunFilter !== 'all') {
    filtered = _allRunsCache.filter(r => _normalizeMode(r.mode) === _currentRunFilter);
  }
  // Apply search + sort
  filtered = _applySortAndSearch(filtered);

  if (!filtered.length) {
    if (emptyResults) { emptyResults.style.display = 'block'; emptyResults.textContent = _runsSearchQuery ? 'No matching runs.' : (_currentRunFilter === 'all' ? 'No runs yet.' : 'No ' + _currentRunFilter + ' runs found.'); }
    if (containerResults) containerResults.innerHTML = '';
    if (pagEl) { pagEl.style.display = 'none'; pagEl.innerHTML = ''; }
    _updateBulkBar();
    return;
  }
  if (emptyResults) emptyResults.style.display = 'none';

  const total = filtered.length;
  const totalPages = Math.ceil(total / _RUNS_PER_PAGE);
  if (_runsPageResults > totalPages) _runsPageResults = totalPages;
  if (_runsPageResults < 1) _runsPageResults = 1;
  const start = (_runsPageResults - 1) * _RUNS_PER_PAGE;
  const pageData = filtered.slice(start, start + _RUNS_PER_PAGE);

  // Bulk bar
  let bulkHtml = '<div id="runs-bulk-bar" class="bulk-bar" style="display:none;"></div>';
  if (containerResults) containerResults.innerHTML = bulkHtml + _buildRunsTable(pageData);
  if (pagEl) {
    const pHtml = _buildPagination(_runsPageResults, total, _RUNS_PER_PAGE, '_goResultsPage');
    if (pHtml) { pagEl.innerHTML = pHtml; pagEl.style.display = 'flex'; } else { pagEl.style.display = 'none'; }
  }
  _updateBulkBar();
}

function _goResultsPage(p) { _runsPageResults = p; _renderFilteredRuns(); }

function _dashboardTransactionRowKey(txn, idx) {
  const base = [
    txn.sourceKind || '',
    txn.sourceId ?? '',
    txn.tradeSignature || '',
    txn.tradeOccurrence || 1,
    txn.entryTime || '',
    txn.exitTime || '',
    txn.symbol || '',
    idx,
  ].join('|');
  return base || ('dashboard-txn-' + idx);
}

function _normalizeDashboardTransactions(transactions = null) {
  const sourceTxns = Array.isArray(transactions) ? transactions : _dashboardTransactionsCache;
  const txns = Array.isArray(sourceTxns) && sourceTxns.length ? sourceTxns.map((txn, idx) => ({
    time: txn.time || txn.exit_time || txn.entry_time || '',
    symbol: txn.symbol || '—',
    transactionType: String(txn.transaction_type || txn.action || 'TRADE').toUpperCase(),
    entryTime: txn.entry_time || '',
    exitTime: txn.exit_time || '',
    entryPrice: Number(txn.entry_price ?? txn.price ?? 0),
    exitPrice: Number(txn.exit_price ?? txn.price ?? 0),
    quantity: txn.quantity ?? txn.lots ?? '—',
    pnl: Number(txn.pnl ?? 0),
    reason: txn.reason || '—',
    runName: txn.run_name || '',
    mode: txn.mode || 'paper',
    deletable: !!txn.deletable,
    sourceKind: txn.source_kind || '',
    sourceId: txn.source_id ?? '',
    tradeSignature: txn.trade_signature || '',
    tradeOccurrence: Number(txn.trade_occurrence || 1),
    _idx: idx,
  })) : [];

  if (!txns.length) {
    (_allRunsCache || []).forEach(run => {
      const trades = Array.isArray(run && run.trades) ? run.trades : [];
      trades.forEach((trade, idx) => {
        if (!trade || typeof trade !== 'object') return;
        const symbol = trade.symbol || [trade.underlying, trade.strike, trade.option_type].filter(Boolean).join(' ') || '—';
        txns.push({
          time: trade.exit_time || trade.entry_time || run.created_at || '',
          symbol,
          transactionType: String(trade.transaction_type || 'TRADE').toUpperCase(),
          entryTime: trade.entry_time || '',
          exitTime: trade.exit_time || '',
          entryPrice: Number(trade.entry_premium ?? trade.entry_price ?? trade.current_premium ?? 0),
          exitPrice: Number(trade.exit_premium ?? trade.exit_price ?? trade.current_premium ?? 0),
          quantity: trade.lots ?? trade.quantity ?? '—',
          pnl: Number(trade.pnl ?? 0),
          reason: trade.exit_reason || trade.reason || '—',
          runName: run.run_name || run.strategy_name || '',
          mode: run.mode || 'backtest',
          deletable: false,
          sourceKind: '',
          sourceId: '',
          tradeSignature: '',
          tradeOccurrence: 1,
          _idx: idx,
        });
      });
    });
  }

  txns.forEach((txn, idx) => {
    txn.rowKey = _dashboardTransactionRowKey(txn, idx);
  });

  txns.sort((a, b) => {
    const ta = Date.parse(a.time || '') || 0;
    const tb = Date.parse(b.time || '') || 0;
    return tb - ta || b._idx - a._idx;
  });

  return txns;
}

function setDashboardTransactionSearch(value) {
  _dashboardTxnSearchQuery = String(value || '').trim().toLowerCase();
  _dashboardTxnPage = 1;
  _renderDashboardTransactions();
}

function _dashboardTxnSortArrow(col) {
  if (_dashboardTxnSortCol !== col) return '<span style="opacity:0.3;font-size:10px;margin-left:3px;">⇅</span>';
  return _dashboardTxnSortAsc
    ? '<span style="color:var(--accent);font-size:10px;margin-left:3px;">▲</span>'
    : '<span style="color:var(--accent);font-size:10px;margin-left:3px;">▼</span>';
}

function _toggleDashboardTxnSort(col) {
  if (_dashboardTxnSortCol === col) _dashboardTxnSortAsc = !_dashboardTxnSortAsc;
  else {
    _dashboardTxnSortCol = col;
    _dashboardTxnSortAsc = true;
  }
  _dashboardTxnPage = 1;
  _renderDashboardTransactions();
}

function _applyDashboardTransactionFilters(rows) {
  const search = _dashboardTxnSearchQuery;
  const filtered = (rows || []).filter(txn => {
    const searchHaystack = [
      txn.symbol,
      txn.runName,
      txn.mode,
      txn.transactionType,
      txn.reason,
      txn.entryTime,
      txn.exitTime,
      txn.quantity,
      txn.pnl,
    ].join(' ').toLowerCase();
    if (search && !searchHaystack.includes(search)) return false;
    return true;
  });

  const sorted = filtered.slice().sort((a, b) => {
    let left = a?.[_dashboardTxnSortCol];
    let right = b?.[_dashboardTxnSortCol];
    if (_dashboardTxnSortCol === 'entryTime' || _dashboardTxnSortCol === 'exitTime' || _dashboardTxnSortCol === 'time') {
      left = Date.parse(left || '') || 0;
      right = Date.parse(right || '') || 0;
    } else if (_dashboardTxnSortCol === 'entryPrice' || _dashboardTxnSortCol === 'exitPrice' || _dashboardTxnSortCol === 'pnl') {
      left = Number(left || 0);
      right = Number(right || 0);
    } else if (_dashboardTxnSortCol === 'quantity') {
      left = parseFloat(left || 0) || 0;
      right = parseFloat(right || 0) || 0;
    } else {
      left = String(left ?? '').toLowerCase();
      right = String(right ?? '').toLowerCase();
    }
    if (left < right) return _dashboardTxnSortAsc ? -1 : 1;
    if (left > right) return _dashboardTxnSortAsc ? 1 : -1;
    return (b._idx || 0) - (a._idx || 0);
  });
  return sorted;
}

function toggleDashboardTransactionCheck(el) {
  const key = String(el.dataset.key || '');
  if (!key) return;
  if (el.checked) _selectedDashboardTxnKeys.add(key); else _selectedDashboardTxnKeys.delete(key);
  _updateDashboardTransactionsBulkBar();
}

function toggleAllDashboardTransactions(el) {
  document.querySelectorAll('.dashboard-txn-chk').forEach(box => {
    box.checked = el.checked;
    const key = String(box.dataset.key || '');
    if (!key) return;
    if (el.checked) _selectedDashboardTxnKeys.add(key); else _selectedDashboardTxnKeys.delete(key);
  });
  _updateDashboardTransactionsBulkBar();
}

function _dashboardTransactionDeleteItems(rows) {
  return (rows || []).filter(txn => txn && txn.deletable).map(txn => ({
    source_kind: txn.sourceKind,
    source_id: txn.sourceId,
    trade_signature: txn.tradeSignature,
    trade_occurrence: txn.tradeOccurrence,
  }));
}

function _updateDashboardTransactionsBulkBar(pageRows = null) {
  const bar = document.getElementById('transactions-bulk-bar');
  const headerBox = document.getElementById('transactions-check-all');
  if (!bar) return;

  const validKeys = new Set(_dashboardTxnByKey.keys());
  Array.from(_selectedDashboardTxnKeys).forEach(key => {
    if (!validKeys.has(key)) _selectedDashboardTxnKeys.delete(key);
  });

  const selectedRows = Array.from(_selectedDashboardTxnKeys).map(key => _dashboardTxnByKey.get(key)).filter(Boolean);
  const selectedCount = _selectedDashboardTxnKeys.size;
  const deletableCount = selectedRows.filter(row => row.deletable).length;
  if (!selectedCount) {
    bar.style.display = 'none';
  } else {
    bar.style.display = 'flex';
    bar.innerHTML = '<span class="bulk-count">' + selectedCount + ' selected</span>'
      + (deletableCount ? '<button class="bulk-del-btn" onclick="bulkDeleteDashboardTransactions()">' + ICO.trash(14) + ' Delete Selected</button>' : '<span style="font-size:11px;color:var(--muted);">Active rows cannot be deleted</span>')
      + '<button class="page-btn" onclick="_selectedDashboardTxnKeys.clear();_renderDashboardTransactions();" style="font-size:11px;">Clear</button>';
  }

  if (headerBox) {
    const visibleRows = Array.isArray(pageRows)
      ? pageRows
      : Array.from(document.querySelectorAll('.dashboard-txn-chk')).map(box => _dashboardTxnByKey.get(String(box.dataset.key || ''))).filter(Boolean);
    const checkedCount = visibleRows.filter(txn => _selectedDashboardTxnKeys.has(txn.rowKey)).length;
    headerBox.checked = !!visibleRows.length && checkedCount === visibleRows.length;
    headerBox.indeterminate = checkedCount > 0 && checkedCount < visibleRows.length;
  }
}

async function _submitDashboardTransactionDelete(rows) {
  const items = _dashboardTransactionDeleteItems(rows);
  if (!items.length) {
    toast('No saved transactions selected', 'warn');
    return;
  }
  const res = await fetch('/api/dashboard/recent-transactions/bulk-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ items }),
  });
  if (!res.ok) throw new Error('Delete failed');
  return res.json();
}

async function deleteDashboardTransaction(rowKey) {
  const txn = _dashboardTxnByKey.get(String(rowKey || ''));
  if (!txn || !txn.deletable) {
    toast('Only saved transactions can be deleted', 'warn');
    return;
  }
  const ok = await customConfirm('Delete this recent transaction?<br><span style="font-size:11px;">This removes the saved trade record from history.</span>', {
    title: 'Delete Transaction',
    icon: ICO.trash(28),
    okText: 'Delete',
    danger: true,
  });
  if (!ok) return;
  try {
    await _submitDashboardTransactionDelete([txn]);
    _selectedDashboardTxnKeys.delete(txn.rowKey);
    toast('Transaction deleted', 'success');
    await Promise.all([fetchRuns(), loadDashboardSummary()]);
  } catch (e) {
    toast('Delete failed: ' + e.message, 'danger');
  }
}

async function bulkDeleteDashboardTransactions() {
  const rows = Array.from(_selectedDashboardTxnKeys).map(key => _dashboardTxnByKey.get(key)).filter(Boolean);
  const deletableRows = rows.filter(txn => txn.deletable);
  if (!deletableRows.length) {
    toast('No saved transactions selected', 'warn');
    return;
  }
  const ok = await customConfirm('Delete <strong>' + deletableRows.length + '</strong> selected transaction' + (deletableRows.length > 1 ? 's' : '') + '?<br><span style="font-size:11px;">This removes the saved trade records from history.</span>', {
    title: 'Bulk Delete',
    icon: ICO.trash(28),
    okText: 'Delete All',
    danger: true,
  });
  if (!ok) return;
  try {
    await _submitDashboardTransactionDelete(deletableRows);
    _selectedDashboardTxnKeys.clear();
    toast(deletableRows.length + ' transaction' + (deletableRows.length > 1 ? 's' : '') + ' deleted', 'success');
    await Promise.all([fetchRuns(), loadDashboardSummary()]);
  } catch (e) {
    toast('Bulk delete failed: ' + e.message, 'danger');
  }
}

function _goDashboardTxnPage(page) {
  _dashboardTxnPage = page;
  _renderDashboardTransactions();
}

function _formatDashboardTxnTime(value) {
  if (!value) return '—';
  const dt = new Date(value);
  if (!Number.isNaN(dt.getTime())) {
    return dt.toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    });
  }
  return String(value);
}

function _dashboardTransactionCardHtml(txn) {
  const dir = String(txn.transactionType || '—').replace(/_/g, ' ');
  const dirColor = dir === 'BUY' ? 'var(--success)' : dir === 'SELL' ? 'var(--danger)' : 'var(--muted)';
  const pnl = Number(txn.pnl || 0);
  const chk = _selectedDashboardTxnKeys.has(txn.rowKey) ? ' checked' : '';
  const actionHtml = txn.deletable
    ? `<button class="btn btn-danger btn-sm" onclick="deleteDashboardTransaction('${escapeAttr(txn.rowKey)}')" style="font-size:11px;padding:6px 12px;">Del</button>`
    : '<span style="font-size:11px;color:var(--muted);">Active</span>';
  return `<article class="mobile-data-card">
    <div class="mobile-data-card-head">
      <div>
        <div class="mobile-data-card-title">${escapeHtml(txn.symbol || '—')}</div>
        <div class="mobile-data-card-sub">${_getModeBadge(txn.mode)} <span style="margin-left:6px;">${escapeHtml(dir)}</span></div>
      </div>
      <div class="mobile-data-card-value" style="color:${pnl >= 0 ? 'var(--success)' : 'var(--danger)'}">₹${round2(pnl).toFixed(2)}</div>
    </div>
    <div class="mobile-data-card-grid">
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Entry</span><span class="mobile-data-card-text">${escapeHtml(_formatDashboardTxnTime(txn.entryTime))} · ₹${round2(txn.entryPrice || 0).toFixed(2)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Exit</span><span class="mobile-data-card-text">${escapeHtml(_formatDashboardTxnTime(txn.exitTime))} · ₹${round2(txn.exitPrice || 0).toFixed(2)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Qty</span><span class="mobile-data-card-text">${escapeHtml(txn.quantity)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Reason</span><span class="mobile-data-card-text">${escapeHtml(txn.reason || '—')}</span></div>
    </div>
    <div class="mobile-card-actions">
      <label style="display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--muted);"><input type="checkbox" class="tbl-chk dashboard-txn-chk" data-key="${escapeAttr(txn.rowKey)}" onchange="toggleDashboardTransactionCheck(this)"${chk}> Select</label>
      ${actionHtml}
    </div>
  </article>`;
}

function _renderDashboardTransactions(transactions = null) {
  const body = document.getElementById('transactions-body');
  const cards = document.getElementById('transactions-mobile-cards');
  const pagEl = document.getElementById('transactions-pagination');
  if (!body) return;

  const txns = _normalizeDashboardTransactions(transactions);
  _dashboardTxnByKey = new Map(txns.map(txn => [txn.rowKey, txn]));
  const filtered = _applyDashboardTransactionFilters(txns);
  const totalPages = Math.max(1, Math.ceil(filtered.length / _DASHBOARD_TXN_PER_PAGE));
  if (_dashboardTxnPage > totalPages) _dashboardTxnPage = totalPages;
  if (_dashboardTxnPage < 1) _dashboardTxnPage = 1;
  const start = (_dashboardTxnPage - 1) * _DASHBOARD_TXN_PER_PAGE;
  const pageRows = filtered.slice(start, start + _DASHBOARD_TXN_PER_PAGE);

  if (!pageRows.length) {
    body.innerHTML = '<tr><td colspan="11" class="dash-txn-empty">' + (_dashboardTxnSearchQuery ? 'No matching transactions' : 'No recent transactions') + '</td></tr>';
    if (cards) cards.innerHTML = '<div class="mobile-data-card mobile-data-card-empty">' + (_dashboardTxnSearchQuery ? 'No matching transactions' : 'No recent transactions') + '</div>';
    if (pagEl) {
      pagEl.style.display = 'none';
      pagEl.innerHTML = '';
    }
    _updateDashboardTransactionsBulkBar([]);
    return;
  }

  body.innerHTML = pageRows.map((txn) => {
    const dir = String(txn.transactionType || '—').replace(/_/g, ' ');
    const dirColor = dir === 'BUY' ? 'var(--success)' : dir === 'SELL' ? 'var(--danger)' : 'var(--muted)';
    const chk = _selectedDashboardTxnKeys.has(txn.rowKey) ? ' checked' : '';
    const actionHtml = txn.deletable
      ? '<button class="btn btn-danger btn-sm" onclick="deleteDashboardTransaction(\'' + escapeAttr(txn.rowKey) + '\')" style="font-size:11px;padding:5px 10px;">Del</button>'
      : '<span style="font-size:11px;color:var(--muted);">Active</span>';
    return `
      <tr>
        <td style="text-align:center;"><input type="checkbox" class="tbl-chk dashboard-txn-chk" data-key="${escapeAttr(txn.rowKey)}" aria-label="Select ${escapeAttr(txn.symbol || 'transaction')}" onchange="toggleDashboardTransactionCheck(this)"${chk}></td>
        <td>
          <div class="dash-txn-symbol-row">
            <div style="font-weight:600;color:var(--text);">${escapeHtml(txn.symbol || '—')}</div>
            ${_getModeBadge(txn.mode)}
          </div>
        </td>
        <td style="font-family:'JetBrains Mono', monospace;font-size:11px;color:var(--text-dim);">${escapeHtml(_formatDashboardTxnTime(txn.entryTime))}</td>
        <td style="font-family:'JetBrains Mono', monospace;font-size:11px;color:var(--text-dim);">${escapeHtml(_formatDashboardTxnTime(txn.exitTime))}</td>
        <td style="text-align:right;color:${dirColor};font-weight:700;">${escapeHtml(dir)}</td>
        <td style="text-align:right;font-family:'JetBrains Mono', monospace;">₹${round2(txn.entryPrice || 0).toFixed(2)}</td>
        <td style="text-align:right;font-family:'JetBrains Mono', monospace;">₹${round2(txn.exitPrice || 0).toFixed(2)}</td>
        <td style="text-align:right;">${escapeHtml(txn.quantity)}</td>
        <td style="text-align:right;font-family:'JetBrains Mono', monospace;color:${Number(txn.pnl || 0) >= 0 ? 'var(--success)' : 'var(--danger)'}">₹${round2(txn.pnl || 0).toFixed(2)}</td>
        <td style="font-size:11px;color:var(--text-dim);">${escapeHtml(txn.reason || '—')}</td>
        <td><div class="dash-txn-actions">${actionHtml}</div></td>
      </tr>
    `;
  }).join('');
  if (cards) cards.innerHTML = pageRows.map(_dashboardTransactionCardHtml).join('');

  if (pagEl) {
    const pHtml = _buildPagination(_dashboardTxnPage, filtered.length, _DASHBOARD_TXN_PER_PAGE, '_goDashboardTxnPage');
    if (pHtml) {
      pagEl.innerHTML = pHtml;
      pagEl.style.display = 'flex';
    } else {
      pagEl.style.display = 'none';
      pagEl.innerHTML = '';
    }
  }
  ['symbol', 'entryTime', 'exitTime', 'transactionType', 'entryPrice', 'exitPrice', 'quantity', 'pnl', 'reason'].forEach(col => {
    const el = document.getElementById('dash-txn-sort-' + col);
    if (el) el.innerHTML = _dashboardTxnSortArrow(col);
  });
  _updateDashboardTransactionsBulkBar(pageRows);
}

function _renderDashRuns() {
  const container = document.getElementById('runs-list');
  const empty = document.getElementById('runs-empty');
  const pagEl = document.getElementById('runs-pagination-dash');
  const dashRuns = (_allRunsCache || []).filter(r => _normalizeMode(r.mode) === 'backtest');
  if (!dashRuns.length) {
    if (empty) empty.style.display = 'block';
    if (container) container.innerHTML = '';
    if (pagEl) { pagEl.style.display = 'none'; }
    _renderDashboardTransactions(_dashboardTransactionsCache);
    return;
  }
  if (empty) empty.style.display = 'none';
  const total = dashRuns.length;
  const totalPages = Math.ceil(total / _RUNS_PER_PAGE);
  if (_runsPageDash > totalPages) _runsPageDash = totalPages;
  const start = (_runsPageDash - 1) * _RUNS_PER_PAGE;
  const pageData = dashRuns.slice(start, start + _RUNS_PER_PAGE);
  let bulkHtml = '<div id="runs-bulk-bar-dash" class="bulk-bar" style="display:none;"></div>';
  if (container) container.innerHTML = bulkHtml + _buildRunsTable(pageData);
  if (pagEl) {
    const pHtml = _buildPagination(_runsPageDash, total, _RUNS_PER_PAGE, '_goDashPage');
    if (pHtml) { pagEl.innerHTML = pHtml; pagEl.style.display = 'flex'; } else { pagEl.style.display = 'none'; }
  }
  _renderDashboardTransactions(_dashboardTransactionsCache);
  _updateBulkBar();
}
function _goDashPage(p) { _runsPageDash = p; _renderDashRuns(); }

function _buildScalpRunCards(trades) {
  if (!trades.length) return '<div class="mobile-data-card mobile-data-card-empty">No scalp trades found.</div>';
  return trades.map(t => {
    const pnl = Number(t.pnl || 0);
    const pnlColor = pnl >= 0 ? 'var(--success)' : 'var(--danger)';
    const fmtTime = (ts) => { if (!ts) return '—'; const s = String(ts); const m = s.match(/(\d{2}:\d{2}:\d{2})/); return m ? m[1] : s.slice(-8); };
    const name = t.symbol || (t.underlying + ' ' + t.strike + t.option_type) || 'Unnamed';
    const entryDate = t.entry_time ? String(t.entry_time).substring(0, 10) : '';
    const runName = name + (entryDate ? ' ' + entryDate : '');
    const chk = _selectedScalpRunIds.has(t.trade_id) ? ' checked' : '';
    return `<article class="mobile-data-card">
      <div class="mobile-data-card-head">
        <div>
          <div class="mobile-data-card-title">${escapeHtml(runName)}</div>
          <div class="mobile-data-card-sub">${_getModeBadge('scalp')} <span style="margin-left:6px;">${escapeHtml(t.underlying || '—')}</span></div>
        </div>
        <div class="mobile-data-card-value" style="color:${pnlColor};">${fmt(pnl)}</div>
      </div>
      <div class="mobile-data-card-grid">
        <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Entry</span><span class="mobile-data-card-text">${escapeHtml(fmtTime(t.entry_time))}</span></div>
        <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Exit</span><span class="mobile-data-card-text">${escapeHtml(fmtTime(t.exit_time))}</span></div>
        <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Period</span><span class="mobile-data-card-text">${escapeHtml(t.exit_reason || '—')}</span></div>
        <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Trade ID</span><span class="mobile-data-card-text">S${escapeHtml(t.trade_id)}</span></div>
      </div>
      <div class="mobile-card-actions">
        <label style="display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--muted);"><input type="checkbox" class="tbl-chk scalp-run-chk" data-id="${t.trade_id}" onchange="toggleScalpRunCheck(this)"${chk}> Select</label>
        <button class="btn btn-sm" onclick="viewScalpTrade(${t.trade_id})" style="font-size:11px;padding:6px 12px;">View</button>
        <button class="btn btn-danger btn-sm" onclick="deleteScalpTrade(${t.trade_id})" style="font-size:11px;padding:6px 12px;">Del</button>
      </div>
    </article>`;
  }).join('');
}

async function _renderScalpRuns(container, emptyEl, pagEl) {
  try {
    const res = await fetch('/api/scalp/trades');
    const trades = await res.json();
    if (!trades.length) {
      if (emptyEl) { emptyEl.style.display = 'block'; emptyEl.textContent = 'No scalp trades found.'; }
      if (container) container.innerHTML = '';
      if (pagEl) { pagEl.style.display = 'none'; }
      return;
    }
    if (emptyEl) emptyEl.style.display = 'none';
    _scalpTradesCache = trades.slice().reverse();

    const total = _scalpTradesCache.length;
    const totalPages = Math.ceil(total / _RUNS_PER_PAGE);
    if (_scalpPage > totalPages) _scalpPage = totalPages;
    const start = (_scalpPage - 1) * _RUNS_PER_PAGE;
    const pageData = _scalpTradesCache.slice(start, start + _RUNS_PER_PAGE);

    let tableHtml = `<table style="width: 100%; text-align: left; border-collapse: collapse; font-size: 13px;">
      <thead><tr style="border-bottom: 1px solid var(--border); color: var(--muted); text-transform: uppercase; font-size: 12px; letter-spacing: 0.3px; font-weight: 600;">
        <th style="padding: 10px; width: 36px;"><input type="checkbox" class="tbl-chk" aria-label="Select all open scalp trades" onchange="toggleAllScalpRuns(this)"></th>
        <th style="padding: 10px;">ID</th><th style="padding: 10px;">Mode</th><th style="padding: 10px;">Run Name</th><th style="padding: 10px;">Instrument</th><th style="padding: 10px;">Period</th>
        <th style="padding: 10px;">Trades</th><th style="padding: 10px;">P&L</th><th style="padding: 10px;">Entry Time</th><th style="padding: 10px;">Exit Time</th><th style="padding: 10px; width: 200px; min-width: 200px; text-align: center;">Actions</th>
      </tr></thead><tbody>`;
    pageData.forEach(t => {
      const pnl = t.pnl || 0;
      const pnlColor = pnl >= 0 ? 'var(--success)' : 'var(--danger)';
      const fmtTime = (ts) => { if (!ts) return '—'; const s = String(ts); const m = s.match(/(\d{2}:\d{2}:\d{2})/); return m ? m[1] : s.slice(-8); };
      const name = t.symbol || (t.underlying + ' ' + t.strike + t.option_type) || 'Unnamed';
      const entryDate = t.entry_time ? String(t.entry_time).substring(0, 10) : '';
      const runName = name + (entryDate ? ' ' + entryDate : '');
      const chk = _selectedScalpRunIds.has(t.trade_id) ? ' checked' : '';
      tableHtml += `<tr style="border-bottom: 1px solid var(--border);">
        <td style="padding: 10px;"><input type="checkbox" class="tbl-chk scalp-run-chk" data-id="${t.trade_id}" aria-label="Select scalp trade ${escapeAttr(t.trade_id)}" onchange="toggleScalpRunCheck(this)"${chk}></td>
        <td style="padding: 10px; color: var(--muted);">S${t.trade_id}</td>
        <td style="padding: 10px;">${_getModeBadge('scalp')}</td>
        <td style="padding: 10px; font-weight: 600; color: var(--accent);">${escapeHtml(runName)}</td>
        <td style="padding: 10px;">${escapeHtml(t.underlying || '—')}</td>
        <td style="padding: 10px; font-size: 12px;">${escapeHtml(t.exit_reason || '—')}</td>
        <td style="padding: 10px; font-weight: 600;">1</td>
        <td style="padding: 10px; font-weight: 700; color: ${pnlColor}; font-family: 'JetBrains Mono', monospace;">${fmt(pnl)}</td>
        <td style="padding: 10px; font-family: 'JetBrains Mono'; font-size: 12px; color: var(--muted);">${fmtTime(t.entry_time)}</td>
        <td style="padding: 10px; font-family: 'JetBrains Mono'; font-size: 12px; color: var(--muted);">${fmtTime(t.exit_time)}</td>
        <td style="padding: 10px; width: 200px; min-width: 200px; white-space: nowrap; text-align: center;">
          <div style="display: inline-flex; gap: 4px; align-items: center; justify-content: center;">
            <button class="btn btn-sm" onclick="viewScalpTrade(${t.trade_id})" style="font-size: 11px; padding: 5px 10px;">View</button>
            <button class="btn btn-danger btn-sm" onclick="deleteScalpTrade(${t.trade_id})" style="font-size: 11px; padding: 5px 10px;">Del</button>
          </div>
        </td>
      </tr>`;
    });
    tableHtml += '</tbody></table>';
    if (container) {
      container.innerHTML = '<div id="scalp-runs-bulk-bar" class="bulk-bar" style="display:none;"></div>'
        + `<div class="trade-table-scroll">${tableHtml}</div>`
        + `<div class="mobile-data-cards">${_buildScalpRunCards(pageData)}</div>`;
    }
    if (pagEl) {
      const pHtml = _buildPagination(_scalpPage, total, _RUNS_PER_PAGE, '_goScalpPage');
      if (pHtml) { pagEl.innerHTML = pHtml; pagEl.style.display = 'flex'; } else { pagEl.style.display = 'none'; }
    }
    _updateScalpRunsBulkBar();
  } catch(e) {
    console.error('Failed to load scalp trades:', e);
    if (emptyEl) { emptyEl.style.display = 'block'; emptyEl.textContent = 'Error loading scalp trades.'; }
  }
}
function _goScalpPage(p) { _scalpPage = p; _renderFilteredRuns(); }

function _portfolioCompactNames(names, fallback = '—') {
  const unique = [...new Set((names || []).map(name => String(name || '').trim()).filter(Boolean))];
  if (!unique.length) return fallback;
  if (unique.length <= 2) return unique.join(', ');
  return `${unique[0]} +${unique.length - 1}`;
}

function _portfolioRunTs(run) {
  const candidates = [
    run?.last_exit_time,
    run?.stopped_at,
    run?.first_entry_time,
    run?.started_at,
    run?.created_at,
  ];
  return candidates.reduce((best, value) => {
    const parsed = Date.parse(value || '');
    return Number.isFinite(parsed) && parsed > best ? parsed : best;
  }, 0);
}

function _portfolioRunDateLabel(value) {
  const ts = typeof value === 'number' ? value : Date.parse(value || '');
  if (!Number.isFinite(ts) || ts <= 0) return '—';
  const d = new Date(ts);
  return `${String(d.getDate()).padStart(2, '0')}-${String(d.getMonth() + 1).padStart(2, '0')}-${d.getFullYear()}`;
}

function _portfolioRunTimeLabel(value) {
  if (!value) return '—';
  const s = String(value);
  const m = s.match(/(\d{2}:\d{2})(?::\d{2})?/);
  if (m) return m[1];
  const parsed = Date.parse(s);
  if (Number.isFinite(parsed)) {
    const d = new Date(parsed);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }
  return '—';
}

function _portfolioModeClass(mode) {
  return _normalizeMode(mode) === 'live' ? 'live' : 'paper';
}

function _portfolioModeLabel(mode) {
  return _normalizeMode(mode) === 'live' ? 'Live' : 'Paper';
}

function _portfolioRunDateKey(run) {
  const ts = _portfolioRunTs(run);
  if (!ts) return '';
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function _portfolioRunHistoryBucket(run) {
  return [
    _normalizeMode(run?.mode),
    String(run?.run_name || run?.strategy_name || '').trim().toLowerCase(),
    _portfolioRunDateKey(run),
  ].join('|');
}

function _portfolioTradeTs(trade) {
  const exitTs = Date.parse(trade?.exit_time || '');
  if (Number.isFinite(exitTs)) return exitTs;
  const entryTs = Date.parse(trade?.entry_time || '');
  return Number.isFinite(entryTs) ? entryTs : 0;
}

function _portfolioTradeSignature(trade) {
  return [
    String(trade?.symbol || trade?.trading_symbol || '').trim(),
    String(trade?.transaction_type || trade?.side || '').trim(),
    String(trade?.option_type || '').trim(),
    String(trade?.strike ?? '').trim(),
    String(trade?.entry_time || '').trim(),
    String(trade?.exit_time || '').trim(),
    Number(trade?.entry_premium || trade?.entry_price || 0).toFixed(4),
    Number(trade?.exit_premium || trade?.exit_price || 0).toFixed(4),
    String(trade?.quantity || trade?.lots || '').trim(),
    Number(trade?.pnl || 0).toFixed(4),
    String(trade?.exit_reason || trade?.reason || '').trim(),
  ].join('|');
}

function _portfolioTradeDateLabel(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (isNaN(d)) return '—';
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const yyyy = d.getFullYear();
  return `${dd}-${mm}-${yyyy}`;
}

function _portfolioTradeTimeLabel(value) {
  if (!value) return '—';
  const s = String(value);
  const match = s.match(/(\d{2}:\d{2}:\d{2})/);
  return match ? match[1] : s.slice(-8);
}

function _portfolioCompletedTradeRowHtml(trade) {
  const pnl = round2(trade?.pnl || 0);
  return `<tr style="border-bottom:1px solid var(--border);"><td style="padding:8px 12px;font-family:'JetBrains Mono';font-size:11px;color:var(--muted);white-space:nowrap;">${_portfolioTradeDateLabel(trade?.entry_time)}</td><td style="padding:8px 12px;">${escapeHtml(trade?.symbol || trade?.trading_symbol || '—')}</td><td style="padding:8px 12px;font-family:'JetBrains Mono';font-size:11px;color:var(--muted);">${escapeHtml(_portfolioTradeTimeLabel(trade?.entry_time))}</td><td style="padding:8px 12px;font-family:'JetBrains Mono';font-size:11px;color:var(--muted);">${escapeHtml(_portfolioTradeTimeLabel(trade?.exit_time))}</td><td style="padding:8px 12px;text-align:right;color:${trade?.transaction_type === 'BUY' ? 'var(--success)' : 'var(--danger)'}">${escapeHtml(trade?.transaction_type || '—')}</td><td style="padding:8px 12px;text-align:right;font-family:'JetBrains Mono';">₹${round2(trade?.entry_premium || trade?.entry_price || 0).toFixed(2)}</td><td style="padding:8px 12px;text-align:right;font-family:'JetBrains Mono';">₹${round2(trade?.exit_premium || trade?.exit_price || 0).toFixed(2)}</td><td style="padding:8px 12px;text-align:right;">${escapeHtml(trade?.lots || trade?.quantity || '—')}</td><td style="padding:8px 12px;text-align:right;font-family:'JetBrains Mono';color:${pnl >= 0 ? 'var(--success)' : 'var(--danger)'}">₹${pnl.toFixed(2)}</td><td style="padding:8px 12px;text-align:right;font-size:11px;color:var(--muted);">${escapeHtml(trade?.exit_reason || trade?.reason || '—')}</td></tr>`;
}

function _portfolioCompletedTradeCardHtml(trade) {
  const pnl = round2(trade?.pnl || 0);
  const symbol = escapeHtml(trade?.symbol || trade?.trading_symbol || '—');
  const side = escapeHtml(trade?.transaction_type || '—');
  return `<article class="mobile-data-card">
    <div class="mobile-data-card-head">
      <div>
        <div class="mobile-data-card-title">${symbol}</div>
        <div class="mobile-data-card-sub">${escapeHtml(_portfolioTradeDateLabel(trade?.entry_time))} · ${side}</div>
      </div>
      <div class="mobile-data-card-value" style="color:${pnl >= 0 ? 'var(--success)' : 'var(--danger)'}">₹${pnl.toFixed(2)}</div>
    </div>
    <div class="mobile-data-card-grid">
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Entry</span><span class="mobile-data-card-text">${escapeHtml(_portfolioTradeTimeLabel(trade?.entry_time))} · ₹${round2(trade?.entry_premium || trade?.entry_price || 0).toFixed(2)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Exit</span><span class="mobile-data-card-text">${escapeHtml(_portfolioTradeTimeLabel(trade?.exit_time))} · ₹${round2(trade?.exit_premium || trade?.exit_price || 0).toFixed(2)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Qty</span><span class="mobile-data-card-text">${escapeHtml(trade?.lots || trade?.quantity || '—')}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Reason</span><span class="mobile-data-card-text">${escapeHtml(trade?.exit_reason || trade?.reason || '—')}</span></div>
    </div>
  </article>`;
}

function _portfolioMonthlyTradeCardHtml(entry) {
  const isGrossWin = Number(entry?.grossReal || 0) >= 0;
  const isNetWin = Number(entry?.netReal || 0) >= 0;
  const dateLabel = formatPortfolioDateLabel(entry?.dateStr || '');
  return `<article class="mobile-data-card">
    <div class="mobile-data-card-head">
      <div>
        <div class="mobile-data-card-title">${escapeHtml(dateLabel)}</div>
        <div class="mobile-data-card-sub">${escapeHtml(String(entry?.displayTradeCount || 0))} trade${Number(entry?.displayTradeCount || 0) !== 1 ? 's' : ''}</div>
      </div>
      <div class="mobile-data-card-value" style="color:${isGrossWin ? 'var(--success)' : 'var(--danger)'}">₹${Number(entry?.grossReal || 0).toFixed(2)}</div>
    </div>
    <div class="mobile-data-card-grid">
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Net Real</span><span class="mobile-data-card-text" style="color:${isNetWin ? 'var(--success)' : 'var(--danger)'}">₹ ${Number(entry?.netReal || 0).toFixed(2)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Charges</span><span class="mobile-data-card-text">₹ ${Number(entry?.charges || 0).toFixed(2)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Brokerage</span><span class="mobile-data-card-text">₹ ${Number(entry?.brokerage || 0).toFixed(2)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Orders / Fills</span><span class="mobile-data-card-text">${escapeHtml(String(entry?.realOrderCount || entry?.displayTradeCount || 0))} / ${escapeHtml(String(entry?.realTrades || entry?.displayTradeCount || 0))}</span></div>
    </div>
  </article>`;
}

function _renderCompletedTradesBlock(trades, options = {}) {
  const total = Array.isArray(trades) ? trades.length : 0;
  const perPage = Math.max(1, Number(options.perPage || _LIVE_TRADES_PER_PAGE || 10));
  const renderMobileCards = !!options.renderMobileCards;
  const totalPages = Math.max(1, Math.ceil(total / perPage));
  const page = Math.max(1, Math.min(Number(options.page || 1), totalPages));
  const start = (page - 1) * perPage;
  const slice = (trades || []).slice(start, start + perPage);
  const emptyMessage = options.emptyMessage || 'No completed trades yet';
  const tbodyHtml = total
    ? slice.map(_portfolioCompletedTradeRowHtml).join('')
    : `<tr><td colspan="10" style="text-align:center;padding:20px;color:var(--muted);">${escapeHtml(emptyMessage)}</td></tr>`;
  const prevPage = Math.max(1, page - 1);
  const nextPage = Math.min(totalPages, page + 1);
  const pageTemplate = String(options.pageHandlerTemplate || '');
  const pageBarHtml = total > perPage && pageTemplate
    ? `<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 16px;border-top:1px solid var(--border);">
        <span style="font-size:11px;color:var(--muted);font-family:'JetBrains Mono',monospace;">Showing ${start + 1}–${Math.min(start + perPage, total)} of ${total}</span>
        <div style="display:flex;gap:3px;">
          <button class="btn" onclick="${pageTemplate.replace('__PAGE__', String(prevPage))}" style="font-size:11px;padding:5px 10px;" ${page <= 1 ? 'disabled' : ''}>‹</button>
          <span style="font-size:11px;padding:5px 8px;color:var(--muted);">Page ${page} / ${totalPages}</span>
          <button class="btn" onclick="${pageTemplate.replace('__PAGE__', String(nextPage))}" style="font-size:11px;padding:5px 10px;" ${page >= totalPages ? 'disabled' : ''}>›</button>
        </div>
      </div>`
    : '';
  return `<div class="live-panel-closed" style="margin:12px 0 0;background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;">
    <div style="padding:12px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;">
      <span style="font-weight:700;font-size:13px;">Completed Trades</span>
      <span style="font-size:11px;color:var(--muted);font-family:'JetBrains Mono',monospace;">${total} trade${total !== 1 ? 's' : ''}</span>
    </div>
    <div class="trade-table-scroll">
      <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:700px;">
        <thead><tr style="background:var(--card2);"><th style="padding:8px 12px;text-align:left;color:var(--muted);font-size:11px;">Date</th><th style="padding:8px 12px;text-align:left;color:var(--muted);font-size:11px;">Symbol</th><th style="padding:8px 12px;text-align:left;color:var(--muted);font-size:11px;">Entry Time</th><th style="padding:8px 12px;text-align:left;color:var(--muted);font-size:11px;">Exit Time</th><th style="padding:8px 12px;text-align:right;color:var(--muted);font-size:11px;">Type</th><th style="padding:8px 12px;text-align:right;color:var(--muted);font-size:11px;">Entry ₹</th><th style="padding:8px 12px;text-align:right;color:var(--muted);font-size:11px;">Exit ₹</th><th style="padding:8px 12px;text-align:right;color:var(--muted);font-size:11px;">Qty</th><th style="padding:8px 12px;text-align:right;color:var(--muted);font-size:11px;">P&L</th><th style="padding:8px 12px;text-align:right;color:var(--muted);font-size:11px;">Reason</th></tr></thead>
        <tbody>${tbodyHtml}</tbody>
      </table>
    </div>
    ${renderMobileCards ? `<div class="mobile-data-cards">${total ? slice.map(_portfolioCompletedTradeCardHtml).join('') : `<div class="mobile-data-card mobile-data-card-empty">${escapeHtml(emptyMessage)}</div>`}</div>` : ''}
    ${pageBarHtml}
  </div>`;
}

function _portfolioRunHasStrategyConfig(run) {
  return ['entry_conditions', 'exit_conditions', 'legs', 'indicators', 'folder', 'max_trades_per_day']
    .some(key => Array.isArray(run?.[key]) ? run[key].length : !!run?.[key]);
}

function _portfolioBuildActiveRuns() {
  return (_portfolioEngineSnapshotCache || [])
    .filter(engine => engine && engine.running && ['paper', 'live'].includes(_normalizeMode(engine.mode)))
    .map((engine, idx) => {
      const mode = _normalizeMode(engine.mode);
      const closed = Array.isArray(engine.closed_trades) ? engine.closed_trades : [];
      const positions = Array.isArray(engine.positions) ? engine.positions : [];
      const firstTrade = closed[0] || positions[0] || {};
      const lastTrade = closed[closed.length - 1] || {};
      return {
        id: `active:${mode}:${engine.run_id || engine.strategy_name || idx}`,
        _active: true,
        mode,
        run_name: engine.strategy_name || engine.run_id || 'Unnamed Strategy',
        instrument: engine.instrument || engine.current_candle?.symbol || '',
        trade_count: Number(engine.trades_today || closed.length || 0),
        total_pnl: Number(engine.total_pnl || 0),
        first_entry_time:
          firstTrade.entry_time ||
          firstTrade.createTime ||
          engine.started_at ||
          engine.current_time ||
          '',
        last_exit_time: lastTrade.exit_time || '',
        started_at: engine.started_at || '',
        created_at: engine.current_time || engine.started_at || '',
        status: 'running',
        closed_trades: closed,
      };
    });
}

function _portfolioSessionRunCandidates(runs) {
  const historyRuns = (runs || []).filter(r => ['paper', 'live'].includes(_normalizeMode(r.mode)));
  const groupedHistoryRuns = historyRuns.filter(r => _portfolioRunHasStrategyConfig(r) || Number(r.trade_count || 0) > 1);
  const groupedHistoryBuckets = new Set(groupedHistoryRuns.map(_portfolioRunHistoryBucket).filter(Boolean));
  const singleHistoryRuns = historyRuns.filter(r => !(_portfolioRunHasStrategyConfig(r) || Number(r.trade_count || 0) > 1));
  const source = [
    ...groupedHistoryRuns,
    ...singleHistoryRuns.filter(run => !groupedHistoryBuckets.has(_portfolioRunHistoryBucket(run))),
  ];
  const merged = [..._portfolioBuildActiveRuns(), ...source];
  const seen = new Set();
  return merged.filter(run => {
    const mode = _normalizeMode(run.mode);
    const key = [
      mode,
      String(run.run_name || '').trim().toLowerCase(),
      String(run.instrument || '').trim().toLowerCase(),
      Number(run.trade_count || 0),
      Number(run.total_pnl || 0).toFixed(2),
      String(run.first_entry_time || '').slice(0, 19),
      String(run.last_exit_time || '').slice(0, 19),
      run._active ? 'active' : 'history',
    ].join('|');
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function _portfolioGroupedRuns(runs) {
  const groups = new Map();
  _portfolioSessionRunCandidates(runs).forEach(run => {
    const name = String(run.run_name || run.strategy_name || 'Unnamed Strategy').trim() || 'Unnamed Strategy';
    const mode = _normalizeMode(run.mode);
    const key = `${name.toLowerCase()}::${mode}`;
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        name,
        mode,
        sessions: [],
        instruments: new Set(),
        latestTs: 0,
        runningCount: 0,
      });
    }
    const group = groups.get(key);
    group.sessions.push(run);
    const instName = getInstrumentName(run.instrument) || '';
    if (instName) group.instruments.add(instName);
    const ts = _portfolioRunTs(run);
    if (ts > group.latestTs) group.latestTs = ts;
    if (run._active) group.runningCount += 1;
  });

  return Array.from(groups.values())
    .map(group => {
      group.sessions.sort((a, b) => {
        const activeDelta = Number(!!b._active) - Number(!!a._active);
        if (activeDelta) return activeDelta;
        const tsDelta = _portfolioRunTs(b) - _portfolioRunTs(a);
        if (tsDelta) return tsDelta;
        return Number(b.id || 0) - Number(a.id || 0);
      });
      group.display = group.sessions.find(run => run._active) || group.sessions[0] || null;
      group.latest = group.display;
      group.instrumentSummary = _portfolioCompactNames(Array.from(group.instruments), 'Mixed');
      group.displayPnl = round2(Number(group.display?.total_pnl || 0));
      const displayClosedTrades = Array.isArray(group.display?.closed_trades) ? group.display.closed_trades : [];
      group.displayTrades = displayClosedTrades.length || Number(group.display?.trade_count || 0);
      group.latestTs = _portfolioRunTs(group.display) || group.latestTs;
      return group;
    })
    .sort((a, b) => {
      const runningDelta = b.runningCount - a.runningCount;
      if (runningDelta) return runningDelta;
      return b.latestTs - a.latestTs;
    });
}

async function _ensurePortfolioRunTradesLoaded(groupKey, force = false) {
  const key = String(groupKey || '');
  if (!key) return;
  if (!force && (_portfolioRunTradeCache[key] || _portfolioRunTradeLoading[key])) return;
  const group = _portfolioGroupedRuns(_allRunsCache).find(item => item.key === key);
  if (!group) return;
  _portfolioRunTradeLoading[key] = true;
  delete _portfolioRunTradeErrors[key];
  if (force) delete _portfolioRunTradeCache[key];
  _renderPortfolioPaperRuns(_allRunsCache);
  try {
    const historyRunIds = [...new Set(group.sessions
      .filter(run => !run._active && Number.isFinite(Number(run.id)) && Number(run.id) > 0)
      .map(run => Number(run.id)))];
    const detailRuns = await Promise.all(historyRunIds.map(async (id) => {
      const res = await fetch('/api/runs/' + id);
      if (await handleUnauthorizedResponse(res)) return null;
      if (!res.ok) throw new Error(`Failed to load run ${id}`);
      return res.json();
    }));
    const historyTrades = detailRuns
      .filter(Boolean)
      .flatMap(run => Array.isArray(run.trades) ? run.trades : []);
    const seen = new Set();
    const deduped = historyTrades.filter(trade => {
      const signature = _portfolioTradeSignature(trade);
      if (seen.has(signature)) return false;
      seen.add(signature);
      return true;
    }).sort((a, b) => _portfolioTradeTs(b) - _portfolioTradeTs(a));
    _portfolioRunTradeCache[key] = deduped;
  } catch (error) {
    _portfolioRunTradeErrors[key] = error?.message || 'Failed to load completed trades.';
  } finally {
    delete _portfolioRunTradeLoading[key];
    _renderPortfolioPaperRuns(_allRunsCache);
  }
}

function _portfolioRunTradesForGroup(group) {
  const activeTrades = group.sessions
    .filter(run => run._active)
    .flatMap(run => Array.isArray(run.closed_trades) ? run.closed_trades : []);
  const historyTrades = Array.isArray(_portfolioRunTradeCache[group.key]) ? _portfolioRunTradeCache[group.key] : [];
  const seen = new Set();
  return [...activeTrades, ...historyTrades]
    .filter(trade => {
      const signature = _portfolioTradeSignature(trade);
      if (seen.has(signature)) return false;
      seen.add(signature);
      return true;
    })
    .sort((a, b) => _portfolioTradeTs(b) - _portfolioTradeTs(a));
}

async function _togglePortfolioRunGroup(encodedKey) {
  const key = decodeURIComponent(String(encodedKey || ''));
  if (_portfolioRunExpandedKey === key) {
    _portfolioRunExpandedKey = '';
    _renderPortfolioPaperRuns(_allRunsCache);
    return;
  }
  _portfolioRunExpandedKey = key;
  if (!_portfolioRunTradePages[key]) _portfolioRunTradePages[key] = 1;
  _renderPortfolioPaperRuns(_allRunsCache);
  await _ensurePortfolioRunTradesLoaded(key, true);
}

function _goPortfolioRunSessionPage(encodedKey, page) {
  const key = decodeURIComponent(String(encodedKey || ''));
  _portfolioRunSessionPages[key] = Math.max(1, Number(page || 1));
  _renderPortfolioPaperRuns(_allRunsCache);
}

function _goPortfolioRunTradePage(encodedKey, page) {
  const key = decodeURIComponent(String(encodedKey || ''));
  const trades = _portfolioRunTradeCache[key] || [];
  const totalPages = Math.max(1, Math.ceil(trades.length / _LIVE_TRADES_PER_PAGE));
  _portfolioRunTradePages[key] = Math.max(1, Math.min(Number(page || 1), totalPages));
  _renderPortfolioPaperRuns(_allRunsCache);
}

function _renderPortfolioPaperRuns(runs) {
  const container = document.getElementById('portfolio-paper-runs');
  const empty = document.getElementById('portfolio-paper-runs-empty');
  const pagEl = document.getElementById('portfolio-paper-pagination');
  const groupedRuns = _portfolioGroupedRuns(runs);

  if (!container) return;
  if (!groupedRuns.length) {
    if (empty) empty.style.display = 'block';
    container.innerHTML = '';
    if (pagEl) { pagEl.style.display = 'none'; }
    return;
  }
  if (empty) empty.style.display = 'none';

  const total = groupedRuns.length;
  const totalPages = Math.ceil(total / _RUNS_PER_PAGE);
  if (_portfolioPaperPage > totalPages) _portfolioPaperPage = totalPages;
  const start = (_portfolioPaperPage - 1) * _RUNS_PER_PAGE;
  const pageData = groupedRuns.slice(start, start + _RUNS_PER_PAGE);

  let html = '';
  pageData.forEach(group => {
    const expanded = _portfolioRunExpandedKey === group.key;
    const encodedKey = encodeURIComponent(group.key);
    const pnlClass = group.displayPnl >= 0 ? 'pos' : 'neg';
    const expandedTrades = _portfolioRunTradesForGroup(group);
    const tradePage = Math.max(1, Number(_portfolioRunTradePages[group.key] || 1));
    const loadingTrades = !!_portfolioRunTradeLoading[group.key];
    const tradeError = _portfolioRunTradeErrors[group.key] || '';
    const completedTradesHtml = tradeError
        ? `<div class="live-panel-closed" style="margin:12px 0 0;background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;">
            <div style="padding:16px;text-align:center;color:var(--danger);">${escapeHtml(tradeError)}</div>
          </div>`
        : (expandedTrades.length || !loadingTrades)
          ? _renderCompletedTradesBlock(expandedTrades, {
            page: tradePage,
            perPage: _LIVE_TRADES_PER_PAGE,
            pageHandlerTemplate: `_goPortfolioRunTradePage('${escapeAttr(encodedKey)}', __PAGE__)`,
          })
          : `<div class="live-panel-closed" style="margin:12px 0 0;background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;">
              <div style="padding:16px;text-align:center;color:var(--muted);">Loading completed trades...</div>
            </div>`;
    html += `<article class="portfolio-run-group${expanded ? ' open' : ''}">
      <button class="portfolio-run-group-head" type="button" onclick="_togglePortfolioRunGroup('${escapeAttr(encodedKey)}')">
        <div class="portfolio-run-group-main">
          <div class="portfolio-run-group-title-row">
            <span class="portfolio-run-group-title">${escapeHtml(group.name)}</span>
            ${group.runningCount ? `<span class="portfolio-run-status-pill running">${group.runningCount} Running</span>` : ''}
          </div>
          <div class="portfolio-run-group-sub">
            <span>${escapeHtml(group.instrumentSummary)}</span>
            <span>${group.displayTrades} trade${group.displayTrades !== 1 ? 's' : ''}</span>
            <span>Latest ${_portfolioRunDateLabel(group.latestTs)}</span>
          </div>
        </div>
        <div class="portfolio-run-group-side">
          <span class="portfolio-run-mode-pill ${_portfolioModeClass(group.mode)}">${_portfolioModeLabel(group.mode)}</span>
          <span class="portfolio-run-group-pnl ${pnlClass}">${fmt(group.displayPnl)}</span>
          <span class="portfolio-run-chevron">${expanded ? '▲' : '▼'}</span>
        </div>
      </button>
      <div class="portfolio-run-group-body">
        ${completedTradesHtml}
      </div>
    </article>`;
  });
  container.innerHTML = html;
  if (pagEl) {
    const pHtml = _buildPagination(_portfolioPaperPage, total, _RUNS_PER_PAGE, '_goPortfolioPaperPage');
    if (pHtml) { pagEl.innerHTML = pHtml; pagEl.style.display = 'flex'; } else { pagEl.style.display = 'none'; }
  }
}
function _goPortfolioPaperPage(p) { _portfolioPaperPage = p; _renderPortfolioPaperRuns(_allRunsCache); }

async function fetchRuns() {
  try {
    const res = await fetch('/api/runs');
    if (await handleUnauthorizedResponse(res)) return;
    if (!res.ok) throw new Error('Failed to load runs');
    const runs = await res.json();
    _allRunsCache = runs.slice().reverse();
    _portfolioRunTradeErrors = Object.create(null);

    // Dashboard: paginated
    _renderDashRuns();

    // Results page: apply current filter (paginated)
    _renderFilteredRuns();

    // Portfolio page: show grouped paper/live sessions
    _renderPortfolioPaperRuns(_allRunsCache);
    if (_portfolioRunExpandedKey) _ensurePortfolioRunTradesLoaded(_portfolioRunExpandedKey, true);
  } catch(e) { console.error('fetchRuns error:', e); }
}

let currentViewingRunId = null;

async function viewRunModal(id) {
  try {
    const res = await fetch('/api/runs/' + id);
    const data = await res.json();
    lastBacktestData = data;
    currentViewingRunId = id;
    lastBacktestPayload = data;
    viewRunDetails(data);
  } catch(e) { toast('Error loading run', 'danger'); }
}

async function viewRun(id, options = {}) {
  try {
    const res = await fetch('/api/runs/' + id);
    const data = await res.json();
    lastBacktestData = data;
    currentViewingRunId = id;
    lastBacktestPayload = data;
    renderResults(data, data);
    const historyState = Object.assign({}, options.historyState || {}, { runId: id });
    showPage(
      'results-page',
      document.getElementById('nav-results'),
      Object.assign({}, options, { historyState, scrollToTop: true })
    );
  } catch(e) { toast('Error loading run', 'danger'); }
}

async function deleteRun(id) {
  const ok = await customConfirm('Are you sure you want to delete this run?', { title: 'Delete Run', icon: ICO.trash(28), okText: 'Delete', danger: true });
  if (!ok) return;
  await fetch('/api/runs/' + id, { method: 'DELETE' });
  toast('Run deleted', 'success');
  fetchRuns();
}

function viewScalpTrade(tid) {
  fetch('/api/scalp/trades').then(r => r.json()).then(trades => {
    const t = trades.find(x => x.trade_id === tid);
    if (!t) { toast('Trade not found', 'danger'); return; }
    const pnl = (t.pnl || 0).toFixed(2);
    const pnlColor = t.pnl >= 0 ? 'var(--success)' : 'var(--danger)';
    const row = (label, val) => `<div style="padding:8px 0;border-bottom:1px solid var(--border);"><span style="color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:0.5px;">${escapeHtml(label)}</span><div style="font-weight:600;margin-top:2px;">${escapeHtml(val)}</div></div>`;
    const html = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 16px;font-size:13px;">
      ${row('Symbol', t.symbol || '—')}
      ${row('Type', `${t.transaction_type || ''} ${t.option_type || ''}`)}
      ${row('Strike', t.strike || '—')}
      ${row('Expiry', t.expiry || '—')}
      ${row('Entry Price', `₹${(t.entry_premium||0).toFixed(2)}`)}
      ${row('Exit Price', `₹${(t.exit_premium||0).toFixed(2)}`)}
      ${row('Entry Time', t.entry_time || '—')}
      ${row('Exit Time', t.exit_time || '—')}
      ${row('Lots', `${t.lots||1} × ${t.lot_size||1} = ${t.quantity||''}`)}
      ${row('Exit Reason', t.exit_reason || '—')}
      ${row('Mode', t.mode || 'scalp')}
      <div style="padding:8px 0;border-bottom:1px solid var(--border);"><span style="color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:0.5px;">P&amp;L</span><div style="font-weight:700;margin-top:2px;font-family:'JetBrains Mono',monospace;font-size:16px;color:${pnlColor};">₹${pnl}</div></div>
    </div>`;
    document.getElementById('view-modal-title').textContent = 'Scalp Trade S' + tid;
    document.getElementById('view-modal-content').innerHTML = html;
    document.getElementById('view-modal-load-btn').style.display = 'none';
    document.getElementById('view-strategy-modal').classList.add('open');
  }).catch(() => toast('Error loading trade', 'danger'));
}

async function deleteScalpTrade(tid) {
  const ok = await customConfirm('Delete this scalp trade?', { title: 'Delete Scalp Trade', icon: ICO.trash(28), okText: 'Delete', danger: true });
  if (!ok) return;
  await fetch('/api/scalp/trades/' + tid, { method: 'DELETE' });
  toast('Scalp trade deleted', 'success');
  _renderFilteredRuns();
}

// ══════════════════════════════════════════════════════════════
//  STOCK TERMINAL PAGE
// ══════════════════════════════════════════════════════════════
let _stockTerminalInitialized = false;
let _stockTerminalStocks = [];
let _stockTerminalSelected = null;
let _stockTerminalQuoteTimer = null;
let _stockTerminalOrdersTimer = null;
let _stockTerminalOrderWatchTimer = null;
let _stockTerminalQuoteTick = 0;
let _stockTerminalOrdersTick = 0;
let _stockTerminalOrderInFlight = false;
let _stockTerminalLastLtp = 0;
let _stockTerminalValueListenersAttached = false;
let _terminalCascadePollTimer = null;
let _lastTerminalCascadeStatus = null;
let _terminalCascadeInputsBound = false;
const _terminalCascadeOpenSymbols = new Set();
let _terminalCascadeChartTimeframe = 'auto';
let _terminalCascadeChartPayload = null;
let _terminalCascadeChartContext = null;
const _STOCK_TERMINAL_KEY = 'philforge_stock_terminal_symbol_v1';
const _TERMINAL_CASCADE_CAPITAL_KEY = 'philforge_terminal_cascade_capital_v1';

async function initStockTerminalPage(force = false) {
  bindStockTerminalValueInputs();
  bindTerminalCascadeInputs();
  toggleStockOrderMode();
  toggleStockOrderFields();
  if (force || !_stockTerminalInitialized || !_stockTerminalStocks.length) {
    _stockTerminalInitialized = await loadStockTerminalStocks();
  } else {
    renderStockTerminalList();
  }
  if (_stockTerminalSelected) refreshStockTerminalQuote(true);
  updateTerminalCascadeReference();
  refreshTerminalCascadeStatus();
  refreshTerminalClosedCampaigns();
  refreshStockTerminalOrders();
  if (!_terminalCascadePollTimer) {
    _terminalCascadePollTimer = setInterval(() => {
      if (!_isPageVisible() || !_isPageActive('stock-terminal-page')) return;
      refreshTerminalCascadeStatus();
    }, _ws && _ws.readyState === 1 ? 10000 : 4000);
  }
  if (!_stockTerminalQuoteTimer) {
    _stockTerminalQuoteTimer = setInterval(() => {
      if (!_isPageVisible() || !_isPageActive('stock-terminal-page')) return;
      // Off-session the LTP cannot move; one refresh every 2 minutes keeps
      // the number honest without spending Dhan's rate limit all night.
      if (!_nseSessionLikelyOpen() && (_stockTerminalQuoteTick++ % 24 !== 0)) return;
      refreshStockTerminalQuote(false);
    }, 5000);
  }
  if (!_stockTerminalOrdersTimer) {
    _stockTerminalOrdersTimer = setInterval(() => {
      if (!_isPageVisible() || !_isPageActive('stock-terminal-page')) return;
      // Off-session the order book cannot change on a fresh action, and the
      // Dhan rate budget is account-wide — the same trap behind the 4AM 429
      // storm. GTT/AMO can still move outside cash hours, so don't stop, just
      // slow to one refresh every ~10 minutes (every 30th 20s tick).
      if (!_nseSessionLikelyOpen() && (_stockTerminalOrdersTick++ % 30 !== 0)) return;
      refreshStockTerminalOrders();
    }, 20000);
  }
}

function _setStockTerminalStatus(text, tone) {
  const el = document.getElementById('stock-terminal-status');
  if (!el) return;
  el.textContent = text;
  el.style.color = tone === 'ok' ? 'var(--success)' : tone === 'error' ? 'var(--danger)' : 'var(--muted)';
}

function bindStockTerminalValueInputs() {
  if (_stockTerminalValueListenersAttached) return;
  _stockTerminalValueListenersAttached = true;
  ['stock-quantity', 'stock-price', 'stock-trigger-price', 'stock-gtt-price1', 'stock-gtt-trigger1'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', updateStockOrderValue);
  });
  ['stock-order-mode', 'stock-order-type'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', updateStockOrderValue);
  });
}

function _stockTerminalPriceContext() {
  const mode = document.getElementById('stock-order-mode')?.value || 'regular';
  const orderType = document.getElementById('stock-order-type')?.value || 'MARKET';
  const price = parseFloat(document.getElementById('stock-price')?.value) || 0;
  const trigger = parseFloat(document.getElementById('stock-trigger-price')?.value) || 0;
  if (mode === 'gtt' && orderType === 'LIMIT' && price > 0) return { price, source: 'Limit' };
  if (mode === 'gtt' && trigger > 0) return { price: trigger, source: 'Trigger' };
  if (['LIMIT', 'STOP_LOSS'].includes(orderType) && price > 0) return { price, source: orderType === 'LIMIT' ? 'Limit' : 'SL Price' };
  if (orderType === 'STOP_LOSS_MARKET' && trigger > 0) return { price: trigger, source: 'Trigger' };
  return { price: _stockTerminalLastLtp || 0, source: 'LTP' };
}

function updateStockOrderValue() {
  const unitEl = document.getElementById('stock-unit-price');
  const sourceEl = document.getElementById('stock-unit-price-source');
  const valueEl = document.getElementById('stock-order-value');
  if (!unitEl && !valueEl) return;
  const qty = parseInt(document.getElementById('stock-quantity')?.value, 10) || 0;
  const ctx = _stockTerminalPriceContext();
  const unitPrice = Number(ctx.price || 0);
  const hasPrice = unitPrice > 0;
  if (unitEl) unitEl.textContent = hasPrice ? '₹' + unitPrice.toFixed(2) : '—';
  if (sourceEl) sourceEl.textContent = ctx.source || 'LTP';
  if (valueEl) {
    valueEl.textContent = hasPrice && qty > 0 ? '₹' + (unitPrice * qty).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
    valueEl.style.color = hasPrice && qty > 0 ? 'var(--text)' : 'var(--muted)';
  }
}

async function loadStockTerminalStocks() {
  _setStockTerminalStatus('Loading', '');
  try {
    const res = await fetch('/api/terminal/nifty200', { cache: 'no-store' });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(_apiErrorMessage(data, 'Failed to load stocks'));
    _stockTerminalStocks = Array.isArray(data.data) ? data.data : [];
    const countEl = document.getElementById('stock-terminal-count');
    if (countEl) countEl.textContent = String(_stockTerminalStocks.length);
    const saved = _getLocalState(_STOCK_TERMINAL_KEY);
    const savedStock = _stockTerminalStocks.find(s => s.symbol === saved);
    selectStockTerminal(savedStock?.symbol || _stockTerminalStocks.find(s => s.tradable)?.symbol || _stockTerminalStocks[0]?.symbol || '', { skipQuote: true });
    _setStockTerminalStatus('Ready', 'ok');
    return true;
  } catch (e) {
    _setStockTerminalStatus('Load failed', 'error');
    const body = document.getElementById('stock-terminal-body');
    if (body) body.innerHTML = `<tr><td colspan="3" style="text-align:center;padding:20px;color:var(--danger);">${escapeHtml(e.message || 'Load failed')}</td></tr>`;
    return false;
  }
}

function renderStockTerminalList() {
  const body = document.getElementById('stock-terminal-body');
  if (!body) return;
  const q = String(document.getElementById('stock-terminal-search')?.value || '').trim().toUpperCase();
  const rows = _stockTerminalStocks.filter(s => {
    if (!q) return true;
    return String(s.symbol || '').includes(q) || String(s.name || '').toUpperCase().includes(q);
  });
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="3" style="text-align:center;padding:20px;color:var(--muted);">No matches</td></tr>';
    return;
  }
  body.innerHTML = rows.map(s => {
    const active = _stockTerminalSelected && _stockTerminalSelected.symbol === s.symbol;
    const tradable = !!s.tradable;
    const bg = active ? 'background:rgba(34,197,94,0.10);' : '';
    const status = tradable
      ? '<span style="color:var(--success);font-weight:700;" title="Tradable — Dhan security ID resolved">OK</span>'
      : '<span style="color:var(--warn);font-weight:700;" title="No Dhan security ID — quote and orders unavailable">NO ID</span>';
    return `<tr onclick="selectStockTerminal('${escapeJsAttr(s.symbol)}')" style="border-bottom:1px solid rgba(255,255,255,0.03);cursor:pointer;${bg}">
      <td style="padding:8px 10px;font-family:'JetBrains Mono',monospace;font-weight:700;color:${active ? 'var(--success)' : 'var(--text)'};">${escapeHtml(s.symbol || '')}</td>
      <td style="padding:8px 10px;color:var(--muted);">${escapeHtml(s.name || '')}</td>
      <td style="padding:8px 10px;text-align:center;">${status}</td>
    </tr>`;
  }).join('');
}

function selectStockTerminal(symbol, options = {}) {
  const stock = _stockTerminalStocks.find(s => s.symbol === symbol);
  if (!stock) return;
  _stockTerminalSelected = stock;
  const symInput = document.getElementById('stock-terminal-symbol');
  if (symInput) symInput.value = stock.symbol;
  const chip = document.getElementById('stock-terminal-selected-chip');
  if (chip) chip.textContent = stock.symbol;
  const sec = document.getElementById('stock-terminal-security');
  if (sec) sec.textContent = stock.security_id ? `sec ${stock.security_id}` : 'sec missing';
  const ltp = document.getElementById('stock-terminal-ltp');
  if (ltp) {
    ltp.textContent = '—';
    ltp.style.color = 'var(--muted)';
  }
  _stockTerminalLastLtp = 0;
  updateStockOrderValue();
  updateTerminalCascadeReference();
  _setLocalState(_STOCK_TERMINAL_KEY, stock.symbol);
  renderStockTerminalList();
  if (!options.skipQuote) refreshStockTerminalQuote(true);
}

async function refreshStockTerminalQuote(force) {
  if (!_stockTerminalSelected) return;
  const ltpEl = document.getElementById('stock-terminal-ltp');
  if (force && ltpEl) {
    ltpEl.textContent = '…';
    ltpEl.style.color = 'var(--muted)';
  }
  try {
    const res = await fetch('/api/terminal/quote?symbol=' + encodeURIComponent(_stockTerminalSelected.symbol), { cache: 'no-store' });
    const data = await res.json();
    if (data.stock) {
      _stockTerminalSelected = data.stock;
      const sec = document.getElementById('stock-terminal-security');
      if (sec) sec.textContent = data.stock.security_id ? `sec ${data.stock.security_id}` : 'sec missing';
    }
    if (data.status === 'ok' && Number(data.ltp) > 0) {
      _stockTerminalLastLtp = Number(data.ltp);
      if (ltpEl) {
        ltpEl.textContent = '₹' + _stockTerminalLastLtp.toFixed(2);
        ltpEl.style.color = '#4ade80';
      }
      updateStockOrderValue();
      // Open Positions marks the selected scrip at this tick, so repaint it on
      // the quote rather than waiting for the slower status poll to come round.
      if (_lastTerminalCascadeStatus?.campaigns) {
        try { _renderTerminalOpenPositions(_lastTerminalCascadeStatus.campaigns); } catch (err) { /* panel optional */ }
      }
    } else if (ltpEl && force) {
      ltpEl.textContent = data.message ? 'N/A' : '—';
      ltpEl.style.color = 'var(--muted)';
      _stockTerminalLastLtp = 0;
      updateStockOrderValue();
    }
  } catch (e) {
    if (ltpEl && force) {
      ltpEl.textContent = 'N/A';
      ltpEl.style.color = 'var(--muted)';
    }
    if (force) {
      _stockTerminalLastLtp = 0;
      updateStockOrderValue();
    }
  }
}

function _terminalCascadeEl(id) { return document.getElementById(id); }

function bindTerminalCascadeInputs() {
  const chartIcons = {
    'terminal-cascade-refresh-icon': ICO.refresh(15),
    'terminal-cascade-expand-icon': ICO.target(15),
    'terminal-cascade-close-icon': ICO.cross(15),
  };
  Object.entries(chartIcons).forEach(([id, icon]) => {
    const element = _terminalCascadeEl(id);
    if (element) element.innerHTML = icon;
  });
  if (_terminalCascadeInputsBound) return;
  _terminalCascadeInputsBound = true;
  const capital = _terminalCascadeEl('terminal-cascade-capital');
  if (capital) {
    const saved = _getLocalState(_TERMINAL_CASCADE_CAPITAL_KEY);
    if (saved && Number(saved) > 0) capital.value = saved;
    capital.addEventListener('input', () => _setLocalState(_TERMINAL_CASCADE_CAPITAL_KEY, capital.value || ''));
  }
}

function _terminalCascadeMoney(value) {
  const n = Number(value);
  return Number.isFinite(n) ? '₹' + n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
}

function _terminalCascadeSetStatus(message, tone = 'muted') {
  const el = _terminalCascadeEl('terminal-cascade-form-status');
  if (!el) return;
  el.textContent = message || '';
  el.style.color = ({ muted: 'var(--muted)', busy: '#fde68a', error: 'var(--danger)', success: '#6ee7b7' }[tone] || 'var(--muted)');
}

function updateTerminalCascadeReference() {
  const el = _terminalCascadeEl('terminal-cascade-reference');
  if (!el) return;
  const stock = _stockTerminalSelected;
  if (!stock) {
    el.textContent = 'Select a symbol';
    return;
  }
  const ref = stock.cascade_reference || {};
  const refSymbol = ref.symbol || stock.symbol;
  const refName = ref.name || stock.name || refSymbol;
  el.textContent = ref.mode === 'reference_index'
    ? `${refSymbol} signal -> ${stock.symbol} trade/TP`
    : `${stock.symbol} own chart -> own trade/TP`;
  el.title = refName;
}

function _terminalCascadeMetric(label, value, accent = 'var(--text)') {
  return `<div class="terminal-cascade-metric"><span>${escapeHtml(label)}</span><strong style="color:${accent};">${escapeHtml(value)}</strong></div>`;
}

function setTerminalCascadeScripOpen(symbol, isOpen) {
  const key = String(symbol || '');
  if (!key) return;
  if (isOpen) _terminalCascadeOpenSymbols.add(key);
  else _terminalCascadeOpenSymbols.delete(key);
}

function _terminalCascadeStatusColor(status) {
  return ({ PENDING: 'var(--muted)', COLLECTED: '#fde68a', FILLED: '#6ee7b7', CLOSED: '#a78bfa', CANCELLED: '#fca5a5' }[String(status || '').toUpperCase()] || 'var(--muted)');
}

function _renderTerminalCascadeStatus(payload) {
  _lastTerminalCascadeStatus = payload || null;
  const campaigns = Array.isArray(payload?.campaigns)
    ? payload.campaigns
    : (payload?.campaign ? [payload.campaign] : []);
  const gate = payload?.live_gate || {};
  const gateEl = _terminalCascadeEl('terminal-cascade-live-gate');
  if (gateEl) {
    gateEl.textContent = gate.enabled ? 'LIVE GATE' : 'LIVE LOCKED';
    gateEl.title = gate.reason || '';
    gateEl.classList.toggle('terminal-cascade-pill-live', !!gate.enabled);
  }
  const badge = _terminalCascadeEl('terminal-cascade-badge');
  const start = _terminalCascadeEl('terminal-cascade-start');
  const stop = _terminalCascadeEl('terminal-cascade-stop');
  const del = _terminalCascadeEl('terminal-cascade-delete');
  const chart = _terminalCascadeEl('terminal-cascade-chart-btn');
  const controlSummary = _terminalCascadeEl('terminal-cascade-control-summary');
  const scripSubtitle = _terminalCascadeEl('terminal-cascade-scrip-subtitle');
  const flow = _terminalCascadeEl('terminal-cascade-scrip-flow');
  const runningCampaigns = campaigns.filter(campaign => campaign && campaign.running);
  if (!campaigns.length) {
    const selectedSymbol = _stockTerminalSelected?.symbol || 'Selected scrip';
    const referenceSymbol = _stockTerminalSelected?.cascade_reference?.symbol || '';
    if (badge) { badge.textContent = 'IDLE'; badge.style.color = 'var(--muted)'; badge.style.borderColor = 'var(--border)'; }
    if (start) start.disabled = false;
    if (stop) stop.style.display = 'none';
    if (del) del.style.display = 'none';
    if (chart) chart.disabled = false;
    if (controlSummary) controlSummary.textContent = 'Mother, capital, timeframe and product';
    if (scripSubtitle) {
      scripSubtitle.textContent = referenceSymbol && referenceSymbol !== selectedSymbol
        ? `${referenceSymbol} signal -> ${selectedSymbol} trade/TP`
        : 'Select a symbol to load its Cascade window';
    }
    if (flow) flow.innerHTML = _terminalCascadeEmptyWindow(selectedSymbol, referenceSymbol);
    _renderTerminalOpenPositions([]);
    _renderTerminalRoundsLedger();
    _renderTerminalEventFeed([]);
    return;
  }
  if (badge) {
    badge.textContent = `${runningCampaigns.length} ACTIVE / ${campaigns.length}`;
    badge.style.color = runningCampaigns.length ? '#6ee7b7' : 'var(--warn)';
    badge.style.borderColor = runningCampaigns.length ? '#6ee7b7' : 'var(--warn)';
  }
  if (start) start.disabled = false;
  if (stop) stop.style.display = 'none';
  if (del) del.style.display = 'none';
  if (chart) chart.disabled = false;
  if (controlSummary) {
    controlSummary.textContent = `${campaigns.length} scrip${campaigns.length === 1 ? '' : 's'} · paper only`;
  }
  const motherInput = _terminalCascadeEl('terminal-cascade-mother-timestamp');
  if (motherInput && !motherInput.value && campaigns[0]?.mother?.signal?.timestamp) {
    motherInput.value = String(campaigns[0].mother.signal.timestamp).slice(0, 16);
  }
  if (scripSubtitle) scripSubtitle.textContent = `${runningCampaigns.length} active paper campaign${runningCampaigns.length === 1 ? '' : 's'}`;
  if (flow) flow.innerHTML = campaigns.map(_terminalCascadeWindow).join('');
  _renderTerminalOpenPositions(campaigns);
  _renderTerminalRoundsLedger();
  _renderTerminalEventFeed(campaigns);
}

function _terminalCascadeEmptyWindow(symbol, reference) {
  const subtitle = reference && reference !== symbol ? `${reference} signal -> ${symbol} trade/TP` : 'Choose a scrip, then start a paper campaign.';
  const open = _terminalCascadeOpenSymbols.has(String(symbol)) ? ' open' : '';
  return `<details class="terminal-cascade-scrip-window" data-terminal-cascade-symbol="${escapeAttr(symbol)}" ontoggle="setTerminalCascadeScripOpen('${escapeJsAttr(symbol)}', this.open)"${open}><summary class="terminal-cascade-scrip-window-head"><div><span>${escapeHtml(symbol)}</span><strong>${escapeHtml(subtitle)}</strong></div></summary><div class="terminal-cascade-scrip-window-body"><div class="terminal-cascade-empty">No paper campaign for this scrip.</div></div></details>`;
}

// One sentence for "so why is nothing happening?". A campaign can sit at
// WAITING for an hour for half a dozen different reasons — no rung reached yet,
// cash pooled but no second red close, a stop armed and not yet crossed, a pot
// too small to buy one share, or a closed round that may only re-arm under the
// previous low. The engine knows which; the card never said.
function _terminalCampaignWaitingFor(campaign) {
  const status = String(campaign.status || '').toUpperCase();
  const money = _terminalCascadeMoney;
  const price = _cascadeNumber;
  const pot = (Number(campaign.pending_inr) || 0) + (Number(campaign.cash_carry_inr) || 0);
  const last = Number(campaign.last_trade_close) || 0;

  if (status === 'MOTHER_BROKEN') return 'Mother broken — no new legs can form. Start a new mother to continue.';
  if (status === 'MOTHER_RETESTED') return 'Mother retested — the geometry is finished. Start a new mother to continue.';
  if (status === 'KILLED') return 'Killed — the basket was closed by hand and nothing manages this scrip now.';
  if (status === 'STOPPED') return 'Stopped — nothing is watching this scrip. Any holding is unmanaged.';

  if ((Number(campaign.open_quantity) || 0) > 0) {
    const target = Number(campaign.target_price) || 0;
    if (target && last) {
      const away = ((target - last) / last) * 100;
      return `Holding ${campaign.open_quantity} share${campaign.open_quantity === 1 ? '' : 's'} — sells itself at ${price(target)}, ${away.toFixed(2)}% above the last mark.`;
    }
    return `Holding ${campaign.open_quantity} share${campaign.open_quantity === 1 ? '' : 's'} — waiting for the target.`;
  }

  const stop = Number(campaign.pending_stop) || 0;
  if (stop > 0) return `${money(pot)} pooled — buys when price trades back above ${price(stop)}.`;

  if (status === 'AWAITING_CASH') {
    return `${money(pot)} pooled, still short of one share near ${price(last)} — it carries forward until a deeper rung adds to it.`;
  }
  if (pot > 0) return `${money(pot)} pooled — waiting for two red closes to set the buy-stop.`;

  const reuse = Number(campaign.reuse_below) || 0;
  if (reuse > 0) return `Round closed. The ladder only re-arms once price prints a new low under ${price(reuse)}.`;

  const pending = (campaign.rungs || [])
    .filter(row => String(row.status || '').toUpperCase() === 'PENDING' && Number(row.signal_price) > 0)
    .sort((a, b) => Number(b.signal_price) - Number(a.signal_price));
  if (pending.length) {
    const nearest = pending[0];
    const away = last ? ((last - Number(nearest.signal_price)) / last) * 100 : 0;
    return `Watching for the fall to reach ${price(nearest.signal_price)}${last ? ` — ${away.toFixed(2)}% below the last mark` : ''}.`;
  }
  return 'Watching for a fresh leg to form off the mother.';
}

function _terminalCascadeWindow(campaign) {
  const inst = campaign.instrument || {};
  const config = campaign.config || {};
  const symbol = String(inst.symbol || 'Scrip');
  const signal = String(inst.signal_symbol || symbol);
  const state = String(campaign.status || 'waiting').replaceAll('_', ' ').toUpperCase();
  const mother = String(campaign?.mother?.signal?.timestamp || '');
  const timeframe = String(config.timeframe || '5m');
  const cardClass = campaign.running ? ' is-active' : '';
  const open = _terminalCascadeOpenSymbols.has(symbol) ? ' open' : '';
  const rounds = Array.isArray(campaign.rounds) ? campaign.rounds : [];
  const fills = Array.isArray(campaign.open_fills) ? campaign.open_fills : [];
  const realised = rounds.reduce((sum, row) => sum + (Number(row.net_pnl) || 0), 0);
  const mode = String(campaign.mode || 'paper').toUpperCase();
  const ended = ['STOPPED', 'KILLED', 'MOTHER_BROKEN', 'MOTHER_RETESTED'].includes(String(campaign.status || '').toUpperCase());
  const halted = Boolean(campaign.halted);
  // A collapsed card still has to say how the campaign is doing, otherwise the
  // list is a row of names and you must open every one to find the live trade.
  let gist = fills.length
    ? `${fills.length} open fill${fills.length === 1 ? '' : 's'}`
    : (rounds.length ? `${rounds.length} round${rounds.length === 1 ? '' : 's'}` : 'no entry yet');
  if (rounds.length) gist += ` · ${realised >= 0 ? '+' : ''}${_terminalCascadeMoney(realised)}`;

  const pill = (text, tone) => `<span class="pf-campaign-pill"${tone ? ` data-state="${escapeAttr(tone)}"` : ''}>${escapeHtml(text)}</span>`;
  const stat = (label, value, note) =>
    `<div class="pf-campaign-stat"><div class="pf-campaign-stat-label">${escapeHtml(label)}</div>` +
    `<div class="pf-campaign-stat-value">${escapeHtml(value)}</div>` +
    (note ? `<div class="pf-campaign-stat-note">${escapeHtml(note)}</div>` : '') + '</div>';

  const stats = [
    stat('Mother high', _cascadeNumber(campaign?.mother?.trade?.high)),
    stat('Avg entry', _cascadeNumber(campaign.average_entry_price)),
    stat('Take profit', _cascadeNumber(campaign.target_price), 'a quarter back to the mother high'),
    stat('Quantity', String(campaign.open_quantity || 0), `${escapeHtml(symbol)} shares held`),
    stat('In position', _terminalCascadeMoney(campaign.open_invested_inr || 0), `of ${_terminalCascadeMoney(config.capital_inr || 0)} capital`),
    stat('Waiting to buy', _terminalCascadeMoney(campaign.pending_inr || 0), `${_terminalCascadeMoney(campaign.cash_carry_inr || 0)} carried`),
    stat('Rounds closed', String(rounds.length), `realised ${_terminalCascadeMoney(realised)}`),
  ].join('');

  const button = (label, handler, kind) =>
    `<button class="btn btn-sm ${kind || 'btn-outline'}" onclick="event.preventDefault();event.stopPropagation();${handler}">${escapeHtml(label)}</button>`;

  return `<details class="terminal-cascade-scrip-window pf-campaign-card${cardClass}${ended ? ' is-ended' : ''}" data-terminal-cascade-symbol="${escapeAttr(symbol)}" ontoggle="setTerminalCascadeScripOpen('${escapeJsAttr(symbol)}', this.open)"${open}>
    <summary class="pf-campaign-head">
      <div class="pf-campaign-title">
        <strong>${escapeHtml(symbol)}</strong>
        ${pill(state, campaign.running ? 'ok' : 'warn')}
        ${pill(mode, mode === 'LIVE' ? 'warn' : 'ok')}
        ${pill(timeframe)}
        ${inst.reference_mode === 'reference_index' ? pill(`${signal} signal`, 'info') : ''}
        ${halted ? pill('HALTED', 'danger') : ''}
        <span class="pf-campaign-gist" title="${escapeAttr(_terminalCampaignWaitingFor(campaign))}">${escapeHtml(gist)}</span>
      </div>
      <div class="pf-campaign-actions">
        ${button('Chart', `loadTerminalCascadeChart('${escapeJsAttr(symbol)}','${escapeJsAttr(mother)}','${escapeJsAttr(timeframe)}')`)}
        ${campaign.running ? button('Stop', `stopTerminalCascadePaper('${escapeJsAttr(symbol)}')`) : ''}
        ${ended ? button('New mother', `terminalCascadeNewMother('${escapeJsAttr(symbol)}')`) : ''}
        ${button('Delete', `deleteTerminalCascadePaper('${escapeJsAttr(symbol)}')`, 'btn-danger')}
      </div>
    </summary>
    <div class="terminal-cascade-scrip-window-body">
      <div class="pf-campaign-waiting">${escapeHtml(_terminalCampaignWaitingFor(campaign))}</div>
      <div class="pf-campaign-stats">${stats}</div>
    <div class="terminal-cascade-ladder-panel"><div class="terminal-cascade-section-head"><div><span>Ladder and order flow</span><strong>${(campaign.rungs || []).length} fib rungs</strong></div></div>${_terminalCascadeRungsMarkup(campaign.rungs || [])}</div>
    <div class="terminal-cascade-bottom-grid">
      <section class="terminal-cascade-log-panel"><div class="terminal-cascade-section-head"><div><span>Open fills</span><strong>paper basket</strong></div></div><div class="terminal-cascade-log-body">${_terminalCascadeFillsMarkup(campaign.open_fills || [])}</div></section>
      <section class="terminal-cascade-log-panel"><div class="terminal-cascade-section-head"><div><span>Events</span><strong>latest first</strong></div></div><div class="terminal-cascade-log-body">${_terminalCascadeEventsMarkup(campaign.events || [])}</div></section>
      <section class="terminal-cascade-log-panel"><div class="terminal-cascade-section-head"><div><span>Rounds</span><strong>closed paper trades</strong></div></div><div class="terminal-cascade-log-body">${_terminalCascadeRoundsMarkup(campaign.rounds || [])}</div></section>
    </div></div></details>`;
}

function _terminalCascadeRungsMarkup(rungs) {
  if (!Array.isArray(rungs) || !rungs.length) return '<div class="terminal-cascade-empty">No fib rungs yet.</div>';
  const rows = rungs.map(rung => {
    const status = String(rung.status || 'PENDING').toUpperCase();
    const color = _terminalCascadeStatusColor(status);
    const allocation = Number(rung.allocation_pct || 0);
    return `<tr class="terminal-cascade-rung-row is-${escapeAttr(status.toLowerCase())}"><td><strong>F${escapeHtml(rung.leg_id)} L${escapeHtml(rung.level)}</strong><small>${Number.isFinite(allocation) ? allocation.toFixed(2) : '0.00'}% allocation</small></td><td class="num">${escapeHtml(_cascadeNumber(rung.signal_price))}</td><td class="num">${escapeHtml(_terminalCascadeMoney(rung.pool_inr || 0))}<small>pool</small></td><td class="num">${escapeHtml(_terminalCascadeMoney(rung.budget_inr || 0))}</td><td><span class="terminal-cascade-status" style="color:${color};border-color:${color};">${escapeHtml(status)}</span></td></tr>`;
  }).join('');
  return `<div class="terminal-cascade-table-scroll"><table class="terminal-cascade-ladder-table"><thead><tr><th>Level</th><th class="num">Signal price</th><th class="num">Pool</th><th class="num">Amount</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function _terminalCascadeFillsMarkup(fills) {
  if (!Array.isArray(fills) || !fills.length) return '<div class="terminal-cascade-empty">No open fills.</div>';
  return fills.slice().reverse().map(fill => `<div class="terminal-cascade-log-row"><span>${escapeHtml(_cascadeOptionsTimestamp(fill.timestamp))}</span><strong style="color:#6ee7b7;">${escapeHtml(String(fill.quantity))} @ ${escapeHtml(_cascadeNumber(fill.trade_price))}</strong><em>${escapeHtml(_terminalCascadeMoney(fill.spent_inr || fill.budget_inr || 0))}</em></div>`).join('');
}

function _terminalCascadeRoundsMarkup(rounds) {
  if (!Array.isArray(rounds) || !rounds.length) return '<div class="terminal-cascade-empty">No completed round.</div>';
  return rounds.slice(-8).reverse().map(row => {
    const pnl = Number(row.net_pnl || 0);
    const color = pnl > 0 ? '#6ee7b7' : pnl < 0 ? '#fca5a5' : 'var(--muted)';
    return `<div class="terminal-cascade-log-row"><span>#${escapeHtml(row.round_id)} ${escapeHtml(String(row.exit_reason || '').replaceAll('_', ' '))}</span><strong style="color:${color};">${escapeHtml(_terminalCascadeMoney(row.net_pnl))}</strong><em>${escapeHtml(String(row.exit_quantity || 0))} qty</em></div>`;
  }).join('');
}

function _terminalCascadeEventsMarkup(events) {
  if (!Array.isArray(events) || !events.length) return '<div class="terminal-cascade-empty">No events.</div>';
  return events.slice(-12).reverse().map(event => {
    const bits = [];
    if (event.rung) bits.push(event.rung);
    if (event.quantity) bits.push(`${event.quantity} qty`);
    if (event.trade_price) bits.push(_cascadeNumber(event.trade_price));
    if (event.target_price) bits.push(`TP ${_cascadeNumber(event.target_price)}`);
    return `<div class="terminal-cascade-log-row"><span>${escapeHtml(_cascadeOptionsTimestamp(event.timestamp))}</span><strong>${escapeHtml(String(event.event || '').replaceAll('_', ' '))}</strong><em>${bits.length ? escapeHtml(bits.join(' · ')) : ''}</em></div>`;
  }).join('');
}

function _renderTerminalCascadeRungs(rungs) {
  const el = _terminalCascadeEl('terminal-cascade-rungs');
  const count = _terminalCascadeEl('terminal-cascade-rung-count');
  if (!el) return;
  if (count) count.textContent = Array.isArray(rungs) && rungs.length ? `${rungs.length} rungs` : 'No rungs';
  if (!Array.isArray(rungs) || !rungs.length) {
    el.innerHTML = '<div class="terminal-cascade-empty">No fib rungs yet.</div>';
    return;
  }
  const rows = rungs.map(rung => {
    const status = String(rung.status || 'PENDING').toUpperCase();
    const color = _terminalCascadeStatusColor(status);
    const allocation = Number(rung.allocation_pct || 0);
    return `<tr class="terminal-cascade-rung-row is-${escapeAttr(status.toLowerCase())}">
      <td><strong>F${escapeHtml(rung.leg_id)} L${escapeHtml(rung.level)}</strong><small>${Number.isFinite(allocation) ? allocation.toFixed(2) : '0.00'}% allocation</small></td>
      <td class="num">${escapeHtml(_cascadeNumber(rung.signal_price))}</td>
      <td class="num">${escapeHtml(_terminalCascadeMoney(rung.pool_inr || 0))}<small>pool</small></td>
      <td class="num">${escapeHtml(_terminalCascadeMoney(rung.budget_inr || 0))}</td>
      <td><span class="terminal-cascade-status" style="color:${color};border-color:${color};">${escapeHtml(status)}</span></td>
    </tr>`;
  }).join('');
  el.innerHTML = `<div class="terminal-cascade-table-scroll"><table class="terminal-cascade-ladder-table">
    <thead><tr><th>Level</th><th class="num">Signal price</th><th class="num">Pool</th><th class="num">Amount</th><th>Status</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function _renderTerminalCascadeFills(fills) {
  const el = _terminalCascadeEl('terminal-cascade-fills');
  if (!el) return;
  if (!Array.isArray(fills) || !fills.length) {
    el.innerHTML = '<div class="terminal-cascade-empty">No open fills.</div>';
    return;
  }
  el.innerHTML = fills.slice().reverse().map(fill => `<div class="terminal-cascade-log-row"><span>${escapeHtml(_cascadeOptionsTimestamp(fill.timestamp))}</span><strong style="color:#6ee7b7;">${escapeHtml(String(fill.quantity))} @ ${escapeHtml(_cascadeNumber(fill.trade_price))}</strong><em>${escapeHtml(_terminalCascadeMoney(fill.spent_inr || fill.budget_inr || 0))}</em></div>`).join('');
}

function _renderTerminalCascadeRounds(rounds) {
  const el = _terminalCascadeEl('terminal-cascade-rounds');
  if (!el) return;
  if (!Array.isArray(rounds) || !rounds.length) {
    el.innerHTML = '<div class="terminal-cascade-empty">No completed round.</div>';
    return;
  }
  el.innerHTML = rounds.slice(-8).reverse().map(row => {
    const pnl = Number(row.net_pnl || 0);
    const color = pnl > 0 ? '#6ee7b7' : pnl < 0 ? '#fca5a5' : 'var(--muted)';
    return `<div class="terminal-cascade-log-row"><span>#${escapeHtml(row.round_id)} ${escapeHtml(String(row.exit_reason || '').replaceAll('_', ' '))}</span><strong style="color:${color};">${escapeHtml(_terminalCascadeMoney(row.net_pnl))}</strong><em>${escapeHtml(String(row.exit_quantity || 0))} qty</em></div>`;
  }).join('');
}

function _renderTerminalCascadeEvents(events) {
  const el = _terminalCascadeEl('terminal-cascade-events');
  if (!el) return;
  if (!Array.isArray(events) || !events.length) {
    el.innerHTML = '<div class="terminal-cascade-empty">No events.</div>';
    return;
  }
  el.innerHTML = events.slice(-30).reverse().map(event => {
    const bits = [];
    if (event.rung) bits.push(event.rung);
    if (event.quantity) bits.push(`${event.quantity} qty`);
    if (event.trade_price) bits.push(_cascadeNumber(event.trade_price));
    if (event.target_price) bits.push(`TP ${_cascadeNumber(event.target_price)}`);
    return `<div class="terminal-cascade-log-row"><span>${escapeHtml(_cascadeOptionsTimestamp(event.timestamp))}</span><strong>${escapeHtml(String(event.event || '').replaceAll('_', ' '))}</strong><em>${bits.length ? escapeHtml(bits.join(' · ')) : ''}</em></div>`;
  }).join('');
}

async function refreshTerminalCascadeStatus() {
  try {
    const res = await fetch('/api/terminal/cascade/status', { credentials: 'same-origin', cache: 'no-store' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data?.detail || 'Unable to load Terminal Cascade');
    _renderTerminalCascadeStatus(data);
    _terminalCascadePollChartRefresh();
  } catch (error) {
    _terminalCascadeSetStatus(error.message || 'Unable to load Terminal Cascade.', 'error');
  }
}

// True while the NSE cash session (with a small margin) could be printing
// candles. Off-hours, Dhan-backed polling is pure rate-limit spend: nothing
// it returns can change until 09:15.
function _nseSessionLikelyOpen() {
  const now = new Date();
  const ist = new Date(now.getTime() + (330 + now.getTimezoneOffset()) * 60000);
  const day = ist.getDay();
  if (day === 0 || day === 6) return false;
  const minutes = ist.getHours() * 60 + ist.getMinutes();
  return minutes >= 550 && minutes <= 935; // 09:10-15:35 IST
}

// An open chart follows the campaign it shows: while the session is open it
// re-fetches at most once a minute (the chart endpoint replays the campaign
// from Dhan, so each refresh is real API spend). One request in flight is
// enough — a slow chart must not race a newer poll.
let _terminalCascadeChartPollInFlight = false;
let _terminalCascadeChartPollLast = 0;
async function _terminalCascadePollChartRefresh() {
  const overlayEl = _terminalCascadeEl('terminal-cascade-chart-overlay');
  const context = _terminalCascadeChartContext;
  if (!overlayEl?.classList.contains('is-open') || !context?.symbol || !context?.timestamp) return;
  if (_terminalCascadeChartPollInFlight) return;
  if (!_nseSessionLikelyOpen()) return;
  if (Date.now() - _terminalCascadeChartPollLast < 60000) return;
  _terminalCascadeChartPollLast = Date.now();
  _terminalCascadeChartPollInFlight = true;
  try {
    const timeframe = _terminalCascadeCurrentChartTimeframe();
    const url = `/api/terminal/cascade/chart?symbol=${encodeURIComponent(context.symbol)}&mother_timestamp=${encodeURIComponent(context.timestamp)}&timeframe=${encodeURIComponent(timeframe)}`;
    const res = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status !== 'ok') return;
    if (!overlayEl.classList.contains('is-open')) return;
    const keep = _terminalCascadeCanvasViewSnapshot();
    _terminalCascadeChartPayload = data;
    const body = _terminalCascadeEl('terminal-cascade-chart-body');
    if (body) {
      body.innerHTML = _terminalCascadeChartHtml(data);
      _terminalCascadeMountCanvas(data, keep);
    }
  } catch (error) {
    // A missed refresh is fine; the next poll tries again.
  } finally {
    _terminalCascadeChartPollInFlight = false;
  }
}

async function startTerminalCascadePaper() {
  const symbol = _stockTerminalSelected?.symbol || document.getElementById('stock-terminal-symbol')?.value || '';
  const timestamp = _terminalCascadeEl('terminal-cascade-mother-timestamp')?.value || '';
  const capital = Number(_terminalCascadeEl('terminal-cascade-capital')?.value || 0);
  const targetPct = Number(_terminalCascadeEl('terminal-cascade-target-pct')?.value || 25);
  if (!symbol || !timestamp || !Number.isFinite(capital) || capital <= 0 || !Number.isFinite(targetPct) || targetPct <= 0) {
    _terminalCascadeSetStatus('Select a symbol, timestamp, capital and TP fraction.', 'error');
    return;
  }
  const timeframe = _terminalCascadeEl('terminal-cascade-timeframe')?.value || '5m';
  const payload = {
    symbol,
    mother_timestamp: timeframe === '1d' ? `${timestamp.slice(0, 10)}T09:15` : timestamp,
    capital_inr: capital,
    timeframe,
    target_fraction: targetPct / 100,
    product_type: _terminalCascadeEl('terminal-cascade-product')?.value || 'CNC',
  };
  const ref = _stockTerminalSelected?.cascade_reference || {};
  const ok = await customConfirm(
    `Start Terminal Cascade paper for <strong>${escapeHtml(symbol)}</strong>?<br><span style="font-size:12px;color:var(--muted);">Signal: ${escapeHtml(ref.symbol || symbol)} · Capital: ${escapeHtml(_terminalCascadeMoney(capital))} · No Dhan order is sent.</span>`,
    { title: 'Start Cash Cascade', icon: ICO.chart(28), okText: 'Start Paper' }
  );
  if (!ok) return;
  const button = _terminalCascadeEl('terminal-cascade-start');
  if (button) button.disabled = true;
  _terminalCascadeSetStatus('Starting paper campaign...', 'busy');
  try {
    const res = await fetch('/api/terminal/cascade/start', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status !== 'started') throw new Error(data?.detail || 'Terminal Cascade did not start');
    _terminalCascadeSetStatus('Terminal Cascade paper campaign started.', 'success');
    await refreshTerminalCascadeStatus();
    loadTerminalCascadeChart(symbol, timestamp, payload.timeframe).catch(() => {});
  } catch (error) {
    _terminalCascadeSetStatus(error.message || 'Terminal Cascade start failed.', 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

async function stopTerminalCascadePaper(symbol) {
  const target = symbol || _stockTerminalSelected?.symbol || '';
  if (!target) return;
  const res = await fetch(`/api/terminal/cascade/stop?symbol=${encodeURIComponent(target)}`, { method: 'POST', credentials: 'same-origin' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { _terminalCascadeSetStatus(data?.detail || 'Stop failed.', 'error'); return; }
  _terminalCascadeSetStatus('Terminal Cascade monitoring stopped.', 'success');
  refreshTerminalCascadeStatus();
}

async function deleteTerminalCascadePaper(symbol) {
  const target = symbol || _stockTerminalSelected?.symbol || '';
  if (!target) return;
  const ok = await customConfirm('Delete this Terminal Cascade paper campaign from the Terminal view? No Dhan order is sent.', { title: 'Delete Cash Cascade', icon: ICO.warn(28), okText: 'Delete', danger: true });
  if (!ok) return;
  const res = await fetch(`/api/terminal/cascade?symbol=${encodeURIComponent(target)}`, { method: 'DELETE', credentials: 'same-origin' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status !== 'deleted') { _terminalCascadeSetStatus(data?.detail || 'Delete failed.', 'error'); return; }
  _terminalCascadeSetStatus('Terminal Cascade paper campaign deleted — its record moved to Closed Campaigns.', 'success');
  refreshTerminalCascadeStatus();
  refreshTerminalClosedCampaigns();
}

async function killTerminalCascadePaper(symbol) {
  const target = symbol || _stockTerminalSelected?.symbol || '';
  if (!target) return;
  const ok = await customConfirm('Kill this Terminal Cascade paper campaign and close any paper basket at current quote? No Dhan order is sent.', { title: 'Kill Cash Cascade', icon: ICO.warn(28), okText: 'Kill Paper', danger: true });
  if (!ok) return;
  const res = await fetch(`/api/terminal/cascade/kill?symbol=${encodeURIComponent(target)}`, { method: 'POST', credentials: 'same-origin' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status !== 'killed') { _terminalCascadeSetStatus(data?.detail || 'Kill failed.', 'error'); return; }
  _terminalCascadeSetStatus('Terminal Cascade campaign killed.', 'success');
  refreshTerminalCascadeStatus();
}

// ── Open positions, closed-rounds ledger, closed campaigns ─────────────
// The CryptoForge Cascade page's three summary surfaces, ported to the cash
// Terminal: they read the same per-campaign status payload the cards render.
let _terminalLedgerScrip = 'ALL';
let _terminalLedgerPage = 0;
const _TERMINAL_LEDGER_PAGE_SIZE = 8;
let _terminalClosedCampaigns = [];

function _terminalRoundAvgEntry(round) {
  const fills = Array.isArray(round?.fills) ? round.fills : [];
  let qty = 0, cost = 0;
  fills.forEach(fill => { const q = Number(fill.quantity) || 0; qty += q; cost += q * (Number(fill.trade_price) || 0); });
  return qty > 0 ? cost / qty : 0;
}

function _terminalRoundInvested(round) {
  const fills = Array.isArray(round?.fills) ? round.fills : [];
  return fills.reduce((sum, fill) => sum + (Number(fill.spent_inr) || 0), 0);
}

function _terminalHeldFor(openedAt, closedAt) {
  const from = Date.parse(openedAt || ''), to = Date.parse(closedAt || '');
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) return '—';
  const mins = Math.round((to - from) / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours}h ${mins % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

function _renderTerminalOpenPositions(campaigns) {
  const body = document.getElementById('terminal-open-positions-body');
  const meta = document.getElementById('terminal-open-positions-meta');
  if (!body) return;
  const open = (campaigns || []).filter(campaign => (Number(campaign?.open_quantity) || 0) > 0 && (campaign.open_fills || []).length);
  if (!open.length) {
    body.innerHTML = '<div class="terminal-cascade-empty">No open positions — nothing has been bought yet.</div>';
    if (meta) meta.textContent = 'Every campaign holding shares right now, what it cost, and what it is worth.';
    return;
  }
  let totalCost = 0, totalNow = 0;
  const rows = open.map(campaign => {
    const inst = campaign.instrument || {};
    const symbol = String(inst.symbol || '—');
    const qty = Number(campaign.open_quantity) || 0;
    const avg = Number(campaign.average_entry_price) || 0;
    // The engine marks a basket at the last CLOSED candle, which can be minutes
    // stale. The header already polls a real LTP, but only for the symbol on
    // screen — quoting every open scrip would spend the account-wide Dhan
    // budget. So use the live tick where we genuinely have one, and say which
    // number each row is showing rather than passing a candle close off as live.
    const quoted = (_stockTerminalSelected?.symbol === symbol && Number(_stockTerminalLastLtp) > 0)
      ? Number(_stockTerminalLastLtp)
      : 0;
    const last = quoted || Number(campaign.last_trade_close) || 0;
    const liveMark = quoted ? '<small style="color:#6ee7b7;">live</small>' : '<small>at candle close</small>';
    const cost = Number(campaign.open_invested_inr) || avg * qty;
    const now = last ? last * qty : cost;
    totalCost += cost; totalNow += now;
    const pnl = last ? now - cost : 0;
    const pct = cost > 0 && last ? (pnl / cost) * 100 : 0;
    const tone = pnl >= 0 ? '#6ee7b7' : '#fca5a5';
    const fills = campaign.open_fills || [];
    const opened = fills.length ? fills[0].timestamp : '';
    const tp = Number(campaign.target_price) || 0;
    const toTp = tp && last ? ((tp - last) / last) * 100 : null;
    const running = !!campaign.running;
    const mother = String(campaign?.mother?.signal?.timestamp || '');
    const timeframe = String(campaign?.config?.timeframe || '5m');
    return `<tr>
      <td><strong>${escapeHtml(symbol)}</strong><small>${escapeHtml(String(campaign.status || '').replaceAll('_', ' '))}${running ? '' : ' · stopped — nothing manages this basket'}</small></td>
      <td>${escapeHtml(_cascadeOptionsTimestamp(opened))}<small>${fills.length} buy${fills.length === 1 ? '' : 's'}</small></td>
      <td class="num">${escapeHtml(_cascadeNumber(avg))}<small>${qty} qty</small></td>
      <td class="num">${last ? escapeHtml(_cascadeNumber(last)) : '—'}${last ? liveMark : ''}</td>
      <td class="num">${escapeHtml(_terminalCascadeMoney(cost))}</td>
      <td class="num" style="color:${tone};font-weight:700;">${last ? `${pnl >= 0 ? '+' : '−'}${escapeHtml(_terminalCascadeMoney(Math.abs(pnl)))}` : '—'}<small style="color:${tone};">${last ? `${pnl >= 0 ? '+' : '−'}${Math.abs(pct).toFixed(2)}%` : ''}</small></td>
      <td class="num">${tp ? escapeHtml(_cascadeNumber(tp)) : '—'}${toTp !== null ? `<small>${toTp.toFixed(2)}% away</small>` : ''}</td>
      <td><button class="btn btn-sm btn-outline" onclick="loadTerminalCascadeChart('${escapeJsAttr(symbol)}','${escapeJsAttr(mother)}','${escapeJsAttr(timeframe)}')">Chart</button> <button class="btn btn-sm btn-danger" onclick="killTerminalCascadePaper('${escapeJsAttr(symbol)}')">Kill</button></td>
    </tr>`;
  }).join('');
  body.innerHTML = `<div class="terminal-cascade-table-scroll"><table class="terminal-cascade-ladder-table"><thead><tr><th>Scrip</th><th>Opened</th><th class="num">Avg entry</th><th class="num">Last</th><th class="num">Invested</th><th class="num">Unrealised</th><th class="num">Target</th><th>Action</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  if (meta) {
    const totalPnl = totalNow - totalCost;
    const tone = totalPnl >= 0 ? '#6ee7b7' : '#fca5a5';
    meta.innerHTML = `${open.length} open · ${escapeHtml(_terminalCascadeMoney(totalCost))} invested · <strong style="color:${tone};">${totalPnl >= 0 ? '+' : '−'}${escapeHtml(_terminalCascadeMoney(Math.abs(totalPnl)))}</strong> unrealised. The selected scrip marks at its live tick; the rest mark at their last candle close. Each basket sells itself at its target.`;
  }
}

// Every event the cash engine emits, said in words. The feed used to print a
// generic five-field guess, so a `rung_created` read as a bare "6:2" and
// `stop_armed` / `await_second_red` / `new_low_restart` said nothing at all —
// the engine had the price, the budget and the trigger in the payload the
// whole time. Each case below names what actually happened and why.
function _terminalEventDetail(event) {
  const price = value => (Number.isFinite(Number(value)) ? _cascadeNumber(value) : '—');
  const money = value => (Number.isFinite(Number(value)) ? _terminalCascadeMoney(value) : '—');
  const pct = value => (Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)}%` : '');
  const bits = [];
  switch (String(event.event || '')) {
    case 'rung_created':
      bits.push(`rung ${event.rung}`, `buys at ${price(event.signal_price)}`, `budget ${money(event.budget_inr)}`);
      if (pct(event.allocation_pct)) bits.push(`${pct(event.allocation_pct)} of the fall`);
      break;
    case 'rung_collected':
      bits.push(`rung ${event.rung}`, `price hit ${price(event.signal_price)}`,
        `pot ${money(event.pending_inr)}${Number(event.carry_inr) ? ` + ${money(event.carry_inr)} carry` : ''}`,
        `still under the ${money(event.minimum_inr)} minimum`);
      break;
    case 'cash_placeable':
      bits.push(`rung ${event.rung}`, `line ${price(event.signal_price)}`,
        `pot ${money(event.pending_inr)}${Number(event.carry_inr) ? ` + ${money(event.carry_inr)} carry` : ''}`,
        `can buy near ${price(event.estimated_trade_price)}`);
      break;
    case 'await_second_red':
      bits.push(`first red close ${price(event.close)} under the ${price(event.line)} line`, 'waiting for a second');
      break;
    case 'stop_armed':
      bits.push(`buy-stop set at ${price(event.trigger)}`, 'fills when price trades back above it');
      break;
    case 'stop_moved':
      bits.push(`buy-stop lowered to ${price(event.trigger)}`, 'the fall kept going');
      break;
    case 'cash_below_one_share':
      bits.push(`pot ${money(event.pending_inr)}${Number(event.carry_inr) ? ` + ${money(event.carry_inr)} carry` : ''}`,
        `cannot buy one share at ${price(event.trade_price)}`, 'carried forward');
      break;
    case 'paper_cash_fill':
      bits.push(`bought ${event.quantity} @ ${price(event.trade_price)}`, `spent ${money(event.spent_inr)}`);
      if (Number.isFinite(Number(event.target_price))) bits.push(`target ${price(event.target_price)}`);
      if (Number(event.carry_inr)) bits.push(`${money(event.carry_inr)} carried`);
      break;
    case 'new_low_restart':
      bits.push(`new low ${price(event.low)}`,
        `${(event.rungs || []).length} rung${(event.rungs || []).length === 1 ? '' : 's'} armed again`);
      break;
    case 'round_closed':
      bits.push(`sold at ${price(event.exit_price)}`, `${String(event.reason || '').replaceAll('_', ' ')}`,
        `gross ${money(event.gross_pnl)}`, `costs ${money(event.costs)}`, `net ${money(event.net_pnl)}`);
      break;
    case 'campaign_killed':
      bits.push(`${(event.cancelled_rungs || []).length} pending rung${(event.cancelled_rungs || []).length === 1 ? '' : 's'} cancelled`);
      break;
    case 'campaign_ended':
      bits.push(`${String(event.reason || '').replaceAll('_', ' ')} — no new legs can form`);
      break;
    default:
      if (event.rung) bits.push(`rung ${event.rung}`);
      if (event.quantity) bits.push(`${event.quantity} qty`);
      if (event.trade_price) bits.push(price(event.trade_price));
      if (event.net_pnl !== undefined) bits.push(`net ${money(event.net_pnl)}`);
  }
  return bits.filter(Boolean).join(' · ');
}

let _terminalEventsScrip = 'ALL';

function terminalEventsSetFilter(name) {
  _terminalEventsScrip = String(name || 'ALL');
  const body = document.getElementById('terminal-events-body');
  // A different scrip is a different list, so start it at the top rather than
  // wherever the previous one happened to be scrolled to.
  if (body) { body.dataset.pfFeedKey = ''; body.scrollTop = 0; }
  _renderTerminalEventFeed(_lastTerminalCascadeStatus?.campaigns || []);
}

function _renderTerminalEventFeed(campaigns) {
  const body = document.getElementById('terminal-events-body');
  if (!body) return;
  const meta = document.getElementById('terminal-events-meta');
  const all = [];
  (campaigns || []).forEach(campaign => {
    const symbol = String(campaign?.instrument?.symbol || '—');
    (campaign.events || []).forEach(event => all.push({ symbol, event }));
  });

  // One shared 60-row cap across every campaign meant a busy scrip silently
  // pushed a quieter one's history off the end. Filtering by scrip gives each
  // campaign the whole budget when you actually need to read it.
  const scrips = [...new Set(all.map(row => row.symbol))].sort();
  if (_terminalEventsScrip !== 'ALL' && !scrips.includes(_terminalEventsScrip)) _terminalEventsScrip = 'ALL';
  const filters = document.getElementById('terminal-events-filters');
  if (filters) {
    filters.innerHTML = scrips.length > 1
      ? ['ALL', ...scrips].map(name => {
        const on = name === _terminalEventsScrip;
        return `<button type="button" class="terminal-cascade-tf-option${on ? ' is-active' : ''}" role="radio" aria-checked="${on}" onclick="terminalEventsSetFilter('${escapeJsAttr(name)}')">${escapeHtml(name === 'ALL' ? 'All' : name)}</button>`;
      }).join('')
      : '';
  }

  const rows = _terminalEventsScrip === 'ALL' ? all : all.filter(row => row.symbol === _terminalEventsScrip);
  rows.sort((a, b) => (Date.parse(b.event?.timestamp || 0) || 0) - (Date.parse(a.event?.timestamp || 0) || 0));
  const shown = rows.slice(0, 60);
  if (!shown.length) {
    body.innerHTML = '<div class="terminal-cascade-empty">No events yet.</div>';
    if (meta) meta.textContent = 'Every campaign’s events in one feed, latest first.';
    return;
  }
  const markup = shown.map(row => {
    const event = row.event || {};
    const detail = _terminalEventDetail(event);
    return `<div class="terminal-cascade-log-row"><span>${escapeHtml(_cascadeOptionsTimestamp(event.timestamp))} · ${escapeHtml(row.symbol)}</span><strong>${escapeHtml(String(event.event || '').replaceAll('_', ' '))}</strong><em>${escapeHtml(detail)}</em></div>`;
  }).join('');
  // The status poll repaints this panel every few seconds. Replacing innerHTML
  // wholesale resets scrollTop, so reading anything below the fold was a fight
  // against the clock. Skip the write when nothing changed, and restore the
  // scroll position when it did.
  if (body.dataset.pfFeedKey === markup) return;
  body.dataset.pfFeedKey = markup;
  const keepTop = body.scrollTop;
  body.innerHTML = markup;
  body.scrollTop = keepTop;
  if (meta) {
    const scope = _terminalEventsScrip === 'ALL'
      ? `across ${(campaigns || []).length} campaign${(campaigns || []).length === 1 ? '' : 's'}`
      : `for ${_terminalEventsScrip}`;
    const truncated = rows.length > shown.length
      ? ` — showing the latest ${shown.length}${_terminalEventsScrip === 'ALL' ? ', filter by scrip to see more of one' : ''}.`
      : ' — all of them.';
    meta.textContent = `${rows.length} event${rows.length === 1 ? '' : 's'} ${scope}${truncated}`;
  }
}

function terminalCascadeNewMother(symbol) {
  // A campaign that ended MOTHER_BROKEN/RETESTED continues by hand here:
  // Phil picks the next mother candle himself.
  try { if (typeof selectStockTerminal === 'function') selectStockTerminal(symbol); } catch (err) { /* header optional */ }
  const input = _terminalCascadeEl('terminal-cascade-mother-timestamp');
  if (input) input.value = '';
  document.getElementById('terminal-cascade-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  _terminalCascadeSetStatus(`${symbol} selected — pick the new mother candle and start the next campaign.`, 'busy');
}

function _terminalLedgerRows() {
  const live = _lastTerminalCascadeStatus?.campaigns || [];
  const rows = [];
  live.forEach(campaign => {
    const symbol = String(campaign?.instrument?.symbol || '—');
    (campaign.rounds || []).forEach(round => rows.push({ symbol, round, source: 'live', sourceKey: symbol, ended: !campaign.running }));
  });
  _terminalClosedCampaigns.forEach(archive => {
    const symbol = String(archive?.instrument?.symbol || '—');
    (archive.rounds || []).forEach(round => rows.push({ symbol, round, source: 'archive', sourceKey: String(archive.archive_id || ''), ended: true }));
  });
  rows.sort((a, b) => (Date.parse(b.round?.closed_at || 0) || 0) - (Date.parse(a.round?.closed_at || 0) || 0));
  return rows;
}

function terminalLedgerSetFilter(symbol) {
  _terminalLedgerScrip = symbol || 'ALL';
  _terminalLedgerPage = 0;
  _renderTerminalRoundsLedger();
}

function terminalLedgerPageMove(delta) {
  _terminalLedgerPage += delta;
  _renderTerminalRoundsLedger();
}

function _renderTerminalRoundsLedger() {
  const body = document.getElementById('terminal-rounds-ledger-body');
  if (!body) return;
  const meta = document.getElementById('terminal-rounds-ledger-meta');
  const filters = document.getElementById('terminal-rounds-ledger-filters');
  const pager = document.getElementById('terminal-rounds-ledger-pager');
  const all = _terminalLedgerRows();
  if (filters) {
    const scrips = [];
    all.forEach(row => { if (row.symbol && !scrips.includes(row.symbol)) scrips.push(row.symbol); });
    scrips.sort();
    if (scrips.length < 2) {
      filters.innerHTML = '';
      if (_terminalLedgerScrip !== 'ALL') _terminalLedgerScrip = 'ALL';
    } else {
      filters.innerHTML = ['ALL'].concat(scrips).map(name => {
        const on = name === _terminalLedgerScrip;
        return `<button type="button" class="terminal-cascade-tf-option${on ? ' is-active' : ''}" role="radio" aria-checked="${on}" onclick="terminalLedgerSetFilter('${escapeJsAttr(name)}')">${escapeHtml(name === 'ALL' ? 'All' : name)}</button>`;
      }).join('');
    }
  }
  const rows = _terminalLedgerScrip === 'ALL' ? all : all.filter(row => row.symbol === _terminalLedgerScrip);
  if (!rows.length) {
    body.innerHTML = `<div class="terminal-cascade-empty">${all.length ? 'No closed rounds for this scrip.' : 'No closed rounds yet.'}</div>`;
    if (pager) pager.innerHTML = '';
    if (meta) meta.textContent = 'Every round that reached its target, across every campaign — running and ended alike. Nothing has closed yet.';
    return;
  }
  let invested = 0, realised = 0, wins = 0;
  rows.forEach(row => {
    invested += _terminalRoundInvested(row.round);
    const pnl = Number(row.round?.net_pnl) || 0;
    realised += pnl;
    if (pnl > 0) wins++;
  });
  const pages = Math.max(1, Math.ceil(rows.length / _TERMINAL_LEDGER_PAGE_SIZE));
  if (_terminalLedgerPage >= pages) _terminalLedgerPage = pages - 1;
  if (_terminalLedgerPage < 0) _terminalLedgerPage = 0;
  const from = _terminalLedgerPage * _TERMINAL_LEDGER_PAGE_SIZE;
  const html = rows.slice(from, from + _TERMINAL_LEDGER_PAGE_SIZE).map(row => {
    const round = row.round || {};
    const pnl = Number(round.net_pnl) || 0;
    const inv = _terminalRoundInvested(round);
    const tone = pnl >= 0 ? '#6ee7b7' : '#fca5a5';
    const buys = (round.fills || []).length;
    const costs = Number(round?.costs?.total) || 0;
    return `<tr>
      <td>${escapeHtml(_cascadeOptionsTimestamp(round.closed_at))}</td>
      <td><strong>${escapeHtml(row.symbol)}</strong><small>${row.ended ? 'ended' : 'running'}</small></td>
      <td>#${escapeHtml(String(round.round_id))}<small>${escapeHtml(String(round.exit_reason || '').replaceAll('_', ' '))}</small></td>
      <td class="num">${escapeHtml(_cascadeNumber(_terminalRoundAvgEntry(round)))}</td>
      <td class="num">${escapeHtml(_cascadeNumber(round.exit_price))}<small>${escapeHtml(String(round.exit_quantity || 0))} qty</small></td>
      <td class="num">${escapeHtml(_terminalCascadeMoney(inv))}</td>
      <td class="num">${escapeHtml(_terminalCascadeMoney(costs))}</td>
      <td class="num" style="color:${tone};font-weight:700;">${pnl >= 0 ? '+' : '−'}${escapeHtml(_terminalCascadeMoney(Math.abs(pnl)))}<small style="color:${tone};">${inv > 0 ? `${(pnl / inv * 100).toFixed(2)}%` : ''}</small></td>
      <td>${escapeHtml(_terminalHeldFor(round.opened_at, round.closed_at))}</td>
      <td><button class="btn btn-sm btn-outline" onclick="terminalCascadeShowRoundLog('${escapeJsAttr(row.source)}','${escapeJsAttr(row.sourceKey)}',${Number(round.round_id) || 0})"${buys ? '' : ' disabled title="No per-buy detail recorded for this round"'}>Log${buys ? ` (${buys})` : ''}</button></td>
    </tr>`;
  }).join('');
  body.innerHTML = `<div class="terminal-cascade-table-scroll"><table class="terminal-cascade-ladder-table"><thead><tr><th>Closed</th><th>Scrip</th><th>Round</th><th class="num">Avg entry</th><th class="num">Exit</th><th class="num">Invested</th><th class="num">Costs</th><th class="num">Net</th><th>Held</th><th></th></tr></thead><tbody>${html}</tbody></table></div>`;
  if (meta) {
    const tone = realised >= 0 ? '#6ee7b7' : '#fca5a5';
    meta.innerHTML = `${rows.length} round${rows.length === 1 ? '' : 's'} closed · ${escapeHtml(_terminalCascadeMoney(invested))} deployed · <strong style="color:${tone};">${realised >= 0 ? '+' : '−'}${escapeHtml(_terminalCascadeMoney(Math.abs(realised)))} net</strong> after costs · ${wins}/${rows.length} in profit. Log opens the individual buys behind each average.`;
  }
  if (pager) {
    if (pages <= 1) { pager.innerHTML = ''; } else {
      const to = Math.min(from + _TERMINAL_LEDGER_PAGE_SIZE, rows.length);
      pager.innerHTML = `<span>${from + 1}–${to} of ${rows.length}</span>`
        + `<button class="btn btn-sm btn-outline" ${_terminalLedgerPage === 0 ? 'disabled' : ''} onclick="terminalLedgerPageMove(-1)">Newer</button>`
        + `<button class="btn btn-sm btn-outline" ${_terminalLedgerPage >= pages - 1 ? 'disabled' : ''} onclick="terminalLedgerPageMove(1)">Older</button>`;
    }
  }
}

function terminalCascadeShowRoundLog(source, sourceKey, roundId) {
  let symbol = '—', round = null;
  if (source === 'archive') {
    const archive = _terminalClosedCampaigns.find(row => String(row.archive_id) === String(sourceKey));
    symbol = String(archive?.instrument?.symbol || '—');
    round = (archive?.rounds || []).find(row => Number(row.round_id) === Number(roundId)) || null;
  } else {
    const campaign = (_lastTerminalCascadeStatus?.campaigns || []).find(row => row?.instrument?.symbol === sourceKey);
    symbol = String(campaign?.instrument?.symbol || sourceKey);
    round = (campaign?.rounds || []).find(row => Number(row.round_id) === Number(roundId)) || null;
  }
  const overlay = document.getElementById('terminal-cascade-round-overlay');
  const title = document.getElementById('terminal-cascade-round-title');
  const meta = document.getElementById('terminal-cascade-round-meta');
  const body = document.getElementById('terminal-cascade-round-body');
  if (!overlay || !body) return;
  if (title) title.textContent = `${symbol} — Round #${roundId}`;
  if (!round) {
    if (meta) meta.textContent = 'This round is no longer in the payload.';
    body.innerHTML = '<div class="terminal-cascade-empty">Round not found.</div>';
  } else {
    const fills = round.fills || [];
    const pnl = Number(round.net_pnl) || 0;
    if (meta) {
      meta.textContent = `${fills.length} buy${fills.length === 1 ? '' : 's'} · exit ${_cascadeNumber(round.exit_price)} (${String(round.exit_reason || '').replaceAll('_', ' ')}) · net ${_terminalCascadeMoney(pnl)} after ${_terminalCascadeMoney(Number(round?.costs?.total) || 0)} costs`;
    }
    const rows = fills.map(fill => `<tr>
      <td>${escapeHtml(_cascadeOptionsTimestamp(fill.timestamp))}</td>
      <td class="num">${escapeHtml(String(fill.quantity))}</td>
      <td class="num">${escapeHtml(_cascadeNumber(fill.trade_price))}</td>
      <td class="num">${escapeHtml(_cascadeNumber(fill.signal_price))}</td>
      <td class="num">${escapeHtml(_terminalCascadeMoney(fill.spent_inr))}</td>
      <td>${escapeHtml((fill.rung_keys || []).join(', ') || '—')}</td>
    </tr>`).join('');
    body.innerHTML = fills.length
      ? `<div class="terminal-cascade-table-scroll"><table class="terminal-cascade-ladder-table"><thead><tr><th>Filled</th><th class="num">Qty</th><th class="num">Trade price</th><th class="num">Signal price</th><th class="num">Spent</th><th>Rungs</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : '<div class="terminal-cascade-empty">This round closed before per-buy detail was recorded, so only the averages survive.</div>';
  }
  overlay.classList.add('is-open');
  overlay.setAttribute('aria-hidden', 'false');
  // Move focus into the dialog so keyboard and screen-reader users land inside
  // it, and remember where they came from to restore focus on close.
  _terminalCascadeRoundOpener = document.activeElement;
  const closeBtn = overlay.querySelector('.terminal-cascade-chart-control');
  if (closeBtn) closeBtn.focus();
}

let _terminalCascadeRoundOpener = null;

function terminalCascadeHideRoundLog() {
  const overlay = document.getElementById('terminal-cascade-round-overlay');
  if (!overlay) return;
  overlay.classList.remove('is-open');
  overlay.setAttribute('aria-hidden', 'true');
  if (_terminalCascadeRoundOpener && typeof _terminalCascadeRoundOpener.focus === 'function') {
    _terminalCascadeRoundOpener.focus();
  }
  _terminalCascadeRoundOpener = null;
}

function terminalCascadeRoundBackdrop(event) {
  if (event && event.target && event.target.id === 'terminal-cascade-round-overlay') terminalCascadeHideRoundLog();
}

async function refreshTerminalClosedCampaigns() {
  try {
    const res = await fetch('/api/terminal/cascade/closed', { credentials: 'same-origin', cache: 'no-store' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status !== 'ok') throw new Error(data?.detail || 'Unable to load closed campaigns');
    _terminalClosedCampaigns = Array.isArray(data.campaigns) ? data.campaigns : [];
  } catch (error) {
    _terminalClosedCampaigns = _terminalClosedCampaigns || [];
  }
  _renderTerminalClosedCampaigns();
  _renderTerminalRoundsLedger();
}

async function purgeTerminalClosedCampaign(archiveId) {
  const ok = await customConfirm('Remove this campaign from the closed history? This only clears the record — no orders are affected.', { title: 'Remove from History', icon: ICO.warn(28), okText: 'Remove', danger: true });
  if (!ok) return;
  const res = await fetch(`/api/terminal/cascade/closed/${encodeURIComponent(archiveId)}`, { method: 'DELETE', credentials: 'same-origin' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status !== 'purged') { _terminalCascadeSetStatus(data?.detail || 'Purge failed.', 'error'); return; }
  refreshTerminalClosedCampaigns();
}

function _renderTerminalClosedCampaigns() {
  const body = document.getElementById('terminal-closed-campaigns-body');
  if (!body) return;
  const meta = document.getElementById('terminal-closed-campaigns-meta');
  if (!_terminalClosedCampaigns.length) {
    body.innerHTML = '<div class="terminal-cascade-empty">No closed campaigns yet — deleting a campaign moves it here.</div>';
    if (meta) meta.textContent = 'Deleted campaigns keep their record here — rounds, realised money and how they ended.';
    return;
  }
  let realised = 0;
  const rows = _terminalClosedCampaigns.map(archive => {
    const inst = archive.instrument || {};
    const config = archive.config || {};
    const rounds = archive.rounds || [];
    const net = Number(archive.realised_net_inr) || 0;
    realised += net;
    const tone = net >= 0 ? '#6ee7b7' : '#fca5a5';
    const heldQty = Number(archive.open_quantity) || 0;
    return `<tr>
      <td>${escapeHtml(_cascadeOptionsTimestamp(archive.deleted_at))}</td>
      <td><strong>${escapeHtml(String(inst.symbol || '—'))}</strong><small>${escapeHtml(inst.signal_symbol && inst.signal_symbol !== inst.symbol ? `${inst.signal_symbol} signal` : '')}</small></td>
      <td>${escapeHtml(String(config.timeframe || '—'))}<small>${escapeHtml(String(config.product_type || ''))}</small></td>
      <td class="num">${escapeHtml(_terminalCascadeMoney(config.capital_inr || 0))}</td>
      <td class="num">${escapeHtml(_cascadeNumber(archive?.mother?.trade?.high))}</td>
      <td>${escapeHtml(String(archive.status || '—').replaceAll('_', ' '))}${heldQty ? `<small style="color:var(--warn);">deleted holding ${heldQty} qty</small>` : ''}</td>
      <td class="num">${rounds.length}</td>
      <td class="num" style="color:${tone};font-weight:700;">${net >= 0 ? '+' : '−'}${escapeHtml(_terminalCascadeMoney(Math.abs(net)))}</td>
      <td><button class="btn btn-sm btn-danger" onclick="purgeTerminalClosedCampaign('${escapeJsAttr(String(archive.archive_id || ''))}')">Purge</button></td>
    </tr>`;
  }).join('');
  body.innerHTML = `<div class="terminal-cascade-table-scroll"><table class="terminal-cascade-ladder-table"><thead><tr><th>Deleted</th><th>Scrip</th><th>TF</th><th class="num">Capital</th><th class="num">Mother high</th><th>Ended as</th><th class="num">Rounds</th><th class="num">Realised</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  if (meta) {
    const tone = realised >= 0 ? '#6ee7b7' : '#fca5a5';
    meta.innerHTML = `${_terminalClosedCampaigns.length} closed campaign${_terminalClosedCampaigns.length === 1 ? '' : 's'} · <strong style="color:${tone};">${realised >= 0 ? '+' : '−'}${escapeHtml(_terminalCascadeMoney(Math.abs(realised)))}</strong> realised net across them.`;
  }
}


function _terminalCascadeCurrentChartTimeframe() {
  if (_terminalCascadeChartTimeframe !== 'auto') return _terminalCascadeChartTimeframe;
  const campaigns = _lastTerminalCascadeStatus?.campaigns || [];
  const current = campaigns.find(campaign => campaign?.instrument?.symbol === _terminalCascadeChartContext?.symbol) || campaigns[0];
  return _terminalCascadeChartContext?.timeframe || current?.config?.timeframe || _terminalCascadeEl('terminal-cascade-timeframe')?.value || '5m';
}

function _terminalCascadeMarkChartTimeframe(resolved) {
  document.querySelectorAll('#terminal-cascade-chart-tf .terminal-cascade-tf-option[data-tf]').forEach((button) => {
    const tf = button.getAttribute('data-tf');
    const active = tf === _terminalCascadeChartTimeframe;
    button.classList.toggle('is-active', active);
    button.classList.toggle('is-resolved', _terminalCascadeChartTimeframe === 'auto' && tf === resolved);
    button.setAttribute('aria-checked', active ? 'true' : 'false');
  });
}

function terminalCascadeTimeframeChanged() {
  // A daily mother is its date: the engine identifies it by the 09:15 session
  // open, so snap whatever time is in the picker rather than rejecting it.
  const tf = _terminalCascadeEl('terminal-cascade-timeframe')?.value || '5m';
  const input = _terminalCascadeEl('terminal-cascade-mother-timestamp');
  if (!input) return;
  if (tf === '1d' && input.value) input.value = `${input.value.slice(0, 10)}T09:15`;
  if (tf === '1d') { input.step = 86400; input.title = 'Daily mother: pick the date — the time is always 09:15 IST.'; }
  else { input.step = 300; input.title = ''; }
}

function setTerminalCascadeChartTimeframe(tf) {
  _terminalCascadeChartTimeframe = ['auto', '5m', '15m', '1h', '1d'].includes(tf) ? tf : 'auto';
  _terminalCascadeMarkChartTimeframe(_terminalCascadeCurrentChartTimeframe());
  const overlay = _terminalCascadeEl('terminal-cascade-chart-overlay');
  if (overlay?.classList.contains('is-open')) loadTerminalCascadeChart({ keepOpen: true }).catch(() => {});
}

function _terminalCascadeChartStatusMap() {
  const campaigns = _lastTerminalCascadeStatus?.campaigns || [];
  const campaign = campaigns.find(row => row?.instrument?.symbol === _terminalCascadeChartContext?.symbol) || campaigns[0] || {};
  const map = new Map();
  (campaign.rungs || []).forEach(row => map.set(`${row.leg_id}:${row.level}`, row));
  return map;
}

function _terminalCascadeChartDetails(payload) {
  const geometry = payload?.geometry || {};
  const trendlines = Array.isArray(geometry.trendlines) ? geometry.trendlines : [];
  const legs = Array.isArray(geometry.legs) ? geometry.legs : [];
  const rungMap = _terminalCascadeChartStatusMap();
  const rows = [];
  trendlines.forEach(line => {
    rows.push(`<tr><td>TL ${escapeHtml(line.id)}</td><td>mother high</td><td class="num">${escapeHtml(_cascadeNumber(line.anchor1_price))}</td><td>${escapeHtml(_cascadeOptionsTimestamp(line.anchor1_timestamp))}</td></tr>`);
    rows.push(`<tr><td></td><td>red open</td><td class="num">${escapeHtml(_cascadeNumber(line.anchor2_price))}</td><td>${escapeHtml(_cascadeOptionsTimestamp(line.anchor2_timestamp))}</td></tr>`);
  });
  legs.forEach(leg => {
    const hi = Number(leg.fib_high), lo = Number(leg.fib_low), range = hi - lo;
    rows.push(`<tr><td>Fib ${escapeHtml(leg.leg_id)}</td><td>0 swing high</td><td class="num">${escapeHtml(_cascadeNumber(hi))}</td><td>${escapeHtml(_cascadeOptionsTimestamp(leg.touch_timestamp))}</td></tr>`);
    rows.push(`<tr><td></td><td>1 leg low</td><td class="num">${escapeHtml(_cascadeNumber(lo))}</td><td>reference move</td></tr>`);
    if (Number.isFinite(range) && range > 0) {
      [2, 4, 8].forEach(level => {
        const rung = rungMap.get(`${leg.leg_id}:${level}`) || {};
        const price = hi - level * range;
        rows.push(`<tr><td></td><td>L${level} buy level</td><td class="num">${escapeHtml(_cascadeNumber(price))}</td><td>${escapeHtml(rung.status || 'PENDING')}${rung.budget_inr ? ` · ${escapeHtml(_terminalCascadeMoney(rung.budget_inr))}` : ''}</td></tr>`);
      });
    }
  });
  if (!rows.length) return '<div class="terminal-cascade-empty">No trendlines or fib levels marked yet.</div>';
  return `<details class="terminal-cascade-chart-details"><summary><span>Structure, fibs and order details</span><em>${rows.length} rows</em></summary><div class="terminal-cascade-table-scroll"><table class="terminal-cascade-ladder-table"><thead><tr><th>Object</th><th>Anchor</th><th class="num">Price</th><th>Time / status</th></tr></thead><tbody>${rows.join('')}</tbody></table></div></details>`;
}

function _terminalCascadeChartHtml(payload) {
  const instrument = payload?.instrument || {};
  const referenceMode = instrument.reference_mode === 'reference_index';
  const signal = instrument.signal_symbol || instrument.symbol || 'Signal';
  const trade = instrument.symbol || signal;
  const legend = `<div class="terminal-cascade-chart-legend">
    <span style="color:#a78bfa;">MC / mother high</span>
    <span style="color:#60a5fa;">trendlines</span>
    <span style="color:var(--green);">fib buy levels</span>
    <span style="color:#6ee7b7;">fills</span>
    ${referenceMode ? `<em>${escapeHtml(signal)} signal chart -> ${escapeHtml(trade)} trade/TP</em>` : '<em>Own chart, own trade and TP scale</em>'}
  </div>`;
  return legend + _terminalCascadeCanvasHtml() + _terminalCascadeChartDetails(payload);
}

function _terminalCascadeChartPalette() {
  let theme = document.documentElement.getAttribute('data-theme');
  if (!theme || theme === 'auto') {
    theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  if (theme === 'light') {
    return {
      bg: '#ffffff', grid: 'rgba(15,23,42,.10)', axis: 'rgba(51,65,85,.75)',
      up: '#0f766e', down: '#be123c', mother: '#7c3aed',
      tp: '#047857', avg: '#334155', fill: '#15803d', fillRing: '#ffffff',
      fibs: ['#1d4ed8', '#15803d', '#be123c'],
    };
  }
  return {
    bg: '#07101d', grid: 'rgba(148,163,184,.12)', axis: 'rgba(148,163,184,.55)',
    up: '#3fae56', down: '#d9534f', mother: '#a855f7',
    tp: 'var(--green)', avg: '#e2e8f0', fill: 'var(--green)', fillRing: '#0b1220',
    fibs: ['#3b82f6', 'var(--green)', 'var(--red)'],
  };
}

// ── Canvas chart renderer ─────────────────────────────────────────────
// The Terminal chart draws on canvas the way the CryptoForge Cascade chart
// does: device-pixel-ratio aware, pannable, zoomable around the cursor, with
// a draggable price axis and a crosshair on its own cheap layer. The payload
// and the palette are the only inputs shared with the SVG renderer this
// replaced — what the lines MEAN is unchanged.
let _tcv = null;

function _terminalCascadeCanvasHtml() {
  return '<div class="terminal-cascade-canvas-host" id="terminal-cascade-canvas-host">'
    + '<canvas id="terminal-cascade-canvas-main"></canvas>'
    + '<canvas id="terminal-cascade-canvas-overlay"></canvas>'
    + '</div>';
}

function _terminalCascadeUnmountCanvas() {
  if (_tcv && _tcv.ro) { try { _tcv.ro.disconnect(); } catch (err) { /* detached */ } }
  _tcv = null;
}

function _terminalCascadeCanvasViewSnapshot() {
  if (!_tcv) return null;
  return {
    symbol: _terminalCascadeChartContext?.symbol || '',
    timeframe: _terminalCascadeChartContext?.timeframe || '',
    start: _tcv.start,
    count: _tcv.count,
    atRight: _tcv.start + _tcv.count >= _tcv.candles.length - 0.5,
    yAuto: _tcv.yAuto,
    yMin: _tcv.yMin,
    yMax: _tcv.yMax,
  };
}

function _tcvMinCount(total) {
  // A young campaign has only a handful of bars; a 10-bar floor made zoom a
  // no-op there (9 candles could never dip under it, freezing the readout).
  return Math.min(5, total);
}

function _terminalCascadeMountCanvas(payload, keepView = null) {
  _terminalCascadeUnmountCanvas();
  const host = document.getElementById('terminal-cascade-canvas-host');
  const main = document.getElementById('terminal-cascade-canvas-main');
  const overlay = document.getElementById('terminal-cascade-canvas-overlay');
  if (!host || !main || !overlay) return;
  const candles = (Array.isArray(payload?.candles) ? payload.candles : [])
    .filter(row => [row.o, row.h, row.l, row.c].every(value => Number.isFinite(Number(value))));
  if (!candles.length) {
    host.innerHTML = '<div class="pf-cascade-chart-empty">No candles</div>';
    return;
  }
  const n = candles.length;
  const view = { start: 0, count: n, yAuto: true, yMin: 0, yMax: 1 };
  const sameContext = keepView
    && keepView.symbol === (_terminalCascadeChartContext?.symbol || '')
    && keepView.timeframe === (_terminalCascadeChartContext?.timeframe || '');
  if (sameContext) {
    view.count = Math.min(Math.max(keepView.count, _tcvMinCount(n)), n);
    // Pinned to the newest candle stays pinned when the poll appends bars.
    view.start = keepView.atRight ? n - view.count : Math.max(0, Math.min(keepView.start, n - view.count));
    view.yAuto = keepView.yAuto;
    view.yMin = keepView.yMin;
    view.yMax = keepView.yMax;
  }
  _tcv = {
    host, main, overlay, payload, candles,
    start: view.start, count: view.count,
    yAuto: view.yAuto, yMin: view.yMin, yMax: view.yMax,
    cross: null, drag: null, ro: null,
  };
  _terminalCascadeCanvasBindEvents();
  _tcv.ro = new ResizeObserver(() => { _terminalCascadeCanvasDraw(); });
  _tcv.ro.observe(host);
  _terminalCascadeCanvasDraw();
}

function _tcvLayout() {
  const dpr = window.devicePixelRatio || 1;
  const w = _tcv.host.clientWidth || 900;
  const h = _tcv.host.clientHeight || 480;
  [_tcv.main, _tcv.overlay].forEach(cv => {
    const pw = Math.max(1, Math.round(w * dpr)), ph = Math.max(1, Math.round(h * dpr));
    if (cv.width !== pw || cv.height !== ph) { cv.width = pw; cv.height = ph; }
    cv.style.width = `${w}px`;
    cv.style.height = `${h}px`;
  });
  return { dpr, w, h, gutter: 150, padR: 64, padT: 12, padB: 24 };
}

function _tcvMillis(value) {
  const t = Date.parse(value);
  return Number.isFinite(t) ? t : 0;
}

// Fractional bar index for a timestamp. Candles deliberately skip NSE
// overnight/weekend gaps, so anchors must live on the bar sequence, not on
// elapsed calendar time.
function _tcvBarIndexAt(value) {
  const c = _tcv.candles, n = c.length;
  const t = _tcvMillis(value);
  if (!t || t <= _tcvMillis(c[0].t)) return 0;
  if (t >= _tcvMillis(c[n - 1].t)) return n - 1;
  for (let i = 1; i < n; i += 1) {
    const right = _tcvMillis(c[i].t);
    if (t > right) continue;
    const left = _tcvMillis(c[i - 1].t);
    const fraction = right === left ? 1 : Math.max(0, Math.min(1, (t - left) / (right - left)));
    return (i - 1) + fraction;
  }
  return n - 1;
}

function _tcvStamp(value, daily) {
  const options = daily
    ? { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short' }
    : { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false };
  return new Intl.DateTimeFormat('en-IN', options).format(new Date(value));
}

// How a fib rung's line is drawn for each engine status. FILLED is the one that
// spent money, so it is the heaviest and solid; PENDING is merely a plan, so it
// is thin and dashed; a retired rung fades rather than disappearing, because
// where the ladder has already been is still worth seeing.
function _terminalRungLineStyle(status) {
  switch (String(status || 'PENDING').toUpperCase()) {
    case 'FILLED':    return { dash: null,   width: 2.0, opacity: 1.00, tag: ' · filled' };
    case 'COLLECTED': return { dash: [7, 3], width: 1.5, opacity: 0.95, tag: ' · armed' };
    case 'CLOSED':    return { dash: [2, 4], width: 1.0, opacity: 0.40, tag: ' · closed' };
    case 'CANCELLED': return { dash: [2, 4], width: 1.0, opacity: 0.28, tag: ' · cancelled' };
    default:          return { dash: [4, 4], width: 1.1, opacity: 0.65, tag: '' };
  }
}

function _terminalCascadeCanvasDraw() {
  if (!_tcv) return;
  const PAL = _terminalCascadeChartPalette();
  const L = _tcvLayout();
  const ctx = _tcv.main.getContext('2d');
  ctx.setTransform(L.dpr, 0, 0, L.dpr, 0, 0);
  ctx.clearRect(0, 0, L.w, L.h);
  ctx.fillStyle = PAL.bg;
  ctx.fillRect(0, 0, L.w, L.h);

  const c = _tcv.candles, n = c.length;
  const x0 = L.gutter, x1 = L.w - L.padR, y0 = L.padT, y1 = L.h - L.padB;
  const plotW = Math.max(x1 - x0, 40), plotH = Math.max(y1 - y0, 40);
  _tcv.count = Math.max(_tcvMinCount(n), Math.min(_tcv.count, n));
  _tcv.start = Math.max(0, Math.min(_tcv.start, n - _tcv.count));
  const barW = plotW / _tcv.count;
  const X = index => x0 + (index - _tcv.start + 0.5) * barW;

  const geometry = _tcv.payload.geometry || {};
  const legs = Array.isArray(geometry.legs) ? geometry.legs : [];
  const trendlines = Array.isArray(geometry.trendlines) ? geometry.trendlines : [];
  const campaigns = _lastTerminalCascadeStatus?.campaigns || [];
  const campaign = campaigns.find(row => row?.instrument?.symbol === _terminalCascadeChartContext?.symbol) || campaigns[0] || {};
  const instrument = _tcv.payload.instrument || campaign.instrument || {};
  const ownScale = instrument.reference_mode !== 'reference_index';
  const mother = _tcv.payload.mother || {};

  // Price range: fit the price action and the geometry it is actually made of
  // — the visible candles, the mother, each leg's real swing high and low, and
  // where an open basket is heading (avg entry, target).
  //
  // It must NOT fit the extrapolated fib rungs. L8 sits eight leg-ranges below
  // the swing high, so on a wide leg it lands hundreds of rupees under any
  // price ever traded; forcing it into the axis squashed every candle into the
  // top fifth of the chart and left the rest of the pane empty. The CryptoForge
  // cascade chart scales off leg.touch_high/leg.low for exactly this reason.
  // Deep rungs still draw — hline() simply skips whatever is off-screen, and
  // zooming out brings them back.
  const first = Math.max(0, Math.floor(_tcv.start));
  const last = Math.min(n - 1, Math.ceil(_tcv.start + _tcv.count));
  let lo = Infinity, hi = -Infinity;
  for (let i = first; i <= last; i += 1) {
    lo = Math.min(lo, Number(c[i].l));
    hi = Math.max(hi, Number(c[i].h));
  }
  if (Number.isFinite(Number(mother.h))) hi = Math.max(hi, Number(mother.h));
  if (Number.isFinite(Number(mother.l))) lo = Math.min(lo, Number(mother.l));
  legs.forEach(leg => {
    // fib_high is the leg's touch high and fib_low its swing low: both are
    // prices the market actually printed, so both belong on the axis.
    [leg.fib_high, leg.fib_low].forEach(value => {
      const price = Number(value);
      if (!Number.isFinite(price) || price <= 0) return;
      hi = Math.max(hi, price);
      lo = Math.min(lo, price);
    });
  });
  if (ownScale) {
    [campaign.target_price, campaign.average_entry_price].forEach(price => {
      const p = Number(price);
      if (Number.isFinite(p) && p > 0) { hi = Math.max(hi, p); lo = Math.min(lo, p); }
    });
  }
  const span = (hi - lo) || 1;
  let maxP = hi + span * 0.06, minP = lo - span * 0.06;
  if (!_tcv.yAuto && Number.isFinite(_tcv.yMin) && Number.isFinite(_tcv.yMax) && _tcv.yMax > _tcv.yMin) {
    minP = _tcv.yMin;
    maxP = _tcv.yMax;
  } else {
    _tcv.yMin = minP;
    _tcv.yMax = maxP;
  }
  const Y = price => y0 + ((maxP - price) / ((maxP - minP) || 1)) * plotH;
  _tcv.frame = { x0, x1, y0, y1, plotW, plotH, barW, minP, maxP };

  const number = value => Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  const mono = size => `${size}px "JetBrains Mono", monospace`;
  const sharp = value => Math.round(value) + 0.5;

  // Grid and price axis.
  ctx.font = mono(9.5);
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= 4; i += 1) {
    const price = minP + (maxP - minP) * (i / 4);
    const y = sharp(Y(price));
    ctx.strokeStyle = PAL.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x0, y);
    ctx.lineTo(x1, y);
    ctx.stroke();
    ctx.fillStyle = PAL.axis;
    ctx.textAlign = 'left';
    ctx.fillText(number(price), x1 + 6, y);
  }

  // Time axis.
  const daily = String(_terminalCascadeChartContext?.timeframe || '') === '1d';
  const ticks = Math.min(6, Math.max(2, Math.floor(plotW / 130)));
  ctx.fillStyle = PAL.axis;
  ctx.textAlign = 'center';
  for (let i = 0; i < ticks; i += 1) {
    const at = Math.round(_tcv.start + (_tcv.count - 1) * (i / Math.max(ticks - 1, 1)));
    if (at < 0 || at >= n) continue;
    ctx.fillText(_tcvStamp(c[at].t, daily), X(at), L.h - 10);
  }

  // Everything price-anchored clips to the plot; labels live in the gutter.
  ctx.save();
  ctx.beginPath();
  ctx.rect(x0, y0, plotW, plotH);
  ctx.clip();

  const bodyW = Math.max(Math.min(barW * 0.65, 9), 1);

  // Overnight gap, drawn as a synthetic candle spanning prev close -> new open,
  // sitting on the bar where the gap happened. The x-axis is a bar sequence, so
  // an overnight jump leaves no blank space of its own — without this block a
  // gap is invisible. NSE trades continuously, so a real close->open gap only
  // happens across a session break; a pause much longer than one bar is what
  // marks one. Translucent body, solid border, so the real candle shows through.
  const SESSION_BREAK_MS = 4 * 3600 * 1000;
  for (let i = Math.max(first, 1); i <= last; i += 1) {
    const prev = c[i - 1], bar = c[i];
    if (_tcvMillis(bar.t) - _tcvMillis(prev.t) < SESSION_BREAK_MS) continue;
    const prevClose = Number(prev.c), openPrice = Number(bar.o);
    if (!Number.isFinite(prevClose) || !Number.isFinite(openPrice) || prevClose === openPrice) continue;
    const gapUp = openPrice > prevClose;
    const gx = X(i), gw = bodyW + 4;
    const gTop = Y(Math.max(prevClose, openPrice));
    const gBottom = Y(Math.min(prevClose, openPrice));
    ctx.save();
    ctx.fillStyle = ctx.strokeStyle = gapUp ? PAL.up : PAL.down;
    ctx.globalAlpha = 0.22;
    ctx.fillRect(gx - gw / 2, gTop, gw, Math.max(gBottom - gTop, 1));
    ctx.globalAlpha = 0.6;
    ctx.lineWidth = 0.8;
    ctx.strokeRect(gx - gw / 2, gTop, gw, Math.max(gBottom - gTop, 1));
    ctx.restore();
  }

  for (let i = first; i <= last; i += 1) {
    const candle = c[i];
    const o = Number(candle.o), h = Number(candle.h), l = Number(candle.l), close = Number(candle.c);
    const x = X(i), up = close >= o;
    ctx.strokeStyle = ctx.fillStyle = up ? PAL.up : PAL.down;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(sharp(x), Math.round(Y(h)));
    ctx.lineTo(sharp(x), Math.round(Y(l)));
    ctx.stroke();
    const top = Math.round(Y(Math.max(o, close)));
    const bottom = Math.round(Y(Math.min(o, close)));
    const left = Math.round(x - bodyW / 2);
    ctx.fillRect(left, top, Math.max(Math.round(x + bodyW / 2) - left, 1), Math.max(bottom - top, 1));
    if (candle.is_mother) {
      const bandW = Math.max(bodyW, 6) + 6;
      ctx.save();
      ctx.globalAlpha = 0.09;
      ctx.fillStyle = PAL.mother;
      ctx.fillRect(x - bandW / 2, y0 + 1, bandW, plotH - 2);
      ctx.restore();
      ctx.strokeStyle = PAL.mother;
      ctx.lineWidth = 1.4;
      ctx.strokeRect(x - bodyW / 2 - 1, Y(h) - 1, bodyW + 2, Math.max(Y(l) - Y(h) + 2, 4));
      ctx.fillStyle = PAL.mother;
      ctx.font = `700 ${mono(9.5)}`;
      ctx.textAlign = 'center';
      ctx.fillText('MC', x, Math.max(Y(h) - 10, y0 + 8));
      ctx.font = mono(9.5);
    }
  }

  // Horizontal levels: draw inside the clip, remember labels for the gutter.
  const labels = [];
  const labelSlots = [];
  const label = (y, text, color) => {
    let ly = y;
    for (let pass = 0, moved = true; moved && pass <= labelSlots.length; pass += 1) {
      moved = false;
      for (let i = 0; i < labelSlots.length; i += 1) {
        if (Math.abs(labelSlots[i] - ly) < 10) { ly = labelSlots[i] + 10.5; moved = true; break; }
      }
    }
    labelSlots.push(ly);
    labels.push({ y: ly, text, color });
  };
  const inView = price => Number.isFinite(Number(price)) && Number(price) >= minP && Number(price) <= maxP;
  const hline = (price, color, text, dash, width, opacity) => {
    const p = Number(price);
    if (!inView(p)) return;
    const y = sharp(Y(p));
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width || 1;
    ctx.globalAlpha = opacity || 1;
    ctx.setLineDash(dash || []);
    ctx.beginPath();
    ctx.moveTo(x0, y);
    ctx.lineTo(x1, y);
    ctx.stroke();
    ctx.restore();
    if (text) label(y, text, color);
  };
  const colorById = id => PAL.fibs[(Math.max(1, Number(id) || 1) - 1) % PAL.fibs.length];

  if (Number.isFinite(Number(mother.h))) hline(Number(mother.h), PAL.mother, `MOTHER (${number(mother.h)})`, [5, 3], 1.1);

  trendlines.forEach(line => {
    const a1p = Number(line.anchor1_price), a2p = Number(line.anchor2_price);
    if (!Number.isFinite(a1p) || !Number.isFinite(a2p)) return;
    const b1 = _tcvBarIndexAt(line.anchor1_timestamp), b2 = _tcvBarIndexAt(line.anchor2_timestamp);
    if (b1 === b2) return;
    const px1 = X(b1), px2 = X(b2);
    const color = colorById(line.id);
    const noFib = line.bears_fib === false;
    const visualSlope = px2 === px1 ? 0 : (a2p - a1p) / (px2 - px1);
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = noFib ? 0.8 : 1.5;
    ctx.globalAlpha = noFib ? 0.35 : 0.96;
    ctx.setLineDash(noFib ? [6, 4] : []);
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(x0, Y(a1p + visualSlope * (x0 - px1)));
    ctx.lineTo(x1, Y(a1p + visualSlope * (x1 - px1)));
    ctx.stroke();
    ctx.restore();
    ctx.fillStyle = color;
    if (inView(a1p)) {
      ctx.beginPath();
      ctx.arc(px1, Y(a1p), 2.3, 0, Math.PI * 2);
      ctx.fill();
    }
    if (inView(a2p)) {
      ctx.beginPath();
      ctx.arc(px2, Y(a2p), 4.2, 0, Math.PI * 2);
      ctx.fillStyle = PAL.bg;
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.8;
      ctx.stroke();
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(px2, Y(a2p), 1.9, 0, Math.PI * 2);
      ctx.fill();
      ctx.font = `700 ${mono(9.5)}`;
      ctx.textAlign = 'left';
      ctx.fillText(`TL${line.id} red open`, px2 + 6, Y(a2p) - 8);
      ctx.font = mono(9.5);
    }
  });

  legs.forEach(leg => {
    const trendline = trendlines.find(line => Number(line.id) === Number(leg.trendline_id));
    if (trendline?.bears_fib === false) return;
    const color = colorById(leg.trendline_id || leg.leg_id);
    const fibHi = Number(leg.fib_high), fibLo = Number(leg.fib_low), range = fibHi - fibLo;
    if (!Number.isFinite(range) || range <= 0) return;
    hline(fibHi, color, `0 (${number(fibHi)})`, null, 0.8, 0.4);
    hline(fibLo, color, `1 (${number(fibLo)})`, null, 0.8, 0.4);
    [2, 4, 8].forEach(level => {
      const price = fibHi - level * range;
      const rung = (campaign.rungs || []).find(row => Number(row.leg_id) === Number(leg.leg_id) && Number(row.level) === level) || {};
      const budget = Number(rung.budget_inr || 0);
      // Status is drawn, not just tabled: which rungs are still waiting, which
      // have pooled their cash, and which actually bought is the whole state of
      // the ladder, and it used to live only inside the details panel. The line
      // keeps its leg colour so a rung stays traceable to the fall it came from;
      // weight, dash and opacity carry the status.
      const style = _terminalRungLineStyle(rung.status);
      hline(price, color, `L${level} (${number(price)})${budget > 0 ? ` ${_terminalCascadeMoney(budget)}` : ''}${style.tag}`,
        style.dash, style.width, style.opacity);
    });
    if (leg.touch_timestamp && inView(fibHi)) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(X(_tcvBarIndexAt(leg.touch_timestamp)), Y(fibHi), 3.5, 0, Math.PI * 2);
      ctx.stroke();
    }
  });

  (campaign.open_fills || []).forEach(fill => {
    const price = Number(fill.signal_price);
    if (!inView(price)) return;
    ctx.beginPath();
    ctx.arc(X(_tcvBarIndexAt(fill.timestamp)), Y(price), 3.5, 0, Math.PI * 2);
    ctx.fillStyle = PAL.fill;
    ctx.fill();
    ctx.strokeStyle = PAL.fillRing;
    ctx.lineWidth = 1;
    ctx.stroke();
  });
  if (ownScale) {
    hline(Number(campaign.target_price), PAL.tp, `TARGET (${number(campaign.target_price)})`, [6, 3], 1.2);
    hline(Number(campaign.average_entry_price), PAL.avg, `AVG ENTRY (${number(campaign.average_entry_price)})`, [4, 4], 1.1);
  }
  ctx.restore();

  // Gutter labels, outside the clip so long ones never smear the candles.
  ctx.font = mono(10);
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  labels.forEach(item => {
    ctx.fillStyle = item.color;
    ctx.fillText(item.text, x0 - 6, item.y);
  });

  const readout = _terminalCascadeEl('terminal-cascade-zoom-level');
  if (readout) readout.textContent = `${Math.round((n / _tcv.count) * 100)}%`;
  _terminalCascadeCanvasDrawOverlay();
}

function _terminalCascadeCanvasDrawOverlay() {
  if (!_tcv || !_tcv.frame) return;
  const PAL = _terminalCascadeChartPalette();
  const L = _tcvLayout();
  const ctx = _tcv.overlay.getContext('2d');
  ctx.setTransform(L.dpr, 0, 0, L.dpr, 0, 0);
  ctx.clearRect(0, 0, L.w, L.h);
  const cross = _tcv.cross;
  if (!cross) return;
  const F = _tcv.frame;
  const c = _tcv.candles, n = c.length;
  const index = Math.floor(_tcv.start + (cross.x - F.x0) / F.barW);
  if (index < 0 || index >= n || cross.x < F.x0 || cross.x > F.x1 || cross.y < F.y0 || cross.y > F.y1) return;
  const snapX = F.x0 + (index - _tcv.start + 0.5) * F.barW;
  const price = F.maxP - ((cross.y - F.y0) / F.plotH) * (F.maxP - F.minP);
  const number = value => Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 });

  ctx.strokeStyle = PAL.axis;
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(snapX, F.y0);
  ctx.lineTo(snapX, F.y1);
  ctx.moveTo(F.x0, cross.y);
  ctx.lineTo(F.x1, cross.y);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.font = '10px "JetBrains Mono", monospace';
  ctx.textBaseline = 'middle';
  const priceText = number(price);
  const pw = ctx.measureText(priceText).width + 10;
  ctx.fillStyle = PAL.bg;
  ctx.fillRect(F.x1 + 1, cross.y - 8, Math.max(pw, L.padR - 2), 16);
  ctx.fillStyle = PAL.axis;
  ctx.textAlign = 'left';
  ctx.fillText(priceText, F.x1 + 6, cross.y);

  const daily = String(_terminalCascadeChartContext?.timeframe || '') === '1d';
  const candle = c[index];
  const stampText = _tcvStamp(candle.t, daily);
  const sw = ctx.measureText(stampText).width + 10;
  ctx.fillStyle = PAL.bg;
  ctx.fillRect(snapX - sw / 2, L.h - 18, sw, 14);
  ctx.fillStyle = PAL.axis;
  ctx.textAlign = 'center';
  ctx.fillText(stampText, snapX, L.h - 11);

  const ohlc = `O ${number(candle.o)}  H ${number(candle.h)}  L ${number(candle.l)}  C ${number(candle.c)}`;
  ctx.textAlign = 'left';
  const ow = ctx.measureText(ohlc).width + 12;
  ctx.fillStyle = PAL.bg;
  ctx.fillRect(F.x0 + 4, F.y0 + 2, ow, 16);
  ctx.fillStyle = Number(candle.c) >= Number(candle.o) ? PAL.up : PAL.down;
  ctx.fillText(ohlc, F.x0 + 10, F.y0 + 10);
}

function _terminalCascadeCanvasBindEvents() {
  const ov = _tcv.overlay;
  ov.style.touchAction = 'none';
  ov.addEventListener('pointerdown', event => {
    if (!_tcv || !_tcv.frame) return;
    const rect = ov.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    _tcv.drag = {
      x, y,
      start: _tcv.start,
      yMin: _tcv.yMin,
      yMax: _tcv.yMax,
      axis: x > _tcv.frame.x1,
      yAutoWas: _tcv.yAuto,
    };
    ov.style.cursor = _tcv.drag.axis ? 'ns-resize' : 'grabbing';
    try { ov.setPointerCapture(event.pointerId); } catch (err) { /* fine */ }
  });
  ov.addEventListener('pointermove', event => {
    if (!_tcv || !_tcv.frame) return;
    const rect = ov.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    const drag = _tcv.drag;
    if (drag) {
      if (drag.axis) {
        // Dragging the price axis down stretches the range (zooms out),
        // up compresses it — around the range centre, TradingView-style.
        const factor = Math.exp((y - drag.y) / 240);
        const centre = (drag.yMin + drag.yMax) / 2;
        const half = ((drag.yMax - drag.yMin) / 2) * factor;
        _tcv.yMin = centre - half;
        _tcv.yMax = centre + half;
        _tcv.yAuto = false;
      } else {
        _tcv.start = drag.start - (x - drag.x) / _tcv.frame.barW;
        if (!drag.yAutoWas) {
          const perPx = (drag.yMax - drag.yMin) / _tcv.frame.plotH;
          _tcv.yMin = drag.yMin + (y - drag.y) * perPx;
          _tcv.yMax = drag.yMax + (y - drag.y) * perPx;
        }
      }
      _terminalCascadeCanvasDraw();
      return;
    }
    _tcv.cross = { x, y };
    _terminalCascadeCanvasDrawOverlay();
  });
  const release = () => {
    if (!_tcv) return;
    _tcv.drag = null;
    ov.style.cursor = 'crosshair';
  };
  ov.addEventListener('pointerup', release);
  ov.addEventListener('pointercancel', release);
  ov.addEventListener('pointerleave', () => {
    if (!_tcv) return;
    _tcv.cross = null;
    _terminalCascadeCanvasDrawOverlay();
  });
  ov.addEventListener('wheel', event => {
    if (!_tcv || !_tcv.frame) return;
    event.preventDefault();
    const rect = ov.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const F = _tcv.frame;
    const anchor = _tcv.start + (x - F.x0) / F.barW;
    const n = _tcv.candles.length;
    const factor = event.deltaY < 0 ? 1 / 1.15 : 1.15;
    _tcv.count = Math.max(_tcvMinCount(n), Math.min(_tcv.count * factor, n));
    _tcv.start = anchor - (x - F.x0) / (F.plotW / _tcv.count);
    _terminalCascadeCanvasDraw();
  }, { passive: false });
  ov.addEventListener('dblclick', () => {
    if (!_tcv) return;
    _tcv.start = 0;
    _tcv.count = _tcv.candles.length;
    _tcv.yAuto = true;
    _terminalCascadeCanvasDraw();
  });
}

function terminalCascadeZoom(factor, resetPan = false) {
  if (!_tcv) return;
  const n = _tcv.candles.length;
  if (resetPan || !factor) {
    _tcv.start = 0;
    _tcv.count = n;
    _tcv.yAuto = true;
  } else {
    const centre = _tcv.start + _tcv.count / 2;
    _tcv.count = Math.max(_tcvMinCount(n), Math.min(_tcv.count / factor, n));
    _tcv.start = centre - _tcv.count / 2;
  }
  _terminalCascadeCanvasDraw();
}

function terminalCascadeZoomIn() { terminalCascadeZoom(1.4); }
function terminalCascadeZoomOut() { terminalCascadeZoom(1 / 1.4); }
function terminalCascadeZoomReset() { terminalCascadeZoom(0, true); }

function toggleTerminalCascadeFullscreen(force) {
  const panel = _terminalCascadeEl('terminal-cascade-chart-panel');
  const button = _terminalCascadeEl('terminal-cascade-expand-btn');
  if (!panel) return;
  const open = typeof force === 'boolean' ? force : !panel.classList.contains('is-fullscreen');
  panel.classList.toggle('is-fullscreen', open);
  panel.classList.toggle('is-chart-only', open);
  terminalCascadeZoomReset();
  if (button) {
    button.setAttribute('aria-pressed', open ? 'true' : 'false');
    button.setAttribute('title', open ? 'Exit full screen' : 'Expand chart');
    button.setAttribute('aria-label', open ? 'Exit full screen' : 'Expand chart');
    const icon = _terminalCascadeEl('terminal-cascade-expand-icon');
    if (icon) icon.innerHTML = open ? ICO.cross(15) : ICO.target(15);
  }
}

async function loadTerminalCascadeChart(symbolArg = '', timestampArg = '', timeframeArg = '') {
  const campaigns = _lastTerminalCascadeStatus?.campaigns || [];
  const requestedSymbol = typeof symbolArg === 'string' ? symbolArg : '';
  const active = campaigns.find(campaign => campaign?.instrument?.symbol === requestedSymbol)
    || campaigns.find(campaign => campaign?.instrument?.symbol === _terminalCascadeChartContext?.symbol)
    || campaigns.find(campaign => campaign?.instrument?.symbol === _stockTerminalSelected?.symbol)
    || campaigns[0];
  const symbol = requestedSymbol || active?.instrument?.symbol || _stockTerminalSelected?.symbol || document.getElementById('stock-terminal-symbol')?.value || '';
  const timestamp = (typeof timestampArg === 'string' && timestampArg) || active?.mother?.signal?.timestamp || _terminalCascadeEl('terminal-cascade-mother-timestamp')?.value || '';
  const timeframe = (typeof timeframeArg === 'string' && timeframeArg) || _terminalCascadeCurrentChartTimeframe();
  const overlay = _terminalCascadeEl('terminal-cascade-chart-overlay');
  const body = _terminalCascadeEl('terminal-cascade-chart-body');
  if (!symbol || !timestamp) { _terminalCascadeSetStatus('Select a symbol and mother timestamp first.', 'error'); return; }
  if (overlay) {
    if (overlay.parentNode !== document.body) document.body.appendChild(overlay);
    overlay.classList.add('is-open');
    overlay.setAttribute('aria-hidden', 'false');
    document.body.classList.add('terminal-cascade-chart-open');
  }
  const keepViewSnapshot = _terminalCascadeCanvasViewSnapshot();
  if (body) body.innerHTML = '<div class="pf-cascade-chart-empty">Loading chart...</div>';
  _terminalCascadeMarkChartTimeframe(timeframe);
  try {
    const url = `/api/terminal/cascade/chart?symbol=${encodeURIComponent(symbol)}&mother_timestamp=${encodeURIComponent(timestamp)}&timeframe=${encodeURIComponent(timeframe)}`;
    const res = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status !== 'ok') throw new Error(data?.detail || `Chart failed (${res.status})`);
    _terminalCascadeChartPayload = data;
    _terminalCascadeChartContext = { symbol, timestamp, timeframe: data.timeframe || timeframe };
    if (body) body.innerHTML = _terminalCascadeChartHtml(data);
    const meta = _terminalCascadeEl('terminal-cascade-chart-meta');
    const instrument = data.instrument || {};
    const cands = Array.isArray(data.candles) ? data.candles.length : 0;
    if (meta) {
      const state = String(data.geometry_state || '');
      const endedNote = state === 'MOTHER_BROKEN' ? ' · MOTHER BROKEN — campaign over, structure frozen at the break'
        : state === 'MOTHER_RETESTED' ? ' · MOTHER RETESTED — campaign over, structure frozen at the retest'
        : '';
      meta.textContent = `${instrument.signal_symbol || symbol} -> ${instrument.symbol || symbol} · ${cands} ${data.timeframe || timeframe} candles · ${(data.geometry?.legs || []).length} fib(s), ${(data.geometry?.trendlines || []).length} trendline(s)${endedNote}`;
    }
    _terminalCascadeSetStatus(`${data.instrument?.signal_symbol || symbol} chart loaded.`, 'success');
    _terminalCascadeMountCanvas(data, keepViewSnapshot);
    _terminalCascadeMarkChartTimeframe(data.timeframe || timeframe);
  } catch (error) {
    if (body) body.innerHTML = `<div class="pf-cascade-chart-empty" style="color:var(--danger);">${escapeHtml(error.message || 'Chart unavailable')}</div>`;
    _terminalCascadeSetStatus(error.message || 'Chart unavailable.', 'error');
  }
}

function refreshTerminalCascadeChart() {
  loadTerminalCascadeChart({ keepOpen: true }).catch(() => {});
}

function hideTerminalCascadeChart() {
  _terminalCascadeUnmountCanvas();
  toggleTerminalCascadeFullscreen(false);
  const overlay = _terminalCascadeEl('terminal-cascade-chart-overlay');
  if (overlay) {
    overlay.classList.remove('is-open');
    overlay.setAttribute('aria-hidden', 'true');
  }
  document.body.classList.remove('terminal-cascade-chart-open');
}

function terminalCascadeChartBackdrop(event) {
  if (event && event.target && event.target.id === 'terminal-cascade-chart-overlay') hideTerminalCascadeChart();
}

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  const roundOverlay = _terminalCascadeEl('terminal-cascade-round-overlay');
  if (roundOverlay?.classList.contains('is-open')) { terminalCascadeHideRoundLog(); return; }
  const overlay = _terminalCascadeEl('terminal-cascade-chart-overlay');
  const panel = _terminalCascadeEl('terminal-cascade-chart-panel');
  if (!overlay?.classList.contains('is-open')) return;
  if (panel?.classList.contains('is-fullscreen')) toggleTerminalCascadeFullscreen(false);
  else hideTerminalCascadeChart();
});


function toggleStockOrderMode() {
  const mode = document.getElementById('stock-order-mode')?.value || 'regular';
  const regular = document.getElementById('stock-regular-extra');
  const gtt = document.getElementById('stock-gtt-extra');
  if (regular) regular.style.display = mode === 'regular' ? '' : 'none';
  if (gtt) gtt.style.display = mode === 'gtt' ? '' : 'none';
  const product = document.getElementById('stock-product-type');
  const orderType = document.getElementById('stock-order-type');
  // GTT/Forever only accepts CNC/MTF products and MARKET/LIMIT order types
  // (see the /api/terminal/gtt validators). Hide the rest in GTT mode instead
  // of silently coercing them after the user has already picked one — a hidden
  // swap places a different order than the one on screen.
  const gttProducts = ['CNC', 'MTF'];
  const gttOrderTypes = ['MARKET', 'LIMIT'];
  _setSelectOptionsAllowed(orderType, mode === 'gtt' ? gttOrderTypes : null, 'LIMIT');
  _setSelectOptionsAllowed(product, mode === 'gtt' ? gttProducts : null, 'CNC');
  toggleStockOrderFields();
  updateStockOrderValue();
}

// Show/enable only the allowed option values on a <select>; disable+hide the
// rest. Passing allowed=null clears the restriction (all options usable). If
// the current value falls outside the allowed set, snap to `fallback`.
function _setSelectOptionsAllowed(select, allowed, fallback) {
  if (!select) return;
  Array.from(select.options).forEach(opt => {
    const ok = !allowed || allowed.includes(opt.value);
    opt.disabled = !ok;
    opt.hidden = !ok;
  });
  if (allowed && !allowed.includes(select.value)) select.value = fallback;
}

function toggleStockOrderFields() {
  const mode = document.getElementById('stock-order-mode')?.value || 'regular';
  const orderType = document.getElementById('stock-order-type')?.value || 'MARKET';
  const price = document.getElementById('stock-price');
  const trigger = document.getElementById('stock-trigger-price');
  if (price) {
    price.disabled = mode === 'regular' && (orderType === 'MARKET' || orderType === 'STOP_LOSS_MARKET');
    price.style.opacity = price.disabled ? '0.55' : '';
  }
  if (trigger) {
    trigger.disabled = mode === 'regular' && (orderType === 'MARKET' || orderType === 'LIMIT');
    trigger.style.opacity = trigger.disabled ? '0.55' : '';
  }
  const gttFlag = document.getElementById('stock-gtt-flag')?.value || 'SINGLE';
  ['stock-gtt-price1', 'stock-gtt-trigger1'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.disabled = mode !== 'gtt' || gttFlag !== 'OCO';
    el.style.opacity = el.disabled ? '0.55' : '';
  });
  updateStockOrderValue();
}

function _readStockTerminalPayload(direction) {
  return {
    symbol: document.getElementById('stock-terminal-symbol')?.value || '',
    transaction_type: direction || 'BUY',
    quantity: parseInt(document.getElementById('stock-quantity')?.value, 10) || 0,
    product_type: document.getElementById('stock-product-type')?.value || 'INTRADAY',
    order_type: document.getElementById('stock-order-type')?.value || 'MARKET',
    validity: document.getElementById('stock-validity')?.value || 'DAY',
    price: parseFloat(document.getElementById('stock-price')?.value) || 0,
    trigger_price: parseFloat(document.getElementById('stock-trigger-price')?.value) || 0,
    disclosed_quantity: parseInt(document.getElementById('stock-disclosed-qty')?.value, 10) || 0,
  };
}

async function submitStockTerminalOrder(direction) {
  if (_stockTerminalOrderInFlight) return;
  const statusEl = document.getElementById('stock-terminal-entry-status');
  const mode = document.getElementById('stock-order-mode')?.value || 'regular';
  const payload = _readStockTerminalPayload(direction);
  if (!payload.symbol) {
    if (statusEl) { statusEl.textContent = 'Select a stock first'; statusEl.style.color = 'var(--danger)'; }
    return;
  }
  if (payload.quantity <= 0) {
    if (statusEl) { statusEl.textContent = 'Quantity required'; statusEl.style.color = 'var(--danger)'; }
    return;
  }
  if (mode === 'regular') {
    payload.after_market_order = !!document.getElementById('stock-after-market')?.checked;
    payload.amo_time = document.getElementById('stock-amo-time')?.value || '';
    payload.bo_profit_value = parseFloat(document.getElementById('stock-bo-profit')?.value) || 0;
    payload.bo_stop_loss_value = parseFloat(document.getElementById('stock-bo-sl')?.value) || 0;
    payload.slice_order = !!document.getElementById('stock-slice-order')?.checked;
  } else {
    payload.order_flag = document.getElementById('stock-gtt-flag')?.value || 'SINGLE';
    payload.price1 = parseFloat(document.getElementById('stock-gtt-price1')?.value) || 0;
    payload.trigger_price1 = parseFloat(document.getElementById('stock-gtt-trigger1')?.value) || 0;
    payload.quantity1 = payload.quantity;
    if (!['CNC', 'MTF'].includes(payload.product_type)) payload.product_type = 'CNC';
  }

  const orderLabel = mode === 'gtt' ? 'GTT' : 'regular order';
  const priceContext = _stockTerminalPriceContext();
  const estimatedValue = priceContext.price > 0 ? priceContext.price * payload.quantity : 0;
  const valueText = estimatedValue > 0
    ? `<br><span style="font-size:12px;color:var(--muted);">Price: ₹${priceContext.price.toFixed(2)} (${escapeHtml(priceContext.source)}) · Value: ₹${estimatedValue.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>`
    : '';
  const ok = await customConfirm(
    `Place <strong>${escapeHtml(payload.transaction_type)}</strong> ${escapeHtml(payload.symbol)} x ${payload.quantity} as ${escapeHtml(orderLabel)}?${valueText}`,
    { title: 'Confirm Order', icon: ICO.money(28), okText: 'Place Order', danger: payload.transaction_type === 'SELL' }
  );
  if (!ok) return;

  _stockTerminalOrderInFlight = true;
  const btns = document.querySelectorAll('#stock-terminal-page .btn[onclick*="submitStockTerminalOrder"]');
  btns.forEach(b => { b.disabled = true; b.style.opacity = '0.5'; b.style.pointerEvents = 'none'; });
  if (statusEl) { statusEl.textContent = 'Placing order...'; statusEl.style.color = 'var(--warn)'; }
  try {
    const res = await fetch(mode === 'gtt' ? '/api/terminal/gtt' : '/api/terminal/order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status !== 'ok') {
      throw new Error(_stockTerminalFailureReason(data, `${mode === 'gtt' ? 'GTT' : 'Order'} failed`));
    }
    const broker = data.response || {};
    const brokerStatus = String(broker.orderStatus || broker.status || '').toUpperCase();
    if (['REJECTED', 'FAILED', 'FAILURE', 'ERROR'].includes(brokerStatus)) {
      throw new Error(_stockBrokerOrderReason(broker) || `${mode === 'gtt' ? 'GTT' : 'Order'} ${brokerStatus}`);
    }
    const oid = _stockTerminalOrderId(broker) || 'submitted';
    _updateStockTerminalEntryStatus(oid, brokerStatus || 'SUBMITTED', mode, _stockBrokerOrderReason(broker));
    toast(`${mode === 'gtt' ? 'GTT' : 'Order'} placed for ${payload.symbol}`, 'success');
    refreshStockTerminalOrders();
    if (oid !== 'submitted' && (!brokerStatus || _stockTerminalIsPendingStatus(brokerStatus))) {
      watchStockTerminalOrder(oid, mode);
    }
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = e.message || 'Order failed';
      statusEl.style.color = 'var(--danger)';
    }
    toast(e.message || 'Order failed', 'error');
  } finally {
    _stockTerminalOrderInFlight = false;
    btns.forEach(b => { b.disabled = false; b.style.opacity = ''; b.style.pointerEvents = ''; });
  }
}

function _stockOrderStatusTone(status) {
  const s = String(status || '').toUpperCase();
  if (_stockTerminalIsSuccessStatus(s)) return 'var(--success)';
  if (_stockTerminalIsFailureStatus(s)) return 'var(--danger)';
  return 'var(--warn)';
}

function _canCancelBrokerOrder(status) {
  return ['TRANSIT', 'PENDING', 'CONFIRM'].includes(String(status || '').toUpperCase());
}

function _stockTerminalOrderId(order) {
  return String(order?.orderId || order?.order_id || order?.id || order?.orderNo || '').trim();
}

function _stockTerminalOrderStatus(order) {
  return String(order?.orderStatus || order?.status || '').trim().toUpperCase();
}

function _stockTerminalIsFailureStatus(status) {
  return ['REJECTED', 'CANCELLED', 'EXPIRED', 'FAILED', 'FAILURE', 'ERROR'].includes(String(status || '').toUpperCase());
}

function _stockTerminalIsSuccessStatus(status) {
  return ['TRADED', 'FILLED', 'COMPLETE', 'COMPLETED'].includes(String(status || '').toUpperCase());
}

function _stockTerminalIsPendingStatus(status) {
  return ['', 'TRANSIT', 'PENDING', 'OPEN', 'CONFIRM', 'PART_TRADED', 'VALIDATION_PENDING', 'VALIDATION PENDING'].includes(String(status || '').toUpperCase());
}

const _STOCK_TERMINAL_REASON_KEYS = [
  'rejectionReason', 'omsErrorDescription', 'reason', 'errorMessage', 'error_message',
  'message', 'detail', 'remarks', 'description'
];

function _stockTerminalCleanReason(value) {
  const text = String(value ?? '').trim();
  if (!text || ['failure', 'failed', 'error', 'none', 'null', 'undefined'].includes(text.toLowerCase())) return '';
  if (text[0] === '{' || text[0] === '[') {
    try {
      return _stockTerminalReasonFromObject(JSON.parse(text)) || text;
    } catch (e) {
      // Fall through to regex extraction.
    }
  }
  const match = text.match(/"(?:rejectionReason|omsErrorDescription|reason|errorMessage|message|detail)"\s*:\s*"([^"]+)"/i);
  if (match && match[1]) return _stockTerminalCleanReason(match[1]);
  return text.replace(/\s+/g, ' ');
}

function _stockTerminalReasonFromObject(value, seen = new Set()) {
  if (value == null) return '';
  if (typeof value === 'string' || typeof value === 'number') return _stockTerminalCleanReason(value);
  if (typeof value !== 'object') return '';
  if (seen.has(value)) return '';
  seen.add(value);
  if (Array.isArray(value)) {
    for (const item of value) {
      const reason = _stockTerminalReasonFromObject(item, seen);
      if (reason) return reason;
    }
    return '';
  }
  for (const key of _STOCK_TERMINAL_REASON_KEYS) {
    if (Object.prototype.hasOwnProperty.call(value, key)) {
      const reason = _stockTerminalReasonFromObject(value[key], seen);
      if (reason) return reason;
    }
  }
  if (value.data && typeof value.data === 'object') {
    const reason = _stockTerminalReasonFromObject(value.data, seen);
    if (reason) return reason;
  }
  return '';
}

function _stockTerminalFailureReason(data, fallback = 'Order failed') {
  return _stockTerminalReasonFromObject(data) || fallback;
}

function _stockBrokerOrderReason(order) {
  const reason = _stockTerminalReasonFromObject(order);
  if (!reason) return '';
  const status = _stockTerminalOrderStatus(order);
  if (!status || _stockTerminalIsFailureStatus(status)) return reason;
  if (/(reject|fail|invalid|insufficient|rms|margin|not allowed|exceed|freeze|tick|price|quantity)/i.test(reason)) return reason;
  return '';
}

function _updateStockTerminalEntryStatus(orderId, status, mode, reason = '') {
  const statusEl = document.getElementById('stock-terminal-entry-status');
  if (!statusEl) return;
  const cleanStatus = String(status || 'SUBMITTED').toUpperCase();
  const label = mode === 'gtt' ? 'GTT' : 'Order';
  const suffix = reason ? ` - ${reason}` : (_stockTerminalIsPendingStatus(cleanStatus) ? ' - awaiting broker confirmation' : '');
  statusEl.textContent = `${label} ${orderId || ''} ${cleanStatus}${suffix}`.trim();
  statusEl.style.color = _stockTerminalIsFailureStatus(cleanStatus)
    ? 'var(--danger)'
    : (_stockTerminalIsPendingStatus(cleanStatus) ? 'var(--warn)' : 'var(--success)');
}

async function _fetchStockTerminalTrackedOrder(orderId, mode) {
  const target = String(orderId || '').trim();
  if (!target) return null;
  if (mode === 'regular') {
    try {
      const res = await fetch('/api/orders/' + encodeURIComponent(target) + '/status', { cache: 'no-store' });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.status === 'success' && data.data && typeof data.data === 'object') {
        if (!_stockTerminalOrderId(data.data)) data.data.orderId = target;
        return data.data;
      }
    } catch (e) {
      // Fall back to the order book below.
    }
  }
  const url = mode === 'gtt' ? '/api/terminal/forever' : '/api/orders';
  const data = await fetch(url, { cache: 'no-store' }).then(r => r.json()).catch(() => ({ data: [] }));
  const rows = Array.isArray(data.data) ? data.data : [];
  return rows.find(o => _stockTerminalOrderId(o) === target) || null;
}

function watchStockTerminalOrder(orderId, mode) {
  if (_stockTerminalOrderWatchTimer) clearTimeout(_stockTerminalOrderWatchTimer);
  let attempts = 0;
  const maxAttempts = mode === 'gtt' ? 8 : 16;
  const tick = async () => {
    attempts += 1;
    try {
      const order = await _fetchStockTerminalTrackedOrder(orderId, mode);
      if (order) {
        const status = _stockTerminalOrderStatus(order) || 'SUBMITTED';
        const reason = _stockBrokerOrderReason(order);
        _updateStockTerminalEntryStatus(orderId, status, mode, reason);
        refreshStockTerminalOrders();
        if (_stockTerminalIsFailureStatus(status)) {
          toast(`Order ${status}: ${reason || 'No broker reason returned'}`, 'error');
          _stockTerminalOrderWatchTimer = null;
          return;
        }
        if (_stockTerminalIsSuccessStatus(status)) {
          _stockTerminalOrderWatchTimer = null;
          return;
        }
      }
    } catch (e) {
      // Keep the existing placement status visible if the poll fails.
    }
    if (attempts < maxAttempts) {
      _stockTerminalOrderWatchTimer = setTimeout(tick, 2500);
    } else {
      _stockTerminalOrderWatchTimer = null;
    }
  };
  tick();
}

async function refreshStockTerminalOrders() {
  const ordersBody = document.getElementById('stock-terminal-orders-body');
  const gttBody = document.getElementById('stock-terminal-gtt-body');
  try {
    const [ordersRes, gttRes] = await Promise.all([
      fetch('/api/orders', { cache: 'no-store' }).then(r => r.json()).catch(() => ({ status: 'error', data: [] })),
      fetch('/api/terminal/forever', { cache: 'no-store' }).then(r => r.json()).catch(() => ({ status: 'error', data: [] })),
    ]);
    const orders = Array.isArray(ordersRes.data) ? ordersRes.data : [];
    const gtt = Array.isArray(gttRes.data) ? gttRes.data : [];
    // A live terminal must never dress "can't see orders" up as "no orders" —
    // that invites a duplicate order. Only status:"success" means the empty
    // list is real; anything else is a broker/token fault worth showing loud.
    const ordersOk = ordersRes.status === 'success';
    const gttOk = gttRes.status === 'success';
    if (ordersBody && !ordersOk) {
      const msg = _apiErrorMessage(ordersRes, 'Broker unavailable — orders could not be loaded');
      ordersBody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:18px;color:var(--danger);">${escapeHtml(msg)}</td></tr>`;
    } else if (ordersBody) {
      const latest = orders.slice().reverse().slice(0, 12);
      ordersBody.innerHTML = latest.length ? latest.map(o => {
        const status = o.orderStatus || o.status || '';
        const reason = _stockBrokerOrderReason(o);
        const orderId = _stockTerminalOrderId(o);
        const cancelBtn = _canCancelBrokerOrder(status) && orderId
          ? `<button class="btn btn-danger btn-sm" onclick="cancelStockTerminalOrder('${escapeJsAttr(orderId)}','regular')" style="padding:2px 8px;font-size:10px;">Cancel</button>`
          : '<span style="color:var(--muted);">—</span>';
        return `<tr style="border-bottom:1px solid rgba(255,255,255,0.03);">
          <td style="padding:7px 8px;font-family:'JetBrains Mono',monospace;">${escapeHtml(o.tradingSymbol || o.securityId || '-')}</td>
          <td style="padding:7px 8px;text-align:center;color:${o.transactionType === 'SELL' ? 'var(--danger)' : 'var(--success)'};">${escapeHtml(o.transactionType || '')}</td>
          <td style="padding:7px 8px;text-align:center;">${escapeHtml(o.orderType || '')}</td>
          <td style="padding:7px 8px;text-align:right;font-family:'JetBrains Mono',monospace;">${escapeHtml(o.quantity || o.orderQuantity || 0)}</td>
          <td style="padding:7px 8px;text-align:center;color:${_stockOrderStatusTone(status)};">${escapeHtml(status)}</td>
          <td title="${escapeAttr(reason || '')}" style="padding:7px 8px;max-width:190px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:${reason ? 'var(--text-dim)' : 'var(--muted)'};">${escapeHtml(reason || '—')}</td>
          <td style="padding:7px 8px;text-align:center;">${cancelBtn}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="7" style="text-align:center;padding:18px;color:var(--muted);">No orders today</td></tr>';
    }
    if (gttBody && !gttOk) {
      const msg = _apiErrorMessage(gttRes, 'Broker unavailable — GTT orders could not be loaded');
      gttBody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:18px;color:var(--danger);">${escapeHtml(msg)}</td></tr>`;
    } else if (gttBody) {
      const latestGtt = gtt.slice().reverse().slice(0, 12);
      gttBody.innerHTML = latestGtt.length ? latestGtt.map(o => {
        const status = o.orderStatus || o.status || '';
        const reason = _stockBrokerOrderReason(o);
        const orderId = _stockTerminalOrderId(o);
        const cancelBtn = _canCancelBrokerOrder(status) && orderId
          ? `<button class="btn btn-danger btn-sm" onclick="cancelStockTerminalOrder('${escapeJsAttr(orderId)}','gtt')" style="padding:2px 8px;font-size:10px;">Cancel</button>`
          : '<span style="color:var(--muted);">—</span>';
        return `<tr style="border-bottom:1px solid rgba(255,255,255,0.03);">
          <td style="padding:7px 8px;font-family:'JetBrains Mono',monospace;">${escapeHtml(o.tradingSymbol || o.securityId || '-')}</td>
          <td style="padding:7px 8px;text-align:center;color:${o.transactionType === 'SELL' ? 'var(--danger)' : 'var(--success)'};">${escapeHtml(o.transactionType || '')}</td>
          <td style="padding:7px 8px;text-align:center;">${escapeHtml(o.orderFlag || o.orderType || '')}</td>
          <td style="padding:7px 8px;text-align:right;font-family:'JetBrains Mono',monospace;">₹${Number(o.triggerPrice || 0).toFixed(2)}</td>
          <td style="padding:7px 8px;text-align:center;color:${_stockOrderStatusTone(status)};">${escapeHtml(status)}</td>
          <td title="${escapeAttr(reason || '')}" style="padding:7px 8px;max-width:190px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:${reason ? 'var(--text-dim)' : 'var(--muted)'};">${escapeHtml(reason || '—')}</td>
          <td style="padding:7px 8px;text-align:center;">${cancelBtn}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="7" style="text-align:center;padding:18px;color:var(--muted);">No GTT orders</td></tr>';
    }
  } catch (e) {
    if (ordersBody) ordersBody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:18px;color:var(--danger);">Orders unavailable</td></tr>';
    if (gttBody) gttBody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:18px;color:var(--danger);">GTT unavailable</td></tr>';
  }
}

async function cancelStockTerminalOrder(orderId, mode) {
  const ok = await customConfirm('Cancel order <strong>' + escapeHtml(orderId) + '</strong>?', { title: 'Cancel Order', icon: ICO.trash(28), okText: 'Cancel Order', danger: true });
  if (!ok) return;
  try {
    const url = mode === 'gtt' ? '/api/terminal/forever/' + encodeURIComponent(orderId) : '/api/orders/' + encodeURIComponent(orderId);
    const res = await fetch(url, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(_apiErrorMessage(data, 'Cancel failed'));
    toast('Order cancelled', 'success');
    refreshStockTerminalOrders();
  } catch (e) {
    toast(e.message || 'Cancel failed', 'error');
  }
}

// ══════════════════════════════════════════════════════════════
//  SCALP PAGE
// ══════════════════════════════════════════════════════════════
let _scalpPollTimer = null;
let _scalpLTPTimer = null;
let _scalpFormInitialized = false;
let _scalpRestoringState = false;
const _SCALP_FORM_STORAGE_KEY = 'philforge_scalp_form_state_v1';
// Correct lot sizes as of Jan 2026
const _LOT_SIZES = { NIFTY: 65, BANKNIFTY: 30, MIDCPNIFTY: 50, SENSEX: 20 };
// Strike step intervals per underlying
const _STRIKE_STEPS = { NIFTY: 50, BANKNIFTY: 100, MIDCPNIFTY: 25, SENSEX: 100 };

function _readScalpFormState() {
  const read = (id) => document.getElementById(id)?.value ?? '';
  return {
    underlying: read('scalp-underlying') || 'NIFTY',
    expiry: read('scalp-expiry'),
    product_type: read('scalp-product-type') || 'INTRADAY',
    option_type: read('scalp-option-type') || 'CE',
    strike: read('scalp-strike'),
    lots: read('scalp-lots'),
    sl_rs: read('scalp-sl-rs'),
    target_rs: read('scalp-target-rs'),
    sl_prem: read('scalp-sl-prem'),
    target_prem: read('scalp-target-prem'),
    mode: read('scalp-mode') || 'paper',
    entry_limit_price: read('scalp-limit-price'),
    entry_limit_max: read('scalp-limit-max'),
  };
}

function _persistScalpFormState() {
  if (_scalpRestoringState) return;
  try {
    _setLocalState(_SCALP_FORM_STORAGE_KEY, JSON.stringify(_readScalpFormState()));
  } catch (e) {}
}

function _loadSavedScalpFormState() {
  try {
    const raw = _getLocalState(_SCALP_FORM_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch (e) {
    return null;
  }
}

function _applyScalpFieldValue(id, value) {
  const el = document.getElementById(id);
  if (!el || value === undefined || value === null || value === '') return;
  el.value = value;
}

async function _restoreScalpFormState() {
  const saved = _loadSavedScalpFormState();
  if (!saved) {
    await loadScalpExpiries();
    return;
  }
  _scalpRestoringState = true;
  try {
    _applyScalpFieldValue('scalp-underlying', saved.underlying);
    _applyScalpFieldValue('scalp-product-type', saved.product_type);
    _applyScalpFieldValue('scalp-option-type', saved.option_type);
    _applyScalpFieldValue('scalp-mode', saved.mode);
    _syncScalpToggleGroup('scalp-option-toggle', 'scalp-option-type');
    _syncScalpToggleGroup('scalp-mode-toggle', 'scalp-mode');
    _applyScalpFieldValue('scalp-lots', saved.lots);
    _applyScalpFieldValue('scalp-sl-rs', saved.sl_rs);
    _applyScalpFieldValue('scalp-target-rs', saved.target_rs);
    _applyScalpFieldValue('scalp-sl-prem', saved.sl_prem);
    _applyScalpFieldValue('scalp-target-prem', saved.target_prem);
    _applyScalpFieldValue('scalp-limit-price', saved.entry_limit_price);
    _applyScalpFieldValue('scalp-limit-max', saved.entry_limit_max);
    await loadScalpExpiries({ preserveState: saved });
  } finally {
    _scalpRestoringState = false;
  }
  _persistScalpFormState();
}

async function initScalpPage() {
  _syncScalpToggleGroup('scalp-option-toggle', 'scalp-option-type');
  _syncScalpToggleGroup('scalp-mode-toggle', 'scalp-mode');
  await _restoreScalpFormState();
  _scalpFormInitialized = true;
  refreshScalpStatus();
  // HTTP poll as fallback — wider interval since WS pushes every 3s
  if (!_scalpPollTimer) {
    _scalpPollTimer = setInterval(() => {
      if (!_isPageVisible() || !_isPageActive('scalp-page')) return;
      refreshScalpStatus();
    }, _ws && _ws.readyState === 1 ? 10000 : 3000);
  }
  if (!_scalpLTPTimer) {
    // REST poll as fallback — 5s since broker-level throttle + WS push handle freshness
    _scalpLTPTimer = setInterval(() => {
      if (!_isPageVisible() || !_isPageActive('scalp-page')) return;
      fetchScalpLTP();
    }, 5000);
  }
}

// Spot price cache from /api/ticker (keyed by underlying)
var _scalpSpotCache = {};

function _syncScalpToggleGroup(groupId, inputId) {
  const group = document.getElementById(groupId);
  const input = document.getElementById(inputId);
  if (!group || !input) return;
  group.querySelectorAll('.scalp-toggle-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.value === input.value);
  });
}

function setScalpOptionType(value) {
  const input = document.getElementById('scalp-option-type');
  if (!input || input.value === value) return;
  input.value = value;
  _syncScalpToggleGroup('scalp-option-toggle', 'scalp-option-type');
  updateScalpStrike();
  fetchScalpLTPThrottled();
  _persistScalpFormState();
}

function setScalpMode(value) {
  const input = document.getElementById('scalp-mode');
  if (!input || input.value === value) return;
  input.value = value;
  _syncScalpToggleGroup('scalp-mode-toggle', 'scalp-mode');
  _persistScalpFormState();
}

function stepScalpStrike(dir) {
  const underlying = document.getElementById('scalp-underlying').value;
  const step = _STRIKE_STEPS[underlying] || 50;
  const inp = document.getElementById('scalp-strike');
  const cur = parseInt(inp.value) || 0;
  if (cur === 0) return;  // Don't step from 0 — wait for auto-populate
  inp.value = cur + dir * step;
  fetchScalpLTP();
  _persistScalpFormState();
}

// Track user-edited scalp inputs — suppress WS/REST overwrites for 5s
const _scalpDirtyInputs = {};
const _SCALP_DIRTY_TTL = 5000; // ms
function _markScalpInputDirty(inputId) { _scalpDirtyInputs[inputId] = Date.now(); }
function _isScalpInputDirty(inputId) { return _scalpDirtyInputs[inputId] && (Date.now() - _scalpDirtyInputs[inputId] < _SCALP_DIRTY_TTL); }
function _clearScalpInputDirty(tradeId) {
  delete _scalpDirtyInputs['scalp-tgt-' + tradeId];
  delete _scalpDirtyInputs['scalp-sl-' + tradeId];
  delete _scalpDirtyInputs['scalp-entry-min-' + tradeId];
  delete _scalpDirtyInputs['scalp-entry-max-' + tradeId];
}

function stepScalpField(inputId, delta) {
  const inp = document.getElementById(inputId);
  if (!inp) return;
  const cur = parseFloat(inp.value) || 0;
  inp.value = Math.max(0, cur + delta);
  _markScalpInputDirty(inputId);
}

// Also mark dirty on manual typing (delegated to scalp-active-body)
document.addEventListener('input', function(e) {
  if (e.target && e.target.id && (
    e.target.id.startsWith('scalp-tgt-') ||
    e.target.id.startsWith('scalp-sl-') ||
    e.target.id.startsWith('scalp-entry-min-') ||
    e.target.id.startsWith('scalp-entry-max-')
  )) {
    _markScalpInputDirty(e.target.id);
  }
});

let _ltpAbort = null;
var _scalpCurrentLTP = 0;
var _scalpLTPFromWS = 0;  // timestamp (ms) of last WS-driven LTP update
let _ltpThrottleTimer = null;

// Throttled wrapper: ensures fetchScalpLTP fires at most once per 1s
function fetchScalpLTPThrottled() {
  if (_ltpThrottleTimer) return;  // already scheduled
  _ltpThrottleTimer = setTimeout(() => { _ltpThrottleTimer = null; fetchScalpLTP(); }, 1000);
}

async function fetchScalpLTP() {
  const el = document.getElementById('scalp-live-ltp');
  const strike = parseInt(document.getElementById('scalp-strike').value) || 0;
  const underlying = document.getElementById('scalp-underlying').value;
  const optType = document.getElementById('scalp-option-type').value;
  const expiry = document.getElementById('scalp-expiry').value;
  if (!strike || !expiry) { el.textContent = '—'; el.style.color = 'var(--muted)'; _scalpCurrentLTP = 0; updateScalpMargin(); return; }

  // Skip REST call if WS provided a fresh price within the last 2 seconds
  // (avoids Dhan rate limits while WS is actively pushing open-trade data)
  if (_scalpLTPFromWS && (Date.now() - _scalpLTPFromWS < 5000) && _scalpCurrentLTP > 0) {
    return;
  }

  // Strategy 1: Try to pull LTP from an open trade matching this contract
  // (zero-cost — already fetched by the 3s status poll via WS/batch feed)
  if (_lastScalpStatus && _lastScalpStatus.open_trades) {
    const match = _lastScalpStatus.open_trades.find(t =>
      t.underlying === underlying && t.strike === strike &&
      t.option_type === optType && t.expiry === expiry &&
      t.current_premium > 0
    );
    if (match) {
      _scalpCurrentLTP = match.current_premium;
      el.textContent = '₹' + _scalpCurrentLTP.toFixed(2);
      el.style.color = 'var(--green)';
      updateScalpMargin();
      return;
    }
  }

  // Strategy 2: REST API call (may hit Dhan rate limits)
  el.textContent = '…';
  el.style.color = 'var(--muted)';
  if (_ltpAbort) _ltpAbort.abort();
  _ltpAbort = new AbortController();
  try {
    const r = await fetch(`/api/option-ltp?underlying=${underlying}&strike=${strike}&expiry=${encodeURIComponent(expiry)}&option_type=${optType}`, { signal: _ltpAbort.signal });
    const d = await r.json();
    if (d.status === 'ok' && d.ltp > 0) {
      el.textContent = '₹' + d.ltp.toFixed(2);
      el.style.color = 'var(--green)';
      _scalpCurrentLTP = d.ltp;
    } else {
      // Fallback: keep last known LTP if we had one, show N/A only if truly unknown
      if (_scalpCurrentLTP > 0) {
        el.textContent = '₹' + _scalpCurrentLTP.toFixed(2);
        el.style.color = 'rgba(52,211,153,0.5)';
      } else {
        el.textContent = 'N/A';
        el.style.color = 'var(--muted)';
      }
    }
  } catch(e) {
    if (e.name !== 'AbortError') {
      if (_scalpCurrentLTP > 0) {
        el.textContent = '₹' + _scalpCurrentLTP.toFixed(2);
        el.style.color = 'rgba(52,211,153,0.5)';
      } else {
        el.textContent = 'N/A';
        el.style.color = 'var(--muted)';
      }
    }
  }
  updateScalpMargin();
}

// ── Margin caching ──
// Margin follows both the selected contract inputs and the displayed premium.
// This keeps the UI internally consistent when live premium moves via REST or WS.
var _marginCache = { underlying: '', strike: 0, optType: '', lots: 0, lotSize: 0, ltp: 0, value: null };

function updateScalpMargin(forceRecalc) {
  const el = document.getElementById('scalp-margin');
  const underlying = document.getElementById('scalp-underlying').value;
  const strike = parseInt(document.getElementById('scalp-strike').value) || 0;
  const optType = document.getElementById('scalp-option-type').value;
  const lots = parseInt(document.getElementById('scalp-lots').value) || 0;
  const lotSize = parseInt(document.getElementById('scalp-lot-size').value) || 0;

  // Check whether any input used by the displayed estimate changed.
  const paramsChanged = (
    underlying !== _marginCache.underlying ||
    strike !== _marginCache.strike ||
    optType !== _marginCache.optType ||
    lots !== _marginCache.lots ||
    lotSize !== _marginCache.lotSize ||
    _scalpCurrentLTP !== _marginCache.ltp
  );

  if (!paramsChanged && !forceRecalc && _marginCache.value !== null) {
    // Contract unchanged — show cached margin, skip recalc
    return;
  }

  // Update cache key
  _marginCache.underlying = underlying;
  _marginCache.strike = strike;
  _marginCache.optType = optType;
  _marginCache.lots = lots;
  _marginCache.lotSize = lotSize;
  _marginCache.ltp = _scalpCurrentLTP;

  if (_scalpCurrentLTP > 0 && lots > 0 && lotSize > 0) {
    const margin = _scalpCurrentLTP * lots * lotSize;
    _marginCache.value = margin;
    el.textContent = '₹' + margin.toLocaleString('en-IN', { maximumFractionDigits: 0 });
    el.style.color = 'var(--warn)';
  } else {
    _marginCache.value = null;
    el.textContent = lots > 0 ? 'N/A' : '—';
    el.style.color = 'var(--muted)';
  }
}

function _scalpITMStrike(spotPrice, underlying, optionType) {
  const step = _STRIKE_STEPS[underlying] || 50;
  const atm = Math.round(spotPrice / step) * step;
  // Default to 2 strikes in-the-money.
  return optionType === 'CE' ? atm - 2 * step : atm + 2 * step;
}

function updateScalpStrike() {
  const underlying = document.getElementById('scalp-underlying').value;
  const optType = document.getElementById('scalp-option-type').value;
  const spot = _scalpSpotCache[underlying] || 0;
  if (spot > 0) {
    document.getElementById('scalp-strike').value = _scalpITMStrike(spot, underlying, optType);
  }
  fetchScalpLTP();
}

async function _fetchScalpSpotPrices() {
  try {
    const r = await fetch(`/api/ticker?_=${Date.now()}`, { cache: 'no-store' });
    const d = await r.json();
    if (d.status === 'ok') {
      if (d.nifty) _scalpSpotCache['NIFTY'] = d.nifty.price;
      if (d.banknifty) _scalpSpotCache['BANKNIFTY'] = d.banknifty.price;
      if (d.midcpnifty) _scalpSpotCache['MIDCPNIFTY'] = d.midcpnifty.price;
      if (d.sensex) _scalpSpotCache['SENSEX'] = d.sensex.price;
    }
  } catch(e) { /* silent */ }
}

async function loadScalpExpiries(options = {}) {
  try {
    const preserve = options && options.preserveState ? options.preserveState : null;
    const underlying = document.getElementById('scalp-underlying').value;
    // Update lot size (read-only, auto)
    const ls = document.getElementById('scalp-lot-size');
    const lsLabel = document.getElementById('scalp-lot-size-label');
    const lotVal = _LOT_SIZES[underlying] || 65;
    if (ls) ls.value = lotVal;
    if (lsLabel) lsLabel.textContent = lotVal;
    // Fetch actual expiry dates from scrip master
    const res = await fetch(`/api/expiry-list/${underlying}`);
    const data = await res.json();
    const sel = document.getElementById('scalp-expiry');
    if (!sel) return;
    sel.innerHTML = '';
    sel.dataset.underlying = underlying;
    const expiries = (data.status === 'ok' && data.expiries) ? data.expiries : [];
    // Show up to 8 nearest real expiry dates
    expiries.slice(0, 8).forEach(d => {
      const opt = document.createElement('option');
      opt.value = d;
      const dt = new Date(d + 'T00:00:00');
      const dayName = dt.toLocaleDateString('en-IN', { weekday: 'short' });
      const label = dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' });
      opt.textContent = `${dayName} ${label}`;
      sel.appendChild(opt);
    });
    const preferredExpiry = preserve?.expiry || '';
    if (preferredExpiry && expiries.includes(preferredExpiry)) {
      sel.value = preferredExpiry;
    } else if (sel.options.length > 0) {
      sel.selectedIndex = 0;
    }
    // Set ITM-2 default strike from live spot per underlying
    await _fetchScalpSpotPrices();
    const preservedStrike = parseInt(preserve?.strike, 10) || 0;
    if (preservedStrike > 0) {
      document.getElementById('scalp-strike').value = preservedStrike;
      fetchScalpLTP();
    } else {
      updateScalpStrike();
    }
    updateScalpMargin(true);
    _persistScalpFormState();
  } catch(e) { /* silent */ }
}

const _SCALP_FORM_IDS = new Set([
  'scalp-underlying',
  'scalp-expiry',
  'scalp-product-type',
  'scalp-option-type',
  'scalp-strike',
  'scalp-lots',
  'scalp-sl-rs',
  'scalp-target-rs',
  'scalp-sl-prem',
  'scalp-target-prem',
  'scalp-mode',
  'scalp-limit-price',
  'scalp-limit-max',
]);

document.addEventListener('change', function(e) {
  const id = e.target && e.target.id;
  if (!_SCALP_FORM_IDS.has(id)) return;
  if (id === 'scalp-expiry') fetchScalpLTPThrottled();
  _persistScalpFormState();
});

document.addEventListener('input', function(e) {
  const id = e.target && e.target.id;
  if (!_SCALP_FORM_IDS.has(id)) return;
  _persistScalpFormState();
});

async function startScalpEngine() {
  const startBtn = document.getElementById('scalp-start-btn');
  if (startBtn) startBtn.disabled = true;
  try {
    const res = await fetch('/api/scalp/start', { method: 'POST' });
    const data = await res.json();
    if (!res.ok || data.status !== 'started') throw new Error(data.detail || data.message || 'Failed to start engine');
    toast('Scalp engine started', 'success');
    _applyScalpEngineState(true);
    refreshScalpStatus();
  } catch(e) {
    if (startBtn) startBtn.disabled = false;
    toast(e.message || 'Failed to start engine', 'error');
  }
}

async function stopScalpEngine() {
  const stopBtn = document.getElementById('scalp-stop-btn');
  if (stopBtn) stopBtn.disabled = true;
  try {
    const res = await fetch('/api/scalp/stop', { method: 'POST' });
    const data = await res.json();
    if (!res.ok || data.status !== 'stopped') throw new Error(data.detail || data.message || 'Failed to stop engine');
    toast('Scalp engine stopped', 'success');
    _applyScalpEngineState(false);
    refreshScalpStatus();
  } catch(e) {
    if (stopBtn) stopBtn.disabled = false;
    toast(e.message || 'Failed to stop engine', 'error');
    refreshScalpStatus();
  }
}

let _scalpEntryInFlight = false;
async function submitScalpEntry(direction) {
  if (_scalpEntryInFlight) return;  // prevent double-click
  _scalpEntryInFlight = true;
  // Disable BUY/SELL buttons while order is in flight
  const btns = document.querySelectorAll('#scalp-page .btn[onclick*="submitScalpEntry"]');
  btns.forEach(b => { b.disabled = true; b.style.opacity = '0.5'; b.style.pointerEvents = 'none'; });
  const _enableBtns = () => { _scalpEntryInFlight = false; btns.forEach(b => { b.disabled = false; b.style.opacity = ''; b.style.pointerEvents = ''; }); };
  const statusEl = document.getElementById('scalp-entry-status');
  statusEl.textContent = 'Placing order...';
  statusEl.style.color = 'var(--warn)';
  try {
    const payload = {
      underlying: document.getElementById('scalp-underlying').value,
      strike: parseInt(document.getElementById('scalp-strike').value),
      option_type: document.getElementById('scalp-option-type').value,
      expiry: document.getElementById('scalp-expiry').value,
      product_type: document.getElementById('scalp-product-type').value || 'INTRADAY',
      transaction_type: direction || 'BUY',
      lots: parseInt(document.getElementById('scalp-lots').value),
      lot_size: parseInt(document.getElementById('scalp-lot-size').value),
      target_rupees: parseFloat(document.getElementById('scalp-target-rs').value) || 0,
      sl_rupees: parseFloat(document.getElementById('scalp-sl-rs').value) || 0,
      target_premium: parseFloat(document.getElementById('scalp-target-prem').value) || 0,
      sl_premium: parseFloat(document.getElementById('scalp-sl-prem').value) || 0,
      sqoff_time: '',
      mode: document.getElementById('scalp-mode').value,
      entry_limit_price: parseFloat(document.getElementById('scalp-limit-price').value) || 0,
      entry_limit_max: parseFloat(document.getElementById('scalp-limit-max').value) || 0,
    };
    if (!payload.strike || payload.strike <= 0) {
      statusEl.textContent = 'Strike price required';
      statusEl.style.color = 'var(--danger)';
      return;
    }
    if (payload.mode === 'live' && (payload.target_premium <= 0 || payload.sl_premium <= 0)) {
      statusEl.textContent = 'Live mode needs both Target Premium and SL Premium for Dhan Super Order';
      statusEl.style.color = 'var(--danger)';
      return;
    }
    // Pre-flight validation: warn if SL premium is on the wrong side of entry
    const liveLtp = parseFloat(document.getElementById('scalp-live-ltp')?.textContent?.replace(/[₹,]/g, '')) || 0;
    if (liveLtp > 0 && payload.sl_premium > 0) {
      const bad = (payload.transaction_type === 'BUY' && payload.sl_premium >= liveLtp)
               || (payload.transaction_type === 'SELL' && payload.sl_premium <= liveLtp);
      if (bad) {
        statusEl.textContent = `⚠️ SL ₹${payload.sl_premium} is on the wrong side of LTP ₹${liveLtp.toFixed(2)} — SL will trigger immediately!`;
        statusEl.style.color = 'var(--danger)';
        const proceed = await customConfirm(
          `<strong style="color:var(--danger)">WARNING:</strong> Your SL Premium (₹${payload.sl_premium}) will trigger immediately!<br><br>` +
          `For a ${payload.transaction_type} at ₹${liveLtp.toFixed(2)}:<br>` +
          `• SL should be ${payload.transaction_type === 'BUY' ? 'BELOW' : 'ABOVE'} entry price.<br><br>` +
          `Did you mean to use "SL ₹ (max loss)" instead?`,
          { title: 'SL Warning', okText: 'Submit Anyway', danger: true }
        );
        if (!proceed) return;
      }
    }

    const res = await fetch('/api/scalp/entry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.status === 'ok') {
      if (data.trade?.status === 'pending') {
        statusEl.textContent = `⏳ Stop-limit #${data.trade_id} pending — trigger ₹${data.trade.entry_limit_price.toFixed(2)}–₹${data.trade.entry_limit_max.toFixed(2)}`;
        statusEl.style.color = 'rgba(139,143,255,0.9)';
      } else {
        statusEl.textContent = `✅ Trade #${data.trade_id} entered @ ₹${(data.trade?.entry_premium || 0).toFixed(2)}`;
        statusEl.style.color = 'var(--success)';
      }
      // Immediately inject the new trade into Active Positions so it's
      // visible without waiting for the next 3-second poll cycle.
      if (data.trade && _lastScalpStatus) {
        if (!_lastScalpStatus.open_trades) _lastScalpStatus.open_trades = [];
        const alreadyExists = _lastScalpStatus.open_trades.some(t => t.trade_id === data.trade.trade_id);
        if (!alreadyExists) {
          _lastScalpStatus.open_trades.push(data.trade);
          _renderScalpStatus(_lastScalpStatus);
        }
      }
      // Also trigger a full refresh to get authoritative server state
      refreshScalpStatus();
    } else {
      statusEl.textContent = '❌ ' + (data.error?.detail || data.error?.message || data.message || data.detail || 'Entry failed');
      statusEl.style.color = 'var(--danger)';
    }
  } catch(e) {
    statusEl.textContent = '❌ Error: ' + e.message;
    statusEl.style.color = 'var(--danger)';
  } finally {
    _enableBtns();
  }
}

async function exitScalpTrade(tradeId) {
  try {
    const res = await fetch('/api/scalp/exit/' + tradeId, { method: 'POST' });
    const data = await res.json();
    if (data.status === 'ok') {
      const pnl = data.trade?.pnl || 0;
      toast(`Exited trade #${tradeId} | P&L: ₹${pnl.toFixed(2)}`, pnl >= 0 ? 'success' : 'error');
      refreshScalpStatus();
    } else {
      toast(data.message || 'Exit failed', 'error');
    }
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

async function killAllScalpTrades() {
  const ok = await customConfirm('<strong style="color:var(--danger)">KILL ALL</strong> — This will immediately exit <strong>ALL</strong> open scalp trades.', { title: 'Kill All Trades', okText: 'Kill All', danger: true });
  if (!ok) return;
  const btn = document.getElementById('scalp-kill-all-btn');
  if (btn) { btn.disabled = true; btn.style.opacity = '0.5'; }
  try {
    const res = await fetch('/api/scalp/kill-all', { method: 'POST' });
    const data = await res.json();
    if (data.status === 'ok') {
      toast(`🔴 Killed ${data.closed} trade(s)`, data.closed > 0 ? 'warning' : 'info');
      refreshScalpStatus();
    } else {
      toast(data.message || 'Kill all failed', 'error');
    }
  } catch(e) { toast('Error: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.style.opacity = ''; } }
}

async function modifyScalpTrade(tradeId) {
  const btn = document.getElementById('scalp-set-btn-' + tradeId);
  const tgtInput = document.getElementById('scalp-tgt-' + tradeId);
  const slInput = document.getElementById('scalp-sl-' + tradeId);
  const entryMinInput = document.getElementById('scalp-entry-min-' + tradeId);
  const entryMaxInput = document.getElementById('scalp-entry-max-' + tradeId);
  if (!tgtInput || !slInput) return;

  const newTarget = parseFloat(tgtInput.value) || 0;
  const newSL = parseFloat(slInput.value) || 0;
  const newEntryMin = entryMinInput ? (parseFloat(entryMinInput.value) || 0) : 0;
  const newEntryMax = entryMaxInput ? (parseFloat(entryMaxInput.value) || 0) : 0;

  const payload = {};
  if (newTarget > 0) payload.target_premium = newTarget;
  if (newSL > 0) payload.sl_premium = newSL;

  if (entryMinInput || entryMaxInput) {
    if (newEntryMin <= 0 || newEntryMax <= 0) {
      toast('Set a valid trigger range before saving the pending trade', 'warn');
      return;
    }
    if (newEntryMax < newEntryMin) {
      toast('Trigger end must be greater than or equal to trigger start', 'warn');
      return;
    }
    payload.entry_limit_price = newEntryMin;
    payload.entry_limit_max = newEntryMax;
  }

  if (!Object.keys(payload).length) {
    toast('Set at least one editable field', 'warn');
    return;
  }

  if (btn) { btn.disabled = true; btn.textContent = '…'; }

  try {
    const res = await fetch('/api/scalp/trades/' + tradeId + '/targets', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.status === 'ok') {
      _clearScalpInputDirty(tradeId);
      const summary = [];
      if (payload.entry_limit_price) summary.push(`Trigger: ₹${payload.entry_limit_price.toFixed(2)}–₹${payload.entry_limit_max.toFixed(2)}`);
      if (payload.target_premium) summary.push(`TP: ₹${payload.target_premium.toFixed(2)}`);
      if (payload.sl_premium) summary.push(`SL: ₹${payload.sl_premium.toFixed(2)}`);
      toast(`Trade #${tradeId} updated — ${summary.join(' | ')}`, 'success');
      refreshScalpStatus();
    } else {
      toast(data.message || 'Update failed', 'error');
    }
  } catch(e) {
    toast('Error: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Set'; }
  }
}

async function deleteScalpHistoryTrade(tid) {
  const ok = await customConfirm('Delete this closed scalp trade?', { title: 'Delete Trade', icon: ICO.trash(28), okText: 'Delete', danger: true });
  if (!ok) return;
  await fetch('/api/scalp/trades/' + tid, { method: 'DELETE' });
  toast('Deleted', 'success');
  refreshScalpStatus();
}

// ── Timestamp formatter for trade history ──
function _fmtScalpTime(raw) {
  // Accepts: "2026-03-10 13:37:55.123", "2026-03-10 13:37:55", "13:37:55", null
  if (!raw || raw === 'None') return '—';
  // If it's already HH:MM:SS, return as-is
  if (/^\d{2}:\d{2}:\d{2}$/.test(raw)) return raw;
  // Try to parse as Date and extract local HH:MM:SS
  try {
    // Backend sends IST naive timestamps like "2026-03-10 13:37:55"
    // If no timezone info, treat as local time
    const d = new Date(raw.replace(' ', 'T'));
    if (!isNaN(d.getTime())) {
      return d.toLocaleTimeString('en-IN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
  } catch(_) {}
  // Last resort: extract time portion from "YYYY-MM-DD HH:MM:SS..."
  const m = raw.match(/(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : raw.slice(0, 8);
}

// ── Pagination state for trade history ──
var _scalpHistPage = 1;
var _SCALP_ROWS_PER_PAGE = 10;
var _scalpAllClosed = [];
var _scalpSortCol = 'trade_id';
var _scalpSortAsc = false;

function _getScalpSortVal(t) {
  switch(_scalpSortCol) {
    case 'date': return new Date(t.entry_time || 0).getTime() || 0;
    case 'symbol': return ((t.underlying||'')+(t.strike||'')+(t.option_type||'')).toLowerCase();
    case 'dir': return (t.transaction_type||'').toLowerCase();
    case 'entry': return t.entry_premium || 0;
    case 'exit': return t.exit_premium || 0;
    case 'time': return new Date(t.entry_time || 0).getTime() || 0;
    case 'lots': return t.lots || 1;
    case 'pnl': return t.pnl || 0;
    case 'reason': return (t.exit_reason||'').toLowerCase();
    case 'mode': return (t.mode||'').toLowerCase();
    default: return t.trade_id || 0;
  }
}

function _applyScalpSort() {
  _scalpAllClosed.sort((a, b) => {
    const va = _getScalpSortVal(a), vb = _getScalpSortVal(b);
    if (va < vb) return _scalpSortAsc ? -1 : 1;
    if (va > vb) return _scalpSortAsc ? 1 : -1;
    return 0;
  });
}

function _sortScalpHistory(col) {
  if (_scalpSortCol === col) { _scalpSortAsc = !_scalpSortAsc; } else { _scalpSortCol = col; _scalpSortAsc = true; }
  _scalpHistPage = 1;
  _renderScalpHistoryPage();
}

function _scalpSortArrow(col) {
  if (_scalpSortCol !== col) return '<span style="opacity:0.3;font-size:9px;margin-left:3px;">▲▼</span>';
  return _scalpSortAsc ? '<span style="font-size:9px;margin-left:3px;">▲</span>' : '<span style="font-size:9px;margin-left:3px;">▼</span>';
}

function _renderScalpHistoryPage() {
  const histBody = document.getElementById('scalp-history-body');
  if (!histBody) return;
  _applyScalpSort();
  const totalPages = Math.max(1, Math.ceil(_scalpAllClosed.length / _SCALP_ROWS_PER_PAGE));
  const start = (_scalpHistPage - 1) * _SCALP_ROWS_PER_PAGE;
  const page = _scalpAllClosed.slice(start, start + _SCALP_ROWS_PER_PAGE);

  if (!page.length) {
    histBody.innerHTML = '<tr><td colspan="12" style="text-align:center;padding:20px;color:var(--muted);">No closed trades</td></tr>';
  } else {
    histBody.innerHTML = page.map(t => {
      const pnl = t.pnl || 0;
      const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--muted)';
      const modeBadge = _getModeBadge(t.mode, true);
      const entryTime = _fmtScalpTime(t.entry_time);
      const exitTime = _fmtScalpTime(t.exit_time);
      const lots = t.lots || 1;
      const qty = t.quantity || (lots * (t.lot_size || 1));
      const lotsStr = lots > 1 ? `${lots} <span style="font-size:9px;color:var(--muted);">(${qty})</span>` : `${lots}`;
      const chk = _selectedScalpHistIds.has(t.trade_id) ? ' checked' : '';
      return `<tr style="border-bottom:1px solid rgba(255,255,255,0.025);">
        <td style="padding:7px 6px;text-align:center;"><input type="checkbox" class="tbl-chk scalp-hist-chk" data-id="${t.trade_id}" aria-label="Select scalp history trade ${escapeAttr(t.trade_id)}" onchange="toggleScalpHistCheck(this)"${chk}></td>
        <td style="padding:7px 10px;color:var(--muted);font-size:11px;font-family:'JetBrains Mono',monospace;white-space:nowrap;">${(() => { const ts = t.entry_time; if (!ts) return '—'; const d = new Date(ts); if (isNaN(d)) return '—'; const dd = String(d.getDate()).padStart(2,'0'), mm = String(d.getMonth()+1).padStart(2,'0'), yyyy = d.getFullYear(); return dd+'-'+mm+'-'+yyyy; })()}</td>
        <td style="padding:7px 10px;font-size:12px;">${escapeHtml(t.underlying||'')} ${escapeHtml(t.strike||'')}${escapeHtml(t.option_type||'')}</td>
        <td style="padding:7px 6px;text-align:center;"><span style="font-size:10px;font-weight:700;color:${t.transaction_type==='BUY'?'var(--green)':'var(--red)'};">${escapeHtml(t.transaction_type||'')}</span></td>
        <td style="padding:7px 10px;text-align:right;font-family:'JetBrains Mono',monospace;font-size:11px;">₹${(t.entry_premium||0).toFixed(2)}</td>
        <td style="padding:7px 10px;text-align:right;font-family:'JetBrains Mono',monospace;font-size:11px;">₹${(t.exit_premium||0).toFixed(2)}</td>
        <td style="padding:7px 6px;text-align:center;font-size:10px;color:var(--muted);white-space:nowrap;" title="Entry: ${escapeAttr(entryTime)}&#10;Exit: ${escapeAttr(exitTime)}">${escapeHtml(entryTime)} → ${escapeHtml(exitTime)}</td>
        <td style="padding:7px 10px;text-align:center;font-family:'JetBrains Mono',monospace;font-size:11px;">${lotsStr}</td>
        <td style="padding:7px 10px;text-align:right;font-family:'JetBrains Mono',monospace;font-weight:700;color:${pnlColor};">${pnl>=0?'+':''}₹${pnl.toFixed(2)}</td>
        <td style="padding:7px 6px;text-align:center;font-size:10px;color:var(--muted);">${escapeHtml((t.exit_reason||'').replace(/_/g,' '))}</td>
        <td style="padding:7px 6px;text-align:center;">${modeBadge}</td>
        <td style="padding:7px 6px;text-align:center;">
          <button class="btn btn-danger btn-sm" onclick="deleteScalpHistoryTrade(${t.trade_id})" style="padding:2px 8px;font-size:10px;">✕</button>
        </td>
      </tr>`;
    }).join('');
  }

  // Update pagination controls
  const pagDiv = document.getElementById('scalp-hist-pagination');
  if (pagDiv) {
    const pHtml = _buildPagination(_scalpHistPage, _scalpAllClosed.length, _SCALP_ROWS_PER_PAGE, '_goScalpHistPage');
    if (pHtml) { pagDiv.innerHTML = pHtml; pagDiv.style.display = 'flex'; } else { pagDiv.style.display = 'none'; }
  }
  // Update sort arrows in headers
  ['date','symbol','dir','entry','exit','time','lots','pnl','reason','mode'].forEach(col => {
    const th = document.getElementById('scalp-th-' + col);
    if (th) { const label = th.textContent.replace(/[▲▼]/g,'').trim(); th.innerHTML = label + _scalpSortArrow(col); }
  });
  _updateScalpHistBulkBar();
}

function scalpHistPage(dir) {
  const totalPages = Math.max(1, Math.ceil(_scalpAllClosed.length / _SCALP_ROWS_PER_PAGE));
  _scalpHistPage += dir;
  if (_scalpHistPage < 1) _scalpHistPage = 1;
  if (_scalpHistPage > totalPages) _scalpHistPage = totalPages;
  _renderScalpHistoryPage();
}
function _goScalpHistPage(p) { _scalpHistPage = p; _renderScalpHistoryPage(); }

// ── Scalp History checkboxes + bulk delete ──
function toggleScalpHistCheck(el) {
  const id = parseInt(el.dataset.id);
  if (el.checked) _selectedScalpHistIds.add(id); else _selectedScalpHistIds.delete(id);
  _updateScalpHistBulkBar();
}
function toggleAllScalpHist(el) {
  document.querySelectorAll('.scalp-hist-chk').forEach(b => { b.checked = el.checked; const id = parseInt(b.dataset.id); if (el.checked) _selectedScalpHistIds.add(id); else _selectedScalpHistIds.delete(id); });
  _updateScalpHistBulkBar();
}
function _updateScalpHistBulkBar() {
  const bar = document.getElementById('scalp-hist-bulk-bar');
  if (!bar) return;
  const n = _selectedScalpHistIds.size;
  if (!n) { bar.style.display = 'none'; return; }
  bar.style.display = 'flex';
  bar.innerHTML = '<span class="bulk-count">' + n + ' selected</span><button class="bulk-del-btn" onclick="bulkDeleteScalpTrades()">' + ICO.trash(14) + ' Delete Selected</button><button class="page-btn" onclick="_selectedScalpHistIds.clear();_renderScalpHistoryPage();" style="font-size:11px;">Clear</button>';
}
async function bulkDeleteScalpTrades() {
  const ids = Array.from(_selectedScalpHistIds);
  if (!ids.length) return;
  const ok = await customConfirm('Delete <strong>' + ids.length + '</strong> selected scalp trade' + (ids.length > 1 ? 's' : '') + '?<br><span style="font-size:11px;">This cannot be undone.</span>', { title: 'Bulk Delete', icon: ICO.trash(28), okText: 'Delete All', danger: true });
  if (!ok) return;
  try {
    const r = await fetch('/api/scalp/trades/bulk-delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ ids }) });
    if (!r.ok) throw new Error('Failed');
    _selectedScalpHistIds.clear();
    toast(ids.length + ' trade' + (ids.length > 1 ? 's' : '') + ' deleted', 'success');
    refreshScalpStatus();
  } catch(e) { toast('Bulk delete failed: ' + e.message, 'danger'); }
}

// ── Scalp Runs (Results tab) checkboxes + bulk delete ──
function toggleScalpRunCheck(el) {
  const id = parseInt(el.dataset.id);
  if (el.checked) _selectedScalpRunIds.add(id); else _selectedScalpRunIds.delete(id);
  _updateScalpRunsBulkBar();
}
function toggleAllScalpRuns(el) {
  document.querySelectorAll('.scalp-run-chk').forEach(b => { b.checked = el.checked; const id = parseInt(b.dataset.id); if (el.checked) _selectedScalpRunIds.add(id); else _selectedScalpRunIds.delete(id); });
  _updateScalpRunsBulkBar();
}
function _updateScalpRunsBulkBar() {
  const bar = document.getElementById('scalp-runs-bulk-bar');
  if (!bar) return;
  const n = _selectedScalpRunIds.size;
  if (!n) { bar.style.display = 'none'; return; }
  bar.style.display = 'flex';
  bar.innerHTML = '<span class="bulk-count">' + n + ' selected</span><button class="bulk-del-btn" onclick="bulkDeleteScalpRuns()">' + ICO.trash(14) + ' Delete Selected</button><button class="page-btn" onclick="_selectedScalpRunIds.clear();_renderFilteredRuns();" style="font-size:11px;">Clear</button>';
}
async function bulkDeleteScalpRuns() {
  const ids = Array.from(_selectedScalpRunIds);
  if (!ids.length) return;
  const ok = await customConfirm('Delete <strong>' + ids.length + '</strong> selected scalp trade' + (ids.length > 1 ? 's' : '') + '?<br><span style="font-size:11px;">This cannot be undone.</span>', { title: 'Bulk Delete', icon: ICO.trash(28), okText: 'Delete All', danger: true });
  if (!ok) return;
  try {
    const r = await fetch('/api/scalp/trades/bulk-delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ ids }) });
    if (!r.ok) throw new Error('Failed');
    _selectedScalpRunIds.clear();
    toast(ids.length + ' trade' + (ids.length > 1 ? 's' : '') + ' deleted', 'success');
    _renderFilteredRuns();
  } catch(e) { toast('Bulk delete failed: ' + e.message, 'danger'); }
}

var _lastScalpStatus = null;
async function refreshScalpStatus() {
  try {
    const res = await fetch('/api/scalp/status');
    if (!res.ok) return;
    const data = await res.json();
    _lastScalpStatus = data;
    _renderScalpStatus(data);
    _wsSetLiveIndicator(_ws && _ws.readyState === 1, _wsStale);
  } catch(e) { /* offline */ }
}

function _buildScalpPremiumEditor(inputId, value, tone) {
  return `<div class="scalp-cell-editor"><button class="scalp-step-btn" onclick="stepScalpField('${inputId}',-10)">−</button><input type="number" id="${inputId}" value="${value || 0}" step="10" min="0" class="scalp-stepper" style="width:58px;font-size:11px;padding:3px 4px;font-family:'JetBrains Mono',monospace;background:rgba(255,255,255,0.04);border:1px solid var(--border);border-radius:4px;color:${tone};text-align:center;"><button class="scalp-step-btn" onclick="stepScalpField('${inputId}',10)">+</button></div>`;
}

function _buildScalpPendingRangeEditor(tradeId, entryMin, entryMax) {
  return `<div class="scalp-range-editor"><input type="number" id="scalp-entry-min-${tradeId}" value="${(entryMin || 0).toFixed(2)}" step="0.05" min="0" class="scalp-stepper" style="width:66px;font-size:10px;padding:3px 4px;font-family:'JetBrains Mono',monospace;background:rgba(255,255,255,0.04);border:1px solid var(--border);border-radius:4px;color:rgba(139,143,255,0.96);text-align:center;" title="Trigger start"><span style="font-size:10px;color:var(--muted);">→</span><input type="number" id="scalp-entry-max-${tradeId}" value="${(entryMax || 0).toFixed(2)}" step="0.05" min="0" class="scalp-stepper" style="width:66px;font-size:10px;padding:3px 4px;font-family:'JetBrains Mono',monospace;background:rgba(255,255,255,0.04);border:1px solid var(--border);border-radius:4px;color:rgba(139,143,255,0.96);text-align:center;" title="Trigger end"></div>`;
}

function _scalpProductBadge(productType) {
  const product = String(productType || 'INTRADAY').toUpperCase();
  const label = product === 'MARGIN' || product === 'NORMAL' || product === 'NRML' ? 'NRML' : 'INTR';
  const tone = label === 'NRML' ? 'rgba(96,165,250,0.8)' : 'rgba(251,191,36,0.78)';
  return `<span title="${label === 'NRML' ? 'Normal / till expiry' : 'Intraday'}" style="font-size:9px;color:${tone};font-weight:700;margin-left:5px;">${label}</span>`;
}

function _scalpLotsBadge(t) {
  const lots = Number(t?.lots || 0);
  if (!Number.isFinite(lots) || lots <= 0) return '';
  const lotLabel = lots === 1 ? 'lot' : 'lots';
  return ` <span title="${escapeAttr(`${lots} ${lotLabel} added`)}" style="font-size:10px;color:var(--muted);font-weight:700;">(${escapeHtml(lots)} ${lotLabel})</span>`;
}

// Shared row builder — used by both _renderScalpStatus (REST) and _renderScalpStatusWS (WebSocket)
function _buildScalpActiveRow(t) {
  const isPending = t.status === 'pending';
  const pnl = t.pnl || 0;
  const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--muted)';
  const tgtVal = t.target_premium || 0;
  const slVal = t.sl_premium || 0;
  if (isPending) {
    return `<tr data-tid="${t.trade_id}" data-status="pending" style="border-bottom:1px solid rgba(255,255,255,0.03);background:rgba(99,102,241,0.06);">
      <td style="padding:8px 10px;font-size:12px;">${escapeHtml(t.underlying || '')} ${escapeHtml(t.strike || '')}${escapeHtml(t.option_type || '')}${_scalpLotsBadge(t)} <span style="font-size:10px;color:var(--muted);">${escapeHtml(t.expiry || '')}</span>${_scalpProductBadge(t.product_type)}<br><span style="font-size:9px;color:rgba(139,143,255,0.9);font-weight:700;">⏳ PENDING</span></td>
      <td style="padding:6px 10px;text-align:right;font-family:'JetBrains Mono',monospace;font-size:11px;color:rgba(139,143,255,0.9);">${_buildScalpPendingRangeEditor(t.trade_id, t.entry_limit_price, t.entry_limit_max)}</td>
      <td style="padding:8px 10px;text-align:right;font-family:'JetBrains Mono',monospace;"><span id="scalp-ltp-${t.trade_id}">₹${(t.current_premium||0).toFixed(2)}</span></td>
      <td style="padding:8px 10px;text-align:center;font-size:10px;color:var(--muted);">—</td>
      <td style="padding:6px 4px;text-align:center;">${_buildScalpPremiumEditor('scalp-tgt-' + t.trade_id, tgtVal, 'var(--green)')}</td>
      <td style="padding:6px 4px;text-align:center;">${_buildScalpPremiumEditor('scalp-sl-' + t.trade_id, slVal, 'var(--red)')}</td>
      <td style="padding:6px 10px;text-align:center;white-space:nowrap;"><div class="scalp-action-wrap">
        <button class="btn btn-sm" id="scalp-set-btn-${t.trade_id}" onclick="modifyScalpTrade(${t.trade_id})" style="padding:3px 8px;font-size:10px;--btn-bg:linear-gradient(180deg,rgba(var(--pf-tint-primary-rgb, 6,182,212),0.25) 0%,rgba(4,130,155,0.4) 100%);--btn-color:var(--accent);--btn-border:rgba(var(--pf-tint-primary-rgb, 6,182,212),0.5);">Set</button>
        <button class="btn btn-danger btn-sm" onclick="exitScalpTrade(${t.trade_id})" style="padding:3px 8px;font-size:10px;">Cancel</button>
      </div></td>
    </tr>`;
  }
  const hasBrokerOrders = t.super_order_id || t.broker_sl_order_id || t.broker_tp_order_id;
  const brokerBadge = hasBrokerOrders
    ? ` <span title="${t.super_order_id ? 'Dhan Super Order active' : 'SL/TP placed on broker'}" style="font-size:9px;color:rgba(52,211,153,0.7);font-weight:700;">${t.super_order_id ? 'SO' : '🛡️'}</span>`
    : '';
  return `<tr data-tid="${t.trade_id}" data-status="open" style="border-bottom:1px solid rgba(255,255,255,0.03);">
    <td style="padding:8px 10px;font-size:12px;">${escapeHtml(t.underlying || '')} ${escapeHtml(t.strike || '')}${escapeHtml(t.option_type || '')}${_scalpLotsBadge(t)} <span style="font-size:10px;color:var(--muted);">${escapeHtml(t.expiry || '')}</span>${_scalpProductBadge(t.product_type)}${brokerBadge}</td>
    <td style="padding:8px 10px;text-align:right;font-family:'JetBrains Mono',monospace;"><span id="scalp-entry-${t.trade_id}">₹${(t.entry_premium||0).toFixed(2)}</span></td>
    <td style="padding:8px 10px;text-align:right;font-family:'JetBrains Mono',monospace;"><span id="scalp-ltp-${t.trade_id}">₹${(t.current_premium||0).toFixed(2)}</span></td>
    <td style="padding:8px 10px;text-align:right;font-family:'JetBrains Mono',monospace;font-weight:700;color:${pnlColor};"><span id="scalp-pnl-${t.trade_id}">${pnl>=0?'+':''}₹${pnl.toFixed(2)}</span></td>
    <td style="padding:6px 4px;text-align:center;">${_buildScalpPremiumEditor('scalp-tgt-' + t.trade_id, tgtVal, 'var(--green)')}</td>
    <td style="padding:6px 4px;text-align:center;">${_buildScalpPremiumEditor('scalp-sl-' + t.trade_id, slVal, 'var(--red)')}</td>
    <td style="padding:6px 10px;text-align:center;white-space:nowrap;"><div class="scalp-action-wrap">
      <button class="btn btn-sm" id="scalp-set-btn-${t.trade_id}" onclick="modifyScalpTrade(${t.trade_id})" style="padding:3px 8px;font-size:10px;--btn-bg:linear-gradient(180deg,rgba(var(--pf-tint-primary-rgb, 6,182,212),0.25) 0%,rgba(4,130,155,0.4) 100%);--btn-color:var(--accent);--btn-border:rgba(var(--pf-tint-primary-rgb, 6,182,212),0.5);">Set</button>
      <button class="btn btn-danger btn-sm" onclick="exitScalpTrade(${t.trade_id})" style="padding:3px 8px;font-size:10px;">Exit</button>
    </div></td>
  </tr>`;
}

function _applyScalpEngineState(running, rootGetter = (id) => document.getElementById(id)) {
  const dot = rootGetter('scalp-status-dot');
  const label = rootGetter('scalp-status-label');
  const engDot = rootGetter('scalp-engine-dot');
  const startBtn = rootGetter('scalp-start-btn');
  const stopBtn = rootGetter('scalp-stop-btn');

  if (running) {
    if (dot) { dot.style.background = 'var(--green)'; dot.style.animation = 'livePulse 2s infinite'; }
    if (label) label.textContent = 'Running';
    if (engDot) engDot.style.color = 'var(--green)';
  } else {
    if (dot) { dot.style.background = 'var(--muted)'; dot.style.animation = 'none'; }
    if (label) label.textContent = 'Idle';
    if (engDot) engDot.style.color = '#06b6d4';
  }

  if (startBtn) {
    startBtn.disabled = !!running;
    startBtn.style.opacity = running ? '0.45' : '1';
    startBtn.style.pointerEvents = running ? 'none' : '';
  }
  if (stopBtn) {
    stopBtn.disabled = !running;
    stopBtn.style.opacity = running ? '1' : '0.45';
    stopBtn.style.pointerEvents = running ? '' : 'none';
  }
}

function _renderScalpStatus(data) {
  _applyScalpEngineState(!!data.running);

  // Session P&L
  const sessionPnl = document.getElementById('scalp-session-pnl');
  if (sessionPnl) {
    const pnl = Number(data.session_pnl ?? data.total_pnl ?? 0);
    sessionPnl.textContent = '₹' + pnl.toFixed(2);
    sessionPnl.style.color = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--muted)';
  }

  // Active positions — GRANULAR DOM updates so typing in Target/SL inputs
  // is never interrupted by the 3-second poll refresh.
  const tbody = document.getElementById('scalp-active-body');
  if (tbody) {
    const open = data.open_trades || [];
    const pendingCount = open.filter(t => t.status === 'pending').length;
    const activeCount = open.length - pendingCount;
    document.getElementById('scalp-open-count').textContent = activeCount + ' open' + (pendingCount ? ` · ${pendingCount} pending` : '');
    const killBtn = document.getElementById('scalp-kill-all-btn');
    if (killBtn) killBtn.style.display = open.length > 0 ? '' : 'none';

    // Build set of current trade IDs from server
    const serverTids = new Set(open.map(t => t.trade_id));
    // Build set of trade IDs currently rendered in the DOM
    const domTids = new Set();
    tbody.querySelectorAll('tr[data-tid]').forEach(tr => domTids.add(parseInt(tr.dataset.tid)));

    // Decide: full rebuild or granular update?
    // Full rebuild if: empty, trade added, trade removed, or status changed (pending→open)
    let sameSet = serverTids.size === domTids.size && [...serverTids].every(id => domTids.has(id));
    if (sameSet) {
      // Also check if any trade status changed (e.g. pending→open needs full rebuild)
      for (const t of open) {
        const row = tbody.querySelector(`tr[data-tid="${t.trade_id}"]`);
        if (row && row.dataset.status !== t.status) { sameSet = false; break; }
      }
    }

    if (!open.length) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:20px;color:var(--muted);">No active positions</td></tr>';
    } else if (!sameSet) {
      // Full rebuild — trade set changed
      tbody.innerHTML = open.map(t => _buildScalpActiveRow(t)).join('');
    } else {
      // Granular update — only touch LTP, P&L, Entry cells. Never touch inputs.
      open.forEach(t => {
        const ltpEl = document.getElementById('scalp-ltp-' + t.trade_id);
        const pnlEl = document.getElementById('scalp-pnl-' + t.trade_id);
        const entryEl = document.getElementById('scalp-entry-' + t.trade_id);
        if (ltpEl) ltpEl.textContent = '₹' + (t.current_premium||0).toFixed(2);
        if (pnlEl) {
          const pnl = t.pnl || 0;
          pnlEl.textContent = (pnl>=0?'+':'') + '₹' + pnl.toFixed(2);
          pnlEl.style.color = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--muted)';
        }
        if (entryEl) entryEl.textContent = '₹' + (t.entry_premium||0).toFixed(2);
        const tgtInput = document.getElementById('scalp-tgt-' + t.trade_id);
        const slInput = document.getElementById('scalp-sl-' + t.trade_id);
        const entryMinInput = document.getElementById('scalp-entry-min-' + t.trade_id);
        const entryMaxInput = document.getElementById('scalp-entry-max-' + t.trade_id);
        if (tgtInput && document.activeElement !== tgtInput && !_isScalpInputDirty('scalp-tgt-' + t.trade_id)) tgtInput.value = t.target_premium || 0;
        if (slInput && document.activeElement !== slInput && !_isScalpInputDirty('scalp-sl-' + t.trade_id)) slInput.value = t.sl_premium || 0;
        if (entryMinInput && document.activeElement !== entryMinInput && !_isScalpInputDirty('scalp-entry-min-' + t.trade_id)) entryMinInput.value = (t.entry_limit_price || 0).toFixed(2);
        if (entryMaxInput && document.activeElement !== entryMaxInput && !_isScalpInputDirty('scalp-entry-max-' + t.trade_id)) entryMaxInput.value = (t.entry_limit_max || 0).toFixed(2);
      });
    }
  }

  // Event log
  const logEl = document.getElementById('scalp-event-log');
  if (logEl) {
    const events = data.event_log || [];
    if (!events.length) {
      logEl.innerHTML = '<div style="color:var(--muted);text-align:center;padding:20px;">No events yet</div>';
    } else {
      const colors = { entry: 'var(--green)', exit: 'var(--green)', stop: 'var(--danger)', error: 'var(--danger)', info: 'var(--accent)' };
      logEl.innerHTML = events.map(e =>
        `<div style="padding:2px 0;border-bottom:1px solid rgba(255,255,255,0.03);">
          <span style="color:var(--muted);margin-right:6px;">${escapeHtml(e.time || '')}</span>
          <span style="color:${colors[e.type]||'var(--text-dim)'};">${escapeHtml(e.message || '')}</span>
        </div>`
      ).join('');
    }
  }

  // Closed trades history (file trades) — with pagination
  const histBody = document.getElementById('scalp-history-body');
  if (histBody) {
    const allClosed = [...(data.closed_trades || []), ...(data.file_trades || [])];
    const seen = new Set();
    _scalpAllClosed = allClosed.filter(t => { if (seen.has(t.trade_id)) return false; seen.add(t.trade_id); return true; })
      .sort((a, b) => (b.trade_id || 0) - (a.trade_id || 0));

    // Total P&L across ALL trades
    let totalPnl = 0;
    _scalpAllClosed.forEach(t => { totalPnl += (t.pnl || 0); });
    const totalEl = document.getElementById('scalp-total-pnl-label');
    if (totalEl) {
      totalEl.textContent = 'Total: ' + (totalPnl >= 0 ? '+' : '') + '₹' + totalPnl.toFixed(2);
      totalEl.style.color = totalPnl > 0 ? 'var(--green)' : totalPnl < 0 ? 'var(--red)' : 'var(--muted)';
    }

    // Clamp page if data shrunk
    const totalPages = Math.max(1, Math.ceil(_scalpAllClosed.length / _SCALP_ROWS_PER_PAGE));
    if (_scalpHistPage > totalPages) _scalpHistPage = totalPages;

    _renderScalpHistoryPage();
  }
}

// ══════════════════════════════════════════════════════════════
//  MARKET TICKER (Live data)
// ══════════════════════════════════════════════════════════════
function renderTickerPayload(data) {
  if (!data || data.status !== 'ok') return false;
  if (data.nifty) setTickerValue('ticker-nifty', 'ticker-nifty-chg', data.nifty);
  if (data.sensex) setTickerValue('ticker-sensex', 'ticker-sensex-chg', data.sensex);
  if (data.vix) setTickerValue('ticker-vix', 'ticker-vix-chg', data.vix);
  if (data.atmCE) setTickerValue('ticker-atm-ce', 'ticker-atm-ce-chg', data.atmCE);
  if (data.atmPE) setTickerValue('ticker-atm-pe', 'ticker-atm-pe-chg', data.atmPE);
  return true;
}

function loadTickerFromCache() {
  try {
    const raw = _getLocalState('philforge_ticker_cache');
    if (!raw) return false;
    const cached = JSON.parse(raw);
    return renderTickerPayload(cached);
  } catch (err) {
    console.warn('Ticker cache load failed:', err);
    return false;
  }
}

function updateTicker() {
  fetch(`/api/ticker?_=${Date.now()}`, { cache: 'no-store' }).then(r => r.json()).then(data => {
    console.log('Ticker data received:', data);
    if (data.status === 'ok') {
      renderTickerPayload(data);
      try {
        _setLocalState('philforge_ticker_cache', JSON.stringify(data));
      } catch (err) {
        console.warn('Ticker cache save failed:', err);
      }
    } else {
      console.warn('Ticker API error:', data);
      loadTickerFromCache();
    }
  }).catch(err => {
    console.error('Ticker fetch error:', err);
    loadTickerFromCache();
  });
}

// Called after backtest completes to populate ticker from backtest data
function updateTickerFromBacktest(data) {
  if (!data || !data.trades || data.trades.length === 0) return;
  // Find the last trade's entry price (this is the index close)
  const lastTrade = data.trades[data.trades.length - 1];
  let idxPrice = 0;
  // If option trade, reverse-estimate index from premium
  if (lastTrade.strike && lastTrade.entry_price < 1000) {
    const m = lastTrade.strike.match(/(\d+)/);
    idxPrice = m ? parseFloat(m[0]) : lastTrade.entry_price * 200;
  } else {
    idxPrice = lastTrade.entry_price > 1000 ? lastTrade.entry_price : 0;
  }
  if (idxPrice > 10000) {
    const el = document.getElementById('ticker-nifty');
    el.textContent = idxPrice.toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2});
    el.style.color = 'var(--text)';
    // Estimate ATM CE/PE from option premium
    const atm = Math.round(idxPrice / 50) * 50;
    const cePrem = idxPrice * 0.005;
    const pePrem = idxPrice * 0.004;
    document.getElementById('ticker-atm-ce').textContent = cePrem.toFixed(2);
    document.getElementById('ticker-atm-pe').textContent = pePrem.toFixed(2);
  }
}

function setTickerValue(priceId, chgId, data) {
  const priceEl = document.getElementById(priceId);
  const chgEl = document.getElementById(chgId);
  if (!priceEl || !data) return;

  const price = parseFloat(data.price);
  if (isNaN(price) || price <= 0) {
    console.warn(`Invalid price for ${priceId}:`, data.price);
    return;
  }

  priceEl.textContent = price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  priceEl.style.color = 'var(--text)';

  if (chgEl && data.change !== undefined && data.pct !== undefined) {
    const isUp = data.change >= 0;
    const dir = isUp ? 'UP' : 'DN';
    chgEl.textContent = `${dir} ${isUp ? '+' : ''}${data.change.toFixed(2)} (${isUp ? '+' : ''}${data.pct.toFixed(2)}%)`;
    chgEl.style.color = isUp ? 'var(--success)' : 'var(--danger)';
  }
}

// ══════════════════════════════════════════════════════════════
//  CLOCK & EXPIRY DATES
// ══════════════════════════════════════════════════════════════
function updateClock() {
  const now = new Date();
  const isCompactMobile = window.innerWidth <= 420;
  const isMobile = window.innerWidth <= 767;
  const dateStr = now.toLocaleDateString('en-IN', isCompactMobile
    ? { weekday: 'short', day: '2-digit', month: 'short', year: '2-digit' }
    : isMobile
      ? { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' }
      : { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
  const timeStr = now.toLocaleTimeString('en-IN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const dateEl = document.getElementById('clock-date');
  const timeEl = document.getElementById('clock-time');
  if (dateEl) dateEl.textContent = dateStr;
  if (timeEl) timeEl.textContent = timeStr;

  // Color market hours
  const h = now.getHours(), m = now.getMinutes();
  const mins = h * 60 + m;
  const inMarket = mins >= 555 && mins <= 930; // 9:15 - 15:30
  if (timeEl) timeEl.style.color = inMarket ? 'var(--success)' : 'var(--accent)';
}

function loadExpiryDates() {
  fetch('/api/expiry-dates').then(r => r.json()).then(data => {
    if (data.status === 'ok') {
      const fmt = (d) => {
        if (!d) return '--';
        const dt = new Date(d + 'T00:00:00');
        const today = new Date(); today.setHours(0,0,0,0);
        const diff = Math.ceil((dt - today) / 86400000);
        const dayName = dt.toLocaleDateString('en-IN', { weekday: 'short' });
        const label = dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
        if (diff === 0) return `${dayName} ${label} (TODAY!)`;
        if (diff === 1) return `${dayName} ${label} (Tomorrow)`;
        return `${dayName} ${label} (${diff}d)`;
      };
      const nEl = document.getElementById('expiry-nifty');
      const bEl = document.getElementById('expiry-banknifty');
      const sEl = document.getElementById('expiry-sensex');
      if (nEl) nEl.textContent = fmt(data.nifty);
      if (bEl) bEl.textContent = fmt(data.banknifty);
      if (sEl) sEl.textContent = fmt(data.sensex);

      // Highlight if expiry is today
      if (data.nifty && nEl) {
        const dt = new Date(data.nifty + 'T00:00:00');
        const today = new Date(); today.setHours(0,0,0,0);
        if (dt.getTime() === today.getTime()) nEl.style.color = 'var(--red)';
      }
      if (data.banknifty && bEl) {
        const dt = new Date(data.banknifty + 'T00:00:00');
        const today = new Date(); today.setHours(0,0,0,0);
        if (dt.getTime() === today.getTime()) bEl.style.color = 'var(--red)';
      }
      if (data.sensex && sEl) {
        const dt = new Date(data.sensex + 'T00:00:00');
        const today = new Date(); today.setHours(0,0,0,0);
        if (dt.getTime() === today.getTime()) sEl.style.color = 'var(--red)';
      }
    }
  }).catch(e => console.warn('Expiry fetch error:', e));
}

// ══════════════════════════════════════════════════════════════
//  SEGMENT & INSTRUMENT (Indices vs Stocks)
// ══════════════════════════════════════════════════════════════
const INDICES_LIST = [
  { value: '26000', label: 'NIFTY 50' },
  { value: '26009', label: 'BANK NIFTY' },
  { value: '1',     label: 'SENSEX' },
  { value: '26017', label: 'NIFTY FIN SVC' },
  { value: '26037', label: 'NIFTY MIDCAP 50' },
  { value: '26074', label: 'NIFTY NEXT 50' },
  { value: '26013', label: 'NIFTY IT' },
];

const STOCKS_LIST = [
  { value: 'RELIANCE',  label: 'Reliance Industries' },
  { value: 'TCS',       label: 'TCS' },
  { value: 'HDFCBANK',  label: 'HDFC Bank' },
  { value: 'INFY',      label: 'Infosys' },
  { value: 'ICICIBANK', label: 'ICICI Bank' },
  { value: 'HINDUNILVR',label: 'Hindustan Unilever' },
  { value: 'ITC',       label: 'ITC' },
  { value: 'SBIN',      label: 'SBI' },
  { value: 'BHARTIARTL',label: 'Bharti Airtel' },
  { value: 'BAJFINANCE',label: 'Bajaj Finance' },
  { value: 'KOTAKBANK', label: 'Kotak Bank' },
  { value: 'LT',        label: 'Larsen & Toubro' },
  { value: 'HCLTECH',   label: 'HCL Tech' },
  { value: 'ASIANPAINT',label: 'Asian Paints' },
  { value: 'AXISBANK',  label: 'Axis Bank' },
  { value: 'MARUTI',    label: 'Maruti Suzuki' },
  { value: 'SUNPHARMA', label: 'Sun Pharma' },
  { value: 'TITAN',     label: 'Titan Company' },
  { value: 'ULTRACEMCO',label: 'UltraTech Cement' },
  { value: 'BAJAJFINSV',label: 'Bajaj Finserv' },
  { value: 'WIPRO',     label: 'Wipro' },
  { value: 'NESTLEIND', label: 'Nestle India' },
  { value: 'TATAMOTORS',label: 'Tata Motors' },
  { value: 'M_M',       label: 'M&M' },
  { value: 'POWERGRID', label: 'Power Grid' },
];

function onSegmentChange() {
  const seg = document.getElementById('segment-select').value;
  const inst = document.getElementById('instrument-select');
  const list = seg === 'indices' ? INDICES_LIST : STOCKS_LIST;

  inst.innerHTML = '<option value="" disabled selected>Select...</option>';
  list.forEach(item => {
    inst.innerHTML += `<option value="${item.value}">${item.label}</option>`;
  });
  applyExecutionProfile(false);
}

// ══════════════════════════════════════════════════════════════
//  MOVE STRATEGY TO DIFFERENT FOLDER
// ══════════════════════════════════════════════════════════════
let movingStrategyId = null;

async function addNewFolder() {
  const name = await customConfirm('Enter new folder name:', { title: 'New Folder', okText: 'Create', prompt: true, promptPlaceholder: 'e.g. Hedging, Positional...' });
  if (!name || !name.trim()) return;
  const folder = name.trim();
  // Check if folder already exists
  const existing = new Set();
  savedStrategiesCache.forEach(s => { if (s.folder) existing.add(s.folder); });
  if (existing.has(folder)) { toast('Folder "' + folder + '" already exists', 'danger'); return; }
  // Create folder via API
  try {
    const res = await fetch('/api/strategies/folders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder: folder })
    });
    if (res.ok) {
      toast('Folder "' + folder + '" created', 'success');
      fetchStrategies();
    } else {
      // API may not support folder endpoint — inject locally
      savedStrategiesCache.push({ id: -Date.now(), run_name: '', folder: folder, _placeholder: true, legs: [], created_at: new Date().toISOString() });
      toast('Folder "' + folder + '" created', 'success');
      fetchStrategies();
    }
  } catch(e) {
    savedStrategiesCache.push({ id: -Date.now(), run_name: '', folder: folder, _placeholder: true, legs: [], created_at: new Date().toISOString() });
    toast('Folder "' + folder + '" created', 'success');
    fetchStrategies();
  }
}

function moveStrategyFolder(id) {
  const s = savedStrategiesCache.find(x => x.id === id);
  if (!s) return;
  movingStrategyId = id;
  const currentFolder = s.folder || 'Intraday';

  document.getElementById('move-strategy-name').textContent = `Moving "${s.run_name}" (current: ${currentFolder})`;

  // Collect all existing folders
  const folders = new Set(['Scalping', 'Intraday', 'Swing', 'Positional', 'Experimental', 'Hedging']);
  savedStrategiesCache.forEach(st => { if (st.folder) folders.add(st.folder); });

  const container = document.getElementById('move-folder-options');
  container.innerHTML = '';
  [...folders].sort().forEach(f => {
    const isActive = f === currentFolder;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.style.cssText = `width: 100%; padding: 10px 14px; text-align: left; background: ${isActive ? 'rgba(var(--pf-tint-primary-rgb, 0,200,150),0.1)' : 'var(--card2)'}; border: 1px solid ${isActive ? 'var(--accent)' : 'var(--border)'}; border-radius: 6px; color: ${isActive ? 'var(--accent)' : 'var(--text)'}; cursor: pointer; font-family: 'Outfit', sans-serif; font-size: 13px; font-weight: ${isActive ? '700' : '500'}; transition: 0.15s;`;
    btn.textContent = `${isActive ? '• ' : ''}${f}${isActive ? ' (current)' : ''}`;
    btn.addEventListener('click', () => confirmMoveTo(f));
    container.appendChild(btn);
  });

  document.getElementById('move-new-folder').value = '';
  document.getElementById('move-folder-modal').classList.add('open');
}

function closeMoveModal() { document.getElementById('move-folder-modal').classList.remove('open'); }

function confirmMoveTo(folder) {
  if (!movingStrategyId) return;
  const s = savedStrategiesCache.find(x => x.id === movingStrategyId);
  if (s && s.folder === folder) { closeMoveModal(); return; }

  fetch(`/api/strategies/${movingStrategyId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder: folder })
  }).then(() => {
    toast(`Moved to "${folder}"`, 'success');
    closeMoveModal();
    fetchStrategies();
  }).catch(() => {
    toast('Move failed', 'danger');
  });
}

function confirmMoveToNew() {
  const newFolder = document.getElementById('move-new-folder').value.trim();
  if (!newFolder) { toast('Enter a folder name', 'warn'); return; }
  confirmMoveTo(newFolder);
}

// ══════════════════════════════════════════════════════════════
//  TOAST
// ══════════════════════════════════════════════════════════════
let toastTimer;
function toast(msg, type='', duration=4000) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), duration);
}

// ══════════════════════════════════════════════════════════════
//  BROKER ✅ FIX #4: Real connection validation with API
// ══════════════════════════════════════════════════════════════
let isBrokerConnected = false;
let brokerCheckInterval = null;

function updateBrokerUI(status, text, dotColor, btnLabel) {
  isBrokerConnected = status;
  document.getElementById('broker-dot').style.backgroundColor = dotColor;
  document.getElementById('broker-text').textContent = text;
  const btn = document.getElementById('broker-toggle-btn');
  if (btn) btn.textContent = btnLabel;
  const refreshBtn = document.getElementById('broker-refresh-btn');
  if (refreshBtn) refreshBtn.style.display = (dotColor === 'red' || dotColor === 'orange') ? '' : 'none';
}

async function checkBrokerStatus(silent) {
  try {
    const res = await fetch('/api/broker/check', { method: 'POST' });
    const data = await res.json();
    if (data.status === 'connected') {
      updateBrokerUI(true, 'Dhan Connected', 'var(--green)', 'Recheck');
      startBrokerHealthCheck();
      if (!silent) toast('Broker connection verified', 'success');
      return true;
    } else if (data.status === 'auth_error') {
      stopBrokerHealthCheck();
      updateBrokerUI(false, 'Token Refresh Needed', 'red', 'Retry');
      if (!silent) toast((data.message || 'Broker token is invalid'), 'danger');
    } else if (data.status === 'marketdata_error') {
      stopBrokerHealthCheck();
      updateBrokerUI(false, 'Market Data Failed', 'orange', 'Retry');
      if (!silent) toast((data.message || 'Market data probe failed'), 'warn');
    } else if (data.status === 'not_configured') {
      stopBrokerHealthCheck();
      updateBrokerUI(false, 'Not Configured', 'orange', 'Check');
      if (!silent) toast((data.message || 'Broker not configured'), 'warn');
    } else {
      stopBrokerHealthCheck();
      updateBrokerUI(false, 'Connection Failed', 'red', 'Retry');
      if (!silent) toast((data.message || 'Connection failed'), 'danger');
    }
  } catch (err) {
    stopBrokerHealthCheck();
    updateBrokerUI(false, 'Network Error', 'red', 'Retry');
    if (!silent) toast('Network error: ' + err.message, 'danger');
  }
  return false;
}

async function toggleBroker() {
  const btn = document.getElementById('broker-toggle-btn');
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = 'Checking...';
  try {
    await checkBrokerStatus(false);
  } finally {
    btn.disabled = false;
  }
}

async function refreshToken() {
  const btn = document.getElementById('broker-refresh-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⟳ ...'; }
  try {
    const res = await fetch('/api/refresh-token', { method: 'POST' });
    const data = await res.json();
    if (data.status === 'ok') {
      toast('Token refreshed!', 'success');
      await loadUserProfile(true);
      await checkBrokerStatus(false);
    } else {
      toast(data.message || 'Token refresh failed', 'danger');
    }
  } catch (err) {
    toast('Token refresh error: ' + err.message, 'danger');
  }
  if (btn) { btn.disabled = false; btn.textContent = '⟳ Token'; }
}

function startBrokerHealthCheck() {
  if (brokerCheckInterval) return;
  brokerCheckInterval = setInterval(async () => {
    if (!_isPageVisible()) return;
    try {
      const res = await fetch('/api/broker/check', { method: 'POST' });
      const data = await res.json();
      if (data.status !== 'connected') {
        updateBrokerUI(false, 'Connection Lost', 'orange', 'Retry');
        toast('Broker connection lost', 'warn');
        stopBrokerHealthCheck();
      }
    } catch (err) {
      updateBrokerUI(false, 'Connection Lost', 'grey', 'Retry');
      stopBrokerHealthCheck();
    }
  }, 30000);
}

function stopBrokerHealthCheck() {
  if (brokerCheckInterval) {
    clearInterval(brokerCheckInterval);
    brokerCheckInterval = null;
  }
}

// Auto-detect broker status on page load
checkBrokerStatus(true);
// (Copy & Edit removed — use Load from Dashboard instead)

// ══════════════════════════════════════════════════════════════
//  SAVED STRATEGIES
// ══════════════════════════════════════════════════════════════
let savedStrategiesCache = [];
let viewingStrategyId = null;
let currentLoadedStrategyId = null;

function _normalizeStrategyKey(v) {
  return String(v || '').trim().toLowerCase();
}

function _savedFolderDomId(folderName) {
  const base = String(folderName || 'Intraday').trim() || 'Intraday';
  return 'saved-folder-' + base.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

function _findSavedStrategyByRef(strategyName, explicitFolder = '', strategyId = 0) {
  const id = Number(strategyId || 0);
  if (id) {
    const exact = (savedStrategiesCache || []).find((s) => Number(s?.id || 0) === id);
    if (exact) return exact;
  }
  const key = _normalizeStrategyKey(strategyName);
  if (!key) return null;
  let matches = (savedStrategiesCache || []).filter((s) => {
    if (s?._placeholder) return false;
    return _normalizeStrategyKey(s?.run_name) === key || _normalizeStrategyKey(s?.name) === key;
  });
  const folderKey = _normalizeStrategyKey(explicitFolder || 'Intraday');
  if (folderKey) {
    const folderMatches = matches.filter((s) => _normalizeStrategyKey(s?.folder || 'Intraday') === folderKey);
    if (folderMatches.length) matches = folderMatches;
  }
  if (matches.length === 1) return matches[0];
  return null;
}

function _resolveSavedStrategyFolder(strategyName, explicitFolder = '', strategyId = 0) {
  const matched = _findSavedStrategyByRef(strategyName, explicitFolder, strategyId);
  if (matched) return String(matched.folder || '').trim() || 'Intraday';
  const explicit = String(explicitFolder || '').trim();
  return explicit || '';
}

function toggleSavedStrategyFolder(headerEl, forceOpen = null) {
  if (!headerEl) return;
  const content = headerEl.nextElementSibling;
  if (!content) return;
  const shouldOpen = forceOpen === null ? content.style.display === 'none' : !!forceOpen;
  content.style.display = shouldOpen ? 'block' : 'none';
  const arrow = headerEl.querySelector('.fold-arrow');
  if (arrow) arrow.textContent = shouldOpen ? '▼' : '▶';
}

function _highlightSavedStrategyRow(row) {
  if (!row) return;
  row.scrollIntoView({ behavior: 'smooth', block: 'center' });
  const prevBg = row.style.background;
  const prevBox = row.style.boxShadow;
  row.style.background = 'rgba(20,184,166,0.08)';
  row.style.boxShadow = 'inset 0 0 0 1px rgba(20,184,166,0.35)';
  setTimeout(() => {
    row.style.background = prevBg;
    row.style.boxShadow = prevBox;
  }, 2200);
}

async function openSavedStrategyFolder(strategyName, folderName = '', strategyId = 0) {
  showPage('dashboard-page', document.getElementById('nav-dashboard'));
  await ensureStrategiesLoaded(true);

  const resolvedFolder = _resolveSavedStrategyFolder(strategyName, folderName, strategyId);
  if (!resolvedFolder) {
    toast(`Folder not found for "${strategyName || 'this strategy'}"`, 'warn');
    return;
  }

  const folderEl = document.getElementById(_savedFolderDomId(resolvedFolder));
  if (!folderEl) {
    toast(`Folder "${resolvedFolder}" not found`, 'warn');
    return;
  }

  const header = folderEl.querySelector('[data-folder-toggle]');
  toggleSavedStrategyFolder(header, true);
  folderEl.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const matched = _findSavedStrategyByRef(strategyName, resolvedFolder, strategyId);
  let targetRow = null;
  if (matched?.id) {
    targetRow = folderEl.querySelector(`[data-strategy-id="${matched.id}"]`);
  }
  if (!targetRow) {
    const key = _normalizeStrategyKey(strategyName);
    if (key) {
      targetRow = Array.from(folderEl.querySelectorAll('[data-strategy-name]')).find((row) =>
        _normalizeStrategyKey(row.getAttribute('data-strategy-name')) === key
      );
    }
  }
  _highlightSavedStrategyRow(targetRow);
}

function _liveEngineIdentityKey(runId, mode = '') {
  return `${String(mode || '').trim()}:${String(runId || '').trim()}`;
}

function _findLiveEngineIndex(runId, mode = '') {
  const targetRunId = String(runId || '');
  const targetMode = String(mode || '');
  return (_liveEngines || []).findIndex((engine) => {
    const sameRunId = String(engine.run_id || '') === targetRunId;
    if (!sameRunId) return false;
    return !targetMode || String(engine.mode || '') === targetMode;
  });
}

function _findLiveEngine(runId, mode = '') {
  const idx = _findLiveEngineIndex(runId, mode);
  return idx >= 0 ? _liveEngines[idx] : null;
}

async function openLiveRunMonitor(runId, mode = '') {
  showPage('live-page', document.getElementById('nav-live'));
  startLiveMonitor();
  await loadLiveMonitor();
  const idx = _findLiveEngineIndex(runId, mode);
  if (idx >= 0) {
    selectLiveTab(idx);
    return;
  }
  toast('Running strategy panel not found', 'warn');
}

function _bindDashboardLeaderboardCard(el, entry, defaultTitle) {
  if (!el) return;
  el.dataset.kind = String(entry?.kind || '');
  el.dataset.rid = String(entry?.id || '');
  el.dataset.runId = String(entry?.run_id || '');
  el.dataset.mode = String(entry?.mode || '');
  const clickable = !!(entry && (entry.id || entry.run_id || entry.kind === 'scalp'));
  el.style.cursor = clickable ? 'pointer' : 'default';
  if (!entry) {
    el.title = defaultTitle || '';
    return;
  }
  if (entry.kind === 'run') {
    el.title = 'Open run details';
  } else if (entry.kind === 'engine') {
    el.title = 'Open live monitor';
  } else if (entry.kind === 'scalp') {
    el.title = 'Open scalp monitor';
  } else {
    el.title = defaultTitle || '';
  }
}

async function openDashboardLeaderboardCard(el) {
  if (!el) return;
  const kind = String(el.dataset.kind || '');
  const rid = Number(el.dataset.rid || 0);
  const runId = String(el.dataset.runId || '');
  const mode = String(el.dataset.mode || '');
  if (kind === 'run' && rid) {
    await viewRun(rid);
    return;
  }
  if (kind === 'engine' && runId) {
    await openLiveRunMonitor(runId, mode);
    return;
  }
  if (kind === 'scalp') {
    showPage('scalp-page', document.getElementById('nav-scalp'));
    initScalpPage();
  }
}

function renderRunningArsenal(engines) {
  const wrap = document.getElementById('arsenal-running-wrap');
  const grid = document.getElementById('arsenal-running-grid');
  if (!wrap || !grid) return;

  const running = (engines || []).filter((engine) => !!engine?.running);
  if (!running.length) {
    wrap.style.display = 'none';
    grid.innerHTML = '';
    return;
  }

  wrap.style.display = 'block';
  grid.innerHTML = running.map((engine) => {
    const runId = String(engine.run_id || '');
    const strategyName = String(engine.strategy_name || engine.run_id || 'Strategy');
    const strategyId = Number(engine.strategy_id || (engine.strategy || {}).strategy_id || 0);
    const mode = engine.mode === 'auto' ? 'Live' : 'Paper';
    const modeColor = engine.mode === 'auto' ? 'var(--danger)' : 'var(--accent)';
    const modeBg = engine.mode === 'auto' ? 'rgba(239,68,68,0.12)' : 'rgba(20,184,166,0.12)';
    const modeBorder = engine.mode === 'auto' ? 'rgba(239,68,68,0.22)' : 'rgba(20,184,166,0.22)';
    const folderName = _resolveSavedStrategyFolder(strategyName, engine.folder || (engine.strategy || {}).folder || '', strategyId);
    const savedStrategy = _findSavedStrategyByRef(strategyName, folderName, strategyId);
    const pnl = round2(engine.total_pnl || 0);
    const pnlColor = pnl >= 0 ? 'var(--success)' : 'var(--danger)';
    const tradesToday = engine.trades_today || 0;
    const instLabel = INST_NAMES[engine.instrument] || (engine.instrument ? `Instrument ${engine.instrument}` : 'NIFTY');
    const statusLabel = engine.in_trade ? 'In Trade' : 'Waiting';
    const statusColor = engine.in_trade ? '#4ade80' : '#f59e0b';
    return `<div class="card card-compact arsenal-template-card" onclick="openLiveRunMonitor('${escapeJsSingleQuoted(runId)}','${escapeJsSingleQuoted(engine.mode || '')}')" style="border-color:rgba(16,185,129,0.18);" onmouseover="this.style.borderColor='rgba(16,185,129,0.45)';this.style.boxShadow='0 0 20px rgba(16,185,129,0.12)'" onmouseout="this.style.borderColor='rgba(16,185,129,0.18)';this.style.boxShadow='none'">
      <div style="position:absolute;top:-14px;right:-14px;width:54px;height:54px;background:radial-gradient(circle,rgba(16,185,129,0.12) 0%,transparent 70%);pointer-events:none;"></div>
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:8px;">
        <div style="min-width:0;">
          <div class="arsenal-running-card-name">${escapeHtml(strategyName)}</div>
          <div style="display:flex;align-items:center;gap:6px;margin-top:6px;flex-wrap:wrap;">
            <span style="display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:999px;font-size:10px;font-weight:800;background:${modeBg};color:${modeColor};border:1px solid ${modeBorder};text-transform:uppercase;letter-spacing:0.55px;">${escapeHtml(mode)}</span>
            <span style="display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:999px;font-size:10px;font-weight:800;background:rgba(34,197,94,0.12);color:${statusColor};border:1px solid rgba(34,197,94,0.22);text-transform:uppercase;letter-spacing:0.55px;">${escapeHtml(statusLabel)}</span>
          </div>
        </div>
        <div style="text-align:right;flex-shrink:0;">
          <div style="font-size:16px;font-weight:800;font-family:'JetBrains Mono';color:${pnlColor};">₹${pnl.toFixed(2)}</div>
          <div class="arsenal-running-trades">${escapeHtml(String(tradesToday))} trade${tradesToday === 1 ? '' : 's'} today</div>
        </div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:10px;">
        <span class="arsenal-tag instrument" style="font-size:10px;padding:3px 8px;">${escapeHtml(instLabel)}</span>
        <button type="button" onclick="event.stopPropagation();openSavedStrategyFolder('${escapeJsSingleQuoted(strategyName)}','${escapeJsSingleQuoted(folderName || '')}',${strategyId || 0})" title="${escapeAttr(`Open folder: ${folderName || 'Intraday'}`)}" style="display:inline-flex;align-items:center;gap:5px;font-size:10px;padding:3px 8px;border-radius:999px;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.24);color:var(--warn);font-weight:700;cursor:pointer;">${ICO.folder(10)} ${escapeHtml(folderName || 'Intraday')}</button>
        ${savedStrategy ? `<button class="btn btn-sm arsenal-running-edit-btn" onclick="event.stopPropagation();showPage('builder-page', document.getElementById('nav-builder'));ensureStrategiesLoaded().then(() => loadStrategy(${savedStrategy.id}))" style="font-size:11px;padding:4px 12px;">Edit</button>` : ''}
      </div>
      <div style="display:flex;justify-content:center;align-items:center;">
        <button class="btn btn-sm" onclick="event.stopPropagation();openLiveRunMonitor('${escapeJsSingleQuoted(runId)}','${escapeJsSingleQuoted(engine.mode || '')}')" style="font-size:11px;padding:5px 18px;min-width:108px;text-align:center;">Open Live</button>
      </div>
    </div>`;
  }).join('');
}

async function refreshStrategyArsenalRunning() {
  const wrap = document.getElementById('arsenal-running-wrap');
  const grid = document.getElementById('arsenal-running-grid');
  if (!wrap || !grid) return;
  const hadExistingCards = grid.children.length > 0;
  try {
    const res = await fetch('/api/engines/all');
    if (await handleUnauthorizedResponse(res)) return;
    if (!res.ok) throw new Error('Failed to load running strategies');
    const data = await res.json();
    renderRunningArsenal(data.engines || []);
  } catch (err) {
    console.warn('Running arsenal load failed:', err);
    if (!hadExistingCards) {
      wrap.style.display = 'none';
      grid.innerHTML = '';
    }
  }
}

async function fetchStrategies() {
  try {
    const res = await fetch('/api/strategies');
    if (await handleUnauthorizedResponse(res)) return;
    if (!res.ok) throw new Error('Failed to load strategies');
    const strats = await res.json();
    savedStrategiesCache = strats;
    const container = document.getElementById('saved-strategies-grouped');
    const emptyMsg = document.getElementById('saved-strategies-empty');

    if (strats.length === 0) { container.innerHTML = ''; emptyMsg.style.display = 'block'; return; }
    emptyMsg.style.display = 'none';

    // Group by folder
    const groups = {};
    strats.forEach(s => {
      const folder = s.folder || 'Intraday';
      if (!groups[folder]) groups[folder] = [];
      groups[folder].push(s);
    });

    let html = '';
    Object.keys(groups).sort().forEach(folder => {
      const items = groups[folder].filter(s => !s._placeholder && s.run_name).reverse();
      const safeFolder = escapeHtml(folder);
      html += `<div id="${escapeAttr(_savedFolderDomId(folder))}" style="margin-bottom: 18px;">
        <div data-folder-toggle="1" style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px; cursor: pointer;" onclick="toggleSavedStrategyFolder(this);">
          <span class="fold-arrow" style="color: var(--muted); font-size: 11px;">▶</span>
          <span style="background: rgba(168,85,247,0.12); color: var(--purple); padding: 5px 14px; border-radius: 5px; font-size: 12px; font-weight: 700; letter-spacing: 0.5px;">${safeFolder}</span>
          <span style="color: var(--muted); font-size: 12px;">${items.length} strateg${items.length > 1 ? 'ies' : 'y'}</span>
        </div>
        <div style="display: none;">`;
      if (!items.length) {
        html += `<div style="padding: 16px; color: var(--muted); font-size: 12px; text-align: center;">Empty folder — move or save strategies here</div>`;
      } else {
        html += `<table style="width: 100%; text-align: left; border-collapse: collapse; table-layout: fixed;">
            <thead><tr style="border-bottom: 1px solid var(--border); color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.3px; font-weight: 600;"><th style="padding: 10px 0; width: 6%;">ID</th><th style="padding: 10px 0; width: 28%;">Name</th><th style="padding: 10px 0; width: 16%;">Instrument</th><th style="padding: 10px 0; width: 10%;">Legs</th><th style="padding: 10px 0; width: 20%;">Saved</th><th style="padding: 10px 0; width: 20%;">Actions</th></tr></thead>
            <tbody>`;
      }
      items.forEach(s => {
        const dateStr = new Date(s.created_at).toLocaleString('en-IN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        const instName = escapeHtml(s.instrument === '26000' ? 'NIFTY' : (s.instrument === '26009' ? 'BNIFTY' : s.instrument));
        const legCount = (s.legs || []).length;
        html += `<tr data-strategy-id="${s.id}" data-strategy-name="${escapeAttr(s.run_name || s.name || '')}" style="border-bottom: 1px solid var(--border); font-size: 13px;">
          <td style="padding: 10px 0; color: var(--muted); width: 6%">${s.id}</td>
          <td style="padding: 10px 0; font-weight: 600; color: var(--accent); cursor: pointer; width: 28%" onclick="viewStrategy(${s.id})">${escapeHtml(s.run_name || 'Unnamed')}</td>
          <td style="padding: 10px 0; width: 16%"><span style="background: var(--card2); padding: 3px 8px; border-radius: 3px; font-size: 12px; display: block">${instName}</span></td>
          <td style="padding: 10px 0; color: var(--muted); width: 10%; text-align: center">${legCount}</td>
          <td style="padding: 10px 0; color: var(--muted); font-size: 12px; width: 20%">${escapeHtml(dateStr)}</td>
          <td style="padding: 10px 0; width: 20%;">
            <button class="btn btn-sm" onclick="viewStrategy(${s.id})" style="font-size: 11px; padding: 5px 10px; margin-right: 3px;">View</button>
            <button class="btn btn-sm" onclick="loadStrategy(${s.id})" style="--btn-bg: linear-gradient(180deg, rgba(var(--pf-tint-primary-rgb, 0,200,150),0.25) 0%, rgba(0,150,110,0.45) 100%); --btn-border: rgba(var(--pf-tint-primary-rgb, 0,200,150),0.45); --btn-color: rgb(52,211,153); font-size: 11px; padding: 5px 10px; margin-right: 3px;">Edit</button>
            <button class="btn btn-sm" onclick="moveStrategyFolder(${s.id})" style="--btn-bg: var(--card2); --btn-color: var(--muted); --btn-border: var(--border); font-size: 11px; padding: 5px 10px; margin-right: 3px;">Move</button>
            <button class="btn btn-danger btn-sm" onclick="deleteStrategy(${s.id})" style="font-size: 11px; padding: 5px 10px;">Del</button>
          </td></tr>`;
      });
      if (items.length) html += `</tbody></table>`;
      html += `</div></div>`;
    });
    container.innerHTML = html;
    _refreshFolderDropdown();
  } catch (err) { console.error("Failed to fetch strategies", err); }
}

function showDetailsModal(data, title) {
  const instName = getInstrumentName(data.instrument) || data.instrument || '-';
  const legs = data.legs || [];
  const entryConds = data.entry_conditions || [];
  const exitConds = data.exit_conditions || [];
  const inds = data.indicators || [];
  const safeRunName = escapeHtml(data.run_name || '-');
  const safeFolder = escapeHtml(data.folder || 'Intraday');
  const safeInstName = escapeHtml(instName);
  const safeOrderType = escapeHtml((data.deploy_config || {}).product_type || data.product_type || 'MIS');

  const chipStyle = "display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;font-family:'JetBrains Mono', monospace;margin:0 6px 6px 0;white-space:nowrap;";

  const condRightVal = (c) => c.right === 'number' ? c.right_number_value : c.right === 'days' ? (c.right_days || []).join(', ') : c.right === 'time' ? (c.right_time || '') : c.right;
  const strikeLabel = (l) => {
    const sv = l.strike_value || '';
    const st = l.strike_type || 'atm';
    return st === 'atm' ? 'ATM'
      : st === 'premium_above' ? `Premium Above ₹${sv}`
      : st === 'premium_below' ? `Premium Below ₹${sv}`
      : st === 'premium_near' ? `Premium ~₹${sv}`
      : st === 'otm' ? `OTM +${sv}`
      : st === 'itm' ? `ITM -${sv}`
      : st === 'strike_price' ? `Strike ${sv}`
      : st === 'spot_price' ? `Spot ± ${sv}`
      : String(st).toUpperCase();
  };

  let html = `
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px;">
      <div><span style="color: var(--muted); font-size: 11px;">Run Name</span><div style="font-weight: 600;">${safeRunName}</div></div>
      <div><span style="color: var(--muted); font-size: 11px;">Folder</span><div><span style="background: rgba(168,85,247,0.12); color: var(--purple); padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600;">${safeFolder}</span></div></div>
      <div><span style="color: var(--muted); font-size: 11px;">Instrument</span><div style="font-weight: 600;">${safeInstName}</div></div>
      <div><span style="color: var(--muted); font-size: 11px;">Lots × Size</span><div style="font-weight: 600;">${data.lots || 1} × ${data.lot_size || _getLotSizeForInstrument(data.instrument)}</div></div>
      ${data.from_date ? `<div><span style="color: var(--muted); font-size: 11px;">Period</span><div style="font-weight: 600;">${escapeHtml(data.from_date)} → ${escapeHtml(data.to_date || '-')}</div></div>` : ''}
      <div><span style="color: var(--muted); font-size: 11px;">Strategy SL</span><div style="font-weight: 600;">${data.sl_type === 'rupees' ? '₹' + (data.stoploss_rupees || 0) : (data.stoploss_pct || 0) + '%'}</div></div>
      <div><span style="color: var(--muted); font-size: 11px;">Target Profit</span><div style="font-weight: 600;">${data.tp_type === 'rupees' ? '₹' + (data.target_profit_rupees || 0) : (data.target_profit_pct || 0) + '%'}</div></div>
      <div><span style="color: var(--muted); font-size: 11px;">Market Hours</span><div style="font-weight: 600;">${escapeHtml(data.market_open || '09:15')} — ${escapeHtml(data.market_close || '15:25')}</div></div>
      <div><span style="color: var(--muted); font-size: 11px;">Max Trades/Day</span><div style="font-weight: 600;">${data.max_trades_per_day || '—'}</div></div>
      <div><span style="color: var(--muted); font-size: 11px;">Order Type</span><div style="font-weight: 600;">${safeOrderType}</div></div>
    </div>`;

  // Indicators
  if (inds.length > 0) {
    html += `<div style="margin-bottom: 14px;"><span style="color: var(--muted); font-size: 11px; text-transform: uppercase; display: block; margin-bottom: 6px;">Indicators (${inds.length})</span><div style="display:flex;flex-wrap:wrap;">`;
    inds.forEach(i => { html += `<span style="${chipStyle} background: rgba(var(--pf-tint-primary-rgb, 0,200,150),0.12); border: 1px solid rgba(var(--pf-tint-primary-rgb, 0,200,150),0.22);">${escapeHtml(i)}</span>`; });
    html += `</div></div>`;
  }

  // Entry conditions
  if (entryConds.length > 0) {
    html += `<div style="margin-bottom: 14px;"><span style="color: var(--accent); font-size: 11px; text-transform: uppercase; display: block; margin-bottom: 6px;">Entry Conditions (${entryConds.length})</span><div style="display:flex;flex-wrap:wrap;align-items:center;">`;
    entryConds.forEach((c, i) => {
      const logic = i === 0 ? 'IF' : (c.logic || 'AND');
      html += `<span style="${chipStyle} background: rgba(34,197,94,0.12); border: 1px solid rgba(34,197,94,0.24);"><span style="color: var(--muted);">${escapeHtml(logic)}</span> ${escapeHtml(c.left || '')} <span style="color: var(--accent);">${escapeHtml(c.operator || '')}</span> ${escapeHtml(condRightVal(c))}</span>`;
    });
    html += `</div></div>`;
  }

  // Exit conditions
  if (exitConds.length > 0) {
    html += `<div style="margin-bottom: 14px;"><span style="color: var(--warn); font-size: 11px; text-transform: uppercase; display: block; margin-bottom: 6px;">Exit Conditions (${exitConds.length})</span><div style="display:flex;flex-wrap:wrap;align-items:center;">`;
    exitConds.forEach((c, i) => {
      const logic = i === 0 ? 'IF' : (c.logic || 'AND');
      html += `<span style="${chipStyle} background: rgba(245,158,11,0.12); border: 1px solid rgba(245,158,11,0.24);"><span style="color: var(--muted);">${escapeHtml(logic)}</span> ${escapeHtml(c.left || '')} <span style="color: var(--warn);">${escapeHtml(c.operator || '')}</span> ${escapeHtml(condRightVal(c))}</span>`;
    });
    html += `</div></div>`;
  }

  // Legs
  if (legs.length > 0) {
    html += `<div><span style="color: var(--muted); font-size: 11px; text-transform: uppercase; display: block; margin-bottom: 6px;">Legs (${legs.length})</span>`;
    legs.forEach((l, i) => {
      const color = l.transaction_type === 'BUY' ? (l.option_type === 'CE' ? 'var(--success)' : 'var(--danger)') : (l.option_type === 'CE' ? 'var(--warn)' : 'var(--purple)');
      html += `<div style="padding: 6px 10px; background: rgba(0,0,0,0.15); border-left: 3px solid ${color}; border-radius: 4px; margin-bottom: 5px; font-size: 12px;">
        <span style="font-weight: 700; color: ${color};">${escapeHtml(l.transaction_type || '')} ${escapeHtml(l.option_type || '')}</span>
        <span style="color: var(--muted); margin-left: 8px;">Strike: ${escapeHtml(strikeLabel(l))}</span>
        <span style="color: var(--muted); margin-left: 8px;">Lots: ${l.lots || 1}</span>
        ${l.sl_pct ? `<span style="color:var(--danger);margin-left:8px;">SL:${l.sl_pct}%</span>` : ''}
        ${l.target_pct ? `<span style="color:var(--success);margin-left:8px;">TP:${l.target_pct}%</span>` : ''}
      </div>`;
    });
    html += `</div>`;
  }

  document.getElementById('view-modal-title').textContent = title || data.run_name || 'Strategy Details';
  // Show/hide Load into Builder based on whether it's a saved strategy
  const loadBtn = document.getElementById('view-modal-load-btn');
  if (loadBtn) loadBtn.style.display = viewingStrategyId ? '' : 'none';
  document.getElementById('view-modal-content').innerHTML = html;
  document.getElementById('view-strategy-modal').classList.add('open');
}

function viewStrategy(id) {
  const s = savedStrategiesCache.find(x => x.id === id);
  if (!s) return;
  viewingStrategyId = id;
  showDetailsModal(s, s.run_name);
}

function viewRunDetails(data) {
  viewingStrategyId = null;
  showDetailsModal(data, data.run_name);
}

function closeViewModal() { document.getElementById('view-strategy-modal').classList.remove('open'); }
function loadStrategyFromView() { closeViewModal(); if (viewingStrategyId) loadStrategy(viewingStrategyId); }

function loadStrategy(id) {
  const s = savedStrategiesCache.find(x => x.id === id);
  if (!s) { toast('Strategy not found', 'danger'); return; }
  currentLoadedStrategyId = Number(id) || null;

  // 1. Basic fields
  document.getElementById('run-name-input').value = s.run_name || '';
  if (s.segment) {
    document.getElementById('segment-select').value = s.segment;
    onSegmentChange();
  }
  document.getElementById('instrument-select').value = s.instrument || '';
  // Restore SL type and value
  const slType = s.sl_type || 'rupees';
  document.getElementById('sl-type').value = slType;
  document.getElementById('txn-sl').value = slType === 'rupees' ? (s.stoploss_rupees || '') : (s.stoploss_pct || '');
  // Restore TP type and value
  const tpType = s.tp_type || 'rupees';
  document.getElementById('tp-type').value = tpType;
  document.getElementById('target-profit').value = tpType === 'rupees' ? (s.target_profit_rupees || '') : (s.target_profit_pct || '');
  document.getElementById('entry-time-start').value = s.market_open || '09:15';
  document.getElementById('sq-time').value = s.market_close || '15:25';
  document.getElementById('max-trades-per-day').value = s.max_trades_per_day || 1;
  document.getElementById('max-daily-loss').value = s.max_daily_loss || 0;
  if (s.from_date) document.getElementById('bt-from-date').value = s.from_date;
  if (s.to_date) document.getElementById('bt-to-date').value = s.to_date;

  // Restore folder
  if (s.folder) {
    const folderSel = document.getElementById('folder-select');
    const folderCustom = document.getElementById('folder-custom');
    if (folderSel.querySelector(`option[value="${s.folder}"]`) && s.folder !== '__custom__') {
      folderSel.value = s.folder;
      folderCustom.style.display = 'none';
    } else {
      folderSel.value = '__custom__';
      folderCustom.style.display = 'block';
      folderCustom.value = s.folder;
    }
  }

  // 2. Indicators — clear and re-add
  myIndicators = [];
  document.getElementById('active-indicators-list').innerHTML = '';
  if (s.indicators && Array.isArray(s.indicators)) {
    s.indicators.forEach(indId => {
      myIndicators.push(indId);
      const badge = document.createElement('span');
      badge.id = `badge-${indId}`;
      const isCPR = indId.startsWith('CPR');
      badge.style = isCPR
        ? "display:inline-flex;align-items:center;padding:5px 10px;background:linear-gradient(135deg, var(--accent2), var(--purple));color:white;border-radius:4px;font-weight:600;font-size:12px;"
        : "display:inline-flex;align-items:center;padding:5px 10px;background:var(--accent2);color:white;border-radius:4px;font-weight:600;font-size:12px;";
      const displayName = isCPR ? `CPR (${indId.replace('CPR_','').replace('_','% / ')}%)` : indId;
      badge.innerHTML = `${displayName} <span style="cursor:pointer;margin-left:8px;color:#ffb3b3;font-size:14px;" onclick="removeIndicator('${indId}')">&times;</span>`;
      document.getElementById('active-indicators-list').appendChild(badge);
    });
  }
  syncConditionDropdowns();

  // 3. Entry conditions — clear and rebuild
  const entryContainer = document.getElementById('entry-conditions-container');
  entryContainer.innerHTML = '';
  conditionCounters.entry = 0;
  if (s.entry_conditions && s.entry_conditions.length > 0) {
    s.entry_conditions.forEach((cond, i) => {
      addConditionRow('entry');
      const row = entryContainer.lastElementChild;
      if (row.querySelector('.left-op')) row.querySelector('.left-op').value = cond.left || 'current_close';
      // Trigger LHS change to populate RHS correctly
      const lhsSelect = row.querySelector('.left-op');
      if (lhsSelect) onLHSChange(lhsSelect);
      if (row.querySelector('.operator')) row.querySelector('.operator').value = cond.operator || 'is_above';
      // Restore Time Of Day / Day Of Week / standard RHS
      if (cond.left === 'Time_Of_Day') {
        const ti = row.querySelector('.time-rhs');
        if (ti) ti.value = cond.right_time || '11:00';
      } else if (cond.left === 'Day_Of_Week') {
        const days = cond.right_days || [];
        row.querySelectorAll('.day-opt input').forEach(cb => { cb.checked = days.includes(cb.value); });
        const label = row.querySelector('.day-picker-toggle');
        if (label && days.length > 0) label.textContent = days.map(d => d.substring(0,3)).join(', ') + ' \u25BE';
      } else {
        if (row.querySelector('.right-op')) row.querySelector('.right-op').value = cond.right || 'current_close';
        if (cond.right === 'number' && row.querySelector('.right-num')) {
          row.querySelector('.right-num').style.display = 'block';
          row.querySelector('.right-num').value = cond.right_number_value || '';
        }
      }
      if (i > 0) {
        const connector = row.previousElementSibling;
        if (connector && connector.classList.contains('condition-connector')) {
          const logicSel = connector.querySelector('.logic-op');
          if (logicSel) {
            logicSel.value = cond.logic || 'AND';
            logicSel.dispatchEvent(new Event('change'));
          }
        }
      }
    });
  } else { addConditionRow('entry'); }

  // 4. Exit conditions — same pattern
  const exitContainer = document.getElementById('exit-conditions-container');
  exitContainer.innerHTML = '';
  conditionCounters.exit = 0;
  if (s.exit_conditions && s.exit_conditions.length > 0) {
    s.exit_conditions.forEach((cond, i) => {
      addConditionRow('exit');
      const row = exitContainer.lastElementChild;
      if (row.querySelector('.left-op')) row.querySelector('.left-op').value = cond.left || 'current_close';
      const lhsSelect = row.querySelector('.left-op');
      if (lhsSelect) onLHSChange(lhsSelect);
      if (row.querySelector('.operator')) row.querySelector('.operator').value = cond.operator || 'is_above';
      // Restore Time Of Day / Day Of Week / standard RHS
      if (cond.left === 'Time_Of_Day') {
        const ti = row.querySelector('.time-rhs');
        if (ti) ti.value = cond.right_time || '11:00';
      } else if (cond.left === 'Day_Of_Week') {
        const days = cond.right_days || [];
        row.querySelectorAll('.day-opt input').forEach(cb => { cb.checked = days.includes(cb.value); });
        const label = row.querySelector('.day-picker-toggle');
        if (label && days.length > 0) label.textContent = days.map(d => d.substring(0,3)).join(', ') + ' \u25BE';
      } else {
        if (row.querySelector('.right-op')) row.querySelector('.right-op').value = cond.right || 'current_close';
        if (cond.right === 'number' && row.querySelector('.right-num')) {
          row.querySelector('.right-num').style.display = 'block';
          row.querySelector('.right-num').value = cond.right_number_value || '';
        }
      }
      if (i > 0) {
        const connector = row.previousElementSibling;
        if (connector && connector.classList.contains('condition-connector')) {
          const logicSel = connector.querySelector('.logic-op');
          if (logicSel) {
            logicSel.value = cond.logic || 'AND';
            logicSel.dispatchEvent(new Event('change'));
          }
        }
      }
    });
  } else { addConditionRow('exit'); }

  // 5. Legs — clear and re-add
  legs = []; legCounter = 0;
  document.getElementById('legs-container').innerHTML = '';
  document.getElementById('legs-empty').style.display = 'block';
  document.getElementById('combined-pnl-bar').style.display = 'none';
  if (s.legs && s.legs.length > 0) {
    s.legs.forEach(leg => {
      addLeg(leg.transaction_type, leg.option_type);
      const id = legCounter - 1;
      const setVal = (elId, val) => { const el = document.getElementById(elId); if (el && val) el.value = val; };
      setVal(`leg-${id}-expiry`, leg.expiry);
      setVal(`leg-${id}-strike-type`, leg.strike_type);
      if (leg.strike_type && leg.strike_type !== 'atm') toggleStrikeFields(id);
      setVal(`leg-${id}-strike-value`, leg.strike_value);
      setVal(`leg-${id}-lots`, leg.lots);
      setVal(`leg-${id}-sl-pct`, leg.sl_pct || '');
      setVal(`leg-${id}-target-pct`, leg.target_pct || '');
      setVal(`leg-${id}-sl-points`, leg.sl_points || '');
      setVal(`leg-${id}-target-points`, leg.target_points || '');
      setVal(`leg-${id}-sl-rupees`, leg.sl_rupees || '');
      setVal(`leg-${id}-target-rupees`, leg.target_rupees || '');
      setVal(`leg-${id}-trail-pct`, leg.trail_pct || '');
      setVal(`leg-${id}-sqoff-time`, leg.sqoff_time || '15:20');
    });
  }

  // 6. Combined P&L
  if (s.combined_sl_rupees) document.getElementById('combined-sl-rupees').value = s.combined_sl_rupees;
  if (s.combined_target_rupees) document.getElementById('combined-target-rupees').value = s.combined_target_rupees;
  if (s.combined_sqoff_time) document.getElementById('combined-sqoff-time').value = s.combined_sqoff_time;
  if (s.fee_pct !== undefined) document.getElementById('fee-pct').value = s.fee_pct;
  if (s.trailing_sl_pct !== undefined) document.getElementById('trailing-sl-pct').value = s.trailing_sl_pct;
  if (s.initial_capital) document.getElementById('initial-capital').value = s.initial_capital;
  restoreExecutionSettings(s);

  // Switch to builder page
  document.getElementById('nav-builder').click();

  // Update the Loaded Strategy panel on right side
  const panel = document.getElementById('loaded-strategy-info');
  const instName = s.instrument === '26000' ? 'NIFTY 50' : (s.instrument === '26009' ? 'BANK NIFTY' : s.instrument);
  const legCount = (s.legs || []).length;
  const entryCount = (s.entry_conditions || []).length;
  const exitCount = (s.exit_conditions || []).length;
  const indCount = (s.indicators || []).length;
  panel.innerHTML = `
    <div style="margin-bottom: 10px;">
      <span style="color: var(--accent); font-weight: 600; font-size: 15px;">${s.run_name}</span>
      <span style="color: var(--muted); font-size: 12px; margin-left: 8px;">ID: ${s.id}</span>
    </div>
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 12px;">
      <div><span style="color: var(--muted);">Instrument:</span> <strong>${instName}</strong></div>
      <div><span style="color: var(--muted);">Lots:</span> <strong>${s.lots || 1} × ${s.lot_size || _getLotSizeForInstrument(s.instrument)}</strong></div>
      <div><span style="color: var(--muted);">Indicators:</span> <strong>${indCount}</strong></div>
      <div><span style="color: var(--muted);">Legs:</span> <strong>${legCount}</strong></div>
      <div><span style="color: var(--muted);">Entry Rules:</span> <strong>${entryCount}</strong></div>
      <div><span style="color: var(--muted);">Exit Rules:</span> <strong>${exitCount}</strong></div>
      <div><span style="color: var(--muted);">SL:</span> <strong>${s.sl_type === 'rupees' ? '₹' + (s.stoploss_rupees || 0) : (s.stoploss_pct || 0) + '%'}</strong></div>
      <div><span style="color: var(--muted);">Saved:</span> <strong>${new Date(s.created_at).toLocaleString('en-IN', {month:'short',day:'numeric'})}</strong></div>
    </div>
  `;
  toast(`Loaded strategy: ${s.run_name}`, 'success');
}
async function deleteStrategy(id) {
  const ok = await customConfirm('Are you sure you want to delete this saved strategy?', { title: 'Delete Strategy', icon: ICO.trash(28), okText: 'Delete', danger: true });
  if (!ok) return;
  try { const res = await fetch(`/api/strategies/${id}`, { method: 'DELETE' }); if(res.ok) { toast('Strategy deleted', 'warn'); fetchStrategies(); } }
  catch(err) { toast('Error deleting', 'danger'); }
}

// ══════════════════════════════════════════════════════════════
//  INDICATORS
// ══════════════════════════════════════════════════════════════
let myIndicators = [];
function renderIndicatorFields() {
  const name = document.getElementById('new-indicator-name').value;
  const c = document.getElementById('dynamic-indicator-fields');
  const tf = `<select id="ind-tf" aria-label="Indicator timeframe" style="width:100px"><option value="1">1 Min</option><option value="3">3 Min</option><option value="5" selected>5 Min</option><option value="15">15 Min</option><option value="30">30 Min</option><option value="60">1 Hour</option></select>`;
  if (name === 'EMA' || name === 'SMA') c.innerHTML = `<input type="number" id="ind-period" value="14" style="width:80px" title="Period">` + tf;
  else if (name === 'Supertrend') c.innerHTML = `<input type="number" id="ind-period" value="10" style="width:80px"><input type="number" id="ind-multiplier" value="3" step="0.1" style="width:80px">` + tf;
  else if (name === 'RSI') c.innerHTML = `<input type="number" id="ind-period" value="14" style="width:80px" title="Period">` + tf;
  else if (name === 'MACD') c.innerHTML = `<input type="number" id="ind-macd-fast" value="12" style="width:60px" title="Fast"><input type="number" id="ind-macd-slow" value="26" style="width:60px" title="Slow"><input type="number" id="ind-macd-signal" value="9" style="width:60px" title="Signal">` + tf;
  else if (name === 'BB') c.innerHTML = `<input type="number" id="ind-period" value="20" style="width:80px" title="Period"><input type="number" id="ind-bb-std" value="2" step="0.1" style="width:80px" title="Std Dev">` + tf;
  else if (name === 'ATR' || name === 'ADX') c.innerHTML = `<input type="number" id="ind-period" value="14" style="width:80px" title="Period">` + tf;
  else if (name === 'StochRSI') c.innerHTML = `<input type="number" id="ind-period" value="14" style="width:80px" title="Period">` + tf;
  else if (name === 'VWAP' || name === 'Current_Candle') c.innerHTML = tf;
  else if (name === 'ORB') c.innerHTML = `<div style="display:flex;align-items:center;gap:6px"><label style="font-size:11px;color:var(--muted);white-space:nowrap;">Minutes:</label><input type="number" id="ind-orb-minutes" value="15" style="width:70px" title="ORB window in minutes" min="5" max="60" step="5"></div>` + tf;
  else if (name === 'Previous_Day' || name === 'CPR') c.innerHTML = '';
  else if (name === 'Signal_Candle') c.innerHTML = tf;
}
function addIndicator() {
  const name = document.getElementById('new-indicator-name').value;

  // CPR opens a configuration modal instead of adding directly
  if (name === "CPR") {
    if (myIndicators.some(i => i.startsWith('CPR'))) { toast('CPR already added!', 'warn'); return; }
    document.getElementById('cpr-modal').classList.add('open');
    return;
  }

  let id = "";
  if (name === "Current_Candle") id = `Current_Candle_${document.getElementById('ind-tf').value}m`;
  else if (name === "Previous_Day") id = "Previous_Day";
  else if (name === "Signal_Candle") id = `Signal_Candle_${document.getElementById('ind-tf').value}m`;
  else if (name === "ORB") {
    const mins = document.getElementById('ind-orb-minutes').value || 15;
    id = `ORB_${mins}min`;
  }
  else if (name === "EMA" || name === "SMA") id = `${name}_${document.getElementById('ind-period').value}_${document.getElementById('ind-tf').value}m`;
  else if (name === "RSI") id = `RSI_${document.getElementById('ind-period').value}_${document.getElementById('ind-tf').value}m`;
  else if (name === "MACD") id = `MACD_${document.getElementById('ind-macd-fast').value}_${document.getElementById('ind-macd-slow').value}_${document.getElementById('ind-macd-signal').value}_${document.getElementById('ind-tf').value}m`;
  else if (name === "BB") id = `BB_${document.getElementById('ind-period').value}_${document.getElementById('ind-bb-std').value}_${document.getElementById('ind-tf').value}m`;
  else if (name === "VWAP") id = `VWAP_${document.getElementById('ind-tf').value}m`;
  else if (name === "ATR" || name === "ADX" || name === "StochRSI") id = `${name}_${document.getElementById('ind-period').value}_${document.getElementById('ind-tf').value}m`;
  else if (name === "Supertrend") id = `Supertrend_${document.getElementById('ind-period').value}_${document.getElementById('ind-multiplier').value}_${document.getElementById('ind-tf').value}m`;

  if (!myIndicators.includes(id)) {
    myIndicators.push(id);
    const badge = document.createElement('span');
    badge.id = `badge-${id}`;
    badge.style = "display:inline-flex;align-items:center;padding:5px 10px;background:var(--accent2);color:white;border-radius:4px;font-weight:600;font-size:12px;";
    let displayId = id.replace(/_/g, ' ');
    badge.innerHTML = `${displayId} <span style="cursor:pointer;margin-left:8px;color:#ffb3b3;font-size:14px;" onclick="removeIndicator('${id}')">&times;</span>`;
    document.getElementById('active-indicators-list').appendChild(badge);
    syncConditionDropdowns();
    toast(`Added ${id}`, 'success');
  } else toast('Already added!', 'warn');
}

// ── CPR MODAL FUNCTIONS ──
function closeCPRModal() { document.getElementById('cpr-modal').classList.remove('open'); }

function confirmAddCPR() {
  const narrowPct = parseFloat(document.getElementById('cpr-narrow-pct').value) || 0.2;
  const moderatePct = parseFloat(document.getElementById('cpr-moderate-pct').value) || 0.5;
  const tf = document.getElementById('cpr-timeframe').value || 'D';

  // Encode config: CPR_0.2_0.5 (daily) or CPR_0.2_0.5_W (weekly), etc.
  const indId = tf === 'D' ? `CPR_${narrowPct}_${moderatePct}` : `CPR_${narrowPct}_${moderatePct}_${tf}`;
  const tfLabels = { D: 'Daily', '4H': '4H', W: 'Weekly', M: 'Monthly' };
  const tfLabel = tfLabels[tf] || tf;

  // Allow multiple CPR with different timeframes
  if (myIndicators.includes(indId)) {
    toast(`CPR ${tfLabel} already added!`, 'warn');
    closeCPRModal();
    return;
  }

  myIndicators.push(indId);

  const badge = document.createElement('span');
  badge.id = `badge-${indId}`;
  badge.style = "display:inline-flex;align-items:center;padding:5px 10px;background:linear-gradient(135deg, var(--accent2), var(--purple));color:white;border-radius:4px;font-weight:600;font-size:12px;";
  badge.innerHTML = `CPR ${tfLabel} (N:${narrowPct}% M:${moderatePct}%) <span style="cursor:pointer;margin-left:8px;color:#ffb3b3;font-size:14px;" onclick="removeIndicator('${indId}')">&times;</span>`;
  document.getElementById('active-indicators-list').appendChild(badge);

  syncConditionDropdowns();
  closeCPRModal();
  toast(`Added CPR ${tfLabel} (Narrow ≤${narrowPct}%, Moderate ≤${moderatePct}%)`, 'success');
}

// ── INDICATOR SUB-COLUMNS for condition dropdowns ──
function _buildCPRColumns() {
  const cols = [];
  const tfLabels = { D: 'Daily', '4H': '4H', W: 'Weekly', M: 'Monthly' };
  const cprInds = myIndicators.filter(i => i.startsWith('CPR'));
  const timeframes = cprInds.map(ind => {
    const p = ind.split('_');
    return p.length > 3 ? p[3].toUpperCase() : 'D';
  });
  // Deduplicate
  const unique = [...new Set(timeframes)];
  if (!unique.length) unique.push('D');
  unique.forEach(tf => {
    const prefix = tf === 'D' ? 'CPR_' : `CPR_${tf}_`;
    const label = tf === 'D' ? 'CPR' : `CPR ${tfLabels[tf] || tf}`;
    cols.push({ value: `${prefix}Pivot`, label: `${label} — Pivot` });
    cols.push({ value: `${prefix}TC`, label: `${label} — TC` });
    cols.push({ value: `${prefix}BC`, label: `${label} — BC` });
    ['R0.5','R1','R1.5','R2','R2.5','R3','R3.5','R4','R4.5','R5'].forEach(lvl => {
      cols.push({ value: `${prefix}${lvl}`, label: `${label} — ${lvl}` });
    });
    ['S0.5','S1','S1.5','S2','S2.5','S3','S3.5','S4','S4.5','S5'].forEach(lvl => {
      cols.push({ value: `${prefix}${lvl}`, label: `${label} — ${lvl}` });
    });
    cols.push({ value: `${prefix}width_pct`, label: `${label} — Width %` });
    cols.push({ value: `${prefix}is_narrow`, label: `${label} — Is Narrow` });
    cols.push({ value: `${prefix}is_moderate`, label: `${label} — Is Moderate` });
    cols.push({ value: `${prefix}is_wide`, label: `${label} — Is Wide` });
  });
  return cols;
}
// Backward compat: static reference for any code that reads CPR_CONDITION_COLUMNS directly
const CPR_CONDITION_COLUMNS = _buildCPRColumns();
const CANDLE_COLUMNS = [
  { value: "current_open",   label: "Current Candle — Open" },
  { value: "current_high",   label: "Current Candle — High" },
  { value: "current_low",    label: "Current Candle — Low" },
  { value: "current_close",  label: "Current Candle — Close" },
  { value: "current_volume", label: "Current Candle — Volume" },
];
const PREV_DAY_COLUMNS = [
  { value: "Yesterday_Open",  label: "Prev Day — Open" },
  { value: "Yesterday_High",  label: "Prev Day — High" },
  { value: "Yesterday_Low",   label: "Prev Day — Low" },
  { value: "Yesterday_Close", label: "Prev Day — Close" },
];
const ORB_COLUMNS = [
  { value: "ORB_High",             label: "ORB — High" },
  { value: "ORB_Low",              label: "ORB — Low" },
  { value: "ORB_Range",            label: "ORB — Range (pts)" },
  { value: "ORB_is_breakout_up",   label: "ORB — Breakout Up (true/false)" },
  { value: "ORB_is_breakout_down", label: "ORB — Breakout Down (true/false)" },
  { value: "ORB_is_inside",        label: "ORB — Inside Range (true/false)" },
];
const SIGNAL_CANDLE_COLUMNS = [
  { value: "Signal_Candle_Open",  label: "Signal Candle — Open" },
  { value: "Signal_Candle_High",  label: "Signal Candle — High" },
  { value: "Signal_Candle_Low",   label: "Signal Candle — Low" },
  { value: "Signal_Candle_Close", label: "Signal Candle — Close" },
];
const BOOLEAN_FIELDS = [
  'CPR_is_narrow', 'CPR_is_moderate', 'CPR_is_wide',
  'CPR_4H_is_narrow', 'CPR_4H_is_moderate', 'CPR_4H_is_wide',
  'CPR_W_is_narrow', 'CPR_W_is_moderate', 'CPR_W_is_wide',
  'CPR_M_is_narrow', 'CPR_M_is_moderate', 'CPR_M_is_wide',
  'ORB_is_breakout_up', 'ORB_is_breakout_down', 'ORB_is_inside',
  'ORB_Breakout_Up', 'ORB_Breakout_Down', 'ORB_Inside',
];

// Special condition types that get custom UIs
// Special LHS types handled in onLHSChange

function _isDynamicConditionIndicator(indicatorId) {
  return !indicatorId.startsWith('CPR') &&
    !indicatorId.startsWith('Current_Candle') &&
    !indicatorId.startsWith('Signal_Candle') &&
    indicatorId !== 'Previous_Day' &&
    !indicatorId.startsWith('ORB');
}

function _formatIndicatorConditionLabel(indicatorId) {
  return indicatorId.replace(/_/g, ' ');
}

function _buildIndicatorConditionOptions(indicatorId) {
  const label = _formatIndicatorConditionLabel(indicatorId);

  if (indicatorId.startsWith('MACD_')) {
    return [
      { value: indicatorId, label: `${label} - Line` },
      { value: `${indicatorId}_signal`, label: `${label} - Signal` },
      { value: `${indicatorId}_histogram`, label: `${label} - Histogram` },
    ];
  }

  if (indicatorId.startsWith('BB_')) {
    return [
      { value: indicatorId, label: `${label} - Middle` },
      { value: `${indicatorId}_upper`, label: `${label} - Upper` },
      { value: `${indicatorId}_lower`, label: `${label} - Lower` },
      { value: `${indicatorId}_width`, label: `${label} - Width %` },
    ];
  }

  if (indicatorId.startsWith('StochRSI_')) {
    return [
      { value: indicatorId, label: `${label} - %K` },
      { value: `${indicatorId}_D`, label: `${label} - %D` },
    ];
  }

  if (indicatorId.startsWith('ADX_')) {
    return [
      { value: indicatorId, label: `${label} - ADX` },
      { value: `${indicatorId}_plus_di`, label: `${label} - Plus DI` },
      { value: `${indicatorId}_minus_di`, label: `${label} - Minus DI` },
    ];
  }

  return [{ value: indicatorId, label }];
}

function _buildDynamicIndicatorOptionsHtml() {
  let html = '';
  myIndicators.forEach(indicatorId => {
    if (!_isDynamicConditionIndicator(indicatorId)) return;
    _buildIndicatorConditionOptions(indicatorId).forEach(opt => {
      html += `<option value="${opt.value}">${opt.label}</option>`;
    });
  });
  return html;
}

function ensureConditionOption(select, value, label) {
  if (!select || value === undefined || value === null || value === '') return;
  const exists = Array.from(select.options).some(option => option.value === value);
  if (exists) return;
  const option = document.createElement('option');
  option.value = value;
  option.textContent = label || `${value} (legacy)`;
  option.dataset.legacy = 'true';
  select.appendChild(option);
}

function removeIndicator(id) {
  myIndicators = myIndicators.filter(i => i !== id);
  const b = document.getElementById(`badge-${id}`); if(b) b.remove();
  syncConditionDropdowns(); toast(`Removed ${id}`, 'warn');
}

// ══════════════════════════════════════════════════════════════
//  CONDITIONS (smart RHS based on LHS selection)
// ══════════════════════════════════════════════════════════════
let conditionCounters = { entry: 0, exit: 0 };

function addConditionRow(type) {
  const container = document.getElementById(`${type}-conditions-container`);

  // Validate: Don't allow adding conditions without indicators or Current Candle
  if (myIndicators.length === 0) {
    toast('Please add indicators first before creating conditions!', 'warn');
    return;
  }

  // Reset counter if container is empty (all conditions were deleted)
  if (container.children.length === 0) {
    conditionCounters[type] = 0;
  }
  const rowId = conditionCounters[type]++;
  const isFirst = container.children.length === 0;

  // If not first row, insert a centered AND/OR connector between rows
  if (!isFirst) {
    const connector = document.createElement('div');
    connector.className = 'condition-connector';
    connector.id = `${type}-connector-${rowId}`;
    connector.innerHTML = `<select class="logic-op" onchange="this.style.background=this.value==='OR'?'rgba(245,158,11,0.12)':'rgba(var(--pf-tint-primary-rgb, 0,200,150),0.12)';this.style.borderColor=this.value==='OR'?'rgba(245,158,11,0.3)':'rgba(var(--pf-tint-primary-rgb, 0,200,150),0.3)';this.style.color=this.value==='OR'?'var(--warn)':'var(--accent)'"><option value="AND">AND</option><option value="OR">OR</option></select>`;
    container.appendChild(connector);
  }

  const row = document.createElement('div');
  row.className = 'flex-row condition-row'; row.id = `${type}-row-${rowId}`;
  let lhsOpts = buildLHSOptions();
  let rhsOpts = buildRHSOptions(null);
  row.innerHTML = `
    <select class="condition-select left-op" style="flex:2;min-width:140px" onchange="onLHSChange(this)">${lhsOpts}</select>
    <select class="operator" style="flex:1;min-width:110px">
      <option value="crosses_above">Crosses Above</option>
      <option value="is_above">Is Above</option>
      <option value="crosses_below">Crosses Below</option>
      <option value="is_below">Is Below</option>
      <option value="touches">Touches</option>
      <option value=">=">Equal or Above</option>
      <option value="<=">Equal or Below</option>
      <option value="==">Equal To</option>
    </select>
    <div class="rhs-wrap" style="flex:1;display:flex;gap:5px;align-items:center;">
      <select class="condition-select right-op" onchange="toggleNumberInput(this)" style="flex:2;min-width:140px;">${rhsOpts}</select>
      <input type="number" class="right-num" style="display:none;width:100px;padding:8px;font-size:13px;" placeholder="Enter value">
    </div>
    <button type="button" class="btn btn-danger btn-sm" onclick="removeConditionRow('${type}',${rowId})" title="Delete">&#x1F5D1;</button>`;
  container.appendChild(row);
}

function buildLHSOptions() {
  const hasCPR = myIndicators.some(i => i.startsWith('CPR'));
  const hasPrevDay = myIndicators.includes('Previous_Day');
  const hasORB = myIndicators.some(i => i.startsWith('ORB'));
  const hasSignalCandle = myIndicators.some(i => i.startsWith('Signal_Candle'));
  let html = `<optgroup label="\ud83d\udd6f\ufe0f Current Candle">`;
  CANDLE_COLUMNS.forEach(c => { html += `<option value="${c.value}">${c.label}</option>`; });
  html += `</optgroup>`;
  if (hasPrevDay) { html += `<optgroup label="\u2500\u2500 Previous Day \u2500\u2500">`; PREV_DAY_COLUMNS.forEach(c => { html += `<option value="${c.value}">${c.label}</option>`; }); html += `</optgroup>`; }
  if (hasORB) { html += `<optgroup label="\u2500\u2500 ORB \u2500\u2500">`; ORB_COLUMNS.forEach(c => { html += `<option value="${c.value}">${c.label}</option>`; }); html += `</optgroup>`; }
  if (hasCPR) { const _cc = _buildCPRColumns(); html += `<optgroup label="\u2500\u2500 CPR \u2500\u2500">`; _cc.forEach(c => { html += `<option value="${c.value}">${c.label}</option>`; }); html += `</optgroup>`; }
  if (hasSignalCandle) { html += `<optgroup label="\u2500\u2500 Signal Candle \u2500\u2500">`; SIGNAL_CANDLE_COLUMNS.forEach(c => { html += `<option value="${c.value}">${c.label}</option>`; }); html += `</optgroup>`; }
  html += _buildDynamicIndicatorOptionsHtml();
  html += `<optgroup label="\u2500\u2500 Day / Time \u2500\u2500"><option value="Time_Of_Day">Time Of Day</option><option value="Day_Of_Week">Day Of Week</option></optgroup>`;
  return html;
}

function buildRHSOptions(lhsValue) {
  const hasCPR = myIndicators.some(i => i.startsWith('CPR'));
  const hasPrevDay = myIndicators.includes('Previous_Day');
  const hasORB = myIndicators.some(i => i.startsWith('ORB'));
  const hasSignalCandle = myIndicators.some(i => i.startsWith('Signal_Candle'));
  const isBool = BOOLEAN_FIELDS.includes(lhsValue);
  let html = '';

  // For boolean fields, show true/false first
  if (isBool) {
    html += '<option value="true">true</option><option value="false">false</option>';
  }

  // Add Current Candle columns FIRST (default selection)
  html += `<optgroup label="\ud83d\udd6f\ufe0f Current Candle">`;
  CANDLE_COLUMNS.forEach(c => { html += `<option value="${c.value}">${c.label}</option>`; });
  html += '</optgroup>';

  // Add other indicator options
  if (hasPrevDay) {
    html += `<optgroup label="\u2500\u2500 Previous Day \u2500\u2500">`;
    PREV_DAY_COLUMNS.forEach(c => { html += `<option value="${c.value}">${c.label}</option>`; });
    html += '</optgroup>';
  }
  if (hasORB) {
    html += `<optgroup label="\u2500\u2500 ORB \u2500\u2500">`;
    ORB_COLUMNS.forEach(c => { html += `<option value="${c.value}">${c.label}</option>`; });
    html += '</optgroup>';
  }
  if (hasCPR) {
    const _cc = _buildCPRColumns();
    html += `<optgroup label="\u2500\u2500 CPR \u2500\u2500">`;
    _cc.forEach(c => { html += `<option value="${c.value}">${c.label}</option>`; });
    html += '</optgroup>';
  }
  if (hasSignalCandle) {
    html += `<optgroup label="\u2500\u2500 Signal Candle \u2500\u2500">`;
    SIGNAL_CANDLE_COLUMNS.forEach(c => { html += `<option value="${c.value}">${c.label}</option>`; });
    html += '</optgroup>';
  }

  // Add custom indicators
  html += _buildDynamicIndicatorOptionsHtml();

  // Add "Number" option at the END (not default)
  html += '<option value="number">Number</option>';

  return html;
}

function onLHSChange(lhsSelect) {
  const row = lhsSelect.closest('.condition-row');
  const opSelect = row.querySelector('.operator');
  const rhsWrap = row.querySelector('.rhs-wrap');
  const lhsVal = lhsSelect.value;

  if (lhsVal === 'Time_Of_Day') {
    opSelect.innerHTML = '<option value="is_below">Is Below</option><option value="is_above">Is Above</option><option value="<=">Equal or Below</option><option value=">=">Equal or Above</option>';
    rhsWrap.innerHTML = '<input type="time" class="time-rhs" value="11:00" step="1" style="flex:1; font-family: JetBrains Mono, monospace; font-size: 14px; text-align: center;">';
  } else if (lhsVal === 'Day_Of_Week') {
    opSelect.innerHTML = '<option value="contains">Contains</option><option value="not_contains">Not Contains</option>';
    rhsWrap.innerHTML = `<div class="day-picker" style="flex:1;position:relative;">
      <div class="day-picker-toggle" onclick="toggleDayDropdown(this)" style="padding:6px 10px;background:var(--card2);border:1px solid var(--border);border-radius:6px;cursor:pointer;font-size:12px;color:var(--muted);">Can select multiple days \u25BE</div>
      <div class="day-picker-dd" style="display:none;position:absolute;top:100%;left:0;right:0;z-index:100;background:var(--card);border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.5);margin-top:4px;padding:4px 0;">
        <label class="day-opt" style="display:block;padding:10px 16px;cursor:pointer;font-size:14px;border-bottom:1px solid var(--border);" onmouseover="this.style.background='rgba(var(--pf-tint-primary-rgb, 0,200,150),0.08)'" onmouseout="this.style.background='transparent'"><input type="checkbox" value="Monday" style="margin-right:10px;accent-color:var(--accent);" onchange="updateDayLabel(this)"> Monday</label>
        <label class="day-opt" style="display:block;padding:10px 16px;cursor:pointer;font-size:14px;border-bottom:1px solid var(--border);" onmouseover="this.style.background='rgba(var(--pf-tint-primary-rgb, 0,200,150),0.08)'" onmouseout="this.style.background='transparent'"><input type="checkbox" value="Tuesday" style="margin-right:10px;accent-color:var(--accent);" onchange="updateDayLabel(this)"> Tuesday</label>
        <label class="day-opt" style="display:block;padding:10px 16px;cursor:pointer;font-size:14px;border-bottom:1px solid var(--border);" onmouseover="this.style.background='rgba(var(--pf-tint-primary-rgb, 0,200,150),0.08)'" onmouseout="this.style.background='transparent'"><input type="checkbox" value="Wednesday" style="margin-right:10px;accent-color:var(--accent);" onchange="updateDayLabel(this)"> Wednesday</label>
        <label class="day-opt" style="display:block;padding:10px 16px;cursor:pointer;font-size:14px;border-bottom:1px solid var(--border);" onmouseover="this.style.background='rgba(var(--pf-tint-primary-rgb, 0,200,150),0.08)'" onmouseout="this.style.background='transparent'"><input type="checkbox" value="Thursday" style="margin-right:10px;accent-color:var(--accent);" onchange="updateDayLabel(this)"> Thursday</label>
        <label class="day-opt" style="display:block;padding:10px 16px;cursor:pointer;font-size:14px;border-bottom:1px solid var(--border);" onmouseover="this.style.background='rgba(var(--pf-tint-primary-rgb, 0,200,150),0.08)'" onmouseout="this.style.background='transparent'"><input type="checkbox" value="Friday" style="margin-right:10px;accent-color:var(--accent);" onchange="updateDayLabel(this)"> Friday</label>
        <label class="day-opt" style="display:block;padding:10px 16px;cursor:pointer;font-size:14px;" onmouseover="this.style.background='rgba(var(--pf-tint-primary-rgb, 0,200,150),0.08)'" onmouseout="this.style.background='transparent'"><input type="checkbox" value="Saturday" style="margin-right:10px;accent-color:var(--accent);" onchange="updateDayLabel(this)"> Saturday</label>
      </div>
    </div>`;
  } else {
    const isBool = BOOLEAN_FIELDS.includes(lhsVal);
    opSelect.innerHTML = isBool
      ? '<option value="==">Equal To</option><option value="is_true">Is True</option><option value="is_false">Is False</option>'
      : '<option value="crosses_above">Crosses Above</option><option value="is_above">Is Above</option><option value="crosses_below">Crosses Below</option><option value="is_below">Is Below</option><option value="touches">Touches</option><option value=">=">Equal or Above</option><option value="<=">Equal or Below</option><option value="==">Equal To</option>';
    rhsWrap.innerHTML = `<select class="condition-select right-op" onchange="toggleNumberInput(this)" style="flex:1;min-width:120px;">${buildRHSOptions(lhsVal)}</select><input type="number" class="right-num" style="display:none;width:100px;padding:8px;font-size:13px;" placeholder="Enter value">`;
  }
}

function toggleDayDropdown(el) { const dd = el.nextElementSibling; dd.style.display = dd.style.display === 'none' ? 'block' : 'none'; }
function updateDayLabel(cb) {
  const picker = cb.closest('.day-picker');
  const checks = picker.querySelectorAll('input:checked');
  const label = picker.querySelector('.day-picker-toggle');
  if (checks.length === 0) label.textContent = 'Can select multiple days \u25BE';
  else label.textContent = Array.from(checks).map(c => c.value.substring(0,3)).join(', ') + ' \u25BE';
}

function removeConditionRow(type, id) {
  const r = document.getElementById(`${type}-row-${id}`);
  const c = document.getElementById(`${type}-connector-${id}`);
  if(c) c.remove();
  if(r) r.remove();
  // If first row was deleted, remove the connector of the new first row
  const container = document.getElementById(`${type}-conditions-container`);
  if (container) {
    const firstChild = container.firstElementChild;
    if (firstChild && firstChild.classList.contains('condition-connector')) {
      firstChild.remove(); // remove orphaned connector at top
    }
    if (container.children.length === 0) {
      conditionCounters[type] = 0;
    }
  }
}
function toggleNumberInput(sel) {
  const row = sel.closest('.condition-row');
  if (!row) {
    console.error('toggleNumberInput: Could not find .condition-row parent');
    return;
  }

  const rhsWrap = row.querySelector('.rhs-wrap');
  if (!rhsWrap) {
    console.error('toggleNumberInput: Could not find .rhs-wrap');
    return;
  }

  const inp = rhsWrap.querySelector('.right-num');
  if (!inp) {
    console.error('toggleNumberInput: Could not find .right-num input');
    return;
  }

  console.log('toggleNumberInput called, selected value:', sel.value);

  if (sel.value === 'number') {
    inp.style.display = 'block';
    inp.style.width = '100px';
    setTimeout(() => inp.focus(), 100);
    console.log('Number input shown');
  } else {
    inp.style.display = 'none';
    inp.value = '';
    console.log('Number input hidden');
  }
}

function syncConditionDropdowns() {
  document.querySelectorAll('.condition-row').forEach(row => {
    const lhsSel = row.querySelector('.left-op');
    if (!lhsSel) return;
    const curLHS = lhsSel.value;
    lhsSel.innerHTML = buildLHSOptions();
    ensureConditionOption(lhsSel, curLHS);
    lhsSel.value = curLHS;
    if (curLHS !== 'Time_Of_Day' && curLHS !== 'Day_Of_Week') {
      const rhsSel = row.querySelector('.right-op');
      if (rhsSel) {
        const curRHS = rhsSel.value;
        rhsSel.innerHTML = buildRHSOptions(curLHS);
        ensureConditionOption(rhsSel, curRHS);
        rhsSel.value = curRHS;
      }
    }
  });
}

function populateConditionRows(type, conditions) {
  (conditions || []).forEach((cond, i) => {
    addConditionRow(type);
    const row = document.getElementById(`${type}-row-${i}`);
    if (!row) return;

    const lhsSelect = row.querySelector('.left-op');
    const defaultOperator = type === 'entry' ? 'is_above' : 'is_below';

    if (lhsSelect) {
      const lhsValue = cond.left || 'current_close';
      ensureConditionOption(lhsSelect, lhsValue);
      lhsSelect.value = lhsValue;
      onLHSChange(lhsSelect);
    }

    const opSelect = row.querySelector('.operator');
    if (opSelect) opSelect.value = cond.operator || defaultOperator;

    if (cond.left === 'Time_Of_Day') {
      const timeInput = row.querySelector('.time-rhs');
      if (timeInput) timeInput.value = cond.right_time || '11:00';
    } else if (cond.left === 'Day_Of_Week') {
      const days = cond.right_days || [];
      row.querySelectorAll('.day-opt input').forEach(cb => { cb.checked = days.includes(cb.value); });
    } else {
      const rhsSelect = row.querySelector('.right-op');
      const rhsValue = cond.right || 'current_close';
      if (rhsSelect) {
        ensureConditionOption(rhsSelect, rhsValue);
        rhsSelect.value = rhsValue;
      }
      if (cond.right === 'number') {
        const numInput = row.querySelector('.right-num');
        if (numInput) {
          numInput.value = cond.right_number_value || '';
          numInput.style.display = 'block';
        }
      }
    }

    if (i > 0) {
      const connector = row.previousElementSibling;
      if (connector && connector.classList.contains('condition-connector')) {
        const logicSel = connector.querySelector('.logic-op');
        if (logicSel) {
          logicSel.value = cond.logic || cond.connector || 'AND';
          logicSel.dispatchEvent(new Event('change'));
        }
      }
    }
  });
}

function gatherConditions(type) {
  const rows = document.querySelectorAll(`#${type}-conditions-container .condition-row`);
  let arr = [];
  rows.forEach((row, i) => {
    const lhs = row.querySelector('.left-op').value;
    const op = row.querySelector('.operator').value;
    // Logic connector: for first row it's "IF", otherwise look in the preceding connector div
    let logic = "IF";
    if (i > 0) {
      const prevEl = row.previousElementSibling;
      if (prevEl && prevEl.classList.contains('condition-connector')) {
        const logicSel = prevEl.querySelector('.logic-op');
        logic = logicSel ? logicSel.value : "AND";
      } else {
        logic = "AND";
      }
    }
    if (lhs === 'Time_Of_Day') {
      const ti = row.querySelector('.time-rhs');
      arr.push({ logic, left: "Time_Of_Day", operator: op, right: "time", right_time: ti ? ti.value : "11:00" });
    } else if (lhs === 'Day_Of_Week') {
      const checks = row.querySelectorAll('.day-opt input:checked');
      arr.push({ logic, left: "Day_Of_Week", operator: op, right: "days", right_days: Array.from(checks).map(c => c.value) });
    } else {
      const rs = row.querySelector('.right-op');
      const nm = row.querySelector('.right-num');
      arr.push({ logic, left: lhs, operator: op, right: rs ? rs.value : "number", right_number_value: nm ? nm.value : "" });
    }
  });
  return arr;
}


// ══════════════════════════════════════════════════════════════
//  LEG BUILDER
// ══════════════════════════════════════════════════════════════
let legs = [];
let legCounter = 0;

function addLeg(txn, opt) {
  const id = legCounter++;
  const cls = `${txn.toLowerCase()}-${opt.toLowerCase()}`;
  legs.push({ id, transaction_type: txn, option_type: opt });

  const container = document.getElementById('legs-container');
  const card = document.createElement('div');
  card.className = `leg-card ${cls}`;
  card.id = `leg-${id}`;
  card.innerHTML = `
    <div class="leg-header">
      <div class="flex-row" style="gap:8px"><span class="leg-badge ${cls}">${txn} ${opt}</span><span style="color:var(--muted);font-size:12px">Leg #${id+1}</span></div>
      <button class="leg-remove" onclick="removeLeg(${id})" title="Remove">&times;</button>
    </div>
    <div class="leg-grid">
      <div><label>Expiry</label><select id="leg-${id}-expiry"><option value="current_week">Current Week</option><option value="next_week">Next Week</option><option value="current_month">Current Month</option><option value="next_month">Next Month</option></select></div>
      <div><label>Strike Selection</label><select id="leg-${id}-strike-type" onchange="toggleStrikeFields(${id})"><option value="atm">ATM (At The Money)</option><option value="strike_price">Strike Price</option><option value="spot_price">Spot ± Offset</option><option value="otm">OTM by Offset</option><option value="itm">ITM by Offset</option><option value="premium_near">Premium Near</option><option value="premium_above">Premium Above</option><option value="premium_below">Premium Below</option></select></div>
      <div id="leg-${id}-strike-wrap" style="display:none"><label id="leg-${id}-strike-label">Value</label><input type="number" id="leg-${id}-strike-value" placeholder="Auto" step="50"></div>
      <div><label>Lots</label><input type="number" id="leg-${id}-lots" value="1" min="1"></div>
    </div>
    <div class="exit-section">
      <h4>Exit Controls — Leg #${id+1}</h4>
      <div class="exit-grid">
        <div><label>SL %</label><input type="number" id="leg-${id}-sl-pct" placeholder="e.g. 30"></div>
        <div><label>Target %</label><input type="number" id="leg-${id}-target-pct" placeholder="e.g. 50"></div>
        <div><label>SL Points</label><input type="number" id="leg-${id}-sl-points" placeholder="e.g. 20" step="0.5"></div>
        <div><label>Target Points</label><input type="number" id="leg-${id}-target-points" placeholder="e.g. 30" step="0.5"></div>
        <div><label>SL ₹ Total</label><input type="number" id="leg-${id}-sl-rupees" placeholder="e.g. 2000"></div>
        <div><label>Target ₹ Total</label><input type="number" id="leg-${id}-target-rupees" placeholder="e.g. 5000"></div>
        <div><label>Trail SL %</label><input type="number" id="leg-${id}-trail-pct" placeholder="e.g. 10"></div>
        <div><label>Sq Off Time</label><input type="time" id="leg-${id}-sqoff-time" value="15:20"></div>
      </div>
    </div>`;
  container.appendChild(card);
  document.getElementById('legs-empty').style.display = 'none';
  document.getElementById('combined-pnl-bar').style.display = 'flex';
  toast(`Added ${txn} ${opt} leg`, 'success');
}

function removeLeg(id) {
  legs = legs.filter(l => l.id !== id);
  const el = document.getElementById(`leg-${id}`); if(el) el.remove();
  if(legs.length === 0) { document.getElementById('legs-empty').style.display = 'block'; document.getElementById('combined-pnl-bar').style.display = 'none'; }
  toast('Leg removed', 'warn');
}

function toggleStrikeFields(id) {
  const type = document.getElementById(`leg-${id}-strike-type`).value;
  const wrap = document.getElementById(`leg-${id}-strike-wrap`);
  const label = document.getElementById(`leg-${id}-strike-label`);
  const input = document.getElementById(`leg-${id}-strike-value`);
  if (type === 'atm') { wrap.style.display = 'none'; return; }
  wrap.style.display = 'block';
  const labels = { strike_price: 'Strike Price', spot_price: 'Offset (± pts)', otm: 'OTM Offset', itm: 'ITM Offset', premium_near: 'Premium (₹)', premium_above: 'Min Premium (₹)', premium_below: 'Max Premium (₹)' };
  const placeholders = { strike_price: 'e.g. 22500', spot_price: 'e.g. 100', otm: 'e.g. 200', itm: 'e.g. 100', premium_near: 'e.g. 150', premium_above: 'e.g. 100', premium_below: 'e.g. 200' };
  label.textContent = labels[type] || 'Value';
  input.placeholder = placeholders[type] || '';
  input.step = (type.includes('premium')) ? '1' : '50';
}

function gatherLegs() {
  return legs.map(leg => {
    const id = leg.id;
    const v = (el) => { const e = document.getElementById(el); return e ? e.value : ''; };
    return {
      transaction_type: leg.transaction_type, option_type: leg.option_type,
      expiry: v(`leg-${id}-expiry`), strike_type: v(`leg-${id}-strike-type`),
      strike_value: parseFloat(v(`leg-${id}-strike-value`)) || 0,
      lots: parseInt(v(`leg-${id}-lots`)) || 1,
      sl_pct: parseFloat(v(`leg-${id}-sl-pct`)) || 0,
      target_pct: parseFloat(v(`leg-${id}-target-pct`)) || 0,
      sl_points: parseFloat(v(`leg-${id}-sl-points`)) || 0,
      target_points: parseFloat(v(`leg-${id}-target-points`)) || 0,
      sl_rupees: parseFloat(v(`leg-${id}-sl-rupees`)) || 0,
      target_rupees: parseFloat(v(`leg-${id}-target-rupees`)) || 0,
      trail_pct: parseFloat(v(`leg-${id}-trail-pct`)) || 0,
      sqoff_time: v(`leg-${id}-sqoff-time`),
    };
  });
}

// ══════════════════════════════════════════════════════════════
//  PAYLOAD (now includes legs + combined P&L)
// ══════════════════════════════════════════════════════════════

// Copy a specific run into the Strategy Builder for editing

// Generate a clean copy name: "Foo" → "Foo_copy", "Foo_copy" → "Foo_copy_2", "Foo_copy_3" → "Foo_copy_4"
function generateCopyName(name) {
  // Strip trailing _edited chains first (legacy cleanup)
  name = name.replace(/(_edited)+$/i, '');
  // Check if already a copy name: "Base_copy" or "Base_copy_N"
  const copyMatch = name.match(/^(.+?)_copy(?:_(\d+))?$/);
  if (copyMatch) {
    const base = copyMatch[1];
    const num = parseInt(copyMatch[2] || '1', 10);
    return base + '_copy_' + (num + 1);
  }
  return name + '_copy';
}

async function copyEditRun(runId) {
  try {
    const res = await fetch('/api/runs/' + runId);
    if (!res.ok) throw new Error('Run not found');
    const data = await res.json();
    lastBacktestPayload = data;
    currentViewingRunId = runId;
    await copyEditStrategy(runId);
  } catch(e) {
    toast('Failed to load run #' + runId + ': ' + e.message, 'danger');
  }
}

async function copyEditStrategy(runId) {
  let p = lastBacktestPayload;

  // If a specific runId was given, fetch that run
  if (!p && runId) {
    try {
      const res = await fetch('/api/runs/' + runId);
      p = await res.json();
      lastBacktestPayload = p;
      currentViewingRunId = runId;
    } catch(e) {
      toast('Failed to load run data', 'danger');
      return;
    }
  }

  // If no payload available, try to fetch from current run
  if (!p && currentViewingRunId) {
    try {
      const res = await fetch('/api/runs/' + currentViewingRunId);
      p = await res.json();
    } catch(e) {
      toast('Failed to load run data', 'danger');
      return;
    }
  }

  // If still no payload, try to load the most recent run
  if (!p) {
    try {
      const runsRes = await fetch('/api/runs');
      const allRuns = await runsRes.json();
      if (allRuns && allRuns.length > 0) {
        const latestId = allRuns[allRuns.length - 1].id;
        const res = await fetch('/api/runs/' + latestId);
        p = await res.json();
        currentViewingRunId = latestId;
        lastBacktestPayload = p;
      }
    } catch(e) {}
  }

  if (!p) { toast('No backtest data to copy. Run a backtest first!', 'warn'); return; }

  try {
    currentLoadedStrategyId = null;
    // Switch to builder page
    const builderPage = document.getElementById('builder-page');
    const builderBtn = document.getElementById('nav-builder');
    if (!builderPage || !builderBtn) {
      toast('Strategy builder page not found', 'danger');
      return;
    }

    showPage('builder-page', builderBtn);

    // Basic fields
    document.getElementById('run-name-input').value = generateCopyName(p.run_name || 'Strategy');
    if (p.segment) {
      document.getElementById('segment-select').value = p.segment;
      onSegmentChange();
    }
    if (p.instrument) document.getElementById('instrument-select').value = p.instrument;
    // Restore SL/TP type and value
    const slT = p.sl_type || 'rupees';
    document.getElementById('sl-type').value = slT;
    document.getElementById('txn-sl').value = slT === 'rupees' ? (p.stoploss_rupees || '') : (p.stoploss_pct || '');
    const tpT = p.tp_type || 'rupees';
    document.getElementById('tp-type').value = tpT;
    document.getElementById('target-profit').value = tpT === 'rupees' ? (p.target_profit_rupees || '') : (p.target_profit_pct || '');
    document.getElementById('entry-time-start').value = p.market_open || '09:15';
    document.getElementById('sq-time').value = p.market_close || '15:25';
    document.getElementById('max-trades-per-day').value = p.max_trades_per_day || 1;
    document.getElementById('max-daily-loss').value = p.max_daily_loss || 0;
    if (p.from_date) document.getElementById('bt-from-date').value = p.from_date;
    if (p.to_date) document.getElementById('bt-to-date').value = p.to_date;

    // Restore indicators
    myIndicators = [];
    document.getElementById('active-indicators-list').innerHTML = '';
    if (p.indicators && Array.isArray(p.indicators)) {
      p.indicators.forEach(indId => {
        myIndicators.push(indId);
        const badge = document.createElement('span');
        badge.id = `badge-${indId}`;
        badge.style = "display:inline-flex;gap:6px;align-items:center;padding:4px 8px;background:var(--accent2);color:white;border-radius:3px;font-weight:500;font-size:11px;";
        let dn = indId.replace(/_/g, ' ');
        badge.innerHTML = `${dn} <span style="cursor:pointer;opacity:0.7;" onclick="removeIndicator('${indId}')">[×]</span>`;
        document.getElementById('active-indicators-list').appendChild(badge);
      });
    }
    syncConditionDropdowns();

    // Reset and restore conditions
    document.getElementById('entry-conditions-container').innerHTML = '';
    document.getElementById('exit-conditions-container').innerHTML = '';
    conditionCounters = { entry: 0, exit: 0 };
    populateConditionRows('entry', p.entry_conditions);
    populateConditionRows('exit', p.exit_conditions);

    // Restore legs
    legs = []; legCounter = 0;
    document.getElementById('legs-container').innerHTML = '';
    document.getElementById('legs-empty').style.display = 'block';
    document.getElementById('combined-pnl-bar').style.display = 'none';
    if (p.legs && p.legs.length > 0) {
      p.legs.forEach(leg => {
        addLeg(leg.transaction_type || 'BUY', leg.option_type || 'CE');
        const id = legCounter - 1;
        const setVal = (elId, val) => { const el = document.getElementById(elId); if (el && val !== undefined && val !== null) el.value = val; };
        setVal(`leg-${id}-expiry`, leg.expiry);
        setVal(`leg-${id}-strike-type`, leg.strike_type);
        if (leg.strike_type && leg.strike_type !== 'atm') toggleStrikeFields(id);
        setVal(`leg-${id}-strike-value`, leg.strike_value);
        setVal(`leg-${id}-lots`, leg.lots);
        setVal(`leg-${id}-sl-pct`, leg.sl_pct || '');
        setVal(`leg-${id}-target-pct`, leg.target_pct || '');
        setVal(`leg-${id}-sl-points`, leg.sl_points || '');
        setVal(`leg-${id}-target-points`, leg.target_points || '');
        setVal(`leg-${id}-sl-rupees`, leg.sl_rupees || '');
        setVal(`leg-${id}-target-rupees`, leg.target_rupees || '');
        setVal(`leg-${id}-trail-pct`, leg.trail_pct || '');
        setVal(`leg-${id}-sqoff-time`, leg.sqoff_time || '15:20');
      });
    }

    // Restore combined P&L if present
    if (p.combined_sl_rupees) document.getElementById('combined-sl-rupees').value = p.combined_sl_rupees;
    if (p.combined_target_rupees) document.getElementById('combined-target-rupees').value = p.combined_target_rupees;
    if (p.combined_sqoff_time) document.getElementById('combined-sqoff-time').value = p.combined_sqoff_time;
    if (p.fee_pct !== undefined) document.getElementById('fee-pct').value = p.fee_pct;
    if (p.trailing_sl_pct !== undefined) document.getElementById('trailing-sl-pct').value = p.trailing_sl_pct;
    if (p.initial_capital) document.getElementById('initial-capital').value = p.initial_capital;
    restoreExecutionSettings(p);

    toast('Strategy loaded for editing!', 'success');
  } catch(err) {
    console.error('Copy error:', err);
    toast('Error: ' + err.message, 'danger');
  }
}

function buildPayload() {
  const folderSel = document.getElementById('folder-select').value;
  const folderCustom = document.getElementById('folder-custom').value.trim();
  const folder = (folderSel === '__custom__' && folderCustom) ? folderCustom : folderSel;
  const entryConditions = gatherConditions('entry');
  const exitConditions = gatherConditions('exit');
  const mergedIndicators = normalizeStrategyIndicatorsForPayload(myIndicators, entryConditions, exitConditions);

  return {
    strategy_id: Number(currentLoadedStrategyId || 0) || 0,
    run_name: document.getElementById('run-name-input').value,
    folder: folder,
    segment: document.getElementById('segment-select').value,
    instrument: document.getElementById('instrument-select').value,
    from_date: document.getElementById('bt-from-date').value,
    to_date: document.getElementById('bt-to-date').value,
    lots: legs.length > 0 ? parseInt(legs[0].lots || 1) : 1,
    lot_size: 0,  // Auto-detected per date in backend (NIFTY: 65 from Jan 2026, 75 before)
    stoploss_pct: document.getElementById('sl-type').value === 'pct' ? parseFloat(document.getElementById('txn-sl').value || 0) : 0,
    stoploss_rupees: document.getElementById('sl-type').value === 'rupees' ? parseFloat(document.getElementById('txn-sl').value || 0) : 0,
    sl_type: document.getElementById('sl-type').value,
    target_profit_pct: document.getElementById('tp-type').value === 'pct' ? parseFloat(document.getElementById('target-profit').value || 0) : 0,
    target_profit_rupees: document.getElementById('tp-type').value === 'rupees' ? parseFloat(document.getElementById('target-profit').value || 0) : 0,
    tp_type: document.getElementById('tp-type').value,
    market_open: document.getElementById('entry-time-start').value,
    market_close: document.getElementById('sq-time').value,
    max_trades_per_day: parseInt(document.getElementById('max-trades-per-day').value || 1),
    max_daily_loss: parseFloat(document.getElementById('max-daily-loss').value || 0),
    indicators: mergedIndicators,
    entry_conditions: entryConditions,
    exit_conditions: exitConditions,
    legs: gatherLegs(),
    combined_sl_rupees: parseFloat(document.getElementById('combined-sl-rupees').value) || 0,
    combined_target_rupees: parseFloat(document.getElementById('combined-target-rupees').value) || 0,
    combined_sqoff_time: document.getElementById('combined-sqoff-time').value,
    fee_pct: parseFloat(document.getElementById('fee-pct').value) || 0,
    trailing_sl_pct: parseFloat(document.getElementById('trailing-sl-pct').value) || 0,
    execution_profile: document.getElementById('execution-profile').value || 'auto',
    spread_bps: parseFloat(document.getElementById('spread-bps').value) || 0,
    entry_slippage_bps: parseFloat(document.getElementById('entry-slippage-bps').value) || 0,
    exit_slippage_bps: parseFloat(document.getElementById('exit-slippage-bps').value) || 0,
    entry_delay_candles: parseInt(document.getElementById('entry-delay-candles').value || 0, 10) || 0,
    signal_exit_delay_candles: parseInt(document.getElementById('signal-exit-delay-candles').value || 0, 10) || 0,
    enforce_capital: document.getElementById('enforce-capital').checked,
    capital_buffer_pct: parseFloat(document.getElementById('capital-buffer-pct').value) || 0,
    sell_option_margin_per_lot: parseFloat(document.getElementById('sell-option-margin-per-lot').value) || 0,
    initial_capital: parseFloat(document.getElementById('initial-capital').value) || 500000,
  };
}

function normalizeStrategyIndicatorsForPayload(indicators, entryConditions, exitConditions) {
  const merged = [];
  const seen = new Set();
  const add = (indicatorId) => {
    if (!indicatorId || seen.has(indicatorId)) return;
    merged.push(indicatorId);
    seen.add(indicatorId);
  };

  (indicators || []).forEach(add);

  const cprIndicatorForField = (field) => {
    if (typeof field !== 'string' || !field.startsWith('CPR_')) return null;
    let targetTf = 'D';
    if (field.startsWith('CPR_4H_')) targetTf = '4H';
    else if (field.startsWith('CPR_W_')) targetTf = 'W';
    else if (field.startsWith('CPR_M_')) targetTf = 'M';

    const existing = merged.find(indicatorId => {
      if (typeof indicatorId !== 'string' || !indicatorId.startsWith('CPR')) return false;
      const parts = indicatorId.split('_');
      const tf = (parts.length > 1 && /m$/.test(parts[1])) ? 'D' : (parts[3] || 'D').toUpperCase();
      return tf === targetTf;
    });
    return existing || (targetTf === 'D' ? 'CPR_0.2_0.5' : `CPR_0.2_0.5_${targetTf}`);
  };

  const inferDependency = (field) => {
    if (typeof field !== 'string') return null;
    if (field === 'number') return null;
    if (field.startsWith('CPR_')) return cprIndicatorForField(field);
    if (['ORB_High', 'ORB_Low', 'ORB_Range', 'ORB_is_breakout_up', 'ORB_is_breakout_down', 'ORB_is_inside'].includes(field)) {
      return merged.find(indicatorId => typeof indicatorId === 'string' && indicatorId.startsWith('ORB_')) || 'ORB_15min';
    }
    const outputPatterns = [
      /^(MACD_\d+_\d+_\d+_\d+m)_(signal|histogram)$/,
      /^(BB_\d+_(?:\d+(?:\.\d+)?)_\d+m)_(upper|lower|width)$/,
      /^(StochRSI_\d+_\d+m)_(K|D)$/,
      /^(ADX_\d+_\d+m)_(plus_di|minus_di)$/,
    ];
    for (const pattern of outputPatterns) {
      const match = field.match(pattern);
      if (match) return match[1];
    }
    const directPatterns = [
      /^(EMA|SMA|RSI|ATR|VWAP)_\d+_\d+m$/,
      /^Supertrend_\d+_(?:\d+(?:\.\d+)?)_\d+m$/,
      /^MACD_\d+_\d+_\d+_\d+m$/,
      /^BB_\d+_(?:\d+(?:\.\d+)?)_\d+m$/,
      /^StochRSI_\d+_\d+m$/,
      /^ADX_\d+_\d+m$/,
      /^ORB_\d+min$/,
    ];
    return directPatterns.some(pattern => pattern.test(field)) ? field : null;
  };

  [...(entryConditions || []), ...(exitConditions || [])].forEach(cond => {
    ['left', 'right'].forEach(side => add(inferDependency(cond?.[side])));
  });

  return merged;
}

function getSelectedStrategyTimeframes(indicators) {
  const frames = new Set();
  (indicators || []).forEach(ind => {
    if (typeof ind !== 'string') return;
    const parts = ind.split('_').reverse();
    for (const part of parts) {
      if (/^\d+m$/.test(part)) {
        frames.add(parseInt(part.slice(0, -1), 10));
        break;
      }
    }
  });
  return Array.from(frames).sort((a, b) => a - b);
}

let lastMixedTimeframeHint = '';

function ensureSingleStrategyTimeframe(payload) {
  const frames = getSelectedStrategyTimeframes(payload.indicators);
  if (frames.length <= 1) {
    lastMixedTimeframeHint = '';
    return true;
  }
  const hintKey = frames.join(',');
  if (lastMixedTimeframeHint !== hintKey) {
    toast(
      'Mixed timeframes enabled: entry execution follows the strategy timeframe, while other indicator frames stay as aligned context. Exit-only lower indicators will not pull entries onto a faster timeframe.',
      'info',
      6500
    );
    lastMixedTimeframeHint = hintKey;
  }
  return true;
}

// ══════════════════════════════════════════════════════════════
//  SAVE & BACKTEST
// ══════════════════════════════════════════════════════════════
async function saveStrategy() {
  const payload = buildPayload();
  if(!payload.instrument) { toast('Select an instrument first', 'warn'); return; }
  if (!ensureSingleStrategyTimeframe(payload)) return;
  try {
    const res = await fetch('/api/strategies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (res.ok) {
      const saved = await res.json();
      currentLoadedStrategyId = Number(saved?.id || currentLoadedStrategyId || 0) || null;
      toast('Strategy Saved!', 'success');
      fetchStrategies();
    } else {
      toast('Failed to save.', 'danger');
    }
  } catch (err) { toast('Error connecting to backend.', 'danger'); }
}

let lastBacktestData = null;
let lastBacktestPayload = null;

async function runBacktest() {
  const payload = buildPayload();
  if(!payload.instrument) { toast('Select an instrument first', 'warn'); return; }
  if (!ensureSingleStrategyTimeframe(payload)) return;

  // Switch to results page immediately with loading
  showPage('results-page', document.getElementById('nav-results'));
  document.getElementById('results-empty').style.display = 'block';
  document.getElementById('results-content').style.display = 'none';

  let countdown = 10;
  const emptyDiv = document.getElementById('results-empty');
  emptyDiv.innerHTML = '<div style="text-align:center;"><div style="font-size:48px;font-weight:700;color:var(--accent);font-family:\'JetBrains Mono\',monospace;" id="bt-cd">' + countdown + '</div><div style="color:var(--muted);margin-top:8px;">Fetching candles & running backtest...</div><div style="margin-top:12px;"><div style="width:200px;height:4px;background:var(--card2);border-radius:2px;margin:0 auto;overflow:hidden;"><div id="bt-pr" style="height:100%;background:var(--accent);border-radius:2px;width:0%;transition:width 1s linear;"></div></div></div></div>';
  const cdI = setInterval(() => {
    countdown--;
    const el = document.getElementById('bt-cd');
    const pr = document.getElementById('bt-pr');
    if(el) el.textContent = Math.max(0, countdown);
    if(pr) pr.style.width = Math.min(100, (10-countdown)*10) + '%';
    if(countdown<=0) clearInterval(cdI);
  }, 1000);

  try {
    const res = await fetch('/api/backtest', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    clearInterval(cdI);
    const data = await res.json();
    if(data.status === 'success') {
      lastBacktestData = data;
      lastBacktestPayload = payload;
      renderResults(data, payload);
      if (data.data_range_warning) {
        toast(data.data_range_warning, 'warn', 8000);
      }
      if (data.timeframe_warning) {
        toast(data.timeframe_warning, 'warn', 8000);
      }
      toast('Backtest Complete! ' + data.stats.total_trades + ' trades', 'success');
      fetchRuns();
    } else if(data.status === 'no_trades') {
      emptyDiv.innerHTML = '<h2 style="color:var(--warn);">No trades generated</h2>';
    } else {
      emptyDiv.innerHTML = '<h2 style="color:var(--danger);">Failed: ' + escapeHtml(data.message || 'Error') + '</h2>';
    }
  } catch(err) { clearInterval(cdI); emptyDiv.innerHTML = '<h2 style="color:var(--danger);">Error: ' + escapeHtml(err.message || 'Unknown error') + '</h2>'; }
}

function fmt(n) { return '\u20B9' + Math.round(n).toLocaleString('en-IN'); }
function fmtMoneyPrecise(n, digits = 2, absolute = false) {
  const value = Number(n || 0);
  const display = absolute ? Math.abs(value) : value;
  return '\u20B9' + display.toLocaleString('en-IN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  });
}
function fmtNumber(n, digits = 0, absolute = false) {
  const value = Number(n || 0);
  const display = absolute ? Math.abs(value) : value;
  return display.toLocaleString('en-IN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  });
}

function getInstrumentName(id) {
  const instrumentMap = {
    '26000': 'NIFTY 50',
    '26009': 'BANK NIFTY',
    '1': 'SENSEX',
    '26017': 'NIFTY FIN SVC',
    '26037': 'NIFTY MIDCAP 50',
    '26074': 'NIFTY NEXT 50',
    '26013': 'NIFTY IT',
    'RELIANCE': 'Reliance Industries',
    'TCS': 'TCS',
    'HDFCBANK': 'HDFC Bank',
    'INFY': 'Infosys',
    'ICICIBANK': 'ICICI Bank',
    'HINDUNILVR': 'Hindustan Unilever',
    'ITC': 'ITC',
    'SBIN': 'SBI',
    'BHARTIARTL': 'Bharti Airtel',
    'BAJFINANCE': 'Bajaj Finance',
    'KOTAKBANK': 'Kotak Bank',
    'LT': 'Larsen & Toubro',
    'HCLTECH': 'HCL Tech',
    'ASIANPAINT': 'Asian Paints',
    'AXISBANK': 'Axis Bank',
    'MARUTI': 'Maruti',
    'SUNPHARMA': 'Sun Pharma',
    'TITAN': 'Titan',
    'ULTRACEMCO': 'UltraTech',
    'BAJAJFINSV': 'Bajaj Finserv',
    'WIPRO': 'Wipro',
    'NESTLEIND': 'Nestle',
    'TATAMOTORS': 'Tata Motors',
    'M_M': 'M&M',
    'POWERGRID': 'Power Grid'
  };
  return instrumentMap[id] || id;
}

function renderResults(data, payload) {
  const s = data.stats;
  document.getElementById('results-empty').style.display = 'none';
  document.getElementById('results-content').style.display = 'block';

  // Strategy details now shown via modal popup (View Strategy button)
  document.getElementById('strategy-display-section').innerHTML = '';

  document.getElementById('res-header-pnl').textContent = fmt(s.total_pnl);
  document.getElementById('res-header-pnl').style.color = s.total_pnl >= 0 ? 'var(--success)' : 'var(--danger)';
  document.getElementById('res-from').textContent = payload ? (payload.from_date||'') : '-';
  document.getElementById('res-to').textContent = payload ? (payload.to_date||'') : '-';
  document.getElementById('res-trade-count-badge').textContent = s.total_trades + ' Trades';
  document.getElementById('res-win-rate').textContent = s.win_rate.toFixed(2);
  document.getElementById('res-win-rate').style.color = s.win_rate >= 50 ? 'var(--success)' : 'var(--danger)';
  document.getElementById('res-wl-ratio').textContent = s.winning_trades + ':' + s.losing_trades;
  const riskCapital = Number(s.initial_capital || payload?.initial_capital || 0);
  const riskPct = Number.isFinite(Number(s.risk_per_trade_pct))
    ? Number(s.risk_per_trade_pct)
    : (riskCapital > 0 ? ((Number(s.risk_per_trade || 0) / riskCapital) * 100) : 0);
  document.getElementById('res-risk').textContent = riskPct.toFixed(2) + '%';
  document.getElementById('res-max-dd').textContent = fmtNumber(s.max_drawdown_val||0, 0, true);
  document.getElementById('res-dd-days').textContent = String(s.max_drawdown_days||0);
  document.getElementById('res-avg-profit').textContent = fmtMoneyPrecise(s.avg_profit, 2, false);
  document.getElementById('res-avg-loss').textContent = fmtMoneyPrecise(s.avg_loss, 2, true);
  document.getElementById('res-win-streak').textContent = s.win_streak||0;
  document.getElementById('res-loss-streak').textContent = s.loss_streak||0;
  document.getElementById('res-max-profit').textContent = fmtMoneyPrecise(s.max_profit||0, 0, false);
  document.getElementById('res-max-loss').textContent = fmtMoneyPrecise(s.max_loss||0, 0, true);
  renderEquityChart(data.equity);
  renderDOW(data.day_of_week||[]);
  renderYearly(data.yearly||[]);
  const mpGrid = document.getElementById('monthly-pnl-grid');
  mpGrid.innerHTML = '';
  const monthly = data.monthly || [];
  if (monthly.length) {
    // Group by year for a table layout
    const byYear = {};
    monthly.forEach(m => {
      const [y, mo] = m.month.split('-');
      if (!byYear[y]) byYear[y] = {};
      byYear[y][parseInt(mo)] = m.pnl;
    });
    const moNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    let tbl = '<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr style="border-bottom:1px solid var(--border);"><th style="padding:6px 8px;color:var(--muted);font-size:10px;text-align:left;">Year</th>';
    moNames.forEach(mn => { tbl += `<th style="padding:6px 4px;color:var(--muted);font-size:10px;text-align:center;">${mn}</th>`; });
    tbl += '<th style="padding:6px 8px;color:var(--accent);font-size:10px;text-align:right;">Total</th></tr></thead><tbody>';
    Object.keys(byYear).sort().forEach(yr => {
      let yearTotal = 0;
      tbl += `<tr style="border-bottom:1px solid var(--border);"><td style="padding:6px 8px;font-weight:700;font-size:11px;">${yr}</td>`;
      for (let m = 1; m <= 12; m++) {
        const val = byYear[yr][m] || 0;
        yearTotal += val;
        const isP = val >= 0;
        const bg = val !== 0 ? (isP ? `rgba(34,197,94,${Math.min(0.25, Math.abs(val)/200000)})` : `rgba(239,68,68,${Math.min(0.25, Math.abs(val)/200000)})`) : '';
        const color = val !== 0 ? (isP ? 'var(--success)' : 'var(--danger)') : 'var(--muted)';
        tbl += `<td style="padding:6px 4px;text-align:center;font-family:'JetBrains Mono',monospace;font-weight:600;font-size:11px;color:${color};background:${bg};border-radius:3px;" title="${yr}-${String(m).padStart(2,'0')}: ${fmt(val)}">${val !== 0 ? fmt(val) : '—'}</td>`;
      }
      const ytColor = yearTotal >= 0 ? 'var(--success)' : 'var(--danger)';
      tbl += `<td style="padding:6px 8px;text-align:right;font-family:'JetBrains Mono',monospace;font-weight:700;font-size:12px;color:${ytColor};">${fmt(yearTotal)}</td></tr>`;
    });
    tbl += '</tbody></table>';
    mpGrid.innerHTML = tbl;
  }
  renderHeatmap(data.trades||[]);
  window._allTrades = [...(data.trades||[])].reverse();
  window._tradePage = 1;
  renderTradePage();
}

// ── Trade sort/search state ──
let _tradeSortCol = null;
let _tradeSortAsc = true;
let _tradeSearchQuery = '';

function _tradeSortArrow(col) {
  if (_tradeSortCol !== col) return '<span style="opacity:0.3;font-size:9px;">⇅</span>';
  return _tradeSortAsc ? '<span style="color:var(--accent);font-size:9px;">▲</span>' : '<span style="color:var(--accent);font-size:9px;">▼</span>';
}
function _toggleTradeSort(col) {
  if (_tradeSortCol === col) _tradeSortAsc = !_tradeSortAsc;
  else { _tradeSortCol = col; _tradeSortAsc = col === 'id'; }
  renderTradePage();
}
function _filterTradesBySearch() {
  _tradeSearchQuery = (document.getElementById('trade-search-input')?.value || '').toLowerCase().trim();
  window._tradePage = 1;
  renderTradePage();
}
function _getFilteredTrades() {
  let trades = window._allTrades || [];
  if (_tradeSearchQuery) {
    trades = trades.filter(t => {
      const sk = (t.strike||'').toLowerCase();
      const reason = (t.exit_reason||'').toLowerCase();
      return sk.includes(_tradeSearchQuery) || reason.includes(_tradeSearchQuery) || String(t.id).includes(_tradeSearchQuery);
    });
  }
  if (_tradeSortCol) {
    trades = [...trades].sort((a, b) => {
      const va = a[_tradeSortCol] ?? 0, vb = b[_tradeSortCol] ?? 0;
      if (typeof va === 'number' && typeof vb === 'number') return _tradeSortAsc ? va - vb : vb - va;
      return _tradeSortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
    });
  }
  return trades;
}

function _tradeLogCardHtml(t) {
  const isWin = Number(t.pnl || 0) >= 0;
  const sk = t.strike || 'NIFTY';
  const qty = t.qty || '-';
  const txn = t.txn_type || 'BUY';
  const entryTime = t.entry_time ? String(t.entry_time).substring(11, 16) || t.entry_time : '';
  const exitTime = t.exit_time ? String(t.exit_time).substring(11, 16) || t.exit_time : '';
  const entryDate = t.entry_time ? String(t.entry_time).substring(0, 10) : '';
  const exitDate = t.exit_time ? String(t.exit_time).substring(0, 10) : '';
  const dirColor = txn === 'BUY' ? 'var(--success)' : 'var(--danger)';
  const reasonText = String(t.exit_reason || '').replace(/_/g, ' ') || '—';
  return `<article class="mobile-data-card">
    <div class="mobile-data-card-head">
      <div>
        <div class="mobile-data-card-title">${escapeHtml(sk)}</div>
        <div class="mobile-data-card-sub"><span style="color:${dirColor};font-weight:700;">${escapeHtml(txn)}</span> · #${escapeHtml(t.id)}</div>
      </div>
      <div class="mobile-data-card-value" style="color:${isWin ? 'var(--success)' : 'var(--danger)'}">${isWin ? '+' : ''}${fmt(t.pnl)}</div>
    </div>
    <div class="mobile-data-card-grid">
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Entry</span><span class="mobile-data-card-text">${escapeHtml(entryDate)} ${escapeHtml(entryTime)} · ₹${Number(t.entry_price || 0).toFixed(2)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Exit</span><span class="mobile-data-card-text">${escapeHtml(exitDate)} ${escapeHtml(exitTime)} · ₹${Number(t.exit_price || 0).toFixed(2)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Qty</span><span class="mobile-data-card-text">${escapeHtml(qty)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Reason</span><span class="mobile-data-card-text">${escapeHtml(reasonText)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Cumulative</span><span class="mobile-data-card-text" style="color:${(t.cumulative || 0) >= 0 ? 'var(--success)' : 'var(--danger)'}">${fmt(t.cumulative)}</span></div>
      <div class="mobile-data-card-metric"><span class="mobile-data-card-label">Time</span><span class="mobile-data-card-text">${escapeHtml(entryTime)} → ${escapeHtml(exitTime)}</span></div>
    </div>
  </article>`;
}

function renderTradePage() {
  const PS=10, pg=window._tradePage;
  const trades = _getFilteredTrades();
  const tp=Math.ceil(trades.length/PS)||1, st=(pg-1)*PS;
  const sl=trades.slice(st, st+PS);
  const tcd = document.getElementById('trade-count-display');
  if(tcd) tcd.textContent = trades.length + ' Transactions';
  // Update sort arrows in headers
  ['id','strike','txn_type','entry_price','exit_price','pnl','cumulative'].forEach(col => {
    const el = document.getElementById('ts-' + col);
    if (el) el.innerHTML = _tradeSortArrow(col);
  });
  const tbody=document.getElementById('trade-log-body');
  const cards=document.getElementById('trade-log-mobile-cards');
  tbody.innerHTML='';
  if (!sl.length) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:20px;color:var(--muted);">No trades found</td></tr>';
    if (cards) cards.innerHTML = '<div class="mobile-data-card mobile-data-card-empty">No trades found</div>';
  } else {
    tbody.innerHTML = sl.map(t => {
      const w=t.pnl>=0, sk=t.strike||'NIFTY', qty=t.qty||'-', txn=t.txn_type||'BUY';
      const entryTime = t.entry_time ? String(t.entry_time).substring(11, 16) || t.entry_time : '';
      const exitTime = t.exit_time ? String(t.exit_time).substring(11, 16) || t.exit_time : '';
      const entryDate = t.entry_time ? String(t.entry_time).substring(0, 10) : '';
      const exitDate = t.exit_time ? String(t.exit_time).substring(0, 10) : '';
      const dirColor = txn === 'BUY' ? 'var(--success)' : 'var(--danger)';
      const reasonBg = t.exit_reason === 'StopLoss' ? 'rgba(239,68,68,0.15)' : (t.exit_reason === 'Target' ? 'rgba(34,197,94,0.15)' : 'rgba(59,130,246,0.15)');
      const reasonColor = t.exit_reason === 'StopLoss' ? 'rgb(248,113,113)' : (t.exit_reason === 'Target' ? 'rgb(74,222,128)' : 'rgb(147,197,253)');
      const reasonText = String(t.exit_reason || '').replace(/_/g,' ');
      return `<tr style="border-bottom:1px solid rgba(255,255,255,0.025);" onmouseover="this.style.background='rgba(var(--pf-tint-primary-rgb, 0,200,150),0.03)'" onmouseout="this.style.background='transparent'">
        <td style="padding:7px 10px;color:var(--muted);font-size:11px;">${escapeHtml(t.id)}</td>
        <td style="padding:7px 10px;font-size:12px;font-weight:600;">${escapeHtml(sk)}</td>
        <td style="padding:7px 6px;text-align:center;"><span style="font-size:10px;font-weight:700;color:${dirColor};">${escapeHtml(txn)}</span></td>
        <td style="padding:7px 10px;text-align:right;font-family:'JetBrains Mono',monospace;font-size:11px;">₹${Number(t.entry_price || 0).toFixed(2)}<br><span style="font-size:9px;color:var(--muted);">${escapeHtml(entryDate)} ${escapeHtml(entryTime)}</span></td>
        <td style="padding:7px 10px;text-align:right;font-family:'JetBrains Mono',monospace;font-size:11px;">₹${Number(t.exit_price || 0).toFixed(2)}<br><span style="font-size:9px;color:var(--muted);">${escapeHtml(exitDate)} ${escapeHtml(exitTime)}</span></td>
        <td style="padding:7px 6px;text-align:center;font-size:10px;color:var(--muted);white-space:nowrap;">${escapeHtml(entryTime)} → ${escapeHtml(exitTime)}</td>
        <td style="padding:7px 10px;text-align:center;font-family:'JetBrains Mono',monospace;font-size:11px;">${escapeHtml(qty)}</td>
        <td style="padding:7px 10px;text-align:right;font-family:'JetBrains Mono',monospace;font-weight:700;color:${w?'var(--success)':'var(--danger)'};">${w?'+':''}${fmt(t.pnl)}</td>
        <td style="padding:7px 6px;text-align:center;"><span style="font-size:9px;padding:1px 6px;border-radius:3px;background:${reasonBg};color:${reasonColor};">${escapeHtml(reasonText)}</span></td>
        <td style="padding:7px 10px;text-align:right;font-family:'JetBrains Mono',monospace;font-size:11px;color:${(t.cumulative||0)>=0?'var(--success)':'var(--danger)'};">${fmt(t.cumulative)}</td>
      </tr>`;
    }).join('');
    if (cards) cards.innerHTML = sl.map(_tradeLogCardHtml).join('');
  }
  let pd=document.getElementById('trade-pagination');
  if(!pd){pd=document.createElement('div');pd.id='trade-pagination';pd.style.cssText='display:flex;justify-content:space-between;align-items:center;margin-top:12px;padding-top:10px;border-top:1px solid var(--border);';const cardEl=tbody.closest('.card-glass')||tbody.closest('.card')||tbody.parentElement.parentElement;cardEl.appendChild(pd);}
  let L='<span style="font-size:11px;color:var(--muted);">Showing '+(trades.length?st+1:0)+' to '+Math.min(st+PS,trades.length)+' of '+trades.length+' entries</span>';
  let R='<div style="display:flex;gap:3px;">';
  R+='<button onclick="goTP('+(pg-1)+')" class="btn btn-sm" style="font-size:11px;padding:5px 14px;--btn-bg:rgba(255,255,255,0.06);--btn-color:var(--text);--btn-border:var(--border);" '+(pg<=1?'disabled':'')+'>\u2190 Prev</button>';
  R+='<span style="padding:5px 8px;font-size:11px;color:var(--muted);font-family:\'JetBrains Mono\',monospace;">Page '+pg+' of '+tp+'</span>';
  R+='<button onclick="goTP('+(pg+1)+')" class="btn btn-sm" style="font-size:11px;padding:5px 14px;--btn-bg:rgba(255,255,255,0.06);--btn-color:var(--text);--btn-border:var(--border);" '+(pg>=tp?'disabled':'')+'> Next \u2192</button></div>';
  pd.innerHTML=L+R;
}
function goTP(p){const tp=Math.ceil((_getFilteredTrades()).length/10)||1;window._tradePage=Math.max(1,Math.min(p,tp));renderTradePage();}


function renderDOW(dowData) {
  const container = document.getElementById('dow-analysis');
  container.innerHTML = '';
  const days = ['Monday','Tuesday','Wednesday','Thursday','Friday'];
  days.forEach(day => {
    const d = dowData.find(x => x.day === day) || {hits: 0, miss: 0, profit: 0, loss: 0};
    const total = d.hits + d.miss || 1;
    const hitPct = (d.hits / total * 100).toFixed(0);
    const missPct = (d.miss / total * 100).toFixed(0);
    container.innerHTML += `<div class="analysis-row">
      <span style="width: 75px; font-size: 11px; color: var(--muted);">${day}</span>
      <div class="analysis-bar-wrap" style="flex: 1; display: flex; height: 18px; border-radius: 999px; overflow: hidden;">
        <div style="width: ${hitPct}%; background: linear-gradient(180deg, rgba(34,197,94,1), rgba(21,128,61,1)); display: flex; align-items: center; justify-content: center; font-size: 8px; font-weight: 700; color: #03150a;">Hit ${d.hits}</div>
        <div style="width: ${missPct}%; background: linear-gradient(180deg, rgba(239,68,68,1), rgba(153,27,27,1)); display: flex; align-items: center; justify-content: center; font-size: 8px; font-weight: 700; color: #fff;">Miss ${d.miss}</div>
      </div>
      <span style="font-size: 10px; color: var(--success); font-family: 'JetBrains Mono', monospace; width: 75px; text-align: right;">${fmt(d.profit)}</span>
      <span style="font-size: 10px; color: var(--danger); font-family: 'JetBrains Mono', monospace; width: 80px; text-align: right;">-${fmt(Math.abs(d.loss))}</span>
    </div>`;
  });
}

function renderYearly(yearData) {
  const container = document.getElementById('year-analysis');
  container.innerHTML = '';
  yearData.forEach(y => {
    const total = y.hits + y.miss || 1;
    const hitPct = (y.hits / total * 100).toFixed(0);
    const missPct = (y.miss / total * 100).toFixed(0);
    container.innerHTML += `<div class="analysis-row">
      <span style="width: 40px; font-size: 11px; font-weight: 600;">${y.year}</span>
      <div class="analysis-bar-wrap" style="flex: 1; display: flex; height: 18px; border-radius: 999px; overflow: hidden;">
        <div style="width: ${hitPct}%; background: linear-gradient(180deg, rgba(34,197,94,1), rgba(21,128,61,1)); display: flex; align-items: center; justify-content: center; font-size: 8px; font-weight: 700; color: #03150a;">Hit ${y.hits}</div>
        <div style="width: ${missPct}%; background: linear-gradient(180deg, rgba(239,68,68,1), rgba(153,27,27,1)); display: flex; align-items: center; justify-content: center; font-size: 8px; font-weight: 700; color: #fff;">Miss ${y.miss}</div>
      </div>
      <span style="font-size: 10px; color: var(--success); font-family: 'JetBrains Mono', monospace; width: 75px; text-align: right;">${fmt(y.profit)}</span>
      <span style="font-size: 10px; color: var(--danger); font-family: 'JetBrains Mono', monospace; width: 80px; text-align: right;">-${fmt(Math.abs(y.loss))}</span>
    </div>`;
  });
}

function renderHeatmap(trades) {
  const container = document.getElementById('pnl-heatmap');
  container.innerHTML = '';
  // Group by year-month
  const grid = {};
  let absMax = 1;
  trades.forEach(t => {
    const year = t.entry_time.substring(0, 4);
    const month = parseInt(t.entry_time.substring(5, 7));
    if (!grid[year]) grid[year] = {};
    grid[year][month] = (grid[year][month] || 0) + t.pnl;
  });
  Object.values(grid).forEach(yr => Object.values(yr).forEach(v => { if (Math.abs(v) > absMax) absMax = Math.abs(v); }));
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  // Header
  let html = '<div style="display: grid; grid-template-columns: 44px repeat(12, 1fr); gap: 3px; margin-bottom: 6px;">';
  html += '<div></div>';
  months.forEach(m => { html += `<div style="text-align: center; font-size: 10px; color: var(--muted); font-weight: 600;">${m}</div>`; });
  html += '</div>';
  // Rows
  Object.keys(grid).sort().forEach(year => {
    html += `<div style="display: grid; grid-template-columns: 44px repeat(12, 1fr); gap: 3px; margin-bottom: 3px;">`;
    html += `<div style="font-size: 11px; font-weight: 700; display: flex; align-items: center;">${year}</div>`;
    for (let m = 1; m <= 12; m++) {
      const val = grid[year][m] || 0;
      const intensity = Math.min(0.8, (Math.abs(val) / absMax) * 0.7 + 0.1);
      const bg = val > 0 ? `rgba(34,197,94,${intensity})` : (val < 0 ? `rgba(239,68,68,${intensity})` : 'rgba(255,255,255,0.03)');
      const textColor = val !== 0 ? (intensity > 0.4 ? '#fff' : (val > 0 ? 'rgb(74,222,128)' : 'rgb(248,113,113)')) : 'var(--muted)';
      const label = val !== 0 ? (val > 0 ? '+' : '') + (Math.abs(val) > 999 ? Math.round(val/1000) + 'k' : Math.round(val)) : '';
      html += `<div style="background:${bg};border-radius:4px;height:34px;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;font-family:'JetBrains Mono',monospace;color:${textColor};transition:transform 0.15s;" title="${year}-${String(m).padStart(2,'0')}: ${fmt(val)}" onmouseenter="this.style.transform='scale(1.08)'" onmouseleave="this.style.transform='scale(1)'">${label}</div>`;
    }
    html += '</div>';
  });
  container.innerHTML = html;
}

function downloadCSV() {
  // If viewing a saved run, use server-side CSV endpoint (includes all fields)
  if (currentViewingRunId) {
    window.open(`/api/runs/${currentViewingRunId}/csv`, '_blank');
    toast('CSV Downloaded', 'success');
    return;
  }
  // Fallback: client-side CSV for fresh backtest results
  if (!lastBacktestData || !lastBacktestData.trades) { toast('No data to download', 'warn'); return; }
  let csv = '#,Entry Time,Exit Time,Entry Price,Exit Price,P&L,Reason,Cumulative,Option Type,Strike,Qty,Txn Type\n';
  lastBacktestData.trades.forEach(t => {
    csv += `${t.id},${t.entry_time},${t.exit_time},${t.entry_price},${t.exit_price},${t.pnl},${t.exit_reason},${t.cumulative},${t.option_type||''},${t.strike||''},${t.qty||''},${t.txn_type||''}\n`;
  });
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  const name = (lastBacktestPayload && lastBacktestPayload.run_name) || 'backtest_trades';
  a.href = url; a.download = name.replace(/\s+/g, '_') + '_trades.csv'; a.click();
  URL.revokeObjectURL(url);
  toast('CSV Downloaded', 'success');
}

function renderEquityChart(equityData) {
  const canvas = document.getElementById('equity-chart');
  if (!canvas || !equityData || equityData.length < 2) return;
  const ctx = canvas.getContext('2d');

  // Wait a frame for DOM to be visible, then render
  requestAnimationFrame(() => {
    const parentW = canvas.parentElement.clientWidth - 40;
    const w = Math.max(parentW, 400);
    const h = 300;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const padding = { top: 20, right: 20, bottom: 30, left: 70 };
  const plotW = w - padding.left - padding.right;
  const plotH = h - padding.top - padding.bottom;

  const values = equityData.map(e => e.equity);
  const minVal = Math.min(...values) * 0.998;
  const maxVal = Math.max(...values) * 1.002;
  const range = maxVal - minVal || 1;
  const startVal = values[0];

  // Theme-aware colors
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  const gridCol = isLight ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.04)';
  const frameCol = isLight ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.08)';
  const labelCol = isLight ? 'rgba(15,23,42,0.62)' : 'rgba(255,255,255,0.3)';
  const dashCol = isLight ? 'rgba(15,23,42,0.16)' : 'rgba(255,255,255,0.1)';

  // Background
  if (!isLight) {
    const bgGrad = ctx.createLinearGradient(0, 0, 0, h);
    bgGrad.addColorStop(0, 'rgba(16,24,38,0.75)');
    bgGrad.addColorStop(1, 'rgba(8,12,20,0.8)');
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, w, h);
  } else {
    ctx.fillStyle = '#f8fafc';
    ctx.fillRect(0, 0, w, h);
  }

  // Rounded frame
  ctx.strokeStyle = frameCol;
  ctx.lineWidth = 1;
  ctx.strokeRect(0.5, 0.5, w - 1, h - 1);

  // Grid lines
  ctx.strokeStyle = gridCol;
  ctx.lineWidth = 0.5;
  for (let i = 0; i <= 5; i++) {
    const y = padding.top + (plotH / 5) * i;
    ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(w - padding.right, y); ctx.stroke();
    const val = maxVal - (range / 5) * i;
    ctx.fillStyle = labelCol;
    ctx.font = '10px JetBrains Mono';
    ctx.textAlign = 'right';
    ctx.fillText('₹' + Math.round(val).toLocaleString('en-IN'), padding.left - 8, y + 3);
  }

  // Starting capital line
  const startY = padding.top + ((maxVal - startVal) / range) * plotH;
  ctx.strokeStyle = dashCol;
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(padding.left, startY); ctx.lineTo(w - padding.right, startY); ctx.stroke();
  ctx.setLineDash([]);

  // Equity line with glow — Catmull-Rom spline for true smooth curves
  // Densify: insert midpoints between each pair for visibly smoother rendering
  const rawPoints = values.map((v, i) => ({
    x: padding.left + (i / (values.length - 1)) * plotW,
    y: padding.top + ((maxVal - v) / range) * plotH
  }));
  const points = [];
  for (let i = 0; i < rawPoints.length; i++) {
    points.push(rawPoints[i]);
    if (i < rawPoints.length - 1) {
      points.push({ x: (rawPoints[i].x + rawPoints[i+1].x) / 2, y: (rawPoints[i].y + rawPoints[i+1].y) / 2 });
    }
  }

  ctx.beginPath();
  ctx.strokeStyle = 'rgba(var(--pf-tint-primary-rgb, 0,200,150),1)';
  ctx.lineWidth = 2.2;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.shadowColor = 'rgba(var(--pf-tint-primary-rgb, 0,200,150),0.35)';
  ctx.shadowBlur = 12;
  ctx.moveTo(points[0].x, points[0].y);

  // Catmull-Rom → cubic Bezier conversion (tension 0.5 = pronounced smoothing)
  const tension = 0.5;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(0, i - 1)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(points.length - 1, i + 2)];
    const cp1x = p1.x + (p2.x - p0.x) * tension;
    const cp1y = p1.y + (p2.y - p0.y) * tension;
    const cp2x = p2.x - (p3.x - p1.x) * tension;
    const cp2y = p2.y - (p3.y - p1.y) * tension;
    ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2.x, p2.y);
  }
  ctx.stroke();
  ctx.shadowBlur = 0;

  // Fill gradient
  const lastX = points[points.length - 1].x;
  const lastY = points[points.length - 1].y;
  ctx.lineTo(lastX, padding.top + plotH);
  ctx.lineTo(padding.left, padding.top + plotH);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, padding.top, 0, padding.top + plotH);
  grad.addColorStop(0, 'rgba(var(--pf-tint-primary-rgb, 0,200,150),0.12)');
  grad.addColorStop(1, 'rgba(var(--pf-tint-primary-rgb, 0,200,150),0.01)');
  ctx.fillStyle = grad;
  ctx.fill();

  // End point marker
  const endX = padding.left + plotW;
  const endY = padding.top + ((maxVal - values[values.length - 1]) / range) * plotH;
  ctx.beginPath();
  ctx.arc(endX, endY, 3.5, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(var(--pf-tint-primary-rgb, 0,200,150),1)';
  ctx.fill();
  ctx.beginPath();
  ctx.arc(endX, endY, 7, 0, Math.PI * 2);
  ctx.strokeStyle = 'rgba(var(--pf-tint-primary-rgb, 0,200,150),0.25)';
  ctx.lineWidth = 2;
  ctx.stroke();
  }); // end requestAnimationFrame
}

// ══════════════════════════════════════════════════════════════
//  FOLDER HELPER
// ══════════════════════════════════════════════════════════════
function onFolderChange() {
  const sel = document.getElementById('folder-select');
  const custom = document.getElementById('folder-custom');
  if (sel.value === '__custom__') { custom.style.display = 'block'; custom.focus(); }
  else { custom.style.display = 'none'; custom.value = ''; }
}

function _refreshFolderDropdown() {
  const sel = document.getElementById('folder-select');
  if (!sel) return;
  const currentVal = sel.value;
  const base = ['Scalping', 'Intraday', 'Swing', 'Positional', 'Experimental', 'Hedging'];
  const extra = new Set();
  (savedStrategiesCache || []).forEach(s => { if (s.folder && !base.includes(s.folder)) extra.add(s.folder); });
  const all = [...base, ...[...extra].sort()];
  sel.innerHTML = all.map(f => `<option value="${escapeAttr(f)}">${escapeHtml(f)}</option>`).join('') + '<option value="__custom__">+ Custom...</option>';
  // Restore previous selection if still valid
  if (all.includes(currentVal)) sel.value = currentVal;
  else sel.value = 'Intraday';
}

// ══════════════════════════════════════════════════════════════
//  REAL PAPER TRADING ENGINE (Live Market Data)
// ══════════════════════════════════════════════════════════════
let paperStatusInterval = null;

async function startPaperTrading() {
  const payload = buildPayload();
  if (!payload.instrument) { toast('Select an instrument first', 'warn'); return; }
  if (!ensureSingleStrategyTimeframe(payload)) return;
  if (!payload.entry_conditions || payload.entry_conditions.length === 0) {
    toast('Add at least one entry condition', 'warn');
    return;
  }

  // UI updates (elements may not exist if panel was replaced by preview)
  const _spb = document.getElementById('start-paper-btn');
  const _stb = document.getElementById('stop-paper-btn');
  const _ps = document.getElementById('paper-stats');
  const _ll = document.getElementById('live-log');
  if (_spb) _spb.style.display = 'none';
  if (_stb) _stb.style.display = 'block';
  if (_ps) _ps.style.display = 'block';
  if (_ll) _ll.innerHTML = '';

  logPaper('Starting Paper Trading Engine with LIVE market data...', 'var(--accent)');

  try {
    const res = await fetch('/api/paper/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();

    if (data.status === 'started') {
      logPaper('Paper Trading Engine Started!', 'var(--success)');
      logPaper(`Strategy: ${payload.run_name || 'Unnamed'}`, 'var(--muted)');
      logPaper(`Instrument: ${getInstrumentName(payload.instrument)}`, 'var(--muted)');
      logPaper(`Monitoring for entry signals...`, 'var(--warn)');

      // Start polling for status updates
      paperStatusInterval = setInterval(updatePaperStatus, 2000);

      // Auto-navigate to Live Monitor tab
      setTimeout(() => {
        showPage('live-page', document.getElementById('nav-live'));
        startLiveMonitor();
      }, 800);
    } else {
      logPaper(`${data.message || 'Failed to start'}`, 'var(--danger)');
    }
  } catch (err) {
    logPaper(`Error: ${err.message}`, 'var(--danger)');
  }
}

async function stopPaperTrading() {
  clearInterval(paperStatusInterval);
  paperStatusInterval = null;

  try {
    const res = await fetch('/api/paper/stop', { method: 'POST' });
    const data = await res.json();

    logPaper('Paper Trading Engine Stopped', 'var(--danger)');

    // Get final status
    setTimeout(updatePaperStatus, 500);

  } catch (err) {
    logPaper(`Error stopping: ${err.message}`, 'var(--danger)');
  }

  const _stb2 = document.getElementById('stop-paper-btn');
  const _spb2 = document.getElementById('start-paper-btn');
  if (_stb2) _stb2.style.display = 'none';
  if (_spb2) _spb2.style.display = 'block';
}

async function updatePaperStatus() {
  try {
    const res = await fetch('/api/paper/status');
    const status = await res.json();

    // Update UI elements
    const statusBadge = status.in_trade ? 'IN TRADE' : (status.running ? 'SCANNING' : 'STOPPED');
    updatePaperStatusBadge(statusBadge);

    const _ppnl = document.getElementById('paper-pnl');
    if (_ppnl) {
      _ppnl.textContent = `₹${status.total_pnl.toFixed(2)}`;
      _ppnl.style.color = status.total_pnl >= 0 ? 'var(--success)' : 'var(--danger)';
    }
    const _ptrades = document.getElementById('paper-trades');
    if (_ptrades) _ptrades.textContent = status.trades_today;

    // Update log with new events
    if (status.event_log && status.event_log.length > 0) {
      const logBox = document.getElementById('live-log');
      if (logBox) {
        const currentHtml = logBox.innerHTML;
        const lastEvent = status.event_log[status.event_log.length - 1];

        // Only add if it's a new event (simple check)
        if (!currentHtml.includes(lastEvent.message.substring(0, 20))) {
          const color = {
            'entry': 'var(--success)',
            'exit': 'var(--warn)',
            'error': 'var(--danger)',
            'signal': 'var(--accent)',
            'info': 'var(--muted)'
          }[lastEvent.type] || 'var(--text)';

          logPaper(lastEvent.message, color);
        }
      }
    }

    // Render positions
    renderPaperPositions(status.positions || [], status.closed_trades || []);

  } catch (err) {
    console.error('Failed to fetch paper status:', err);
  }
}

function updatePaperStatusBadge(text) {
  const badge = document.getElementById('paper-status');
  if (!badge) return;
  badge.textContent = text;

  const colors = {
    'SCANNING': 'var(--warn)',
    'IN TRADE': 'var(--success)',
    'STOPPED': 'var(--muted)'
  };
  badge.style.color = colors[text] || 'var(--text)';
}

function renderPaperPositions(positions, closedTrades) {
  const panel = document.getElementById('paper-legs-panel');
  if (!panel) return;

  let html = '';

  // Open positions
  if (positions.length > 0) {
    html += '<div style=\"margin-bottom: 10px; font-weight: 600; font-size: 11px; color: var(--accent);\">' + ICO.chart(13) + ' Open Positions</div>';
    positions.forEach(pos => {
      const pnlColor = pos.unrealized_pnl >= 0 ? 'var(--success)' : 'var(--danger)';
      html += `<div style=\"background: rgba(0,150,200,0.08); padding: 8px; border-radius: 6px; margin-bottom: 6px; border-left: 3px solid var(--accent2);\">
        <div style=\"display: flex; justify-content: space-between; margin-bottom: 4px;\">
          <span style=\"font-weight: 600; font-size: 12px;\">${pos.transaction_type} ${pos.option_type} @ ${pos.strike}</span>
          <span style=\"font-size: 11px; color: ${pnlColor}; font-weight: 700;\">${pos.unrealized_pnl >= 0 ? '+' : ''}₹${pos.unrealized_pnl.toFixed(2)}</span>
        </div>
        <div style=\"font-size: 10px; color: var(--muted);\">
          Entry: ₹${pos.entry_premium.toFixed(2)} → Current: ₹${pos.current_premium.toFixed(2)} | ${pos.lots} lot(s)
        </div>
      </div>`;
    });
  }

  // Recent closed trades
  if (closedTrades.length > 0) {
    const recent = closedTrades.slice(-3);
    html += '<div style=\"margin: 12px 0 6px; font-weight: 600; font-size: 11px; color: var(--muted);\">' + ICO.clip(13) + ' Recent Trades</div>';
    recent.forEach(trade => {
      const pnlColor = trade.pnl >= 0 ? 'var(--success)' : 'var(--danger)';
      html += `<div style=\"background: rgba(0,0,0,0.2); padding: 6px; border-radius: 4px; margin-bottom: 4px; font-size: 10px;\">
        <div style=\"display: flex; justify-content: space-between;\">
          <span>${trade.transaction_type} ${trade.option_type} @ ${trade.strike}</span>
          <span style=\"color: ${pnlColor}; font-weight: 600;\">${trade.pnl >= 0 ? '+' : ''}₹${trade.pnl.toFixed(2)}</span>
        </div>
        <div style=\"color: var(--muted); font-size: 9px;\">${trade.exit_reason}</div>
      </div>`;
    });
  }

  panel.innerHTML = html || '<div style=\"text-align: center; color: var(--muted); font-size: 11px; padding: 10px;\">No positions yet</div>';
}

function logPaper(msg, color) {
  const box = document.getElementById('live-log');
  if (!box) return; // Panel removed — logging handled by live monitor
  const time = new Date().toLocaleTimeString('en-IN', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
  // Use DOM creation — innerHTML += re-parses the entire container on each tick
  // and allows server-provided msg to inject HTML.
  const line = document.createElement('div');
  line.style.color = color || 'var(--text)';   // style.color: CSS property, not HTML
  line.textContent = '[' + time + '] ' + msg;  // textContent: server event strings can't XSS
  box.appendChild(line);
  // Cap log at 500 lines to prevent memory bloat during long sessions
  while (box.children.length > 500) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

// getInstrumentName is defined once earlier in the file — removed duplicate

// ══════════════════════════════════════════════════════════════
//  DEPLOY STRATEGY IN LIVE
// ══════════════════════════════════════════════════════════════
function openDeployModal() {
  let runName = document.getElementById('run-name-input').value;
  if (!runName && lastBacktestPayload) runName = lastBacktestPayload.run_name;
  if (!runName && lastBacktestData) runName = lastBacktestData.run_name;
  if (!runName) runName = 'Strategy_' + generateRandomID();
  document.getElementById('deploy-run-name').value = runName;
  document.getElementById('deploy-modal').classList.add('open');

  // Run validation when modal opens
  const valBox = document.getElementById('deploy-validation-box');
  valBox.style.display = 'block';
  valBox.style.background = 'rgba(59,130,246,0.08)';
  valBox.style.border = '1px solid rgba(59,130,246,0.2)';
  valBox.innerHTML = '<span style="color:var(--accent2);">Validating strategy...</span>';

  const payload = buildPayload();
  fetch('/api/validate-strategy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).then(r => r.json()).then(data => {
    let html = '';
    if (data.errors && data.errors.length > 0) {
      valBox.style.background = 'rgba(239,68,68,0.08)';
      valBox.style.border = '1px solid rgba(239,68,68,0.3)';
      html += data.errors.map(e => `<div style="color:var(--danger);">${ICO.cross(13)} ${e}</div>`).join('');
    }
    if (data.warnings && data.warnings.length > 0) {
      if (!html) { valBox.style.background = 'rgba(245,158,11,0.08)'; valBox.style.border = '1px solid rgba(245,158,11,0.3)'; }
      html += data.warnings.map(w => `<div style="color:var(--warn);">${ICO.warn(13)} ${w}</div>`).join('');
    }
    if (!html) {
      valBox.style.background = 'rgba(34,197,94,0.08)';
      valBox.style.border = '1px solid rgba(34,197,94,0.3)';
      html = '<span style="color:var(--success);">' + ICO.check(13) + ' Strategy looks good — ready to deploy!</span>';
    }
    valBox.innerHTML = html;
  }).catch(() => {
    valBox.style.display = 'none';
  });
}

function closeDeployModal() {
  document.getElementById('deploy-modal').classList.remove('open');
}

function setDeployType(type) {
  const paperBtn = document.getElementById('deploy-paper-btn');
  const autoBtn = document.getElementById('deploy-auto-btn');

  if (type === 'paper') {
    paperBtn.style.background = 'linear-gradient(135deg, rgba(245,158,11,0.25), rgba(245,158,11,0.1))';
    paperBtn.style.color = 'var(--warn)';
    paperBtn.style.boxShadow = 'inset 0 0 20px rgba(245,158,11,0.08)';
    autoBtn.style.background = 'transparent';
    autoBtn.style.color = 'var(--muted)';
    autoBtn.style.boxShadow = 'none';
  } else {
    autoBtn.style.background = 'linear-gradient(135deg, rgba(59,130,246,0.3), rgba(59,130,246,0.1))';
    autoBtn.style.color = 'var(--accent2)';
    autoBtn.style.boxShadow = 'inset 0 0 20px rgba(59,130,246,0.1)';
    paperBtn.style.background = 'transparent';
    paperBtn.style.color = 'var(--muted)';
    paperBtn.style.boxShadow = 'none';
  }
  paperBtn.dataset.active = type === 'paper' ? '1' : '0';
  autoBtn.dataset.active = type === 'auto' ? '1' : '0';
}

async function deployStrategy() {
  const isPaper = document.getElementById('deploy-paper-btn').dataset.active === '1';
  const deployRunName = document.getElementById('deploy-run-name').value.trim();
  if (!deployRunName) {
    toast('Enter a run name in deploy modal', 'warn');
    return;
  }
  const deployConfig = {
    order_type: isPaper ? 'paper' : 'auto',
    product_type: document.querySelector('input[name="deploy-product"]:checked').value,
    entry_order: document.getElementById('deploy-entry-order').value,
    exit_order: document.getElementById('deploy-exit-order').value,
    sl_limit_diff_pct: parseFloat(document.getElementById('deploy-sl-limit-diff').value) || 0,
    margin_benefit: document.getElementById('deploy-margin-benefit').value,
    sl_tp_based_on: document.querySelector('input[name="deploy-sl-price"]:checked').value,
    place_leg_sl: document.querySelector('input[name="deploy-leg-sl"]:checked').value,
    sqoff_on_fail: document.querySelector('input[name="deploy-sqoff-fail"]:checked').value,
  };

  let payload = buildPayload();
  payload.run_name = deployRunName;
  payload.deploy_config = deployConfig;

  // If deployed from Results page and builder form is empty, use last backtest data
  if (!payload.instrument && lastBacktestPayload) {
    payload = Object.assign(payload, {
      instrument: lastBacktestPayload.instrument,
      segment: lastBacktestPayload.segment,
      indicators: lastBacktestPayload.indicators || [],
      entry_conditions: lastBacktestPayload.entry_conditions || [],
      exit_conditions: lastBacktestPayload.exit_conditions || [],
      legs: lastBacktestPayload.legs || [],
      lots: lastBacktestPayload.lots || 1,
      stoploss_pct: lastBacktestPayload.stoploss_pct || 0,
      stoploss_rupees: lastBacktestPayload.stoploss_rupees || 0,
      sl_type: lastBacktestPayload.sl_type || 'rupees',
      target_profit_pct: lastBacktestPayload.target_profit_pct || 0,
      target_profit_rupees: lastBacktestPayload.target_profit_rupees || 0,
      tp_type: lastBacktestPayload.tp_type || 'rupees',
      market_open: lastBacktestPayload.market_open || '09:15',
      market_close: lastBacktestPayload.market_close || '15:25',
      max_trades_per_day: lastBacktestPayload.max_trades_per_day || 1,
      max_daily_loss: lastBacktestPayload.max_daily_loss || 0,
    });
  }

  console.log("Deploy config:", deployConfig);
  console.log("Full deploy payload:", payload);

  if (!payload.instrument) {
    toast('No instrument selected. Open a strategy or run a backtest first.', 'warn');
    closeDeployModal();
    return;
  }
  if (!ensureSingleStrategyTimeframe(payload)) {
    closeDeployModal();
    return;
  }

  try {
    if (isPaper) {
      const res = await fetch('/api/paper/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (res.ok && (data.status === 'started' || data.status === 'already_running')) {
        toast('Strategy deployed in Paper Testing mode', 'success');
        _activeRunMode = 'paper';
      } else {
        console.error('[Deploy] Paper start error:', data);
        toast(data.error?.detail || data.error?.message || data.message || data.detail || 'Paper deploy failed', 'danger');
        closeDeployModal();
        return;
      }
    } else {
      // Auto trading — confirm with user (REAL MONEY!)
      const confirmed = await customConfirm('This will place <strong style="color:var(--danger)">REAL orders</strong> with <strong>real money</strong> on your Dhan account.<br><br>Are you sure you want to proceed?', { title: 'AUTO TRADING', icon: ICO.siren(28), okText: 'Start Auto Trading', danger: true });
      if (!confirmed) {
        return;
      }
      const res = await fetch('/api/live/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          run_name: deployRunName,
          instrument: payload.instrument,
          indicators: payload.indicators,
          entry_conditions: payload.entry_conditions,
          exit_conditions: payload.exit_conditions,
          legs: payload.legs,
          deploy_config: deployConfig,
          max_trades_per_day: payload.max_trades_per_day,
          market_open: payload.market_open,
          market_close: payload.market_close,
          max_daily_loss: payload.max_daily_loss || 0,
          lots: payload.lots,
          stoploss_pct: payload.stoploss_pct,
          strategy_config: payload,
        })
      });
      const data = await res.json();
      if (res.ok && (data.status === 'started' || data.status === 'already_running')) {
        toast('Strategy deployed for Auto Trading (REAL ORDERS)', 'success');
        _activeRunMode = 'auto';
      } else {
        console.error('[Deploy] Auto start error:', data);
        toast(data.error?.detail || data.error?.message || data.message || data.detail || 'Auto deploy failed', 'danger');
        closeDeployModal();
        return;
      }
    }
    closeDeployModal();
    // Switch to Live Monitor tab
    showPage('live-page', document.getElementById('nav-live'));
    startLiveMonitor();
  } catch (err) {
    toast('Deploy failed: ' + err.message, 'danger');
  }
}

// ══════════════════════════════════════════════════════════════
//  BUILDER LIVE STATUS PREVIEW (polls paper + auto status)
// ══════════════════════════════════════════════════════════════
let _builderPreviewInterval = null;

async function refreshBuilderPreview() {
  try {
    const [paperRes, liveRes] = await Promise.allSettled([
      fetch('/api/paper/status').then(r => r.json()),
      fetch('/api/live/status').then(r => r.json())
    ]);
    const paper = paperRes.status === 'fulfilled' ? paperRes.value : null;
    const live  = liveRes.status  === 'fulfilled' ? liveRes.value  : null;

    // Paper card
    if (paper) {
      const badge = document.getElementById('bp-paper-badge');
      if (paper.running && paper.in_trade) {
        badge.textContent = 'IN TRADE'; badge.style.background = 'rgba(34,197,94,0.15)'; badge.style.color = '#4ade80';
      } else if (paper.running) {
        badge.textContent = 'WAITING'; badge.style.background = 'rgba(245,158,11,0.15)'; badge.style.color = '#f59e0b';
      } else {
        badge.textContent = 'STOPPED'; badge.style.background = 'rgba(239,68,68,0.15)'; badge.style.color = 'var(--red)';
      }
      const pName = document.getElementById('bp-paper-name');
      pName.textContent = paper.strategy_name || '—';
      pName.title = paper.strategy_name || '';
      const pPnl = document.getElementById('bp-paper-pnl');
      const pnlVal = paper.total_pnl || 0;
      pPnl.textContent = '₹' + pnlVal.toFixed(2);
      pPnl.style.color = pnlVal > 0 ? 'var(--success)' : pnlVal < 0 ? 'var(--danger)' : 'var(--muted)';
      document.getElementById('bp-paper-trades').textContent = paper.trades_today || 0;
    }

    // Auto card
    if (live) {
      const badge = document.getElementById('bp-auto-badge');
      if (live.running && live.in_trade) {
        badge.textContent = 'IN TRADE'; badge.style.background = 'rgba(34,197,94,0.15)'; badge.style.color = '#4ade80';
      } else if (live.running) {
        badge.textContent = 'WAITING'; badge.style.background = 'rgba(245,158,11,0.15)'; badge.style.color = '#f59e0b';
      } else {
        badge.textContent = 'STOPPED'; badge.style.background = 'rgba(239,68,68,0.15)'; badge.style.color = 'var(--red)';
      }
      const aName = document.getElementById('bp-auto-name');
      aName.textContent = live.strategy_name || '—';
      aName.title = live.strategy_name || '';
      const aPnl = document.getElementById('bp-auto-pnl');
      const aPnlVal = live.total_pnl || 0;
      aPnl.textContent = '₹' + aPnlVal.toFixed(2);
      aPnl.style.color = aPnlVal > 0 ? 'var(--success)' : aPnlVal < 0 ? 'var(--danger)' : 'var(--muted)';
      document.getElementById('bp-auto-trades').textContent = live.trades_today || 0;
    }
  } catch(e) { console.warn('[BuilderPreview] Poll error:', e); }
}

function startBuilderPreview() {
  refreshBuilderPreview();
  if (_builderPreviewInterval) clearInterval(_builderPreviewInterval);
  _builderPreviewInterval = setInterval(() => {
    if (!_isPageVisible() || !_isPageActive('builder-page')) return;
    refreshBuilderPreview();
  }, 5000);
}
function stopBuilderPreview() {
  if (_builderPreviewInterval) { clearInterval(_builderPreviewInterval); _builderPreviewInterval = null; }
}

// ══════════════════════════════════════════════════════════════
//  LIVE MONITOR — Multi-Strategy Tabbed View
// ══════════════════════════════════════════════════════════════
let liveMonitorInterval = null;
let _activeRunMode = 'paper'; // 'paper' or 'auto' (for backward compat)
let _liveEngines = [];        // cached engine list from /api/engines/all
let _selectedLiveTab = 0;     // index of selected tab
const _LIVE_TRADES_PER_PAGE = 10;
let _liveClosedPages = {};    // mode:run_id → current page number

function startLiveMonitor() {
  loadLiveMonitor();
  if (liveMonitorInterval) clearInterval(liveMonitorInterval);
  liveMonitorInterval = setInterval(() => {
    if (!_isPageVisible() || !_isPageActive('live-page')) return;
    loadLiveMonitor();
  }, 5000);
}

function stopLiveMonitor() {
  if (liveMonitorInterval) { clearInterval(liveMonitorInterval); liveMonitorInterval = null; }
}

function downloadPaperCSV() {
  // Get currently selected engine
  const eng = _liveEngines[_selectedLiveTab];
  if (!eng) return;
  const rid = eng.run_id || '';
  if (eng.mode === 'auto') {
    window.open('/api/live/trades/csv?run_id=' + encodeURIComponent(rid), '_blank');
  } else {
    window.open('/api/paper/trades/csv?run_id=' + encodeURIComponent(rid), '_blank');
  }
}

async function stopEngine(runId, mode) {
  const label = mode === 'auto' ? 'auto trading' : 'paper trade';
  const ok = await customConfirm(`Stop <strong>${label}</strong> run "<strong>${runId}</strong>"?<br><br>Any open broker positions will be squared off first. If the broker exit is not confirmed, the engine will stay running.`, { title: 'Stop Trading', icon: ICO.sqstop(28), okText: 'Stop', danger: true });
  if (!ok) return;
  try {
    const endpoint = mode === 'auto' ? '/api/live/stop' : '/api/paper/stop';
    const res = await fetch(endpoint, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ run_id: runId }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status === 'error' || data.status === 'pending') {
      throw new Error(data.detail || data.message || 'Broker square-off is still pending');
    }
    if (data.status !== 'stopped') {
      throw new Error(data.detail || data.message || 'Failed to stop engine');
    }
    toast(data.message || `${label} "${runId}" stopped`, 'warn');
    // Sync strategy builder buttons
    const startBtn = document.getElementById('start-paper-btn');
    const stopBtn = document.getElementById('stop-paper-btn');
    if (mode === 'paper' && startBtn) startBtn.style.display = 'block';
    if (mode === 'paper' && stopBtn) stopBtn.style.display = 'none';
    setTimeout(() => loadLiveMonitor(), 500);
  } catch(e) {
    toast(e.message || 'Error stopping trade', 'danger');
    loadLiveMonitor();
  }
}

function viewRunningStrategy(runId, mode) {
  // Find the engine data from the cache
  const eng = _findLiveEngine(runId, mode) || _liveEngines[_selectedLiveTab];
  if (!eng) { toast('Engine data not found', 'danger'); return; }
  const strat = eng.strategy || {};
  if (!strat.run_name && !strat.indicators) {
    toast('Strategy details not available yet — try again in a moment', 'warn');
    return;
  }
  viewingStrategyId = null;
  showDetailsModal(strat, (strat.run_name || 'Running Strategy') + ' (Live)');
}

// Backward compat — old stop button handler
async function stopPaperTrade() {
  const eng = _liveEngines[_selectedLiveTab];
  if (eng) {
    await stopEngine(eng.run_id || '', eng.mode || _activeRunMode);
  }
}

async function dismissEngine(runId, mode = '') {
  try {
    await fetch('/api/engines/dismiss', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ run_id: runId, mode }) });
    _liveEngines = _liveEngines.filter((e) => {
      const sameRunId = String(e.run_id || '') === String(runId || '');
      if (!sameRunId) return true;
      return mode ? String(e.mode || '') !== String(mode || '') : false;
    });
    if (_selectedLiveTab >= _liveEngines.length) _selectedLiveTab = Math.max(0, _liveEngines.length - 1);
    renderLiveTabs();
    if (_liveEngines.length > 0) {
      renderLivePanel(_liveEngines[_selectedLiveTab], _selectedLiveTab);
    } else {
      document.getElementById('live-panels-container').innerHTML =
        '<div style="text-align:center;padding:60px 28px;color:var(--muted);">' +
        '<div style="margin-bottom:12px;">' + ICO.antenna(40) + '</div>' +
        '<div style="font-size:16px;font-weight:600;margin-bottom:6px;">No Active Strategies</div>' +
        '<div style="font-size:13px;">Deploy a strategy with Paper Trade or Auto Trade to see live monitoring here.</div></div>';
    }
  } catch(e) { toast('Error dismissing panel', 'danger'); }
}

async function restartEngine(runId, mode) {
  const eng = _findLiveEngine(runId, mode);
  if (!eng) { toast('Engine data not found', 'danger'); return; }
  const strat = eng.strategy || {};
  if (!strat.run_name && !strat.entry_conditions) {
    toast('Strategy config not available — please deploy from the Builder tab', 'warn');
    return;
  }
  const label = mode === 'auto' ? 'Auto Trade' : 'Paper Trade';
  const ok = await customConfirm(`Restart <strong>${label}</strong> "<strong>${runId}</strong>" with the same strategy config?`, { title: 'Restart Strategy', icon: ICO.play(28), okText: 'Start', danger: false });
  if (!ok) return;
  try {
    const endpoint = mode === 'auto' ? '/api/live/start' : '/api/paper/start';
    const payload = {
      ...strat,
      entry_conditions: strat.entry_conditions || eng._entry_conditions || [],
      exit_conditions: strat.exit_conditions || eng._exit_conditions || [],
    };
    if (mode === 'auto') payload.strategy_config = strat;
    const res = await fetch(endpoint, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    const result = await res.json();
    if (result.status === 'started') {
      toast(`${label} "${runId}" restarted`, 'success');
      setTimeout(() => loadLiveMonitor(), 500);
    } else {
      toast(result.message || 'Failed to restart', 'danger');
    }
  } catch(e) { toast('Error restarting engine: ' + e.message, 'danger'); }
}

const INST_NAMES = { '26000': 'NIFTY 50', '26009': 'BANK NIFTY', '26037': 'FINNIFTY', '26041': 'MIDCPNIFTY' };

async function loadLiveMonitor() {
  try {
    const res = await fetch('/api/engines/all');
    const data = await res.json();
    _liveEngines = data.engines || [];

    // Update live dot in nav
    updateLiveTabDot(_liveEngines);

    // Clamp selected tab
    if (_selectedLiveTab >= _liveEngines.length) _selectedLiveTab = Math.max(0, _liveEngines.length - 1);

    renderLiveTabs();
    if (_liveEngines.length > 0) {
      renderLivePanel(_liveEngines[_selectedLiveTab], _selectedLiveTab);
    } else {
      document.getElementById('live-panels-container').innerHTML =
        '<div style="text-align:center;padding:60px 28px;color:var(--muted);">' +
        '<div style="margin-bottom:12px;">' + ICO.antenna(40) + '</div>' +
        '<div style="font-size:16px;font-weight:600;margin-bottom:6px;">No Active Strategies</div>' +
        '<div style="font-size:13px;">Deploy a strategy with Paper Trade or Auto Trade to see live monitoring here.</div></div>';
    }
  } catch(e) { console.error('Live monitor fetch error:', e); }
}

function selectLiveTab(idx) {
  _selectedLiveTab = idx;
  renderLiveTabs();
  if (_liveEngines[idx]) renderLivePanel(_liveEngines[idx], idx);
}

function renderLiveTabs() {
  const bar = document.getElementById('live-tabs-bar');
  if (!_liveEngines.length) {
    bar.innerHTML = '<div style="padding:12px 0;color:var(--muted);font-size:13px;">No active strategies</div>';
    return;
  }

  // Combined summary at left
  const totalPnl = round2(_liveEngines.reduce((s, e) => s + (e.total_pnl || 0), 0));
  const totalRunning = _liveEngines.filter(e => e.running).length;
  const pnlColor = totalPnl >= 0 ? 'var(--success)' : 'var(--danger)';

  let html = `<div class="live-tabs-summary" style="padding:10px 16px 10px 0;border-right:1px solid var(--border);margin-right:4px;display:flex;flex-direction:column;gap:2px;min-width:120px;">
    <div style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;">Combined P&L</div>
    <div style="font-size:16px;font-weight:700;font-family:'JetBrains Mono';color:${pnlColor};">₹${totalPnl.toFixed(2)}</div>
    <div style="font-size:10px;color:var(--muted);">${totalRunning} running</div>
  </div>`;

  // Individual strategy tabs
  _liveEngines.forEach((eng, idx) => {
    const active = idx === _selectedLiveTab;
    const name = eng.strategy_name || eng.run_id || 'Strategy';
    const safeName = escapeHtml(name);
    const mode = eng.mode === 'auto' ? ICO.bot(13) : ICO.memo(13);
    const pnl = round2(eng.total_pnl || 0);
    const running = eng.running;
    const inTrade = eng.in_trade;

    let statusDot = '';
    if (running && inTrade) statusDot = '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#4ade80;margin-right:4px;animation:pulse 2s infinite;"></span>';
    else if (running) statusDot = '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#f59e0b;margin-right:4px;animation:pulse 2s infinite;"></span>';
    else statusDot = '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--red);margin-right:4px;"></span>';

    const borderStyle = active ? 'border-bottom:2px solid var(--accent);' : 'border-bottom:2px solid transparent;';
    const bgStyle = active ? 'background:rgba(99,102,241,0.08);' : '';

    html += `<div class="live-tab-item" onclick="selectLiveTab(${idx})" style="cursor:pointer;padding:8px 14px;${borderStyle}${bgStyle}transition:all 0.15s;display:flex;flex-direction:column;gap:2px;min-width:130px;" onmouseenter="this.style.background='rgba(99,102,241,0.05)'" onmouseleave="this.style.background='${active ? 'rgba(99,102,241,0.08)' : ''}'">
      <div style="display:flex;align-items:center;gap:4px;">
        ${statusDot}<span style="font-size:12px;font-weight:600;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:120px;">${safeName}</span>
        <span style="font-size:10px;">${mode}</span>
      </div>
      <div style="font-size:13px;font-weight:700;font-family:'JetBrains Mono';color:${pnl >= 0 ? 'var(--success)' : 'var(--danger)'};">₹${pnl.toFixed(2)}</div>
    </div>`;
  });

  // Refresh button
  html += `<div class="live-tabs-refresh" style="margin-left:auto;padding:8px 12px;display:flex;align-items:center;">
    <button class="btn" onclick="loadLiveMonitor()" style="--btn-bg: rgba(59,130,246,0.15);--btn-color: #93c5fd;--btn-border: rgba(59,130,246,0.3);font-size:11px;padding:4px 12px;">${ICO.refresh(14)}</button>
  </div>`;

  bar.innerHTML = html;
}

function renderLivePanel(d, idx) {
  const container = document.getElementById('live-panels-container');
  const running = d.running;
  const inTrade = d.in_trade;
  const name = d.strategy_name || d.run_id || 'Strategy';
  const mode = d.mode || 'paper';
  const runId = d.run_id || '';
  const safeName = escapeHtml(name);
  const safeRunIdJs = escapeJsSingleQuoted(runId);
  const safeModeJs = escapeJsSingleQuoted(mode);
  const liveIdentityKey = _liveEngineIdentityKey(runId, mode);

  // Status badge
  let badgeHtml, statusText, statusColor;
  if (running && inTrade) {
    badgeHtml = '<span style="padding:3px 10px;border-radius:4px;font-size:11px;font-weight:600;background:rgba(34,197,94,0.15);color:#4ade80;">IN TRADE</span>';
    statusText = 'In Trade'; statusColor = '#4ade80';
  } else if (running) {
    badgeHtml = '<span style="padding:3px 10px;border-radius:4px;font-size:11px;font-weight:600;background:rgba(245,158,11,0.15);color:#f59e0b;">WAITING</span>';
    statusText = 'Waiting'; statusColor = '#f59e0b';
  } else {
    badgeHtml = '<span style="padding:3px 10px;border-radius:4px;font-size:11px;font-weight:600;background:rgba(239,68,68,0.15);color:var(--red);">STOPPED</span>';
    statusText = 'Idle'; statusColor = 'var(--muted)';
  }

  const modeLabel = mode === 'auto' ? ICO.bot(16) + ' Auto' : ICO.memo(16) + ' Paper';
  const modeColor = mode === 'auto' ? 'var(--accent2)' : 'var(--accent)';
  const closedPnl = round2(d.total_pnl || 0);
  const pnlColor = closedPnl >= 0 ? 'var(--success)' : 'var(--danger)';
  const strategyId = Number(d.strategy_id || (d.strategy || {}).strategy_id || 0);
  const folderName = _resolveSavedStrategyFolder(name, d.folder || (d.strategy || {}).folder || '', strategyId);
  const safeFolderNameJs = escapeJsSingleQuoted(folderName || '');
  const safeStrategyNameJs = escapeJsSingleQuoted(name);
  const folderBadgeHtml = `<button type="button" onclick="openSavedStrategyFolder('${safeStrategyNameJs}','${safeFolderNameJs}',${strategyId || 0});return false;" title="${escapeAttr(folderName ? `Open folder: ${folderName}` : 'Locate this strategy in Saved Strategies')}" style="display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:600;background:rgba(99,102,241,0.12);color:rgb(165,148,249);border:1px solid rgba(99,102,241,0.25);cursor:pointer;">${ICO.folder(12)} ${escapeHtml(folderName || 'Locate Folder')}</button>`;

  const instLabel = INST_NAMES[d.instrument] || (d.instrument ? `Instrument ${d.instrument}` : 'NIFTY');
  const candle = d.current_candle || {};
  const candleTime = candle.updated_at ? `Updated: ${candle.updated_at}` : (d.current_time ? `Last tick: ${d.current_time.slice(11,19)}` : 'Waiting...');

  // Signal / indicator rows
  let signalHtml;
  if (!running || !candle.close) {
    signalHtml = '<tr><td colspan="2" style="text-align:center;padding:30px;color:var(--muted);">Deploy a strategy to see live signals</td></tr>';
  } else {
    const ohlcvRows = [['current', d.current_spot || candle.close],['open', candle.open],['high', candle.high],['low', candle.low],['close', candle.close],['volume', candle.volume],['openInterest', candle.openInterest]];
    const indRows = Object.entries(d.current_indicators || {});
    signalHtml = [...ohlcvRows, ...indRows].map(([k, v]) => {
      const isInd = !['current','open','high','low','close','volume','openInterest'].includes(k);
      const displayValue = typeof v === 'number' ? v.toLocaleString('en-IN') : String(v ?? '—');
      return `<tr style="border-bottom:1px solid var(--border);"><td style="padding:7px 16px;color:${isInd?'var(--text)':'var(--muted)'};${isInd?'':'padding-left:28px;'}">${escapeHtml(k)}</td><td style="padding:7px 16px;text-align:right;font-family:'JetBrains Mono';font-size:12px;color:${isInd?'var(--accent2)':'var(--text)'};">${escapeHtml(displayValue)}</td></tr>`;
    }).join('');
  }
  const signalFieldCount = (!running || !candle.close) ? 0 : (7 + Object.keys(d.current_indicators || {}).length);

  // Positions
  const positions = d.positions || [];
  let posHtml;
  if (!positions.length) {
    posHtml = '<tr><td colspan="6" style="text-align:center;padding:16px;color:var(--muted);font-size:12px;">No open positions</td></tr>';
  } else {
    posHtml = positions.map((p, idx) => {
      const pnl = round2(p.unrealized_pnl || 0);
      const exitApi = mode === 'auto' ? '/api/live/exit-position' : '/api/paper/exit-position';
      return `<tr style="border-bottom:1px solid var(--border);"><td style="padding:7px 12px;">${escapeHtml(p.symbol||p.trading_symbol||'—')}</td><td style="padding:7px 12px;text-align:right;color:${p.transaction_type==='BUY'?'var(--success)':'var(--danger)'};">${escapeHtml(p.transaction_type || '')}</td><td style="padding:7px 12px;text-align:right;font-family:'JetBrains Mono';">₹${round2(p.entry_premium||0).toFixed(2)}</td><td style="padding:7px 12px;text-align:right;font-family:'JetBrains Mono';">₹${round2(p.current_premium||0).toFixed(2)}</td><td style="padding:7px 12px;text-align:right;font-family:'JetBrains Mono';color:${pnl>=0?'var(--success)':'var(--danger)'};">₹${pnl.toFixed(2)}</td><td style="padding:7px 8px;text-align:center;"><button onclick="_forceExitPosition('${escapeJsSingleQuoted(exitApi)}','${safeRunIdJs}',${idx})" style="background:linear-gradient(180deg,rgba(239,68,68,0.2),rgba(180,40,40,0.4));color:var(--red);border:1px solid rgba(239,68,68,0.5);padding:4px 12px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;">Exit</button></td></tr>`;
    }).join('');
  }

  // Events
  const events = (d.event_log || []).slice().reverse();
  const evtColors = {signal:'#4ade80',error:'var(--red)',warning:'var(--warn)',stop:'var(--red)',start:'#4ade80',info:'var(--muted)'};
  let evtHtml;
  if (!events.length) {
    evtHtml = '<div class="live-event-empty">No events yet.</div>';
  } else {
    evtHtml = events.map(e => {
      const typeKey = String(e.type || '').toLowerCase();
      return `<div class="live-event-line"><span style="color:var(--muted);">[${escapeHtml(e.time || '')}]</span> <span style="color:${evtColors[typeKey]||'var(--text)'};">[${escapeHtml(String(e.type || '').toUpperCase())}]</span> ${escapeHtml(e.message || '')}</div>`;
    }).join('');
  }

  // Condition Debug
  const cdebug = d.condition_debug || {};
  let condDebugHtml = '';
  if (running && cdebug.gate) {
    const gate = cdebug.gate;
    const conds = cdebug.conditions || [];
    if (gate !== 'evaluating') {
      condDebugHtml = `<div style="padding:8px 12px;color:var(--muted);font-size:11px;font-family:'JetBrains Mono';">⏸ Skipping entry: <span style="color:var(--warn);">${escapeHtml(gate)}</span></div>`;
    } else {
      const overall = cdebug.overall;
      const timeStr = cdebug.time || '';
      const rows = conds.map(c => {
        const icon = c.result ? '✅' : '❌';
        const color = c.result ? 'var(--success)' : 'var(--danger)';
        return `<tr style="border-bottom:1px solid var(--border);">
          <td style="padding:5px 8px;font-size:11px;">${icon}</td>
          <td style="padding:5px 8px;font-size:11px;color:var(--text);">${escapeHtml(c.condition || '')}</td>
          <td style="padding:5px 8px;font-size:11px;text-align:right;font-family:'JetBrains Mono';color:var(--accent2);">${escapeHtml(c.left_value ?? '')}</td>
          <td style="padding:5px 8px;font-size:11px;text-align:right;font-family:'JetBrains Mono';color:var(--accent2);">${escapeHtml(c.right_value ?? '')}</td>
          <td style="padding:5px 8px;font-size:11px;text-align:center;color:${color};font-weight:700;">${c.result?'PASS':'FAIL'}</td>
        </tr>`;
      }).join('');
      const overallColor = overall ? 'var(--success)' : 'var(--danger)';
      const overallLabel = overall ? '✅ ALL MET — signal pending' : '❌ NOT MET';
      condDebugHtml = `
        <div style="padding:8px 12px;font-size:11px;color:var(--muted);">Last check: ${escapeHtml(timeStr)} — Result: <span style="color:${overallColor};font-weight:700;">${escapeHtml(overallLabel)}</span></div>
        <table style="width:100%;border-collapse:collapse;">
          <thead><tr style="background:var(--card2);"><th style="padding:5px 8px;text-align:left;color:var(--muted);font-size:10px;"></th><th style="padding:5px 8px;text-align:left;color:var(--muted);font-size:10px;">CONDITION</th><th style="padding:5px 8px;text-align:right;color:var(--muted);font-size:10px;">LEFT</th><th style="padding:5px 8px;text-align:right;color:var(--muted);font-size:10px;">RIGHT</th><th style="padding:5px 8px;text-align:center;color:var(--muted);font-size:10px;">RESULT</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }
  }

  // Closed trades with pagination
  const closed = (d.closed_trades || []).slice().reverse();
  const closedTotal = closed.length;
  const closedTotalPages = Math.max(1, Math.ceil(closedTotal / _LIVE_TRADES_PER_PAGE));
  if (!_liveClosedPages[liveIdentityKey]) _liveClosedPages[liveIdentityKey] = 1;
  if (_liveClosedPages[liveIdentityKey] > closedTotalPages) _liveClosedPages[liveIdentityKey] = closedTotalPages;
  const closedPage = _liveClosedPages[liveIdentityKey];
  const closedTradesBlockHtml = _renderCompletedTradesBlock(closed, {
    page: closedPage,
    perPage: _LIVE_TRADES_PER_PAGE,
    pageHandlerTemplate: `_goLiveClosedPage('${safeRunIdJs}','${safeModeJs}', __PAGE__)`,
  });

  // Build full panel
  // Entry/Exit conditions from strategy config
  const strat = d.strategy || {};
  const entryConds = strat.entry_conditions || [];
  const exitConds = strat.exit_conditions || [];
  const legs = strat.legs || [];
  const chipS = "display:inline-block;padding:3px 8px;border-radius:999px;font-size:10px;font-family:'JetBrains Mono',monospace;margin:0 4px 4px 0;white-space:nowrap;";
  const condVal = (c) => c.right === 'number' ? c.right_number_value : c.right === 'days' ? (c.right_days || []).join(',') : c.right === 'time' ? (c.right_time || '') : c.right;

  let conditionsHtml = '';
  if (entryConds.length || exitConds.length || legs.length) {
    let entryChips = entryConds.map((c, i) => {
      const logic = i === 0 ? 'IF' : (c.logic || 'AND');
      return `<span style="${chipS}background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.24);"><span style="color:var(--muted);">${escapeHtml(logic)}</span> ${escapeHtml(c.left || '')} <span style="color:var(--accent);">${escapeHtml(c.operator || '')}</span> ${escapeHtml(condVal(c))}</span>`;
    }).join('');
    let exitChips = exitConds.map((c, i) => {
      const logic = i === 0 ? 'IF' : (c.logic || 'AND');
      return `<span style="${chipS}background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.24);"><span style="color:var(--muted);">${escapeHtml(logic)}</span> ${escapeHtml(c.left || '')} <span style="color:var(--warn);">${escapeHtml(c.operator || '')}</span> ${escapeHtml(condVal(c))}</span>`;
    }).join('');
    let legsChips = legs.map(l => {
      const lc = l.transaction_type === 'BUY' ? (l.option_type === 'CE' ? 'var(--success)' : 'var(--danger)') : (l.option_type === 'CE' ? 'var(--warn)' : 'var(--accent2)');
      const _sv = l.strike_value || ''; const _st = l.strike_type || 'atm';
      const _stLabel = _st==='atm'?'ATM':_st==='premium_above'?`Prem≥${_sv}`:_st==='premium_below'?`Prem≤${_sv}`:_st==='premium_near'?`Prem~${_sv}`:_st==='otm'?`OTM+${_sv}`:_st==='itm'?`ITM-${_sv}`:_st==='strike_price'?`@${_sv}`:_st==='spot_price'?`Spot±${_sv}`:_st.toUpperCase();
      return `<span style="${chipS}background:rgba(139,92,246,0.1);border:1px solid rgba(139,92,246,0.25);"><span style="color:${lc};font-weight:700;">${escapeHtml(l.transaction_type || '')} ${escapeHtml(l.option_type || '')}</span> ${escapeHtml(_stLabel)}${l.sl_pct?' SL:'+l.sl_pct+'%':''}${l.target_pct?' TP:'+l.target_pct+'%':''}</span>`;
    }).join('');

    conditionsHtml = `
    <div style="padding:10px 28px;background:rgba(0,0,0,0.15);border-bottom:1px solid var(--border);display:flex;flex-wrap:wrap;gap:12px;align-items:flex-start;">
      ${entryChips ? `<div style="display:flex;flex-wrap:wrap;align-items:center;gap:2px;"><span style="font-size:10px;font-weight:700;color:var(--success);text-transform:uppercase;letter-spacing:0.5px;margin-right:4px;">ENTRY</span>${entryChips}</div>` : ''}
      ${exitChips ? `<div style="display:flex;flex-wrap:wrap;align-items:center;gap:2px;"><span style="font-size:10px;font-weight:700;color:var(--warn);text-transform:uppercase;letter-spacing:0.5px;margin-right:4px;">EXIT</span>${exitChips}</div>` : ''}
      ${legsChips ? `<div style="display:flex;flex-wrap:wrap;align-items:center;gap:2px;"><span style="font-size:10px;font-weight:700;color:var(--accent2);text-transform:uppercase;letter-spacing:0.5px;margin-right:4px;">LEGS</span>${legsChips}</div>` : ''}
    </div>`;
  }

  container.innerHTML = `
  <!-- Header bar -->
  <div class="live-panel-header" style="display:flex;align-items:center;justify-content:space-between;padding:14px 28px;background:var(--card);border-bottom:${conditionsHtml ? 'none' : '1px solid var(--border)'};">
    <div style="display:flex;align-items:center;gap:12px;">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <div style="font-size:18px;font-weight:700;color:var(--text);">${safeName}</div>
        ${folderBadgeHtml}
      </div>
      ${badgeHtml}
    </div>
    <div class="live-panel-actions" style="display:flex;gap:8px;">
      <button class="btn" onclick="viewRunningStrategy('${safeRunIdJs}','${safeModeJs}')" style="--btn-bg: rgba(139,92,246,0.15);--btn-color: #a78bfa;--btn-border: rgba(139,92,246,0.3);font-size:12px;padding:6px 16px;">${ICO.eye(14)} Strategy</button>
      ${running ? `<button class="btn" onclick="stopEngine('${safeRunIdJs}','${safeModeJs}')" style="--btn-bg: rgba(239,68,68,0.15);--btn-color: var(--red);--btn-border: rgba(239,68,68,0.3);font-size:12px;padding:6px 16px;">${ICO.sqstop(14)} Stop</button>` : ''}
      ${!running && runId ? `<button class="btn" onclick="restartEngine('${safeRunIdJs}','${safeModeJs}')" style="--btn-bg: rgba(34,197,94,0.15);--btn-color: #4ade80;--btn-border: rgba(34,197,94,0.3);font-size:12px;padding:6px 16px;">${ICO.play(14)} Start</button>` : ''}
      ${!running && runId ? `<button class="btn" onclick="dismissEngine('${safeRunIdJs}','${safeModeJs}')" style="--btn-bg: rgba(107,114,128,0.15);--btn-color: #9ca3af;--btn-border: rgba(107,114,128,0.3);font-size:12px;padding:6px 16px;">${ICO.trash(14)} Dismiss</button>` : ''}
      <button class="btn" onclick="downloadPaperCSV()" style="--btn-bg: rgba(59,130,246,0.15);--btn-color: #93c5fd;--btn-border: rgba(59,130,246,0.3);font-size:11px;padding:6px 12px;">${ICO.download(14)} CSV</button>
    </div>
  </div>

  ${conditionsHtml}

  <!-- 7 stat cards -->
  <div class="live-panel-stats" style="display:grid;grid-template-columns:repeat(7,1fr);gap:12px;padding:20px 28px 8px;">
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;">
      <div style="font-size:10px;color:var(--muted);margin-bottom:6px;">Completed P&L</div>
      <div style="font-size:20px;font-weight:700;color:${pnlColor};font-family:'JetBrains Mono';">₹${closedPnl.toFixed(2)}</div>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;">
      <div style="font-size:10px;color:var(--muted);margin-bottom:6px;">Run Type</div>
      <div style="font-size:16px;font-weight:700;color:${modeColor};">${modeLabel}</div>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;">
      <div style="font-size:10px;color:var(--muted);margin-bottom:6px;">Lots</div>
      <div style="font-size:20px;font-weight:700;color:var(--purple);font-family:'JetBrains Mono';">${legs.length ? legs.map(l => l.lots || 1).join(' + ') : '—'}</div>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;">
      <div style="font-size:10px;color:var(--muted);margin-bottom:6px;">Trades Today</div>
      <div style="font-size:20px;font-weight:700;color:var(--accent2);font-family:'JetBrains Mono';">${d.trades_today || 0} / ${strat.max_trades_per_day || '—'}</div>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;">
      <div style="font-size:10px;color:var(--muted);margin-bottom:6px;">Max Trades/Day</div>
      <div style="font-size:20px;font-weight:700;color:var(--warn);font-family:'JetBrains Mono';">${strat.max_trades_per_day || '—'}</div>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;">
      <div style="font-size:10px;color:var(--muted);margin-bottom:6px;">Order Type</div>
      <div style="font-size:16px;font-weight:700;color:${(strat.deploy_config || {}).product_type === 'NORMAL' ? 'var(--accent2)' : 'var(--purple)'};">${escapeHtml((strat.deploy_config || {}).product_type || (d.deploy_config || {}).product_type || 'MIS')}</div>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;">
      <div style="font-size:10px;color:var(--muted);margin-bottom:6px;">Status</div>
      <div style="font-size:16px;font-weight:700;color:${statusColor};">${statusText}</div>
    </div>
  </div>

  <div class="live-panel-main" style="display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:8px 28px 20px;">
    <!-- Left: Signal / Indicator table -->
    <div class="live-panel-card" style="background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;">
        <div style="padding:12px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;">
          <div style="display:flex;align-items:center;gap:10px;min-width:0;">
            <span style="font-weight:700;font-size:14px;">${escapeHtml(instLabel)}</span>
            ${signalFieldCount ? `<span style="font-size:10px;color:var(--muted);font-family:'JetBrains Mono',monospace;padding:3px 8px;border-radius:999px;background:rgba(255,255,255,0.05);border:1px solid var(--border);">${signalFieldCount} fields</span>` : ''}
          </div>
          <span style="font-size:11px;color:var(--muted);">${escapeHtml(candleTime)}</span>
        </div>
      <div class="live-data-window">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead><tr style="background:var(--card2);"><th style="padding:8px 16px;text-align:left;color:var(--muted);font-weight:600;font-size:11px;">Field</th><th style="padding:8px 16px;text-align:right;color:var(--muted);font-weight:600;font-size:11px;">Value</th></tr></thead>
        <tbody>${signalHtml}</tbody>
      </table>
      </div>
    </div>
    <!-- Right: Open Positions + Event Log -->
    <div class="live-panel-side" style="display:flex;flex-direction:column;gap:12px;">
      <div class="live-panel-card" style="background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;">
        <div style="padding:12px 16px;border-bottom:1px solid var(--border);font-weight:700;font-size:13px;">Open Positions</div>
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
          <thead><tr style="background:var(--card2);"><th style="padding:7px 12px;text-align:left;color:var(--muted);font-size:11px;">Symbol</th><th style="padding:7px 12px;text-align:right;color:var(--muted);font-size:11px;">Type</th><th style="padding:7px 12px;text-align:right;color:var(--muted);font-size:11px;">Entry</th><th style="padding:7px 12px;text-align:right;color:var(--muted);font-size:11px;">Current</th><th style="padding:7px 12px;text-align:right;color:var(--muted);font-size:11px;">Unr. P&L</th><th style="padding:7px 8px;text-align:center;color:var(--muted);font-size:11px;">Action</th></tr></thead>
          <tbody>${posHtml}</tbody>
        </table>
      </div>
      <div class="live-panel-card" style="background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;flex:1;">
        <div style="padding:12px 16px;border-bottom:1px solid var(--border);font-weight:700;font-size:13px;">Event Log</div>
        <div class="live-event-log">${evtHtml}</div>
      </div>
      ${condDebugHtml ? `<div class="live-panel-card" style="background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;">
        <div style="padding:12px 16px;border-bottom:1px solid var(--border);font-weight:700;font-size:13px;">Entry Condition Debug</div>
        ${condDebugHtml}
      </div>` : ''}
    </div>
  </div>

  <!-- Closed Trades -->
  <div style="margin:0 28px 28px;">
    ${closedTradesBlockHtml}
  </div>`;
}

function _goLiveClosedPage(runId, mode, page) {
  const eng = _liveEngines[_selectedLiveTab];
  if (!eng) return;
  const closed = (eng.closed_trades || []);
  const tp = Math.max(1, Math.ceil(closed.length / _LIVE_TRADES_PER_PAGE));
  _liveClosedPages[_liveEngineIdentityKey(runId, mode)] = Math.max(1, Math.min(page, tp));
  renderLivePanel(eng, _selectedLiveTab);
}

async function _forceExitPosition(apiPath, runId, posIndex) {
  const ok = await customConfirm('Are you sure you want to exit this position?', { title: 'Exit Position', okText: 'Exit', danger: true });
  if (!ok) return;
  try {
    const res = await fetch(apiPath, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ run_id: runId, position_index: posIndex })
    });
    const data = await res.json();
    if (data.status === 'ok') {
      showToast(data.message || 'Position exited', 'success');
      loadLiveMonitor();
    } else if (data.status === 'partial') {
      showToast(data.message || 'Position partially exited', 'warning');
      loadLiveMonitor();
    } else if (data.status === 'pending') {
      showToast(data.message || 'Exit retry pending', 'warning');
      loadLiveMonitor();
    } else {
      showToast(data.message || 'Exit failed', 'error');
      loadLiveMonitor();
    }
  } catch (e) {
    showToast('Exit request failed: ' + e.message, 'error');
  }
}

function round2(n) { return Math.round(n * 100) / 100; }

// ══════════════════════════════════════════════════════════════
//  PORTFOLIO / PROFILE PAGE
// ══════════════════════════════════════════════════════════════
async function loadPortfolioData() {
  try {
    // Fetch all data sources — each with individual error handling
    let brokerData = { status: 'error', available_balance: 0, funds: {} };
    let tradesData = { status: 'error', trades: [] };
    let paperData = { positions: [], closed_trades: [] };
    let ordersData = { status: 'error', data: [] };
    let positionsData = { status: 'error', data: [] };
    let enginesData = { engines: [] };

    const results = await Promise.allSettled([
      fetch('/api/broker/check', { method: 'POST' }).then(r => r.json()),
      fetch('/api/broker/trades').then(r => r.json()),
      fetch('/api/paper/status').then(r => r.json()),
      fetch('/api/orders').then(r => r.json()),
      fetch('/api/positions').then(r => r.json()),
      fetch('/api/portfolio/history').then(r => r.json()),
      fetch('/api/engines/all').then(r => r.json()),
    ]);

    if (results[0].status === 'fulfilled') brokerData = results[0].value;
    if (results[1].status === 'fulfilled') tradesData = results[1].value;
    if (results[2].status === 'fulfilled') paperData = results[2].value;
    if (results[3].status === 'fulfilled') ordersData = results[3].value;
    if (results[4].status === 'fulfilled') positionsData = results[4].value;
    if (results[6].status === 'fulfilled') enginesData = results[6].value;
    _portfolioEngineSnapshotCache = Array.isArray(enginesData.engines) ? enginesData.engines : [];

    let portfolioHistory = { monthly: {}, yearly: {} };
    if (results[5].status === 'fulfilled' && results[5].value.status === 'success') {
      portfolioHistory = results[5].value;
    }

    console.log('[Portfolio] Broker data:', brokerData);
    console.log('[Portfolio] Trades API response:', tradesData);
    console.log('[Portfolio] Paper data:', paperData);

    // Get real Dhan trades
    const dhanTrades = Array.isArray(tradesData.trades) ? tradesData.trades : [];
    console.log(`[Portfolio] Loaded ${dhanTrades.length} real Dhan trades`);
    if (dhanTrades.length > 0) {
      console.log('[Portfolio] Sample trade:', dhanTrades[0]);
    } else {
      console.warn('[Portfolio] No Dhan trades found. Will use paper trading data for display.');
    }

    // Orders array from wrapped response
    const ordersArr = Array.isArray(ordersData.data) ? ordersData.data : (Array.isArray(ordersData) ? ordersData : []);

    let totalTrades = 0;
    let winningTrades = 0;
    let monthlyPnL = {};
    let yearlyPnL = {};
    const toFiniteNumber = (value) => {
      const num = Number(value);
      return Number.isFinite(num) ? num : null;
    };
    const formatMarketDateKey = (date) => {
      if (!(date instanceof Date) || isNaN(date.getTime())) return '';
      return getMarketDateKey(date);
    };
    const todayDateStr = formatMarketDateKey(new Date());

    const hasOpenBrokerQty = (position) => {
      const qty = toFiniteNumber(
        position.netQty ?? position.netQuantity ?? position.quantity ?? position.qty ?? null
      );
      return qty === null ? true : Math.abs(qty) > 0;
    };

    const getOpenBrokerPositionPnL = (position, allowDayProfitFallback = false) => {
      const candidates = [
        position.unrealizedProfit,
        position.unRealizedProfit,
        position.unrealizedPnl,
        position.mtm,
      ];
      if (allowDayProfitFallback) {
        candidates.push(position.dayProfit, position.dayPnl);
      }
      for (const candidate of candidates) {
        const num = toFiniteNumber(candidate);
        if (num !== null) return num;
      }
      return 0;
    };

    // Helper function to parse various date formats from Dhan
    const parseTradeDate = (trade) => {
      // Try different date field names that Dhan API might use
      const rawDate = trade.tradeDate || trade.createTime || trade.updateTime ||
                      trade.exchangeTime || trade.transactionTime || trade.orderTimestamp ||
                      trade.exit_time || trade.entry_time || trade.timestamp || '';

      if (!rawDate) return null;

      // Handle epoch timestamps (seconds or milliseconds)
      if (typeof rawDate === 'number') {
        const timestamp = rawDate > 1e12 ? rawDate : rawDate * 1000; // Convert seconds to ms if needed
        return new Date(timestamp);
      }

      // Handle string dates
      const dateStr = String(rawDate);

      // Try plain YYYY-MM-DD as a local calendar date
      if (dateStr.match(/^\d{4}-\d{2}-\d{2}$/)) {
        const [year, month, day] = dateStr.split('-').map(Number);
        return new Date(year, month - 1, day);
      }

      // Try ISO datetime strings
      if (dateStr.match(/^\d{4}-\d{2}-\d{2}[T\s]/)) {
        const parsedIso = new Date(dateStr);
        return isNaN(parsedIso.getTime()) ? null : parsedIso;
      }

      // Try DD-MM-YYYY format (common in Indian systems)
      if (dateStr.match(/^\d{2}-\d{2}-\d{4}/)) {
        const [day, month, year] = dateStr.split('-').map(Number);
        return new Date(year, month - 1, day);
      }

      // Fallback: try to parse as-is
      const parsed = new Date(dateStr);
      return isNaN(parsed.getTime()) ? null : parsed;
    };

    const compactNames = (names, fallback = '—') => {
      const unique = [...new Set((names || []).map(name => String(name || '').trim()).filter(Boolean))];
      if (!unique.length) return fallback;
      if (unique.length <= 2) return unique.join(', ');
      return `${unique[0]} +${unique.length - 1}`;
    };

    const countUniqueTradeOrders = (trades) => {
      const keys = new Set();
      (trades || []).forEach(trade => {
        if (!trade || typeof trade !== 'object') return;
        const orderKey = String(trade.orderId || trade.exchangeOrderId || '').trim();
        if (orderKey) {
          keys.add(orderKey);
          return;
        }
        const fallbackKey = [
          trade.transactionType || '',
          trade.securityId || trade.tradingSymbol || '',
          trade.createTime || trade.exchangeTime || trade.updateTime || '',
          trade.tradedPrice || '',
          trade.tradedQuantity || '',
        ].join('|');
        if (fallbackKey.replace(/\|/g, '').trim()) keys.add(fallbackKey);
      });
      return keys.size;
    };

    const summarizeRunningPaperEngines = (engines) => {
      const snapshot = {
        activeCount: 0,
        names: [],
        closedTrades: 0,
        openPositions: 0,
        openUnrealized: 0,
        totalPnl: 0,
        daily: {},
      };

      (engines || []).forEach(engine => {
        if (!engine || engine.mode !== 'paper' || !engine.running) return;
        snapshot.activeCount += 1;
        snapshot.names.push(engine.strategy_name || engine.run_id || 'Paper Strategy');
        const closedTrades = Array.isArray(engine.closed_trades) ? engine.closed_trades : [];
        const positions = Array.isArray(engine.positions) ? engine.positions : [];
        snapshot.closedTrades += closedTrades.length;
        snapshot.openPositions += positions.length;
        snapshot.openUnrealized += positions.reduce((sum, position) => sum + Number(position.unrealized_pnl || 0), 0);
        snapshot.totalPnl += Number(engine.total_pnl || 0);

        closedTrades.forEach(trade => {
          const tradeDate = parseTradeDate(trade);
          const tradeDateStr = formatMarketDateKey(tradeDate);
          if (!tradeDateStr) return;
          if (!snapshot.daily[tradeDateStr]) snapshot.daily[tradeDateStr] = { pnl: 0, count: 0, wins: 0 };
          const pnl = Number(trade.pnl || 0);
          snapshot.daily[tradeDateStr].pnl += pnl;
          snapshot.daily[tradeDateStr].count += 1;
          if (pnl > 0) snapshot.daily[tradeDateStr].wins += 1;
        });
      });

      if (snapshot.openUnrealized !== 0) {
        if (!snapshot.daily[todayDateStr]) snapshot.daily[todayDateStr] = { pnl: 0, count: 0, wins: 0 };
        snapshot.daily[todayDateStr].pnl += snapshot.openUnrealized;
      }

      snapshot.totalPnl = round2(snapshot.totalPnl);
      snapshot.openUnrealized = round2(snapshot.openUnrealized);
      return snapshot;
    };

    const mergePaperSnapshotIntoHistory = (history, snapshot) => {
      if (!snapshot || !snapshot.activeCount) return history || { daily: {}, monthly: {}, yearly: {} };
      const merged = {
        ...(history || {}),
        daily: { ...((history && history.daily) || {}) },
        monthly: { ...((history && history.monthly) || {}) },
        yearly: { ...((history && history.yearly) || {}) },
      };

      Object.entries(snapshot.daily || {}).forEach(([dateStr, data]) => {
        const existing = merged.daily[dateStr] || {};
        merged.daily[dateStr] = {
          ...existing,
          paper_pnl: round2(Number(existing.paper_pnl || 0) + Number(data.pnl || 0)),
          paper_trades: Number(existing.paper_trades || 0) + Number(data.count || 0),
          paper_wins: Number(existing.paper_wins || 0) + Number(data.wins || 0),
        };
      });

      return merged;
    };

    const runningPaperEngines = (Array.isArray(enginesData.engines) ? enginesData.engines : []).filter(
      engine => engine && engine.mode === 'paper' && engine.running
    );
    const runningPaperSnapshot = summarizeRunningPaperEngines(runningPaperEngines);
    portfolioHistory = mergePaperSnapshotIntoHistory(portfolioHistory, runningPaperSnapshot);
    const todayHistoryEntry = portfolioHistory.daily?.[todayDateStr] || {};

    // ── Helper: Calculate P&L by pairing BUY/SELL trades per securityId ──
    function calcPnLFromTrades(tradeList) {
      const groups = {};
      tradeList.forEach(t => {
        const key = t.securityId || t.tradingSymbol || 'unknown';
        if (!groups[key]) groups[key] = { buys: [], sells: [], symbol: t.tradingSymbol || key };
        if (t.transactionType === 'BUY') groups[key].buys.push(t);
        else if (t.transactionType === 'SELL') groups[key].sells.push(t);
      });

      let totalPnL = 0;
      const pnlByGroup = [];
      Object.values(groups).forEach(g => {
        const buyQty = g.buys.reduce((s, t) => s + Number(t.tradedQuantity || 0), 0);
        const sellQty = g.sells.reduce((s, t) => s + Number(t.tradedQuantity || 0), 0);
        const buyValue = g.buys.reduce((s, t) => s + Number(t.tradedPrice || 0) * Number(t.tradedQuantity || 0), 0);
        const sellValue = g.sells.reduce((s, t) => s + Number(t.tradedPrice || 0) * Number(t.tradedQuantity || 0), 0);
        const matchedQty = Math.min(buyQty, sellQty);
        if (matchedQty > 0 && buyQty > 0 && sellQty > 0) {
          const buyAvg = buyValue / buyQty;
          const sellAvg = sellValue / sellQty;
          const pnl = (sellAvg - buyAvg) * matchedQty;
          totalPnL += pnl;
          pnlByGroup.push({ symbol: g.symbol, pnl, buyAvg, sellAvg, qty: matchedQty });
        }
      });
      return { totalPnL, pnlByGroup };
    }

    // Process Dhan trades: group by date, compute P&L per day
    const tradesByDate = {};
    dhanTrades.forEach(trade => {
      const tradeDate = parseTradeDate(trade);
      if (tradeDate && !isNaN(tradeDate.getTime())) {
        const dateStr = formatMarketDateKey(tradeDate);
        if (!tradesByDate[dateStr]) tradesByDate[dateStr] = [];
        tradesByDate[dateStr].push(trade);
      }
    });

    // Compute monthly/yearly P&L and trade stats from paired trades
    Object.entries(tradesByDate).forEach(([dateStr, dayTrades]) => {
      const { totalPnL: dayPnL, pnlByGroup } = calcPnLFromTrades(dayTrades);
      const yearMonth = dateStr.slice(0, 7);
      const year = dateStr.slice(0, 4);

      monthlyPnL[yearMonth] = (monthlyPnL[yearMonth] || 0) + dayPnL;
      if (year && /^\d{4}$/.test(year)) {
        yearlyPnL[year] = (yearlyPnL[year] || 0) + dayPnL;
      }

      // Count paired instrument groups as trades
      pnlByGroup.forEach(g => {
        totalTrades++;
        if (g.pnl > 0) winningTrades++;
      });
    });

    // Today's filled orders from Dhan order book
    const todayOrders = ordersArr.filter(o => {
      if (o.orderStatus !== 'TRADED') return false;
      const orderDate = parseTradeDate(o);
      return formatMarketDateKey(orderDate) === todayDateStr;
    });

    // Filter today's trades from Dhan with improved date matching
    const todayDhanTrades = dhanTrades.filter(t => {
      const tradeDate = parseTradeDate(t);
      if (!tradeDate) return false;

      // Compare dates (ignore time component)
      const tradeDateStr = formatMarketDateKey(tradeDate);
      return tradeDateStr === todayDateStr;
    });

    console.log(`[Portfolio] Today (${todayDateStr}): ${todayDhanTrades.length} trades`);
    const todayOrderCountFromTrades = countUniqueTradeOrders(todayDhanTrades);

    // Calculate today's P&L from Dhan trades by pairing BUY/SELL
    let todayPnL = 0;
    let todayTradesDisplay = [];

    const openBrokerPositions = (Array.isArray(positionsData.data) ? positionsData.data : []).filter(hasOpenBrokerQty);
    const realizedBrokerTodayFallback = Number(
      todayHistoryEntry.real_net_pnl ?? todayHistoryEntry.real_pnl ?? 0
    ) || 0;

    if (todayDhanTrades.length > 0) {
      const { totalPnL, pnlByGroup } = calcPnLFromTrades(todayDhanTrades);
      todayPnL = totalPnL;
      // Convert grouped results to display format
      todayTradesDisplay = pnlByGroup.map(g => ({
        tradingSymbol: g.symbol,
        transactionType: 'PAIR',
        tradedQuantity: g.qty,
        buyAvgPrice: g.buyAvg,
        sellAvgPrice: g.sellAvg,
        pnl: g.pnl,
        createTime: todayDhanTrades.find(t => t.tradingSymbol === g.symbol)?.createTime || ''
      }));
      console.log(`[Portfolio] Today's P&L from Dhan trades (paired): ₹${todayPnL.toFixed(2)} (${pnlByGroup.length} instruments)`);

      // Add unrealized P&L from open Dhan positions
      if (openBrokerPositions.length > 0) {
        const unrealizedPnL = openBrokerPositions.reduce(
          (sum, p) => sum + getOpenBrokerPositionPnL(p),
          0
        );
        todayPnL += unrealizedPnL;
        console.log(`[Portfolio] Added unrealized P&L from ${openBrokerPositions.length} open positions: ₹${unrealizedPnL.toFixed(2)}`);
      }
    } else if (
      openBrokerPositions.length > 0 ||
      Number(todayHistoryEntry.real_trades || 0) > 0 ||
      realizedBrokerTodayFallback !== 0
    ) {
      const unrealizedPnL = openBrokerPositions.reduce(
        (sum, p) => sum + getOpenBrokerPositionPnL(p, realizedBrokerTodayFallback === 0),
        0
      );
      todayPnL = realizedBrokerTodayFallback + unrealizedPnL;
      console.log(
        `[Portfolio] Using broker fallback for today P&L: realized ₹${realizedBrokerTodayFallback.toFixed(2)}, open ₹${unrealizedPnL.toFixed(2)}, total ₹${todayPnL.toFixed(2)}`
      );
    } else {
      // Fallback to paper trading closed trades + unrealized positions for today
      const paperTodayTrades = (paperData.closed_trades || []).filter(t => {
        const ts = (t.exit_time || t.entry_time || '').toString();
        return ts.startsWith(todayDateStr);
      });
      const unrealizedPnL = (paperData.positions || []).reduce((sum, p) => sum + Number(p.unrealized_pnl || 0), 0);
      todayPnL = paperTodayTrades.reduce((sum, t) => sum + Number(t.pnl || 0), 0) + unrealizedPnL;
      todayTradesDisplay = paperTodayTrades;
      console.log(`[Portfolio] Paper trades: ${paperTodayTrades.length}, unrealized: ₹${unrealizedPnL.toFixed(2)}, total: ₹${todayPnL.toFixed(2)}`);
    }

    const availableBal = Number(brokerData.available_balance || brokerData.funds?.availabelBalance || 0);

    // Update the UI
    document.getElementById('portfolio-balance').textContent = '₹' + availableBal.toFixed(2);
    document.getElementById('portfolio-today-pnl').textContent = '₹' + todayPnL.toFixed(2);
    document.getElementById('portfolio-today-pnl').style.color = todayPnL >= 0 ? 'var(--success)' : 'var(--danger)';
    const todayTradeCount = Number(
      todayHistoryEntry.real_order_count ||
      todayOrders.length ||
      todayOrderCountFromTrades ||
      todayHistoryEntry.real_trade_legs ||
      todayHistoryEntry.real_trades ||
      todayDhanTrades.length ||
      0
    ) || 0;
    document.getElementById('portfolio-total-trades').textContent = todayTradeCount;
    window._portfolioTodayDisplayTradeCount = todayTradeCount;
    document.getElementById('portfolio-win-rate').textContent = totalTrades > 0 ? ((winningTrades / totalTrades) * 100).toFixed(1) + '%' : '0%';

    // ── Paper Trade P&L summary card ──
    const fallbackPaperClosed = Array.isArray(paperData.closed_trades) ? paperData.closed_trades : [];
    const fallbackPaperPositions = Array.isArray(paperData.positions) ? paperData.positions : [];
    const hasRunningPaperSnapshot = runningPaperSnapshot.activeCount > 0;
    const paperClosed = hasRunningPaperSnapshot ? [] : fallbackPaperClosed;
    const paperPositions = hasRunningPaperSnapshot ? [] : fallbackPaperPositions;
    const paperTotalPnl = hasRunningPaperSnapshot
      ? runningPaperSnapshot.totalPnl
      : fallbackPaperClosed.reduce((s, t) => s + Number(t.pnl || 0), 0)
        + fallbackPaperPositions.reduce((s, p) => s + Number(p.unrealized_pnl || 0), 0);
    const paperRunning = hasRunningPaperSnapshot ? true : (paperData.running || fallbackPaperPositions.length > 0);
    const paperStratName = hasRunningPaperSnapshot
      ? compactNames(runningPaperSnapshot.names, 'Paper Flow')
      : (paperData.strategy_name || paperData.run_name || '');
    const pfPaperPnlEl = document.getElementById('portfolio-paper-total-pnl');
    if (pfPaperPnlEl) {
      pfPaperPnlEl.textContent = '₹' + paperTotalPnl.toFixed(2);
      pfPaperPnlEl.style.color = paperTotalPnl > 0 ? 'var(--success)' : paperTotalPnl < 0 ? 'var(--danger)' : 'var(--muted)';
    }
    const pfStatus = document.getElementById('portfolio-paper-status');
    if (pfStatus) { pfStatus.textContent = paperRunning ? 'Running' : 'Idle'; pfStatus.style.color = paperRunning ? 'var(--success)' : 'var(--muted)'; }
    const pfSym = document.getElementById('portfolio-paper-symbol');
    if (pfSym) pfSym.textContent = paperStratName || (paperData.symbol || '—');
    const pfTrades = document.getElementById('portfolio-paper-trades-count');
    if (pfTrades) pfTrades.textContent = hasRunningPaperSnapshot ? runningPaperSnapshot.closedTrades : paperClosed.length;
    const pfMeta = document.getElementById('portfolio-paper-meta');
    if (pfMeta) {
      if (hasRunningPaperSnapshot) {
        const engineLabel = `${runningPaperSnapshot.activeCount} active run${runningPaperSnapshot.activeCount !== 1 ? 's' : ''}`;
        const positionLabel = `${runningPaperSnapshot.openPositions} open position${runningPaperSnapshot.openPositions !== 1 ? 's' : ''}`;
        pfMeta.textContent = `${engineLabel} · ${positionLabel} · Unrealized ₹${runningPaperSnapshot.openUnrealized.toFixed(2)}`;
      } else {
        const unrealized = paperPositions.reduce((s, p) => s + Number(p.unrealized_pnl || 0), 0);
        pfMeta.textContent = paperRunning
          ? `${paperPositions.length} open position${paperPositions.length !== 1 ? 's' : ''} · Unrealized ₹${unrealized.toFixed(2)}`
          : 'No paper run active';
      }
    }

    // ── Store portfolio history globally for month navigation ──
    window._portfolioDaily = portfolioHistory.daily || {};
    window._portfolioMonthly = portfolioHistory.monthly || {};
    window._currentMonthlyView = getMarketYearMonth();
    renderMonthlyDailyGrid();
    renderYearlyMonthlyTable();
    _renderPortfolioPaperRuns(_allRunsCache);
    if (_portfolioRunExpandedKey) _renderPortfolioPaperRuns(_allRunsCache);

    // Render today's trades (from Dhan or paper trading)
    const tradesBody = document.getElementById('portfolio-trades-body');
    if (todayTradesDisplay.length === 0) {
      tradesBody.innerHTML = '<tr><td colspan="7" style="text-align: center; padding: 20px; color: var(--muted);">No trades executed today</td></tr>';
    } else {
      tradesBody.innerHTML = todayTradesDisplay.slice().reverse().map(t => {
        // Handle paired Dhan trades, raw Dhan trades, and paper trades
        const pnl = Number(t.pnl || t.realizedProfit || 0);
        const isWin = pnl >= 0;

        // Parse time from trade
        let tradeTime = '-';
        const tradeDate = parseTradeDate(t);
        if (tradeDate) {
          tradeTime = tradeDate.toTimeString().slice(0, 5);
        } else {
          const timeStr = String(t.exit_time || t.entry_time || t.createTime || '-');
          tradeTime = timeStr.slice(11, 16) || '-';
        }

        const symbol = t.tradingSymbol || t.symbol || t.securityId || '-';
        const txnType = t.transactionType === 'PAIR' ? 'BUY→SELL' : (t.transactionType || t.transaction_type || '-');
        const optionType = t.option_type ? ' ' + t.option_type : '';

        // Prices: prefer paired format (buyAvgPrice/sellAvgPrice)
        const buyPrice = Number(t.buyAvgPrice || t.buyPrice || t.entry_price || t.tradedPrice || 0);
        const sellPrice = Number(t.sellAvgPrice || t.sellPrice || t.exit_price || 0);
        const qty = t.tradedQuantity || t.quantity || t.qty || t.lots || '-';

        return `
          <tr style="border-bottom: 1px solid var(--border);">
            <td style="padding: 10px 0;">${escapeHtml(tradeTime)}</td>
            <td>${escapeHtml(symbol)}</td>
            <td>${escapeHtml(txnType + optionType)}</td>
            <td style="font-family: 'JetBrains Mono'; font-size: 11px;">₹${buyPrice.toFixed(2)}</td>
            <td style="font-family: 'JetBrains Mono'; font-size: 11px;">₹${sellPrice.toFixed(2)}</td>
            <td>${escapeHtml(qty)}</td>
            <td style="font-weight: 700; color: ${isWin ? 'var(--success)' : 'var(--danger)'}; font-family: 'JetBrains Mono'; font-size: 11px;">₹${pnl.toFixed(2)}</td>
          </tr>
        `;
      }).join('');
    }
  } catch (err) {
    console.error('Failed to load portfolio data:', err);
    ['portfolio-balance', 'portfolio-today-pnl', 'portfolio-total-trades', 'portfolio-win-rate'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.textContent = 'Error';
    });
    toast('Failed to load portfolio data. Check broker connection.', 'danger');
  }
}

// ── Monthly P&L: Daily boxes for selected month ──
const MONTH_NAMES_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const MARKET_TIME_ZONE = 'Asia/Kolkata';

function getTimeZoneDateParts(date = new Date(), timeZone = MARKET_TIME_ZONE) {
  if (!(date instanceof Date) || isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date);
  const map = {};
  parts.forEach(part => {
    if (part.type !== 'literal') map[part.type] = part.value;
  });
  if (!map.year || !map.month || !map.day) return null;
  return {
    year: Number(map.year),
    month: Number(map.month),
    day: Number(map.day),
    yearStr: map.year,
    monthStr: map.month,
    dayStr: map.day,
  };
}

function getMarketDateKey(date = new Date()) {
  const parts = getTimeZoneDateParts(date);
  return parts ? `${parts.yearStr}-${parts.monthStr}-${parts.dayStr}` : '';
}

function getMarketYearMonth(date = new Date()) {
  const parts = getTimeZoneDateParts(date);
  return parts ? `${parts.yearStr}-${parts.monthStr}` : '';
}

function formatPortfolioDateLabel(dateStr) {
  const [year, month, day] = String(dateStr || '').split('-').map(Number);
  if (!year || !month || !day) return String(dateStr || '');
  return `${MONTH_NAMES_SHORT[month - 1]} ${day}, ${year}`;
}
const NSE_CAPITAL_MARKET_HOLIDAYS = new Set([
  '2024-01-26', '2024-03-08', '2024-03-25', '2024-03-29', '2024-04-11', '2024-04-17', '2024-05-01',
  '2024-06-17', '2024-07-17', '2024-08-15', '2024-10-02', '2024-11-01', '2024-11-15', '2024-12-25',
  '2025-02-26', '2025-03-14', '2025-03-31', '2025-04-10', '2025-04-14', '2025-04-18', '2025-05-01',
  '2025-08-15', '2025-08-27', '2025-10-02', '2025-10-21', '2025-10-22', '2025-11-05', '2025-12-25',
  '2026-01-26', '2026-03-03', '2026-03-26', '2026-03-31', '2026-04-03', '2026-04-14', '2026-05-01',
  '2026-05-28', '2026-06-26', '2026-09-14', '2026-10-02', '2026-10-20', '2026-11-10', '2026-11-24',
  '2026-12-25',
]);

function isClosedMarketDay(dateStr) {
  if (!dateStr) return false;
  const [year, month, day] = String(dateStr).split('-').map(Number);
  if (!year || !month || !day) return false;
  const dt = new Date(year, month - 1, day);
  const dow = dt.getDay();
  return dow === 0 || dow === 6 || NSE_CAPITAL_MARKET_HOLIDAYS.has(dateStr);
}

function getTimeZoneClockMinutes(date = new Date(), timeZone = MARKET_TIME_ZONE) {
  if (!(date instanceof Date) || isNaN(date.getTime())) return NaN;
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date);
  const map = {};
  parts.forEach(part => {
    if (part.type !== 'literal') map[part.type] = part.value;
  });
  if (!map.hour || !map.minute) return NaN;
  return Number(map.hour) * 60 + Number(map.minute);
}

function isMarketSessionOpen(date = new Date()) {
  const dateStr = getMarketDateKey(date);
  if (!dateStr || isClosedMarketDay(dateStr)) return false;
  const mins = getTimeZoneClockMinutes(date);
  return Number.isFinite(mins) && mins >= (9 * 60 + 15) && mins <= (15 * 60 + 30);
}

function updateLiveTabDot(engines = _liveEngines) {
  const liveDot = document.getElementById('live-tab-dot');
  if (!liveDot) return;
  liveDot.classList.remove('market-open', 'in-trade', 'market-closed');
  const marketOpen = isMarketSessionOpen();
  if (!marketOpen) {
    liveDot.classList.add('market-closed');
    return;
  }
  const anyInTrade = Array.isArray(engines) && engines.some((engine) => engine.running && engine.in_trade);
  if (anyInTrade) {
    liveDot.classList.add('in-trade');
    return;
  }
  liveDot.classList.add('market-open');
}

async function refreshLiveTabStatus() {
  if (liveMonitorInterval) {
    updateLiveTabDot(_liveEngines);
    return;
  }
  try {
    const res = await fetch('/api/engines/all');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _liveEngines = data.engines || [];
  } catch (e) {
    console.warn('Live tab status fetch error:', e);
  }
  updateLiveTabDot(_liveEngines);
}

function changeMonthlyMonth(delta) {
  const [y, m] = window._currentMonthlyView.split('-').map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  // Use local date parts to avoid UTC timezone shift (IST → UTC can shift date back)
  const ny = d.getFullYear();
  const nm = String(d.getMonth() + 1).padStart(2, '0');
  window._currentMonthlyView = `${ny}-${nm}`;
  renderMonthlyDailyGrid();
}

function renderMonthlyDailyGrid() {
  const ym = window._currentMonthlyView;
  const [year, month] = ym.split('-').map(Number);
  const monthName = MONTH_NAMES_SHORT[month - 1] + ' ' + year;
  const todayDateStr = getMarketDateKey(new Date());
  const todayDisplayTradeCount = Number(window._portfolioTodayDisplayTradeCount || 0) || 0;

  document.getElementById('portfolio-monthly-label').textContent = monthName;
  const grid = document.getElementById('portfolio-monthly-grid');
  const summaryEl = document.getElementById('portfolio-monthly-summary');

  const daily = window._portfolioDaily || {};

  // Get all days in this month that have data
  const daysInMonth = new Date(year, month, 0).getDate();
  const dayEntries = [];
  let totalGrossRealPnl = 0, totalNetRealPnl = 0, totalCharges = 0, totalBrokerage = 0, totalTrades = 0, tradingDays = 0, profitDays = 0;
  let totalPaperPnl = 0;

  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = ym + '-' + String(d).padStart(2, '0');
    if (isClosedMarketDay(dateStr)) continue;
    const entry = daily[dateStr];
    if (entry) {
      const grossRealPnl = Number(entry.real_pnl || 0);
      const paperPnl = Number(entry.paper_pnl || 0);
      const netRealPnl = Number(entry.real_net_pnl ?? entry.real_pnl ?? 0);
      const charges = entry.real_charges || 0;
      const brokerage = entry.real_brokerage || 0;
      const totalCosts = entry.real_total_costs || (charges + brokerage);
      const realTrades = entry.real_trade_legs || entry.real_trades || 0;
      const realOrderCount = entry.real_order_count || 0;
      const displayTradeCount = dateStr === todayDateStr
        ? (todayDisplayTradeCount || realOrderCount || realTrades)
        : realTrades;
      const paperTrades = entry.paper_trades || 0;
      const tradeLegs = entry.real_trade_legs || 0;
      const hasRealActivity = displayTradeCount > 0 || tradeLegs > 0 || grossRealPnl !== 0 || netRealPnl !== 0 || charges > 0;
      dayEntries.push({
        day: d,
        dateStr,
        charges,
        brokerage,
        totalCosts,
        tradeLegs,
        grossReal: grossRealPnl,
        netReal: netRealPnl,
        paper: paperPnl,
        realTrades,
        realOrderCount,
        displayTradeCount,
        paperTrades,
        hasRealActivity,
      });
      if (hasRealActivity) {
        totalGrossRealPnl += grossRealPnl;
        totalNetRealPnl += netRealPnl;
        totalCharges += charges;
        totalBrokerage += brokerage;
        totalTrades += displayTradeCount;
        tradingDays++;
        if (grossRealPnl > 0) profitDays++;
      }
      totalPaperPnl += paperPnl;
    }
  }

  const realDayEntries = dayEntries.filter(e => e.hasRealActivity);

  // Summary bar — real trades only (paper is shown in the Paper Trade P&L card)
  const isGrossProfit = totalGrossRealPnl >= 0;
  const isNetProfit = totalNetRealPnl >= 0;
  summaryEl.innerHTML = tradingDays > 0 ? `
    <div style="display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start;">
      <div>
        <div style="font-size: 10px; color: var(--muted); margin-bottom: 2px;">Overall P&L</div>
        <div style="font-size: 16px; font-weight: 700; color: ${isGrossProfit ? 'var(--success)' : 'var(--danger)'}; font-family: 'JetBrains Mono';">₹ ${totalGrossRealPnl.toFixed(2)}</div>
      </div>
      <div>
        <div style="font-size: 10px; color: var(--muted); margin-bottom: 2px;">Net Realised P&L</div>
        <div style="font-size: 16px; font-weight: 700; color: ${isNetProfit ? 'var(--success)' : 'var(--danger)'}; font-family: 'JetBrains Mono';">₹ ${totalNetRealPnl.toFixed(2)}</div>
      </div>
      <div>
        <div style="font-size: 10px; color: var(--muted); margin-bottom: 2px;">Total Trades</div>
        <div style="font-size: 16px; font-weight: 700; color: var(--text); font-family: 'JetBrains Mono';">${totalTrades}</div>
      </div>
      <div>
        <div style="font-size: 10px; color: var(--muted); margin-bottom: 2px;">Charges</div>
        <div style="font-size: 16px; font-weight: 700; color: var(--text); font-family: 'JetBrains Mono';">₹ ${totalCharges.toFixed(2)}</div>
      </div>
      <div>
        <div style="font-size: 10px; color: var(--muted); margin-bottom: 2px;">Brokerage</div>
        <div style="font-size: 16px; font-weight: 700; color: var(--text); font-family: 'JetBrains Mono';">₹ ${totalBrokerage.toFixed(2)}</div>
      </div>
      <div>
        <div style="font-size: 10px; color: var(--muted); margin-bottom: 2px;">Profitable Days</div>
        <div style="font-size: 16px; font-weight: 700; color: var(--success); font-family: 'JetBrains Mono';">${profitDays}/${tradingDays}</div>
      </div>
    </div>
    <div style="margin-top: 8px; font-size: 10px; color: var(--muted);">Charges include GST, STT, SEBI Fees, Exchange Transaction Charges and Stamp Duty. Brokerage is shown separately.</div>
  ` : `<span style="color: var(--muted);">No trades this month</span>`;

  // Daily boxes
  if (realDayEntries.length === 0) {
    grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 20px; color: var(--muted);">No real trade data for ' + monthName + '</div>';
  } else {
    let html = '';
    realDayEntries.forEach(e => {
      const isWin = e.grossReal >= 0;
      const dayLabel = String(e.day).padStart(2, '0') + ' ' + MONTH_NAMES_SHORT[month - 1];
      const detailLines = [`Gross: ₹${e.grossReal.toFixed(2)}`, `Net: ₹${e.netReal.toFixed(2)}`];
      if (e.charges > 0) detailLines.push(`Charges: ₹${e.charges.toFixed(2)}`);
      if (e.brokerage > 0) detailLines.push(`Brokerage: ₹${e.brokerage.toFixed(2)}`);
      if (e.realOrderCount > 0 && e.realOrderCount !== e.realTrades) detailLines.push(`Orders: ${e.realOrderCount}`);
      if (e.paper !== 0) detailLines.push(`Paper: ₹${e.paper.toFixed(2)}`);
      const details = detailLines.length ? `\n${detailLines.join('\n')}` : '';
      html += `
        <div style="padding: 8px 6px; background: ${isWin ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)'}; border: 1px solid ${isWin ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)'}; border-radius: 6px; text-align: center;" title="${e.dateStr}: Gross ₹${e.grossReal.toFixed(2)}${details} (${e.displayTradeCount} trades)">
          <div style="font-size: 10px; color: var(--muted); margin-bottom: 3px;">${dayLabel}</div>
          <div style="font-size: 13px; font-weight: 700; color: ${isWin ? 'var(--success)' : 'var(--danger)'}; font-family: 'JetBrains Mono';">₹${e.grossReal.toFixed(0)}</div>
          <div style="font-size: 9px; color: var(--muted); margin-top: 2px;">${e.displayTradeCount}T${e.charges > 0 ? ' · ₹' + e.charges.toFixed(0) + ' chg' : ''}</div>
        </div>
      `;
    });
    grid.innerHTML = html;
  }

  // ── Trades for month: day-by-day breakdown table (like Dhan) ──
  const tradesTableEl = document.getElementById('portfolio-monthly-trades');
  if (realDayEntries.length > 0) {
    const totalMonthTrades = realDayEntries.reduce((sum, entry) => sum + Number(entry.displayTradeCount || 0), 0);
    let tableHtml = `<table class="portfolio-monthly-trades-table">
      <thead>
        <tr>
          <th style="padding: 10px 8px; text-align: left;">Date</th>
          <th style="padding: 10px 8px; text-align: center;">Trades</th>
          <th style="padding: 10px 8px; text-align: right;">Gross Real</th>
          <th style="padding: 10px 8px; text-align: right;">Net Real</th>
          <th style="padding: 10px 8px; text-align: right;">Charges</th>
          <th style="padding: 10px 8px; text-align: right;">Brokerage</th>
        </tr>
      </thead>
      <tbody>`;
    realDayEntries.forEach(e => {
      const isRealWin = e.grossReal >= 0;
      const isNetWin = e.netReal >= 0;
      const dateLabel = formatPortfolioDateLabel(e.dateStr);
      tableHtml += `<tr>
        <td style="padding: 10px 8px; font-weight: 500;">${dateLabel}</td>
        <td style="padding: 10px 8px; text-align: center;" title="${e.realOrderCount > 0 && e.realOrderCount !== e.realTrades ? `${e.realOrderCount} orders, ${e.realTrades} fills` : `${e.displayTradeCount} trades`}">${e.displayTradeCount}</td>
        <td style="padding: 10px 8px; text-align: right; font-weight: 600; color: ${isRealWin ? 'var(--success)' : 'var(--danger)'}; font-family: 'JetBrains Mono';">₹ ${e.grossReal.toFixed(2)}</td>
        <td style="padding: 10px 8px; text-align: right; font-weight: 600; color: ${isNetWin ? 'var(--success)' : 'var(--danger)'}; font-family: 'JetBrains Mono';">₹ ${e.netReal.toFixed(2)}</td>
        <td style="padding: 10px 8px; text-align: right; font-family: 'JetBrains Mono';">₹ ${e.charges.toFixed(2)}</td>
        <td style="padding: 10px 8px; text-align: right; font-family: 'JetBrains Mono';">₹ ${e.brokerage.toFixed(2)}</td>
      </tr>`;
    });
    tableHtml += `</tbody></table>`;
    const cardHtml = realDayEntries.map(_portfolioMonthlyTradeCardHtml).join('');
    tradesTableEl.innerHTML = `
      <div class="portfolio-monthly-trades-panel${_portfolioMonthlyTradesOpen ? ' open' : ''}">
        <button type="button" class="portfolio-monthly-trades-head" onclick="togglePortfolioMonthlyTrades()">
          <div>
            <div class="portfolio-monthly-trades-title">Trades for month</div>
            <div class="portfolio-monthly-trades-sub">${realDayEntries.length} trading day${realDayEntries.length !== 1 ? 's' : ''} · ${totalMonthTrades} trade${totalMonthTrades !== 1 ? 's' : ''}</div>
          </div>
          <span class="portfolio-monthly-trades-chevron">${_portfolioMonthlyTradesOpen ? '▲' : '▼'}</span>
        </button>
        <div class="portfolio-monthly-trades-body">
          <div class="trade-table-scroll">
            ${tableHtml}
          </div>
          <div class="mobile-data-cards">${cardHtml}</div>
        </div>
      </div>`;
  } else {
    tradesTableEl.innerHTML = '';
  }

  // ── Paper P&L breakdown → left card ──
  const paperMonthEl = document.getElementById('portfolio-paper-monthly');
  if (paperMonthEl) {
    const paperDays = dayEntries.filter(e => e.paper !== 0);
    if (paperDays.length === 0) {
      paperMonthEl.innerHTML = '';
    } else {
      const paperTotal = paperDays.reduce((s, e) => s + e.paper, 0);
      const isPaperPos = paperTotal >= 0;
      let ph = `<div style="font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;">Paper — ${monthName}</div>`;
      ph += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(68px,1fr));gap:6px;margin-bottom:10px;">`;
      paperDays.forEach(e => {
        const win = e.paper >= 0;
        const dl = String(e.day).padStart(2,'0') + ' ' + MONTH_NAMES_SHORT[month-1];
        ph += `<div style="padding:6px 4px;background:${win?'rgba(34,197,94,0.08)':'rgba(239,68,68,0.08)'};border:1px solid ${win?'rgba(34,197,94,0.2)':'rgba(239,68,68,0.2)'};border-radius:5px;text-align:center;">
          <div style="font-size:9px;color:var(--muted);margin-bottom:2px;">${dl}</div>
          <div style="font-size:12px;font-weight:700;color:${win?'var(--success)':'var(--danger)'};font-family:'JetBrains Mono',monospace;">₹${e.paper.toFixed(0)}</div>
          <div style="font-size:8px;color:var(--muted);margin-top:1px;">${e.paperTrades}T</div>
        </div>`;
      });
      ph += `</div>`;
      ph += `<div style="font-size:11px;color:var(--muted);">Month total: <strong style="color:${isPaperPos?'var(--success)':'var(--danger)'};font-family:'JetBrains Mono',monospace;">₹${paperTotal.toFixed(2)}</strong></div>`;
      paperMonthEl.innerHTML = ph;
    }
  }
}

// ── Year-to-Date: Monthly table (Jan–Dec) per year from 2024 ──
function renderYearlyMonthlyTable() {
  const container = document.getElementById('portfolio-yearly-grid');
  const monthly = window._portfolioMonthly || {};

  const currentYear = (getTimeZoneDateParts(new Date()) || {}).year || new Date().getFullYear();
  const startYear = 2024;
  const years = [];
  for (let y = currentYear; y >= startYear; y--) years.push(y);

  // Build table
  let html = `<table class="portfolio-ytd-table">
    <thead>
      <tr style="border-bottom: 2px solid var(--border);">
        <th style="padding: 8px 6px; text-align: left; color: var(--muted); font-size: 11px; font-weight: 600;">Year</th>`;
  MONTH_NAMES_SHORT.forEach(m => {
    html += `<th style="padding: 8px 4px; text-align: center; color: var(--muted); font-size: 11px; font-weight: 600;">${m}</th>`;
  });
  html += `<th style="padding: 8px 6px; text-align: center; color: var(--accent); font-size: 11px; font-weight: 700;">Total</th></tr></thead><tbody>`;

  years.forEach(year => {
    let yearTotal = 0;
    html += `<tr style="border-bottom: 1px solid var(--border);">
      <td style="padding: 10px 6px; font-weight: 700; color: var(--text);">${year}</td>`;

    for (let m = 1; m <= 12; m++) {
      const key = year + '-' + String(m).padStart(2, '0');
      const data = monthly[key];
      if (data) {
        const pnl = Number(data.real_net_pnl ?? data.real_pnl ?? 0);
        yearTotal += pnl;
        const isWin = pnl >= 0;
        const trades = data.trades || 0;
        const grossRealPnl = Number(data.real_pnl || 0);
        const paperPnl = Number(data.paper_pnl || 0);
        const charges = Number(data.real_charges || 0);
        const brokerage = Number(data.real_brokerage || 0);
        const detailLines = [`${key}: ${trades} trades`, `Net real: ₹${pnl.toFixed(2)}`];
        if (grossRealPnl !== pnl || charges !== 0 || brokerage !== 0) {
          detailLines.push(`Gross real: ₹${grossRealPnl.toFixed(2)}`);
          detailLines.push(`Charges: ₹${charges.toFixed(2)}`);
          detailLines.push(`Brokerage: ₹${brokerage.toFixed(2)}`);
        }
        if (paperPnl !== 0) detailLines.push(`Paper: ₹${paperPnl.toFixed(2)}`);
        html += `<td style="padding: 10px 4px; text-align: center;" title="${detailLines.join('\n')}">
          <div style="font-weight: 600; color: ${isWin ? 'var(--success)' : 'var(--danger)'}; font-family: 'JetBrains Mono'; font-size: 11px;">₹${pnl.toFixed(0)}</div>
          <div style="font-size: 9px; color: var(--muted);">${trades}T</div>
        </td>`;
      } else {
        html += `<td style="padding: 10px 4px; text-align: center; color: var(--muted); font-size: 11px;">—</td>`;
      }
    }

    const isYearWin = yearTotal >= 0;
    html += `<td class="portfolio-ytd-total-cell" style="padding: 10px 6px; text-align: center; color: ${yearTotal !== 0 ? (isYearWin ? 'var(--success)' : 'var(--danger)') : 'var(--muted)'};"><span class="portfolio-ytd-total-value">₹${yearTotal.toFixed(0)}</span></td>`;
    html += `</tr>`;
  });

  html += `</tbody></table>`;
  container.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════
//  APPEARANCE CONTROLS
// ══════════════════════════════════════════════════════════════
const PF_APPEARANCE_FALLBACK = {
  default: { tint: 'native', font: 'forge' },
  tints: [{ id: 'native', label: 'PhilForge Default', swatch: 'swatch-native', native: true }],
  fonts: [{ id: 'forge', label: 'Forge Native', className: 'font-forge', sample: 'Aa' }],
};

function appearancePresetConfig() {
  const cfg = window.PHILFORGE_APPEARANCE_PRESETS || {};
  return {
    default: cfg.default || PF_APPEARANCE_FALLBACK.default,
    tints: Array.isArray(cfg.tints) && cfg.tints.length ? cfg.tints : PF_APPEARANCE_FALLBACK.tints,
    fonts: Array.isArray(cfg.fonts) && cfg.fonts.length ? cfg.fonts : PF_APPEARANCE_FALLBACK.fonts,
  };
}

function appearanceDefaults() {
  const defaults = appearancePresetConfig().default || PF_APPEARANCE_FALLBACK.default;
  return { tint: defaults.tint || 'native', font: defaults.font || 'forge' };
}

function appearancePresetLabel(type, id) {
  const list = type === 'font' ? appearancePresetConfig().fonts : appearancePresetConfig().tints;
  const preset = list.find(item => item.id === id);
  return (preset && preset.label) || id;
}

function renderAppearancePanelOptions() {
  const cfg = appearancePresetConfig();
  const tintWrap = document.getElementById('appearance-tint-options');
  if (tintWrap && !tintWrap.dataset.rendered) {
    tintWrap.innerHTML = cfg.tints.map(tint => {
      const swatchClass = tint.swatch || `swatch-${tint.id}`;
      return `<button class="appearance-option" data-appearance-tint="${escapeAttr(tint.id)}"><span class="appearance-swatch ${escapeAttr(swatchClass)}"></span><span>${escapeHtml(tint.label || tint.id)}</span></button>`;
    }).join('');
    tintWrap.dataset.rendered = '1';
  }
  const fontWrap = document.getElementById('appearance-font-options');
  if (fontWrap && !fontWrap.dataset.rendered) {
    fontWrap.innerHTML = cfg.fonts.map(font => {
      const className = font.className || `font-${font.id}`;
      return `<button class="appearance-option ${escapeAttr(className)}" data-appearance-font="${escapeAttr(font.id)}"><span class="appearance-font-sample">${escapeHtml(font.sample || 'Aa')}</span><span>${escapeHtml(font.label || font.id)}</span></button>`;
    }).join('');
    fontWrap.dataset.rendered = '1';
  }
}

function currentAppearance() {
  if (typeof window.pfGetAppearance === 'function') return window.pfGetAppearance();
  return appearanceDefaults();
}

function syncAppearancePanel() {
  renderAppearancePanelOptions();
  const state = currentAppearance();
  document.querySelectorAll('[data-appearance-tint]').forEach((btn) => {
    const active = btn.getAttribute('data-appearance-tint') === state.tint;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
  document.querySelectorAll('[data-appearance-font]').forEach((btn) => {
    const active = btn.getAttribute('data-appearance-font') === state.font;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function openAppearanceModal() {
  const modal = document.getElementById('appearance-modal');
  if (!modal) return;
  syncAppearancePanel();
  modal.classList.add('open');
  document.body.classList.add('appearance-open');
}

function closeAppearanceModal() {
  const modal = document.getElementById('appearance-modal');
  if (!modal) return;
  modal.classList.remove('open');
  document.body.classList.remove('appearance-open');
}

function setAppearanceTint(tint) {
  if (typeof window.pfApplyAppearance === 'function') window.pfApplyAppearance({ tint }, { persist: true });
  syncAppearancePanel();
  if (typeof toast === 'function') toast('Tint changed to ' + appearancePresetLabel('tint', tint), 'success');
}

function setAppearanceFont(font) {
  if (typeof window.pfApplyAppearance === 'function') window.pfApplyAppearance({ font }, { persist: true });
  syncAppearancePanel();
  if (typeof toast === 'function') toast('Font changed to ' + appearancePresetLabel('font', font), 'success');
}

function resetAppearance() {
  if (typeof window.pfApplyAppearance === 'function') window.pfApplyAppearance(appearanceDefaults(), { persist: true });
  syncAppearancePanel();
  if (typeof toast === 'function') toast('Appearance reset', 'info');
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeAppearanceModal();
});

// ══════════════════════════════════════════════════════════════
//  THEME TOGGLE
// ══════════════════════════════════════════════════════════════
function toggleTheme() {
  const next = (typeof window.pfToggleTheme === 'function')
    ? window.pfToggleTheme()
    : (document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light');
  if (typeof window.pfApplyTheme !== 'function') {
    document.documentElement.setAttribute('data-theme', next);
    try { _setLocalState('philforge_theme', next); } catch(e) {}
  }
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.innerHTML = next === 'light' ? ICO.moon(18) : ICO.sun(18);
  requestAnimationFrame(() => {
    if (lastBacktestData?.equity?.length > 1) renderEquityChart(lastBacktestData.equity);
  });
}
(function initTheme() {
  try {
    const saved = (typeof window.pfGetStoredTheme === 'function')
      ? window.pfGetStoredTheme()
      : _getLocalState('philforge_theme');
    const btn = document.getElementById('theme-toggle');
    if (saved === 'light') {
      if (typeof window.pfApplyTheme === 'function') window.pfApplyTheme('light');
      else document.documentElement.setAttribute('data-theme', 'light');
      if (btn) btn.innerHTML = ICO.moon(18);
    } else if (btn) {
      btn.innerHTML = ICO.sun(18);
    }
  } catch(e) {}
})();

// ══════════════════════════════════════════════════════════════
//  EMERGENCY KILL SWITCH
// ══════════════════════════════════════════════════════════════
async function emergencyStop() {
  const confirmed = await customConfirm(
    'This will <strong style="color:var(--danger)">immediately stop ALL</strong> running strategies: paper, auto, Scalp, Options Cascade, Candle Entry, Fib Boundary and Terminal Cascade. Broker exits are confirmed before live tracking is released.<br><br>If any exit is not confirmed, that engine will be left running so it does not lose tracking.',
    { title: ICO.stop(20) + ' EMERGENCY STOP', icon: ICO.siren(28), okText: 'KILL ALL', danger: true }
  );
  if (!confirmed) return;
  try {
    const res = await fetch('/api/emergency-stop', { method: 'POST' });
    const data = await res.json();
    toast((data.message || 'Emergency stop executed'), data.stopped > 0 ? 'warn' : 'success');
    updateKillSwitchVisibility();
    loadLiveMonitor();
    refreshScalpStatus();
  } catch(e) {
    toast('Failed to execute emergency stop', 'danger');
  }
}

// The floating kill switch is gone, so nothing polls /api/engine-control/status
// on a 10s timer any more. emergencyStop() below is kept and still allowlisted
// as a pf-action: re-adding one button restores the whole thing.
function updateKillSwitchVisibility() {}

// ══════════════════════════════════════════════════════════════
//  DASHBOARD SUMMARY
// ══════════════════════════════════════════════════════════════
function _dashMoney(value) {
  const num = Number(value || 0);
  return '₹' + num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _dashMoneyCr(value) {
  const num = Number(value || 0);
  return '₹' + num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' CR';
}

function _dashValueColor(value) {
  const num = Number(value || 0);
  return num > 0 ? 'var(--success)' : num < 0 ? 'var(--danger)' : 'var(--muted)';
}

function _isPageVisible() {
  return document.visibilityState === 'visible';
}

function _isPageActive(id) {
  return !!document.getElementById(id)?.classList.contains('active-page');
}

const _fiidiiRollupState = { open: false, page: 0 };
const _FIIDII_ROLLUP_PAGE_SIZE = 7;
let _lastFiiDiiRows = [];
let _portfolioMonthlyTradesOpen = false;

function toggleFiiDiiRollup() {
  _fiidiiRollupState.open = !_fiidiiRollupState.open;
  _renderFiiDiiRollup();
}

function changeFiiDiiRollupPage(delta) {
  const rows = Array.isArray(_lastFiiDiiRows) ? _lastFiiDiiRows : [];
  const totalPages = Math.max(1, Math.ceil(rows.length / _FIIDII_ROLLUP_PAGE_SIZE));
  _fiidiiRollupState.page = Math.max(0, Math.min(totalPages - 1, _fiidiiRollupState.page + delta));
  _renderFiiDiiRollup();
}

function _renderFiiDiiRollup() {
  const card = document.getElementById('dash-fiidii-rollup-card');
  const listEl = document.getElementById('dash-fiidii-rollup-list');
  const pageEl = document.getElementById('dash-fiidii-rollup-page');
  const prevBtn = document.getElementById('dash-fiidii-rollup-prev');
  const nextBtn = document.getElementById('dash-fiidii-rollup-next');
  const subEl = document.getElementById('dash-fiidii-rollup-sub');
  if (!card || !listEl || !pageEl || !prevBtn || !nextBtn || !subEl) return;

  const rows = Array.isArray(_lastFiiDiiRows) ? _lastFiiDiiRows : [];
  const totalPages = Math.max(1, Math.ceil(rows.length / _FIIDII_ROLLUP_PAGE_SIZE));
  _fiidiiRollupState.page = Math.max(0, Math.min(totalPages - 1, _fiidiiRollupState.page));
  card.classList.toggle('open', !!_fiidiiRollupState.open);

  if (!rows.length) {
    listEl.innerHTML = '<div class="dash-fiidii-rollup-row"><span class="dash-fiidii-rollup-date">No history yet</span><span class="dash-fiidii-rollup-value" style="color:var(--muted);">—</span><span class="dash-fiidii-rollup-value" style="color:var(--muted);">—</span></div>';
    subEl.textContent = '(Waiting for NSE history)';
    pageEl.textContent = 'Page 1 / 1';
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    return;
  }

  const start = _fiidiiRollupState.page * _FIIDII_ROLLUP_PAGE_SIZE;
  const pageRows = rows.slice(start, start + _FIIDII_ROLLUP_PAGE_SIZE);
  listEl.innerHTML = `
    <div class="dash-fiidii-rollup-grid-head">
      <span class="dash-fiidii-rollup-date">Day</span>
      <span class="dash-fiidii-rollup-col-label">FII</span>
      <span class="dash-fiidii-rollup-col-label">DII</span>
    </div>
    ${pageRows.map((row) => {
      const fiiValue = Number(row.fii_net || 0);
      const diiValue = Number(row.dii_net || 0);
      const label = row.display_date || row.date || '—';
      return `
        <div class="dash-fiidii-rollup-row">
          <span class="dash-fiidii-rollup-date">${escapeHtml(label)}</span>
          <span class="dash-fiidii-rollup-value" style="color:${_dashValueColor(fiiValue)};">${escapeHtml(_dashMoneyCr(fiiValue))}</span>
          <span class="dash-fiidii-rollup-value" style="color:${_dashValueColor(diiValue)};">${escapeHtml(_dashMoneyCr(diiValue))}</span>
        </div>
      `;
    }).join('')}
  `;
  subEl.textContent = `(${rows.length} sessions total)`;
  pageEl.textContent = `Page ${_fiidiiRollupState.page + 1} / ${totalPages}`;
  prevBtn.disabled = _fiidiiRollupState.page <= 0;
  nextBtn.disabled = _fiidiiRollupState.page >= totalPages - 1;
}

function togglePortfolioMonthlyTrades() {
  _portfolioMonthlyTradesOpen = !_portfolioMonthlyTradesOpen;
  renderMonthlyDailyGrid();
}

function _renderDashboardFiiDiiPanel(block) {
  const panel = document.getElementById('dash-fiidii-card');
  if (!panel) return;
  if (!block || block.status === 'unavailable') {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';
  document.getElementById('dash-fiidii-caption').textContent = (block.status === 'partial')
    ? 'Official NSE feed · building 30D history'
    : (block.source || 'Official NSE feed');
  document.getElementById('dash-fiidii-asof').textContent = block.as_of ? ('As of ' + block.as_of) : '—';

  const latest = block.latest || {};
  const latestFii = Number(latest.fii_net || 0);
  const latestDii = Number(latest.dii_net || 0);
  const rolling = block.rolling_30d || {};
  _lastFiiDiiRows = Array.isArray(block.rolling_daily) ? block.rolling_daily.slice() : [];
  const rollingFii = Number(rolling.fii_net || 0);
  const rollingDii = Number(rolling.dii_net || 0);

  const latestFiiEl = document.getElementById('dash-fiidii-latest-fii');
  latestFiiEl.textContent = _dashMoneyCr(latestFii);
  latestFiiEl.style.color = _dashValueColor(latestFii);

  const latestDiiEl = document.getElementById('dash-fiidii-latest-dii');
  latestDiiEl.textContent = _dashMoneyCr(latestDii);
  latestDiiEl.style.color = _dashValueColor(latestDii);

  const rollingFiiEl = document.getElementById('dash-fiidii-rolling-fii');
  rollingFiiEl.textContent = _dashMoneyCr(rollingFii);
  rollingFiiEl.style.color = _dashValueColor(rollingFii);

  const rollingDiiEl = document.getElementById('dash-fiidii-rolling-dii');
  rollingDiiEl.textContent = _dashMoneyCr(rollingDii);
  rollingDiiEl.style.color = _dashValueColor(rollingDii);

  _renderFiiDiiRollup();
}

async function loadDashboardSummary() {
  try {
    const res = await fetch('/api/dashboard/summary');
    if (await handleUnauthorizedResponse(res)) return;
    if (!res.ok) return;
    const d = await res.json();

    const paperFlow = d.paper_flow || {};
    const realFlow = d.real_flow || {};
    const paperStrategyFlow = d.paper_strategy_flow || {};
    const liveStrategyFlow = d.live_strategy_flow || {};
    const scalpFlow = d.scalp_flow || {};
    const paperPnl = Number(paperFlow.pnl ?? d.paper_total_pnl ?? d.paper_pnl ?? 0);
    const realPnl = Number(realFlow.pnl ?? d.real_total_pnl ?? d.real_pnl ?? 0);
    const paperTrades = Number(paperFlow.trades ?? d.paper_total_trades ?? d.paper_trades ?? 0);
    const realTrades = Number(realFlow.trades ?? d.real_total_trades ?? d.real_trades ?? 0);

    const ppEl = document.getElementById('dash-paper-pnl-card');
    ppEl.textContent = _dashMoney(paperPnl);
    ppEl.style.color = _dashValueColor(paperPnl);
    document.getElementById('dash-paper-pnl-sub').textContent = 'Strategy + SCALP';
    document.getElementById('dash-paper-pnl-trades').textContent = `${paperTrades} trades`;

    const rpEl = document.getElementById('dash-real-pnl-card');
    rpEl.textContent = _dashMoney(realPnl);
    rpEl.style.color = _dashValueColor(realPnl);
    document.getElementById('dash-real-pnl-sub').textContent = realFlow.source_label || d.real_source_label || 'Dhan today';
    document.getElementById('dash-real-pnl-trades').textContent = `${realTrades} trades${d.real_stale ? ' · Cached' : ''}`;

    // Active count
    document.getElementById('dash-active-count').textContent = d.active_count || 0;
    document.getElementById('dash-active-detail').textContent = d.active_detail || 'No strategies running';

    document.getElementById('dash-strategies-count').textContent = d.strategy_count || 0;
    document.getElementById('dash-backtests-count').textContent = (d.backtest_count || 0) + ' backtests';

    // Active engines panel
    const enginesPanel = document.getElementById('dash-active-engines');
    const activeOpenBtn = document.getElementById('dash-active-open-btn');
    if (paperStrategyFlow.active || liveStrategyFlow.active || scalpFlow.active) {
      enginesPanel.style.display = 'block';
      if (scalpFlow.active && !paperStrategyFlow.active && !liveStrategyFlow.active) {
        activeOpenBtn.textContent = 'Open Scalp →';
        activeOpenBtn.onclick = () => {
          showPage('scalp-page', document.getElementById('nav-scalp'));
          initScalpPage();
        };
      } else if (!liveStrategyFlow.active && paperStrategyFlow.active) {
        activeOpenBtn.textContent = 'Open Paper Monitor →';
        activeOpenBtn.onclick = () => {
          showPage('live-page', document.getElementById('nav-live'));
          startLiveMonitor();
        };
      } else {
        activeOpenBtn.textContent = 'Open Live Monitor →';
        activeOpenBtn.onclick = () => {
          showPage('live-page', document.getElementById('nav-live'));
          startLiveMonitor();
        };
      }

      if (paperStrategyFlow.active) {
        document.getElementById('dash-paper-card').style.display = 'block';
        document.getElementById('dash-paper-name').textContent = paperStrategyFlow.name || d.paper_strategy || 'Paper Strategy';
        document.getElementById('dash-paper-pnl').textContent = _dashMoney(paperStrategyFlow.pnl || d.paper_strategy_pnl || 0);
        document.getElementById('dash-paper-pnl').style.color = _dashValueColor(paperStrategyFlow.pnl || d.paper_strategy_pnl || 0);
        document.getElementById('dash-paper-trades').textContent = paperStrategyFlow.trades ?? d.paper_strategy_trades ?? 0;
        document.getElementById('dash-paper-note').textContent = 'Paper strategy only';
      } else {
        document.getElementById('dash-paper-card').style.display = 'none';
      }

      if (liveStrategyFlow.active) {
        const livePnl = Number(liveStrategyFlow.pnl ?? 0);
        document.getElementById('dash-live-card').style.display = 'block';
        document.getElementById('dash-live-name').textContent = liveStrategyFlow.name || d.live_strategy || 'Live Trades';
        document.getElementById('dash-live-pnl').textContent = _dashMoney(livePnl);
        document.getElementById('dash-live-pnl').style.color = _dashValueColor(livePnl);
        document.getElementById('dash-live-trades').textContent = liveStrategyFlow.trades ?? 0;
        document.getElementById('dash-live-note').textContent = `${liveStrategyFlow.source_label || d.real_source_label || 'Dhan today'} excluding SCALP`;
      } else {
        document.getElementById('dash-live-card').style.display = 'none';
      }

      if (scalpFlow.active) {
        const scalpPaperPnl = Number(scalpFlow.paper_pnl ?? d.paper_scalp_pnl ?? 0);
        const scalpRealPnl = Number(scalpFlow.real_pnl ?? d.real_scalp_pnl ?? 0);
        document.getElementById('dash-scalp-card').style.display = 'block';
        document.getElementById('dash-scalp-name').textContent = scalpFlow.name || d.scalp_strategy || 'SCALP';
        document.getElementById('dash-scalp-paper-pnl').textContent = _dashMoney(scalpPaperPnl);
        document.getElementById('dash-scalp-paper-pnl').style.color = _dashValueColor(scalpPaperPnl);
        document.getElementById('dash-scalp-paper-trades').textContent = `${scalpFlow.paper_trades ?? d.paper_scalp_trades ?? 0} trades`;
        document.getElementById('dash-scalp-real-pnl').textContent = _dashMoney(scalpRealPnl);
        document.getElementById('dash-scalp-real-pnl').style.color = _dashValueColor(scalpRealPnl);
        document.getElementById('dash-scalp-real-trades').textContent = `${scalpFlow.real_trades ?? d.real_scalp_trades ?? 0} trades`;
      } else {
        document.getElementById('dash-scalp-card').style.display = 'none';
      }
    } else {
      enginesPanel.style.display = 'none';
    }

    _renderDashboardFiiDiiPanel(d.fii_dii);

    // Best / Worst run
    if (d.best_run) {
      document.getElementById('dash-best-pnl').textContent = '₹' + (d.best_run.pnl || 0).toLocaleString('en-IN');
      document.getElementById('dash-best-name').textContent = d.best_run.name || 'Best Run';
      _bindDashboardLeaderboardCard(document.getElementById('dash-best-run'), d.best_run, 'Open best result');
    } else {
      _bindDashboardLeaderboardCard(document.getElementById('dash-best-run'), null, 'No best result yet');
    }
    if (d.worst_run) {
      document.getElementById('dash-worst-pnl').textContent = '₹' + (d.worst_run.pnl || 0).toLocaleString('en-IN');
      document.getElementById('dash-worst-name').textContent = d.worst_run.name || 'Worst Run';
      _bindDashboardLeaderboardCard(document.getElementById('dash-worst-run'), d.worst_run, 'Open worst result');
    } else {
      _bindDashboardLeaderboardCard(document.getElementById('dash-worst-run'), null, 'No worst result yet');
    }
    _dashboardTransactionsCache = Array.isArray(d.recent_transactions) ? d.recent_transactions : [];
    _renderDashboardTransactions(_dashboardTransactionsCache);
    if (Array.isArray(d.running_engines) && d.running_engines.length) {
      renderRunningArsenal(d.running_engines);
    } else {
      refreshStrategyArsenalRunning();
    }
  } catch(e) {
    console.warn('Dashboard summary failed:', e);
  }
}

// Load dashboard summary on page load and refresh every 30s
setTimeout(loadDashboardSummary, 500);
setInterval(() => {
  if (!_isPageVisible() || !_isPageActive('dashboard-page')) return;
  loadDashboardSummary();
}, 30000);
setTimeout(refreshLiveTabStatus, 500);
setInterval(() => {
  if (!_isPageVisible()) return;
  refreshLiveTabStatus();
}, 30000);

// ══════════════════════════════════════════════════════════════
//  TRADER'S EDGE — MOOD WIDGETS
// ══════════════════════════════════════════════════════════════
const _TRADING_MANTRAS = [
  { text: 'The market rewards patience and punishes impulse.', author: 'Trading Wisdom' },
  { text: 'Discipline is the bridge between goals and accomplishment.', author: 'Jim Rohn' },
  { text: 'Cut your losses short, let your winners run.', author: 'David Ricardo' },
  { text: 'The goal of a successful trader is to make the best trades. Money is secondary.', author: 'Alexander Elder' },
  { text: 'Risk comes from not knowing what you are doing.', author: 'Warren Buffett' },
  { text: 'It is not the strongest that survive, nor the most intelligent, but the most responsive to change.', author: 'Charles Darwin' },
  { text: 'Plan your trade and trade your plan.', author: 'Trader\'s Creed' },
  { text: 'The trend is your friend until the end when it bends.', author: 'Ed Seykota' },
  { text: 'In trading, the impossible happens about twice a year.', author: 'Henri M. Simoes' },
  { text: 'Markets can remain irrational longer than you can remain solvent.', author: 'John Maynard Keynes' },
  { text: 'Every battle is won before it is fought.', author: 'Sun Tzu' },
  { text: 'Amateurs think about how much money they can make. Professionals think about how much money they could lose.', author: 'Jack Schwager' },
  { text: 'The stock market is a device for transferring money from the impatient to the patient.', author: 'Warren Buffett' },
  { text: 'One good trade is worth more than a hundred forced ones.', author: 'Trading Wisdom' },
  { text: 'Protect your capital. Everything else follows.', author: 'Trading Wisdom' },
];

function initMoodWidgets() {
  // Warrior's Creed — deterministic pick based on date so it stays the same all day
  const today = new Date();
  const dayIdx = (today.getFullYear() * 366 + today.getMonth() * 31 + today.getDate()) % _TRADING_MANTRAS.length;
  const mantra = _TRADING_MANTRAS[dayIdx];
  const mantraEl = document.getElementById('daily-mantra');
  const authorEl = document.getElementById('daily-mantra-author');
  if (mantraEl) mantraEl.innerHTML = '&ldquo;' + mantra.text + '&rdquo;';
  if (authorEl) authorEl.textContent = '— ' + mantra.author;

  // Win Streak & Confidence — computed from runs.json data
  updateMoodFromRuns();
}

function updateMoodFromRuns() {
  try {
    if (!Array.isArray(_allRunsCache) || !_allRunsCache.length) return;

    // _allRunsCache is already sorted newest-first
    const sorted = _allRunsCache;

    // Calculate win streak (consecutive profitable runs from most recent)
    let streak = 0;
    for (const r of sorted) {
      if ((r.total_pnl || 0) > 0) streak++;
      else break;
    }

    // Win rate from last 20 runs
    const recent = sorted.slice(0, 20);
    const wins = recent.filter(r => (r.total_pnl || 0) > 0).length;
    const winRate = recent.length > 0 ? Math.round((wins / recent.length) * 100) : 0;

    // Confidence = weighted combination: 60% win rate + 40% streak bonus (capped at 100)
    const streakBonus = Math.min(streak * 12, 100);
    const confidence = Math.min(100, Math.round(winRate * 0.6 + streakBonus * 0.4));

    // Update Win Streak
    const streakEl = document.getElementById('win-streak-count');
    const fireEl = document.getElementById('win-streak-fire');
    const dotsEl = document.getElementById('win-streak-dots');
    if (streakEl) {
      streakEl.textContent = streak;
      streakEl.style.color = streak >= 5 ? 'var(--success)' : streak >= 3 ? 'var(--warn)' : 'var(--muted)';
    }
    if (fireEl) {
      const suffix = streak === 1 ? 'win in a row' : 'wins in a row';
      fireEl.textContent = streak > 0 ? suffix + (streak >= 5 ? ' 🔥' : streak >= 3 ? ' ⚡' : '') : 'Start your streak!';
    }
    if (dotsEl) {
      // Show last 10 results as W/L dots
      const last10 = sorted.slice(0, 10).reverse();
      dotsEl.innerHTML = last10.map(r => {
        const w = (r.total_pnl || 0) > 0;
        return '<span style="width:8px;height:8px;border-radius:50%;background:' + (w ? 'var(--success)' : 'rgba(239,68,68,0.4)') + ';display:inline-block;" title="' + escapeAttr((r.run_name || 'Run') + ': ₹' + (r.total_pnl || 0).toFixed(0)) + '"></span>';
      }).join('');
    }

    // Update Confidence Meter
    const barEl = document.getElementById('confidence-bar');
    const valEl = document.getElementById('confidence-val');
    const labelEl = document.getElementById('confidence-label');
    if (barEl) {
      barEl.style.width = confidence + '%';
      if (confidence >= 70) barEl.style.background = 'linear-gradient(90deg,var(--success),var(--green))';
      else if (confidence >= 40) barEl.style.background = 'linear-gradient(90deg,var(--warn),var(--warn))';
      else barEl.style.background = 'linear-gradient(90deg,var(--danger),var(--red))';
    }
    if (valEl) {
      valEl.textContent = confidence + '%';
      valEl.style.color = confidence >= 70 ? 'var(--success)' : confidence >= 40 ? 'var(--warn)' : 'var(--danger)';
    }
    if (labelEl) {
      if (confidence >= 80) labelEl.textContent = 'You\'re on fire — trust your setups';
      else if (confidence >= 60) labelEl.textContent = 'Solid form — stay disciplined';
      else if (confidence >= 40) labelEl.textContent = 'Steady — review your recent trades';
      else labelEl.textContent = 'Reset & refocus — smaller size today';
    }
  } catch(e) {
    console.warn('Mood widgets update failed:', e);
  }
}

setTimeout(initMoodWidgets, 600);

// ══════════════════════════════════════════════════════════════
//  BACKTEST COMPARISON
// ══════════════════════════════════════════════════════════════
function populateCompareDropdowns() {
  try {
    const runs = JSON.parse(localStorage.getItem('_comparison_runs') || '[]');
    if (runs.length === 0) return;
    const selA = document.getElementById('compare-run-a');
    const selB = document.getElementById('compare-run-b');
    if (!selA || !selB) return;
    selA.innerHTML = '<option value="">Select Run A</option>';
    selB.innerHTML = '<option value="">Select Run B</option>';
    runs.forEach(r => {
      const opt = `<option value="${r.id}">${escapeHtml(r.name)} (₹${escapeHtml(r.pnl)})</option>`;
      selA.innerHTML += opt;
      selB.innerHTML += opt;
    });
  } catch(e) {}
}

function compareBacktests() {
  const idA = document.getElementById('compare-run-a').value;
  const idB = document.getElementById('compare-run-b').value;
  if (!idA || !idB) { toast('Select both Run A and Run B to compare', 'warn'); return; }
  if (idA === idB) { toast('Select two different runs to compare', 'warn'); return; }

  fetch('/api/runs').then(r => r.json()).then(runs => {
    const runA = runs.find(r => String(r.id) === String(idA));
    const runB = runs.find(r => String(r.id) === String(idB));
    if (!runA || !runB) { toast('Could not find selected runs', 'danger'); return; }

    const grid = document.getElementById('comparison-grid');
    grid.innerHTML = '';

    function _cmpMetric(label, valA, valB, opts = {}) {
      const colorFn = opts.colorFn || (() => 'var(--text)');
      const fmtFn = opts.fmtFn || (v => v);
      const highlight = opts.higherBetter !== undefined;
      let bgA = '', bgB = '';
      if (highlight && valA !== valB) {
        const aWins = opts.higherBetter ? valA > valB : valA < valB;
        bgA = aWins ? 'background:rgba(34,197,94,0.06);' : '';
        bgB = !aWins ? 'background:rgba(34,197,94,0.06);' : '';
      }
      return `<tr style="border-bottom:1px solid var(--border);">
        <td style="padding:8px 10px;font-size:12px;color:var(--muted);font-weight:600;white-space:nowrap;">${label}</td>
        <td style="padding:8px 10px;font-weight:700;font-family:'JetBrains Mono',monospace;font-size:13px;color:${colorFn(valA)};text-align:right;${bgA}">${fmtFn(valA)}</td>
        <td style="padding:8px 10px;font-weight:700;font-family:'JetBrains Mono',monospace;font-size:13px;color:${colorFn(valB)};text-align:right;${bgB}">${fmtFn(valB)}</td>
      </tr>`;
    }

    const sA = runA.stats || {}, sB = runB.stats || {};
    const pnlA = sA.total_pnl || 0, pnlB = sB.total_pnl || 0;
    const pnlColor = v => v >= 0 ? 'var(--success)' : 'var(--danger)';
    const pctColor = v => v >= 50 ? 'var(--success)' : 'var(--danger)';
    const pfColor = v => v >= 1.5 ? 'var(--success)' : (v >= 1 ? 'var(--warn)' : 'var(--danger)');
    const srColor = v => v >= 1.5 ? 'var(--success)' : (v >= 0.5 ? 'var(--warn)' : 'var(--danger)');

    let tableHtml = `<table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="border-bottom:2px solid var(--border);">
        <th style="padding:10px;text-align:left;color:var(--muted);font-size:11px;text-transform:uppercase;">Metric</th>
        <th style="padding:10px;text-align:right;color:var(--accent);font-size:12px;font-weight:700;">${escapeHtml(runA.run_name || 'Run A')}</th>
        <th style="padding:10px;text-align:right;color:var(--accent2);font-size:12px;font-weight:700;">${escapeHtml(runB.run_name || 'Run B')}</th>
      </tr></thead><tbody>`;
    tableHtml += _cmpMetric('Total P&L', pnlA, pnlB, { fmtFn: fmt, colorFn: pnlColor, higherBetter: true });
    tableHtml += _cmpMetric('Total Trades', sA.total_trades||0, sB.total_trades||0, { fmtFn: v => v });
    tableHtml += _cmpMetric('Win Rate', sA.win_rate||0, sB.win_rate||0, { fmtFn: v => v.toFixed(2) + '%', colorFn: pctColor, higherBetter: true });
    tableHtml += _cmpMetric('Winning', sA.winning_trades||0, sB.winning_trades||0, { fmtFn: v => v, higherBetter: true });
    tableHtml += _cmpMetric('Losing', sA.losing_trades||0, sB.losing_trades||0, { fmtFn: v => v, higherBetter: false });
    tableHtml += _cmpMetric('Profit Factor', sA.profit_factor||0, sB.profit_factor||0, { fmtFn: v => Number(v).toFixed(2), colorFn: pfColor, higherBetter: true });
    tableHtml += _cmpMetric('Sharpe Ratio', sA.sharpe_ratio||0, sB.sharpe_ratio||0, { fmtFn: v => Number(v).toFixed(2), colorFn: srColor, higherBetter: true });
    tableHtml += _cmpMetric('Calmar Ratio', sA.calmar_ratio||0, sB.calmar_ratio||0, { fmtFn: v => Number(v).toFixed(2), higherBetter: true });
    tableHtml += _cmpMetric('Max Drawdown', sA.max_drawdown_val||0, sB.max_drawdown_val||0, { fmtFn: fmt, colorFn: () => 'var(--danger)', higherBetter: false });
    tableHtml += _cmpMetric('Drawdown Days', sA.max_drawdown_days||0, sB.max_drawdown_days||0, { fmtFn: v => v + ' days', higherBetter: false });
    tableHtml += _cmpMetric('Risk Per Trade', sA.risk_per_trade||0, sB.risk_per_trade||0, { fmtFn: fmt });
    tableHtml += _cmpMetric('Avg Profit', sA.avg_profit||0, sB.avg_profit||0, { fmtFn: fmt, colorFn: () => 'var(--success)', higherBetter: true });
    tableHtml += _cmpMetric('Avg Loss', sA.avg_loss||0, sB.avg_loss||0, { fmtFn: fmt, colorFn: () => 'var(--danger)' });
    tableHtml += _cmpMetric('Win Streak', sA.win_streak||0, sB.win_streak||0, { fmtFn: v => v, higherBetter: true });
    tableHtml += _cmpMetric('Loss Streak', sA.loss_streak||0, sB.loss_streak||0, { fmtFn: v => v, higherBetter: false });
    tableHtml += _cmpMetric('Max Profit', sA.max_profit||0, sB.max_profit||0, { fmtFn: fmt, colorFn: () => 'var(--success)', higherBetter: true });
    tableHtml += _cmpMetric('Max Loss', sA.max_loss||0, sB.max_loss||0, { fmtFn: fmt, colorFn: () => 'var(--danger)' });
    tableHtml += _cmpMetric('Expectancy', sA.expectancy||0, sB.expectancy||0, { fmtFn: fmt, colorFn: pnlColor, higherBetter: true });
    tableHtml += _cmpMetric('ROI %', sA.roi_pct||0, sB.roi_pct||0, { fmtFn: v => Number(v).toFixed(2) + '%', colorFn: pnlColor, higherBetter: true });
    tableHtml += _cmpMetric('Avg Duration', sA.avg_duration||'-', sB.avg_duration||'-', { fmtFn: v => v });
    tableHtml += _cmpMetric('Total Fees', sA.total_fees||0, sB.total_fees||0, { fmtFn: fmt, colorFn: () => 'var(--warn)' });
    tableHtml += '</tbody></table>';
    grid.innerHTML = tableHtml;

    document.getElementById('comparison-result').style.display = 'block';
    document.getElementById('comparison-empty').style.display = 'none';
  }).catch(e => toast('Failed to load runs for comparison', 'danger'));
}

// ══════════════════════════════════════════════════════════════
//  WEBSOCKET INTEGRATION
// ══════════════════════════════════════════════════════════════
let _ws = null;
let _wsReconnectTimer = null;
const _wsDecoder = new TextDecoder();

// ── Exponential backoff state ──
let _wsBackoff = 1000;          // start at 1s
const _WS_BACKOFF_MAX = 16000;  // cap at 16s
const _WS_BACKOFF_BASE = 1000;

// ── Heartbeat / staleness tracking ──
let _wsLastMsgAt = 0;           // Date.now() of last received message
const _WS_STALE_MS = 2500;     // 2.5s = 10 missed 250ms cycles
const _WS_ZOMBIE_MS = 7000;    // 7s = kill zombie TCP, force reconnect
let _wsStale = false;
let _wsHeartbeatTimer = null;

function _wsSetLiveIndicator(connected, stale) {
  const dot = document.getElementById('ws-status-dot');
  const label = document.getElementById('ws-status-label');
  if (!dot || !label) return;
  const scalpRunning = !!(_lastScalpStatus && _lastScalpStatus.running);
  if (!connected) {
    dot.style.background = 'var(--red)';
    dot.style.animation = 'none';
    label.textContent = 'Disconnected';
    label.style.color = 'var(--red)';
  } else if (stale) {
    dot.style.background = '#f59e0b';
    dot.style.animation = 'none';
    label.textContent = 'Stale';
    label.style.color = '#f59e0b';
  } else if (!scalpRunning) {
    dot.style.background = '#06b6d4';
    dot.style.animation = 'none';
    label.textContent = 'Feed Ready';
    label.style.color = '#0891b2';
  } else {
    dot.style.background = 'var(--green)';
    dot.style.animation = 'livePulse 2s infinite';
    label.textContent = 'Feed Live';
    label.style.color = 'var(--green)';
  }
}

function _wsStartHeartbeat() {
  if (_wsHeartbeatTimer) return;
  _wsHeartbeatTimer = setInterval(() => {
    const connected = _ws && _ws.readyState === 1;
    if (!connected) {
      if (!_wsStale) { _wsStale = true; _wsSetLiveIndicator(false, false); }
      return;
    }
    const gap = Date.now() - _wsLastMsgAt;
    // Zombie killer: TCP alive but no data for 7s → force close → triggers reconnect
    if (gap > _WS_ZOMBIE_MS) {
      console.error('[WS] Zombie detected (' + gap + 'ms silence) — killing connection');
      _ws.close(4000, 'Zombie kill');
      return;
    }
    if (gap > _WS_STALE_MS && !_wsStale) {
      _wsStale = true;
      _wsSetLiveIndicator(true, true);
      console.warn('[WS] Data stale — no message for ' + gap + 'ms');
    } else if (gap <= _WS_STALE_MS && _wsStale) {
      _wsStale = false;
      _wsSetLiveIndicator(true, false);
    }
  }, 1000);
}

function _wsStopHeartbeat() {
  if (_wsHeartbeatTimer) { clearInterval(_wsHeartbeatTimer); _wsHeartbeatTimer = null; }
}

function connectWebSocket() {
  if (_ws && _ws.readyState <= 1) return; // already open or connecting
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${proto}//${location.host}/ws`;
  try {
    _ws = new WebSocket(wsUrl);
    _ws.binaryType = 'arraybuffer';

    _ws.onopen = () => {
      console.log('[WS] Connected');
      _wsBackoff = _WS_BACKOFF_BASE;  // reset backoff on success
      _wsStale = false;
      _wsLastMsgAt = Date.now();
      _wsSetLiveIndicator(true, false);
      _wsStartHeartbeat();
    };

    _ws.onmessage = (evt) => {
      _wsLastMsgAt = Date.now();
      // Clear stale state on first message after gap
      if (_wsStale) { _wsStale = false; _wsSetLiveIndicator(true, false); }
      try {
        // Robust: handle both binary (orjson bytes) and plain text (stdlib json)
        let raw;
        if (evt.data instanceof ArrayBuffer) {
          raw = _wsDecoder.decode(evt.data);
        } else if (evt.data instanceof Blob) {
          evt.data.text().then(t => { try { handleWSMessage(JSON.parse(t)); } catch(_){} });
          return;
        } else {
          raw = evt.data;
        }
        handleWSMessage(JSON.parse(raw));
      } catch(e) {}
    };

    _ws.onclose = (evt) => {
      _ws = null;
      _wsStopHeartbeat();
      _wsSetLiveIndicator(false, false);
      console.warn('[WS] Closed (code=' + evt.code + ') — reconnecting in ' + _wsBackoff + 'ms');
      clearTimeout(_wsReconnectTimer);
      _wsReconnectTimer = setTimeout(connectWebSocket, _wsBackoff);
      _wsBackoff = Math.min(_wsBackoff * 2, _WS_BACKOFF_MAX);  // exponential backoff
    };

    _ws.onerror = () => {
      // onclose will fire after onerror — reconnect happens there
      if (_ws) { _ws.close(); }
    };
  } catch(e) {
    console.warn('[WS] Connection failed:', e);
    _wsSetLiveIndicator(false, false);
    clearTimeout(_wsReconnectTimer);
    _wsReconnectTimer = setTimeout(connectWebSocket, _wsBackoff);
    _wsBackoff = Math.min(_wsBackoff * 2, _WS_BACKOFF_MAX);
  }
}

// ── Cached DOM refs for scalp WS updates (Phase 2) ──
const _scalpDomCache = {};
function _getScalpEl(id) {
  if (!_scalpDomCache[id] || !_scalpDomCache[id].isConnected) {
    _scalpDomCache[id] = document.getElementById(id);
  }
  return _scalpDomCache[id];
}

let _scalpRafPending = false;
let _pendingScalpData = null;
let _pendingScalpTs = 0;

function handleWSMessage(msg) {
  // msg format: { type: 'trade'|'status'|'pnl', data: {...} }
  if (msg.type === 'trade' || msg.event === 'trade') {
    const d = msg.data || msg;
    sendTradeNotification(d);
    loadDashboardSummary();
  }
  // Engine status fields only arrive every ~5s — update kill switch when present
  if (msg.type === 'status' && (msg.paper_engines || msg.live_engines)) {
    updateKillSwitchVisibility();
  }
  if (msg.event === 'engine_status') {
    updateKillSwitchVisibility();
  }
  // Scalp status pushed from WS every 250ms — skip HTTP poll
  if (msg.type === 'status' && msg.scalp) {
    _lastScalpStatus = msg.scalp;
    _pendingScalpData = msg.scalp;
    _pendingScalpTs = msg._ts || 0;
    if (!_scalpRafPending) {
      _scalpRafPending = true;
      window.requestAnimationFrame(_flushScalpRender);
    }
  }
  // Options Cascade updates are per-user paper-campaign snapshots.  They are
  // rendered directly so the monitor does not have to wait for its 12s poll.
  const cascadeCampaign = msg.type === 'cascade_status'
    ? msg.cascade
    : (msg.type === 'status' ? msg.cascade : null);
  if (cascadeCampaign) {
    _renderCascadeOptionsStatus({
      status: 'ok',
      mode: 'paper',
      live_gate: _lastCascadeOptionsStatus?.live_gate,
      campaign: cascadeCampaign,
    });
  }
  // The fib-space run pushes a full status payload, not just its book, so the
  // panel can render a push and a poll identically.
  const fibSpaceStatus = msg.type === 'fib_space_status'
    ? msg.fib_space
    : (msg.type === 'status' ? msg.fib_space : null);
  if (fibSpaceStatus) _renderFibSpaceStatus(fibSpaceStatus);
  const terminalCascadeCampaign = msg.type === 'terminal_cascade_status'
    ? msg.terminal_cascade
    : (msg.type === 'status' ? msg.terminal_cascade : null);
  if (terminalCascadeCampaign) {
    _renderTerminalCascadeStatus({
      status: 'ok',
      mode: 'paper',
      live_gate: _lastTerminalCascadeStatus?.live_gate,
      campaigns: terminalCascadeCampaign.campaigns || [terminalCascadeCampaign],
    });
  }
}

function _flushScalpRender() {
  _scalpRafPending = false;
  if (_pendingScalpData) {
    // LATENCY DEBUG — remove after verification
    if (_pendingScalpTs) console.log('[WS→Paint] ' + (Date.now() - _pendingScalpTs * 1000).toFixed(0) + 'ms');
    _renderScalpStatusWS(_pendingScalpData);
    _pendingScalpData = null;
    _pendingScalpTs = 0;
  }
}

// Lightweight WS-driven scalp render — only updates dynamic cells via cached refs
function _renderScalpStatusWS(data) {
  _applyScalpEngineState(!!data.running, _getScalpEl);
  _wsSetLiveIndicator(_ws && _ws.readyState === 1, _wsStale);

  // Session P&L
  const sessionPnl = _getScalpEl('scalp-session-pnl');
  if (sessionPnl) {
    const pnl = Number(data.session_pnl ?? data.total_pnl ?? 0);
    sessionPnl.textContent = '₹' + pnl.toFixed(2);
    sessionPnl.style.color = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--muted)';
  }

  // Active positions — granular update via cached refs
  const tbody = _getScalpEl('scalp-active-body');
  if (!tbody) return;
  const open = data.open_trades || [];
  const pendingCount = open.filter(t => t.status === 'pending').length;
  const activeCount = open.length - pendingCount;
  const countEl = _getScalpEl('scalp-open-count');
  if (countEl) countEl.textContent = activeCount + ' open' + (pendingCount ? ` · ${pendingCount} pending` : '');
  const killBtn = _getScalpEl('scalp-kill-all-btn');
  if (killBtn) killBtn.style.display = open.length > 0 ? '' : 'none';

  const serverTids = new Set(open.map(t => t.trade_id));
  const domTids = new Set();
  tbody.querySelectorAll('tr[data-tid]').forEach(tr => domTids.add(parseInt(tr.dataset.tid)));
  let sameSet = serverTids.size === domTids.size && [...serverTids].every(id => domTids.has(id));
  if (sameSet) {
    for (const t of open) {
      const row = tbody.querySelector(`tr[data-tid="${t.trade_id}"]`);
      if (row && row.dataset.status !== t.status) { sameSet = false; break; }
    }
  }

  if (!open.length) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:20px;color:var(--muted);">No active positions</td></tr>';
    // Invalidate cached refs for removed trades
    for (const tid of domTids) { delete _scalpDomCache['scalp-ltp-'+tid]; delete _scalpDomCache['scalp-pnl-'+tid]; delete _scalpDomCache['scalp-entry-'+tid]; }
  } else if (!sameSet) {
    // Trade set changed — full rebuild, invalidate cache
    tbody.innerHTML = open.map(t => _buildScalpActiveRow(t)).join('');
    for (const tid of domTids) { delete _scalpDomCache['scalp-ltp-'+tid]; delete _scalpDomCache['scalp-pnl-'+tid]; delete _scalpDomCache['scalp-entry-'+tid]; }
  } else {
    // Same trades — granular .textContent updates only
    open.forEach(t => {
      const ltpEl = _getScalpEl('scalp-ltp-' + t.trade_id);
      const pnlEl = _getScalpEl('scalp-pnl-' + t.trade_id);
      const entryEl = _getScalpEl('scalp-entry-' + t.trade_id);
      if (ltpEl) ltpEl.textContent = '₹' + (t.current_premium||0).toFixed(2);
      if (pnlEl) {
        const pnl = t.pnl || 0;
        pnlEl.textContent = (pnl>=0?'+':'') + '₹' + pnl.toFixed(2);
        pnlEl.style.color = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--muted)';
      }
      if (entryEl) entryEl.textContent = '₹' + (t.entry_premium||0).toFixed(2);
      const tgtInput = _getScalpEl('scalp-tgt-' + t.trade_id);
      const slInput = _getScalpEl('scalp-sl-' + t.trade_id);
      const entryMinInput = _getScalpEl('scalp-entry-min-' + t.trade_id);
      const entryMaxInput = _getScalpEl('scalp-entry-max-' + t.trade_id);
      if (tgtInput && document.activeElement !== tgtInput && !_isScalpInputDirty('scalp-tgt-' + t.trade_id)) tgtInput.value = t.target_premium || 0;
      if (slInput && document.activeElement !== slInput && !_isScalpInputDirty('scalp-sl-' + t.trade_id)) slInput.value = t.sl_premium || 0;
      if (entryMinInput && document.activeElement !== entryMinInput && !_isScalpInputDirty('scalp-entry-min-' + t.trade_id)) entryMinInput.value = (t.entry_limit_price || 0).toFixed(2);
      if (entryMaxInput && document.activeElement !== entryMaxInput && !_isScalpInputDirty('scalp-entry-max-' + t.trade_id)) entryMaxInput.value = (t.entry_limit_max || 0).toFixed(2);
    });
  }

  // ── Surgical Live Premium update from WS open trade data ──
  // Piggyback on the 250ms WS push: if an open trade matches the form's
  // instrument, update the Live Premium display with zero extra latency.
  _updateLivePremiumFromWS(open);
}

/**
 * Surgical DOM update for the Live Premium display.
 * Reads instrument from form fields, finds matching open trade,
 * writes directly to #scalp-live-ltp — no React, no re-render, no REST call.
 */
function _updateLivePremiumFromWS(openTrades) {
  if (!openTrades || !openTrades.length) return;
  const underlying = document.getElementById('scalp-underlying')?.value;
  const strike = parseInt(document.getElementById('scalp-strike')?.value) || 0;
  const optType = document.getElementById('scalp-option-type')?.value;
  const expiry = document.getElementById('scalp-expiry')?.value;
  if (!strike || !expiry) return;

  const match = openTrades.find(t =>
    t.underlying === underlying && t.strike === strike &&
    t.option_type === optType && t.expiry === expiry &&
    t.current_premium > 0
  );
  if (match) {
    const el = _getScalpEl('scalp-live-ltp');
    if (el) {
      el.textContent = '₹' + match.current_premium.toFixed(2);
      el.style.color = 'var(--green)';
    }
    _scalpCurrentLTP = match.current_premium;
    _scalpLTPFromWS = Date.now();  // mark as fresh — suppresses REST fallback
    updateScalpMargin();
  }
}

// Connect WebSocket on load
setTimeout(connectWebSocket, 1000);

// ── Page Visibility: instant reconnect when trader returns to tab ──
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  try { updateTicker(); } catch(e) {}
  if (_isPageActive('dashboard-page')) {
    try { loadDashboardSummary(); } catch(e) {}
  }
  if (_isPageActive('builder-page')) {
    try { refreshBuilderPreview(); } catch(e) {}
  }
  if (_isPageActive('live-page')) {
    try { loadLiveMonitor(); } catch(e) {}
  }
  if (_isPageActive('scalp-page')) {
    try { refreshScalpStatus(); } catch(e) {}
  }
  if (_isPageActive('options-cascade-page')) {
    try { refreshFibBoundaryStatus(); } catch(e) {}
  }
  // Tab is back in focus — check WebSocket health immediately
  const alive = _ws && _ws.readyState === 1;
  if (!alive) {
    // Dead or absent — skip backoff timer, reconnect now
    console.log('[WS] Tab visible — forcing immediate reconnect');
    clearTimeout(_wsReconnectTimer);
    _wsBackoff = _WS_BACKOFF_BASE;  // reset backoff since this is user-initiated
    connectWebSocket();
  } else if (_wsLastMsgAt && (Date.now() - _wsLastMsgAt > _WS_STALE_MS)) {
    // Open but stale — zombie kill, reconnect will follow via onclose
    console.warn('[WS] Tab visible — connection stale, killing zombie');
    _ws.close(4001, 'Visibility zombie kill');
  }
});

// ══════════════════════════════════════════════════════════════
//  BROWSER NOTIFICATIONS
// ══════════════════════════════════════════════════════════════
let _notifPermission = 'default';

function requestNotificationPermission() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'granted') { _notifPermission = 'granted'; return; }
  if (Notification.permission !== 'denied') {
    Notification.requestPermission().then(perm => { _notifPermission = perm; });
  }
}

function sendTradeNotification(trade) {
  if (_notifPermission !== 'granted' && Notification.permission !== 'granted') return;
  try {
    const pnl = trade.pnl || trade.profit || 0;
    const icon = pnl >= 0 ? '▲' : '▼';
    const title = `${icon} PhilForge Trade`;
    const body = `${trade.instrument || trade.symbol || 'Trade'}: ${trade.action || trade.type || ''} ₹${Math.abs(pnl).toFixed(2)}`;
    new Notification(title, { body, icon: '/static/logo.png', tag: 'philforge-trade-' + Date.now() });
  } catch(e) {}
}

// Request permission on load
setTimeout(requestNotificationPermission, 3000);

// ══════════════════════════════════════════════════════════════
//  ENHANCED DEPLOY — VALIDATION BEFORE DEPLOY
// ══════════════════════════════════════════════════════════════
const _originalDeployStrategy = deployStrategy;
deployStrategy = async function() {
  const isPaper = document.getElementById('deploy-paper-btn').dataset.active === '1';
  const deployRunName = document.getElementById('deploy-run-name').value.trim();
  if (!deployRunName) { toast('Enter a run name', 'warn'); return; }

  // Validate strategy first
  try {
    const payload = buildPayload();
    payload.run_name = deployRunName;
    const valRes = await fetch('/api/validate-strategy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const valData = await valRes.json();

    if (valData.errors && valData.errors.length > 0) {
      const errList = valData.errors.map(e => `${ICO.cross(13)} ${e}`).join('<br>');
      toast('Strategy has errors — fix before deploying', 'danger');
      await customConfirm(
        `<div style="text-align:left;font-size:12px;line-height:1.8;">${errList}</div>`,
        { title: ICO.cross(20) + ' Strategy Validation Failed', icon: ICO.ban(28), okText: 'OK', cancelText: 'Close' }
      );
      return;
    }

    // Show warnings but allow proceeding
    if (valData.warnings && valData.warnings.length > 0) {
      const warnList = valData.warnings.map(w => `${ICO.warn(13)} ${w}`).join('<br>');
      const proceed = await customConfirm(
        `<div style="text-align:left;font-size:12px;line-height:1.8;">${warnList}</div><br><span style="font-size:12px;color:var(--muted);">Do you want to proceed anyway?</span>`,
        { title: ICO.warn(20) + ' Strategy Warnings', icon: ICO.warn(28), okText: 'Deploy Anyway', cancelText: 'Go Back' }
      );
      if (!proceed) return;
    }
  } catch(e) {
    console.warn('Validation check failed, proceeding anyway:', e);
  }

  // Call original deploy logic
  return _originalDeployStrategy();
};

// ══════════════════════════════════════════════════════════════
//  UPDATE COMPARE DROPDOWNS WHEN RUNS CHANGE
// ══════════════════════════════════════════════════════════════
const _origFetchRuns = fetchRuns;
fetchRuns = async function() {
  await _origFetchRuns();
  // After runs are loaded, update comparison dropdowns
  try {
    const res = await fetch('/api/runs');
    const runs = await res.json();
    const selA = document.getElementById('compare-run-a');
    const selB = document.getElementById('compare-run-b');
    if (!selA || !selB) return;
    selA.innerHTML = '<option value="">Select Run A</option>';
    selB.innerHTML = '<option value="">Select Run B</option>';
    runs.forEach(r => {
      const name = r.run_name || r.name || 'Run #' + r.id;
      const pnl = r.total_pnl || (r.stats ? (r.stats.total_pnl || 0) : 0);
      const opt = `<option value="${r.id}">${name} (₹${Math.round(pnl).toLocaleString('en-IN')})</option>`;
      selA.innerHTML += opt;
      selB.innerHTML += opt;
    });
  } catch(e) {}
  // Refresh mood widgets with latest runs data
  try { updateMoodFromRuns(); } catch(e) {}
};

// ══════════════════════════════════════════════════════════════
//  CHART VIEWER + DAILY JOURNAL TAB
// ══════════════════════════════════════════════════════════════
(function() {
  let _chTree = {};
  let _chImages = [];
  let _chDateLabel = '';
  let _chLBIdx = 0;
  let _chLoaded = false;
  let _cjCurrentDate = '';   // YYYY-MM-DD of selected day
  let _cjSaveTimer = null;
  let _cjLoadController = null;
  let _cjLoadGeneration = 0;
  const _cjSaveChains = new Map();
  let _cjCurrentDayMeta = null;  // {year, monthFolder, dayFolder}
  let _cjPanelMode = 'journal';
  let _cjPlanTimer = null;
  let _cjPlanner = null;
  const _cjPlanMonths = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function _chTodayIso() {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(new Date());
    const map = {};
    parts.forEach((part) => {
      if (part.type !== 'literal') map[part.type] = part.value;
    });
    return `${map.year}-${map.month}-${map.day}`;
  }

  function _chRenderEmptyState() {
    const area = document.getElementById('ch-content');
    if (!area) return;
    const phIcon = document.getElementById('ch-placeholder-icon');
    if (phIcon && !phIcon.innerHTML) phIcon.innerHTML = ICO.candle(52);
    area.innerHTML = `
      <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:400px;text-align:center;color:var(--muted);">
        <div style="margin-bottom:16px;opacity:0.5;" id="ch-placeholder-icon">${ICO.candle(52)}</div>
        <h3 style="font-family:'Syne',sans-serif;font-size:18px;color:var(--text-dim);margin-bottom:6px;">Select a Date</h3>
        <p style="font-size:13px;max-width:340px;line-height:1.6;">Choose a year, month, and day from the sidebar to browse your historical trading charts.</p>
        <p style="font-size:12px;color:var(--accent);margin-top:12px;opacity:0.7;">💡 Pro tip: Press <kbd style="background:rgba(255,255,255,0.08);border:1px solid var(--border);padding:2px 6px;border-radius:4px;font-size:11px;">Ctrl+V</kbd> to paste a screenshot directly into today&apos;s journal.</p>
      </div>
    `;
  }

  function _chResetSelection() {
    _chImages = [];
    _chDateLabel = '';
    _cjCurrentDate = '';
    _cjCurrentDayMeta = null;
    _cjLoadGeneration += 1;
    if (_cjLoadController) _cjLoadController.abort();
    document.querySelectorAll('.chday-btn.active').forEach((btn) => btn.classList.remove('active'));
    document.querySelectorAll('.cj-entry-item.active').forEach((el) => el.classList.remove('active'));
    const dateLabel = document.getElementById('cj-date-label');
    if (dateLabel) dateLabel.textContent = 'Select a date';
    _cjClearForm();
    _chRenderEmptyState();
  }

  window.initChartsPage = function() {
    if (!_chLoaded) {
      _chLoaded = true;
      const phIcon = document.getElementById('ch-placeholder-icon');
      if (phIcon) phIcon.innerHTML = ICO.candle(52);
      // Inject SVG icons replacing emojis
      const sideTitle = document.getElementById('ch-sidebar-title');
      if (sideTitle) sideTitle.insertAdjacentHTML('afterbegin', ICO.folder(14) + ' ');
      const jIco = document.getElementById('cj-journal-ico');
      if (jIco) jIco.innerHTML = ICO.edit(24);
      const saveIco = document.getElementById('cj-save-ico');
      if (saveIco) saveIco.innerHTML = ICO.save(14);
      const toastIco = document.getElementById('cj-toast-ico');
      if (toastIco) toastIco.innerHTML = ICO.download(14);
      _cjBindForm();
      _cjBindPaste();
      _cjLoadPlanner();
      window._cjShowPanel('journal');
    }
    _chResetSelection();
    _chLoadTree();
    _cjLoadEntries();
  };

  // ── Tree loading ──
  async function _chLoadTree() {
    try {
      const r = await fetch('/api/charts/tree', { credentials: 'same-origin' });
      const d = await r.json();
      _chTree = d.years || {};
      _chRenderTree();
    } catch (e) {
      document.getElementById('ch-tree').innerHTML = '<div style="text-align:center;color:var(--red);font-size:12px;padding:20px 0;">Failed to load chart tree</div>';
    }
  }

  function _chBuildTargetFromDate(dateStr) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr || ''));
    if (!match) return null;
    const year = match[1];
    const monthIdx = Number(match[2]) - 1;
    const day = match[3];
    const monthAbbr = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][monthIdx];
    if (!monthAbbr) return null;
    return {
      year,
      monthFolder: monthAbbr + '-' + year,
      dayFolder: day + '-' + monthAbbr + '-' + year,
      dateLabel: day + ' ' + monthAbbr + ' ' + year,
      sortKey: dateStr
    };
  }

  function _chFindDayBySortKey(sortKey) {
    for (const year of Object.keys(_chTree || {})) {
      const months = _chTree[year] || [];
      for (const month of months) {
        const day = (month.days || []).find(entry => entry.sort === sortKey);
        if (day) {
          return {
            year,
            monthFolder: month.folder,
            dayFolder: day.folder,
            dateLabel: day.label + ' ' + year,
            sortKey: day.sort
          };
        }
      }
    }
    return null;
  }

  function _chFindDayByFolders(year, monthFolder, dayFolder) {
    const months = ((_chTree || {})[year] || []);
    const month = months.find(entry => entry.folder === monthFolder);
    if (!month) return null;
    const day = (month.days || []).find(entry => entry.folder === dayFolder);
    if (!day) return null;
    return {
      year,
      monthFolder,
      dayFolder,
      dateLabel: day.label + ' ' + year,
      sortKey: day.sort
    };
  }

  function _chFindDayButton(meta) {
    if (!meta) return null;
    return Array.from(document.querySelectorAll('.chday-btn')).find(btn =>
      btn.dataset.year === String(meta.year) &&
      btn.dataset.month === meta.monthFolder &&
      btn.dataset.day === meta.dayFolder
    ) || null;
  }

  function _chRenderTree() {
    const el = document.getElementById('ch-tree');
    const years = Object.keys(_chTree).sort().reverse();
    if (!years.length) {
      el.innerHTML = '<div style="text-align:center;color:var(--muted);font-size:12px;padding:20px 0;">No charts found.<br>Add images to <code style="color:var(--accent);font-size:11px;">Daily Charts/</code></div>';
      return;
    }
    const selectedYear = _cjCurrentDayMeta ? String(_cjCurrentDayMeta.year) : '';
    const selectedMonth = _cjCurrentDayMeta ? String(_cjCurrentDayMeta.monthFolder) : '';
    const selectedDay = _cjCurrentDayMeta ? String(_cjCurrentDayMeta.dayFolder) : '';
    let h = '';
    years.forEach((year, yi) => {
      const months = _chTree[year];
      const totalDays = months.reduce((s, m) => s + m.days.length, 0);
      const yo = String(year) === selectedYear ? ' open' : '';
      h += '<div>';
      h += '<button class="chtree-toggle' + yo + '" onclick="window._chToggle(this)"><span class="arrow">▶</span><span class="yr-label">' + ICO.calendar(14) + ' ' + year + '</span><span class="cnt">' + totalDays + 'd</span></button>';
      h += '<div class="chtree-children' + yo + '">';
      months.forEach((mo, mi) => {
        const moo = String(year) === selectedYear && mo.folder === selectedMonth ? ' open' : '';
        h += '<div>';
        h += '<button class="chtree-toggle' + moo + '" onclick="window._chToggle(this)"><span class="arrow">▶</span><span class="mo-label">' + escapeHtml(mo.label) + '</span><span class="cnt">' + mo.days.length + '</span></button>';
        h += '<div class="chtree-children' + moo + '">';
        mo.days.forEach((day, di) => {
          const activeDay = String(year) === selectedYear && mo.folder === selectedMonth && day.folder === selectedDay ? ' active' : '';
          h += '<div class="ch-drag-item" draggable="true" data-year="' + escapeAttr(year) + '" data-month="' + escapeAttr(mo.folder) + '" data-folder="' + escapeAttr(day.folder) + '" data-idx="' + di + '" style="display:flex;align-items:center;gap:2px;position:relative;">';
          h += '<span class="ch-drag-handle" style="cursor:grab;opacity:0.2;padding:2px 3px;font-size:10px;flex-shrink:0;user-select:none;" title="Drag to reorder">&#9776;</span>';
          h += '<button class="chday-btn' + activeDay + '" data-year="' + escapeAttr(year) + '" data-month="' + escapeAttr(mo.folder) + '" data-day="' + escapeAttr(day.folder) + '" data-sort="' + escapeAttr(day.sort) + '" style="flex:1;" onclick="window._chSelectDay(\'' + escapeJsSingleQuoted(year) + '\',\'' + escapeJsSingleQuoted(mo.folder) + '\',\'' + escapeJsSingleQuoted(day.folder) + '\',\'' + escapeJsSingleQuoted(day.label + ' ' + year) + '\',\'' + escapeJsSingleQuoted(day.sort) + '\',this)"><span style="opacity:0.5;">' + ICO.doc(12) + '</span> ' + escapeHtml(day.label) + '</button>';
          h += '<span class="ch-edit-btn" onclick="event.stopPropagation();window._chRenameFolder(\'' + escapeJsSingleQuoted(year) + '\',\'' + escapeJsSingleQuoted(mo.folder) + '\',\'' + escapeJsSingleQuoted(day.folder) + '\')" title="Rename" style="cursor:pointer;opacity:0.3;padding:2px 4px;font-size:11px;flex-shrink:0;" onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=0.3">&#9998;</span>';
          h += '</div>';
        });
        // Add new folder button at end of month
        h += '<button class="chday-btn" style="opacity:0.5;font-style:italic;border:1px dashed var(--border);" onclick="window._chCreateFolder(\'' + escapeJsSingleQuoted(year) + '\',\'' + escapeJsSingleQuoted(mo.folder) + '\')">+ New folder</button>';
        h += '</div></div>';
      });
      // Add new month/folder button at year level
      h += '<button class="chday-btn" style="opacity:0.5;font-style:italic;border:1px dashed var(--border);margin-top:2px;" onclick="window._chCreateMonthFolder(\'' + escapeJsSingleQuoted(year) + '\')">+ New month folder</button>';
      h += '</div></div>';
    });
    el.innerHTML = h;
  }

  window._chToggle = function(btn) {
    btn.classList.toggle('open');
    const ch = btn.nextElementSibling;
    if (ch) ch.classList.toggle('open');
  };

  // ── Styled modal helpers for chart folder operations ──
  function _chOpenInputModal(title, fields, onSave) {
    document.getElementById('ch-folder-modal-title').textContent = title;
    const container = document.getElementById('ch-folder-modal-fields');
    container.innerHTML = fields.map(f =>
      `<div style="margin-bottom:14px;">
        <label style="display:block;font-size:11px;color:var(--muted);margin-bottom:5px;text-transform:uppercase;letter-spacing:0.5px;">${escapeHtml(f.label)}</label>
        <input type="text" id="ch-inp-${f.id}" value="${escapeAttr(f.value||'')}" placeholder="${escapeAttr(f.placeholder||'')}"
          style="width:100%;padding:10px 14px;background:var(--card2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-family:'Outfit',sans-serif;font-size:14px;">
      </div>`
    ).join('');
    const modal = document.getElementById('ch-folder-modal');
    modal.classList.add('open');
    // Focus first input
    setTimeout(() => { const inp = container.querySelector('input'); if (inp) inp.focus(); }, 100);
    // Wire save button
    const okBtn = document.getElementById('ch-folder-modal-ok');
    const handler = () => {
      const vals = {};
      fields.forEach(f => { vals[f.id] = document.getElementById('ch-inp-' + f.id).value.trim(); });
      onSave(vals);
      okBtn.removeEventListener('click', handler);
    };
    okBtn.onclick = handler;
    // Enter key submits
    container.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); handler(); } };
  }
  window._chCloseInputModal = function() {
    document.getElementById('ch-folder-modal').classList.remove('open');
  };

  // ── Drag & Drop reorder for day folders ──
  let _chDragItem = null;
  document.addEventListener('dragstart', function(e) {
    const item = e.target.closest('.ch-drag-item');
    if (!item) return;
    _chDragItem = item;
    item.style.opacity = '0.4';
    e.dataTransfer.effectAllowed = 'move';
  });
  document.addEventListener('dragend', function(e) {
    if (_chDragItem) _chDragItem.style.opacity = '1';
    _chDragItem = null;
    document.querySelectorAll('.ch-drag-item').forEach(el => {
      el.style.borderTop = '';
      el.style.borderBottom = '';
    });
  });
  document.addEventListener('dragover', function(e) {
    const target = e.target.closest('.ch-drag-item');
    if (!target || !_chDragItem || target === _chDragItem) return;
    if (target.dataset.month !== _chDragItem.dataset.month || target.dataset.year !== _chDragItem.dataset.year) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    // Show drop indicator
    document.querySelectorAll('.ch-drag-item').forEach(el => { el.style.borderTop = ''; el.style.borderBottom = ''; });
    const rect = target.getBoundingClientRect();
    const mid = rect.top + rect.height / 2;
    if (e.clientY < mid) {
      target.style.borderTop = '2px solid var(--accent)';
      target.style.borderBottom = '';
    } else {
      target.style.borderBottom = '2px solid var(--accent)';
      target.style.borderTop = '';
    }
  });
  document.addEventListener('drop', async function(e) {
    const target = e.target.closest('.ch-drag-item');
    if (!target || !_chDragItem || target === _chDragItem) return;
    if (target.dataset.month !== _chDragItem.dataset.month || target.dataset.year !== _chDragItem.dataset.year) return;
    e.preventDefault();
    const year = target.dataset.year;
    const month = target.dataset.month;
    // Get all items in this month group
    const parent = target.parentElement;
    const items = [...parent.querySelectorAll('.ch-drag-item[data-month="' + month + '"][data-year="' + year + '"]')];
    const dragFolder = _chDragItem.dataset.folder;
    const targetFolder = target.dataset.folder;
    // Build new order
    let folders = items.map(el => el.dataset.folder);
    folders = folders.filter(f => f !== dragFolder);
    const targetIdx = folders.indexOf(targetFolder);
    const rect = target.getBoundingClientRect();
    const insertBefore = e.clientY < rect.top + rect.height / 2;
    if (insertBefore) {
      folders.splice(targetIdx, 0, dragFolder);
    } else {
      folders.splice(targetIdx + 1, 0, dragFolder);
    }
    // Save to server
    try {
      const r = await fetch('/api/charts/reorder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ year, month, order: folders })
      });
      const d = await r.json();
      if (d.status === 'ok') {
        _chLoadTree();
      }
    } catch (err) { showToast('Reorder failed', 'error'); }
  });

  window._chRenameFolder = function(year, month, dayFolder) {
    _chOpenInputModal('Rename Folder', [
      { id: 'name', label: 'Folder Name', value: dayFolder, placeholder: 'e.g. 17-Mar-2026' }
    ], async (vals) => {
      if (!vals.name || vals.name === dayFolder) { _chCloseInputModal(); return; }
      try {
        const r = await fetch('/api/charts/rename-folder', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ year, month, old_day: dayFolder, new_day: vals.name })
        });
        const d = await r.json();
        _chCloseInputModal();
        if (d.status === 'ok') { showToast('Folder renamed', 'success'); _chLoadTree(); }
        else { showToast(d.detail || 'Rename failed', 'error'); }
      } catch (e) { _chCloseInputModal(); showToast('Rename failed: ' + e.message, 'error'); }
    });
  };

  window._chCreateFolder = function(year, month) {
    _chOpenInputModal('Create New Folder', [
      { id: 'name', label: 'Folder Name', value: '', placeholder: 'e.g. 17-Mar-2026' }
    ], async (vals) => {
      if (!vals.name) { _chCloseInputModal(); return; }
      try {
        const r = await fetch('/api/charts/create-folder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ year, month, day_name: vals.name })
        });
        const d = await r.json();
        _chCloseInputModal();
        if (d.status === 'ok') { showToast('Folder created', 'success'); _chLoadTree(); }
        else { showToast(d.detail || 'Create failed', 'error'); }
      } catch (e) { _chCloseInputModal(); showToast('Create failed: ' + e.message, 'error'); }
    });
  };

  window._chCreateMonthFolder = function(year) {
    _chOpenInputModal('Create Month Folder', [
      { id: 'month', label: 'Month Folder', value: '', placeholder: 'e.g. Apr-2026' },
      { id: 'day', label: 'First Day Folder', value: '', placeholder: 'e.g. 01-Apr-2026' }
    ], async (vals) => {
      if (!vals.month || !vals.day) { _chCloseInputModal(); return; }
      try {
        const r = await fetch('/api/charts/create-folder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ year, month: vals.month, day_name: vals.day })
        });
        const d = await r.json();
        _chCloseInputModal();
        if (d.status === 'ok') { showToast('Month folder created', 'success'); _chLoadTree(); }
        else { showToast(d.detail || 'Create failed', 'error'); }
      } catch (e) { _chCloseInputModal(); showToast('Create failed: ' + e.message, 'error'); }
    });
  };

  // ── Day selection → load images + journal ──
  window._chSelectDay = async function(year, monthFolder, dayFolder, dateLabel, sortKey, btn) {
    _chDateLabel = dateLabel;
    _cjCurrentDate = sortKey;
    _cjCurrentDayMeta = { year, monthFolder, dayFolder };
    _chRenderTree();
    document.getElementById('cj-date-label').textContent = dateLabel + ' (' + sortKey + ')';

    const area = document.getElementById('ch-content');
    area.innerHTML = '<div class="ch-grid">' + '<div class="skeleton" style="height:200px;"></div>'.repeat(4) + '</div>';
    try {
      const url = '/api/charts/images/' + encodeURIComponent(year) + '/' + encodeURIComponent(monthFolder) + '/' + encodeURIComponent(dayFolder);
      const r = await fetch(url, { credentials: 'same-origin' });
      const d = await r.json();
      _chImages = (d.images || []).map((name, i) => ({ name, url: d.urls[i] }));
      _chRenderImages();
    } catch (e) {
      area.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:300px;color:var(--red);"><h3>Failed to load</h3><p>' + escapeHtml(e.message || 'Unknown error') + '</p></div>';
    }
    _cjLoadJournal(sortKey);
    // Highlight in entries list
    document.querySelectorAll('.cj-entry-item').forEach(el => {
      el.classList.toggle('active', el.dataset.date === sortKey);
    });
  };

  function _chRenderImages() {
    const area = document.getElementById('ch-content');
    if (!_chImages.length) {
      let emptyHtml = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:300px;text-align:center;color:var(--muted);">';
      if (_chDateLabel) {
        emptyHtml += '<div style="font-family:\'Syne\',sans-serif;font-size:18px;color:var(--text);margin-bottom:8px;">' + escapeHtml(_chDateLabel) + '</div>';
      }
      emptyHtml += '<div style="font-size:42px;margin-bottom:12px;">📭</div><h3 style="font-family:\'Syne\',sans-serif;font-size:16px;color:var(--text-dim);">No Charts</h3><p style="font-size:12px;color:var(--accent);margin-top:8px;opacity:0.7;">Press Ctrl+V to paste a screenshot</p></div>';
      area.innerHTML = emptyHtml;
      return;
    }
    let h = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">';
    h += '<div><div style="font-family:\'Syne\',sans-serif;font-size:20px;font-weight:700;">' + escapeHtml(_chDateLabel) + '</div>';
    h += '<div style="font-size:12px;color:var(--muted);margin-top:2px;">Press Ctrl+V to add more charts</div></div>';
    h += '<span style="background:rgba(var(--pf-tint-primary-rgb, 0,191,165),0.12);border:1px solid rgba(var(--pf-tint-primary-rgb, 0,191,165),0.25);color:var(--accent);padding:3px 12px;border-radius:20px;font-size:12px;font-weight:600;font-family:\'JetBrains Mono\',monospace;">' + _chImages.length + ' chart' + (_chImages.length > 1 ? 's' : '') + '</span>';
    h += '</div><div class="ch-grid">';
    _chImages.forEach((img, i) => {
      const safeName = escapeHtml(img.name || '');
      h += '<div class="ch-card">';
      h += '<button class="ch-delete-btn" title="Delete chart" onclick="event.stopPropagation();window._chDeleteChart(' + i + ')">✕</button>';
      h += '<div onclick="window._chOpenLB(' + i + ')" style="cursor:pointer;"><img src="' + escapeAttr(img.url || '') + '" alt="' + escapeAttr(img.name || '') + '" loading="lazy"></div>';
      h += '<div class="ch-card-info"><span class="ch-card-name" title="Click to rename" onclick="event.stopPropagation();window._chRenameChart(' + i + ')">' + safeName + '</span><span class="ch-card-idx">#' + (i + 1) + '</span></div>';
      h += '</div>';
    });
    h += '</div>';
    area.innerHTML = h;
  }

  // ── Lightbox ──
  const _chLBState = {
    scale: 1,
    minScale: 1,
    maxScale: 5,
    baseWidth: 0,
    baseHeight: 0,
    stageWidth: 0,
    stageHeight: 0,
    pointerId: null,
    dragStartX: 0,
    dragStartY: 0,
    scrollStartLeft: 0,
    scrollStartTop: 0,
  };

  function _chClamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function _chGetLBRefs() {
    return {
      overlay: document.getElementById('ch-lightbox'),
      wrap: document.getElementById('ch-lb-wrap'),
      stage: document.getElementById('ch-lb-stage'),
      img: document.getElementById('ch-lb-img'),
      zoomLabel: document.getElementById('ch-lb-zoom'),
    };
  }

  function _chMeasureLBBase() {
    const { wrap, img } = _chGetLBRefs();
    if (!wrap || !img) return null;
    const wrapWidth = wrap.clientWidth || wrap.getBoundingClientRect().width || 1;
    const wrapHeight = wrap.clientHeight || wrap.getBoundingClientRect().height || 1;
    const naturalWidth = img.naturalWidth || wrapWidth || 1;
    const naturalHeight = img.naturalHeight || wrapHeight || 1;
    const fit = Math.min(wrapWidth / naturalWidth, wrapHeight / naturalHeight, 1);
    _chLBState.baseWidth = Math.max(1, naturalWidth * fit);
    _chLBState.baseHeight = Math.max(1, naturalHeight * fit);
  }

  function _chGetLBViewport(anchorX, anchorY) {
    const { wrap } = _chGetLBRefs();
    if (!wrap) return { focusX: 0.5, focusY: 0.5, offsetX: 0, offsetY: 0 };
    const rect = wrap.getBoundingClientRect();
    const offsetX = anchorX == null ? wrap.clientWidth / 2 : anchorX - rect.left;
    const offsetY = anchorY == null ? wrap.clientHeight / 2 : anchorY - rect.top;
    const stageWidth = _chLBState.stageWidth || wrap.clientWidth || 1;
    const stageHeight = _chLBState.stageHeight || wrap.clientHeight || 1;
    return {
      focusX: _chClamp((wrap.scrollLeft + offsetX) / stageWidth, 0, 1),
      focusY: _chClamp((wrap.scrollTop + offsetY) / stageHeight, 0, 1),
      offsetX,
      offsetY,
    };
  }

  function _chApplyLBLayout(viewport) {
    const { wrap, stage, img, zoomLabel } = _chGetLBRefs();
    if (!wrap || !stage || !img) return;
    const wrapWidth = wrap.clientWidth || wrap.getBoundingClientRect().width || 1;
    const wrapHeight = wrap.clientHeight || wrap.getBoundingClientRect().height || 1;
    const scaledWidth = Math.max(1, _chLBState.baseWidth * _chLBState.scale);
    const scaledHeight = Math.max(1, _chLBState.baseHeight * _chLBState.scale);
    _chLBState.stageWidth = Math.max(wrapWidth, Math.ceil(scaledWidth));
    _chLBState.stageHeight = Math.max(wrapHeight, Math.ceil(scaledHeight));
    stage.style.width = _chLBState.stageWidth + 'px';
    stage.style.height = _chLBState.stageHeight + 'px';
    img.style.width = Math.ceil(scaledWidth) + 'px';
    img.style.height = Math.ceil(scaledHeight) + 'px';
    wrap.classList.toggle('is-zoomed', _chLBState.scale > 1.01);
    wrap.classList.toggle('is-dragging', _chLBState.pointerId !== null);
    if (zoomLabel) zoomLabel.textContent = Math.round(_chLBState.scale * 100) + '%';
    requestAnimationFrame(function() {
      const maxScrollLeft = Math.max(0, _chLBState.stageWidth - wrap.clientWidth);
      const maxScrollTop = Math.max(0, _chLBState.stageHeight - wrap.clientHeight);
      if (!viewport) {
        wrap.scrollLeft = maxScrollLeft / 2;
        wrap.scrollTop = maxScrollTop / 2;
        return;
      }
      const targetLeft = (viewport.focusX * _chLBState.stageWidth) - viewport.offsetX;
      const targetTop = (viewport.focusY * _chLBState.stageHeight) - viewport.offsetY;
      wrap.scrollLeft = _chClamp(targetLeft, 0, maxScrollLeft);
      wrap.scrollTop = _chClamp(targetTop, 0, maxScrollTop);
    });
  }

  function _chSetLBZoom(nextScale, clientX, clientY) {
    if (!_chLBState.baseWidth || !_chLBState.baseHeight) _chMeasureLBBase();
    const viewport = _chGetLBViewport(clientX, clientY);
    const targetScale = _chClamp(nextScale, _chLBState.minScale, _chLBState.maxScale);
    if (Math.abs(targetScale - _chLBState.scale) < 0.001) return;
    _chLBState.scale = targetScale;
    if (_chLBState.scale <= 1.01) {
      _chLBState.scale = 1;
    }
    _chApplyLBLayout(viewport);
  }

  window.chAdjustLBZoom = function(step) {
    const { wrap } = _chGetLBRefs();
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    _chSetLBZoom(_chLBState.scale + step, rect.left + rect.width / 2, rect.top + rect.height / 2);
  };

  window.chResetLBView = function() {
    if (!_chLBState.baseWidth || !_chLBState.baseHeight) _chMeasureLBBase();
    _chLBState.scale = 1;
    _chLBState.pointerId = null;
    _chApplyLBLayout({ focusX: 0.5, focusY: 0.5, offsetX: 0, offsetY: 0 });
  };

  window._chOpenLB = function(idx) {
    _chLBIdx = idx;
    document.getElementById('ch-lightbox').classList.add('open');
    document.body.style.overflow = 'hidden';
    _chUpdateLB();
  };
  window.chCloseLB = function() {
    document.getElementById('ch-lightbox').classList.remove('open');
    document.body.style.overflow = '';
    chResetLBView();
  };
  window.chNavLB = function(dir) {
    const n = _chLBIdx + dir;
    if (n < 0 || n >= _chImages.length) return;
    _chLBIdx = n;
    _chUpdateLB();
  };
  function _chUpdateLB() {
    const img = _chImages[_chLBIdx];
    if (!img) return;
    const el = document.getElementById('ch-lb-img');
    el.src = img.url;
    el.alt = img.name;
    el.onload = function() {
      requestAnimationFrame(function() {
        _chMeasureLBBase();
        chResetLBView();
      });
    };
    if (el.complete) requestAnimationFrame(function() {
      _chMeasureLBBase();
      chResetLBView();
    });
    document.getElementById('ch-lb-name').textContent = img.name;
    document.getElementById('ch-lb-count').textContent = (_chLBIdx + 1) + ' / ' + _chImages.length;
    document.querySelector('.ch-lb-nav.prev').disabled = _chLBIdx === 0;
    document.querySelector('.ch-lb-nav.next').disabled = _chLBIdx === _chImages.length - 1;
  }
  (function bindChartLightboxPanZoom() {
    const wrap = document.getElementById('ch-lb-wrap');
    if (!wrap) return;
    wrap.addEventListener('wheel', function(e) {
      const lb = document.getElementById('ch-lightbox');
      if (!lb || !lb.classList.contains('open')) return;
      e.preventDefault();
      const step = e.deltaY < 0 ? 0.28 : -0.28;
      _chSetLBZoom(_chLBState.scale + step, e.clientX, e.clientY);
    }, { passive: false });
    wrap.addEventListener('dblclick', function(e) {
      e.preventDefault();
      _chSetLBZoom(_chLBState.scale > 1.2 ? 1 : 2.2, e.clientX, e.clientY);
    });
    wrap.addEventListener('pointerdown', function(e) {
      if (_chLBState.scale <= 1.01) return;
      if (e.button !== undefined && e.button !== 0) return;
      _chLBState.pointerId = e.pointerId;
      _chLBState.dragStartX = e.clientX;
      _chLBState.dragStartY = e.clientY;
      _chLBState.scrollStartLeft = wrap.scrollLeft;
      _chLBState.scrollStartTop = wrap.scrollTop;
      wrap.setPointerCapture?.(e.pointerId);
      wrap.classList.add('is-dragging');
      e.preventDefault();
    });
    wrap.addEventListener('pointermove', function(e) {
      if (_chLBState.pointerId !== e.pointerId) return;
      wrap.scrollLeft = _chLBState.scrollStartLeft - (e.clientX - _chLBState.dragStartX);
      wrap.scrollTop = _chLBState.scrollStartTop - (e.clientY - _chLBState.dragStartY);
      e.preventDefault();
    });
    function stopDrag(e) {
      if (_chLBState.pointerId === null) return;
      if (e.pointerId !== undefined && _chLBState.pointerId !== e.pointerId) return;
      _chLBState.pointerId = null;
      wrap.classList.remove('is-dragging');
    }
    wrap.addEventListener('pointerup', stopDrag);
    wrap.addEventListener('pointercancel', stopDrag);
    wrap.addEventListener('lostpointercapture', stopDrag);
    window.addEventListener('resize', function() {
      const lb = document.getElementById('ch-lightbox');
      if (!lb || !lb.classList.contains('open')) return;
      const viewport = _chGetLBViewport();
      _chMeasureLBBase();
      _chApplyLBLayout(viewport);
    });
  })();
  // Keyboard nav + close on outside click
  document.addEventListener('keydown', function(e) {
    const lb = document.getElementById('ch-lightbox');
    if (!lb || !lb.classList.contains('open')) return;
    if (e.key === 'Escape') chCloseLB();
    if (e.key === 'ArrowLeft') chNavLB(-1);
    if (e.key === 'ArrowRight') chNavLB(1);
    if (e.key === '+' || e.key === '=') chAdjustLBZoom(0.35);
    if (e.key === '-' || e.key === '_') chAdjustLBZoom(-0.35);
    if (e.key === '0') chResetLBView();
  });
  // Close lightbox when clicking outside the image
  document.addEventListener('click', function(e) {
    const lb = document.getElementById('ch-lightbox');
    if (!lb || !lb.classList.contains('open')) return;
    if (e.target === lb) {
      chCloseLB();
    }
  });

  // ══════════════════════════════════════════════════════════
  //  DELETE CHART — custom modal
  // ══════════════════════════════════════════════════════════
  window._chDeleteChart = async function(idx) {
    const img = _chImages[idx];
    if (!img || !_cjCurrentDayMeta) return;
    const ok = await customConfirm('Delete chart <strong>' + img.name + '</strong>?<br><span style="font-size:11px;">This cannot be undone.</span>', {
      title: 'Delete Chart',
      icon: ICO.trash ? ICO.trash(28) : '🗑️',
      okText: 'Delete',
      danger: true,
    });
    if (!ok) return;
    const { year, monthFolder, dayFolder } = _cjCurrentDayMeta;
    try {
      const url = '/api/charts/delete/' + [year, monthFolder, dayFolder, img.name].map(encodeURIComponent).join('/');
      const r = await fetch(url, { method: 'DELETE', credentials: 'same-origin' });
      if (!r.ok) {
        const ct = r.headers.get('content-type') || '';
        const err = ct.includes('json') ? (await r.json()).detail : 'Delete failed (' + r.status + ')';
        throw new Error(err);
      }
      _chImages.splice(idx, 1);
      _chRenderImages();
    } catch(e) {
      await customConfirm(e.message, { title: 'Error', icon: '❌', okText: 'OK', cancelText: '' });
    }
  };

  // ══════════════════════════════════════════════════════════
  //  RENAME CHART — click filename to rename
  // ══════════════════════════════════════════════════════════
  window._chRenameChart = async function(idx) {
    const img = _chImages[idx];
    if (!img || !_cjCurrentDayMeta) return;
    const baseName = img.name.replace(/\.[^.]+$/, '');
    const newName = await customConfirm('Enter a new name for this chart:', {
      title: 'Rename Chart',
      icon: ICO.memo(28),
      okText: 'Rename',
      prompt: true,
      promptValue: baseName,
    });
    if (!newName || newName === baseName) return;
    const { year, monthFolder, dayFolder } = _cjCurrentDayMeta;
    try {
      const url = '/api/charts/rename/' + [year, monthFolder, dayFolder, img.name].map(encodeURIComponent).join('/');
      const r = await fetch(url, {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_name: newName }),
      });
      if (!r.ok) {
        const ct = r.headers.get('content-type') || '';
        const err = ct.includes('json') ? (await r.json()).detail : 'Rename failed (' + r.status + ')';
        throw new Error(err);
      }
      const d = await r.json();
      _chImages[idx] = { name: d.new_name, url: d.url };
      _chRenderImages();
    } catch(e) {
      await customConfirm(e.message, { title: 'Error', icon: '❌', okText: 'OK', cancelText: '' });
    }
  };

  function _cjDefaultPlanner() {
    return {
      monthly_expense: 0,
      assets_value: 0,
      years_to_reserve: 10,
      years_to_ffv: 10,
      monthly_income: 0,
      phv_increase: 0,
    };
  }

  function _cjNormalizePlanner(plan) {
    const base = _cjDefaultPlanner();
    const src = plan && typeof plan === 'object' ? plan : {};
    const moneyFields = ['monthly_expense', 'assets_value', 'monthly_income', 'phv_increase'];
    moneyFields.forEach(field => {
      const num = Number(src[field]);
      base[field] = Number.isFinite(num) && num >= 0 ? num : base[field];
    });
    ['years_to_reserve', 'years_to_ffv'].forEach(field => {
      const num = Number(src[field]);
      base[field] = Number.isFinite(num) && num >= 1 ? Math.min(50, Math.round(num)) : base[field];
    });
    if (src.updated_at) base.updated_at = src.updated_at;
    return base;
  }

  function _cjPlannerMoney(value) {
    const num = Number(value || 0);
    if (typeof fmt === 'function') return fmt(num);
    return '₹' + num.toFixed(2);
  }

  const _CJ_PLAN_WORK_HOURS_PER_DAY = 8;
  const _CJ_PLAN_WORK_DAYS_PER_WEEK = 5;
  const _CJ_PLAN_WORK_WEEKS_PER_MONTH = 4;
  const _CJ_PLAN_WORK_DAYS_PER_MONTH = _CJ_PLAN_WORK_DAYS_PER_WEEK * _CJ_PLAN_WORK_WEEKS_PER_MONTH;
  const _CJ_PLAN_WORK_HOURS_PER_MONTH = _CJ_PLAN_WORK_HOURS_PER_DAY * _CJ_PLAN_WORK_DAYS_PER_MONTH;

  function _cjPlanCalc() {
    const plan = _cjNormalizePlanner(_cjPlanner || {});
    const reserveValue = plan.monthly_expense * 12;
    const ffvValue = plan.monthly_expense * 300;
    const shortageValue = Math.max(0, ffvValue - plan.assets_value);
    const reserveGap = Math.max(0, reserveValue - plan.assets_value);
    const reserveMonthlySave = reserveGap / Math.max(1, plan.years_to_reserve * 12);
    const ffvMonthlySave = shortageValue / Math.max(1, plan.years_to_ffv * 12);
    const pdv = plan.monthly_income / _CJ_PLAN_WORK_DAYS_PER_MONTH;
    const phv = plan.monthly_income / _CJ_PLAN_WORK_HOURS_PER_MONTH;
    const addedIncome = plan.phv_increase * _CJ_PLAN_WORK_HOURS_PER_MONTH;
    const monthlySurplus = plan.monthly_income - plan.monthly_expense;
    const runwayMonths = plan.monthly_expense > 0 ? plan.assets_value / plan.monthly_expense : 0;
    const ffvProgressPct = ffvValue > 0 ? Math.min(100, (plan.assets_value / ffvValue) * 100) : 0;
    const actions = [];
    if (plan.monthly_expense <= 0) actions.push('Enter a realistic monthly expense first');
    if (plan.monthly_expense > 0 && runwayMonths < 12) actions.push('Build 1 year reserve');
    if (monthlySurplus <= 0) actions.push('Increase income or reduce burn');
    if (plan.phv_increase <= 0) actions.push('Increase PHV urgently');
    if (shortageValue > 0) actions.push('Close the FFV shortage steadily');
    if (!actions.length) actions.push('Stay invested and review monthly');
    return {
      ...plan,
      reserveValue,
      ffvValue,
      shortageValue,
      reserveMonthlySave,
      ffvMonthlySave,
      pdv,
      phv,
      addedIncome,
      monthlySurplus,
      runwayMonths,
      ffvProgressPct,
      actions,
    };
  }

  function _cjRenderPlanner() {
    _cjPlanner = _cjNormalizePlanner(_cjPlanner || {});
    const calc = _cjPlanCalc();
    const wrap = document.getElementById('cj-plan-table-wrap');
    if (!wrap) return;
    const field = (id, value, opts = {}) =>
      `<input type="number" step="${opts.step || '100'}" min="0" id="${id}" class="cj-plan-input" inputmode="numeric" aria-label="${escapeAttr(opts.label || id)}" value="${escapeAttr(String(Math.round(Number(value || 0))))}" oninput="window._cjPlannerFieldChanged(this,false)" onchange="window._cjPlannerFieldChanged(this,true)" onblur="window._cjPlannerFieldChanged(this,true)">`;
    const label = (text, subhint = '') =>
      `<div><div class="cj-plan-label">${escapeHtml(text)}</div>${subhint ? `<div class="cj-plan-subhint">${escapeHtml(subhint)}</div>` : ''}</div>`;
    const rowValue = (id, value, variant = '') =>
      `<div id="${id}" class="cj-plan-row-value${variant ? ` ${variant}` : ''}">${escapeHtml(value)}</div>`;
    const outputCard = (id, title, value, variant = '') =>
      `<div class="cj-plan-output-card"><div class="cj-plan-output-label">${escapeHtml(title)}</div><div id="${id}" class="cj-plan-output-value${variant ? ` ${variant}` : ''}">${escapeHtml(value)}</div></div>`;
    const actionChip = (item) => {
      const lower = String(item || '').toLowerCase();
      const cls = lower.includes('phv') ? ' emphasis' : lower.includes('stay') ? ' good' : '';
      return `<div class="cj-plan-action-chip${cls}">${escapeHtml(String(item || '').toUpperCase())}</div>`;
    };
    wrap.innerHTML = `
      <div class="cj-plan-layout">
        <section class="cj-plan-block">
          <div class="cj-plan-block-title ffv">FINANCIAL FREEDOM VALUE</div>
          <div class="cj-plan-form">
            <div class="cj-plan-row">
              ${label('MONTHLY EXPENSE', 'Your monthly burn rate')}
              ${field('cj-plan-monthly-expense', calc.monthly_expense, { step: '100', label: 'Monthly expense' })}
            </div>
            <div class="cj-plan-row">
              ${label('CURRENT ASSETS', 'Cash + investments already built')}
              ${field('cj-plan-assets-value', calc.assets_value, { step: '100', label: 'Current assets' })}
            </div>
            <div class="cj-plan-row">
              ${label('YEARS TO 1 YEAR RESERVE', 'Target timeline for emergency reserve')}
              ${field('cj-plan-years-reserve', calc.years_to_reserve, { step: '1', decimals: 0, label: 'Years to one year reserve' })}
            </div>
            <div class="cj-plan-row">
              ${label('YEARS TO FFV', 'Target timeline for long-term freedom')}
              ${field('cj-plan-years-ffv', calc.years_to_ffv, { step: '1', decimals: 0, label: 'Years to financial freedom value' })}
            </div>
          </div>
        </section>
        <section class="cj-plan-block">
          <div class="cj-plan-block-title income">INCOME ENGINE</div>
          <div class="cj-plan-form">
            <div class="cj-plan-row">
              ${label('MONTHLY INCOME', 'Stable take-home or recurring income')}
              ${field('cj-plan-monthly-income', calc.monthly_income, { step: '100', label: 'Monthly income' })}
            </div>
            <div class="cj-plan-row">
              ${label('INCREASE PHV', `Extra hourly value target (₹/hour) over ${_CJ_PLAN_WORK_HOURS_PER_MONTH} work hours/month`)}
              ${field('cj-plan-phv-increase', calc.phv_increase, { step: '10', label: 'Increase personal hourly value' })}
            </div>
            <div class="cj-plan-row">
              ${label('P D V', `${_CJ_PLAN_WORK_DAYS_PER_MONTH} working days/month`)}
              ${rowValue('cj-plan-pdv', _cjPlannerMoney(calc.pdv))}
            </div>
            <div class="cj-plan-row">
              ${label('P H V', `${_CJ_PLAN_WORK_HOURS_PER_DAY} hours/day × ${_CJ_PLAN_WORK_DAYS_PER_WEEK} days/week`)}
              ${rowValue('cj-plan-phv', _cjPlannerMoney(calc.phv))}
            </div>
            <div class="cj-plan-row">
              ${label('MONTHLY SURPLUS', 'Income minus monthly expense')}
              ${rowValue('cj-plan-surplus', _cjPlannerMoney(calc.monthlySurplus), calc.monthlySurplus >= 0 ? 'pos' : 'neg')}
            </div>
            <div class="cj-plan-row">
              ${label('RUNWAY TODAY', 'How many months your assets can cover')}
              ${rowValue('cj-plan-runway', `${calc.runwayMonths.toFixed(1)} months`, 'accent')}
            </div>
          </div>
        </section>
      </div>
      <div class="cj-plan-output-grid" style="margin-top:18px;">
        ${outputCard('cj-plan-out-reserve', '1 YEAR RESERVE', _cjPlannerMoney(calc.reserveValue), 'accent2')}
        ${outputCard('cj-plan-out-ffv', 'FFV TARGET', _cjPlannerMoney(calc.ffvValue), 'accent2')}
        ${outputCard('cj-plan-out-shortage', 'SHORTAGE', _cjPlannerMoney(calc.shortageValue), calc.shortageValue > 0 ? 'neg' : 'accent')}
        ${outputCard('cj-plan-out-added', 'ADDED INCOME IF PHV IMPROVES', _cjPlannerMoney(calc.addedIncome), 'accent')}
        ${outputCard('cj-plan-out-reserve-save', 'SAVE / MONTH FOR RESERVE', _cjPlannerMoney(calc.reserveMonthlySave))}
        ${outputCard('cj-plan-out-ffv-save', 'SAVE / MONTH FOR FFV', _cjPlannerMoney(calc.ffvMonthlySave))}
        ${outputCard('cj-plan-out-progress', 'FFV PROGRESS', `${calc.ffvProgressPct.toFixed(1)}%`, 'warn')}
        ${outputCard('cj-plan-out-runway', 'RUNWAY MONTHS', `${calc.runwayMonths.toFixed(1)} months`, 'accent')}
      </div>
      <section class="cj-plan-block cj-plan-actions-card">
        <div class="cj-plan-block-title actions">ACTION STEPS</div>
        <div id="cj-plan-actions-list" class="cj-plan-action-list">${calc.actions.map(actionChip).join('')}</div>
      </section>
    `;
    const summary = document.getElementById('cj-plan-summary');
    if (summary) {
      summary.innerHTML = `
        <div class="cj-plan-card"><div class="cj-plan-card-label">FFV</div><div id="cj-plan-summary-ffv" class="cj-plan-card-value">${_cjPlannerMoney(calc.ffvValue)}</div></div>
        <div class="cj-plan-card"><div class="cj-plan-card-label">Reserve</div><div id="cj-plan-summary-reserve" class="cj-plan-card-value">${_cjPlannerMoney(calc.reserveValue)}</div></div>
        <div class="cj-plan-card"><div class="cj-plan-card-label">Shortage</div><div id="cj-plan-summary-shortage" class="cj-plan-card-value ${calc.shortageValue > 0 ? 'neg' : 'pos'}">${_cjPlannerMoney(calc.shortageValue)}</div></div>
        <div class="cj-plan-card"><div class="cj-plan-card-label">Monthly Surplus</div><div id="cj-plan-summary-surplus" class="cj-plan-card-value ${calc.monthlySurplus >= 0 ? 'pos' : 'neg'}">${_cjPlannerMoney(calc.monthlySurplus)}</div></div>
      `;
    }
    _cjApplyPlannerComputed(calc);
  }

  function _cjApplyPlannerComputed(calc) {
    const setText = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };
    setText('cj-plan-pdv', _cjPlannerMoney(calc.pdv));
    setText('cj-plan-phv', _cjPlannerMoney(calc.phv));
    setText('cj-plan-surplus', _cjPlannerMoney(calc.monthlySurplus));
    setText('cj-plan-runway', `${calc.runwayMonths.toFixed(1)} months`);
    setText('cj-plan-summary-ffv', _cjPlannerMoney(calc.ffvValue));
    setText('cj-plan-summary-reserve', _cjPlannerMoney(calc.reserveValue));
    setText('cj-plan-summary-shortage', _cjPlannerMoney(calc.shortageValue));
    setText('cj-plan-summary-surplus', _cjPlannerMoney(calc.monthlySurplus));
    setText('cj-plan-out-reserve', _cjPlannerMoney(calc.reserveValue));
    setText('cj-plan-out-ffv', _cjPlannerMoney(calc.ffvValue));
    setText('cj-plan-out-shortage', _cjPlannerMoney(calc.shortageValue));
    setText('cj-plan-out-added', _cjPlannerMoney(calc.addedIncome));
    setText('cj-plan-out-reserve-save', _cjPlannerMoney(calc.reserveMonthlySave));
    setText('cj-plan-out-ffv-save', _cjPlannerMoney(calc.ffvMonthlySave));
    setText('cj-plan-out-progress', `${calc.ffvProgressPct.toFixed(1)}%`);
    setText('cj-plan-out-runway', `${calc.runwayMonths.toFixed(1)} months`);

    const shortageSummary = document.getElementById('cj-plan-summary-shortage');
    if (shortageSummary) shortageSummary.className = `cj-plan-card-value ${calc.shortageValue > 0 ? 'neg' : 'pos'}`;
    const surplusSummary = document.getElementById('cj-plan-summary-surplus');
    if (surplusSummary) surplusSummary.className = `cj-plan-card-value ${calc.monthlySurplus >= 0 ? 'pos' : 'neg'}`;
    const shortageCard = document.getElementById('cj-plan-out-shortage');
    if (shortageCard) shortageCard.className = `cj-plan-output-value ${calc.shortageValue > 0 ? 'neg' : 'accent'}`;
    const addedCard = document.getElementById('cj-plan-out-added');
    if (addedCard) addedCard.className = `cj-plan-output-value ${calc.addedIncome > 0 ? 'accent' : ''}`.trim();
    const surplusRow = document.getElementById('cj-plan-surplus');
    if (surplusRow) surplusRow.className = `cj-plan-row-value ${calc.monthlySurplus >= 0 ? 'pos' : 'neg'}`;

    const actions = document.getElementById('cj-plan-actions-list');
    if (actions) {
      actions.innerHTML = calc.actions
        .map((item) => {
          const lower = String(item || '').toLowerCase();
          const cls = lower.includes('phv') ? ' emphasis' : lower.includes('stay') ? ' good' : '';
          return `<div class="cj-plan-action-chip${cls}">${escapeHtml(String(item || '').toUpperCase())}</div>`;
        })
        .join('');
    }
  }

  async function _cjLoadPlanner() {
    try {
      const cached = localStorage.getItem('cj_financial_plan');
      if (cached) {
        _cjPlanner = _cjNormalizePlanner(JSON.parse(cached));
        _cjRenderPlanner();
      }
    } catch (e) {}
    try {
      const r = await fetch('/api/financial-plan', { credentials: 'same-origin' });
      const d = await r.json();
      _cjPlanner = _cjNormalizePlanner(d.plan || {});
      try { localStorage.setItem('cj_financial_plan', JSON.stringify(_cjPlanner)); } catch (e) {}
    } catch (e) {
      console.warn('[Journal] Financial plan load failed:', e);
      if (!_cjPlanner) _cjPlanner = _cjDefaultPlanner(new Date().getFullYear());
    }
    _cjRenderPlanner();
  }

  async function _cjSavePlanner() {
    if (!_cjPlanner) return;
    try { localStorage.setItem('cj_financial_plan', JSON.stringify(_cjPlanner)); } catch (e) {}
    try {
      await fetch('/api/financial-plan', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(_cjPlanner),
      });
      const dot = document.getElementById('cj-save-dot');
      dot.classList.add('show');
      setTimeout(() => dot.classList.remove('show'), 1500);
    } catch (e) {
      console.warn('[Journal] Financial plan save failed:', e);
    }
  }

  window._cjPlannerFieldChanged = function(input, commit) {
    if (!input) return;
    _cjPlanner = _cjNormalizePlanner(_cjPlanner || {});
    const mapping = {
      'cj-plan-monthly-expense': 'monthly_expense',
      'cj-plan-assets-value': 'assets_value',
      'cj-plan-years-reserve': 'years_to_reserve',
      'cj-plan-years-ffv': 'years_to_ffv',
      'cj-plan-monthly-income': 'monthly_income',
      'cj-plan-phv-increase': 'phv_increase',
    };
    const field = mapping[input.id];
    if (!field) return;
    const value = Number(input.value || 0);
    _cjPlanner[field] = Number.isFinite(value) ? value : 0;
    if (field === 'years_to_reserve' || field === 'years_to_ffv') {
      _cjPlanner[field] = Math.max(1, Math.round(_cjPlanner[field]));
    } else {
      _cjPlanner[field] = Math.max(0, _cjPlanner[field]);
    }
    _cjApplyPlannerComputed(_cjPlanCalc());
    if (commit) input.value = String(Math.round(_cjPlanner[field]));
    clearTimeout(_cjPlanTimer);
    _cjPlanTimer = setTimeout(() => _cjSavePlanner(), 500);
  };

  window._cjShiftPlanYear = function(delta) {
    return;
  };

  window._cjShowPanel = function(mode) {
    _cjPanelMode = mode === 'plan' ? 'plan' : 'journal';
    const journal = document.getElementById('cj-journal-view');
    const plan = document.getElementById('cj-plan-view');
    const journalTab = document.getElementById('cj-tab-journal');
    const planTab = document.getElementById('cj-tab-plan');
    const shell = document.getElementById('charts-shell');
    const journalSelected = _cjPanelMode === 'journal';
    if (journal) {
      journal.classList.toggle('active', journalSelected);
      journal.setAttribute('aria-hidden', String(!journalSelected));
    }
    if (plan) {
      plan.classList.toggle('active', !journalSelected);
      plan.setAttribute('aria-hidden', String(journalSelected));
    }
    if (journalTab) {
      journalTab.classList.toggle('active', journalSelected);
      journalTab.setAttribute('aria-selected', String(journalSelected));
      journalTab.tabIndex = journalSelected ? 0 : -1;
    }
    if (planTab) {
      planTab.classList.toggle('active', !journalSelected);
      planTab.setAttribute('aria-selected', String(!journalSelected));
      planTab.tabIndex = journalSelected ? -1 : 0;
    }
    if (shell) shell.classList.toggle('planner-mode', _cjPanelMode === 'plan');
    if (_cjPanelMode === 'plan' && !_cjPlanner) _cjLoadPlanner();
  };

  // ══════════════════════════════════════════════════════════
  //  JOURNAL — auto-save on change, persist to backend + localStorage
  // ══════════════════════════════════════════════════════════
  function _cjBindForm() {
    const fields = ['cj-asset', 'cj-strategy', 'cj-well', 'cj-improve', 'cj-mental', 'cj-strategy-custom'];
    fields.forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('input', _cjScheduleSave);
    });
    document.querySelectorAll('input[name="cj-grade"]').forEach(r => {
      r.addEventListener('change', _cjScheduleSave);
    });
  }

  function _cjScheduleSave() {
    if (!_cjCurrentDate) return;
    clearTimeout(_cjSaveTimer);
    const dateStr = _cjCurrentDate;
    const snapshot = _cjGetFormData();
    _cjSaveTimer = setTimeout(() => _cjSaveJournal(dateStr, snapshot), 800);
  }

  function _cjGetFormData() {
    const grade = document.querySelector('input[name="cj-grade"]:checked');
    const sel = document.getElementById('cj-strategy').value || '';
    const custom = (document.getElementById('cj-strategy-custom').value || '').trim().slice(0, 100);
    return {
      asset: (document.getElementById('cj-asset').value || '').slice(0, 100),
      strategy: sel === 'Other' && custom ? custom : sel,
      strategy_custom: sel === 'Other' ? custom : '',
      grade: grade ? grade.value : '',
      went_well: (document.getElementById('cj-well').value || '').slice(0, 2000),
      to_improve: (document.getElementById('cj-improve').value || '').slice(0, 2000),
      mental_state: document.getElementById('cj-mental').value || '',
    };
  }

  function _cjSetFormData(d) {
    document.getElementById('cj-asset').value = d.asset || '';
    const customInput = document.getElementById('cj-strategy-custom');
    if (d.strategy_custom) {
      document.getElementById('cj-strategy').value = 'Other';
      customInput.value = d.strategy_custom;
      customInput.style.display = '';
    } else {
      const sel = document.getElementById('cj-strategy');
      sel.value = d.strategy || '';
      if (!sel.value && d.strategy) { sel.value = 'Other'; customInput.value = d.strategy; customInput.style.display = ''; }
      else { customInput.value = ''; customInput.style.display = 'none'; }
    }
    document.getElementById('cj-well').value = d.went_well || '';
    document.getElementById('cj-improve').value = d.to_improve || '';
    document.getElementById('cj-mental').value = d.mental_state || '';
    document.querySelectorAll('input[name="cj-grade"]').forEach(r => {
      r.checked = r.value === (d.grade || '');
    });
  }

  function _cjClearForm() {
    document.getElementById('cj-asset').value = '';
    document.getElementById('cj-strategy').value = '';
    document.getElementById('cj-strategy-custom').value = '';
    document.getElementById('cj-strategy-custom').style.display = 'none';
    document.getElementById('cj-well').value = '';
    document.getElementById('cj-improve').value = '';
    document.getElementById('cj-mental').value = '';
    document.querySelectorAll('input[name="cj-grade"]').forEach(r => r.checked = false);
  }

  async function _cjLoadJournal(dateStr) {
    const generation = ++_cjLoadGeneration;
    if (_cjLoadController) _cjLoadController.abort();
    const controller = new AbortController();
    _cjLoadController = controller;
    _cjClearForm();
    const lsKey = 'cj_journal_' + dateStr;
    try {
      const cached = localStorage.getItem(lsKey);
      if (cached && _cjCurrentDate === dateStr) _cjSetFormData(JSON.parse(cached));
    } catch(e) {}
    try {
      const r = await fetch('/api/journal/' + encodeURIComponent(dateStr), {
        credentials: 'same-origin',
        signal: controller.signal,
      });
      if (!r.ok) throw new Error('Journal load failed (' + r.status + ')');
      const d = await r.json();
      if (generation !== _cjLoadGeneration || _cjCurrentDate !== dateStr) return;
      if (d.data) {
        _cjSetFormData(d.data);
        try { localStorage.setItem(lsKey, JSON.stringify(d.data)); } catch(e) {}
      }
    } catch(e) {
      if (e.name !== 'AbortError') console.warn('[Journal] Backend load failed, using localStorage:', e);
    } finally {
      if (_cjLoadController === controller) _cjLoadController = null;
    }
  }

  async function _cjSaveJournal(dateStr, dataOverride = null) {
    const data = dataOverride || _cjGetFormData();
    try { localStorage.setItem('cj_journal_' + dateStr, JSON.stringify(data)); } catch(e) {}
    const previous = _cjSaveChains.get(dateStr) || Promise.resolve();
    const task = previous.catch(() => {}).then(async () => {
      const r = await fetch('/api/journal/' + encodeURIComponent(dateStr), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify(data),
      });
      if (!r.ok) {
        let detail = 'Journal save failed (' + r.status + ')';
        try {
          const payload = await r.json();
          detail = payload.detail || payload.error?.detail || detail;
        } catch(e) {}
        throw new Error(detail);
      }
      return true;
    });
    _cjSaveChains.set(dateStr, task);
    try {
      await task;
      const dot = document.getElementById('cj-save-dot');
      if (dot) {
        dot.classList.add('show');
        setTimeout(() => dot.classList.remove('show'), 1500);
      }
      return true;
    } catch(e) {
      console.warn('[Journal] Backend save failed:', e);
      return false;
    } finally {
      if (_cjSaveChains.get(dateStr) === task) _cjSaveChains.delete(dateStr);
    }
  }

  // ══════════════════════════════════════════════════════════
  //  MANUAL SAVE BUTTON
  // ══════════════════════════════════════════════════════════
  window._cjManualSave = async function() {
    if (!_cjCurrentDate) {
      await customConfirm('Select a date first from the chart sidebar.', { title: 'No Date Selected', icon: ICO.calendar(24), okText: 'OK', cancelText: '' });
      return;
    }
    clearTimeout(_cjSaveTimer);
    const btn = document.getElementById('cj-submit-btn');
    btn.innerHTML = ICO.hour(14) + ' Saving...';
    btn.disabled = true;
    const dateStr = _cjCurrentDate;
    const saved = await _cjSaveJournal(dateStr, _cjGetFormData());
    btn.innerHTML = saved ? ICO.check(14) + ' Saved!' : '⚠ Save failed';
    if (!saved) showToast('Journal remains in this browser, but the server save failed.', 'error');
    setTimeout(() => { btn.innerHTML = ICO.save(14) + ' Save Journal'; btn.disabled = false; }, 1500);
    if (saved) _cjLoadEntries();
  };

  // ══════════════════════════════════════════════════════════
  //  PASTE UPLOAD — Ctrl+V pastes screenshot
  // ══════════════════════════════════════════════════════════
  function _cjBindPaste() {
    document.addEventListener('paste', async function(e) {
      const page = document.getElementById('charts-page');
      if (!page || !page.classList.contains('active-page')) return;

      const items = (e.clipboardData || {}).items;
      if (!items) return;

      let imageFile = null;
      for (let i = 0; i < items.length; i++) {
        if (items[i].type.startsWith('image/')) {
          imageFile = items[i].getAsFile();
          break;
        }
      }
      if (!imageFile) return;

      e.preventDefault();
      const toast = document.getElementById('cj-toast');
      toast.textContent = '📤 Uploading chart...';
      toast.classList.add('show');

      try {
        const fd = new FormData();
        fd.append('file', imageFile, 'screenshot.png');
        const uploadTarget = _cjCurrentDayMeta || _chBuildTargetFromDate(_cjCurrentDate) || _chBuildTargetFromDate(_chTodayIso());
        if (uploadTarget) {
          fd.append('target_year', uploadTarget.year);
          fd.append('target_month', uploadTarget.monthFolder);
          fd.append('target_day', uploadTarget.dayFolder);
        }
        const r = await fetch('/api/upload-chart', {
          method: 'POST',
          credentials: 'same-origin',
          body: fd,
        });
        if (!r.ok) {
          const ct = r.headers.get('content-type') || '';
          if (ct.includes('application/json')) {
            const err = await r.json();
            throw new Error(err.detail || 'Upload failed (' + r.status + ')');
          }
          throw new Error('Server error ' + r.status + (r.status === 413 ? ' — file too large' : ''));
        }
        const d = await r.json();
        if (d.status !== 'ok') throw new Error(d.detail || d.message || 'Upload failed');

        await _chLoadTree();
        const uploadedDay = _chFindDayByFolders(d.year, d.month_folder, d.day_folder);
        if (uploadedDay) {
          const uploadedBtn = _chFindDayButton(uploadedDay);
          await window._chSelectDay(uploadedDay.year, uploadedDay.monthFolder, uploadedDay.dayFolder, uploadedDay.dateLabel, uploadedDay.sortKey, uploadedBtn);
        } else {
          _cjCurrentDayMeta = { year: d.year, monthFolder: d.month_folder, dayFolder: d.day_folder };
          _chDateLabel = (uploadTarget && uploadTarget.dateLabel) || _chDateLabel;
          _chImages = [{ name: d.filename, url: d.url }];
          _chRenderImages();
        }

        toast.textContent = '✅ Chart saved: ' + d.filename;
      } catch(err) {
        toast.textContent = '❌ Upload failed: ' + err.message;
      }

      setTimeout(() => {
        toast.classList.remove('show');
        toast.textContent = '📤 Uploading chart...';
      }, 2500);
    });
  }

  // ══════════════════════════════════════════════════════════
  //  JOURNAL ENTRIES — collapsible month tree
  // ══════════════════════════════════════════════════════════
  async function _cjLoadEntries() {
    const el = document.getElementById('cj-entries-list');
    if (!el) return;
    try {
      const r = await fetch('/api/journal/list', { credentials: 'same-origin' });
      const d = await r.json();
      const entries = d.entries || [];
      if (!entries.length) { el.innerHTML = ''; return; }

      // Group entries by YYYY-MM
      const months = {};
      const monthNames = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
      entries.forEach(e => {
        const [y, m] = e.date.split('-');
        const key = y + '-' + m;
        if (!months[key]) months[key] = { label: monthNames[parseInt(m)] + ' ' + y, entries: [] };
        months[key].entries.push(e);
      });

      let h = '<div class="cj-entries-title">' + ICO.clip(12) + ' Journal Entries</div>';
      const keys = Object.keys(months).sort().reverse();
      const selectedMonthKey = String(_cjCurrentDate || '').slice(0, 7);
      keys.forEach((key, ki) => {
        const mo = months[key];
        const isOpen = key === selectedMonthKey ? ' open' : '';
        h += '<div>';
        h += '<button class="cj-month-toggle' + isOpen + '" onclick="this.classList.toggle(\'open\');this.nextElementSibling.classList.toggle(\'open\')"><span class="arrow">▶</span>' + escapeHtml(mo.label) + '<span class="cnt">' + mo.entries.length + '</span></button>';
        h += '<div class="cj-month-children' + isOpen + '">';
        mo.entries.forEach(e => {
          const entryDate = String(e.date || '');
          const day = entryDate.split('-')[2] || '—';
          const active = entryDate === _cjCurrentDate ? ' active' : '';
          const grade = ['A', 'B', 'C', 'D'].includes(e.grade) ? e.grade : '';
          const gradeEl = grade ? '<span class="cj-entry-grade g-' + grade + '">' + grade + '</span>' : '';
          const safeDateJs = escapeJsAttr(entryDate);
          h += '<div class="cj-entry-item' + active + '" data-date="' + escapeAttr(entryDate) + '" onclick="window._cjSelectEntry(\'' + safeDateJs + '\')">';
          h += '<span class="cj-entry-date">' + escapeHtml(day) + '</span>';
          h += '<span class="cj-entry-asset">' + escapeHtml(e.asset || e.strategy || '—') + '</span>';
          h += gradeEl;
          h += '<button class="cj-entry-del" title="Delete entry" onclick="event.stopPropagation();window._cjDeleteEntry(\'' + safeDateJs + '\')">✕</button>';
          h += '</div>';
        });
        h += '</div></div>';
      });
      el.innerHTML = h;
    } catch(e) {
      console.warn('[Journal] Entries load failed:', e);
    }
  }

  window._cjStrategyChanged = function() {
    const sel = document.getElementById('cj-strategy');
    const custom = document.getElementById('cj-strategy-custom');
    if (sel.value === 'Other') { custom.style.display = ''; custom.focus(); }
    else { custom.style.display = 'none'; custom.value = ''; }
    _cjScheduleSave();
  };

  window._cjSelectEntry = async function(dateStr) {
    _cjCurrentDate = dateStr;
    const matchingDay = _chFindDayBySortKey(dateStr);
    if (matchingDay) {
      const matchingBtn = _chFindDayButton(matchingDay);
      await window._chSelectDay(matchingDay.year, matchingDay.monthFolder, matchingDay.dayFolder, matchingDay.dateLabel, matchingDay.sortKey, matchingBtn);
      return;
    }

    _cjCurrentDayMeta = null;
    _chImages = [];
    const derivedTarget = _chBuildTargetFromDate(dateStr);
    _chDateLabel = derivedTarget ? derivedTarget.dateLabel : dateStr;
    _chRenderImages();
    await _cjLoadJournal(dateStr);
    document.getElementById('cj-date-label').textContent = dateStr;
    document.querySelectorAll('.chday-btn.active').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.cj-entry-item').forEach(el => {
      el.classList.toggle('active', el.dataset.date === dateStr);
    });
  };

  window._cjDeleteEntry = async function(dateStr) {
    const ok = await customConfirm('Delete journal entry for <strong>' + dateStr + '</strong>?<br><span style="font-size:11px;">This cannot be undone.</span>', {
      title: 'Delete Journal Entry',
      icon: '🗑️',
      okText: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      const r = await fetch('/api/journal/' + dateStr, { method: 'DELETE', credentials: 'same-origin' });
      if (!r.ok) throw new Error('Failed (' + r.status + ')');
      try { localStorage.removeItem('cj_journal_' + dateStr); } catch(e) {}
      if (_cjCurrentDate === dateStr) _cjClearForm();
      _cjLoadEntries();
    } catch(e) {
      await customConfirm(e.message, { title: 'Error', icon: '❌', okText: 'OK', cancelText: '' });
    }
  };
})();

// ══ TEST BENCH ══════════════════════════════════════════════
// One mother candle, replayed and shown. The chart itself lives in
// philforge-bench-chart.js (the CryptoForge Canvas renderer, ported); this is
// only the screen around it: pick, run, read.

const _TB_LEVELS_BY_TF = { '1m': 'L4 · L8', '5m': 'L4 · L8', '15m': 'L2 · L4 · L8', '1h': 'L2 · L4 · L8' };

// What each strategy does with the timeframe you pick. Fib reads levels off the
// mother; Two Red starts on that chart and climbs to the next one after every
// buy — so a 1H start has nowhere left to climb and is a single trade.
const _TB_LADDER = ['1m', '5m', '15m', '1h'];
const _TB_TF_LABEL = { '1m': '1m', '5m': '5m', '15m': '15m', '1h': '1H' };

function _tbLadderFrom(timeframe) {
  const start = _TB_LADDER.indexOf(timeframe);
  return start < 0 ? [] : _TB_LADDER.slice(start, start + 4);
}

const _TB_STRATEGY_COPY = {
  fib: 'Draws the trendline and fib levels from the mother candle, then buys each deep level the market falls through. The whole basket leaves on the first target, or at expiry.',
  two_red: 'Waits for two red candles to close, then puts a buy-stop at the FIRST red’s close. Once it fills, it marks the low, climbs to the next timeframe and waits for two reds again — 1, 2, 3 then 4 lots. One mother, one trade.',
};

function _tbRenderTimeframes() {
  const select = document.getElementById('tb-timeframe');
  if (!select) return;
  const strategy = document.getElementById('tb-strategy')?.value || 'fib';
  const chosen = select.value || '5m';
  select.innerHTML = _TB_LADDER.map((tf) => {
    const detail = strategy === 'two_red'
      ? _tbLadderFrom(tf).map((step) => _TB_TF_LABEL[step]).join(' → ')
      : `buys ${_TB_LEVELS_BY_TF[tf]}`;
    return `<option value="${tf}"${tf === chosen ? ' selected' : ''}>${_TB_TF_LABEL[tf]} · ${detail}</option>`;
  }).join('');
  select.value = chosen;
  // The rupee-per-level budget is a fib idea. The ladder sizes itself 1/2/3/4
  // lots, so showing a cash box there would imply a control that does nothing.
  const rung = document.getElementById('tb-rung-field');
  if (rung) rung.style.display = strategy === 'two_red' ? 'none' : '';
  const explainer = document.getElementById('tb-explainer');
  if (explainer) explainer.textContent = _TB_STRATEGY_COPY[strategy] || '';
}

// The minutes a candle of each timeframe can actually open on. NSE sessions
// start at 09:15, so every bar is offset by 15: a 1H bar opens at :15 and
// nothing else, a 15m bar at :00/:15/:30/:45. Offering a minute that can never
// be a candle open only produces "Dhan has no candle at that time".
//
// Shared by all three mother pickers -- Test Bench, candle-entry and
// fib-boundary. The fib tab was the one that never got wired to it, so its
// picker offered all 60 minutes on every timeframe and 14:17 looked selectable
// on a 15m chart.
const _MOTHER_MINUTES_BY_TF = {
  '1m': Array.from({ length: 60 }, (_, i) => i).join(','),
  '5m': [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55].join(','),
  '15m': [0, 15, 30, 45].join(','),
  '1h': '15',
};

function _tbSyncCalendarToTimeframe() {
  const mother = document.getElementById('tb-mother');
  const timeframe = document.getElementById('tb-timeframe')?.value || '5m';
  if (mother) mother.dataset.pfCalendarMinutes = _MOTHER_MINUTES_BY_TF[timeframe] || '';
}

function initTestBenchPage() {
  const mother = document.getElementById('tb-mother');
  // Default to a mother that certainly has candles after it: yesterday's 10:15,
  // which is inside a normal session and old enough to have priced history.
  if (mother && !mother.value) {
    const d = new Date(Date.now() - 86400000);
    const pad = (n) => String(n).padStart(2, '0');
    mother.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T10:15`;
  }
  _tbRenderTimeframes();
  _tbSyncCalendarToTimeframe();
  _tbLoadSaved(1);
}

function _tbStatus(message, kind) {
  const el = document.getElementById('tb-status');
  if (!el) return;
  el.textContent = message || '';
  el.className = 'tb-status' + (kind ? ' is-' + kind : '');
}

function _tbInr(value) {
  const n = Number(value);
  if (!isFinite(n)) return '—';
  // Sign outside the symbol: "−₹605", not "₹-605".
  const sign = n < 0 ? '−' : '';
  return sign + '₹' + Math.round(Math.abs(n)).toLocaleString('en-IN');
}

function _tbTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
}

async function runTestBench(event, el) {
  const button = el || document.getElementById('tb-run');
  const timeframe = document.getElementById('tb-timeframe')?.value || '5m';
  const payload = {
    force: _tbForceNextRun,
    instrument: document.getElementById('tb-instrument')?.value || 'NIFTY',
    strategy: document.getElementById('tb-strategy')?.value || 'fib',
    timeframe,
    mother_timestamp: document.getElementById('tb-mother')?.value || '',
    rung_inr: Number(document.getElementById('tb-rung')?.value || 25000),
  };
  if (!payload.mother_timestamp) {
    _tbStatus('Pick a mother candle date and time first.', 'error');
    return;
  }
  if (button) { button.disabled = true; button.textContent = 'Running…'; }
  _tbStatus(`Fetching the ${payload.instrument} ${timeframe} candle and replaying it…`, 'busy');
  try {
    const response = await fetch('/api/test-bench/run', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    // The server wraps errors as { error: { detail, message } }, and only 4xx
    // carry `detail` — the specific, useful sentence. Reading a top-level
    // `data.detail` (as older code here does) finds nothing, shows
    // "Run failed (400)" and throws away what is usually the whole answer.
    if (!response.ok || data.status !== 'ok') {
      throw new Error(data?.error?.detail || data?.error?.message || data?.detail || `Run failed (${response.status})`);
    }
    _tbRender(data);
    if (data.duplicate) {
      _tbStatus(`Already in your saved runs (replayed ${_tbTime(data.stored_at)}). Showing the stored result — press Run again to replay it fresh.`, 'busy');
      _tbForceNextRun = true;
    } else {
      _tbStatus('', '');
      _tbForceNextRun = false;
    }
    _tbLoadSaved();
  } catch (error) {
    document.getElementById('tb-results')?.setAttribute('hidden', '');
    _tbStatus(error.message || 'Run failed.', 'error');
  } finally {
    if (button) { button.disabled = false; button.textContent = 'Run'; }
  }
}

// The last run, kept so the chart can be drawn on demand — the strip carries a
// chart BUTTON like every other panel, rather than always painting the canvas.
let _lastTestBenchRun = null;

function _tbRender(data) {
  _lastTestBenchRun = data;
  const results = document.getElementById('tb-results');
  if (results) results.removeAttribute('hidden');
  const strip = document.getElementById('tb-result-strip');
  if (strip) strip.open = true;
  // Each fresh run starts with the chart folded away, same as the fib panel.
  const box = document.getElementById('tb-chart-box');
  const btn = document.getElementById('tb-chart-btn');
  if (box) box.style.display = 'none';
  if (btn) btn.textContent = '↗ Chart';
  if (typeof _pfChartCanvasTeardown === 'function') _pfChartCanvasTeardown();
  const host = document.getElementById('tb-chart');
  if (host) host.innerHTML = '';
  _tbRenderVerdict(data);
  _tbRenderEntries(data);
}

function toggleTestBenchChart(event) {
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  const box = document.getElementById('tb-chart-box');
  const btn = document.getElementById('tb-chart-btn');
  const host = document.getElementById('tb-chart');
  if (!box || !host) return;
  if (box.style.display !== 'none') {
    box.style.display = 'none';
    host.innerHTML = '';
    if (typeof _pfChartCanvasTeardown === 'function') _pfChartCanvasTeardown();
    if (btn) btn.textContent = '↗ Chart';
    return;
  }
  const chart = _lastTestBenchRun && _lastTestBenchRun.chart;
  if (!chart) return;
  // Visible first, then drawn: the canvas sizes itself from its host, and a
  // display:none host measures zero.
  box.style.display = '';
  if (typeof pfBenchDrawChart === 'function') pfBenchDrawChart(host, chart);
  if (btn) btn.textContent = '× Hide chart';
}

function _tbRenderVerdict(data) {
  const el = document.getElementById('tb-verdict');
  if (!el) return;
  const s = data.summary || {};
  const traded = Number(s.entry_count) > 0;
  const pnl = Number(s.net_pnl);
  const open = !!s.still_open;
  const targetHit = String(s.exit_reason || '').toLowerCase().startsWith('target');
  const GOOD = '#6ee7b7', BAD = '#fca5a5', WAIT = 'var(--warn)';

  const badge = document.getElementById('tb-outcome-badge');
  if (badge) {
    const text = !traded ? 'NO BUY' : open ? 'STILL OPEN' : targetHit ? 'TARGET HIT' : 'ENDED';
    const tone = !traded ? 'var(--muted)' : open ? WAIT : targetHit ? GOOD : (isFinite(pnl) && pnl >= 0 ? GOOD : BAD);
    badge.textContent = text;
    badge.style.color = tone;
    badge.style.borderColor = tone;
  }
  const contractEl = document.getElementById('tb-contract');
  if (contractEl) {
    contractEl.textContent = traded && s.strike
      ? `${s.underlying || ''} ${s.strike} ${s.option_type || ''} · expiry ${s.expiry || '—'} · lot ${s.lot_size || '—'}`
      : 'No contract was bought';
  }
  const gist = document.getElementById('tb-gist');
  if (gist) gist.textContent = `${s.instrument || ''} · ${String(s.timeframe || '').toUpperCase()} mother ${_tbTime(s.mother_timestamp)} — ${s.outcome || ''}`;

  const netColor = !traded || !isFinite(pnl) ? 'var(--muted)' : pnl > 0 ? GOOD : pnl < 0 ? BAD : 'var(--text)';
  // Phil asked for the target numbers in colour: green once reached, amber
  // while the trade is still watching it, muted when it never got there.
  const targetColor = targetHit ? GOOD : open ? WAIT : 'var(--muted)';
  el.innerHTML = [
    _cascadeOptionsMetric('Outcome', String(s.outcome || '—'), open ? WAIT : targetHit ? GOOD : 'var(--text)'),
    _cascadeOptionsMetric('First buy', _tbTime(s.entry_timestamp)),
    _cascadeOptionsMetric('Closed', open ? 'OPEN — not yet' : _tbTime(s.exit_timestamp), open ? WAIT : 'var(--text)'),
    _cascadeOptionsMetric('Target idx', s.target_index == null ? '—' : Number(s.target_index).toLocaleString('en-IN'), targetColor),
    _cascadeOptionsMetric('Avg entry idx', s.average_spot == null ? '—' : Number(s.average_spot).toLocaleString('en-IN')),
    _cascadeOptionsMetric('Buys', traded ? String(s.entry_count) : '0'),
    _cascadeOptionsMetric('Spent on premium', traded ? _tbInr(s.spend_inr) : '—'),
    _cascadeOptionsMetric('Costs', traded ? _tbInr(s.costs_total) : '—', traded ? BAD : 'var(--muted)'),
    _cascadeOptionsMetric('Net P&L', traded && isFinite(pnl) ? _tbInr(pnl) : open ? '— still open' : '—', netColor),
  ].join('');

  // A gap is stated, never averaged away: a spend total that quietly skipped an
  // unpriced leg is the one number here that would mislead without looking wrong.
  const warn = document.getElementById('tb-warn');
  if (warn) {
    warn.innerHTML = Number(s.unpriced_entries) > 0
      ? `<p class="tb-warn">${s.unpriced_entries} buy(s) had no price in the option history, so the money figures above cover only the priced ones.</p>`
      : '';
  }
}

function _tbRenderEntries(data) {
  const table = document.getElementById('tb-entries');
  if (!table) return;
  const rows = data.entries || [];
  if (!rows.length) {
    table.innerHTML = '<tbody><tr><td class="tb-none">Nothing was bought — the index never reached a fib level for this mother.</td></tr></tbody>';
    return;
  }
  const ladder = data.strategy === 'two_red';
  const head = [ladder ? 'Buy' : 'Level', 'Time', 'Index at', 'Strike', 'Price paid', 'Lots', 'Qty', 'Spent'];
  const body = rows.map((row) => {
    const spend = row.spend_inr == null ? '<span class="tb-gap">no price</span>' : _tbInr(row.spend_inr);
    // A ladder rung is named by the chart it was read on, which is the whole
    // point of the strategy; a fib entry is named by the level it sat on.
    const label = row.level == null ? '—'
      : (ladder ? `#${row.level} · ${_TB_TF_LABEL[row.timeframe] || row.timeframe || ''}` : 'L' + row.level);
    return `<tr>
      <td>${escapeHtml(label)}</td>
      <td>${_tbTime(row.timestamp)}</td>
      <td>${row.spot == null ? '—' : Number(row.spot).toLocaleString('en-IN')}</td>
      <td>${escapeHtml(String(row.strike ?? '—'))} ${escapeHtml(String(row.option_type || ''))}</td>
      <td>${row.option_price == null ? '<span class="tb-gap">no price</span>' : _tbInr(row.option_price)}</td>
      <td>${escapeHtml(String(row.lots ?? '—'))}</td>
      <td>${escapeHtml(String(row.quantity ?? '—'))}</td>
      <td>${spend}</td>
    </tr>`;
  }).join('');
  table.innerHTML = `<thead><tr>${head.map((h) => `<th>${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody>`;
}

// Pressing Run on a question that already has a stored answer shows the
// stored one; pressing Run again replays it for real.
let _tbForceNextRun = false;
let _tbSavedPage = 1;
let _tbSearchTimer = null;

async function _tbLoadSaved(page) {
  const table = document.getElementById('tb-saved-table');
  const pager = document.getElementById('tb-pager');
  const count = document.getElementById('tb-saved-count');
  if (!table) return;
  if (page) _tbSavedPage = page;
  const search = document.getElementById('tb-search')?.value || '';
  let data;
  try {
    const query = new URLSearchParams({ search, page: String(_tbSavedPage), per_page: '10' });
    const response = await fetch(`/api/test-bench/results?${query}`, { credentials: 'same-origin' });
    data = await response.json();
    if (!response.ok || data.status !== 'ok') throw new Error('could not load');
  } catch (error) {
    table.innerHTML = '<tbody><tr><td class="tb-none">Saved runs are unavailable right now.</td></tr></tbody>';
    if (pager) pager.innerHTML = '';
    return;
  }
  if (count) count.textContent = String(data.total);
  const rows = data.rows || [];
  if (!rows.length) {
    table.innerHTML = `<tbody><tr><td class="tb-none">${search ? 'Nothing matches that search.' : 'Runs you make are saved here automatically.'}</td></tr></tbody>`;
    if (pager) pager.innerHTML = '';
    return;
  }
  const head = ['Mother', 'Instrument', 'Strategy', 'Chart', 'Per level', 'Outcome', 'Buys', 'Net P&L', ''];
  const body = rows.map((row) => {
    const pnl = Number(row.net_pnl);
    const tone = !isFinite(pnl) ? '' : pnl > 0 ? ' style="color:#6ee7b7;"' : pnl < 0 ? ' style="color:#fca5a5;"' : '';
    const open = String(row.outcome || '').toUpperCase().includes('OPEN');
    return `<tr>
      <td><a href="#" data-pf-action="openSavedTestBenchRun" data-tb-run="${row.id}" style="color:#38bdf8;">${_tbTime(row.mother_timestamp)}</a></td>
      <td>${escapeHtml(String(row.instrument || ''))}</td>
      <td>${escapeHtml(row.strategy === 'two_red' ? 'Two red' : 'Fib levels')}</td>
      <td>${escapeHtml(_TB_TF_LABEL[row.timeframe] || row.timeframe || '')}</td>
      <td>${_tbInr(row.rung_inr)}</td>
      <td${open ? ' style="color:var(--warn);"' : ''}>${escapeHtml(String(row.outcome || '—'))}</td>
      <td>${escapeHtml(String(row.entry_count ?? 0))}</td>
      <td${tone}>${isFinite(pnl) ? _tbInr(pnl) : '—'}</td>
      <td><button class="btn btn-sm cascade-options-control" type="button" data-pf-action="deleteSavedTestBenchRun" data-tb-run="${row.id}" aria-label="Delete this saved run">×</button></td>
    </tr>`;
  }).join('');
  table.innerHTML = `<thead><tr>${head.map((h) => `<th>${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody>`;
  if (pager) {
    pager.innerHTML = data.pages <= 1 ? '' : `
      <button class="btn btn-sm cascade-options-control" type="button" data-pf-action="pageSavedTestBenchRuns" data-tb-page="${Math.max(1, data.page - 1)}" ${data.page <= 1 ? 'disabled' : ''}>‹ Prev</button>
      <span>Page ${data.page} of ${data.pages} · ${data.total} run${data.total === 1 ? '' : 's'}</span>
      <button class="btn btn-sm cascade-options-control" type="button" data-pf-action="pageSavedTestBenchRuns" data-tb-page="${Math.min(data.pages, data.page + 1)}" ${data.page >= data.pages ? 'disabled' : ''}>Next ›</button>`;
  }
}

function pageSavedTestBenchRuns(event, el) {
  _tbLoadSaved(Number(el?.getAttribute('data-tb-page')) || 1);
}

async function openSavedTestBenchRun(event, el) {
  const id = el?.getAttribute('data-tb-run');
  if (!id) return;
  _tbStatus('Opening the saved run…', 'busy');
  try {
    const response = await fetch(`/api/test-bench/results/${id}`, { credentials: 'same-origin' });
    const data = await response.json();
    if (!response.ok || data.status !== 'ok') throw new Error(data?.error?.detail || 'Could not open that run.');
    _tbRender(data);
    _tbStatus(`Saved run from ${_tbTime(data.stored_at)}.`, '');
    _tbForceNextRun = true;
  } catch (error) {
    _tbStatus(error.message || 'Could not open that run.', 'error');
  }
}

async function deleteSavedTestBenchRun(event, el) {
  const id = el?.getAttribute('data-tb-run');
  if (!id) return;
  const ok = await customConfirm('Delete this saved run? The mother candle can always be replayed again.', { title: 'Delete saved run', icon: ICO.warn(28), okText: 'Delete', danger: true });
  if (!ok) return;
  await fetch(`/api/test-bench/results/${id}`, { method: 'DELETE', credentials: 'same-origin' });
  _tbLoadSaved();
}

document.addEventListener('input', (event) => {
  if (event.target && event.target.id === 'tb-search') {
    clearTimeout(_tbSearchTimer);
    _tbSearchTimer = setTimeout(() => _tbLoadSaved(1), 250);
  }
});

document.addEventListener('change', (event) => {
  const id = event.target && event.target.id;
  if (id === 'tb-timeframe') {
    _tbSyncCalendarToTimeframe();
    _tbRenderTimeframes();
  } else if (id === 'tb-strategy') {
    _tbRenderTimeframes();
  } else if (id === 'candle-entry-timeframe') {
    _ceRenderTimeframes();
  }
  if (['tb-instrument', 'tb-strategy', 'tb-timeframe', 'tb-mother', 'tb-rung'].includes(id)) {
    _tbForceNextRun = false;
  }
});

/* ⓘ info reveals: the button names its target by id, so the prose can sit
   wherever the layout wants it rather than being forced adjacent. */
document.addEventListener('click', (event) => {
  const btn = event.target.closest('.pf-info-btn');
  if (!btn) return;
  const target = document.getElementById(btn.getAttribute('data-pf-info') || '');
  if (!target) return;
  const open = target.classList.toggle('is-open');
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
});

// ── Recovery (two reds, stop on the entry candle low) ─────────────
// Phil's stop-loss recovery rules. The panel is a monitor: mothers are named by
// hand, the engine replays each one from its own mother on every poll, and the
// target is a RUPEE threshold on the ledger — never a price level, so there is
// no target line to draw.

let _recoveryTimer = null;

function _recNum(v, dp) {
  const n = Number(v);
  if (!isFinite(n)) return '—';
  return n.toLocaleString('en-IN', { minimumFractionDigits: dp || 0, maximumFractionDigits: dp || 0 });
}

function _recInr(v) {
  const n = Number(v);
  if (v === null || v === undefined || !isFinite(n)) return '—';
  const sign = n < 0 ? '−' : '+';
  return `${sign}₹${Math.abs(Math.round(n)).toLocaleString('en-IN')}`;
}

function _recTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return `${String(d.getDate()).padStart(2, '0')} ${d.toLocaleString('en-IN', { month: 'short' })} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function _recoveryError(msg) {
  const box = document.getElementById('recovery-error');
  if (!box) return;
  box.textContent = msg || '';
  box.style.display = msg ? '' : 'none';
}

async function recoveryStart() {
  _recoveryError('');
  const body = {
    symbol: document.getElementById('recovery-symbol')?.value || 'nifty',
    timeframe: document.getElementById('recovery-timeframe')?.value || '15m',
    mode: document.getElementById('recovery-mode')?.value || 'ladder',
    horizon_sessions: Number(document.getElementById('recovery-horizon')?.value || 10),
    min_profit_inr: Number(document.getElementById('recovery-margin')?.value || 500),
  };
  try {
    const res = await apiFetch('/api/recovery/paper/start', { method: 'POST', body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Could not start the run.');
    if (data.readopted_mothers) showToast(`Re-adopted ${data.readopted_mothers} named mother(s)`, 'info');
    refreshRecoveryStatus();
  } catch (err) {
    _recoveryError(String(err.message || err));
  }
}

async function recoveryStop() {
  try {
    await apiFetch('/api/recovery/paper/stop', { method: 'POST', body: '{}' });
  } catch (err) { /* the refresh below tells the truth either way */ }
  refreshRecoveryStatus();
}

async function recoveryAddMother() {
  _recoveryError('');
  const raw = document.getElementById('recovery-mother')?.value;
  if (!raw) { _recoveryError('Pick a completed candle open first.'); return; }
  try {
    const res = await apiFetch('/api/recovery/paper/mother', {
      method: 'POST', body: JSON.stringify({ mother_timestamp: raw }),
    });
    const data = await res.json();
    // FastAPI errors can be a list; `data.detail ||` alone swallows those.
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
    showToast(`Mother ${_recTime(data.mother)} accepted — high ${_recNum(data.mother_high, 2)}`, 'success');
    refreshRecoveryStatus();
  } catch (err) {
    _recoveryError(String(err.message || err));
  }
}

async function recoveryDrop(event, el) {
  const id = el?.getAttribute('data-rec-campaign');
  if (!id) return;
  try {
    await apiFetch('/api/recovery/paper/drop', { method: 'POST', body: JSON.stringify({ campaign_id: id }) });
  } catch (err) { /* refresh reports the real state */ }
  refreshRecoveryStatus();
}

function _recoveryTrades(rows) {
  if (!rows || !rows.length) return '<div style="color:var(--muted);font:11px \'JetBrains Mono\',monospace;">No trade yet — waiting for two reds that break lower.</div>';
  const body = rows.map(t => {
    const net = t.net_pnl === null || t.net_pnl === undefined
      ? (t.open ? '<span style="color:#fbbf24;">OPEN</span>' : '<span style="color:var(--muted);">unpriced</span>')
      : `<span style="color:${t.net_pnl >= 0 ? '#34d399' : '#f87171'};">${_recInr(t.net_pnl)}</span>`;
    return `<tr style="border-top:1px solid var(--border);">
      <td style="padding:4px 8px;">T${t.trade_no}</td>
      <td style="padding:4px 8px;">${_recTime(t.entry_time)}</td>
      <td style="padding:4px 8px;text-align:right;">${_recNum(t.entry_index, 2)}</td>
      <td style="padding:4px 8px;text-align:right;color:#f87171;">${_recNum(t.sl_level, 2)}</td>
      <td style="padding:4px 8px;text-align:right;">${t.lots || '—'}×${t.strike || '—'}</td>
      <td style="padding:4px 8px;text-align:right;">${t.entry_premium === null || t.entry_premium === undefined ? '—' : _recNum(t.entry_premium, 2)}</td>
      <td style="padding:4px 8px;text-align:right;">${t.exit_premium === null || t.exit_premium === undefined ? '—' : _recNum(t.exit_premium, 2)}</td>
      <td style="padding:4px 8px;">${t.exit_reason || (t.open ? 'holding' : '—')}</td>
      <td style="padding:4px 8px;text-align:right;">${net}</td>
    </tr>`;
  }).join('');
  return `<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font:11px 'JetBrains Mono',monospace;">
    <thead><tr style="color:var(--muted);text-align:left;">
      <th style="padding:4px 8px;">#</th><th style="padding:4px 8px;">ENTRY</th>
      <th style="padding:4px 8px;text-align:right;">INDEX</th><th style="padding:4px 8px;text-align:right;">SL</th>
      <th style="padding:4px 8px;text-align:right;">LOTS×STRIKE</th><th style="padding:4px 8px;text-align:right;">IN ₹</th>
      <th style="padding:4px 8px;text-align:right;">OUT ₹</th><th style="padding:4px 8px;">EXIT</th>
      <th style="padding:4px 8px;text-align:right;">NET</th>
    </tr></thead><tbody>${body}</tbody></table></div>`;
}

function _recoveryCampaign(c) {
  const statusColour = c.status === 'RECOVERED' ? '#34d399' : (c.status === 'ABANDONED' ? '#f87171' : '#93c5fd');
  const zones = (c.zones || []).map(z =>
    `<span style="margin-right:12px;">zone ${z.level}-${z.level}: ${_recNum(z.lower, 1)}–${_recNum(z.upper, 1)} · ${z.lots} lot</span>`).join('');
  const need = c.required_recovery === null || c.required_recovery === undefined ? null : c.required_recovery;
  return `<div class="card" style="padding:14px;margin-top:12px;">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
      <div style="font:12px 'JetBrains Mono',monospace;">
        <b>Mother ${_recTime(c.mother.timestamp)}</b> · high ${_recNum(c.mother.high, 2)}
        <span style="color:${statusColour};margin-left:10px;">${c.status}</span>
        ${c.end_reason ? `<span style="color:var(--muted);"> (${c.end_reason})</span>` : ''}
      </div>
      <button class="btn btn-ghost" type="button" data-pf-action="recoveryDrop" data-rec-campaign="${escapeHtml(c.campaign_id)}" style="font-size:11px;padding:4px 10px;">Remove</button>
    </div>
    <div style="margin-top:6px;font:11px 'JetBrains Mono',monospace;color:var(--muted);">
      ledger <b style="color:${(c.booked_net || 0) >= 0 ? '#34d399' : '#f87171'};">${_recInr(c.booked_net)}</b>
      ${need !== null ? ` · open trade must net <b>₹${_recNum(need, 0)}</b> to finish green` : ''}
      ${c.open_trades ? ` · ${c.open_trades} open` : ''}
      ${zones ? `<div style="margin-top:4px;">${zones}</div>` : ''}
    </div>
    <div style="margin-top:8px;">${_recoveryTrades(c.trades)}</div>
  </div>`;
}

function renderRecovery(data) {
  const badge = document.getElementById('recovery-badge');
  const startBtn = document.getElementById('recovery-start');
  const stopBtn = document.getElementById('recovery-stop');
  const poll = document.getElementById('recovery-poll');
  const tiles = document.getElementById('recovery-tiles');
  const list = document.getElementById('recovery-campaigns');
  if (!badge || !tiles || !list) return;

  const running = data && data.status === 'ok' && data.running;
  badge.textContent = running ? 'RUNNING' : 'IDLE';
  badge.style.color = running ? '#34d399' : 'var(--muted)';
  if (startBtn) startBtn.style.display = running ? 'none' : '';
  if (stopBtn) stopBtn.style.display = running ? '' : 'none';

  if (!running) {
    if (poll) poll.textContent = 'not started';
    tiles.innerHTML = '';
    list.innerHTML = '<div style="color:var(--muted);font:11px \'JetBrains Mono\',monospace;">Nothing running. Start the run, then name a mother candle.</div>';
    return;
  }

  const book = data.book || {};
  const campaigns = book.campaigns || [];
  const openTrades = campaigns.reduce((a, c) => a + (c.open_trades || 0), 0);
  const closed = campaigns.reduce((a, c) => a + (c.trades || []).filter(t => t.exit_time).length, 0);
  const tile = (label, value, colour) =>
    `<div style="border:1px solid var(--border);border-radius:8px;padding:10px 12px;">
      <div style="font:800 9px 'JetBrains Mono',monospace;letter-spacing:.6px;color:var(--muted);">${label}</div>
      <div style="margin-top:4px;font:600 17px 'JetBrains Mono',monospace;${colour ? `color:${colour};` : ''}">${value}</div>
    </div>`;
  tiles.innerHTML = [
    tile('CAMPAIGNS', campaigns.length),
    tile('OPEN TRADES', openTrades),
    tile('CLOSED TRADES', closed),
    tile('LEDGER', _recInr(book.booked_net), (book.booked_net || 0) >= 0 ? '#34d399' : '#f87171'),
  ].join('');

  if (poll) {
    const skipped = book.last_report && book.last_report.skipped;
    poll.textContent = `${String(book.timeframe || '').toUpperCase()} · ${book.mode} · lot ${book.lot_size} · `
      + (book.last_poll ? `last poll ${_recTime(book.last_poll)} IST` : 'no poll yet')
      + (skipped ? ` (${skipped})` : '');
  }

  list.innerHTML = campaigns.length
    ? campaigns.map(_recoveryCampaign).join('')
    : '<div style="color:var(--muted);font:11px \'JetBrains Mono\',monospace;">Running, but no mother named yet. Pick a completed candle open above.</div>';
}

async function refreshRecoveryStatus() {
  try {
    const res = await apiFetch('/api/recovery/paper/status');
    renderRecovery(await res.json());
  } catch (err) {
    renderRecovery(null);
  }
  if (_recoveryTimer) clearTimeout(_recoveryTimer);
  const visible = document.getElementById('oc-tab-recovery');
  if (visible && visible.style.display !== 'none') {
    _recoveryTimer = setTimeout(refreshRecoveryStatus, 15000);
  }
}

window.recoveryStart = recoveryStart;
window.recoveryStop = recoveryStop;
window.recoveryAddMother = recoveryAddMother;
window.recoveryDrop = recoveryDrop;
window.refreshRecoveryStatus = refreshRecoveryStatus;
