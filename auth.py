"""
auth.py — PhilForge Authentication Module (Multi-Tenant)

Handles password hashing (bcrypt), Fernet encryption for broker creds,
session creation/validation, and the get_current_user FastAPI dependency.
"""

import base64
import hashlib
import io
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import bcrypt
import pyotp
from fastapi import HTTPException, Request

import config
import db

_logger = logging.getLogger(__name__)

# ── Password Hashing (bcrypt, cost 12) ───────────────────────────


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── Fernet Encryption (broker credentials at rest) ───────────────
_fernet = None


def encryption_enabled() -> bool:
    """Whether encrypted broker credential storage is configured."""
    return bool(config.ENCRYPTION_KEY)


def _get_fernet():
    """Lazy-init Fernet cipher from ENCRYPTION_KEY env var."""
    global _fernet
    if _fernet is not None:
        return _fernet
    key = config.ENCRYPTION_KEY
    if not key:
        _logger.warning("[Auth] ENCRYPTION_KEY not set — stored broker credentials are disabled")
        return None
    from cryptography.fernet import Fernet

    _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string. Returns plaintext if no key configured."""
    f = _get_fernet()
    if f is None:
        return plaintext
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a string. Returns as-is if no key configured."""
    if not ciphertext:
        return ""
    f = _get_fernet()
    if f is None:
        return ciphertext
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        # If decryption fails, it's probably plaintext (pre-encryption data)
        return ciphertext


# ── Session Management ───────────────────────────────────────────


def _session_storage_key(token: str) -> str:
    """Return the DB storage key for a bearer session token."""
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def session_storage_key(token: str) -> str:
    """Public canonical hash used to bind action tokens to one login session."""
    return _session_storage_key(token)


def _action_token_storage_key(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


async def create_session(user_id: int) -> str:
    """Create a new session token for a user. Returns the token string."""
    token = secrets.token_hex(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=config.SESSION_TTL_HOURS)).isoformat()
    await db.create_session(_session_storage_key(token), user_id, expires_at)
    return token


async def validate_session(token: str) -> dict | None:
    """Validate a session token. Returns session dict (with user_id) or None."""
    if not token:
        return None
    session = await db.get_session(_session_storage_key(token))
    if session:
        return session
    # Backward compatibility for legacy raw-token session rows created before hashing-at-rest.
    return await db.get_session(token)


async def destroy_session(token: str) -> None:
    """Destroy a session (logout)."""
    if not token:
        return
    await db.delete_session(_session_storage_key(token))
    await db.delete_session(token)


# ── MFA and one-time action authorization ────────────────────────


def generate_totp_enrollment(username: str) -> dict:
    """Create an encrypted-at-rest TOTP enrollment payload for an account."""
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="PhilForge")
    qr_data_uri = ""
    try:
        import segno

        qr_buffer = io.BytesIO()
        segno.make(uri, micro=False, error="m").save(qr_buffer, kind="svg", scale=5, border=2)
        qr_data_uri = "data:image/svg+xml;base64," + base64.b64encode(qr_buffer.getvalue()).decode("ascii")
    except ImportError:
        # Manual secret enrollment remains functional in minimal developer
        # environments; production requirements include segno for the QR.
        pass
    return {"secret": secret, "otpauth_uri": uri, "qr_data_uri": qr_data_uri}


def _matching_totp_counter(secret: str, code: str, *, now: float | None = None, valid_window: int = 1) -> int | None:
    """Return the matching 30-second counter without accepting a replay."""
    candidate = re.sub(r"\s+", "", str(code or ""))
    if not re.fullmatch(r"\d{6}", candidate):
        return None
    timestamp = float(time.time() if now is None else now)
    totp = pyotp.TOTP(secret)
    current_counter = int(timestamp // totp.interval)
    for counter in range(current_counter - valid_window, current_counter + valid_window + 1):
        if counter < 0:
            continue
        expected = totp.at(counter * totp.interval)
        if pyotp.utils.strings_equal(expected, candidate):
            return counter
    return None


async def verify_user_totp(user: dict, code: str, *, consume: bool = True, now: float | None = None) -> bool:
    """Verify an enrolled user's TOTP and atomically reject code reuse."""
    if not bool(user.get("mfa_enabled")):
        return False
    secret = str(user.get("mfa_totp_secret") or "")
    if not secret:
        return False
    counter = _matching_totp_counter(secret, code, now=now)
    if counter is None:
        return False
    if not consume:
        return counter > int(user.get("mfa_last_counter", -1) or -1)
    return await db.claim_mfa_counter(int(user["id"]), counter)


async def verify_totp_enrollment(user_id: int, secret: str, code: str, *, now: float | None = None) -> bool:
    """Verify and consume the first code for a pending enrollment secret."""
    counter = _matching_totp_counter(secret, code, now=now)
    if counter is None:
        return False
    return await db.claim_mfa_counter(int(user_id), counter)


_SENSITIVE_ACTION_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("POST", re.compile(r"^/api/live/start$"), "live_trading"),
    ("POST", re.compile(r"^/api/live/stop$"), "live_trading"),
    ("POST", re.compile(r"^/api/live/exit-position$"), "live_trading"),
    ("POST", re.compile(r"^/api/orders/place$"), "broker_order"),
    ("DELETE", re.compile(r"^/api/orders/[^/]+$"), "broker_order"),
    ("POST", re.compile(r"^/api/terminal/order$"), "broker_order"),
    ("POST", re.compile(r"^/api/terminal/gtt$"), "broker_order"),
    ("DELETE", re.compile(r"^/api/terminal/forever/[^/]+$"), "broker_order"),
    # The instrument is part of the signed one-time target. A token minted to
    # arm or kill NIFTY cannot be replayed against BANKNIFTY by changing only a
    # query string.
    ("POST", re.compile(r"^/api/fib-boundary/live/[^/]+/arm$"), "live_trading"),
    ("POST", re.compile(r"^/api/fib-boundary/live/[^/]+/kill$"), "live_trading"),
    ("POST", re.compile(r"^/api/scalp/entry$"), "live_scalp"),
    ("POST", re.compile(r"^/api/scalp/stop$"), "live_scalp"),
    ("POST", re.compile(r"^/api/scalp/exit/[^/]+$"), "live_scalp"),
    ("POST", re.compile(r"^/api/scalp/kill-all$"), "live_scalp"),
    ("PUT", re.compile(r"^/api/scalp/trades/[^/]+/targets$"), "live_scalp"),
    ("PUT", re.compile(r"^/api/user/broker$"), "broker_credentials"),
    ("DELETE", re.compile(r"^/api/user/broker$"), "broker_credentials"),
    ("POST", re.compile(r"^/api/refresh-token$"), "broker_credentials"),
    ("POST", re.compile(r"^/api/broker/connect$"), "broker_credentials"),
    ("PUT", re.compile(r"^/api/user/password$"), "account_security"),
    ("POST", re.compile(r"^/api/admin/users$"), "admin_account"),
    ("PUT", re.compile(r"^/api/admin/users/[^/]+/(?:toggle|password)$"), "admin_account"),
)


def classify_sensitive_action(method: str, path: str) -> str | None:
    """Map one exact method/path to its server-owned authorization class."""
    method = str(method or "").upper()
    path = str(path or "")
    for expected_method, pattern, action_class in _SENSITIVE_ACTION_RULES:
        if method == expected_method and pattern.fullmatch(path):
            return action_class
    return None


async def create_action_authorization(
    *, user_id: int, session_token: str, action_class: str, method: str, path: str
) -> tuple[str, int]:
    """Issue a short-lived bearer that works once for one exact request."""
    expected = classify_sensitive_action(method, path)
    if not expected or expected != action_class:
        raise ValueError("Unsupported action authorization target")
    token = secrets.token_urlsafe(32)
    ttl = int(config.ACTION_TOKEN_TTL_SECONDS)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
    await db.create_action_token(
        _action_token_storage_key(token),
        int(user_id),
        _session_storage_key(session_token),
        action_class,
        method,
        path,
        expires_at,
    )
    return token, ttl


async def consume_action_authorization(
    *, token: str, user_id: int, session_token: str, action_class: str, method: str, path: str
) -> bool:
    """Consume an exact action authorization; every mismatch fails closed."""
    if not token or not session_token:
        return False
    return await db.consume_action_token(
        _action_token_storage_key(token),
        int(user_id),
        _session_storage_key(session_token),
        action_class,
        method,
        path,
    )


# ── Request Helpers ──────────────────────────────────────────────


def get_session_token(request: Request) -> str:
    """Extract session token from cookie or Authorization header."""
    token = request.cookies.get("philforge_session", "")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    return token


async def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency — extracts and validates the session, returns the full user dict.
    Raises 401 if not authenticated.
    """
    cached_user = getattr(request.state, "current_user", None)
    if cached_user:
        return cached_user

    token = get_session_token(request)
    session = await validate_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await db.get_user_by_id(session["user_id"])
    if not user or not user["is_active"]:
        if user:
            await db.delete_sessions_for_user(user["id"])
        elif session.get("user_id"):
            await db.delete_session(token)
        raise HTTPException(status_code=401, detail="Account disabled or not found")

    request.state.current_user = user
    return user


async def require_admin(request: Request) -> dict:
    """FastAPI dependency — like get_current_user but requires admin role."""
    user = await get_current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
