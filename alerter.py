"""
alerter.py — Async Telegram & Discord alerting for PhilForge.

Sends fire-and-forget notifications on broker failures, order errors,
and critical events. Non-blocking — never delays the API response.

Configure via .env:
  TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
  TELEGRAM_CHAT_ID=-100123456789
  DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

import config

IST = ZoneInfo("Asia/Kolkata")

_log = logging.getLogger("alerter")

# ── Config (from env) ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")

_TELEGRAM_OK = bool(config.TELEGRAM_ALERTS_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
_DISCORD_OK = bool(DISCORD_WEBHOOK_URL)

# Shared async client — connection-pooled, reused across calls
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=5))
    return _client


# ── Low-level senders ─────────────────────────────────────────────


# A 429 FROM TELEGRAM IS NOT A FAILURE, IT IS A WAIT.
#
# 2026-09-08: PhilForge sent exactly two messages all day -- the live entry at
# 09:20 and the exit at 10:15 -- and Telegram answered both with
#
#     429 {"description":"Too Many Requests: retry after 8"}
#
# which was logged as a warning and dropped. Phil got nothing on a day his
# money traded. Two messages cannot exceed any rate limit on their own; this
# bot and this chat are SHARED with CryptoForge, whose own alerts were being
# throttled the same way an hour earlier, and Telegram's limit is per chat.
#
# `retry_after` says exactly how long the wait is, so waiting it out is the
# whole fix. This runs in a fire-and-forget task, never on the trade path, so
# sleeping here delays nothing that matters -- and an alert that arrives eight
# seconds late is worth infinitely more than one that never arrives.
_TELEGRAM_MAX_ATTEMPTS = 4
_TELEGRAM_MAX_TOTAL_WAIT = 60.0  # never hold a task longer than this
_TELEGRAM_DEFAULT_BACKOFF = 5.0


def _retry_after_seconds(resp) -> float:
    """What Telegram asked us to wait, clamped to something sane."""
    delay = _TELEGRAM_DEFAULT_BACKOFF
    try:
        body = resp.json()
        asked = (body.get("parameters") or {}).get("retry_after")
        if asked is None:
            asked = body.get("retry_after")
        if asked is not None:
            delay = float(asked)
    except Exception:
        pass
    if not delay or delay <= 0:
        delay = _TELEGRAM_DEFAULT_BACKOFF
    return max(1.0, min(float(delay) + 0.5, 30.0))


async def _send_telegram(text: str) -> None:
    if not _TELEGRAM_OK:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    waited = 0.0
    for attempt in range(1, _TELEGRAM_MAX_ATTEMPTS + 1):
        try:
            resp = await _get_client().post(url, json=payload)
        except Exception as e:
            _log.warning("Telegram error: %s", e)
            return
        if resp.status_code == 200:
            if attempt > 1:
                _log.info("Telegram delivered on attempt %s after %.0fs of throttling", attempt, waited)
            return
        if resp.status_code != 429:
            _log.warning("Telegram send failed: %s %s", resp.status_code, resp.text[:200])
            return
        delay = _retry_after_seconds(resp)
        if attempt >= _TELEGRAM_MAX_ATTEMPTS or waited + delay > _TELEGRAM_MAX_TOTAL_WAIT:
            _log.warning(
                "Telegram throttled and GAVE UP after %s attempts / %.0fs — this alert was never delivered: %s",
                attempt,
                waited,
                resp.text[:200],
            )
            return
        _log.info("Telegram throttled (attempt %s); waiting %.1fs and sending again", attempt, delay)
        await asyncio.sleep(delay)
        waited += delay


async def _send_discord(text: str) -> None:
    if not _DISCORD_OK:
        return
    payload = {"content": text}
    try:
        resp = await _get_client().post(DISCORD_WEBHOOK_URL, json=payload)
        if resp.status_code not in (200, 204):
            _log.warning("Discord send failed: %s %s", resp.status_code, resp.text[:200])
    except Exception as e:
        _log.warning("Discord error: %s", e)


async def _dispatch(text_html: str, text_plain: str) -> None:
    """Send to all configured channels in parallel."""
    tasks = []
    if _TELEGRAM_OK:
        tasks.append(_send_telegram(text_html))
    if _DISCORD_OK:
        tasks.append(_send_discord(text_plain))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ── Public API ────────────────────────────────────────────────────


def alert(title: str, body: str, level: str = "error") -> None:
    """Fire-and-forget alert. Safe to call from any async context.

    Args:
        title: Short heading, e.g. "Order Failed"
        body:  Details — symbol, error message, etc.
        level: "error" | "warn" | "info"  (controls emoji prefix)
    """
    if not (_TELEGRAM_OK or _DISCORD_OK):
        return

    icon = {"error": "🔴", "warn": "🟡", "info": "🟢"}.get(level, "⚪")
    # IST, ALWAYS. datetime.now() reads the machine's clock, and production runs
    # on UTC -- so every alert was stamped five and a half hours behind, and one
    # sent after 18:30 IST also carried the wrong DATE. The alert is read by a
    # person in India next to a market that runs on IST; nothing else is useful.
    ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

    # HTML for Telegram
    html = f"{icon} <b>[PhilForge] {title}</b>\n<code>{ts}</code>\n\n{body}"
    # Plain for Discord
    plain = f"{icon} **[PhilForge] {title}**\n`{ts}`\n\n{body}"

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_dispatch(html, plain))
    except RuntimeError:
        # No running loop — skip (startup context)
        _log.debug("No event loop — alert skipped: %s", title)


async def shutdown() -> None:
    """Close the shared HTTP client. Call on app shutdown."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
