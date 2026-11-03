"""
Database doctor - run this if the Admin app shows a database error.

Usage (from the PrintingSystem folder, with the venv active):

    python -m admin.tools.db_doctor            # report only
    python -m admin.tools.db_doctor --fix      # report, then repair

It prints every table and its columns, compares them with the current
schema.sql, and lists anything that is missing. --fix applies the same
automatic repair the Admin server now runs at startup (old tables are
renamed to <name>__backup_1, never deleted).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from admin.config.config import config                       # noqa: E402
from admin.database.db import get_conn                        # noqa: E402
from admin.database.repair import (                           # noqa: E402
    SCHEMA_PATH, describe_schema, parse_schema, repair_schema,
)


def main() -> int:
    fix = "--fix" in sys.argv
    print(f"Database file: {config.db_path}")
    if not Path(config.db_path).exists():
        print("This file does not exist yet - start the Admin app once to create it.")
        return 1

    conn = get_conn()
    print("\nTables currently in the database:")
    print(describe_schema(conn))

    expected = parse_schema(SCHEMA_PATH.read_text(encoding="utf-8"))
    problems = []
    for table, spec in expected.items():
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not row:
            problems.append(f"table '{table}' is missing")
            continue
        actual = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        for col in spec["columns"]:
            if col not in actual:
                problems.append(f"'{table}' is missing the column '{col}'")

    print("\nComparison with the current schema:")
    if not problems:
        print("  OK - nothing is missing.")
        return 0
    for p in problems:
        print(f"  PROBLEM: {p}")

    if not fix:
        print("\nRun the same command again with --fix to repair these automatically.")
        return 1

    changes = repair_schema(conn)
    print("\nRepairs applied:")
    for c in changes:
        print(f"  {c}")
    print("\nDone. Start the Admin app again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
