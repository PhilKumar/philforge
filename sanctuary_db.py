"""Data access for the Sanctuary page — the owner's private journal and ledger.

Every row is keyed to the owning user id; the tables are created by db.py's
schema list and cleared by account deletion alongside every other user-owned
table.  Amount arithmetic happens in SQL where possible so month summaries
stay consistent with the rows they summarise.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite

import config


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── State (settings + small JSON collections) ────────────────────


async def get_state(user_id: int, key: str, default: str = "") -> str:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT value FROM sanctuary_state WHERE user_id = ? AND key = ?",
            (int(user_id), key),
        )
        row = await cursor.fetchone()
        return row[0] if row else default


async def set_state(user_id: int, key: str, value: str) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """INSERT INTO sanctuary_state (user_id, key, value, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, key)
               DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (int(user_id), key, value, _now_iso()),
        )
        await db.commit()


async def get_json_state(user_id: int, key: str, default):
    raw = await get_state(user_id, key, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


async def set_json_state(user_id: int, key: str, value) -> None:
    await set_state(user_id, key, json.dumps(value, ensure_ascii=False))


# ── Journal entries ──────────────────────────────────────────────


def _entry_from_row(row: aiosqlite.Row) -> dict:
    entry = dict(row)
    try:
        entry["photos"] = json.loads(entry.get("photos") or "[]")
    except (ValueError, TypeError):
        entry["photos"] = []
    return entry


async def list_entries(
    user_id: int,
    month: str | None = None,
    kind: str | None = None,
    query: str | None = None,
    limit: int = 200,
) -> list[dict]:
    sql = "SELECT * FROM sanctuary_entries WHERE user_id = ?"
    params: list = [int(user_id)]
    if month:
        sql += " AND entry_date LIKE ?"
        params.append(f"{month}%")
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if query:
        sql += " AND (title LIKE ? OR body LIKE ? OR music LIKE ?)"
        needle = f"%{query}%"
        params.extend([needle, needle, needle])
    sql += " ORDER BY entry_date DESC, id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, params)
        return [_entry_from_row(row) for row in await cursor.fetchall()]


async def entry_months(user_id: int, kind: str | None = None, query: str | None = None) -> list[str]:
    """Every month that has something written in it, oldest first.

    The book turns a day at a time, and a day at the edge of a month has a
    neighbour in the month next door. Without knowing which months hold
    writing the book could only stop at the month's edge and wait to be
    told where to go next — and the months between two entries can be
    empty, so stepping one along blindly would land on a blank spread.
    """
    sql = "SELECT DISTINCT substr(entry_date, 1, 7) AS month FROM sanctuary_entries WHERE user_id = ?"
    params: list = [int(user_id)]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if query:
        sql += " AND (title LIKE ? OR body LIKE ? OR music LIKE ?)"
        needle = f"%{query}%"
        params.extend([needle, needle, needle])
    sql += " ORDER BY month"
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(sql, params)
        return [row[0] for row in await cursor.fetchall() if row[0]]


async def get_entry(user_id: int, entry_id: int) -> dict | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sanctuary_entries WHERE user_id = ? AND id = ?",
            (int(user_id), int(entry_id)),
        )
        row = await cursor.fetchone()
        return _entry_from_row(row) if row else None


async def create_entry(user_id: int, fields: dict) -> int:
    now = _now_iso()
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO sanctuary_entries
               (user_id, entry_date, kind, title, body, mood, music, photos, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(user_id),
                fields["entry_date"],
                fields.get("kind", "note"),
                fields.get("title", ""),
                fields.get("body", ""),
                fields.get("mood"),
                fields.get("music", ""),
                json.dumps(fields.get("photos", []), ensure_ascii=False),
                now,
                now,
            ),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)


async def update_entry(user_id: int, entry_id: int, fields: dict) -> bool:
    allowed = {"entry_date", "kind", "title", "body", "mood", "music", "photos"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        params.append(json.dumps(value, ensure_ascii=False) if key == "photos" else value)
    if not sets:
        return False
    sets.append("updated_at = ?")
    params.extend([_now_iso(), int(user_id), int(entry_id)])
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            f"UPDATE sanctuary_entries SET {', '.join(sets)} WHERE user_id = ? AND id = ?",  # nosec B608
            params,
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_entry(user_id: int, entry_id: int) -> dict | None:
    """Delete and return the entry so the caller can remove its photo files."""
    entry = await get_entry(user_id, entry_id)
    if entry is None:
        return None
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "DELETE FROM sanctuary_entries WHERE user_id = ? AND id = ?",
            (int(user_id), int(entry_id)),
        )
        await db.commit()
    return entry


# ── Moods ────────────────────────────────────────────────────────


async def upsert_mood(user_id: int, mood_date: str, mood: int, note: str = "") -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """INSERT INTO sanctuary_moods (user_id, mood_date, mood, note)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, mood_date)
               DO UPDATE SET mood = excluded.mood, note = excluded.note""",
            (int(user_id), mood_date, int(mood), note),
        )
        await db.commit()


async def moods_for_range(user_id: int, start: str, end: str) -> dict[str, dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT mood_date, mood, note FROM sanctuary_moods
               WHERE user_id = ? AND mood_date >= ? AND mood_date <= ?""",
            (int(user_id), start, end),
        )
        return {row["mood_date"]: dict(row) for row in await cursor.fetchall()}


# ── Ledger ───────────────────────────────────────────────────────


async def add_ledger(user_id: int, fields: dict) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO sanctuary_ledger
               (user_id, entry_date, category, amount, note, source, ref_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(user_id),
                fields["entry_date"],
                fields.get("category", "Other"),
                float(fields.get("amount", 0)),
                fields.get("note", ""),
                fields.get("source", "manual"),
                fields.get("ref_id", ""),
                _now_iso(),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)


async def existing_ledger_refs(user_id: int, ref_ids: list[str]) -> set[str]:
    """Which of these ref_ids are already in the ledger. Chunked: a year's
    statement is ~800 refs and SQLite's default parameter cap is 999."""
    found: set[str] = set()
    if not ref_ids:
        return found
    async with aiosqlite.connect(config.DB_PATH) as db:
        for start in range(0, len(ref_ids), 500):
            chunk = ref_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            cursor = await db.execute(
                f"SELECT ref_id FROM sanctuary_ledger WHERE user_id = ? AND ref_id IN ({placeholders})",  # nosec B608 - placeholders only
                (int(user_id), *chunk),
            )
            found.update(row[0] for row in await cursor.fetchall())
    return found


async def add_ledger_many(user_id: int, rows: list[dict]) -> int:
    """Insert statement rows in one transaction, skipping refs already posted."""
    if not rows:
        return 0
    existing = await existing_ledger_refs(user_id, [r["ref_id"] for r in rows if r.get("ref_id")])
    fresh = [r for r in rows if r.get("ref_id") and r["ref_id"] not in existing]
    if not fresh:
        return 0
    now = _now_iso()
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executemany(
            """INSERT INTO sanctuary_ledger
               (user_id, entry_date, category, amount, note, source, ref_id, created_at, balance)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    int(user_id),
                    r["entry_date"],
                    r.get("category", "Uncategorised"),
                    float(r.get("amount", 0)),
                    r.get("note", ""),
                    r.get("source", "statement"),
                    r["ref_id"],
                    now,
                    None if r.get("balance") is None else float(r["balance"]),
                )
                for r in fresh
            ],
        )
        await db.commit()
    return len(fresh)


async def preview_matching(user_id: int, match: str, category: str, limit: int = 4) -> tuple[int, list[str]]:
    """What a rule WOULD take, before it takes it: how many rows in the
    category it draws from, and a few of their narrations.

    A rule is a substring, so a short word is a net. "bank" sits inside
    every UPI narration that names one, and teaching it once files a year
    of newspapers and juice under a school. Counting first is the only
    moment he can see that coming.
    """
    if not (match or "").strip():
        return 0, []
    word = match.strip()
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """SELECT COUNT(*) FROM sanctuary_ledger
               WHERE user_id = ? AND category = ? AND instr(lower(note), lower(?)) > 0""",
            (int(user_id), category, word),
        )
        row = await cursor.fetchone()
        total = int(row[0]) if row else 0
        cursor = await db.execute(
            """SELECT note FROM sanctuary_ledger
               WHERE user_id = ? AND category = ? AND instr(lower(note), lower(?)) > 0
               ORDER BY entry_date DESC LIMIT ?""",
            (int(user_id), category, word, int(limit)),
        )
        samples = [str(r[0] or "") for r in await cursor.fetchall()]
    return total, samples


async def count_rows_matching(user_id: int, match: str) -> int:
    """How many ledger rows a taught rule's match currently catches."""
    if not (match or "").strip():
        return 0
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """SELECT COUNT(*) FROM sanctuary_ledger
               WHERE user_id = ? AND instr(lower(note), lower(?)) > 0""",
            (int(user_id), match.strip()),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0


async def recategorise_matching(user_id: int, match: str, category: str, from_category: str = "Uncategorised") -> int:
    """Move every row in from_category whose note carries the match — the
    default splits the unsorted pile, a named source splits an existing
    category (two loans from one lender, told apart by mandate number)."""
    if not match.strip():
        return 0
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """UPDATE sanctuary_ledger SET category = ?
               WHERE user_id = ? AND category = ?
                 AND instr(lower(note), lower(?)) > 0""",
            (category, int(user_id), from_category, match.strip()),
        )
        await db.commit()
        return cursor.rowcount


async def months_with_anything(user_id: int) -> dict[str, int]:
    """Every month that holds a ledger row or a salary — for the jump picker."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """SELECT substr(entry_date, 1, 7) AS month, COUNT(*)
               FROM sanctuary_ledger WHERE user_id = ? GROUP BY month""",
            (int(user_id),),
        )
        return {row[0]: row[1] for row in await cursor.fetchall()}


async def monthly_flows(user_id: int, months: int = 12) -> list[dict]:
    """Per-month money in and money out, Self transfers excluded on both
    sides — moved money is neither income nor spending."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT substr(entry_date, 1, 7) AS month,
                      SUM(CASE WHEN source = 'statement-in' THEN amount ELSE 0 END) AS inflow,
                      SUM(CASE WHEN source != 'statement-in' THEN amount ELSE 0 END) AS outflow
               FROM sanctuary_ledger
               WHERE user_id = ? AND category != 'Self transfer'
               GROUP BY month ORDER BY month DESC LIMIT ?""",
            (int(user_id), max(1, min(int(months), 60))),
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        rows.reverse()
        return rows


async def statement_outflow_rows(user_id: int) -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT note, entry_date, amount FROM sanctuary_ledger
               WHERE user_id = ? AND source = 'statement' ORDER BY entry_date""",
            (int(user_id),),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def statement_account_summary(user_id: int) -> list[dict]:
    """Every account a statement was imported from, by its last six digits.

    The ref_id an imported row carries is 'stmt:<tail>:<digest>', so the
    accounts he has actually banked through can be read back without
    keeping a separate list that could fall out of step.
    """
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT substr(ref_id, 6, instr(substr(ref_id, 6), ':') - 1) AS tail,
                      COUNT(*) AS entries, MIN(entry_date) AS first, MAX(entry_date) AS last
               FROM sanctuary_ledger
               WHERE user_id = ? AND ref_id LIKE 'stmt:%'
               GROUP BY tail""",
            (int(user_id),),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def transfer_narrations(user_id: int) -> list[dict]:
    """Statement rows whose narration carries an IFSC — the transfers that
    name the account on the other side."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT note, entry_date, amount, source FROM sanctuary_ledger
               WHERE user_id = ? AND ref_id LIKE 'stmt:%'
                 AND (note LIKE '%RTGS%' OR note LIKE '%NEFT%')
               ORDER BY entry_date""",
            (int(user_id),),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def ledger_rows_by_sources(user_id: int, sources: tuple) -> list[dict]:
    placeholders = ",".join("?" for _ in sources)
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""SELECT id, entry_date, category, amount, note, source FROM sanctuary_ledger
               WHERE user_id = ? AND source IN ({placeholders}) ORDER BY entry_date""",  # nosec B608 - placeholders only
            (int(user_id), *sources),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def merge_duplicate_pair(user_id: int, manual_id: int, bank_id: int) -> bool:
    """The bank row adopts the hand-entered row's words; the duplicate goes."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sanctuary_ledger WHERE user_id = ? AND id = ?",
            (int(user_id), int(manual_id)),
        )
        manual = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT * FROM sanctuary_ledger WHERE user_id = ? AND id = ? AND source LIKE 'statement%'",
            (int(user_id), int(bank_id)),
        )
        bank = await cursor.fetchone()
        if not manual or not bank:
            return False
        note = bank["note"]
        if manual["note"]:
            note = f"{manual['note']} — {bank['note']}"[:500]
        await db.execute(
            "UPDATE sanctuary_ledger SET category = ?, note = ? WHERE user_id = ? AND id = ?",
            (manual["category"], note, int(user_id), int(bank_id)),
        )
        await db.execute(
            "DELETE FROM sanctuary_ledger WHERE user_id = ? AND id = ?",
            (int(user_id), int(manual_id)),
        )
        await db.commit()
        return True


async def search_ledger(user_id: int, query: str, limit: int = 400) -> list[dict]:
    """Every year at once: rows whose note or category carries the query."""
    needle = f"%{query.strip()}%"
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, entry_date, category, amount, note, source, ref_id
               FROM sanctuary_ledger
               WHERE user_id = ? AND (note LIKE ? OR category LIKE ?)
               ORDER BY entry_date DESC, id DESC LIMIT ?""",
            (int(user_id), needle, needle, max(1, min(int(limit), 1000))),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def search_ledger_tally(user_id: int, query: str) -> dict:
    """What the search comes to, over EVERY matching row.

    The rows themselves stop at four hundred so the table stays openable,
    and a total taken from those would be a total of the newest four
    hundred — which is not what "how much have I spent on Zepto" asks. The
    arithmetic is done in the database, across the lot.
    """
    needle = f"%{query.strip()}%"
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        where = "WHERE user_id = ? AND (note LIKE ? OR category LIKE ?)"
        args = (int(user_id), needle, needle)
        cursor = await db.execute(
            f"""SELECT COUNT(*) AS n, MIN(entry_date) AS first, MAX(entry_date) AS last,
                       SUM(CASE WHEN source = 'statement-in' THEN 0 ELSE amount END) AS out,
                       SUM(CASE WHEN source = 'statement-in' THEN amount ELSE 0 END) AS inn
                FROM sanctuary_ledger {where}""",  # nosec B608
            args,
        )
        totals = dict(await cursor.fetchone())
        cursor = await db.execute(
            f"""SELECT substr(entry_date, 1, 4) AS year, COUNT(*) AS n,
                       SUM(CASE WHEN source = 'statement-in' THEN 0 ELSE amount END) AS out
                FROM sanctuary_ledger {where} GROUP BY year ORDER BY year DESC""",  # nosec B608
            args,
        )
        years = [dict(row) for row in await cursor.fetchall()]
        cursor = await db.execute(
            f"""SELECT category, COUNT(*) AS n,
                       SUM(CASE WHEN source = 'statement-in' THEN 0 ELSE amount END) AS out
                FROM sanctuary_ledger {where} GROUP BY category ORDER BY out DESC LIMIT 8""",  # nosec B608
            args,
        )
        cats = [dict(row) for row in await cursor.fetchall()]
    return {
        "count": int(totals["n"] or 0),
        "spent": round(float(totals["out"] or 0), 2),
        "received": round(float(totals["inn"] or 0), 2),
        "first": str(totals["first"] or ""),
        "last": str(totals["last"] or ""),
        "by_year": [{"year": y["year"], "count": y["n"], "spent": round(float(y["out"] or 0), 2)} for y in years],
        "by_category": [
            {"name": c["category"], "count": c["n"], "spent": round(float(c["out"] or 0), 2)}
            for c in cats
            if float(c["out"] or 0) > 0
        ],
    }


async def set_ledger_category(user_id: int, row_id: int, category: str) -> bool:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE sanctuary_ledger SET category = ? WHERE user_id = ? AND id = ?",
            (category, int(user_id), int(row_id)),
        )
        await db.commit()
        return cursor.rowcount > 0


async def uncategorised_ledger(user_id: int) -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, entry_date, amount, note, source FROM sanctuary_ledger
               WHERE user_id = ? AND category = 'Uncategorised'
               ORDER BY entry_date DESC""",
            (int(user_id),),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def balance_last_known(user_id: int) -> dict:
    """The newest running balance a statement has printed, and its day.

    The one figure that says how much money is actually in the account. It
    is the statement's own arithmetic, not a total this page assembles, so
    it is right even where the ledger has gaps.
    """
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT entry_date, balance FROM sanctuary_ledger
               WHERE user_id = ? AND balance IS NOT NULL
               ORDER BY entry_date DESC, id DESC LIMIT 1""",
            (int(user_id),),
        )
        row = await cursor.fetchone()
    if not row:
        return {"balance": None, "as_of": ""}
    return {"balance": round(float(row["balance"]), 2), "as_of": str(row["entry_date"])}


async def refs_missing_balance(user_id: int, ref_ids: list[str]) -> set[str]:
    """Which of these rows are posted but carry no running balance yet."""
    found: set[str] = set()
    if not ref_ids:
        return found
    async with aiosqlite.connect(config.DB_PATH) as db:
        for start in range(0, len(ref_ids), 500):
            chunk = ref_ids[start : start + 500]
            holes = ",".join("?" for _ in chunk)
            cursor = await db.execute(
                f"""SELECT ref_id FROM sanctuary_ledger
                    WHERE user_id = ? AND balance IS NULL AND ref_id IN ({holes})""",  # nosec B608 - placeholders only
                (int(user_id), *chunk),
            )
            found.update(row[0] for row in await cursor.fetchall())
    return found


async def backfill_balances(user_id: int, rows: list[dict]) -> int:
    """Fill in the running balance on rows posted before it was being kept."""
    carrying = [r for r in rows if r.get("balance") is not None and r.get("ref_id")]
    if not carrying:
        return 0
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.executemany(
            """UPDATE sanctuary_ledger SET balance = ?
               WHERE user_id = ? AND ref_id = ? AND balance IS NULL""",
            [(float(r["balance"]), int(user_id), r["ref_id"]) for r in carrying],
        )
        await db.commit()
        return int(cursor.rowcount or 0)


async def rows_since(user_id: int, after: str) -> int:
    """How many statement rows were posted after this day."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """SELECT COUNT(*) FROM sanctuary_ledger
               WHERE user_id = ? AND entry_date > ? AND source LIKE 'statement%'""",
            (int(user_id), str(after or "")),
        )
        row = await cursor.fetchone()
        return int(row[0] if row else 0)


async def credits_since(user_id: int, since: str) -> list[dict]:
    """Money that came IN on or after this day, oldest first.

    For matching a thing he is owed against the day it actually landed.
    """
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, entry_date, amount, note, source FROM sanctuary_ledger
               WHERE user_id = ? AND source = 'statement-in' AND entry_date >= ?
               ORDER BY entry_date""",
            (int(user_id), str(since or "")),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def categories_in_use(user_id: int) -> set[str]:
    """Every category name that actually holds a row."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT DISTINCT category FROM sanctuary_ledger WHERE user_id = ?",
            (int(user_id),),
        )
        return {row[0] for row in await cursor.fetchall() if row[0]}


async def every_filed_row(user_id: int) -> list[dict]:
    """Every row that already carries a category.

    For the one pass that has to look at all of them: when the rulebook
    itself was misreading, the rows that need moving are the ones already
    sitting under its wrong answers, and nothing narrower can find them.
    """
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT id, entry_date, amount, note, source, category FROM sanctuary_ledger
               WHERE user_id = ? AND category != 'Uncategorised'
                 AND note IS NOT NULL AND note != ''
               ORDER BY entry_date DESC""",
            (int(user_id),),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def ledger_rows_in_categories(user_id: int, categories: list[str], since: str = "") -> list[dict]:
    """Every row currently filed under one of these categories.

    The resort pass reads only the unsorted pile, on purpose. This is for
    the rarer case where a row IS sorted but the rulebook's answer for it
    has since changed.
    """
    if not categories:
        return []
    holes = ", ".join("?" for _ in categories)
    where = f"user_id = ? AND category IN ({holes})"
    params: list = [int(user_id), *categories]
    if since:
        # Strictly after: the day he read the balance off the bank already
        # has that day's sweeps inside the figure he wrote down.
        where += " AND entry_date > ?"
        params.append(since)
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""SELECT id, entry_date, amount, note, source, category FROM sanctuary_ledger
                WHERE {where} ORDER BY entry_date DESC""",  # nosec B608 - every hole is a placeholder
            params,
        )
        return [dict(row) for row in await cursor.fetchall()]


async def delete_ledger_row(user_id: int, row_id: int) -> bool:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM sanctuary_ledger WHERE user_id = ? AND id = ?",
            (int(user_id), int(row_id)),
        )
        await db.commit()
        return cursor.rowcount > 0


async def list_ledger(user_id: int, month: str) -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM sanctuary_ledger
               WHERE user_id = ? AND entry_date LIKE ?
               ORDER BY entry_date DESC, id DESC""",
            (int(user_id), f"{month}%"),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def ledger_ref_exists(user_id: int, ref_id: str) -> bool:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM sanctuary_ledger WHERE user_id = ? AND ref_id = ? LIMIT 1",
            (int(user_id), ref_id),
        )
        return await cursor.fetchone() is not None


async def ledger_month_trend(user_id: int, months: list[str]) -> dict[str, float]:
    """Total OUTFLOW per requested YYYY-MM month — inflows are not spending."""
    if not months:
        return {}
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ",".join("?" for _ in months)
        cursor = await db.execute(
            f"""SELECT substr(entry_date, 1, 7) AS month, SUM(amount) AS total
                FROM sanctuary_ledger
                WHERE user_id = ? AND source != 'statement-in'
                  AND substr(entry_date, 1, 7) IN ({placeholders})
                GROUP BY month""",  # nosec B608
            [int(user_id), *months],
        )
        return {row["month"]: float(row["total"] or 0) for row in await cursor.fetchall()}


# ── Loans and EMI schedules ──────────────────────────────────────


async def list_loans(user_id: int, include_inactive: bool = True) -> list[dict]:
    sql = "SELECT * FROM sanctuary_loans WHERE user_id = ?"
    if not include_inactive:
        sql += " AND active = 1"
    sql += " ORDER BY active DESC, id"
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, (int(user_id),))
        return [dict(row) for row in await cursor.fetchall()]


async def get_loan(user_id: int, loan_id: int) -> dict | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sanctuary_loans WHERE user_id = ? AND id = ?",
            (int(user_id), int(loan_id)),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def create_loan(user_id: int, fields: dict) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO sanctuary_loans
               (user_id, name, lender, emi_amount, due_day, start_date, note, account_no, details, drawn_amount, stated_on, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(user_id),
                fields["name"],
                fields.get("lender", ""),
                float(fields.get("emi_amount", 0)),
                int(fields.get("due_day", 5)),
                fields.get("start_date", ""),
                fields.get("note", ""),
                fields.get("account_no", ""),
                fields.get("details", ""),
                float(fields.get("drawn_amount", 0)),
                fields.get("stated_on", ""),
                1 if fields.get("active", True) else 0,
                _now_iso(),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)


async def update_loan(user_id: int, loan_id: int, fields: dict) -> bool:
    allowed = {
        "name",
        "lender",
        "emi_amount",
        "due_day",
        "start_date",
        "note",
        "account_no",
        "details",
        "drawn_amount",
        "stated_on",
        "on_a_card",
        "active",
    }
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return False
    params.extend([int(user_id), int(loan_id)])
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            f"UPDATE sanctuary_loans SET {', '.join(sets)} WHERE user_id = ? AND id = ?",  # nosec B608
            params,
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_loan(user_id: int, loan_id: int) -> bool:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "DELETE FROM sanctuary_emis WHERE user_id = ? AND loan_id = ?",
            (int(user_id), int(loan_id)),
        )
        cursor = await db.execute(
            "DELETE FROM sanctuary_loans WHERE user_id = ? AND id = ?",
            (int(user_id), int(loan_id)),
        )
        await db.commit()
        return cursor.rowcount > 0


async def replace_schedule(user_id: int, loan_id: int, rows: list[dict]) -> int:
    """Swap in a freshly parsed schedule, preserving paid marks by due date."""
    now = _now_iso()
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT due_date, paid_on FROM sanctuary_emis WHERE user_id = ? AND loan_id = ? AND paid_on != ''",
            (int(user_id), int(loan_id)),
        )
        paid_marks = {row["due_date"]: row["paid_on"] for row in await cursor.fetchall()}
        await db.execute(
            "DELETE FROM sanctuary_emis WHERE user_id = ? AND loan_id = ?",
            (int(user_id), int(loan_id)),
        )
        for row in rows:
            await db.execute(
                """INSERT INTO sanctuary_emis
                   (user_id, loan_id, due_date, amount, principal_part, interest_part,
                    outstanding, paid_on, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(user_id),
                    int(loan_id),
                    row["due_date"],
                    float(row.get("amount", 0)),
                    row.get("principal_part"),
                    row.get("interest_part"),
                    row.get("outstanding"),
                    paid_marks.get(row["due_date"], ""),
                    now,
                ),
            )
        await db.commit()
        return len(rows)


async def list_emis(user_id: int, start: str, end: str) -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT e.*, l.name AS loan_name, l.lender AS loan_lender
               FROM sanctuary_emis e JOIN sanctuary_loans l ON l.id = e.loan_id
               WHERE e.user_id = ? AND e.due_date >= ? AND e.due_date <= ?
               ORDER BY e.due_date, e.id""",
            (int(user_id), start, end),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def emis_for_loan(user_id: int, loan_id: int) -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sanctuary_emis WHERE user_id = ? AND loan_id = ? ORDER BY due_date",
            (int(user_id), int(loan_id)),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def unpaid_emis_through(user_id: int, through: str) -> list[dict]:
    """Unpaid EMIs due on or before `through` — the alert feed."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT e.*, l.name AS loan_name, l.lender AS loan_lender
               FROM sanctuary_emis e JOIN sanctuary_loans l ON l.id = e.loan_id
               WHERE e.user_id = ? AND e.paid_on = '' AND e.due_date <= ? AND l.active = 1
               ORDER BY e.due_date""",
            (int(user_id), through),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def settle_past_emis(user_id: int, loan_id: int, through: str) -> int:
    """Mark every unpaid EMI due on or before `through` as paid on its due date."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """UPDATE sanctuary_emis SET paid_on = due_date
               WHERE user_id = ? AND loan_id = ? AND paid_on = '' AND due_date <= ?""",
            (int(user_id), int(loan_id), through),
        )
        await db.commit()
        return cursor.rowcount


async def set_emi_paid(user_id: int, emi_id: int, paid_on: str) -> bool:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE sanctuary_emis SET paid_on = ? WHERE user_id = ? AND id = ?",
            (paid_on, int(user_id), int(emi_id)),
        )
        await db.commit()
        return cursor.rowcount > 0


# ── Vault documents ──────────────────────────────────────────────


async def create_document(user_id: int, fields: dict) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO sanctuary_documents
               (user_id, title, category, doc_number, note, series, doc_date,
                filename, content_type, size, file_token, content_sha, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(user_id),
                fields["title"],
                fields.get("category", "Other"),
                fields.get("doc_number", ""),
                fields.get("note", ""),
                fields.get("series", ""),
                fields.get("doc_date", ""),
                fields.get("filename", ""),
                fields.get("content_type", ""),
                int(fields.get("size", 0)),
                fields.get("file_token", ""),
                fields.get("content_sha", ""),
                _now_iso(),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)


async def find_document_by_sha(user_id: int, content_sha: str) -> dict | None:
    """The paper already in the vault with this exact content, if any."""
    if not content_sha:
        return None
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sanctuary_documents WHERE user_id = ? AND content_sha = ? LIMIT 1",
            (int(user_id), content_sha),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def documents_without_sha(user_id: int, filename: str = "", size: int | None = None) -> list[dict]:
    """Rows stored before fingerprints existed — narrowed when a name is known."""
    query = "SELECT * FROM sanctuary_documents WHERE user_id = ? AND content_sha = ''"
    params: list = [int(user_id)]
    if filename:
        query += " AND filename = ? AND size = ?"
        params.extend([filename, int(size or 0)])
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        return [dict(row) for row in await cursor.fetchall()]


async def set_document_sha(user_id: int, doc_id: int, content_sha: str) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "UPDATE sanctuary_documents SET content_sha = ? WHERE user_id = ? AND id = ?",
            (content_sha, int(user_id), int(doc_id)),
        )
        await db.commit()


async def list_documents(user_id: int) -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM sanctuary_documents WHERE user_id = ?
               ORDER BY category, series, doc_date DESC, id DESC""",
            (int(user_id),),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_document(user_id: int, doc_id: int) -> dict | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sanctuary_documents WHERE user_id = ? AND id = ?",
            (int(user_id), int(doc_id)),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def update_document(user_id: int, doc_id: int, fields: dict) -> bool:
    allowed = {"title", "category", "doc_number", "note", "series", "doc_date"}
    sets, params = [], []
    for key, value in fields.items():
        if key in allowed:
            sets.append(f"{key} = ?")
            params.append(value)
    if not sets:
        return False
    params.extend([int(user_id), int(doc_id)])
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            f"UPDATE sanctuary_documents SET {', '.join(sets)} WHERE user_id = ? AND id = ?",  # nosec B608
            params,
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_document(user_id: int, doc_id: int) -> dict | None:
    """Delete and return the row so the caller can remove the file blob."""
    doc = await get_document(user_id, doc_id)
    if doc is None:
        return None
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "DELETE FROM sanctuary_documents WHERE user_id = ? AND id = ?",
            (int(user_id), int(doc_id)),
        )
        await db.commit()
    return doc


# ── the planner ──────────────────────────────────────────────────────────
# Tasks he wrote in one line, with the date the reader pulled out of it and
# the words it read. A done task is kept, not deleted: a planner that
# forgets what was finished cannot show him a week's worth of work.
_PLAN_FIELDS = ("title", "due_date", "due_kind", "said", "note")


async def list_plans(user_id: int, include_done: bool = True) -> list[dict]:
    sql = "SELECT * FROM sanctuary_plans WHERE user_id = ?"
    if not include_done:
        sql += " AND done = 0"
    # Undated tasks sort last rather than first, which is where an empty
    # string would otherwise put them.
    sql += " ORDER BY done, CASE WHEN due_date = '' THEN 1 ELSE 0 END, due_date, id"
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, (int(user_id),))
        return [dict(row) for row in await cursor.fetchall()]


async def add_plan(user_id: int, fields: dict) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO sanctuary_plans
               (user_id, title, due_date, due_kind, said, note, done, done_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, '', ?)""",
            (
                int(user_id),
                str(fields.get("title") or "")[:300],
                str(fields.get("due_date") or "")[:10],
                str(fields.get("due_kind") or "on")[:10],
                str(fields.get("said") or "")[:80],
                str(fields.get("note") or "")[:500],
                _now_iso(),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def update_plan(user_id: int, plan_id: int, fields: dict) -> bool:
    sets, params = [], []
    for key in (*_PLAN_FIELDS, "done", "done_at"):
        if key in fields:
            sets.append(f"{key} = ?")
            params.append(fields[key])
    if not sets:
        return False
    params.extend([int(user_id), int(plan_id)])
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            f"UPDATE sanctuary_plans SET {', '.join(sets)} WHERE user_id = ? AND id = ?",  # nosec B608
            params,
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_plan(user_id: int, plan_id: int) -> bool:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM sanctuary_plans WHERE user_id = ? AND id = ?",
            (int(user_id), int(plan_id)),
        )
        await db.commit()
        return cursor.rowcount > 0
