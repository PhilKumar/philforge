"""A throttled alert must be sent again, not dropped.

2026-09-08. PhilForge sent two Telegram messages all day — the live entry at
09:20 and the exit at 10:15 — and Telegram answered both with

    429 {"ok":false,"description":"Too Many Requests: retry after 8"}

Both were logged as warnings and thrown away, so Phil heard nothing on a day
his money traded. Two messages cannot breach a rate limit on their own: this
bot token and chat id are shared with CryptoForge, whose alerts were being
throttled the same way, and the limit is per chat.

`retry_after` states the wait exactly. Waiting it out is the entire fix.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerter  # noqa: E402


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}
        self.text = str(self._body)

    def json(self):
        return self._body


class _Client:
    """Answers 429 for the first `throttles` calls, then 200."""

    def __init__(self, throttles, retry_after=8):
        self.throttles = throttles
        self.retry_after = retry_after
        self.calls = 0

    async def post(self, url, json=None):
        self.calls += 1
        if self.calls <= self.throttles:
            return _Resp(
                429,
                {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": self.retry_after}},
            )
        return _Resp(200, {"ok": True})


class ThrottledAlertsAreStillDelivered(unittest.TestCase):
    def setUp(self):
        self._ok = alerter._TELEGRAM_OK
        self._client = alerter._get_client
        self._sleeps: list[float] = []
        self._real_sleep = asyncio.sleep

        async def _no_wait(seconds):
            self._sleeps.append(seconds)
            await self._real_sleep(0)

        alerter.asyncio.sleep = _no_wait
        alerter._TELEGRAM_OK = True

    def tearDown(self):
        alerter._TELEGRAM_OK = self._ok
        alerter._get_client = self._client
        alerter.asyncio.sleep = self._real_sleep

    def _send(self, client):
        alerter._get_client = lambda: client
        asyncio.run(alerter._send_telegram("hello"))

    def test_the_message_that_was_dropped_now_arrives(self):
        client = _Client(throttles=1)
        self._send(client)
        self.assertEqual(client.calls, 2, "one 429 must be followed by a second attempt")

    def test_it_waits_the_time_telegram_asked_for(self):
        client = _Client(throttles=1, retry_after=8)
        self._send(client)
        self.assertTrue(self._sleeps, "nothing waited")
        self.assertGreaterEqual(self._sleeps[0], 8.0)
        self.assertLessEqual(self._sleeps[0], 30.0)

    def test_a_missing_retry_after_still_backs_off(self):
        class _NoHint(_Client):
            async def post(self, url, json=None):
                self.calls += 1
                return _Resp(429, {"ok": False}) if self.calls <= self.throttles else _Resp(200, {"ok": True})

        client = _NoHint(throttles=1)
        self._send(client)
        self.assertEqual(client.calls, 2)
        self.assertGreaterEqual(self._sleeps[0], 1.0)

    def test_it_gives_up_loudly_rather_than_looping_for_ever(self):
        client = _Client(throttles=99)
        self._send(client)
        self.assertLessEqual(client.calls, alerter._TELEGRAM_MAX_ATTEMPTS)
        self.assertGreaterEqual(client.calls, 2)

    def test_a_real_failure_is_not_retried(self):
        class _Bad(_Client):
            async def post(self, url, json=None):
                self.calls += 1
                return _Resp(400, {"ok": False, "description": "chat not found"})

        client = _Bad(throttles=0)
        self._send(client)
        self.assertEqual(client.calls, 1, "a 400 is not going to become a 200 by asking again")


if __name__ == "__main__":
    unittest.main()
