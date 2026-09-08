"""
db.py — PhilForge SQLite Data Layer (Multi-Tenant)

Async SQLite via aiosqlite with WAL mode for concurrent reads.
All queries filter by user_id for data isolation.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone

import aiosqlite

import config

_logger = logging.getLogger(__name__)

# Module-level: whether schema has been initialized
_initialized = False


# ── Schema (individual statements) ───────────────────────────────
_SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS users (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        username        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
        email           TEXT    UNIQUE,
        password_hash   TEXT    NOT NULL,
        role            TEXT    NOT NULL DEFAULT 'user',
        is_active       INTEGER NOT NULL DEFAULT 1,
        dhan_client_id  TEXT    DEFAULT '',
        dhan_access_token TEXT  DEFAULT '',
        dhan_pin        TEXT    DEFAULT '',
        dhan_totp_secret TEXT   DEFAULT '',
        mfa_totp_secret TEXT    DEFAULT '',
        mfa_pending_secret TEXT DEFAULT '',
        mfa_enabled     INTEGER NOT NULL DEFAULT 0,
        mfa_enrolled_at TEXT,
        mfa_last_counter INTEGER NOT NULL DEFAULT -1,
        created_at      TEXT    NOT NULL,
        last_login      TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token       TEXT    PRIMARY KEY,
        user_id     INTEGER NOT NULL,
        expires_at  TEXT    NOT NULL,
        created_at  TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    """CREATE TABLE IF NOT EXISTS action_tokens (
        token_hash   TEXT    PRIMARY KEY,
        user_id      INTEGER NOT NULL,
        session_hash TEXT    NOT NULL,
        action_class TEXT    NOT NULL,
        method       TEXT    NOT NULL,
        path         TEXT    NOT NULL,
        expires_at   TEXT    NOT NULL,
        created_at   TEXT    NOT NULL,
        consumed_at  TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_action_tokens_user_expiry ON action_tokens(user_id, expires_at)",
    # One confirmation, then a window. A token is still one-shot and still
    # bound to one exact request; what this remembers is that the person at
    # this session proved themselves for a whole CLASS of action recently, so
    # the admin console stops demanding a fresh code per user it touches. Bound
    # to the session hash, so it dies with logout and never travels.
    """CREATE TABLE IF NOT EXISTS action_grants (
        user_id      INTEGER NOT NULL,
        session_hash TEXT    NOT NULL,
        action_class TEXT    NOT NULL,
        expires_at   TEXT    NOT NULL,
        created_at   TEXT    NOT NULL,
        PRIMARY KEY (user_id, session_hash, action_class),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_action_grants_expiry ON action_grants(expires_at)",
    # Passkeys — Face ID / fingerprint sign-in. Only PUBLIC keys live here; the
    # private key never leaves the phone's secure hardware and no biometric is
    # ever transmitted, so this table holds nothing that can impersonate anyone.
    """CREATE TABLE IF NOT EXISTS passkeys (
        credential_id TEXT    PRIMARY KEY,
        user_id       INTEGER NOT NULL,
        public_key    TEXT    NOT NULL,
        sign_count    INTEGER NOT NULL DEFAULT 0,
        label         TEXT    NOT NULL DEFAULT '',
        created_at    TEXT    NOT NULL,
        last_used_at  TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_passkeys_user ON passkeys(user_id)",
    # A challenge is single-use and short-lived. Kept in the DB rather than in
    # process memory so a restart mid-ceremony fails closed instead of
    # accepting a stale one.
    """CREATE TABLE IF NOT EXISTS webauthn_challenges (
        challenge_id TEXT    PRIMARY KEY,
        user_id      INTEGER,
        purpose      TEXT    NOT NULL,
        challenge    TEXT    NOT NULL,
        expires_at   TEXT    NOT NULL,
        created_at   TEXT    NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS strategies (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        name        TEXT    NOT NULL,
        folder      TEXT    DEFAULT '',
        config      TEXT    NOT NULL DEFAULT '{}',
        version     INTEGER DEFAULT 1,
        versions    TEXT    DEFAULT '[]',
        created_at  TEXT    NOT NULL,
        updated_at  TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_strategies_user ON strategies(user_id)",
    """CREATE TABLE IF NOT EXISTS runs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL,
        mode            TEXT    NOT NULL DEFAULT 'backtest',
        strategy_name   TEXT    DEFAULT '',
        config          TEXT    DEFAULT '{}',
        trades          TEXT    DEFAULT '[]',
        summary         TEXT    DEFAULT '{}',
        trade_count     INTEGER DEFAULT 0,
        total_pnl       REAL    DEFAULT 0.0,
        created_at      TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id)",
    """CREATE TABLE IF NOT EXISTS trade_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        trade_date  TEXT    NOT NULL,
        data        TEXT    NOT NULL DEFAULT '{}',
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_trade_history_user ON trade_history(user_id)",
    """CREATE TABLE IF NOT EXISTS journals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        entry_date  TEXT    NOT NULL,
        data        TEXT    NOT NULL DEFAULT '{}',
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_journals_user ON journals(user_id)",
    """CREATE TABLE IF NOT EXISTS financial_plans (
        user_id      INTEGER PRIMARY KEY,
        data         TEXT    NOT NULL DEFAULT '{}',
        updated_at   TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    """CREATE TABLE IF NOT EXISTS app_state (
        key         TEXT    PRIMARY KEY,
        value       TEXT    NOT NULL DEFAULT '',
        updated_at  TEXT    NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS scalp_trades (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        trade_data  TEXT    NOT NULL DEFAULT '{}',
        created_at  TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_scalp_trades_user ON scalp_trades(user_id)",
    # RETIRED 2026-08-29, when the Test Bench was removed: every strategy can
    # now replay its own rule from its own Backtest button, so nothing reads or
    # writes this table any more.
    #
    # The 30 rows on prod were backed up and then deleted on Phil's word the
    # same day (backups/test_bench_runs_backup_20260829.json here, and the same
    # file in ~ec2-user on prod). The TABLE is left in place: empty it costs
    # nothing, and dropping it would need a migration for a feature that is
    # already gone. It stays in _USER_OWNED_TABLES below so deleting an account
    # still clears anything that somehow lands here.
    """CREATE TABLE IF NOT EXISTS test_bench_runs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL,
        query_key       TEXT    NOT NULL,
        instrument      TEXT    NOT NULL,
        strategy        TEXT    NOT NULL,
        timeframe       TEXT    NOT NULL,
        mother_date     TEXT    NOT NULL,
        mother_timestamp TEXT   NOT NULL,
        rung_inr        REAL    NOT NULL DEFAULT 0,
        itm_steps       INTEGER NOT NULL DEFAULT 0,
        outcome         TEXT    NOT NULL DEFAULT '',
        net_pnl         REAL,
        entry_count     INTEGER NOT NULL DEFAULT 0,
        payload         TEXT    NOT NULL DEFAULT '{}',
        created_at      TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_test_bench_query ON test_bench_runs(user_id, query_key)",
    "CREATE INDEX IF NOT EXISTS idx_test_bench_date ON test_bench_runs(user_id, mother_date)",
    """CREATE TABLE IF NOT EXISTS fib_backtest_runs (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id          INTEGER NOT NULL,
        mother_timestamp TEXT    NOT NULL,
        side             TEXT    NOT NULL,
        timeframe        TEXT    NOT NULL,
        horizon_to       TEXT    NOT NULL,
        fully_priced     INTEGER NOT NULL DEFAULT 0,
        net_pnl          REAL,
        gap_count        INTEGER NOT NULL DEFAULT 0,
        payload          TEXT    NOT NULL DEFAULT '{}',
        created_at       TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_fib_backtest_user_date ON fib_backtest_runs(user_id, mother_timestamp)",
    # ── THE PAPER LEDGER. A finished campaign used to live nowhere. ──
    # Candle Entry, Fib Boundary and Gap Carry each keep their WHOLE state in a
    # single app_state row, and the next auto mother OVERWRITES it -- so the
    # first live Candle Entry campaign (mother 3 Aug, NIFTY 24400 CE, two rungs,
    # ~-Rs 52,891) reached expiry on 25 Aug 2026 and was destroyed within the
    # hour by the campaign that replaced it. Phil, that evening: "Where is that
    # trade that is completed today with expiry?" -- it was gone, and no backup
    # was late enough to hold its settlement.
    #
    # A campaign is archived here the moment it goes terminal, keyed so the same
    # one can never be written twice. `source` says whether the row was captured
    # live or rebuilt afterwards from recorded prices; a rebuilt row is honest
    # about being a reconstruction rather than a live capture.
    """CREATE TABLE IF NOT EXISTS paper_campaigns (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id       INTEGER NOT NULL,
        strategy      TEXT    NOT NULL,
        campaign_key  TEXT    NOT NULL,
        symbol        TEXT    NOT NULL DEFAULT '',
        contract      TEXT    NOT NULL DEFAULT '',
        opened_at     TEXT,
        closed_at     TEXT,
        status        TEXT    NOT NULL DEFAULT '',
        exit_reason   TEXT,
        buys          INTEGER NOT NULL DEFAULT 0,
        deployed_inr  REAL,
        gross_pnl     REAL,
        costs_total   REAL,
        net_pnl       REAL,
        source        TEXT    NOT NULL DEFAULT 'live',
        payload       TEXT    NOT NULL DEFAULT '{}',
        created_at    TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_campaign_key ON paper_campaigns(user_id, strategy, campaign_key)",
    "CREATE INDEX IF NOT EXISTS idx_paper_campaign_closed ON paper_campaigns(user_id, strategy, closed_at)",
    # ── Sanctuary: the owner's private journal and household ledger. ──
    # Small collections (categories, songs, recurring config, month salaries,
    # the cached verse of the day) live as JSON values in sanctuary_state,
    # following the journals/financial_plans document style; only the rows
    # that get queried by date carry their own tables.
    """CREATE TABLE IF NOT EXISTS sanctuary_state (
        user_id     INTEGER NOT NULL,
        key         TEXT    NOT NULL,
        value       TEXT    NOT NULL DEFAULT '',
        updated_at  TEXT    NOT NULL,
        PRIMARY KEY (user_id, key),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    """CREATE TABLE IF NOT EXISTS sanctuary_entries (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        entry_date  TEXT    NOT NULL,
        kind        TEXT    NOT NULL DEFAULT 'note',
        title       TEXT    NOT NULL DEFAULT '',
        body        TEXT    NOT NULL DEFAULT '',
        mood        INTEGER,
        music       TEXT    NOT NULL DEFAULT '',
        photos      TEXT    NOT NULL DEFAULT '[]',
        created_at  TEXT    NOT NULL,
        updated_at  TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sanctuary_entries_user_date ON sanctuary_entries(user_id, entry_date)",
    """CREATE TABLE IF NOT EXISTS sanctuary_ledger (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        entry_date  TEXT    NOT NULL,
        category    TEXT    NOT NULL DEFAULT 'Other',
        amount      REAL    NOT NULL DEFAULT 0,
        note        TEXT    NOT NULL DEFAULT '',
        source      TEXT    NOT NULL DEFAULT 'manual',
        ref_id      TEXT    NOT NULL DEFAULT '',
        created_at  TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sanctuary_ledger_user_date ON sanctuary_ledger(user_id, entry_date)",
    """CREATE TABLE IF NOT EXISTS sanctuary_loans (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        name        TEXT    NOT NULL,
        lender      TEXT    NOT NULL DEFAULT '',
        emi_amount  REAL    NOT NULL DEFAULT 0,
        due_day     INTEGER NOT NULL DEFAULT 5,
        start_date  TEXT    NOT NULL DEFAULT '',
        note        TEXT    NOT NULL DEFAULT '',
        account_no  TEXT    NOT NULL DEFAULT '',
        details     TEXT    NOT NULL DEFAULT '',
        drawn_amount REAL   NOT NULL DEFAULT 0,
        stated_on   TEXT    NOT NULL DEFAULT '',
        active      INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sanctuary_loans_user ON sanctuary_loans(user_id)",
    # The planner. `said` keeps the words he actually wrote — "before next
    # wednesday" — so a date read wrongly can be SEEN to have been read
    # wrongly, instead of standing there as a bare date nobody chose.
    """CREATE TABLE IF NOT EXISTS sanctuary_plans (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL,
        title       TEXT    NOT NULL,
        due_date    TEXT    NOT NULL DEFAULT '',
        due_kind    TEXT    NOT NULL DEFAULT 'on',
        said        TEXT    NOT NULL DEFAULT '',
        note        TEXT    NOT NULL DEFAULT '',
        done        INTEGER NOT NULL DEFAULT 0,
        done_at     TEXT    NOT NULL DEFAULT '',
        created_at  TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sanctuary_plans_user ON sanctuary_plans(user_id, done, due_date)",
    """CREATE TABLE IF NOT EXISTS sanctuary_emis (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER NOT NULL,
        loan_id        INTEGER NOT NULL,
        due_date       TEXT    NOT NULL,
        amount         REAL    NOT NULL DEFAULT 0,
        principal_part REAL,
        interest_part  REAL,
        outstanding    REAL,
        paid_on        TEXT    NOT NULL DEFAULT '',
        created_at     TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (loan_id) REFERENCES sanctuary_loans(id)
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sanctuary_emis_loan_due ON sanctuary_emis(loan_id, due_date)",
    "CREATE INDEX IF NOT EXISTS idx_sanctuary_emis_user_due ON sanctuary_emis(user_id, due_date)",
    """CREATE TABLE IF NOT EXISTS sanctuary_documents (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL,
        title        TEXT    NOT NULL,
        category     TEXT    NOT NULL DEFAULT 'Other',
        doc_number   TEXT    NOT NULL DEFAULT '',
        note         TEXT    NOT NULL DEFAULT '',
        series       TEXT    NOT NULL DEFAULT '',
        doc_date     TEXT    NOT NULL DEFAULT '',
        filename     TEXT    NOT NULL DEFAULT '',
        content_type TEXT    NOT NULL DEFAULT '',
        size         INTEGER NOT NULL DEFAULT 0,
        file_token   TEXT    NOT NULL DEFAULT '',
        content_sha  TEXT    NOT NULL DEFAULT '',
        created_at   TEXT    NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sanctuary_documents_user ON sanctuary_documents(user_id, category)",
    """CREATE TABLE IF NOT EXISTS sanctuary_moods (
        user_id     INTEGER NOT NULL,
        mood_date   TEXT    NOT NULL,
        mood        INTEGER NOT NULL,
        note        TEXT    NOT NULL DEFAULT '',
        PRIMARY KEY (user_id, mood_date),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""",
]


def _init_db_sync():
    """Synchronous schema initialization (runs once at import/startup)."""
    global _initialized
    if _initialized:
        return
    db_path = config.DB_PATH
    _logger.info(f"[DB] Initializing SQLite schema at {db_path}")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    for stmt in _SCHEMA_STATEMENTS:
        conn.execute(stmt)
    # Existing installations pre-date application MFA. SQLite's CREATE TABLE
    # IF NOT EXISTS does not add columns, so upgrades need explicit, idempotent
    # column migrations before any authenticated request reads a user row.
    existing_user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    user_column_migrations = {
        "mfa_totp_secret": "TEXT DEFAULT ''",
        "mfa_pending_secret": "TEXT DEFAULT ''",
        "mfa_enabled": "INTEGER NOT NULL DEFAULT 0",
        "mfa_enrolled_at": "TEXT",
        "mfa_last_counter": "INTEGER NOT NULL DEFAULT -1",
    }
    for column, definition in user_column_migrations.items():
        if column not in existing_user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")  # nosec B608

    # The sanctuary's loans learned to carry their account number and the
    # details Phil wants at hand; a table created before that needs them added.
    existing_loan_columns = {row[1] for row in conn.execute("PRAGMA table_info(sanctuary_loans)").fetchall()}
    for column, decl in (
        ("account_no", "TEXT NOT NULL DEFAULT ''"),
        ("details", "TEXT NOT NULL DEFAULT ''"),
        # A revolving debt (the sweep-linked OD, a card) has no schedule;
        # what it owes is a single stated figure.
        ("drawn_amount", "REAL NOT NULL DEFAULT 0"),
        # The day that figure was true. Without it a stated balance cannot be
        # carried forward, because there is no telling which of the ledger's
        # movements happened after he read it off the bank.
        ("stated_on", "TEXT NOT NULL DEFAULT ''"),
        # An instalment that is charged to a credit card never leaves the
        # bank on its own. It arrives inside the card's bill, and the bill
        # is already a ledger row, so a schedule that also spends it counts
        # the same money twice.
        ("on_a_card", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if column not in existing_loan_columns:
            conn.execute(f"ALTER TABLE sanctuary_loans ADD COLUMN {column} {decl}")  # nosec B608
            if column == "on_a_card":
                # The importer names these off the statement that carries
                # them — "Instaloan on card ••4838" — so the ones already in
                # the table can say for themselves what the column is for.
                # Once only, on the migration that creates it; the loan card
                # has a button, and his answer there outlives this.
                conn.execute("UPDATE sanctuary_loans SET on_a_card = 1 WHERE lower(name) LIKE '%on card%'")

    # A statement prints a running balance beside every row and the reader
    # was throwing it away, so the one figure that says how much money is
    # actually there had to be typed in by hand. It is kept now; rows
    # imported before this carry nothing and are simply not consulted.
    existing_ledger_columns = {row[1] for row in conn.execute("PRAGMA table_info(sanctuary_ledger)").fetchall()}
    if "balance" not in existing_ledger_columns:
        conn.execute("ALTER TABLE sanctuary_ledger ADD COLUMN balance REAL")

    # A document remembers its content's fingerprint, so the same paper
    # offered twice — in one folder drop or across two — is stored once.
    existing_doc_columns = {row[1] for row in conn.execute("PRAGMA table_info(sanctuary_documents)").fetchall()}
    if "content_sha" not in existing_doc_columns:
        conn.execute("ALTER TABLE sanctuary_documents ADD COLUMN content_sha TEXT NOT NULL DEFAULT ''")
    conn.commit()
    conn.close()
    _initialized = True
    _logger.info("[DB] Schema initialized")


async def init_db():
    """Initialize the database (sync schema creation, safe to call multiple times)."""
    _init_db_sync()


async def _connect() -> aiosqlite.Connection:
    """Open a fresh async connection with WAL mode."""
    db = await aiosqlite.connect(config.DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA foreign_keys = ON")
    return db


async def get_db() -> aiosqlite.Connection:
    """Get a new database connection. Caller should close when done, or use _connect()."""
    if not _initialized:
        _init_db_sync()
    return await _connect()


async def close_db():
    """No-op for connection-per-call pattern. Kept for API compatibility."""
    pass


def _connect_sync() -> sqlite3.Connection:
    """Open a synchronous SQLite connection for thread/off-loop helpers."""
    if not _initialized:
        _init_db_sync()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SENSITIVE_USER_FIELDS = frozenset(
    {
        "dhan_client_id",
        "dhan_access_token",
        "dhan_pin",
        "dhan_totp_secret",
        "mfa_totp_secret",
        "mfa_pending_secret",
    }
)


def _encrypt_user_fields(fields: dict) -> dict:
    """Encrypt broker credential fields before storing them."""
    if not fields:
        return fields
    encrypted = dict(fields)
    for key in _SENSITIVE_USER_FIELDS & encrypted.keys():
        value = encrypted.get(key)
        if value in (None, ""):
            encrypted[key] = ""
            continue
        from auth import encrypt_value, encryption_enabled

        if not encryption_enabled():
            raise RuntimeError("ENCRYPTION_KEY must be configured before saving broker credentials")

        encrypted[key] = encrypt_value(str(value))
    return encrypted


def _decrypt_user_row(row: aiosqlite.Row | sqlite3.Row | dict | None) -> dict | None:
    """Decrypt broker credential fields on read, tolerating legacy plaintext rows."""
    if not row:
        return None
    user = dict(row)
    for key in _SENSITIVE_USER_FIELDS:
        value = user.get(key)
        if not value:
            user[key] = ""
            continue
        from auth import decrypt_value

        user[key] = decrypt_value(value)
    return user


# ── Users ────────────────────────────────────────────────────────


async def create_user(username: str, password_hash: str, role: str = "user", email: str | None = None) -> int:
    """Create a new user and return their id."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        now = _now_iso()
        cursor = await db.execute(
            "INSERT INTO users (username, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, email, password_hash, role, now),
        )
        await db.commit()
        return cursor.lastrowid


async def get_user_by_username(username: str) -> dict | None:
    """Fetch a user by username (case-insensitive)."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = await cursor.fetchone()
        return _decrypt_user_row(row)


async def get_user_by_id(user_id: int) -> dict | None:
    """Fetch a user by id."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        return _decrypt_user_row(row)


async def get_admin_user(preferred_username: str | None = None) -> dict | None:
    """Fetch the preferred admin account, falling back to any existing admin."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        candidates: list[str | None] = []
        if preferred_username:
            candidates.append(preferred_username)
        if (preferred_username or "").lower() != "admin":
            candidates.append("admin")
        for username in candidates:
            if not username:
                continue
            cursor = await db.execute(
                "SELECT * FROM users WHERE role = 'admin' AND username = ? COLLATE NOCASE ORDER BY id LIMIT 1",
                (username,),
            )
            row = await cursor.fetchone()
            if row:
                return _decrypt_user_row(row)
        cursor = await db.execute("SELECT * FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
        row = await cursor.fetchone()
        return _decrypt_user_row(row)


def get_admin_user_sync(preferred_username: str | None = None) -> dict | None:
    """Synchronous admin lookup for thread-based startup helpers."""
    with _connect_sync() as db:
        candidates: list[str | None] = []
        if preferred_username:
            candidates.append(preferred_username)
        if (preferred_username or "").lower() != "admin":
            candidates.append("admin")
        for username in candidates:
            if not username:
                continue
            cursor = db.execute(
                "SELECT * FROM users WHERE role = 'admin' AND username = ? COLLATE NOCASE ORDER BY id LIMIT 1",
                (username,),
            )
            row = cursor.fetchone()
            if row:
                return _decrypt_user_row(row)
        cursor = db.execute("SELECT * FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
        row = cursor.fetchone()
        return _decrypt_user_row(row)


async def list_users() -> list[dict]:
    """List all users (admin use)."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, username, email, role, is_active, created_at, last_login, dhan_client_id, dhan_access_token "
            "FROM users ORDER BY id"
        )
        rows = await cursor.fetchall()
        users: list[dict] = []
        for row in rows:
            full_user = _decrypt_user_row(row) or {}
            client_id = str(full_user.get("dhan_client_id", "") or "").strip()
            access_token = str(full_user.get("dhan_access_token", "") or "").strip()
            users.append(
                {
                    "id": full_user.get("id"),
                    "username": full_user.get("username"),
                    "email": full_user.get("email"),
                    "role": full_user.get("role"),
                    "is_active": full_user.get("is_active"),
                    "created_at": full_user.get("created_at"),
                    "last_login": full_user.get("last_login"),
                    "broker_configured": bool(client_id and access_token),
                    "broker_partial": bool((client_id and not access_token) or (access_token and not client_id)),
                }
            )
        return users


_ALLOWED_USER_FIELDS = frozenset(
    {
        "username",
        "email",
        "password_hash",
        "role",
        "is_active",
        "dhan_client_id",
        "dhan_access_token",
        "dhan_pin",
        "dhan_totp_secret",
        "mfa_totp_secret",
        "mfa_pending_secret",
        "mfa_enabled",
        "mfa_enrolled_at",
        "mfa_last_counter",
        "last_login",
    }
)


async def update_user(user_id: int, **fields) -> bool:
    """Update user fields. Pass only the fields you want to change."""
    if not fields:
        return False
    # Whitelist column names to prevent SQL injection via kwargs
    bad = set(fields) - _ALLOWED_USER_FIELDS
    if bad:
        raise ValueError(f"Invalid user fields: {bad}")
    fields = _encrypt_user_fields(fields)
    async with aiosqlite.connect(config.DB_PATH) as db:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [user_id]
        await db.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)  # nosec B608
        await db.commit()
        return True


def update_user_sync(user_id: int, **fields) -> bool:
    """Synchronous user update helper for thread/off-loop broker callbacks."""
    if not fields:
        return False
    bad = set(fields) - _ALLOWED_USER_FIELDS
    if bad:
        raise ValueError(f"Invalid user fields: {bad}")
    fields = _encrypt_user_fields(fields)
    with _connect_sync() as db:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [user_id]
        db.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)  # nosec B608
        db.commit()
        return True


async def set_user_active(user_id: int, is_active: bool) -> bool:
    """Enable or disable a user account."""
    return await update_user(user_id, is_active=int(is_active))


async def update_last_login(user_id: int):
    """Update the last_login timestamp."""
    await update_user(user_id, last_login=_now_iso())


# ── Sessions ─────────────────────────────────────────────────────


async def create_session(token: str, user_id: int, expires_at: str) -> None:
    """Store a new session."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        now = _now_iso()
        await db.execute(
            "INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token, user_id, expires_at, now),
        )
        await db.commit()


async def get_session(token: str) -> dict | None:
    """Get a session by token, returns None if expired or missing."""
    if not token:
        return None
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        now = _now_iso()
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE token = ? AND expires_at > ?",
            (token, now),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_session(token: str) -> None:
    """Remove a session."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        await db.commit()


async def delete_sessions_for_user(user_id: int) -> int:
    """Remove all sessions for a user. Returns count deleted."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        await db.commit()
        return cursor.rowcount


async def cleanup_expired_sessions() -> int:
    """Remove all expired sessions. Returns count deleted."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        now = _now_iso()
        cursor = await db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        await db.commit()
        return cursor.rowcount


async def claim_mfa_counter(user_id: int, counter: int) -> bool:
    """Atomically accept a TOTP counter once, preventing code replay."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE users SET mfa_last_counter = ? WHERE id = ? AND mfa_last_counter < ?",
            (int(counter), int(user_id), int(counter)),
        )
        await db.commit()
        return cursor.rowcount == 1


async def create_action_token(
    token_hash: str,
    user_id: int,
    session_hash: str,
    action_class: str,
    method: str,
    path: str,
    expires_at: str,
) -> None:
    """Persist one short-lived, session-bound action authorization token."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        now = _now_iso()
        await db.execute("DELETE FROM action_tokens WHERE expires_at <= ? OR consumed_at IS NOT NULL", (now,))
        await db.execute(
            """INSERT INTO action_tokens
               (token_hash, user_id, session_hash, action_class, method, path, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                token_hash,
                int(user_id),
                session_hash,
                action_class,
                method.upper(),
                path,
                expires_at,
                now,
            ),
        )
        await db.commit()


async def consume_action_token(
    token_hash: str,
    user_id: int,
    session_hash: str,
    action_class: str,
    method: str,
    path: str,
) -> bool:
    """Atomically consume an exact-match action token once."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        now = _now_iso()
        cursor = await db.execute(
            """UPDATE action_tokens
               SET consumed_at = ?
               WHERE token_hash = ? AND user_id = ? AND session_hash = ?
                 AND action_class = ? AND method = ? AND path = ?
                 AND expires_at > ? AND consumed_at IS NULL""",
            (
                now,
                token_hash,
                int(user_id),
                session_hash,
                action_class,
                method.upper(),
                path,
                now,
            ),
        )
        await db.commit()
        return cursor.rowcount == 1


async def grant_action_class(user_id: int, session_hash: str, action_class: str, expires_at: str) -> None:
    """Remember that this session proved itself for a class of action."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        now = _now_iso()
        await db.execute("DELETE FROM action_grants WHERE expires_at <= ?", (now,))
        await db.execute(
            """INSERT INTO action_grants (user_id, session_hash, action_class, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, session_hash, action_class)
               DO UPDATE SET expires_at = excluded.expires_at, created_at = excluded.created_at""",
            (int(user_id), session_hash, action_class, expires_at, now),
        )
        await db.commit()


async def has_action_grant(user_id: int, session_hash: str, action_class: str) -> bool:
    """True while this session's confirmation for that class is still good."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """SELECT 1 FROM action_grants
               WHERE user_id = ? AND session_hash = ? AND action_class = ? AND expires_at > ?
               LIMIT 1""",
            (int(user_id), session_hash, action_class, _now_iso()),
        )
        return await cursor.fetchone() is not None


async def delete_action_grants_for_user(user_id: int) -> None:
    """Drop every remembered confirmation for a user (password reset, delete)."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("DELETE FROM action_grants WHERE user_id = ?", (int(user_id),))
        await db.commit()


async def delete_action_grants_for_session(session_hash: str) -> None:
    """Close the re-ask window for one session (logout)."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("DELETE FROM action_grants WHERE session_hash = ?", (session_hash,))
        await db.commit()


# Every table that keys rows to a user id. Deleting an account has to clear all
# of them or the next account to be handed the same id inherits the leftovers.
_USER_OWNED_TABLES: tuple[str, ...] = (
    "sessions",
    "action_tokens",
    "action_grants",
    "passkeys",
    "webauthn_challenges",
    "strategies",
    "runs",
    "trade_history",
    "journals",
    "financial_plans",
    "scalp_trades",
    "test_bench_runs",
    "fib_backtest_runs",
    "sanctuary_state",
    "sanctuary_entries",
    "sanctuary_ledger",
    "sanctuary_loans",
    "sanctuary_emis",
    "sanctuary_documents",
    "sanctuary_moods",
)


async def delete_user_and_data(user_id: int) -> dict[str, int]:
    """Delete a user and every row that belongs to them, in one transaction.

    Returns the row count removed per table, so the caller can say what it
    actually destroyed rather than claiming success blind. The user's broker
    credentials and MFA secrets are columns on the users row, so they go with
    it; their charts live on disk and are the caller's to remove.
    """
    removed: dict[str, int] = {}
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("BEGIN")
        try:
            for table in _USER_OWNED_TABLES:
                # nosec B608 - the table name is one of the module-level literals
                # in _USER_OWNED_TABLES; nothing from a request reaches this string,
                # and the only value is bound as a parameter.
                cursor = await db.execute(f"DELETE FROM {table} WHERE user_id = ?", (int(user_id),))  # nosec B608
                if cursor.rowcount > 0:
                    removed[table] = cursor.rowcount
            cursor = await db.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
            removed["users"] = cursor.rowcount
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return removed


async def get_app_state(key: str) -> str | None:
    """Fetch one app-state value by key."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT value FROM app_state WHERE key = ? LIMIT 1", (str(key),))
        row = await cursor.fetchone()
        if not row:
            return None
        return str(row["value"])


async def delete_app_state(key: str) -> bool:
    """Forget one app-state value. True when a row was actually removed."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute("DELETE FROM app_state WHERE key = ?", (str(key),))
        await db.commit()
        return bool(cursor.rowcount)


async def set_app_state(key: str, value: str) -> None:
    """Insert or update one app-state value."""
    state_key = str(key)
    state_value = str(value)
    now = _now_iso()
    async with aiosqlite.connect(config.DB_PATH) as db:
        # ONE statement, not SELECT-then-INSERT. Two savers of the same key
        # (a Start route's forced save and the paper loop's first tick) both
        # saw no row and both inserted; the second died on the UNIQUE index.
        await db.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (state_key, state_value, now),
        )
        await db.commit()


def _json_loads(blob: str | None, default):
    """Parse JSON columns defensively."""
    if not blob:
        return default
    try:
        return json.loads(blob)
    except Exception:
        return default


def _json_dumps(value) -> str:
    """Serialize JSON payloads with datetime-safe fallback."""
    return json.dumps(value, default=str)


def _strategy_row_to_dict(row: aiosqlite.Row | sqlite3.Row | dict | None) -> dict | None:
    if not row:
        return None
    item = dict(row)
    config_data = _json_loads(item.get("config"), {})
    if not isinstance(config_data, dict):
        config_data = {}
    versions = _json_loads(item.get("versions"), [])
    if not isinstance(versions, list):
        versions = []

    strategy = dict(config_data)
    name = item.get("name") or strategy.get("run_name") or strategy.get("name") or "Untitled Strategy"
    strategy["id"] = item["id"]
    strategy["run_name"] = strategy.get("run_name") or name
    strategy["name"] = strategy.get("name") or name
    strategy["folder"] = item.get("folder", "") or strategy.get("folder", "")
    strategy["version"] = int(item.get("version") or strategy.get("version") or 1)
    strategy["versions"] = versions
    strategy["created_at"] = item.get("created_at") or strategy.get("created_at") or _now_iso()
    strategy["updated_at"] = item.get("updated_at") or strategy.get("updated_at") or strategy["created_at"]
    return strategy


def _strategy_to_record(strategy: dict) -> dict:
    payload = dict(strategy or {})
    payload.pop("id", None)
    payload.pop("user_id", None)

    versions = payload.pop("versions", []) or []
    version = int(payload.pop("version", 1) or 1)
    created_at = str(payload.pop("created_at", _now_iso()) or _now_iso())
    updated_at = str(payload.pop("updated_at", created_at) or created_at)
    folder = str(payload.get("folder", "") or "")
    name = str(payload.get("run_name") or payload.get("name") or "Untitled Strategy")

    return {
        "name": name,
        "folder": folder,
        "config": _json_dumps(payload),
        "version": version,
        "versions": _json_dumps(versions),
        "created_at": created_at,
        "updated_at": updated_at,
    }


async def list_strategies(user_id: int) -> list[dict]:
    """Return all strategies for a user in save order."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM strategies WHERE user_id = ? ORDER BY id", (user_id,))
        rows = await cursor.fetchall()
        return [_strategy_row_to_dict(row) for row in rows]


async def get_strategy(user_id: int, strategy_id: int) -> dict | None:
    """Fetch one strategy belonging to a user."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM strategies WHERE user_id = ? AND id = ?", (user_id, strategy_id))
        row = await cursor.fetchone()
        return _strategy_row_to_dict(row)


async def create_strategy_record(user_id: int, strategy: dict) -> dict:
    """Insert a strategy and return the stored record."""
    record = _strategy_to_record(strategy)
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO strategies (user_id, name, folder, config, version, versions, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                record["name"],
                record["folder"],
                record["config"],
                record["version"],
                record["versions"],
                record["created_at"],
                record["updated_at"],
            ),
        )
        await db.commit()
        strategy_id = cursor.lastrowid
    return await get_strategy(user_id, strategy_id)


async def replace_strategy_record(user_id: int, strategy_id: int, strategy: dict) -> dict | None:
    """Replace a strategy record with the provided payload."""
    record = _strategy_to_record(strategy)
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE strategies
            SET name = ?, folder = ?, config = ?, version = ?, versions = ?, created_at = ?, updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (
                record["name"],
                record["folder"],
                record["config"],
                record["version"],
                record["versions"],
                record["created_at"],
                record["updated_at"],
                user_id,
                strategy_id,
            ),
        )
        await db.commit()
        if cursor.rowcount <= 0:
            return None
    return await get_strategy(user_id, strategy_id)


async def delete_strategy_record(user_id: int, strategy_id: int) -> bool:
    """Delete one strategy owned by the user."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute("DELETE FROM strategies WHERE user_id = ? AND id = ?", (user_id, strategy_id))
        await db.commit()
        return cursor.rowcount > 0


def _run_row_to_dict(row: aiosqlite.Row | sqlite3.Row | dict | None) -> dict | None:
    if not row:
        return None
    item = dict(row)
    config_data = _json_loads(item.get("config"), {})
    if not isinstance(config_data, dict):
        config_data = {}
    trades = _json_loads(item.get("trades"), [])
    if not isinstance(trades, list):
        trades = []
    summary = _json_loads(item.get("summary"), {})
    if not isinstance(summary, dict):
        summary = {}

    run = dict(config_data)
    strategy_name = item.get("strategy_name") or run.get("run_name") or run.get("strategy_name") or f"Run #{item['id']}"
    if "stats" not in run and summary:
        run["stats"] = summary
    run["id"] = item["id"]
    run["mode"] = item.get("mode") or run.get("mode") or "backtest"
    run["run_name"] = run.get("run_name") or strategy_name
    run["strategy_name"] = run.get("strategy_name") or strategy_name
    run["trade_count"] = int(item.get("trade_count") or run.get("trade_count") or len(trades))
    run["total_pnl"] = float(item.get("total_pnl") if item.get("total_pnl") is not None else run.get("total_pnl", 0))
    run["created_at"] = item.get("created_at") or run.get("created_at") or _now_iso()
    run["trades"] = trades
    return run


def _run_to_record(run: dict) -> dict:
    payload = dict(run or {})
    payload.pop("id", None)
    payload.pop("user_id", None)

    trades = payload.pop("trades", []) or []
    mode = str(payload.get("mode", "backtest") or "backtest")
    created_at = str(payload.get("created_at", _now_iso()) or _now_iso())
    trade_count = int(payload.get("trade_count", len(trades)) or 0)
    total_pnl = float(payload.get("total_pnl", 0) or 0)
    strategy_name = str(payload.get("run_name") or payload.get("strategy_name") or payload.get("name") or "")
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        summary = payload.get("stats", {}) if isinstance(payload.get("stats"), dict) else {}

    return {
        "mode": mode,
        "strategy_name": strategy_name,
        "config": _json_dumps(payload),
        "trades": _json_dumps(trades),
        "summary": _json_dumps(summary),
        "trade_count": trade_count,
        "total_pnl": total_pnl,
        "created_at": created_at,
    }


async def list_runs(user_id: int) -> list[dict]:
    """Return all saved runs for a user."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM runs WHERE user_id = ? ORDER BY id", (user_id,))
        rows = await cursor.fetchall()
        return [_run_row_to_dict(row) for row in rows]


async def get_run(user_id: int, run_id: int) -> dict | None:
    """Fetch one saved run for a user."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM runs WHERE user_id = ? AND id = ?", (user_id, run_id))
        row = await cursor.fetchone()
        return _run_row_to_dict(row)


async def create_run_record(user_id: int, run: dict) -> dict:
    """Insert a run and return the stored record."""
    record = _run_to_record(run)
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO runs (user_id, mode, strategy_name, config, trades, summary, trade_count, total_pnl, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                record["mode"],
                record["strategy_name"],
                record["config"],
                record["trades"],
                record["summary"],
                record["trade_count"],
                record["total_pnl"],
                record["created_at"],
            ),
        )
        await db.commit()
        run_id = cursor.lastrowid
    return await get_run(user_id, run_id)


async def replace_run_record(user_id: int, run_id: int, run: dict) -> dict | None:
    """Replace a saved run with the provided payload."""
    record = _run_to_record(run)
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE runs
            SET mode = ?, strategy_name = ?, config = ?, trades = ?, summary = ?, trade_count = ?, total_pnl = ?, created_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (
                record["mode"],
                record["strategy_name"],
                record["config"],
                record["trades"],
                record["summary"],
                record["trade_count"],
                record["total_pnl"],
                record["created_at"],
                user_id,
                run_id,
            ),
        )
        await db.commit()
        if cursor.rowcount <= 0:
            return None
    return await get_run(user_id, run_id)


async def delete_run_record(user_id: int, run_id: int) -> bool:
    """Delete one run owned by the user."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute("DELETE FROM runs WHERE user_id = ? AND id = ?", (user_id, run_id))
        await db.commit()
        return cursor.rowcount > 0


async def bulk_delete_run_records(user_id: int, run_ids: list[int]) -> int:
    """Delete multiple runs for a user."""
    ids = [int(rid) for rid in run_ids]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            f"DELETE FROM runs WHERE user_id = ? AND id IN ({placeholders})",  # nosec B608
            [user_id, *ids],
        )
        await db.commit()
        return cursor.rowcount


async def cleanup_empty_runs(user_id: int | None = None) -> int:
    """Delete empty non-backtest runs, optionally scoped to one user."""
    if user_id is None:
        sql = (
            "DELETE FROM runs WHERE mode != 'backtest' AND trade_count <= 0 "
            "AND (trades IS NULL OR trades = '' OR trades = '[]')"
        )
        params: list[object] = []
    else:
        sql = (
            "DELETE FROM runs WHERE user_id = ? AND mode != 'backtest' AND trade_count <= 0 "
            "AND (trades IS NULL OR trades = '' OR trades = '[]')"
        )
        params = [user_id]
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(sql, params)
        await db.commit()
        return cursor.rowcount


def _trade_history_data_from_row(row: aiosqlite.Row | sqlite3.Row | dict | None) -> dict | None:
    if not row:
        return None
    data = _json_loads(dict(row).get("data"), {})
    return data if isinstance(data, dict) else {}


async def list_trade_history(user_id: int) -> dict[str, dict]:
    """Return all persisted real-trade history for a user keyed by date."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT trade_date, data FROM trade_history WHERE user_id = ? ORDER BY trade_date",
            (user_id,),
        )
        rows = await cursor.fetchall()
        history: dict[str, dict] = {}
        for row in rows:
            history[str(row["trade_date"])] = _trade_history_data_from_row(row) or {}
        return history


async def get_trade_history_entry(user_id: int, trade_date: str) -> dict | None:
    """Fetch one persisted real-trade summary for a user/date."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT data FROM trade_history WHERE user_id = ? AND trade_date = ? LIMIT 1",
            (user_id, trade_date),
        )
        row = await cursor.fetchone()
        return _trade_history_data_from_row(row)


async def upsert_trade_history_entry(user_id: int, trade_date: str, data: dict) -> None:
    """Insert or replace one trade-history date for a user."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id FROM trade_history WHERE user_id = ? AND trade_date = ? LIMIT 1",
            (user_id, trade_date),
        )
        row = await cursor.fetchone()
        payload = _json_dumps(data or {})
        if row:
            await db.execute("UPDATE trade_history SET data = ? WHERE id = ?", (payload, row["id"]))
        else:
            await db.execute(
                "INSERT INTO trade_history (user_id, trade_date, data) VALUES (?, ?, ?)",
                (user_id, trade_date, payload),
            )
        await db.commit()


def list_trade_history_sync(user_id: int) -> dict[str, dict]:
    """Synchronous trade-history loader for thread-based backfill tasks."""
    with _connect_sync() as conn:
        cursor = conn.execute(
            "SELECT trade_date, data FROM trade_history WHERE user_id = ? ORDER BY trade_date",
            (user_id,),
        )
        history: dict[str, dict] = {}
        for row in cursor.fetchall():
            history[str(row["trade_date"])] = _trade_history_data_from_row(row) or {}
        return history


def upsert_trade_history_entry_sync(user_id: int, trade_date: str, data: dict) -> None:
    """Synchronous trade-history upsert for thread-based backfill tasks."""
    with _connect_sync() as conn:
        cursor = conn.execute(
            "SELECT id FROM trade_history WHERE user_id = ? AND trade_date = ? LIMIT 1",
            (user_id, trade_date),
        )
        row = cursor.fetchone()
        payload = _json_dumps(data or {})
        if row:
            conn.execute("UPDATE trade_history SET data = ? WHERE id = ?", (payload, row["id"]))
        else:
            conn.execute(
                "INSERT INTO trade_history (user_id, trade_date, data) VALUES (?, ?, ?)",
                (user_id, trade_date, payload),
            )
        conn.commit()


def clear_trade_history_sync(user_id: int) -> int:
    """Delete all real-trade history rows for a user."""
    with _connect_sync() as conn:
        cursor = conn.execute("DELETE FROM trade_history WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount


async def list_journal_entries(user_id: int) -> list[dict]:
    """Return journal entry summaries for the journal list view."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT entry_date, data FROM journals WHERE user_id = ? ORDER BY entry_date DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        entries: list[dict] = []
        for row in rows:
            data = _json_loads(dict(row).get("data"), {})
            if not isinstance(data, dict):
                data = {}
            entries.append(
                {
                    "date": str(row["entry_date"]),
                    "asset": data.get("asset", ""),
                    "grade": data.get("grade", ""),
                    "strategy": data.get("strategy", ""),
                }
            )
        return entries


async def get_journal_entry(user_id: int, entry_date: str) -> dict | None:
    """Fetch one journal entry for a user/date."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT data FROM journals WHERE user_id = ? AND entry_date = ? LIMIT 1",
            (user_id, entry_date),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        data = _json_loads(dict(row).get("data"), {})
        return data if isinstance(data, dict) else {}


async def upsert_journal_entry(user_id: int, entry_date: str, data: dict) -> None:
    """Insert or replace one journal entry for a user/date."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id FROM journals WHERE user_id = ? AND entry_date = ? LIMIT 1",
            (user_id, entry_date),
        )
        row = await cursor.fetchone()
        payload = _json_dumps(data or {})
        if row:
            await db.execute("UPDATE journals SET data = ? WHERE id = ?", (payload, row["id"]))
        else:
            await db.execute(
                "INSERT INTO journals (user_id, entry_date, data) VALUES (?, ?, ?)",
                (user_id, entry_date, payload),
            )
        await db.commit()


async def delete_journal_entry(user_id: int, entry_date: str) -> bool:
    """Delete one journal entry for a user/date."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM journals WHERE user_id = ? AND entry_date = ?",
            (user_id, entry_date),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_financial_plan(user_id: int) -> dict:
    """Fetch saved financial plan for a user."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT data, updated_at FROM financial_plans WHERE user_id = ? LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return {}
        data = _json_loads(dict(row).get("data"), {})
        if not isinstance(data, dict):
            data = {}
        data["updated_at"] = row["updated_at"]
        return data


async def upsert_financial_plan(user_id: int, data: dict) -> None:
    """Insert or update one user's financial plan."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT user_id FROM financial_plans WHERE user_id = ? LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        payload = _json_dumps(data or {})
        now = _now_iso()
        if row:
            await db.execute(
                "UPDATE financial_plans SET data = ?, updated_at = ? WHERE user_id = ?",
                (payload, now, user_id),
            )
        else:
            await db.execute(
                "INSERT INTO financial_plans (user_id, data, updated_at) VALUES (?, ?, ?)",
                (user_id, payload, now),
            )
        await db.commit()


def _trade_id_from_scalp_payload(payload: dict) -> int:
    try:
        return int(payload.get("trade_id") or 0)
    except (TypeError, ValueError):
        return 0


async def list_scalp_trades(user_id: int) -> list[dict]:
    """Return persisted closed scalp trades for a user."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT trade_data FROM scalp_trades WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        rows = await cursor.fetchall()
        trades: list[dict] = []
        for row in rows:
            payload = _json_loads(dict(row).get("trade_data"), {})
            if isinstance(payload, dict):
                trades.append(payload)
        return trades


async def create_scalp_trade(user_id: int, trade: dict) -> None:
    """Persist one closed scalp trade for a user."""
    payload = dict(trade or {})
    created_at = str(
        payload.get("closed_at")
        or payload.get("exit_time")
        or payload.get("created_at")
        or payload.get("entry_time")
        or _now_iso()
    )
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "INSERT INTO scalp_trades (user_id, trade_data, created_at) VALUES (?, ?, ?)",
            (user_id, _json_dumps(payload), created_at),
        )
        await db.commit()


async def bulk_delete_scalp_trades(user_id: int, trade_ids: list[int]) -> int:
    """Delete persisted scalp trades for a user by nested trade_id."""
    ids = {int(tid) for tid in trade_ids if str(tid).strip()}
    if not ids:
        return 0
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, trade_data FROM scalp_trades WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        rows = await cursor.fetchall()
        row_ids = []
        for row in rows:
            payload = _json_loads(dict(row).get("trade_data"), {})
            if isinstance(payload, dict) and _trade_id_from_scalp_payload(payload) in ids:
                row_ids.append(int(row["id"]))
        if not row_ids:
            return 0
        placeholders = ",".join("?" for _ in row_ids)
        delete_cursor = await db.execute(
            f"DELETE FROM scalp_trades WHERE user_id = ? AND id IN ({placeholders})",  # nosec B608
            [user_id, *row_ids],
        )
        await db.commit()
        return delete_cursor.rowcount


async def delete_scalp_trade(user_id: int, trade_id: int) -> bool:
    """Delete one persisted scalp trade for a user by nested trade_id."""
    return (await bulk_delete_scalp_trades(user_id, [trade_id])) > 0


def get_max_scalp_trade_id_sync(user_id: int) -> int:
    """Return the max persisted scalp trade_id for a user."""
    max_trade_id = 0
    with _connect_sync() as conn:
        cursor = conn.execute("SELECT trade_data FROM scalp_trades WHERE user_id = ?", (user_id,))
        for row in cursor.fetchall():
            payload = _json_loads(dict(row).get("trade_data"), {})
            if isinstance(payload, dict):
                max_trade_id = max(max_trade_id, _trade_id_from_scalp_payload(payload))
    return max_trade_id


# ── Fib Boundary backtests: durable, user-owned replay packages ──
FIB_BACKTEST_RUNS_KEPT = 10


async def save_fib_backtest_run(user_id: int, payload: dict) -> int:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO fib_backtest_runs
                   (user_id, mother_timestamp, side, timeframe, horizon_to,
                    fully_priced, net_pnl, gap_count, payload, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                int(user_id),
                str((payload.get("mother") or {}).get("timestamp") or ""),
                str(payload.get("side") or ""),
                str(payload.get("timeframe") or ""),
                str(payload.get("horizon_to") or ""),
                int(bool(result.get("fully_priced"))),
                result.get("net_pnl"),
                len(result.get("data_gaps") or []),
                _json_dumps(payload),
                _now_iso(),
            ),
        )
        # ONE PANEL, A HANDFUL OF ROWS. Every replay appended forever and the
        # page reads only the newest, so this had reached 81 rows for one user.
        # Keep a short tail for the export links and drop the rest.
        await db.execute(
            "DELETE FROM fib_backtest_runs WHERE user_id = ? AND id NOT IN ("
            "SELECT id FROM fib_backtest_runs WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
            (int(user_id), int(user_id), FIB_BACKTEST_RUNS_KEPT),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)
    finally:
        await db.close()


def _paper_campaign_payload(value) -> str:
    """The payload as ONE layer of JSON, whatever the caller handed over.

    `json_extract(payload, '$.engine')` is how this table answers "can this
    campaign be drawn?", and it matches nothing when the stored text is a JSON
    string containing JSON rather than an object. A caller passing an
    already-encoded payload is a mistake, but a silent one, so it is absorbed
    here rather than left to break a button three screens away.
    """
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return value
        return _json_dumps(decoded)
    return _json_dumps(value or {})


async def save_paper_campaign(user_id: int, strategy: str, row: dict) -> bool:
    """Archive one FINISHED paper campaign. Returns True if it was new.

    Idempotent on (user, strategy, campaign_key): the save path runs on every
    poll, and a campaign that has ended stays ended, so this is called again and
    again for the same one. The first write wins and later ones update the money
    in place -- a campaign whose exit is priced late must be allowed to correct
    itself, but it must never appear twice.
    """
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO paper_campaigns
                   (user_id, strategy, campaign_key, symbol, contract, opened_at,
                    closed_at, status, exit_reason, buys, deployed_inr, gross_pnl,
                    costs_total, net_pnl, source, payload, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id, strategy, campaign_key) DO UPDATE SET
                   closed_at    = excluded.closed_at,
                   status       = excluded.status,
                   exit_reason  = excluded.exit_reason,
                   buys         = excluded.buys,
                   deployed_inr = excluded.deployed_inr,
                   gross_pnl    = excluded.gross_pnl,
                   costs_total  = excluded.costs_total,
                   net_pnl      = excluded.net_pnl,
                   payload      = excluded.payload""",
            (
                int(user_id),
                str(strategy),
                str(row.get("campaign_key") or ""),
                str(row.get("symbol") or ""),
                str(row.get("contract") or ""),
                row.get("opened_at"),
                row.get("closed_at"),
                str(row.get("status") or ""),
                row.get("exit_reason"),
                int(row.get("buys") or 0),
                row.get("deployed_inr"),
                row.get("gross_pnl"),
                row.get("costs_total"),
                row.get("net_pnl"),
                str(row.get("source") or "live"),
                # A caller that has already serialised its payload must not have
                # it serialised twice: JSON-inside-JSON defeats every
                # `json_extract` this table relies on, silently. That is
                # exactly how Supertrend lost its chart button.
                _paper_campaign_payload(row.get("payload")),
                _now_iso(),
            ),
        )
        await db.commit()
        return int(cursor.rowcount or 0) == 1
    finally:
        await db.close()


async def get_paper_campaign(user_id: int, campaign_id: int) -> dict | None:
    """One archived campaign, payload included, for redrawing its chart."""
    db = await get_db()
    try:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM paper_campaigns WHERE user_id = ? AND id = ?",
            (int(user_id), int(campaign_id)),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        out = dict(row)
        out["payload"] = _json_loads(out.get("payload"), {})
        return out
    finally:
        await db.close()


async def list_paper_campaigns(user_id: int, strategy: str, limit: int = 50, *, only_traded: bool = True) -> list[dict]:
    """Archived campaigns for one strategy, newest first.

    A CAMPAIGN THAT NEVER BOUGHT IS NOT A RESULT. Fib Boundary archives every
    mother it accepts, including the ones that break before a single fill, so
    the ledger filled up with `mother_broken_no_buys` rows carrying Rs 0.00 in
    every money column -- eight of them in one morning, burying the seven
    campaigns that actually traded and spending the row limit on nothing
    (Phil, 2026-09-08: "I do not want all the no buys data").

    They stay in the table; they are simply not a line in the money ledger.
    Pass `only_traded=False` to see them.
    """
    db = await get_db()
    try:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, campaign_key, symbol, contract, opened_at, closed_at,
                      status, exit_reason, buys, deployed_inr, gross_pnl,
                      costs_total, net_pnl, source,
                      -- Can this one be drawn? Only if it kept the engine it
                      -- ended as; the payload itself is far too big to ship
                      -- with a list that only needs to know yes or no.
                      CASE WHEN json_valid(payload)
                                AND (json_extract(payload, '$.engine') IS NOT NULL
                                     OR json_extract(payload, '$.chart') IS NOT NULL)
                           THEN 1 ELSE 0 END AS has_chart,
                      -- The handful of fields a redraw needs, for the ladders
                      -- whose chart is recomputed from the candles rather than
                      -- rebuilt from an engine. Small enough to ship in a list.
                      CASE WHEN json_valid(payload)
                           THEN json_extract(payload, '$.chart') END AS chart_params
               FROM paper_campaigns
               WHERE user_id = ? AND strategy = ?
                 AND (? = 0 OR buys > 0)
               ORDER BY COALESCE(closed_at, created_at) DESC, id DESC
               LIMIT ?""",
            (int(user_id), str(strategy), 1 if only_traded else 0, max(1, min(int(limit), 200))),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def list_fib_backtest_runs(user_id: int, limit: int = 50) -> list[dict]:
    db = await get_db()
    try:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, mother_timestamp, side, timeframe, horizon_to,
                      fully_priced, net_pnl, gap_count, created_at
               FROM fib_backtest_runs WHERE user_id = ?
               ORDER BY id DESC LIMIT ?""",
            (int(user_id), max(1, min(int(limit), 200))),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()


async def get_fib_backtest_run(user_id: int, run_id: int) -> dict | None:
    db = await get_db()
    try:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM fib_backtest_runs WHERE user_id = ? AND id = ?",
            (int(user_id), int(run_id)),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = _json_loads(result.get("payload"), {})
        return result
    finally:
        await db.close()


async def delete_fib_backtest_runs(user_id: int) -> int:
    """Forget every saved Fib Boundary replay this user has. Returns how many.

    Deleting only the newest was worse than useless: the panel restores the
    next one, so it looked like the delete had done nothing. Phil clicked it
    repeatedly against a stack 81 deep (2026-08-22): "Even after deleting this
    still comes back". Nothing in the UI browses this history -- the panel
    reads only the latest -- so the button means what it says.
    """
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM fib_backtest_runs WHERE user_id = ?", (int(user_id),))
        await db.commit()
        return int(cursor.rowcount or 0)
    finally:
        await db.close()


# ── Passkeys (Face ID / fingerprint) ─────────────────────────────
async def add_passkey(credential_id: str, user_id: int, public_key: str, sign_count: int, label: str) -> None:
    """Store one registered passkey. Public key only — never a biometric."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO passkeys (credential_id, user_id, public_key, sign_count, label, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (credential_id, int(user_id), public_key, int(sign_count), label, _now_iso()),
        )
        await db.commit()


async def get_passkey(credential_id: str) -> dict | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM passkeys WHERE credential_id = ?", (credential_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def list_passkeys(user_id: int) -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT credential_id, label, created_at, last_used_at FROM passkeys WHERE user_id = ? ORDER BY created_at",
            (int(user_id),),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def touch_passkey(credential_id: str, sign_count: int) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "UPDATE passkeys SET sign_count = ?, last_used_at = ? WHERE credential_id = ?",
            (int(sign_count), _now_iso(), credential_id),
        )
        await db.commit()


async def delete_passkey(credential_id: str, user_id: int) -> bool:
    """Remove one passkey. Scoped to the owner so an id alone is not enough."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM passkeys WHERE credential_id = ? AND user_id = ?", (credential_id, int(user_id))
        )
        await db.commit()
        return cursor.rowcount > 0


async def store_webauthn_challenge(
    challenge_id: str, user_id: int | None, purpose: str, challenge: str, expires_at: str
) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        # Sweep expired rows on the way in; this table has no other reader.
        await db.execute("DELETE FROM webauthn_challenges WHERE expires_at < ?", (_now_iso(),))
        await db.execute(
            "INSERT OR REPLACE INTO webauthn_challenges"
            " (challenge_id, user_id, purpose, challenge, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (challenge_id, user_id, purpose, challenge, expires_at, _now_iso()),
        )
        await db.commit()


async def consume_webauthn_challenge(challenge_id: str, purpose: str) -> dict | None:
    """Take a challenge and delete it in one step, so it can never be replayed."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM webauthn_challenges WHERE challenge_id = ? AND purpose = ? AND expires_at >= ?",
            (challenge_id, purpose, _now_iso()),
        )
        row = await cursor.fetchone()
        await db.execute("DELETE FROM webauthn_challenges WHERE challenge_id = ?", (challenge_id,))
        await db.commit()
        return dict(row) if row else None
