"""
SQLite access layer.

Design notes:
- WAL mode: allows concurrent readers while a writer is active, which
  matters because the Flask API is multi-threaded and the queue worker
  runs in its own thread.
- Every connection enables foreign_keys (off by default in SQLite).
- get_conn() returns a fresh thread-local connection; SQLite connections
  are not safe to share across threads.
- transaction() is a context manager giving atomic commit/rollback so
  "payment verified but job never queued" style bugs can't happen.
"""

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from admin.config.config import config
from admin.database.repair import repair_schema, describe_schema

logger = logging.getLogger("database")

_local = threading.local()

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _new_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(config.db_path),
        timeout=30,               # wait up to 30s on lock contention instead of failing immediately
        isolation_level=None,     # manual transaction control via BEGIN/COMMIT
        check_same_thread=True,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


def get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _new_connection()
    return _local.conn


def init_db():
    """Create all tables if they don't exist. Safe to call on every startup."""
    conn = get_conn()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    conn.executescript(schema_sql)
    _migrate(conn)
    repair_schema(conn, schema_sql)      # fixes databases made by older versions
    logger.info("Database schema in use:\n%s", describe_schema(conn))
    _seed_defaults(conn)
    _apply_one_time_defaults(conn)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migrate(conn: sqlite3.Connection):
    """Upgrades databases created by earlier versions (new columns only; nothing is dropped)."""
    _ensure_column(conn, "print_profiles", "ask_properties", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "print_profiles", "default_printer_name", "TEXT")
    _ensure_column(conn, "orders", "print_settings_json", "TEXT")
    _ensure_column(conn, "orders", "station_host", "TEXT")
    _ensure_column(conn, "orders", "station_key", "TEXT")
    _ensure_column(conn, "orders", "station_port", "INTEGER NOT NULL DEFAULT 8767")
    _ensure_column(conn, "orders", "computer_name", "TEXT")
    _ensure_column(conn, "orders", "printer_name", "TEXT")
    _ensure_column(conn, "payments", "payment_method", "TEXT")
    _ensure_column(conn, "payments", "receipt_no", "INTEGER")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_receipt_no ON payments(receipt_no) WHERE receipt_no IS NOT NULL")
    ensure_price_tiers_table(conn)
    # Existing print_jobs keeps its legacy printer_id FK for database compatibility.
    # New remote jobs use the disabled placeholder below and route using order-level
    # station/printer identity instead of an Admin-PC printer.
    conn.execute(
        """INSERT OR IGNORE INTO printers
           (system_name, display_name, is_network, is_enabled, last_status)
           VALUES ('__USER_PC_REMOTE_PRINTER__', 'User PC Remote Printer', 1, 0, 'UNKNOWN')"""
    )


def repair_now(conn: sqlite3.Connection | None = None) -> list[str]:
    """
    On-demand schema repair, used when a query trips over a missing column at
    runtime on a database that was created by an older version.
    """
    conn = conn or get_conn()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return repair_schema(conn, f.read())


def ensure_price_tiers_table(conn: sqlite3.Connection):
    """Package/tier prices table - created here too so an upgraded database can never be missing it."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS price_tiers (
               id              INTEGER PRIMARY KEY AUTOINCREMENT,
               profile_id      INTEGER NOT NULL,
               min_qty         INTEGER NOT NULL,
               max_qty         INTEGER,
               price_per_page  REAL NOT NULL,
               FOREIGN KEY (profile_id) REFERENCES print_profiles(id) ON DELETE CASCADE
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_price_tiers_profile ON price_tiers(profile_id)")


def _flag_done(conn, key: str) -> bool:
    return conn.execute("SELECT 1 FROM system_settings WHERE key = ?", (key,)).fetchone() is not None


def _set_flag(conn, key: str):
    conn.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, '1')", (key,))


def _apply_one_time_defaults(conn: sqlite3.Connection):
    """
    One-time defaults, each guarded by a flag so an admin's later edits are
    never overwritten:
      * photo-paper types (Glossy/Matte/Luster) ask the customer for print properties
      * the shop's price sheet: Black & White student packages + Color Normal Rs 20
    """
    if not _flag_done(conn, "defaults_hide_builtin_printing_v1"):
        # The new Admin UI exposes one unified Printing section. Legacy
        # built-in profiles are hidden so the shop owner starts with only
        # the print types they create themselves. Historical orders remain
        # intact because profiles are not deleted.
        conn.execute("UPDATE print_profiles SET is_enabled = 0 WHERE code NOT LIKE 'CUSTOM_%'")
        _set_flag(conn, "defaults_hide_builtin_printing_v1")

    if not _flag_done(conn, "defaults_ask_properties_v1"):
        conn.execute("UPDATE print_profiles SET ask_properties = 1 WHERE media_type IN ('GLOSSY','MATTE','LUSTER')")
        _set_flag(conn, "defaults_ask_properties_v1")

    if not _flag_done(conn, "defaults_price_sheet_v1"):
        bw = conn.execute("SELECT id, price_per_page FROM print_profiles WHERE code = 'BW_NORMAL'").fetchone()
        if bw and bw["price_per_page"] == 0 and not conn.execute(
                "SELECT 1 FROM price_tiers WHERE profile_id = ?", (bw["id"],)).fetchone():
            conn.execute("UPDATE print_profiles SET price_per_page = 10 WHERE id = ?", (bw["id"],))
            for lo, hi, price in ((1, 30, 10), (31, 100, 8), (101, 150, 7), (151, 300, 6), (301, None, 5)):
                conn.execute(
                    "INSERT INTO price_tiers (profile_id, min_qty, max_qty, price_per_page) VALUES (?,?,?,?)",
                    (bw["id"], lo, hi, price),
                )
        conn.execute("UPDATE print_profiles SET price_per_page = 20 WHERE code = 'COLOR_NORMAL' AND price_per_page = 0")
        _set_flag(conn, "defaults_price_sheet_v1")

    if not _flag_done(conn, "defaults_all_formats_v1"):
        # Older installs seeded Black & White / Laser / Voucher types with a
        # narrower file list (no jpg/png/etc). Every built-in type now accepts
        # the same full set, so this brings an existing shop's database in
        # line without touching any type an admin created themselves.
        all_formats = ('["pdf","docx","doc","xlsx","xls","pptx","ppt","txt","rtf","csv",'
                       '"jpg","jpeg","png","bmp","tiff"]')
        conn.execute(
            "UPDATE print_profiles SET supported_formats_json = ? WHERE code NOT LIKE 'CUSTOM_%'",
            (all_formats,),
        )
        _set_flag(conn, "defaults_all_formats_v1")

    if not _flag_done(conn, "defaults_design_formats_v2"):
        # v3.8+: Adobe Photoshop/Illustrator and AutoCAD files are now part of
        # the same built-in supported-format set. This migration deliberately
        # touches only built-in profiles; CUSTOM_* profiles keep the admin
        # configuration they already have.
        all_formats = ('["pdf","docx","doc","xlsx","xls","pptx","ppt","txt","rtf","csv",'
                       '"jpg","jpeg","png","bmp","tiff","psd","ai","dwg","dxf"]')
        conn.execute(
            "UPDATE print_profiles SET supported_formats_json = ? WHERE code NOT LIKE 'CUSTOM_%'",
            (all_formats,),
        )
        _set_flag(conn, "defaults_design_formats_v2")


def _seed_defaults(conn: sqlite3.Connection):
    """Insert the 13 required print profiles if the table is empty."""
    row = conn.execute("SELECT COUNT(*) AS c FROM print_profiles").fetchone()
    if row["c"] > 0:
        return

    # New installs start with no built-in print types. The Admin
    # "Printing" screen is the single place where the owner creates the
    # shop's actual print offerings.
    return

    # Legacy built-in seed kept below for historical documentation only.
    # Every built-in print type accepts the same full set of file types
    # (there is no admin screen to customise this per type, so all 13
    # built-ins are kept identical here; a custom type an admin adds
    # gets the same list too - see create_profile()).
    all_formats = '["pdf","docx","doc","xlsx","xls","pptx","ppt","txt","rtf","csv","jpg","jpeg","png","bmp","tiff","psd","ai","dwg","dxf"]'
    profiles = [
        ("VOUCHER", "Voucher Printing", "A4", "PLAIN", "COLOR", all_formats),
        ("A3_GLOSSY", "A3 Glossy", "A3", "GLOSSY", "COLOR", all_formats),
        ("A4_GLOSSY", "A4 Glossy", "A4", "GLOSSY", "COLOR", all_formats),
        ("A3_MATTE", "A3 Matte", "A3", "MATTE", "COLOR", all_formats),
        ("A4_MATTE", "A4 Matte", "A4", "MATTE", "COLOR", all_formats),
        ("A3_LUSTER", "A3 Luster", "A3", "LUSTER", "COLOR", all_formats),
        ("A4_LUSTER", "A4 Luster", "A4", "LUSTER", "COLOR", all_formats),
        ("A3_COLOR", "A3 Color", "A3", "PLAIN", "COLOR", all_formats),
        ("A4_COLOR", "A4 Color", "A4", "PLAIN", "COLOR", all_formats),
        ("A3_LASER", "A3 Laser", "A3", "LASER", "BW", all_formats),
        ("A4_LASER", "A4 Laser", "A4", "LASER", "BW", all_formats),
        ("BW_NORMAL", "Normal Black & White", "A4", "PLAIN", "BW", all_formats),
        ("COLOR_NORMAL", "Normal Color", "A4", "PLAIN", "COLOR", all_formats),
    ]
    conn.execute("BEGIN;")
    try:
        for code, name, size, media, color, formats in profiles:
            conn.execute(
                """INSERT INTO print_profiles
                   (code, display_name, paper_size, media_type, color_mode,
                    supported_formats_json, price_per_page, ask_properties)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (code, name, size, media, color, formats, 0.0,
                 1 if media in ("GLOSSY", "MATTE", "LUSTER") else 0),
            )
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise


@contextmanager
def transaction():
    """
    Atomic transaction context manager.

    Usage:
        with transaction() as conn:
            conn.execute(...)
            conn.execute(...)
    Commits on clean exit, rolls back on any exception, so multi-step
    operations (e.g. verify payment -> create print_jobs -> update order
    status) can never leave the database in a half-updated state.
    """
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE;")
    try:
        yield conn
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
