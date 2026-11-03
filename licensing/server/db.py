"""Storage + versioned migrations for the licensing server: PostgreSQL (persistent) or SQLite (dev/tests).

The server is configured with DATABASE_URL (postgresql://...) for real use: licenses, installations and the owner
login then SURVIVE restarts, redeploys and the free hosting tier going to sleep. Without it the server falls back to a
local SQLite file, which is only suitable for development, tests and hosts with a persistent disk.
PostgreSQL is reached through a thin adapter that keeps the sqlite3-style API used by service.py
(conn.execute(sql, params).fetchone() / .fetchall() / .lastrowid, BEGIN IMMEDIATE / COMMIT / ROLLBACK).

* One connection per request/operation (thread-safe; pooled for PostgreSQL).
* Writes use BEGIN IMMEDIATE so two simultaneous activations are serialized and
  can never exceed a license's PC limit.
* History tables (ip_history, audit_events) are never deleted by any code path.
"""
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

MIGRATIONS = [
    # ---- version 1: complete Feature-1 schema ----
    """
    CREATE TABLE owners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        password_salt TEXT NOT NULL,
        iterations INTEGER NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE owner_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL REFERENCES owners(id),
        token_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        ip TEXT
    );
    CREATE TABLE login_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        ip TEXT,
        ts TEXT NOT NULL,
        success INTEGER NOT NULL
    );
    CREATE INDEX idx_login_attempts ON login_attempts(username, ts);
    CREATE TABLE licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_hash TEXT NOT NULL UNIQUE,
        key_hint TEXT NOT NULL,
        label TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active','suspended','revoked')),
        max_installations INTEGER NOT NULL CHECK (max_installations >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT,
        notes TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE installations (
        id TEXT PRIMARY KEY,
        license_id INTEGER NOT NULL REFERENCES licenses(id),
        device_name TEXT NOT NULL DEFAULT '',
        app_version TEXT NOT NULL DEFAULT '',
        os_info TEXT NOT NULL DEFAULT '',
        secret_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active','locked','deactivated')),
        activated_at TEXT NOT NULL,
        last_checkin_at TEXT,
        last_ip TEXT,
        lock_reason TEXT NOT NULL DEFAULT '',
        locked_at TEXT,
        deactivated_at TEXT,
        deactivated_reason TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_installations_license ON installations(license_id, status);
    CREATE TABLE ip_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        installation_id TEXT NOT NULL REFERENCES installations(id),
        license_id INTEGER NOT NULL REFERENCES licenses(id),
        ip TEXT NOT NULL,
        ts TEXT NOT NULL,
        app_version TEXT NOT NULL DEFAULT '',
        event TEXT NOT NULL,
        result TEXT NOT NULL
    );
    CREATE INDEX idx_ip_history_inst ON ip_history(installation_id, id);
    CREATE TABLE ip_restrictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        network TEXT NOT NULL,
        scope TEXT NOT NULL CHECK (scope IN ('block_activation','block_authorization')),
        license_id INTEGER REFERENCES licenses(id),
        reason TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        changed_at TEXT,
        changed_by TEXT
    );
    CREATE INDEX idx_ip_restrictions_active ON ip_restrictions(active);
    CREATE TABLE audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        license_id INTEGER,
        before_json TEXT,
        after_json TEXT,
        reason TEXT NOT NULL DEFAULT '',
        ip TEXT
    );
    CREATE INDEX idx_audit_ts ON audit_events(id);
    CREATE INDEX idx_audit_target ON audit_events(target_type, target_id);
    """,
    # ---- version 2: remote command queue (owner -> PCs, with per-PC delivery tracking) ----
    """
    CREATE TABLE remote_commands (
        id TEXT PRIMARY KEY,
        license_id INTEGER NOT NULL REFERENCES licenses(id),
        installation_id TEXT REFERENCES installations(id),
        command_type TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        issued_by TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE INDEX idx_remote_commands_license ON remote_commands(license_id, issued_at);
    CREATE TABLE remote_command_deliveries (
        command_id TEXT NOT NULL REFERENCES remote_commands(id),
        installation_id TEXT NOT NULL REFERENCES installations(id),
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','delivered','applied','rejected','expired')),
        delivered_at TEXT,
        acknowledged_at TEXT,
        result TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (command_id, installation_id)
    );
    CREATE INDEX idx_rcd_installation ON remote_command_deliveries(installation_id, status);
    """,
]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if value else None


# ----------------------------------------------------------------------------- PostgreSQL adapter
_LOCK_KEY = 73110001                      # advisory lock that makes BEGIN IMMEDIATE serialize writers (like SQLite)
_SERIAL_TABLES = {"owners", "owner_sessions", "login_attempts", "licenses", "ip_history", "ip_restrictions", "audit_events"}
_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO\s+([a-z_]+)", re.I)
_POOLS: dict = {}
_POOL_LOCK = threading.Lock()


def is_postgres(target) -> bool:
    return isinstance(target, str) and target.lower().startswith(("postgres://", "postgresql://"))


def translate(sql: str) -> str:
    """sqlite-flavoured SQL -> PostgreSQL (placeholders, auto ids, printf, case-insensitive LIKE)."""
    sql = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "SERIAL PRIMARY KEY", sql, flags=re.I)
    sql = re.sub(r"printf\('%05d',\s*id\)", "lpad(CAST(id AS TEXT), 5, '0')", sql)
    sql = re.sub(r"\bLIKE\b", "ILIKE", sql)
    return sql.replace("%", "%%").replace("?", "%s")


class PgCursor:
    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid

    def fetchone(self):
        return self._cur.fetchone() if self._cur.description else None

    def fetchall(self):
        return self._cur.fetchall() if self._cur.description else []

    @property
    def rowcount(self):
        return self._cur.rowcount

    def __iter__(self):
        return iter(self.fetchall())


class PgConnection:
    """sqlite3-like facade over a pooled psycopg2 connection (autocommit; explicit BEGIN / COMMIT)."""

    def __init__(self, pool, raw):
        self._pool, self._raw = pool, raw

    def execute(self, sql, params=()):
        import psycopg2.extras
        text = sql.strip().rstrip(";")
        cur = self._raw.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if text.upper() == "BEGIN IMMEDIATE":
            cur.execute("BEGIN")
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
            return PgCursor(cur)
        lastrowid = None
        m = _INSERT_RE.match(text)
        if m and m.group(1).lower() in _SERIAL_TABLES and "returning" not in text.lower():
            cur.execute(translate(text) + " RETURNING id", tuple(params))
            row = cur.fetchone()
            lastrowid = row[0] if row else None
            return PgCursor(cur, lastrowid)
        cur.execute(translate(text), tuple(params) if params else None)
        return PgCursor(cur)

    def close(self):
        pool, raw, self._pool, self._raw = self._pool, self._raw, None, None
        if raw is None:
            return
        try:
            cur = raw.cursor()
            cur.execute("ROLLBACK")          # leaves nothing half-done if an error skipped COMMIT (harmless warning otherwise)
        except Exception:                      # noqa: BLE001
            pass
        pool.putconn(raw, close=bool(raw.closed))


def _pool_for(url: str):
    with _POOL_LOCK:
        pool = _POOLS.get(url)
        if pool is None:
            from psycopg2.pool import ThreadedConnectionPool
            pool = ThreadedConnectionPool(1, int(os.environ.get("LICENSE_DB_POOL_MAX", "8")), url, connect_timeout=15)
            _POOLS[url] = pool
        return pool


def _pg_connect(url: str) -> PgConnection:
    """Get a live pooled connection. A request that finds the pool busy WAITS for a free connection (up to
    ~20 s) instead of failing; idle connections the cloud database already closed are replaced."""
    from psycopg2.pool import PoolError
    pool = _pool_for(url)
    deadline = time.monotonic() + 20
    last = None
    while time.monotonic() < deadline:
        try:
            raw = pool.getconn()
        except PoolError as exc:               # every connection is in use right now: wait for one
            last = exc
            time.sleep(0.02)
            continue
        raw.autocommit = True
        try:
            raw.cursor().execute("SELECT 1")   # drop connections the cloud database closed while idle
            return PgConnection(pool, raw)
        except Exception as exc:               # noqa: BLE001
            last = exc
            pool.putconn(raw, close=True)
            time.sleep(0.2)
    raise RuntimeError(f"Cannot reach the licensing database: {last}")


def close_pools():
    """Close every pooled PostgreSQL connection (used by tests / shutdown)."""
    with _POOL_LOCK:
        for pool in _POOLS.values():
            try:
                pool.closeall()
            except Exception:                  # noqa: BLE001
                pass
        _POOLS.clear()


# ----------------------------------------------------------------------------- connect / migrate
def connect(target):
    """target = path of a SQLite file, or a postgresql:// URL."""
    if is_postgres(target):
        return _pg_connect(target)
    conn = sqlite3.connect(str(target), timeout=15, isolation_level=None)  # autocommit; we BEGIN explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def migrate(path) -> int:
    """Apply any missing migrations (SQLite file or PostgreSQL URL). Safe to run on every start; never drops data."""
    conn = connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = row["v"] or 0
        for i, script in enumerate(MIGRATIONS[current:], start=current + 1):
            conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in [s for s in script.split(";") if s.strip()]:
                    conn.execute(stmt)
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (i,))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return len(MIGRATIONS)
    finally:
        conn.close()


@contextmanager
def transaction(conn):
    """Serialized write transaction (BEGIN IMMEDIATE takes the write lock up front)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
