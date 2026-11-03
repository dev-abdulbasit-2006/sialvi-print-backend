"""Run the WHOLE test-suite with every licensing server using PostgreSQL instead of SQLite.

    set TEST_DATABASE_URL=postgresql://user:password@host:5432/dbname      (an empty scratch database)
    python tests\\run_server_tests_on_postgres.py

Each test service gets its own schema (search_path), so tests stay isolated. Tests that open the SQLite file
directly (test_licensing: stored-key hashing and the "not recently seen" clock) cannot work on PostgreSQL and
are expected to fail here; everything else must pass. Not part of the default suite (needs a database).
"""
import os
import sys
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
BASE = os.environ.get("TEST_DATABASE_URL", "").strip()
if not BASE:
    sys.exit("Set TEST_DATABASE_URL to an empty scratch PostgreSQL database first.")

import psycopg2  # noqa: E402
from licensing.server import service as svc_mod  # noqa: E402

_orig = svc_mod.LicenseService.__init__


def _patched(self, settings):
    schema = "t_" + uuid.uuid4().hex[:12]
    c = psycopg2.connect(BASE)
    c.autocommit = True
    c.cursor().execute(f"CREATE SCHEMA {schema}")
    c.close()
    settings.database_url = f"{BASE}{'&' if '?' in BASE else '?'}options=-csearch_path%3D{schema}"
    _orig(self, settings)


svc_mod.LicenseService.__init__ = _patched
os.environ["LICENSE_DB_POOL_MAX"] = "3"


def _flatten(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from _flatten(t)
        else:
            yield t


# test_persistence chooses SQLite/PostgreSQL itself, so it is run separately (see its docstring).
suite = unittest.TestSuite(t for t in _flatten(unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests")))
                           if not t.id().startswith("test_persistence."))
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
