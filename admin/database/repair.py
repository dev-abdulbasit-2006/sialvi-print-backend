"""
Database self-repair.

Why this exists
---------------
schema.sql uses CREATE TABLE IF NOT EXISTS. That protects existing data, but it
also means a database created by an OLDER version of SialviPrint keeps its old
table shape forever: if an old table is missing a column (or has a column under a
different name), every query written against the new schema fails with

    OperationalError: no such column: <name>

which the Admin UI shows as "An unexpected error occurred on the Admin server".

This module compares every table in the live database against schema.sql and
repairs the differences automatically at startup (and again on demand if a query
still trips over a missing column at runtime):

  * missing column that SQLite can add in place  -> ALTER TABLE ADD COLUMN
  * missing column that cannot be added in place -> the table is rebuilt from
    schema.sql and all data from the columns that BOTH versions share is copied
    across. The old table is kept as <name>__backup_<n> so nothing is ever lost.

Extra columns that schema.sql doesn't know about are left alone.
"""

import logging
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger("db_repair")

_CONSTRAINT_START = re.compile(
    r"^\s*(FOREIGN\s+KEY|PRIMARY\s+KEY|UNIQUE|CHECK|CONSTRAINT)\b", re.IGNORECASE
)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# SQLite refuses ALTER TABLE ADD COLUMN for these; such a column forces a rebuild.
_CANNOT_ADD = re.compile(
    r"\bPRIMARY\s+KEY\b|\bUNIQUE\b|\bREFERENCES\b|\bNOT\s+NULL\b(?!.*\bDEFAULT\b)|DEFAULT\s*\(",
    re.IGNORECASE,
)


# ----------------------------------------------------------------------
# schema.sql parsing
# ----------------------------------------------------------------------
def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on commas that are not inside parentheses."""
    parts, depth, current = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current).strip())
    return parts


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def parse_schema(schema_sql: str) -> dict[str, dict]:
    """
    Returns {table_name: {"ddl": full CREATE statement,
                          "columns": {column_name: column_ddl}}}
    """
    clean = _strip_comments(schema_sql)
    tables: dict[str, dict] = {}
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)\s*;",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(clean):
        name, body = match.group(1), match.group(2)
        columns: dict[str, str] = {}
        for item in _split_top_level(body):
            if not item.strip():
                continue
            # table-level constraint (FOREIGN KEY..., UNIQUE(a, b), PRIMARY KEY(a)) - not a column
            if _CONSTRAINT_START.match(item):
                continue
            col_name = re.split(r"[\s(]", item.strip(), 1)[0].strip('"[]`')
            columns[col_name] = item
        tables[name] = {"ddl": match.group(0), "columns": columns}
    return tables


# ----------------------------------------------------------------------
# repair
# ----------------------------------------------------------------------
def _existing_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] if not isinstance(r, sqlite3.Row) else r["name"]
            for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _backup_name(conn: sqlite3.Connection, table: str) -> str:
    n = 1
    while _table_exists(conn, f"{table}__backup_{n}"):
        n += 1
    return f"{table}__backup_{n}"


def _rebuild_table(conn: sqlite3.Connection, table: str, spec: dict, actual: list[str]) -> None:
    """
    Recreate `table` exactly as schema.sql defines it, copying every column the
    old and new versions have in common. The old table is renamed, never dropped.
    """
    temp = f"{table}__repair_new"
    conn.execute(f"DROP TABLE IF EXISTS {temp}")
    new_ddl = re.sub(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?" + re.escape(table),
        f"CREATE TABLE {temp}",
        spec["ddl"],
        count=1,
        flags=re.IGNORECASE,
    )
    conn.execute(new_ddl)

    shared = [c for c in spec["columns"] if c in actual]
    if shared:
        # If the new table REQUIRES a column the old one never had, there is no
        # honest value to copy in (inventing one could mean a wrong price), so
        # the rows are left in the backup table instead of being copied blind.
        required_missing = [
            col for col, ddl in spec["columns"].items()
            if col not in actual
            and re.search(r"\bNOT\s+NULL\b", ddl, re.IGNORECASE)
            and not re.search(r"\bDEFAULT\b", ddl, re.IGNORECASE)
            and not re.search(r"\bPRIMARY\s+KEY\b", ddl, re.IGNORECASE)
        ]
        if required_missing:
            logger.warning(
                "Table '%s' could not be converted row-by-row (the old version had no %s). "
                "The repaired table starts empty; the old rows are kept in the backup table.",
                table, ", ".join(required_missing),
            )
        else:
            cols = ", ".join(shared)
            try:
                conn.execute(f"INSERT OR IGNORE INTO {temp} ({cols}) SELECT {cols} FROM {table}")
            except sqlite3.Error as exc:      # never let a copy problem block the repair
                logger.warning("Could not copy old rows of '%s' (%s); the repaired table starts "
                               "empty. The old rows stay in the backup table.", table, exc)

    backup = _backup_name(conn, table)
    conn.execute(f"ALTER TABLE {table} RENAME TO {backup}")
    conn.execute(f"ALTER TABLE {temp} RENAME TO {table}")
    logger.warning(
        "Repaired table '%s': rebuilt from schema, copied columns %s, old copy kept as '%s'.",
        table, shared or "(none)", backup,
    )


def repair_schema(conn: sqlite3.Connection, schema_sql: str | None = None) -> list[str]:
    """
    Bring every table in the database up to the current schema.
    Returns a list of human-readable descriptions of what was changed.
    Safe to call as often as you like: it does nothing when the database
    already matches.
    """
    if schema_sql is None:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    expected = parse_schema(schema_sql)
    changes: list[str] = []

    for table, spec in expected.items():
        if not _table_exists(conn, table):
            conn.execute(spec["ddl"])          # brand-new table for an old database
            changes.append(f"created missing table '{table}'")
            continue

        actual = _existing_columns(conn, table)
        missing = [c for c in spec["columns"] if c not in actual]
        if not missing:
            continue

        simple = [c for c in missing if not _CANNOT_ADD.search(spec["columns"][c])]
        hard = [c for c in missing if c not in simple]

        if hard:
            # foreign_keys must be OFF while a table is swapped out.
            fk_was_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
            if fk_was_on:
                conn.execute("PRAGMA foreign_keys = OFF")
            try:
                _rebuild_table(conn, table, spec, actual)
                changes.append(f"rebuilt table '{table}' (was missing: {', '.join(missing)})")
            finally:
                if fk_was_on:
                    conn.execute("PRAGMA foreign_keys = ON")
            continue

        for col in simple:
            ddl = spec["columns"][col]
            tail = ddl[len(col):].strip()
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {tail}")
            changes.append(f"added column '{table}.{col}'")

    # Indexes are dropped together with a rebuilt table, so re-run the script
    # (every statement in it is CREATE ... IF NOT EXISTS).
    conn.executescript(schema_sql)

    if changes:
        logger.warning("Database repaired: %s", "; ".join(changes))
    return changes


def describe_schema(conn: sqlite3.Connection) -> str:
    """Human-readable dump of every table and its columns (for the log / support)."""
    lines = []
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall():
        cols = ", ".join(_existing_columns(conn, name))
        lines.append(f"  {name}: {cols}")
    return "\n".join(lines)
