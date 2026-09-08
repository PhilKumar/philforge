"""The Sanctuary — the owner's private journal and household ledger.

A standalone page at /sanctuary, deliberately outside the trading terminal:
its API prefix is not on the viewer shared-read list, every endpoint
requires the admin role (anyone else gets a 404, as if the page does not
exist), and the content sits behind a second password that only ever
lives here as a bcrypt hash.  Unlocking opens a sliding 45-minute grant
keyed to the login session, so logging out locks the sanctuary too.

Monthly automation is lazy: recurring savings and expenses (NPS, PF, MF
SIP, school fees…) are posted for any elapsed months whenever the finance
view loads, which survives deploys without a background task.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
import secrets
import tempfile
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

import auth
import config
import db as core_db
import sanctuary_apple_journal
import sanctuary_content
import sanctuary_db
import sanctuary_docs
import sanctuary_emi
import sanctuary_epf
import sanctuary_holdings
import sanctuary_identity
import sanctuary_plan
import sanctuary_statements
from image_uploads import ImageValidationError, looks_like_heic, sanitize_image, sanitized_parts

router = APIRouter()

IST = ZoneInfo("Asia/Kolkata")
UNLOCK_TTL_SECONDS = 45 * 60
ACTION_CLASS = "sanctuary"
ALERT_WINDOW_DAYS = 7
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PHOTO_PATH_RE = re.compile(r"^\d{4}/\d{2}/[A-Za-z0-9_\-]+\.(jpg|png|webp)$")
# Who pays him. A credit naming one of these IS the month's pay, whatever
# category an older import gave it.
_EMPLOYER_WORDS = ("kyndryl", "ibm india")

ENTRY_KINDS = ("note", "event", "blog", "emotion", "family", "friends", "achievement", "photo")

# Unlock attempts: in-memory failure tally, cleared on success.  Eight wrong
# tries buys a fifteen-minute wait — gentle for one owner, hostile to a guess.
_unlock_failures: dict[int, list[float]] = {}
_FAILURE_LIMIT = 8
_FAILURE_WINDOW = 15 * 60

# The sweep-linked overdraft's category, named once because three places
# have to agree on it: the rules that file into it, the categories list
# that offers it, and the spending sum that leaves it out.
OD_CATEGORY = "OD loan"
# The name the overdraft's loan card carries, so the card and the sweeps
# can find each other even before an account number is known.
OD_LOAN_NAME = "Sweep overdraft"

DEFAULT_CATEGORIES = [
    {"name": "Milk", "emoji": "🥛", "kind": "expense", "quick": True},
    {"name": "EB Bill", "emoji": "⚡", "kind": "expense", "quick": True},
    {"name": "Groceries", "emoji": "🧺", "kind": "expense", "quick": True},
    # Anything eaten, wherever it was eaten. Three names for it had grown —
    # Eatables, Food & Dining, Eating out — and a fourth, Zepto, for one shop.
    {"name": "Eating out", "emoji": "🍛", "kind": "expense", "quick": True},
    # He holds two cards, and a bill that cannot say which one it paid is not
    # worth much a year later. The Citi card is an Axis card since the
    # takeover; CRED settles the HDFC one.
    {"name": "Trading", "emoji": "📊", "kind": "saving", "quick": False},
    {"name": "HDFC Creditcard", "emoji": "💳", "kind": "expense", "quick": False},
    {"name": "AxisBank Creditcard", "emoji": "💳", "kind": "expense", "quick": False},
    {"name": "Fuel — Car", "emoji": "⛽", "kind": "expense", "quick": True},
    {"name": "Fuel — Bike", "emoji": "🏍️", "kind": "expense", "quick": True},
    {"name": "Music Class", "emoji": "🎵", "kind": "expense", "quick": False},
    {"name": "Fees for Oliver and Evin", "emoji": "🎒", "kind": "expense", "quick": False},
    {"name": "Household Repair", "emoji": "🔧", "kind": "expense", "quick": False},
    {"name": "New Items", "emoji": "🛍️", "kind": "expense", "quick": False},
    {"name": "Mobile & Internet", "emoji": "📶", "kind": "expense", "quick": False},
    {"name": "Health", "emoji": "💊", "kind": "expense", "quick": False},
    {"name": "Giving", "emoji": "🕊️", "kind": "expense", "quick": False},
    {"name": "Travel", "emoji": "🚌", "kind": "expense", "quick": False},
    {"name": "Other", "emoji": "🌱", "kind": "expense", "quick": False},
    # The sweep-linked overdraft: a loan account, not a place to park money.
    # Both directions of a sweep move this debt's balance and neither is
    # spending — see the finance view, where the category is excluded.
    {"name": OD_CATEGORY, "emoji": "🏛️", "kind": "debt", "quick": False},
    {"name": "NPS", "emoji": "🌳", "kind": "saving", "quick": False},
    {"name": "PF", "emoji": "🌳", "kind": "saving", "quick": False},
]


_logger = logging.getLogger(__name__)


def _today_ist() -> date:
    return datetime.now(IST).date()


def _now_ist() -> datetime:
    return datetime.now(IST)


def _month_key(day: date) -> str:
    return day.strftime("%Y-%m")


def _month_bounds(month: str) -> tuple[str, str]:
    start = date.fromisoformat(f"{month}-01")
    end = sanctuary_emi._add_months(start, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _months_between(start: str, end: str) -> list[str]:
    """Inclusive YYYY-MM keys from start to end."""
    try:
        cursor = date.fromisoformat(f"{start}-01")
        stop = date.fromisoformat(f"{end}-01")
    except ValueError:
        return []
    months = []
    while cursor <= stop and len(months) < 600:
        months.append(_month_key(cursor))
        cursor = sanctuary_emi._add_months(cursor, 1)
    return months


# ── Gates ────────────────────────────────────────────────────────


async def _admin_user(request: Request) -> dict:
    """The sanctuary exists only for the owner; everyone else sees a 404."""
    user = await auth.get_current_user(request)
    if str(user.get("role") or "").lower() != "admin":
        raise HTTPException(status_code=404, detail="Not found")
    return user


async def _unlocked_user(request: Request) -> dict:
    user = await _admin_user(request)
    token = auth.get_session_token(request)
    session_key = auth.session_storage_key(token)
    if not await core_db.has_action_grant(int(user["id"]), session_key, ACTION_CLASS):
        raise HTTPException(status_code=423, detail="sanctuary_locked")
    # Sliding idle window: presence keeps the door open, absence closes it.
    expires_at = (datetime.now(ZoneInfo("UTC")) + timedelta(seconds=UNLOCK_TTL_SECONDS)).isoformat()
    await core_db.grant_action_class(int(user["id"]), session_key, ACTION_CLASS, expires_at)
    return user


async def _password_hash(user_id: int) -> str:
    return await sanctuary_db.get_state(user_id, "password_hash", "")


def _too_many_failures(user_id: int) -> bool:
    now = time.monotonic()
    attempts = [t for t in _unlock_failures.get(user_id, []) if now - t < _FAILURE_WINDOW]
    _unlock_failures[user_id] = attempts
    return len(attempts) >= _FAILURE_LIMIT


# ── Page ─────────────────────────────────────────────────────────


def _page_and_version() -> tuple[str, str]:
    """The page, and a short stamp of exactly this copy of it.

    A sanctuary tab stays open for days. A deploy changes the file on the
    server and nothing at all in the tab he is looking at, so a fix that
    landed at seven o'clock is still missing at eight — and the only
    evidence is him saying it is not fixed. The page carries its own stamp
    and asks for it again whenever he comes back to the tab.
    """
    page_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sanctuary.html")
    with open(page_path, encoding="utf-8") as handle:
        page = handle.read()
    return page, hashlib.sha1(page.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]


@router.get("/sanctuary", response_class=HTMLResponse)
async def sanctuary_page(request: Request):
    token = auth.get_session_token(request)
    session = await auth.validate_session(token)
    user = await core_db.get_user_by_id(session["user_id"]) if session else None
    if not user or not user.get("is_active"):
        return RedirectResponse("/app", status_code=307)
    if str(user.get("role") or "").lower() != "admin":
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    page, version = _page_and_version()
    # The whole page — markup, style and script — is this one file, so a
    # browser holding yesterday's copy is holding yesterday's fixes, and a
    # deploy looks like nothing happened. Never keep it.
    return HTMLResponse(
        page.replace("__SANCT_VERSION__", version),
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


# ── Lock, unlock, setup ──────────────────────────────────────────


@router.get("/api/sanctuary/status")
async def sanctuary_status(request: Request, user: dict = Depends(_admin_user)):
    token = auth.get_session_token(request)
    unlocked = await core_db.has_action_grant(int(user["id"]), auth.session_storage_key(token), ACTION_CLASS)
    return {
        "setup": bool(await _password_hash(int(user["id"]))),
        "unlocked": bool(unlocked),
        "today": _today_ist().isoformat(),
        # What the server would serve him right now. The open tab compares
        # it with the stamp it was born with.
        "version": _page_and_version()[1],
    }


@router.post("/api/sanctuary/setup")
async def sanctuary_setup(request: Request, user: dict = Depends(_admin_user)):
    payload = await request.json()
    password = str(payload.get("password") or "")
    if await _password_hash(int(user["id"])):
        raise HTTPException(status_code=409, detail="Sanctuary password is already set")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Use at least 8 characters")
    await sanctuary_db.set_state(int(user["id"]), "password_hash", auth.hash_password(password))
    return await _grant_unlock(request, user)


@router.post("/api/sanctuary/unlock")
async def sanctuary_unlock(request: Request, user: dict = Depends(_admin_user)):
    user_id = int(user["id"])
    if _too_many_failures(user_id):
        raise HTTPException(status_code=429, detail="Too many tries — rest a while and come back")
    payload = await request.json()
    password = str(payload.get("password") or "")
    stored = await _password_hash(user_id)
    if not stored:
        raise HTTPException(status_code=409, detail="sanctuary_not_setup")
    if not auth.verify_password(password, stored):
        _unlock_failures.setdefault(user_id, []).append(time.monotonic())
        await asyncio.sleep(0.6)
        # 403, not 401: the login session is fine, only this door stays shut —
        # the page treats 401 as "signed out" and would bounce to /app.
        raise HTTPException(status_code=403, detail="That is not the key to this garden")
    _unlock_failures.pop(user_id, None)
    return await _grant_unlock(request, user)


async def _grant_unlock(request: Request, user: dict) -> dict:
    token = auth.get_session_token(request)
    expires_at = (datetime.now(ZoneInfo("UTC")) + timedelta(seconds=UNLOCK_TTL_SECONDS)).isoformat()
    await core_db.grant_action_class(int(user["id"]), auth.session_storage_key(token), ACTION_CLASS, expires_at)
    return {"unlocked": True, "ttl_seconds": UNLOCK_TTL_SECONDS}


@router.post("/api/sanctuary/lock")
async def sanctuary_lock(request: Request, user: dict = Depends(_admin_user)):
    token = auth.get_session_token(request)
    session_key = auth.session_storage_key(token)
    expired = datetime.now(ZoneInfo("UTC")).isoformat()
    await core_db.grant_action_class(int(user["id"]), session_key, ACTION_CLASS, expired)
    return {"unlocked": False}


@router.post("/api/sanctuary/change-password")
async def sanctuary_change_password(request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    current = str(payload.get("current") or "")
    new = str(payload.get("new") or "")
    stored = await _password_hash(int(user["id"]))
    if not auth.verify_password(current, stored):
        raise HTTPException(status_code=403, detail="Current password is wrong")
    if len(new) < 8:
        raise HTTPException(status_code=400, detail="Use at least 8 characters")
    await sanctuary_db.set_state(int(user["id"]), "password_hash", auth.hash_password(new))
    return {"ok": True}


# ── Daily verse and quote ────────────────────────────────────────


def _pick_quote(day: date) -> dict:
    text, author = sanctuary_content.QUOTES[day.toordinal() % len(sanctuary_content.QUOTES)]
    return {"text": text, "author": author}


def _fallback_verse(day: date) -> dict:
    ref, text = sanctuary_content.FALLBACK_VERSES[day.toordinal() % len(sanctuary_content.FALLBACK_VERSES)]
    return {"reference": ref, "text": text, "version": "WEB", "permalink": "", "fallback": True}


async def _fetch_votd() -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            response = await client.get(sanctuary_content.VOTD_URL)
            response.raise_for_status()
            votd = response.json().get("votd") or {}
        text = html.unescape(re.sub(r"<[^>]+>", "", votd.get("text") or "")).strip()
        reference = html.unescape(votd.get("display_ref") or votd.get("reference") or "").strip()
        if not text or not reference:
            return None
        return {
            "reference": reference,
            "text": text,
            "version": "NIV",
            "permalink": html.unescape(votd.get("permalink") or ""),
            "fallback": False,
        }
    except Exception:
        return None


@router.get("/api/sanctuary/daily")
async def sanctuary_daily(user: dict = Depends(_unlocked_user)):
    user_id = int(user["id"])
    today = _today_ist()
    cache_key = f"daily:{today.isoformat()}"
    cached = await sanctuary_db.get_json_state(user_id, cache_key, None)
    if cached:
        return cached
    verse = await _fetch_votd()
    payload = {
        "date": today.isoformat(),
        "verse": verse or _fallback_verse(today),
        "quote": _pick_quote(today),
    }
    if verse:  # only cache the real feed; a fallback day retries on next load
        await sanctuary_db.set_json_state(user_id, cache_key, payload)
    return payload


# ── Journal ──────────────────────────────────────────────────────


def _clean_entry_fields(payload: dict, partial: bool = False) -> dict:
    fields: dict = {}
    if "entry_date" in payload or not partial:
        entry_date = str(payload.get("entry_date") or _today_ist().isoformat())
        if not _DATE_RE.match(entry_date):
            raise HTTPException(status_code=400, detail="Bad entry_date")
        fields["entry_date"] = entry_date
    if "kind" in payload or not partial:
        kind = str(payload.get("kind") or "note")
        if kind not in ENTRY_KINDS:
            raise HTTPException(status_code=400, detail="Bad kind")
        fields["kind"] = kind
    for key, limit in (("title", 300), ("body", 20000), ("music", 500)):
        if key in payload or not partial:
            fields[key] = str(payload.get(key) or "")[:limit]
    if "mood" in payload:
        mood = payload.get("mood")
        fields["mood"] = int(mood) if mood is not None and str(mood).isdigit() else None
        if fields["mood"] is not None and not 1 <= fields["mood"] <= 5:
            raise HTTPException(status_code=400, detail="Mood is 1–5")
    if "photos" in payload:
        photos = payload.get("photos") or []
        if not isinstance(photos, list) or len(photos) > 20:
            raise HTTPException(status_code=400, detail="Bad photos list")
        cleaned = []
        for photo in photos:
            file_id = str((photo or {}).get("file") or "")
            if not _PHOTO_PATH_RE.match(file_id):
                raise HTTPException(status_code=400, detail="Bad photo reference")
            cleaned.append({"file": file_id, "caption": str((photo or {}).get("caption") or "")[:300]})
        fields["photos"] = cleaned
    return fields


@router.get("/api/sanctuary/journal")
async def journal_list(
    month: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    user: dict = Depends(_unlocked_user),
):
    if month and not _MONTH_RE.match(month):
        raise HTTPException(status_code=400, detail="Bad month")
    if kind and kind not in ENTRY_KINDS:
        raise HTTPException(status_code=400, detail="Bad kind")
    entries = await sanctuary_db.list_entries(int(user["id"]), month=month, kind=kind, query=q)
    return {"entries": entries}


@router.get("/api/sanctuary/journal/months")
async def journal_months(
    kind: str | None = None,
    q: str | None = None,
    user: dict = Depends(_unlocked_user),
):
    """Which months hold writing — so the book can turn out of one into the
    next instead of stopping at the month's edge."""
    if kind and kind not in ENTRY_KINDS:
        raise HTTPException(status_code=400, detail="Bad kind")
    return {"months": await sanctuary_db.entry_months(int(user["id"]), kind=kind, query=q)}


@router.post("/api/sanctuary/journal")
async def journal_create(request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    fields = _clean_entry_fields(payload)
    entry_id = await sanctuary_db.create_entry(int(user["id"]), fields)
    if fields.get("mood"):
        await sanctuary_db.upsert_mood(int(user["id"]), fields["entry_date"], fields["mood"])
    return {"id": entry_id}


@router.put("/api/sanctuary/journal/{entry_id}")
async def journal_update(entry_id: int, request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    fields = _clean_entry_fields(payload, partial=True)
    if not await sanctuary_db.update_entry(int(user["id"]), entry_id, fields):
        raise HTTPException(status_code=404, detail="No such entry")
    return {"ok": True}


@router.delete("/api/sanctuary/journal/{entry_id}")
async def journal_delete(entry_id: int, user: dict = Depends(_unlocked_user)):
    entry = await sanctuary_db.delete_entry(int(user["id"]), entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such entry")
    for photo in entry.get("photos", []):
        _remove_photo_file(int(user["id"]), photo.get("file", ""))
    return {"ok": True}


@router.post("/api/sanctuary/mood")
async def mood_set(request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    mood_date = str(payload.get("date") or _today_ist().isoformat())
    mood = payload.get("mood")
    if not _DATE_RE.match(mood_date) or not isinstance(mood, int) or not 1 <= mood <= 5:
        raise HTTPException(status_code=400, detail="Bad mood payload")
    await sanctuary_db.upsert_mood(int(user["id"]), mood_date, mood, str(payload.get("note") or "")[:300])
    return {"ok": True}


@router.get("/api/sanctuary/moods")
async def moods_get(month: str, user: dict = Depends(_unlocked_user)):
    if not _MONTH_RE.match(month):
        raise HTTPException(status_code=400, detail="Bad month")
    start, end = _month_bounds(month)
    return {"moods": await sanctuary_db.moods_for_range(int(user["id"]), start, end)}


@router.get("/api/sanctuary/songs")
async def songs_get(user: dict = Depends(_unlocked_user)):
    return {"songs": await sanctuary_db.get_json_state(int(user["id"]), "songs", [])}


@router.put("/api/sanctuary/songs")
async def songs_put(request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    songs = payload.get("songs")
    if not isinstance(songs, list) or len(songs) > 500:
        raise HTTPException(status_code=400, detail="Bad songs list")
    cleaned = [
        {
            "title": str((song or {}).get("title") or "")[:200],
            "artist": str((song or {}).get("artist") or "")[:200],
            "link": str((song or {}).get("link") or "")[:500],
            "note": str((song or {}).get("note") or "")[:300],
        }
        for song in songs
    ]
    await sanctuary_db.set_json_state(int(user["id"]), "songs", cleaned)
    return {"ok": True}


# ── Photos ───────────────────────────────────────────────────────


def _photo_root(user_id: int) -> str:
    return os.path.join(config.USER_DATA_ROOT, str(int(user_id)), "sanctuary")


def _remove_photo_file(user_id: int, file_id: str) -> None:
    if not _PHOTO_PATH_RE.match(file_id or ""):
        return
    path = os.path.join(_photo_root(user_id), file_id)
    try:
        os.remove(path)
    except OSError:
        pass


async def _read_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
        chunks.append(chunk)
    return b"".join(chunks)


# Converting a phone's photograph costs more memory than this machine has to
# spare, and Python does not hand it back when it is done — a journal of a
# hundred and twenty six pictures would leave the server that much heavier
# for good, on a box already deep into its swap.
#
# The first three attempts at that gave the work to a child process. All
# three failed, each in its own way and all of them silently: a pool cannot
# retire a child it made by forking, which is what Linux does by default; a
# forkserver's socket name is too long to live in this machine's temp
# directory; and a spawned child re-opens the pool's semaphore by name, a
# name that is gone by the time the second child asks for it under this
# unit's private /dev/shm. Every one of those lost every picture while the
# writing imported perfectly.
#
# So the conversion happens here, in a thread, one picture at a time — and
# what it cost is handed back to the machine when the import is over.
def _hand_the_memory_back() -> None:
    """Return what the conversion took to the operating system.

    Freeing a picture only returns it to Python's allocator, which keeps it;
    the server's own footprint is what stays large. glibc will give the
    arenas back if it is asked, and asking is the whole reason the work
    used to be done in a child at all. Anywhere that is not glibc, nothing
    happens and nothing breaks.
    """
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001 - a courtesy, never a requirement
        pass


async def _entry_already_here(user_id: int, entry_id, entry: dict) -> dict | None:
    """The entry a fingerprint stands for, by its id or by what it says."""
    if entry_id:
        standing = await sanctuary_db.get_entry(user_id, int(entry_id))
        if standing:
            return standing
    for standing in await sanctuary_db.list_entries(user_id, month=entry["entry_date"][:7]):
        if (
            standing["entry_date"] == entry["entry_date"]
            and (standing.get("title") or "") == entry["title"]
            and (standing.get("body") or "") == entry["body"]
        ):
            return standing
    return None


def _write_photo(user_id: int, data: bytes, extension: str, on: date) -> str:
    rel_dir = on.strftime("%Y/%m")
    directory = os.path.join(_photo_root(user_id), rel_dir)
    os.makedirs(directory, exist_ok=True)
    name = f"{on.strftime('%d')}-{secrets.token_hex(8)}{extension}"
    fd = os.open(os.path.join(directory, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return f"{rel_dir}/{name}"


def _keep_photo(user_id: int, data: bytes, declared: str, on: date) -> str:
    """Decode a picture, strip it, and file it under the day it belongs to.

    An imported entry's picture is filed under the day it was taken, not the
    day it was imported, so the folders on disk still read as a diary.
    """
    cleaned = sanitize_image(data, declared)
    rel_dir = on.strftime("%Y/%m")
    directory = os.path.join(_photo_root(user_id), rel_dir)
    os.makedirs(directory, exist_ok=True)
    name = f"{on.strftime('%d')}-{secrets.token_hex(8)}{cleaned.extension}"
    fd = os.open(os.path.join(directory, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(cleaned.data)
    return f"{rel_dir}/{name}"


@router.post("/api/sanctuary/photos")
async def photo_upload(file: UploadFile, user: dict = Depends(_unlocked_user)):
    data = await _read_upload(file)
    try:
        stored = await asyncio.to_thread(_keep_photo, int(user["id"]), data, file.content_type or "", _today_ist())
    except ImageValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"file": stored}


# ── The iPhone's own Journal ─────────────────────────────────────
#
# Nothing can connect to it: Apple keeps those entries in an encrypted store
# on the phone with no API. The app exports them though — Journal ▸ ⋯ ▸
# Export writes AppleJournalEntries.zip — and that is what this reads. The
# same zip offered twice adds nothing the second time.

_APPLE_JOURNAL_SEEN = "apple_journal_seen"
_MAX_JOURNAL_ZIP = 600 * 1024 * 1024


@router.post("/api/sanctuary/journal/import/apple")
async def apple_journal_import(file: UploadFile, request: Request, user: dict = Depends(_unlocked_user)):
    user_id = int(user["id"])
    # A journal off a phone is a hundred megabytes and more; it goes to disk
    # as it arrives and is read from there. Held in memory it would be the
    # largest thing on this machine, twice over.
    spool = tempfile.NamedTemporaryFile(prefix="apple-journal-", suffix=".zip", delete=False)
    try:
        size = 0
        with spool:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_JOURNAL_ZIP:
                    raise HTTPException(status_code=413, detail="That export is larger than this can take")
                spool.write(chunk)
        if not size:
            raise HTTPException(status_code=400, detail="Empty file")
        return await _bring_the_journal_over(spool.name, user_id, request)
    finally:
        try:
            os.remove(spool.name)
        except OSError:
            pass


async def _bring_the_journal_over(path: str, user_id: int, request: Request) -> dict:
    try:
        entries = await asyncio.to_thread(sanctuary_apple_journal.read_export, path, False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    form = await request.form()
    dry_run = str(form.get("look") or "") == "1"
    # What has come over already, and where it landed. It was a plain list
    # once; a list read back is a map with nothing known about where.
    remembered = await sanctuary_db.get_json_state(user_id, _APPLE_JOURNAL_SEEN, {})
    seen = dict(remembered) if isinstance(remembered, dict) else {fp: None for fp in remembered}
    fresh = [entry for entry in entries if entry["fingerprint"] not in seen]
    undated = [entry for entry in fresh if not entry["entry_date"]]
    span = [entry["entry_date"] for entry in fresh if entry["entry_date"]]
    if dry_run:
        # Days already on the shelf whose pictures never made it — the whole
        # of his first import. Counted here so the page can offer to fetch
        # them rather than saying "all of those are already here" and stopping.
        fillable = 0
        for entry in entries:
            if entry["fingerprint"] not in seen or not entry["photos"] or not entry["entry_date"]:
                continue
            standing = await _entry_already_here(user_id, seen.get(entry["fingerprint"]), entry)
            if standing is not None and not standing.get("photos"):
                fillable += 1
        return {
            "entries": len(entries),
            "fresh": len(fresh),
            "fillable": fillable,
            "already_here": len(entries) - len(fresh),
            "undated": len(undated),
            "pictures": sum(len(entry["photos"]) for entry in fresh)
            or sum(len(entry["photos"]) for entry in entries if entry["fingerprint"] in seen),
            # A video has nowhere to live here; better said than dropped quietly.
            "videos": sum(entry.get("videos") or 0 for entry in fresh),
            "from": min(span, default=""),
            "to": max(span, default=""),
        }

    written, filled, pictures, skipped, lost = 0, 0, 0, 0, 0
    trouble = ""

    async def _photographs(entry: dict, on: date) -> list[dict]:
        """The entry's pictures, converted and filed. What cannot be read is
        counted and named — a hundred and twenty six failures once looked
        exactly like a hundred and twenty six successes."""
        nonlocal lost, trouble
        kept = []
        for picture in entry["photos"]:
            # Fetched one at a time and let go of before the next, so the
            # whole journal never sits in memory at once.
            try:
                blob = await asyncio.to_thread(sanctuary_apple_journal.picture_bytes, path, picture["member"])
                data, extension, _ = await asyncio.to_thread(sanitized_parts, blob, "")
                del blob
                stored = await asyncio.to_thread(_write_photo, user_id, data, extension, on)
                del data
            except Exception as exc:  # noqa: BLE001 - one bad picture is not a lost entry
                lost += 1
                if not trouble:
                    trouble = f"{type(exc).__name__}: {exc}"
                    _logger.warning("Apple Journal import could not keep a picture — %s", trouble)
                continue
            kept.append({"file": stored, "caption": picture["caption"]})
        return kept

    for entry in fresh:
        if not entry["entry_date"]:
            skipped += 1
            continue
        on = date.fromisoformat(entry["entry_date"])
        kept = await _photographs(entry, on)
        pictures += len(kept)
        entry_id = await sanctuary_db.create_entry(
            user_id,
            {
                "entry_date": entry["entry_date"],
                "kind": "photo" if kept and not entry["body"] else "note",
                "title": entry["title"],
                "body": entry["body"],
                "music": "",
                "mood": None,
                "photos": kept,
            },
        )
        seen[entry["fingerprint"]] = entry_id
        written += 1

    # An entry that came over before but lost its pictures on the way — the
    # whole of his first import — is filled in rather than brought again.
    just_written = {entry["fingerprint"] for entry in fresh}
    for entry in entries:
        if entry["fingerprint"] in just_written or entry["fingerprint"] not in seen:
            continue
        if not entry["photos"] or not entry["entry_date"]:
            continue
        standing = await _entry_already_here(user_id, seen.get(entry["fingerprint"]), entry)
        if standing is None or standing.get("photos"):
            continue
        kept = await _photographs(entry, date.fromisoformat(entry["entry_date"]))
        if not kept:
            continue
        await sanctuary_db.update_entry(user_id, standing["id"], {"photos": kept})
        seen[entry["fingerprint"]] = standing["id"]
        pictures += len(kept)
        filled += 1

    await sanctuary_db.set_json_state(user_id, _APPLE_JOURNAL_SEEN, seen)
    if pictures or lost:
        await asyncio.to_thread(_hand_the_memory_back)
    return {
        "written": written,
        "filled": filled,
        "pictures": pictures,
        "pictures_lost": lost,
        "trouble": trouble,
        "videos": sum(entry.get("videos") or 0 for entry in fresh),
        "already_here": len(entries) - len(fresh),
        "undated": skipped,
        "from": min(span, default=""),
        "to": max(span, default=""),
    }


@router.get("/api/sanctuary/photos/{file_path:path}")
async def photo_serve(file_path: str, user: dict = Depends(_unlocked_user)):
    if not _PHOTO_PATH_RE.match(file_path):
        raise HTTPException(status_code=404, detail="Not found")
    root = os.path.realpath(_photo_root(int(user["id"])))
    full = os.path.realpath(os.path.join(root, file_path))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(full)


# ── The Vault: identity papers, encrypted at rest ────────────────

FAMILY_ACTION_CLASS = "vault-family"
_VAULT_CATEGORIES = ("Identity", "Work", "Vehicle", "Finance", "Family", "Other")
_VAULT_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    # A household's papers are not only PDFs: the tax workings arrive as
    # spreadsheets, letters as documents, notes as plain text.
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "text/plain": ".txt",
    "text/csv": ".csv",
}

# A file saved without an extension arrives typed as octet-stream or as
# nothing at all; its first bytes still say what it is.
_MAGIC = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),
    (b"\xd0\xcf\x11\xe0", "application/vnd.ms-excel"),
    (b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
)


def _sniff_content_type(blob: bytes, declared: str) -> str:
    if declared in _VAULT_CONTENT_TYPES:
        return declared
    for magic, kind in _MAGIC:
        if blob.startswith(magic):
            return kind
    try:
        blob[:2048].decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        return ""


def _vault_root(user_id: int) -> str:
    return os.path.join(config.USER_DATA_ROOT, str(int(user_id)), "vault")


def _vault_file_path(user_id: int, token: str) -> str:
    root = os.path.realpath(_vault_root(user_id))
    full = os.path.realpath(os.path.join(root, f"{token}.enc"))
    if not full.startswith(root + os.sep):
        raise HTTPException(status_code=404, detail="Not found")
    return full


def _clean_document_fields(payload: dict) -> dict:
    fields: dict = {}
    if "title" in payload:
        fields["title"] = str(payload.get("title") or "").strip()[:120]
        if not fields["title"]:
            raise HTTPException(status_code=400, detail="The document needs a name")
    if "category" in payload:
        category = str(payload.get("category") or "Other").strip()
        fields["category"] = category if category in _VAULT_CATEGORIES else "Other"
    for key, limit in (("doc_number", 120), ("note", 500), ("series", 80)):
        if key in payload:
            fields[key] = str(payload.get(key) or "").strip()[:limit]
    if "doc_date" in payload:
        doc_date = str(payload.get("doc_date") or "")
        if doc_date and not _DATE_RE.match(doc_date):
            raise HTTPException(status_code=400, detail="Bad date")
        fields["doc_date"] = doc_date
    # The number is sensitive on its own — it rests encrypted like the file.
    if fields.get("doc_number"):
        fields["doc_number"] = auth.encrypt_value(fields["doc_number"])
    return fields


def _document_view(doc: dict) -> dict:
    view = {
        k: doc[k]
        for k in (
            "id",
            "title",
            "category",
            "note",
            "series",
            "doc_date",
            "filename",
            "content_type",
            "size",
            "created_at",
        )
    }
    view["doc_number"] = auth.decrypt_value(doc.get("doc_number") or "")
    return view


@router.get("/api/sanctuary/vault")
async def vault_list(user: dict = Depends(_unlocked_user)):
    docs = [_document_view(d) for d in await sanctuary_db.list_documents(int(user["id"]))]
    return {"documents": docs, "encryption_ready": auth.encryption_enabled()}


@router.post("/api/sanctuary/vault")
async def vault_upload(file: UploadFile, request: Request, user: dict = Depends(_unlocked_user)):
    if not auth.encryption_enabled():
        raise HTTPException(
            status_code=503,
            detail="The vault refuses to store documents unencrypted — ENCRYPTION_KEY is not configured.",
        )
    declared = (file.content_type or "").split(";")[0].strip().lower()
    blob = await _read_upload(file)
    if not blob:
        raise HTTPException(status_code=400, detail="Empty file")
    upload_name = file.filename or ""
    if looks_like_heic(blob):
        # A picture straight out of an Apple photo library. It is turned to
        # JPEG at the door, because what the vault keeps has to open in any
        # browser years from now, not only on his own machine.
        try:
            picture = await asyncio.to_thread(sanitize_image, blob, "image/heic")
        except ImageValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        blob, declared = picture.data, picture.content_type
        upload_name = f"{(upload_name or 'picture').rsplit('.', 1)[0]}.jpg"
    content_type = _sniff_content_type(blob, declared)
    if not content_type:
        raise HTTPException(status_code=400, detail="Papers and pictures only — that file is something else")
    # The same paper offered twice — inside one folder drop, or a folder
    # re-picked after a deploy — is acknowledged, not stored again.
    content_sha = hashlib.sha1(blob, usedforsecurity=False).hexdigest()
    existing = await sanctuary_db.find_document_by_sha(int(user["id"]), content_sha)
    if not existing:
        # Rows stored before fingerprints existed: a matching name and size
        # is worth opening to compare, and either way learns its fingerprint.
        for old in await sanctuary_db.documents_without_sha(
            int(user["id"]), (upload_name or "document")[:160], len(blob)
        ):
            stored = _read_document_blob(old, int(user["id"]))
            if stored is not None:
                await sanctuary_db.set_document_sha(
                    int(user["id"]),
                    old["id"],
                    hashlib.sha1(stored, usedforsecurity=False).hexdigest(),
                )
                if stored == blob:
                    existing = old
                    break
    if existing:
        return {"id": existing["id"], "duplicate": True, "title": existing.get("title") or ""}
    encrypted = auth.encrypt_bytes(blob)
    if encrypted is None:
        raise HTTPException(status_code=503, detail="Encryption unavailable")
    form = await request.form()
    if str(form.get("auto") or "") == "1":
        # A whole folder at once: the filename and its parent already say
        # what each paper is, so nothing needs typing 150 times.
        read = sanctuary_docs.classify_document(upload_name, str(form.get("folder") or ""))
        payload = dict(read)
        payload["note"] = str(form.get("folder") or "")
    else:
        payload = {
            "title": form.get("title") or (upload_name or "Document"),
            "category": form.get("category"),
            "doc_number": form.get("doc_number"),
            "note": form.get("note"),
            "series": form.get("series"),
            "doc_date": form.get("doc_date"),
        }
    fields = _clean_document_fields(payload)
    token = secrets.token_hex(16)
    directory = _vault_root(int(user["id"]))
    os.makedirs(directory, exist_ok=True)
    fd = os.open(os.path.join(directory, f"{token}.enc"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(encrypted)
    fields.update(
        {
            "filename": (upload_name or "document")[:160],
            "content_type": content_type,
            "size": len(blob),
            "file_token": token,
            "content_sha": content_sha,
        }
    )
    doc_id = await sanctuary_db.create_document(int(user["id"]), fields)
    salary = None
    if content_type == "application/pdf":
        salary = await _salary_from_payslip(int(user["id"]), blob)
    return {"id": doc_id, "salary": salary}


def _previous_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{year - 1:04d}-12" if mon == 1 else f"{year:04d}-{mon - 1:02d}"


async def _pay_that_arrived(user_id: int, month: str, months_state: dict, default_salary: float) -> float:
    """The pay that LANDED in this month — his envelope for the next one.

    Not last month's leftover: a month's spending was funded by the month
    before it, so subtracting it here would take the same rupees out twice.
    What travels into August is what arrived at the end of July, whole. One
    month back and no further — a running balance would compound every
    mis-filed row into a figure nobody could check.
    """
    entry = months_state.get(month) or {}
    stated, said = _stated_salary(entry)
    if _salary_outranks_the_bank(said):  # his own figure, or a payslip's
        return stated
    ledger = await sanctuary_db.list_ledger(user_id, month)
    return round(_salary_from_ledger(ledger) or (default_salary if stated is None else stated), 2)


def _stated_salary(entry: dict) -> tuple[float | None, str]:
    """This month's pay as it has been written down, and who wrote it.

    Only his own correction and a payslip outrank what the bank actually
    saw arrive. A figure with nobody behind it does not: it was written
    before this page recorded who said what, and it is exactly the stale
    one — his tile sat on an old employer's number for months while the
    statement underneath it carried the true credit, because any stored
    figure at all stopped the bank from being consulted.
    """
    if "salary" not in entry:
        return None, ""
    try:
        figure = round(float(entry["salary"]), 2)
    except (TypeError, ValueError):
        return None, ""
    said = str(entry.get("salary_source") or "")
    return figure, said if said in ("manual", "payslip") else "kept"


def _salary_outranks_the_bank(said: str) -> bool:
    return said in ("manual", "payslip")


def _salary_from_ledger(ledger: list[dict]) -> float:
    """What the bank saw arrive from his employer this month.

    Pay can land in two credits — arrears, a shift allowance paid apart, or
    the month an employer changes and both of them pay — so the month's pay
    credits are summed rather than picking the largest. Only money coming IN
    counts: a debit to an employer is not pay.

    An employer's own credit is the trustworthy one. Filed under Salary is
    not the same thing: a transfer of somebody else's pay into the house
    reads "Salary" too, and adding it made his month look five and ten
    thousand rupees richer than it was. So a named employer answers first,
    and the filing only answers for months where no employer is named — the
    old imports, which predate the payer being known, and which nothing
    should need re-importing to correct.
    """
    credits = [row for row in ledger if row.get("source") == "statement-in"]
    from_employer = [row for row in credits if any(word in (row.get("note") or "").lower() for word in _EMPLOYER_WORDS)]
    counted = from_employer or [row for row in credits if row.get("category") == "Salary"]
    return round(sum(row["amount"] for row in counted), 2)


async def _salary_from_payslip(user_id: int, blob: bytes) -> dict | None:
    """A payslip knows the month it belongs to and what actually arrived.

    Whatever it says fills that month's salary, so the tiles stop waiting to
    be told what the paper already knows. A month the owner set by hand is
    left alone — his correction outranks the reader.
    """
    try:
        read = await asyncio.to_thread(_read_payslip_pdf, blob)
    except Exception:  # noqa: BLE001 - an unreadable upload is not an error
        return None
    if not read:
        # A paper that calls itself a payslip and still will not parse is
        # worth saying out loud. Filed in silence, it looked exactly like a
        # payslip that had been read, and the tile it should have corrected
        # went on showing a figure from a previous employer.
        try:
            announced = await asyncio.to_thread(_payslip_text_unread, blob)
        except Exception:  # noqa: BLE001
            announced = False
        return {"unread": True} if announced else None
    months = await sanctuary_db.get_json_state(user_id, "months", {})
    entry = dict(months.get(read["month"]) or {})
    if entry.get("salary_source") == "manual":
        return None
    entry["salary"] = read["net"]
    entry["salary_source"] = "payslip"
    if read.get("employer"):
        entry["salary_note"] = read["employer"]
    months[read["month"]] = entry
    await sanctuary_db.set_json_state(user_id, "months", months)
    return {"month": read["month"], "net": read["net"], "employer": read.get("employer", "")}


def _payslip_text_unread(blob: bytes) -> bool:
    """Whether the upload announced itself a payslip though nothing parsed."""
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(blob)) as pdf:
        return sanctuary_docs.looks_like_a_payslip(pdf.pages[0].extract_text() or "")


def _read_payslip_pdf(blob: bytes) -> dict | None:
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(blob)) as pdf:
        text = pdf.pages[0].extract_text() or ""
    return sanctuary_docs.read_payslip(text)


def _read_document_blob(doc: dict, user_id: int) -> bytes | None:
    """The stored paper, decrypted — or None if it is gone or unreadable."""
    try:
        full = _vault_file_path(user_id, doc.get("file_token") or "missing")
    except HTTPException:
        return None
    if not os.path.isfile(full):
        return None
    with open(full, "rb") as handle:
        return auth.decrypt_bytes(handle.read())


def _serve_document(doc: dict, user_id: int):
    blob = _read_document_blob(doc, user_id)
    if blob is None:
        raise HTTPException(status_code=404, detail="The file is gone from disk or cannot be decrypted")
    from fastapi.responses import Response

    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", doc.get("filename") or "document")
    return Response(
        content=blob,
        media_type=doc.get("content_type") or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{safe_name}"', "Cache-Control": "no-store"},
    )


@router.get("/api/sanctuary/vault/{doc_id}/file")
async def vault_file(doc_id: int, user: dict = Depends(_unlocked_user)):
    doc = await sanctuary_db.get_document(int(user["id"]), doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="No such document")
    return _serve_document(doc, int(user["id"]))


# One refile at a time per user; the page polls this while it runs. Opening
# and reading a few hundred encrypted papers takes minutes on the small box —
# done inside the request, nginx gave up at 60s and the button looked dead.
_REFILE_JOBS: dict[int, dict] = {}


async def _refile_job(user_id: int) -> None:
    state = _REFILE_JOBS[user_id]
    try:
        # ── fingerprints first, so twins can be seen ──
        docs = await sanctuary_db.list_documents(user_id)
        state["total"] = len(docs)
        by_sha: dict[str, dict] = {}
        for doc in docs:
            state["scanned"] += 1
            sha = doc.get("content_sha") or ""
            if not sha:
                blob = await asyncio.to_thread(_read_document_blob, doc, user_id)
                if blob is None:
                    continue
                sha = hashlib.sha1(blob, usedforsecurity=False).hexdigest()
                await sanctuary_db.set_document_sha(user_id, doc["id"], sha)
                doc["content_sha"] = sha
                # A payslip stored before the salary reader existed gets its
                # month filled now, while its pages are open anyway. Only a
                # payslip is worth the PDF reader's time.
                if (doc.get("content_type") or "") == "application/pdf" and doc.get("series") == "Payslips":
                    await _salary_from_payslip(user_id, blob)
            keeper = by_sha.get(sha)
            if keeper is None:
                by_sha[sha] = doc
                continue
            # Two rows, one paper: keep the better-described of the pair.
            loser = doc
            if keeper["category"] == "Other" and doc["category"] != "Other":
                by_sha[sha], loser = doc, keeper
            gone = await sanctuary_db.delete_document(user_id, loser["id"])
            if gone:
                state["removed"] += 1
                try:
                    os.unlink(_vault_file_path(user_id, gone.get("file_token") or "missing"))
                except OSError:
                    pass
        for doc in await sanctuary_db.list_documents(user_id):
            folder = doc.get("note") or ""
            if folder.count("/") >= 0 and folder.startswith("payslips"):
                folder = "/".join(folder.split("/")[1:])
            read = sanctuary_docs.classify_document(doc.get("filename") or "", folder)
            changed = {}
            if read["category"] != doc["category"]:
                changed["category"] = read["category"]
            if read["series"] != doc["series"]:
                changed["series"] = read["series"]
            if read["doc_date"] and not doc["doc_date"]:
                changed["doc_date"] = read["doc_date"]
            if folder != (doc.get("note") or ""):
                changed["note"] = folder
            if changed:
                await sanctuary_db.update_document(user_id, doc["id"], changed)
                state["moved"] += 1
    finally:
        state["running"] = False


@router.post("/api/sanctuary/vault/refile")
async def vault_refile(user: dict = Depends(_unlocked_user)):
    """Read every document's name again, for when the reading improves.

    Each row keeps the folder it arrived from in its note, so nothing needs
    re-uploading. A document whose title he has edited himself is left alone.
    While every file is open anyway, each learns its content fingerprint, and
    a paper stored twice under one fingerprint is kept only once — the copy
    that knows what it is, or failing that the oldest. The work runs in the
    background and the page watches /refile/status until it settles.
    """
    user_id = int(user["id"])
    state = _REFILE_JOBS.get(user_id)
    if state and state.get("running"):
        return {"started": False, **state}
    _REFILE_JOBS[user_id] = {"running": True, "scanned": 0, "total": 0, "moved": 0, "removed": 0}
    asyncio.create_task(_refile_job(user_id))
    return {"started": True, **_REFILE_JOBS[user_id]}


@router.get("/api/sanctuary/vault/refile/status")
async def vault_refile_status(user: dict = Depends(_unlocked_user)):
    return _REFILE_JOBS.get(int(user["id"])) or {"running": False, "scanned": 0, "total": 0, "moved": 0, "removed": 0}


# Reading the registration numbers out of a few hundred encrypted papers
# takes as long as a refile does, and for the same reason: every PDF has to
# be decrypted and opened. Same shape, then — a background job the page
# watches — and the numbers are only ever OFFERED, never written.
_IDENTITY_JOBS: dict[int, dict] = {}
# A payslip or a tax paper first; they are where the numbers live. Anything
# else is only opened if those run out before the numbers are corroborated.
_IDENTITY_FIRST = ("Payslips", "Tax", "Form 16", "EPF", "NPS", "Salary")


def _identity_rank(doc: dict) -> tuple[int, str]:
    haystack = f"{doc.get('category', '')} {doc.get('series', '')} {doc.get('title', '')} {doc.get('filename', '')}"
    for index, word in enumerate(_IDENTITY_FIRST):
        if word.lower() in haystack.lower():
            return (index, str(doc.get("doc_date") or ""))
    return (len(_IDENTITY_FIRST), str(doc.get("doc_date") or ""))


def _identity_text(blob: bytes) -> str:
    """The first pages of a PDF as text — the numbers are always on page 1."""
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(blob)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages[:3])


async def _identity_job(user_id: int) -> None:
    state = _IDENTITY_JOBS[user_id]
    tally: dict[str, dict] = {}
    try:
        docs = [
            doc
            for doc in await sanctuary_db.list_documents(user_id)
            if (doc.get("content_type") or "") == "application/pdf"
        ]
        docs.sort(key=_identity_rank)
        state["total"] = len(docs)
        for doc in docs:
            state["scanned"] += 1
            blob = await asyncio.to_thread(_read_document_blob, doc, user_id)
            if blob is None:
                continue
            try:
                text = await asyncio.to_thread(_identity_text, blob)
            except Exception:
                # A locked Form 16 or a scan with no text layer. Not a failure
                # of the scan — just a paper that will not say anything.
                state["unreadable"] += 1
                continue
            found = sanctuary_identity.find_identifiers(text)
            if found:
                sanctuary_identity.merge_findings(tally, found, doc.get("title") or doc.get("filename") or "a paper")
                state["found"] = len(sanctuary_identity.best_of(tally))
            if sanctuary_identity.is_complete(tally):
                state["stopped_early"] = True
                break
        state["numbers"] = sanctuary_identity.best_of(tally)
        state["others"] = sanctuary_identity.runners_up(tally)
    finally:
        state["running"] = False


def _blank_identity_job() -> dict:
    return {
        "running": False,
        "scanned": 0,
        "total": 0,
        "found": 0,
        "unreadable": 0,
        "stopped_early": False,
        "numbers": [],
        "others": [],
    }


@router.post("/api/sanctuary/identity/scan")
async def identity_scan(user: dict = Depends(_unlocked_user)):
    """Read the vault for the registrations a working life leaves behind.

    Nothing is saved by this call. It reports what the papers say and how
    many of them agree, and he decides what to keep — a number that lands
    on Important info without his say-so is a number nobody has checked.
    """
    user_id = int(user["id"])
    state = _IDENTITY_JOBS.get(user_id)
    if state and state.get("running"):
        return {"started": False, **state}
    _IDENTITY_JOBS[user_id] = {**_blank_identity_job(), "running": True}
    asyncio.create_task(_identity_job(user_id))
    return {"started": True, **_IDENTITY_JOBS[user_id]}


# ── The provident fund ───────────────────────────────────────────
#
# A working life leaves one member account per employer, and the papers
# for them sit in the vault like everything else. Reading them is the only
# way the sanctuary can say what the fund holds: no bank statement ever
# shows it, and the EPFO portal cannot be asked on his behalf.


# How many unnamed PDFs the fallback sweep will open before giving up, and
# how long it may spend doing it. A vault of hundreds must never hold the
# request open long enough for the gateway to give up on it.
_EPF_SWEEP = 400
_EPF_SWEEP_SECONDS = 25


def _epf_papers(docs: list[dict]) -> list[dict]:
    """The vault documents worth opening for the fund.

    A passbook or a claim, by where it was filed or by what it is called.
    Everything else in the vault is left shut — this is not a search of his
    papers, it is a read of the ones that are about the fund.
    """
    wanted = []
    for doc in docs:
        if (doc.get("content_type") or "") != "application/pdf":
            continue
        where = f"{doc.get('series', '')} {doc.get('category', '')}".lower()
        called = f"{doc.get('title', '')} {doc.get('filename', '')}"
        # A year-wise passbook is named after the member account and nothing
        # else, so the name is matched the way the filing rules match it.
        if (
            "epf" in where
            or "epf" in called.lower()
            or "passbook" in called.lower()
            or "provident" in called.lower()
            # A claim form is named for the claim, not the fund. Opening one
            # costs nothing: a paper that is not the fund's reads as None.
            or "claim" in called.lower()
            or sanctuary_docs.looks_like_member_id(called)
        ):
            wanted.append(doc)
    return wanted


@router.post("/api/sanctuary/epf/scan")
async def epf_scan(user: dict = Depends(_unlocked_user)):
    """Read every provident fund paper in the vault, and keep the total.

    Unlike the identity scan, this one saves: the figures are the papers'
    own, not a guess between them, so there is nothing for him to tick.
    """
    user_id = int(user["id"])
    everything = await sanctuary_db.list_documents(user_id)
    pdfs = [d for d in everything if (d.get("content_type") or "") == "application/pdf"]
    docs = _epf_papers(everything)
    papers: list[dict] = []
    unreadable = 0

    async def read_them(these: list[dict]) -> None:
        nonlocal unreadable
        for doc in these:
            blob = await asyncio.to_thread(_read_document_blob, doc, user_id)
            if blob is None:
                continue
            try:
                text = await asyncio.to_thread(_identity_text, blob)
            except Exception:
                # A scan with no text layer says nothing, and says it quietly.
                unreadable += 1
                continue
            read = sanctuary_epf.read_epf(text)
            if read:
                papers.append(read)

    await read_them(docs)
    # A passbook filed under a name nobody would guess is still a passbook.
    # When the names find nothing, the papers themselves are asked — newest
    # first, and only as far as _EPF_SWEEP, because this is a vault of
    # hundreds and each PDF costs a read.
    swept = 0
    ran_out = False
    if not papers:
        rest = [d for d in pdfs if d not in docs][:_EPF_SWEEP]
        started = time.monotonic()
        for doc in rest:
            if time.monotonic() - started > _EPF_SWEEP_SECONDS:
                ran_out = True
                break
            swept += 1
            await read_them([doc])

    summary = sanctuary_epf.summarise(papers, _today_ist())
    summary["read"] = len(papers)
    summary["looked_at"] = len(docs) + swept
    summary["pdfs_in_vault"] = len(pdfs)
    summary["swept"] = swept
    summary["gave_up_early"] = ran_out
    summary["unreadable"] = unreadable
    summary["scanned_on"] = _today_ist().isoformat()
    await sanctuary_db.set_json_state(user_id, "epf", summary)
    return summary


async def _fund_with_claims_settled(user_id: int) -> dict:
    """The fund as the papers left it, with any claim the bank has since
    paid marked as answered.

    Both places that show the fund ask this. Fixing one and not the other is
    why the standing panel called the claim settled while the fund's own
    card went on promising four and a half lakh that had already arrived.
    """
    fund = await sanctuary_db.get_json_state(user_id, "epf", {})
    asked = min(
        (str(c.get("asked_on") or "") for c in fund.get("claims") or [] if c.get("awaiting")),
        default="",
    )
    if not asked:
        return fund
    return sanctuary_epf.settle_claims(fund, await sanctuary_db.credits_since(user_id, asked), _today_ist())


@router.get("/api/sanctuary/epf")
async def epf_get(user: dict = Depends(_unlocked_user)):
    """What the papers found, and what the bank has since paid against it."""
    return await _fund_with_claims_settled(int(user["id"]))


@router.get("/api/sanctuary/identity/scan/status")
async def identity_scan_status(user: dict = Depends(_unlocked_user)):
    return _IDENTITY_JOBS.get(int(user["id"])) or _blank_identity_job()


@router.post("/api/sanctuary/identity/keep")
async def identity_keep(request: Request, user: dict = Depends(_unlocked_user)):
    """Keep the numbers he has ticked, beside the accounts and the cards.

    They join the same encrypted store, as their own kind, so a number the
    scan read and a number he typed himself are indistinguishable from here
    on — and re-running the scan never duplicates one he already holds.
    """
    payload = await request.json()
    items = payload.get("numbers")
    if not isinstance(items, list) or len(items) > 20:
        raise HTTPException(status_code=400, detail="Bad numbers list")
    user_id = int(user["id"])
    stored = await sanctuary_db.get_json_state(user_id, "accounts", [])
    held = {(auth.decrypt_value(a.get("number") or ""), a.get("kind")) for a in stored}
    kept = 0
    for item in items:
        item = item or {}
        number = str(item.get("number") or "").strip()[:40]
        kind = str(item.get("kind") or "")
        if not number or kind not in dict(sanctuary_identity.KINDS):
            continue
        if (number, "id") in held:
            continue
        stored.append(
            {
                "id": secrets.token_hex(4),
                "kind": "id",
                "bank": "",
                "label": sanctuary_identity.LABELS.get(kind, kind),
                "number": auth.encrypt_value(number),
                "note": str(item.get("note") or "")[:300],
            }
        )
        held.add((number, "id"))
        kept += 1
    await sanctuary_db.set_json_state(user_id, "accounts", stored)
    return {"kept": kept}


@router.put("/api/sanctuary/vault/{doc_id}")
async def vault_update(doc_id: int, request: Request, user: dict = Depends(_unlocked_user)):
    fields = _clean_document_fields(await request.json())
    if not fields or not await sanctuary_db.update_document(int(user["id"]), doc_id, fields):
        raise HTTPException(status_code=404, detail="No such document")
    return {"ok": True}


@router.delete("/api/sanctuary/vault/{doc_id}")
async def vault_delete(doc_id: int, user: dict = Depends(_unlocked_user)):
    doc = await sanctuary_db.delete_document(int(user["id"]), doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="No such document")
    try:
        os.unlink(_vault_file_path(int(user["id"]), doc.get("file_token") or "missing"))
    except OSError:
        pass
    return {"ok": True}


# ── Family access: the vault outlives its keeper ─────────────────
#
# Phil's ask, 2026-08-27: "I want my family to access these docs here
# after me." The sanctuary itself stays his alone — this is a separate,
# read-only door. A family member signs in with their own PhilForge
# account (any role), opens /vault, and gives the family passcode he
# set. Wrong passcode is 403 with the same slow-down as the sanctuary
# gate; no passcode set means the door does not exist.


async def _vault_owner() -> dict | None:
    return await core_db.get_admin_user()


async def _family_user(request: Request) -> dict:
    token = auth.get_session_token(request)
    session = await auth.validate_session(token)
    user = await core_db.get_user_by_id(session["user_id"]) if session else None
    if not user or not user.get("is_active"):
        raise HTTPException(status_code=401, detail="Sign in first")
    granted = await core_db.has_action_grant(int(user["id"]), auth.session_storage_key(token), FAMILY_ACTION_CLASS)
    if not granted:
        raise HTTPException(status_code=423, detail="vault_locked")
    return user


@router.post("/api/sanctuary/vault/family/passcode")
async def vault_family_passcode(request: Request, user: dict = Depends(_unlocked_user)):
    """The owner sets (or clears) the family passcode."""
    payload = await request.json()
    passcode = str(payload.get("passcode") or "")
    if not passcode:
        await sanctuary_db.set_state(int(user["id"]), "vault_family_hash", "")
        return {"family_access": False}
    if len(passcode) < 8:
        raise HTTPException(status_code=400, detail="Use at least 8 characters")
    await sanctuary_db.set_state(int(user["id"]), "vault_family_hash", auth.hash_password(passcode))
    return {"family_access": True}


@router.get("/api/sanctuary/vault/family/status")
async def vault_family_status(user: dict = Depends(_unlocked_user)):
    stored = await sanctuary_db.get_state(int(user["id"]), "vault_family_hash", "")
    return {"family_access": bool(stored)}


@router.post("/api/vault/unlock")
async def vault_family_unlock(request: Request):
    token = auth.get_session_token(request)
    session = await auth.validate_session(token)
    user = await core_db.get_user_by_id(session["user_id"]) if session else None
    if not user or not user.get("is_active"):
        raise HTTPException(status_code=401, detail="Sign in first")
    user_id = int(user["id"])
    if _too_many_failures(user_id):
        raise HTTPException(status_code=429, detail="Too many tries — rest a while and come back")
    owner = await _vault_owner()
    stored = await sanctuary_db.get_state(int(owner["id"]), "vault_family_hash", "") if owner else ""
    payload = await request.json()
    passcode = str(payload.get("passcode") or "")
    if not stored or not auth.verify_password(passcode, stored):
        _unlock_failures.setdefault(user_id, []).append(time.monotonic())
        await asyncio.sleep(0.6)
        raise HTTPException(status_code=403, detail="That is not the family key")
    _unlock_failures.pop(user_id, None)
    expires_at = (datetime.now(ZoneInfo("UTC")) + timedelta(seconds=UNLOCK_TTL_SECONDS)).isoformat()
    await core_db.grant_action_class(user_id, auth.session_storage_key(token), FAMILY_ACTION_CLASS, expires_at)
    return {"unlocked": True, "ttl_seconds": UNLOCK_TTL_SECONDS}


@router.get("/api/vault/documents")
async def vault_family_list(user: dict = Depends(_family_user)):
    owner = await _vault_owner()
    if not owner:
        return {"documents": []}
    docs = [_document_view(d) for d in await sanctuary_db.list_documents(int(owner["id"]))]
    return {"documents": docs, "owner": owner.get("username") or ""}


@router.get("/api/vault/documents/{doc_id}/file")
async def vault_family_file(doc_id: int, user: dict = Depends(_family_user)):
    owner = await _vault_owner()
    doc = await sanctuary_db.get_document(int(owner["id"]), doc_id) if owner else None
    if not doc:
        raise HTTPException(status_code=404, detail="No such document")
    return _serve_document(doc, int(owner["id"]))


@router.get("/vault", response_class=HTMLResponse)
async def vault_page(request: Request):
    token = auth.get_session_token(request)
    session = await auth.validate_session(token)
    user = await core_db.get_user_by_id(session["user_id"]) if session else None
    if not user or not user.get("is_active"):
        return RedirectResponse("/app", status_code=307)
    page_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vault.html")
    with open(page_path, encoding="utf-8") as handle:
        return HTMLResponse(handle.read())


# ── Finance: categories, months, ledger ──────────────────────────


def _od_movement(ledger: list[dict]) -> dict:
    """What the overdraft did this month, in the language of a debt.

    Drawn is money that came OUT of the overdraft into his account — the
    debt growing. Repaid is money swept back the other way. `deeper` is the
    net: positive means he ended the month owing the overdraft more than he
    started it. None of it is spending, and none of it is saving; it is the
    shape of a borrowing, which is why it is reported on its own.
    """
    drawn = sum(r["amount"] for r in ledger if r["category"] == OD_CATEGORY and r["source"] == "statement-in")
    repaid = sum(r["amount"] for r in ledger if r["category"] == OD_CATEGORY and r["source"] != "statement-in")
    account = ""
    for row in ledger:
        if row["category"] != OD_CATEGORY:
            continue
        found = sanctuary_statements.od_account(row.get("note") or "")
        if found:
            account = found
            break
    return {
        "drawn": round(drawn, 2),
        "repaid": round(repaid, 2),
        "deeper": round(drawn - repaid, 2),
        "moves": sum(1 for r in ledger if r["category"] == OD_CATEGORY),
        "account": account,
    }


OD_HELD_STATE = "od_held"


async def _money_sitting_in_the_od(user_id: int) -> dict:
    """What is in the sweep account, and how it has moved since he said so.

    A sweep-linked overdraft is one account, not a line of credit sitting
    somewhere abstract. When the current account is in surplus the money
    sweeps INTO it and stays there; when the current account is short it
    sweeps back out. So the balance printed on his statement is what is left
    AFTER the sweep took money away — the two hundred and forty-four thousand
    that went across on the first of September is his, and appeared nowhere.

    He states it once. Every sweep after that day carries it: money swept in
    adds to it, money swept back out takes from it, which is exactly what
    those rows already record.
    """
    held = await sanctuary_db.get_json_state(user_id, OD_HELD_STATE, {})
    try:
        limit = round(float(held.get("limit")), 2)
    except (TypeError, ValueError):
        limit = None
    try:
        stated = round(float(held.get("amount")), 2)
    except (TypeError, ValueError):
        return {"held": None, "stated": None, "on": "", "moved": 0.0, "sweeps": 0, "limit": limit}
    on = str(held.get("on") or "")
    rows = await sanctuary_db.ledger_rows_in_categories(user_id, [OD_CATEGORY], since=on) if on else []
    into = sum(r["amount"] for r in rows if r.get("source") != "statement-in")
    out = sum(r["amount"] for r in rows if r.get("source") == "statement-in")
    moved = round(into - out, 2)
    return {
        "held": round(stated + moved, 2),
        "stated": stated,
        "on": on,
        "moved": moved,
        "sweeps": len(rows),
        # What it would LEND him if he drew on it. Recorded because it is
        # worth knowing and because the two are so easily confused — a bank
        # showing "available balance" adds the two together, and a page that
        # did the same would be telling him he owns money he would have to
        # borrow. It is never added to anything here.
        "limit": limit,
    }


async def _bank_balance(user_id: int) -> dict:
    """What is actually in the account, and how stale that is.

    A statement prints its own running balance beside every row, and that
    figure is right even where the ledger has gaps — it is the bank's
    arithmetic, not a total assembled here out of rows that may be missing.
    Rows posted after it are counted so a figure that has been overtaken
    can say so rather than pretending to be today's.
    """
    known = await sanctuary_db.balance_last_known(user_id)
    od = await _money_sitting_in_the_od(user_id)
    if known["balance"] is None:
        return {"balance": None, "as_of": "", "after": 0, "current": None, "in_od": od["held"]}
    after = await sanctuary_db.rows_since(user_id, known["as_of"])
    # The statement's figure is one account. Money parked in the sweep
    # account is the same money and belongs in the same total, shown apart
    # so the sum can be checked rather than believed.
    total = round(known["balance"] + (od["held"] or 0), 2)
    return {
        "balance": total,
        "current": known["balance"],
        "in_od": od["held"],
        "od_on": od["on"],
        "od_limit": od.get("limit"),
        "od_sweeps": od["sweeps"],
        "as_of": known["as_of"],
        "after": after,
    }


def _a_balance(value) -> float | None:
    """A running balance off a statement, or None where it printed none."""
    try:
        if value is None:
            return None
        balance = float(value)
    except (TypeError, ValueError):
        return None
    return balance if -100_000_000 <= balance <= 100_000_000 else None


def _od_standing(od_rows: list[dict]) -> dict:
    """What the sweeps say about the overdraft, and whether they can say it.

    An overdraft's floor is zero. It is drawn by some amount or it is clear;
    it never holds money, and any reckoning that produces a credit is a
    reckoning that has lost something.

    Adding both directions up over his ledger produced exactly that — sixty
    thousand "in credit" — and the ledger says why itself: the very first
    sweep it holds is a REPAYMENT, and the running total is under water from
    that row onward. The account was alive before these rows begin, so its
    opening balance is missing and no arithmetic here can invent it. Where
    the running total never goes below zero the sweeps do tell the whole
    story and the net is the balance; where it dips, only the bank knows,
    which is what this page said before it started guessing.
    """
    running, lowest = 0.0, 0.0
    for row in sorted(od_rows, key=lambda r: (str(r.get("entry_date") or ""), r.get("id") or 0)):
        running += row["amount"] if row.get("source") == "statement-in" else -row["amount"]
        lowest = min(lowest, running)
    dates = sorted(str(r.get("entry_date") or "") for r in od_rows if r.get("entry_date"))
    knowable = bool(od_rows) and lowest > -0.005
    return {
        "net": round(running, 2),
        "lowest": round(lowest, 2),
        "knowable": knowable,
        "owing": round(max(running, 0.0), 2) if knowable else None,
        "sweeps": len(od_rows),
        "from": dates[0] if dates else "",
        "to": dates[-1] if dates else "",
    }


def _od_anchor(today: str, od_rows: list[dict]) -> str:
    """The day a newly stated balance should be pinned to.

    The later of today and the newest sweep already on the ledger. Pinning
    at today alone let a sweep dated ahead of today be applied again on the
    next read — accepting the carried-forward figure deducted the same
    eighteen thousand a second time, and again every time the card opened.
    """
    return max([today, *(str(row.get("entry_date") or "") for row in od_rows)])


def _od_view(ledger: list[dict], loans: list[dict], since_rows: list[dict] | None = None) -> dict:
    """The overdraft as the page shows it: what he owes, then what moved.

    The two are different kinds of fact and the card must not blur them.
    What he owes is the bank's figure, which only he can supply; what moved
    is the sweeps, which is all a partial ledger can honestly claim.
    Reporting only the second invited it to be read as the first.

    Between the two sits `now`: the stated balance carried forward by every
    sweep the ledger has seen since the day he stated it. It is an estimate
    and is named as one — it is only as complete as the statements he has
    imported — so it is OFFERED rather than written over his figure.
    """
    view = _od_movement(ledger)
    loan = _od_loan(loans, view["account"])
    # Only the reverse sweep spells the account out, so a month holding
    # nothing but repayments would lose the number the card is named by.
    if not view["account"] and loan:
        view["account"] = str(loan.get("account_no") or "")
    view["loan_id"] = loan["id"] if loan else 0
    view["stated_on"] = str(loan.get("stated_on") or "") if loan else ""
    # A figure with no day attached is not a stated balance. Nothing can
    # carry it forward, so it is not today's — and presenting it as owed is
    # how his card sat on three lakh nineteen thousand for months while the
    # sweeps went on underneath it. The panel asks for it instead.
    said = bool(loan and float(loan.get("drawn_amount") or 0) > 0 and view["stated_on"])
    view["owed"] = round(float(loan.get("drawn_amount") or 0), 2) if said else 0.0
    view["owed_said"] = said
    view["owed_unanchored"] = bool(loan and float(loan.get("drawn_amount") or 0) > 0 and not view["stated_on"])
    rows = since_rows or []
    moved = sum(r["amount"] for r in rows if r["source"] == "statement-in") - sum(
        r["amount"] for r in rows if r["source"] != "statement-in"
    )
    view["moved_since"] = round(moved, 2)
    view["sweeps_since"] = len(rows)
    view["now"] = round(view["owed"] + moved, 2) if view["owed_said"] else 0.0
    return view


def _od_loan(loans: list[dict], account: str) -> dict | None:
    """The loan card standing for this overdraft, if he has made one."""
    for loan in loans:
        if account and str(loan.get("account_no") or "") == account:
            return loan
        if str(loan.get("name") or "").lower().startswith(OD_LOAN_NAME.lower()):
            return loan
    return None


def _missing_rule_categories(categories: list[dict]) -> list[dict]:
    """Default categories a rule files into that his own list has lost.

    His list was saved the first time he opened the page, so a category
    added to the defaults afterwards never reaches him — and a rule that
    files into a category the dropdown does not offer is a row he cannot
    correct. Only categories a RULE names are restored; one he deleted on
    purpose and no rule points at stays deleted.
    """
    held = {str(c.get("name") or "") for c in categories}
    wanted = {r["category"] for r in sanctuary_statements.DEFAULT_RULES}
    return [c for c in DEFAULT_CATEGORIES if c["name"] in wanted and c["name"] not in held]


async def _get_categories(user_id: int) -> list[dict]:
    categories = await sanctuary_db.get_json_state(user_id, "categories", None)
    if categories is None:
        categories = DEFAULT_CATEGORIES
        await sanctuary_db.set_json_state(user_id, "categories", categories)
        return categories
    missing = _missing_rule_categories(categories)
    if missing:
        categories = [*categories, *missing]
        await sanctuary_db.set_json_state(user_id, "categories", categories)
    return categories


@router.put("/api/sanctuary/finance/categories")
async def categories_put(request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    categories = payload.get("categories")
    if not isinstance(categories, list) or not 1 <= len(categories) <= 100:
        raise HTTPException(status_code=400, detail="Bad categories list")
    cleaned = []
    for category in categories:
        name = str((category or {}).get("name") or "").strip()[:60]
        if not name:
            continue
        cleaned.append(
            {
                "name": name,
                "emoji": str((category or {}).get("emoji") or "🌱")[:8],
                "kind": "saving" if (category or {}).get("kind") == "saving" else "expense",
                "quick": bool((category or {}).get("quick")),
            }
        )
    if not cleaned:
        raise HTTPException(status_code=400, detail="Bad categories list")
    await sanctuary_db.set_json_state(int(user["id"]), "categories", cleaned)
    return {"categories": cleaned}


@router.put("/api/sanctuary/finance/month")
async def month_put(request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    month = str(payload.get("month") or "")
    if not _MONTH_RE.match(month):
        raise HTTPException(status_code=400, detail="Bad month")
    months = await sanctuary_db.get_json_state(int(user["id"]), "months", {})
    entry = months.get(month) or {}
    if "salary" in payload:
        typed = max(0.0, float(payload.get("salary") or 0))
        # The box opens already holding whatever the tile is showing, which
        # is usually the bank's own figure. Saving it unchanged would freeze
        # that reading as his, outranking the bank for good — and a frozen
        # reading is how a tile came to spend a month showing the pay of the
        # month before. Agreeing with the bank is not a correction: the
        # month goes back to being told by the statement.
        ledger = await sanctuary_db.list_ledger(int(user["id"]), month)
        if typed and typed == _salary_from_ledger(ledger):
            entry.pop("salary", None)
            entry.pop("salary_source", None)
        else:
            entry["salary"] = typed
            # Marked as his, so a payslip uploaded later cannot overwrite
            # what he deliberately typed.
            entry["salary_source"] = "manual"
    if "note" in payload:
        entry["note"] = str(payload.get("note") or "")[:1000]
    months[month] = entry
    await sanctuary_db.set_json_state(int(user["id"]), "months", months)
    # Asking for it to become the usual figure still means it, even when the
    # month itself went back to being told by the bank.
    if payload.get("make_default") and "salary" in payload:
        await sanctuary_db.set_state(
            int(user["id"]), "salary_default", str(max(0.0, float(payload.get("salary") or 0)))
        )
    return {"ok": True}


@router.post("/api/sanctuary/finance/entry")
async def ledger_add(request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    entry_date = str(payload.get("entry_date") or _today_ist().isoformat())
    if not _DATE_RE.match(entry_date):
        raise HTTPException(status_code=400, detail="Bad date")
    try:
        amount = round(float(payload.get("amount")), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Bad amount") from None
    if not 0 < amount <= 100_000_000:
        raise HTTPException(status_code=400, detail="Bad amount")
    row_id = await sanctuary_db.add_ledger(
        int(user["id"]),
        {
            "entry_date": entry_date,
            "category": str(payload.get("category") or "Other")[:60],
            "amount": amount,
            "note": str(payload.get("note") or "")[:500],
            "source": "manual",
        },
    )
    return {"id": row_id}


@router.put("/api/sanctuary/finance/entry/{row_id}")
async def ledger_recategorise_row(row_id: int, request: Request, user: dict = Depends(_unlocked_user)):
    """One row, one correction — a rule is for a payee, this is for an oddity."""
    payload = await request.json()
    category = str(payload.get("category") or "").strip()[:60]
    if not category:
        raise HTTPException(status_code=400, detail="Name the category")
    if not await sanctuary_db.set_ledger_category(int(user["id"]), row_id, category):
        raise HTTPException(status_code=404, detail="No such entry")
    return {"ok": True}


@router.get("/api/sanctuary/holdings")
async def holdings_get(user: dict = Depends(_unlocked_user)):
    """What he owns, as his broker last stated it."""
    return await sanctuary_db.get_json_state(int(user["id"]), "holdings", {})


@router.post("/api/sanctuary/holdings")
async def holdings_import(file: UploadFile, user: dict = Depends(_unlocked_user)):
    """A broker's holdings export, read whole.

    Nothing is fetched live: a holding is worth what the statement said on
    the day it was exported, and the page says which day, because a figure
    presented as this morning's when it is three months old is worse than
    no figure.
    """
    blob = await _read_upload(file)
    if not blob:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        read = await asyncio.to_thread(sanctuary_holdings.parse_holdings, blob)
    except Exception:  # noqa: BLE001 - an unreadable upload is not a crash
        read = None
    if not read:
        raise HTTPException(
            status_code=400,
            detail="No holdings found in that file — it wants the broker's own .xlsx holdings export.",
        )
    read["filename"] = (file.filename or "holdings.xlsx")[:120]
    read["imported_at"] = _today_ist().isoformat()
    await sanctuary_db.set_json_state(int(user["id"]), "holdings", read)
    return read


@router.get("/api/sanctuary/finance/standing")
async def finance_standing(user: dict = Depends(_unlocked_user)):
    """Where he stands: every debt still owed, the monthly load, and what
    the last year's real cashflow says about the road out."""
    import statistics

    user_id = int(user["id"])
    fund = await _fund_with_claims_settled(user_id)
    # The overdraft's own sweeps, so a stale figure typed into its card does
    # not go on being counted as a debt he still owes.
    od_standing = _od_standing(await sanctuary_db.ledger_rows_in_categories(user_id, [OD_CATEGORY]))
    loans = await sanctuary_db.list_loans(user_id)
    od_card = _od_loan(loans, "")
    debts = []
    unknown = 0
    for loan in loans:
        # The overdraft answers to its sweeps, not to a figure typed once
        # and never dated. His said three lakh nineteen thousand still drawn
        # while the sweeps had it sixty thousand to the good, and that phantom
        # was sitting inside every "still to go" on the page.
        # The overdraft answers to its sweeps where they tell the whole
        # story. Where they begin mid-life they cannot, and a figure typed
        # once with no day attached is not today's balance either — so it is
        # counted as a debt of unknown size rather than as a number.
        if loan is od_card and not str(loan.get("stated_on") or "") and od_standing["sweeps"]:
            if od_standing["knowable"]:
                loan = dict(loan) | {
                    "drawn_amount": od_standing["owing"],
                    "active": 1 if od_standing["owing"] else 0,
                }
            elif float(loan.get("drawn_amount") or 0) > 0:
                unknown += 1
                continue
        if not loan.get("active"):
            continue
        emis = await sanctuary_db.emis_for_loan(user_id, loan["id"])
        # The principal, where the schedule keeps a balance column; where it
        # does not, what is left to hand over is the closest honest answer.
        remaining = principal_owed(emis)
        if remaining is None:
            remaining = sum(e["amount"] for e in emis if not e["paid_on"])
        if remaining <= 0:
            # A schedule built from statements only knows the PAST — a
            # running loan with every known EMI paid still owes its future.
            # The card's drawn/outstanding figure fills in; without one the
            # debt is honestly unknown, never zero.
            remaining = float(loan.get("drawn_amount") or 0)
            if remaining <= 0 and float(loan.get("emi_amount") or 0) > 0:
                unknown += 1
                continue
        if remaining > 0:
            debts.append(
                {
                    "name": loan["name"],
                    "remaining": round(remaining, 2),
                    "emi": float(loan.get("emi_amount") or 0),
                }
            )
    total_debt = round(sum(d["remaining"] for d in debts), 2)
    monthly_emi = round(sum(d["emi"] for d in debts), 2)

    flows = await sanctuary_db.monthly_flows(user_id, 13)
    current = _month_key(_today_ist())
    complete = [f for f in flows if f["month"] != current][-12:]
    surpluses = [f["inflow"] - f["outflow"] for f in complete if f["inflow"] > 0]
    surplus = round(statistics.median(surpluses), 2) if len(surpluses) >= 3 else None

    months_out = None
    if surplus and surplus > 0 and total_debt > 0:
        months_out = round(total_debt / surplus)
    # The other side of the ledger. Until now this panel counted only what
    # he owes; what he owns belongs beside it, or "where I stand" is only
    # ever half the truth.
    # Only the current account. The sweep account's three lakh thirty is the
    # overdraft's LIMIT — money the bank would lend him, which his banking
    # app helpfully folds into one "balance" and which this panel repeated as
    # though he owned it. What he owns is what is left when it is taken back
    # out, which is the same subtraction the "left to breathe" tile makes.
    bank = await _bank_balance(user_id)
    bank_total = round(float(bank["current"] or 0), 2)
    owned = await sanctuary_db.get_json_state(user_id, "holdings", {})
    held = round(float(owned.get("present") or 0), 2)
    return {
        "debts": debts,
        "debt_count": len(debts),
        "unknown_count": unknown,
        "total_debt": total_debt,
        "monthly_emi": monthly_emi,
        "flows": complete,
        "surplus_median": surplus,
        "months_to_clear": months_out,
        "held": held,
        "held_invested": round(float(owned.get("invested") or 0), 2),
        "held_gain": round(float(owned.get("gain") or 0), 2),
        "held_as_on": owned.get("as_on") or "",
        # Money in the bank is his too, and it was the one thing he owns that
        # this panel never counted, while the page said the only thing
        # standing between him and the debt was sixty-six thousand of shares.
        "in_bank": bank_total,
        # Said, never added: the headroom is a loan he has not taken.
        "od_headroom": round(float(bank.get("in_od") or 0), 2),
        # Debts minus what he owns: the honest distance to dry land.
        "net": round(held + bank_total - total_debt, 2),
        # The provident fund is his too, and it is the largest thing he owns
        # — but it is not spendable at will, so it is said on its own line
        # rather than folded into the number above.
        "fund": round(float(fund.get("worth") or 0), 2),
        "fund_epf": round(float(fund.get("fund") or 0), 2),
        "fund_pension": round(float(fund.get("pension") or 0), 2),
        "fund_as_of": fund.get("as_of") or "",
        "fund_claim": round(float(fund.get("claimed_pending") or 0), 2),
        "fund_paid_out": round(float(fund.get("paid_out_since") or 0), 2),
        # Only the fund half joins the net. The pension is his, but it is
        # not his to decide about — it comes back as a pension, on the
        # scheme's terms, and counting it as though it could clear a loan
        # would flatter the number he is trying to trust.
        "net_with_fund": round(held + bank_total + float(fund.get("fund") or 0) - total_debt, 2),
    }


@router.get("/api/sanctuary/finance/duplicates")
async def finance_duplicates(user: dict = Depends(_unlocked_user)):
    """Hand-entered rows the statements later told again. Each hand row is
    paired with its closest bank row by amount and date; pairs the owner
    ruled 'truly separate' stay dismissed."""
    from datetime import datetime as _dt

    user_id = int(user["id"])
    hand = await sanctuary_db.ledger_rows_by_sources(user_id, ("manual", "recurring"))
    bank = await sanctuary_db.ledger_rows_by_sources(user_id, ("statement",))
    dismissed = set(await sanctuary_db.get_json_state(user_id, "dup_dismissed", []))

    def day(row):
        return _dt.strptime(row["entry_date"], "%Y-%m-%d").date()

    taken: set[int] = set()
    pairs = []
    for m in hand:
        best = None
        for b in bank:
            if b["id"] in taken or abs(b["amount"] - m["amount"]) >= 0.01:
                continue
            distance = abs((day(b) - day(m)).days)
            if distance <= 3 and (best is None or distance < best[0]):
                best = (distance, b)
        if not best:
            continue
        b = best[1]
        if f"{m['id']}:{b['id']}" in dismissed:
            continue
        taken.add(b["id"])
        pairs.append({"hand": m, "bank": b})
    return {"pairs": pairs}


@router.post("/api/sanctuary/finance/duplicates/resolve")
async def finance_duplicates_resolve(request: Request, user: dict = Depends(_unlocked_user)):
    user_id = int(user["id"])
    payload = await request.json()
    try:
        hand_id, bank_id = int(payload.get("hand_id")), int(payload.get("bank_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Which pair?") from None
    action = str(payload.get("action") or "")
    if action == "merge":
        if not await sanctuary_db.merge_duplicate_pair(user_id, hand_id, bank_id):
            raise HTTPException(status_code=404, detail="That pair is gone")
        return {"ok": True}
    if action == "keep":
        dismissed = await sanctuary_db.get_json_state(user_id, "dup_dismissed", [])
        mark = f"{hand_id}:{bank_id}"
        if mark not in dismissed:
            dismissed.append(mark)
        await sanctuary_db.set_json_state(user_id, "dup_dismissed", dismissed[:1000])
        return {"ok": True}
    raise HTTPException(status_code=400, detail="merge or keep")


@router.get("/api/sanctuary/finance/search")
async def ledger_search(q: str = "", user: dict = Depends(_unlocked_user)):
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(status_code=400, detail="Give the search two characters or more")
    rows = await sanctuary_db.search_ledger(int(user["id"]), query)
    # The rows are capped so the table stays openable; the tally is not, so
    # "how much on Zepto, ever" is answered by all of them and not by the
    # newest four hundred that happened to fit.
    tally = await sanctuary_db.search_ledger_tally(int(user["id"]), query)
    return {"rows": rows, "capped": len(rows) >= 400, **tally}


@router.delete("/api/sanctuary/finance/entry/{row_id}")
async def ledger_delete(row_id: int, user: dict = Depends(_unlocked_user)):
    if not await sanctuary_db.delete_ledger_row(int(user["id"]), row_id):
        raise HTTPException(status_code=404, detail="No such entry")
    return {"ok": True}


# ── Bank statements (the ledger's backfill and its monthly feed) ─


@router.post("/api/sanctuary/statements/parse")
async def statement_parse(file: UploadFile, user: dict = Depends(_unlocked_user)):
    """Parse an uploaded statement into a preview. Nothing posts here."""
    user_id = int(user["id"])
    blob = await _read_upload(file)
    result = await asyncio.to_thread(sanctuary_statements.parse_statement, file.filename or "", blob)
    if result.get("status") != "ok":
        return result
    user_rules = await sanctuary_db.get_json_state(user_id, "stmt_rules", [])
    for row in result["rows"]:
        row["category"] = sanctuary_statements.categorise(row["note"], user_rules)
    existing = await sanctuary_db.existing_ledger_refs(user_id, [r["ref_id"] for r in result["rows"]])
    result["already_posted"] = len(existing)
    result["new_count"] = len(result["rows"]) - len(existing)
    for row in result["rows"]:
        row["posted"] = row["ref_id"] in existing
    # A statement with nothing new in it can still be worth offering: the
    # running balance was thrown away for years, and these rows are carrying
    # it. Without saying so the page told him everything was already here and
    # gave him no way to hand it over.
    bare = await sanctuary_db.refs_missing_balance(user_id, sorted(existing))
    result["fillable"] = sum(
        1 for row in result["rows"] if row.get("posted") and row.get("balance") is not None and row["ref_id"] in bare
    )
    return result


@router.post("/api/sanctuary/statements/commit")
async def statement_commit(request: Request, user: dict = Depends(_unlocked_user)):
    user_id = int(user["id"])
    payload = await request.json()
    rows = payload.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5000:
        raise HTTPException(status_code=400, detail="Bad statement rows")
    cleaned = []
    for row in rows:
        row = row or {}
        entry_date = str(row.get("entry_date") or "")
        ref_id = str(row.get("ref_id") or "")
        try:
            amount = round(float(row.get("amount")), 2)
        except (TypeError, ValueError):
            continue
        if not _DATE_RE.match(entry_date) or not ref_id.startswith("stmt:"):
            continue
        if not 0 < amount <= 100_000_000:
            continue
        cleaned.append(
            {
                "entry_date": entry_date,
                "category": str(row.get("category") or "Uncategorised")[:60],
                "amount": amount,
                "note": str(row.get("note") or "")[:500],
                "source": "statement-in" if row.get("dir") == "in" else "statement",
                "ref_id": ref_id[:120],
                # The statement's own running balance, carried through so the
                # page can say what is actually in the account.
                "balance": _a_balance(row.get("balance")),
            }
        )
    added = await sanctuary_db.add_ledger_many(user_id, cleaned)
    # A statement offered again adds nothing — that is the point — but it may
    # be carrying the running balance the first reading threw away, and this
    # is the only moment it can be recovered: every row is already posted, so
    # nothing else would ever read them.
    filled = await sanctuary_db.backfill_balances(user_id, cleaned)
    account = re.sub(r"\D", "", str(payload.get("account") or ""))
    if added and 9 <= len(account) <= 18:
        await _remember_account_number(user_id, account, str(payload.get("bank") or ""))
    linked = payload.get("linked_accounts")
    if added and isinstance(linked, list):
        for raw in linked[:10]:
            entry = raw if isinstance(raw, dict) else {"number": raw}
            number = re.sub(r"\D", "", str(entry.get("number") or ""))
            kind = str(entry.get("kind") or "Linked account")[:80]
            if 9 <= len(number) <= 18 and number != account:
                await _remember_account_number(user_id, number, kind)
    return {"added": added, "skipped": len(cleaned) - added, "filled": filled}


async def _remember_account_number(user_id: int, account: str, bank: str) -> None:
    """File the statement's account among his accounts, once.

    This used to write a loose note titled "Bank account ··8400", so four
    imports left four notes saying nothing but a number. An account belongs
    in the accounts list, where the panel already shows it by name with what
    the statements prove — and where the number rests encrypted.
    """
    stored = await sanctuary_db.get_json_state(user_id, "accounts", [])
    for item in stored:
        if auth.decrypt_value(item.get("number") or "") == account:
            # Known already — but a statement that now names its bank can
            # fill in a blank one.
            if bank.strip() and not (item.get("bank") or "").strip():
                item["bank"] = bank.strip()[:60]
                await sanctuary_db.set_json_state(user_id, "accounts", stored)
            return
    if len(stored) >= 60:
        return
    stored.append(
        {
            "id": secrets.token_hex(4),
            "kind": "bank",
            "bank": bank.strip()[:60],
            "label": "",
            "number": auth.encrypt_value(account),
            "note": f"found in a statement, {_today_ist().isoformat()}",
        }
    )
    await sanctuary_db.set_json_state(user_id, "accounts", stored)


@router.get("/api/sanctuary/statements/review")
async def statement_review(request: Request, user: dict = Depends(_unlocked_user)):
    """The unsorted rows, grouped by who was paid — the review row's feed.

    Only the biggest are shown, which is right for working down a pile but
    useless for finding ONE payee among fourteen hundred: everyone below the
    fold might as well not exist. So a search runs over the whole pile —
    both the payee's key and a sample of the narration, because he
    remembers "flour" more often than he remembers whose QR it was.
    """
    query = str(request.query_params.get("q") or "").strip().lower()[:60]
    rows = await sanctuary_db.uncategorised_ledger(int(user["id"]))
    groups: dict[str, dict] = {}
    for row in rows:
        key = sanctuary_statements.payee_key(row["note"])
        if query and query not in key.lower() and query not in (row["note"] or "").lower():
            continue
        group = groups.setdefault(
            key,
            {"match": key, "count": 0, "total": 0.0, "sample": row["note"], "in_total": 0.0, "out_total": 0.0},
        )
        group["count"] += 1
        group["total"] = round(group["total"] + row["amount"], 2)
        # Which way the money went. A payee can be both — money sent to the
        # broker and money coming back — so both sides are kept, and the
        # page can say so instead of showing one ambiguous figure.
        side = "in_total" if row["source"] == "statement-in" else "out_total"
        group[side] = round(group[side] + row["amount"], 2)
    ordered = sorted(groups.values(), key=lambda g: -g["total"])
    # Two thousand payees is not a list, it is a page that never ends. They
    # come ten at a time, biggest first, and the page says which ten.
    per = 10
    pages = max(1, -(-len(ordered) // per))
    try:
        page = int(request.query_params.get("page") or 1)
    except ValueError:
        page = 1
    page = min(max(page, 1), pages)
    start = (page - 1) * per
    return {
        "uncategorised": len(rows),
        "payees": len(ordered),
        "matched": sum(g["count"] for g in ordered),
        "query": query,
        "page": page,
        "pages": pages,
        "per": per,
        "groups": ordered[start : start + per],
    }


# Rows the rulebook now files DIFFERENTLY from the day they were imported.
#
# The resort pass reads only the unsorted pile, and that is the right
# default: a row he filed by hand must never be overwritten by a rule.
# But when the MEANING of a rule changes, the rows already sitting under
# the old answer are exactly the ones that have to move — and they are
# invisible to a pass that only looks at what was never sorted.
#
# So each correction names the categories it may take FROM, narrowly. The
# overdraft sweeps were filed as self transfers when the sanctuary thought
# the OD was somewhere he kept money; it is a debt, and both directions of
# a sweep belong to it. Nothing outside those two categories is touched,
# so a row he moved somewhere himself stays where he put it.
# ── one bill, two cards ──
# "Credit card bill" had been holding both of them: the payments that go
# through CRED, which settle the HDFC card, and the direct payments to the
# Citi card that Axis took over. Four hundred and thirty-seven rows with no
# way to tell which card a year's spending had been on.
#
# The narrations name the card themselves, and where the wording does not,
# the card number does — 524216 is the HDFC one, 524133 the Citi one. Only
# rows sitting in that one bucket are touched, so nothing he filed elsewhere
# moves.
_CARD_BILLS = ("Credit card bill",)
_CRED_APP = ("cred.club", "cred.wallet", "credclub@", "payment on cred", "paid via cred", "cred@")

_RECATEGORISE = (
    {
        # CRED settles the HDFC card, and so do the older direct payments
        # that name it or carry its number.
        "match": _CRED_APP + ("hdfc credit", "hdfc due", "hdfc last due", "hdfcdec", "524216"),
        "from": (*_CARD_BILLS, sanctuary_statements.UNCATEGORISED),
        "to": "HDFC Creditcard",
    },
    {
        # The Citi card, which is an Axis card now.
        "match": ("citibank", "citicard", "citi march", "citi due", "524133"),
        "from": (*_CARD_BILLS, sanctuary_statements.UNCATEGORISED),
        "to": "AxisBank Creditcard",
    },
    {
        "match": ("rev sweep", "sweep to od", "sweep from od"),
        "from": ("Self transfer", sanctuary_statements.UNCATEGORISED),
        "to": OD_CATEGORY,
    },
)

# Two names for one category, differing only in case. The rules filed the
# school's fees under "School fees" while the category list — and the quick
# button, and the dropdown — said "School Fees", so Alpha School's money
# landed somewhere he could see but not choose, and "where it went" drew it
# as a second bar beside the one he knew. Same for the electricity board.
# The rules now use the list's spelling and these move the rows already
# filed under the other one. A test keeps a third from appearing.

# A rule taught from one true row can be an accident of the narration. "axis
# bank" was taught from a credit-card payment and matched the BENEFICIARY
# BANK printed in every UPI line, so a haircut, a dress, a dinner and a
# vehicle service were all filed as an Axis credit card — a card he has
# never held. The rule goes, and the rows it filed are read again by the
# rulebook without it. Only rows still sitting under exactly what it said
# move; anything he filed himself stays where he put it.
_RETIRED_RULES = (
    {
        "match": "axis bank",
        "category": "AxisBank Creditcard",
        "why": "it matched the bank printed in every UPI line, not the payee",
    },
    # Paying CRED settles a credit card. Both of these filed those payments
    # as a loan, and he has no loan with CRED — every one of the rows they
    # held is a card being paid off.
    {
        "match": "cred club",
        "category": "CRED Loan",
        "why": "paying CRED settles a card; it is not a loan he holds",
    },
    {
        "match": "credclub",
        "category": "CRED Loan",
        "why": "paying CRED settles a card; it is not a loan he holds",
    },
)


# Names for one thing, gathered up. Seventy-eight categories had grown, and
# several of them were the same thing twice — one by a capital letter, one by
# a typo, and the rest simply because a new name was easier to type than
# finding the old one. Where he chose the surviving name it is his; where he
# did not, the one already holding the most rows wins.
_RENAMED_CATEGORIES = {
    "School fees": "Fees for Oliver and Evin",
    "School Fees": "Fees for Oliver and Evin",
    "Alpha school": "Fees for Oliver and Evin",
    "EB bill": "EB Bill",
    # Anything eaten, wherever it was eaten. Groceries and Milk stay out of
    # it on purpose: provisions for the house are not a meal bought.
    "Eatables": "Eating out",
    "Food & Dining": "Eating out",
    "Zepto": "Eating out",
    # One word for a chemist, a hospital and a doctor.
    "Medical": "Health",
    "Medicines": "Health",
    # The same name twice, differing by a capital.
    "Kotak Loan": "Kotak loan",
    # A typo that split fifteen rows from three.
    "Maduari Karthi": "Madurai Karthi",
    "Citi loan": "CitiBank loan",
    "Home repairs": "Household Repair",
    # Four brokers, one activity. Trading is not investing — he keeps them
    # apart — but neither is money spent: it goes to a broker and it is still
    # his when it gets there.
    "AliceBlue": "Trading",
    "Fyers": "Trading",
    "Zerodha": "Trading",
    "Trading_AngelOne": "Trading",
}


# Names that have never held a row and that nothing files into. Listed by
# name rather than found by a rule, because a rule would come round again
# every time he sorted and take away a category he had just made for
# something he has not spent on yet.
#
# NPS is deliberately not here. It is empty too, but three built-in rules
# file into it, and a category a rule files into while the dropdown does not
# offer it leaves him a row he cannot correct by hand.
# AxisBank Creditcard is deliberately NOT here. It read as a card he had never
# held and I nearly took it away on that reading — but Axis took over Citi's
# cards in India, so his Citi card became an Axis one and the name is exactly
# right. What was wrong was only the rule that filled it: "axis bank" matched
# the beneficiary bank printed in every UPI line, so a haircut, a dress and a
# dinner were filed as card payments. The rule goes; the card stays.
# Money put ASIDE is not money spent. Investments was marked an expense, so
# sixty-four thousand handed to a broker in September read as spending and
# the Saved tile read nought — this page already treats it as a saving when
# it creates the category itself, and only the older stored one disagreed.
_SAVING_CATEGORIES = ("Investments", "Trading")


_RETIRED_CATEGORIES = (
    "Autorickshaw",  # Auto & Cab has held all fifty of them
    "MF SIP",
    "RD / FD",
    "Gold",
)


async def _empty_and_unspoken_for(user_id: int, name: str, rules: list[dict]) -> bool:
    """Whether this category holds nothing and nothing points at it.

    Asked at the moment of removal rather than assumed from the list above:
    if he has started using one since, it stays.
    """
    if any(str(r.get("category") or "") == name for r in rules):
        return False
    if any(r["category"] == name for r in sanctuary_statements.DEFAULT_RULES):
        return False
    return not await sanctuary_db.ledger_rows_in_categories(user_id, [name])


def _recategorised(note: str, category: str) -> str:
    """Where an already-filed row belongs now, or "" to leave it alone."""
    lowered = (note or "").lower()
    for correction in _RECATEGORISE:
        if category not in correction["from"] or category == correction["to"]:
            continue
        if any(sanctuary_statements.rule_matches(lowered, word) for word in correction["match"]):
            return str(correction["to"])
    return ""


@router.post("/api/sanctuary/statements/resort")
async def statement_resort(user: dict = Depends(_unlocked_user)):
    """Run every rule over the unsorted pile again.

    A rule only ever touched rows at import time or when it was taught —
    one added later (Kyndryl as salary, the brokers as investments) never
    reached rows already sitting in the pile, so the pile could only grow.
    This walks the unsorted rows through the full rulebook, his taught
    rules first, and files every row the book now knows. A category a rule
    names into being is created on the way — Investments as a saving, so
    the broker money leaves 'spent' rather than swelling it.
    """
    user_id = int(user["id"])
    user_rules = await sanctuary_db.get_json_state(user_id, "stmt_rules", [])
    # A rule that reads the rail rather than the payee is retired before
    # anything is sorted, so the pass below files by what a row actually is.
    retired = [
        rule
        for rule in user_rules
        if any(
            str(rule.get("match") or "").lower().strip() == gone["match"]
            and str(rule.get("category") or "") == gone["category"]
            for gone in _RETIRED_RULES
        )
    ]
    # The rulebook AS IT WAS, kept for one purpose: telling which rows a rule
    # put where. Repairing them asked that question with the retired rules
    # already taken out, so it was asking what the rulebook would say now —
    # which is the answer it was comparing against. Every row looked like one
    # he had filed himself, every row was spared, and nothing moved.
    rules_before = list(user_rules)
    if retired:
        user_rules = [rule for rule in user_rules if rule not in retired]
        await sanctuary_db.set_json_state(user_id, "stmt_rules", user_rules)
    # ── one name for one thing, before anything is sorted ──
    # The rows, the rules he taught, and the list itself. This has to come
    # first: run after the sorting passes, a rule still pointing at a retired
    # name files fresh rows into it, and the category is put back in the list
    # by the very pass that was meant to retire it.
    moved: dict[str, int] = {}
    renamed = 0
    for was, now in _RENAMED_CATEGORIES.items():
        for row in await sanctuary_db.ledger_rows_in_categories(user_id, [was]):
            await sanctuary_db.set_ledger_category(user_id, row["id"], now)
            moved[now] = moved.get(now, 0) + 1
    for rule in user_rules:
        landing = _RENAMED_CATEGORIES.get(str(rule.get("category") or ""))
        if landing:
            rule["category"] = landing
            renamed += 1
    if renamed:
        await sanctuary_db.set_json_state(user_id, "stmt_rules", user_rules)

    for row in await sanctuary_db.uncategorised_ledger(user_id):
        category = sanctuary_statements.categorise(row["note"], user_rules)
        if category == sanctuary_statements.UNCATEGORISED:
            continue
        await sanctuary_db.set_ledger_category(user_id, row["id"], category)
        moved[category] = moved.get(category, 0) + 1
    # ── undoing what a misreading rulebook filed ──
    # A rule used to be read as a bare substring anywhere in the line,
    # machine writing included, and the first rule that fitted took the row
    # rather than the one that fitted best. So "dd", found inside a
    # transaction's hexadecimal trace, filed the chemist and the chicken
    # shop as school fees, and "philip" filed his own transfers as a broking
    # account because it had been taught before "philip ranjith".
    #
    # A row still sitting under exactly what that reading said is a row a
    # rule put there, and it moves. A row sitting under anything else is one
    # he filed by hand, and it does not.
    # A row is normally never un-filed: landing back in the pile is worse
    # than an imperfect label. But a row filed by a rule that has just been
    # retired has no label — it has a lie — and the pile is exactly where it
    # belongs, because that is where he can teach it what it really is.
    # ── the rows a retired rule filed ──
    # These cannot be found the way every other repair here finds its rows.
    # That test asks what the rulebook USED to say and compares it with where
    # the row sits — and the rule that put them there has just been taken out
    # of the rulebook, so it can no longer own up to them. The first time he
    # sorted, the rules went and not one row moved; they were stranded, and
    # sorting again would never have freed them.
    #
    # A retired rule can still be asked directly. A row sitting in its
    # category whose narration it matches is a row it filed, whether or not
    # it is still in the book.
    for gone in _RETIRED_RULES:
        for row in await sanctuary_db.ledger_rows_in_categories(user_id, [str(gone["category"])]):
            if not sanctuary_statements.rule_matches(row["note"], gone["match"]):
                continue
            now = sanctuary_statements.categorise(row["note"], user_rules)
            if now == row["category"]:
                continue
            await sanctuary_db.set_ledger_category(user_id, row["id"], now)
            moved[now] = moved.get(now, 0) + 1
    voided = {str(gone["category"]) for gone in _RETIRED_RULES}
    for row in await sanctuary_db.every_filed_row(user_id):
        now = sanctuary_statements.categorise(row["note"], user_rules)
        if now == row["category"]:
            continue
        if now == sanctuary_statements.UNCATEGORISED and row["category"] not in voided:
            continue
        if not sanctuary_statements.filed_by_the_old_reading(row["note"], row["category"], rules_before):
            continue
        await sanctuary_db.set_ledger_category(user_id, row["id"], now)
        moved[now] = moved.get(now, 0) + 1
    for correction in _RECATEGORISE:
        for row in await sanctuary_db.ledger_rows_in_categories(user_id, list(correction["from"])):
            moving = _recategorised(row["note"], row["category"])
            if not moving:
                continue
            await sanctuary_db.set_ledger_category(user_id, row["id"], moving)
            moved[moving] = moved.get(moving, 0) + 1
    # Tidied whether or not rows moved: the gathering happens once, and a
    # later sort that finds nothing to move must still be able to take away a
    # name that has since fallen empty.
    categories = await _get_categories(user_id)
    # A retired name must leave the list as well, or it goes on being
    # offered in the dropdown that files rows into it.
    gone = {was for was in _RENAMED_CATEGORIES if was not in _RENAMED_CATEGORIES.values()}
    for name in _RETIRED_CATEGORIES:
        if await _empty_and_unspoken_for(user_id, name, user_rules):
            gone.add(name)
    categories = [c for c in categories if str(c.get("name") or "") not in gone]
    for c in categories:
        if str(c.get("name") or "") in _SAVING_CATEGORIES:
            c["kind"] = "saving"
    have = {c["name"] for c in categories}
    # Money put ASIDE is a saving, so it leaves 'spent'; money that
    # merely arrives is neither — it is filed for the record.
    savings = set(_SAVING_CATEGORIES)
    emoji = {
        "Interest": "🪙",
        "Refund": "↩️",
        "Personal care": "💇",
        "Home repairs": "🔧",
        "Groceries": "🧺",
        "Eating out": "🍽️",
        "Health": "💊",
        "Credit card bill": "💳",
        "Self transfer": "🔁",
        "Salary": "🌾",
        "Investments": "📈",
    }
    # Every category that HOLDS something must be one he can also pick by
    # hand. Eleven were not — Cash withdrawal, Shopping, Fuel, Tithe, Home
    # loan and the rest — holding eight hundred rows between them that he
    # could see and could not correct, because the dropdown had never heard
    # of them. Categories a rule could one day file into but never has are
    # deliberately not added: an empty name he has never used is clutter, and
    # the moment a row does land in one, this same pass makes it pickable.
    holding = await sanctuary_db.categories_in_use(user_id)
    for name in sorted(moved) + sorted(holding - set(moved)):
        if not name or name in gone or name == sanctuary_statements.UNCATEGORISED:
            continue
        if name not in have and len(categories) < 100:
            categories.append(
                {
                    "name": name,
                    "emoji": emoji.get(name, "🏷️"),
                    "kind": "saving" if name in savings else "expense",
                    "quick": False,
                }
            )
            have.add(name)
    # Applied last, so a category made moments ago in this same pass is
    # marked too — Trading was created as an expense because the
    # correction had already run by the time it existed.
    for c in categories:
        if str(c.get("name") or "") in _SAVING_CATEGORIES:
            c["kind"] = "saving"
    await sanctuary_db.set_json_state(user_id, "categories", categories)
    return {"moved": sum(moved.values()), "by_category": moved}


@router.get("/api/sanctuary/statements/rules")
async def statement_rules_get(user: dict = Depends(_unlocked_user)):
    """The rules he has taught, and how much each one is now holding.

    A taught rule is invisible once it is taught: it keeps filing rows for
    ever and the only sign of it is a category that will not stay
    corrected. "kaliraj r means Alpha school", learned from one school
    payment, then claimed every bag of flour from the same shop — and
    correcting the row by hand did nothing, because the rule was still
    there and nothing on the page said so.
    """
    user_id = int(user["id"])
    rules = await sanctuary_db.get_json_state(user_id, "stmt_rules", [])
    counted = []
    for index, rule in enumerate(rules):
        match = str(rule.get("match") or "")
        counted.append(
            {
                "at": index,
                "match": match,
                "category": str(rule.get("category") or ""),
                "holds": await sanctuary_db.count_rows_matching(user_id, match),
            }
        )
    # The defaults travel too, so the ledger can say WHY any row carries the
    # category it does — and, just as usefully, when no rule claims it at
    # all and the answer is simply one he chose himself.
    return {
        "rules": counted,
        "defaults": [{"match": r["match"], "category": r["category"]} for r in sanctuary_statements.DEFAULT_RULES],
    }


@router.delete("/api/sanctuary/statements/rules/{at}")
async def statement_rule_delete(at: int, request: Request, user: dict = Depends(_unlocked_user)):
    """Forget a rule, and optionally hand its rows back to the rulebook.

    Deleting the rule alone leaves its rows where it put them, which is
    almost never what he wants — he is deleting it BECAUSE those rows are
    wrong. So the rows it is holding go back to Uncategorised, where the
    default rules and his own eye can file them again.
    """
    user_id = int(user["id"])
    rules = await sanctuary_db.get_json_state(user_id, "stmt_rules", [])
    # The position is only a handle, and positions move: forget one rule and
    # every rule under it slides up. So the caller says which rule it MEANT,
    # and the word wins over the number — otherwise a second click, or a list
    # drawn a minute ago, forgets a neighbour and releases its rows.
    wanted = str(request.query_params.get("match") or "").strip()
    if wanted:
        at = next(
            (i for i, r in enumerate(rules) if str(r.get("match") or "").lower() == wanted.lower()),
            -1,
        )
    if not 0 <= at < len(rules):
        raise HTTPException(status_code=404, detail="No such rule")
    gone = rules.pop(at)
    await sanctuary_db.set_json_state(user_id, "stmt_rules", rules)
    freed = 0
    if str(request.query_params.get("release") or "1") != "0":
        freed = await sanctuary_db.recategorise_matching(
            user_id,
            str(gone.get("match") or ""),
            sanctuary_statements.UNCATEGORISED,
            str(gone.get("category") or ""),
        )
    return {"forgot": gone.get("match", ""), "freed": freed}


def _rule_word(raw: str) -> str:
    """The word a rule will actually watch for — handles lose their bank
    half, so the rule catches the truncated forms a narration prints."""
    word = str(raw or "").strip()[:80]
    return word[:-1] if word.endswith("@") else word


@router.get("/api/sanctuary/statements/rule/preview")
async def statement_rule_preview(request: Request, user: dict = Depends(_unlocked_user)):
    """What this word would claim if it became a rule.

    A rule matches anywhere inside a narration, so a short word is a net,
    not a name: "bank" is inside every UPI line that names one, and one
    lesson filed a year of newspapers, juice and biriyani under a school.
    The page asks this first and shows him the catch before he casts it.
    """
    word = _rule_word(request.query_params.get("match"))
    source = str(request.query_params.get("from") or sanctuary_statements.UNCATEGORISED).strip()[:60]
    if len(word) < 2:
        return {"match": word, "claims": 0, "samples": [], "wide": False}
    claims, samples = await sanctuary_db.preview_matching(int(user["id"]), word, source)
    # Wide by the shape of the word, or by the size of the catch. Four
    # characters is where words stop being names: "veg", "ban", "icic".
    return {
        "match": word,
        "from": source,
        "claims": claims,
        "samples": samples,
        "wide": len(word) < 5 or claims > 40,
    }


@router.post("/api/sanctuary/statements/rule")
async def statement_rule(request: Request, user: dict = Depends(_unlocked_user)):
    """Teach a rule: this match means this category, now and from now on."""
    user_id = int(user["id"])
    payload = await request.json()
    # A handle-shaped match ("hepzibah08@") is stored by its name stem, so
    # the rule also catches the truncated forms a narration prints.
    match = _rule_word(payload.get("match"))
    category = str(payload.get("category") or "").strip()[:60]
    if len(match) < 2 or not category:
        raise HTTPException(status_code=400, detail="Need a match and a category")
    rules = await sanctuary_db.get_json_state(user_id, "stmt_rules", [])
    rules = [r for r in rules if str(r.get("match", "")).lower() != match.lower()]
    rules.insert(0, {"match": match, "category": category})
    await sanctuary_db.set_json_state(user_id, "stmt_rules", rules[:400])
    categories = await _get_categories(user_id)
    if category not in {c["name"] for c in categories} and len(categories) < 100:
        categories.append({"name": category, "emoji": "🏷️", "kind": "expense", "quick": False})
        await sanctuary_db.set_json_state(user_id, "categories", categories)
    from_category = str(payload.get("from_category") or "Uncategorised").strip()[:60]
    moved = await sanctuary_db.recategorise_matching(user_id, match, category, from_category)
    return {"moved": moved, "rules": len(rules)}


# ── Recurring items (NPS, PF, MF SIP, fees…) ─────────────────────


async def _materialize_recurring(user_id: int) -> None:
    """Post every recurring item for each elapsed month, exactly once."""
    items = await sanctuary_db.get_json_state(user_id, "recurring", [])
    today = _today_ist()
    current = _month_key(today)
    for item in items:
        if not item.get("active", True):
            continue
        start = item.get("start_month") or current
        for month in _months_between(start, current):
            day = min(max(int(item.get("day", 1)), 1), 28)
            if month == current and today.day < day:
                continue
            ref = f"rec:{item.get('id')}:{month}"
            if await sanctuary_db.ledger_ref_exists(user_id, ref):
                continue
            await sanctuary_db.add_ledger(
                user_id,
                {
                    "entry_date": f"{month}-{day:02d}",
                    "category": str(item.get("category") or item.get("name") or "Other")[:60],
                    "amount": round(float(item.get("amount") or 0), 2),
                    "note": str(item.get("name") or ""),
                    "source": "recurring",
                    "ref_id": ref,
                },
            )


@router.put("/api/sanctuary/finance/recurring")
async def recurring_put(request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    items = payload.get("recurring")
    if not isinstance(items, list) or len(items) > 100:
        raise HTTPException(status_code=400, detail="Bad recurring list")
    cleaned = []
    for item in items:
        name = str((item or {}).get("name") or "").strip()[:80]
        try:
            amount = round(float((item or {}).get("amount")), 2)
        except (TypeError, ValueError):
            continue
        if not name or not 0 < amount <= 100_000_000:
            continue
        start_month = str((item or {}).get("start_month") or "")
        cleaned.append(
            {
                "id": str((item or {}).get("id") or secrets.token_hex(4)),
                "name": name,
                "amount": amount,
                "day": min(max(int((item or {}).get("day") or 1), 1), 28),
                "category": str((item or {}).get("category") or name)[:60],
                "start_month": start_month if _MONTH_RE.match(start_month) else _month_key(_today_ist()),
                "active": bool((item or {}).get("active", True)),
            }
        )
    await sanctuary_db.set_json_state(int(user["id"]), "recurring", cleaned)
    await _materialize_recurring(int(user["id"]))
    return {"recurring": cleaned}


# ── Loans and EMI schedules ──────────────────────────────────────


def _clean_loan_fields(payload: dict) -> dict:
    fields: dict = {}
    if "name" in payload:
        fields["name"] = str(payload.get("name") or "").strip()[:80]
        if not fields["name"]:
            raise HTTPException(status_code=400, detail="Loan needs a name")
    for key, limit in (("lender", 80), ("note", 500), ("account_no", 60), ("details", 4000)):
        if key in payload:
            fields[key] = str(payload.get(key) or "")[:limit]
    if "emi_amount" in payload:
        try:
            fields["emi_amount"] = max(0.0, round(float(payload.get("emi_amount") or 0), 2))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Bad EMI amount") from None
    if "drawn_amount" in payload:
        try:
            fields["drawn_amount"] = max(0.0, round(float(payload.get("drawn_amount") or 0), 2))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Bad drawn amount") from None
    if "due_day" in payload:
        fields["due_day"] = min(max(int(payload.get("due_day") or 5), 1), 28)
    if "start_date" in payload:
        start_date = str(payload.get("start_date") or "")
        if start_date and not _DATE_RE.match(start_date):
            raise HTTPException(status_code=400, detail="Bad start date")
        fields["start_date"] = start_date
    if "active" in payload:
        fields["active"] = 1 if payload.get("active") else 0
    if "on_a_card" in payload:
        fields["on_a_card"] = 1 if payload.get("on_a_card") else 0
    return fields


def principal_owed(emis: list[dict]) -> float | None:
    """What a scheduled loan still OWES, as its own schedule says.

    Not the same thing as what is left to hand over: an instalment is part
    interest the lender has not charged yet, so summing the instalments
    counts the bank's future earnings as today's debt. On a home loan with
    twenty-one EMIs left that was thirty-nine thousand rupees of debt he
    did not have — and the loan card and "where I stand" said two
    different numbers for the same loan.

    The balance after the last instalment paid is the answer. Before the
    first payment there is no such line, so the opening balance is the
    first instalment's closing balance plus the principal it will retire.
    A schedule with no balance column cannot say, and returns None.
    """
    for emi in reversed(emis):
        if emi["paid_on"] and emi["outstanding"] is not None:
            return round(float(emi["outstanding"]), 2)
    for emi in emis:
        if emi["outstanding"] is None:
            continue
        opening = float(emi["outstanding"]) + float(emi["principal_part"] or 0)
        return round(opening, 2)
    return None


@router.get("/api/sanctuary/loans")
async def loans_list(user: dict = Depends(_unlocked_user)):
    loans = await sanctuary_db.list_loans(int(user["id"]))
    today = _today_ist().isoformat()
    # The overdraft is the one card whose balance the ledger can work out
    # for itself. A figure he typed stands only while it has a day attached
    # to carry it forward; without one, the sweeps are the better answer and
    # a stale number must not go on being shown as what he owes.
    od_rows = await sanctuary_db.ledger_rows_in_categories(int(user["id"]), [OD_CATEGORY])
    standing = _od_standing(od_rows)
    od_card = _od_loan(loans, "")
    for loan in loans:
        emis = await sanctuary_db.emis_for_loan(int(user["id"]), loan["id"])
        remaining = [e for e in emis if not e["paid_on"] and e["due_date"] >= today]
        overdue = [e for e in emis if not e["paid_on"] and e["due_date"] < today]
        loan["schedule_count"] = len(emis)
        loan["paid_count"] = sum(1 for e in emis if e["paid_on"])
        loan["overdue_count"] = len(overdue)
        loan["remaining_amount"] = round(sum(e["amount"] for e in remaining + overdue), 2)
        upcoming = min((e["due_date"] for e in remaining), default="")
        loan["next_due"] = min((e["due_date"] for e in overdue), default="") or upcoming
        loan["outstanding"] = principal_owed(emis)
        if loan is od_card:
            loan["od_sweeps"] = standing["sweeps"]
            loan["od_from"] = standing["from"]
            loan["od_to"] = standing["to"]
        if od_rows and loan is od_card and not str(loan.get("stated_on") or ""):
            # A figure typed with no day attached cannot be carried forward,
            # so it is not today's balance however true it once was.
            loan["od_unanchored"] = True
            if standing["knowable"]:
                loan["drawn_amount"] = standing["owing"]
                loan["od_by_sweeps"] = True
                loan["active"] = 1 if standing["owing"] else 0
    return {"loans": loans}


@router.post("/api/sanctuary/loans")
async def loan_create(request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    payload.setdefault("name", "")
    fields = _clean_loan_fields(payload)
    loan_id = await sanctuary_db.create_loan(int(user["id"]), fields)
    return {"id": loan_id}


@router.put("/api/sanctuary/od/held")
async def od_held_put(request: Request, user: dict = Depends(_unlocked_user)):
    """Record what is sitting IN the sweep account, off the bank.

    Not what it lends him — what of his is parked in it. The two are
    different facts and the page must never add one to the other.
    """
    payload = await request.json()
    figures = {}
    for field in ("amount", "limit"):
        if field not in payload:
            continue
        try:
            figures[field] = round(float(payload.get(field) or 0), 2)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Bad amount") from None
        if not 0 <= figures[field] <= 100_000_000:
            raise HTTPException(status_code=400, detail="Bad amount")
    amount = figures.get("amount", 0.0)
    user_id = int(user["id"])
    # Anchored the same way the drawn figure is: at the later of today and
    # the newest sweep already posted, so a sweep dated ahead of today is
    # not applied to it twice.
    on = _od_anchor(
        _today_ist().isoformat(),
        await sanctuary_db.ledger_rows_in_categories(user_id, [OD_CATEGORY]),
    )
    kept = await sanctuary_db.get_json_state(user_id, OD_HELD_STATE, {})
    kept.update({"amount": amount, "on": on})
    if "limit" in figures:
        kept["limit"] = figures["limit"]
    await sanctuary_db.set_json_state(user_id, OD_HELD_STATE, kept)
    return {"amount": amount, "on": on, "limit": kept.get("limit")}


@router.put("/api/sanctuary/od/owed")
async def od_owed_put(request: Request, user: dict = Depends(_unlocked_user)):
    """Record what the overdraft actually stands at, off the bank.

    It becomes a loan card like any other, so the figure counts toward the
    total debt instead of living somewhere only this one panel can see. The
    sweeps in the ledger say how it MOVED; this says where it IS, and only
    the bank knows that.
    """
    payload = await request.json()
    try:
        owed = round(float(payload.get("owed") or 0), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Bad amount") from None
    if not 0 <= owed <= 100_000_000:
        raise HTTPException(status_code=400, detail="Bad amount")
    user_id = int(user["id"])
    account = str(payload.get("account") or "")[:40]
    loans = await sanctuary_db.list_loans(user_id)
    loan = _od_loan(loans, account)
    name = f"{OD_LOAN_NAME}{f' ··{account[-4:]}' if account else ''}"
    # The day it was true is what lets every sweep after it carry the figure
    # forward. Without it the balance is a number with no anchor.
    #
    # It anchors at the LATER of today and the newest sweep already on the
    # ledger. Anchoring at today alone let a sweep dated ahead of today be
    # applied again on the very next read, so accepting the carried-forward
    # figure deducted the same eighteen thousand a second time, and a third,
    # every time the card was opened.
    stated_on = _od_anchor(
        _today_ist().isoformat(),
        await sanctuary_db.ledger_rows_in_categories(user_id, [OD_CATEGORY]),
    )
    if loan:
        await sanctuary_db.update_loan(
            user_id,
            loan["id"],
            {"drawn_amount": owed, "stated_on": stated_on, "active": 1 if owed else 0},
        )
        return {"id": loan["id"], "owed": owed, "stated_on": stated_on}
    loan_id = await sanctuary_db.create_loan(
        user_id,
        _clean_loan_fields(
            {
                "name": name,
                "lender": "Sweep-linked overdraft",
                "account_no": account,
                "drawn_amount": owed,
                "note": "A revolving debt: no schedule, only a balance the bank states.",
            }
        )
        | {"stated_on": stated_on},
    )
    return {"id": loan_id, "owed": owed, "stated_on": stated_on}


async def _loan_candidates(user_id: int) -> list[dict]:
    rows = await sanctuary_db.statement_outflow_rows(user_id)
    candidates = await asyncio.to_thread(sanctuary_statements.discover_loans, rows, _today_ist())
    ignored = set(await sanctuary_db.get_json_state(user_id, "loan_ignored", []))
    # A carded stream is recognised by its SCHEDULE, not its key: keys can
    # be textual (a lender whose references never repeat stores no number on
    # the card) and cards get renamed and edited. If a loan's EMI dates cover
    # most of a candidate's debit dates at the same EMI, that stream is
    # already on the shelf — and deleting the card brings the offer back.
    carded = []
    for loan in await sanctuary_db.list_loans(user_id):
        emis = await sanctuary_db.emis_for_loan(user_id, loan["id"])
        carded.append(
            {
                "loan": loan,
                "dates": {e["due_date"] for e in emis},
                "emi": float(loan.get("emi_amount") or 0),
                "acct": str(loan.get("account_no") or ""),
            }
        )

    def match(c):
        dates = {d["due_date"] for d in c["debits"]}
        for entry in carded:
            if abs(entry["emi"] - c["emi"]) > 0.03 * max(c["emi"], 1):
                continue
            if entry["acct"] and entry["acct"] == c["key"]:
                return entry
            if dates and entry["dates"] and len(dates & entry["dates"]) >= 0.6 * len(dates):
                return entry
        return None

    fresh = []
    for c in candidates:
        if c["key"] in ignored or f"{c['key']}#{c['emi']:.0f}" in ignored:
            continue
        entry = match(c)
        if entry is None:
            fresh.append(c)
            continue
        # Already on the shelf — but a card he typed himself carries no loan
        # number, and the statements know it. Fill that gap silently: having
        # the number at hand is the whole reason he asked for it stored.
        loan = entry["loan"]
        if c["key"].isdigit() and not loan.get("account_no"):
            detail = f"Mandate found in the statements: {c['sample']}"
            existing = loan.get("details") or ""
            await sanctuary_db.update_loan(
                user_id,
                loan["id"],
                {
                    "account_no": c["key"],
                    "details": f"{existing}\n\n{detail}" if existing else detail,
                },
            )
            entry["acct"] = c["key"]
            loan["account_no"] = c["key"]
    return fresh


@router.get("/api/sanctuary/loans/discover")
async def loans_discover(user: dict = Depends(_unlocked_user)):
    """EMI-shaped streams the statements remember, not yet on the shelf."""
    candidates = await _loan_candidates(int(user["id"]))
    for cand in candidates:
        cand.pop("debits", None)
    return {"candidates": candidates}


def _match_candidate(candidates: list[dict], key: str, emi) -> dict | None:
    """Two parallel loans share a mandate key — the EMI tells them apart."""
    matches = [c for c in candidates if c["key"] == key]
    if emi is not None:
        matches = [c for c in matches if abs(c["emi"] - float(emi)) <= 0.03 * max(c["emi"], 1)]
    return matches[0] if matches else None


@router.post("/api/sanctuary/loans/adopt")
async def loans_adopt(request: Request, user: dict = Depends(_unlocked_user)):
    """One tap: the stream becomes a loan card wearing its real history."""
    user_id = int(user["id"])
    payload = await request.json()
    key = str(payload.get("key") or "")
    try:
        emi = float(payload["emi"]) if payload.get("emi") is not None else None
    except (TypeError, ValueError):
        emi = None
    cand = _match_candidate(await _loan_candidates(user_id), key, emi)
    if not cand:
        raise HTTPException(status_code=404, detail="That stream is gone or already carded")
    # The offer list filters what is already carded, but a page open since
    # before the last adoption still shows the old list — two taps three
    # minutes apart put the Kotak car loan on the shelf three times. The
    # schedule is checked again HERE, against the shelf as it stands now.
    wanted = {d["due_date"] for d in cand["debits"]}
    for loan in await sanctuary_db.list_loans(user_id):
        if abs(float(loan.get("emi_amount") or 0) - cand["emi"]) > 0.03 * max(cand["emi"], 1):
            continue
        held = {e["due_date"] for e in await sanctuary_db.emis_for_loan(user_id, loan["id"])}
        if wanted and held and len(wanted & held) >= 0.6 * len(wanted):
            raise HTTPException(
                status_code=409,
                detail=f"“{loan['name']}” is already this loan — same instalments, same dates.",
            )
    # The schedule holds one EMI per day, and a bank can debit twice on one
    # date (the CRED mandate did) — same-day debits fold into one row.
    by_date: dict[str, float] = {}
    for debit in cand["debits"]:
        by_date[debit["due_date"]] = round(by_date.get(debit["due_date"], 0) + debit["amount"], 2)
    cand["debits"] = [{"due_date": d, "amount": a} for d, a in sorted(by_date.items())]
    years = cand["first"][:4] + ("–" + cand["last"][:4] if cand["last"][:4] != cand["first"][:4] else "")
    name = str(payload.get("name") or "").strip()[:80] or f"{cand['lender'] or 'Loan'} · {years}"
    loan_id = await sanctuary_db.create_loan(
        user_id,
        {
            "name": name,
            "lender": cand["lender"],
            "emi_amount": cand["emi"],
            "due_day": min(max(int(cand["last"][8:10]), 1), 28),
            "start_date": cand["first"],
            "account_no": cand["key"] if cand["key"].isdigit() else "",
            "note": "found in the statements",
            "details": f"Auto-found from the bank statements: {cand['count']} debits of ~₹{cand['emi']:,.0f}, {cand['first']} to {cand['last']}. Last narration: {cand['sample']}",
            "active": not cand["closed"],
        },
    )
    try:
        await sanctuary_db.replace_schedule(user_id, loan_id, cand["debits"])
        await sanctuary_db.settle_past_emis(user_id, loan_id, cand["last"])
    except Exception:
        # A card without its history is worse than no card — undo and say so.
        await sanctuary_db.delete_loan(user_id, loan_id)
        raise HTTPException(status_code=500, detail="The schedule would not take — nothing was added") from None
    return {"id": loan_id, "name": name, "paid": len(cand["debits"]), "closed": cand["closed"]}


@router.post("/api/sanctuary/loans/schedule")
async def loans_schedule_import(file: UploadFile, user: dict = Depends(_unlocked_user)):
    """A lender's own schedule, read straight onto the shelf.

    A loan drawn on a credit card never appears as a discoverable stream
    until its debits have run for months, and even then the dates are
    guessed. The card issues the truth — every instalment to the last one,
    the rate, and what is still owed — so this reads it whole. Importing
    the same schedule twice updates that loan rather than adding another.
    """
    user_id = int(user["id"])
    blob = await _read_upload(file)
    if not blob:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        read = await asyncio.to_thread(_read_card_schedule_pdf, blob, file.filename or "")
    except Exception:  # noqa: BLE001 - an unreadable upload is not a crash
        read = None
    if not read:
        raise HTTPException(
            status_code=400,
            detail="No loan schedule found in that file — it wants the lender's own EMI table.",
        )

    number = read["loan_number"]
    card = f"card ••{read['card_tail']}" if read["card_tail"] else "a card"
    name = f"{read['kind']} on {card} · {read['booked'][:4]}"
    details = (
        f"From the lender's own schedule: ₹{read['principal']:,.0f} over {read['tenure']} months "
        f"at {read['rate']}%, booked {read['booked']}. Outstanding ₹{read['outstanding']:,.0f}. "
        f"Loan number {number}."
    )
    fields = {
        "name": name,
        "lender": read["kind"],
        "emi_amount": read["emi"],
        "due_day": min(max(int(read["first"][8:10]), 1), 28),
        "start_date": read["first"],
        "account_no": number,
        "note": f"drawn on {card}",
        "details": details,
        "active": read["outstanding"] > 0,
    }
    existing = next(
        (loan for loan in await sanctuary_db.list_loans(user_id) if str(loan.get("account_no") or "") == number),
        None,
    )
    if existing:
        await sanctuary_db.update_loan(user_id, existing["id"], fields)
        loan_id = existing["id"]
    else:
        loan_id = await sanctuary_db.create_loan(user_id, fields)
    try:
        await sanctuary_db.replace_schedule(user_id, loan_id, read["emis"])
        await sanctuary_db.settle_past_emis(user_id, loan_id, _today_ist().isoformat())
    except Exception:
        if not existing:
            await sanctuary_db.delete_loan(user_id, loan_id)
        raise HTTPException(status_code=500, detail="The schedule would not take — nothing was changed") from None
    return {
        "id": loan_id,
        "name": name,
        "updated": bool(existing),
        "instalments": len(read["emis"]),
        "emi": read["emi"],
        "outstanding": read["outstanding"],
        "card_tail": read["card_tail"],
    }


def _read_card_schedule_pdf(blob: bytes, filename: str) -> dict | None:
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(blob)) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    return sanctuary_statements.parse_card_loan_schedule(text, filename)


@router.post("/api/sanctuary/loans/discover/ignore")
async def loans_discover_ignore(request: Request, user: dict = Depends(_unlocked_user)):
    user_id = int(user["id"])
    payload = await request.json()
    key = str(payload.get("key") or "")[:80]
    if not key:
        raise HTTPException(status_code=400, detail="Which one?")
    try:
        mark = f"{key}#{float(payload['emi']):.0f}" if payload.get("emi") is not None else key
    except (TypeError, ValueError):
        mark = key
    ignored = await sanctuary_db.get_json_state(user_id, "loan_ignored", [])
    if mark not in ignored:
        ignored.append(mark)
    await sanctuary_db.set_json_state(user_id, "loan_ignored", ignored[:200])
    return {"ok": True}


@router.put("/api/sanctuary/loans/{loan_id}")
async def loan_update(loan_id: int, request: Request, user: dict = Depends(_unlocked_user)):
    fields = _clean_loan_fields(await request.json())
    if not fields or not await sanctuary_db.update_loan(int(user["id"]), loan_id, fields):
        raise HTTPException(status_code=404, detail="No such loan")
    return {"ok": True}


@router.delete("/api/sanctuary/loans/{loan_id}")
async def loan_delete(loan_id: int, user: dict = Depends(_unlocked_user)):
    if not await sanctuary_db.delete_loan(int(user["id"]), loan_id):
        raise HTTPException(status_code=404, detail="No such loan")
    return {"ok": True}


@router.get("/api/sanctuary/loans/{loan_id}/schedule")
async def schedule_get(loan_id: int, user: dict = Depends(_unlocked_user)):
    if not await sanctuary_db.get_loan(int(user["id"]), loan_id):
        raise HTTPException(status_code=404, detail="No such loan")
    return {"schedule": await sanctuary_db.emis_for_loan(int(user["id"]), loan_id)}


@router.post("/api/sanctuary/loans/{loan_id}/schedule/parse")
async def schedule_parse(
    loan_id: int,
    file: UploadFile,
    request: Request,
    user: dict = Depends(_unlocked_user),
):
    loan = await sanctuary_db.get_loan(int(user["id"]), loan_id)
    if not loan:
        raise HTTPException(status_code=404, detail="No such loan")
    form = await request.form()
    first_due_raw = str(form.get("first_due") or "")
    first_due = date.fromisoformat(first_due_raw) if _DATE_RE.match(first_due_raw) else None
    password = str(form.get("pdf_password") or "") or None
    blob = await _read_upload(file)
    result = await asyncio.to_thread(
        sanctuary_emi.parse_emi_document,
        file.filename or "",
        blob,
        loan.get("due_day") or 5,
        first_due,
        password,
    )
    # A card's linked-loan table is a schedule too, and it is the file the
    # bank actually hands him — but it prints no running balance, so the
    # generic reader finds nothing and says "no schedule here". Rather than
    # asking him to remember which of two buttons reads which file, this
    # one falls back to the card reader and computes the balance itself.
    if not result.get("rows"):
        card = await asyncio.to_thread(_read_card_schedule_pdf, blob, file.filename or "")
        if card:
            result = {
                "status": "ok",
                "rows": card["emis"],
                "needs_first_due": False,
                "source": "card-linked-loan",
                "note": (
                    f"{card['kind']} of ₹{card['principal']:,.0f} over {card['tenure']} months "
                    f"at {card['rate']}%, ₹{card['outstanding']:,.0f} still owed."
                ),
            }
    return result


@router.post("/api/sanctuary/loans/{loan_id}/schedule/commit")
async def schedule_commit(loan_id: int, request: Request, user: dict = Depends(_unlocked_user)):
    loan = await sanctuary_db.get_loan(int(user["id"]), loan_id)
    if not loan:
        raise HTTPException(status_code=404, detail="No such loan")
    payload = await request.json()
    rows = payload.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 600:
        raise HTTPException(status_code=400, detail="Bad schedule rows")
    cleaned = []
    for row in rows:
        due = str((row or {}).get("due_date") or "")
        try:
            amount = round(float((row or {}).get("amount")), 2)
        except (TypeError, ValueError):
            continue
        if not _DATE_RE.match(due) or not 0 < amount <= 100_000_000:
            continue

        def _optional(key: str, source=row):
            value = (source or {}).get(key)
            try:
                return round(float(value), 2) if value is not None else None
            except (TypeError, ValueError):
                return None

        cleaned.append(
            {
                "due_date": due,
                "amount": amount,
                "principal_part": _optional("principal_part"),
                "interest_part": _optional("interest_part"),
                "outstanding": _optional("outstanding"),
            }
        )
    if not cleaned:
        raise HTTPException(status_code=400, detail="No valid rows to commit")
    count = await sanctuary_db.replace_schedule(int(user["id"]), loan_id, cleaned)
    amounts = sorted(row["amount"] for row in cleaned)
    median_amount = amounts[len(amounts) // 2]
    days = sorted(int(row["due_date"][8:10]) for row in cleaned)
    await sanctuary_db.update_loan(
        int(user["id"]),
        loan_id,
        {"emi_amount": median_amount, "due_day": min(days[len(days) // 2], 28)},
    )
    return {"count": count}


@router.post("/api/sanctuary/loans/{loan_id}/schedule/generate")
async def schedule_generate(loan_id: int, request: Request, user: dict = Depends(_unlocked_user)):
    """Build a flat schedule when there is no sheet to upload."""
    loan = await sanctuary_db.get_loan(int(user["id"]), loan_id)
    if not loan:
        raise HTTPException(status_code=404, detail="No such loan")
    payload = await request.json()
    first_due_raw = str(payload.get("first_due") or "")
    if not _DATE_RE.match(first_due_raw):
        raise HTTPException(status_code=400, detail="Bad first due date")
    try:
        months = int(payload.get("months"))
        amount = round(float(payload.get("amount") or loan.get("emi_amount") or 0), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Bad months or amount") from None
    if not 1 <= months <= 600 or not 0 < amount <= 100_000_000:
        raise HTTPException(status_code=400, detail="Bad months or amount")
    first_due = date.fromisoformat(first_due_raw)
    rows = [{"due_date": sanctuary_emi._add_months(first_due, i).isoformat(), "amount": amount} for i in range(months)]
    count = await sanctuary_db.replace_schedule(int(user["id"]), loan_id, rows)
    await sanctuary_db.update_loan(int(user["id"]), loan_id, {"emi_amount": amount, "due_day": min(first_due.day, 28)})
    return {"count": count}


@router.post("/api/sanctuary/loans/{loan_id}/schedule/settle-past")
async def schedule_settle_past(loan_id: int, user: dict = Depends(_unlocked_user)):
    """After uploading an old schedule: mark every past installment paid."""
    if not await sanctuary_db.get_loan(int(user["id"]), loan_id):
        raise HTTPException(status_code=404, detail="No such loan")
    count = await sanctuary_db.settle_past_emis(int(user["id"]), loan_id, _today_ist().isoformat())
    return {"count": count}


@router.post("/api/sanctuary/emis/{emi_id}/paid")
async def emi_mark_paid(emi_id: int, request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    paid_on = str(payload.get("paid_on") or _today_ist().isoformat())
    if not _DATE_RE.match(paid_on):
        raise HTTPException(status_code=400, detail="Bad date")
    if not await sanctuary_db.set_emi_paid(int(user["id"]), emi_id, paid_on):
        raise HTTPException(status_code=404, detail="No such EMI")
    return {"ok": True}


@router.post("/api/sanctuary/emis/{emi_id}/unpaid")
async def emi_mark_unpaid(emi_id: int, user: dict = Depends(_unlocked_user)):
    if not await sanctuary_db.set_emi_paid(int(user["id"]), emi_id, ""):
        raise HTTPException(status_code=404, detail="No such EMI")
    return {"ok": True}


# ── Important info notes ─────────────────────────────────────────


@router.get("/api/sanctuary/notes")
async def notes_get(user: dict = Depends(_unlocked_user)):
    return {"notes": await sanctuary_db.get_json_state(int(user["id"]), "notes", [])}


# The banks and cards he holds. A statement proves an account exists but
# never says whose bank it is, and a card he has never spent from leaves no
# trace at all — so these are his to state, and the number rests encrypted
# like everything else the family may one day need.
# "id" is a registration rather than an account — a UAN, a PAN, a provident
# fund number. It holds a number and belongs on Important info for exactly
# the same reason the bank accounts do.
_ACCOUNT_KINDS = ("bank", "card", "id")


def _account_view(item: dict) -> dict:
    number = auth.decrypt_value(item.get("number") or "")
    # decrypt_value hands the ciphertext back when the key cannot open it,
    # which used to reach the page as an account ending "xKVg==". Better to
    # show nothing than to show a number that is not one.
    if number.startswith("gAAAAA"):
        number = ""
    return {
        "id": item.get("id") or "",
        "kind": item.get("kind") or "bank",
        "bank": item.get("bank") or "",
        "label": item.get("label") or "",
        "number": number,
        "tail": number[-6:] if number else "",
        "note": item.get("note") or "",
    }


@router.get("/api/sanctuary/accounts")
async def accounts_get(user: dict = Depends(_unlocked_user)):
    stored = await sanctuary_db.get_json_state(int(user["id"]), "accounts", [])
    return {"accounts": [_account_view(a) for a in stored]}


@router.put("/api/sanctuary/accounts")
async def accounts_put(request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    items = payload.get("accounts")
    if not isinstance(items, list) or len(items) > 60:
        raise HTTPException(status_code=400, detail="Bad accounts list")
    cleaned = []
    for item in items:
        item = item or {}
        number = str(item.get("number") or "").strip()[:40]
        kind = str(item.get("kind") or "bank")
        cleaned.append(
            {
                "id": str(item.get("id") or secrets.token_hex(4)),
                "kind": kind if kind in _ACCOUNT_KINDS else "bank",
                "bank": str(item.get("bank") or "").strip()[:60],
                "label": str(item.get("label") or "").strip()[:80],
                "number": auth.encrypt_value(number) if number else "",
                "note": str(item.get("note") or "").strip()[:300],
            }
        )
    await sanctuary_db.set_json_state(int(user["id"]), "accounts", cleaned)
    return {"ok": True}


# ── the planner ──────────────────────────────────────────────────────────
def _plan_view(row: dict, today: date) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "due": row["due_date"],
        "kind": row["due_kind"],
        "said": row["said"],
        "note": row["note"],
        "done": bool(row["done"]),
        "done_at": row["done_at"],
        "horizon": sanctuary_plan.horizon(row["due_date"], today) if not row["done"] else "done",
    }


@router.get("/api/sanctuary/plans")
async def plans_get(user: dict = Depends(_unlocked_user)):
    today = _today_ist()
    rows = await sanctuary_db.list_plans(int(user["id"]))
    return {
        "today": today.isoformat(),
        "horizons": list(sanctuary_plan.HORIZONS),
        "plans": [_plan_view(row, today) for row in rows],
    }


def _reminder_channels() -> dict:
    """Where a reminder could go, judged on the credentials alone.

    NOT on PHILFORGE_TELEGRAM_ALERTS_ENABLED. That flag governs the engine's
    automatic alerting — broker failures, order errors — and it is off on
    purpose. A once-a-day planner note he switched on himself is a different
    thing entirely, and gating it behind the trading alert flag would have
    forced him to turn every order error alert on to get it. The flag is
    left exactly as it is; nothing about trading alerting changes here.
    """
    try:
        import alerter

        return {
            "telegram": bool(alerter.TELEGRAM_BOT_TOKEN and alerter.TELEGRAM_CHAT_ID),
            "discord": bool(alerter.DISCORD_WEBHOOK_URL),
        }
    except Exception:  # noqa: BLE001 - no alerter is simply nowhere to send
        return {"telegram": False, "discord": False}


def _nudge_reachable() -> bool:
    """Whether there is anywhere to send a reminder to at all."""
    return any(_reminder_channels().values())


async def _send_reminder(html_text: str, plain_text: str) -> tuple[bool, str]:
    """Post the reminder, and say plainly whether it arrived.

    Awaited rather than fired and forgotten, because the page offers a
    "send one now" and a button that cannot report failure is a button that
    lies.
    """
    import alerter

    channels = _reminder_channels()
    sent, trouble = [], []
    async with httpx.AsyncClient(timeout=12) as client:
        if channels["telegram"]:
            try:
                resp = await client.post(
                    f"https://api.telegram.org/bot{alerter.TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": alerter.TELEGRAM_CHAT_ID,
                        "text": html_text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                if resp.status_code == 200:
                    sent.append("Telegram")
                else:
                    trouble.append(f"Telegram said {resp.status_code}")
            except Exception as err:  # noqa: BLE001 - the reason belongs on the page
                trouble.append(f"Telegram: {type(err).__name__}")
        if channels["discord"]:
            try:
                resp = await client.post(alerter.DISCORD_WEBHOOK_URL, json={"content": plain_text})
                if resp.status_code in (200, 204):
                    sent.append("Discord")
                else:
                    trouble.append(f"Discord said {resp.status_code}")
            except Exception as err:  # noqa: BLE001
                trouble.append(f"Discord: {type(err).__name__}")
    if sent:
        return True, "Sent to " + " and ".join(sent)
    return False, "; ".join(trouble) or "Nowhere configured to send it"


def plan_nudge_text(plans: list[dict], today: date) -> str:
    """The morning message, or "" when nothing is owed today.

    Overdue first, then today, then tomorrow as a warning shot. Nothing is
    sent on a clear morning: a reminder that arrives when there is nothing
    to remind him of teaches him to ignore the ones that matter.
    """
    late, now, next_up = [], [], []
    stamp = today.isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()
    for row in plans:
        due = row.get("due_date") or ""
        if row.get("done") or not due:
            continue
        if due < stamp:
            late.append(row)
        elif due == stamp:
            now.append(row)
        elif due == tomorrow:
            next_up.append(row)
    if not (late or now):
        return ""
    lines = []
    for label, rows in (("Overdue", late), ("Today", now), ("Tomorrow", next_up)):
        if not rows:
            continue
        lines.append(f"<b>{label}</b>")
        for row in rows[:6]:
            when = f" ({row.get('due_kind') or 'on'} {row['due_date']})" if label == "Overdue" else ""
            lines.append(f"· {html.escape(str(row.get('title') or ''))}{when}")
        if len(rows) > 6:
            lines.append(f"· …and {len(rows) - 6} more")
    return "\n".join(lines)


@router.get("/api/sanctuary/plans/nudge")
async def plans_nudge_get(user: dict = Depends(_unlocked_user)):
    state = await sanctuary_db.get_state(int(user["id"]), "plan_nudge", "off")
    channels = _reminder_channels()
    return {
        "on": state == "on",
        "reachable": any(channels.values()),
        "channels": [name.title() for name, ready in channels.items() if ready],
        "at": f"{NUDGE_HOUR:02d}:{NUDGE_MINUTE:02d} IST",
    }


@router.put("/api/sanctuary/plans/nudge")
async def plans_nudge_put(request: Request, user: dict = Depends(_unlocked_user)):
    """Turn the morning reminder on or off.

    OFF is the state it ships in. The message carries the words of a task
    out over Telegram, off a page he put a second password on — that is his
    decision to make, not a default to inherit.
    """
    payload = await request.json()
    on = bool(payload.get("on"))
    await sanctuary_db.set_state(int(user["id"]), "plan_nudge", "on" if on else "off")
    return {"on": on, "reachable": _nudge_reachable()}


def _reminder_message(body: str, today: date) -> tuple[str, str]:
    head = f"🌿 <b>What's coming</b> · {today.strftime('%a %d %b')}"
    return f"{head}\n\n{body}", f"🌿 **What's coming** · {today.strftime('%a %d %b')}\n\n{body}"


async def send_plan_nudge(user_id: int, today: date) -> bool:
    """Send today's reminder if it is wanted, owed, and not already sent."""
    if await sanctuary_db.get_state(user_id, "plan_nudge", "off") != "on":
        return False
    if await sanctuary_db.get_state(user_id, "plan_nudge_sent", "") == today.isoformat():
        return False
    body = plan_nudge_text(await sanctuary_db.list_plans(user_id, include_done=False), today)
    if not body:
        # A quiet morning is stamped so it is not re-examined every quarter
        # of an hour until midnight.
        await sanctuary_db.set_state(user_id, "plan_nudge_sent", today.isoformat())
        return False
    html_text, plain_text = _reminder_message(body, today)
    ok, why = await _send_reminder(html_text, plain_text)
    if not ok:
        # NOT stamped: a network that was down at 07:30 gets another go at
        # 07:45. Stamping on failure would lose the day silently.
        _logger.warning("[SANCTUARY] the morning reminder did not go out: %s", why)
        return False
    await sanctuary_db.set_state(user_id, "plan_nudge_sent", today.isoformat())
    return True


@router.post("/api/sanctuary/plans/nudge/test")
async def plans_nudge_test(user: dict = Depends(_unlocked_user)):
    """Send one now, so he can see it land instead of waiting for 07:30."""
    today = _today_ist()
    body = plan_nudge_text(await sanctuary_db.list_plans(int(user["id"]), include_done=False), today)
    if not body:
        body = "<b>Nothing owed today</b>\n· this is only a test of where the reminder lands"
    ok, why = await _send_reminder(*_reminder_message(body, today))
    if not ok:
        raise HTTPException(status_code=502, detail=why)
    return {"ok": True, "said": why}


# 07:30 IST, looked at every quarter of an hour. This belongs to the app's
# auto-loop registry rather than its startup block: a deploying worker skips
# startup, and a loop started only there never comes back after a deploy.
NUDGE_HOUR, NUDGE_MINUTE, NUDGE_EVERY = 7, 30, 900


async def plan_nudge_loop() -> None:
    while True:
        try:
            now = _now_ist()
            if (now.hour, now.minute) >= (NUDGE_HOUR, NUDGE_MINUTE):
                for user in await core_db.list_users():
                    await send_plan_nudge(int(user["id"]), now.date())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad morning must not end the loop
            _logger.warning("[SANCTUARY] the reminder loop stumbled", exc_info=True)
        await asyncio.sleep(NUDGE_EVERY)


@router.post("/api/sanctuary/plans/read")
async def plans_read(request: Request, user: dict = Depends(_unlocked_user)):
    """What one written line means, without keeping it.

    The page asks this as he types so the date it understood is on screen
    BEFORE he commits to it. A planner that silently decides what "before
    next wednesday" meant is a planner he cannot trust with a school fee.
    """
    payload = await request.json()
    return sanctuary_plan.read_plan(str(payload.get("text") or "")[:300], _today_ist())


@router.post("/api/sanctuary/plans")
async def plans_add(request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    text = str(payload.get("text") or "")[:300]
    read = sanctuary_plan.read_plan(text, _today_ist())
    # He may correct the reading in the form before keeping it; his
    # correction is the record, and the phrase it came from travels with it.
    title = str(payload.get("title") or read["title"]).strip()[:300]
    if not title:
        raise HTTPException(status_code=400, detail="It needs something to do")
    due = str(payload.get("due", read["due"]) or "")[:10]
    if due and not _DATE_RE.match(due):
        raise HTTPException(status_code=400, detail="Bad date")
    kind = str(payload.get("kind") or read["kind"])
    plan_id = await sanctuary_db.add_plan(
        int(user["id"]),
        {
            "title": title,
            "due_date": due,
            "due_kind": kind if kind in sanctuary_plan.KINDS else "on",
            "said": str(payload.get("said", read["said"]) or "")[:80],
            "note": str(payload.get("note") or "")[:500],
        },
    )
    return {"id": plan_id, "title": title, "due": due, "kind": kind, "said": read["said"]}


@router.put("/api/sanctuary/plans/{plan_id}")
async def plans_update(plan_id: int, request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    fields: dict = {}
    if "title" in payload:
        title = str(payload.get("title") or "").strip()[:300]
        if not title:
            raise HTTPException(status_code=400, detail="It needs something to do")
        fields["title"] = title
    if "due" in payload:
        due = str(payload.get("due") or "")[:10]
        if due and not _DATE_RE.match(due):
            raise HTTPException(status_code=400, detail="Bad date")
        fields["due_date"] = due
        # A date he set himself is his own words now, not the reader's.
        fields.setdefault("said", str(payload.get("said") or ""))
    if "kind" in payload:
        kind = str(payload.get("kind") or "on")
        fields["due_kind"] = kind if kind in sanctuary_plan.KINDS else "on"
    if "note" in payload:
        fields["note"] = str(payload.get("note") or "")[:500]
    if "done" in payload:
        fields["done"] = 1 if payload.get("done") else 0
        fields["done_at"] = _now_ist().isoformat() if payload.get("done") else ""
    if not await sanctuary_db.update_plan(int(user["id"]), plan_id, fields):
        raise HTTPException(status_code=404, detail="No such plan")
    return {"ok": True}


@router.delete("/api/sanctuary/plans/{plan_id}")
async def plans_delete(plan_id: int, user: dict = Depends(_unlocked_user)):
    if not await sanctuary_db.delete_plan(int(user["id"]), plan_id):
        raise HTTPException(status_code=404, detail="No such plan")
    return {"ok": True}


def _loan_account_lines(loans: list[dict]) -> list[dict]:
    """The lenders still owed, each with the number they answer to.

    Only the running ones are named. A settled loan is a closed door, and
    its number on this card would read as a debt that is still open.
    """
    lines = [
        {
            "id": f"loan-{loan.get('id')}",
            "lender": str(loan.get("lender") or loan.get("name") or "Loan"),
            "name": str(loan.get("name") or ""),
            "number": str(loan.get("account_no") or ""),
            "active": True,
        }
        for loan in loans
        if loan.get("active") and loan.get("account_no")
    ]
    return sorted(lines, key=lambda x: x["lender"].lower())


@router.get("/api/sanctuary/known")
async def known_get(user: dict = Depends(_unlocked_user)):
    """The accounts and lenders the sanctuary already knows about.

    Important info was a page he had to type himself, so a bank he had
    never written down looked missing even when its loan was on the shelf.
    This says what the statements and cards already prove — every account
    a statement was imported from, and every lender with a card — so the
    family can find them without him having written a word.
    """
    user_id = int(user["id"])
    accounts: dict[str, dict] = {}
    for row in await sanctuary_db.statement_account_summary(user_id):
        tail = row["tail"]
        if tail == "unknown":
            continue
        accounts[tail] = {
            "tail": tail,
            "entries": row["entries"],
            "first": row["first"],
            "last": row["last"],
            "kind": "bank",
            "bank": "",
            "label": "",
            "id": "",
        }
    # An account he has stated but never imported a statement from can still
    # be corroborated: his own RTGS and NEFT narrations name the other side
    # in full. The transfers only CONFIRM what he says — they never invent an
    # account, because most counterparties in a narration are employers and
    # payees, not his.
    stated_accounts = [_account_view(a) for a in await sanctuary_db.get_json_state(user_id, "accounts", [])]
    wanted = {a["number"] for a in stated_accounts if a["number"] and a["kind"] == "bank"}
    corroboration: dict[str, dict] = {}
    if wanted:
        for row in await sanctuary_db.transfer_narrations(user_id):
            for seen in sanctuary_statements.counterparty_accounts(row["note"]):
                match = next((w for w in wanted if w in seen["number"] or seen["number"] in w), None)
                if not match:
                    continue
                entry = corroboration.setdefault(match, {"transfers": 0, "last": "", "bank": seen["bank"]})
                entry["transfers"] += 1
                entry["last"] = max(entry["last"], row["entry_date"])

    # What he has told us marries into what the statements prove: an account
    # he has named gains its bank, and one he holds but has never imported
    # appears anyway. A card is his word alone — it leaves no statement here.
    cards = []
    identifiers = []
    for stated in stated_accounts:
        if stated["kind"] == "card":
            cards.append(stated)
            continue
        # A registration is nobody's account: it has no tail to match against
        # a statement and no bank behind it, only the name he filed it under.
        if stated["kind"] == "id":
            identifiers.append(stated)
            continue
        proof = corroboration.get(stated["number"]) or {}
        extra = {
            "bank": stated["bank"] or proof.get("bank", ""),
            "label": stated["label"],
            "id": stated["id"],
            "transfers": proof.get("transfers", 0),
            "transfer_last": proof.get("last", ""),
        }
        seen = accounts.get(stated["tail"])
        if seen:
            seen.update(extra)
        else:
            accounts[stated["tail"] or stated["id"]] = {**stated, **extra, "entries": 0, "first": "", "last": ""}
    # The loans travel with their account numbers now. The count alone was
    # a pointer to another panel, which is no use to someone holding a
    # death certificate and a list of lenders to telephone — the number the
    # bank answers to is the whole point of this card. Only the ones still
    # running are named; a settled loan is a closed door.
    loans = await sanctuary_db.list_loans(user_id)
    return {
        "accounts": sorted(accounts.values(), key=lambda a: -a["entries"]),
        "cards": cards,
        "identifiers": identifiers,
        "loan_accounts": _loan_account_lines(loans),
        "loans_open": sum(1 for loan in loans if loan.get("active")),
        "loans_settled": sum(1 for loan in loans if not loan.get("active")),
    }


@router.put("/api/sanctuary/notes")
async def notes_put(request: Request, user: dict = Depends(_unlocked_user)):
    payload = await request.json()
    notes = payload.get("notes")
    if not isinstance(notes, list) or len(notes) > 200:
        raise HTTPException(status_code=400, detail="Bad notes list")
    cleaned = [
        {
            "id": str((note or {}).get("id") or secrets.token_hex(4)),
            "title": str((note or {}).get("title") or "")[:200],
            "body": str((note or {}).get("body") or "")[:10000],
            "updated_at": _today_ist().isoformat(),
        }
        for note in notes
    ]
    await sanctuary_db.set_json_state(int(user["id"]), "notes", cleaned)
    return {"ok": True}


# ── The month view (one call feeds the whole finance tab) ────────


@router.get("/api/sanctuary/finance")
async def finance_month(month: str | None = None, user: dict = Depends(_unlocked_user)):
    user_id = int(user["id"])
    today = _today_ist()
    month = month if month and _MONTH_RE.match(month) else _month_key(today)
    await _materialize_recurring(user_id)

    categories = await _get_categories(user_id)
    saving_names = {c["name"] for c in categories if c["kind"] == "saving"}
    ledger = await sanctuary_db.list_ledger(user_id, month)
    start, end = _month_bounds(month)
    emis = await sanctuary_db.list_emis(user_id, start, end)
    # For the overdraft card: the balance it stands at is held on a loan
    # card, because only he can know it and it has to count as debt. Every
    # sweep since the day he stated it carries that figure forward.
    all_loans = await sanctuary_db.list_loans(user_id)
    od_anchor = str((_od_loan(all_loans, "") or {}).get("stated_on") or "")
    od_since = (
        await sanctuary_db.ledger_rows_in_categories(user_id, [OD_CATEGORY], since=od_anchor) if od_anchor else []
    )

    bank = await _bank_balance(user_id)
    months_state = await sanctuary_db.get_json_state(user_id, "months", {})
    default_salary = float(await sanctuary_db.get_state(user_id, "salary_default", "0") or 0)
    entry = months_state.get(month) or {}
    stated, said = _stated_salary(entry)
    salary = default_salary if stated is None else stated
    salary_source = said
    # The bank saw the pay arrive: the employer's credit is the salary. His
    # own figure and a payslip outrank it; nothing else does. It is never
    # written down — the statement stays the source, so a corrected import
    # corrects the tile.
    if not _salary_outranks_the_bank(said):
        from_bank = _salary_from_ledger(ledger)
        if from_bank:
            salary, salary_source = from_bank, "statement"

    # Neither half of an overdraft sweep is spending. Paying the OD down is
    # moving a debt, not consuming anything, and what the OD funded is
    # already in the ledger line by line — counting the sweeps on top would
    # add a hundred thousand rupees of bank housekeeping to a month's
    # groceries. The debt itself is reported separately, below.
    excluded = saving_names | {"Self transfer", OD_CATEGORY}
    outgo = [r for r in ledger if r["source"] != "statement-in"]
    spent = sum(r["amount"] for r in outgo if r["category"] not in excluded)
    saved = sum(r["amount"] for r in outgo if r["category"] in saving_names)
    inflow = sum(r["amount"] for r in ledger if r["source"] == "statement-in")
    # The pay that arrives on the 30th is not spent that day — it is what
    # the NEXT month lives on. So each month opens with whatever the last
    # one still held, carried in as funds rather than as a second salary.
    carried_in = await _pay_that_arrived(user_id, _previous_month(month), months_state, default_salary)

    # An EMI the bank statement already told is not spent AGAIN by the
    # schedule that planned it. A schedule row whose amount appears as a
    # statement debit within five days of its due date is marked in_ledger:
    # it stays visible on its loan, but the money counts once — in the
    # ledger row, where it actually moved.
    stmt_days = {}
    for row in outgo:
        if str(row.get("ref_id") or "").startswith("stmt:"):
            stmt_days.setdefault(round(row["amount"], 2), []).append(row["entry_date"])
    # An instalment charged to a credit card never leaves the bank on its
    # own, so the test above can never find it: it arrives inside the card's
    # bill, and the bill is already a row here. Two Instaloans on card ··4838
    # were being spent twice over that way — once by CRED settling the card,
    # once by the schedule that had already been settled with it.
    on_a_card = {int(loan["id"]) for loan in all_loans if loan.get("on_a_card")}
    for emi in emis:
        due = date.fromisoformat(emi["due_date"])
        emi["in_ledger"] = int(emi.get("loan_id") or 0) in on_a_card or any(
            abs((date.fromisoformat(d) - due).days) <= 5 for d in stmt_days.get(round(emi["amount"], 2), [])
        )
        # An instalment that has not come due is not money spent. A future
        # month must read empty, not pre-charged with what it will owe.
        emi["due_yet"] = bool(emi["paid_on"]) or due <= today
    emi_total = sum(e["amount"] for e in emis if not e["in_ledger"] and e["due_yet"])

    by_category: dict[str, float] = {}
    # Neither his own money moving nor a debt's balance moving is a spending
    # bar. The overdraft would otherwise stand at the top of "where it went"
    # as the largest thing he never spent. But leaving it out entirely made
    # it look lost, so it is counted apart and shown apart.
    moved_apart: dict[str, float] = {}
    for row in outgo:
        if row["category"] in ("Self transfer", OD_CATEGORY):
            moved_apart[row["category"]] = moved_apart.get(row["category"], 0) + row["amount"]
            continue
        by_category[row["category"]] = by_category.get(row["category"], 0) + row["amount"]
    for emi in emis:
        if emi["in_ledger"] or not emi["due_yet"]:
            continue
        label = f"EMI · {emi['loan_name']}"
        by_category[label] = by_category.get(label, 0) + emi["amount"]

    # Which months hold anything at all — the jump picker rings them, so
    # eight years of history is reachable without a hundred taps.
    known = await sanctuary_db.months_with_anything(user_id)

    alert_through = (today + timedelta(days=ALERT_WINDOW_DAYS)).isoformat()
    alerts = [
        {
            **emi,
            "overdue": emi["due_date"] < today.isoformat(),
        }
        for emi in await sanctuary_db.unpaid_emis_through(user_id, alert_through)
    ]

    trend_months = []
    cursor = date.fromisoformat(f"{month}-01")
    for _ in range(6):
        trend_months.append(_month_key(cursor))
        cursor = sanctuary_emi._add_months(cursor, -1)
    trend_months.reverse()
    ledger_trend = await sanctuary_db.ledger_month_trend(user_id, trend_months)
    emi_rows = await sanctuary_db.list_emis(user_id, f"{trend_months[0]}-01", _month_bounds(trend_months[-1])[1])
    trend = []
    for key in trend_months:
        emi_sum = sum(e["amount"] for e in emi_rows if e["due_date"].startswith(key))
        trend.append({"month": key, "total": round(ledger_trend.get(key, 0) + emi_sum, 2)})

    return {
        "month": month,
        "today": today.isoformat(),
        "salary": salary,
        # What is actually in the account: the statement's own running
        # balance, which is right even where the ledger has gaps, and the
        # count of rows posted after it so a stale figure says so itself.
        "bank_balance": bank["balance"],
        "bank_current": bank.get("current"),
        "bank_in_od": bank.get("in_od"),
        "bank_od_on": bank.get("od_on", ""),
        "bank_od_limit": bank.get("od_limit"),
        "bank_as_of": bank["as_of"],
        "bank_after": bank["after"],
        # salary_source is what the page says out loud now — "the bank saw it
        # arrive", "from your payslip", "your own figure" — because a figure
        # that could not name its origin was impossible to tell from a stale
        # one, and the only way to find out was to delete it and look.
        "salary_source": salary_source,
        "note": (months_state.get(month) or {}).get("note", ""),
        "totals": {
            "inflow": round(inflow, 2),
            "spent": round(spent + emi_total, 2),
            "expenses": round(spent, 2),
            "emis": round(emi_total, 2),
            "saved": round(saved, 2),
            "carried_in": carried_in,
            # What is actually in the current account, which is the only
            # figure that answers "how much can I spend today".
            #
            # It used to be the month's envelope less what had gone out of
            # it, and that was a fiction here: his bank sweeps the leftover
            # into the overdraft account the same night the salary lands, so
            # August's pay was out of the current account within hours and
            # the envelope was describing money that had already moved. It
            # also docked him for what he put ASIDE — sixty-four thousand to
            # a broker came off the month as though it had been spent.
            #
            # The bank's own figure, less what is parked in the sweep
            # account. Where no statement has printed a balance yet the old
            # reckoning still answers, so the tile is never blank.
            "left": (
                round(bank["current"], 2)
                if bank.get("current") is not None
                else round(carried_in - spent - emi_total - saved, 2)
            ),
        },
        # The overdraft, read as a debt: what he drew on it this month and
        # what went back. Kept out of every total above on purpose — this is
        # the size of a borrowing, not a month's spending. `owed` is HIS
        # figure off the bank, because a ledger that holds a few months
        # cannot know what the overdraft stood at before them.
        "od": _od_view(ledger, all_loans, od_since),
        "carried_from": _previous_month(month),
        "months_known": known,
        "salary_months": {m: 1 for m, v in months_state.items() if (v or {}).get("salary")},
        "by_category": {k: round(v, 2) for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])},
        # Money that moved without being spent — shown under the bars, never
        # inside them, so the month's spending stays the month's spending.
        "moved_apart": {k: round(v, 2) for k, v in sorted(moved_apart.items(), key=lambda kv: -kv[1])},
        "ledger": ledger,
        "emis": emis,
        "alerts": alerts,
        "categories": categories,
        "recurring": await sanctuary_db.get_json_state(user_id, "recurring", []),
        "trend": trend,
    }
