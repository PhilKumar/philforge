const PIN_LENGTH = 6;
const PASSWORD_MODE = 'password';
const PIN_MODE = 'pin';
let pin = '';
let locked = false;
let loginMode = PASSWORD_MODE;
let mfaPending = false;

const dots = document.querySelectorAll('.pin-dot');
const status = document.getElementById('unlock-status');
const card = document.getElementById('unlock-card');
const usernameInput = document.getElementById('username-input');
const passwordInput = document.getElementById('password-input');
const passwordField = document.getElementById('password-field');
const passwordToggle = document.getElementById('password-toggle');
const totpField = document.getElementById('totp-field');
const totpInput = document.getElementById('totp-input');
const pinSection = document.getElementById('pin-section');
const modeSwitchBtn = document.getElementById('mode-switch-btn');
const unlockBtn = document.getElementById('unlock-btn');
const keypad = document.getElementById('keypad');

function baseStatusMessage() {
  return mfaPending ? 'Enter the code from your authenticator app' : 'Enter username & password';
}

function updateDots() {
  dots.forEach((dot, i) => {
    dot.classList.remove('filled', 'error', 'success');
    if (i < pin.length) dot.classList.add('filled');
  });
}

function resetSecrets(clearPassword = true) {
  pin = '';
  updateDots();
  if (clearPassword) passwordInput.value = '';
}

function setIdleStatus() {
  status.textContent = baseStatusMessage();
  status.className = 'unlock-status';
  unlockBtn.disabled = false;
}

function setMode(mode, focus = true) {
  loginMode = PASSWORD_MODE;
  passwordField.classList.remove('hidden');
  totpField.classList.add('hidden');
  totpInput.value = '';
  mfaPending = false;
  pinSection.classList.add('hidden');
  modeSwitchBtn.classList.add('hidden');
  modeSwitchBtn.setAttribute('aria-hidden', 'true');
  unlockBtn.textContent = 'Unlock';
  resetSecrets(true);
  locked = false;
  setIdleStatus();
  if (!focus) return;
  if (!usernameInput.value.trim()) usernameInput.focus();
  else passwordInput.focus();
}

function showValidationError(msg, focusEl = null) {
  status.textContent = msg;
  status.className = 'unlock-status error';
  card.classList.add('shake');
  unlockBtn.disabled = false;
  setTimeout(() => {
    card.classList.remove('shake');
    if (focusEl) focusEl.focus();
  }, 400);
}

function setError(msg) {
  status.textContent = msg;
  status.className = 'unlock-status error';
  if (loginMode === PIN_MODE) {
    dots.forEach(d => {
      d.classList.remove('filled');
      d.classList.add('error');
    });
  }
  card.classList.add('shake');
  setTimeout(() => {
    card.classList.remove('shake');
    if (mfaPending) {
      totpInput.value = '';
    } else {
      resetSecrets(true);
    }
    locked = false;
    setIdleStatus();
    if (loginMode === PASSWORD_MODE) (mfaPending ? totpInput : passwordInput).focus();
  }, 800);
}

function setSuccess() {
  status.textContent = 'Unlocked! Redirecting...';
  status.className = 'unlock-status success';
  if (loginMode === PIN_MODE) {
    dots.forEach(d => {
      d.classList.remove('filled');
      d.classList.add('success');
    });
  }
  card.classList.add('unlock-pulse');
}

async function tryUnlock() {
  if (locked) return;
  const username = usernameInput.value.trim();
  const secret = loginMode === PIN_MODE ? pin : passwordInput.value;
  const totp = totpInput.value.trim();

  if (!secret) {
    showValidationError(loginMode === PIN_MODE ? 'Enter your PIN' : 'Enter your password', passwordInput);
    return;
  }
  if (mfaPending && !/^\d{6}$/.test(totp)) {
    showValidationError('Enter the 6-digit authenticator code', totpInput);
    return;
  }

  locked = true;
  status.textContent = 'Verifying...';
  status.className = 'unlock-status';
  unlockBtn.disabled = true;

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(username
        ? { username, password: secret, ...(mfaPending ? { totp } : {}) }
        : { password: secret, pin: secret, ...(mfaPending ? { totp } : {}) })
    });
    if (res.ok) {
      if ('caches' in window) {
        const keys = await caches.keys().catch(() => []);
        await Promise.all(keys.filter(key => key.startsWith('philforge-shell-')).map(key => caches.delete(key)));
      }
      setSuccess();
      // The terminal is at /app; "/" is now the public landing page. Sending a
      // freshly logged-in user to "/" would show them marketing copy.
      setTimeout(() => { window.location.href = '/app'; }, 400);
    } else {
      const data = await res.json().catch(() => ({}));
      if (res.status === 428 && data.code === 'mfa_required') {
        locked = false;
        mfaPending = true;
        totpField.classList.remove('hidden');
        unlockBtn.disabled = false;
        unlockBtn.textContent = 'Verify & Unlock';
        status.textContent = data.detail || baseStatusMessage();
        status.className = 'unlock-status';
        totpInput.focus();
        return;
      }
      setError(data.detail || 'Wrong credentials. Try again.');
    }
  } catch (e) {
    setError('Connection error.');
  }
}

function addDigit(d) {
  if (loginMode !== PIN_MODE) return;
  if (locked || pin.length >= PIN_LENGTH) return;
  pin += d;
  updateDots();
  if (pin.length === PIN_LENGTH) {
    setTimeout(tryUnlock, 150);
  }
}

function removeDigit() {
  if (loginMode !== PIN_MODE) return;
  if (locked || pin.length === 0) return;
  pin = pin.slice(0, -1);
  updateDots();
}

function clearAll() {
  if (loginMode !== PIN_MODE || locked) return;
  pin = '';
  updateDots();
}

usernameInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    passwordInput.focus();
  }
});

passwordInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    tryUnlock();
  }
});

totpInput.addEventListener('input', () => {
  totpInput.value = totpInput.value.replace(/\D/g, '').slice(0, 6);
});

totpInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    tryUnlock();
  }
});

passwordToggle.addEventListener('click', () => {
  const nextType = passwordInput.type === 'password' ? 'text' : 'password';
  passwordInput.type = nextType;
  passwordToggle.textContent = nextType === 'password' ? 'Show' : 'Hide';
  passwordToggle.setAttribute('aria-label', nextType === 'password' ? 'Show password' : 'Hide password');
});

modeSwitchBtn.addEventListener('click', () => {
  setMode(loginMode === PIN_MODE ? PASSWORD_MODE : PIN_MODE);
});

keypad.addEventListener('click', (e) => {
  const btn = e.target.closest('.key');
  if (!btn) return;
  const val = btn.dataset.val;
  if (val === 'clear') clearAll();
  else if (val === 'back') removeDigit();
  else addDigit(val);
});

document.addEventListener('keydown', (e) => {
  if (loginMode !== PIN_MODE) return;
  if (document.activeElement === usernameInput || document.activeElement === passwordInput) return;
  if (e.key >= '0' && e.key <= '9') addDigit(e.key);
  else if (e.key === 'Backspace') removeDigit();
  else if (e.key === 'Enter' && pin.length === PIN_LENGTH) tryUnlock();
  else if (e.key === 'Escape') clearAll();
});

unlockBtn.addEventListener('click', tryUnlock);
setMode(PASSWORD_MODE, false);

/* ── Appearance at the door ─────────────────────────────────────────────
   The panel is BUILT from PHILFORGE_APPEARANCE_PRESETS rather than typed
   here: CryptoForge's unlock once hand-listed preset names its allowlist
   had never heard of, and three of its five font buttons silently did
   nothing. Rendering from the registry makes that class of drift
   impossible — a preset that exists is offered, one that doesn't isn't. */
(function () {
  const toggle = document.getElementById('login-appearance-toggle');
  const panel = document.getElementById('login-appearance-panel');
  const tintGrid = document.getElementById('login-tint-grid');
  const fontList = document.getElementById('login-font-list');
  const presets = window.PHILFORGE_APPEARANCE_PRESETS || {};
  if (!toggle || !panel || !tintGrid || !fontList) return;

  (presets.tints || []).forEach((tint) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'swatch ' + (tint.swatch || '');
    btn.setAttribute('data-login-tint', tint.id);
    btn.setAttribute('aria-label', tint.label || tint.id);
    btn.title = tint.label || tint.id;
    tintGrid.appendChild(btn);
  });
  (presets.fonts || []).forEach((font) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.setAttribute('data-login-font', font.id);
    btn.textContent = font.label || font.id;
    fontList.appendChild(btn);
  });

  function sync() {
    const state = typeof window.pfGetAppearance === 'function' ? window.pfGetAppearance() : {};
    const theme = document.documentElement.getAttribute('data-theme') || 'dark';
    panel.querySelectorAll('[data-login-tint]').forEach((btn) => {
      const active = btn.getAttribute('data-login-tint') === state.tint;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    panel.querySelectorAll('[data-login-font]').forEach((btn) => {
      const active = btn.getAttribute('data-login-font') === state.font;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    panel.querySelectorAll('[data-login-theme]').forEach((btn) => {
      const active = btn.getAttribute('data-login-theme') === theme;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  toggle.addEventListener('click', () => {
    panel.hidden = !panel.hidden;
    toggle.setAttribute('aria-expanded', panel.hidden ? 'false' : 'true');
    sync();
  });
  panel.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    toggle.focus();
  });
  panel.addEventListener('click', (event) => {
    const tintBtn = event.target.closest('[data-login-tint]');
    const fontBtn = event.target.closest('[data-login-font]');
    const themeBtn = event.target.closest('[data-login-theme]');
    if (tintBtn && typeof window.pfApplyAppearance === 'function') {
      window.pfApplyAppearance({ tint: tintBtn.getAttribute('data-login-tint') }, { persist: true });
    }
    if (fontBtn && typeof window.pfApplyAppearance === 'function') {
      window.pfApplyAppearance({ font: fontBtn.getAttribute('data-login-font') }, { persist: true });
    }
    if (themeBtn && typeof window.pfApplyTheme === 'function') {
      window.pfApplyTheme(themeBtn.getAttribute('data-login-theme'), { persist: true });
    }
    if (tintBtn || fontBtn || themeBtn) sync();
  });
  document.addEventListener('click', (event) => {
    if (panel.hidden) return;
    if (panel.contains(event.target) || toggle.contains(event.target)) return;
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
  });
  sync();
})();
