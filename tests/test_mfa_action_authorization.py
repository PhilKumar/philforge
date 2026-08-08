"""End-to-end security checks for MFA and protected broker mutations."""

import hashlib
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("PHILFORGE_SKIP_STARTUP_JOBS", "1")
os.environ.setdefault("PHILFORGE_STARTUP_SCRIP_MASTER", "0")
os.environ.setdefault("PHILFORGE_STARTUP_TRADE_BACKFILL", "0")
os.environ.setdefault("PHILFORGE_STARTUP_ENGINE_RESTORE", "0")
os.environ.setdefault("DHAN_CLIENT_ID", "dummy")
os.environ.setdefault("DHAN_ACCESS_TOKEN", "dummy")

import httpx
import pyotp
from cryptography.fernet import Fernet

import app as app_module
import auth
import config
import db


class MfaActionAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = config.DB_PATH
        self.old_encryption_key = config.ENCRYPTION_KEY
        self.old_ttl = config.ACTION_TOKEN_TTL_SECONDS
        config.DB_PATH = os.path.join(self.temp_dir.name, "philforge-test.db")
        config.ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
        config.ACTION_TOKEN_TTL_SECONDS = 120
        auth._fernet = None
        db._initialized = False
        app_module._login_attempts.clear()
        await db.init_db()
        password_hash = auth.hash_password("Correct-Horse-42!")
        self.user_id = await db.create_user("mfa-user", password_hash)
        self.transport = httpx.ASGITransport(app=app_module.app)
        self.client = httpx.AsyncClient(transport=self.transport, base_url="https://philforge.test")

    async def asyncTearDown(self):
        await self.client.aclose()
        config.DB_PATH = self.old_db_path
        config.ENCRYPTION_KEY = self.old_encryption_key
        config.ACTION_TOKEN_TTL_SECONDS = self.old_ttl
        auth._fernet = None
        db._initialized = False
        self.temp_dir.cleanup()

    async def _login(self, *, totp=""):
        body = {"username": "mfa-user", "password": "Correct-Horse-42!"}
        if totp:
            body["totp"] = totp
        return await self.client.post("/api/auth/login", json=body)

    def _totp_clock(self, timestamp: float):
        matcher = auth._matching_totp_counter

        def match_at_test_time(secret, code, **kwargs):
            return matcher(secret, code, now=timestamp, valid_window=kwargs.get("valid_window", 1))

        return patch.object(auth, "_matching_totp_counter", side_effect=match_at_test_time)

    async def _enroll(self, timestamp: float) -> str:
        login = await self._login()
        self.assertEqual(login.status_code, 200)
        start = await self.client.post(
            "/api/auth/mfa/enroll/start",
            json={"password": "Correct-Horse-42!"},
        )
        self.assertEqual(start.status_code, 200, start.text)
        if start.json()["qr_data_uri"]:
            self.assertTrue(start.json()["qr_data_uri"].startswith("data:image/svg+xml;base64,"))
        secret = start.json()["secret"]
        with self._totp_clock(timestamp):
            verify = await self.client.post(
                "/api/auth/mfa/enroll/verify",
                json={"password": "Correct-Horse-42!", "totp": pyotp.TOTP(secret).at(timestamp)},
            )
        self.assertEqual(verify.status_code, 200, verify.text)
        status = await self.client.get("/api/auth/status")
        with sqlite3.connect(config.DB_PATH) as conn:
            sessions = conn.execute("SELECT token, user_id, expires_at FROM sessions").fetchall()
        self.assertTrue(
            status.json().get("authenticated"), (verify.headers, self.client.cookies, sessions, status.text)
        )
        return secret

    async def test_enrollment_encrypts_secret_and_login_requires_fresh_totp(self):
        timestamp = 1_700_000_000.0
        secret = await self._enroll(timestamp)

        with sqlite3.connect(config.DB_PATH) as conn:
            stored = conn.execute(
                "SELECT mfa_totp_secret, mfa_pending_secret, mfa_enabled FROM users WHERE id = ?",
                (self.user_id,),
            ).fetchone()
        self.assertNotEqual(stored[0], secret)
        self.assertTrue(str(stored[0]).startswith("gAAAA"))
        self.assertEqual(stored[1], "")
        self.assertEqual(stored[2], 1)

        await self.client.post("/api/auth/logout")
        missing = await self._login()
        self.assertEqual(missing.status_code, 428)
        self.assertEqual(missing.json()["code"], "mfa_required")

        next_timestamp = timestamp + 30
        next_code = pyotp.TOTP(secret).at(next_timestamp)
        with self._totp_clock(next_timestamp):
            accepted = await self._login(totp=next_code)
        self.assertEqual(accepted.status_code, 200, accepted.text)

        await self.client.post("/api/auth/logout")
        with self._totp_clock(next_timestamp):
            replay = await self._login(totp=next_code)
        self.assertEqual(replay.status_code, 401)

    async def test_action_token_is_exact_session_bound_and_single_use(self):
        timestamp = 1_700_000_000.0
        secret = await self._enroll(timestamp)

        challenge = await self.client.post("/api/orders/place", json={})
        self.assertEqual(challenge.status_code, 428)
        self.assertEqual(challenge.json()["code"], "action_authorization_required")
        self.assertEqual(challenge.json()["action_class"], "broker_order")

        action_timestamp = timestamp + 30
        with self._totp_clock(action_timestamp):
            authorized = await self.client.post(
                "/api/auth/action-token",
                json={
                    "password": "Correct-Horse-42!",
                    "totp": pyotp.TOTP(secret).at(action_timestamp),
                    "action_class": "broker_order",
                    "target_method": "POST",
                    "target_path": "/api/orders/place",
                },
            )
        self.assertEqual(authorized.status_code, 200, authorized.text)
        action_token = authorized.json()["action_token"]

        with sqlite3.connect(config.DB_PATH) as conn:
            raw_token_rows = conn.execute(
                "SELECT COUNT(*) FROM action_tokens WHERE token_hash = ?", (action_token,)
            ).fetchone()[0]
            hashed_token_rows = conn.execute(
                "SELECT COUNT(*) FROM action_tokens WHERE token_hash = ?",
                (f"sha256:{hashlib.sha256(action_token.encode()).hexdigest()}",),
            ).fetchone()[0]
        self.assertEqual(raw_token_rows, 0)
        self.assertEqual(hashed_token_rows, 1)

        second_client = httpx.AsyncClient(transport=self.transport, base_url="https://philforge.test")
        second_timestamp = action_timestamp + 30
        with self._totp_clock(second_timestamp):
            second_login = await second_client.post(
                "/api/auth/login",
                json={
                    "username": "mfa-user",
                    "password": "Correct-Horse-42!",
                    "totp": pyotp.TOTP(secret).at(second_timestamp),
                },
            )
        self.assertEqual(second_login.status_code, 200, second_login.text)
        wrong_session = await second_client.post(
            "/api/orders/place",
            json={},
            headers={"X-PhilForge-Action-Token": action_token},
        )
        self.assertEqual(wrong_session.status_code, 403)
        await second_client.aclose()

        wrong_path = await self.client.post(
            "/api/live/start",
            json={},
            headers={"X-PhilForge-Action-Token": action_token},
        )
        self.assertEqual(wrong_path.status_code, 403)

        first_use = await self.client.post(
            "/api/orders/place",
            json={},
            headers={"X-PhilForge-Action-Token": action_token},
        )
        self.assertEqual(first_use.status_code, 422)
        replay = await self.client.post(
            "/api/orders/place",
            json={},
            headers={"X-PhilForge-Action-Token": action_token},
        )
        self.assertEqual(replay.status_code, 403)
        self.assertEqual(replay.json()["code"], "invalid_action_authorization")

    async def test_expired_action_token_fails_closed(self):
        timestamp = 1_700_000_000.0
        secret = await self._enroll(timestamp)
        with self._totp_clock(timestamp + 30):
            authorized = await self.client.post(
                "/api/auth/action-token",
                json={
                    "password": "Correct-Horse-42!",
                    "totp": pyotp.TOTP(secret).at(timestamp + 30),
                    "action_class": "live_trading",
                    "target_method": "POST",
                    "target_path": "/api/live/start",
                },
            )
        self.assertEqual(authorized.status_code, 200, authorized.text)
        action_token = authorized.json()["action_token"]
        with sqlite3.connect(config.DB_PATH) as conn:
            conn.execute("UPDATE action_tokens SET expires_at = '2000-01-01T00:00:00+00:00'")
            conn.commit()
        expired = await self.client.post(
            "/api/live/start",
            json={},
            headers={"X-PhilForge-Action-Token": action_token},
        )
        self.assertEqual(expired.status_code, 403)
        self.assertEqual(expired.json()["code"], "invalid_action_authorization")

    async def test_unenrolled_user_is_blocked_from_risk_increasing_action_only(self):
        login = await self._login()
        self.assertEqual(login.status_code, 200)
        blocked = await self.client.post("/api/live/start", json={})
        self.assertEqual(blocked.status_code, 428)
        self.assertEqual(blocked.json()["code"], "mfa_enrollment_required")

        self.assertIsNone(auth.classify_sensitive_action("POST", "/api/emergency-stop"))
        self.assertEqual(auth.classify_sensitive_action("POST", "/api/live/stop"), "live_trading")
        self.assertEqual(auth.classify_sensitive_action("POST", "/api/live/exit-position"), "live_trading")
        self.assertEqual(auth.classify_sensitive_action("POST", "/api/fib-boundary/live/NIFTY/arm"), "live_trading")
        self.assertEqual(auth.classify_sensitive_action("POST", "/api/fib-boundary/live/NIFTY/kill"), "live_trading")
        self.assertIsNone(auth.classify_sensitive_action("POST", "/api/fib-boundary/paper/arm"))
        self.assertIsNone(auth.classify_sensitive_action("POST", "/api/fib-boundary/paper/kill"))
        self.assertEqual(auth.classify_sensitive_action("POST", "/api/scalp/kill-all"), "live_scalp")
        self.assertEqual(auth.classify_sensitive_action("POST", "/api/scalp/entry"), "live_scalp")

    async def test_fib_live_token_is_bound_to_the_instrument_path(self):
        timestamp = 1_700_000_000.0
        secret = await self._enroll(timestamp)
        with self._totp_clock(timestamp + 30):
            authorized = await self.client.post(
                "/api/auth/action-token",
                json={
                    "password": "Correct-Horse-42!",
                    "totp": pyotp.TOTP(secret).at(timestamp + 30),
                    "action_class": "live_trading",
                    "target_method": "POST",
                    "target_path": "/api/fib-boundary/live/NIFTY/arm",
                },
            )
        self.assertEqual(authorized.status_code, 200, authorized.text)
        action_token = authorized.json()["action_token"]

        wrong_symbol = await self.client.post(
            "/api/fib-boundary/live/BANKNIFTY/arm",
            headers={"X-PhilForge-Action-Token": action_token},
        )
        self.assertEqual(wrong_symbol.status_code, 403)

        exact_symbol = await self.client.post(
            "/api/fib-boundary/live/NIFTY/arm",
            headers={"X-PhilForge-Action-Token": action_token},
        )
        self.assertEqual(exact_symbol.status_code, 404)

    async def test_action_token_cannot_be_minted_for_unprotected_or_mismatched_target(self):
        timestamp = 1_700_000_000.0
        secret = await self._enroll(timestamp)
        code = pyotp.TOTP(secret).at(timestamp + 30)
        with self._totp_clock(timestamp + 30):
            response = await self.client.post(
                "/api/auth/action-token",
                json={
                    "password": "Correct-Horse-42!",
                    "totp": code,
                    "action_class": "broker_order",
                    "target_method": "POST",
                    "target_path": "/api/emergency-stop",
                },
            )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
